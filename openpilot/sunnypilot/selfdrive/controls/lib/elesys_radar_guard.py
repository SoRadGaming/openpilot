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
  * the low-speed override may take a track only when the camera has a confident lead (radard's own filtered prob
    > 0.5) at a consistent distance (OVERRIDE_D_TOL) or speed (OVERRIDE_V_TOL). Otherwise the lead is whatever the
    camera path gave: the camera's own lead, or none.
A stopped car the camera also sees as stopped or slow is kept, at any range. Given up: below 4 m/s with no confident
camera lead the radar no longer stops the car; that stop is the camera's and e2e's (route 07 t=1240.7, a return at 6 m
the camera saw at prob 0.13-0.17).

Thresholds, from radard rerun on 14 routes (01, 06, 07, 08, 10, 14, c0, c8, c9, 10f, 113, 115, 120, 121; 479,301
frames) with stationary tracks called clutter when the car drove past the point within 5 s while the track never
moved:
  * camera-minus-radar speed of moving radar matches, 99th percentile: 1.6 m/s within 20 m, 2.0 at 20-30 m, 2.3 at
    30-45 m, 4.2 at 45-60 m, 5.0 at 60-80 m, 5.2 at 80-120 m. Clutter matches: the camera is faster by a median 3.6
    m/s at 10-20 m and 7.6-8.1 m/s at 20-45 m; there are 3 clutter frames beyond 45 m. Stationary tracks that are not
    clutter: 99th percentile 2.4 m/s within 20 m, then 5.7-9.8 m/s at 20-80 m, where the camera is slow to see that
    traffic has stopped (route c0, a stopped queue at 65-120 m read at 8-12 m/s by the camera).
    So the tolerance is 3 m/s up to 20 m, grows 0.15 m/s per meter and stops at upstream's 10 m/s from 67 m.
  * the model's vStd cannot set it: on the current model (routes 10f, 113, 115, 120, 121) leadsV3 vStd and xStd read
    up to 59874, so "a few vStd" would pass everything.
  * the override without a confident camera lead: 9056 frames, 56% of them clutter. With one, a track within 1.5 m of
    the camera's distance is clutter on 2-9% of frames, within 1 m/s of its speed on 2%; 1.5-2 m/s rises to 27%.
"""
from opendbc.car.honda.values import HONDA_ELESYS

RADAR_TO_CAMERA = 1.52  # radard.RADAR_TO_CAMERA (test_elesys_radar_guard.py pins the two equal)
CAMERA_PROB = 0.5       # radard's "confident camera lead": its filtered leadsV3 prob

V_CHECKED = 3.0         # m/s: tracks slower than this are checked (upstream's vel_sane passes faster ones outright)
V_TOL_MIN = 3.0         # m/s, up to 20 m of camera range
V_TOL_PER_M = 0.15      # m/s per meter of camera range beyond that
V_TOL_MAX = 10.0        # m/s, upstream's vel_sane window, from 67 m
OVERRIDE_D_TOL = 1.5    # m: low-speed override track within this of the camera lead's distance
OVERRIDE_V_TOL = 1.0    # m/s: or within this of its speed


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


def override_confirmed(track, lead_msg, lead_prob: float) -> bool:
  """May radard's low-speed override take this track? Only with the camera's confident lead at its distance or speed."""
  if lead_prob <= CAMERA_PROB:
    return False
  return (abs(track.dRel - camera_distance(lead_msg)) < OVERRIDE_D_TOL or
          abs(track.vLead - lead_msg.v[0]) < OVERRIDE_V_TOL)
