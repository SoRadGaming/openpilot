"""
FORK(LEAD-BRAKE): brake sooner when the car ahead brakes hard. HONDA_ELESYS with openpilot longitudinal and stock ACC
mode off only (lead_brake_applies(), the re-entry ramp's gate); every other car, and stock ACC mode, never builds it.

WHY. This car's openpilot brake tops out at about -1.8..-1.9 m/s^2 (the 255-count cap), reached ~0.5 s after it is asked
for, and a lighter request gets less than it asks (-0.8 -> ~-0.5, -1.2 -> ~-1.0): starting earlier is the only lever.
The MPC sees the lead through radarState: its raw speed (vLead), radard's Kalman acceleration (aLeadK) and how long that
acceleration lasts in the prediction (aLeadTau). On this radar aLeadK reaches -2 a median 1.0 s after the lead starts
slowing faster than 1 m/s^2 (182 hard-braking leads, 51 h of drives).

WHAT. leadOne's aLeadK and aLeadTau, for the planner's MPC only, from two ports behind one projection:
  * the raw decel (henryopen/openpilot hcop c3aba1e72, ec47d05ad, 628dad62c, MIT; adapted): the least-squares slope of
    the lead's raw radar speed over its own radar samples (10 Hz; a reading radard repeats is no new sample), over the
    last 0.3 s under RAW_THR (-2.0) and over 0.5 s under RAW_THR2 (-1.5), the last 3 steps not rising, a track seen
    moving (4 m/s), within 80 m, no jump in range or speed (another object), and radard's own aLeadK already under -1.0
    on the sample's frame. Decided once per radar sample. It stands in for aLeadK when harsher (floored at -6).
  * the tau attack (CarrotPilot carrot-wip openpilot/selfdrive/carrot/radar_motion/lead_dynamics.py, MIT): while the
    lead's braking is strong and still building (projected accel under -0.5, jerk under -1.5, full at -1.5 / -3.0) on
    two distinct radar samples <= 150 ms apart, aLeadTau decays up to 0.5 a frame instead of radard's 0.1, so the MPC
    keeps the braking longer. Here only on samples where the raw decel counts (tau_needs_raw), and as a factor on
    radard's own aLeadTau (radard's quiet reset and its "an acceleration is never prolonged" rule kept).
  Left out: vision leads (radarTrackId -1), held leads (<= -2, held_lead.py), stationary and oncoming tracks (never
  moving), far tracks. radarState itself is never changed: the projection is handed to the MPC and to the re-entry
  ramp's lead gate in the planner.

MODES (LeadBrakeAssistMode, read once when plannerd starts): 0 off (nothing built, upstream's plan bit for bit),
1 shadow (default: computes, solves the MPC a second time from the planner's own warm start while the projection
differs, logs customReserved13 and one cloudlog line per episode; the plan is untouched - proven bit for bit), 2 live.

REPLAY (radard on the logged radar CAN, then the real planner open loop, mode 2 against mode 0; 192 routes, 51.3 h,
21.2 h engaged; the 14 clutter routes and round 8's 69 among them). Phantom brake: an engaged episode on a lead that
did not then slow (its own track, or what radard followed next) with the plan 0.1 m/s^2 or more firmer.
  * this (rawC + tau on raw): 0 phantom brakes on the 14, the 69 and all 192 routes. 182 hard-braking leads (112
    engaged), projected on 176. The plan reaches -1.5 a median 0.13 s (max 1.05) and -2.0 0.15 s (max 1.70) sooner;
    first-order (this car's brake, a 0.3 s lag) a median 0.28 m and up to 1.85 m more room after 4 s on the engaged
    ones, 1 m or more on 17 of 112. The plan is 0.3 m/s^2 or more firmer on 5.3 episodes per engaged hour, every one on
    a lead that was braking. Active on 0.23% of frames. One FCW frame more (disengaged, a lead braking at -2.8 20 m
    ahead).
  * henryopen verbatim (vLead differenced over 0.3 s in frames, -1.0, 2 frames): 40 phantom brakes on the 14 clutter
    routes (15 per engaged hour); with his gap gate 34. This radar's raw speed jumps up to 2 m/s between readings.
  * CarrotPilot alone (the tau attack on aLeadK): 0 phantom brakes but ~nothing gained (median 0.00 s, 0.03 m): radard's
    own tau has usually decayed by the time aLeadK is under -0.5.
  * the raw decel with 0.5 s under -2.0 (rawA): 0 phantoms, about two thirds of the gain, 3.5 firm episodes / engaged h.
SHADOW PROOF. process_replay of plannerd -> controlsd -> card (each feeding the next) on routes d5 (segments 15-26,
the shadow active on 54 frames) and 142 (10-18, 147 frames, logging plans up to 0.61 m/s^2 firmer), push12 against
this in mode 1: longitudinalPlan, controlsState, carControl and sendcan identical message for message.
"""
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from opendbc.car.honda.values import HONDA_ELESYS
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import messaging
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_accel_from_plan
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import LongitudinalMpc, N, T_IDXS, get_T_FOLLOW
from openpilot.selfdrive.modeld.constants import ModelConstants

