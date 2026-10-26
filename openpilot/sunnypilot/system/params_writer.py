"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(COMM-ISSUE): Params writes for a loop that publishes a service selfdrived checks for liveness.

Params.put(block=True), Params.remove() and set_offroad_alert() take <params>/.lock with flock(LOCK_EX) and fsync the
params directory while they hold it - remove() takes the lock even when the key is absent. When an ext4 journal commit
on /data stalls, every one of those calls waits for it, and so does every caller queued on the lock behind it. On the
comma 4 in routes 14d and 152 the commits stalled for 4-8 s; hardwared's deviceState loop made those calls every
0.5 s, so deviceState stopped for the stall and selfdrived raised commIssueAvgFreq (gap > 3 s) or commIssue (> 5 s):
"TAKE CONTROL IMMEDIATELY", a soft disable and the car braking at 95-110 km/h (docs/fork/COMM-ISSUE.md).

ParamsWriter's methods return at once. One daemon thread applies what they were asked with the same blocking calls,
so what lands on disk is what upstream writes, a little later; a stall now holds that thread instead of the loop.

* Per key the newest request wins: a key asked for again before it was written is written once, with the newer
  value, in the place of its first request (it does not move behind keys asked for since). Different keys are
  written in the order they were first asked for.
* An unknown key or a value of the wrong type raises on the caller, as Params.put does.
* Unlike Params.put(block=False) it also covers remove() and the offroad alerts, and nothing it is given stays in a
  queue: Params::putNonBlocking can leave a value queued until the next put on the same handle when it arrives just
  as that handle's writer thread is finishing, which a write made only on change would never repair.
* When the process ends it waits up to FLUSH_AT_EXIT_S for what is pending. That is a multiprocessing finalizer, not
  an atexit handler: manager starts every PythonProcess with multiprocessing (fork), and such a child leaves through
  os._exit, which skips atexit; multiprocessing runs its finalizers both there and at a normal interpreter exit.
* A write that takes longer than slow_write_s (SLOW_WRITE_S) is logged at error level, so the qlogs keep it: "params
  writer slow write", with the key, how long the write took and how long since it was asked for, and the disk's
  /proc/diskstats delta over the write. The loops no longer wait in a /data stall, so this thread is where one shows.
  At most one line per min_log_interval s; the slow writes in between are counted into the next line.
* in_flight(key) (push15): asked for and not written yet - pending, or being written now. It turns False only once
  the write has finished (or failed), so a read made after in_flight() said False sees what was written. selfdrived's
  distance-button press keeps its own value while its write is in flight (FORK(CLOSE-FOLLOW), distance_bars.py).
