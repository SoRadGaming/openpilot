"""
FORK(HONDA_ACCORD_9G_AU): a SHADOW beside torqued that files every point torqued accepts by the driver's hands and
by speed, and LOGS torqued's own fit for each cell, with the controller's integrator and paramsd's roll term beside
it. Nothing reads it back: lateralTorqueParameters, the cache torqued writes and every actuator are exactly what
they are without it.

Why (v2, round 8 proposal 7). v1 split torqued's points at 70 km/h only. The drives of 17-19 Oct showed that what
pinned torqued's factor at its floor was mostly a hand resting on the wheel, not town speed: 92-97% of its points
had 300-1199 counts of driver torque in torqued's window, and at 54-70 km/h the car turned about half as much per
unit of torque with a hand on (0.64) as hands-off (1.69). v1 mixed hands into both of its halves. v2 keeps one cell
per (hands, speed band), so a hands gate for torqued, a speed-split factor or a roll feed-forward gain can each be
decided from pooled data later. It applies none of them.

Cells. Hands, from the largest |carState.steeringTorque| over torqued's own override window (the samples torqued
received from t - MIN_ENGAGE_BUFFER to t + lag, on its lag-shifted clock):
    off   below HANDS_QUIET (300 counts)
    on    HANDS_QUIET to HANDS_TOUCH (300-1199): a hand on the wheel that torqued's steeringPressed gate let through
    hard  HANDS_TOUCH (1200) and up, still not flagged pressed (this car's threshold is 600, debounced): kept apart
The thresholds are the analysis's (round 8, steer-learn 2.2). Speed: 54-70, 70-90 and 90+ km/h; torqued's MIN_VEL
(15 m/s = 54 km/h) is the floor of the first band.

How. A total-least-squares fit of [steer, 1, lateral accel] -- torqued's own estimate_params() -- needs only the
3x3 Gram matrix of those points, so each cell keeps six running sums instead of the points: the right singular
vector torqued takes from the SVD is the Gram's smallest eigenvector, and the friction (torqued: std of the spread
across the fitted line x 1.5) comes from the same second moments. Beside them each cell keeps the six sums of the
controller's integrator i (controlsState lateralControlState.torqueState.i, lateral-accel units) and the roll term
r = vehicleParameters.roll x g (the controller's own roll_compensation) at the moment each point was filed, when
both are fresh: the mean controller offset and roll term per cell, and the slope of one on the other. Every sum
combines EXACTLY across drives: the report adds them and refits.

Differences from torqued, on purpose. It keeps every point of the drive (torqued keeps the newest 1500 per steer
bucket and fits a random 2000 of them), it starts empty at every ignition (no cache, no param writes), and it does
not filter. The controller inputs come from a SubMaster of the shadow's own, polled without blocking, so torqued's
SubMaster, its all_checks() and its loop timing stay upstream's.

LOG. One `latsplit` line via cloudlog (-> logMessage) every LOG_INTERVAL while points arrived, so the last line of
a route is at most a minute behind the drive's end. v=2 lines carry what they were measured on: commit (Params
GitCommit) and board (Params EpsLkasBoardVersion, the 0x707 hash card writes; the previous drive's until card has
seen this drive's first 0x707), plus torqued's window; shadow_learn_report.py pools only lines of one build.
"""

import math
from collections import deque

import numpy as np

from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.swaglog import cloudlog

LOG_TAG = "latsplit"
LOG_VERSION = 2
DT = 0.05                     # torqued's loop (DT_MDL, driven by deviceMotion at 20 Hz)
LOG_INTERVAL = int(60 / DT)   # frames: one line a minute
HISTORY_LEN = int(5 / DT)     # torqued's hist_len (HISTORY / DT_MDL): the driver torque it can see

BAND_EDGES = (70.0 / 3.6, 90.0 / 3.6)    # m/s
BANDS = ("54", "70", "90")                # each band's lower edge in km/h
HANDS_QUIET = 300                         # carState.steeringTorque counts (0x18F, 129-count steps)
HANDS_TOUCH = 1200
HANDS = ("off", "on", "hard")
CELLS = tuple((h, b) for h in HANDS for b in BANDS)

SHADOW_SERVICES = ("controlsState", "vehicleParameters")
CTL_FRESH_FRAMES = 2          # controlsState is 100 Hz: one per torqued loop, so 2 loops (0.1 s) is stale
ROLL_FRESH_FRAMES = 10        # vehicleParameters is 20 Hz: 0.5 s

# torqued's own constants (selfdrive/locationd/torqued.py). Copied rather than imported from torqued.py, which
# imports this module (a cycle); test_lat_speed_split.py fails if they ever drift apart.
STEER_BUCKET_BOUNDS = [(-0.5, -0.3), (-0.3, -0.2), (-0.2, -0.1), (-0.1, 0), (0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.5)]
MIN_BUCKET_POINTS = [100, 300, 500, 500, 500, 500, 300, 100]
MIN_POINTS_TOTAL = 4000
FACTOR_SANITY = 0.3
FRICTION_FACTOR = 1.5
MIN_ENGAGE_BUFFER = 2
MIN_FIT_POINTS = 200          # below this a cell's fit is not reported (nan)


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


