import numpy as np
from abc import abstractmethod, ABC
from openpilot.selfdrive.locationd.helpers import Pose


class LatControl(ABC):
  def __init__(self, CP, CP_SP, CI, dt):
    self.dt = dt
    self.sat_limit = CP.steerLimitTimer
    self.sat_time = 0.
    self.sat_check_min_speed = 10.

    # we define the steer torque scale as [-1.0...1.0]
    self.steer_max = 1.0

    # --- LIN-bus gateway (HONDA_ELESYS aftermarket EPS gateway) ------------------
    # On that car nothing follows openpilot's steering request until the gateway board
    # engages: the camera talks LIN to the EPS and the board translates. Until then the
    # loop is OPEN -- latActive is true, we command, the car does not respond, the error
    # never closes, and the integrator winds without bound. Measured on route 000000b3:
    # +0.65 of integrator, 65% of the board's whole torque budget, pointing RIGHT through
    # a LEFT curve the feedforward already had right.
    #
    # controlsd feeds the board's own status (GW_ACTIVE, 0x704) in here every frame. The
    # torque controllers hold the integrator while the board is not actuating and reset
    # the PID the frame it takes over, so nothing accumulated open-loop is ever handed to
    # a car that has just started listening. Defaults mean "no gateway on this car", so
    # every other platform is bit-identical to before.
    self.linbus_gateway_present = False
    self.linbus_gateway_actuating = False
    self._linbus_was_actuating = False
    # the gateway-caused hold specifically, echoed back to the board as an acknowledgement
    self.integrator_frozen = False

  def set_linbus_gateway(self, present: bool, actuating: bool) -> None:
    self.linbus_gateway_present = present
    self.linbus_gateway_actuating = actuating

  def _linbus_integrator_gate(self) -> bool:
    """True while the integrator must be held for the gateway.

    Both halves are needed. Freeze alone leaves whatever the integrator held when the board
    engages; reset alone lets it wind during a dry run and dump it in on the first closed
    frame. Together, i is zero until the loop is genuinely closed and only ever integrates
    against a car that is responding.
    """
    if not self.linbus_gateway_present:
      self._linbus_was_actuating = False
      self.integrator_frozen = False
      return False
    actuating = self.linbus_gateway_actuating
    if actuating and not self._linbus_was_actuating:
      pid = getattr(self, "pid", None)
      if pid is not None:
        pid.reset()
    self._linbus_was_actuating = actuating
    self.integrator_frozen = not actuating
    return self.integrator_frozen

  @abstractmethod
  def update(self, active: bool, CS, VM, params, steer_limited_by_safety: bool, desired_curvature: float, calibrated_pose: Pose,
             curvature_limited: bool, lat_delay: float):
    pass

  def reset(self):
    self.sat_time = 0.

  def _check_saturation(self, saturated, CS, steer_limited_by_safety, curvature_limited):
    # Saturated only if control output is not being limited by car torque/angle rate limits
    if (saturated or curvature_limited) and CS.vEgo > self.sat_check_min_speed and not steer_limited_by_safety and not CS.steeringPressed:
      self.sat_time += self.dt
    else:
      self.sat_time -= self.dt
    self.sat_time = np.clip(self.sat_time, 0.0, self.sat_limit)
    return self.sat_time > (self.sat_limit - 1e-3)
