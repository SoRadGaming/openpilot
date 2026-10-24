"""FORK(CLOSE-FOLLOW): close follow in queues (close_follow.py, push13: shadow).

Pinned here: the gap schedule (0.5 s at a stop, 1.0 s at 18 km/h, the personality's by CF_FADE_KPH; the stopped gap
stays 6 m), the fade, the gap's trips (each drops it on the same frame), the lift (never past the MPC candidate or
cruise, nothing on the frame a lead brakes, a hard trip or a pedal, then a 3 s lockout and a slow rise), and the
planner: on this car mode 1 (shadow) publishes exactly what mode 0 does - every frame, bit for bit - while its own log
says what it would have done, and does all of its work after longitudinalPlan is sent; mode 2 (live) takes close
follow's candidates, blended by the gap's authority while it ramps in, and is the plan's own MPC on the frame a lead
brakes; an FCW (the car's, or close follow's own MPC crash flag) holds the gap and the lift out 5 s, the lift never
back before the gap; every other car, stock ACC mode and mode 0 never build it.

Big outputs are compared with assert_same_frames(): unittest's assertEqual on two 400-frame lists builds a difflib diff
that takes minutes, so a shadow leak would look like a hang rather than a failure.
"""
import math
import random
from typing import Any, cast

import numpy as np

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib.drive_helpers import CONTROL_N, get_accel_from_plan
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (LongitudinalMpc, T_IDXS as T_IDXS_MPC,
                                                                           get_T_FOLLOW, get_jerk_factor)
from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.sunnypilot.selfdrive.controls.lib import close_follow as cf
from openpilot.sunnypilot.selfdrive.controls.lib.close_follow import (CAP, CF_FADE_SPEED, NO_LEAD, GapGuard, Inhibit,
                                                                      Inputs, Lead, LeadFollowLift, ModelView,
                                                                      close_follow_applies, close_jerk_factor,
                                                                      close_t_follow, desired_gap, lift_speed_weight,
                                                                      read_mode, speed_weight)
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_id

P = log.LongitudinalPersonality
AGGRESSIVE, STANDARD, RELAXED = int(P.aggressive), int(P.standard), int(P.relaxed)
LongCtrlState = structs.CarControl.Actuators.LongControlState
CONTROL_N_T_IDX = ModelConstants.T_IDXS[:CONTROL_N]   # the planner's control points (longitudinal_planner.py)
HOLD = round(cf.GAP_HOLD_S / DT_MDL)
GAP_FULL = HOLD + round(1. / cf.GAP_RISE / DT_MDL) + 2
LIFT_FULL = round(cf.LIFT_HOLD_S / DT_MDL) + round(1. / cf.AUTHORITY_RISE / DT_MDL) + \
  round(max(CAP.values()) / cf.BOOST_RISE / DT_MDL) + 10


def assert_same_frames(tc, a, b, what: str) -> None:
  """a == b, frame by frame, failing at once with the first frame that differs (assertEqual's difflib on two 400-frame
  lists takes minutes to say the same)."""
  tc.assertEqual(len(a), len(b), f"{what}: frame count")
  for k, (x, y) in enumerate(zip(a, b, strict=True)):
    if x != y:
      tc.fail(f"{what}: frame {k} of {len(a)} differs:\n  {x!r:.1500}\n  {y!r:.1500}")


def lead(d=20., v=6., a=0., prob=0.95, radar=True, track=7, v_ego=None) -> Lead:
  return Lead(True, d, v, v - (v if v_ego is None else v_ego), a, prob, radar, track)


def model(v=7., a=0., n=ModelConstants.IDX_N, lead_v=None, **kw) -> ModelView:
  base = {'a': a, 'should_stop': False, 'vel': np.full(n, v), 'yaw_rate': np.full(n, 0.001), 'curvature': 0.,
          'hard_brake': False, 'lane_change': False,
          'lead_v': tuple(np.full(len(ModelConstants.LEAD_T_IDXS), v if lead_v is None else lead_v) for _ in range(3))}
  base.update(kw)
  return ModelView(**base)


def inputs(v=7., lead_one=None, lead_two=NO_LEAD, md=None, **kw) -> Inputs:
  base = {'engaged': True, 'is_e2e': True, 'v_ego': v, 'personality': AGGRESSIVE,
          'lead_one': lead(v=v, v_ego=v) if lead_one is None else lead_one, 'lead_two': lead_two, 'held': False,
          'gas': False, 'brake': False, 'standstill': False, 'car_fcw': False, 'force_decel': False, 'steer_lat_accel': 0.,
          'allow_throttle': True, 'model': model(v) if md is None else md}
  base.update(kw)
  return Inputs(**base)


