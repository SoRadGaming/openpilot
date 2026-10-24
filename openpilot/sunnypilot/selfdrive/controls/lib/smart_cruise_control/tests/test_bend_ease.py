"""FORK(BENDS): ease off for bends this car's EPS can't hold (smart_cruise_control/bend_ease.py, push13).

TestBendEaseStep pins the rule on plain inputs: the start (1.0 m/s^2 at today's speed for 0.3 s, 55 km/h or faster),
the end (under 0.9 at the start speed for 1 s), the target (the brake curve to 1.1 m/s^2, never under 55 km/h, 15 km/h
off the start speed or vEgo - 0.6), and the gates (openpilot steering, its longitudinal engaged, the gas and 2 s after,
lane changes). TestBendEasePlanner drives the real LongitudinalPlanner (MPC included): shadow mode leaves every
planner output bit-identical to the planner without it, live mode eases at 0.6 m/s^2 at most through sccVision, and
every other car and stock ACC mode get no tune and publish nothing. The rest: the param, the service, the struct, the
sunnylink setting and plannerd's PubMaster.
"""
import math
import os
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import numpy as np
import yaml

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log, messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.basedir import BASEDIR
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control import bend_ease as be
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.bend_ease import BendEase, Mode, Reason, bend_tune, plan_curvature

LongCtrlState = structs.CarControl.Actuators.LongControlState
LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
T = np.array(ModelConstants.T_IDXS)
TUNE = be.ELESYS_TUNE
KPH = CV.KPH_TO_MS


def car_params(fingerprint=CAR.HONDA_ACCORD_9G_AU, brand='honda', op_long=True):
  CP = structs.CarParams()
  CP.brand, CP.carFingerprint, CP.openpilotLongitudinalControl = brand, fingerprint, op_long
  CP.steerRatio, CP.wheelbase, CP.longitudinalActuatorDelay = 15.0, 2.7, 0.6
  return CP


def sp_params(flags=0):
  CP_SP = custom.CarParamsSP.new_message()
  CP_SP.flags = flags
  return CP_SP.as_reader()


def bend(v: float, radius: float | None, at: float = 0.0):
  """The model's plan at a steady v: straight up to `at` m, then a curve of `radius` m (None: straight all the way)."""
  pos = v * T
  rate = np.full(len(T), 1e-4)
  if radius is not None:
    rate = np.where(pos >= at, v / radius, 1e-4)
  return rate, np.full(len(T), v), pos


def curv(v: float, radius: float | None, at: float = 0.0):
  rate, vel, pos = bend(v, radius, at)
  return plan_curvature(rate, vel, pos, TUNE.curv_min_v)


class Ease:
  """BendEase on plain inputs, in shadow mode by default (step() works out the target in either mode)."""

  def __init__(self, mode=Mode.SHADOW):
    self.b = BendEase(car_params(), sp_params())
    self.b.mode = mode
    self.b.params = None   # the tests set the mode themselves

  def run(self, n, v, radius=None, at=0.0, lat=True, long=True, gas=False, lc=False):
    c, d = curv(v, radius, at)
    return [self.b.step(v, lat, long, gas, lc, c, d) for _ in range(n)]


class TestBendTune(OpenpilotTestCase):
  def test_this_car_only(self):
    self.assertIs(bend_tune(car_params(), sp_params()), TUNE)
    self.assertIsNone(bend_tune(car_params(CAR.HONDA_CIVIC), sp_params()))
    self.assertIsNone(bend_tune(car_params(op_long=False), sp_params()))
    self.assertIsNone(bend_tune(car_params(brand='toyota'), sp_params()))
    self.assertIsNone(bend_tune(car_params(), sp_params(HondaFlagsSP.ELESYS_STOCK_ACC.value)), "stock ACC mode")
    self.assertIsNone(bend_tune(None, None))

  def test_constants(self):
    self.assertEqual((TUNE.start_lat, TUNE.end_lat, TUNE.target_lat), (1.0, 0.9, 1.1))
    self.assertEqual((TUNE.start_s, TUNE.end_s, TUNE.gas_hold_s, TUNE.lag_s), (0.3, 1.0, 2.0, 1.0))
    self.assertAlmostEqual(TUNE.min_speed * CV.MS_TO_KPH, 55.0)
    self.assertAlmostEqual(TUNE.max_cut * CV.MS_TO_KPH, 15.0)
    self.assertEqual((TUNE.decel, TUNE.plan_decel, TUNE.curv_min_v, TUNE.hold_rise, TUNE.accel_cap), (0.6, 0.4, 5.0, 0.3, 0.5))
    self.assertEqual(be.DEFAULT_MODE, Mode.SHADOW)


