"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Flash the EPS-LKAS gateway board over CAN, from the device, using the panda
that is already in the car.

A SELF-CONTAINED PORT of the bootloader protocol in inc/boot_proto.h. It
deliberately shares no code with the firmware repo's tools/can_update.py: that
tool is built around python-can, which is not in openpilot's lockfile and is not
something to add to an AGNOS rootfs, and it raises SystemExit on a bad image -
which would slip straight past pandad's `except Exception` and leave the car
with no CAN at all. Nothing here raises; every failure is a returned string.

THE TRANSPORT IS INJECTED, and that is the point. The protocol below is the
code that will run in the car, and it can be exercised today against a spare
board on a bench candleLight adapter without a panda anywhere near it. A port that is
only ever run in its final position is a port nobody has tested.

WHAT THE BOARD IS DOING WHILE THIS RUNS. Its relays are de-energised for the
whole session, which is the NC path: the car is wired straight through to the
camera on all four lines and the EPS and camera talk exactly as they do with no
board fitted. The steering is plain copper. That is why erasing its flash
mid-session is uneventful, and why the worst outcome of a failure here is a
board sitting in its bootloader with stock LKAS working.

Run it by hand:

    python -m sunnypilot.selfdrive.pandad.eps_lkas_flasher --dry-run
    python -m sunnypilot.selfdrive.pandad.eps_lkas_flasher --bench   # candleLight
