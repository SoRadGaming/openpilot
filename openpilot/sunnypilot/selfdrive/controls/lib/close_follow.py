"""
FORK(CLOSE-FOLLOW): close follow in queues (the owner's round-5 request: "close up in motorway queues so cars can't cut
in, gap grows back with speed"). push13 built it in shadow; release13 makes it LIVE (owner, 2026-10-24: "Automatic in
queues", "Live now, with a safety limit", "Safe limit only").

THE SWITCH: one user setting, CloseFollow (a BOOL, default ON; comma 4 Settings > Toggles 'close follow', sunnylink
Vehicle > Honda 'Close Follow'). ON: live - close follow shapes the plan. OFF: shadow - it computes and logs everything
below every plan and changes nothing (the plan, the accel sent, the HUD, every CAN frame are as without it). The planner
reads it when plannerd starts, again every SWITCH_READ_S (3 s) and at every engage (reads only, never a write), and a
change applies on that frame: switched ON, the gap and the lift start from nothing (the 1 s hold, then the ramp);
switched OFF, close follow drops out at once, as on any trip. A switch that cannot be read is OFF (shadow).

ACTIVATION is automatic (CloseFollowSwitch: wanted whenever openpilot longitudinal is engaged, off at every disengage):
it switches itself in and out by the guards below - engaged, a real radar lead, under the hand-back speed.

WHY. In a queue at 10-30 km/h openpilot sat 13-20 m behind (round 5, 95 routes: median 13.2 m at 10-20 km/h, 19.6 m at
20-30) against the owner's own 9.4 / 13.4 m, and cars cut into gaps over 9-12 m. Two things hold the gap open:
  * the MPC's follow time, 1.25 s on aggressive (long_mpc.get_T_FOLLOW), plus its 6 m stop distance;
  * in experimental mode the model's own e2e plan, which keeps a longer gap than any personality and which the planner
    takes whenever it is the lowest candidate (49% of the owner's low-speed following, round 5).
So close follow does two things, both only with a real (not held) lead and only where its follow time is under the
personality's:

1. THE GAP (after theo886's Traffic Mode, sunnypilot `accord-hybrid-9g` e83f724d0, from FrogPilot's traffic follow):
   a second LongitudinalMpc beside the plan's own with a shorter follow time - THE SAFE LIMIT (release13, the cfenv
   study and its skeptic, skeptic_schedule.json): CF_T_FOLLOW_V over CF_T_FOLLOW_BP, 0.5 s at a stop, 0.88 s at
   13.5 km/h, 1.0 s at 15, 1.34 s at 18 and 1.8 s at 22 km/h, evaluated at the speed the queue is heading for -
   max(vEgo, the real lead's vLead + LEAD_AHEAD_S x its aLeadK over LEAD_AHEAD_DEADBAND) - never more than
   GAP_DEFICIT_MAX (1 m) under the personality's gap at this speed and never above the personality's (close_t_follow).
   So it hands back to the personality by itself - at 17.2 / 19.0 / 21.6 km/h on aggressive / standard / relaxed, and
   sooner when the queue pulls away - with no separate fade, and never closes up while the lead pulls away faster.
   It was sized so that behind a lead braking at -4 m/s^2 from STEADY following anywhere in the band this car keeps 2 m
   with its DESIGN brake (-1.8 m/s^2 after a 0.36 s dead time: the 255-count ceiling, the p90 weak end of the logged
   full brakes); with brake320's 320 counts it is conservative. THE RELEASE13 REVIEW found where that sizing did not
   hold: a queue speeding up from a crawl and then braking hard. The gap closed at a crawl does not reopen while the
   queue pulls away (the MPC tracks the lead's speed; its desired gap shrinks while the lead is faster), and the
   schedule, read at the radar's lagging vLead, kept the closer gap in as the car accelerated - so a lead braking at -4
   or -6 just after it touched in 65 of 648 sim runs where push12 stopped short (11 with the closer gap still in).
   Three rules since: the schedule is read ahead of a lead that accelerates (LEAD_AHEAD_S), so it hands back while the
   queue is still speeding up; the deficit against the personality's gap is capped (GAP_DEFICIT_MAX), so what a crawl
   closes up is at most 1 m to reopen; and the hand-back (speedWeight 0) resets the authority, so the closer gap never
   snaps back in at full authority when the lead slows (the schedule speed falls the moment a lead brakes). Left: the
   closer gap is still up to 1 m less room in such a wave; where push12 itself stops within about 0.9 m, that can be a
   touch at 2-5 km/h (docs/CHANGELOG_SERIAL_STEERING.md, push13). The jerk factor is max(1.0, the personality's)
   (CF_JERK_FACTOR_MIN: not the aggressive 0.5, which the envelope showed reacts later to a braking lead).
   STOP_DISTANCE is compiled into the solver and untouched: the plan still aims for 6 m at a standstill, as push12's -
   but arriving at the stop from a closer gap, the car stops about 0.5-1.3 m closer than push12 after a gentle stop (the
   release13 review's sim: 3.7-4.3 m against 4.3-5.1). CF_FADE_SPEED (22 km/h, the schedule's last point) is the one
   eligibility bound: the close MPC solves below it + CF_MPC_MARGIN, and above the hand-back it is the plan's own.
   Its candidate is blended into the plan's by gapAuthority: a = a_plan_mpc + authority * (a_close_mpc - a_plan_mpc),
   the trajectories alike. Authority drops to 0 AT ONCE on any trip, so on that frame the MPC candidate is the plan's
   own MPC, push12's, exactly. It waits out GAP_HOLD_S (1 s) of clean frames (the close MPC warms up), then rises at
   GAP_RISE (0.5/s). Trips: no real lead (or a held one, radard's clutter guard), lead modelProb under 0.8, a vision-only
   lead above CF_VISION_SPEED (2 m/s; the radar sees nothing at a stop and little under 2 m/s, where t*v is under 1 m),
   a braking lead (any real lead's aLeadK under -0.5 m/s^2, or leadOne's own speed falling faster than that over the
   last 0.5 s with aLeadK below 0 - aLeadK is a filter and trails the onset), a new car (leadOne 3 m off where it should
   be), FCW (the planner's, the close MPC's own crash flag, or the car's FCW/AEB, held FCW_HOLD_S = 5 s), gas or brake,
   forceDecel, and since release13 three more: a HARD-braking lead (leadOne's own speed falling faster than 2.0 m/s^2
   over 0.5 s, or any real lead's aLeadK under -1.5) holds it out HARD_BRAKE_HOLD_S (3 s); a downhill steeper than 3 %
   (carControl.orientationNED pitch, filtered over PITCH_TAU_S, under DOWNHILL_PITCH); a closing lead with a time to
   collision under TTC_MIN_S (3 s, closing faster than 0.5 m/s).

2. THE LIFT (adapted from zoompilot's Lead Follow Assist, E2ELeadGapController, MIT - notice below): in experimental
   mode the model's e2e accel is lifted toward the close MPC's candidate when the leads are 3-10 m (GAP_BP) beyond the
   close gap: e2e = a_model + boost, boost <= authority * gain * weight * min(CAP, headroom), headroom =
   min(a_mpc_close, a_cruise) - a_model. It can at most tie that candidate - NEVER PAST the radar plan - so the
   planner's min() never follows closer than the close MPC itself would. Fades in over 9-12.6 km/h (LIFT_SPEED_BP, the
   crawl stays the model's) and is zero wherever the close follow time is not under the personality's (it fades out
   over the last LIFT_T_FADE of it). CAP 0.5 / 0.4 / 0.3 m/s^2 for aggressive / standard / relaxed, rising at
   0.25 m/s^3. HARD trips drop it to nothing on the same frame - a braking lead (as for the gap, or the model's lead
   forecast losing 1 m/s within 3 s), FCW (for as long as the gap's FCW_HOLD_S hold lasts: the lift never comes back
   before the gap), a hard-braking lead (for as long as the gap's 3 s hold), a downhill, a short time to collision, a
   new car (distance jump or radar track swap), gas or brake, no lead or a held one, a vision-only lead, forceDecel, the
   model's hard-brake flag or a stop in its plan. Soft trips (lead uncertain or under LEAD_MIN_SPEED 2.5 m/s, the model
   braking under -0.5 or its plan slowing, over 1 m/s^2 of lateral accel, coasting, a lane change, under 2.5 m/s) let it
   fall at 4/s. Then a 3 s hold (LIFT_HOLD_S, with its authority at 0 after a hard trip) and a 0.5/s rise, as zoompilot.

NOT CHANGED for close follow: LongControl's kiV, the PID floor and the brake's rise limiter are the car's, as without it.

GATED: HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (close_follow_applies(), the re-entry ramp's
gate). Every other car and stock ACC mode never build it: upstream's planner bit for bit, no second MPC, no
customReserved10.

LOGGED (the owner has no SSH): customReserved10 (CustomReserved10 in custom.capnp) every plan, 2 Hz in the qlog -
since release13 with switchOn (the user switch as last read), live, raised (how far its candidates raise the plan's
min() over the plan's own: what it does to the plan, or would in shadow), tightening (live, engaged and raised by
TIGHT_RAISE or more: what the comma 4's right-rail cue shows - the release13 review: not merely the gap in while the
model's plan or cruise is the lower candidate) and gapCloser (the gap in and 0.5 m shorter: release13's first
'tightening') - and at most one `closefollow` cloudlog event at error level per
SUMMARY_S (10 s), only when there was a chance to act (engaged, a real lead, under the eligibility bound): `mode` (1
shadow, 2 live), `sw` (the switch), the share of those frames with the gap and the lift in, `live` (the share
tightening live), the mean follow time and gap change, `up` (the most it raised the plan), the top reasons it was out, and
`brk`, the frames where it lifted the plan while a lead braked - by construction 0; a non-zero `brk` in a route is a
bug - and `smax` / `xmax`, the slowest close-MPC solve and the slowest whole close-follow step of those 10 s, in ms
(mpcSolveTime / stepTime, every plan). A switch change is one `closefollow switch` line.

COST: a second MPC solve only while eligible: requested, engaged, a real lead, under CF_FADE_SPEED + CF_MPC_MARGIN.
On the device the plan's own solve on such frames is 0.6 ms at the median but has a long tail (p95 7.6-13 ms, max
~23 ms on routes 14c, 141, 142, 14d), and this one is the same solver. So in SHADOW the planner's three calls are only
recorded (defer) and all of close follow runs in LongitudinalPlanner.publish_deferred(), which plannerd calls once
longitudinalPlan, longitudinalPlanSP and driverAssistance are sent (run_deferred): its cost never delays a message
controlsd acts on. Live it runs inline - it has to, it shapes the plan. Its cost reaches the route either way
(mpcSolveTime, stepTime, smax, xmax above).

REPLAYED (push13, shadow, with push13's schedule - 1.0 s at 18 km/h faded to the personality's by 35 km/h - on 20 of
the owner's routes: 0x130-0x152, after the radar speed fix, and 14, 1f, c0, c9, d5, 12a with vRel doubled; 290 engaged
minutes, 61 of them engaged behind a real lead under 35 km/h). The replayed plans match the logged ones (median 0.000,
p95 under 0.02 m/s^2). By its own braking test it never raised the plan while a lead braked, never during any of the
76 engaged FCW frames, and the lift was never in within 12 s after one. release13's numbers - the safe limit, live -
are in docs/fork/README.md (CF) and docs/CHANGELOG_SERIAL_STEERING.md.

The lift is adapted from zoompilot (github.com/zoompilot/zoompilot, develop 5b87a72b,
openpilot/sunnypilot/selfdrive/controls/lib/e2e_lead_gap/controller.py), under this notice:
  Copyright (c) 2026-, Zeph Leggett.
  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
  documentation files (the "Software"), to deal in the Software without restriction, including without limitation the
  rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit
  persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice
  and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS
  PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
  MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
  HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
  ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
Changes from it: the ceiling is close follow's MPC candidate; hard trips cut it on the same frame; a vision-only or held
lead is no lead; it is zero wherever close follow's follow time is the personality's; its constants are inlined
(zoompilot's set-speed nudge is not here).
"""
import math
import time
from collections import Counter, deque
from typing import NamedTuple

