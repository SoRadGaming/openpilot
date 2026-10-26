"""
FORK(CLOSE-FOLLOW): close follow in queues (the owner's round-5 request: "close up in motorway queues so cars can't cut
in, gap grows back with speed"). push13 built it in shadow; release13 makes it LIVE (owner, 2026-10-24: "Automatic in
queues", "Live now, with a safety limit", "Safe limit only").

THE SWITCH: one user setting, CloseFollow (a BOOL, default ON; comma 4 Settings > Toggles 'close follow at 1 bar',
sunnylink Vehicle > Honda 'Close Follow'). ON: live - close follow shapes the plan - but since push14 (the four dash
bars, distance_bars.py) only in the dash's 1-bar mode, the fourth step of the steering wheel's distance button
(4 relaxed, 3 standard, 2 aggressive, 1 aggressive with close follow). OFF, or at 4, 3 or 2 bars: shadow - it computes
and logs everything below every plan and changes nothing (the plan, the accel sent, the HUD, every CAN frame are as
without it), and with the switch off the button never reaches 1 bar. The planner reads the switch when plannerd starts,
again every SWITCH_READ_S (3 s), at every engage and on the plan the 1-bar mode comes on (reads only, never a write;
selfdrived's own 0.1 s read of the switch is in that mode, so the master switched on at a remembered 1 bar is live on the
plan the dash shows 1 bar - the push14 review), and the 1-bar mode every plan from
selfdriveStateSP.closeFollowOneBar (read_one_bar); a change of either applies on that frame: into live, the gap and the
lift start from nothing (the 1 s hold, then the ramp); out of it, close follow drops out at once, as on any trip. A
switch or a 1-bar mode that cannot be read is OFF (shadow).

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
   GAP_DEFICIT_MAX (0.85 m since round 3; 1 m before) under the personality's gap at that same speed and never above
   the personality's (close_t_follow) - and since round 2 (below) never while the gap the radar measures is already
   REAL_GAP_MAX short.
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
   snaps back in at full authority when the lead slows (the schedule speed falls the moment a lead brakes). The review
   said what was left was the closer gap itself: at most 1 m less room in such a wave.
   THE RELEASE13 VERIFICATION (round 2) showed that was the TARGET (the follow time x vEgo), not the gap the car really
   keeps. In a gentle pull-away from a crawl (0.6-0.8 m/s^2, under the look-ahead's dead band, the commonest kind) the
   closer gap stayed in at full authority while the car sped up and the real gap fell to 1.4 m short of push12's
   (touches at up to 5.1 km/h where push12 had kept up to 0.8 m); stop-and-go waves stacked the offset (up to 2.7 m less
   room, standstill gaps up to 2.5 m shorter); a slower car cutting in within LEAD_JUMP of the old lead's range kept the
   closer gap. Since round 2:
   * THE REAL GAP: leadOne's radar gap against the personality's own desired gap - desired_gap() at vEgo and vLead with
     the personality's follow time, what push12's MPC aims for - filtered over REAL_GAP_TAU_S, per car (realDeficit).
     From REAL_GAP_MAX (0.8 m since round 3, 0.9 m in round 2) short close follow raises nothing - neither the gap nor
     the lift - and it fades out over the REAL_GAP_FADE (0.25 m) before that, so there is no on/off at the line
     (real_weight, realWeight; the gap held out by it alone logs realGap). Under 1 m: push12 itself settles 0.1-0.2 m
     past its desired gap, so a limit at the desired gap's 1 m is more than 1 m short of the gap push12 really keeps. It
     bounds what stop-and-go waves stack up, and the stop: a car following closer arrives at the stop no more than that
     much closer.
   * A QUEUE PULLING AWAY - leadOne's aLeadK over PULL_AWAY_ACCEL (0.3 m/s^2) for PULL_AWAY_T (0.5 s), the same car - is
     a soft trip (pullAway): nothing tightens, the gap's authority falls at PULL_AWAY_FALL (1/s) and the lift's at its
     own 4/s, then their holds. (Chosen over a smaller look-ahead dead band, 0.1, by the verifier's
     own sims: on its gentle pull-aways the dead band still touched at up to 3.2 km/h where push12 kept 0.45 m and cost
     up to 0.9 m at the closest; the rule 1.2 km/h where push12 kept 0.12 m, and 0.5 m.)
   * The 1 m cap is held at the speed the queue is heading for (schedule_speed), not at vEgo: capped at a crawl's own
     speed, a launch planned its whole horizon at the crawl's short follow time.
   * The close follow time shortens at most T_FOLLOW_FALL (0.1 s per s) and lengthens at once: under the hand-back, a
     lead that stopped accelerating dropped it from the look-ahead's to the crawl's on one frame, at full authority.
   * A radar track swap is a new car for the gap (leadChanged), as it was for the lift; the hand-back resets the lift as
     it resets the gap (the lift was back before the gap's 1 s hold); the mark never shows at a standstill.
   * The closer gap fades in over CF_SPEED_IN_BP (3.6 -> 7.2 km/h): at a standstill, creeping into a stop and through a
     launch the plan is push12's. A car that comes to rest a moment sooner also sets off a moment sooner (LongControl's
     stopping state), and in stop-and-go waves that put it up to 1.8 m off push12; under 7 km/h the closer gap was worth
     under a meter anyway (0.8 m at 5 km/h on aggressive). gapInhibit says speed there.
   Measured in the verifier's own closed-loop sim (9,960 runs in 14 kinds of traffic, each against push12's plan with
   close follow absent; docs/CHANGELOG_SERIAL_STEERING.md, push13): 43 touches where push12 stopped short (92 before),
   none with the closer gap in (1), all where push12 itself kept 0.63 m or less, at up to 4.4 km/h (5.1); where both stop
   short, at most 0.97 m less room when the lead brakes (2.9) and 0.74 m at the closest point (2.7) - but for a hard
   brake 3-5 s after a launch from a stop, 2.1 m (p95 0.84). That one is the launch's own timing: a car that comes to
   rest a moment sooner sets off a moment sooner, and push12 itself, moved 0.2 / 0.4 m closer as the lead begins to slow
   (close follow's car was at most 0.38 m closer there), comes 1.4 / 2.6 m closer at that point. Before a brake the real
   gap is at most 1.03 m short of push12's at the same moment (p95 0.84; 3 cm over while push12's own gap swung 0.26 m
   past its target).
   THE VERIFICATION OF ROUND 2 (round 3: a closed-loop sim written from scratch - its own plant with the measured gas
   response and a brake that builds only while the pump runs, 10 Hz radar with latency, noise, dropouts and track ids;
   7,996 pairs in 17 families - and a mutation-tested code review) found no safety bug, but the real gap 1.03-1.15 m
   short of push12's in steady following, slow pull-aways under PULL_AWAY_ACCEL (0.15-0.25 m/s^2), a slow accordion,
   cut-ins and personality changes (no touch in any). Two causes: REAL_GAP_MAX was 0.9 m against push12's TARGET, which
   push12 overshoots by 0.1-0.2 m; and while both chase a queue speeding up slowly, both behind their targets, the
   real-gap limit never acts and the closer target pulled the car up to 0.15 m past its own difference. So since round
   3 the target is GAP_DEFICIT_MAX 0.85 m and the real-gap limit REAL_GAP_MAX 0.8 m: in that sim the real gap is at
   most 0.95 m short of push12's at the same moment in every family but one (1.15 before), 25 touches where push12
   stopped short (27), all where push12 itself kept 0.41 m or less (0.53), at up to 4.4 km/h, none with the closer gap
   in; in the first verifier's sim (9,960 runs) 0.91 m before a brake (1.03) and 41 such touches (43), push12 having
   kept 0.63 m or less, at up to 3.9 km/h (4.4). The stop-launch-brake case stays - at the closest point 1.4 m in the
   first sim (2.1) and 1.8 m in the second (1.8): the closeness left as the lead begins to slow is not the limit's - it
   is what the car had not given back yet - and push12 moved 0.4 m closer there does the same (1.8 / 2.6 m). Holding
   1 m there too took the closer gap down to 0.25 m below 12 km/h (tried, not done - the owner's call): 0.6 m in that
   case, but 73 % fewer frames raising the plan in the second sim's critical families, and on the owner's 20 routes
   10 s raising it instead of 27 (a 0.23 m closer target at the median instead of 0.56).
   (Tried as well: a second pull-away rule, aLeadK over 0.15 for 1.5 s - as good on the slow pull-aways, but 11 % less
   time with the closer gap in across the families against 1 % for the lower limits.)
   The jerk factor is max(1.0, the personality's) (CF_JERK_FACTOR_MIN: not the aggressive 0.5, which the envelope showed
   reacts later to a braking lead). STOP_DISTANCE is compiled into the solver and untouched: the plan still aims for 6 m
   at a standstill, as push12's. Arriving from a closer gap the car stopped closer than push12 (the release13 review's
   sim: 0.5-1.3 m after a gentle stop; the verification: up to 2.5 m after stop-and-go waves); since round 2 the real
   gap bounds it and under 3.6 km/h the closer gap is out: at most 0.13 m closer in the verifier's sim (p95 0.08).
   CF_FADE_SPEED (22 km/h, the schedule's last point) is the one
   eligibility bound: the close MPC solves below it + CF_MPC_MARGIN, and above the hand-back it is the plan's own.
   Its candidate is blended into the plan's by gapAuthority: a = a_plan_mpc + authority * (a_close_mpc - a_plan_mpc),
   the trajectories alike. Authority drops to 0 AT ONCE on any trip, so on that frame the MPC candidate is the plan's
   own MPC, push12's, exactly. It waits out GAP_HOLD_S (1 s) of clean frames (the close MPC warms up), then rises at
   GAP_RISE (0.5/s). Trips: no real lead (or a held one, radard's clutter guard), lead modelProb under 0.8, a vision-only
   lead above CF_VISION_SPEED (2 m/s; the radar sees nothing at a stop and little under 2 m/s, where t*v is under 1 m),
   a braking lead (any real lead's aLeadK under -0.5 m/s^2, or leadOne's own speed falling faster than that over the
   last 0.5 s with aLeadK below 0 - aLeadK is a filter and trails the onset), a new car (leadOne 3 m off where it should
   be, or since round 2 its radar track swapped), FCW (the planner's, the close MPC's own crash flag, or the car's
   FCW/AEB, held FCW_HOLD_S = 5 s), gas or brake, forceDecel, and since release13 three more: a HARD-braking lead
   (leadOne's own speed falling faster than 2.0 m/s^2 over 0.5 s, or any real lead's aLeadK under -1.5) holds it out
   HARD_BRAKE_HOLD_S (3 s); a downhill steeper than 3 % (carControl.orientationNED pitch, filtered over PITCH_TAU_S,
   under DOWNHILL_PITCH); a closing lead with a time to collision under TTC_MIN_S (3 s, closing faster than 0.5 m/s).
   Round 2's two are not trips of the guard's kind: the queue pulling away fades the authority (above), and the real
   gap scales whatever authority there is (g = authority x realWeight).

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
   model's hard-brake flag or a stop in its plan, and (round 2) the hand-back: from where close follow's own follow time
   is the personality's, the lift is out with its authority at 0, as the gap is. Soft trips (lead uncertain or under
   LEAD_MIN_SPEED 2.5 m/s, the model braking under -0.5 or its plan slowing, over 1 m/s^2 of lateral accel, coasting, a
   lane change, under 2.5 m/s, and since round 2 the queue pulling away) let it fall at 4/s. Then a 3 s hold
   (LIFT_HOLD_S, with its authority at 0 after a hard trip) and a 0.5/s rise, as zoompilot. Since round 2 it is also
   scaled by the real gap (realWeight), as the gap is.

NOT CHANGED for close follow: LongControl's kiV, the PID floor and the brake's rise limiter are the car's, as without it.

GATED: HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (close_follow_applies(), the re-entry ramp's
gate). Every other car and stock ACC mode never build it: upstream's planner bit for bit, no second MPC, no
customReserved10.

LOGGED (the owner has no SSH): customReserved10 (CustomReserved10 in custom.capnp) every plan, 2 Hz in the qlog -
since release13 with switchOn (the user switch as last read), live, raised (how far its candidates raise the plan's
min() over the plan's own: what it does to the plan, or would in shadow), tightening (live, engaged and raised by
TIGHT_RAISE or more: what the comma 4's right-rail cue shows - the release13 review: not merely the gap in while the
model's plan or cruise is the lower candidate) and gapCloser (the gap in and 0.5 m shorter: release13's first
'tightening'), and since round 2 realDeficit (the personality's desired gap minus the radar gap, filtered), realWeight
(how much of it may still raise the plan) and pullAway - and at most one `closefollow` cloudlog event at error level per
SUMMARY_S (10 s), only when there was a chance to act (engaged, a real lead, under the eligibility bound): `mode` (1
shadow, 2 live), `sw` (the switch), the share of those frames with the gap and the lift in, `live` (the share
tightening live), the mean follow time and gap change, `up` (the most it raised the plan), the top reasons it was out
(round 2 adds realGap and pullAway), `brk`, the frames where it lifted the plan while a lead braked - by construction 0;
a non-zero `brk` in a route is a bug - `smax` / `xmax`, the slowest close-MPC solve and the slowest whole close-follow
step of those 10 s, in ms (mpcSolveTime / stepTime, every plan), (round 2) `rmax`, the largest realDeficit while the
gap was in - at or above REAL_GAP_MAX is a bug, but by construction it cannot show more (the weight is 0 from there) -
and (round 3) `rraw`, the largest UNFILTERED real deficit while the gap was in and REAL_RAW_TAIL_S (1 s) after, on the
same car: what the radar really showed, the one to read against 'about 1 m'. A switch change is one `closefollow
switch` line.

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
# push14: the gate and the switch live in close_follow_gate.py (light, shared with distance_bars.py); re-exported here
from openpilot.sunnypilot.selfdrive.controls.lib.close_follow_gate import (SWITCH_PARAM as SWITCH_PARAM, read_switch,
                                                                           close_follow_applies as close_follow_applies)

Inhibit = custom.CustomReserved10.CloseFollowInhibit
LaneChangeState = log.LaneChangeState
Personality = log.LongitudinalPersonality
PlanSource = log.LongitudinalPlan.LongitudinalPlanSource

# CloseFollow (params_keys.h, BOOL, default "1"): the user's switch, SWITCH_PARAM. Since push14 the master: live needs
# it ON and the dash's 1-bar mode (selfdriveStateSP.closeFollowOneBar, distance_bars.py); anything else is shadow.
ONE_BAR_SERVICE = "selfdriveStateSP"
SWITCH_READ_S = 3.0             # s: plannerd re-reads it this often (at every engage and when the 1-bar mode comes on too); reads only
MODE_SHADOW, MODE_LIVE = 1, 2   # customReserved10.mode
SERVICE = "customReserved10"

# --- the gap: the safe limit (release13; cfenv's skeptic, skeptic_schedule.json) ---
CF_T_FOLLOW_BP = [0., 3.75, 4.167, 5.0, 6.111]   # m/s: 0, 13.5, 15, 18, 22 km/h
CF_T_FOLLOW_V = [0.50, 0.88, 1.00, 1.34, 1.80]   # s, at max(vEgo, the real lead's vLead); never above the personality's
CF_FADE_SPEED = CF_T_FOLLOW_BP[-1]  # m/s: the one eligibility bound (22 km/h); every personality hands back below it
# the release13 review: a queue pulling away from a crawl, then braking hard
LEAD_AHEAD_S = 2.0              # s: the schedule is read at the lead's speed this far ahead on its acceleration ...
LEAD_AHEAD_DEADBAND = 0.3       # m/s^2: ... above this (a creeping queue's swell is not a pull-away)
GAP_DEFICIT_MAX = 0.85          # m: the closer gap is never more than this under the personality's, at the speed the
                                # queue is heading for (schedule_speed) - the MPC plans the launch at that speed. 0.15 m
                                # inside the 1 m it promises (round 3: chasing a slowly accelerating queue the real gap
                                # ran up to 0.15 m past the target's difference)
# the release13 verification (round 2): GAP_DEFICIT_MAX bounds the TARGET; the gap the car really keeps drifted further
# (a gentle pull-away, stop-and-go waves). So close follow also reads the REAL gap, and leaves a queue pulling away alone
REAL_GAP_MAX = 0.8              # m: the radar gap this far under the personality's own desired gap (long_mpc's
                                # distance at vEgo and vLead): close follow raises nothing - neither the gap nor the lift.
                                # 0.2 m inside the 1 m it promises: push12 itself settles 0.1-0.2 m past its desired gap
                                # (round 3: 0.9 left steady following 1.03-1.06 m short of push12)
REAL_GAP_FADE = 0.25            # m: ... and fades out over the last this before it (no on/off chatter at the line)
REAL_GAP_TAU_S = 0.5            # s: the real deficit's filter (dRel and vLead are the radar's raw values)
REAL_RAW_TAIL_S = 1.0           # s: the log's rraw - the unfiltered deficit's max while the gap is in and this long after
PULL_AWAY_ACCEL = 0.3           # m/s^2: leadOne's aLeadK above this ...
PULL_AWAY_T = 0.5               # s: ... this long (the same car): the queue pulls away - no new tightening ...
PULL_AWAY_FALL = 1.0            # 1/s: ... and the closer gap fades out at this (then the 1 s hold once it is over)
T_FOLLOW_FALL = 0.1             # s per s: the close follow time shortens no faster than this (it lengthens at once)
CF_SPEED_IN_BP = [1.0, 2.0]     # m/s (3.6 -> 7.2 km/h): the closer gap fades in over this - at a standstill, creeping
                                # into a stop and through a launch the plan is push12's (a stop a moment sooner sets off
                                # the launch a moment sooner: stop-and-go waves up to 1.8 m apart)
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


def read_mode(params) -> int:
  """The mode the switch asks for: MODE_LIVE when it is on, MODE_SHADOW otherwise (read_switch). Since push14 the
  planner's close follow also needs the 1-bar mode for live (read_one_bar, CloseFollow(one_bar_gate=True))."""
  return MODE_LIVE if read_switch(params) else MODE_SHADOW


