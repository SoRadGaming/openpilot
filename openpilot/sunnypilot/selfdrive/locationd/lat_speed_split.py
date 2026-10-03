"""
FORK(HONDA_ACCORD_9G_AU): a SHADOW, speed-split lateral-accel factor beside torqued. It sees every point
torqued accepts, files it under "below" or "above" SPLIT_SPEED (70 km/h), and LOGS the torqued-style fit
of each half. Nothing reads it back: lateralTorqueParameters, the cache torqued writes and every
actuator are exactly what they are without it.

Why. On this car (board AUTHORITY 160) the lateral accel per unit torque is ~1.42-1.49 at ~90 km/h
(route 10f) and ~0.54-0.76 at ~61 km/h (route 115) -- one torqued factor cannot hold both, and it swings
drive to drive with the road mix (1.41 after a highway drive, 1.13 after town). A_synth L3 proposes two
speed-bucketed estimators with the factor interpolated by speed. This runs that in shadow first, to see
whether the two halves separate cleanly and stay put across drives before anything acts on them.

How. A total-least-squares fit of [steer, 1, lateral accel] -- torqued's own estimate_params() -- needs
only the 3x3 Gram matrix of those points, so each half keeps six running sums instead of the points:
the right singular vector torqued takes from the SVD is the Gram's smallest eigenvector, and the
friction (torqued: std of the spread across the fitted line x 1.5) comes from the same second moments.
That is a few float additions per point (torqued adds at most 20 points/s), one 3x3 eigen solve per
log line, and sums that combine EXACTLY across drives: the report adds them and refits.

Differences from torqued, on purpose. It keeps every point of the drive (torqued keeps the newest 1500
per steer bucket and fits a random 2000 of them), it starts empty at every ignition (no cache, no param
writes), and it does not filter (torqued's FirstOrderFilter with its decay). The fit is reported raw and
also clipped to torqued's window around the prior, with torqued's validity rule per half.

Points arrive from TorqueEstimator.handle_log(), on the line after torqued files the same point, so they
pass exactly torqued's gates (latActive with the engage buffer, no steering override, vEgo > MIN_VEL,
|steer| > STEER_MIN_THRESHOLD, |lateral accel| <= LAT_ACC_THRESHOLD). Which means the "below" half is
15-19.4 m/s: torqued's MIN_VEL is a floor for both.

LOG. One `latsplit` line via cloudlog (-> logMessage) every LOG_INTERVAL while points arrived, so the
last line of a route is at most a minute behind the drive's end.
"""

import math

import numpy as np

from openpilot.common.swaglog import cloudlog

LOG_TAG = "latsplit"
LOG_VERSION = 1
SPLIT_SPEED = 70.0 / 3.6      # m/s
HALVES = ("lo", "hi")
DT = 0.05                     # torqued's loop (DT_MDL, driven by deviceMotion at 20 Hz)
LOG_INTERVAL = int(60 / DT)   # frames: one line a minute

# torqued's own constants (selfdrive/locationd/torqued.py). Copied rather than imported, because torqued
# imports this module through torqued_ext; test_lat_speed_split.py fails if they ever drift apart.
STEER_BUCKET_BOUNDS = [(-0.5, -0.3), (-0.3, -0.2), (-0.2, -0.1), (-0.1, 0), (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5)]
MIN_BUCKET_POINTS = [100, 300, 500, 500, 500, 500, 300, 100]
MIN_POINTS_TOTAL = 4000
FACTOR_SANITY = 0.3
FRICTION_FACTOR = 1.5
MIN_FIT_POINTS = 200          # below this the half's fit is not reported (nan)


def tls_fit(n, sx, sy, sxx, sxy, syy):
  """torqued's estimate_params() on the points behind these sums: (slope, offset, friction), or None.

  torqued: SVD of P = [x, 1, y] rows, slope/offset = -v[0:2, 2] / v[2, 2] for the last right singular
  vector -- the eigenvector of P^T P with the smallest eigenvalue; friction = FRICTION_FACTOR x the
  population std of -sin*x + cos*y, with torqued's slope2rot (sin, cos >= 0)."""
  if n < 2:
    return None
  gram = np.array([[sxx, sx, sxy], [sx, n, sy], [sxy, sy, syy]], dtype=np.float64)
  if not np.all(np.isfinite(gram)):
    return None
  _, vecs = np.linalg.eigh(gram)
  v = vecs[:, 0]
  if abs(v[2]) < 1e-12:
    return None
  slope, offset = float(-v[0] / v[2]), float(-v[1] / v[2])
  sin = math.sqrt(slope ** 2 / (slope ** 2 + 1))
  cos = math.sqrt(1 / (slope ** 2 + 1))
  mx, my = sx / n, sy / n
  vxx, vyy, vxy = sxx / n - mx * mx, syy / n - my * my, sxy / n - mx * my
  var = sin * sin * vxx - 2 * sin * cos * vxy + cos * cos * vyy
  return slope, offset, math.sqrt(max(var, 0.0)) * FRICTION_FACTOR


