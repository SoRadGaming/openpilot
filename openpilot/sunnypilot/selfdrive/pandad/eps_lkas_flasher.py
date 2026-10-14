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

TWO BOARDS, ONE PROTOCOL (BLR-v1, 2026-10-14). The brake-lamp relay board
(the EPS-LKAS repo's blr/) runs the same v1 bootloader protocol with every
identity value changed. A Target carries one board's identity - see TARGETS
below - and every function here takes one, EPS by default: with EPS this
module puts on the wire exactly what it always has. The BLR's app forces its
lamp output off before it resets on the knock, and its bootloader never drives
it, so for the lamp board the worst outcome is the same in kind: a board in its
bootloader, and brake lamps that work from the pedal exactly as stock.

Run it by hand, from the repo root:

    python -m openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher --dry-run
    python -m openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher --bench   # candleLight
    python -m openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher --target blr --dry-run
"""
# NO `from __future__ import annotations` (removed for BLR-v1). The tests load
# this file with spec_from_file_location and never register it in sys.modules,
# and dataclasses resolves a STRING annotation through sys.modules[__module__]
# - so with postponed annotations the Target dataclass below raises
# AttributeError at import there. Every annotation here is valid at runtime.
import os
import struct
import time
import zlib
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

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
#
# 0x70B GW_STEER_GRANT (push9) is the board's own state, 10 Hz on the car bus
# while its firmware runs: STATE/REASON say whether K2 was split or waiting
# for the comma at the knock, and again in `post` after the new image starts
# (the one window where the wheel was ever seen to move, route f6). The
# bootloader sends nothing, so it appears in `pre` and `post` only - about
# 120 frames, nowhere near TRACE_MAX.
GRANT_ID = 0x70B
TRACE_IDS = (0x156, 0x18F, 0x17C, 0x1A6, GRANT_ID)
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
# the vibration follows is unambiguous. They cost ~16 s per update; the car is
# parked in bypass throughout, which is stock wiring.
#
# THE HOLD IS THE OWNER'S COUNTDOWN (push9, 2026-10-13). "Relays to stock,
# wait, then update" already ran - the knock drops K1 and K2, then the session
# sat 8 s with no data - but the page showed 0% for all of it (route 134's
# trace: relays at stock 9.6 s before the first data frame). It is now 5 s,
# shown on screen as a countdown with a Cancel button (ABORT: the old image
# stays). 5 s still leaves the census its >= 1 s and the o3 fit its >= 50
# samples, and the pings at 2 s and 4 s stay inside the bootloader's 10 s.
PRE_CAPTURE_S = 4.0        # baseline, before the knock, on top of moving()'s 1 s
HOLD_BEFORE_DATA_S = 5.0   # bootloader session open, relays in bypass, no data
HOLD_PING_S = 2.0          # keep-alive; the bootloader resets after 10 s silent

# THE ENGINE IS OFF FOR AN UPDATE (owner, 2026-10-13: "ign on no engine on").
# Every update with the engine idling has left the V6's firing vibration
# stepped up in the column (f5/f6, and route 134's trace: from the erase on,
# 2.5x, with the relays already at stock for 8 s). With the engine off there
# is no firing vibration to step up, and no EPS assist either. The ignition
# stays on: the comma and the board are powered from it.
#
# 0x17C ENGINE_RPM, the median over the moving() second: 0 with the engine off
# (the first 0.1 s of routes 135/136/137, before the crank), 32-188 cranking,
# 637-1400 running (warm idle ~695 on 134, 816-859 on 132). Checked before the
# knock, at the end of the baseline and through the countdown up to BEGIN. No
# 0x17C at all is refused in the car (NO_ENGINE_DATA, below) and allowed only
# on the bench adapter, which has no car to see.
ENGINE_OFF_MAX_RPM = 400
ENGINE_RUNNING = "Turn the engine off - leave the ignition on - then update"
IGNITION_OFF = "the ignition is off - turn it on, leave the engine off, then update"
# No 0x17C at all with the ignition on in the car (PandaTransport) is a blind
# gate - the wrong bus, no PCM traffic, accessory - and the owner's rule would
# go unenforced: refused. 134-137 have 0x17C at 100 Hz from the first frame.
NO_ENGINE_DATA = "no engine data on the car bus - ignition on, engine off, then update"
CANCELLED = "cancelled"
CANCEL_POLL_S = 0.2        # the Cancel param is a file: read it at most this often
# The countdown stops offering Cancel this long before its end, and reads the
# Cancel once more, unthrottled, immediately before BEGIN: a Cancel the screen
# took is honored right up to the erase, and the screen never offers one the
# flasher could miss (push9 review: one at +4.95 s used to be ignored).
CANCEL_LAST_S = 0.5
ENGINE_WINDOW_S = 0.5      # the countdown watches the engine over this much 0x17C
# THE COUNTDOWN KEEPS THE GATE'S PROOF (push9 re-review). 0x17C seen at the gate
# and then gone for this long in the countdown is a gate gone blind - the
# ignition switched off, or to accessory, under the update - and is ABORT and
# NO_ENGINE_DATA, before BEGIN: the old image stays. 100 frames at 100 Hz, so
# never a USB batch. The panda's ignition reading off (read at most every
# CANCEL_POLL_S, and once more right before BEGIN) is ABORT and IGNITION_OFF.
# The bench - no 0x17C from the start, allowed at the gate - is not watched for
# it: there is nothing there to lose.
ENGINE_LOST_S = 1.0
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
# The board's telemetry (0x700-0x70F) and its bootloader's replies (0x711) -
# NOT 0x712-0x71F: a module answering the update's own 0x710/0x712 would reply
# on request + 8 (0x718/0x71A), and that is exactly what `new` is for. Our own
# 0x710/0x712 never count: echoes are not RX.
CENSUS_OWN = frozenset(range(0x700, 0x710)) | {0x711}
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


def decode_grant(d: bytes) -> tuple[int, int, int, int] | None:
  """0x70B GW_STEER_GRANT (opendbc honda_accord_au_2015 / the board's dbc):
  (STATE byte 0, REASON byte 1, EPS_ERROR_STATE byte 3 bits 5:2,
  CAM_LKAS_ON byte 3 bit 7). STATE 8 REASON 16 is the stock wait (K2 at
  stock until the comma is heard); STATE 0 is split."""
  if len(d) < 4:
    return None
  return d[0], d[1], (d[3] >> 2) & 0xF, (d[3] >> 7) & 1


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
  m = [row[:] + [bb] for row, bb in zip(a, b, strict=False)]
  for i in range(3):
    p = max(range(i, 3), key=lambda r: abs(m[r][i]))
    if abs(m[p][i]) < 1e-12:
      return None
    m[i], m[p] = m[p], m[i]
    for r in range(3):
      if r != i:
        f = m[r][i] / m[i][i]
        m[r] = [x - f * y for x, y in zip(m[r], m[i], strict=False)]
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

# ---- BLR-v1: blr/inc/blr_boot_proto.h, blr_app_id.h, blr_proto.h (the EPS-LKAS repo). -------------------------
# Change a number there, change it here: test_blr_flasher.py compares these with the headers whenever the firmware
# tree is reachable, and with the design's literals always.
BLR_ID_HOST = 0x628
BLR_ID_BOARD = 0x629
BLR_ID_DATA = 0x62A
BLR_MAGIC = b"BLRBOOT"
BLR_BOARD_CLASS = 2                # HELLO byte 7 and ENTER-ACK byte 7; the EPS bootloader sends 0 there
BLR_APP_ID_MAGIC = 0x31524C42      # "BLR1", at APP_ID_OFFSET like APL1
BLR_BOOT_ID_MAGIC = 0x31424C42     # "BLB1": the BLR's BOOTLOADER record - an image carrying it is never an app
BLR_APP_ORIGIN = 0x08004000        # the same die and flash map as the EPS board
BLR_APP_SLOT_BYTES = 112 * 1024
BLR_APP_ID_RESERVED = 0x0E         # flags bits 3:1 - READONLY/TEST on the EPS marker, never set by a BLR build
BLR_VARIANT_SHIFT = 4              # flags bits 5:4 = VARIANT, and HELLO status bits 5:4 the installed one's
BLR_VARIANT_MASK = 0x30
BLR_VARIANT_BENCH, BLR_VARIANT_SHADOW, BLR_VARIANT_LIVE = 1, 2, 3
BLR_VARIANT_NAMES = {0: "invalid", BLR_VARIANT_BENCH: "bench", BLR_VARIANT_SHADOW: "shadow", BLR_VARIANT_LIVE: "live"}
BLR_ERR_WRONG_IMAGE, BLR_ERR_NOT_DUAL_BANK, BLR_ERR_OUTPUT_ON = 8, 9, 10
# HELLO status bit 6: this boot erased a flash page that read back with an ECC error (an erase or a write a power cut
# interrupted) before anything else read flash - and a staged image was cancelled with it (review round 1, BOOT-2).
BLR_HELLO_ECC_FIXED = 0x40
# The post-build trailer, the last 12 bytes of every BLR app .bin (review round 1, BOOT-6): {"BLRT", the length of the
# whole file including these 12 bytes, CRC-32 of every byte before them}, 0xFF-padded in front to a double word. The
# marker and the vectors do not say how long an image is, so without it any prefix of a real image that still holds
# its reset handler passed every check. The bootloader checks it at FINISH and again before the copy; the comma checks
# it before a frame is sent (check_app_slot_image) and before an image is offered (board.py bundled_offer).
BLR_TRAILER_MAGIC = 0x54524C42     # "BLRT" little-endian
BLR_TRAILER_BYTES = 12
BLR_ERRS = {**ERRS, BLR_ERR_WRONG_IMAGE: "image is not for this board",
            BLR_ERR_NOT_DUAL_BANK: "board flash is not dual-bank",
            BLR_ERR_OUTPUT_ON: "lamp output is on"}
# The BLR's own block, 0x620-0x62F: telemetry, and its bootloader's 0x628-0x62A.
BLR_ID_BLOCK = frozenset(range(0x620, 0x630))
BLR_STATUS_ID = 0x620              # BLR_STATUS, 10 Hz: STATE bits 2:0, OUT_DRIVE bit 3 (blr.dbc)
# <= 400 DATA frames/s (DESIGN B.1/R5): a BLR update shares bus 0 with the EPS board, which forwards it.
BLR_DATA_FPS_MAX = 400.0
DATA_PACE_MARGIN_S = 10e-6         # 2.51 ms, not 2.50: no rounding of a clock can fit a 401st frame in a second
BLR_APPSLOT_BIN = os.path.join(os.path.dirname(__file__), "blr_appslot.bin")
BLR_TRACE_DIR = "/data/blr-trace"
# A LOST FINISH ACK IS NOT A FAILED INSTALL (BLR-v1 review, lens B). The BLR bootloader's FINISH is two CRC-32 passes
# over at most 112 KB of bank 2 and one header write - well under a second - and it ACKs a repeated FINISH again
# without writing twice (blr_boot_main.c: the header is already exactly this one). So its ACK is awaited this long,
# and on silence FINISH is sent once more: twice this is still inside the session's 10 s, which any frame restarts.
# The EPS's 20 s single wait would outlast that session: the board resets and installs the image (its header is
# written) while the page reports "no ACK for FINISH".
BLR_FINISH_ACK_S = 4.0
# The EPS board's GW_SPLIT_STATUS (its dbc): forwarding-ring peak byte 0, drop/overflow flags byte 4 bits 4:0,
# FAULT byte 4 bits 7:5. Its K1 forwards a BLR update's burst from the car side to the camera side.
EPS_SPLIT_ID = 0x706


@dataclass(frozen=True, eq=False)
class Target:
  """One board's identity: everything that makes an update this board's and never the other's (DESIGN E.1).

  Frozen, and compared by identity - EPS is EPS. None in hello_class / allowed_variants / data_fps_max / retry_naks /
  finish_retry_s, and False in check_slot / knock_enters, means "not checked / not paced / retried on anything / one
  FINISH, 20 s / INFO's slot not compared / a HELLO and nothing else answers the knock", which is the EPS as proven in
  the car (test_eps_lkas_wire_errors.py); the BLR sets all seven, as its own host tool blr_can_update.py does.

  words - what the log, the trace file and the update screen call things on this board:
    board, image      the board, and its images, in a sentence
    no_marker         the refusal when 0x100 holds no marker this module knows ({off}, {magic})
    dirty             the log's warning for an image built from an edited tree (the EPS installs one; the BLR refuses)
    no_hello          the result when nothing says HELLO
    trace             the log's name for the raw trace file
    trace_header      its first lines
    title, click, click_line, stock, done_line
                      the update screen (board.py): button title (<= 10 characters), the knock phase's title and
                      line, the countdown's title, and the line after "done" ("" = none). The EPS's are today's.
  """
  name: str                                # "eps" | "blr"
  id_host: int                             # host -> board: knock, commands
  id_board: int                            # board -> host: ACK/NAK/INFO/HELLO
  id_data: int                             # host -> board: 8 image bytes
  magic: bytes                             # ENTER bytes 1..7, and the knock
  marker_magic: int                        # the app marker at APP_ID_OFFSET
  marker_origin: int                       # ... and the address it must say the image was linked for
  slot_bytes: int
  hello_class: int | None                  # HELLO[7] and ENTER-ACK[7]; None = not checked
  allowed_variants: frozenset[int] | None  # marker VARIANTs the comma installs; None = no VARIANT field
  refuse_dirty: bool                       # refuse an image built from an edited tree
  strict_image: bool                       # the bootloader's full image rules here too: odd PC inside the image,
                                           # reserved flag bits zero, the image's own length/CRC-32 trailer (BLRT)
  image_path: str                          # the bundled image
  census_own: frozenset[int]               # IDs the census never reports as lost or new
  trace_ids: tuple[int, ...]               # traced raw - and kept out of _rx, so never the board's reply ID
  trace_dir: str
  data_fps_max: float | None               # DATA frame ceiling; None = the echo's pace
  retry_naks: frozenset[int] | None        # NAKs to CEND a chunk is sent again after; None = any (the EPS)
  finish_retry_s: float | None             # FINISH's ACK awaited this long, then FINISH once more; None = once, 20 s
  check_slot: bool                         # INFO's slot size must be slot_bytes, or ABORT before the countdown
  knock_enters: bool                       # an ENTER ACK to the knock is a resident bootloader's open session
  errs: Mapping[int, str]                  # NAK texts
  words: Mapping[str, str]


EPS_WORDS = MappingProxyType({
  "board": "EPS-LKAS board",
  "image": "an EPS-LKAS application image",
  "no_marker": ("no app-slot marker at {off:#x} (found {magic:#010x}) - this looks " +
                "like a STANDALONE image linked for 0x08000000 and must not be installed"),
  "dirty": "  WARNING: built from an edited tree - 0x707 will report a commit it is not",
  "no_hello": ("no HELLO. Either the board has no bootloader (it needs one SWD " +
               "visit), or it never reset."),
  "trace": "steering trace",
  "trace_header": ("# EPS-LKAS flash steering trace.\n" +
                   "# t = seconds from the first frame captured.\n" +
                   "# 0x156 STEERING_SENSORS, 0x18F STEER_STATUS - decode with\n" +
                   "# opendbc honda _steering_sensors_c / _steering_control_e.\n"),
  "title": "update",
  "click": "relays to stock",
  "click_line": "the car is on stock wiring",
  "stock": "relays in stock",
  "done_line": "turn the car off, then on, before driving",
})

BLR_WORDS = MappingProxyType({
  "board": "BLR-v1 lamp board",
  "image": "a BLR-v1 application image",
  "no_marker": "no BLR1 app-slot marker at {off:#x} (found {magic:#010x}) - not a BLR-v1 application image",
  "dirty": "  WARNING: built from an edited tree - 0x627 will report a commit it is not",
  "no_hello": ("no HELLO. Either the lamp board has no bootloader (it needs one SWD " +
               "visit), or it never reset."),
  "trace": "flash trace",
  "trace_header": ("# BLR-v1 flash trace.\n" +
                   "# t = seconds from the first frame captured.\n" +
                   "# 0x17C POWERTRAIN_DATA, 0x1A6, 0x620 BLR_STATUS (blr.dbc), and the EPS board's\n" +
                   "# 0x706 GW_SPLIT_STATUS / 0x70B GW_STEER_GRANT (eps-lkas-gw.dbc).\n"),
  "title": "lamp board",
  "click": "lamp output off",
  "click_line": "brake lamps are pedal-only",
  "stock": "lamp output off",
  "done_line": "",
})

EPS = Target(
  name="eps", id_host=ID_HOST, id_board=ID_BOARD, id_data=ID_DATA, magic=MAGIC,
  marker_magic=APP_ID_MAGIC, marker_origin=APP_ORIGIN, slot_bytes=APP_SLOT_BYTES,
  hello_class=None, allowed_variants=None, refuse_dirty=False, strict_image=False,
  image_path=EPS_LKAS_APPSLOT_BIN, census_own=CENSUS_OWN, trace_ids=TRACE_IDS, trace_dir=TRACE_DIR,
  data_fps_max=None, retry_naks=None, finish_retry_s=None, check_slot=False, knock_enters=False,
  errs=MappingProxyType(ERRS), words=EPS_WORDS)

# The census leaves out the BLR's own block (its telemetry stops at the knock and its bootloader answers on 0x629)
# and keeps the EPS board's 0x700-0x70F in: if a BLR update's burst starves the EPS board, that is news. The trace is
# the engine gate's 0x17C and the load bit, the BLR's own status, and the EPS board under the burst: its forwarding
# ring (0x706) and its state (0x70B). A chunk is sent again only after a lost ACK or a NAK that a lost frame explains
# - sequence, CRC, short (blr_can_update.py RETRY_NAKS): after NAK 8 the board has already closed the session, and
# a retry would only bury "image is not for this board" under "command out of sequence".
# Three session rules are the BLR host tool's too (blr_can_update.py, BLR-v1 review lens B): a FINISH whose ACK went
# missing is asked once more (BLR_FINISH_ACK_S); INFO must report this board's 112 KB slot, or it is ABORT before the
# countdown - a bootloader with another slot is not the one this image was checked for; and a bootloader that was
# already running - nothing valid in its slot, so it never jumps - takes the knock for what its bytes are, an ENTER,
# and ACKs it: that ACK is the session ("entered"), where waiting for a HELLO meant its 10 s timeout and a reset.
BLR = Target(
  name="blr", id_host=BLR_ID_HOST, id_board=BLR_ID_BOARD, id_data=BLR_ID_DATA, magic=BLR_MAGIC,
  marker_magic=BLR_APP_ID_MAGIC, marker_origin=BLR_APP_ORIGIN, slot_bytes=BLR_APP_SLOT_BYTES,
  hello_class=BLR_BOARD_CLASS, allowed_variants=frozenset({BLR_VARIANT_SHADOW, BLR_VARIANT_LIVE}),
  refuse_dirty=True, strict_image=True,
  image_path=BLR_APPSLOT_BIN, census_own=BLR_ID_BLOCK,
  trace_ids=(ENGINE_RPM_ID, LOAD_ID, BLR_STATUS_ID, EPS_SPLIT_ID, GRANT_ID), trace_dir=BLR_TRACE_DIR,
  data_fps_max=BLR_DATA_FPS_MAX, retry_naks=frozenset({1, 3, 6}),
  finish_retry_s=BLR_FINISH_ACK_S, check_slot=True, knock_enters=True,
  errs=MappingProxyType(BLR_ERRS), words=BLR_WORDS)

TARGETS = {EPS.name: EPS, BLR.name: BLR}

# What a marker at APP_ID_OFFSET says an image is, for the refusal when it is not the target's.
MARKER_NAMES = {
  APP_ID_MAGIC: "an EPS-LKAS image (APL1 marker)",
  BLR_APP_ID_MAGIC: "a BLR-v1 lamp-board image (BLR1 marker)",
  BLR_BOOT_ID_MAGIC: "the BLR-v1 BOOTLOADER (BLB1 record), which goes on over SWD only,",
}


def marker_variant(flags: int) -> int:
  """The BLR marker's VARIANT (flags bits 5:4): 1 bench, 2 shadow, 3 live, 0 invalid."""
  return (flags & BLR_VARIANT_MASK) >> BLR_VARIANT_SHIFT


def crc16_ccitt(data: bytes) -> int:
  """CRC-16/CCITT-FALSE, matching crc16() in the bootloader's boot_main.c."""
  c = 0xFFFF
  for b in data:
    c ^= b << 8
    for _ in range(8):
      c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
  return c


def describe_nak(d: bytes, errs: Mapping[int, str] = ERRS) -> str:
  err = d[2] if len(d) > 2 else 0
  text = errs.get(err, str(err))
  if err == 4 and len(d) >= 8:
    sr = int.from_bytes(d[4:8], "little")
    text += f" [driver code {d[3]}, FLASH_SR={sr:#010x}]"
  return text


def check_app_slot_image(image: bytes, target: Target = EPS) -> str | None:
  """None if this image may be installed on `target`'s board, otherwise why not.

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

  AND WHICH BOARD IT IS FOR (BLR-v1). The marker's magic says that too: APL1 is
  the EPS board's, BLR1 the lamp board's. Each is refused on the other board by
  name - an EPS image on a BLR would drive PB10 high, its UART idle level, which
  on the BLR is the brake lamps on. For the BLR this applies its bootloader's
  full rules (check_blr_image.py --for-car): an odd reset vector inside the
  image's own bytes, reserved flag bits zero, a shadow or live VARIANT - never
  bench - and a clean tree; and the image's own trailer, the post-build {BLRT,
  length, CRC-32} in its last 12 bytes, so a truncated or partial file is refused
  here and not at FINISH (blr_trailer_problem()).
  """
  t = target
  if len(image) < APP_ID_OFFSET + 16:
    return f"only {len(image)} bytes - not a firmware image"
  if len(image) > t.slot_bytes:
    return f"{len(image)} bytes will not fit the {t.slot_bytes} byte slot"

  sp, pc = struct.unpack("<II", image[0:8])
  magic, origin, git, flags = struct.unpack("<4I", image[APP_ID_OFFSET:APP_ID_OFFSET + 16])

  if magic != t.marker_magic:
    if magic in MARKER_NAMES:
      return f"this is {MARKER_NAMES[magic]}, not {t.words['image']} - it must never be installed on the {t.words['board']}"
    return t.words["no_marker"].format(off=APP_ID_OFFSET, magic=magic)
  if origin != t.marker_origin:
    return f"linked for {origin:#010x}, not {t.marker_origin:#010x}"
  if not 0x20000000 <= sp <= 0x20024000:
    return f"initial stack pointer {sp:#010x} is not in SRAM"
  if not t.marker_origin < pc < t.marker_origin + t.slot_bytes:
    return f"reset vector {pc:#010x} is outside the application slot"
  if t.strict_image:
    if not pc & 1:
      return f"reset vector {pc:#010x} is not a Thumb address"
    if (pc & ~1) - t.marker_origin >= len(image):
      return f"reset vector {pc:#010x} points past the end of the image ({len(image)} bytes)"
    if flags & BLR_APP_ID_RESERVED:
      return f"marker flags {flags:#x} set bits 1-3, which no {t.words['board']} build sets"
  if t.allowed_variants is not None:
    v = marker_variant(flags)
    if v == BLR_VARIANT_BENCH:
      return "bench image - never installed from the comma"
    if v not in t.allowed_variants:
      return f"the marker's VARIANT is {v} ({BLR_VARIANT_NAMES.get(v, '?')}): only shadow or live is installed"
  if t.refuse_dirty and flags & APP_ID_DIRTY:
    return f"built from a dirty tree: the board would report {git:08x}, a commit this image was not built from"
  if t.strict_image and (why := blr_trailer_problem(image)):
    return why
  return None


def blr_trailer_problem(image: bytes) -> str:
  """Empty if `image` ends with its own valid BLRT trailer, else why not - in the
  BLR bootloader's order (img_trailer_ok(): the length, the magic, the length
  field, the CRC-32), and in blr_hostlib.trailer_problem()'s sense. Never raises."""
  n = len(image)
  if n < APP_ID_OFFSET + 16 + BLR_TRAILER_BYTES:
    return f"{n} bytes cannot hold the marker and the {BLR_TRAILER_BYTES}-byte trailer"
  magic, length, crc = struct.unpack_from("<III", image, n - BLR_TRAILER_BYTES)
  if magic != BLR_TRAILER_MAGIC:
    return (f"no image trailer (BLRT) in its last {BLR_TRAILER_BYTES} bytes: a truncated or partial copy of an " +
            "image, or a .bin the BLR post-build did not make")
  if length != n:
    return f"the trailer says the image is {length} bytes, but it is {n}: a truncated, padded or concatenated file"
  if zlib.crc32(image[:n - BLR_TRAILER_BYTES]) & 0xFFFFFFFF != crc:
    return "the image does not match its own trailer's CRC-32: the file was changed after the build"
  return ""


def image_identity(image: bytes, target: Target = EPS) -> dict:
  """The marker's contents. Only meaningful once check_app_slot_image passed.

  Reads the 16 bytes at APP_ID_OFFSET only, so the first 0x110 bytes of a file
  are enough. A target whose marker carries a VARIANT (the BLR) adds it by name:
  "shadow" / "live" - two images of one commit differ only there."""
  _magic, _origin, git, flags = struct.unpack("<4I", image[APP_ID_OFFSET:APP_ID_OFFSET + 16])
  ident = {"git": f"{git:08x}", "dirty": bool(flags & APP_ID_DIRTY),
           "readOnly": bool(flags & APP_ID_READONLY), "test": bool(flags & APP_ID_TEST)}
  if target.allowed_variants is not None:
    ident["variant"] = BLR_VARIANT_NAMES.get(marker_variant(flags), "invalid")
  return ident


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
    from panda import Panda                              # device only

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
      raise RuntimeError(f"panda is in safety mode {mode}, not elm327 ({SAFETY_ELM327}); " +
                         "0x710/0x712 would be dropped")
    self.p.can_clear(0xFFFF)

  def describe(self) -> str:
    return f"panda {self.p.get_serial()[0]} bus {self.bus}, elm327"

  def ignition(self) -> bool | None:
    """The panda's own ignition reading (line or CAN), as pandaStates carries it
    to the page's gate. None if it cannot be read: the gate is then the page's."""
    try:
      h = self.p.health()
      return bool(h.get("ignition_line") or h.get("ignition_can"))
    except Exception:
      return None

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
    import can                                           # bench only
    import usb.core
    try:
      import libusb_package
      backend = libusb_package.get_libusb1_backend()
    except ImportError:
      backend = None
    dev = usb.core.find(idVendor=0x1D50, idProduct=0x606F, backend=backend)
    if dev is None:
      raise RuntimeError("no candleLight adapter (VID 1d50 PID 606f)")
    name = dev.product
    import usb.util
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
  """The protocol. Transport-agnostic on purpose - see the module docstring.

  One board per Flasher: `target` (EPS by default) says which IDs, which magic,
  which board class and which pace. The protocol methods are written in v1's
  own channel names, ID_HOST and ID_DATA, and _wire_id is the one place those
  become the target's identifiers."""

  def __init__(self, transport, log: Callable[[str], None] = print,
               progress: Callable[[int], None] | None = None,
               phase: Callable[[dict], None] | None = None,
               should_cancel: Callable[[], bool] | None = None,
               target: Target = EPS):
    self.t = transport
    self.target = target
    # DATA pacing (BLR: <= data_fps_max). 0 = none: the EPS goes at the echo's pace.
    self._data_gap = 1.0 / target.data_fps_max + DATA_PACE_MARGIN_S if target.data_fps_max else 0.0
    self._t_data = -1e9
    self._nak: int | None = None       # the last _expect_ack's NAK code, if it was one
    self.log = log
    self.progress = progress
    # What the screen shows (push9): {"p": phase, "cancel": the Cancel button is
    # live, "left": countdown seconds, "pct": write progress}. should_cancel is
    # the page's Cancel, polled at most every CANCEL_POLL_S. Both best effort:
    # a callback that throws costs the screen, never the flash.
    self.phase_cb = phase
    self.should_cancel = should_cancel
    self._cancel_next = 0.0
    self._cancel_seen = False
    # The gate saw 0x17C (run_flash sets it): from then on, up to BEGIN, its
    # disappearance is a stop (ENGINE_LOST_S). False on the bench, where there
    # was none from the start. And the panda's ignition, read like the Cancel.
    self.engine_seen = False
    self._ign_next = 0.0
    self._ign_off = False
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
      if kind != ECHO and addr in self.target.trace_ids:
        # Timestamp and raw bytes only. No decoding in the receive path, and
        # no opendbc import in a module that must load on a bench.
        self._trace.append((time.monotonic(), addr, data))
        continue
      if kind != ECHO and addr not in (self.target.id_board, ENGINE_DATA_ID):
        continue                      # ordinary car traffic; see __init__
      self._rx.append((addr, data, kind))

  def _wire_id(self, can_id: int) -> int:
    """v1's channel name -> this target's identifier: ID_HOST and ID_DATA are
    themselves for the EPS, 0x628/0x62A for the BLR. Nothing else is ever sent
    from here, and anything else is refused (run_flash turns it into a string)."""
    if can_id == ID_HOST:
      return self.target.id_host
    if can_id == ID_DATA:
      return self.target.id_data
    raise ValueError(f"{can_id:#05x} is not a bootloader channel")

  def _send_raw(self, can_id: int, payload: bytes) -> None:
    """Fire and forget. For ENTER (racing the 800 ms boot window) and the knock
    (after which the board resets and may stop ACKing), waiting for an echo
    costs more than the flow control is worth."""
    self.t.send(self._wire_id(can_id), payload)

  def _send(self, can_id: int, payload: bytes, echo_timeout: float = 1.5) -> str | None:
    """Send, then wait for one frame to come back.

    THE ECHO IS THE FLOW CONTROL. Both transports hand a frame back only once it
    has actually been transmitted, so waiting for one throttles this to the speed
    of the wire instead of the speed of USB - and on a gs_usb adapter that is not
    optional, because it holds frames in about three echo slots and silently
    stops accepting them when they are full. Firing a 34-frame chunk at one puts
    two frames on the wire and discards the rest, with no error anywhere.
    """
    can_id = self._wire_id(can_id)
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

  def _send_data(self, payload: bytes) -> str | None:
    """One DATA frame. A paced target (the BLR) first waits out the gap since
    the previous one, receiving - and so tracing - while it waits, on top of the
    echo wait. Unpaced (the EPS) it is exactly _send, at the echo's pace."""
    if self._data_gap > 0:
      while (left := self._t_data + self._data_gap - time.monotonic()) > 0:
        self._pump(left)
      self._t_data = time.monotonic()
    return self._send(ID_DATA, payload)

  def _recv(self, timeout: float, want: int | None = None) -> bytes | None:
    end = time.monotonic() + timeout
    while True:
      for item in list(self._rx):
        addr, data, kind = item
        if kind == ECHO or addr != self.target.id_board:
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
    self._nak = d[2] if d is not None and d[0] == RSP_NAK and len(d) > 2 else None
    if d is None:
      return f"no ACK for {CMD_NAMES.get(cmd, cmd)} within {timeout}s"
    if d[0] == RSP_NAK:
      return f"board refused {CMD_NAMES.get(d[1], d[1])}: {describe_nak(d, self.target.errs)}"
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
    self._send_raw(ID_HOST, bytes([CMD_ENTER]) + self.target.magic)

  def wrong_board(self, d: bytes, what: str) -> str | None:
    """None, unless this target checks the board class (the BLR: HELLO[7] and
    the ENTER ACK's [7]) and `d` - a HELLO or that ACK - carries another. Then
    whatever answered is not this board, and the caller ABORTs before a byte of
    image is sent. The EPS bootloader sends 0 there and is not checked."""
    want = self.target.hello_class
    if want is None:
      return None
    got = d[7] if len(d) > 7 else None
    if got == want:
      return None
    return (f"wrong board answered on {self.target.id_board:#05x}: {what} byte 7 is {got}, " +
            f"the {self.target.words['board']} says {want} - nothing was written")

  def wait_hello(self, timeout: float, entered: bool = False) -> bytes | None:
    """The post-reset HELLO, or None.

    A NAK seen here is returned too, and run_flash reports it. The board NAKs a
    knock it refuses - "the car is not stationary" - and swallowing that meant
    waiting the full timeout and then blaming the board for having no
    bootloader, which is the single most misleading thing this tool could say.

    entered (the BLR, on its knock only - Target.knock_enters): an ENTER ACK is
    returned too. A bootloader that was already running took the knock as the
    ENTER it is, and its session is open; there will be no HELLO until that
    session times out 10 s later. The EPS never passes it: it waits for the
    HELLO, exactly as it always has.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      d = self._recv(0.2)
      if d is not None and (d[0] in (RSP_HELLO, RSP_NAK) or
                            (entered and d[0] == RSP_ACK and len(d) > 2 and d[1] == CMD_ENTER)):
        return d
    return None

  def enter(self, timeout: float = 4.0) -> str | None:
    """Take the bootloader, inside its 800 ms listen window.

    Fired repeatedly without waiting for echoes: the window is finite and every
    round trip spent being careful is window spent.
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      self._send_raw(ID_HOST, bytes([CMD_ENTER]) + self.target.magic)
      d = self._recv(0.05, want=RSP_ACK)
      if d is not None:
        if d[0] == RSP_NAK:
          return f"refused: {describe_nak(d, self.target.errs)}"
        if d[1] == CMD_ENTER:
          if (e := self.wrong_board(d, "ENTER ACK")):
            self.abort()                # a session is open on whatever that was: close it
            return e
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

  def abort(self, ack_s: float = 0.5) -> None:
    """Close the session rather than leaving it to time out.

    An idle session self-resets after 10 s. That reset lands wherever it lands -
    it has already interrupted an SWD programming run and shown up elsewhere as
    the board apparently rebooting on its own.

    Sent with its echo awaited - the frame has left the panda before this
    returns, so the transport's close() (can_clear) cannot drop it - and then
    the bootloader's ACK (boot_main.c ACKs ABORT before it jumps back to the
    app) for up to `ack_s`. Best effort: never raises, and a missing ACK only
    means the board falls back on its 10 s session timeout, which also runs the
    installed firmware.
    """
    try:
      self._drain()
      if (e := self._send(ID_HOST, bytes([CMD_ABORT]))) is None and ack_s > 0:
        e = self._expect_ack(CMD_ABORT, timeout=ack_s)
      if e:
        self.log(f"  ABORT: {e} - the board's session timeout will end it")
    except Exception as e:
      self.log(f"  ABORT not confirmed: {type(e).__name__}: {e}")

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

  def report(self, p: str, cancel: bool = False, **kw) -> None:
    """Tell the screen which phase this is. Never raises."""
    if self.phase_cb is None:
      return
    try:
      self.phase_cb({"p": p, "cancel": cancel, **kw})
    except Exception:
      pass

  def cancelled(self, now_or_never: bool = False) -> bool:
    """The page's Cancel, read at most every CANCEL_POLL_S (always, with
    now_or_never: the last look before BEGIN). Latched: once seen it stays seen
    for this run. Never raises - a param that cannot be read is no Cancel."""
    if self._cancel_seen or self.should_cancel is None:
      return self._cancel_seen
    now = time.monotonic()
    if now_or_never or now >= self._cancel_next:
      self._cancel_next = now + CANCEL_POLL_S
      try:
        self._cancel_seen = bool(self.should_cancel())
      except Exception:
        self._cancel_seen = False
    return self._cancel_seen

  def engine_rpm(self, since: float) -> int | None:
    """Median 0x17C ENGINE_RPM traced since `since` (monotonic), or None if none
    was seen. Read from the trace: 0x17C never reaches _rx."""
    v = []
    for t, a, d in reversed(self._trace):     # newest first: stop at `since`
      if t < since:
        break
      if a == ENGINE_RPM_ID and (r := decode_engine_rpm(d)) is not None:
        v.append(r)
    v.sort()
    return v[len(v) // 2] if v else None

  def listen(self, seconds: float, cancellable: bool = False) -> bool:
    """Keep receiving - and so keep tracing - without sending anything. True if
    the page's Cancel came (only when cancellable)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
      self._pump(0.05)
      if cancellable and self.cancelled():
        return True
    return False

  def hold(self, seconds: float) -> str | None:
    """Keep the bootloader session open with no data flowing - the countdown.

    The knock and the data stream used to be 1.7 s apart, and the vibration
    started with the data stream - close enough that one cannot be told from
    the other. This puts seconds between them. The board sits in bypass the
    whole time, which is stock wiring. INFO is the keep-alive: any command
    refreshes the bootloader's 10 s session timer (boot_main.c).

    The screen shows it as "relays in stock - updating in 5..1", with Cancel.
    Cancel is ABORT: the bootloader closes the session and, its listen window
    long past, jumps straight back to the firmware it has. BEGIN has not been
    sent, so bank 2 is untouched and nothing has changed - the existing
    --dry-run path. Returns CANCELLED then. Cancel is offered until
    CANCEL_LAST_S before the end, and read once more, unthrottled, after it.

    The engine stays off (owner, 2026-10-13): 0x17C over the last
    ENGINE_WINDOW_S, every loop and once more at the end. An engine started
    during the click, the HELLO wait or the countdown is ABORT and
    ENGINE_RUNNING - still before BEGIN, so nothing has changed. So is the
    gate going blind (push9 re-review): 0x17C seen at the gate and gone for
    ENGINE_LOST_S is NO_ENGINE_DATA, the panda's ignition off is IGNITION_OFF.
    """
    start = time.monotonic()
    end = start + seconds
    next_ping = start + HOLD_PING_S
    shown = None
    while (now := time.monotonic()) < end:
      left = max(1, int(-(-(end - now) // 1)))   # 5, 4, 3, 2, 1
      can = end - now > CANCEL_LAST_S
      if (left, can) != shown:
        shown = (left, can)
        self.report("stock", cancel=can, left=left)
      self._pump(0.05)
      if (e := self._hold_stop()):
        return e
      if time.monotonic() >= next_ping:
        next_ping += HOLD_PING_S
        if self.info() is None:
          return "the bootloader stopped answering during the hold"
    # the last look, immediately before BEGIN: nothing throttled
    return self._hold_stop(last=True)

  def _hold_stop(self, last: bool = False) -> str | None:
    """In the countdown: the page's Cancel, the engine started, the ignition
    off, or the 0x17C the gate saw gone. Each is ABORT (the installed image
    stays) and the reason; None to go on. `last`: the look right before BEGIN,
    nothing throttled."""
    if self.cancelled(now_or_never=last):
      self.abort()
      return CANCELLED
    now = time.monotonic()
    rpm = self.engine_rpm(now - ENGINE_WINDOW_S)
    if rpm is not None and rpm > ENGINE_OFF_MAX_RPM:
      self.log(f"  engine started before the erase: {rpm} rpm - ABORT, the installed image stays")
      self.abort()
      return ENGINE_RUNNING
    if self.ignition_off(now_or_never=last):
      self.log("  the ignition went off before the erase - ABORT, the installed image stays")
      self.abort()
      return IGNITION_OFF
    if self.engine_seen and self.engine_rpm(now - ENGINE_LOST_S) is None:
      self.log(f"  0x17C gone for {ENGINE_LOST_S:.1f} s before the erase, the engine-off gate is blind - ABORT")
      self.abort()
      return NO_ENGINE_DATA
    return None

  def ignition_off(self, now_or_never: bool = False) -> bool:
    """The transport's ignition reading (the panda's) says OFF - read at most
    every CANCEL_POLL_S (always, with now_or_never). False when the transport
    has none (the bench adapter) or it cannot be read (None), as at the gate.
    Never raises."""
    read = getattr(self.t, "ignition", None)
    if read is None:
      return False
    now = time.monotonic()
    if now_or_never or now >= self._ign_next:
      self._ign_next = now + CANCEL_POLL_S
      try:
        self._ign_off = read() is False
      except Exception:
        self._ign_off = False
    return self._ign_off

  def program(self, image: bytes) -> str | None:
    n = len(image)
    chunks = (n + CHUNK_BYTES - 1) // CHUNK_BYTES

    self.report("erase")
    self._drain()
    if (e := self._send(ID_HOST, bytes([CMD_BEGIN]) + struct.pack("<I", n))):
      return e
    if (e := self._expect_ack(CMD_BEGIN, timeout=20.0)):     # erases all of bank 2
      return e
    self.mark("data")
    self.report("write", pct=0)

    last_pct = -1
    for idx in range(chunks):
      body = image[idx * CHUNK_BYTES:(idx + 1) * CHUNK_BYTES]
      for attempt in range(3):
        self._drain()
        if (e := self._send(ID_HOST, bytes([CMD_CHUNK]) + struct.pack("<H", idx))):
          return e
        for off in range(0, len(body), DATA_PER_FRAME):
          if (e := self._send_data(body[off:off + DATA_PER_FRAME])):
            return e
        if (e := self._send(ID_HOST, bytes([CMD_CEND]) + struct.pack("<HH", idx, crc16_ccitt(body)))):
          return e
        err = self._expect_ack(CMD_CEND, timeout=3.0)
        if err is None:
          break
        retry = self.target.retry_naks
        if retry is not None and self._nak is not None and self._nak not in retry:
          return f"chunk {idx}: {err}"          # the board said why, and it is not a lost frame (BLR NAK 8...)
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
        self.report("write", pct=pct)

    self.mark("finish")
    self.report("install")
    crc = zlib.crc32(image) & 0xFFFFFFFF
    finish = bytes([CMD_FINISH]) + struct.pack("<I", crc)
    self._drain()
    if (e := self._send(ID_HOST, finish)):
      return e
    wait = self.target.finish_retry_s
    if wait is None:
      return self._expect_ack(CMD_FINISH, timeout=20.0)      # the EPS: one FINISH, as proven in the car
    # The BLR (BLR_FINISH_ACK_S): silence is asked about once more, inside the session. Not drained first, so an
    # ACK that arrives late still counts; a NAK is the board's answer and is never asked again.
    if (e := self._expect_ack(CMD_FINISH, timeout=wait)) is None or "no ACK" not in e:
      return e
    self.log(f"  {e} - FINISH again (the board ACKs a repeated FINISH and writes nothing twice)")
    if (e := self._send(ID_HOST, finish)):
      return e
    return self._expect_ack(CMD_FINISH, timeout=wait)

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

    A target that does not trace the steering (the BLR) gets _summarise_blr()
    instead: the same phase table, with the lamp board's own state and the EPS
    board's forwarding ring under the update's burst.
    """
    if 0x156 not in self.target.trace_ids:
      return self._summarise_blr()
    ang: list[tuple[float, float, float]] = []
    tq: list[tuple[float, int, bytes]] = []
    rpm: list[tuple[float, int]] = []
    load: list[tuple[float, int]] = []
    grant: list[tuple[float, tuple[int, int, int, int]]] = []
    for t, addr, data in self._trace:
      if addr == GRANT_ID:
        if (g := decode_grant(data)) is not None:
          grant.append((t, g))
      elif addr == 0x156:
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
      "phases": self._phase_table(eps_grid(tq), rpm, load, ang, grant),
      "series": series,
    }

  def _summarise_blr(self) -> dict:
    """The summary of a BLR update, which traces no steering: what each phase did
    to the car bus and to both boards. Per phase, beside rpm, the load bit, the
    census and the error deltas: the lamp board's last 0x620 STATE `bst` (1 idle,
    7 knock; none while its bootloader runs) and `bout`, whether its output drove
    at all; the EPS board's 0x70B `st`/`eer`/`cam`, and from its 0x706 the
    forwarding-ring peak `ring`, any drop/overflow flag `sdrop` and its FAULT
    `sflt` (non-zero: it fell back to bypass under the burst). "verdict" is the
    line that matters for the lamps: was the output ever on during the update.
    {} when nothing was traced, as for the EPS."""
    if not self._trace:
      return {}
    rpm: list[tuple[float, int]] = []
    load: list[tuple[float, int]] = []
    grant: list[tuple[float, tuple[int, int, int, int]]] = []
    lamp: list[tuple[float, int, int]] = []
    split: list[tuple[float, int, int, int]] = []
    for t, addr, data in self._trace:
      if addr == GRANT_ID:
        if (g := decode_grant(data)) is not None:
          grant.append((t, g))
      elif addr == ENGINE_RPM_ID:
        if (v := decode_engine_rpm(data)) is not None:
          rpm.append((t, v))
      elif addr == LOAD_ID:
        if (v := decode_load_bit(data)) is not None:
          load.append((t, v))
      elif addr == BLR_STATUS_ID and len(data) >= 1:
        lamp.append((t, data[0] & 0x7, (data[0] >> 3) & 1))
      elif addr == EPS_SPLIT_ID and len(data) >= 5:
        split.append((t, data[0], data[4] & 0x1F, (data[4] >> 5) & 0x7))
    if not lamp:
      verdict = "no lamp-board status seen"
    elif any(out for _, _, out in lamp):
      verdict = "LAMP OUTPUT ON DURING THE UPDATE"
    else:
      verdict = "lamp output off throughout"
    return {
      "n": len(self._trace),
      "d": round(self._trace[-1][0] - self._trace[0][0], 2),       # seconds traced, as a phase row's "d"
      "verdict": verdict,
      "phases": self._phase_table([], rpm, load, [], grant, lamp, split),
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
    own = self.target.census_own
    lost = {}
    for addr, n in pre.items():
      if addr in own or n / pre_dur < CENSUS_MIN_HZ:
        continue
      ratio = (here.get(addr, 0) / dur) / (n / pre_dur)
      if ratio < CENSUS_LOST:
        lost[f"{addr:x}"] = round(ratio, 2)
    new = sorted(f"{addr:x}" for addr, n in here.items()
                 if addr not in pre and addr not in own and n >= 3)
    if lost:
      row["lost"] = dict(sorted(lost.items(), key=lambda kv: kv[1])[:8])
    if new:
      row["new"] = new[:8]

  def _phase_table(self, grid, rpm, load, ang, grant=(), lamp=(), split=()) -> list[dict]:
    """One row per phase. With the board's 0x70B in the phase (its firmware is
    running: `pre`, `post`): `st` its last STATE/REASON ("8/16" = K2 at stock
    waiting for the comma, "0/7" = split, standstill), `eer` the highest
    EPS_ERROR_STATE and `cam` whether the camera's LKAS_ON was ever set.
    A BLR update adds the lamp board's 0x620 and the EPS board's 0x706
    (_summarise_blr); an EPS update has neither, and its rows are unchanged."""
    marks = sorted(self._marks)
    if len(marks) < 2:
      return []
    base = marks[0][0]
    health = dict(self._health)
    out = []
    for (a, label), (b, _next) in zip(marks, marks[1:], strict=False):
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
      gr = [g for t, g in grant if a <= t < b]
      if gr:
        row["st"] = f"{gr[-1][0]}/{gr[-1][1]}"
        row["eer"] = max(g[2] for g in gr)
        row["cam"] = int(any(g[3] for g in gr))
      lp = [(s, o) for t, s, o in lamp if a <= t < b]
      if lp:
        row["bst"] = lp[-1][0]
        row["bout"] = int(any(o for _, o in lp))
      sp = [x for x in split if a <= x[0] < b]
      if sp:
        row["ring"] = max(x[1] for x in sp)
        row["sdrop"] = int(any(x[2] for x in sp))
        row["sflt"] = max(x[3] for x in sp)
      self._census_row(row, a, b - a, marks[0][0])
      ha, hb = health.get(a) or {}, health.get(b) or {}
      for bus in (0, 1, 2):
        ea = (ha.get(bus) or {}).get("total_error_cnt")
        eb = (hb.get(bus) or {}).get("total_error_cnt")
        if ea is not None and eb is not None:
          row[f"e{bus}"] = eb - ea
      out.append(row)
    return out

  def save_trace(self, directory: str | None = None) -> str | None:
    """Write the trace out - to the target's trace_dir unless told otherwise
    (TRACE_DIR for the EPS). Returns the path, or None.

    Best effort in the strongest sense: this runs inside pandad's wrapper, so
    a full disk or a read-only mount must cost nothing but the trace itself.
    """
    if not self._trace:
      return None
    try:
      if directory is None:
        directory = self.target.trace_dir
      os.makedirs(directory, exist_ok=True)
      path = os.path.join(directory, f"flash-{int(time.time())}.csv")  # noqa: TID251 - wall clock: a file name kept across boots
      t0 = self._trace[0][0]
      with open(path, "w") as fh:
        fh.write(self.target.words["trace_header"])
        fh.write("t,addr,data\n")
        for t, addr, data in self._trace:
          fh.write(f"{t - t0:.4f},{addr:#05x},{data.hex()}\n")
      return path
    except Exception:
      return None


def describe_hello(d: bytes, target: Target = EPS) -> dict:
  """HELLO is the only frame a board with an empty slot ever sends. A BLR's also
  says which VARIANT is installed (status bits 5:4), its board class (byte 7),
  and whether this boot repaired a flash page that failed ECC (bit 6), which
  cancels a staged image."""
  h = {"proto": d[1], "appOk": bool(d[2] & HELLO_APP_OK),
       "appId": bool(d[2] & HELLO_APP_ID), "dirty": bool(d[2] & HELLO_APP_DIRTY),
       "staged": bool(d[2] & HELLO_STAGED),
       "git": f"{int.from_bytes(d[3:7], 'little'):08x}"}
  if target.hello_class is not None:
    h["variant"] = BLR_VARIANT_NAMES.get(marker_variant(d[2]), "invalid")
    h["cls"] = d[7] if len(d) > 7 else None
    h["eccFixed"] = bool(d[2] & BLR_HELLO_ECC_FIXED)
  return h


def run_flash(transport, image: bytes, log: Callable[[str], None] = print,
              progress: Callable[[int], None] | None = None,
              dry_run: bool = False, knock: bool = True,
              hello_timeout: float = 25.0,
              trace_out: Callable[[dict], None] | None = None,
              phase: Callable[[dict], None] | None = None,
              should_cancel: Callable[[], bool] | None = None,
              require_engine_data: bool | None = None,
              target: Target = EPS) -> tuple[bool, str]:
  """Do the whole thing, on `target`'s board. Returns (ok, message). NEVER RAISES.

  phase / should_cancel (push9) drive the screen: the phases in order are
  check, baseline, click, stock (the countdown), erase, write, install,
  restart. Cancel is taken in check, baseline (nothing has been sent) and
  stock (ABORT before BEGIN: the old image stays); from the erase on there is
  no cancel. A cancelled run returns (False, CANCELLED).

  The engine is off for the whole of it up to BEGIN: before the knock, at the
  end of the baseline and through the countdown (ENGINE_RUNNING, with ABORT
  once the bootloader has the session). require_engine_data: no 0x17C at all
  is refused (NO_ENGINE_DATA) - by default whenever the transport reads the
  ignition (the panda, in the car); the bench adapter has no car to see. The
  countdown also stops (ABORT) on the ignition reading off (IGNITION_OFF) and
  on 0x17C, seen at the gate, gone for ENGINE_LOST_S (NO_ENGINE_DATA); with
  none from the start (the bench) it goes on as before.

  NOTHING NEW REACHES THE BOARD. The gate, the countdown and the cancel are all
  on this side; the frames on the wire are the ones every board since the
  first bootloader understands, so an old board updates exactly as before.

  THE BLR (target=BLR) runs this same sequence - the gate, the countdown, the
  cancel points and the engine-off rule unchanged - on its own IDs and magic,
  with these additions: HELLO and the ENTER ACK must carry its board class
  (anything else is ABORT, "wrong board answered", before a byte of image),
  DATA is paced to <= 400 frames/s, and the post-install HELLO must report
  the image's VARIANT as well as its commit (shadow and live of one commit
  share a hash). And, as its own host tool does: a resident bootloader's
  ENTER ACK to the knock is the session, INFO must say 112 KB (else ABORT),
  and a lost FINISH ACK is asked about once more. The phases are the same
  names; the screen words are the target's.
  """
  f = None
  try:
    if (why := check_app_slot_image(image, target)):
      return False, f"refusing this image: {why}"

    ident = image_identity(image, target)
    marks = [k for k in ("dirty", "readOnly") if ident[k]]
    log(f"  image {len(image)} bytes, commit {ident['git']}"
        + (f" ({ident['variant']})" if "variant" in ident else "")
        + (f" [{', '.join(marks)}]" if marks else ""))
    if ident["dirty"]:
      log(target.words["dirty"])

    f = Flasher(transport, log=log, progress=progress, phase=phase, should_cancel=should_cancel, target=target)
    log(f"  {transport.describe()}")
    f.mark("start")
    f.report("check", cancel=True)

    # THE GATE, before a single frame is sent: ignition on, parked, engine off.
    ignition = getattr(transport, "ignition", None)
    if ignition is not None and ignition() is False:
      return False, IGNITION_OFF

    t_check = time.monotonic()
    if f.moving():
      return False, "the car is moving - stop first"
    if f.cancelled():
      return False, CANCELLED
    rpm = f.engine_rpm(t_check)
    if require_engine_data is None:
      # in the car (the panda, which reads the ignition) the gate must see the
      # engine; the bench adapter has no car to see
      require_engine_data = ignition is not None
    if rpm is None and require_engine_data:
      log("  no 0x17C on the car bus - the engine-off gate is blind; refused")
      return False, NO_ENGINE_DATA
    if rpm is None:
      # the bench: nothing to watch through the countdown either (engine_seen
      # stays False) - its absence there is not news
      log("  no 0x17C on the bus - engine state unknown (bench); allowed")
    elif rpm > ENGINE_OFF_MAX_RPM:
      log(f"  engine running: {rpm} rpm")
      return False, ENGINE_RUNNING
    # seen at the gate: the countdown stops if it goes (ENGINE_LOST_S)
    f.engine_seen = rpm is not None

    if knock:
      f.report("baseline", cancel=True)
      if f.listen(PRE_CAPTURE_S, cancellable=True):
        return False, CANCELLED
      # and still off at the end of the baseline - the last second of it
      if (rpm := f.engine_rpm(time.monotonic() - 1.0)) is not None and rpm > ENGINE_OFF_MAX_RPM:
        log(f"  engine started during the baseline: {rpm} rpm")
        return False, ENGINE_RUNNING
      log("  knocking: asking the application to reboot into its bootloader")
      f.report("click")
      f.mark("knock")
      f.knock()

    hello = f.wait_hello(hello_timeout, entered=target.knock_enters and knock)
    f.mark("hello")
    if hello is None:
      return False, target.words["no_hello"]
    if hello[0] == RSP_NAK:
      return False, f"refused: {describe_nak(hello, target.errs)}"
    # the BLR only (knock_enters): a bootloader already running took the knock as ENTER - its session is open
    entered = hello[0] == RSP_ACK
    if (e := f.wrong_board(hello, "ENTER ACK" if entered else "HELLO")):
      f.abort()                         # its 800 ms window ends, whatever it was goes back to its app
      return False, e
    if entered:
      if hello[2] != PROTO_VERSION:
        f.abort()
        return False, f"board speaks protocol v{hello[2]}, this speaks v{PROTO_VERSION}"
      # no HELLO to read: a bootloader that never jumped has nothing valid installed, or it reset just now
      h = {"git": "unknown"}
      log("  the bootloader was already running and took the knock as ENTER: in session, no HELLO to read")
    else:
      h = describe_hello(hello, target)
      log(f"  HELLO v{h['proto']}: installed {h['git']}, appOk={h['appOk']}"
          + (f", {h['variant']}" if "variant" in h else "")
          + (", ECC page repaired this boot" if h.get("eccFixed") else ""))

      if (e := f.enter()):
        return False, e

    slot = f.info()
    if slot is None:
      return False, "no INFO reply"
    log(f"  app slot {slot[0]} KB, current contents CRC32 {slot[1]:#010x}")
    if target.check_slot and slot[0] * 1024 != target.slot_bytes:
      # blr_can_update.py: another slot size is another bootloader than the one this image was checked for
      f.abort()
      return False, (f"the board reports a {slot[0]} KB app slot, not {target.slot_bytes // 1024} KB - " +
                     "not the bootloader this image is for; nothing was written")

    # The countdown starts on the INFO reply: HELLO already proved the board
    # reset with its relays de-energised (the bootloader drives PA0/PA1 low at
    # its first instruction and says HELLO only after the 15 ms settle).
    f.mark("hold")
    if (e := f.hold(HOLD_BEFORE_DATA_S)):
      if e == CANCELLED:
        log("  cancelled in the countdown: ABORT sent, the installed image stays")
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
    f.report("restart")
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
      return False, f"refused: {describe_nak(after, target.errs)}"
    if (e := f.wrong_board(after, "HELLO")):
      return False, e
    a = describe_hello(after, target)
    # a BLR bootloader that repaired an ECC page on this boot cancelled the staged image with it (BOOT-2)
    ecc = " - its bootloader repaired a flash page this boot, which cancels a staged image" if a.get("eccFixed") else ""
    if not a["appOk"]:
      return False, ("installed, but the board refuses to run it " +
                     f"(HELLO appOk=0, appId={a['appId']})" + ecc)
    if a["git"] != ident["git"]:
      return False, f"board reports {a['git']} after installing {ident['git']}" + ecc
    if "variant" in ident and a.get("variant") != ident["variant"]:
      return False, f"board reports a {a.get('variant')} image after installing {ident['variant']}" + ecc
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
        log(f"  {target.words['trace']}: {len(f._trace)} frames -> {p}")
      # The caller decides where this goes. The hook puts it in a param,
      # because a file on /data needs SSH and the answer has to reach someone
      # who does not have it.
      if trace_out is not None:
        try:
          trace_out(f.summarise_trace())
        except Exception:
          pass


def load_bundled_image(target: Target = EPS) -> tuple[bytes | None, str]:
  """(image, "") - or (None, why) when `target` has no bundled image, which for
  the BLR is the normal state until one is bundled: nothing to offer, no update."""
  if not os.path.exists(target.image_path):
    return None, f"no bundled image at {target.image_path}"
  with open(target.image_path, "rb") as fh:
    return fh.read(), ""


def main(argv: list[str] | None = None) -> int:
  import argparse
  ap = argparse.ArgumentParser(description=__doc__)
  ap.add_argument("--target", choices=sorted(TARGETS), default=EPS.name,
                  help="eps: the EPS-LKAS gateway (default); blr: the BLR-v1 brake-lamp board")
  ap.add_argument("--bin", default=None, help="image to install (default: the target's bundled one)")
  ap.add_argument("--bench", action="store_true", help="candleLight adapter instead of the panda")
  ap.add_argument("--bus", type=int, default=0, help="0 car, 2 camera (EPS only). NEVER 1.")
  ap.add_argument("--dry-run", action="store_true", help="reach the bootloader and stop")
  ap.add_argument("--no-knock", action="store_true", help="wait for a manual reset instead")
  a = ap.parse_args(argv)
  target = TARGETS[a.target]

  if target is BLR and a.bus != 0:
    print("the BLR-v1 lamp board is on bus 0 only")
    return 2
  if a.bus == 1:
    print("bus 1 has no transceiver on this board - use 0 (car) or 2 (camera)")
    return 2

  if a.bin:
    with open(a.bin, "rb") as fh:
      image = fh.read()
  else:
    image, err = load_bundled_image(target)
    if image is None:
      print(err)
      return 2

  transport = BenchTransport() if a.bench else PandaTransport(bus=a.bus)
  try:
    ok, msg = run_flash(transport, image, dry_run=a.dry_run, knock=not a.no_knock, target=target,
                        progress=lambda p: print(f"\r  {p:3d}%", end="", flush=True))
  finally:
    transport.close()
  print()
  print(("  OK: " if ok else "  FAILED: ") + msg)
  return 0 if ok else 1


if __name__ == "__main__":
  raise SystemExit(main())
