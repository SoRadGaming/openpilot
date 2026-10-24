"""
FORK(CLOSE-FOLLOW): close follow in queues (push13; the owner's round-5 request: "close up in motorway queues so cars
can't cut in, gap grows back with speed"). SHADOW in push13: CloseFollowMode 1 computes and logs everything below every
plan and changes nothing - not the plan, the accel sent, the HUD or a CAN frame. Mode 2 (live) is built and tested but
no UI can set it, and the on-switch is not decided (CloseFollowSwitch).

WHY. In a queue at 10-30 km/h openpilot sat 13-20 m behind (round 5, 95 routes: median 13.2 m at 10-20 km/h, 19.6 m at
20-30) against the owner's own 9.4 / 13.4 m, and cars cut into gaps over 9-12 m. Two things hold the gap open:
  * the MPC's follow time, 1.25 s on aggressive (long_mpc.get_T_FOLLOW), plus its 6 m stop distance;
  * in experimental mode the model's own e2e plan, which keeps a longer gap than any personality and which the planner
    takes whenever it is the lowest candidate (49% of the owner's low-speed following, round 5).
So close follow does two things, both only below CF_FADE_KPH (35 km/h), both with a real (not held) lead:

1. THE GAP (after theo886's Traffic Mode, sunnypilot `accord-hybrid-9g` e83f724d0, from FrogPilot's traffic follow):
   a second LongitudinalMpc beside the plan's own, with a follow time of 0.5 s at a stop rising to 1.0 s at 18 km/h
   (CF_T_FOLLOW_BP/_V) and - this car's change - fading back to the selected personality's by CF_FADE_KPH, and the
   jerk factor 0.5 (the aggressive personality's: quicker to react) faded the same way. STOP_DISTANCE is compiled into
   the solver and untouched: the stopped gap stays 6 m. Its candidate is blended into the plan's by gapAuthority:
   a = a_plan_mpc + authority * (a_close_mpc - a_plan_mpc), the trajectories alike. Authority drops to 0 AT ONCE on any
   trip, so on that frame the MPC candidate is the plan's own MPC, push12's, exactly. It waits out GAP_HOLD_S (1 s) of
   clean frames (the close MPC warms up), then rises at GAP_RISE (0.5/s). Trips: no real lead (or a held one, radard's
   clutter guard), lead modelProb under 0.8, a vision-only lead above CF_VISION_SPEED (2 m/s; the radar sees nothing
   at a stop and little under 2 m/s, where t*v is under 1 m), a braking lead (any real lead's aLeadK under -0.5 m/s^2,
   or leadOne's own speed falling faster than that over the last 0.5 s with aLeadK below 0 - aLeadK is a filter and
   trails the onset), a new car
   (leadOne 3 m off where it should be), FCW (the planner's, the close MPC's own crash flag, or the car's FCW/AEB,
   held FCW_HOLD_S = 5 s), gas or brake, forceDecel.

2. THE LIFT (adapted from zoompilot's Lead Follow Assist, E2ELeadGapController, MIT - notice below): in experimental
   mode the model's e2e accel is lifted toward the close MPC's candidate when the leads are 3-10 m (GAP_BP) beyond the
   close gap: e2e = a_model + boost, boost <= authority * gain * weight * min(CAP, headroom), headroom =
   min(a_mpc_close, a_cruise) - a_model. It can at most tie that candidate - NEVER PAST the radar plan - so the
   planner's min() never follows closer than the close MPC itself would. Fades in at 18-29 km/h (LIFT_SPEED_BP, the
   crawl stays the model's) and out by CF_FADE_KPH. CAP 0.5 / 0.4 / 0.3 m/s^2 for aggressive / standard / relaxed,
   rising at 0.25 m/s^3. HARD trips drop it to nothing on the same frame - a braking lead (as for the gap, or the
   model's lead forecast losing 1 m/s within 3 s), FCW, a new car (distance jump or radar track swap), gas or brake,
   no lead or a held one, a vision-only lead, forceDecel, the model's hard-brake flag or a stop in its plan. Soft trips
   (lead uncertain or under 5 m/s, the model braking under -0.5 or its plan slowing, over 1 m/s^2 of lateral accel,
   coasting, a lane change, under 5 m/s) let it fall at 4/s. Then a 3 s hold and a 0.5/s rise, as zoompilot.

THE ON-SWITCH is not decided (a distance-button hold, or automatic below ~30 km/h with a radar lead: the owner's
choice). CloseFollowSwitch is the one place it goes. Until then it says "requested" whenever openpilot longitudinal
is engaged: that is what the shadow measures (the most close follow could do), and it is off at every disengage.

GATED: HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (close_follow_applies(), the re-entry ramp's
gate), CloseFollowMode 1 or 2, read once when plannerd starts. Mode 0, every other car and stock ACC mode never build
it: upstream's planner bit for bit, no second MPC, no customReserved10.

LOGGED (the owner has no SSH): customReserved10 (CustomReserved10 in custom.capnp) every plan, 2 Hz in the qlog, and
at most one `closefollow` cloudlog event at error level per SUMMARY_S (10 s), only when there was a chance to act
(engaged, a real lead, under the fade speed): the share of those frames with the gap and the lift in, the mean follow
time and gap change, the most it lifted the plan, the top reasons it was out, and `brk`, the frames where it lifted the
plan while a lead braked - by construction 0; a non-zero `brk` in a route is a bug.

COST: a second MPC solve (median 0.6 ms on the device) only while eligible: requested, engaged, a real lead, under
the fade speed + CF_MPC_MARGIN.

REPLAYED (push13, shadow; the real planner open loop on 20 of the owner's routes: 0x130-0x152, after the radar speed
fix, and 14, 1f, c0, c9, d5, 12a with vRel doubled; 290 engaged minutes, 61 of them engaged behind a real lead under
35 km/h, 35 of those standing still). The replayed plans match the logged ones (median 0.000, p95 under 0.02 m/s^2).
The closer gap would have been in for 48 / 43 / 35 / 28 % of the time at 2-7 / 7-18 / 18-29 / 29-35 km/h, out mostly
for a braking lead and the 1 s hold after it; the lift for 5-9 % at 18-35 km/h (a braking lead, its 3 s hold, a
vision lead, a stop in the model's plan). Close follow would have raised the plan by more than 0.01 m/s^2 for 3.9 of
the 61 minutes (12-22 % of the moving time), median +0.05..0.09, p95 +0.13..0.38. The gap it aims for in steady
following: 9.4 m at 7-18 km/h (aggressive 10.5, driven 11.4) and 12.9 m at 18-29 (13.5, driven 17.2 - the model's own
gap in experimental mode, which the lift seldom overrules). By its own braking test it never raised the plan while a
lead braked; a look-ahead test (the lead's speed over +-0.5 s, which no controller can see) finds 43 onset frames,
max +0.024 m/s^2 on the routes after the radar fix (+0.46 on route 14, where aLeadK is rebuilt from the half-scale
radar). Never during any of the 76 engaged FCW frames.

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
lead is no lead; it fades out by CF_FADE_KPH; its constants are inlined (zoompilot's set-speed nudge is not here).
"""
import math
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