"""
from __future__ import annotations

import os
import struct
import time
import zlib
from collections import deque
from collections.abc import Callable

# ---- inc/boot_proto.h. Change a number there, change it here. ---------------
PROTO_VERSION = 1
ID_HOST = 0x710
ID_BOARD = 0x711
ID_DATA = 0x712
MAGIC = b"EPSBOOT"

CMD_ENTER, CMD_INFO, CMD_BEGIN = 0x01, 0x02, 0x03
CMD_CHUNK, CMD_DATA, CMD_CEND = 0x04, 0x05, 0x06
CMD_FINISH, CMD_REBOOT, CMD_ABORT = 0x07, 0x08, 0x09

RSP_ACK, RSP_NAK, RSP_INFO, RSP_HELLO = 0x80, 0x81, 0x82, 0x83

CMD_NAMES = {1: "ENTER", 2: "INFO", 3: "BEGIN", 4: "CHUNK", 5: "DATA",
             6: "CEND", 7: "FINISH", 8: "REBOOT", 9: "ABORT"}
ERRS = {0: "none", 1: "command out of sequence", 2: "image too big for the slot",
        3: "CRC mismatch", 4: "flash erase/program failed",
        5: "the car is not stationary", 6: "chunk short - frames were lost",
        7: "protocol version mismatch"}

CHUNK_BYTES = 256
DATA_PER_FRAME = 8

# HELLO byte 2
HELLO_APP_OK, HELLO_APP_ID, HELLO_APP_DIRTY, HELLO_STAGED = 0x01, 0x02, 0x04, 0x08

# ---- inc/gw_app_id.h -------------------------------------------------------
APP_ID_OFFSET = 0x100
APP_ID_MAGIC = 0x314C5041          # "APL1" little-endian
APP_ORIGIN = 0x08004000
APP_SLOT_BYTES = 112 * 1024
APP_ID_DIRTY, APP_ID_READONLY, APP_ID_TEST = 0x01, 0x02, 0x04

# ---- the car ---------------------------------------------------------------
ENGINE_DATA_ID = 0x158

# THE FLASH IS THE ONE THING NOTHING WATCHES.
#
# It runs offroad, so loggerd is not running and nothing - not CAN, not
# cloudlog - is recorded. A driver reported the EPS humming and the wheel
# moving slightly during an update, with their hands off the wheel, and there
# was no way to check: the routes either side of the flash end and begin
# outside it, leaving a 47 s hole with no data in it at all.
#
# Meanwhile the panda was receiving the whole car bus the entire time - about
# 100,000 frames on bus 0 - and _pump threw every one of them away. These two
# carry the answer, and are the reason this trace exists:
#
#   0x156 STEERING_SENSORS  STEER_ANGLE (0.1 deg) + STEER_ANGLE_RATE, 100 Hz
#   0x18F STEER_STATUS      STEER_TORQUE_SENSOR (column torque), 100 Hz
#
# Together they also separate the two candidate causes without a scope: angle
# moving while column torque stays in the low hundreds means something drove
# the column, and torque in the thousands leading the angle means a hand.
#
# 0x1AB STEER_MOTOR_TORQUE does not exist on this car, and the EPS's own motor
# torque is reported only over the LKAS serial link - which the board stops
# mirroring the moment it enters its bootloader, and which is blind below
# about 20 km/h anyway. So this is the best available witness, not a
# second-best one.
TRACE_IDS = (0x156, 0x18F, 0x17C, 0x1A6)
TRACE_MAX = 24000          # ~2 min of both at 100 Hz; ~1 MB of raw bytes
TRACE_DIR = "/data/eps-lkas-trace"
TRACE_HZ = 10              # the summary series; the raw file keeps all 100 Hz

# WHAT THE SECOND UPDATE TAUGHT (2026-09-23, routes f5/f6 and a phone video).
#
# The "hum" during an update is not the EPS. It is the V6's firing vibration -
# the 3rd engine order, ~44 Hz at idle, and its 2nd harmonic ~88 Hz - which
# stepped up about 2.5x structurally (24x in the cabin audio) at the moment the
# update's data stream began, 1.7 s AFTER the knock, with the rpm unchanged. It
# then held until the engine was restarted. The column-to-body vibration ratio
# did not change, so the EPS neither makes it nor amplifies it; its torque
# sensor simply sits in a shaking column.
#
# So these two are traced as well, because the vibration only means anything
# at a known rpm and load:
#
#   0x17C POWERTRAIN_DATA  ENGINE_RPM, bytes 2-3. NOT the ENGINE_RPM field of
#                          0x158, which reads ~8% low on this car at idle in
#                          Park - that is the torque converter, not the crank,
#                          and using it once produced "it cannot be the engine".
#   0x1A6                  byte 2 bit 6: an accessory load that cycles about
#                          every 7 s with a 12 V dip and a sag in idle. The
#                          firing-order amplitude is only comparable at the
#                          same load state.
ENGINE_RPM_ID = 0x17C
LOAD_ID = 0x1A6

# The phases. Each gap exists to separate two events in time so that whatever
# the vibration follows is unambiguous. They cost ~19 s per update; the car is
# parked in bypass throughout, which is stock wiring.
PRE_CAPTURE_S = 4.0        # baseline, before the knock, on top of moving()'s 1 s
HOLD_BEFORE_DATA_S = 8.0   # bootloader session open, relays in bypass, no data
HOLD_PING_S = 2.0          # keep-alive; the bootloader resets after 10 s silent
POST_CAPTURE_S = 6.0       # after the post-reboot HELLO: app start and K2 re-split
EPS_PERIOD_S = 0.010       # 0x18F cadence - the grid the firing-order fit uses

# WHICH CAR MESSAGES DOES THE BURST DISTURB? The owner confirms this car has
# active noise cancellation and VCM active control engine mounts. The update
# left BOTH signatures: structural vibration up 2.5x (the mounts' job) and
# cabin sound up far more at 88 Hz (ANC's). Both are timed from engine speed,
# so one disturbance upstream - the data burst corrupting or starving the
# frames they depend on - would take out both. Error counters say whether the
# bus saw errors; this says which messages actually went missing, and whether
# anything started answering our frames.
CENSUS_OWN = range(0x700, 0x720)   # the board's telemetry and the bootloader protocol
CENSUS_MIN_HZ = 5.0                # ignore IDs too slow to rate over a few seconds
CENSUS_LOST = 0.8                  # report an ID below 80% of its pre-knock rate
CENSUS_MIN_PHASE_S = 1.0           # phases shorter than this rate too noisily

# Named for what is happening DURING the phase that starts at each mark.
PHASE_NAMES = {
  "start": "pre",          # parked, board running normally, relays split
  "knock": "reset",        # relays drop, board resets into the bootloader
  "hello": "enter",        # ENTER, INFO
  "hold": "hold",          # bootloader session open, nothing sent but pings
  "begin": "erase",        # BEGIN: the bootloader erases bank 2
  "data": "data",          # the chunk stream - ~770 frames/s on the car bus
  "finish": "finish",      # FINISH: CRC check
  "reboot": "copy",        # bootloader copies staging into the app slot
  "hello_after": "post",   # app starts, K1/K2 re-split
}


def _i16be(b: bytes, off: int) -> int:
  """Both signals are 16-bit big-endian signed in the first four bytes."""
  v = (b[off] << 8) | b[off + 1]
  return v - 65536 if v & 0x8000 else v


def decode_steering_sensors(d: bytes) -> tuple[float, float] | None:
  """0x156: STEER_ANGLE 7|16@0- (-0.1), STEER_ANGLE_RATE 23|16@0- (1).

  Checked against carState on route f1: this decode gives 2.4..2.5 deg where
  carState.steeringAngleDeg gives 2.5, and the rate matches at 0.
  """
  if len(d) < 4:
    return None
  return (_i16be(d, 0) * -0.1, float(_i16be(d, 2)))


def steer_status_counter(d: bytes) -> int | None:
  """0x18F COUNTER 53|2@0+ - byte 6 bits 5:4. The frame is 7 bytes."""
  if len(d) < 7:
    return None
  return (d[6] >> 4) & 0x3


def decode_engine_rpm(d: bytes) -> int | None:
  """0x17C POWERTRAIN_DATA ENGINE_RPM 23|16@0+ - bytes 2-3, big-endian."""
  if len(d) < 4:
    return None
  return (d[2] << 8) | d[3]


def decode_load_bit(d: bytes) -> int | None:
  """0x1A6 byte 2 bit 6 - see LOAD_ID."""
  if len(d) < 3:
    return None
  return (d[2] >> 6) & 1


def eps_grid(tq: list) -> list[tuple[float, float]]:
  """Rebuild 0x18F sample times from the EPS's own clock.

  Receive timestamps here are USB-batch times - frames that arrive together
  share one, and they jitter by ~10 ms. The firing order is ~44 Hz, a 22.7 ms
  period, so a fit on those times measures nothing. The EPS sends 0x18F every
  10.000 ms with a 2-bit counter: each frame's index is its predecessor's plus
  the counter step, choosing among step, step+4, step+8... whichever best
  matches the (coarse) receive gap, so dropped frames leave a hole rather than
  sliding everything after them.

  tq is [(t_rx, value, raw_bytes)]. Returns [(t, value)] on the EPS grid.
  """
  out: list[tuple[float, float]] = []
  k = 0
  prev_c = None
  prev_t = 0.0
  t0 = tq[0][0] if tq else 0.0
  for t, v, raw in tq:
    c = steer_status_counter(raw)
    if prev_c is not None and c is not None:
      est = (t - prev_t) / EPS_PERIOD_S
      base = (c - prev_c) % 4 or 4
      k += min((base + 4 * m for m in range(64)), key=lambda st: abs(st - est))
    elif prev_c is not None:
      k += 1
    out.append((t0 + k * EPS_PERIOD_S, float(v)))
    prev_c, prev_t = c, t
  return out


def _solve3(a: list[list[float]], b: list[float]) -> list[float] | None:
  m = [row[:] + [bb] for row, bb in zip(a, b)]
  for i in range(3):
    p = max(range(i, 3), key=lambda r: abs(m[r][i]))
    if abs(m[p][i]) < 1e-12:
      return None
    m[i], m[p] = m[p], m[i]
    for r in range(3):
      if r != i:
        f = m[r][i] / m[i][i]
        m[r] = [x - f * y for x, y in zip(m[r], m[i])]
  return [m[i][3] / m[i][i] for i in range(3)]


def order_amplitude(samples: list[tuple[float, float]], rpm: list[tuple[float, int]],
                    order: float) -> float | None:
  """Least-squares amplitude of one engine order, phase tracked from rpm.

  Tracking beats a fixed-frequency fit because idle wanders by tens of rpm:
  at the 3rd order that is ~1 Hz, more than a few seconds' frequency
  resolution. This is the method that separated the firing order from the
  noise on routes f5/f6.
  """
  if len(samples) < 50 or not rpm:
    return None
  import math
  j = 0
  ph = 0.0
  prev = samples[0][0]
  S = [[0.0] * 3 for _ in range(3)]
  B = [0.0] * 3
  for t, y in samples:
    while j + 1 < len(rpm) and rpm[j + 1][0] <= t:
      j += 1
    ph += 2.0 * math.pi * order * rpm[j][1] / 60.0 * (t - prev)
    prev = t
    v = (math.cos(ph), math.sin(ph), 1.0)
    for x in range(3):
      B[x] += v[x] * y
      for z in range(3):
        S[x][z] += v[x] * v[z]
  sol = _solve3(S, B)
  return None if sol is None else math.hypot(sol[0], sol[1])


def decode_steer_status(d: bytes) -> int | None:
  """0x18F: STEER_TORQUE_SENSOR 7|16@0- (-1). Column torque, not motor torque.

  Checked against carState on route f1: -74..0 from both.
  """
  if len(d) < 2:
    return None
  return -_i16be(d, 0)
STATIONARY_CPH = 100               # 1.00 km/h, same decode the bootloader uses

# panda can_recv() reports frames back with the bus number offset like this
PANDA_ECHO_OFFSET = 128
PANDA_REJECT_OFFSET = 192

# opendbc/safety/declarations.h: SAFETY_ELM327 is 3. It was 15 here, which is
# SAFETY_VOLKSWAGEN_MQB - a one-digit mistake that made the feature completely
# non-functional in three ways at once, and reported itself as "the board has
# no bootloader, it needs an SWD visit". MQB's TX allowlist has no 0x710/0x712
# so nothing reached the board; mode 15 falls through to the default arm of
# set_safety_mode, which OPENS the harness relay and cuts the stock camera off
# the bus - the exact thing the docstring rejects allOutput for; and it counts
# as a car safety mode, so the panda force-clears heartbeat_disabled and drops
# to SILENT a few seconds in. The health() readback below exists so a wrong
# number can never again present as a dead board.
SAFETY_ELM327 = 3
ELM327_KEEP_NORMAL_CAN = 1         # any non-zero param: do NOT remap bus 1 onto OBD

EPS_LKAS_APPSLOT_BIN = os.path.join(os.path.dirname(__file__), "eps_lkas_appslot.bin")

RX, ECHO, REJECTED = "rx", "echo", "rejected"


def crc16_ccitt(data: bytes) -> int:
  """CRC-16/CCITT-FALSE, matching crc16() in the bootloader's boot_main.c."""
  c = 0xFFFF
  for b in data:
    c ^= b << 8
    for _ in range(8):
      c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
  return c


