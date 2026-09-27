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
import re
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
  """Looks enough like Popen. Stays alive until it is signalled."""

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


def _param_keys_used(path: Path) -> set[str]:
  """Every params key a module reads or writes.

  A key is the first argument of a Params method called on `params` or on
  `<anything>.params` (ui_state.params), either as a string literal or as a
  module-level string constant such as REQUEST_PARAM. A key this cannot resolve
  fails the test rather than being skipped, so nothing escapes the check.
  """
  tree = ast.parse(path.read_text())
  consts = {}
  for node in tree.body:
    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
      for target in node.targets:
        if isinstance(target, ast.Name):
          consts[target.id] = node.value.value

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
    assert {"EpsLkasFlashRequested", "EpsLkasFlashState", "EpsLkasFlashProgress", "EpsLkasFlashTrace"} <= hook_keys, hook_keys
    assert {"EpsLkasBoardVersion", "EpsLkasFlashRequested", "EpsLkasFlashState"} <= board_keys, board_keys
    missing = sorted((hook_keys | board_keys) - registered)
    assert not missing, f"{missing} are not in params_keys.h; Params would raise UnknownKeyName"
