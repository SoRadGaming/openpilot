"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): NswZoneMapData, the liveMapDataSP publisher with the NSW matcher in it. The promises:
  mode 0  the message is byte for byte what OsmMapData publishes
  mode 1  speedLimit (and the look-ahead) are exactly OSM's; nswZone says what NSW would have published
  mode 2  the NSW limit where NSW matched or is dead reckoning, its held value once dead reckoning has ended (never
          OSM while GPS is lost), 0 where it is ambiguous (unless OSM equals NSW's best guess), the NSW limit for up
          to 3 s of a no-match right after a match, OSM everywhere else
  any mode: an exception in the matcher publishes OSM (state 5) and is counted; no data file is state 6
"""
import datetime
import math
import os
import shutil
import tempfile
from types import SimpleNamespace
from unittest import mock

import openpilot.cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.mapd.live_map_data import nsw_map_data as nmd
from openpilot.sunnypilot.mapd.live_map_data.nsw_map_data import NswZoneMapData
from openpilot.sunnypilot.mapd.live_map_data.osm_map_data import OsmMapData
from openpilot.sunnypilot.mapd.nsw_zones import MODE_LIVE, MODE_LOG_ONLY, MODE_OFF
from openpilot.sunnypilot.mapd.nsw_zones import downloader as dl
from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import MiniIndex, feat, install_for_test, latlon

GPS = 'gpsLocationExternal'
OSM_KPH = 50.
NSW_KPH = 60
T0 = 1000.0


class Clock:
  def __init__(self):
    self.t = T0

  def monotonic(self):
    return self.t

  def wall(self):
    return 1_790_000_000.0 + self.t  # 2026-09; school zones do not matter here


class FakeParams:
  def __init__(self, mode):
    self.values = {"SpeedLimitNswZones": mode}

  def get(self, key, **_):
    return self.values.get(key)

  def get_bool(self, key, **_):
    return bool(self.values.get(key))

  def put(self, key, val, **_):
    self.values[key] = val


class FakeMem:
  """mapd's /dev/shm params: what the Go mapd matched from OSM."""
  def __init__(self):
    self.values = {"MapSpeedLimit": OSM_KPH * CV.KPH_TO_MS, "RoadName": "Parramatta Rd",
                   "NextMapSpeedLimit": {"speedlimit": 70 * CV.KPH_TO_MS, "latitude": -33.79, "longitude": 151.0}}

  def get(self, key, **_):
    return self.values.get(key)

  def put(self, key, val, **_):
    self.values[key] = val


class FakePM:
  def __init__(self):
    self.sent = []

  def send(self, service, msg):
    self.sent.append(msg)


class FakeSM:
  """The latest message of each service, with logMonoTime on the test clock."""
  def __init__(self, clock):
    self.clock = clock
    self.msgs = {s: messaging.new_message(s) for s in ('liveLocationKalman', 'carState', 'deviceMotion', GPS)}
    self.seen = dict.fromkeys(self.msgs, False)
    self.logMonoTime = dict.fromkeys(self.msgs, 0)

  def update(self, _timeout=0):
    pass

  def put(self, service, **fields):
    msg = self.msgs[service]
    body = getattr(msg, service)
    for k, v in fields.items():
      obj = body
      *path, last = k.split('.')
      for p in path:
        obj = getattr(obj, p)
      setattr(obj, last, v)
    self.seen[service] = True
    self.logMonoTime[service] = int(self.clock.monotonic() * 1e9)

  def __getitem__(self, s):
    return getattr(self.msgs[s], s)


class TestNswMapData(OpenpilotTestCase):
  @classmethod
  def setUpClass(cls):
    cls.mini = MiniIndex([feat([(0, -2000), (0, 2000)], NSW_KPH)])

  @classmethod
  def tearDownClass(cls):
    cls.mini.close()

  def setup_method(self):
    self.dir = tempfile.mkdtemp(prefix='nswz_md_')
    install_for_test(self.dir, self.mini.path)
    self.clock = Clock()
    for p in (mock.patch.object(nmd, "time", self.clock), mock.patch.object(nmd, "wall_now", self.clock.wall),
              mock.patch.object(nmd, "system_time_valid", lambda: True)):
      p.start()
      self.addCleanup(p.stop)
    self.y = 0.

  def teardown_method(self):
    shutil.rmtree(self.dir, ignore_errors=True)

  # -- harness ----------------------------------------------------------------
  def wire(self, obj, mode, sm=None):
    obj.params = FakeParams(mode)
    obj.mem_params = FakeMem()
    obj.pm = FakePM()
    obj.sm = sm or FakeSM(self.clock)
    if isinstance(obj, NswZoneMapData):
      obj.gps_service = GPS
    return obj

  def make(self, mode, load=True) -> NswZoneMapData:
    md = self.wire(NswZoneMapData(data_dir=self.dir, load=False), mode)
    if load:
      md._load()
      assert md.matcher is not None
    return md

  def feed(self, sm, gps_ok=True, speed=15.0, fresh_gps=True):
    """The car driving north along the road at x=0."""
    self.clock.t += 1.0
    self.y += speed
    lat, lon = latlon(0.0, self.y)
    sm.put('liveLocationKalman', gpsOK=gps_ok)
    sm.put('carState', vEgo=speed)
    sm.put('deviceMotion', **{'orientationNED.z': 0.0, 'orientationNED.valid': True})
    if fresh_gps:
      sm.put(GPS, latitude=lat, longitude=lon, bearingDeg=0.0, horizontalAccuracy=3.0, hasFix=gps_ok, speed=speed)

  def tick(self, md, **kw):
    self.feed(md.sm, **kw)
    md.tick()
    return md.pm.sent[-1].liveMapDataSP

  def until_matched(self, md):
    for _ in range(10):
      lmd = self.tick(md)
      if lmd.nswZone.state == nmd.STATE_MATCHED:
        return lmd
    raise AssertionError(f"never matched: state {md.state}, {md.result and md.result['reason']}")

  # -- mode 0 -----------------------------------------------------------------
  def test_mode_off_is_osm_map_data_byte_for_byte(self):
    osm = self.wire(OsmMapData(), MODE_OFF)
    nsw = self.make(MODE_OFF)
    nsw.sm = osm.sm
    for _ in range(4):
      self.feed(osm.sm)
      osm.tick()
      nsw.tick()
      a, b = osm.pm.sent[-1], nsw.pm.sent[-1]
      b.logMonoTime = a.logMonoTime
      assert a.to_bytes() == b.to_bytes()
    assert nsw.result is None and nsw.state == nmd.STATE_OFF

  def test_switching_to_off_stops_nsw_at_once(self):
    md = self.make(MODE_LIVE)
    self.until_matched(md)
    md.params.values["SpeedLimitNswZones"] = MODE_OFF
    lmd = self.tick(md)
    assert math.isclose(lmd.speedLimit, OSM_KPH * CV.KPH_TO_MS, rel_tol=1e-6)
    assert lmd.nswZone.state == 0 and lmd.nswZone.dataVersion == ""

  # -- mode 1 -----------------------------------------------------------------
  def test_log_only_publishes_osm_exactly(self):
    md = self.make(MODE_LOG_ONLY)
    osm = self.wire(OsmMapData(), MODE_OFF, sm=md.sm)
    for _ in range(6):
      self.feed(md.sm)
      md.tick()
      osm.tick()
      a, b = osm.pm.sent[-1].liveMapDataSP, md.pm.sent[-1].liveMapDataSP
      for f in ('speedLimitValid', 'speedLimit', 'speedLimitAheadValid', 'speedLimitAhead', 'speedLimitAheadDistance',
                'roadName'):
        assert getattr(a, f) == getattr(b, f), f
    z = md.pm.sent[-1].liveMapDataSP.nswZone
    assert z.state == nmd.STATE_MATCHED and z.mode == MODE_LOG_ONLY
    assert math.isclose(z.speedLimit, NSW_KPH * CV.KPH_TO_MS, rel_tol=1e-6)
    assert math.isclose(z.osmSpeedLimit, OSM_KPH * CV.KPH_TO_MS, rel_tol=1e-6)
    assert z.dataVersion == "test"
    assert z.zoneType == nz.TYPES.index('Default')
    assert 0. <= z.matchDistance < 5. and z.candidates >= 1

  # -- mode 2 -----------------------------------------------------------------
  def test_live_publishes_the_nsw_limit(self):
    md = self.make(MODE_LIVE)
    lmd = self.until_matched(md)
    assert lmd.speedLimitValid
    assert math.isclose(lmd.speedLimit, NSW_KPH * CV.KPH_TO_MS, rel_tol=1e-6)
    assert lmd.roadName == "Parramatta Rd", "the road name stays OSM's: the resolver's settle window reads it"
    # nothing ahead on this road: never OSM's next limit behind an NSW current one
    assert not lmd.speedLimitAheadValid and lmd.speedLimitAheadDistance == 0.

  def fake(self, md, **fields):
    r = nz._blank()
    r.update(fields)
    md.matcher = SimpleNamespace(update=lambda **kw: nz._finish(dict(r)), reset=lambda: None, data_version="fake")
    return self.tick(md)

  def test_live_states(self):
    md = self.make(MODE_LIVE)
    osm = OSM_KPH * CV.KPH_TO_MS
    lmd = self.fake(md, state='dead_reckoning', limit_kph=90, base_kph=90, dr_m=850, dr_hyps=3, variable=True,
                    ahead_kph=80, ahead_dist_m=400.0)
    assert lmd.nswZone.state == 4 and math.isclose(lmd.speedLimit, 25., rel_tol=1e-6)
    # the look-ahead along a dead-reckoned path is logged, not published (it reported limits that never came)
    assert lmd.speedLimitAhead == 0. and not lmd.speedLimitAheadValid
    assert math.isclose(lmd.nswZone.speedLimitAhead, 80 * CV.KPH_TO_MS, rel_tol=1e-6)
    assert lmd.nswZone.speedLimitAheadDistance == 400.
    assert lmd.nswZone.holdDistance == 850. and lmd.nswZone.hypotheses == 3 and lmd.nswZone.variable
    assert lmd.nswZone.matchDistance == -1. and lmd.nswZone.headingError == -1.

    lmd = self.fake(md, state='matched', limit_kph=90, base_kph=90, ahead_kph=80, ahead_dist_m=300.0, dist_m=2.5,
                    heading_err_deg=4.0, n_candidates=2)
    assert lmd.nswZone.state == 2 and math.isclose(lmd.speedLimit, 25., rel_tol=1e-6)
    assert math.isclose(lmd.speedLimitAhead, 80 * CV.KPH_TO_MS, rel_tol=1e-6) and lmd.speedLimitAheadDistance == 300.
    assert lmd.nswZone.matchDistance == 2.5 and lmd.nswZone.headingError == 4. and lmd.nswZone.candidates == 2

    lmd = self.fake(md, state='ambiguous', candidate_kph=60)
    assert lmd.nswZone.state == 3 and lmd.speedLimit == 0. and not lmd.speedLimitValid
    assert lmd.speedLimitAhead == 0. and not lmd.speedLimitAheadValid

    # OSM's limit IS NSW's best guess: two sources agreeing is published (still no look-ahead)
    lmd = self.fake(md, state='ambiguous', candidate_kph=int(OSM_KPH))
    assert lmd.nswZone.state == 3 and math.isclose(lmd.speedLimit, osm, rel_tol=1e-6)
    assert math.isclose(lmd.nswZone.speedLimit, osm, rel_tol=1e-6)
    assert lmd.speedLimitAhead == 0. and not lmd.speedLimitAheadValid

    self.clock.t += nmd.NO_MATCH_HOLD_S  # well past the last match
    for st in ('no_match', 'gps_lost'):
      lmd = self.fake(md, state=st)
      assert lmd.nswZone.state == 1 and math.isclose(lmd.speedLimit, osm, rel_tol=1e-6), st
      assert math.isclose(lmd.speedLimitAhead, 70 * CV.KPH_TO_MS, rel_tol=1e-6), "OSM's look-ahead with OSM's limit"

  def test_dead_reckoning_ended_holds_its_value_never_osm(self):
    md = self.make(MODE_LIVE)
    lmd = self.fake(md, state='dr_ended', limit_kph=70, base_kph=70, dr_m=2400, ahead_kph=60, ahead_dist_m=100.0)
    assert lmd.nswZone.state == nmd.STATE_DR_ENDED == nz.STATE_CODES['dr_ended'] == 7
    assert math.isclose(lmd.speedLimit, 70 * CV.KPH_TO_MS, rel_tol=1e-6)
    assert lmd.speedLimitAhead == 0. and not lmd.speedLimitAheadValid
    # with nothing to hold, and a dead-reckoning tick with no limit: 0, never OSM's surface street
    for st in ('dr_ended', 'dead_reckoning'):
      lmd = self.fake(md, state=st)
      assert lmd.speedLimit == 0. and not lmd.speedLimitValid, st

  def test_a_no_match_right_after_a_match_holds_the_nsw_limit(self):
    """A turn can reject every line by heading for one tick; OSM's different value used to flick through (60 -> 80 ->
    60 on the Great Western Hwy, two prompts)."""
    md = self.make(MODE_LIVE)
    osm = OSM_KPH * CV.KPH_TO_MS
    lmd = self.fake(md, state='matched', limit_kph=60, base_kph=60, ahead_kph=80, ahead_dist_m=300.0)
    assert math.isclose(lmd.speedLimit, 60 * CV.KPH_TO_MS, rel_tol=1e-6)
    for _ in range(int(nmd.NO_MATCH_HOLD_S)):  # the harness clock moves 1 s a tick
      lmd = self.fake(md, state='no_match')
      assert lmd.nswZone.state == 1 and math.isclose(lmd.speedLimit, 60 * CV.KPH_TO_MS, rel_tol=1e-6)
      assert math.isclose(lmd.nswZone.speedLimit, 60 * CV.KPH_TO_MS, rel_tol=1e-6)
      assert lmd.speedLimitAhead == 0., "a held value has no look-ahead of its own"
    lmd = self.fake(md, state='no_match')
    assert math.isclose(lmd.speedLimit, osm, rel_tol=1e-6), "only for NO_MATCH_HOLD_S"
    # GPS lost is not a no-match: no hold (fix B's freeze is the resolver's business)
    self.fake(md, state='matched', limit_kph=60, base_kph=60)
    lmd = self.fake(md, state='gps_lost')
    assert math.isclose(lmd.speedLimit, osm, rel_tol=1e-6)
    # log-only: the held value is logged, OSM published
    md.params.values["SpeedLimitNswZones"] = MODE_LOG_ONLY
    self.fake(md, state='matched', limit_kph=60, base_kph=60)
    lmd = self.fake(md, state='no_match')
    assert math.isclose(lmd.speedLimit, osm, rel_tol=1e-6)
    assert math.isclose(lmd.nswZone.speedLimit, 60 * CV.KPH_TO_MS, rel_tol=1e-6)

  def test_school_zone_code_is_carried(self):
    md = self.make(MODE_LIVE)
    lmd = self.fake(md, state='matched', limit_kph=40, base_kph=60, school_state='active', school_apply=True)
    assert lmd.nswZone.schoolZone == 2 and math.isclose(lmd.speedLimit, 40 * CV.KPH_TO_MS, rel_tol=1e-6)

  # -- failures ---------------------------------------------------------------
  def test_a_matcher_exception_publishes_osm_and_counts(self):
    md = self.make(MODE_LIVE)

    def boom(**_kw):
      raise ValueError("geometry")
    md.matcher = SimpleNamespace(update=boom, reset=lambda: None)  # ty: ignore[invalid-assignment]
    for n in range(1, 4):
      lmd = self.tick(md)
      assert lmd.nswZone.state == nmd.STATE_ERROR and lmd.nswZone.errors == n
      assert math.isclose(lmd.speedLimit, OSM_KPH * CV.KPH_TO_MS, rel_tol=1e-6)

  def test_no_data_file(self):
    shutil.rmtree(self.dir)
    md = self.make(MODE_LIVE, load=False)
    md._load()
    lmd = self.tick(md)
    assert md.matcher is None and lmd.nswZone.state == nmd.STATE_NO_DATA
    assert math.isclose(lmd.speedLimit, OSM_KPH * CV.KPH_TO_MS, rel_tol=1e-6)

  def test_a_damaged_file_is_an_error_not_a_crash(self):
    path, _ = dl.installed_index(self.dir)
    with open(path, 'r+b') as f:
      f.seek(4096)
      f.write(b'\xff' * 64)
    md = self.make(MODE_LIVE, load=False)
    md._load()
    lmd = self.tick(md)
    assert md.matcher is None and lmd.nswZone.state == nmd.STATE_ERROR
    assert math.isclose(lmd.speedLimit, OSM_KPH * CV.KPH_TO_MS, rel_tol=1e-6)
    # ... and it is set aside, so the downloader sees no data and fetches a fresh copy (it would say 'up to date')
    assert dl.read_current(self.dir) is None
    assert os.path.isfile(os.path.join(self.dir, dl.BAD_CURRENT_NAME))
    md._load()
    assert md.load_state == nmd.STATE_NO_DATA

  def test_a_failed_setup_is_osm_map_data(self):
    """Nothing after the publisher is made may raise out of the constructor (mapd_manager would build a second one)."""
    with mock.patch.object(nmd, "get_gps_location_service", side_effect=RuntimeError("no params")):
      md = NswZoneMapData(data_dir=self.dir, load=True)
    assert md.disabled and md.matcher is None
    osm = self.wire(OsmMapData(), MODE_OFF)
    md = self.wire(md, MODE_LIVE, sm=osm.sm)
    for _ in range(3):
      self.feed(osm.sm)
      osm.tick()
      md.tick()
      a, b = osm.pm.sent[-1], md.pm.sent[-1]
      b.logMonoTime = a.logMonoTime
      assert a.to_bytes() == b.to_bytes()

  def test_stale_alert(self):
    md = self.make(MODE_LIVE)
    md.data_version = "2026-09-01"
    md.calendar_end = datetime.date(2028, 1, 28)
    assert md.stale_alert(datetime.date(2026, 10, 1)) is None
    assert "days old" in (md.stale_alert(datetime.date(2026, 12, 1)) or "")
    assert "calendar ends 2028-01-28" in (md.stale_alert(datetime.date(2027, 12, 1)) or "")
    md.params.values["SpeedLimitNswZones"] = MODE_OFF
    self.tick(md)
    assert md.stale_alert(datetime.date(2027, 12, 1)) is None

  def test_the_offroad_alert_is_written_only_on_a_change(self):
    from openpilot.sunnypilot.mapd import mapd_manager as mm
    calls = []
    text = ["old data"]
    fake = SimpleNamespace(stale_alert=lambda today: text[0])
    with mock.patch.object(mm, "set_offroad_alert", lambda *a: calls.append(a)), \
         mock.patch("openpilot.common.time_helpers.system_time_valid", lambda: True):
      shown = mm.update_nsw_alert(fake, "")
      shown = mm.update_nsw_alert(fake, shown)
      assert shown == "old data" and calls == [("Offroad_NswZonesStale", True, "old data")]
      text[0] = None
      shown = mm.update_nsw_alert(fake, shown)
      assert shown == "" and calls[-1] == ("Offroad_NswZonesStale", False, None)
    with mock.patch.object(mm, "set_offroad_alert", lambda *a: calls.append(a)), \
         mock.patch("openpilot.common.time_helpers.system_time_valid", lambda: False):
      assert mm.update_nsw_alert(fake, "kept") == "kept", "no valid clock: no change"

  def test_reload_picks_up_a_new_install(self):
    md = self.make(MODE_LIVE, load=False)
    md.reload()
    md._loader.join(30)
    assert md.matcher is not None and md.data_version == "test"

  # -- inputs -----------------------------------------------------------------
  def test_inputs(self):
    md = self.make(MODE_LIVE)
    self.feed(md.sm)
    kw = md.matcher_inputs()
    assert kw['gps_ok'] and kw['lat'] is not None and kw['h_accuracy_m'] == 3.0
    assert kw['speed_mps'] == 15.0 and kw['yaw_deg'] == 0.0 and kw['mono_time'] == self.clock.t

    self.feed(md.sm, gps_ok=False)  # locationd says GPS is not OK / no fix
    kw = md.matcher_inputs()
    assert not kw['gps_ok'] and kw['lat'] is None and kw['bearing_deg'] is None

    self.feed(md.sm)
    self.clock.t += 2.0  # the last fix is 2 s old
    assert not md.matcher_inputs()['gps_ok']

    self.feed(md.sm)
    md.sm.put('deviceMotion', **{'orientationNED.valid': False})
    assert md.matcher_inputs()['yaw_deg'] is None
    md.sm.put('deviceMotion', **{'orientationNED.z': math.pi / 2, 'orientationNED.valid': True})
    assert math.isclose(md.matcher_inputs()['yaw_deg'], 90.0, rel_tol=1e-5)  # Float32

    # OffroadMode: no carState while driving - the GPS speed drives the odometry
    md.sm.seen['carState'] = False
    md.sm.put(GPS, speed=12.5)
    assert md.matcher_inputs()['speed_mps'] == 12.5

  def test_publishing_states_are_the_matchers(self):
    assert nz.STATE_CODES['matched'] == nmd.STATE_MATCHED and nz.STATE_CODES['dead_reckoning'] == nmd.STATE_DEAD_RECKONING
    assert nz.STATE_CODES['dr_ended'] == nmd.STATE_DR_ENDED
    assert nz.STATE_CODES['ambiguous'] == nmd.STATE_AMBIGUOUS and nz.STATE_CODES['no_match'] == nmd.STATE_NO_MATCH

  def test_capnp_field_is_appended(self):
    """LiveMapDataSP @0-@5 are upstream's; nswZone is @6, and the NswZone fields keep their ordinals."""
    from openpilot.cereal import custom
    fields = custom.LiveMapDataSP.schema.fields
    assert fields['nswZone'].proto.ordinal.explicit == 6
    assert fields['roadName'].proto.ordinal.explicit == 5
    nsw = custom.LiveMapDataSP.NswZone.schema.fields
    names = [f for f, _ in sorted(nsw.items(), key=lambda kv: kv[1].proto.ordinal.explicit)]
    assert names[:13] == ['state', 'speedLimit', 'speedLimitAhead', 'speedLimitAheadDistance', 'zoneType', 'schoolZone',
                          'matchDistance', 'headingError', 'candidates', 'dataVersion', 'osmSpeedLimit', 'errors',
                          'holdDistance']