def ctl_stats(cn, si, sr, sii, srr, sir):
  """(mean integrator, its sd, mean roll term, slope of the integrator on the roll term) from the six sums; nan where
  undefined. The slope is the share of the roll term the integrator ends up canceling (0 = none)."""
  if cn < 1:
    return (float("nan"),) * 4
  mi, mr = si / cn, sr / cn
  vi, vr, cir = sii / cn - mi * mi, srr / cn - mr * mr, sir / cn - mi * mr
  slope = cir / vr if (cn >= MIN_FIT_POINTS and vr > 1e-6) else float("nan")
  return mi, math.sqrt(max(vi, 0.0)), mr, slope


def hands_class(max_abs_torque: float) -> str:
  if max_abs_torque < HANDS_QUIET:
    return "off"
  return "on" if max_abs_torque < HANDS_TOUCH else "hard"


def band_of(v_ego: float) -> str:
  return BANDS[sum(v_ego >= e for e in BAND_EDGES)]


class Cell:
  __slots__ = ("n", "sx", "sy", "sxx", "sxy", "syy", "bins", "cn", "si", "sr", "sii", "srr", "sir")

  def __init__(self):
    self.n = 0
    self.sx = self.sy = self.sxx = self.sxy = self.syy = 0.0
    self.bins = [0] * len(STEER_BUCKET_BOUNDS)
    self.cn = 0
    self.si = self.sr = self.sii = self.srr = self.sir = 0.0

  def add(self, x: float, y: float) -> bool:
    for i, (lo, hi) in enumerate(STEER_BUCKET_BOUNDS):
      if lo <= x < hi:
        self.bins[i] += 1
        break
    else:
      return False  # torqued's TorqueBuckets drops a point outside every bucket too
    self.n += 1
    self.sx += x
    self.sy += y
    self.sxx += x * x
    self.sxy += x * y
    self.syy += y * y
    return True

  def add_ctl(self, i: float, r: float) -> None:
    self.cn += 1
    self.si += i
    self.sr += r
    self.sii += i * i
    self.srr += r * r
    self.sir += i * r

  def moments(self) -> tuple:
    return self.n, self.sx, self.sy, self.sxx, self.sxy, self.syy

  def ctl(self) -> tuple:
    return self.cn, self.si, self.sr, self.sii, self.srr, self.sir

  def fit(self):
    return tls_fit(*self.moments()) if self.n >= MIN_FIT_POINTS else None

  def valid(self) -> bool:
    """torqued's PointBuckets.is_valid() for this cell's points."""
    return self.n >= MIN_POINTS_TOTAL and all(b >= m for b, m in zip(self.bins, MIN_BUCKET_POINTS, strict=True))

  def valid_percent(self) -> int:
    """torqued's PointBuckets.get_valid_percent()."""
    total = min(self.n / MIN_POINTS_TOTAL * 100, 100)
    each = min(min(b / m * 100 for b, m in zip(self.bins, MIN_BUCKET_POINTS, strict=True)), 100)
    return int((total + each) / 2)


def interpolated_factor(v_ego: float, lo: float, hi: float, blend=(60 / 3.6, 80 / 3.6)) -> float:
  """What a speed-split torqued would hand the controller: the low factor up to 60 km/h, the high one from 80 km/h,
  linear between. Reporting only."""
  return float(np.interp(v_ego, blend, [lo, hi]))


def _tag(raw, width: int) -> str:
  """A Params string as a build tag: alphanumeric, cut to `width`, or '-'."""
  if isinstance(raw, bytes):
    raw = raw.decode(errors="replace")
  txt = str(raw or "").strip()[:width]
  return txt if txt and txt.isalnum() else "-"


