import numpy as np
from cereal import car
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N
from openpilot.common.pid import PIDController
from openpilot.selfdrive.modeld.constants import ModelConstants

CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]

LongCtrlState = car.CarControl.Actuators.LongControlState

# FORK: debounce the exit from the stopping state at a standstill.
#
# Measured on route 2418f2eb2b (t~376 s): shouldStop flipped false for ~0.5 s --
# a lead creeping or a model blip -- long control left stopping immediately,
# the brake ramped 0.99 -> 0.48, the car rolled forward at 0.3 m/s for about a
# second, then re-clamped over ~2 s because stoppingDecelRate is 0.8 m/s^3. That
# is the roll you feel at red lights; 17 of 22 holds in that review showed
# micro-motion. It is NOT a pump or brake-calibration problem -- held pressure
# was measured not to decay at stops, even on 2-3 degree grades.
#
# starting_condition above has no debounce at all, so a single frame of
# shouldStop going false is enough to release the brake. Requiring it to hold
# briefly costs nothing at a real launch (the planner holds shouldStop false
# continuously once it means it) and rejects the blips.
STANDSTILL_SPEED = 0.15          # m/s, matches the review's hold threshold
STOPPING_EXIT_DEBOUNCE = 40      # frames at 100 Hz -> 0.4 s


def long_control_state_trans(CP, CP_SP, active, long_control_state, v_ego,
                             should_stop, brake_pressed, cruise_standstill):
  # Gas Interceptor
  cruise_standstill = cruise_standstill and not CP_SP.enableGasInterceptor

  stopping_condition = should_stop
  starting_condition = (not should_stop and
                        not cruise_standstill and
                        not brake_pressed)
  started_condition = v_ego > CP.vEgoStarting

  if not active:
    long_control_state = LongCtrlState.off

  else:
    if long_control_state == LongCtrlState.off:
      if not starting_condition:
        long_control_state = LongCtrlState.stopping
      else:
        if starting_condition and CP.startingState:
          long_control_state = LongCtrlState.starting
        else:
          long_control_state = LongCtrlState.pid

    elif long_control_state == LongCtrlState.stopping:
      if starting_condition and CP.startingState:
        long_control_state = LongCtrlState.starting
      elif starting_condition:
        long_control_state = LongCtrlState.pid

    elif long_control_state in [LongCtrlState.starting, LongCtrlState.pid]:
      if stopping_condition:
        long_control_state = LongCtrlState.stopping
      elif started_condition:
        long_control_state = LongCtrlState.pid
  return long_control_state

class LongControl:
  def __init__(self, CP, CP_SP):
    self.CP = CP
    self.CP_SP = CP_SP
    self.long_control_state = LongCtrlState.off
    self.pid = PIDController((CP.longitudinalTuning.kpBP, CP.longitudinalTuning.kpV),
                             (CP.longitudinalTuning.kiBP, CP.longitudinalTuning.kiV),
                             rate=1 / DT_CTRL)
    self.last_output_accel = 0.0

    # FORK: shares HondaDynamicTuningEnabled so the road test has a single
    # switch. That is a naming wart in a core file -- if this goes upstream or
    # you want to A/B it separately, give it its own param here; nothing else
    # depends on which key it reads.
    self._stopping_debounce = 0
    try:
      from openpilot.common.params import Params
      if Params().get_bool("HondaDynamicTuningEnabled"):
        self._stopping_debounce = STOPPING_EXIT_DEBOUNCE
    except Exception:
      pass
    self._stopping_exit_frames = 0

  def reset(self):
    self.pid.reset()

  def update(self, active, CS, a_target, should_stop, accel_limits):
    """Update longitudinal control. This updates the state machine and runs a PID loop"""
    self.pid.neg_limit = accel_limits[0]
    self.pid.pos_limit = accel_limits[1]

    prev_state = self.long_control_state
    self.long_control_state = long_control_state_trans(self.CP, self.CP_SP, active, self.long_control_state, CS.vEgo,
                                                       should_stop, CS.brakePressed,
                                                       CS.cruiseState.standstill)

    # FORK: hold the stopping state until the intent to start has been sustained.
    # Applied as a post-step rather than inside long_control_state_trans so that
    # function keeps its signature and stays pure for the existing tests.
    # A driver gas press always releases immediately -- an override must never
    # wait on a debounce.
    #
    # Only a transition toward a LAUNCH is debounced. Matching "any state other
    # than stopping" would also catch stopping -> off, which is what a disengage
    # looks like: the debounce would then report stopping and keep commanding
    # stopAccel for 0.4 s after the driver dropped out, in a file every car runs.
    leaving_stop = (prev_state == LongCtrlState.stopping and
                    self.long_control_state in (LongCtrlState.pid, LongCtrlState.starting))
    if (self._stopping_debounce > 0 and leaving_stop
        and CS.vEgo < STANDSTILL_SPEED and not CS.gasPressed):
      self._stopping_exit_frames += 1
      if self._stopping_exit_frames < self._stopping_debounce:
        self.long_control_state = LongCtrlState.stopping
    else:
      self._stopping_exit_frames = 0

    if self.long_control_state == LongCtrlState.off:
      self.reset()
      output_accel = 0.

    elif self.long_control_state == LongCtrlState.stopping:
      output_accel = self.last_output_accel
      if output_accel > self.CP.stopAccel:
        output_accel = min(output_accel, 0.0)
        output_accel -= self.CP.stoppingDecelRate * DT_CTRL
      self.reset()

    elif self.long_control_state == LongCtrlState.starting:
      output_accel = self.CP.startAccel
      self.reset()

    else:  # LongCtrlState.pid
      error = a_target - CS.aEgo
      output_accel = self.pid.update(error, speed=CS.vEgo,
                                     feedforward=a_target)

    self.last_output_accel = np.clip(output_accel, accel_limits[0], accel_limits[1])
    return self.last_output_accel
