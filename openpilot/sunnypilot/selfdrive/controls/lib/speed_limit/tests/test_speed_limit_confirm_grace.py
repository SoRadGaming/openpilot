"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): the 'press + (or -) to confirm' prompt's grace. The prompt times out after 5 s (non-PCM cruise); the
asked-for button still confirms if it went down no more than PRE_ACTIVE_CONFIRM_GRACE (1.0 s) after that, on the same
limit. Route 120 t=182.40 ('+', 0.29 s late) and route 121 t=71.13 ('-', 0.88 s late) both missed the prompt.

These run the three processes the way they run in the car, on the owner's path (openpilot longitudinal, not PCM cruise):
card's VCruiseHelper at 100 Hz, selfdrived's ButtonStateTracker on the same CarState, and the planner's SpeedLimitAssist
at 20 Hz on a clock this test owns. card hears the plan, and the planner hears the buttons, a few frames late.
"""
from types import SimpleNamespace

from openpilot.common.parameterized import parameterized

from openpilot.cereal import custom
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.structs import car
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car.cruise import VCruiseHelper
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
import openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist as sla_module
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist, \
  PRE_ACTIVE_CONFIRM_GRACE, PRE_ACTIVE_GUARD_PERIOD
from openpilot.sunnypilot.selfdrive.selfdrived.button_state_tracker import ButtonStateTracker
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.common.test import OpenpilotTestCase

ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type
AssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState
PLUS, MINUS = ButtonType.accelCruise, ButtonType.decelCruise
PLANNER_EVERY = 5   # card frames per planner frame: 100 Hz / 20 Hz


class Rig:
  """card + selfdrived + plannerd. `plan_lag`: card frames before card sees a plan; `button_lag`: card frames before
  the planner sees the buttons (selfdrived publishing, then the planner's poll)."""
  def __init__(self, set_kph: float, limit_kph: float, plan_lag: int = 1, button_lag: int = 1, pcm_cruise: bool = False):
    CarInterface = interfaces[HONDA.HONDA_ACCORD_9G_AU]
    CP = CarInterface.get_non_essential_params(HONDA.HONDA_ACCORD_9G_AU)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, HONDA.HONDA_ACCORD_9G_AU)
    CP.openpilotLongitudinalControl = True
    CP.pcmCruise = pcm_cruise
    self.now = 0.
    self.sla = SpeedLimitAssist(CP, CP_SP)
    assert self.sla.pcm_op_long == pcm_cruise
    self.card = VCruiseHelper(car.CarParams(pcmCruise=pcm_cruise), custom.CarParamsSP(pcmCruiseSpeed=not pcm_cruise))
    self.card.v_cruise_kph = self.card.v_cruise_cluster_kph = set_kph
    self.tracker = ButtonStateTracker()
    self.limit_kph = limit_kph
    self.final_kph = limit_kph   # speedLimitFinalLast: the limit with any offset
    self.plan_lag, self.button_lag = plan_lag, button_lag
    self.frame = 0
    self.plans: list = [(-1000, self._plan())]   # (planner frame, its plan); card starts on a disabled one
    self.buttons: list = []
    self.set_speeds: list = []
    self.states: list = []
    self.timeout_frame: int | None = None  # the planner frame the prompt timed out on

  def _plan(self):
    msg = custom.LongitudinalPlanSP.new_message()
    r = msg.speedLimit.resolver
    r.speedLimitValid = r.speedLimitLastValid = True
    r.speedLimit = r.speedLimitLast = self.limit_kph * CV.KPH_TO_MS
    r.speedLimitFinal = r.speedLimitFinalLast = self.final_kph * CV.KPH_TO_MS
    msg.speedLimit.assist.state = self.sla.state
    return msg

  def step(self, events=()):
    # card: the newest plan it has heard of, then this frame's buttons
    plan = next(p for f, p in reversed(self.plans) if f + self.plan_lag <= self.frame)
    self.card.update_speed_limit_assist(True, plan)
    cs = car.CarState(cruiseState={"available": True}, vEgo=60 * CV.KPH_TO_MS)
    cs.buttonEvents = [ButtonEvent(type=b, pressed=p) for b, p in events]
    self.card.update_v_cruise(cs, enabled=True, is_metric=True)
    self.set_speeds.append(float(self.card.v_cruise_kph))
    # selfdrived
    self.tracker.update(cs)
    self.buttons.append((self.tracker.release_toggle, self.tracker.pressed, self.card.v_cruise_cluster_kph))
    # plannerd at 20 Hz
    if self.frame % PLANNER_EVERY == 0:
      self.now = self.frame * DT_CTRL
      toggle, pressed, cluster_kph = self.buttons[max(self.frame - self.button_lag, 0)]
      self.sla.update_buttons(toggle, pressed)
      before = self.sla.state
      self.sla.update(True, False, 60 * CV.KPH_TO_MS, 0., cluster_kph * CV.KPH_TO_MS, self.limit_kph * CV.KPH_TO_MS,
                      self.final_kph * CV.KPH_TO_MS, True, 0., EventsSP())
      if before == AssistState.preActive and self.sla.state == AssistState.inactive:
        self.timeout_frame = self.frame
      self.plans.append((self.frame, self._plan()))
    self.states.append(self.sla.state)
    self.frame += 1

  def run(self, seconds: float):
    for _ in range(round(seconds / DT_CTRL)):
      self.step()

  def until_timeout(self) -> None:
    """Engage, let the prompt come up, and let it time out unanswered."""
    while self.timeout_frame is None:
      self.step()
      assert self.frame < 1000, "the prompt never timed out"

  def press(self, button, at: float, hold: float = 0.12) -> None:
    """Press `button` `at` seconds after the planner's timeout (route 120's '+' was held 0.12 s), then run 3 s."""
    assert self.timeout_frame is not None
    down = self.timeout_frame + round(at / DT_CTRL)
    up = down + max(round(hold / DT_CTRL), 1)
    while self.frame < down:
      self.step()
    self.step([(button, True)])
    while self.frame < up:
      self.step()
    self.step([(button, False)])
    self.run(3.)

  @property
  def confirmed(self) -> bool:
    return self.sla.state in (AssistState.active, AssistState.adapting)


