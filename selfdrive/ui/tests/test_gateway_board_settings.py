"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The EPS-LKAS board's firmware identity crosses five boundaries between the wire
and the screen: a DBC in a submodule, a parser registration, a capnp schema, a
dataclass that must mirror it field for field, a params registry in C++, and a
raylib page. Four of those are in places where a mistake is silent -- a capnp
ordinal collision fails at compile time, but a dataclass field that does not
match its capnp twin raises only when card actually publishes it, on a drive.

This test is what keeps the copies honest.

Source is parsed, never imported, for the UI files: the panels pull in raylib,
which is not available in every test environment. The DBC and the capnp schema
ARE loaded, because that is the only way to prove the byte order is right -- and
the byte order is the one thing here that a reviewer cannot check by eye.
"""
import ast
import re
import struct
from pathlib import Path

ROOT = Path(__file__).parents[3]
DBC = ROOT / "opendbc_repo/opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc"
CARSTATE = ROOT / "opendbc_repo/opendbc/car/honda/carstate.py"
CARSTATE_EXT = ROOT / "opendbc_repo/opendbc/sunnypilot/car/honda/carstate_ext.py"
STRUCTS = ROOT / "opendbc_repo/opendbc/car/structs.py"
CUSTOM_CAPNP = ROOT / "cereal/custom.capnp"
PARAMS_KEYS = ROOT / "common/params_keys.h"
CARD = ROOT / "selfdrive/car/card.py"
BOARD_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/board.py"
MICI_SETTINGS = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/settings.py"

PARAMS = ["EpsLkasBoardVersion", "EpsLkasBoardBuild", "EpsLkasBoardSeenAt"]

# The seven fields added to LinbusGateway, in order. Order matters: card splats the
# dataclass into custom.CarStateSP.new_message(**dict).
FW_FIELDS = ["fwValid", "fwGitHash", "fwDirty", "fwAppSlot", "fwBootloader", "fwReadOnly",
             "boardUid", "fwBuildValid"]


def test_dbc_decodes_a_real_board_frame():
  """The byte order is the whole test.

  Every other message in this DBC is @0+ (big-endian) because they are single
  bytes, where it makes no difference. GW_VERSION's hash is 32 bits and the
  board packs it LSB-first, so it must be @1+. Getting this wrong produces a
  plausible-looking hash that matches no commit, which is far worse than an
  obvious failure.
  """
  import cantools
  db = cantools.database.load_file(str(DBC), strict=True)

  # exactly the bytes gw_version_pack() puts on the wire for commit 0x52ca4732
  raw = struct.pack("<I", 0x52CA4732) + bytes([0x6E, 160, 0x79, 3])
  d = db.decode_message("GW_VERSION", raw)
  assert int(d["GIT_HASH"]) == 0x52CA4732, f"GIT_HASH decoded as {int(d['GIT_HASH']):#x}, byte order is wrong"
  assert int(d["AUTHORITY"]) == 160
  assert int(d["HOLD_FRAMES"]) == 0x79 & 0x1F

  # gw_build_pack(): flags, floor LE, lin_max, 24-bit uid LE, counter
  raw = bytes([0x06]) + struct.pack("<H", 5150) + bytes([160]) + struct.pack("<I", 0x3F2A10)[:3] + bytes([7])
  d = db.decode_message("GW_BUILD", raw)
  assert int(d["BUILD_DIRTY"]) == 0
  assert int(d["BUILD_APP_SLOT"]) == 1
  assert int(d["BUILD_BOOTLOADER"]) == 1
  assert int(d["EPS_FLOOR_CPH"]) == 5150, "the EPS floor is 16-bit little-endian"
  assert int(d["BOARD_UID"]) == 0x3F2A10, "the board UID is 24-bit little-endian"


def test_frames_are_registered_liveness_exempt():
  """float("nan") is what stops a 1/min identity frame costing openpilot its CAN."""
  src = CARSTATE.read_text()
  for name in ("GW_VERSION", "GW_BUILD"):
    assert re.search(rf'\("{name}",\s*float\("nan"\)\)', src), \
      f"{name} is not registered liveness-exempt in get_can_parsers()"


def test_capnp_and_dataclass_agree():
  """A mismatch here raises only when card publishes, i.e. on a drive."""
  capnp_src = CUSTOM_CAPNP.read_text()
  structs_src = STRUCTS.read_text()

  block = capnp_src[capnp_src.index("struct LinbusGateway"):]
  block = block[:block.index("\n  }")]
  capnp_order = [m.group(1) for m in re.finditer(r"^\s+(\w+) @\d+ :", block, re.M)]

  sblock = structs_src[structs_src.index("class LinbusGateway"):]
  sblock = sblock[:sblock.index("\n  @")] if "\n  @" in sblock else sblock[:3000]
  struct_order = [m.group(1) for m in re.finditer(r"^\s+(\w+): \w+ = auto_field\(\)", sblock, re.M)]

  for f in FW_FIELDS:
    assert f in capnp_order, f"{f} missing from cereal/custom.capnp"
    assert f in struct_order, f"{f} missing from opendbc structs.py LinbusGateway"

  ci = [capnp_order.index(f) for f in FW_FIELDS]
  si = [struct_order.index(f) for f in FW_FIELDS]
  assert ci == sorted(ci) and si == sorted(si), "field ORDER differs between capnp and the dataclass"

  # ordinals must be unique and contiguous from 19 -- a reused ordinal is a
  # silent field collision, not a compile error, if the types happen to match
  ordinals = [int(m.group(1)) for m in re.finditer(r"@(\d+) :", block)]
  assert len(ordinals) == len(set(ordinals)), f"duplicate capnp ordinal in LinbusGateway: {ordinals}"


def test_decoder_exists_and_is_called():
  src = CARSTATE_EXT.read_text()
  assert "def _update_linbus_firmware" in src
  assert "self._update_linbus_firmware(ret_sp, cp)" in src, "the decoder is never called"
  # ts_nanos != 0 is the "have we ever seen this frame" test; without it an
  # absent GW_BUILD would read as all-flags-false, which is indistinguishable
  # from a board that really has no bootloader.
  assert 'cp.ts_nanos["GW_VERSION"]' in src
  assert 'cp.ts_nanos["GW_BUILD"]' in src


def test_params_are_registered():
  registered = set(re.findall(r'\{"(\w+)",\s*\{', PARAMS_KEYS.read_text()))
  for key in PARAMS:
    assert key in registered, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"

  card = CARD.read_text()
  assert "def write_board_firmware" in card
  for key in PARAMS:
    assert key in card, f"card.py never writes {key}"


def test_json_params_are_given_objects_not_strings():
  """THE BUG THIS EXISTS FOR. Params.put looks up PYTHON_2_CPP[(type(v), keytype)],
  and the only JSON entries are (dict, JSON) and (list, JSON). Passing a
  pre-serialised str to a JSON-typed key raises TypeError - and on the card path
  nothing catches it, so card died on the first drive that decoded a 0x707 and
  kept dying a minute after every restart."""
  keys = PARAMS_KEYS.read_text()
  json_keys = {m.group(1) for m in re.finditer(r'\{"(\w+)",\s*\{[^}]*JSON', keys)}
  assert "EpsLkasBoardBuild" in json_keys, "EpsLkasBoardBuild is no longer JSON-typed"

  tree = ast.parse(CARD.read_text())
  for node in ast.walk(tree):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "put" and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in json_keys):
      value = node.args[1]
      bad = (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
             and value.func.attr == "dumps")
      assert not bad, (f"card.py line {node.lineno}: {node.args[0].value} is JSON-typed; "
                       f"pass the object, not json.dumps(...) - Params serialises it")
      assert not (isinstance(value, ast.Constant) and isinstance(value.value, str)),         f"card.py line {node.lineno}: {node.args[0].value} is JSON-typed but given a str"


def test_board_firmware_is_not_written_from_the_control_loop():
  """A Params put is a blocking write with two fsyncs. state_publish runs at
  100 Hz; params_thread exists at 10 Hz for exactly this."""
  tree = ast.parse(CARD.read_text())
  writer = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "write_board_firmware")
  # and it must be called from params_thread, not from state_publish
  pt = next(n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "params_thread")
  called = {c.func.attr for c in ast.walk(pt)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
  assert "write_board_firmware" in called, "the param write is not on params_thread"

  sp = next(n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "state_publish")
  sp_calls = {c.func.attr for c in ast.walk(sp)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
  assert "write_board_firmware" not in sp_calls,     "the blocking write is back on the 100 Hz control loop"
  assert writer is not None


def test_panel_is_reachable():
  """An unreferenced page is the same as no page at all."""
  settings = MICI_SETTINGS.read_text()
  assert "BoardLayoutMici" in settings, "the gateway page is never constructed"
  assert re.search(r"items\.insert\(\d+,\s*board_btn\)", settings), \
    "board_btn is built but never inserted into the scroller"
  # the base list is re-added through add_widget, which re-wraps touch callbacks
  assert "self._scroller.add_widget(item)" in settings

  panel = BOARD_PANEL.read_text()
  # add_widgets lives on the inner _Scroller; self.add_widgets would AttributeError.
  # Comments are stripped first -- the panel explains this trap in prose, and a
  # naive substring search matches the explanation.
  code = "\n".join(line.split("#", 1)[0] for line in panel.splitlines())
  assert "self._scroller.add_widgets(" in code
  assert "self.add_widgets(" not in code


def test_panel_reads_params_on_a_tick_not_every_frame():
  """ui_state.params has no cache: a per-frame get is a file read 60x/second."""
  panel = BOARD_PANEL.read_text()
  assert "REFRESH_S" in panel
  assert "_update_state" in panel
  assert "time.monotonic() - self._updated > REFRESH_S" in panel


def test_panel_says_last_seen_not_live():
  """card is only_onroad and the frame is 1/min: nothing here is live, and the
  page must not imply otherwise."""
  panel = BOARD_PANEL.read_text()
  assert "last seen" in panel
  assert "def board_last_seen" in panel


# ---------------------------------------------------------------------------
# The update button. Source-level, like the rest of this file: the panel pulls
# in raylib, which is not available in every test environment.

FLASH_PARAMS = ["EpsLkasFlashRequested", "EpsLkasFlashProgress", "EpsLkasFlashState"]


def test_flash_params_are_registered():
  registered = set(re.findall(r'\{"(\w+)",\s*\{', PARAMS_KEYS.read_text()))
  for key in FLASH_PARAMS:
    assert key in registered, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"

  # A request that survived a restart would be a request nobody made.
  entry = re.search(r'\{"EpsLkasFlashRequested",\s*\{([^}]*)\}', PARAMS_KEYS.read_text())
  assert entry and "CLEAR_ON_MANAGER_START" in entry.group(1)


def test_button_exists_and_is_on_the_page():
  panel = BOARD_PANEL.read_text()
  assert "class UpdateBoardButton(BigButton)" in panel
  code = "\n".join(line.split("#", 1)[0] for line in panel.splitlines())
  assert re.search(r"add_widgets\(\[.*_update_btn\]\)", code), \
    "the button is built but never added to the scroller"


def test_confirmation_exits_on_confirm():
  """With exit_on_confirm=False the full-screen dialog stays up forever on a
  536x240 screen, with no feedback and the button hidden behind it."""
  panel = BOARD_PANEL.read_text()
  assert "exit_on_confirm=True" in panel
  assert "BigConfirmationDialog(" in panel


def test_the_gate_is_rechecked_inside_the_confirm_callback():
  """A slide-to-confirm can sit open across an ignition event, and the dismiss
  animation adds most of a second on top. device.py makes the same point about
  engagement: "Check engaged again in case it changed while the dialog was
  open"."""
  panel = BOARD_PANEL.read_text()
  tree = ast.parse(panel)
  confirm = None
  for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "confirm":
      confirm = node
  assert confirm is not None, "no confirm callback found"
  calls = [n.func.attr for n in ast.walk(confirm)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
  assert "_can_update" in calls, "the confirm callback does not re-check the gate"
  assert "put_bool" in calls, "the confirm callback does not write the request param"


def test_enabled_state_is_imperative_and_never_mixed():
  """Widget.set_enabled has ONE slot and is a plain assignment with no save or
  restore anywhere, so mixing a callable and a bool means whichever ran last
  wins for the life of the UI process."""
  panel = BOARD_PANEL.read_text()
  code = "\n".join(line.split("#", 1)[0] for line in panel.splitlines())
  assert "set_enabled(lambda" not in code, "callable and imperative styles are mixed"
  # and it must actually be re-asserted on the tick, not set once in __init__
  tree = ast.parse(panel)
  refresh = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "refresh"
                 and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                         and c.func.attr == "set_enabled" for c in ast.walk(n)))
  assert refresh is not None


