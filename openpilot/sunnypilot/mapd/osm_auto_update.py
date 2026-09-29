"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): the weekly OSM refresh.

WHY THIS EXISTS. The OSM tiles on a device are fetched once, when a region is
picked, and then never again unless someone presses "Database Update" on the
comma 3/3X OSM panel. The comma 4 (mici) has no OSM panel at all, so on that
device the tiles simply age. OSM is edited continuously and the speed limits
in it are exactly the data that goes wrong off motorways, so a stale copy is a
source of wrong prompts that nothing on the device will ever fix.

WHAT IT DOES. Sets OsmDbUpdatesCheck once, the same param the OSM panel and the
mici maps page write, when all of these hold:
  * OsmAutoUpdateWeekly is on (its own param; off = upstream behavior),
  * a region is already chosen and has been downloaded before (it never starts
    a FIRST download nobody asked for; "All states" of the US is ~6 GB),
  * the last COMPLETED download is more than a week old, by a clock that is
    known good (see "COMPLETED" below),
  * the car has been PARKED for OFFROAD_SETTLE_S (see "PARKED" below),
  * the device is on wi-fi (or ethernet), never cellular,
  * the network is not marked metered, by deviceState,
  * nothing is already requested or downloading.
mapd_manager.update_osm_db() then does the rest, exactly as for a manual press.

PARKED. deviceState.started is not "parked": with OffroadMode (always offroad,
which the mici settings expose) it is False while the car is driving. So parked
is started=False AND every panda reporting no ignition (line or CAN). The comma
3X/4 always have an internal panda, so pandaStates is always there; a device
that has not heard from its panda is not treated as parked.

WI-FI. networkMetered is only True for wi-fi when the connection is marked
metered in NetworkManager. A phone hotspot is "unknown", which reads unmetered,
so a refresh CAN run over a phone hotspot that is in range while parked. The
maps page says so; marking the hotspot metered stops it.

COMPLETED. OsmDownloadedDate is written when a download is REQUESTED, and the
download list lives in /dev/shm, so a power off mid-download leaves a fresh
date and nothing downloading. record_completion() therefore copies the request
date into OsmLastCompleteDate when mapd reports every file downloaded, and the
age is measured from that. An interrupted refresh is retried on the next
eligible boot. A device whose last download predates this recorder has no
completion date and gets one refresh on its first eligible boot.

ONCE PER BOOT. Once it has fired it never looks again for the life of the
process (mapd_manager is always_run, so that is the boot). A download that fails
therefore does not become a download loop.

WHY OFFROAD HAS TO SETTLE. At ignition the device boots with the car already
running, and deviceState.started is False for the first few seconds, until the
panda reports ignition. With home wifi in range that window looked exactly like
"parked, unmetered, stale maps", and the download would start as the car was
pulling out of the garage.

A REQUEST SOMEONE MADE is not held to any of that. OsmDbUpdatesCheck set by the
mici maps button, the comma 3/3X OSM panel or sunnylink runs whenever the device
is OFFROAD (deviceState heard, alive, started False) - OffroadMode with the
ignition on included, because offroad nothing is controlling the car - on any
network: the person asked for it. answer_request() clears a request that cannot
run (onroad, no region chosen, a download already running) before
update_osm_db() sees it, so a request made onroad does not fire later, and
request_allowed tells mapd_manager whether the request update_osm_db() is about
to read is one that was checked: a sunnylink write that lands between the two
reads waits for the next tick's check instead of starting a download unchecked.

GOING ONROAD DOES NOT CANCEL AN OSM DOWNLOAD. mapd has no cancel (upstream's
"TODO-SP: introduce CANCEL database download with mapd"), so a download started
offroad - OffroadMode with the car on, say - runs on after the car drives off,
on whatever network is up. Only the NSW zones downloader cancels on started.

