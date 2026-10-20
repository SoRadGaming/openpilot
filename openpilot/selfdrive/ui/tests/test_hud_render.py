"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 onroad view, drawn for real in a headless raylib window, per HUD state:

  * with every setting off, the frame is PIXEL-IDENTICAL to the stock drawing, i.e. the same view with the FORK(HUD)
    call sites neutralised (cluster and rail renders no-ops - so the ball's floor stays unset - the alert hook answering
    "not mine", and the coming limit never drawn nor counted);
  * moving, nothing top right (round 5, option A: no speed, no sign, no next limit); stopped, the stop timer in the
    corner;
  * with only an alert's own setting off, that alert is drawn exactly as stock - the full-screen confirm included, which
    no longer draws the limit in its arrow's box;
  * the right rail: its item only in the ball's strip, white or grey as openpilot drives the plan or not, the ball held
    under it and back where the stock one is once it has gone, and with its two settings off the stock strip;
  * compact alerts: 'set speed changed' pixel-identical to no alert (and the MAX number free to show), the disengage
    and turn banners top left, nothing below them, a critical alert exactly stock, each group's setting off exactly
    stock;
  * the compact confirm, as the card of the style HudLimitStyle picks (the split card by default; stock-plus in its own
    section): its target in the MAX number's place - for stock-plus the stock MAX digits pixel for pixel - with
    'press - to set 50' and no banner, a top icon; the limit named with an offset; today's banner with no limit; once
    the press has set the speed the card turns into the stock MAX number (the split card's slide, the stock-plus
    dissolve) and the frames are then exactly the drive's with no prompt; a critical alert on its first frame exactly
    stock - also in the middle of a flash; the outline flashing twice and then marching (split card) or breathing
    (stock-plus), the digits identical to a still card on every frame;
  * the coming limit: in the MAX number's place, lower or higher, 'now' close in, a top icon; never in a tunnel, out of
    its window, while MAX or an alert shows, or with its setting off; held past the look-ahead and handed over to the
    confirm's card - the same card, no fade between - or to the stock MAX number, in the same place;
  * the style: every state neither card draws is the same pixels in both styles, and switching it while a card is up
    (coming, waiting, turning into MAX) draws the other style from the next read on;
  * the review fixes: not engaged, the card names no MAX ('–'); the widest confirm stops 40 px short of the road's edge,
    or 14 px short of the stop timer, which it leaves untouched; a card turning into MAX from a fading prompt starts at
    the strength it was drawn at.

The real AugmentedRoadView, fed synthetic messages (no camera: the placeholder is black, which keeps the color
sampling honest). It runs in a child process: a raylib that cannot open a headless window must not take the test
runner's worker down with it. No window at all is a skip; anything after the window opened is a real result.
Set HUD_RENDER_OUT=<dir> to keep the PNGs.
"""
import json
import os
import subprocess
import sys

from openpilot.common.test import OpenpilotTestCase

STRIP_X = 476  # the confidence ball's strip (CHILD's STRIP)

CHILD = r'''
import json, os
import numpy as np
import pyray as rl
from openpilot.system.ui.lib.application import gui_app
gui_app.init_window("hud-render-test")
print("INIT_OK", flush=True)

from openpilot.cereal import messaging
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.selfdrive.ui.sunnypilot.onroad.speed_limit import SpeedLimitAlertRenderer
SpeedLimitAlertRenderer.ARROW_SIZE = 90  # the comma 4's (a PC evaluates it to 200 at import)
from openpilot.selfdrive.ui.mici.onroad.augmented_road_view import AugmentedRoadView
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_alerts
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_renderer as hud_renderer_sp
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model as HM

KPH = 3.6
W, H = 536, 240
OUT = os.environ.get("HUD_RENDER_OUT")
clock = {"t": 100.0}
rl.get_time = lambda: clock["t"]

ON = {**dict.fromkeys(HS.ALL_PARAMS, True), HS.PARAM_LIMIT_STYLE: HS.STYLE_SPLIT}   # the default: the split card (D3)
OFF = {**dict.fromkeys(HS.ALL_PARAMS, False), HS.PARAM_LIMIT_STYLE: HS.STYLE_SPLIT}
D1 = {**ON, HS.PARAM_LIMIT_STYLE: HS.STYLE_STOCK_PLUS}                               # stock-plus
RAIL_OFF = {**ON, HS.PARAM_PLANNED_STOP: False, HS.PARAM_CURVE: False}
FULL_LIMIT = {**ON, HS.PARAM_COMPACT_LIMIT: False}   # the speed-limit prompts full screen, as before the compact ones
NO_APPROACH = {**ON, HS.PARAM_APPROACHING_LIMIT: False}

# what was drawn on a frame: the confirm's banner (hud_draw.confirm_banner) and the card in the MAX number's place, by
# kind - the coming limit ('approach'), the confirm waiting ('box') and the confirm done ('done') - and by the style's
# drawer that drew it, counted through the module attributes the callers use
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw
calls = {}
_banner, _split, _plus = hud_draw.confirm_banner, hud_draw.split_card, hud_draw.stock_plus_card


def reset_calls():
  calls.update(banner=0, banner_text=None, box=0, box_rect=None, box_text=None, box_note=None, box_alpha=None, lit=None,
               phase=None, breath=None, grow=None, approach=0, approach_rect=None, approach_args=None, approach_lit=None,
               enter=None, done=0, done_args=None, style=None, card=None, approach_current=None, box_current=None,
               done_alpha=None, max_right=None, approach_armed=None, approach_digits=None, approach_alpha=None)


def counted_banner(*a, **k):
  calls["banner"] += 1
  calls["banner_text"] = a[2]
  return _banner(*a, **k)


def counted(real, style):
  def draw(c, x0, y0):
    r = [round(v, 1) for v in real(c, x0, y0)]
    calls["style"], calls["card"] = style, c
    calls["max_right"] = c.max_right if c.max_right != float("inf") else None
    if c.kind == HM.CARD_PENDING:
      calls["box"] += 1
      calls.update(box_rect=r, box_text=c.text, box_note=c.note, box_alpha=round(c.alpha, 3), lit=bool(c.lit),
                   phase=c.phase, breath=round(c.breath, 3), grow=round(c.grow, 3), box_current=c.current)
    elif c.kind == HM.CARD_APPROACH:
      calls["approach"] += 1
      calls.update(approach_rect=r, approach_args=[c.value, c.lower, c.text, round(c.frac, 3)], approach_lit=bool(c.lit),
                   enter=round(c.enter, 3), approach_current=c.current, approach_armed=bool(c.armed),
                   approach_digits=round(c.digits, 3), approach_alpha=round(c.alpha, 3))
    else:
      calls["done"] += 1
      calls["done_args"] = [c.value, c.current, round(c.done, 3)]
      calls["done_alpha"] = round(c.alpha, 3)
    return r
  return draw


hud_draw.confirm_banner = counted_banner
hud_draw.split_card, hud_draw.stock_plus_card = counted(_split, "split"), counted(_plus, "plus")
reset_calls()
last = {}  # the last rendered frame's calls, and whether the HUD renderer counted a top icon


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
ui_state.speed_limit_mode = 1
ui_state.status = UIStatus.ENGAGED
sm = ui_state.sm
SERVICES = ("carState", "selfdriveState", "controlsState", "longitudinalPlanSP", "liveMapDataSP", "radarState")
RAIL_SERVICES = ("modelV2", "carControl", "longitudinalPlan")  # the right rail's own; a fresh default every scene


def stopping_plan(stop_m):
  """modelV2's 33-point speed plan: a steady brake from 8 m/s to a stop stop_m ahead, or cruising (None)."""
  vx, px, v0 = [], [], 8.0
  a = v0 ** 2 / (2 * stop_m) if stop_m else 0.0
  for t in ModelConstants.T_IDXS:
    if a and t >= v0 / a:
      vx.append(0.0)
      px.append(stop_m)
    else:
      vx.append(v0 - a * t)
      px.append(v0 * t - a * t * t / 2)
  return vx, px


def put(svc, fill, frame=5):
  m = messaging.new_message(svc)
  fill(getattr(m, svc))
  sm.data[svc] = getattr(m, svc)
  sm.seen[svc] = sm.alive[svc] = sm.valid[svc] = sm.updated[svc] = True
  sm.recv_frame[svc] = frame


def scene(v=110 / KPH, limit=110, ahead=0, ahead_dist=0.0, ahead_valid=True, nsw_state=2, source="map",
          standstill=False, alert=None, assist="active", set_kph=110, lead=None, lp=True, cs=True, offset=0, rail=None,
          map_frame=5, enabled=True, now_key=None, armed=None):
  """rail = dict(stop_m, solid, curve_kph, confident): the right rail's messages (modelV2's plan, carControl,
  longitudinalPlan, smart cruise control) - none of them otherwise. No limit ahead unless ahead > 0. enabled=False:
  openpilot not engaged (selfdriveState), the set speed kept as this non-PCM car keeps it. sla-flash: now_key =
  (key, limit, target), the planner's offer (assist.nowKey / nowLimit / nowTarget); armed = (limit, target), the press
  it holds (assist.armedLimit / armedTarget / armedSpeed)."""
  for svc in SERVICES + RAIL_SERVICES:
    sm.seen[svc] = sm.alive[svc] = sm.updated[svc] = False
    sm.recv_frame[svc] = 0
  for svc in RAIL_SERVICES:
    sm.data[svc] = getattr(messaging.new_message(svc), svc)
  rail = rail or {}
  if rail:
    def model(m):
      m.velocity.x, m.position.x = stopping_plan(rail.get("stop_m"))
      m.orientationRate.z = [-0.1 * min(1.0, t) for t in ModelConstants.T_IDXS]  # a left curve
      if rail.get("confident"):
        m.meta.disengagePredictions.brakeDisengageProbs = [0.0] * 6
        m.meta.disengagePredictions.steerOverrideProbs = [0.0] * 6
    put("modelV2", model)
    put("carControl", lambda c: setattr(c, "longActive", rail.get("solid", True)))
    put("longitudinalPlan", lambda p: setattr(p, "longitudinalPlanSource", "e2e" if rail.get("solid", True) else "cruise"))

  def car(c):
    c.vEgo = c.vEgoCluster = v
    c.standstill = standstill
    c.vCruiseCluster = set_kph
  if cs:
    put("carState", car)

  def ss(s):
    s.enabled = enabled
    s.state = "enabled" if enabled else "disabled"
    if alert is not None:
      kind, t1, t2, size, status = alert[:5]
      s.alertType, s.alertText1, s.alertText2, s.alertSize, s.alertStatus = kind, t1, t2, size, status
      if len(alert) > 5:
        s.alertHudVisual = alert[5]
  put("selfdriveState", ss)
  put("controlsState", lambda c: None)

  def plan(p):
    r = p.speedLimit.resolver
    r.speedLimit = r.speedLimitLast = limit / KPH
    r.speedLimitOffset = offset / KPH
    r.speedLimitFinal = r.speedLimitFinalLast = (limit + offset) / KPH
    r.speedLimitValid = r.speedLimitLastValid = True
    r.source = source
    p.speedLimit.assist.state = assist
    a = p.speedLimit.assist
    if now_key is not None:
      a.nowKey, a.nowLimit, a.nowTarget = now_key
    if armed is not None:
      a.armedLimit, a.armedTarget = armed
      a.armedSpeed, a.armSeq = armed[1] / KPH, 1
    if rail.get("curve_kph"):
      vis = p.smartCruiseControl.vision
      vis.state, vis.active, vis.vTarget = "entering", True, rail["curve_kph"] / KPH
      p.longitudinalPlanSource = "sccVision"
  if lp:
    put("longitudinalPlanSP", plan)

  def lmd(m):
    m.speedLimitValid = True
    m.speedLimit = limit / KPH
    m.speedLimitAheadValid = ahead_valid and ahead > 0
    m.speedLimitAhead = ahead / KPH
    m.speedLimitAheadDistance = ahead_dist
    z = m.nswZone
    z.mode, z.state, z.speedLimit = 2, nsw_state, limit / KPH
  put("liveMapDataSP", lmd, frame=map_frame)

  def radar(r):
    if lead is not None:
      r.leadOne.present, r.leadOne.vLead, r.leadOne.dRel = True, lead, 4.0
  put("radarState", radar)