def describe_nak(d: bytes) -> str:
  err = d[2] if len(d) > 2 else 0
  text = ERRS.get(err, str(err))
  if err == 4 and len(d) >= 8:
    sr = int.from_bytes(d[4:8], "little")
    text += f" [driver code {d[3]}, FLASH_SR={sr:#010x}]"
  return text


def check_app_slot_image(image: bytes) -> str | None:
  """None if this image may be installed, otherwise why not.

  RETURNS A STRING, NEVER RAISES, and that is not a style preference. The
  firmware repo's version of this check raises SystemExit, which inherits
  BaseException and would therefore slip past the `except Exception` in
  pandad.py - the wrapper would exit, the pandad binary would never start, and
  the car would have no CAN at all until manager noticed.

  What it is checking: a STANDALONE image, linked for 0x08000000, has an initial
  stack pointer in SRAM and a reset vector that lands inside the app slot's
  address range by arithmetic coincidence. It passes every structural test the
  bootloader can apply to the raw vector table, and then gets branched into
  16 KB away from its real reset handler. So an app-slot image says what it was
  linked for, in its own bytes, and that is what gets compared.
  """
  if len(image) < APP_ID_OFFSET + 16:
    return f"only {len(image)} bytes - not a firmware image"
  if len(image) > APP_SLOT_BYTES:
    return f"{len(image)} bytes will not fit the {APP_SLOT_BYTES} byte slot"

  sp, pc = struct.unpack("<II", image[0:8])
  magic, origin, _git, _flags = struct.unpack("<4I", image[APP_ID_OFFSET:APP_ID_OFFSET + 16])

  if magic != APP_ID_MAGIC:
    return (f"no app-slot marker at {APP_ID_OFFSET:#x} (found {magic:#010x}) - this looks "
            f"like a STANDALONE image linked for 0x08000000 and must not be installed")
  if origin != APP_ORIGIN:
    return f"linked for {origin:#010x}, not {APP_ORIGIN:#010x}"
  if not 0x20000000 <= sp <= 0x20024000:
    return f"initial stack pointer {sp:#010x} is not in SRAM"
  if not APP_ORIGIN < pc < APP_ORIGIN + APP_SLOT_BYTES:
    return f"reset vector {pc:#010x} is outside the application slot"
  return None


def image_identity(image: bytes) -> dict:
  """The marker's contents. Only meaningful once check_app_slot_image passed."""
  _magic, _origin, git, flags = struct.unpack("<4I", image[APP_ID_OFFSET:APP_ID_OFFSET + 16])
  return {"git": f"{git:08x}", "dirty": bool(flags & APP_ID_DIRTY),
          "readOnly": bool(flags & APP_ID_READONLY), "test": bool(flags & APP_ID_TEST)}


