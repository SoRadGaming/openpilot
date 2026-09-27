"""The stopping-exit debounce in longcontrol.py and this car's stopping tune (stopping_tune.py).

longcontrol.py is a core file that every car goes through, so the properties that matter
most are that with the toggle off the state machine is upstream's, and that a car not in
stopping_tune.py gets upstream's stopping speed (0.3 m/s) and ramp (1.0 m/s^3).
"""
from dataclasses import dataclass, field

import numpy as np

from opendbc.car.structs import car
from openpilot.cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib import longcontrol as lc
from openpilot.selfdrive.controls.lib.drive_helpers import should_stop
from openpilot.sunnypilot.selfdrive.controls.lib.stopping_tune import STOPPING_SPEED, STOPPING_DECEL_RATE

LongCtrlState = lc.LongCtrlState

THIS_CAR = "HONDA_ACCORD_9G_AU"
OTHER_CAR = "HONDA_CIVIC"
STOP_ACCEL = -2.0
ACCEL_LIMITS = (-4.0, 2.0)


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


def make_loc(toggle: bool, fingerprint: str = THIS_CAR) -> lc.LongControl:
  Params().put_bool("HondaDynamicTuningEnabled", toggle)
  CP = car.CarParams.new_message()
  CP.carFingerprint = fingerprint
  CP.stopAccel = STOP_ACCEL
  CP.longitudinalTuning.kiBP = [0.]
  CP.longitudinalTuning.kiV = [1.]
  CP_SP = custom.CarParamsSP.new_message()
  CP_SP.enableGasInterceptor = True
  return lc.LongControl(CP, CP_SP)


def run(loc, frames, stop, v_ego=0.0, gas=False, brake=False, active=True):
  """Returns the list of states over `frames` frames."""
  out = []
  cs = CS(vEgo=v_ego, gasPressed=gas, brakePressed=brake)
  for _ in range(frames):
    loc.update(active, cs, 0.0, stop, ACCEL_LIMITS)
    out.append(loc.long_control_state)
  return out


class TestStoppingDebounce(OpenpilotTestCase):
  def test_toggle_off_is_upstream(self):
    loc = make_loc(False)
    assert loc._stopping_debounce == 0, "debounce disabled when the toggle is off"
    run(loc, 50, True)                      # settle into stopping
    assert loc.long_control_state == LongCtrlState.stopping
    states = run(loc, 5, False)             # one frame of shouldStop false
    assert states[0] == LongCtrlState.pid, f"leaves stopping on the very first frame, as upstream does: {states[:3]}"

  def test_a_blip_is_rejected(self):
    loc = make_loc(True)
    assert loc._stopping_debounce == lc.STOPPING_EXIT_DEBOUNCE, "debounce active when the toggle is on"
    run(loc, 50, True)
    blip = run(loc, lc.STOPPING_EXIT_DEBOUNCE - 5, False)     # 0.35 s blip, shorter than the debounce
    assert all(s == LongCtrlState.stopping for s in blip), f"holds stopping through a sub-threshold blip: {set(blip)}"
    back = run(loc, 10, True)
    assert all(s == LongCtrlState.stopping for s in back), "returns cleanly when shouldStop comes back"
    assert loc._stopping_exit_frames == 0, "counter reset by the blip ending"

  def test_a_real_launch_proceeds(self):
    loc = make_loc(True)
    run(loc, 50, True)
    launch = run(loc, lc.STOPPING_EXIT_DEBOUNCE + 20, False)
    assert launch[-1] == LongCtrlState.pid, "eventually leaves stopping"
    delay = sum(1 for s in launch if s == LongCtrlState.stopping)
    assert delay == lc.STOPPING_EXIT_DEBOUNCE - 1, f"delay is exactly the debounce length: {delay} frames"
    assert delay * DT_CTRL < 0.5, f"delay is under half a second: {delay * DT_CTRL:.2f} s"
    # upstream 031b1ad0a: there is no starting state, a launch is stopping -> pid
    assert LongCtrlState.starting not in launch

  def test_gas_press_releases_immediately(self):
    loc = make_loc(True)
    run(loc, 50, True)
    states = run(loc, 5, False, gas=True)
    assert states[0] == LongCtrlState.pid, f"gas press releases immediately, no debounce: {states[:3]}"

  def test_moving_traffic_is_unaffected(self):
    loc = make_loc(True)
    run(loc, 50, True, v_ego=0.0)
    states = run(loc, 5, False, v_ego=2.0)   # rolling, above STANDSTILL_SPEED
    assert states[0] == LongCtrlState.pid, f"no debounce while still rolling: {states[:3]}"

  def test_brake_stays_applied_while_held(self):
    loc = make_loc(True)
    # 0.8 m/s^3 at 100 Hz needs 250 frames to walk from 0 to STOP_ACCEL
    run(loc, 400, True)
    held = loc.last_output_accel
    assert held <= STOP_ACCEL + 1e-6, f"brake command ramps all the way down to stopAccel during the hold: {held:.3f}"
    before = loc.last_output_accel
    run(loc, lc.STOPPING_EXIT_DEBOUNCE - 5, False)
    assert loc.last_output_accel <= before + 1e-6, f"brake does not release during a rejected blip: {before:.3f} -> {loc.last_output_accel:.3f}"

  def test_flapping_cannot_accumulate_credit(self):
    loc = make_loc(True)
    run(loc, 50, True)
    for _ in range(30):
      run(loc, 10, False)      # 0.1 s of "go"
      run(loc, 10, True)       # then "stop" again
    assert loc.long_control_state == LongCtrlState.stopping, "alternating shouldStop never escapes the hold"

  def test_a_disengage_is_never_debounced(self):
    """Regression: matching "left stopping for any other state" also matched stopping -> off, so a
    driver disengaging at a red light kept longControlState reporting stopping and kept commanding
    stopAccel for the whole 0.4 s. Only a transition toward a launch may be held."""
    loc = make_loc(True)
    run(loc, 400, True)                     # deep into the hold, output at stopAccel
    assert loc.last_output_accel <= STOP_ACCEL + 1e-6, f"hold is at stopAccel before disengage: {loc.last_output_accel:.3f}"
    states = run(loc, 60, True, active=False)
    assert all(s == LongCtrlState.off for s in states), f"disengage goes straight to off, no debounce: {set(states)}"
    assert abs(loc.last_output_accel) < 1e-9, f"output is released immediately on disengage: {loc.last_output_accel:.3f}"


