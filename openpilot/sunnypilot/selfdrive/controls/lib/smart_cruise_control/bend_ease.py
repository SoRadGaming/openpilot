"""
FORK(BENDS): ease off for bends this car's EPS can't hold (push13).

WHY. The EPS on HONDA_ELESYS gives about 1.0-1.25 m/s^2 of lateral accel at full authority. sunnypilot's curve control
(SCC-V, vision_controller.py) only enters a turn at a predicted 1.3 m/s^2 and sizes its speed for 2.0, so on motorway
bends and ramps of about 300-750 m radius at 80-110 km/h it never limits: openpilot pins the steering and the board's
steer-required alarm sounds. Counted on the owner's routes aa-152 (7.8 h of openpilot steering at 60 km/h or more):
65 bends with a peak of 1.0-1.6 m/s^2 set off the board's alarm (its ceiling test, |torque| >= 90% for 0.3 s), 8.3 an
hour; 12 of them on push9 and later (13d-152, 1.56 h, 7.7 an hour). On 14d t=644 SCC-V went 'entering' and 'turning'
through such a bend and never became the planner's limiting source.

WHAT. Every planner frame (modelV2, 20 Hz) it reads the model's plan - the curvature at each point (|orientationRate.z|
/ velocity.x, points slower than CURV_MIN_V left out, a single point over the 97th percentile cut to it, as SCC-V guards
against spikes) and how far ahead it is (position.x). The bend ahead at today's speed is predLatAccel = vEgo^2 x the
largest of those curvatures.
  * An episode starts once predLatAccel has been >= START_LAT (1.0) for START_S (0.3 s), at MIN_SPEED (55 km/h) or
    faster. It ends once the bend, at the speed the episode began at (or today's, if higher), has stayed under
    END_LAT (0.9) for END_S (1 s): judging the end at the start speed keeps it from ending on the slower car and
    starting again as the speed comes back.
  * While it runs, the speed it sets is the brake curve to the tightest point: for each point, the speed that takes it
    at TARGET_LAT (1.1 m/s^2), raised by what PLAN_DECEL (0.4 m/s^2) sheds over its distance less LAG_S (1 s) of travel
    (the planner's jerk limit and the actuator delay). The model's bend flickers by +-0.2 m/s^2 from frame to frame
    (c8 t=2044-2075: 0.84-1.32 through one bend), and a target that followed it had the planner ease and re-accelerate in
    turn and shed nothing; so within an episode that speed falls at once but rises only at HOLD_RISE (0.3 m/s per s),
    and the start and end counters are leaky (a frame on the wrong side takes a frame back). Then never under
    MIN_SPEED, never more than MAX_CUT (15 km/h)
    under the speed the episode began at, and never more than DECEL (0.6 m/s^2) under today's speed: the planner turns
    a target into accel as clip(target - vEgo, -1.2, ...), so the ease asks for at most 0.6 m/s^2 - the comfort of the
    planner's normal cruise, under its own -1.2 - and get_cruise_accel's jerk limit (0.6-0.8 m/s^3 here) shapes it.
    And never more than ACCEL_CAP (0.5 m/s^2) over today's speed: inside a bend the car accelerates at 0.5 at most
    (SCC-V's 'leaving' rate), and the planner's cruise accel - a jerk-limited state that otherwise winds up to +2.0 in
    experimental mode whenever a target is far above the speed (c8 t=2071: 3.3 s from +2.0 to -0.6, the bend 1.5 s
    ahead) - stays near the car's, so a sharper part seen late still gets its 0.6 in about 1.4 s.
  * Nothing - and the episode keeps nothing - while openpilot is not steering (carControl.latActive) or its
    longitudinal is not engaged (carControl.enabled). While the driver presses the gas (carControl.cruiseControl.override
    or carState.gasPressed), and for GAS_HOLD_S (2 s) after, it sets nothing; then it may ease again, from the speed the
    driver left it at (SCC-V's own 'overriding' works the same way). During a lane change (laneChangeStarting/Finishing)
    the model's path is the lane change, not the road: no episode starts, and a running one keeps its last numbers.

MODES (BendEaseMode): 0 off; 1 shadow, the default for push13 - it works out and logs the speed it would set and changes
nothing; 2 live - once the planner has picked the lowest of its targets (cruise, SCC-V, SCC-M, SLA), a lower bend speed
takes over as sccVision (curve control). Only the speed changes: the accel the MPC starts from stays the one the planner
chose (for cruise, its own last accel), and the planner's min() over the cruise, MPC and e2e accels still has the last
word. So the ease only ever LOWERS THE CRUISE TARGET - but it does not bound the plan from above. The planner's own
speed (v_desired_filter) follows the eased plan, and behind a lead the MPC plans the lead from that lower speed, so for a
while the plan can be HIGHER than without the ease: open loop on 14c t=1977.1-1978.3 and 1990.5-1994.7 (110 frames, up
to +0.22 m/s^2) and on fd (86 frames, up to +0.22), a lead on every such frame. Handing the MPC the bend's accel
instead does the same (+0.24 on 14c). In the car, which really is slower, the gap to the lead is larger too.

BUILT ONLY for HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (bend_tune()). Every other car, and stock
ACC mode, get no tune: nothing is computed, nothing published, the plan is upstream's. It never touches the ACC
stand-down, AEB/CMBS (it only lowers a cruise target, gently) or the panda.

REPLAYED (push13, this class's step() on the 48 rlog routes aa-14d with a model, 7.53 h of steering at 60 km/h or more,
closed loop: the car follows its logged speed except where the target binds, through the planner's cruise accel with its
jerk limit and the logged set speed; cuts measured against the same sim with the ease off). The ease never asked for
more than 0.6 m/s^2. Of the 64 alarm bends of 1.0-1.6 m/s^2: 9 eased by 2 km/h or more at the peak (2 of them to 1.1 or
under: fd t=539 1.35 -> 0.99, 14d t=649 1.50 -> 1.05; the 9: peak 1.43 -> 1.24 median, 5.3 km/h off, the slowing
starting 8.4 s before the peak, 1.5-20 s), 3 by under 2 km/h. Not reached: 19 the model never put at 1.0 in time (5 of
them with openpilot longitudinal off), 12 seen too late for 0.6 m/s^2 to shed much or within the target, 12 peaked
under 1.1 (the EPS gave out first), 9 with the driver on the gas. The model's look-ahead reads about 8% under
openpilot's own peak on alarm bends and does not tell the bends the EPS holds from those it doesn't (1.0-1.2 peaks:
1.09 held, 1.06 alarm). False triggers: 4 of the 283 bends the EPS held slowed by 2.7-8.9 km/h, and 9 slowing episodes
on curves the count leaves out (an exit taken with the blinker, a start under 60 km/h) - each a real curve, openpilot
asking 1.1-2.5 m/s^2. 19 episodes an hour, 3.9 of them slowing the car (5.4 km/h for 15 s, median). Route 137's second
curve (52 km/h) gets nothing, as expected. Thresholds 1.0/1.0 ease 11 (3 to 1.1) and slow 5 held bends; 0.8/0.95 ease
15 (6) and slow 15. Shadow mode (the default) changes no plan: the planner replayed open loop on 14c and 14d (55,913
and 52,232 frames) is bit-identical to push12's on every frame, longitudinalPlan and longitudinalPlanSP. Live, open
loop: the plan differs on 3,047 and 4,158 frames; wherever the ease leads and the plan is under -0.6 m/s^2, push12's
was too (the e2e plan or the MPC). Behind a lead it is also higher than push12's at times (MODES above).

LOGGED: plannerd publishes customReserved11 (CustomReserved11) every planner frame on this car - mode, the episode,
predLatAccel, the bend's own speed, the target (0 when none), how far ahead it binds, the accel it implies, whether it is
(live) or would be (shadow) the planner's limiting target, and why there is no target. One cloudlog line per episode,
at error level so the qlog carries it: 'bendease ...'.
"""
import math
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from opendbc.car.honda.values import HONDA_ELESYS
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD

