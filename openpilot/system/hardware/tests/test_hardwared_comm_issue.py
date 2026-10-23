"""FORK(COMM-ISSUE): hardwared's deviceState loop never waits on /data (docs/fork/COMM-ISSUE.md).

The bench proof with the fault injection (another process on <params>/.lock, an LD_PRELOAD fsync stall, the
unpatched hardwared beside it) is comm_issue/fault_injection.py; these are its fast parts.
"""
import contextlib
import fcntl
import math
import os
import queue
import random
import threading
import time
from unittest import mock

import openpilot.cereal.messaging as messaging
from openpilot.cereal import log
from openpilot.common.hardware.base import ThermalConfig, ThermalZone
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.common.version import terms_version, terms_version_sp, training_version
from openpilot.system.hardware import hardwared


def params_lock_path() -> str:
  return os.path.join(os.path.dirname(Params().get_param_path()), ".lock")


@contextlib.contextmanager
def held_params_lock():
  fd = os.open(params_lock_path(), os.O_CREAT | os.O_RDONLY, 0o775)
  try:
    fcntl.flock(fd, fcntl.LOCK_EX)
    yield
  finally:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


class TestSetOffroadAlertIfChanged(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    hardwared.prev_offroad_states.clear()
    self.addCleanup(hardwared.prev_offroad_states.clear)

  def test_a_hidden_alert_text_is_not_a_change(self):
    """:381 - the temperature's 0.1 C text moved in 26-30 % of loops, each a remove() under the params lock."""
    w = mock.Mock()
    for text in ("50.1C", "50.2C", "50.3C", "49.9C"):
      hardwared.set_offroad_alert_if_changed("Offroad_TemperatureTooHigh", False, text, writer=w)
    assert w.offroad_alert.call_args_list == [mock.call("Offroad_TemperatureTooHigh", False, None)]

  def test_a_shown_alert_follows_its_text(self):
    w = mock.Mock()
    for show, text in ((True, "96.1C"), (True, "96.1C"), (True, "96.2C"), (False, "96.2C"), (False, "95.0C")):
      hardwared.set_offroad_alert_if_changed("Offroad_TemperatureTooHigh", show, text, writer=w)
    assert w.offroad_alert.call_args_list == [mock.call("Offroad_TemperatureTooHigh", True, "96.1C"),
                                              mock.call("Offroad_TemperatureTooHigh", True, "96.2C"),
                                              mock.call("Offroad_TemperatureTooHigh", False, None)]

  def test_without_a_writer_it_is_upstream(self, mocker):
    direct = mocker.patch("openpilot.system.hardware.hardwared.set_offroad_alert")
    hardwared.set_offroad_alert_if_changed("Offroad_TiciSupport", False, "release")
    hardwared.set_offroad_alert_if_changed("Offroad_TiciSupport", False, "release")
    direct.assert_called_once_with("Offroad_TiciSupport", False, None)


class TestHardwareThreadNeverWaits(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    hardwared.prev_offroad_states.clear()
    self.addCleanup(hardwared.prev_offroad_states.clear)

  def test_device_state_keeps_coming_while_the_params_lock_is_held(self):
    """The real hardware_thread, the params lock held by someone else for 4 s and an engage edge inside that: deviceState
    keeps its 2 Hz (push11 stopped for the whole hold, and selfdrived raised commIssue past 5 s), and the thread
    itself makes no blocking Params write."""
    params = Params()
    params.put("HasAcceptedTerms", terms_version, block=True)
    params.put("HasAcceptedTermsSP", terms_version_sp, block=True)
    params.put("CompletedTrainingVersion", training_version, block=True)

    rng = random.Random(7)
    t0 = time.monotonic()

    class Drift(ThermalZone):  # a moving temperature, so :381's text changes as in the car
      def __init__(self, base):
        super().__init__(name="drift")
        self.base = base

      def read(self) -> float:
        return self.base + 0.3 * math.sin((time.monotonic() - t0) / 3.) + rng.uniform(-0.6, 0.6)

    cfg = ThermalConfig(cpu=[Drift(55.)], gpu=[Drift(50.)], memory=Drift(48.), pmic=[Drift(45.)])

    writes: list = []  # (thread ident, method, key, block)
    originals = {name: getattr(Params, name) for name in ("put", "put_bool", "remove")}

    def wrap(name):
      orig = originals[name]

      def w(self, key, *a, **kw):
        block = True if name == "remove" else kw.get("block", a[1] if len(a) > 1 else False)
        writes.append((threading.get_ident(), name, key, bool(block)))
        return orig(self, key, *a, **kw)
      return w

    for name in originals:
      setattr(Params, name, wrap(name))
      self.addCleanup(setattr, Params, name, originals[name])

    pm = messaging.PubMaster(["pandaStates", "peripheralState", "selfdriveState"])
    sm = messaging.SubMaster(["deviceState"])
    stop = threading.Event()
    end_event = threading.Event()
    hold_done = threading.Event()
    first: list[float] = []

    def feeder():
      k = 0
      while not stop.is_set():
        ss = messaging.new_message("selfdriveState")
        ss.selfdriveState.enabled = bool(first) and time.monotonic() - first[0] > 4.  # an edge inside the hold
        pm.send("selfdriveState", ss)
        if k % 10 == 0:
          ps = messaging.new_message("pandaStates", 1)
          ps.pandaStates[0].ignitionLine = True
          ps.pandaStates[0].pandaType = log.PandaState.PandaType.tres
          pm.send("pandaStates", ps)
        if k % 50 == 0:
          per = messaging.new_message("peripheralState")
          per.peripheralState.pandaType = log.PandaState.PandaType.tres
          per.peripheralState.voltage = 13000
          pm.send("peripheralState", per)
        k += 1
        time.sleep(0.01)

    with mock.patch.object(hardwared.HARDWARE, "get_thermal_config", return_value=cfg):
      feed = threading.Thread(target=feeder, daemon=True)
      feed.start()
      hw = threading.Thread(target=hardwared.hardware_thread, args=(end_event, queue.Queue(maxsize=1)), daemon=True)
      hw.start()
      recv: list[float] = []
      try:
        deadline = time.monotonic() + 30.
        while not first and time.monotonic() < deadline:
          sm.update(100)
          if sm.updated["deviceState"]:
            first.append(time.monotonic())
            recv.append(first[0])
        assert first, "no deviceState"

        def hold():
          time.sleep(2.)
          with held_params_lock():
            time.sleep(4.)
          hold_done.set()
        threading.Thread(target=hold, daemon=True).start()
        while time.monotonic() - first[0] < 9.:
          sm.update(100)
          if sm.updated["deviceState"]:
            recv.append(time.monotonic())
      finally:
        end_event.set()
        hw.join(15.)
        stop.set()
        feed.join(5.)

    assert hold_done.is_set()
    assert not hw.is_alive()
    gaps = [b - a for a, b in zip(recv, recv[1:], strict=False)]
    assert len(recv) >= 15, recv
    assert max(gaps) < 1.5, f"deviceState stopped for {max(gaps):.2f} s while the params lock was held"

    loop_writes = [w for w in writes if w[0] == hw.ident]
    assert not [w for w in loop_writes if w[3]], f"blocking Params writes from the deviceState loop: {loop_writes}"
    # the writer wrote what the loop asked for, the engage edge inside the hold included, once the lock was free
    assert params.get_bool("IsEngaged")
    temp = [w for w in writes if w[2] == "Offroad_TemperatureTooHigh"]
    assert len(temp) <= 1, temp  # the first loop's remove only; the moving text is no longer a write
    tici = [w for w in writes if w[2] == "Offroad_TiciSupport"]
    assert len(tici) <= 1, tici  # was one remove() every loop
    metered = [w for w in writes if w[2] == "NetworkMetered"]
    assert len(metered) <= 1, metered  # on change (and once a minute), was every loop