ONE SubMaster UPDATE PER TICK, ALWAYS. deviceState is a conflated queue at 2 Hz:
a SubMaster left unread for minutes is lapped, and its next update() reports
nothing and alive False. So update() reads it at the top of every tick, also
after the weekly latch, or a request after a weekly refresh would be refused
"no deviceState".
"""
import time
from datetime import datetime

import openpilot.cereal.messaging as messaging
from openpilot.cereal import log
from openpilot.common.swaglog import cloudlog
from openpilot.common.time_helpers import system_time_valid

AUTO_UPDATE_PARAM = "OsmAutoUpdateWeekly"
COMPLETE_PARAM = "OsmLastCompleteDate"
REQUEST_PARAM = "OsmDbUpdatesCheck"  # upstream's; mapd_manager.update_osm_db() consumes it
# the reason a request made onroad is refused; the NSW downloader and the maps page say the same
OFFROAD_ONLY = "offroad only"
MAX_MAP_AGE_S = 7 * 24 * 3600
OFFROAD_SETTLE_S = 60.
# mapd drops the download list and writes its last progress in no promised order
COMPLETION_WINDOW_S = 10.

NETWORK_NONE = log.DeviceState.NetworkType.none
UNMETERED_LINKS = (log.DeviceState.NetworkType.wifi, log.DeviceState.NetworkType.ethernet)


def wall_now() -> float:
  """Unix seconds, the same clock mapd_manager stamps OsmDownloadedDate with."""
  return datetime.now().timestamp()


def parse_downloaded_date(raw) -> float:
  """OsmDownloadedDate / OsmLastCompleteDate as unix seconds, or 0. when missing or unreadable.

  mapd_manager writes str(datetime.now().timestamp()); the registry default is
  "0.0". Anything else is treated as "never downloaded".
  """
  try:
    ts = float(raw)
  except (TypeError, ValueError):
    return 0.
  return ts if ts > 0. else 0.


def download_progress(raw) -> tuple[int, int]:
  """OSMDownloadProgress as (downloaded_files, total_files); (0, 0) when missing or unreadable."""
  if not isinstance(raw, dict):
    return 0, 0
  try:
    return int(raw.get("downloaded_files", 0)), int(raw.get("total_files", 0))
  except (TypeError, ValueError):
    return 0, 0


def auto_update_due(*, enabled: bool, location: str, requested_at: float, completed_at: float, now: float,
                    time_valid: bool, device_state_ok: bool, offroad_for: float, network_up: bool, metered: bool,
                    busy: bool) -> tuple[bool, str]:
  """(due, why not). Pure: every input is handed in, so it can be tested without a device.

  requested_at is OsmDownloadedDate, completed_at OsmLastCompleteDate (0. when
  there is none). offroad_for is how long the car has been parked continuously,
  in seconds; negative when it is not parked or not known to be. network_up is
  "on wi-fi or ethernet".
  """
  if not enabled:
    return False, "disabled"
  if not location:
    return False, "no region"
  if requested_at <= 0.:
    return False, "never downloaded"
  if busy:
    return False, "already requested"
  if not device_state_ok:
    return False, "no deviceState"
  if offroad_for < OFFROAD_SETTLE_S:
    return False, "not offroad long enough"
  if not network_up:
    return False, "no wifi"
  if metered:
    return False, "metered"
  # Before NTP the clock can be anywhere; a clock that is behind makes the map
  # look new (harmless), one that is ahead makes it look old (a pointless 270 MB).
  if not time_valid:
    return False, "clock not set"
  # no completion on record: the last download was interrupted, or predates the recorder
  if completed_at > 0. and now - completed_at < MAX_MAP_AGE_S:
    return False, "maps are recent"
  return True, ""


def request_refusal(*, device_state_ok: bool, offroad: bool, location: str, downloading: bool) -> str:
  """Why a request someone made (OsmDbUpdatesCheck) cannot run now; "" when it can. Pure."""
  if not device_state_ok:
    return "no deviceState"
  if not offroad:
    return OFFROAD_ONLY
  if not location:
    return "no region"
  if downloading:
    return "already downloading"  # a second request would delete the tiles and start again
  return ""


def is_offroad(sm) -> bool:
  """Offroad, by a SubMaster over deviceState: heard, alive and not started. Nothing controls the car offroad, so this
  is the rule for a request someone made (a button, sunnylink). OffroadMode with the ignition on IS offroad.

  A default-constructed deviceState reads started=False: unheard or stale must not count as offroad.
  """
  return bool(sm.seen['deviceState'] and sm.alive['deviceState']) and not sm['deviceState'].started


def is_parked(sm) -> bool:
  """Parked, by a SubMaster over deviceState and pandaStates: offroad AND no ignition on any panda. The rule for
  AUTOMATIC downloads. Shared with the NSW zones downloader (FORK(NSW-ZONES)).

  A default-constructed deviceState reads started=False, networkMetered=False:
  exactly the "go" answer. Unheard or stale must not count as parked.
  """
  if not is_offroad(sm):
    return False
  # started is False while driving in OffroadMode: ignition decides
  if not (sm.seen['pandaStates'] and sm.alive['pandaStates']):
    return False
  panda_states = sm['pandaStates']
  return len(panda_states) > 0 and not any(ps.ignitionLine or ps.ignitionCan for ps in panda_states)


class OsmAutoUpdater:
  """The stateful half: watches deviceState and pandaStates, records completions, keeps the once-per-boot latch, and
  answers requests someone made (answer_request)."""

  def __init__(self, params, mem_params):
    self.params = params
    self.mem_params = mem_params
    self.sm = messaging.SubMaster(['deviceState', 'pandaStates'])
    self.done = False
    self._offroad_since: float | None = None
    self._completion_until: float | None = None  # a download just ended: look for "all files" until then
    self._completion_failed = False
    self._request_gate_failed = False
    self._sm_failed = False
    # this tick: the OsmDbUpdatesCheck update_osm_db() may act on was checked (answer_request) or set here (_update)
    self.request_allowed = False

  def update(self) -> bool:
    """Call once per mapd_manager tick, before update_osm_db(). True when it requested a refresh.

    NEVER RAISES. mapd_manager is the process that publishes liveMapDataSP;
    an exception here would take every map speed limit down with it, which is
    a far worse failure than a missed refresh. On any error the refresh is
    given up for this boot.
    """
    self.request_allowed = False
    self._read_sm()
    if not self._completion_failed:
      try:
        self.record_completion()
      except Exception:
        self._completion_failed = True
        cloudlog.exception("mapd: recording the OSM download completion failed, not retrying this boot")
    self.answer_request()
    if self.done:
      return False
    try:
      return self._update()
    except Exception:
      self.done = True
      cloudlog.exception("mapd: weekly OSM refresh check failed, not retrying this boot")
      return False

  def _read_sm(self) -> None:
    """Every tick, whatever else happens (see ONE SubMaster UPDATE PER TICK). A failure leaves deviceState to go
    stale, which every check here reads as "not offroad": it fails closed."""
    try:
      self.sm.update(0)
    except Exception:
      if not self._sm_failed:
        self._sm_failed = True
        cloudlog.exception("mapd: reading deviceState/pandaStates failed")

  def record_completion(self) -> None:
    """Copy the request date into OsmLastCompleteDate once mapd has downloaded every file.

    Runs whatever OsmAutoUpdateWeekly says (it only records), and for every
    download, whoever asked for it. OSMDownloadProgress is cleared on manager
    start, so it can only describe a download seen in this boot.
    """
    now_mono = time.monotonic()
    if self.mem_params.get("OSMDownloadLocations"):
      self._completion_until = now_mono + COMPLETION_WINDOW_S
      return
    if self._completion_until is None:
      return
    done, total = download_progress(self.params.get("OSMDownloadProgress"))
    if total > 0 and done >= total:
      self._completion_until = None
      requested = self.params.get("OsmDownloadedDate") or ""
      self.params.put(COMPLETE_PARAM, requested)
      cloudlog.info(f"mapd: OSM download complete ({done}/{total} files), requested at {requested}")
    elif now_mono > self._completion_until:
      self._completion_until = None
      cloudlog.warning(f"mapd: OSM download ended incomplete ({done}/{total} files)")

  def answer_request(self) -> str:
    """Clear an OsmDbUpdatesCheck that cannot run now, before update_osm_db() consumes it. -> why it was refused
    ("" when there was none, or it may run); request_allowed says which. NEVER RAISES. Reads the SubMaster as update()
    left it this tick.

    Upstream's update_osm_db() starts a download for any request, onroad or not, and sunnylink can write the param
    from a phone at any time. Offroad (not parked) is the rule: the owner parks in OffroadMode with the car on and
    presses the button. It fails closed: after an error every request is cleared for the rest of the boot.
    """
    try:
      if not self.params.get_bool(REQUEST_PARAM):
        return ""
      if self._request_gate_failed:
        why = "request check failed earlier"
      else:
        why = request_refusal(
          device_state_ok=bool(self.sm.seen['deviceState'] and self.sm.alive['deviceState']),
          offroad=is_offroad(self.sm),
          location=self.params.get("OsmLocationName") or "",
          downloading=bool(self.mem_params.get("OSMDownloadLocations")),
        )
      if why:
        self.params.remove(REQUEST_PARAM)
        cloudlog.warning(f"mapd: OSM update request refused: {why}")
      else:
        self.request_allowed = True
      return why
    except Exception:
      self._request_gate_failed = True
      cloudlog.exception("mapd: checking an OSM update request failed, refusing requests this boot")
      try:
        self.params.remove(REQUEST_PARAM)
      except Exception:
        pass
      return "request check failed"

  def _parked(self) -> bool:
    return is_parked(self.sm)

  def _update(self) -> bool:
    now_mono = time.monotonic()
    ds = self.sm['deviceState']
    device_state_ok = self.sm.seen['deviceState'] and self.sm.alive['deviceState']
    if self._parked():
      if self._offroad_since is None:
        self._offroad_since = now_mono
    else:
      self._offroad_since = None
    offroad_for = now_mono - self._offroad_since if self._offroad_since is not None else -1.

    due, why = auto_update_due(
      enabled=self.params.get_bool(AUTO_UPDATE_PARAM),
      location=self.params.get("OsmLocationName") or "",
      requested_at=parse_downloaded_date(self.params.get("OsmDownloadedDate")),
      completed_at=parse_downloaded_date(self.params.get(COMPLETE_PARAM)),
      now=wall_now(),
      time_valid=system_time_valid(),
      device_state_ok=device_state_ok,
      offroad_for=offroad_for,
      network_up=ds.networkType in UNMETERED_LINKS,
      metered=ds.networkMetered,
      busy=self.params.get_bool("OsmDbUpdatesCheck") or bool(self.mem_params.get("OSMDownloadLocations")),
    )
    if not due:
      return False

    self.done = True
    cloudlog.info("mapd: the last completed OSM download is over a week old, requesting the weekly refresh")
    # block=True: update_osm_db() runs next in the same tick and reads it back
    self.params.put_bool("OsmDbUpdatesCheck", True, block=True)
    self.request_allowed = True
    return True
