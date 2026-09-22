"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The EPS-LKAS flasher runs inside pandad's wrapper, in the one window where
nothing else owns the panda. That placement is what makes it useful and it is
also what makes it dangerous: anything that escapes from it takes the wrapper
with it, the pandad binary never starts, and the car has no CAN at all until
manager notices.

So the invariants this file pins are mostly about what the module must NOT do.
The protocol itself is proven against real hardware -- a spare board on a bench
candleLight, A to B to A with the slot CRC as the witness -- not here.
"""
import ast
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).parents[3]
FLASHER = ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_flasher.py"
IMAGE = ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin"

APP_ID_OFFSET = 0x100
APP_ID_MAGIC = 0x314C5041
APP_ORIGIN = 0x08004000


def _module():
  """Import the module without importing panda or python-can.

  Both transports import their library inside __init__, so the module itself is
  importable anywhere -- which is the point, and worth asserting by doing it.
  """
  import importlib.util
  spec = importlib.util.spec_from_file_location("eps_lkas_flasher", FLASHER)
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def test_module_imports_without_panda_or_python_can():
  mod = _module()
  assert mod.PROTO_VERSION == 1
  assert mod.ID_HOST == 0x710 and mod.ID_BOARD == 0x711 and mod.ID_DATA == 0x712


def test_no_hard_dependency_at_module_scope():
  """python-can is not in the lockfile and panda is not importable off-device."""
  tree = ast.parse(FLASHER.read_text())
  top = []
  for node in tree.body:
    if isinstance(node, ast.Import):
      top += [a.name.split(".")[0] for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.module:
      top.append(node.module.split(".")[0])
  for banned in ("can", "usb", "panda", "openpilot", "cereal"):
    assert banned not in top, f"{banned} is imported at module scope"


def test_nothing_raises_out_of_the_flasher():
  """SystemExit inherits BaseException and would slip past pandad's `except
  Exception`. The firmware repo's equivalent check raises it five times; this
  port must return strings instead."""
  tree = ast.parse(FLASHER.read_text())

  # The AST, not a substring search: this module explains at length WHY it must
  # not raise SystemExit, and a text search matches the explanation.
  guard_lines: set[int] = set()
  for node in tree.body:
    if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
      guard_lines = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))

  offences = []
  for node in ast.walk(tree):
    if getattr(node, "lineno", None) in guard_lines:
      continue
    if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
        and isinstance(node.exc.func, ast.Name) and node.exc.func.id == "SystemExit"):
      offences.append(f"raise SystemExit at line {node.lineno}")
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "exit" and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sys"):
      offences.append(f"sys.exit() at line {node.lineno}")
  assert not offences, f"these would slip past pandad's except Exception: {offences}"

  # run_flash is the entry point pandad will call: it must catch everything
  run_flash = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "run_flash")
  handlers = [h for n in ast.walk(run_flash) if isinstance(n, ast.Try) for h in n.handlers]
  assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception" for h in handlers), \
    "run_flash does not catch Exception"


def test_image_check_rejects_a_standalone_image():
  """The check that stops a standalone image being branched into 16 KB from its
  real reset handler."""
  mod = _module()

  good = IMAGE.read_bytes()
  assert mod.check_app_slot_image(good) is None, mod.check_app_slot_image(good)

  # same image with the marker's magic corrupted -- i.e. what a standalone
  # image looks like at that offset
  bad = bytearray(good)
  bad[APP_ID_OFFSET:APP_ID_OFFSET + 4] = struct.pack("<I", 0xD30D428B)
  assert "STANDALONE" in (mod.check_app_slot_image(bytes(bad)) or "")

  # linked for the wrong address
  wrong = bytearray(good)
  wrong[APP_ID_OFFSET + 4:APP_ID_OFFSET + 8] = struct.pack("<I", 0x08000000)
  assert "linked for" in (mod.check_app_slot_image(bytes(wrong)) or "")

  assert "not a firmware image" in (mod.check_app_slot_image(b"\x00" * 16) or "")
  assert mod.check_app_slot_image(b"\xff" * (200 * 1024)) is not None


def test_bundled_image_is_an_app_slot_image():
  assert IMAGE.exists(), "no bundled image to flash"
  d = IMAGE.read_bytes()
  magic, origin, _git, _flags = struct.unpack("<4I", d[APP_ID_OFFSET:APP_ID_OFFSET + 16])
  assert magic == APP_ID_MAGIC, f"marker is {magic:#010x}, not APL1"
  assert origin == APP_ORIGIN, f"linked for {origin:#010x}"
  sp, pc = struct.unpack("<II", d[0:8])
  assert 0x20000000 <= sp <= 0x20024000
  assert APP_ORIGIN < pc < APP_ORIGIN + 112 * 1024

  mod = _module()
  ident = mod.image_identity(d)
  assert re.fullmatch(r"[0-9a-f]{8}", ident["git"]), ident["git"]
  assert not ident["dirty"], \
    "the bundled image was built from an edited tree; 0x707 would report a commit it is not"


def test_crc16_matches_the_bootloader():
  """CRC-16/CCITT-FALSE. A wrong CRC here fails every one of the 182 chunks."""
  mod = _module()
  assert mod.crc16_ccitt(b"123456789") == 0x29B1     # the standard check value
  assert mod.crc16_ccitt(b"") == 0xFFFF


def test_elm327_param_is_not_zero():
  """In elm327 mode panda does `if (param == 0) set_can_mode(CAN_MODE_OBD_CAN2)`,
  which re-multiplexes bus 1 onto the OBD-II port. The default param is 0."""
  mod = _module()

  # Against opendbc's own header, NOT a literal. This assertion used to read
  # `== 15` and so locked in the bug it was supposed to prevent: 15 is
  # SAFETY_VOLKSWAGEN_MQB. A test that compares a constant to itself proves
  # only that somebody typed the same number twice.
  decl = (ROOT / "opendbc_repo/opendbc/safety/declarations.h").read_text()
  want = int(re.search(r"#define SAFETY_ELM327 (\d+)U", decl).group(1))
  assert mod.SAFETY_ELM327 == want,     f"safety mode is {mod.SAFETY_ELM327}, opendbc says elm327 is {want}"
  assert mod.ELM327_KEEP_NORMAL_CAN != 0
  src = FLASHER.read_text()
  assert "set_safety_mode(SAFETY_ELM327, ELM327_KEEP_NORMAL_CAN)" in src
  assert "cli=False" in src, "Panda(cli=True) prompts on stdin with several pandas attached"
  assert 'health().get("safety_mode")' in src,     "the safety mode is set but never read back, so a wrong one looks like a dead board"


def test_commands_are_padded_to_eight_bytes():
  """elm327 forwards only DLC 8. Every length check in the bootloader is a
  minimum, so padding is free."""
  src = FLASHER.read_text()
  assert src.count('ljust(8, b"\\x00")') >= 2, "both transports must pad"


def test_protocol_constants_match_the_firmware():
  mod = _module()
  assert (mod.CMD_ENTER, mod.CMD_INFO, mod.CMD_BEGIN) == (1, 2, 3)
  assert (mod.CMD_CHUNK, mod.CMD_DATA, mod.CMD_CEND) == (4, 5, 6)
  assert (mod.CMD_FINISH, mod.CMD_REBOOT, mod.CMD_ABORT) == (7, 8, 9)
  assert (mod.RSP_ACK, mod.RSP_NAK, mod.RSP_INFO, mod.RSP_HELLO) == (0x80, 0x81, 0x82, 0x83)
  assert mod.MAGIC == b"EPSBOOT"
  assert mod.CHUNK_BYTES == 256 and mod.DATA_PER_FRAME == 8
  assert mod.STATIONARY_CPH == 100
  assert mod.APP_ID_MAGIC == APP_ID_MAGIC and mod.APP_ORIGIN == APP_ORIGIN


def test_silence_is_not_motion():
  """A car with no powertrain traffic is a car with the ignition off, which is
  the normal bench case. Reading silence as motion would make the bench path
  refuse to run at all."""
  mod = _module()

  class Quiet:
    def describe(self): return "quiet"
    def send(self, *a): pass
    def poll(self, timeout=0.0): return []
    def close(self): pass

  f = mod.Flasher(Quiet(), log=lambda s: None)
  assert f.moving(listen_s=0.05) is False


def test_moving_is_detected_from_the_wire():
  mod = _module()

  class Moving:
    def describe(self): return "moving"
    def send(self, *a): pass
    def poll(self, timeout=0.0):
      return [(mod.ENGINE_DATA_ID, bytes([0x13, 0x88, 0, 0, 0, 0, 0, 0]), mod.RX)]
    def close(self): pass

  f = mod.Flasher(Moving(), log=lambda s: None)
  assert f.moving(listen_s=0.05) is True

  ok, msg = mod.run_flash(Moving(), IMAGE.read_bytes(), log=lambda s: None)
  assert not ok and "moving" in msg


def test_run_flash_returns_rather_than_raising():
  """A transport that explodes must produce a string, not an exception."""
  mod = _module()

  class Broken:
    def describe(self): raise RuntimeError("usb fell over")
    def send(self, *a): raise RuntimeError("usb fell over")
    def poll(self, timeout=0.0): raise RuntimeError("usb fell over")
    def close(self): pass

  ok, msg = mod.run_flash(Broken(), IMAGE.read_bytes(), log=lambda s: None)
  assert not ok
  assert "RuntimeError" in msg and "usb fell over" in msg


def _tracing_flasher(mod, frames):
  """A Flasher fed a fixed frame list, pumped until the transport is empty."""
  class T:
    def __init__(self): self.n = 0
    def describe(self): return "fake"
    def send(self, *a): pass
    def poll(self, timeout=0.0):
      self.n += 1
      return frames if self.n <= 3 else []
    def close(self): pass
  f = mod.Flasher(T(), log=lambda s: None)
  for _ in range(4):
    f._pump()
  return f


def test_the_steering_trace_captures_the_two_ids_that_answer_the_question(tmp_path):
  """The flash runs offroad with loggerd stopped, so nothing records it. A
  driver reported the wheel moving during an update, hands off, and the 47 s
  window had no data in it at all - while the panda was receiving the whole
  bus and _pump discarded it. These two IDs are the witness."""
  mod = _module()
  assert mod.TRACE_IDS == (0x156, 0x18F)

  f = _tracing_flasher(mod, [
    (0x156, bytes([0x12, 0x34, 0, 0, 0, 0, 0, 0]), mod.RX),   # STEERING_SENSORS
    (0x18F, bytes([0xAB, 0xCD, 0, 0, 0, 0, 0, 0]), mod.RX),   # STEER_STATUS
    (mod.ID_BOARD, bytes(8), mod.RX),
    (0x1FA, bytes(8), mod.RX),                                 # ordinary traffic
  ])
  assert len(f._trace) == 6, "both traced IDs, three pumps"
  # and the traced frames must NOT reach _rx, which is flow control and is
  # scanned linearly on every receive
  assert len(f._rx) == 3, "only the board's replies belong in _rx"

  path = f.save_trace(str(tmp_path))
  assert path is not None
  txt = Path(path).read_text()
  assert chr(92) + "n" not in txt, "literal backslash-n in the output"
  assert "t,addr,data" in txt
  assert "0x156" in txt and "0x18f" in txt
  assert "1234000000000000" in txt and "abcd000000000000" in txt
  assert len([ln for ln in txt.splitlines() if ln and not ln.startswith("#")]) == 7


def test_the_trace_is_bounded_and_never_costs_more_than_itself(tmp_path):
  """It runs inside pandad's wrapper. A full disk must lose the trace and
  nothing else, and a long session must not grow without limit."""
  mod = _module()
  assert mod.TRACE_MAX <= 30000, "an unbounded trace is a memory leak in pandad"

  f = _tracing_flasher(mod, [(0x156, bytes(8), mod.RX)])
  assert f._trace.maxlen == mod.TRACE_MAX

  assert f.save_trace("Z:/definitely/not/writable") is None
  # nothing captured -> nothing written, and no empty file left behind
  empty = _tracing_flasher(mod, [(0x1FA, bytes(8), mod.RX)])
  assert empty.save_trace(str(tmp_path)) is None
  assert not list(tmp_path.iterdir())


def test_run_flash_saves_the_trace_on_the_failure_paths_too():
  """A flash that went wrong is exactly when the trace is worth having, so the
  save has to be in a finally, not on the success path."""
  src = FLASHER.read_text()
  tree = ast.parse(src)
  run_flash = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "run_flash")
  tries = [n for n in ast.walk(run_flash) if isinstance(n, ast.Try)]
  assert any(
    any("save_trace" in ast.dump(stmt) for stmt in t.finalbody)
    for t in tries
  ), "save_trace is not in a finally block"


def _replay(mod, frames):
  """A Flasher with a trace already loaded, no transport activity."""
  class T:
    def describe(self): return "replay"
    def send(self, *a): pass
    def poll(self, timeout=0.0): return []
    def close(self): pass
  f = mod.Flasher(T(), log=lambda s: None)
  for t, addr, data in frames:
    f._trace.append((t, addr, data))
  return f


def test_the_steering_decoders_match_carstate():
  """Both are 16-bit big-endian signed in the first four bytes. These exact
  values were cross-checked against carState on route f1: the decode gives
  2.4-2.5 deg where carState.steeringAngleDeg gives 2.5, and -74..0 counts
  where carState.steeringTorque gives -74..0."""
  mod = _module()
  # STEER_ANGLE 7|16@0- scale -0.1  ->  raw -25 = 2.5 deg
  ang, rate = mod.decode_steering_sensors(bytes([0xFF, 0xE7, 0x00, 0x00, 0, 0]))
  assert abs(ang - 2.5) < 1e-9, ang
  assert rate == 0.0
  # STEER_TORQUE_SENSOR 7|16@0- scale -1  ->  raw 74 = -74 counts
  assert mod.decode_steer_status(bytes([0x00, 0x4A, 0, 0, 0, 0, 0])) == -74
  # sign both ways, and short frames refused rather than throwing
  assert mod.decode_steer_status(bytes([0xFF, 0xB6, 0, 0, 0, 0, 0])) == 74
  assert mod.decode_steering_sensors(b"") is None
  assert mod.decode_steer_status(b"") is None


def _synth(mod, span_deg, torque):
  frames = []
  for i in range(20):
    raw = int(round((span_deg if i > 10 else 0.0) / -0.1))
    frames.append((i * 0.01, 0x156,
                   bytes([(raw >> 8) & 0xFF, raw & 0xFF, 0, 0, 0, 0])))
    frames.append((i * 0.01, 0x18F,
                   bytes([((-torque) >> 8) & 0xFF, (-torque) & 0xFF, 0, 0, 0, 0, 0])))
  return _replay(mod, frames).summarise_trace()


def test_the_verdict_separates_a_hand_from_something_driving_the_column():
  """The whole reason the trace exists. A driver reported the wheel moving
  during a flash with their hands off it; angle alone cannot tell the two
  apart, angle against column torque can."""
  mod = _module()
  assert _synth(mod, 0.1, 50)["verdict"] == "wheel did not move"
  assert "something drove the column" in _synth(mod, 3.0, 80)["verdict"]
  assert "hand on the wheel" in _synth(mod, 3.0, 5000)["verdict"]


def test_the_summary_is_small_enough_to_log_and_empty_when_there_is_nothing():
  """It leaves the device inside cloudlog lines, so it has to stay small - and
  an empty summary must be falsy so the hook does not write an empty param."""
  mod = _module()
  assert _replay(mod, []).summarise_trace() == {}
  # a trace of nothing but untraced IDs is also nothing
  assert _replay(mod, [(0.0, 0x1FA, bytes(8))]).summarise_trace() == {}

  d = _synth(mod, 3.0, 80)
  assert d["angle_span"] == 3.0
  assert d["torque_absmax"] == 80
  series = d.pop("series")
  assert len(json.dumps(d)) < 400, "summary line is too big for a log"
  # decimated, not one entry per frame
  assert len(series) <= 20
  assert all(len(x) == 3 for x in series)


def test_run_flash_reports_the_trace_even_when_it_fails():
  """A flash that went wrong is exactly when the trace matters, and the hook
  cannot read it from a return value that never comes."""
  mod = _module()
  seen = []

  class Broken:
    def describe(self): raise RuntimeError("usb fell over")
    def send(self, *a): pass
    def poll(self, timeout=0.0): return []
    def close(self): pass

  ok, msg = mod.run_flash(Broken(), IMAGE.read_bytes(), log=lambda s: None,
                          trace_out=seen.append)
  assert not ok and "usb fell over" in msg
  assert seen == [{}], "trace_out must still be called, with an empty summary"

  # and a trace_out that throws must not change the outcome
  ok, msg = mod.run_flash(Broken(), IMAGE.read_bytes(), log=lambda s: None,
                          trace_out=lambda d: (_ for _ in ()).throw(ValueError("boom")))
  assert not ok and "usb fell over" in msg, "trace_out must not mask the result"