import numpy as np

from opendbc.car.honda.values import HONDA_ELESYS
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log
import openpilot.cereal.messaging as messaging
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.drive_helpers import get_accel_from_plan
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (COMFORT_BRAKE, N as MPC_N, STOP_DISTANCE,
                                                                           T_IDXS as T_IDXS_MPC, get_T_FOLLOW,
                                                                           get_jerk_factor)
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import is_held, real_lead

Inhibit = custom.CustomReserved10.CloseFollowInhibit
LaneChangeState = log.LaneChangeState
Personality = log.LongitudinalPersonality
PlanSource = log.LongitudinalPlan.LongitudinalPlanSource

# CloseFollow (params_keys.h, BOOL, default "1"): the user's switch. ON live, OFF shadow (computed and logged only).
SWITCH_PARAM = "CloseFollow"
SWITCH_READ_S = 3.0             # s: plannerd re-reads it this often (and at every engage); reads only, never a write
MODE_SHADOW, MODE_LIVE = 1, 2   # customReserved10.mode
SERVICE = "customReserved10"

# --- the gap: the safe limit (release13; cfenv's skeptic, skeptic_schedule.json) ---
CF_T_FOLLOW_BP = [0., 3.75, 4.167, 5.0, 6.111]   # m/s: 0, 13.5, 15, 18, 22 km/h
CF_T_FOLLOW_V = [0.50, 0.88, 1.00, 1.34, 1.80]   # s, at max(vEgo, the real lead's vLead); never above the personality's
CF_FADE_SPEED = CF_T_FOLLOW_BP[-1]  # m/s: the one eligibility bound (22 km/h); every personality hands back below it
# the release13 review: a queue pulling away from a crawl, then braking hard
LEAD_AHEAD_S = 2.0              # s: the schedule is read at the lead's speed this far ahead on its acceleration ...
LEAD_AHEAD_DEADBAND = 0.3       # m/s^2: ... above this (a creeping queue's swell is not a pull-away)
GAP_DEFICIT_MAX = 1.0           # m: the closer gap is never more than this under the personality's at this speed
CF_FADE_KPH = 22.               # km/h, the same
CF_JERK_FACTOR_MIN = 1.0        # the close MPC's jerk factor: max(this, the personality's) - not aggressive's 0.5
CF_VISION_SPEED = 2.0           # m/s: above it the gap needs a radar lead
CF_MPC_MARGIN = 1.5             # m/s: the close MPC keeps running this far past CF_FADE_SPEED, so slowing back is seamless
GAP_LEAD_BRAKE = -0.5           # m/s^2: a real lead's aLeadK under this drops the gap (and the lift) at once
# radard's aLeadK is a filter and trails a lead that starts braking: replayed on 20 routes, leadOne's own speed fell
# faster than 0.5 m/s^2 over 0.5 s on 127 occasions while aLeadK still read -0.1 (median). So leadOne's own speed
# slope over the last LEAD_SLOWING_T (same car, a least-squares fit: one jittery radar sample does not trip it) under
# LEAD_SLOWING_ACCEL, with aLeadK at least below 0, is a braking lead too.
LEAD_SLOWING_T = 0.5            # s
LEAD_SLOWING_ACCEL = -0.5       # m/s^2
LEAD_SLOWING_MIN_N = 5          # samples in the fit
GAP_HOLD_S = 1.0                # s of clean frames before the gap's authority rises
GAP_RISE = 0.5                  # 1/s
FCW_HOLD_S = 5.0                # s: an FCW (any) keeps close follow out this long
LEAD_PROB = 0.8
LEAD_JUMP = 3.0                 # m off where last frame's leadOne should be: a new car
# release13, the safe limit's three extra trips (the gap and the lift alike, both hard)
HARD_BRAKE_SLOPE = -2.0         # m/s^2: leadOne's own speed over the last LEAD_SLOWING_T falling faster than this ...
HARD_BRAKE_ALEAD = -1.5         # ... or any real lead's aLeadK under this: a hard-braking lead ...
HARD_BRAKE_HOLD_S = 3.0         # ... keeps close follow out this long after the last such frame
DOWNHILL_PITCH = -0.03          # rad (nose-up positive, carControl.orientationNED[1]): steeper than 3 % downhill, out
PITCH_TAU_S = 1.0               # s: the pitch's filter, so a brake's nose dive is not a hill
TTC_MIN_S = 3.0                 # s: leadOne's time to collision under this, out ...
TTC_CLOSING = 0.5               # m/s: ... while closing on it faster than this

