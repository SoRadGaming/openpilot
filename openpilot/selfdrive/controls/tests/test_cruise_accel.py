"""The cruise accel's jerk bound (commaai/openpilot#39127, cruise planner: remove delay when lowering set speed).

get_cruise_accel() jerk-limits the cruise accel from its last value. Falling, the bound now starts from the lower of
that and the plan's last output. So a set speed lowered while the plan already brakes (for a lead, or the model in
experimental mode) puts the cruise accel at or below that braking at once. Before, it ramped down at 0.6-1.6 m/s^3 from
wherever it had wound up (+0.8 in chill at 25 m/s, +2.0 in experimental), and the plan could rise in between. Rising
is unchanged, and so is every frame where the plan was not below the cruise accel.

The cruise accel's own target never goes under A_CRUISE_MIN, -0.5 since commaai/openpilot#39060 (it was -1.2). Behind a
plan braking harder than that, a lowered set speed puts the cruise accel at -0.5 at once, and the plan's min() keeps the
harder braking.

The planner takes that output before update_targets, which hands back SCC-V's own accel when SCC-V wins. A curve entry
moves the cruise accel at most one jerk step under min(its last value, the last published aTarget); a bound from SCC-V's
accel would let the cruise accel fall to its own target, A_CRUISE_MIN (-0.5), in one frame and the plan with it (2.0
m/s^2 from an experimental-mode plan at +1.5; 2.54 when the floor was -1.2).
TestCruiseAccelPlanner drives the real LongitudinalPlanner (MPC included) on this car.
"""
import itertools
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import numpy as np

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.car.interfaces import ACCEL_MAX
from openpilot.cereal import custom, log, messaging
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib import longitudinal_planner as lp
from openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlannerSP

LongCtrlState = structs.CarControl.Actuators.LongControlState
V_EGO = 25.0
STEP = float(np.interp(V_EGO, lp.A_CRUISE_MAX_BP, lp.J_CRUISE_VALS)) * DT_MDL   # 0.04 m/s^2 per frame at 25 m/s
V_LOW = 70 * CV.KPH_TO_MS     # under vEgo: the cruise accel's target is A_CRUISE_MIN
V_HIGH = 100 * CV.KPH_TO_MS   # over vEgo: its target is get_max_accel(25) = 0.8
CP_GEOM = SimpleNamespace(steerRatio=15.0, wheelbase=2.7)


def cruise(v_cruise, a_cruise_prev, a_target_prev, v_ego=V_EGO, e2e=False):
  """Straight ahead, no coast limit: the planner's call on this test's inputs."""
  return lp.get_cruise_accel(e2e, v_cruise, v_ego, a_cruise_prev, a_target_prev, 0.0, CP_GEOM, DT_MDL, ACCEL_MAX, True)


def old_bound(v_cruise, a_cruise_prev, v_ego=V_EGO, e2e=False):
  """The bound before #39127, from the cruise accel alone: the same call with the plan at the cruise accel."""
  return cruise(v_cruise, a_cruise_prev, a_cruise_prev, v_ego, e2e)


