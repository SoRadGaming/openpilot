"""
FORK(HONDA_ACCORD_9G_AU): torqued on the Elesys Accord.

* The car has its own prior (opendbc override.toml, [1.25, 1.25, 0.18]; [1.1, 1.1, 0.18] until 2026-10-03) and
  seeds its lateral-accel offset at -0.43 (opendbc interface.py). torqued starts its offset from CP whenever it
  has no valid cache, so the one-time cache reset a new prior forces does not throw away the ~0.43 m/s^2 of
  crossfall the car carries.
* A prior that changes is a restore key that changes: the cache learned under the old prior is discarded.
* CarController reports 0 torque while the gateway board is not actuating, and a 0 never becomes a point.
* The seed also survives sunnypilot's EnforceTorqueControl / NNLC re-run of configure_torque_tune().
"""
import numpy as np

from openpilot.cereal import messaging
from opendbc.car.structs import car
from opendbc.car.interfaces import CarInterfaceBase
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd.torqued import TorqueEstimator, VERSION, MIN_VEL, STEER_BUCKET_BOUNDS
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces

ELESYS = HONDA.HONDA_ACCORD_9G_AU
OLD_PRIOR = (1.6893333799149202, 0.2120497022936265)   # HONDA_ACCORD's factor / friction, the substitute until 2026-10
BATCH1_PRIOR = (1.1, 0.18)                              # the car's own first prior, until 2026-10-03


def _cp(car_name=ELESYS, offset=None, prior=None):
  CP = CarInterface.get_non_essential_params(car_name)
  if offset is not None:
    CP.lateralTuning.torque.latAccelOffset = offset
  if prior is not None:
    CP.lateralTuning.torque.latAccelFactor, CP.lateralTuning.torque.friction = prior
  return CP


def _ltp(est):
  return est.get_msg().lateralTorqueParameters


def _feed(est, reported_torque: float, seconds: float = 12.0, v_ego: float = 25.0):
  """latActive, no override, above MIN_VEL, a gentle constant curve; carOutput reports `reported_torque`."""
  assert v_ego > MIN_VEL
  carControl = messaging.new_message('carControl').carControl
  carOutput = messaging.new_message('carOutput').carOutput
  carState = messaging.new_message('carState').carState
  deviceMotion = messaging.new_message('deviceMotion').deviceMotion
  carControl.latActive = True
  carState.vEgo = v_ego
  carState.steeringPressed = False
  carOutput.actuatorsOutput.torque = float(reported_torque)
  for t in DT_MDL * np.arange(int(seconds / DT_MDL)):
    deviceMotion.orientationNED = {'x': 0.0, 'valid': True}
    deviceMotion.angularVelocityDevice = {'z': float(0.3 / v_ego), 'valid': True}
    deviceMotion.inputsOK, deviceMotion.sensorsOK, deviceMotion.posenetOK = True, True, True
    deviceMotion.timestamp = int(t * 1e9)
    for which, msg in (('carControl', carControl), ('carOutput', carOutput), ('carState', carState), ('deviceMotion', deviceMotion)):
      est.handle_log(float(t), which, msg)


class TestTorquedElesysPrior(OpenpilotTestCase):
  def test_prior_and_offset_are_published_before_any_point(self):
    est = TorqueEstimator(_cp().as_reader())
    ltp = _ltp(est)
    self.assertEqual(ltp.totalBucketPoints, 0)
    self.assertAlmostEqual(ltp.latAccelOffsetFiltered, -0.43, places=6)
    self.assertAlmostEqual(ltp.latAccelFactorFiltered, 1.25, places=6)
    self.assertAlmostEqual(ltp.frictionCoefficientFiltered, 0.18, places=6)
    # the learnable window is the car's own prior's (not HONDA_ACCORD's 1.18-2.20, not the 1.1 prior's 0.77-1.43), with
    # this car's floor (round 8): 1.10 instead of 0.875
    self.assertAlmostEqual(est.min_lataccel_factor, 1.10, places=5)
    self.assertAlmostEqual(est.max_lataccel_factor, 1.625, places=5)

  def test_a_zero_offset_starts_where_upstream_does(self):
    # every other car: configure_torque_tune() leaves 0.0, which is exactly upstream's hard-coded start
    for CP in (_cp(offset=0.0), _cp(HONDA.HONDA_ACCORD_11G), car.CarParams.new_message()):
      est = TorqueEstimator(CP.as_reader())
      self.assertEqual(_ltp(est).latAccelOffsetFiltered, 0.0)
      self.assertEqual(est.filtered_params['latAccelOffset'].x, 0.0)

  def test_the_offset_seed_moves_like_any_offset_once_points_arrive(self):
    # the seed is only the filter's starting point: the same points pull both estimators the same way
    a, b = TorqueEstimator(_cp().as_reader()), TorqueEstimator(_cp(offset=0.0).as_reader())
    for est in (a, b):
      for _ in range(50):
        est.update_params({'latAccelFactor': 1.2, 'latAccelOffset': -0.40, 'frictionCoefficient': 0.18})
    oa, ob = _ltp(a).latAccelOffsetFiltered, _ltp(b).latAccelOffsetFiltered
    self.assertTrue(-0.43 < oa < -0.40, msg=f"{oa}")    # from the seed, a small rise toward -0.40
    self.assertTrue(-0.40 < ob < 0.0, msg=f"{ob}")      # from 0, a long fall toward -0.40
    self.assertLess(abs(oa + 0.40), abs(ob + 0.40))


