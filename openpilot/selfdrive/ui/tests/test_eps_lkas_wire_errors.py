"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-RELAY): the EPS update's ERROR PATHS, frozen - the second half of H1.

test_eps_lkas_flasher.py's test_eps_wire_transcript_is_frozen (H1) pins the happy path, the cancel points and the
engine gate, and stubs the trace file. The BLR refactor also touched the chunk retry loop (retry_naks), the NAK texts
(errs), the image refusals, the FINISH and the HELLO wait - and a change to any of them on the EPS side would make an
update that recovers in the car today give up there, with every H1 scenario still green (the BLR-v1 review, lens A:
mutations of exactly those survived all 75 flasher tests). So this pins, the same way and captured the same way - on
the unmodified flasher, sunnypilot nsw-live 55d9562b6 - what H1 leaves out:

  CEND        a NAK 3, 6, 1, 8 or 10 and the chunk sent again (the EPS retries on ANY NAK, three attempts); a lost CEND
              ACK asked about again, once, twice, four times
  the session a NAK at BEGIN (flash, with FLASH_SR) and at ENTER (moving); a lost FINISH ACK (one FINISH, 20 s - no
              retry on the EPS) and a FINISH NAK; HELLO byte 7 = 2 or 0xFF (the EPS checks no board class); an ENTER
              ACK before the HELLO (the EPS waits for the HELLO); INFO saying a 96 KB slot (not checked on the EPS);
              the wrong commit after the install
  images      dirty and read-only (installed, with the warning), an even reset vector (installed: the EPS is not
              strict), a standalone image (refused, by its words)
  the trace   every run writes its trace file - its header, every row - into a temporary directory, and the file's
              hash and the log's "steering trace: N frames -> ..." line are in the transcript

Every frame handed to the transport (fake time, id, bytes), every phase, log line, progress callback, the trace
summary, the result and the trace file, hashed against literals. H1 itself is unmodified; its SimBoard, FakeTime and
image are loaded from its file, never copied. A mismatch here is the EPS update changing: a bench run (spare board,
candleLight, A to B to A) before it may reach the car - not a test to re-capture.

The one intended difference from nsw-live is pinned by hand below: a BLR1 (or BLB1) image is refused on the EPS by
name, before a frame.
"""
import hashlib
import importlib.util
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
H1_FILE = HERE / "test_eps_lkas_flasher.py"
FLASHER = HERE.parents[2] / "sunnypilot/selfdrive/pandad/eps_lkas_flasher.py"


def _load(path: Path, name: str):
  """By file, never registered in sys.modules - as every flasher test loads the flasher. Loading H1's file this way
  also keeps its TestCases out of this module, so they are not run twice."""
  spec = importlib.util.spec_from_file_location(name, path)
  assert spec is not None and spec.loader is not None
  mod = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(mod)
  return mod


def _images(h1) -> dict[str, bytes]:
  """H1's golden image, and the variants of it the scenarios install."""
  img = h1._golden_image()

  def flags(v: int) -> bytes:
    b = bytearray(img)
    struct.pack_into("<I", b, 0x10C, v)
    return bytes(b)

  even = bytearray(img)
  struct.pack_into("<I", even, 4, 0x08004200)
  standalone = bytearray(img)
  struct.pack_into("<I", standalone, 0x100, 0xD30D428B)
  return {"golden": img, "dirty": flags(0x01), "readonly": flags(0x03), "even_pc": bytes(even),
          "standalone": bytes(standalone)}


# What each scenario does to H1's SimBoard. cend {chunk: (NAK code or "drop", how many times)}; finish (code or "drop",
# times); begin_nak / enter_nak a code; hello_b7 HELLO's byte 7; enter_ack_first an ENTER ACK [80 01 01 .. 02] to the
# knock 10 ms before the HELLO; info_kb INFO's slot size; git_new the commit the board reports after the REBOOT.
SCENARIOS: dict[str, dict[str, Any]] = {
  "full_with_trace_file": {},
  "cend_nak_crc_once": {"cend": {2: (3, 1)}},
  "cend_nak_short_twice": {"cend": {3: (6, 2)}},
  "cend_nak_sequence_3x": {"cend": {1: (1, 3)}},
  "cend_nak_8_retried": {"cend": {1: (8, 1)}},
  "cend_nak_10_3x": {"cend": {4: (10, 3)}},
  "cend_ack_lost": {"cend": {5: ("drop", 1)}},
  "cend_ack_lost_twice": {"cend": {5: ("drop", 2)}},
  "cend_ack_lost_4x": {"cend": {6: ("drop", 4)}},
  "begin_nak_flash": {"begin_nak": 4},
  "enter_nak_moving": {"enter_nak": 5},
  "finish_ack_lost": {"finish": ("drop", 1)},
  "finish_nak_crc": {"finish": (3, 1)},
  "hello_byte7_2": {"hello_b7": 2},
  "hello_byte7_ff": {"hello_b7": 0xFF},
  "enter_ack_before_hello": {"enter_ack_first": True},
  "info_slot_96kb": {"info_kb": 96},
  "wrong_commit_after": {"git_new": 0x11112222},
  "dirty_image": {"image": "dirty"},
  "readonly_image": {"image": "readonly"},
  "even_pc_image": {"image": "even_pc"},
  "standalone_image": {"image": "standalone"},
}


