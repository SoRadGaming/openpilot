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
"""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[3]
HOOK = ROOT / "sunnypilot/selfdrive/pandad/eps_lkas_hook.py"
PANDAD = ROOT / "selfdrive/pandad/pandad.py"


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


def _hook(params: FakeParams):
  """Import the hook with openpilot's heavy bits stubbed out."""
  saved = {k: sys.modules.get(k) for k in
           ("openpilot", "openpilot.common", "openpilot.common.params",
            "openpilot.common.swaglog", "openpilot.sunnypilot",
            "openpilot.sunnypilot.selfdrive", "openpilot.sunnypilot.selfdrive.pandad",
            "openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher")}

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
  fl = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = fl
  spec.loader.exec_module(fl)

  spec2 = importlib.util.spec_from_file_location("eps_lkas_hook", HOOK)
  mod = importlib.util.module_from_spec(spec2)
  spec2.loader.exec_module(mod)
  mod._saved = saved
  return mod


def test_watcher_ignores_a_request_while_onroad():
  """Taking CAN away from a moving car must not be something a param write can
  do, whatever the UI believes it is gating on."""
  params = FakeParams(EpsLkasFlashRequested=True, IsOnroad=True)
  mod = _hook(params)
  mod.WATCH_PERIOD_S = 0.01

  proc = FakeProcess()
  skipped = []
  t = mod.watch_for_request(proc, lambda: skipped.append(True), params=params)
  t.join(timeout=0.5)

  assert proc.signals == [], "pandad was stopped while onroad"
  assert skipped == []


def test_watcher_stops_pandad_offroad():
  import signal as sig
  params = FakeParams(EpsLkasFlashRequested=True, IsOnroad=False)
  mod = _hook(params)
  mod.WATCH_PERIOD_S = 0.01

  proc = FakeProcess()
  skipped = []
  t = mod.watch_for_request(proc, lambda: skipped.append(True), params=params)
  t.join(timeout=2.0)

  assert proc.signals == [sig.SIGINT], f"expected one SIGINT, got {proc.signals}"
  # the skip must be armed BEFORE the signal, or the loop can re-enter and
  # DFU-recover the panda before the flag is set
  assert skipped == [True]


def test_watcher_does_nothing_without_a_request():
  params = FakeParams(EpsLkasFlashRequested=False, IsOnroad=False)
  mod = _hook(params)
  mod.WATCH_PERIOD_S = 0.01
  proc = FakeProcess()
  t = mod.watch_for_request(proc, lambda: None, params=params)
  t.join(timeout=0.2)
  assert proc.signals == []


def test_request_is_cleared_before_anything_is_attempted():
  """If it survives the attempt, the loop re-enters, the watcher fires again,
  and the gateway is reflashed forever with no backoff."""
  params = FakeParams(EpsLkasFlashRequested=True)
  mod = _hook(params)

  # no panda here, so PandaTransport will raise - which is the point: the
  # request must already be cleared by then
  mod.flash_if_requested("deadbeef")

  assert params.get_bool("EpsLkasFlashRequested") is False
  order = [k for k, _ in params.writes]
  assert order[0] == "EpsLkasFlashRequested", f"cleared too late: {order}"
  assert params.get("EpsLkasFlashState", "").startswith("failed"), params.get("EpsLkasFlashState")


def test_no_request_means_no_writes_at_all():
  params = FakeParams(EpsLkasFlashRequested=False)
  mod = _hook(params)
  mod.flash_if_requested("deadbeef")
  assert params.writes == []


def test_hook_never_raises():
  class Exploding(FakeParams):
    def get_bool(self, k):
      raise RuntimeError("params exploded")

  params = Exploding()
  mod = _hook(params)
  mod.flash_if_requested("deadbeef")      # must simply return


def test_pandad_skips_the_panda_reset_on_that_re_entry():
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
      body = ast.dump(node)
      if "recover_internal_panda" in ast.dump(node) or "reset_internal_panda" in ast.dump(node):
        # the resets must be in orelse, not in the skip branch
        assert "reset_internal_panda" not in ast.dump(ast.Module(body=node.body, type_ignores=[]))
        assert "reset_internal_panda" in ast.dump(ast.Module(body=node.orelse, type_ignores=[]))
        found = True
  assert found, "could not find the skip guard around the panda reset"


def test_pandad_calls_the_hook_and_the_watcher_in_the_right_places():
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
