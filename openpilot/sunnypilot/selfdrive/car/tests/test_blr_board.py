"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-RELAY): the BLR-v1 brake-lamp board's data on the comma side.

The board's identity and health cross the same five boundaries as the EPS-LKAS board's (test_gateway_board_settings.py
says why each one is a place a mistake goes silent): a DBC in the submodule, a parser registration, a capnp schema, a
dataclass that must mirror it field for field, and the params registry - and then card's writes, which the offroad
Settings > gateway page reads. These are the twins of that file's tests, for the second board, plus what is new here:

  * BlrBoardParams (blr_board.py) against real Params: what is written, when, and in which shape - the shapes are
    the contract with the page;
  * NOTHING ON A CONTROLS PATH READS THE BOARD (DESIGN E.7). brakeLampRelay is named in the schema and in
    blr_board.py and nowhere else in openpilot, and no Blr* param is read by selfdrived, controls, MADS or pandad.
    opendbc's own test_blr.py holds the other half: CarState is byte-identical with and without the board's frames.

The board's bytes are the firmware's golden vector (opendbc_repo/opendbc/sunnypilot/car/honda/fixtures/
blr_golden_frames.json, a byte-for-byte copy of the board repo's blr/tools/golden_frames.json, blr-v1 31c138f).
"""
import ast
import json
import math
import re
import struct
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from opendbc.can.parser import CANParser
from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.car import blr_board
from openpilot.sunnypilot.selfdrive.car.blr_board import BlrBoardParams, LAST_DRIVE_MIN_S, SEEN_AT_S

ROOT = Path(__file__).parents[4]      # openpilot/
REPO = ROOT.parent                    # the repo root, which holds opendbc_repo/
OPENDBC = REPO / "opendbc_repo/opendbc"
CAR_DBC = "honda_accord_au_2015_can_generated"   # includes generator/honda/_sunnypilot_blr.dbc
CARSTATE = OPENDBC / "car/honda/carstate.py"
CARSTATE_EXT = OPENDBC / "sunnypilot/car/honda/carstate_ext.py"
STRUCTS = OPENDBC / "car/structs.py"
GOLDEN = OPENDBC / "sunnypilot/car/honda/fixtures/blr_golden_frames.json"
CUSTOM_CAPNP = ROOT / "cereal/custom.capnp"
PARAMS_KEYS = ROOT / "common/params_keys.h"
CARD = ROOT / "selfdrive/car/card.py"
BLR_BOARD = ROOT / "sunnypilot/selfdrive/car/blr_board.py"

TELEMETRY = ("BLR_STATUS", "BLR_ANALOG", "BLR_HEALTH", "BLR_COUNTERS", "BLR_COUNTERS2", "BLR_VERSION", "BLR_BUILD")

# DESIGN E.2, every one: name -> (lifetime, type)
PARAMS = {
  "BlrBoardVersion": ("PERSISTENT", "STRING"),
  "BlrBoardBuild": ("PERSISTENT", "JSON"),
  "BlrBoardSeenAt": ("PERSISTENT", "STRING"),
  "BlrLastDrive": ("PERSISTENT", "JSON"),
  "BlrFlashTrace": ("PERSISTENT", "JSON"),
  "BlrFlashRequested": ("CLEAR_ON_MANAGER_START", "BOOL"),
  "BlrFlashProgress": ("CLEAR_ON_MANAGER_START", "STRING"),
  "BlrFlashState": ("CLEAR_ON_MANAGER_START", "STRING"),
  "BlrFlashPhase": ("CLEAR_ON_MANAGER_START", "JSON"),
  "BlrFlashCancel": ("CLEAR_ON_MANAGER_START", "BOOL"),
}

# CarStateSP.BrakeLampRelay, in order. card splats the dataclass into custom.CarStateSP.new_message(**dict).
FIELDS = ["valid", "state", "outDrive", "wouldOn", "request", "cmdFresh", "variant", "faults", "latchReason",
          "driverBraking", "extSink", "relaySnsMv", "lampSnsMv", "vbatSnsMv", "fwValid", "fwGitHash", "fwDirty",
          "bootPresent", "bootGitLo24", "boardUid24", "optBorEn", "buildValid", "optDualBank"]


def golden() -> dict[str, dict]:
  with open(GOLDEN, encoding="utf-8") as f:
    return {fr["tag"]: fr for fr in json.load(f)["frames"]}


def code_of(path: Path) -> str:
  return "\n".join(line.split("#", 1)[0] for line in path.read_text(encoding="utf-8").splitlines())


class TestBlrBoundaries(unittest.TestCase):
  def test_dbc_decodes_a_real_board_frame(self):
    """The byte order is the whole test, as for GW_VERSION: the board packs LSB first, and a hash decoded the other
    way round is a plausible number that matches no commit. Decoded with the parser card runs, on the car's DBC."""
    g = golden()

    def decode(version: bytes):
      cp = CANParser(CAR_DBC, [("BLR_VERSION", math.nan), ("BLR_BUILD", math.nan), ("BLR_STATUS", math.nan)], 0)  # ty: ignore[invalid-argument-type]
      frames = [(0x627, version, 0), (0x62F, bytes.fromhex(g["build"]["hex"]), 0),
                (0x620, bytes.fromhex(g["status_active"]["hex"]), 0)]
      cp.update([(int(1e9), frames)])
      return cp.vl

    version = bytes.fromhex(g["version"]["hex"])
    vl = decode(version)
    self.assertEqual(f"{int(vl['BLR_VERSION']['GIT_HASH']):08x}", "883a3cd1", "GIT_HASH byte order is wrong")
    self.assertEqual(int(vl["BLR_VERSION"]["VARIANT"]), 3)
    self.assertEqual(int(vl["BLR_VERSION"]["BOARD_CLASS"]), 2)
    self.assertEqual(int(vl["BLR_BUILD"]["BOARD_UID24"]), g["build"]["signals"]["BOARD_UID24"], "the UID is 24-bit LE")
    self.assertEqual(int(vl["BLR_BUILD"]["BOOT_GIT_LO24"]), g["build"]["signals"]["BOOT_GIT_LO24"])
    for name, want in g["status_active"]["signals"].items():
      self.assertAlmostEqual(vl["BLR_STATUS"][name], want, msg=f"BLR_STATUS.{name}")

    # negative control: the same hash packed MSB-first must not decode to it
    swapped = struct.pack(">I", struct.unpack("<I", version[:4])[0]) + version[4:]
    self.assertNotEqual(f"{int(decode(swapped)['BLR_VERSION']['GIT_HASH']):08x}", "883a3cd1")

  def test_frames_are_registered_liveness_exempt(self):
    """float("nan") is what stops a quiet lamp board costing openpilot its CAN."""
    src = CARSTATE.read_text()
    for name in TELEMETRY:
      assert re.search(rf'\("{name}",\s*float\("nan"\)\)', src), f"{name} is not registered liveness-exempt"

  def test_capnp_and_dataclass_agree(self):
    """A name on one side only raises when card publishes, i.e. on a drive."""
    capnp_src = CUSTOM_CAPNP.read_text()
    assert re.search(r"^  brakeLampRelay @6 :BrakeLampRelay;", capnp_src, re.M), "CarStateSP.brakeLampRelay @6"
    block = capnp_src[capnp_src.index("struct BrakeLampRelay {"):]
    block = block[:block.index("\n  }")]
    capnp_fields = [(m.group(1), int(m.group(2))) for m in re.finditer(r"^\s+(\w+) @(\d+) :\w+;", block, re.M)]
    self.assertEqual([f for f, _ in capnp_fields], FIELDS)
    self.assertEqual([o for _, o in capnp_fields], list(range(len(FIELDS))), "ordinals must be unique and contiguous")

    structs_src = STRUCTS.read_text()
    assert re.search(r"^  brakeLampRelay: 'CarStateSP\.BrakeLampRelay' = field\(", structs_src, re.M)
    sblock = structs_src[structs_src.index("class BrakeLampRelay:"):]
    sblock = sblock[:sblock.index("\n\n")] if "\n\n" in sblock else sblock
    self.assertEqual(re.findall(r"^\s+(\w+): \w+ = auto_field\(\)", sblock, re.M), FIELDS)

    # and it round-trips through card's own converter
    from opendbc.car import structs
    from openpilot.selfdrive.car.helpers import convert_to_capnp
    c = structs.CarStateSP()
    c.brakeLampRelay.fwGitHash, c.brakeLampRelay.boardUid24, c.brakeLampRelay.faults = 0x883A3CD1, 0xA5C3E1, 0xA0
    c.brakeLampRelay.valid = c.brakeLampRelay.optDualBank = True
    r = convert_to_capnp(c).as_reader().brakeLampRelay
    assert (r.fwGitHash, r.boardUid24, r.faults, r.valid, r.optDualBank) == (0x883A3CD1, 0xA5C3E1, 0xA0, True, True)

  def test_decoder_exists_and_is_called(self):
    src = CARSTATE_EXT.read_text()
    assert "def _update_blr(self, ret_sp: structs.CarStateSP, cp: CANParser)" in src
    assert "self._update_blr(ret_sp, cp)" in src, "the decoder is never called"
    for name in ("BLR_STATUS", "BLR_ANALOG", "BLR_VERSION", "BLR_BUILD"):
      assert f'cp.ts_nanos["{name}"]' in src, f"{name}: absence must be told from a zero"

  def test_params_are_registered(self):
    keys = PARAMS_KEYS.read_text()
    for key, (lifetime, kind) in PARAMS.items():
      m = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}', keys)
      assert m, f"{key} is not in params_keys.h; Params would raise UnknownKeyName"
      flags = m.group(1)
      assert lifetime in flags and kind in flags, f"{key}: {flags!r}, want {lifetime} {kind}"
      assert "BACKUP" not in flags, f"{key} describes one car's board, not a setting to restore elsewhere"

  def test_card_writes_from_params_thread_never_the_control_loop(self):
    """A Params put is a blocking write with two fsyncs; state_publish is the 100 Hz loop."""
    tree = ast.parse(CARD.read_text())
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def calls(fn):
      return {ast.unparse(c.func) for c in ast.walk(fns[fn]) if isinstance(c, ast.Call)}

    assert "self.blr_board.write" in calls("params_thread"), "BlrBoardParams.write is not on params_thread"
    assert "self.blr_board.stage" in calls("state_publish"), "nothing stages the board's identity"
    for fn in ("state_publish", "state_update", "controls_update", "step", "__init__"):
      assert "self.blr_board.write" not in calls(fn), f"the blocking write is on {fn}"
    assert "BlrBoardParams(self.params)" in CARD.read_text()

    # BlrBoardParams: stage() does no I/O; every put is in _write()
    tree = ast.parse(BLR_BOARD.read_text())
    puts = {}
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
      n_puts = sum(1 for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and c.func.attr == "put")
      if n_puts:
        puts[fn.name] = n_puts
    self.assertEqual(set(puts), {"_write"}, puts)

  def test_json_params_are_given_objects_not_strings(self):
    """Params.put has (dict, JSON) and (list, JSON) and no (str, JSON): a json.dumps here raises TypeError on a drive."""
    keys = PARAMS_KEYS.read_text()
    json_keys = {m.group(1) for m in re.finditer(r'\{"(\w+)",\s*\{[^}]*JSON', keys)}
    assert {"BlrBoardBuild", "BlrLastDrive", "BlrFlashTrace", "BlrFlashPhase"} <= json_keys
    const = {blr_board.BUILD_PARAM: "BlrBoardBuild", blr_board.LAST_DRIVE_PARAM: "BlrLastDrive"}
    assert set(const) == {"BlrBoardBuild", "BlrLastDrive"}
    tree = ast.parse(BLR_BOARD.read_text())
    for node in ast.walk(tree):
      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "put" and node.args:
        key = node.args[0]
        if isinstance(key, ast.Name) and key.id in ("BUILD_PARAM", "LAST_DRIVE_PARAM"):
          value = node.args[1]
          assert not (isinstance(value, ast.Call) and ast.unparse(value.func).endswith("dumps")), f"line {node.lineno}"
          assert not (isinstance(value, ast.Constant) and isinstance(value.value, str)), f"line {node.lineno}"

  def test_card_emits_both_flash_traces(self):
    tree = ast.parse(CARD.read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "log_flash_trace")
    src = ast.unparse(fn)
    assert "BlrFlashTrace" in src and "EpsLkasFlashTrace" in src
    assert "_flash_trace_done" in src and "remove" in src


