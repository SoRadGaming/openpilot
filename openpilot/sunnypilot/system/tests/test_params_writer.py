"""FORK(COMM-ISSUE): ParamsWriter - Params writes that never make their caller wait on /data."""
import contextlib
import fcntl
import os
import subprocess
import sys
import threading
import time
from unittest import mock

from openpilot.common.basedir import BASEDIR
from openpilot.common.params import Params, UnknownKeyName
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.sunnypilot.system import params_writer
from openpilot.sunnypilot.system.params_writer import ParamsWriter

SLOW_WRITE = "params writer slow write"

# A process that hands its writer a put and ends while <params>/.lock is held: started the way manager starts a
# PythonProcess (multiprocessing, fork - main() returns into multiprocessing's bootstrap, which leaves through
# os._exit), or as a plain interpreter (out through atexit).
EXIT_CHILD = """
import multiprocessing, sys

def main(key, value):
  from openpilot.sunnypilot.system.params_writer import ParamsWriter
  writer = ParamsWriter("exit_test")
  writer.put(key, value)  # returns at once; the lock is held
  print("put", flush=True)

how, key, value = sys.argv[1], sys.argv[2], float(sys.argv[3])
if how == "manager":
  p = multiprocessing.get_context("fork").Process(target=main, args=(key, value))
  p.start()
  p.join()
  sys.exit(p.exitcode)
main(key, value)
"""


def params_lock_path() -> str:
  # Params::put / remove lock <params root>/.lock; get_param_path() is <params root>/<prefix>
  return os.path.join(os.path.dirname(Params().get_param_path()), ".lock")


@contextlib.contextmanager
def held_params_lock(max_hold: float = 5.):
  """What a put stuck in a slow journal commit looks like to everyone else: <params>/.lock taken and not let go. Held
  by a thread of its own and let go after max_hold s at the latest, so a writer that wrongly wrote on its caller's
  thread fails a test instead of deadlocking it."""
  held, release = threading.Event(), threading.Event()

  def holder():
    fd = os.open(params_lock_path(), os.O_CREAT | os.O_RDONLY, 0o775)
    try:
      fcntl.flock(fd, fcntl.LOCK_EX)
      held.set()
      release.wait(max_hold)
    finally:
      fcntl.flock(fd, fcntl.LOCK_UN)
      os.close(fd)

  t = threading.Thread(target=holder, daemon=True)
  t.start()
  assert held.wait(5.)
  try:
    yield
  finally:
    release.set()
    t.join(5.)


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

  def test_a_key_asked_for_again_keeps_its_first_place(self):
    """'in the place of its first request': the newer value does not move its key behind the keys asked for since."""
    order = self.record_writes()
    with held_params_lock():
      self.writer.put("UptimeOffroad", 1.0)  # taken by the writer, which then waits on the lock
      time.sleep(0.3)
      self.writer.put("UptimeOnroad", 1.0)
      self.writer.put_bool("IsEngaged", True)
      self.writer.put("UptimeOnroad", 2.0)
    assert self.writer.flush(5.)
    assert order == [("put", "UptimeOffroad", 1.0), ("put", "UptimeOnroad", 2.0), ("put_bool", "IsEngaged", True)]

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

  def test_in_flight_until_the_write_has_finished(self):
    """push15 (distance_bars.py): a key is in flight from its request until its write has finished - pending, then
    being written (no longer pending), never neither in between - and a read made once it is not sees the write."""
    put = self.writer._params.put
    entered, go = threading.Event(), threading.Event()

    def slow(key, value, block=False):
      entered.set()
      assert go.wait(5.)
      put(key, value, block=block)

    self.patch_params("put", slow)
    assert not self.writer.in_flight("UptimeOffroad")
    with self.writer._cv:                      # held: the writer cannot take it off the queue yet
      self.writer.put("UptimeOffroad", 1.0)
      assert self.writer.in_flight("UptimeOffroad") and "UptimeOffroad" in self.writer._pending
    assert entered.wait(5.)
    assert "UptimeOffroad" not in self.writer._pending
    assert self.writer.in_flight("UptimeOffroad"), "being written"
    self.writer.put("UptimeOnroad", 2.0)
    assert self.writer.in_flight("UptimeOnroad") and not self.writer.in_flight("IsEngaged")
    assert self.params.get("UptimeOffroad") is None
    go.set()
    deadline = time.monotonic() + 5.
    while self.writer.in_flight("UptimeOffroad"):
      assert time.monotonic() < deadline
    assert self.params.get("UptimeOffroad") == 1.0, "not in flight: on disk"
    assert self.writer.flush(5.)
    assert not self.writer.in_flight("UptimeOnroad") and self.params.get("UptimeOnroad") == 2.0

  def test_in_flight_through_a_newer_request_and_a_stall(self):
    """Asked again while being written, the key stays in flight until the newer value is written; with <params>/.lock
    held (a /data stall) everything asked for stays in flight; a write that failed is not in flight."""
    order = self.record_writes()
    with held_params_lock():
      self.writer.put("UptimeOffroad", 1.0)    # taken by the writer, which then waits on the lock
      time.sleep(0.3)
      self.writer.put("UptimeOffroad", 2.0)
      time.sleep(0.2)
      assert self.writer.in_flight("UptimeOffroad")
    assert self.writer.flush(5.)
    assert not self.writer.in_flight("UptimeOffroad")
    assert order == [("put", "UptimeOffroad", 1.0), ("put", "UptimeOffroad", 2.0)]
    assert self.params.get("UptimeOffroad") == 2.0

    def fail(key, value, block=False):
      raise OSError("disk said no")
    self.patch_params("put", fail)
    self.writer.put("UptimeOnroad", 3.0)
    assert self.writer.flush(5.)
    assert not self.writer.in_flight("UptimeOnroad")