PARAM = "BendEaseMode"
LOG_TAG = "bendease"
LANE_CHANGES = (log.LaneChangeState.laneChangeStarting, log.LaneChangeState.laneChangeFinishing)
LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource


class Mode(IntEnum):
  OFF = 0
  SHADOW = 1
  LIVE = 2


DEFAULT_MODE = Mode.SHADOW


class Reason(IntEnum):
  """CustomReserved11.reason: why there is (or is not) a target this frame."""
  OFF = 0           # BendEaseMode 0
  IDLE = 1          # no bend over START_LAT ahead (or not for START_S yet)
  ACTIVE = 2        # a target is set
  GAS = 3           # the driver is pressing the gas, or released it under gas_hold_s ago
  NOT_STEERING = 4  # openpilot is not steering (latActive)
  LONG_OFF = 5      # openpilot longitudinal is not engaged
  SLOW = 6          # under MIN_SPEED when a bend came up
  LANE_CHANGE = 7   # a lane change: no new episode


@dataclass(frozen=True)
class BendTune:
  start_lat: float = 1.0                 # m/s^2 at today's speed: an episode starts
  end_lat: float = 0.9                   # m/s^2 at the start speed: ...and ends under this
  start_s: float = 0.3                   # s over start_lat before it starts
  end_s: float = 1.0                     # s under end_lat before it ends
  target_lat: float = 1.1                # m/s^2: the speed it sets takes the bend at this
  min_speed: float = 55 * CV.KPH_TO_MS   # m/s: never under, and no episode starts slower
  decel: float = 0.6                     # m/s^2: the most it asks for (target never under vEgo - decel)
  plan_decel: float = 0.4                # m/s^2: the brake curve to the bend (the margin to decel absorbs a sharpening)
  lag_s: float = 1.0                     # s of travel the brake curve does not count on (jerk limit, actuator delay)
  max_cut: float = 15 * CV.KPH_TO_MS     # m/s: the most it takes off the speed the episode began at
  gas_hold_s: float = 2.0                # s after the gas is released before it may set a target again
  hold_rise: float = 0.3                 # m/s per s: within an episode the brake curve's speed falls at once but rises
                                         # only this fast - the model's bend flickers +-0.2 m/s^2 frame to frame
  accel_cap: float = 0.5                 # m/s^2: within an episode the target is never more than this over vEgo, so the
                                         # planner's cruise accel stays near the car's and a sharper part bites in ~1.4 s
  curv_min_v: float = 5.0                # m/s: plan points slower than this give no curvature


