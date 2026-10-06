"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 onroad view, drawn for real in a headless raylib window, per HUD state:

  * with the settings on, each piece is where it should be (colors sampled in its box);
  * with every setting off, the frame is PIXEL-IDENTICAL to the stock drawing, i.e. the same view with the FORK(HUD)
    call sites neutralised (cluster and rail renders no-ops - so the ball's floor stays unset - and both alert hooks
    answering "not mine");
  * with only an alert's own setting off, that alert is drawn exactly as stock;
  * the right rail: its item only in the ball's strip, white or grey as openpilot drives the plan or not, the ball held
    under it and back where the stock one is once it has gone, and with its two settings off the stock strip;
  * compact alerts: 'set speed changed' pixel-identical to no alert (and the MAX number free to show), the disengage
    and turn banners top left and short of the cluster, nothing below them, a critical alert exactly stock, each group's
    setting off exactly stock;
  * the compact confirm (design A): its target in the stock MAX number's place, the same digits pixel for pixel, dashed,
    the pill under it and no banner, a top icon (the live speed makes way); the limit named in the pill with an offset
    and no sign; today's banner with no limit; the solid stock MAX number after the confirm; a critical alert on its
    first frame exactly stock; the offset badge on a school-zone sign clear of its lamps and SCHOOL;
  * Current Speed off: only the live speed goes, in every layout; the stop time stays;
  * the sign setting: zones outside a zone = off, inside one = always; off puts the speed, the stop time and the next
    limit in the corner; entering a zone the speed slides and the sign fades in.

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
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS

KPH = 3.6
W, H = 536, 240
OUT = os.environ.get("HUD_RENDER_OUT")
clock = {"t": 100.0}
rl.get_time = lambda: clock["t"]

ON = {HS.PARAM_SPEED_CLUSTER: True, HS.PARAM_NEXT_LIMIT: 3, HS.PARAM_SCHOOL_CUE: True, HS.PARAM_VARIABLE_SIGN: True,
      HS.PARAM_STOPPED_TIMER: True, HS.PARAM_STOPPED_BANNER: True, HS.PARAM_CONFIRM_LIMIT: True,
      HS.PARAM_PLANNED_STOP: True, HS.PARAM_CURVE: True, HS.PARAM_COMPACT_LIMIT: True, HS.PARAM_COMPACT_DISENGAGE: True,
      HS.PARAM_COMPACT_TURN: True, HS.PARAM_LIMIT_SIGN: HS.SIGN_ALWAYS, HS.PARAM_CURRENT_SPEED: True}
assert set(ON) == set(HS.ALL_PARAMS)
OFF = {k: (0 if k in HS.INT_PARAMS else False) for k in ON}
RAIL_OFF = {**ON, HS.PARAM_PLANNED_STOP: False, HS.PARAM_CURVE: False}
FULL_LIMIT = {**ON, HS.PARAM_COMPACT_LIMIT: False}   # the speed-limit prompts full screen, as before the compact ones
SIGN_OFF = {**ON, HS.PARAM_LIMIT_SIGN: HS.SIGN_OFF}
SIGN_ZONES = {**ON, HS.PARAM_LIMIT_SIGN: HS.SIGN_ZONES}
SPEED_OFF = {HS.PARAM_CURRENT_SPEED: False}

# what the confirm drew on a frame: the banner (hud_draw.confirm_banner) or the target in the MAX number's place
# (hud_draw.pending_max), counted through the module attributes both callers use
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw
calls = {"banner": 0, "box": 0, "box_rect": None}
_banner, _box = hud_draw.confirm_banner, hud_draw.pending_max


def counted_banner(*a, **k):
  calls["banner"] += 1
  return _banner(*a, **k)


def counted_box(*a, **k):
  calls["box"] += 1
  calls["box_rect"] = [round(v, 1) for v in _box(*a, **k)]
  return calls["box_rect"]


hud_draw.confirm_banner, hud_draw.pending_max = counted_banner, counted_box
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


def put(svc, fill):
  m = messaging.new_message(svc)
  fill(getattr(m, svc))
  sm.data[svc] = getattr(m, svc)
  sm.seen[svc] = sm.alive[svc] = sm.valid[svc] = sm.updated[svc] = True
  sm.recv_frame[svc] = 5


def scene(v=110 / KPH, limit=110, ahead=100, ahead_dist=339.0, ahead_valid=True, school=0, variable=False,
          standstill=False, alert=None, assist="active", set_kph=110, lead=None, lp=True, cs=True, offset=0, rail=None):
  """rail = dict(stop_m, solid, curve_kph, confident): the right rail's messages (modelV2's plan, carControl,
  longitudinalPlan, smart cruise control) - none of them otherwise."""
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
    s.enabled = True
    s.state = "enabled"
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
    r.source = "map"
    p.speedLimit.assist.state = assist
    if rail.get("curve_kph"):
      vis = p.smartCruiseControl.vision
      vis.state, vis.active, vis.vTarget = "entering", True, rail["curve_kph"] / KPH
      p.longitudinalPlanSource = "sccVision"
  if lp:
    put("longitudinalPlanSP", plan)

  def lmd(m):
    m.speedLimitValid = True
    m.speedLimit = limit / KPH
    m.speedLimitAheadValid = ahead_valid
    m.speedLimitAhead = ahead / KPH
    m.speedLimitAheadDistance = ahead_dist
    z = m.nswZone
    z.mode, z.state, z.speedLimit, z.schoolZone, z.variable = 2, 2, limit / KPH, school, variable
  put("liveMapDataSP", lmd)

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


