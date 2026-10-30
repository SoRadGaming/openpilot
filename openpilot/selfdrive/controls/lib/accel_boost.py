import numpy as np
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL

# FORK: commaai/openpilot#39015 (upstream sunnypilot has it), ported ahead of the sync. One change: update() takes the
# planner's is_e2e for experimentalMode, which upstream gated on until chill mode went (#39097) - this fork still has
# chill and DEC's acc phases, where the model is not in the min(). So a press counts only if the model was a candidate
# and the plan's limit on the frame before it; once counting it builds its 0.05 even if DEC goes to acc mid-press (as
# upstream's does if experimental mode is switched mid-press), and that boost waits until the model is a candidate again.

ACCEL_BOOST_MAX = 0.2
ACCEL_BOOST_RATE = 0.025
ACCEL_BOOST_PER_OVERRIDE = 0.05
ACCEL_BOOST_MIN_SPEED = 10 * CV.MPH_TO_MS


class AccelBoost:
  def __init__(self):
    self.total_boost = 0.0
    self.boost_this_override = 0.0
    self.boost_eligible = False

  def update(self, sm, output_a_target_e2e, output_a_target_mpc, a_cruise, is_e2e):
    enabled = sm['selfdriveState'].enabled
    gas_pressed = sm['carState'].gasPressed
    v_ego = sm['carState'].vEgo
    speed_scale = np.interp(v_ego, [ACCEL_BOOST_MIN_SPEED, 2 * ACCEL_BOOST_MIN_SPEED], [0.0, 1.0])
    boost_scale = speed_scale * np.interp(output_a_target_e2e, [-1.0, -0.5, 5.0], [0.0, 1.0, 1.0], right=0.0)
    model_limited = (is_e2e and
                     output_a_target_e2e + self.total_boost * boost_scale < min(output_a_target_mpc, a_cruise) - 0.1)

    if not enabled or not gas_pressed:
      self.boost_this_override = 0.0
      # Latch eligibility before the override changes the lead plan.
      self.boost_eligible = enabled and model_limited

    if not enabled:
      self.total_boost = 0.0
    elif gas_pressed and self.boost_eligible:
      increase = min(ACCEL_BOOST_RATE * DT_MDL * speed_scale,
                     ACCEL_BOOST_PER_OVERRIDE - self.boost_this_override, ACCEL_BOOST_MAX - self.total_boost)
      self.total_boost += increase
      self.boost_this_override += increase

    return output_a_target_e2e + self.total_boost * boost_scale
