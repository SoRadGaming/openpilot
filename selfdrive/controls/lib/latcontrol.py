import numpy as np
from abc import abstractmethod, ABC
from openpilot.selfdrive.locationd.helpers import Pose

# LIN-bus gateway: how much learned steering trim may survive a hold, and how fast a hold
# forgets it. Units are lateral acceleration, the space the torque controller's PID runs in.
LINBUS_I_CARRY_MAX = 0.25   # m/s^2, ~2.5x the trim d3/d4 settled on, well under the b3 windup
LINBUS_I_HOLD_TAU = 30.0    # s, so a minute of not actuating keeps about an eighth of it


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

    The freeze is the load-bearing half: while the board is not actuating the loop is open,
    so nothing is integrated against a car that is not listening. That alone makes the
    open-loop windup this gate was written for (+0.65 on route 000000b3) unreachable.

    The takeover used to zero the integrator outright, and that turned out to cost more than
    it bought. This car has a real, one-signed steering trim -- routes 000000d3 and 000000d4
    hold the integrator positive in all 14 lateral engagements, settling between +0.04 and
    +0.20 m/s^2, roughly ten serial counts of right-hand torque against a persistent left
    pull. Starting from zero every time means re-learning it, and the two engagements that
    did start from zero took 14.1 s and 14.2 s to reach 63% of their settled value. The board
    was actuating for only 48% (d3) and 65% (d4) of the time openpilot was laterally active,
    so that re-learn was paid over and over. From the driver's seat it is the car drifting
    left at every takeover and correcting itself half a minute later.

    So the trim is carried across a hold instead, with two bounds on how much history can
    survive: it is clipped to LINBUS_I_CARRY_MAX on the takeover frame, and it decays with
    LINBUS_I_HOLD_TAU while held, so a trim learned on one road is forgotten rather than
    applied to the next one after a long dark stretch.
    """
    if not self.linbus_gateway_present:
      self._linbus_was_actuating = False
      self.integrator_frozen = False
      return False
    actuating = self.linbus_gateway_actuating
    pid = getattr(self, "pid", None)
    if pid is not None:
      if actuating and not self._linbus_was_actuating:
        pid.i = float(np.clip(pid.i, -LINBUS_I_CARRY_MAX, LINBUS_I_CARRY_MAX))
      elif not actuating:
        pid.i = float(pid.i * np.exp(-self.dt / LINBUS_I_HOLD_TAU))
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