MODE_OFF, MODE_SHADOW, MODE_LIVE = 0, 1, 2
MODE_DEFAULT = MODE_SHADOW
PARAM = "LeadBrakeAssistMode"
LOG_TAG = "leadbrake"

# why the projection differs from radarState's leadOne (CustomReserved13.reason)
REASON_RAW = 1   # the lead's raw radar speed is falling faster than radard's Kalman aLeadK says (henryopen)
REASON_TAU = 2   # the lead's braking is strong and still building: keep it longer in the prediction (CarrotPilot)

# why a raw decel under the threshold did not count (CustomReserved13.blocked)
BLOCK_SLOW = 1     # the track has not been seen moving (stationary objects, clutter, oncoming traffic)
BLOCK_RANGE = 2    # beyond raw_d_max: the radar's speed is noisiest far out
BLOCK_GAP = 4      # the gap does not need it yet (henryopen's RAW_GAP_FRAC / RAW_TTC gate)
BLOCK_MONO = 8     # the samples are not falling one after another (a jump, not a braking car)
BLOCK_KALMAN = 16  # radard's own aLeadK does not yet say the lead is slowing

RADAR_DT = 0.1     # s: the Elesys radar reports at 10 Hz; radard runs at 20 Hz and sees each reading twice
LEAD_ACCEL_TAU = 1.5                      # radard._LEAD_ACCEL_TAU
LEAD_ACCEL_ALPHA = 0.05 / (0.45 + 0.05)   # radard's FirstOrderFilter(1.5, 0.45, DT_MDL).update(0.0), per frame
ACTIVE_DV = 0.05   # m/s: a projection counts as different when it moves the lead's predicted 4 s speed by this much
EPISODE_GAP = 0.5  # s: active frames this close together are one episode (one cloudlog line)
LOG_MIN_GAP = 2.0  # s between cloudlog lines,
LOG_MAX = 40       # and at most this many a drive


