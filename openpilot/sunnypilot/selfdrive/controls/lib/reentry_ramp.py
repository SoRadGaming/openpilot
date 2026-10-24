"""
FORK(HONDA_ACCORD_9G_AU): ramp the plan in after the driver lifts off the gas (round 8, proposal 2).

WHY (round 8 braking analysis, drives 13d-143, 17-19 Oct 2026). While the driver holds the gas, longControlState
is off, the planner resets to aEgo every frame, and the plan it publishes may already be braking for a slowing
lead (141 t206.4: -0.66 with the foot still on the pedal). When the foot comes off, LongControl's PID restarts at
zero with the plan as its feedforward, so the command steps from what the car is doing straight to the plan. The
only smoothing left is the brake's rise limiter (1.0/s, about 2.6 m/s^3). 28 of 92 re-entries above 3 m/s went
straight into a plan below -0.5. Their felt onset jerk had a median of -1.56 m/s^3, against -1.09 for ordinary
lead braking. The worst were -3.6 (141 t207.8, Sun 15:52:07, lead 35 m ahead) and -4.0 (141 t1783.0, Sun 16:18:22,
no lead, a -1.3 model plan).

WHAT. For REENTRY_RAMP_S (1.5 s) after a gas override ends - reset_state turns false with the gas pressed within the
last REENTRY_RAMP_GAS_S (0.5 s) - the plan the planner publishes may fall by at most REENTRY_RAMP_JERK (1.5 m/s^3)
x DT_MDL per frame. The ramp starts from min(aEgo, 0) on the first frame back: what the car was doing, but never a
request to keep accelerating after the driver lifted off. It only ever RAISES the plan (out = max(plan, limit)), and
a plan above the limit passes untouched, so a re-entry into anything but a falling plan is bit-identical to upstream.
An engagement is NOT a lift-off: reset_state also turns false when longitudinal engages, and with no gas in the last
0.5 s (re-engaging after the driver braked, a cold start) the planner is upstream's. The first build armed there too
(round 8 review: 13d t=370.9, re-engaging behind a lead braking at -3.5, held -1.04 at -0.48).

It is skipped, and stays off for the rest of that re-entry, the first frame any of these holds:
  * the planner's FCW (mpc.crash_cnt > 2);
  * the MPC's crash flag (crash_cnt > 0: a collision course with a confident lead in the next second);
  * the MPC's plan below REENTRY_RAMP_MPC_FLOOR (-2.0), well inside this car's full brake (corrected 2026-10-24, below):
    its action-time value OR any point of its trajectory over the next 2.5 s (the planner passes the lower of the two). Its
    action-time value is itself jerk-limited and takes 0.4-0.85 s to pass -2.0 behind a hard-braking lead;
  * the plan itself (the min of every candidate, e2e included) below -2.0 (round 8 review: 0dd t=296.8, an e2e plan of
    -2.05 held back by 1.73 m/s^2 while the MPC sat above -2.0);
  * a lead braking harder than REENTRY_RAMP_LEAD_DECEL (-2.0): the lowest radarState aLeadK of leadOne and leadTwo
    (lead_decel(); a present lead without a finite aLeadK counts as braking hard).
On that frame the plan is passed as it is, and from there the brake's own 1.0/s rise limit is the only smoothing.
Why the last three (round 8 review): it was written believing the car is at full brake around -1.9, so braking the ramp
holds back is never recovered. Measured (brake320 study, 2026-10-24, steady decel on the level), full brake is -2.45
m/s^2 at 255 counts and about -2.95 at 320 (brake320, opendbc brake_ceiling.py); 143's -1.65 to -1.92 was full brake
on a ~5% downhill. The floor stays -2.0 by decision: it is where the ramp stops holding braking back, and the ramp only
ever takes braking away, so giving way 0.45-0.95 m/s^2 before the brake's reach is the safe side. Moved toward the new
ceiling it would hold back up to 0.95 m/s^2 more behind a hard-braking lead - the case below. In the closed-loop sim
below, the ramp with only the first three turned 11 of 192 near-misses behind a lead braking at -3 to -6 m/s^2 into
collisions.

It arms only at REENTRY_RAMP_MIN_SPEED (3 m/s) or faster on the first frame back. All 92 measured re-entries were
above 3 m/s. Below it, re-entry is the stop-and-go creep behind a close lead, where nothing was measured and the
stopping state takes over below 0.8 m/s. There the planner is upstream's.

BUILT ONLY for HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (reentry_ramp_applies()). Every
other car, and stock ACC mode (HondaElesysStockAcc), keeps upstream's longitudinalPlan bit for bit.

REPLAYED (round 8, drives 13d-143; the planner open loop on the logged inputs, then the real CarController and the
batch-3 plant closed loop; only the differences mean much). 92 re-entries at 3 m/s or more, 26 where the ramp binds.
The steepest fall of the published plan from the car's accel at the lift-off: 141 t207.9 -28 -> -1.5 m/s^3 (one frame
at 20 Hz; -3.0 -> -1.5 over 0.5 s), 141 t206.4 -2.4 -> -1.5 over 0.5 s. Felt jerk in the closed loop: 141 t1783.0
-3.10 -> -2.44, 141 t207.8 -3.06 -> -2.42, 141 t206.3 -1.94 -> -1.04 m/s^3 (logged -3.86, -3.62, -1.87). What is left is
the PID's integrator winding against the brake's lag on top of the ramp. Onset cost: the median braking re-entry is no
later, the worst 0.18 s, at most 0.34 m further by the end of the window. The plan is untouched where the ramp does
not bind (66 re-entries), and outside a window plus 2 s it differs by at most 0.012 m/s^2 (the MPC's warm start).
The review fixes (gas-only arming, the plan and trajectory floors, the lead gate) leave every one of those lift-offs
as it was: replayed open loop on 13d-143 (the logged radarState), the ramp binds on the same 20 lift-offs frame for
frame, and no longer on the 5 engagements the first build ramped (13d t=115.2, 370.9; 141 t=226.9, 668.5; 142
t=1242.5). Over 55 routes (13d-143 and the review's 51) it arms on 1485 lift-offs, binds on 496 (1649 / 542 before),
and binds while leadOne's aLeadK is under -1.5 on 7 (10 before); the trajectory floor alone took out 129 t=744.5 and
t=751.7, lift-offs at 28 m/s 26 m behind a slower car, where the first build held back up to 1.36 m/s^2.
CLOSED LOOP (the round 8 review's sim, rerun: the real planner, LongControl, the HONDA_ELESYS CarController with the
tuner on and the batch-3 plant; the driver holds speed on the gas until the lift-off): 192 scenarios behind a lead
braking at -2 to -6 m/s^2 from 0.5 s before to 0.7 s after the lift-off, at 12-27 m/s and 1.0-2.0 s gaps: 0 new
collisions (11 with the first build), the closest approach at worst 0.44 m shorter (6.10), where both collide at most
0.17 m/s harder (4.18). 20 stopped-lead scenarios: unchanged but for at most 0.29 m. 60 lift-offs closing on a slower
lead at constant speed (3-12 m/s slower, needing 1.0-2.2 m/s^2): no collision with or without the ramp; the following
gap it settles to is up to 1.6 m shorter (of 10-28 m), the cost of braking later.

COST: when the driver lifts off behind a car that is slowing but not braking harder than -2.0, and the plan stays
above -2.0, braking starts up to about 0.2-0.5 s later and the closest approach can be up to about 1.6 m shorter.
The ramp reaches -2.25 m/s^2 from 0 inside its 1.5 s: about the old ceiling's measured -2.45, short of brake320's -2.95.

One line per re-entry where the ramp bound goes to cloudlog (`reentryramp ...`, a logMessage on the route). It
records the start, the deepest the plan fell below the ramp, and why the ramp ended (end=time, override, fcw, crash,
mpc, plan or lead).
"""
import math

