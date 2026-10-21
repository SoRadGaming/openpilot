"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-RELAY): Settings > gateway with the second board, the BLR-v1 brake-lamp board (DESIGN E.3, E.6).

The page gets the lamp board's card and its own update button beside the EPS-LKAS board's, from its own Blr* params.
What must hold:

  separate     each button writes only its own board's params and opens its own board's screen
  exclusive    only one update can ever be in flight: each button's _busy() is both boards'
  hidden       the lamp board's pair is not on the page until the board has spoken (BlrBoardVersion)
  honest       no image bundled is no offer ("no image"); "up to date" is the hash AND the variant (a shadow and a
               live image of one commit share a hash); 0x62F not heard yet is never read as "no bootloader"
  its words    the lamp board's screen says lamp output off / brake lamps pedal-only, never relays, and has no
               "turn the car off, then on"
  EPS as was   the EPS card and button say and do what they did, with the lamp board's params present or not

The pure parts are tested in-process (board.py imports without a window, as test_gateway_flash_screen.py does). The
page itself is driven for real in a headless raylib window in a child process (the test_software_update_button.py
pattern): a raylib that cannot open a window must not take the test runner's worker down with it. No window at all
is a skip; anything after the window opened is a real result.
"""
import dataclasses
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
BLR1, APL1 = 0x31524C42, 0x314C5041


def _board():
  from openpilot.selfdrive.ui.sunnypilot.mici.layouts import board
  return board


def _seal(body: bytes) -> bytes:
  """As the BLR post-build seals every app .bin (review round 1, BOOT-6): 0xFF to a double word, then {BLRT, the
  whole length, CRC-32 of the rest} (test_blr_flasher.py sealed())."""
  body = bytes(body) + b"\xff" * ((-(len(body) + 12)) % 8)
  return body + struct.pack("<III", 0x54524C42, len(body) + 12, zlib.crc32(body) & 0xFFFFFFFF)


def _image(magic=BLR1, git=0x9F8E7D6C, variant=2, dirty=0, size=0x400, trailer=True) -> bytes:
  d = bytearray(size)
  struct.pack_into("<II", d, 0, 0x20024000, 0x08004201)
  struct.pack_into("<4I", d, 0x100, magic, 0x08004000, git, (dirty & 1) | (variant << 4))
  return _seal(bytes(d)) if trailer else bytes(d)


class TestLampBoardScreen(unittest.TestCase):
  """flash_view() in the lamp board's words. The EPS's are pinned, unchanged, by test_gateway_flash_screen.py."""

  def test_the_lamp_boards_sequence(self):
    b = _board()

    def v(*a):
      return b.flash_view(*a, target=b.BLR)

    assert v("", None, True, False) == ("starting", "stopping the car link", None, True)
    assert v("running", {"p": "check", "cancel": True}, False, False)[0] == "checking"
    assert v("running", {"p": "click", "cancel": False}, False, False) == \
      ("lamp output off", "brake lamps are pedal-only", None, False)
    for left in (5, 3, 1):
      assert v("running", {"p": "stock", "cancel": True, "left": left}, False, False) == \
        ("lamp output off", f"updating in {left}", None, True)
    assert v("running", {"p": "write", "cancel": False, "pct": 37}, False, False) == \
      ("writing 37%", "keep the car parked", 37, False)
    assert v("ok 9f8e7d6c", {"p": "ok"}, False, False) == ("done", "", None, False)
    assert v("cancelled", None, False, True) == ("cancelled", "the board keeps its firmware", None, False)
    assert v("failed: refused: lamp output is on", None, False, False) == \
      ("failed", "refused: lamp output is on", None, False)

  def test_no_relay_and_no_restart_wording_on_the_lamp_board(self):
    """The EPS's relays and its "turn the car off, then on" (the vibration workaround) are the EPS's alone."""
    b = _board()
    states = [("", None, True), ("ok 1", None, False), ("cancelled", None, False), ("failed: x", None, False)]
    states += [("running", {"p": p, "cancel": c, "left": 2, "pct": 9}, False)
               for p in ("check", "baseline", "click", "stock", "erase", "write", "install", "restart", "other")
               for c in (True, False)]
    for state, phase, req in states:
      for sent in (False, True):
        title, line, _, _ = b.flash_view(state, phase, req, sent, target=b.BLR)
        text = f"{title} {line}".lower()
        assert "relay" not in text and "turn the car off" not in text, (state, phase, title, line)
    # and the EPS still says both
    assert b.flash_view("running", {"p": "click"}, False, False)[0] == "relays to stock"
    assert b.flash_view("ok 1", None, False, False)[1] == "turn the car off, then on, before driving"


class TestLampBoardCard(unittest.TestCase):
  BUILD = {"dirty": False, "variant": 2, "bootloader": True, "bootGit": "b28113", "uid": "a5c3e1", "borEn": True,
           "dualBank": True}

  def test_the_card(self):
    b = _board()
    ok = {"faults": 0, "faultNames": [], "latch": 0, "latchName": ""}
    assert b.lamp_board_lines("1a2b3c4d", self.BUILD, "12 min ago", ok) == \
      ("1a2b3c4d • shadow • 12 min ago", "board id a5c3e1 • boot b28113", "last drive: ok")
    live = {**self.BUILD, "variant": 3, "dirty": True}
    assert b.lamp_board_lines("1a2b3c4d", live, "", ok)[0] == "1a2b3c4d • live (dirty)"
    assert b.lamp_board_lines("", {}, "", None) == ("never seen", "drive once to read it", "")

  def test_what_0x62f_has_not_said_is_left_out_not_read_as_no(self):
    b = _board()
    assert b.lamp_board_lines("1a2b3c4d", {"dirty": False, "variant": 2}, "", None)[1] == "board id unknown"
    no = {**self.BUILD, "bootloader": False, "dualBank": False}
    assert b.lamp_board_lines("1a2b3c4d", no, "", None)[1] == "board id a5c3e1 • no bootloader • not dual-bank"

  def test_the_last_drive(self):
    b = _board()
    t = b.last_drive_text
    assert t({"faults": 0, "faultNames": [], "latch": 0, "latchName": ""}) == "ok"
    assert t({"faults": 0xA0, "faultNames": ["D_SENSE_MID", "F_FEEDBACK"], "latch": 0}) == "D_SENSE_MID, F_FEEDBACK"
    # a latch first: it is what kept the output off for the rest of that power cycle
    assert t({"faults": 0x80, "faultNames": ["F_FEEDBACK"], "latch": 2, "latchName": "FEEDBACK"}) == "LATCHED FEEDBACK"
    assert t({"faults": 0x01}) == "0x01"
    for bad in (None, "x", [], {"latch": "oops"}, {"faults": "?"}):
      assert t(bad) == "unknown", bad


class TestBundledOffer(unittest.TestCase):
  """What the lamp board's button offers is the flasher's own verdict on the whole image: what it would refuse is never
  offered. The EPS's is read from the marker, as it always was."""

  def _offer(self, data: bytes | None, target=None):
    b = _board()
    with tempfile.TemporaryDirectory() as d:
      path = os.path.join(d, "blr_appslot.bin")
      if data is not None:
        with open(path, "wb") as fh:
          fh.write(data)
      return b.bundled_offer(dataclasses.replace(target or b.BLR, image_path=path))

  def test_shadow_and_live_are_offered_by_hash_and_variant(self):
    assert self._offer(_image(variant=2)) == ("9f8e7d6c", "shadow")
    assert self._offer(_image(variant=3, git=0x12345678)) == ("12345678", "live")

  def test_nothing_the_comma_would_never_install_is_offered(self):
    assert self._offer(None) == ("", ""), "no file"
    assert self._offer(_image(variant=1)) == ("", ""), "a bench image"
    assert self._offer(_image(variant=0)) == ("", ""), "VARIANT 0"
    assert self._offer(_image(dirty=1)) == ("", ""), "a dirty tree"
    assert self._offer(_image(magic=APL1)) == ("", ""), "the EPS's image"
    assert self._offer(b"\x00" * 0x80) == ("", ""), "too short to hold a marker"
    # the whole file, not its head: a copy cut short keeps its marker and vectors, and only its trailer says so
    good = _image(variant=2, size=0x800)
    assert self._offer(good) == ("9f8e7d6c", "shadow")
    assert self._offer(good[:-12]) == ("", ""), "the trailer cut off"
    assert self._offer(good[:0x600]) == ("", ""), "a truncated copy"
    assert self._offer(_image(variant=2, trailer=False)) == ("", ""), "a bare objcopy .bin, never sealed"
    bad = bytearray(good)
    bad[0x500] ^= 0x01
    assert self._offer(bytes(bad)) == ("", ""), "changed after the build"
    even = bytearray(_image(variant=2, size=0x800, trailer=False))
    struct.pack_into("<I", even, 4, 0x08004200)
    assert self._offer(_seal(bytes(even))) == ("", ""), "an even reset vector, sealed: the bootloader's own rules too"
    # and the EPS's offer still reads the marker alone: no trailer, no full parse
    assert self._offer(_image(magic=APL1, variant=0, trailer=False), target=_board().EPS) == ("9f8e7d6c", "")
    # and the EPS refuses the lamp board's image the same way
    assert self._offer(_image(magic=BLR1), target=_board().EPS) == ("", "")
    assert self._offer(_image(magic=APL1, variant=0), target=_board().EPS) == ("9f8e7d6c", "")

  def test_no_lamp_board_image_is_no_offer(self):
    """No blr_appslot.bin is no offer. It used to run only while none was bundled; since push11 bundles one it runs on
    a path that does not exist, so it never skips."""
    assert self._offer(None) == ("", "")

  def test_the_lamp_board_offer_is_the_bundled_images_commit_and_variant(self):
    """The bundled image (push11: live) through the page's own function: the flasher's verdict, its hash and VARIANT."""
    from openpilot.sunnypilot.selfdrive.pandad import eps_lkas_flasher as fl
    b = _board()
    if not os.path.exists(b.BLR.image_path):
      self.skipTest("no blr_appslot.bin bundled")
    image = Path(b.BLR.image_path).read_bytes()
    assert fl.check_app_slot_image(image, fl.BLR) is None, fl.check_app_slot_image(image, fl.BLR)
    ident = fl.image_identity(image, fl.BLR)
    assert ident["variant"] in ("shadow", "live") and not ident["dirty"], ident
    assert b.bundled_offer(b.BLR) == (ident["git"], ident["variant"])
    assert b.bundled_firmware(b.BLR) == ident["git"]
    # and the EPS board's offer is still its own image, never this one
    assert b.bundled_offer(b.EPS)[0] != ident["git"]

  def test_the_eps_offer_is_the_bundled_images_commit(self):
    """The twin of test_bundled_hash_read_agrees_with_the_full_parse, through the page's own function."""
    from openpilot.sunnypilot.selfdrive.pandad import eps_lkas_flasher as fl
    b = _board()
    image = Path(fl.EPS_LKAS_APPSLOT_BIN).read_bytes()
    assert b.bundled_offer(b.EPS) == (fl.image_identity(image)["git"], "")
    assert b.bundled_firmware() == fl.image_identity(image)["git"]