STOPPED = ("manualRestart/warning", "TAKE CONTROL", "Resume Driving Manually", "mid", "userPrompt")
CONFIRM = ("speedLimitPreActive/warning", "Press - to confirm speed limit", "", "small", "normal")
CONFIRM_PLUS = ("speedLimitPreActive/warning", "Press + to confirm speed limit", "", "small", "normal")
CONFIRM_EMPTY = ("speedLimitPreActive/warning", "", "", "small", "normal")   # set speed already the limit: no words
SATURATED = ("steerSaturated/warning", "TAKE CONTROL", "Turn Exceeds Steering Limit", "mid", "userPrompt")
CHANGED = ("speedLimitChanged/warning", "Set speed changed", "", "small", "normal")
ACTIVE = ("speedLimitActive/warning", "Auto adjusting to speed limit", "", "small", "normal")
DISENGAGE = ("manualLongitudinalRequired/warning", "Smart/Adaptive Cruise Control: OFF", "Manual Speed Control Required",
             "mid", "normal")
TURN = ("laneTurnLeft/warning", "Turning Left", "", "small", "normal")
CRITICAL = ("controlsMismatchLateral/immediateDisable", "TAKE CONTROL IMMEDIATELY", "Controls Mismatch: Lateral", "full",
            "critical", "steerRequired")

rt = rl.load_render_texture(W, H)
rect = rl.Rectangle(0, 0, W, H)


def grab():
  img = rl.load_image_from_texture(rt.texture)
  buf = rl.ffi.buffer(img.data, img.width * img.height * 4)
  arr = np.frombuffer(buf, dtype=np.uint8).reshape(img.height, img.width, 4)[::-1, :, :3].astype(int).copy()
  rl.unload_image(img)
  return arr


def make_stock(view):
  """The view with every FORK(HUD) call site neutralised: what the unchanged upstream drawing would show."""
  saved = (hud_alerts.draw_compact, hud_alerts.frees_top_icons)
  hud_alerts.draw_compact = lambda ar, alert: False
  hud_alerts.frees_top_icons = lambda alert: False
  view._hud_cluster.render = lambda *a, **k: None
  view._hud_rail.render = lambda *a, **k: None
  view._hud_renderer._draw_approach = lambda *a, **k: None
  view._hud_renderer._approach_top_icon = lambda: False
  return saved


def unstock(saved):
  hud_alerts.draw_compact, hud_alerts.frees_top_icons = saved


def frame_once(view):
  reset_calls()
  rl.begin_drawing()
  rl.begin_texture_mode(rt)
  rl.clear_background(rl.BLACK)
  view.render(rect)
  rl.end_texture_mode()
  rl.end_drawing()


def render(name, params, stock=False, frames=100, t0=100.0, jump_at=None, jump=0.0):
  """A fresh view, `frames` frames at 20 fps on the sim clock; the last frame is returned. 100 frames = 5 s: past the
  2.5 s the stock MAX number shows after engaging (and its fade)."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()  # a fresh view is a fresh UI start
  view = AugmentedRoadView()
  view.set_rect(rect)
  saved = make_stock(view) if stock else None
  try:
    for i in range(frames):
      clock["t"] = t0 + i * 0.05 + (jump if jump_at is not None and i >= jump_at else 0.0)
      frame_once(view)
  finally:
    if saved:
      unstock(saved)
  last.clear()
  last.update({k: v for k, v in calls.items() if k != "card"}, top_icons=bool(view._hud_renderer.drawing_top_icons()))
  arr = grab()
  save(arr, name)
  view.close()
  return arr


def save(arr, name):
  if OUT:
    from PIL import Image
    Image.fromarray(arr.astype(np.uint8)).save(os.path.join(OUT, f"{name}.png"))


phase_calls = []  # run(): each phase's last frame, as last


def run(params, steps, stock=False, t0=100.0, setup=None):
  """ONE view through several phases, as a drive goes: steps = [(scene kwargs, frames, name)]. Returns the last frame
  of each phase (saved as name when HUD_RENDER_OUT is set). setup(view), when given, runs once the view is built."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()
  view = AugmentedRoadView()
  view.set_rect(rect)
  if setup is not None:
    setup(view)
  saved = make_stock(view) if stock else None
  grabs, t = [], t0
  phase_calls.clear()
  try:
    for kw, frames, name in steps:
      kw = dict(kw)
      t += kw.pop("_gap", 0.0)   # the road view not drawn for this long before the phase (swiped away)
      if "_params" in kw:        # the settings changed from this phase on (read at once, as the next second's read would)
        ui_state.params.v = dict(kw.pop("_params"))
        HS.hud_settings.get(force=True)
      scene(**kw)
      for _ in range(frames):
        clock["t"] = t
        t += 0.05
        frame_once(view)
      grabs.append(grab())
      phase_calls.append(dict({k: v for k, v in calls.items() if k != "card"},
                              top_icons=bool(view._hud_renderer.drawing_top_icons()),
                              timer=view._hud_cluster.frame.timer_s is not None))
      save(grabs[-1], name.replace("_on", "_stock") if stock else name)
  finally:
    if saved:
      unstock(saved)
  view.close()
  return grabs


def box(arr, x0, y0, x1, y1):
  return arr[y0:y1, x0:x1]


def count(arr, b, kind):
  p = box(arr, *b)
  r, g, bl = p[..., 0], p[..., 1], p[..., 2]
  if kind == "red":
    m = (r > 150) & (g < 90) & (bl < 90)
  elif kind == "white":
    m = (r > 200) & (g > 200) & (bl > 200)
  elif kind == "orange":
    m = (r > 80) & (g < r * 0.6) & (g > r * 0.2) & (bl < 40)
  elif kind == "green":
    m = (g > 200) & (r < 120) & (bl < 160)
  elif kind == "dark":
    m = (r < 40) & (g < 40) & (bl < 40)
  elif kind == "grey":
    m = (abs(r - g) < 12) & (abs(g - bl) < 12) & (r > 50) & (r < 180)
  else:
    raise KeyError(kind)
  return int(m.sum())


def same(a, b):
  return int((np.abs(a - b).max(axis=2) > 0).sum())


def over1(a, b):
  """Pixels more than one level apart: the stock MAX number's fade, which the card hands over at full, is still a level
  short of it a second and a half into an ordinary drive."""
  return int((np.abs(a - b).max(axis=2) > 1).sum())


def diff_box(a, b):
  ys, xs = np.nonzero(np.abs(a - b).max(axis=2) > 0)
  return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None


TIMER = (300, 10, 470, 62)   # the stop timer, in the corner
TOP_RIGHT = (200, 0, 476, 110)
TOP_LEFT = (0, 0, 200, 140)
BANNER = (8, 6, 200, 72)
LOWER = (100, 110, 400, 150)
STRIP = 476                  # the confidence ball's strip: x 476..536
RAIL = (477, 4, 536, 80)     # the rail's item: a 48 px glyph from y 6, figures to y 76
BALL_HIGH = (476, 0, 536, 89)
BALL_LOW = (476, 89, 536, 140)
MAX_BOX = (0, 0, 170, 140)


def strip_diff(a, b):
  return same(a[:, STRIP:], b[:, STRIP:])


def stop_gap(arr):
  """(the compact banner's right edge, the left edge of what the cluster draws right of it), rows of the top row."""
  p = arr[6:72]
  orange = (p[..., 0] > 80) & (p[..., 1] < p[..., 0] * 0.6) & (p[..., 1] > p[..., 0] * 0.2) & (p[..., 2] < 40)
  cols = np.nonzero(orange.any(axis=0))[0]
  right = int(cols.max()) if len(cols) else -1
  q = arr[8:64, right + 1:476]
  white = (q[..., 0] > 200) & (q[..., 1] > 200) & (q[..., 2] > 200)
  wc = np.nonzero(white.any(axis=0))[0]
  return right, (int(wc.min()) + right + 1 if len(wc) else -1)


out = {}
APPROACH = dict(ahead=100, ahead_dist=300.0)   # the M4: 110, a 100 in 300 m (window 458 m at 110 km/h)
states = {
  "cruise": dict(),
  "cruise_slow": dict(limit=50, v=40 / KPH),
  "approach_lower": dict(APPROACH),
  "approach_higher": dict(limit=50, v=50 / KPH, ahead=70, ahead_dist=90.0),   # Bringelly Rd, 50 -> 70
  "approach_near": dict(ahead=100, ahead_dist=30.0),                           # 'Near': drawn 'now'
  "approach_far": dict(v=20.0, ahead=100, ahead_dist=480.0),                   # out of the 15 s window
  "approach_tunnel": dict(APPROACH, nsw_state=4),                              # dead reckoning: never
  "approach_car": dict(APPROACH, source="car"),                                # a car-sourced limit: never
  "approach_standstill": dict(APPROACH, v=0.0, standstill=True),
  "confirm": dict(limit=50, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105),
  "confirm_offset": dict(limit=50, offset=5, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105),
  "stopped": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, lead=0.1),
  "stopped_1h": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, lead=0.1),
  "stopped_lead_departs": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, lead=2.0),
  "stopped_no_alert": dict(limit=40, v=0.0, standstill=True),
  "missing_plan": dict(lp=False),
  "missing_carstate": dict(cs=False),
  # the right rail; the model fully confident, so the stock ball would sit at the very top of the strip
  "rail_stop": dict(v=30 / KPH, limit=50, rail=dict(stop_m=25.0, solid=True, confident=True)),
  "rail_stop_grey": dict(v=30 / KPH, limit=50, rail=dict(stop_m=25.0, solid=False, confident=True)),
  "rail_stop_lead": dict(v=30 / KPH, limit=50, lead=0.0, rail=dict(stop_m=25.0, confident=True)),
  "rail_stop_standstill": dict(v=0.0, standstill=True, limit=50, rail=dict(stop_m=25.0, confident=True)),
  "rail_curve": dict(v=45 / KPH, limit=60, rail=dict(curve_kph=37, confident=True)),
  "rail_both": dict(v=45 / KPH, limit=60, rail=dict(stop_m=25.0, curve_kph=37, confident=True)),
  # compact alerts
  "confirm_compact": dict(limit=50, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105),
  "confirm_compact_plus": dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40),
  "confirm_compact_offset": dict(limit=50, offset=5, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105),
  # the text asks for '+' while sunnypilot's arrow points down (its rounding at .5 ties): the key follows the text
  "confirm_compact_text_vs_arrow": dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=105),
  "confirm_compact_empty": dict(limit=50, v=27 / KPH, alert=CONFIRM_EMPTY, assist="preActive", set_kph=50),
  # no limit at all: nothing to put in the MAX number's place - the banner
  "confirm_compact_no_limit": dict(limit=0, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40),
  # a confirm while the look-ahead has another limit coming: the box, never the coming limit
  "confirm_compact_approach": dict(limit=50, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105, ahead=70,
                                   ahead_dist=143.0),
  # the target 80, for the stock MAX number '80' of "changed" to measure it against
  "confirm_compact_80": dict(limit=80, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105),
  "changed": dict(limit=80, v=60 / KPH, alert=CHANGED, set_kph=80),
  "disengage": dict(alert=DISENGAGE),
  "disengage_approach": dict(APPROACH, alert=DISENGAGE),   # a banner up: the coming limit waits
  "turn": dict(limit=50, v=20 / KPH, alert=TURN),
  "critical": dict(alert=CRITICAL),
  "critical_approach": dict(APPROACH, alert=CRITICAL),
}
PARAMS = {"confirm": FULL_LIMIT, "confirm_offset": FULL_LIMIT}

JUMP_S = {"stopped_1h": 3725.0}  # stopped for 1:02:05
images, stocks = {}, {}
for name, kw in states.items():
  scene(**kw)
  stopped = kw.get("standstill", False)
  jump = dict(jump_at=10, jump=JUMP_S.get(name, 30.0)) if stopped else {}
  on = render(f"{name}_on", PARAMS.get(name, ON), **jump)
  on_calls = dict(last)
  off = render(f"{name}_off", OFF, **jump)
  stock = render(f"{name}_stock", ON, stock=True, **jump)
  r = {"off_vs_stock": same(off, stock), "on_vs_stock": same(on, stock), "on_diff_box": diff_box(on, stock),
       "strip_vs_stock": strip_diff(on, stock), "calls": on_calls}
  for b, bn in ((TIMER, "timer"), (TOP_RIGHT, "topright"), (TOP_LEFT, "topleft"), (BANNER, "banner"), (LOWER, "lower"),
                (RAIL, "rail"), (BALL_HIGH, "ballhi"), (BALL_LOW, "balllo")):
    for kind in ("red", "white", "orange", "green", "dark", "grey"):
      r[f"{bn}_{kind}"] = count(on, b, kind)
      r[f"stock_{bn}_{kind}"] = count(stock, b, kind)
  if stopped:
    r["banner_right"], r["cluster_left"] = stop_gap(on)
  out[name] = r
  images[name], stocks[name] = on, stock

