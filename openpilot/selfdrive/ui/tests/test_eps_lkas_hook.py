"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The pandad hook is three lines of glue around a large consequence: it stops the
process that owns the car's CAN bus. The things that must hold are ordering and
refusal, not protocol -- the protocol is proven against real hardware in
test_eps_lkas_flasher.py and on the bench.

openpilot.common.params is a compiled extension and is not importable
everywhere, so it is stubbed. That is not a shortcut: the stub is what lets the
ordering be asserted at all, because a real Params would need a real device.
The stubs live in sys.modules for the length of one test and are removed again
afterwards, so they never leak into another test module run in the same process.

The hook's onroad test is `not params.get_bool("IsOffroad")`: upstream deleted
IsOnroad (ad5151b38). FakeParams returns False for a key it was not given, which
reads as ONROAD - so every FakeParams meant to be offroad says IsOffroad=True.
"""
import ast
import dataclasses
import re
import struct
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[3]      # openpilot/
HOOK = ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_hook.py"
PANDAD = ROOT / "selfdrive/pandad/pandad.py"
BOARD_PANEL = ROOT / "selfdrive/ui/sunnypilot/mici/layouts/board.py"
PARAMS_KEYS = ROOT / "common/params_keys.h"

# the Params methods whose first argument is a key
PARAM_METHODS = {"get", "get_bool", "put", "put_bool", "put_nonblocking", "put_bool_nonblocking", "remove"}


class FakeParams:
  def __init__(self, **initial):
    self.d = dict(initial)
    self.writes: list[tuple[str, object]] = []

  def get_bool(self, k):
    return bool(self.d.get(k, False))

  def put_bool(self, k, v, block=False):
    self.d[k] = bool(v)
    self.writes.append((k, bool(v)))

  def get(self, k, block=False):
    return self.d.get(k)

  def put(self, k, v, block=False):
    self.d[k] = v
    self.writes.append((k, v))


class FakeProcess:
  """Looks enough like Popen. Stays alive until it is signaled."""

  def __init__(self):
    self.signals: list[int] = []
    self._alive = True

  def poll(self):
    return None if self._alive else 0

  def send_signal(self, sig):
    self.signals.append(sig)
    self._alive = False


def _is_openpilot(name: str) -> bool:
  return name == "openpilot" or name.startswith("openpilot.")


def _restore_modules(saved: dict) -> None:
  """Put every openpilot.* entry of sys.modules back the way it was."""
  for name in [n for n in list(sys.modules) if _is_openpilot(n)]:
    if name not in saved:
      del sys.modules[name]
  sys.modules.update(saved)


def _stop_watcher(proc: FakeProcess, thread) -> None:
  """A watcher whose process never exits polls forever; end it with the test."""
  proc._alive = False
  thread.join(timeout=1.0)


def _hook(test: unittest.TestCase, params: FakeParams):
  """Import the hook with openpilot's heavy bits stubbed out, for one test."""
  saved = {k: m for k, m in list(sys.modules.items()) if _is_openpilot(k)}
  test.addCleanup(_restore_modules, saved)

  def pkg(name, path=None):
    m = types.ModuleType(name)
    if path:
      m.__path__ = [str(path)]
    sys.modules[name] = m
    return m

  pkg("openpilot", ROOT)
  pkg("openpilot.common", ROOT / "common")
  p = pkg("openpilot.common.params")
  p.Params = lambda *a, **k: params
  log = pkg("openpilot.common.swaglog")
  log.cloudlog = types.SimpleNamespace(
    info=lambda *a, **k: None, warning=lambda *a, **k: None,
    error=lambda *a, **k: None, exception=lambda *a, **k: None,
    event=lambda *a, **k: None)
  pkg("openpilot.sunnypilot", ROOT / "sunnypilot")
  pkg("openpilot.sunnypilot.selfdrive", ROOT / "sunnypilot/selfdrive")
  pkg("openpilot.sunnypilot.selfdrive.pandad", ROOT / "sunnypilot/selfdrive/pandad")

  import importlib.util
  spec = importlib.util.spec_from_file_location(
    "openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher",
    ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_flasher.py")
  assert spec is not None and spec.loader is not None
  fl = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = fl
  spec.loader.exec_module(fl)

  spec2 = importlib.util.spec_from_file_location("eps_lkas_hook", HOOK)
  assert spec2 is not None and spec2.loader is not None
  mod = importlib.util.module_from_spec(spec2)
  spec2.loader.exec_module(mod)
  mod._saved = saved
  return mod


def _module_consts(tree: ast.Module) -> dict[str, str]:
  """The module-level string constants: REQUEST_PARAM = "EpsLkasFlashRequested"."""
  consts = {}
  for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
      for target in node.targets:
        if isinstance(target, ast.Name):
          consts[target.id] = node.value.value
  return consts


def _key_tables(tree: ast.Module) -> dict[str, dict[str, str]]:
  """The module-level key tables - EPS_KEYS = FlashKeys(request=REQUEST_PARAM, ...) - as {table: {field: key}}, each
  value a string literal or a module-level string constant."""
  consts = _module_consts(tree)
  tables: dict[str, dict[str, str]] = {}
  for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and node.value.keywords:
      fields = {}
      for kw in node.value.keywords:
        v = kw.value
        if kw.arg is None:
          continue
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
          fields[kw.arg] = v.value
        elif isinstance(v, ast.Name) and v.id in consts:
          fields[kw.arg] = consts[v.id]
      for target in node.targets:
        if isinstance(target, ast.Name) and fields:
          tables[target.id] = fields
  return tables


def _param_keys_used(path: Path) -> set[str]:
  """Every params key a module reads or writes.

  A key is the first argument of a Params method called on `params` or on
  `<anything>.params` (ui_state.params), either as a string literal or as a
  module-level string constant such as REQUEST_PARAM. A key this cannot resolve
  fails the test rather than being skipped, so nothing escapes the check.

  Since the second board (BLR-v1) a key can also be a field of a module-level
  key table (_key_tables): `k.request`, `self._keys.request` and
  `EPS_KEYS.version` resolve to EVERY key any table gives that field - all of
  which must then be registered.
  """
  tree = ast.parse(path.read_text())
  consts = _module_consts(tree)
  fields: dict[str, set[str]] = {}
  for table in _key_tables(tree).values():
    for field, key in table.items():
      fields.setdefault(field, set()).add(key)

  keys, unresolved = set(), []
  for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in PARAM_METHODS and node.args):
      continue
    recv = node.func.value
    if not ((isinstance(recv, ast.Name) and recv.id == "params")
            or (isinstance(recv, ast.Attribute) and recv.attr == "params")):
      continue
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
      keys.add(arg.value)
    elif isinstance(arg, ast.Name) and arg.id in consts:
      keys.add(consts[arg.id])
    elif isinstance(arg, ast.Attribute) and arg.attr in fields:
      keys |= fields[arg.attr]
    else:
      unresolved.append(f"{path.name}:{node.lineno}")
  assert not unresolved, f"params keys this test cannot resolve: {unresolved}"
  return keys