@dataclass
class Config:
  use_raw: bool = True
  use_tau: bool = True
  tau_from_proj: bool = True     # the tau attack reads the projected accel (raw decel included), not aLeadK alone
  tau_needs_raw: bool = True     # the tau attack only on samples where the raw decel counts
  # raw decel (henryopen hcop c3aba1e72 / ec47d05ad / 628dad62c, adapted to a 10 Hz multi-track radar)
  raw_frames: bool = False       # henryopen verbatim: vLead differenced over raw_win in radard FRAMES, raw_n frames running
  raw_win: float = 0.3           # s: least-squares slope of the lead's raw vLead over its radar samples in this window
  raw_win2: float = 0.5          # s: and over this longer window too (0: not checked)
  raw_thr2: float | None = -1.5  # m/s^2: the longer window's threshold (None: raw_thr)
  raw_thr: float = -2.0          # m/s^2: a window slope under this counts,
  raw_n: int = 1                 # for this many radar samples in a row (each <= raw_gap_max apart)
  raw_gap_max: float = 0.15      # s
  raw_min_samples: int = 4       # samples the window must hold
  raw_jump_m: float = 2.0        # m: a range step this much beyond what vRel explains is another object: restart
  raw_step_max: float = 12.0     # m/s^2: a speed step between two samples steeper than this is no car braking: restart
  raw_v_moving: float = 4.0      # m/s: the track must have read this raw vLead at some point while leadOne
  raw_d_max: float = 80.0        # m
  raw_mono: bool = True          # each of the last raw_mono_n sample steps no rise beyond raw_mono_dv
  raw_mono_n: int = 3
  raw_mono_dv: float = 0.05      # m/s
  raw_kalman: float = -1.0       # m/s^2: and radard's aLeadK under this on the sample's frame (1e9: not checked)
  raw_floor: float = -6.0        # m/s^2: the most braking the raw decel may claim
  raw_gap_gate: bool = False     # henryopen's gate: only while the gap is under raw_gap_frac of the MPC's or TTC short
  raw_gap_frac: float = 0.9
  raw_ttc: float = 3.0           # s
  # tau attack (CarrotPilot carrot-wip radar_motion/lead_dynamics.py, MIT)
  tau_a_start: float = 0.5
  tau_a_full: float = 1.5
  tau_j_start: float = 1.5
  tau_j_full: float = 3.0
  tau_attack_rc: float = 0.05
  tau_confirm_gap: float = 0.15
  # jerk: CarrotPilot opendbc radar_tracks.py MyTrack (a quadratic velocity trend), at the radar's 10 Hz
  jerk_min_samples: int = 7
  jerk_hist_s: float = 0.5
  jerk_deadband: float = 0.2
  jerk_rc: float = 0.25
  jerk_zero_rc: float = 0.10


def lead_brake_applies(CP, CP_SP) -> bool:
  """HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (reentry_ramp.reentry_ramp_applies's gate)."""
  try:
    if CP.brand != 'honda' or CP.carFingerprint not in HONDA_ELESYS or not CP.openpilotLongitudinalControl:
      return False
    return not (int(getattr(CP_SP, 'flags', 0)) & HondaFlagsSP.ELESYS_STOCK_ACC.value)
  except Exception:
    return False


def read_mode(params=None) -> int:
  """LeadBrakeAssistMode, read once when the planner starts (plannerd runs per drive). Anything unreadable is shadow."""
  try:
    if params is None:
      from openpilot.common.params import Params
      params = Params()
    mode = int(params.get(PARAM, return_default=True))
  except Exception:
    return MODE_DEFAULT
  return mode if mode in (MODE_OFF, MODE_SHADOW, MODE_LIVE) else MODE_DEFAULT


def _ramp(value: float, start: float, full: float) -> float:
  return max(0.0, min(1.0, (value - start) / (full - start)))


def _finite(*xs) -> bool:
  return all(math.isfinite(x) for x in xs)


def ls_slope(ts, xs) -> float:
  n = len(ts)
  tm = sum(ts) / n
  xm = sum(xs) / n
  den = sum((t - tm) ** 2 for t in ts)
  return sum((t - tm) * (x - xm) for t, x in zip(ts, xs, strict=True)) / den if den > 0 else 0.0


def quadratic_jerk_weights(n: int) -> list[float]:
  """CarrotPilot radar_tracks.radar_quadratic_jerk_weights: 2 x the t^2 row of the least-squares pseudo-inverse of
  [1, t, t^2] at t = -(n-1) .. 0 (radar samples)."""
  ts = np.arange(n, dtype=float) - (n - 1)
  design = np.column_stack((np.ones(n), ts, ts ** 2))
  return (2.0 * np.linalg.pinv(design)[2]).tolist()


class _Jerk:
  """The lead's jerk at its radar samples: CarrotPilot's MyTrack estimator (a causal quadratic trend of the raw speed,
  per-step clip, deadband, first-order filter) at the Elesys radar's 10 Hz."""

  def __init__(self, cfg: Config):
    self.cfg = cfg
    self.hist: deque = deque(maxlen=max(cfg.jerk_min_samples, int(round(cfg.jerk_hist_s / RADAR_DT)) + 1))
    self.j = 0.0
    self.cnt = 0
    self._w: dict[int, list[float]] = {}

  def update(self, v: float) -> float:
    cfg = self.cfg
    if self.hist:
      prev = self.hist[-1]
      v = prev + max(-10.0, min(5.0, (v - prev) / RADAR_DT)) * RADAR_DT
    self.hist.append(v)
    self.cnt += 1
    n = len(self.hist)
    if n < cfg.jerk_min_samples or self.cnt <= 2:
      target = 0.0
    else:
      if n not in self._w:
        self._w[n] = quadratic_jerk_weights(n)
      jm = sum(w * x for w, x in zip(self._w[n], self.hist, strict=True)) / RADAR_DT ** 2
      jm = max(-6.0, min(6.0, jm))
      target = math.copysign(max(0.0, abs(jm) - cfg.jerk_deadband), jm)
    rc = cfg.jerk_zero_rc if target == 0.0 else cfg.jerk_rc
    alpha = RADAR_DT / (rc + RADAR_DT)
    self.j = max(-5.0, min(5.0, (1.0 - alpha) * self.j + alpha * target))
    return self.j


