from openpilot.cereal import log, custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import AutoLaneChangeController, AutoLaneChangeMode
from openpilot.sunnypilot.selfdrive.controls.lib.lane_turn_desire import LaneTurnController

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection
TurnDirection = custom.ModelDataV2SP.TurnDirection

LANE_CHANGE_SPEED_MIN = 20 * CV.MPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.
LANE_CHANGE_START_TIME = 0.5

# FORK(HONDA_ACCORD_9G_AU): A NUDGE MUST BE FIRM, OR HELD - NOT BRUSHED.
#
# This car's STEER_THRESHOLD is 600 (values.py), lowered from 1200 because weak
# but deliberate nudges at cruise peak around 900 with the gateway's assist in
# the wheel. That is right for steeringPressed - driver monitoring and the
# lateral controller should see a light hand - but as a lane-change trigger it
# confirms on ONE model frame: route fc t=768.8 fired on a single 10 ms reading
# of 645 counts, and the driver reported that just touching the wheel with the
# blinker on starts a lane change.
#
# Over 115 blinker windows on routes d9..fd, the pushes that confirmed a lane
# change split two ways: firm tugs that are often brief (3765-4773 counts for
# only 50-100 ms) and light pushes that are held. The brushes are the ones
# that are BOTH light and brief. So:
#   * a push of NUDGE_FIRM or more in the wanted direction confirms at once,
#     exactly as before;
#   * a lighter one (still over the car's steeringPressed threshold) must be
#     present on NUDGE_HOLD_FRAMES consecutive model frames - 150 ms or more.
# Replayed over those windows: of 66 lane changes the old rule confirmed, 29
# confirm at the same instant, 29 50-150 ms later, 4 later still (weak pushes
# hovering at the threshold), and 3 not at all - peaks of 693, 740 and 903
# held for one or two frames.
#
# Counts are this car's STEER_TORQUE_SENSOR units, or the gateway's mirror of
# the same quantity scaled to them, so the table is per fingerprint and every
# car not in it keeps the upstream single-frame behaviour.
NUDGE_FIRM = {
  "HONDA_ACCORD_9G_AU": 1500.,
}
NUDGE_HOLD_FRAMES = 4

TURN_DESIRES = {
  TurnDirection.none: log.Desire.none,
  TurnDirection.turnLeft: log.Desire.turnLeft,
  TurnDirection.turnRight: log.Desire.turnRight,
}

class DesireHelper:
  def __init__(self, car_fingerprint: str = ""):
    # None keeps the upstream behaviour: any steeringPressed in the wanted
    # direction confirms at once. See NUDGE_FIRM.
    self.nudge_firm = NUDGE_FIRM.get(car_fingerprint)
    self.nudge_frames = 0
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.Desire.none
    self.alc = AutoLaneChangeController(self)
    self.lane_turn_controller = LaneTurnController(self)
    self.lane_turn_direction = TurnDirection.none

  @staticmethod
  def get_lane_change_direction(CS):
    return LaneChangeDirection.left if CS.leftBlinker else LaneChangeDirection.right

  # FORK(LKAS-GATEWAY): driver_torque_stale is last and passed by keyword; see torque_applied below.
  def update(self, carstate, lateral_active, lane_change_prob, left_edge_detected=False, right_edge_detected=False,
             driver_torque_stale=False):
    self.alc.update_params()
    self.lane_turn_controller.update_params()
    v_ego = carstate.vEgo
    one_blinker = carstate.leftBlinker != carstate.rightBlinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    # Lane turn controller update
    self.lane_turn_controller.update_lane_turn(blindspot_left=carstate.leftBlindspot, blindspot_right=carstate.rightBlindspot,
                                               left_blinker=carstate.leftBlinker, right_blinker=carstate.rightBlinker, v_ego=v_ego)
    self.lane_turn_direction = self.lane_turn_controller.get_turn_direction()

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX or self.alc.lane_change_set_timer == AutoLaneChangeMode.OFF:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
    else:
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        self.nudge_frames = 0  # FORK(HONDA_ACCORD_9G_AU): NUDGE_FIRM hold count starts afresh
        # Initialize lane change direction to prevent UI alert flicker
        self.lane_change_direction = self.get_lane_change_direction(carstate)

      elif self.lane_change_state == LaneChangeState.preLaneChange:
        # Update lane change direction
        self.lane_change_direction = self.get_lane_change_direction(carstate)

        # A latched steeringTorque cannot confirm anything. On HONDA_ELESYS the EPS stops
        # updating it while it is under LKAS control, so the reading is the driver's torque
        # from the moment the gateway engaged and it never changes again. Honouring it makes
        # every lane change in whichever direction that stale value happens to point fire on
        # the first frame of preLaneChange, and every lane change the other way impossible.
        # Measured on routes dd/de/df: 14 of 17 confirmations landed in a single 0.05 s
        # sample, and all 10 failures were the direction the latched sign opposed.
        torque_applied = carstate.steeringPressed and not driver_torque_stale and \
                         ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                          (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right))

        # FORK(HONDA_ACCORD_9G_AU): NUDGE_FIRM - firm confirms at once, light has to be held.
        self.nudge_frames = self.nudge_frames + 1 if torque_applied else 0
        if torque_applied and self.nudge_firm is not None:
          torque_applied = abs(carstate.steeringTorque) >= self.nudge_firm or self.nudge_frames >= NUDGE_HOLD_FRAMES

        blindspot_detected = (((carstate.leftBlindspot or left_edge_detected) and self.lane_change_direction == LaneChangeDirection.left) or
                              ((carstate.rightBlindspot or right_edge_detected) and self.lane_change_direction == LaneChangeDirection.right))

        self.alc.update_lane_change(blindspot_detected, carstate.brakePressed)

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif (torque_applied or self.alc.auto_lane_change_allowed) and not blindspot_detected:
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0

      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_timer += DT_MDL

        if lane_change_prob < 0.02 and self.lane_change_timer >= LANE_CHANGE_START_TIME:
          self.lane_change_timer = 0.0
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
            self.lane_change_direction = self.get_lane_change_direction(carstate)
          else:
            self.lane_change_state = LaneChangeState.off
            self.lane_change_direction = LaneChangeDirection.none

    self.prev_one_blinker = one_blinker and lateral_active

    if self.lane_turn_direction != TurnDirection.none:
      self.desire = TURN_DESIRES[self.lane_turn_direction]
    else:
      self.desire = log.Desire.none
      if self.lane_change_state == LaneChangeState.laneChangeStarting:
        if self.lane_change_direction == LaneChangeDirection.left:
          self.desire = log.Desire.laneChangeLeft
        elif self.lane_change_direction == LaneChangeDirection.right:
          self.desire = log.Desire.laneChangeRight

    self.alc.update_state()
