"""
FORK(HONDA_ACCORD_9G_AU): radard's clutter guard for this car's Elesys/Nidec radar (HONDA_ELESYS only).

Why. Since the radar's REL_SPEED is read at its true scale (opendbc 83c8b5b0, 2026-10-09) a stationary return reads
vLead = 0 instead of vEgo/2, and two unchanged upstream paths in radard can make such a return the lead:
  * the camera match: `vel_sane` (radard.py match_vision_to_track) passes any track within 10 m/s of the camera lead's
    speed, so below about 36 km/h a stationary return near a moving camera lead is matched to it (route c8
    t=2320.4-2321.1: returns at 5-11 m matched to a camera lead doing 6-7 m/s, a false FCW in the replay);
  * the low-speed override: below 4 m/s radard takes the closest track within 1 m of the path and 0.75-25 m ahead
    with no camera and no speed check (route 10f t=2740-2745: near-range returns at 4-8 m whose distance closes and
    jumps back, with the camera seeing nothing it trusts).

What. Only on HONDA_ELESYS, and only where radard picks a radar track:
  * a track slower than V_CHECKED may stand for the camera's lead only if the camera does not say the lead is faster
    by more than speed_tolerance(). Tracks at or above V_CHECKED are left to upstream (vel_sane already passes them),
    and a track FASTER than the camera is never rejected: the guard only removes leads that would brake harder than
    the camera asks;
  * the low-speed override takes a MOVING track as upstream does. Moving is counted in radar updates (the radar
    reports at 10 Hz, radard runs at 20 Hz and sees each reading twice) and must be backed by the track's own range:
    its range plus the car's travel (its point in the world) must move away at V_WORLD or more and at least
    WORLD_RATIO of its radar speed. A track is latched as moving, for the rest of its life (a vehicle that stops stays
    a vehicle), once its raw vLead has read V_MOVING for MOVING_UPDATES updates in a row and WORLD_SPAN of range
    history agrees. It is moving now while both its vLeadK and its raw vLead have read V_MOVING_NOW and V_MOVING for
    MOVING_NOW_UPDATES updates and its range history agrees; a track younger than NOW_SPAN (a cut-in) is taken on its
    speed alone. Any other track the override takes only when the camera has a confident lead at a consistent
    distance (OVERRIDE_D_TOL) or speed (OVERRIDE_V_TOL). Confident means radard's filtered prob rose above CAMERA_ON
    and has not since fallen to CAMERA_OFF (radard's own 0.5);
  * a track the override refuses still HOLDS the plan when radard has no lead at all: the closest one seen for
    HOLD_UPDATES updates becomes a lead at the car's own speed, not accelerating, at the MPC's desired distance for
    that speed on its shortest time gap (hold_lead()). The MPC then neither accelerates toward it nor brakes for it
    as for a stopped car; the planner takes the lower of the MPC and e2e, so the plan stops accelerating. Below the
    car's stopping speed (0.8 m/s) holding speed is a stop: the planner asks to stop and, from standstill, does not
    launch while the track stays held, whatever its range. The held lead is marked (radarTrackId -2 - the track's id,
    held_lead.py): it is a lead for the MPC only, and DEC, longitudinalPlan.hasLead, the e2e alerts and the UI skip it.
A stopped car the camera also sees as stopped or slow is kept, at any range.

Given up (the camera is the only discriminator for a stationary return that works in this data, see below):
  * below 4 m/s, a stationary object that only the radar sees no longer stops the car: the guard holds the plan for
    it, and the stop rests on the camera, e2e and the driver. Worst engaged case: route 06 t=1306.3-1309.8, at night,
    a stopped vehicle 20 -> 13 m ahead that the camera rated 0.03-0.45. Upstream at the true scale braked to
    -0.3/-0.5; the first guard held +0.4 to +1.0 m/s^2 (and, closed loop, could have carried the car past 4 m/s,
    where radard has no lead for it at all); the hold keeps the plan at -0.5 to 0.0. Also route 07 t=1240.7 (a return
    at 6 m the camera saw at prob 0.13-0.17). These losses fall mostly at night;
  * a vehicle creeping at about 1 m/s reads 0.3-1.2 m/s on this radar at walking pace (route 10f t=2756-2757 and
    t=2851-2853, daylight, 4-8 m ahead, camera prob 0.1-0.5), so until it has read 1 m/s for MOVING_UPDATES updates
    with its range agreeing it counts as stationary and is only held;
  * a vehicle coming TOWARD the car (reversing, rolling back, or oncoming in the path on a curve) never counts as
    moving: with the camera unsure it is only held, where upstream would brake for it. Latching such tracks on the
    same range test admitted clutter in the replay (route c8 t=2800.2: a return at 2.6 m read -1.2 m/s for three
    updates as its range jumped, then sat still; -3.5 m/s^2 with it, disengaged), and taking them gained nothing
    engaged (0.1 s of engaged radar lead, no plan change).

Thresholds, from radard rerun on 14 routes (01, 06, 07, 08, 10, 14, c0, c8, c9, 10f, 113, 115, 120, 121; 479,301
frames) with stationary tracks called clutter when the car drove past the point within 5 s while the track never
moved:
  * camera-minus-radar speed of moving radar matches, 99th percentile: 1.6 m/s within 20 m, 2.0 at 20-30 m, 2.3 at
    30-45 m, 4.2 at 45-60 m, 5.0 at 60-80 m, 5.2 at 80-120 m. Clutter matches: the camera is faster by a median 3.6
    m/s at 10-20 m and 7.6-8.1 m/s at 20-45 m; there are 3 clutter frames beyond 45 m. Stationary tracks that are not
    clutter: 99th percentile 2.4 m/s within 20 m, then 5.7-9.8 m/s at 20-80 m, where the camera is slow to see that
    traffic has stopped (route c0, a stopped queue at 65-120 m read at 8-12 m/s by the camera).
    So the tolerance is 3 m/s up to 20 m, grows 0.15 m/s per meter and stops at upstream's 10 m/s from 67 m. Between
    20 and 67 m it deliberately sits below that 99th percentile for real stopped traffic, because the clutter's median
    sits there too: a stopped car the camera still reads as fast is then handed to the camera's (lagging) lead, as
    the old half-scale decode effectively did. In the 13-route replay that costs 0.9 s of real-object radar lead
    engaged at 4 m/s and over, and no engaged stop brakes later than the old decode for it.
  * the model's vStd cannot set it: on the current model (routes 10f, 113, 115, 120, 121) leadsV3 vStd and xStd read
    up to 59874, so "a few vStd" would pass everything.
  * the override without a confident camera lead: 9056 frames, 56% of them clutter. With one, a track within 1.5 m of
    the camera's distance is clutter on 2-9% of frames, within 1 m/s of its speed on 2%; 1.5-2 m/s rises to 27%.
  * moving: of 548 latches with the car under 4 m/s, the track's world speed over +-0.75 s around the latch is 0.7 m/s
    or more on 533 and under 0.3 m/s on 5, all of them 30-59 m away and at least 1.1 m off the path. Without the
    range test a speed spike latched: route 07 t=1128.23-1128.78, track 1796 read 3.7-3.9 m/s for six radar updates
    while its point in the world stayed within 11755.0-11755.9 m (-3.5 m/s^2 in the replay, disengaged); route 120
    t=17.7, track 74, read 2.0 m/s for three. WORLD_RATIO rejects 1796, whose range said 1.4 m/s against 3.9. No other
    per-track feature separates clutter from a real stationary object in this data (age, distance jumps, range-rate
    residual all overlap).
  * hold, in the 13-route plan replay: 44.7 s engaged (30.7 s on clutter the car drove through, 9.9 s on objects it
    stopped short of); no FCW; the engaged episodes at -2 m/s^2 or below and 1 below the old decode stay the same 3;
    engaged, it is 0.5 m/s^2 or more firmer than the guard without it on 7.3 s, never by 1 m/s^2 to below -1, and
    at worst -0.83 (route 06 t=1265.5, where the old decode braked to -0.78).
"""
import math
from collections import deque

