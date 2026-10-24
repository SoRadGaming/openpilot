"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the radar dots (radar_dots.py, push13) - HudRadarDots, off by default, on the comma 4's toggles page and in
sunnylink Visuals > HUD; what radard made of each track (lead, second lead, held, the rest); where a track lands on the
screen; that with the toggle off no socket is opened and nothing is laid out or drawn, and that on it lays the dots out
again only when an input changed, and draws only what this frame laid out. The pixels, in a headless raylib child
process: the real mici ModelRenderer - nothing at all with the toggle off, the dots where the projection puts them with
it on, and each dot's dark rim on a light 'sky'; and the real mici AugmentedRoadView - tracks 8 and 9 m away, under its
bottom fade's dark rows, drawn over the fade at full strength, and nothing else on the screen changed.

The settings pages are parsed, never imported (they pull in the whole mici UI).
"""
import ast
import dataclasses
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import numpy as np

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import radar_dots as RD
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_id

ROOT = Path(__file__).parents[3]   # openpilot/
PARAMS_KEYS = ROOT / "common/params_keys.h"
TOGGLES = ROOT / "selfdrive/ui/mici/layouts/settings/toggles.py"
MODEL_RENDERER = ROOT / "selfdrive/ui/mici/onroad/model_renderer.py"
ROAD_VIEW = ROOT / "selfdrive/ui/mici/onroad/augmented_road_view.py"
RADARD = ROOT / "selfdrive/controls/radard.py"
UI_STATE = ROOT / "selfdrive/ui/ui_state.py"
VISUALS_YAML = ROOT / "sunnypilot/sunnylink/settings_ui_src/pages/visuals.yaml"
SETTINGS_JSON = ROOT / "sunnypilot/sunnylink/settings_ui.json"

KEY = HS.PARAM_RADAR_DOTS
W, H = 536, 240
F, CX, CY = 300.0, 268.0, 60.0
# a pinhole camera at the model's origin, car space x forward / y right / z down: u = CX + F y / x, v = CY + F z / x
PINHOLE = np.array([[CX, F, 0.0], [CY, 0.0, F], [1.0, 0.0, 0.0]], dtype=np.float32)
FLAT = np.stack([np.linspace(0.0, 120.0, 33), np.zeros(33), np.zeros(33)], axis=1).astype(np.float32)
RECT = SimpleNamespace(x=0.0, y=0.0, width=float(W), height=float(H))


def tracks_msg(*pts, valid=True):
  """A radarTracks message: pts as (trackId, dRel, yRel)."""
  m = messaging.new_message("radarTracks")
  m.valid = valid
  out = m.radarTracks.init("points", len(pts))
  for p, (tid, d, y) in zip(out, pts, strict=True):
    p.trackId, p.dRel, p.yRel = tid, d, y
  return m.as_reader()


def radar_state(one=None, two=None):
  """radarState with leadOne / leadTwo on the given radarTrackId (None: no lead)."""
  m = messaging.new_message("radarState")
  for lead, tid in ((m.radarState.leadOne, one), (m.radarState.leadTwo, two)):
    if tid is not None:
      lead.present, lead.radarTrackId, lead.dRel = True, tid, 30.0
  return m.as_reader().radarState


def expect(d, y, z=0.0, z_offset=1.2):
  x = d + RD.RADAR_TO_CAMERA
  return CX + F * (-y) / x, CY + F * (z + z_offset) / x


class FakeSettings:
  def __init__(self, on):
    self.on = on
    self.gets = 0

  def get(self, force=False):
    self.gets += 1
    return dataclasses.replace(HS.ALL_ON, radar_dots=self.on)


class FakeSock:
  """TracksSocket's surface: update(now) takes the message fed since the last update, as the conflated socket would;
  tracks is then the same object until the next message."""
  def __init__(self):
    self.pending = None
    self.tracks, self.valid, self.recv_t = None, False, -math.inf
    self.updates = 0

  def feed(self, msg):
    self.pending = msg

  def update(self, now):
    self.updates += 1
    if self.pending is not None:
      self.tracks, self.valid, self.recv_t = self.pending.radarTracks, self.pending.valid, now
      self.pending = None


def dots_of(on=True, msg=None, clock=None):
  sock, made = FakeSock(), []
  dots = RD.RadarDots(settings=cast(Any, FakeSettings(on)), sock_factory=lambda: made.append(sock) or sock,
                      clock=clock or (lambda: 100.0))
  if msg is not None:
    sock.feed(msg)
  return dots, sock, made


def step(dots, rs=None, transform=PINHOLE, path=FLAT, rect=RECT):
  return dots.update(rect, transform, path, 1.2, 0.0, rs)


def _param_entry(key):
  m = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}\}', PARAMS_KEYS.read_text())
  assert m, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
  return m.group(1)


def marked(path: Path, needles, window=3):
  lines = path.read_text(encoding="utf-8").splitlines()
  for needle in needles:
    idx = next((i for i, line in enumerate(lines) if needle in line), None)
    assert idx is not None, f"{path.name}: {needle!r} not found"
    assert any("FORK(HUD)" in line for line in lines[max(0, idx - window):idx + 1]), \
      f"{path.name}:{idx + 1}: {needle!r} has no FORK(HUD) marker"


# ------------------------------------------------------------------------------------------- the setting
class TestRadarDotsSetting(OpenpilotTestCase):
  def test_registered_backed_up_and_off_by_default(self):
    flags = _param_entry(KEY)
    assert "PERSISTENT" in flags and "BACKUP" in flags and "BOOL" in flags
    assert '"0"' in flags, "a diagnostic: off unless the owner turns it on"
    assert Params().get(KEY, return_default=True) is False
    assert HS.read_settings(Params()).radar_dots is False
    assert HS.ALL_ON.radar_dots is False and HS.ALL_OFF.radar_dots is False, "not one of the HUD pieces that default on"
    assert KEY in HS.KEYS and KEY not in HS.FIELDS

  def test_it_switches_the_dots_only(self):
    params = Params()
    try:
      params.put_bool(KEY, True, block=True)
      assert HS.read_settings(params) == dataclasses.replace(HS.ALL_ON, radar_dots=True)
    finally:
      params.put_bool(KEY, False, block=True)
    assert HS.read_settings(params) == HS.ALL_ON

  def test_unreadable_is_off(self):
    class Broken:
      def get(self, key, block=False, return_default=False):
        raise RuntimeError("UnknownKeyName")
    assert HS.read_settings(Broken()).radar_dots is False

  def test_read_with_the_hud_at_most_once_a_second(self):
    t, v = [0.0], {KEY: False}

    class P:
      def get(self, key, block=False, return_default=False):
        return v.get(key, True if key in HS.FIELDS else None)
    r = HS.HudSettingsReader(params_fn=P, clock=lambda: t[0])
    assert r.get().radar_dots is False
    v[KEY] = True
    t[0] = 0.5
    assert r.get().radar_dots is False and r.reads == 1
    t[0] = 1.0
    assert r.get().radar_dots is True and r.reads == 2

  def test_on_the_comma_4_toggles_page(self):
    src = TOGGLES.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "BigParamControl"]
    ours = [c for c in calls if [ast.literal_eval(a) for a in c.args] == ["radar dots", KEY]]
    assert len(ours) == 1, "one 'radar dots' toggle on HudRadarDots"
    assert not ours[0].keywords, "no restart, no callback: the road view re-reads it within a second"
    assert "      radar_dots_toggle,  # FORK(HUD)\n" in src, "in the page's list"
    assert f'      ("{KEY}", radar_dots_toggle),  # FORK(HUD)\n' in src, "refreshed with the others (sunnylink may set it)"
    marked(TOGGLES, ('BigParamControl("radar dots"',), window=2)

  def test_in_sunnylink_visuals_hud(self):
    import yaml
    ui = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    panel = next(p for p in ui["panels"] if p["id"] == "visuals")
    sec = next(s for s in panel["sections"] if s["id"] == "hud_comma4")
    item = next(it for it in sec["items"] if it["key"] == KEY)
    src = yaml.safe_load(VISUALS_YAML.read_text(encoding="utf-8"))
    hud = next(s for s in src["sections"] if s["id"] == "hud_comma4")
    assert item == next(it for it in hud["items"] if it["key"] == KEY), "settings_ui.json is stale"
    assert item["widget"] == "toggle" and item["title"] == "Radar Dots"
    for words in ("radar", "White", "cyan", "ring", "Off by default", "changes nothing"):
      assert words in item["description"], words
    assert sum(1 for p in ui["panels"] for s in p["sections"] for it in s.get("items", []) if it.get("key") == KEY) == 1
    marked(VISUALS_YAML, ("key: HudRadarDots",), window=1)
    marked(PARAMS_KEYS, (f'"{KEY}"',), window=2)


# ------------------------------------------------------------------------------------------- what radard made of a track
class TestLeadMarks(OpenpilotTestCase):
  def test_the_leads_tracks(self):
    assert RD.lead_marks(None) == {}
    assert RD.lead_marks(radar_state()) == {}
    assert RD.lead_marks(radar_state(one=7)) == {7: RD.LEAD}
    assert RD.lead_marks(radar_state(one=7, two=9)) == {7: RD.LEAD, 9: RD.LEAD2}
    assert RD.lead_marks(radar_state(one=7, two=7)) == {7: RD.LEAD}, "one track, the first lead's mark"

  def test_a_vision_only_lead_marks_nothing(self):
    assert RD.lead_marks(radar_state(one=-1, two=-1)) == {}

  def test_a_held_lead_marks_the_track_it_holds_on(self):
    assert held_id(42) == -44
    assert RD.lead_marks(radar_state(one=held_id(42))) == {42: RD.HELD}
    assert RD.lead_marks(radar_state(one=held_id(0), two=5)) == {0: RD.HELD, 5: RD.LEAD2}

  def test_a_lead_not_present_marks_nothing(self):
    rs = messaging.new_message("radarState")
    rs.radarState.leadOne.radarTrackId = 3   # present stays False
    assert RD.lead_marks(rs.as_reader().radarState) == {}


# ------------------------------------------------------------------------------------------- where a dot lands
class TestLayout(OpenpilotTestCase):
  def lay(self, *pts, marks=None, transform=PINHOLE, path=FLAT, z_offset=1.2, camera_offset=0.0, rect=RECT):
    return RD.layout(tracks_msg(*pts).radarTracks.points, marks or {}, transform, path, z_offset, camera_offset, rect)

  def test_the_radar_is_ahead_of_the_camera(self):
    with open(RADARD) as f:
      m = re.search(r"^RADAR_TO_CAMERA = ([0-9.]+)", f.read(), re.M)
    assert m and float(m.group(1)) == RD.RADAR_TO_CAMERA

  def test_on_the_road_under_the_track(self):
    (dot,) = self.lay((1, 20.0, 0.0))
    ex, ey = expect(20.0, 0.0)
    assert math.isclose(dot.x, ex, abs_tol=1e-3) and math.isclose(dot.y, ey, abs_tol=1e-3), (dot, ex, ey)
    assert dot.track_id == 1 and dot.mark == RD.OTHER and dot.d_rel == 20.0
    left, right = self.lay((1, 20.0, 2.0)), self.lay((1, 20.0, -2.0))
    assert left[0].x < dot.x < right[0].x, "yRel is left positive; screen x grows to the right"
    assert math.isclose(left[0].x, expect(20.0, 2.0)[0], abs_tol=1e-3)
    far = self.lay((1, 60.0, 0.0))[0]
    assert far.y < dot.y, "further is higher up the screen"

  def test_the_path_height_and_the_camera_offset(self):
    hill = FLAT.copy()
    hill[:, 2] = np.linspace(0.0, -6.0, 33)   # z down: the road rises ahead
    (dot,) = self.lay((1, 40.0, 0.0), path=hill)
    z = float(np.interp(40.0 + RD.RADAR_TO_CAMERA, hill[:, 0], hill[:, 2]))
    assert math.isclose(dot.y, expect(40.0, 0.0, z=z)[1], abs_tol=1e-3)
    (shifted,) = self.lay((1, 20.0, 0.0), camera_offset=0.5)
    assert math.isclose(shifted.x, CX + F * 0.5 / (20.0 + RD.RADAR_TO_CAMERA), abs_tol=1e-3), "as the path is shifted"
    (no_path,) = self.lay((1, 20.0, 0.0), path=np.empty((0, 3), dtype=np.float32))
    assert math.isclose(no_path.y, expect(20.0, 0.0)[1], abs_tol=1e-3), "no path yet: flat"

  def test_the_rect_origin_is_added(self):
    rect = SimpleNamespace(x=10.0, y=5.0, width=float(W), height=float(H))
    (dot,) = self.lay((1, 20.0, 0.0), rect=rect)
    assert math.isclose(dot.x, expect(20.0, 0.0)[0] + 10.0, abs_tol=1e-3)
    assert math.isclose(dot.y, expect(20.0, 0.0)[1] + 5.0, abs_tol=1e-3)

  def test_no_dot_where_there_is_nothing_to_draw(self):
    bad = [(1, float("nan"), 0.0), (2, 20.0, float("inf")), (3, 0.0, 0.0), (4, -5.0, 0.0),   # not finite, not ahead
           (5, 20.0, -40.0), (6, 20.0, 40.0)]                                                   # off the screen's sides
    assert self.lay(*bad) == []
    assert self.lay((1, 20.0, 0.0), transform=np.zeros((3, 3), dtype=np.float32)) == [], "no calibration yet"
    assert len(self.lay((1, 20.0, 0.0), *bad)) == 1

  def test_the_marked_are_drawn_last_and_bigger(self):
    dots = self.lay((1, 15.0, 0.0), (2, 30.0, 1.0), (3, 50.0, -1.0), (4, 70.0, 0.5), marks={2: RD.LEAD, 3: RD.HELD})
    assert [d.track_id for d in dots] == [4, 1, 3, 2], "the rest far to near, then the marked far to near"
    by_id = {d.track_id: d for d in dots}
    assert by_id[2].mark == RD.LEAD and by_id[3].mark == RD.HELD and by_id[1].mark == by_id[4].mark == RD.OTHER
    assert by_id[2].r > RD.radius(RD.OTHER, 30.0)
    assert RD.radius(RD.OTHER, 5.0) == RD.RADIUS[RD.OTHER][0] and RD.radius(RD.OTHER, 200.0) == RD.RADIUS[RD.OTHER][1]
    assert RD.radius(RD.LEAD, 30.0) > RD.radius(RD.LEAD2, 30.0) > RD.radius(RD.OTHER, 30.0)

  def test_small(self):
    # small on a 536 x 240 screen: no dot wider than 13 px with its rim
    assert max(max(v) for v in RD.RADIUS.values()) + RD.RIM <= 6.5


# ------------------------------------------------------------------------------------------- the subscription
class TestSubscription(OpenpilotTestCase):
  def test_off_opens_nothing_and_lays_out_nothing(self):
    dots, sock, made = dots_of(on=False, msg=tracks_msg((1, 20.0, 0.0)))
    for _ in range(100):
      assert step(dots, radar_state(one=1)) == []
    assert made == [] and not dots.subscribed and sock.updates == 0

  def test_on_one_socket_read_every_frame(self):
    dots, sock, made = dots_of(on=True, msg=tracks_msg((1, 20.0, 0.0), (2, 40.0, 1.0)))
    for _ in range(10):
      out = step(dots, radar_state(one=2))
    assert len(made) == 1 and dots.subscribed and sock.updates == 10
    assert sorted((d.track_id, d.mark) for d in out) == [(1, RD.OTHER), (2, RD.LEAD)]

  def test_off_again_drops_the_socket(self):
    settings, made = FakeSettings(True), []
    dots = RD.RadarDots(settings=cast(Any, settings), sock_factory=lambda: made.append(FakeSock()) or made[-1],
                        clock=lambda: 1.0)
    assert step(dots) == [] and len(made) == 1, "on: a socket, nothing received yet"
    made[0].feed(tracks_msg((1, 20.0, 0.0)))
    assert len(step(dots)) == 1
    settings.on = False
    assert step(dots) == [] and not dots.subscribed and dots.dots == []
    settings.on = True
    assert step(dots) == [] and len(made) == 2, "a new socket when it is turned on again, and only its messages"
    made[1].feed(tracks_msg((1, 20.0, 0.0)))
    assert len(step(dots)) == 1

  def test_stale_invalid_or_unseen_tracks_draw_nothing(self):
    t = [100.0]
    dots, sock, _ = dots_of(on=True, clock=lambda: t[0])
    assert step(dots) == [], "nothing received yet"
    sock.feed(tracks_msg((1, 20.0, 0.0)))
    assert len(step(dots)) == 1
    t[0] += RD.STALE_S
    assert len(step(dots)) == 1, "half a second: still the radar's last word"
    t[0] += 0.01
    assert step(dots) == [], "older than half a second: the last drive's, or the radar stopped"
    sock.feed(tracks_msg((1, 20.0, 0.0)))
    assert len(step(dots)) == 1, "back with the next message"
    sock.feed(tracks_msg((1, 20.0, 0.0), valid=False))
    assert step(dots) == [], "card marked the radar message invalid (a radar error)"

  def test_the_real_socket(self):
    # TracksSocket: what card publishes, read without waiting; nothing new keeps the last message
    pm = messaging.PubMaster([RD.SERVICE])
    sock = RD.TracksSocket()
    sock.update(1.0)
    assert sock.tracks is None and sock.recv_t == -math.inf
    for _ in range(50):   # a fresh queue can take a moment to connect
      pm.send(RD.SERVICE, tracks_msg((7, 20.0, 1.0)).as_builder())
      sock.update(2.0)
      if sock.tracks is not None:
        break
    assert sock.tracks is not None and sock.valid and sock.recv_t == 2.0
    assert [(p.trackId, p.dRel) for p in sock.tracks.points] == [(7, 20.0)]
    kept = sock.tracks
    sock.update(3.0)
    assert sock.tracks is kept and sock.recv_t == 2.0, "nothing new: the same message, its own time"

  def test_laid_out_again_only_when_an_input_changed(self):
    dots, sock, _ = dots_of(on=True, msg=tracks_msg((1, 20.0, 0.0)))
    rs, path, transform = radar_state(one=1), FLAT.copy(), PINHOLE.copy()
    first = step(dots, rs, transform, path)
    assert step(dots, rs, transform, path) is first, "the same inputs: the same layout, no work"
    for change in ("tracks", "leads", "transform", "path", "rect"):
      before = step(dots, rs, transform, path)
      if change == "tracks":
        sock.feed(tracks_msg((1, 20.0, 0.0)))
      elif change == "leads":
        rs = radar_state()
      elif change == "transform":
        transform = PINHOLE.copy()
      elif change == "path":
        path = FLAT.copy()
      if change == "rect":
        after = step(dots, rs, transform, path, rect=SimpleNamespace(x=1.0, y=0.0, width=float(W), height=float(H)))
      else:
        after = step(dots, rs, transform, path)
      assert after is not before, change
    assert step(dots, radar_state(), transform, path)[0].mark == RD.OTHER

  def test_draws_only_what_this_frame_laid_out(self):
    # the model renderer lays out (update()), the road view draws (draw()): a frame the model renderer left early (no
    # model or calibration yet) has no update() and draws no dots, not the last frame's
    dots, _, _ = dots_of(on=True, msg=tracks_msg((1, 20.0, 0.0), (2, 40.0, 1.0)))
    drawn = []
    with mock.patch.object(RD, "draw_dot", lambda center, r, mark: drawn.append((center.x, center.y, r, mark))):
      dots.draw()
      assert drawn == [], "nothing laid out yet"
      laid = step(dots, radar_state(one=2))
      dots.draw()
      assert len(drawn) == len(laid) == 2
      for (x, y, r, mark), d in zip(drawn, laid, strict=True):   # Vector2 holds float32
        assert abs(x - d.x) < 1e-3 and abs(y - d.y) < 1e-3 and (r, mark) == (d.r, d.mark), "the layout, in its order"
      drawn.clear()
      dots.draw()
      assert drawn == [], "no update() this frame: nothing"
      step(dots, radar_state(one=2))
      dots.draw()
      assert len(drawn) == 2, "the next frame's layout"
      drawn.clear()
      dots._settings.on = False
      step(dots)
      dots.draw()
      assert drawn == [], "off: nothing"

  def test_not_in_the_shared_submaster(self):
    # the UI's SubMaster is built once for every frame of every drive: radarTracks stays out of it (radar_dots.py)
    assert '"radarTracks"' not in UI_STATE.read_text(encoding="utf-8")
    assert "liveTracks" not in UI_STATE.read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------- the hook
class TestHook(OpenpilotTestCase):
  def test_the_hooks_are_marked(self):
    marked(MODEL_RENDERER, ("radar_dots import RadarDots", "self.radar_dots = RadarDots()", "self.radar_dots.update("))
    marked(ROAD_VIEW, ("self._model_renderer.radar_dots.draw()",), window=0)

  def test_laid_out_after_the_path_engaged_or_not(self):
    src = MODEL_RENDERER.read_text(encoding="utf-8")
    render = src[src.index("  def _render(self, rect"):src.index("  def _update_raw_points")]
    call = render.index("self.radar_dots.update(rect, self._car_space_transform, self._path.raw_points")
    assert render.index("self._draw_path(sm)") < call, "after the path"
    line = render[render.rindex("\n", 0, call) + 1:call]
    assert line == "    ", "at the method's own level: not inside the DISENGAGED branch"
    assert "radar_state)" in render[call:call + 200]
    assert render.count("radar_dots") == 1, "laid out once; drawn by the road view"

  def test_drawn_over_the_bottom_fade_under_the_alerts(self):
    # review finding 1 (push13): drawn under the fade, a track nearer than ~10-12 m was dimmed to nothing
    src = ROAD_VIEW.read_text(encoding="utf-8")
    start = src.index("  def _render(self, _):", src.index("class AugmentedRoadView"))
    render = src[start:src.index("  def _switch_stream_if_needed")]
    call = render.index("self._model_renderer.radar_dots.draw()")
    fade = render.index("rl.draw_texture_ex(self._fade_texture")
    assert render.index("self._model_renderer.render(self._content_rect)") < fade < call, "after the model and the fade"
    assert call < render.index("self._alert_renderer.render(") and call < render.index("self._hud_renderer.render("), \
      "under the alerts and the HUD"
    assert call < render.index("rl.end_scissor_mode()"), "inside the camera's scissor"
    line = render[render.rindex("\n", 0, call) + 1:call]
    assert line == "    " and sum("radar_dots" in ln for ln in render.splitlines()) == 1, "every frame, once"


# ------------------------------------------------------------------------------------------- the pixels
CHILD = r'''
import json
import math
import numpy as np
import pyray as rl
from openpilot.system.ui.lib.application import gui_app
gui_app.init_window("radar-dots-test")
print("INIT_OK", flush=True)

import dataclasses
from openpilot.cereal import messaging
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.mici.onroad.model_renderer import ModelRenderer
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import radar_dots as RD
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_id

W, H = 536, 240
F, CX, CY = 300.0, 268.0, 60.0
PINHOLE = np.array([[CX, F, 0.0], [CY, 0.0, F], [1.0, 0.0, 0.0]], dtype=np.float32)
rt = rl.load_render_texture(W, H)
rect = rl.Rectangle(0, 0, W, H)


class Settings:
  on = False
  def get(self, force=False):
    return dataclasses.replace(HS.ALL_ON, radar_dots=self.on)


class Sock:
  def __init__(self, msg):
    self.tracks, self.valid, self.recv_t = msg.radarTracks, True, 0.0
  def update(self, now):
    pass


tm = messaging.new_message("radarTracks")
pts = tm.radarTracks.init("points", 3)
for p, (tid, d, y) in zip(pts, ((1, 20.0, 3.0), (2, 25.0, 0.0), (3, 30.0, -3.0)), strict=True):
  p.trackId, p.dRel, p.yRel = tid, d, y
made = []
settings = Settings()

sm = ui_state.sm
ui_state.started_frame = 1
md = messaging.new_message("modelV2")
xs = np.linspace(0.0, 120.0, 33).tolist()
md.modelV2.position.x, md.modelV2.position.y, md.modelV2.position.z = xs, [0.0] * 33, [0.0] * 33
for lane in md.modelV2.init("laneLines", 4):
  lane.x, lane.y, lane.z = xs, [0.0] * 33, [0.0] * 33
md.modelV2.init("roadEdges", 2)
for e in md.modelV2.roadEdges:
  e.x, e.y, e.z = xs, [0.0] * 33, [0.0] * 33
md.modelV2.laneLineProbs, md.modelV2.roadEdgeStds = [0.0] * 4, [1.0] * 2
rs = messaging.new_message("radarState")
rs.radarState.leadOne.present, rs.radarState.leadOne.radarTrackId = True, 2
rs.radarState.leadTwo.present, rs.radarState.leadTwo.radarTrackId = True, held_id(3)  # a held track: its ring
sm.data["modelV2"], sm.data["radarState"] = md.as_reader().modelV2, rs.as_reader().radarState
for s in ("modelV2", "extrinsicsCalibration", "radarState"):
  sm.recv_frame[s], sm.valid[s], sm.updated[s] = 5, True, True
ui_state.status = UIStatus.DISENGAGED   # no path, no lane lines: on a black frame only the dots


def grab():
  img = rl.load_image_from_texture(rt.texture)
  buf = rl.ffi.buffer(img.data, img.width * img.height * 4)
  arr = np.frombuffer(buf, dtype=np.uint8).reshape(img.height, img.width, 4)[::-1, :, :3].astype(int).copy()
  rl.unload_image(img)
  return arr


def frame(mr, on, bg=rl.BLACK):
  # the model renderer lays the dots out, and here (as the road view does after its fade) they are drawn
  settings.on = on
  rl.begin_drawing()
  rl.begin_texture_mode(rt)
  rl.clear_background(bg)
  mr.render(rect)
  mr.radar_dots.draw()
  rl.end_texture_mode()
  rl.end_drawing()
  return grab()


mr = ModelRenderer()
mr.radar_dots = RD.RadarDots(settings=settings, sock_factory=lambda: made.append(1) or Sock(tm.as_reader()),
                             clock=lambda: 0.1)
mr.set_transform(PINHOLE)
off = frame(mr, False)
on = frame(mr, True)
dots = [dataclasses.asdict(d) for d in mr.radar_dots.dots]
off2 = frame(mr, False)
from openpilot.selfdrive.locationd.calibrationd import HEIGHT_INIT
res = {"height": float(HEIGHT_INIT[0]), "off_lit": int((off.sum(axis=2) > 0).sum()), "off2_lit": int((off2.sum(axis=2) > 0).sum()), "made": len(made),
       "dots": dots, "subscribed_after_off": mr.radar_dots.subscribed, "px": {}}
for d in dots:
  # pixel i covers [i, i + 1): the pixel whose center is nearest the dot's center, and the one half a ring in from its edge
  x, y = int(d["x"]), int(d["y"])
  ring = int(d["x"] + d["r"] - RD.RING / 2)
  res["px"][str(d["track_id"])] = {"center": on[y, x].tolist(), "ring": on[y, ring].tolist(),
                                   "lit": int((on[y - 8:y + 9, x - 8:x + 9].sum(axis=2) > 0).sum())}
res["on_lit"] = int((on.sum(axis=2) > 0).sum())

# the rim (review finding 2): on black it cannot be seen, so the same dots on a light grey sky. The pixels whose centers
# lie 0.2 px outside a dot's fill up to 0.2 px inside its rim's edge (16-gons: both stay within 2% of the radius)
light = frame(mr, True, rl.Color(220, 220, 220, 255))
yy, xx = np.mgrid[0:H, 0:W] + 0.5
brightest = light.max(axis=2)
res["light"] = {}
for d in dots:
  dist = np.hypot(xx - d["x"], yy - d["y"])
  rim = (dist >= d["r"] + 0.2) & (dist <= d["r"] + RD.RIM - 0.2)
  res["light"][str(d["track_id"])] = {"rim_px": int(rim.sum()), "rim_dark": int((brightest[rim] < 150).sum()),
                                      "center": light[int(d["y"]), int(d["x"])].tolist(), "sky": light[5, 5].tolist()}

# over the fade (review finding 1): the real road view, its model renderer's dots on near tracks. No camera frame, so the
# view never sets the transform: a pinhole that puts 8-9 m on the screen's bottom rows, where the fade is ~200/255 dark
from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
from openpilot.system.ui.lib.application import ASSETS_DIR
rl.get_time = lambda: 100.0   # nothing animates between the frames compared
ui_state.started = True
sm.recv_frame["selfdriveState"], sm.updated["selfdriveState"] = 5, True   # selfdrived is up: no 'waiting to start' alert
fade = rl.load_image(str(ASSETS_DIR.joinpath("icons_mici/onroad/onroad_fade.png")))
road_tm = messaging.new_message("radarTracks")
for p, (tid, d, y) in zip(road_tm.radarTracks.init("points", 3), ((11, 8.0, 0.8), (12, 9.0, -0.8), (13, 30.0, 0.0)),
                          strict=True):
  p.trackId, p.dRel, p.yRel = tid, d, y
road_rs = messaging.new_message("radarState")
road_rs.radarState.leadOne.present, road_rs.radarState.leadOne.radarTrackId = True, held_id(12)   # the held ring, near
road_rs.radarState.leadTwo.present, road_rs.radarState.leadTwo.radarTrackId = True, 13
sm.data["radarState"] = road_rs.as_reader().radarState
view = AugmentedRoadView()
view.set_rect(rect)
road_settings = Settings()
view._model_renderer.radar_dots = RD.RadarDots(settings=road_settings, sock_factory=lambda: Sock(road_tm.as_reader()),
                                               clock=lambda: 0.1)
view._model_renderer.set_transform(np.array([[238.0, 600.0, 0.0], [150.0, 0.0, 600.0], [1.0, 0.0, 0.0]], dtype=np.float32))


def road(on):
  road_settings.on = on
  rl.begin_drawing()
  rl.begin_texture_mode(rt)
  rl.clear_background(rl.BLACK)
  view.render(rect)
  rl.end_texture_mode()
  rl.end_drawing()
  return grab()


for _ in range(30):   # let the view settle
  road(False)
road_off = road(False)
road_on = road(True)
road_dots = [dataclasses.asdict(d) for d in view._model_renderer.radar_dots.dots]
road_off2 = road(False)
changed = (road_on != road_off).any(axis=2) | (road_on != road_off2).any(axis=2)
boxes = np.zeros((H, W), dtype=bool)
rv = {"dots": road_dots, "px": {}, "inside": {}, "off_same": bool((road_off == road_off2).all())}
for d in road_dots:
  x, y, k = int(d["x"]), int(d["y"]), int(math.ceil(d["r"] + RD.RIM)) + 1
  box = (slice(max(y - k, 0), y + k + 1), slice(max(x - k, 0), x + k + 1))
  boxes[box] = True
  rv["inside"][str(d["track_id"])] = int(changed[box].sum())
  rv["px"][str(d["track_id"])] = {"center": road_on[y, x].tolist(), "ring": road_on[y, int(d["x"] + d["r"] - RD.RING / 2)].tolist(),
                                  "fade": int(rl.get_image_color(fade, x, y).a)}
rv["outside"] = int((changed & ~boxes).sum())
res["road"] = rv
print("RESULT " + json.dumps(res), flush=True)
'''


class TestRadarDotsRender(OpenpilotTestCase):
  r: dict = {}
  stdout = stderr = ""
  returncode = -1

  @classmethod
  def setup_class(cls):
    env = {**os.environ, "RAYLIB_BACKEND": "headless", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=300)
    cls.stdout, cls.stderr, cls.returncode = p.stdout, p.stderr, p.returncode
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    cls.r = json.loads(lines[-1][len("RESULT "):]) if lines else {}

  def setUp(self):
    super().setUp()
    if "INIT_OK" not in self.stdout:
      self.skipTest(f"no headless raylib window here: {self.stderr[-300:]}")
    assert self.returncode == 0 and self.r, self.stderr[-3000:]

  def test_off_draws_nothing_and_opens_nothing(self):
    assert self.r["off_lit"] == 0, "toggle off: not one pixel"
    assert self.r["off2_lit"] == 0 and self.r["subscribed_after_off"] is False, "off again: gone, socket dropped"
    assert self.r["made"] == 1, "one socket, only while on"

  def test_on_the_dots_where_the_projection_puts_them(self):
    by_id = {str(d["track_id"]): d for d in self.r["dots"]}
    assert sorted(by_id) == ["1", "2", "3"]
    for tid, d_rel, y_rel in (("1", 20.0, 3.0), ("2", 25.0, 0.0), ("3", 30.0, -3.0)):
      x = d_rel + RD.RADAR_TO_CAMERA
      z = self.r["height"]   # the camera's height (no calibration height yet: HEIGHT_INIT), the path flat
      assert abs(by_id[tid]["x"] - (CX - F * y_rel / x)) < 0.01, tid
      assert abs(by_id[tid]["y"] - (CY + F * z / x)) < 0.01, tid
    assert (by_id["1"]["mark"], by_id["2"]["mark"], by_id["3"]["mark"]) == (RD.OTHER, RD.LEAD, RD.HELD)
    assert self.r["on_lit"] == sum(p["lit"] for p in self.r["px"].values()), "nothing drawn but the three dots"

  def test_the_colours(self):
    px = self.r["px"]
    r, g, b = px["1"]["center"]
    assert min(r, g, b) > 200, f"a track: white {px['1']}"
    r, g, b = px["2"]["center"]
    assert r < 60 and g > 150 and b > 200, f"the lead: cyan {px['2']}"
    r, g, b = px["3"]["ring"]
    assert r < 60 and g > 120 and b > 180, f"the held track's ring: cyan {px['3']}"
    assert sum(px["3"]["center"]) < 60, f"the held track: hollow (its dark rim inside) {px['3']}"
    for tid in ("1", "2", "3"):
      assert 15 <= px[tid]["lit"] <= 160, f"{tid}: small {px[tid]}"

  def test_the_rim_reads_on_a_bright_sky(self):
    # review finding 2 (push13): every dot's rim darker than a light grey sky all the way round, and the held ring's
    # middle (its dark disc) too
    light = self.r["light"]
    assert sorted(light) == ["1", "2", "3"]
    for tid, p in light.items():
      assert min(p["sky"]) >= 210, f"the sky {p}"
      assert p["rim_px"] >= 10 and p["rim_dark"] >= 0.9 * p["rim_px"], f"{tid}: a dark rim round it {p}"
    assert max(light["3"]["center"]) < 150, f"the held ring's middle: its dark disc, not the sky {light['3']}"
    assert min(light["1"]["center"]) > 230 and light["2"]["center"][0] < 60, "the fills drawn over the rim"

  def test_near_tracks_over_the_bottom_fade(self):
    # review finding 1 (push13): the real road view; tracks 8 and 9 m away sit on rows its bottom fade darkens by
    # ~200/255. Drawn under the fade (before the fix) the white dot's center came out 43 of 235
    rv = self.r["road"]
    assert sorted(str(d["track_id"]) for d in rv["dots"]) == ["11", "12", "13"], rv["dots"]
    px = rv["px"]
    assert px["11"]["fade"] >= 150 and px["12"]["fade"] >= 150, f"the near tracks are under the fade's dark rows {px}"
    r, g, b = px["11"]["center"]
    assert min(r, g, b) > 200, f"a track at 8 m: full white {px['11']}"
    r, g, b = px["12"]["ring"]
    assert r < 60 and g > 150 and b > 200, f"a held track at 9 m: a full cyan ring {px['12']}"
    r, g, b = px["13"]["center"]
    assert r < 60 and g > 150 and b > 200, f"the second lead at 30 m: cyan {px['13']}"
    assert rv["off_same"], "the view drew the same frame twice with the dots off"
    assert rv["outside"] == 0, f"on vs off: {rv['outside']} pixels changed away from the dots"
    assert all(n > 0 for n in rv["inside"].values()), rv["inside"]