class _TauBoost:
  """CarrotPilot's jerk-confirmed attack on aLeadTau (lead_dynamics.LeadAccelTau), kept as a factor on radard's own
  aLeadTau so that only the attack differs: radard decays aLeadTau by LEAD_ACCEL_ALPHA per frame while |aLeadK| >= 0.5
  and resets it to 1.5 below; Carrot decays by up to 0.5 per frame instead, on two distinct radar samples no more than
  tau_confirm_gap apart, by the strength min over that pair of ramp(-a, 0.5..1.5) x ramp(-j, 1.5..3.0). A positive
  acceleration restores radard's tau: it never prolongs an acceleration."""

  def __init__(self, cfg: Config):
    self.cfg = cfg
    self.boost = 1.0
    self.prev_strength = 0.0
    self.sample_t: float | None = None

  def update(self, a: float, j: float, sample_t: float, new_sample: bool, allowed: bool = True) -> float:
    cfg = self.cfg
    if not _finite(a, j, sample_t):
      self.prev_strength, self.sample_t, self.boost = 0.0, None, 1.0
      return self.boost
    strength = _ramp(-a, cfg.tau_a_start, cfg.tau_a_full) * _ramp(-j, cfg.tau_j_start, cfg.tau_j_full) if new_sample and allowed else 0.0
    confirmed = 0.0
    if self.sample_t is not None and new_sample and 0.0 < sample_t - self.sample_t <= cfg.tau_confirm_gap:
      confirmed = min(strength, self.prev_strength)
    if new_sample:
      self.prev_strength = strength
      self.sample_t = sample_t
    if abs(a) < 0.5 or a >= 0.0:
      self.boost = 1.0
    else:
      fast = DT_MDL / (cfg.tau_attack_rc + DT_MDL)
      alpha = LEAD_ACCEL_ALPHA + confirmed * (fast - LEAD_ACCEL_ALPHA)
      self.boost *= (1.0 - alpha) / (1.0 - LEAD_ACCEL_ALPHA)
    return self.boost


class _Track:
  def __init__(self, tid: int, cfg: Config):
    self.tid = tid
    self.samples: deque = deque()     # (t, vLead, dRel) at each new radar sample, over the last window + margin
    self.key: tuple | None = None     # (dRel, vRel) of the last sample: a radard frame repeating it is no new sample
    self.last_t: float | None = None
    self.n = 0                        # samples seen while leadOne
    self.v_max = -math.inf
    self.slope = 0.0
    self.slope2 = 0.0
    self.run = 0
    self.mono = False
    self.jerk = _Jerk(cfg)
    self.tau = _TauBoost(cfg)
    self.fh: deque = deque(maxlen=int(round(cfg.raw_win / DT_MDL)) + 1)   # henryopen: vLead at every radard frame
    self.last_d: float | None = None
    self.raw: float | None = None     # the raw decel decided on the last sample's frame (None: it does not count)
    self.blocked = 0


@dataclass
class Projection:
  active: bool = False      # moves the lead's predicted speed by ACTIVE_DV or more
  reason: int = 0
  blocked: int = 0
  track_id: int = -1
  a_lead_k: float = 0.0
  a_lead_tau: float = LEAD_ACCEL_TAU
  a_raw: float = 0.0        # the raw decel when it counts (0 otherwise)
  slope: float = 0.0        # the window slope, counted or not
  j_lead: float = 0.0
  a_lead: float = 0.0       # projected
  tau: float = LEAD_ACCEL_TAU   # projected


