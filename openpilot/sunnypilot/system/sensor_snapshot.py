"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(COMM): hardwared's sensor reads off the loop that publishes deviceState (push16, docs/fork/COMM-ISSUE.md section 9).

Routes 153 and 15d showed "TAKE CONTROL IMMEDIATELY / Low Communication Rate Between Processes" (commIssueAvgFreq)
because deviceState stopped for 4.12 s and 4.81 s: hardwared's loop sat 3.70 s in its "power" section (three sysfs
files) and 4.43 s in its "thermal" one (the thermal-zone files), with /data idle, both after minutes of CPU core 4 at
full load. A sysfs read runs the driver's code, under the driver's locks, so a read can wait for whatever holds them.

* SensorWorker: the reads of one section, on a thread of its own. The loop asks for a sweep (request() never waits)
  and takes the last finished one (`latest`, a Snapshot replaced whole, never changed). A read slower than
  SLOW_READ_S is logged with its name, its time, and how much of that time the thread ran, waited for a CPU, or slept.
* DeviceStateGuard: what the loop does with the snapshots it has. Up to FRESH_S old they are what it always published.
  A thermal snapshot older than that: the thermal status may rise but not fall (thermal_status(); an old power
  snapshot alone does not hold it, since the status is computed from the temperatures only); any snapshot past
  MASK_MAX_S: deviceState is not sent at all, which is what a stuck read did before - the comm checks see it as they
  always did. A read stuck STALL_REPORT_S gets one report, from a thread of its own: where the reading thread is in the
  kernel, and what the bound kernel threads of the busy cores (sched_diag.VIEW_CPUS) are doing. Episodes are logged
  when publishing stops and when the snapshots are fresh again.