# ---------------------------------------------------------------------------
class PandaTransport:
  """The comma's own panda, in the one window where nothing else owns it.

  ELM327, NOT allOutput. Panda safety drops 0x710/0x712 in any Honda mode, so
  the mode has to change. allOutput is the obvious pick and is the wrong one: it
  calls set_intercept_relay(true, false), which opens the car/camera harness
  relay and cuts the stock ADAS camera off the bus for as long as it is set, and
  it is inside #ifdef ALLOW_DEBUG so it is simply absent from a release panda
  build. elm327 leaves the harness alone and is always compiled in.

  THE PARAM IS NOT OPTIONAL. In elm327 mode panda does
  `if (param == 0) set_can_mode(CAN_MODE_OBD_CAN2)`, and the default param is 0
  - so taking the default silently re-multiplexes panda bus 1 onto the OBD-II
  port. A non-zero param keeps CAN_MODE_NORMAL.

  ELM327 ONLY FORWARDS 8-BYTE FRAMES, so every command is padded. That costs
  nothing: every length check in the bootloader is a minimum (`f->len < 5u`),
  never an equality, so a padded BEGIN is still a BEGIN.
  """

  def __init__(self, bus: int = 0, serial: str | None = None):
    from panda import Panda                              # noqa: PLC0415 - device only

    self.bus = bus
    # cli=False matters: it defaults True and prompts on stdin via input() when
    # more than one panda is attached, which would hang this with no output.
    self.p = Panda(serial, cli=False)
    self.p.set_safety_mode(SAFETY_ELM327, ELM327_KEEP_NORMAL_CAN)

    # Read it back. A wrong mode number is otherwise indistinguishable from a
    # board that is not answering, and the failure it produces points at the
    # hardware rather than at this file.
    mode = self.p.health().get("safety_mode")
    if mode != SAFETY_ELM327:
      raise RuntimeError(f"panda is in safety mode {mode}, not elm327 ({SAFETY_ELM327}); "
                         f"0x710/0x712 would be dropped")
    self.p.can_clear(0xFFFF)

  def describe(self) -> str:
    return f"panda {self.p.get_serial()[0]} bus {self.bus}, elm327"

  def send(self, can_id: int, data: bytes) -> None:
    self.p.can_send(can_id, bytes(data).ljust(8, b"\x00"), self.bus)

  def health(self) -> dict:
    """CAN error counters per bus, best effort.

    The data stream is the prime suspect for the vibration, and the one
    mechanism a CAN log would show is error frames. During the previous update
    the panda counted 123,231 errors on its camera-side controller with the car
    and camera buses joined through K1's bypass. These counters say whether
    that happens during the stream, the hold, or not at all.
    """
    out = {}
    for b in (0, 1, 2):
      try:
        h = self.p.can_health(b)
        out[b] = {k: h[k] for k in ("total_error_cnt", "bus_off_cnt", "error_passive",
                                     "total_rx_lost_cnt", "total_tx_lost_cnt") if k in h}
      except Exception:
        pass
    return out

  def poll(self, timeout: float = 0.0) -> list[tuple[int, bytes, str]]:
    end = time.monotonic() + timeout
    while True:
      out = []
      for addr, dat, src in self.p.can_recv():
        if src == self.bus + PANDA_REJECT_OFFSET:
          out.append((addr, bytes(dat), REJECTED))
        elif src == self.bus + PANDA_ECHO_OFFSET:
          out.append((addr, bytes(dat), ECHO))
        elif src == self.bus:
          out.append((addr, bytes(dat), RX))
      if out or time.monotonic() >= end:
        return out
      time.sleep(0.001)

  def close(self) -> None:
    try:
      self.p.can_clear(0xFFFF)
      self.p.close()
    except Exception:
      pass


class BenchTransport:
  """A candleLight adapter, for exercising this module against a spare board.

  Bench only. python-can is imported inside __init__ so importing this module on
  the device never touches it.
  """

  def __init__(self, bitrate: int = 500000):
    import can                                           # noqa: PLC0415 - bench only
    import usb.core                                      # noqa: PLC0415
    try:
      import libusb_package                              # noqa: PLC0415
      backend = libusb_package.get_libusb1_backend()
    except ImportError:
      backend = None
    dev = usb.core.find(idVendor=0x1D50, idProduct=0x606F, backend=backend)
    if dev is None:
      raise RuntimeError("no candleLight adapter (VID 1d50 PID 606f)")
    name = dev.product
    import usb.util                                      # noqa: PLC0415
    usb.util.dispose_resources(dev)                      # WinUSB is exclusive
    del dev
    self._can = can
    self.bus = can.Bus(interface="gs_usb", channel=name, index=0, bitrate=bitrate)

  def describe(self) -> str:
    return "candleLight, bench"

  def send(self, can_id: int, data: bytes) -> None:
    self.bus.send(self._can.Message(arbitration_id=can_id,
                                    data=bytes(data).ljust(8, b"\x00"),
                                    is_extended_id=False))

  def poll(self, timeout: float = 0.0) -> list[tuple[int, bytes, str]]:
    # ONE blocking read, never a drain-until-empty loop. python-can's gs_usb
    # backend turns timeout=0 into a 1 ms libusb timeout, which Windows rounds
    # up to a ~15 ms timer tick - so the read that finds nothing is the
    # expensive one, and draining always ends with exactly that read. Doing it
    # once per frame instead of once per call took a 46 KB flash from 1m44 to
    # about twelve seconds.
    m = self.bus.recv(timeout=timeout)
    if m is None:
      return []
    kind = ECHO if getattr(m, "is_rx", True) is False else RX
    return [(m.arbitration_id, bytes(m.data), kind)]

  def close(self) -> None:
    try:
      self.bus.shutdown()
    except Exception:
      pass


