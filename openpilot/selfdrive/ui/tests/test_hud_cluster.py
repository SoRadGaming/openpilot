"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 HUD's rules, settings, sunnylink items and markers - everything that needs no window. The
pixels are tested in test_hud_render.py (a headless raylib child process).

hud_model.py and hud_settings.py import no raylib, so they are imported here; the drawing files are only parsed (hud_alerts
is imported for FrameAlert alone, which draws nothing).
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
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (Approach, ApproachState, HudFrame, StandstillBanner,
                                                                     approach_now, approach_text, box_outline, build_frame,
                                                                     next_window_m, pending_limit, short_line2,
                                                                     target_speed, LEAD_DEPART_S, MAP_EXTRAPOLATE_MAX_S)
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
HUD_RENDERER = ROOT / "selfdrive/ui/mici/onroad/hud_renderer.py"
HUD_RENDERER_SP = ROOT / "selfdrive/ui/sunnypilot/mici/onroad/hud_renderer.py"
HUD_ALERTS = ROOT / "selfdrive/ui/sunnypilot/mici/onroad/hud_alerts.py"
HUD_CLUSTER = ROOT / "selfdrive/ui/sunnypilot/mici/onroad/hud_cluster.py"
KPH = 3.6

DEFAULTS = dict.fromkeys(HS.ALL_PARAMS, True)
# round 5 (2026-10-09, option A) took these off the screen with what they switched: nothing may read them any more
RETIRED = ("HudSpeedCluster", "HudCurrentSpeed", "HudLimitSign", "HudSchoolZoneCue", "HudVariableLimitSign",
           "HudNextLimit", "HudConfirmLimit")
RETIRED_RE = re.compile(r"\b(" + "|".join(RETIRED) + r")\b")


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
          ahead_valid=True, nsw_state=2, standstill=False, lp=True, lmd=True, lmd_alive=True, lp_alive=True, offset=0,
          cs_alive=True):
  sm = FakeSM()
  cs = sm.put('carState', alive=cs_alive)
  cs.vEgo = v
  cs.vEgoCluster = v
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
    m.nswZone.mode = 2
    m.nswZone.state = nsw_state
    m.nswZone.speedLimit = limit / KPH
  return sm


def frame(sm, settings=HS.ALL_ON, *, stopped_s=None, started_frame=5) -> HudFrame:
  return build_frame(sm, settings, started_frame=started_frame, stopped_s=stopped_s)


def raw(sm, *, metric=True, limit=None, map_age_s=0.0, started_frame=5) -> Approach | None:
  if limit is None:
    limit = HM.resolver_map_limit(sm, started_frame, KPH if metric else 2.23694)
  return approach_now(sm, started_frame=started_frame, is_metric=metric, limit=limit, map_age_s=map_age_s)


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
      assert "BOOL" in flags and '"1"' in flags, f"{key} defaults to on"
    assert set(HS.FIELDS) == set(HS.ALL_PARAMS) and len(HS.ALL_PARAMS) == 8
    assert {f.name for f in dataclasses.fields(HS.HudSettings)} == set(HS.FIELDS.values())

  def test_the_built_params_library_knows_them(self):
    params = Params()
    for key, value in DEFAULTS.items():
      assert params.get(key, return_default=True) == value, key
    assert HS.read_settings(params) == HS.ALL_ON

  def test_each_param_switches_only_its_piece(self):
    params = Params()
    for key, field in HS.FIELDS.items():
      params.put_bool(key, False, block=True)
      s = HS.read_settings(params)
      assert getattr(s, field) is False
      assert s == with_(**{field: False}), key
      params.put_bool(key, True, block=True)

  def test_unreadable_params_draw_the_stock_screen(self):
    # a build whose params library predates the keys: everything off, not half a HUD
    assert HS.read_settings(FakeParams(fail=True)) == HS.ALL_OFF
    assert HS.read_settings(FakeParams()) == HS.ALL_ON

  def test_params_are_read_at_most_once_a_second(self):
    t = [0.0]
    params = FakeParams()
    r = HS.HudSettingsReader(params_fn=lambda: params, clock=lambda: t[0])
    assert r.get() == HS.ALL_ON and r.reads == 1
    params.v[HS.PARAM_APPROACHING_LIMIT] = False
    for dt in (0.016, 0.5, 0.99):
      t[0] = dt
      assert r.get().approaching_limit and r.reads == 1, "re-read inside a second"
    t[0] = 1.0
    assert not r.get().approaching_limit and r.reads == 2
    params.v[HS.PARAM_APPROACHING_LIMIT] = True
    assert r.get(force=True).approaching_limit and r.reads == 3

  def test_the_reader_starts_with_everything_off(self):
    # before its first read (the first frame) nothing is drawn
    assert HS.HudSettingsReader(params_fn=FakeParams).settings == HS.ALL_OFF


class TestRetiredKeys(OpenpilotTestCase):
  """Round 5 removed the live speed, the sign, its zone styling, the next lower limit and the old confirm drawing. A
  retired key read anywhere would raise UnknownKeyName (a read with no default) or silently do nothing: it must not
  appear in code, the params table, or sunnylink's settings."""
  def test_no_removed_key_is_read_anywhere(self):
    me = Path(__file__).resolve()
    hits = []
    for pattern in ("**/*.py", "**/*.h", "**/*.cc", "**/*.yaml", "**/*.json"):
      for path in ROOT.glob(pattern):
        if path.resolve() == me or ".venv" in path.parts or "third_party" in path.parts:
          continue
        try:
          text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
          continue
        for i, line in enumerate(text.splitlines(), 1):
          if RETIRED_RE.search(line):
            hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
    assert not hits, "a removed HUD key is still used:\n" + "\n".join(hits)

  def test_the_guard_finds_one(self):
    assert RETIRED_RE.search('params.get("HudLimitSign")') and RETIRED_RE.search("{\"HudNextLimit\", {PERSISTENT}}")
    assert not RETIRED_RE.search("HudStoppedTimer HudApproachingLimit HudCompactLimitPrompts")
    assert not set(RETIRED) & set(HS.ALL_PARAMS)


