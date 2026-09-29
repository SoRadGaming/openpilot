"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(LKAS-GATEWAY): the fast-wheel takeover's two settings, MadsEmergencySteerDisable and
MadsEmergencySteerRate, live in four places that have to agree: the Params registry (C++),
mads.py, the mici page (selfdrive/ui/sunnypilot/mici/layouts/vehicle.py) and the sunnylink
schema. What the takeover does is tested in sunnypilot/mads/tests/test_mads_gateway_pause.py.

The mici page is parsed, never imported: it pulls in raylib (the same approach as
test_honda_dynamic_settings.py and test_maps_settings.py).
"""
import ast
import base64
import json
import re
from pathlib import Path

from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.mads.mads import EMERGENCY_STEER_RATE, EMERGENCY_STEER_RATES, read_emergency_steer_rate
from openpilot.sunnypilot.sunnylink.utils import save_param_from_base64_encoded_string

ROOT = Path(__file__).parents[3]      # openpilot/
PARAMS_KEYS = ROOT / "common/params_keys.h"
MADS = ROOT / "sunnypilot/mads/mads.py"
MICI_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/vehicle.py"
SDUI = ROOT / "sunnypilot/sunnylink/settings_ui.json"
SDUI_SRC = ROOT / "sunnypilot/sunnylink/settings_ui_src/pages/steering.yaml"
APPLICATION = ROOT / "system/ui/lib/application.py"

TOGGLE = "MadsEmergencySteerDisable"
RATE = "MadsEmergencySteerRate"


def _param_entry(key: str) -> str:
  m = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}\}', PARAMS_KEYS.read_text())
  assert m, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
  return m.group(1)


def _panel_constant(name: str):
  for node in ast.parse(MICI_PANEL.read_text()).body:
    if isinstance(node, ast.Assign):
      for target in node.targets:
        if isinstance(target, ast.Name) and target.id == name:
          return ast.literal_eval(node.value)
  raise AssertionError(f"{name} not found in {MICI_PANEL.name}")


def _code_only(src: str) -> str:
  return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def _walk_items(node, ancestors=()):
  if isinstance(node, dict):
    if isinstance(node.get("key"), str) and "widget" in node:
      yield node, ancestors
    for v in node.values():
      yield from _walk_items(v, ancestors + (node,))
  elif isinstance(node, list):
    for v in node:
      yield from _walk_items(v, ancestors)


def _sdui_items() -> dict[str, tuple[dict, tuple]]:
  schema = json.loads(SDUI.read_text())
  found: dict[str, tuple[dict, tuple]] = {}
  for item, ancestors in _walk_items(schema):
    if item["key"] in (TOGGLE, RATE):
      assert item["key"] not in found, f"{item['key']} appears twice in settings_ui.json"
      found[item["key"]] = (item, ancestors)
  return found


class TestFastWheelParams(OpenpilotTestCase):
  def test_params_are_registered_backed_up_and_default_to_the_old_behaviour(self):
    toggle = _param_entry(TOGGLE)
    assert "PERSISTENT" in toggle and "BACKUP" in toggle and "BOOL" in toggle
    assert '"1"' in toggle, "on by default: off would silently remove a takeover every existing install has"
    rate = _param_entry(RATE)
    assert "PERSISTENT" in rate and "BACKUP" in rate and "INT" in rate
    m = re.search(r'"(\d+)"', rate)
    assert m and float(m.group(1)) == EMERGENCY_STEER_RATE, "the registry default must be mads.py's default"

  def test_the_registry_defaults_read_back_as_the_old_behaviour(self):
    params = Params()
    assert params.get_default_value(TOGGLE) is True
    assert params.get_default_value(RATE) == int(EMERGENCY_STEER_RATE)
    assert read_emergency_steer_rate(params) == EMERGENCY_STEER_RATE   # unset: return_default

  def test_what_sunnylink_writes_is_what_mads_reads(self):
    """saveParams sends base64 text and converts it by the key's registered type."""
    params = Params()
    for rate in EMERGENCY_STEER_RATES:
      save_param_from_base64_encoded_string(RATE, base64.b64encode(str(rate).encode()).decode())
      assert params.get(RATE) == rate
      assert read_emergency_steer_rate(params) == float(rate)
    save_param_from_base64_encoded_string(TOGGLE, base64.b64encode(b"0").decode())
    assert params.get_bool(TOGGLE) is False
    save_param_from_base64_encoded_string(TOGGLE, base64.b64encode(b"1").decode())
    assert params.get_bool(TOGGLE) is True

  def test_an_out_of_set_value_on_disk_reads_as_the_default(self):
    params = Params()
    params.put(RATE, 175)
    assert read_emergency_steer_rate(params) == EMERGENCY_STEER_RATE

  def test_mads_reads_both_at_init_and_live(self):
    """read_params() runs every 0.1 s in selfdrived's params thread, so neither needs a restart."""
    tree = ast.parse(MADS.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ModularAssistiveDrivingSystem")
    for fn in ("__init__", "read_params"):
      src = ast.get_source_segment(MADS.read_text(), next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == fn))
      assert src is not None and f'"{TOGGLE}"' in src and "read_emergency_steer_rate(" in src, f"{fn} does not read both"

  def test_every_fork_hunk_is_marked(self):
    for path, needles in ((MADS, ("EMERGENCY_STEER_RATES =", "def read_emergency_steer_rate",
                                  f'get_bool("{TOGGLE}")', "self.emergency_steer_disable and abs(")),
                          (PARAMS_KEYS, (f'{{"{TOGGLE}"', f'{{"{RATE}"')),
                          (SDUI_SRC, (f"- key: {TOGGLE}",))):
      lines = path.read_text().splitlines()
      for needle in needles:
        hits = [i for i, line in enumerate(lines) if needle in line]
        assert hits, f"{path.name}: {needle!r} not found"
        for idx in hits:
          window = lines[max(0, idx - 9):idx + 2]   # a marker above, or a docstring's first line
          assert any("FORK(LKAS-GATEWAY)" in line for line in window), f"{path.name}:{idx + 1}: {needle!r} is not marked"