# --- the lift (zoompilot Lead Follow Assist) ---
GAP_BP = [3., 10.]              # m beyond the close gap: the lift opens over this
LIFT_SPEED_BP = [2.5, 3.5]      # m/s (9 -> 12.6 km/h): the lift fades in (the crawl stays the model's)
LIFT_T_FADE = 0.1               # s: and out over the last 0.1 s of follow time before the personality's - zero from it
CAP = {int(Personality.aggressive): 0.5, int(Personality.standard): 0.4, int(Personality.relaxed): 0.3}  # m/s^2
GAIN_BP = [-0.4, -0.1]          # m/s^2 of the model's own accel: the lift fades out as the model starts braking
MODEL_BRAKE_ACCEL = -0.5        # m/s^2
PLAN_SLOWDOWN = 1.0             # m/s the model's plan loses within PLAN_SLOWDOWN_T
PLAN_SLOWDOWN_T = 5.0           # s
LAT_ACCEL_MAX = 1.0             # m/s^2, now or anywhere on the plan within LAT_HORIZON
LAT_HORIZON = 10.0              # s
STOP_SPEED = 2.0                # m/s: a model plan dipping under this is a stop
LEAD_SLOWDOWN = 1.0             # m/s a lead's forecast loses within LEAD_SLOWDOWN_T
LEAD_SLOWDOWN_T = 3.0           # s
LEAD_MIN_SPEED = 2.5            # m/s (release13: 5.0 in push13, the old lift band's floor)
BOOST_RISE = 0.25               # m/s^3
LIFT_HOLD_S = 3.0               # s after the last trip
AUTHORITY_RISE = 0.5            # 1/s
AUTHORITY_FALL = 4.0            # 1/s, soft trips

SUMMARY_S = 10.0                # s: at most one closefollow cloudlog event per this
TIGHT_RAISE = 0.02              # m/s^2: live and raising the plan by at least this (raised): tightening - the HUD cue
TIGHT_GAP_M = 0.5               # m: the gap in and at least this much shorter than the personality's: gapCloser (log)
_EPS = 1e-9

T_IDXS = np.asarray(ModelConstants.T_IDXS)
LEAD_T_IDXS = np.asarray(ModelConstants.LEAD_T_IDXS)
LAT_MASK = T_IDXS <= LAT_HORIZON
SLOWDOWN_MASK = T_IDXS <= PLAN_SLOWDOWN_T
LEAD_SLOWDOWN_MASK = LEAD_T_IDXS <= LEAD_SLOWDOWN_T

HARD_LIFT_TRIPS = frozenset({Inhibit.noLead, Inhibit.visionLead, Inhibit.leadBraking, Inhibit.leadChanged, Inhibit.fcw,
                             Inhibit.driver, Inhibit.forceDecel, Inhibit.hardBrake, Inhibit.stop, Inhibit.leadHardBrake,
                             Inhibit.downhill, Inhibit.closing})

# the schedule rises with speed (np.interp needs an increasing x), and every personality hands back below its end
assert all(b > a for a, b in zip(CF_T_FOLLOW_BP, CF_T_FOLLOW_BP[1:], strict=False))
assert all(b >= a for a, b in zip(CF_T_FOLLOW_V, CF_T_FOLLOW_V[1:], strict=False))
assert CF_T_FOLLOW_V[-1] >= get_T_FOLLOW(Personality.relaxed) and abs(CF_FADE_KPH - CF_FADE_SPEED * CV.MS_TO_KPH) < 0.01
assert CF_FADE_SPEED > LIFT_SPEED_BP[1] > LIFT_SPEED_BP[0] >= LEAD_MIN_SPEED


def close_follow_applies(CP, CP_SP) -> bool:
  """HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (the re-entry ramp's gate)."""
  try:
    if CP.brand != 'honda' or CP.carFingerprint not in HONDA_ELESYS or not CP.openpilotLongitudinalControl:
      return False
    return not (int(getattr(CP_SP, 'flags', 0)) & HondaFlagsSP.ELESYS_STOCK_ACC.value)
  except Exception:
    return False


def read_switch(params) -> bool:
  """The user's CloseFollow switch (a key nobody has written yet is its params_keys.h default, ON). Anything unreadable
  is OFF: an unknown switch never makes it live."""
  try:
    v = params.get(SWITCH_PARAM, return_default=True)
    if isinstance(v, bool):
      return v
    if isinstance(v, bytes):
      v = v.decode(errors='ignore')
    return str(v).strip() == "1"
  except Exception:
    return False


def read_mode(params) -> int:
  """The mode the switch asks for: MODE_LIVE when it is on, MODE_SHADOW otherwise (read_switch)."""
  return MODE_LIVE if read_switch(params) else MODE_SHADOW


def _personality(p) -> int:
  return int(getattr(p, 'raw', p))


def _finite(*xs) -> bool:
  try:
    return all(math.isfinite(float(x)) for x in xs)
  except (TypeError, ValueError):
    return False


def schedule_speed(v_ego: float, v_lead: float | None = None, a_lead: float = 0.) -> float:
  """The speed the schedule is read at - where the queue is heading: max(vEgo, the real lead's vLead + LEAD_AHEAD_S x
  its aLeadK over LEAD_AHEAD_DEADBAND). No closer gap while the lead pulls away faster than the car, and a lead
  speeding up hands it back before the queue gets there (the release13 review). A missing or non-finite vLead is vEgo's;
  a non-finite aLeadK reads ahead nothing."""
  if v_lead is None or not _finite(v_lead):
    return float(v_ego)
  ahead = LEAD_AHEAD_S * max(float(a_lead) - LEAD_AHEAD_DEADBAND, 0.) if _finite(a_lead) else 0.
  return max(float(v_ego), float(v_lead) + ahead)


def schedule_t_follow(v: float) -> float:
  """The safe limit's schedule itself: CF_T_FOLLOW_V over CF_T_FOLLOW_BP at v."""
  return float(np.interp(v, CF_T_FOLLOW_BP, CF_T_FOLLOW_V))


def close_t_follow(v_ego: float, t_personality: float, v_lead: float | None = None, a_lead: float = 0.) -> float:
  """The close follow time (the safe limit): the schedule at schedule_speed(), never more than GAP_DEFICIT_MAX of gap
  under the personality's at vEgo, and never above the personality's - so it is the personality's from where the
  schedule reaches it (aggressive 17.2, standard 19.0, relaxed 21.6 km/h; sooner behind a lead speeding up)."""
  t = schedule_t_follow(schedule_speed(v_ego, v_lead, a_lead))
  if _finite(v_ego) and v_ego > _EPS:
    t = max(t, t_personality - GAP_DEFICIT_MAX / float(v_ego))
  return min(t, t_personality)


def close_jerk_factor(j_personality: float) -> float:
  """max(CF_JERK_FACTOR_MIN, the personality's): 1.0 on every personality today."""
  return max(CF_JERK_FACTOR_MIN, float(j_personality))


def speed_weight(t_close: float, t_personality: float) -> float:
  """How much of the closer gap there is: 1 wherever the close follow time is under the personality's, 0 from the
  hand-back on (there the close MPC would only differ by its jerk factor, which is not close follow's to change)."""
  return 1. if t_close < t_personality - 1e-6 else 0.


def lift_speed_weight(v_ego: float, t_close: float, t_personality: float) -> float:
  """The lift's window: in over LIFT_SPEED_BP (9 -> 12.6 km/h), and out over the last LIFT_T_FADE of the close follow
  time before the personality's - zero wherever t_close >= t_personality (above the hand-back the e2e plan is push12's)."""
  fade_in = float(np.interp(v_ego, LIFT_SPEED_BP, [0., 1.]))
  fade_out = min(1., max(0., (t_personality - t_close) / LIFT_T_FADE))
  return fade_in * fade_out


def desired_gap(v_ego: float, v_lead: float, t_follow: float) -> float:
  """The gap long_mpc's obstacle cost settles at behind a lead at v_lead with this follow time."""
  return t_follow * v_ego + STOP_DISTANCE + (v_ego ** 2 - v_lead ** 2) / (2 * COMFORT_BRAKE)


