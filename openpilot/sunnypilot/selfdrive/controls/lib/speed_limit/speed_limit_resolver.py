"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.common.gps import get_gps_location_service
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD, get_sanitize_int_param
from openpilot.sunnypilot.mapd.nsw_zones import MODE_LIVE  # FORK(NSW-ZONES)
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_MAX_MAP_DATA_AGE, LIMIT_ADAPT_ACC
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy, OffsetType

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

ALL_SOURCES = tuple(SpeedLimitSource.schema.enumerants.values())

# FORK(SPEED-LIMIT): SpeedLimitMapStrict drops a carried MAP limit after this long on an untagged road with good GPS.
MAP_HOLD_TIMEOUT = 10.  # s. Every junction gap in the logs was < 8 s; the median untagged stretch was 20-90 s.
# FORK(SPEED-LIMIT): after GPS returns, the frozen map limit is kept until mapd's road name changes or this long passes.
MAP_GPS_SETTLE_TIME = 10.  # s. mapd reported its stale in-tunnel match for 4-8 s after GPS came back at the M4 East exits.
# FORK(NSW-ZONES): liveMapDataSP.nswZone.state values the resolver acts on (nsw_map_data.py)
NSW_STATE_OFF = 0
NSW_STATE_MATCHED = 2
NSW_STATE_DEAD_RECKONING = 4
NSW_STATE_DR_ENDED = 7  # dead reckoning gave up, GPS still lost: its last value, held by mapd
NSW_GPS_LOST_STATES = (NSW_STATE_DEAD_RECKONING, NSW_STATE_DR_ENDED)


