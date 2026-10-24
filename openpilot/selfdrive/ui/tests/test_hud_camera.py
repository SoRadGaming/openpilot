"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-CAMERAS): the comma 4's 'camera (map)' (HudCameraAlerts): what decides it (hud_model.CameraState, on mapd's
customReserved12 - this drive's, still arriving, a camera ahead, its distance run down by vEgo between the 1 Hz
messages) and how it is drawn, on the real AugmentedRoadView in a headless raylib window (the harness of
test_hud_render.py): right of the card in MAX's place, under the stop timer at a standstill, never under an alert that
has the screen, nothing with its setting off - then the frame is pixel-identical to one with no camera at all.
Set HUD_RENDER_OUT=<dir> to keep the PNGs.
"""
import json
import os
import subprocess
import sys
from collections import defaultdict

from openpilot.cereal import messaging
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model as HM
from openpilot.selfdrive.ui.tests.test_hud_render import CHILD as RENDER_CHILD

SVC = HM.CAMERA_SERVICE


class FakeSM:
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


def cam_sm(state=2, kind=1, dist=300.0, frame=10, v=20.0, alive=True):
  sm = FakeSM()
  c = sm.put(SVC, frame=frame, alive=alive)
  c.state, c.kind, c.distance = state, kind, dist
  sm.put('carState').vEgo = v
  return sm


ON = HS.HudSettings(**dict.fromkeys(HS.FIELDS.values(), True))
OFF_CAMERA = HS.HudSettings(**{**dict.fromkeys(HS.FIELDS.values(), True), 'camera': False})


class TestCameraState(OpenpilotTestCase):
  def test_shown_with_its_kind_and_distance(self):
    st = HM.CameraState()
    for kind, label in ((1, 'camera (map)'), (2, 'red-light cam (map)'), (3, 'school cam (map)')):
      st.reset()
      c = st.update(cam_sm(kind=kind), ON, started_frame=1, now=0.0)
      assert (c.kind, c.dist, c.label) == (kind, 300.0, label)
      assert '(map)' in c.label

  def test_held_by_mapd_is_shown_too(self):
    assert HM.CameraState().update(cam_sm(state=3), ON, started_frame=1, now=0.0) is not None

  def test_nothing_without_a_camera_its_setting_or_a_live_message(self):
    for sm in (cam_sm(state=1, kind=0, dist=0.0), cam_sm(state=0), cam_sm(kind=0), cam_sm(alive=False), cam_sm(kind=9)):
      assert HM.CameraState().update(sm, ON, started_frame=1, now=0.0) is None
    assert HM.CameraState().update(cam_sm(), OFF_CAMERA, started_frame=1, now=0.0) is None
    assert HM.CameraState().update(cam_sm(frame=3), ON, started_frame=5, now=0.0) is None  # an earlier drive's
    assert HM.CameraState().update(FakeSM(), ON, started_frame=1, now=0.0) is None

  def test_run_down_between_messages_for_one_period(self):
    st, sm = HM.CameraState(), cam_sm(dist=300.0, v=20.0)
    assert st.update(sm, ON, started_frame=1, now=10.0).dist == 300.0
    assert abs(st.update(sm, ON, started_frame=1, now=10.5).dist - 290.0) < 1e-6
    assert abs(st.update(sm, ON, started_frame=1, now=11.8).dist - 280.0) < 1e-6  # run down for one period at most
    sm.put(SVC, frame=11).distance = 275.0
    assert st.update(sm, ON, started_frame=1, now=12.0).dist == 275.0  # mapd's next figure is taken as it is

  def test_gone_once_reached_or_stale(self):
    st, sm = HM.CameraState(), cam_sm(dist=15.0, v=20.0)
    assert st.update(sm, ON, started_frame=1, now=0.0) is not None
    assert st.update(sm, ON, started_frame=1, now=0.9) is None  # run down past it
    st, sm = HM.CameraState(), cam_sm(dist=300.0, v=0.0)
    assert st.update(sm, ON, started_frame=1, now=0.0) is not None
    assert st.update(sm, ON, started_frame=1, now=HM.CAMERA_STALE_S + 0.1) is None  # mapd stopped sending
    assert HM.CameraState().update(cam_sm(dist=HM.CAMERA_MAX_M + 50), ON, started_frame=1, now=0.0) is None

  def test_the_service_is_logged_and_in_the_uis_submaster(self):
    from openpilot.cereal.services import SERVICE_LIST
    from openpilot.selfdrive.ui.sunnypilot import ui_state as ui_state_sp
    assert SERVICE_LIST[SVC].should_log and SERVICE_LIST[SVC].frequency == 1.0 and SERVICE_LIST[SVC].decimation == 1
    with open(ui_state_sp.__file__, encoding="utf-8") as f:
      assert f'"{SVC}",  # FORK(NSW-CAMERAS)' in f.read()


CHILD = RENDER_CHILD.split("\nout = {}\n")[0] + r'''
SVC = "customReserved12"
frame_no = [10]
res = {}


def camera(state=2, kind=1, dist=300.0):
  frame_no[0] += 1

  def fill(c):
    c.state, c.kind, c.distance, c.osmId, c.cameras = state, kind, dist, 123456, 370
  put(SVC, fill, frame=frame_no[0])


def no_camera():
  sm.seen[SVC] = sm.alive[SVC] = sm.updated[SVC] = False
  sm.recv_frame[SVC] = 0


def run_cam(name, params, phases):
  """phases: [(scene kwargs, camera kwargs | None | 'stale', frames)]: the camera message re-sent once a second (every
  20 frames) with its distance run down, as mapd sends it; 'stale': left as it was, no new message."""
  ui_state.params.v = dict(params)
  HS.hud_settings.get(force=True)
  hud_alerts.standstill_banner.reset()
  view = AugmentedRoadView()
  view.set_rect(rect)
  t = 100.0
  for kw, cam, frames in phases:
    scene(**kw)
    v = kw.get("v", 0.0)
    for i in range(frames):
      if i % 20 == 0 and cam != "stale":
        if cam is None:
          no_camera()
        else:
          camera(**dict(cam, dist=max(cam.get("dist", 0.0) - v * (i // 20), 0.0)))
      clock["t"] = t
      t += 0.05
      frame_once(view)
  arr = grab()
  save(arr, name)
  hr = view._hud_renderer
  res[name] = {"rect": [round(x, 1) for x in hr.camera_rect] if hr.camera_rect else None,
               "approach_rect": calls.get("approach_rect"), "box_rect": calls.get("box_rect"),
               "white": count(arr, (226, 0, 476, 150), "white")}
  view.close()
  return arr


V60 = 60 / KPH
CRUISE = dict(v=V60, limit=60, set_kph=60)
COMING = dict(v=V60, limit=60, ahead=40, ahead_dist=200.0, set_kph=60)
STOP = dict(v=0.0, limit=60, standstill=True, set_kph=60)
OFF_CAM = {**ON, HS.PARAM_CAMERA: False}

plain = run_cam("cam_ref_no_camera", ON, [(CRUISE, None, 60), (CRUISE, None, 30)])
on = run_cam("cam_on", ON, [(CRUISE, None, 60), (CRUISE, dict(kind=1, dist=300.0), 30)])
red = run_cam("cam_red_beside_card", ON, [(CRUISE, None, 60), (COMING, dict(kind=2, dist=260.0), 30)])
school = run_cam("cam_school_stock_plus", D1, [(CRUISE, None, 60), (COMING, dict(kind=3, dist=150.0), 30)])
conf = run_cam("cam_beside_confirm", ON, [(CRUISE, None, 60), (dict(v=V60, limit=40, set_kph=60, alert=CONFIRM), dict(kind=1, dist=250.0), 30)])
stop = run_cam("cam_stopped_under_timer", ON, [(STOP, dict(kind=2, dist=22.0), 60)])
off = run_cam("cam_setting_off", OFF_CAM, [(CRUISE, None, 60), (CRUISE, dict(kind=1, dist=300.0), 30)])
crit_ref = run_cam("cam_ref_critical", ON, [(CRUISE, None, 60), (dict(CRUISE, alert=CRITICAL), None, 30)])
crit = run_cam("cam_under_critical", ON, [(CRUISE, None, 60), (dict(CRUISE, alert=CRITICAL), dict(kind=1, dist=300.0), 30)])
none = run_cam("cam_none_ahead", ON, [(CRUISE, None, 60), (CRUISE, dict(state=1, kind=0, dist=0.0), 30)])
plain_long = run_cam("cam_ref_no_camera_long", ON, [(CRUISE, None, 60), (CRUISE, None, 1), (CRUISE, None, 100)])
stale = run_cam("cam_mapd_silent", ON, [(CRUISE, None, 60), (CRUISE, dict(kind=1, dist=300.0), 1), (CRUISE, "stale", 100)])
all_off_ref = run_cam("cam_all_off_ref", OFF, [(CRUISE, None, 60), (CRUISE, None, 30)])
all_off = run_cam("cam_all_off", OFF, [(CRUISE, None, 60), (CRUISE, dict(kind=1, dist=300.0), 30)])
res["off_vs_plain"] = same(off, plain)
res["crit_vs_ref"] = same(crit, crit_ref)
res["none_vs_plain"] = same(none, plain)
res["stale_vs_plain"] = same(stale, plain_long)
res["all_off_vs_ref"] = same(all_off, all_off_ref)
res["on_vs_plain"] = same(on, plain)
res["on_diff_box"] = diff_box(on, plain)
print("RESULT " + json.dumps(res), flush=True)
'''


class TestCameraRender(OpenpilotTestCase):
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

  def test_drawn_top_right_of_the_max_place(self):
    rect = self.r["cam_on"]["rect"]
    assert rect is not None and rect[:2] == [236.0, 12.0], rect
    assert 100 < rect[2] - rect[0] < 200 and 50 < rect[3] - rect[1] < 90, rect
    assert self.r["cam_on"]["white"] > 200, "the camera and its words, in white"
    x0, y0, x1, y1 = self.r["on_diff_box"]
    assert x0 >= 236 and y0 >= 12 and x1 <= rect[2] + 1 and y1 <= rect[3] + 1, "nothing else on the screen changes"

  def test_right_of_the_card_in_both_styles_and_beside_the_confirm(self):
    for name, key in (("cam_red_beside_card", "approach_rect"), ("cam_school_stock_plus", "approach_rect"),
                      ("cam_beside_confirm", "box_rect")):
      rect, card = self.r[name]["rect"], self.r[name][key]
      assert rect is not None and card is not None, name
      assert rect[0] >= card[2] + 10, f"{name}: the camera {rect} keeps right of the card {card}"

  def test_under_the_stop_timer_at_a_standstill(self):
    rect = self.r["cam_stopped_under_timer"]["rect"]
    assert rect is not None and rect[1] == 72.0, rect

  def test_nothing_with_its_setting_off_under_an_alert_with_none_ahead_or_when_mapd_is_silent(self):
    for name in ("cam_setting_off", "cam_under_critical", "cam_none_ahead", "cam_mapd_silent"):
      assert self.r[name]["rect"] is None, name
    for k in ("off_vs_plain", "crit_vs_ref", "none_vs_plain", "stale_vs_plain", "all_off_vs_ref"):
      assert self.r[k] == 0, f"{k}: {self.r[k]} pixels differ"
    assert self.r["on_vs_plain"] > 0
