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
"""
from dataclasses import dataclass

from openpilot.common.constants import CV
from openpilot.cereal import custom
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings, NEXT_OFF

SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

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


@dataclass
class HudFrame:
  speed: int | None = None        # display units; None = no digits
  timer_s: float | None = None    # seconds stopped, when the stopwatch replaces the speed
  sign_slot: bool = False         # the sign's place is kept (SpeedLimitMode is not off)
  limit: int = 0                  # display units; 0 = no sign
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
  elif s.speed_cluster and not max_visible:
    f.speed = int(round(v_ego * conv))

  if not s.speed_cluster:
    return f

  f.sign_slot = speed_limit_mode_on
  res = None
  if speed_limit_mode_on and fresh(sm, 'longitudinalPlanSP', started_frame):
    res = sm['longitudinalPlanSP'].speedLimit.resolver
    if res.speedLimitValid or res.speedLimitLastValid:
      limit = int(round(res.speedLimitLast * conv))
      if limit > 0:
        f.limit = limit
        f.limit_held = not res.speedLimitValid
  if not f.limit or not fresh(sm, 'liveMapDataSP', started_frame):
    return f

  lmd = sm['liveMapDataSP']
  z = lmd.nswZone
  from_map = res is not None and res.source == SpeedLimitSource.map
  nsw_shown = (from_map and int(z.mode) == NSW_MODE_LIVE and int(z.state) in NSW_STATES_SHOWN
               and z.speedLimit > 0 and int(round(z.speedLimit * conv)) == f.limit)
  if nsw_shown and s.variable_sign and z.variable:
    f.electronic = True
  if nsw_shown and s.school_cue and int(z.schoolZone) in (SCHOOL_ACTIVE, SCHOOL_INACTIVE):
    f.school = int(z.schoolZone)

  if s.next_limit != NEXT_OFF and not standstill and from_map and lmd.speedLimitAheadValid:
    ahead = int(round(lmd.speedLimitAhead * conv))
    run_down = max(0.0, float(cs.vEgo)) * min(max(map_age_s, 0.0), MAP_EXTRAPOLATE_MAX_S)
    dist = float(lmd.speedLimitAheadDistance) - run_down
    window = next_window_m(v_ego)
    if 0 < ahead < f.limit and 0 < dist <= window:
      f.next_limit = ahead
      f.next_dist = dist
      f.next_frac = dist / window
  return f


# ------------------------------------------------------------------------------------------- alerts
def event_name(alert) -> str:
  return alert.alert_type.split('/')[0] if alert is not None and alert.alert_type else ''


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