Normal age is one loop (0.5 s): each loop publishes the sweep the loop before asked for. A read stuck for D seconds:
up to 5.5 s nothing pauses; up to 8 s publishing pauses at most 3 s, under selfdrived's 3.0 s (no alert); beyond that
the alerts come as before, MASK_MAX_S later. Every line here is at error level, so the qlogs keep it.
"""
import glob
import math
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Protocol

from openpilot.common.hardware.base import ThermalConfig
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.system.sched_diag import VIEW_CPUS

FRESH_S = 1.5          # a snapshot older than this is stale: the thermal status may rise, not fall
MASK_MAX_S = 6.0       # a snapshot older than this: deviceState is not sent (what a stuck read did before)
STALL_REPORT_S = 1.5   # a read still running this long gets one stall report
SLOW_READ_S = 0.3      # a finished read slower than this is logged (storage_diag.SLOW_SECTION_S)
MIN_LOG_INTERVAL_S = 10.
STACK_SUDO_INTERVAL_S = 600.


@dataclass(frozen=True)
class Snapshot:
  seq: int
  t_start: float
  t_end: float
  values: dict


def _read(path: str, limit: int = 8192) -> str | None:
  try:
    with open(path) as f:
      return f.read(limit).strip()
  except (OSError, UnicodeDecodeError):
    return None


def task_schedstat(tid: int | None) -> tuple[int, int, int] | None:
  """/proc/self/task/<tid>/schedstat: (ns on a CPU, ns runnable and waiting for one, times run); None without it."""
  if tid is None:
    return None
  text = _read(f"/proc/self/task/{tid}/schedstat")
  if not text:
    return None
  try:
    a, b, c = (int(x) for x in text.split()[:3])
  except ValueError:
    return None
  return a, b, c


def task_stat(tid: int, pid: str = "self") -> dict | None:
  """The fields of /proc/<pid>/task/<tid>/stat the reports need: comm, state, the CPU it last ran on."""
  text = _read(f"/proc/{pid}/task/{tid}/stat")
  if not text or ")" not in text:
    return None
  close = text.rindex(")")
  rest = text[close + 2:].split()
  try:
    return {"comm": text[text.index("(") + 1:close], "state": rest[0], "cpu": int(rest[36])}
  except (IndexError, ValueError):
    return None


class SensorWorker:
  """reads: (name, function) pairs, swept in order on this worker's thread whenever request() was called since the
  last sweep (requests made during a sweep merge into the next one). A read's exception stops the worker and is
  raised on the loop by raise_if_failed(), so it ends hardwared as it did when the loop made the read."""

  def __init__(self, name: str, reads: list[tuple[str, Callable[[], float]]], slow_read_s: float = SLOW_READ_S,
               min_log_interval: float = MIN_LOG_INTERVAL_S):
    self.name = name
    self.reads = list(reads)
    self.slow_read_s = slow_read_s
    self.min_log_interval = min_log_interval
    self.latest: Snapshot | None = None
    self.current: tuple[str, float, int] | None = None  # (read, started, sweep) while a read runs
    self.error: BaseException | None = None
    self.tid: int | None = None
    self.slow_sweeps = 0
    self.suppressed = 0
    self._last_log = -1e9
    self._seq = 0
    self._req = threading.Event()
    self._stop = threading.Event()
    self._first = threading.Event()
    self.thread = threading.Thread(target=self._run, name=f"hw_{name}", daemon=True)

  def start(self) -> None:
    self.thread.start()

  def request(self) -> None:
    self._req.set()

  def stop(self) -> None:
    self._stop.set()
    self._req.set()

  def wait_first(self, timeout: float | None = None) -> bool:
    """For the loop's first message only: the first sweep (or its error), as the loop's own read used to wait."""
    return self._first.wait(timeout)

  def age(self, now: float) -> float:
    s = self.latest
    return math.inf if s is None else now - s.t_start

  def raise_if_failed(self) -> None:
    if self.error is not None:
      raise self.error

  def _run(self) -> None:
    self.tid = threading.get_native_id()
    try:
      while True:
        self._req.wait()
        if self._stop.is_set():
          return
        self._req.clear()
        self._sweep()
    except BaseException as e:  # handed to the loop, which raises it
      self.error = e
    finally:
      self._first.set()

  def _sweep(self) -> None:
    sweep = self._seq + 1
    s0 = task_schedstat(self.tid)
    t0 = time.monotonic()
    values: dict = {}
    slow: list[tuple[str, float]] = []
    for name, fn in self.reads:
      t = time.monotonic()
      self.current = (name, t, sweep)
      values[name] = fn()
      dt = time.monotonic() - t
      if dt > self.slow_read_s:
        slow.append((name, round(dt, 3)))
    self.current = None
    t1 = time.monotonic()
    self._seq = sweep
    self.latest = Snapshot(sweep, t0, t1, values)
    self._first.set()
    if slow:
      self._log_slow(slow, t1 - t0, s0, task_schedstat(self.tid))

  def _log_slow(self, slow: list[tuple[str, float]], sweep_s: float, s0, s1) -> None:
    self.slow_sweeps += 1
    now = time.monotonic()
    if now - self._last_log < self.min_log_interval:
      self.suppressed += 1
      return
    self_s = None
    if s0 is not None and s1 is not None:
      ran, waited = (s1[0] - s0[0]) / 1e9, (s1[1] - s0[1]) / 1e9
      # sleeping = in the kernel waiting for something (a lock, the device); waiting = runnable, without a CPU
      self_s = {"ran": round(ran, 3), "waited_for_cpu": round(waited, 3), "slept": round(max(sweep_s - ran - waited, 0.), 3)}
    try:
      st = task_stat(self.tid) if self.tid is not None else None
      cloudlog.event("hardwared slow sensor read", error=True, worker=self.name, reads=slow, sweep_s=round(sweep_s, 3),
                     self_s=self_s, cpu=st["cpu"] if st else None, slow_sweeps=self.slow_sweeps,
                     suppressed=self.suppressed)
    except Exception:
      cloudlog.exception("slow sensor read log failed")
    self._last_log = now
    self.suppressed = 0


# ------------------------------------------------------------------------------------------ the thermal zones
def thermal_reads(cfg) -> list[tuple[str, Callable[[], float]]]:
  """Every zone of a ThermalConfig as a read, in ThermalConfig.get_msg()'s order. The name says which field and zone."""
  reads = []
  for f in fields(ThermalConfig):  # get_msg()'s fields
    v = getattr(cfg, f.name)
    if v is None:
      continue
    for i, zone in (enumerate(v) if isinstance(v, list) else ((None, v),)):
      key = f"{f.name}[{i}]" if i is not None else f.name
      reads.append((f"{key}:{zone.name}", zone.read))
  return reads