class TestEpsLkasHook(unittest.TestCase):
  def test_watcher_ignores_a_request_while_onroad(self):
    """Taking CAN away from a moving car must not be something a param write can
    do, whatever the UI believes it is gating on."""
    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=False)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01

    proc = FakeProcess()
    skipped = []
    t = mod.watch_for_request(proc, lambda: skipped.append(True), params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=0.5)

    assert proc.signals == [], "pandad was stopped while onroad"
    assert skipped == []

  def test_watcher_stops_pandad_offroad(self):
    import signal as sig
    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01

    proc = FakeProcess()
    skipped = []
    t = mod.watch_for_request(proc, lambda: skipped.append(True), params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=2.0)

    assert proc.signals == [sig.SIGINT], f"expected one SIGINT, got {proc.signals}"
    # the skip must be armed BEFORE the signal, or the loop can re-enter and
    # DFU-recover the panda before the flag is set
    assert skipped == [True]

  def test_watcher_does_nothing_without_a_request(self):
    params = FakeParams(EpsLkasFlashRequested=False, IsOffroad=True)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01
    proc = FakeProcess()
    t = mod.watch_for_request(proc, lambda: None, params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=0.2)
    assert proc.signals == []

  def test_request_is_cleared_before_anything_is_attempted(self):
    """If it survives the attempt, the loop re-enters, the watcher fires again,
    and the gateway is reflashed forever with no backoff."""
    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)

    # no panda here, so PandaTransport will raise - which is the point: the
    # request must already be cleared by then
    mod.flash_if_requested("deadbeef")

    assert params.get_bool("EpsLkasFlashRequested") is False
    order = [k for k, _ in params.writes]
    assert order[0] == "EpsLkasFlashRequested", f"cleared too late: {order}"
    assert params.get("EpsLkasFlashState", "").startswith("failed"), params.get("EpsLkasFlashState")
    # The onroad drop ALSO clears the request first and ALSO ends in "failed",
    # so without these two the test would pass without ever reaching the flash.
    assert params.get("EpsLkasFlashState") != "failed: not while driving", \
      "the offroad request took the onroad drop path; the flash path is not being tested"
    assert ("EpsLkasFlashState", "running") in params.writes, f"the flash was never attempted: {params.writes}"

  def test_no_request_means_no_writes_at_all(self):
    params = FakeParams(EpsLkasFlashRequested=False)
    mod = _hook(self, params)
    mod.flash_if_requested("deadbeef")
    assert params.writes == []

  def test_hook_never_raises(self):
    class Exploding(FakeParams):
      def get_bool(self, k):
        raise RuntimeError("params exploded")

    params = Exploding()
    mod = _hook(self, params)
    mod.flash_if_requested("deadbeef")      # must simply return

  def test_pandad_skips_the_panda_reset_on_that_re_entry(self):
    """recover_internal_panda() drives BOOT0 high and reflashes the panda.
    Pressing "update the gateway board" must not do that as a side effect."""
    src = PANDAD.read_text()
    assert "skip_panda_reset" in src
    assert "request_skip_panda_reset" in src
    # the reset block must be inside the else of the skip check
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
      if isinstance(node, ast.If) and isinstance(node.test, ast.Subscript):
        if "recover_internal_panda" in ast.dump(node) or "reset_internal_panda" in ast.dump(node):
          # the resets must be in orelse, not in the skip branch
          assert "reset_internal_panda" not in ast.dump(ast.Module(body=node.body, type_ignores=[]))
          assert "reset_internal_panda" in ast.dump(ast.Module(body=node.orelse, type_ignores=[]))
          found = True
    assert found, "could not find the skip guard around the panda reset"

  def test_pandad_calls_the_hook_and_the_watcher_in_the_right_places(self):
    src = PANDAD.read_text()
    i_flash_panda = src.index("flash_panda(panda_serials[0])")
    i_hook = src.index("flash_if_requested(panda_serials[0])")
    i_popen = src.index('subprocess.Popen(["./pandad"]')
    i_watch = src.index("watch_for_request(process")
    i_wait = src.index("process.wait()")

    # the hook needs a panda that is out of bootstub, which flash_panda ensures,
    # and it needs ./pandad not to be running yet
    assert i_flash_panda < i_hook < i_popen, "the hook is in the wrong place"
    # the watcher needs the process object, and must be armed before we block
    assert i_popen < i_watch < i_wait, "the watcher is not armed before the wait"

  def test_the_flash_trace_survives_to_the_next_drive(self):
    """The flash runs offroad with loggerd stopped, so nothing it records is
    kept. The trace reaches a human through a param that card emits into the
    next route - which means the key must be PERSISTENT, not cleared when
    manager restarts, and the value must be a dict.
    """
    root = Path(__file__).parents[3]
    keys = (root / "common/params_keys.h").read_text()
    line = next(l for l in keys.splitlines() if "EpsLkasFlashTrace" in l and "{" in l)
    assert "PERSISTENT" in line, \
      "CLEAR_ON_MANAGER_START would throw the trace away at exactly the wrong moment"
    assert "JSON" in line

    hook = (root / "sunnypilot/selfdrive/pandad/eps_lkas_hook.py").read_text()
    assert "trace_out=" in hook, "the hook never asks for the trace"
    # THE DICT, NOT json.dumps(d). Params.put looks up PYTHON_2_CPP[(type,
    # keytype)], which has (dict, JSON) and (list, JSON) and no (str, JSON) -
    # a pre-serialised string raises TypeError. This already killed card once.
    assert "json.dumps" not in hook.split("trace_out=")[1].split(chr(10))[0]

    card = (root / "selfdrive/car/card.py").read_text()
    assert "EpsLkasFlashTrace" in card, "nothing emits it into a route"
    tree = ast.parse(card)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "log_flash_trace")
    dumped = ast.dump(fn)
    assert "_flash_trace_done" in dumped, "it would re-log every 100 ms"
    assert "remove" in dumped, "the trace must appear in exactly one route"
    assert any(isinstance(h.type, ast.Name) and h.type.id == "Exception"
               for n in ast.walk(fn) if isinstance(n, ast.Try) for h in n.handlers), \
      "diagnostics must never take card down"

  def test_every_param_the_hook_and_the_page_use_is_registered(self):
    """FakeParams accepts any key, so nothing above can see a key that upstream
    deleted. Upstream's Params raises UnknownKeyName on one, and the hook catches
    it: the update feature then dies quietly, at `requested`, forever. That is
    exactly what IsOnroad did when upstream removed it (ad5151b38)."""
    registered = set(re.findall(r'\{"(\w+)",\s*\{', PARAMS_KEYS.read_text()))
    hook_keys = _param_keys_used(HOOK)
    board_keys = _param_keys_used(BOARD_PANEL)
    # the extraction must have found the keys we know are there, or this proves nothing
    assert {"EpsLkasFlashRequested", "EpsLkasFlashState", "EpsLkasFlashProgress", "EpsLkasFlashTrace",
            "EpsLkasFlashPhase", "EpsLkasFlashCancel"} <= hook_keys, hook_keys
    assert {"EpsLkasBoardVersion", "EpsLkasFlashRequested", "EpsLkasFlashState", "EpsLkasFlashPhase",
            "EpsLkasFlashCancel"} <= board_keys, board_keys
    # and the lamp board's (BLR-v1), through the key tables
    assert {"BlrFlashRequested", "BlrFlashState", "BlrFlashProgress", "BlrFlashTrace", "BlrFlashPhase",
            "BlrFlashCancel", "BlrBoardVersion", "BlrBoardBuild", "BlrBoardSeenAt"} <= hook_keys, hook_keys
    assert {"BlrBoardVersion", "BlrBoardBuild", "BlrBoardSeenAt", "BlrLastDrive", "BlrFlashRequested",
            "BlrFlashState", "BlrFlashPhase", "BlrFlashCancel", "BlrFlashProgress"} <= board_keys, board_keys
    missing = sorted((hook_keys | board_keys) - registered)
    assert not missing, f"{missing} are not in params_keys.h; Params would raise UnknownKeyName"


