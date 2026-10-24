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
    the lead's raw radar speed over its own radar samples (10 Hz; a reading radard repeats is no new sample; each stamped
    with radarState's own time), over the last 0.3 s under RAW_THR (-2.0) and over 0.5 s under RAW_THR2 (-1.5), the last
    3 steps not rising, a track seen moving (4 m/s), within 60 m, no jump in range or speed (another object), and
    radard's own aLeadK already under -1.0 on the sample's frame. And corroborated: the camera (modelV2's lead, the same
    car within 6 m or 30% of the range, prob 0.5) saw it slowing (accel under -0.25) in the last 0.3 s; else the fall
    must show on two radar samples in a row - as must a fall resting on one reading (a step of 0.5 m/s or more, the two
    before it under half of it), camera or not. This radar's speed glitches - one to three readings 1-2.5 m/s low, then
    back - look just like a car braking, and the camera does not see them. Decided once per radar sample. It stands in
    for aLeadK when harsher (floored at -6). It is harsher than the lead's real braking by design, not a better estimate
    of it: the slope is taken the moment it crosses -2, a median 0.5 m/s^2 harsher than what the lead did (measured with
    hindsight), so the MPC starts early rather than accurately.
  * the tau attack (CarrotPilot carrot-wip openpilot/selfdrive/carrot/radar_motion/lead_dynamics.py, MIT): while the
    lead's braking is strong and still building (projected accel under -0.5, jerk under -1.5, full at -1.5 / -3.0) on
    two distinct radar samples <= 150 ms apart, aLeadTau decays faster than radard's, so the MPC keeps the braking longer:
    at full strength to about 0.45 of its value per 0.1 s (radard alone: 0.81; the attack's 0.5 applies on the sample's
    frame only, every other frame). Here only on samples where the raw decel counts (tau_needs_raw), and as a factor on
    radard's own aLeadTau (radard's quiet reset and its "an acceleration is never prolonged" rule kept); given back on
    the first sample on which the lead's raw speed no longer falls (tau_release): a braking that ended is not prolonged.
  Left out: vision leads (radarTrackId -1), held leads (<= -2, held_lead.py), stationary and oncoming tracks (never
  moving), tracks beyond 60 m. radarState itself is never changed: the projection is handed to the MPC and to the
  re-entry ramp's lead gate in the planner (in mode 2 a projected lead under -2.0 ends a re-entry ramp, as a lead radard
  sees braking that hard does). Anything else that solves an MPC on the lead in the plan (close follow, once merged) must
  be handed the same view.

MODES (LeadBrakeAssistMode, read once when plannerd starts; sunnylink Vehicle > Honda, offroad only): 0 off (nothing
built, upstream's plan bit for bit), 1 shadow (default: computes, solves the MPC a second time from the planner's own
warm start while the projection differs, logs customReserved13 and an error-level cloudlog line per episode; the plan
is untouched - proven bit for bit), 2 live. Shadow's logged difference is one solve on one frame: mode 2's effect on
the plan lasts longer (the MPC carries it on), so read the shadow logs as a lower bound and the replay as the measure.
customReserved13 goes out on every frame where the projection is active, a raw decel is under its threshold or an
episode is open, and once a second otherwise; all of them reach the qlog. Episodes that end within LOG_MIN_GAP of the
last line share the next one (merged = how many), up to LOG_MAX lines a drive.