# CloseFollowMode (params_keys.h): 0 off, 1 shadow (log only), 2 live. Read once when plannerd starts.
MODE_PARAM = "CloseFollowMode"
MODE_OFF, MODE_SHADOW, MODE_LIVE = 0, 1, 2
SERVICE = "customReserved10"

# --- the gap (theo886 Traffic Mode's schedule, faded back to the personality on this car) ---
CF_T_FOLLOW_BP = [0., 5.]       # m/s: a stop, 18 km/h
CF_T_FOLLOW_V = [0.5, 1.0]      # s
CF_FADE_KPH = 35.               # km/h: THE fade speed - the personality's own gap (and jerk) by here, and no lift
CF_FADE_SPEED = CF_FADE_KPH * CV.KPH_TO_MS
CF_JERK_FACTOR = 0.5            # the aggressive personality's: quicker reactions
CF_VISION_SPEED = 2.0           # m/s: above it the gap needs a radar lead
CF_MPC_MARGIN = 1.5             # m/s: the close MPC keeps running this far past the fade, so slowing back is seamless
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

# --- the lift (zoompilot Lead Follow Assist) ---
GAP_BP = [3., 10.]              # m beyond the close gap: the lift opens over this
LIFT_SPEED_BP = [5., 8.]        # m/s (18 -> 29 km/h): the lift fades in; out from LIFT_SPEED_BP[1] to CF_FADE_SPEED
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
LEAD_MIN_SPEED = 5.0            # m/s
BOOST_RISE = 0.25               # m/s^3
LIFT_HOLD_S = 3.0               # s after the last trip
AUTHORITY_RISE = 0.5            # 1/s
AUTHORITY_FALL = 4.0            # 1/s, soft trips

SUMMARY_S = 10.0                # s: at most one closefollow cloudlog event per this
_EPS = 1e-9