def _transcript(scenario: str, flasher: Path | None = None) -> list[str]:
  """One run_flash of `scenario` against H1's SimBoard on its fake clock, as H1's _wire_transcript writes it, plus
  each trace file the run wrote: "file <name> <bytes> <sha256>"."""
  h1 = _load(H1_FILE, "test_eps_lkas_flasher_for_wire_errors")
  mod = _load(FLASHER if flasher is None else flasher, "eps_lkas_flasher")
  clock = h1.FakeTime()
  mod.time = clock
  sc = SCENARIOS[scenario]
  seen: dict[str, Any] = {"cend": {}, "finish": 0}
  lines: list[str] = []

  class Board(h1.SimBoard):
    def _swallow(self, can_id, data):
      """What SimBoard.send does with every frame before it answers it: logged, and echoed."""
      self.sent.append((self.clock.now, can_id, data))
      self.queue.append((self.clock.now, can_id, data.ljust(8, b"\x00"), self.mod.ECHO))

    def send(self, can_id, data):
      m = self.mod
      data = bytes(data)
      lines.append(f"tx {self.clock.now:.6f} {can_id:03x} {data.hex()}")
      if can_id == m.ID_HOST:
        cmd = data[0]
        if cmd == m.CMD_CEND and self.session:
          idx = struct.unpack_from("<H", data, 1)[0]
          n = seen["cend"].get(idx, 0)
          seen["cend"][idx] = n + 1
          rule = sc.get("cend", {}).get(idx)
          if rule and n < rule[1]:
            self._swallow(can_id, data)
            if rule[0] != "drop":
              self._reply(0.003, [m.RSP_NAK, m.CMD_CEND, rule[0]])
            return
        if cmd == m.CMD_FINISH and self.session and sc.get("finish") and seen["finish"] < sc["finish"][1]:
          seen["finish"] += 1
          self._swallow(can_id, data)
          if sc["finish"][0] != "drop":
            self._reply(0.05, [m.RSP_NAK, m.CMD_FINISH, sc["finish"][0]])
          return
        if cmd == m.CMD_BEGIN and self.session and sc.get("begin_nak"):
          self._swallow(can_id, data)
          self._reply(0.01, [m.RSP_NAK, m.CMD_BEGIN, sc["begin_nak"], 7, 0x34, 0x12, 0, 0])
          return
        if cmd == m.CMD_ENTER and self.state == "boot" and sc.get("enter_nak"):
          self._swallow(can_id, data)
          self._reply(0.002, [m.RSP_NAK, m.CMD_ENTER, sc["enter_nak"]])
          return
        if cmd == m.CMD_ENTER and self.state == "app" and data[1:8] == m.MAGIC and sc.get("enter_ack_first"):
          super()._reply(0.01, [m.RSP_ACK, m.CMD_ENTER, m.PROTO_VERSION, 0, 0, 0, 0, 2])
      super().send(can_id, data)

    def _reply(self, dt, data):
      data = bytes(data)
      if data[0] == self.mod.RSP_HELLO and sc.get("hello_b7") is not None:
        data = data.ljust(7, b"\x00")[:7] + bytes([sc["hello_b7"]])
      if data[0] == self.mod.RSP_INFO and sc.get("info_kb") is not None:
        data = data[:2] + bytes([sc["info_kb"] & 0xFF, sc["info_kb"] >> 8]) + data[4:]
      super()._reply(dt, data)

  with tempfile.TemporaryDirectory() as tmp:
    save = mod.Flasher.save_trace
    mod.Flasher.save_trace = lambda self, directory=None: save(self, tmp)   # the real writer, never /data
    board = Board(mod, clock, sc.get("git_new", 0x5EED1234), rpm=0, speed_cph=0)
    board.ignition = lambda: True

    def phase(d):
      lines.append("ph " + json.dumps(d, sort_keys=True))

    ok, msg = mod.run_flash(board, _images(h1)[sc.get("image", "golden")],
                            log=lambda s: lines.append("log " + s.replace(tmp, "<trace dir>")),
                            progress=lambda p: lines.append(f"pct {p}"),
                            trace_out=lambda d: lines.append("trace " + json.dumps(d, sort_keys=True)),
                            phase=phase, should_cancel=lambda: False)
    lines.append(f"= {ok} {msg}")
    for name in sorted(os.listdir(tmp)):
      raw = (Path(tmp) / name).read_bytes()
      lines.append(f"file {name} {len(raw)} {hashlib.sha256(raw).hexdigest()}")
  return lines


