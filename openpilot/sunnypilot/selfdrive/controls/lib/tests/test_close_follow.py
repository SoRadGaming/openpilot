"""FORK(CLOSE-FOLLOW): close follow in queues (close_follow.py; push13 shadow, release13 live behind the CloseFollow
switch, default on).

Pinned here: the safe limit (CF_T_FOLLOW_BP/_V at max(vEgo, vLead + 2 s of its aLeadK over 0.3), never more than 1 m of
gap under the personality's and never above it: it hands back at 17.2 / 19.0 / 21.6 km/h, sooner behind a lead speeding
up; the plan's stopped gap stays 6 m; the jerk factor max(1.0, the personality's); the hand-back resets the gap's
authority - the release13 review, with the accelerating queue closed loop in TestAcceleratingQueue), the gap's trips (each
drops it on the same frame - release13 adds a hard-braking lead and its 3 s hold, a downhill steeper than 3 % on the
filtered pitch, and a time to collision under 3 s), the lift (never past the MPC candidate or cruise, zero from the
hand-back, nothing on the frame a lead brakes, a hard trip or a pedal, then a 3 s lockout and a slow rise), the switch
(read every 3 s and at every engage; on -> a ramp from nothing, off -> out on that frame; unreadable -> shadow), and
the planner: with the switch off (shadow) it publishes exactly what the planner without close follow does - every
frame, bit for bit - while its own log says what it would have done, and does all of its work after plannerd's sends;
live takes close follow's candidates, blended by the gap's authority while it ramps in, and is the plan's own MPC on the
frame a lead brakes; an FCW (the car's, or close follow's own MPC crash flag) holds the gap and the lift out 5 s, the
lift never back before the gap; the HUD mark (tightening) only while close follow raises the plan; every other car and
stock ACC mode never build it.
Round 2 (the release13 verification; each of these fails on 5a778e077): the REAL gap - leadOne's radar gap 0.9 m under
the personality's own desired gap (about 1 m under the gap push12 keeps) and close follow raises nothing, fading out
over the last 0.25 m; a queue pulling away
(aLeadK over 0.3 for 0.5 s) fades the gap and the lift out; the closer gap fades in over 3.6-7.2 km/h (out at a
standstill, creeping into a stop and through a launch); the 1 m cap held at the speed the queue is heading for; the
close follow time shortening at most 0.1 s per s; a radar track swap is a new car for the gap; the hand-back resets the
lift (and the lift reads the look-ahead); no mark at a standstill; the settings' texts; and, closed loop, a gentle
pull-away from a crawl and four stop-and-go waves each costing at most 1 m of real gap.
Round 3 (the verification of round 2): the limits 0.15-0.2 m inside the 1 m - the target 0.85 m under the personality's
gap, the real gap's limit 0.8 m (fading from 0.55) - and the log's rraw (the unfiltered real deficit while the gap is in
and 1 s after; rmax, the filtered one, cannot pass the limit by construction); tests for what round 2's mutations showed
no test pinned: the deficit's vLead term, its filter and its restart for each car, the pull-away counted per car, a held
leadTwo, the lift reading close follow's own (rate-limited) follow time, the mark's standstill rule behind speed_in, rmax
and rraw in the summary line; the settings' texts as the sims bear them out (about 1 m at most, and the one case of
about 2 m: a hard brake just after a stop and a launch).

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
                                                                      read_mode, read_switch, schedule_t_follow,
                                                                      speed_weight)
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


def inputs(v=4., lead_one=None, lead_two=NO_LEAD, md=None, **kw) -> Inputs:
  base: dict[str, Any] = {'engaged': True, 'is_e2e': True, 'v_ego': v, 'personality': AGGRESSIVE,
          'lead_one': lead(v=v, v_ego=v) if lead_one is None else lead_one, 'lead_two': lead_two, 'held': False,
          'gas': False, 'brake': False, 'standstill': False, 'car_fcw': False, 'force_decel': False, 'steer_lat_accel': 0.,
          'allow_throttle': True, 'model': model(v) if md is None else md}
  base.update(kw)
  return Inputs(**base)


class TestSchedule(OpenpilotTestCase):
  def test_constants(self):
    # the safe limit (release13; cfenv's skeptic, skeptic_schedule.json): 0, 13.5, 15, 18, 22 km/h
    self.assertEqual((cf.CF_T_FOLLOW_BP, cf.CF_T_FOLLOW_V), ([0., 3.75, 4.167, 5.0, 6.111], [0.50, 0.88, 1.00, 1.34, 1.80]))
    self.assertEqual((CF_FADE_SPEED, cf.CF_FADE_KPH, cf.CF_JERK_FACTOR_MIN, cf.CF_MPC_MARGIN), (6.111, 22., 1.0, 1.5))
    # the release13 review: read ahead of a lead speeding up, at most 1 m under the personality's gap (round 3: 0.85 m,
    # 0.15 m inside the 1 m - chasing a slowly accelerating queue the real gap ran up to 0.15 m past the target's)
    self.assertEqual((cf.LEAD_AHEAD_S, cf.LEAD_AHEAD_DEADBAND, cf.GAP_DEFICIT_MAX), (2.0, 0.3, 0.85))
    # round 2 (the release13 verification): the real gap, the pull-away, the close follow time's fall limit (round 3: the
    # real gap's limit 0.8 m, 0.9 left steady following 1.03-1.06 m short of push12; the log's raw deficit over 1 s)
    self.assertEqual((cf.REAL_GAP_MAX, cf.REAL_GAP_FADE, cf.REAL_GAP_TAU_S, cf.REAL_RAW_TAIL_S), (0.8, 0.25, 0.5, 1.0))
    self.assertEqual((cf.PULL_AWAY_ACCEL, cf.PULL_AWAY_T, cf.PULL_AWAY_FALL, cf.T_FOLLOW_FALL), (0.3, 0.5, 1.0, 0.1))
    self.assertEqual(cf.CF_SPEED_IN_BP, [1.0, 2.0])
    self.assertEqual((cf.GAP_BP, cf.LIFT_SPEED_BP, cf.LIFT_T_FADE, cf.GAP_LEAD_BRAKE, cf.GAP_HOLD_S, cf.GAP_RISE),
                     ([3., 10.], [2.5, 3.5], 0.1, -0.5, 1.0, 0.5))
    self.assertEqual((cf.LEAD_SLOWING_ACCEL, cf.LEAD_SLOWING_T, cf.LEAD_PROB, cf.LEAD_JUMP, cf.LEAD_MIN_SPEED),
                     (-0.5, 0.5, 0.8, 3.0, 2.5))
    self.assertEqual((cf.HARD_BRAKE_SLOPE, cf.HARD_BRAKE_ALEAD, cf.HARD_BRAKE_HOLD_S, cf.DOWNHILL_PITCH, cf.TTC_MIN_S,
                      cf.TTC_CLOSING), (-2.0, -1.5, 3.0, -0.03, 3.0, 0.5))
    self.assertEqual(CAP, {AGGRESSIVE: 0.5, STANDARD: 0.4, RELAXED: 0.3})
    # the holds and rates the safety tests below count frames by (60 / 20 / 100 frames at 20 Hz)
    self.assertEqual((cf.LIFT_HOLD_S, cf.AUTHORITY_RISE, cf.AUTHORITY_FALL, cf.BOOST_RISE, cf.FCW_HOLD_S),
                     (3.0, 0.5, 4.0, 0.25, 5.0))
    self.assertEqual((round(cf.LIFT_HOLD_S / DT_MDL), HOLD, round(cf.FCW_HOLD_S / DT_MDL)), (60, 20, 100))
    self.assertEqual((cf.SWITCH_PARAM, cf.SWITCH_READ_S), ("CloseFollow", 3.0))
    self.assertEqual(read_mode.__module__, cf.__name__)

  def test_the_safe_limit(self):
    # the schedule itself (the skeptic's set)
    self.assertAlmostEqual(schedule_t_follow(0.), 0.5)
    self.assertAlmostEqual(schedule_t_follow(3.75), 0.88)
    self.assertAlmostEqual(schedule_t_follow(4.167), 1.0)
    self.assertAlmostEqual(schedule_t_follow(1.875), 0.69, msg="halfway to 13.5 km/h: halfway to 0.88 s")
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      t_p = get_T_FOLLOW(p)
      self.assertAlmostEqual(close_t_follow(0., t_p), 0.5)
      vs = np.linspace(0., 15., 1501)
      ts = [close_t_follow(v, t_p) for v in vs]
      for v, t in zip(vs[1:], ts[1:], strict=True):
        self.assertAlmostEqual(t, min(t_p, max(schedule_t_follow(v), t_p - cf.GAP_DEFICIT_MAX / v)), places=12)
      self.assertTrue(all(b >= a - 1e-12 for a, b in zip(ts, ts[1:], strict=False)), "the gap only grows with speed")
      self.assertTrue(all(t <= t_p + 1e-12 for t in ts), "never longer than the personality's")
      self.assertAlmostEqual(close_t_follow(CF_FADE_SPEED, t_p), t_p)
      self.assertAlmostEqual(close_t_follow(30., t_p), t_p)
    # aggressive (the owner's): the schedule up to ~5 km/h, then 0.85 m under its own gap until the schedule hands back
    self.assertAlmostEqual(close_t_follow(1.3, 1.25), schedule_t_follow(1.3))
    self.assertAlmostEqual(close_t_follow(1.5, 1.25), 1.25 - 0.85 / 1.5)
    self.assertAlmostEqual(close_t_follow(3.75, 1.25), 1.25 - 0.85 / 3.75)

  def test_never_more_than_0_85_m_under_the_personalitys_gap(self):
    # the release13 review: what a crawl closes up must be little enough to reopen when the queue pulls away - the
    # schedule alone was up to 1.4 / 2.1 / 3.2 m under aggressive / standard / relaxed at 10-15 km/h. Its 1 m is 0.85 m
    # since round 3 (the target 0.15 m inside the 1 m, the real gap's limit 0.2 m inside it)
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      t_p = get_T_FOLLOW(p)
      deficits = [(t_p - close_t_follow(v, t_p)) * v for v in np.linspace(0.05, 8., 800)]
      self.assertLessEqual(max(deficits), 0.85 + 1e-9)
      self.assertGreater(max(deficits), 0.84, "and it still follows closer")

  def test_it_hands_back_by_itself(self):
    # the personality's follow time from 17.2 / 19.0 / 21.6 km/h on aggressive / standard / relaxed: no separate fade
    for p, kph in ((AGGRESSIVE, 17.2), (STANDARD, 19.0), (RELAXED, 21.6)):
      t_p = get_T_FOLLOW(p)
      self.assertLess(close_t_follow((kph - 0.15) / 3.6, t_p), t_p)
      self.assertEqual(close_t_follow((kph + 0.1) / 3.6, t_p), t_p)
      self.assertLess(kph / 3.6, CF_FADE_SPEED, "every personality hands back below the one eligibility bound")

  def test_read_at_the_faster_of_the_car_and_its_lead(self):
    # no closer gap while the lead pulls away faster than the car: the schedule is read at max(vEgo, vLead)
    t_p = get_T_FOLLOW(P.aggressive)
    # (round 2: and never more than 1 m - 0.85 m since round 3 - under the personality's gap at that speed: 1.02 s at
    # 13.5 km/h on aggressive)
    self.assertAlmostEqual(close_t_follow(2., t_p, v_lead=3.75), max(0.88, t_p - 0.85 / 3.75))
    self.assertAlmostEqual(close_t_follow(3.75, t_p, v_lead=1.), close_t_follow(3.75, t_p),
                           msg="a slower lead does not shorten it")
    self.assertEqual(close_t_follow(2., t_p, v_lead=6.), t_p)
    self.assertAlmostEqual(close_t_follow(2., t_p, v_lead=float('nan')), close_t_follow(2., t_p))

  def test_read_where_the_queue_is_heading(self):
    # the release13 review: a lead speeding up is read 2 s ahead on its acceleration over 0.3 m/s^2, so the gap is
    # handed back while the queue is still speeding up - not once the radar's lagging vLead gets there
    self.assertAlmostEqual(cf.schedule_speed(3., 3., 1.3), 5.0)
    self.assertAlmostEqual(cf.schedule_speed(3., 3., 0.25), 3.0, msg="a creeping queue's swell is not a pull-away")
    self.assertAlmostEqual(cf.schedule_speed(3., 3., -2.), 3.0)
    self.assertAlmostEqual(cf.schedule_speed(3., 3., float('nan')), 3.0)
    self.assertAlmostEqual(cf.schedule_speed(4., None, 2.), 4.0, msg="no lead: the car's own speed")
    # a queue at 10.8 km/h pulling away at 1.2 m/s^2 is read at 17.3 km/h: aggressive (17.2) has handed back already,
    # standard takes its 17.3 km/h follow time; relaxed stays 0.85 m under its own gap at that speed (the cap, round 2:
    # at the speed the queue is heading for, not at the crawl's)
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      t_p = get_T_FOLLOW(p)
      self.assertAlmostEqual(close_t_follow(3., t_p, 3., 1.2), min(t_p, max(schedule_t_follow(4.8), t_p - 0.85 / 4.8)))
      self.assertLessEqual(close_t_follow(3., t_p, 3., 0.), close_t_follow(3., t_p, 3., 1.2))
    self.assertEqual(close_t_follow(3., get_T_FOLLOW(P.aggressive), 3., 1.2), get_T_FOLLOW(P.aggressive))
    self.assertLess(close_t_follow(3., get_T_FOLLOW(P.standard), 3., 0.), close_t_follow(3., get_T_FOLLOW(P.standard), 3., 1.2))

  def test_the_1_m_holds_at_the_speed_the_queue_is_heading_for(self):
    # round 2 (stop-and-go in the verification): capped at the crawl's own speed, a launch behind a lead pulling away
    # planned its whole horizon at the crawl's short follow time - up to 2.4 m under the personality's gap at the speed
    # the queue was heading for - and set off ahead of push12. The cap holds at schedule_speed() now
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      t_p = get_T_FOLLOW(p)
      for v_ego in (0., 0.3, 0.5, 1., 2., 3., 4.):
        for v_lead in (0., 0.5, 1., 2., 3., 4., 5.):
          for a_lead in (-1., 0., 0.3, 0.5, 1., 1.5):
            v_s = cf.schedule_speed(v_ego, v_lead, a_lead)
            self.assertLessEqual((t_p - close_t_follow(v_ego, t_p, v_lead, a_lead)) * v_s, cf.GAP_DEFICIT_MAX + 1e-9,
                                 (p, v_ego, v_lead, a_lead))
    # the case it was: relaxed creeping at 1.8 km/h, the lead at 7.2 km/h pulling away at 0.5 m/s^2 (read at 8.6 km/h)
    self.assertAlmostEqual(close_t_follow(0.5, 1.75, 2.0, 0.5), 1.75 - 0.85 / 2.4)

  def test_real_weight(self):
    # round 2: how much close follow may still raise the plan, from the REAL deficit against the personality's own
    # desired gap - all of it to 0.55 m short, nothing from 0.8 m short (round 3: 0.65 / 0.9 left steady following
    # 1.03-1.06 m short of push12, which settles 0.1-0.2 m past its desired gap), linear between (no on/off at the line)
    for deficit, w in ((-5., 1.), (0., 1.), (0.55, 1.), (0.675, 0.5), (0.8, 0.), (0.9, 0.), (1.5, 0.), (float('nan'), 0.)):
      self.assertAlmostEqual(cf.real_weight(deficit), w, msg=str(deficit))

  def test_jerk_factor(self):
    # max(1.0, the personality's) - never aggressive's 0.5
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      self.assertEqual(close_jerk_factor(get_jerk_factor(p)), max(1.0, get_jerk_factor(p)))
    self.assertEqual(close_jerk_factor(0.5), 1.0)

  def test_speed_weights(self):
    t_p = get_T_FOLLOW(P.aggressive)
    self.assertEqual((speed_weight(0.5, t_p), speed_weight(1.2, t_p), speed_weight(t_p, t_p)), (1., 1., 0.))
    # the lift: in over 9 -> 12.6 km/h, out over the last 0.1 s of follow time, zero from the hand-back
    self.assertEqual(lift_speed_weight(2.4, 0.7, t_p), 0.)
    self.assertAlmostEqual(lift_speed_weight(3.0, 0.8, t_p), 0.5)
    self.assertAlmostEqual(lift_speed_weight(4.0, 0.95, t_p), 1.0)
    self.assertAlmostEqual(lift_speed_weight(4.0, 1.20, t_p), 0.5)
    self.assertEqual(lift_speed_weight(4.0, t_p, t_p), 0.)
    for v in np.linspace(0., 10., 201):
      t_c = close_t_follow(v, t_p)
      if t_c >= t_p:
        self.assertEqual(lift_speed_weight(v, t_c, t_p), 0.)

  def test_the_stopped_gap_stays_6_m(self):
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      self.assertEqual(desired_gap(0., 0., close_t_follow(0., get_T_FOLLOW(p))), 6.0)
    self.assertAlmostEqual(desired_gap(3.75, 3.75, close_t_follow(3.75, 1.25)), 1.25 * 3.75 + 6.0 - cf.GAP_DEFICIT_MAX)

  def test_read_switch(self):
    class _P:
      def __init__(self, v):
        self.v = v

      def get(self, key, return_default=False):
        if isinstance(self.v, Exception):
          raise self.v
        return self.v
    self.assertEqual([read_switch(_P(v)) for v in (True, False, 1, 0, "1", "0", b"1", None, "x", RuntimeError("no key"))],
                     [True, False, True, False, True, False, True, False, False, False])
    self.assertEqual([read_mode(_P(v)) for v in (True, False, RuntimeError("no key"))],
                     [cf.MODE_LIVE, cf.MODE_SHADOW, cf.MODE_SHADOW], "unreadable: shadow, never live")
    # the built params library: a key nobody wrote is its default, ON - the owner's device after the update
    params = Params()
    params.remove("CloseFollow")
    self.assertIs(params.get("CloseFollow"), None)
    self.assertTrue(read_switch(params), "default on")
    self.assertEqual(read_mode(params), cf.MODE_LIVE)
    params.put_bool("CloseFollow", False, block=True)
    self.assertEqual(read_mode(params), cf.MODE_SHADOW)
    params.remove("CloseFollow")

  def test_the_settings_say_what_it_does(self):
    # round 2 (the verification): 'about 1 s behind at 15 km/h' is aggressive's only (standard 1.2 s, relaxed 1.5 s).
    # What holds on every personality leads. Round 3 (its verification): 'never more than 1 m' was not true of the gap the
    # radar measures (1.03-1.15 m in the sims, and up to about 2 m after a stop, a launch and a hard brake - push12's own
    # launch timing), so the texts say 'about 1 m at most' and name that one case; and the mark does not go 'the moment'
    # the traffic ahead pulls away (a soft trip, faded over up to a couple of seconds)
    import json
    import os
    from openpilot.sunnypilot import sunnylink
    with open(os.path.join(os.path.dirname(sunnylink.__file__), 'settings_ui.json')) as f:
      ui = json.load(f)
    found = {}

    def walk(o):
      if isinstance(o, dict):
        if o.get('key') in ('CloseFollow', 'HudCloseFollow'):
          found[o['key']] = o
        for v in o.values():
          walk(v)
      elif isinstance(o, list):
        for v in o:
          walk(v)
    walk(ui)
    desc, details, mark = (found['CloseFollow']['description'], found['CloseFollow']['details'],
                           found['HudCloseFollow']['description'])
    self.assertTrue(desc.startswith("About 1 m closer than your personality's gap at most"), desc[:80])
    assert "up to about 2 m closer" in desc, desc
    assert "about 1 m closer than your personality's gap at most" in mark, mark
    for text in (desc, details, mark):
      self.assertNotIn('ever more than', text)
      self.assertNotIn('about 1 s behind', text)
      self.assertNotIn('braking or pulling away', text)
    assert 'never shown at a stop' in mark, mark
    for text in (details, mark):
      assert 'within a few seconds once the traffic ahead pulls away' in text, text
    for kph, p in ((15, AGGRESSIVE), (15, STANDARD), (15, RELAXED)):
      t_p = get_T_FOLLOW(p)
      self.assertLessEqual((t_p - close_t_follow(kph / 3.6, t_p)) * kph / 3.6, 1. + 1e-9)


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

  def trip_cases(self, v=4.):
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
      # release13: a hard-braking lead (CloseFollow sets lead_hard, and keeps it 3 s), a downhill, a short TTC
      ({'lead_hard': True}, Inhibit.leadHardBrake),
      ({'downhill': True}, Inhibit.downhill),
      ({'lead_one': lead(d=20., v=v - 7., v_ego=v)}, Inhibit.closing),   # 7 m/s closing at 20 m: 2.9 s
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
      g.update(inputs(v=6., lead_one=lead(d=30. - 2. * DT_MDL * i, v=4., v_ego=6.)), True, False)
    self.assertEqual((g.authority, g.inhibit), (1., Inhibit.none))

  def test_a_mild_lead_brake_does_not_trip(self):
    _, out = self.run_guard(GAP_FULL, lead_one=lead(v=4., v_ego=4., a=-0.45))
    self.assertEqual(out, 1.)

  def test_the_hand_back_resets_it(self):
    # the release13 review: past the hand-back (speed weight 0) the authority stood at 1, so the moment the schedule
    # speed fell again - a lead starting to brake - the closer gap was back at full authority on that frame
    g, out = self.run_guard(GAP_FULL)
    self.assertEqual(out, 1.)
    self.assertEqual((g.update(inputs(), True, False, 0.), g.inhibit, g.authority), (0., Inhibit.speed, 0.))
    outs = [g.update(inputs(), True, False, 1.) for _ in range(HOLD + 3)]
    self.assertEqual(outs[:HOLD], [0.] * HOLD, "back only by the hold ...")
    self.assertAlmostEqual(outs[HOLD], cf.GAP_RISE * DT_MDL, msg="... and the ramp")
    # a trip on the same frame is the trip's
    g, _ = self.run_guard(GAP_FULL)
    self.assertEqual((g.update(inputs(gas=True), True, False, 0.), g.inhibit), (0., Inhibit.driver))

  def test_a_radar_track_swap_is_a_new_car(self):
    # round 2 (the verification's cut-in L1): a slower car cutting in within LEAD_JUMP (3 m) of where the old lead was
    # is a new radar track at nearly the same range - the gap is out on that frame and waits out its hold, as the lift
    g, out = self.run_guard(GAP_FULL, lead_one=lead(d=20., v=4., v_ego=4., track=7))
    self.assertEqual(out, 1.)
    _, out = self.run_guard(1, g=g, lead_one=lead(d=18.5, v=3., v_ego=4., track=12))
    self.assertEqual((out, g.inhibit), (0., Inhibit.leadChanged))
    outs = [g.update(inputs(lead_one=lead(d=18.5, v=4., v_ego=4., track=12)), True, False) for _ in range(HOLD + 2)]
    self.assertEqual(outs[:HOLD], [0.] * HOLD, "the new car: the hold again")
    self.assertGreater(outs[-1], 0.)
    # the same track: no trip; a vision lead under 2 m/s carries no track, so it never counts as a swap
    g, _ = self.run_guard(GAP_FULL, v=1.5, lead_one=lead(d=7., v=1.5, v_ego=1.5, radar=False, track=-1))
    _, out = self.run_guard(1, g=g, v=1.5, lead_one=lead(d=7., v=1.5, v_ego=1.5, radar=True, track=5))
    self.assertEqual(out, 1.)

  def test_a_queue_pulling_away_fades_it_out(self):
    # round 2 (the verification's gentle pull-aways): while the queue pulls away (CloseFollow: leadOne's aLeadK over 0.3
    # for 0.5 s) nothing tightens - the authority never rises and falls at PULL_AWAY_FALL - then the hold and the ramp
    g, out = self.run_guard(GAP_FULL)
    self.assertEqual(out, 1.)
    outs = [g.update(inputs(), True, False, 1., True) for _ in range(30)]
    self.assertEqual(g.inhibit, Inhibit.pullAway)
    np.testing.assert_allclose(outs[:10], [1. - cf.PULL_AWAY_FALL * DT_MDL * (k + 1) for k in range(10)], atol=1e-12)
    self.assertEqual(outs[-1], 0.)
    self.assertTrue(all(b <= a for a, b in zip(outs, outs[1:], strict=False)), "never rises")
    outs = [g.update(inputs(), True, False, 1., False) for _ in range(HOLD + 2)]
    self.assertEqual(outs[:HOLD], [0.] * HOLD)
    self.assertAlmostEqual(outs[HOLD], cf.GAP_RISE * DT_MDL)
    # a hard trip on the same frame is the trip's
    g, _ = self.run_guard(GAP_FULL)
    self.assertEqual((g.update(inputs(gas=True), True, False, 1., True), g.inhibit), (0., Inhibit.driver))

  def test_time_to_collision(self):
    # out under 3 s while closing faster than 0.5 m/s; a slow close at any distance, or 3.1 s, is not
    self.assertEqual(cf.time_to_collision(lead(d=10., v=2., v_ego=6.)), 2.5)
    self.assertTrue(cf.ttc_short(lead(d=10., v=2., v_ego=6.)))
    self.assertFalse(cf.ttc_short(lead(d=12.4, v=2., v_ego=6.)))
    self.assertFalse(cf.ttc_short(lead(d=1.2, v=3.6, v_ego=4.)), "closing at 0.4 m/s: no TTC")
    self.assertFalse(cf.ttc_short(NO_LEAD))
    self.assertEqual(cf.time_to_collision(lead(d=10., v=6., v_ego=4.)), math.inf)
    _, out = self.run_guard(GAP_FULL, lead_one=lead(d=13., v=2., v_ego=4.))   # 6.5 s: in
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

  def test_a_hard_braking_lead(self):
    # release13: leadOne's own speed falling faster than 2.0 m/s^2 over the 0.5 s window, or any real lead's aLeadK
    # under -1.5, is a hard-braking lead (CloseFollow then holds close follow out 3 s)
    w = cf.LeadSpeedWindow()
    for _ in range(20):
      w.update(lead(v=8., a=-0.1))
    for k in range(12):
      w.update(lead(v=8. - 2.5 * DT_MDL * (k + 1), a=-0.3))
    self.assertAlmostEqual(w.slope, -2.5, places=6)
    inp = inputs(lead_one=lead(v=6., a=-0.3))
    self.assertTrue(cf.lead_hard_now(inp, w.slope))
    self.assertFalse(cf.lead_hard_now(inp, -1.9))
    self.assertTrue(cf.lead_hard_now(inputs(lead_one=lead(v=6., a=-1.6)), 0.))
    self.assertTrue(cf.lead_hard_now(inputs(lead_two=lead(d=40., v=6., a=-1.6)), 0.))
    self.assertTrue(cf.lead_hard_now(inputs(lead_one=lead(v=6., a=float('nan'))), 0.))
    self.assertFalse(cf.lead_hard_now(inputs(lead_one=lead(v=6., a=-1.4)), 0.))

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


FAR = 40.   # m: far past the close gap at VL
VL = 4.     # m/s (14.4 km/h): inside the lift's band and under every personality's hand-back


def lift_run(frames, lift=None, a_mpc=1.0, a_cruise=2.0, fcw=False, t_follow=1.0, requested=True, pull_away=False,
             real_w=1., **kw):
  lift = lift or LeadFollowLift()
  out = None
  for _ in range(frames):
    inp = inputs(**dict({'v': VL, 'lead_one': lead(d=FAR, v=VL, v_ego=VL)}, **kw))
    if pull_away or real_w != 1.:
      out = lift.update(inp, requested, a_mpc, a_cruise, fcw, t_follow, pull_away=pull_away, real_w=real_w)
    else:
      out = lift.update(inp, requested, a_mpc, a_cruise, fcw, t_follow)
  return lift, out


class TestLift(OpenpilotTestCase):
  def test_lifts_a_long_gap_up_to_the_cap(self):
    lift, out = lift_run(LIFT_FULL)
    self.assertEqual(lift.inhibit, Inhibit.none)
    self.assertAlmostEqual(lift.weight, 1.)
    self.assertAlmostEqual(out, CAP[AGGRESSIVE])

  def test_never_past_the_mpc_or_cruise(self):
    _, out = lift_run(LIFT_FULL, a_mpc=0.1)
    self.assertAlmostEqual(out, 0.1)
    _, out = lift_run(LIFT_FULL, a_cruise=0.05)
    self.assertAlmostEqual(out, 0.05)
    _, out = lift_run(LIFT_FULL, md=model(VL, a=-0.05), a_mpc=-0.3)
    self.assertEqual(out, -0.05, "an MPC braking harder than the model is left to the min()")

  def test_rise_is_rate_limited(self):
    lift = LeadFollowLift()
    outs = [lift.update(inputs(v=VL, lead_one=lead(d=FAR, v=VL, v_ego=VL), md=model(VL)), True, 1.0, 2.0, False, 1.0)
            for _ in range(LIFT_FULL)]
    self.assertLessEqual(max(np.diff(outs)), cf.BOOST_RISE * DT_MDL + 1e-9)

  def test_inside_the_deadband_nothing(self):
    d = desired_gap(VL, VL, 1.0) + cf.GAP_BP[0] - 0.5
    lift, out = lift_run(LIFT_FULL, lead_one=lead(d=d, v=VL, v_ego=VL))
    self.assertEqual((lift.inhibit, out), (Inhibit.none, 0.))

  def test_speed_window(self):
    # in over 9 -> 12.6 km/h; zero from the hand-back (aggressive 17.2 km/h) and above CF_FADE_SPEED
    for v, want_zero in ((2.4, True), (3.0, False), (VL, False), (4.9, True), (CF_FADE_SPEED, True),
                         (CF_FADE_SPEED + 1., True)):
      with self.subTest(v=v):
        _, out = lift_run(LIFT_FULL, v=v, lead_one=lead(d=FAR + 10., v=v, v_ego=v), md=model(v))
        self.assertEqual(out == 0., want_zero)

  def test_zero_from_the_hand_back(self):
    # above the hand-back the e2e candidate is the model's own, exactly - push12's - however long the gap; and a lead
    # pulling away faster than the hand-back reads the schedule at its speed
    t_p = get_T_FOLLOW(P.aggressive)
    for v, v_lead in ((4.9, 4.9), (5.5, 5.5), (VL, 6.0)):
      with self.subTest(v=v, v_lead=v_lead):
        self.assertEqual(close_t_follow(v, t_p, v_lead), t_p)
        lift, out = lift_run(LIFT_FULL, v=v, lead_one=lead(d=FAR, v=v_lead, v_ego=v), md=model(v, a=0.1))
        self.assertEqual((out, lift.boost, lift.weight), (0.1, 0., 0.))

  def test_the_hand_back_resets_it_and_it_reads_where_the_queue_is_heading(self):
    # round 2 (the verification): handed back - here by a lead at our 14.4 km/h pulling away at 1.2 m/s^2, read 2 s
    # ahead at 20.9 km/h, past aggressive's 17.2 - the lift is out on that frame with its authority at 0, and when the
    # lead stops pulling away it comes back only after its 3 s hold (it used to keep its authority and be back at once,
    # before the gap's own 1 s hold). Without the look-ahead (a lead at 14.4 km/h) it would not have handed back at all
    t_p = get_T_FOLLOW(P.aggressive)
    self.assertLess(close_t_follow(VL, t_p, VL), t_p)
    self.assertEqual(close_t_follow(VL, t_p, VL, 1.2), t_p)
    lift, out = lift_run(LIFT_FULL)
    self.assertAlmostEqual(out, CAP[AGGRESSIVE])
    _, out = lift_run(1, lift=lift, lead_one=lead(d=FAR, v=VL, v_ego=VL, a=1.2))
    self.assertEqual((lift.inhibit, out, lift.authority, lift.boost), (Inhibit.speed, 0., 0., 0.))
    outs = [lift_run(1, lift=lift)[1] for _ in range(round(cf.LIFT_HOLD_S / DT_MDL) + 5)]
    self.assertEqual(outs[:round(cf.LIFT_HOLD_S / DT_MDL)], [0.] * round(cf.LIFT_HOLD_S / DT_MDL))
    self.assertGreater(outs[-1], 0.)
    # CloseFollow hands it its own follow time (t_close): the personality's is the hand-back too
    lift, _ = lift_run(LIFT_FULL)
    out = lift.update(inputs(v=VL, lead_one=lead(d=FAR, v=VL, v_ego=VL)), True, 1.0, 2.0, False, 1.0, t_close=t_p)
    self.assertEqual((lift.inhibit, out, lift.authority), (Inhibit.speed, 0., 0.))

  def test_a_queue_pulling_away_or_a_short_real_gap(self):
    # round 2: the queue pulling away is a soft trip for the lift (it falls at 4/s, then its 3 s hold); the real gap
    # scales it (real_w)
    lift, before = lift_run(LIFT_FULL)
    _, out = lift_run(1, lift=lift, pull_away=True)
    self.assertEqual(lift.inhibit, Inhibit.pullAway)
    self.assertLess(out, before)
    _, out = lift_run(round(0.25 / DT_MDL), lift=lift, pull_away=True)
    self.assertEqual(out, 0.)
    for w in (1., 0.5, 0.):
      with self.subTest(real_w=w):
        lift, out = lift_run(LIFT_FULL, real_w=w)
        self.assertAlmostEqual(out, CAP[AGGRESSIVE] * w)

  def test_never_toward_a_close_lead_it_is_closing_on(self):
    # time to collision under 3 s (closing faster than 0.5 m/s): out, a hard trip
    lift, out = lift_run(LIFT_FULL, lead_one=lead(d=8., v=1.0, v_ego=VL))    # 3 m/s at 8 m: 2.7 s
    self.assertEqual((lift.inhibit, out, lift.authority), (Inhibit.closing, 0., 0.))

  def test_hard_trips_leave_nothing_on_the_same_frame(self):
    v = VL
    base = {'v': v, 'lead_one': lead(d=FAR, v=v, v_ego=v), 'md': model(v)}
    cases = [
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, a=-0.6)}, {}, Inhibit.leadBraking),
      ({'lead_two': lead(d=FAR + 20., v=v, v_ego=v, a=-0.6)}, {}, Inhibit.leadBraking),
      ({'md': model(v, lead_v=[v, v - 1.5, v - 0.5, v, v, v])}, {}, Inhibit.leadBraking),
      ({'lead_slowing': True}, {}, Inhibit.leadBraking),
      ({'lead_hard': True}, {}, Inhibit.leadHardBrake),
      ({'downhill': True}, {}, Inhibit.downhill),
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
      # a hard trip found by the other check still wins over a soft lead trip (a lead under 2.5 m/s + gas)
      ({'lead_one': lead(d=FAR, v=2., v_ego=v), 'gas': True}, {}, Inhibit.driver),
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
    v = VL
    base = {'v': v, 'lead_one': lead(d=FAR, v=v, v_ego=v), 'md': model(v)}
    hold = 60
    cases = [
      # (the trip frame, its extra arguments, the clean frames after it, the reason)
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, a=-0.6)}, {}, {}, Inhibit.leadBraking),
      ({'lead_two': lead(d=FAR + 20., v=v, v_ego=v, a=-0.6)}, {}, {}, Inhibit.leadBraking),
      ({'lead_slowing': True}, {}, {}, Inhibit.leadBraking),
      ({'lead_hard': True}, {}, {}, Inhibit.leadHardBrake),
      ({'downhill': True}, {}, {}, Inhibit.downhill),
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
    v = VL
    base = {'v': v, 'lead_one': lead(d=FAR, v=v, v_ego=v), 'md': model(v)}
    cases = [
      ({'steer_lat_accel': 1.2}, Inhibit.lateral),
      ({'allow_throttle': False}, Inhibit.coast),
      ({'md': model(v, lane_change=True)}, Inhibit.laneChange),
      ({'lead_one': lead(d=FAR, v=v, v_ego=v, prob=0.6)}, Inhibit.leadUncertain),
      ({'lead_one': lead(d=FAR, v=2.4, v_ego=v)}, Inhibit.leadSlow),
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
    lift, _ = lift_run(LIFT_FULL, md=model(VL))
    _, out = lift_run(1, lift=lift, md=model(VL, a=-0.6))
    self.assertEqual((lift.inhibit, out), (Inhibit.modelBraking, -0.6))

  def test_idle_states(self):
    for kw, requested, reason in (({'is_e2e': False}, True, Inhibit.notE2e), ({'engaged': False}, True, Inhibit.inactive),
                                  ({}, False, Inhibit.notRequested)):
      with self.subTest(reason=reason):
        lift, _ = lift_run(LIFT_FULL)
        _, out = lift_run(1, lift=lift, requested=requested, **kw)
        self.assertEqual((lift.inhibit, out, lift.authority), (reason, 0., 0.))

  def test_non_finite_is_the_model(self):
    lift, out = lift_run(LIFT_FULL, a_mpc=float('nan'))
    self.assertEqual((lift.inhibit, out), (Inhibit.invalid, 0.))

  def test_random_frames_never_past_the_radar_plan_and_nothing_while_a_lead_brakes(self):
    rng = random.Random(1024)
    lift = LeadFollowLift()
    lifted = 0
    t_p = get_T_FOLLOW(P.aggressive)
    for k in range(20000):
      v = 3.9 + 1.5 * math.sin(k / 300.) + rng.uniform(-0.05, 0.05)    # in and out of the lift's band, slowly
      a_lead = rng.choice([0.2, -0.3, -0.49, -0.51, -1.5, float('nan')]) if rng.random() < 0.01 else 0.
      d = rng.uniform(5., 60.) if rng.random() < 0.002 else FAR
      v_lead = v + rng.uniform(-0.3, 0.3)
      inp = inputs(v=v, lead_one=lead(d=d, v=v_lead, v_ego=v, a=a_lead),
                   md=model(v, a=rng.uniform(-0.6, 0.6) if rng.random() < 0.3 else rng.uniform(-0.1, 0.2)),
                   gas=rng.random() < 0.002, lead_hard=rng.random() < 0.002, downhill=rng.random() < 0.002)
      a_mpc, a_cruise = rng.uniform(-1., 1.5), rng.uniform(-0.5, 2.)
      out = lift.update(inp, True, a_mpc, a_cruise, rng.random() < 0.002, 1.0)
      a_model = inp.model.a
      self.assertGreaterEqual(out, a_model)
      self.assertLessEqual(out, max(a_model, min(a_mpc, a_cruise)) + 1e-12)
      if not (a_lead >= cf.GAP_LEAD_BRAKE) or inp.gas or inp.lead_hard or inp.downhill:
        self.assertEqual(out, a_model)
      if close_t_follow(v, t_p, v_lead) >= t_p:
        self.assertEqual(out, a_model, "nothing from the hand-back")
      lifted += out > a_model
    self.assertGreater(lifted, 400, "the property ran on frames that did lift")


# --- through the real planner ---

V0 = 3.5    # m/s (12.6 km/h): a queue, inside the safe limit (close 0.86 s, aggressive 1.25 s)
D0 = 16.    # m: the lead's distance in queue() - the close gap is ~9.0 m, the aggressive one ~10.4 m


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
             personality=P.aggressive, stock_fcw=False, pitch=None, held_two=False) -> _SM:
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)
  rs = messaging.new_message('radarState')
  if d_rel is not None:
    ld = rs.radarState.leadOne
    ld.present, ld.dRel, ld.vLead, ld.vLeadK, ld.vRel, ld.modelProb = True, d_rel, v_lead, v_lead, v_lead - v_ego, 0.99
    ld.aLeadK, ld.aLeadTau = a_lead, 1.5
    ld.radar, ld.radarTrackId = radar, track
  if held_two:   # leadTwo a lead radard's clutter guard holds (held_lead.py), 10 m past leadOne
    l2 = rs.radarState.leadTwo
    l2.present, l2.dRel, l2.vLead, l2.vLeadK, l2.vRel, l2.modelProb = True, (d_rel or 0.) + 10., v_lead, v_lead, v_lead - v_ego, 0.99
    l2.radar, l2.radarTrackId = True, held_id(9)
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
  if pitch is not None:
    cc.carControl.orientationNED = [0., pitch, 0.]
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


def planner(switch=True, fingerprint=CAR.HONDA_ACCORD_9G_AU, stock_acc=False, close_follow=True):
  """The real planner. switch: the user's CloseFollow (on: live). close_follow=False: the same planner with close follow
  removed - the reference, push12's plan."""
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  Params().put_bool("CloseFollow", switch, block=True)
  Params().put("LeadBrakeAssistMode", 0)  # FORK(LEAD-BRAKE): close follow alone (test_lead_brake_close_follow.py has both)
  CP_SP = custom.CarParamsSP.new_message()
  if stock_acc:
    CP_SP.flags = HondaFlagsSP.ELESYS_STOCK_ACC.value
  pl = LongitudinalPlanner(honda_cp(fingerprint), CP_SP.as_reader(), init_v=V0)
  if not close_follow:
    pl.close_follow = None
  return pl


