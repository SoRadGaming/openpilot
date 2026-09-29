"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): Settings > maps on the small screen, and the weekly OSM
refresh in mapd_manager.

Source is parsed, never imported, for the UI files: the panels pull in raylib,
which is not available in every test environment (the same approach as
test_gateway_board_settings.py). The refresh decision itself is unit-tested in
sunnypilot/mapd/tests/test_osm_auto_update.py.
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
PARAMS_KEYS = ROOT / "common/params_keys.h"
MAPS_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/maps.py"
MICI_SETTINGS = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/settings.py"
MAPD_MANAGER = ROOT / "sunnypilot/mapd/mapd_manager.py"
APPLICATION = ROOT / "system/ui/lib/application.py"


def code_only(src: str) -> str:
  """Comments stripped: the panels explain their traps in prose, and a naive
  substring search matches the explanation."""
  return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def function(tree: ast.AST, name: str) -> ast.FunctionDef:
  return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def attr_calls(node: ast.AST) -> list[str]:
  return [c.func.attr for c in ast.walk(node) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]


class TestMapsSettings(unittest.TestCase):
  def test_params_are_registered(self):
    keys = PARAMS_KEYS.read_text()
    m = re.search(r'\{"OsmAutoUpdateWeekly",\s*\{([^}]*)\}\}', keys)
    assert m, "OsmAutoUpdateWeekly is not in params_keys.h; Params would raise UnknownKeyName"
    flags = m.group(1)
    assert "PERSISTENT" in flags and "BACKUP" in flags and "BOOL" in flags
    assert '"1"' in flags, "the weekly refresh defaults to on"

    # the request param must not survive a restart: a request nobody made
    m = re.search(r'\{"OsmDbUpdatesCheck",\s*\{([^}]*)\}\}', keys)
    assert m and "CLEAR_ON_MANAGER_START" in m.group(1)

    # the completion date must survive a restart, and not travel in a backup to another device
    m = re.search(r'\{"OsmLastCompleteDate",\s*\{([^}]*)\}\}', keys)
    assert m and "PERSISTENT" in m.group(1) and "STRING" in m.group(1)
    assert "BACKUP" not in m.group(1) and "CLEAR" not in m.group(1)

  def test_page_is_reachable_and_the_existing_rows_did_not_move(self):
    settings = MICI_SETTINGS.read_text()
    assert "MapsLayoutMici()" in settings, "the maps page is never constructed"
    inserts = re.findall(r"items\.insert\((\d+),\s*(\w+)\)", settings)
    order = [name for _, name in inserts]
    assert "maps_btn" in order, "maps_btn is built but never inserted into the scroller"
    # the vehicle and gateway rows are pinned by other tests and by muscle memory
    assert ("2", "vehicle_btn") in inserts and ("3", "board_btn") in inserts
    assert order.index("maps_btn") == order.index("board_btn") + 1, "maps goes right after the gateway insert"
    assert ("4", "maps_btn") in inserts, "maps must land directly after the gateway row"

  def test_every_fork_line_is_marked(self):
    """A fork hunk in an upstream file carries a FORK(SPEED-LIMIT) marker, so a
    sync can find it."""
    for path, needles in ((MICI_SETTINGS, ("maps import", "maps_panel = ", "items.insert(4, maps_btn)")),
                          (MAPD_MANAGER, ("import OsmAutoUpdater", "OsmAutoUpdater(", "auto_updater.update()",
                                          'params.remove("OsmLastCompleteDate")'))):
      lines = path.read_text().splitlines()
      for needle in needles:
        idx = next((i for i, line in enumerate(lines) if needle in line), None)
        assert idx is not None, f"{path.name}: {needle!r} not found"
        window = lines[max(0, idx - 3):idx + 1]
        assert any("FORK(SPEED-LIMIT)" in line for line in window), \
          f"{path.name}:{idx + 1}: {needle!r} has no FORK(SPEED-LIMIT) marker"

  def test_panel_uses_the_inner_scroller(self):
    code = code_only(MAPS_PANEL.read_text())
    assert "class MapsLayoutMici(NavScroller)" in code
    assert "self._scroller.add_widgets(" in code
    assert "self.add_widgets(" not in code
    # upstream's constructor style: NavWidget pops itself; a back_callback popped Settings too
    assert "back_callback" not in code

  def test_page_carries_card_button_and_toggle(self):
    code = code_only(MAPS_PANEL.read_text())
    assert re.search(r"add_widgets\(\[self\._osm_info,\s*self\._osm_update_btn,\s*self\._auto_update_toggle,"
                     + r"\s*self\._nsw_info,\s*self\._nsw_update_btn\]\)", code)  # FORK(NSW-ZONES): the second pair
    assert re.search(r'AUTO_UPDATE_PARAM\s*=\s*"OsmAutoUpdateWeekly"', code)
    assert re.search(r'BigParamControl\(tr\("[^"]*"\), AUTO_UPDATE_PARAM[,)]', code)

  def test_the_button_writes_the_same_request_as_the_osm_panel(self):
    panel = MAPS_PANEL.read_text()
    assert re.search(r'UPDATE_PARAM\s*=\s*"OsmDbUpdatesCheck"', panel)
    confirm = function(ast.parse(panel), "confirm")
    calls = attr_calls(confirm)
    assert "_can_update" in calls, "the confirm callback does not re-check the gate"
    assert "_busy" in calls, "a second confirm could re-request a download in flight"
    assert "put_bool" in calls, "the confirm callback does not write the request param"

  def test_the_gate_is_offroad_and_a_region(self):
    panel = MAPS_PANEL.read_text()
    src = ast.get_source_segment(panel, function(ast.parse(panel), "_can_update"))
    assert src is not None
    assert "is_offroad()" in src
    # OffroadMode reads offroad while driving: the ignition decides
    assert "ui_state.ignition" in src
    assert '"OsmLocationName"' in src
    assert "no region set" in src and "car must be parked" in src

  def test_confirmation_exits_and_warns_to_stay_on(self):
    """OSMDownloadLocations is a /dev/shm param: a reboot drops a download in progress."""
    panel = MAPS_PANEL.read_text()
    assert "BigConfirmationDialog(" in panel
    assert "exit_on_confirm=True" in panel
    confirm = ast.get_source_segment(panel, function(ast.parse(panel), "confirm"))
    assert confirm is not None and "keep the device on" in confirm

  def test_the_date_is_the_last_completed_download(self):
    """mapd_manager writes OsmDownloadedDate when the download is requested; the page shows the last completion."""
    panel = MAPS_PANEL.read_text()
    assert 'MapDataInfo(tr("osm maps"), tr("updated"), osm_status)' in panel
    assert re.search(r'COMPLETE_PARAM\s*=\s*"OsmLastCompleteDate"', panel)
    assert '"OsmDownloadedDate"' in panel
    # a request with no completion on record is shown, but not as a finished update
    osm_date = ast.get_source_segment(panel, function(ast.parse(panel), "osm_date"))
    assert osm_date is not None and 'tr("requested")' in osm_date and 'tr("updated")' in osm_date
    assert "elif last_request_incomplete():" in code_only(panel)

  def test_the_weekly_toggle_says_hotspots_count(self):
    """A phone hotspot is wi-fi and reads unmetered unless it is marked metered."""
    panel = MAPS_PANEL.read_text()
    assert "toggle_callback=self._on_auto_update_toggled" in panel
    cb = ast.get_source_segment(panel, function(ast.parse(panel), "_on_auto_update_toggled"))
    assert cb is not None and "hotspot" in cb and "metered" in cb

  def test_reads_params_on_a_tick_not_every_frame(self):
    panel = MAPS_PANEL.read_text()
    assert "REFRESH_S" in panel
    assert panel.count("time.monotonic() - self._updated > REFRESH_S") >= 2, "the card and the button both tick"

  def test_enabled_state_is_imperative_and_never_mixed(self):
    code = code_only(MAPS_PANEL.read_text())
    assert "set_enabled(lambda" not in code
    assert "set_enabled(ui_state" not in code
    refresh = [n for n in ast.walk(ast.parse(MAPS_PANEL.read_text()))
               if isinstance(n, ast.FunctionDef) and n.name == "refresh" and "set_enabled" in attr_calls(n)]
    assert refresh, "the button's enabled state is not re-asserted on the tick"

  def test_hand_positioned_labels_never_wrap(self):
    tree = ast.parse(MAPS_PANEL.read_text())
    labels = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "UnifiedLabel"]
    assert labels
    for node in labels:
      val = next((k.value for k in node.keywords if k.arg == "wrap_text"), None)
      assert isinstance(val, ast.Constant) and val.value is False, \
        f"maps.py:{node.lineno}: UnifiedLabel without wrap_text=False in a hand-positioned card"

  def test_headers_and_titles_fit(self):
    """Headers are 48 pt in a 340 px card ("board firmware", 14 chars, was 343 px);
    the button title shares its box with the sub-label and must stay on one line."""
    panel = MAPS_PANEL.read_text()
    cards = re.findall(r'MapDataInfo\(tr\("([^"]*)"\), tr\("([^"]*)"\)', panel)
    assert len(cards) == 2, cards  # FORK(NSW-ZONES): osm maps + nsw zones
    assert all(len(h) <= 12 for card in cards for h in card), cards
    titles = re.findall(r'super\(\)\.__init__\(tr\("([^"]*)"\)', panel)
    assert titles and all(len(t) <= 10 for t in titles), "an update button title is long enough to wrap"
    m = re.search(r'BigParamControl\(tr\("([^"]*)"\)', panel)
    assert m and len(m.group(1)) <= 18, "a toggle title over 18 chars drops to the smaller font"

  def test_no_glyphs_the_baked_font_does_not_have(self):
    extra = re.search(r'EXTRA_FONT_CHARS\s*=\s*"([^"]*)"', APPLICATION.read_text(encoding="utf-8")).group(1)
    allowed = set(map(chr, range(32, 127))) | set(extra)
    for path in (MAPS_PANEL, MICI_SETTINGS):
      src = path.read_text(encoding="utf-8")
      # tr() strings, and f-strings that are drawn (the date line)
      strings = re.findall(r'tr\("([^"]*)"\)', src) + re.findall(r'f"([^"]*)"', src)
      for s in strings:
        bad = sorted({c for c in s if c not in allowed})
        assert not bad, f"{path.name}: {s!r} has {[hex(ord(c)) for c in bad]}, not in the baked font"

  def test_mapd_manager_checks_before_it_acts(self):
    """The refresh sets OsmDbUpdatesCheck; update_osm_db() consumes it. The check
    must run first in the loop, or every refresh waits an extra tick for nothing
    and the order stops meaning what the comment says."""
    tree = ast.parse(MAPD_MANAGER.read_text())
    loop = next(n for n in ast.walk(function(tree, "main_thread")) if isinstance(n, ast.While))
    calls = [c for c in ast.walk(loop) if isinstance(c, ast.Call)]
    names = [c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "") for c in calls]
    lines = {name: c.lineno for name, c in zip(names, calls, strict=True)}
    assert "update" in names and "update_osm_db" in names
    update_line = next(c.lineno for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "update"
                       and isinstance(c.func.value, ast.Name) and c.func.value.id == "auto_updater")
    assert update_line < lines["update_osm_db"]