_DV_T = np.arange(0.0, 4.0 + 1e-9, 0.1)


def predicted_dv(a: float, tau: float) -> float:
  """The lead's speed change over 4 s as long_mpc.extrapolate_lead() predicts it (before its clip at standstill)."""
  return float(np.sum(a * np.exp(-tau * _DV_T ** 2 / 2.0)) * 0.1)


def _desired_gap(v_ego: float, v_lead: float, t_follow: float) -> float:
  """long_mpc: get_safe_obstacle_distance(v_ego, t_follow) - get_stopped_equivalence_factor(v_lead)."""
  return v_ego ** 2 / (2 * 2.5) + t_follow * v_ego + 6.0 - v_lead ** 2 / (2 * 2.5)


class LeadBrakeAssist:
  """leadOne as the MPC should see it when the lead brakes hard. update() once per planner frame."""

  def __init__(self, cfg: Config | None = None):
    self.cfg = cfg or Config()
    self.track: _Track | None = None
    self.proj = Projection()

  def update(self, lead, t: float, v_ego: float, t_follow: float = 1.25) -> Projection:
    cfg = self.cfg
    p = Projection()
    self.proj = p
    try:
      present, radar, tid = bool(lead.present), bool(lead.radar), int(lead.radarTrackId)
      d, v_rel, v_lead = float(lead.dRel), float(lead.vRel), float(lead.vLead)
      a_k, tau_k = float(lead.aLeadK), float(lead.aLeadTau)
    except (AttributeError, TypeError, ValueError):
      self.track = None
      return p
    p.track_id, p.a_lead_k, p.a_lead_tau, p.a_lead, p.tau = tid, a_k, tau_k, a_k, tau_k
    # a radar track only: not a vision lead (-1), not a held lead (<= -2, held_lead.py), never a non-finite one
    if not present or not radar or tid < 0 or not _finite(d, v_rel, v_lead, a_k, tau_k, t, v_ego):
      self.track = None
      return p
    tr = self.track
    if tr is None or tr.tid != tid:
      tr = self.track = _Track(tid, cfg)
    # a new radar sample: the reading changed, or a radar period has passed (a reading that really stayed the same; a
    # stalled radar's repeats only flatten the slopes, which never makes the projection brake)
    new = (d, v_rel) != tr.key or (tr.last_t is not None and t - tr.last_t >= RADAR_DT - 1e-3)
    if new:
      if tr.last_t is not None and t - tr.last_t > cfg.raw_gap_max + 0.05:
        tr.samples.clear()      # a dropout: the slope restarts
        tr.run = 0
      if tr.samples:
        t1, v1, d1 = tr.samples[-1]
        if abs(d - (d1 + v_rel * (t - t1))) > cfg.raw_jump_m or abs(v_lead - v1) > cfg.raw_step_max * max(t - t1, RADAR_DT):
          tr.samples.clear()    # another object in the same track, or a reading no braking car could make
          tr.run = 0
      tr.key = (d, v_rel)
      tr.last_t = t
      tr.n += 1
      tr.v_max = max(tr.v_max, v_lead)
      tr.samples.append((t, v_lead, d))
      keep = max(cfg.raw_win, cfg.raw_win2, RADAR_DT * cfg.raw_mono_n)
      while tr.samples and tr.samples[0][0] < t - keep - 0.025:
        tr.samples.popleft()
      win = [s for s in tr.samples if s[0] >= t - cfg.raw_win - 0.025]
      if len(win) >= cfg.raw_min_samples:
        tr.slope = ls_slope([s[0] for s in win], [s[1] for s in win])
      else:
        tr.slope = 0.0
      if cfg.raw_win2 > 0.0:
        win2 = [s for s in tr.samples if s[0] >= t - cfg.raw_win2 - 0.025]
        tr.slope2 = ls_slope([s[0] for s in win2], [s[1] for s in win2]) if len(win2) >= cfg.raw_min_samples + 2 else 0.0
      vs = [s[1] for s in tr.samples][-(cfg.raw_mono_n + 1):]
      tr.mono = len(vs) == cfg.raw_mono_n + 1 and all(b - a <= cfg.raw_mono_dv for a, b in zip(vs[:-1], vs[1:], strict=True))
      if not cfg.raw_frames:
        thr2 = cfg.raw_thr if cfg.raw_thr2 is None else cfg.raw_thr2
        under = tr.slope < cfg.raw_thr and (cfg.raw_win2 <= 0.0 or tr.slope2 < thr2)
        tr.run = tr.run + 1 if under else 0
      tr.jerk.update(v_lead)
    if cfg.raw_frames:
      # henryopen lead_view._raw_decel, verbatim: every frame; a dRel jump over RAW_JUMP_M restarts the difference
      if tr.last_d is not None and abs(d - tr.last_d) > cfg.raw_jump_m:
        tr.fh.clear()
        tr.run = 0
      tr.last_d = d
      tr.fh.append(v_lead)
      tr.slope = (tr.fh[-1] - tr.fh[0]) / cfg.raw_win if len(tr.fh) == tr.fh.maxlen else 0.0
      tr.run = tr.run + 1 if tr.slope < cfg.raw_thr else 0
      tr.mono = True
    p.slope = tr.slope
    p.j_lead = tr.jerk.j

    # the raw decel is decided once per radar sample, on the frame it arrives (radard's aLeadK of that frame), and held
    # until the next: a frame repeating a reading neither confirms nor changes it (henryopen's frames mode: every frame)
    if new or cfg.raw_frames:
      tr.raw, tr.blocked = None, 0
      if cfg.use_raw and tr.run >= cfg.raw_n:
        blocked = 0
        if tr.v_max < cfg.raw_v_moving:
          blocked |= BLOCK_SLOW
        if d > cfg.raw_d_max:
          blocked |= BLOCK_RANGE
        if cfg.raw_mono and not tr.mono:
          blocked |= BLOCK_MONO
        if a_k >= cfg.raw_kalman:
          blocked |= BLOCK_KALMAN
        if cfg.raw_gap_gate:
          want = _desired_gap(v_ego, v_lead, t_follow)
          closing = v_ego - v_lead
          if not ((want > 0.0 and d < cfg.raw_gap_frac * want) or (closing > 0.1 and d / closing < cfg.raw_ttc)):
            blocked |= BLOCK_GAP
        tr.blocked = blocked
        if not blocked:
          tr.raw = max(tr.slope, cfg.raw_floor)
    p.blocked = tr.blocked
    a_proj = a_k
    if tr.raw is not None:
      p.a_raw = tr.raw
      if tr.raw < a_proj:
        a_proj = tr.raw
        p.reason |= REASON_RAW

    tau = tau_k
    if cfg.use_tau:
      a_in = a_proj if cfg.tau_from_proj else a_k
      boost = tr.tau.update(a_in, tr.jerk.j, tr.last_t if tr.last_t is not None else t, new,
                            allowed=not cfg.tau_needs_raw or tr.raw is not None)
      if boost < 1.0 and a_in < 0.0:
        tau = tau_k * boost
        if tau < tau_k:
          p.reason |= REASON_TAU
    p.a_lead, p.tau = a_proj, tau
    p.active = p.reason != 0 and predicted_dv(a_proj, tau) - predicted_dv(a_k, tau_k) <= -ACTIVE_DV
    return p


