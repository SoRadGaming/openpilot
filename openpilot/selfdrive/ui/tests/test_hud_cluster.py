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
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (HudFrame, StandstillBanner, build_frame, next_window_m,
                                                                     pending_limit, short_line2, SCHOOL_ACTIVE,
                                                                     SCHOOL_INACTIVE, SCHOOL_NONE)

ROOT = Path(__file__).parents[3]   # openpilot/
PARAMS_KEYS = ROOT / "common/params_keys.h"
ROAD_VIEW = ROOT / "selfdrive/ui/mici/onroad/augmented_road_view.py"
ALERTS = ROOT / "selfdrive/ui/mici/onroad/alert_renderer.py"
MICI_SETTINGS = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/settings.py"
BOARD = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/board.py"
VISUALS_YAML = ROOT / "sunnypilot/sunnylink/settings_ui_src/pages/visuals.yaml"
SETTINGS_JSON = ROOT / "sunnypilot/sunnylink/settings_ui.json"
GATEWAY_ICON = ROOT / "sunnypilot/selfdrive/assets/icons_mici/gateway.png"
KPH = 3.6

DEFAULTS = {HS.PARAM_SPEED_CLUSTER: True, HS.PARAM_NEXT_LIMIT: HS.NEXT_BOTH, HS.PARAM_SCHOOL_CUE: True,
            HS.PARAM_VARIABLE_SIGN: True, HS.PARAM_STOPPED_TIMER: True, HS.PARAM_STOPPED_BANNER: True,
            HS.PARAM_CONFIRM_LIMIT: True}


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
          ahead_valid=True, nsw_mode=2, nsw_limit=None, school=0, variable=False, standstill=False, v_cluster=None,
          lp=True, lmd=True, lmd_alive=True, lp_alive=True):
  sm = FakeSM()
  cs = sm.put('carState')
  cs.vEgo = v
  cs.vEgoCluster = v if v_cluster is None else v_cluster
  cs.standstill = standstill
  if lp:
    res = sm.put('longitudinalPlanSP', alive=lp_alive).speedLimit.resolver
    res.speedLimit = res.speedLimitLast = res.speedLimitFinal = res.speedLimitFinalLast = limit / KPH
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
    z.state = 2
    z.speedLimit = (limit if nsw_limit is None else nsw_limit) / KPH
    z.schoolZone = school
    z.variable = variable
  return sm


def frame(sm, settings=HS.ALL_ON, *, metric=True, sl_mode_on=True, stopped_s=None, cluster_seen=True, max_visible=False,
          started_frame=5) -> HudFrame:
  return build_frame(sm, settings, started_frame=started_frame, is_metric=metric, speed_limit_mode_on=sl_mode_on,
                     stopped_s=stopped_s, v_ego_cluster_seen=cluster_seen, max_visible=max_visible)


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
              HS.PARAM_STOPPED_BANNER: "stopped_banner", HS.PARAM_CONFIRM_LIMIT: "confirm_limit"}
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


# ------------------------------------------------------------------------------------------- the alerts' rules
def alert(kind="manualRestart/warning", text1="TAKE CONTROL", text2="Resume Driving Manually"):
  return SimpleNamespace(alert_type=kind, text1=text1, text2=text2)


class TestAlertRules(OpenpilotTestCase):
  def test_the_standstill_prompt_is_compact_with_the_setting_on(self):
    b, sm = StandstillBanner(), FakeSM()
    assert b.compact(alert(), HS.ALL_ON, sm)
    assert not b.compact(alert(), with_(stopped_banner=False), sm)
    for other in ("speedLimitPreActive/warning", "steerSaturated/warning", "resumeRequired/warning", ""):
      assert not b.compact(alert(kind=other), HS.ALL_ON, sm), other
    assert not b.compact(None, HS.ALL_ON, sm)

  def test_the_full_prompt_returns_when_the_car_ahead_moves_off_and_stays(self):
    b, sm = StandstillBanner(), FakeSM()
    lead = sm.put('radarState').leadOne
    lead.present, lead.vLead = True, 0.2
    assert b.compact(alert(), HS.ALL_ON, sm), "a stopped car ahead"
    lead.vLead = 1.5
    assert not b.compact(alert(), HS.ALL_ON, sm), "it moves off: the full alert"
    lead.vLead = 0.0
    assert not b.compact(alert(), HS.ALL_ON, sm), "and it stays full until the prompt clears"
    assert not b.compact(alert(), HS.ALL_ON, sm), "idempotent: the cluster and the renderer both ask"
    assert not b.compact(None, HS.ALL_ON, sm)
    assert b.compact(alert(), HS.ALL_ON, sm), "the next stop starts compact again"
    lead.present, lead.vLead = False, 5.0
    assert b.compact(alert(), HS.ALL_ON, sm), "no car ahead: nothing to move off"

  def test_second_line(self):
    assert short_line2("Resume Driving Manually") == "resume manually"
    assert short_line2("Something Else") == "something else"

  def test_the_pending_limit(self):
    sm = drive(limit=50)
    assert pending_limit(sm, True) == 50
    assert pending_limit(sm, False) == round(50 / KPH * 2.23694)
    assert pending_limit(FakeSM(), True) == 0


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
    for key in (HS.PARAM_SPEED_CLUSTER, HS.PARAM_STOPPED_TIMER, HS.PARAM_STOPPED_BANNER, HS.PARAM_CONFIRM_LIMIT):
      assert "visibility" not in by_key[key], f"{key} works without the cluster"


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
    marked(ROAD_VIEW, ("hud_cluster import HudCluster", "self._hud_cluster = HudCluster(", "self._hud_cluster.render("))
    marked(ALERTS, ("import hud_alerts", "hud_alerts.draw_compact_standstill(", "hud_alerts.draw_pending_limit("))
    marked(PARAMS_KEYS, [f'"{k}"' for k in HS.ALL_PARAMS], window=10)
    marked(VISUALS_YAML, ("id: hud_comma4",))
    marked(MICI_SETTINGS, ("icons_mici/gateway.png",), window=5)

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


# ------------------------------------------------------------------------------------------- the gateway icon
class TestGatewayIcon(OpenpilotTestCase):
  def test_the_tile_uses_icon_b_and_the_dialog_keeps_the_arrow(self):
    settings = MICI_SETTINGS.read_text(encoding="utf-8")
    tile = settings[settings.index('board_btn = SettingsBigButton(tr("gateway")'):settings.index("board_btn.set_click_callback")]
    assert "icons_mici/gateway.png" in tile and "icon_software.png" not in tile
    assert "icon_software.png" in BOARD.read_text(encoding="utf-8"), "the update dialog describes the action: an arrow"

  def test_the_asset(self):
    data = GATEWAY_ICON.read_bytes()
    if data.startswith(b"version https://git-lfs"):
      self.skipTest("git-lfs pointer: the image itself is not checked out here")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (w, h) == (128, 88), "128 on the long side, like the rest of icons_mici"
    assert data[24] == 8 and data[25] == 6, "8-bit RGBA: white on transparent"