class SpeedLimitResolver:
  limit_solutions: dict[custom.LongitudinalPlanSP.SpeedLimit.Source, float]
  distance_solutions: dict[custom.LongitudinalPlanSP.SpeedLimit.Source, float]
  v_ego: float
  speed_limit: float
  speed_limit_last: float
  speed_limit_final: float
  speed_limit_final_last: float
  distance: float
  source: custom.LongitudinalPlanSP.SpeedLimit.Source
  speed_limit_offset: float

  def __init__(self):
    self.params = Params()
    self.frame = -1

    self._gps_location_service = get_gps_location_service(self.params)
    self.limit_solutions = {}  # Store for speed limit solutions from different sources
    self.distance_solutions = {}  # Store for distance to current speed limit start for different sources

    self.policy = self.params.get("SpeedLimitPolicy", return_default=True)
    self.policy = get_sanitize_int_param(
      "SpeedLimitPolicy",
      Policy.min().value,
      Policy.max().value,
      self.params
    )
    self._policy_to_sources_map = {
      Policy.car_state_only: [SpeedLimitSource.car],
      Policy.map_data_only: [SpeedLimitSource.map],
      Policy.car_state_priority: [SpeedLimitSource.car, SpeedLimitSource.map],
      Policy.map_data_priority: [SpeedLimitSource.map, SpeedLimitSource.car],
      Policy.combined: [SpeedLimitSource.car, SpeedLimitSource.map],
    }
    self.source = SpeedLimitSource.none
    for source in ALL_SOURCES:
      self._reset_limit_sources(source)

    self.is_metric = self.params.get_bool("IsMetric")
    self.offset_type = get_sanitize_int_param(
      "SpeedLimitOffsetType",
      OffsetType.min().value,
      OffsetType.max().value,
      self.params
    )
    self.offset_value = self.params.get("SpeedLimitValueOffset", return_default=True)

    self.speed_limit = 0.
    self.speed_limit_last = 0.
    self.speed_limit_final = 0.
    self.speed_limit_final_last = 0.
    self.speed_limit_offset = 0.

    # FORK(SPEED-LIMIT): SpeedLimitMapStrict - tunnel freeze and carried-map-limit timeout (off = upstream behavior).
    self.map_strict = self.params.get_bool("SpeedLimitMapStrict")
    self._map_limit_frozen = 0.  # the map limit last taken with good GPS, held while GPS is lost
    self._map_gps_ok = False  # GPS good and mapd settled; only read in strict mode
    self._map_gps_ok_prev = True
    self._map_settle_timer = 0.
    self._map_settle_road = ""
    self._map_frozen_now = False  # this frame's map limit is the frozen one, not one mapd matched
    self._no_limit_timer = 0.
    self._last_source = SpeedLimitSource.none  # the source that set speed_limit_last

    # FORK(NSW-ZONES): what mapd PUBLISHED decides - liveMapDataSP.nswZone.mode == 2 (live) and state != 0 - not this
    # process's own copy of SpeedLimitNswZones, which lags it by up to PARAMS_UPDATE_PERIOD (a switch to log-only in a
    # tunnel would otherwise let OSM's surface-street value through the freeze for up to 3 s). A mapd started in mode 0,
    # or a log without nswZone (process replay), keeps upstream's behavior exactly.
    self._nsw_active = False
    self._nsw_state = NSW_STATE_OFF

  def update_speed_limit_states(self) -> None:
    self.speed_limit_final = self.speed_limit + self.speed_limit_offset

    if self.speed_limit > 0.:
      self.speed_limit_last = self.speed_limit
      self.speed_limit_final_last = self.speed_limit_final
      self._last_source = self.source  # FORK(SPEED-LIMIT): below
      self._no_limit_timer = 0.
    # FORK(SPEED-LIMIT): an untagged road after a map limit is 'no limit', not the old road's limit carried on for the
    # rest of the drive. Only while GPS is good (a tunnel holds the limit) and never for a car-sourced (sign) limit.
    elif self.map_strict and self._last_source == SpeedLimitSource.map and self._map_gps_ok:
      self._no_limit_timer += DT_MDL  # plannerd runs at 20 Hz (poll='modelV2')
      if self._no_limit_timer > MAP_HOLD_TIMEOUT:
        self.speed_limit_last = 0.
        self.speed_limit_final_last = 0.

  # FORK(SPEED-LIMIT): SLA does not prompt for a frozen limit on engagement (SpeedLimitMapStrict only).
  @property
  def map_limit_frozen(self) -> bool:
    return self._map_frozen_now and self.source == SpeedLimitSource.map

  @property
  def speed_limit_valid(self) -> bool:
    return self.speed_limit > 0.

  @property
  def speed_limit_last_valid(self) -> bool:
    return self.speed_limit_last > 0.

  def update_params(self):
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.policy = self.params.get("SpeedLimitPolicy", return_default=True)
      self.is_metric = self.params.get_bool("IsMetric")
      self.offset_type = self.params.get("SpeedLimitOffsetType", return_default=True)
      self.offset_value = self.params.get("SpeedLimitValueOffset", return_default=True)
      self.map_strict = self.params.get_bool("SpeedLimitMapStrict")  # FORK(SPEED-LIMIT)

  def _get_speed_limit_offset(self) -> float:
    if self.offset_type == OffsetType.off:
      return 0
    elif self.offset_type == OffsetType.fixed:
      return float(self.offset_value * (CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS))
    elif self.offset_type == OffsetType.percentage:
      return float(self.offset_value * 0.01 * self.speed_limit)
    else:
      raise NotImplementedError("Offset not supported")

  def _reset_limit_sources(self, source: custom.LongitudinalPlanSP.SpeedLimit.Source) -> None:
    self.limit_solutions[source] = 0.
    self.distance_solutions[source] = 0.

  def _get_from_car_state(self, sm: messaging.SubMaster) -> None:
    self._reset_limit_sources(SpeedLimitSource.car)
    self.limit_solutions[SpeedLimitSource.car] = sm['carStateSP'].speedLimit
    self.distance_solutions[SpeedLimitSource.car] = 0.

  def _get_from_map_data(self, sm: messaging.SubMaster) -> None:
    self._reset_limit_sources(SpeedLimitSource.map)
    self._process_map_data(sm)
    self._map_limit_frozen = self.limit_solutions[SpeedLimitSource.map]  # FORK(SPEED-LIMIT): held through GPS loss

  def _process_map_data(self, sm: messaging.SubMaster) -> None:
    gps_data = sm[self._gps_location_service]
    map_data = sm['liveMapDataSP']

    # FORK(NSW-ZONES): read before the strict hold, which lets a dead-reckoned NSW limit through
    nsw = map_data.nswZone
    self._nsw_state = int(nsw.state) if int(nsw.mode) == MODE_LIVE else NSW_STATE_OFF
    self._nsw_active = self._nsw_state != NSW_STATE_OFF

    # FORK(SPEED-LIMIT): strict only. Upstream never reads sm.valid here (the plant tests hand plannerd a plain dict).
    self._map_frozen_now = self.map_strict and self._map_strict_hold(sm)
    if self._map_frozen_now:
      # Tunnel or GPS loss: mapd is matching an unlocated position (M4 East -> a surface street). Take no new limit.
      self.limit_solutions[SpeedLimitSource.map] = self._map_limit_frozen
      self.distance_solutions[SpeedLimitSource.map] = 0.
      return

    gps_fix_age = time.monotonic() - gps_data.unixTimestampMillis * 1e-3
    # FORK(NSW-ZONES): upstream subtracts a unix time from a monotonic one, so this age is hugely negative and the
    # check never fires (a dead mapd's last limit is kept). Under NSW live, the age of the liveMapDataSP message.
    if self._nsw_active:
      gps_fix_age = self._map_data_age(sm)
    if gps_fix_age > LIMIT_MAX_MAP_DATA_AGE:
      return

    speed_limit = map_data.speedLimit if map_data.speedLimitValid else 0.
    next_speed_limit = map_data.speedLimitAhead if map_data.speedLimitAheadValid else 0.

    self._calculate_map_data_limits(sm, speed_limit, next_speed_limit)

  # FORK(SPEED-LIMIT): the tunnel freeze and its GPS-return settle window (SpeedLimitMapStrict only).
  def _map_strict_hold(self, sm: messaging.SubMaster) -> bool:
    """True while the map limit stays frozen, and sets _map_gps_ok for the carried-limit timer.

    liveMapDataSP.valid is roughly liveLocationKalman.gpsOK (base_map_data.py): mapd_manager republishes the last LLK
    message it received, so it can lag or stick when locationd_llk is not running. SubMaster stores msg.valid in sm.valid
    even though plannerd lists liveMapDataSP in ignore_valid (that only affects all_valid()).

    GPS lost: frozen. GPS back: still frozen until mapd's road name changes or MAP_GPS_SETTLE_TIME passes, because the
    tick that first publishes valid=True still carries the limit matched from the dead-reckoned position (routes de and
    fc: 4 and 8 s of a surface street's 50 at motorway speed). The carried-limit timer is paused throughout.
    """
    gps_ok = bool(sm.valid['liveMapDataSP'])
    road_name = sm['liveMapDataSP'].roadName
    if gps_ok and not self._map_gps_ok_prev:
      self._map_settle_timer = MAP_GPS_SETTLE_TIME
      self._map_settle_road = road_name
    self._map_gps_ok_prev = gps_ok

    # FORK(NSW-ZONES): the freeze exists because mapd matched the streets above an unlocated position. NSW does not:
    # while it dead-reckons along the tunnel line (state 4) its limit is taken, and so is what it holds once dead
    # reckoning has ended (state 7: its last value, never above a branch still contending - not a value NSW has given
    # up on being re-frozen here by accident). Once it has matched a trusted fix after GPS returns (state 2) the settle
    # window, which waits out OSM's stale match, is over. The carried-limit timer stays paused while GPS is lost.
    if self._nsw_active:
      if self._nsw_state in NSW_GPS_LOST_STATES:
        self._map_gps_ok = False
        return False
      if self._nsw_state == NSW_STATE_MATCHED and gps_ok:
        self._map_settle_timer = 0.

    if gps_ok and self._map_settle_timer > 0.:
      self._map_settle_timer -= DT_MDL
      if road_name == self._map_settle_road:
        self._map_gps_ok = False
        return True
      self._map_settle_timer = 0.

    self._map_gps_ok = gps_ok
    return not gps_ok

  def _calculate_map_data_limits(self, sm: messaging.SubMaster, speed_limit: float, next_speed_limit: float) -> None:
    gps_data = sm[self._gps_location_service]
    map_data = sm['liveMapDataSP']

    distance_since_fix = self.v_ego * (time.monotonic() - gps_data.unixTimestampMillis * 1e-3)
    distance_to_speed_limit_ahead = max(0., map_data.speedLimitAheadDistance - distance_since_fix)

    self.limit_solutions[SpeedLimitSource.map] = speed_limit
    self.distance_solutions[SpeedLimitSource.map] = 0.

    # FORK(NSW-ZONES): no early switch to the next limit under NSW live. Upstream's look-ahead below never fires on a car
    # (distance_since_fix is a monotonic minus a unix time: hugely negative), and making it fire is a behavior change of
    # its own: replaying the owner's four M4 East / Rozelle passes through mapd and this resolver, NSW's look-ahead
    # reported lower limits that never came (80 or 60 ahead in the main tunnel while dead reckoning, 80 at the west portal
    # while matched), and each would have dropped the limit for 1-3 s. The next limit is still published for the UI.
    if self._nsw_active:
      return

    # FIXME-SP: this is not working as expected
    if 0. < next_speed_limit < self.v_ego:
      adapt_time = (next_speed_limit - self.v_ego) / LIMIT_ADAPT_ACC
      adapt_distance = self.v_ego * adapt_time + 0.5 * LIMIT_ADAPT_ACC * adapt_time ** 2

      if distance_to_speed_limit_ahead <= adapt_distance:
        self.limit_solutions[SpeedLimitSource.map] = next_speed_limit
        self.distance_solutions[SpeedLimitSource.map] = distance_to_speed_limit_ahead

  # FORK(NSW-ZONES)
  @staticmethod
  def _map_data_age(sm: messaging.SubMaster) -> float:
    """Seconds since mapd published the liveMapDataSP message in hand (logMonoTime is time.monotonic() in ns). The GPS
    fix age is NOT used: a fix is old by design while NSW dead-reckons through a tunnel."""
    return time.monotonic() - sm.logMonoTime['liveMapDataSP'] * 1e-9

  def _get_source_solution_according_to_policy(self) -> custom.LongitudinalPlanSP.SpeedLimit.Source:
    sources_for_policy = self._policy_to_sources_map[Policy(self.policy)]

    if Policy(self.policy) != Policy.combined:
      # They are ordered in the order of preference, so we pick the first that's non-zero
      for source in sources_for_policy:
        if self.limit_solutions[source] > 0.:
          return source
      return SpeedLimitSource.none

    sources_with_limits = [(s, limit) for s, limit in [(s, self.limit_solutions[s]) for s in sources_for_policy] if limit > 0.]
    if sources_with_limits:
      return min(sources_with_limits, key=lambda x: x[1])[0]

    return SpeedLimitSource.none

  def _resolve_limit_sources(self, sm: messaging.SubMaster) -> tuple[float, float, custom.LongitudinalPlanSP.SpeedLimit.Source]:
    """Get limit solutions from each data source"""
    self._get_from_car_state(sm)
    self._get_from_map_data(sm)

    source = self._get_source_solution_according_to_policy()
    speed_limit = self.limit_solutions[source] if source else 0.
    distance = self.distance_solutions[source] if source else 0.

    return speed_limit, distance, source

  def update(self, v_ego: float, sm: messaging.SubMaster) -> None:
    self.v_ego = v_ego
    self.update_params()

    self.speed_limit, self.distance, self.source = self._resolve_limit_sources(sm)
    self.speed_limit_offset = self._get_speed_limit_offset()

    self.update_speed_limit_states()

    self.frame += 1
