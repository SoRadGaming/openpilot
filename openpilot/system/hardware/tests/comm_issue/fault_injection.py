"""
FORK(COMM-ISSUE): the bench proof of the "Communication Issue Between Processes" fix (docs/fork/COMM-ISSUE.md).

Runs the REAL hardwared - its main(), so hw_state_thread and hardware_thread - in a child process, unpatched
(hardwared.py from --baseline, push11 39cbb01be by default, imported against this tree) and patched (this tree), with
Params on a scratch root (PARAMS_ROOT) and an OPENPILOT_PREFIX of its own. The parent feeds it pandaStates (10 Hz,
ignition on), peripheralState (2 Hz, 13 V) and selfdriveState (100 Hz), and the thermal zones drift (+-0.3 C, with
sample noise) so the temperature alert's 0.1 C text moves about as often as in the car. One fault per run, FAULT_S long:

  F1  another process holds <params>/.lock with flock(LOCK_EX) (a put stuck in a slow journal commit), and the
      engage state changes 2 s in, so the patched hardwared has something to write inside the window
  F2  every directory fsync that starts inside the window returns at its end (fscount.c, LD_PRELOAD): one slow
      commit and no other process involved; the same engage edge
  F3  F1 and F2 together, with two engage edges and hardwared's 60 s Uptime write inside the window

The parent stamps each deviceState as it arrives, runs cereal's own SubMaster on the stream at selfdrived's 100 Hz
(alive and freq_ok, which selfdrived.py turns into commIssue and commIssueAvgFreq), and replays the receive times
through vf_model.py's copy of the same logic (d1024). fscount.c records every fsync and flock(LOCK_EX) with its thread.

PASS: unpatched shows the stall and the alert in every fault; patched keeps every gap under 0.75 s, predicts no
alert, and its publishing thread (hardware_thread) makes no flock() and no fsync() after its first loop.

Needs gcc and git. About four minutes, so it is not a unit test (test_hardwared_comm_issue.py is). From the repo root:
  python openpilot/system/hardware/tests/comm_issue/fault_injection.py [--baseline REV] [--only F1,F3] [--json OUT]
"""
import argparse
import fcntl
import json
import math
import os
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3].parent  # the repo: openpilot/system/hardware/tests/comm_issue -> ../../../../..

FAULT_S = 8.0
WARMUP_S = 5.0          # selfdrived starts with a full window; the checks are read from here on
MAX_GAP_PATCHED_S = 0.75
# at: fault start, s after the first deviceState; edges: selfdriveState.enabled toggles (s after the first deviceState)
FAULTS = {
  "F1": {"at": 10.0, "lock": True, "fsync": False, "edges": (12.0,), "run": 25.0, "enabled": True},
  "F2": {"at": 10.0, "lock": False, "fsync": True, "edges": (12.0,), "run": 25.0, "enabled": True},
  # hardwared's count reaches 120 (its second Uptime write) about 59.5 s after its first loop
  "F3": {"at": 55.5, "lock": True, "fsync": True, "edges": (57.5, 61.0), "run": 75.0, "enabled": False},
}