class TestFastWheelMiciPage(OpenpilotTestCase):
  def test_the_page_uses_the_same_params_and_rates_as_mads(self):
    assert _panel_constant("FAST_WHEEL_PARAM") == TOGGLE
    assert _panel_constant("FAST_WHEEL_RATE_PARAM") == RATE
    assert tuple(_panel_constant("FAST_WHEEL_RATES")) == tuple(int(r) for r in EMERGENCY_STEER_RATES)
    assert _panel_constant("FAST_WHEEL_DEFAULT") == int(EMERGENCY_STEER_RATE)

  def test_the_page_carries_both_rows(self):
    code = _code_only(MICI_PANEL.read_text())
    assert re.search(r'BigParamControl\(tr\("[^"]*"\), FAST_WHEEL_PARAM[,)]', code)
    assert "FastWheelRateToggle()" in code
    widgets = re.search(r"self\._scroller\.add_widgets\(\[(.*?)\]\)", code, re.DOTALL)
    assert widgets, "the page never adds its rows"
    assert "self._fast_wheel_toggle" in widgets.group(1) and "self._fast_wheel_rate" in widgets.group(1)
    # the Honda rows stay first: the fast-wheel pair goes after them
    assert widgets.group(1).index("self._reset_btn") < widgets.group(1).index("self._fast_wheel_toggle")

  def test_the_rate_row_writes_the_rate_not_the_index(self):
    """BigMultiParamToggle stores the option index (0..3), which mads.py would read as the default."""
    src = MICI_PANEL.read_text()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "FastWheelRateToggle")
    assert [b.id for b in cls.bases if isinstance(b, ast.Name)] == ["BigMultiToggle"]
    on_select = ast.get_source_segment(src, next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_on_select"))
    assert on_select is not None and "FAST_WHEEL_RATES[" in on_select and "put(FAST_WHEEL_RATE_PARAM, rate" in on_select

  def test_the_rate_row_is_disabled_with_the_takeover_off(self):
    code = _code_only(MICI_PANEL.read_text())
    assert "toggle_callback=self._on_fast_wheel_toggled" in code
    assert "self._fast_wheel_rate.set_enabled(checked)" in code
    assert "self._fast_wheel_rate.set_enabled(ui_state.params.get_bool(FAST_WHEEL_PARAM))" in code, \
      "a change from sunnylink must re-enable or disable the rate row on the tick"

  def test_titles_fit_the_widgets(self):
    """A BigToggle title over 18 chars drops to the smaller font; the multi-toggle's pills run down its right edge,
    so its title and value have to stay clear of them (\"network usage\", 13 chars, is upstream's longest)."""
    src = MICI_PANEL.read_text()
    toggle = re.search(r'BigParamControl\(tr\("([^"]*)"\), FAST_WHEEL_PARAM', src)
    assert toggle and len(toggle.group(1)) <= 14, toggle
    cls_src = src[src.index("class FastWheelRateToggle"):src.index("class VehicleLayoutMici")]
    title = re.search(r'super\(\)\.__init__\(tr\("([^"]*)"\)', cls_src)
    assert title and len(title.group(1)) <= 13, title
    for rate in _panel_constant("FAST_WHEEL_RATES"):
      assert len(f"{rate}°/s") <= 8

  def test_no_glyphs_the_baked_font_does_not_have(self):
    extra = re.search(r'EXTRA_FONT_CHARS\s*=\s*"([^"]*)"', APPLICATION.read_text(encoding="utf-8")).group(1)
    allowed = set(map(chr, range(32, 127))) | set(extra)
    src = MICI_PANEL.read_text(encoding="utf-8")
    # tr() strings, and f-strings that are drawn (the rate labels); \w before f" would be the end of a word
    for s in re.findall(r'tr\("([^"\n]*)"\)', src) + re.findall(r'(?<!\w)f"([^"\n]*)"', src):
      bad = sorted({c for c in s if c not in allowed})
      assert not bad, f"{MICI_PANEL.name}: {s!r} has {[hex(ord(c)) for c in bad]}, not in the baked font"


class TestFastWheelSunnylink(OpenpilotTestCase):
  def test_both_are_under_the_mads_settings(self):
    found = _sdui_items()
    assert set(found) == {TOGGLE, RATE}, f"missing from settings_ui.json: {sorted({TOGGLE, RATE} - set(found))}"
    for key, (_, ancestors) in found.items():
      assert any(a.get("id") == "mads_settings" for a in ancestors), f"{key} is not in the MADS settings sub-panel"
      assert any(a.get("id") == "steering" for a in ancestors), f"{key} is not on the steering page"

  def test_the_toggle(self):
    item, _ = _sdui_items()[TOGGLE]
    assert item["widget"] == "toggle"
    assert item.get("title") not in (None, "", TOGGLE)
    assert not item.get("blocked") and not item.get("needs_onroad_cycle"), "mads.py re-reads it every 0.1 s"
    assert "offroad_only" not in json.dumps(item.get("enablement", [])), "it applies live; no reason to lock it onroad"

  def test_the_rate_offers_exactly_the_rates_mads_accepts(self):
    item, ancestors = _sdui_items()[RATE]
    assert item["widget"] == "option"
    assert [o["value"] for o in item["options"]] == [int(r) for r in EMERGENCY_STEER_RATES]
    assert all(isinstance(o["value"], int) and not isinstance(o["value"], bool) for o in item["options"]), \
      "INT param: the app must send integers"
    default = next(o for o in item["options"] if o["value"] == int(EMERGENCY_STEER_RATE))
    assert "default" in default["label"]
    # it sits under the toggle and is only live while the toggle is on
    assert any(a.get("key") == TOGGLE for a in ancestors), "the rate should be a sub-item of the toggle"
    assert {"type": "param", "key": TOGGLE, "equals": True} in item.get("enablement", [])
