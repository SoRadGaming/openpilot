"""
FORK(HONDA_ACCORD_9G_AU): the shadow hands x speed split of torqued's lateral fit (lat_speed_split.py, latsplit v=2).

* Its fit IS torqued's fit: the moment-based total least squares equals estimate_params() on the same points.
* It changes nothing torqued publishes: lateralTorqueParameters is identical with the shadow and without it.
* It files each point torqued accepts by the driver's hands (under 300 / 300-1199 / 1200+ counts in torqued's own
  override window) and by speed (54-70 / 70-90 / 90+ km/h), and recovers a different factor in each cell.
* It keeps the controller's integrator and the roll term per cell, only while both are fresh.
* torqued's own SubMaster stays upstream's: the shadow's inputs come from a SubMaster of its own.
* It runs on the Elesys Accord's live torqued only, never raises, and logs once a minute, tagged with its build.
"""
import math
from unittest import mock

import numpy as np

from openpilot.cereal import messaging
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR as HONDA
from openpilot.common.constants import ACCELERATION_DUE_TO_GRAVITY
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
import openpilot.selfdrive.locationd.torqued as torqued
from openpilot.selfdrive.locationd.torqued import TorqueEstimator
import openpilot.sunnypilot.selfdrive.locationd.lat_speed_split as ls

ELESYS = HONDA.HONDA_ACCORD_9G_AU


def _cp(car_name=ELESYS):
  return CarInterface.get_non_essential_params(car_name)


def _drive(est, segments, seed=0, t0=0.0):
  """segments: [(seconds, v_ego, steer, factor, driver_torque)] -- lateral accel = factor * steer + offset, plus
  noise; carState.steeringTorque = driver_torque with steeringPressed False throughout (a hand torqued lets through)."""
  rng = np.random.default_rng(seed)
  carControl = messaging.new_message('carControl').carControl
  carOutput = messaging.new_message('carOutput').carOutput
  carState = messaging.new_message('carState').carState
  deviceMotion = messaging.new_message('deviceMotion').deviceMotion
  carControl.latActive = True
  carState.steeringPressed = False
  t = t0
  for seconds, v_ego, steer_amp, factor, driver in segments:
    carState.vEgo = v_ego
    carState.steeringTorque = float(driver)
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
        est.lat_split.tick(est.filtered_params['latAccelFactor'].x, (est.min_lataccel_factor, est.max_lataccel_factor))
      t += DT_MDL
  return t


class FakeShadowSM:
  """The shadow's SubMaster: controlsState and vehicleParameters, updated when the test says so."""
  def __init__(self):
    self.services = list(ls.SHADOW_SERVICES)
    self.updated = dict.fromkeys(self.services, False)
    self.next = {}
    self.data = {}
    self.timeouts = []

  def push(self, integrator=None, roll=None, active=True):
    if integrator is not None:
      cs = messaging.new_message('controlsState')
      cs.controlsState.lateralControlState.init('torqueState')
      cs.controlsState.lateralControlState.torqueState.active = active
      cs.controlsState.lateralControlState.torqueState.i = float(integrator)
      self.next['controlsState'] = cs.controlsState
    if roll is not None:
      vp = messaging.new_message('vehicleParameters')
      vp.vehicleParameters.roll = float(roll)
      self.next['vehicleParameters'] = vp.vehicleParameters

  def update(self, timeout=100):
    self.timeouts.append(timeout)
    self.updated = {s: s in self.next for s in self.services}
    self.data.update(self.next)
    self.next = {}

  def __getitem__(self, s):
    return self.data[s]


