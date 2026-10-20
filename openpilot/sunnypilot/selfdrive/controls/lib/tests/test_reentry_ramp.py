"""FORK(HONDA_ACCORD_9G_AU): the ramp-in after a gas override or an engagement (reentry_ramp.py, round 8 proposal 2).

The unit tests pin the ramp itself: the window, the rate, the start, the three exceptions, the speed gate and the
log line. TestReentryRampPlanner drives the real LongitudinalPlanner (MPC included) through a gas override and a
lift-off. It checks that the published aTarget falls at most 1.5 m/s^3 for 1.5 s on this car and nowhere else, and
that every frame outside the window is bit-identical to the planner without the ramp.
"""
import math
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log, messaging
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.controls.lib import reentry_ramp as rr
from openpilot.sunnypilot.selfdrive.controls.lib.reentry_ramp import ReentryRamp, reentry_ramp_applies

STEP = rr.REENTRY_RAMP_JERK * DT_MDL      # 0.075 m/s^2 per 20 Hz frame
WINDOW = round(rr.REENTRY_RAMP_S / DT_MDL)   # 30 frames
LongCtrlState = structs.CarControl.Actuators.LongControlState


class Drive:
  """Feeds ReentryRamp one planner frame at a time. Defaults: 15 m/s, a calm MPC, no FCW, no crash."""

  def __init__(self):
    self.r = ReentryRamp()
    self.out = []

  def step(self, plan, reset=False, v=15.0, a=-0.2, mpc=0.0, fcw=False, crash=0.0):
    o = self.r.update(reset, v, a, plan, mpc, fcw, crash)
    self.out.append(o)
    return o

  def override(self, n=5, plan=-1.0, **kw):
    return [self.step(plan, reset=True, **kw) for _ in range(n)]

  def run(self, n, plan, **kw):
    return [self.step(plan(i) if callable(plan) else plan, **kw) for i in range(n)]


