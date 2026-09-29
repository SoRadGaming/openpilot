"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the device side of the NSW speed-zone data release.

WHERE THE DATA COMES FROM. A weekly GitHub Action on SoRadGaming/openpilot builds the index from Transport for NSW's
open data and publishes it as the release `nswzones-latest`: nsw_zones.npz (~22 MB), manifest.json (format version,
sha256, byte size, data date, attribution) and ATTRIBUTION.txt. The device only ever GETs those three fixed URLs.
Nothing about the car - no position, no route, no identifier - is sent.

WHEN. update_due() is the whole decision, pure so it can be tested:
  * the FIRST download is automatic (no data installed) whenever the mode is not off,
  * after that, weekly (NswZonesAutoUpdate, default on), measured from the last successful CHECK,
  * NswZonesUpdateCheck (the maps page button, or sunnylink) forces a check now.
  Automatic checks run only PARKED (deviceState offroad AND no panda ignition, settled for OFFROAD_SETTLE_S - the
  rule and its reasons are osm_auto_update's) on UNMETERED wi-fi or ethernet, and ignition cancels one in flight.
  A forced check needs only OFFROAD (deviceState heard, alive, started False - OffroadMode with the ignition on
  included: offroad nothing is controlling the car) and a network, not an unmetered one: pressing it is the owner's
  choice of 22 MB. Going onroad cancels any check in flight. A failed automatic check is retried after RETRY_S, not
  every second.

  A newly installed index is loaded into the running matcher only offroad: the swap drops the matcher's state, which
  must not happen while a drive is using it.

HOW. Manifest first; nothing more is downloaded when its sha256 is the installed one or its format version is not
one this code reads (index.manifest_compatible). The index is streamed into a staging directory with a byte cap and
a running sha256, verified against the manifest (size, sha256, format version, attribution) and decoded, converted to
the uncompressed 64-byte aligned copy the matcher memory-maps (~68 MB, ~0 RSS, instant load), smoke-tested with the
matcher, and only then renamed into place as its own version directory. current.json - written to a temp file and
renamed, so it is always whole - is the one switch that names the installed version. Older version directories are
removed afterwards; a matcher still mapping one keeps its pages (Linux keeps an unlinked file's inode).

  /data/media/0/nswzones/
    current.json                      {"dir", "sha256", "stored_sha256", "stored_bytes", "data_version", ...}
    status.json                       the last check, for the maps page
    <data_version>_<sha12>/           nsw_zones_stored.npz, manifest.json, ATTRIBUTION.txt

Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0.
Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW.
"""

import hashlib
import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from openpilot.cereal import log
from openpilot.common.realtime import drop_realtime
from openpilot.common.swaglog import cloudlog
from openpilot.common.time_helpers import system_time_valid
from openpilot.sunnypilot.mapd.nsw_zones import MODE_OFF, read_mode
from openpilot.sunnypilot.mapd.nsw_zones.index import (
  ATTRIBUTION_NAME,
  INDEX_NAME,
  MANIFEST_NAME,
  IndexInvalid,
  load_index,
  manifest_compatible,
  read_manifest,
  sha256_file,
  verify_index,
  write_stored_aligned,
)
from openpilot.sunnypilot.mapd.osm_auto_update import OFFROAD_ONLY, OFFROAD_SETTLE_S, UNMETERED_LINKS, is_offroad, is_parked

BASE_URL = "https://github.com/SoRadGaming/openpilot/releases/download/nswzones-latest/"
DEVICE_DIR = "/data/media/0/nswzones"
DIR_NAME = "nswzones"
CURRENT_NAME = "current.json"
BAD_CURRENT_NAME = "current.bad.json"  # a current.json whose index failed its check at load (mark_bad)
STATUS_NAME = "status.json"
STORED_NAME = "nsw_zones_stored.npz"

AUTO_UPDATE_PARAM = "NswZonesAutoUpdate"
CHECK_PARAM = "NswZonesUpdateCheck"
VERSION_PARAM = "NswZonesVersion"

MAX_AGE_S = 7 * 24 * 3600
RETRY_S = 3600.0  # a failed automatic check waits this long before the next one (this boot)
MAX_INDEX_BYTES = 128 * 1024 * 1024  # the index is ~22 MB; a manifest claiming more is not believed
MAX_SMALL_BYTES = 256 * 1024  # manifest.json, ATTRIBUTION.txt
TIMEOUT_S = 30.0
CHUNK = 1 << 16
STATUS_EVERY_S = 1.0

_SAFE = re.compile(r'[^0-9A-Za-z._-]')


def data_dir() -> str:
  """/data/media/0/nswzones on the device; ~/.comma<prefix>/media/0/nswzones on a PC (as Paths.mapd_root does)."""
  from openpilot.common.hardware import PC

  if PC:
    from openpilot.common.hardware.hw import Paths

    return os.path.join(Paths.comma_home(), "media", "0", DIR_NAME)
  return DEVICE_DIR


def wall_now() -> float:
  """Unix seconds, the same clock osm_auto_update uses (time.time is banned in this repo)."""
  return datetime.now().timestamp()


# ============================================================================ small file helpers
def read_json(path: str) -> dict | None:
  try:
    with open(path, encoding='utf-8') as f:
      d = json.load(f)
  except (OSError, ValueError):
    return None
  return d if isinstance(d, dict) else None


def write_json_atomic(path: str, obj: dict) -> None:
  tmp = f"{path}.tmp{os.getpid()}"
  with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(obj, f, indent=1, sort_keys=True)
    f.flush()
    os.fsync(f.fileno())
  os.replace(tmp, path)


def read_current(d: str) -> dict | None:
  cur = read_json(os.path.join(d, CURRENT_NAME))
  if cur is None or not isinstance(cur.get('dir'), str) or _SAFE.search(cur['dir']) or cur['dir'] in ('', '.', '..'):
    return None
  return cur


def read_status(d: str) -> dict:
  return read_json(os.path.join(d, STATUS_NAME)) or {}


def mark_bad(d: str, dir_name: str) -> bool:
  """The installed index failed its check at load (bit rot, a truncated write): set current.json aside so the device
  counts as having no data and the first-download rule fetches a fresh copy. Only if current.json still names the
  version that failed - an install that finished meanwhile is left alone. -> True if it was set aside."""
  cur = read_current(d)
  if cur is None or cur.get('dir') != dir_name:
    return False
  try:
    os.replace(os.path.join(d, CURRENT_NAME), os.path.join(d, BAD_CURRENT_NAME))
  except OSError:
    return False
  return True


def installed_index(d: str, verify: bool = True) -> tuple[str, dict]:
  """(path of the installed stored index, current.json). Raises IndexInvalid when there is none or it is damaged.
  verify=True re-hashes the file against the sha256 recorded at install (~68 MB: seconds on the device, so a caller
  on a 1 Hz loop does it off-thread)."""
  cur = read_current(d)
  if cur is None:
    raise IndexInvalid('no installed index (current.json missing or unreadable)')
  path = os.path.join(d, cur['dir'], STORED_NAME)
  if not os.path.isfile(path):
    raise IndexInvalid(f'installed index missing: {cur["dir"]}/{STORED_NAME}')
  if cur.get('stored_bytes') is not None and os.path.getsize(path) != int(cur['stored_bytes']):
    raise IndexInvalid('installed index has the wrong size')
  if verify and cur.get('stored_sha256') and sha256_file(path) != cur['stored_sha256']:
    raise IndexInvalid('installed index does not match its recorded sha256')
  return path, cur


# ============================================================================ the decision
def update_due(
  *,
  mode: int,
  auto_enabled: bool,
  forced: bool,
  installed: bool,
  last_check: float,
  now: float,
  time_valid: bool,
  device_state_ok: bool,
  offroad: bool,
  offroad_for: float,
  network_up: bool,
  unmetered: bool,
  busy: bool,
  retry_wait: bool,
) -> tuple[bool, str]:
  """(due, why not). Pure: every input is handed in.

  offroad: deviceState says not started (the rule for a forced check). offroad_for: seconds PARKED continuously
  (offroad and no ignition), negative when not parked or not known to be (the rule for an automatic one). network_up:
  any network; unmetered: wi-fi or ethernet AND not marked metered. last_check: unix time of the last successful check
  (0 never). retry_wait: an automatic check failed less than RETRY_S ago.
  """
  if busy:
    return False, "in progress"
  if not device_state_ok:
    return False, "no deviceState"
  if forced:
    if not offroad:
      return False, OFFROAD_ONLY
    if not network_up:
      return False, "no network"
    return True, ""
  if mode == MODE_OFF:
    return False, "off"
  if installed and not auto_enabled:
    return False, "weekly update off"
  if retry_wait:
    return False, "retry later"
  if offroad_for < OFFROAD_SETTLE_S:
    return False, "not parked long enough"
  if not network_up:
    return False, "no network"
  if not unmetered:
    return False, "metered"
  if not installed:
    return True, ""  # the first download: without it the feature does nothing
  # a clock that is ahead would make the data look old: a pointless 22 MB. One that is behind makes it look new.
  if not time_valid:
    return False, "clock not set"
  if last_check > 0.0 and now - last_check < MAX_AGE_S:
    return False, "checked recently"
  return True, ""


# ============================================================================ fetch + install
class Cancelled(Exception):
  pass


@dataclass
class FetchResult:
  ok: bool
  changed: bool = False
  why: str = ""
  data_version: str = ""
  sha256: str = ""


def http_get(url: str, timeout: float = TIMEOUT_S):
  """GET with no query, no cookies and no identifying header beyond requests' default User-Agent."""
  import requests

  return requests.get(url, stream=True, timeout=timeout, allow_redirects=True)


def _download(get, url: str, dest: str, limit: int, cancel: threading.Event | None = None, progress: Callable[[int], None] | None = None) -> tuple[int, str]:
  """Stream url into dest with a byte cap. -> (bytes, sha256 hex)."""
  h = hashlib.sha256()
  n = 0
  resp = get(url)
  try:
    resp.raise_for_status()
    with open(dest, 'wb') as f:
      for chunk in resp.iter_content(CHUNK):
        if cancel is not None and cancel.is_set():
          raise Cancelled()
        if not chunk:
          continue
        n += len(chunk)
        if n > limit:
          raise IndexInvalid(f'{os.path.basename(dest)}: more than {limit} bytes')
        h.update(chunk)
        f.write(chunk)
        if progress is not None:
          progress(n)
      f.flush()
      os.fsync(f.fileno())
  finally:
    resp.close()
  return n, h.hexdigest()


def _smoke_test(path: str) -> None:
  """The installed file must load the way mapd will load it, and the matcher must run on it."""
  from openpilot.sunnypilot.mapd.nsw_zones.matcher import Matcher

  m = Matcher(path, mmap=True)
  m.update(None, None, None, 0.0, False, None, None, mono_time=0.0, lookahead=False)


def prune(d: str, keep: str | None) -> None:
  """Remove every version and staging directory except `keep`."""
  try:
    names = os.listdir(d)
  except OSError:
    return
  for name in names:
    p = os.path.join(d, name)
    if name != keep and os.path.isdir(p):
      shutil.rmtree(p, ignore_errors=True)
    elif '.tmp' in name:  # an atomic write cut short
      try:
        os.remove(p)
      except OSError:
        pass


def fetch(
  d: str,
  installed_sha: str | None,
  base_url: str = BASE_URL,
  get=http_get,
  cancel: threading.Event | None = None,
  status: Callable[[str, float], None] | None = None,
) -> FetchResult:
  """Check the release and install a new index when there is one. Never raises (except on a programming error in a
  callback): the result says what happened. status(state, fraction) reports progress."""
  os.makedirs(d, exist_ok=True)
  stage = os.path.join(d, '.staging')
  shutil.rmtree(stage, ignore_errors=True)
  try:
    os.makedirs(stage)
    say = status or (lambda _s, _f: None)
    say('checking', 0.0)

    man_path = os.path.join(stage, MANIFEST_NAME)
    _download(get, base_url + MANIFEST_NAME, man_path, MAX_SMALL_BYTES, cancel)
    manifest = read_manifest(man_path)
    if not manifest_compatible(manifest):
      return FetchResult(False, why=f"format {manifest.get('format_version')} not supported")
    sha = str(manifest['sha256']).lower()
    data_version = str(manifest.get('data_version') or '')
    if not re.fullmatch(r'[0-9a-f]{64}', sha):
      return FetchResult(False, why="bad manifest sha256")
    if installed_sha and sha == installed_sha.lower():
      return FetchResult(True, changed=False, why="up to date", data_version=data_version, sha256=sha)
    size = int(manifest['bytes'])
    if not 0 < size <= MAX_INDEX_BYTES:
      return FetchResult(False, why=f"bad manifest size {size}")

    npz = os.path.join(stage, INDEX_NAME)
    last = [0.0]

    def prog(n: int) -> None:
      now = time.monotonic()
      if now - last[0] >= STATUS_EVERY_S:
        last[0] = now
        say('downloading', min(n / size, 1.0))

    say('downloading', 0.0)
    n, got_sha = _download(get, base_url + INDEX_NAME, npz, size, cancel, prog)
    if n != size:
      return FetchResult(False, why=f"size {n} != manifest {size}")
    if got_sha != sha:
      return FetchResult(False, why="sha256 mismatch")
    _download(get, base_url + ATTRIBUTION_NAME, os.path.join(stage, ATTRIBUTION_NAME), MAX_SMALL_BYTES, cancel)

    def check_cancel() -> None:
      # between every install step: each is seconds of CPU and I/O on the device, and the car may be driving off
      if cancel is not None and cancel.is_set():
        raise Cancelled()

    say('installing', 1.0)
    check_cancel()
    info = verify_index(npz, man_path, check_sha=False)  # sha checked while streaming; this decodes it
    data_version = info.data_version or data_version
    check_cancel()
    arrays = load_index(npz)
    check_cancel()
    stored = os.path.join(stage, STORED_NAME)
    write_stored_aligned(arrays, stored)
    del arrays
    os.remove(npz)
    check_cancel()
    _smoke_test(stored)
    check_cancel()  # the last point where nothing has changed: past it the new version is switched in

    name = _SAFE.sub('_', f"{data_version or 'unknown'}_{sha[:12]}")
    final = os.path.join(d, name)
    shutil.rmtree(final, ignore_errors=True)
    os.rename(stage, final)
    stored = os.path.join(final, STORED_NAME)
    write_json_atomic(
      os.path.join(d, CURRENT_NAME),
      {
        'dir': name,
        'sha256': sha,
        'stored_sha256': sha256_file(stored),
        'stored_bytes': os.path.getsize(stored),
        'data_version': data_version,
        'format_version': info.format_version,
        'installed': wall_now(),
      },
    )
    prune(d, keep=name)
    return FetchResult(True, changed=True, why="updated", data_version=data_version, sha256=sha)
  except Cancelled:
    return FetchResult(False, why="cancelled")
  except IndexInvalid as e:
    return FetchResult(False, why=f"invalid: {e}")
  except Exception as e:  # network, disk, anything: the installed data stays as it was
    return FetchResult(False, why=f"failed: {type(e).__name__}")
  finally:
    shutil.rmtree(stage, ignore_errors=True)


# ============================================================================ the stateful half, in mapd_manager
class NswZonesUpdater:
  """Called once per mapd_manager tick. Decides, runs a check in a worker thread, reports it.

  NEVER RAISES into mapd_manager, the process that publishes liveMapDataSP: an error disables it for this boot.
  NswZonesVersion is written here and only here: it names the installed data date for the maps page.
  """

  def __init__(self, params, d: str | None = None, base_url: str = BASE_URL, on_installed: Callable[[], None] | None = None, sm=None, get=http_get):
    self.params = params
    self.dir = d or data_dir()
    self.base_url = base_url
    self.on_installed = on_installed
    self.get = get
    if sm is None:
      import openpilot.cereal.messaging as messaging

      sm = messaging.SubMaster(['deviceState', 'pandaStates'])
    self.sm = sm
    self.disabled = False
    self._offroad_since: float | None = None
    self._thread: threading.Thread | None = None
    self._cancel = threading.Event()
    self._result: FetchResult | None = None
    self._forced = False
    self._retry_at = 0.0
    self._install_pending = False
    self._status_lock = threading.Lock()
    self._sync_version()

  # -- status file ------------------------------------------------------------
  def _write_status(self, **fields) -> None:
    with self._status_lock:
      st = read_status(self.dir)
      st.update(fields)
      try:
        os.makedirs(self.dir, exist_ok=True)
        write_json_atomic(os.path.join(self.dir, STATUS_NAME), st)
      except OSError:
        pass

  def _sync_version(self) -> None:
    """NswZonesVersion follows current.json (a wiped data directory clears it)."""
    try:
      cur = read_current(self.dir)
      want = str(cur.get('data_version') or '') if cur else ''
      if (self.params.get(VERSION_PARAM) or '') != want:
        if want:
          self.params.put(VERSION_PARAM, want)
        else:
          self.params.remove(VERSION_PARAM)
    except Exception:
      pass

  # -- the tick ---------------------------------------------------------------
  def update(self) -> bool:
    """True when it started a check this tick."""
    if self.disabled:
      return False
    try:
      return self._update()
    except Exception:
      self.disabled = True
      self._cancel.set()
      cloudlog.exception("nsw_zones: the update check failed, not retrying this boot")
      return False

  def _worker(self, installed_sha: str | None) -> None:
    try:
      drop_realtime()  # mapd_manager is SCHED_FIFO 5 on cores 0-3; seconds of sha256/decode must not run at that
      self._result = fetch(self.dir, installed_sha, self.base_url, self.get, self._cancel, lambda s, f: self._write_status(state=s, progress=round(f, 3)))
    except Exception as e:
      self._result = FetchResult(False, why=f"failed: {type(e).__name__}")

  def _finish(self) -> None:
    r = self._result or FetchResult(False, why="failed")
    self._thread = None
    self._result = None
    now = wall_now()
    fields = {'state': 'idle', 'progress': 0.0, 'last_attempt': now, 'last_result': r.why}
    if r.ok:
      fields['last_check'] = now
      self._retry_at = 0.0
    elif not self._forced:
      self._retry_at = time.monotonic() + RETRY_S
    self._forced = False
    self._write_status(**fields)
    (cloudlog.info if r.ok else cloudlog.warning)(f"nsw_zones: update check: {r.why} {r.data_version}".rstrip())
    if r.changed:
      self._sync_version()
      self._install_pending = True  # loaded by _update, and only offroad (see there)

  def _update(self) -> bool:
    self.sm.update(0)
    now_mono = time.monotonic()
    offroad = is_offroad(self.sm)
    parked = is_parked(self.sm)
    if parked:
      if self._offroad_since is None:
        self._offroad_since = now_mono
    else:
      self._offroad_since = None

    # A new index replaces the running matcher, and with it every piece of its state (dead-reckoning paths, the line
    # it is on). An install that finished after the device went onroad - the last steps cannot be cancelled once the
    # new version is switched in - is loaded when it is next offroad, not mid-drive. Offroad is enough: nothing is
    # controlled offroad, so OffroadMode with the car on loads it at once.
    if self._install_pending and offroad:
      self._install_pending = False
      if self.on_installed is not None:
        self.on_installed()

    if self._thread is not None:
      # Onroad stops any check: keep what is installed. Ignition stops an automatic one too - automatic stays parked
      # only - but not one somebody asked for in OffroadMode with the car on.
      if not offroad or (not self._forced and not parked):
        self._cancel.set()
      if not self._thread.is_alive():
        self._finish()
      return False

    ds = self.sm['deviceState']
    device_state_ok = bool(self.sm.seen['deviceState'] and self.sm.alive['deviceState'])
    forced = self.params.get_bool(CHECK_PARAM)
    cur = read_current(self.dir)
    due, why = update_due(
      mode=read_mode(self.params),
      auto_enabled=self.params.get_bool(AUTO_UPDATE_PARAM),
      forced=forced,
      installed=cur is not None,
      last_check=float(read_status(self.dir).get('last_check') or 0.0),
      now=wall_now(),
      time_valid=system_time_valid(),
      device_state_ok=device_state_ok,
      offroad=offroad,
      offroad_for=now_mono - self._offroad_since if self._offroad_since is not None else -1.0,
      network_up=ds.networkType != log.DeviceState.NetworkType.none,
      unmetered=ds.networkType in UNMETERED_LINKS and not ds.networkMetered,
      busy=False,
      retry_wait=now_mono < self._retry_at,
    )
    if forced and not due:
      # the button is answered either way: a request that cannot run now is not left to fire later
      self.params.remove(CHECK_PARAM)
      self._write_status(last_attempt=wall_now(), last_result=why)
      return False
    if not due:
      return False
    if forced:
      self.params.remove(CHECK_PARAM)
    self._forced = forced
    self._cancel = threading.Event()
    self._write_status(state='checking', progress=0.0)
    self._thread = threading.Thread(target=self._worker, args=((cur or {}).get('sha256'),), name='nsw_zones_fetch', daemon=True)
    self._thread.start()
    return True
