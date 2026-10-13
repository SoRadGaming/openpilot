"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): what the comma 4 HUD shows, decided from the messages - no drawing here (hud_cluster.py, hud_alerts.py and
hud_renderer.py draw it), so every rule is testable without a window.

THE CLUSTER, top right (HudStoppedTimer), is the stop time only: after TIMER_DELAY_S at a standstill (carState), a
stopwatch and m:ss. Round 5 (2026-10-09, the owner's option A) removed the live speed, the speed-limit sign with its
school-zone lamps and electronic-sign styling, and the next lower limit, with their settings: the car's own dash has the
speed, and the coming limit is announced top left instead (below). Nothing on the screen marks a school zone now.

THE COMING LIMIT (HudApproachingLimit, ApproachState; drawn by hud_renderer.py in the stock MAX number's place, as a
card in the style HudLimitStyle picks - MaxCard below), from:
  longitudinalPlanSP    the resolver's limit (speedLimitLast while valid or last-valid), source map: a car-sourced limit
                        is never paired with a map look-ahead, as sunnypilot's own sign does. Missing or stale: nothing.
  liveMapDataSP         the next limit (speedLimitAhead*, exactly what mapd published - never nswZone's own look-ahead,
                        which mapd deliberately does not publish while dead reckoning in a tunnel, where the look-ahead
                        gave limits that never came). nswZone.state 4 or 7 (dead reckoning, or GPS still lost after it):
                        nothing, whatever else says so. Missing or stale: nothing.
                        It arrives once a second: between messages the distance is run down by the car's own speed (vEgo),
                        for at most one period, so the pill drains smoothly instead of in ~30 m steps.
  carState              vEgo, standstill: never at a standstill.
A limit LOWER or HIGHER than the one now in force, once it is within 15 s of travel or 500 m (next_window_m). While the
same change is coming its distance only counts down - unless mapd's figure comes back up by more than one period's
run-down plus APPROACH_JUMP_M, which is news (the map matched it elsewhere), taken as it is.
When the look-ahead stops giving it, what happens depends on how far away it was (the last distance shown, run down by
vEgo since):
  REACHED - within APPROACH_NEAR_S of travel, or under sunnypilot's 'Near' (50 m, 100 ft): HELD, drawn 'now', until the
            resolver's limit becomes it (the hand-over: the confirm prompt or the stock MAX number takes the same place),
            for at most APPROACH_HOLD_S after the look-ahead ended: on route 10f mapd stopped publishing the 100 at 773.75
            and MAX 100 came at 774.8, a second with nothing on screen.
  further out - a 1 Hz message missed, the window shrinking as the car slows: the distance keeps running down, drawn as
            a distance, for at most APPROACH_GAP_S; it becomes held if it reaches the change in that time, and goes if
            not. Never a jump to 'now' (route 10f t 196.7-202.2, braking for a red light with the 70 still 40-65 m off:
            'now' for 8 s for a limit that never came, before round 5's fix round 1).

COMPACT ALERTS (HudCompactLimitPrompts / HudCompactDisengage / HudCompactTurn, drawn by hud_alerts.py): an alert is drawn
small only when its FULL ALERT TYPE (event name AND event type, COMPACT_TYPES) is in an enabled group below AND it is
AlertStatus.normal AND VisualAlert.none AND it is not an 'openpilot Unavailable'. Anything else - any critical or
userPrompt alert, any 'take control' or steer-required one, AEB / FCW, a no-entry ('openpilot unavailable', which is
itself normal / LOW / none, so the status test alone would not stop it), the UI's own 'system unresponsive' alerts (no
event name), an alert upstream adds tomorrow, under a new name or a new type of a listed one - is drawn exactly as stock.
test_hud_cluster walks every event, proves no listed one can be anything but normal / LOW / none, and fails if a listed
name gains or loses an event type.

WHAT FEEDS THE RIGHT RAIL (RailState, drawn by hud_rail.py in the confidence ball's strip), one item at a time, a planned
stop before a curve, and what a missing message does:
  modelV2               the speed plan (velocity.x / position.x, 33 points over 10 s): it STOPS where its speed first
                        drops under 0.5 m/s, at position.x there. Also the predicted yaw rate, for the curve's direction.
  radarState            the car ahead: a lead inside the stop distance (+10 m) means the plan is stopping behind a car,
                        which the Accord's own HUD already shows - nothing is drawn. Once a stop shows, only a lead inside
                        the stop + 5 m hides it (a band, so a car parked just past the line cannot blink it).
  carControl            longActive, and
  longitudinalPlan      longitudinalPlanSource == e2e: openpilot is driving the speed AND the model's plan is what it
                        follows (the lowest accel of the candidates). Only then is the stop drawn solid white; otherwise
                        grey with a dashed line - the model's plan, which openpilot is not braking for.
  longitudinalPlanSP    smart cruise control: the curve while vision is entering/turning, or the map is turning, AND it
                        is the target the cruise speed is limited by (longitudinalPlanSource sccVision / sccMap), with
                        its vTarget - openpilot's own target speed for the curve - and only while that target is under
                        the car's speed (it appears 2 km/h under, goes 3 km/h over: the car is slowing for it).
                        The driver taking over (smart cruise control overriding, or carControl.longActive off) hides it
                        on the frame.
  carState              standstill: nothing (the stopwatch has it); vEgo, for the curve.
Missing or stale (not this drive, or no longer arriving): modelV2, radarState or carState - no stop; longitudinalPlanSP
or carState - no curve; carControl or longitudinalPlan - the stop can still be shown, but only grey. Nothing ever says
WHY the plan stops (a light, a sign, a queue the radar has not locked onto): no message knows.
The figures are held so they can be read: the stop's distance counts down freely but up only by 5 m or more, the
curve's target moves in 5 km/h (5 mph) steps with a margin before it changes.
"""
import math
from dataclasses import dataclass

from openpilot.common.constants import CV
from openpilot.cereal import custom, log
from opendbc.car.structs import car
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import real_lead
# FORK(SPEED-LIMIT): push9 - the coming limit's state machine is shared with the planner, which takes a press during
# 'now' as the confirm (speed_limit/now_confirm.py): ApproachState below is its ComingLimit fed from the UI's SubMaster,
# so the card and the planner cannot disagree about 'now'. The geometry and the confirm's target live there too.
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.now_confirm import (  # noqa: F401 - re-exported
  APPROACH_GAP_S, APPROACH_HOLD_S, APPROACH_JUMP_M, APPROACH_NEAR_S, CRUISE_MAX_KPH, CRUISE_MIN_KPH, KM_TO_MILE,
  MAP_EXTRAPOLATE_MAX_S, NEAR_FT, NEAR_M, NEXT_MAX_M, NEXT_MAX_S, NSW_NO_LOOKAHEAD, OFFSET_FIXED, OFFSET_PERCENT,
  Approach, ComingLimit, confirm_target, cruise_min_kph, look_ahead, next_window_m, reached)

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source
PlanSource = log.LongitudinalPlan.LongitudinalPlanSource
PlanSourceSP = custom.LongitudinalPlanSP.LongitudinalPlanSource
VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.VisionState
MapState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState
AlertStatus = log.SelfdriveState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert

TIMER_DELAY_S = 1.0     # a standstill this long before the stopwatch shows
# The coming limit's constants are imported above from speed_limit/now_confirm.py (push9), which the planner shares:
# NEXT_MAX_M / NEXT_MAX_S (500 m, 15 s: the window it shows from, next_window_m), MAP_EXTRAPOLATE_MAX_S (1 s:
# liveMapDataSP is 1 Hz, the distance is run down for at most one period, then held), APPROACH_HOLD_S (3 s: once the
# look-ahead ends at the change, the coming limit is held as 'now' this long at most for the resolver to take it -
# mapd's last look-ahead to the limit in force took 1.05 s on route 10f, and plannerd's 1 s stability hold,
# speed_limit/change_hold.py, comes on top), APPROACH_NEAR_S (2 s: the change is REACHED once the distance left is
# within this much travel ...), NEAR_M / NEAR_FT (... or under sunnypilot's 'Near', SpeedLimitRenderer._format_dist:
# 50 m / 100 ft, which the pill draws 'now'), APPROACH_GAP_S (2 s: the look-ahead lost further out, the distance runs
# down by vEgo this long at most, then it goes), APPROACH_JUMP_M (10 m: mapd's figure coming back up by one period's
# run-down + this is noise, held; more is news), NSW_NO_LOOKAHEAD and reached().

STANDSTILL_EVENT = 'manualRestart'
LEAD_DEPART_MS = 1.0    # m/s: the car ahead is moving off ...
LEAD_DEPART_S = 0.4     # ... continuously for this long (radarState is 20 Hz: 8 frames), so radar noise cannot do it

# the compact confirm's card outline, from the frame it is first half visible: it flashes solid BOX_BLINKS times,
# BOX_BLINK_ON_S lit and BOX_BLINK_OFF_S not - 2.8 flashes a second, under the 3 Hz photosensitivity line - then, while
# the prompt waits, the split card's dashes march clockwise at BOX_MARCH_PX_S and the stock-plus card's outline breathes
# at BREATH_HZ. The digits never blink.
BOX_BLINKS = 2
BOX_BLINK_ON_S = 0.18
BOX_BLINK_OFF_S = 0.18
BOX_MARCH_PX_S = 30.0
BREATH_HZ = 1.0

# the card's own animations (MaxCard), in seconds
CARD_ENTER_S = 0.25     # split card: it opens out of its MAX half when a coming limit appears
CARD_GROW_S = 0.25      # the coming limit's card turns into the confirm's (stock-plus: grows to MAX size; split: footer)
CARD_DONE_S = 0.3       # the confirm done: the split card's target slides into MAX's place, the stock-plus card
                        # dissolves round its digits - then the stock MAX number takes over
NOW_HALO_S = 0.2        # stock-plus: 'now' lights the outline once (the split card's red bar glows BOX_BLINKS times)

# the compact alerts, by event name (hud_alerts.py draws them), each only under the one event type it has today
# (COMPACT_TYPES): a new type of a listed name - a '/noEntry' 'openpilot Unavailable', which is normal / LOW / none like
# these - would otherwise be drawn compact, under these banners' words or as nothing at all.
CONFIRM_EVENT = 'speedLimitPreActive'   # 'press + (or -) to confirm speed limit': a banner
# the set speed was changed: nothing over the road - the stock MAX number shows the new set speed, the cluster stays
COMPACT_QUIET = frozenset({'speedLimitActive', 'speedLimitChanged', 'speedLimitPending'})
COMPACT_LIMIT = COMPACT_QUIET | {CONFIRM_EVENT}
# The driver's own disengagement, which these normal alerts spell out: the cancel button ending cruise with lane
# centering kept (mads.py), the LKAS button ending lane centering with cruise on. Every other normal alert that ends or refuses
# something is not the driver's doing (speedTooLow 'openpilot Canceled', HIGH), refuses an engagement
# (pedalPressedAlertOnly, 'openpilot Unavailable'), or is a fault (lkasGatewayEpsLatchedReminder, steerUnavailable,
# accFaulted): those stay full screen. lkasDisable, buttonCancel and pcmDisable draw nothing anyway (AlertSize.none).
COMPACT_DISENGAGE = frozenset({'manualLongitudinalRequired', 'manualSteeringRequired'})
COMPACT_TURN = frozenset({'laneTurnLeft', 'laneTurnRight'})
# the one event type each listed name is raised with (events.py / sunnypilot's events.py); test_hud_cluster fails if
# either table gives a listed name any other
COMPACT_TYPES = dict.fromkeys(COMPACT_LIMIT | COMPACT_TURN | {'manualLongitudinalRequired'}, 'warning')
COMPACT_TYPES['manualSteeringRequired'] = 'userDisable'
NO_ENTRY_TEXT = 'openpilot unavailable'   # NoEntryAlert's words: never compact, whatever its type
COMPACT_NONE, COMPACT_LIMIT_KIND, COMPACT_DISENGAGE_KIND, COMPACT_TURN_KIND = 0, 1, 2, 3
# the banners' words: the stock texts, shortened to fit top left ('Smart/Adaptive Cruise Control: OFF / Manual Speed
# Control Required', 'Automatic Lane Centering is OFF / Manual Steering Required', 'Turning Left')
COMPACT_TEXT = {
  'manualLongitudinalRequired': ("CRUISE OFF", "manual speed control"),
  'manualSteeringRequired': ("LANE CENTERING OFF", "manual steering"),
  'laneTurnLeft': ("TURNING LEFT", ""),
  'laneTurnRight': ("TURNING RIGHT", ""),
}


@dataclass
class HudFrame:
  timer_s: float | None = None    # seconds stopped, when the stopwatch shows; None: nothing top right


def received(sm, service: str, started_frame: int) -> bool:
  """Received at least once since this drive started."""
  return bool(sm.seen[service]) and sm.recv_frame[service] >= started_frame


def fresh(sm, service: str, started_frame: int) -> bool:
  """Received this drive and still arriving (SubMaster's alive: within its expected period)."""
  return received(sm, service, started_frame) and bool(sm.alive[service])


def build_frame(sm, s: HudSettings, *, started_frame: int, stopped_s: float | None) -> HudFrame:
  """What the cluster shows this frame: the stop time, after TIMER_DELAY_S at a standstill. Pure: everything it reads is
  passed in."""
  f = HudFrame()
  if s.stopped_timer and received(sm, 'carState', started_frame) and sm['carState'].standstill and \
     stopped_s is not None and stopped_s >= TIMER_DELAY_S:
    f.timer_s = stopped_s
  return f


# ------------------------------------------------------------------------------------------- the coming limit
# Approach (limit, lower, dist, frac, held) and ComingLimit, the state machine, are now_confirm.py's (imported above).
def approach_text(dist_text: str, held: bool) -> str:
  """The pill's words: 'in 300 m' from sunnypilot's own rounding (SpeedLimitRenderer._format_dist), which says 'Near'
  close in - that, and a held limit, is 'now'."""
  return "now" if held or not dist_text[:1].isdigit() else f"in {dist_text}"


def resolver_map_limit(sm, started_frame: int, conv: float) -> int:
  """The limit in force as the resolver has it (display units), when it is a map limit; 0 otherwise."""
  if not fresh(sm, 'longitudinalPlanSP', started_frame):
    return 0
  res = sm['longitudinalPlanSP'].speedLimit.resolver
  if not (res.speedLimitValid or res.speedLimitLastValid) or res.source != SpeedLimitSource.map:
    return 0
  return max(0, int(round(res.speedLimitLast * conv)))


def approach_now(sm, *, started_frame: int, is_metric: bool, limit: int, map_age_s: float) -> Approach | None:
  """What mapd's look-ahead says this frame, before any hold: a limit other than `limit` (the resolver's map limit, display
  units) within the window, or None (now_confirm.look_ahead, on this SubMaster). Pure."""
  cs, lmd = sm['carState'], sm['liveMapDataSP']
  return look_ahead(limit=limit, map_ok=fresh(sm, 'liveMapDataSP', started_frame), car_ok=fresh(sm, 'carState', started_frame),
                    standstill=bool(cs.standstill), ahead_valid=bool(lmd.speedLimitAheadValid),
                    ahead_ms=float(lmd.speedLimitAhead), ahead_dist=float(lmd.speedLimitAheadDistance),
                    nsw_state=int(lmd.nswZone.state), v_ego=float(cs.vEgo), map_age_s=map_age_s, is_metric=is_metric)


SLA_MODE_ASSIST = 3     # SpeedLimitMode: assist (speed_limit/common.py Mode.assist) - the mode with the confirm


def now_prompt(limit: int, set_speed: int, *, is_metric: bool, offset_type: int = 0, offset_value: float = 0.0,
               pcm_cruise_speed: bool = True) -> str:
  """FORK(SPEED-LIMIT): push9. The card's words at 'now' while the press would be the confirm (now_confirm.py):
  'press - to set 100' when the set speed that limit gives is under the set speed, 'press + to set 100' over it; ''
  when neither (the set speed is already it). The number - and so the key - is the set speed the press gives once the
  limit is in force: the limit with the user's offset as the resolver adds it, clamped and rounded as the confirm
  stores it (now_confirm.confirm_target, which the planner's NowConfirm.press arms by). The caller decides WHETHER:
  engaged, non-PCM, Speed Limit Assist on. Pure."""
  if limit <= 0 or set_speed <= 0:
    return ""
  target = confirm_target(limit, is_metric=is_metric, offset_type=offset_type, offset_value=offset_value,
                          pcm_cruise_speed=pcm_cruise_speed)
  if target <= 0 or target == set_speed:
    return ""
  return f"press {'-' if target < set_speed else '+'} to set {target}"


class ApproachState:
  """The coming limit, frame by frame, with its run-down and hold: now_confirm.ComingLimit - the planner's 'now' is the
  same machine - fed from the UI's SubMaster, behind the card's settings. One per HUD renderer; update() is idempotent
  within a frame (the same now), as the face asks before the HUD draws."""
  def __init__(self):
    self.core = ComingLimit()

  def reset(self) -> None:
    self.core.reset()

  @property
  def frame(self) -> Approach | None:
    return self.core.frame

  def update(self, sm, s: HudSettings, *, started_frame: int, now: float, is_metric: bool,
             speed_limit_mode_on: bool) -> Approach | None:
    if not (s.approaching_limit and speed_limit_mode_on):
      self.core.reset()
      return None
    conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
    cs, lmd = sm['carState'], sm['liveMapDataSP']
    return self.core.update(now, map_frame=sm.recv_frame['liveMapDataSP'], limit=resolver_map_limit(sm, started_frame, conv),
                            map_ok=fresh(sm, 'liveMapDataSP', started_frame), car_ok=fresh(sm, 'carState', started_frame),
                            standstill=bool(cs.standstill), v_ego=float(cs.vEgo),
                            ahead_valid=bool(lmd.speedLimitAheadValid), ahead_ms=float(lmd.speedLimitAhead),
                            ahead_dist=float(lmd.speedLimitAheadDistance), nsw_state=int(lmd.nswZone.state),
                            is_metric=is_metric)


def box_outline(t: float) -> tuple[bool, float]:
  """(lit, phase) of the compact confirm's card outline t seconds after it was first half visible: lit = the outline is
  one solid line (a flash), else - the split card's dashes - drawn phase px along the outline (clockwise): 0 through the
  flashes, then marching at BOX_MARCH_PX_S. (The stock-plus card breathes instead: box_breath.)"""
  period = BOX_BLINK_ON_S + BOX_BLINK_OFF_S
  flashes = BOX_BLINKS * period
  t = max(0.0, t)
  if t < flashes:
    return (t % period) < BOX_BLINK_ON_S - 1e-9, 0.0
  return False, BOX_MARCH_PX_S * (t - flashes)


def box_breath(t: float) -> float:
  """The stock-plus card's outline, t seconds after the confirm was first half visible: 1 (full) through the flashes,
  then breathing 1 -> 0 -> 1 at BREATH_HZ while the prompt waits."""
  flashes = BOX_BLINKS * (BOX_BLINK_ON_S + BOX_BLINK_OFF_S)
  if t < flashes:
    return 1.0
  return 0.5 + 0.5 * math.cos(2 * math.pi * BREATH_HZ * (t - flashes))


def ease_out(u: float) -> float:
  u = min(1.0, max(0.0, u))
  return 1 - (1 - u) ** 3


def ease_in_out(u: float) -> float:
  u = min(1.0, max(0.0, u))
  return 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2


# ------------------------------------------------------------------------------------------- the card in MAX's place
CARD_APPROACH, CARD_PENDING, CARD_DONE = 0, 1, 2


@dataclass(frozen=True)
class MaxCard:
  """One frame of what the MAX number's place shows, decided once (HudRendererSP) and drawn by the style's drawer
  (hud_draw.split_card, D3; hud_draw.stock_plus_card, D1). The two styles draw the same states from the same fields;
  each ignores the animations it does not have.
    CARD_APPROACH  a coming limit (HudApproachingLimit): value, lower, text 'in 300 m' / 'now', frac
    CARD_PENDING   the confirm waiting for the driver: value = the set speed it would set, text 'press - to set 100'
    CARD_DONE      the confirm done (the set speed is now its target): done 0..1 turns the card into the stock MAX
                   number - the split card slides its target there, the stock-plus card dissolves round its digits"""
  kind: int
  value: int                  # the coming limit, or the set speed the confirm sets (display units)
  lower: bool | None          # lower (a down chevron, the '-' key), higher (up, '+'); None: no key
  current: str                # the set speed now, as the stock MAX number shows it ('110'; '–' with none, or while
  #                             openpilot is not engaged, when the stock MAX is not drawn at all)
  text: str = ""              # 'in 300 m', 'now', 'press - to set 100'
  note: str = ""              # 'limit 50 +5': the limit, when the target is not the limit itself
  frac: float = 0.0           # approach: what is left of the 15 s / 500 m window, 0..1
  alpha: float = 1.0
  color: tuple = (0, 0, 0)    # the confirm alert's color (normal: black)
  lit: bool = False           # pending: the outline flashing; approach 'now': the glow (split) or halo (stock-plus)
  phase: float | None = None  # pending, split card: the dashes' march (box_outline)
  breath: float = 1.0         # pending, stock-plus: the outline's breathing (box_breath)
  key_alpha: float = 1.0      # the green key's blink, sunnypilot's own arrow's
  enter: float = 1.0          # approach, split card: the card opening, 0..1
  grow: float = 1.0           # pending: turning from the coming limit's card into the confirm's, 0..1
  done: float = 0.0           # CARD_DONE: 0..1, eased
  max_right: float = math.inf  # the card's right edge stays left of this x (the stop timer, the road's edge): the
  #                              stock-plus card squeezes its row to fit (the split card is narrower than any limit)


# ------------------------------------------------------------------------------------------- alerts
def event_name(alert) -> str:
  return alert.alert_type.split('/')[0] if alert is not None and alert.alert_type else ''


def _says_unavailable(alert) -> bool:
  """A NoEntryAlert: 'openpilot Unavailable' is its first line, or on the comma 4 its second (events_base swaps them)."""
  return any(str(getattr(alert, t, '') or '').strip().lower() == NO_ENTRY_TEXT for t in ('text1', 'text2'))


def compact_kind(alert, s: HudSettings) -> int:
  """Which compact group draws this alert (COMPACT_*_KIND), or COMPACT_NONE: the stock drawing. Only a listed EVENT NAME
  under its one recorded EVENT TYPE (COMPACT_TYPES) with its group's setting on, and only while the alert is normal, asks
  for no visual and is not an 'openpilot Unavailable': a listed event raised critical, userPrompt, steer-required or as a
  no-entry by some future table is drawn as stock."""
  if alert is None or not alert.alert_type:
    return COMPACT_NONE
  name, _, event_type = alert.alert_type.partition('/')
  if COMPACT_TYPES.get(name) != event_type or _enum(alert.status) != AlertStatus.normal or \
     _enum(alert.visual_alert) != VisualAlert.none or _says_unavailable(alert):
    return COMPACT_NONE
  if s.compact_limit and name in COMPACT_LIMIT:
    return COMPACT_LIMIT_KIND
  if s.compact_disengage and name in COMPACT_DISENGAGE:
    return COMPACT_DISENGAGE_KIND
  if s.compact_turn and name in COMPACT_TURN:
    return COMPACT_TURN_KIND
  return COMPACT_NONE


def frees_top_icons(alert, s: HudSettings) -> bool:
  """Drawn as nothing at all (the set speed changed): the stock MAX number may show, as with no alert."""
  return compact_kind(alert, s) == COMPACT_LIMIT_KIND and event_name(alert) in COMPACT_QUIET


def compact_text(alert) -> tuple[str, str]:
  """(line 1, line 2) of a compact disengage or turn banner: COMPACT_TEXT, or the alert's own words."""
  return COMPACT_TEXT.get(event_name(alert), (alert.text1, alert.text2.lower()))


def confirm_text(text1: str) -> str:
  """The confirm banner's words: 'Press + to confirm speed limit' as 'press + to confirm' - the key and the sign beside
  it say which limit."""
  t = text1.lower()
  return t[:-len(" speed limit")] if t.endswith(" to confirm speed limit") else t


def confirm_prompt(text1: str, target: int) -> str:
  """The confirm's words on the comma 4 (owner, 2026-10-12): 'Press - to confirm speed limit' as 'press - to set 100' -
  the key the alert names (sunnypilot's text says the button the confirm accepts since batch 2) and the set speed that
  press gives. No target (no limit) or a text naming no key: confirm_text."""
  lower = confirm_lower(text1)
  if lower is None or target <= 0:
    return confirm_text(text1)
  return f"press {'-' if lower else '+'} to set {target}"


def prompt_parts(text: str) -> tuple[str, str] | None:
  """'press - to set 100' as ('press', 'to set 100'): the words either side of the key, for a card that draws the key
  itself in its place; None when the text names no key."""
  for key in (" - ", " + "):
    if key in text:
      before, after = text.split(key, 1)
      return before, after
  return None


def confirm_lower(text1: str) -> bool | None:
  """The direction the confirm text asks for: True '-', False '+', None neither (the PCM text)."""
  t = text1.lower()
  return True if "press -" in t else False if "press +" in t else None


def short_line2(text2: str) -> str:
  """The banner's second line: 'Resume Driving Manually' fits the banner as 'resume manually'."""
  t = text2.lower()
  return "resume manually" if t == "resume driving manually" else t


class StandstillBanner:
  """Whether the standstill prompt is drawn compact. The FULL alert comes back once the car ahead moves off (this
  drive's radarState, still arriving, with leadOne faster than LEAD_DEPART_MS for LEAD_DEPART_S) and stays full until
  the prompt clears; the next stop starts compact again.

  "Until the prompt clears" needs observe(): HudRendererSP._update_state calls it EVERY frame with the alert the renderer
  will draw (the previous one while it fades out, None when there is none). compact() alone cannot see the prompt go - the alert
  renderer stops asking the moment there is no alert - and the first departure of a drive would stick for all of it.
  Since round 5 the HUD renderer, drawn AFTER the alert renderer, calls it (the cluster did, before it); it is handed
  the frame's alert as it was before the alert renderer drew (hud_alerts.frame_alert, looked up once a frame), so it
  ends the prompt on the same frame as when the cluster called it: the frame after the fade-out finished."""
  def __init__(self):
    self.departed = False
    self._moving_since: float | None = None

  def reset(self):
    self.departed = False
    self._moving_since = None

  def observe(self, alert):
    """Every frame, with what the alert renderer will draw: anything but the standstill prompt (or nothing) ends it."""
    if event_name(alert) != STANDSTILL_EVENT:
      self.reset()

  def compact(self, alert, settings: HudSettings, sm, started_frame: int, now: float) -> bool:
    """Idempotent within a frame (the same now): the cluster and the alert renderer both ask."""
    if not settings.stopped_banner or event_name(alert) != STANDSTILL_EVENT:
      self.reset()
      return False
    lead = sm['radarState'].leadOne
    if fresh(sm, 'radarState', started_frame) and real_lead(lead) and lead.vLead > LEAD_DEPART_MS:
      if self._moving_since is None:
        self._moving_since = now
      if now - self._moving_since >= LEAD_DEPART_S:
        self.departed = True
    else:
      self._moving_since = None
    return not self.departed


def pending_limit(sm, is_metric: bool) -> tuple[int, int]:
  """(limit, offset) the speed-limit confirm asks for, in display units: the resolver's current limit and the user's
  offset, which sunnypilot's own sign shows as a small number on the sign. The confirm sets limit + offset - the
  arrow's own up/down compares the set speed with speedLimitFinalLast, which is that sum. limit 0 when there is none."""
  conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
  res = sm['longitudinalPlanSP'].speedLimit.resolver
  if res.speedLimitLast <= 0:
    return 0, 0
  return int(round(res.speedLimitLast * conv)), int(round(res.speedLimitOffset * conv))


# CRUISE_MIN_KPH, CRUISE_MAX_KPH, KM_TO_MILE and cruise_min_kph(): now_confirm.py's (imported above), which
# confirm_target - the planner's too - uses.


def target_speed(sm, is_metric: bool, pcm_cruise_speed: bool = True) -> int:
  """The set speed the speed-limit confirm would set, as the MAX number will then show it: limit + offset
  (speedLimitFinalLast) to 0.1 km/h and clamped to the cruise range, as the confirm stores it (cruise_ext.py:
  clip(round(kph, 1), v_cruise_min, V_CRUISE_MAX)), then converted and rounded as HudRenderer._draw_set_speed does.
  0 when there is no limit."""
  res = sm['longitudinalPlanSP'].speedLimit.resolver
  if res.speedLimitLast <= 0:
    return 0
  kph = min(max(round(res.speedLimitFinalLast * CV.MS_TO_KPH, 1), cruise_min_kph(is_metric, pcm_cruise_speed)),
            CRUISE_MAX_KPH)
  return max(0, int(round(kph * (1.0 if is_metric else KM_TO_MILE))))


# ------------------------------------------------------------------------------------------- the right rail
STOP_V_MS = 0.5         # the plan stops where its speed first drops under this ...
STOP_HORIZON_S = 10.0   # ... within this much of it (modelV2's plan is 33 points over 0..10 s)
STOP_MIN_M = 1.0        # a stop closer than this is here: not drawn (and at a standstill the stopwatch has it)
LEAD_MARGIN_M = 10.0    # a radar lead within the stop distance + this: the plan stops behind a car, not at a line ...
LEAD_KEEP_M = 5.0       # ... but once the stop shows, only a lead within the stop + this hides it. One threshold blinked
                        # it: route 110 t 411-413, engaged, a stationary car 8-12 m past the plan's stop went in and out
                        # of +10 m, and the countdown went 7 m, blank for 0.65 s, 4 m. (openpilot stops ~6 m behind a car)
STOP_UP_M = 5.0         # the stop's figure follows the plan down freely, but goes UP only for a jump of this or more
CURVE_V_MAX_MS = 70.0   # a curve target above this is not one (smart cruise control's 'unset' is 255 m/s)
CURVE_DIR_MIN = 0.3     # m/s^2: below this predicted lateral accel the model shows no curve to point the arrow at
CURVE_UNDER_MS = 2.0 / 3.6  # the curve appears once its target is this far under the car's speed (it is slowing for it)
CURVE_OVER_MS = 3.0 / 3.6   # ... and goes once the target is this far over it: route 10f t 2050, on the motorway, it
                            # showed '87 km/h' at 83 km/h through a lane change while the car sped up
CURVE_STEP = 5          # the curve's figure in 5 km/h (5 mph) steps, as Australian curve advisory signs are ...
CURVE_HOLD = 1.0        # ... and it changes only once the target is this far past the half step (display units)
RAIL_ON_S = 0.3         # an item shows once its condition has held this long ...
RAIL_OFF_S = 0.5        # ... and goes once it has been false this long, so neither flickers
STOP_DIST_TAU_S = 0.15  # the distance and the target speed are smoothed a little: the plan is re-solved 20 times a second
CURVE_V_TAU_S = 0.25
M_TO_FT = 3.28084

RAIL_NONE, RAIL_STOP, RAIL_CURVE = 0, 1, 2
T_IDXS = tuple(float(t) for t in ModelConstants.T_IDXS)


@dataclass
class RailFrame:
  kind: int = RAIL_NONE
  stop_m: float = 0.0       # RAIL_STOP: meters to where the plan stops, as the figure shows it (stop_figure)
  solid: bool = False       # RAIL_STOP: openpilot is driving the speed on the model's plan (else: grey, dashed)
  curve_v: float = 0.0      # RAIL_CURVE: m/s, openpilot's target speed for the curve (smoothed, not stepped)
  curve_left: bool = False  # RAIL_CURVE: the curve turns left
  num: str = ""             # the figures as drawn ('14', '35') ...
  unit: str = ""            # ... and their unit ('m', 'km/h'), in the units the cluster uses


def _enum(v) -> int:
  """A capnp enum field as its number (it compares with ints anyway; this keeps `in` and dict lookups plain)."""
  return int(getattr(v, 'raw', v))


def plan_stop_m(model) -> float | None:
  """Where the model's speed plan comes to a stop: position.x at the first point whose velocity.x is under STOP_V_MS,
  within STOP_HORIZON_S. None when it does not stop, or the plan is not the full 33 points."""
  vx, px = list(model.velocity.x), list(model.position.x)
  if len(vx) != len(T_IDXS) or len(px) != len(T_IDXS):
    return None
  for t, v, x in zip(T_IDXS, vx, px, strict=True):
    if t > STOP_HORIZON_S:
      break
    if v < STOP_V_MS:
      return float(x)
  return None


def curve_left(model) -> bool | None:
  """Which way the curve goes: the sign of the model's predicted yaw rate where its predicted lateral acceleration
  (|orientationRate.z| x velocity.x, what smart cruise control slows for) peaks. The model's frame has z DOWN, so a
  positive yaw rate is a RIGHT turn - on route 10f the left curve at t 330 ran orientationRate.z -0.25 with the wheel at
  +58 deg, and the lane change right at t 392 ran +0.005 with the wheel at -10. None when there is no curve to see."""
  rate, vel = list(model.orientationRate.z), list(model.velocity.x)
  if not rate or len(rate) != len(vel):
    return None
  i = max(range(len(rate)), key=lambda k: abs(rate[k] * vel[k]))
  if abs(rate[i] * vel[i]) < CURVE_DIR_MIN:
    return None
  return rate[i] < 0.0


def curve_target(sm, started_frame: int) -> float | None:
  """openpilot's target speed (m/s) for a curve it is slowing for or about to, else None. Smart cruise control, while
  it is the target that limits the cruise speed (longitudinalPlanSP.longitudinalPlanSource): vision entering or turning
  (not leaving - that is speeding up again), or the map turning."""
  if not fresh(sm, 'longitudinalPlanSP', started_frame):
    return None
  p = sm['longitudinalPlanSP']
  scc, src = p.smartCruiseControl, _enum(p.longitudinalPlanSource)
  v = None
  if src == PlanSourceSP.sccVision and scc.vision.active and _enum(scc.vision.state) in (VisionState.entering,
                                                                                         VisionState.turning):
    v = float(scc.vision.vTarget)
  elif src == PlanSourceSP.sccMap and scc.map.active and _enum(scc.map.state) == MapState.turning:
    v = float(scc.map.vTarget)
  return v if v is not None and 0.0 < v < CURVE_V_MAX_MS else None


def driver_has_speed(sm, started_frame: int) -> bool:
  """The driver has taken the speed back: smart cruise control says it is overriding (the gas), or openpilot is not
  driving the speed any more (carControl.longActive off: the brake, a disengagement). Only what fresh messages say."""
  if fresh(sm, 'carControl', started_frame) and not sm['carControl'].longActive:
    return True
  if fresh(sm, 'longitudinalPlanSP', started_frame):
    scc = sm['longitudinalPlanSP'].smartCruiseControl
    return _enum(scc.vision.state) == VisionState.overriding or _enum(scc.map.state) == MapState.overriding
  return False


def stop_figure(d: float, prev: float | None, rising: bool = False) -> tuple[float, bool]:
  """(the distance the stop's figure shows (m), rising). It follows the (smoothed) plan down freely, but goes UP only
  for a jump of STOP_UP_M or more - and then all the way up, for as long as the distance keeps rising (rising=True),
  not stuck where the smoothing was when it crossed the 5 m. The plan is re-solved 20 times a second; 10-9-10 or
  8-7-6-7-8 m (route 10f, the queue at t 2676) is noise, not news."""
  if prev is None or d < prev:
    return d, False
  if rising or d >= prev + STOP_UP_M:
    return d, True
  return prev, False


def curve_figure(v: float, is_metric: bool, prev: int | None) -> int:
  """The curve's target speed as drawn, in display units: CURVE_STEP steps, as curve advisory signs are, and held until
  the target is CURVE_HOLD past the half step - smart cruise control's target is re-solved 20 times a second, and on
  route 10f (t 336) the whole-unit figure changed up to 8.6 times a second, 58-57-58-57."""
  x = v * (CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH)
  if prev is not None and abs(x - prev) <= CURVE_STEP / 2 + CURVE_HOLD:
    return prev
  return max(CURVE_STEP, CURVE_STEP * math.floor(x / CURVE_STEP + 0.5))


def fmt_stop_dist(d: float, is_metric: bool) -> tuple[str, str]:
  """(figures, unit) for the stop distance: whole meters under 20 m (a 5 m step drew 12.7 m as '15'), then 5 m steps,
  10 m steps from 100 m; imperial in feet (whole under 30 ft, then 10 ft steps, 50 ft from 300 ft) and miles from
  3000 ft."""
  if is_metric:
    if d < 19.5:
      return str(max(1, int(round(d)))), "m"
    if d < 97.5:
      return str(int(5 * round(d / 5))), "m"
    if d < 995:
      return str(int(10 * round(d / 10))), "m"
    return f"{d / 1000:.1f}", "km"
  ft = d * M_TO_FT
  if ft < 29.5:
    return str(max(1, int(round(ft)))), "ft"
  if ft < 295:
    return str(int(10 * round(ft / 10))), "ft"
  if ft < 2975:
    return str(int(50 * round(ft / 50))), "ft"
  return f"{ft / 5280:.1f}", "mi"


def speed_unit(is_metric: bool) -> str:
  return "km/h" if is_metric else "mph"


class Debounce:
  """Follows a condition: True once it has held for on_s, False once it has been false for off_s."""
  def __init__(self, on_s: float, off_s: float):
    self.on_s, self.off_s = on_s, off_s
    self.state = False
    self._since: float | None = None

  def reset(self):
    self.state = False
    self._since = None

  def update(self, raw: bool, now: float) -> bool:
    if raw == self.state:
      self._since = None
    else:
      if self._since is None:
        self._since = now
      if now - self._since >= (self.on_s if raw else self.off_s) - 1e-9:
        self.state, self._since = raw, None
    return self.state


def _smooth(prev: float | None, x: float, dt: float, tau: float) -> float:
  return x if prev is None else prev + (x - prev) * (1.0 - math.exp(-max(dt, 0.0) / tau))


class RailState:
  """The right rail's item this frame: update() every frame, on a steady clock. It holds the debounces, the smoothing
  and the figures' holds, so there is one per view (hud_rail.HudRail).

  HARD hides, on the frame: the setting off, a standstill, a message missing, a stop closer than STOP_MIN_M, and for the
  curve the driver taking the speed back. Everything else - the plan no longer stopping inside 10 s, a lead appearing in
  front of the stop, smart cruise control leaving its curve states or its target rising over the car's speed - goes
  through the debounce: on after RAIL_ON_S, off after RAIL_OFF_S (drawn with its last value)."""
  def __init__(self):
    self._t: float | None = None
    self._stop_on = Debounce(RAIL_ON_S, RAIL_OFF_S)
    self._e2e_on = Debounce(RAIL_ON_S, RAIL_OFF_S)
    self._curve_on = Debounce(RAIL_ON_S, RAIL_OFF_S)
    self._stop_m: float | None = None     # the plan's stop distance, smoothed
    self._stop_fig: float | None = None   # ... and as the figure shows it (stop_figure) ...
    self._stop_rising = False             # ... while it climbs after a jump of 5 m or more
    self._curve_v: float | None = None
    self._curve_fig: int | None = None    # the curve's figure (curve_figure), in ...
    self._curve_fig_metric = True         # ... these units
    self._curve_left = False

  def _stop_off(self):
    self._stop_on.reset()
    self._stop_m = self._stop_fig = None
    self._stop_rising = False

  def _curve_off(self):
    self._curve_on.reset()
    self._curve_v = self._curve_fig = None

  def _stop(self, sm, started_frame: int, now: float, dt: float) -> float | None:
    if not (fresh(sm, 'modelV2', started_frame) and fresh(sm, 'radarState', started_frame)):
      self._stop_off()
      return None
    d = plan_stop_m(sm['modelV2'])
    if d is not None and d < STOP_MIN_M:
      self._stop_off()
      return None
    lead = sm['radarState'].leadOne
    margin = LEAD_KEEP_M if self._stop_on.state else LEAD_MARGIN_M
    raw = d is not None and not (real_lead(lead) and lead.dRel <= d + margin)  # a held lead's dRel is made up
    if raw:
      self._stop_m = _smooth(self._stop_m, d, dt, STOP_DIST_TAU_S)
      self._stop_fig, self._stop_rising = stop_figure(self._stop_m, self._stop_fig, self._stop_rising)
    if self._stop_on.update(raw, now):
      return self._stop_fig
    if not raw:
      self._stop_m = self._stop_fig = None
      self._stop_rising = False
    return None

  def _curve(self, sm, started_frame: int, now: float, dt: float, v_ego: float, is_metric: bool) -> float | None:
    if driver_has_speed(sm, started_frame):
      self._curve_off()
      return None
    v = curve_target(sm, started_frame)
    if v is not None and v > v_ego + (CURVE_OVER_MS if self._curve_on.state else -CURVE_UNDER_MS):
      v = None  # not slower than the car: openpilot is not slowing for it
    if v is not None:
      self._curve_v = _smooth(self._curve_v, v, dt, CURVE_V_TAU_S)
      if self._curve_fig_metric != is_metric:
        self._curve_fig = None
      self._curve_fig, self._curve_fig_metric = curve_figure(self._curve_v, is_metric, self._curve_fig), is_metric
      left = curve_left(sm['modelV2']) if fresh(sm, 'modelV2', started_frame) else None
      if left is not None:
        self._curve_left = left
    if self._curve_on.update(v is not None, now):
      return self._curve_v
    if v is None:
      self._curve_v = self._curve_fig = None
    return None

  def update(self, sm, s: HudSettings, *, started_frame: int, now: float, is_metric: bool = True) -> RailFrame:
    dt = 0.0 if self._t is None else now - self._t
    self._t = now
    moving = fresh(sm, 'carState', started_frame) and not sm['carState'].standstill

    # openpilot driving the speed on the model's plan: the plan source settles like the items do; longActive counts on
    # the frame, so braking out of it grays the stop at once
    e2e = fresh(sm, 'longitudinalPlan', started_frame) and \
      _enum(sm['longitudinalPlan'].longitudinalPlanSource) == PlanSource.e2e
    in_control = self._e2e_on.update(e2e, now)
    long_active = fresh(sm, 'carControl', started_frame) and bool(sm['carControl'].longActive)

    stop = None
    if s.planned_stop and moving:
      stop = self._stop(sm, started_frame, now, dt)
    else:
      self._stop_off()

    curve = None
    if s.curve and moving:
      curve = self._curve(sm, started_frame, now, dt, max(0.0, float(sm['carState'].vEgo)), is_metric)
    else:
      self._curve_off()

    if stop is not None:
      num, unit = fmt_stop_dist(stop, is_metric)
      return RailFrame(kind=RAIL_STOP, stop_m=stop, solid=long_active and in_control, num=num, unit=unit)
    if curve is not None:
      if self._curve_fig is None or self._curve_fig_metric != is_metric:  # the units changed while it was held
        self._curve_fig, self._curve_fig_metric = curve_figure(curve, is_metric, None), is_metric
      return RailFrame(kind=RAIL_CURVE, curve_v=curve, curve_left=self._curve_left, num=str(self._curve_fig),
                       unit=speed_unit(is_metric))
    return RailFrame()