# the coming limit's setting off: the stock screen; and while the stock MAX number shows (the first 2.5 s after
# engaging) it waits
scene(**states["approach_lower"])
out["approach_setting_off_vs_stock"] = same(render("approach_lower_setting_off", NO_APPROACH), stocks["approach_lower"])
early = render("approach_while_max_on", ON, frames=20)
early_calls = dict(last)
out["approach_while_max"] = {"calls": early_calls, "vs_stock": same(early, render("approach_while_max_stock", ON, frames=20,
                                                                                    stock=True)),
                             "max_white": count(early, MAX_BOX, "white")}


# compact alerts: each against the same scene with no alert at all - what the alert adds, and where
def cluster_left(arr):
  """The left edge of what the cluster drew in its top rows (anything brighter than its shadows), right of x 200."""
  p = arr[6:66, 200:476]
  cols = np.nonzero((p.max(axis=2) > 40).any(axis=0))[0]
  return int(cols.min()) + 200 if len(cols) else 476


compact = {}
for name in ("confirm_compact", "confirm_compact_plus", "confirm_compact_offset", "confirm_compact_text_vs_arrow",
             "confirm_compact_empty", "changed", "disengage", "turn", "stopped", "confirm_compact_no_limit"):
  kw = {k: v for k, v in states[name].items() if k != "alert"}
  scene(**kw)
  stopped = kw.get("standstill", False)
  base = render(f"{name}_noalert_on", PARAMS.get(name, ON), **(dict(jump_at=10, jump=30.0) if stopped else {}))
  img, cl = images[name], cluster_left(base)
  ys, xs = np.nonzero(np.abs(img - base).max(axis=2) > 0)
  left = xs < cl
  compact[name] = {"px": same(img, base), "box": diff_box(img, base), "cluster_left": cl,
                   "banner": [int(xs[left].min()), int(ys[left].min()), int(xs[left].max()), int(ys[left].max())]
                   if left.any() else None,
                   "cluster_px": int((~left).sum()),
                   "banner_red": count(img, (0, 0, cl, 160), "red"), "banner_green": count(img, (0, 0, cl, 160), "green"),
                   "banner_white": count(img, (0, 0, cl, 160), "white"), "calls": out[name]["calls"],
                   "lower_white": count(img, (0, 100, 476, 240), "white")}
out["compact"] = compact

# the full-screen versions: each group's own setting off draws its alert exactly as stock
for name, key in (("changed", HS.PARAM_COMPACT_LIMIT), ("disengage", HS.PARAM_COMPACT_DISENGAGE),
                  ("turn", HS.PARAM_COMPACT_TURN), ("confirm_compact", HS.PARAM_COMPACT_LIMIT)):
  scene(**states[name])
  full = render(f"{name}_full_on", {**ON, key: False})
  out[f"{name}_full_vs_stock"] = same(full, stocks[name])

# 'set speed changed', compact: nothing over the road, so the stock MAX number shows the new set speed (the first 2.5 s
# after a change - here, after engaging)
scene(**states["changed"])
early = {k: render(f"changed_max_{k}", p, frames=20, stock=(k == "stock")) for k, p in
         (("on", ON), ("full", FULL_LIMIT), ("stock", ON))}
out["changed_max"] = {k: {"max_white": count(v, MAX_BOX, "white"), "mid_white": count(v, (180, 0, 400, 130), "white")}
                      for k, v in early.items()}
scene(**{k: v for k, v in states["changed"].items() if k != "alert"})
out["changed_max"]["noalert_vs_on"] = same(render("changed_max_noalert_on", ON, frames=20), early["on"])

# stock-plus: the confirm's target is the stock MAX number - the same digits, drawn in the same place at the same size:
# '80' of the compact confirm (limit 80) against '80' of the stock MAX number after "changed" (45 frames: faded fully
# in, not yet out), inside the digits' ink box (the card's outline is outside it)
from openpilot.system.ui.lib.application import FontWeight
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import FONT_SIZES


def white_h(arr, b):
  p = box(arr, *b)
  rows = np.nonzero(((p[..., 0] > 200) & (p[..., 1] > 200) & (p[..., 2] > 200)).any(axis=1))[0]
  return int(rows.max() - rows.min() + 1) if len(rows) else 0


scene(**states["confirm_compact_80"])
plus80 = render("confirm_compact_80_d1", D1)
plus80_calls = dict(last)
scene(**states["changed"])
max80 = render("changed_max_45_on", ON, frames=45)
iw, ih, ix0, iy0 = hud_draw.ink("80", FONT_SIZES.set_speed, 1.0, FontWeight.DISPLAY)
ib = (int(17 + ix0) + 1, int(-4 + iy0) + 1, int(17 + ix0 + iw) - 1, int(-4 + iy0 + ih) - 1)
around = (ib[0], max(0, ib[1] - 4), ib[2], ib[3] + 4)
out["digits"] = {"ink_box": ib, "ink_h": round(ih, 1),
                 "diff": same(box(plus80, *ib), box(max80, *ib)),
                 "box_h": white_h(plus80, around), "max_h": white_h(max80, around), "calls": plus80_calls}

# the confirm's outline, frame by frame from its first: two flashes, then the split card's march / the stock-plus card's
# breathing - against the same frames with the outline held still (box_outline patched to 'not lit, no phase',
# box_breath to full): only the outline differs, never the digits (the target's ink box, inset a pixel)
CONF = dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40)
CONF_NONE = {k: v for k, v in CONF.items() if k != "alert"}
N_ANIM = 40


def outline_anim(params, tag):
  a = run(params, [(CONF_NONE, 100, f"{tag}_00_before_on")] + [(CONF, 1, f"{tag}_{i + 1:02d}_on") for i in range(N_ANIM)])
  a_calls = list(phase_calls)
  card = calls["card"]
  real = hud_renderer_sp.box_outline, hud_renderer_sp.box_breath
  hud_renderer_sp.box_outline, hud_renderer_sp.box_breath = (lambda t: (False, None)), (lambda t: 1.0)
  try:
    b = run(params, [(CONF_NONE, 100, f"{tag}_00_before_still")] +
            [(CONF, 1, f"{tag}_{i + 1:02d}_still") for i in range(N_ANIM)])
  finally:
    hud_renderer_sp.box_outline, hud_renderer_sp.box_breath = real
  if params[HS.PARAM_LIMIT_STYLE] == HS.STYLE_SPLIT:
    tx, ty, tw, th = hud_draw.split_layout(card, 0, 0).target
  else:
    tw, th, tx, ty = hud_draw.ink(str(card.value), FONT_SIZES.set_speed, 1.0, FontWeight.DISPLAY)
    tx, ty = 17 + tx, -4 + ty
  db = (int(tx) + 1, int(ty) + 1, int(tx + tw) - 1, int(ty + th) - 1)
  return {
    "lit": [c["lit"] for c in a_calls[1:]], "phase": [c["phase"] for c in a_calls[1:]],
    "breath": [c["breath"] for c in a_calls[1:]], "box": [c["box"] for c in a_calls[1:]],
    "text": a_calls[-1]["box_text"], "style": a_calls[-1]["style"], "digits_box": db,
    "digits_vs_still": [same(box(x, *db), box(y, *db)) for x, y in zip(a[1:], b[1:], strict=True)],
    "outline_vs_still": [same(box(x, 0, 0, 300, 170), box(y, 0, 0, 300, 170)) for x, y in zip(a[1:], b[1:], strict=True)],
    "outline_lit": [int((box(x, 0, 0, 300, 170).max(axis=2) > 60).sum()) for x in a[1:]],
    "frame_to_frame": [same(box(x, 0, 0, 300, 170), box(y, 0, 0, 300, 170)) for x, y in zip(a[1:], a[2:], strict=False)],
  }


out["anim"] = outline_anim(ON, "anim")
out["anim_d1"] = outline_anim(D1, "anim_d1")
anim_calls = [None] + [{"lit": x} for x in out["anim"]["lit"]]

# a second prompt while the first box is still fading out, and the target changing under one (fix round 1): each its
# own two flashes - the box never left the screen, so only its outline says the request is new
again = run(ON, [(CONF, 60, "again_00_on"), (CONF_NONE, 2, "again_01_fading_on")] +
            [(CONF, 1, f"again_{i + 2:02d}_on") for i in range(16)])
again_calls = list(phase_calls)
retarget = run(ON, [(CONF, 60, "retarget_00_on")] + [(dict(CONF, limit=45), 1, f"retarget_{i + 1:02d}_on") for i in range(16)])
retarget_calls = list(phase_calls)
out["rearm"] = {"again": {"box": [c["box"] for c in again_calls], "lit": [c["lit"] for c in again_calls]},
                "retarget": {"box": [c["box"] for c in retarget_calls], "lit": [c["lit"] for c in retarget_calls]}}

# design A through a drive: the confirm's box, then "set speed changed" - the solid stock MAX number takes its place - or
# a critical alert, which takes the screen on its first frame. Each against the same phases with no confirm before them.
CHANGED50 = dict(CONF_NONE, alert=CHANGED, set_kph=50, assist="active")
CRIT = dict(CONF, alert=CRITICAL)
seq = {}
steps = [(CONF, 60, "seq_changed_1_confirm_on"), (CHANGED50, 1, "seq_changed_2_first_frame_on"), (CHANGED50, 40, "seq_changed_3_on")]
a = run(ON, steps)
a_calls = list(phase_calls)
b = run(ON, [(CONF_NONE, 60, "seq_changed_ref_1_on")] + steps[1:])
seq["changed"] = {"calls": a_calls, "end_vs_ref": same(a[2], b[2]), "max_white": count(a[2], MAX_BOX, "white")}


def critical_after(n, tag):
  steps = [(CONF_NONE, 100, f"seq_{tag}_0_on"), (CONF, n, f"seq_{tag}_1_confirm_on"), (CRIT, 1, f"seq_{tag}_2_first_frame_on"),
           (CRIT, 40, f"seq_{tag}_3_on")]
  a = run(ON, steps)
  a_calls = list(phase_calls)
  b = run(ON, steps, stock=True)
  return {"calls": a_calls[1:], "end_vs_stock": same(a[3], b[3]),
          "first_vs_stock_top_left": same(box(a[2], 0, 0, 260, 170), box(b[2], 0, 0, 260, 170))}


seq["critical"] = critical_after(60, "critical")
# in the middle of a flash: the last lit frame of the second flash
lit_frames = [i + 1 for i, c in enumerate(anim_calls[1:]) if c["lit"]]
seq["critical_lit"] = critical_after(lit_frames[-1], "critical_lit")

# the frame the confirm ends, against the same drive with the confirm drawn as today's banner (no HUD renderer to draw
# the box: pending_max.drawer off) - which is the unchanged build's screen. Whatever replaces the confirm - a compact
# banner, the limit lost (the banner fallback), no alert at all, the road view coming back after a while - the face is
# back on the frame it is there, and from then on the frames are the same.
CONF_NOLIMIT = dict(CONF, limit=0)


def ends(follow, n1=1, n2=5, n3=12):
  steps = [(CONF, 60, "seq_end_0_confirm_on"), (follow, n1, "seq_end_1_on"), (follow, n2, "seq_end_2_on"),
           (follow, n3, "seq_end_3_on")]
  a = run(ON, steps)
  a_calls = list(phase_calls)
  try:
    # the view's HUD renderer sets drawer when it is built: unset it after
    b = run(ON, steps, setup=lambda view: setattr(hud_alerts.pending_max, "drawer", False))
    b_calls = list(phase_calls)
  finally:
    hud_alerts.pending_max.drawer = True
  return {"calls": a_calls, "ref_calls": b_calls,
          "px": [same(x, y) for x, y in zip(a[1:], b[1:], strict=True)],
          "px_right": [same(box(x, 320, 0, 536, 240), box(y, 320, 0, 536, 240)) for x, y in zip(a[1:], b[1:], strict=True)]}


seq["ends"] = {"disengage": ends(dict(CONF_NONE, alert=DISENGAGE)), "turn": ends(dict(CONF_NONE, alert=TURN)),
               "no_limit": ends(CONF_NOLIMIT), "no_alert": ends(CONF_NONE),
               "back_later": ends(dict(CONF_NONE, _gap=5.0)), "back_changed": ends(dict(CHANGED50, _gap=5.0))}