class TestNothingOnAControlsPathReadsTheBoard(unittest.TestCase):
  """DESIGN E.7: no BLR signal feeds brakePressed, brakeLights, any controls decision or panda safety."""

  def test_only_blr_board_reads_the_struct(self):
    allowed = {"cereal/custom.capnp", "sunnypilot/selfdrive/car/blr_board.py",
               "sunnypilot/selfdrive/car/tests/test_blr_board.py"}
    # the source tree as git sees it (tracked, plus new files not ignored): the build products generated from
    # custom.capnp (cereal/gen, jotpluggler's extractors) name every field and read none of them
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", "openpilot"], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    found = set()
    for rel in out.splitlines():
      path = REPO / rel
      if path.suffix in (".py", ".cc", ".h", ".capnp", ".pyx") and path.is_file():
        if re.search(r"brakeLampRelay|BrakeLampRelay", path.read_text(encoding="utf-8", errors="replace")):
          found.add(path.relative_to(ROOT).as_posix())
    assert found <= allowed, f"carStateSP.brakeLampRelay is read in {sorted(found - allowed)}: review it against DESIGN E.7"
    assert found >= {"cereal/custom.capnp", "sunnypilot/selfdrive/car/blr_board.py"}
    assert "brakeLampRelay" not in code_of(CARD), "card hands CS_SP to BlrBoardParams and reads nothing itself"

  def test_no_blr_param_on_a_controls_path(self):
    """No Blr* param and no brakeLampRelay field in selfdrived, controls, MADS, the C++ pandad that sets the panda's
    safety mode, opendbc's car code or panda safety. Code only: a comment saying why is not a read."""
    controls = [(ROOT / "selfdrive/controls", (".py",)), (ROOT / "selfdrive/selfdrived", (".py",)),
                (ROOT / "selfdrive/pandad", (".cc", ".h")), (ROOT / "sunnypilot/selfdrive/controls", (".py",)),
                (ROOT / "sunnypilot/selfdrive/selfdrived", (".py",)), (ROOT / "sunnypilot/mads", (".py",)),
                (ROOT / "selfdrive/car/cruise.py", (".py",)), (OPENDBC / "car", (".py",)),
                (OPENDBC / "safety", (".h", ".c", ".py"))]
    pattern = re.compile(r"\bBlr[A-Z]\w*|brakeLampRelay")
    checked = 0
    for top, suffixes in controls:
      for path in ([top] if top.is_file() else top.rglob("*")):
        if path.suffix not in suffixes or not path.is_file() or "tests" in path.parts or path == STRUCTS:
          continue   # STRUCTS: the dataclass itself
        text = code_of(path) if path.suffix == ".py" else path.read_text(encoding="utf-8", errors="replace")
        m = pattern.search(text)
        assert m is None, f"{path.relative_to(REPO)}: {m.group(0)!r} on a controls path"
        checked += 1
    assert checked > 100, checked