# FORK(NSW-ZONES) ------------------------------------------------------------------------------------------------------
class TestNswZonesSettings(unittest.TestCase):
  def test_params_are_registered(self):
    keys = PARAMS_KEYS.read_text()

    def flags(key):
      m = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}\}', keys)
      assert m, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
      return m.group(1)
    mode = flags("SpeedLimitNswZones")
    assert "PERSISTENT" in mode and "BACKUP" in mode and "INT" in mode and '"2"' in mode, "live by default"
    auto = flags("NswZonesAutoUpdate")
    assert "PERSISTENT" in auto and "BOOL" in auto and '"1"' in auto
    assert "CLEAR_ON_MANAGER_START" in flags("NswZonesUpdateCheck"), "a button press must not survive a restart"
    version = flags("NswZonesVersion")
    assert "PERSISTENT" in version and "BACKUP" not in version, "the installed version is this device's"

  def test_the_card_carries_the_attribution_and_the_data_date(self):
    panel = MAPS_PANEL.read_text()
    assert 'MapDataInfo(tr("nsw zones"), tr("data"), nsw_status)' in panel
    status = ast.get_source_segment(panel, function(ast.parse(panel), "nsw_status"))
    assert status is not None and "NSW_ATTRIBUTION" in status
    from openpilot.sunnypilot.mapd.nsw_zones import ATTRIBUTION
    assert "Transport for NSW" in ATTRIBUTION and "CC BY 4.0" in ATTRIBUTION and "Not endorsed" in ATTRIBUTION

  def test_the_button_writes_the_downloaders_request(self):
    panel = MAPS_PANEL.read_text()
    from openpilot.sunnypilot.mapd.nsw_zones.downloader import CHECK_PARAM
    assert CHECK_PARAM == "NswZonesUpdateCheck"
    tree = ast.parse(panel)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "UpdateNswButton")
    confirm = next(n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name == "confirm")
    calls = attr_calls(confirm)
    assert "_can_update" in calls and "_busy" in calls and "put_bool" in calls
    gate = ast.get_source_segment(panel, next(n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef)
                                              and n.name == "_can_update"))
    assert gate is not None and "is_offroad()" in gate and "ui_state.ignition" in gate

  def test_mapd_manager_wiring_is_marked(self):
    lines = MAPD_MANAGER.read_text().splitlines()
    for needle in ("import NswZoneMapData", "import NswZonesUpdater", "NswZoneMapData()", "NswZonesUpdater(",
                   "nsw_updater.update()"):
      idx = next((i for i, line in enumerate(lines) if needle in line), None)
      assert idx is not None, f"mapd_manager.py: {needle!r} not found"
      window = lines[max(0, idx - 6):idx + 1]
      assert any("FORK(NSW-ZONES)" in line for line in window), f"mapd_manager.py:{idx + 1}: {needle!r} is not marked"

  def test_off_is_upstreams_class(self):
    """Mode 0 constructs OsmMapData itself, not NswZoneMapData in a pass-through mode; the NSW modules are imported
    lazily (process_config imports mapd_manager at the manager's start)."""
    src = code_only(MAPD_MANAGER.read_text())
    assert "nsw_on = read_mode(params) != MODE_OFF" in src
    assert "nsw_map_sp if nsw_map_sp is not None else OsmMapData()" in src
    head = src[:src.index("def main_thread")]
    assert "nsw_zones" not in head and "nsw_map_data" not in head, "no NSW import at module scope"


if __name__ == "__main__":
  unittest.main()
