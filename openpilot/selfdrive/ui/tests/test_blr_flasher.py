"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

BLR-v1, the brake-lamp relay board (DESIGN E.1, E.6): the flasher's second target. The board runs the EPS board's v1
bootloader protocol with every identity value changed (blr/inc/blr_boot_proto.h in the EPS-LKAS repo), so the same
run_flash serves both - and an update of one must never reach the other. This file pins the BLR target:

  constants   the design's literals always; the firmware headers too, whenever the firmware tree is reachable
  images      APL1 and BLR1 refused on each other's board, the BLR bootloader record, bench, dirty, VARIANT 0, the
              bootloader's own vector rules, the image's own length/CRC-32 trailer (review round 1, BOOT-6); and the
              bundled blr_appslot.bin, skipped while none is bundled
  the wire    run_flash against BlrSim - the BLR bootloader as blr_boot_main.c implements it and the app's knock as
              BLR-CAN.md section 4 states it, with the car and the EPS board around it, on a fake clock: a full
              update, every cancel point, the engine gate, the class check, pacing, NAK 5/8/9/10, lost frames, a
              resident bootloader, the VARIANT read back after the install, a truncated image refused at FINISH, a
              boot that repaired an ECC page (BOOT-2), and the trace

The EPS side is pinned elsewhere, unchanged: test_eps_lkas_flasher.py, including test_eps_wire_transcript_is_frozen.
"""
import ast
import dataclasses
import io
import os
import random
import re
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
PANDAD = ROOT / "sunnypilot/selfdrive/pandad"
FLASHER = PANDAD / "eps_lkas_flasher.py"
EPS_IMAGE = PANDAD / "eps_lkas_appslot.bin"
BLR_IMAGE = PANDAD / "blr_appslot.bin"

# The firmware tree, when it is reachable (the owner's machine, from WSL): the EPS-LKAS repo's blr/.
FIRMWARE_BLR = [Path(p) for p in (os.environ.get("BLR_FIRMWARE_DIR", ""),
                                  "/mnt/s/Software/EPS-LKAS/blr",
                                  "/mnt/s/Software/EPS-LKAS/.claude/worktrees/blr/blr") if p]

APP_ORIGIN = 0x08004000
SLOT = 112 * 1024
BLR1, APL1, BLB1 = 0x31524C42, 0x314C5041, 0x31424C42
BLRT = 0x54524C42                     # the app image's trailer (blr_boot_proto.h BLR_TRAILER_MAGIC)
KNOCK = bytes([0x01]) + b"BLRBOOT"


def _module():
  """Import the flasher the way the EPS tests do: from its file, NOT registered in sys.modules. That is also what
  proves the Target dataclass imports there (dataclasses resolves string annotations through sys.modules)."""
  import importlib.util
  spec = importlib.util.spec_from_file_location("eps_lkas_flasher", FLASHER)
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def make_image(magic=BLR1, variant=2, git=0x1234ABCD, dirty=0, size=4 * 1024 + 4, origin=APP_ORIGIN,
               sp=0x20024000, pc=None, flags=None, seed=1, trailer=True) -> bytes:
  """An app-slot image: vectors, the 16-byte marker at 0x100, random bytes - `size` of them, which is not a multiple
  of 8 by default, as an objcopy .bin is not - and then, as the BLR post-build seals every app .bin (review round 1,
  BOOT-6), sealed(): 0xFF to a double word and the 12-byte trailer. The last chunk is still short. trailer=False is
  the bare objcopy output, and every EPS image."""
  rnd = random.Random(seed)
  d = bytearray(rnd.randrange(256) for _ in range(size))
  struct.pack_into("<II", d, 0, sp, APP_ORIGIN + 0x201 if pc is None else pc)
  f = (dirty & 1) | (variant << 4) if flags is None else flags
  struct.pack_into("<4I", d, 0x100, magic, origin, git, f)
  return sealed(bytes(d)) if trailer else bytes(d)


def sealed(body: bytes) -> bytes:
  """blr_hostlib.add_trailer(), typed in again: 0xFF to a double word, then {BLRT, the length INCLUDING these 12
  bytes, CRC-32 of every byte before them} (blr_boot_proto.h, the image trailer)."""
  body = bytes(body) + b"\xff" * ((-(len(body) + 12)) % 8)
  return body + struct.pack("<III", BLRT, len(body) + 12, zlib.crc32(body) & 0xFFFFFFFF)


def eps_image(git=0x0EB50001, flags=0, size=4 * 1024 + 4, seed=7) -> bytes:
  return make_image(magic=APL1, git=git, flags=flags, size=size, seed=seed, trailer=False)


class FakeTime:
  """The flasher's clock. Only BlrSim.poll() moves it."""
  def __init__(self):
    self.now = 1000.0

  def monotonic(self):
    return self.now

  def time(self):
    return 1.7e9 + self.now

  def sleep(self, s):
    self.now += s


def crc16(data: bytes) -> int:
  """CRC-16/CCITT-FALSE, typed in again (blr_boot_main.c crc16())."""
  c = 0xFFFF
  for b in data:
    c ^= b << 8
    for _ in range(8):
      c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
  return c