T_IDXS = np.asarray(ModelConstants.T_IDXS)
LEAD_T_IDXS = np.asarray(ModelConstants.LEAD_T_IDXS)
LAT_MASK = T_IDXS <= LAT_HORIZON
SLOWDOWN_MASK = T_IDXS <= PLAN_SLOWDOWN_T
LEAD_SLOWDOWN_MASK = LEAD_T_IDXS <= LEAD_SLOWDOWN_T

HARD_LIFT_TRIPS = frozenset({Inhibit.noLead, Inhibit.visionLead, Inhibit.leadBraking, Inhibit.leadChanged, Inhibit.fcw,
                             Inhibit.driver, Inhibit.forceDecel, Inhibit.hardBrake, Inhibit.stop})

assert CF_FADE_SPEED > LIFT_SPEED_BP[1] > LIFT_SPEED_BP[0] and CF_FADE_SPEED > CF_T_FOLLOW_BP[-1]


def close_follow_applies(CP, CP_SP) -> bool:
  """HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (the re-entry ramp's gate)."""
  try:
    if CP.brand != 'honda' or CP.carFingerprint not in HONDA_ELESYS or not CP.openpilotLongitudinalControl:
      return False
    return not (int(getattr(CP_SP, 'flags', 0)) & HondaFlagsSP.ELESYS_STOCK_ACC.value)
  except Exception:
    return False


def read_mode(params) -> int:
  """CloseFollowMode as 0/1/2. Anything unreadable or out of range is OFF: an unknown mode must not run."""
  try:
    mode = int(params.get(MODE_PARAM, return_default=True))
  except (TypeError, ValueError):
    return MODE_OFF
  return mode if mode in (MODE_OFF, MODE_SHADOW, MODE_LIVE) else MODE_OFF


def _personality(p) -> int:
  return int(getattr(p, 'raw', p))


def _finite(*xs) -> bool:
  try:
    return all(math.isfinite(float(x)) for x in xs)
  except (TypeError, ValueError):
    return False


def close_t_follow(v_ego: float, t_personality: float) -> float:
  """The close follow time: 0.5 s at a stop, 1.0 s at 18 km/h, the personality's from CF_FADE_KPH. Never above the
  personality's."""
  t = float(np.interp(v_ego, CF_T_FOLLOW_BP + [CF_FADE_SPEED], CF_T_FOLLOW_V + [t_personality]))
  return min(t, t_personality)


def close_jerk_factor(v_ego: float, j_personality: float) -> float:
  """CF_JERK_FACTOR up to 18 km/h, the personality's from CF_FADE_KPH."""
  return float(np.interp(v_ego, [CF_T_FOLLOW_BP[-1], CF_FADE_SPEED], [CF_JERK_FACTOR, j_personality]))


def speed_weight(v_ego: float) -> float:
  """How much of the closer gap is left at this speed: 1 up to 18 km/h, 0 from CF_FADE_KPH."""
  return float(np.interp(v_ego, [CF_T_FOLLOW_BP[-1], CF_FADE_SPEED], [1., 0.]))


def lift_speed_weight(v_ego: float) -> float:
  """The lift's speed window: in over 18 -> 29 km/h, out by CF_FADE_KPH."""
  return float(np.interp(v_ego, LIFT_SPEED_BP, [0., 1.]) * np.interp(v_ego, [LIFT_SPEED_BP[1], CF_FADE_SPEED], [1., 0.]))


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


def inputs_from_sm(sm, reset_state: bool, is_e2e: bool, allow_throttle: bool) -> Inputs:
  cs, rs = sm['carState'], sm['radarState']
  return Inputs(engaged=not reset_state, is_e2e=bool(is_e2e), v_ego=float(cs.vEgo),
                personality=_personality(sm['selfdriveState'].personality),
                lead_one=lead_view(rs.leadOne), lead_two=lead_view(rs.leadTwo),
                held=is_held(rs.leadOne) or is_held(rs.leadTwo), gas=bool(cs.gasPressed), brake=bool(cs.brakePressed),
                standstill=bool(cs.standstill), car_fcw=bool(cs.stockFcw) or bool(cs.stockAeb),
                force_decel=bool(sm['controlsState'].forceDecel),
                steer_lat_accel=float(sm['controlsState'].curvature) * float(cs.vEgo) ** 2,
                allow_throttle=bool(allow_throttle), model=model_view(sm['modelV2']))


def _leads(inp: Inputs) -> list[Lead]:
  return [ld for ld in (inp.lead_one, inp.lead_two) if ld.present]