class TestTorquedElesysReportedTorque(OpenpilotTestCase):
  def test_reported_zero_adds_no_point(self):
    # CarController reports 0.0 while the gateway is not actuating; latActive is still true
    est = TorqueEstimator(_cp().as_reader())
    _feed(est, reported_torque=0.0)
    self.assertEqual(len(est.filtered_points), 0)

  def test_reported_torque_adds_points(self):
    # the control: the same drive with the board actuating does collect points
    est = TorqueEstimator(_cp().as_reader())
    _feed(est, reported_torque=-0.25)
    self.assertGreater(len(est.filtered_points), 0)


class TestTorquedElesysCache(OpenpilotTestCase):
  def _cache(self, prev_CP, valid=True):
    msg = messaging.new_message('lateralTorqueParameters')
    ltp = msg.lateralTorqueParameters
    ltp.version = VERSION
    ltp.valid = valid
    ltp.latAccelFactorFiltered = 1.1833
    ltp.latAccelOffsetFiltered = -0.45
    ltp.frictionCoefficientFiltered = 0.19
    ltp.decay = 120.0
    ltp.points = [[0.1, 0.12], [-0.1, -0.13], [0.25, 0.3]]
    params = Params()
    params.put("CarParamsPrevRoute", prev_CP.to_bytes(), block=True)
    params.put("LiveTorqueParameters", msg.to_bytes(), block=True)

  def test_a_changed_prior_discards_the_cache(self):
    # a cache learned under the HONDA_ACCORD substitute or under the 1.1 prior; the new prior is a new restore key
    for old in (OLD_PRIOR, BATCH1_PRIOR):
      with self.subTest(old=old):
        self._cache(_cp(prior=old))
        est = TorqueEstimator(_cp().as_reader())
        ltp = _ltp(est)
        self.assertEqual(ltp.totalBucketPoints, 0)
        self.assertAlmostEqual(ltp.latAccelFactorFiltered, 1.25, places=6)
        self.assertAlmostEqual(ltp.latAccelOffsetFiltered, -0.43, places=6)   # the seed, not the cache's -0.45
        self.assertAlmostEqual(ltp.frictionCoefficientFiltered, 0.18, places=6)

  def test_the_same_prior_restores_the_cache(self):
    # the control: with an unchanged prior the cache is used, offset included
    self._cache(_cp())
    est = TorqueEstimator(_cp().as_reader())
    ltp = _ltp(est)
    self.assertEqual(ltp.totalBucketPoints, 3)
    self.assertAlmostEqual(ltp.latAccelFactorFiltered, 1.1833, places=4)
    self.assertAlmostEqual(ltp.latAccelOffsetFiltered, -0.45, places=4)

  def test_an_invalid_cache_keeps_its_points_and_starts_from_the_seed(self):
    self._cache(_cp(), valid=False)
    est = TorqueEstimator(_cp().as_reader())
    ltp = _ltp(est)
    self.assertEqual(ltp.totalBucketPoints, 3)
    self.assertAlmostEqual(ltp.latAccelOffsetFiltered, -0.43, places=6)

  def test_restore_key_includes_the_prior(self):
    for old in (OLD_PRIOR, BATCH1_PRIOR):
      self.assertNotEqual(TorqueEstimator.get_restore_key(_cp(prior=old), VERSION),
                          TorqueEstimator.get_restore_key(_cp(), VERSION))


def _ltp_cache(prev_CP, factor, points, decay=250.0, valid=True):
  """LiveTorqueParameters and CarParamsPrevRoute as torqued's main() leaves them for the next drive."""
  msg = messaging.new_message('lateralTorqueParameters')
  ltp = msg.lateralTorqueParameters
  ltp.version = VERSION
  ltp.valid = valid
  ltp.latAccelFactorFiltered = factor
  ltp.latAccelOffsetFiltered = -0.44
  ltp.frictionCoefficientFiltered = 0.22
  ltp.decay = decay
  ltp.points = [[float(x), float(y)] for x, y in points]
  params = Params()
  params.put("CarParamsPrevRoute", prev_CP.to_bytes(), block=True)
  params.put("LiveTorqueParameters", msg.to_bytes(), block=True)