from opendbc.car.honda.values import HONDA_ELESYS
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_id

RADAR_TO_CAMERA = 1.52  # radard.RADAR_TO_CAMERA (test_elesys_radar_guard.py pins the two equal)
DT = 0.05               # s per radard frame (DT_MDL: radard runs once per modelV2)
CAMERA_ON = 0.55        # radard's filtered leadsV3 prob: the camera's lead becomes confident above this,
CAMERA_OFF = 0.5        # and stops being confident at or below radard's own 0.5

V_CHECKED = 3.0         # m/s: tracks slower than this are checked (upstream's vel_sane passes faster ones outright)
V_TOL_MIN = 3.0         # m/s, up to 20 m of camera range
V_TOL_PER_M = 0.15      # m/s per meter of camera range beyond that
V_TOL_MAX = 10.0        # m/s, upstream's vel_sane window, from 67 m
OVERRIDE_D_TOL = 1.5    # m: low-speed override track within this of the camera lead's distance
OVERRIDE_V_TOL = 1.0    # m/s: or within this of its speed

# Moving. Counted in radar updates: the radar reports at 10 Hz and radard runs at 20 Hz, so it sees every reading twice
V_MOVING = 1.0          # m/s: a track whose raw vLead is at least this
MOVING_UPDATES = 3      # for this many radar updates in a row,
V_WORLD = 0.7           # m/s: and whose range plus the car's travel moves away at least this,
WORLD_RATIO = 0.5       # and at least this share of that speed (a radar speed the range does not follow is noise),
WORLD_SPAN = 0.5        # s: over the last this much of its history, is moving for the rest of its life
V_MOVING_NOW = 1.5      # m/s: a track whose filtered vLeadK is at least this
MOVING_NOW_UPDATES = 2  # for this many radar updates in a row, with its raw vLead at least V_MOVING, is moving now,
                        # if its range history agrees
