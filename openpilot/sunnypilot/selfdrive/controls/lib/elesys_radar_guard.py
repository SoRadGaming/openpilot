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
  * the low-speed override takes a MOVING track as upstream does: one whose raw vLead has been at least V_MOVING for
    MOVING_FRAMES radard frames in a row (latched for the rest of that track's life, so a vehicle that stops stays a
    vehicle), or whose filtered vLeadK is at least V_MOVING_NOW. Any other track it takes only when the camera has a
    confident lead at a consistent distance (OVERRIDE_D_TOL) or speed (OVERRIDE_V_TOL). Confident means radard's
    filtered prob rose above CAMERA_ON and has not since fallen to CAMERA_OFF (radard's own 0.5), so a single frame
    just over 0.5 does not switch the override on. Otherwise the lead is whatever the camera path gave.
A stopped car the camera also sees as stopped or slow is kept, at any range.

Given up (the camera is the only discriminator for a stationary return that works in this data, see below):
  * below 4 m/s, a stationary object that only the radar sees no longer stops the car; the stop rests on the camera,
    e2e and the driver. Worst engaged case: route 06 t=1306.3-1309.8, at night, a stopped vehicle 20 -> 13 m ahead
    that the camera rated 0.03-0.45; the plan held +0.4 to +1.0 m/s^2 for about 3 s where upstream braked to -0.6.
    Also route 07 t=1240.7 (a return at 6 m the camera saw at prob 0.13-0.17). These losses fall mostly at night;
  * a vehicle creeping at about 1 m/s reads 0.3-1.2 m/s on this radar at walking pace (route 10f t=2756-2757 and
    t=2851-2853, daylight, 4-8 m ahead, camera prob 0.1-0.5), so until a track has read 1 m/s for MOVING_FRAMES
    frames it counts as stationary and needs the camera.

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
  * moving: of 247 latches (MOVING_FRAMES = 5 at 1 m/s, car under 4 m/s) the track's own range history shows a world
    speed of 0.7 m/s or more on 235, under 0.3 m/s on 2. A raw per-frame test flickers on creeping vehicles and admits
    one-frame spikes on stationary tracks (route 120 t=17.7: track 74 reads 0.1 -> 2.0 x3 -> 0.2 m/s; three frames
    do not latch, and its vLeadK peaks at 1.01). No other per-track feature separates clutter from a real stationary
    object in this data (age, distance jumps, range-rate residual all overlap).
"""
from opendbc.car.honda.values import HONDA_ELESYS

RADAR_TO_CAMERA = 1.52  # radard.RADAR_TO_CAMERA (test_elesys_radar_guard.py pins the two equal)
CAMERA_ON = 0.55        # radard's filtered leadsV3 prob: the camera's lead becomes confident above this,
CAMERA_OFF = 0.5        # and stops being confident at or below radard's own 0.5

V_CHECKED = 3.0         # m/s: tracks slower than this are checked (upstream's vel_sane passes faster ones outright)
V_TOL_MIN = 3.0         # m/s, up to 20 m of camera range
V_TOL_PER_M = 0.15      # m/s per meter of camera range beyond that
V_TOL_MAX = 10.0        # m/s, upstream's vel_sane window, from 67 m
OVERRIDE_D_TOL = 1.5    # m: low-speed override track within this of the camera lead's distance
OVERRIDE_V_TOL = 1.0    # m/s: or within this of its speed

V_MOVING = 1.0          # m/s: a track whose raw vLead is at least this for
MOVING_FRAMES = 5       # this many radard frames in a row is a moving object for the rest of its life
V_MOVING_NOW = 1.5      # m/s: a track whose filtered vLeadK is at least this is moving now


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


class ElesysRadarGuard:
  """The guard's per-frame state: which radar tracks have been seen moving, and whether the camera's lead is confident.

  RadarD calls update_tracks() after it updates its tracks and update_camera() with its filtered prob for leadOne,
  once per frame, before get_lead()."""

  def __init__(self):
    self.moving_frames: dict[int, int] = {}  # track id -> radard frames in a row at vLead >= V_MOVING
    self.moving: set[int] = set()            # track ids latched as moving
    self.camera_confident = False

  def update_tracks(self, tracks: dict) -> None:
    for tid in [t for t in self.moving_frames if t not in tracks]:
      del self.moving_frames[tid]
      self.moving.discard(tid)
    for tid, track in tracks.items():
      n = self.moving_frames.get(tid, 0) + 1 if track.vLead >= V_MOVING else 0
      self.moving_frames[tid] = n
      if n >= MOVING_FRAMES:
        self.moving.add(tid)

  def update_camera(self, lead_prob: float) -> None:
    if lead_prob > CAMERA_ON:
      self.camera_confident = True
    elif lead_prob <= CAMERA_OFF:
      self.camera_confident = False

  def is_moving(self, track) -> bool:
    return track.identifier in self.moving or track.vLeadK >= V_MOVING_NOW

  def override_confirmed(self, track, lead_msg) -> bool:
    """May radard's low-speed override take this track? A moving one always, as upstream; a stationary one only with
    the camera's confident lead at its distance or speed."""
    if self.is_moving(track):
      return True
    if not self.camera_confident:
      return False
    return (abs(track.dRel - camera_distance(lead_msg)) < OVERRIDE_D_TOL or
            abs(track.vLead - lead_msg.v[0]) < OVERRIDE_V_TOL)