def _hands_on_points(slope=0.8, seed=11):
  """A full, valid bucket set like the one the car carried into 13d: every bucket at its 1500 cap, raw slope ~0.8."""
  rng = np.random.default_rng(seed)
  pts = []
  for lo, hi in STEER_BUCKET_BOUNDS:
    x = rng.uniform(lo, hi, 1500)
    pts += list(zip(x, slope * x - 0.44 + rng.normal(0, 0.03, x.size), strict=True))
  return pts


class TestTorquedElesysFloor(OpenpilotTestCase):
  """Round 8, proposal 6: torqued's factor floor on this car is 1.10, not 0.875 (torque_floor.py). The prior, the
  ceiling, the restore key and the cache are untouched: a lower raw estimate is clipped, and the restored value slides."""

  def test_the_window_on_this_car_and_nowhere_else(self):
    est = TorqueEstimator(_cp().as_reader())
    self.assertEqual((round(est.min_lataccel_factor, 6), round(est.max_lataccel_factor, 6)), (1.10, 1.625))
    self.assertAlmostEqual(est.min_friction, 0.5 * 0.18, places=6)                      # friction window unchanged
    self.assertAlmostEqual(est.max_friction, 1.5 * 0.18, places=6)
    est = TorqueEstimator(_cp().as_reader(), decimated=True)     # qlog estimator: upstream's 0.5, so 0.625 -> 1.10
    self.assertEqual((round(est.min_lataccel_factor, 6), round(est.max_lataccel_factor, 6)), (1.10, 1.875))
    for car_name in (HONDA.HONDA_ACCORD_11G, HONDA.HONDA_CIVIC):
      with self.subTest(car=car_name):
        CP = CarInterface.get_non_essential_params(car_name)
        est = TorqueEstimator(CP.as_reader())
        self.assertEqual(est.min_lataccel_factor, (1.0 - 0.3) * est.offline_latAccelFactor)   # upstream, to the bit
        self.assertEqual(est.max_lataccel_factor, (1.0 + 0.3) * est.offline_latAccelFactor)
    est = TorqueEstimator(car.CarParams.new_message())
    self.assertEqual((est.min_lataccel_factor, est.max_lataccel_factor), (0.0, 0.0))

  def test_the_floor_only_raises_and_never_passes_the_prior(self):
    for prior, want in ((1.25, 1.10), (1.0, 1.0), (1.4, 1.10), (1.6, 0.7 * 1.6)):
      with self.subTest(prior=prior):
        est = TorqueEstimator(_cp(prior=(prior, 0.18)).as_reader())
        self.assertAlmostEqual(est.min_lataccel_factor, want, places=6)     # CarParams floats are float32
        self.assertAlmostEqual(est.max_lataccel_factor, 1.3 * prior, places=6)
        self.assertLessEqual(est.min_lataccel_factor, prior)

  def test_the_relaxed_toggle_keeps_sunnypilots_window(self):
    params = Params()
    params.put_bool("EnforceTorqueControl", True, block=True)
    params.put_bool("LiveTorqueParamsRelaxedToggle", True, block=True)
    est = TorqueEstimator(_cp().as_reader())
    self.assertEqual(est.factor_sanity, 1.0)
    self.assertEqual((est.min_lataccel_factor, est.max_lataccel_factor), (0.0, 2.5))

  def test_without_the_module_torqued_keeps_upstreams_window(self):
    import importlib.util
    import sys
    from unittest import mock
    import openpilot.selfdrive.locationd.torqued as torqued
    with mock.patch.dict(sys.modules, {'openpilot.sunnypilot.selfdrive.locationd.torque_floor': None}):
      spec = importlib.util.spec_from_file_location('torqued_without_floor', torqued.__file__)
      assert spec is not None and spec.loader is not None
      mod = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(mod)
    est = mod.TorqueEstimator(_cp().as_reader())
    self.assertAlmostEqual(est.min_lataccel_factor, 0.875, places=9)

  def test_the_old_builds_cache_is_kept_and_slides_up(self):
    # 13d: the cache the 0.875 build wrote (0.883 filtered, 12000 points, decay 250) under the same restore key
    self.assertEqual(TorqueEstimator.get_restore_key(_cp(), VERSION), TorqueEstimator.get_restore_key(_cp().as_reader(), VERSION))
    _ltp_cache(_cp(), 0.883, _hands_on_points())
    est = TorqueEstimator(_cp().as_reader())
    x0 = est.filtered_params['latAccelFactor'].x
    self.assertAlmostEqual(x0, 0.883, places=6)                               # restored as it was, not clipped to 1.10
    self.assertEqual(len(est.filtered_points), 12000)
    self.assertEqual((est.decay, est.resets), (250.0, 1.0))                   # the one reset every __init__ does
    alpha = DT_MDL / (250.0 + DT_MDL)
    n = 1200                                                                  # 5 minutes of 4 Hz updates
    for k in range(n):
      ltp = _ltp(est)
      self.assertTrue(ltp.valid)
      self.assertLess(ltp.latAccelFactorRaw, 0.875)                           # the raw estimate is the points', ~0.8
      if k == 0:
        self.assertLess(ltp.latAccelFactorFiltered - x0, 1e-4)               # a slide, not a step
    # every update took the clipped 1.10: the closed form of torqued's FirstOrderFilter, to the bit
    self.assertAlmostEqual(est.filtered_params['latAccelFactor'].x, 1.10 - (1.10 - x0) * (1 - alpha) ** n, places=9)
    self.assertEqual(ltp.maxResets, 1.0)                                      # clipped, never reset
    self.assertEqual(ltp.totalBucketPoints, 12000)
    self.assertAlmostEqual(ltp.latAccelOffsetFiltered, -0.44, delta=0.005)   # offset and friction learn as before

  def test_the_filter_settles_on_the_floor_and_moves_off_it_for_a_higher_raw(self):
    _ltp_cache(_cp(), 1.10, _hands_on_points(slope=1.45))
    est = TorqueEstimator(_cp().as_reader())
    for _ in range(400):
      ltp = _ltp(est)
    self.assertGreater(ltp.latAccelFactorRaw, 1.10)
    self.assertGreater(ltp.latAccelFactorFiltered, 1.10 + 0.01)              # above the floor the learner is upstream's


