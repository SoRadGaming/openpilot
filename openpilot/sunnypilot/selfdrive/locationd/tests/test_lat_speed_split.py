"""
FORK(HONDA_ACCORD_9G_AU): the shadow speed-split lateral factor (lat_speed_split.py) beside torqued.

* Its fit IS torqued's fit: the moment-based total least squares equals estimate_params() on the same points.
* It changes nothing torqued publishes: lateralTorqueParameters is identical with the shadow and without it.
* It splits at 70 km/h, sees only the points torqued accepts, and recovers a different factor on each side.
* It runs on the Elesys Accord's live torqued only, never raises, and logs once a minute, not per point.
"""
import math
from unittest import mock

import numpy as np

from openpilot.cereal import messaging
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
import openpilot.selfdrive.locationd.torqued as torqued
from openpilot.selfdrive.locationd.torqued import TorqueEstimator
import openpilot.sunnypilot.selfdrive.locationd.lat_speed_split as ls

ELESYS = HONDA.HONDA_ACCORD_9G_AU


def _cp(car_name=ELESYS):
  return CarInterface.get_non_essential_params(car_name)


def _drive(est, segments, seed=0):
  """segments: [(seconds, v_ego, steer, factor)] -- lateral accel = factor * steer + offset, plus noise."""
  rng = np.random.default_rng(seed)
  carControl = messaging.new_message('carControl').carControl
  carOutput = messaging.new_message('carOutput').carOutput
  carState = messaging.new_message('carState').carState
  deviceMotion = messaging.new_message('deviceMotion').deviceMotion
  carControl.latActive = True
  carState.steeringPressed = False
  t = 0.0
  for seconds, v_ego, steer_amp, factor in segments:
    carState.vEgo = v_ego
    for _ in range(int(seconds / DT_MDL)):
      steer = float(steer_amp * math.sin(t * 0.7) + rng.normal(0, 0.01))
      carOutput.actuatorsOutput.torque = -steer      # torqued negates it back
      lat = factor * steer - 0.4 + rng.normal(0, 0.05)
      deviceMotion.orientationNED = {'x': 0.0, 'valid': True}
      deviceMotion.angularVelocityDevice = {'z': float(lat / v_ego), 'valid': True}
      deviceMotion.inputsOK, deviceMotion.sensorsOK, deviceMotion.posenetOK = True, True, True
      deviceMotion.timestamp = int(t * 1e9)
      for which, msg in (('carControl', carControl), ('carOutput', carOutput), ('carState', carState), ('deviceMotion', deviceMotion)):
        est.handle_log(t, which, msg)
      if est.lat_split is not None:
        est.lat_split.tick(est.filtered_params['latAccelFactor'].x)
      t += DT_MDL