class _LeadView:
  """radarState's leadOne with the projected aLeadK and aLeadTau: what the MPC reads (present, dRel, vLead, aLeadK,
  aLeadTau, modelProb) and every other field as radard published it."""

  def __init__(self, base, a_lead: float, tau: float):
    self._base = base
    self.aLeadK = a_lead
    self.aLeadTau = tau

  def __getattr__(self, k):
    return getattr(self.__dict__['_base'], k)


class _RadarView:
  def __init__(self, base, lead_one):
    self._base = base
    self.leadOne = lead_one

  def __getattr__(self, k):
    return getattr(self.__dict__['_base'], k)


class LeadBrakePlanner:
  """The longitudinal planner's handle on the assist. Built only where lead_brake_applies(); in mode 0 the planner
  does not build it either (lead_brake_planner()).

  before_mpc(): radarState for the planner's MPC - the projection in mode 2, radarState itself in mode 1 - and, while
  the projection is active, a snapshot of the MPC's warm start. after_plan(): the comparison solve, on a second MPC
  started from that snapshot, of the other radarState (the projection in mode 1, radarState in mode 2), turned into a
  plan the same way the planner does (get_accel_from_plan, then the min with the other candidates). publish(): the
  frame's CustomReserved13."""

  def __init__(self, mode: int, dt: float = DT_MDL, cfg: Config | None = None):
    self.mode = mode
    self.dt = dt
    self.assist = LeadBrakeAssist(cfg)
    self.t = 0.0
    self.proj = Projection()
    self._snap = None
    self._alt = None
    self.compared = False
    self.a_base = self.a_assist = 0.0
    self.fcw_base = self.fcw_assist = False
    self.episodes = 0
    self._ep: dict | None = None
    self._last_log_t = -math.inf
    self._logs = 0
    self._failed = False
    try:
      self._mpc2 = LongitudinalMpc(dt=dt)   # the second MPC, built once here so no plan frame pays for it
    except Exception:
      self._fail("init")

  # ---- planner hooks ----
  def before_mpc(self, radar_state, mpc, v_ego: float, personality, prev_accel_constraint: bool):
    self.t += self.dt
    self._alt = None
    self.compared = False
    if self._failed:
      return radar_state
    try:
      self.proj = self.assist.update(radar_state.leadOne, self.t, v_ego, get_T_FOLLOW(personality))
      if not self.proj.active:
        return radar_state
      view = _RadarView(radar_state, _LeadView(radar_state.leadOne, self.proj.a_lead, self.proj.tau))
      self._snap = (_snapshot(mpc), prev_accel_constraint, personality)
      if self.mode == MODE_LIVE:
        self._alt = radar_state
        return view
      self._alt = view
      return radar_state
    except Exception:
      self._fail("before_mpc")
      return radar_state

  def after_plan(self, a_plan: float, a_others, action_t: float, mpc_fcw: bool, standstill: bool, engaged: bool,
                 v_ego: float) -> None:
    """a_plan: the planner's min over its candidates this frame (before the re-entry ramp); a_others: the candidates
    other than the MPC's (cruise, and e2e when it is one); mpc_fcw: the planner's FCW this frame."""
    self.a_base = self.a_assist = float(a_plan)
    self.fcw_base = self.fcw_assist = bool(mpc_fcw)
    if self._alt is not None and not self._failed:
      try:
        a_alt, fcw_alt, ok = self._compare(a_others, action_t, standstill)
        self.compared = ok
        if ok:
          if self.mode == MODE_LIVE:
            self.a_base, self.fcw_base = a_alt, fcw_alt
          else:
            self.a_assist, self.fcw_assist = a_alt, fcw_alt
      except Exception:
        self._fail("after_plan")
    self._episode(engaged, v_ego)

  def publish(self, pm, valid: bool, a_target: float) -> None:
    msg = messaging.new_message('customReserved13')
    msg.valid = bool(valid)
    m = msg.customReserved13
    p = self.proj
    m.mode = self.mode
    m.active = bool(p.active)
    m.reason = int(p.reason)
    m.blocked = int(p.blocked)
    m.trackId = int(p.track_id)
    m.aLeadK = float(p.a_lead_k)
    m.aLeadTau = float(p.a_lead_tau)
    m.aRaw = float(p.slope)
    m.jLead = float(p.j_lead)
    m.aLeadProj = float(p.a_lead)
    m.aLeadTauProj = float(p.tau)
    m.aTarget = float(a_target)
    m.aTargetBase = float(self.a_base)
    m.aTargetAssist = float(self.a_assist)
    m.fcwBase = bool(self.fcw_base)
    m.fcwAssist = bool(self.fcw_assist)
    m.compared = bool(self.compared)
    m.episodes = min(int(self.episodes), 65535)
    pm.send('customReserved13', msg)

  # ---- internals ----
  def _compare(self, a_others, action_t: float, standstill: bool):
    m = self._mpc2
    (xs, us, x0, a_prev, crash_cnt), prev_accel_constraint, personality = self._snap
    m.set_weights(prev_accel_constraint, personality=personality)
    for i in range(N + 1):
      m.solver.set(i, 'x', xs[i])
    for i in range(N):
      m.solver.set(i, 'u', us[i])
    m.x0[:] = x0
    m.a_prev = a_prev.copy()
    m.crash_cnt = crash_cnt
    m.update(self._alt, personality=personality)
    if m.solution_status != 0:
      return 0.0, False, False
    t_idx = ModelConstants.T_IDXS[:CONTROL_N]
    v = np.interp(t_idx, T_IDXS, m.v_solution)
    a = np.interp(t_idx, T_IDXS, m.a_solution)
    a_mpc = get_accel_from_plan(v, a, t_idx, action_t=action_t)
    a_alt = min([a_mpc, *a_others])
    return float(a_alt), bool(m.crash_cnt > 2 and not standstill), True

  def _episode(self, engaged: bool, v_ego: float) -> None:
    p = self.proj
    delta = self.a_assist - self.a_base
    if p.active:
      if self._ep is None:
        self.episodes += 1
        self._ep = {'t0': self.t, 'track': p.track_id, 'v_ego': v_ego, 'reasons': 0, 'a_k_min': p.a_lead_k,
                    'a_proj_min': p.a_lead, 'tau_min': p.tau, 'delta_min': 0.0, 'fcw_assist': False, 'fcw_base': False,
                    'engaged': False, 'frames': 0}
      e = self._ep
      e['t1'] = self.t
      e['frames'] += 1
      e['reasons'] |= p.reason
      e['a_k_min'] = min(e['a_k_min'], p.a_lead_k)
      e['a_proj_min'] = min(e['a_proj_min'], p.a_lead)
      e['tau_min'] = min(e['tau_min'], p.tau)
      e['delta_min'] = min(e['delta_min'], delta)
      e['fcw_assist'] |= self.fcw_assist
      e['fcw_base'] |= self.fcw_base
      e['engaged'] |= bool(engaged)
    elif self._ep is not None and self.t - self._ep['t1'] > EPISODE_GAP:
      e, self._ep = self._ep, None
      if self._logs < LOG_MAX and self.t - self._last_log_t >= LOG_MIN_GAP:
        self._logs += 1
        self._last_log_t = self.t
        cloudlog.event(LOG_TAG, error=True, mode=self.mode, episode=self.episodes, t0=round(e['t0'], 2),
                       dur=round(e['t1'] - e['t0'], 2), frames=e['frames'], track=e['track'], v_ego=round(e['v_ego'], 2),
                       reasons=e['reasons'], a_k_min=round(e['a_k_min'], 2), a_proj_min=round(e['a_proj_min'], 2),
                       tau_min=round(e['tau_min'], 3), delta_min=round(e['delta_min'], 3), fcw_assist=e['fcw_assist'],
                       fcw_base=e['fcw_base'], engaged=e['engaged'])

  def _fail(self, where: str) -> None:
    # never take the planner down: the assist turns itself off for the rest of the drive, radarState passes through
    self._failed = True
    self._alt = None
    cloudlog.exception(f"{LOG_TAG}: {where} failed, off for this drive")


def _snapshot(mpc):
  """The MPC's warm start right before its solve: the solver's x and u at every stage (set_cur_state may have reset
  them), x0, a_prev and crash_cnt. A second MPC started from it and given the same radarState lands within about 1e-6
  m/s^2 of the planner's own plan (the QP solver's internal state is not copied; test_lead_brake_assist pins it)."""
  xs = [np.array(mpc.solver.get(i, 'x')) for i in range(N + 1)]
  us = [np.array(mpc.solver.get(i, 'u')) for i in range(N)]
  return xs, us, mpc.x0.copy(), np.array(mpc.a_prev), mpc.crash_cnt


def lead_brake_planner(CP, CP_SP, dt: float = DT_MDL, mode: int | None = None, cfg: Config | None = None):
  """What the planner holds: a LeadBrakePlanner on HONDA_ELESYS (op long, not stock ACC) with the mode not off, else
  None (upstream's planner, bit for bit)."""
  if not lead_brake_applies(CP, CP_SP):
    return None
  mode = read_mode() if mode is None else mode
  if mode == MODE_OFF:
    return None
  return LeadBrakePlanner(mode, dt, cfg)