REPLAY (radard on the logged radar CAN, then the real planner open loop, mode 2 against mode 0). Phantom brake: engaged,
the plan 0.1 m/s^2 or more firmer, on a lead that was not braking. Two independent replays, each with its own test:
  * the review's (opendbc's RadarInterface on the bus-1 CAN, the branch's RadarD, a reduced planner; 78 routes - the 14
    clutter routes, round 8's 69, 9 more from October - 29.8 h, 13.5 h engaged; the lead "not braking" when its own radar
    speed, a 1 s centered slope seen with hindsight, never fell faster than -0.5 where the raw decel was used): the first
    version (80 m, no corroboration) braked for nothing twice engaged - c8 t=1449.31 (three readings 1.8 m/s low 75 m
    out) and fd t=662.96 (a jump to ~20 m/s for 0.7 s, 37 m) - and would have three more times not engaged (de, 112, 14).
    This: none engaged; one not engaged (14 t=1412.35, 8 m/s, 10 m, the camera itself seeing the lead ease at -0.45). On
    the first version's 184 engaged hard-braking leads, the same events, the plan reaches -1.5 a mean 0.20 s sooner (the
    first version 0.21) and -2.0 0.24 s (0.26); first-order (this car's brake, a 0.3 s lag) 0.25 m more room after 4 s
    (0.31); on the 50 where the plan was 0.3 or more firmer 0.25 / 0.33 s (0.27 / 0.33) and 0.69 m (0.82). The plan is
    0.3 or more firmer 3.4 times an engaged hour (3.7), active on 0.20% of frames (0.25%), no new FCW. Onsets resting on
    one reading 17 (76), of them bouncing straight back 2 (10). Frames 0.1 or more firmer while the lead had stopped
    braking 131 (205). In mode 2 a projected lead ended 10 re-entry ramps (1.5 s after a gas lift-off) on the lead gate
    before radard's view would have, and 10 more through the MPC's own plan, on leads that were slowing (route 141
    t=199.2: -1.4 real, -3.25 projected, the plan -1.58 at once instead of ramping in over 0.65 s).
  * the first version's (its stage-1 radard extraction, the full planner, windows around every episode and every true
    hard-braking event; 192 routes, 51.3 h, 21.2 h engaged; "not braking": the lead did not then slow): 0 phantom brakes
    on the 14 + 69 routes and on all 192 (as before); no new FCW (the first version had one, not engaged); the plan
    reaches -1.5 a mean 0.15 s sooner (0.17) and -2.0 0.18 s (0.20, max 1.70), 0.28 m median room (0.26); 0.3 or more
    firmer 5.0 times an engaged hour (5.3).
  How the first version was chosen (its replay): henryopen's rule as written (vLead differenced over 0.3 s in frames,
  -1.0, 2 frames) braked for nothing 40 times on the 14 clutter routes (34 with his gap gate) - this radar's raw speed
  jumps up to 2 m/s between readings; CarrotPilot's tau attack alone gained ~nothing (radard's own tau has usually
  decayed by the time aLeadK is under -0.5); the 0.5 s window at -2.0 instead of -1.5 kept about two thirds of the gain.
SHADOW PROOF. process_replay of plannerd on route 141 segments 1-10 (12000 plans): mode 1 against mode 0, every
longitudinalPlan and driverAssistance identical (processingDelay and solverExecutionTime aside); mode 1 logged 898
customReserved13 messages, 100 of them active, every one compared. The first version's proof: plannerd -> controlsd ->
card on routes d5 (15-26) and 142 (10-18) against push12, longitudinalPlan, controlsState, carControl and sendcan
identical message for message.
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
BLOCK_CAMERA = 32  # not corroborated yet: no camera agreement (or a fall resting on one reading) and not raw_n_alone samples

