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
  # angle, column torque, and - since the second update showed the "hum" is
  # engine firing vibration - engine rpm and the accessory-load bit
  assert mod.TRACE_IDS == (0x156, 0x18F, 0x17C, 0x1A6)

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
  assert all(len(x) == 4 for x in series), "t, angle, column torque, rpm"


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


def _f18f(value: int, counter: int) -> bytes:
  """A 7-byte 0x18F with STEER_TORQUE_SENSOR = value and the given counter."""
  raw = (-value) & 0xFFFF
  return bytes([raw >> 8, raw & 0xFF, 0, 0, 0, 0, (counter & 3) << 4])


def _f17c(rpm: int) -> bytes:
  return bytes([0, 0, rpm >> 8, rpm & 0xFF, 0, 0, 0, 0])


def test_engine_rpm_comes_from_0x17c_not_0x158():
  """0x158's ENGINE_RPM field reads ~8% low at idle in Park on this car - it is
  the torque converter - and using it once produced the wrong conclusion that
  the vibration could not be the engine. Real f6 frame: 0x17C 0000037500000005
  is 885 rpm, where 0x158 read 810 at the same instant."""
  mod = _module()
  assert mod.ENGINE_RPM_ID == 0x17C
  assert mod.decode_engine_rpm(bytes.fromhex("0000037500000005")) == 885
  assert mod.decode_load_bit(bytes([0, 0, 0x40])) == 1
  assert mod.decode_load_bit(bytes([0, 0, 0x00])) == 0
  assert mod.steer_status_counter(_f18f(0, 2)) == 2
  assert mod.decode_steer_status(_f18f(-74, 0)) == -74


def test_the_eps_grid_ignores_usb_batching_and_keeps_dropped_frames_as_holes():
  """Receive times come in USB batches with ~10 ms of jitter, which scrambles a
  44 Hz fit. The grid is rebuilt from the frame counter at exactly 10 ms."""
  mod = _module()
  frames = []
  # five frames sent 10 ms apart but all RECEIVED in one USB batch - which
  # arrives when the last of them does, at 1.040 - then one lost (counter 1
  # never arrives), then two more in a later batch
  for k, c in enumerate((0, 1, 2, 3, 0)):
    frames.append((1.040, k, _f18f(k, c)))
  frames.append((1.070, 6, _f18f(6, 2)))        # counter 1 skipped: one frame lost
  frames.append((1.070, 7, _f18f(7, 3)))
  g = mod.eps_grid(frames)
  ts = [round(t - g[0][0], 3) for t, _ in g]
  assert ts == [0.0, 0.01, 0.02, 0.03, 0.04, 0.06, 0.07], ts


def test_the_firing_order_fit_finds_the_order_and_only_the_order():
  """The measurement the next update rests on. A 44 Hz vibration whose
  frequency follows a wandering idle must come out at order 3 and not at the
  off-order reference."""
  import math
  mod = _module()
  rpm = [(i * 0.01, 870 + int(20 * math.sin(i / 90))) for i in range(600)]
  ph = 0.0
  samples = []
  for i in range(600):
    t = i * 0.01
    ph += 2 * math.pi * 3 * rpm[i][1] / 60 * 0.01
    samples.append((t, 5.0 * math.cos(ph) + 0.3 * math.sin(i * 1.7)))
  o3 = mod.order_amplitude(samples, rpm, 3.0)
  ref = mod.order_amplitude(samples, rpm, 2.6)
  assert abs(o3 - 5.0) < 0.3, o3
  assert ref < 0.8, ref
  assert mod.order_amplitude(samples[:20], rpm, 3.0) is None, "too short to mean anything"


def test_the_phase_table_shows_which_step_the_vibration_starts_in():
  """End to end on a synthetic run: marks, rpm, column torque that only
  vibrates once the data stream starts. The table must put the step in the
  data phase and nowhere earlier - that is the whole point of the hold."""
  import math
  mod = _module()

  class T:
    def describe(self): return "replay"
    def send(self, *a): pass
    def poll(self, timeout=0.0): return []
    def close(self): pass

  f = mod.Flasher(T(), log=lambda s: None)
  marks = [(0.0, "start"), (5.0, "knock"), (5.3, "hello"), (5.5, "hold"),
           (13.5, "begin"), (14.8, "data"), (22.0, "finish"), (22.2, "reboot"),
           (23.0, "hello_after"), (29.0, "end")]
  f._marks = list(marks)
  f._health = [(t, {0: {"total_error_cnt": (7 if t >= 22.0 else 0)}}) for t, _ in marks]
  ph = 0.0
  for i in range(2900):
    t = i * 0.01
    ph += 2 * math.pi * 3 * 875 / 60 * 0.01
    amp = 5.0 if t >= 14.8 else 1.0
    f._trace.append((t, 0x18F, _f18f(int(round(amp * math.cos(ph))) * 1, i % 4)))
    f._trace.append((t, 0x17C, _f17c(875)))
    f._trace.append((t, 0x156, bytes([0xFF, 0xE7, 0, 0, 0, 0])))
  rows = {r["p"]: r for r in f.summarise_trace()["phases"]}
  assert list(rows) == ["pre", "reset", "enter", "hold", "erase", "data", "finish", "copy", "post"]
  assert rows["pre"]["o3"] < 2 and rows["hold"]["o3"] < 2
  assert rows["data"]["o3"] > 3.5
  assert rows["data"]["rpm"] == 875
  # errors counted between the data mark (14.8) and the finish mark (22.0)
  assert rows["data"].get("e0") == 7, "the error delta lands in the phase it happened in"
  assert rows["finish"].get("e0") == 0 and rows["pre"].get("e0") == 0


def test_the_hold_keeps_the_bootloader_alive_and_sits_before_the_data():
  """The bootloader resets after 10 s without a command. The hold pings well
  inside that, and it must come after INFO and before program() - otherwise it
  separates nothing."""
  mod = _module()
  assert mod.HOLD_PING_S * 2 < 10.0
  assert 3.0 <= mod.HOLD_BEFORE_DATA_S <= 9.0
  src = FLASHER.read_text()
  body = src[src.index("def run_flash"):]
  assert body.index("f.info()") < body.index("f.hold(") < body.index("f.program(image)")
  assert body.index("f.mark(\"knock\")") < body.index("f.knock()")
  assert body.index("f.wait_hello(25.0)") < body.index("f.listen(POST_CAPTURE_S)")


def test_panda_health_is_best_effort():
  mod = _module()
  t = mod.PandaTransport.__new__(mod.PandaTransport)

  class P:
    def can_health(self, b):
      if b == 1:
        raise RuntimeError("no bus 1")
      return {"total_error_cnt": 3, "bus_off_cnt": 0, "error_passive": False,
              "total_rx_lost_cnt": 0, "total_tx_lost_cnt": 0, "ignored": 9}
  t.p = P()
  h = t.health()
  assert set(h) == {0, 2}
  assert h[0]["total_error_cnt"] == 3 and "ignored" not in h[0]