class TestSchedule(OpenpilotTestCase):
  def test_constants(self):
    self.assertEqual((cf.CF_T_FOLLOW_BP, cf.CF_T_FOLLOW_V, cf.CF_FADE_KPH, cf.CF_JERK_FACTOR), ([0., 5.], [0.5, 1.0], 35., 0.5))
    self.assertAlmostEqual(CF_FADE_SPEED, 35. / 3.6)
    self.assertEqual((cf.GAP_BP, cf.LIFT_SPEED_BP, cf.GAP_LEAD_BRAKE, cf.GAP_HOLD_S, cf.GAP_RISE),
                     ([3., 10.], [5., 8.], -0.5, 1.0, 0.5))
    self.assertEqual(CAP, {AGGRESSIVE: 0.5, STANDARD: 0.4, RELAXED: 0.3})
    # the holds and rates the safety tests below count frames by (60 / 20 / 100 frames at 20 Hz)
    self.assertEqual((cf.LIFT_HOLD_S, cf.AUTHORITY_RISE, cf.AUTHORITY_FALL, cf.BOOST_RISE, cf.FCW_HOLD_S),
                     (3.0, 0.5, 4.0, 0.25, 5.0))
    self.assertEqual((round(cf.LIFT_HOLD_S / DT_MDL), HOLD, round(cf.FCW_HOLD_S / DT_MDL)), (60, 20, 100))
    self.assertEqual(read_mode.__module__, cf.__name__)

  def test_follow_time_near_a_stop_and_at_18_kph(self):
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      t_p = get_T_FOLLOW(p)
      self.assertAlmostEqual(close_t_follow(0., t_p), 0.5)
      self.assertAlmostEqual(close_t_follow(2.5, t_p), 0.75)
      self.assertAlmostEqual(close_t_follow(5., t_p), 1.0)

  def test_fades_back_to_the_personality_by_35_kph(self):
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      t_p = get_T_FOLLOW(p)
      self.assertAlmostEqual(close_t_follow(CF_FADE_SPEED, t_p), t_p)
      self.assertAlmostEqual(close_t_follow(CF_FADE_SPEED + 5., t_p), t_p)
      self.assertAlmostEqual(close_t_follow(30., t_p), t_p)
      # halfway between 18 and 35 km/h: halfway between 1.0 s and the personality's
      self.assertAlmostEqual(close_t_follow((5. + CF_FADE_SPEED) / 2, t_p), (1.0 + t_p) / 2)
      vs = np.linspace(0., 15., 301)
      ts = [close_t_follow(v, t_p) for v in vs]
      self.assertTrue(all(b >= a - 1e-12 for a, b in zip(ts, ts[1:], strict=False)), "the gap only grows with speed")
      self.assertTrue(all(t <= t_p + 1e-12 for t in ts), "never longer than the personality's")

  def test_jerk_factor(self):
    self.assertEqual(close_jerk_factor(3., get_jerk_factor(P.standard)), 0.5)
    self.assertAlmostEqual(close_jerk_factor(CF_FADE_SPEED, get_jerk_factor(P.standard)), 1.0)
    self.assertEqual(close_jerk_factor(8., get_jerk_factor(P.aggressive)), 0.5)

  def test_speed_weights(self):
    self.assertEqual((speed_weight(0.), speed_weight(5.), speed_weight(CF_FADE_SPEED), speed_weight(20.)), (1., 1., 0., 0.))
    self.assertEqual((lift_speed_weight(4.), lift_speed_weight(5.), lift_speed_weight(CF_FADE_SPEED)), (0., 0., 0.))
    self.assertAlmostEqual(lift_speed_weight(8.), 1.)
    self.assertTrue(0. < lift_speed_weight(6.5) < 1. and 0. < lift_speed_weight(9.) < 1.)

  def test_the_stopped_gap_stays_6_m(self):
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      self.assertEqual(desired_gap(0., 0., close_t_follow(0., get_T_FOLLOW(p))), 6.0)
    self.assertAlmostEqual(desired_gap(5., 5., close_t_follow(5., 1.25)), 11.0)

  def test_read_mode(self):
    class _P:
      def __init__(self, v):
        self.v = v

      def get(self, key, return_default=False):
        return self.v
    self.assertEqual([read_mode(_P(v)) for v in (0, 1, 2, "2", 3, -1, None, "x")], [0, 1, 2, 2, 0, 0, 0, 0])
    self.assertEqual(read_mode(Params()), cf.MODE_SHADOW, "default 1: shadow")


class TestGapGuard(OpenpilotTestCase):
  def run_guard(self, frames, g=None, requested=True, fcw=False, **kw):
    g = g or GapGuard()
    out = None
    for _ in range(frames):
      out = g.update(inputs(**kw), requested, fcw)
    return g, out

  def test_holds_then_rises_at_half_per_second(self):
    g = GapGuard()
    outs = [g.update(inputs(), True, False) for _ in range(GAP_FULL)]
    self.assertEqual(outs[:HOLD], [0.] * HOLD)
    self.assertAlmostEqual(outs[HOLD], cf.GAP_RISE * DT_MDL)
    self.assertEqual(outs[-1], 1.)
    self.assertLessEqual(max(np.diff(outs)), cf.GAP_RISE * DT_MDL + 1e-12)

  def trip_cases(self, v=7.):
    held = lead(v=v, v_ego=v, track=held_id(7))
    return [
      ({'lead_one': NO_LEAD}, Inhibit.noLead),
      ({'held': True}, Inhibit.noLead),
      ({'lead_one': lead(v=v, v_ego=v, prob=0.6)}, Inhibit.leadUncertain),
      ({'lead_one': lead(v=v, v_ego=v, radar=False, track=-1)}, Inhibit.visionLead),
      ({'lead_one': lead(v=v, v_ego=v, a=-0.6)}, Inhibit.leadBraking),
      ({'lead_two': lead(d=40., v=v, v_ego=v, a=-0.6)}, Inhibit.leadBraking),
      ({'lead_one': lead(v=v, v_ego=v, a=float('nan'))}, Inhibit.leadBraking),
      ({'lead_slowing': True}, Inhibit.leadBraking),
      ({'lead_one': lead(d=15., v=v, v_ego=v)}, Inhibit.leadChanged),
      ({'gas': True}, Inhibit.driver),
      ({'brake': True}, Inhibit.driver),
      ({'force_decel': True}, Inhibit.forceDecel),
      ({'car_fcw': True}, Inhibit.fcw),
      ({'engaged': False}, Inhibit.inactive),
      ({'v': CF_FADE_SPEED + cf.CF_MPC_MARGIN + 0.1}, Inhibit.speed),
      ({'lead_one': held, 'held': True}, Inhibit.noLead),
    ]

  def test_every_trip_drops_it_on_the_same_frame(self):
    for kw, reason in self.trip_cases():
      with self.subTest(reason=reason, kw=kw):
        g, out = self.run_guard(GAP_FULL)
        self.assertEqual(out, 1.)
        _, out = self.run_guard(1, g=g, **kw)
        self.assertEqual(out, 0.)
        self.assertEqual(g.inhibit, reason)
        # and it waits out the hold again
        _, out = self.run_guard(HOLD, g=g)
        self.assertEqual(out, 0.)

  def test_not_requested_and_planner_fcw(self):
    g, _ = self.run_guard(GAP_FULL)
    _, out = self.run_guard(1, g=g, requested=False)
    self.assertEqual((out, g.inhibit), (0., Inhibit.notRequested))
    g, _ = self.run_guard(GAP_FULL)
    _, out = self.run_guard(1, g=g, fcw=True)
    self.assertEqual((out, g.inhibit), (0., Inhibit.fcw))

  def test_an_fcw_keeps_it_out_for_5_s(self):
    g, _ = self.run_guard(GAP_FULL)
    self.run_guard(1, g=g, fcw=True)
    _, out = self.run_guard(round(cf.FCW_HOLD_S / DT_MDL) - 1, g=g)
    self.assertEqual((out, g.inhibit), (0., Inhibit.fcw))
    _, out = self.run_guard(HOLD + 5, g=g)
    self.assertGreater(out, 0.)

  def test_a_vision_lead_is_fine_under_2_m_s(self):
    _, out = self.run_guard(GAP_FULL, v=1.5, lead_one=lead(d=7., v=1.5, v_ego=1.5, radar=False, track=-1))
    self.assertEqual(out, 1.)

  def test_a_tracked_closing_lead_is_not_a_new_car(self):
    g = GapGuard()
    for i in range(GAP_FULL):
      g.update(inputs(v=7., lead_one=lead(d=30. - 2. * DT_MDL * i, v=5., v_ego=7.)), True, False)
    self.assertEqual((g.authority, g.inhibit), (1., Inhibit.none))

  def test_a_mild_lead_brake_does_not_trip(self):
    _, out = self.run_guard(GAP_FULL, lead_one=lead(v=7., v_ego=7., a=-0.45))
    self.assertEqual(out, 1.)