class FakeClock:
  """blr_board's `time`: monotonic() and time(), stepped in whole milliseconds so no float drift."""
  def __init__(self):
    self.ms = 0

  def monotonic(self):
    return 1000.0 + self.ms / 1000

  def time(self):
    return 1_760_000_000 + self.ms / 1000

  def advance(self, s):
    self.ms += round(s * 1000)


def cs(**blr):
  """carStateSP as card holds it: a capnp builder."""
  return custom.CarStateSP.new_message(brakeLampRelay=blr)


IDENTITY = {"fwValid": True, "fwGitHash": 0x883A3CD1, "fwDirty": False, "variant": 2}
BUILD = {"buildValid": True, "bootPresent": True, "bootGitLo24": 0xB28113, "boardUid24": 0xA5C3E1, "optBorEn": True,
         "optDualBank": True}


class BlockingParams(Params):
  """Params.put is asynchronous unless block=True, so a get straight after one may not see it. card need not wait;
  this test must. Everything else - the key registry and the type checks - is the real Params."""
  def put(self, key, dat, block=False):
    super().put(key, dat, block=True)


class TestBlrBoardParams(OpenpilotTestCase):
  def setUp(self):
    self.params = BlockingParams()
    self.clock = FakeClock()
    patcher = mock.patch.object(blr_board, "time", self.clock)
    patcher.start()
    self.addCleanup(patcher.stop)
    self.board = BlrBoardParams(self.params)

  def tick(self, CS_SP=None, n=1):
    """n of params_thread's 10 Hz ticks, each after one staged frame (card stages ten; the last one is what counts)."""
    for _ in range(n):
      if CS_SP is not None:
        self.board.stage(CS_SP)
      self.wall = int(self.clock.time())   # the wall clock this tick's write saw
      self.board.write()
      self.clock.advance(0.1)

  def get(self, key):
    return self.params.get(key)

  def test_nothing_is_written_before_the_board_speaks(self):
    self.tick(cs(), n=50)
    for key in ("BlrBoardVersion", "BlrBoardBuild", "BlrBoardSeenAt", "BlrLastDrive"):
      assert self.get(key) is None, key

  def test_identity_then_build_once_0x62f_arrives(self):
    self.tick(cs(**IDENTITY))
    assert self.get("BlrBoardVersion") == "883a3cd1"
    # absence is "unknown", never "no bootloader": only the 0x627 keys until 0x62F arrives
    assert self.get("BlrBoardBuild") == {"dirty": False, "variant": 2}
    self.tick(cs(**IDENTITY, **BUILD))
    assert self.get("BlrBoardBuild") == {"dirty": False, "variant": 2, "bootloader": True, "bootGit": "b28113",
                                         "uid": "a5c3e1", "borEn": True, "dualBank": True}

  def test_identity_is_written_on_change_only(self):
    with mock.patch.object(self.params, "put", wraps=self.params.put) as put:
      self.tick(cs(**IDENTITY, **BUILD), n=30)
      assert [c.args[0] for c in put.call_args_list] == ["BlrBoardVersion", "BlrBoardBuild"]
      self.tick(cs(**{**IDENTITY, "fwGitHash": 0x12345678}, **BUILD))
      assert self.get("BlrBoardVersion") == "12345678"

  def test_seen_at_means_heard_not_remembered(self):
    self.tick(cs(**IDENTITY))
    assert self.get("BlrBoardSeenAt") is None, "identity alone is latched; it is not the board talking now"
    self.tick(cs(**IDENTITY, valid=True))
    first = self.get("BlrBoardSeenAt")
    assert first == str(self.wall)
    self.tick(cs(**IDENTITY, valid=True), n=int(SEEN_AT_S * 10) - 5)
    assert self.get("BlrBoardSeenAt") == first, "at most once a minute"
    self.tick(cs(**IDENTITY, valid=True), n=10)
    assert int(self.get("BlrBoardSeenAt")) > int(first) + SEEN_AT_S - 1
    # the board falls silent half a minute later: SeenAt ends up at the last frame heard, then stops moving -
    # the identity stays latched and must not keep it alive
    self.tick(cs(**IDENTITY, valid=True), n=300)
    last_heard = self.wall
    self.tick(cs(**IDENTITY), n=int(SEEN_AT_S * 10) * 3)
    assert self.get("BlrBoardSeenAt") == str(last_heard)

  def test_last_drive_shape_and_ok(self):
    self.tick(cs(**IDENTITY, valid=True, state=1))
    rec = self.get("BlrLastDrive")
    assert rec == {"t": self.wall, "variant": 2, "faults": 0, "faultNames": [], "latch": 0,
                   "latchName": "", "wouldOn": False, "outDrive": False, "extSink": False}, rec

  def test_last_drive_accumulates_and_is_rate_limited(self):
    self.tick(cs(**IDENTITY, valid=True, state=1))
    t0 = self.get("BlrLastDrive")["t"]
    # a fault on one frame, gone on the next: the drive keeps it
    self.tick(cs(**IDENTITY, valid=True, state=3, faults=0x20, wouldOn=True))
    self.tick(cs(**IDENTITY, valid=True, state=1))
    assert self.get("BlrLastDrive")["faults"] == 0, "within a second of the last write: not yet"
    self.tick(cs(**IDENTITY, valid=True, state=1), n=int(LAST_DRIVE_MIN_S * 10))
    rec = self.get("BlrLastDrive")
    assert (rec["faults"], rec["faultNames"], rec["wouldOn"], rec["t"]) == (0x20, ["D_SENSE_MID"], True, t0), rec
    # a latch, and more faults: names in bit order, the latch by name
    self.tick(cs(**IDENTITY, valid=True, state=4, faults=0x81, latchReason=2, extSink=True), n=15)
    rec = self.get("BlrLastDrive")
    assert rec["faults"] == 0xA1 and rec["faultNames"] == ["F_U4_NOT_SINKING", "D_SENSE_MID", "F_FEEDBACK"], rec
    assert (rec["latch"], rec["latchName"], rec["extSink"]) == (2, "FEEDBACK", True), rec

  def test_a_stale_status_is_not_a_clean_one(self):
    """valid False carries zeros by construction (carstate_ext.py); even if it did not, it must not count."""
    self.tick(cs(**IDENTITY, valid=False, faults=0xFF, latchReason=1, outDrive=True), n=20)
    assert self.get("BlrLastDrive") is None
    self.tick(cs(**IDENTITY, valid=True), n=1)
    assert self.get("BlrLastDrive")["faults"] == 0

  def test_a_new_drive_starts_a_new_record(self):
    self.tick(cs(**IDENTITY, valid=True, faults=0x01))
    assert self.get("BlrLastDrive")["faults"] == 1
    # card restarts with the next drive: the old record stands until the board speaks
    self.board = BlrBoardParams(self.params)
    self.tick(cs(**IDENTITY), n=20)
    assert self.get("BlrLastDrive")["faults"] == 1
    self.tick(cs(**IDENTITY, valid=True))
    assert self.get("BlrLastDrive")["faults"] == 0

  def test_never_raises_into_card(self):
    self.board.stage(SimpleNamespace())               # no brakeLampRelay at all
    self.board.stage(cs(**IDENTITY, valid=True))
    with mock.patch.object(self.params, "put", side_effect=TypeError("boom")):
      self.board.write()
      self.board.write()


if __name__ == "__main__":
  unittest.main()