def render(name, params, stock=False, frames=100, t0=100.0, jump_at=None, jump=0.0):
  """A fresh view, `frames` frames at 20 fps on the sim clock; the last frame is returned. 100 frames = 5 s: past the
  2.5 s the stock MAX number shows after engaging (and its fade), which hides the speed digits."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()  # a fresh view is a fresh UI start
  view = AugmentedRoadView()
  view.set_rect(rect)
  saved = (hud_alerts.draw_compact, hud_alerts.draw_pending_limit, hud_alerts.frees_top_icons)
  if stock:
    hud_alerts.draw_compact = lambda ar, alert: False
    hud_alerts.draw_pending_limit = lambda ar, layout: False
    hud_alerts.frees_top_icons = lambda alert: False
    view._hud_cluster.render = lambda *a, **k: None
    view._hud_rail.render = lambda *a, **k: None
  try:
    for i in range(frames):
      clock["t"] = t0 + i * 0.05 + (jump if jump_at is not None and i >= jump_at else 0.0)
      calls.update(banner=0, box=0, box_rect=None)
      rl.begin_drawing()
      rl.begin_texture_mode(rt)
      rl.clear_background(rl.BLACK)
      view.render(rect)
      rl.end_texture_mode()
      rl.end_drawing()
  finally:
    hud_alerts.draw_compact, hud_alerts.draw_pending_limit, hud_alerts.frees_top_icons = saved
  last.clear()
  last.update(calls, top_icons=bool(view._hud_renderer.drawing_top_icons()))
  arr = grab()
  save(arr, name)
  view.close()
  return arr


def save(arr, name):
  if OUT:
    from PIL import Image
    Image.fromarray(arr.astype(np.uint8)).save(os.path.join(OUT, f"{name}.png"))


phase_calls = []  # run(): each phase's last frame, as last


def run(params, steps, stock=False, t0=100.0):
  """ONE view through several phases, as a drive goes: steps = [(scene kwargs, frames, name)]. Returns the last frame
  of each phase (saved as name when HUD_RENDER_OUT is set)."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()
  view = AugmentedRoadView()
  view.set_rect(rect)
  if stock:
    saved = (hud_alerts.draw_compact, hud_alerts.draw_pending_limit, hud_alerts.frees_top_icons)
    hud_alerts.draw_compact = lambda ar, alert: False
    hud_alerts.draw_pending_limit = lambda ar, layout: False
    hud_alerts.frees_top_icons = lambda alert: False
    view._hud_cluster.render = lambda *a, **k: None
    view._hud_rail.render = lambda *a, **k: None
  grabs, t = [], t0
  phase_calls.clear()
  try:
    for kw, frames, name in steps:
      scene(**kw)
      for _ in range(frames):
        clock["t"] = t
        t += 0.05
        calls.update(banner=0, box=0, box_rect=None)
        rl.begin_drawing()
        rl.begin_texture_mode(rt)
        rl.clear_background(rl.BLACK)
        view.render(rect)
        rl.end_texture_mode()
        rl.end_drawing()
      grabs.append(grab())
      phase_calls.append(dict(calls, top_icons=bool(view._hud_renderer.drawing_top_icons()), speed=view._hud_cluster.frame.speed))
      save(grabs[-1], name.replace("_on", "_stock") if stock else name)
  finally:
    if stock:
      hud_alerts.draw_compact, hud_alerts.draw_pending_limit, hud_alerts.frees_top_icons = saved
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
  elif kind == "amber":
    m = (r > 200) & (g > 110) & (g < 210) & (bl < 70)
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


def diff_box(a, b):
  ys, xs = np.nonzero(np.abs(a - b).max(axis=2) > 0)
  return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())] if len(xs) else None


SIGN = (406, 6, 466, 66)
SIGN_INNER = (418, 18, 454, 54)
SPEED = (300, 16, 396, 56)
NEXT = (250, 58, 397, 97)
LAMP_L = (407, 7, 423, 23)
LAMP_R = (449, 7, 465, 23)
LABEL = (404, 68, 468, 92)
BANNER = (8, 6, 200, 72)
LOWER = (100, 110, 400, 150)
ARROW = (376, 18, 466, 108)
KEY = (374, 54, 400, 72)

out = {}
states = {
  "cruise": dict(),
  "next_higher": dict(ahead=120),
  "next_far": dict(v=20.0, ahead_dist=480.0),
  "next_tunnel": dict(limit=80, variable=True, ahead_valid=False, ahead=0, ahead_dist=0.0),
  "confirm": dict(limit=50, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105, ahead=70, ahead_dist=143.0),
  "confirm_offset": dict(limit=50, offset=5, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105, ahead=70,
                         ahead_dist=143.0),
  "stopped": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0, lead=0.1),
  "stopped_1h": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0, lead=0.1),
  "stopped_lead_departs": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0, lead=2.0),
  "school_active": dict(limit=40, v=33 / KPH, school=2, ahead=0, ahead_dist=0.0),
  "school_inactive": dict(limit=50, v=33 / KPH, school=1, ahead=70, ahead_dist=85.0),
  "variable": dict(limit=80, v=70 / KPH, variable=True, ahead=0, ahead_dist=0.0),
  "missing_plan": dict(lp=False),
  "missing_carstate": dict(cs=False),
  # the right rail; the model fully confident, so the stock ball would sit at the very top of the strip
  "rail_stop": dict(v=30 / KPH, limit=50, ahead=0, ahead_dist=0.0, rail=dict(stop_m=25.0, solid=True, confident=True)),
  "rail_stop_grey": dict(v=30 / KPH, limit=50, ahead=0, ahead_dist=0.0, rail=dict(stop_m=25.0, solid=False, confident=True)),
  "rail_stop_lead": dict(v=30 / KPH, limit=50, ahead=0, ahead_dist=0.0, lead=0.0, rail=dict(stop_m=25.0, confident=True)),
  "rail_stop_standstill": dict(v=0.0, standstill=True, limit=50, ahead=0, ahead_dist=0.0,
                               rail=dict(stop_m=25.0, confident=True)),
  "rail_curve": dict(v=45 / KPH, limit=60, ahead=0, ahead_dist=0.0, rail=dict(curve_kph=37, confident=True)),
  "rail_both": dict(v=45 / KPH, limit=60, ahead=0, ahead_dist=0.0, rail=dict(stop_m=25.0, curve_kph=37, confident=True)),
  # compact alerts
  "confirm_compact": dict(limit=50, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105, ahead=0, ahead_dist=0.0),
  "confirm_compact_plus": dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40, ahead=0,
                               ahead_dist=0.0),
  "confirm_compact_offset": dict(limit=50, offset=5, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105, ahead=0,
                                 ahead_dist=0.0),
  "confirm_compact_sign_off": dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40, ahead=0,
                                   ahead_dist=0.0),
  "confirm_compact_sign_off_no_cl": dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40,
                                         ahead=0, ahead_dist=0.0),
  # the text asks for '+' while sunnypilot's arrow points down (its rounding at .5 ties): the key follows the text
  "confirm_compact_text_vs_arrow": dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=105,
                                        ahead=0, ahead_dist=0.0),
  "confirm_compact_empty": dict(limit=50, v=27 / KPH, alert=CONFIRM_EMPTY, assist="preActive", set_kph=50, ahead=0,
                                ahead_dist=0.0),
  # an offset with no sign in the cluster: the pill names the limit
  "confirm_compact_sign_off_offset": dict(limit=50, offset=5, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40,
                                          ahead=0, ahead_dist=0.0),
  # no limit at all: nothing to put in the MAX number's place - the banner
  "confirm_compact_no_limit": dict(limit=0, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40, ahead=0,
                                   ahead_dist=0.0),
  # the target 80, for the stock MAX number '80' of "changed" to measure it against
  "confirm_compact_80": dict(limit=80, v=27 / KPH, alert=CONFIRM, assist="preActive", set_kph=105, ahead=0, ahead_dist=0.0),
  "changed": dict(limit=80, v=60 / KPH, alert=CHANGED, set_kph=80, ahead=0, ahead_dist=0.0),
  "disengage": dict(alert=DISENGAGE),
  "disengage_next": dict(v=30.3, alert=DISENGAGE),   # the next lower limit's row, which a two-line banner reaches down to
  "turn": dict(limit=50, v=20 / KPH, alert=TURN, ahead=0, ahead_dist=0.0),
  "critical": dict(alert=CRITICAL),
  # the sign setting
  "sign_off": dict(),
  "sign_zones_out": dict(),
  "sign_zones_in": dict(limit=80, v=70 / KPH, variable=True, ahead=0, ahead_dist=0.0),
  "stopped_sign_off": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0, lead=0.1),
}
PARAMS = {"confirm": FULL_LIMIT, "confirm_offset": FULL_LIMIT, "confirm_compact_sign_off": SIGN_OFF,
          "confirm_compact_sign_off_no_cl": {**SIGN_OFF, HS.PARAM_CONFIRM_LIMIT: False}, "sign_off": SIGN_OFF,
          "sign_zones_out": SIGN_ZONES, "sign_zones_in": SIGN_ZONES, "stopped_sign_off": SIGN_OFF,
          "confirm_compact_sign_off_offset": SIGN_OFF}