class TestLatSpeedSplit(OpenpilotTestCase):
  def test_constants_are_torqueds(self):
    self.assertEqual(ls.STEER_BUCKET_BOUNDS, torqued.STEER_BUCKET_BOUNDS)
    self.assertEqual(ls.MIN_BUCKET_POINTS, torqued.MIN_BUCKET_POINTS.tolist())
    self.assertEqual(ls.MIN_POINTS_TOTAL, torqued.MIN_POINTS_TOTAL)
    self.assertEqual(ls.FACTOR_SANITY, torqued.FACTOR_SANITY)
    self.assertEqual(ls.FRICTION_FACTOR, torqued.FRICTION_FACTOR)
    self.assertAlmostEqual(ls.DT, DT_MDL)

  def test_moment_fit_equals_torqueds_svd(self):
    rng = np.random.default_rng(3)
    est = TorqueEstimator(_cp())
    for _ in range(3):
      x = rng.uniform(-0.45, 0.45, 3000)
      y = 1.3 * x - 0.4 + rng.normal(0, 0.08, x.size) + 0.15 * np.sign(rng.normal(size=x.size))
      half = ls.SplitHalf()
      est.filtered_points = torqued.TorqueBuckets(x_bounds=torqued.STEER_BUCKET_BOUNDS, min_points=[0] * 8, min_points_total=0,
                                                  points_per_bucket=10 ** 6, rowsize=3)
      est.fit_points = 10 ** 6       # every point, so torqued's random subsample is the whole set
      for xi, yi in zip(x, y, strict=True):
        half.add(float(xi), float(yi))
        est.filtered_points.add_point(float(xi), float(yi))
      slope, offset, friction = est.estimate_params()
      mine = half.fit()
      self.assertAlmostEqual(mine[0], slope, places=6)
      self.assertAlmostEqual(mine[1], offset, places=6)
      self.assertAlmostEqual(mine[2], friction, places=6)

  def test_moments_combine_exactly_across_drives(self):
    rng = np.random.default_rng(4)
    a, b, both = ls.SplitHalf(), ls.SplitHalf(), ls.SplitHalf()
    for i in range(4000):
      x, y = float(rng.uniform(-0.4, 0.4)), float(rng.normal())
      (a if i % 3 else b).add(x, y)
      both.add(x, y)
    summed = [p + q for p, q in zip(a.moments(), b.moments(), strict=True)]
    for p, q in zip(ls.tls_fit(*summed), both.fit(), strict=True):
      self.assertAlmostEqual(p, q, places=9)

  def test_splits_at_70_and_recovers_each_sides_factor(self):
    est = TorqueEstimator(_cp())
    self.assertIsNotNone(est.lat_split)
    _drive(est, [(200, 16.0, 0.35, 0.7), (200, 25.0, 0.35, 1.45)])
    lo, hi = est.lat_split.halves['lo'], est.lat_split.halves['hi']
    self.assertGreater(lo.n, 1000)
    self.assertGreater(hi.n, 1000)
    self.assertAlmostEqual(lo.fit()[0], 0.7, delta=0.05)
    self.assertAlmostEqual(hi.fit()[0], 1.45, delta=0.08)
    self.assertAlmostEqual(lo.fit()[1], -0.4, delta=0.03)
    self.assertEqual(lo.n + hi.n, len(est.filtered_points))   # every point torqued kept, no more (no bucket is full here)
    self.assertAlmostEqual(ls.interpolated_factor(10.0, 0.7, 1.45), 0.7)
    self.assertAlmostEqual(ls.interpolated_factor(70 / 3.6, 0.7, 1.45), (0.7 + 1.45) / 2)
    self.assertAlmostEqual(ls.interpolated_factor(30.0, 0.7, 1.45), 1.45)

  def test_only_points_torqued_accepts(self):
    est = TorqueEstimator(_cp())
    _drive(est, [(60, torqued.MIN_VEL - 1.0, 0.3, 1.0)])      # below torqued's MIN_VEL: torqued keeps none
    self.assertEqual(len(est.filtered_points), 0)
    self.assertEqual(est.lat_split.halves['lo'].n + est.lat_split.halves['hi'].n, 0)

  def test_publishes_exactly_what_torqued_publishes_without_it(self):
    msgs = {}
    for shadow in (True, False):
      with mock.patch.object(np.random, 'default_rng', lambda *a, **k: np.random.Generator(np.random.PCG64(7))):
        est = TorqueEstimator(_cp())
      if not shadow:
        est.lat_split = None
      _drive(est, [(200, 17.0, 0.35, 0.8), (300, 26.0, 0.3, 1.4)], seed=5)
      # the payload, not the envelope: new_message() stamps logMonoTime with the wall clock
      msgs[shadow] = est.get_msg(with_points=True).lateralTorqueParameters.to_dict()
    self.assertEqual(msgs[True], msgs[False])

  def test_only_on_the_elesys_live_estimator(self):
    self.assertIsNotNone(TorqueEstimator(_cp()).lat_split)
    self.assertIsNone(TorqueEstimator(_cp(), decimated=True).lat_split)
    self.assertIsNone(TorqueEstimator(_cp(HONDA.HONDA_CIVIC)).lat_split)

  def test_log_cadence_and_line(self):
    lines = []
    with mock.patch.object(ls.cloudlog, 'info', lambda msg, *a, **k: lines.append(msg)):
      est = TorqueEstimator(_cp())
      _drive(est, [(150, 25.0, 0.3, 1.4)])            # 150 s of points: two lines, at 60 s and 120 s
      self.assertEqual(len(lines), 2)
      for _ in range(3 * ls.LOG_INTERVAL):               # the points after 120 s go out at the next minute...
        est.lat_split.tick(1.0)
      self.assertEqual(len(lines), 3)                    # ...and with nothing new, nothing more
      self.assertNotEqual(lines[-1], lines[-2])
    line = lines[-1]
    self.assertTrue(line.startswith('latsplit v=1 '))
    self.assertLess(len(line), 800)
    d = dict(tok.split('=', 1) for tok in line.split()[1:])
    for k in ('n', 'fac', 'clip', 'off', 'fric', 'cal', 'valid', 'mom_lo', 'mom_hi', 'bins_lo', 'bins_hi'):
      self.assertTrue(d[k].startswith('['), k)
    self.assertEqual(d['fac'].split(',')[0], '[nan')    # no low-speed points: reported as nan, not 0

  def test_never_raises(self):
    sh = ls.LatSpeedSplitShadow(1.25)
    sh.add_point(float('nan'), 0.1, 0.1)
    sh.add_point('x', 0.1, 0.1)  # ty: ignore[invalid-argument-type]  # garbage: switched off, not raised
    self.assertTrue(sh.dead)
    sh.add_point(25.0, 0.1, 0.1)
    sh.tick(1.0)
    self.assertEqual(sh.halves['hi'].n, 0)
    sh = ls.LatSpeedSplitShadow(1.25)
    with mock.patch.object(ls, 'tls_fit', side_effect=np.linalg.LinAlgError):
      for _ in range(ls.MIN_FIT_POINTS):
        sh.add_point(25.0, 0.1, 0.1)
      for _ in range(ls.LOG_INTERVAL):
        sh.tick(1.0)
    self.assertTrue(sh.dead)
