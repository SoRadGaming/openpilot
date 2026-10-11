"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4's lane-block tile (selfdrive/ui/sunnypilot/mici/onroad/hud_lane_block.py; HudLaneBlock).

The rules without a window: which block it shows (selfdrived's own order: a car in the blind spot, then a road edge;
only while the change waits), the flash's timing, the fades, what cuts it, the hand-over, one flash per refused nudge,
one step a frame however many times it is asked.

Then the real AugmentedRoadView in a headless raylib window (a child process, as test_hud_render.py; skipped where no
window opens; HUD_RENDER_OUT=<dir> keeps the PNGs), each frame against the same view with the tile neutralised - the
stock drawing:
  * the tile on the blinker's side - right or left - and only there (and the turn-intent arrow over the wheel, which
    stays up); EDGE and CAR differ only in the word;
  * a left tile has the top left: the face and the stock MAX number make way - and (push7, with the MAX card of
    HudLimitStyle) the coming limit's card in either style, which goes on the frame and comes back once the tile has
    faded out; a right tile leaves the card drawn, clear of it; the confirm's card, an alert, cuts the tile;
  * a refused nudge flashes the rim twice, then the rim is still;
  * when the block clears the tile goes on the frame the stock 'steer right' alert comes (never a struck-through arrow
    over its green one), that alert and its green arrow take the corner, and the screen is then the stock one pixel for
    pixel; a change that starts straight from a block gets the green arrow on the model's side;
  * every state without a block, and the setting off with a block, is the stock screen pixel for pixel.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal import log
from openpilot.common.test import OpenpilotTestCase

LCS, LCD = log.LaneChangeState, log.LaneChangeDirection
REPO = Path(__file__).resolve().parents[4]
HUD_RENDERER_SP = REPO / "openpilot/selfdrive/ui/sunnypilot/mici/onroad/hud_renderer.py"
ALERTS = REPO / "openpilot/selfdrive/ui/mici/onroad/alert_renderer.py"


class FakeSM:
  def __init__(self, lc=LCS.preLaneChange, side=LCD.right, edge=False, car=False, refused=False, fresh=True):
    self.frame = 10
    self.recv_frame = {"modelV2": 5 if fresh else 0, "modelDataV2SP": 5 if fresh else 0, "carState": 5}
    right = side == LCD.right
    self.data = {
      "modelV2": SimpleNamespace(meta=SimpleNamespace(laneChangeState=lc, laneChangeDirection=side)),
      "carState": SimpleNamespace(leftBlindspot=car and not right, rightBlindspot=car and right),
      "modelDataV2SP": SimpleNamespace(leftLaneChangeEdgeBlock=edge and not right, rightLaneChangeEdgeBlock=edge and right,
                                       laneChangeNudgeRefused=refused),
    }

  def __getitem__(self, k):
    return self.data[k]


def alert(kind: str, status=log.SelfdriveState.AlertStatus.normal, size=log.SelfdriveState.AlertSize.small):
  from openpilot.selfdrive.ui.mici.onroad.alert_renderer import Alert
  return Alert(text1="x", text2="", size=size, status=status, alert_type=f"{kind}/warning")