class LatSpeedSplitShadow:
  def __init__(self, prior_factor: float, params=None):
    self.prior = float(prior_factor)
    self.cells = {c: Cell() for c in CELLS}
    self.driver: deque = deque(maxlen=HISTORY_LEN)    # (lag-shifted t, |steeringTorque|), as torqued keeps carState
    self.unclassified = 0
    self.window = (float("nan"), float("nan"))
    self.frame = 0
    self._logged_n = 0
    self.lines = 0
    self.dead = False
    self._params = params
    self._ctl_i, self._ctl_age = float("nan"), 1 << 30
    self._roll, self._roll_age = float("nan"), 1 << 30

  # Every entry point runs inside torqued, whose lateralTorqueParameters the controller uses, so none may raise: the
  # first exception logs once and leaves this object inert for the rest of the drive.

  def _die(self) -> None:
    self.dead = True
    try:
      cloudlog.exception(f"{LOG_TAG}: raised; off for the rest of this drive")
    except Exception:
      pass

  def add_driver_torque(self, t: float, torque: float) -> None:
    if self.dead:
      return
    try:
      self.driver.append((float(t), abs(float(torque))))
    except Exception:
      self._die()

  def driver_max(self, lo: float, hi: float) -> float | None:
    """The largest |driver torque| torqued's override window saw; if no sample falls inside it, the nearest ones
    (torqued's np.interp clamps to them the same way). None before any carState."""
    inside = [a for ts, a in self.driver if lo <= ts <= hi]
    if inside:
      return max(inside)
    before = [a for ts, a in self.driver if ts < lo]
    after = [a for ts, a in self.driver if ts > hi]
    edge = before[-1:] + after[:1]
    return max(edge) if edge else None

  def make_submaster(self):
    """The shadow's own, non-blocking SubMaster for SHADOW_SERVICES, or None: then cells keep no controller sums."""
    if self.dead:
      return None
    try:
      import openpilot.cereal.messaging as messaging
      return messaging.SubMaster(list(SHADOW_SERVICES))
    except Exception:
      cloudlog.exception(f"{LOG_TAG}: no controller inputs")
      return None

  def observe(self, sm) -> None:
    """Once per torqued loop, before torqued handles its messages: the latest integrator and roll term."""
    if self.dead:
      return
    try:
      sm.update(0)
      self._ctl_age += 1
      self._roll_age += 1
      if sm.updated["controlsState"]:
        lcs = sm["controlsState"].lateralControlState
        if lcs.which() == "torqueState" and lcs.torqueState.active:
          self._ctl_i, self._ctl_age = float(lcs.torqueState.i), 0
      if sm.updated["vehicleParameters"]:
        self._roll, self._roll_age = float(sm["vehicleParameters"].roll) * ACCELERATION_DUE_TO_GRAVITY, 0
    except Exception:
      self._die()

  def add_point(self, v_ego: float, steer: float, lateral_acc: float, t_lo: float, t_hi: float) -> None:
    """A point torqued just filed, and torqued's override window [t_lo, t_hi] on its lag-shifted clock."""
    if self.dead:
      return
    try:
      v, x, y = float(v_ego), float(steer), float(lateral_acc)
      if not (math.isfinite(v) and math.isfinite(x) and math.isfinite(y)):
        return
      mx = self.driver_max(float(t_lo), float(t_hi))
      if mx is None or not math.isfinite(mx):
        self.unclassified += 1
        return
      cell = self.cells[(hands_class(mx), band_of(v))]
      if cell.add(x, y) and self._ctl_age <= CTL_FRESH_FRAMES and self._roll_age <= ROLL_FRESH_FRAMES \
         and math.isfinite(self._ctl_i) and math.isfinite(self._roll):
        cell.add_ctl(self._ctl_i, self._roll)
    except Exception:
      self._die()

  def total(self) -> int:
    return sum(c.n for c in self.cells.values())

  def tick(self, main_factor: float = float("nan"), window=None) -> None:
    """Once per torqued loop. Logs every LOG_INTERVAL frames, if any point arrived since the last line."""
    if self.dead:
      return
    try:
      if window is not None:
        self.window = (float(window[0]), float(window[1]))
      self.frame += 1
      if self.frame % LOG_INTERVAL != 0:
        return
      n = self.total()
      if n == self._logged_n:
        return
      self._logged_n = n
      self.lines += 1
      cloudlog.info(self.line(float(main_factor)))
    except Exception:
      self._die()

  def build(self) -> tuple[str, str]:
    """(commit, board) of the running build, '-' where unknown. Read at every line: card writes the board's hash
    when it decodes this drive's first 0x707 (once a minute)."""
    try:
      if self._params is None:
        from openpilot.common.params import Params
        self._params = Params()
      return _tag(self._params.get("GitCommit"), 9), _tag(self._params.get("EpsLkasBoardVersion"), 8)
    except Exception:
      return "-", "-"

  def line(self, main_factor: float = float("nan")) -> str:
    def fl(vals, spec):
      return "[" + ",".join("nan" if not math.isfinite(x) else format(x, spec) for x in vals) + "]"

    commit, board = self.build()
    fits = [self.cells[c].fit() for c in CELLS]
    parts = [f"{LOG_TAG} v={LOG_VERSION} commit={commit} board={board} prior={self.prior:.3f}",
             f"win={fl(self.window, '.3f')}",
             f"main={main_factor:.3f}" if math.isfinite(main_factor) else "main=nan",
             f"bands={fl([int(b) for b in BANDS], 'd')} hands={fl([HANDS_QUIET, HANDS_TOUCH], 'd')}",
             f"n={fl([self.cells[c].n for c in CELLS], 'd')} nc={fl([self.cells[c].cn for c in CELLS], 'd')} nx={self.unclassified}",
             f"fac={fl([f[0] if f else float('nan') for f in fits], '.3f')}"]
    for h, b in CELLS:
      cell = self.cells[(h, b)]
      if cell.n:
        parts.append(f"m_{h}_{b}={fl(cell.moments(), '.7g')} b_{h}_{b}={fl(cell.bins, 'd')} c_{h}_{b}={fl(cell.ctl(), '.7g')}")
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
