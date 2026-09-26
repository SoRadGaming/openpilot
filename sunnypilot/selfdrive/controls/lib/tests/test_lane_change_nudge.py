"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

A lane-change nudge must be firm, or held - not brushed (NUDGE_FIRM in
selfdrive/controls/lib/desire_helper.py). Route fc t=768.8 confirmed a lane
change on a single 10 ms reading of 645 counts; the driver reported that just
touching the wheel with the blinker on started one.
"""
from types import SimpleNamespace

from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState, LaneChangeDirection, \
  NUDGE_FIRM, NUDGE_HOLD_FRAMES

CAR = "HONDA_ACCORD_9G_AU"


def _cs(torque: float, left: bool = True):
  """Car state for one model frame, blinker on, above lane-change speed."""
  return SimpleNamespace(vEgo=25.0, leftBlinker=left, rightBlinker=not left,
                         leftBlindspot=False, rightBlindspot=False, brakePressed=False,
                         steeringTorque=torque, steeringPressed=abs(torque) > 600)


def _armed(fingerprint: str = "", left: bool = True) -> DesireHelper:
  """A helper already in preLaneChange, as after the blinker frame."""
  dh = DesireHelper(fingerprint)
  dh.update(_cs(0, left), True, 1.0)
  assert dh.lane_change_state == LaneChangeState.preLaneChange
  return dh


def _frames_to_confirm(dh: DesireHelper, torques: list[float], left: bool = True) -> int | None:
  for n, t in enumerate(torques):
    dh.update(_cs(t, left), True, 1.0)
    if dh.lane_change_state == LaneChangeState.laneChangeStarting:
      return n
  return None


def test_other_cars_keep_the_upstream_single_frame_nudge():
  assert DesireHelper("TOYOTA_RAV4").nudge_firm is None
  assert _frames_to_confirm(_armed(), [645]) == 0


def test_a_brush_does_not_confirm():
  """fc t=768.8: one frame at 645 in the wanted direction."""
  assert _frames_to_confirm(_armed(CAR), [645, 0, 0, 0, 0, 0]) is None


def test_a_light_push_confirms_once_held():
  frames = [700] * (NUDGE_HOLD_FRAMES + 2)
  assert _frames_to_confirm(_armed(CAR), frames) == NUDGE_HOLD_FRAMES - 1


def test_a_light_push_that_lets_go_starts_the_count_again():
  n = NUDGE_HOLD_FRAMES - 1
  frames = [700] * n + [0] + [700] * n + [0]
  assert _frames_to_confirm(_armed(CAR), frames) is None


def test_a_firm_tug_confirms_at_once():
  """The firm pushes that confirmed lane changes on d9..fd were often brief:
  3765-4773 counts for 50-100 ms. They must not wait."""
  firm = NUDGE_FIRM[CAR]
  assert _frames_to_confirm(_armed(CAR), [firm]) == 0
  assert _frames_to_confirm(_armed(CAR), [4773, 0]) == 0


def test_direction_still_matters():
  # right-hand torque while signalling left: never, however firm or long
  assert _frames_to_confirm(_armed(CAR, left=True), [-4000] * 10, left=True) is None
  assert _frames_to_confirm(_armed(CAR, left=False), [-4000], left=False) == 0


def test_a_stale_torque_still_confirms_nothing():
  dh = _armed(CAR)
  for _ in range(NUDGE_HOLD_FRAMES + 2):
    dh.update(_cs(4000), True, 1.0, driver_torque_stale=True)
  assert dh.lane_change_state == LaneChangeState.preLaneChange


def test_the_count_restarts_on_a_new_blinker():
  dh = _armed(CAR)
  _frames_to_confirm(dh, [700] * (NUDGE_HOLD_FRAMES - 1))
  dh.update(SimpleNamespace(**{**vars(_cs(0)), "leftBlinker": False}), True, 1.0)   # blinker off
  assert dh.lane_change_state == LaneChangeState.off
  dh.update(_cs(0), True, 1.0)                                                       # blinker on again
  assert dh.lane_change_direction == LaneChangeDirection.left
  assert _frames_to_confirm(dh, [700]) is None, "a held count must not survive a new blinker"
