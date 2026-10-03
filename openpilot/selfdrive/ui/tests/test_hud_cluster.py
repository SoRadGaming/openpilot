"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 HUD's rules, settings, sunnylink items and markers - everything that needs no window. The
pixels are tested in test_hud_render.py (a headless raylib child process).

hud_model.py and hud_settings.py import no raylib, so they are imported here; the drawing files are only parsed.
"""
import dataclasses
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (HudFrame, StandstillBanner, build_frame, next_window_m,
                                                                     pending_limit, short_line2, LEAD_DEPART_S,
                                                                     MAP_EXTRAPOLATE_MAX_S, SCHOOL_ACTIVE,
                                                                     SCHOOL_INACTIVE, SCHOOL_NONE)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model as HM

ROOT = Path(__file__).parents[3]   # openpilot/
REPO = ROOT.parent
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PARAMS_KEYS = ROOT / "common/params_keys.h"
ROAD_VIEW = ROOT / "selfdrive/ui/mici/onroad/augmented_road_view.py"
BALL = ROOT / "selfdrive/ui/mici/onroad/confidence_ball.py"
ALERTS = ROOT / "selfdrive/ui/mici/onroad/alert_renderer.py"
MICI_SETTINGS = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/settings.py"
BOARD = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/board.py"
VISUALS_YAML = ROOT / "sunnypilot/sunnylink/settings_ui_src/pages/visuals.yaml"
SETTINGS_JSON = ROOT / "sunnypilot/sunnylink/settings_ui.json"
GATEWAY_ICON = ROOT / "sunnypilot/selfdrive/assets/icons_mici/gateway.png"
KPH = 3.6

DEFAULTS = {HS.PARAM_SPEED_CLUSTER: True, HS.PARAM_NEXT_LIMIT: HS.NEXT_BOTH, HS.PARAM_SCHOOL_CUE: True,
            HS.PARAM_VARIABLE_SIGN: True, HS.PARAM_STOPPED_TIMER: True, HS.PARAM_STOPPED_BANNER: True,
            HS.PARAM_CONFIRM_LIMIT: True, HS.PARAM_PLANNED_STOP: True, HS.PARAM_CURVE: True}


# ------------------------------------------------------------------------------------------- a SubMaster stand-in
class FakeSM:
  """Real capnp messages, SubMaster's bookkeeping (seen / alive / recv_frame) by hand."""
  def __init__(self):
    self.data = {}
    self.seen = defaultdict(bool)
    self.alive = defaultdict(bool)
    self.recv_frame = defaultdict(int)

  def __getitem__(self, svc):
    if svc not in self.data:
      self.data[svc] = getattr(messaging.new_message(svc), svc)
    return self.data[svc]

  def put(self, svc, frame=10, alive=True):
    self.seen[svc] = True
    self.alive[svc] = alive
    self.recv_frame[svc] = frame
    return self[svc]


def drive(v=110 / KPH, limit=110, valid=True, last_valid=True, source='map', ahead=100, ahead_dist=339.0,
          ahead_valid=True, nsw_mode=2, nsw_state=2, nsw_limit=None, school=0, variable=False, standstill=False,
          v_cluster=None, lp=True, lmd=True, lmd_alive=True, lp_alive=True, offset=0):
  sm = FakeSM()
  cs = sm.put('carState')
  cs.vEgo = v
  cs.vEgoCluster = v if v_cluster is None else v_cluster
  cs.standstill = standstill
  if lp:
    res = sm.put('longitudinalPlanSP', alive=lp_alive).speedLimit.resolver
    res.speedLimit = res.speedLimitLast = limit / KPH
    res.speedLimitOffset = offset / KPH
    res.speedLimitFinal = res.speedLimitFinalLast = (limit + offset) / KPH
    res.speedLimitValid = valid
    res.speedLimitLastValid = last_valid
    res.source = source
  if lmd:
    m = sm.put('liveMapDataSP', alive=lmd_alive)
    m.speedLimitValid = True
    m.speedLimit = limit / KPH
    m.speedLimitAheadValid = ahead_valid
    m.speedLimitAhead = ahead / KPH
    m.speedLimitAheadDistance = ahead_dist
    z = m.nswZone
    z.mode = nsw_mode
    z.state = nsw_state
    z.speedLimit = (limit if nsw_limit is None else nsw_limit) / KPH
    z.schoolZone = school
    z.variable = variable
  return sm


def frame(sm, settings=HS.ALL_ON, *, metric=True, sl_mode_on=True, stopped_s=None, cluster_seen=True, max_visible=False,
          started_frame=5, map_age_s=0.0) -> HudFrame:
  return build_frame(sm, settings, started_frame=started_frame, is_metric=metric, speed_limit_mode_on=sl_mode_on,
                     stopped_s=stopped_s, v_ego_cluster_seen=cluster_seen, max_visible=max_visible, map_age_s=map_age_s)


def with_(**kw) -> HS.HudSettings:
  return dataclasses.replace(HS.ALL_ON, **kw)


class FakeParams:
  def __init__(self, values=None, fail=False):
    self.v = dict(values or {})
    self.fail = fail

  def get(self, key, block=False, return_default=False):
    if self.fail:
      raise RuntimeError("UnknownKeyName")
    if key in self.v:
      return self.v[key]
    return DEFAULTS.get(key) if return_default else None


# ------------------------------------------------------------------------------------------- params
class TestHudParams(OpenpilotTestCase):
  def test_every_setting_is_registered_backed_up_and_on_by_default(self):
    keys = PARAMS_KEYS.read_text()
    for key in HS.ALL_PARAMS:
      m = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}\}', keys)
      assert m, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
      flags = m.group(1)
      assert "PERSISTENT" in flags and "BACKUP" in flags, f"{key}: a setting, kept and backed up"
      if key == HS.PARAM_NEXT_LIMIT:
        assert "INT" in flags and '"3"' in flags, "the next limit defaults to both (bar and distance)"
      else:
        assert "BOOL" in flags and '"1"' in flags, f"{key} defaults to on"

  def test_the_built_params_library_knows_them(self):
    params = Params()
    for key, value in DEFAULTS.items():
      assert params.get(key, return_default=True) == value, key
    assert HS.read_settings(params) == HS.ALL_ON

  def test_each_param_switches_only_its_piece(self):
    params = Params()
    fields = {HS.PARAM_SPEED_CLUSTER: "speed_cluster", HS.PARAM_SCHOOL_CUE: "school_cue",
              HS.PARAM_VARIABLE_SIGN: "variable_sign", HS.PARAM_STOPPED_TIMER: "stopped_timer",
              HS.PARAM_STOPPED_BANNER: "stopped_banner", HS.PARAM_CONFIRM_LIMIT: "confirm_limit",
              HS.PARAM_PLANNED_STOP: "planned_stop", HS.PARAM_CURVE: "curve"}
    assert set(fields) | {HS.PARAM_NEXT_LIMIT} == set(HS.ALL_PARAMS), "every setting is switched here"
    for key, field in fields.items():
      params.put_bool(key, False, block=True)
      s = HS.read_settings(params)
      assert getattr(s, field) is False
      assert s == with_(**{field: False}), key
      params.put_bool(key, True, block=True)
    for mode in HS.NEXT_MODES:
      params.put(HS.PARAM_NEXT_LIMIT, mode, block=True)
      assert HS.read_settings(params).next_limit == mode

  def test_unreadable_params_draw_the_stock_screen(self):
    # a build whose params library predates the keys: everything off, not half a HUD
    assert HS.read_settings(FakeParams(fail=True)) == HS.ALL_OFF

  def test_a_next_limit_value_sunnylink_never_writes_is_the_default(self):
    assert HS.read_settings(FakeParams({HS.PARAM_NEXT_LIMIT: 7})).next_limit == HS.NEXT_BOTH

  def test_params_are_read_at_most_once_a_second(self):
    t = [0.0]
    params = FakeParams()
    r = HS.HudSettingsReader(params_fn=lambda: params, clock=lambda: t[0])
    assert r.get() == HS.ALL_ON and r.reads == 1
    params.v[HS.PARAM_SPEED_CLUSTER] = False
    for dt in (0.016, 0.5, 0.99):
      t[0] = dt
      assert r.get().speed_cluster and r.reads == 1, "re-read inside a second"
    t[0] = 1.0
    assert not r.get().speed_cluster and r.reads == 2
    params.v[HS.PARAM_SPEED_CLUSTER] = True
    assert r.get(force=True).speed_cluster and r.reads == 3

  def test_the_reader_starts_with_everything_off(self):
    # before its first read (the first frame) nothing is drawn
    assert HS.HudSettingsReader(params_fn=FakeParams).settings == HS.ALL_OFF