class TestFlashScreenHook(unittest.TestCase):
  """push9: the update screen's phase and its Cancel, through the hook."""

  def test_a_cancel_pressed_while_pandad_was_stopping_is_read_before_it_is_cleared(self):
    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=True, EpsLkasFlashCancel=True)
    mod = _hook(self, params)
    made = []
    mod.flash_if_requested("deadbeef", transport_factory=lambda: made.append(1))
    assert params.get("EpsLkasFlashState") == "cancelled", params.get("EpsLkasFlashState")
    assert params.get_bool("EpsLkasFlashCancel") is False, "the Cancel must be spent"
    assert params.get("EpsLkasFlashPhase") == {"p": "cancelled", "cancel": False}
    assert ("EpsLkasFlashState", "running") not in params.writes and made == [], "the flash was started anyway"
    assert params.writes[0] == ("EpsLkasFlashRequested", False), "the request is still cleared first"

  def test_the_watcher_drops_a_cancelled_request_without_stopping_pandad(self):
    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=True, EpsLkasFlashCancel=True)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01
    proc = FakeProcess()
    t = mod.watch_for_request(proc, lambda: None, params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=0.3)
    assert proc.signals == [], "the car link was taken down for a cancelled request"
    assert params.get_bool("EpsLkasFlashRequested") is False
    assert params.get("EpsLkasFlashState") == "cancelled"

  def test_the_phase_and_the_cancel_are_wired_to_the_flasher(self):
    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    seen = {}

    def fake_run(transport, image, **kw):
      seen.update(kw)
      kw["phase"]({"p": "stock", "cancel": True, "left": 3})
      assert params.get("EpsLkasFlashPhase") == {"p": "stock", "cancel": True, "left": 3}
      params.put_bool("EpsLkasFlashCancel", True)
      assert kw["should_cancel"]() is True
      return False, mod.CANCELLED

    mod.run_flash = fake_run
    mod.flash_if_requested("deadbeef", transport_factory=lambda: types.SimpleNamespace(close=lambda: None))
    assert "phase" in seen and "should_cancel" in seen
    assert params.get("EpsLkasFlashState") == "cancelled", "a cancel is not a failure"
    assert params.get_bool("EpsLkasFlashCancel") is False

    params = FakeParams(EpsLkasFlashRequested=True, IsOffroad=True, EpsLkasFlashCancel=False)
    mod = _hook(self, params)
    mod.run_flash = lambda transport, image, **kw: (True, "abcd1234")
    mod.flash_if_requested("deadbeef", transport_factory=lambda: types.SimpleNamespace(close=lambda: None))
    assert params.get("EpsLkasFlashState") == "ok abcd1234"
    assert params.get("EpsLkasFlashPhase") == {"p": "ok", "cancel": False}