class TestLatSpeedSplit(OpenpilotTestCase):
  def test_constants_are_torqueds(self):
    self.assertEqual(ls.STEER_BUCKET_BOUNDS, torqued.STEER_BUCKET_BOUNDS)
    self.assertEqual(ls.MIN_BUCKET_POINTS, torqued.MIN_BUCKET_POINTS.tolist())
    self.assertEqual(ls.MIN_POINTS_TOTAL, torqued.MIN_POINTS_TOTAL)
    self.assertEqual(ls.FACTOR_SANITY, torqued.FACTOR_SANITY)
    self.assertEqual(ls.FRICTION_FACTOR, torqued.FRICTION_FACTOR)
    self.assertEqual(ls.MIN_ENGAGE_BUFFER, torqued.MIN_ENGAGE_BUFFER)
    self.assertAlmostEqual(ls.DT, DT_MDL)
    self.assertEqual(ls.HISTORY_LEN, TorqueEstimator(_cp()).hist_len)
    self.assertAlmostEqual(torqued.MIN_VEL * 3.6, float(ls.BANDS[0]))     # torqued's MIN_VEL is the first band's floor

  def test_moment_fit_equals_torqueds_svd(self):
    rng = np.random.default_rng(3)
    est = TorqueEstimator(_cp())
    for _ in range(3):
      x = rng.uniform(-0.45, 0.45, 3000)
      y = 1.3 * x - 0.4 + rng.normal(0, 0.08, x.size) + 0.15 * np.sign(rng.normal(size=x.size))
      cell = ls.Cell()
      est.filtered_points = torqued.TorqueBuckets(x_bounds=torqued.STEER_BUCKET_BOUNDS, min_points=[0] * 8, min_points_total=0,
                                                  points_per_bucket=10 ** 6, rowsize=3)
      est.fit_points = 10 ** 6       # every point, so torqued's random subsample is the whole set
      for xi, yi in zip(x, y, strict=True):
        cell.add(float(xi), float(yi))
        est.filtered_points.add_point(float(xi), float(yi))
      slope, offset, friction = est.estimate_params()
      mine = cell.fit()
      self.assertAlmostEqual(mine[0], slope, places=6)
      self.assertAlmostEqual(mine[1], offset, places=6)
      self.assertAlmostEqual(mine[2], friction, places=6)

  def test_moments_combine_exactly_across_drives(self):
    rng = np.random.default_rng(4)
    a, b, both = ls.Cell(), ls.Cell(), ls.Cell()
    for i in range(4000):
      x, y, ii, r = float(rng.uniform(-0.4, 0.4)), float(rng.normal()), float(rng.normal()), float(rng.normal())
      for c in ((a if i % 3 else b), both):
        c.add(x, y)
        c.add_ctl(ii, r)
    summed = [p + q for p, q in zip(a.moments(), b.moments(), strict=True)]
    for p, q in zip(ls.tls_fit(*summed), both.fit(), strict=True):
      self.assertAlmostEqual(p, q, places=9)
    summed = [p + q for p, q in zip(a.ctl(), b.ctl(), strict=True)]
    for p, q in zip(ls.ctl_stats(*summed), ls.ctl_stats(*both.ctl()), strict=True):
      self.assertAlmostEqual(p, q, places=9)

  def test_classes_and_bands(self):
    self.assertEqual([ls.hands_class(x) for x in (0, 299, 300, 1199, 1200, 5000)], ["off", "off", "on", "on", "hard", "hard"])
    self.assertEqual([ls.band_of(v / 3.6) for v in (54.0, 69.9, 70.0, 89.9, 90.0, 130.0)], ["54", "54", "70", "70", "90", "90"])
    self.assertEqual(len(ls.CELLS), 9)

  def test_cells_split_by_hands_and_speed_and_recover_each_factor(self):
    est = TorqueEstimator(_cp())
    self.assertIsNotNone(est.lat_split)
    segs = {("off", "54"): (16.5, 0.0, 1.6), ("on", "54"): (17.5, 516.0, 0.7), ("off", "70"): (22.0, 129.0, 1.5),
            ("on", "70"): (23.0, 387.0, 1.1), ("on", "90"): (27.0, 903.0, 1.25), ("hard", "90"): (28.0, 1290.0, 1.0)}
    segments = []
    for v, tq, f in segs.values():
      # 10 s below MIN_VEL first (torqued takes no point), so torqued's window holds only this segment's torque
      segments += [(10, 10.0, 0.3, f, tq), (200, v, 0.35, f, tq)]
    with mock.patch.object(est.filtered_points, 'add_point', wraps=est.filtered_points.add_point) as added:
      _drive(est, segments)
    sh = est.lat_split
    for cell, (_v, _tq, factor) in segs.items():
      c = sh.cells[cell]
      self.assertGreater(c.n, 3000, cell)
      self.assertAlmostEqual(c.fit()[0], factor, delta=0.08, msg=str(cell))
      self.assertAlmostEqual(c.fit()[1], -0.4, delta=0.03, msg=str(cell))
    for cell in set(ls.CELLS) - set(segs):
      self.assertEqual(sh.cells[cell].n, 0, cell)
    self.assertEqual(sh.unclassified, 0)
    self.assertEqual(sh.total(), added.call_count)            # every point torqued filed, no more

  def test_hands_come_from_torqueds_override_window(self):
    sh = ls.LatSpeedSplitShadow(1.25)
    for k in range(100):                                  # 5 s of carState at torqued's 20 Hz, lag-shifted clock
      sh.add_driver_torque(10.0 + 0.05 * k, 129.0)
    sh.add_driver_torque(15.0, -516.0)                    # one touch, the newest sample
    self.assertEqual(sh.driver_max(13.0, 15.0), 516.0)    # inside [t - MIN_ENGAGE_BUFFER, t + lag]: a hand on
    self.assertEqual(sh.driver_max(12.0, 14.0), 129.0)    # the touch is outside this window
    self.assertEqual(sh.driver_max(16.0, 17.0), 516.0)    # nothing inside: the nearest sample, as torqued's np.interp
    self.assertIsNone(ls.LatSpeedSplitShadow(1.25).driver_max(0.0, 1.0))
    sh.add_point(20.0, 0.1, 0.1, 13.0, 15.0)
    sh.add_point(20.0, 0.1, 0.1, 12.0, 14.0)
    self.assertEqual((sh.cells[("on", "70")].n, sh.cells[("off", "70")].n), (1, 1))
    empty = ls.LatSpeedSplitShadow(1.25)
    empty.add_point(20.0, 0.1, 0.1, 0.0, 1.0)
    self.assertEqual((empty.total(), empty.unclassified), (0, 1))

  def test_controller_sums_only_while_fresh(self):
    sh = ls.LatSpeedSplitShadow(1.25)
    sm = FakeShadowSM()
    for k in range(50):
      sh.add_driver_torque(k * 0.05, 0.0)
    sm.push(integrator=0.12, roll=0.05)
    sh.observe(sm)
    sh.add_point(20.0, 0.1, 0.2, 0.0, 2.5)
    c = sh.cells[("off", "70")]
    self.assertEqual(c.cn, 1)
    self.assertAlmostEqual(c.si, 0.12, places=6)
    self.assertAlmostEqual(c.sr, 0.05 * ACCELERATION_DUE_TO_GRAVITY, places=5)    # the controller's roll_compensation
    for _ in range(ls.CTL_FRESH_FRAMES):                  # the integrator goes stale after CTL_FRESH_FRAMES loops
      sh.observe(sm)
    sh.add_point(20.0, 0.1, 0.2, 0.0, 2.5)
    self.assertEqual(c.cn, 2)
    sh.observe(sm)
    sh.add_point(20.0, 0.1, 0.2, 0.0, 2.5)
    self.assertEqual((c.n, c.cn), (3, 2), "a stale integrator: the point is kept, the controller sums are not")
    sm.push(integrator=0.3, active=False)                 # the controller not steering: no integrator to keep
    sh.observe(sm)
    sh.add_point(20.0, 0.1, 0.2, 0.0, 2.5)
    self.assertEqual((c.n, c.cn), (4, 2))
    self.assertTrue(all(t == 0 for t in sm.timeouts), "the shadow's SubMaster is never allowed to block torqued")
    self.assertFalse(sh.dead)

  def test_ctl_stats(self):
    rng = np.random.default_rng(9)
    c = ls.Cell()
    for _ in range(2000):
      r = float(rng.normal(0.3, 0.2))
      c.add_ctl(0.05 - 0.4 * r + float(rng.normal(0, 0.01)), r)
    mi, si, mr, slope = ls.ctl_stats(*c.ctl())
    self.assertAlmostEqual(mr, 0.3, delta=0.02)
    self.assertAlmostEqual(mi, 0.05 - 0.4 * mr, delta=0.01)
    self.assertAlmostEqual(slope, -0.4, delta=0.02)
    self.assertTrue(all(math.isnan(x) for x in ls.ctl_stats(0, 0, 0, 0, 0, 0)))

  def test_only_points_torqued_accepts(self):
    est = TorqueEstimator(_cp())
    _drive(est, [(60, torqued.MIN_VEL - 1.0, 0.3, 1.0, 0.0)])      # below torqued's MIN_VEL: torqued keeps none
    self.assertEqual(len(est.filtered_points), 0)
    self.assertEqual(est.lat_split.total() + est.lat_split.unclassified, 0)

  def test_publishes_exactly_what_torqued_publishes_without_it(self):
    msgs = {}
    for shadow in (True, False):
      with mock.patch.object(np.random, 'default_rng', lambda *a, **k: np.random.Generator(np.random.PCG64(7))):
        est = TorqueEstimator(_cp())
      if not shadow:
        est.lat_split = None
      _drive(est, [(200, 17.0, 0.35, 0.8, 516.0), (300, 26.0, 0.3, 1.4, 0.0)], seed=5)
      # the payload, not the envelope: new_message() stamps logMonoTime with the wall clock
      msgs[shadow] = est.get_msg(with_points=True).lateralTorqueParameters.to_dict()
    self.assertEqual(msgs[True], msgs[False])

  def test_only_on_the_elesys_live_estimator(self):
    self.assertIsNotNone(TorqueEstimator(_cp()).lat_split)
    self.assertIsNone(TorqueEstimator(_cp(), decimated=True).lat_split)
    self.assertIsNone(TorqueEstimator(_cp(HONDA.HONDA_CIVIC)).lat_split)

  def test_torqued_runs_as_upstream_if_the_shadow_cannot_be_imported(self):
    # torqued's lateralTorqueParameters feed the controller: a broken shadow module must not take torqued down
    import importlib.util
    import sys
    name = 'openpilot.sunnypilot.selfdrive.locationd.lat_speed_split'
    with mock.patch.dict(sys.modules, {name: None}):     # None in sys.modules: the import raises ImportError
      spec = importlib.util.spec_from_file_location('torqued_without_shadow', torqued.__file__)
      assert spec is not None and spec.loader is not None
      mod = importlib.util.module_from_spec(spec)
      spec.loader.exec_module(mod)
    est = mod.TorqueEstimator(_cp())
    self.assertIsNone(est.lat_split)
    _drive(est, [(30, 25.0, 0.3, 1.4, 0.0)])
    self.assertGreater(len(est.filtered_points), 0)

  def test_log_cadence_and_line(self):
    params = Params()
    params.put("GitCommit", "55d9562b6aa3f00", block=True)
    params.put("EpsLkasBoardVersion", "883a3cd1", block=True)
    lines = []
    with mock.patch.object(ls.cloudlog, 'info', lambda msg, *a, **k: lines.append(msg)):
      est = TorqueEstimator(_cp())
      t = _drive(est, [(150, 24.0, 0.3, 1.4, 0.0)])          # 150 s of points: two lines, at 60 s and 120 s
      self.assertEqual(len(lines), 2)
      for _ in range(3 * ls.LOG_INTERVAL):                     # the points after 120 s go out at the next minute...
        est.lat_split.tick(1.0)
      self.assertEqual(len(lines), 3)                          # ...and with nothing new, nothing more
      self.assertNotEqual(lines[-1], lines[-2])
      params.put("EpsLkasBoardVersion", "c8221c29", block=True)            # card writes the board's hash when it sees 0x707
      # each segment after 10 s below MIN_VEL, so no point straddles two segments' hands
      _drive(est, [(10, 10.0, 0.3, 0.7, 516.0), (60, 17.0, 0.3, 0.7, 516.0), (10, 10.0, 0.3, 1.5, 1290.0),
                   (60, 27.0, 0.3, 1.5, 1290.0)], t0=t)
      for _ in range(ls.LOG_INTERVAL):                         # the minute after the drive: its last points go out
        est.lat_split.tick(1.0)
    line = lines[-1]
    self.assertTrue(line.startswith('latsplit v=2 commit=55d9562b6 board=c8221c29 prior=1.250 '), line[:80])
    self.assertLess(len(line), 2500)
    d = dict(tok.split('=', 1) for tok in line.split()[1:])
    for k in ('win', 'main', 'bands', 'hands', 'n', 'nc', 'fac', 'm_off_70', 'b_off_70', 'c_off_70', 'm_on_54', 'm_hard_90'):
      self.assertTrue(k in d, k)
    self.assertEqual(d['win'], f"[{est.min_lataccel_factor:.3f},{est.max_lataccel_factor:.3f}]")
    self.assertEqual(d['bands'], '[54,70,90]')
    self.assertEqual(d['hands'], '[300,1200]')
    self.assertNotIn('m_off_54', d)                          # an empty cell is left out of the line
    self.assertEqual(d['fac'].split(',')[0], '[nan')         # and its fit is nan, not 0
    n = [int(x) for x in d['n'].strip('[]').split(',')]
    self.assertEqual(sum(n), est.lat_split.total())
    self.assertEqual(d['nc'], '[' + ','.join(['0'] * 9) + ']')   # no controller inputs in this test

  def test_build_tags_without_params(self):
    sh = ls.LatSpeedSplitShadow(1.25, params=mock.Mock(get=mock.Mock(side_effect=RuntimeError)))
    self.assertEqual(sh.build(), ("-", "-"))
    sh = ls.LatSpeedSplitShadow(1.25, params=mock.Mock(get=mock.Mock(side_effect=[b"abc 123", None])))
    self.assertEqual(sh.build(), ("-", "-"))

  def test_the_shadow_submaster_never_blocks(self):
    sh = ls.LatSpeedSplitShadow(1.25)
    sm = sh.make_submaster()
    self.assertIsNotNone(sm)
    self.assertEqual(sorted(sm.services), sorted(ls.SHADOW_SERVICES))
    for _ in range(3):
      sh.observe(sm)                                         # nobody publishes: returns at once, nothing fresh
    self.assertFalse(sh.dead)
    self.assertGreater(sh._ctl_age, ls.CTL_FRESH_FRAMES)

  def test_main_keeps_torqueds_submaster_and_wires_the_shadow(self):
    # torqued's own SubMaster is created exactly as upstream's; the shadow observes its own one every loop and gets
    # torqued's window with each tick
    Params().put("CarParams", _cp().to_bytes(), block=True)
    made, observed, ticks = [], [], []

    class Stop(Exception):
      pass

    class FakeSM:
      def __init__(self, services, *args, **kwargs):
        made.append((list(services), args, kwargs))
        self.frame, self.updated, self.logMonoTime = -1, dict.fromkeys(services, False), dict.fromkeys(services, 0)

      def update(self, timeout=100):
        self.frame += 1
        if self.frame > 20:
          raise Stop

      def all_checks(self):
        return False

    with mock.patch.object(torqued.messaging, 'SubMaster', FakeSM), \
         mock.patch.object(torqued.messaging, 'PubMaster', mock.Mock()), \
         mock.patch.object(torqued, 'config_realtime_process', mock.Mock()), \
         mock.patch.object(ls.LatSpeedSplitShadow, 'make_submaster', lambda self: 'shadow-sm'), \
         mock.patch.object(ls.LatSpeedSplitShadow, 'observe', lambda self, sm: observed.append(sm)), \
         mock.patch.object(ls.LatSpeedSplitShadow, 'tick', lambda self, f, w=None: ticks.append(w)):
      with self.assertRaises(Stop):
        torqued.main()
    self.assertEqual(made, [(['carControl', 'carOutput', 'carState', 'extrinsicsCalibration', 'deviceMotion', 'lateralDelay'],
                             (), {'poll': 'deviceMotion'})])
    self.assertEqual(observed, ['shadow-sm'] * 21)
    self.assertEqual(len(ticks), 21)
    self.assertAlmostEqual(ticks[0][0], 1.10, places=6)
    self.assertAlmostEqual(ticks[0][1], 1.625, places=6)

  def test_never_raises(self):
    sh = ls.LatSpeedSplitShadow(1.25)
    sh.add_driver_torque(0.0, 0.0)
    sh.add_point(float('nan'), 0.1, 0.1, 0.0, 1.0)
    self.assertFalse(sh.dead)
    sh.add_point('x', 0.1, 0.1, 0.0, 1.0)  # ty: ignore[invalid-argument-type]  # garbage: switched off, not raised
    self.assertTrue(sh.dead)
    sh.add_point(25.0, 0.1, 0.1, 0.0, 1.0)
    sh.tick(1.0)
    self.assertEqual(sh.total(), 0)
    for bad in (lambda s: s.add_driver_torque('x', 1.0), lambda s: s.observe(object()), lambda s: s.tick(1.0, window=('a', 'b'))):
      s = ls.LatSpeedSplitShadow(1.25)
      bad(s)
      self.assertTrue(s.dead)
    sh = ls.LatSpeedSplitShadow(1.25)
    for k in range(ls.MIN_FIT_POINTS):
      sh.add_driver_torque(k * 0.05, 0.0)
    with mock.patch.object(ls, 'tls_fit', side_effect=np.linalg.LinAlgError):
      for _ in range(ls.MIN_FIT_POINTS):
        sh.add_point(25.0, 0.1, 0.1, 0.0, 20.0)
      for _ in range(ls.LOG_INTERVAL):
        sh.tick(1.0)
    self.assertTrue(sh.dead)