RADAR_DT = 0.1     # s: the Elesys radar reports at 10 Hz; radard runs at 20 Hz and sees each reading twice
LEAD_ACCEL_TAU = 1.5                      # radard._LEAD_ACCEL_TAU
LEAD_ACCEL_ALPHA = 0.05 / (0.45 + 0.05)   # radard's FirstOrderFilter(1.5, 0.45, DT_MDL).update(0.0), per frame
ACTIVE_DV = 0.05   # m/s: a projection counts as different when it moves the lead's predicted 4 s speed by this much
EPISODE_GAP = 0.5  # s: active frames this close together are one episode (one cloudlog line)
LOG_MIN_GAP = 2.0  # s between cloudlog lines (episodes ending closer together share one),
LOG_MAX = 100      # and at most this many a drive
HEARTBEAT_FRAMES = 20   # customReserved13 at least once a second, so even a qlog shows the assist running


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
  raw_n: int = 1                 # for this many radar samples in a row (each <= raw_gap_max apart) while the camera agrees,
  raw_n_alone: int = 2           # and this many while it does not (raw_cam_a None: raw_n always)
  raw_cam_a: float | None = -0.25  # m/s^2: the camera agrees when the vision model's lead (modelV2 leadsV3[0]) accel is
  raw_cam_win: float = 0.3       # under this on some frame in the last raw_cam_win s, the lead at prob >= raw_cam_prob
  raw_cam_prob: float = 0.5      # and within raw_cam_dx m (or raw_cam_dx_frac of dRel) of the radar lead's range
  raw_cam_dx: float = 6.0
  raw_cam_dx_frac: float = 0.3
  raw_one_step: float = 0.5      # m/s: a fall resting on one reading - the newest step down this much or more, the two
                                 # before it each under half of it - needs raw_n_alone samples even with the camera
  raw_gap_max: float = 0.15      # s
  raw_min_samples: int = 4       # samples the window must hold
  raw_jump_m: float = 2.0        # m: a range step this much beyond what vRel explains is another object: restart
  raw_step_max: float = 12.0     # m/s^2: a speed step between two samples steeper than this is no car braking: restart
  raw_v_moving: float = 4.0      # m/s: the track must have read this raw vLead at some point while leadOne
  raw_d_max: float = 60.0        # m
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
  tau_release: bool = True       # an unconfirmed sample with the lead's raw speed no longer falling (0.3 s slope above
                                 # -tau_a_start) gives radard's own aLeadTau back: a braking that ended is not prolonged
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
  aLeadTau so that only the attack differs: radard decays aLeadTau by LEAD_ACCEL_ALPHA (0.1) per frame while
  |aLeadK| >= 0.5 and resets it to 1.5 below; the attack decays it by up to 0.5 instead, on the frame of a radar sample
  that confirms it - two distinct samples no more than tau_confirm_gap apart, by the strength min over that pair of
  ramp(-a, 0.5..1.5) x ramp(-j, 1.5..3.0). A sample comes every other frame (10 Hz radar, 20 Hz planner) and the frames
  between keep radard's 0.1, so at full strength aLeadTau falls to about 0.45 of radard's per 0.1 s, not 0.25. A positive
  acceleration restores radard's tau (it never prolongs an acceleration), and with tau_release so does a sample on which
  the lead's raw speed is no longer falling (a braking that ended is not prolonged either)."""

  def __init__(self, cfg: Config):
    self.cfg = cfg
    self.boost = 1.0
    self.prev_strength = 0.0
    self.sample_t: float | None = None

  def update(self, a: float, j: float, sample_t: float, new_sample: bool, allowed: bool = True,
             falling: bool = True) -> float:
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
    if abs(a) < 0.5 or a >= 0.0 or (cfg.tau_release and new_sample and confirmed <= 0.0 and not falling):
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
    self.cam_t: float | None = None   # the last frame the camera saw this lead slowing (camera_slowing())


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
  d_rel: float = 0.0
  a_cam: float = math.nan   # the camera's lead accel (modelV2 leadsV3[0].a[0]; NaN: no camera lead readable)
  cam: bool = False         # the camera saw this lead slowing within raw_cam_win (camera_slowing())


_DV_T = np.arange(0.0, 4.0 + 1e-9, 0.1)


def predicted_dv(a: float, tau: float) -> float:
  """The lead's speed change over 4 s as long_mpc.extrapolate_lead() predicts it (before its clip at standstill)."""
  return float(np.sum(a * np.exp(-tau * _DV_T ** 2 / 2.0)) * 0.1)