from opendbc.car.honda.values import HONDA_ELESYS
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog

REENTRY_RAMP_S = 1.5             # s after reset_state turns false
REENTRY_RAMP_JERK = 1.5          # m/s^3: the most the plan may fall per second inside the window
REENTRY_RAMP_MPC_FLOOR = -2.0    # m/s^2: the MPC's plan, or the plan itself, below this skips the ramp (urgent braking;
                                 # full brake measured -2.45 at 255 counts, -2.95 at 320 - kept -2.0, see the docstring)
REENTRY_RAMP_LEAD_DECEL = -2.0   # m/s^2: a lead braking harder than this (radarState aLeadK) skips the ramp
REENTRY_RAMP_MIN_SPEED = 3.0     # m/s (vEgo) on the first frame back: slower re-entries are upstream's
REENTRY_RAMP_GAS_S = 0.5         # s: arms only if the gas was pressed this recently (a lift-off, not an engagement)
LOG_TAG = "reentryramp"

_EPS = 1e-6


def reentry_ramp_applies(CP, CP_SP) -> bool:
  """HONDA_ELESYS with openpilot longitudinal and stock ACC mode off."""
  try:
    if CP.brand != 'honda' or CP.carFingerprint not in HONDA_ELESYS or not CP.openpilotLongitudinalControl:
      return False
    return not (int(getattr(CP_SP, 'flags', 0)) & HondaFlagsSP.ELESYS_STOCK_ACC.value)
  except Exception:
    return False


