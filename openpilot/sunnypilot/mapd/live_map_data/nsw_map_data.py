"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): liveMapDataSP with Transport for NSW's speed zones as the map speed limit.

ONE PUBLISHER. mapd_manager publishes liveMapDataSP and nothing else may, so this is OsmMapData with the NSW matcher
in it, chosen by mapd_manager at start when SpeedLimitNswZones is not 0. Everything OsmMapData does still happens:
LastGPSPosition is written for the Go mapd, and its limit, next limit and road name are read every tick.

WHAT IS PUBLISHED as liveMapDataSP.speedLimit (Source.map downstream; nothing else in the stack changes):
  mode 0 (off)       OSM, and the message is exactly OsmMapData's (nswZone is never touched)
  mode 1 (log only)  OSM, always. nswZone says what NSW would have published.
  mode 2 (live)      matched (2): the NSW limit and the NSW next limit; dead reckoning (4): the NSW limit, no next
                     dead reckoning ended (7): the last value dead reckoning published, held until GPS returns
                       (never OSM: its match of an unlocated position is the surface street above the tunnel)
                     ambiguous (3): 0 - fix A's timer then drops a carried limit ("silence when unsure") - unless
                       OSM's limit IS the NSW best guess: two sources agreeing is not ambiguous (the Western
                       Distributor after the city-bound Rozelle exit: NSW 60 vs a 50 rival, OSM 60)
                     no match (1) within NO_MATCH_HOLD_S of a match: the NSW limit held (a turn can reject every
                       line by heading for one tick; OSM's different value used to flick through for a second)
                     no match / GPS lost (1), error (5), no data (6): OSM
roadName is always OSM's: the resolver's GPS-return settle window watches it.

INPUTS, once per tick (1 Hz, as validated on the owner's drives):
  position, course, accuracy   the GPS service (gpsLocationExternal on a u-blox device), fresh within 1.5 s, with a fix
  gps_ok                       ... AND liveLocationKalman.gpsOK (fresh within 2 s): the P1 replay's rule
  speed                        carState.vEgo (fresh), else the GPS speed; it drives the tunnel odometry
  yaw                          degrees(deviceMotion.orientationNED.z) while orientationNED.valid (deviceMotion is what
                               upstream renamed livePose to). Fed every tick: the pre-loss history seeds dead reckoning.
  time                         the wall clock when system_time_valid(), else None (school zones read 'unknown')

EVERY NSW CALL IS WRAPPED: an exception publishes OSM with state 5 and counts in nswZone.errors. The index is loaded
off-thread (a sha256 over ~68 MB takes seconds on the device); until it is there the state is 6 and OSM is published.

Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0.
Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW.
"""
import math
import threading
import time
from datetime import date, datetime

import openpilot.cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.gps import get_gps_location_service
from openpilot.common.realtime import drop_realtime
from openpilot.common.swaglog import cloudlog
from openpilot.common.time_helpers import system_time_valid
from openpilot.sunnypilot.mapd.live_map_data.osm_map_data import OsmMapData
from openpilot.sunnypilot.mapd.nsw_zones import MODE_LIVE, MODE_OFF, read_mode
from openpilot.sunnypilot.mapd.nsw_zones import downloader
from openpilot.sunnypilot.mapd.nsw_zones.index import IndexInvalid

# nswZone.state codes the integration sets itself (the matcher's own are matcher.STATE_CODES)
STATE_OFF = 0
STATE_NO_MATCH = 1
STATE_MATCHED = 2
STATE_AMBIGUOUS = 3
STATE_DEAD_RECKONING = 4
STATE_ERROR = 5
STATE_NO_DATA = 6
STATE_DR_ENDED = 7
DR_STATES = (STATE_DEAD_RECKONING, STATE_DR_ENDED)  # GPS lost: NSW's value or nothing, never OSM's

NO_MATCH_HOLD_S = 3.0  # a no-match this soon after a match keeps the NSW limit
STALE_DATA_DAYS = 60  # the maps-data offroad alert: data older than this ...
CALENDAR_WARN_DAYS = 60  # ... or a school calendar ending within this

GPS_MAX_AGE_S = 1.5
LLK_MAX_AGE_S = 2.0
CAR_STATE_MAX_AGE_S = 1.0
MOTION_MAX_AGE_S = 1.0
LOG_FIRST_ERRORS = 5
LOG_ERROR_EVERY = 100


def wall_now() -> float:
  """Unix seconds for the school-zone clock (time.time is banned in this repo)."""
  return datetime.now().timestamp()


def _num(x) -> float:
  """A matcher result number for a Float32 field; -1 for 'unknown'."""
  return float(x) if x is not None and math.isfinite(float(x)) else -1.


class NswZoneMapData(OsmMapData):
  def __init__(self, data_dir: str | None = None, load: bool = True):
    # OsmMapData first: it creates the one liveMapDataSP PubMaster. Nothing after this may raise out of here - a
    # failure would make mapd_manager build a second publisher - so a broken NSW setup leaves this object working
    # exactly as OsmMapData, for the whole boot.
    super().__init__()
    self.disabled = False
    self.gps_service = ""
    self.dir = data_dir or ""
    self.mode = MODE_OFF

    self.matcher = None
    self.data_version = ""
    self.calendar_end = None  # the loaded matcher's school calendar end (datetime.date)
    self.load_state = STATE_NO_DATA  # what to publish while there is no matcher
    self._loader: threading.Thread | None = None
    self._reload_pending = False

    self.errors = 0
    self.result: dict | None = None  # this tick's matcher result
    self.state = STATE_OFF
    self.osm_speed_limit = 0.
    self._nsw_pub: float | None = None  # this tick's NSW value in m/s (0 = silence), None = OSM's
    self._last_match: tuple[float, float] | None = None  # (monotonic time, m/s) of the last matched NSW value
    try:
      self.gps_service = get_gps_location_service(self.params)
      # replaces BaseMapData's liveLocationKalman-only SubMaster; liveLocationKalman stays first for OsmMapData
      self.sm = messaging.SubMaster(['liveLocationKalman', 'carState', 'deviceMotion', self.gps_service])
      self.dir = data_dir or downloader.data_dir()
      self.mode = read_mode(self.params)
      if load:
        self.reload()
    except Exception:
      self.disabled = True
      self.mode = MODE_OFF
      cloudlog.exception("nsw_zones: setup failed, publishing OSM only this boot")

  # -- the index --------------------------------------------------------------
  def reload(self) -> None:
    """(Re)load the installed index off-thread. mapd_manager calls it after the downloader installs a new one."""
    if self._loader is not None and self._loader.is_alive():
      self._reload_pending = True
      return
    self._reload_pending = False
    self._loader = threading.Thread(target=self._load, name='nsw_zones_load', daemon=True)
    self._loader.start()

  def _load(self) -> None:
    from openpilot.sunnypilot.mapd.nsw_zones.matcher import Matcher
    drop_realtime()  # this thread only: seconds of sha256 over ~68 MB must not run at mapd_manager's SCHED_FIFO 5
    cur = None
    try:
      cur = downloader.read_current(self.dir)
      path, cur = downloader.installed_index(self.dir, verify=True)
      m = Matcher(path, mmap=True)
      self.data_version = str(m.data_version or cur.get('data_version') or '')
      self.calendar_end = m.calendar_end
      self.matcher = m
      cloudlog.info(f"nsw_zones: index {self.data_version} loaded ({m.calendar_desc})")
    except IndexInvalid as e:
      self.matcher = None
      self.data_version = ""
      self.load_state = STATE_NO_DATA if cur is None else STATE_ERROR
      cloudlog.warning(f"nsw_zones: no usable index: {e}")
      # an installed copy that fails its check is never going to pass it: set it aside, so the downloader sees no
      # data and fetches a fresh copy by the first-download rule (it would otherwise answer 'up to date' forever)
      if cur is not None and downloader.mark_bad(self.dir, cur['dir']):
        cloudlog.warning(f"nsw_zones: installed index {cur['dir']} set aside; a fresh copy will be downloaded")
    except Exception:
      self.matcher = None
      self.data_version = ""
      self.load_state = STATE_ERROR
      self.errors += 1
      cloudlog.exception("nsw_zones: loading the index failed")
    if self._reload_pending:
      self._reload_pending = False
      self._load()

  # -- inputs -----------------------------------------------------------------
  def _fresh(self, service: str, max_age: float, now: float) -> bool:
    return bool(self.sm.seen[service]) and now - self.sm.logMonoTime[service] * 1e-9 <= max_age

  def matcher_inputs(self) -> dict:
    now = time.monotonic()
    llk = self.sm['liveLocationKalman']
    gps = self.sm[self.gps_service]
    have_fix = self._fresh(self.gps_service, GPS_MAX_AGE_S, now) and bool(gps.hasFix)
    gps_ok = have_fix and self._fresh('liveLocationKalman', LLK_MAX_AGE_S, now) and bool(llk.gpsOK)

    if self._fresh('carState', CAR_STATE_MAX_AGE_S, now):
      speed = float(self.sm['carState'].vEgo)
    else:
      speed = float(gps.speed) if have_fix else None  # OffroadMode: no carState while driving

    yaw_deg = None
    if self._fresh('deviceMotion', MOTION_MAX_AGE_S, now):
      ned = self.sm['deviceMotion'].orientationNED
      if ned.valid and math.isfinite(ned.z):
        yaw_deg = math.degrees(ned.z)

    return {
      'lat': float(gps.latitude) if gps_ok else None,
      'lon': float(gps.longitude) if gps_ok else None,
      'bearing_deg': float(gps.bearingDeg) if gps_ok else None,
      'speed_mps': speed,
      'gps_ok': gps_ok,
      'h_accuracy_m': float(gps.horizontalAccuracy) if gps_ok else None,
      'unix_time': wall_now() if system_time_valid() else None,
      'mono_time': now,
      'yaw_deg': yaw_deg,
    }

  # -- the tick ---------------------------------------------------------------
  def update_location(self) -> None:
    super().update_location()
    self.result = None
    if self.disabled:
      return

    mode = read_mode(self.params)
    if mode != self.mode and self.matcher is not None:
      self.matcher.reset()
    self.mode = mode
    if mode == MODE_OFF:
      self.state = STATE_OFF
      return

    m = self.matcher
    if m is None:
      self.state = self.load_state
      return
    try:
      r = m.update(**self.matcher_inputs())
      self.result = r
      self.state = int(r['state_code'])
    except Exception:
      self.result = None
      self.state = STATE_ERROR
      self.errors += 1
      if self.errors <= LOG_FIRST_ERRORS or self.errors % LOG_ERROR_EVERY == 0:
        cloudlog.exception(f"nsw_zones: matcher error #{self.errors}, publishing OSM")

  def _nsw_limit_ms(self) -> float:
    kph = self.result.get('limit_kph') if self.result is not None else None
    return float(kph) * CV.KPH_TO_MS if kph else 0.

  def _live(self) -> bool:
    return self.mode == MODE_LIVE and self.result is not None

  def _decide(self, osm: float) -> float | None:
    """What NSW publishes this tick in m/s (0. = nothing, on purpose), or None to leave it to OSM. Computed in modes 1
    and 2 alike (nswZone.speedLimit logs it); only mode 2 publishes it."""
    r = self.result
    if r is None:
      return None
    now = time.monotonic()
    nsw = self._nsw_limit_ms()
    if self.state == STATE_MATCHED and nsw > 0.:
      self._last_match = (now, nsw)
      return nsw
    if self.state in DR_STATES:
      self._last_match = None
      return nsw  # 0. if it has none: never OSM while GPS is lost (fix B's freeze is bypassed for these states)
    if self.state == STATE_AMBIGUOUS:
      cand = r.get('candidate_kph')
      if cand and osm > 0. and round(osm * CV.MS_TO_KPH) == int(cand):
        return osm  # OSM's limit is NSW's own best guess: two sources agree
      return 0.
    if self.state == STATE_NO_MATCH and r.get('state') == 'no_match' and self._last_match is not None:
      t, v = self._last_match
      if now - t <= NO_MATCH_HOLD_S:
        return v
    return None

  def get_current_speed_limit(self) -> float:
    osm = super().get_current_speed_limit()
    self.osm_speed_limit = osm
    self._nsw_pub = self._decide(osm) if not self.disabled else None
    if self._live() and self._nsw_pub is not None:
      return self._nsw_pub
    return osm

  def _nsw_ahead(self) -> tuple[float, float]:
    r = self.result
    if r is None or not r.get('ahead_kph') or r.get('ahead_dist_m') is None:
      return 0., 0.
    return float(r['ahead_kph']) * CV.KPH_TO_MS, float(r['ahead_dist_m'])

  def get_next_speed_limit_and_distance(self) -> tuple[float, float]:
    osm = super().get_next_speed_limit_and_distance()
    if self._live() and self._nsw_pub is not None:
      # never OSM's next limit behind an NSW current one. Only a match has one: while dead reckoning the look-ahead
      # along the dead-reckoned path reported lower limits that never came on the owner's tunnel passes (nswZone keeps
      # it for the logs), and a held or agreed value has none of its own.
      return self._nsw_ahead() if self.state == STATE_MATCHED and self._nsw_pub > 0. else (0., 0.)
    return osm

  def stale_alert(self, today) -> str | None:
    """Text for the offroad alert when the installed data is old or its school calendar ends soon; None otherwise.
    today: datetime.date (the caller checks the clock is valid)."""
    if self.disabled or self.mode == MODE_OFF or self.matcher is None:
      return None
    out = []
    try:
      age = (today - date.fromisoformat(self.data_version[:10])).days
    except ValueError:
      age = None
    if age is not None and age > STALE_DATA_DAYS:
      out.append(f"The NSW speed zone data is {age} days old ({self.data_version[:10]}). It updates weekly when parked on "
                 + "wi-fi: check the nsw zones card on the maps page.")
    end = self.calendar_end
    if end is not None and (end - today).days < CALENDAR_WARN_DAYS:
      out.append(f"The NSW school-day calendar ends {end}. After it, every weekday is taken as a school day until a "
                 + "software update adds the next year's dates.")
    return " ".join(out) or None

  def fill_extensions(self, live_map_data) -> None:
    if self.mode == MODE_OFF:
      return  # exactly OsmMapData's message
    z = live_map_data.nswZone
    z.state = self.state
    z.mode = self.mode
    z.errors = min(self.errors, 65535)
    z.dataVersion = self.data_version if self.matcher is not None else ""
    z.osmSpeedLimit = float(self.osm_speed_limit)
    r = self.result
    if r is None:
      z.zoneType = 255
      z.matchDistance = -1.
      z.headingError = -1.
      return
    z.speedLimit = float(self._nsw_pub or 0.)  # what NSW publishes (mode 2) or would publish (mode 1)
    z.speedLimitAhead, z.speedLimitAheadDistance = self._nsw_ahead()
    z.zoneType = int(r.get('zone_type_code', 255)) & 0xFF
    z.schoolZone = int(r.get('school_code') or 0)
    z.matchDistance = _num(r.get('dist_m'))
    z.headingError = _num(r.get('heading_err_deg'))
    z.candidates = min(int(r.get('n_candidates') or 0), 255)
    z.holdDistance = float(r.get('dr_m') or 0.)
    z.variable = bool(r.get('variable'))
    z.confidence = float(r.get('confidence') or 0.)
    z.hypotheses = min(int(r.get('dr_hyps') or 0), 255)