# ------------------------------------------------------------------------------------------- the cluster's rules
class TestClusterRules(OpenpilotTestCase):
  def test_cruise(self):
    f = frame(drive())
    assert (f.speed, f.limit, f.limit_held, f.electronic, f.school, f.sign_slot) == (110, 110, False, False, 0, True)
    assert f.visible and f.timer_s is None

  def test_speed_is_the_cars_speedometer_once_it_has_been_seen(self):
    sm = drive(v=30.0, v_cluster=29.0)
    assert frame(sm, cluster_seen=True).speed == round(29.0 * KPH)
    assert frame(sm, cluster_seen=False).speed == round(30.0 * KPH)

  def test_next_lower_limit_within_the_window(self):
    f = frame(drive(v=30.3))
    assert f.next_limit == 100 and f.next_dist == 339.0
    assert abs(f.next_frac - 339.0 / (30.3 * 15)) < 1e-3
    assert f.next_mode == HS.NEXT_BOTH

  def test_next_higher_or_equal_limit_is_hidden(self):
    assert frame(drive(ahead=120)).next_limit == 0
    assert frame(drive(ahead=110)).next_limit == 0

  def test_next_limit_outside_15_s_or_500_m_is_hidden(self):
    assert next_window_m(0.0) == 15.0 and next_window_m(10.0) == 150.0 and next_window_m(40.0) == 500.0
    assert frame(drive(v=40.0, ahead_dist=510.0)).next_limit == 0
    assert frame(drive(v=40.0, ahead_dist=499.0)).next_limit == 100
    assert frame(drive(v=10.0, ahead_dist=160.0)).next_limit == 0
    assert frame(drive(v=10.0, ahead_dist=140.0)).next_limit == 100
    assert frame(drive(ahead_dist=0.0)).next_limit == 0

  def test_next_limit_modes(self):
    for mode in (HS.NEXT_BAR, HS.NEXT_TEXT, HS.NEXT_BOTH):
      f = frame(drive(), with_(next_limit=mode))
      assert f.next_limit == 100 and f.next_mode == mode
    assert frame(drive(), with_(next_limit=HS.NEXT_OFF)).next_limit == 0

  def test_no_next_limit_at_a_standstill(self):
    assert frame(drive(v=0.0, standstill=True, ahead_dist=10.0)).next_limit == 0

  def test_next_limit_only_from_what_mapd_published(self):
    # dead reckoning in a tunnel: mapd publishes no next limit; nswZone keeps its own look-ahead for the logs only
    sm = drive(ahead_valid=False, ahead=0, ahead_dist=0.0)
    z = sm['liveMapDataSP'].nswZone
    z.speedLimitAhead = 60 / KPH
    z.speedLimitAheadDistance = 250.0
    assert frame(sm).next_limit == 0

  def test_next_limit_only_while_the_limit_on_screen_is_from_the_map(self):
    # as sunnypilot's own sign (_draw_ahead_info): a car-sourced limit is never paired with a map look-ahead
    assert frame(drive(source='map')).next_limit == 100
    for source in ('car', 'none'):
      f = frame(drive(source=source))
      assert f.limit == 110 and f.next_limit == 0, source

  def test_the_distance_runs_down_between_map_messages(self):
    # liveMapDataSP is 1 Hz: between messages the distance shrinks with vEgo (not the speedometer), for one period
    sm = drive(v=30.0, v_cluster=31.0, ahead_dist=339.0)
    assert frame(sm, map_age_s=0.0).next_dist == 339.0
    assert abs(frame(sm, map_age_s=0.5).next_dist - (339.0 - 15.0)) < 1e-3
    assert abs(frame(sm, map_age_s=MAP_EXTRAPOLATE_MAX_S).next_dist - 309.0) < 1e-3
    assert abs(frame(sm, map_age_s=5.0).next_dist - 309.0) < 1e-3, "a late message: held, not run down further"
    assert frame(sm, map_age_s=-1.0).next_dist == 339.0
    f0, f1 = frame(sm, map_age_s=0.0), frame(sm, map_age_s=0.5)
    assert f1.next_frac < f0.next_frac, "the bar shrinks in between"
    # passed the sign before the next message: gone, not drawn at 0 m
    assert frame(drive(v=30.0, ahead_dist=20.0), map_age_s=1.0).next_limit == 0

  def test_school_zone(self):
    assert frame(drive(limit=40, school=2)).school == SCHOOL_ACTIVE
    assert frame(drive(limit=50, school=1)).school == SCHOOL_INACTIVE
    assert frame(drive(limit=50, school=3)).school == SCHOOL_NONE, "unknown: nothing is published, nothing is cued"
    assert frame(drive(limit=40, school=2), with_(school_cue=False)).school == SCHOOL_NONE
    assert frame(drive(limit=40, school=2)).limit == 40, "the sign keeps its shape and shows the school limit"

  def test_school_and_variable_cues_need_the_nsw_limit_on_screen(self):
    # log only: the shown limit is OSM's
    assert frame(drive(limit=40, school=2, nsw_mode=1)).school == SCHOOL_NONE
    assert not frame(drive(limit=80, variable=True, nsw_mode=1)).electronic
    # the resolver holds another value than the one NSW published
    assert frame(drive(limit=50, school=2, nsw_limit=40)).school == SCHOOL_NONE
    assert not frame(drive(limit=80, variable=True, nsw_limit=60)).electronic
    # a car-sourced limit
    assert not frame(drive(limit=80, variable=True, source='car')).electronic
    # nswZone not matched: 0 off, 1 no match (OSM's value), 3 ambiguous (0), 5 error, 6 no data file
    for state in (0, 1, 3, 5, 6):
      assert frame(drive(limit=40, school=2, nsw_state=state)).school == SCHOOL_NONE, state
      assert not frame(drive(limit=80, variable=True, nsw_state=state)).electronic, state
    # dead reckoning publishes the tunnel line's limit: still the NSW limit on screen
    assert frame(drive(limit=80, variable=True, nsw_state=4)).electronic
    assert frame(drive(limit=40, school=2, nsw_state=4)).school == SCHOOL_ACTIVE

  def test_variable_zone_is_the_electronic_sign(self):
    assert frame(drive(limit=80, variable=True)).electronic
    assert not frame(drive(limit=80, variable=True), with_(variable_sign=False)).electronic
    assert not frame(drive(limit=80, variable=False)).electronic

  def test_a_held_limit(self):
    f = frame(drive(valid=False, last_valid=True))
    assert f.limit == 110 and f.limit_held
    assert frame(drive(valid=False, last_valid=False)).limit == 0

  def test_speed_limit_mode_off(self):
    f = frame(drive(), sl_mode_on=False)
    assert f.limit == 0 and not f.sign_slot and f.speed == 110 and f.next_limit == 0

  def test_stop_timer(self):
    sm = drive(v=0.0, standstill=True)
    assert frame(sm, stopped_s=0.5).speed == 0 and frame(sm, stopped_s=0.5).timer_s is None, "a second first"
    f = frame(sm, stopped_s=29.9)
    assert f.timer_s == 29.9 and f.speed is None and f.limit == 110
    f = frame(sm, with_(stopped_timer=False), stopped_s=29.9)
    assert f.timer_s is None and f.speed == 0
    f = frame(sm, with_(speed_cluster=False), stopped_s=29.9)
    assert f.timer_s == 29.9 and f.limit == 0 and not f.sign_slot, "the timer alone, without the cluster"
    assert frame(drive(), with_(speed_cluster=False), stopped_s=None).visible is False

  def test_the_stock_max_number_hides_the_speed_not_the_sign(self):
    f = frame(drive(), max_visible=True)
    assert f.speed is None and f.limit == 110

  def test_missing_messages(self):
    # no carState this drive: nothing
    sm = drive()
    sm.seen['carState'] = False
    assert not frame(sm).visible
    sm = drive()
    sm.recv_frame['carState'] = 2  # from before this drive started (started_frame 5)
    assert not frame(sm).visible
    # no plan: the speed alone
    f = frame(drive(lp=False))
    assert f.speed == 110 and f.limit == 0 and f.next_limit == 0
    f = frame(drive(lp_alive=False))
    assert f.speed == 110 and f.limit == 0
    # no map data: the plain sign, no next limit, no cue
    f = frame(drive(lmd=False, limit=40))
    assert f.limit == 40 and f.next_limit == 0 and f.school == SCHOOL_NONE and not f.electronic
    f = frame(drive(lmd_alive=False, limit=80, variable=True))
    assert f.limit == 80 and not f.electronic and f.next_limit == 0

  def test_all_off_draws_nothing(self):
    for sm, kw in ((drive(), {}), (drive(v=0.0, standstill=True), {"stopped_s": 30.0}),
                   (drive(limit=40, school=2), {}), (drive(limit=80, variable=True), {})):
      assert not frame(sm, HS.ALL_OFF, **kw).visible

  def test_imperial(self):
    f = frame(drive(v=30.0, limit=100, ahead=90, ahead_dist=300.0), metric=False)
    assert f.speed == round(30.0 * 2.23694) and f.limit == round(100 / KPH * 2.23694) and f.next_limit == 56