def _finite(x) -> float | None:
  try:
    v = float(x)
  except (TypeError, ValueError):
    return None
  return v if math.isfinite(v) else None


def lead_decel(leads) -> float | None:
  """The hardest braking (lowest aLeadK) among the present leads (radarState leadOne/leadTwo), or None when there is no
  lead. A present lead without a finite aLeadK counts as braking hard (-inf), so it skips the ramp."""
  worst = None
  try:
    for lead in leads:
      if not bool(lead.present):
        continue
      a = _finite(lead.aLeadK)
      a = -math.inf if a is None else a
      worst = a if worst is None else min(worst, a)
  except Exception:
    return -math.inf
  return worst


class ReentryRamp:
  """The planner's handle on the ramp: call update() once per planner frame, after the candidate min()."""

  def __init__(self, dt: float = DT_MDL):
    self.dt = dt
    self.prev_reset = True       # the planner starts reset (not engaged)
    self.since_gas = math.inf    # s since the gas was last seen pressed while reset (inf: not this reset)
    self.t: float | None = None  # s since the ramp started; None: no ramp running
    self.out = 0.0               # the last plan this ramp let through
    self._log: dict | None = None

  @property
  def active(self) -> bool:
    return self.t is not None

  def update(self, reset_state: bool, v_ego, a_ego, a_plan, a_mpc, fcw: bool, crash_cnt, gas_pressed,
             a_lead) -> float:
    """One 20 Hz planner frame. Returns the plan to publish: a_plan, or the ramp's limit when the plan falls faster.
    gas_pressed: carState.gasPressed. a_lead: lead_decel() of radarState's leads (None: no lead)."""
    started = self.prev_reset and not reset_state
    self.prev_reset = bool(reset_state)
    if reset_state:
      self.since_gas = 0.0 if bool(gas_pressed) else self.since_gas + self.dt
      self._end("override")
      return a_plan

    if started:
      # a gas override that just ended; an engagement (no gas) is upstream's
      lifted = bool(gas_pressed) or self.since_gas <= REENTRY_RAMP_GAS_S + _EPS
      self.since_gas = math.inf
      v, a = _finite(v_ego), _finite(a_ego)
      if lifted and v is not None and a is not None and v >= REENTRY_RAMP_MIN_SPEED:
        self.t = 0.0
        self.out = min(a, 0.0)
        self._log = {"v": v, "a0": a, "plan0": _finite(a_plan), "short": 0.0, "bound": 0}
    if self.t is None:
      return a_plan

    plan = _finite(a_plan)
    mpc = _finite(a_mpc)
    if bool(fcw):
      self._end("fcw")
      return a_plan
    if not (_finite(crash_cnt) == 0.0):
      self._end("crash")
      return a_plan
    if mpc is None or mpc < REENTRY_RAMP_MPC_FLOOR:
      self._end("mpc")
      return a_plan
    if plan is None or plan < REENTRY_RAMP_MPC_FLOOR:   # any candidate under the floor (e2e too)
      self._end("plan")
      return a_plan
    if a_lead is not None and not (_finite(a_lead) is not None and a_lead >= REENTRY_RAMP_LEAD_DECEL):
      self._end("lead")
      return a_plan

    limit = self.out - REENTRY_RAMP_JERK * self.dt
    out = max(plan, limit)
    if out > plan and self._log is not None:
      self._log["bound"] += 1
      self._log["short"] = max(self._log["short"], out - plan)
    self.out = out
    self.t += self.dt
    if self.t >= REENTRY_RAMP_S - _EPS:
      self._end("time")
    return out

  def _end(self, reason: str) -> None:
    if self.t is None:
      return
    log, t = self._log, self.t
    self.t = None
    self._log = None
    try:
      if log is not None and log["bound"] > 0:
        plan0 = log["plan0"]
        cloudlog.info(f"{LOG_TAG} end={reason} t={t:.2f} v={log['v']:.1f} a0={log['a0']:.2f} " +
                      f"plan0={plan0 if plan0 is None else round(plan0, 2)} short={log['short']:.2f} frames={log['bound']}")
    except Exception:
      pass