class BlrSim:
  """The BLR-v1 on bus 0, as the flasher's transport sees it, on a fake clock.

  THE BOOTLOADER is blr_boot_main.c's protocol core: a 0x628 frame is acted on only if it is a known command with at
  least its bytes and zeros after them (anything else: silence), and only once listen-before-talk has passed (HELLO
  `lbt_s` after the reset; nothing before it); ENTER is refused moving (NAK 5) and ACKed `80 01 01 00 00 00 00 02`;
  out of session every command but ENTER/REBOOT/ABORT is NAK 1; any BEGIN ends the previous transfer, NAK 9 not
  dual-bank, NAK 8 (and the session and window closed) below 0x110 bytes, NAK 2 above the slot, else ACK after the
  bank-2 erase; CEND checks sequence, the image end, SHORT, the CRC-16, then the vectors at chunk 0 and the BLR1
  marker at chunk 1 (NAK 8, session closed), a chunk already in flash is ACKed again; FINISH checks the CRC-32, the
  whole image read back from staging and its own trailer (NAK 8, session closed); REBOOT ACKs and resets; ABORT ACKs
  and ends the window; a session dies 10 s after its last frame. The reset first repairs a flash page that failed ECC
  on the boots in `ecc_boots` - the staging header erased first, so a staged image is cancelled - then copies a
  staged image only if it still passes every check, the trailer among them, then HELLO
  `83 01 <status> <git LE> <class>` once (status bit 6 if it repaired a page), then 800 ms for ENTER, then the app -
  or, with nothing valid in the slot, resident for good.

  THE APP takes exactly `01 42 4C 52 42 4F 4F 54` on 0x628 as the knock: ignored in INIT, NAK 5 not stationary, NAK 10
  with the lamp output on, otherwise a 0x620 STATE 7 and a reset. It sends 0x620 at 10 Hz and 0x627 at 1 Hz.

  AROUND IT: 0x158 and 0x17C at 100 Hz, and the EPS board's 0x706 (1 Hz) and 0x70B (10 Hz).

  `cls` / `ack_cls` impersonate a board that is not a BLR; `drop_rx(id, data)` loses a host frame on the wire,
  `drop_tx(data)` one of the board's replies; `fail_copy` makes the boot copy refuse."""

  CMD_LEN = {0x01: 8, 0x02: 1, 0x08: 1, 0x09: 1, 0x03: 5, 0x07: 5, 0x04: 3, 0x06: 5}

  def __init__(self, mod, clock, app: bytes | None = None, rpm: int | None = 0, speed: int = 0,
               ignition: bool | None = True, board_moving=False, output_on=False, dual_bank=True,
               cls=2, ack_cls=2, resident=False, lbt_s=0.1, app_init_s=0.3, ecc_boots=()):
    self.mod, self.clock = mod, clock
    self.ecc_boots = frozenset(ecc_boots)
    self.rpm, self.speed = rpm, speed
    if ignition is not None:
      self.ignition = lambda: ignition
    self.board_moving, self.output_on, self.dual_bank = board_moving, output_on, dual_bank
    self.cls, self.ack_cls = cls, ack_cls
    self.lbt_s, self.app_init_s = lbt_s, app_init_s
    self.slot = bytearray(b"\xff" * SLOT)
    if app:
      self.slot[:len(app)] = app
    self.stage = bytearray(b"\xff" * 128 * 1024)
    self.hdr: dict | None = None
    self.boots = self.installs = 0
    self.naks: list[tuple[int, int]] = []
    self.sent: list[tuple[float, int, bytes]] = []
    self.queue: list[tuple[float, int, bytes, str]] = []
    self.drop_rx = None
    self.drop_tx = None
    self.fail_copy = False
    self.seq = 0
    now = clock.now
    self.next = dict.fromkeys((0x158, 0x17C, 0x620, 0x627, 0x706, 0x70B), now)
    self.period = {0x158: 0.01, 0x17C: 0.01, 0x620: 0.1, 0x627: 1.0, 0x706: 1.0, 0x70B: 0.1}
    self.s: dict = {}
    if self.app_present() and not resident:
      self.mode, self.app_since = "app", now - 10.0
    else:
      self.reset(quiet=resident)   # resident: its HELLO went by long before anyone listened

  # -- the transport ----------------------------------------------------------
  def describe(self):
    return "blr-sim"

  def close(self):
    pass

  def health(self):
    return {0: {"total_error_cnt": 0}, 2: {"total_error_cnt": 0}}

  def send(self, can_id, data):
    m = self.mod
    data = bytes(data)
    self.sent.append((self.clock.now, can_id, data))
    wire = data.ljust(8, b"\x00")                     # the panda pads every frame to DLC 8
    self.queue.append((self.clock.now, can_id, wire, m.ECHO))
    if self.drop_rx is not None and self.drop_rx(can_id, wire):
      return
    self.on_frame(can_id, wire)

  def poll(self, timeout=0.0):
    self._tick(self.clock.now)
    self._car(self.clock.now)
    due = [q for q in self.queue if q[0] <= self.clock.now]
    if not due:
      nxt = min([q[0] for q in self.queue] + list(self.next.values()))
      self.clock.now = max(self.clock.now + 1e-4, min(self.clock.now + max(timeout, 1e-4), nxt))
      self._tick(self.clock.now)
      self._car(self.clock.now)
      due = [q for q in self.queue if q[0] <= self.clock.now]
    self.queue = [q for q in self.queue if q[0] > self.clock.now]
    return [(a, d, k) for _, a, d, k in sorted(due, key=lambda q: q[0])]

  # -- images (blr_boot_main.c img_*) -------------------------------------------
  @staticmethod
  def vectors_ok(p) -> bool:
    sp, pc = struct.unpack_from("<II", p, 0)
    return 0x20000000 <= sp <= 0x20024000 and bool(pc & 1) and APP_ORIGIN < pc < 0x08020000

  @staticmethod
  def marker_ok(m) -> bool:
    magic, origin, _git, flags = struct.unpack_from("<4I", m, 0)
    return magic == BLR1 and origin == APP_ORIGIN and not flags & 0x0E and 1 <= (flags >> 4) & 3 <= 3

  @staticmethod
  def pc_inside(p, n) -> bool:
    return (struct.unpack_from("<I", p, 4)[0] & ~1) - APP_ORIGIN < n

  def img_ok(self, p, n) -> bool:
    return 0x110 <= n <= SLOT and self.vectors_ok(p) and self.marker_ok(p[0x100:0x110]) and self.pc_inside(p, n)

  @staticmethod
  def trailer_ok(img) -> bool:
    """img_trailer_ok(): {BLRT, the length, CRC-32 of the rest} in the image's last 12 bytes."""
    n = len(img)
    if n < 0x110 + 12 or n > SLOT:
      return False
    magic, length, crc = struct.unpack_from("<III", img, n - 12)
    return magic == BLRT and length == n and zlib.crc32(img[:n - 12]) & 0xFFFFFFFF == crc

  def app_present(self) -> bool:
    return self.vectors_ok(self.slot) and self.marker_ok(self.slot[0x100:0x110])

  def marker(self):
    return struct.unpack_from("<4I", self.slot, 0x100)

  # -- the board -----------------------------------------------------------------
  def reset(self, quiet=False):
    now = self.clock.now
    self.boots += 1
    ecc = self.boots in self.ecc_boots
    if ecc:
      self.hdr = None                                  # ecc_recover(): the staging header page erased first
    h = self.hdr
    if h is not None and h["state"] == "pending" and not self.fail_copy:
      img = bytes(self.stage[:h["length"]])
      if zlib.crc32(img) == h["crc"] and self.img_ok(img, len(img)) and self.trailer_ok(img):
        self.slot[:] = b"\xff" * SLOT
        self.slot[:len(img)] = img
        h["state"] = "done"
        self.installs += 1
    self.mode = "boot"
    self.s = {"active": False, "begun": False, "chunk_open": False, "chunk": 0, "have": bytearray(), "length": 0,
              "last": now}
    if quiet:
      self.tx_ok_at = self.t_window = now
      return
    self.tx_ok_at = self.t_window = now + self.lbt_s
    magic, _origin, git, flags = self.marker()
    st = 0
    if self.app_present():
      st |= 0x01
    if magic == BLR1:
      st |= 0x02 | (0x04 if flags & 1 else 0) | (flags & 0x30)
    if self.hdr is not None and self.hdr["state"] == "pending":
      st |= 0x08
    if ecc:
      st |= 0x40                                       # BLR_BOOT_HELLO_ECC_FIXED
    g = git if st & 0x01 else 0
    self.queue.append((self.tx_ok_at, 0x629, bytes([0x83, 1, st]) + g.to_bytes(4, "little") + bytes([self.cls]),
                       self.mod.RX))

  def _tick(self, now):
    if self.mode != "boot":
      return
    if self.s["active"]:
      if now - self.s["last"] > 10.0:
        self.reset()                                   # BOOT_ACT_RESET
    elif now >= self.tx_ok_at and now - self.t_window > 0.8:
      if self.app_present():
        self.mode, self.app_since = "app", now         # BOOT_ACT_JUMP
      else:
        self.t_window = now                            # resident, PB10 low, updatable

  def _reply(self, data, dt=0.002):
    data = bytes(data).ljust(8, b"\x00")
    if self.drop_tx is not None and self.drop_tx(data):
      return
    self.queue.append((self.clock.now + dt, 0x629, data, self.mod.RX))

  def _ack(self, cmd, detail=0, b7=0, dt=0.002):
    self._reply([0x80, cmd, detail & 0xFF, detail >> 8, 0, 0, 0, b7], dt)

  def _nak(self, cmd, code):
    self.naks.append((cmd, code))
    self._reply([0x81, cmd, code])

  def _close(self, now):
    self.s.update(active=False, begun=False, chunk_open=False, have=bytearray())
    self.t_window = now - 0.801

  def on_frame(self, can_id, d):
    now = self.clock.now
    if self.mode == "app":
      if can_id == 0x628 and d == KNOCK:
        if now - self.app_since < self.app_init_s:
          return                                       # INIT: ignored
        if self.board_moving:
          return self._nak(0x01, 5)
        if self.output_on:
          return self._nak(0x01, 10)
        self.queue.append((now, 0x620, self._status(7), self.mod.RX))
        self.reset()
      return
    if now < self.tx_ok_at:
      return                                           # no voice yet: commands wait for one
    s = self.s
    if can_id == 0x62A:
      if s["active"] and s["chunk_open"]:
        if len(s["have"]) + len(d) <= 256:
          s["have"] += d
        s["last"] = now
      return
    if can_id != 0x628:
      return
    cmd = d[0]
    need = self.CMD_LEN.get(cmd, 0)
    if need == 0 or any(d[need:]):
      return                                           # not addressed to this board
    if cmd == 0x01:
      if d[1:8] != b"BLRBOOT":
        return
      if self.board_moving:
        return self._nak(cmd, 5)
      s.update(active=True, begun=False, chunk_open=False, have=bytearray(), last=now)
      return self._ack(cmd, 1, self.ack_cls)
    if cmd == 0x08:
      self._ack(cmd)
      return self.reset()
    if cmd == 0x09:
      self._close(now)
      return self._ack(cmd)
    if not s["active"]:
      return self._nak(cmd, 1)
    s["last"] = now
    if cmd == 0x02:
      c = zlib.crc32(bytes(self.slot)) if self.app_present() else 0
      return self._reply([0x82, 1, (SLOT // 1024) & 0xFF, (SLOT // 1024) >> 8] + list(c.to_bytes(4, "little")))
    if cmd == 0x03:
      n = struct.unpack_from("<I", d, 1)[0]
      s.update(begun=False, chunk_open=False, have=bytearray())
      if not self.dual_bank:
        return self._nak(cmd, 9)
      if n < 0x110:
        self._close(now)
        return self._nak(cmd, 8)
      if n > SLOT:
        return self._nak(cmd, 2)
      self.stage[:] = b"\xff" * len(self.stage)
      self.hdr = None
      s.update(begun=True, length=n)
      return self._ack(cmd, dt=1.5)                    # all of bank 2
    if cmd == 0x04:
      if not s["begun"]:
        return self._nak(cmd, 1)
      s.update(chunk=struct.unpack_from("<H", d, 1)[0], have=bytearray(), chunk_open=True)
      return None
    if cmd == 0x06:
      if not (s["begun"] and s["chunk_open"]):
        return self._nak(cmd, 1)
      idx, want = struct.unpack_from("<HH", d, 1)
      s["chunk_open"] = False
      if idx != s["chunk"]:
        return self._nak(cmd, 1)
      addr = idx * 256
      if addr >= s["length"]:
        return self._nak(cmd, 1)
      n = min(256, s["length"] - addr)
      if len(s["have"]) < n:
        return self._nak(cmd, 6)
      body = bytes(s["have"][:n])
      if crc16(body) != want:
        return self._nak(cmd, 3)
      if (idx == 0 and not (self.vectors_ok(body) and self.pc_inside(body, s["length"]))) or \
         (idx == 1 and not self.marker_ok(body[:16])):
        self._close(now)
        return self._nak(cmd, 8)
      here = bytes(self.stage[addr:addr + n])
      if here != body:
        if here != b"\xff" * n:
          return self._nak(cmd, 4)                     # programming flash that is not erased
        self.stage[addr:addr + n] = body
      return self._ack(cmd, idx)
    if cmd == 0x07:
      if not s["begun"]:
        return self._nak(cmd, 1)
      want = struct.unpack_from("<I", d, 1)[0]
      img = bytes(self.stage[:s["length"]])
      if zlib.crc32(img) != want:
        return self._nak(cmd, 3)
      if not self.img_ok(img, len(img)) or not self.trailer_ok(img):
        self._close(now)
        return self._nak(cmd, 8)
      self.hdr = {"length": s["length"], "crc": want, "state": "pending"}
      return self._ack(cmd, dt=0.05)
    return None

  def _status(self, state):
    self.seq += 1
    _m, _o, _git, flags = self.marker()
    return bytes([state | (0x08 if self.output_on else 0), 0, 0, 0, 0, 0, (flags >> 4) & 3, self.seq & 0xF])

  def _car(self, until):
    m = self.mod
    for addr, period in self.period.items():
      while self.next[addr] <= until:
        t = self.next[addr]
        self.next[addr] += period
        if addr == 0x158:
          data = bytes([self.speed >> 8, self.speed & 0xFF, 0, 0, 0, 0, 0, 0])
        elif addr == 0x17C:
          if self.rpm is None:
            continue
          data = bytes([0, 0, self.rpm >> 8, self.rpm & 0xFF, 0, 0, 0, 0])
        elif addr in (0x620, 0x627):
          if self.mode != "app":
            continue
          if addr == 0x620:
            data = self._status(0 if t - self.app_since < self.app_init_s else 1)
          else:
            _mg, _o, git, flags = self.marker()
            data = git.to_bytes(4, "little") + bytes([((flags >> 4) & 3) | (flags & 1) << 2 | 1 << 3, 1, 1, 2])
        elif addr == 0x706:
          data = bytes([12, 3, 9, 4, 0, 0x27, 0x07, 0])
        else:
          data = bytes([8, 16, 0, 0x40, 0, 0, 0, 0])
        self.queue.append((t, addr, data, m.RX))


def _setup(app=None, **kw):
  mod = _module()
  clock = FakeTime()
  mod.time = clock
  mod.Flasher.save_trace = lambda self, directory=None: None   # never /data from a test
  return mod, clock, BlrSim(mod, clock, app, **kw)


def _run(mod, sim, image, cancel_in=None, should_cancel=None, on_phase=None, target=None, **kw):
  """run_flash on `sim`: (ok, msg, phases, logs, traces)."""
  phases: list[dict] = []
  logs: list[str] = []
  traces: list[dict] = []

  def phase(d):
    phases.append(d)
    if on_phase is not None:
      on_phase(d, sim)

  if should_cancel is None:
    should_cancel = lambda: bool(cancel_in) and bool(phases) and phases[-1]["p"] == cancel_in   # noqa: E731
  ok, msg = mod.run_flash(sim, image, log=logs.append, phase=phase, trace_out=traces.append,
                          should_cancel=should_cancel, target=mod.BLR if target is None else target, **kw)
  return ok, msg, phases, logs, traces


def _host(sim, mod) -> list[bytes]:
  return [d for _, a, d in sim.sent if a == mod.BLR_ID_HOST]


def _data(sim, mod) -> list[float]:
  return [t for t, a, _ in sim.sent if a == mod.BLR_ID_DATA]


def _firmware_dir() -> Path | None:
  for p in FIRMWARE_BLR:
    if (p / "inc/blr_boot_proto.h").is_file():
      return p
  return None


def _c_defines(paths) -> dict[str, int]:
  """Every object-like #define whose value is an integer expression of literals and earlier names. Casts, strings and
  function-like macros are skipped rather than guessed at."""
  ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
         ast.Div: lambda a, b: a // b, ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
         ast.LShift: lambda a, b: a << b, ast.RShift: lambda a, b: a >> b, ast.BitOr: lambda a, b: a | b,
         ast.BitAnd: lambda a, b: a & b, ast.BitXor: lambda a, b: a ^ b}
  out: dict[str, int] = {}

  def ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, int):
      return n.value
    if isinstance(n, ast.Name):
      return out[n.id]
    if isinstance(n, ast.BinOp) and type(n.op) in ops:
      return ops[type(n.op)](ev(n.left), ev(n.right))
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Invert):
      return ~ev(n.operand)
    raise ValueError(ast.dump(n))

  for p in paths:
    text = re.sub(r"/\*.*?\*/", " ", Path(p).read_text(encoding="utf-8", errors="replace"), flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+([^\n]+?)[ \t]*$", text, re.M):
      expr = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\b", r"\1", m.group(2))
      try:
        out[m.group(1)] = ev(ast.parse(expr, mode="eval").body)
      except Exception:
        continue
  return out


# ---------------------------------------------------------------------------
class TestBlrTarget(unittest.TestCase):
  def test_blr_target_constants_are_the_design_literals(self):
    """DESIGN E.1 and blr_boot_proto.h, typed in again. These run everywhere; the header comparison below needs the
    firmware tree."""
    mod = _module()
    b = mod.BLR
    assert (b.name, b.id_host, b.id_board, b.id_data, b.magic) == ("blr", 0x628, 0x629, 0x62A, b"BLRBOOT")
    assert (b.marker_magic, b.marker_origin, b.slot_bytes) == (BLR1, APP_ORIGIN, SLOT)
    assert b.hello_class == 2 and b.allowed_variants == frozenset({2, 3})
    assert b.refuse_dirty and b.strict_image and b.data_fps_max == 400.0 and b.retry_naks == frozenset({1, 3, 6})
    assert b.census_own == frozenset(range(0x620, 0x630))
    assert b.trace_ids == (0x17C, 0x1A6, 0x620, 0x706, 0x70B) and b.trace_dir == "/data/blr-trace"
    assert Path(b.image_path) == BLR_IMAGE and b.image_path == mod.BLR_APPSLOT_BIN
    assert {k: b.errs[k] for k in (8, 9, 10)} == {8: "image is not for this board", 9: "board flash is not dual-bank",
                                                   10: "lamp output is on"}
    assert all(b.errs[k] == mod.ERRS[k] for k in range(8)), "codes 0-7 are the EPS v1 ones"
    assert mod.BLR_BOOT_ID_MAGIC == BLB1 and mod.BLR_APP_ID_RESERVED == 0x0E
    assert (mod.BLR_VARIANT_BENCH, mod.BLR_VARIANT_SHADOW, mod.BLR_VARIANT_LIVE) == (1, 2, 3)
    assert abs(1.0 / b.data_fps_max + mod.DATA_PACE_MARGIN_S - 0.00251) < 1e-12, "blr_can_update.py DATA_SPACING_S"
    # review round 1: the image trailer (BOOT-6) and HELLO's ECC_FIXED (BOOT-2)
    assert (mod.BLR_TRAILER_MAGIC, mod.BLR_TRAILER_BYTES, mod.BLR_HELLO_ECC_FIXED) == (BLRT, 12, 0x40)
    assert struct.pack("<I", mod.BLR_TRAILER_MAGIC) == b"BLRT"
    assert mod.BLR_HELLO_ECC_FIXED & (mod.BLR_VARIANT_MASK | 0x0F) == 0, "a bit of its own in HELLO's status"
    # nothing of the BLR's identity is the EPS board's
    e = mod.EPS
    assert not {b.id_host, b.id_board, b.id_data} & {e.id_host, e.id_board, e.id_data}
    assert b.magic != e.magic and b.marker_magic != e.marker_magic
    assert not b.census_own & e.census_own
    assert mod.TARGETS == {"eps": mod.EPS, "blr": mod.BLR}

  def test_the_eps_target_is_todays_constants(self):
    """EPS = the module constants board.py and the tests import, exactly, and every check, pace and retry the BLR adds
    is off for it - which is what keeps its wire transcript frozen."""
    mod = _module()
    e = mod.EPS
    assert (e.name, e.id_host, e.id_board, e.id_data, e.magic) == ("eps", mod.ID_HOST, mod.ID_BOARD, mod.ID_DATA,
                                                                   mod.MAGIC)
    assert (e.marker_magic, e.marker_origin, e.slot_bytes) == (mod.APP_ID_MAGIC, mod.APP_ORIGIN, mod.APP_SLOT_BYTES)
    assert e.census_own is mod.CENSUS_OWN and e.trace_ids is mod.TRACE_IDS and e.trace_dir == mod.TRACE_DIR
    assert e.image_path == mod.EPS_LKAS_APPSLOT_BIN and dict(e.errs) == mod.ERRS
    assert (e.hello_class, e.allowed_variants, e.data_fps_max, e.retry_naks) == (None, None, None, None)
    assert not e.refuse_dirty and not e.strict_image
    # and it is the default everywhere a target is taken
    import inspect
    for fn in (mod.run_flash, mod.check_app_slot_image, mod.image_identity, mod.load_bundled_image,
               mod.describe_hello, mod.Flasher.__init__):
      assert inspect.signature(fn).parameters["target"].default is mod.EPS, fn.__name__
    f = mod.Flasher(object(), log=lambda s: None)
    assert f.target is mod.EPS and f._data_gap == 0.0
    # frozen: a target cannot be edited in place by anything that holds it
    with self.assertRaises(dataclasses.FrozenInstanceError):
      e.id_host = 0x628
    with self.assertRaises(TypeError):
      e.errs[8] = "x"

  def test_blr_target_constants_match_the_firmware(self):
    """Against blr_boot_proto.h, blr_proto.h, blr_app_id.h and blr_logic.h themselves, and the host tool's pacing -
    when the firmware tree is reachable (the owner's machine). Elsewhere the literals above stand alone."""
    fw = _firmware_dir()
    if fw is None:
      self.skipTest("the EPS-LKAS blr/ tree is not reachable (set BLR_FIRMWARE_DIR); the design literals still ran")
    inc = fw / "inc"
    d = _c_defines([inc / "blr_proto.h", inc / "blr_boot_proto.h", inc / "blr_app_id.h", inc / "blr_logic.h"])
    mod = _module()
    b = mod.BLR
    assert (d["BLR_BOOT_ID_HOST_TO_BOARD"], d["BLR_BOOT_ID_BOARD_TO_HOST"], d["BLR_BOOT_ID_DATA"]) == \
           (b.id_host, b.id_board, b.id_data)
    assert (d["BLR_ID_BOOT_HOST"], d["BLR_ID_BOOT_BOARD"], d["BLR_ID_BOOT_DATA"]) == (b.id_host, b.id_board, b.id_data)
    assert bytes(d[f"BLR_BOOT_MAGIC_{i}"] for i in range(7)) == b.magic
    assert d["BLR_BOOT_CLASS_BYTE"] == d["BLR_BOARD_CLASS"] == b.hello_class
    assert d["BLR_BOOT_PROTO_VERSION"] == mod.PROTO_VERSION
    for name in ("ENTER", "INFO", "BEGIN", "CHUNK", "DATA", "CEND", "FINISH", "REBOOT", "ABORT"):
      assert d[f"BLR_BOOT_CMD_{name}"] == getattr(mod, f"CMD_{name}"), name
    for name in ("ACK", "NAK", "INFO", "HELLO"):
      assert d[f"BLR_BOOT_RSP_{name}"] == getattr(mod, f"RSP_{name}"), name
    for name in ("APP_OK", "APP_ID", "APP_DIRTY", "STAGED"):
      assert d[f"BLR_BOOT_HELLO_{name}"] == getattr(mod, f"HELLO_{name}"), name
    assert (d["BLR_BOOT_HELLO_VARIANT_SHIFT"], d["BLR_BOOT_HELLO_VARIANT_MASK"]) == \
           (mod.BLR_VARIANT_SHIFT, mod.BLR_VARIANT_MASK)
    assert d["BLR_BOOT_HELLO_ECC_FIXED"] == mod.BLR_HELLO_ECC_FIXED
    assert (d["BLR_TRAILER_MAGIC"], d["BLR_TRAILER_BYTES"]) == (mod.BLR_TRAILER_MAGIC, mod.BLR_TRAILER_BYTES)
    assert (d["BLR_BOOT_ERR_WRONG_IMAGE"], d["BLR_BOOT_ERR_NOT_DUAL_BANK"], d["BLR_BOOT_ERR_OUTPUT_ON"]) == \
           (mod.BLR_ERR_WRONG_IMAGE, mod.BLR_ERR_NOT_DUAL_BANK, mod.BLR_ERR_OUTPUT_ON)
    assert {d[f"BLR_BOOT_ERR_{n}"] for n in ("SEQUENCE", "CRC", "SHORT")} == b.retry_naks
    errs = {n: d[f"BLR_BOOT_ERR_{n}"] for n in ("NONE", "SEQUENCE", "TOO_BIG", "CRC", "FLASH", "MOVING", "SHORT",
                                               "VERSION", "WRONG_IMAGE", "NOT_DUAL_BANK", "OUTPUT_ON")}
    assert sorted(errs.values()) == sorted(b.errs), "every NAK code has a text, and only those"
    assert (d["BLR_BOOT_CHUNK_BYTES"], d["BLR_BOOT_DATA_PER_FRAME"]) == (mod.CHUNK_BYTES, mod.DATA_PER_FRAME)
    assert d["BLR_BOOT_IMG_MIN_LEN"] == mod.APP_ID_OFFSET + 16
    assert (d["BLR_BOOT_APP_ORIGIN"], d["BLR_BOOT_APP_SLOT_BYTES"]) == (b.marker_origin, b.slot_bytes)
    assert d["BLR_BOOT_PC_END"] == b.marker_origin + b.slot_bytes
    assert (d["BLR_BOOT_SP_MIN"], d["BLR_BOOT_SP_MAX"]) == (0x20000000, 0x20024000)
    sp_max = d["BLR_BOOT_SP_MAX"]
    assert mod.check_app_slot_image(make_image(sp=sp_max), b) is None
    assert "stack pointer" in mod.check_app_slot_image(make_image(sp=sp_max + 4), b)
    assert "stack pointer" in mod.check_app_slot_image(make_image(sp=d["BLR_BOOT_SP_MIN"] - 4), b)
    assert d["BLR_APP_ID_MAGIC"] == d["BLR_MAGIC_APP_BLR1"] == b.marker_magic
    assert d["BLR_BOOT_ID_MAGIC"] == d["BLR_MAGIC_BOOT_BLB1"] == mod.BLR_BOOT_ID_MAGIC
    assert d["BLR_MAGIC_EPS_APL1"] == mod.APP_ID_MAGIC == mod.EPS.marker_magic
    assert d["BLR_ID_RECORD_OFFSET"] == mod.APP_ID_OFFSET
    assert d["BLR_APP_ID_DIRTY"] == mod.APP_ID_DIRTY and d["BLR_APP_ID_RESERVED_MASK"] == mod.BLR_APP_ID_RESERVED
    assert (d["BLR_APP_ID_VARIANT_SHIFT"], d["BLR_APP_ID_VARIANT_MASK"]) == (mod.BLR_VARIANT_SHIFT, mod.BLR_VARIANT_MASK)
    assert (d["BLR_VARIANT_CODE_BENCH"], d["BLR_VARIANT_CODE_SHADOW"], d["BLR_VARIANT_CODE_LIVE"]) == \
           (mod.BLR_VARIANT_BENCH, mod.BLR_VARIANT_SHADOW, mod.BLR_VARIANT_LIVE)
    assert b.census_own == frozenset(range(d["BLR_ID_BLOCK_LO"], d["BLR_ID_BLOCK_HI"] + 1))
    assert d["BLR_ID_STATUS"] == mod.BLR_STATUS_ID
    assert (d["BLR_ID_ENGINE_DATA"], d["BLR_ID_POWERTRAIN"]) == (mod.ENGINE_DATA_ID, mod.ENGINE_RPM_ID)
    assert d["BLR_STATIONARY_CPH"] == mod.STATIONARY_CPH
    # the comma's countdown keeps the session alive inside the bootloader's timeout, and ENTER bursts for longer than
    # its window
    assert 2 * mod.HOLD_PING_S < d["BLR_BOOT_SESSION_TIMEOUT_MS"] / 1000.0
    assert d["BLR_BOOT_WINDOW_MS"] == 800
    tool = fw / "tools/blr_can_update.py"
    if tool.is_file():
      spacing = float(re.search(r"^DATA_SPACING_S = ([0-9.]+)", tool.read_text(), re.M).group(1))
      assert abs(spacing - (1.0 / b.data_fps_max + mod.DATA_PACE_MARGIN_S)) < 1e-12

  def test_the_trailer_is_the_firmware_post_builds(self):
    """The trailer as the firmware's own host library seals and checks it (blr_hostlib.add_trailer / trailer_problem,
    which the BLR post-build, check_blr_image.py and blr_can_update.py use) - when the firmware tree is reachable.
    Same bytes from the same body, and the same verdict on every image here."""
    fw = _firmware_dir()
    if fw is None or not (fw / "tools/blr_hostlib.py").is_file():
      self.skipTest("the EPS-LKAS blr/ tree is not reachable (set BLR_FIRMWARE_DIR)")
    import importlib.util
    spec = importlib.util.spec_from_file_location("blr_hostlib_for_test", fw / "tools/blr_hostlib.py")
    assert spec is not None and spec.loader is not None
    hl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hl)
    assert hasattr(hl, "add_trailer") and hasattr(hl, "trailer_problem"), "the firmware tree has no image trailer"
    mod = _module()
    for size in (0x110, 0x400, 4 * 1024 + 4, 6 * 1024 + 1, 20 * 1024):
      body = make_image(size=size, pc=APP_ORIGIN + 0x101, seed=size, trailer=False)
      assert hl.add_trailer(body) == sealed(body), size
    good = make_image(size=6 * 1024 + 4, seed=12)
    for img in (good, good[:-12], good[:-40], good + b"\xff" * 8, good + good[-12:], good[:0x300] + good[0x301:],
                make_image(size=0x118, pc=APP_ORIGIN + 0x101, trailer=False), sealed(good)):
      assert (hl.trailer_problem(img) == "") == (mod.blr_trailer_problem(img) == ""), len(img)

  def test_the_blr_marker_constants_are_exported_for_the_ui(self):
    """The screen reads the bundled image's marker at the flasher's offset with the flasher's magic, never its own
    copy (the EPS twin is test_the_ui_shares_the_marker_constants_with_the_flasher, on board.py)."""
    mod = _module()
    assert mod.APP_ID_OFFSET == 0x100 and mod.BLR_APP_ID_MAGIC == BLR1
    assert os.path.dirname(mod.BLR_APPSLOT_BIN) == os.path.dirname(mod.EPS_LKAS_APPSLOT_BIN)
    assert os.path.basename(mod.BLR_APPSLOT_BIN) == "blr_appslot.bin"
    # image_identity needs only the first 0x110 bytes, which is all the screen reads
    head = make_image(variant=3, git=0xC0FFEE11)[:0x110]
    assert mod.image_identity(head, mod.BLR) == {"git": "c0ffee11", "dirty": False, "readOnly": False, "test": False,
                                                  "variant": "live"}
    assert mod.marker_variant(0x20) == 2 and mod.BLR_VARIANT_NAMES[2] == "shadow"

  def test_the_words_the_screen_and_log_use(self):
    mod = _module()
    for t in (mod.EPS, mod.BLR):
      assert set(t.words) == {"board", "image", "no_marker", "dirty", "no_hello", "trace", "trace_header", "title",
                              "click", "click_line", "stock", "done_line"}, t.name
      assert len(t.words["title"]) <= 10, "the button's sub-label needs the room"
      assert all(ord(c) < 128 for w in t.words.values() for c in w), "the baked font"
    assert mod.EPS.words["title"] == "update" and mod.BLR.words["title"] == "lamp board"
    assert mod.BLR.words["click_line"] == "brake lamps are pedal-only" and mod.BLR.words["done_line"] == ""
    assert mod.EPS.words["done_line"] == "turn the car off, then on, before driving"