# ------------------------------------------------------------------------------------------- the stop timer
class TestTimerRules(OpenpilotTestCase):
  def test_the_stop_time_after_a_second_at_a_standstill(self):
    sm = drive(v=0.0, standstill=True)
    assert frame(sm, stopped_s=0.5).timer_s is None, "a second first"
    f = frame(sm, stopped_s=29.9)
    assert f.timer_s == 29.9
    assert frame(sm, with_(stopped_timer=False), stopped_s=29.9).timer_s is None
    assert frame(sm, HS.ALL_OFF, stopped_s=29.9).timer_s is None

  def test_moving_draws_nothing_top_right(self):
    # option A: no speed, no sign, no next limit - whatever the messages say
    for sm in (drive(), drive(limit=40), drive(v=30.3), drive(lp=False), drive(lmd=False)):
      assert frame(sm, stopped_s=30.0) == HudFrame(), "standstill is carState's, not the caller's"
    assert frame(drive(v=0.0, standstill=False), stopped_s=30.0).timer_s is None

  def test_missing_car_state(self):
    sm = drive(v=0.0, standstill=True)
    sm.seen['carState'] = False
    assert frame(sm, stopped_s=30.0).timer_s is None
    sm = drive(v=0.0, standstill=True)
    sm.recv_frame['carState'] = 2  # from before this drive started (started_frame 5)
    assert frame(sm, stopped_s=30.0).timer_s is None

  def test_the_cluster_draws_only_the_timer(self):
    src = HUD_CLUSTER.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self"):]
    assert "hd.big_digits(" in render and "hd.stopwatch_glyph(" in render
    for gone in ("hd.sign(", "next_row", "school_cue", "f.speed", "_slide("):
      assert gone not in src, gone
    # the timer's place is exactly the corner it had with the sign off (rect right - 10, row center 36)
    assert "right, cy = rect.x + rect.width - MARGIN, rect.y + ROW_CY" in render
    import ast
    consts = {n.targets[0].id: n.value.value for n in ast.parse(src).body
              if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.targets[0], ast.Name)}
    assert (consts["MARGIN"], consts["ROW_CY"], consts["GAP"], consts["TIMER_SIZE"], consts["STOPWATCH_SIZE"]) == \
      (10, 36, 10, 50, 28)


# ------------------------------------------------------------------------------------------- the coming limit
class ApproachSim:
  """An ApproachState on a 20 Hz sim clock, as HudRendererSP drives it; put() bumps liveMapDataSP's recv_frame like a
  new 1 Hz message."""
  def __init__(self, settings=HS.ALL_ON, metric=True, sl_mode_on=True):
    self.st, self.t, self.settings, self.metric, self.sl_mode_on = ApproachState(), 100.0, settings, metric, sl_mode_on
    self.map_frame = 10

  def step(self, sm, seconds=0.05, new_map=True) -> Approach | None:
    if new_map:
      self.map_frame += 1
    sm.recv_frame['liveMapDataSP'] = self.map_frame
    a = None
    for _ in range(max(1, int(round(seconds / 0.05)))):
      self.t += 0.05
      a = self.st.update(sm, self.settings, started_frame=5, now=self.t, is_metric=self.metric,
                         speed_limit_mode_on=self.sl_mode_on)
    return a


