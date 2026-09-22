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
TRACE_IDS = (0x156, 0x18F)
TRACE_MAX = 24000          # ~2 min of both at 100 Hz; ~1 MB of raw bytes
TRACE_DIR = "/data/eps-lkas-trace"
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
    self.rejected = 0

  # -- wire -----------------------------------------------------------------
  def _pump(self, timeout: float = 0.0) -> None:
    for addr, data, kind in self.t.poll(timeout):
      if kind == REJECTED:
        self.rejected += 1
        if self.rejected == 1:
          self.log(f"  panda SAFETY REJECTED {addr:#05x} - the mode is not letting this out")
        continue
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

  def program(self, image: bytes) -> str | None:
    n = len(image)
    chunks = (n + CHUNK_BYTES - 1) // CHUNK_BYTES

    self._drain()
    if (e := self._send(ID_HOST, bytes([CMD_BEGIN]) + struct.pack("<I", n))):
      return e
    if (e := self._expect_ack(CMD_BEGIN, timeout=20.0)):     # erases all of bank 2
      return e

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

    crc = zlib.crc32(image) & 0xFFFFFFFF
    self._drain()
    if (e := self._send(ID_HOST, bytes([CMD_FINISH]) + struct.pack("<I", crc))):
      return e
    return self._expect_ack(CMD_FINISH, timeout=20.0)

  def reboot(self) -> None:
    self._drain()
    self._send_raw(ID_HOST, bytes([CMD_REBOOT]))

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
              hello_timeout: float = 25.0) -> tuple[bool, str]:
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

    if f.moving():
      return False, "the car is moving - stop first"

    if knock:
      log("  knocking: asking the application to reboot into its bootloader")
      f.knock()

    hello = f.wait_hello(hello_timeout)
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

    if dry_run:
      f.abort()
      return True, f"dry run: reached the bootloader, installed {h['git']}, nothing written"

    if (e := f.program(image)):
      return False, e
    log("  image accepted and the staging header is written")

    f.reboot()

    # Read the result back out of the post-reset HELLO rather than believing our
    # own optimism. This is also what makes the settings page honest immediately
    # instead of at the end of the next drive.
    after = f.wait_hello(25.0)
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
    if f is not None and (p := f.save_trace()):
      log(f"  steering trace: {len(f._trace)} frames -> {p}")


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