# ---------------------------------------------------------------------------
class Flasher:
  """The protocol. Transport-agnostic on purpose - see the module docstring."""

  def __init__(self, transport, log: Callable[[str], None] = print,
               progress: Callable[[int], None] | None = None):
    self.t = transport
    self.log = log
    self.progress = progress
    # BOUNDED, AND FILTERED. On the bench this saw a handful of frames a
    # second; on the car's bus 0 it is about 1830. Appending all of them made
    # _rx grow without limit for the whole session and turned _recv's linear
    # scan into an O(n^2) crawl - a flash that works on a bench and gets slower
    # and slower in a car. Nothing here cares about any identifier except the
    # board's replies, our own echoes, and the speed frame.
    self._rx: deque[tuple[int, bytes, str]] = deque(maxlen=512)
    # Separate from _rx on purpose. _rx is flow control and is scanned
    # linearly on every receive, so it has to stay short; this one is only
    # ever appended to, and read once at the end.
    self._trace: deque[tuple[float, int, bytes]] = deque(maxlen=TRACE_MAX)
    # (monotonic time, label) at each step of the procedure, and the panda's
    # CAN error counters at the same instants - so the summary can say what
    # happened in each phase rather than across the whole run.
    self._marks: list[tuple[float, str]] = []
    self._health: list[tuple[float, dict]] = []
    # frames per car-bus ID in the phase currently running, and the finished
    # phases keyed by their start time
    self._census: dict[int, int] = {}
    self._census_phase: dict[float, dict[int, int]] = {}
    self._phase_start: float | None = None
    self.rejected = 0

  # -- wire -----------------------------------------------------------------
  def _pump(self, timeout: float = 0.0) -> None:
    for addr, data, kind in self.t.poll(timeout):
      if kind == REJECTED:
        self.rejected += 1
        if self.rejected == 1:
          self.log(f"  panda SAFETY REJECTED {addr:#05x} - the mode is not letting this out")
        continue
      if kind == RX:
        self._census[addr] = self._census.get(addr, 0) + 1
      if kind != ECHO and addr in TRACE_IDS:
        # Timestamp and raw bytes only. No decoding in the receive path, and
        # no opendbc import in a module that must load on a bench.
        self._trace.append((time.monotonic(), addr, data))
        continue
      if kind != ECHO and addr not in (ID_BOARD, ENGINE_DATA_ID):
        continue                      # ordinary car traffic; see __init__
      self._rx.append((addr, data, kind))

  def _send_raw(self, can_id: int, payload: bytes) -> None:
    """Fire and forget. For ENTER (racing the 800 ms boot window) and the knock
    (after which the board resets and may stop ACKing), waiting for an echo
    costs more than the flow control is worth."""
    self.t.send(can_id, payload)

  def _send(self, can_id: int, payload: bytes, echo_timeout: float = 1.5) -> str | None:
    """Send, then wait for one frame to come back.

    THE ECHO IS THE FLOW CONTROL. Both transports hand a frame back only once it
    has actually been transmitted, so waiting for one throttles this to the speed
    of the wire instead of the speed of USB - and on a gs_usb adapter that is not
    optional, because it holds frames in about three echo slots and silently
    stops accepting them when they are full. Firing a 34-frame chunk at one puts
    two frames on the wire and discards the rest, with no error anywhere.
    """
    self.t.send(can_id, payload)
    end = time.monotonic() + echo_timeout
    while True:
      for item in list(self._rx):
        if item[2] == ECHO:
          self._rx.remove(item)
          return None
      if time.monotonic() >= end:
        return f"frame {can_id:#05x} was never transmitted (no echo in {echo_timeout}s)"
      self._pump(0.02)

  def _recv(self, timeout: float, want: int | None = None) -> bytes | None:
    end = time.monotonic() + timeout
    while True:
      for item in list(self._rx):
        addr, data, kind = item
        if kind == ECHO or addr != ID_BOARD:
          continue
        if want is not None and data[0] != want and data[0] != RSP_NAK:
          continue
        self._rx.remove(item)
        return data
      if time.monotonic() >= end:
        return None
      self._pump(0.02)

  def _drain(self) -> None:
    self._pump()
    self._rx.clear()

  def _expect_ack(self, cmd: int, timeout: float = 3.0) -> str | None:
    d = self._recv(timeout, want=RSP_ACK)
    if d is None:
      return f"no ACK for {CMD_NAMES.get(cmd, cmd)} within {timeout}s"
    if d[0] == RSP_NAK:
      return f"board refused {CMD_NAMES.get(d[1], d[1])}: {describe_nak(d)}"
    if d[1] != cmd:
      return f"ACK for {CMD_NAMES.get(d[1], d[1])}, expected {CMD_NAMES.get(cmd, cmd)}"
    return None

  # -- the car --------------------------------------------------------------
  def moving(self, listen_s: float = 1.0) -> bool:
    """True only if the car SAID it was moving.

    Silence is not motion: a car with no powertrain traffic is a car with the
    ignition off, which is the normal bench case. The bootloader makes the same
    reading, and NAKs ENTER with BOOT_ERR_MOVING regardless of what this decides
    - this is the belt to its braces, not the only check.
    """
    end = time.monotonic() + listen_s
    while time.monotonic() < end:
      self._pump(0.05)
      for addr, data, kind in list(self._rx):
        if kind != ECHO and addr == ENGINE_DATA_ID and len(data) >= 2:
          if ((data[0] << 8) | data[1]) >= STATIONARY_CPH:
            return True
    return False

  # -- session --------------------------------------------------------------
  def knock(self) -> None:
    """Ask a running application to reset itself into its bootloader."""
    self._send_raw(ID_HOST, bytes([CMD_ENTER]) + MAGIC)

  def wait_hello(self, timeout: float) -> bytes | None:
    """The post-reset HELLO, or None.

    A NAK seen here is returned too, and run_flash reports it. The board NAKs a
    knock it refuses - "the car is not stationary" - and swallowing that meant
    waiting the full timeout and then blaming the board for having no
    bootloader, which is the single most misleading thing this tool could say.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      d = self._recv(0.2)
      if d is not None and d[0] in (RSP_HELLO, RSP_NAK):
        return d
    return None

  def enter(self, timeout: float = 4.0) -> str | None:
    """Take the bootloader, inside its 800 ms listen window.

    Fired repeatedly without waiting for echoes: the window is finite and every
    round trip spent being careful is window spent.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      self._send_raw(ID_HOST, bytes([CMD_ENTER]) + MAGIC)
      d = self._recv(0.05, want=RSP_ACK)
      if d is not None:
        if d[0] == RSP_NAK:
          return f"refused: {describe_nak(d)}"
        if d[1] == CMD_ENTER:
          if d[2] != PROTO_VERSION:
            return f"board speaks protocol v{d[2]}, this speaks v{PROTO_VERSION}"
          return None
    return "no answer to ENTER - the board did not reset, or it left its boot window"

  def info(self, tries: int = 3) -> tuple[int, int] | None:
    """Slot size and the CRC32 of what is installed.

    ECHO-WAITED AND RETRIED, unlike ENTER. ENTER is fire-and-forget because it
    is racing an 800 ms boot window and a round trip spent being careful is
    window spent. INFO has no such pressure, and it is a single command whose
    loss costs the whole session - which is exactly what happened on a bench
    run: the adapter's echo slots were still full from the ENTER burst, the
    INFO frame was silently never transmitted, and the session died with
    "no INFO reply" pointing at the board rather than at the adapter.
    """
    for _ in range(tries):
      self._drain()
      if self._send(ID_HOST, bytes([CMD_INFO])) is not None:
        continue                      # never made it onto the wire; try again
      d = self._recv(2.0, want=RSP_INFO)
      if d is not None and d[0] == RSP_INFO:
        return d[2] | (d[3] << 8), int.from_bytes(d[4:8], "little")
    return None

  def abort(self) -> None:
    """Close the session rather than leaving it to time out.

    An idle session self-resets after 10 s. That reset lands wherever it lands -
    it has already interrupted an SWD programming run and shown up elsewhere as
    the board apparently rebooting on its own.
    """
    self._send_raw(ID_HOST, bytes([CMD_ABORT]))

  def mark(self, label: str) -> None:
    now = time.monotonic()
    if self._phase_start is not None:
      self._census_phase[self._phase_start] = self._census
    self._census = {}
    self._phase_start = now
    self._marks.append((now, label))
    h = None
    health = getattr(self.t, "health", None)
    if health is not None:
      try:
        h = health()
      except Exception:
        h = None
    self._health.append((now, h or {}))

  def listen(self, seconds: float) -> None:
    """Keep receiving - and so keep tracing - without sending anything."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
      self._pump(0.05)

  def hold(self, seconds: float) -> str | None:
    """Keep the bootloader session open with no data flowing.

    The knock and the data stream used to be 1.7 s apart, and the vibration
    started with the data stream - close enough that one cannot be told from
    the other. This puts seconds between them. The board sits in bypass the
    whole time, which is stock wiring. INFO is the keep-alive: any command
    refreshes the bootloader's 10 s session timer (boot_main.c).
    """
    end = time.monotonic() + seconds
    next_ping = time.monotonic() + HOLD_PING_S
    while time.monotonic() < end:
      self._pump(0.05)
      if time.monotonic() >= next_ping:
        next_ping += HOLD_PING_S
        if self.info() is None:
          return "the bootloader stopped answering during the hold"
    return None

  def program(self, image: bytes) -> str | None:
    n = len(image)
    chunks = (n + CHUNK_BYTES - 1) // CHUNK_BYTES

    self._drain()
    if (e := self._send(ID_HOST, bytes([CMD_BEGIN]) + struct.pack("<I", n))):
      return e
    if (e := self._expect_ack(CMD_BEGIN, timeout=20.0)):     # erases all of bank 2
      return e
    self.mark("data")

    last_pct = -1
    for idx in range(chunks):
      body = image[idx * CHUNK_BYTES:(idx + 1) * CHUNK_BYTES]
      for attempt in range(3):
        self._drain()
        if (e := self._send(ID_HOST, bytes([CMD_CHUNK]) + struct.pack("<H", idx))):
          return e
        for off in range(0, len(body), DATA_PER_FRAME):
          if (e := self._send(ID_DATA, body[off:off + DATA_PER_FRAME])):
            return e
        if (e := self._send(ID_HOST, bytes([CMD_CEND]) + struct.pack("<HH", idx, crc16_ccitt(body)))):
          return e
        err = self._expect_ack(CMD_CEND, timeout=3.0)
        if err is None:
          break
        # A LOST ACK IS NOT A LOST CHUNK. If the board programmed the chunk and
        # only the reply went missing, resending the whole chunk writes into
        # flash that is no longer erased and fails with PROGERR - turning a
        # dropped frame into a failed update. Ask again first; the board's
        # chunk index and buffer are still where we left them.
        if "no ACK" in err:
          self._send(ID_HOST, bytes([CMD_CEND]) + struct.pack("<HH", idx, crc16_ccitt(body)))
          if self._expect_ack(CMD_CEND, timeout=3.0) is None:
            break
        if attempt == 2:
          return f"chunk {idx}: {err}"
      pct = min(100, 100 * (idx + 1) // chunks)
      if pct != last_pct:
        last_pct = pct
        if self.progress:
          self.progress(pct)

    self.mark("finish")
    crc = zlib.crc32(image) & 0xFFFFFFFF
    self._drain()
    if (e := self._send(ID_HOST, bytes([CMD_FINISH]) + struct.pack("<I", crc))):
      return e
    return self._expect_ack(CMD_FINISH, timeout=20.0)

  def reboot(self) -> None:
    self._drain()
    self._send_raw(ID_HOST, bytes([CMD_REBOOT]))

  def summarise_trace(self) -> dict:
    """Decode the trace into something small enough to put in a log line.

    THIS IS HOW THE TRACE LEAVES THE DEVICE. The raw CSV needs SSH to fetch,
    which not every owner has, so the answer travels the way everything else
    does: a param, emitted to cloudlog by card on the next drive, landing in an
    ordinary route.

    Two answers. "verdict" is whether the wheel MOVED - angle against column
    torque separates a hand from something driving the column. "phases" is
    what the idle vibration did in each step of the procedure: the 3rd engine
    order in the column torque, fitted on the EPS's own sample grid with its
    phase tracked from rpm, beside an off-order reference, the rpm, the load
    state and the panda's CAN error counts. Whichever phase the o3 column
    steps up in is the trigger.
    """
    ang: list[tuple[float, float, float]] = []
    tq: list[tuple[float, int, bytes]] = []
    rpm: list[tuple[float, int]] = []
    load: list[tuple[float, int]] = []
    for t, addr, data in self._trace:
      if addr == 0x156:
        if (v := decode_steering_sensors(data)) is not None:
          ang.append((t, v[0], v[1]))
      elif addr == 0x18F:
        if (v := decode_steer_status(data)) is not None:
          tq.append((t, v, data))
      elif addr == ENGINE_RPM_ID:
        if (v := decode_engine_rpm(data)) is not None:
          rpm.append((t, v))
      elif addr == LOAD_ID:
        if (v := decode_load_bit(data)) is not None:
          load.append((t, v))
    if not ang and not tq:
      return {}

    t0 = min(x[0] for x in (ang or tq))
    degs = [a for _, a, _ in ang]
    rates = [abs(r) for _, _, r in ang]
    tqs = [abs(v) for _, v, _ in tq]
    span = (max(degs) - min(degs)) if degs else 0.0
    peak_tq = max(tqs) if tqs else 0

    if not degs:
      verdict = "no angle frames"
    elif span < 0.5:
      verdict = "wheel did not move"
    elif peak_tq >= 1000:
      verdict = "moved, with column torque - looks like a hand on the wheel"
    else:
      verdict = "MOVED WITH LOW COLUMN TORQUE - something drove the column"

    # Decimated to TRACE_HZ so the whole thing fits in a handful of log lines.
    series = []
    step = 1.0 / TRACE_HZ
    nxt = t0
    ti = ri = 0
    for t, a, _r in ang:
      if t < nxt:
        continue
      nxt = t + step
      while ti + 1 < len(tq) and tq[ti + 1][0] <= t:
        ti += 1
      while ri + 1 < len(rpm) and rpm[ri + 1][0] <= t:
        ri += 1
      series.append([round(t - t0, 2), round(a, 1),
                     tq[ti][1] if tq else 0, rpm[ri][1] if rpm else 0])

    return {
      "n": len(self._trace),
      "dur": round((max(x[0] for x in (ang or tq)) - t0), 2),
      "angle_min": round(min(degs), 1) if degs else None,
      "angle_max": round(max(degs), 1) if degs else None,
      "angle_span": round(span, 1),
      "rate_max": round(max(rates), 1) if rates else None,
      "torque_absmax": peak_tq,
      "verdict": verdict,
      "phases": self._phase_table(eps_grid(tq), rpm, load, ang),
      "series": series,
    }

  def _census_row(self, row: dict, start: float, dur: float, pre_start: float) -> None:
    """Add "lost" and "new" to a phase row, relative to the pre-knock phase."""
    if dur < CENSUS_MIN_PHASE_S or start == pre_start:
      return
    pre = self._census_phase.get(pre_start)
    here = self._census_phase.get(start)
    if not pre or here is None:
      return
    pre_marks = sorted(self._marks)
    pre_dur = pre_marks[1][0] - pre_marks[0][0] if len(pre_marks) > 1 else 0.0
    if pre_dur < CENSUS_MIN_PHASE_S:
      return
    lost = {}
    for addr, n in pre.items():
      if addr in CENSUS_OWN or n / pre_dur < CENSUS_MIN_HZ:
        continue
      ratio = (here.get(addr, 0) / dur) / (n / pre_dur)
      if ratio < CENSUS_LOST:
        lost[f"{addr:x}"] = round(ratio, 2)
    new = sorted(f"{addr:x}" for addr, n in here.items()
                 if addr not in pre and addr not in CENSUS_OWN and n >= 3)
    if lost:
      row["lost"] = dict(sorted(lost.items(), key=lambda kv: kv[1])[:8])
    if new:
      row["new"] = new[:8]

  def _phase_table(self, grid, rpm, load, ang) -> list[dict]:
    marks = sorted(self._marks)
    if len(marks) < 2:
      return []
    base = marks[0][0]
    health = dict(self._health)
    out = []
    for (a, label), (b, _next) in zip(marks, marks[1:]):
      seg = [(t, y) for t, y in grid if a <= t < b]
      r = [v for t, v in rpm if a <= t < b]
      ld = [v for t, v in load if a <= t < b]
      an = [x for t, x, _ in ang if a <= t < b]
      row = {"p": PHASE_NAMES.get(label, label), "s": round(a - base, 2), "d": round(b - a, 2)}
      if r:
        row["rpm"] = round(sum(r) / len(r))
      if ld:
        row["ld"] = round(sum(ld) / len(ld), 2)
      if (o3 := order_amplitude(seg, rpm, 3.0)) is not None:
        row["o3"] = round(o3, 2)
        ref = order_amplitude(seg, rpm, 2.6)
        row["ref"] = None if ref is None else round(ref, 2)
      if len(seg) > 1:
        ys = [y for _, y in seg]
        mu = sum(ys) / len(ys)
        row["sd"] = round((sum((y - mu) ** 2 for y in ys) / len(ys)) ** 0.5, 2)
      if an:
        row["ang"] = round(max(an) - min(an), 1)
      self._census_row(row, a, b - a, marks[0][0])
      ha, hb = health.get(a) or {}, health.get(b) or {}
      for bus in (0, 1, 2):
        ea = (ha.get(bus) or {}).get("total_error_cnt")
        eb = (hb.get(bus) or {}).get("total_error_cnt")
        if ea is not None and eb is not None:
          row[f"e{bus}"] = eb - ea
      out.append(row)
    return out

  def save_trace(self, directory: str = TRACE_DIR) -> str | None:
    """Write the steering trace out. Returns the path, or None.

    Best effort in the strongest sense: this runs inside pandad's wrapper, so
    a full disk or a read-only mount must cost nothing but the trace itself.
    """
    if not self._trace:
      return None
    try:
      os.makedirs(directory, exist_ok=True)
      path = os.path.join(directory, f"flash-{int(time.time())}.csv")
      t0 = self._trace[0][0]
      with open(path, "w") as fh:
        fh.write("# EPS-LKAS flash steering trace.\n"
                 "# t = seconds from the first frame captured.\n"
                 "# 0x156 STEERING_SENSORS, 0x18F STEER_STATUS - decode with\n"
                 "# opendbc honda _steering_sensors_c / _steering_control_e.\n")
        fh.write("t,addr,data\n")
        for t, addr, data in self._trace:
          fh.write(f"{t - t0:.4f},{addr:#05x},{data.hex()}\n")
      return path
    except Exception:
      return None


def describe_hello(d: bytes) -> dict:
  """HELLO is the only frame a board with an empty slot ever sends."""
  return {"proto": d[1], "appOk": bool(d[2] & HELLO_APP_OK),
          "appId": bool(d[2] & HELLO_APP_ID), "dirty": bool(d[2] & HELLO_APP_DIRTY),
          "staged": bool(d[2] & HELLO_STAGED),
          "git": f"{int.from_bytes(d[3:7], 'little'):08x}"}


def run_flash(transport, image: bytes, log: Callable[[str], None] = print,
              progress: Callable[[int], None] | None = None,
              dry_run: bool = False, knock: bool = True,
              hello_timeout: float = 25.0,
              trace_out: Callable[[dict], None] | None = None) -> tuple[bool, str]:
  """Do the whole thing. Returns (ok, message). NEVER RAISES."""
  f = None
  try:
    if (why := check_app_slot_image(image)):
      return False, f"refusing this image: {why}"

    ident = image_identity(image)
    marks = [k for k in ("dirty", "readOnly") if ident[k]]
    log(f"  image {len(image)} bytes, commit {ident['git']}"
        + (f" [{', '.join(marks)}]" if marks else ""))
    if ident["dirty"]:
      log("  WARNING: built from an edited tree - 0x707 will report a commit it is not")

    f = Flasher(transport, log=log, progress=progress)
    log(f"  {transport.describe()}")
    f.mark("start")

    if f.moving():
      return False, "the car is moving - stop first"

    if knock:
      f.listen(PRE_CAPTURE_S)
      log("  knocking: asking the application to reboot into its bootloader")
      f.mark("knock")
      f.knock()

    hello = f.wait_hello(hello_timeout)
    f.mark("hello")
    if hello is None:
      return False, ("no HELLO. Either the board has no bootloader (it needs one SWD "
                     "visit), or it never reset.")
    if hello[0] == RSP_NAK:
      return False, f"refused: {describe_nak(hello)}"
    h = describe_hello(hello)
    log(f"  HELLO v{h['proto']}: installed {h['git']}, appOk={h['appOk']}")

    if (e := f.enter()):
      return False, e

    slot = f.info()
    if slot is None:
      return False, "no INFO reply"
    log(f"  app slot {slot[0]} KB, current contents CRC32 {slot[1]:#010x}")

    f.mark("hold")
    if (e := f.hold(HOLD_BEFORE_DATA_S)):
      return False, e
    f.mark("begin")

    if dry_run:
      f.abort()
      return True, f"dry run: reached the bootloader, installed {h['git']}, nothing written"

    if (e := f.program(image)):
      return False, e
    log("  image accepted and the staging header is written")

    f.reboot()
    f.mark("reboot")

    # Read the result back out of the post-reset HELLO rather than believing our
    # own optimism. This is also what makes the settings page honest immediately
    # instead of at the end of the next drive.
    after = f.wait_hello(25.0)
    f.mark("hello_after")
    # The app starts ~1 s after this HELLO and re-splits K1/K2. Nothing else
    # records that moment, and on route f6 the column shifted 1.4 deg somewhere
    # in exactly that unrecorded window.
    f.listen(POST_CAPTURE_S)
    if after is None:
      # Still the bare hash: the hook writes this straight into a param the
      # settings page renders, and an English sentence there would be shown to
      # the driver as a firmware version.
      log("  no confirming HELLO seen - the image was accepted but not witnessed")
      return True, ident["git"]
    if after[0] == RSP_NAK:
      return False, f"refused: {describe_nak(after)}"
    a = describe_hello(after)
    if not a["appOk"]:
      return False, (f"installed, but the board refuses to run it "
                     f"(HELLO appOk=0, appId={a['appId']})")
    if a["git"] != ident["git"]:
      return False, f"board reports {a['git']} after installing {ident['git']}"
    return True, a["git"]
  except Exception as e:
    # NEVER let this reach pandad. An exception escaping here would take the
    # wrapper down with it, the pandad binary would never start, and the car
    # would have no CAN at all until manager noticed.
    return False, f"{type(e).__name__}: {e}"
  finally:
    # On the failure paths too - a flash that went wrong is exactly when the
    # steering trace is worth having.
    if f is not None:
      try:
        f.mark("end")
      except Exception:
        pass
      if (p := f.save_trace()):
        log(f"  steering trace: {len(f._trace)} frames -> {p}")
      # The caller decides where this goes. The hook puts it in a param,
      # because a file on /data needs SSH and the answer has to reach someone
      # who does not have it.
      if trace_out is not None:
        try:
          trace_out(f.summarise_trace())
        except Exception:
          pass


def load_bundled_image() -> tuple[bytes | None, str]:
  if not os.path.exists(EPS_LKAS_APPSLOT_BIN):
    return None, f"no bundled image at {EPS_LKAS_APPSLOT_BIN}"
  with open(EPS_LKAS_APPSLOT_BIN, "rb") as fh:
    return fh.read(), ""


def main() -> int:
  import argparse                                          # noqa: PLC0415
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--bin", default=None, help="image to install (default: the bundled one)")
  ap.add_argument("--bench", action="store_true", help="candleLight adapter instead of the panda")
  ap.add_argument("--bus", type=int, default=0, help="0 car, 2 camera. NEVER 1.")
  ap.add_argument("--dry-run", action="store_true", help="reach the bootloader and stop")
  ap.add_argument("--no-knock", action="store_true", help="wait for a manual reset instead")
  a = ap.parse_args()

  if a.bus == 1:
    print("bus 1 has no transceiver on this board - use 0 (car) or 2 (camera)")
    return 2

  if a.bin:
    with open(a.bin, "rb") as fh:
      image = fh.read()
  else:
    image, err = load_bundled_image()
    if image is None:
      print(err)
      return 2

  transport = BenchTransport() if a.bench else PandaTransport(bus=a.bus)
  try:
    ok, msg = run_flash(transport, image, dry_run=a.dry_run, knock=not a.no_knock,
                        progress=lambda p: print(f"\r  {p:3d}%", end="", flush=True))
  finally:
    transport.close()
  print()
  print(("  OK: " if ok else "  FAILED: ") + msg)
  return 0 if ok else 1


if __name__ == "__main__":
  raise SystemExit(main())
