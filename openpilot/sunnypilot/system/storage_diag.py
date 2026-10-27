"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(COMM-ISSUE): what the device's storage was doing, in the routes (the owner has no SSH).

Routes 14d and 152 stalled on /data (sda12, the ext4 journal thread in D state, iowait to 51 % on a core) for 4-8 s,
and nothing in the logs could say why: no mount options, no writeback settings, no TRIM state, no disk counters. Two
things, both at error level so they reach errorLogMessage and with it the qlogs:

* StorageReporter: one "storage report" when hardwared starts and one per onroad start (ONROAD_DELAY_S in, once
  loggerd is recording): the mount and ext4's effective options, vm.dirty_*, the fstrim timer, the block queue
  (scheduler, discard support), free space and inodes, and the journal's own statistics. Collected on a thread of its
  own; nothing here touches the loop that calls update().
* LoopTimer: the time of each section of a loop. When one takes longer than SLOW_SECTION_S it logs which, with the
  disk's /proc/diskstats delta over the loop. At most one line per MIN_LOG_INTERVAL_S; the lines in between are
  counted and the count goes out with the next.

procfs and sysfs only, plus one statvfs and two stats on the probed filesystem in the report thread.

FORK(STORAGE), push16: the report also carries the UFS's own wear estimate where the kernel exposes it (`ufs`), and
the CPU's state (`cpu`: each cpufreq policy's cap and current speed, the real-time throttle), because the alerts of
routes 153 and 15d came with core 4 at full load and /data idle. WriteSampler is the deleter's: which processes wrote
to storage while the car was parked (deleter_policy.ParkedSummary).
"""
import glob
import os
import subprocess
import threading
import time

from openpilot.common.swaglog import cloudlog

SLOW_SECTION_S = 0.3
MIN_LOG_INTERVAL_S = 10.
ONROAD_DELAY_S = 30.
SKIP_FIRST_LOOPS = 2  # the first loops run the cached git calls and the first reads; not what this is for

# /proc/diskstats after major, minor, name (kernel 4.9 has these 11; later kernels append discard and flush fields)
DISKSTATS_FIELDS = ("reads", "reads_merged", "sectors_read", "read_ms", "writes", "writes_merged", "sectors_written",
                    "write_ms", "in_flight", "io_ms", "weighted_ms")
QUEUE_FILES = ("scheduler", "nr_requests", "rotational", "discard_max_bytes", "discard_granularity", "write_cache",
               "wbt_lat_usec")
EXT4_SYSFS_FILES = ("lifetime_write_kbytes", "session_write_kbytes", "delayed_allocation_blocks", "errors_count",
                    "first_error_time", "last_error_time")
# FORK(STORAGE): the UFS health descriptor (JESD220: life time A / B in 10 % steps, 0x0B = past its life; pre-EOL
# 0x01 normal, 0x02 warning, 0x03 urgent) and who made it
UFS_HEALTH_FILES = ("eol_info", "life_time_estimation_a", "life_time_estimation_b")
UFS_STRING_FILES = ("manufacturer_name", "product_name", "product_revision")
UFS_ENTRIES_MAX = 60
CPUFREQ_FILES = ("related_cpus", "scaling_governor", "cpuinfo_max_freq", "scaling_max_freq", "scaling_cur_freq")
WRITERS_TOP = 5
WRITERS_TRACKED_MAX = 4000


def _read(path: str, limit: int = 4096) -> str | None:
  try:
    with open(path) as f:
      return f.read(limit).strip()
  except (OSError, UnicodeDecodeError):
    return None


def mount_for(path: str, mounts_file: str = "/proc/mounts") -> dict | None:
  """The /proc/mounts entry that holds path: the longest mount point that is a prefix of its real path."""
  real = os.path.realpath(path)
  best = None
  try:
    with open(mounts_file) as f:
      for line in f:
        parts = line.split()
        if len(parts) < 4:
          continue
        device, mountpoint, fstype, options = parts[:4]
        mountpoint = mountpoint.replace("\\040", " ")
        if real == mountpoint or real.startswith(mountpoint.rstrip("/") + "/") or mountpoint == "/":
          if best is None or len(mountpoint) >= len(best["mountpoint"]):
            best = {"device": device, "mountpoint": mountpoint, "fstype": fstype, "options": options}
  except OSError:
    return None
  return best


def block_names(device: str) -> tuple[str | None, str | None]:
  """('/dev/sda12') -> ('sda12', 'sda'); a whole disk is its own disk; anything not a block device -> (None, None)."""
  name = os.path.basename(os.path.realpath(device)) if device.startswith("/dev/") else None
  if not name or not os.path.exists(f"/sys/class/block/{name}"):
    return None, None
  parent = os.path.basename(os.path.dirname(os.path.realpath(f"/sys/class/block/{name}")))
  disk = parent if os.path.exists(f"/sys/class/block/{name}/partition") else name
  return name, disk


def disk_devices(path: str) -> list[str]:
  """The partition and the disk that hold path (['sda12', 'sda']), for read_diskstats; [] off a block device."""
  m = mount_for(path)
  part, disk = block_names(m["device"]) if m else (None, None)
  return [n for n in dict.fromkeys((part, disk)) if n]


def read_diskstats(names: list[str], diskstats_file: str = "/proc/diskstats") -> dict[str, dict[str, int]]:
  out: dict[str, dict[str, int]] = {}
  if not names:
    return out
  try:
    with open(diskstats_file) as f:
      for line in f:
        parts = line.split()
        if len(parts) >= 3 + len(DISKSTATS_FIELDS) and parts[2] in names:
          out[parts[2]] = {k: int(v) for k, v in zip(DISKSTATS_FIELDS, parts[3:3 + len(DISKSTATS_FIELDS)], strict=True)}
  except (OSError, ValueError):
    pass
  return out


def diskstats_delta(before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
  """Counter deltas per device; in_flight is the value at the end, not a delta."""
  out = {}
  for name, b in after.items():
    a = before.get(name)
    if a is None:
      continue
    out[name] = {k: (b[k] if k == "in_flight" else b[k] - a[k]) for k in DISKSTATS_FIELDS}
  return out


def _fstrim_state() -> dict:
  state: dict = {
    "unit": next((p for p in ("/lib/systemd/system/fstrim.timer", "/usr/lib/systemd/system/fstrim.timer",
                              "/etc/systemd/system/fstrim.timer") if os.path.exists(p)), None),
    "wanted": os.path.exists("/etc/systemd/system/timers.target.wants/fstrim.timer"),
  }
  try:
    state["last_run"] = int(os.stat("/var/lib/systemd/timers/stamp-fstrim.timer").st_mtime)
  except OSError:
    state["last_run"] = None
  for verb in ("is-enabled", "is-active"):
    try:
      r = subprocess.run(["systemctl", verb, "fstrim.timer"], capture_output=True, text=True, timeout=2, check=False)
      state[verb] = r.stdout.strip() or r.stderr.strip()[:80]
    except (OSError, subprocess.SubprocessError):
      state[verb] = None
  return state


def ufs_health(disk: str | None, sys_block: str = "/sys/class/block") -> dict | None:
  """FORK(STORAGE): the UFS host above disk in sysfs and, where this kernel has them, its health and string
  descriptors. Without a health descriptor the host's entries are listed instead, to see what this kernel offers."""
  if not disk:
    return None
  d = os.path.realpath(os.path.join(sys_block, disk))
  host = None
  while d and d != os.path.dirname(d):
    if os.path.isdir(os.path.join(d, "health_descriptor")) or "ufshc" in os.path.basename(d):
      host = d
      break
    d = os.path.dirname(d)
  if host is None:
    return {"host": None}
  out: dict = {"host": host}
  for group, files in (("health_descriptor", UFS_HEALTH_FILES), ("string_descriptors", UFS_STRING_FILES)):
    g = os.path.join(host, group)
    out[group] = {k: _read(os.path.join(g, k)) for k in files} if os.path.isdir(g) else None
  if out["health_descriptor"] is None:
    try:
      out["entries"] = sorted(os.listdir(host))[:UFS_ENTRIES_MAX]
    except OSError:
      out["entries"] = None
  return out