# ------------------------------------------------------------------------------------------- the right rail's rules
def stopping_plan(stop_m: float | None, v0: float = 8.0):
  """modelV2's 33-point plan (velocity.x, position.x at T_IDXS): a steady brake from v0 to a stop stop_m ahead, or
  cruising at v0 (None)."""
  vx, px = [], []
  a = v0 ** 2 / (2 * stop_m) if stop_m else 0.0
  for t in HM.T_IDXS:
    if a and t >= v0 / a:
      vx.append(0.0)
      px.append(stop_m)
    else:
      vx.append(v0 - a * t)
      px.append(v0 * t - a * t * t / 2)
  return vx, px


def rail_sm(stop_m=None, v=50 / KPH, standstill=False, lead_d=None, long_active=True, src='e2e', scc=None, scc_src=None,
            v_target=37 / KPH, map_turning=False, yaw=-0.1, plan_v0=8.0, missing=()):
  """Every message the rail reads, this drive's and arriving; `missing` leaves services out. The car at 50 km/h, so the
  default 37 km/h curve target is one it is slowing for."""
  sm = FakeSM()
  if 'carState' not in missing:
    cs = sm.put('carState')
    cs.vEgo = cs.vEgoCluster = v
    cs.standstill = standstill
  if 'modelV2' not in missing:
    m = sm.put('modelV2')
    m.velocity.x, m.position.x = stopping_plan(stop_m, plan_v0)
    m.orientationRate.z = [yaw * min(1.0, t) for t in HM.T_IDXS]
  if 'radarState' not in missing:
    lead = sm.put('radarState').leadOne
    if lead_d is not None:
      lead.present, lead.dRel = True, lead_d
  if 'carControl' not in missing:
    sm.put('carControl').longActive = long_active
  if 'longitudinalPlan' not in missing:
    sm.put('longitudinalPlan').longitudinalPlanSource = src
  if 'longitudinalPlanSP' not in missing:
    p = sm.put('longitudinalPlanSP')
    if scc is not None:
      p.smartCruiseControl.vision.state = scc
      p.smartCruiseControl.vision.active = scc in ('entering', 'turning', 'leaving')
      p.smartCruiseControl.vision.vTarget = v_target
    if map_turning:
      p.smartCruiseControl.map.state = 'turning'
      p.smartCruiseControl.map.active = True
      p.smartCruiseControl.map.vTarget = v_target
    p.longitudinalPlanSource = scc_src or ('sccVision' if scc else 'sccMap' if map_turning else 'cruise')
  return sm


class Rail:
  """A RailState driven at 20 Hz on a sim clock, as HudRail drives it."""
  def __init__(self, settings=HS.ALL_ON, metric=True):
    self.s, self.t, self.settings, self.metric = HM.RailState(), 100.0, settings, metric

  def step(self, sm, seconds=0.05) -> HM.RailFrame:
    f = HM.RailFrame()
    for _ in range(max(1, int(round(seconds / 0.05)))):
      self.t += 0.05
      f = self.s.update(sm, self.settings, started_frame=STARTED, now=self.t, is_metric=self.metric)
    return f

  def frames(self, sm, seconds) -> list[HM.RailFrame]:
    return [self.step(sm) for _ in range(int(round(seconds / 0.05)))]


STARTED = 5  # the drive's started_frame; FakeSM.put() receives at frame 10