CHILD = r'''
import dataclasses, json, time
import pyray as rl
from openpilot.system.ui.lib.application import gui_app, FontWeight
gui_app.init_window("gateway-lamp-board-test")
print("INIT_OK", flush=True)

from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.selfdrive.ui.sunnypilot.mici.layouts import board as B


class FakeParams:
  def __init__(self):
    self.v = {}
  def get(self, key, block=False, return_default=False):
    return self.v.get(key)
  def get_bool(self, key, block=False):
    return bool(self.v.get(key))
  def put(self, key, value, block=False):
    self.v[key] = value
  def put_bool(self, key, value, block=False):
    self.v[key] = bool(value)
  def remove(self, key):
    self.v.pop(key, None)


P = FakeParams()
ui_state.params = P
ui_state.ignition = True
ui_state.started = False
now = int(time.time())
EPS = {"EpsLkasBoardVersion": "686e4603", "EpsLkasBoardSeenAt": str(now - 600),
       "EpsLkasBoardBuild": {"dirty": False, "appSlot": True, "bootloader": True, "readOnly": False, "uid": "256625"}}
LAMP = {"BlrBoardVersion": "1a2b3c4d", "BlrBoardSeenAt": str(now - 720),
        "BlrBoardBuild": {"dirty": False, "variant": 2, "bootloader": True, "bootGit": "b28113", "uid": "a5c3e1",
                          "borEn": True, "dualBank": True},
        "BlrLastDrive": {"t": now - 4000, "variant": 2, "faults": 0, "faultNames": [], "latch": 0, "latchName": "",
                         "wouldOn": True, "outDrive": False, "extSink": False}}
out = {}


def fresh():
  B._visible_cache[1] = -1e9
  B._lamp_seen_cache[1] = -1e9


def frame(widget, w=536, h=240):
  rl.begin_drawing()
  widget.render(rl.Rectangle(0, 0, w, h))
  rl.end_drawing()


def eps_view(page):
  page._firmware_info.refresh()
  page._update_btn.refresh()
  return {"card": [page._firmware_info.fw_text.text, page._firmware_info.id_text.text],
          "btn": [page._update_btn.text, page._update_btn.value, bool(page._update_btn.enabled)]}


def btn(b):
  b.refresh()
  return [b.value, bool(b.enabled)]


# -- the EPS alone: the page as it was, the lamp board's pair hidden
P.v.update(EPS)
fresh()
page = B.BoardLayoutMici()
page.show_event()
out["eps_only"] = {"page": B.board_page_visible(), "lamp_visible": [page._lamp_info.is_visible,
                   page._lamp_update_btn.is_visible], "eps": eps_view(page),
                   "items": [type(w).__name__ for w in page._scroller._items]}
for _ in range(3):
  frame(page)
out["eps_only"]["shown"] = [type(w).__name__ for w in page._scroller._visible_items]
P.v.clear()
fresh()
out["nothing_seen_page"] = B.board_page_visible()
P.v.update(LAMP)
fresh()
out["lamp_only_page"] = B.board_page_visible()

# -- both boards
P.v.update(EPS)
fresh()
page = B.BoardLayoutMici()
page.show_event()
for _ in range(3):
  frame(page)
lamp, eps = page._lamp_update_btn, page._update_btn
out["both"] = {"lamp_visible": [page._lamp_info.is_visible, lamp.is_visible], "eps": eps_view(page),
               "shown": [type(w).__name__ for w in page._scroller._visible_items],
               "card": [ln.text for ln in page._lamp_info.lines], "lamp_title": lamp.text,
               "lamp_btn": btn(lamp)}
out["titles_fit"] = {b.text: [measure_text_cached(gui_app.font(FontWeight.BOLD), b.text, 48).x, b._title_width_hint()]
                     for b in (eps, lamp)}

# -- the lamp board's gate, with an image on offer (a stand-in: the bundled one is checked further down)
real = B.bundled_offer
offer = {"o": ("9f8e7d6c", "shadow")}
B.bundled_offer = lambda target=B.EPS: offer["o"] if target is B.BLR else real(target)
out["gate"] = {"offer": btn(lamp)}
offer["o"] = ("1a2b3c4d", "shadow")
out["gate"]["same"] = btn(lamp)
offer["o"] = ("1a2b3c4d", "live")
out["gate"]["same_hash_other_variant"] = btn(lamp)
offer["o"] = ("9f8e7d6c", "shadow")
P.v["BlrBoardBuild"] = {"dirty": False, "variant": 2}
out["gate"]["no_0x62f"] = btn(lamp)
P.v["BlrBoardBuild"] = {**LAMP["BlrBoardBuild"], "bootloader": False}
out["gate"]["no_bootloader"] = btn(lamp)
P.v["BlrBoardBuild"] = {**LAMP["BlrBoardBuild"], "dualBank": False}
out["gate"]["single_bank"] = btn(lamp)
P.v["BlrBoardBuild"] = LAMP["BlrBoardBuild"]
ui_state.ignition = False
out["gate"]["ignition_off"] = btn(lamp)
ui_state.ignition = True
ui_state.started = True
out["gate"]["onroad"] = btn(lamp)
ui_state.started = False

# -- one update at a time
before = eps_view(page)
P.v["BlrFlashRequested"] = True
out["busy"] = {"lamp_requested": {"eps": btn(eps), "lamp": btn(lamp)}}
P.v["BlrFlashRequested"] = False
P.v["BlrFlashState"] = "running"
P.v["BlrFlashPhase"] = {"p": "stock", "cancel": True, "left": 3}
out["busy"]["lamp_running"] = {"eps": btn(eps), "lamp": btn(lamp)}
P.v["BlrFlashState"] = "ok 9f8e7d6c"
out["busy"]["lamp_done"] = {"eps": btn(eps), "lamp": btn(lamp)}
P.v["BlrFlashState"] = ""
P.v["EpsLkasFlashState"] = "running"
P.v["EpsLkasFlashProgress"] = "40"
P.v["EpsLkasFlashPhase"] = {"p": "write", "cancel": False, "pct": 40}
out["busy"]["eps_running"] = {"eps": btn(eps), "lamp": btn(lamp)}
P.v["EpsLkasFlashState"] = ""
eps._asked = True
out["busy"]["eps_asked"] = {"lamp": btn(lamp)}
eps._asked = False
out["busy"]["idle_again"] = {"eps": btn(eps), "lamp": btn(lamp)}
out["eps_unchanged_after"] = eps_view(page) == before

# -- the lamp board's confirm writes its own params only, and opens its own screen
P.v.pop("BlrFlashState", None)
before_keys = dict(P.v)
lamp._on_click()
top = gui_app._nav_stack[-1]
out["confirm"] = {"dialog": type(top).__name__}
top._confirm_callback()
pushed = gui_app._nav_stack[-1]
changed = sorted(k for k in P.v if P.v.get(k) != before_keys.get(k))
out["confirm"].update({"changed": changed, "request": P.v.get("BlrFlashRequested"),
                       "screen": type(pushed).__name__, "screen_target": getattr(pushed, "_target", None) is B.BLR,
                       "lamp_after": btn(lamp), "eps_after": btn(eps)})
for _ in range(3):
  frame(pushed)
# the EPS's confirm, now: refused, nothing written
eps_before = dict(P.v)
B.bundled_offer = real
eps._on_click()
dlg = gui_app._nav_stack[-1]
out["eps_confirm_while_lamp_pending"] = {"dialog": type(dlg).__name__}
if hasattr(dlg, "_confirm_callback"):
  dlg._confirm_callback()
out["eps_confirm_while_lamp_pending"]["wrote"] = sorted(k for k in P.v if P.v.get(k) != eps_before.get(k))
# the lamp board's screen's Cancel writes the lamp board's Cancel
P.v["BlrFlashState"] = "running"
P.v["BlrFlashPhase"] = {"p": "stock", "cancel": True, "left": 4}
scr = B.GatewayFlashDialog(B.BLR)
scr._on_cancel()
out["cancel"] = {"blr": P.v.get("BlrFlashCancel"), "eps": P.v.get("EpsLkasFlashCancel"), "view": list(scr._view)}

# -- a run over inside one refresh tick: never seen "running", it must not stay "requested" (and hold both buttons)
P.v.update({"BlrFlashRequested": False, "BlrFlashCancel": False,
            "BlrFlashState": "failed: the ignition is off - turn it on, leave the engine off, then update"})
asked_before = lamp._asked
out["fast_fail"] = {"asked_before": asked_before, "lamp": btn(lamp), "asked": lamp._asked, "eps": btn(eps)}

# -- the image this build bundles (blr_appslot.bin, push11): offered by its own hash and variant, and "up to date" once
# the board reports exactly that commit and variant. The real bundled_offer(), reading the real file.
P.v.update({"BlrFlashRequested": False, "BlrFlashState": "", "BlrFlashCancel": False})
lamp._asked = False
bundled = list(real(B.BLR))
out["bundled"] = {"offer": bundled, "btn": btn(lamp)}
if bundled[0]:
  P.v["BlrBoardVersion"] = bundled[0]
  P.v["BlrBoardBuild"] = {**LAMP["BlrBoardBuild"], "variant": {"shadow": 2, "live": 3}[bundled[1]]}
  out["bundled"]["running_it"] = btn(lamp)
  page._lamp_info.refresh()
  out["bundled"]["card"] = [ln.text for ln in page._lamp_info.lines]
P.v.update(LAMP)

# -- no image bundled: the lamp board's button offers nothing, says why, and its press only explains. The real
# bundled_offer() on a path that does not exist, so this runs whether or not an image is bundled.
B.bundled_offer = lambda target=B.EPS: (real(dataclasses.replace(target, image_path="/nonexistent/blr_appslot.bin"))
                                        if target is B.BLR else real(target))
out["no_image"] = btn(lamp)
lamp._on_click()
out["no_image_press"] = type(gui_app._nav_stack[-1]).__name__
B.bundled_offer = real
print("RESULT " + json.dumps(out), flush=True)
'''