BADGE = (440, 24, 468, 52)   # the offset badge on the pending sign's ring, up and right
NEXT_CORNER = (330, 58, 470, 97)  # the next lower limit's row with no sign above it: its small sign in the corner
STRIP = 476                  # the confidence ball's strip: x 476..536
RAIL = (477, 4, 536, 80)     # the rail's item: a 48 px glyph from y 6, figures to y 76
BALL_HIGH = (476, 0, 536, 89)
BALL_LOW = (476, 89, 536, 140)


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
  for b, bn in ((SIGN, "sign"), (SIGN_INNER, "inner"), (SPEED, "speed"), (NEXT, "next"), (LAMP_L, "lamp_l"),
                (LAMP_R, "lamp_r"), (LABEL, "label"), (BANNER, "banner"), (LOWER, "lower"), (ARROW, "arrow"), (KEY, "key"),
                (BADGE, "badge"), (RAIL, "rail"), (BALL_HIGH, "ballhi"), (BALL_LOW, "balllo"), (NEXT_CORNER, "nextc")):
    for kind in ("red", "white", "amber", "orange", "green", "dark", "grey"):
      r[f"{bn}_{kind}"] = count(on, b, kind)
      r[f"stock_{bn}_{kind}"] = count(stock, b, kind)
  if stopped:
    r["banner_right"], r["cluster_left"] = stop_gap(on)
  out[name] = r
  images[name], stocks[name] = on, stock

# compact alerts: each against the same scene with no alert at all - what the alert adds, and where
def cluster_left(arr):
  """The left edge of what the cluster drew in its top rows (anything brighter than its shadows), right of x 200."""
  p = arr[6:66, 200:476]
  cols = np.nonzero((p.max(axis=2) > 40).any(axis=0))[0]
  return int(cols.min()) + 200 if len(cols) else 476


compact = {}
for name in ("confirm_compact", "confirm_compact_plus", "confirm_compact_offset", "confirm_compact_sign_off",
             "confirm_compact_sign_off_no_cl", "confirm_compact_text_vs_arrow", "confirm_compact_empty", "changed",
             "disengage", "disengage_next", "turn", "stopped_sign_off", "confirm_compact_sign_off_offset",
             "confirm_compact_no_limit"):
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
                   "banner_white": count(img, (0, 0, cl, 160), "white"), "sign_red": count(img, SIGN, "red"),
                   "calls": out[name]["calls"], "speed_white": count(img, SPEED, "white"),
                   "base_speed_white": count(base, SPEED, "white"),
                   "base_sign_red": count(base, SIGN, "red"), "lower_white": count(img, (0, 100, 476, 240), "white")}
out["compact"] = compact

# the full-screen versions: each group's own setting off draws its alert exactly as stock
for name, key in (("changed", HS.PARAM_COMPACT_LIMIT), ("disengage", HS.PARAM_COMPACT_DISENGAGE),
                  ("turn", HS.PARAM_COMPACT_TURN), ("confirm_compact", HS.PARAM_COMPACT_LIMIT)):
  scene(**states[name])
  full = render(f"{name}_full_on", {**ON, key: False, HS.PARAM_CONFIRM_LIMIT: False})
  out[f"{name}_full_vs_stock"] = same(full, stocks[name])

# 'set speed changed', compact: nothing over the road, so the stock MAX number shows the new set speed (the first 2.5 s
# after a change - here, after engaging) and the cluster stays
MAX_BOX = (0, 0, 170, 140)
scene(**states["changed"])
early = {k: render(f"changed_max_{k}", p, frames=20, stock=(k == "stock")) for k, p in
         (("on", ON), ("full", FULL_LIMIT), ("stock", ON))}
out["changed_max"] = {k: {"max_white": count(v, MAX_BOX, "white"), "sign_red": count(v, SIGN, "red"),
                          "mid_white": count(v, (180, 0, 400, 130), "white")} for k, v in early.items()}