class TestPlanCurvature(OpenpilotTestCase):
  def test_a_circle(self):
    c, d = curv(25.0, 500.0)
    np.testing.assert_allclose(c, 1 / 500.0)
    np.testing.assert_allclose(d, 25.0 * T)

  def test_slow_points_and_nans_give_nothing(self):
    rate, vel, pos = bend(25.0, 500.0)
    vel[:5] = 3.0
    rate[5] = np.nan
    c, _ = plan_curvature(rate, vel, pos, TUNE.curv_min_v)
    self.assertTrue(np.all(c[:6] == 0) and np.all(c[6:] > 0))

  def test_a_single_spike_is_cut_to_the_97th_percentile(self):
    rate, vel, pos = bend(25.0, 1000.0)
    rate[20] = 25.0 / 100.0   # one point at a 100 m radius
    c, _ = plan_curvature(rate, vel, pos, TUNE.curv_min_v)
    self.assertLess(c.max(), 1 / 500.0)

  def test_empty_and_ragged(self):
    self.assertEqual(len(plan_curvature([], [], [], 5.0)[0]), 0)
    c, d = plan_curvature([0.05] * 33, [25.0] * 20, [1.0] * 33, 5.0)
    self.assertEqual((len(c), len(d)), (20, 20))


class TestBendEaseStep(OpenpilotTestCase):
  def test_a_straight_road_sets_nothing(self):
    e = Ease()
    self.assertEqual(e.run(50, 25.0), [V_CRUISE_UNSET] * 50)
    self.assertEqual(e.b.reason, Reason.IDLE)
    self.assertFalse(e.b.active)

  def test_a_bend_the_eps_holds_sets_nothing(self):
    # 25 m/s (90 km/h) on a 700 m radius: 0.89 m/s^2, under the 1.0 start
    e = Ease()
    self.assertEqual(e.run(100, 25.0, 700.0), [V_CRUISE_UNSET] * 100)
    self.assertAlmostEqual(e.b.pred_lat, 25.0 ** 2 / 700.0, places=6)

  def test_it_starts_after_0_3_s_over_1_0(self):
    e = Ease()
    out = e.run(10, 25.0, 500.0)   # 1.25 m/s^2
    n = round(TUNE.start_s / DT_MDL)
    self.assertEqual(out[:n - 1], [V_CRUISE_UNSET] * (n - 1))
    self.assertLess(out[n - 1], V_CRUISE_UNSET)
    self.assertTrue(e.b.active)
    self.assertEqual(e.b.reason, Reason.ACTIVE)
    self.assertAlmostEqual(e.b.v_start, 25.0)

  def test_the_target_is_the_brake_curve_to_1_1(self):
    # in the bend now (at 0 m): the speed that takes 500 m at 1.1 m/s^2, under the 0.6 decel floor's reach
    e = Ease()
    e.run(10, 23.6, 500.0)
    self.assertAlmostEqual(e.b.v_bend, math.sqrt(1.1 * 500.0), places=4)
    self.assertAlmostEqual(e.b.v_target, max(math.sqrt(1.1 * 500.0), 23.6 - 0.6), places=4)
    # 300 m out: the brake curve, sqrt(v_bend^2 + 2 x 0.4 x (300 - 1 s of travel))
    e = Ease()
    e.run(10, 25.0, 500.0, at=150.0)
    v_bend = math.sqrt(1.1 * 500.0)
    first = float(np.min(25.0 * T[25.0 * T >= 150.0]))
    self.assertAlmostEqual(e.b.v_curve, math.sqrt(v_bend ** 2 + 2 * 0.4 * (first - 25.0)), places=4)

  def test_it_never_asks_for_more_than_0_6(self):
    e = Ease()
    out = e.run(20, 25.0, 200.0)   # 3.1 m/s^2: far over
    self.assertAlmostEqual(out[-1], 25.0 - 0.6, places=6)

  def test_never_under_55_kmh_and_never_15_kmh_off_the_start(self):
    e = Ease()
    e.run(10, 16.0, 150.0)   # 57.6 km/h on a 150 m radius (1.7 m/s^2)
    for v in np.arange(16.0, 14.0, -0.05):
      c, d = curv(v, 150.0)
      tgt = e.b.step(v, True, True, False, False, c, d)
      self.assertGreaterEqual(tgt, 55 * KPH - 1e-9)
    e = Ease()
    e.run(10, 30.0, 300.0)   # 108 km/h on 300 m: the bend wants 65 km/h
    for v in np.arange(30.0, 20.0, -0.5):
      c, d = curv(v, 300.0)
      tgt = e.b.step(v, True, True, False, False, c, d)
      self.assertGreaterEqual(tgt, 30.0 - 15 * KPH - 1e-9)

  def test_no_episode_starts_under_55_kmh(self):
    e = Ease()
    self.assertEqual(e.run(40, 54 * KPH, 100.0), [V_CRUISE_UNSET] * 40)
    self.assertEqual(e.b.reason, Reason.SLOW)
    self.assertFalse(e.b.active)

  def test_only_while_openpilot_steers_and_drives(self):
    e = Ease()
    self.assertLess(e.run(10, 25.0, 400.0)[-1], V_CRUISE_UNSET)
    self.assertEqual(e.run(1, 25.0, 400.0, lat=False), [V_CRUISE_UNSET])
    self.assertFalse(e.b.active, "the episode ends")
    self.assertEqual(e.b.reason, Reason.NOT_STEERING)
    self.assertEqual(e.run(20, 25.0, 400.0, long=False), [V_CRUISE_UNSET] * 20)
    self.assertEqual(e.b.reason, Reason.LONG_OFF)
    self.assertLess(e.run(10, 25.0, 400.0)[-1], V_CRUISE_UNSET, "and starts again once both are back")

  def test_nothing_while_the_gas_is_pressed_and_2_s_after(self):
    e = Ease()
    self.assertLess(e.run(10, 25.0, 400.0)[-1], V_CRUISE_UNSET)
    self.assertEqual(e.run(30, 26.0, 400.0, gas=True), [V_CRUISE_UNSET] * 30)
    self.assertEqual(e.b.reason, Reason.GAS)
    self.assertAlmostEqual(e.b.v_start, 26.0, msg="the cut counts from where the driver left it")
    hold = round(TUNE.gas_hold_s / DT_MDL)   # 40 frames: the 40th after the last press is 2.0 s after it
    after = e.run(hold, 26.0, 400.0)
    self.assertEqual(after[:hold - 1], [V_CRUISE_UNSET] * (hold - 1))
    self.assertLess(after[hold - 1], V_CRUISE_UNSET)
    self.assertTrue(e.b.active, "the same episode")

  def test_the_gas_at_the_start_of_a_bend(self):
    e = Ease()
    self.assertEqual(e.run(20, 25.0, 400.0, gas=True), [V_CRUISE_UNSET] * 20)
    self.assertTrue(e.b.active)

  def test_no_start_in_a_lane_change_and_numbers_held_through_one(self):
    e = Ease()
    self.assertEqual(e.run(20, 25.0, 300.0, lc=True), [V_CRUISE_UNSET] * 20)
    self.assertEqual(e.b.reason, Reason.LANE_CHANGE)
    e = Ease()
    before = e.run(10, 25.0, 450.0)[-1]
    held = e.run(40, 25.0, 100.0, lc=True)   # the lane change's S-curve is not the road
    self.assertEqual(held, [before] * 40)
    self.assertTrue(e.b.active)

  def test_it_ends_1_s_after_the_bend_at_the_start_speed(self):
    e = Ease()
    e.run(10, 25.0, 450.0)                     # 1.39 m/s^2, the episode starts at 25 m/s
    e.run(20, 23.0, 25.0 ** 2 / 0.95)          # at 25 m/s this is 0.95 - over 0.9: it stays, though 0.80 at 23
    self.assertTrue(e.b.active)
    out = e.run(round(TUNE.end_s / DT_MDL) - 1, 23.0, None)
    self.assertTrue(e.b.active)
    np.testing.assert_allclose(np.diff(out), TUNE.hold_rise * DT_MDL, err_msg="nothing curved ahead: the hold rises")
    self.assertEqual(e.b.reason, Reason.ACTIVE)
    self.assertEqual(e.run(1, 23.0, None), [V_CRUISE_UNSET])
    self.assertFalse(e.b.active)
    self.assertEqual(e.b.reason, Reason.IDLE)

  def test_the_target_falls_at_once_and_rises_at_0_3(self):
    e = Ease()
    e.run(10, 25.0, 400.0)
    low = e.run(1, 25.0, 300.0)[0]
    self.assertAlmostEqual(low, 25.0 - 0.6, msg="falls at once (to the decel floor here)")
    hold = e.b.v_hold
    self.assertAlmostEqual(hold, math.sqrt(1.1 * 300.0), places=4)
    # the model flickers back to a gentler bend: the held curve rises 0.3 m/s per s, not to the new number
    for k in range(1, 21):
      e.run(1, 25.0, 450.0)
      self.assertAlmostEqual(e.b.v_hold, hold + k * TUNE.hold_rise * DT_MDL, places=6)
    self.assertLess(e.b.v_hold, math.sqrt(1.1 * 450.0))

  def test_inside_a_bend_it_accelerates_at_0_5_at_most(self):
    # 23 m/s on a bend that the start speed (25) made an episode of, now gentle enough that its own speed is 27: the
    # target is vEgo + 0.5, so the planner may accelerate at 0.5 m/s^2 at most and its cruise accel does not wind up
    e = Ease()
    e.run(10, 25.0, 450.0)
    e.b.v_hold = math.inf
    out = e.run(5, 23.0, 25.0 ** 2 / 0.95)
    self.assertAlmostEqual(out[-1], 23.0 + TUNE.accel_cap, places=6)

  def test_a_flickering_bend_still_starts(self):
    # over 1.0 two frames in three (1.25 / 0.89): the leaky counter gets there, a reset-on-dip one never would
    e = Ease()
    out = []
    for k in range(30):
      out += e.run(1, 25.0, 700.0 if k % 3 == 2 else 500.0)
    self.assertTrue(e.b.active)
    self.assertLess(out[-1], V_CRUISE_UNSET)

  def test_one_log_line_per_episode(self):
    e = Ease()
    with mock.patch.object(be.cloudlog, 'error') as err:
      e.run(10, 25.0, 400.0)
      e.run(60, 25.0, None)
    self.assertEqual(err.call_count, 1)
    line = err.call_args[0][0]
    self.assertTrue(line.startswith("bendease end=bend mode=1 "), line)
    self.assertTrue(" v0=90.0 " in line, line)

  def test_off_sets_nothing(self):
    e = Ease(Mode.OFF)
    self.assertEqual(e.run(50, 25.0, 300.0), [V_CRUISE_UNSET] * 50)
    self.assertEqual(e.b.reason, Reason.OFF)
    self.assertFalse(e.b.live)

  def test_apply_takes_over_only_in_live(self):
    cruise, vision = LongitudinalPlanSource.cruise, LongitudinalPlanSource.sccVision
    for mode in (Mode.SHADOW, Mode.LIVE, Mode.OFF):
      e = Ease(mode)
      e.run(10, 25.0, 300.0)
      got = e.b.apply(cruise, 30.0)
      if mode == Mode.LIVE:
        self.assertEqual(got, (vision, e.b.v_target), "only the speed: the planner keeps the chosen source's accel")
      else:
        self.assertEqual(got, (cruise, 30.0), mode)
      self.assertEqual(e.b.limiting, mode != Mode.OFF, "live: took over; shadow: would have; off: nothing")
      self.assertEqual(e.b.apply(cruise, 10.0), (cruise, 10.0), "a lower chosen target stays")
      self.assertFalse(e.b.limiting)