# ---------------------------------------------------------------------------
# FORK(BRAKE-LAMP-RELAY): the second board. BLR-v1 DESIGN E.2 / E.6.

BLR1_MAGIC = 0x31524C42


def _blr_image(git=0x9F8E7D6C, variant=2, dirty=0) -> bytes:
  """Enough of a BLR-v1 app-slot image for the hook: the marker at 0x100 (image_identity reads nothing else)."""
  head = bytearray(0x110)
  struct.pack_into("<4I", head, 0x100, BLR1_MAGIC, 0x08004000, git, (dirty & 1) | (variant << 4))
  return bytes(head)


class _Transport:
  def __init__(self, made: list):
    made.append(self)
    self.closed = False

  def close(self):
    self.closed = True


def _pandad_window(mod) -> None:
  """ONE pass of pandad.py's window, run from pandad.py's own source: the statement that asks each board. Taken out
  of the file rather than typed in again here, so this tests what pandad actually does."""
  tree = ast.parse(PANDAD.read_text())
  found = [n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.UnaryOp)
           and isinstance(n.test.op, ast.Not) and isinstance(n.test.operand, ast.Call)
           and getattr(n.test.operand.func, "id", "") == "flash_if_requested"]
  assert len(found) == 1, "pandad.py no longer asks the boards in one `if not flash_if_requested(...)`"
  calls = [c for c in ast.walk(found[0]) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "flash_if_requested"]
  assert len(calls) == 2, "pandad.py's window statement should ask exactly two boards"
  module = ast.fix_missing_locations(ast.Module(body=[found[0]], type_ignores=[]))
  exec(compile(module, str(PANDAD), "exec"),
       {"flash_if_requested": mod.flash_if_requested, "panda_serials": ["deadbeef"], "BLR": mod.BLR})