scene(**{k: v for k, v in states["changed"].items() if k != "alert"})
out["changed_max"]["noalert_vs_on"] = same(render("changed_max_noalert_on", ON, frames=20), early["on"])

# the confirm's target is the stock MAX number: the same digits, drawn in the same place at the same size - '80' of the
# compact confirm (limit 80) against '80' of the stock MAX number after "changed" (45 frames: faded fully in, not yet out),
# inside the digits' ink box (the dashed outline is outside it, 7 px round the ink)
from openpilot.system.ui.lib.application import FontWeight
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import FONT_SIZES


def white_h(arr, b):
  p = box(arr, *b)
  rows = np.nonzero(((p[..., 0] > 200) & (p[..., 1] > 200) & (p[..., 2] > 200)).any(axis=1))[0]
  return int(rows.max() - rows.min() + 1) if len(rows) else 0


scene(**states["changed"])
max80 = render("changed_max_45_on", ON, frames=45)
iw, ih, ix0, iy0 = hud_draw.ink("80", FONT_SIZES.set_speed, 1.0, FontWeight.DISPLAY)
ib = (int(17 + ix0) + 1, int(-4 + iy0) + 1, int(17 + ix0 + iw) - 1, int(-4 + iy0 + ih) - 1)
around = (ib[0], max(0, ib[1] - 4), ib[2], ib[3] + 4)
out["digits"] = {"ink_box": ib, "ink_h": round(ih, 1),
                 "diff": same(box(images["confirm_compact_80"], *ib), box(max80, *ib)),
                 "box_h": white_h(images["confirm_compact_80"], around), "max_h": white_h(max80, around),
                 "calls": out["confirm_compact_80"]["calls"]}

# design A through a drive: the confirm's box, then "set speed changed" - the solid stock MAX number takes its place - or
# a critical alert, which takes the screen on its first frame. Each against the same phases with no confirm before them.
CONF = dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40, ahead=0, ahead_dist=0.0)
CONF_NONE = {k: v for k, v in CONF.items() if k != "alert"}
CHANGED50 = dict(CONF_NONE, alert=CHANGED, set_kph=50, assist="active")
CRIT = dict(CONF, alert=CRITICAL)
seq = {}
steps = [(CONF, 60, "seq_changed_1_confirm_on"), (CHANGED50, 1, "seq_changed_2_first_frame_on"), (CHANGED50, 40, "seq_changed_3_on")]
a = run(ON, steps)
a_calls = list(phase_calls)
b = run(ON, [(CONF_NONE, 60, "seq_changed_ref_1_on")] + steps[1:])
seq["changed"] = {"calls": a_calls, "end_vs_ref": same(a[2], b[2]), "max_white": count(a[2], MAX_BOX, "white")}
steps = [(CONF, 60, "seq_critical_1_confirm_on"), (CRIT, 1, "seq_critical_2_first_frame_on"), (CRIT, 40, "seq_critical_3_on")]
a = run(ON, steps)
a_calls = list(phase_calls)
b = run(ON, steps, stock=True)
seq["critical"] = {"calls": a_calls, "end_vs_stock": same(a[2], b[2]),
                   "first_vs_stock_top_left": same(box(a[1], 0, 0, 260, 170), box(b[1], 0, 0, 260, 170))}
out["seq"] = seq

# HudCurrentSpeed off: only the live speed goes, in every layout; the stop time stays
spd = {}
for name in ("cruise", "sign_off", "sign_zones_in", "school_active", "stopped", "stopped_sign_off", "confirm_compact"):
  scene(**states[name])
  jump = dict(jump_at=10, jump=30.0) if states[name].get("standstill") else {}
  img = render(f"{name}_speed_off_on", {**PARAMS.get(name, ON), **SPEED_OFF}, **jump)
  ref_img = images[name]
  spd[name] = {"px": same(img, ref_img), "box": diff_box(img, ref_img), "white": count(img, (200, 0, 476, 70), "white"),
               "ref_white": count(ref_img, (200, 0, 476, 70), "white"), "sign_red": count(img, SIGN, "red"),
               "ref_sign_red": count(ref_img, SIGN, "red"),
               "row2": same(box(img, 250, 76, 476, 100), box(ref_img, 250, 76, 476, 100)),
               "label": same(box(img, *LABEL), box(ref_img, *LABEL))}
out["speed_off"] = spd

# the sign setting: zones outside a zone is the sign off; inside one it is Always, once it has slid back
out["sign"] = {"zones_out_vs_off": same(images["sign_zones_out"], images["sign_off"]),
               "zones_in_vs_always": same(images["sign_zones_in"], images["variable"]),
               "off_vs_always": same(images["sign_off"], images["cruise"])}
# entering a zone: the digits slide from the corner to beside the sign, not jump (one view, as a drive goes)
ZONE_IN = dict(limit=80, v=70 / KPH, variable=True, ahead=0, ahead_dist=0.0)
ZONE_OUT = dict(limit=80, v=70 / KPH, ahead=0, ahead_dist=0.0)
steps = [(ZONE_OUT, 100, "slide_1_out_on"), (ZONE_IN, 6, "slide_2_debounce_on"), (ZONE_IN, 3, "slide_3_sliding_on"),
         (ZONE_IN, 40, "slide_4_in_on")]
slide = run(SIGN_ZONES, steps)


def digits_left(arr):
  """The left edge of the speed digits: the leftmost white in the top row (the sign's face is right of them)."""
  p = arr[16:56, 250:476]
  white = (p[..., 0] > 200) & (p[..., 1] > 200) & (p[..., 2] > 200)
  cols = np.nonzero(white.any(axis=0))[0]
  return int(cols.min()) + 250 if len(cols) else -1


def reddish(arr, b):
  """Red at any brightness (a sign fading in), not white or grey."""
  p = box(arr, *b)
  r, g, bl = p[..., 0], p[..., 1], p[..., 2]
  return int(((r > 30) & (r > 2 * g) & (r > 2 * bl)).sum())


out["slide"] = {"digits_left": [digits_left(g) for g in slide], "sign_red": [reddish(g, SIGN) for g in slide],
                "sign_red_full": [count(g, SIGN, "red") for g in slide]}

