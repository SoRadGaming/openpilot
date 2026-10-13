"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): push9 - a '+'/'-' press while the comma 4's card says 'now' is the confirm for that coming limit
(speed_limit/now_confirm.py). Route 137 at 305.16 and route 134 at 313.44, 446.37, 716.18 and 717.54: the driver pressed
during 'now', got a 1 km/h step, and had to press again ~4 s later when the prompt opened.

The rig is test_speed_limit_confirm_grace's - card's VCruiseHelper at 100 Hz, selfdrived's ButtonStateTracker, the
planner's SpeedLimitAssist at 20 Hz on a clock this test owns, non-PCM - plus mapd's look-ahead at 1 Hz and the planner's
'now' window (NowConfirm), fed the way LongitudinalPlannerSP.update_now_window feeds it.
"""
from openpilot.common.parameterized import parameterized

from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.now_confirm import ARM_KEEP_S, APPROACH_HOLD_S, NEAR_M, \
  NowConfirm, reached
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.tests.test_speed_limit_confirm_grace import AssistState, \
  GraceTestBase, MINUS, PLANNER_EVERY, PLUS, Rig
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

V_KPH = 60.
V = V_KPH * CV.KPH_TO_MS


class NowRig(Rig):
  """Driving at 60 km/h toward a sign `sign_m` meters ahead whose limit is `ahead_kph`. mapd publishes the look-ahead
  once a second until the car reaches the sign; the limit comes into force (the held limit changes) `in_force_s` after
  the sign - 1.3-1.6 s on routes 134/137 - or never (`phantom`)."""
  def __init__(self, set_kph, limit_kph, ahead_kph, sign_m=300., in_force_s: float = 1.5, phantom: bool = False, **kw):
    super().__init__(set_kph, limit_kph, **kw)
    self.ahead_kph = ahead_kph
    self.sign_t = sign_m / V
    self.t_force = self.sign_t + in_force_s
    self.phantom = phantom
    self.now_window: list[int] = []
    self._map = (0, False, 0.)

  def _map_now(self):
    """(map frame, look-ahead valid, distance) as mapd last published, 1 Hz."""
    k = int(self.now)
    t_msg = float(k)
    if self._map[0] != k + 1:
      dist = (self.sign_t - t_msg) * V
      self._map = (k + 1, dist > 0, dist)
    return self._map

  def step(self, events=()):
    if self.frame % PLANNER_EVERY == 0:
      t = self.frame * DT_CTRL
      if not self.phantom and t >= self.t_force and self.limit_kph != self.ahead_kph:
        self.limit_kph = self.final_kph = self.ahead_kph
      self.now = t
      frame, valid, dist = self._map_now()
      self.now_window.append(self.sla.now_confirm.update(
        t, map_ok=True, map_frame=frame, ahead_valid=valid, ahead_ms=self.ahead_kph * CV.KPH_TO_MS, ahead_dist=dist,
        nsw_state=2, car_ok=True, v_ego=V, standstill=False, limit=int(self.limit_kph), is_metric=True))
    super().step(events)

  def at(self, t: float):
    while self.frame * DT_CTRL < t:
      self.step()

  def tap(self, button, at: float, hold: float = 0.12):
    """Press at `at` for `hold`, then 0.1 s for the planner to hear the release."""
    self.at(at)
    self.step([(button, True)])
    self.run(hold)
    self.step([(button, False)])
    self.run(0.1)

  def engage_and_settle(self):
    self.run(1.5)
    assert self.sla.state == AssistState.active, self.sla.state


class NowConfirmBase(GraceTestBase):
  def rig(self, mocker, set_kph=60., limit_kph=60., ahead_kph=50., **kw) -> NowRig:
    rig = NowRig(set_kph, limit_kph, ahead_kph, **kw)
    self.setup_clock(mocker, rig)
    rig.engage_and_settle()
    return rig


class TestNowWindow(NowConfirmBase):
  def test_the_window_is_the_cards_now(self):
    assert NEAR_M == 50.0 and APPROACH_HOLD_S == 3.0 and ARM_KEEP_S == 2.5
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model
    # one definition: the HUD imports it
    assert hud_model.NEAR_M is NEAR_M and hud_model.reached is reached

  def test_now_opens_under_50_m_and_never_further_out(self, mocker):
    rig = self.rig(mocker)
    rig.at(rig.sign_t - 120. / V)          # 'in 120 m'
    assert rig.now_window[-1] == 0
    rig.at(rig.sign_t - 40. / V)           # under 'Near'
    assert rig.now_window[-1] == 50
    rig.at(rig.sign_t + 1.0)               # past the sign, the look-ahead gone, held
    assert rig.now_window[-1] == 50

  def test_the_window_closes_when_the_limit_comes_into_force(self, mocker):
    rig = self.rig(mocker)
    rig.at(rig.sign_t + 1.6)
    assert rig.limit_kph == 50. and rig.now_window[-1] == 0

  def test_never_in_a_tunnel_nor_at_a_standstill(self):
    n = NowConfirm()

    def now(nsw_state: int, standstill: bool) -> int:
      n.reset()
      return n.update(0., map_ok=True, map_frame=1, ahead_valid=True, ahead_ms=50 * CV.KPH_TO_MS, ahead_dist=30.,
                      nsw_state=nsw_state, car_ok=True, v_ego=V, standstill=standstill, limit=60, is_metric=True)
    assert now(2, False) == 50
    assert now(4, False) == 0 and now(7, False) == 0, "never in a tunnel"
    assert now(2, True) == 0, "never at a standstill"


class TestNowConfirm(NowConfirmBase):
  @parameterized.expand([(MINUS, 60., 50.), (PLUS, 50., 60.)], names=["button", "limit_kph", "ahead_kph"])
  def test_a_press_at_now_sets_the_limit_the_moment_it_is_in_force(self, mocker, button, limit_kph, ahead_kph):
    rig = self.rig(mocker, set_kph=limit_kph, limit_kph=limit_kph, ahead_kph=ahead_kph)
    rig.tap(button, rig.sign_t - 30. / V)
    stepped = limit_kph + (1. if button == PLUS else -1.)
    assert rig.set_speeds[-1] == stepped, "the press is still its 1 km/h step until the limit is in force"
    assert rig.sla.now_confirm.armed == int(ahead_kph)
    t_force = rig.t_force
    rig.at(t_force + 0.3)
    assert rig.set_speeds[-1] == ahead_kph, rig.set_speeds[-1]
    assert AssistState.preActive not in rig.states, "a prompt opened: the press did not count"
    # set within two planner frames of the limit coming into force
    first = next(i for i, v in enumerate(rig.set_speeds) if v == ahead_kph)
    assert first * DT_CTRL - t_force <= 2 * PLANNER_EVERY * DT_CTRL + 0.02, first * DT_CTRL - t_force
    rig.run(2.)
    assert rig.confirmed and rig.set_speeds[-1] == ahead_kph

  def test_the_wrong_way_does_not_arm(self, mocker):
    rig = self.rig(mocker)                       # 60 now, 50 coming: '-' asked
    rig.tap(PLUS, rig.sign_t - 30. / V)
    assert rig.sla.now_confirm.armed == 0
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive, "the ordinary prompt, as before"

  def test_a_press_the_other_way_disarms(self, mocker):
    rig = self.rig(mocker)
    rig.tap(MINUS, rig.sign_t - 45. / V)
    assert rig.sla.now_confirm.armed == 50
    rig.tap(PLUS, rig.sign_t - 20. / V)
    assert rig.sla.now_confirm.armed == 0
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive

  def test_further_out_than_now_is_an_ordinary_step(self, mocker):
    rig = self.rig(mocker)
    rig.tap(MINUS, rig.sign_t - 120. / V)        # 'in 120 m'
    assert rig.sla.now_confirm.armed == 0
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive and rig.set_speeds[-1] == 59.

  def test_only_that_exact_limit(self, mocker):
    rig = self.rig(mocker, ahead_kph=50.)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.sla.now_confirm.armed == 50
    rig.ahead_kph = 40.                          # what actually comes into force is 40
    rig.at(rig.t_force + 0.3)
    assert rig.limit_kph == 40.
    assert rig.sla.state == AssistState.preActive, "a different limit is the ordinary prompt"
    assert rig.set_speeds[-1] == 59.

  def test_a_phantom_sets_nothing(self, mocker):
    """Route 134 at 304.52 and 674.56: a limit announced that never came into force. The arm lapses ARM_KEEP_S after
    'now' closes, so the same number coming into force much later is the ordinary prompt."""
    rig = self.rig(mocker, phantom=True)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.sla.now_confirm.armed == 50
    rig.at(rig.sign_t + APPROACH_HOLD_S + ARM_KEEP_S + 0.2)
    assert rig.sla.now_confirm.armed == 0
    assert rig.set_speeds[-1] == 59. and rig.limit_kph == 60.
    rig.limit_kph = rig.final_kph = 50.          # much later, the 50 after all
    rig.run(0.5)
    assert rig.sla.state == AssistState.preActive

  def test_not_while_cruise_is_off(self, mocker):
    """With cruise off '-' engages (routes 137 88.47, 479.71): a press while not engaged arms nothing."""
    rig = self.rig(mocker)
    sla = rig.sla
    sla.update(False, False, V, 0., 60 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, True, 0., EventsSP())
    assert sla.state == AssistState.disabled
    rig.now = rig.sign_t - 1.
    sla.now_confirm.now_limit = 50
    sla.update_buttons(sla._release_toggle_prev ^ (1 << MINUS), 0)
    sla.update(True, False, V, 0., 59 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, True, 0., EventsSP())
    assert sla.now_confirm.armed == 0

  def test_nothing_changes_before_now(self, mocker):
    """Without a press at 'now', every frame is what it was before push9: the prompt at the change."""
    rig = self.rig(mocker)
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive and rig.set_speeds[-1] == 60.

  def test_a_pcm_car_never_arms(self, mocker):
    rig = NowRig(60., 60., 50., pcm_cruise=True)
    self.setup_clock(mocker, rig)
    rig.run(1.5)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.sla.now_confirm.armed == 0


class TestNowPrompt(GraceTestBase):
  """The card's words at 'now' (hud_model.now_prompt): the key the press takes and the set speed it gives."""
  def test_the_words(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import confirm_lower, now_prompt, prompt_parts
    assert now_prompt(50, 60, is_metric=True) == "press - to set 50"
    assert now_prompt(80, 60, is_metric=True) == "press + to set 80"
    assert now_prompt(60, 60, is_metric=True) == "", "the set speed is already it: nothing to press"
    assert now_prompt(0, 60, is_metric=True) == "" and now_prompt(50, 0, is_metric=True) == ""
    # the set speed it gives is the limit with the offset, as the resolver adds it
    assert now_prompt(50, 60, is_metric=True, offset_type=1, offset_value=5) == "press - to set 55"
    assert now_prompt(100, 110, is_metric=True, offset_type=2, offset_value=10) == "press - to set 110"
    assert now_prompt(35, 45, is_metric=False) == "press - to set 35"
    # the card's drawers read the key out of the words, as they do the confirm's
    t = now_prompt(50, 60, is_metric=True)
    assert prompt_parts(t) == ("press", "to set 50") and confirm_lower(t) is True
    assert confirm_lower(now_prompt(80, 60, is_metric=True)) is False