def cpu_state(cpufreq: str = "/sys/devices/system/cpu/cpufreq", kernel: str = "/proc/sys/kernel",
              sched_features: str = "/sys/kernel/debug/sched_features") -> dict:
  """FORK(STORAGE): each cpufreq policy's cap and current speed, and the real-time throttle (RT_RUNTIME_SHARE is in
  debugfs, which is usually root's only: None then)."""
  features = _read(sched_features)
  share = None
  if features is not None:
    for f in features.split():
      if f in ("RT_RUNTIME_SHARE", "NO_RT_RUNTIME_SHARE"):
        share = f == "RT_RUNTIME_SHARE"
  return {"cpufreq": {os.path.basename(p): {k: _read(os.path.join(p, k)) for k in CPUFREQ_FILES}
                      for p in sorted(glob.glob(os.path.join(cpufreq, "policy*")))},
          "sched_rt_runtime_us": _read(os.path.join(kernel, "sched_rt_runtime_us")),
          "sched_rt_period_us": _read(os.path.join(kernel, "sched_rt_period_us")),
          "rt_runtime_share": share}


def storage_report(path: str) -> dict:
  """Everything above for the filesystem that holds path."""
  m = mount_for(path)
  part, disk = block_names(m["device"]) if m else (None, None)
  report: dict = {"path": path, "mount": m, "part": part, "disk": disk}
  try:
    st = os.statvfs(path)
    report["space"] = {"total_mb": st.f_blocks * st.f_frsize // 2**20, "avail_mb": st.f_bavail * st.f_frsize // 2**20,
                       "avail_pct": round(100. * st.f_bavail / st.f_blocks, 2) if st.f_blocks else None,
                       "inodes_free_pct": round(100. * st.f_favail / st.f_files, 2) if st.f_files else None}
  except OSError:
    report["space"] = None
  report["vm"] = {os.path.basename(p): _read(p) for p in sorted(glob.glob("/proc/sys/vm/dirty_*"))}
  report["vm"]["swappiness"] = _read("/proc/sys/vm/swappiness")
  report["fstrim"] = _fstrim_state()
  if disk:
    report["queue"] = {k: _read(f"/sys/block/{disk}/queue/{k}") for k in QUEUE_FILES}
  if part:
    report["ext4_options"] = (_read(f"/proc/fs/ext4/{part}/options") or "").replace("\n", ",") or None
    report["ext4"] = {k: _read(f"/sys/fs/ext4/{part}/{k}") for k in EXT4_SYSFS_FILES}
    jbd2 = sorted(glob.glob(f"/proc/fs/jbd2/{part}-*/info"))
    report["jbd2"] = " | ".join((_read(jbd2[0]) or "").split("\n")) if jbd2 else None
  report["diskstats"] = read_diskstats([n for n in (part, disk) if n])
  report["ufs"] = ufs_health(disk)  # FORK(STORAGE)
  report["cpu"] = cpu_state()  # FORK(STORAGE)
  report["uptime_s"] = round(time.monotonic(), 1)
  return report


