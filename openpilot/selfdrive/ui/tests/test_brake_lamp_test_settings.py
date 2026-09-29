"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-TEST): the stop-lamp bit test has one param and three front ends that have to
agree -- the Params registry, the comma 4 vehicle page, sunnylink -- and the car side in opendbc,
whose CANDIDATES list is the truth. The panel hardcodes the labels (so a broken opendbc cannot
take the page down); this test keeps that copy honest.

The panel is parsed, never imported: it pulls in raylib.
"""
import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
REPO = Path(__file__).parents[4]      # the repo root, which holds opendbc_repo/
PARAMS_KEYS = ROOT / "common/params_keys.h"
MICI_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/vehicle.py"
SDUI = ROOT / "sunnypilot/sunnylink/settings_ui.json"
CAR_SIDE = REPO / "opendbc_repo/opendbc/sunnypilot/car/honda/brake_lamp_test.py"
FONT = ROOT / "selfdrive/assets/fonts/Inter-Regular.ttf"

PARAM = "HondaBrakeLampTest"
PARAM_ENTRY_RE = re.compile(r'\{"(?P<key>\w+)",\s*\{(?P<flags>[^,}]+),\s*(?P<type>\w+)(?:,\s*"(?P<default>[^"]*)")?\}\}')

# BigButton gives its value (sub-label) 402 - 2 * 40 px, drawn in Inter Regular at COMPLICATION_SIZE 36
VALUE_WIDTH_PX = 322
VALUE_FONT_SIZE = 36


def _panel_constant(name: str):
  for node in ast.parse(MICI_PANEL.read_text()).body:
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Name) and target.id == name:
          return ast.literal_eval(node.value)
  raise AssertionError(f"{name} not found in {MICI_PANEL.name}")


def _car_side_candidates() -> list[tuple[str, bool]]:
  """(label, risky) per entry. Parsed, like the panel, so this runs without opendbc on the path."""
  tree = ast.parse(CAR_SIDE.read_text())
  for node in tree.body:
    if isinstance(node, ast.AnnAssign | ast.Assign):
      targets = node.targets if isinstance(node, ast.Assign) else [node.target]
      if any(isinstance(t, ast.Name) and t.id == "CANDIDATES" for t in targets):
        assert isinstance(node.value, ast.Tuple)
        out = []
        for call in node.value.elts:
          assert isinstance(call, ast.Call)
          risky = [ast.literal_eval(k.value) for k in call.keywords if k.arg == "risky"]
          out.append((ast.literal_eval(call.args[0]), bool(risky and risky[0])))
        return out
  raise AssertionError("CANDIDATES not found in brake_lamp_test.py")


def _car_side_labels() -> list[str]:
  return [label for label, _ in _car_side_candidates()]


def _sdui_item() -> dict:
  items = json.loads(SDUI.read_text())["vehicle_settings"]["honda"]["items"]
  found = [i for i in items if i["key"] == PARAM]
  assert len(found) == 1, f"{PARAM} must appear exactly once in the honda section of settings_ui.json"
  return found[0]


class TestBrakeLampTestSettings(unittest.TestCase):
  def test_param_is_registered(self):
    params = {m.group("key"): m.groups()[1:] for m in PARAM_ENTRY_RE.finditer(PARAMS_KEYS.read_text())}
    assert PARAM in params, f"{PARAM} is not in params_keys.h"
    flags, key_type, default = params[PARAM]
    self.assertEqual(key_type, "INT")
    self.assertEqual(default, "0")
    # a test mode must not outlive the drive it was set in, nor come back from a sunnylink restore
    self.assertIn("CLEAR_ON_MANAGER_START", flags)
    self.assertIn("CLEAR_ON_OFFROAD_TRANSITION", flags)
    self.assertNotIn("PERSISTENT", flags)
    self.assertNotIn("BACKUP", flags)

  def test_panel_labels_match_the_car(self):
    if not CAR_SIDE.is_file():
      self.skipTest(f"opendbc is not checked out at {CAR_SIDE}")
    self.assertEqual(list(_panel_constant("LAMP_TEST_LABELS")), _car_side_labels())
    self.assertEqual(_panel_constant("LAMP_TEST_PARAM"), PARAM)
    # the panel asks for a slide from the first risky entry on; the car side keeps them a tail
    risky = [r for _, r in _car_side_candidates()]
    first = _panel_constant("LAMP_TEST_RISKY_FROM")
    self.assertEqual(risky, [i >= first for i in range(1, len(risky) + 1)])

  def test_panel_builds_the_rows(self):
    src = MICI_PANEL.read_text()
    for needle in ('tr("lamp test")', 'tr("lamp test back")', 'tr("lamp test off")', "self._lamp_btn, self._lamp_back_btn, self._lamp_off_btn",
                   'BigConfirmationDialog(tr("slide to test")',"CP is not None and CP.carFingerprint == LAMP_TEST_PLATFORM"):
      self.assertIn(needle, src)

  def test_panel_text_fits_and_is_ascii(self):
    labels = _panel_constant("LAMP_TEST_LABELS")
    texts = ["off"] + [f"{i}: {label}" for i, label in enumerate(labels, 1)]
    for text in texts:
      self.assertTrue(text.isascii() and text.isprintable(), text)
    try:
      from PIL import ImageFont
    except ImportError:
      self.skipTest("PIL is not installed")
    font = ImageFont.truetype(str(FONT), VALUE_FONT_SIZE)
    widest = max(texts, key=font.getlength)
    self.assertLessEqual(font.getlength(widest), VALUE_WIDTH_PX, f"'{widest}' does not fit a BigButton value")

  def test_sunnylink_item(self):
    item = _sdui_item()
    self.assertEqual(item["widget"], "option")
    options = item["options"]
    self.assertEqual([o["value"] for o in options], list(range(len(options))))
    if CAR_SIDE.is_file():
      self.assertEqual([o["label"] for o in options], ["Off"] + _car_side_labels())
    # usable onroad: the whole point is changing it while stopped with openpilot holding the brake
    self.assertNotIn("offroad_only", json.dumps(item.get("enablement", [])))
    self.assertNotIn("needs_onroad_cycle", item)
    rules = json.dumps(item.get("visibility", []))
    self.assertIn("has_longitudinal_control", rules)
    # there is no per-platform capability, so the title has to say which car it is for
    self.assertIn("HONDA_ACCORD_9G_AU", item["title"] + item.get("description", ""))


if __name__ == "__main__":
  unittest.main()
