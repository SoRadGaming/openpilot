"""
FORK(HONDA_ACCORD_9G_AU): the steering-delay fallbacks on the Elesys Accord land on the measured 0.38 s.

opendbc sets steerActuatorDelay 0.18 for HONDA_ELESYS. lagd's initial_lag is that plus 0.2, and with the
LagdToggle off LagdToggle.update() returns it plus LagdToggleDelay (default 0.2). Both used to be 0.58 s.
"""
import time

from openpilot.cereal import log, messaging
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.locationd.lagd import LateralLagEstimator, retrieve_initial_lag, VERSION
from openpilot.sunnypilot.livedelay.helpers import get_lat_delay
from openpilot.sunnypilot.livedelay.lagd_toggle import LagdToggle

ELESYS = HONDA.HONDA_ACCORD_9G_AU
DT = 0.05


def _cp(car_name=ELESYS):
  return CarInterface.get_non_essential_params(car_name).as_reader()


class TestLagdElesysFallback(OpenpilotTestCase):
  def test_initial_lag_is_the_measured_delay(self):
    est = LateralLagEstimator(_cp(), DT)
    self.assertAlmostEqual(est.initial_lag, 0.38, places=5)
    ld = est.get_msg(True).lateralDelay
    self.assertEqual(ld.status, log.LateralDelay.Status.unestimated)
    self.assertAlmostEqual(ld.lateralDelay, 0.38, places=5)
    self.assertAlmostEqual(ld.lateralDelayEstimate, 0.38, places=5)

  @staticmethod
  def _cached_delay(params, timeout=2.0):
    # LagdToggle writes LagdValueCache without blocking; wait for it to land
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      value = params.get("LagdValueCache")
      if value is not None:
        return float(value)
      time.sleep(0.01)
    return None

  def test_lagd_toggle_off_is_the_measured_delay(self):
    params = Params()
    params.put_bool("LagdToggle", False, block=True)
    params.remove("LagdValueCache")
    toggle = LagdToggle(_cp())
    toggle.update(messaging.new_message('lateralDelay'))
    self.assertAlmostEqual(toggle.lag, 0.38, places=5)
    # what torqued, modeld and controlsd_ext read through get_lat_delay() in this mode
    self.assertAlmostEqual(self._cached_delay(params), 0.38, places=5)
    self.assertAlmostEqual(get_lat_delay(params, 0.9), 0.38, places=5)

  def test_lagd_toggle_on_passes_the_learned_value_through(self):
    params = Params()
    params.put_bool("LagdToggle", True, block=True)
    msg = messaging.new_message('lateralDelay')
    msg.lateralDelay.lateralDelay = 0.342
    toggle = LagdToggle(_cp())
    toggle.update(msg)
    self.assertAlmostEqual(toggle.lag, 0.342, places=5)
    self.assertAlmostEqual(get_lat_delay(params, 0.342), 0.342, places=5)

  def test_a_learned_cache_survives_the_new_delay(self):
    # lagd's cache key is fingerprint + VERSION; the old CP (0.38) and the new one (0.18) share it
    old_CP = CarInterface.get_non_essential_params(ELESYS)
    old_CP.steerActuatorDelay = 0.38
    msg = messaging.new_message('lateralDelay')
    msg.lateralDelay.lateralDelayEstimate = 0.342
    msg.lateralDelay.validBlocks = 12
    msg.lateralDelay.status = log.LateralDelay.Status.estimated
    msg.lateralDelay.version = VERSION
    params = Params()
    params.put("CarParamsPrevRoute", old_CP.to_bytes(), block=True)
    params.put("LiveDelay", msg.to_bytes(), block=True)
    restored = retrieve_initial_lag(params, _cp())
    self.assertIsNotNone(restored)
    lag, valid_blocks = restored
    self.assertAlmostEqual(lag, 0.342, places=5)
    self.assertEqual(valid_blocks, 12)

  def test_other_cars_are_unchanged(self):
    # upstream's +0.2 on upstream's own value
    for car_name in (HONDA.HONDA_ACCORD, HONDA.HONDA_ACCORD_11G):
      CP = _cp(car_name)
      self.assertAlmostEqual(LateralLagEstimator(CP, DT).initial_lag, CP.steerActuatorDelay + 0.2, places=6)
      self.assertNotAlmostEqual(CP.steerActuatorDelay, 0.18, places=6)