def test_a_refusal_is_explained_not_silent():
  """A dead button with no reason is the worst of both."""
  panel = BOARD_PANEL.read_text()
  assert "BigDialog(" in panel, "a blocked press says nothing"
  assert "needs SWD once" in panel
  assert "ignition off" in panel
  assert "use always offroad" in panel


def test_the_button_says_what_it_would_install():
  """Without this the card could not tell you there was anything to install:
  it showed the board's own hash, which is what the card beside it shows."""
  panel = BOARD_PANEL.read_text()
  assert "def bundled_firmware" in panel
  assert "up to date" in panel
  assert "no image" in panel
  # the offer must be compared against the board's version, not just displayed
  assert re.search(r"offer\s*==\s*version", panel), \
    "the bundled hash is read but never compared with the board's"


def test_the_ui_shares_the_marker_constants_with_the_flasher():
  """The screen and the bootloader must never disagree about where the marker
  is. Importing them is the only way to make that true by construction -- a
  second copy of 0x100 in this file is a copy that can drift."""
  panel = BOARD_PANEL.read_text()
  tree = ast.parse(panel)
  imported = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module and "eps_lkas_flasher" in node.module:
      imported |= {a.name for a in node.names}
  for name in ("APP_ID_MAGIC", "APP_ID_OFFSET", "EPS_LKAS_APPSLOT_BIN"):
    assert name in imported, f"{name} is not imported from the flasher"

  # and it must not define its own
  code = "\n".join(line.split("#", 1)[0] for line in panel.splitlines())
  assert not re.search(r"^APP_ID_OFFSET\s*=", code, re.M), "a second copy of the offset"
  assert not re.search(r"^APP_ID_MAGIC\s*=", code, re.M), "a second copy of the magic"