def camera_slowing(model_lead, d_rel: float, cfg: Config) -> tuple[float, bool]:
  """The camera's view of the radar's lead: (its accel, whether it sees the lead slowing). modelV2 leadsV3[0]: its
  accel now (a[0]; NaN when there is no lead, a non-finite one, or prob under raw_cam_prob), and slowing when that is
  under raw_cam_a with the lead's distance within raw_cam_dx m (or raw_cam_dx_frac of the range) of the radar lead's -
  the same car. The camera judges the brake lights and the car's shape, not the Doppler, so a radar speed glitch (which
  on this radar looks just like a car braking for one to three readings) does not move it."""
  if model_lead is None:
    return math.nan, False
  try:
    prob, x, a = float(model_lead.prob), float(model_lead.x[0]), float(model_lead.a[0])
  except (AttributeError, TypeError, ValueError, IndexError):
    return math.nan, False
  if not _finite(prob, x, a, d_rel) or prob < cfg.raw_cam_prob:
    return math.nan, False
  same = abs(x - d_rel) <= max(cfg.raw_cam_dx, cfg.raw_cam_dx_frac * d_rel)
  return a, bool(same and cfg.raw_cam_a is not None and a < cfg.raw_cam_a)


def _one_reading(samples, step: float) -> bool:
  """Does the fall rest on one reading: the newest step down step m/s or more, each of the two before it less than half
  of it? (Reviewer's 12a: 8.09, 8.02, 7.88, 7.79, then 6.81 and back to 7.67 - a lead easing off at -1, one low reading.)"""
  if step <= 0.0 or len(samples) < 4:
    return False
  v = [s[1] for s in list(samples)[-4:]]
  last = v[3] - v[2]
  return last <= -step and v[2] - v[1] > last / 2 and v[1] - v[0] > last / 2


def _desired_gap(v_ego: float, v_lead: float, t_follow: float) -> float:
  """long_mpc: get_safe_obstacle_distance(v_ego, t_follow) - get_stopped_equivalence_factor(v_lead)."""
  return v_ego ** 2 / (2 * 2.5) + t_follow * v_ego + 6.0 - v_lead ** 2 / (2 * 2.5)