class StorageReporter:
  """storage_report(path) once when update() is first called, and once per onroad start, ONROAD_DELAY_S in."""

  def __init__(self, path: str, onroad_delay: float = ONROAD_DELAY_S):
    self.path = path
    self.onroad_delay = onroad_delay
    self._started_logged: float | None = None
    self._first = True

  def update(self, started_ts: float | None, now: float) -> None:
    if self._first:
      self._first = False
      self._spawn("start")
    if started_ts is not None and started_ts != self._started_logged and now - started_ts >= self.onroad_delay:
      self._started_logged = started_ts
      self._spawn("onroad")

  def _spawn(self, reason: str) -> None:
    threading.Thread(target=self._log, args=(reason,), name="storage_report", daemon=True).start()

  def _log(self, reason: str) -> None:
    try:
      cloudlog.event("storage report", error=True, reason=reason, **storage_report(self.path))
    except Exception:
      cloudlog.exception("storage report failed")


class LoopTimer:
  """start() at the top of each iteration, mark(name) after each section, finish() at the bottom. sample_disk() takes
  the /proc/diskstats sample a slow loop's delta is measured from (once per published message is enough)."""

  def __init__(self, name: str, path: str, threshold: float = SLOW_SECTION_S, min_interval: float = MIN_LOG_INTERVAL_S):
    self.name = name
    self.threshold = threshold
    self.min_interval = min_interval
    self.devices = disk_devices(path)
    self.loops = 0
    self.slow_loops = 0
    self.suppressed = 0
    self.last_log = -1e9
    self.sections: list[tuple[str, float]] = []
    self.t0 = self.t = time.monotonic()
    self.disk_t = self.t0
    self.disk = read_diskstats(self.devices)

  def start(self) -> None:
    self.t0 = self.t = time.monotonic()
    self.sections = []

  def sample_disk(self) -> None:
    self.disk_t = time.monotonic()
    self.disk = read_diskstats(self.devices)

  def mark(self, section: str) -> None:
    now = time.monotonic()
    self.sections.append((section, now - self.t))
    self.t = now

  def finish(self, **extra) -> bool:
    """True when this loop had a slow section (whether or not it was logged)."""
    self.loops += 1
    if self.loops <= SKIP_FIRST_LOOPS or not self.sections:
      return False
    slowest, slowest_s = max(self.sections, key=lambda s: s[1])
    if slowest_s <= self.threshold:
      return False
    self.slow_loops += 1
    now = time.monotonic()
    if now - self.last_log < self.min_interval:
      self.suppressed += 1
      return True
    try:
      cloudlog.event(f"{self.name} slow loop section", error=True, slowest=slowest, slowest_s=round(slowest_s, 3),
                     total_s=round(now - self.t0, 3), sections={s: round(d, 3) for s, d in self.sections if d >= 0.02},
                     disk=diskstats_delta(self.disk, read_diskstats(self.devices)), disk_window_s=round(now - self.disk_t, 3),
                     slow_loops=self.slow_loops, suppressed=self.suppressed, **extra)
    except Exception:
      cloudlog.exception("loop timer log failed")
    self.last_log = now
    self.suppressed = 0
    return True