# ------------------------------------------------------------------------------------------------ child: hardwared
def child_main(variant: str, baseline: str, out: str) -> None:
  # the parent stops hardwared with SIGINT, as manager does; a shell or `timeout` above may have left it ignored
  signal.signal(signal.SIGINT, signal.default_int_handler)
  import importlib.util
  from openpilot.common import params as params_mod
  from openpilot.common.hardware.base import ThermalConfig

  if variant == "patched":
    import openpilot.system.hardware.hardwared as hw
  else:
    spec = importlib.util.spec_from_file_location("hardwared_baseline", baseline)
    assert spec is not None and spec.loader is not None
    hw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hw)

  # the thermal zones drift: a +-0.3 C sine and +-0.6 C of sample noise, so the filtered temperature's 0.1 C text moves
  # in about a fifth of the loops (17-22 % in the recorded runs; 26-30 % on 14d)
  rng = random.Random(1234)
  t0 = time.monotonic()

  class Drift:
    def __init__(self, base: float):
      self.base = base

    def read(self) -> float:
      return self.base + 0.3 * math.sin((time.monotonic() - t0) / 3.) + rng.uniform(-0.6, 0.6)

  cfg = ThermalConfig(cpu=[Drift(55.), Drift(54.6)], gpu=[Drift(50.)], memory=Drift(48.), pmic=[Drift(45.)])
  hw.HARDWARE.get_thermal_config = lambda: cfg

  info: dict = {"variant": variant, "pid": os.getpid()}
  calls: list = []     # Params calls: (tid, method, key, block, start, dur)
  alert_calls: list = []  # set_offroad_alert_if_changed(alert, show, extra), as the loop made them

  def wrap(name: str):
    orig = getattr(params_mod.Params, name)

    def w(self, key, *a, **kw):
      t = time.monotonic()
      try:
        return orig(self, key, *a, **kw)
      finally:
        if name in ("put", "put_bool"):
          block = kw.get("block", a[1] if len(a) > 1 else False)
        elif name in ("get", "get_bool"):
          block = kw.get("block", a[0] if a else False)
        else:
          block = True
        k = key.decode() if isinstance(key, bytes) else key
        calls.append((threading.get_native_id(), name, k, bool(block), t, time.monotonic() - t))
    setattr(params_mod.Params, name, w)

  for name in ("put", "put_bool", "remove", "get", "get_bool"):
    wrap(name)

  orig_if_changed = hw.set_offroad_alert_if_changed

  def if_changed(alert, show_alert, extra_text=None, **kw):
    alert_calls.append((alert, bool(show_alert), extra_text))
    return orig_if_changed(alert, show_alert, extra_text, **kw)
  hw.set_offroad_alert_if_changed = if_changed

  orig_thread = hw.hardware_thread

  def hardware_thread(end_event, hw_queue):
    info["publishing_tid"] = threading.get_native_id()
    orig_thread(end_event, hw_queue)
  hw.hardware_thread = hardware_thread

  try:
    hw.main()
  except KeyboardInterrupt:
    pass
  finally:
    temp = [c for c in alert_calls if c[0] == "Offroad_TemperatureTooHigh"]
    info["temp_alert_calls"] = len(temp)
    info["temp_text_changes"] = sum(1 for a, b in zip(temp, temp[1:], strict=False) if a[2] != b[2])
    info["calls"] = calls
    with open(out, "w") as f:
      json.dump(info, f)
    # leave without interpreter shutdown, which can hang for 30 s in statsd's zmq term(); the shim's record first
    import ctypes
    try:
      ctypes.CDLL(None).fscount_dump()
    except AttributeError:
      pass
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


# ------------------------------------------------------------------------------------------------ parent
def replay_alerts(recv: list[float], start: float, end: float) -> list[tuple[float, str]]:
  """vf_model.py (d1024): cereal's alive / FrequencyTracker logic for deviceState at selfdrived's 100 Hz, over the
  receive times. Returns the onsets (t, event) from start to end."""
  avg: deque = deque(maxlen=20)
  rec: deque = deque(maxlen=2)
  prev = last = None
  j = 0
  out: list[tuple[float, str]] = []
  state = None
  n = int((end - start) / 0.01)
  for i in range(n):
    now = start + i * 0.01
    while j < len(recv) and recv[j] <= now:
      if prev is not None:
        avg.append(recv[j] - prev)
        rec.append(recv[j] - prev)
      prev = last = recv[j]
      j += 1
    if last is None or not avg:
      continue
    alive = (now - last) < 5.0
    ok = (1.6 <= 1 / (sum(avg) / len(avg)) <= 2.4) or (1.6 <= 1 / (sum(rec) / len(rec)) <= 2.4)
    s = "commIssue" if not alive else ("commIssueAvgFreq" if not ok else None)
    if s != state and s is not None:
      out.append((round(now, 2), s))
    state = s
  return out


def build_shim(workdir: Path) -> Path:
  so = workdir / "fscount.so"
  subprocess.run(["gcc", "-shared", "-fPIC", "-O2", "-o", str(so), str(HERE / "fscount.c"), "-ldl"], check=True)
  return so


