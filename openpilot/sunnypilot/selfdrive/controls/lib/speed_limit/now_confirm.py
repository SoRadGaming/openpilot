"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): push9 (owner, 2026-10-13, after route 137: "it says now but I can't set it"). A '+' or '-' press while
the comma 4's card says 'now' for a coming limit, in that limit's direction, is the confirm for EXACTLY that limit: Speed
Limit Assist sets it the moment it comes into force (speed_limit_assist.py, the non-PCM state machine), with no prompt
and no second press.

WHY. 'now' means the coming limit is less than 50 m away (or held after mapd's look-ahead ended there), not that it is in
force: the prompt only opens once the car has passed the sign, mapd has switched (1 Hz) and plannerd's 1 s change hold
(change_hold.py) has run out - 4.0 to 4.4 s after 'now' on routes 134 and 137. A press in between was an ordinary 1 km/h
step: SLA went active -> inactive, the card vanished behind the MAX number, and the driver had to press again when the
prompt came (137 305.16, 134 313.44 / 446.37 / 716.18 / 717.54).

THE GUARDS. Only a press:
  - in the last 50 m (100 ft): the same 'now' the card draws - mapd's live look-ahead under sunnypilot's 'Near', or held
    after it ended there (within 2 s of travel or 'Near') for at most APPROACH_HOLD_S. Not 'in 120 m'.
  - the right way: '-' for a limit below the set speed, '+' above it (compare_cluster_target, as the prompt asks).
  - for that exact limit: a different limit coming into force is the ordinary prompt, as today.
  - while long control is engaged, non-PCM cruise (the prompt's own flow), in SLA active or inactive - never a press
    the prompt itself is taking (preActive).
A press the other way disarms. The arm lapses ARM_KEEP_S after the 'now' window closes (kept while change_hold is still
holding a new limit back): a phantom 'now' (a limit mapd announced that never came - route 134 at 304.52 and 674.56)
sets nothing. Nothing changes before the limit is in force: until then the press is the ordinary 1 km/h step it always
was.

This module is the one place the 'now' geometry lives: the HUD (selfdrive/ui/sunnypilot/mici/onroad/hud_model.py) imports
NEAR_M & co from here, so the card and the planner cannot disagree about where 'now' starts.
"""
from openpilot.common.constants import CV

MAP_EXTRAPOLATE_MAX_S = 1.0  # liveMapDataSP is 1 Hz: the distance is run down by vEgo for at most one period
APPROACH_HOLD_S = 3.0   # once the look-ahead ends at the change, the coming limit is held (as 'now') this long at most
APPROACH_NEAR_S = 2.0   # the change is REACHED once the distance left is within this much travel ...
NEAR_M = 50.0           # ... or under sunnypilot's 'Near' (SpeedLimitRenderer._format_dist: 50 m metric ...
NEAR_FT = 100.0         # ... 100 ft imperial)
NSW_NO_LOOKAHEAD = (4, 7)  # nswZone.state: dead reckoning in a tunnel, and GPS still lost after it - never announced
ARM_KEEP_S = 2.5        # an armed press lapses this long after 'now' closes, if its limit has not come into force


def near_m(is_metric: bool) -> float:
  return NEAR_M if is_metric else NEAR_FT * 0.3048


def reached(dist: float, v_ego: float, is_metric: bool) -> bool:
  """The change is here: the distance left (m) is within APPROACH_NEAR_S of travel, or under sunnypilot's 'Near'."""
  return dist <= max(near_m(is_metric), max(v_ego, 0.0) * APPROACH_NEAR_S)


class NowConfirm:
  """The 'now' window and the press armed in it. update() once a plannerd frame, before Speed Limit Assist; press() for
  each '+'/'-' release SLA sees while it may arm; take() at a limit change."""
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.now_limit = 0             # the coming limit (display units) while the card says 'now'; 0 otherwise
    self.armed = 0                 # the limit a press armed (display units); 0: none
    self.armed_at = 0.0
    self._t: float | None = None
    self._map_frame = -1
    self._map_t = 0.0
    self._shown = 0                # the last coming limit the look-ahead gave ...
    self._from = 0                 # ... the limit in force then ...
    self._dist = 0.0               # ... and its distance, run down by vEgo once the look-ahead has lost it
    self._lost_t: float | None = None
    self._held = False
    self._closed_t: float | None = None  # when 'now' last closed with a press armed

  def _drop(self) -> None:
    self._shown = 0
    self._lost_t = None
    self._held = False

  def update(self, now: float, *, map_ok: bool, map_frame: int, ahead_valid: bool, ahead_ms: float, ahead_dist: float,
             nsw_state: int, car_ok: bool, v_ego: float, standstill: bool, limit: int, is_metric: bool,
             holding: bool = False) -> int:
    """The coming limit while the card would say 'now' (display units), else 0. `limit`: the limit in force as the
    resolver has it, when it is a map limit (display units, 0 otherwise) - the HUD's resolver_map_limit. `holding`:
    change_hold.py is holding a new limit back, which keeps an armed press alive."""
    dt = 0.0 if self._t is None else max(0.0, now - self._t)
    self._t = now
    if map_frame != self._map_frame:
      self._map_frame, self._map_t = map_frame, now
    conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
    moving = car_ok and not standstill
    v = max(0.0, v_ego) if moving else 0.0
    tunnel = map_ok and int(nsw_state) in NSW_NO_LOOKAHEAD
    now_limit = 0

    raw = 0
    dist = 0.0
    if limit > 0 and map_ok and moving and ahead_valid and not tunnel:
      ahead = int(round(ahead_ms * conv))
      dist = float(ahead_dist) - v * min(max(now - self._map_t, 0.0), MAP_EXTRAPOLATE_MAX_S)
      if ahead > 0 and ahead != limit and 0 < dist:
        raw = ahead
    if raw:
      self._shown, self._from, self._dist, self._lost_t, self._held = raw, limit, dist, None, False
      if dist < near_m(is_metric):
        now_limit = raw
    elif self._shown:
      if self._lost_t is None:
        self._lost_t = now
      if limit != self._from or not moving or tunnel:
        self._drop()
      else:
        self._dist -= v * dt
        self._held = self._held or reached(self._dist, v, is_metric)
        lost_s = now - self._lost_t
        if self._held and lost_s <= APPROACH_HOLD_S:
          now_limit = self._shown
        elif lost_s > APPROACH_HOLD_S:
          self._drop()
    self.now_limit = now_limit

    if self.armed:
      if now_limit == self.armed or holding:
        self._closed_t = None
      elif self._closed_t is None:
        self._closed_t = now
      elif now - self._closed_t > ARM_KEEP_S:
        self.armed = 0
        self._closed_t = None
    return now_limit

  def press(self, now: float, plus: bool, set_speed: int) -> None:
    """A '+' (plus) or '-' release, with the set speed it was pressed at (display units). Arms the 'now' limit when it
    goes that way; a press the other way disarms."""
    if self.armed and plus != (self.armed > set_speed):
      self.armed = 0
      self._closed_t = None
    lim = self.now_limit
    if lim and lim != set_speed and plus == (lim > set_speed):
      self.armed, self.armed_at, self._closed_t = lim, now, None

  def disarm(self) -> None:
    self.armed = 0
    self._closed_t = None

  def take(self, new_limit: int) -> bool:
    """A new limit has come into force (display units): True if a press armed exactly it. The arm is spent either way:
    a different limit is the ordinary prompt."""
    hit = self.armed > 0 and new_limit == self.armed
    self.disarm()
    return hit