def _digest(lines: list[str]) -> str:
  return hashlib.sha256("\n".join(lines).encode()).hexdigest()


# sha256 of each scenario's transcript, captured on the unmodified flasher (sunnypilot nsw-live 55d9562b6), with H1's
# SimBoard at 3e059dd8d. The comment: frames sent, and how it ends. HELLO byte 7 and an ENTER ACK before the HELLO are
# the full run exactly: the EPS reads neither, and must not start to - the BLR target's rules stay the BLR's.
EPS_ERROR_GOLDEN = {
  "full_with_trace_file": "ea2e64c1828c1ab361e17d0b7bf65fcbd9176846b307f2c205c4f31d29261339",      # 351, ok; H1's full run plus its trace file
  "cend_nak_crc_once": "5b5a718ab800cda3f9a803d748f2b72f6fd3ed19355e2708059d0d1a758a62c3",         # 385, ok
  "cend_nak_short_twice": "7392eb678f4a2a9c924fee2d6682a4373d8450b34b8560a6025a03b3d81e7f57",      # 419, ok
  "cend_nak_sequence_3x": "c7affbb1b862d2914c0b76de343444326638c53c17530a0b49e5d6fd3c7e4a29",      # 142, chunk 1 refused
  "cend_nak_8_retried": "9321ae4dd648de97719d4c69bcc4c7b6cae8dad7a883c707d623b42d96d5d6e8",        # 385, ok
  "cend_nak_10_3x": "3f5a354e60aab9b6094cac66f83746c1f2f356c3a369b6e8dd13dc95f3bd532a",            # 244, chunk 4 refused: 10
  "cend_ack_lost": "d125ad5b0dc1b09923205e06e17c754109d253ebf43cce2a26ddf45c9c1b3713",             # 352, ok
  "cend_ack_lost_twice": "627daf35e47fd280d0fc2c5cf05a0d8184f665a87e1758085325121f61a81d76",       # 386, ok
  "cend_ack_lost_4x": "9288005a766bf30ffdc1aa96eb652949d7eaf75d4780274c906a344e572ffa1f",          # 421, ok
  "begin_nak_flash": "cd2d05e987a761019e19ba691a7e6ddcb3b53cf648706cf837ee57aa50bf1bd5",           # 6, BEGIN refused
  "enter_nak_moving": "5a292d713cbcf9293b0aa9555cf2c9ec5c171f307cef0bc8193b083376f4d824",          # 2, refused
  "finish_ack_lost": "61d329875c69e3088fcf2e566e1bbe7ca8da733fceee734706e05deaaf12a85f",           # 350, no ACK for FINISH in 20 s
  "finish_nak_crc": "41949dd3ba17214a865e1d05bef51e396ab0ad16eebce1243c99393c00bca694",            # 350, FINISH refused
  "hello_byte7_2": "ea2e64c1828c1ab361e17d0b7bf65fcbd9176846b307f2c205c4f31d29261339",             # 351, = full_with_trace_file
  "hello_byte7_ff": "ea2e64c1828c1ab361e17d0b7bf65fcbd9176846b307f2c205c4f31d29261339",            # 351, = full_with_trace_file
  "enter_ack_before_hello": "ea2e64c1828c1ab361e17d0b7bf65fcbd9176846b307f2c205c4f31d29261339",    # 351, = full_with_trace_file
  "info_slot_96kb": "7c7fb81ecb98718b82af3e76488f5a68f36cc614139b1e3bc8358ac4f6ca8d61",            # 351, ok
  "wrong_commit_after": "93a808cc1c9cec9dfb534ecc350b5df679c7399d29ae30c7b091a561ba9199e0",        # 351, wrong commit
  "dirty_image": "9980201eaf3347cf39ec83839d13fce27aeb98d920b1ce15766e8ff062437adb",               # 351, ok, warned
  "readonly_image": "c1e661089f239b1c6c076b0220a2e244f761220c3d8c9c5b3220dee6abc6d8f1",            # 351, ok
  "even_pc_image": "d243e41f937eca80611e91d51e440413dd415a7062957a35ffed0450422e018d",             # 351, ok
  "standalone_image": "cebd534e414889bae410377f428866600ebaee793d2c0a8fc412fd14eeac93d7",          # 0, refused
}