class TestReentryRamp(OpenpilotTestCase):
  def test_constants(self):
    self.assertEqual((rr.REENTRY_RAMP_S, rr.REENTRY_RAMP_JERK, rr.REENTRY_RAMP_MPC_FLOOR, rr.REENTRY_RAMP_MIN_SPEED),
                     (1.5, 1.5, -2.0, 3.0))
    self.assertEqual(WINDOW, 30)

  def test_nothing_while_the_driver_overrides(self):
    d = Drive()
    self.assertEqual(d.override(10, plan=-1.4), [-1.4] * 10)
    self.assertFalse(d.r.active)

  def test_the_plan_falls_at_most_1_5_m_s3_from_min_aego_0(self):
    d = Drive()
    d.override(plan=-1.6)
    out = d.run(WINDOW + 5, -1.6, a=-0.2)
    # starts from aEgo -0.2 and walks down 0.075 per frame until it meets the plan
    want = [max(-1.6, -0.2 - STEP * (k + 1)) for k in range(WINDOW + 5)]
    for k, (o, w) in enumerate(zip(out, want, strict=True)):
      self.assertAlmostEqual(o, w, places=9, msg=f"frame {k}")
    steps = [b - a for a, b in zip(out, out[1:], strict=False)]
    self.assertGreaterEqual(min(steps), -STEP - 1e-9)

  def test_a_driver_still_accelerating_starts_the_ramp_at_zero(self):
    d = Drive()
    d.override(plan=-1.0, a=0.8)
    self.assertAlmostEqual(d.step(-1.0, a=0.8), -STEP)      # never a request to keep accelerating
    d = Drive()
    d.override(plan=0.4, a=0.8)
    self.assertEqual(d.step(0.4, a=0.8), 0.4)               # a plan above the start is the plan

  def test_the_window_is_1_5_s(self):
    d = Drive()
    d.override(plan=-2.5, mpc=0.0)
    out = d.run(WINDOW + 3, -1.95, a=0.0)
    self.assertAlmostEqual(out[24], -STEP * 25)            # the ramp meets -1.95 on frame 25 (1.25 s)
    for o in out[25:]:
      self.assertAlmostEqual(o, -1.95, places=9)
    d = Drive()
    d.override(plan=-1.0)
    out = d.run(WINDOW, lambda k: -0.3 if k < WINDOW - 1 else -3.0, a=0.0)   # a plan that drops late in the window
    self.assertAlmostEqual(out[-1], -0.3 - STEP)
    self.assertFalse(d.r.active)
    self.assertEqual(d.step(-3.0, a=0.0), -3.0)             # one frame after the window: upstream's plan

  def test_a_plan_above_the_limit_is_untouched(self):
    d = Drive()
    d.override(plan=-0.1)
    plans = [-0.1, -0.12, 0.2, 0.25, 0.2, 0.15]
    self.assertEqual(d.run(len(plans), lambda k: plans[k], a=-0.1), plans)

  def test_a_fall_after_a_rise_is_limited_again(self):
    # the limit follows what was let through: a plan that rises and falls again inside the window falls at the ramp
    d = Drive()
    d.override(plan=-0.2)
    d.run(5, 0.3, a=0.0)
    self.assertAlmostEqual(d.step(-1.5, a=0.0), 0.3 - STEP)

  def test_the_exceptions_skip_the_ramp_for_the_rest_of_the_reentry(self):
    for label, kw in (("fcw", {"fcw": True}), ("crash", {"crash": 1.0}), ("mpc", {"mpc": -2.01}),
                      ("mpc nan", {"mpc": float("nan")}), ("crash nan", {"crash": float("nan")})):
      with self.subTest(label), mock.patch.object(rr.cloudlog, "info") as info:
        d = Drive()
        d.override(plan=-1.5)
        d.run(3, -1.5, a=0.0)                               # bound for 3 frames
        self.assertEqual(d.step(-1.8, a=0.0, **kw), -1.8)  # the urgent frame passes the plan
        self.assertFalse(d.r.active)
        self.assertEqual(d.run(5, -1.9, a=0.0), [-1.9] * 5)  # and the ramp does not come back
        self.assertEqual(len(info.call_args_list), 1)
        self.assertTrue(f"end={label.split()[0]} " in info.call_args_list[0].args[0] + " ", label)

  def test_an_mpc_plan_of_exactly_minus_2_keeps_the_ramp(self):
    d = Drive()
    d.override(plan=-1.5)
    self.assertAlmostEqual(d.step(-1.5, a=0.0, mpc=-2.0), -STEP)

  def test_urgent_on_the_first_frame_back(self):
    d = Drive()
    d.override(plan=-2.4)
    self.assertEqual(d.step(-2.4, a=0.0, mpc=-2.4), -2.4)
    self.assertFalse(d.r.active)

  def test_below_3_m_s_the_reentry_is_upstreams(self):
    d = Drive()
    d.override(plan=-1.2, v=2.9)
    self.assertEqual(d.run(10, -1.2, v=2.9), [-1.2] * 10)
    self.assertFalse(d.r.active)
    d = Drive()
    d.override(plan=-1.2, v=3.0)
    self.assertLess(d.step(-1.2, v=3.0, a=0.0), 0.0)
    self.assertTrue(d.r.active)
    # the gate is read on the first frame back only: slowing below 3 m/s inside the window keeps the ramp
    self.assertGreater(d.step(-1.2, v=2.0, a=0.0), -1.2)

  def test_a_new_override_rearms_and_one_inside_the_window_ends_it(self):
    d = Drive()
    d.override(plan=-1.5)
    d.run(5, -1.5, a=0.0)
    self.assertTrue(d.r.active)
    self.assertEqual(d.step(-1.5, reset=True, a=0.3), -1.5)  # the driver presses the gas again
    self.assertFalse(d.r.active)
    self.assertAlmostEqual(d.step(-1.5, a=0.3), -STEP)      # and lifts off again: a fresh ramp from min(0.3, 0)
    self.assertTrue(d.r.active)

  def test_engagement_is_a_reentry_too(self):
    # reset_state is also what disengaged looks like: the first engaged frame starts a ramp
    d = Drive()
    self.assertEqual(d.step(-1.3, reset=True, a=-0.3), -1.3)
    self.assertAlmostEqual(d.step(-1.3, a=-0.3), -0.3 - STEP)

  def test_non_finite_inputs(self):
    d = Drive()
    d.override(plan=-1.5)
    self.assertEqual(d.step(-1.5, a=float("nan")), -1.5)    # unknown aEgo: no ramp
    self.assertFalse(d.r.active)
    d = Drive()
    d.override(plan=-1.5, v=float("nan"))
    self.assertEqual(d.step(-1.5, v=float("nan")), -1.5)    # unknown speed: no ramp
    d = Drive()
    d.override(plan=-1.5)
    d.step(-1.5, a=0.0)
    o = d.step(float("nan"), a=0.0)                         # a NaN plan passes as it is (upstream's behavior)
    self.assertTrue(math.isnan(o))
    self.assertFalse(d.r.active)

  def test_one_log_line_per_reentry_that_bound(self):
    with mock.patch.object(rr.cloudlog, "info") as info:
      d = Drive()
      d.override(plan=-1.5)
      d.run(WINDOW + 2, -1.5, a=0.0)
      lines = [c.args[0] for c in info.call_args_list]
      self.assertEqual(len(lines), 1, lines)
      self.assertTrue(lines[0].startswith("reentryramp end=time t=1.50 v=15.0 a0=0.00 plan0=-1.5 short="), lines[0])
      self.assertTrue(int(lines[0].rsplit("frames=", 1)[1]) in (19, 20), lines[0])   # 0.075 per frame from 0 meets -1.5 on frame 19-20
      info.reset_mock()
      d = Drive()
      d.override(plan=0.2)
      d.run(WINDOW + 2, 0.2, a=0.0)                         # never bound: nothing to say
      self.assertEqual(info.call_args_list, [])
    with mock.patch.object(rr.cloudlog, "info", side_effect=RuntimeError("log down")):
      d = Drive()
      d.override(plan=-1.5)
      out = d.run(WINDOW + 2, -1.5, a=0.0)
      self.assertEqual(out[-1], -1.5)                       # a broken log changes nothing

  def test_applies_to_this_car_with_openpilot_longitudinal_only(self):
    def cp(fp, brand='honda', op_long=True):
      return SimpleNamespace(brand=brand, carFingerprint=fp, openpilotLongitudinalControl=op_long)
    sp = SimpleNamespace(flags=0)
    self.assertTrue(reentry_ramp_applies(cp(CAR.HONDA_ACCORD_9G_AU), sp))
    self.assertFalse(reentry_ramp_applies(cp(CAR.HONDA_ACCORD_9G_AU), SimpleNamespace(flags=HondaFlagsSP.ELESYS_STOCK_ACC.value)))
    self.assertFalse(reentry_ramp_applies(cp(CAR.HONDA_ACCORD_9G_AU, op_long=False), sp))
    self.assertFalse(reentry_ramp_applies(cp(CAR.HONDA_CIVIC), sp))
    self.assertFalse(reentry_ramp_applies(cp("TOYOTA_COROLLA_TSS2", brand='toyota'), sp))
    self.assertFalse(reentry_ramp_applies(SimpleNamespace(), sp))   # never raises