def read_one_bar(sm) -> bool:
  """push14: the dash's 1-bar mode in effect, as selfdrived publishes it (selfdriveStateSP.closeFollowOneBar,
  distance_bars.py). A message without it (a route from before push14) or anything unreadable is off: shadow."""
  try:
    return bool(sm[ONE_BAR_SERVICE].closeFollowOneBar)
  except Exception:
    return False


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
  under the personality's at that same speed - where the queue is heading, at least vEgo (round 2: capped at vEgo alone,
  a launch from a crawl planned its whole horizon at the crawl's follow time and set off ahead of push12) - and never
  above the personality's. So it is the personality's from where the schedule reaches it (aggressive 17.2, standard
  19.0, relaxed 21.6 km/h; sooner behind a lead speeding up)."""
  v_s = schedule_speed(v_ego, v_lead, a_lead)
  t = schedule_t_follow(v_s)
  if _finite(v_s) and v_s > _EPS:
    t = max(t, t_personality - GAP_DEFICIT_MAX / float(v_s))
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


def real_weight(deficit: float) -> float:
  """How much close follow may still raise the plan, from the real deficit (m: the personality's desired gap minus the
  radar gap, filtered): 1 up to REAL_GAP_MAX - REAL_GAP_FADE, 0 from REAL_GAP_MAX on, linear between. A non-finite
  deficit is 0."""
  if not _finite(deficit):
    return 0.
  return min(1., max(0., (REAL_GAP_MAX - float(deficit)) / REAL_GAP_FADE))


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
  under the hand-back, every trip). It answers one question per plan - does the driver want close follow now? The
  user's CloseFollow setting is not this, nor (push14) the dash's 1-bar mode the distance button selects: together they
  pick live or shadow (CloseFollow._wanted_mode, set_mode)."""
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
    self.last_track_id = -1

  def update(self, inp: Inputs, requested: bool, fcw: bool, weight: float = 1., pull_away: bool = False) -> float:
    """fcw: the planner's FCW or the close MPC's crash flag this frame (the car's own is read from inp); weight: the
    speed weight (speed_weight), 0 from the hand-back; pull_away: the queue pulls away (round 2) - a soft trip: the
    authority does not rise and falls at PULL_AWAY_FALL, and once it is over the 1 s hold and the ramp again."""
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
    if trip is None and pull_away:
      # a soft trip: no new tightening, the closer gap fades out (the queue reopens the gap by itself), then the hold
      self.authority = max(0., self.authority - PULL_AWAY_FALL * self.dt)
      self.hold_left = self.hold_frames
      self.inhibit = Inhibit.pullAway
    elif trip is not None:
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
    # round 2: a radar car's track swapping to another car is a new car, as for the lift - a slower car cutting in within
    # LEAD_JUMP of the old one's range kept the closer gap
    track_changed = lead.present and lead.radar and self.last_track_id >= 0 and lead.track_id != self.last_track_id
    self.last_d_rel = lead.d_rel if lead.present else math.nan
    self.last_track_id = lead.track_id if lead.present and lead.radar else -1
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
    if track_changed or (math.isfinite(expected) and not (abs(lead.d_rel - expected) < LEAD_JUMP)):
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

  def update(self, inp: Inputs, requested: bool, a_mpc: float, a_cruise: float, fcw: bool, t_follow: float,
             pull_away: bool = False, real_w: float = 1., t_close: float | None = None) -> float:
    """Returns the e2e candidate: inp.model.a unchanged whenever the lift is idle. a_mpc: close follow's MPC candidate
    (the plan's own while the gap is out); t_follow: the follow time of that candidate; pull_away: the queue pulls away
    (a soft trip, round 2); real_w: real_weight() of the real gap (the lift scales by it); t_close: close follow's own
    follow time (CloseFollow.t_follow; None: close_t_follow() here). From the hand-back on (t_close at the
    personality's) the lift is out with its authority at 0 - it comes back only by its 3 s hold and its ramp, after the
    gap (round 2: it kept its authority and was back before the gap's 1 s hold)."""
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
    # zero wherever close follow's own follow time is the personality's (above the hand-back the e2e plan is push12's)
    t_personality = get_T_FOLLOW(inp.personality)
    if t_close is None:
      t_close = close_t_follow(v_ego, t_personality, lead.v_lead if lead.present else None,
                               lead.a_lead if lead.present else 0.)
    if trip is None and not speed_weight(t_close, t_personality) > 0.:
      trip, hard = Inhibit.speed, True    # the hand-back resets it, as the gap's (round 2)
    elif trip is None and pull_away:
      trip = Inhibit.pullAway             # soft: falls at AUTHORITY_FALL, then the 3 s hold
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
    self.weight = (float(np.interp(self.gap_excess, GAP_BP, [0., 1.])) * lift_speed_weight(v_ego, t_close, t_personality) *
                   min(1., max(0., float(real_w))))
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
  one_bar_gate (push14, the planner's): live also needs the dash's 1-bar mode, which update_mpc() reads every plan
  (read_one_bar: selfdriveStateSP.closeFollowOneBar) and applies on that frame too, re-reading the switch on the plan it
  comes on (_poll_mode); until the first plan it is shadow.
  An exception anywhere in close follow never reaches plannerd: it is logged once (cloudlog.exception, error level),
  close follow is off for the rest of the drive, and every call hands back the plan's own values (push12's)."""

  def __init__(self, mode: int, dt: float = DT_MDL, mpc_factory=None, defer: bool | None = None, params=None,
               one_bar_gate: bool = False):
    self.switch_on = mode == MODE_LIVE   # the user's CloseFollow switch as last read
    self.one_bar_gate = bool(one_bar_gate)
    self.one_bar = False                 # push14: the dash's 1-bar mode as last read (one_bar_gate only)
    self.mode = self._wanted_mode()
    self._defer_forced = defer
    self.defer = (self.mode != MODE_LIVE) if defer is None else bool(defer)
    self._pending: list | None = None
    self.failed = False
    self.dt = dt
    self.params = params
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
    # round 2: the real gap, the pull-away and the close follow time's fall limit
    self.real_deficit = math.nan  # m, the personality's desired gap minus leadOne's radar gap, filtered (REAL_GAP_TAU_S)
    self.real_w = 0.              # real_weight(real_deficit): how much close follow may still raise the plan
    self._real_k = dt / (REAL_GAP_TAU_S + dt)
    self._real_key = None         # the car the filter is on
    self.real_raw = math.nan      # m, the same deficit unfiltered, this frame
    self.raw_watch = False        # the gap in, or out under REAL_RAW_TAIL_S on the same car: rraw counts this frame
    self._raw_tail_frames = int(round(REAL_RAW_TAIL_S / dt))
    self._raw_tail = 0
    self.pull_frames = 0          # frames leadOne's aLeadK has been over PULL_AWAY_ACCEL (the same car)
    self.pull_away = False
    self._pull_n = max(1, int(round(PULL_AWAY_T / dt)))
    self._t_prev = math.nan       # last plan's close follow time (T_FOLLOW_FALL)
    self.speed_in = 0.            # the closer gap's fade-in over CF_SPEED_IN_BP at vEgo
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

  def _wanted_mode(self) -> int:
    """Live while the user's switch is on and - the planner's close follow (one_bar_gate, push14) - the dash is in its
    1-bar mode; shadow otherwise."""
    return MODE_LIVE if self.switch_on and (self.one_bar or not self.one_bar_gate) else MODE_SHADOW

  def set_mode(self, mode: int) -> None:
    """Live or shadow, from the user's switch (and since push14 the dash's 1-bar mode, _wanted_mode). A change applies
    on this frame: the gap and the lift start again from nothing (authority 0, their holds) - switched on, close follow
    ramps in as after any trip; switched off, it drops out at once, the plan's own on this frame. The FCW and hard-brake
    holds carry over."""
    mode = MODE_LIVE if mode == MODE_LIVE else MODE_SHADOW
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
      cloudlog.event("closefollow switch", error=True, mode=int(mode), on=bool(self.switch_on), bar=bool(self.one_bar),
                     changes=self.switch_changes)
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
    self.switch_on = read_switch(self.params)

  def _poll_mode(self, sm, engaged: bool) -> None:
    """The 1-bar mode every plan (one_bar_gate), the switch when it is due (_poll_switch) - and on the plan the 1-bar
    mode comes on, which selfdrived sets only with its own read of the switch on (the push14 review: the master switched
    on at a remembered 1 bar showed 1 bar on the dash for up to SWITCH_READ_S while this was still shadow) - then live
    or shadow."""
    if self.one_bar_gate:
      one_bar = read_one_bar(sm)
      if one_bar and not self.one_bar:
        self._read_left = min(self._read_left, 1)   # the switch on this plan (a read, never a write)
      self.one_bar = one_bar
    self._poll_switch(engaged)
    self.set_mode(self._wanted_mode())

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
    where the call is only recorded for run_deferred(). First the user's switch, when it is due, and the dash's 1-bar
    mode (_poll_mode): live or shadow is decided for the whole plan here."""
    if not self.failed:
      try:
        self._poll_mode(sm, not reset_state)
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
    lead = inp.lead_one
    v_lead = lead.v_lead if lead.present else None
    a_lead = lead.a_lead if lead.present else 0.
    self.v_schedule = schedule_speed(v_ego, v_lead, a_lead) if _finite(v_ego) else 0.
    t_raw = close_t_follow(v_ego, self.t_personality, v_lead, a_lead) if _finite(v_ego) else self.t_personality
    # round 2: it shortens no faster than T_FOLLOW_FALL - a lead that stops accelerating under the hand-back no longer
    # drops it to the crawl's follow time on one frame at full authority; it lengthens at once
    if _finite(self._t_prev) and t_raw < self._t_prev:
      t_raw = max(t_raw, self._t_prev - T_FOLLOW_FALL * self.dt)
    self.t_follow = self._t_prev = min(t_raw, self.t_personality)
    self.weight = speed_weight(self.t_follow, self.t_personality) if _finite(v_ego) else 0.
    # round 2: the real gap against the personality's own desired gap (what push12 aims for), filtered, per car
    key = (lead.radar, lead.track_id) if lead.present and not inp.held else None
    raw = (desired_gap(v_ego, lead.v_lead, self.t_personality) - lead.d_rel
           if key is not None and _finite(v_ego, lead.v_lead, lead.d_rel) else math.nan)
    if not _finite(raw):
      key = None
    same_car = key is not None and key == self._real_key
    self._real_key = key
    if key is None:
      self.real_deficit = math.nan
    elif not same_car or not _finite(self.real_deficit):
      self.real_deficit = raw
    else:
      self.real_deficit += self._real_k * (raw - self.real_deficit)
    self.real_w = real_weight(self.real_deficit)
    self.real_raw = raw if key is not None else math.nan
    if not same_car:
      self._raw_tail = 0
    self.speed_in = float(np.interp(v_ego, CF_SPEED_IN_BP, [0., 1.])) if _finite(v_ego) else 0.
    # round 2: the queue pulls away - leadOne's aLeadK over PULL_AWAY_ACCEL for PULL_AWAY_T, the same car
    pulling = key is not None and _finite(lead.a_lead) and lead.a_lead > PULL_AWAY_ACCEL
    self.pull_frames = (self.pull_frames + 1 if same_car else 1) if pulling else 0
    self.pull_away = self.pull_frames >= self._pull_n
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

    authority = self.gap.update(inp, self.requested, bool(fcw) or self.mpc_crash, self.weight, self.pull_away)
    # round 2: scaled by the real gap - nothing from REAL_GAP_MAX under the personality's own desired gap - and faded in
    # over CF_SPEED_IN_BP: nothing at a standstill, creeping into a stop or through a launch
    self.g = authority * self.real_w * self.speed_in if (close is not None and self.weight > 0.) else 0.
    if not self.g > 0. or close is None:
      self.g = 0.
    # round 3: what the radar shows while the gap is in and REAL_RAW_TAIL_S after (the same car) - the log's rraw. The
    # filtered deficit, which the weight reads, cannot pass REAL_GAP_MAX while the gap is in; the raw one can
    if self.g > 0.:
      self._raw_tail = self._raw_tail_frames
    self.raw_watch = (self.g > 0. or self._raw_tail > 0) and _finite(self.real_raw)
    if self.g <= 0. and self._raw_tail > 0:
      self._raw_tail -= 1
    if self.g <= 0. or close is None:
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
                                                   self.t_follow_used, pull_away=self.pull_away, real_w=self.real_w,
                                                   t_close=self.t_follow))
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
    # round 2: never at a standstill - a raise there (a lead creeping a few meters ahead) moves nothing closer
    if inp.standstill:
      return False
    return self.raised >= TIGHT_RAISE

  def _gap_inhibit(self):
    # (the hand-back is the guard's own 'speed' since the release13 review: it resets the authority; round 2: the gap
    # in by its guard but faded out under CF_SPEED_IN_BP is speed too, and by the real gap realGap)
    if self.gap.inhibit == Inhibit.none and self.weight > 0.:
      if not self.speed_in > 0.:
        return Inhibit.speed
      if not self.real_w > 0.:
        return Inhibit.realGap
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
    msg.oneBar = bool(self.one_bar)
    msg.live = bool(self.live)
    msg.tightening = bool(self.tightening)
    msg.raised = float(self.raised)
    msg.gapCloser = bool(self.gap_closer)
    msg.pitchFiltered = float(self.pitch_f)
    ttc = time_to_collision(lead)
    msg.ttc = float(ttc) if math.isfinite(ttc) else -1.
    msg.hardBrakeHold = float(self.hard_left * self.dt)
    msg.scheduleSpeed = float(self.v_schedule)
    msg.realDeficit = float(self.real_deficit) if _finite(self.real_deficit) else 0.
    msg.realWeight = float(self.real_w)
    msg.pullAway = bool(self.pull_away)

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
            'smax': 0., 'xmax': 0., 'rmax': -math.inf, 'rraw': -math.inf, 'why': Counter()}

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
        if _finite(self.real_deficit):
          w['rmax'] = max(w['rmax'], self.real_deficit)
      if self.raw_watch:
        w['rraw'] = max(w['rraw'], self.real_raw)
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
      cloudlog.event("closefollow", error=True, mode=int(self.mode), sw=int(self.switch_on), bar=int(self.one_bar), n=n,
                     v=round(w['v'] / n, 1),
                     gap=round(g / n, 2), lift=round(w['lift'] / n, 2), live=round(w['live'] / n, 2),
                     t=round(w['t'] / g, 2) if g else None, dg=round(w['dg'] / g, 1) if g else None,
                     lmax=round(w['lmax'], 2), up=round(w['up'], 2), brk=w['brk'], fcw=w['fcw'], why=why,
                     smax=round(w['smax'] * 1e3, 1), xmax=round(w['xmax'] * 1e3, 1),
                     rmax=round(w['rmax'], 2) if math.isfinite(w['rmax']) else None,
                     rraw=round(w['rraw'], 2) if math.isfinite(w['rraw']) else None)
    except Exception:
      pass