class TestBlrImages(unittest.TestCase):
  def test_apl1_and_blr1_are_refused_on_each_others_board(self):
    """The first of the four layers (DESIGN 7.2): the wrong file is refused by name before a single frame. An EPS image
    on a BLR would drive PB10 high - its UART idle level, the brake lamps on there."""
    mod = _module()
    eps, blr = eps_image(), make_image()
    assert mod.check_app_slot_image(eps, mod.EPS) is None
    assert mod.check_app_slot_image(blr, mod.BLR) is None
    why = mod.check_app_slot_image(eps, mod.BLR)
    assert why and "this is an EPS-LKAS image" in why, why
    why = mod.check_app_slot_image(blr, mod.EPS)
    assert why and "this is a BLR-v1" in why and "STANDALONE" not in why, why
    assert mod.check_app_slot_image(blr) == mod.check_app_slot_image(blr, mod.EPS), "EPS is the default"
    why = mod.check_app_slot_image(make_image(magic=BLB1), mod.BLR)
    assert why and "BOOTLOADER" in why, why
    assert "STANDALONE" not in (mod.check_app_slot_image(make_image(magic=0xD30D428B), mod.BLR) or "")
    assert "not a BLR-v1 application image" in (mod.check_app_slot_image(make_image(magic=0xD30D428B), mod.BLR) or "")
    # the real bundled EPS image too
    real = EPS_IMAGE.read_bytes()
    assert "this is an EPS-LKAS image" in (mod.check_app_slot_image(real, mod.BLR) or "")

  def test_bench_dirty_and_every_other_refusal(self):
    """check_blr_image.py --for-car, on the comma: bench never, dirty never, and the bootloader's own image rules."""
    mod = _module()
    b = mod.BLR
    assert mod.check_app_slot_image(make_image(variant=2), b) is None
    assert mod.check_app_slot_image(make_image(variant=3), b) is None
    assert mod.check_app_slot_image(make_image(variant=1), b) == "bench image - never installed from the comma"
    assert "built from a dirty tree" in mod.check_app_slot_image(make_image(variant=2, dirty=1), b)
    assert "VARIANT is 0" in mod.check_app_slot_image(make_image(variant=0), b)
    assert "bits 1-3" in mod.check_app_slot_image(make_image(flags=0x20 | 0x02), b)
    assert "bits 1-3" in mod.check_app_slot_image(make_image(flags=0x20 | 0x08), b)
    assert "Thumb" in mod.check_app_slot_image(make_image(pc=APP_ORIGIN + 0x200), b)
    assert "past the end" in mod.check_app_slot_image(make_image(pc=APP_ORIGIN + 0x2001, size=0x1000), b)
    assert "outside the application slot" in mod.check_app_slot_image(make_image(pc=0x08000201), b)
    assert "linked for" in mod.check_app_slot_image(make_image(origin=0x08000000), b)
    assert "not a firmware image" in mod.check_app_slot_image(b"\x00" * 0x10F, b)
    assert "will not fit" in mod.check_app_slot_image(make_image(size=SLOT + 8), b)
    # the EPS check is what it was: a dirty or read-only EPS image is still installable (it warns), and has no VARIANT
    assert mod.check_app_slot_image(eps_image(flags=0x01), mod.EPS) is None
    assert mod.check_app_slot_image(eps_image(flags=0x03), mod.EPS) is None
    assert set(mod.image_identity(eps_image(flags=0x01))) == {"git", "dirty", "readOnly", "test"}

  def test_the_image_trailer(self):
    """Review round 1 (BOOT-6): nothing in the vectors or the marker says how long an image is, so a truncated file
    that still holds its reset handler passed every other check - and the host's CRC over what it sent agreed with
    it. The BLR bootloader refuses at FINISH an image that does not end with its own {BLRT, length, CRC-32}; the
    comma refuses it before a frame is sent, in the bootloader's order. The EPS has no trailer and is not checked."""
    mod = _module()
    b = mod.BLR
    good = make_image(size=6 * 1024 + 4, seed=11)
    assert mod.check_app_slot_image(good, b) is None and mod.blr_trailer_problem(good) == ""
    assert good[-12:-8] == b"BLRT" and len(good) % 8 == 0, "sealed to a double word, the trailer last"
    assert "no image trailer (BLRT)" in mod.check_app_slot_image(make_image(size=6 * 1024 + 4, seed=11, trailer=False), b)
    for cut in (12, 40, 2048, len(good) - 0x300):     # truncated copies: vectors, marker and reset handler all kept
      why = mod.check_app_slot_image(good[:-cut], b)
      assert why and "trailer" in why, (cut, why)
    assert "no image trailer" in mod.check_app_slot_image(good + b"\xff" * 8, b), "padded after it: not last"
    assert "trailer says the image is" in mod.check_app_slot_image(good[:0x300] + good[0x301:], b), "a byte lost"
    assert "trailer says the image is" in mod.check_app_slot_image(good + good[-12:], b), "concatenated"
    changed = bytearray(good)
    changed[0x300] ^= 0x01
    assert "trailer's CRC-32" in mod.check_app_slot_image(bytes(changed), b)
    assert "cannot hold the marker" in mod.check_app_slot_image(make_image(size=0x118, pc=APP_ORIGIN + 0x101,
                                                                           trailer=False), b)
    # the specific refusals still come first: an EPS image, a bench image, a dirty tree say what they are
    assert "this is an EPS-LKAS image" in mod.check_app_slot_image(eps_image(), b)
    assert mod.check_app_slot_image(make_image(variant=1, trailer=False), b) == \
      "bench image - never installed from the comma"
    assert "dirty tree" in mod.check_app_slot_image(make_image(dirty=1, trailer=False), b)
    # the EPS's check is what it was: no trailer asked for, and one (were it ever there) ignored
    assert mod.check_app_slot_image(eps_image(), mod.EPS) is None
    assert mod.check_app_slot_image(sealed(eps_image()), mod.EPS) is None
    for blob in (b"", b"\x00" * 0x11B, good[:0x11C]):
      assert isinstance(mod.blr_trailer_problem(blob), str)

  def test_the_check_never_raises(self):
    mod = _module()
    rnd = random.Random(5)
    for _ in range(300):
      n = rnd.choice((0, 7, 0x10F, 0x110, 0x111, 600, 5000))
      blob = bytes(rnd.randrange(256) for _ in range(n))
      for t in (mod.EPS, mod.BLR):
        r = mod.check_app_slot_image(blob, t)
        assert r is None or isinstance(r, str)

  def test_no_bundled_image_is_nothing_to_offer(self):
    """WP-H5 bundles the BLR image only after the bench tests: until then there is no blr_appslot.bin, and that must
    read as "nothing to offer" everywhere, never an exception."""
    mod = _module()
    with tempfile.TemporaryDirectory() as tmp:
      missing = dataclasses.replace(mod.BLR, image_path=os.path.join(tmp, "blr_appslot.bin"))
      image, err = mod.load_bundled_image(missing)
      assert image is None and err.startswith("no bundled image at ") and "blr_appslot.bin" in err
      Path(missing.image_path).write_bytes(b"abc")
      assert mod.load_bundled_image(missing) == (b"abc", "")
    if not BLR_IMAGE.exists():
      assert mod.load_bundled_image(mod.BLR)[0] is None
    # the CLI says so and never opens a transport
    def boom(*a, **k):
      raise AssertionError("a transport was opened")
    mod.PandaTransport = boom
    mod.BenchTransport = boom
    mod.BLR = missing
    mod.TARGETS = {"eps": mod.EPS, "blr": missing}
    with redirect_stdout(io.StringIO()) as out:
      assert mod.main(["--target", "blr"]) == 2
      assert mod.main(["--target", "blr", "--bus", "2"]) == 2
      assert mod.main(["--target", "blr", "--bus", "1"]) == 2
    assert "no bundled image" in out.getvalue() and "bus 0 only" in out.getvalue()