def thermal_msg(cfg, values: dict) -> dict:
  """What cfg.get_msg() returns, from a sweep of thermal_reads(cfg)."""
  ret = {}
  for f in fields(ThermalConfig):  # get_msg()'s fields
    v = getattr(cfg, f.name)
    if v is None:
      continue
    if isinstance(v, list):
      ret[f.name + "TempC"] = [values[f"{f.name}[{i}]:{zone.name}"] for i, zone in enumerate(v)]
    else:
      ret[f.name + "TempC"] = values[f"{f.name}:{v.name}"]
  return ret


# ------------------------------------------------------------------------------------------ the guard
@dataclass(frozen=True)
class Decision:
  send: bool         # no snapshot is past mask_max_s
  stale: bool        # the oldest snapshot is past fresh_s
  age: float         # the oldest snapshot's age
  stale_names: frozenset[str] = frozenset()  # every snapshot past fresh_s, by name: the thermal rule asks for "thermal"


class WorkerState(Protocol):
  """What the guard reads of a worker (a SensorWorker)."""
  name: str
  current: tuple[str, float, int] | None
  tid: int | None


@dataclass
class _Episode:
  start: float
  worker: str
  max_age_s: float = 0.
  read: str | None = None
  held_at: float | None = None
  held_s: float = 0.