class LeadBrakeAssist:
  """leadOne as the MPC should see it when the lead brakes hard. update() once per planner frame."""

  def __init__(self, cfg: Config | None = None):
    self.cfg = cfg or Config()
    self.track: _Track | None = None
    self.proj = Projection()

  def update(self, lead, t: float, v_ego: float, t_follow: float = 1.25, model_lead=None) -> Projection:
    """lead: radarState's leadOne; t: the time of this radarState (s, monotonic); model_lead: modelV2's leadsV3[0] (or
    None), the camera's view of the lead."""
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
    p.track_id, p.a_lead_k, p.a_lead_tau, p.a_lead, p.tau, p.d_rel = tid, a_k, tau_k, a_k, tau_k, d
    # a radar track only: not a vision lead (-1), not a held lead (<= -2, held_lead.py), never a non-finite one
    if not present or not radar or tid < 0 or not _finite(d, v_rel, v_lead, a_k, tau_k, t, v_ego):
      self.track = None
      return p
    tr = self.track
    if tr is None or tr.tid != tid:
      tr = self.track = _Track(tid, cfg)
    p.a_cam, slowing = camera_slowing(model_lead, d, cfg)
    if slowing:
      tr.cam_t = t
    p.cam = tr.cam_t is not None and t - tr.cam_t <= cfg.raw_cam_win + 1e-6
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
    # until the next: a frame repeating a reading neither confirms nor changes it (henryopen's frames mode: every frame).
    # A fall the camera does not see must last raw_n_alone samples: this radar's speed glitches (one to three readings
    # low, then back) look just like a car braking, and the camera does not see them.
    if new or cfg.raw_frames:
      tr.raw, tr.blocked = None, 0
      if cfg.use_raw and tr.run >= cfg.raw_n:
        blocked = 0
        alone = not (p.cam or cfg.raw_cam_a is None) or _one_reading(tr.samples, cfg.raw_one_step)
        if tr.run < (max(cfg.raw_n, cfg.raw_n_alone) if alone else cfg.raw_n):
          blocked |= BLOCK_CAMERA
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
                            allowed=not cfg.tau_needs_raw or tr.raw is not None, falling=tr.slope <= -cfg.tau_a_start)
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
  plan the same way the planner does (get_accel_from_plan, then the min with the other candidates); skipped (and
  flagged) when another layer changed the MPC's candidate after its solve (close follow live, once merged), since the
  second solve could not reproduce that. publish(): CustomReserved13 on every frame that matters (active, a raw decel
  under the threshold, an open episode) and once a second, every one of them in the qlog too."""

  def __init__(self, mode: int, dt: float = DT_MDL, cfg: Config | None = None):
    self.mode = mode
    self.dt = dt
    self.assist = LeadBrakeAssist(cfg)
    self.t = 0.0                   # the planner's frame clock (episodes, the log lines)
    self.frame = 0
    self._ts: float | None = None  # the last radarState time handed to the assist (s)
    self.proj = Projection()
    self._snap = None
    self._mpc_ref = None
    self._alt = None
    self.compared = False
    self.skipped = False
    self.a_base = self.a_assist = 0.0
    self.fcw_base = self.fcw_assist = False
    self.episodes = 0
    self._ep: dict | None = None
    self._pending: dict | None = None   # ended episodes waiting for LOG_MIN_GAP: one line for all of them
    self._last_log_t = -math.inf
    self._logs = 0
    self.unlogged = 0                   # episodes after the LOG_MAX-th line: in customReserved13 only
    self._failed = False
    try:
      self._mpc2 = LongitudinalMpc(dt=dt)   # the second MPC, built once here so no plan frame pays for it
    except Exception:
      self._fail("init")

  # ---- planner hooks ----
  def before_mpc(self, radar_state, mpc, v_ego: float, personality, prev_accel_constraint: bool, model=None,
                 radar_mono_time=None):
    """model: modelV2 (the camera's lead, leadsV3[0]); radar_mono_time: radarState's logMonoTime (ns)."""
    self.t += self.dt
    self.frame += 1
    self._alt = None
    self.compared = False
    self.skipped = False
    if self._failed:
      return radar_state
    try:
      self.proj = self.assist.update(radar_state.leadOne, self._time(radar_mono_time), v_ego, get_T_FOLLOW(personality),
                                     _model_lead(model))
      if not self.proj.active:
        return radar_state
      view = _RadarView(radar_state, _LeadView(radar_state.leadOne, self.proj.a_lead, self.proj.tau))
      self._snap = (_snapshot(mpc), prev_accel_constraint, personality)
      self._mpc_ref = mpc
      if self.mode == MODE_LIVE:
        self._alt = radar_state
        return view
      self._alt = view
      return radar_state
    except Exception:
      self._fail("before_mpc")
      return radar_state

  def after_plan(self, a_plan: float, a_candidates, action_t: float, mpc_fcw: bool, standstill: bool, engaged: bool,
                 v_ego: float) -> None:
    """a_plan: the planner's min over its candidates this frame (before the re-entry ramp); a_candidates: those
    candidates, the MPC's first (then cruise, and e2e when it is one); mpc_fcw: the planner's FCW this frame."""
    self.a_base = self.a_assist = float(a_plan)
    self.fcw_base = self.fcw_assist = bool(mpc_fcw)
    if self._alt is not None and not self._failed:
      try:
        if self._mpc_altered(float(a_candidates[0]), action_t):
          self.skipped = True
        else:
          a_alt, fcw_alt, ok = self._compare(a_candidates[1:], action_t, standstill)
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
    p = self.proj
    if not (p.active or p.blocked or p.reason or self._ep is not None or self.frame % HEARTBEAT_FRAMES == 1):
      return
    msg = messaging.new_message('customReserved13')
    msg.valid = bool(valid)
    m = msg.customReserved13
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
    m.skipped = bool(self.skipped)
    m.aCamera = float(p.a_cam)
    m.camera = bool(p.cam)
    pm.send('customReserved13', msg)

  # ---- internals ----
  def _time(self, mono_ns) -> float:
    """radarState's own time (logMonoTime) when the planner has it: a plannerd stall then never squeezes the lead's
    speed change over the stall into one 50 ms step. Without it (tools, tests): the planner's frame clock."""
    try:
      ts = float(mono_ns) * 1e-9 if mono_ns is not None else 0.0
    except (TypeError, ValueError):
      ts = 0.0
    if not (math.isfinite(ts) and ts > 0.0):
      return self.t
    if self._ts is not None and ts < self._ts:
      ts = self._ts   # never backwards
    self._ts = ts
    return ts

  def _mpc_altered(self, a_mpc_used: float, action_t: float) -> bool:
    """Did something change the MPC's candidate after its solve (close follow's live blend, once merged)? The planner
    computes it exactly like this from the MPC's solution, so without such a layer the two are equal bit for bit."""
    m = self._mpc_ref
    t_idx = ModelConstants.T_IDXS[:CONTROL_N]
    own = get_accel_from_plan(np.interp(t_idx, T_IDXS, m.v_solution), np.interp(t_idx, T_IDXS, m.a_solution), t_idx,
                              action_t=action_t)
    return not abs(float(own) - a_mpc_used) <= 1e-6

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
    with cloudlog.ctx(leadbrake="second mpc"):   # its "Long mpc reset" warning is not the planner's own MPC failing
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
        self._ep = {'episode': self.episodes, 't0': self.t, 'track': p.track_id, 'v_ego': v_ego, 'd_rel': p.d_rel,
                    'reasons': 0, 'a_k_min': p.a_lead_k, 'a_proj_min': p.a_lead, 'tau_min': p.tau, 'delta_min': 0.0,
                    'fcw_assist': False, 'fcw_base': False, 'engaged': False, 'camera': False, 'skipped': 0,
                    'frames': 0}
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
      e['camera'] |= bool(p.cam)
      e['skipped'] += int(self.skipped)
    elif self._ep is not None and self.t - self._ep['t1'] > EPISODE_GAP:
      e, self._ep = self._ep, None
      self._queue(e)
    self._flush()

  def _queue(self, e: dict) -> None:
    """An episode that ended: its line waits while the last line is under LOG_MIN_GAP old, and every episode that ends
    meanwhile joins it - one line for all of them, with the details of the one whose plan differed most, and how many
    it stands for. None is dropped until the LOG_MAX-th line; after that they are counted (customReserved13.episodes)."""
    if self._logs >= LOG_MAX:
      self.unlogged += 1
      return
    e = dict(e, merged=1, engaged_any=e['engaged'], fcw_assist_any=e['fcw_assist'])
    q = self._pending
    if q is not None:
      worst = e if e['delta_min'] <= q['delta_min'] else q
      e = dict(worst, episode=e['episode'], merged=q['merged'] + 1, engaged_any=q['engaged_any'] or e['engaged'],
               fcw_assist_any=q['fcw_assist_any'] or e['fcw_assist'])
    self._pending = e

  def _flush(self) -> None:
    e = self._pending
    if e is None or self.t - self._last_log_t < LOG_MIN_GAP:
      return
    self._pending = None
    self._logs += 1
    self._last_log_t = self.t
    cloudlog.event(LOG_TAG, error=True, mode=self.mode, episode=e['episode'], merged=e['merged'],
                   engaged_any=e['engaged_any'], fcw_assist_any=e['fcw_assist_any'], last_line=self._logs >= LOG_MAX,
                   t0=round(e['t0'], 2), duration=round(e['t1'] - e['t0'], 2), frames=e['frames'], track=e['track'],
                   v_ego=round(e['v_ego'], 2), d_rel=round(e['d_rel'], 1), reasons=e['reasons'],
                   a_k_min=round(e['a_k_min'], 2), a_proj_min=round(e['a_proj_min'], 2), tau_min=round(e['tau_min'], 3),
                   delta_min=round(e['delta_min'], 3), fcw_assist=e['fcw_assist'], fcw_base=e['fcw_base'],
                   engaged=e['engaged'], camera=e['camera'], skipped=e['skipped'])

  def _fail(self, where: str) -> None:
    # never take the planner down: the assist turns itself off for the rest of the drive, radarState passes through
    self._failed = True
    self._alt = None
    cloudlog.exception(f"{LOG_TAG}: {where} failed, off for this drive")


def _model_lead(model):
  """modelV2's leadsV3[0] (the camera's nearest lead), or None."""
  try:
    leads = model.leadsV3
    return leads[0] if len(leads) else None
  except Exception:
    return None


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
