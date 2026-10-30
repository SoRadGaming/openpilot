"""The gas override boost (commaai/openpilot#39015), ported ahead of the sync.

While the model is a candidate (is_e2e) and was the plan's limit just before the driver pressed the gas - its accel, with
the boost, more than 0.1 under both the MPC's and the cruise accel - each press adds up to 0.05 m/s^2 to the model's
accel at 0.025 m/s^2 per second, up to 0.2 in all, until openpilot disengages. It is applied in full from 20 mph, not
under 10 mph, and fades out as the model brakes from -0.5 to -1.0. The planner's min() still has the last word.

TestAccelBoost holds the class to upstream's (as merged, with experimentalMode for is_e2e) and to those rules;
TestAccelBoostPlanner drives the real LongitudinalPlanner on this car.
"""
from types import SimpleNamespace
from typing import Any, cast

import numpy as np

from openpilot.cereal import log, messaging
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib import accel_boost as ab
from openpilot.selfdrive.controls.lib import longitudinal_planner as lp
from openpilot.selfdrive.controls.lib.accel_boost import AccelBoost
from openpilot.selfdrive.controls.tests.test_cruise_accel import V_EGO, LongCtrlState, build_sm, planner

PER_FRAME = ab.ACCEL_BOOST_RATE * DT_MDL   # 0.00125 m/s^2 per frame at full speed scale
PRESS = int(round(ab.ACCEL_BOOST_PER_OVERRIDE / PER_FRAME))   # 40 frames, 2 s, for one press's 0.05


# upstream's constants as merged (ab8c84c398), literal: the port's own are tested against these, not with them
UP_MAX, UP_RATE, UP_PER_OVERRIDE, UP_MIN_SPEED = 0.2, 0.025, 0.05, 10 * CV.MPH_TO_MS


class UpstreamAccelBoost:
  """commaai/openpilot#39015 as merged (ab8c84c398), before #39097 dropped its experimentalMode gate with chill mode."""
  def __init__(self):
    self.total_boost = 0.0
    self.boost_this_override = 0.0
    self.boost_eligible = False

  def update(self, sm, output_a_target_e2e, output_a_target_mpc, a_cruise):
    enabled = sm['selfdriveState'].enabled
    gas_pressed = sm['carState'].gasPressed
    v_ego = sm['carState'].vEgo
    speed_scale = np.interp(v_ego, [UP_MIN_SPEED, 2 * UP_MIN_SPEED], [0.0, 1.0])
    boost_scale = speed_scale * np.interp(output_a_target_e2e, [-1.0, -0.5, 5.0], [0.0, 1.0, 1.0], right=0.0)
    model_limited = (sm['selfdriveState'].experimentalMode and
                     output_a_target_e2e + self.total_boost * boost_scale < min(output_a_target_mpc, a_cruise) - 0.1)

    if not enabled or not gas_pressed:
      self.boost_this_override = 0.0
      self.boost_eligible = enabled and model_limited

    if not enabled:
      self.total_boost = 0.0
    elif gas_pressed and self.boost_eligible:
      increase = min(UP_RATE * DT_MDL * speed_scale,
                     UP_PER_OVERRIDE - self.boost_this_override, UP_MAX - self.total_boost)
      self.total_boost += increase
      self.boost_this_override += increase

    return output_a_target_e2e + self.total_boost * boost_scale


def fake_sm(gas=False, enabled=True, experimental=True, v_ego=V_EGO):
  return {'selfdriveState': SimpleNamespace(enabled=enabled, experimentalMode=experimental),
          'carState': SimpleNamespace(gasPressed=gas, vEgo=v_ego)}


def press(boost, frames, a_model=0.0, a_mpc=1.0, a_cruise=1.0, is_e2e=True, v_ego=V_EGO, released=1):
  """`released` frames off the gas (eligibility latches there), then `frames` on it. Returns the last update()."""
  out = None
  for _ in range(released):
    out = boost.update(fake_sm(v_ego=v_ego), a_model, a_mpc, a_cruise, is_e2e)
  for _ in range(frames):
    out = boost.update(fake_sm(gas=True, v_ego=v_ego), a_model, a_mpc, a_cruise, is_e2e)
  return out


