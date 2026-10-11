"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): DesireHelper.nudge_refused, the comma 4 lane-block tile's chime (openpilot/selfdrive/controls/lib/
desire_helper.py). A nudge toward the blinker that would have started the change but for a block - a road edge or a
car in the blind spot - is refused; selfdrived chimes and the tile flashes on the rising edge of the flag, so it must
rise at most once per nudge, stay up while that nudge lasts, re-arm only after the wheel has been let go, rise at most
once in NUDGE_REFUSED_GAP_FRAMES (2 s), and never rise for a change that is allowed, without a block, or from a stale
gateway torque.
"""
from types import SimpleNamespace

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.desire_helper import DesireHelper, LaneChangeState, NUDGE_FIRM, NUDGE_HOLD_FRAMES, \
  NUDGE_RELEASE_FRAMES, NUDGE_REFUSED_GAP_FRAMES

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

  def rise_frames(self) -> list[int]:
    return rise_frames(self.trace)

  def rises(self) -> int:
    return len(self.rise_frames())


def rise_frames(trace) -> list[int]:
  return [i for i, (a, b) in enumerate(zip([False] + trace, trace, strict=False)) if b and not a]


# Route d8 t 452.0-457.3 (the reviewer's replay, RELC forced on; right blinker, 59.8 km/h, the road edge up throughout):
# per model frame the torque toward the blinker and carState.steeringPressed. Five rises without the gap, two of them
# 0.4 s apart from one wobble (2119, 1215, 333, 456, -626, 495, 2129).
D8_TOWARD = [139] * 27 + [2728, 5006, 2810, 442, -1198, -1001, -505, -306, -180, -66, -4, 35, 44, 71, 32, 55, 93, 28, 49] + \
  [67] * 16 + [-1925, -2239, -1706, -1533, -2147, -1832, -1333, -183, 1327, 1490, 1922, 1933, 1533, 1627, 1229, 1299, 1615,
               1946, 1821, 1572, 694, -247, -248, -25, -185, -177, -313, -261, -251, -266, -225, -280, 406, 1890, 2384, 2119,
               1215, 333, 456, -626, 495, 2129, 3078, 2638, 2733, 3275, 3182, 3492, 3468, 3633, 3579, 3494, 3176, 2944, 2510,
               2490, 2632, 2687, 2897, 3103, 3468, 3794, 3610, 4231, 4305, 3513, 2302, 1082, 62, -417, 491, 2058, 3148, 3532]
D8_PRESSED = [False] * 27 + [True] * 3 + [False] * 32 + [True] * 7 + [False] + [True] * 12 + [False] * 13 + [True] * 4 + \
  [False] * 4 + [True] * 26 + [False] * 4 + [True] * 3


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
    rel = [0] * NUDGE_REFUSED_GAP_FRAMES
    d = Drive().run([FIRM] * 3 + rel + [FIRM] * 3 + rel + [FIRM] * 3)
    assert d.rises() == 3, d.trace
    assert not any(d.trace[1 + 3 + NUDGE_RELEASE_FRAMES - 1:1 + 3 + NUDGE_RELEASE_FRAMES]), "down once released"

  def test_at_most_one_rise_in_2_s(self):
    """A nudge inside the gap is refused silently and has to be let go: holding it past the gap does not chime."""
    rel = [0] * NUDGE_RELEASE_FRAMES
    d = Drive().run([FIRM] * 3 + rel + [FIRM] * 3 + rel + [FIRM] * NUDGE_REFUSED_GAP_FRAMES)
    assert d.rises() == 1, d.trace
    assert d.dh.lane_change_state == LaneChangeState.preLaneChange, "every one of them refused"
    d.run(rel + [FIRM] * 3)
    assert d.rises() == 2, "let go and pushed again after the gap"

  def test_a_pulsing_hand_chimes_once_in_2_s(self):
    """The resting hand: 700 toward the blinker for 0.2 s, 300 for 0.2 s, for 10 s - 25 chimes without the gap."""
    r = Drive().run(([700] * 4 + [300] * 4) * 25).rise_frames()
    assert 1 <= len(r) <= 5 and all(b - a >= NUDGE_REFUSED_GAP_FRAMES for a, b in zip(r, r[1:], strict=False)), r
    r = Drive().run(([FIRM] * 2 + [0] * 4) * 10).rise_frames()
    assert len(r) == 2 and r[1] - r[0] >= NUDGE_REFUSED_GAP_FRAMES, f"firm 0.1 s tugs every 0.3 s for 3 s: {r}"

  def test_route_d8_wobble(self):
    """The logged wobble: at most one rise in 2 s, and every nudge still refused."""
    assert len(D8_TOWARD) == len(D8_PRESSED) == 136

    def cs(toward=0, pressed=False):
      return SimpleNamespace(vEgo=16.6, leftBlinker=False, rightBlinker=True, leftBlindspot=False, rightBlindspot=False,
                             brakePressed=False, steeringTorque=-toward, steeringPressed=pressed)
    dh = DesireHelper(CAR)
    dh.update(cs(), True, 1.0)
    trace, states = [], set()
    for toward, pressed in zip(D8_TOWARD, D8_PRESSED, strict=True):
      dh.update(cs(toward, pressed), True, 1.0, right_edge_detected=True)
      trace.append(dh.nudge_refused)
      states.add(dh.lane_change_state)
    r = rise_frames(trace)
    assert len(r) == 3 and all(b - a >= NUDGE_REFUSED_GAP_FRAMES for a, b in zip(r, r[1:], strict=False)), r
    assert states == {LaneChangeState.preLaneChange}, "the edge refused every one of them"

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

  def test_a_push_the_board_fell_silent_under_is_the_same_push(self):
    """A held push whose torque goes stale for 0.25 s and then comes back live: one refusal, not two."""
    d = Drive().run([FIRM] * (NUDGE_REFUSED_GAP_FRAMES + 5))   # past the gap: only the release rule keeps it one
    d.run([FIRM] * 5, stale=True)
    d.run([FIRM] * 10)
    assert d.rises() == 1, d.trace

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
    d.run([FIRM] * 3)   # blinker on again at once, still pushing into the edge: inside the 2 s, refused silently
    assert d.rises() == 1 and d.dh.lane_change_state == LaneChangeState.preLaneChange
    d.run([0] * NUDGE_REFUSED_GAP_FRAMES, blinker=False)
    d.run([FIRM] * 3)   # and again after 2 s
    assert d.rises() == 2, d.trace

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