NOW_SPAN = 0.3          # s: over the history it has; a shorter history (a track that just appeared) cannot disagree

# Hold: with no lead at all, an in-path track the override refuses still stops the plan accelerating toward it
HOLD_UPDATES = 5        # radar updates: once seen for at least this many
STOP_DISTANCE = 6.0     # m: long_mpc.STOP_DISTANCE (test_elesys_radar_guard.py pins the two equal)
HOLD_T_FOLLOW = 1.25    # s: long_mpc's shortest T_FOLLOW (aggressive), pinned the same way


def enabled(CP) -> bool:
  return CP.brand == 'honda' and CP.carFingerprint in HONDA_ELESYS


def camera_distance(lead_msg) -> float:
  return lead_msg.x[0] - RADAR_TO_CAMERA


def speed_tolerance(lead_msg) -> float:
  """How much faster than a slow radar track the camera may say its lead is, by the camera lead's range."""
  return min(max(V_TOL_PER_M * camera_distance(lead_msg), V_TOL_MIN), V_TOL_MAX)


def match_agrees(track, lead_msg) -> bool:
  if track.vLead >= V_CHECKED:
    return True
  return lead_msg.v[0] - track.vLead <= speed_tolerance(lead_msg)


def match_candidates(tracks: dict, lead_msg) -> dict:
  """The tracks radard may match to the camera's lead."""
  return {k: c for k, c in tracks.items() if match_agrees(c, lead_msg)}


def slope(samples) -> float:
  """Least-squares slope of (t, x) samples."""
  n = len(samples)
  tm = sum(t for t, _ in samples) / n
  xm = sum(x for _, x in samples) / n
  return sum((t - tm) * (x - xm) for t, x in samples) / sum((t - tm) ** 2 for t, _ in samples)


class _TrackState:
  __slots__ = ('updates', 'run_ahead', 'run_now', 'history')

  def __init__(self):
    self.updates = 0     # radar updates seen
    self.run_ahead = 0   # radar updates in a row at vLead >= V_MOVING
    self.run_now = 0     # radar updates in a row at vLeadK >= V_MOVING_NOW and vLead >= V_MOVING
    self.history: deque = deque()  # (t, range + the car's travel) at each radar update, over the last WORLD_SPAN s