class TestAccelBoost(OpenpilotTestCase):
  def test_is_upstreams_with_experimental_mode_for_is_e2e(self):
    rng = np.random.default_rng(39015)
    for run in range(40):
      ours, theirs = AccelBoost(), UpstreamAccelBoost()
      gas = False
      for _ in range(400):
        if rng.random() < 0.05:
          gas = not gas
        sm = fake_sm(gas=gas, enabled=bool(rng.random() > 0.01), experimental=bool(run % 4 != 0 or rng.random() > 0.5),
                     v_ego=float(rng.uniform(0.0, 35.0)))
        a_model, a_mpc, a_cruise = (float(x) for x in rng.uniform(-2.5, 2.5, 3))
        a = ours.update(sm, a_model, a_mpc, a_cruise, sm['selfdriveState'].experimentalMode)
        self.assertEqual(a, theirs.update(sm, a_model, a_mpc, a_cruise))
        self.assertEqual((ours.total_boost, ours.boost_this_override, ours.boost_eligible),
                         (theirs.total_boost, theirs.boost_this_override, theirs.boost_eligible))

  def test_the_constants_are_upstreams(self):
    self.assertEqual((ab.ACCEL_BOOST_MAX, ab.ACCEL_BOOST_RATE, ab.ACCEL_BOOST_PER_OVERRIDE, ab.ACCEL_BOOST_MIN_SPEED),
                     (UP_MAX, UP_RATE, UP_PER_OVERRIDE, UP_MIN_SPEED))

  def test_a_press_while_the_model_limits_adds_0_05_over_2_s(self):
    self.assertEqual(PRESS, int(round(2.0 / DT_MDL)))      # 40 frames at 20 Hz
    self.assertAlmostEqual(PER_FRAME, 0.00125, places=12)   # 0.025 m/s^2 per second
    boost = AccelBoost()
    for n in range(1, PRESS + 20):
      a = press(boost, 1, released=1 if n == 1 else 0)
      self.assertAlmostEqual(boost.total_boost, min(n, PRESS) * PER_FRAME, places=12)
      self.assertEqual(a, boost.total_boost)   # the model's 0.0 plus the boost, applied in full at 25 m/s
    self.assertAlmostEqual(boost.total_boost, ab.ACCEL_BOOST_PER_OVERRIDE, places=12)

  def test_four_presses_reach_the_cap_and_a_fifth_adds_nothing(self):
    boost = AccelBoost()
    for i in range(5):
      press(boost, PRESS + 10)
      self.assertAlmostEqual(boost.total_boost, min(i + 1, 4) * ab.ACCEL_BOOST_PER_OVERRIDE, places=9)
    self.assertLessEqual(boost.total_boost, ab.ACCEL_BOOST_MAX)

  def test_only_a_model_limiting_before_the_press_counts(self):
    # the model at or within 0.1 of the lower of the MPC and cruise just before the press: nothing, even if it
    # limits once the gas is down (the override changes the lead plan)
    for a_model, a_mpc, a_cruise in ((1.0, 1.0, 1.0), (0.95, 1.5, 1.04), (0.0, 0.1, 2.0)):
      boost = AccelBoost()
      boost.update(fake_sm(), a_model, a_mpc, a_cruise, True)
      for _ in range(PRESS):
        boost.update(fake_sm(gas=True), a_model - 1.0, a_mpc, a_cruise, True)
      self.assertEqual(boost.total_boost, 0.0)
    boost = AccelBoost()
    press(boost, PRESS, a_model=0.0, a_mpc=0.11, a_cruise=2.0)
    self.assertGreater(boost.total_boost, 0.0)

  def test_the_boost_counts_toward_the_limit(self):
    # once the boosted model is within 0.1 of the limit, the next press adds nothing
    boost = AccelBoost()
    press(boost, PRESS, a_model=0.0, a_mpc=0.14, a_cruise=2.0)
    self.assertAlmostEqual(boost.total_boost, 0.05, places=12)
    press(boost, PRESS, a_model=0.0, a_mpc=0.14, a_cruise=2.0)
    self.assertAlmostEqual(boost.total_boost, 0.05, places=12)
    press(boost, PRESS, a_model=0.0, a_mpc=0.16, a_cruise=2.0)   # 0.05 under 0.06: one more press counts
    self.assertAlmostEqual(boost.total_boost, 0.1, places=12)

  def test_chill_and_dec_acc_build_nothing(self):
    boost = AccelBoost()
    for _ in range(4):
      press(boost, PRESS, is_e2e=False)
    self.assertEqual(boost.total_boost, 0.0)
    # a boost built in experimental stays while DEC's acc phase leaves the model out, and applies again after it
    press(boost, PRESS)
    built = boost.total_boost
    self.assertGreater(built, 0.0)
    for _ in range(30):
      self.assertEqual(boost.update(fake_sm(), 0.0, 1.0, 1.0, False), built)
    self.assertEqual(boost.total_boost, built)

  def test_a_disengage_clears_it(self):
    boost = AccelBoost()
    press(boost, PRESS)
    self.assertGreater(boost.total_boost, 0.0)
    self.assertEqual(boost.update(fake_sm(enabled=False), 0.0, 1.0, 1.0, True), 0.0)
    self.assertEqual((boost.total_boost, boost.boost_this_override, boost.boost_eligible), (0.0, 0.0, False))
    # pressing the gas while disengaged builds nothing
    for _ in range(PRESS):
      boost.update(fake_sm(gas=True, enabled=False), 0.0, 1.0, 1.0, True)
    self.assertEqual(boost.total_boost, 0.0)

  def test_speed_scale(self):
    # nothing under 10 mph, in full from 20 mph: both how fast it builds and how much of it applies
    lo, hi = ab.ACCEL_BOOST_MIN_SPEED, 2 * ab.ACCEL_BOOST_MIN_SPEED
    for v_ego, scale in ((0.0, 0.0), (lo - 0.1, 0.0), (lo, 0.0), (15 * CV.MPH_TO_MS, 0.5), (hi, 1.0), (35.0, 1.0)):
      boost = AccelBoost()
      a = press(boost, 1, v_ego=v_ego)
      self.assertAlmostEqual(boost.total_boost, PER_FRAME * scale, places=12)
      self.assertAlmostEqual(a, boost.total_boost * scale, places=12)
    boost = AccelBoost()
    for _ in range(4):
      press(boost, PRESS + 10)
    for v_ego, scale in ((lo, 0.0), (15 * CV.MPH_TO_MS, 0.5), (hi, 1.0)):
      self.assertAlmostEqual(boost.update(fake_sm(v_ego=v_ego), 0.0, 1.0, 1.0, True), 0.2 * scale, places=9)

  def test_it_fades_as_the_model_brakes(self):
    boost = AccelBoost()
    for _ in range(4):
      press(boost, PRESS + 10)
    self.assertAlmostEqual(boost.total_boost, 0.2, places=9)
    for a_model, added in ((1.5, 0.2), (0.0, 0.2), (-0.5, 0.2), (-0.75, 0.1), (-0.9, 0.04), (-1.0, 0.0), (-3.0, 0.0)):
      self.assertAlmostEqual(boost.update(fake_sm(), a_model, 2.0, 2.0, True) - a_model, added, places=9)


