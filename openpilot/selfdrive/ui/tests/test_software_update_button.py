"""FORK(UPDATER): the comma 4's "check for update" button - the download progress label and the
"download update" highlight - driven for real, in a headless raylib window.

The widget runs in a child process: a raylib that cannot open a headless window must not take the
test runner's worker down with it. No window at all is a skip; anything after the window opened
is a real result.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from openpilot.common.test import OpenpilotTestCase

ROOT = Path(__file__).parents[3]   # openpilot/
SOFTWARE = ROOT / "selfdrive/ui/mici/layouts/settings/software.py"

CHILD = r'''
import json, sys
import pyray as rl
from openpilot.system.ui.lib.application import gui_app
gui_app.init_window("software-button-test")
print("INIT_OK", flush=True)

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.mici.layouts.settings import software
from openpilot.selfdrive.ui.mici.widgets.button import LABEL_COLOR, COMPLICATION_GREY


class FakeParams:
  def __init__(self):
    self.v = {}
  def get(self, key, block=False, return_default=False):
    return self.v.get(key)
  def get_bool(self, key, block=False):
    return bool(self.v.get(key))
  def put(self, key, value, block=False):
    self.v[key] = value
  def put_bool(self, key, value, block=False):
    self.v[key] = value
  def remove(self, key):
    self.v.pop(key, None)


ui_state.params = FakeParams()
ui_state.started = False
software.system_time_valid = lambda: True
b = software.CheckUpdateButton()
signals = []
b._signal_updater = signals.append
out = {}


def step(name, **params):
  ui_state.params.v.update(params)
  b._update_state()
  rl.begin_drawing()
  b.render(rl.Rectangle(0, 0, 402, 180))   # the overrides draw without raising
  rl.end_drawing()
  bg = b._handle_background()[0]
  c = b._sub_label._text_color
  out[name] = {"value": b.get_value(), "ready": b._download_ready, "enabled": bool(b.enabled),
               "pressed_bg": bg.id == b._txt_pressed_bg.id,
               "white_sub": (c.r, c.g, c.b, c.a) == (LABEL_COLOR.r, LABEL_COLOR.g, LABEL_COLOR.b, LABEL_COLOR.a),
               "grey_sub": (c.r, c.g, c.b, c.a) == (COMPLICATION_GREY.r, COMPLICATION_GREY.g, COMPLICATION_GREY.b, COMPLICATION_GREY.a)}


step("download_ready", UpdaterState="idle", UpdateFailedCount=0, UpdaterFetchAvailable=True)
b._handle_mouse_release(rl.Vector2(10, 10))
out["tap_signal"] = signals[-1] if signals else None

b._state = software.UpdaterState.UPDATER_RESPONDING
b.set_enabled(False)
step("fetch", UpdaterState="downloading...", UpdaterDownloadProgress={"phase": "code", "pct": 37})
step("checkout", UpdaterDownloadProgress={"phase": "checkout", "pct": None})
step("os", UpdaterDownloadProgress={"phase": "os", "pct": 42})
step("no_progress", UpdaterDownloadProgress=None)
step("finalizing", UpdaterState="finalizing update...", UpdaterDownloadProgress={"phase": "os", "pct": 99})
step("back_to_idle", UpdaterState="idle", UpdaterFetchAvailable=False)
step("ready_again", UpdaterFetchAvailable=True)
# updated's own background download, hours after the last tap: the page is IDLE throughout
b._hide_value_t = None
step("bg_download", UpdaterState="downloading...", UpdaterDownloadProgress={"phase": "os", "pct": 12})
step("bg_checkout", UpdaterDownloadProgress={"phase": "checkout", "pct": None})
step("bg_done", UpdaterState="idle", UpdaterFetchAvailable=False, UpdaterDownloadProgress=None)
out["bg_state_idle"] = b._state == software.UpdaterState.IDLE
ui_state.started = True
step("onroad")
print("RESULT " + json.dumps(out), flush=True)
'''


class TestCheckUpdateButton(OpenpilotTestCase):
  r: dict = {}
  stdout = stderr = ""
  returncode = -1

  @classmethod
  def setup_class(cls):
    env = {**os.environ, "RAYLIB_BACKEND": "headless", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=120)
    cls.stdout, cls.stderr, cls.returncode = p.stdout, p.stderr, p.returncode
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    cls.r = json.loads(lines[-1][len("RESULT "):]) if lines else {}

  def setUp(self):
    super().setUp()
    if "INIT_OK" not in self.stdout:
      self.skipTest(f"no headless raylib window here: {self.stderr[-300:]}")
    assert self.returncode == 0 and self.r, self.stderr[-2000:]

  def test_download_update_stands_out_and_still_downloads(self):
    s = self.r["download_ready"]
    assert s["value"] == "download update" and s["enabled"]
    assert s["ready"] and s["pressed_bg"], "the waiting second tap is not highlighted"
    assert s["white_sub"], "its sub-label is white, not the usual grey"
    assert self.r["tap_signal"] == "SIGHUP", "the highlighted button must still send the download signal"

  def test_progress_label_per_phase(self):
    assert self.r["fetch"]["value"] == "downloading...\ncode 37%"
    assert self.r["checkout"]["value"] == "downloading...\nchecking out"
    assert self.r["os"]["value"] == "downloading...\nos update 42%"
    assert self.r["no_progress"]["value"] == "downloading..."
    assert self.r["finalizing"]["value"] == "finalizing update...", "progress is shown only while downloading"

  def test_a_background_download_shows_its_progress(self):
    # not "download update" (highlighted) for the update already being fetched
    assert self.r["bg_state_idle"], "the page never left IDLE"
    assert self.r["bg_download"]["value"] == "downloading...\nos update 12%"
    assert self.r["bg_checkout"]["value"] == "downloading...\nchecking out"
    assert self.r["bg_done"]["value"] == "", "the progress label outlived the download"
    assert self.r["bg_done"]["enabled"]

  def test_the_highlight_and_the_progress_never_meet(self):
    for k in ("fetch", "checkout", "os", "no_progress", "finalizing", "back_to_idle", "bg_download", "bg_checkout",
              "bg_done", "onroad"):
      assert not self.r[k]["ready"] and not self.r[k]["pressed_bg"] and self.r[k]["grey_sub"], k
    assert self.r["ready_again"]["ready"], "a new update waiting for its tap is highlighted again"

  def test_onroad_is_disabled_and_plain(self):
    assert not self.r["onroad"]["enabled"] and not self.r["onroad"]["ready"]

  def test_the_page_reads_the_param_only_while_downloading(self):
    src = SOFTWARE.read_text(encoding="utf-8")
    assert 'ui_state.params.get(DOWNLOAD_PROGRESS_PARAM) if updater_state == "downloading..." else None' in src
    # the second tap still decides on exactly this text, which download_label() never produces
    assert 'self.DOWNLOAD_UPDATE if self.get_value() == "download update" else self.CHECK_FOR_UPDATE' in src
