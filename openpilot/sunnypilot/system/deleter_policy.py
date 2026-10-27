"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(STORAGE): when the deleter makes room on /data, and how (push16, docs/fork/STORAGE.md).

Upstream's deleter keeps /data at 10 % free (or 5 GiB) at all times. Once a device has filled up it removes the oldest
one-minute segment (about 120 MB on this comma 4) whenever it is below that, so about once a minute while driving.
This device has sat at that floor since July. Its /data stalls (routes 14d, 152, 157, 158, 15d) were not caused by
those deletes: they came with and without them, at 10.9-12.8 % free too, and every one came after half a minute of
CPU core 4 near full load. But a delete frees a whole segment's blocks at once, and /data is mounted with online
`discard`, so the next journal commits send all of them to the UFS; on route 157 four of six stalls began 6-13 s
after a delete. So the routine part of the work moves to when the car is parked:

* Parked (IsOffroad): make room up to ROUTINE_PERCENT (20 %), and only from ordinary segments the uploaders are done
  with (the qlog and qcamera carry an uploader's xattr) and athenad has not been asked to send, so what goes is already
  in connect. Never a preserved (bookmarked) segment or the boot / crash folders: upstream takes those only below its
  floor, after every ordinary segment, and so does this. Only once the car has been parked PARKED_GRACE_S, and a
  segment every ROUTINE_SEGMENT_PACE_S: no burst of discards just before a drive starts (a boot, a short stop). A drive
  then starts with about 9 GB (about 75 minutes of recording) above the floor.
* Driving: nothing, unless free space is below upstream's floor (deleter.MIN_PERCENT / MIN_BYTES), which stays the
  floor at all times. Then the oldest segment goes, uploaded or not, as upstream, but one file at a time, the largest
  first, ONROAD_FILE_PACE_S apart: longer than ext4's 5 s commit interval, so each file's discards go out with a
  commit of their own. Below EMERGENCY_PERCENT (selfdrived's "Out of Storage" alert is below 7 %) there is no pacing.
* Upstream's order is kept: the oldest first, never a segment with a .lock file, the preserved (bookmarked) segments
  and the boot / crash folders last (below the floor; a routine delete never takes them).
* Every delete is logged at error level, so a route's qlog keeps it: "deleter deleted", with the segment, why, the
  size, the files and the time it took. What happened while parked goes into the next drive's qlog, ParkedSummary's
  "deleter parked summary" (nothing logged offroad reaches a route): the deletes, /data's writes over the parked
  time and the processes that made them.

No Params writes: the deleter reads IsOffroad (as the uploader does) and AthenadUploadQueue, and nothing else.
"""
import math
import os
import shutil
import time
from collections import Counter
from collections.abc import Callable, Iterator

from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.system.storage_diag import WriteSampler, diskstats_delta, disk_devices, read_diskstats
from openpilot.system.loggerd import xattr_cache

ROUTINE = "routine"    # parked, below ROUTINE_PERCENT
CRITICAL = "critical"  # below upstream's floor, parked or driving

ROUTINE_PERCENT = 20.         # parked: room up to here. About 9 GB more than upstream's floor on this device's 90 GB.
EMERGENCY_PERCENT = 8.        # below this a critical delete is not paced (selfdrived's outOfSpace is below 7 %)
ONROAD_FILE_PACE_S = 6.       # driving, critical: one file at a time; > ext4's commit=5 on /data
ROUTINE_SEGMENT_PACE_S = ONROAD_FILE_PACE_S  # parked: between segments, so a commit carries one segment's discards
PARKED_GRACE_S = 120.         # parked this long (as the deleter saw it, from its own start) before a routine delete
CRITICAL_RECHECK_S = 0.1      # upstream's re-check after a delete
NOTHING_DELETABLE_S = 5.      # critical, but every segment is locked
IDLE_S = 30.                  # upstream's sleep when there is room (also: parked, nothing uploaded yet)

# What the uploaders send by themselves, and the xattr each sets once it has (or was told to skip it).
# system/loggerd/uploader.py (comma connect) and sunnypilot/sunnylink/uploader.py; test_deleter_policy checks both.
AUTO_UPLOADED = ("qlog", "qlog.zst", "qcamera.ts")
UPLOAD_ATTRS = (("user.upload", b"1"), ("user.sunny.upload", b"1"))

SUMMARY_DELAY_S = 30.     # the parked summary goes out this long into the drive, once loggerd is recording
WRITERS_SAMPLE_S = 300.   # parked: /proc/<pid>/io every 5 minutes


def is_onroad(params) -> bool:
  """Not IsOffroad, which manager writes on every change of `started` (the uploader reads it too). Unknown counts as
  driving: the routine work waits, the floor does not."""
  try:
    return not params.get_bool("IsOffroad")
  except Exception:
    cloudlog.exception("deleter: IsOffroad unreadable")
    return True


def deletion_need(free_percent: float, free_bytes: int, onroad: bool, floor_percent: float, floor_bytes: int,
                  parked_s: float = math.inf) -> str | None:
  """CRITICAL below upstream's floor (at any time), ROUTINE when parked for PARKED_GRACE_S below ROUTINE_PERCENT,
  else None."""
  if free_percent < floor_percent or free_bytes < floor_bytes:
    return CRITICAL
  if not onroad and free_percent < ROUTINE_PERCENT and parked_s >= PARKED_GRACE_S:
    return ROUTINE
  return None


class ParkedTimer:
  """How long the car has been parked, as the deleter has seen it: from the deleter's own start, because manager writes
  IsOffroad=True when it starts and clears it only once the drive has `started`, so a boot straight into a drive looks
  parked for its first seconds. A drive restarts it."""

  def __init__(self) -> None:
    self.since: float | None = None

  def parked_s(self, onroad: bool, now: float) -> float:
    if onroad:
      self.since = None
      return 0.
    if self.since is None:
      self.since = now
    return now - self.since


def file_pace_s(need: str, onroad: bool, free_percent: float) -> float:
  """The wait between the files of one segment: only a critical delete while driving is paced, and not in an emergency."""
  if need == CRITICAL and onroad and free_percent >= EMERGENCY_PERCENT:
    return ONROAD_FILE_PACE_S
  return 0.


def next_wait_s(need: str, pace_s: float, removed: bool) -> float:
  """The deleter's wait before it looks again."""
  if not removed:
    return NOTHING_DELETABLE_S if need == CRITICAL else IDLE_S
  if need == ROUTINE:
    return ROUTINE_SEGMENT_PACE_S
  return max(pace_s, CRITICAL_RECHECK_S)


def uploaded(fn: str) -> bool:
  """An uploader has sent fn (or skipped it on purpose). Read past xattr_cache's cache: the uploader sets the attribute
  in its own process, so a cached "not yet" would never change here."""
  for attr, value in UPLOAD_ATTRS:
    try:
      if xattr_cache._getxattr(fn, attr) == value:
        return True
    except OSError:
      pass  # not set (ENODATA / ENOATTR), or unreadable: not known to be uploaded
  return False


def pending_upload(path: str, names: list[str] | None = None) -> bool:
  """The segment holds a file the uploaders send by themselves that neither has sent yet."""
  if names is None:
    try:
      names = os.listdir(path)
    except OSError:
      return False
  return any(n in AUTO_UPLOADED and not uploaded(os.path.join(path, n)) for n in names)


def athena_queued(params, root: str) -> set[str]:
  """The directories under root holding a file queued in athenad's AthenadUploadQueue: what someone asked connect for
  (rlogs, full videos), which no xattr marks yet. Missing or not valid JSON is an empty queue, as athenad itself starts
  with one then (UploadQueueCache.initialize); athenad rewrites the param whenever its queue changes."""
  try:
    items = params.get("AthenadUploadQueue")
  except Exception:
    cloudlog.exception("deleter: AthenadUploadQueue unreadable")
    return set()
  if not isinstance(items, list):
    return set()
  root = os.path.normpath(root)
  queued = set()
  for item in items:
    path = item.get("path") if isinstance(item, dict) else None
    if not isinstance(path, str) or not os.path.isabs(path):
      continue
    rel = os.path.relpath(os.path.normpath(path), root)
    if rel != os.curdir and not rel.startswith(os.pardir):
      queued.add(rel.split(os.sep, 1)[0])
  return queued


def candidates(root: str, dirs: list[str], preserved: set[str], delete_last: list[str], need: str, params,
               held: Counter | None = None) -> Iterator[str]:
  """The directories to try, in upstream's order (the oldest first; the preserved segments and boot / crash last),
  never one with a .lock file. A routine delete takes only ordinary segments that no upload is waiting for (the
  uploaders' qlog and qcamera, athenad's queue): the preserved segments and boot / crash are left to the floor, as
  upstream leaves them. held["upload"] counts the segments passed over for an upload."""
  held = Counter() if held is None else held
  queued = athena_queued(params, root) if need == ROUTINE else set()
  for d in sorted(dirs, key=lambda d: (d in delete_last, d in preserved)):
    if need == ROUTINE and (d in delete_last or d in preserved):
      continue
    path = os.path.join(root, d)
    try:
      names = os.listdir(path)
    except OSError:
      continue  # gone since it was listed
    if any(name.endswith(".lock") for name in names):
      continue
    if need == ROUTINE and (d in queued or pending_upload(path, names)):
      held["upload"] += 1
      continue
    yield d


def _size(path: str, is_dir: bool) -> int:
  if not is_dir:
    try:
      return os.lstat(path).st_size
    except OSError:
      return 0
  total = 0
  for base, _, files in os.walk(path):
    for f in files:
      try:
        total += os.lstat(os.path.join(base, f)).st_size
      except OSError:
        pass
  return total


def remove_dir(path: str, pace_s: float, wait: Callable[[float], bool]) -> tuple[int, int, bool]:
  """Removes path one entry at a time, the largest first, with wait(pace_s) between entries when pace_s > 0 (wait
  returns True to stop: the deleter's exit event). Returns (bytes, entries removed, whether path is gone). Raises
  OSError as shutil.rmtree would."""
  with os.scandir(path) as it:
    entries = [(e.name, e.is_dir(follow_symlinks=False)) for e in it]
  sized = sorted(((_size(os.path.join(path, n), d), n, d) for n, d in entries), key=lambda e: (-e[0], e[1]))
  removed_bytes = removed = 0
  for i, (size, name, is_dir) in enumerate(sized):
    if i and pace_s > 0 and wait(pace_s):
      return removed_bytes, removed, False
    p = os.path.join(path, name)
    try:
      if is_dir:
        shutil.rmtree(p)
      else:
        os.unlink(p)
    except FileNotFoundError:
      continue
    removed_bytes += size
    removed += 1
  try:
    os.rmdir(path)
  except OSError:
    shutil.rmtree(path)  # something appeared since the listing (or rmdir failed otherwise): upstream's way
  return removed_bytes, removed, True


def log_delete(segment: str, need: str, onroad: bool, pace_s: float, nbytes: int, files: int, seconds: float,
               free_percent: float, complete: bool) -> None:
  cloudlog.event("deleter deleted", error=True, segment=segment, why=need, onroad=onroad, pace_s=pace_s,
                 mb=round(nbytes / 2**20, 1), files=files, seconds=round(seconds, 2), free_pct=round(free_percent, 2),
                 complete=complete)


def _ext4_written_kb(part: str | None) -> int | None:
  if not part:
    return None
  try:
    with open(f"/sys/fs/ext4/{part}/lifetime_write_kbytes") as f:
      return int(f.read().strip())
  except (OSError, ValueError):
    return None


class ParkedSummary:
  """What happened on /data while the car was parked, logged once the next drive has been recording for
  SUMMARY_DELAY_S: lines logged offroad go to the device's swaglog only, never into a route. update() at the start of
  every pass of the deleter's loop (at least every IDLE_S), deleted() after each delete, pass_done() at its end."""

  def __init__(self, path: str, floor_percent: float, summary_delay: float = SUMMARY_DELAY_S, sample_s: float = WRITERS_SAMPLE_S):
    self.path = path
    self.floor_percent = floor_percent
    self.summary_delay = summary_delay
    self.sample_s = sample_s
    self.devices = disk_devices(path)
    self.part = self.devices[0] if self.devices else None
    self.window: dict | None = None
    self.sampler: WriteSampler | None = None
    self.onroad_since: float | None = None

  def update(self, onroad: bool, free_percent: float, now: float) -> None:
    try:
      self._update(onroad, free_percent, now)
    except Exception:
      cloudlog.exception("deleter parked summary failed")
      self.window = self.sampler = None

  def _update(self, onroad: bool, free_percent: float, now: float) -> None:
    if not onroad:
      self.onroad_since = None
      if self.window is None:
        self._open(free_percent, now)
      elif self.sampler is not None and now - self.window["sampled"] >= self.sample_s:
        self.sampler.sample()
        self.window["sampled"] = now
      return

    if self.onroad_since is None:
      self.onroad_since = now
      if self.window is not None:
        self.window["parked_s"] = now - self.window["start"]
        if self.sampler is not None:
          self.sampler.sample()  # before manager has stopped the offroad-only processes for long
    if self.window is not None and now - self.onroad_since >= self.summary_delay:
      self._log(free_percent)
      self.window = self.sampler = None

  def _open(self, free_percent: float, now: float) -> None:
    self.window = {"start": now, "sampled": now, "free_pct": free_percent, "parked_s": None,
                   "deleted": {ROUTINE: 0, CRITICAL: 0}, "bytes": 0, "seconds": 0., "waiting_for_upload": False,
                   "disk": read_diskstats(self.devices), "ext4_kb": _ext4_written_kb(self.part)}
    self.sampler = WriteSampler()

  def deleted(self, need: str, onroad: bool, nbytes: int, seconds: float) -> None:
    if self.window is None or onroad:
      return  # a delete while driving is in the route already ("deleter deleted")
    self.window["deleted"][need] += 1
    self.window["bytes"] += nbytes
    self.window["seconds"] += seconds

  def pass_done(self, onroad: bool, waiting_for_upload: bool) -> None:
    """Whether the pass's routine work stopped short at segments an upload is waiting for. Parked passes only: the
    summary is logged 30 s into the drive, and the drive's passes would always say no."""
    if self.window is not None and not onroad:
      self.window["waiting_for_upload"] = waiting_for_upload

  def _log(self, free_percent: float) -> None:
    w = self.window
    assert w is not None
    disk = diskstats_delta(w["disk"], read_diskstats(self.devices)).get(self.part or "", {})
    ext4_kb = _ext4_written_kb(self.part)
    ext4_mb = round((ext4_kb - w["ext4_kb"]) / 1024, 1) if ext4_kb is not None and w["ext4_kb"] is not None else None
    cloudlog.event("deleter parked summary", error=True,
                   parked_s=round(w["parked_s"], 1) if w["parked_s"] is not None else None,
                   free_pct_parked=round(w["free_pct"], 2), free_pct_now=round(free_percent, 2),
                   deleted=w["deleted"], deleted_mb=round(w["bytes"] / 2**20, 1), deleting_s=round(w["seconds"], 1),
                   waiting_for_upload=w["waiting_for_upload"],
                   disk_written_mb=round(disk["sectors_written"] * 512 / 2**20, 1) if disk else None,
                   disk_writes=disk.get("writes"), disk_write_ms=disk.get("write_ms"),
                   ext4_written_mb=ext4_mb,
                   writers=self.sampler.top() if self.sampler else [],
                   writers_mb=round(self.sampler.total_bytes() / 2**20, 1) if self.sampler else None,
                   procs_unreadable=self.sampler.unreadable if self.sampler else None,
                   policy={"routine_pct": ROUTINE_PERCENT, "floor_pct": self.floor_percent, "emergency_pct": EMERGENCY_PERCENT,
                           "pace_s": ONROAD_FILE_PACE_S, "routine_pace_s": ROUTINE_SEGMENT_PACE_S, "grace_s": PARKED_GRACE_S},
                   uptime_s=round(time.monotonic(), 1))
