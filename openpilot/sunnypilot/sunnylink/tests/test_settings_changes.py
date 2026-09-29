"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Per-bug regression tests for the Raylib-vs-schema parity audit. Each test
isolates one of the gating bugs that the design-overhaul branch fixes so a
future regression is loud and obvious. These tests are intentionally narrow
and additive — they do not replace the broader test_settings_schema.py.
"""
from __future__ import annotations

import json
import os
from typing import Any

from openpilot.common.parameterized import parameterized

from openpilot.sunnypilot.sunnylink.tools.generate_settings_schema import (
  DEFINITION_PATH,
  TORQUE_VERSIONS_PATH,
  _build_torque_options,
  _load_torque_versions,
  generate_schema,
)
from openpilot.common.test import OpenpilotTestCase


SCHEMA_VALIDATOR_PATH = os.path.join(os.path.dirname(DEFINITION_PATH), "settings_ui.schema.json")


def _walk_items(schema: dict[str, Any]):
  """Yield every item dict from the schema."""
  def _yield(item: dict[str, Any]):
    yield item
    for sub in item.get("sub_items", []):
      yield from _yield(sub)

  for panel in schema.get("panels", []):
    for section in panel.get("sections", []):
      for item in section.get("items", []):
        yield from _yield(item)
      for sp in section.get("sub_panels", []):
        for item in sp.get("items", []):
          yield from _yield(item)
    for item in panel.get("items", []):
      yield from _yield(item)
    for sp in panel.get("sub_panels", []):
      for item in sp.get("items", []):
        yield from _yield(item)
  for brand in schema.get("vehicle_settings", {}).values():
    items = brand.get("items", []) if isinstance(brand, dict) else brand
    for item in items:
      yield from _yield(item)


def _find_item(schema: dict[str, Any], key: str) -> dict[str, Any] | None:
  for item in _walk_items(schema):
    if item.get("key") == key:
      return item
  return None


def _find_section(schema: dict[str, Any], panel_id: str, section_id: str) -> dict[str, Any] | None:
  for panel in schema.get("panels", []):
    if panel.get("id") != panel_id:
      continue
    for section in panel.get("sections", []):
      if section.get("id") == section_id:
        return section
  return None


def _flatten_rule_types(rules: list[dict[str, Any]] | None) -> set[str]:
  out: set[str] = set()

  def _walk(rule: dict[str, Any]) -> None:
    out.add(rule.get("type", ""))
    if rule.get("type") == "not" and "condition" in rule:
      _walk(rule["condition"])
    elif rule.get("type") in ("any", "all"):
      for c in rule.get("conditions", []):
        _walk(c)

  for rule in rules or []:
    _walk(rule)
  return out


def _references_capability_field(rules: list[dict[str, Any]] | None, field: str) -> bool:
  found = False

  def _walk(rule: dict[str, Any]) -> None:
    nonlocal found
    if rule.get("type") == "capability" and rule.get("field") == field:
      found = True
    elif rule.get("type") == "not" and "condition" in rule:
      _walk(rule["condition"])
    elif rule.get("type") in ("any", "all"):
      for c in rule.get("conditions", []):
        _walk(c)

  for rule in rules or []:
    _walk(rule)
  return found


def schema():
  return generate_schema()


class TestMadsBrandGates(OpenpilotTestCase):
  def test_mads_main_cruise_has_brand_gate(self, schema):
    """MadsMainCruiseAllowed must gate on brand and tesla_has_vehicle_bus."""
    item = _find_item(schema, "MadsMainCruiseAllowed")
    assert item is not None
    assert _references_capability_field(item.get("enablement"), "brand")
    assert _references_capability_field(item.get("enablement"), "tesla_has_vehicle_bus")

  def test_mads_unified_engagement_has_brand_gate(self, schema):
    """MadsUnifiedEngagementMode must mirror MadsMainCruiseAllowed brand-gate."""
    item = _find_item(schema, "MadsUnifiedEngagementMode")
    assert item is not None
    assert _references_capability_field(item.get("enablement"), "brand")
    assert _references_capability_field(item.get("enablement"), "tesla_has_vehicle_bus")


class TestTestManeuversSection(OpenpilotTestCase):
  def test_lateral_maneuver_mode_in_test_maneuvers(self, schema):
    section = _find_section(schema, "developer", "test_maneuvers")
    assert section is not None, "developer.test_maneuvers section missing"
    keys = {item["key"] for item in section.get("items", [])}
    assert "LateralManeuverMode" in keys
    assert "LongitudinalManeuverMode" in keys

  def test_test_maneuvers_section_requires_attestation(self, schema):
    section = _find_section(schema, "developer", "test_maneuvers")
    assert section is not None
    assert section.get("attestation_required") is True

  def test_test_maneuvers_section_visibility_gate(self, schema):
    section = _find_section(schema, "developer", "test_maneuvers")
    assert section is not None
    visibility = section.get("visibility")
    assert visibility, "test_maneuvers must have visibility gate"
    vis_refs = json.dumps(visibility)
    assert "is_development" in vis_refs
    assert "is_sp_release" in vis_refs
    enablement = section.get("enablement") or []
    enable_refs = json.dumps(enablement)
    assert "ShowAdvancedControls" in enable_refs, \
      "test_maneuvers must gate ShowAdvancedControls via enablement"


class TestValidator(OpenpilotTestCase):
  def test_validator_accepts_real_json(self):
    """settings_ui.json validates against settings_ui.schema.json."""
    try:
      import jsonschema
    except ImportError:
      self.skipTest("jsonschema not installed")
    with open(DEFINITION_PATH) as f:
      data = json.load(f)
    with open(SCHEMA_VALIDATOR_PATH) as f:
      validator = json.load(f)
    jsonschema.validate(instance=data, schema=validator)


class TestTorqueOptionGeneration(OpenpilotTestCase):
  def test_torque_versions_match_generated_options(self, schema):
    versions = _load_torque_versions()
    assert versions, "latcontrol_torque_versions.json must have at least one version"
    expected = _build_torque_options(versions)
    item = _find_item(schema, "TorqueControlTune")
    assert item is not None, "TorqueControlTune item must be present"
    assert item.get("options") == expected

  def test_torque_versions_path_resolves(self):
    assert os.path.exists(TORQUE_VERSIONS_PATH), (
      f"latcontrol_torque_versions.json not found at {TORQUE_VERSIONS_PATH}"
    )


class TestReleaseBranchGates(OpenpilotTestCase):
  @parameterized.expand([
    "EnableGithubRunner",
    "QuickBootToggle",
  ], names=["key"])
  def test_sp_dev_items_gate_on_is_sp_release(self, schema, key):
    """sunnypilot dev items must hide on sunnypilot release branches (is_sp_release gate)."""
    item = _find_item(schema, key)
    assert item is not None, f"{key} not found in schema"
    rules = (item.get("visibility") or []) + (item.get("enablement") or [])
    assert _references_capability_field(rules, "is_sp_release"), f"{key} missing is_sp_release gate"


class TestSpuriousOffroadGatesDropped(OpenpilotTestCase):
  def test_disengage_on_accelerator_has_no_offroad_only(self, schema):
    item = _find_item(schema, "DisengageOnAccelerator")
    assert item is not None
    assert "offroad_only" not in _flatten_rule_types(item.get("enablement"))

  def test_dynamic_experimental_has_no_offroad_only(self, schema):
    item = _find_item(schema, "DynamicExperimentalControl")
    assert item is not None
    assert "offroad_only" not in _flatten_rule_types(item.get("enablement"))


class TestNotEngagedReplacement(OpenpilotTestCase):
  @parameterized.expand([
    "AlphaLongitudinalEnabled",
    "ToyotaEnforceStockLongitudinal",
    "ToyotaStopAndGoHack",
  ], names=["key"])
  def test_offroad_only_replaced_with_not_engaged(self, schema, key):
    """These items should use not_engaged, not offroad_only."""
    item = _find_item(schema, key)
    assert item is not None, f"{key} not found"
    rule_types = _flatten_rule_types(item.get("enablement"))
    assert "offroad_only" not in rule_types, f"{key} still uses offroad_only"
    assert "not_engaged" in rule_types, f"{key} missing not_engaged"


# FORK(NSW-ZONES) ------------------------------------------------------------------------------------------------------
MAP_DATA_SUB_PANEL = "speed_limit_settings"
MAP_DATA_NOW = ("NswZonesUpdateCheck", "OsmDbUpdatesCheck")      # requests mapd_manager consumes and clears
MAP_DATA_WEEKLY = ("NswZonesAutoUpdate", "OsmAutoUpdateWeekly")


def _sub_panel_items(schema: dict[str, Any], sub_panel_id: str) -> list[dict[str, Any]]:
  for panel in schema.get("panels", []):
    for sp in [*panel.get("sub_panels", []), *(s for sec in panel.get("sections", []) for s in sec.get("sub_panels", []))]:
      if sp.get("id") == sub_panel_id:
        return sp.get("items", [])
  return []


class TestMapDataControls(OpenpilotTestCase):
  """The mici maps page's update buttons and weekly toggles, from the phone. The "Now" items are toggles: nothing in
  this tree says what the app does for a `button` widget, and the device clears the param once it has the request."""

  def test_they_sit_with_the_nsw_mode(self, schema):
    keys = [item.get("key") for item in _sub_panel_items(schema, MAP_DATA_SUB_PANEL)]
    assert "SpeedLimitNswZones" in keys
    for key in (*MAP_DATA_NOW, *MAP_DATA_WEEKLY):
      assert key in keys, f"{key} is not in the {MAP_DATA_SUB_PANEL} sub-panel"

  @parameterized.expand([*MAP_DATA_NOW, *MAP_DATA_WEEKLY], names=["key"])
  def test_they_are_toggles_over_bool_params(self, schema, key):
    from openpilot.common.params import Params, ParamKeyType
    item = _find_item(schema, key)
    assert item is not None and item["widget"] == "toggle", item
    assert Params().get_type(key) == ParamKeyType.BOOL, "a toggle writes a bool"
    assert not item.get("blocked"), f"{key} must be writable from the app"

  @parameterized.expand(list(MAP_DATA_NOW), names=["key"])
  def test_update_now_is_offroad_only_and_says_it_resets(self, schema, key):
    item = _find_item(schema, key)
    assert item is not None
    assert "offroad_only" in _flatten_rule_types(item.get("enablement")), f"{key}: the app greys it out while driving"
    desc = item.get("description", "")
    assert "offroad" in desc and "switches it back off" in desc, desc
    # the device clears the param without bumping ParamsVersion: the app only sees it on its next load
    assert "next time it loads" in desc, desc

  def test_update_nsw_now_is_dimmed_with_the_mode_off(self, schema):
    """With NSW zones off a tap would still download ~22 MB for a disabled feature: same rule as its weekly sibling."""
    now, weekly = _find_item(schema, "NswZonesUpdateCheck"), _find_item(schema, "NswZonesAutoUpdate")
    assert now is not None and weekly is not None
    assert now.get("visibility") and now.get("visibility") == weekly.get("visibility"), now.get("visibility")

  def test_update_osm_now_says_driving_off_does_not_stop_it(self, schema):
    """mapd has no cancel: an OSM download started offroad runs on after the car drives off."""
    item = _find_item(schema, "OsmDbUpdatesCheck")
    assert item is not None and "Driving off does not stop" in item.get("description", "")

  @parameterized.expand(list(MAP_DATA_NOW), names=["key"])
  def test_sunnylink_can_write_the_request_and_sees_it_cleared(self, key):
    """saveParams writes any key not in BLOCKED_PARAMS whatever its flags: CLEAR_ON_MANAGER_START is no obstacle.
    Once mapd_manager has cleared it, getParams reports it off."""
    import base64

    from openpilot.common.params import Params
    from openpilot.sunnypilot.sunnylink.athena import sunnylinkd
    assert key not in sunnylinkd.BLOCKED_PARAMS
    sunnylinkd.saveParams({key: base64.b64encode(b"1").decode()})
    assert Params().get_bool(key)
    assert base64.b64decode(sunnylinkd.getParams([key])[key]) in (b"True", b"1")
    Params().remove(key)  # what the consumer does with a request, taken or refused
    assert base64.b64decode(sunnylinkd.getParams([key])[key]) in (b"0", b"False")