class TestParamsWriterSlowWriteLog(OpenpilotTestCase):
  """After the fix a /data stall holds the writer's thread, not the loop: the writer's line is the stall's trace in the
  routes (the loop's 'slow loop section' no longer fires for it)."""

  def setUp(self):
    super().setUp()
    patcher = mock.patch.object(params_writer, "cloudlog")
    self.log = patcher.start()
    self.addCleanup(patcher.stop)

  def writer(self, **kw) -> ParamsWriter:
    w = ParamsWriter("test_slow_write", **kw)
    self.addCleanup(w.stop, 5.)
    return w

  def slow_put(self, writer: ParamsWriter, key: str, value: float, hold: float = 0.6) -> None:
    with held_params_lock():
      writer.put(key, value)
      time.sleep(hold)
    assert writer.flush(5.)

  def lines(self) -> list[dict]:
    return [c.kwargs for c in self.log.event.call_args_list if c.args == (SLOW_WRITE,)]

  def test_a_slow_write_is_logged_with_the_disk_counters(self):
    w = self.writer()
    self.slow_put(w, "UptimeOnroad", 5.0)
    assert Params().get("UptimeOnroad") == 5.0
    lines = self.lines()
    assert len(lines) == 1, self.log.event.call_args_list
    kw = lines[0]
    assert kw["error"] is True  # errorLogMessage: the qlogs keep it
    assert (kw["writer"], kw["key"], kw["op"]) == ("test_slow_write", "UptimeOnroad", "put")
    assert 0.4 < kw["op_s"] < 5. and kw["since_request_s"] >= kw["op_s"]
    assert isinstance(kw["disk"], dict) and kw["pending"] == []
    assert (kw["slow_writes"], kw["suppressed"]) == (1, 0)
    assert not self.log.exception.called

  def test_a_write_under_the_threshold_is_not_logged(self):
    w = self.writer(slow_write_s=5.)
    self.slow_put(w, "UptimeOnroad", 1.0)
    assert self.lines() == [] and w.slow_writes == 0

  def test_at_most_one_line_per_interval(self):
    w = self.writer(min_log_interval=1.5)
    self.slow_put(w, "UptimeOnroad", 1.0)   # logged
    self.slow_put(w, "UptimeOffroad", 1.0)  # inside 1.5 s of it: counted
    time.sleep(1.0)
    self.slow_put(w, "UptimeOnroad", 2.0)   # logged, with the one in between
    assert [(k["key"], k["slow_writes"], k["suppressed"]) for k in self.lines()] == [("UptimeOnroad", 1, 0), ("UptimeOnroad", 3, 1)]


class TestParamsWriterAtExit(OpenpilotTestCase):
  def exit_with_a_write_pending(self, how: str):
    """The child puts and ends while the lock is held; the lock goes 0.5 s later. What is on disk afterwards?"""
    proc = None
    try:
      with held_params_lock(max_hold=20.):
        proc = subprocess.Popen([sys.executable, "-c", EXIT_CHILD, how, "UptimeOnroad", "42.0"], cwd=BASEDIR,
                                stdout=subprocess.PIPE, text=True)
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "put"
        time.sleep(0.5)  # the child is on its way out, its write still waiting on the lock
      assert proc.wait(30) == 0
    finally:
      if proc is not None:
        if proc.poll() is None:
          proc.kill()
          proc.wait()
        if proc.stdout is not None:
          proc.stdout.close()
    return Params().get("UptimeOnroad")

  def test_a_pending_write_lands_when_a_process_manager_started_ends(self):
    """manager's PythonProcess: multiprocessing with fork, out through os._exit, which skips atexit - selfdrived at
    key-off with Offroad_ExcessiveActuation still pending was the case."""
    assert self.exit_with_a_write_pending("manager") == 42.0

  def test_a_pending_write_lands_when_a_plain_interpreter_ends(self):
    assert self.exit_with_a_write_pending("plain") == 42.0

  def test_stop_cancels_the_exit_flush(self):
    w = ParamsWriter("test_stop")
    assert w.stop(5.)
    assert not w._exit_flush.still_active()