def baseline_file(rev: str, workdir: Path) -> Path:
  out = workdir / f"hardwared_{rev}.py"
  src = subprocess.run(["git", "-C", str(ROOT), "show", f"{rev}:openpilot/system/hardware/hardwared.py"],
                       check=True, capture_output=True).stdout
  out.write_bytes(src)
  return out


def run_one(variant: str, fault: str, baseline: Path, shim: Path, workdir: Path) -> dict:
  import openpilot.cereal.messaging as messaging
  from openpilot.cereal import log
  from openpilot.common.params import Params
  from openpilot.common.prefix import OpenpilotPrefix
  from openpilot.common.version import terms_version, terms_version_sp, training_version

  spec = FAULTS[fault]
  rundir = workdir / f"{variant}_{fault}"
  rundir.mkdir(parents=True, exist_ok=True)
  params_root = rundir / "params"
  ctl = rundir / "ctl"
  shimlog = rundir / "fscount"
  child_json = rundir / "child.json"
  prefix = f"cifi{os.getpid() % 100000}{variant[0]}{fault}"

  with OpenpilotPrefix(prefix):
    os.environ["PARAMS_ROOT"] = str(params_root)
    try:
      params = Params()
      params.put("HasAcceptedTerms", terms_version, block=True)
      params.put("HasAcceptedTermsSP", terms_version_sp, block=True)
      params.put("CompletedTrainingVersion", training_version, block=True)
      lock_path = os.path.join(str(params_root), ".lock")

      pm = messaging.PubMaster(["pandaStates", "peripheralState", "selfdriveState"])
      sm = messaging.SubMaster(["deviceState"], frequency=100)  # as selfdrived: SubMaster(..., frequency=1/DT_CTRL)

      env = dict(os.environ, LD_PRELOAD=str(shim), FSCOUNT_LOG=str(shimlog), FSCOUNT_CTL=str(ctl))
      proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", variant, str(baseline),
                               str(child_json)], env=env)

      stop = threading.Event()
      t_first: list[float] = []

      def enabled_at(now: float) -> bool:
        e = spec["enabled"]
        if t_first:
          for edge in spec["edges"]:
            if now - t_first[0] >= edge:
              e = not e
        return e

      def feeder():
        k = 0
        nxt = time.monotonic()
        while not stop.is_set():
          ss = messaging.new_message("selfdriveState")
          ss.selfdriveState.enabled = ss.selfdriveState.active = enabled_at(time.monotonic())
          pm.send("selfdriveState", ss)
          if k % 10 == 0:
            ps = messaging.new_message("pandaStates", 1)
            ps.pandaStates[0].ignitionLine = True
            ps.pandaStates[0].pandaType = log.PandaState.PandaType.tres
            ps.pandaStates[0].harnessStatus = log.PandaState.HarnessStatus.normal
            pm.send("pandaStates", ps)
          if k % 50 == 0:
            per = messaging.new_message("peripheralState")
            per.peripheralState.pandaType = log.PandaState.PandaType.tres
            per.peripheralState.voltage = 13000
            pm.send("peripheralState", per)
          k += 1
          nxt += 0.01
          time.sleep(max(0., nxt - time.monotonic()))

      window: list[float] = []

      def start_fault():
        a = time.monotonic()
        window[:] = [a, a + FAULT_S]
        if spec["fsync"]:
          tmp = ctl.with_suffix(".tmp")
          tmp.write_text(f"{int(a * 1e9)} {int((a + FAULT_S) * 1e9)}")
          os.replace(tmp, ctl)
        if spec["lock"]:
          fd = os.open(lock_path, os.O_CREAT | os.O_RDONLY, 0o775)
          fcntl.flock(fd, fcntl.LOCK_EX)
          threading.Timer(max(0., a + FAULT_S - time.monotonic()), lambda: (fcntl.flock(fd, fcntl.LOCK_UN), os.close(fd))).start()

      threading.Thread(target=feeder, daemon=True).start()
      recv: list[float] = []
      live: list[tuple[float, str]] = []
      state = None
      nxt = time.monotonic()
      deadline = nxt + 60.
      while True:
        sm.update(0)
        now = time.monotonic()
        if sm.updated["deviceState"]:
          recv.append(now)
          if not t_first:
            t_first.append(now)
            threading.Timer(spec["at"], start_fault).start()
        if t_first and now - t_first[0] > WARMUP_S:
          if sm.all_checks(["deviceState"]):
            s = None
          elif not sm.all_alive(["deviceState"]):
            s = "commIssue"
          elif not sm.all_freq_ok(["deviceState"]):
            s = "commIssueAvgFreq"
          else:
            s = "commIssue"
          if s != state and s is not None:
            live.append((round(now - t_first[0], 2), s))
          state = s
        if t_first and now - t_first[0] > spec["run"]:
          break
        if not t_first and now > deadline:
          raise RuntimeError(f"{variant} {fault}: no deviceState in 60 s")
        if proc.poll() is not None:
          raise RuntimeError(f"{variant} {fault}: hardwared exited with {proc.returncode}")
        nxt += 0.01
        time.sleep(max(0., nxt - time.monotonic()))

      stop.set()
      proc.send_signal(signal.SIGINT)
      try:
        proc.wait(30)
      except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
      os.environ.pop("PARAMS_ROOT", None)

  return analyse(variant, fault, spec, recv, live, window, t_first[0], shimlog, proc.pid, child_json)