class TestRailRules(OpenpilotTestCase):
  def test_where_the_plan_stops(self):
    vx, px = stopping_plan(25.0)
    m = rail_sm(stop_m=25.0)['modelV2']
    d = HM.plan_stop_m(m)
    i = next(k for k, v in enumerate(vx) if v < HM.STOP_V_MS)
    assert d is not None and d == px[i] and 22.0 < d <= 25.0, "position.x at the first point under 0.5 m/s"
    assert HM.plan_stop_m(rail_sm(stop_m=None)['modelV2']) is None, "cruising: no stop"
    # a stop past the 10 s horizon is not in the plan; a short or empty plan is no plan
    assert HM.plan_stop_m(rail_sm(stop_m=200.0, plan_v0=25.0)['modelV2']) is None
    sm = rail_sm(stop_m=25.0)
    sm['modelV2'].velocity.x = [0.0] * 5
    assert HM.plan_stop_m(sm['modelV2']) is None
    assert HM.plan_stop_m(FakeSM()['modelV2']) is None

  def test_the_stop_shows_after_the_debounce_with_its_distance(self):
    r, sm = Rail(), rail_sm(stop_m=25.0)
    early = r.frames(sm, HM.RAIL_ON_S)
    assert all(f.kind == HM.RAIL_NONE for f in early), "not before it has held 0.3 s"
    f, d = r.step(sm), HM.plan_stop_m(sm['modelV2'])
    assert d is not None and f.kind == HM.RAIL_STOP and abs(f.stop_m - d) < 1e-6
    assert HM.fmt_stop_dist(f.stop_m, True) == (f.num, f.unit) == ("25", "m")
    f = Rail(metric=False).step(sm, 1.0)
    assert (f.num, f.unit) == HM.fmt_stop_dist(d, False) == ("80", "ft"), "feet when the comma is set to imperial"

  def test_solid_only_while_openpilot_drives_the_speed_on_the_plan(self):
    assert Rail().step(rail_sm(stop_m=25.0), 1.0).solid, "longActive and the e2e plan in control: white"
    assert not Rail().step(rail_sm(stop_m=25.0, long_active=False), 1.0).solid, "disengaged: grey, the model's plan"
    for src in ('cruise', 'lead0'):
      f = Rail().step(rail_sm(stop_m=25.0, src=src), 1.0)
      assert f.kind == HM.RAIL_STOP and not f.solid, f"{src} in control: grey"
    for missing in ('carControl', 'longitudinalPlan'):
      f = Rail().step(rail_sm(stop_m=25.0, missing=(missing,)), 1.0)
      assert f.kind == HM.RAIL_STOP and not f.solid, f"no {missing}: still the plan, but never white"

  def test_braking_out_of_it_greys_it_on_the_frame(self):
    r = Rail()
    assert r.step(rail_sm(stop_m=25.0), 1.0).solid
    f = r.step(rail_sm(stop_m=25.0, long_active=False))
    assert f.kind == HM.RAIL_STOP and not f.solid

  def test_the_plan_source_settles_before_it_counts(self):
    r = Rail()
    assert r.step(rail_sm(stop_m=25.0), 1.0).solid
    blip = [r.step(rail_sm(stop_m=25.0, src='cruise')).solid for _ in range(round(HM.RAIL_OFF_S / 0.05) - 1)]
    assert all(blip), "a moment of another source: still white"
    assert not r.step(rail_sm(stop_m=25.0, src='cruise'), 0.1).solid, "held: grey"
    back = [r.step(rail_sm(stop_m=25.0)).solid for _ in range(round(HM.RAIL_ON_S / 0.05) + 1)]
    assert not back[0] and back[-1], "white again once e2e has held 0.3 s"

  def test_a_car_ahead_inside_the_stop_hides_it(self):
    # the plan stops behind a car: the Accord's own HUD shows the car
    assert Rail().step(rail_sm(stop_m=25.0, lead_d=20.0), 1.0).kind == HM.RAIL_NONE
    assert Rail().step(rail_sm(stop_m=25.0, lead_d=25.0 + HM.LEAD_MARGIN_M - 1), 1.0).kind == HM.RAIL_NONE
    assert Rail().step(rail_sm(stop_m=25.0, lead_d=25.0 + HM.LEAD_MARGIN_M + 5), 1.0).kind == HM.RAIL_STOP, \
      "a car well past the stop line: the stop is not for it"
    sm = rail_sm(stop_m=25.0, lead_d=10.0)
    sm['radarState'].leadOne.present = False
    assert Rail().step(sm, 1.0).kind == HM.RAIL_STOP, "a lead slot the radar does not mark present is no car"

  def test_a_lead_appearing_hides_it_after_the_debounce(self):
    r = Rail()
    assert r.step(rail_sm(stop_m=25.0), 1.0).kind == HM.RAIL_STOP
    held = r.frames(rail_sm(stop_m=25.0, lead_d=15.0), HM.RAIL_OFF_S - 0.05)
    assert all(f.kind == HM.RAIL_STOP for f in held), "drawn with its last distance while it settles"
    assert r.step(rail_sm(stop_m=25.0, lead_d=15.0), 0.1).kind == HM.RAIL_NONE

  def test_a_shown_stop_is_hidden_only_by_a_car_inside_it_plus_5m(self):
    d = HM.plan_stop_m(rail_sm(stop_m=25.0)['modelV2'])
    assert d is not None
    r = Rail()
    assert r.step(rail_sm(stop_m=25.0), 1.0).kind == HM.RAIL_STOP
    # a car 8 m past the plan's stop: it would have kept a stop from appearing (+10 m), but it does not hide one
    assert all(f.kind == HM.RAIL_STOP for f in r.frames(rail_sm(stop_m=25.0, lead_d=d + 8.0), 2.0))
    # a car inside the stop + 5 m: the plan stops behind it - gone after the debounce ...
    assert r.step(rail_sm(stop_m=25.0, lead_d=d + HM.LEAD_KEEP_M - 1.0), HM.RAIL_OFF_S + 0.05).kind == HM.RAIL_NONE
    # ... and back only once no car is inside the stop + 10 m again
    assert all(f.kind == HM.RAIL_NONE for f in r.frames(rail_sm(stop_m=25.0, lead_d=d + 8.0), 2.0))
    assert r.step(rail_sm(stop_m=25.0, lead_d=d + HM.LEAD_MARGIN_M + 2.0), HM.RAIL_ON_S + 0.05).kind == HM.RAIL_STOP

  def test_a_parked_car_past_the_line_does_not_blink_the_countdown(self):
    # route 110, t 406-414, engaged: the plan stopped 40 m ahead with nothing in front; from 7 m out a stationary car
    # 8-12 m past the stop came and went on the radar, in and out of the stop + 10 m. The countdown went 7 m, blank for
    # 0.65 s, 4 m. Now it counts down to the last metre without a gap.
    r, figures = Rail(), []
    r.step(rail_sm(stop_m=40.0), 1.0)
    for i, stop in enumerate(x / 2 for x in range(14, 2, -1)):   # 7.0 .. 1.5 m
      gap = 8.0 + 4.0 * (i % 3) / 2                               # the car 8, 10, 12 m past the stop
      for lead in (stop + gap, None, stop + gap):
        f = r.step(rail_sm(stop_m=stop, lead_d=lead, plan_v0=3.0), 0.1)
        assert f.kind == HM.RAIL_STOP, (stop, lead)
        figures.append(int(f.num))
    assert figures == sorted(figures, reverse=True) and figures[-1] <= 2, figures

  def test_no_flicker(self):
    r = Rail()
    # the plan dips under 0.5 m/s for a moment, four times: never shown
    for _ in range(4):
      assert all(f.kind == HM.RAIL_NONE for f in r.frames(rail_sm(stop_m=25.0), HM.RAIL_ON_S - 0.1))
      r.frames(rail_sm(stop_m=None), 0.2)
    # shown, then the plan stops reaching zero for a moment, four times: never hidden
    r.step(rail_sm(stop_m=25.0), 1.0)
    for _ in range(4):
      assert all(f.kind == HM.RAIL_STOP for f in r.frames(rail_sm(stop_m=None), HM.RAIL_OFF_S - 0.1))
      assert all(f.kind == HM.RAIL_STOP for f in r.frames(rail_sm(stop_m=25.0), 0.2))
    assert r.step(rail_sm(stop_m=None), HM.RAIL_OFF_S + 0.05).kind == HM.RAIL_NONE, "for good: gone"

  def test_hidden_at_once_at_a_standstill_and_under_a_metre(self):
    r = Rail()
    assert r.step(rail_sm(stop_m=25.0), 1.0).kind == HM.RAIL_STOP
    assert r.step(rail_sm(stop_m=25.0, standstill=True, v=0.0)).kind == HM.RAIL_NONE, "stopped: the stopwatch has it"
    r = Rail()
    assert r.step(rail_sm(stop_m=25.0), 1.0).kind == HM.RAIL_STOP
    assert r.step(rail_sm(stop_m=0.8)).kind == HM.RAIL_NONE, "the stop is here"
    assert Rail().step(rail_sm(stop_m=0.8), 1.0).kind == HM.RAIL_NONE

  def test_the_distance_follows_the_plan(self):
    r = Rail()
    r.step(rail_sm(stop_m=40.0), 1.0)
    d = [r.step(rail_sm(stop_m=x)).stop_m for x in (38.0, 36.0, 34.0, 32.0) + (30.0,) * 11]
    assert all(b <= a for a, b in zip(d, d[1:], strict=False)), "it counts down"
    target = HM.plan_stop_m(rail_sm(stop_m=30.0)['modelV2'])
    assert target is not None and abs(d[-1] - target) < 0.3, "and settles on the plan's own number within half a second"

  def test_the_figure_counts_up_only_for_5m_or_more(self):
    # the queue on route 10f (t 2676-2678): the plan's stop went 10-9-10-9, then 8-7-6-7-8 m
    assert [HM.stop_figure(d, p)[0] for d, p in ((9.0, 10.0), (10.0, 9.0), (14.9, 10.0), (15.0, 10.0), (20.0, None))] == \
      [9.0, 9.0, 10.0, 15.0, 20.0]
    assert HM.stop_figure(15.0, 10.0) == (15.0, True) and HM.stop_figure(15.5, 15.0, True) == (15.5, True), \
      "after a jump it follows the distance all the way up ..."
    assert HM.stop_figure(15.4, 15.5, True) == (15.4, False) and HM.stop_figure(15.6, 15.4) == (15.4, False), \
      "... and counts down again once it stops rising"
    r, figures = Rail(), []
    for stop in (10.0, 9.0, 10.0, 9.0, 8.0, 7.0, 6.0, 7.0, 8.0, 7.5):
      figures.append(r.step(rail_sm(stop_m=stop, plan_v0=4.0), 0.5).num)
    assert figures == ["10", "9", "9", "9", "8", "7", "6", "6", "6", "6"], figures
    f = r.step(rail_sm(stop_m=14.0, plan_v0=4.0), 1.0)
    assert f.num == HM.fmt_stop_dist(HM.plan_stop_m(rail_sm(stop_m=14.0, plan_v0=4.0)['modelV2']) or 0, True)[0], \
      "a stop 5 m or more further than shown is news: it counts up"

  def test_a_curve(self):
    f = Rail().step(rail_sm(scc='entering'), 1.0)
    assert f.kind == HM.RAIL_CURVE and abs(f.curve_v - 37 / KPH) < 1e-4 and f.curve_left
    assert (f.num, f.unit) == ("35", "km/h"), "5 km/h steps"
    f = Rail().step(rail_sm(scc='turning', yaw=0.1), 1.0)
    assert f.kind == HM.RAIL_CURVE and not f.curve_left, "a positive yaw rate (z down) is a right curve"
    f = Rail(metric=False).step(rail_sm(scc='entering', v_target=38 / KPH), 1.0)
    assert (f.num, f.unit) == ("25", "mph"), "38 km/h = 23.6 mph: 5 mph steps"

  def test_a_curve_only_while_the_car_is_slowing_for_it(self):
    # route 10f t 2050: the motorway, a lane change, smart cruise control limiting with a target AT or over the car's speed ('87 km/h'
    # at 83 km/h, accelerating) - nothing to slow for, nothing shown
    for target in (83, 84, 87, 89):
      assert Rail().step(rail_sm(scc='entering', v=83 / KPH, v_target=target / KPH), 2.0).kind == HM.RAIL_NONE, target
    # it appears 2 km/h under the car's speed ...
    r = Rail()
    assert r.step(rail_sm(scc='entering', v=50 / KPH, v_target=49 / KPH), 1.0).kind == HM.RAIL_NONE
    assert r.step(rail_sm(scc='entering', v=50 / KPH, v_target=47 / KPH), 1.0).kind == HM.RAIL_CURVE
    # ... stays while the car settles on the target and a little over (turning) ...
    assert all(f.kind == HM.RAIL_CURVE for f in r.frames(rail_sm(scc='turning', v=47 / KPH, v_target=49.5 / KPH), 2.0))
    # ... and goes, after the debounce, once the target is 3 km/h or more over the car's speed
    held = r.frames(rail_sm(scc='turning', v=47 / KPH, v_target=51 / KPH), HM.RAIL_OFF_S - 0.05)
    assert all(f.kind == HM.RAIL_CURVE for f in held)
    assert r.step(rail_sm(scc='turning', v=47 / KPH, v_target=51 / KPH), 0.1).kind == HM.RAIL_NONE

  def test_the_curve_figure_holds_between_steps(self):
    assert [HM.curve_figure(v / KPH, True, None) for v in (37.4, 37.6, 2.0, 112.4)] == [35, 40, 5, 110]
    assert HM.curve_figure(58.0 / KPH, True, 55) == 55 and HM.curve_figure(59.1 / KPH, True, 55) == 60
    assert HM.curve_figure(51.6 / KPH, True, 55) == 55 and HM.curve_figure(51.4 / KPH, True, 55) == 50
    assert HM.curve_figure(38 / KPH, False, None) == 25, "mph"
    # route 10f t 336: the target ran 58-57-58-57 within half a second, and the whole-unit figure with it
    r, figures = Rail(), []
    for target in (55.0, 58.0, 57.0, 58.4, 57.2, 59.0, 56.9, 58.2) * 3:
      f = r.step(rail_sm(scc='entering', v=62 / KPH, v_target=target / KPH), 0.1)
      if f.kind == HM.RAIL_CURVE:
        figures.append(f.num)
    assert figures and set(figures) == {"55"}, figures

  def test_a_curve_only_while_it_is_the_limit(self):
    for state in ('disabled', 'enabled', 'leaving', 'overriding'):
      assert Rail().step(rail_sm(scc=state, scc_src='sccVision'), 1.0).kind == HM.RAIL_NONE, state
    for src in ('cruise', 'speedLimitAssist', 'sccMap'):
      assert Rail().step(rail_sm(scc='entering', scc_src=src), 1.0).kind == HM.RAIL_NONE, f"{src} is the limit"
    sm = rail_sm(scc='entering')
    sm['longitudinalPlanSP'].smartCruiseControl.vision.active = False
    assert Rail().step(sm, 1.0).kind == HM.RAIL_NONE
    assert Rail().step(rail_sm(scc='entering', v_target=255.0), 1.0).kind == HM.RAIL_NONE, "the 'unset' target"
    assert Rail().step(rail_sm(scc='entering', v_target=0.0), 1.0).kind == HM.RAIL_NONE

  def test_a_map_curve(self):
    f = Rail().step(rail_sm(map_turning=True, v_target=45 / KPH), 1.0)
    assert f.kind == HM.RAIL_CURVE and (f.num, f.unit) == ("45", "km/h") and abs(f.curve_v - 45 / KPH) < 1e-4
    assert Rail().step(rail_sm(map_turning=True, scc_src='cruise'), 1.0).kind == HM.RAIL_NONE

  def test_the_curve_keeps_its_direction_when_the_model_sees_none(self):
    r = Rail()
    assert r.step(rail_sm(scc='entering', yaw=0.1), 1.0).curve_left is False
    assert r.step(rail_sm(scc='entering', yaw=0.0), 0.5).curve_left is False
    assert Rail().step(rail_sm(scc='entering', missing=('modelV2',)), 1.0).kind == HM.RAIL_CURVE, \
      "the curve needs no model: the arrow just keeps its last direction"

  def test_the_driver_taking_the_speed_hides_the_curve_on_the_frame(self):
    # route 10f: the gas at t 338.0, the brake at t 2285.4 - it used to stay 0.5 s
    r = Rail()
    assert r.step(rail_sm(scc='entering'), 1.0).kind == HM.RAIL_CURVE
    assert r.step(rail_sm(scc='overriding', scc_src='cruise')).kind == HM.RAIL_NONE, "the gas: smart cruise overriding"
    r = Rail()
    assert r.step(rail_sm(scc='entering'), 1.0).kind == HM.RAIL_CURVE
    assert r.step(rail_sm(scc='entering', long_active=False)).kind == HM.RAIL_NONE, "the brake: openpilot lets go"
    sm = rail_sm(map_turning=True)
    sm['longitudinalPlanSP'].smartCruiseControl.map.state = 'overriding'
    assert Rail().step(sm, 1.0).kind == HM.RAIL_NONE, "the map's own override state too"
    # back only through the debounce, as at first
    assert r.step(rail_sm(scc='entering')).kind == HM.RAIL_NONE
    assert r.step(rail_sm(scc='entering'), HM.RAIL_ON_S).kind == HM.RAIL_CURVE
    # stale carControl says nothing about the driver: the curve stays
    sm = rail_sm(scc='entering', long_active=False)
    sm.alive['carControl'] = False
    assert Rail().step(sm, 1.0).kind == HM.RAIL_CURVE

  def test_a_moment_out_of_its_states_does_not_flash_the_curve(self):
    r = Rail()
    assert r.step(rail_sm(scc='entering'), 1.0).kind == HM.RAIL_CURVE
    assert all(f.kind == HM.RAIL_CURVE for f in r.frames(rail_sm(scc='enabled', scc_src='cruise'), 0.4))
    assert r.step(rail_sm(scc='entering')).kind == HM.RAIL_CURVE
    assert r.step(rail_sm(scc='enabled', scc_src='cruise'), HM.RAIL_OFF_S + 0.05).kind == HM.RAIL_NONE

  def test_a_planned_stop_wins_over_a_curve(self):
    r = Rail()
    f = r.step(rail_sm(stop_m=25.0, scc='entering'), 1.0)
    assert f.kind == HM.RAIL_STOP
    f = r.step(rail_sm(stop_m=None, scc='entering'), HM.RAIL_OFF_S + 0.05)
    assert f.kind == HM.RAIL_CURVE, "and the curve is back once the stop has gone"

  def test_each_toggle(self):
    sm = rail_sm(stop_m=25.0, scc='entering')
    assert Rail(dataclasses.replace(HS.ALL_ON, planned_stop=False)).step(sm, 1.0).kind == HM.RAIL_CURVE
    assert Rail(dataclasses.replace(HS.ALL_ON, curve=False)).step(sm, 1.0).kind == HM.RAIL_STOP
    assert Rail(dataclasses.replace(HS.ALL_ON, curve=False)).step(rail_sm(scc='entering'), 1.0).kind == HM.RAIL_NONE
    assert Rail(dataclasses.replace(HS.ALL_ON, planned_stop=False, curve=False)).step(sm, 1.0) == HM.RailFrame()
    assert Rail(HS.ALL_OFF).step(sm, 1.0) == HM.RailFrame()
    # switched off mid-stop: gone on the frame, and back on it starts over (the debounce again)
    r = Rail()
    r.step(sm, 1.0)
    r.settings = dataclasses.replace(HS.ALL_ON, planned_stop=False, curve=False)
    assert r.step(sm).kind == HM.RAIL_NONE
    r.settings = HS.ALL_ON
    assert r.step(sm).kind == HM.RAIL_NONE and r.step(sm, HM.RAIL_ON_S).kind == HM.RAIL_STOP

  def test_missing_or_stale_messages_never_raise(self):
    services = ('carState', 'modelV2', 'radarState', 'carControl', 'longitudinalPlan', 'longitudinalPlanSP')
    assert Rail().step(FakeSM(), 1.0) == HM.RailFrame(), "nothing received"
    for svc in ('carState', 'modelV2', 'radarState'):
      assert Rail().step(rail_sm(stop_m=25.0, missing=(svc,)), 1.0).kind == HM.RAIL_NONE, f"no {svc}: no stop"
    assert Rail().step(rail_sm(scc='entering', missing=('longitudinalPlanSP',)), 1.0).kind == HM.RAIL_NONE
    assert Rail().step(rail_sm(scc='entering', missing=('carState',)), 1.0).kind == HM.RAIL_NONE
    for svc in services:
      sm = rail_sm(stop_m=25.0, scc='entering')
      sm.alive[svc] = False
      f = Rail().step(sm, 1.0)
      sm = rail_sm(stop_m=25.0, scc='entering')
      sm.recv_frame[svc] = STARTED - 1   # last drive's
      g = Rail().step(sm, 1.0)
      assert f == g, svc
      if svc in ('carState', ):
        assert f.kind == HM.RAIL_NONE
      elif svc in ('modelV2', 'radarState'):
        assert f.kind == HM.RAIL_CURVE, f"no stop without {svc}; the curve does not need it"
      elif svc == 'longitudinalPlanSP':
        assert f.kind == HM.RAIL_STOP
      else:
        assert f.kind == HM.RAIL_STOP and not f.solid, f"stale {svc}: grey"

  def test_units(self):
    # whole metres under 20 m: 5 m steps drew 12.7 m as '15' and 18.7 m as '20'
    assert [HM.fmt_stop_dist(d, True) for d in (1.2, 4.4, 9.6, 12.7, 18.7, 19.6, 23.0, 97.0, 123.0, 1500.0)] == \
      [("1", "m"), ("4", "m"), ("10", "m"), ("13", "m"), ("19", "m"), ("20", "m"), ("25", "m"), ("95", "m"), ("120", "m"),
       ("1.5", "km")]
    assert [HM.fmt_stop_dist(d, False) for d in (2.0, 25.0, 120.0, 1200.0)] == \
      [("7", "ft"), ("80", "ft"), ("400", "ft"), ("0.7", "mi")]
    assert HM.fmt_speed(37 / KPH, True) == ("37", "km/h") and HM.fmt_speed(37 / KPH, False) == ("23", "mph")