class TestApproachRules(OpenpilotTestCase):
  def test_a_lower_limit_within_the_window(self):
    a = raw(drive(v=30.3))
    assert a == Approach(limit=100, lower=True, dist=339.0, frac=a.frac) and abs(a.frac - 339.0 / (30.3 * 15)) < 1e-3

  def test_a_higher_limit_too(self):
    # route 10f t 182-194, Bringelly Rd: 50 -> 70 was never shown before round 5
    a = raw(drive(v=50 / KPH, limit=50, ahead=70, ahead_dist=60.0))
    assert a is not None and a.limit == 70 and not a.lower
    assert raw(drive(ahead=110)) is None, "the same limit is not a change"

  def test_outside_15_s_or_500_m_is_hidden(self):
    assert next_window_m(0.0) == 15.0 and next_window_m(10.0) == 150.0 and next_window_m(40.0) == 500.0
    assert raw(drive(v=40.0, ahead_dist=510.0)) is None
    assert raw(drive(v=40.0, ahead_dist=499.0)).limit == 100
    assert raw(drive(v=10.0, ahead_dist=160.0)) is None
    assert raw(drive(v=10.0, ahead_dist=140.0)).limit == 100
    assert raw(drive(ahead_dist=0.0)) is None

  def test_never_at_a_standstill(self):
    assert raw(drive(v=0.0, standstill=True, ahead_dist=10.0)) is None

  def test_only_what_mapd_published_and_never_in_a_tunnel(self):
    # dead reckoning: mapd publishes no next limit; nswZone keeps its own look-ahead for the logs only
    sm = drive(ahead_valid=False, ahead=0, ahead_dist=0.0, nsw_state=4)
    sm['liveMapDataSP'].nswZone.speedLimitAhead = 60 / KPH
    sm['liveMapDataSP'].nswZone.speedLimitAheadDistance = 250.0
    assert raw(sm) is None
    # and should mapd ever publish one there: still nothing (dead reckoning, or GPS still lost after it)
    for state in HM.NSW_NO_LOOKAHEAD:
      assert raw(drive(nsw_state=state)) is None, state
    for state in (0, 1, 2, 3, 5, 6):
      assert raw(drive(nsw_state=state)) is not None, state

  def test_only_while_the_limit_in_force_is_from_the_map(self):
    assert raw(drive(source='map')).limit == 100
    for source in ('car', 'none'):
      assert raw(drive(source=source)) is None, source
    assert raw(drive(valid=False, last_valid=True)).limit == 100, "a held map limit is still the one in force"
    assert raw(drive(valid=False, last_valid=False)) is None

  def test_missing_or_stale_messages(self):
    for kw in ({'lp': False}, {'lp_alive': False}, {'lmd': False}, {'lmd_alive': False}, {'cs_alive': False}):
      assert raw(drive(**kw)) is None, kw

  def test_the_distance_runs_down_between_map_messages(self):
    sm = drive(v=30.0, ahead_dist=339.0)
    assert raw(sm, map_age_s=0.0).dist == 339.0
    assert abs(raw(sm, map_age_s=0.5).dist - (339.0 - 15.0)) < 1e-3
    assert abs(raw(sm, map_age_s=MAP_EXTRAPOLATE_MAX_S).dist - 309.0) < 1e-3
    assert abs(raw(sm, map_age_s=5.0).dist - 309.0) < 1e-3, "a late message: held, not run down further"
    assert raw(sm, map_age_s=-1.0).dist == 339.0
    assert raw(sm, map_age_s=0.5).frac < raw(sm).frac, "the pill drains in between"
    assert raw(drive(v=30.0, ahead_dist=20.0), map_age_s=1.0) is None, "passed it before the next message"

  def test_imperial(self):
    a = raw(drive(v=30.0, limit=100, ahead=90, ahead_dist=300.0), metric=False)
    assert a.limit == round(90 / KPH * 2.23694) == 56 and a.lower

  def test_the_words(self):
    assert approach_text("300 m", False) == "in 300 m" and approach_text("1.2 km", False) == "in 1.2 km"
    assert approach_text("Near", False) == "now", "sunnypilot's 'Near' is drawn 'now'"
    assert approach_text("300 m", True) == "now", "held past the look-ahead"
    assert approach_text("600 ft", False) == "in 600 ft"

  def test_the_setting_and_speed_limit_mode(self):
    assert ApproachSim().step(drive()) is not None
    assert ApproachSim(with_(approaching_limit=False)).step(drive()) is None
    assert ApproachSim(sl_mode_on=False).step(drive()) is None
    assert ApproachSim(HS.ALL_OFF).step(drive()) is None

  def test_held_until_the_limit_in_force_becomes_it(self):
    # route 10f, the M4: the look-ahead ends at 773.75 (11 m), MAX 100 at 774.8 - nothing on screen in between, before
    s = ApproachSim()
    a = s.step(drive(ahead_dist=200.0), 1.0)
    assert a.limit == 100 and not a.held
    assert s.step(drive(ahead_dist=11.0)).dist == 11.0, "the last look-ahead, at the change"
    gone = drive(ahead_valid=False, ahead=0, ahead_dist=0.0)   # mapd stopped publishing it; the resolver still has 110
    for _ in range(int(HM.APPROACH_HOLD_S / 0.05) - 1):
      h = s.step(gone, new_map=False)
      assert h == Approach(limit=100, lower=True, dist=0.0, frac=0.0, held=True)
    assert s.step(drive(limit=100, ahead_valid=False, ahead=0, ahead_dist=0.0)) is None, "the resolver took it: hand-over"
    assert s.step(gone) is None, "and it does not come back"

  def test_held_only_once_the_change_is_reached(self):
    # reached: within APPROACH_NEAR_S of travel, or under 'Near' (50 m; 100 ft imperial)
    assert HM.reached(49.0, 5.0, True) and not HM.reached(51.0, 5.0, True)
    assert HM.reached(60.0, 30.6, True) and not HM.reached(62.0, 30.6, True), "2 s at 110 km/h"
    assert HM.reached(30.0, 5.0, False) and not HM.reached(31.0, 5.0, False), "100 ft"
    # the look-ahead lost 80 m out at 110 km/h: a distance, running down - never a jump to 'now'
    s = ApproachSim()
    s.step(drive(ahead_dist=80.0))
    gone = drive(ahead_valid=False, ahead=0, ahead_dist=0.0)
    a = s.step(gone, new_map=False)
    assert a is not None and not a.held and abs(a.dist - (80.0 - 110 / KPH * 0.05)) < 1e-6, a
    # ... until it is within 2 s of travel: then held 'now'
    seen = [s.step(gone, new_map=False) for _ in range(30)]
    first_held = next(i for i, x in enumerate(seen) if x.held)
    assert all(not x.held and x.dist > 110 / KPH * HM.APPROACH_NEAR_S for x in seen[:first_held]), seen
    assert all(x.held for x in seen[first_held:])

  def test_the_hold_ends(self):
    def held_then(after, seconds=0.05):
      s = ApproachSim()
      s.step(drive(ahead_dist=200.0), 1.0)
      s.step(drive(ahead_dist=20.0))
      assert s.step(drive(ahead_valid=False, ahead=0, ahead_dist=0.0), 0.5).held
      return s.step(after, seconds)
    none = {"ahead_valid": False, "ahead": 0, "ahead_dist": 0.0}
    assert held_then(drive(**none), HM.APPROACH_HOLD_S) is None, "after APPROACH_HOLD_S at most"
    assert held_then(drive(limit=90, **none)) is None, "another limit came"
    assert held_then(drive(v=0.0, standstill=True, **none)) is None, "stopped"
    assert held_then(drive(nsw_state=4, **none)) is None, "into a tunnel"
    assert held_then(drive(lp=False, **none)) is None, "the plan gone"
    assert held_then(drive(source='car', **none)) is None, "a car-sourced limit now"

  def test_a_gap_in_the_look_ahead_runs_the_distance_down_and_a_new_value_replaces_it(self):
    s = ApproachSim()
    s.step(drive(ahead_dist=300.0))
    a = s.step(drive(ahead_valid=False, ahead=0, ahead_dist=0.0), 1.0)
    assert a is not None and not a.held and abs(a.dist - (300.0 - 110 / KPH * 1.0)) < 1e-6, \
      f"one message without it, 300 m out: still a distance, run down by the car's speed - not 'now' {a}"
    a = s.step(drive(ahead_dist=250.0))
    assert a.limit == 100 and not a.held and a.dist == 250.0
    a = s.step(drive(ahead=90, ahead_dist=240.0))
    assert a.limit == 90 and a.lower
    # lost for longer than APPROACH_GAP_S, still far out: it goes (and the tunnel, the stop and the hand-over end it too)
    s = ApproachSim()
    s.step(drive(ahead_dist=440.0))
    gone = drive(ahead_valid=False, ahead=0, ahead_dist=0.0)
    assert s.step(gone, HM.APPROACH_GAP_S - 0.05) is not None
    assert s.step(gone, 0.2) is None
    for after in (drive(nsw_state=4, ahead_valid=False), drive(v=0.0, standstill=True, ahead_valid=False),
                  drive(limit=100, ahead_valid=False)):
      s = ApproachSim()
      s.step(drive(ahead_dist=300.0))
      assert s.step(after) is None, "into a tunnel / stopped / the resolver took it"

  def test_the_window_shrinking_as_the_car_slows_is_a_gap(self):
    # 70 at 66 m at 37 km/h (window 154 m); the car brakes to 14 km/h (window 58 m): out of the window, not reached
    s, kw = ApproachSim(), {"limit": 50, "ahead": 70}
    assert s.step(drive(v=37 / KPH, ahead_dist=66.0, **kw)).dist == 66.0
    a = s.step(drive(v=14 / KPH, ahead_dist=66.0, **kw))
    assert a is not None and not a.held and 64.0 < a.dist < 66.0 and a.frac == 1.0, a
    assert s.step(drive(v=14 / KPH, ahead_dist=66.0, **kw), HM.APPROACH_GAP_S + 0.1) is None

  def test_route_10f_braking_for_a_red_light_with_the_70_ahead(self):
    # route 10f t 192.7-201.7 (Bringelly Rd, limit 50 in force throughout): mapd's 1 Hz look-ahead and the car's speed,
    # as logged. Before fix round 1 it drew '70 now' from 194.2 to 202.2 - held over the gap, then the distance clamped
    # under 50 m while mapd said 65 m. Now: 'now' only where mapd's own figure is under 50 m (sunnypilot's 'Near') and
    # the hold after it (3 s at most), then 'in 65 m' as soon as mapd says 65 m, and nothing once the limit in force is
    # no longer the map's
    log = [(37.2, 66.3), (38.0, 54.8), (35.7, 50.8), (33.5, 39.0), (31.2, None), (29.7, None), (27.8, None),
           (25.9, 65.4), (20.9, 60.4), (15.0, 41.2)]
    s, words = ApproachSim(), []
    for k, (v, d) in enumerate(log):
      src = 'none' if k == len(log) - 1 else 'map'
      sm = drive(v=v / KPH, limit=50, ahead=70, ahead_dist=d or 0.0, ahead_valid=d is not None, source=src)
      for i in range(20):
        a = s.step(sm, new_map=i == 0)
        words.append(None if a is None else "now" if a.held or a.dist < HM.NEAR_M else f"in {a.dist:.0f}")
    sec = [words[k * 20:(k + 1) * 20] for k in range(len(log))]
    assert all(w.startswith("in ") for w in sec[0]), sec[0]
    assert sec[3] == ["now"] * 20, f"39 m: 'Near' {sec[3]}"
    held = sum(w == "now" for k in (4, 5, 6) for w in sec[k])
    assert held <= HM.APPROACH_HOLD_S / 0.05, f"held at most APPROACH_HOLD_S after the look-ahead went: {sec[4:7]}"
    assert sec[7][0] == "in 65" and all(w is not None and w.startswith("in ") for w in sec[7]), \
      f"mapd's 65 m is news, not clamped to 'now': {sec[7]}"
    assert all(w.startswith("in ") for w in sec[8]), sec[8]
    assert all(w is None for w in sec[9]), "the limit in force is not the map's any more"

  def test_the_distance_only_counts_down(self):
    # route 10f t 194.2-194.8: the run-down passed 50 m, the next 1 Hz message said 50.8 - 'now', 'in 50 m', 'now'
    s, kw = ApproachSim(), {"v": 50 / KPH, "limit": 50, "ahead": 70}
    assert s.step(drive(ahead_dist=49.0, **kw)).dist == 49.0
    a = s.step(drive(ahead_dist=50.8, **kw))
    assert a.dist == 49.0 and abs(a.frac - 49.0 / next_window_m(50 / KPH)) < 1e-6
    assert s.step(drive(ahead_dist=40.0, **kw)).dist == 40.0
    assert s.step(drive(v=50 / KPH, limit=50, ahead=60, ahead_dist=80.0)).dist == 80.0, "another change: its own distance"
    # ... but only by what a period's run-down could explain, + APPROACH_JUMP_M: more is news (route 10f t 199.7)
    s = ApproachSim()
    s.step(drive(ahead_dist=40.0, **kw))
    slack = 50 / KPH * MAP_EXTRAPOLATE_MAX_S + HM.APPROACH_JUMP_M
    assert s.step(drive(ahead_dist=40.0 + slack - 0.5, **kw)).dist == 40.0
    assert abs(s.step(drive(ahead_dist=40.0 + slack + 0.5, **kw)).dist - (40.0 + slack + 0.5)) < 1e-4

  def test_idempotent_within_a_frame(self):
    s, sm = ApproachSim(), drive(ahead_dist=200.0)
    s.step(sm, 1.0)
    gone = drive(ahead_valid=False, ahead=0, ahead_dist=0.0)
    first = s.st.update(gone, HS.ALL_ON, started_frame=5, now=s.t, is_metric=True, speed_limit_mode_on=True)
    assert first is not None and not first.held, "the same frame: the answer already given"


