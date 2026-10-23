"""FORK(COMM-ISSUE): ParamsWriter - Params writes that never make their caller wait on /data."""
import contextlib
import fcntl
import os
import time
from unittest import mock

from openpilot.common.params import Params, UnknownKeyName
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.sunnypilot.system.params_writer import ParamsWriter


def params_lock_path() -> str:
  # Params::put / remove lock <params root>/.lock; get_param_path() is <params root>/<prefix>
  return os.path.join(os.path.dirname(Params().get_param_path()), ".lock")


@contextlib.contextmanager
def held_params_lock():
  """What a put stuck in a slow journal commit looks like to everyone else: <params>/.lock taken and not let go."""
  fd = os.open(params_lock_path(), os.O_CREAT | os.O_RDONLY, 0o775)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


class TestParamsWriter(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.params = Params()
    self.writer = ParamsWriter("test_params_writer")
    self.addCleanup(self.writer.stop, 5.)

  def patch_params(self, name: str, side_effect) -> None:
    patcher = mock.patch.object(self.writer._params, name, side_effect=side_effect)
    patcher.start()
    self.addCleanup(patcher.stop)

  def record_writes(self) -> list:
    """The writer's Params writes, in the order it makes them: (method, key[, value])."""
    order: list = []

    def recorder(name, orig):
      def rec(key, *args, **kwargs):
        order.append((name, key, *args[:1]))
        return orig(key, *args, **kwargs)
      return rec

    for name in ("put", "put_bool", "remove"):
      self.patch_params(name, recorder(name, getattr(self.writer._params, name)))
    return order

  def test_writes_land(self):
    self.writer.put("UptimeOnroad", 12.5)
    self.writer.put_bool("IsEngaged", True)
    self.writer.put("LastOffroadStatusPacket", {"count": 3})
    assert self.writer.flush(5.)
    assert self.params.get("UptimeOnroad") == 12.5
    assert self.params.get_bool("IsEngaged")
    assert self.params.get("LastOffroadStatusPacket") == {"count": 3}

    self.writer.remove("IsEngaged")
    assert self.writer.flush(5.)
    assert self.params.get("IsEngaged") is None

  def test_callers_never_wait_for_the_lock(self):
    """The whole point: with <params>/.lock held, every call returns at once and the writer's thread does the waiting."""
    with held_params_lock():
      t = time.monotonic()
      self.writer.put_bool("IsEngaged", True)
      self.writer.put("UptimeOnroad", 1.0)
      self.writer.remove("DriverTooDistracted")
      self.writer.offroad_alert("Offroad_TemperatureTooHigh", True, "96.1C")
      assert time.monotonic() - t < 0.1
      assert not self.writer.flush(0.5)  # still waiting on the lock
      assert self.params.get("UptimeOnroad") is None
    assert self.writer.flush(5.)
    assert self.params.get_bool("IsEngaged")
    assert self.params.get("UptimeOnroad") == 1.0
    assert self.params.get("Offroad_TemperatureTooHigh")["extra"] == "96.1C"

  def test_newest_request_wins_in_first_request_order(self):
    order = self.record_writes()
    with held_params_lock():
      self.writer.put("UptimeOffroad", 1.0)  # taken by the writer, which then waits on the lock
      time.sleep(0.3)
      self.writer.put("UptimeOnroad", 1.0)
      self.writer.put_bool("IsEngaged", True)
      self.writer.put("UptimeOnroad", 2.0)   # replaces 1.0, keeps its place before IsEngaged
      self.writer.put_bool("IsEngaged", False)
    assert self.writer.flush(5.)
    assert order == [("put", "UptimeOffroad", 1.0), ("put", "UptimeOnroad", 2.0), ("put_bool", "IsEngaged", False)]
    assert self.params.get("UptimeOnroad") == 2.0
    assert not self.params.get_bool("IsEngaged")

  def test_put_then_remove_of_one_key_is_the_remove(self):
    order = self.record_writes()
    with held_params_lock():
      self.writer.put("UptimeOffroad", 1.0)
      time.sleep(0.3)
      self.writer.put_bool("DriverTooDistracted", True)
      self.writer.remove("DriverTooDistracted")
    assert self.writer.flush(5.)
    assert order == [("put", "UptimeOffroad", 1.0), ("remove", "DriverTooDistracted")]
    assert self.params.get("DriverTooDistracted") is None

  def test_if_changed(self):
    order = self.record_writes()
    for value, if_changed, writes in ((True, True, 1), (True, True, 1), (True, True, 1),
                                      (False, True, 2),
                                      (False, False, 3),  # not if_changed: hardwared's once-a-minute rewrite
                                      (False, True, 3)):
      self.writer.put_bool("NetworkMetered", value, if_changed=if_changed)
      assert self.writer.flush(5.)
      assert len(order) == writes, (value, if_changed, order)
    assert [o[2] for o in order] == [True, False, False]

  def test_offroad_alert_is_set_offroad_alert(self):
    set_offroad_alert("Offroad_TemperatureTooHigh", True, "91.2C")
    direct = self.params.get("Offroad_TemperatureTooHigh")
    self.params.remove("Offroad_TemperatureTooHigh")

    self.writer.offroad_alert("Offroad_TemperatureTooHigh", True, "91.2C")
    assert self.writer.flush(5.)
    assert self.params.get("Offroad_TemperatureTooHigh") == direct
    self.writer.offroad_alert("Offroad_TemperatureTooHigh", False, "91.3C")
    assert self.writer.flush(5.)
    assert self.params.get("Offroad_TemperatureTooHigh") is None

  def test_caller_mistakes_raise_on_the_caller(self):
    with self.assertRaises(UnknownKeyName):
      self.writer.put("NoSuchParamKey", 1)
    with self.assertRaises(UnknownKeyName):
      self.writer.remove("NoSuchParamKey")
    with self.assertRaises(TypeError):
      self.writer.put("UptimeOnroad", "not a float")
    assert self.writer.flush(1.)
    assert self.writer.written == 0

  def test_a_failed_write_does_not_stop_the_writer(self):
    put = self.writer._params.put
    calls = []

    def flaky(key, value, block=False):
      calls.append(key)
      if len(calls) == 1:
        raise OSError("disk said no")
      put(key, value, block=block)

    self.patch_params("put", flaky)
    self.writer.put("UptimeOffroad", 3.0)
    self.writer.put("UptimeOnroad", 4.0)
    assert self.writer.flush(5.)
    assert calls == ["UptimeOffroad", "UptimeOnroad"]
    assert self.params.get("UptimeOnroad") == 4.0
