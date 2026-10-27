"""FORK(CPU) / FORK(COMM): which process runs on which core, held on the source (docs/fork/COMM-ISSUE.md section 9).

push16 moves card from core 4 to core 6 and keeps hardwared off cores 4 and 5. Route 157: card 48.4 %, selfdrived
27.6 % and controlsd 23.4 % of core 4 (99.7 % busy), its per-CPU kernel workers starved for minutes. An upstream merge
that puts card back, or anything real-time beside it on core 6, fails here.
"""
import ast
import threading
from pathlib import Path
from unittest import mock

from openpilot.common.basedir import BASEDIR
from openpilot.common.realtime import Priority
from openpilot.common.test import OpenpilotTestCase

OP = Path(BASEDIR) / "openpilot"


def _const(node: ast.AST):
  if isinstance(node, ast.Constant):
    return node.value
  if isinstance(node, ast.List):
    return [_const(e) for e in node.elts]
  if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "Priority":
    return getattr(Priority, node.attr)
  return None


def realtime_layout() -> dict[str, tuple[list[int], int]]:
  """Every config_realtime_process(cores, priority) outside the tests and tools: file -> (cores, priority)."""
  out = {}
  for path in OP.rglob("*.py"):
    rel = path.relative_to(OP).as_posix()
    if "/tests/" in rel or rel.startswith("tools/") or "/test/" in rel or rel.endswith("/debug.py"):
      continue
    try:
      tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
      continue
    for call in ast.walk(tree):
      if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "config_realtime_process":
        cores, prio = _const(call.args[0]), _const(call.args[1])
        out[rel] = (cores if isinstance(cores, list) else [cores], prio)
  return out


class TestCpuLayout(OpenpilotTestCase):
  def test_card_runs_on_core_6(self):
    layout = realtime_layout()
    assert layout["selfdrive/car/card.py"] == ([6], Priority.CTRL_HIGH), layout["selfdrive/car/card.py"]

  def test_core_4_keeps_controlsd_and_selfdrived_only(self):
    on4 = sorted(f for f, (cores, _) in realtime_layout().items() if 4 in cores)
    assert on4 == ["selfdrive/controls/controlsd.py", "selfdrive/selfdrived/selfdrived.py"], on4

  def test_nothing_else_real_time_on_core_6(self):
    """card shares core 6 only with camerad, which is not real-time (isolcpus keeps the rest away)."""
    on6 = sorted(f for f, (cores, _) in realtime_layout().items() if 6 in cores)
    assert on6 == ["selfdrive/car/card.py"], on6
    camerad = (OP / "system/camerad/main.cc").read_text()
    assert "set_core_affinity({6})" in camerad
    assert "setRealtimePriority" not in camerad and "set_realtime_priority" not in camerad

  def test_hardwared_cores_are_never_offlined_and_avoid_the_real_time_cores(self):
    from openpilot.system.hardware import hardwared
    assert hardwared.HARDWARED_CORES == [0, 1, 2, 3]
    rt = {c for f, (cores, prio) in realtime_layout().items() if prio >= Priority.CTRL_LOW for c in cores}
    assert {4, 5} <= rt, rt  # controlsd and selfdrived; plannerd and radard (and the UI, which moves itself to 5)
    assert not {4, 5} & set(hardwared.HARDWARED_CORES)
    # set_power_save takes the big cluster offline while parked; hardwared's cores must never all go
    tree = ast.parse((OP / "common/hardware/comma/hardware.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "set_power_save")
    offlined = set()
    for loop in ast.walk(fn):
      if isinstance(loop, ast.For) and "online" in ast.unparse(loop) and isinstance(loop.iter, ast.Call):
        offlined |= set(range(*[_const(a) for a in loop.iter.args]))
    assert offlined == {4, 5, 6, 7} and not offlined & set(hardwared.HARDWARED_CORES), offlined

  def test_hardwared_sets_its_affinity_before_any_thread_starts(self):
    from openpilot.system.hardware import hardwared
    calls: list = []
    orig_start = threading.Thread.start

    def start(t):
      calls.append(("start", t.name))
      return orig_start(t)

    def quick(*a, **kw):
      return None

    with mock.patch.object(hardwared, "set_core_affinity", side_effect=lambda c: calls.append(("affinity", list(c)))), \
         mock.patch.object(threading.Thread, "start", start), \
         mock.patch.object(hardwared, "hw_state_thread", quick), mock.patch.object(hardwared, "hardware_thread", quick), \
         mock.patch.object(hardwared, "touch_thread", quick):
      hardwared.main()
    assert calls and calls[0] == ("affinity", [0, 1, 2, 3]), calls
    assert sum(c[0] == "start" for c in calls) >= 2, calls

  def test_a_failed_affinity_call_does_not_stop_hardwared(self):
    from openpilot.system.hardware import hardwared
    started = []
    with mock.patch.object(hardwared, "set_core_affinity", side_effect=OSError(22, "Invalid argument")), \
         mock.patch.object(hardwared, "hw_state_thread", lambda *a: started.append("hw")), \
         mock.patch.object(hardwared, "hardware_thread", lambda *a: started.append("hardware")), \
         mock.patch.object(hardwared, "touch_thread", lambda *a: None):
      hardwared.main()
    assert sorted(started) == ["hardware", "hw"], started