class TestTorquedElesysSeedWithTorqueToggles(OpenpilotTestCase):
  """With EnforceTorqueControl or NNLC on, sunnypilot's setup_interfaces() re-runs configure_torque_tune(), which
  resets latAccelOffset to 0.0. Its FORK(HONDA_ACCORD_9G_AU) hunk keeps the car's seed across the re-run; every other
  car ends exactly where upstream's re-run leaves it."""

  @staticmethod
  def _setup(car_name, enforce, nnlc):
    params = Params()
    params.put_bool("EnforceTorqueControl", enforce, block=True)
    params.put_bool("NeuralNetworkLateralControl", nnlc, block=True)
    CP = CarInterface.get_non_essential_params(car_name)
    CI = CarInterface(CP, CarInterface.get_non_essential_params_sp(CP, car_name))
    sunnypilot_interfaces.setup_interfaces(CI, params)
    return CI.CP

  def test_the_seed_survives_the_re_run(self):
    for enforce, nnlc in ((True, False), (False, True), (True, True), (False, False)):
      with self.subTest(enforce=enforce, nnlc=nnlc):
        CP = self._setup(ELESYS, enforce, nnlc)
        self.assertEqual(CP.lateralTuning.which(), 'torque')
        self.assertAlmostEqual(CP.lateralTuning.torque.latAccelOffset, -0.43, places=6)
        self.assertAlmostEqual(CP.lateralTuning.torque.latAccelFactor, 1.25, places=6)
        self.assertAlmostEqual(CP.lateralTuning.torque.friction, 0.18, places=6)
        ltp = _ltp(TorqueEstimator(CP.as_reader()))
        self.assertEqual(ltp.totalBucketPoints, 0)
        self.assertAlmostEqual(ltp.latAccelOffsetFiltered, -0.43, places=6)

  def test_other_cars_end_where_upstream_leaves_them(self):
    # a torque-tuned and a PID-tuned Honda: upstream's re-run is a bare configure_torque_tune() on the car's CarParams
    for car_name in (HONDA.HONDA_ACCORD_11G, HONDA.HONDA_CIVIC):
      with self.subTest(car=car_name):
        want = CarInterface.get_non_essential_params(car_name)
        CarInterfaceBase.configure_torque_tune(car_name, want.lateralTuning)
        CP = self._setup(car_name, enforce=True, nnlc=False)
        self.assertEqual(CP.lateralTuning.which(), 'torque')
        self.assertEqual(CP.lateralTuning.to_dict(), want.lateralTuning.to_dict())
        self.assertEqual(CP.lateralTuning.torque.latAccelOffset, 0.0)
