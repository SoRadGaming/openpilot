"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): a LOWER limit is a prompt, never a silent set-speed rewrite (non-PCM cruise), and a new limit counts
only once it has held for 1 s.

Upstream asks for confirmation only when the new limit is below 80 km/h; anything else changes the set speed by itself
with a 5 s chime alert. On the M4 eastbound that rewrote 110 -> 100 (and 100 -> 90) on every commute - routes c8 747.64,
dd 728.30, fc 812.94, 10f 771.07 - and the driver, believing the set speed was still 110, gassed back up and was braked
again on release (fc 822.0). dd's map limit flipped 110/100/110/100 in 4 s, each flip a rewrite.

These run card (VCruiseHelper, 100 Hz), selfdrived's button tracker and the planner (the change hold, then SLA, 20 Hz)
together on the owner's path, with test_speed_limit_confirm_grace's Rig.
"""
from types import SimpleNamespace
from unittest import mock

from openpilot.cereal import custom
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL, DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import CONFIRM_SPEED_THRESHOLD
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.change_hold import SpeedLimitChangeHold
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SPEED_LIMIT_CHANGED_HOLD_PERIOD, \
  PRE_ACTIVE_GUARD_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.tests.test_speed_limit_confirm_grace import GraceTestBase, \
  Rig, PLANNER_EVERY, MINUS, PLUS
from openpilot.common.test import OpenpilotTestCase

AssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState
ACTIVE = (AssistState.active, AssistState.adapting)


def resolver_out(kph: float, current_kph: float | None = None) -> SimpleNamespace:
  """What SpeedLimitResolver hands plannerd: `kph` is the final (and last) limit, `current_kph` the raw current one."""
  ms = kph * CV.KPH_TO_MS
  cur = ms if current_kph is None else current_kph * CV.KPH_TO_MS
  return SimpleNamespace(speed_limit=cur, speed_limit_last=ms, speed_limit_final=cur, speed_limit_final_last=ms,
                         speed_limit_offset=0.)


class HoldRig(Rig):
  """Rig with plannerd's change hold in front of SLA and card: `raw_kph` is the resolver's limit, and SLA and card see
  what the hold publishes (longitudinal_planner.LongitudinalPlannerSP)."""
  def __init__(self, set_kph: float, limit_kph: float, **kw):
    super().__init__(set_kph, limit_kph, **kw)
    self.raw_kph = limit_kph
    self.hold = SpeedLimitChangeHold()
    self.hold.update(resolver_out(limit_kph))

  def step(self, events=()):
    if self.frame % PLANNER_EVERY == 0:
      self.hold.update(resolver_out(self.raw_kph))
      self.limit_kph = self.final_kph = round(self.hold.speed_limit_final_last * CV.MS_TO_KPH, 6)
    super().step(events)

  def run_until(self, state, seconds: float = 10.) -> float:
    """Run until SLA is in `state`; -> the seconds that took."""
    start = self.frame
    while self.sla.state != state:
      self.step()
      assert self.frame - start < round(seconds / DT_CTRL), f"never {state}: still {self.sla.state}"
    return (self.frame - start) * DT_CTRL

  def tap(self, button) -> None:
    self.step([(button, True)])
    self.run(0.1)
    self.step([(button, False)])
    self.run(1.)


class TestLowerLimitPrompts(GraceTestBase):
  def active_rig(self, mocker, kph: float) -> HoldRig:
    """Engaged with the set speed already at the limit: SLA confirms itself (no prompt) and goes active."""
    rig = HoldRig(kph, kph)
    self.setup_clock(mocker, rig)
    rig.run(2.)
    assert rig.sla.state == AssistState.active
    rig.set_speeds.clear()
    rig.states.clear()
    return rig

  def test_the_hold_is_upstreams_1_s(self):
    assert SPEED_LIMIT_CHANGED_HOLD_PERIOD == 1
    assert SpeedLimitChangeHold.HOLD_FRAMES == round(1. / DT_MDL) == 20

  def test_110_to_100_is_a_prompt_not_a_rewrite(self, mocker):
    """Route fc 812.94: active at 110, the map limit drops to 100. Upstream: set speed 100 on the next card frame."""
    rig = self.active_rig(mocker, 110.)
    rig.raw_kph = 100.
    rig.run(0.9)
    assert rig.sla.state == AssistState.active, "not before the limit has held for 1 s"
    took = rig.run_until(AssistState.preActive)
    assert 0. <= took <= 0.15
    assert rig.sla.speed_limit_final_last_conv == 100 and rig.sla.v_cruise_cluster_conv == 110
    # unanswered, it times out and the set speed is still the driver's
    rig.run(PRE_ACTIVE_GUARD_PERIOD[False] + 0.5)
    assert rig.sla.state == AssistState.inactive
    assert set(rig.set_speeds) == {110.}, sorted(set(rig.set_speeds))
    assert AssistState.adapting not in rig.states

  def test_minus_confirms_the_lower_limit(self, mocker):
    rig = self.active_rig(mocker, 110.)
    rig.raw_kph = 100.
    rig.run_until(AssistState.preActive)
    rig.run(1.)
    rig.tap(MINUS)
    assert rig.sla.state == AssistState.active
    assert set(rig.set_speeds) == {110., 100.}, "the press sets the limit: no 109 on the way"
    assert rig.set_speeds[-1] == 100.

  def test_plus_during_the_prompt_changes_nothing(self, mocker):
    """The wrong way inside a '-' prompt would raise the set speed: blocked, as for every prompt
    (cruise_ext.update_speed_limit_assist_pre_active_raise_blocked)."""
    rig = self.active_rig(mocker, 110.)
    rig.raw_kph = 100.
    rig.run_until(AssistState.preActive)
    rig.tap(PLUS)
    assert rig.sla.state == AssistState.preActive
    assert set(rig.set_speeds) == {110.}

  def test_100_to_90_prompts_too(self, mocker):
    """Every route's second rewrite (c8 1156.65, dd 1091.31, fc 1337.94, 10f 1172.06)."""
    rig = self.active_rig(mocker, 100.)
    rig.raw_kph = 90.
    rig.run_until(AssistState.preActive)
    rig.run(PRE_ACTIVE_GUARD_PERIOD[False] + 0.5)
    assert set(rig.set_speeds) == {100.}

  def test_after_an_unanswered_prompt_the_next_drop_prompts_again(self, mocker):
    rig = self.active_rig(mocker, 110.)
    rig.raw_kph = 100.
    rig.run_until(AssistState.preActive)
    rig.run_until(AssistState.inactive)
    rig.raw_kph = 90.
    rig.run_until(AssistState.preActive, 2.)
    assert rig.sla.speed_limit_final_last_conv == 90
    assert set(rig.set_speeds) == {110.}

  def test_a_higher_limit_still_raises_the_set_speed_by_itself(self, mocker):
    """Route de 2178.68: active at 100, the limit rises to 110. No prompt, as upstream - one second later."""
    rig = self.active_rig(mocker, 100.)
    rig.raw_kph = 110.
    rig.run(0.9)
    assert set(rig.set_speeds) == {100.}, "not before the limit has held for 1 s"
    rig.run(2.)
    assert AssistState.preActive not in rig.states
    assert rig.sla.state in ACTIVE
    assert rig.set_speeds[-1] == 110.
    assert set(rig.set_speeds) == {100., 110.}

  def test_a_limit_that_flips_back_within_the_hold_never_counts(self, mocker):
    rig = self.active_rig(mocker, 110.)
    for kph, seconds in ((100., 0.5), (110., 0.5), (100., 0.9), (110., 2.)):
      rig.raw_kph = kph
      rig.run(seconds)
    assert set(rig.states) == {AssistState.active}
    assert set(rig.set_speeds) == {110.}

  def test_route_dd_flip_flop(self, mocker):
    """Route dd 728.30 / 730.31 / 732.31: 110 -> 100 -> 110 -> 100, two seconds apart, a lead ahead. Upstream rewrote the
    set speed three times (110, 100, 110, 100). Each value held for 2 s, longer than the hold, so each still counts;
    what changes is that the set speed is never touched: two '-' prompts, and between them the limit is back at the
    set speed, which confirms itself as on engaging."""
    rig = self.active_rig(mocker, 110.)
    prompts = 0
    for kph in (100., 110., 100.):
      rig.raw_kph = kph
      for _ in range(round(2.0 / DT_CTRL)):
        before = rig.sla.state
        rig.step()
        prompts += rig.sla.state == AssistState.preActive and before != AssistState.preActive
    assert prompts == 2
    assert set(rig.set_speeds) == {110.}

  def test_engaging_above_a_lower_limit_is_unchanged(self, mocker):
    """Engaging at 110 on a 100 road was already a prompt (disabled -> preActive): it still is, and nothing else."""
    rig = HoldRig(110., 100.)
    self.setup_clock(mocker, rig)
    rig.run_until(AssistState.preActive, 3.)
    rig.run(PRE_ACTIVE_GUARD_PERIOD[False] + 0.5)
    assert rig.sla.state == AssistState.inactive
    assert set(rig.set_speeds) == {110.}