# ---------------------------------------------------------------------------------------------------------------------
# through the real planner

V_EGO = 15.0


class _SM(dict):
  def __init__(self, services):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.updated = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, 1)

  def all_checks(self, service_list=None) -> bool:
    return True


def build_sm(override: bool, a_ego: float, e2e: float, lead_d: float | None = None) -> _SM:
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)
  rs = messaging.new_message('radarState')
  if lead_d is not None:
    lead = rs.radarState.leadOne
    lead.present, lead.dRel, lead.vLead, lead.vLeadK, lead.vRel, lead.modelProb = True, lead_d, 0.0, 0.0, -V_EGO, 1.0
    lead.radar, lead.radarTrackId = True, 7
  services['radarState'] = rs.radarState.as_reader()
  cs = messaging.new_message('carState')
  cs.carState.vEgo, cs.carState.aEgo = V_EGO, a_ego
  cs.carState.vCruise = cs.carState.vCruiseCluster = 100.0
  cs.carState.gasPressed = override
  services['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = LongCtrlState.off if override else LongCtrlState.pid
  services['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.experimentalMode = True
  ss.selfdriveState.enabled = True
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


def planner(fingerprint=CAR.HONDA_ACCORD_9G_AU, ramp=True):
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  CP = structs.CarParams()
  CP.brand = 'honda'
  CP.carFingerprint = fingerprint
  CP.openpilotLongitudinalControl = True
  CP.steerRatio = 15.0
  CP.wheelbase = 2.7
  CP.longitudinalActuatorDelay = 0.6
  pl = LongitudinalPlanner(CP, custom.CarParamsSP.new_message().as_reader(), init_v=V_EGO)
  if not ramp:
    pl.reentry_ramp = None
  return pl


def drive(pl, n_override=20, n_after=50, e2e=-1.6, a_override=0.3, a_after=-0.2, lead_d=None):
  out = []
  for k in range(n_override + n_after):
    over = k < n_override
    pl.update(cast(Any, build_sm(over, a_override if over else a_after, e2e, lead_d)))
    out.append(float(pl.output_a_target))
  return out


class TestReentryRampPlanner(OpenpilotTestCase):
  def test_this_car_ramps_the_lift_off_in(self):
    pl = planner()
    self.assertIsNotNone(pl.reentry_ramp)
    out = drive(pl)
    ref = drive(planner(ramp=False))
    self.assertEqual(out[:20], ref[:20], "the override is untouched")
    self.assertAlmostEqual(ref[20], -1.6, places=3)         # upstream: the e2e plan at once
    after = out[20:]
    self.assertAlmostEqual(after[0], -0.2 - STEP, places=6)  # from min(aEgo -0.2, 0) on the first frame back
    steps = [b - a for a, b in zip([-0.2] + after, after, strict=False)]
    self.assertGreaterEqual(min(steps[:WINDOW]), -STEP - 1e-6, "at most 1.5 m/s^3 inside the window")
    met = next(k for k, (o, r) in enumerate(zip(after, ref[20:], strict=True)) if abs(o - r) < 1e-9)
    self.assertAlmostEqual(met * DT_MDL, 1.4 / rr.REENTRY_RAMP_JERK - DT_MDL, delta=2 * DT_MDL)   # ~0.9 s to meet -1.6
    self.assertLess(met, WINDOW)

  def test_every_other_car_is_upstream(self):
    pl = planner(CAR.HONDA_CIVIC)
    self.assertIsNone(pl.reentry_ramp)
    a, b = drive(pl), drive(planner(CAR.HONDA_CIVIC, ramp=False))
    self.assertEqual(a, b)
    self.assertAlmostEqual(a[20], -1.6, places=3)

  def test_stock_acc_mode_is_upstream(self):
    from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
    CP = structs.CarParams()
    CP.brand, CP.carFingerprint, CP.openpilotLongitudinalControl = 'honda', CAR.HONDA_ACCORD_9G_AU, True
    CP_SP = custom.CarParamsSP.new_message()
    CP_SP.flags = HondaFlagsSP.ELESYS_STOCK_ACC.value
    self.assertIsNone(LongitudinalPlanner(CP, CP_SP.as_reader(), init_v=V_EGO).reentry_ramp)

  def test_a_stopped_car_close_ahead_skips_the_ramp(self):
    # lifting off at 15 m/s with a stopped car 40 m ahead: the MPC plans past -2.0 (and flags a crash), so the plan is
    # passed at once, exactly as upstream's
    pl = planner()
    out = drive(pl, n_override=20, n_after=10, e2e=-0.5, lead_d=40.0)
    ref = drive(planner(ramp=False), n_override=20, n_after=10, e2e=-0.5, lead_d=40.0)
    self.assertLess(ref[20], -2.0)
    self.assertEqual(out, ref)