def _fake_flash(mod, result=(False, "no HELLO")):
  """Stand in for the flasher: the bundled image per board, the transport, and run_flash, which records whose
  update it was asked to run."""
  runs, made = [], []
  mod.load_bundled_image = lambda target: (_blr_image() if target is mod.BLR else b"\x00" * 0x110, "")
  mod.PandaTransport = lambda bus, serial: _Transport(made)

  def fake_run(transport, image, **kw):
    runs.append(kw["target"])
    return result

  mod.run_flash = fake_run
  return runs, made


class TestTwoBoards(unittest.TestCase):
  """The lamp board through the same hook: its own params, one session per window, everything else per board."""

  def test_one_session_per_window_when_both_are_requested(self):
    """The page makes both pending at once unreachable (_busy() is both boards'); if it ever happens anyway the
    window still holds ONE update, the EPS's, and the lamp board's request waits for the next window - which the
    watcher opens once ./pandad is back."""
    params = FakeParams(EpsLkasFlashRequested=True, BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    runs, made = _fake_flash(mod)

    _pandad_window(mod)
    assert runs == [mod.EPS], f"one window ran {[t.name for t in runs]}"
    assert params.get_bool("EpsLkasFlashRequested") is False
    assert params.get_bool("BlrFlashRequested") is True, "the lamp board's request was spent without a session"
    assert not any(k.startswith("Blr") for k, _ in params.writes), "the EPS's session wrote the lamp board's params"
    assert len(made) == 1 and made[0].closed

    # the next window: the lamp board's turn, and nothing of the EPS's is touched
    params.writes.clear()
    _pandad_window(mod)
    assert runs == [mod.EPS, mod.BLR], [t.name for t in runs]
    assert params.get_bool("BlrFlashRequested") is False
    assert params.get("BlrFlashState") == "failed: no HELLO"
    assert not any(k.startswith("EpsLkas") for k, _ in params.writes), "the lamp board's session wrote the EPS's"

    # and a window with nothing pending runs nothing
    _pandad_window(mod)
    assert runs == [mod.EPS, mod.BLR]

  def test_the_lamp_board_alone_runs_in_the_first_window(self):
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    runs, _ = _fake_flash(mod)
    _pandad_window(mod)
    assert runs == [mod.BLR]
    assert params.writes[0] == ("BlrFlashRequested", False), "the lamp board's request is not cleared first"

  def test_a_cancelled_eps_request_does_not_cost_the_lamp_board_its_window(self):
    """A Cancel read before anything ran is no session: the window is still free."""
    params = FakeParams(EpsLkasFlashRequested=True, EpsLkasFlashCancel=True, BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    runs, _ = _fake_flash(mod)
    _pandad_window(mod)
    assert params.get("EpsLkasFlashState") == "cancelled"
    assert runs == [mod.BLR]

  def test_each_board_clears_only_its_own_request(self):
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    runs, _ = _fake_flash(mod)
    assert mod.flash_if_requested("deadbeef") is False, "the EPS ran on the lamp board's request"
    assert params.writes == [], "asking the EPS wrote something"
    assert mod.flash_if_requested("deadbeef", target=mod.BLR) is True
    assert runs == [mod.BLR]
    assert {k for k, _ in params.writes} <= {"BlrFlashRequested", "BlrFlashState", "BlrFlashProgress",
                                             "BlrFlashPhase", "BlrFlashCancel"}, params.writes

    # onroad: dropped, by its own params
    params = FakeParams(BlrFlashRequested=True, EpsLkasFlashRequested=False, IsOffroad=False)
    mod = _hook(self, params)
    runs, _ = _fake_flash(mod)
    assert mod.flash_if_requested("deadbeef", target=mod.BLR) is False
    assert params.get("BlrFlashState") == "failed: not while driving" and params.get_bool("BlrFlashRequested") is False
    assert runs == [] and not any(k.startswith("EpsLkas") for k, _ in params.writes)

  def test_a_lamp_board_cancel_is_read_before_it_is_cleared(self):
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True, BlrFlashCancel=True)
    mod = _hook(self, params)
    runs, made = _fake_flash(mod)
    assert mod.flash_if_requested("deadbeef", target=mod.BLR) is False
    assert params.get("BlrFlashState") == "cancelled"
    assert params.get_bool("BlrFlashCancel") is False, "the Cancel must be spent"
    assert params.get("BlrFlashPhase") == {"p": "cancelled", "cancel": False}
    assert runs == [] and made == [], "the update was started anyway"
    assert params.writes[0] == ("BlrFlashRequested", False)

  def test_the_watcher_watches_the_lamp_board_too(self):
    import signal as sig
    # offroad: it stops ./pandad for the lamp board's request, skip armed first
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01
    proc, skipped = FakeProcess(), []
    t = mod.watch_for_request(proc, lambda: skipped.append(True), params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=2.0)
    assert proc.signals == [sig.SIGINT] and skipped == [True]
    assert params.get_bool("BlrFlashRequested") is True, "the watcher must leave the request for the hook"

    # onroad: refused, and only the lamp board's request is dropped
    params = FakeParams(BlrFlashRequested=True, EpsLkasFlashRequested=False, IsOffroad=False)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01
    proc = FakeProcess()
    t = mod.watch_for_request(proc, lambda: None, params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=0.3)
    assert proc.signals == []
    assert params.get_bool("BlrFlashRequested") is False and params.get("BlrFlashState") == "failed: not while driving"
    assert not any(k.startswith("EpsLkas") for k, _ in params.writes), params.writes

    # cancelled before pandad was asked: dropped, the car link untouched
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True, BlrFlashCancel=True)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01
    proc = FakeProcess()
    t = mod.watch_for_request(proc, lambda: None, params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=0.3)
    assert proc.signals == [] and params.get("BlrFlashState") == "cancelled"

  def test_with_both_requested_the_watcher_stops_pandad_once(self):
    import signal as sig
    params = FakeParams(EpsLkasFlashRequested=True, BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    mod.WATCH_PERIOD_S = 0.01
    proc = FakeProcess()
    t = mod.watch_for_request(proc, lambda: None, params=params)
    self.addCleanup(_stop_watcher, proc, t)
    t.join(timeout=2.0)
    assert proc.signals == [sig.SIGINT]

  def test_a_lamp_board_update_writes_its_own_identity(self):
    """The flags move with the hash, as for the EPS - in the lamp board's terms: VARIANT as card writes it (the
    number), a bootloader, and dual-bank flash (its bootloader NAKs BEGIN on single-bank). What card read off the
    board - uid, bootGit, borEn - stays."""
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True,
                        BlrBoardVersion="1a2b3c4d",
                        BlrBoardBuild={"dirty": False, "variant": 2, "bootloader": True, "bootGit": "b28113",
                                       "uid": "a5c3e1", "borEn": True, "dualBank": True},
                        BlrLastDrive={"faults": 0, "latch": 0})
    mod = _hook(self, params)
    image = _blr_image(git=0x9F8E7D6C, variant=3)
    mod.load_bundled_image = lambda target: (image, "") if target is mod.BLR else (None, "not this one")
    seen = {}

    def fake_run(transport, img, **kw):
      seen.update(kw, image=img)
      kw["trace_out"]({"n": 1})
      kw["phase"]({"p": "write", "cancel": False, "pct": 50})
      kw["progress"](50)
      return True, "9f8e7d6c"

    mod.run_flash = fake_run
    assert mod.flash_if_requested("deadbeef", target=mod.BLR,
                                  transport_factory=lambda: types.SimpleNamespace(close=lambda: None)) is True
    assert seen["target"] is mod.BLR and seen["image"] == image
    assert params.get("BlrFlashState") == "ok 9f8e7d6c"
    assert params.get("BlrFlashPhase") == {"p": "ok", "cancel": False}
    assert params.get("BlrBoardVersion") == "9f8e7d6c"
    assert params.get("BlrBoardBuild") == {"dirty": False, "variant": 3, "bootloader": True, "bootGit": "b28113",
                                           "uid": "a5c3e1", "borEn": True, "dualBank": True}
    assert int(params.get("BlrBoardSeenAt")) > 0
    assert params.get("BlrFlashTrace") == {"n": 1} and params.get("BlrFlashProgress") == "50"
    assert params.get("BlrLastDrive") == {"faults": 0, "latch": 0}, "the last drive's record is the drive's, not ours"
    assert not any(k.startswith("EpsLkas") for k, _ in params.writes), params.writes

    # a board whose 0x62F card never heard: the update itself proved a bootloader and dual-bank flash, and without
    # them the page would refuse the next update ("drive once", or "not dual-bank" beside a bootloader)
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True, BlrBoardBuild={"dirty": False, "variant": 2})
    mod = _hook(self, params)
    mod.load_bundled_image = lambda target: (image, "")
    mod.run_flash = lambda transport, img, **kw: (True, "9f8e7d6c")
    mod.flash_if_requested("deadbeef", target=mod.BLR, transport_factory=lambda: types.SimpleNamespace(close=lambda: None))
    assert params.get("BlrBoardBuild") == {"dirty": False, "variant": 3, "bootloader": True, "dualBank": True}

  def test_no_lamp_board_image_is_a_clean_failure(self):
    """Without blr_appslot.bin the page offers nothing. A request made anyway - by hand - ends as a failure that names
    the file, before any transport is opened. Since push11 bundles an image, the flasher's own load_bundled_image()
    is pointed at a path that does not exist, so this runs either way."""
    params = FakeParams(BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    real = mod.load_bundled_image
    missing = str(Path(mod.BLR.image_path).parent / "nonexistent" / "blr_appslot.bin")
    mod.load_bundled_image = lambda target: real(dataclasses.replace(target, image_path=missing))
    made = []
    assert mod.flash_if_requested("deadbeef", target=mod.BLR, transport_factory=lambda: made.append(1)) is True
    assert made == []
    state = params.get("BlrFlashState")
    assert state.startswith("failed: no bundled image at ") and state.endswith("blr_appslot.bin"), state

  def test_the_hook_never_raises_for_the_lamp_board(self):
    class Exploding(FakeParams):
      def get_bool(self, k):
        raise RuntimeError("params exploded")

    mod = _hook(self, Exploding())
    assert mod.flash_if_requested("deadbeef", target=mod.BLR) is False

    class ExplodingPut(FakeParams):
      def put_bool(self, k, v, block=False):
        raise RuntimeError("params exploded")

    params = ExplodingPut(BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    assert mod.flash_if_requested("deadbeef", target=mod.BLR) is False   # could not even clear it: nothing ran

    params = FakeParams(BlrFlashRequested=True, IsOffroad=True)
    mod = _hook(self, params)
    _fake_flash(mod)

    def bad_close():
      raise OSError("usb gone")

    assert mod.flash_if_requested("deadbeef", target=mod.BLR,
                                  transport_factory=lambda: types.SimpleNamespace(close=bad_close)) is True
    assert params.get("BlrFlashState") == "failed: no HELLO"
    # and a board the hook has no params for is nobody's request
    assert mod.flash_if_requested("deadbeef", target=object()) is False
    assert mod.flash_if_requested("deadbeef", target=[]) is False

  def test_the_page_and_the_hook_name_the_same_params(self):
    """The page writes the request the hook watches, and reads the state the hook writes. Two tables, one per
    module (board.py BOARD_KEYS, the hook's KEYS), held to the same names here."""
    hook = _key_tables(ast.parse(HOOK.read_text()))
    page = _key_tables(ast.parse(BOARD_PANEL.read_text()))
    for table in ("EPS_KEYS", "BLR_KEYS"):
      assert table in hook and table in page, (table, sorted(hook), sorted(page))
      common = set(hook[table]) & set(page[table])
      assert common >= {"request", "progress", "state", "phase", "cancel", "version", "build", "seen"}, common
      for field in common:
        assert hook[table][field] == page[table][field], f"{table}.{field}: hook {hook[table][field]} != page {page[table][field]}"
    assert hook["EPS_KEYS"]["request"] == "EpsLkasFlashRequested" and hook["BLR_KEYS"]["request"] == "BlrFlashRequested"
    # and each board's names are its own: no key is in both tables
    eps, blr = set(hook["EPS_KEYS"].values()), set(hook["BLR_KEYS"].values())
    assert not (eps & blr), eps & blr

  def test_the_lamp_board_params_have_the_eps_twins_lifetimes(self):
    """A request or a Cancel that survived a restart would be one nobody made; a trace that did not would be lost."""
    keys = PARAMS_KEYS.read_text()

    def entry(key):
      m = re.search(r'\{"' + key + r'",\s*\{([^}]*)\}', keys)
      assert m, f"{key} is not in params_keys.h"
      return m.group(1).replace(" ", "")

    for eps, blr in (("EpsLkasFlashRequested", "BlrFlashRequested"), ("EpsLkasFlashProgress", "BlrFlashProgress"),
                     ("EpsLkasFlashState", "BlrFlashState"), ("EpsLkasFlashPhase", "BlrFlashPhase"),
                     ("EpsLkasFlashCancel", "BlrFlashCancel"), ("EpsLkasFlashTrace", "BlrFlashTrace"),
                     ("EpsLkasBoardVersion", "BlrBoardVersion"), ("EpsLkasBoardBuild", "BlrBoardBuild"),
                     ("EpsLkasBoardSeenAt", "BlrBoardSeenAt")):
      assert entry(eps) == entry(blr), f"{blr} is {entry(blr)}, its twin {eps} is {entry(eps)}"