class TestConfirmThreshold(GraceTestBase):
  def sla(self, pcm_cruise: bool):
    return Rig(80., 80., pcm_cruise=pcm_cruise).sla

  def test_non_pcm_lower_limit_needs_confirming_at_any_value(self):
    sla = self.sla(False)
    sla.is_metric = True
    for set_kph, limit_kph, want in ((110, 100, True), (100, 90, True), (130, 120, True), (110, 110, False),
                                     (100, 110, False), (90, 80, True), (80, 70, True), (60, 50, True), (60, 70, True)):
      sla.v_cruise_cluster_conv, sla.speed_limit_final_last_conv = set_kph, limit_kph
      assert sla.apply_confirm_speed_threshold is want, (set_kph, limit_kph)

  def test_pcm_op_long_is_upstream(self):
    """A PCM-cruise car's prompt asks for a set speed (PCM_LONG_REQUIRED_MAX_SET_SPEED), not the limit: its rule is
    upstream's, the 80 km/h line only."""
    sla = self.sla(True)
    sla.is_metric = True
    for set_kph, limit_kph in ((110, 100), (100, 90), (130, 80), (100, 110)):
      sla.v_cruise_cluster_conv, sla.speed_limit_final_last_conv = set_kph, limit_kph
      assert sla.apply_confirm_speed_threshold is (limit_kph < CONFIRM_SPEED_THRESHOLD[True]), (set_kph, limit_kph)


