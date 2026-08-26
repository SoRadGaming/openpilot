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
import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
HONDA_PANEL = ROOT / "selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py"
CRUISE_PANEL = ROOT / "selfdrive/ui/sunnypilot/layouts/settings/cruise.py"
PARAMS_KEYS = ROOT / "common/params_keys.h"
TUNER = ROOT / "opendbc_repo/opendbc/sunnypilot/car/honda/dynamic_tuning.py"

TOGGLE_PARAMS = ("HondaDynamicTuningEnabled", "HondaDynamicPcmBlendEnabled")

# {"Key", {FLAGS, TYPE, "default"}},  -- the default is optional
PARAM_ENTRY_RE = re.compile(r'\{"(?P<key>\w+)",\s*\{(?P<flags>[^,}]+),\s*(?P<type>\w+)(?:,\s*"(?P<default>[^"]*)")?\}\}')


def _panel_constant(name: str, path: Path = HONDA_PANEL):
  tree = ast.parse(path.read_text())
  for node in tree.body:
    if isinstance(node, ast.Assign | ast.AnnAssign):
      targets = node.targets if isinstance(node, ast.Assign) else [node.target]
      for target in targets:
        if isinstance(target, ast.Name) and target.id == name:
          return ast.literal_eval(node.value)
  raise AssertionError(f"{name} not found in {path.name}")


def _registered_params() -> dict[str, tuple[str, str, str | None]]:
  return {m.group("key"): (m.group("flags").strip(), m.group("type"), m.group("default"))
          for m in PARAM_ENTRY_RE.finditer(PARAMS_KEYS.read_text())}


def test_learned_params_are_registered_as_floats():
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


def test_toggles_are_registered_and_backed_up():
  registered = _registered_params()
  for key in TOGGLE_PARAMS:
    assert key in registered, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
    flags, key_type, key_default = registered[key]
    assert key_type == "BOOL", f"{key} is {key_type}, but both panels use it as a toggle"
    assert key_default == "0", f"{key} must default to off, got {key_default}"
    assert "BACKUP" in flags, f"{key} is a setting and should survive a sunnylink restore"


def test_pedal_gain_breakpoints_match_the_learned_gains():
  breakpoints = _panel_constant("PEDAL_GAIN_BP")
  gains = [k for k in _panel_constant("LEARNED_DEFAULTS") if k.startswith("HondaDynPedalGain")]
  assert len(breakpoints) == len(gains), "one learned gain per speed breakpoint"
  assert sorted(gains) == [f"HondaDynPedalGain{i}" for i in range(len(gains))], "gains must be numbered from 0"
  assert list(breakpoints) == sorted(breakpoints), "breakpoints must ascend"


def test_both_panels_drive_the_same_params():
  # the toggle exists in Settings > Cruise and in Settings > Vehicle > Honda;
  # if one of them ever points at a different key they would silently disagree
  cruise = CRUISE_PANEL.read_text()
  honda = HONDA_PANEL.read_text()
  for key in TOGGLE_PARAMS:
    assert key in cruise, f"the Cruise panel no longer references {key}"
    assert key in honda, f"the Honda vehicle panel no longer references {key}"


def test_honda_panel_publishes_its_items():
  # an empty HondaSettings.items is exactly the regression this panel exists to
  # fix: the brand page renders, with nothing on it
  tree = ast.parse(HONDA_PANEL.read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HondaSettings")
  init = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
  items = [n for n in ast.walk(init) if isinstance(n, ast.Assign)
           and any(isinstance(t, ast.Attribute) and t.attr == "items" for t in n.targets)]
  assert items, "HondaSettings.__init__ never assigns self.items"
  assert isinstance(items[-1].value, ast.List) and len(items[-1].value.elts) >= 3, \
    "HondaSettings.items should hold the two toggles and the learned values row"


def test_panel_defaults_match_the_tuner():
  # opendbc is a submodule; skip when it isn't checked out
  if not TUNER.is_file():
    return

  source = TUNER.read_text()
  spec = re.search(r"_PARAM_SPEC\s*=\s*\{(.*?)\n\}", source, re.DOTALL)
  assert spec, "could not find _PARAM_SPEC in dynamic_tuning.py"
  tuner_defaults = {k: float(v) for k, v in re.findall(r'"(\w+)":\s*\(\s*(-?[\d.]+)', spec.group(1))}

  panel_defaults = _panel_constant("LEARNED_DEFAULTS")
  assert tuner_defaults == panel_defaults, "the panel and the tuner disagree about the learned defaults"

  bp = re.search(r"ELESYS_GAS_BP\s*=\s*\[([^\]]*)\]",
                 (TUNER.parent / "gas_interceptor.py").read_text())
  assert bp, "could not find ELESYS_GAS_BP in gas_interceptor.py"
  tuner_bp = [float(v) for v in bp.group(1).replace(" ", "").strip(",").split(",")]
  assert tuner_bp == list(_panel_constant("PEDAL_GAIN_BP")), "the panel and the tuner disagree about the speed bands"


if __name__ == "__main__":
  for name, fn in sorted(globals().copy().items()):
    if name.startswith("test_") and callable(fn):
      fn()
      print(f"{name}: ok")