# through the real planner

class _PM:
  def __init__(self):
    self.sent = {}

  def send(self, service, msg):
    self.sent[service] = msg


def sm_at(e2e: float, gas: bool = False, experimental: bool = True, enabled: bool = True, set_kph: float = 120.0,
          lead: bool = False, **kw):
  """build_sm's 25 m/s, with the gas, and openpilot's longitudinal off while it is pressed (as controlsd has it)."""
  sm = build_sm(set_kph, lead, experimental=experimental, e2e=e2e, **kw)
  cs = messaging.new_message('carState')
  cs.carState.vEgo = V_EGO
  cs.carState.vCruise = cs.carState.vCruiseCluster = set_kph
  cs.carState.gasPressed = gas
  sm['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = LongCtrlState.off if gas or not enabled else LongCtrlState.pid
  sm['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.enabled = enabled
  ss.selfdriveState.experimentalMode = experimental
  ss.selfdriveState.personality = log.LongitudinalPersonality.standard
  sm['selfdriveState'] = ss.selfdriveState.as_reader()
  return sm


def run(pl, frames: int, e2e: float, **kw) -> list[float]:
  out = []
  for _ in range(frames):
    pl.update(cast(Any, sm_at(e2e, **kw)))
    out.append(float(pl.output_a_target))
  return out


class TestAccelBoostPlanner(OpenpilotTestCase):
  def test_presses_behind_a_slow_model_raise_the_plan(self):
    # experimental, no lead, set speed far above: the model's +0.3 is the plan, under the MPC and cruise
    pl = planner()
    plan = run(pl, 60, 0.3)
    self.assertAlmostEqual(plan[-1], 0.3, places=6)
    self.assertTrue(pl.accel_boost.boost_eligible)
    for i in range(1, 5):
      run(pl, PRESS + 10, 0.3, gas=True)
      plan = run(pl, 30, 0.3)
      self.assertAlmostEqual(pl.accel_boost.total_boost, min(i * 0.05, 0.2), places=9)
      self.assertAlmostEqual(plan[-1], 0.3 + pl.accel_boost.total_boost, places=6)
    pm = _PM()
    pl.publish(cast(Any, sm_at(0.3)), pm)
    self.assertAlmostEqual(pm.sent['longitudinalPlan'].longitudinalPlan.accelBoost, 0.2, places=6)
    # a disengage clears it, and the plan is the model's own again
    run(pl, 1, 0.3, enabled=False)
    self.assertEqual(pl.accel_boost.total_boost, 0.0)
    self.assertAlmostEqual(run(pl, 30, 0.3)[-1], 0.3, places=6)

  def test_it_never_lifts_the_plan_past_the_lead_or_the_set_speed(self):
    # the model brakes harder than the MPC for a lead (35 m at 22 m/s, about -0.8), or than the cruise accel's floor
    # under a lower set speed, and the presses build the boost to 0.2 - with the model at -1.2 it adds nothing (it fades
    # out by -1.0). Then the model eases to -0.6, boosted to -0.44. Behind the lead the plan is the MPC's, bit for bit
    # a planner's without the boost; under the set speed the boost lifts it from the model's -0.6 to the cruise accel's
    # -0.5 and no further
    for kw in ({'lead': True}, {'set_kph': 70.0}):
      pl, ref = planner(), planner()
      ref.accel_boost.update = lambda sm, a_e2e, a_mpc, a_cruise, is_e2e: a_e2e
      plans = []
      for p in (pl, ref):
        plan = run(p, 60, -1.2, **kw)
        for _ in range(4):
          plan += run(p, PRESS + 10, -1.2, gas=True, **kw)
          plan += run(p, 5, -1.2, **kw)
        plans.append(plan + run(p, 40, -0.6, **kw))
      self.assertAlmostEqual(pl.accel_boost.total_boost, 0.2, places=9)
      self.assertEqual(plans[0][:-40], plans[1][:-40])
      if 'lead' in kw:
        self.assertEqual(plans[0], plans[1])
        self.assertTrue(all(a < -0.44 for a in plans[0][-40:]))   # under the boosted model on every frame
      else:
        self.assertAlmostEqual(plans[1][-1], -0.6, places=6)   # the model's, a Float32 in modelV2
        for w, wo in zip(plans[0][-40:], plans[1][-40:], strict=True):
          self.assertEqual(w, max(wo, lp.A_CRUISE_MIN))

  def test_chill_builds_nothing_and_plans_as_without_it(self):
    pl, ref = planner(), planner()
    ref.accel_boost.update = lambda sm, a_e2e, a_mpc, a_cruise, is_e2e: a_e2e
    plans = []
    for p in (pl, ref):
      plan = run(p, 60, -1.2, experimental=False)
      for _ in range(4):
        plan += run(p, PRESS + 10, -1.2, gas=True, experimental=False)
        plan += run(p, 30, 0.3, experimental=False)
      plans.append(plan)
    self.assertEqual(pl.accel_boost.total_boost, 0.0)
    self.assertEqual(plans[0], plans[1])