class Lead(NamedTuple):
  present: bool        # a real lead: present and not held
  d_rel: float
  v_lead: float
  v_rel: float
  a_lead: float        # aLeadK
  prob: float
  radar: bool
  track_id: int


NO_LEAD = Lead(False, 0., 0., 0., 0., 0., False, -1)


def lead_view(lead) -> Lead:
  """A radarState LeadData as close follow sees it: a held lead (held_lead.py) is no lead."""
  if lead is None or not real_lead(lead):
    return NO_LEAD
  return Lead(True, float(lead.dRel), float(lead.vLead), float(lead.vRel), float(lead.aLeadK), float(lead.modelProb),
              bool(lead.radar), int(lead.radarTrackId))


class ModelView(NamedTuple):
  a: float                  # action.desiredAcceleration
  should_stop: bool         # action.shouldStop
  vel: np.ndarray           # velocity.x at T_IDXS
  yaw_rate: np.ndarray      # orientationRate.z at T_IDXS
  curvature: float          # action.desiredCurvature
  hard_brake: bool          # meta.hardBrakePredicted
  lane_change: bool         # meta.laneChangeState != off
  lead_v: tuple             # leadsV3[k].v at LEAD_T_IDXS, the model's leads in order


def model_view(md) -> ModelView:
  return ModelView(float(md.action.desiredAcceleration), bool(md.action.shouldStop),
                   np.asarray(md.velocity.x, dtype=float), np.asarray(md.orientationRate.z, dtype=float),
                   float(md.action.desiredCurvature), bool(md.meta.hardBrakePredicted),
                   md.meta.laneChangeState != LaneChangeState.off,
                   tuple(np.asarray(ld.v, dtype=float) for ld in md.leadsV3))


class Inputs(NamedTuple):
  """One planner frame, as close follow needs it (inputs_from_sm() on the device, a route replay off it)."""
  engaged: bool             # openpilot longitudinal engaged: the planner's reset_state is False
  is_e2e: bool              # the plan has an e2e candidate
  v_ego: float
  personality: int
  lead_one: Lead
  lead_two: Lead
  held: bool                # radarState carries a held lead (radard's clutter guard)
  gas: bool
  brake: bool
  standstill: bool
  car_fcw: bool             # carState.stockFcw or stockAeb: the car's own radar warning or braking
  force_decel: bool
  steer_lat_accel: float    # controlsState.curvature * vEgo^2
  allow_throttle: bool
  model: ModelView
  lead_slowing: bool = False  # leadOne's own speed falling faster than LEAD_SLOWING_ACCEL (LeadSpeedWindow)
  pitch: float = math.nan     # carControl.orientationNED[1], rad, nose-up positive (NaN without a calibrated pose)
  lead_hard: bool = False     # a hard-braking lead now or within HARD_BRAKE_HOLD_S (CloseFollow fills it)
  downhill: bool = False      # the filtered pitch under DOWNHILL_PITCH (CloseFollow fills it)


def _pitch(cc) -> float:
  try:
    ned = list(cc.orientationNED)
    return float(ned[1]) if len(ned) == 3 else math.nan
  except Exception:
    return math.nan


def inputs_from_sm(sm, reset_state: bool, is_e2e: bool, allow_throttle: bool) -> Inputs:
  cs, rs = sm['carState'], sm['radarState']
  return Inputs(engaged=not reset_state, is_e2e=bool(is_e2e), v_ego=float(cs.vEgo),
                personality=_personality(sm['selfdriveState'].personality),
                lead_one=lead_view(rs.leadOne), lead_two=lead_view(rs.leadTwo),
                held=is_held(rs.leadOne) or is_held(rs.leadTwo), gas=bool(cs.gasPressed), brake=bool(cs.brakePressed),
                standstill=bool(cs.standstill), car_fcw=bool(cs.stockFcw) or bool(cs.stockAeb),
                force_decel=bool(sm['controlsState'].forceDecel),
                steer_lat_accel=float(sm['controlsState'].curvature) * float(cs.vEgo) ** 2,
                allow_throttle=bool(allow_throttle), model=model_view(sm['modelV2']), pitch=_pitch(sm['carControl']))


def _leads(inp: Inputs) -> list[Lead]:
  return [ld for ld in (inp.lead_one, inp.lead_two) if ld.present]


def lead_braking(inp: Inputs) -> bool:
  """Any real lead braking harder than GAP_LEAD_BRAKE (a non-finite aLeadK counts as braking), or leadOne's own speed
  falling as fast over the last LEAD_SLOWING_T."""
  return inp.lead_slowing or any(not (_finite(ld.a_lead) and ld.a_lead >= GAP_LEAD_BRAKE) for ld in _leads(inp))


def lead_hard_now(inp: Inputs, slope: float) -> bool:
  """A hard-braking lead this frame: leadOne's own speed slope (LeadSpeedWindow, over LEAD_SLOWING_T) under
  HARD_BRAKE_SLOPE, or any real lead's aLeadK under HARD_BRAKE_ALEAD (a non-finite aLeadK counts)."""
  return slope < HARD_BRAKE_SLOPE or any(not (_finite(ld.a_lead) and ld.a_lead >= HARD_BRAKE_ALEAD) for ld in _leads(inp))


def time_to_collision(lead: Lead) -> float:
  """leadOne's time to collision in s while closing on it faster than TTC_CLOSING; inf otherwise."""
  if not lead.present or not _finite(lead.d_rel, lead.v_rel) or -lead.v_rel <= TTC_CLOSING:
    return math.inf
  return max(0., lead.d_rel) / -lead.v_rel


def ttc_short(lead: Lead) -> bool:
  return time_to_collision(lead) < TTC_MIN_S


class LeadSpeedWindow:
  """leadOne's speed over the last LEAD_SLOWING_T, for one car: is its least-squares slope under LEAD_SLOWING_ACCEL
  while aLeadK is below 0? A new car (a radar track or source change, or no lead) starts the window again."""

  def __init__(self, dt: float = DT_MDL):
    self.dt = dt
    self.v: deque = deque(maxlen=int(round(LEAD_SLOWING_T / dt)) + 1)
    self.key = None
    self.slope = 0.

  def update(self, lead: Lead) -> bool:
    key = (lead.radar, lead.track_id) if lead.present and _finite(lead.v_lead) else None
    if key is None or key != self.key:
      self.v.clear()
    self.key = key
    if key is not None:
      self.v.append(lead.v_lead)
    n = len(self.v)
    if n < LEAD_SLOWING_MIN_N:
      self.slope = 0.
      return False
    t = np.arange(n, dtype=np.float64) * self.dt
    t -= t.mean()
    v = np.asarray(self.v)
    self.slope = float(np.dot(t, v - v.mean()) / np.dot(t, t))
    return self.slope < LEAD_SLOWING_ACCEL and not (_finite(lead.a_lead) and lead.a_lead >= 0.)


class CloseFollowSwitch:
  """THE ACTIVATION, decided 2026-10-24 ("Automatic in queues"): close follow is wanted whenever openpilot longitudinal
  is engaged, and off at every disengage; it then switches itself in and out by its own guards (a real radar lead,
  under the hand-back, every trip). It answers one question per plan - does the driver want close follow now? - and is
  the one place a manual on-switch (a distance-button hold) would go. The user's CloseFollow setting is not this: it
  picks live or shadow (CloseFollow.set_mode)."""
  SWITCH_ASSUMED = True

  def __init__(self):
    self.on = False

  def update(self, sm, engaged: bool) -> bool:
    if not engaged:
      self.on = False
    else:
      self.on = self.SWITCH_ASSUMED
    return self.on