def analyse(variant, fault, spec, recv, live, window, t_first, shimlog, pid, child_json) -> dict:
  child = json.loads(child_json.read_text())
  tid = child["publishing_tid"]
  first_loop_done = recv[1]  # the second deviceState: the first loop's tail (its puts after pm.send) is behind it

  gaps = [(b - a, a) for a, b in zip(recv, recv[1:], strict=False)]
  max_gap = max(g for g, _ in gaps)
  w0, w1 = window
  in_window = [g for g, a in gaps if w0 - 1. <= a <= w1 + 1.]
  replay = [(round(t - t_first, 2), s) for t, s in replay_alerts(recv, t_first + WARMUP_S, recv[-1] + 1.)]

  events = []
  log_file = Path(f"{shimlog}.{pid}")
  if log_file.exists():
    for line in log_file.read_text().splitlines():
      kind, etid, start, dur = line.split()
      events.append((kind, int(etid), int(start) / 1e9, int(dur) / 1e9))

  def ev_summary(sel):
    out = {}
    for kind in ("flock_ex", "fsync_dir", "fsync"):
      e = [d for k, _, _, d in sel if k == kind]
      out[kind] = {"n": len(e), "max_s": round(max(e), 3) if e else 0., "total_s": round(sum(e), 3)}
    return out

  pub_after = [e for e in events if e[1] == tid and e[2] > first_loop_done]
  pub_all = [e for e in events if e[1] == tid]
  others = [e for e in events if e[1] != tid]

  calls = child["calls"]
  pub_calls = [c for c in calls if c[0] == tid and c[4] > first_loop_done]
  # a blocking write: remove(), or put / put_bool with block=True. A non-blocking put on a long-lived handle (the
  # PowerMonitoring CarBatteryCapacity one) hands the write to that handle's own C++ thread.
  blocking = [c for c in pub_calls if c[1] == "remove" or (c[1] in ("put", "put_bool") and c[3])]
  nonblocking = [c for c in pub_calls if c[1] in ("put", "put_bool") and not c[3]]
  uptime_writes = [round(c[4] - t_first, 2) for c in calls if c[1] == "put" and c[2] == "UptimeOnroad"]
  engaged_writes = [round(c[4] - t_first, 2) for c in calls if c[1] == "put_bool" and c[2] == "IsEngaged"]

  r = {
    "variant": variant, "fault": fault, "window_s": [round(w0 - t_first, 2), round(w1 - t_first, 2)],
    "deviceState": len(recv), "max_gap_s": round(max_gap, 3), "max_gap_in_window_s": round(max(in_window), 3) if in_window else None,
    "gaps_over_1s": sum(1 for g, _ in gaps if g > 1.), "alerts_live": live, "alerts_replay": replay,
    "pub_after_first_loop": ev_summary(pub_after), "pub_all": ev_summary(pub_all), "other_threads": ev_summary(others),
    "pub_blocking_writes_after_first_loop": len(blocking),
    "pub_blocking_write_keys": sorted({c[2] for c in blocking}),
    "pub_nonblocking_puts_after_first_loop": sorted({c[2] for c in nonblocking}),
    "pub_params_reads_after_first_loop": sum(1 for c in pub_calls if c[1] in ("get", "get_bool")),
    "pub_slowest_params_call_s": round(max((c[5] for c in pub_calls), default=0.), 3),
    "temp_alert_calls": child["temp_alert_calls"], "temp_text_changes": child["temp_text_changes"],
    "temp_alert_writes": sum(1 for c in calls if c[2] == "Offroad_TemperatureTooHigh" and c[1] in ("put", "remove")),
    "tici_alert_writes": sum(1 for c in calls if c[2] == "Offroad_TiciSupport" and c[1] in ("put", "remove")),
    "uptime_writes_t": uptime_writes, "is_engaged_writes_t": engaged_writes,
  }
  pub = r["pub_after_first_loop"]
  if variant == "patched":
    r["pass"] = (max_gap < MAX_GAP_PATCHED_S and not live and not replay and pub["flock_ex"]["n"] == 0
                 and pub["fsync_dir"]["n"] == 0 and pub["fsync"]["n"] == 0 and not blocking)
  else:
    r["pass"] = (r["max_gap_in_window_s"] or 0.) >= FAULT_S - 1.5 and any(s == "commIssue" for _, s in live)
  return r


