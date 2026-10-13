"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

push9 (owner, 2026-10-13: "countdown add that"): the board update on screen. GatewayFlashDialog shows what
flash_view() makes of three params - the hook's EpsLkasFlashState, the flasher's EpsLkasFlashPhase and the page's own
EpsLkasFlashRequested - so the words, the countdown and when Cancel is offered are pinned here, on the pure function.
The dialog itself is drawn for real by the render harness (scratchpad/push9/renders).
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
BOARD_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/board.py"
FLASHER = ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_flasher.py"


def _board():
  from openpilot.selfdrive.ui.sunnypilot.mici.layouts import board
  return board


def _flasher():
  from openpilot.sunnypilot.selfdrive.pandad import eps_lkas_flasher
  return eps_lkas_flasher


class TestFlashView(unittest.TestCase):
  def test_the_owners_sequence(self):
    b = _board()
    v = b.flash_view
    assert v("", None, True, False) == ("starting", "stopping the car link", None, True)
    assert v("running", {"p": "check", "cancel": True}, False, False)[0] == "checking"
    assert v("running", {"p": "click", "cancel": False}, False, False)[:2] == ("relays to stock",
                                                                               "the car is on stock wiring")
    for left in (5, 4, 3, 2, 1):
      assert v("running", {"p": "stock", "cancel": True, "left": left}, False, False) == \
        ("relays in stock", f"updating in {left}", None, True)
    assert v("running", {"p": "erase", "cancel": False}, False, False)[0] == "erasing"
    assert v("running", {"p": "write", "cancel": False, "pct": 37}, False, False) == \
      ("writing 37%", "keep the car parked", 37, False)
    assert v("running", {"p": "install", "cancel": False}, False, False)[0] == "installing"
    assert v("running", {"p": "restart", "cancel": False}, False, False)[0] == "restarting the board"
    assert v("ok 1234abcd", {"p": "ok"}, False, False) == \
      ("done", "turn the car off, then on, before driving", None, False)

  def test_cancel_is_offered_exactly_where_the_flasher_takes_it(self):
    b = _board()
    for p, cancel in (("check", True), ("baseline", True), ("click", False), ("stock", True), ("erase", False),
                      ("write", False), ("install", False), ("restart", False)):
      assert b.flash_view("running", {"p": p, "cancel": cancel, "left": 3, "pct": 5}, False, False)[3] is cancel, p
    # once pressed, never again - and the screen says so
    assert b.flash_view("running", {"p": "stock", "cancel": True, "left": 3}, False, True) == \
      ("relays in stock", "canceling", None, False)
    assert b.flash_view("", None, True, True) == ("starting", "canceling", None, False)

  def test_a_cancel_too_late_is_never_called_canceling(self):
    """push9 review: a Cancel the flasher no longer takes (the erase has begun) showed 'canceling' over 'erasing',
    'writing' and 'done'. From the erase on the screen says it was too late."""
    b = _board()
    for p in ("erase", "install", "restart"):
      title, line, _, can = b.flash_view("running", {"p": p, "cancel": False}, False, True)
      assert line == "too late to cancel" and not can, p
    assert b.flash_view("running", {"p": "write", "cancel": False, "pct": 40}, False, True) == \
      ("writing 40%", "too late to cancel", 40, False)

  def test_the_endings(self):
    b, f = _board(), _flasher()
    assert b.flash_view(f.CANCELLED, None, False, True) == ("cancelled", "the board keeps its firmware", None, False)
    assert b.flash_view(f"failed: {f.ENGINE_RUNNING}", None, False, False) == \
      ("engine on", "turn the engine off - leave the ignition on - then update", None, False)
    assert b.flash_view(f"failed: {f.IGNITION_OFF}", None, False, False)[0] == "ignition off"
    assert b.flash_view(f"failed: {f.NO_ENGINE_DATA}", None, False, False)[0] == "no engine data"
    assert b.flash_view("failed: no HELLO", None, False, False) == ("failed", "no HELLO", None, False)

  def test_the_confirm_opens_the_screen_and_clears_a_stale_cancel(self):
    src = BOARD_PANEL.read_text()
    tree = ast.parse(src)
    confirm = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "confirm")
    seg = ast.get_source_segment(src, confirm) or ""
    assert "GatewayFlashDialog()" in seg
    assert seg.index("put_bool(CANCEL_PARAM, False)") < seg.index("put_bool(REQUEST_PARAM, True)"), \
      "a Cancel left from an earlier run would cancel this one"
    assert 'tr("engine off\\nslide to update")' in src, "the slider does not say engine off"
