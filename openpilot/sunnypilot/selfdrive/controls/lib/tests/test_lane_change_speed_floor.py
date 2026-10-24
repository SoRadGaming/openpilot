"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): no lane change starts below the EPS's steering floor (LANE_CHANGE_SPEED_FLOOR in
openpilot/selfdrive/controls/lib/desire_helper.py). The board steers from 52 km/h and lets go under 51, so on this car
upstream's LANE_CHANGE_SPEED_MIN rule moves up to 52 km/h, with a waiting change dropped only under 51: a blinker
below the floor arms nothing, a nudge (or the auto lane change) starts nothing below it, and a change already running
finishes, as upstream's does below its minimum. Every other car keeps upstream's 20 mph.
"""
import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openpilot.cereal import log
from openpilot.common.constants import CV
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState, LaneChangeDirection, \
  LANE_CHANGE_SPEED_MIN, LANE_CHANGE_SPEED_FLOOR, LANE_CHANGE_FLOOR_HYST, LANE_CHANGE_START_TIME, LANE_CHANGE_TIME_MAX, \
  NUDGE_FIRM
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_change import AutoLaneChangeMode
from openpilot.common.realtime import DT_MDL

CAR = "HONDA_ACCORD_9G_AU"
OTHER = "TOYOTA_RAV4"
FIRM = NUDGE_FIRM[CAR]
FLOOR_KPH = 52.0
DROP_KPH = 51.0
MIN_KPH = LANE_CHANGE_SPEED_MIN * CV.MS_TO_KPH   # upstream's 20 mph, 32.19 km/h
OFF, PRE, STARTING = LaneChangeState.off, LaneChangeState.preLaneChange, LaneChangeState.laneChangeStarting


def _cs(kph: float, blinker: bool = True, torque: float = 0.0, left: bool = True, blindspot: bool = False,
        ms: float | None = None):
  """Car state for one model frame, at kph (or exactly ms m/s). Positive torque is toward the left."""
  return SimpleNamespace(vEgo=kph * CV.KPH_TO_MS if ms is None else ms, leftBlinker=blinker and left, rightBlinker=blinker and not left,
                         leftBlindspot=blindspot and left, rightBlindspot=blindspot and not left, brakePressed=False,
                         steeringTorque=torque, steeringPressed=abs(torque) > 600)


class Drive:
  """One DesireHelper fed model frames; records the lane-change state, the desire and nudge_refused per frame."""
  def __init__(self, fingerprint: str = CAR, left: bool = True):
    self.dh = DesireHelper(fingerprint)
    self.left = left
    self.states: list = []
    self.desires: list = []
    self.refused: list = []

  def step(self, kph: float, blinker: bool = True, nudge: float = 0.0, prob: float = 1.0, lat: bool = True, n: int = 1,
           ms: float | None = None):
    """nudge is torque toward the blinker's side; ms, if given, is the exact vEgo instead of kph."""
    for _ in range(n):
      self.dh.update(_cs(kph, blinker, nudge if self.left else -nudge, self.left, ms=ms), lat, prob)
      self.states.append(self.dh.lane_change_state)
      self.desires.append(self.dh.desire)
      self.refused.append(self.dh.nudge_refused)
    return self

  @property
  def state(self):
    return self.dh.lane_change_state