class GraceTestBase(OpenpilotTestCase):
  def setup_method(self):
    self.params = Params()
    self.params.put("IsReleaseSpBranch", True, block=True)
    self.params.put("SpeedLimitMode", int(Mode.assist), block=True)
    self.params.put_bool("IsMetric", True, block=True)
    self.params.put("SpeedLimitOffsetType", 0, block=True)
    self.params.put("SpeedLimitValueOffset", 0, block=True)
    self.params.put_bool("SpeedLimitMapStrict", True, block=True)

  def setup_clock(self, mocker, rig: Rig):
    mocker.patch.object(sla_module, "time", SimpleNamespace(monotonic=lambda: rig.now))

  def rig(self, mocker, set_kph=60., limit_kph=80., **kw) -> Rig:
    rig = Rig(set_kph, limit_kph, **kw)
    self.setup_clock(mocker, rig)
    rig.until_timeout()
    assert rig.sla.state == AssistState.inactive
    assert rig.timeout_frame is not None
    assert rig.set_speeds[-1] == set_kph
    return rig


class TestConfirmGrace(GraceTestBase):
  def test_the_grace_is_1_s_after_the_5_s_prompt(self):
    assert PRE_ACTIVE_CONFIRM_GRACE == 1.0 and PRE_ACTIVE_GUARD_PERIOD[False] == 5

  @parameterized.expand([(at, b, s, lim) for at in (0.0, 0.29, 0.5, 0.88, 0.95)
                         for b, s, lim in ((PLUS, 60., 80.), (MINUS, 80., 60.))],
                        names=["at", "button", "set_kph", "limit_kph"])
  def test_the_asked_for_press_inside_the_grace_confirms(self, mocker, at, button, set_kph, limit_kph):
    rig = self.rig(mocker, set_kph, limit_kph)
    rig.press(button, at)
    assert rig.confirmed, f"{at} s late: still {rig.sla.state}"
    assert rig.set_speeds[-1] == limit_kph
    # swallowed as a set-speed step, as a press inside the prompt is: never a 1 km/h step on the way
    assert set(rig.set_speeds) == {set_kph, limit_kph}, sorted(set(rig.set_speeds))

  @parameterized.expand([(PLUS, 60., 80.), (MINUS, 80., 60.)], names=["button", "set_kph", "limit_kph"])
  def test_a_long_press_inside_the_grace_confirms_and_never_walks(self, mocker, button, set_kph, limit_kph):
    """Route 121: '-' down 0.88 s late and held 1.26 s. It used to walk 80 -> 75 -> 70 (5 km/h a step) and on past a
    limit that is not a multiple of the step; now it confirms on its first long-press step and the rest is swallowed."""
    rig = self.rig(mocker, set_kph, limit_kph)
    rig.press(button, 0.88, hold=1.26)
    assert rig.confirmed
    assert set(rig.set_speeds) == {set_kph, limit_kph}, sorted(set(rig.set_speeds))

  @parameterized.expand([(at,) for at in (1.03, 1.1, 1.5, 3.0)], names=["at"])
  def test_after_the_grace_a_press_is_a_set_speed_step_as_before(self, mocker, at):
    rig = self.rig(mocker)
    rig.press(PLUS, at)
    assert rig.sla.state == AssistState.inactive
    assert rig.set_speeds[-1] == 61.

  @parameterized.expand([(at,) for at in (0.0, 0.29, 0.88)], names=["at"])
  def test_the_other_button_never_confirms(self, mocker, at):
    rig = self.rig(mocker, 60., 80.)        # asked '+'
    rig.press(MINUS, at)
    assert rig.sla.state == AssistState.inactive
    assert rig.set_speeds[-1] == 59., "an ordinary step down, as after any timeout"
    rig = self.rig(mocker, 80., 60.)        # asked '-'
    rig.press(PLUS, at)
    assert rig.sla.state == AssistState.inactive
    assert rig.set_speeds[-1] == 81.

  def test_the_other_button_held_never_confirms(self, mocker):
    rig = self.rig(mocker, 60., 80.)
    rig.press(MINUS, 0.3, hold=1.3)
    assert rig.sla.state == AssistState.inactive
    assert rig.set_speeds[-1] < 60.

  def test_a_wrong_press_then_the_right_one_inside_the_grace_confirms(self, mocker):
    rig = self.rig(mocker, 60., 80.)
    assert rig.timeout_frame is not None
    down = rig.timeout_frame + 20
    while rig.frame < down:
      rig.step()
    rig.step([(MINUS, True)])
    rig.step([(MINUS, False)])
    rig.press(PLUS, 0.6)
    assert rig.confirmed and rig.set_speeds[-1] == 80.

  def test_never_after_the_limit_changed(self, mocker):
    """The limit it asked about changed (an offset, so no new prompt): the press is an ordinary step."""
    rig = self.rig(mocker, 60., 80.)
    rig.final_kph = 85.
    rig.run(0.2)
    rig.press(PLUS, 0.5)
    assert rig.sla.state == AssistState.inactive
    assert rig.set_speeds[-1] == 61.

  def test_a_new_limit_prompts_anew_and_the_old_grace_is_gone(self, mocker):
    """80 asked '+' from 60 and timed out; the limit drops to 50 0.2 s later, a new prompt asking '-'. A '+' 0.4 s after
    the old timeout is the wrong way for the prompt on screen: it confirms nothing and, as any wrong-way press that
    would raise the set speed inside a prompt, changes nothing (cruise_ext.update_speed_limit_assist_pre_active_raise_blocked)."""
    rig = self.rig(mocker, 60., 80.)
    rig.limit_kph = rig.final_kph = 50.
    rig.run(0.2)
    rig.press(PLUS, 0.4)
    assert rig.sla.state == AssistState.preActive
    assert rig.set_speeds[-1] == 60.

  def test_inside_the_prompt_nothing_changed(self, mocker):
    """The press inside the 5 s: swallowed, and the planner confirms, as it always has."""
    rig = Rig(60., 80.)
    self.setup_clock(mocker, rig)
    while rig.sla.state != AssistState.preActive:
      rig.step()
    rig.run(2.)
    rig.step([(PLUS, True)])
    rig.run(0.1)
    rig.step([(PLUS, False)])
    rig.run(1.)
    assert rig.confirmed and set(rig.set_speeds) == {60., 80.}
    assert rig.timeout_frame is None

  @parameterized.expand([(1, 1), (6, 6), (11, 6)], names=["plan_lag", "button_lag"])
  def test_card_and_planner_never_disagree(self, mocker, plan_lag, button_lag):
    """Every press from 0.80 s to 1.20 s late: either it confirms with no set-speed step, or it is a plain step and
    nothing confirms. Never a swallowed press that confirms nothing (the press lost), never a step that confirms
    anyway - whatever the planner's view of the buttons lags card's by."""
    for at in [round(0.80 + 0.01 * i, 2) for i in range(41)]:
      rig = self.rig(mocker, 60., 80., plan_lag=plan_lag, button_lag=button_lag)
      rig.press(PLUS, at)
      outcome = (rig.confirmed, rig.set_speeds[-1], set(rig.set_speeds))
      assert outcome in ((True, 80., {60., 80.}), (False, 61., {60., 61.})), f"{at} s: {outcome}"
      if at <= 0.95:
        assert rig.confirmed, f"{at} s"
      # card's 1.0 s starts when it hears of the timeout, plan_lag frames after the planner
      if at > PRE_ACTIVE_CONFIRM_GRACE + plan_lag * DT_CTRL:
        assert not rig.confirmed, f"{at} s"

  def test_the_planner_alone_dates_a_held_press_by_when_it_went_down(self, mocker):
    """The planner's own grace: a press seen held inside the 1.0 s confirms on its release, however late."""
    rig = self.rig(mocker)
    sla = rig.sla
    rig.now += 0.5
    sla.update_buttons(sla._release_toggle_prev, 1 << PLUS)      # seen held 0.5 s late
    rig.now += 1.5
    sla.update_buttons(sla._release_toggle_prev ^ (1 << PLUS), 0)  # released 2.0 s late
    assert sla._get_button_release(True, False, down_by=sla._grace_deadline)
    sla.update_buttons(sla._release_toggle_prev, 1 << PLUS)
    rig.now += 0.05
    sla.update_buttons(sla._release_toggle_prev ^ (1 << PLUS), 0)  # a new press, 2.05 s late
    assert not sla._get_button_release(True, False, down_by=sla._grace_deadline)

  def test_a_pcm_cruise_car_has_no_card_grace(self, mocker):
    """card never swallows a press after the timeout on a PCM car: its prompt asks for a set speed, not a button."""
    c = VCruiseHelper(car.CarParams(pcmCruise=True), custom.CarParamsSP(pcmCruiseSpeed=False))
    for state in (AssistState.preActive, AssistState.inactive):
      msg = custom.LongitudinalPlanSP.new_message()
      msg.speedLimit.resolver.speedLimitValid = True
      msg.speedLimit.resolver.speedLimitFinalLast = 80 * CV.KPH_TO_MS
      msg.speedLimit.assist.state = state
      c.v_cruise_cluster_kph = 60.
      c.update_speed_limit_assist(True, msg)
    assert c._sla_grace_frames == 0
    assert not c.update_speed_limit_assist_pre_active_confirmed(PLUS, 1)