# the compact confirm while entering a zone: until the cluster's sign has (half) faded in, the banner carries the limit
ZC_OUT = dict(limit=50, v=27 / KPH, alert=CONFIRM_PLUS, assist="preActive", set_kph=40, ahead=0, ahead_dist=0.0)
ZC_IN = dict(ZC_OUT, variable=True)
zc = run(SIGN_ZONES, [(ZC_OUT, 100, "zone_confirm_1_out_on"), (ZC_IN, 8, "zone_confirm_2_sliding_on"),
                      (ZC_IN, 60, "zone_confirm_3_in_on")])
out["zone_confirm"] = [{"banner_red": count(g, (0, 0, 300, 120), "red"), "sign_red": reddish(g, SIGN), "calls": c}
                       for g, c in zip(zc, phase_calls, strict=True)]

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
CRUISE_RAIL = dict(v=30 / KPH, limit=50, ahead=0, ahead_dist=0.0, rail=dict(stop_m=None, confident=True))
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

# the offset badge is the only difference an offset makes
out["badge"] = {"px": same(images["confirm_offset"], images["confirm"]),
                "box": diff_box(images["confirm_offset"], images["confirm"])}

# the offset badge on a school-zone sign during the compact confirm: it used to sit on the right-hand lamp; with the
# lamps it sits low on the right of the ring, clear of both lamps and of SCHOOL (pixel for pixel: the lamps' and
# SCHOOL's colors are counted with and without the badge)
school_badge = {}
for school in (1, 2):
  kw = dict(limit=50, v=27 / KPH, school=school, alert=CONFIRM_PLUS, assist="preActive", set_kph=40, ahead=0,
            ahead_dist=0.0)
  scene(**kw)
  plain = render(f"confirm_compact_school{school}_on", ON)
  scene(**dict(kw, offset=5))
  badged = render(f"confirm_compact_school{school}_offset_on", ON)
  # the cluster only: top left the target is 55 against 50
  bx = diff_box(badged[:, 300:476], plain[:, 300:476])
  school_badge[school] = {"px": same(badged[:, 300:476], plain[:, 300:476]),
                          "box": [bx[0] + 300, bx[1], bx[2] + 300, bx[3]] if bx else None,
                          **{f"{bn}_{kind}": [count(plain, b, kind), count(badged, b, kind)]
                             for b, bn in ((LAMP_L, "lamp_l"), (LAMP_R, "lamp_r"), (LABEL, "label"))
                             for kind in ("amber", "grey")}}
out["school_badge"] = school_badge

# while the stock MAX number shows (the first 2.5 s after engaging) the speed digits hide; the sign stays
scene(**states["cruise"])
early = render("cruise_while_max_on", ON, frames=20)
out["cruise_while_max"] = {"speed_white": count(early, SPEED, "white"), "sign_red": count(early, SIGN, "red")}

# the school lamps alternate: half a second later the other one is lit
scene(**states["school_active"])
later = render("school_active_later_on", ON, t0=100.5)
out["school_active_later"] = {"lamp_l_amber": count(later, LAMP_L, "amber"), "lamp_r_amber": count(later, LAMP_R, "amber")}

# an alert's own setting off, the rest on: that alert is drawn exactly as stock
for name, key in (("confirm", HS.PARAM_CONFIRM_LIMIT), ("stopped", HS.PARAM_STOPPED_BANNER)):
  scene(**states[name])
  jump = dict(jump_at=10, jump=30.0) if name == "stopped" else {}
  alone = render(f"{name}_only_its_setting_off", {**PARAMS.get(name, ON), key: False}, **jump)
  stock = render(f"{name}_stock2", ON, stock=True, **jump)
  out[f"{name}_only_its_setting_off_vs_stock"] = same(alone, stock)

# two stops in one drive: the second starts compact again (the banner's departure must not stick for the drive)
STOP = dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0)
steps = [(dict(STOP, lead=0.1), 40, "two_stops_1_stopped_on"),
         (dict(STOP, lead=2.0), 20, "two_stops_2_lead_departs_on"),
         (dict(limit=40, v=10.0, ahead=0, ahead_dist=0.0, lead=8.0), 100, "two_stops_3_driving_on"),
         (dict(STOP, lead=0.1), 40, "two_stops_4_stopped_again_on")]
two = run(ON, steps)
out["two_stops"] = [{"banner_orange": count(g, BANNER, "orange"), "lower_orange": count(g, LOWER, "orange"),
                     "sign_red": count(g, SIGN, "red")} for g in two]

# an alert fading out: the cluster comes back only once the alert has gone (it used to fade in 3 frames into the fade)
steps = [(dict(alert=SATURATED), 40, "fade_1_alert_on"), (dict(), 3, "fade_2_gone_3_frames_on"),
         (dict(), 60, "fade_3_gone_60_frames_on")]
fade_on, fade_stock = run(ON, steps), run(ON, steps, stock=True)
out["alert_fade"] = {"on_vs_stock": [same(a, b) for a, b in zip(fade_on, fade_stock, strict=True)],
                     "sign_red": [count(g, SIGN, "red") for g in fade_on]}

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