class TestStoppingTune(OpenpilotTestCase):
  """D2: upstream removed per-car stopping tunes; this car keeps vEgoStopping 0.8 and a 0.8 m/s^3 ramp."""

  def test_the_table_holds_this_cars_values(self):
    assert STOPPING_SPEED[THIS_CAR] == 0.8
    # float32(0.8), what CP.stoppingDecelRate delivered before the merge (bit-for-bit ramp parity)
    assert STOPPING_DECEL_RATE[THIS_CAR] == float(np.float32(0.8))
    assert OTHER_CAR not in STOPPING_SPEED and OTHER_CAR not in STOPPING_DECEL_RATE

  def _ramp_after(self, fingerprint: str, frames: int) -> float:
    loc = make_loc(False, fingerprint)
    run(loc, frames, True)
    return float(loc.last_output_accel)

  def test_this_car_ramps_at_its_own_rate(self):
    assert make_loc(False, THIS_CAR).stopping_decel_rate == float(np.float32(0.8))
    self.assertAlmostEqual(self._ramp_after(THIS_CAR, 100), -0.8 * 100 * DT_CTRL, places=6)

  def test_this_car_holds_exactly_at_its_stop_accel(self):
    """CP.stopAccel is a capnp Float32 (-0.8 reads back as -0.800000011920929). Ramping at the
    Python float 0.8 from zero stops one step short of it and takes a 101st step to -0.808;
    ramping at float32(0.8), as CP.stoppingDecelRate did before the merge, lands on it."""
    loc = make_loc(False, THIS_CAR)
    loc.CP.stopAccel = -0.8   # this car's value (opendbc honda/interface.py)
    run(loc, 400, True)
    assert abs(float(loc.last_output_accel) - loc.CP.stopAccel) < 1e-6, \
      f"hold is {float(loc.last_output_accel):.6f}, stopAccel is {loc.CP.stopAccel:.6f}"

  def test_another_car_ramps_at_upstreams_rate(self):
    assert make_loc(False, OTHER_CAR).stopping_decel_rate == 1.0
    self.assertAlmostEqual(self._ramp_after(OTHER_CAR, 100), -1.0 * 100 * DT_CTRL, places=6)

  def test_both_hold_at_stop_accel(self):
    for fingerprint in (THIS_CAR, OTHER_CAR):
      ramp = self._ramp_after(fingerprint, 400)
      # the ramp stops at the first step that reaches stopAccel, so it may pass it by less than one step
      assert STOP_ACCEL - 1.0 * DT_CTRL - 1e-9 <= ramp <= STOP_ACCEL + 1e-9, f"{fingerprint}: {ramp:.4f}"

  def test_upstream_should_stop_is_unchanged_without_an_override(self):
    assert should_stop(0.25, 0.0)
    assert not should_stop(0.35, 0.0)
    assert not should_stop(0.25, 0.1)

  def test_this_car_may_stop_below_its_own_speed(self):
    v_ego_stopping = STOPPING_SPEED.get(THIS_CAR)
    assert should_stop(0.5, 0.0, v_ego_stopping=v_ego_stopping)
    assert should_stop(0.79, 0.05, v_ego_stopping=v_ego_stopping)
    assert not should_stop(0.85, 0.0, v_ego_stopping=v_ego_stopping)
    assert not should_stop(0.5, 0.1, v_ego_stopping=v_ego_stopping)

  def test_another_car_gets_upstreams_stopping_speed(self):
    v_ego_stopping = STOPPING_SPEED.get(OTHER_CAR)
    assert v_ego_stopping is None
    assert should_stop(0.25, 0.0, v_ego_stopping=v_ego_stopping)
    assert not should_stop(0.5, 0.0, v_ego_stopping=v_ego_stopping)

  def test_the_planner_reads_the_table(self):
    # imported here: the planner builds the longitudinal MPC, which needs the compiled solver
    from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
    for fingerprint, expected in ((THIS_CAR, 0.8), (OTHER_CAR, None)):
      CP = car.CarParams.new_message()
      CP.carFingerprint = fingerprint
      planner = LongitudinalPlanner(CP, custom.CarParamsSP.new_message().as_reader())
      assert planner.v_ego_stopping == expected
