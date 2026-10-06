"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): what the comma 4 HUD shows, decided from the messages - no drawing here (hud_cluster.py and hud_alerts.py
draw it), so every rule is testable without a window.

WHAT FEEDS THE CLUSTER, and what it does when that is missing:
  carState              speed (vEgoCluster once it has been non-zero, as the stock HUD does: the Accord's own
                        speedometer reading), standstill. None received this drive: nothing is drawn.
  longitudinalPlanSP    the resolver's limit (speedLimitLast while valid or last-valid; last-valid only = "held",
                        drawn with a grey legend as sunnypilot's own sign does). Missing or stale: no sign.
  liveMapDataSP         the next limit (speedLimitAhead*, exactly what mapd published - never nswZone's own look-ahead,
                        which mapd deliberately does not publish while dead reckoning in a tunnel) and nswZone (school
                        zone, Variable zone). Missing or stale: no next limit, no school cue, the plain white sign.
                        It arrives once a second: between messages the next limit's distance is run down by the car's
                        own speed (vEgo), for at most one period, so the bar shrinks smoothly instead of in ~30 m steps.
The next limit is shown only while the limit on screen comes from the map (source map, as sunnypilot's own sign does):
a car-sourced limit is never paired with a map look-ahead.
The school cue and the electronic sign are shown only when the limit on screen IS the NSW limit mapd published (live
mode, source map, the same number, nswZone.state matched or dead reckoning): in log-only mode, where the resolver holds
another value, or in an ambiguous or unmatched state, nswZone describes a limit that is not the one shown.
WHETHER THE SIGN IS DRAWN (HudLimitSign): always (whenever there is a limit), off, or only in a zone - a school zone that
is on, or a Variable zone, by those same rules and whatever the two cue settings say (they only style a sign that is
shown). Zone matching flickers at limit changes (route 10f: one Variable zone broke into 10 pieces, gaps of 2-58 s), so
the zone is debounced (SignSlot): on after SIGN_ZONE_ON_S, off after SIGN_ZONE_OFF_S. Without the sign the limit is still
known: the next lower limit is still shown, and the speed takes the sign's place.
WHETHER THE SPEED IS DRAWN (HudCurrentSpeed): off, the live speed is never drawn and nothing else changes - the sign,
the next lower limit and the stop time keep their places (they never depended on the digits), and the stop time still
replaces the speed at a standstill (its own setting, HudStoppedTimer).

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
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings, NEXT_OFF, SIGN_ALWAYS, SIGN_ZONES

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source
PlanSource = log.LongitudinalPlan.LongitudinalPlanSource
PlanSourceSP = custom.LongitudinalPlanSP.LongitudinalPlanSource
VisionState = custom.LongitudinalPlanSP.SmartCruiseControl.VisionState
MapState = custom.LongitudinalPlanSP.SmartCruiseControl.MapState
AlertStatus = log.SelfdriveState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert

NEXT_MAX_M = 500.0      # the next lower limit shows from the earlier of 500 m ...
NEXT_MAX_S = 15.0       # ... or 15 s of travel at the current speed
TIMER_DELAY_S = 1.0     # a standstill this long before the stopwatch replaces the speed
MAP_EXTRAPOLATE_MAX_S = 1.0  # liveMapDataSP is 1 Hz: the distance is run down for at most one period, then held

NSW_MODE_LIVE = 2
NSW_STATES_SHOWN = (2, 4)  # nswZone.state: 2 matched, 4 dead reckoning (the tunnel line's limit is published)
SCHOOL_NONE = 0         # nswZone.schoolZone: 0 none, 1 inactive, 2 active, 3 unknown (nothing is published then)
SCHOOL_INACTIVE = 1
SCHOOL_ACTIVE = 2

STANDSTILL_EVENT = 'manualRestart'
LEAD_DEPART_MS = 1.0    # m/s: the car ahead is moving off ...
LEAD_DEPART_S = 0.4     # ... continuously for this long (radarState is 20 Hz: 8 frames), so radar noise cannot do it

SIGN_ZONE_ON_S = 0.3    # HudLimitSign = zones: the sign appears once the zone has held this long ...
SIGN_ZONE_OFF_S = 12.0  # ... and goes once it has been gone this long - the gaps in one zone on route 10f were 2-19 s,
                        # bar one of 58 s

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
  speed: int | None = None        # display units; None = no digits
  timer_s: float | None = None    # seconds stopped, when the stopwatch replaces the speed
  sign_slot: bool = False         # the sign's place is kept (SpeedLimitMode is not off; HudLimitSign)
  sign_shown: bool = False        # the sign is drawn: a limit, and HudLimitSign says so
  zone: bool = False              # a NSW school zone that is on, or a Variable zone, and it is the limit shown
  limit: int = 0                  # display units; 0 = none (the next lower limit needs one, the sign drawn or not)
  limit_held: bool = False        # the resolver's last limit, not a current one
  electronic: bool = False
  school: int = SCHOOL_NONE
  next_limit: int = 0             # 0 = no next row
  next_dist: float = 0.0          # m
  next_frac: float = 0.0          # what is left of the approach window, 0..1
  next_mode: int = NEXT_OFF

  @property
  def visible(self) -> bool:
    return self.speed is not None or self.timer_s is not None or self.limit > 0


def next_window_m(v_ego: float) -> float:
  """How far ahead a next lower limit is shown: the earlier of 15 s of travel and 500 m."""
  return min(NEXT_MAX_M, max(v_ego, 1.0) * NEXT_MAX_S)


def received(sm, service: str, started_frame: int) -> bool:
  """Received at least once since this drive started."""
  return bool(sm.seen[service]) and sm.recv_frame[service] >= started_frame


def fresh(sm, service: str, started_frame: int) -> bool:
  """Received this drive and still arriving (SubMaster's alive: within its expected period)."""
  return received(sm, service, started_frame) and bool(sm.alive[service])


def build_frame(sm, s: HudSettings, *, started_frame: int, is_metric: bool, speed_limit_mode_on: bool,
                stopped_s: float | None, v_ego_cluster_seen: bool, max_visible: bool, map_age_s: float = 0.0) -> HudFrame:
  """What the cluster shows this frame. Pure: everything it reads is passed in. map_age_s = seconds since the
  liveMapDataSP in sm arrived."""
  f = HudFrame(next_mode=s.next_limit)
  if not (s.speed_cluster or s.stopped_timer):
    return f
  if not received(sm, 'carState', started_frame):
    return f

  conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
  cs = sm['carState']
  v_ego = max(0.0, float(cs.vEgoCluster) if v_ego_cluster_seen else float(cs.vEgo))
  standstill = bool(cs.standstill)

  if s.stopped_timer and standstill and stopped_s is not None and stopped_s >= TIMER_DELAY_S:
    f.timer_s = stopped_s
  elif s.speed_cluster and s.current_speed and not max_visible:
    f.speed = int(round(v_ego * conv))

  if not s.speed_cluster:
    return f

  _limit_and_zone(f, sm, s, cs, conv, v_ego, standstill, started_frame=started_frame,
                  speed_limit_mode_on=speed_limit_mode_on, map_age_s=map_age_s)
  if s.limit_sign == SIGN_ALWAYS:
    f.sign_slot, f.sign_shown = speed_limit_mode_on, f.limit > 0
  elif s.limit_sign == SIGN_ZONES:
    f.sign_slot = f.sign_shown = f.zone  # undebounced: SignSlot.apply does that
  return f


def _limit_and_zone(f: HudFrame, sm, s: HudSettings, cs, conv: float, v_ego: float, standstill: bool, *,
                    started_frame: int, speed_limit_mode_on: bool, map_age_s: float) -> None:
  res = None
  if speed_limit_mode_on and fresh(sm, 'longitudinalPlanSP', started_frame):
    res = sm['longitudinalPlanSP'].speedLimit.resolver
    if res.speedLimitValid or res.speedLimitLastValid:
      limit = int(round(res.speedLimitLast * conv))
      if limit > 0:
        f.limit = limit
        f.limit_held = not res.speedLimitValid
  if not f.limit or not fresh(sm, 'liveMapDataSP', started_frame):
    return

  lmd = sm['liveMapDataSP']
  z = lmd.nswZone
  from_map = res is not None and res.source == SpeedLimitSource.map
  nsw_shown = (from_map and int(z.mode) == NSW_MODE_LIVE and int(z.state) in NSW_STATES_SHOWN
               and z.speedLimit > 0 and int(round(z.speedLimit * conv)) == f.limit)
  if nsw_shown and s.variable_sign and z.variable:
    f.electronic = True
  if nsw_shown and s.school_cue and int(z.schoolZone) in (SCHOOL_ACTIVE, SCHOOL_INACTIVE):
    f.school = int(z.schoolZone)
  f.zone = nsw_shown and (bool(z.variable) or int(z.schoolZone) == SCHOOL_ACTIVE)

  if s.next_limit != NEXT_OFF and not standstill and from_map and lmd.speedLimitAheadValid:
    ahead = int(round(lmd.speedLimitAhead * conv))
    run_down = max(0.0, float(cs.vEgo)) * min(max(map_age_s, 0.0), MAP_EXTRAPOLATE_MAX_S)
    dist = float(lmd.speedLimitAheadDistance) - run_down
    window = next_window_m(v_ego)
    if 0 < ahead < f.limit and 0 < dist <= window:
      f.next_limit = ahead
      f.next_dist = dist
      f.next_frac = dist / window


class SignSlot:
  """HudLimitSign = zones: the sign, and the place it keeps, follow the zone debounced - on after SIGN_ZONE_ON_S, off
  after SIGN_ZONE_OFF_S - so the speed does not move every time the zone match blinks. One per cluster; apply() every
  frame, on a steady clock. The other modes pass through untouched."""
  def __init__(self):
    self._on = Debounce(SIGN_ZONE_ON_S, SIGN_ZONE_OFF_S)

  def apply(self, f: HudFrame, s: HudSettings, now: float) -> HudFrame:
    if not s.speed_cluster or s.limit_sign != SIGN_ZONES:
      self._on.reset()
      return f
    f.sign_slot = self._on.update(f.zone, now)
    f.sign_shown = f.sign_slot and f.limit > 0
    return f


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


def confirm_pending(alert, s: HudSettings) -> bool:
  """The compact confirm prompt is up and shows the limit it asks about (HudConfirmLimit): the cluster's sign is that
  limit, drawn pending (a dashed ring)."""
  return s.confirm_limit and compact_kind(alert, s) == COMPACT_LIMIT_KIND and event_name(alert) == CONFIRM_EVENT


def compact_text(alert) -> tuple[str, str]:
  """(line 1, line 2) of a compact disengage or turn banner: COMPACT_TEXT, or the alert's own words."""
  return COMPACT_TEXT.get(event_name(alert), (alert.text1, alert.text2.lower()))


def confirm_text(text1: str) -> str:
  """The confirm banner's words: 'Press + to confirm speed limit' as 'press + to confirm' - the key and the sign beside
  it say which limit."""
  t = text1.lower()
  return t[:-len(" speed limit")] if t.endswith(" to confirm speed limit") else t


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

  "Until the prompt clears" needs observe(): HudCluster calls it EVERY frame with the alert the renderer will draw (the
  previous one while it fades out, None when there is none). compact() alone cannot see the prompt go - the alert
  renderer stops asking the moment there is no alert - and the first departure of a drive would stick for all of it."""
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
    if fresh(sm, 'radarState', started_frame) and lead.present and lead.vLead > LEAD_DEPART_MS:
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


def target_speed(sm, is_metric: bool) -> int:
  """The set speed the speed-limit confirm would set, in display units: limit + offset (speedLimitFinalLast), rounded
  exactly as sunnypilot's arrow and the alert's own '+' / '-' round it (speed_limit.py, events.py), so the number and the
  key can never disagree. 0 when there is no limit."""
  res = sm['longitudinalPlanSP'].speedLimit.resolver
  if res.speedLimitLast <= 0:
    return 0
  conv = CV.MS_TO_KPH if is_metric else CV.MS_TO_MPH
  return max(0, int(round(res.speedLimitFinalLast * conv)))


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


def fmt_speed(v: float, is_metric: bool) -> tuple[str, str]:
  """(figures, unit) for a target speed in m/s, in the units the cluster uses."""
  return (str(int(round(v * CV.MS_TO_KPH))), "km/h") if is_metric else (str(int(round(v * CV.MS_TO_MPH))), "mph")


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
    raw = d is not None and not (lead.present and lead.dRel <= d + margin)
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