class TestBundledBlrImage(unittest.TestCase):
  """Twins of the EPS bundled-image tests. They skip while no blr_appslot.bin is bundled (DESIGN E.5: only after the
  bench tests), and bind the moment one is."""

  def setUp(self):
    if not BLR_IMAGE.exists():
      self.skipTest("no blr_appslot.bin bundled yet (WP-H5)")

  def test_bundled_blr_image_is_an_app_slot_image(self):
    d = BLR_IMAGE.read_bytes()
    assert len(d) < 120 * 1024, "check_added_large_files --maxkb=120"
    magic, origin, _git, flags = struct.unpack("<4I", d[0x100:0x110])
    assert magic == BLR1, f"marker is {magic:#010x}, not BLR1"
    assert origin == APP_ORIGIN, f"linked for {origin:#010x}"
    sp, pc = struct.unpack("<II", d[0:8])
    assert 0x20000000 <= sp <= 0x20024000
    assert APP_ORIGIN < pc < APP_ORIGIN + SLOT and pc & 1
    mod = _module()
    assert mod.check_app_slot_image(d, mod.BLR) is None, mod.check_app_slot_image(d, mod.BLR)
    ident = mod.image_identity(d, mod.BLR)
    assert re.fullmatch(r"[0-9a-f]{8}", ident["git"]), ident["git"]
    assert not ident["dirty"], "built from an edited tree; 0x627 would report a commit it is not"
    assert ident["variant"] in ("shadow", "live"), ident["variant"]
    assert "this is a BLR-v1" in (mod.check_app_slot_image(d, mod.EPS) or ""), "never offered to the EPS board"

  def test_bundled_blr_image_check_rejects_a_standalone_image(self):
    mod = _module()
    good = BLR_IMAGE.read_bytes()
    bad = bytearray(good)
    bad[0x100:0x104] = struct.pack("<I", 0xD30D428B)
    assert "not a BLR-v1 application image" in (mod.check_app_slot_image(bytes(bad), mod.BLR) or "")
    wrong = bytearray(good)
    wrong[0x104:0x108] = struct.pack("<I", 0x08000000)
    assert "linked for" in (mod.check_app_slot_image(bytes(wrong), mod.BLR) or "")

  def test_bundled_blr_hash_read_agrees_with_the_full_parse(self):
    """The screen reads only the first 0x110 bytes."""
    mod = _module()
    d = BLR_IMAGE.read_bytes()
    assert mod.image_identity(d[:mod.APP_ID_OFFSET + 16], mod.BLR) == mod.image_identity(d, mod.BLR)