"""
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Callable
from multiprocessing import util as mp_util

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.sunnypilot.system.storage_diag import MIN_LOG_INTERVAL_S, SLOW_SECTION_S, disk_devices, diskstats_delta, read_diskstats

FLUSH_AT_EXIT_S = 3.0  # manager SIGKILLs a daemon 5 s after its SIGINT
SLOW_WRITE_S = SLOW_SECTION_S
EXIT_FLUSH_PRIORITY = 10  # multiprocessing runs finalizers with a priority >= 0 first, highest first


def _flush_at_exit(ref: "weakref.ref[ParamsWriter]") -> None:
  writer = ref()
  if writer is not None:
    writer.flush(FLUSH_AT_EXIT_S)


class ParamsWriter:
  def __init__(self, name: str = "params_writer", slow_write_s: float = SLOW_WRITE_S,
               min_log_interval: float = MIN_LOG_INTERVAL_S):
    self.name = name
    self.slow_write_s = slow_write_s
    self.min_log_interval = min_log_interval
    self._params = Params()
    self._devices = disk_devices(self._params.get_param_path())  # procfs and sysfs only
    self._cv = threading.Condition()
    self._pending: OrderedDict[str, tuple[str, Callable[[], None]]] = OrderedDict()
    self._queued_at: dict[str, float] = {}  # when each pending key was first asked for
    self._last: dict[str, tuple] = {}
    self._writing: str | None = None  # the key whose write is on the thread now
    self._busy = False
    self._stop = False
    self.written = 0  # writes applied, for tests and the logs
    self.slow_writes = 0
    self._suppressed = 0
    self._last_log = -1e9
    self._thread = threading.Thread(target=self._run, name=name, daemon=True)
    self._thread.start()
    self._exit_flush = mp_util.Finalize(None, _flush_at_exit, args=(weakref.ref(self),), exitpriority=EXIT_FLUSH_PRIORITY)

  @property
  def native_id(self) -> int | None:
    return self._thread.native_id

  def put(self, key: str, value, if_changed: bool = False) -> None:
    """Params.put(key, value, block=True), on the writer's thread. if_changed: skip it when the last request for this
    key through this writer was the same put."""
    self._params.python2cpp(type(value), self._params.get_type(key), value, key)  # raises as Params.put would
    self._submit(key, ("put", value), lambda: self._params.put(key, value, block=True), if_changed)

  def put_bool(self, key: str, value, if_changed: bool = False) -> None:
    """Params.put_bool(key, value, block=True), on the writer's thread."""
    self._params.check_key(key)
    value = bool(value)
    self._submit(key, ("put_bool", value), lambda: self._params.put_bool(key, value, block=True), if_changed)

  def remove(self, key: str) -> None:
    """Params.remove(key), on the writer's thread."""
    self._params.check_key(key)
    self._submit(key, ("remove",), lambda: self._params.remove(key), False)

  def offroad_alert(self, alert: str, show_alert: bool, extra_text: str | None = None) -> None:
    """alertmanager.set_offroad_alert(alert, show_alert, extra_text), on the writer's thread."""
    self._params.check_key(alert)
    self._submit(alert, ("alert", show_alert, extra_text), lambda: set_offroad_alert(alert, show_alert, extra_text), False)

  def in_flight(self, key: str) -> bool:
    """key asked for and its write not finished yet: pending, or being written now."""
    with self._cv:
      return key in self._pending or self._writing == key

  def flush(self, timeout: float | None = None) -> bool:
    """Wait until everything asked for so far is written. False if it was not within timeout."""
    with self._cv:
      return self._cv.wait_for(lambda: not self._pending and not self._busy, timeout)

  def stop(self, timeout: float | None = None) -> bool:
    """Flush, then end the thread. Requests after stop() are dropped."""
    ok = self.flush(timeout)
    self._exit_flush.cancel()
    with self._cv:
      self._stop = True
      self._cv.notify_all()
    self._thread.join(timeout)
    return ok

  def _submit(self, key: str, state: tuple, op: Callable[[], None], if_changed: bool) -> None:
    with self._cv:
      if self._stop or (if_changed and self._last.get(key) == state):
        return
      self._last[key] = state
      if key not in self._pending:
        self._queued_at[key] = time.monotonic()
      self._pending[key] = (state[0], op)  # an existing key keeps its place, with the newer op
      self._cv.notify_all()

  def _run(self) -> None:
    while True:
      with self._cv:
        while not self._pending:
          self._busy = False
          self._cv.notify_all()
          if self._stop:
            return
          self._cv.wait()
        key, (kind, op) = self._pending.popitem(last=False)
        queued_at = self._queued_at.pop(key)
        self._busy = True
        self._writing = key  # (with the pop: a key is pending or being written, never neither while in flight)
      t = time.monotonic()
      disk = read_diskstats(self._devices)
      ok = False
      try:
        op()
        ok = True
      except Exception:
        cloudlog.exception(f"{self.name}: writing {key} failed")
      with self._cv:
        self._writing = None
        if ok:
          self.written += 1
      op_s = time.monotonic() - t
      if op_s > self.slow_write_s:
        self._log_slow(key, kind, op_s, queued_at, disk)

  def _log_slow(self, key: str, kind: str, op_s: float, queued_at: float, disk_before: dict) -> None:
    self.slow_writes += 1
    now = time.monotonic()
    if now - self._last_log < self.min_log_interval:
      self._suppressed += 1
      return
    with self._cv:
      pending = list(self._pending)
    try:
      cloudlog.event("params writer slow write", error=True, writer=self.name, key=key, op=kind, op_s=round(op_s, 3),
                     since_request_s=round(now - queued_at, 3), pending=pending,
                     disk=diskstats_delta(disk_before, read_diskstats(self._devices)), slow_writes=self.slow_writes,
                     suppressed=self._suppressed)
    except Exception:
      cloudlog.exception("params writer slow write log failed")
    self._last_log = now
    self._suppressed = 0