def queue(n=400, brake_at=None, e2e=-0.1, a_brake=-1.0):
  """A queue at V0: the lead D0 ahead at our speed (the model holding its own longer gap with a small negative accel).
  From brake_at on the lead brakes at a_brake."""
  frames = []
  d, v_lead = D0, V0
  for k in range(n):
    a_lead = a_brake if brake_at is not None and k >= brake_at else 0.
    frames.append(build_sm(V0, d, v_lead, a_lead=a_lead, e2e=e2e))
    v_lead = max(0., v_lead + a_lead * DT_MDL)
    d += (v_lead - V0) * DT_MDL
  return frames


SERVICES = ('longitudinalPlan', 'longitudinalPlanSP', 'driverAssistance', 'customReserved10')


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
  """One plannerd loop: update(), publish(), driverAssistance, then publish_deferred() - where shadow close follow does
  its work, after every message controlsd acts on is out."""
  pm = pm if pm is not None else PM()
  pl.update(cast(Any, sm))
  pl.publish(cast(Any, sm), pm)
  if 'driverAssistance' in pm.sock:
    pm.send('driverAssistance', messaging.new_message('driverAssistance'))
  pl.publish_deferred(pm)


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


def last_log(pm):
  return messaging.log_from_bytes(pm.sent['customReserved10'][-1]).customReserved10