# FORK(BENDS): this car's EPS (the gateway board at authority 160) - see the module docstring
ELESYS_TUNE = BendTune()


def bend_tune(CP, CP_SP) -> BendTune | None:
  """HONDA_ELESYS with openpilot longitudinal and stock ACC mode off; None for every other car."""
  try:
    if CP.brand != 'honda' or CP.carFingerprint not in HONDA_ELESYS or not CP.openpilotLongitudinalControl:
      return None
    if int(getattr(CP_SP, 'flags', 0)) & HondaFlagsSP.ELESYS_STOCK_ACC.value:
      return None
    return ELESYS_TUNE
  except Exception:
    return None


def plan_curvature(rate, vel, pos, curv_min_v: float) -> tuple[np.ndarray, np.ndarray]:
  """The model plan's curvature at each point (1/m, >= 0) and its distance ahead (m). Points slower than curv_min_v,
  or not finite, give 0. A single point over the 97th percentile is cut to it (SCC-V's spike guard)."""
  r, v, x = (np.asarray(a, dtype=float) for a in (rate, vel, pos))
  n = min(len(r), len(v), len(x))
  if n == 0:
    return np.zeros(0), np.zeros(0)
  r, v, x = r[:n], v[:n], x[:n]
  ok = np.isfinite(r) & np.isfinite(v) & np.isfinite(x) & (v >= curv_min_v)
  curv = np.zeros(n)
  curv[ok] = np.abs(r[ok]) / v[ok]
  if ok.any():
    curv = np.minimum(curv, float(np.percentile(curv[ok], 97)))
  return curv, np.where(np.isfinite(x), np.maximum(x, 0.0), 0.0)