# ------------------------------------------------------------------------------------------- the alerts' rules
def alert(kind="manualRestart/warning", text1="TAKE CONTROL", text2="Resume Driving Manually"):
  return SimpleNamespace(alert_type=kind, text1=text1, text2=text2)




class StopFlow:
  """The banner's calls in the order the UI makes them, one frame at a time (20 Hz):
    1. HudCluster._update_state: observe(what the alert renderer will draw) - the alert, the previous one while it
       fades out, or None - every frame;
    2. HudCluster._alert_covers: compact() for any alert it draws;
    3. AlertRenderer._render -> hud_alerts.draw_compact_standstill: compact(), only when there is an alert.
  The renderer never asks while there is no alert: that is the call order the first version's test did not follow."""
  def __init__(self, settings=HS.ALL_ON):
    self.b, self.sm, self.t, self.settings = StandstillBanner(), FakeSM(), 100.0, settings
    self.lead = self.sm.put('radarState').leadOne

  def frame(self, shown, dt=0.05):
    self.t += dt
    self.b.observe(shown)
    if shown is None:
      return None
    cluster = self.b.compact(shown, self.settings, self.sm, STARTED, self.t)
    renderer = self.b.compact(shown, self.settings, self.sm, STARTED, self.t)
    assert cluster == renderer, "the cluster and the renderer agree within a frame"
    return renderer

  def frames(self, shown, seconds):
    return [self.frame(shown) for _ in range(int(round(seconds / 0.05)))]