class TestPlanner(OpenpilotTestCase):
  def test_shadow_changes_nothing(self):
    frames = queue(400, brake_at=300)
    off, shadow = planner(close_follow=False), planner(False)
    self.assertIsNone(off.close_follow)
    self.assertIsNotNone(shadow.close_follow)
    self.assertFalse(shadow.close_follow.live)
    gap_on = lift_on = would_raise = 0
    out_off, out_shadow = [], []
    for sm in frames:
      # a whole plannerd loop each: in shadow close follow runs in publish_deferred(), after the plan is out - and must
      # not leave anything behind that the next plan would see
      for pl, out in ((off, out_off), (shadow, out_shadow)):
        step(pl, sm)
        out.append((float(pl.output_a_target), bool(pl.output_should_stop), pl.v_desired_trajectory.tolist(),
                    pl.a_desired_trajectory.tolist(), pl.j_desired_trajectory.tolist(), int(pl.mpc.source), bool(pl.fcw),
                    float(pl.v_desired_filter.x), float(pl.a_cruise)))
      c = shadow.close_follow
      gap_on += c.gap_active
      lift_on += c.lifting > 0.
      would_raise += c.a_target_close > c.a_target + 1e-3
      self.assertFalse(c.tightening, "shadow never says it tightens")
    assert_same_frames(self, out_off, out_shadow, "shadow: every planner output bit for bit")
    # ...while its own log says it would have acted
    self.assertGreater(gap_on, 100)
    self.assertGreater(lift_on, 20)
    self.assertGreater(would_raise, 20)

  def test_shadow_publishes_the_same_plan_and_its_own_log(self):
    pm_off, pm_shadow = PM(), PM()
    off, shadow = planner(close_follow=False), planner(False)
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
    self.assertEqual((last.switchOn, last.live, last.tightening), (False, False, False))
    self.assertAlmostEqual(last.tFollow, close_t_follow(V0, 1.25), places=5)
    self.assertAlmostEqual(last.tFollowPersonality, 1.25, places=5)
    self.assertAlmostEqual(last.jerkFactor, 1.0, places=5)
    self.assertLess(last.desiredGap, last.desiredGapPersonality)
    self.assertNotEqual(last.aMpcClose, last.aMpc)
    self.assertGreaterEqual(last.aTargetClose, last.aTarget)
    self.assertEqual((last.ttc, last.hardBrakeHold, last.pitchFiltered), (-1., 0., 0.))
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
    last = last_log(pm_shadow)
    self.assertEqual((last.mpcSolved, last.mpcSolveTime), (False, 0.))
    self.assertGreater(last.stepTime, 0.)

  def test_shadow_runs_after_every_message_is_sent(self):
    # the shadow's second MPC solve must not delay a message controlsd acts on (the device's solves have a ~23 ms tail):
    # update() only records the calls, publish() sends longitudinalPlan and longitudinalPlanSP, plannerd sends
    # driverAssistance, and only then publish_deferred() solves (the push13 review's low: driverAssistance used to wait
    # for it). Live has to solve inside update(), before the plan it shapes
    log: list = []
    for switch, in_update, in_loop in ((False, [], ['longitudinalPlan', 'longitudinalPlanSP', 'driverAssistance', 'closeSolve',
                                                     'customReserved10']),
                                       (True, ['closeSolve'], ['longitudinalPlan', 'longitudinalPlanSP', 'driverAssistance',
                                                               'customReserved10'])):
      with self.subTest(switch=switch):
        pl = planner(switch)
        self.assertEqual(pl.close_follow.defer, not switch)
        pl.close_follow._mpc_factory = mpc_class(lambda mpc, n: log.append('closeSolve'))
        pm = PM(log=log)
        for sm in queue(30):
          log.clear()
          pl.update(cast(Any, sm))
          self.assertEqual(log, in_update)
          log.clear()
          pl.publish(cast(Any, sm), pm)
          pm.send('driverAssistance', messaging.new_message('driverAssistance'))
          pl.publish_deferred(pm)
          # publish() also sends bend_ease's customReserved11 (FORK(BENDS)); this test is about close follow's place
          self.assertEqual([e for e in log if e in in_loop + ['closeSolve']], in_loop)
          if not switch:
            self.assertEqual(log[log.index('closeSolve') + 1:], ['customReserved10'])
        self.assertTrue(pl.close_follow.mpc_solved)

  def test_plannerd_sends_driver_assistance_before_close_follow(self):
    # plannerd itself: driverAssistance, then publish_deferred() (close follow's deferred work and customReserved10)
    import inspect
    from openpilot.selfdrive.controls import plannerd
    src = inspect.getsource(plannerd.main)
    self.assertLess(src.index("pm.send('driverAssistance', msg)"), src.index("longitudinal_planner.publish_deferred(pm)"))
    self.assertLess(src.index("longitudinal_planner.publish(sm, pm)"), src.index("pm.send('driverAssistance', msg)"))

  def test_deferred_shadow_logs_what_inline_would(self):
    # the deferred shadow computes exactly what it computed inline (a tool sets defer = False to read the values right
    # after update()): the same customReserved10 on every frame, wall-clock fields aside, and the same plans
    deferred, inline = planner(False), planner(False)
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
    pl = planner(True)
    c = pl.close_follow
    self.assertTrue(c.live)
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
    self.assertGreater(max(abs(diffs[k]) for k in ramp), 0.01, "the ramp blends two different candidates")

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
        pl = planner(True)
        c = pl.close_follow
        frames = queue(F + 200)
        if source == 'carFcw':
          frames[F] = build_sm(V0, D0, V0, e2e=-0.1, stock_fcw=True)
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

  def test_a_hard_braking_lead_holds_it_out_3_s(self):
    # release13: two frames of a lead at aLeadK -2.0 (under -1.5): the gap and the lift out on the first, and kept out
    # HARD_BRAKE_HOLD_S (60 frames) after the last - then the gap's 1 s hold and its ramp; a lead at -1.0 is only the
    # ordinary braking trip (back after the 1 s hold)
    F = 300
    for a_brake, held_frames in ((-2.0, 60), (-1.0, 0)):
      with self.subTest(a_brake=a_brake):
        pl = planner(True)
        c = pl.close_follow
        frames = queue(F + 120)
        for k in (F, F + 1):
          frames[k] = build_sm(V0, D0, V0, a_lead=a_brake, e2e=-0.1)
        for sm in frames[:F]:
          step(pl, sm)
        self.assertTrue(c.gap_active and c.lifting > 0.)
        states = []
        for sm in frames[F:]:
          step(pl, sm)
          states.append((c.gap.authority, c.gap.inhibit, c.lifting, c.lift.inhibit))
        self.assertEqual(states[0][0], 0.)
        self.assertEqual(states[0][2], 0.)
        hard = [k for k, s in enumerate(states) if s[1] == Inhibit.leadHardBrake]
        if held_frames:
          self.assertEqual(hard, list(range(2 + held_frames - 1)), "the 2 frames, then 3 s after the last")
          self.assertTrue(all(s[3] == Inhibit.leadHardBrake for s in states[:2 + held_frames - 1]), "the lift too")
        else:
          self.assertEqual(hard, [])
        first_gap = next(k for k, s in enumerate(states) if s[0] > 0.)
        self.assertEqual(first_gap, (1 + held_frames) + HOLD if held_frames else 2 + HOLD)
        self.assertTrue(all(s[2] == 0. for s in states[:first_gap]), "the lift never back before the gap")

  def test_a_downhill_takes_it_out(self):
    # release13: the filtered pitch (1 s) under -0.03 - steeper than 3 % downhill - and the gap and the lift are out;
    # a brake's 0.3 s nose dive to -0.06 is not a hill, and an uphill or a level road is nothing
    F = 300
    pl = planner(True)
    c = pl.close_follow
    for sm in queue(F):
      step(pl, sm)
    self.assertTrue(c.gap_active)
    dive = [build_sm(V0, D0, V0, e2e=-0.1, pitch=-0.06)] * 6 + [build_sm(V0, D0, V0, e2e=-0.1, pitch=0.)] * 20
    for sm in dive:
      step(pl, sm)
      self.assertNotEqual(c.gap.inhibit, Inhibit.downhill)
    self.assertTrue(c.gap_active)
    states = []
    for _ in range(60):
      step(pl, build_sm(V0, D0, V0, e2e=-0.1, pitch=-0.05))
      states.append((c.gap_active, c.gap.inhibit, c.lifting, c.lift.inhibit))
    first = next(k for k, s in enumerate(states) if s[1] == Inhibit.downhill)
    self.assertLess(first, 40, "out within 2 s of reaching the hill")
    self.assertTrue(all(s[0] is False and s[2] == 0. for s in states[first:]))
    self.assertTrue(all(s[3] == Inhibit.downhill for s in states[first:]))
    self.assertAlmostEqual(last_pitch(pl), -0.05, delta=0.01)
    for _ in range(GAP_FULL + 40):
      step(pl, build_sm(V0, D0, V0, e2e=-0.1, pitch=0.04))     # an uphill: in again
    self.assertTrue(c.gap_active)

  def test_live_takes_close_follows_candidates(self):
    live = planner(True)
    c = live.close_follow
    self.assertTrue(c.live)
    ref = planner(close_follow=False)
    frames = queue(300)
    out_live, out_ref = drive(live, frames), drive(ref, frames)
    # closer: the MPC candidate (and so the plan, the model's being the lowest-but-lifted) is higher than push12's
    self.assertTrue(c.gap_active)
    self.assertGreater(out_live[-1][0], out_ref[-1][0] + 0.05)
    self.assertAlmostEqual(c.a_target_close, out_live[-1][0], places=9)
    self.assertLessEqual(c.a_model_lifted, max(c.a_model, min(c.a_mpc_close, live.a_cruise)) + 1e-12)

  def test_tightening_and_its_log(self):
    # live, holding the closer gap AND raising the plan: tightening (the HUD cue), and the log says live, the switch,
    # how far it raised the plan and gapCloser; shadow never
    pm = PM()
    live = planner(True)
    c = live.close_follow
    seen = []
    for sm in queue(200):
      step(live, sm, pm)
      seen.append(c.tightening)
      a_off = min(c.a_mpc, live.a_cruise, c.a_model)
      self.assertAlmostEqual(c.raised, c.a_target_close - a_off, places=9)
      self.assertEqual(c.tightening, c.active and c.raised >= cf.TIGHT_RAISE)
    self.assertFalse(any(seen[:HOLD]), "not in the 1 s hold")
    self.assertTrue(all(seen[GAP_FULL + 5:]), "once the lift raises the model's plan (the model's own is the lowest here)")
    last = last_log(pm)
    self.assertEqual((last.mode, last.switchOn, last.live, last.tightening, last.gapCloser), (2, True, True, True, True))
    self.assertAlmostEqual(last.raised, c.raised, places=6)
    self.assertGreater(last.raised, 0.1)
    # at a standstill nothing tightens: the stopped gap is the 6 m it always was
    for _ in range(40):
      step(live, build_sm(0.05, 6., 0., e2e=0.), pm)
    self.assertFalse(live.close_follow.tightening)

  def test_the_mark_only_while_it_raises_the_plan(self):
    # the release13 review: the closer gap in while the model's own e2e plan is the lower candidate changes nothing - the
    # plan is the model's, as without close follow - so no mark (it showed 40 % of its time on the owner's routes)
    pm = PM()
    live, ref = planner(True), planner(close_follow=False)
    c = live.close_follow
    frames = queue(200, e2e=-0.6)    # the model braking a little: the lift is out (modelBraking), e2e binds
    for sm in frames:
      step(live, sm, pm)
      step(ref, sm)
    self.assertTrue(c.gap_active and c.gap_closer, "the closer gap is in")
    self.assertEqual((c.lifting, c.source_close), (0., log.LongitudinalPlan.LongitudinalPlanSource.e2e))
    self.assertEqual(float(live.output_a_target), float(ref.output_a_target), "the plan is the model's, as push12's")
    self.assertEqual((c.raised, c.tightening), (0., False))
    last = last_log(pm)
    self.assertEqual((last.live, last.gapActive, last.gapCloser, last.tightening, last.raised), (True, True, True, False, 0.))
    # chill (no e2e candidate), at about the closer gap: close follow's MPC candidate is the plan, raised over the plan's
    # own MPC (which wants to drop back to its longer gap) - the mark
    chill = planner(True)
    for _ in range(200):
      step(chill, build_sm(V0, 10.5, V0, e2e=-0.6, experimental=False))
    cc = chill.close_follow
    self.assertAlmostEqual(cc.raised, cc.a_mpc_close - min(cc.a_mpc, chill.a_cruise), places=9)
    self.assertGreater(cc.raised, cf.TIGHT_RAISE)
    self.assertTrue(cc.tightening)

  def test_live_on_the_frame_a_lead_brakes_is_the_plans_own_mpc(self):
    live = planner(True)
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
    live = planner(True)
    c = live.close_follow
    frames = queue(300)
    d, v_lead = D0, V0
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
    live = planner(True)
    sm = build_sm(V0, None, 0.)     # no lead: close follow out
    live.update(cast(Any, sm))
    traj = (live.v_desired_trajectory, live.a_desired_trajectory, live.j_desired_trajectory)
    c = live.close_follow
    self.assertFalse(c.mpc_solved)
    self.assertIs(c.update_mpc(sm, False, V0, 0., True, False, live.mpc, traj, np.zeros(1), 0.65, True, True), traj)

  def test_past_the_hand_back_it_is_the_plans_own(self):
    # past the hand-back the close MPC has the plan's own follow time: seeded from it, it agrees, and the candidate it
    # hands on is the plan's own exactly (speedWeight 0)
    v = CF_FADE_SPEED + 0.5
    pl = planner(False)
    for _ in range(60):
      step(pl, build_sm(v, None, 0.))
    step(pl, build_sm(v, 30., v))
    c = pl.close_follow
    self.assertTrue(c.mpc_solved)
    self.assertEqual((c.t_follow, c.weight), (c.t_personality, 0.))
    self.assertAlmostEqual(float(c.mpc.a_solution[1]), float(pl.mpc.a_solution[1]), delta=0.05)
    self.assertAlmostEqual(c.a_mpc_close, c.a_mpc, delta=1e-9, msg="past the hand-back it hands on the plan's own")
    live = planner(True)
    for v in (4.9, 5.5):      # aggressive's hand-back is 17.2 km/h (4.78 m/s)
      for _ in range(GAP_FULL + 5):
        step(live, build_sm(v, 30., v, e2e=-0.1))
      lc = live.close_follow
      self.assertEqual((lc.t_follow, lc.weight, lc.g, lc.lifting, lc.tightening), (lc.t_personality, 0., 0., 0., False))

  def test_a_held_lead_never_runs_it(self):
    pl = planner(False)
    for _ in range(100):
      step(pl, build_sm(V0, D0, V0, track=held_id(7)))
    c = pl.close_follow
    self.assertEqual((c.gap_active, c.mpc_solved, c.gap.inhibit), (False, False, Inhibit.noLead))

  def test_every_other_car_and_stock_acc_never_build_it(self):
    self.assertIsNone(planner(True, CAR.HONDA_CIVIC).close_follow)
    self.assertIsNone(planner(False, CAR.HONDA_CIVIC).close_follow)
    self.assertIsNone(planner(True, stock_acc=True).close_follow)
    self.assertTrue(planner(True).close_follow.live)
    self.assertFalse(planner(False).close_follow.live)
    self.assertFalse(close_follow_applies(honda_cp(op_long=False), custom.CarParamsSP.new_message()))
    self.assertTrue(close_follow_applies(honda_cp(), custom.CarParamsSP.new_message()))

  def test_a_queue_pulling_away_hands_it_back_before_it_gets_there(self):
    # the release13 review: a queue speeding up from a crawl. Read at the radar's vLead, the closer gap stayed in as the
    # car accelerated; read where the queue is heading (vLead + 2 s of aLeadK over 0.3), it is out - authority reset -
    # while the queue is still well under the hand-back speed
    live = planner(True)
    c = live.close_follow
    for sm in queue(GAP_FULL + 20):
      step(live, sm)
    self.assertEqual(c.g, 1.)
    d, v_lead, states = D0, V0, []
    for _ in range(10):
      v_lead += 1.2 * DT_MDL
      d += (v_lead - V0) * DT_MDL
      step(live, build_sm(V0, d, v_lead, a_lead=1.2, e2e=-0.1))
      states.append((c.g, c.gap.authority, c.t_follow == c.t_personality, c.gap.inhibit))
    self.assertLess(v_lead, 4.3, "the queue is still well under aggressive's hand-back (4.78 m/s, 17.2 km/h)")
    self.assertAlmostEqual(c.v_schedule, v_lead + 2. * (1.2 - 0.3), places=6)
    self.assertTrue(all(st == (0., 0., True, Inhibit.speed) for st in states), states)
    # the queue settles at 14.8 km/h, under the hand-back: back in only by the 1 s hold and the ramp
    gs = []
    for _ in range(HOLD + 2):
      d += (v_lead - V0) * DT_MDL
      step(live, build_sm(V0, d, v_lead, a_lead=0., e2e=-0.1))
      gs.append(c.g)
    self.assertEqual(gs[:HOLD], [0.] * HOLD)
    self.assertGreater(gs[-1], 0.)

  def test_after_the_hand_back_it_never_snaps_back_in(self):
    # the release13 review: handed back (the lead at 18 km/h, past aggressive's 17.2), the lead then eases off - not
    # enough for any braking trip - and the schedule speed falls under the hand-back: the closer gap must come back by
    # the hold and the ramp, never at full authority on that frame
    live = planner(True)
    c = live.close_follow
    for sm in queue(GAP_FULL + 20):
      step(live, sm)
    d, v_lead = D0, 5.0
    for _ in range(20):
      d += (v_lead - V0) * DT_MDL
      step(live, build_sm(V0, d, v_lead, e2e=-0.1))
    self.assertEqual((c.weight, c.g, c.gap.authority, c.gap.inhibit), (0., 0., 0., Inhibit.speed))
    gs = []
    for _ in range(45):
      v_lead = max(4.4, v_lead - 0.4 * DT_MDL)
      d += (v_lead - V0) * DT_MDL
      step(live, build_sm(V0, d, v_lead, a_lead=-0.4, e2e=-0.1))
      gs.append((c.weight, c.g, c.gap.inhibit))
    first = next(k for k, x in enumerate(gs) if x[0] > 0.)
    self.assertTrue(all(g == 0. for _, g, _ in gs[first:first + HOLD]), gs)
    self.assertTrue(all(i in (Inhibit.speed, Inhibit.hold) for _, _, i in gs[:first + HOLD]), gs)

  def test_the_real_gap_0_8_m_short_raises_nothing(self):
    # round 2 (the verification): the 1 m bounded the TARGET gap; the gap the car really kept drifted further (a gentle
    # pull-away, stop-and-go waves). Close follow now reads leadOne's radar gap against the personality's own desired gap
    # (long_mpc's, at vEgo and vLead): 0.8 m short or more (0.9 in round 2; push12 settles 0.1-0.2 m past its desired
    # gap, so about 1 m short of the gap it really keeps) and it raises nothing - its MPC candidate is the plan's own, no
    # lift, no mark, and the log says realGap; 0.55 m short or less and it is all in; between, it fades
    t_p = get_T_FOLLOW(P.aggressive)
    want = desired_gap(V0, V0, t_p)
    for short, w in ((1.2, 0.), (0.85, 0.), (0.675, 0.5), (0.5, 1.)):
      with self.subTest(short=short):
        pm = PM()
        pl = planner(True)
        c = pl.close_follow
        for _ in range(GAP_FULL + 5):
          step(pl, build_sm(V0, want - short, V0, e2e=-0.1), pm)
        self.assertAlmostEqual(c.real_deficit, short, places=5)   # (the radar's dRel is a Float32)
        self.assertAlmostEqual(c.real_w, w, places=4)
        self.assertEqual(c.gap.authority, 1.)
        self.assertAlmostEqual(c.g, w, places=4)
        last = last_log(pm)
        self.assertAlmostEqual(last.realDeficit, short, places=4)
        self.assertAlmostEqual(last.realWeight, w, places=4)
        if w == 0.:
          self.assertEqual((c.a_mpc_close, c.lifting, c.raised, c.tightening), (c.a_mpc, 0., 0., False))
          self.assertEqual((c._gap_inhibit(), last.gapInhibit.raw), (Inhibit.realGap, int(Inhibit.realGap)))
          self.assertFalse(last.gapActive)
        else:
          self.assertGreater(c.a_mpc_close, c.a_mpc + 0.01, "the closer gap pulls in")
          self.assertEqual(last.gapInhibit.raw, int(Inhibit.none))

  def test_the_closer_gap_fades_in_over_3_6_to_7_2_km_h(self):
    # round 2 (stop-and-go in the verification): a car that comes to rest a moment sooner also sets off a moment sooner,
    # and the waves put it up to 1.8 m off push12. Under 3.6 km/h the closer gap is out - at a standstill, creeping into
    # a stop and through a launch the plan is push12's - from 7.2 km/h it is all in, linear between; the log says speed
    t_p = get_T_FOLLOW(P.aggressive)
    for v, w in ((0.5, 0.), (1.0, 0.), (1.5, 0.5), (2.0, 1.), (3.0, 1.)):
      with self.subTest(v=v):
        pm = PM()
        pl = planner(True)
        c = pl.close_follow
        for _ in range(GAP_FULL + 5):
          step(pl, build_sm(v, desired_gap(v, v, t_p), v, e2e=-0.1), pm)
        self.assertLess(c.t_follow, c.t_personality)
        self.assertEqual((c.gap.authority, c.real_w), (1., 1.))
        self.assertAlmostEqual(c.speed_in, w, places=9)
        self.assertAlmostEqual(c.g, w, places=9)
        last = last_log(pm)
        if w == 0.:
          self.assertEqual((c.a_mpc_close, c.raised, c.tightening), (c.a_mpc, 0., False))
          self.assertEqual((c._gap_inhibit(), last.gapInhibit.raw), (Inhibit.speed, int(Inhibit.speed)))
          self.assertFalse(last.gapActive)
        else:
          self.assertEqual(last.gapInhibit.raw, int(Inhibit.none))
          self.assertTrue(last.gapActive)

  def test_a_queue_pulling_away_gently_fades_it_out(self):
    # round 2 (the verification's most common case: a queue pulling away at 0.6-0.8 m/s^2 from a crawl): read 2 s ahead,
    # the schedule stayed well under the hand-back, so the closer gap stayed in at full authority while the car sped up,
    # and the real gap fell over 1 m behind push12's. Now leadOne's aLeadK over 0.3 for 0.5 s is a pull-away: nothing
    # tightens, the closer gap fades out at 1/s. A creeping queue's swell (0.25 m/s^2) is not one
    for a_lead, pulls in ((0.5, True), (0.25, False)):
      with self.subTest(a_lead=a_lead):
        pm = PM()
        live = planner(True)
        c = live.close_follow
        for sm in queue(GAP_FULL + 20):
          step(live, sm, pm)
        self.assertEqual(c.g, 1.)
        d, v_lead, gs = D0, V0, []
        for _ in range(32):
          v_lead += a_lead * DT_MDL
          d += (v_lead - V0) * DT_MDL
          step(live, build_sm(V0, d, v_lead, a_lead=a_lead, e2e=-0.1), pm)
          gs.append((c.g, c.pull_away, c.gap.inhibit, c.weight))
        self.assertTrue(all(w > 0. for *_, w in gs), "under aggressive's hand-back all along")
        if not pulls:
          self.assertTrue(all(g == 1. and not pa for g, pa, _, _ in gs))
          continue
        n = round(cf.PULL_AWAY_T / DT_MDL)
        self.assertTrue(all(g == 1. and not pa for g, pa, _, _ in gs[:n - 1]))
        self.assertTrue(all(pa and i == Inhibit.pullAway for _, pa, i, _ in gs[n - 1:]))
        np.testing.assert_allclose([g for g, *_ in gs[n - 1:n + 9]], [1. - cf.PULL_AWAY_FALL * DT_MDL * (k + 1) for k in range(10)],
                                   atol=1e-12)
        self.assertEqual(gs[-1][0], 0.)
        self.assertTrue(last_log(pm).pullAway)

  def test_its_follow_time_shortens_no_faster_than_0_1_s_per_s(self):
    # round 2 (the verification): under the hand-back, a lead that stopped accelerating dropped the close follow time from
    # the look-ahead's to the crawl's on one frame, at full authority. It shortens at most T_FOLLOW_FALL now (0.1 s per
    # s); it lengthens at once
    live = planner(True)
    c = live.close_follow
    for sm in queue(GAP_FULL + 20):
      step(live, sm)
    ts = []
    for a_lead in [0.] * 5 + [0.9] * 30 + [0.] * 70:
      step(live, build_sm(V0, D0, V0, a_lead=a_lead, e2e=-0.1, experimental=False))
      ts.append(c.t_follow)
    steps = np.diff(ts)
    self.assertGreaterEqual(float(steps.min()), -0.1 * DT_MDL - 1e-12, "0.1 s per s")
    self.assertGreater(float(steps.max()), 0.2, "it lengthened at once when the lead pulled away")
    self.assertAlmostEqual(ts[-1], close_t_follow(V0, c.t_personality, V0, 0.), places=9)
    self.assertLess(ts[-1], max(ts) - 0.2, "and fell back, slowly")

  def test_no_mark_at_a_standstill(self):
    # round 2 (the verification): stopped behind a lead creeping a few meters ahead, the mark showed - nothing moves closer
    # at a standstill. tightening is never set while the car stands. (Round 3: the creeping leads at aLeadK 0.2 - at 0.3
    # or 0.5, as a Float32 over PULL_AWAY_ACCEL, they tripped the pull-away and never reached the standstill rule)
    for d, v_lead, a_lead in ((4.0, 0.3, 0.2), (6.5, 0.3, 0.2), (7.0, 0.5, 0.2), (6.0, 0., 0.)):
      for experimental in (False, True):
        with self.subTest(d=d, v_lead=v_lead, experimental=experimental):
          pm = PM()
          pl = planner(True)
          c = pl.close_follow
          tight = []
          for _ in range(80):
            step(pl, build_sm(0.05, d, v_lead, a_lead=a_lead, e2e=0., experimental=experimental), pm)
            tight.append(c.tightening)
          self.assertFalse(any(tight))
          self.assertFalse(c.pull_away, "the standstill itself, not a pull-away")
          self.assertFalse(any(messaging.log_from_bytes(b).customReserved10.tightening for b in pm.sent['customReserved10']))

  def test_after_the_hand_back_the_lift_waits_for_the_gap(self):
    # round 2 (the verification): handed back (the lead at 18 km/h, past aggressive's 17.2), then the lead eases to 15.8
    # km/h: the gap comes back by its 1 s hold and ramp - and the lift, whose authority the hand-back now resets too, only
    # after its own 3 s hold (it used to be back at once, before the gap)
    live = planner(True)
    c = live.close_follow
    for sm in queue(LIFT_FULL + 20):
      step(live, sm)
    self.assertTrue(c.gap_active and c.lifting > 0.)
    d, v_lead = D0, 5.0
    for _ in range(20):
      d += (v_lead - V0) * DT_MDL
      step(live, build_sm(V0, d, v_lead, e2e=-0.1))
    self.assertEqual((c.weight, c.g, c.lifting, c.lift.authority, c.lift.inhibit), (0., 0., 0., 0., Inhibit.speed))
    rows = []
    for _ in range(120):
      v_lead = max(4.4, v_lead - 0.4 * DT_MDL)
      d += (v_lead - V0) * DT_MDL
      step(live, build_sm(V0, d, v_lead, a_lead=-0.4 if v_lead > 4.4 else 0., e2e=-0.1))
      rows.append((c.weight, c.g, c.lifting))
    first_w = next(k for k, r in enumerate(rows) if r[0] > 0.)
    first_g = next(k for k, r in enumerate(rows) if r[1] > 0.)
    first_lift = next(k for k, r in enumerate(rows) if r[2] > 0.)
    self.assertEqual(first_g, first_w + HOLD)
    self.assertGreaterEqual(first_lift, first_w + round(cf.LIFT_HOLD_S / DT_MDL), "the lift's own 3 s hold, after the gap")

  # --- round 3 (the verification of round 2): rules its mutations showed no test pinned ---

  def test_the_real_deficit_reads_the_leads_speed(self):
    # the personality's desired gap at vEgo AND vLead, as long_mpc's: closing on a slower car, (vEgo^2 - vLead^2) /
    # (2 COMFORT_BRAKE) is part of it. Here 0.59 m of it: the static gap alone is 0.3 m short (all in), with the closing
    # term 0.89 m (nothing). A deficit read without vLead let a closing car in at full strength
    t_p = get_T_FOLLOW(P.aggressive)
    v_lead = V0 - 0.45                     # closing at 0.45 m/s: under the time-to-collision rule's 0.5
    d = desired_gap(V0, V0, t_p) - 0.3
    full = desired_gap(V0, v_lead, t_p) - d
    self.assertAlmostEqual(full - 0.3, (V0 ** 2 - v_lead ** 2) / (2 * cf.COMFORT_BRAKE), places=9)
    self.assertEqual(cf.real_weight(0.3), 1.)
    self.assertLess(cf.real_weight(full), 0.1)
    pm = PM()
    pl = planner(True)
    c = pl.close_follow
    for _ in range(GAP_FULL + 5):
      step(pl, build_sm(V0, d, v_lead, e2e=-0.1), pm)
    self.assertAlmostEqual(c.real_deficit, full, places=4)
    self.assertAlmostEqual(c.real_w, cf.real_weight(full), places=4)
    self.assertAlmostEqual(last_log(pm).realDeficit, full, places=4)

  def test_the_real_deficit_is_filtered_and_starts_again_for_each_car(self):
    # filtered over REAL_GAP_TAU_S: one radar frame 1 m short (noise) moves it a k-th of the way and the weight stays;
    # read raw it dropped close follow out on that frame. Another radar car (a track swap) starts it again from that
    # car's own deficit on the frame - blended into the old car's, a closer car read as the old one's longer gap
    t_p = get_T_FOLLOW(P.aggressive)
    want = desired_gap(V0, V0, t_p)
    k = DT_MDL / (cf.REAL_GAP_TAU_S + DT_MDL)
    pl = planner(True)
    c = pl.close_follow
    for _ in range(GAP_FULL + 5):
      step(pl, build_sm(V0, want - 0.3, V0, e2e=-0.1))
    self.assertAlmostEqual(c.real_deficit, 0.3, places=4)
    self.assertEqual((c.real_w, c.g), (1., 1.))
    step(pl, build_sm(V0, want - 1.3, V0, e2e=-0.1))
    self.assertAlmostEqual(c.real_raw, 1.3, places=4)
    self.assertAlmostEqual(c.real_deficit, 0.3 + k * 1.0, places=4)
    self.assertEqual((c.real_w, c.g), (1., 1.))
    for _ in range(120):
      step(pl, build_sm(V0, want - 0.3, V0, e2e=-0.1))
    self.assertAlmostEqual(c.real_deficit, 0.3, places=4)
    step(pl, build_sm(V0, want - 0.7, V0, e2e=-0.1, track=9))
    self.assertAlmostEqual(c.real_raw, 0.7, places=4)
    self.assertAlmostEqual(c.real_deficit, 0.7, places=4)
    self.assertEqual(c.gap.inhibit, Inhibit.leadChanged)

  def test_a_pull_away_is_counted_per_car(self):
    # PULL_AWAY_T of the SAME car over PULL_AWAY_ACCEL: a car that took over from another one pulling away starts the
    # count again (the old car's frames do not count toward the new one's)
    pl = planner(True)
    c = pl.close_follow
    for sm in queue(GAP_FULL + 20):
      step(pl, sm)
    n = round(cf.PULL_AWAY_T / DT_MDL)
    for _ in range(n - 2):
      step(pl, build_sm(V0, D0, V0, a_lead=0.5, e2e=-0.1, track=7))
    self.assertEqual((c.pull_frames, c.pull_away), (n - 2, False))
    rows = []
    for _ in range(n):
      step(pl, build_sm(V0, D0, V0, a_lead=0.5, e2e=-0.1, track=9))
      rows.append((c.pull_frames, c.pull_away))
    self.assertEqual(rows, [(j + 1, j + 1 >= n) for j in range(n)])

  def test_a_held_lead_two_leaves_no_real_gap(self):
    # radarState carrying a held lead (radard's clutter guard; made-up kinematics) anywhere: no real deficit is read off
    # it, as no gap is (GapGuard's noLead) - realWeight 0, realDeficit logged 0
    pm = PM()
    pl = planner(True)
    c = pl.close_follow
    for sm in queue(GAP_FULL + 5):
      step(pl, sm, pm)
    self.assertTrue(math.isfinite(c.real_deficit) and c.gap_active)
    step(pl, build_sm(V0, D0, V0, e2e=-0.1, held_two=True), pm)
    self.assertTrue(c.inputs.held and c.inputs.lead_one.present)
    self.assertTrue(math.isnan(c.real_deficit) and math.isnan(c.real_raw))
    self.assertEqual((c.real_w, c.g, c.raw_watch, c.gap.inhibit), (0., 0., False, Inhibit.noLead))
    self.assertEqual((last_log(pm).realDeficit, last_log(pm).realWeight), (0., 0.))

  def test_the_lift_reads_close_follows_own_follow_time(self):
    # the lift is handed CloseFollow.t_follow - the close follow time with its 0.1 s per s fall - not one worked out on
    # the frame: after a short swell of the lead's acceleration (no pull-away, no hand-back) the close follow time comes
    # back slowly, and the lift's fade near the personality's follows it
    from unittest import mock
    live = planner(True)
    c = live.close_follow
    t_p = get_T_FOLLOW(P.aggressive)
    for sm in queue(LIFT_FULL + 20):
      step(live, sm)
    self.assertTrue(c.gap_active and c.lifting > 0.)
    for _ in range(3):
      step(live, build_sm(V0, D0, V0, a_lead=0.9, e2e=-0.1))
    self.assertTrue(0. < t_p - c.t_follow < cf.LIFT_T_FADE, "close to the personality's, not past it")
    with mock.patch.object(c.lift, 'update', wraps=c.lift.update) as up:
      step(live, build_sm(V0, D0, V0, e2e=-0.1))
    self.assertEqual(up.call_args.kwargs['t_close'], c.t_follow)
    now = close_t_follow(V0, t_p, V0, 0.)
    self.assertGreater(c.t_follow - now, 0.1, "the instant one is back at the crawl's already")
    self.assertLess(lift_speed_weight(V0, c.t_follow, t_p), lift_speed_weight(V0, now, t_p))
    excess = float(np.interp(c.lift.gap_excess, cf.GAP_BP, [0., 1.]))
    self.assertAlmostEqual(c.lift.weight, excess * lift_speed_weight(V0, c.t_follow, t_p) * c.real_w, places=9)

  def test_never_the_mark_at_a_standstill_whatever_it_raised(self):
    # the planner's own standstill rule, behind speed_in (out under 3.6 km/h, so nothing is raised at a standstill): were
    # anything ever raised there, tightening - the mark - still says no while the car stands
    c = cf.CloseFollow(cf.MODE_LIVE)
    c.requested, c.g, c.raised = True, 1., 0.5
    c.inputs = inputs(v=0., standstill=True)
    self.assertTrue(c.active)
    self.assertFalse(c.tightening)
    c.inputs = inputs(v=0.5, standstill=False)
    self.assertTrue(c.tightening)

  def test_the_log_shows_the_raw_real_gap_while_the_gap_is_in_and_1_s_after(self):
    # rraw: the filtered deficit cannot pass REAL_GAP_MAX while the gap is in (the weight is 0 from there) - so rmax can
    # never show it - but the radar's own can. raw_watch: the gap in, or out under REAL_RAW_TAIL_S on the same car
    t_p = get_T_FOLLOW(P.aggressive)
    want = desired_gap(V0, V0, t_p)
    n = round(cf.REAL_RAW_TAIL_S / DT_MDL)
    for new_car in (False, True):
      with self.subTest(new_car=new_car):
        pl = planner(True)
        c = pl.close_follow
        for _ in range(GAP_FULL + 5):
          step(pl, build_sm(V0, want - 0.3, V0, e2e=-0.1))
        self.assertTrue(c.gap_active and c.raw_watch)
        rows = []
        for _ in range(n + 10):   # the car ahead braking: out at once
          step(pl, build_sm(V0, want - 0.3, V0, a_lead=-1.0, e2e=-0.1, track=9 if new_car else 7))
          rows.append((c.gap_active, c.raw_watch))
        self.assertFalse(any(g for g, _ in rows))
        self.assertEqual([w for _, w in rows], [False] * (n + 10) if new_car else [True] * n + [False] * 10)

  def test_disengaged_it_is_off(self):
    pl = planner(False)
    for sm in queue(200):
      step(pl, sm)
    step(pl, build_sm(V0, D0, V0, engaged=False))
    c = pl.close_follow
    self.assertEqual((c.requested, c.gap_active, c.lifting, c.gap.inhibit), (False, False, 0., Inhibit.notRequested))

  def test_the_switch_applies_without_a_reboot(self):
    # the user's CloseFollow, re-read every 3 s and at every engage (reads only): off -> shadow on that frame, the plan
    # its own MPC's at once (as on any trip); on -> live, from nothing (the 1 s hold, then the 0.5/s ramp)
    pl = planner(True)
    c = pl.close_follow
    params = Params()
    frames = queue(900)
    for sm in frames[:200]:
      step(pl, sm)
    self.assertTrue(c.live and c.gap_active and c.g == 1.)
    params.put_bool("CloseFollow", False, block=True)
    k_off = None
    for k, sm in enumerate(frames[200:300]):
      step(pl, sm)
      if not c.live:
        k_off = k
        plan = plan_trajectories(pl.mpc)
        np.testing.assert_allclose(pl.a_desired_trajectory, plan[1], rtol=0., atol=0.)
        self.assertEqual((c.g, c.gap.authority, c.lift.authority, c.lifting), (0., 0., 0., 0.))
        break
    assert k_off is not None, "switched off within 3 s"
    self.assertLessEqual(k_off, round(cf.SWITCH_READ_S / DT_MDL))
    self.assertTrue(c.defer)
    params.put_bool("CloseFollow", True, block=True)
    gs = []
    for sm in frames[300 + k_off:600]:
      step(pl, sm)
      gs.append((c.live, c.g))
    k_on = next(k for k, s in enumerate(gs) if s[0])
    self.assertLessEqual(k_on, round(cf.SWITCH_READ_S / DT_MDL))
    after = [g for _, g in gs[k_on:]]
    self.assertEqual(after[:HOLD], [0.] * HOLD, "from nothing: the 1 s hold")
    self.assertLessEqual(max(np.diff(after)), cf.GAP_RISE * DT_MDL + 1e-12, "then the ramp, never a step")
    self.assertEqual(c.switch_changes, 2)
    # at an engage it is read on the frame (the 3 s read is held off here, so only the engage can read it)
    params.put_bool("CloseFollow", False, block=True)
    c._read_left = 10
    step(pl, build_sm(V0, D0, V0, engaged=False))
    self.assertTrue(c.live, "not read while disengaged before its 3 s")
    step(pl, frames[0])
    self.assertFalse(c.live, "read at the engage")

  def test_an_unreadable_switch_is_shadow(self):
    from unittest import mock
    pl = planner(True)
    c = pl.close_follow
    with mock.patch.object(c.params, 'get', side_effect=RuntimeError("no such key")):
      for sm in queue(round(cf.SWITCH_READ_S / DT_MDL) + 2):
        step(pl, sm)
    self.assertFalse(c.live)
    self.assertFalse(c.failed, "a failed read is shadow, not a failure")

  def test_the_summary_line(self):
    from unittest import mock
    pl = planner(False)
    with mock.patch.object(cf.cloudlog, 'event') as event:
      for sm in queue(450, brake_at=300):
        step(pl, sm)
    calls = [c for c in event.call_args_list if c.args and c.args[0] == 'closefollow']
    self.assertEqual(len(calls), 2, "at most one per 10 s of plans")
    kw = calls[0].kwargs
    self.assertTrue(kw['error'])
    self.assertEqual((kw['mode'], kw['sw'], kw['n'], kw['brk'], kw['live']), (1, 0, 200, 0, 0.))
    self.assertGreater(kw['gap'], 0.)
    self.assertLess(kw['dg'], 0.)
    # the slowest close-MPC solve and the slowest whole step of the 10 s, in ms (the owner's view of its cost)
    self.assertTrue(0. < kw['smax'] <= kw['xmax'] < 1000.)
    self.assertEqual(calls[1].kwargs['brk'], 0)
    self.assertTrue('leadBraking' in calls[1].kwargs['why'])
    # live: the switch and the share of the chances it tightened
    pl = planner(True)
    with mock.patch.object(cf.cloudlog, 'event') as event:
      for sm in queue(450, brake_at=300):
        step(pl, sm)
    calls = [c for c in event.call_args_list if c.args and c.args[0] == 'closefollow']
    kw = calls[0].kwargs
    self.assertEqual((kw['mode'], kw['sw'], kw['brk']), (2, 1, 0))
    self.assertGreater(kw['live'], 0.5)
    # up: the most it raised the plan (live, a_target already has close follow's candidates: up was always 0 before the
    # release13 review)
    self.assertGreater(kw['up'], 0.1)
    self.assertEqual(calls[1].kwargs['brk'], 0)
    # rmax: the most the radar gap was short of the personality's desired gap while the gap was in (filtered); round 3's
    # rraw the same unfiltered, while in and 1 s after - here the queue's 5.6 m LONGER gap, steady, so both read it
    short = desired_gap(V0, V0, get_T_FOLLOW(P.aggressive)) - D0
    self.assertAlmostEqual(kw['rmax'], short, delta=0.006)
    self.assertAlmostEqual(kw['rraw'], short, delta=0.006)
    # no chance to act (no lead): silent
    pl = planner(False)
    with mock.patch.object(cf.cloudlog, 'event') as event:
      for _ in range(450):
        step(pl, build_sm(V0, None, 0.))
    self.assertFalse([c for c in event.call_args_list if c.args and c.args[0] == 'closefollow'])

  def test_a_failure_in_close_follow_never_reaches_the_plan(self):
    from unittest import mock

    def broken_mpc(dt):
      raise RuntimeError("acados said no")
    frames = queue(120)
    ref = drive(planner(close_follow=False), frames)
    for switch in (False, True):
      with self.subTest(switch=switch, where='mpc'):
        pl = planner(switch)
        pl.close_follow._mpc_factory = broken_mpc
        with mock.patch.object(cf.cloudlog, 'exception') as exc:
          out = drive(pl, frames)
        assert_same_frames(self, out, ref, "push12's plan on every frame")
        self.assertTrue(pl.close_follow.failed)
        self.assertEqual(exc.call_count, 1, "logged once")
      with self.subTest(switch=switch, where='lift'):
        pl = planner(switch)
        with mock.patch.object(pl.close_follow.lift, 'update', side_effect=ValueError("bad")), \
             mock.patch.object(cf.cloudlog, 'exception') as exc:
          out = drive(pl, frames)
        self.assertTrue(pl.close_follow.failed)
        self.assertEqual(exc.call_count, 1)
        # it failed on the first frame, still in its hold: nothing was ever blended
        assert_same_frames(self, out, ref, "push12's plan on every frame")
    # shadow: a failure in its deferred step (after the plan is sent) is caught and logged the same way
    pl = planner(False)
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


