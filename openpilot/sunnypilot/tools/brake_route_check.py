#!/usr/bin/env python3
"""
FORK(HONDA_ACCORD_9G_AU): did a drive pass the brake pump rule's and the brake law's proof plan?

    python openpilot/sunnypilot/tools/brake_route_check.py ROUTE [ROUTE ...] [--baseline ROUTE ...]
                                                          [--rule v5|v6] [--rlog] [--json OUT.json]

ROUTE is a route folder as the owner keeps them (S:/OP/sunny_logs/<dongle>_<route>/ with parquet/, read with pyarrow when
it is installed, or raw/*rlog*, read through LogReader, one process per segment), or a single rlog file. The ROUTEs are
one arm of the A/B; --baseline ROUTEs are the other arm, and the comparative abort criteria are judged against them.

What it prints, per route and pooled over the arm, is the proof plan of the pump study (pump2 A_synth section 4; the
car doc, docs/fork/CAR-HONDA-ACCORD-9G-AU.md 7.2) and the acceptance metrics of the brake law (learnaudit A_synth
section 3, B4), then a verdict on every abort criterion a log can judge. Section numbers follow the study:

  1. the rule ran as designed: the rule the route ran (CarParamsSP flag 16, ELESYS_PUMP_V6; else the route's own
     pump= tag; else --rule) replayed on the logged 0x1FA commands must match the logged pump bit on >= 99.5% of
     braking frames, a mismatch within one frame of a burst edge counting as a match (the log's timestamps jitter
     against the controller's frame clock by about that much; the raw match is printed too); pump time and starts
     (per braking minute: ~17 today, ~14 under C1); standstill bursts per stop (C1: at most one - a hold build on a
     stop reached below 100 delivered counts, or the delivery of a rise of more than 15 counts over what was
     delivered, the soft stop's rise to the hold); the longest moving pump-off at >= 100 counts (abort above 6.1 s).
     Both rules are also replayed on the same commands, so one drive gives the today-vs-C1 table of A_synth
     section 3 (open loop: only the difference between the rules means anything).
  2. steady-command gain: decel beyond coasting per 100 counts in the bands 15-60 / 60-100 / 100-150 / 150-200 / 200+,
     on frames whose command (0.3 s earlier) held within +-3 counts for 1 s, at v >= 3 m/s, grade-corrected.
  3. bleed: achieved minus commanded decel against time since the last pump run, 0-1 / 1-3 / 3-6 / 6-12 / >12 s.
  4. rise response: decel change 0.3-0.7 s after a rise larger than the deadband, per 100 counts (~ -1.0), and
     over-target bites (more than 0.5 m/s^2 beyond the command within 1 s of a pump start).
  5. stops. An ARRIVAL is any standstill reached from above 3 m/s with openpilot engaged at the start of its last 6 s
     (or where it last crossed 5 m/s); a driver brake press in those 6 s makes it a take-over, counted per 100
     arrivals (a press cancels openpilot, so those approaches end disengaged - that is why they are counted from the
     start of the window). The CLEAN stops (engaged and braking through the last 0.5-1 s, no pedal in the 6 s) give:
     radar distance to a stopped lead 1 s after; final-approach tracking error (pump2 ss/approach.py: from where the
     braked approach crossed 2.5 m/s to the stop, at v >= 0.3, aEgo + g sin(pitch) - commanded, pitch from
     carControl.orientationNED; the median over stops, against V5's -0.32 m/s^2, and the seconds it covers); settle
     jerk (the largest |d aEgo/dt| of 0.1 s means, 0.5 s before to 1 s after).
  6. creep: every openpilot hold, from the stop (engaged, >= 100 counts, the planner asking for no launch, the wheels
     at zero) for as long as those hold - WHATEVER the wheels do after it, so a hold that rolls stays one hold - of
     5 s or more: XMISSION_SPEED, vEgo and WHEELS_MOVING all 0 from 0.5 s in to its end (else MOVED: the abort),
     radar distance to a stopped lead (first 2 s against last 2 s) within 0.1 m and camera displacement (after the
     first 4 s, where the estimator settles) within 0.2 m (else SUSPECT, to look at: today's 10f holds, where nothing
     moved, already read 0.21-0.27 m on the radar). Each hold also shows the pressure DELIVERED (pump2's plant model
     on the logged pump bit: a rise arrives only while the motor runs), which is what holds the car, not the command.
  7. the brake-gain learner (hondadyn brakec; baseline 0.99-1.03): each drive's change (last - first) and its level.
     The arms of an A/B share one stored gain, so the verdict is on the per-drive change; the level is printed.
  8. the VSA: 0x1A4 COMPUTER_BRAKING on >= 99% of brake frames, 0x1B0 brake-error bits 0, the pump motor's ripple on
     0x1A4 USER_BRAKE starting within 0.2 s (median) of the request.
  B4. the brake law: openpilot's longitudinal integrator (controlsState.uiAccelCmd) while braking, per speed band
     (today -0.25..+0.28; accept within +-0.05), the lag-compensated brake RMS (achieved against the law's command
     through a 0.3 s lag, steady PID braking; it should fall against the other arm), the stop (decel ~0.97 m/s^2 and
     ~174 counts when the wheels stop), FCW/AEB events, VSA errors and driver brake take-overs.

GRADE. Accelerations are net of gravity: aEgo + g * (-vD / vH) from the GPS Doppler velocity, a 2 s rolling median
(pump2: r = 0.88 against the accelerometer's forward specific force, which the report prints for each route as a
cross-check). Without GPS the grade-corrected metrics read '-'. The GPS grade is blank below 2 m/s, so the final
approach of a stop uses the pitch instead, as pump2 did.

WHAT RAN. The pump rule: CarParamsSP flag 16, else the route's pump= tag, else --rule (a --rule that disagrees with
the log is ignored, with a warning). The brake law: the route's blaw= tag (hondadyn/hondashadow lines, which say
what ran), else CarParamsSP flag 32. Flag 32 is the SETTING: without gas law v2 the law does not run, the lines say
blaw=v1, and the report says 'v1 (v2 requested by flag 32, not run)'.

What a log cannot judge -- a VSA/ABS lamp or a new DTC on the post-drive scan -- is listed as a manual check. This
script only reads.
"""
import argparse
import glob
import json
import math
import os
import sys
from collections.abc import Callable

import numpy as np

G = 9.81
DT = 0.02                       # the 0x1FA timeline: 50 Hz
SENT = 128                      # src of the echo of a frame openpilot sent on bus 0
SENDCAN = 256                   # src offset this script gives frames read from sendcan (fallback when no echo)
ADDR_BRAKE, ADDR_VSA, ADDR_STANDSTILL, ADDR_ENGINE = 0x1FA, 0x1A4, 0x1B0, 0x158
WANT_SRC = {ADDR_BRAKE: (SENT,), ADDR_VSA: (0,), ADDR_STANDSTILL: (0,), ADDR_ENGINE: (0,)}
LCS = {"off": 0, "pid": 1, "stopping": 2, "starting": 3}
LOG_TAGS = ("hondadyn", "hondashadow")

# the pump rule's deadband (carcontroller.py ELESYS_PUMP_DEADBAND_BP/_V, the same in v5 and C1), its big rise
# (ELESYS_PUMP_BIG_RISE) and C1's standstill hold-build limit (ELESYS_PUMP_C1_HOLD_OK)
DEADBAND_BP, DEADBAND_V = (0., 60., 200.), (12., 6., 3.)
BIG_RISE = 15
HOLD_OK = 100
# Coasting deceleration with neither pedal nor brake, grade-corrected (learnaudit, GPS column of out/coast.pkl, 8 routes):
# used only to express the brake's own share of a deceleration, the same way in every arm.
COAST_BP = (0., 2., 3., 5., 6., 8., 10., 12., 15., 20., 25., 30.)
COAST_V = (0.0, 0.0, -0.05, -0.05, -0.25, -0.50, -0.50, -0.45, -0.37, -0.39, -0.40, -0.44)

GAIN_BANDS = ((15, 60), (60, 100), (100, 150), (150, 200), (200, 1024))
BLEED_BINS = ((0., 1.), (1., 3.), (3., 6.), (6., 12.), (12., math.inf))
SPEED_BANDS = ((1., 5.), (5., 10.), (10., 15.), (15., 20.), (20., 25.), (25., 99.))

# thresholds of the proof plan (pump2 A_synth section 4) and of B4
MATCH_MIN = 0.995
MOVING_OFF_CB100_MAX = 6.1      # s
GAIN_ABORT = 0.10               # m/s^2 per 100 counts weaker than the other arm, with >= GAIN_MIN_S of data in the band
GAIN_MIN_S = 60.0
BLEED_ABORT = 0.10              # 6-12 s bin at cb >= 60 weaker than the 0-1 s bin, with >= BLEED_MIN_STRETCHES
BLEED_MIN_STRETCHES = 20
STOP_DIST_ABORT = 0.5           # m shorter median than the other arm
STOP_DIST_MIN = 2.0             # m, any stop
LEARNER_ABORT = 0.04            # brake gain above the other arm's mean
CREEP_RADAR_MAX = 0.1           # m
HOLD_V_EPS = 0.01               # m/s: vEgo at a standstill, filter residue included
HOLD_SETTLE = 25                # frames (0.5 s) after the stop before motion counts: the stop itself settling
HOLD_MIN_S = 5.0
STOP_WINDOW = 300               # frames (6 s): an arrival's window
APPROACH_V = 2.5                # m/s: the final approach starts where the braked approach crosses this (pump2)
CREEP_CAMERA_MAX = 0.2          # m
COMP_BRAKING_MIN = 0.99
RIPPLE_ONSET_MAX = 0.2          # s, median
INTEGRATOR_OK = 0.05            # m/s^2, B4: every speed band's braking integrator within this
BITE = 0.5                      # m/s^2 beyond the command within 1 s of a pump start (section 4)


# --- small helpers ---------------------------------------------------------------------------------------------------

