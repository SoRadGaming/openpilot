"""
FORK(HONDA_ACCORD_9G_AU): ramp the plan in after a gas override or an engagement (round 8, proposal 2).

WHY (round 8 braking analysis, drives 13d-143, 17-19 Oct 2026). While the driver holds the gas, longControlState
is off, the planner resets to aEgo every frame, and the plan it publishes may already be braking for a slowing
lead (141 t206.4: -0.66 with the foot still on the pedal). When the foot comes off, LongControl's PID restarts at
zero with the plan as its feedforward, so the command steps from what the car is doing straight to the plan. The
only smoothing left is the brake's rise limiter (1.0/s, about 2.6 m/s^3). 28 of 92 re-entries above 3 m/s went
straight into a plan below -0.5. Their felt onset jerk had a median of -1.56 m/s^3, against -1.09 for ordinary
lead braking. The worst were -3.6 (141 t207.8, Sun 15:52:07, lead 35 m ahead) and -4.0 (141 t1783.0, Sun 16:18:22,
no lead, a -1.3 model plan).

WHAT. For REENTRY_RAMP_S (1.5 s) after reset_state turns false - the gas override ends, or longitudinal engages -
the plan the planner publishes may fall by at most REENTRY_RAMP_JERK (1.5 m/s^3) x DT_MDL per frame. The ramp
starts from min(aEgo, 0) on the first frame back: what the car was doing, but never a request to keep
accelerating after the driver lifted off. It only ever RAISES the plan (out = max(plan, limit)), and a plan above
the limit passes untouched, so a re-entry into anything but a falling plan is bit-identical to upstream.

It is skipped, and stays off for the rest of that re-entry, the first frame any of these holds:
  * the planner's FCW (mpc.crash_cnt > 2);
  * the MPC's crash flag (crash_cnt > 0: a collision course with a confident lead in the next second);
  * the MPC's own plan below REENTRY_RAMP_MPC_FLOOR (-2.0), which is about this car's full brake (-1.8 to -2.0).
On that frame the plan is passed as it is, and from there the brake's own 1.0/s rise limit is the only smoothing.

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

COST: up to about 0.2-0.5 s later onset when the driver lifts off behind a hard-braking lead, bounded by the
exceptions above. The ramp reaches -2.25 m/s^2 from 0 inside its 1.5 s, which is past this car's full brake.

One line per re-entry where the ramp bound goes to cloudlog (`reentryramp ...`, a logMessage on the route). It
records the start, the deepest the plan fell below the ramp, and why the ramp ended.
"""
import math

from opendbc.car.honda.values import HONDA_ELESYS
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog

REENTRY_RAMP_S = 1.5             # s after reset_state turns false
REENTRY_RAMP_JERK = 1.5          # m/s^3: the most the plan may fall per second inside the window
REENTRY_RAMP_MPC_FLOOR = -2.0    # m/s^2: an MPC plan below this skips the ramp (about this car's full brake)
REENTRY_RAMP_MIN_SPEED = 3.0     # m/s (vEgo) on the first frame back: slower re-entries are upstream's
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


class ReentryRamp:
  """The planner's handle on the ramp: call update() once per planner frame, after the candidate min()."""

  def __init__(self, dt: float = DT_MDL):
    self.dt = dt
    self.prev_reset = True       # the planner starts reset (not engaged)
    self.t: float | None = None  # s since the ramp started; None: no ramp running
    self.out = 0.0               # the last plan this ramp let through
    self._log: dict | None = None

  @property
  def active(self) -> bool:
    return self.t is not None

  def update(self, reset_state: bool, v_ego, a_ego, a_plan, a_mpc, fcw: bool, crash_cnt) -> float:
    """One 20 Hz planner frame. Returns the plan to publish: a_plan, or the ramp's limit when the plan falls faster."""
    started = self.prev_reset and not reset_state
    self.prev_reset = bool(reset_state)
    if reset_state:
      self._end("override")
      return a_plan

    if started:
      v, a = _finite(v_ego), _finite(a_ego)
      if v is not None and a is not None and v >= REENTRY_RAMP_MIN_SPEED:
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
    if plan is None:
      self._end("plan")
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