# the confirm's first frame: the box is a top icon from it on
a = run(ON, [(CONF_NONE, 100, "seq_start_0_on"), (CONF, 1, "seq_start_1_on")])
seq["start"] = {"calls": list(phase_calls)}
# the confirm's grace (speed_limit_assist.PRE_ACTIVE_CONFIRM_GRACE, 2026-10-06): the prompt times out (the planner
# goes inactive, no alert) and the box goes with it, as with "no_alert" above; a press inside the grace then confirms -
# card sets the limit, the planner goes active ('set speed changed' or 'auto adjusting'). Then the screen is the stock
# solid MAX number: exactly the drive's screen with no prompt before it. 8 frames = route 120 (a '+' released 0.41 s
# after the timeout), 28 = route 121 (a '-' confirming on its first long-press step 1.38 s after it), 1 = at once (the
# box still fading with the prompt).
CONF_TIMEOUT = dict(CONF_NONE, assist="inactive")


def grace(gap, alert):
  confirmed = dict(CONF_NONE, alert=alert, set_kph=50, assist="active")
  steps = [(CONF_TIMEOUT, gap, "seq_grace_1_timeout_on"), (confirmed, 1, "seq_grace_2_first_frame_on"),
           (confirmed, 40, "seq_grace_3_on")]
  a = run(ON, [(CONF, 60, "seq_grace_0_confirm_on")] + steps)
  a_calls = list(phase_calls)
  b = run(ON, [(CONF_TIMEOUT, 60, "seq_grace_ref_0_on")] + steps)
  return {"calls": a_calls, "px": [same(x, y) for x, y in zip(a[1:], b[1:], strict=True)],
          "max_white": count(a[3], MAX_BOX, "white")}


seq["grace"] = {"r120_changed": grace(8, CHANGED), "r120_active": grace(8, ACTIVE), "r121_changed": grace(28, CHANGED),
                "at_once_changed": grace(1, CHANGED)}

# the coming limit through a drive (option C), one view: the M4 110 -> 100 - announced at 400 m, nearer, the look-ahead
# gone (held: 'now'), then the resolver takes 100 and Speed Limit Assist asks to confirm (the box takes the same place)
M4 = dict(limit=110, v=110 / KPH, set_kph=110)
steps = [(dict(M4, ahead=100, ahead_dist=400.0), 100, "c_m4_1_announce_on"),
         (dict(M4, ahead=100, ahead_dist=150.0, map_frame=6), 10, "c_m4_2_nearer_on"),
         (dict(M4, ahead=100, ahead_dist=11.0, map_frame=7), 1, "c_m4_3_at_on"),
         (dict(M4, map_frame=8), 10, "c_m4_4_held_on"),
         (dict(M4, limit=100, alert=CONFIRM, assist="preActive", map_frame=9), 1, "c_m4_5_handover_first_on"),
         (dict(M4, limit=100, alert=CONFIRM, assist="preActive", map_frame=9), 30, "c_m4_6_confirm_on")]
m4 = run(ON, steps)
seq["m4"] = {"calls": list(phase_calls), "topleft_white": [count(g, TOP_LEFT, "white") for g in m4]}
# ... the same with the resolver taking it and no prompt (Speed Limit Assist off): it goes, nothing else comes
steps_quiet = steps[:4] + [(dict(M4, limit=100, map_frame=9), 1, "c_quiet_5_on"), (dict(M4, limit=100, map_frame=9), 40,
                                                                                    "c_quiet_6_on")]
quiet = run(ON, steps_quiet)
quiet_calls = list(phase_calls)
quiet_ref = run(NO_APPROACH, steps_quiet)
seq["quiet"] = {"calls": quiet_calls, "end_vs_off": same(quiet[5], quiet_ref[5])}
# a rising limit, Bringelly Rd 50 -> 70: announced with the chevron up, then the set speed follows (auto adjusting,
# compact: the stock MAX number takes the place)
BR = dict(limit=50, v=50 / KPH, set_kph=50)
steps = [(dict(BR, ahead=70, ahead_dist=150.0), 100, "c_br_1_announce_on"),
         (dict(BR, limit=70, set_kph=70, alert=ACTIVE, map_frame=6), 1, "c_br_2_first_on"),
         (dict(BR, limit=70, set_kph=70, alert=ACTIVE, map_frame=6), 30, "c_br_3_max_on")]
br = run(ON, steps)
seq["rising"] = {"calls": list(phase_calls), "max_white": count(br[2], MAX_BOX, "white")}
# the held limit is never drawn for longer than its hold: the look-ahead gone, the resolver never taking it
steps = [(dict(M4, ahead=100, ahead_dist=400.0), 100, "c_hold_1_on"),
         (dict(M4, ahead=100, ahead_dist=11.0, map_frame=6), 1, "c_hold_2_at_on"), (dict(M4, map_frame=7), 50, "c_hold_3_on"),
         (dict(M4, map_frame=7), 60, "c_hold_4_on")]
hold = run(ON, steps)
seq["hold"] = {"calls": list(phase_calls)}
# the look-ahead lost far from the change (a 1 Hz message missed): still a distance, never 'now'; a new figure takes
# over; lost for longer than APPROACH_GAP_S it goes (fix round 1)
steps = [(dict(M4, ahead=100, ahead_dist=400.0), 100, "c_gap_1_on"), (dict(M4, map_frame=6), 10, "c_gap_2_on"),
         (dict(M4, ahead=100, ahead_dist=330.0, map_frame=7), 1, "c_gap_3_on"), (dict(M4, map_frame=8), 100, "c_gap_4_on")]
gap = run(ON, steps)
seq["gap"] = {"calls": list(phase_calls)}
out["seq"] = seq

# ----------------------------------------------------------------------------------------- the style (HudLimitStyle)
# stock-plus (D1) through the same states and drives; and every state neither card draws: the same pixels in both
styles = {}
for name in ("approach_lower", "approach_higher", "approach_near", "confirm_compact", "confirm_compact_plus",
             "confirm_compact_offset", "confirm_compact_no_limit"):
  scene(**states[name])
  render(f"{name}_d1", D1)
  styles[name] = dict(last)
UNTOUCHED = ("cruise", "cruise_slow", "approach_far", "approach_tunnel", "approach_car", "approach_standstill", "confirm",
             "stopped", "stopped_no_alert", "missing_plan", "missing_carstate", "rail_stop", "rail_curve", "changed",
             "disengage", "disengage_approach", "turn", "critical", "critical_approach", "confirm_compact_empty")
untouched = {}
for name in UNTOUCHED:
  kw = states[name]
  scene(**kw)
  jump = dict(jump_at=10, jump=JUMP_S.get(name, 30.0)) if kw.get("standstill", False) else {}
  untouched[name] = same(render(f"{name}_d1", {**PARAMS.get(name, ON), HS.PARAM_LIMIT_STYLE: HS.STYLE_STOCK_PLUS},
                                **jump), images[name])
styles["untouched"] = untouched
# the M4 hand-over and the confirm, stock-plus: the coming limit's card grows into the confirm's (no fade between)
M4D = dict(limit=110, v=110 / KPH, set_kph=110)
m4_steps = [(dict(M4D, ahead=100, ahead_dist=150.0), 100, "s_m4_1_coming"), (dict(M4D, map_frame=6), 10, "s_m4_2_held"),
            (dict(M4D, limit=100, alert=CONFIRM, assist="preActive", map_frame=7), 1, "s_m4_3_first"),
            (dict(M4D, limit=100, alert=CONFIRM, assist="preActive", map_frame=7), 3, "s_m4_4_growing"),
            (dict(M4D, limit=100, alert=CONFIRM, assist="preActive", map_frame=7), 30, "s_m4_5_waiting"),
            (dict(M4D, limit=100, set_kph=100, alert=CHANGED, assist="active", map_frame=7), 1, "s_m4_6_pressed"),
            (dict(M4D, limit=100, set_kph=100, alert=CHANGED, assist="active", map_frame=7), 3, "s_m4_7_done"),
            (dict(M4D, limit=100, set_kph=100, alert=CHANGED, assist="active", map_frame=7), 30, "s_m4_8_max")]
ref_steps = m4_steps[:5] + [(dict(M4D, limit=100, set_kph=100, alert=CHANGED, assist="active", map_frame=7, _gap=5.0), 1,
                             "s_m4_ref_6")] + m4_steps[6:]
for tag, prm in (("d3", ON), ("d1", D1)):
  g = run(prm, [(kw, n, f"{nm}_{tag}") for kw, n, nm in m4_steps])
  g_calls = list(phase_calls)
  # the reference: the same drive with no card left to turn into MAX (the road view away for 5 s before the press)
  rf = run(prm, [(kw, n, f"{nm}_{tag}") for kw, n, nm in ref_steps])
  styles[f"m4_{tag}"] = {"calls": g_calls, "max_white": count(g[7], MAX_BOX, "white"), "end_vs_ref": over1(g[7], rf[7]),
                         "done_frames": None}
  # every frame of the press, one at a time
  steps = m4_steps[:5] + [(dict(M4D, limit=100, set_kph=100, alert=CHANGED, assist="active", map_frame=7), 1,
                           f"s_m4_press_{i:02d}_{tag}") for i in range(10)]
  run(prm, [(kw, n, f"{nm}_{tag}") if i < 5 else (kw, n, nm) for i, (kw, n, nm) in enumerate(steps)])
  styles[f"m4_{tag}"]["press"] = [dict(done=c["done"], box=c["box"], done_args=c["done_args"], top_icons=c["top_icons"])
                                  for c in phase_calls[5:]]
# the style switched while a card is up - coming, waiting, turning into MAX: the next frames in the other style, no gap
switch = {}
for when, at in (("coming", 1), ("waiting", 4), ("done", 5)):
  steps = [((dict(kw, _params=D1) if i == at else kw), n, f"s_switch_{when}_{i}") for i, (kw, n, nm) in enumerate(m4_steps)]
  run(ON, steps)
  switch[when] = [dict(style=c["style"], approach=c["approach"], box=c["box"], done=c["done"], top_icons=c["top_icons"])
                  for c in phase_calls]
styles["switch"] = switch
out["styles"] = styles

# not engaged (review M1): the coming limit still comes (it needs no engagement), but the stock MAX is never drawn then
# and the set speed is only the one this non-PCM car kept - the split card's MAX half shows the stock disabled char
not_engaged = {}
for tag, prm in (("d3", ON), ("d1", D1)):
  scene(**states["approach_lower"], enabled=False)
  img = render(f"approach_lower_not_engaged_{tag}", prm)
  not_engaged[tag] = dict(last, max_white=count(img, MAX_BOX, "white"))
styles["not_engaged"] = not_engaged

# the widest confirm (review M2): a three-digit target and the offset note, 'press - to set 110' + 'limit 100 +10'.
# Moving, the card stops 40 px short of the road's edge; stopped, short of the stop timer (its widest, past an hour
# too), which is then untouched - the same pixels as with no confirm, from its left edge on
WIDE = dict(limit=100, offset=10, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=130)
wide = {}
for tag, prm in (("d3", ON), ("d1", D1)):
  for where, kw, jump in (("moving", WIDE, None), ("stopped", dict(WIDE, v=0.0, standstill=True), 30.0),
                          ("stopped_1h", dict(WIDE, v=0.0, standstill=True), 3725.0)):
    j = dict(jump_at=10, jump=jump) if jump else {}
    scene(**kw)
    img = render(f"confirm_wide_{where}_{tag}", prm, **j)
    c = dict(last)
    edge = hud_alerts.cluster_edge.timer_left
    w = {"calls": c, "timer_left": None if edge == float("inf") else round(edge, 1)}
    if w["timer_left"] is not None:
      scene(**{k: v for k, v in kw.items() if k != "alert"})
      ref = render(f"confirm_wide_{where}_noalert_{tag}", prm, **j)
      x = int(edge) - 1
      w["timer_vs_noalert"] = same(box(img, x, 0, STRIP, 80), box(ref, x, 0, STRIP, 80))
      w["timer_white"] = count(img, (x, 0, STRIP, 80), "white")
    wide[f"{where}_{tag}"] = w
styles["wide"] = wide

# the card turning into MAX while the prompt was already fading out (review n1): from the strength it was drawn at
fade_done = {}
for k in (1, 2, 3, 4):
  confirmed = dict(CONF_NONE, alert=CHANGED, set_kph=50, assist="active")
  run(ON, [(CONF, 60, f"fade_done_{k}_0"), (CONF_TIMEOUT, k, f"fade_done_{k}_1")] +
      [(confirmed, 1, f"fade_done_{k}_{i + 2}") for i in range(9)])
  fade_done[k] = [dict(box=c["box"], box_alpha=c["box_alpha"], done=c["done"], done_alpha=c["done_alpha"],
                       done_u=c["done_args"][2] if c["done_args"] else None) for c in phase_calls[1:]]