class TestEpsWireErrors(unittest.TestCase):
  def test_eps_error_paths_are_frozen(self):
    """THE EPS UPDATE PATH IS PROVEN IN THE CAR, its recoveries included: a chunk the board NAKs is sent again, a lost
    ACK is asked about before the chunk is resent, and FINISH is sent once and given 20 s. What every one of these
    puts on the wire, tells the screen and the log, and writes to its trace file must stay as captured."""
    assert set(EPS_ERROR_GOLDEN) == set(SCENARIOS)
    for name, want in EPS_ERROR_GOLDEN.items():
      lines = _transcript(name)
      tx = sum(1 for ln in lines if ln.startswith("tx "))
      assert _digest(lines) == want, \
        f"{name}: the EPS error-path transcript changed ({len(lines)} lines, {tx} frames, last: {lines[-2:]!r})"

  def test_the_scenarios_do_what_they_say(self):
    """The hashes mean something only if each run took the path its name says. Spot checks, on the same runs."""
    def result(name):
      lines = _transcript(name)
      return lines, next(ln for ln in reversed(lines) if ln.startswith("= "))

    lines, end = result("full_with_trace_file")
    assert end == "= True 5eed1234", end
    assert any(ln.startswith("log   steering trace: ") and "-> <trace dir>/flash-" in ln for ln in lines)
    assert len([ln for ln in lines if ln.startswith("file flash-")]) == 1
    for name, frag in (("cend_nak_sequence_3x", "chunk 1: board refused CEND: command out of sequence"),
                       ("cend_nak_10_3x", "chunk 4: board refused CEND: 10"),
                       ("begin_nak_flash", "board refused BEGIN: flash erase/program failed [driver code 7"),
                       ("enter_nak_moving", "refused: the car is not stationary"),
                       ("finish_ack_lost", "no ACK for FINISH within 20.0s"),
                       ("finish_nak_crc", "board refused FINISH: CRC mismatch"),
                       ("wrong_commit_after", "board reports 11112222 after installing 5eed1234"),
                       ("standalone_image", "this looks like a STANDALONE image")):
      _, end = result(name)
      assert end.startswith("= False") and frag in end, (name, end)
    for name in ("cend_nak_crc_once", "cend_nak_short_twice", "cend_nak_8_retried", "cend_ack_lost",
                 "cend_ack_lost_twice", "cend_ack_lost_4x", "hello_byte7_2", "hello_byte7_ff", "enter_ack_before_hello",
                 "info_slot_96kb", "dirty_image", "readonly_image", "even_pc_image"):
      lines, end = result(name)
      assert end == "= True 5eed1234", (name, end)
    lines, _ = result("finish_ack_lost")
    finishes = [ln for ln in lines if ln.startswith("tx ") and ln.split()[2] == "710" and ln.split()[3].startswith("07")]
    assert len(finishes) == 1, "the EPS sends FINISH once"
    lines, _ = result("dirty_image")
    assert any("WARNING: built from an edited tree - 0x707" in ln for ln in lines)

  def test_a_blr_image_is_refused_on_the_eps_by_name(self):
    """The intended difference from nsw-live (whose refusal called a BLR1 image a standalone one): by name, and
    before a single frame."""
    h1 = _load(H1_FILE, "test_eps_lkas_flasher_for_wire_errors")
    mod = _load(FLASHER, "eps_lkas_flasher")
    mod.time = clock = h1.FakeTime()
    mod.Flasher.save_trace = lambda self, directory=None: None
    for magic, what in ((0x31524C42, "a BLR-v1 lamp-board image (BLR1 marker)"),
                        (0x31424C42, "the BLR-v1 BOOTLOADER (BLB1 record)")):
      img = bytearray(h1._golden_image())
      struct.pack_into("<I", img, 0x100, magic)
      board = h1.SimBoard(mod, clock, 0x5EED1234)
      ok, msg = mod.run_flash(board, bytes(img), log=lambda s: None)
      assert not ok and msg.startswith(f"refusing this image: this is {what}") and \
        msg.endswith("must never be installed on the EPS-LKAS board"), msg
      assert board.sent == []


if __name__ == "__main__":
  unittest.main()