print("RESULT " + json.dumps(out), flush=True)
'''


class TestHudRender(OpenpilotTestCase):
  r: dict = {}
  stdout = stderr = ""
  returncode = -1

  @classmethod
  def setup_class(cls):
    env = {**os.environ, "RAYLIB_BACKEND": "headless", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=600)
    cls.stdout, cls.stderr, cls.returncode = p.stdout, p.stderr, p.returncode
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    cls.r = json.loads(lines[-1][len("RESULT "):]) if lines else {}

  def setUp(self):
    super().setUp()
    if "INIT_OK" not in self.stdout:
      self.skipTest(f"no headless raylib window here: {self.stderr[-300:]}")
    assert self.returncode == 0 and self.r, self.stderr[-3000:]

  def test_every_setting_off_is_the_stock_screen_pixel_for_pixel(self):
    for name, r in self.r.items():
      if isinstance(r, dict) and "off_vs_stock" in r:
        assert r["off_vs_stock"] == 0, f"{name}: {r['off_vs_stock']} pixels differ from the stock drawing"

  def test_an_alerts_own_setting_off_draws_it_as_stock(self):
    assert self.r["confirm_only_its_setting_off_vs_stock"] == 0
    assert self.r["stopped_only_its_setting_off_vs_stock"] == 0
    for name in ("changed", "disengage", "turn", "confirm_compact"):
      assert self.r[f"{name}_full_vs_stock"] == 0, f"{name}: its compact setting off - the stock full-screen alert"

  # ----------------------------------------------------------------------------------------- compact alerts
  def assert_banner(self, c, bottom):
    x0, y0, x1, y1 = c["banner"]
    assert x0 >= 8 and y0 >= 6 and y1 <= bottom, f"top left: {c}"
    assert x1 < c["cluster_left"] - 10, f"and short of the cluster: {c}"
    bx0, by0, bx1, by1 = c["box"]
    assert by1 <= max(bottom, 66), f"nothing drawn below the banner and the cluster's top row: the road stays clear {c}"

  def assert_box(self, c):
    """The compact confirm as design A: the target in the MAX number's place, its pill under it - and no banner."""
    assert c["calls"]["box"] == 1 and c["calls"]["banner"] == 0, f"the box, not the banner: {c}"
    assert c["calls"]["top_icons"], f"a top icon, as the stock MAX number is: {c}"
    x0, y0, x1, y1 = c["calls"]["box_rect"]
    assert x0 >= 8 and y0 >= 6 and y1 <= 150, f"top left, the pill clear of the wheel (y 176): {c}"
    bx0, by0, bx1, by1 = c["box"]
    assert by1 <= 150, f"nothing drawn below the pill: the road stays clear {c}"
    assert c["speed_white"] == 0 < c["base_speed_white"] or c["base_speed_white"] == 0, \
      f"the live speed makes way, as for the stock MAX number: {c}"

  def test_the_compact_confirm_puts_its_target_in_the_max_place_and_the_clusters_sign_turns_pending(self):
    minus, plus = self.r["compact"]["confirm_compact"], self.r["compact"]["confirm_compact_plus"]
    for c in (minus, plus):
      self.assert_box(c)
      assert c["banner_green"] > 100 and c["banner_white"] > 3000, f"the big digits, 'press - to confirm', the key: {c}"
      assert c["banner_red"] == 0, "no sign top left: the cluster's own shows the limit"
      assert c["cluster_px"] > 100 and c["sign_red"] < c["base_sign_red"] - 150, f"the cluster's sign dashed: {c}"
    assert plus["banner_green"] > minus["banner_green"] + 50, "'+' has the key's upright too"
    off = self.r["compact"]["confirm_compact_offset"]
    self.assert_box(off)
    assert off["cluster_px"] > minus["cluster_px"] + 100, f"the offset's badge on the cluster's sign: {off}"
    assert off["calls"]["box_rect"][2] == minus["calls"]["box_rect"][2], "the sign shows the offset: no note in the pill"
    assert self.r["confirm_compact"]["strip_vs_stock"] == 0

  def test_the_target_is_the_stock_max_number(self):
    d = self.r["digits"]
    assert d["calls"]["box"] == 1
    assert d["diff"] == 0, f"pixel for pixel the stock MAX digits inside their ink box: {d}"
    assert d["box_h"] == d["max_h"] and 78 <= d["box_h"] <= 82, f"~81 px, the stock size: {d}"

  def test_with_no_sign_in_the_cluster_the_target_still_shows_and_an_offset_is_named(self):
    c = self.r["compact"]["confirm_compact_sign_off"]
    self.assert_box(c)
    assert c["banner_red"] == 0 and c["sign_red"] == 0, f"no sign anywhere: the target is the number {c}"
    # HudConfirmLimit changes nothing here
    n = self.r["compact"]["confirm_compact_sign_off_no_cl"]
    assert n["px"] == c["px"] and n["banner"] == c["banner"], f"HudConfirmLimit off changes nothing here: {n} {c}"
    # with an offset the target is not the limit: the pill names it ('limit 50 +5')
    o = self.r["compact"]["confirm_compact_sign_off_offset"]
    self.assert_box(o)
    assert o["calls"]["box_rect"][2] > c["calls"]["box_rect"][2] + 80, f"the pill is longer by the note: {o} {c}"
    assert o["calls"]["box_rect"][2] < 476 - 20, "and stays on the road"

  def test_with_no_limit_the_confirm_is_todays_banner(self):
    c = self.r["compact"]["confirm_compact_no_limit"]
    assert c["calls"]["banner"] == 1 and c["calls"]["box"] == 0 and not c["calls"]["top_icons"], c
    self.assert_banner(c, 6 + 40)
    assert c["banner_green"] > 100 and c["banner_red"] == 0, f"'press + to confirm' and the key, no sign: {c}"
    assert c["speed_white"] == c["base_speed_white"] > 100, "the live speed stays: there is no big number"

  def test_the_confirm_key_says_what_the_text_says(self):
    plus, minus = self.r["compact"]["confirm_compact_plus"], self.r["compact"]["confirm_compact"]
    c = self.r["compact"]["confirm_compact_text_vs_arrow"]
    self.assert_box(c)
    assert c["banner_green"] > minus["banner_green"] + 50 and abs(c["banner_green"] - plus["banner_green"]) < 30, \
      f"'press + to confirm' with the '+' key although the arrow points down: {c}"

  def test_entering_a_zone_the_target_stays_on_screen(self):
    out, sliding, inside = self.r["zone_confirm"]
    for z in (out, sliding, inside):
      assert z["calls"]["box"] == 1 and z["calls"]["banner"] == 0 and z["banner_red"] == 0, z
    assert out["sign_red"] == 0 and inside["sign_red"] > 100, f"the cluster's sign comes in the zone: {inside}"

  def test_after_the_confirm_the_solid_stock_max_number_takes_its_place(self):
    s = self.r["seq"]["changed"]
    confirm, first, after = s["calls"]
    assert confirm["box"] == 1 and first["box"] == 1, f"it fades out (with the stock MAX fading in), not cut: {s}"
    assert after["box"] == 0 and after["top_icons"], s
    assert s["end_vs_ref"] == 0 and s["max_white"] > 2000, f"then exactly the stock 'set speed changed' frame: {s}"

  def test_a_critical_alert_during_the_confirm_takes_the_screen_at_once(self):
    s = self.r["seq"]["critical"]
    confirm, first, after = s["calls"]
    assert confirm["box"] == 1 and first["box"] == 0 and after["box"] == 0, s
    assert not first["top_icons"], f"not a top icon from its first frame: {s}"
    assert s["first_vs_stock_top_left"] == 0, f"its first frame top left is the stock one: no box over the alert {s}"
    assert confirm["speed"] is None and first["speed"] == 27, \
      f"the box hid the live speed; the cluster fading out under the alert's first frame has it, as stock: {s}"
    assert s["end_vs_stock"] == 0, f"then exactly the stock full-screen alert: {s}"

  def test_a_confirm_with_no_words_draws_no_banner(self):
    c = self.r["compact"]["confirm_compact_empty"]
    assert c["banner"] is None and c["banner_white"] == 0 and c["banner_green"] == 0, f"no empty pill top left: {c}"
    assert c["calls"]["box"] == 0 and c["calls"]["banner"] == 0, c

  def test_set_speed_changed_draws_nothing_and_frees_the_max_number(self):
    assert self.r["compact"]["changed"]["px"] == 0, "pixel for pixel the screen without the alert"
    m = self.r["changed_max"]
    assert m["noalert_vs_on"] == 0
    assert m["on"]["max_white"] > 2000 and m["on"]["sign_red"] > 150 and m["on"]["mid_white"] == 0, \
      f"the MAX number shows the new set speed, the sign stays: {m}"
    assert m["full"]["sign_red"] == 0 and m["full"]["mid_white"] > 500, f"full screen: the cluster under the alert {m}"

  def test_disengage_and_turn_notices_are_banners(self):
    for name, bottom in (("disengage", 6 + 66), ("disengage_next", 6 + 66), ("turn", 6 + 40)):
      c = self.r["compact"][name]
      self.assert_banner(c, bottom)
      assert c["banner_white"] > 500, name
      assert c["cluster_px"] == 0 and c["sign_red"] == c["base_sign_red"] > 150, f"{name}: the cluster untouched {c}"

  def test_a_critical_alert_is_still_the_stock_full_screen(self):
    r = self.r["critical"]
    assert r["on_vs_stock"] == 0, "drawn exactly as stock, the cluster faded out under it"
    assert r["stock_banner_white"] > 300, "the full red alert and its text"

  # ----------------------------------------------------------------------------------------- the sign setting
  def test_the_sign_setting(self):
    s = self.r["sign"]
    assert s["zones_out_vs_off"] == 0, "zones, outside one: exactly the sign off"
    assert s["zones_in_vs_always"] == 0, "zones, in a Variable zone: exactly Always, once it has slid back"
    assert s["off_vs_always"] > 0
    off = self.r["sign_off"]
    assert off["inner_red"] == 0 and off["sign_white"] > 150 and off["speed_white"] < 50, \
      f"no sign, the speed in its place in the corner {off}"
    assert off["nextc_red"] > 30 and off["nextc_white"] > 100, f"the next lower limit stays, in the corner {off}"
    x0, y0, x1, y1 = off["on_diff_box"]
    assert x1 <= 466 and x0 > 300, f"the digits clear the rounded corner and the next limit follows: {off['on_diff_box']}"

  def test_current_speed_off_hides_only_the_live_speed(self):
    spd = self.r["speed_off"]
    for name in ("cruise", "sign_zones_in", "school_active"):
      s = spd[name]
      assert s["px"] > 300 and s["white"] < s["ref_white"] - 300, f"{name}: the digits go {s}"
      x0, y0, x1, y1 = s["box"]
      assert x0 >= 300 and x1 <= 414 and y0 >= 10 and y1 <= 62, f"{name}: only the digits (and their shadow) {s}"
      assert s["sign_red"] == s["ref_sign_red"] > 150 and s["row2"] == 0 and s["label"] == 0, f"{name}: the rest stays {s}"
    s = spd["sign_off"]
    x0, y0, x1, y1 = s["box"]
    assert s["px"] > 300 and x0 >= 380 and x1 <= 466 and y1 <= 62, f"sign Off: the digits in the corner go {s}"
    assert s["row2"] == 0, f"the next lower limit stays where it is: {s}"
    for name in ("stopped", "stopped_sign_off"):
      assert spd[name]["px"] == 0, f"{name}: the stop time is its own setting and stays {spd[name]}"
    assert spd["confirm_compact"]["px"] == 0, "the confirm's target hides the speed anyway"

  def test_the_stop_time_in_the_corner(self):
    r = self.r["stopped_sign_off"]
    assert r["sign_red"] == 0 and r["sign_white"] > 150, f"the stop time where the sign was: {r}"
    assert r["banner_orange"] > 4000 and r["cluster_left"] - r["banner_right"] >= 100, r

  def test_entering_a_zone_the_speed_slides_and_the_sign_fades_in(self):
    s = self.r["slide"]
    out, debounce, sliding, inside = s["digits_left"]
    assert out == debounce and s["sign_red"][0] == s["sign_red"][1] == 0, f"0.3 s first, in the corner {s}"
    assert out > sliding > inside > 0, f"on its way, not jumped: {s}"
    assert s["sign_red"][2] < s["sign_red"][3] and s["sign_red_full"][3] > 150, \
      f"the sign only fading in while the digits leave its place {s}"

  def test_cruise_speed_sign_and_next_lower_limit(self):
    r = self.r["cruise"]
    assert r["on_vs_stock"] > 0
    assert r["sign_red"] > 150 and r["inner_white"] > 300, "the white roundel with its red ring"
    assert r["stock_sign_red"] == 0
    assert r["speed_white"] > 150, "the speed digits"
    assert r["next_red"] > 30 and r["next_white"] > 100, "the next-limit sign, distance and bar"
    x0, y0, x1, y1 = r["on_diff_box"]
    assert x0 >= 200 and y1 <= 100 and x1 < 476, f"top right only, above the horizon, clear of the ball: {r['on_diff_box']}"

  def test_the_stock_max_number_hides_the_speed(self):
    r = self.r["cruise_while_max"]
    assert r["speed_white"] == 0 and r["sign_red"] > 150

  def test_a_higher_or_distant_next_limit_is_not_drawn(self):
    for name in ("next_higher", "next_far", "next_tunnel"):
      r = self.r[name]
      assert r["next_red"] == 0 and r["next_white"] == 0, name
      assert r["sign_red"] > 150, name

  def test_confirm_shows_the_pending_limit_and_hides_the_cluster(self):
    r = self.r["confirm"]
    assert r["arrow_red"] > 60, "the dashed red ring of the pending sign"
    assert r["stock_arrow_red"] == 0, "the stock arrow has no red"
    assert r["key_green"] > 20, "the green minus key"
    x0, y0, x1, y1 = r["on_diff_box"]
    assert x0 >= 374 and y0 >= 18 and x1 < 467 and y1 < 108, f"only the arrow's box changes, the cluster stays hidden: {r['on_diff_box']}"

  def test_the_offset_badge_clears_the_school_zone_lamps(self):
    for school, b in self.r["school_badge"].items():
      assert b["px"] > 150, f"the offset adds a badge: {b}"
      x0, y0, x1, y1 = b["box"]
      assert x0 >= 440 and x1 <= 474 and 23 + 8 <= y0 and y1 < 68, \
        f"low on the right of the ring: under the lamp, above SCHOOL: {school} {b}"
      for k in ("lamp_l_amber", "lamp_l_grey", "lamp_r_amber", "lamp_r_grey", "label_amber"):
        assert b[k][0] == b[k][1], f"the lamps and SCHOOL untouched by the badge ({k}): {school} {b}"
    assert self.r["school_badge"]["2"]["label_amber"][0] > 50, "the active zone's SCHOOL is drawn"

  def test_confirm_shows_an_offset_as_sunnypilots_badge(self):
    r, plain, badge = self.r["confirm_offset"], self.r["confirm"], self.r["badge"]
    assert r["arrow_red"] > 60 and r["key_green"] > 20
    assert badge["px"] > 150, "the offset adds a badge"
    x0, y0, x1, y1 = badge["box"]
    assert x0 >= 438 and y0 >= 22 and x1 <= 468 and y1 <= 54, f"on the ring, up and right, and nothing else: {badge['box']}"
    assert r["badge_grey"] > plain["badge_grey"] + 20, "sunnypilot's grey rim"
    assert r["badge_white"] > 10, "and its white digit"
    x0, y0, x1, y1 = r["on_diff_box"]
    assert x0 >= 374 and y0 >= 18 and x1 < 467 and y1 < 108, f"still inside the arrow's box: {r['on_diff_box']}"

  def test_stopped_banner_and_timer(self):
    r = self.r["stopped"]
    assert r["banner_orange"] > 4000, "the compact banner"
    assert r["lower_orange"] == 0 < r["stock_lower_orange"], "the road below it is not covered"
    assert r["speed_white"] > 150 and r["sign_red"] > 150, "the stop time and the sign"
    assert r["cluster_left"] - r["banner_right"] >= 30, (r["banner_right"], r["cluster_left"])
    assert self.r["timer_size"]["0:30"] == self.r["timer_size"]["59:59"] == 50, "an m:ss time is drawn at full size"

  def test_an_hour_long_stop_keeps_clear_of_the_banner(self):
    r = self.r["stopped_1h"]
    assert r["banner_orange"] > 4000 and r["speed_white"] > 100 and r["sign_red"] > 150
    assert r["cluster_left"] - r["banner_right"] >= 10, ("1:02:05 shrinks to the width of 59:59",
                                                         r["banner_right"], r["cluster_left"])
    assert self.r["timer_size"]["1:02:05"] < 50

  def test_the_full_prompt_returns_when_the_car_ahead_moves_off(self):
    r = self.r["stopped_lead_departs"]
    assert r["on_vs_stock"] == 0, "the stock full-screen prompt, and no cluster over it"

  def test_two_stops_in_a_drive_both_start_compact(self):
    stop1, departs, driving, stop2 = self.r["two_stops"]
    for s in (stop1, stop2):
      assert s["banner_orange"] > 4000 and s["lower_orange"] == 0, s
    assert departs["lower_orange"] > 4000, "the full prompt when the car ahead moved off"
    assert driving["banner_orange"] == 0 and driving["lower_orange"] == 0 and driving["sign_red"] > 150, driving

  def test_the_cluster_returns_only_after_an_alert_has_faded_out(self):
    r = self.r["alert_fade"]
    up, gone3, gone60 = r["on_vs_stock"]
    assert up == 0 and gone3 == 0, f"hidden under the alert and through its fade-out: {r}"
    assert gone60 > 0 and r["sign_red"][2] > 150, f"back once it has gone: {r}"

  def test_the_gateway_icon_falls_back_instead_of_crashing(self):
    g = self.r["gateway_icon"]
    assert g["b"][0] != 0 and g["b"][1] > 0, "icon B loads"
    assert g["fallback_id"] == g["software_id"] != 0, "a pointer in its place: the old icon, and the UI keeps going"

  def test_school_zone_keeps_the_round_sign_and_adds_lamps(self):
    a, later = self.r["school_active"], self.r["school_active_later"]
    assert a["sign_red"] > 150 and a["inner_white"] > 300, "the same roundel"
    assert a["label_amber"] > 30, "SCHOOL under it"
    assert (a["lamp_l_amber"] > 10) != (a["lamp_r_amber"] > 10), "one lamp lit"
    assert (later["lamp_l_amber"] > 10) != (a["lamp_l_amber"] > 10), "and half a second later the other"
    i = self.r["school_inactive"]
    assert i["lamp_l_amber"] == 0 and i["lamp_r_amber"] == 0 and i["label_amber"] == 0
    assert i["lamp_l_grey"] > 10 and i["lamp_r_grey"] > 10, "unlit grey lamps"

  def test_variable_zone_is_the_electronic_sign(self):
    r = self.r["variable"]
    assert r["sign_red"] > 150 and r["inner_dark"] > 600 and r["inner_white"] > 50, "black face, white digits"
    assert r["inner_dark"] > self.r["cruise"]["inner_dark"] + 300 and r["inner_white"] < self.r["cruise"]["inner_white"] - 300

  def test_missing_messages(self):
    r = self.r["missing_plan"]
    assert r["sign_red"] == 0 and r["speed_white"] > 150, "no plan: the speed alone"
    assert self.r["missing_carstate"]["on_vs_stock"] == 0, "no carState: nothing at all"

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
