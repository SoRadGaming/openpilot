"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import numpy as np

from openpilot.cereal import custom
from opendbc.car.structs import car
from opendbc.car import structs
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot.selfdrive.car.intelligent_cruise_button_management.helpers import get_minimum_set_speed
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import ACTIVE_STATES as SLA_ACTIVE_STATES, \
  PRE_ACTIVE_CONFIRM_GRACE
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.helpers import compare_cluster_target

ButtonType = car.CarState.ButtonEvent.Type
SpeedLimitAssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState

CRUISE_BUTTON_TIMER = {ButtonType.decelCruise: 0, ButtonType.accelCruise: 0,
                       ButtonType.setCruise: 0, ButtonType.resumeCruise: 0,
                       ButtonType.cancel: 0, ButtonType.mainCruise: 0}

V_CRUISE_MIN = 8
V_CRUISE_MAX = 145
V_CRUISE_UNSET = 255


def update_manual_button_timers(CS: car.CarState, button_timers: dict[car.CarState.ButtonEvent.Type, int]) -> None:
  # increment timer for buttons still pressed
  for k in button_timers:
    if button_timers[k] > 0:
      button_timers[k] += 1

  for b in CS.buttonEvents:
    if b.type.raw in button_timers:
      # Start/end timer and store current state on change of button pressed
      button_timers[b.type.raw] = 1 if b.pressed else 0


class VCruiseHelperSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP) -> None:
    self.CP = CP
    self.CP_SP = CP_SP
    self.v_cruise_kph = V_CRUISE_UNSET
    self.v_cruise_cluster_kph = V_CRUISE_UNSET
    self.params = Params()
    self.v_cruise_min = 0
    self.enabled_prev = False

    self.custom_acc_enabled = self.params.get_bool("CustomAccIncrementsEnabled")
    self.short_increment = self.params.get("CustomAccShortPressIncrement", return_default=True)
    self.long_increment = self.params.get("CustomAccLongPressIncrement", return_default=True)

    self.enable_button_timers = CRUISE_BUTTON_TIMER

    # Speed Limit Assist
    self.sla_state = SpeedLimitAssistState.disabled
    self.prev_sla_state = SpeedLimitAssistState.disabled
    self.has_speed_limit = False
    self.speed_limit_final_last = 0.
    self.speed_limit_final_last_kph = 0.
    self.prev_speed_limit_final_last_kph = 0.
    self.req_plus = False
    self.req_minus = False
    # FORK(SPEED-LIMIT): the confirm prompt's grace (speed_limit_assist.PRE_ACTIVE_CONFIRM_GRACE). Card frames since the
    # planner's prompt timed out (-1: none to speak of), and the limit that prompt asked about.
    self._sla_state_seen = SpeedLimitAssistState.disabled
    self._sla_grace_frames = -1
    self._sla_grace_limit_kph = 0.
    self._sla_frame = 0                           # card frames, to tell one press from the next
    self._sla_grace_press: tuple | None = None    # (button, frame it went down) of the press that confirmed in the grace

  def read_custom_set_speed_params(self) -> None:
    self.custom_acc_enabled = self.params.get_bool("CustomAccIncrementsEnabled")
    self.short_increment = self.params.get("CustomAccShortPressIncrement", return_default=True)
    self.long_increment = self.params.get("CustomAccLongPressIncrement", return_default=True)

  def update_v_cruise_delta(self, long_press: bool, v_cruise_delta: float) -> tuple[bool, float]:
    if not self.custom_acc_enabled:
      v_cruise_delta = v_cruise_delta * (5 if long_press else 1)
      return long_press, v_cruise_delta

    # Apply user-specified multipliers to the base increment
    short_increment = np.clip(self.short_increment, 1, 10)
    long_increment = np.clip(self.long_increment, 1, 10)

    actual_increment = long_increment if long_press else short_increment
    round_to_nearest = actual_increment in (5, 10)
    v_cruise_delta = v_cruise_delta * actual_increment

    return round_to_nearest, v_cruise_delta

  def get_minimum_set_speed(self, is_metric: bool) -> None:
    if self.CP_SP.pcmCruiseSpeed:
      self.v_cruise_min = V_CRUISE_MIN
      return

    self.v_cruise_min = get_minimum_set_speed(is_metric)

  def update_enabled_state(self, CS: car.CarState, enabled: bool) -> bool:
    # special enabled state for non pcmCruiseSpeed, unchanged for non pcmCruise
    if not self.CP_SP.pcmCruiseSpeed:
      update_manual_button_timers(CS, self.enable_button_timers)
      button_pressed = any(self.enable_button_timers[k] > 0 for k in self.enable_button_timers)

      if enabled and not self.enabled_prev:
        self.enabled_prev = not button_pressed
        enabled = False
      elif not enabled:
        self.enabled_prev = enabled

      return enabled and self.enabled_prev

    return enabled

  def update_speed_limit_assist(self, is_metric, LP_SP: custom.LongitudinalPlanSP) -> None:
    resolver = LP_SP.speedLimit.resolver
    self.has_speed_limit = resolver.speedLimitValid or resolver.speedLimitLastValid
    self.speed_limit_final_last = LP_SP.speedLimit.resolver.speedLimitFinalLast
    self.speed_limit_final_last_kph = self.speed_limit_final_last * CV.MS_TO_KPH
    self.sla_state = LP_SP.speedLimit.assist.state
    self.req_plus, self.req_minus = compare_cluster_target(self.v_cruise_cluster_kph * CV.KPH_TO_MS,
                                                           self.speed_limit_final_last, is_metric)

    # FORK(SPEED-LIMIT): the confirm grace. preActive -> inactive is the prompt timing out (the non-PCM planner
    # leaves preActive otherwise only for active or disabled); it lasts while the planner stays inactive on that limit.
    if self.sla_state == SpeedLimitAssistState.inactive and self._sla_state_seen == SpeedLimitAssistState.preActive:
      self._sla_grace_frames = 0
      self._sla_grace_limit_kph = self.speed_limit_final_last_kph
    elif self._sla_grace_frames >= 0:
      same = self.sla_state == SpeedLimitAssistState.inactive and self.speed_limit_final_last_kph == self._sla_grace_limit_kph
      self._sla_grace_frames = self._sla_grace_frames + 1 if same else -1
    self._sla_state_seen = self.sla_state
    self._sla_frame += 1

  @property
  def update_speed_limit_final_last_changed(self) -> bool:
    return self.has_speed_limit and bool(self.speed_limit_final_last_kph != self.prev_speed_limit_final_last_kph)

  def update_speed_limit_assist_pre_active_confirmed(self, button_type: car.CarState.ButtonEvent.Type,
                                                     held_frames: int = 0) -> bool:
    """FORK(SPEED-LIMIT): `held_frames` is how many frames ago the button went down (cruise.py's button timer), for the
    confirm grace below. cruise.py calls this on a short press's release and on each long-press step."""
    press = (button_type, self._sla_frame - max(held_frames, 0))
    if press == self._sla_grace_press:
      return True   # FORK(SPEED-LIMIT): the rest of a press that confirmed in the grace (its later long-press steps)

    in_prompt = self.sla_state == SpeedLimitAssistState.preActive or self.prev_sla_state == SpeedLimitAssistState.preActive
    in_grace = not in_prompt and self._sla_confirm_grace(held_frames)
    if in_prompt or in_grace:
      if (button_type == ButtonType.decelCruise and self.req_minus) or (button_type == ButtonType.accelCruise and self.req_plus):
        if in_grace:
          self._sla_grace_confirm(press)
        return True

    return False

  def _sla_confirm_grace(self, held_frames: int) -> bool:
    """FORK(SPEED-LIMIT): the prompt timed out, but this press went down no more than PRE_ACTIVE_CONFIRM_GRACE after
    card saw that, on the same limit. It is the confirm, and it is swallowed as a set-speed step as a press inside the
    prompt is. The asked-for direction is the caller's test, as inside the prompt. Non-PCM cruise only: a PCM car's
    planner prompt asks for a set speed, not a button, and has no grace."""
    if self.CP.pcmCruise or self._sla_grace_frames < 0:
      return False
    return self._sla_grace_frames - max(held_frames, 0) <= round(PRE_ACTIVE_CONFIRM_GRACE / DT_CTRL)

  def _sla_grace_confirm(self, press: tuple) -> None:
    """FORK(SPEED-LIMIT): a press confirmed in the grace - on its release, or on its first long-press step if held.
    Set the limit here, as update_speed_limit_assist_v_cruise_non_pcm() does once the planner goes active: inside the
    prompt the planner confirms first, but after the timeout it confirms on this, the set speed now matching the limit
    (speed_limit_assist.py), so card does not depend on the planner's 20 Hz view of the button agreeing to the frame.
    The grace is spent; the rest of this press (later long-press steps) stays swallowed, so a held button cannot walk
    the set speed on past the limit it has just confirmed."""
    self.v_cruise_kph = np.clip(round(self.speed_limit_final_last_kph, 1), self.v_cruise_min, V_CRUISE_MAX)
    self._sla_grace_frames = -1
    self._sla_grace_press = press

  def update_speed_limit_assist_pre_active_raise_blocked(self, button_type: car.CarState.ButtonEvent.Type,
                                                         v_cruise_kph_prev: float) -> bool:
    """FORK(SPEED-LIMIT): True when a press the OTHER way while the 'press + (or -) to confirm' prompt is up would RAISE
    the set speed; cruise.py then keeps the set speed it had. '-' with the gas down took the set speed UP to vEgo
    (route 114, 50 -> 72.5 km/h, still asking for '+'), and '+' during a '-' prompt raises it. A wrong-way press that
    lowers the set speed (115: '-', 60 -> 59), or leaves it, works as always, so the driver can always slow down. Neither
    confirms (the planner takes only the asked-for button), so the prompt stays until the right press or its timeout.
    The asked-for press is update_speed_limit_assist_pre_active_confirmed()'s, as upstream. Non-PCM cruise only: that
    prompt's flow (PCM long asks for a set speed instead)."""
    if self.CP.pcmCruise:
      return False
    if self.sla_state != SpeedLimitAssistState.preActive and self.prev_sla_state != SpeedLimitAssistState.preActive:
      return False
    wrong_way = (button_type == ButtonType.decelCruise and self.req_plus) or \
                (button_type == ButtonType.accelCruise and self.req_minus)
    return bool(wrong_way and self.v_cruise_kph > v_cruise_kph_prev)

  def update_speed_limit_assist_v_cruise_non_pcm(self) -> None:
    if self.sla_state in SLA_ACTIVE_STATES and (self.prev_sla_state not in SLA_ACTIVE_STATES or
                                                self.update_speed_limit_final_last_changed):
      self.v_cruise_kph = np.clip(round(self.speed_limit_final_last_kph, 1), self.v_cruise_min, V_CRUISE_MAX)

    self.prev_sla_state = self.sla_state
    self.prev_speed_limit_final_last_kph = self.speed_limit_final_last_kph