def test_bundled_hash_read_agrees_with_the_full_parse():
  """The UI reads only the first 0x110 bytes. That shortcut has to give the
  same answer as parsing the whole image."""
  import importlib.util
  import struct as _struct
  spec = importlib.util.spec_from_file_location(
    "eps_lkas_flasher", ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_flasher.py")
  fl = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(fl)

  path = ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin"
  head = path.read_bytes()[:fl.APP_ID_OFFSET + 16]
  magic, _origin, git, _flags = _struct.unpack(
    "<4I", head[fl.APP_ID_OFFSET:fl.APP_ID_OFFSET + 16])
  assert magic == fl.APP_ID_MAGIC
  assert f"{git:08x}" == fl.image_identity(path.read_bytes())["git"]


def test_absence_and_a_negative_answer_are_different():
  """Firmware older than 2026-09-22 sends 0x707 and not 0x70F. Writing zeros
  for the fields it did not send made a real board on a real car show
  "board id 000000" and "no bootloader" where it should have said "unknown"
  and "pre-bootloader"."""
  card = CARD.read_text()
  tree = ast.parse(card)
  fn = next(n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "stage_board_firmware")
  # the build dict must be gated on fwBuildValid, not written unconditionally
  names = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
  assert "fwBuildValid" in names, "card writes the 0x70F fields without checking one arrived"

  # The UI collapses "never sent 0x70F" and "sent it, bootloader false" into
  # one message, and that is correct rather than lazy: 0x70F landed in the same
  # commit as the bootloader, so a board that does not send it genuinely has no
  # bootloader. What must NOT collapse is card's write - an absent frame must
  # not be recorded as a real answer of zero, which is what put "board id
  # 000000" on the screen.
  panel = BOARD_PANEL.read_text()
  assert "no bootloader" in panel


VEHICLE_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/vehicle.py"


def test_hand_positioned_labels_never_wrap():
  """A wrapped header silently overprints the value beneath it.

  These cards hand-position four labels at fixed y offsets inside a 180 px
  box. UnifiedLabel sizes its rect ONCE at construction and set_text never
  re-measures, so a header that exceeds max_width wraps to two lines and eats
  the row below - the four labels then need ~236 px of a 180 px card, and all
  the driver sees is the last value.

  "board firmware" is 343 px against a 340 px limit. Three pixels.

  DeviceInfoLayoutMici, the card these were copied from, passes wrap_text=False
  on all four labels. HondaLearnedInfo copied it without the flag and board.py
  copied HondaLearnedInfo; this test stops that propagating any further.
  """
  for path in (BOARD_PANEL, VEHICLE_PANEL):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
      if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id == "UnifiedLabel"):
        kw = {k.arg for k in node.keywords}
        assert "wrap_text" in kw, (
          f"{path.name}:{node.lineno}: UnifiedLabel without wrap_text=False in a "
          f"hand-positioned card - a wrapped header overprints the row below it")
        val = next(k.value for k in node.keywords if k.arg == "wrap_text")
        assert isinstance(val, ast.Constant) and val.value is False, \
          f"{path.name}:{node.lineno}: wrap_text must be False here"


