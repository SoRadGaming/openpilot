"""
FORK(LKAS-GATEWAY): openpilot's "Turn Exceeds Steering Limit" (EventName.steerSaturated) decision, in one place.

selfdrived raises steerSaturated only when ALL of these hold - lac.saturated is just the first:
  * the lateral controller is active and has been saturated for steerLimitTimer (lac.saturated);
  * the driver has not pressed the wheel within the last STEER_PRESSED_HOLDOFF seconds;
  * the car is undershooting: |desired lat accel| / |actual lat accel| > UNDERSHOOT_RATIO;
  * it is a real turn: |desired lat accel| > TURNING_LAT_ACCEL;
  * the car is a car (not CP.notCar).
Desired is modelV2.action.desiredCurvature (raw, not controlsd's clipped curvature) and actual is controlsState.curvature,
both times max(vEgo, 0.3)^2.

The same decision goes to the HONDA_ELESYS gateway board as 0x500 OP_SATURATED (controlsd_ext.py), where it drives the
board's HUD alarm. It used to carry lac.saturated raw, and the board alarmed on lane changes where openpilot never would:
routes 115/120/121, 195 saturated frames, every one within 2 s of a steering press and none over 1.0 m/s^2 desired -
120@486.24 and 121@330.31 lit the lane-departure lamp with a held chime. selfdrived.py calls this class too, so the two can
never disagree about what "saturated" means.
"""
from openpilot.common.realtime import DT_CTRL

STEER_PRESSED_HOLDOFF = 2.0   # s
UNDERSHOOT_RATIO = 1.2
TURNING_LAT_ACCEL = 1.0       # m/s^2
MIN_SPEED = 0.3               # m/s, the speed the lateral accelerations are evaluated at below this


class SteerSaturatedAlert:
  """Call update() once per control frame (DT_CTRL), with every input from the same frame.

  frame: the caller's own frame number (selfdrived passes sm.frame, so its window is exactly what it always was).
  Without one, the calls are counted from 0 - which is how selfdrived's sm.frame starts, with
  last_steering_pressed_frame = 0, so nothing can fire in the first STEER_PRESSED_HOLDOFF either way.
  """

  def __init__(self):
    self.frame = 0
    self.last_steering_pressed_frame = 0

  def update(self, steering_pressed: bool, lac_active: bool, lac_saturated: bool, v_ego: float, curvature: float,
             desired_curvature: float, not_car: bool = False, frame: int | None = None) -> bool:
    if frame is not None:
      self.frame = frame
    if steering_pressed:
      self.last_steering_pressed_frame = self.frame
    recent_steer_pressed = (self.frame - self.last_steering_pressed_frame) * DT_CTRL < STEER_PRESSED_HOLDOFF
    self.frame += 1
    if not lac_active or recent_steer_pressed or not_car:
      return False
    clipped_speed = max(v_ego, MIN_SPEED)
    actual_lateral_accel = curvature * (clipped_speed ** 2)
    desired_lateral_accel = desired_curvature * (clipped_speed ** 2)
    undershooting = abs(desired_lateral_accel) / abs(1e-3 + actual_lateral_accel) > UNDERSHOOT_RATIO
    turning = abs(desired_lateral_accel) > TURNING_LAT_ACCEL
    # TODO (upstream): lac.saturated includes speed and other checks, should be pulled out
    return bool(undershooting and turning and lac_saturated)
