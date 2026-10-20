"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): KTuner fix 1's hooks in card.py. What must hold: the guard sees every step's frames before
controls run; it can hold back only the FIRST controls_update (CI.init + ControlsReady), and the release runs that
first call exactly as a normal start does; once controls have started nothing stops sendcan; no Params I/O on the
100 Hz path; and controls_update itself - the path the ELESYS stand-down is sent on - is byte-for-byte nsw-live's.
"""
import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openpilot.common.test import OpenpilotTestCase

ROOT = Path(__file__).parents[4]   # openpilot/
CARD = ROOT / "selfdrive/car/card.py"

# ast.dump() of Car.controls_update at nsw-live 55d9562b6 (no line numbers; CPython 3.12's ast). This is the path
# CI.apply() sends the ELESYS stand-down (SCM_BUTTONS, MAIN_ON=0) on: a change here must be re-reviewed against the
# stand-down, then this pin updated.
CONTROLS_UPDATE_SHA256 = "772eb5619b47ccf08bb394235f71d4d2264373b9ebdf6b8e68c15ada8c2e27cf"


def card_class() -> ast.ClassDef:
  tree = ast.parse(CARD.read_text(encoding="utf-8"))
  return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Car")


def method(name: str) -> ast.FunctionDef:
  return next(n for n in card_class().body if isinstance(n, ast.FunctionDef) and n.name == name)


def calls(fn: ast.AST) -> list[str]:
  return [ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)]


class TestCardSource(OpenpilotTestCase):
  def test_the_guard_runs_after_sm_update_in_state_update(self):
    src = ast.unparse(method("state_update"))
    guard = src.index("self.diag_guard.update(can_list, CS, self.sm['carControl'], self.sm.all_alive(['carControl']), self.initialized_prev)")
    assert src.index("self.sm.update(0)") < guard
    assert src.index("CS, CS_SP = self.CI.update(can_list)") < guard
    assert "cloudlog.event(diag[0], error=True, **diag[1])" in src

  def test_the_hold_line_is_exactly_this(self):
    src = ast.unparse(method("step"))
    line = "initialized = initialized and (not (self.diag_guard.stock and (not self.initialized_prev)))"
    assert src.count(line) == 1, src
    # after `initialized` is computed, before controls_update and before initialized_prev is updated
    assert src.index("e.name == EventName.selfdriveInitializing") < src.index(line) < src.index("self.controls_update(")
    assert src.index(line) < src.index("self.initialized_prev = initialized")

  def test_no_params_io_on_the_100hz_path(self):
    for name in ("step", "state_update", "state_publish", "controls_update"):
      bad = [c for c in calls(method(name)) if c.startswith("self.params.") and name != "controls_update"]
      assert bad == [], (name, bad)
    # controls_update has upstream's one put, ControlsReady, and nothing of ours
    assert [c for c in calls(method("controls_update")) if c.startswith("self.params.")] == ["self.params.put_bool"]

  def test_params_thread_writes_it_on_change(self):
    assert "self.write_diag_stock" in calls(method("params_thread"))
    src = ast.unparse(method("write_diag_stock"))
    assert "if stock != self._diag_written:" in src and "self.params.put_bool('DiagToolStock', stock)" in src

  def test_init_inherits_the_param(self):
    src = ast.unparse(method("__init__"))
    assert "self._diag_written = self.params.get_bool('DiagToolStock')" in src
    assert "self.diag_guard = DiagToolGuard(self.CP, self._diag_written)" in src
    # after CP.passive is decided (the guard never arms in dashcam mode)
    assert src.index("self.CP.passive = ") < src.index("self.diag_guard = DiagToolGuard(")

  def test_controls_update_is_nsw_live(self):
    digest = hashlib.sha256(ast.dump(method("controls_update")).encode()).hexdigest()
    assert digest == CONTROLS_UPDATE_SHA256, f"controls_update changed (sha256 {digest}): re-review the stand-down path"


class FakeSM(dict):
  def __init__(self):
    super().__init__(onroadEvents=[], carControl=SimpleNamespace(), carControlSP=SimpleNamespace())
    self.seen = {'onroadEvents': True}


class TestStep(OpenpilotTestCase):
  def make(self, stock: bool):
    from openpilot.selfdrive.car.card import Car
    c: Any = Car.__new__(Car)   # a bare instance, its collaborators stubbed
    c.CP = SimpleNamespace(passive=False)
    c.sm = FakeSM()
    c.initialized_prev = False
    c.diag_guard = SimpleNamespace(stock=stock)
    c.state_update = lambda: (SimpleNamespace(), SimpleNamespace(), None)
    c.state_publish = lambda *a: None
    c.calls = []
    c.controls_update = lambda CS, CC, CC_SP: c.calls.append(c.initialized_prev)
    return c, Car

  def test_held_while_stock_then_released_as_a_normal_start(self):
    c, Car = self.make(stock=True)
    for _ in range(500):
      Car.step(c)
    assert c.calls == [] and c.initialized_prev is False
    c.diag_guard.stock = False
    for _ in range(5):
      Car.step(c)
    assert c.calls == [False, True, True, True, True], "the first call is the one that runs CI.init + ControlsReady"

  def test_never_held_once_controls_started(self):
    c, Car = self.make(stock=False)
    for _ in range(3):
      Car.step(c)
    c.diag_guard.stock = True
    for _ in range(300):
      Car.step(c)
    assert len(c.calls) == 303 and all(c.calls[1:]), "card never stops sendcan mid-drive"

  def test_no_guard_no_change(self):
    c, Car = self.make(stock=False)
    for _ in range(10):
      Car.step(c)
    assert c.calls == [False] + [True] * 9

  def test_write_on_change_only(self):
    from openpilot.selfdrive.car.card import Car
    c: Any = Car.__new__(Car)   # a bare instance, its collaborators stubbed
    writes = []
    c.params = SimpleNamespace(put_bool=lambda k, v: writes.append((k, v)))
    c._diag_written = False
    c.diag_guard = SimpleNamespace(stock=False)
    for stock in (False, False, True, True, True, False, False, True):
      c.diag_guard.stock = stock
      Car.write_diag_stock(c)
    assert writes == [("DiagToolStock", True), ("DiagToolStock", False), ("DiagToolStock", True)]