class TestCruiseAccel(OpenpilotTestCase):
  def test_the_floor_is_upstreams(self):
    # commaai/openpilot#39060, the owner's call (2026-10-30): a slowdown only the set speed asks for is at most -0.5
    # m/s^2 - a set speed of 0 too (forceDecel)
    self.assertEqual(lp.A_CRUISE_MIN, -0.5)
    self.assertEqual(cruise(0.0, lp.A_CRUISE_MIN, lp.A_CRUISE_MIN), -0.5)

  def test_a_set_speed_lowered_while_braking_brakes_at_once(self):
    # one jerk step under the braking plan, or the floor when the plan brakes harder than that
    for e2e, wound_up in ((False, 0.8), (True, 2.0)):
      for a_out in (-0.2, -0.45, -0.8, -1.1):
        a = cruise(V_LOW, wound_up, a_out, e2e=e2e)
        self.assertLessEqual(a, max(a_out, lp.A_CRUISE_MIN))
        self.assertAlmostEqual(a, max(lp.A_CRUISE_MIN, a_out - STEP), places=9)
        self.assertAlmostEqual(old_bound(V_LOW, wound_up, e2e=e2e), wound_up - STEP, places=9)   # the delay it removes

  def test_a_small_drop_goes_straight_to_its_own_target(self):
    # set 0.3 m/s under vEgo while the plan brakes at -1.0: the cruise accel is -0.3 at once (before: +0.76), and the
    # plan's min() keeps the lower braking
    self.assertAlmostEqual(cruise(V_EGO - 0.3, 0.8, -1.0), -0.3, places=9)

  def test_a_raised_set_speed_is_unchanged(self):
    for a_prev, a_out in itertools.product((-1.2, -0.5, 0.0, 0.5), (-3.5, -1.0, 0.0, 1.0)):
      a = cruise(V_HIGH, a_prev, a_out)
      self.assertEqual(a, old_bound(V_HIGH, a_prev))
      self.assertAlmostEqual(a, min(a_prev + STEP, lp.get_max_accel(V_EGO)), places=9)

  def test_only_a_plan_below_the_cruise_accel_lowers_the_bound(self):
    # a plan at or above the cruise accel (cruise was the plan, or the reentry ramp held the plan up) is upstream's
    # bound bit for bit. A plan below it: never higher than before, never more than one jerk step under that plan, and
    # never under A_CRUISE_MIN unless the old bound already was
    for v_ego, dv, a_prev, d_out, e2e in itertools.product((3., 12., 25., 33.), (-8., -1., -0.2, 0., 0.5, 4.),
                                                         (-1.5, -1.2, -0.4, 0., 0.7, 2.0), (-2.5, -0.6, -0.01, 0., 0.3),
                                                         (False, True)):
      a_out = a_prev + d_out
      new, old = cruise(v_ego + dv, a_prev, a_out, v_ego, e2e), old_bound(v_ego + dv, a_prev, v_ego, e2e)
      step = float(np.interp(v_ego, lp.A_CRUISE_MAX_BP, lp.J_CRUISE_VALS)) * DT_MDL
      if a_out >= a_prev:
        self.assertEqual(new, old)
      else:
        self.assertLessEqual(new, old)
        self.assertGreaterEqual(new, a_out - step - 1e-12)
        self.assertTrue(new == old or new >= lp.A_CRUISE_MIN)


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


