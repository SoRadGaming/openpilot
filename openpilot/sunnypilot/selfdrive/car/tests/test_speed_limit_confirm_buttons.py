"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): the cruise buttons while Speed Limit Assist asks 'press + (or -) to confirm speed limit' (preActive),
non-PCM cruise. The asked-for button confirms, as upstream always did: the planner takes it, and the car side swallows
it as a set-speed change. The OTHER button used to be a plain set-speed change: route 114, asked '+' at 50 km/h for an
80 zone, the driver pressed '-' with the gas down and the set speed went UP to vEgo, 72.5 km/h (cruise.py clips a SET
under the gas to vEgo); route 115, 60 -> 59. Now it is ignored: no change, no confirm, the prompt stays.
Outside preActive nothing changes, and nor does a PCM-cruise car.
"""
from opendbc.car.structs import car
from openpilot.cereal import custom
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.car.cruise import VCruiseHelper

ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type
AssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState
PLUS, MINUS = ButtonType.accelCruise, ButtonType.decelCruise


def plan(state, limit_kph: float):
  msg = custom.LongitudinalPlanSP.new_message()
  r = msg.speedLimit.resolver
  r.speedLimitValid = r.speedLimitLastValid = True
  r.speedLimit = r.speedLimitLast = r.speedLimitFinal = r.speedLimitFinalLast = limit_kph / 3.6
  msg.speedLimit.assist.state = state
  return msg


class Car:
  """card.py's order every frame: the plan into the helper, then the buttons."""
  def __init__(self, set_kph: float, pcm_cruise: bool = False):
    self.h = VCruiseHelper(car.CarParams(pcmCruise=pcm_cruise), custom.CarParamsSP(pcmCruiseSpeed=not pcm_cruise))
    self.h.v_cruise_kph = self.h.v_cruise_cluster_kph = set_kph

  def step(self, lp, buttons=(), gas=False, v_kph=50.0):
    self.h.update_speed_limit_assist(True, lp)
    cs = car.CarState(cruiseState={"available": True}, gasPressed=gas, vEgo=v_kph / 3.6)
    cs.buttonEvents = [ButtonEvent(type=b, pressed=p) for b, p in buttons]
    self.h.update_v_cruise(cs, enabled=True, is_metric=True)
    return self.h.v_cruise_kph

  def press(self, lp, button, frames=1, **kw):
    self.step(lp, [(button, True)], **kw)
    for _ in range(frames - 1):
      self.step(lp, **kw)
    return self.step(lp, [(button, False)], **kw)


class TestConfirmButtons(OpenpilotTestCase):
  def test_route_114_minus_with_the_gas_while_asked_plus(self):
    c, lp = Car(50.0), plan(AssistState.preActive, 80)
    c.step(lp)
    assert c.press(lp, MINUS, gas=True, v_kph=72.5) == 50.0, "not raised to vEgo (was 72.5)"
    assert c.h.v_cruise_cluster_kph == 50.0

  def test_route_115_minus_while_asked_plus(self):
    c, lp = Car(60.0), plan(AssistState.preActive, 80)
    c.step(lp)
    assert c.press(lp, MINUS) == 60.0, "not lowered (was 59)"

  def test_plus_while_asked_minus(self):
    c, lp = Car(105.0), plan(AssistState.preActive, 50)
    c.step(lp)
    assert c.press(lp, PLUS) == 105.0

  def test_a_long_press_the_wrong_way_is_ignored_too(self):
    c, lp = Car(60.0), plan(AssistState.preActive, 80)
    c.step(lp)
    assert c.press(lp, MINUS, frames=120) == 60.0
    c = Car(105.0)
    lp = plan(AssistState.preActive, 50)
    c.step(lp)
    assert c.press(lp, PLUS, frames=120) == 105.0

  def test_the_right_button_confirms_as_before(self):
    # swallowed as a set-speed change, as upstream does; the planner goes active and the set speed becomes the limit
    c, lp = Car(50.0), plan(AssistState.preActive, 80)
    c.step(lp)
    assert c.press(lp, PLUS) == 50.0
    assert c.step(plan(AssistState.active, 80)) == 80.0
    c, lp = Car(105.0), plan(AssistState.preActive, 50)
    c.step(lp)
    assert c.press(lp, MINUS) == 105.0
    assert c.step(plan(AssistState.active, 50)) == 50.0

  def test_after_a_wrong_press_the_right_one_still_confirms(self):
    c, lp = Car(50.0), plan(AssistState.preActive, 80)
    c.step(lp)
    c.press(lp, MINUS, gas=True, v_kph=72.5)
    assert c.press(lp, PLUS) == 50.0
    assert c.step(plan(AssistState.active, 80)) == 80.0

  def test_outside_pre_active_the_buttons_are_unchanged(self):
    for state in (AssistState.disabled, AssistState.inactive, AssistState.active, AssistState.pending,
                  AssistState.adapting):
      c, lp = Car(60.0), plan(state, 80)
      c.step(lp)
      if state in (AssistState.active, AssistState.adapting):
        c.step(lp)  # its first active frame sets the limit
        c.h.v_cruise_kph = c.h.v_cruise_cluster_kph = 60.0
      assert c.press(lp, MINUS) == 59.0, state
      assert c.press(lp, PLUS) == 60.0, state
      assert c.press(lp, MINUS, gas=True, v_kph=72.5) == 72.5, f"{state}: SET under the gas, vEgo as upstream"

  def test_at_the_limit_nothing_is_asked_and_nothing_is_ignored(self):
    c, lp = Car(80.0), plan(AssistState.preActive, 80)
    c.step(lp)
    assert c.press(lp, MINUS) == 79.0

  def test_a_pcm_cruise_car_is_untouched(self):
    # openpilot's own set speed on a PCM car (pcmCruiseSpeed off): only the asked-for press is swallowed, as upstream
    c, lp = Car(60.0, pcm_cruise=True), plan(AssistState.preActive, 80)
    for _ in range(3):
      c.step(lp)
    c.h.v_cruise_kph = c.h.v_cruise_cluster_kph = 60.0
    assert c.press(lp, PLUS) == 60.0, "the asked-for press: swallowed, as upstream"
    assert c.press(lp, MINUS) == 59.0, "the other one: a set-speed change, as upstream"
