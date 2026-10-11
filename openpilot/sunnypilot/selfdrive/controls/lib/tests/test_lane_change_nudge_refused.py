"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): DesireHelper.nudge_refused, the comma 4 lane-block tile's chime (openpilot/selfdrive/controls/lib/
desire_helper.py). A nudge toward the blinker that would have started the change but for a block - a road edge or a
car in the blind spot - is refused; selfdrived chimes on the rising edge of the flag, so it must rise exactly once per
nudge, stay up while that nudge lasts, re-arm only after the wheel has been let go, and never rise for a change that
is allowed, without a block, or from a stale gateway torque.
"""
from types import SimpleNamespace

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState, NUDGE_FIRM, NUDGE_HOLD_FRAMES, \
  NUDGE_RELEASE_FRAMES

CAR = "HONDA_ACCORD_9G_AU"
FIRM = NUDGE_FIRM[CAR]


def _cs(torque: float = 0.0, left: bool = True, blindspot: bool = False, blinker: bool = True):
  """Car state for one model frame, above lane-change speed. Positive torque is toward the left."""
  return SimpleNamespace(vEgo=25.0, leftBlinker=blinker and left, rightBlinker=blinker and not left,
                         leftBlindspot=blindspot and left, rightBlindspot=blindspot and not left, brakePressed=False,
                         steeringTorque=torque, steeringPressed=abs(torque) > 600)


class Drive:
  """One DesireHelper fed model frames; records nudge_refused per frame."""
  def __init__(self, fingerprint: str = CAR, left: bool = True):
    self.dh = DesireHelper(fingerprint)
    self.left = left
    self.trace: list[bool] = []
    self.step(0.0, edge=False)
    assert self.dh.lane_change_state == LaneChangeState.preLaneChange

  def step(self, torque: float, edge: bool = True, blindspot: bool = False, stale: bool = False, blinker: bool = True):
    toward = torque if self.left else -torque
    self.dh.update(_cs(toward, self.left, blindspot, blinker), True, 1.0,
                   left_edge_detected=edge and self.left, right_edge_detected=edge and not self.left,
                   driver_torque_stale=stale)
    self.trace.append(self.dh.nudge_refused)
    return self.dh.nudge_refused

  def run(self, torques, **kw):
    for t in torques:
      self.step(t, **kw)
    return self

  def rises(self) -> int:
    return sum(1 for a, b in zip([False] + self.trace, self.trace, strict=False) if b and not a)


class TestNudgeRefused(OpenpilotTestCase):
  def test_a_firm_nudge_into_a_road_edge_is_refused_once(self):
    for left in (True, False):
      d = Drive(left=left).run([0, 0, FIRM, FIRM, FIRM, 0, 0])
      assert d.dh.lane_change_state == LaneChangeState.preLaneChange, "the refusal itself is unchanged"
      assert d.trace[3] and d.rises() == 1, d.trace

  def test_a_car_in_the_blind_spot_too(self):
    d = Drive().run([0, FIRM, FIRM], edge=False, blindspot=True)
    assert d.rises() == 1 and d.dh.lane_change_state == LaneChangeState.preLaneChange

  def test_held_is_one_refusal_not_one_per_frame(self):
    d = Drive().run([FIRM] * 40)
    assert d.rises() == 1 and all(d.trace[2:]), "up for as long as the nudge is held"

  def test_a_light_push_is_refused_when_it_would_have_confirmed(self):
    """NUDGE_FIRM: a light push confirms once held NUDGE_HOLD_FRAMES - and is refused on that same frame, not before."""
    d = Drive().run([700] * (NUDGE_HOLD_FRAMES + 3))
    first = d.trace.index(True)
    assert first == NUDGE_HOLD_FRAMES, d.trace   # trace[0] is the blinker frame
    assert d.rises() == 1

  def test_a_brush_is_not_a_refusal(self):
    """fc t=768.8: one frame at 645 would not have confirmed, so nothing was refused either."""
    assert Drive().run([645, 0, 0, 0, 0]).rises() == 0

  def test_it_re_arms_after_the_wheel_is_let_go(self):
    rel = [0] * NUDGE_RELEASE_FRAMES
    d = Drive().run([FIRM] * 3 + rel + [FIRM] * 3 + rel + [FIRM] * 3)
    assert d.rises() == 3, d.trace
    assert not any(d.trace[1 + 3 + NUDGE_RELEASE_FRAMES - 1:1 + 3 + NUDGE_RELEASE_FRAMES]), "down once released"

  def test_a_dip_shorter_than_the_release_is_the_same_nudge(self):
    """Route 120 t 487.7-488.4: one push into the road edge with one 50 ms reading under steeringPressed."""
    torques = [-774, -903, -1032, -903, -774, -774, -516, -774, -903, -2709, -4902, -3870, -2709, -1806, 258]
    d = Drive(left=False).run([-t for t in torques])   # Drive takes torque toward the blinker
    assert d.rises() == 1, d.trace

  def test_never_when_the_change_is_allowed(self):
    d = Drive().run([0, FIRM, FIRM], edge=False)
    assert d.rises() == 0 and d.dh.lane_change_state == LaneChangeState.laneChangeStarting

  def test_never_without_a_block_or_a_nudge(self):
    assert Drive().run([0] * 30).rises() == 0, "a block alone is silent"
    assert Drive().run([-FIRM] * 10).rises() == 0, "torque away from the blinker is no nudge"

  def test_a_stale_gateway_torque_refuses_nothing(self):
    """HONDA_ELESYS latches steeringTorque under LKAS control: a stale reading is no nudge, so no refusal (and no chime)."""
    d = Drive().run([FIRM] * 20, stale=True)
    assert d.rises() == 0
    d.run([FIRM] * 3)   # the reading is live again: a real nudge
    assert d.rises() == 1

  def test_the_block_clearing_mid_nudge_starts_the_change_and_ends_the_refusal(self):
    d = Drive().run([FIRM] * 3)
    assert d.trace[-1]
    d.step(FIRM, edge=False)
    assert d.dh.lane_change_state == LaneChangeState.laneChangeStarting and not d.trace[-1]

  def test_a_new_blinker_is_a_new_request(self):
    d = Drive().run([FIRM] * 3)
    d.step(FIRM, blinker=False)
    assert d.dh.lane_change_state == LaneChangeState.off and not d.trace[-1]
    d.run([FIRM] * 3)   # blinker on again, still pushing into the edge
    assert d.rises() == 2

  def test_a_flickering_block_under_a_held_nudge_is_one_refusal(self):
    """A firm tug, refused, easing to a light push while the block drops for a frame: still under NUDGE_HOLD_FRAMES, so it
    cannot start the change - and the same push must not chime again when the block comes back."""
    d = Drive().run([FIRM])
    assert d.rises() == 1
    d.step(700, edge=False)
    assert d.dh.lane_change_state == LaneChangeState.preLaneChange and d.trace[-1]
    d.run([700] * 4)
    assert d.rises() == 1, d.trace

  def test_other_cars_refuse_on_the_upstream_single_frame_nudge(self):
    d = Drive(fingerprint="TOYOTA_RAV4").run([0, 700, 700, 0])
    assert d.trace[2] and d.rises() == 1