class TestLaneChangeSpeedFloor(OpenpilotTestCase):
  def test_the_floor_is_the_boards_and_only_on_honda_elesys(self):
    from opendbc.car.honda.values import HONDA_ELESYS
    assert set(LANE_CHANGE_SPEED_FLOOR) == {str(c) for c in HONDA_ELESYS} == {CAR}
    # the board asks from GW_EPS_ACK_CPH 5200 (52.00 km/h) and keeps the EPS down to GW_EPS_KEEP_CPH 5100 (51.00 km/h)
    assert abs(LANE_CHANGE_SPEED_FLOOR[CAR] * CV.MS_TO_KPH - FLOOR_KPH) < 1e-9
    assert abs(LANE_CHANGE_FLOOR_HYST * CV.MS_TO_KPH - (FLOOR_KPH - DROP_KPH)) < 1e-9
    dh = DesireHelper(CAR)
    assert abs(dh.lane_change_speed_min * CV.MS_TO_KPH - FLOOR_KPH) < 1e-9
    assert abs(dh.lane_change_speed_drop * CV.MS_TO_KPH - DROP_KPH) < 1e-9

  def test_other_cars_keep_upstreams_minimum(self):
    for fp in ("", OTHER, "HONDA_CIVIC"):
      dh = DesireHelper(fp)
      assert dh.lane_change_speed_min == dh.lane_change_speed_drop == LANE_CHANGE_SPEED_MIN, fp
    # just above 20 mph a blinker arms and a nudge starts; just below, nothing; and a waiting change is dropped at once
    assert Drive(OTHER).step(MIN_KPH + 0.1).step(MIN_KPH + 0.1, nudge=FIRM).state == STARTING
    assert Drive(OTHER).step(MIN_KPH - 0.1).step(MIN_KPH - 0.1, nudge=FIRM, n=20).state == OFF
    d = Drive(OTHER).step(MIN_KPH + 0.1)
    assert d.state == PRE
    assert d.step(MIN_KPH - 0.01).state == OFF, "no hysteresis on a car without the floor"
    # and at 45 km/h, which is under this car's floor, another car changes lanes as before
    assert Drive(OTHER).step(45).step(45, nudge=FIRM).state == STARTING

  def test_a_blinker_below_the_floor_arms_nothing(self):
    for kph in (FLOOR_KPH - 0.01, 51.5, DROP_KPH, 45, 33):
      d = Drive().step(kph).step(kph, nudge=FIRM, n=40)
      assert set(d.states) == {OFF}, kph
      assert set(d.desires) == {log.Desire.none}, kph
      assert d.dh.lane_change_direction == LaneChangeDirection.none

  def test_at_the_floor(self):
    assert Drive().step(0, ms=DesireHelper(CAR).lane_change_speed_min).state == PRE
    assert Drive().step(0, ms=DesireHelper(CAR).lane_change_speed_min - 1e-6).state == OFF
    d = Drive().step(FLOOR_KPH)
    assert d.state == PRE and d.dh.lane_change_direction == LaneChangeDirection.left
    d.step(FLOOR_KPH, nudge=FIRM)
    assert d.state == STARTING and d.desires[-1] == log.Desire.laneChangeLeft
    d = Drive(left=False).step(FLOOR_KPH).step(FLOOR_KPH, nudge=FIRM)
    assert d.state == STARTING and d.desires[-1] == log.Desire.laneChangeRight

  def test_above_the_floor_nothing_changes(self):
    for kph in (52.5, 60, 90, 110):
      for fp in (CAR, OTHER):
        d = Drive(fp).step(kph)
        assert d.state == PRE, (fp, kph)
        assert d.step(kph, nudge=FIRM).state == STARTING, (fp, kph)

  def test_a_blinker_held_up_through_the_floor_needs_a_new_one(self):
    """As upstream at its minimum: the blinker's rising edge is what arms a change, and below the floor it armed nothing."""
    d = Drive().step(45)
    for i in range(60):   # 3 s from 45 to 63 km/h, the blinker held
      d.step(45 + 0.3 * i)
    assert set(d.states) == {OFF}
    d.step(63, blinker=False).step(63)
    assert d.state == PRE
    assert d.step(63, nudge=FIRM).state == STARTING

  def test_a_waiting_change_survives_a_dip_into_the_band(self):
    d = Drive().step(53)
    assert d.state == PRE
    d.step(51.5, n=40).step(DROP_KPH, n=10).step(0, ms=d.dh.lane_change_speed_drop, n=10).step(51.99, n=10).step(53, n=10)
    assert set(d.states) == {PRE}
    assert d.step(53, nudge=FIRM).state == STARTING

  def test_no_lane_change_starts_inside_the_band(self):
    """A waiting change is kept down to 51 km/h but started only at 52: a nudge in between waits for the floor."""
    d = Drive().step(53).step(51.5, nudge=FIRM, n=10)
    assert set(d.states) == {PRE} and set(d.desires) == {log.Desire.none}
    # and it is not a refused nudge: no chime and no lane-block tile, which are for a blind spot or a road edge only
    # (review of push13: the prompt stays up and the nudge just waits for the floor - the owner's call, CAR doc 10.2)
    assert not any(d.refused)
    d.step(FLOOR_KPH, nudge=FIRM)   # the push still held as the car comes back to the floor
    assert d.state == STARTING
    # a nudge let go inside the band is gone: the change waits for the next one at the floor
    d = Drive().step(53).step(51.5, nudge=FIRM, n=10).step(51.5, n=10).step(FLOOR_KPH, n=10)
    assert set(d.states) == {PRE} and not any(d.refused)
    assert d.step(FLOOR_KPH, nudge=FIRM).state == STARTING

  def test_a_waiting_change_is_dropped_under_the_band(self):
    d = Drive().step(53)
    d.step(0, ms=d.dh.lane_change_speed_drop - 1e-6)
    assert d.state == OFF and d.dh.lane_change_direction == LaneChangeDirection.none
    assert Drive().step(53).step(DROP_KPH - 0.01).state == OFF
    d.step(53, n=20).step(53, nudge=FIRM, n=10)
    assert d.state == OFF, "as upstream, a blinker the speed canceled must be switched on again"
    d.step(53, blinker=False).step(53)
    assert d.state == PRE

  def test_no_chatter_at_the_floor(self):
    # a waiting change with the speed see-sawing across the floor, every frame for 10 s: it just waits
    d = Drive().step(53)
    for i in range(200):
      d.step(51.2 if i % 2 else 52.8)
    assert set(d.states) == {PRE}
    # the same with a held nudge: it starts on the first frame at the floor and stays started
    d = Drive().step(53)
    d.step(51.2, nudge=FIRM)
    for i in range(20):
      d.step(52.8 if i % 2 == 0 else 51.2, nudge=FIRM)
    assert d.states[:2] == [PRE, PRE] and set(d.states[2:]) == {STARTING}
    # and a blinker switched on while the speed see-saws over the drop line, never at the floor, arms nothing
    d = Drive().step(50.8)
    for i in range(100):
      d.step(51.2 if i % 2 else 50.8)
    assert set(d.states) == {OFF}

  def test_a_running_change_finishes_below_the_floor(self):
    """Upstream finishes a change that drops under its minimum; so does this car under the floor."""
    d = Drive().step(56).step(56, nudge=FIRM)
    assert d.state == STARTING
    for i in range(40):   # 2 s, down to 40 km/h, the model still changing lanes
      d.step(56 - 0.4 * i)
    assert set(d.states[1:]) == {STARTING}
    assert set(d.desires[1:]) == {log.Desire.laneChangeLeft}
    d.step(40, prob=0.0)   # the model is done: back to waiting, as the blinker is still on...
    assert d.state == PRE
    d.step(40, prob=0.0)   # ...and that is dropped under the floor, as upstream drops it under its minimum
    assert d.state == OFF
    # the blinker off when the model finishes: straight to off
    d = Drive().step(56).step(56, nudge=FIRM).step(45, n=20).step(45, blinker=False, prob=0.0)
    assert d.state == OFF
    # and LANE_CHANGE_TIME_MAX still ends one the model never finishes
    d = Drive().step(56).step(56, nudge=FIRM).step(45, n=int(LANE_CHANGE_TIME_MAX / DT_MDL) + 2)
    assert d.state == OFF

  def test_a_running_change_is_left_to_finish_exactly_as_upstream(self):
    """The same frames, the speeds raised by the floor's offset over upstream's minimum, give the same states."""
    offset = FLOOR_KPH - MIN_KPH
    rng = random.Random(1234)
    for _ in range(300):
      frames: list[dict[str, Any]] = []
      kph = rng.uniform(MIN_KPH - 6, MIN_KPH + 15)
      for _ in range(rng.randint(5, 120)):
        kph = min(max(kph + rng.uniform(-1.5, 1.5), 5.), 70.)
        if MIN_KPH - (FLOOR_KPH - DROP_KPH) <= kph < MIN_KPH:
          kph = MIN_KPH + 0.05 if rng.random() < 0.5 else MIN_KPH - 1.1   # keep out of the floor's band (it has no twin)
        frames.append({'kph': kph, 'blinker': rng.random() < 0.85, 'nudge': rng.choice([0., 0., FIRM, -FIRM]),
                       'prob': rng.choice([0., 1., 1.]), 'lat': rng.random() < 0.97})
      other, accord = Drive(OTHER), Drive(CAR)
      for f in frames:
        other.step(**f)
        accord.step(**{**f, 'kph': f['kph'] + offset})
      assert accord.states == other.states, frames
      assert accord.desires == other.desires

  def test_the_auto_lane_change_waits_for_the_floor_too(self):
    for kph_wait, starts in ((51.5, False), (45, False), (53, True)):
      d = Drive()
      d.dh.alc.update_params = lambda: None  # ty: ignore[invalid-assignment]  # the test sets the mode itself
      d.dh.alc.lane_change_set_timer = AutoLaneChangeMode.NUDGELESS
      d.step(53 if kph_wait == 51.5 else kph_wait).step(kph_wait, n=40)   # 2 s, well past the 0.05 s timer
      assert (d.state == STARTING) == starts, kph_wait
      if kph_wait == 51.5:
        assert d.state == PRE
        assert d.step(FLOOR_KPH).state == STARTING, "starts on the first frame back at the floor"

  def test_an_engagement_with_the_blinker_on_follows_the_floor(self):
    """Upstream: a blinker already on when lateral engages arms a change at once - only at or above the floor here."""
    d = Drive().step(45, lat=False, n=5).step(45, n=5)
    assert set(d.states) == {OFF}
    d = Drive().step(60, lat=False, n=5).step(60)
    assert d.state == PRE

  def test_start_and_drop_times_are_the_lane_change_state_machines(self):
    # a change at the floor still spends upstream's LANE_CHANGE_START_TIME in laneChangeStarting
    d = Drive().step(FLOOR_KPH).step(FLOOR_KPH, nudge=FIRM)
    frames = 0
    while d.state == STARTING and frames < 100:
      d.step(FLOOR_KPH, blinker=False, prob=0.0)
      frames += 1
    assert abs(frames * DT_MDL - LANE_CHANGE_START_TIME) <= DT_MDL + 1e-9

  def test_both_modelds_pass_the_fingerprint(self):
    root = Path(__file__).resolve().parents[5]
    for path in ("selfdrive/modeld/modeld.py", "sunnypilot/modeld_v2/modeld.py"):
      assert "DesireHelper(CP.carFingerprint)" in (root / path).read_text(), path