def lead_braking(inp: Inputs) -> bool:
  """Any real lead braking harder than GAP_LEAD_BRAKE (a non-finite aLeadK counts as braking), or leadOne's own speed
  falling as fast over the last LEAD_SLOWING_T."""
  return inp.lead_slowing or any(not (_finite(ld.a_lead) and ld.a_lead >= GAP_LEAD_BRAKE) for ld in _leads(inp))


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
    t = np.arange(n) * self.dt
    t -= t.mean()
    v = np.asarray(self.v)
    self.slope = float(np.dot(t, v - v.mean()) / np.dot(t, t))
    return self.slope < LEAD_SLOWING_ACCEL and not (_finite(lead.a_lead) and lead.a_lead >= 0.)


class CloseFollowSwitch:
  """THE ON-SWITCH - not decided; the owner chooses later (a distance-button hold, or automatic below ~30 km/h with a
  radar lead). Whatever it becomes it answers one question per plan - does the driver want close follow now? - and it
  is off at every disengage. A button hold would be read where the presses are (selfdrived, 100 Hz) and reach this
  through a field the planner subscribes to; automatic needs nothing more than the guards close follow already has.
  Until then: requested whenever openpilot longitudinal is engaged (SWITCH_ASSUMED), what the shadow measures."""
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
  """The closer gap's authority (0..1): 0 at once on any trip, GAP_HOLD_S of clean frames, then up at GAP_RISE."""

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

  def update(self, inp: Inputs, requested: bool, fcw: bool) -> float:
    """fcw: the planner's FCW or the close MPC's crash flag this frame (the car's own is read from inp)."""
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
    if lead_braking(inp):
      return Inhibit.leadBraking
    if math.isfinite(expected) and not (abs(lead.d_rel - expected) < LEAD_JUMP):
      return Inhibit.leadChanged
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
    self.weight = float(np.interp(self.gap_excess, GAP_BP, [0., 1.])) * lift_speed_weight(v_ego)
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
  cruise candidate is known, finish() with the min() the plan took, publish() with the plan. In shadow the planner uses
  none of the returns; live it takes update_mpc()'s trajectories and update_lift()'s e2e candidate.
  An exception anywhere in close follow never reaches plannerd: it is logged once (cloudlog.exception, error level),
  close follow is off for the rest of the drive, and every call hands back the plan's own values (push12's)."""

  def __init__(self, mode: int, dt: float = DT_MDL, mpc_factory=None):
    self.mode = mode
    self.failed = False
    self.dt = dt
    self.switch = CloseFollowSwitch()
    self.gap = GapGuard(dt)
    self.lift = LeadFollowLift(dt)
    self.lead_window = LeadSpeedWindow(dt)
    self._mpc_factory = mpc_factory
    self.mpc = None
    self.mpc_warm = False
    self.inputs: Inputs | None = None
    self.requested = False
    self.eligible = False
    self.mpc_solved = False
    self.mpc_crash = False
    self.g = 0.
    self.weight = 0.
    self.t_follow = self.t_personality = get_T_FOLLOW(Personality.standard)
    self.jerk = 1.
    self.a_mpc = self.a_mpc_close = 0.
    self.a_model = self.a_model_lifted = 0.
    self.a_target = self.a_target_close = 0.
    self.source_close = PlanSource.cruise
    self.frame = 0
    self.summary_frames = int(round(SUMMARY_S / dt))
    self._last_summary = 0
    self._win = self._new_window()

  @property
  def live(self) -> bool:
    return self.mode == MODE_LIVE

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
                 allow_throttle: bool = True, inputs: Inputs | None = None):
    """After the plan's own MPC solve. v_state, a_state: what the plan's MPC was set to (set_cur_state); fcw: the
    planner's FCW this frame; trajectories: the plan's (v, a, j) at control_t_idxs. Returns the trajectories with close
    follow blended in by the gap's authority - the very same arrays while it is out."""
    if self.failed:
      return trajectories
    try:
      return self._update_mpc(sm, reset_state, v_state, a_state, prev_accel_constraint, fcw, plan_mpc, trajectories,
                              control_t_idxs, action_t, is_e2e, allow_throttle, inputs)
    except Exception:
      self._fail('update_mpc')
      return trajectories

  def _update_mpc(self, sm, reset_state, v_state, a_state, prev_accel_constraint, fcw, plan_mpc, trajectories,
                  control_t_idxs, action_t, is_e2e, allow_throttle, inputs):
    self.frame += 1
    inp = inputs if inputs is not None else inputs_from_sm(sm, reset_state, is_e2e, allow_throttle)
    inp = inp._replace(lead_slowing=self.lead_window.update(inp.lead_one))
    self.inputs = inp
    self.requested = self.switch.update(sm, inp.engaged)
    v_ego = inp.v_ego
    self.t_personality = get_T_FOLLOW(inp.personality)
    j_personality = get_jerk_factor(inp.personality)
    self.weight = speed_weight(v_ego) if _finite(v_ego) else 0.
    self.t_follow = close_t_follow(v_ego, self.t_personality) if _finite(v_ego) else self.t_personality
    self.jerk = close_jerk_factor(v_ego, j_personality) if _finite(v_ego) else j_personality
    v_traj, a_traj, j_traj = trajectories
    self.a_mpc = float(get_accel_from_plan(v_traj, a_traj, control_t_idxs, action_t=action_t))

    self.eligible = (self.requested and inp.engaged and _finite(v_ego) and v_ego < CF_FADE_SPEED + CF_MPC_MARGIN and
                     inp.lead_one.present and not inp.held)
    self.mpc_solved = False
    self.mpc_crash = False
    close = None
    if self.eligible:
      mpc = self._close_mpc()
      if not self.mpc_warm:
        self._seed(mpc, plan_mpc)
      personality = sm['selfdriveState'].personality
      mpc.set_weights(prev_accel_constraint, personality=personality, jerk_factor=self.jerk)
      mpc.set_cur_state(v_state, a_state)
      mpc.update(sm['radarState'], personality=personality, t_follow=self.t_follow)
      self.mpc_warm = True
      self.mpc_solved = True
      self.mpc_crash = bool(mpc.crash_cnt > 2 and not inp.standstill)
      close = (np.interp(control_t_idxs, T_IDXS_MPC, mpc.v_solution), np.interp(control_t_idxs, T_IDXS_MPC, mpc.a_solution),
               np.interp(control_t_idxs, T_IDXS_MPC[:-1], mpc.j_solution))
    else:
      self.mpc_warm = False

    authority = self.gap.update(inp, self.requested, bool(fcw) or self.mpc_crash)
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
    """Once the cruise candidate is known. Returns the e2e candidate with the lift (a_model while it is idle)."""
    inp = self.inputs
    if inp is None or self.failed:
      return a_model
    try:
      if inp.is_e2e != bool(is_e2e) or inp.allow_throttle != bool(allow_throttle) or inp.model.a != a_model:
        inp = self.inputs = inp._replace(is_e2e=bool(is_e2e), allow_throttle=bool(allow_throttle),
                                         model=inp.model._replace(a=float(a_model)))
      self.a_model = float(a_model)
      self.a_model_lifted = float(self.lift.update(inp, self.requested, self.a_mpc_close, float(a_cruise),
                                                   bool(fcw) or self.mpc_crash, self.t_follow_used))
      return self.a_model_lifted
    except Exception:
      self._fail('update_lift')
      return a_model

  def finish(self, a_target: float, mpc_source, a_cruise: float, is_e2e: bool) -> None:
    """With the min() the plan took (before the re-entry ramp): what close follow's candidates would have given."""
    if self.failed:
      return
    try:
      self.a_target = float(a_target)
      candidates = [(self.a_mpc_close, mpc_source), (float(a_cruise), PlanSource.cruise)]
      if is_e2e:
        candidates.append((self.a_model_lifted, PlanSource.e2e))
      self.a_target_close, self.source_close = min(candidates, key=lambda c: c[0])
      self._account()
    except Exception:
      self._fail('finish')

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

  def _gap_inhibit(self):
    if self.gap.inhibit in (Inhibit.none, Inhibit.hold) and self.weight <= 0. and self.gap.authority > 0.:
      return Inhibit.speed
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
    return {'n': 0, 'gap': 0, 'lift': 0, 't': 0., 'dg': 0., 'v': 0., 'lmax': 0., 'up': 0., 'brk': 0, 'fcw': 0,
            'why': Counter()}

  def _account(self) -> None:
    inp = self.inputs
    w = self._win
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
      w['up'] = max(w['up'], self.a_target_close - self.a_target)
      if lead_braking(inp) and (self.lifting > _EPS or self.a_target_close > self.a_target + 1e-6):
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
      cloudlog.event("closefollow", error=True, mode=int(self.mode), n=n, v=round(w['v'] / n, 1),
                     gap=round(g / n, 2), lift=round(w['lift'] / n, 2),
                     t=round(w['t'] / g, 2) if g else None, dg=round(w['dg'] / g, 1) if g else None,
                     lmax=round(w['lmax'], 2), up=round(w['up'], 2), brk=w['brk'], fcw=w['fcw'], why=why)
    except Exception:
      pass