class TestWhichBlock(OpenpilotTestCase):
  def test_selfdriveds_order_and_side(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import lane_block, Block, LEFT, RIGHT, EDGE, CAR
    assert lane_block(FakeSM(edge=True), 1) == Block(RIGHT, EDGE, False)
    assert lane_block(FakeSM(side=LCD.left, edge=True), 1) == Block(LEFT, EDGE, False)
    assert lane_block(FakeSM(car=True), 1) == Block(RIGHT, CAR, False)
    assert lane_block(FakeSM(edge=True, car=True), 1).word == CAR, "the blind spot first, as selfdrived"
    assert lane_block(FakeSM(edge=True, refused=True), 1).refused

  def test_only_while_the_change_waits_and_only_with_a_block(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import lane_block
    assert lane_block(FakeSM(), 1) is None, "no block: the stock 'steer right'"
    for lc in (LCS.off, LCS.laneChangeStarting, LCS.laneChangeFinishing):
      assert lane_block(FakeSM(lc=lc, edge=True, car=True), 1) is None, lc
    assert lane_block(FakeSM(side=LCD.none, edge=True), 1) is None
    # the edge on the other side is no block for this one
    sm = FakeSM(edge=True)
    sm.data["modelDataV2SP"].rightLaneChangeEdgeBlock, sm.data["modelDataV2SP"].leftLaneChangeEdgeBlock = False, True
    assert lane_block(sm, 1) is None

  def test_old_messages_show_nothing(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import lane_block
    assert lane_block(FakeSM(edge=True, fresh=False), 1) is None
    sm = FakeSM(edge=True, refused=True)
    sm.recv_frame["modelDataV2SP"] = 0
    assert lane_block(sm, 1) is None, "a road edge from before this drive"


class TestTile(OpenpilotTestCase):
  def tile(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import LaneBlockTile
    return LaneBlockTile()

  def run_tile(self, t, frames, sm, a=None, t0=100.0, dt=0.05, on=True, a_out=False):
    alphas = []
    for i in range(frames):
      sm.frame += 1
      t.update(sm, on, a, t0 + i * dt, 1, a_out)
      alphas.append(t.alpha)
    return alphas

  def test_fades_in_like_the_max_number_and_out_in_0_15_s(self):
    t = self.tile()
    up = self.run_tile(t, 60, FakeSM(edge=True))
    assert 0.1 < up[0] < 0.6 and up[-1] > 0.99 and all(b >= a for a, b in zip(up, up[1:], strict=False)), up
    down = self.run_tile(t, 5, FakeSM(), t0=103.0)   # 0.05 s after the last frame above
    assert down[0] > down[1] > down[2] == 0.0 and t.side() is None, f"linear, gone in 0.15 s: {down}"

  def test_an_alert_cuts_it_at_once_but_the_lane_change_ones_do_not(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import cuts_tile
    assert cuts_tile(alert("steerSaturated", status=log.SelfdriveState.AlertStatus.userPrompt))
    assert cuts_tile(alert("controlsMismatchLateral", status=log.SelfdriveState.AlertStatus.critical))
    for kind in ("preLaneChangeLeft", "preLaneChangeRight", "laneChange"):
      assert not cuts_tile(alert(kind)), kind
    assert not cuts_tile(None)
    t = self.tile()
    self.run_tile(t, 20, FakeSM(edge=True))
    self.run_tile(t, 1, FakeSM(edge=True), a=alert("steerSaturated", status=log.SelfdriveState.AlertStatus.userPrompt),
                  t0=101.0)
    assert t.alpha == 0.0 and t.side() is None

  def test_it_hands_over_to_the_green_arrow_on_the_frame(self):
    """The block clears and 'steer right' (or 'changing lanes') is up: gone at once, no struck arrow over the green one.
    The same alert still fading out from before the block is no hand-over: the tile fades as usual."""
    for kind in ("preLaneChangeRight", "laneChange"):
      t = self.tile()
      self.run_tile(t, 20, FakeSM(edge=True))
      self.run_tile(t, 1, FakeSM(), a=alert(kind), t0=101.0)
      assert t.alpha == 0.0 and t.side() is None, kind
    t = self.tile()
    self.run_tile(t, 20, FakeSM(edge=True))
    self.run_tile(t, 1, FakeSM(), a=alert("preLaneChangeRight"), a_out=True, t0=101.0)
    assert 0.0 < t.alpha < 1.0 and t.side() == "right"
    t = self.tile()
    self.run_tile(t, 60, FakeSM(edge=True), a=alert("preLaneChangeRight"))
    assert t.alpha > 0.99, "while the block holds, the tile stays: selfdrived drops 'steer right' then"

  def test_the_setting_off_shows_nothing(self):
    t = self.tile()
    assert max(self.run_tile(t, 20, FakeSM(edge=True), on=False)) == 0.0 and t.block is None

  def test_one_flash_per_refused_nudge(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import flash_lit, FLASH_S
    lit = [flash_lit(i * 0.05) for i in range(20)]
    starts = [i for i in range(len(lit)) if lit[i] and (i == 0 or not lit[i - 1])]
    assert len(starts) == 2 and not any(lit[15:]), lit
    assert flash_lit(0.0) and not flash_lit(FLASH_S + 0.01) and flash_lit(2 * FLASH_S + 0.01)
    t = self.tile()
    self.run_tile(t, 10, FakeSM(edge=True))
    assert t._flash_t0 is None
    self.run_tile(t, 10, FakeSM(edge=True, refused=True), t0=101.0)
    assert t._flash_t0 == 101.0, "from the first frame of the refusal, once"
    self.run_tile(t, 5, FakeSM(edge=True), t0=102.0)
    self.run_tile(t, 2, FakeSM(edge=True, refused=True), t0=103.0)
    assert t._flash_t0 == 103.0, "the next refused nudge flashes again"

  def test_once_a_frame(self):
    """The face, the MAX number's gate, the HUD's state, the box and the coming limit all ask in one frame, on a clock
    that runs between them (rl.get_time()): one step, at the frame's first time."""
    from openpilot.system.ui.lib.application import gui_app
    alphas = {}
    for calls in (1, 6):
      t, sm, now, alphas[calls] = self.tile(), FakeSM(edge=True), 100.0, []
      for _ in range(8):
        sm.frame += 1
        for c in range(calls):
          t.update(sm, True, None, now + c * 0.0005, 1)
        alphas[calls].append(t.alpha)
        assert t._t == now
        now += 1 / gui_app.target_fps
    assert alphas[6] == alphas[1] and alphas[1][0] < 0.3, alphas

  def test_render_ends_the_frame(self):
    """A sim that advances neither gui_app's nor the SubMaster's frame (as hud_alerts.FrameAlert): the render ends it."""
    import pyray as rl
    t, sm = self.tile(), FakeSM(edge=True)
    t.update(sm, True, None, 100.0, 1)
    a = t.alpha
    t.update(sm, True, None, 100.05, 1)
    assert t.alpha == a and t._t == 100.0
    t._shown = None   # nothing to draw: render() only ends the frame, no window needed
    t.render(rl.Rectangle(0, 0, 536, 240))
    t.update(sm, True, None, 100.05, 1)
    assert t.alpha > a and t._t == 100.05

  def test_the_other_blinker_is_a_new_tile(self):
    t = self.tile()
    self.run_tile(t, 20, FakeSM(edge=True))
    self.run_tile(t, 1, FakeSM(side=LCD.left, edge=True), t0=101.0)
    assert t.side() == "left" and t.alpha < 0.6


class TestHooks(OpenpilotTestCase):
  def test_the_hud_renderer_hooks_are_marked(self):
    src = HUD_RENDERER_SP.read_text(encoding="utf-8")
    for needle in ("hud_lane_block import BlockTurnIntent, LaneBlockTile  # FORK(HUD)",
                   "self._lane_block_now()  # FORK(HUD)",
                   "self._lane_block.render(rect)  # FORK(HUD)",
                   "or self._approach_top_icon() or self._lane_block_left()",
                   "if self._lane_block_left():  # FORK(HUD): the lane-block tile has the top left\n      return APPROACH_CUT",
                   "if self._lane_block_left():  # FORK(HUD): the lane-block tile has the top left; the box"):
      assert needle in src, needle
    render = src[src.index("  def _render(self"):src.index("  def _has_blind_spot_detected")]
    assert render.index("self._draw_approach(rect)") < render.index("self._lane_block.render(") < \
      render.index("hud_alerts.frame_alert.clear()"), "drawn last of the top items, before the frame's alert is forgotten"

  def test_the_lane_change_arrow_takes_its_side_from_the_model(self):
    src = ALERTS.read_text(encoding="utf-8")
    helper = src[src.index("  def _icon_helper"):src.index("  def _render(self")]
    lc = helper[helper.index("elif event_name == 'laneChange':"):helper.index("elif event_name == 'laneChangeBlocked':")]
    assert "# FORK(HUD)" in lc and "meta.laneChangeDirection" in lc and "else self._last_icon_side" in lc


# ------------------------------------------------------------------------------------------- the real view
CHILD = r'''
import json, os, types
import numpy as np
import pyray as rl
from openpilot.system.ui.lib.application import gui_app
gui_app.init_window("lane-block-test")
print("INIT_OK", flush=True)

from openpilot.cereal import messaging, log
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import TurnIntent
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_alerts
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
import openpilot.selfdrive.ui.mici.onroad.alert_renderer as ar_mod

W, H = 536, 240
OUT = os.environ.get("HUD_RENDER_OUT")
clock = {"t": 100.0}
rl.get_time = lambda: clock["t"]
ar_mod.time = types.SimpleNamespace(monotonic=lambda: clock["t"])   # the turn-signal arrow's blink on the same clock
ON = dict.fromkeys(HS.ALL_PARAMS, True)
TILE_OFF = {**ON, HS.PARAM_LANE_BLOCK: False}
LCS, LCD, EN = log.LaneChangeState, log.LaneChangeDirection, log.OnroadEvent.EventName


class FakeParams:
  def __init__(self):
    self.v = dict(ON)
  def get(self, key, block=False, return_default=False):
    return self.v.get(key)
  def get_bool(self, key, block=False):
    return bool(self.v.get(key))


ui_state.params = FakeParams()
ui_state.started = True
ui_state.started_frame = 1
ui_state.is_metric = True
ui_state.status = UIStatus.ENGAGED
sm = ui_state.sm
SERVICES = ("carState", "selfdriveState", "controlsState", "modelV2", "modelDataV2SP", "onroadEvents", "carControl",
            "longitudinalPlan", "longitudinalPlanSP", "liveMapDataSP", "radarState")

STEER_RIGHT = ("preLaneChangeRight/warning", "Steer Right", "Confirm Lane Change", "mid", "normal")
STEER_LEFT = ("preLaneChangeLeft/warning", "Steer Left", "Confirm Lane Change", "mid", "normal")
CHANGING = ("laneChange/warning", "Changing Lanes", "", "small", "normal")
ROAD_EDGE = ("laneChangeRoadEdge/warning", "Lane Change Unavailable: Road Edge", "", "small", "userPrompt")
SATURATED = ("steerSaturated/warning", "TAKE CONTROL", "Turn Exceeds Steering Limit", "mid", "userPrompt")
CONFIRM = ("speedLimitPreActive/warning", "Press - to confirm speed limit", "", "small", "normal")


def put(svc, fill, frame=5):
  m = messaging.new_message(svc)
  fill(getattr(m, svc))
  sm.data[svc] = getattr(m, svc)
  sm.seen[svc] = sm.alive[svc] = sm.valid[svc] = sm.updated[svc] = True
  sm.recv_frame[svc] = frame


def scene(lc=None, side="right", edge=False, car=False, refused=False, alert=None, events=(), blinker=None, limit=None,
          ahead=0, ahead_dist=0.0, assist="active", set_kph=110):
  """lc: None (off) | 'pre' | 'starting'. The blinker follows lc unless given. limit: the resolver's map limit (and mapd's
  look-ahead, `ahead` km/h in `ahead_dist` m: the coming limit's card), none unless given."""
  for svc in SERVICES:
    sm.seen[svc] = sm.alive[svc] = sm.updated[svc] = False
    sm.recv_frame[svc] = 0
    sm.data[svc] = getattr(messaging.new_message(svc), svc) if svc != "onroadEvents" else \
      messaging.new_message("onroadEvents", 0).onroadEvents
  left = side == "left"
  blink = (lc is not None) if blinker is None else blinker

  def car_(c):
    c.vEgo = c.vEgoCluster = 60 / 3.6
    c.vCruiseCluster = set_kph
    c.leftBlinker, c.rightBlinker = blink and left, blink and not left
    c.leftBlindspot, c.rightBlindspot = car and left, car and not left
  put("carState", car_)

  def ss(s):
    s.enabled = True
    s.state = "enabled"
    if alert is not None:
      s.alertType, s.alertText1, s.alertText2, s.alertSize, s.alertStatus = alert
  put("selfdriveState", ss)
  put("controlsState", lambda c: None)

  def mv(m):
    m.meta.laneChangeState = {None: LCS.off, "pre": LCS.preLaneChange, "starting": LCS.laneChangeStarting}[lc]
    m.meta.laneChangeDirection = LCD.none if lc is None else (LCD.left if left else LCD.right)
  put("modelV2", mv)

  def md(d):
    d.leftLaneChangeEdgeBlock, d.rightLaneChangeEdgeBlock = edge and left, edge and not left
    d.laneChangeNudgeRefused = refused
  put("modelDataV2SP", md)
  m = messaging.new_message("onroadEvents", len(events))
  for i, e in enumerate(events):
    m.onroadEvents[i].name = e
    m.onroadEvents[i].warning = True
  sm.data["onroadEvents"] = m.onroadEvents
  sm.seen["onroadEvents"] = sm.alive["onroadEvents"] = sm.valid["onroadEvents"] = sm.updated["onroadEvents"] = True
  sm.recv_frame["onroadEvents"] = 5
  if limit is not None:
    def plan(p):
      r = p.speedLimit.resolver
      r.speedLimit = r.speedLimitLast = r.speedLimitFinal = r.speedLimitFinalLast = limit / 3.6
      r.speedLimitValid = r.speedLimitLastValid = True
      r.source = "map"
      p.speedLimit.assist.state = assist
    put("longitudinalPlanSP", plan)

    def lmd(d):
      d.speedLimitValid, d.speedLimit = True, limit / 3.6
      d.speedLimitAheadValid, d.speedLimitAhead, d.speedLimitAheadDistance = ahead > 0, ahead / 3.6, ahead_dist
      d.nswZone.mode, d.nswZone.state, d.nswZone.speedLimit = 2, 2, limit / 3.6
    put("liveMapDataSP", lmd)


rt = rl.load_render_texture(W, H)
rect = rl.Rectangle(0, 0, W, H)


def grab():
  img = rl.load_image_from_texture(rt.texture)
  arr = np.frombuffer(rl.ffi.buffer(img.data, img.width * img.height * 4), dtype=np.uint8)
  arr = arr.reshape(img.height, img.width, 4)[::-1, :, :3].astype(int).copy()
  rl.unload_image(img)
  return arr


def neutralise(view):
  """The tile's hooks off: what the unchanged drawing shows (the HUD renderer's stock turn-intent arrow, no tile, the
  top left free)."""
  hr = view._hud_renderer
  hr._lane_block.render = lambda *a, **k: None
  hr._lane_block_left = lambda: False
  hr._turn_intent = TurnIntent()


def run(params, steps, stock=False):
  """ONE view through phases: steps = [(scene kwargs, frames, name)]; the last frame of each phase, and the tile's
  alpha on every frame."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()
  view = AugmentedRoadView()
  view.set_rect(rect)
  if stock:
    neutralise(view)
  grabs, alphas, tops, t = [], [], [], 100.0
  for kw, frames, name in steps:
    scene(**kw)
    for _ in range(frames):
      clock["t"] = t
      t += 0.05
      rl.begin_drawing()
      rl.begin_texture_mode(rt)
      rl.clear_background(rl.BLACK)
      view.render(rect)
      rl.end_texture_mode()
      rl.end_drawing()
      alphas.append(round(view._hud_renderer._lane_block.alpha, 3))
    grabs.append(grab())
    tops.append(bool(view._hud_renderer.drawing_top_icons()))
    if OUT:
      from PIL import Image
      Image.fromarray(grabs[-1].astype(np.uint8)).save(os.path.join(OUT, f"lb_{name}{'_stock' if stock else ''}.png"))
  view.close()
  return grabs, alphas, tops


def mask(arr, kind):
  r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
  if kind == "orange":
    return (r > 200) & (g > 70) & (g < 160) & (b < 60)
  if kind == "white":
    return (r > 200) & (g > 200) & (b > 200)
  if kind == "green":   # the turn-signal arrow, at any point of its blink
    return (g > 30) & (g > 2 * r) & (g > 1.5 * b)
  raise KeyError(kind)


def count(arr, box, kind):
  x0, y0, x1, y1 = box
  return int(mask(arr[y0:y1, x0:x1], kind).sum())


def diff_px(a, b):
  return int((np.abs(a - b).max(axis=2) > 0).sum())


def diff_outside(a, b, boxes):
  d = np.abs(a - b).max(axis=2) > 0
  for x0, y0, x1, y1 in boxes:
    d[y0:y1, x0:x1] = False
  return int(d.sum())


TILE_R = (376, 8, 468, 108)
TILE_L = (8, 8, 100, 108)
ARROW_SLOT_R = (370, 5, 474, 101)
WHEEL = (0, 140, 110, 240)              # the wheel and its turn-intent arrow
HALO_R = (362, 0, 476, 122)
out = {}

# the tile, each side and each word, against the same frame with the tile neutralised
for name, kw in {"edge_right": dict(lc="pre", edge=True), "edge_left": dict(lc="pre", side="left", edge=True),
                 "car_right": dict(lc="pre", car=True), "car_left": dict(lc="pre", side="left", car=True)}.items():
  (on,), _, (top,) = run(ON, [(kw, 100, name)])
  (st,), _, (stock_top,) = run(ON, [(kw, 100, name)], stock=True)
  tile = TILE_L if kw.get("side") == "left" else TILE_R
  other = TILE_R if tile is TILE_L else TILE_L
  # on the left the face and the stock MAX number make way: their place too
  out[name] = {"outside": diff_outside(on, st, [tile, WHEEL] + ([(0, 0, 200, 140)] if tile is TILE_L else [])),
               "tile_orange": count(on, tile, "orange"), "other_orange": count(on, other, "orange"),
               "tile_white": count(on, tile, "white"), "wheel_white": count(on, WHEEL, "white"),
               "stock_wheel_white": count(st, WHEEL, "white"), "top_icons": top, "stock_top_icons": stock_top}
  out[name + "_img"] = on.tolist() if name in ("edge_right", "car_right") else None
edge, car_ = np.array(out.pop("edge_right_img")), np.array(out.pop("car_right_img"))
out.pop("edge_left_img"); out.pop("car_left_img")
ys = np.nonzero((np.abs(edge - car_).max(axis=2) > 0).any(axis=1))[0]
xs = np.nonzero((np.abs(edge - car_).max(axis=2) > 0).any(axis=0))[0]
out["edge_vs_car_box"] = [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None

# a left tile takes the top left from the stock MAX number (the first 2.5 s after engaging)
(on,), _, _ = run(ON, [(dict(lc="pre", side="left", edge=True), 20, "max_left")])
(st,), _, _ = run(ON, [(dict(lc="pre", side="left", edge=True), 20, "max_left")], stock=True)
out["max"] = {"on": count(on, (100, 0, 200, 140), "white"), "stock": count(st, (100, 0, 200, 140), "white")}

# a refused nudge: the rim flashes twice, then is still
steps = [(dict(lc="pre", edge=True), 40, "flash_before")] + \
        [(dict(lc="pre", edge=True, refused=True), 1, f"flash_{i:02d}") for i in range(24)]
grabs, _, _ = run(ON, steps)
halo = [count(g, HALO_R, "orange") - count(g, TILE_R, "orange") for g in grabs]
x0, y0, x1, y1 = HALO_R
out["flash"] = {"halo": halo, "still": diff_px(grabs[-1][y0:y1, x0:x1], grabs[0][y0:y1, x0:x1])}

# the block clears: the tile goes in 0.15 s, the stock 'steer right' and its green arrow take the corner, and the
# screen is then the stock one; then the change starts
seq = [(dict(lc="pre", edge=True), 60, "seq_block"),
       (dict(lc="pre", alert=STEER_RIGHT, events=(EN.preLaneChangeRight,)), 1, "seq_clear_1"),
       (dict(lc="pre", alert=STEER_RIGHT, events=(EN.preLaneChangeRight,)), 1, "seq_clear_2"),
       (dict(lc="pre", alert=STEER_RIGHT, events=(EN.preLaneChangeRight,)), 1, "seq_clear_3"),
       (dict(lc="pre", alert=STEER_RIGHT, events=(EN.preLaneChangeRight,)), 57, "seq_steer"),
       (dict(lc="starting", alert=CHANGING, events=(EN.laneChange,)), 60, "seq_changing")]
g_on, a_on, _ = run(ON, seq)
g_st, _, _ = run(ON, seq, stock=True)
out["seq"] = {"alpha": a_on[58:66], "vs_stock": [diff_px(a, b) for a, b in zip(g_on, g_st)],
              "tile_orange": [count(g, TILE_R, "orange") for g in g_on],
              "arrow_green": [count(g, ARROW_SLOT_R, "green") for g in g_on]}

# a change that starts straight from a block (the nudge held as the edge clears): no 'steer right' before it
g, _, _ = run(ON, [(dict(lc="pre", edge=True), 60, "direct_block"),
                   (dict(lc="starting", alert=CHANGING, events=(EN.laneChange,)), 12, "direct_changing")])
out["direct"] = {"arrow_green": count(g[-1], ARROW_SLOT_R, "green"), "tile_orange": count(g[-1], TILE_R, "orange")}

# an alert cuts the tile on the frame
g, a, _ = run(ON, [(dict(lc="pre", edge=True), 60, "cut_block"), (dict(lc="pre", edge=True, alert=SATURATED), 1, "cut")])
out["cut"] = {"alpha": a[-1]}

# push7: the tile meets the MAX card (HudLimitStyle, both styles). The coming limit's card is up (the limit 80, mapd's
# 60 in 200 m); a LEFT tile has the top left - the card goes on the frame, and comes back once the tile has faded out
# (as after any cut: it opens again) - and a RIGHT tile leaves it drawn, clear of the tile; the confirm's card is an
# alert, which cuts the tile on the frame. Every card drawn is counted through the module attributes the HUD calls.
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw
cards = []
_split, _plus = hud_draw.split_card, hud_draw.stock_plus_card


def counted_card(real, style):
  def draw(c, x0, y0):
    r = real(c, x0, y0)
    cards.append([style, int(c.kind), [round(v, 1) for v in r] if r else None])
    return r
  return draw


hud_draw.split_card, hud_draw.stock_plus_card = counted_card(_split, "split"), counted_card(_plus, "plus")


def run_cards(params, steps):
  """run(), but every frame's cards, the tile's alpha and side and the face's answer (as the view asked it)."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()
  view = AugmentedRoadView()
  view.set_rect(rect)
  hr = view._hud_renderer
  asked = {}
  real_top = hr.drawing_top_icons
  def top():
    asked["top"] = real_top()
    return asked["top"]
  hr.drawing_top_icons = top
  phases, t = [], 100.0
  for kw, frames, name in steps:
    scene(**kw)
    rows = []
    for _ in range(frames):
      clock["t"] = t
      t += 0.05
      cards.clear()
      asked.clear()
      rl.begin_drawing()
      rl.begin_texture_mode(rt)
      rl.clear_background(rl.BLACK)
      view.render(rect)
      rl.end_texture_mode()
      rl.end_drawing()
      rows.append({"cards": [list(c) for c in cards], "alpha": round(hr._lane_block.alpha, 3), "side": hr._lane_block.side(),
                   "top": asked.get("top")})
    phases.append({"rows": rows, "img": grab()})
    if OUT:
      from PIL import Image
      Image.fromarray(phases[-1]["img"].astype(np.uint8)).save(os.path.join(OUT, f"lb_{name}.png"))
  view.close()
  return phases


mode0 = ui_state.speed_limit_mode
ui_state.speed_limit_mode = 1   # Speed Limit on: the coming limit needs it
COMING = dict(limit=80, ahead=60, ahead_dist=200.0)
meet = {}
for tag, style in (("d3", HS.STYLE_SPLIT), ("d1", HS.STYLE_STOCK_PLUS)):
  prm = {**ON, HS.PARAM_LIMIT_STYLE: style}
  ph = run_cards(prm, [(COMING, 100, f"meet_{tag}_1_card"),
                       (dict(COMING, lc="pre", side="left", edge=True), 20, f"meet_{tag}_2_left"),
                       (COMING, 20, f"meet_{tag}_3_left_gone"),
                       (dict(COMING, lc="pre", side="right", edge=True), 20, f"meet_{tag}_4_right"),
                       (dict(COMING, lc="pre", side="left", edge=True), 20, f"meet_{tag}_5_left_again"),
                       (dict(lc="pre", side="left", edge=True, limit=60, set_kph=80, assist="preActive", alert=CONFIRM), 20,
                        f"meet_{tag}_6_confirm")])
  # the same drive with no coming limit: the left tile's frame must be the same pixels (nothing of the card left)
  ref = run_cards(prm, [(dict(limit=80), 100, f"meet_{tag}_ref_1"), (dict(lc="pre", side="left", edge=True, limit=80), 20,
                                                                     f"meet_{tag}_ref_2_left")])
  meet[tag] = {"phases": [p["rows"] for p in ph], "left_vs_no_card": diff_px(ph[1]["img"], ref[1]["img"]),
               "tile_l_orange": count(ph[1]["img"], TILE_L, "orange"), "tile_r_orange": count(ph[3]["img"], TILE_R, "orange")}
ui_state.speed_limit_mode = mode0
out["meet"] = meet

# every state without a block, and the setting off with a block (selfdrived then raises the stock alert): stock
same = {}
for name, (params, kw) in {
    "cruise": (ON, dict()),
    "steer_right": (ON, dict(lc="pre", alert=STEER_RIGHT, events=(EN.preLaneChangeRight,))),
    "steer_left": (ON, dict(lc="pre", side="left", alert=STEER_LEFT, events=(EN.preLaneChangeLeft,))),
    "changing": (ON, dict(lc="starting", alert=CHANGING, events=(EN.laneChange,))),
    "edge_no_blinker": (ON, dict(edge=True)),
    "car_no_blinker": (ON, dict(car=True)),
    "tile_off_edge": (TILE_OFF, dict(lc="pre", edge=True, alert=ROAD_EDGE)),
    "tile_off_refused": (TILE_OFF, dict(lc="pre", edge=True, refused=True, alert=ROAD_EDGE)),
}.items():
  (on,), _, _ = run(params, [(kw, 60, f"same_{name}")])
  (st,), _, _ = run(params, [(kw, 60, f"same_{name}")], stock=True)
  same[name] = diff_px(on, st)
out["same"] = same

print("RESULT " + json.dumps(out), flush=True)
'''


class TestLaneBlockRender(OpenpilotTestCase):
  r: dict = {}
  stdout = stderr = ""
  returncode = -1

  @classmethod
  def setup_class(cls):
    env = {**os.environ, "RAYLIB_BACKEND": "headless", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=900)
    cls.stdout, cls.stderr, cls.returncode = p.stdout, p.stderr, p.returncode
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    cls.r = json.loads(lines[-1][len("RESULT "):]) if lines else {}

  def setUp(self):
    super().setUp()
    if "INIT_OK" not in self.stdout:
      self.skipTest(f"no headless raylib window here: {self.stderr[-300:]}")
    assert self.returncode == 0 and self.r, self.stderr[-3000:]

  def test_the_tile_is_on_the_blinker_side_and_only_there(self):
    for name in ("edge_right", "edge_left", "car_right", "car_left"):
      r = self.r[name]
      assert r["outside"] == 0, f"{name}: nothing drawn outside the tile and the wheel {r}"
      assert r["tile_orange"] > 400 and r["other_orange"] == 0, f"{name}: the orange rim on its side only {r}"
      assert r["tile_white"] > 150, f"{name}: the word {r}"

  def test_the_turn_intent_arrow_stays_up(self):
    for name in ("edge_right", "edge_left"):
      r = self.r[name]
      assert r["wheel_white"] > r["stock_wheel_white"] + 100, f"{name}: the arrow over the wheel, which stock hides {r}"

  def test_edge_and_car_differ_only_in_the_word(self):
    x0, y0, x1, y1 = self.r["edge_vs_car_box"]
    assert 376 <= x0 and x1 < 468 and y0 >= 8 + 60 and y1 < 108, self.r["edge_vs_car_box"]

  def test_a_left_tile_has_the_top_left(self):
    left, right = self.r["edge_left"], self.r["edge_right"]
    assert left["top_icons"] and not left["stock_top_icons"], f"the face makes way on the left {left}"
    assert right["top_icons"] == right["stock_top_icons"], f"not for a right tile {right}"
    m = self.r["max"]
    assert m["stock"] > 300 and m["on"] == 0, f"the stock MAX number makes way {m}"

  def test_a_refused_nudge_flashes_the_rim_twice(self):
    f = self.r["flash"]
    halo = f["halo"][1:]
    lit = [h > 300 for h in halo]
    starts = [i for i in range(len(lit)) if lit[i] and (i == 0 or not lit[i - 1])]
    assert f["halo"][0] <= 0 and len(starts) == 2 and starts[0] == 0, f
    assert not any(lit[16:]) and f["still"] == 0, f"then the tile as it was {f}"

  def test_when_allowed_the_tile_hands_over_to_the_green_arrow(self):
    s = self.r["seq"]
    block, c1, c2, c3, steer, changing = range(6)
    assert s["alpha"][1] > 0.95 and s["alpha"][2] < s["alpha"][1] and s["alpha"][4] == 0.0, f"gone in 0.15 s {s}"
    assert s["alpha"][2] == 0.0 and s["tile_orange"][c1] == 0, f"gone on the frame 'steer right' comes {s}"
    assert s["tile_orange"][c3] == 0 and s["arrow_green"][steer] > 300, f"the green arrow in the corner {s}"
    assert s["vs_stock"][steer] == 0 and s["vs_stock"][changing] == 0, f"then the stock screen {s}"

  def test_a_change_straight_from_a_block_gets_its_green_arrow(self):
    d = self.r["direct"]
    assert d["arrow_green"] > 300 and d["tile_orange"] == 0, d

  def test_an_alert_cuts_it(self):
    assert self.r["cut"]["alpha"] == 0.0

  def test_every_other_state_is_the_stock_screen(self):
    for name, px in self.r["same"].items():
      assert px == 0, f"{name}: {px} pixels differ from the stock drawing"

  # push7: the tile and the MAX card (HudLimitStyle) together
  APPROACH, PENDING = 0, 1   # hud_model.CARD_APPROACH / CARD_PENDING

  def kinds(self, row, style):
    assert all(c[0] == style for c in row["cards"]), row
    return [c[1] for c in row["cards"]]

  def test_a_left_tile_puts_the_max_card_away_in_both_styles(self):
    for tag, style in (("d3", "split"), ("d1", "plus")):
      m = self.r["meet"][tag]
      card, left, gone = m["phases"][0], m["phases"][1], m["phases"][2]
      assert self.kinds(card[-1], style) == [self.APPROACH] and card[-1]["top"], f"{tag}: the coming limit's card {card[-1]}"
      assert all(r["side"] == "left" and self.kinds(r, style) == [] and r["top"] for r in left), \
        f"{tag}: the card goes on the tile's first frame, the face stays away {left}"
      assert m["tile_l_orange"] > 400 and m["left_vs_no_card"] == 0, f"{tag}: the tile, and nothing of the card {m}"
      back = [i for i, r in enumerate(gone) if self.kinds(r, style)]
      assert back and all(gone[i]["alpha"] < 1e-2 for i in back), f"{tag}: never under the fading tile {gone}"
      assert back[0] <= 5 and self.kinds(gone[-1], style) == [self.APPROACH], f"{tag}: back once the tile has gone {gone}"
      again = m["phases"][4]
      assert all(self.kinds(r, style) == [] for r in again), f"{tag}: and away again for the next left tile {again}"

  def test_a_right_tile_leaves_the_max_card_drawn_and_clear_of_it(self):
    for tag, style in (("d3", "split"), ("d1", "plus")):
      m = self.r["meet"][tag]
      right = m["phases"][3]
      assert all(r["side"] == "right" and self.kinds(r, style) == [self.APPROACH] for r in right), f"{tag} {right}"
      x1 = max(c[2][2] for r in right for c in r["cards"])
      assert m["tile_r_orange"] > 400 and x1 < 376, f"{tag}: the card ends at x {x1}, left of the tile's 376"

  def test_the_confirms_card_cuts_the_tile(self):
    for tag, style in (("d3", "split"), ("d1", "plus")):
      conf = self.r["meet"][tag]["phases"][5]
      assert all(r["alpha"] == 0.0 and r["side"] is None for r in conf), f"{tag}: the alert cuts the tile on the frame {conf}"
      assert self.kinds(conf[-1], style) == [self.PENDING], f"{tag}: the confirm's card {conf[-1]}"