class GapGuard:
  """The closer gap's authority (0..1): 0 at once on any trip, GAP_HOLD_S of clean frames, then up at GAP_RISE. The
  hand-back (speed weight 0: the close follow time is the personality's) resets it too (release13 review): before, the
  authority stayed up past the hand-back, so the moment the schedule speed fell again - a lead starting to brake - the
  closer gap was back at full authority on that frame, until the braking trip caught it 0.2-0.4 s later."""

  def __init__(self, dt: float = DT_MDL):
    self.dt = dt
    self.hold_frames = int(round(GAP_HOLD_S / dt))
    self.fcw_frames = int(round(FCW_HOLD_S / dt))
    self.fcw_left = 0
    self._reset(Inhibit.notRequested)

  def _reset(self, reason) -> None:
    self.authority = 0.
    self.hold_left = self.hold_frames
    self.inhibit = reason
    self.last_d_rel = math.nan

  def update(self, inp: Inputs, requested: bool, fcw: bool, weight: float = 1.) -> float:
    """fcw: the planner's FCW or the close MPC's crash flag this frame (the car's own is read from inp); weight: the
    speed weight (speed_weight), 0 from the hand-back."""
    if fcw or inp.car_fcw:
      self.fcw_left = self.fcw_frames
    elif self.fcw_left > 0:
      self.fcw_left -= 1
    if not requested:
      self._reset(Inhibit.notRequested)
      return 0.
    if not inp.engaged:
      self._reset(Inhibit.inactive)
      return 0.
    if not _finite(inp.v_ego):
      self._reset(Inhibit.invalid)
      return 0.
    if inp.v_ego >= CF_FADE_SPEED + CF_MPC_MARGIN:
      self._reset(Inhibit.speed)
      return 0.

    trip = self._trip(inp)
    if trip is None and not weight > 0.:
      trip = Inhibit.speed   # the hand-back: out, and back in only by the hold and the ramp
    if trip is not None:
      self.authority = 0.
      self.hold_left = self.hold_frames
      self.inhibit = trip
    elif self.hold_left > 0:
      self.hold_left -= 1
      self.inhibit = Inhibit.hold
    else:
      self.authority = min(1., self.authority + GAP_RISE * self.dt)
      self.inhibit = Inhibit.none
    return self.authority

  def _trip(self, inp: Inputs):
    lead = inp.lead_one
    expected = self.last_d_rel + lead.v_rel * self.dt
    self.last_d_rel = lead.d_rel if lead.present else math.nan
    if not lead.present or inp.held:
      return Inhibit.noLead
    if not _finite(lead.d_rel, lead.v_lead, lead.v_rel, lead.prob):
      return Inhibit.invalid
    if lead.prob < LEAD_PROB:
      return Inhibit.leadUncertain
    if inp.v_ego >= CF_VISION_SPEED and not lead.radar:
      return Inhibit.visionLead
    if inp.lead_hard:
      return Inhibit.leadHardBrake
    if lead_braking(inp):
      return Inhibit.leadBraking
    if math.isfinite(expected) and not (abs(lead.d_rel - expected) < LEAD_JUMP):
      return Inhibit.leadChanged
    if ttc_short(lead):
      return Inhibit.closing
    if inp.downhill:
      return Inhibit.downhill
    if self.fcw_left > 0:
      return Inhibit.fcw
    if inp.gas or inp.brake:
      return Inhibit.driver
    if inp.force_decel:
      return Inhibit.forceDecel
    return None


def plan_drop(vel: np.ndarray) -> float:
  """Most speed the model's plan loses within PLAN_SLOWDOWN_T, from its own v(0): a dip that recovers counts too."""
  return min(float(np.min(vel[SLOWDOWN_MASK])), float(np.interp(PLAN_SLOWDOWN_T, T_IDXS, vel))) - float(vel[0])


class LeadFollowLift:
  """The lift: the model's e2e accel toward close follow's MPC candidate, never past it (zoompilot, see the notice)."""

  def __init__(self, dt: float = DT_MDL):
    self.dt = dt
    self.hold_frames = int(round(LIFT_HOLD_S / dt))
    self._reset(Inhibit.notRequested)

  def _reset(self, reason) -> None:
    self.authority = 0.
    self.gain = 0.
    self.weight = 0.
    self.gap_excess = 0.
    self.boost = 0.
    self.hold_left = self.hold_frames
    self.inhibit = reason
    self.last_d_rel = math.nan
    self.last_track_id = -1

  def update(self, inp: Inputs, requested: bool, a_mpc: float, a_cruise: float, fcw: bool, t_follow: float) -> float:
    """Returns the e2e candidate: inp.model.a unchanged whenever the lift is idle. a_mpc: close follow's MPC candidate
    (the plan's own while the gap is out); t_follow: the follow time of that candidate."""
    md = inp.model
    a_model = md.a
    if not requested:
      self._reset(Inhibit.notRequested)
      return a_model
    if not inp.engaged:
      self._reset(Inhibit.inactive)
      return a_model
    if not inp.is_e2e:
      self._reset(Inhibit.notE2e)
      return a_model
    lead = inp.lead_one
    if (len(md.vel) != ModelConstants.IDX_N or len(md.yaw_rate) != ModelConstants.IDX_N or
        not _finite(inp.v_ego, a_model, a_mpc, a_cruise, md.curvature, inp.steer_lat_accel, t_follow) or
        not (np.isfinite(md.vel).all() and np.isfinite(md.yaw_rate).all()) or
        (lead.present and not _finite(lead.d_rel, lead.v_lead, lead.v_rel))):
      self._reset(Inhibit.invalid)
      return a_model

    v_ego = inp.v_ego
    lat_accel = max(abs(md.curvature) * v_ego ** 2, float(np.max(np.abs(md.yaw_rate * md.vel)[LAT_MASK])),
                    abs(inp.steer_lat_accel))
    lead_trip = self._lead_trip(inp)
    other_trip = self._trip(inp, a_model, plan_drop(md.vel), float(np.min(md.vel[LAT_MASK])), lat_accel, fcw)
    # a hard trip wins over a soft one, whichever check found it
    if lead_trip in HARD_LIFT_TRIPS or other_trip not in HARD_LIFT_TRIPS:
      trip = lead_trip if lead_trip is not None else other_trip
    else:
      trip = other_trip
    hard = trip in HARD_LIFT_TRIPS
    if trip is not None:
      self.authority = 0. if hard else max(0., self.authority - AUTHORITY_FALL * self.dt)
      self.hold_left = self.hold_frames
      self.inhibit = trip
    elif self.hold_left > 0:
      self.hold_left -= 1
      self.inhibit = Inhibit.hold
    else:
      self.authority = min(1., self.authority + AUTHORITY_RISE * self.dt)
      self.inhibit = Inhibit.none

    leads = _leads(inp)
    self.gap_excess = min(ld.d_rel - desired_gap(v_ego, ld.v_lead, t_follow) for ld in leads) if leads else 0.
    # zero wherever close follow's own follow time is the personality's (above the hand-back the e2e plan is push12's)
    t_personality = get_T_FOLLOW(inp.personality)
    t_close = close_t_follow(v_ego, t_personality, lead.v_lead if lead.present else None,
                             lead.a_lead if lead.present else 0.)
    self.weight = float(np.interp(self.gap_excess, GAP_BP, [0., 1.])) * lift_speed_weight(v_ego, t_close, t_personality)
    self.gain = float(np.interp(a_model, GAIN_BP, [0., 1.]))
    ceiling = min(a_mpc, a_cruise)
    headroom = max(0., ceiling - a_model)
    target = self.authority * self.gain * self.weight * min(CAP.get(inp.personality, CAP[int(Personality.standard)]), headroom)
    # falls at once (a binding MPC, a trip), rises at BOOST_RISE; a hard trip leaves nothing on this frame
    self.boost = 0. if hard else min(target, self.boost + BOOST_RISE * self.dt, headroom)
    # NEVER PAST the radar plan: at most what the MPC candidate (or cruise) asks, and never under the model's own
    return min(a_model + self.boost, max(a_model, ceiling))

  def _lead_trip(self, inp: Inputs):
    lead = inp.lead_one
    expected = self.last_d_rel + lead.v_rel * self.dt
    # a radar car's track can swap to another car at a similar range
    track_changed = lead.present and lead.radar and self.last_track_id >= 0 and lead.track_id != self.last_track_id
    self.last_d_rel = lead.d_rel if lead.present else math.nan
    self.last_track_id = lead.track_id if lead.present and lead.radar else -1
    if not lead.present or inp.held:
      return Inhibit.noLead
    if not lead.radar:
      return Inhibit.visionLead
    leads = _leads(inp)
    if inp.lead_hard:
      return Inhibit.leadHardBrake
    if lead_braking(inp):
      return Inhibit.leadBraking
    # each present lead's own forecast from the model, its lowest point within LEAD_SLOWDOWN_T
    for k in range(len(leads)):
      if k < len(inp.model.lead_v) and len(inp.model.lead_v[k]) == len(LEAD_T_IDXS):
        v = inp.model.lead_v[k]
        if np.isfinite(v).all():
          v_min = min(float(np.min(v[LEAD_SLOWDOWN_MASK])), float(np.interp(LEAD_SLOWDOWN_T, LEAD_T_IDXS, v)))
          if v_min < v[0] - LEAD_SLOWDOWN:
            return Inhibit.leadBraking
    if track_changed or (math.isfinite(expected) and not (abs(lead.d_rel - expected) < LEAD_JUMP)):
      return Inhibit.leadChanged
    if ttc_short(lead):
      return Inhibit.closing
    if any(ld.prob < LEAD_PROB for ld in leads):
      return Inhibit.leadUncertain
    if any(ld.v_lead < LEAD_MIN_SPEED for ld in leads):
      return Inhibit.leadSlow
    return None

  @staticmethod
  def _trip(inp: Inputs, a_model: float, plan_drop_v: float, plan_min_v: float, lat_accel: float, fcw: bool):
    md = inp.model
    if fcw or inp.car_fcw:
      return Inhibit.fcw
    if inp.gas or inp.brake:
      return Inhibit.driver
    if inp.force_decel:
      return Inhibit.forceDecel
    if inp.downhill:
      return Inhibit.downhill
    if md.hard_brake:
      return Inhibit.hardBrake
    if md.should_stop or plan_min_v < STOP_SPEED:
      return Inhibit.stop
    if a_model < MODEL_BRAKE_ACCEL:
      return Inhibit.modelBraking
    if plan_drop_v < -PLAN_SLOWDOWN:
      return Inhibit.planSlowing
    if lat_accel > LAT_ACCEL_MAX:
      return Inhibit.lateral
    if not inp.allow_throttle:
      return Inhibit.coast
    if md.lane_change:
      return Inhibit.laneChange
    if inp.v_ego < LIFT_SPEED_BP[0]:
      return Inhibit.speed
    return None