def proc_write_bytes(proc: str = "/proc") -> tuple[dict[tuple[int, int], tuple[str, int]], int]:
  """FORK(STORAGE): {(pid, start time in clock ticks): (comm, write_bytes)} for every process whose /proc/<pid>/io this
  user may read (write_bytes: what it caused to be sent to storage), and how many it may not."""
  out: dict[tuple[int, int], tuple[str, int]] = {}
  unreadable = 0
  try:
    pids = [p for p in os.listdir(proc) if p.isdigit()]
  except OSError:
    return out, unreadable
  for pid in pids:
    try:
      with open(os.path.join(proc, pid, "stat")) as f:
        stat = f.read()
      close = stat.rindex(")")
      comm = stat[stat.index("(") + 1:close]
      start = int(stat[close + 2:].split()[19])  # field 22, starttime
    except (OSError, ValueError, IndexError):
      continue  # gone
    try:
      with open(os.path.join(proc, pid, "io")) as f:
        written = next(int(line.split()[1]) for line in f if line.startswith("write_bytes:"))
    except (OSError, ValueError, IndexError, StopIteration):
      unreadable += 1
      continue
    out[(int(pid), start)] = (comm, written)
  return out, unreadable


class WriteSampler:
  """FORK(STORAGE): what each process sent to storage over a window, from samples of /proc/<pid>/io: a process that
  was there at the start counts from its first sample, one started since counts from zero. A process that came and
  went between two samples is missed; ParkedSummary also logs the disk's own counters over the window."""

  def __init__(self, proc: str = "/proc", uptime_file: str = "/proc/uptime"):
    self.proc = proc
    try:
      hz = os.sysconf("SC_CLK_TCK")
      with open(uptime_file) as f:
        self.start_ticks = float(f.read().split()[0]) * hz
    except (OSError, ValueError, IndexError, AttributeError):
      self.start_ticks = float("inf")  # unknown: count everyone from their first sample
    self.seen: dict[tuple[int, int], list] = {}  # (pid, start) -> [comm, base, last]
    self.unreadable = 0
    self.samples = 0
    self.sample()

  def sample(self) -> None:
    procs, self.unreadable = proc_write_bytes(self.proc)
    for key, (comm, written) in procs.items():
      s = self.seen.get(key)
      if s is None:
        self.seen[key] = [comm, 0 if key[1] >= self.start_ticks else written, written]
      else:
        s[2] = written
    self.samples += 1
    if len(self.seen) > WRITERS_TRACKED_MAX:
      keep = sorted(self.seen.items(), key=lambda kv: kv[1][2] - kv[1][1], reverse=True)[:WRITERS_TRACKED_MAX // 2]
      self.seen = dict(keep)

  def top(self, n: int = WRITERS_TOP) -> list[dict]:
    rows = sorted(((s[2] - s[1], s[0], key[0]) for key, s in self.seen.items()), reverse=True)
    return [{"comm": comm, "pid": pid, "mb": round(d / 2**20, 1)} for d, comm, pid in rows[:n] if d > 0]

  def total_bytes(self) -> int:
    return sum(max(0, s[2] - s[1]) for s in self.seen.values())
