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

ONE STATE MACHINE. The coming limit - mapd's look-ahead, its run-down by vEgo between the 1 Hz messages, the 'distance
only counts down' hysteresis, the 15 s / 500 m window, the hold at the change and the drop when the look-ahead is lost -
is ComingLimit below, on plain values. The HUD's ApproachState (selfdrive/ui/sunnypilot/mici/onroad/hud_model.py) is
ComingLimit fed from the UI's SubMaster, and NowConfirm is the same ComingLimit fed from plannerd's: the card and the
planner cannot disagree about 'now' (push9 review: a second copy here had no window cap, no hysteresis and the wrong
drop time when the look-ahead was lost far out, and opened a planner-only 'now' on route 112 at 2170.65 with no card on
screen). test_speed_limit_now_confirm feeds both the same frames.

THE GUARDS. Only a press:
  - in the card's 'now': under sunnypilot's 'Near' (50 m, 100 ft), or held after the look-ahead ended there (within 2 s
    of travel or 'Near') for at most APPROACH_HOLD_S. Not 'in 120 m'.
  - the right way: '-' when the set speed that limit gives (the limit with the user's offset, confirm_target - the
    number the card shows) is below the set speed, '+' above it. Equal: nothing to press, nothing armed.
  - for that exact limit: a different limit coming into force is the ordinary prompt, as today.
  - while long control is engaged, non-PCM cruise (the prompt's own flow), in SLA active or inactive - never a press
    the prompt itself is taking (preActive).
  - with the card switched on (HudApproachingLimit): with it off nothing says 'now', so nothing arms.
A press the other way disarms. The arm lapses ARM_KEEP_S after the 'now' window closes (kept while change_hold is still
holding a new limit back): a phantom 'now' (a limit mapd announced that never came - route 134 at 304.52 and 674.56)
sets nothing. Nothing changes before the limit is in force: until then the press is the ordinary 1 km/h step it always
was.

WHAT IT DOES NOT KNOW. plannerd cannot see the screen. A press in 'now' counts while the card is drawn small or hidden by
something drawn over it - the stock MAX number while SLA is inactive after the press's own step, a full-screen alert,
the lane-block tile - exactly as it would with the card showing: the card, when it shows, says what the press does.
"""
from dataclasses import dataclass

from openpilot.common.constants import CV

MAP_EXTRAPOLATE_MAX_S = 1.0  # liveMapDataSP is 1 Hz: the distance is run down by vEgo for at most one period
APPROACH_HOLD_S = 3.0   # once the look-ahead ends at the change, the coming limit is held (as 'now') this long at most
APPROACH_NEAR_S = 2.0   # the change is REACHED once the distance left is within this much travel ...
NEAR_M = 50.0           # ... or under sunnypilot's 'Near' (SpeedLimitRenderer._format_dist: 50 m metric ...
NEAR_FT = 100.0         # ... 100 ft imperial)
APPROACH_GAP_S = 2.0    # the look-ahead lost further out: the distance runs down by vEgo this long at most, then it goes
APPROACH_JUMP_M = 10.0  # mapd's figure coming back up by one period's run-down + this is noise (held); more is news
NEXT_MAX_M = 500.0      # the coming limit shows from the earlier of 500 m ...
NEXT_MAX_S = 15.0       # ... or 15 s of travel at the current speed
NSW_NO_LOOKAHEAD = (4, 7)  # nswZone.state: dead reckoning in a tunnel, and GPS still lost after it - never announced
ARM_KEEP_S = 2.5        # an armed press lapses this long after 'now' closes, if its limit has not come into force

# the confirm's target, as cruise_ext stores it and the MAX number shows it (hud_model.target_speed / now_prompt)
CRUISE_MIN_KPH = 8      # cruise_ext.V_CRUISE_MIN: the confirm's floor with pcmCruiseSpeed
CRUISE_MAX_KPH = 145    # cruise_ext.V_CRUISE_MAX
KM_TO_MILE = 0.621371   # mici/onroad/hud_renderer.KM_TO_MILE: how the MAX number converts
OFFSET_FIXED, OFFSET_PERCENT = 1, 2   # SpeedLimitOffsetType (speed_limit/common.py OffsetType)


def near_m(is_metric: bool) -> float:
  return NEAR_M if is_metric else NEAR_FT * 0.3048


def reached(dist: float, v_ego: float, is_metric: bool) -> bool:
  """The change is here: the distance left (m) is within APPROACH_NEAR_S of travel, or under sunnypilot's 'Near'."""
  return dist <= max(near_m(is_metric), max(v_ego, 0.0) * APPROACH_NEAR_S)


def next_window_m(v_ego: float) -> float:
  """How far ahead a coming limit is shown: the earlier of 15 s of travel and 500 m."""
  return min(NEXT_MAX_M, max(v_ego, 1.0) * NEXT_MAX_S)


def cruise_min_kph(is_metric: bool, pcm_cruise_speed: bool) -> float:
  """The confirm's floor (cruise_ext.VCruiseHelperSP: V_CRUISE_MIN with pcmCruiseSpeed, else the button-managed
  cars' minimum set speed, intelligent_cruise_button_management.helpers.get_minimum_set_speed - 30, or 20 imperial,
  used as km/h there too)."""
  return CRUISE_MIN_KPH if pcm_cruise_speed else (30 if is_metric else 20)


def confirm_target(limit: int, *, is_metric: bool, offset_type: int = 0, offset_value: float = 0.0,
                   pcm_cruise_speed: bool = True) -> int:
  """The set speed a confirm of `limit` (display units) gives, in display units: the limit with the user's offset as
  the resolver adds it (speed_limit_resolver._get_speed_limit_offset), clamped and rounded as the confirm stores it
  (cruise_ext: clip(round(kph, 1), v_cruise_min, V_CRUISE_MAX)) and converted as the MAX number shows it. 0: none."""
  if limit <= 0:
    return 0
  unit = CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS
  lim_ms = limit * unit
  off_ms = offset_value * unit if offset_type == OFFSET_FIXED else \
    offset_value * 0.01 * lim_ms if offset_type == OFFSET_PERCENT else 0.0
  kph = min(max(round((lim_ms + off_ms) * CV.MS_TO_KPH, 1), cruise_min_kph(is_metric, pcm_cruise_speed)), CRUISE_MAX_KPH)
  return max(0, int(round(kph * (1.0 if is_metric else KM_TO_MILE))))


@dataclass(frozen=True)
class Approach:
  limit: int          # the coming limit, display units
  lower: bool         # lower than the one in force (a down chevron), else higher
  dist: float         # m to it; 0 while held
  frac: float         # what is left of the 15 s / 500 m window, 0..1; 0 while held
  held: bool = False  # the look-ahead has ended and the resolver has not taken it yet: drawn 'now'


def is_now(a: Approach | None, is_metric: bool) -> bool:
  """The card says 'now' for it: held, or under sunnypilot's 'Near' (SpeedLimitRenderer._format_dist: < 50 m, < 100 ft),
  which hud_model.approach_text turns into 'now'."""
  return a is not None and (a.held or a.dist < near_m(is_metric))


def look_ahead(*, limit: int, map_ok: bool, car_ok: bool, standstill: bool, ahead_valid: bool, ahead_ms: float,
               ahead_dist: float, nsw_state: int, v_ego: float, map_age_s: float, is_metric: bool) -> Approach | None:
  """What mapd's look-ahead says this frame, before any hold: a limit other than `limit` (the resolver's map limit in
  force, display units) within the window, or None. map_ok / car_ok: liveMapDataSP / carState received this drive and
  still arriving. Pure."""
  if limit <= 0 or not (map_ok and car_ok):
    return None
  if standstill or not ahead_valid or int(nsw_state) in NSW_NO_LOOKAHEAD:
    return None
  conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
  ahead = int(round(ahead_ms * conv))
  v = max(0.0, float(v_ego))
  dist = float(ahead_dist) - v * min(max(map_age_s, 0.0), MAP_EXTRAPOLATE_MAX_S)
  window = next_window_m(v)
  if ahead <= 0 or ahead == limit or not 0 < dist <= window:
    return None
  return Approach(limit=ahead, lower=ahead < limit, dist=dist, frac=dist / window)


class ComingLimit:
  """The coming limit, frame by frame, with its run-down and hold - the one state machine behind the HUD's card
  (hud_model.ApproachState) and the planner's 'now' (NowConfirm). update() is idempotent within a frame (the same
  now). Rules (hud_model's docstring has the routes):
    - while the same change is coming its distance only counts down - unless mapd's figure comes back up by more than
      one period's run-down plus APPROACH_JUMP_M, which is news, taken as it is;
    - the look-ahead lost: the last distance runs down by vEgo. REACHED (within APPROACH_NEAR_S of travel, or 'Near'):
      HELD, 'now', for at most APPROACH_HOLD_S. Further out: a distance, for at most APPROACH_GAP_S; held if it
      reaches the change in that time, gone if not;
    - the limit in force changing (the hand-over), a standstill or a tunnel ends it."""
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self.frame: Approach | None = None
    self._t: float | None = None
    self._shown: Approach | None = None   # the last approach the look-ahead gave
    self._from = 0                        # ... and the limit in force then
    self._dist = 0.0                      # the distance last drawn (run down by vEgo while the look-ahead is lost)
    self._lost_t: float | None = None     # when the look-ahead stopped giving it
    self._held = False                    # ... and the change was reached: drawn 'now'
    self._map_frame = -1
    self._map_t = 0.0

  def _drop(self) -> None:
    self.frame = self._shown = self._lost_t = None
    self._held = False

  def update(self, now: float, *, map_frame: int, limit: int, map_ok: bool, car_ok: bool, standstill: bool,
             v_ego: float, ahead_valid: bool, ahead_ms: float, ahead_dist: float, nsw_state: int,
             is_metric: bool) -> Approach | None:
    if self._t == now:
      return self.frame
    dt = 0.0 if self._t is None else max(0.0, now - self._t)
    self._t = now
    if map_frame != self._map_frame:
      self._map_frame, self._map_t = map_frame, now
    moving = car_ok and not standstill
    v = max(0.0, float(v_ego)) if moving else 0.0
    raw = look_ahead(limit=limit, map_ok=map_ok, car_ok=car_ok, standstill=standstill, ahead_valid=ahead_valid,
                     ahead_ms=ahead_ms, ahead_dist=ahead_dist, nsw_state=nsw_state, v_ego=v_ego,
                     map_age_s=now - self._map_t, is_metric=is_metric)
    if raw is not None:
      prev = self._shown
      if prev is not None and prev.limit == raw.limit and self._from == limit and \
         self._dist < raw.dist <= self._dist + v * MAP_EXTRAPOLATE_MAX_S + APPROACH_JUMP_M:
        # the same change coming: its distance only counts down. mapd's 1 Hz figure jumps back up a little when the
        # run-down between messages was ahead of it (route 10f t 194.2-194.8: 'now', 'in 50 m', 'now'); by more than
        # a period's run-down + APPROACH_JUMP_M it is news and is taken (route 10f t 199.7: 65 m, after 'now')
        d = max(self._dist, 0.0)
        raw = Approach(limit=raw.limit, lower=raw.lower, dist=d, frac=min(1.0, d / next_window_m(v)))
      self._shown, self._from, self._dist, self._lost_t, self._held = raw, limit, raw.dist, None, False
      self.frame = raw
      return raw
    self.frame = None
    if self._shown is None:
      return None
    if self._lost_t is None:
      self._lost_t = now
    # kept only while the limit in force is still the one the change was announced from, the car still moving, the map
    # not dead reckoning; anything else (the resolver took it - the hand-over - or another limit came) ends it
    tunnel = map_ok and int(nsw_state) in NSW_NO_LOOKAHEAD
    if limit != self._from or not moving or tunnel:
      self._drop()
      return None
    self._dist -= v * dt
    lost_s = now - self._lost_t
    self._held = self._held or reached(self._dist, v, is_metric)
    if lost_s > (APPROACH_HOLD_S if self._held else APPROACH_GAP_S):
      self._drop()
      return None
    if self._held:
      self.frame = Approach(limit=self._shown.limit, lower=self._shown.lower, dist=0.0, frac=0.0, held=True)
    else:
      # further out: still a distance, running down - never 'now' before it is reached
      self.frame = Approach(limit=self._shown.limit, lower=self._shown.lower, dist=self._dist,
                            frac=min(1.0, self._dist / next_window_m(v)))
    return self.frame


class NowConfirm:
  """The 'now' window and the press armed in it. update() once a plannerd frame, before Speed Limit Assist; press() for
  each '+'/'-' release SLA sees while it may arm; take() at a limit change."""
  def __init__(self):
    self.coming = ComingLimit()
    self.reset()

  def reset(self) -> None:
    self.coming.reset()
    self.now_limit = 0             # the coming limit (display units) while the card says 'now'; 0 otherwise
    self.armed = 0                 # the limit a press armed (display units); 0: none
    self.armed_target = 0          # ... and the set speed its confirm gives (confirm_target)
    self.armed_at = 0.0
    self._closed_t: float | None = None  # when 'now' last closed with a press armed
    self._is_metric = True
    self._offset = (0, 0.0, True)  # offset type, offset value, pcmCruiseSpeed: what confirm_target needs

  def update(self, now: float, *, map_ok: bool, map_frame: int, ahead_valid: bool, ahead_ms: float, ahead_dist: float,
             nsw_state: int, car_ok: bool, v_ego: float, standstill: bool, limit: int, is_metric: bool,
             holding: bool = False, enabled: bool = True, offset_type: int = 0, offset_value: float = 0.0,
             pcm_cruise_speed: bool = True) -> int:
    """The coming limit while the card would say 'now' (display units), else 0. `limit`: the limit in force as the
    resolver has it, when it is a map limit (display units, 0 otherwise) - the HUD's resolver_map_limit. `holding`:
    change_hold.py is holding a new limit back, which keeps an armed press alive. `enabled`: the card is switched on
    (HudApproachingLimit) - off, there is no 'now' and nothing stays armed. The offset and pcmCruiseSpeed are the
    resolver's and the car's, for the set speed a press gives (confirm_target)."""
    self._is_metric = is_metric
    self._offset = (int(offset_type or 0), float(offset_value or 0.0), bool(pcm_cruise_speed))
    if not enabled:
      self.coming.reset()
      self.now_limit = 0
      self.disarm()
      return 0
    a = self.coming.update(now, map_frame=map_frame, limit=limit, map_ok=map_ok, car_ok=car_ok, standstill=standstill,
                           v_ego=v_ego, ahead_valid=ahead_valid, ahead_ms=ahead_ms, ahead_dist=ahead_dist,
                           nsw_state=nsw_state, is_metric=is_metric)
    now_limit = a.limit if is_now(a, is_metric) else 0
    self.now_limit = now_limit

    if self.armed:
      if now_limit == self.armed or holding:
        self._closed_t = None
      elif self._closed_t is None:
        self._closed_t = now
      elif now - self._closed_t > ARM_KEEP_S:
        self.disarm()
    return now_limit

  def target(self, limit: int) -> int:
    """The set speed a confirm of `limit` gives (display units), with the offset update() was last given."""
    offset_type, offset_value, pcm_cruise_speed = self._offset
    return confirm_target(limit, is_metric=self._is_metric, offset_type=offset_type, offset_value=offset_value,
                          pcm_cruise_speed=pcm_cruise_speed)

  def press(self, now: float, plus: bool, set_speed: int) -> None:
    """A '+' (plus) or '-' release, with the set speed it was pressed at (display units). Arms the 'now' limit when the
    set speed it gives is that way - the key the card shows (hud_model.now_prompt); a press the other way disarms."""
    if self.armed and plus != (self.armed_target > set_speed):
      self.disarm()
    lim = self.now_limit
    if not lim:
      return
    target = self.target(lim)
    if target > 0 and target != set_speed and plus == (target > set_speed):
      self.armed, self.armed_target, self.armed_at, self._closed_t = lim, target, now, None

  def disarm(self) -> None:
    self.armed = 0
    self.armed_target = 0
    self._closed_t = None

  def take(self, new_limit: int) -> bool:
    """A new limit has come into force (display units): True if a press armed exactly it. The arm is spent either way:
    a different limit is the ordinary prompt."""
    hit = self.armed > 0 and new_limit == self.armed
    self.disarm()
    return hit
