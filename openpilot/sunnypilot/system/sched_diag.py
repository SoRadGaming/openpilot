"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(COMM): which CPU's kernel housekeeping is being starved, in the routes (push16, docs/fork/COMM-ISSUE.md section 9).

Route 157's kernel log showed a per-CPU kernel worker on CPU 4 runnable and not run for minutes ("BUG: workqueue lockup
- pool cpus=4 ... stuck for 150s") while card, controlsd and selfdrived held core 4 at FIFO 53; the qlog-only routes
(153, 15d) have no kernel log. This samples, passively, what the kernel already keeps per task:

* /proc/<pid>/schedstat (time on a CPU, time runnable and waiting for one, times run) of every per-CPU kernel thread
  (kworker/N:M, kworker/N:MH, ksoftirqd/N), every POLL_S: the wait each CPU's housekeeping had, and a thread that is
  runnable at two samples and ran not at all in between (its wait is only counted once it runs, so a stuck one would
  otherwise show nothing until it is over).
* /proc/stat: each CPU's busy share. /proc/schedstat (per CPU: all tasks' run time and wait), where the kernel has it.
* Once a window, the SCHED_FIFO / SCHED_RR processes (card, controlsd and selfdrived waiting for each other among them)
  and hardwared's own threads: run time and wait.