class BendEase:
  """The planner's handle: update() once per planner frame (after SCC), apply() once the planner has chosen its
  target, publish() with longitudinalPlanSP."""

  def __init__(self, CP, CP_SP, dt: float = DT_MDL):
    self.tune = bend_tune(CP, CP_SP)
    self.dt = dt
    self.params = Params() if self.tune is not None else None
    self.frame = -1
    self.mode = Mode.OFF if self.tune is None else DEFAULT_MODE
    self._reset_episode()
    self.pred_lat = 0.        # m/s^2: the bend ahead at today's speed
    self.v_bend = 0.          # m/s: the speed that takes the tightest point at target_lat (0: no curvature)
    self.v_curve = math.inf   # m/s: the brake curve's speed now
    self.distance = 0.        # m: where the brake curve binds
    self.curv_max = 0.        # 1/m
    self.v_ego = 0.
    self.v_target = V_CRUISE_UNSET   # m/s: the target, live or shadow (V_CRUISE_UNSET: none)
    self.reason = Reason.OFF
    self.limiting = False
    self._above = 0.
    self._below = 0.
    self._input_error_logged = False

  def _reset_episode(self) -> None:
    self.active = False
    self.gas_t = 0.           # s left of the gas hold
    self.v_start = 0.
    self.v_hold = math.inf    # m/s: the held brake curve (inf: none yet)
    self._ep: dict | None = None

  @property
  def live(self) -> bool:
    return self.tune is not None and self.mode == Mode.LIVE

  def _read_mode(self) -> None:
    if self.params is None or self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) != 0:
      return
    try:
      raw = self.params.get(PARAM, return_default=True)
      m = int(raw) if raw is not None else int(DEFAULT_MODE)
      self.mode = Mode(m) if m in Mode._value2member_map_ else DEFAULT_MODE
    except Exception:
      self.mode = DEFAULT_MODE   # unreadable: shadow, which changes nothing

  def update(self, sm, long_enabled: bool, long_override: bool) -> None:
    """The planner's frame: reads carState, carControl and modelV2 from sm."""
    self.frame += 1
    self.limiting = False
    if self.tune is None:
      return
    self._read_mode()
    try:
      cs, cc, md = sm['carState'], sm['carControl'], sm['modelV2']
      v_ego = float(cs.vEgo)
      gas = bool(long_override) or bool(cs.gasPressed)
      lat_active = bool(cc.latActive)
      lane_change = md.meta.laneChangeState in LANE_CHANGES
      curv, dist = plan_curvature(md.orientationRate.z, md.velocity.x, md.position.x, self.tune.curv_min_v)
    except Exception:
      # unreadable inputs: as if openpilot were not steering - nothing set, the episode ends. Said once per process.
      if not self._input_error_logged:
        cloudlog.exception(f"{LOG_TAG} inputs unreadable")
        self._input_error_logged = True
      v_ego, gas, lat_active, lane_change, curv, dist = 0., False, False, False, np.zeros(0), np.zeros(0)
    self.step(v_ego, lat_active, bool(long_enabled), gas, lane_change, curv, dist)

  def step(self, v_ego: float, lat_active: bool, long_enabled: bool, gas: bool, lane_change: bool,
           curv: np.ndarray, dist: np.ndarray) -> float:
    """One frame on plain inputs (the replay and the unit tests drive this). Returns the target (V_CRUISE_UNSET: none)."""
    t = self.tune
    assert t is not None
    self.v_ego = v_ego = max(0., float(v_ego))
    self.v_target = V_CRUISE_UNSET
    if self.mode == Mode.OFF:
      self._end("off")
      self.reason = Reason.OFF
      self._above = self._below = 0.
      return self.v_target
    if not lane_change:
      self._predict(v_ego, curv, dist)

    if not lat_active or not long_enabled:
      self._end("steer" if not lat_active else "long")
      self.reason = Reason.NOT_STEERING if not lat_active else Reason.LONG_OFF
      self._above = 0.
      return self.v_target

    if not self.active:
      if lane_change:
        self._above = 0.
        self.reason = Reason.LANE_CHANGE
        return self.v_target
      if v_ego < t.min_speed:
        self._above = 0.
        self.reason = Reason.SLOW if self.pred_lat >= t.start_lat else Reason.IDLE
        return self.v_target
      # leaky: a frame under start_lat takes a frame back rather than starting over (the model's bend flickers)
      self._above = self._above + self.dt if self.pred_lat >= t.start_lat else max(0., self._above - self.dt)
      if self._above + 1e-9 < t.start_s:
        self.reason = Reason.IDLE
        return self.v_target
      self.active, self.v_start, self._below, self.v_hold = True, v_ego, 0., math.inf
      self._ep = {"v0": v_ego, "pred": self.pred_lat, "tgt": math.inf, "lim": 0, "n": 0, "gas": False}
    else:
      v_ref = max(v_ego, self.v_start)
      bend_at_ref = v_ref ** 2 * self.curv_max
      if lane_change:
        self._below = 0.
      else:
        self._below = self._below + self.dt if bend_at_ref < t.end_lat else max(0., self._below - self.dt)
      if self._below + 1e-9 >= t.end_s:
        self._end("bend")
        self.reason = Reason.IDLE
        self._above = 0.
        return self.v_target

    self.gas_t = t.gas_hold_s if gas else max(0., self.gas_t - self.dt)
    ep = self._ep
    if ep is not None:
      ep["n"] += 1
      ep["pred"] = max(ep["pred"], self.pred_lat)
      ep["gas"] = ep["gas"] or gas
    if self.gas_t > 1e-9:
      self.v_start = max(self.v_start, v_ego)   # the cut is counted from where the driver took it
      self.v_hold = math.inf                    # and the hold starts again from there
      self.reason = Reason.GAS
      return self.v_target

    self.reason = Reason.ACTIVE
    # the held brake curve: down at once, up at hold_rise (nothing curved ahead counts as a rise)
    self.v_hold = min(self.v_curve, self.v_hold + t.hold_rise * self.dt)
    if not math.isfinite(self.v_hold):
      return self.v_target   # nothing curved ahead and no hold yet: no target
    lo = max(t.min_speed, self.v_start - t.max_cut, v_ego - t.decel)
    self.v_target = min(V_CRUISE_UNSET, max(lo, min(self.v_hold, v_ego + t.accel_cap)))
    if ep is not None:
      ep["tgt"] = min(ep["tgt"], self.v_target)
    return self.v_target

  def _predict(self, v_ego: float, curv: np.ndarray, dist: np.ndarray) -> None:
    t = self.tune
    assert t is not None
    if len(curv) == 0 or not np.any(curv > 0):
      self.curv_max, self.pred_lat, self.v_bend, self.v_curve, self.distance = 0., 0., 0., math.inf, 0.
      return
    self.curv_max = float(np.max(curv))
    self.pred_lat = v_ego ** 2 * self.curv_max
    with np.errstate(divide='ignore'):
      v_pt = np.where(curv > 0, np.sqrt(t.target_lat / np.maximum(curv, 1e-9)), np.inf)
    reach = np.maximum(dist - v_ego * t.lag_s, 0.)
    v_allow = np.sqrt(v_pt ** 2 + 2. * t.plan_decel * reach)
    k = int(np.argmin(v_allow))
    self.v_curve = float(v_allow[k])
    self.distance = float(dist[k])
    self.v_bend = float(np.min(v_pt))

  def _end(self, why: str) -> None:
    ep = self._ep
    if self.active and ep is not None:
      tgt = ep["tgt"]
      cloudlog.error(f"{LOG_TAG} end={why} mode={int(self.mode)} s={ep['n'] * self.dt:.1f} v0={ep['v0'] * CV.MS_TO_KPH:.1f} " +
                     f"pred={ep['pred']:.2f} tgt={(tgt * CV.MS_TO_KPH if math.isfinite(tgt) else 0.):.1f} " +
                     f"lim={ep['lim'] * self.dt:.1f} gas={int(ep['gas'])}")
    self._reset_episode()

  def apply(self, source, v_target: float):
    """After the planner's min() over its targets: in live mode a lower bend speed takes over, as sccVision (curve
    control). Only the speed changes - the accel the planner starts its MPC from stays the chosen source's. That keeps
    the bend to lowering the cruise target; it does not keep the plan from rising behind a lead, where the MPC plans from
    the planner's eased speed (module docstring, MODES). Shadow and off return their inputs; `limiting` says whether it
    took over (live) or would have (shadow) - also when only the accel cap binds (target at or over vEgo), which holds
    the car's accel rather than slows it."""
    self.limiting = False
    if self.tune is None or self.v_target >= V_CRUISE_UNSET or not self.v_target < v_target:
      return source, v_target
    self.limiting = True
    if self._ep is not None:
      self._ep["lim"] += 1
    if self.live:
      return LongitudinalPlanSource.sccVision, self.v_target
    return source, v_target

  def publish(self, pm) -> None:
    if self.tune is None:
      return
    import openpilot.cereal.messaging as messaging
    msg = messaging.new_message('customReserved11')
    b = msg.customReserved11
    b.mode = int(self.mode)
    b.active = bool(self.active)
    has = self.v_target < V_CRUISE_UNSET
    b.vTarget = float(self.v_target) if has else 0.
    b.predLatAccel = float(self.pred_lat)
    b.vBend = float(self.v_bend) if math.isfinite(self.v_bend) else 0.
    b.distance = float(self.distance)
    b.aTarget = float(max(-1.2, self.v_target - self.v_ego)) if has else 0.
    b.limiting = bool(self.limiting)
    b.reason = int(self.reason)
    b.vStart = float(self.v_start)
    pm.send('customReserved11', msg)