def last_pitch(pl) -> float:
  return float(pl.close_follow.pitch_f)


class TestAcceleratingQueue(OpenpilotTestCase):
  """The release13 review's case, closed loop on the real planner: a queue creeping at 8 km/h speeds up at 1.0 m/s^2
  to 22 km/h, and right then the lead brakes at -4 m/s^2 to a stop. A plain plant - the plan's accel after 0.3 s,
  lagged 0.3 s, braking at most 2.13 m/s^2 (the design brake at 320 counts) - and radar truth with aLeadK lagged 0.5 s.
  This is not the acceptance sim (the scratch closed-loop sims, with the car's real controllers, brake and radard): it
  pins the structure. When the lead brakes the closer gap is out - it was handed back while the queue was still
  speeding up - and the gap it costs at that moment, against the planner without close follow, is at most
  GAP_DEFICIT_MAX. Before the review the gap held from the crawl was 1.5-2 m short there on standard and relaxed."""
  DEAD, LAG, BRAKE_MAX, A_LAG = 6, 0.3, -2.13, 0.5

  def run_queue(self, close_follow: bool, personality, v0=8 / 3.6, v1=22 / 3.6, acc=1.0, a_l=-4., settle=15.):
    pl = planner(True, close_follow=close_follow)
    t_up = (v1 - v0) / acc
    x_e, v_e, a_e = 0., v0, 0.
    x_l, v_l, a_lk = get_T_FOLLOW(personality) * v0 + 6., v0, 0.
    cmd = [0.] * self.DEAD
    t, rec = -settle - t_up, []
    while t < 8. and not (t > 0. and v_e < 0.2):   # to the stop (no closer after it; the scene has no standstill)
      a_l_now = a_l if t >= 0. else (acc if t >= -t_up else 0.)
      if v_l <= 0. and a_l_now < 0.:
        a_l_now = 0.
      a_lk += (a_l_now - a_lk) * DT_MDL / (self.A_LAG + DT_MDL)
      step(pl, build_sm(v_e, x_l - x_e, v_l, a_lead=a_lk, e2e=0., experimental=False, personality=personality))
      cmd.append(float(np.clip(pl.output_a_target, self.BRAKE_MAX, 2.)))
      a_e += (cmd.pop(0) - a_e) * DT_MDL / (self.LAG + DT_MDL)
      v_l = max(0., v_l + a_l_now * DT_MDL)
      x_l += v_l * DT_MDL
      v_e = max(0., v_e + a_e * DT_MDL)
      x_e += v_e * DT_MDL
      c = pl.close_follow
      rec.append((round(t, 2), x_l - x_e, c.g if c is not None else 0., v_e))
      t += DT_MDL
    onset = next(r for r in rec if r[0] >= 0.)
    return onset[1], min(r[1] for r in rec if r[0] >= 0.), max(r[2] for r in rec if -0.5 <= r[0]), rec

  def test_the_closer_gap_is_out_and_costs_at_most_1_m_when_the_lead_brakes(self):
    for p in (P.aggressive, P.standard, P.relaxed):
      with self.subTest(personality=p):
        gap_live, min_live, g_live, rec = self.run_queue(True, p)
        gap_ref, min_ref, _, _ = self.run_queue(False, p)
        crawl = [r for r in rec if r[0] < -6.]
        self.assertTrue(any(r[2] >= 1. for r in crawl), "it held the closer gap at the crawl")
        self.assertEqual(g_live, 0., "out from 0.5 s before the lead brakes, through the stop")
        self.assertGreaterEqual(gap_live, gap_ref - cf.GAP_DEFICIT_MAX - 0.1,
                                f"the gap when the lead brakes: {gap_live:.2f} m against {gap_ref:.2f} without close follow")
        self.assertGreaterEqual(min_live, min_ref - cf.GAP_DEFICIT_MAX - 0.1,
                                f"the closest it comes: {min_live:.2f} m against {min_ref:.2f}")

  def run_waves(self, close_follow: bool, personality, cycles=4, v_top=15 / 3.6, acc=1.0, dec=-1.0, cruise=6., wait=3.):
    """Stop-and-go: the lead from a standstill to v_top at acc, cruise s, to a stop at dec, wait s - cycles times - with
    the same plant as run_queue. Returns the gaps every frame and the gap at each standstill (both stopped)."""
    pl = planner(True, close_follow=close_follow)
    x_e, v_e, a_e = 0., 0., 0.
    x_l, v_l, a_lk = 6., 0., 0.
    cmd = [0.] * self.DEAD
    phases = []
    for _ in range(cycles):
      phases += [(acc, (v_top / acc)), (0., cruise), (dec, (v_top / -dec)), (0., wait)]
    gaps, stand = [], []
    t_end = sum(d for _, d in phases)
    t, standing = 0., False
    while t < t_end:
      tt, a_l_now = t, 0.
      for a, d in phases:
        if tt < d:
          a_l_now = a
          break
        tt -= d
      if v_l <= 0. and a_l_now < 0.:
        a_l_now = 0.
      a_lk += (a_l_now - a_lk) * DT_MDL / (self.A_LAG + DT_MDL)
      # (at least 1 cm/s: build_sm's flat model path at exactly 0 divides by zero in SCC's vision curve)
      step(pl, build_sm(max(v_e, 0.01), x_l - x_e, v_l, a_lead=a_lk, e2e=0., experimental=False, personality=personality))
      cmd.append(float(np.clip(pl.output_a_target, self.BRAKE_MAX, 2.)))
      a_e += (cmd.pop(0) - a_e) * DT_MDL / (self.LAG + DT_MDL)
      v_l = max(0., v_l + a_l_now * DT_MDL)
      x_l += v_l * DT_MDL
      v_e = max(0., v_e + a_e * DT_MDL)
      if v_e <= 0. and a_e < 0.:
        a_e = 0.
      x_e += v_e * DT_MDL
      gaps.append(x_l - x_e)
      now = v_l < 0.01 and v_e < 0.05
      if standing and not now:
        stand.append(gaps[-2])
      standing = now
      t += DT_MDL
    return gaps, stand

  def test_stop_and_go_never_stacks_up(self):
    # round 2 (the verification's stop-and-go waves): the 1 m bounded the target, so the offset stacked up - up to 2.7 m
    # less room in its sim, and stops up to 2.5 m closer than push12. Here (four waves to 15 km/h, 6 s at it) 5a778e077's
    # gap fell 1.4 m short of push12's on relaxed. With the real gap read against the personality's own and the closer
    # gap out under 3.6 km/h, never more than 1 m (0.4 here), and every stop within 0.3 m of push12's (0.1 here)
    for p in (P.relaxed, P.standard):
      with self.subTest(personality=p):
        g_live, s_live = self.run_waves(True, p)
        g_ref, s_ref = self.run_waves(False, p)
        self.assertEqual(len(s_live), len(s_ref))
        self.assertGreaterEqual(len(s_live), 2)
        short = max(b - a for a, b in zip(g_live, g_ref, strict=True))
        self.assertLessEqual(short, 1.0, f"the real gap short of push12's by {short:.2f} m")
        stops = [b - a for a, b in zip(s_live, s_ref, strict=True)]
        self.assertLessEqual(max(stops), 0.3, f"stopped closer than push12 by {stops}")

  def test_a_gentle_pull_away_from_a_crawl_costs_at_most_1_m_of_real_gap(self):
    # round 2 (the verification's most common case): a queue creeping at 5 km/h pulls away at 0.6-0.8 m/s^2 - read 2 s
    # ahead, still well under the hand-back - and then brakes at -6. The closer gap stayed in at full authority while
    # the car sped up, and the REAL gap at the brake was up to 1.3 m short of push12's. With the real gap read against
    # the personality's own and the pull-away rule, never more than 1 m - at the onset, through the whole run and at
    # the closest point
    for p, acc in ((P.relaxed, 0.6), (P.relaxed, 0.8), (P.standard, 0.6)):
      with self.subTest(personality=p, acc=acc):
        _, min_live, _, rec_live = self.run_queue(True, p, v0=5 / 3.6, acc=acc, a_l=-6.)
        _, min_ref, _, rec_ref = self.run_queue(False, p, v0=5 / 3.6, acc=acc, a_l=-6.)
        crawl = [r for r in rec_live if r[0] < -12.]
        self.assertTrue(any(r[2] > 0.5 for r in crawl), "it followed closer at the crawl")
        lost = [b[1] - a[1] for a, b in zip(rec_live, rec_ref, strict=False) if a[0] <= 0.]
        self.assertLessEqual(max(lost), 1.0 + 0.1, f"the real gap, up to the brake: {max(lost):.2f} m short")
        self.assertGreaterEqual(min_live, min_ref - 1.0, f"the closest: {min_live:.2f} m against {min_ref:.2f}")
