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
# FORK(HUD): a refused nudge re-arms once the wheel has been let go for this many model frames (0.2 s, as a light push
# must be held): route 120 t 487.7-488.4 is one push into a road edge with a single 50 ms dip under steeringPressed
NUDGE_RELEASE_FRAMES = 4
# FORK(HUD): and nudge_refused rises (the chime and the tile's flash) at most once in this many model frames (2 s): a hand
# that wobbles or pulses on the wheel must not chime on every wobble - route d8 t 452.0-457.3 chimed five times in
# 5.3 s, two of them 0.4 s apart. A nudge inside it is refused as ever, silently, and must be let go before the next
# one counts
NUDGE_REFUSED_GAP_FRAMES = int(2.0 / DT_MDL)

# FORK(HONDA_ACCORD_9G_AU): NO LANE CHANGE STARTS BELOW THE EPS'S STEERING FLOOR.
#
# This car steers through the LKAS gateway board, and the EPS will not take the
# board's torque below about 51 km/h: the board asks from 52.00 km/h and lets go
# under 51.00 (GW_EPS_ACK_CPH / GW_EPS_KEEP_CPH in the board's gw_active.c; the
# EPS itself drops out at 50.2-50.8). A lane change started below that is the
# driver's hands alone, and it is a single desire pulse to the model, which goes
# on with the maneuver once the car is back over the floor and the board has the
# wheel again - after an intersection turn as much as after a real lane change.
# On routes 10f-152, 66 of 222 lane changes started below 52 km/h, half of them
# with the wheel past 90 degrees within 8 s (turns), and 6 started below 50 were
# still running above 52.5. Replayed over 67 routes, this takes 174 of 438 lane
# change starts: 171 started below 52 km/h, 3 were a second change in the same
# blinker hold after one of them; every other change is armed and started on
# the same frame as before but one, nudged at 51.8 km/h, which starts 0.45 s
# later at 52.0.
#
# So, per fingerprint, upstream's LANE_CHANGE_SPEED_MIN rule moves up to this
# floor, with the board's 1 km/h of hysteresis:
#   * the blinker arms a lane change (preLaneChange) only at or above the floor,
#     and a nudge or the auto lane change starts one only there;
#   * a waiting change is dropped, as upstream drops it under its minimum, only
#     once the car is LANE_CHANGE_FLOOR_HYST below the floor; in between it
#     waits, so a dip to 51.9 km/h does not cancel the blinker;
#   * a change already running finishes, as upstream's does under its minimum:
#     the model had its one pulse when the change started and finishes it
#     whatever this state says, so ending the state would only hide a lane
#     change that is still happening.
# As upstream, only the blinker's rising edge arms a change: one switched on
# below the floor, or still on after its waiting change was dropped under the
# hysteresis, arms nothing until it is switched on again at the floor. In the
# band a nudge is not a refused one (no chime, no tile); it waits for the floor.
# Both are left for the owner (CAR doc 10.2 rule 3 has the replayed
# alternatives). Every car not in the table keeps LANE_CHANGE_SPEED_MIN exactly.
LANE_CHANGE_SPEED_FLOOR = {
  "HONDA_ACCORD_9G_AU": 52. * CV.KPH_TO_MS,
}
LANE_CHANGE_FLOOR_HYST = 1. * CV.KPH_TO_MS

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
    # FORK(HONDA_ACCORD_9G_AU): where a lane change may start, and under which a waiting one is dropped; both are
    # upstream's LANE_CHANGE_SPEED_MIN on a car without a LANE_CHANGE_SPEED_FLOOR
    floor = LANE_CHANGE_SPEED_FLOOR.get(car_fingerprint)
    self.lane_change_speed_min = LANE_CHANGE_SPEED_MIN if floor is None else max(floor, LANE_CHANGE_SPEED_MIN)
    self.lane_change_speed_drop = LANE_CHANGE_SPEED_MIN if floor is None else max(floor - LANE_CHANGE_FLOOR_HYST, LANE_CHANGE_SPEED_MIN)
    # FORK(HUD): a nudge refused by a block, for the comma 4's lane-block tile and its chime (see update())
    self.nudge_refused = False
    self.refusal_armed = True
    self.release_frames = NUDGE_RELEASE_FRAMES
    self.refusal_announced = False                   # the nudge being refused raised nudge_refused (outside the gap)
    self.refusal_gap = NUDGE_REFUSED_GAP_FRAMES      # model frames since nudge_refused last rose
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
    below_lane_change_speed = v_ego < self.lane_change_speed_min  # FORK(HONDA_ACCORD_9G_AU): LANE_CHANGE_SPEED_FLOOR
    below_lane_change_drop_speed = v_ego < self.lane_change_speed_drop  # FORK(HONDA_ACCORD_9G_AU): its hysteresis

    # Lane turn controller update
    self.lane_turn_controller.update_lane_turn(blindspot_left=carstate.leftBlindspot, blindspot_right=carstate.rightBlindspot,
                                               left_blinker=carstate.leftBlinker, right_blinker=carstate.rightBlinker, v_ego=v_ego)
    self.lane_turn_direction = self.lane_turn_controller.get_turn_direction()
    self.nudge_refused = False  # FORK(HUD): only ever set in preLaneChange, below
    self.refusal_gap = min(self.refusal_gap + 1, NUDGE_REFUSED_GAP_FRAMES)  # FORK(HUD): every frame, whatever the state

    if not lateral_active or self.lane_change_timer > LANE_CHANGE_TIME_MAX or self.alc.lane_change_set_timer == AutoLaneChangeMode.OFF:
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.lane_change_timer = 0.0
    else:
      if self.lane_change_state == LaneChangeState.off and one_blinker and not self.prev_one_blinker and not below_lane_change_speed:
        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_timer = 0.0
        self.nudge_frames = 0  # FORK(HONDA_ACCORD_9G_AU): NUDGE_FIRM hold count starts afresh
        self.refusal_armed, self.release_frames = True, NUDGE_RELEASE_FRAMES  # FORK(HUD): a new blinker, a new request
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

        # FORK(HUD): NUDGE REFUSED - a nudge that would have started the change (torque_applied, after the stale guard
        # and NUDGE_FIRM) but for the block. It holds from that frame until the wheel has been let go (nudge_frames: the
        # raw, non-stale torque toward the blinker) for NUDGE_RELEASE_FRAMES, so one nudge is one refusal however its
        # torque wavers round the thresholds or the block flickers. A stale torque is no release: the frames are not
        # counted, so a push the board fell silent under is still that push when it comes back. A refusal inside
        # NUDGE_REFUSED_GAP_FRAMES of the last one that rose is not announced. Read by modeld into modelDataV2SP.
        if self.nudge_frames > 0:
          self.release_frames = 0
        elif not driver_torque_stale:
          self.release_frames += 1
        if self.release_frames >= NUDGE_RELEASE_FRAMES:
          self.refusal_armed = True
        elif torque_applied and blindspot_detected and self.refusal_armed:
          self.refusal_armed = False
          self.refusal_announced = self.refusal_gap >= NUDGE_REFUSED_GAP_FRAMES
          if self.refusal_announced:
            self.refusal_gap = 0

        self.alc.update_lane_change(blindspot_detected, carstate.brakePressed)

        # FORK(HONDA_ACCORD_9G_AU): dropped under the floor's hysteresis, started only at the floor (on every other car
        # both are upstream's below_lane_change_speed, and the start's extra test is then always true)
        if not one_blinker or below_lane_change_drop_speed:
          self.lane_change_state = LaneChangeState.off
          self.lane_change_direction = LaneChangeDirection.none
          self.lane_change_timer = 0.0
        elif (torque_applied or self.alc.auto_lane_change_allowed) and not blindspot_detected and not below_lane_change_speed:
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.lane_change_timer = 0.0

        # FORK(HUD): only while the change is still waiting; a change that starts or ends ends the refusal with it
        if self.lane_change_state != LaneChangeState.preLaneChange:
          self.refusal_armed, self.release_frames = True, NUDGE_RELEASE_FRAMES
        self.nudge_refused = not self.refusal_armed and self.refusal_announced

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