def main() -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("--baseline", default="39cbb01be", help="git revision of the unpatched hardwared.py (push11)")
  ap.add_argument("--only", default="F1,F2,F3")
  ap.add_argument("--variants", default="unpatched,patched")
  ap.add_argument("--json", help="write the results here")
  ap.add_argument("--child", nargs=3, metavar=("VARIANT", "BASELINE", "OUT"), help=argparse.SUPPRESS)
  args = ap.parse_args()
  if args.child:
    child_main(*args.child)
    return 0

  workdir = Path(tempfile.mkdtemp(prefix="comm_issue_fi_"))
  shim = build_shim(workdir)
  baseline = baseline_file(args.baseline, workdir)
  results = []
  for fault in args.only.split(","):
    for variant in args.variants.split(","):
      r = run_one(variant, fault, baseline, shim, workdir)
      results.append(r)
      pub, oth = r["pub_after_first_loop"], r["other_threads"]
      print(" ".join([
        f"{fault} {variant:9s} window {r['window_s']}: {r['deviceState']} deviceState, max gap {r['max_gap_s']:.2f} s",
        f"(in window {r['max_gap_in_window_s']}), live {r['alerts_live']}, replay {r['alerts_replay']};",
        f"hardware_thread after its first loop: flock {pub['flock_ex']['n']} (max {pub['flock_ex']['max_s']} s),",
        f"fsync {pub['fsync_dir']['n']}+{pub['fsync']['n']}, blocking params writes",
        f"{r['pub_blocking_writes_after_first_loop']} {r['pub_blocking_write_keys']};",
        f"other threads: flock max {oth['flock_ex']['max_s']} s, dir fsync max {oth['fsync_dir']['max_s']} s;",
        f"temp text changes {r['temp_text_changes']}/{r['temp_alert_calls']} loops, temp alert writes",
        f"{r['temp_alert_writes']}, tici alert writes {r['tici_alert_writes']};",
        f"IsEngaged at {r['is_engaged_writes_t']}, UptimeOnroad at {r['uptime_writes_t']} ->",
        "PASS" if r["pass"] else "FAIL"]), flush=True)
  if args.json:
    Path(args.json).write_text(json.dumps({"baseline": args.baseline, "results": results}, indent=1))
  ok = all(r["pass"] for r in results)
  print("ALL PASS" if ok else "FAILED")
  return 0 if ok else 1


if __name__ == "__main__":
  sys.exit(main())