class TestAlertRules(OpenpilotTestCase):
  def test_the_standstill_prompt_is_compact_with_the_setting_on(self):
    b, sm = StandstillBanner(), FakeSM()
    assert b.compact(alert(), HS.ALL_ON, sm, STARTED, 0.0)
    assert not b.compact(alert(), with_(stopped_banner=False), sm, STARTED, 0.0)
    for other in ("speedLimitPreActive/warning", "steerSaturated/warning", "resumeRequired/warning", ""):
      assert not b.compact(alert(kind=other), HS.ALL_ON, sm, STARTED, 0.0), other

  def test_the_full_prompt_returns_when_the_car_ahead_moves_off_and_stays(self):
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 0.2
    assert all(s.frames(alert(), 2.0)), "a stopped car ahead: compact"
    s.lead.vLead = 1.5
    moving = s.frames(alert(), 1.0)
    assert not moving[-1], "it moves off: the full alert"
    assert all(moving[:int(LEAD_DEPART_S / 0.05) - 1]), f"after {LEAD_DEPART_S} s of moving, not on the first frame"
    s.lead.vLead = 0.0
    assert not any(s.frames(alert(), 2.0)), "and it stays full until the prompt clears"
    s.lead.present, s.lead.vLead = False, 5.0
    assert not any(s.frames(alert(), 1.0)), "the car ahead gone from the radar: still full"

  def test_two_stops_in_a_drive_both_start_compact(self):
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 0.1
    assert all(s.frames(alert(), 2.0)), "stop 1: compact"
    s.lead.vLead = 2.0
    assert not s.frames(alert(), 1.0)[-1], "stop 1: the car ahead moves off - full"
    # we drive away: the prompt clears; the renderer keeps drawing it while it fades out (will_render returns it),
    # then there is no alert at all and the renderer stops asking
    s.lead.vLead = 8.0
    assert not any(s.frames(alert(), 0.3)), "still full while it fades out"
    s.frames(None, 5.0)
    s.lead.vLead = 0.1
    assert all(s.frames(alert(), 2.0)), "stop 2: compact again"
    s.lead.vLead = 2.0
    assert not s.frames(alert(), 1.0)[-1], "stop 2: full again when the car ahead moves off"

  def test_another_alert_in_between_also_ends_the_prompt(self):
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 2.0
    assert not s.frames(alert(), 1.0)[-1]
    s.frames(alert(kind="steerSaturated/warning", text1="TAKE CONTROL", text2="Turn Exceeds Steering Limit"), 0.5)
    s.lead.vLead = 0.0
    assert all(s.frames(alert(), 1.0))

  def test_a_moment_of_radar_noise_does_not_bring_the_full_prompt_back(self):
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 0.0
    for _ in range(5):
      s.lead.vLead = 1.6
      assert all(s.frames(alert(), LEAD_DEPART_S - 0.1)), "under the debounce"
      s.lead.vLead = 0.3
      assert all(s.frames(alert(), 0.2))

  def test_a_radar_state_from_before_this_drive_or_stale_is_ignored(self):
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 3.0
    s.sm.recv_frame['radarState'] = STARTED - 1   # seen, but last drive's
    assert all(s.frames(alert(), 2.0))
    s.sm.recv_frame['radarState'] = STARTED + 5
    s.sm.alive['radarState'] = False              # this drive's, but no longer arriving
    assert all(s.frames(alert(), 2.0))
    s.sm.alive['radarState'] = True
    assert not s.frames(alert(), 1.0)[-1], "fresh: it counts"

  def test_the_setting_off_mid_stop_draws_it_full_and_forgets(self):
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 2.0
    assert not s.frames(alert(), 1.0)[-1]
    s.settings = with_(stopped_banner=False)
    assert not any(s.frames(alert(), 0.2))
    s.settings, s.lead.vLead = HS.ALL_ON, 0.0
    assert all(s.frames(alert(), 0.2)), "on again: a fresh start, compact"

  def test_second_line(self):
    assert short_line2("Resume Driving Manually") == "resume manually"
    assert short_line2("Something Else") == "something else"

  def test_the_pending_limit(self):
    sm = drive(limit=50)
    assert pending_limit(sm, True) == (50, 0)
    assert pending_limit(sm, False) == (round(50 / KPH * 2.23694), 0)
    assert pending_limit(drive(limit=50, offset=5), True) == (50, 5), "the offset, as sunnypilot's sign shows it"
    assert pending_limit(drive(limit=50, offset=-3), True) == (50, -3)
    assert pending_limit(FakeSM(), True) == (0, 0)