class DeviceStateGuard:
  """update() once per published loop with the snapshots the loop took. kthread_view: a callable that gives the
  starvation sampler's last view of the bound kernel threads (sched_diag.StarvationSampler.view), for the reports."""

  def __init__(self, workers: list, fresh_s: float = FRESH_S, mask_max_s: float = MASK_MAX_S,
               stall_report_s: float = STALL_REPORT_S, min_log_interval: float = MIN_LOG_INTERVAL_S,
               kthread_view: Callable[[], dict] | None = None, sudo_stacks: bool = False):
    self.workers: list[WorkerState] = workers
    self.fresh_s = fresh_s
    self.mask_max_s = mask_max_s
    self.stall_report_s = stall_report_s
    self.min_log_interval = min_log_interval
    self.kthread_view = kthread_view
    self.sudo_stacks = sudo_stacks
    self.episode: _Episode | None = None
    self.episodes = 0
    self.suppressed = 0
    self._last_log = -1e9
    self._reported: set[tuple[str, int]] = set()
    self._last_sudo = -1e9
    self.report_threads: list[threading.Thread] = []

  @staticmethod
  def thermal_status(prev, new, stale: bool):
    """Old temperatures never lower the thermal status: while the thermal snapshot is stale it can only rise. The caller
    passes the thermal snapshot's own staleness ("thermal" in Decision.stale_names), not the oldest snapshot's."""
    return max(prev, new) if stale else new

  def update(self, now: float, snapshots: dict[str, Snapshot | None]) -> Decision:
    ages = {k: (math.inf if s is None else now - s.t_start) for k, s in snapshots.items()}
    age = max(ages.values(), default=0.)
    stale = age > self.fresh_s
    stale_names = frozenset(k for k, a in ages.items() if a > self.fresh_s)
    send = age <= self.mask_max_s
    self._check_stuck(now)
    if stale:
      oldest = max(ages, key=lambda k: ages[k])
      w = next((x for x in self.workers if x.name == oldest), None)
      cur = w.current if w is not None else None
      ep = self.episode
      if ep is None:
        ep = self.episode = _Episode(start=now, worker=oldest)
      if age >= ep.max_age_s:
        ep.max_age_s, ep.worker = age, oldest
        if cur is not None:
          ep.read = cur[0]
      if not send and ep.held_at is None:
        ep.held_at = now
        cloudlog.event("deviceState held: sensors stale", error=True, age_s=round(age, 3), worker=oldest,
                       read=ep.read, mask_max_s=self.mask_max_s)
      if send and ep.held_at is not None:
        ep.held_s += now - ep.held_at
        ep.held_at = None
    elif self.episode is not None:
      self._end_episode(now, self.episode)
    return Decision(send=send, stale=stale, age=age, stale_names=stale_names)

  def _end_episode(self, now: float, ep: _Episode) -> None:
    self.episode = None
    if ep.held_at is not None:
      ep.held_s += now - ep.held_at
    self.episodes += 1
    if now - self._last_log < self.min_log_interval:
      self.suppressed += 1
      return
    cloudlog.event("deviceState resumed" if ep.held_s > 0. else "deviceState masked a sensor stall", error=True,
                   max_age_s=round(ep.max_age_s, 3), worker=ep.worker, read=ep.read, stale_s=round(now - ep.start, 3),
                   held_s=round(ep.held_s, 3), episodes=self.episodes, suppressed=self.suppressed)
    self._last_log = now
    self.suppressed = 0

  def _check_stuck(self, now: float) -> None:
    for w in self.workers:
      cur = w.current
      if cur is None:
        continue
      name, started, sweep = cur
      if now - started >= self.stall_report_s and (w.name, sweep) not in self._reported:
        self._reported.add((w.name, sweep))
        sudo = self.sudo_stacks and now - self._last_sudo >= STACK_SUDO_INTERVAL_S
        if sudo:
          self._last_sudo = now
        t = threading.Thread(target=self._report, args=(w, name, now - started, sudo), name="hw_stall_report",
                             daemon=True)
        self.report_threads = [x for x in self.report_threads if x.is_alive()][-7:] + [t]
        t.start()
    if len(self._reported) > 64:
      self._reported = set(sorted(self._reported, key=lambda k: k[1])[-16:])

  def _report(self, w: WorkerState, read: str, stuck_s: float, sudo: bool) -> None:
    try:
      tid = w.tid
      worker = None
      if tid is not None:
        base = f"/proc/self/task/{tid}"
        worker = {"tid": tid, "stat": task_stat(tid), "wchan": _read(f"{base}/wchan"),
                  "schedstat": task_schedstat(tid), "stack": _read(f"{base}/stack")}
      threads = {}
      for p in sorted(glob.glob("/proc/self/task/*")):
        try:
          t = int(os.path.basename(p))
        except ValueError:
          continue
        threads[str(t)] = task_stat(t)
      kthreads = self.kthread_view() if self.kthread_view is not None else None
      stacks = _sudo_stacks(kthreads) if sudo else None
      cloudlog.event("hardwared sensor stall report", error=True, worker=w.name, read=read, stuck_s=round(stuck_s, 3),
                     thread=worker, threads=threads, kthreads=kthreads, kthread_stacks=stacks)
    except Exception:
      cloudlog.exception("sensor stall report failed")


def _sudo_stacks(kthreads: dict | None, cpus: tuple[int, ...] = VIEW_CPUS, limit: int = 4) -> dict | None:
  """The kernel stacks of the runnable bound kernel threads of the busy cores (root only: sudo -n, 2 s at most)."""
  if not kthreads:
    return None
  out = {}
  for t in kthreads.get("threads", []):
    if t.get("cpu_bound") in cpus and t.get("state") == "R" and len(out) < limit:
      try:
        r = subprocess.run(["sudo", "-n", "cat", f"/proc/{t['pid']}/stack"], capture_output=True, text=True, timeout=2,
                           check=False)
        out[f"{t['comm']}:{t['pid']}"] = (r.stdout or r.stderr).strip()[:1500]
      except (OSError, subprocess.SubprocessError) as e:
        out[f"{t['comm']}:{t['pid']}"] = repr(e)[:200]
  return out
