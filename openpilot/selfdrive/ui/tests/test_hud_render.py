"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 onroad view, drawn for real in a headless raylib window, per HUD state:

  * with the settings on, each piece is where it should be (colours sampled in its box);
  * with every setting off, the frame is PIXEL-IDENTICAL to the stock drawing, i.e. the same view with the three
    FORK(HUD) call sites neutralised (cluster render a no-op, both alert hooks answering "not mine");
  * with only an alert's own setting off, that alert is drawn exactly as stock.

The real AugmentedRoadView, fed synthetic messages (no camera: the placeholder is black, which keeps the colour
sampling honest). It runs in a child process: a raylib that cannot open a headless window must not take the test
runner's worker down with it. No window at all is a skip; anything after the window opened is a real result.
Set HUD_RENDER_OUT=<dir> to keep the PNGs.
"""
import json
import os
import subprocess
import sys

from openpilot.common.test import OpenpilotTestCase

CHILD = r'''
import json, os
import numpy as np
import pyray as rl
from openpilot.system.ui.lib.application import gui_app
gui_app.init_window("hud-render-test")
print("INIT_OK", flush=True)

from openpilot.cereal import messaging
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
      HS.PARAM_STOPPED_TIMER: True, HS.PARAM_STOPPED_BANNER: True, HS.PARAM_CONFIRM_LIMIT: True}
OFF = {k: (0 if k == HS.PARAM_NEXT_LIMIT else False) for k in ON}


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


def put(svc, fill):
  m = messaging.new_message(svc)
  fill(getattr(m, svc))
  sm.data[svc] = getattr(m, svc)
  sm.seen[svc] = sm.alive[svc] = sm.valid[svc] = sm.updated[svc] = True
  sm.recv_frame[svc] = 5


def scene(v=110 / KPH, limit=110, ahead=100, ahead_dist=339.0, ahead_valid=True, school=0, variable=False,
          standstill=False, alert=None, assist="active", set_kph=110, lead=None, lp=True, cs=True):
  for svc in SERVICES:
    sm.seen[svc] = sm.alive[svc] = sm.updated[svc] = False
    sm.recv_frame[svc] = 0

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
      kind, t1, t2, size, status = alert
      s.alertType, s.alertText1, s.alertText2, s.alertSize, s.alertStatus = kind, t1, t2, size, status
  put("selfdriveState", ss)
  put("controlsState", lambda c: None)

  def plan(p):
    r = p.speedLimit.resolver
    r.speedLimit = r.speedLimitLast = r.speedLimitFinal = r.speedLimitFinalLast = limit / KPH
    r.speedLimitValid = r.speedLimitLastValid = True
    r.source = "map"
    p.speedLimit.assist.state = assist
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
  view = AugmentedRoadView()
  view.set_rect(rect)
  saved = (hud_alerts.draw_compact_standstill, hud_alerts.draw_pending_limit)
  if stock:
    hud_alerts.draw_compact_standstill = lambda ar, alert: False
    hud_alerts.draw_pending_limit = lambda ar, layout: False
    view._hud_cluster.render = lambda *a, **k: None
  try:
    for i in range(frames):
      clock["t"] = t0 + i * 0.05 + (jump if jump_at is not None and i >= jump_at else 0.0)
      rl.begin_drawing()
      rl.begin_texture_mode(rt)
      rl.clear_background(rl.BLACK)
      view.render(rect)
      rl.end_texture_mode()
      rl.end_drawing()
  finally:
    hud_alerts.draw_compact_standstill, hud_alerts.draw_pending_limit = saved
  arr = grab()
  if OUT:
    from PIL import Image
    Image.fromarray(arr.astype(np.uint8)).save(os.path.join(OUT, f"{name}.png"))
  view.close()
  return arr


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
  "stopped": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0, lead=0.1),
  "stopped_lead_departs": dict(limit=40, v=0.0, standstill=True, alert=STOPPED, ahead=0, ahead_dist=0.0, lead=2.0),
  "school_active": dict(limit=40, v=33 / KPH, school=2, ahead=0, ahead_dist=0.0),
  "school_inactive": dict(limit=50, v=33 / KPH, school=1, ahead=70, ahead_dist=85.0),
  "variable": dict(limit=80, v=70 / KPH, variable=True, ahead=0, ahead_dist=0.0),
  "missing_plan": dict(lp=False),
  "missing_carstate": dict(cs=False),
}
for name, kw in states.items():
  scene(**kw)
  stopped = kw.get("standstill", False)
  jump = dict(jump_at=10, jump=30.0) if stopped else {}
  on = render(f"{name}_on", ON, **jump)
  off = render(f"{name}_off", OFF, **jump)
  stock = render(f"{name}_stock", ON, stock=True, **jump)
  r = {"off_vs_stock": same(off, stock), "on_vs_stock": same(on, stock), "on_diff_box": diff_box(on, stock)}
  for b, bn in ((SIGN, "sign"), (SIGN_INNER, "inner"), (SPEED, "speed"), (NEXT, "next"), (LAMP_L, "lamp_l"),
                (LAMP_R, "lamp_r"), (LABEL, "label"), (BANNER, "banner"), (LOWER, "lower"), (ARROW, "arrow"), (KEY, "key")):
    for kind in ("red", "white", "amber", "orange", "green", "dark", "grey"):
      r[f"{bn}_{kind}"] = count(on, b, kind)
      r[f"stock_{bn}_{kind}"] = count(stock, b, kind)
  out[name] = r

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
  alone = render(f"{name}_only_its_setting_off", {**ON, key: False}, **jump)
  stock = render(f"{name}_stock2", ON, stock=True, **jump)
  out[f"{name}_only_its_setting_off_vs_stock"] = same(alone, stock)

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

  def test_stopped_banner_and_timer(self):
    r = self.r["stopped"]
    assert r["banner_orange"] > 4000, "the compact banner"
    assert r["lower_orange"] == 0 < r["stock_lower_orange"], "the road below it is not covered"
    assert r["speed_white"] > 150 and r["sign_red"] > 150, "the stop time and the sign"

  def test_the_full_prompt_returns_when_the_car_ahead_moves_off(self):
    r = self.r["stopped_lead_departs"]
    assert r["on_vs_stock"] == 0, "the stock full-screen prompt, and no cluster over it"

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