def sig(x: np.ndarray, start_bit: int, length: int) -> np.ndarray:
  """A big-endian (Motorola) DBC signal out of frames packed as the first 8 bytes, big-endian, into uint64."""
  p = (start_bit // 8) * 8 + (7 - start_bit % 8)
  sh = 64 - p - length
  return ((np.asarray(x, dtype=np.uint64) >> np.uint64(sh)) & np.uint64((1 << length) - 1)).astype(np.int64)


def pack8(dat: bytes) -> int:
  return int.from_bytes(bytes(dat)[:8].ljust(8, b"\0"), "big")


def prev(ts: np.ndarray, vals: np.ndarray, t: np.ndarray, max_age: float = math.inf) -> np.ndarray:
  """vals at the last ts <= t (nan before the first, or when older than max_age seconds)."""
  out = np.full(len(t), np.nan)
  if ts is None or len(ts) == 0:
    return out
  i = np.searchsorted(ts, t, side="right") - 1
  ok = i >= 0
  out[ok] = np.asarray(vals, dtype=float)[i[ok]]
  if math.isfinite(max_age):
    age = np.full(len(t), np.inf)
    age[ok] = t[ok] - ts[i[ok]]
    out[age > max_age] = np.nan
  return out


def runs(mask) -> tuple[np.ndarray, np.ndarray]:
  m = np.asarray(mask, bool).astype(np.int8)
  e = np.diff(np.r_[0, m, 0])
  return np.flatnonzero(e == 1), np.flatnonzero(e == -1)


def rolling(x: np.ndarray, n: int, fn: Callable, min_periods: int | None = None, center: bool = False) -> np.ndarray:
  """A rolling window statistic without pandas: fn over x[i-n+1 .. i] (or centered), nan-aware via min_periods."""
  x = np.asarray(x, dtype=float)
  out = np.full(len(x), np.nan)
  if len(x) == 0:
    return out
  mp = n if min_periods is None else min_periods
  pad = np.r_[np.full(n - 1, np.nan), x]
  w = np.lib.stride_tricks.sliding_window_view(pad, n)
  ok = np.sum(np.isfinite(w), axis=1) >= mp
  with np.errstate(all="ignore"), _quiet():
    out[ok] = fn(w[ok], axis=1)
  if center:
    k = n // 2
    out = np.r_[out[k:], np.full(k, np.nan)]
  return out


class _quiet:
  def __enter__(self):
    import warnings
    self._w = warnings.catch_warnings()
    self._w.__enter__()
    warnings.simplefilter("ignore")

  def __exit__(self, *a):
    self._w.__exit__(*a)


def frame_dt(t: np.ndarray) -> np.ndarray:
  return np.clip(np.diff(t, append=t[-1] + DT) if len(t) else t, 0.0, 0.1)


def longest(mask: np.ndarray, t: np.ndarray, maxgap: float = 0.1) -> tuple[float, float]:
  """The longest stretch (s) where mask holds, a hole in t longer than maxgap breaking it; and where it starts."""
  best, best_t = 0.0, float("nan")
  s, e = runs(mask)
  for a, b in zip(s, e, strict=True):
    tt = t[a:b]
    cuts = np.flatnonzero(np.diff(tt) > maxgap) + 1
    for seg in np.split(np.arange(a, b), cuts):
      L = t[seg[-1]] - t[seg[0]] + DT
      if L > best:
        best, best_t = float(L), float(t[seg[0]])
  return best, best_t


def fnum(x, spec: str = "+.2f", dash: str = "-") -> str:
  try:
    return dash if x is None or not math.isfinite(float(x)) else format(float(x), spec)
  except (TypeError, ValueError):
    return dash


def deadband(cb):
  return np.interp(cb, DEADBAND_BP, DEADBAND_V)


# --- reading a route -------------------------------------------------------------------------------------------------

def _streams() -> dict:
  return {
    "can": {"t": [], "addr": [], "src": [], "x": []},
    "cs": {"t": [], "v": [], "a": [], "bp": [], "gp": [], "ss": [], "en": [], "aeb": [], "fcw": []},
    "cc": {"t": [], "la": [], "acc": [], "lcs": [], "pitch": [], "fcw": []},
    "ctl": {"t": [], "ui": []},
    "rad": {"t": [], "st": [], "d": [], "vl": []},
    "gps": {"t": [], "fix": [], "vn": [], "ve": [], "vd": []},
    "imu": {"t": [], "x": [], "y": [], "z": []},
    "pose": {"t": [], "vx": []},
    "logs": [],
    "meta": {},
  }


def _read_rlog(fn: str) -> dict:
  """One rlog (or qlog) file into the streams this script uses."""
  from openpilot.tools.lib.logreader import LogReader
  S = _streams()
  can, cs, cc, ctl, rad, gps, imu, pose = (S[k] for k in ("can", "cs", "cc", "ctl", "rad", "gps", "imu", "pose"))
  meta = S["meta"]
  for m in LogReader(fn):
    w = m.which()
    t = m.logMonoTime
    if w in ("can", "sendcan"):
      off = SENDCAN if w == "sendcan" else 0
      for c in getattr(m, w):
        a = c.address
        if a in WANT_SRC:
          s = c.src
          if s in WANT_SRC[a] or (off and a == ADDR_BRAKE and s == 0):
            can["t"].append(t)
            can["addr"].append(a)
            can["src"].append(s + off)
            can["x"].append(pack8(c.dat))
    elif w == "carState":
      o = m.carState
      for k, v in (("t", t), ("v", o.vEgo), ("a", o.aEgo), ("bp", o.brakePressed), ("gp", o.gasPressed),
                   ("ss", o.standstill), ("en", o.cruiseState.enabled), ("aeb", o.stockAeb), ("fcw", o.stockFcw)):
        cs[k].append(v)
    elif w == "carControl":
      o = m.carControl
      ned = list(o.orientationNED)
      cc["t"].append(t)
      cc["la"].append(o.longActive)
      cc["acc"].append(o.actuators.accel)
      cc["lcs"].append(LCS.get(str(o.actuators.longControlState), -1))
      cc["pitch"].append(ned[1] if len(ned) == 3 else math.nan)
      cc["fcw"].append(str(o.hudControl.visualAlert) == "fcw")
    elif w == "controlsState":
      ctl["t"].append(t)
      ctl["ui"].append(m.controlsState.uiAccelCmd)
    elif w == "radarState":
      ld = m.radarState.leadOne
      for k, v in (("t", t), ("st", ld.present), ("d", ld.dRel), ("vl", ld.vLead)):   # `present` was `status`, same field
        rad[k].append(v)
    elif w == "gpsLocationExternal":
      o = m.gpsLocationExternal
      vn = list(o.vNED)
      if len(vn) == 3:
        for k, v in (("t", t), ("fix", o.hasFix), ("vn", vn[0]), ("ve", vn[1]), ("vd", vn[2])):
          gps[k].append(v)
    elif w == "accelerometer":
      v3 = list(m.accelerometer.acceleration.v)
      if len(v3) == 3:
        for k, v in zip(("t", "x", "y", "z"), (t, *v3), strict=True):
          imu[k].append(v)
    elif w in ("livePose", "deviceMotion"):   # the same service, renamed upstream; old routes read as deviceMotion
      pose["t"].append(t)
      pose["vx"].append(getattr(m, w).velocityDevice.x)
    elif w == "logMessage":
      txt = m.logMessage
      if any(tag in txt for tag in LOG_TAGS):
        S["logs"].append((t, txt))
    elif w == "initData":
      meta.setdefault("t0", t)
      meta.setdefault("commit", m.initData.gitCommit)
    elif w == "carParamsSP":
      meta.setdefault("flags_sp", int(m.carParamsSP.flags))
    elif w == "carParams":
      meta.setdefault("op_long", bool(m.carParams.openpilotLongitudinalControl))
      meta.setdefault("fingerprint", str(m.carParams.carFingerprint))
  return _to_numpy(S)


def _to_numpy(S: dict) -> dict:
  for k, cols in S.items():
    if k in ("logs", "meta"):
      continue
    for c in list(cols):
      if c == "x":
        cols[c] = np.array(cols[c], dtype=np.uint64)
      elif c in ("t", "addr", "src"):
        cols[c] = np.array(cols[c], dtype=np.int64)
      else:
        cols[c] = np.array(cols[c], dtype=float)
  return S


def _merge(parts: list[dict]) -> dict:
  S = _streams()
  for k in S:
    if k == "logs":
      S[k] = [x for p in parts for x in p["logs"]]
    elif k == "meta":
      for p in parts:
        for mk, mv in p["meta"].items():
          S["meta"].setdefault(mk, mv)
    else:
      for c in S[k]:
        arrs = [p[k][c] for p in parts if len(p[k][c])]
        S[k][c] = np.concatenate(arrs) if arrs else np.array([], dtype=np.uint64 if c == "x" else float)
      if len(S[k]["t"]):
        o = np.argsort(S[k]["t"], kind="stable")
        for c in S[k]:
          S[k][c] = S[k][c][o]
  S["logs"].sort(key=lambda x: x[0])
  return S


def rlog_files(path: str) -> tuple[list[str], list[str]]:
  """The route's log files in segment order (an rlog per segment, a qlog where there is none) and the qlog-only ones."""
  if os.path.isfile(path):
    return [path], []
  d = os.path.join(path, "raw") if os.path.isdir(os.path.join(path, "raw")) else path

  def seg(p):
    head = os.path.basename(p).split("--")[0]
    return int(head) if head.isdigit() else -1
  r = {seg(p): p for p in glob.glob(os.path.join(d, "*rlog*"))}
  q = {seg(p): p for p in glob.glob(os.path.join(d, "*qlog*"))}
  files, qonly = [], []
  for s in sorted(set(r) | set(q)):
    if s in r:
      files.append(r[s])
    else:
      files.append(q[s])
      qonly.append(q[s])
  return files, qonly


def read_rlogs(files: list[str], workers: int) -> dict:
  if workers > 1 and len(files) > 1:
    import multiprocessing as mp
    with mp.get_context("fork").Pool(min(workers, len(files))) as pool:
      parts = pool.map(_read_rlog, files)
  else:
    parts = [_read_rlog(f) for f in files]
  return _merge(parts)


def _pyarrow():
  """(pyarrow, pyarrow.parquet, pyarrow.compute), or None: the repo's own venv has no pyarrow, and then rlogs are read."""
  try:
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    return pa, pq, pc
  except ImportError:
    return None


def read_parquet(pdir: str) -> dict:
  """The owner's parquet export (comma_logs.py): one file per service, flattened columns."""
  pa, pq, pc = _pyarrow()  # type: ignore[misc]
  S = _streams()

  def table(name, cols):
    p = os.path.join(pdir, f"{name}.parquet")
    if not os.path.isfile(p):
      return None
    have = set(pq.read_schema(p).names)
    return pq.read_table(p, columns=[c for c in cols if c in have])

  def col(tb, name, default=math.nan):
    if tb is None:
      return np.array([])
    if name not in tb.column_names:
      return np.full(tb.num_rows, default)
    return np.array(tb.column(name).to_pylist(), dtype=object)

  def as_float(a):
    return np.array([math.nan if x is None else float(x) for x in a], dtype=float)

  # CAN: the frames this script uses, out of the list column, with pyarrow compute (no per-row Python)
  for name, off in (("can", 0), ("sendcan", SENDCAN)):
    p = os.path.join(pdir, f"{name}.parquet")
    if not os.path.isfile(p):
      continue
    tb = pq.read_table(p, columns=["_logMonoTime", "value"])
    v = tb.column("value").combine_chunks()
    par = pc.list_parent_indices(v)
    fl = pc.list_flatten(v)
    addr = fl.field("address")
    keep = pc.is_in(addr, value_set=pa.array(list(WANT_SRC), type=addr.type))
    t = pc.take(tb.column("_logMonoTime").combine_chunks(), pc.filter(par, keep)).to_numpy()
    a = pc.filter(addr, keep).to_numpy()
    s = pc.filter(fl.field("src"), keep).to_numpy()
    dat = pc.filter(fl.field("dat"), keep).to_pylist()
    x = np.array([pack8(bytes.fromhex(d) if isinstance(d, str) else bytes(d)) for d in dat], dtype=np.uint64)
    want = np.array([si in WANT_SRC[ai] or (off and ai == ADDR_BRAKE and si == 0) for ai, si in zip(a, s, strict=True)],
                    dtype=bool)
    for k, arr in (("t", t[want]), ("addr", a[want]), ("src", s[want] + off), ("x", x[want])):
      S["can"][k].append(arr)
  for k in S["can"]:
    S["can"][k] = list(np.concatenate(S["can"][k])) if S["can"][k] else []

  tb = table("carState", ["_logMonoTime", "vEgo", "aEgo", "brakePressed", "gasPressed", "standstill", "cruiseState.enabled",
                          "stockAeb", "stockFcw"])
  if tb is not None:
    for k, c in (("t", "_logMonoTime"), ("v", "vEgo"), ("a", "aEgo"), ("bp", "brakePressed"), ("gp", "gasPressed"),
                 ("ss", "standstill"), ("en", "cruiseState.enabled"), ("aeb", "stockAeb"), ("fcw", "stockFcw")):
      S["cs"][k] = list(col(tb, c, False if k not in ("t", "v", "a") else math.nan))
  tb = table("carControl", ["_logMonoTime", "longActive", "actuators.accel", "actuators.longControlState", "orientationNED",
                            "hudControl.visualAlert"])
  if tb is not None:
    ned = col(tb, "orientationNED", None)
    S["cc"]["t"] = list(col(tb, "_logMonoTime"))
    S["cc"]["la"] = list(col(tb, "longActive", False))
    S["cc"]["acc"] = list(as_float(col(tb, "actuators.accel")))
    S["cc"]["lcs"] = [LCS.get(str(x), -1) for x in col(tb, "actuators.longControlState", "")]
    S["cc"]["pitch"] = [float(n[1]) if n is not None and len(n) == 3 and n[1] is not None else math.nan for n in ned]
    S["cc"]["fcw"] = [str(x) == "fcw" for x in col(tb, "hudControl.visualAlert", "")]
  tb = table("controlsState", ["_logMonoTime", "uiAccelCmd"])
  if tb is not None:
    S["ctl"]["t"], S["ctl"]["ui"] = list(col(tb, "_logMonoTime")), list(as_float(col(tb, "uiAccelCmd")))
  tb = table("radarState", ["_logMonoTime", "leadOne.status", "leadOne.dRel", "leadOne.vLead"])
  if tb is not None:
    for k, c in (("t", "_logMonoTime"), ("st", "leadOne.status"), ("d", "leadOne.dRel"), ("vl", "leadOne.vLead")):
      S["rad"][k] = list(col(tb, c))
  tb = table("gpsLocationExternal", ["_logMonoTime", "hasFix", "vNED"])
  if tb is not None:
    vned = col(tb, "vNED", None)
    ok = np.array([n is not None and len(n) == 3 for n in vned], dtype=bool)
    V = np.array([n for n, k in zip(vned, ok, strict=True) if k], dtype=float).reshape(-1, 3)
    S["gps"]["t"], S["gps"]["fix"] = list(col(tb, "_logMonoTime")[ok]), list(col(tb, "hasFix", False)[ok])
    S["gps"]["vn"], S["gps"]["ve"], S["gps"]["vd"] = list(V[:, 0]), list(V[:, 1]), list(V[:, 2])
  tb = table("accelerometer", ["_logMonoTime", "acceleration.v"])
  if tb is not None:
    av = col(tb, "acceleration.v", None)
    ok = np.array([n is not None and len(n) == 3 for n in av], dtype=bool)
    A = np.array([n for n, k in zip(av, ok, strict=True) if k], dtype=float).reshape(-1, 3)
    S["imu"]["t"] = list(col(tb, "_logMonoTime")[ok])
    S["imu"]["x"], S["imu"]["y"], S["imu"]["z"] = list(A[:, 0]), list(A[:, 1]), list(A[:, 2])
  tb = table("livePose", ["_logMonoTime", "velocityDevice.x"])
  if tb is None:
    tb = table("deviceMotion", ["_logMonoTime", "velocityDevice.x"])
  if tb is not None:
    S["pose"]["t"], S["pose"]["vx"] = list(col(tb, "_logMonoTime")), list(as_float(col(tb, "velocityDevice.x")))
  tb = table("logMessage", ["_logMonoTime", "value"])
  if tb is not None:
    for t, txt in zip(col(tb, "_logMonoTime"), col(tb, "value", ""), strict=True):
      if isinstance(txt, str) and any(tag in txt for tag in LOG_TAGS):
        S["logs"].append((int(t), txt))
  tb = table("initData", ["_logMonoTime", "gitCommit"])
  if tb is not None and tb.num_rows:
    S["meta"]["t0"] = int(min(col(tb, "_logMonoTime")))
    S["meta"]["commit"] = str(col(tb, "gitCommit", "")[0])
  tb = table("carParams", ["_logMonoTime", "openpilotLongitudinalControl", "carFingerprint"])
  if tb is not None and tb.num_rows:
    S["meta"]["op_long"] = bool(col(tb, "openpilotLongitudinalControl", False)[0])
  return _to_numpy(_merge([_to_numpy(S)]))


def _flags_from_rlog(files: list[str]) -> int | None:
  """CarParamsSP.flags from the first log that has it (the owner's parquet export cannot decode CarParamsSP)."""
  from openpilot.tools.lib.logreader import LogReader
  for fn in files[:2]:
    try:
      for m in LogReader(fn):
        if m.which() == "carParamsSP":
          return int(m.carParamsSP.flags)
    except Exception:
      continue
  return None


def load_route(path: str, force_rlog: bool = False, workers: int = 0) -> dict:
  """Streams for one route, from parquet when pyarrow is there (and not --rlog), else from the rlogs."""
  workers = workers or max(1, (os.cpu_count() or 2) - 1)
  files, qonly = rlog_files(path)
  pdir = os.path.join(path, "parquet")
  notes = []
  if not force_rlog and os.path.isdir(pdir) and os.path.isfile(os.path.join(pdir, "can.parquet")) and _pyarrow():
    S = read_parquet(pdir)
    S["meta"]["source"] = "parquet"
    if "flags_sp" not in S["meta"] and files:
      fl = _flags_from_rlog(files)
      if fl is not None:
        S["meta"]["flags_sp"] = fl
  else:
    if not files:
      raise FileNotFoundError(f"{path}: no parquet/can.parquet (or no pyarrow) and no rlogs")
    S = read_rlogs(files, workers)
    S["meta"]["source"] = f"{len(files)} log file(s)"
    if qonly:
      notes.append(f"{len(qonly)} segment(s) have only a qlog: their CAN is decimated, read every number with that in mind")
  S["meta"]["notes"] = notes
  return S


# --- the 50 Hz frame table -------------------------------------------------------------------------------------------

def build_frames(S: dict) -> dict:
  """Every signal at each 0x1FA frame openpilot sent (the echo on bus 0, or sendcan without it)."""
  can, meta = S["can"], S["meta"]
  t0 = meta.get("t0")
  if t0 is None:
    starts = [S[k]["t"][0] for k in ("can", "cs", "cc") if len(S[k]["t"])]
    t0 = min(starts) if starts else 0
  sec = lambda tt: (np.asarray(tt, dtype=np.int64) - int(t0)) / 1e9  # noqa: E731

  def frames_of(addr, srcs):
    m = (can["addr"] == addr) & np.isin(can["src"], srcs)
    tt, xx = can["t"][m], can["x"][m]
    o = np.argsort(tt, kind="stable")
    return sec(tt[o]), xx[o]

  t, x = frames_of(ADDR_BRAKE, [SENT])
  src = "echo"
  if len(t) < 100:
    t, x = frames_of(ADDR_BRAKE, [SENDCAN])
    src = "sendcan"
  F = {"t": t, "cb": sig(x, 7, 10).astype(float), "pump": sig(x, 8, 1).astype(bool), "brake_src": src}
  n = len(t)

  tv, xv = frames_of(ADDR_VSA, [0])
  F["user_brake"] = prev(tv, sig(xv, 7, 16), t)
  F["comp_braking"] = prev(tv, sig(xv, 23, 1), t)
  ts_, xs_ = frames_of(ADDR_STANDSTILL, [0])
  F["wheels_moving"] = prev(ts_, sig(xs_, 12, 1), t)
  F["berr"] = prev(ts_, sig(xs_, 11, 1) | sig(xs_, 9, 1), t)
  te, xe = frames_of(ADDR_ENGINE, [0])
  F["xmission"] = prev(te, sig(xe, 7, 16) * 0.01 / 3.6, t)
  # the raw USER_BRAKE samples (for the ripple detector, which needs every frame, not one per 0x1FA)
  F["_ub_t"], F["_ub"] = tv, sig(xv, 7, 16)

  cs = S["cs"]
  ct = sec(cs["t"])
  for k in ("v", "a", "bp", "gp", "ss", "en", "aeb", "fcw"):
    F[k] = prev(ct, cs[k], t, max_age=0.5)
  cc = S["cc"]
  cct = sec(cc["t"])
  F["la"] = prev(cct, cc["la"], t, max_age=0.5) == 1
  F["acc"] = prev(cct, cc["acc"], t, max_age=0.5)
  F["lcs"] = prev(cct, cc["lcs"], t, max_age=0.5)
  F["op_fcw"] = prev(cct, cc["fcw"], t, max_age=0.5) == 1
  F["pitch"] = prev(cct, cc["pitch"], t, max_age=0.5)
  F["ui"] = prev(sec(S["ctl"]["t"]), S["ctl"]["ui"], t, max_age=0.5)
  r = S["rad"]
  rt = sec(r["t"])
  F["lead"] = prev(rt, r["st"], t, max_age=0.5) == 1
  F["dRel"] = prev(rt, r["d"], t, max_age=0.5)
  F["vLead"] = prev(rt, r["vl"], t, max_age=0.5)
  F["cam_vx"] = prev(sec(S["pose"]["t"]), S["pose"]["vx"], t, max_age=0.5)

  # grade: the GPS Doppler climb angle, a 2 s rolling median (pump2 lib.load)
  g = S["gps"]
  Gr = np.full(n, np.nan)
  if len(g["t"]) > 10 and n:
    gt = sec(g["t"])
    fix = g["fix"] == 1
    gt, vn, ve, vd = gt[fix], g["vn"][fix], g["ve"][fix], g["vd"][fix]
    if len(gt) > 10:
      vh = np.interp(t, gt, np.hypot(vn, ve))
      vdd = np.interp(t, gt, vd)
      age = t - prev(gt, gt, t)
      raw = -G * vdd / np.maximum(vh, 1.0)
      raw = np.where((age < 1.0) & (vh > 2.0), raw, np.nan)
      Gr = rolling(raw, 100, np.nanmedian, min_periods=25, center=True)
  F["grade"] = Gr
  F["ac"] = F["a"] + Gr

  # the accelerometer's forward specific force, aligned to ac (the grade cross-check; pump2 lib.load)
  F["ff"] = np.full(n, np.nan)
  im = S["imu"]
  if len(im["t"]) > 1000 and n:
    it = sec(im["t"])
    A = np.stack([im["x"], im["y"], im["z"]], 1)
    Af = np.stack([prev(it, A[:, i], t, max_age=0.1) for i in range(3)], 1)
    mv = (F["v"] > 3) & np.isfinite(Af).all(1)
    if mv.sum() > 500:
      gm = np.nanmean(Af[mv], 0)
      gh = gm / np.linalg.norm(gm)
      e = np.array([0, 0, -1.0])
      u = e - (e @ gh) * gh
      u /= np.linalg.norm(u)
      ff = rolling(Af @ u, 5, np.nanmean, min_periods=3, center=True)
      m = (F["v"] > 8) & np.isfinite(F["ac"]) & np.isfinite(ff)
      off = np.nanmedian(ff[m] - F["ac"][m]) if m.sum() > 500 else 0.0
      F["ff"] = ff - off

  # time since the last pump frame inside the same application (inf when none yet)
  tsp = np.full(n, np.inf)
  last = -1
  pump, cb = F["pump"], F["cb"]
  for i in range(n):
    if cb[i] <= 0:
      last = -1
    if pump[i]:
      last = i
    if last >= 0:
      tsp[i] = t[i] - t[last]
  F["tsp"] = tsp
  F["dt"] = frame_dt(t)
  return F


# --- the pump rules --------------------------------------------------------------------------------------------------

def _c1_reference(apply_brake, v_ego, level, trig, last_pump_ts, ts):
  """pump2 A_synth section 3 pseudo-code, for a tree whose carcontroller.py does not have the rule, with the one
  deviation the controller makes (a standstill rise of more than BIG_RISE over the delivered level is delivered)."""
  if apply_brake <= 0:
    return False, 0, trig, last_pump_ts
  if v_ego >= 2.5 and apply_brake > 200:
    return True, max(level, apply_brake), apply_brake, ts
  db = float(np.interp(apply_brake, DEADBAND_BP, DEADBAND_V))
  if ts - last_pump_ts < 0.5:
    if apply_brake >= trig + max(2.0, 0.5 * db):
      last_pump_ts, trig = ts, apply_brake
  else:
    still = v_ego < 0.15
    gap_ok = level == 0 or ts - last_pump_ts >= 1.5 or apply_brake > level + BIG_RISE
    hold_rise = apply_brake > level + BIG_RISE   # batch 3 fix round 1: the soft stop's rise to the hold
    if (not still or level < HOLD_OK or hold_rise) and apply_brake > level + db and gap_ok:
      last_pump_ts, trig = ts, apply_brake
    elif not still and apply_brake >= 100 and ts - last_pump_ts >= 6.0:
      last_pump_ts, trig = ts, apply_brake
  if apply_brake < level - 6:
    level = apply_brake
  on = ts - last_pump_ts < 0.5
  if on:
    level = max(level, apply_brake)
  return on, level, trig, last_pump_ts


def rule_functions() -> dict:
  """name -> (function, where it came from). v5 and C1 are the controller's own functions where the tree has them."""
  out = {}
  try:
    from opendbc.car.honda import carcontroller as ccm
    out["v5"] = (ccm.brake_pump_hysteresis_elesys, "carcontroller.brake_pump_hysteresis_elesys")
    if hasattr(ccm, "brake_pump_c1_elesys"):
      out["v6"] = (ccm.brake_pump_c1_elesys, "carcontroller.brake_pump_c1_elesys")
  except Exception:
    pass
  out.setdefault("v6", (_c1_reference, "this script's transcription of pump2 A_synth section 3"))
  return out


def replay_rule(name: str, cb: np.ndarray, v: np.ndarray, t: np.ndarray, fns: dict | None = None) -> np.ndarray:
  fns = fns or rule_functions()
  if name not in fns:
    raise KeyError(f"no pump rule {name!r} in this tree")
  fn = fns[name][0]
  on = np.zeros(len(cb), dtype=bool)
  ab = cb.astype(int)
  if name == "v5":
    anchor, last = 0, 0.0
    for i in range(len(ab)):
      on[i], anchor, last = fn(int(ab[i]), float(v[i]), anchor, last, float(t[i]))
  else:
    level, trig, last = 0, 0, 0.0
    for i in range(len(ab)):
      on[i], level, trig, last = fn(int(ab[i]), float(v[i]), level, trig, last, float(t[i]))
  return on & (ab > 0)


def delivered(cb: np.ndarray, on: np.ndarray, t: np.ndarray, tail: float = 0.2, lag: float = 0.12) -> np.ndarray:
  """pump2's plant model: a RISE is delivered only while the motor runs (request on for more than `lag`, or within
  `tail` after it clears); releases always; a steady command needs nothing."""
  D = np.zeros(len(cb))
  d, on_since, off_at, prev_on = 0.0, -1e9, -1e9, False
  for i in range(len(cb)):
    c = cb[i]
    if on[i] and not prev_on:
      on_since = t[i]
    if not on[i] and prev_on:
      off_at = t[i]
    prev_on = bool(on[i])
    motor = (on[i] and t[i] - on_since >= lag) or ((not on[i]) and t[i] - off_at < tail)
    d = 0.0 if c <= 0 else (c if motor else min(d, c))
    D[i] = d
  return D


def pump_metrics(F: dict, on: np.ndarray) -> dict:
  """pump2 syn/metrics.route_metrics: engaged braking only."""
  t, cb = F["t"], F["cb"]
  v = np.nan_to_num(F["v"])
  dt, act = F["dt"], F["la"]
  brk = (cb > 0) & act
  mv = v >= 0.15
  onA = on & act
  st = np.flatnonzero(np.diff(np.r_[0, onA.astype(np.int8)]) == 1)
  r = {"eng_s": float(dt[act].sum()), "brk_s": float(dt[brk].sum()), "brk_mv_s": float(dt[brk & mv].sum()),
       "pump_s": float(dt[onA].sum()), "starts": int(len(st)), "starts_still": int((~mv[st]).sum()),
       "pump_still_s": float(dt[onA & ~mv].sum())}
  r["starts_per_brk_min"] = r["starts"] / (r["brk_s"] / 60.0) if r["brk_s"] > 0 else math.nan
  r["off_mv_cb100"], r["off_mv_cb100_t"] = longest(brk & mv & (cb >= 100) & ~on, t)
  r["off_mv_brk"], r["off_mv_brk_t"] = longest(brk & mv & ~on, t)
  es, ee = runs(cb > 0)
  ev = [(a, b) for a, b in zip(es, ee, strict=True) if act[a:b].any()]
  r["applications"] = len(ev)
  r["applications_unpumped"] = sum(1 for a, b in ev if not on[a:b].any())
  D = delivered(cb, on, t)
  sf = np.clip(cb - D, 0, None)
  m = brk & mv
  r["undelivered10_pct"] = 100.0 * float(dt[m & (sf >= 10)].sum()) / max(float(dt[m].sum()), 1e-9)
  r["undelivered10_long"], _ = longest(m & (sf >= 10), t)
  return r


def standstill_bursts(F: dict, on: np.ndarray) -> list[dict]:
  """Per openpilot-held stop (engaged, braking, v < 0.15 for >= 1 s): the pump starts while stopped, the command the
  stop was reached at, the pressure delivered then (pump2's model) and the highest command of the hold. C1's design:
  at most one burst, and only to build a hold (reached below HOLD_OK delivered) or to deliver a rise of more than
  BIG_RISE over what was delivered (the soft stop's rise from its ~125 cap to the hold)."""
  t, cb, act = F["t"], F["cb"], F["la"]
  v = np.nan_to_num(F["v"])
  D = delivered(cb, on, t)
  still = (cb > 0) & act & (v < 0.15)
  st = np.flatnonzero(np.diff(np.r_[0, on.astype(np.int8)]) == 1)
  out = []
  for a, b in zip(*runs(still), strict=True):
    if t[b - 1] - t[a] < 1.0:
      continue
    n = int(((st >= a) & (st < b)).sum())
    lvl, top = float(D[a]), float(cb[a:b].max())
    allowed = 1 if (lvl < HOLD_OK or top > lvl + BIG_RISE) else 0
    out.append({"t": float(t[a]), "hold_s": float(t[b - 1] - t[a]), "cb_reached": float(cb[a]), "delivered_reached": lvl,
                "cb_max": top, "cb_hold": float(np.median(cb[a:b])), "delivered_hold": float(np.median(D[a:b])),
                "bursts": n, "c1_ok": n <= allowed})
  return out


# --- section 2-8 and B4 ----------------------------------------------------------------------------------------------

def _ok(F: dict) -> np.ndarray:
  return F["la"] & (F["bp"] != 1) & (F["gp"] != 1) & (np.nan_to_num(F["berr"]) == 0)


def coast(v):
  return np.interp(v, COAST_BP, COAST_V)


def steady_gain(F: dict, lag: int = 15) -> dict:
  """Decel beyond coasting per 100 counts, per command band, on frames whose command (`lag` frames earlier) held within
  +-3 counts for 1 s, v >= 3 m/s, grade-corrected; and the overall least-squares slope (pump2 plant.py: ~ -0.79)."""
  cb = F["cb"]
  n = len(cb)
  cbl = np.r_[np.full(lag, np.nan), cb[:-lag]] if n > lag else np.full(n, np.nan)
  hi = rolling(cbl, 50, np.max)
  lo = rolling(cbl, 50, np.min)
  m = _ok(F) & (np.nan_to_num(F["v"]) >= 3) & (cb > 0) & (hi - lo <= 6) & (cbl >= GAIN_BANDS[0][0]) & np.isfinite(F["ac"])
  beyond = F["ac"] - coast(np.nan_to_num(F["v"]))
  out = {"bands": [], "samples": {"cbl": cbl[m], "beyond": beyond[m], "ac": F["ac"][m]}}
  for lo_, hi_ in GAIN_BANDS:
    mm = m & (cbl >= lo_) & (cbl < hi_)
    s = float(F["dt"][mm].sum())
    g = 100.0 * float(np.mean(beyond[mm])) / float(np.mean(cbl[mm])) if mm.sum() >= 25 else math.nan
    out["bands"].append({"band": f"{lo_}-{hi_ if hi_ < 1024 else ''}".rstrip("-") + ("+" if hi_ >= 1024 else ""),
                         "s": s, "per100": g, "cb": float(np.mean(cbl[mm])) if mm.any() else math.nan})
  out["slope100"] = _slope100(cbl[m], F["ac"][m])
  return out


def _slope100(x, y) -> float:
  if len(x) < 150 or np.ptp(x) < 40:
    return math.nan
  X = np.c_[np.ones(len(x)), x]
  b, *_ = np.linalg.lstsq(X, y, rcond=None)
  return float(b[1] * 100.0)


def bleed(F: dict, lag: int = 15) -> dict:
  """Achieved minus commanded (0.3 s earlier) accel, moving braking, by time since the last pump run; per bin the
  frames' mean and the number of pump-off stretches that reach it."""
  n = len(F["t"])
  acc_l = np.r_[np.full(lag, np.nan), F["acc"][:-lag]] if n > lag else np.full(n, np.nan)
  err = F["ac"] - acc_l
  m = _ok(F) & (np.nan_to_num(F["v"]) >= 3) & (F["cb"] > 0) & np.isfinite(err) & np.isfinite(F["tsp"])
  # a stretch: frames since one pump run, inside one application
  with np.errstate(invalid="ignore"):        # inf - inf between two frames with no pump yet
    run_id = np.cumsum(np.r_[1, (np.diff(F["tsp"]) < 0) | (F["cb"][1:] <= 0)])
  out = {}
  for cbmin in (0, 60):
    rows = []
    for lo_, hi_ in BLEED_BINS:
      mm = m & (F["cb"] >= cbmin) & (F["tsp"] >= lo_) & (F["tsp"] < hi_)
      rows.append({"bin": f"{lo_:g}-{hi_:g}s" if math.isfinite(hi_) else f">{lo_:g}s", "n_frames": int(mm.sum()),
                   "stretches": int(len(np.unique(run_id[mm]))), "err": float(np.mean(err[mm])) if mm.sum() >= 10 else math.nan})
    out[f"cb{cbmin}"] = rows
  out["samples"] = {"err": err[m], "tsp": F["tsp"][m], "cb": F["cb"][m], "run": run_id[m]}
  return out


def rises(F: dict) -> dict:
  """Rises larger than the deadband from a flat command (+-3 for 0.5 s), moving: decel change 0.3-0.7 s after the rise
  per 100 counts; and over-target bites, more than 0.5 m/s^2 beyond the command within 1 s of a pump start."""
  t, cb, ac, ok = F["t"], F["cb"], F["ac"], _ok(F)
  v = np.nan_to_num(F["v"])
  n = len(t)
  ev = []
  i = 25
  while i < n - 40:
    pre = cb[i - 25:i + 1]
    d = cb[i + 20] - cb[i] if i + 20 < n else 0
    if (cb[i] > 0 and pre.max() - pre.min() <= 6 and cb[i + 1] > cb[i] and d > deadband(cb[i]) and v[i] >= 4
            and ok[i - 25:i + 36].all() and np.isfinite(ac[i - 20:i + 36]).all()):
      dcb = float(np.mean(cb[i:i + 20]) - cb[i])
      da = float(np.mean(ac[i + 15:i + 36]) - np.mean(ac[i - 20:i + 1]))
      ev.append({"t": float(t[i]), "cb0": float(cb[i]), "dcb": dcb, "da": da,
                 "pumped": bool(F["pump"][i:i + 15].any()), "per100": 100.0 * da / dcb if dcb > 0 else math.nan})
      i += 50
    else:
      i += 1
  # a bite: within 1 s of a pump start the car goes more than BITE beyond the command (0.3 s earlier, 0.2 s means),
  # counted against how far beyond it already was just before the start - a steady over- or under-delivery of the law
  # (the integrator's job) is not a bite
  st = np.flatnonzero(np.diff(np.r_[0, (F["pump"] & F["la"]).astype(np.int8)]) == 1)
  acc_l = np.r_[np.full(15, np.nan), F["acc"][:-15]] if n > 15 else np.full(n, np.nan)
  e_all = rolling(ac - acc_l, 10, np.nanmean, min_periods=5, center=True)
  bites = 0
  starts_mv = 0
  for s in st:
    if v[s] < 3 or not ok[s] or s < 20:
      continue
    starts_mv += 1
    before = e_all[s - 20:s + 1]
    after = e_all[s:min(s + 50, n)]
    if np.isfinite(before).any() and np.isfinite(after).any() and np.nanmin(after) - np.nanmean(before) < -BITE:
      bites += 1
  per = [e["per100"] for e in ev if math.isfinite(e["per100"])]
  return {"events": ev, "n": len(per), "median_per100": float(np.median(per)) if per else math.nan,
          "pumped_frac": float(np.mean([e["pumped"] for e in ev])) if ev else math.nan,
          "bites": bites, "starts_moving": starts_mv}


def stops(F: dict) -> dict:
  """Arrivals at a standstill from above 3 m/s with openpilot engaged at the start of the last 6 s (or where the car
  last crossed 5 m/s in them); a brake press in those 6 s is a take-over. A press cancels openpilot longitudinal, so a
  take-over ends disengaged - which is why engagement is judged at the START of the window. Clean stops (engaged and
  braking to the stop, no pedal in the window) carry the stop metrics (learnaudit stops.py, pump2 ss/approach.py)."""
  t, cb, v = F["t"], F["cb"], np.nan_to_num(F["v"], nan=99.0)
  la = F["la"]
  ss = (v < 0.1) | (F["ss"] == 1)
  n = len(t)
  rows, arrivals, pressed, press_t = [], 0, 0, []
  for i in np.flatnonzero(ss[1:] & ~ss[:-1]) + 1:
    if i < STOP_WINDOW or i + 50 >= n:
      continue
    w = slice(i - STOP_WINDOW, i)
    if not v[i - STOP_WINDOW] > 3:
      continue
    fast = np.flatnonzero(v[w] >= 5.0)
    if not (la[i - STOP_WINDOW] or (len(fast) and la[i - STOP_WINDOW + fast[-1]])):
      continue
    arrivals += 1
    if (F["bp"][w] == 1).any():
      pressed += 1
      press_t.append(float(t[i]))
      continue
    if (F["gp"][w] == 1).any() or not (la[i - 50:i].all() and (cb[i - 25:i] > 0).all()):
      continue
    ap = approach(F, int(i))
    a_s = F["a"][i - 25:i + 50]
    jerk = np.abs(np.diff(rolling(a_s, 5, np.nanmean, min_periods=3, center=True))) / DT if len(a_s) > 6 else np.array([np.nan])
    j = i + 50
    lead_stopped = bool(F["lead"][j] and np.isfinite(F["vLead"][j]) and abs(F["vLead"][j]) < 0.5)
    dec_imu = -np.nanmean(F["ff"][i - 10:i]) if np.isfinite(F["ff"][i - 10:i]).any() else math.nan
    rows.append({"t": float(t[i]), "cb_at_stop": float(cb[i]), "cb_max_1s": float(cb[i:i + 50].max()),
                 "dist": float(F["dRel"][j]) if lead_stopped else math.nan,
                 "approach_err": ap["err"], "approach_s": ap["s"], "approach_v0": ap["v0"],
                 "settle_jerk": float(np.nanmax(jerk)) if np.isfinite(jerk).any() else math.nan,
                 "decel_at_stop": float(dec_imu)})
  return {"stops": rows, "arrivals": arrivals, "brake_pressed": pressed, "press_t": press_t}


def approach(F: dict, i: int) -> dict:
  """pump2 ss/approach.py: walk back from the stop while v < 2.5, braking, engaged, no hole in the log; only an
  approach that crossed 2.5 m/s under braking counts. Over its frames at v >= 0.3: mean(aEgo + g sin(pitch)) minus
  mean(the command). The pitch (carControl.orientationNED) because the GPS grade is blank below 2 m/s."""
  t, cb, la = F["t"], F["cb"], F["la"]
  v = np.nan_to_num(F["v"])
  j = i
  while j > 0 and v[j - 1] < APPROACH_V and cb[j - 1] > 0 and la[j - 1] and t[j] - t[j - 1] < 0.2:
    j -= 1
  out = {"err": math.nan, "s": 0.0, "v0": float(v[j - 1]) if j > 0 else math.nan}
  if j == 0 or v[j - 1] < APPROACH_V or i - j < 5:
    return out
  w = np.arange(j, i)
  m = (v[w] >= 0.3) & np.isfinite(F["pitch"][w]) & np.isfinite(F["acc"][w]) & np.isfinite(F["a"][w])
  out["s"] = float(len(w) * DT)
  if m.any():
    anet = F["a"][w][m] + G * np.sin(F["pitch"][w][m])
    out["err"] = float(np.mean(anet) - np.mean(F["acc"][w][m]))
  return out


def holds(F: dict, on: np.ndarray | None = None) -> list[dict]:
  """Every openpilot hold of HOLD_MIN_S or more: from the stop (engaged, >= 100 counts, no planner launch, the wheels
  at zero: standstill, or vEgo under HOLD_V_EPS) for as long as engaged, >= 100 counts and no launch hold - whatever
  the wheels do after the stop. A hold that starts to roll therefore stays the same hold (the old definition ended a
  hold on its first moving frame and then trimmed its last 0.5 s, so it could never see the roll). Motion counts from
  HOLD_SETTLE in (the stop settling) to the hold's end, less only a trailing release (the command falling more than 6
  counts below the hold's median: the brake being let go, which the car may follow before the hold formally ends); a
  launch request, a pedal or a disengage end the hold, and the car follows those only afterwards. `on` is the pump
  bit (default: the logged one), for the delivered level."""
  t, cb = F["t"], F["cb"]
  v = np.nan_to_num(F["v"], nan=0.0)
  wire = (F["pump"] & (cb > 0)) if on is None else on
  D = delivered(cb, wire, t)
  base = F["la"] & (cb >= 100) & ~(F["acc"] > 0) & (F["gp"] != 1) & (F["bp"] != 1)
  stopped = (F["ss"] == 1) | (np.abs(v) < HOLD_V_EPS)
  out = []
  for a0, b in zip(*runs(base), strict=True):
    st = np.flatnonzero(stopped[a0:b])
    if not len(st):
      continue
    a = a0 + int(st[0])
    if t[b - 1] - t[a] < HOLD_MIN_S:
      continue
    k, cb_med = b, float(np.median(cb[a:b]))
    while k - 1 > a + HOLD_SETTLE and cb[k - 1] < cb_med - 6:
      k -= 1
    w = slice(min(a + HOLD_SETTLE, k - 1), k)
    xm = np.nan_to_num(F["xmission"][w])
    vv = np.abs(np.nan_to_num(F["v"][w]))
    wm = np.nan_to_num(F["wheels_moving"][w])
    # vEgo at a standstill reads +-1e-6 (the filter's residue), so "0" is |vEgo| <= HOLD_V_EPS
    moved = {"xmission": bool(xm.max(initial=0) > 0), "vEgo": bool(vv.max(initial=0) > HOLD_V_EPS),
             "wheels_moving": bool(wm.max(initial=0) > 0)}
    mv = (xm > 0) | (vv > HOLD_V_EPS) | (wm > 0)
    # radar: the net change in distance to a STOPPED lead (|vLead| < 0.1), the median of its first 2 s against its
    # last 2 s - so a creep and a re-stop inside the hold both count; a lead that pulls away at the end, or the
    # radar's +-0.1-0.2 m frame noise, is not our creep
    rd = math.nan
    tw = t[w]
    lw = F["lead"][w] & np.isfinite(F["dRel"][w]) & (np.abs(np.nan_to_num(F["vLead"][w], nan=9)) < 0.1)
    if lw.sum() >= 200:
      tl, dl = tw[lw], F["dRel"][w][lw]
      rd = float(abs(np.median(dl[tl >= tl[-1] - 2.0]) - np.median(dl[tl <= tl[0] + 2.0])))
    # camera: the pose's forward velocity integrated over the hold after its first 4 s, where the estimator is still
    # settling from the stop (pump2 A_standstill: every outlier was that settling, none was creep)
    cam = math.nan
    vx, tt = F["cam_vx"][a:b], t[a:b]
    if np.isfinite(vx).mean() > 0.8 and tt[-1] - tt[0] > 5.0:
      rest = (tt - tt[0] >= 4.0) & np.isfinite(vx)
      cam = float(abs(np.sum(vx[rest] * F["dt"][a:b][rest])))
    # MOVED is the car's own sensors. The radar and the camera are the plan's finer checks, but on the baseline
    # (10f t 293 and t 2368: wheels, XMISSION and vEgo all 0, camera 0.06-0.11 m) the radar's net change to a stopped
    # lead already reads 0.21-0.27 m against the plan's 0.1 -- so past their limits a hold is SUSPECT, to look at,
    # not an abort on its own.
    out.append({"t": float(t[a]), "s": float(t[b - 1] - t[a]), "cb": float(np.median(cb[a:b])), **moved,
                "moved_s": float(F["dt"][w][mv].sum()), "moved_first_t": float(tw[mv][0]) if mv.any() else math.nan,
                "dist_moved": float(np.sum(np.maximum(xm, vv) * F["dt"][w])),
                "delivered": float(np.median(D[a:b])), "delivered_min": float(D[a:b].min()),
                "radar_change": rd, "camera_disp": cam, "moved": any(moved.values()),
                "suspect": (rd > CREEP_RADAR_MAX) or (cam > CREEP_CAMERA_MAX)})
  return out


def learner(S: dict) -> dict:
  vals = []
  for _, txt in S["logs"]:
    msg = _log_text(txt)
    if not msg.startswith("hondadyn ") or " tuner=0 " in f" {msg} ":
      continue
    for tok in msg.split():
      if tok.startswith("brakec="):
        try:
          vals.append(float(tok.split("=", 1)[1]))
        except ValueError:
          pass
  return {"n": len(vals), "first": vals[0] if vals else math.nan, "last": vals[-1] if vals else math.nan,
          "delta": vals[-1] - vals[0] if vals else math.nan}


def _log_text(txt: str) -> str:
  try:
    m = json.loads(txt).get("msg", "")
    return m if isinstance(m, str) else ""
  except Exception:
    for tag in LOG_TAGS:
      k = txt.find(tag + " ")
      if k >= 0:
        return txt[k:]
    return ""


def log_tags(S: dict) -> dict:
  """pump= / blaw= / commit= from the route's own hondadyn or hondashadow lines (batch 3 and later)."""
  out = {}
  for _, txt in S["logs"]:
    for tok in _log_text(txt).split():
      k, _, v = tok.partition("=")
      if k in ("pump", "blaw", "commit") and v and v != "-":
        out.setdefault(k, v)
  return out


def vsa(F: dict) -> dict:
  cb, act = F["cb"], F["la"]
  es, ee = runs(cb > 0)
  core = np.zeros(len(cb), dtype=bool)
  for a, b in zip(es, ee, strict=True):      # skip each application's first 0.2 s (0x1A4 answers ~0.1 s later)
    core[a + 10:b] = True
  m = core & act & np.isfinite(F["comp_braking"])
  comp = float(np.mean(F["comp_braking"][m] == 1)) if m.any() else math.nan
  berr_frames = int(np.nansum(F["berr"] == 1))
  first_err = float(F["t"][np.argmax(F["berr"] == 1)]) if berr_frames else math.nan
  # ripple: dips in USER_BRAKE (a local minimum with |d_prev| + |d_next| >= 3), first one after each pump onset
  ub_t, ub = F["_ub_t"], F["_ub"].astype(int)
  onsets = []
  if len(ub) > 3:
    a_ = np.r_[0, ub[1:] - ub[:-1]]
    b_ = np.r_[ub[:-1] - ub[1:], 0]
    dip_t = ub_t[(a_ < 0) & (b_ < 0) & (np.abs(a_) + np.abs(b_) >= 3)]
    st = np.flatnonzero(np.diff(np.r_[0, (F["pump"] & act).astype(np.int8)]) == 1)
    pedal = (F["bp"] == 1) | (F["gp"] == 1)
    for s in st:
      if pedal[max(0, s - 25):s + 25].any() or F["cb"][s] > 60:
        continue
      k = np.searchsorted(dip_t, F["t"][s])
      if k < len(dip_t) and dip_t[k] - F["t"][s] <= 1.0:
        onsets.append(float(dip_t[k] - F["t"][s]))
  return {"comp_braking_frac": comp, "brake_error_frames": berr_frames, "first_brake_error_t": first_err,
          "ripple_onsets": len(onsets), "ripple_onset_median": float(np.median(onsets)) if onsets else math.nan}


def brake_law(F: dict) -> dict:
  """B4: the integrator while braking per speed band, lag-compensated brake RMS, take-overs, FCW/AEB."""
  v = np.nan_to_num(F["v"])
  pid_brk = F["la"] & (F["lcs"] == LCS["pid"]) & (F["cb"] > 0) & (F["bp"] != 1) & (F["gp"] != 1)
  bands = []
  for lo_, hi_ in SPEED_BANDS:
    m = pid_brk & (v >= lo_) & (v < hi_) & np.isfinite(F["ui"])
    bands.append({"band": f"{lo_:g}-{hi_:g}" if hi_ < 99 else f">{lo_:g}", "s": float(F["dt"][m].sum()),
                  "ui": float(np.mean(F["ui"][m])) if m.sum() >= 50 else math.nan})
  # lag-compensated: the command through a 0.3 s first-order lag, against the grade-corrected accel
  ref = np.full(len(v), np.nan)
  x = 0.0
  alpha = DT / (0.3 + DT)
  for i, a in enumerate(F["acc"]):
    if np.isfinite(a):
      x += alpha * (a - x)
    ref[i] = x
  hi = rolling(F["cb"], 40, np.max)
  lo = rolling(F["cb"], 40, np.min)
  m = pid_brk & (v >= 3) & (hi - lo <= 8) & np.isfinite(F["ac"])
  rms = float(np.sqrt(np.mean((F["ac"][m] - ref[m]) ** 2))) if m.sum() >= 50 else math.nan
  la, bp = F["la"], F["bp"] == 1
  takeovers = int(np.sum(la[:-1] & ~la[1:] & bp[1:] & (v[1:] > 1.0)))
  edge = lambda x: int(np.sum(~x[:-1] & x[1:]))  # noqa: E731
  return {"bands": bands, "rms": rms, "rms_s": float(F["dt"][m].sum()), "takeovers": takeovers,
          "stock_fcw": edge(F["fcw"] == 1), "stock_aeb": edge(F["aeb"] == 1), "op_fcw": edge(F["op_fcw"])}


def rule_match(rep_on: np.ndarray, wire: np.ndarray, m: np.ndarray) -> tuple[float, float]:
  """(raw, tolerant) share of the frames in m where the replayed bit equals the logged one. Tolerant: a mismatch counts
  as a match when the replay one frame earlier or later equals the logged bit - a burst edge one frame off, which is
  the log's timestamps jittering against the controller's frame*DT_CTRL clock (115: pairs of mismatches 0.5 s apart
  at burst edges, no rule difference)."""
  if not m.any():
    return math.nan, math.nan
  ok = rep_on == wire
  near = (np.r_[rep_on[1:], rep_on[-1:]] == wire) | (np.r_[rep_on[:1], rep_on[:-1]] == wire)
  return float(np.mean(ok[m])), float(np.mean((ok | near)[m]))


# --- one route -------------------------------------------------------------------------------------------------------

def check_route(path: str, rule: str | None = None, force_rlog: bool = False, workers: int = 0) -> dict:
  S = load_route(path, force_rlog, workers)
  F = build_frames(S)
  meta = S["meta"]
  tags = log_tags(S)
  ran, how, blaw, blaw_how, warn = what_ran(meta.get("flags_sp"), tags, rule)
  fns = rule_functions()
  wire = F["pump"] & (F["cb"] > 0)
  reps = {name: replay_rule(name, F["cb"], F["v"], F["t"], fns) for name in ("v5", "v6")}
  m = F["cb"] > 0
  raw, tol = rule_match(reps[ran], wire, m) if ran in reps else (math.nan, math.nan)
  R = {"route": path, "name": route_name(path), "source": meta.get("source"), "notes": meta.get("notes", []) + warn,
       "commit": (meta.get("commit") or tags.get("commit") or "-")[:9], "rule": ran, "rule_how": how, "brake_law": blaw,
       "brake_law_how": blaw_how, "rule_sources": {k: fns[k][1] for k in fns}, "brake_src": F["brake_src"],
       "frames": int(len(F["t"])), "match": tol, "match_raw": raw,
       "pump": {"wire": pump_metrics(F, wire), "v5": pump_metrics(F, reps["v5"]), "v6": pump_metrics(F, reps["v6"])},
       "stops_pump": standstill_bursts(F, wire),
       "gain": steady_gain(F), "bleed": bleed(F), "rises": rises(F), "stops": stops(F), "holds": holds(F, wire),
       "learner": learner(S), "vsa": vsa(F), "law": brake_law(F)}
  R["grade_r"] = grade_check(F)
  R["grade_s"] = float(F["dt"][np.isfinite(F["grade"])].sum())
  return R


def what_ran(fl: int | None, tags: dict, rule: str | None) -> tuple[str, str, str, str, list[str]]:
  """(pump rule, how known, brake law, how known, warnings). The pump rule: CarParamsSP flag 16, else the route's
  pump= tag, else --rule, else v5 assumed; a --rule the log contradicts is ignored. The brake law: the route's blaw=
  tag (it says what RAN - set_brake_law_v2 corrects it when gas law v2 is off), else flag 32 (the setting)."""
  warn = []
  if fl is not None:
    ran, how = ("v6" if fl & 16 else "v5"), f"CarParamsSP.flags = {fl}"
  elif tags.get("pump") in ("v5", "v6"):
    ran, how = tags["pump"], "the route's hondadyn/hondashadow lines"
  elif rule:
    ran, how = rule, "--rule (the log does not say)"
  else:
    ran, how = "v5", "assumed: no CarParamsSP, no tagged line, no --rule"
  if rule and rule != ran:
    warn.append(f"--rule {rule} ignored: the log says {ran} ({how})")
  if fl is not None and tags.get("pump") in ("v5", "v6") and tags["pump"] != ran:
    warn.append(f"CarParamsSP says pump {ran} but the route's lines say {tags['pump']}")
  flag_law = None if fl is None else ("v2" if fl & 32 else "v1")
  tag_law = tags.get("blaw") if tags.get("blaw") in ("v1", "v2") else None
  if tag_law is not None:
    blaw, blaw_how = tag_law, "the route's blaw= tag"
    if flag_law == "v2" and tag_law == "v1":
      blaw_how = "the route's blaw= tag; v2 requested by flag 32, not run (it needs gas law v2)"
      warn.append("brake law v2 was requested (CarParamsSP flag 32) but did not run (blaw=v1): gas law v2 was off")
    elif flag_law is not None and flag_law != tag_law:
      warn.append(f"CarParamsSP flag 32 says brake law {flag_law} but the route's lines say {tag_law}")
  elif flag_law is not None:
    blaw, blaw_how = flag_law, f"CarParamsSP.flags = {fl} (no blaw= tag: the setting, not proof it ran)"
  else:
    blaw, blaw_how = "v1?", "unknown: no CarParamsSP, no tagged line"
  return ran, how, blaw, blaw_how, warn


def grade_check(F: dict) -> float:
  """r between the GPS-grade-corrected aEgo and the accelerometer's forward specific force, 1 s means, v > 3 m/s."""
  a = rolling(F["ac"], 50, np.nanmean, min_periods=25, center=True)
  f = rolling(F["ff"], 50, np.nanmean, min_periods=25, center=True)
  g = np.isfinite(a) & np.isfinite(f) & (np.nan_to_num(F["v"]) > 3)
  return float(np.corrcoef(a[g], f[g])[0, 1]) if g.sum() > 500 else math.nan


def route_name(path: str) -> str:
  base = os.path.basename(os.path.normpath(path))
  return base.split("--")[0][-8:] if "--" in base else os.path.splitext(base)[0]


# --- pooling an arm, verdicts, printing ------------------------------------------------------------------------------

def pool(results: list[dict]) -> dict:
  """The arm: the sample-level metrics re-computed over every route's samples, the extremes over all routes."""
  P: dict = {"routes": [r["name"] for r in results]}
  if not results:
    return P
  cbl = np.concatenate([r["gain"]["samples"]["cbl"] for r in results])
  bey = np.concatenate([r["gain"]["samples"]["beyond"] for r in results])
  acc = np.concatenate([r["gain"]["samples"]["ac"] for r in results])
  P["gain"] = []
  for lo_, hi_ in GAIN_BANDS:
    mm = (cbl >= lo_) & (cbl < hi_)
    P["gain"].append({"s": float(mm.sum() * DT), "per100": 100.0 * float(np.mean(bey[mm])) / float(np.mean(cbl[mm]))
                      if mm.sum() >= 25 else math.nan})
  P["slope100"] = _slope100(cbl, acc)
  b = [r["bleed"]["samples"] for r in results]
  err = np.concatenate([x["err"] for x in b]) if b else np.array([])
  tsp = np.concatenate([x["tsp"] for x in b]) if b else np.array([])
  cb = np.concatenate([x["cb"] for x in b]) if b else np.array([])
  run = np.concatenate([x["run"] + 10_000_000 * i for i, x in enumerate(b)]) if b else np.array([])
  P["bleed60"] = []
  for lo_, hi_ in BLEED_BINS:
    mm = (cb >= 60) & (tsp >= lo_) & (tsp < hi_)
    P["bleed60"].append({"err": float(np.mean(err[mm])) if mm.sum() >= 10 else math.nan, "stretches": int(len(np.unique(run[mm])))})
  dists = [s["dist"] for r in results for s in r["stops"]["stops"] if math.isfinite(s["dist"])]
  P["stop_dist_median"] = float(np.median(dists)) if dists else math.nan
  P["stop_dist_min"] = float(np.min(dists)) if dists else math.nan
  P["stop_dist_n"] = len(dists)
  lv = [r["learner"]["last"] for r in results if math.isfinite(r["learner"]["last"])]
  P["learner_mean"] = float(np.mean(lv)) if lv else math.nan
  ld = [r["learner"]["delta"] for r in results if math.isfinite(r["learner"].get("delta", math.nan))]
  P["learner_delta_mean"] = float(np.mean(ld)) if ld else math.nan
  P["takeovers"] = sum(r["stops"]["brake_pressed"] for r in results)
  P["arrivals"] = sum(r["stops"]["arrivals"] for r in results)
  P["off_mv_cb100"] = max(r["pump"]["wire"]["off_mv_cb100"] for r in results)
  P["moved_holds"] = sum(1 for r in results for h in r["holds"] if h["moved"])
  P["suspect_holds"] = sum(1 for r in results for h in r["holds"] if h["suspect"] and not h["moved"])
  P["holds"] = sum(len(r["holds"]) for r in results)
  P["brake_error_frames"] = sum(r["vsa"]["brake_error_frames"] for r in results)
  P["match_min"] = min((r["match"] for r in results if math.isfinite(r["match"])), default=math.nan)
  for k in ("starts", "brk_s", "pump_s"):
    P[k] = sum(r["pump"]["wire"][k] for r in results)
  P["starts_per_brk_min"] = P["starts"] / (P["brk_s"] / 60.0) if P["brk_s"] > 0 else math.nan
  ui = {}
  for r in results:
    for i, bnd in enumerate(r["law"]["bands"]):
      if math.isfinite(bnd["ui"]):
        ui.setdefault(i, []).append((bnd["ui"], bnd["s"]))
  P["ui"] = {i: sum(u * s for u, s in x) / sum(s for _, s in x) for i, x in ui.items()}
  return P


def verdicts(P: dict, B: dict | None) -> list[tuple[str, str, str]]:
  """(criterion, PASS / ABORT / n.a. / MANUAL, detail) for every abort criterion of pump2 A_synth section 4."""
  out = []
  moved, suspect = P.get("moved_holds", 0), P.get("suspect_holds", 0)
  out.append(("a hold that moves with cb >= 100 and no planner launch", "ABORT" if moved else ("CHECK" if suspect else "PASS"),
              f"{moved} of {P.get('holds', 0)} holds of 5 s or more moved (wheels, XMISSION, vEgo); {suspect} only past " +
              "the radar/camera limits - look at those"))
  if B and B.get("gain"):
    worse = []
    for i, (a, b) in enumerate(zip(P["gain"], B["gain"], strict=True)):
      if a["s"] >= GAIN_MIN_S and b["s"] >= GAIN_MIN_S and math.isfinite(a["per100"]) and math.isfinite(b["per100"]):
        if a["per100"] - b["per100"] > GAIN_ABORT:
          worse.append(f"{GAIN_BANDS[i][0]}+: {a['per100']:+.2f} vs {b['per100']:+.2f}")
    out.append(("a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)", "ABORT" if worse else "PASS",
                "; ".join(worse) or "no band"))
  else:
    out.append(("a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)", "n.a.", "needs --baseline (the other arm)"))
  b0, b6 = P["bleed60"][0], P["bleed60"][3]
  if b6["stretches"] >= BLEED_MIN_STRETCHES and math.isfinite(b0["err"]) and math.isfinite(b6["err"]):
    d = b6["err"] - b0["err"]
    out.append(("the 6-12 s bleed bin at cb >= 60 weaker than 0-1 s by >= 0.10 (>= 20 stretches)",
                "ABORT" if d >= BLEED_ABORT else "PASS", f"{d:+.3f} m/s^2 over {b6['stretches']} stretches"))
  else:
    out.append(("the 6-12 s bleed bin at cb >= 60 weaker than 0-1 s by >= 0.10 (>= 20 stretches)", "n.a.",
                f"{b6['stretches']} stretches reach 6-12 s at cb >= 60 (needs 20)"))
  dmin = P.get("stop_dist_min", math.nan)
  if math.isfinite(dmin) and dmin < STOP_DIST_MIN:
    out.append(("the median stop distance shorter by > 0.5 m, or any stop under 2.0 m", "ABORT", f"a stop at {dmin:.2f} m"))
  elif B and math.isfinite(B.get("stop_dist_median", math.nan)) and math.isfinite(P.get("stop_dist_median", math.nan)):
    d = B["stop_dist_median"] - P["stop_dist_median"]
    out.append(("the median stop distance shorter by > 0.5 m, or any stop under 2.0 m", "ABORT" if d > STOP_DIST_ABORT else "PASS",
                f"median {P['stop_dist_median']:.2f} m vs {B['stop_dist_median']:.2f} m (n {P['stop_dist_n']} / {B['stop_dist_n']})"))
  else:
    out.append(("the median stop distance shorter by > 0.5 m, or any stop under 2.0 m",
                "PASS" if math.isfinite(dmin) else "n.a.",
                f"no stop under 2.0 m (n {P.get('stop_dist_n', 0)}, min {fnum(dmin, '.2f')} m); the median needs --baseline"))
  if B and math.isfinite(B.get("learner_delta_mean", math.nan)) and math.isfinite(P.get("learner_delta_mean", math.nan)):
    # the arms share one stored gain (each drive starts where the other arm's last drive left it), so the verdict is on
    # each drive's own change, averaged per arm; the level is printed beside it
    d = P["learner_delta_mean"] - B["learner_delta_mean"]
    out.append(("the brake-gain learner more than 0.04 above the other arm's mean", "ABORT" if d > LEARNER_ABORT else "PASS",
                f"per-drive change {P['learner_delta_mean']:+.3f} vs {B['learner_delta_mean']:+.3f}; level " +
                f"{fnum(P.get('learner_mean'), '.3f')} vs {fnum(B.get('learner_mean'), '.3f')}"))
  else:
    out.append(("the brake-gain learner more than 0.04 above the other arm's mean", "n.a.",
                "needs --baseline, and hondadyn lines with the tuner on in both arms"))
  be = P.get("brake_error_frames", 0)
  out.append(("any BRAKE_ERROR (0x1B0)", "ABORT" if be else "PASS", f"{be} frame(s)"))
  out.append(("a VSA/ABS lamp or a new DTC", "MANUAL", "scan after the drive; not in the log"))
  off = P.get("off_mv_cb100", 0.0)
  out.append(("a moving pump-off at cb >= 100 longer than 6.1 s", "ABORT" if off > MOVING_OFF_CB100_MAX else "PASS", f"longest {off:.2f} s"))
  return out


def print_route(R: dict) -> None:
  print(f"\n=== {R['route']}")
  print(f"  {R['frames']} 0x1FA frames ({R['brake_src']}) from {R['source']}; build {R['commit']}; pump rule {R['rule']} " +
        f"({R['rule_how']}); brake law {R['brake_law']} ({R.get('brake_law_how', '-')})")
  for n in R["notes"]:
    print(f"  NOTE: {n}")
  print(f"  grade: GPS-Doppler on {R['grade_s']:.0f} s; r against the accelerometer {fnum(R['grade_r'], '.2f')} (pump2: median 0.88)")
  print(f"  rules replayed from: v5 {R['rule_sources']['v5']}; v6 {R['rule_sources']['v6']}")

  print("\n  1. THE RULE")
  print(f"    replay of {R['rule']} matches the logged pump bit on {fnum(100 * R['match'], '.2f')}% of braking frames " +
        f"with a burst edge one frame off allowed ({fnum(100 * R.get('match_raw', math.nan), '.2f')}% exact; need >= " +
        f"{100 * MATCH_MIN:.1f}%): {'PASS' if R['match'] >= MATCH_MIN else 'FAIL'}")
  hdr = ("pump s", "starts", "/brk-min", "still st", "off cb>=100", "off moving", "undeliv>=10", "apps", "unpumped")
  print("    " + " " * 22 + "".join(h.rjust(12) for h in hdr))
  for k, label in (("wire", "logged (as driven)"), ("v5", "v5 replayed (today)"), ("v6", "v6 replayed (C1)")):
    p = R["pump"][k]
    cells = (f"{p['pump_s']:.1f}", f"{p['starts']}", fnum(p["starts_per_brk_min"], ".1f"), f"{p['starts_still']}",
             f"{p['off_mv_cb100']:.2f}s", f"{p['off_mv_brk']:.2f}s",
             f"{p['undelivered10_pct']:.1f}%/{p['undelivered10_long']:.2f}s", f"{p['applications']}", f"{p['applications_unpumped']}")
    print(f"    {label:22s}" + "".join(c.rjust(12) for c in cells))
  w = R["pump"]["wire"]
  print(f"    engaged {w['eng_s'] / 60:.1f} min, braking {w['brk_s'] / 60:.1f} min. Replays are open loop on the logged " +
        "commands: only the difference between the two rules means anything.")
  sp = R["stops_pump"]
  bad = [s for s in sp if not s["c1_ok"]]
  print(f"    stops held >= 1 s: {len(sp)}; standstill bursts {sum(s['bursts'] for s in sp)} " +
        f"({fnum(np.mean([s['bursts'] for s in sp]) if sp else math.nan, '.2f')}/stop); stops breaking C1's design " +
        f"(more than one, or one that neither built a hold nor delivered a rise of > {BIG_RISE}): {len(bad)}" +
        ("" if R["rule"] == "v6" else " - expected under v5, which tops up every 30 s"))
  short = [s for s in sp if s["hold_s"] >= 5.0 and s["delivered_hold"] < s["cb_hold"] - BIG_RISE]
  print(f"    stops held >= 5 s that held (median) more than {BIG_RISE} counts under their command, by pump2's delivery " +
        f"model: {len(short)}" + "".join(f"\n      t {s['t']:.1f}: command {s['cb_hold']:.0f}, delivered " +
                                         f"{s['delivered_hold']:.0f}" for s in short))
  print(f"    longest moving pump-off at cb >= 100: {w['off_mv_cb100']:.2f} s at t {fnum(w['off_mv_cb100_t'], '.1f')} " +
        f"(abort above {MOVING_OFF_CB100_MAX} s)")

  g = R["gain"]
  print("\n  2. STEADY-COMMAND GAIN (decel beyond coasting per 100 counts; command held +-3 for 1 s, v >= 3 m/s)")
  print("    " + "  ".join(f"{b['band']:>8s}: {fnum(b['per100'])} ({b['s']:.0f}s)" for b in g["bands"]))
  print(f"    least-squares slope of the grade-corrected accel on the command: {fnum(g['slope100'])} per 100 counts " +
        "(pump2 V5: about -0.79)")

  b = R["bleed"]
  print("\n  3. BLEED (achieved - commanded 0.3 s earlier, m/s^2; + = less decel than asked), by time since the last pump run")
  for key, lab in (("cb0", "all commands"), ("cb60", "cb >= 60")):
    print(f"    {lab:13s}" + "".join(f"{x['bin']:>8s} {fnum(x['err'], '+.3f')} ({x['stretches']})" for x in b[key]))

  r = R["rises"]
  print("\n  4. RISE RESPONSE")
  print(f"    {r['n']} rises above the deadband from a flat command: decel change 0.3-0.7 s after, median " +
        f"{fnum(r['median_per100'])} per 100 counts (expect about -1.0); pumped within 0.3 s: {fnum(100 * r['pumped_frac'], '.0f')}%")
  print(f"    over-target bites (> 0.5 m/s^2 beyond the command within 1 s of a pump start): {r['bites']} of " +
        f"{r['starts_moving']} moving pump starts")

  s = R["stops"]
  rows = s["stops"]
  print("\n  5. STOPS")
  dist = [x["dist"] for x in rows if math.isfinite(x["dist"])]
  ae = [x["approach_err"] for x in rows if math.isfinite(x["approach_err"])]
  ae_s = sum(x["approach_s"] for x in rows if math.isfinite(x["approach_err"]))
  jk = [x["settle_jerk"] for x in rows if math.isfinite(x["settle_jerk"])]
  print(f"    {s['arrivals']} engaged arrivals (openpilot on 6 s before the stop); driver brake take-overs " +
        f"{s['brake_pressed']} = {fnum(100.0 * s['brake_pressed'] / s['arrivals'] if s['arrivals'] else math.nan, '.0f')} " +
        "per 100 arrivals" + (f" (at t {', '.join(f'{x:.1f}' for x in s['press_t'])})" if s["press_t"] else "") +
        f"; {len(rows)} clean openpilot stops")
  print(f"    distance to a stopped lead at standstill: median {fnum(np.median(dist) if dist else math.nan, '.2f')} m, " +
        f"min {fnum(min(dist) if dist else math.nan, '.2f')} m (n {len(dist)})")
  print("    final-approach tracking error below 2.5 m/s (pitch-corrected, v >= 0.3): median " +
        f"{fnum(np.median(ae) if ae else math.nan, '+.2f')} m/s^2 over {len(ae)} approach(es), {ae_s:.1f} s " +
        "(pump2 V5 median -0.32, the same method)")
  print(f"    settle jerk: median {fnum(np.median(jk) if jk else math.nan, '.2f')} m/s^3")

  h = R["holds"]
  print("\n  6. CREEP (openpilot holds of 5 s or more at >= 100 counts)")
  moved = [x for x in h if x["moved"]]
  rc = [x["radar_change"] for x in h if math.isfinite(x["radar_change"])]
  cm = [x["camera_disp"] for x in h if math.isfinite(x["camera_disp"])]
  dl = [x["delivered"] for x in h]
  print(f"    {len(h)} holds, {sum(x['s'] for x in h):.0f} s; moved (XMISSION_SPEED, vEgo or WHEELS_MOVING): {len(moved)}; " +
        f"radar net change to a stopped lead max {fnum(max(rc) if rc else math.nan, '.2f')} m (n {len(rc)}; plan " +
        f"<= {CREEP_RADAR_MAX}); camera displacement after 4 s max {fnum(max(cm) if cm else math.nan, '.2f')} m (n {len(cm)}; " +
        f"plan <= {CREEP_CAMERA_MAX})")
  for x in h:
    if x["moved"] or x["suspect"]:
      print(f"      {'MOVED' if x['moved'] else 'suspect'} t {x['t']:.1f} ({x['s']:.0f} s at {x['cb']:.0f}, delivered " +
            f"{x['delivered']:.0f}): xmission {x['xmission']} vEgo {x['vEgo']} wheels {x['wheels_moving']}; moving " +
            f"{x['moved_s']:.2f} s from t {fnum(x['moved_first_t'], '.1f')}, ~{x['dist_moved']:.2f} m; radar " +
            f"{fnum(x['radar_change'], '.2f')} camera {fnum(x['camera_disp'], '.2f')}")
  if h:
    print(f"    held pressure DELIVERED (pump2's model on the logged pump bit): median {np.median(dl):.0f} counts, lowest " +
          f"{min(x['delivered_min'] for x in h):.0f}; holds commanded >= {BIG_RISE} counts above what was delivered: " +
          f"{sum(1 for x in h if x['cb'] > x['delivered'] + BIG_RISE)}")

  lr = R["learner"]
  print(f"\n  7. BRAKE-GAIN LEARNER: {lr['n']} hondadyn line(s) with the tuner on; brakec {fnum(lr['first'], '.3f')} -> " +
        f"{fnum(lr['last'], '.3f')}, this drive's change {fnum(lr.get('delta'), '+.3f')} (baseline level 0.99-1.03)")

  v = R["vsa"]
  print(f"\n  8. VSA: COMPUTER_BRAKING on {fnum(100 * v['comp_braking_frac'], '.2f')}% of brake frames (need >= 99%); " +
        f"brake-error frames {v['brake_error_frames']}"
        + (f" (first at t {v['first_brake_error_t']:.1f})" if v["brake_error_frames"] else "")
        + f"; ripple onset median {fnum(v['ripple_onset_median'], '.2f')} s over {v['ripple_onsets']} pump starts (need <= 0.2)")

  L = R["law"]
  print("\n  B4. BRAKE LAW")
  print("    integrator (uiAccelCmd) while braking, by speed (m/s): " +
        "  ".join(f"{x['band']}: {fnum(x['ui'], '+.3f')} ({x['s']:.0f}s)" for x in L["bands"]) +
        f"   (accept: all within +-{INTEGRATOR_OK})")
  stp = [x for x in rows if math.isfinite(x["decel_at_stop"])]
  print(f"    brake RMS, lag-compensated: {fnum(L['rms'], '.3f')} m/s^2 over {L['rms_s']:.0f} s (steady PID braking: achieved " +
        "against the command through 0.3 s; compare the arms - learnaudit's 'about 0.20' is its model-fit figure)")
  print(f"    at the stop: decel {fnum(np.median([x['decel_at_stop'] for x in stp]) if stp else math.nan, '.2f')} m/s^2 " +
        f"(IMU; ~0.97), {fnum(np.median([x['cb_at_stop'] for x in rows]) if rows else math.nan, '.0f')} counts when the wheels " +
        f"stop (~174), {fnum(np.median([x['cb_max_1s'] for x in rows]) if rows else math.nan, '.0f')} a second later (~189)")
  print(f"    driver brake take-overs {L['takeovers']}; stock FCW {L['stock_fcw']}, stock AEB {L['stock_aeb']}, openpilot FCW " +
        f"{L['op_fcw']}; VSA brake errors: section 8")


def print_arm(P: dict, B: dict | None, title: str) -> None:
  print(f"\n=== {title}: {', '.join(P['routes'])}")
  print(f"  starts per braking minute {fnum(P['starts_per_brk_min'], '.1f')} (today ~17, C1 ~14); replay match min " +
        f"{fnum(100 * P['match_min'], '.2f')}%; longest moving pump-off at cb >= 100 {P['off_mv_cb100']:.2f} s")
  print(f"  driver brake take-overs {P.get('takeovers', 0)} of {P.get('arrivals', 0)} engaged arrivals; brake-gain learner " +
        f"per-drive change {fnum(P.get('learner_delta_mean'), '+.3f')}, level {fnum(P.get('learner_mean'), '.3f')}")
  print("  steady gain per 100: " + "  ".join(f"{GAIN_BANDS[i][0]}+ {fnum(x['per100'])} ({x['s']:.0f}s)" for i, x in enumerate(P["gain"])) +
        f"; slope {fnum(P['slope100'])}")
  if B:
    print("  baseline arm:        " + "  ".join(f"{GAIN_BANDS[i][0]}+ {fnum(x['per100'])} ({x['s']:.0f}s)" for i, x in enumerate(B["gain"])) +
          f"; slope {fnum(B['slope100'])}")
  print("  bleed at cb >= 60:   " + "  ".join(f"{BLEED_BINS[i][0]:g}s+ {fnum(x['err'], '+.3f')} ({x['stretches']})" for i, x in enumerate(P["bleed60"])))
  print("  integrator braking:  " + "  ".join(f"{SPEED_BANDS[i][0]:g}+ {fnum(u, '+.3f')}" for i, u in sorted(P["ui"].items())))
  print("\n  ABORT CRITERIA (pump2 A_synth section 4)")
  for crit, verdict, detail in verdicts(P, B):
    print(f"    [{verdict:6s}] {crit}: {detail}")


def _jsonable(x):
  if isinstance(x, dict):
    return {str(k): _jsonable(v) for k, v in x.items() if k != "samples"}
  if isinstance(x, list | tuple):
    return [_jsonable(v) for v in x]
  if isinstance(x, np.ndarray):
    return [_jsonable(v) for v in x.tolist()]
  if isinstance(x, np.generic):
    return x.item()
  if isinstance(x, float) and not math.isfinite(x):
    return None
  return x


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("routes", nargs="+", help="route folders (or rlog files): the arm being checked")
  ap.add_argument("--baseline", nargs="*", default=[], help="route folders of the other arm, for the comparative criteria")
  ap.add_argument("--rule", choices=("v5", "v6"), help="the pump rule the routes ran, used only when neither CarParamsSP " +
                  "nor the route's pump= tag says (one the log contradicts is ignored, with a warning)")
  ap.add_argument("--rlog", action="store_true", help="read the rlogs even when a parquet export is there")
  ap.add_argument("--workers", type=int, default=0, help="processes for reading rlogs (default: cores - 1)")
  ap.add_argument("--json", help="also write every number to this file")
  args = ap.parse_args(argv)

  arm = [check_route(p, args.rule, args.rlog, args.workers) for p in args.routes]
  for R in arm:
    print_route(R)
  base = [check_route(p, None, args.rlog, args.workers) for p in args.baseline]
  P, B = pool(arm), (pool(base) if base else None)
  print_arm(P, B, f"ARM ({len(arm)} route(s))")
  if args.json:
    with open(args.json, "w") as f:
      json.dump(_jsonable({"arm": arm, "baseline": base, "pooled": P, "baseline_pooled": B,
                           "verdicts": verdicts(P, B)}), f, indent=1)
  return 0


if __name__ == "__main__":
  sys.exit(main())