def build_sm(set_kph: float, lead: bool, experimental: bool = False, e2e: float = 0.0,
             lead_d: float = 35.0, lead_v: float = 22.0) -> _SM:
  """Engaged at 25 m/s, chill unless experimental (then e2e is the model's accel). The lead: 35 m ahead at 22 m/s, which
  the MPC brakes for at about -0.8 (50 m at 23 m/s: about -0.32)."""
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)
  rs = messaging.new_message('radarState')
  if lead:
    ld = rs.radarState.leadOne
    ld.present, ld.dRel, ld.vLead, ld.vLeadK, ld.vRel, ld.modelProb = True, lead_d, lead_v, lead_v, lead_v - V_EGO, 1.0
    ld.aLeadK, ld.aLeadTau = 0.0, 1.5
    ld.radar, ld.radarTrackId = True, 7
  services['radarState'] = rs.radarState.as_reader()
  cs = messaging.new_message('carState')
  cs.carState.vEgo = V_EGO
  cs.carState.vCruise = cs.carState.vCruiseCluster = set_kph
  services['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = LongCtrlState.pid
  services['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.enabled = True
  ss.selfdriveState.experimentalMode = experimental
  ss.selfdriveState.personality = log.LongitudinalPersonality.standard
  services['selfdriveState'] = ss.selfdriveState.as_reader()
  cc = messaging.new_message('carControl')
  cc.carControl.enabled = True
  services['carControl'] = cc.carControl.as_reader()
  m = messaging.new_message('modelV2')
  m.modelV2.orientationRate.z = [0.01] * 33   # nonzero: a straight path divides by zero in SCC vision
  m.modelV2.velocity.x = [V_EGO] * 33
  m.modelV2.position.x = [float(i) for i in range(33)]
  m.modelV2.action.desiredAcceleration = e2e
  services['modelV2'] = m.modelV2.as_reader()
  return _SM(services)


def planner():
  CP = structs.CarParams()
  CP.brand, CP.carFingerprint, CP.openpilotLongitudinalControl = 'honda', CAR.HONDA_ACCORD_9G_AU, True
  CP.steerRatio, CP.wheelbase, CP.longitudinalActuatorDelay = 15.0, 2.7, 0.6
  return lp.LongitudinalPlanner(CP, custom.CarParamsSP.new_message().as_reader(), init_v=V_EGO)


def drive(pl, frames: int, set_kph: float, lead: bool = True, **kw) -> list[tuple[float, float]]:
  """(output_a_target, a_cruise) after each of `frames` planner frames."""
  out = []
  for _ in range(frames):
    pl.update(cast(Any, build_sm(set_kph, lead, **kw)))
    out.append((float(pl.output_a_target), float(pl.a_cruise)))
  return out


class TestCruiseAccelPlanner(OpenpilotTestCase):
  def test_a_set_speed_lowered_behind_a_braking_plan_brakes_at_once(self):
    # the MPC brakes for a lead softer than the cruise accel's floor (50 m ahead at 23 m/s), then harder (35 m at 22 m/s)
    for lead_d, lead_v, softer in ((50.0, 23.0, True), (35.0, 22.0, False)):
      pl = planner()
      a_out, a_cruise = drive(pl, 40, 100.0, lead_d=lead_d, lead_v=lead_v)[-1]
      self.assertAlmostEqual(a_cruise, 0.8, places=6, msg="the cruise accel wound up toward 100 km/h")
      self.assertTrue(-1.0 < a_out < -0.2, f"the plan brakes for the lead: {a_out}")
      self.assertEqual(a_out > lp.A_CRUISE_MIN, softer)
      after = drive(pl, 12, 70.0, lead_d=lead_d, lead_v=lead_v)
      # at once one jerk step under the plan, or the floor (before: +0.76)
      self.assertAlmostEqual(after[0][1], max(lp.A_CRUISE_MIN, a_out - STEP), places=6)
      self.assertLess(after[0][1], old_bound(V_LOW, a_cruise) - 1.0)
      prev = a_out
      for o, a in after:
        self.assertLessEqual(a, max(prev, lp.A_CRUISE_MIN))   # the cruise accel never rises on the way
        self.assertLessEqual(o, a)                           # the plan is at or under it
        if softer:
          self.assertEqual(o, a)                             # under the lead's braking, the cruise accel is the plan
        prev = a
      self.assertAlmostEqual(prev, lp.A_CRUISE_MIN, places=9)

  def test_a_raised_set_speed_ramps_up_as_before(self):
    pl = planner()
    a_out, a_cruise = drive(pl, 40, 70.0)[-1]
    self.assertAlmostEqual(a_cruise, lp.A_CRUISE_MIN, places=9)
    self.assertLess(a_out, a_cruise)   # the MPC's braking for the lead, harder than the cruise accel's floor
    prev = a_cruise
    for o, a in drive(pl, 12, 100.0):
      self.assertEqual(a, old_bound(V_HIGH, prev))
      self.assertAlmostEqual(a, prev + STEP, places=9)
      self.assertLessEqual(o, a)
      prev = a

  def test_scc_vision_far_below_the_plan_moves_the_cruise_accel_one_jerk_step(self):
    # SCC-V wins with its own accel (entering a curve), far under the published plan: in chill the plan at 0 and SCC-V
    # at -0.6; in experimental, this car's usual mode, the model at +1.5 with the cruise accel wound up to +2.0 and SCC-V
    # at -1.0. Each frame the cruise accel moves one jerk step under min(its last value, the last published aTarget) -
    # its own target, A_CRUISE_MIN (-0.5), is lower still over these 8 frames - and the plan with it. A bound from
    # SCC-V's accel would put both at SCC-V's accel less one step, floored at A_CRUISE_MIN, on the first frame: a 0.5
    # and a 2.0 m/s^2 step.
    update_targets = LongitudinalPlannerSP.update_targets
    for experimental, e2e, set_kph, a_scc, settled_out, settled_cruise in ((False, 0.0, 90.0, -0.6, 0.0, 0.0),
                                                                            (True, 1.5, 100.0, -1.0, 1.5, 2.0)):
      pl = planner()
      settled = drive(pl, 60, set_kph, lead=False, experimental=experimental, e2e=e2e)[-1]
      self.assertAlmostEqual(settled[0], settled_out, places=6)
      self.assertAlmostEqual(settled[1], settled_cruise, places=6)

      def scc_vision_entering(planner_sp, sm, v_ego, a_ego, v_cruise, a_scc=a_scc):
        update_targets(planner_sp, sm, v_ego, a_ego, v_cruise)
        return 17.6, a_scc

      a_out, a_cruise = settled
      with mock.patch.object(LongitudinalPlannerSP, 'update_targets', scc_vision_entering):
        for o, a in drive(pl, 8, set_kph, lead=False, experimental=experimental, e2e=e2e):
          self.assertAlmostEqual(a, min(a_cruise, a_out) - STEP, places=9)
          self.assertAlmostEqual(o, a, places=9)
          a_out, a_cruise = o, a
