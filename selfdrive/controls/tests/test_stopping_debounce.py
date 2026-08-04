#!/usr/bin/env python3
"""Checks for the stopping-exit debounce in longcontrol.py.

This is a core-openpilot file that every car goes through, so the property that
matters most is the first one: with the toggle off, the state machine must be
bit-identical to upstream.
"""

import sys
import types
from dataclasses import dataclass, field

# --- stub the openpilot surface longcontrol.py imports -----------------------

def _mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

class _LCS:
    off = "off"
    pid = "pid"
    starting = "starting"
    stopping = "stopping"

_mod("cereal", car=types.SimpleNamespace(
    CarControl=types.SimpleNamespace(Actuators=types.SimpleNamespace(LongControlState=_LCS))))
_mod("openpilot")
_mod("openpilot.common")
_mod("openpilot.common.realtime", DT_CTRL=0.01)
_mod("openpilot.selfdrive")
_mod("openpilot.selfdrive.controls")
_mod("openpilot.selfdrive.controls.lib")
_mod("openpilot.selfdrive.controls.lib.drive_helpers", CONTROL_N=5)
_mod("openpilot.selfdrive.modeld")
_mod("openpilot.selfdrive.modeld.constants",
     ModelConstants=types.SimpleNamespace(T_IDXS=[0., .1, .2, .3, .4, .5, .6, .7]))

class _PID:
    def __init__(self, *a, **k): self.neg_limit, self.pos_limit = -4., 2.
    def reset(self): pass
    def update(self, error, speed=0., feedforward=0.): return float(feedforward)
_mod("openpilot.common.pid", PIDController=_PID)

TOGGLE = {"HondaDynamicTuningEnabled": False}
class _Params:
    def get_bool(self, k): return bool(TOGGLE.get(k, False))
_mod("openpilot.common.params", Params=_Params)

import importlib.util
import os
_LONGCONTROL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            os.pardir, "lib", "longcontrol.py")
spec = importlib.util.spec_from_file_location("longcontrol", os.path.normpath(_LONGCONTROL))
lc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lc)

FAILURES = []
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)

# --- doubles -----------------------------------------------------------------

@dataclass
class CP:
    vEgoStarting: float = 0.5
    vEgoStopping: float = 0.8
    startingState: bool = False
    stopAccel: float = -2.0
    stoppingDecelRate: float = 0.8
    startAccel: float = 0.0
    longitudinalTuning: object = field(default_factory=lambda: types.SimpleNamespace(
        kpBP=[0], kpV=[1.], kiBP=[0], kiV=[1.]))

@dataclass
class CPSP:
    enableGasInterceptor: bool = True

@dataclass
class CruiseState:
    standstill: bool = False

@dataclass
class CS:
    vEgo: float = 0.0
    aEgo: float = 0.0
    brakePressed: bool = False
    gasPressed: bool = False
    cruiseState: CruiseState = field(default_factory=CruiseState)

def make_loc(toggle):
    TOGGLE["HondaDynamicTuningEnabled"] = toggle
    return lc.LongControl(CP(), CPSP())

def run(loc, frames, should_stop, v_ego=0.0, gas=False, brake=False, active=True):
    """Returns the list of states over `frames` frames."""
    out = []
    cs = CS(vEgo=v_ego, gasPressed=gas, brakePressed=brake)
    for _ in range(frames):
        loc.update(active, cs, 0.0, should_stop, (-4.0, 2.0))
        out.append(loc.long_control_state)
    return out


# --- 1. toggle off is upstream behaviour ------------------------------------

print("\n[1] toggle off -> unchanged")
loc = make_loc(False)
check("debounce disabled when the toggle is off", loc._stopping_debounce == 0)
run(loc, 50, True)                      # settle into stopping
check("reaches stopping", loc.long_control_state == lc.LongCtrlState.stopping)
states = run(loc, 5, False)             # one frame of shouldStop false
check("leaves stopping on the very first frame, as upstream does",
      states[0] == lc.LongCtrlState.pid, str(states[:3]))


# --- 2. toggle on rejects a blip --------------------------------------------

print("\n[2] toggle on -> blips rejected")
loc = make_loc(True)
check("debounce active when the toggle is on",
      loc._stopping_debounce == lc.STOPPING_EXIT_DEBOUNCE)
run(loc, 50, True)
blip = run(loc, lc.STOPPING_EXIT_DEBOUNCE - 5, False)     # 0.35 s blip, shorter than the debounce
check("holds stopping through a sub-threshold blip",
      all(s == lc.LongCtrlState.stopping for s in blip), str(set(blip)))