out["fade_done"] = fade_done

# the rail's own settings: each off alone, and both off = the stock strip
toggles = {}
for name, label, prm in (("rail_stop", "stop_off", {**ON, HS.PARAM_PLANNED_STOP: False}),
                         ("rail_curve", "curve_off", {**ON, HS.PARAM_CURVE: False}),
                         ("rail_both", "stop_off", {**ON, HS.PARAM_PLANNED_STOP: False}),
                         ("rail_stop", "both_off", RAIL_OFF), ("rail_curve", "both_off", RAIL_OFF),
                         ("rail_both", "both_off", RAIL_OFF)):
  scene(**states[name])
  img = render(f"{name}_{label}", prm)
  toggles[f"{name}_{label}"] = {"strip_vs_stock": strip_diff(img, stocks[name]),
                                "strip_vs_curve": strip_diff(img, images["rail_curve"])}
out["rail_toggles"] = toggles
out["rail_both_vs_stop"] = strip_diff(images["rail_both"], images["rail_stop"])

# one drive: cruising, a stop appears (debounced) and fades in as the ball eases down, stays a moment after the plan
# stops stopping, fades out, goes; the ball returns
CRUISE_RAIL = dict(v=30 / KPH, limit=50, rail=dict(stop_m=None, confident=True))
steps = [(CRUISE_RAIL, 40, "rail_seq_1_cruise_on"), (states["rail_stop"], 4, "rail_seq_2_debounce_on"),
         (states["rail_stop"], 3, "rail_seq_3_fade_in_on"), (states["rail_stop"], 17, "rail_seq_4_stop_on"),
         (CRUISE_RAIL, 6, "rail_seq_5_held_on"), (CRUISE_RAIL, 6, "rail_seq_6_fade_out_on"),
         (CRUISE_RAIL, 40, "rail_seq_7_gone_on")]
seq_on, seq_stock = run(ON, steps), run(ON, steps, stock=True)
out["rail_seq"] = {"strip_vs_stock": [strip_diff(a, b) for a, b in zip(seq_on, seq_stock, strict=True)],
                   "rail_white": [count(g, RAIL, "white") for g in seq_on],
                   "rail_grey": [count(g, RAIL, "grey") for g in seq_on],
                   "ballhi_green": [count(g, BALL_HIGH, "green") for g in seq_on],
                   "balllo_green": [count(g, BALL_LOW, "green") for g in seq_on],
                   "stock_ballhi_green": [count(g, BALL_HIGH, "green") for g in seq_stock]}

# the stop time's size: full up to 59:59, shrunk beyond
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_cluster import timer_size
out["timer_size"] = {t: round(timer_size(t), 3) for t in ("0:30", "59:59", "1:02:05")}

# an alert's own setting off, the rest on: that alert is drawn exactly as stock
for name, key in (("stopped", HS.PARAM_STOPPED_BANNER),):
  scene(**states[name])
  jump = dict(jump_at=10, jump=30.0)
  alone = render(f"{name}_only_its_setting_off", {**ON, key: False}, **jump)
  stock = render(f"{name}_stock2", ON, stock=True, **jump)
  out[f"{name}_only_its_setting_off_vs_stock"] = same(alone, stock)

# two stops in one drive: the second starts compact again (the banner's departure must not stick for the drive - the
# HUD renderer observes every frame, round 5)
STOP = dict(limit=40, v=0.0, standstill=True, alert=STOPPED)
steps = [(dict(STOP, lead=0.1), 40, "two_stops_1_stopped_on"),
         (dict(STOP, lead=2.0), 20, "two_stops_2_lead_departs_on"),
         (dict(limit=40, v=10.0, lead=8.0), 100, "two_stops_3_driving_on"),
         (dict(STOP, lead=0.1), 40, "two_stops_4_stopped_again_on")]
two = run(ON, steps)
out["two_stops"] = [{"banner_orange": count(g, BANNER, "orange"), "lower_orange": count(g, LOWER, "orange"),
                     "timer_white": count(g, TIMER, "white")} for g in two]

# an alert fading out during a stop: the timer comes back only once the alert has gone
STILL = dict(limit=40, v=0.0, standstill=True)
steps = [(dict(STILL, alert=SATURATED), 40, "fade_1_alert_on"), (STILL, 3, "fade_2_gone_3_frames_on"),
         (STILL, 60, "fade_3_gone_60_frames_on")]
fade_on, fade_stock = run(ON, steps), run(ON, steps, stock=True)
out["alert_fade"] = {"on_vs_stock": [same(a, b) for a, b in zip(fade_on, fade_stock, strict=True)],
                     "timer_white": [count(g, TIMER, "white") for g in fade_on]}