class TestLeadSpeedWindow(OpenpilotTestCase):
  def test_a_lead_slowing_at_0_6_is_braking_before_alead_says_so(self):
    w = cf.LeadSpeedWindow()
    self.assertEqual((round(cf.LEAD_SLOWING_T / DT_MDL), cf.LEAD_SLOWING_ACCEL), (10, -0.5))
    self.assertFalse(any(w.update(lead(v=8., a=-0.1)) for _ in range(30)))
    # 0.6 m/s^2 while radard's aLeadK (a filter) still reads -0.1: caught within the window
    outs = [w.update(lead(v=8. - 0.6 * DT_MDL * (k + 1), a=-0.1)) for k in range(12)]
    self.assertTrue(outs[-1])
    self.assertLessEqual(outs.index(True), 10)
    self.assertAlmostEqual(w.slope, -0.6, places=6)

  def test_not_braking(self):
    # 0.4 m/s^2 is not
    w = cf.LeadSpeedWindow()
    self.assertFalse(any(w.update(lead(v=8. - 0.4 * DT_MDL * k, a=-0.2)) for k in range(30)))
    # nor a falling speed while aLeadK says the lead accelerates (radar jitter after a track change)
    w = cf.LeadSpeedWindow()
    self.assertFalse(any(w.update(lead(v=8. - 0.6 * DT_MDL * k, a=0.3)) for k in range(30)))
    # nor one jittery sample of 0.3 m/s in a steady lead
    w = cf.LeadSpeedWindow()
    self.assertFalse(any(w.update(lead(v=8. - (0.3 if k == 25 else 0.), a=-0.1)) for k in range(40)))
    # nor fewer than LEAD_SLOWING_MIN_N samples of a car
    w = cf.LeadSpeedWindow()
    self.assertFalse(any(w.update(lead(v=8. - 2. * k, a=-1.)) for k in range(cf.LEAD_SLOWING_MIN_N - 1)))

  def test_a_new_car_starts_again(self):
    w = cf.LeadSpeedWindow()
    for _ in range(10):
      w.update(lead(v=8., a=-0.1, track=7))
    self.assertFalse(w.update(lead(v=6., a=-0.1, track=8)), "another radar track")
    self.assertEqual(len(w.v), 1)
    w.update(lead(v=6., radar=False, track=-1))
    self.assertEqual(len(w.v), 1, "radar to vision")
    w.update(NO_LEAD)
    self.assertEqual(len(w.v), 0)


FAR = 40.   # m: far past the close gap at 7 m/s


def lift_run(frames, lift=None, a_mpc=1.0, a_cruise=2.0, fcw=False, t_follow=1.0, requested=True, **kw):
  lift = lift or LeadFollowLift()
  out = None
  for _ in range(frames):
    inp = inputs(**dict({'lead_one': lead(d=FAR, v=7., v_ego=7.)}, **kw))
    out = lift.update(inp, requested, a_mpc, a_cruise, fcw, t_follow)
  return lift, out