Every file is optional: a missing one gives None and the probe says which were there (`caps`, in the first line of
each start). Reads are procfs only, never cmdline (it takes the target's mmap lock), and hold no lock of their own.
Only drives are sampled - nothing logged while parked reaches a route. Lines, all at error level so the qlogs keep them:

* "sched starvation": a window (SUMMARY_S) in which some CPU's bound kernel threads waited WAIT_LOG_S or more in all,
  or one was runnable and not run for STUCK_LOG_S.
* "kthread runnable, not run": as it happens, once per thread and episode (at most one per MIN_LOG_INTERVAL_S).
* "sched baseline": the first window of each drive and every BASELINE_EVERY-th, whatever it held.
* "sched drive summary": when a drive ends, and again inside the next drive's first window line (`prev_drive`), since a
  line logged as the car goes offroad may miss the route.
"""
import os
import re
import threading
import time
from collections.abc import Callable

from openpilot.common.swaglog import cloudlog

BOUND = re.compile(r"^(?:kworker/(\d+):\d+H?|ksoftirqd/(\d+))$")
POLL_S = 2.
SUMMARY_S = 60.
RESCAN_S = 30.
WAIT_LOG_S = 1.0       # one CPU's bound kernel threads waited this long in a window, summed
STUCK_LOG_S = 2.0      # one bound kernel thread runnable and not run this long
BASELINE_EVERY = 10
MIN_LOG_INTERVAL_S = 10.
SLOW_POLL_S = 0.3
VIEW_CPUS = (4, 5)     # the cores the real-time processes fill: the stall report's view
SCHEDSTAT_VERSIONS = (15, 16, 17)


def read_text(path: str, limit: int = 65536) -> str | None:
  try:
    with open(path) as f:
      return f.read(limit)
  except (OSError, UnicodeDecodeError):
    return None


def parse_pid_stat(text: str | None) -> dict | None:
  """/proc/<pid>/stat (4.9 and later): comm, state, ppid, utime + stime (ticks), the CPU it last ran on, policy."""
  if not text or ")" not in text:
    return None
  close = text.rindex(")")
  rest = text[close + 2:].split()
  try:
    return {"comm": text[text.index("(") + 1:close], "state": rest[0], "ppid": int(rest[1]),
            "ticks": int(rest[11]) + int(rest[12]), "cpu": int(rest[36]), "policy": int(rest[38])}
  except (IndexError, ValueError):
    return None


def parse_pid_schedstat(text: str | None) -> tuple[int, int, int] | None:
  """/proc/<pid>/schedstat: (ns on a CPU, ns runnable and waiting, times run). None if absent or malformed."""
  if not text:
    return None
  parts = text.split()
  if len(parts) < 3:
    return None
  try:
    return int(parts[0]), int(parts[1]), int(parts[2])
  except ValueError:
    return None


def parse_proc_schedstat(text: str | None) -> dict[int, tuple[int, int, int]] | None:
  """/proc/schedstat, versions 15-17: per CPU (ns its tasks ran, ns its tasks waited, timeslices). None if absent or of
  a version whose cpu lines this does not know."""
  if not text:
    return None
  lines = text.splitlines()
  try:
    version = int(lines[0].split()[1]) if lines and lines[0].startswith("version") else None
  except (IndexError, ValueError):
    return None
  if version not in SCHEDSTAT_VERSIONS:
    return None
  out = {}
  for line in lines:
    parts = line.split()
    if parts and parts[0].startswith("cpu") and parts[0][3:].isdigit() and len(parts) >= 10:
      try:
        out[int(parts[0][3:])] = (int(parts[7]), int(parts[8]), int(parts[9]))
      except ValueError:
        continue
  return out or None


def parse_proc_stat(text: str | None) -> dict[int, tuple[int, int]] | None:
  """/proc/stat: per online CPU (busy ticks, all ticks); idle and iowait are not busy."""
  if not text:
    return None
  out = {}
  for line in text.splitlines():
    parts = line.split()
    if parts and parts[0].startswith("cpu") and parts[0][3:].isdigit():
      try:
        v = [int(x) for x in parts[1:]]
      except ValueError:
        continue
      total = sum(v[:8])  # user nice system idle iowait irq softirq steal (guest is inside user)
      idle = v[3] + (v[4] if len(v) > 4 else 0)
      out[int(parts[0][3:])] = (total - idle, total)
  return out or None


def bound_cpu(comm: str) -> int | None:
  m = BOUND.match(comm)
  if m is None:
    return None
  return int(m.group(1) if m.group(1) is not None else m.group(2))


def probe(proc: str = "/proc", self_pid: int | None = None) -> dict:
  """Which of the files the kernel offers. schedstat reading "0 0 0" for this very process means it is compiled in
  but off (a process that runs has run time)."""
  pid = str(self_pid if self_pid is not None else os.getpid())
  own = parse_pid_schedstat(read_text(os.path.join(proc, pid, "schedstat")))
  try:
    affinity = sorted(os.sched_getaffinity(0)) if self_pid is None else None
  except (AttributeError, OSError):
    affinity = None
  return {
    "pid_schedstat": None if own is None else own[0] > 0,
    "proc_schedstat": parse_proc_schedstat(read_text(os.path.join(proc, "schedstat"))) is not None,
    "proc_stat": parse_proc_stat(read_text(os.path.join(proc, "stat"))) is not None,
    "pressure": os.path.exists(os.path.join(proc, "pressure", "cpu")),
    "sched_debug": os.path.exists(os.path.join(proc, "sched_debug")),
    "own_stack": read_text(os.path.join(proc, pid, "stack"), 64) is not None,
    "affinity": affinity,
  }


class _Win:
  """One window's accumulators (also used for a whole drive)."""

  def __init__(self, start: float):
    self.start = start
    self.kwait: dict[int, float] = {}       # cpu -> s its bound kernel threads waited
    self.kwait_max: dict[int, float] = {}   # cpu -> the most one of them waited
    self.stuck_max: dict[int, float] = {}   # cpu -> the longest one was runnable and not run
    self.per_thread: dict[tuple[int, int], float] = {}  # (pid, cpu) -> s it waited in this window
    self.polls = 0
    self.slow_polls = 0


class StarvationSampler:
  """poll(now) does one sample (and, when due, a rescan and a window line). start() runs it every POLL_S on a daemon
  thread; hardwared sets `started` every loop."""

  def __init__(self, proc: str = "/proc", self_pid: int | None = None, clock: Callable[[], float] = time.monotonic,
               poll_s: float = POLL_S, summary_s: float = SUMMARY_S, rescan_s: float = RESCAN_S):
    self.proc = proc
    self.self_pid = self_pid if self_pid is not None else os.getpid()
    self.clock = clock
    self.poll_s, self.summary_s, self.rescan_s = poll_s, summary_s, rescan_s
    self.started = False
    self.caps = probe(proc, self_pid)
    self._caps_logged = False
    self.kthreads: dict[int, tuple[str, int]] = {}  # pid -> (comm, bound cpu)
    self.rt: dict[int, str] = {}                    # FIFO / RR processes: pid -> comm
    self._prev: dict[int, tuple] = {}               # pid -> (t, state, exec, delay, pcount, ticks)
    self._stuck_since: dict[int, float] = {}
    self._stuck_logged: set[int] = set()
    self._last_rescan = -1e9
    self._win: _Win | None = None
    self._drive: _Win | None = None
    self._windows = 0
    self._starved_windows = 0
    self._worst: dict | None = None
    self.prev_drive: dict | None = None
    self._prev_stat: dict | None = None
    self._drive_stat: dict | None = None
    self._prev_sched: dict | None = None
    self._prev_rt: dict[str, tuple[int, int]] = {}
    self._last_kline = -1e9
    self._klines_suppressed = 0
    self._was_started = False
    self._view: dict = {"t": None, "threads": []}
    self._lock = threading.Lock()
    self._stop = threading.Event()
    self.thread: threading.Thread | None = None

  # ---------------------------------------------------------------------------------------------- thread
  def start(self) -> None:
    self.thread = threading.Thread(target=self._run, name="hw_sched_diag", daemon=True)
    self.thread.start()

  def stop(self) -> None:
    self._stop.set()

  def _run(self) -> None:
    while not self._stop.wait(self.poll_s):
      try:
        self.poll(self.clock())
      except Exception:
        cloudlog.exception("sched diag poll failed")  # a diagnostic never takes hardwared down

  def view(self) -> dict:
    with self._lock:
      return {"t": self._view["t"], "threads": [dict(t) for t in self._view["threads"]]}

  # ---------------------------------------------------------------------------------------------- one sample
  def poll(self, now: float) -> None:
    started = self.started
    if started and not self._was_started:
      self._drive_start(now)
    elif not started and self._was_started:
      self._drive_end(now)
    self._was_started = started
    if not started:
      return
    t0 = time.monotonic()
    if now - self._last_rescan >= self.rescan_s:
      self._rescan()
      self._last_rescan = now
    self._sample_kthreads(now)
    if self._win is not None and now - self._win.start >= self.summary_s:
      self._close_window(now)
    dt = time.monotonic() - t0
    for w in (self._win, self._drive):
      if w is not None:
        w.polls += 1
        w.slow_polls += dt > SLOW_POLL_S

  def _rescan(self) -> None:
    kthreads, rt = {}, {}
    try:
      pids = [p for p in os.listdir(self.proc) if p.isdigit()]
    except OSError:
      return
    for p in pids:
      st = parse_pid_stat(read_text(os.path.join(self.proc, p, "stat"), 1024))
      if st is None:
        continue
      if st["ppid"] == 2:
        cpu = bound_cpu(st["comm"])
        if cpu is not None:
          kthreads[int(p)] = (st["comm"], cpu)
      elif st["policy"] in (1, 2):  # SCHED_FIFO, SCHED_RR
        rt[int(p)] = st["comm"]
    self.kthreads, self.rt = kthreads, rt
    for gone in set(self._prev) - set(kthreads):
      self._prev.pop(gone, None)
      self._stuck_since.pop(gone, None)
      self._stuck_logged.discard(gone)

  def _sample_kthreads(self, now: float) -> None:
    win, drive = self._win, self._drive
    view = []
    for pid, (comm, cpu) in list(self.kthreads.items()):
      base = os.path.join(self.proc, str(pid))
      st = parse_pid_stat(read_text(os.path.join(base, "stat"), 1024))
      if st is None or st["comm"] != comm:
        continue  # gone (or the pid reused): the next rescan drops it
      ss = parse_pid_schedstat(read_text(os.path.join(base, "schedstat"), 128))
      exe, delay, pcount = ss if ss is not None and self.caps.get("pid_schedstat") else (None, None, None)
      cur = (now, st["state"], exe, delay, pcount, st["ticks"])
      prev = self._prev.get(pid)
      self._prev[pid] = cur
      stuck_s = 0.
      if prev is not None:
        if delay is not None and prev[3] is not None:
          d = max(delay - prev[3], 0) / 1e9
          for w in (win, drive):
            if w is not None:
              w.kwait[cpu] = w.kwait.get(cpu, 0.) + d
              w.per_thread[(pid, cpu)] = w.per_thread.get((pid, cpu), 0.) + d
        # runnable at both samples and no run in between: waiting for the CPU right now
        ran = (exe != prev[2]) if exe is not None and prev[2] is not None else (st["ticks"] != prev[5])
        if st["state"] == "R" and prev[1] == "R" and not ran:
          since = self._stuck_since.setdefault(pid, prev[0])
          stuck_s = now - since
          for w in (win, drive):
            if w is not None:
              w.stuck_max[cpu] = max(w.stuck_max.get(cpu, 0.), stuck_s)
          if stuck_s >= STUCK_LOG_S and pid not in self._stuck_logged:
            self._stuck_logged.add(pid)
            self._kline(now, comm, pid, cpu, st["cpu"], stuck_s)
        elif pid in self._stuck_since:
          self._stuck_since.pop(pid)
          self._stuck_logged.discard(pid)
      if cpu in VIEW_CPUS:
        view.append({"pid": pid, "comm": comm, "cpu_bound": cpu, "cpu": st["cpu"], "state": st["state"],
                     "stuck_s": round(stuck_s, 2), "run_delay_s": None if delay is None else round(delay / 1e9, 3)})
    with self._lock:
      self._view = {"t": round(now, 2), "threads": view}

  def _kline(self, now: float, comm: str, pid: int, cpu: int, last_cpu: int, stuck_s: float) -> None:
    if now - self._last_kline < MIN_LOG_INTERVAL_S:
      self._klines_suppressed += 1
      return
    cloudlog.event("kthread runnable, not run", error=True, comm=comm, pid=pid, cpu=cpu, last_cpu=last_cpu,
                   for_s=round(stuck_s, 2), suppressed=self._klines_suppressed)
    self._last_kline = now
    self._klines_suppressed = 0

  # ---------------------------------------------------------------------------------------------- windows
  def _drive_start(self, now: float) -> None:
    self._drive = _Win(now)
    self._win = _Win(now)
    self._windows = self._starved_windows = 0
    self._worst = None
    self._prev_stat = parse_proc_stat(read_text(os.path.join(self.proc, "stat")))
    self._prev_sched = parse_proc_schedstat(read_text(os.path.join(self.proc, "schedstat")))
    self._drive_stat = self._prev_stat
    self._prev_rt = self._rt_sample()
    self._last_rescan = -1e9

  def _drive_end(self, now: float) -> None:
    if self._win is not None and now - self._win.start >= 1.:
      self._close_window(now, partial=True)
    d = self._drive
    if d is not None:
      stat = parse_proc_stat(read_text(os.path.join(self.proc, "stat")))
      self.prev_drive = {
        "drive_s": round(now - d.start, 1), "windows": self._windows, "starved_windows": self._starved_windows,
        "cpu": self._cpu_table(d, now, self._drive_stat, stat, None, None), "worst": self._worst,
        "polls": d.polls, "slow_polls": d.slow_polls,
      }
      cloudlog.event("sched drive summary", error=True, **self.prev_drive)
    self._drive = self._win = None

  def _cpu_table(self, w: _Win, now: float, stat0, stat1, sched0, sched1) -> dict:
    cpus = set(w.kwait) | set(w.stuck_max) | set(stat1 or {}) | set(sched1 or {})
    out = {}
    for c in sorted(cpus):
      busy = None
      if stat0 and stat1 and c in stat0 and c in stat1:
        db, dt = stat1[c][0] - stat0[c][0], stat1[c][1] - stat0[c][1]
        busy = round(100. * db / dt, 1) if dt > 0 else None
      rq = None
      if sched0 and sched1 and c in sched0 and c in sched1:
        rq = round(max(sched1[c][1] - sched0[c][1], 0) / 1e9, 3)
      maxw = max((v for (_, pc), v in w.per_thread.items() if pc == c), default=0.)
      out[str(c)] = {"busy": busy, "kwait_s": round(w.kwait.get(c, 0.), 3), "kwait_max_s": round(maxw, 3),
                     "stuck_max_s": round(w.stuck_max.get(c, 0.), 2), "rq_wait_s": rq}
    return out

  def _rt_sample(self) -> dict[str, tuple[int, int]]:
    out = {}
    for pid, comm in self.rt.items():
      ss = parse_pid_schedstat(read_text(os.path.join(self.proc, str(pid), "schedstat"), 128))
      if ss is not None:
        out[f"{comm}:{pid}"] = (ss[0], ss[1])
    try:
      tids = os.listdir(os.path.join(self.proc, str(self.self_pid), "task"))
    except OSError:
      tids = []
    for t in tids:
      base = os.path.join(self.proc, str(self.self_pid), "task", t)
      st = parse_pid_stat(read_text(os.path.join(base, "stat"), 1024))
      ss = parse_pid_schedstat(read_text(os.path.join(base, "schedstat"), 128))
      if st is not None and ss is not None:
        out[f"self/{st['comm']}:{t}"] = (ss[0], ss[1])
    return out

  def _close_window(self, now: float, partial: bool = False) -> None:
    w = self._win
    if w is None:
      return
    stat = parse_proc_stat(read_text(os.path.join(self.proc, "stat")))
    sched = parse_proc_schedstat(read_text(os.path.join(self.proc, "schedstat")))
    rt = self._rt_sample()
    cpu = self._cpu_table(w, now, self._prev_stat, stat, self._prev_sched, sched)
    rt_d, own_d = {}, {}
    for k, (exe, delay) in rt.items():
      p = self._prev_rt.get(k, (0, 0))  # new since the last window (a drive's processes start with it): since it started
      entry = {"exec_s": round((exe - p[0]) / 1e9, 3), "delay_s": round((delay - p[1]) / 1e9, 3)}
      (own_d if k.startswith("self/") else rt_d)[k.removeprefix("self/")] = entry
    self._prev_stat, self._prev_sched, self._prev_rt = stat, sched, rt
    starved = any(c["kwait_s"] >= WAIT_LOG_S or c["stuck_max_s"] >= STUCK_LOG_S for c in cpu.values())
    self._windows += 1
    if starved:
      self._starved_windows += 1
      top = max(cpu.items(), key=lambda kv: (kv[1]["kwait_s"], kv[1]["stuck_max_s"]))
      if self._worst is None or top[1]["kwait_s"] > self._worst["kwait_s"]:
        self._worst = {"cpu": int(top[0]), "kwait_s": top[1]["kwait_s"], "stuck_max_s": top[1]["stuck_max_s"],
                       "at_s": round(now - (self._drive.start if self._drive else w.start), 1)}
    line = {"window_s": round(now - w.start, 1), "cpu": cpu, "rt": rt_d, "hardwared": own_d, "polls": w.polls,
            "slow_polls": w.slow_polls, "window": self._windows, "partial": partial,
            "drive_s": round(now - self._drive.start, 1) if self._drive else None}
    first = self._windows == 1
    if first:
      line["prev_drive"] = self.prev_drive
    if not self._caps_logged:
      line["caps"] = self.caps
    if starved:
      cloudlog.event("sched starvation", error=True, **line)
    elif first or self._windows % BASELINE_EVERY == 0 or not self._caps_logged:
      cloudlog.event("sched baseline", error=True, **line)
    else:
      line = None
    if line is not None:
      self._caps_logged = True
    self._win = _Win(now)