# the gateway tile's icon: icon B, and the old icon if B ever fails to load (an LFS pointer in place of the PNG)
from openpilot.selfdrive.ui.sunnypilot.mici.layouts import settings as mici_settings
icon_b = mici_settings.gateway_icon()
pointer = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"hud_gateway_pointer_{os.getpid()}.png")
with open(pointer, "wb") as fh:
  fh.write(b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 1734\n")
real_load = gui_app._load_image_from_path
for key in [k for k in gui_app._textures if "icons_mici/gateway.png" in k]:
  del gui_app._textures[key]  # load it again, through the loader
gui_app._load_image_from_path = lambda path, *a, **k: real_load(pointer if path.endswith("icons_mici/gateway.png") else path, *a, **k)
try:
  icon_fallback = mici_settings.gateway_icon()
finally:
  gui_app._load_image_from_path = real_load
  os.unlink(pointer)
software = gui_app.texture("../../sunnypilot/selfdrive/assets/offroad/icon_software.png", 70, 70)
out["gateway_icon"] = {"b": [icon_b.id, icon_b.width, icon_b.height], "fallback_id": icon_fallback.id,
                       "software_id": software.id}

# ----------------------------------------------------------------------------------------- sla-flash: the armed card
# A press at 'now' the planner holds (assist.armedLimit): the coming limit's number flashes - lit 0.4 s, dim 0.2 s, 8 and
# 4 frames at 20 fps - and nothing else on the card; 'sets 100 at sign' and no key; drawn from the arm with the
# look-ahead gone; when the arm drops it fades with the armed words and solid digits (a lapse), or the take's stock MAX
# number takes the place. The 'press' words come from the planner's offer, and only for that limit.
NOW_M4 = dict(M4, ahead=100, ahead_dist=30.0, map_frame=6)                    # 110, the 100 under 50 m: 'now'
ARMED = dict(NOW_M4, now_key=(-1, 100, 100), armed=(100, 100))
ARMED_GONE = dict(M4, map_frame=7, now_key=(-1, 100, 100), armed=(100, 100))  # the look-ahead gone, still armed
TAKEN = dict(M4, limit=100, set_kph=100, map_frame=8, alert=CHANGED)
LAPSED = dict(M4, map_frame=7)


def digit_box(card, style):
  """The coming limit's digits' ink box (+2 px of anti-aliasing), from the HUD rect's top left."""
  txt = str(int(card.value))
  if style == "split":
    tx, ty, tw, th = hud_draw.split_layout(card, 0, 0).target
  else:
    mx, my, _, _ = hud_draw._max_ink(txt)
    tw, th, _, _ = hud_draw.ink(txt, hud_draw.SP_APPROACH_SIZE, 1.0, FontWeight.DISPLAY)
    tx, ty = mx, my
  return (int(tx) - 2, int(ty) - 2, int(tx + tw) + 3, int(ty + th) + 3)


def outside(a, b, bx):
  """Pixels that differ outside the box bx."""
  d = np.abs(a - b).max(axis=2) > 0
  x0, y0, x1, y1 = bx
  d[max(0, y0):y1, max(0, x0):x1] = False
  return int(d.sum())


CARD_BOX = (0, 0, 300, 170)
# the stock MAX number has the place for 2.5 s after engaging (and its fade), and mapd's one message runs down to the
# change in a second and is held 3 s: cruise 5 s first, then 'now' for 2 s (its glow and the fade-in done)
LEAD_IN = [(M4, 100, "cruise"), (NOW_M4, 40, "now")]
armed_r = {}
views = []
for tag, prm in (("d3", ON), ("d1", D1)):
  g = run(prm, [(kw, n, f"armed_{tag}_0{i}_{nm}") for i, (kw, n, nm) in enumerate(LEAD_IN)] +
          [(ARMED, 1, f"armed_{tag}_{i + 1:02d}") for i in range(14)])
  g_calls = list(phase_calls)[1:]
  g = g[1:]
  style = g_calls[-1]["style"]
  bx = digit_box(calls["card"], style)
  fr = g_calls[1:]
  lit = [i + 1 for i, c in enumerate(fr) if c["approach_digits"] == 1.0]
  dim = [i + 1 for i, c in enumerate(fr) if c["approach_digits"] is not None and c["approach_digits"] < 1.0]
  r = {"digits": [c["approach_digits"] for c in fr], "armed": [c["approach_armed"] for c in fr],
       "text": [c["approach_args"][2] if c["approach_args"] else None for c in fr],
       "value": [c["approach_args"][0] if c["approach_args"] else None for c in fr],
       "top_icons": [c["top_icons"] for c in fr], "style": style, "box": bx,
       "now_text": g_calls[0]["approach_args"][2] if g_calls[0]["approach_args"] else None,
       "green": [count(x, CARD_BOX, "green") for x in g[1:]], "now_green": count(g[0], CARD_BOX, "green"),
       "lit_vs_dim": same(g[lit[0]], g[dim[0]]) if lit and dim else None,
       "lit_vs_dim_outside": outside(g[lit[0]], g[dim[0]], bx) if lit and dim else None,
       "lit_vs_lit": same(g[lit[0]], g[lit[1]]) if len(lit) > 1 else None,
       "dim_vs_dim": same(g[dim[0]], g[dim[1]]) if len(dim) > 1 else None,
       "lit_digit_white": count(g[lit[0]], bx, "white") if lit else None,
       "dim_digit_white": count(g[dim[0]], bx, "white") if dim else None}
  # the look-ahead gone (ApproachState drops the coming limit after its 3 s hold): still drawn, from the arm
  g = run(prm, LEAD_IN + [(ARMED, 10, f"armed_gone_{tag}_1"), (ARMED_GONE, 80, f"armed_gone_{tag}_2")],
          setup=views.append)
  r["gone"] = dict(phase_calls[-1], approach_state=views[-1]._hud_renderer._approach.frame is not None)
  # the take: the set speed becomes the limit (one change) - the stock MAX number takes the place, the card fades into it
  g = run(prm, LEAD_IN + [(ARMED, 20, f"armed_take_{tag}_1")] +
          [(TAKEN, 1, f"armed_take_{tag}_{i + 2:02d}") for i in range(12)] + [(TAKEN, 30, f"armed_take_{tag}_99")])
  r["take"] = [dict(approach=c["approach"], text=c["approach_args"][2] if c["approach"] else None,
                    digits=c["approach_digits"] if c["approach"] else None, alpha=c["approach_alpha"] if c["approach"] else None,
                    top_icons=c["top_icons"]) for c in phase_calls[3:]]
  r["take_max_white"] = count(g[-1], MAX_BOX, "white")
  # a lapse (a phantom): the arm drops with nothing coming - the card fades with the armed words, the number solid
  g = run(prm, LEAD_IN + [(ARMED, 10, f"armed_lapse_{tag}_1"), (ARMED_GONE, 80, f"armed_lapse_{tag}_2")] +
          [(LAPSED, 1, f"armed_lapse_{tag}_{i + 3:02d}") for i in range(10)] + [(LAPSED, 30, f"armed_lapse_{tag}_99")])
  r["lapse"] = [dict(approach=c["approach"], text=c["approach_args"][2] if c["approach"] else None,
                     digits=c["approach_digits"] if c["approach"] else None, alpha=c["approach_alpha"] if c["approach"] else None)
                for c in phase_calls[4:]]
  # the other key: the arm drops while 'now' is still open - the 'now' card again
  run(prm, LEAD_IN + [(ARMED, 10, f"armed_other_{tag}_1"), (NOW_M4, 3, f"armed_other_{tag}_2")])
  r["other_key"] = dict(phase_calls[-1])
  armed_r[tag] = r

# the 'press' words: only with the planner's key for THAT limit; push9's guess (Speed Limit Assist mode, engaged) alone
# no longer says it - the prompt up, or pending - and the words are planner's target
ui_state.speed_limit_mode = 3
words = {}
for name, kw in (("offered", dict(NOW_M4, now_key=(-1, 100, 100))), ("offered_plus", dict(NOW_M4, now_key=(1, 100, 105))),
                 ("other_limit", dict(NOW_M4, now_key=(-1, 90, 90))), ("none", dict(NOW_M4)),
                 ("pre_active", dict(NOW_M4, assist="preActive")), ("pending", dict(NOW_M4, assist="pending")),
                 ("not_engaged", dict(NOW_M4, now_key=(-1, 100, 100), enabled=False))):
  img = run(ON, [(M4, 100, f"words_{name}_0"), (kw, 40, f"words_{name}")])[-1]
  c = phase_calls[-1]
  words[name] = dict(text=c["approach_args"][2] if c["approach"] else None, green=count(img, CARD_BOX, "green"))
ui_state.speed_limit_mode = 1
armed_r["words"] = words
out["armed"] = armed_r

print("RESULT " + json.dumps(out), flush=True)
'''


class TestHudRender(OpenpilotTestCase):
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

  def test_every_setting_off_is_the_stock_screen_pixel_for_pixel(self):
    n = 0
    for name, r in self.r.items():
      if isinstance(r, dict) and "off_vs_stock" in r:
        n += 1
        assert r["off_vs_stock"] == 0, f"{name}: {r['off_vs_stock']} pixels differ from the stock drawing"
    assert n >= 35, n

  def test_an_alerts_own_setting_off_draws_it_as_stock(self):
    assert self.r["stopped_only_its_setting_off_vs_stock"] == 0
    for name in ("changed", "disengage", "turn", "confirm_compact"):
      assert self.r[f"{name}_full_vs_stock"] == 0, f"{name}: its compact setting off - the stock full-screen alert"

  def test_the_full_screen_confirm_is_sunnypilots_own(self):
    # round 5 removed the old drawing of the limit in the arrow's box: exactly stock, offset or not
    for name in ("confirm", "confirm_offset"):
      assert self.r[name]["on_vs_stock"] == 0, f"{name}: {self.r[name]['on_diff_box']}"

  # ----------------------------------------------------------------------------------------- option A: top right
  def test_moving_the_top_right_is_empty(self):
    for name in ("cruise", "cruise_slow", "missing_plan", "missing_carstate", "approach_far", "approach_tunnel",
                 "approach_car"):
      assert self.r[name]["on_vs_stock"] == 0, f"{name}: no speed, no sign, no next limit - the stock screen {self.r[name]}"

  def test_stopped_the_timer_in_the_corner(self):
    r = self.r["stopped_no_alert"]
    assert r["timer_white"] > 150 and r["stock_timer_white"] == 0, r
    x0, y0, x1, y1 = r["on_diff_box"]
    assert x0 >= 300 and x1 <= 466 and y0 >= 8 and y1 <= 64, f"the corner, nothing else: {r['on_diff_box']}"
    assert r["timer_red"] == 0 and r["topleft_white"] == 0, "no sign beside it, nothing top left"

  # ----------------------------------------------------------------------------------------- the coming limit
  def test_the_coming_limit_in_the_max_place(self):
    r = self.r["approach_lower"]
    c = r["calls"]
    assert c["approach"] == 1 and c["approach_args"][:3] == [100, True, "in 300 m"], c
    assert 0.5 < c["approach_args"][3] < 0.7, f"300 m (run down by a second of travel) of a 458 m window: {c}"
    assert c["top_icons"], "a top icon, as the stock MAX number: the face makes way"
    assert c["style"] == "split", "the default style: the split card"
    x0, y0, x1, y1 = r["on_diff_box"]
    assert x0 >= 0 and y0 >= 0 and x1 < 300 and y1 < 160, f"top left only, above the road: {r['on_diff_box']}"
    ax0, ay0, ax1, ay1 = c["approach_rect"]
    assert ax0 >= 8 and ay0 >= 6 and ax1 < 300 and ay1 < 130, c
    assert r["topleft_white"] > 300 and r["timer_white"] == 0, r

  def test_a_higher_limit_too_and_near_is_now(self):
    h = self.r["approach_higher"]["calls"]
    assert h["approach"] == 1 and h["approach_args"][:3] == [70, False, "in 80 m"], h
    n = self.r["approach_near"]["calls"]
    assert n["approach"] == 1 and n["approach_args"][:3] == [100, True, "now"], n

  def test_never_where_it_must_not_be(self):
    for name in ("approach_far", "approach_tunnel", "approach_car", "approach_standstill"):
      assert self.r[name]["calls"]["approach"] == 0, name
      assert not self.r[name]["calls"]["top_icons"], name
    assert self.r["approach_setting_off_vs_stock"] == 0
    m = self.r["approach_while_max"]
    assert m["calls"]["approach"] == 0 and m["vs_stock"] == 0 and m["max_white"] > 2000, f"MAX has the place: {m}"

  def test_any_alert_has_the_place(self):
    for name in ("disengage_approach", "critical_approach", "confirm_compact_approach"):
      assert self.r[name]["calls"]["approach"] == 0, name
    assert self.r["critical_approach"]["on_vs_stock"] == 0
    assert self.r["confirm_compact_approach"]["calls"]["box"] == 1

  def test_the_m4_announce_hold_and_hand_over(self):
    s = self.r["seq"]["m4"]
    announce, nearer, at, held, first, confirm = s["calls"]
    assert announce["approach"] == 1 and announce["approach_args"][:3] == [100, True, "in 400 m"], announce
    assert nearer["approach_args"][2] == "in 140 m" and nearer["approach_args"][3] < announce["approach_args"][3], \
      f"the pill drains: {nearer}"
    assert at["approach_args"][:3] == [100, True, "now"], f"11 m: sunnypilot's 'Near' {at}"
    assert held["approach"] == 1 and held["approach_args"][:3] == [100, True, "now"] and held["top_icons"], \
      f"the look-ahead gone at the change, the limit not yet in force: held - no gap with nothing on screen {held}"
    assert first["approach"] == 0 and first["box"] == 1, f"the confirm's box in the same place on its first frame {first}"
    assert first["grow"] < 0.1 and first["box_alpha"] > 0.9, f"the coming limit's card turns into it, no fade between {first}"
    assert confirm["approach"] == 0 and confirm["box"] == 1 and confirm["grow"] == 1.0, confirm
    q = self.r["seq"]["quiet"]
    assert q["calls"][4]["top_icons"] and q["calls"][5]["approach"] == 0, f"taken: it fades out, nothing held {q}"
    assert q["end_vs_off"] == 0, f"then the screen as with the setting off {q}"

  def test_a_rising_limit_hands_over_to_the_stock_max(self):
    s = self.r["seq"]["rising"]
    announce, first, after = s["calls"]
    assert announce["approach"] == 1 and announce["approach_args"][:2] == [70, False], announce
    assert first["top_icons"], f"no frame for the face at the hand-over: {s}"
    assert first["approach"] == 1 and first["approach_args"][:2] == [70, False], f"it fades out under MAX, not cut {s}"
    assert after["approach"] == 0 and after["top_icons"] and s["max_white"] > 2000, s

  def test_the_hold_ends(self):
    announce, at, held, gone = self.r["seq"]["hold"]["calls"]
    assert announce["approach"] == 1 and held["approach"] == 1 and held["approach_args"][2] == "now"
    assert gone["approach"] == 0 and not gone["top_icons"], "never held past APPROACH_HOLD_S"

  def test_a_gap_far_from_the_change_is_a_distance_never_now(self):
    announce, gap, back, gone = self.r["seq"]["gap"]["calls"]
    assert announce["approach_args"][:3] == [100, True, "in 400 m"], announce
    assert gap["approach"] == 1 and gap["approach_args"][2].startswith("in ") and gap["top_icons"], \
      f"a message missed 350 m out: the distance runs on {gap}"
    assert gap["approach_args"][3] < announce["approach_args"][3], f"and the pill keeps draining {gap}"
    assert back["approach_args"][:3] == [100, True, "in 300 m"], back
    assert gone["approach"] == 0 and not gone["top_icons"], "lost far out for longer than APPROACH_GAP_S: gone"

  # ----------------------------------------------------------------------------------------- the style
  def test_stock_plus_draws_the_same_states(self):
    st = self.r["styles"]
    a = st["approach_lower"]
    assert a["style"] == "plus" and a["approach"] == 1 and a["approach_args"][:3] == [100, True, "in 300 m"], a
    x0, y0, x1, y1 = a["approach_rect"]
    assert x0 >= 8 and y0 >= 6 and x1 < 300 and y1 < 160, a
    assert st["approach_higher"]["approach_args"][:3] == [70, False, "in 80 m"]
    assert st["approach_near"]["approach_args"][2] == "now"
    for name in ("confirm_compact", "confirm_compact_plus"):
      c = st[name]
      assert c["style"] == "plus" and c["box"] == 1 and c["banner"] == 0 and c["top_icons"], c
      assert c["box_rect"][3] <= 160, c
    assert st["confirm_compact"]["box_text"] == "press - to set 50"

  def test_every_state_neither_card_draws_is_the_same_in_both_styles(self):
    u = self.r["styles"]["untouched"]
    assert len(u) >= 20
    for name, px in u.items():
      assert px == 0, f"{name}: {px} pixels differ between the styles"

  def test_switching_the_style_while_a_card_is_up(self):
    sw = self.r["styles"]["switch"]
    for when, phases in sw.items():
      at = {"coming": 1, "waiting": 4, "done": 5}[when]
      for i, p in enumerate(phases):
        drew = p["approach"] or p["box"] or p["done"]
        if drew:
          assert p["style"] == ("plus" if i >= at else "split"), f"{when}: phase {i} {phases}"
      assert all(p["top_icons"] for p in phases), f"{when}: nothing ever drops out: {phases}"
      assert phases[at]["approach"] or phases[at]["box"] or phases[at]["done"], f"{when}: drawn on the frame it switched"

  def test_not_engaged_the_card_names_no_max(self):
    # review M1: the stock MAX is only drawn while openpilot is engaged; the coming limit needs no engagement, and the
    # set speed this non-PCM car keeps while disengaged is held by nothing - so no 'MAX 110' beside it
    engaged = self.r["approach_lower"]["calls"]
    assert engaged["approach"] == 1 and engaged["approach_current"] == "110", engaged
    for tag, c in self.r["styles"]["not_engaged"].items():
      assert c["approach"] == 1 and c["approach_args"][:3] == [100, True, "in 300 m"], f"{tag}: it still comes {c}"
      assert c["approach_current"] == "–", f"{tag}: the stock disabled char, not the kept set speed: {c}"
      assert c["top_icons"], c

  def test_the_widest_confirm_keeps_clear_of_the_timer(self):
    # review M2: 'press - to set 110' and 'limit 100 +10' - stock-plus's row alone is ~355 px
    for name, w in self.r["styles"]["wide"].items():
      c = w["calls"]
      assert c["box"] == 1 and c["box_text"] == "press - to set 110" and c["box_note"] == "limit 100 +10", f"{name}: {w}"
      x1 = c["box_rect"][2]
      if name.startswith("moving"):
        assert w["timer_left"] is None and c["max_right"] == 476 - 40 and x1 <= c["max_right"] + 0.5, f"{name}: {w}"
      else:
        assert w["timer_left"] is not None and abs(c["max_right"] - (w["timer_left"] - 14)) < 0.1, f"{name}: {w}"
        assert x1 <= c["max_right"] + 0.5, f"{name}: the card stops 14 px short of the timer: {w}"
        assert w["timer_vs_noalert"] == 0 and w["timer_white"] > 1000, f"{name}: the timer untouched: {w}"
    assert self.r["styles"]["wide"]["moving_d1"]["calls"]["box_rect"][2] > 380, "moving, stock-plus's row is not squeezed"
    assert self.r["styles"]["wide"]["stopped_d1"]["calls"]["box_rect"][2] < 320, "stopped, it is"

  def test_turning_into_max_from_a_fading_prompt_does_not_jump(self):
    # review n1: the prompt timed out and its card was fading when the press set the speed: the card turns into MAX
    # from the strength it was drawn at, up to full - never a jump to full on the first frame
    seen = 0
    for k, frames in self.r["fade_done"].items():
      if not frames[1]["done"]:
        assert all(f["box"] for f in frames) and not any(f["done"] for f in frames), \
          f"{k}: faded under half - it fades out, as before {frames}"
        continue
      seen += 1
      last = frames[0]["box_alpha"]
      done = [f["done_alpha"] for f in frames if f["done"]]
      assert last < 0.9 and abs(done[0] - last) < 0.02, f"{k}: from the strength it was drawn at: {frames}"
      assert done == sorted(done) and done[-1] == 1.0, f"{k}: up to full: {frames}"
    assert seen >= 2, self.r["fade_done"]

  # ----------------------------------------------------------------------------------------- compact alerts
  def assert_banner(self, c, bottom):
    x0, y0, x1, y1 = c["banner"]
    assert x0 >= 8 and y0 >= 6 and y1 <= bottom, f"top left: {c}"
    assert x1 < c["cluster_left"] - 10, f"and short of the cluster: {c}"
    bx0, by0, bx1, by1 = c["box"]
    assert by1 <= max(bottom, 66), f"nothing drawn below the banner and the cluster's top row: the road stays clear {c}"

  def assert_box(self, c):
    """The compact confirm as the card in the MAX number's place - and no banner."""
    assert c["calls"]["box"] == 1 and c["calls"]["banner"] == 0, f"the card, not the banner: {c}"
    assert c["calls"]["top_icons"], f"a top icon, as the stock MAX number is: {c}"
    x0, y0, x1, y1 = c["calls"]["box_rect"]
    assert x0 >= 8 and y0 >= 6 and y1 <= 160, f"top left, clear of the wheel (y 176): {c}"
    bx0, by0, bx1, by1 = c["box"]
    assert by1 <= 160, f"nothing drawn below the card: the road stays clear {c}"
    assert c["cluster_px"] == 0, f"nothing top right: {c}"

  def test_the_compact_confirm_puts_its_target_in_the_max_place(self):
    minus, plus = self.r["compact"]["confirm_compact"], self.r["compact"]["confirm_compact_plus"]
    for c in (minus, plus):
      self.assert_box(c)
      assert c["banner_green"] > 100 and c["banner_white"] > 3000, f"the big digits, 'press - to set 50', the key: {c}"
    assert minus["calls"]["box_text"] == "press - to set 50" and plus["calls"]["box_text"] == "press + to set 50", \
      "the key the alert names and the speed the press gives (owner, 2026-10-12)"
    assert plus["banner_green"] > minus["banner_green"] + 50, "'+' has the key's upright too"
    assert self.r["confirm_compact"]["strip_vs_stock"] == 0

  def test_an_offset_is_named_in_the_pill(self):
    # deletion trap 3: with no sign anywhere, 'limit 50 +5' always shows beside a target that is not the limit
    minus, off = self.r["compact"]["confirm_compact"], self.r["compact"]["confirm_compact_offset"]
    self.assert_box(off)
    assert off["calls"]["box_note"] == "limit 50 +5" and off["calls"]["box_text"] == "press - to set 55", off
    assert off["calls"]["box_rect"][2] > minus["calls"]["box_rect"][2] + 10, f"the card is wider by the note: {off}"
    assert off["calls"]["box_rect"][2] < 300, "and stays in the top left"
    plus_minus, plus_off = self.r["styles"]["confirm_compact"], self.r["styles"]["confirm_compact_offset"]
    assert plus_off["box_note"] == "limit 50 +5" and plus_off["box_rect"][2] > plus_minus["box_rect"][2] + 80, plus_off
    assert plus_off["box_rect"][2] < 476 - 20, "stock-plus: the row is longer by the note, and stays on the road"

  def test_the_target_is_the_stock_max_number(self):
    # stock-plus: the confirm's number IS the stock MAX number (the split card's lands there: test_the_card_turns_into_max)
    d = self.r["digits"]
    assert d["calls"]["box"] == 1 and d["calls"]["style"] == "plus"
    assert d["diff"] == 0, f"pixel for pixel the stock MAX digits inside their ink box: {d}"
    assert d["box_h"] == d["max_h"] and 78 <= d["box_h"] <= 82, f"~81 px, the stock size: {d}"

  def assert_two_flashes(self, a, steady=0.0):
    assert all(a["box"]), a
    lit = a["lit"]
    starts = [i for i in range(len(lit)) if lit[i] and (i == 0 or not lit[i - 1])]
    assert len(starts) == 2, f"two flashes: {lit}"
    for s in starts:
      run = next((k for k in range(s, len(lit)) if not lit[k]), len(lit)) - s
      assert 3 <= run <= 4, f"0.18 s each, at 20 fps: {lit}"
    assert starts[1] - starts[0] >= 7, f"under 3 Hz: {lit}"
    end = max(i for i, x in enumerate(lit) if x)
    assert not any(lit[end + 1:]) and end < 20, lit
    assert max(a["outline_lit"][s + k] for s in starts for k in range(3)) > max(a["outline_lit"][end + 3:]) + 100, \
      f"a flash lights the outline, its halo round it: {a['outline_lit']}"
    assert all(d == 0 for d in a["digits_vs_still"]), f"the digits never blink: {a['digits_vs_still']}"
    # the march moves every frame; the breath stands still for a frame or two at its crest (steady: that share)
    moving = [d > 0 for d in a["outline_vs_still"][end + 3:]]
    assert sum(moving) >= (1.0 - steady) * len(moving), f"the outline moves, frame after frame: {a['outline_vs_still']}"
    moving = [d > 0 for d in a["frame_to_frame"][end + 3:]]
    assert sum(moving) >= (1.0 - steady) * len(moving), f"it moves while the prompt waits: {a['frame_to_frame']}"
    return end

  def test_the_outline_flashes_twice_then_marches(self):
    a = self.r["anim"]
    assert a["style"] == "split" and a["text"] == "press + to set 50", a
    end = self.assert_two_flashes(a)
    march = [k for k, p in enumerate(a["phase"]) if p]
    assert march and march[0] <= end + 6 and march == list(range(march[0], len(a["lit"]))), f"then the dashes march: {a}"
    phases = [a["phase"][k] for k in march]
    assert all(q > p for p, q in zip(phases, phases[1:], strict=False)), f"clockwise, on and on: {a['phase']}"

  def test_the_stock_plus_outline_flashes_twice_then_breathes(self):
    a = self.r["anim_d1"]
    assert a["style"] == "plus" and a["text"] == "press + to set 50", a
    end = self.assert_two_flashes(a, steady=0.25)
    b = a["breath"][end + 1:]
    assert b[0] > 0.9 and min(b) < 0.1 and max(b[10:]) > 0.9, f"then breathes, full to faint and back, 1 Hz: {b}"

  def test_a_new_prompt_or_target_flashes_again(self):
    for name in ("again", "retarget"):
      r = self.r["rearm"][name]
      assert all(r["box"]), f"{name}: the box never leaves the screen {r}"
      lit = r["lit"][2:] if name == "again" else r["lit"][1:]
      starts = [i for i in range(len(lit)) if lit[i] and (i == 0 or not lit[i - 1])]
      assert len(starts) == 2 and starts[0] <= 2, f"{name}: its own two flashes, at once {r['lit']}"
      assert not r["lit"][0], f"{name}: the first prompt was marching by then {r['lit']}"

  def test_with_no_limit_the_confirm_is_todays_banner(self):
    c = self.r["compact"]["confirm_compact_no_limit"]
    assert c["calls"]["banner"] == 1 and c["calls"]["box"] == 0 and not c["calls"]["top_icons"], c
    assert c["calls"]["banner_text"] == "press + to confirm", "no limit: no speed to name"
    assert self.r["styles"]["confirm_compact_no_limit"]["banner"] == 1, "whatever the style"
    self.assert_banner(c, 6 + 40)
    assert c["banner_green"] > 100 and c["banner_red"] == 0, f"'press + to confirm' and the key, no sign: {c}"

  def test_the_confirm_key_says_what_the_text_says(self):
    plus, minus = self.r["compact"]["confirm_compact_plus"], self.r["compact"]["confirm_compact"]
    c = self.r["compact"]["confirm_compact_text_vs_arrow"]
    self.assert_box(c)
    assert c["banner_green"] > minus["banner_green"] + 50 and abs(c["banner_green"] - plus["banner_green"]) < 30, \
      f"'press + to confirm' with the '+' key although the arrow points down: {c}"

  def test_after_the_confirm_the_solid_stock_max_number_takes_its_place(self):
    s = self.r["seq"]["changed"]
    confirm, first, after = s["calls"]
    assert confirm["box"] == 1 and first["box"] == 0 and first["done"] == 1 and first["top_icons"], \
      f"the press set the speed: the card turns into MAX, not cut: {s}"
    assert after["box"] == 0 and after["done"] == 0 and after["top_icons"], s
    assert s["end_vs_ref"] == 0 and s["max_white"] > 2000, f"then exactly the stock 'set speed changed' frame: {s}"

  def test_the_card_turns_into_max(self):
    for tag in ("d3", "d1"):
      m = self.r["styles"][f"m4_{tag}"]
      press = m["press"]
      done = [p["done_args"][2] for p in press if p["done"]]
      assert press[0]["done"] == 1 and len(done) in (6, 7), f"{tag}: CARD_DONE_S, 0.3 s at 20 fps: {press}"
      assert done == sorted(done) and done[0] < 0.1 and done[-1] == 1.0, f"{tag}: on to the end: {done}"
      assert all(p["top_icons"] for p in press), f"{tag}: the face never shows in between: {press}"
      assert all(p["done_args"][1] == "110" for p in press if p["done"]), f"{tag}: beside it, the MAX it replaces"
      assert not press[-1]["done"] and not press[-1]["box"], press
      assert m["end_vs_ref"] == 0 and m["max_white"] > 2000, f"{tag}: then the stock MAX number, as with no card: {m}"
      # (within one level: the card hands the stock MAX over at full; the drive's own fade is a level short of full)

  def test_a_critical_alert_during_the_confirm_takes_the_screen_at_once(self):
    for name in ("critical", "critical_lit"):
      s = self.r["seq"][name]
      confirm, first, after = s["calls"]
      assert confirm["box"] == 1 and first["box"] == 0 and after["box"] == 0, s
      assert first["done"] == 0 and after["done"] == 0, s
      assert not first["top_icons"], f"not a top icon from its first frame: {s}"
      assert s["first_vs_stock_top_left"] == 0, f"its first frame top left is the stock one: no box over the alert {s}"
      assert s["end_vs_stock"] == 0, f"then exactly the stock full-screen alert: {s}"
    assert self.r["seq"]["critical_lit"]["calls"][0]["lit"], "the box was mid-flash the frame before"

  def test_whatever_ends_the_confirm_the_screen_is_todays_from_that_frame(self):
    for name, e in self.r["seq"]["ends"].items():
      confirm = e["calls"][0]
      assert confirm["box"] == 1 and confirm["top_icons"], f"{name}: {e}"
      assert e["ref_calls"][0]["box"] == 0 and e["ref_calls"][0]["banner"] == 1, f"{name}: the reference is the banner {e}"
      first = e["calls"][1]
      assert e["px_right"][0] == 0, f"{name}: the right of the screen on the first frame is the unchanged build's {e}"
      if name in ("no_alert", "back_later"):
        # the confirm fades out top left (the box where the banner was), then the screen is the same, face and all
        assert e["px"][2] == 0, f"{name}: {e}"
      else:
        assert first["box"] == 0, f"{name}: the box goes at once {e}"
        assert first["top_icons"] == e["ref_calls"][1]["top_icons"], f"{name}: the face as the unchanged build's {e}"
        assert e["px"] == [0, 0, 0], f"{name}: the whole frame, from the first on {e}"
    assert self.r["seq"]["ends"]["no_limit"]["calls"][1]["banner"] == 1, "the limit lost: today's banner"

  def test_a_confirm_in_the_grace_after_the_prompt_is_the_stock_solid_max(self):
    for name, g in self.r["seq"]["grace"].items():
      confirm, timeout, first, after = g["calls"]
      assert confirm["box"] == 1 and confirm["top_icons"], f"{name}: {g}"
      # the prompt timed out: the face is back at once; the box only fades where the banner faded
      assert not timeout["top_icons"], f"{name}: {g}"
      if name == "r121_changed":
        # past the prompt's fade: the box is gone, and the confirm is the drive's screen with no prompt from its first frame
        assert timeout["box"] == 0 and first["box"] == 0 and first["done"] == 0, f"{name}: {g}"
        assert g["px"] == [0, 0, 0], f"{name}: {g}"
      else:
        # inside the fade: the card, still up, turns into MAX - or, faded under half, fades out under it
        assert timeout["box"] == 1 and first["box"] + first["done"] == 1 and first["top_icons"], f"{name}: {g}"
      assert after["box"] == 0 and after["done"] == 0 and after["top_icons"], f"{name}: {g}"
      assert g["px"][2] == 0 and g["max_white"] > 2000, f"{name}: then exactly the stock solid MAX number {g}"

  def test_the_confirms_first_frame_is_a_top_icon(self):
    before, first = self.r["seq"]["start"]["calls"]
    assert not before["top_icons"] and before["box"] == 0, before
    assert first["box"] == 1 and first["top_icons"], f"the face makes way from the first frame: {first}"

  def test_a_confirm_with_no_words_draws_no_banner(self):
    c = self.r["compact"]["confirm_compact_empty"]
    assert c["banner"] is None and c["banner_white"] == 0 and c["banner_green"] == 0, f"no empty pill top left: {c}"
    assert c["calls"]["box"] == 0 and c["calls"]["banner"] == 0, c

  def test_set_speed_changed_draws_nothing_and_frees_the_max_number(self):
    assert self.r["compact"]["changed"]["px"] == 0, "pixel for pixel the screen without the alert"
    m = self.r["changed_max"]
    assert m["noalert_vs_on"] == 0
    assert m["on"]["max_white"] > 2000 and m["on"]["mid_white"] == 0, f"the MAX number shows the new set speed: {m}"
    assert m["full"]["mid_white"] > 500, f"full screen: the alert's words {m}"

  def test_disengage_and_turn_notices_are_banners(self):
    for name, bottom in (("disengage", 6 + 66), ("turn", 6 + 40)):
      c = self.r["compact"][name]
      self.assert_banner(c, bottom)
      assert c["banner_white"] > 500, name
      assert c["cluster_px"] == 0, f"{name}: nothing top right {c}"

  def test_a_critical_alert_is_still_the_stock_full_screen(self):
    r = self.r["critical"]
    assert r["on_vs_stock"] == 0, "drawn exactly as stock"
    assert r["stock_banner_white"] > 300, "the full red alert and its text"

  # ----------------------------------------------------------------------------------------- the stop
  def test_stopped_banner_and_timer(self):
    r = self.r["stopped"]
    assert r["banner_orange"] > 4000, "the compact banner"
    assert r["lower_orange"] == 0 < r["stock_lower_orange"], "the road below it is not covered"
    assert r["timer_white"] > 150, "the stop time"
    assert r["cluster_left"] - r["banner_right"] >= 30, (r["banner_right"], r["cluster_left"])
    assert self.r["timer_size"]["0:30"] == self.r["timer_size"]["59:59"] == 50, "an m:ss time is drawn at full size"
    c = self.r["compact"]["stopped"]
    self.assert_banner(c, 6 + 66)

  def test_an_hour_long_stop_keeps_clear_of_the_banner(self):
    r = self.r["stopped_1h"]
    assert r["banner_orange"] > 4000 and r["timer_white"] > 100
    assert r["cluster_left"] - r["banner_right"] >= 10, ("1:02:05 shrinks to the width of 59:59",
                                                         r["banner_right"], r["cluster_left"])
    assert self.r["timer_size"]["1:02:05"] < 50

  def test_the_full_prompt_returns_when_the_car_ahead_moves_off(self):
    r = self.r["stopped_lead_departs"]
    assert r["on_vs_stock"] == 0, "the stock full-screen prompt, and no cluster over it"

  def test_two_stops_in_a_drive_both_start_compact(self):
    stop1, departs, driving, stop2 = self.r["two_stops"]
    for s in (stop1, stop2):
      assert s["banner_orange"] > 4000 and s["lower_orange"] == 0 and s["timer_white"] > 100, s
    assert departs["lower_orange"] > 4000, "the full prompt when the car ahead moved off"
    assert driving["banner_orange"] == 0 and driving["lower_orange"] == 0 and driving["timer_white"] == 0, driving

  def test_the_timer_returns_only_after_an_alert_has_faded_out(self):
    r = self.r["alert_fade"]
    up, gone3, gone60 = r["on_vs_stock"]
    assert up == 0 and gone3 == 0, f"hidden under the alert and through its fade-out: {r}"
    assert gone60 > 0 and r["timer_white"][2] > 150, f"back once it has gone: {r}"

  def test_the_gateway_icon_falls_back_instead_of_crashing(self):
    g = self.r["gateway_icon"]
    assert g["b"][0] != 0 and g["b"][1] > 0, "icon B loads"
    assert g["fallback_id"] == g["software_id"] != 0, "a pointer in its place: the old icon, and the UI keeps going"

  # ----------------------------------------------------------------------------------------- the right rail
  def test_the_rail_draws_only_in_the_strip_and_only_with_an_item(self):
    for name, r in self.r.items():
      if not isinstance(r, dict) or not isinstance(r.get("strip_vs_stock"), int):
        continue  # not one of the single states
      if name in ("rail_stop", "rail_stop_grey", "rail_curve", "rail_both"):
        assert r["strip_vs_stock"] > 0, name
      else:
        assert r["strip_vs_stock"] == 0, f"{name}: nothing for the rail, the stock strip - {r['strip_vs_stock']} px differ"
    for name in ("rail_stop", "rail_curve"):
      x0, y0, x1, y1 = self.r[name]["on_diff_box"]
      assert x1 >= STRIP_X, f"{name}: the rail is in the strip"

  def test_a_planned_stop_openpilot_is_driving_is_white(self):
    r = self.r["rail_stop"]
    assert r["rail_white"] > 250 and r["rail_white"] > 4 * r["rail_grey"], f"white (grey only at the text's edges) {r}"
    g = self.r["rail_stop_grey"]
    assert g["rail_grey"] > 250 and g["rail_white"] == 0, f"the model's plan only: grey, nothing white {g}"

  def test_a_curve_and_its_target_speed(self):
    r = self.r["rail_curve"]
    assert r["rail_white"] > 200, r

  def test_the_ball_moves_down_only_while_an_item_shows(self):
    for name in ("rail_stop", "rail_stop_grey", "rail_curve"):
      r = self.r[name]
      assert r["stock_ballhi_green"] > 100 and r["stock_balllo_green"] == 0, f"{name}: the stock ball at the top"
      assert r["ballhi_green"] == 0 and r["balllo_green"] > 100, f"{name}: held under the item {r}"
    for name in ("rail_stop_lead", "rail_stop_standstill"):
      r = self.r[name]
      assert r["ballhi_green"] == r["stock_ballhi_green"] > 100, f"{name}: no item, the ball where it was"

  def test_no_stop_behind_a_car_or_at_a_standstill(self):
    for name in ("rail_stop_lead", "rail_stop_standstill"):
      assert self.r[name]["rail_white"] == 0 and self.r[name]["rail_grey"] == 0, name

  def test_a_planned_stop_wins_over_a_curve(self):
    assert self.r["rail_both_vs_stop"] == 0

  def test_the_rail_settings(self):
    t = self.r["rail_toggles"]
    for key in ("rail_stop_stop_off", "rail_curve_curve_off", "rail_stop_both_off", "rail_curve_both_off",
                "rail_both_both_off"):
      assert t[key]["strip_vs_stock"] == 0, f"{key}: the stock strip, pixel for pixel"
    assert t["rail_both_stop_off"]["strip_vs_curve"] == 0, "the stop off: the curve takes its place"

  def test_the_rail_through_a_drive(self):
    s = self.r["rail_seq"]
    cruise, debounce, fade_in, stop, held, fade_out, gone = s["strip_vs_stock"]
    assert cruise == 0 and debounce == 0, f"nothing while cruising, nor in the first 0.2 s of a stop: {s}"
    assert fade_in > 0 and s["rail_white"][2] == 0 and s["rail_grey"][2] > 50, \
      f"its first frame: the white item dimmed toward the black strip, not drawn at once {s}"
    assert s["ballhi_green"][2] == s["stock_ballhi_green"][2] > 100, f"and the ball not dropped in one frame {s}"
    assert stop > 0 and s["rail_white"][3] > 250 and s["ballhi_green"][3] == 0 and s["balllo_green"][3] > 100, s
    assert held > 0 and s["rail_white"][4] > 250, f"kept a moment after the plan stops stopping: {s}"
    assert fade_out > 0 and s["rail_white"][5] == 0 and s["rail_grey"][5] > 20, f"then fades out: {s}"
    assert gone == 0, f"then gone, and the ball back exactly where the stock one is: {s}"

  # ----------------------------------------------------------------------------------------- sla-flash: the armed card
  def test_the_armed_number_flashes_and_nothing_else(self):
    for tag in ("d3", "d1"):
      r = self.r["armed"][tag]
      assert r["style"] == ("split" if tag == "d3" else "plus"), r["style"]
      assert all(r["armed"]) and set(r["value"]) == {100}, r
      assert r["digits"] == [1.0] * 8 + [0.25] * 4 + [1.0] * 2, f"{tag}: lit 0.4 s, dim 0.2 s, from the first frame {r['digits']}"
      assert set(r["text"]) == {"sets 100 at sign"}, r["text"]
      assert r["lit_vs_dim"] > 0 and r["lit_vs_dim_outside"] == 0, \
        f"{tag}: the frames differ, and only inside the number's ink box {r['box']}: {r}"
      assert r["lit_vs_lit"] == 0 and r["dim_vs_dim"] == 0, f"{tag}: the rest of the card is solid {r}"
      assert r["lit_digit_white"] > 200 and r["dim_digit_white"] == 0, f"{tag}: dim, not gone (25 %): {r}"
      assert all(r["top_icons"]), "a top icon: the face makes way"

  def test_the_armed_words_have_no_key(self):
    for tag in ("d3", "d1"):
      r = self.r["armed"][tag]
      assert r["now_text"] == "now", r["now_text"]
      assert all(g == 0 for g in r["green"]), f"{tag}: 'sets 100 at sign' - no green key {r['green']}"

  def test_the_armed_card_outlives_the_look_ahead(self):
    for tag in ("d3", "d1"):
      g = self.r["armed"][tag]["gone"]
      assert not g["approach_state"], f"{tag}: the look-ahead's hold has ended: {g}"
      assert g["approach"] == 1 and g["approach_args"][:3] == [100, True, "sets 100 at sign"] and g["approach_armed"], g

  def test_the_take_hands_over_to_the_stock_max(self):
    for tag in ("d3", "d1"):
      r = self.r["armed"][tag]
      take = r["take"]
      drawn = [t for t in take if t["approach"]]
      assert drawn and all(t["text"] == "sets 100 at sign" and t["digits"] == 1.0 for t in drawn), \
        f"{tag}: it fades with the armed words, the number solid: {take}"
      alphas = [t["alpha"] for t in drawn]
      assert alphas == sorted(alphas, reverse=True) and take[-1]["approach"] == 0, f"{tag}: a fade, then gone {take}"
      assert all(t["top_icons"] for t in take), f"{tag}: no frame for the face at the hand-over {take}"
      assert r["take_max_white"] > 2000, f"{tag}: the stock MAX number, the new set speed: {r['take_max_white']}"

  def test_a_lapse_fades_with_the_armed_words(self):
    for tag in ("d3", "d1"):
      lapse = self.r["armed"][tag]["lapse"]
      drawn = [t for t in lapse if t["approach"]]
      assert drawn and all(t["text"] == "sets 100 at sign" and t["digits"] == 1.0 for t in drawn), lapse
      assert [t["alpha"] for t in drawn] == sorted([t["alpha"] for t in drawn], reverse=True), lapse
      assert lapse[-1]["approach"] == 0 and len(drawn) == len(lapse) - 1, f"never back to 'now' for its tail: {lapse}"

  def test_the_other_key_is_the_now_card_again(self):
    for tag in ("d3", "d1"):
      o = self.r["armed"][tag]["other_key"]
      assert o["approach"] == 1 and o["approach_args"][2] == "now" and not o["approach_armed"], o

  def test_the_press_words_are_the_planners(self):
    w = self.r["armed"]["words"]
    assert w["offered"]["text"] == "press - to set 100" and w["offered"]["green"] > 0, w
    assert w["offered_plus"]["text"] == "press + to set 105", "the planner's target, offset and all"
    for name in ("other_limit", "none", "pre_active", "pending", "not_engaged"):
      assert w[name]["text"] == "now" and w[name]["green"] == 0, f"{name}: no key the planner would not take {w}"