class TestSpeedLimitChangeHold(OpenpilotTestCase):
  def frames(self, hold: SpeedLimitChangeHold, n: int, kph: float, current_kph: float | None = None) -> list[float]:
    out = []
    for _ in range(n):
      hold.update(resolver_out(kph, current_kph))
      out.append(round(hold.speed_limit_final_last * CV.MS_TO_KPH, 3))
    return out

  def test_the_first_limit_and_losing_it_pass_at_once(self):
    h = SpeedLimitChangeHold()
    assert self.frames(h, 1, 100.) == [100.]
    assert h.speed_limit_valid and h.speed_limit_last_valid
    assert self.frames(h, 1, 0.) == [0.]
    assert not h.speed_limit_valid and not h.speed_limit_last_valid

  def test_a_new_limit_counts_on_its_20th_frame(self):
    h = SpeedLimitChangeHold()
    self.frames(h, 1, 110.)
    assert self.frames(h, 20, 100.) == [110.] * 19 + [100.]
    assert not h.holding

  def test_everything_is_held_together(self):
    h = SpeedLimitChangeHold()
    self.frames(h, 1, 110.)
    h.update(resolver_out(100.))
    assert h.holding
    assert round(h.speed_limit * CV.MS_TO_KPH) == round(h.speed_limit_final * CV.MS_TO_KPH) == 110
    assert round(h.speed_limit_last * CV.MS_TO_KPH) == 110

  def test_a_flicker_never_counts(self):
    h = SpeedLimitChangeHold()
    self.frames(h, 1, 110.)
    assert set(self.frames(h, 19, 100.) + self.frames(h, 1, 110.) + self.frames(h, 19, 100.)) == {110.}
    assert self.frames(h, 1, 100.) == [100.], "19 + 1 frames of 100, but not consecutive: the count restarted"

  def test_a_second_change_restarts_the_count(self):
    h = SpeedLimitChangeHold()
    self.frames(h, 1, 110.)
    self.frames(h, 10, 100.)
    assert self.frames(h, 20, 90.) == [110.] * 19 + [90.]

  def test_the_current_limit_going_0_under_a_carried_one_passes(self):
    """An untagged stretch: the raw limit is 0 while the last one is carried. Not a new limit: nothing to hold."""
    h = SpeedLimitChangeHold()
    self.frames(h, 1, 110.)
    h.update(resolver_out(110., current_kph=0.))
    assert h.speed_limit == 0. and round(h.speed_limit_final_last * CV.MS_TO_KPH) == 110
    assert not h.speed_limit_valid and h.speed_limit_last_valid


class TestPlannerPublishesTheHeldLimit(GraceTestBase):
  def test_plan_carries_what_sla_saw(self, mocker):
    """card, selfdrived and the UI read longitudinalPlanSP.speedLimit.resolver: it must be the held limit, or card
    would rewrite the set speed on the raw change while SLA still waits."""
    from openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlannerSP

    CarInterface = interfaces[HONDA.HONDA_ACCORD_9G_AU]
    CP = CarInterface.get_non_essential_params(HONDA.HONDA_ACCORD_9G_AU)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, HONDA.HONDA_ACCORD_9G_AU)
    planner = LongitudinalPlannerSP(CP, CP_SP, mock.MagicMock())
    planner.limit_hold.update(resolver_out(110.))
    planner.limit_hold.update(resolver_out(100.))  # held back
    planner.resolver.speed_limit = planner.resolver.speed_limit_final_last = 100 * CV.KPH_TO_MS  # the raw change
    planner.resolver.distance = 0.

    sent = {}
    sm = SimpleNamespace(all_checks=lambda **_: True)
    pm = SimpleNamespace(send=lambda name, msg: sent.update({name: msg}))
    planner.publish_longitudinal_plan_sp(sm, pm)  # ty: ignore[invalid-argument-type]
    res = sent['longitudinalPlanSP'].longitudinalPlanSP.speedLimit.resolver
    assert round(res.speedLimitFinalLast * CV.MS_TO_KPH) == 110
    assert round(res.speedLimit * CV.MS_TO_KPH) == 110
    assert res.speedLimitValid and res.speedLimitLastValid
