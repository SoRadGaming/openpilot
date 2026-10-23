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
  value, in the place of its first request. Different keys are written in the order they were first asked for.
* An unknown key or a value of the wrong type raises on the caller, as Params.put does.
* Unlike Params.put(block=False) it also covers remove() and the offroad alerts, and nothing it is given stays in a
  queue: Params::putNonBlocking can leave a value queued until the next put on the same handle when it arrives just
  as that handle's writer thread is finishing, which a write made only on change would never repair.
* At interpreter exit it waits up to FLUSH_AT_EXIT_S for what is pending.
"""
import atexit
import threading
from collections import OrderedDict
from collections.abc import Callable

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert

FLUSH_AT_EXIT_S = 3.0


class ParamsWriter:
  def __init__(self, name: str = "params_writer"):
    self.name = name
    self._params = Params()
    self._cv = threading.Condition()
    self._pending: OrderedDict[str, Callable[[], None]] = OrderedDict()
    self._last: dict[str, tuple] = {}
    self._busy = False
    self._stop = False
    self.written = 0  # writes applied, for tests and the logs
    self._thread = threading.Thread(target=self._run, name=name, daemon=True)
    self._thread.start()
    atexit.register(self.flush, FLUSH_AT_EXIT_S)

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

  def flush(self, timeout: float | None = None) -> bool:
    """Wait until everything asked for so far is written. False if it was not within timeout."""
    with self._cv:
      return self._cv.wait_for(lambda: not self._pending and not self._busy, timeout)

  def stop(self, timeout: float | None = None) -> bool:
    """Flush, then end the thread. Requests after stop() are dropped."""
    ok = self.flush(timeout)
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
      self._pending[key] = op  # an existing key keeps its place, with the newer op
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
        key, op = self._pending.popitem(last=False)
        self._busy = True
      try:
        op()
        self.written += 1
      except Exception:
        cloudlog.exception(f"{self.name}: writing {key} failed")