class SplitHalf:
  __slots__ = ("n", "sx", "sy", "sxx", "sxy", "syy", "bins")

  def __init__(self):
    self.n = 0
    self.sx = self.sy = self.sxx = self.sxy = self.syy = 0.0
    self.bins = [0] * len(STEER_BUCKET_BOUNDS)

  def add(self, x: float, y: float) -> None:
    for i, (lo, hi) in enumerate(STEER_BUCKET_BOUNDS):
      if lo <= x < hi:
        self.bins[i] += 1
        break
    else:
      return        # torqued's TorqueBuckets drops a point outside every bucket too
    self.n += 1
    self.sx += x
    self.sy += y
    self.sxx += x * x
    self.sxy += x * y
    self.syy += y * y

  def moments(self) -> tuple:
    return self.n, self.sx, self.sy, self.sxx, self.sxy, self.syy

  def fit(self):
    return tls_fit(*self.moments()) if self.n >= MIN_FIT_POINTS else None

  def valid(self) -> bool:
    """torqued's PointBuckets.is_valid() for this half's points."""
    return self.n >= MIN_POINTS_TOTAL and all(b >= m for b, m in zip(self.bins, MIN_BUCKET_POINTS, strict=True))

  def valid_percent(self) -> int:
    """torqued's PointBuckets.get_valid_percent()."""
    total = min(self.n / MIN_POINTS_TOTAL * 100, 100)
    each = min(min(b / m * 100 for b, m in zip(self.bins, MIN_BUCKET_POINTS, strict=True)), 100)
    return int((total + each) / 2)


def interpolated_factor(v_ego: float, lo: float, hi: float, blend=(60 / 3.6, 80 / 3.6)) -> float:
  """What a speed-split torqued would hand the controller: the low half's factor up to 60 km/h, the high
  half's from 80 km/h, linear between. Reporting only."""
  return float(np.interp(v_ego, blend, [lo, hi]))


class LatSpeedSplitShadow:
  def __init__(self, prior_factor: float):
    self.prior = float(prior_factor)
    self.halves = {h: SplitHalf() for h in HALVES}
    self.frame = 0
    self._logged_n = 0
    self.lines = 0
    self.dead = False

  # Both entry points run inside torqued, whose lateralTorqueParameters the controller uses, so neither may
  # raise: the first exception logs once and leaves this object inert for the rest of the drive.

  def add_point(self, v_ego: float, steer: float, lateral_acc: float) -> None:
    if self.dead:
      return
    try:
      v, x, y = float(v_ego), float(steer), float(lateral_acc)
      if math.isfinite(v) and math.isfinite(x) and math.isfinite(y):
        self.halves["lo" if v < SPLIT_SPEED else "hi"].add(x, y)
    except Exception:
      self._die()

  def _die(self) -> None:
    self.dead = True
    try:
      cloudlog.exception(f"{LOG_TAG}: raised; off for the rest of this drive")
    except Exception:
      pass

  def clipped(self, factor: float) -> float:
    lo, hi = (1.0 - FACTOR_SANITY) * self.prior, (1.0 + FACTOR_SANITY) * self.prior
    return min(max(factor, lo), hi)

  def tick(self, main_factor: float = float("nan")) -> None:
    """Once per torqued loop. Logs every LOG_INTERVAL frames, if any point arrived since the last line."""
    if self.dead:
      return
    try:
      self.frame += 1
      if self.frame % LOG_INTERVAL != 0:
        return
      n = sum(h.n for h in self.halves.values())
      if n == self._logged_n:
        return
      self._logged_n = n
      self.lines += 1
      cloudlog.info(self.line(float(main_factor)))
    except Exception:
      self._die()

  def line(self, main_factor: float = float("nan")) -> str:
    fac, off, fric, clip = [], [], [], []
    for h in HALVES:
      f = self.halves[h].fit()
      fac.append(f[0] if f else float("nan"))
      off.append(f[1] if f else float("nan"))
      fric.append(f[2] if f else float("nan"))
      clip.append(self.clipped(f[0]) if f else float("nan"))

    def fl(vals, spec):
      return "[" + ",".join("nan" if not math.isfinite(x) else format(x, spec) for x in vals) + "]"

    parts = [f"{LOG_TAG} v={LOG_VERSION} split={SPLIT_SPEED:.2f} prior={self.prior:.3f}",
             f"main={main_factor:.3f}" if math.isfinite(main_factor) else "main=nan",
             f"n={fl([self.halves[h].n for h in HALVES], 'd')}",
             f"fac={fl(fac, '.3f')} clip={fl(clip, '.3f')} off={fl(off, '+.3f')} fric={fl(fric, '.3f')}",
             f"cal={fl([self.halves[h].valid_percent() for h in HALVES], 'd')}",
             f"valid={fl([int(self.halves[h].valid()) for h in HALVES], 'd')}"]
    for h in HALVES:
      half = self.halves[h]
      parts.append(f"mom_{h}={fl(half.moments(), '.7g')} bins_{h}={fl(half.bins, 'd')}")
    return " ".join(parts)


def make_lat_speed_split(CP, prior_factor: float, decimated: bool):
  """The shadow for the Elesys Accord's live torqued (not the qlog-decimated estimator); None otherwise."""
  try:
    from opendbc.car.honda.values import HONDA_ELESYS
    if decimated or CP.carFingerprint not in HONDA_ELESYS or CP.lateralTuning.which() != 'torque':
      return None
    return LatSpeedSplitShadow(prior_factor)
  except Exception:
    cloudlog.exception(f"{LOG_TAG}: not started")
    return None