back = run(loc, 10, True)
check("returns cleanly when shouldStop comes back",
      all(s == lc.LongCtrlState.stopping for s in back))
check("counter reset by the blip ending", loc._stopping_exit_frames == 0)


# --- 3. a real launch still gets through ------------------------------------

print("\n[3] toggle on -> real launch proceeds")
loc = make_loc(True)
run(loc, 50, True)
launch = run(loc, lc.STOPPING_EXIT_DEBOUNCE + 20, False)
check("eventually leaves stopping", launch[-1] == lc.LongCtrlState.pid)
delay = sum(1 for s in launch if s == lc.LongCtrlState.stopping)
check("delay is exactly the debounce length",
      delay == lc.STOPPING_EXIT_DEBOUNCE - 1, f"{delay} frames")
check("delay is under half a second", delay * 0.01 < 0.5, f"{delay*0.01:.2f} s")


# --- 4. driver override is never delayed ------------------------------------

print("\n[4] driver override")
loc = make_loc(True)
run(loc, 50, True)
states = run(loc, 5, False, gas=True)
check("gas press releases immediately, no debounce",
      states[0] == lc.LongCtrlState.pid, str(states[:3]))


# --- 5. only applies at a standstill ----------------------------------------

print("\n[5] moving traffic is unaffected")
loc = make_loc(True)
run(loc, 50, True, v_ego=0.0)
states = run(loc, 5, False, v_ego=2.0)   # rolling, above STANDSTILL_SPEED
check("no debounce while still rolling", states[0] == lc.LongCtrlState.pid, str(states[:3]))


# --- 6. brake stays applied while held ---------------------------------------

print("\n[6] brake output during the hold")
loc = make_loc(True)
# stoppingDecelRate 0.8 m/s^3 at 100 Hz needs 250 frames to walk from 0 to stopAccel
run(loc, 400, True)
held = loc.last_output_accel
check("brake command ramps all the way down to stopAccel during the hold",
      held <= CP().stopAccel + 1e-6, f"{held:.3f} vs {CP().stopAccel}")
loc2 = make_loc(True)
run(loc2, 400, True)
before = loc2.last_output_accel
run(loc2, lc.STOPPING_EXIT_DEBOUNCE - 5, False)
check("brake does not release during a rejected blip",
      loc2.last_output_accel <= before + 1e-6,
      f"{before:.3f} -> {loc2.last_output_accel:.3f}")


# --- 7. flapping cannot accumulate credit ------------------------------------

print("\n[7] flapping")
loc = make_loc(True)
run(loc, 50, True)
for _ in range(30):
    run(loc, 10, False)      # 0.1 s of "go"
    run(loc, 10, True)       # then "stop" again
check("alternating shouldStop never escapes the hold",
      loc.long_control_state == lc.LongCtrlState.stopping)


# --- 8. a disengage is never debounced ---------------------------------------
#
# Regression: matching "left stopping for any other state" also matched
# stopping -> off, so a driver disengaging at a red light kept longControlState
# reporting stopping and kept commanding stopAccel for the whole 0.4 s. Only a
# transition toward a launch (pid/starting) may be held.

print("\n[8] disengage is not debounced")
loc = make_loc(True)
run(loc, 400, True)                     # deep into the hold, output at stopAccel
check("hold is at stopAccel before disengage",
      loc.last_output_accel <= CP().stopAccel + 1e-6, f"{loc.last_output_accel:.3f}")
states = run(loc, 60, True, active=False)
check("disengage goes straight to off, no debounce",
      all(s == lc.LongCtrlState.off for s in states), str(set(states)))
check("output is released immediately on disengage",
      abs(loc.last_output_accel) < 1e-9, f"{loc.last_output_accel:.3f}")

# a car with startingState must still get the debounce on a real launch
cp = CP()
cp.startingState = True
loc = lc.LongControl(cp, CPSP())
run(loc, 50, True)
launch = run(loc, lc.STOPPING_EXIT_DEBOUNCE + 10, False)
held = sum(1 for s in launch if s == lc.LongCtrlState.stopping)
check("startingState car is still debounced into starting",
      held == lc.STOPPING_EXIT_DEBOUNCE - 1 and launch[-1] == lc.LongCtrlState.starting,
      f"{held} frames, ended {launch[-1]}")


print("\n" + "=" * 60)
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("ALL CHECKS PASSED")