def test_no_glyphs_the_baked_font_does_not_have():
  """The .fnt atlases carry ASCII 32-126 plus EXTRA_CHARS plus whatever the
  translations use. An em dash (U+2014) and a middle dot (U+00B7) are in none
  of those, and raylib silently substitutes a wrong glyph - so the card read
  "no _ needs SWD once" on the device. The bullet (U+2022) and en dash
  (U+2013) ARE in EXTRA_CHARS and are safe."""
  extra = re.search(r'EXTRA_CHARS\s*=\s*"([^"]*)"',
                    (ROOT / "selfdrive/assets/fonts/process.py").read_text(encoding="utf-8")).group(1)
  allowed = set(map(chr, range(32, 127))) | set(extra)
  for path in (BOARD_PANEL, VEHICLE_PANEL):
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
      for m in re.finditer(r'tr\("([^"]*)"\)', line):
        bad = sorted({c for c in m.group(1) if c not in allowed})
        assert not bad, (f"{path.name}:{i}: {[hex(ord(c)) for c in bad]} is not in the "
                         f"baked font; raylib will substitute a wrong glyph")


def test_the_button_title_leaves_room_for_its_sub_label():
  """BigButton gives the sub-label whatever the title does not use. "update
  firmware" is 370 px at 48 pt against a 322 px content width, so it wrapped to
  two lines and left ~33 px for a 42 px sub-label line - which was then force
  elided. The title has to fit one line."""
  panel = BOARD_PANEL.read_text()
  m = re.search(r'super\(\)\.__init__\(tr\("([^"]*)"\)', panel)
  assert m, "could not find the button title"
  assert len(m.group(1)) <= 10,     f'button title "{m.group(1)}" is long enough to wrap and squeeze out the sub-label'


def test_up_to_date_disables_the_button():
  """A live button under a success message is an invitation to reflash the
  gateway that steers the car for no reason. If the board reports the hash we
  would install, it is running that image and there is nothing to recover."""
  panel = BOARD_PANEL.read_text()
  tree = ast.parse(panel)
  fn = next(n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_can_update")
  src = ast.get_source_segment(panel, fn)
  assert "up to date" in src, "'up to date' is not a refusal, so the button stays enabled"
  # and it must be decided before ignition/offroad, which are less useful to say
  assert src.index("up to date") < src.index("ignition off"), \
    "'ignition off' would mask 'up to date' at a parked car"
