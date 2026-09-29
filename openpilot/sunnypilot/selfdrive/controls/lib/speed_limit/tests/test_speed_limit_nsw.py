"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the resolver when mapd publishes NSW live (liveMapDataSP.nswZone.mode 2).
  * fix B's GPS-loss freeze lets the NSW dead-reckoned limit through (nswZone.state 4) and the value NSW holds once
    dead reckoning has ended (state 7); an NSW match after GPS returns (state 2) ends the settle window; everything
    else still freezes as before
  * the map-data age is the liveMapDataSP message's age, so the stale check works; there is no early switch to the
    next limit (upstream's look-ahead never fires on a car, and NSW's reported limits that never came)
  * none of it happens in mode 0 or 1, or when the message does not come from the NSW publisher (state 0). The MESSAGE's
    mode decides, not the resolver's copy of the param, which lags mapd by up to 3 s
"""
import time
from datetime import datetime
from types import SimpleNamespace

from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import LIMIT_MAX_MAP_DATA_AGE
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Policy
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver, \
  MAP_GPS_SETTLE_TIME

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

MATCHED, DEAD_RECKONING, NO_MATCH, OSM_PUBLISHER, DR_ENDED, AMBIGUOUS = 2, 4, 1, 0, 7, 3
V_EGO = 25.


class NswSM:
  def __init__(self, limit_kph, state, gps_ok=True, age=0., ahead_kph=0., ahead_m=0., road="", unix_fix=True, mode=2):
    self._map = SimpleNamespace(speedLimit=limit_kph * CV.KPH_TO_MS, speedLimitValid=limit_kph > 0,
                                speedLimitAhead=ahead_kph * CV.KPH_TO_MS, speedLimitAheadValid=ahead_kph > 0,
                                speedLimitAheadDistance=float(ahead_m), roadName=road,
                                nswZone=SimpleNamespace(state=state if mode else 0, mode=mode))
    # a real unix time, as on the car (upstream's own tests feed time.monotonic() here, which hides the bug)
    self._gps = SimpleNamespace(unixTimestampMillis=(datetime.now().timestamp() if unix_fix else time.monotonic()) * 1e3)
    self._car = SimpleNamespace(speedLimit=0.)
    self.valid = {'liveMapDataSP': gps_ok}
    self.logMonoTime = {'liveMapDataSP': int((time.monotonic() - age) * 1e9)}

  def __getitem__(self, key):
    return {'liveMapDataSP': self._map, 'carStateSP': self._car}.get(key, self._gps)


class NswResolverTestBase(OpenpilotTestCase):
  nsw_mode = 2

  def setup_method(self):
    params = Params()
    params.put("SpeedLimitPolicy", int(Policy.map_data_only), block=True)
    params.put_bool("SpeedLimitMapStrict", True, block=True)
    params.put("SpeedLimitNswZones", self.nsw_mode, block=True)
    params.put_bool("IsMetric", True, block=True)
    params.put("SpeedLimitOffsetType", 0, block=True)
    params.put("SpeedLimitValueOffset", 0, block=True)
    self.r = SpeedLimitResolver()

  def run_for(self, seconds, *args, **kwargs):
    kwargs.setdefault('mode', self.nsw_mode)  # mapd publishes the mode it runs in (0: OsmMapData, nswZone unset)
    for _ in range(max(1, round(seconds / DT_MDL))):
      self.r.update(V_EGO, NswSM(*args, **kwargs))  # ty: ignore[invalid-argument-type]
    return self.r.speed_limit / CV.KPH_TO_MS

  def tunnel(self, dr_kph, dr_state):
    """Good GPS at 90, then GPS lost for 20 s while mapd publishes dr_kph with nswZone.state dr_state."""
    assert round(self.run_for(2., 90, MATCHED)) == 90
    return round(self.run_for(20., dr_kph, dr_state, gps_ok=False))


class TestNswLive(NswResolverTestBase):
  def test_dead_reckoning_passes_the_freeze(self):
    assert self.tunnel(80, DEAD_RECKONING) == 80
    assert not self.r.map_limit_frozen, "a dead-reckoned NSW limit is a real limit: SLA may prompt for it"

  def test_dead_reckoning_ended_is_not_refrozen(self):
    """Dead reckoning held 90, then the paths disagreed (80 / 70 branches): mapd published the lowest, 70, and holds it
    as state 7 once dead reckoning ends. The resolver takes that value - it does not freeze the 90 it had before -
    for as long as GPS stays lost, and without prompting as if it were frozen."""
    assert self.tunnel(90, DEAD_RECKONING) == 90
    assert round(self.run_for(5., 70, DEAD_RECKONING, gps_ok=False)) == 70
    assert round(self.run_for(300., 70, DR_ENDED, gps_ok=False)) == 70
    assert round(self.r.speed_limit_final_last / CV.KPH_TO_MS) == 70
    assert not self.r.map_limit_frozen
    # GPS back, NSW not yet matched (ambiguous, 0): the settle window carries the 70, never the old 90
    assert round(self.run_for(5., 0, AMBIGUOUS)) == 70
    assert round(self.r.speed_limit_final_last / CV.KPH_TO_MS) == 70

  def test_dead_reckoning_ended_holds_its_value_not_osm(self):
    # mapd publishes NSW's held value in state 7, never OSM's surface street: the resolver passes it
    assert self.tunnel(90, DEAD_RECKONING) == 90
    assert round(self.run_for(60., 90, DR_ENDED, gps_ok=False)) == 90

  def test_the_message_mode_decides_not_the_param(self):
    """Switched to log-only in a tunnel: mapd publishes OSM (mode 1) at once, while this resolver's copy of the param
    still says 2 for up to 3 s. The message wins: fix B's freeze, never OSM's surface value."""
    assert self.tunnel(90, DEAD_RECKONING) == 90
    assert round(self.run_for(2., 50, DEAD_RECKONING, gps_ok=False, mode=1)) == 90
    assert self.r.map_limit_frozen

  def test_gps_lost_without_nsw_dead_reckoning_is_still_frozen(self):
    # NSW has nothing to follow (no match / GPS lost -> OSM, which matches the streets above)
    assert self.tunnel(50, NO_MATCH) == 90
    assert self.r.map_limit_frozen

  def test_the_osm_publisher_keeps_fix_b(self):
    """mapd_manager started in mode 0 (OsmMapData, state 0) while the param now says 2: upstream behavior."""
    assert self.tunnel(50, OSM_PUBLISHER) == 90

  def test_an_nsw_match_after_gps_returns_ends_the_settle_window(self):
    self.tunnel(90, DEAD_RECKONING)
    assert round(self.run_for(DT_MDL, 60, MATCHED, road="Tunnel")) == 60

  def test_the_settle_window_still_holds_osm(self):
    self.tunnel(90, DEAD_RECKONING)
    # GPS back, NSW has no match: OSM's stale in-tunnel street is not taken until the road name changes / 10 s pass
    assert round(self.run_for(MAP_GPS_SETTLE_TIME / 2, 50, NO_MATCH, road="Surface St")) == 90
    assert round(self.run_for(MAP_GPS_SETTLE_TIME, 50, NO_MATCH, road="Surface St")) == 50

  def test_stale_map_data_is_dropped(self):
    assert round(self.run_for(1., 90, MATCHED, age=LIMIT_MAX_MAP_DATA_AGE + 1)) == 0
    assert round(self.run_for(1., 90, MATCHED, age=LIMIT_MAX_MAP_DATA_AGE - 1)) == 90

  def test_no_early_switch_to_the_next_limit(self):
    """Upstream's look-ahead never fires on a car; under NSW live it is off on purpose (see the resolver), not by the
    time-base bug: 60 ahead at 10 m, with a unix-time fix, still reads 90."""
    for dist in (300, 150, 10):
      assert round(self.run_for(DT_MDL, 90, MATCHED, ahead_kph=60, ahead_m=dist)) == 90
      assert self.r.distance == 0.
    # also with the fix time upstream's own tests use (monotonic), which WOULD make upstream's look-ahead fire
    assert round(self.run_for(DT_MDL, 90, MATCHED, ahead_kph=60, ahead_m=10, unix_fix=False)) == 90


class TestNswLogOnly(NswResolverTestBase):
  """Mode 1 changes nothing downstream: everything is as with NSW off."""
  nsw_mode = 1

  def test_fix_b_is_unchanged(self):
    assert self.tunnel(80, DEAD_RECKONING) == 90

  def test_the_age_is_upstreams(self):
    # upstream's age (monotonic minus unix) never exceeds the limit, so a stale message is still taken ...
    assert round(self.run_for(1., 90, MATCHED, age=LIMIT_MAX_MAP_DATA_AGE + 1)) == 90
    # ... and its look-ahead never triggers
    assert round(self.run_for(DT_MDL, 90, MATCHED, ahead_kph=60, ahead_m=150)) == 90


class TestNswOff(TestNswLogOnly):
  nsw_mode = 0