class TestModeParam(OpenpilotTestCase):
  def test_registered_with_shadow_by_default(self):
    p = Params()
    self.assertEqual(p.get_default_value(be.PARAM), 1)
    self.assertEqual(p.get(be.PARAM, return_default=True), 1)

  def test_read_every_3_s(self):
    b = BendEase(car_params(), sp_params())
    sm = build_sm(25.0, None)
    b.update(sm, True, False)
    self.assertEqual(b.mode, Mode.SHADOW)
    Params().put(be.PARAM, 2)
    for _ in range(59):
      b.update(sm, True, False)
    self.assertEqual(b.mode, Mode.SHADOW)
    b.update(sm, True, False)
    self.assertEqual(b.mode, Mode.LIVE)
    self.assertTrue(b.live)

  def test_unknown_values_are_shadow(self):
    Params().put(be.PARAM, 7)
    b = BendEase(car_params(), sp_params())
    b.update(build_sm(25.0, None), True, False)
    self.assertEqual(b.mode, Mode.SHADOW)


# ---------------------------------------------------------------------------------------------------------------------
# through the real planner

class _SM(dict):
  def __init__(self, services):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.updated = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, 1)

  def all_checks(self, service_list=None) -> bool:
    return True


def build_sm(v: float, radius: float | None, at: float = 0.0, lat=True, gas=False, lc=0, e2e=0.0, cruise_kph=110.0) -> _SM:
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation", "radarState"):
    services[service] = getattr(messaging.new_message(service), service)
  cs = messaging.new_message('carState')
  cs.carState.vEgo, cs.carState.aEgo = v, 0.0
  cs.carState.vCruise = cs.carState.vCruiseCluster = cruise_kph
  cs.carState.gasPressed = gas
  services['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = LongCtrlState.off if gas else LongCtrlState.pid
  services['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.experimentalMode = True
  ss.selfdriveState.enabled = True
  ss.selfdriveState.personality = log.LongitudinalPersonality.aggressive
  services['selfdriveState'] = ss.selfdriveState.as_reader()
  cc = messaging.new_message('carControl')
  cc.carControl.enabled = True
  cc.carControl.latActive = lat
  cc.carControl.cruiseControl.override = gas
  services['carControl'] = cc.carControl.as_reader()
  m = messaging.new_message('modelV2')
  rate, vel, pos = bend(v, radius, at)
  m.modelV2.orientationRate.z = [float(x) for x in rate]
  m.modelV2.velocity.x = [float(x) for x in vel]
  m.modelV2.position.x = [float(x) for x in pos]
  m.modelV2.action.desiredAcceleration = e2e
  m.modelV2.meta.laneChangeState = lc
  services['modelV2'] = m.modelV2.as_reader()
  return _SM(services)


def planner(fingerprint=CAR.HONDA_ACCORD_9G_AU, without=False, flags=0):
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  pl = LongitudinalPlanner(car_params(fingerprint), sp_params(flags), init_v=25.0)
  if without:
    pl.bend_ease = BendEase(car_params(CAR.HONDA_CIVIC), sp_params())   # the inert one every other car gets
  return pl


def drive(pl, frames, sent=None):
  """frames: [(n, build_sm kwargs)]. Returns per frame (aTarget, source, sccVision target, trajectories)."""
  out = []
  pm = SimpleNamespace(send=lambda name, msg: (sent.setdefault(name, []).append(msg) if sent is not None else None))
  for n, kw in frames:
    for _ in range(n):
      sm = build_sm(**kw)
      pl.update(cast(Any, sm))
      pl.publish_longitudinal_plan_sp(cast(Any, sm), cast(Any, pm))
      out.append((float(pl.output_a_target), int(pl.mpc.source), int(pl.source), float(pl.output_v_target),
                  tuple(float(x) for x in pl.v_desired_trajectory), tuple(float(x) for x in pl.a_desired_trajectory)))
  return out


ROUTE = [(20, {"v": 25.0, "radius": None}), (80, {"v": 25.0, "radius": 350.0, "at": 120.0}), (40, {"v": 25.0, "radius": None})]


class TestBendEasePlanner(OpenpilotTestCase):
  def test_shadow_changes_nothing(self):
    Params().put(be.PARAM, 1)
    pl, sent = planner(), {}
    out = drive(pl, ROUTE, sent)
    ref = drive(planner(without=True), ROUTE)
    self.assertEqual(out, ref, "every planner output bit-identical to the planner without it")
    logs = [m.customReserved11 for m in sent['customReserved11']]
    self.assertEqual(len(logs), sum(n for n, _ in ROUTE))
    eased = [b for b in logs if b.reason == Reason.ACTIVE and b.vTarget > 0]   # (not the 1 s count-down after the bend)
    self.assertGreater(len(eased), 50)
    self.assertEqual(sum(b.active for b in logs[100:]), round(TUNE.end_s / DT_MDL) - 1, "it ends 1 s after the bend")
    self.assertTrue(all(b.mode == 1 and b.limiting and 0 < b.vTarget < 25.0 for b in eased))
    self.assertTrue(all(-0.6 - 1e-6 <= b.aTarget < 0 for b in eased))
    self.assertEqual({b.reason for b in logs[:5]}, {Reason.IDLE})

  def test_off_changes_nothing_either(self):
    Params().put(be.PARAM, 0)
    self.assertEqual(drive(planner(), ROUTE), drive(planner(without=True), ROUTE))

  def test_live_eases_through_scc_vision_at_0_6_at_most(self):
    Params().put(be.PARAM, 2)
    pl = planner()
    out = drive(pl, ROUTE)
    ref = drive(planner(without=True), ROUTE)
    self.assertEqual(out[:20], ref[:20], "straight: untouched")
    accels = [o[0] for o in out[20:100]]
    self.assertGreaterEqual(min(accels), -0.6 - 1e-6)
    self.assertLess(min(accels), -0.55, "it gets to its 0.6")
    self.assertGreater(min(o[0] for o in ref[20:100]), -0.05, "without it the plan does not slow")
    self.assertTrue(int(LongitudinalPlanSource.sccVision) in {o[2] for o in out[20:100]}, "the planner's source")
    self.assertTrue(all(o[0] <= r[0] + 1e-9 for o, r in zip(out, ref, strict=True)), "it only ever lowers the plan")
    steps = np.diff([0.0] + accels)
    self.assertGreaterEqual(steps.min(), -0.8 * DT_MDL - 1e-6, "the planner's cruise jerk limit shapes the onset")

  def test_live_the_gas_wins(self):
    Params().put(be.PARAM, 2)
    frames = [(20, {"v": 25.0, "radius": None}), (80, {"v": 25.0, "radius": 350.0, "at": 120.0, "gas": True})]
    a, b = drive(planner(), frames), drive(planner(without=True), frames)
    self.assertEqual(a, b)

  def test_live_not_steering_no_ease(self):
    Params().put(be.PARAM, 2)
    frames = [(20, {"v": 25.0, "radius": None}), (80, {"v": 25.0, "radius": 350.0, "at": 120.0, "lat": False})]
    self.assertEqual(drive(planner(), frames), drive(planner(without=True), frames))

  def test_every_other_car_and_stock_acc_are_upstream_and_publish_nothing(self):
    Params().put(be.PARAM, 2)
    for fp, flags in ((CAR.HONDA_CIVIC, 0), (CAR.HONDA_ACCORD_9G_AU, HondaFlagsSP.ELESYS_STOCK_ACC.value)):
      pl, sent = planner(fp, flags=flags), {}
      self.assertIsNone(pl.bend_ease.tune)
      out = drive(pl, ROUTE, sent)
      self.assertNotIn('customReserved11', sent)
      self.assertEqual(out, drive(planner(fp, without=True, flags=flags), ROUTE))


class TestWiring(OpenpilotTestCase):
  def test_the_service(self):
    s = SERVICE_LIST['customReserved11']
    self.assertEqual((s.should_log, s.frequency, s.decimation), (True, 20., 2))

  def test_the_struct(self):
    b = messaging.new_message('customReserved11').customReserved11
    for f in ('mode', 'active', 'vTarget', 'predLatAccel', 'vBend', 'distance', 'aTarget', 'limiting', 'reason', 'vStart'):
      self.assertTrue(hasattr(b, f), f)

  def test_plannerd_publishes_it(self):
    from openpilot.selfdrive.controls import plannerd

    class Stop(Exception):
      pass

    pubs: list[str] = []
    with mock.patch.object(plannerd, 'config_realtime_process'), \
         mock.patch.object(plannerd, 'Params', lambda: SimpleNamespace(get=lambda *_a, **_k: b'')), \
         mock.patch.object(plannerd, 'get_gps_location_service', lambda _p: 'gpsLocation'), \
         mock.patch.object(plannerd, 'LongitudinalPlanner'), \
         mock.patch.object(plannerd, 'LaneDepartureWarning'), \
         mock.patch.object(plannerd.messaging, 'log_from_bytes', lambda *_a: SimpleNamespace(brand='honda')), \
         mock.patch.object(plannerd.messaging, 'PubMaster', lambda services: pubs.extend(services)), \
         mock.patch.object(plannerd.messaging, 'SubMaster', mock.MagicMock(side_effect=Stop)):
      with self.assertRaises(Stop):
        plannerd.main()
    self.assertTrue('customReserved11' in pubs, pubs)

  def test_the_sunnylink_setting(self):
    path = os.path.join(BASEDIR, "openpilot/sunnypilot/sunnylink/settings_ui_src/pages/vehicle.yaml")
    if not os.path.exists(path):
      path = os.path.join(BASEDIR, "sunnypilot/sunnylink/settings_ui_src/pages/vehicle.yaml")
    with open(path) as f:
      page = yaml.safe_load(f)
    honda = next(s for s in page['sections'] if s['id'] == 'honda')
    item = next(i for i in honda['items'] if i['key'] == be.PARAM)
    self.assertEqual(item['widget'], 'multiple_button')
    self.assertEqual([o['value'] for o in item['options']], [0, 1, 2])
    self.assertEqual([o['label'] for o in item['options']], ['Off', 'Shadow', 'Live'])
    self.assertNotIn('enablement', item, "it can change on the move: plannerd reads it every 3 s")