class TestLift(OpenpilotTestCase):
  def test_lifts_a_long_gap_up_to_the_cap(self):
    lift, out = lift_run(LIFT_FULL, v=8.5, lead_one=lead(d=FAR, v=8.5, v_ego=8.5))
    self.assertEqual(lift.inhibit, Inhibit.none)
    self.assertAlmostEqual(out, CAP[AGGRESSIVE] * lift_speed_weight(8.5))

  def test_never_past_the_mpc_or_cruise(self):
    _, out = lift_run(LIFT_FULL, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), a_mpc=0.1)
    self.assertAlmostEqual(out, 0.1)
    _, out = lift_run(LIFT_FULL, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), a_cruise=0.05)
    self.assertAlmostEqual(out, 0.05)
    _, out = lift_run(LIFT_FULL, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), md=model(8., a=-0.05), a_mpc=-0.3)
    self.assertEqual(out, -0.05, "an MPC braking harder than the model is left to the min()")

  def test_rise_is_rate_limited(self):
    lift = LeadFollowLift()
    outs = [lift.update(inputs(v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), md=model(8.)), True, 1.0, 2.0, False, 1.0)
            for _ in range(LIFT_FULL)]
    self.assertLessEqual(max(np.diff(outs)), cf.BOOST_RISE * DT_MDL + 1e-9)

  def test_inside_the_deadband_nothing(self):
    v = 8.
    d = desired_gap(v, v, 1.0) + cf.GAP_BP[0] - 0.5
    lift, out = lift_run(LIFT_FULL, v=v, lead_one=lead(d=d, v=v, v_ego=v))
    self.assertEqual((lift.inhibit, out), (Inhibit.none, 0.))

  def test_speed_window(self):
    for v, want_zero in ((4.9, True), (CF_FADE_SPEED, True), (CF_FADE_SPEED + 1., True), (8., False)):
      with self.subTest(v=v):
        _, out = lift_run(LIFT_FULL, v=v, lead_one=lead(d=FAR + 10., v=v, v_ego=v), md=model(v))
        self.assertEqual(out == 0., want_zero)

  def test_hard_trips_leave_nothing_on_the_same_frame(self):
    v = 8.
    base = {'v': v, 'lead_one': lead(d=FAR, v=v, v_ego=v), 'md': model(v)}
    cases = [
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, a=-0.6)}, {}, Inhibit.leadBraking),
      ({'lead_two': lead(d=FAR + 20., v=v, v_ego=v, a=-0.6)}, {}, Inhibit.leadBraking),
      ({'md': model(v, lead_v=[v, v - 1.5, v - 0.5, v, v, v])}, {}, Inhibit.leadBraking),
      ({'lead_slowing': True}, {}, Inhibit.leadBraking),
      ({'lead_one': lead(d=FAR - 10., v=v, v_ego=v)}, {}, Inhibit.leadChanged),
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, track=8)}, {}, Inhibit.leadChanged),
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, radar=False, track=-1)}, {}, Inhibit.visionLead),
      ({'lead_one': NO_LEAD}, {}, Inhibit.noLead),
      ({'held': True}, {}, Inhibit.noLead),
      ({}, {'fcw': True}, Inhibit.fcw),
      ({'car_fcw': True}, {}, Inhibit.fcw),
      ({'gas': True}, {}, Inhibit.driver),
      ({'brake': True}, {}, Inhibit.driver),
      ({'force_decel': True}, {}, Inhibit.forceDecel),
      ({'md': model(v, hard_brake=True)}, {}, Inhibit.hardBrake),
      ({'md': model(v, should_stop=True)}, {}, Inhibit.stop),
      # a hard trip found by the other check still wins over a soft lead trip (a lead under 5 m/s + gas)
      ({'lead_one': lead(d=FAR, v=4., v_ego=v), 'gas': True}, {}, Inhibit.driver),
    ]
    for kw, args, reason in cases:
      with self.subTest(reason=reason, kw=kw):
        lift, out = lift_run(LIFT_FULL, **base)
        self.assertGreater(out, 0.3)
        _, out = lift_run(1, lift=lift, **dict(base, **kw), **args)
        self.assertEqual((lift.inhibit, out, lift.boost), (reason, 0., 0.))

  def test_after_a_hard_trip_nothing_for_3_s_then_a_slow_rise(self):
    # one frame of each hard trip, then clean frames: the lift stays at the model's own accel (0 here) for exactly
    # LIFT_HOLD_S (60 frames) with its authority at 0, and then rises no faster than its authority (0.5/s) x CAP and
    # BOOST_RISE (0.25 m/s^3) allow - a braking lead or a pedal locks it out, it does not merely dip
    v = 8.
    base = {'v': v, 'lead_one': lead(d=FAR, v=v, v_ego=v), 'md': model(v)}
    hold = 60
    cases = [
      # (the trip frame, its extra arguments, the clean frames after it, the reason)
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, a=-0.6)}, {}, {}, Inhibit.leadBraking),
      ({'lead_two': lead(d=FAR + 20., v=v, v_ego=v, a=-0.6)}, {}, {}, Inhibit.leadBraking),
      ({'lead_slowing': True}, {}, {}, Inhibit.leadBraking),
      ({'md': model(v, lead_v=[v, v - 1.5, v - 0.5, v, v, v])}, {}, {}, Inhibit.leadBraking),
      ({'lead_one': lead(d=FAR - 10., v=v, v_ego=v)}, {}, {'lead_one': lead(d=FAR - 10., v=v, v_ego=v)}, Inhibit.leadChanged),
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, track=8)}, {}, {'lead_one': lead(d=FAR, v=v, v_ego=v, track=8)},
       Inhibit.leadChanged),
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, radar=False, track=-1)}, {}, {}, Inhibit.visionLead),
      ({'lead_one': NO_LEAD}, {}, {}, Inhibit.noLead),
      ({'held': True}, {}, {}, Inhibit.noLead),
      ({}, {'fcw': True}, {}, Inhibit.fcw),
      ({'car_fcw': True}, {}, {}, Inhibit.fcw),
      ({'gas': True}, {}, {}, Inhibit.driver),
      ({'brake': True}, {}, {}, Inhibit.driver),
      ({'force_decel': True}, {}, {}, Inhibit.forceDecel),
      ({'md': model(v, hard_brake=True)}, {}, {}, Inhibit.hardBrake),
      ({'md': model(v, should_stop=True)}, {}, {}, Inhibit.stop),
    ]
    rise = cf.BOOST_RISE * DT_MDL
    for trip, args, after, reason in cases:
      with self.subTest(reason=reason, trip=trip):
        lift, out = lift_run(LIFT_FULL, **base)
        self.assertAlmostEqual(out, CAP[AGGRESSIVE])
        _, out = lift_run(1, lift=lift, **dict(base, **trip), **args)
        self.assertEqual((lift.inhibit, out, lift.authority), (reason, 0., 0.))
        clean = dict(base, **after)
        outs, auths = [], []
        for _ in range(hold + 40):
          _, out = lift_run(1, lift=lift, **clean)
          outs.append(out)
          auths.append(lift.authority)
        self.assertEqual(outs[:hold], [0.] * hold, "nothing for 3 s after the trip")
        self.assertEqual(auths[:hold], [0.] * hold)
        self.assertGreater(outs[hold], 0., "and back once the 3 s are up")
        for k in range(1, 40):
          self.assertAlmostEqual(auths[hold + k - 1], k * cf.AUTHORITY_RISE * DT_MDL, places=9)
          self.assertLessEqual(outs[hold + k - 1], min(k * rise, auths[hold + k - 1] * CAP[AGGRESSIVE]) + 1e-12)

  def test_soft_trips_fade_within_a_quarter_second(self):
    v = 8.
    base = {'v': v, 'lead_one': lead(d=FAR, v=v, v_ego=v), 'md': model(v)}
    cases = [
      ({'steer_lat_accel': 1.2}, Inhibit.lateral),
      ({'allow_throttle': False}, Inhibit.coast),
      ({'md': model(v, lane_change=True)}, Inhibit.laneChange),
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, prob=0.6)}, Inhibit.leadUncertain),
      ({'md': model(v, vel=np.interp(cf.T_IDXS, [0., 2.5, 5., 10.], [v, v - 1.5, v, v]))}, Inhibit.planSlowing),
    ]
    for kw, reason in cases:
      with self.subTest(reason=reason):
        lift, before = lift_run(LIFT_FULL, **base)
        _, out = lift_run(1, lift=lift, **dict(base, **kw))
        self.assertEqual(lift.inhibit, reason)
        self.assertLess(out, before)
        _, out = lift_run(round(0.25 / DT_MDL), lift=lift, **dict(base, **kw))
        self.assertEqual(out, 0.)

  def test_model_braking(self):
    lift, _ = lift_run(LIFT_FULL, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), md=model(8.))
    _, out = lift_run(1, lift=lift, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), md=model(8., a=-0.6))
    self.assertEqual((lift.inhibit, out), (Inhibit.modelBraking, -0.6))

  def test_idle_states(self):
    for kw, requested, reason in (({'is_e2e': False}, True, Inhibit.notE2e), ({'engaged': False}, True, Inhibit.inactive),
                                  ({}, False, Inhibit.notRequested)):
      with self.subTest(reason=reason):
        lift, _ = lift_run(LIFT_FULL, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.))
        _, out = lift_run(1, lift=lift, requested=requested, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), **kw)
        self.assertEqual((lift.inhibit, out, lift.authority), (reason, 0., 0.))

  def test_non_finite_is_the_model(self):
    lift, out = lift_run(LIFT_FULL, v=8., lead_one=lead(d=FAR, v=8., v_ego=8.), a_mpc=float('nan'))
    self.assertEqual((lift.inhibit, out), (Inhibit.invalid, 0.))

  def test_random_frames_never_past_the_radar_plan_and_nothing_while_a_lead_brakes(self):
    rng = random.Random(1024)
    lift = LeadFollowLift()
    lifted = 0
    for k in range(20000):
      v = 7.5 + 2.2 * math.sin(k / 300.) + rng.uniform(-0.05, 0.05)    # in and out of the lift's band, slowly
      a_lead = rng.choice([0.2, -0.3, -0.49, -0.51, -1.5, float('nan')]) if rng.random() < 0.01 else 0.
      d = rng.uniform(5., 60.) if rng.random() < 0.002 else FAR
      inp = inputs(v=v, lead_one=lead(d=d, v=v + rng.uniform(-0.3, 0.3), v_ego=v, a=a_lead),
                   md=model(v, a=rng.uniform(-0.6, 0.6) if rng.random() < 0.3 else rng.uniform(-0.1, 0.2)),
                   gas=rng.random() < 0.002)
      a_mpc, a_cruise = rng.uniform(-1., 1.5), rng.uniform(-0.5, 2.)
      out = lift.update(inp, True, a_mpc, a_cruise, rng.random() < 0.002, 1.0)
      a_model = inp.model.a
      self.assertGreaterEqual(out, a_model)
      self.assertLessEqual(out, max(a_model, min(a_mpc, a_cruise)) + 1e-12)
      if not (a_lead >= cf.GAP_LEAD_BRAKE) or inp.gas:
        self.assertEqual(out, a_model)
      lifted += out > a_model
    self.assertGreater(lifted, 1000, "the property ran on frames that did lift")