# ------------------------------------------------------------------------------------------- the confirm's outline
class TestBoxOutline(OpenpilotTestCase):
  def test_two_flashes_then_the_march(self):
    fps = 20
    lit = [box_outline(i / fps)[0] for i in range(4 * fps)]
    runs = [k for k in range(1, len(lit)) if lit[k] and not lit[k - 1]] + ([0] if lit[0] else [])
    assert len(runs) == HM.BOX_BLINKS == 2, lit
    assert not any(lit[int((HM.BOX_BLINKS * (HM.BOX_BLINK_ON_S + HM.BOX_BLINK_OFF_S)) * fps) + 1:]), "then never again"
    phases = [box_outline(i / fps)[1] for i in range(4 * fps)]
    assert all(p == 0.0 for p in phases[:14]) and phases[-1] > phases[-2] > 0, "still while flashing, then marching"
    assert abs((phases[-1] - phases[-21]) - HM.BOX_MARCH_PX_S) < 1e-6, "30 px a second"

  def test_under_three_flashes_a_second(self):
    rate = 1.0 / (HM.BOX_BLINK_ON_S + HM.BOX_BLINK_OFF_S)
    assert 2.5 < rate < 3.0, rate
    assert box_outline(-1.0) == (True, 0.0) and box_outline(0.0)[0], "lit from the first half-visible frame"
    assert not box_outline(HM.BOX_BLINK_ON_S + 0.01)[0]

  def test_the_renderer_and_the_box_use_it(self):
    src = HUD_RENDERER_SP.read_text(encoding="utf-8")
    body = src[src.index("  def _draw_pending_max"):src.index("  def _approach_now")]
    assert "box_outline(" in body and "lit=lit, phase=phase" in body and "alpha >= 0.5" in body
    draw = (Path(HM.__file__).parent / "hud_draw.py").read_text(encoding="utf-8")
    box = draw[draw.index("def pending_max("):draw.index("def _solid_rrect(")]
    assert box.count("phase=phase") == 2, "both the underlay and the dashes march"
    assert box.index("rl.draw_text_ex(") < box.index("if lit:"), "the digits are drawn the same, lit or not"


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
    sm = rail_sm(stop_m=25.0, lead_d=10.0)
    sm['radarState'].leadOne.radarTrackId = -2 - 2633  # held_lead.held_id(): radard's clutter guard, made-up dRel
    assert Rail().step(sm, 1.0).kind == HM.RAIL_STOP, "a held lead is no car"

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
    # 0.65 s, 4 m. Now it counts down to the last meter without a gap.
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
    # whole meters under 20 m: 5 m steps drew 12.7 m as '15' and 18.7 m as '20'
    assert [HM.fmt_stop_dist(d, True) for d in (1.2, 4.4, 9.6, 12.7, 18.7, 19.6, 23.0, 97.0, 123.0, 1500.0)] == \
      [("1", "m"), ("4", "m"), ("10", "m"), ("13", "m"), ("19", "m"), ("20", "m"), ("25", "m"), ("95", "m"), ("120", "m"),
       ("1.5", "km")]
    assert [HM.fmt_stop_dist(d, False) for d in (2.0, 25.0, 120.0, 1200.0)] == \
      [("7", "ft"), ("80", "ft"), ("400", "ft"), ("0.7", "mi")]


# ------------------------------------------------------------------------------------------- the alerts' rules
def alert(kind="manualRestart/warning", text1="TAKE CONTROL", text2="Resume Driving Manually"):
  return SimpleNamespace(alert_type=kind, text1=text1, text2=text2)


