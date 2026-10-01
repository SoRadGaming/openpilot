"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The Honda dynamic longitudinal learning settings live in three places that have
to agree: the Params registry (C++), the settings panels (Python/raylib), and
the tuner itself (opendbc, a submodule). The panels deliberately hardcode the
key names and defaults instead of importing the tuner, so that a broken tuner
can never take the settings screen off the screen -- this test is what keeps
that copy honest.

Source is parsed, never imported: the panels pull in raylib, which is not
available in every test environment.
"""
import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
REPO = Path(__file__).parents[4]      # the repo root, which holds opendbc_repo/
HONDA_PANEL = ROOT / "selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py"
CRUISE_PANEL = ROOT / "selfdrive/ui/sunnypilot/layouts/settings/cruise.py"
PARAMS_KEYS = ROOT / "common/params_keys.h"
TUNER = REPO / "opendbc_repo/opendbc/sunnypilot/car/honda/dynamic_tuning.py"
GAS_LAW = REPO / "opendbc_repo/opendbc/sunnypilot/car/honda/elesys_gas.py"
SDUI = ROOT / "sunnypilot/sunnylink/settings_ui.json"
SDUI_SRC = ROOT / "sunnypilot/sunnylink/settings_ui_src/pages"
STATSD = ROOT / "sunnypilot/sunnylink/statsd.py"
MICI_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/vehicle.py"
MICI_SETTINGS = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/settings.py"

TOGGLE_PARAMS = ("HondaDynamicTuningEnabled",)
# the HONDA_ELESYS gas law: a setting like the toggle above, but ON by default
GAS_LAW_PARAM = "HondaElesysGasLawV2"
# retired in 2026-10 with the pedal and aero learners; nothing may read, write or show them
RETIRED_RE = re.compile(r"HondaDynPedalGain\d*|HondaDynWindFactor")

# {"Key", {FLAGS, TYPE, "default"}},  -- the default is optional
PARAM_ENTRY_RE = re.compile(r'\{"(?P<key>\w+)",\s*\{(?P<flags>[^,}]+),\s*(?P<type>\w+)(?:,\s*"(?P<default>[^"]*)")?\}\}')


def _panel_constant(name: str, path: Path = HONDA_PANEL):
  tree = ast.parse(path.read_text())
  for node in tree.body:
    if isinstance(node, ast.Assign | ast.AnnAssign):
      targets = node.targets if isinstance(node, ast.Assign) else [node.target]
      for target in targets:
        if isinstance(target, ast.Name) and target.id == name and node.value is not None:
          return ast.literal_eval(node.value)
  raise AssertionError(f"{name} not found in {path.name}")


def _registered_params() -> dict[str, tuple[str, str, str | None]]:
  return {m.group("key"): (m.group("flags").strip(), m.group("type"), m.group("default"))
          for m in PARAM_ENTRY_RE.finditer(PARAMS_KEYS.read_text())}


def _sdui_honda_items() -> list[dict]:
  schema = json.loads(SDUI.read_text())
  honda = schema["vehicle_settings"]["honda"]
  return honda["items"]


def _walk_items(node, ancestors=()):
  """Yield (item, ancestors) for every dict carrying a `key`, with the chain of enclosing dicts."""
  if isinstance(node, dict):
    if isinstance(node.get("key"), str) and "widget" in node:
      yield node, ancestors
    for v in node.values():
      yield from _walk_items(v, ancestors + (node,))
  elif isinstance(node, list):
    for v in node:
      yield from _walk_items(v, ancestors)


def _gated_to_honda(item, ancestors) -> bool:
  for node in (item, *ancestors):
    for rule in node.get("visibility", []) or []:
      if rule.get("type") == "capability" and rule.get("field") == "brand" and rule.get("equals") == "honda":
        return True
  return False


class TestHondaDynamicSettings(unittest.TestCase):
  def test_learned_params_are_registered_as_floats(self):
    registered = _registered_params()
    for key, default in _panel_constant("LEARNED_DEFAULTS").items():
      assert key in registered, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
      flags, key_type, key_default = registered[key]
      assert key_type == "FLOAT", f"{key} is {key_type}, but the panel reads and writes it as a float"
      assert key_default is not None, f"{key} has no default in params_keys.h"
      assert float(key_default) == default, f"{key} defaults to {key_default} in params_keys.h, {default} in the panel"
      # learned values are per-car state that changes every 60 s, so they are
      # deliberately not backed up to sunnylink
      assert "BACKUP" not in flags, f"{key} is learned state and must not be BACKUP"

  def test_toggles_are_registered_and_backed_up(self):
    registered = _registered_params()
    for key in TOGGLE_PARAMS:
      assert key in registered, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
      flags, key_type, key_default = registered[key]
      assert key_type == "BOOL", f"{key} is {key_type}, but both panels use it as a toggle"
      assert key_default == "0", f"{key} must default to off, got {key_default}"
      assert "BACKUP" in flags, f"{key} is a setting and should survive a sunnylink restore"

  def test_mode_time_keys_cover_every_slot(self):
    slots = _panel_constant("MODE_SLOTS")
    learned = _panel_constant("LEARNED_DEFAULTS")
    assert [k for k in learned if k.startswith("HondaDynModeSec")] == [f"HondaDynModeSec{s}" for s in slots], \
      "one HondaDynModeSec<slot> per drive-mode slot, in slot order"

  def test_gas_law_param_is_registered_on_by_default_and_backed_up(self):
    flags, key_type, key_default = _registered_params()[GAS_LAW_PARAM]
    assert key_type == "BOOL", f"{GAS_LAW_PARAM} is {key_type}"
    assert key_default == "1", f"{GAS_LAW_PARAM} must default to the measured law, got {key_default}"
    assert "BACKUP" in flags, f"{GAS_LAW_PARAM} is a setting and should survive a sunnylink restore"
    assert _panel_constant("GAS_LAW_PARAM") == GAS_LAW_PARAM
    assert _panel_constant("GAS_LAW_DEFAULT") is True

  def test_retired_keys_are_gone_everywhere(self):
    # Gone from the registry, so the mici and big panels, sunnylink and statsd must not name
    # them either: Params would raise UnknownKeyName on every read.
    paths = [PARAMS_KEYS, HONDA_PANEL, MICI_PANEL, SDUI, STATSD, *sorted(SDUI_SRC.glob("*.yaml"))]
    if TUNER.is_file():
      paths += [TUNER, GAS_LAW]
    for path in paths:
      for i, line in enumerate(path.read_text().splitlines(), 1):
        # a comment may record the history; code and data may not use the names
        code = line.split("//")[0] if path.suffix == ".h" else line.split("#")[0] if path.suffix in (".py", ".yaml") else line
        assert not RETIRED_RE.search(code), f"{path.name}:{i} still uses a retired key: {line.strip()}"

  def test_statsd_reports_only_registered_honda_keys(self):
    registered = _registered_params()
    reported = re.findall(r"'(Honda\w+)'", STATSD.read_text())
    assert reported, "statsd no longer reports the Honda keys"
    for key in reported:
      assert key in registered, f"statsd reports {key}, which params_keys.h does not register"
    for key in (*TOGGLE_PARAMS, GAS_LAW_PARAM, *_panel_constant("LEARNED_DEFAULTS")):
      assert key in reported, f"statsd does not report {key}"

  def test_both_panels_drive_the_same_params(self):
    # the toggle exists in Settings > Cruise and in Settings > Vehicle > Honda;
    # if one of them ever points at a different key they would silently disagree
    cruise = CRUISE_PANEL.read_text()
    honda = HONDA_PANEL.read_text()
    for key in TOGGLE_PARAMS:
      assert key in cruise, f"the Cruise panel no longer references {key}"
      assert key in honda, f"the Honda vehicle panel no longer references {key}"

  def test_honda_panel_publishes_its_items(self):
    # an empty HondaSettings.items is exactly the regression this panel exists to
    # fix: the brand page renders, with nothing on it
    tree = ast.parse(HONDA_PANEL.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HondaSettings")
    init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    items = [n for n in ast.walk(init) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Attribute) and t.attr == "items" for t in n.targets)]
    assert items, "HondaSettings.__init__ never assigns self.items"
    assert isinstance(items[-1].value, ast.List) and len(items[-1].value.elts) >= 2, \
      "HondaSettings.items should hold the tuning toggle and the learned values row"

  def test_mici_page_shares_the_panel_params(self):
    # the small screen (mici, comma 4) has no Cruise or Vehicle panel of its own,
    # so it carries its own page -- it must drive the same params, not re-spell them
    tree = ast.parse(MICI_PANEL.read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and (node.module or "").endswith("vehicle.brands.honda") for alias in node.names}
    assert {"TUNING_PARAM"} <= imported, \
      "the small-screen page must import the toggle param from the brand panel"

    # any learned key it does name literally has to be a real one: learned_value()
    # falls back to LEARNED_DEFAULTS, so a typo would be a KeyError on render
    learned = _panel_constant("LEARNED_DEFAULTS")
    for literal in re.findall(r"'(HondaDyn\w+)'", MICI_PANEL.read_text()):
      assert literal in learned, f"{literal} is not a learned param"

  def test_mici_settings_registers_the_vehicle_page(self):
    # an unreferenced page is the same as no page at all, which is the bug this
    # whole thing exists to fix
    src = MICI_SETTINGS.read_text()
    assert "VehicleLayoutMici" in src, "the small-screen settings never builds the vehicle page"
    assert re.search(r"items\.insert\(\d+,\s*vehicle_btn\)", src), \
      "the vehicle button is built but never added to the settings row"

  def test_sunnylink_exposes_the_same_toggles(self):
    # the app reads settings_ui.json; if it drifts from the panels, a toggle set
    # from the phone writes a key the car never reads
    items = {i["key"]: i for i in _sdui_honda_items()}
    for key in TOGGLE_PARAMS:
      assert key in items, f"{key} is missing from the honda section of settings_ui.json"
      assert items[key]["widget"] == "toggle"
      # the tuner reads the toggle once when the car goes onroad
      assert items[key].get("needs_onroad_cycle") is True, f"{key} must tell the app it needs an ignition cycle"
      assert items[key].get("title") not in (None, key), f"{key} needs a real title"

  def test_sunnylink_exposes_the_gas_law_toggle(self):
    items = {i["key"]: i for i in _sdui_honda_items()}
    assert GAS_LAW_PARAM in items, f"{GAS_LAW_PARAM} is missing from the honda section of settings_ui.json"
    item = items[GAS_LAW_PARAM]
    assert item["widget"] == "toggle"
    # the car reads it once, at CarController init
    assert item.get("needs_onroad_cycle") is True, f"{GAS_LAW_PARAM} must tell the app it needs an ignition cycle"
    assert "next drive" in item.get("details", ""), "the details must say it applies at the next drive"
    assert item.get("title") not in (None, GAS_LAW_PARAM) and item.get("description"), f"{GAS_LAW_PARAM} needs a real title and description"

  def test_sunnylink_learned_values_are_read_only_and_on_a_page(self):
    # The learned values live in a PAGE section (cruise), NOT in the honda vehicle section.
    # The only info row the dashboard demonstrably renders is LanguageSetting, which is in a
    # page; no brand's vehicle section has ever carried one, and these did not draw there.
    learned = _panel_constant("LEARNED_DEFAULTS")
    schema = json.loads(SDUI.read_text())
    found = {}
    for item, ancestors in _walk_items(schema["panels"]):
      if item["key"] in learned:
        assert item["key"] not in found, f"{item['key']} appears in more than one page"
        found[item["key"]] = (item, ancestors)

    missing = sorted(set(learned) - set(found))
    assert not missing, f"learned values missing from every page of settings_ui.json: {missing}"

    for key, (item, ancestors) in found.items():
      assert item["widget"] == "info", f"{key} is learned state, not a setting"
      # NOT `blocked`: that means DEVICE_ONLY, which the dashboard hides outright.
      # `widget: info` is already read-only -- LanguageSetting is the precedent.
      assert "blocked" not in item, f"{key} must not be blocked, or the app hides it"
      # a Hyundai owner must not see Honda learned state
      assert _gated_to_honda(item, ancestors), f"{key} must sit under a visibility rule gating brand == honda"

    in_vehicle_section = [i["key"] for i in _sdui_honda_items() if i["key"] in learned]
    assert not in_vehicle_section, f"learned values must not be in the honda vehicle section (they do not render there): {in_vehicle_section}"

  def test_sunnylink_keys_are_registered_and_unique(self):
    registered = _registered_params()
    schema = json.loads(SDUI.read_text())
    panel_keys = set()
    for panel in schema["panels"]:
      panel_keys.update(re.findall(r'"key":\s*"(\w+)"', json.dumps(panel)))

    for item in _sdui_honda_items():
      key = item["key"]
      assert key in registered, f"{key} is in settings_ui.json but not in params_keys.h"
      # keys may live in at most one panel; the brand section is separate
      assert key not in panel_keys, f"{key} appears in both a panel and the honda vehicle section"

  def test_panel_defaults_match_the_tuner(self):
    # opendbc is a submodule; skip when it isn't checked out
    if not TUNER.is_file():
      self.skipTest(f"opendbc is not checked out at {TUNER}")

    source = TUNER.read_text()
    spec = re.search(r"_PARAM_SPEC\s*=\s*\{(.*?)\n\}", source, re.DOTALL)
    assert spec, "could not find _PARAM_SPEC in dynamic_tuning.py"
    tuner_defaults = {k: float(v) for k, v in re.findall(r'"(\w+)":\s*\(\s*(-?[\d.]+)', spec.group(1))}

    panel_defaults = _panel_constant("LEARNED_DEFAULTS")
    assert tuner_defaults == panel_defaults, "the panel and the tuner disagree about the learned defaults"

    law = GAS_LAW.read_text()
    slots = re.search(r"DRIVE_MODE_SLOTS\s*=\s*\(([^)]*)\)", law)
    assert slots, "could not find DRIVE_MODE_SLOTS in elesys_gas.py"
    assert tuple(re.findall(r'"(\w+)"', slots.group(1))) == tuple(_panel_constant("MODE_SLOTS")), \
      "the panel and the gas law disagree about the drive-mode slots"
    param = re.search(r'GAS_LAW_PARAM\s*=\s*"(\w+)"', law)
    default = re.search(r"GAS_LAW_DEFAULT\s*=\s*(True|False)", law)
    assert param and param.group(1) == _panel_constant("GAS_LAW_PARAM"), "the panel and the gas law name different params"
    assert default and (default.group(1) == "True") == _panel_constant("GAS_LAW_DEFAULT"), \
      "the panel and the gas law disagree about the default law"


if __name__ == "__main__":
  unittest.main()