# ---------------------------------------------------------------------------
class TestBlrUpdate(unittest.TestCase):
  def test_a_full_update(self):
    """The whole sequence, the EPS's phases and gate, on the BLR's channel only: knock, HELLO class 2, ENTER, INFO,
    the countdown, BEGIN, the chunks, FINISH, REBOOT, then the new image's HELLO with its commit AND its VARIANT."""
    old = make_image(variant=2, git=0x11111111, seed=2)
    new = make_image(variant=3, git=0x22222222, size=6 * 1024 + 4, seed=3)
    mod, clock, sim = _setup(old)
    ok, msg, phases, logs, traces = _run(mod, sim, new)
    assert ok and msg == "22222222", msg
    assert bytes(sim.slot[:len(new)]) == new and sim.installs == 1 and sim.mode == "app"
    names = [p["p"] for i, p in enumerate(phases) if i == 0 or phases[i - 1]["p"] != p["p"]]
    assert names == ["check", "baseline", "click", "stock", "erase", "write", "install", "restart"], names
    lefts = [p["left"] for p in phases if p["p"] == "stock"]
    assert [x for i, x in enumerate(lefts) if i == 0 or lefts[i - 1] != x] == [5, 4, 3, 2, 1]
    # every frame on the BLR's own channel, none on the EPS board's
    assert {a for _, a, _ in sim.sent} == {0x628, 0x62A}
    host = _host(sim, mod)
    assert host[0] == KNOCK, "the knock"
    assert {h[0] for h in host} <= {1, 2, 3, 4, 6, 7, 8, 9}
    assert sim.naks == []
    assert any("(live)" in x for x in logs) and any("shadow" in x for x in logs if "HELLO" in x), logs

  def test_pacing_is_at_most_400_data_frames_a_second(self):
    """DESIGN B.1/R5: a BLR update shares bus 0 with the EPS board, which forwards it. 2.51 ms apart at least, and
    never more than 400 in any second - here over 20 KB and its trailer, 2562 DATA frames."""
    mod, clock, sim = _setup(make_image(git=0x1))
    ok, msg, *_ = _run(mod, sim, make_image(variant=2, git=0x2, size=20 * 1024, seed=9))
    assert ok, msg
    t = _data(sim, mod)
    assert len(t) == -(-(20 * 1024 + 4 + 12) // 8), "20 KB, 0xFF to a double word and the trailer: 2562 frames"
    assert min(b - a for a, b in zip(t, t[1:], strict=False)) >= 1.0 / 400
    j = worst = 0
    for i in range(len(t)):                         # frames in any (t - 1 s, t]
      while t[j] <= t[i] - 1.0:
        j += 1
      worst = max(worst, i - j + 1)
    assert worst <= 400, worst
    assert worst >= 380, f"paced, not throttled to a crawl: {worst}/s"

  def test_a_hello_from_another_board_class_is_abort_before_enter(self):
    """Layer 3 (DESIGN 7.2): positive identification. Whatever answers 0x629 must say class 2."""
    mod, clock, sim = _setup(make_image(git=0x1), cls=0)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and msg.startswith("wrong board answered on 0x629"), msg
    host = _host(sim, mod)
    assert host[0] == KNOCK and [h[0] for h in host[1:]] == [mod.CMD_ABORT], host
    assert not _data(sim, mod) and sim.installs == 0

  def test_an_enter_ack_from_another_board_class_is_abort_before_a_byte(self):
    mod, clock, sim = _setup(make_image(git=0x1), ack_cls=0)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and "wrong board answered on 0x629: ENTER ACK byte 7 is 0" in msg, msg
    cmds = [h[0] for h in _host(sim, mod)]
    assert mod.CMD_ABORT in cmds and not {mod.CMD_INFO, mod.CMD_BEGIN, mod.CMD_FINISH} & set(cmds), cmds
    assert not _data(sim, mod) and sim.mode == "app"

  def test_every_cancel_point(self):
    old = make_image(git=0x1)
    for where in ("check", "baseline"):
      mod, clock, sim = _setup(old)
      ok, msg, phases, *_ = _run(mod, sim, make_image(git=0x2), cancel_in=where)
      assert not ok and msg == mod.CANCELLED and sim.sent == [], where
    # in the countdown: ABORT, never BEGIN; the board goes back to the app it has
    mod, clock, sim = _setup(old)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2), cancel_in="stock")
    cmds = [h[0] for h in _host(sim, mod)]
    assert not ok and msg == mod.CANCELLED and mod.CMD_ABORT in cmds and mod.CMD_BEGIN not in cmds
    assert sim.mode == "app" and bytes(sim.slot[:len(old)]) == old and sim.hdr is None
    # its last instant, after the button has gone: read once more before BEGIN
    for after_s in (4.90, 4.99):
      mod, clock, sim = _setup(old)
      hold: dict = {}

      def started(d, s, hold=hold, clock=clock):
        if d["p"] == "stock":
          hold.setdefault("t", clock.now)

      ok, msg, *_ = _run(mod, sim, make_image(git=0x2), on_phase=started,
                         should_cancel=lambda hold=hold, clock=clock, after_s=after_s: "t" in hold and
                         clock.now >= hold["t"] + after_s)
      cmds = [h[0] for h in _host(sim, mod)]
      assert not ok and msg == mod.CANCELLED and mod.CMD_BEGIN not in cmds, after_s
    # from the erase on there is no cancel
    mod, clock, sim = _setup(old)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2), cancel_in="write")
    assert ok, msg

  def test_the_engine_gate_is_the_eps_one(self):
    mod, clock, sim = _setup(make_image(git=0x1), rpm=695)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and msg == mod.ENGINE_RUNNING and sim.sent == []
    mod, clock, sim = _setup(make_image(git=0x1))
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2),
                       on_phase=lambda d, s: setattr(s, "rpm", 700) if d["p"] == "stock" and d.get("left") == 3 else None)
    cmds = [h[0] for h in _host(sim, mod)]
    assert not ok and msg == mod.ENGINE_RUNNING and mod.CMD_ABORT in cmds and mod.CMD_BEGIN not in cmds
    mod, clock, sim = _setup(make_image(git=0x1), rpm=None)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and msg == mod.NO_ENGINE_DATA and sim.sent == []
    mod, clock, sim = _setup(make_image(git=0x1), ignition=False)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and msg == mod.IGNITION_OFF and sim.sent == []
    mod, clock, sim = _setup(make_image(git=0x1), speed=500)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and "moving" in msg and sim.sent == []

  def test_the_app_refuses_the_knock_moving_or_with_the_lamps_on(self):
    """BLR-CAN.md section 4: NAK 5 not stationary, NAK 10 with the output on or an ON edge pending - and nothing
    else happens: no reset, no session, the lamps keep what they had."""
    for kw, why in (({"board_moving": True}, "refused: the car is not stationary"),
                    ({"output_on": True}, "refused: lamp output is on")):
      mod, clock, sim = _setup(make_image(git=0x1), **kw)
      ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
      assert not ok and msg == why, (kw, msg)
      assert _host(sim, mod) == [KNOCK] and sim.boots == 0 and sim.mode == "app"

  def test_the_bootloader_refusing_an_eps_image_at_chunk_1(self):
    """Layer 4: were the comma's own check ever bypassed, the BLR bootloader refuses an APL1 image at CEND 1 with
    NAK 8 and closes the session. The flasher stops THERE, with the board's reason: no retry, no FINISH, no REBOOT,
    exactly two chunks of DATA, and nothing staged or installed."""
    old = make_image(git=0x33333333, seed=6)
    mod, clock, sim = _setup(old)
    mod.check_app_slot_image = lambda image, target=None: None
    ok, msg, *_ = _run(mod, sim, eps_image(size=12 * 1024))
    assert not ok and msg == "chunk 1: board refused CEND: image is not for this board", msg
    assert sim.naks == [(mod.CMD_CEND, 8)]
    assert len(_data(sim, mod)) == 64
    cmds = [h[0] for h in _host(sim, mod)]
    assert mod.CMD_FINISH not in cmds and mod.CMD_REBOOT not in cmds
    assert bytes(sim.slot[:len(old)]) == old and sim.hdr is None and sim.installs == 0

  def test_the_bootloader_refusing_a_truncated_image_at_finish(self):
    """Were the comma's own check ever bypassed: a truncated image keeps its vectors and marker, so every chunk is
    ACKed and the CRC-32 the host sends is over exactly what it sent - and FINISH reads it back out of bank 2 with no
    trailer at its end and refuses it, NAK 8, the session closed. No header is written: nothing staged, nothing to
    install on the next boot, the old app kept."""
    old = make_image(git=0x44444444, seed=8)
    mod, clock, sim = _setup(old)
    mod.check_app_slot_image = lambda image, target=None: None
    cut = make_image(git=0x55555555, size=6 * 1024, seed=9)[:-40]
    ok, msg, *_ = _run(mod, sim, cut)
    assert not ok and msg == "board refused FINISH: image is not for this board", msg
    assert sim.naks == [(mod.CMD_FINISH, 8)]
    cmds = [h[0] for h in _host(sim, mod)]
    assert mod.CMD_REBOOT not in cmds and sim.hdr is None and sim.installs == 0
    assert bytes(sim.slot[:len(old)]) == old

  def test_a_boot_that_repaired_an_ecc_page(self):
    """Review round 1 (BOOT-2): a page a power cut left half-written reads back with an ECC error; the bootloader
    erases it on the next boot - the staging header first, which cancels a staged image - and says so in HELLO (bit
    6). The flasher logs it, and when it is why the new image is not there after the REBOOT, it says so."""
    mod, clock, sim = _setup(make_image(git=0x1), ecc_boots={1})
    ok, msg, phases, logs, traces = _run(mod, sim, make_image(git=0x2))
    assert ok and msg == "00000002", msg
    assert any("HELLO" in x and "ECC page repaired this boot" in x for x in logs), logs
    mod, clock, sim = _setup(make_image(git=0x1), ecc_boots={2})
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and msg == ("board reports 00000001 after installing 00000002 - its bootloader repaired a flash " +
                              "page this boot, which cancels a staged image"), msg
    assert sim.installs == 0

  def test_not_dual_bank(self):
    mod, clock, sim = _setup(make_image(git=0x1), dual_bank=False)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2))
    assert not ok and msg == "board refused BEGIN: board flash is not dual-bank", msg
    assert not _data(sim, mod)

  def test_a_lost_data_frame_and_a_lost_ack_are_recovered(self):
    """NAK 6 (a DATA frame lost) is a chunk sent again. A lost CEND ACK is asked about once (the BLR answers that
    with NAK 1, its chunk closed), then the chunk is sent again and ACKed as already in flash."""
    mod, clock, sim = _setup(make_image(git=0x1))
    n = {"data": 0, "ack": 0}

    def lose_data(can_id, d):
      if can_id == 0x62A:
        n["data"] += 1
        return n["data"] == 40
      return False

    def lose_ack(d):
      if d[0] == 0x80 and d[1] == 0x06 and d[2] == 3 and n["ack"] == 0:
        n["ack"] += 1
        return True
      return False

    sim.drop_rx, sim.drop_tx = lose_data, lose_ack
    img = make_image(git=0x2, size=8 * 1024, seed=4)
    ok, msg, *_ = _run(mod, sim, img)
    assert ok, msg
    assert (mod.CMD_CEND, 6) in sim.naks and (mod.CMD_CEND, 1) in sim.naks and n["ack"] == 1
    assert bytes(sim.slot[:len(img)]) == img

  def test_a_resident_bootloader_is_recovered_from_the_comma(self):
    """A board whose slot holds nothing valid sits in its bootloader, PB10 low - stock lamps - and has said its HELLO
    long ago. The knock opens a session there; it times out after 10 s, the board resets and says HELLO again, inside
    the flasher's 25 s wait, and the update goes on."""
    mod, clock, sim = _setup(None, resident=True)
    img = make_image(git=0x5, seed=5)
    t0 = clock.now
    ok, msg, *_ = _run(mod, sim, img)
    assert ok and msg == "00000005", msg
    assert bytes(sim.slot[:len(img)]) == img and sim.boots == 3   # resident, the session timeout, the REBOOT
    assert clock.now - t0 < 60

  def test_a_dry_run(self):
    old = make_image(git=0x1)
    mod, clock, sim = _setup(old)
    ok, msg, *_ = _run(mod, sim, make_image(git=0x2), dry_run=True)
    assert ok and msg == "dry run: reached the bootloader, installed 00000001, nothing written", msg
    cmds = [h[0] for h in _host(sim, mod)]
    assert cmds[-1] == mod.CMD_ABORT and mod.CMD_BEGIN not in cmds and sim.mode == "app"

  def test_the_variant_is_read_back_after_the_install(self):
    """Shadow and live built from one commit share a hash, so the commit alone cannot say the install happened. Here
    the boot copy refuses: the board comes back with the old shadow image of the same commit."""
    mod, clock, sim = _setup(make_image(variant=2, git=0xABCDEF01, seed=2))
    sim.fail_copy = True
    ok, msg, *_ = _run(mod, sim, make_image(variant=3, git=0xABCDEF01, seed=3))
    assert not ok and msg == "board reports a shadow image after installing live", msg

  def test_the_trace_says_what_both_boards_did(self):
    """The BLR trace: the engine gate's 0x17C, the lamp board's 0x620 and the EPS board's 0x706/0x70B, per phase; the
    BLR's own block never in the census; and the verdict on the lamps."""
    mod, clock, sim = _setup(make_image(git=0x1))
    ok, msg, phases, logs, traces = _run(mod, sim, make_image(git=0x2))
    assert ok, msg
    (s,) = traces
    assert s["verdict"] == "lamp output off throughout" and s["n"] > 0 and s["d"] > 10
    rows = {r["p"]: r for r in s["phases"]}
    assert rows["pre"]["bst"] == 1 and rows["pre"]["bout"] == 0 and rows["pre"]["st"] == "8/16"
    assert "bst" not in rows["hold"], "the bootloader sends no 0x620"
    assert rows["post"]["bst"] in (0, 1)
    assert any("ring" in r for r in s["phases"]) and all(r.get("sflt", 0) == 0 for r in s["phases"])
    assert not any(k.startswith("62") for r in s["phases"] for k in r.get("lost", {}) | dict.fromkeys(r.get("new", [])))
    assert rows["pre"].get("e0") == 0

  def test_the_trace_verdict_and_file(self):
    mod = _module()

    class T:
      def describe(self): return "replay"
      def send(self, *a): pass
      def poll(self, timeout=0.0): return []
      def close(self): pass

    f = mod.Flasher(T(), log=lambda s: None, target=mod.BLR)
    assert f.summarise_trace() == {}, "nothing traced, nothing to write"
    f._marks = [(0.0, "start"), (5.0, "knock"), (6.0, "end")]
    for i in range(50):
      f._trace.append((i / 10, 0x620, bytes([1 | (0x08 if i == 20 else 0), 0, 0, 0, 0, 0, 3, 0])))
      f._trace.append((i / 10, 0x17C, bytes([0, 0, 0, 0, 0, 0, 0, 0])))
    s = f.summarise_trace()
    assert s["verdict"] == "LAMP OUTPUT ON DURING THE UPDATE" and s["phases"][0]["bout"] == 1
    assert s["phases"][0]["rpm"] == 0
    with tempfile.TemporaryDirectory() as tmp:
      txt = Path(f.save_trace(tmp)).read_text()
    assert txt.startswith("# BLR-v1 flash trace.\n") and "0x620" in txt
    # the trace never swallows a reply or the speed frame, and always keeps the engine gate's 0x17C
    for t in (mod.EPS, mod.BLR):
      assert mod.ENGINE_RPM_ID in t.trace_ids and t.id_board not in t.trace_ids and mod.ENGINE_DATA_ID not in t.trace_ids

  def test_the_wire_names_map_to_the_target_and_nothing_else_goes_out(self):
    mod = _module()
    blr = mod.Flasher(object(), log=lambda s: None, target=mod.BLR)
    eps = mod.Flasher(object(), log=lambda s: None)
    assert (blr._wire_id(mod.ID_HOST), blr._wire_id(mod.ID_DATA)) == (0x628, 0x62A)
    assert (eps._wire_id(mod.ID_HOST), eps._wire_id(mod.ID_DATA)) == (0x710, 0x712)
    for f in (blr, eps):
      for bad in (mod.ID_BOARD, 0x628, 0x62A, 0x1FA, 0x7FF):
        with self.assertRaises(ValueError):
          f._wire_id(bad)

  def test_naks_and_hellos_read_in_the_targets_words(self):
    mod = _module()
    nak10 = bytes([0x81, 0x01, 10, 0, 0, 0, 0, 0])
    assert mod.describe_nak(nak10, mod.BLR.errs) == "lamp output is on"
    assert mod.describe_nak(nak10) == "10", "the EPS has no code 10"
    hello = bytes([0x83, 1, 0x03 | 0x30]) + (0x1234).to_bytes(4, "little") + bytes([2])
    h = mod.describe_hello(hello, mod.BLR)
    assert (h["git"], h["appOk"], h["variant"], h["cls"], h["eccFixed"]) == ("00001234", True, "live", 2, False)
    ecc = hello[:2] + bytes([hello[2] | 0x40]) + hello[3:]
    assert mod.describe_hello(ecc, mod.BLR)["eccFixed"] is True and mod.describe_hello(ecc, mod.BLR)["variant"] == "live"
    assert "variant" not in mod.describe_hello(hello), "the EPS HELLO is read as it always was"
    assert set(mod.describe_hello(ecc)) == {"proto", "appOk", "appId", "dirty", "staged", "git"}


if __name__ == "__main__":
  unittest.main()
