#!/usr/bin/env python3
import os
import time
import shutil
import threading
from pathlib import Path
from openpilot.common.hardware.hw import Paths
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.system.loggerd.config import get_available_bytes, get_available_percent
from openpilot.system.loggerd.uploader import listdir_by_creation
from openpilot.system.loggerd.xattr_cache import getxattr
from openpilot.sunnypilot.system import deleter_policy as policy  # FORK(STORAGE)

# FORK(STORAGE): upstream's floor, kept as the floor at all times (driving included); parked, the deleter makes room
# up to deleter_policy.ROUTINE_PERCENT (docs/fork/STORAGE.md)
MIN_BYTES = 5 * 1024 * 1024 * 1024
MIN_PERCENT = 10

DELETE_LAST = ['boot', 'crash']

PRESERVE_ATTR_NAME = 'user.preserve'
PRESERVE_ATTR_VALUE = b'1'
PRESERVE_COUNT = 5


def has_preserve_xattr(d: str) -> bool:
  return getxattr(os.path.join(Paths.log_root(), d), PRESERVE_ATTR_NAME) == PRESERVE_ATTR_VALUE


def get_preserved_segments(dirs_by_creation: list[str]) -> set[str]:
  # skip deleting most recent N preserved segments (and their prior segment)
  preserved = set()
  for n, d in enumerate(filter(has_preserve_xattr, reversed(dirs_by_creation))):
    if n == PRESERVE_COUNT:
      break
    date_str, _, seg_str = d.rpartition("--")

    # ignore non-segment directories
    if not date_str:
      continue
    try:
      seg_num = int(seg_str)
    except ValueError:
      continue

    # preserve segment and two prior
    for _seg_num in range(max(0, seg_num - 2), seg_num + 1):
      preserved.add(f"{date_str}--{_seg_num}")

  return preserved


def free_space() -> tuple[float, int]:
  # FORK(STORAGE): unknown (statvfs failed) counts as room enough, as upstream's defaults did
  return get_available_percent(default=100.), get_available_bytes(default=MIN_BYTES + 1)


def deleter_step(onroad: bool | None = None) -> tuple[bool, str | None]:
  # FORK(STORAGE): push16's policy. Parked: room up to ROUTINE_PERCENT from segments the uploaders are done with;
  # driving: only below the floor. Returns (room needed, the directory removed).
  if onroad is None:
    onroad = policy.is_onroad(Params())
  free_percent, free_bytes = free_space()
  need = policy.deletion_need(free_percent, free_bytes, onroad, MIN_PERCENT, MIN_BYTES)
  if need is None:
    return False, None

  dirs = listdir_by_creation(Paths.log_root())
  preserved_dirs = get_preserved_segments(dirs)

  # remove the earliest directory we can
  for delete_dir in policy.candidates(Paths.log_root(), dirs, preserved_dirs, DELETE_LAST, need):
    delete_path = os.path.join(Paths.log_root(), delete_dir)

    try:
      cloudlog.info(f"deleting {delete_path}")
      shutil.rmtree(delete_path)
      return True, delete_path
    except OSError:
      cloudlog.exception(f"issue deleting {delete_path}")
  return True, None


def deleter_thread(exit_event: threading.Event):
  # FORK(STORAGE): push16's policy and pacing (deleter_policy.py); upstream's order, locks and external storage kept
  params = Params()
  parked = policy.ParkedSummary(Paths.log_root(), MIN_PERCENT)
  while not exit_event.is_set():
    onroad = policy.is_onroad(params)
    free_percent, free_bytes = free_space()
    parked.update(onroad, free_percent, time.monotonic())
    need = policy.deletion_need(free_percent, free_bytes, onroad, MIN_PERCENT, MIN_BYTES)

    if need is not None:
      pace_s = policy.file_pace_s(need, onroad, free_percent)
      removed = False
      dirs = listdir_by_creation(Paths.log_root())
      preserved_dirs = get_preserved_segments(dirs)

      # remove the earliest directory we can
      for delete_dir in policy.candidates(Paths.log_root(), dirs, preserved_dirs, DELETE_LAST, need):
        delete_path = os.path.join(Paths.log_root(), delete_dir)

        if Path(Paths.log_root_external()).is_mount():
          out_of_bytes_external = get_available_bytes(default=MIN_BYTES + 1, path_type="external") < MIN_BYTES
          out_of_percent_external = get_available_percent(default=MIN_PERCENT + 1, path_type="external") < MIN_PERCENT

          if out_of_percent_external or out_of_bytes_external:
            dirs_external = listdir_by_creation(Paths.log_root_external())

            # remove the earliest external directory we can
            for delete_dir_external in sorted(dirs_external):
              delete_path_external = os.path.join(Paths.log_root_external(), delete_dir_external)
              try:
                cloudlog.warning(f"deleting {delete_path_external}")
                shutil.rmtree(delete_path_external)
                break
              except OSError:
                cloudlog.exception(f"issue deleting {delete_path_external}")

          # move directory from internal to external
          path_external = os.path.join(Paths.log_root_external(), delete_dir)
          try:
            cloudlog.warning(f"moving {delete_path} to {path_external}")
            start = time.monotonic()
            shutil.move(delete_path, path_external)
            cloudlog.warning(f"moved {delete_path} to {path_external} in {time.monotonic() - start:.2f}s")
            removed = True
            break
          except Exception:
            cloudlog.error(f"issue moving {delete_path} to {path_external}")
            try:
              cloudlog.warning(f"deleting {delete_path}")
              shutil.rmtree(delete_path)
              removed = True
              break
            except OSError:
              cloudlog.exception(f"issue deleting {delete_path}")
          continue

        try:
          cloudlog.info(f"deleting {delete_path}")
          start = time.monotonic()
          nbytes, files, complete = policy.remove_dir(delete_path, pace_s, exit_event.wait)
          seconds = time.monotonic() - start
          policy.log_delete(delete_dir, need, onroad, pace_s, nbytes, files, seconds, free_percent, complete)
          parked.deleted(need, onroad, nbytes, seconds)
          removed = True
          break
        except OSError:
          cloudlog.exception(f"issue deleting {delete_path}")
      parked.waiting_for_upload = need == policy.ROUTINE and not removed
      exit_event.wait(policy.next_wait_s(need, pace_s, removed))
    else:
      parked.waiting_for_upload = False
      exit_event.wait(policy.IDLE_S)


def main():
  deleter_thread(threading.Event())


if __name__ == "__main__":
  main()