# --- through the real planner ---

V0 = 6.0


class _SM(dict):
  def __init__(self, services):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.updated = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, 1)

  def all_checks(self, service_list=None) -> bool:
    return True


def build_sm(v_ego, d_rel, v_lead, a_lead=0., e2e=0., engaged=True, experimental=True, gas=False, radar=True, track=7,
             personality=P.aggressive, stock_fcw=False) -> _SM:
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)
  rs = messaging.new_message('radarState')
  if d_rel is not None:
    ld = rs.radarState.leadOne
    ld.present, ld.dRel, ld.vLead, ld.vLeadK, ld.vRel, ld.modelProb = True, d_rel, v_lead, v_lead, v_lead - v_ego, 0.99
    ld.aLeadK, ld.aLeadTau = a_lead, 1.5
    ld.radar, ld.radarTrackId = radar, track
  services['radarState'] = rs.radarState.as_reader()
  cs = messaging.new_message('carState')
  cs.carState.vEgo, cs.carState.aEgo = v_ego, 0.
  cs.carState.vCruise = cs.carState.vCruiseCluster = 100.0
  cs.carState.gasPressed = gas
  cs.carState.stockFcw = stock_fcw
  cs.carState.standstill = v_ego < 0.1
  services['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = LongCtrlState.pid if engaged else LongCtrlState.off
  services['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.experimentalMode = experimental
  ss.selfdriveState.enabled = engaged
  ss.selfdriveState.personality = personality
  services['selfdriveState'] = ss.selfdriveState.as_reader()
  cc = messaging.new_message('carControl')
  cc.carControl.enabled = engaged
  services['carControl'] = cc.carControl.as_reader()
  m = messaging.new_message('modelV2')
  m.modelV2.orientationRate.z = [0.001] * 33   # nonzero: a straight path divides by zero in SCC vision
  m.modelV2.velocity.x = [v_ego] * 33
  m.modelV2.position.x = [float(i) for i in range(33)]
  m.modelV2.action.desiredAcceleration = e2e
  m.modelV2.meta.disengagePredictions.gasPressProbs = [1.0] * 6
  leads = m.modelV2.init('leadsV3', 3)
  for ml in leads:
    ml.prob = 0.99
    ml.v = [v_lead] * 6
  services['modelV2'] = m.modelV2.as_reader()
  return _SM(services)


def honda_cp(fingerprint=CAR.HONDA_ACCORD_9G_AU, op_long=True):
  CP = structs.CarParams()
  CP.brand = 'honda'
  CP.carFingerprint = fingerprint
  CP.openpilotLongitudinalControl = op_long
  CP.steerRatio = 15.0
  CP.wheelbase = 2.7
  CP.longitudinalActuatorDelay = 0.6
  return CP


def planner(mode, fingerprint=CAR.HONDA_ACCORD_9G_AU, stock_acc=False):
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  Params().put("CloseFollowMode", mode)
  CP_SP = custom.CarParamsSP.new_message()
  if stock_acc:
    CP_SP.flags = HondaFlagsSP.ELESYS_STOCK_ACC.value
  return LongitudinalPlanner(honda_cp(fingerprint), CP_SP.as_reader(), init_v=V0)


def queue(n=400, brake_at=None, e2e=-0.1):
  """A moving queue at 6 m/s (22 km/h): the lead 22 m ahead at our speed (the close gap is ~13 m, the aggressive one
  ~13.5-14 m, the model holding its own longer gap with a small negative accel). From brake_at on the lead brakes."""
  frames = []
  d, v_lead = 22., V0
  for k in range(n):
    a_lead = -1.0 if brake_at is not None and k >= brake_at else 0.
    frames.append(build_sm(V0, d, v_lead, a_lead=a_lead, e2e=e2e))
    v_lead = max(0., v_lead + a_lead * DT_MDL)
    d += (v_lead - V0) * DT_MDL
  return frames


SERVICES = ('longitudinalPlan', 'longitudinalPlanSP', 'customReserved10')


class PM:
  """plannerd's PubMaster, as far as the planner sees it: what was sent per service, and the order of the sends in
  `log` (which a test may share with anything else it wants to place in that order)."""

  def __init__(self, services=SERVICES, log=None):
    self.sock = dict.fromkeys(services)
    self.sent: dict[str, list] = {}
    self.log = [] if log is None else log

  def send(self, service, msg):
    self.log.append(service)
    self.sent.setdefault(service, []).append(msg.to_bytes())


def step(pl, sm, pm=None):
  """One plannerd loop: update() then publish() - where shadow close follow does its work, after the plan is sent."""
  pl.update(cast(Any, sm))
  pl.publish(cast(Any, sm), pm if pm is not None else PM())


def drive(pl, frames):
  out = []
  for sm in frames:
    step(pl, sm)
    out.append((float(pl.output_a_target), bool(pl.output_should_stop), pl.v_desired_trajectory.tolist(),
                pl.a_desired_trajectory.tolist(), pl.j_desired_trajectory.tolist(), int(pl.mpc.source), bool(pl.fcw)))
  return out


def plan_dicts(pm, s, drop=('processingDelay', 'solverExecutionTime')):
  """Every message sent on s, as dicts, without its wall-clock fields (different on every run)."""
  out = []
  for b in pm.sent[s]:
    d = getattr(messaging.log_from_bytes(b), s).to_dict()
    for timing in drop:
      d.pop(timing, None)
    out.append(d)
  return out


def mpc_class(on_update):
  """A LongitudinalMpc for close follow's mpc_factory that calls on_update(mpc, n) after its n-th solve (n from 1)."""
  class _Mpc(LongitudinalMpc):
    def update(self, *args, **kwargs):
      super().update(*args, **kwargs)
      self.n_updates = getattr(self, 'n_updates', 0) + 1
      on_update(self, self.n_updates)
  return _Mpc


def plan_trajectories(mpc):
  """An MPC's (v, a, j) at the planner's control points, as the planner takes them."""
  return (np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, mpc.v_solution), np.interp(CONTROL_N_T_IDX, T_IDXS_MPC, mpc.a_solution),
          np.interp(CONTROL_N_T_IDX, T_IDXS_MPC[:-1], mpc.j_solution))


class TestPlanner(OpenpilotTestCase):
  def test_shadow_changes_nothing(self):
    frames = queue(400, brake_at=300)
    off, shadow = planner(0), planner(1)
    self.assertIsNone(off.close_follow)
    self.assertIsNotNone(shadow.close_follow)
    gap_on = lift_on = would_raise = 0
    out_off, out_shadow = [], []
    for sm in frames:
      # a whole plannerd loop each: in shadow close follow runs in publish(), after the plan is out - and must not
      # leave anything behind that the next plan would see
      for pl, out in ((off, out_off), (shadow, out_shadow)):
        step(pl, sm)
        out.append((float(pl.output_a_target), bool(pl.output_should_stop), pl.v_desired_trajectory.tolist(),
                    pl.a_desired_trajectory.tolist(), pl.j_desired_trajectory.tolist(), int(pl.mpc.source), bool(pl.fcw),
                    float(pl.v_desired_filter.x), float(pl.a_cruise)))
      c = shadow.close_follow
      gap_on += c.gap_active
      lift_on += c.lifting > 0.
      would_raise += c.a_target_close > c.a_target + 1e-3
    assert_same_frames(self, out_off, out_shadow, "shadow: every planner output bit for bit")
    # ...while its own log says it would have acted
    self.assertGreater(gap_on, 100)
    self.assertGreater(lift_on, 20)
    self.assertGreater(would_raise, 20)

  def test_shadow_publishes_the_same_plan_and_its_own_log(self):
    pm_off, pm_shadow = PM(), PM()
    off, shadow = planner(0), planner(1)
    for sm in queue(200):
      for pl, pm in ((off, pm_off), (shadow, pm_shadow)):
        step(pl, sm, pm)
    for s in ('longitudinalPlan', 'longitudinalPlanSP'):
      assert_same_frames(self, plan_dicts(pm_off, s), plan_dicts(pm_shadow, s), s)
    self.assertNotIn('customReserved10', pm_off.sent)
    msgs = [messaging.log_from_bytes(b).customReserved10 for b in pm_shadow.sent['customReserved10']]
    self.assertEqual(len(msgs), 200)
    last = msgs[-1]
    self.assertEqual((last.mode, last.requested, last.gapActive, last.mpcSolved), (1, True, True, True))
    self.assertAlmostEqual(last.tFollow, close_t_follow(V0, 1.25), places=5)
    self.assertAlmostEqual(last.tFollowPersonality, 1.25, places=5)
    self.assertLess(last.desiredGap, last.desiredGapPersonality)
    self.assertGreater(last.aMpcClose, last.aMpc)
    self.assertGreaterEqual(last.aTargetClose, last.aTarget)
    # its cost reaches the route: the close MPC's own solve on every plan it ran, and the whole step on every plan
    self.assertTrue(all(m.mpcSolved for m in msgs))
    self.assertTrue(all(0. < m.mpcSolveTime < 1. for m in msgs))
    self.assertTrue(all(m.stepTime >= m.mpcSolveTime for m in msgs))
    # a PubMaster without the service (an older plannerd, a tool): nothing sent, nothing raised
    pm = PM(['longitudinalPlan', 'longitudinalPlanSP'])
    step(shadow, queue(1)[0], pm)
    self.assertNotIn('customReserved10', pm.sent)
    # no lead: the close MPC does not run, and says so
    step(shadow, build_sm(V0, None, 0.), pm_shadow)
    last = messaging.log_from_bytes(pm_shadow.sent['customReserved10'][-1]).customReserved10
    self.assertEqual((last.mpcSolved, last.mpcSolveTime), (False, 0.))
    self.assertGreater(last.stepTime, 0.)

  def test_shadow_runs_after_the_plan_is_sent(self):
    # the shadow's second MPC solve must not delay longitudinalPlan (the device's solves have a ~23 ms tail): update()
    # only records the calls, publish() sends longitudinalPlan and longitudinalPlanSP and only then solves. Live has to
    # solve inside update(), before the plan it shapes
    log: list = []
    for mode, in_update, in_publish in ((1, [], ['longitudinalPlan', 'longitudinalPlanSP', 'closeSolve', 'customReserved10']),
                                        (2, ['closeSolve'], ['longitudinalPlan', 'longitudinalPlanSP', 'customReserved10'])):
      with self.subTest(mode=mode):
        pl = planner(mode)
        self.assertEqual(pl.close_follow.defer, mode == 1)
        pl.close_follow._mpc_factory = mpc_class(lambda mpc, n: log.append('closeSolve'))
        pm = PM(log=log)
        for sm in queue(30):
          log.clear()
          pl.update(cast(Any, sm))
          self.assertEqual(log, in_update)
          log.clear()
          pl.publish(cast(Any, sm), pm)
          self.assertEqual(log, in_publish)
        self.assertTrue(pl.close_follow.mpc_solved)

  def test_deferred_shadow_logs_what_inline_would(self):
    # the deferred shadow computes exactly what it computed inline (a tool sets defer = False to read the values right
    # after update()): the same customReserved10 on every frame, wall-clock fields aside, and the same plans
    deferred, inline = planner(1), planner(1)
    inline.close_follow.defer = False
    pm_d, pm_i = PM(), PM()
    for sm in queue(400, brake_at=300):
      step(deferred, sm, pm_d)
      step(inline, sm, pm_i)
    timing = ('mpcSolveTime', 'stepTime')
    assert_same_frames(self, plan_dicts(pm_d, 'customReserved10', timing), plan_dicts(pm_i, 'customReserved10', timing),
                       'customReserved10, deferred vs inline')
    for s in ('longitudinalPlan', 'longitudinalPlanSP'):
      assert_same_frames(self, plan_dicts(pm_d, s), plan_dicts(pm_i, s), s)

  def test_live_ramps_in_blended_by_the_authority(self):
    # after the 1 s hold the gap's authority g rises at 0.5/s, and live takes the plan's own MPC trajectory plus g of
    # the way to close follow's (and the same for the MPC candidate's accel): a 2 s ramp, never a step to the close MPC
    pl = planner(2)
    c = pl.close_follow
    action_t = pl.CP.longitudinalActuatorDelay + DT_MDL
    gs, diffs = [], []
    for sm in queue(GAP_FULL + 10):
      step(pl, sm)
      plan, close = plan_trajectories(pl.mpc), plan_trajectories(c.mpc)
      g = c.g
      self.assertTrue(c.mpc_solved and c.weight > 0.)
      self.assertEqual(g, c.gap.authority)
      for got, p, q in zip((pl.v_desired_trajectory, pl.a_desired_trajectory, pl.j_desired_trajectory), plan, close,
                           strict=True):
        np.testing.assert_allclose(got, p + g * (q - p), rtol=0., atol=1e-12)
      a_plan = float(get_accel_from_plan(plan[0], plan[1], CONTROL_N_T_IDX, action_t=action_t))
      a_close = float(get_accel_from_plan(close[0], close[1], CONTROL_N_T_IDX, action_t=action_t))
      self.assertAlmostEqual(c.a_mpc, a_plan, places=12)
      self.assertAlmostEqual(c.a_mpc_close - c.a_mpc, g * (a_close - a_plan), places=9)
      gs.append(g)
      diffs.append(a_close - a_plan)
    ramp = [k for k, g in enumerate(gs) if 0. < g < 1.]
    self.assertEqual(gs[:HOLD], [0.] * HOLD, "the 1 s hold")
    self.assertEqual(ramp[0], HOLD)
    self.assertTrue(len(ramp) in (39, 40), f"0 to 1 at 0.5/s: 2 s, not {len(ramp)} frames")
    np.testing.assert_allclose(np.diff(gs[HOLD - 1:ramp[-1] + 1]), cf.GAP_RISE * DT_MDL, rtol=0., atol=1e-12)
    self.assertEqual(gs[-1], 1.)
    self.assertGreater(min(diffs[k] for k in ramp), 0.01, "the ramp blends two different candidates")

  def test_an_fcw_holds_the_gap_and_the_lift_out(self):
    # one frame of the car's FCW, or of close follow's own MPC crash flag: the gap and the lift drop on that frame and
    # stay out as an FCW for FCW_HOLD_S (100 frames); then the gap's 1 s hold (back at +6 s) and the lift's 3 s (back at
    # +8 s) - the lift never comes back before the gap
    F = 300

    def crash_once(mpc, n):
      if n == F + 1:      # the close MPC solves from frame 0: its (F+1)-th solve is frame F
        mpc.crash_cnt = 3
    for source in ('carFcw', 'closeMpcCrash'):
      with self.subTest(source=source):
        pl = planner(2)
        c = pl.close_follow
        frames = queue(F + 200)
        if source == 'carFcw':
          frames[F] = build_sm(V0, 22., V0, e2e=-0.1, stock_fcw=True)
        else:
          c._mpc_factory = mpc_class(crash_once)
        for sm in frames[:F]:
          step(pl, sm)
        self.assertTrue(c.gap_active and c.lifting > 0.)
        states = []
        for sm in frames[F:]:
          step(pl, sm)
          states.append((c.gap.authority, c.gap.inhibit, c.lifting, c.lift.inhibit, c.mpc_crash))
        self.assertEqual(states[0], (0., Inhibit.fcw, 0., Inhibit.fcw, source == 'closeMpcCrash'))
        self.assertTrue(all(s[1] == Inhibit.fcw and s[3] == Inhibit.fcw for s in states[:100]), "both out 5 s, as FCW")
        first_gap = next(k for k, s in enumerate(states) if s[0] > 0.)
        first_lift = next(k for k, s in enumerate(states) if s[2] > 0.)
        self.assertEqual((first_gap, first_lift), (120, 160))

  def test_live_takes_close_follows_candidates(self):
    live = planner(2)
    c = live.close_follow
    self.assertTrue(c.live)
    ref = planner(0)
    frames = queue(300)
    out_live, out_ref = drive(live, frames), drive(ref, frames)
    # closer: the MPC candidate (and so the plan, the model's being the lowest-but-lifted) is higher than mode 0's
    self.assertTrue(c.gap_active)
    self.assertGreater(out_live[-1][0], out_ref[-1][0] + 0.05)
    self.assertAlmostEqual(c.a_target_close, out_live[-1][0], places=9)
    self.assertLessEqual(c.a_model_lifted, max(c.a_model, min(c.a_mpc_close, live.a_cruise)) + 1e-12)

  def test_live_on_the_frame_a_lead_brakes_is_the_plans_own_mpc(self):
    live = planner(2)
    c = live.close_follow
    frames = queue(301, brake_at=300)
    for sm in frames[:300]:
      step(live, sm)
    self.assertTrue(c.gap_active and c.lifting > 0.)
    step(live, frames[300])
    self.assertEqual((c.gap_active, c.lifting, c.gap.inhibit), (False, 0., Inhibit.leadBraking))
    self.assertEqual(c.a_mpc_close, c.a_mpc)
    self.assertEqual(float(live.output_a_target), min(c.a_mpc, live.a_cruise, c.a_model))

  def test_a_lead_slowing_before_alead_shows_it(self):
    # the lead slows at 0.6 m/s^2 while radard's aLeadK still reads -0.1: out (the plan's own) within the window
    live = planner(2)
    c = live.close_follow
    frames = queue(300)
    d, v_lead = 22., V0
    for _ in range(12):
      v_lead -= 0.6 * DT_MDL
      d += (v_lead - V0) * DT_MDL
      frames.append(build_sm(V0, d, v_lead, a_lead=-0.1))
    for sm in frames[:300]:
      step(live, sm)
    self.assertTrue(c.gap_active and c.lifting > 0.)
    states = []
    for sm in frames[300:]:
      step(live, sm)
      states.append((c.gap_active, c.lifting, c.a_mpc_close == c.a_mpc, c.a_target_close == c.a_target))
    self.assertEqual(states[-1], (False, 0., True, True))
    first = next(k for k, s in enumerate(states) if not s[0])
    self.assertLessEqual(first, 9, "out within 0.5 s")
    self.assertTrue(all(s == (False, 0., True, True) for s in states[first:]))

  def test_live_out_hands_back_the_same_arrays(self):
    live = planner(2)
    sm = build_sm(V0, None, 0.)     # no lead: close follow out
    live.update(cast(Any, sm))
    traj = (live.v_desired_trajectory, live.a_desired_trajectory, live.j_desired_trajectory)
    c = live.close_follow
    self.assertFalse(c.mpc_solved)
    self.assertIs(c.update_mpc(sm, False, V0, 0., True, False, live.mpc, traj, np.zeros(1), 0.65, True, True), traj)

  def test_the_close_mpc_starts_from_the_plans(self):
    # past the fade the close MPC has the plan's own follow time and jerk: seeded from it, it agrees at once
    v = CF_FADE_SPEED + 0.5
    pl = planner(1)
    for _ in range(60):
      step(pl, build_sm(v, None, 0.))
    step(pl, build_sm(v, 30., v))
    c = pl.close_follow
    self.assertTrue(c.mpc_solved)
    self.assertEqual(c.t_follow, c.t_personality)
    self.assertAlmostEqual(float(c.mpc.a_solution[1]), float(pl.mpc.a_solution[1]), delta=0.05)
    self.assertAlmostEqual(c.a_mpc_close, c.a_mpc, delta=1e-9, msg="past the fade it hands on the plan's own")

  def test_a_held_lead_never_runs_it(self):
    pl = planner(1)
    for _ in range(100):
      step(pl, build_sm(V0, 22., V0, track=held_id(7)))
    c = pl.close_follow
    self.assertEqual((c.gap_active, c.mpc_solved, c.gap.inhibit), (False, False, Inhibit.noLead))

  def test_every_other_car_stock_acc_and_mode_0_never_build_it(self):
    self.assertIsNone(planner(1, CAR.HONDA_CIVIC).close_follow)
    self.assertIsNone(planner(2, CAR.HONDA_CIVIC).close_follow)
    self.assertIsNone(planner(1, stock_acc=True).close_follow)
    self.assertIsNone(planner(0).close_follow)
    self.assertIsNone(planner(7).close_follow)
    self.assertFalse(close_follow_applies(honda_cp(op_long=False), custom.CarParamsSP.new_message()))
    self.assertTrue(close_follow_applies(honda_cp(), custom.CarParamsSP.new_message()))

  def test_disengaged_it_is_off(self):
    pl = planner(1)
    for sm in queue(200):
      step(pl, sm)
    step(pl, build_sm(V0, 22., V0, engaged=False))
    c = pl.close_follow
    self.assertEqual((c.requested, c.gap_active, c.lifting, c.gap.inhibit), (False, False, 0., Inhibit.notRequested))

  def test_the_summary_line(self):
    from unittest import mock
    pl = planner(1)
    with mock.patch.object(cf.cloudlog, 'event') as event:
      for sm in queue(450, brake_at=300):
        step(pl, sm)
    calls = [c for c in event.call_args_list if c.args and c.args[0] == 'closefollow']
    self.assertEqual(len(calls), 2, "at most one per 10 s of plans")
    kw = calls[0].kwargs
    self.assertTrue(kw['error'])
    self.assertEqual((kw['mode'], kw['n'], kw['brk']), (1, 200, 0))
    self.assertGreater(kw['gap'], 0.)
    self.assertLess(kw['dg'], 0.)
    # the slowest close-MPC solve and the slowest whole step of the 10 s, in ms (the owner's view of its cost)
    self.assertTrue(0. < kw['smax'] <= kw['xmax'] < 1000.)
    self.assertEqual(calls[1].kwargs['brk'], 0)
    self.assertTrue('leadBraking' in calls[1].kwargs['why'])
    # no chance to act (no lead): silent
    pl = planner(1)
    with mock.patch.object(cf.cloudlog, 'event') as event:
      for _ in range(450):
        step(pl, build_sm(V0, None, 0.))
    self.assertFalse([c for c in event.call_args_list if c.args and c.args[0] == 'closefollow'])

  def test_a_failure_in_close_follow_never_reaches_the_plan(self):
    from unittest import mock

    def broken_mpc(dt):
      raise RuntimeError("acados said no")
    frames = queue(120)
    ref = drive(planner(0), frames)
    for mode in (1, 2):
      with self.subTest(mode=mode, where='mpc'):
        pl = planner(mode)
        pl.close_follow._mpc_factory = broken_mpc
        with mock.patch.object(cf.cloudlog, 'exception') as exc:
          out = drive(pl, frames)
        assert_same_frames(self, out, ref, "push12's plan on every frame")
        self.assertTrue(pl.close_follow.failed)
        self.assertEqual(exc.call_count, 1, "logged once")
      with self.subTest(mode=mode, where='lift'):
        pl = planner(mode)
        with mock.patch.object(pl.close_follow.lift, 'update', side_effect=ValueError("bad")), \
             mock.patch.object(cf.cloudlog, 'exception') as exc:
          out = drive(pl, frames)
        self.assertTrue(pl.close_follow.failed)
        self.assertEqual(exc.call_count, 1)
        # it failed on the first frame, still in its hold: nothing was ever blended
        assert_same_frames(self, out, ref, "push12's plan on every frame")
    # shadow: a failure in its deferred step (after the plan is sent) is caught and logged the same way
    pl = planner(1)
    with mock.patch.object(pl.close_follow, '_run_finish', side_effect=RuntimeError("late")), \
         mock.patch.object(cf.cloudlog, 'exception') as exc:
      out = drive(pl, frames)
    self.assertTrue(pl.close_follow.failed)
    self.assertEqual(exc.call_count, 1)
    assert_same_frames(self, out, ref, "push12's plan on every frame")

  def test_the_switch_hook(self):
    s = cf.CloseFollowSwitch()
    self.assertTrue(s.update(None, True))
    self.assertFalse(s.update(None, False))
    self.assertFalse(s.on, "off at every disengage")
    self.assertTrue(math.isfinite(cf.SUMMARY_S))