class StopFlow:
  """The banner's calls in the order the UI makes them, one frame at a time (20 Hz):
    1. HudCluster._alert_covers: compact() for any alert;
    2. AlertRenderer._render -> hud_alerts.draw_compact_standstill: compact(), only when there is an alert;
    3. HudRendererSP._update_state: observe(what the alert renderer will draw) - the alert, the previous one while it
       fades out, or None - every frame (round 5 moved it there from the cluster).
  The renderer never asks while there is no alert: that is the call order the first version's test did not follow."""
  def __init__(self, settings=HS.ALL_ON):
    self.b, self.sm, self.t, self.settings = StandstillBanner(), FakeSM(), 100.0, settings
    self.lead = self.sm.put('radarState').leadOne

  def frame(self, shown, dt=0.05):
    self.t += dt
    if shown is None:
      self.b.observe(shown)
      return None
    cluster = self.b.compact(shown, self.settings, self.sm, STARTED, self.t)
    renderer = self.b.compact(shown, self.settings, self.sm, STARTED, self.t)
    assert cluster == renderer, "the cluster and the renderer agree within a frame"
    self.b.observe(shown)
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

  def test_a_held_lead_never_brings_the_full_prompt_back(self):
    # radard's clutter guard holds a lead with vLead = vEgo and marks it (held_lead.held_id()); only the MPC reads it
    s = StopFlow()
    s.lead.present, s.lead.vLead = True, 2.0
    s.lead.radarTrackId = -2 - 2633
    assert all(s.frames(alert(), 2 * LEAD_DEPART_S + 0.5)), "a held lead moving off: still compact"
    s.lead.radarTrackId = 2633
    assert not s.frames(alert(), 1.0)[-1], "the same track, not held: the full alert"

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

  def test_the_target_speed(self):
    # what the confirm sets: limit + offset, rounded as sunnypilot's arrow and the alert's '+' / '-' round it
    assert target_speed(drive(limit=50), True) == 50
    assert target_speed(drive(limit=50, offset=5), True) == 55
    assert target_speed(drive(limit=50, offset=-3), True) == 47
    assert target_speed(drive(limit=50), False) == round(50 / KPH * 2.23694) == 31
    assert target_speed(drive(limit=0), True) == 0 and target_speed(FakeSM(), True) == 0, "no limit: nothing to show"
    for limit, offset in ((60, 0), (80, 10), (40, -5), (110, 7)):
      sm = drive(limit=limit, offset=offset)
      final = sm['longitudinalPlanSP'].speedLimit.resolver.speedLimitFinalLast
      for metric, conv in ((True, 1.0), (False, 0.621371)):
        assert target_speed(sm, metric) == round(round(final * 3.6, 1) * conv), (limit, offset, metric)

  def test_the_target_is_what_the_confirm_stores_and_max_then_shows(self):
    # cruise_ext.py: v_cruise_kph = clip(round(speed_limit_final_last_kph, 1), v_cruise_min, V_CRUISE_MAX); the MAX number
    # then shows round(v_cruise * KM_TO_MILE) in mph. The constants are the ones the confirm and the MAX number use.
    from openpilot.sunnypilot.selfdrive.car import cruise_ext
    from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed
    from openpilot.selfdrive.ui.mici.onroad import hud_renderer
    assert (HM.CRUISE_MIN_KPH, HM.CRUISE_MAX_KPH) == (cruise_ext.V_CRUISE_MIN, cruise_ext.V_CRUISE_MAX)
    assert HM.KM_TO_MILE == hud_renderer.KM_TO_MILE
    for metric in (True, False):
      assert HM.cruise_min_kph(metric, False) == get_minimum_set_speed(metric) and HM.cruise_min_kph(metric, True) == 8

    def final(kph):
      sm = drive(limit=kph)
      sm['longitudinalPlanSP'].speedLimit.resolver.speedLimitFinalLast = kph / KPH
      return sm
    # a .5 tie in mph: 50 mph + 5 % is 84.49 km/h, stored as 84.5 = 52.506 mph -> MAX shows 53 (not round(52.50) = 52)
    assert target_speed(final(50 * 1.609344 * 1.05), False) == 53
    # under the floor: a 10 km/h shared zone on a button-managed car sets 30; with pcmCruiseSpeed 8 is the floor
    assert target_speed(final(10), True, pcm_cruise_speed=False) == 30
    assert target_speed(final(10), True) == 10 and target_speed(final(5), True) == 8
    # over the top: no more than V_CRUISE_MAX
    assert target_speed(final(160), True) == 145
    # a % offset: 45 km/h + 10 % = 49.5, stored 49.5, shown 50 (round half even: 50)
    assert target_speed(final(49.5), True) == 50

  def test_the_box_and_the_banner_ask_the_same_question(self):
    # the HUD renderer's 'is the box up this frame' and the confirm's own drawing share confirm_target(): the cluster can
    # never hide the speed for a box that is not drawn, nor show it beside one that is
    src = (Path(HM.__file__).parent / "hud_alerts.py").read_text(encoding="utf-8")
    confirm = src[src.index("def _draw_confirm("):src.index("def draw_compact_standstill(")]
    box = src[src.index("def box_alert("):src.index("def _draw_confirm(")]
    assert "target = confirm_target(alert)" in confirm and "confirm_target(alert) > 0" in box
    assert "target_speed(" not in confirm, "one place computes the target"
    note = confirm[confirm.index("note = \"\""):confirm.index("pending_max.publish(")]
    assert "d = target - limit" in note, "the note adds up to the number beside it"


# ------------------------------------------------------------------------------------------- compact alerts
def ui_alert(name, et, a):
  """The alert as the UI gets it from selfdriveState (alert_renderer.get_alert)."""
  return SimpleNamespace(alert_type=f"{name}/{et}", text1=a.alert_text_1, text2=a.alert_text_2, size=int(a.alert_size),
                         status=int(a.alert_status), visual_alert=int(a.visual_alert), priority=int(a.priority))


def pre_active_alerts():
  """speedLimitPreActive's alert is a callback: both its branches (non-PCM: '+', '-', none; PCM long), metric and not."""
  from openpilot.sunnypilot.selfdrive.selfdrived import events as ev_sp
  out = []
  for pcm in (False, True):
    for set_kph, limit_kph in ((50, 80), (80, 50), (60, 60)):
      for metric in (True, False):
        cp = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=pcm)
        cs = SimpleNamespace(vCruiseCluster=float(set_kph))
        res = SimpleNamespace(speedLimitFinalLast=limit_kph / KPH)
        sm = {'longitudinalPlanSP': SimpleNamespace(speedLimit=SimpleNamespace(resolver=res)),
              'controlsState': SimpleNamespace(deprecated=SimpleNamespace(vCruise=0.0))}
        for mici in (True, False):
          saved, ev_sp.IS_MICI = ev_sp.IS_MICI, mici
          try:
            out.append(ev_sp.speed_limit_pre_active_alert(cp, cs, sm, metric, 0, None))  # ty: ignore[invalid-argument-type]
          finally:
            ev_sp.IS_MICI = saved
  return out


