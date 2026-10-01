"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(UPDATER): what updated is doing while it downloads, for the comma 4's software page.

updated writes UpdaterDownloadProgress (CLEAR_ON_MANAGER_START, JSON) as {"phase": ..., "pct": ...}
and the software page shows it under "downloading...". It reports what it can measure and no more:

* "code": `git fetch` of the branch. pct is git's own "Receiving objects" meter - objects, not
  bytes. On this fork it usually finishes in seconds. A fetch under 100 objects (fetch.unpackLimit)
  goes through unpack-objects, whose meter git draws only after 2 s, so a small update shows no pct.
* "checkout": checkout, clean and `git submodule update`. git gives no usable meter here, so no pct.
* "os": the AGNOS images, only when the new branch needs a different AGNOS. pct is the share of
  the image bytes done, each partition weighted by its size (system is 99% of them) and measured
  as compressed bytes received over the server's Content-Length. MVL's version weighted the seven
  partitions the same, so the six small ones took the OS share to 6/7 in seconds (~91% on its
  70-95% scale) and the system image crawled the rest.

There is no ETA: nothing here measures time, and a percentage of objects or bytes is not one.
Writes are throttled to one per WRITE_INTERVAL within a phase; a phase change and a phase's 100% are
written at once, so the label never freezes short of a meter that finished. Writes block: Params'
non-blocking put is queued to a thread and would land after a remove() that follows it at once.
"""
import re
import subprocess
import time
from collections.abc import Callable

from openpilot.common.swaglog import cloudlog

PARAM = "UpdaterDownloadProgress"

PHASE_CODE = "code"
PHASE_CHECKOUT = "checkout"
PHASE_OS = "os"

# what the software page says under "downloading..."
PHASE_LABELS = {
  PHASE_CODE: "code",
  PHASE_CHECKOUT: "checking out",
  PHASE_OS: "os update",
}

WRITE_INTERVAL = 1.0  # s, between writes within one phase

# `git fetch --progress` meters. Only "Receiving objects" (index-pack) and "Unpacking objects"
# (unpack-objects, under 100 objects) measure the transfer: "Counting" and "Compressing" are the
# server's work and "Resolving deltas" is local.
GIT_RECEIVING = re.compile(r"(?:Receiving|Unpacking) objects:\s+(\d{1,3})%")
# A meter line that is not its final ", done." line - left out of the log, which otherwise gets one
# line per percent per meter. Everything else git prints is kept, as run() kept it before.
GIT_METER_UPDATE = re.compile(r"^(remote: )?[A-Za-z ]+:\s+\d{1,3}% \((?!.*done\.\s*$)")


class DownloadProgress:
  def __init__(self, params, clock: Callable[[], float] = time.monotonic):
    self._params = params
    self._clock = clock
    self._last: tuple[str, int | None] | None = None
    self._t = 0.0
    self._failed = False

  def set(self, phase: str, pct: float | None = None) -> None:
    pct_i = None if pct is None else max(0, min(100, int(pct)))
    if self._last == (phase, pct_i):
      return
    now = self._clock()
    if self._last is not None and self._last[0] == phase and now - self._t < WRITE_INTERVAL and pct_i != 100:
      return
    self._last, self._t = (phase, pct_i), now
    self._put({"phase": phase, "pct": pct_i})

  def clear(self) -> None:
    self._last = None
    try:
      self._params.remove(PARAM)
    except Exception:
      self._log_once()

  def _put(self, value: dict) -> None:
    # A progress write must never be what fails an update. block=True: a queued write would land after a
    # clear() straight after it (the "already flashed" AGNOS path) and leave the param set.
    try:
      self._params.put(PARAM, value, block=True)
    except Exception:
      self._log_once()

  def _log_once(self) -> None:
    if not self._failed:
      self._failed = True
      cloudlog.exception("updated: could not write the download progress")


def git_fetch_with_progress(cmd: list[str], cwd: str, on_percent: Callable[[int], None]) -> str:
  """run(cmd, cwd) for a `git fetch --progress`, calling on_percent with git's receiving meter.

  Returns, and raises CalledProcessError with, what run() would have: git's output with stderr
  merged - minus the meter's intermediate updates, so cloudlog gets the same "git fetch success"
  text it always did plus the meters' final "done." lines.
  """
  kept: list[str] = []
  with subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        encoding="utf8", errors="replace") as proc:
    assert proc.stdout is not None
    # text mode reads with universal newlines, so each of git's \r meter redraws arrives as a line
    for line in proc.stdout:
      m = GIT_RECEIVING.search(line)
      if m:
        on_percent(int(m.group(1)))
      if not GIT_METER_UPDATE.match(line):
        kept.append(line)
  output = "".join(kept)
  if proc.returncode != 0:
    raise subprocess.CalledProcessError(proc.returncode, cmd, output=output)
  return output


def download_label(state: str, progress) -> str:
  """The software page's sub-label while updated reports `state`: the state, plus the phase and,
  where one is measured, its percentage. Anything malformed shows the plain state."""
  if state != "downloading..." or not isinstance(progress, dict):
    return state
  name = PHASE_LABELS.get(progress.get("phase"))
  if name is None:
    return state
  pct = progress.get("pct")
  if isinstance(pct, int) and not isinstance(pct, bool) and 0 <= pct <= 100:
    return f"{state}\n{name} {pct}%"
  return f"{state}\n{name}"
