"""FORK(COMM-ISSUE): the loops selfdrived checks for liveness make no Params write that waits on /data.

A guard on the source, for the next upstream merge: hardwared's hardware_thread (deviceState), selfdrived's loop
(selfdriveState) and the driver-monitoring policy (driverMonitoringState). A blocking write is set_offroad_alert(),
Params.remove(), put(..., block=True) / put_bool(..., block=True), or any put on a temporary Params() (its destructor
waits for the write). docs/fork/COMM-ISSUE.md lists every publisher and why the rest need nothing.
"""
import ast
from pathlib import Path

from openpilot.common.basedir import BASEDIR
from openpilot.common.test import OpenpilotTestCase

OP = Path(BASEDIR) / "openpilot"


def _function(tree: ast.AST, name: str, cls: str | None = None) -> ast.AST:
  for node in ast.walk(tree):
    if isinstance(node, ast.ClassDef) and cls is not None and node.name == cls:
      return node
    if cls is None and isinstance(node, ast.FunctionDef) and node.name == name:
      return node
  raise AssertionError(f"{cls or name} not found")


def blocking_writes(node: ast.AST, skip_methods: tuple[str, ...] = ()) -> list[str]:
  found = []
  skip = {id(n) for fn in ast.walk(node) if isinstance(fn, ast.FunctionDef) and fn.name in skip_methods
          for n in ast.walk(fn)}
  for call in ast.walk(node):
    if not isinstance(call, ast.Call) or id(call) in skip:
      continue
    f = call.func
    where = f"line {call.lineno}: {ast.unparse(call)[:100]}"
    if isinstance(f, ast.Name) and f.id == "set_offroad_alert":
      found.append(where)
    elif isinstance(f, ast.Attribute) and f.attr in ("put", "put_bool", "remove"):
      recv = ast.unparse(f.value)
      temporary = isinstance(f.value, ast.Call) and ast.unparse(f.value.func) == "Params"
      if "writer" in recv or not (temporary or "params" in recv.lower()):
        continue  # a ParamsWriter (never waits), or not a Params handle at all (events.remove, a queue's put)
      block = any(k.arg == "block" and isinstance(k.value, ast.Constant) and k.value.value is True for k in call.keywords)
      if temporary or block or f.attr == "remove":
        found.append(where)
  return found


class TestCommIssueGuards(OpenpilotTestCase):
  def test_hardware_thread(self):
    tree = ast.parse((OP / "system/hardware/hardwared.py").read_text())
    found = blocking_writes(_function(tree, "hardware_thread"))
    # DoShutdown: offroad only (power_monitor.should_shutdown), and manager must find it set before it shuts down
    found = [f for f in found if "DoShutdown" not in f]
    assert not found, f"hardware_thread (deviceState) waits on /data: {found}"

  def test_selfdrived_loop(self):
    tree = ast.parse((OP / "selfdrive/selfdrived/selfdrived.py").read_text())
    # __init__ runs before selfdriveState is first sent
    found = blocking_writes(_function(tree, "", cls="SelfdriveD"), skip_methods=("__init__",))
    assert not found, f"selfdrived's loop (selfdriveState) waits on /data: {found}"

  def test_driver_monitoring_policy(self):
    tree = ast.parse((OP / "selfdrive/monitoring/policy.py").read_text())
    found = blocking_writes(_function(tree, "", cls="DriverMonitoring"))
    assert not found, f"the DM policy (driverMonitoringState) waits on /data: {found}"

  def test_the_guard_sees_what_push11_had(self):
    """The guard itself: the calls push11 made in these loops are what it flags."""
    src = '''
def hardware_thread():
  set_offroad_alert("Offroad_TiciSupport", False)
  params.put_bool("IsEngaged", True, block=True)
  params.put("UptimeOnroad", 1.0, block=True)
  params.put_bool("NetworkMetered", False)
  params_writer.put_bool("IsEngaged", True)
  Params().put("DriverLockoutCount", 1)
  self.params.remove("DriverTooDistracted")
'''
    found = blocking_writes(_function(ast.parse(src), "hardware_thread"))
    assert [f.split(":")[0] for f in found] == ["line 3", "line 4", "line 5", "line 8", "line 9"], found