class TestGatewayPageWithTheLampBoard(unittest.TestCase):
  r: dict = {}
  stdout = stderr = ""
  returncode = -1

  @classmethod
  def setUpClass(cls):
    env = {**os.environ, "RAYLIB_BACKEND": "headless", "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    p = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=120)
    cls.stdout, cls.stderr, cls.returncode = p.stdout, p.stderr, p.returncode
    lines = [ln for ln in p.stdout.splitlines() if ln.startswith("RESULT ")]
    cls.r = json.loads(lines[-1][len("RESULT "):]) if lines else {}

  def setUp(self):
    if "INIT_OK" not in self.stdout:
      self.skipTest(f"no headless raylib window here: {self.stderr[-300:]}")
    assert self.returncode == 0 and self.r, self.stderr[-2000:]

  def test_the_lamp_boards_pair_is_hidden_until_it_has_spoken(self):
    e = self.r["eps_only"]
    assert e["page"] is True
    assert e["lamp_visible"] == [False, False]
    assert e["items"] == ["BoardFirmwareInfo", "UpdateBoardButton", "LampBoardInfo", "UpdateBoardButton"]
    assert e["shown"] == ["BoardFirmwareInfo", "UpdateBoardButton"], "the page is not the EPS's alone"
    assert self.r["both"]["lamp_visible"] == [True, True]
    assert self.r["both"]["shown"] == ["BoardFirmwareInfo", "UpdateBoardButton", "LampBoardInfo", "UpdateBoardButton"]
    # the page itself: either board having spoken
    assert self.r["nothing_seen_page"] is False and self.r["lamp_only_page"] is True

  def test_the_eps_pair_is_what_it_was(self):
    e = self.r["eps_only"]["eps"]
    assert e["card"][0].startswith("686e4603 • ") and e["card"][1] == "256625"
    assert e["btn"][0] == "update"
    assert e["btn"][1].startswith("to ") and e["btn"][2] is True
    assert self.r["both"]["eps"] == e, "the lamp board's params changed the EPS's card or button"
    assert self.r["eps_unchanged_after"], "the EPS's card or button did not come back after the lamp board's update"

  def test_the_lamp_boards_card(self):
    assert self.r["both"]["card"] == ["1a2b3c4d • shadow • 12 min ago", "board id a5c3e1 • boot b28113",
                                      "last drive: ok"]

  def test_titles_fit_one_line(self):
    """"lamp board" is 251 px at 48 pt bold against a 252 px title width (402 - 2x40 - the 70 px icon)."""
    assert self.r["both"]["lamp_title"] == "lamp board"
    for title, (w, room) in self.r["titles_fit"].items():
      assert w <= room, f'"{title}" is {w:.1f} px against {room} - it wraps and squeezes out the sub-label'

  def test_the_lamp_boards_gate(self):
    g = self.r["gate"]
    assert g["offer"] == ["to 9f8e7d6c\n(shadow)", True]
    assert g["same"] == ["up to date", False]
    assert g["same_hash_other_variant"] == ["to 1a2b3c4d\n(live)", True], "a live image of the same commit is new"
    assert g["no_0x62f"] == ["drive once", False], "0x62F not heard yet was read as an answer"
    assert g["no_bootloader"] == ["needs SWD once", False]
    assert g["single_bank"] == ["not dual-bank", False]
    assert g["ignition_off"] == ["ignition off", False]
    assert g["onroad"] == ["use always offroad", False]

  def test_only_one_update_can_be_in_flight(self):
    b = self.r["busy"]
    assert b["lamp_requested"] == {"eps": ["busy", False], "lamp": ["requested", False]}
    assert b["lamp_running"] == {"eps": ["busy", False], "lamp": ["in 3", False]}
    assert b["lamp_done"]["eps"][1] is True, "a finished lamp board update still blocks the EPS"
    assert b["eps_running"] == {"eps": ["40%", False], "lamp": ["busy", False]}
    assert b["eps_asked"] == {"lamp": ["busy", False]}, "the EPS button's own 'asked' does not block the lamp board"
    assert b["idle_again"]["eps"][1] is True and b["idle_again"]["lamp"][1] is True

  def test_the_lamp_boards_confirm_is_its_own(self):
    c = self.r["confirm"]
    assert c["dialog"] == "BigConfirmationDialog"
    assert c["changed"] == ["BlrFlashCancel", "BlrFlashPhase", "BlrFlashProgress", "BlrFlashRequested",
                            "BlrFlashState"], c["changed"]
    assert c["request"] is True
    assert c["screen"] == "GatewayFlashDialog" and c["screen_target"] is True
    assert c["lamp_after"] == ["requested", False]
    assert c["eps_after"] == ["busy", False]
    # and the EPS's, while it is pending, writes nothing at all
    assert self.r["eps_confirm_while_lamp_pending"]["wrote"] == []
    assert self.r["cancel"]["blr"] is True and self.r["cancel"]["eps"] is None

  def test_a_run_over_inside_one_tick_releases_both_buttons(self):
    f = self.r["fast_fail"]
    assert f["asked_before"] is True, "the scenario did not start from a confirmed request"
    assert f["lamp"][0] == "failed" and f["asked"] is False, "the button stayed 'requested' after the run ended"
    assert f["eps"][1] is True, "a finished lamp board run still holds the EPS button"

  def test_no_image_bundled_is_no_offer(self):
    assert self.r["no_image"] == ["no image", False]
    assert self.r["no_image_press"] == "BigDialog", "a refused press must explain itself"

  def test_the_bundled_image_is_offered_and_then_up_to_date(self):
    """push11 bundles the live image: the button offers it by hash and variant, and stops once the board runs it."""
    if not (ROOT / "sunnypilot/selfdrive/pandad/blr_appslot.bin").exists():
      self.skipTest("no blr_appslot.bin bundled")
    b = self.r["bundled"]
    git, variant = b["offer"]
    assert len(git) == 8 and variant in ("shadow", "live"), b["offer"]
    assert b["btn"] == [f"to {git}\n({variant})", True], b["btn"]
    assert b["running_it"] == ["up to date", False], b["running_it"]
    assert b["card"] and b["card"][0].startswith(f"{git} • {variant} • "), b["card"]