# ------------------------------------------------------------------------------------------- sunnylink
def hud_section():
  ui = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
  panel = next(p for p in ui["panels"] if p["id"] == "visuals")
  return panel, next(s for s in panel["sections"] if s["id"] == "hud_comma4")


class TestSunnylink(OpenpilotTestCase):
  def test_every_setting_is_in_the_hud_section(self):
    panel, sec = hud_section()
    assert panel["label"] == "Visuals" and sec["title"] == "HUD"
    keys = [it["key"] for it in sec["items"]]
    assert sorted(keys) == sorted(HS.ALL_PARAMS)
    assert keys[0] == HS.PARAM_SPEED_CLUSTER

  def test_widgets_match_the_param_types(self):
    _, sec = hud_section()
    for it in sec["items"]:
      if it["key"] == HS.PARAM_NEXT_LIMIT:
        assert it["widget"] == "multiple_button"
        assert [o["value"] for o in it["options"]] == list(HS.NEXT_MODES)
        assert [o["label"] for o in it["options"]] == ["Off", "Bar", "Distance", "Both"]
      else:
        assert it["widget"] == "toggle", it["key"]
      assert it.get("title") and it.get("description"), it["key"]

  def test_shown_for_a_comma_4_only(self):
    _, sec = hud_section()
    assert sec["visibility"] == [{"type": "capability", "field": "device_type", "equals": "mici"}]

  def test_the_cluster_pieces_hide_with_the_cluster(self):
    _, sec = hud_section()
    rule = [{"type": "param", "key": HS.PARAM_SPEED_CLUSTER, "equals": True}]
    by_key = {it["key"]: it for it in sec["items"]}
    for key in (HS.PARAM_NEXT_LIMIT, HS.PARAM_SCHOOL_CUE, HS.PARAM_VARIABLE_SIGN):
      assert by_key[key].get("visibility") == rule, key
    for key in (HS.PARAM_SPEED_CLUSTER, HS.PARAM_STOPPED_TIMER, HS.PARAM_STOPPED_BANNER, HS.PARAM_CONFIRM_LIMIT,
                HS.PARAM_PLANNED_STOP, HS.PARAM_CURVE):
      assert "visibility" not in by_key[key], f"{key} works without the cluster"

  def test_the_rail_items_say_what_they_cannot_know(self):
    _, sec = hud_section()
    by_key = {it["key"]: it for it in sec["items"]}
    stop = by_key[HS.PARAM_PLANNED_STOP]["description"]
    assert "never says why" in stop and "grey" in stop and "10 seconds" in stop
    assert "target speed" in by_key[HS.PARAM_CURVE]["description"]