class ElesysRadarGuard:
  """The guard's per-frame state: which radar tracks are moving, how long each has been seen, and whether the camera's
  lead is confident.

  RadarD calls update_tracks() after it updates its tracks and update_camera() with its filtered prob for leadOne,
  once per frame, before get_lead()."""

  def __init__(self):
    self.t = 0.0
    self.odometer = 0.0                       # m the car has travelled, at the radar's delay
    self.radar_frame = -1                     # radard's recv_frame['radarTracks'] at the last update
    self.state: dict[int, _TrackState] = {}
    self.moving: set[int] = set()             # track ids latched as moving
    self.camera_confident = False

  def update_tracks(self, tracks: dict, radar_frame: int, v_ego: float) -> None:
    """tracks: radard's, after this frame's update. radar_frame: radard's recv_frame['radarTracks'], which changes when
    they carry a radar update radard had not seen. v_ego: the car's speed at the radar's measurement (v_ego_hist[0])."""
    new_radar = radar_frame != self.radar_frame
    self.radar_frame = radar_frame
    self.t += DT
    if math.isfinite(v_ego):  # one NaN would poison the odometer, and with it every range test, for the whole drive
      self.odometer += v_ego * DT
    for tid in [t for t in self.state if t not in tracks]:
      del self.state[tid]
      self.moving.discard(tid)
    if not new_radar:
      return
    for tid, track in tracks.items():
      s = self.state.get(tid)
      if s is None:
        s = self.state[tid] = _TrackState()
      s.updates += 1
      s.run_ahead = s.run_ahead + 1 if track.vLead >= V_MOVING else 0
      s.run_now = s.run_now + 1 if track.vLeadK >= V_MOVING_NOW and track.vLead >= V_MOVING else 0
      s.history.append((self.t, track.dRel + self.odometer))
      while len(s.history) > 2 and s.history[1][0] <= self.t - WORLD_SPAN + DT / 2:
        s.history.popleft()
      if (tid not in self.moving and s.run_ahead >= MOVING_UPDATES and
          self.range_agrees(tid, track.vLead, WORLD_SPAN)):
        self.moving.add(tid)

  def update_camera(self, lead_prob: float) -> None:
    if lead_prob > CAMERA_ON:
      self.camera_confident = True
    elif lead_prob <= CAMERA_OFF:
      self.camera_confident = False

  def world_speed(self, tid: int, span: float = WORLD_SPAN) -> float | None:
    """The track's speed over the ground from its own range history (up to WORLD_SPAN s of it); None if that history
    is shorter than span."""
    s = self.state.get(tid)
    if s is None or len(s.history) < 2 or s.history[-1][0] - s.history[0][0] < span - DT / 2:
      return None
    return slope(s.history)

  def range_agrees(self, tid: int, v: float, span: float) -> bool:
    """Does the track's range history over at least span s move away at V_WORLD or more, and at least WORLD_RATIO of
    its radar speed v?"""
    w = self.world_speed(tid, span)
    return w is not None and w >= max(V_WORLD, WORLD_RATIO * v)

  def is_moving(self, track) -> bool:
    if track.identifier in self.moving:
      return True
    s = self.state.get(track.identifier)
    if s is None or s.run_now < MOVING_NOW_UPDATES:
      return False
    if self.world_speed(track.identifier, NOW_SPAN) is None:
      return True  # too new for a range history (a cut-in): taken on its speed alone
    return self.range_agrees(track.identifier, track.vLeadK, NOW_SPAN)

  def override_confirmed(self, track, lead_msg) -> bool:
    """May radard's low-speed override take this track? A moving one always, as upstream; a stationary one only with
    the camera's confident lead at its distance or speed."""
    if self.is_moving(track):
      return True
    if not self.camera_confident:
      return False
    return (abs(track.dRel - camera_distance(lead_msg)) < OVERRIDE_D_TOL or
            abs(track.vLead - lead_msg.v[0]) < OVERRIDE_V_TOL)

  def hold_track(self, refused: list):
    """Of the in-path tracks the low-speed override refused, the closest one seen for HOLD_UPDATES radar updates,
    or None."""
    held = [c for c in refused if c.identifier in self.state and self.state[c.identifier].updates >= HOLD_UPDATES]
    return min(held, key=lambda c: c.dRel) if held else None


def hold_lead(lead_dict: dict, v_ego: float) -> dict:
  """A refused track's lead, made one the planner's MPC neither accelerates toward nor brakes for: a lead at the car's
  own speed, not accelerating, at the MPC's desired distance for that speed on its shortest time gap. The MPC then
  holds speed (aggressive) or eases off slightly (standard, relaxed); the camera, e2e and the driver still do any
  stopping. Below the car's stopping speed (0.8 m/s) that is a stop: the MPC's output is under should_stop()'s 0.1, so
  the planner asks to stop, and from standstill does not launch while the track stays held. Its dRel is therefore not
  the track's range, and it is marked (radarTrackId = held_lead.held_id(track)) so that every leadOne consumer but the
  MPC can skip it (held_lead.real_lead())."""
  lead_dict.update(dRel=STOP_DISTANCE + HOLD_T_FOLLOW * v_ego, vLead=v_ego, vLeadK=v_ego, vRel=0.0, aLeadK=0.0,
                   radarTrackId=held_id(lead_dict['radarTrackId']))
  return lead_dict
