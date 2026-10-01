"""
FORK(HONDA_ACCORD_9G_AU): torqued on the Elesys Accord.

* The car has its own prior (opendbc override.toml, [1.1, 1.1, 0.18]) and seeds its lateral-accel offset at
  -0.43 (opendbc interface.py). torqued starts its offset from CP whenever it has no valid cache, so the
  one-time cache reset the new prior forces does not throw away the ~0.43 m/s^2 of crossfall the car carries.
* A prior that changes is a restore key that changes: the cache learned under the old prior is discarded.
* CarController reports 0 torque while the gateway board is not actuating, and a 0 never becomes a point.
"""
import numpy as np

from openpilot.cereal import messaging
from opendbc.car.structs import car
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd.torqued import TorqueEstimator, VERSION, MIN_VEL

ELESYS = HONDA.HONDA_ACCORD_9G_AU
OLD_PRIOR = (1.6893333799149202, 0.2120497022936265)   # HONDA_ACCORD's LAF / friction, the substitute until 2026-10


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
    self.assertAlmostEqual(ltp.latAccelFactorFiltered, 1.1, places=6)
    self.assertAlmostEqual(ltp.frictionCoefficientFiltered, 0.18, places=6)
    # the learnable window is centred on the car's own prior, not on HONDA_ACCORD's 1.18-2.20
    self.assertAlmostEqual(est.min_lataccel_factor, 0.77, places=5)
    self.assertAlmostEqual(est.max_lataccel_factor, 1.43, places=5)

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
    # the cache was learned under the HONDA_ACCORD substitute; the new prior is a new restore key
    self._cache(_cp(prior=OLD_PRIOR))
    est = TorqueEstimator(_cp().as_reader())
    ltp = _ltp(est)
    self.assertEqual(ltp.totalBucketPoints, 0)
    self.assertAlmostEqual(ltp.latAccelFactorFiltered, 1.1, places=6)
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
    self.assertNotEqual(TorqueEstimator.get_restore_key(_cp(prior=OLD_PRIOR), VERSION),
                        TorqueEstimator.get_restore_key(_cp(), VERSION))