# ------------------------------------------------------------------------------------------- upstream files
def marked(path: Path, needles, window=3):
  lines = path.read_text(encoding="utf-8").splitlines()
  for needle in needles:
    idx = next((i for i, line in enumerate(lines) if needle in line), None)
    assert idx is not None, f"{path.name}: {needle!r} not found"
    assert any("FORK(HUD)" in line for line in lines[max(0, idx - window):idx + 1]), \
      f"{path.name}:{idx + 1}: {needle!r} has no FORK(HUD) marker"


class TestUpstreamHunks(OpenpilotTestCase):
  def test_every_hook_in_an_upstream_file_is_marked(self):
    marked(ROAD_VIEW, ("hud_cluster import HudCluster", "self._hud_cluster = HudCluster(", "self._hud_cluster.render(",
                       "hud_rail import HudRail", "self._hud_rail = HudRail(", "self._hud_rail.render("))
    marked(BALL, ("self.hud_floor_y = -math.inf", "max(dot_height, self.hud_floor_y)"), window=0)
    marked(ALERTS, ("import hud_alerts", "hud_alerts.draw_compact_standstill(", "hud_alerts.draw_pending_limit("))
    marked(PARAMS_KEYS, [f'"{k}"' for k in HS.ALL_PARAMS], window=10)
    marked(VISUALS_YAML, ("id: hud_comma4",))
    marked(MICI_SETTINGS, ("import cloudlog", "def gateway_icon():", "icons_mici/gateway.png", "gateway_icon())"), window=5)
    marked(REPO / ".gitattributes", ("icons_mici/gateway.png -filter",))

  def test_the_hooks_fall_through_to_the_stock_drawing(self):
    src = ALERTS.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self, rect"):src.index("  def _draw_icons")]
    hook = render.index("hud_alerts.draw_compact_standstill(self, alert)")
    assert render.index("self._draw_background(alert)") > hook, "the stock drawing still runs after the hook"
    assert "SpeedLimitAlertRenderer.update(self)\n      return True" in render[hook:], "the banner keeps the confirm fade running"
    icons = src[src.index("  def _draw_icons"):src.index("  def _draw_background")]
    assert icons.index("hud_alerts.draw_pending_limit(") > icons.index("self._turn_signal_alpha_filter.update(255 * 0.2)"), \
      "the turn-signal blink advances exactly as before"
    road = ROAD_VIEW.read_text(encoding="utf-8")
    assert road.index("self._hud_cluster.render(") < road.index("self._alert_renderer.render(self._content_rect)"), \
      "the cluster is drawn under the alerts"
    assert road.index("self._hud_cluster.render(") > road.index("rl.begin_scissor_mode("), "inside the content rect"
    rail, ball = road.index("self._hud_rail.render(self.rect)"), road.index("self._confidence_ball.render(self.rect)")
    assert road.index("rl.end_scissor_mode()") < rail < ball, "the rail is in the ball's strip, and sets its floor first"
    src = BALL.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self"):]
    assert render.index("dot_height = self._rect.y + dot_height") < render.index("max(dot_height, self.hud_floor_y)") < \
      render.index("draw_circle_gradient("), "the floor applies to the stock position, before it is drawn"


# ------------------------------------------------------------------------------------------- the gateway icon
def git(*args) -> subprocess.CompletedProcess:
  return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)


class TestGatewayIcon(OpenpilotTestCase):
  def test_the_tile_uses_icon_b_and_the_dialog_keeps_the_arrow(self):
    settings = MICI_SETTINGS.read_text(encoding="utf-8")
    tile = settings[settings.index('board_btn = SettingsBigButton(tr("gateway")'):settings.index("board_btn.set_click_callback")]
    assert "gateway_icon()" in tile
    helper = settings[settings.index("def gateway_icon():"):settings.index("class SunnylinkBigButton")]
    loaded, fallback = helper.split("except Exception:")
    assert "icons_mici/gateway.png" in loaded and "icon_software.png" not in loaded
    assert "if tex.id:" in loaded, "an empty texture (a failed load at another scale) also falls back"
    assert "icon_software.png" in fallback, "the old icon only when B cannot be loaded"
    assert "icon_software.png" in BOARD.read_text(encoding="utf-8"), "the update dialog describes the action: an arrow"

  def test_the_asset(self):
    # never skipped: a git-lfs pointer here is the bug (the fork cannot push LFS objects; see the next test)
    data = GATEWAY_ICON.read_bytes()
    assert data[:8] == PNG_SIGNATURE, f"not a PNG: {data[:60]!r}"
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (w, h) == (128, 88), "128 on the long side, like the rest of icons_mici"
    assert data[24] == 8 and data[25] == 6, "8-bit RGBA: white on transparent"

  def test_git_stores_the_png_itself_not_an_lfs_pointer(self):
    # .gitattributes sends *.png to sunnypilot's LFS server, which the fork cannot push to: a pointer committed here
    # would reach the car without its object. The path override keeps this file a plain git object.
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
      return  # an exported tree without git: test_the_asset has already checked the file itself
    rel = GATEWAY_ICON.relative_to(REPO).as_posix()
    attrs = git("check-attr", "filter", "--", rel).stdout.decode()
    assert attrs.strip().endswith("filter: unset"), f"the LFS filter applies to it: {attrs!r}"
    staged = git("cat-file", "-p", f":{rel}")
    assert staged.returncode == 0 and staged.stdout[:8] == PNG_SIGNATURE, f"the index holds {staged.stdout[:60]!r}"
    committed = git("cat-file", "-p", f"HEAD:{rel}")
    if committed.returncode == 0:  # absent only before the commit that adds it
      assert committed.stdout[:8] == PNG_SIGNATURE, f"HEAD holds {committed.stdout[:60]!r}"
    assert staged.stdout == GATEWAY_ICON.read_bytes()