class CloseFollow:
  """The planner's handle. Per plan: update_mpc() right after the plan's own MPC solve and FCW, update_lift() once the
  cruise candidate is known, finish() with the min() the plan took, publish() with the plan. Live, the planner takes
  update_mpc()'s trajectories and update_lift()'s e2e candidate. In shadow (defer, the default for MODE_SHADOW) those
  three calls only record their arguments and hand back the plan's own values, and run_deferred() - which the planner
  calls in publish_deferred(), once plannerd has sent longitudinalPlan, longitudinalPlanSP and driverAssistance - runs
  them: the second MPC solve never delays a message controlsd acts on. A tool that wants the values right after update()
  sets defer = False (the values are the same, only computed earlier).
  mode: MODE_LIVE or MODE_SHADOW (read_mode() of the user's switch); with `params`, update_mpc() re-reads the switch
  every SWITCH_READ_S and at every engage and applies a change on that frame (set_mode).
  An exception anywhere in close follow never reaches plannerd: it is logged once (cloudlog.exception, error level),
  close follow is off for the rest of the drive, and every call hands back the plan's own values (push12's)."""

  def __init__(self, mode: int, dt: float = DT_MDL, mpc_factory=None, defer: bool | None = None, params=None):
    self.mode = MODE_LIVE if mode == MODE_LIVE else MODE_SHADOW
    self._defer_forced = defer
    self.defer = (self.mode != MODE_LIVE) if defer is None else bool(defer)
    self._pending: list | None = None
    self.failed = False
    self.dt = dt
    self.params = params
    self.switch_on = self.mode == MODE_LIVE   # the user's CloseFollow switch as last read
    self.switch_changes = 0
    self._read_frames = max(1, int(round(SWITCH_READ_S / dt)))
    self._read_left = self._read_frames
    self._was_engaged = False
    self.switch = CloseFollowSwitch()
    self.gap = GapGuard(dt)
    self.lift = LeadFollowLift(dt)
    self.lead_window = LeadSpeedWindow(dt)
    self.hard_frames = int(round(HARD_BRAKE_HOLD_S / dt))
    self.hard_left = 0            # frames the hard-brake hold has left
    self.pitch_f = 0.             # the filtered pitch (PITCH_TAU_S), from a level road
    self._pitch_k = dt / (PITCH_TAU_S + dt)
    self._mpc_factory = mpc_factory
    self.mpc = None
    self.mpc_warm = False
    self.inputs: Inputs | None = None
    self.requested = False
    self.eligible = False
    self.mpc_solved = False
    self.mpc_crash = False
    self.mpc_solve_time = 0.   # s, the close MPC's own solve this plan (acados time_tot)
    self.step_time = 0.        # s, wall time of all of close follow this plan
    self.g = 0.
    self.weight = 0.
    self.t_follow = self.t_personality = get_T_FOLLOW(Personality.standard)
    self.v_schedule = 0.          # m/s, the speed the schedule was read at (schedule_speed)
    self.jerk = 1.
    self.a_mpc = self.a_mpc_close = 0.
    self.a_model = self.a_model_lifted = 0.
    self.a_target = self.a_target_close = 0.
    self.raised = 0.           # m/s^2, the plan with close follow's candidates minus the plan without them (_run_finish)
    self.source_close = PlanSource.cruise
    self.frame = 0
    self.summary_frames = int(round(SUMMARY_S / dt))
    self._last_summary = 0
    self._win = self._new_window()

  @property
  def live(self) -> bool:
    return self.mode == MODE_LIVE

  def set_mode(self, mode: int) -> None:
    """Live or shadow, from the user's switch. A change applies on this frame: the gap and the lift start again from
    nothing (authority 0, their holds) - switched on, close follow ramps in as after any trip; switched off, it drops
    out at once, the plan's own on this frame. The FCW and hard-brake holds carry over."""
    mode = MODE_LIVE if mode == MODE_LIVE else MODE_SHADOW
    self.switch_on = mode == MODE_LIVE
    if mode == self.mode:
      return
    self.mode = mode
    if self._defer_forced is None:
      self.defer = mode != MODE_LIVE
    self._pending = None
    self.gap._reset(Inhibit.notRequested)
    self.lift._reset(Inhibit.notRequested)
    self.g = 0.
    self.a_model_lifted = self.a_model
    self.switch_changes += 1
    try:
      cloudlog.event("closefollow switch", error=True, mode=int(mode), on=bool(self.switch_on), changes=self.switch_changes)
    except Exception:
      pass

  def _poll_switch(self, engaged: bool) -> None:
    """Re-read the user's switch every SWITCH_READ_S and on every engage (reads only; never a write)."""
    if self.params is None:
      return
    self._read_left -= 1
    engage = engaged and not self._was_engaged
    self._was_engaged = engaged
    if self._read_left > 0 and not engage:
      return
    self._read_left = self._read_frames
    self.set_mode(read_mode(self.params))

  def _close_mpc(self):
    if self.mpc is None:
      if self._mpc_factory is None:
        from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc
        self._mpc_factory = LongitudinalMpc
      self.mpc = self._mpc_factory(dt=self.dt)
    return self.mpc

  def _seed(self, mpc, plan_mpc) -> None:
    """Warm-start the close MPC from the plan's own: its state, its solution and the a_prev it solved with."""
    mpc.reset()
    mpc.x0 = np.copy(plan_mpc.x0)
    for i in range(MPC_N + 1):
      mpc.solver.set(i, 'x', np.copy(plan_mpc.x_sol[i]))
    mpc.a_prev = np.copy(plan_mpc.params[:, 3])

  def _fail(self, where: str) -> None:
    if not self.failed:
      self.failed = True
      self.g = 0.
      try:
        cloudlog.exception(f"closefollow: {where} raised; close follow is off for this drive")
      except Exception:
        pass

  def update_mpc(self, sm, reset_state: bool, v_state: float, a_state: float, prev_accel_constraint: bool, fcw: bool,
                 plan_mpc, trajectories, control_t_idxs, action_t: float, is_e2e: bool = False,
                 allow_throttle: bool = True, inputs: Inputs | None = None, radar_state=None):
    """After the plan's own MPC solve. v_state, a_state: what the plan's MPC was set to (set_cur_state); fcw: the
    planner's FCW this frame; trajectories: the plan's (v, a, j) at control_t_idxs. Returns the trajectories with close
    follow blended in by the gap's authority - the very same arrays while it is out, and always in shadow (defer),
    where the call is only recorded for run_deferred(). First the user's switch, when it is due (_poll_switch): live or
    shadow is decided for the whole plan here."""
    if not self.failed:
      try:
        self._poll_switch(not reset_state)
      except Exception:
        self._fail('switch')
    args = (sm, reset_state, v_state, a_state, prev_accel_constraint, fcw, plan_mpc, trajectories, control_t_idxs,
            action_t, is_e2e, allow_throttle, inputs, radar_state)
    if self.defer:
      self._pending = None if self.failed else [(self._run_mpc, args)]
      return trajectories
    return self._run_mpc(*args)

  def _run_mpc(self, sm, reset_state, v_state, a_state, prev_accel_constraint, fcw, plan_mpc, trajectories,
               control_t_idxs, action_t, is_e2e, allow_throttle, inputs, radar_state=None):
    if self.failed:
      return trajectories
    t0 = time.monotonic()
    self.step_time = 0.
    try:
      return self._update_mpc(sm, reset_state, v_state, a_state, prev_accel_constraint, fcw, plan_mpc, trajectories,
                              control_t_idxs, action_t, is_e2e, allow_throttle, inputs, radar_state)
    except Exception:
      self._fail('update_mpc')
      return trajectories
    finally:
      self.step_time += time.monotonic() - t0

  def _update_mpc(self, sm, reset_state, v_state, a_state, prev_accel_constraint, fcw, plan_mpc, trajectories,
                  control_t_idxs, action_t, is_e2e, allow_throttle, inputs, radar_state=None):
    self.frame += 1
    inp = inputs if inputs is not None else inputs_from_sm(sm, reset_state, is_e2e, allow_throttle)
    inp = inp._replace(lead_slowing=self.lead_window.update(inp.lead_one))
    # the safe limit's holds: a hard-braking lead keeps it out HARD_BRAKE_HOLD_S; the pitch is filtered from a level
    # road (a brake's nose dive is not a hill) and kept through a frame without a calibrated pose
    self.hard_left = self.hard_frames if lead_hard_now(inp, self.lead_window.slope) else max(0, self.hard_left - 1)
    if _finite(inp.pitch):
      self.pitch_f += self._pitch_k * (inp.pitch - self.pitch_f)
    inp = inp._replace(lead_hard=self.hard_left > 0, downhill=self.pitch_f < DOWNHILL_PITCH)
    self.inputs = inp
    self.requested = self.switch.update(sm, inp.engaged)
    v_ego = inp.v_ego
    self.t_personality = get_T_FOLLOW(inp.personality)
    j_personality = get_jerk_factor(inp.personality)
    v_lead = inp.lead_one.v_lead if inp.lead_one.present else None
    a_lead = inp.lead_one.a_lead if inp.lead_one.present else 0.
    self.v_schedule = schedule_speed(v_ego, v_lead, a_lead) if _finite(v_ego) else 0.
    self.t_follow = close_t_follow(v_ego, self.t_personality, v_lead, a_lead) if _finite(v_ego) else self.t_personality
    self.weight = speed_weight(self.t_follow, self.t_personality) if _finite(v_ego) else 0.
    self.jerk = close_jerk_factor(j_personality)
    v_traj, a_traj, j_traj = trajectories
    self.a_mpc = float(get_accel_from_plan(v_traj, a_traj, control_t_idxs, action_t=action_t))

    self.eligible = (self.requested and inp.engaged and _finite(v_ego) and v_ego < CF_FADE_SPEED + CF_MPC_MARGIN and
                     inp.lead_one.present and not inp.held)
    self.mpc_solved = False
    self.mpc_crash = False
    self.mpc_solve_time = 0.
    close = None
    if self.eligible:
      mpc = self._close_mpc()
      if not self.mpc_warm:
        self._seed(mpc, plan_mpc)
      personality = sm['selfdriveState'].personality
      mpc.set_weights(prev_accel_constraint, personality=personality, jerk_factor=self.jerk)
      mpc.set_cur_state(v_state, a_state)
      # FORK(LEAD-BRAKE): the lead as the plan's own MPC saw it (lead_brake_assist.py's projection in its mode 2)
      mpc.update(sm['radarState'] if radar_state is None else radar_state, personality=personality, t_follow=self.t_follow)
      self.mpc_warm = True
      self.mpc_solved = True
      self.mpc_solve_time = float(getattr(mpc, 'solve_time', 0.))
      self.mpc_crash = bool(mpc.crash_cnt > 2 and not inp.standstill)
      close = (np.interp(control_t_idxs, T_IDXS_MPC, mpc.v_solution), np.interp(control_t_idxs, T_IDXS_MPC, mpc.a_solution),
               np.interp(control_t_idxs, T_IDXS_MPC[:-1], mpc.j_solution))
    else:
      self.mpc_warm = False

    authority = self.gap.update(inp, self.requested, bool(fcw) or self.mpc_crash, self.weight)
    self.g = authority if (close is not None and self.weight > 0.) else 0.
    if self.g <= 0. or close is None:
      self.g = 0.
      self.a_mpc_close = self.a_mpc
      return trajectories
    g = self.g
    blended = tuple(p + g * (c - p) for p, c in zip(trajectories, close, strict=True))
    self.a_mpc_close = float(get_accel_from_plan(blended[0], blended[1], control_t_idxs, action_t=action_t))
    return blended

  @property
  def t_follow_used(self) -> float:
    """The follow time of the MPC candidate close follow hands on (the personality's while the gap is out)."""
    return self.t_personality + self.g * (self.t_follow - self.t_personality)

  def update_lift(self, sm, a_model: float, a_cruise: float, is_e2e: bool, allow_throttle: bool, fcw: bool) -> float:
    """Once the cruise candidate is known. Returns the e2e candidate with the lift (a_model while it is idle, and
    always in shadow, where the call is only recorded)."""
    if self.defer:
      if self._pending is not None:
        self._pending.append((self._run_lift, (sm, a_model, a_cruise, is_e2e, allow_throttle, fcw)))
      return a_model
    return self._run_lift(sm, a_model, a_cruise, is_e2e, allow_throttle, fcw)

  def _run_lift(self, sm, a_model, a_cruise, is_e2e, allow_throttle, fcw) -> float:
    inp = self.inputs
    if inp is None or self.failed:
      return a_model
    t0 = time.monotonic()
    try:
      if inp.is_e2e != bool(is_e2e) or inp.allow_throttle != bool(allow_throttle) or inp.model.a != a_model:
        inp = self.inputs = inp._replace(is_e2e=bool(is_e2e), allow_throttle=bool(allow_throttle),
                                         model=inp.model._replace(a=float(a_model)))
      self.a_model = float(a_model)
      # an FCW (the planner's, the close MPC's crash flag, the car's) holds the lift out as long as the gap's FCW hold
      # (FCW_HOLD_S), then the lift's own LIFT_HOLD_S: it never comes back before the gap does
      lift_fcw = bool(fcw) or self.mpc_crash or self.gap.fcw_left > 0
      self.a_model_lifted = float(self.lift.update(inp, self.requested, self.a_mpc_close, float(a_cruise), lift_fcw,
                                                   self.t_follow_used))
      return self.a_model_lifted
    except Exception:
      self._fail('update_lift')
      return a_model
    finally:
      self.step_time += time.monotonic() - t0

  def finish(self, a_target: float, mpc_source, a_cruise: float, is_e2e: bool) -> None:
    """With the min() the plan took (before the re-entry ramp): what close follow's candidates would have given. In
    shadow only recorded, like the other two."""
    if self.defer:
      if self._pending is not None:
        self._pending.append((self._run_finish, (a_target, mpc_source, a_cruise, is_e2e)))
      return
    self._run_finish(a_target, mpc_source, a_cruise, is_e2e)

  def _run_finish(self, a_target, mpc_source, a_cruise, is_e2e) -> None:
    if self.failed:
      return
    t0 = time.monotonic()
    try:
      self.a_target = float(a_target)
      candidates = [(self.a_mpc_close, mpc_source), (float(a_cruise), PlanSource.cruise)]
      if is_e2e:
        candidates.append((self.a_model_lifted, PlanSource.e2e))
      self.a_target_close, self.source_close = min(candidates, key=lambda c: c[0])
      # what close follow does to the plan: its min() against the min() of the plan's own candidates (live, a_target is
      # already the former; in shadow the latter) - 0 exactly while it is out, whatever the gap and the lift say
      a_off = min(self.a_mpc, float(a_cruise), self.a_model) if is_e2e else min(self.a_mpc, float(a_cruise))
      self.raised = self.a_target_close - a_off
      self.step_time += time.monotonic() - t0
      self._account()
    except Exception:
      self._fail('finish')

  def run_deferred(self) -> None:
    """Shadow: this plan's update_mpc(), update_lift() and finish(), in that order, with the arguments the planner gave
    them. LongitudinalPlanner.publish() calls it once longitudinalPlan and longitudinalPlanSP are sent. Nothing
    pending (live, after a failure, already run): nothing. A plan whose publish() never came (a tool calling update()
    alone) is simply skipped by the next update_mpc()."""
    pending, self._pending = self._pending, None
    if not pending:
      return
    try:
      for fn, args in pending:
        fn(*args)
    except Exception:
      self._fail('run_deferred')

  # --- what reaches the route ---

  @property
  def lifting(self) -> float:
    return self.a_model_lifted - self.a_model

  @property
  def gap_active(self) -> bool:
    return self.g > 0.

  @property
  def active(self) -> bool:
    return self.gap_active or self.lifting > _EPS

  @property
  def gap_closer(self) -> bool:
    """The gap in and at least TIGHT_GAP_M shorter than the personality's at this speed (live or not): what release13
    first called tightening. It says the target is closer, not that the plan is - the model's e2e plan or cruise may be
    the lower candidate (the release13 review: 40 % of those frames on the owner's routes), so it is the log's only."""
    inp = self.inputs
    if self.failed or inp is None or not _finite(inp.v_ego):
      return False
    return self.g > 0. and (self.t_personality - self.t_follow_used) * max(inp.v_ego, 0.) >= TIGHT_GAP_M

  @property
  def tightening(self) -> bool:
    """Live AND actually following closer this frame: engaged, close follow in, and the plan with its candidates at
    least TIGHT_RAISE above the plan without them (raised) - not merely its gap in while the model's e2e plan or cruise
    sets the plan (the release13 review). What the comma 4's right-rail cue shows."""
    inp = self.inputs
    if not self.live or self.failed or inp is None or not inp.engaged or not self.active:
      return False
    return self.raised >= TIGHT_RAISE

  def _gap_inhibit(self):
    # (the hand-back is the guard's own 'speed' since the release13 review: it resets the authority)
    return self.gap.inhibit

  def fill(self, msg) -> None:
    inp = self.inputs
    lead = inp.lead_one if inp is not None else NO_LEAD
    v_ego = inp.v_ego if inp is not None else 0.
    msg.mode = int(self.mode)
    msg.requested = bool(self.requested)
    msg.active = bool(self.active)
    msg.gapActive = bool(self.gap_active)
    msg.gapInhibit = self._gap_inhibit()
    msg.gapAuthority = float(self.gap.authority)
    msg.speedWeight = float(self.weight)
    msg.tFollow = float(self.t_follow)
    msg.tFollowPersonality = float(self.t_personality)
    msg.jerkFactor = float(self.jerk)
    if lead.present and _finite(lead.d_rel, lead.v_lead, v_ego):
      msg.desiredGap = float(desired_gap(v_ego, lead.v_lead, self.t_follow))
      msg.desiredGapPersonality = float(desired_gap(v_ego, lead.v_lead, self.t_personality))
      msg.dRel = float(lead.d_rel)
      msg.vLead = float(lead.v_lead)
    leads = _leads(inp) if inp is not None else []
    a_leads = [ld.a_lead for ld in leads if _finite(ld.a_lead)]
    msg.aLead = float(min(a_leads)) if a_leads else 0.
    msg.mpcSolved = bool(self.mpc_solved)
    msg.mpcCrash = bool(self.mpc_crash)
    msg.aMpc = float(self.a_mpc)
    msg.aMpcClose = float(self.a_mpc_close)
    msg.aModel = float(self.a_model)
    msg.aModelLifted = float(self.a_model_lifted)
    msg.lift = float(self.lifting)
    msg.liftInhibit = self.lift.inhibit
    msg.liftAuthority = float(self.lift.authority)
    msg.liftWeight = float(self.lift.weight)
    msg.liftGain = float(self.lift.gain)
    msg.gapExcess = float(self.lift.gap_excess)
    msg.isE2e = bool(inp.is_e2e) if inp is not None else False
    msg.aTarget = float(self.a_target)
    msg.aTargetClose = float(self.a_target_close)
    msg.sourceClose = int(self.source_close)
    msg.mpcSolveTime = float(self.mpc_solve_time)
    msg.stepTime = float(self.step_time)
    msg.switchOn = bool(self.switch_on)
    msg.live = bool(self.live)
    msg.tightening = bool(self.tightening)
    msg.raised = float(self.raised)
    msg.gapCloser = bool(self.gap_closer)
    msg.pitchFiltered = float(self.pitch_f)
    ttc = time_to_collision(lead)
    msg.ttc = float(ttc) if math.isfinite(ttc) else -1.
    msg.hardBrakeHold = float(self.hard_left * self.dt)
    msg.scheduleSpeed = float(self.v_schedule)

  def publish(self, pm) -> None:
    """customReserved10, once per plan; nothing after a failure (the cloudlog line says why)."""
    if self.failed or SERVICE not in getattr(pm, 'sock', {}):
      return
    try:
      m = messaging.new_message(SERVICE)
      m.valid = True
      self.fill(getattr(m, SERVICE))
      pm.send(SERVICE, m)
    except Exception:
      self._fail('publish')

  # --- the cloudlog summary ---

  @staticmethod
  def _new_window() -> dict:
    return {'n': 0, 'gap': 0, 'lift': 0, 'live': 0, 't': 0., 'dg': 0., 'v': 0., 'lmax': 0., 'up': 0., 'brk': 0, 'fcw': 0,
            'smax': 0., 'xmax': 0., 'why': Counter()}

  def _account(self) -> None:
    inp = self.inputs
    w = self._win
    w['smax'] = max(w['smax'], self.mpc_solve_time)
    w['xmax'] = max(w['xmax'], self.step_time)
    chance = (inp is not None and self.requested and inp.engaged and inp.lead_one.present and not inp.held and
              _finite(inp.v_ego) and inp.v_ego < CF_FADE_SPEED)
    if chance:
      w['n'] += 1
      w['v'] += inp.v_ego
      w['why'][self._gap_inhibit()] += 1
      if self.gap_active:
        w['gap'] += 1
        w['t'] += self.t_follow_used
        w['dg'] += (self.t_follow_used - self.t_personality) * inp.v_ego
      if self.lifting > _EPS:
        w['lift'] += 1
        w['lmax'] = max(w['lmax'], self.lifting)
      if self.tightening:
        w['live'] += 1
      w['up'] = max(w['up'], self.raised)   # (a_target_close - a_target is 0 live: a_target already has close follow)
      # (live, the plan's min() already holds close follow's candidates: the MPC candidate against the plan's own says
      # whether it raised the plan)
      if lead_braking(inp) and (self.lifting > _EPS or self.a_target_close > self.a_target + 1e-6 or
                                self.a_mpc_close > self.a_mpc + 1e-6):
        w['brk'] += 1
      if self.gap.inhibit == Inhibit.fcw:
        w['fcw'] += 1
    if self.frame - self._last_summary >= self.summary_frames:
      self._last_summary = self.frame
      if w['n'] > 0:
        self._emit(w)
      self._win = self._new_window()

  def _emit(self, w: dict) -> None:
    try:
      n, g = w['n'], w['gap']
      names = {v: k for k, v in Inhibit.schema.enumerants.items()}
      why = ",".join(f"{names.get(int(k), int(k))}:{c}" for k, c in w['why'].most_common(3))
      cloudlog.event("closefollow", error=True, mode=int(self.mode), sw=int(self.switch_on), n=n, v=round(w['v'] / n, 1),
                     gap=round(g / n, 2), lift=round(w['lift'] / n, 2), live=round(w['live'] / n, 2),
                     t=round(w['t'] / g, 2) if g else None, dg=round(w['dg'] / g, 1) if g else None,
                     lmax=round(w['lmax'], 2), up=round(w['up'], 2), brk=w['brk'], fcw=w['fcw'], why=why,
                     smax=round(w['smax'] * 1e3, 1), xmax=round(w['xmax'] * 1e3, 1))
    except Exception:
      pass