def every_alert():
  """(event name, event type, Alert) for every alert of every event, openpilot's and sunnypilot's - the callbacks
  only where they can be evaluated without a car (speedLimitPreActive's, the one on the lists)."""
  from openpilot.selfdrive.selfdrived.events import EVENTS, EVENT_NAME
  from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP, EVENT_NAME_SP
  from openpilot.sunnypilot.selfdrive.selfdrived.events_base import Alert
  out = []
  for table, names in ((EVENTS, EVENT_NAME), (EVENTS_SP, EVENT_NAME_SP)):
    for ev, by_type in table.items():
      for et, a in by_type.items():
        if isinstance(a, Alert):
          out.append((names[ev], et, a))
        elif names[ev] == HM.CONFIRM_EVENT:
          out += [(names[ev], et, x) for x in pre_active_alerts()]
  return out


LISTED = HM.COMPACT_LIMIT | HM.COMPACT_DISENGAGE | HM.COMPACT_TURN
VISUALS = [v for k, v in HM.VisualAlert.schema.enumerants.items() if k != 'none']   # fcw, steerRequired, ...


class TestCompactAlerts(OpenpilotTestCase):
  def test_the_lists(self):
    assert HM.COMPACT_LIMIT == {'speedLimitPreActive', 'speedLimitActive', 'speedLimitChanged', 'speedLimitPending'}
    assert HM.COMPACT_QUIET == HM.COMPACT_LIMIT - {HM.CONFIRM_EVENT}
    assert HM.COMPACT_DISENGAGE == {'manualLongitudinalRequired', 'manualSteeringRequired'}
    assert HM.COMPACT_TURN == {'laneTurnLeft', 'laneTurnRight'}
    names = {n for n, _, _ in every_alert()}
    assert LISTED <= names, f"a listed event no longer exists: {LISTED - names}"
    assert set(HM.COMPACT_TYPES) == LISTED

  def test_SAFETY_each_listed_name_has_exactly_its_recorded_type(self):
    # the allow-list is keyed by the full alert type: an upstream table giving a listed name a new type (a '/noEntry'
    # 'openpilot Unavailable' is normal / LOW / none too) must fail here, not go compact unnoticed
    from openpilot.selfdrive.selfdrived.events import EVENTS, EVENT_NAME
    from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP, EVENT_NAME_SP
    types = defaultdict(set)
    for table, names in ((EVENTS, EVENT_NAME), (EVENTS_SP, EVENT_NAME_SP)):
      for ev, by_type in table.items():
        if names[ev] in LISTED:
          types[names[ev]] |= set(by_type)
    assert {n: {t} for n, t in HM.COMPACT_TYPES.items()} == dict(types), dict(types)

  def test_SAFETY_a_new_type_of_a_listed_name_is_drawn_as_stock(self):
    from openpilot.sunnypilot.selfdrive.selfdrived.events_base import ET, NoEntryAlert
    ets = [v for k, v in vars(ET).items() if k.isupper()]
    assert len(ets) == 10, ets
    for name in LISTED:
      for et in ets:
        a = SimpleNamespace(alert_type=f"{name}/{et}", status=0, visual_alert=0, text1="Some Text", text2="")
        if et == HM.COMPACT_TYPES[name]:
          assert HM.compact_kind(a, HS.ALL_ON) != HM.COMPACT_NONE, f"{name}/{et}: the recorded type is compact"
        else:
          assert HM.compact_kind(a, HS.ALL_ON) == HM.COMPACT_NONE, f"{name}/{et}: a new type is drawn as stock"
      # a no-entry under the recorded type itself, either way round (events_base swaps its lines on the comma 4)
      ne = NoEntryAlert("Speed Limit Assist Active")
      for t1, t2 in ((ne.alert_text_1, ne.alert_text_2), (ne.alert_text_2, ne.alert_text_1)):
        a = SimpleNamespace(alert_type=f"{name}/{HM.COMPACT_TYPES[name]}", status=int(ne.alert_status),
                            visual_alert=int(ne.visual_alert), text1=t1, text2=t2)
        assert HM.compact_kind(a, HS.ALL_ON) == HM.COMPACT_NONE, f"{name}: {t1!r} / {t2!r}"

  def test_SAFETY_every_listed_alert_is_normal_low_and_asks_for_no_visual(self):
    from openpilot.sunnypilot.selfdrive.selfdrived.events_base import AlertStatus, Priority, VisualAlert
    seen = set()
    for name, et, a in every_alert():
      if name not in LISTED:
        continue
      seen.add(name)
      assert a.alert_status == AlertStatus.normal, f"{name}/{et}: {a.alert_status}"
      assert a.priority <= Priority.LOW, f"{name}/{et}: {a.priority}"
      assert a.visual_alert == VisualAlert.none, f"{name}/{et}: {a.visual_alert}"
    assert seen == LISTED

  def test_SAFETY_no_critical_prompt_or_steer_required_alert_is_ever_compact(self):
    # every alert of every event, with every compact setting on: compact only if listed AND normal / LOW / none
    from openpilot.sunnypilot.selfdrive.selfdrived.events_base import AlertStatus, Priority, VisualAlert
    n_compact = n_stock = 0
    for name, et, a in every_alert():
      kind = HM.compact_kind(ui_alert(name, et, a), HS.ALL_ON)
      if kind != HM.COMPACT_NONE:
        n_compact += 1
        assert name in LISTED and a.alert_status == AlertStatus.normal and a.priority <= Priority.LOW and \
          a.visual_alert == VisualAlert.none, f"{name}/{et} would be compact"
      else:
        n_stock += 1
        assert name not in LISTED, f"{name}/{et}: listed, yet drawn as stock"
      if a.alert_status != AlertStatus.normal or a.visual_alert != VisualAlert.none or a.priority > Priority.LOW:
        assert kind == HM.COMPACT_NONE, f"{name}/{et}: {a.alert_status} {a.visual_alert} {a.priority}"
    assert n_compact > 10 and n_stock > 100, (n_compact, n_stock)
    # by name too: AEB, FCW, take control, steer required, the disengage-immediately ones, the gateway's own
    for name, et, a in every_alert():
      if name in ('aeb', 'stockAeb', 'fcw', 'stockFcw', 'steerSaturated', 'controlsMismatchLateral', 'steerUnavailable',
                  'driverDistracted3', 'driverUnresponsive3', 'manualRestart', 'speedTooLow', 'pedalPressedAlertOnly',
                  'lkasGatewayEpsLatched', 'lkasGatewayEpsLatchedReminder', 'vsaFault', 'vsaFaultAnnounce'):
        assert HM.compact_kind(ui_alert(name, et, a), HS.ALL_ON) == HM.COMPACT_NONE, f"{name}/{et}"

  def test_SAFETY_a_listed_name_raised_otherwise_is_drawn_as_stock(self):
    # an upstream table change tomorrow: the same name, but critical, a prompt, or a visual - stock
    for name in LISTED:
      for status in (1, 2):   # userPrompt, critical
        assert HM.compact_kind(SimpleNamespace(alert_type=f"{name}/{HM.COMPACT_TYPES[name]}", status=status,
                                               visual_alert=0), HS.ALL_ON) == HM.COMPACT_NONE, (name, status)
      for visual in VISUALS:
        assert HM.compact_kind(SimpleNamespace(alert_type=f"{name}/{HM.COMPACT_TYPES[name]}", status=0,
                                               visual_alert=visual), HS.ALL_ON) == HM.COMPACT_NONE, (name, visual)

  def test_SAFETY_the_uis_own_critical_alerts_are_never_compact(self):
    # ALERT_CRITICAL_TIMEOUT / _REBOOT and 'waiting to start' carry no event name
    src = ALERTS.read_text(encoding="utf-8")
    for const in ("ALERT_STARTUP_PENDING", "ALERT_CRITICAL_TIMEOUT", "ALERT_CRITICAL_REBOOT"):
      body = src[src.index(f"{const} = Alert("):]
      body = body[:body.index(")\n")]
      assert "alert_type" not in body, const
    for status in (0, 2):
      assert HM.compact_kind(SimpleNamespace(alert_type="", status=status, visual_alert=0), HS.ALL_ON) == HM.COMPACT_NONE

  def test_each_group_has_its_own_setting(self):
    def kind(name, s):
      return HM.compact_kind(SimpleNamespace(alert_type=f"{name}/{HM.COMPACT_TYPES.get(name, 'warning')}", status=0,
                                             visual_alert=0), s)
    for name in HM.COMPACT_LIMIT:
      assert kind(name, HS.ALL_ON) == HM.COMPACT_LIMIT_KIND and kind(name, with_(compact_limit=False)) == HM.COMPACT_NONE
      assert kind(name, with_(compact_disengage=False, compact_turn=False)) == HM.COMPACT_LIMIT_KIND
    for name in HM.COMPACT_DISENGAGE:
      assert kind(name, HS.ALL_ON) == HM.COMPACT_DISENGAGE_KIND
      assert kind(name, with_(compact_disengage=False)) == HM.COMPACT_NONE
    for name in HM.COMPACT_TURN:
      assert kind(name, HS.ALL_ON) == HM.COMPACT_TURN_KIND and kind(name, with_(compact_turn=False)) == HM.COMPACT_NONE
    for name in LISTED:
      assert kind(name, HS.ALL_OFF) == HM.COMPACT_NONE
    assert kind("manualRestart", HS.ALL_ON) == HM.COMPACT_NONE, "the standstill banner is its own (HudStoppedBanner)"

  def test_only_a_set_speed_change_frees_the_max_number(self):
    def a(name):
      return SimpleNamespace(alert_type=f"{name}/{HM.COMPACT_TYPES.get(name, 'warning')}", status=0, visual_alert=0)
    for name in HM.COMPACT_QUIET:
      assert HM.frees_top_icons(a(name), HS.ALL_ON) and not HM.frees_top_icons(a(name), with_(compact_limit=False))
    for name in (HM.CONFIRM_EVENT, *HM.COMPACT_DISENGAGE, *HM.COMPACT_TURN, "steerSaturated"):
      assert not HM.frees_top_icons(a(name), HS.ALL_ON), "a banner and the MAX number would share the top left"

  def test_banner_words(self):
    def a(name, t1="x", t2=""):
      return SimpleNamespace(alert_type=f"{name}/warning", text1=t1, text2=t2)
    assert HM.compact_text(a('manualLongitudinalRequired')) == ("CRUISE OFF", "manual speed control")
    assert HM.compact_text(a('manualSteeringRequired')) == ("LANE CENTERING OFF", "manual steering")
    assert HM.compact_text(a('laneTurnRight')) == ("TURNING RIGHT", "")
    assert HM.compact_text(a('other', "Some Text", "Line Two")) == ("Some Text", "line two")
    assert set(HM.COMPACT_TEXT) == HM.COMPACT_DISENGAGE | HM.COMPACT_TURN
    assert HM.confirm_text("Press + to confirm speed limit") == "press + to confirm"
    assert HM.confirm_text("Press - to confirm speed limit") == "press - to confirm"
    assert HM.confirm_text("Speed Limit Assist: set to 60 km/h to engage") == "speed limit assist: set to 60 km/h to engage"
    assert HM.confirm_lower("Press - to confirm speed limit") is True
    assert HM.confirm_lower("Press + to confirm speed limit") is False
    assert HM.confirm_lower("Speed Limit Assist: set to 60 km/h to engage") is None


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
    assert keys[:3] == [HS.PARAM_STOPPED_TIMER, HS.PARAM_STOPPED_BANNER, HS.PARAM_APPROACHING_LIMIT]

  def test_widgets_match_the_param_types(self):
    _, sec = hud_section()
    for it in sec["items"]:
      assert it["widget"] == "toggle", it["key"]
      assert it.get("title") and it.get("description"), it["key"]
      assert "visibility" not in it, f"{it['key']}: every setting stands alone now"

  def test_shown_for_a_comma_4_only(self):
    _, sec = hud_section()
    assert sec["visibility"] == [{"type": "capability", "field": "device_type", "equals": "mici"}]

  def test_the_approaching_limit_says_what_it_does(self):
    _, sec = hud_section()
    by_key = {it["key"]: it for it in sec["items"]}
    a = by_key[HS.PARAM_APPROACHING_LIMIT]
    assert a["title"] == "Approaching Limit"
    for words in ("lower or higher", "15 seconds or 500 m", "tunnel", "now", "face"):
      assert words in a["description"], words
    assert "flashes twice" in by_key[HS.PARAM_COMPACT_LIMIT]["description"]

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
                       "hud_rail import HudRail", "self._hud_rail = HudRail(", "self._hud_rail.render(",
                       "import hud_alerts", "hud_alerts.frees_top_icons("))
    marked(BALL, ("self.hud_floor_y = -math.inf", "max(dot_height, self.hud_floor_y)"), window=0)
    marked(ALERTS, ("import hud_alerts", "hud_alerts.draw_compact("))
    marked(PARAMS_KEYS, [f'"{k}"' for k in HS.ALL_PARAMS], window=12)
    marked(VISUALS_YAML, ("id: hud_comma4",))
    marked(MICI_SETTINGS, ("import cloudlog", "def gateway_icon():", "icons_mici/gateway.png", "gateway_icon())"), window=5)
    marked(REPO / ".gitattributes", ("icons_mici/gateway.png -filter",))
    marked(HUD_RENDERER_SP, ("import hud_alerts, hud_draw", "pending_max.drawer = True", "self._draw_pending_max(rect)",
                             "def drawing_top_icons", "def _draw_pending_max", "standstill_banner.observe(",
                             "self._draw_approach(rect)", "def _draw_approach", "def _approach_blocker"))

  def test_the_hooks_fall_through_to_the_stock_drawing(self):
    src = ALERTS.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self, rect"):src.index("  def _draw_icons")]
    hook = render.index("hud_alerts.draw_compact(self, alert)")
    assert render.index("self._draw_background(alert)") > hook, "the stock drawing still runs after the hook"
    assert "SpeedLimitAlertRenderer.update(self)\n      return True" in render[hook:], "the banner keeps the confirm fade running"
    road = ROAD_VIEW.read_text(encoding="utf-8")
    assert "set_can_draw_top_icons(alert_to_render is None or hud_alerts.frees_top_icons(alert_to_render))" in road, \
      "no alert: the stock answer, untouched"
    assert road.index("self._hud_cluster.render(") < road.index("self._alert_renderer.render(self._content_rect)"), \
      "the cluster is drawn under the alerts"
    assert road.index("self._hud_cluster.render(") > road.index("rl.begin_scissor_mode("), "inside the content rect"
    rail, ball = road.index("self._hud_rail.render(self.rect)"), road.index("self._confidence_ball.render(self.rect)")
    assert road.index("rl.end_scissor_mode()") < rail < ball, "the rail is in the ball's strip, and sets its floor first"
    src = BALL.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self"):]
    assert render.index("dot_height = self._rect.y + dot_height") < render.index("max(dot_height, self.hud_floor_y)") < \
      render.index("draw_circle_gradient("), "the floor applies to the stock position, before it is drawn"

  def test_the_old_confirm_drawing_is_gone(self):
    # round 5 (D0.3): the full-screen confirm is sunnypilot's own again - no hook in _draw_icons, no drawing behind it
    src = ALERTS.read_text(encoding="utf-8")
    icons = src[src.index("  def _draw_icons"):src.index("  def _draw_background")]
    assert "hud_alerts" not in icons and "FORK" not in icons
    assert "draw_pending_limit" not in HUD_ALERTS.read_text(encoding="utf-8")
    draw = (Path(HM.__file__).parent / "hud_draw.py").read_text(encoding="utf-8")
    for gone in ("def pending_icon(", "def school_cue(", "def next_row(", "electronic", "LAMP_"):
      assert gone not in draw, gone
    assert not hasattr(HM, "confirm_pending") and not hasattr(HM, "SignSlot")

  def test_the_standstill_banner_is_observed_every_frame(self):
    # deletion trap 1: the cluster used to call observe(); without it the first departure of a drive would keep the
    # full take-control prompt for the rest of the drive. HudRendererSP renders every frame, with or without an alert.
    src = HUD_RENDERER_SP.read_text(encoding="utf-8")
    upd = src[src.index("  def _update_state(self)"):src.index("  def _render(self")]
    assert "hud_alerts.standstill_banner.observe(self._frame_alert()[0])" in upd
    assert "observe(" not in HUD_CLUSTER.read_text(encoding="utf-8"), "one place calls it"
    # deletion trap 2: the cluster still hands the alert renderer to the HUD renderer (the box's top-icon question)
    cluster = HUD_CLUSTER.read_text(encoding="utf-8")
    assert "hud_renderer.alert_renderer = alert_renderer" in cluster
    # the frame's alert is looked up once a frame (hud_alerts.FrameAlert), for every fork question, and forgotten once
    # the HUD renderer - the last to ask - has drawn
    for path in (HUD_RENDERER_SP, HUD_CLUSTER):
      assert "will_render(" not in path.read_text(encoding="utf-8"), f"{path.name}: through hud_alerts.frame_alert"
    assert HUD_ALERTS.read_text(encoding="utf-8").count("ar.will_render()") == 1
    render = src[src.index("  def _render(self"):src.index("  def _has_blind_spot_detected")]
    assert render.rstrip().endswith("hud_alerts.frame_alert.clear()  # FORK(HUD): the last to ask this frame")

  def test_the_frame_alert_is_looked_up_once_a_frame(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_alerts import FrameAlert
    from openpilot.selfdrive.ui.ui_state import ui_state

    class AR:
      def __init__(self):
        self.n = 0
        self.answer: tuple = ("a", False)

      def will_render(self):
        self.n += 1
        return self.answer
    fa, ar = FrameAlert(), AR()
    assert fa.get(None) == (None, False)
    assert fa.get(ar) == ("a", False) and fa.get(ar) == ("a", False) and ar.n == 1, "once a frame"
    ar.answer = (None, True)   # the alert renderer drew and ended the fade: later askers still see the frame's answer
    assert fa.get(ar) == ("a", False) and ar.n == 1
    fa.clear()
    assert fa.get(ar) == (None, True) and ar.n == 2, "the next frame asks again"
    ui_state.sm.frame += 1
    ar.answer = ("b", False)
    assert fa.get(ar) == ("b", False) and ar.n == 3, "a new SubMaster frame too"

  def test_the_confirms_target_is_drawn_by_the_sunnypilot_subclass(self):
    # design A needs no upstream change: the alert renderer publishes, the subclass draws after the stock HUD
    assert "FORK" not in HUD_RENDERER.read_text(encoding="utf-8"), "upstream's HUD renderer is untouched"
    src = HUD_RENDERER_SP.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self"):src.index("  def _has_blind_spot_detected")]
    assert render.index("super()._render(rect)") < render.index("self._draw_pending_max(rect)") < \
      render.index("self._draw_approach(rect)"), "over the stock HUD"
    assert "hud_alerts.pending_max.drawer = True" in src, "without the subclass the confirm stays the banner"
    tops = src[src.index("  def drawing_top_icons"):src.index("  def _box_top_icon")]
    assert "super().drawing_top_icons() or self._box_top_icon() or self._approach_top_icon()" in tops, \
      "top icons as the stock MAX number is"
    blocked = src[src.index("  def _approach_blocker"):src.index("  def _approach_top_icon")]
    for who in ("self._frame_alert()[0]", "frees_top_icons(alert)", "self._pending is not None",
                "self._set_speed_alpha_filter.x > 1e-2"):
      assert who in blocked, f"the coming limit gives way to: {who}"
    draw = src[src.index("  def _draw_approach"):]
    assert "self._approach_alpha.x * (1.0 - self._set_speed_alpha_filter.x)" in draw, "it cross-fades with MAX"
    road = ROAD_VIEW.read_text(encoding="utf-8")
    assert road.index("self._alert_renderer.render(self._content_rect)") < \
      road.index("self._hud_renderer.render(self._content_rect)"), "the alerts publish before the HUD draws, each frame"
    alerts = HUD_ALERTS.read_text(encoding="utf-8")
    confirm = alerts[alerts.index("def _draw_confirm("):alerts.index("def draw_compact_standstill(")]
    assert confirm.index("pending_max.publish(") < confirm.index("return\n") < confirm.index("hd.confirm_banner("), \
      "the box or the banner, never both"


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
