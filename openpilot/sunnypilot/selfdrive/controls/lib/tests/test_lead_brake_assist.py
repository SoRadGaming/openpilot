"""FORK(LEAD-BRAKE): brake sooner when the car ahead brakes hard (lead_brake_assist.py).

TestProjection pins the projection of leadOne: what counts as a radar sample, the raw decel and every gate on it (a
moving track, range, falling sample after sample, radard's own aLeadK, the two windows), held / vision / stationary /
oncoming leads left out, a track change or a dropout restarting it, the tau attack confirmed on two distinct samples
and never prolonging an acceleration. TestPlanner drives the real LongitudinalPlanner (MPC included) behind a lead
that brakes hard: mode 1 (shadow) is bit for bit the planner without the assist and logs what mode 2 would do, mode 2
plans the braking sooner, the comparison solve reproduces the planner's own solve exactly, every other car / stock ACC
/ mode 0 builds nothing, and a failure turns the assist off without touching the plan.
"""
import math
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import numpy as np

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.cereal import custom, log, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.radard import KalmanParams, Track
from openpilot.sunnypilot.selfdrive.controls.lib import lead_brake_assist as lba
from openpilot.sunnypilot.selfdrive.controls.lib.held_lead import held_id

V0 = 20.0


def lead(tid=7, d=35.0, v_lead=V0, a=0.0, tau=1.5, radar=True, present=True, v_ego=V0):
  return SimpleNamespace(present=present, radar=radar, radarTrackId=tid, dRel=d, vRel=v_lead - v_ego, vLead=v_lead,
                         vLeadK=v_lead, aLeadK=a, aLeadTau=tau, modelProb=1.0, yRel=0.0)


def braking(n=80, brake_at=20, a_brake=-4.0, d0=35.0, v0=V0, tid=7, noise=None):
  """radarState leadOne frames (20 Hz) behind a lead at v0 that brakes at a_brake from frame brake_at: the radar reports
  at 10 Hz (each reading twice), quantized as the Elesys decode (1/128 m, 1/64 m/s), and radard's own Track (Kalman
  aLeadK, aLeadTau) runs on it. The car holds v0 (open loop)."""
  kp = KalmanParams(DT_MDL)
  tr = None
  v, d = v0, d0
  out, last = [], None
  for k in range(n):
    if k >= brake_at:
      v = max(0.0, v + a_brake * DT_MDL)
    d += (v - v0) * DT_MDL
    if k % 2 == 0:
      vr = v - v0 + (noise(k) if noise else 0.0)
      last = (round(d * 128) / 128, round(vr * 64) / 64)
    dr, vr = last
    if tr is None:
      tr = Track(tid, vr + v0, kp)
    tr.update(dr, 0.0, vr, vr + v0)
    out.append(SimpleNamespace(**tr.get_RadarState(1.0)))
  return out


def run(frames, cfg=None, v_ego=V0):
  a = lba.LeadBrakeAssist(cfg)
  return [a.update(f, (k + 1) * DT_MDL, v_ego) for k, f in enumerate(frames)]


class TestProjection(OpenpilotTestCase):
  def test_defaults(self):
    c = lba.Config()
    self.assertEqual((c.raw_thr, c.raw_win, c.raw_thr2, c.raw_win2, c.raw_d_max, c.raw_kalman, c.raw_v_moving),
                     (-2.0, 0.3, -1.5, 0.5, 80.0, -1.0, 4.0))
    self.assertTrue(c.use_raw and c.use_tau and c.tau_from_proj and c.tau_needs_raw)
    self.assertFalse(c.raw_frames or c.raw_gap_gate)
    self.assertEqual(lba.MODE_DEFAULT, lba.MODE_SHADOW)

  def test_a_hard_braking_lead_is_projected_sooner_than_radards_kalman(self):
    frames = braking()
    ps = run(frames)
    first = next(k for k, p in enumerate(ps) if p.active)
    k_minus2 = next(k for k, f in enumerate(frames) if f.aLeadK < -2.0)
    self.assertLess(first, k_minus2, "the projection starts before radard's aLeadK reaches -2")
    p = ps[first]
    self.assertTrue(p.reason & lba.REASON_RAW)
    self.assertAlmostEqual(p.a_lead, -4.0, delta=0.4)
    self.assertLess(p.a_lead, p.a_lead_k)
    # the projection never claims less braking than radard does
    self.assertTrue(all(q.a_lead <= q.a_lead_k + 1e-12 for q in ps))
    self.assertTrue(all(q.tau <= q.a_lead_tau + 1e-12 for q in ps))

  def test_ordinary_following_is_untouched(self):
    rng = np.random.default_rng(0)
    frames = braking(n=200, brake_at=10_000, noise=lambda k: float(rng.normal(0.0, 0.05)))
    self.assertFalse(any(p.active for p in run(frames)))
    frames = braking(n=120, brake_at=20, a_brake=-1.2)   # a lead easing off at -1.2: not hard braking
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(frames)))

  def test_vision_held_and_absent_leads_are_never_projected(self):
    for kw in ({'radar': False, 'tid': -1}, {'tid': held_id(7)}, {'present': False}):
      frames = [SimpleNamespace(**{**vars(f), **{k: v for k, v in kw.items() if k != 'tid'},
                                   'radarTrackId': kw.get('tid', f.radarTrackId)}) for f in braking()]
      ps = run(frames)
      self.assertFalse(any(p.active or p.reason for p in ps), kw)
      self.assertTrue(all(p.a_lead == p.a_lead_k and p.tau == p.a_lead_tau for p in ps), kw)

  def test_a_track_never_seen_moving_is_blocked(self):
    # stationary returns (clutter, a stopped car) and oncoming traffic: never read 4 m/s while leadOne
    frames = braking(v0=3.5, a_brake=-3.0)
    ps = run(frames, v_ego=3.5)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    self.assertTrue(any(p.blocked & lba.BLOCK_SLOW for p in ps))
    oncoming = [lead(d=60.0 - k, v_lead=-8.0 - 0.3 * k, a=-2.5) for k in range(40)]
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(oncoming)))

  def test_far_leads_are_blocked(self):
    ps = run(braking(d0=110.0))
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    self.assertTrue(any(p.blocked & lba.BLOCK_RANGE for p in ps))

  def test_a_speed_jump_is_not_a_braking_car(self):
    # one radar reading 2 m/s low (a Doppler glitch), then back: the samples do not fall one after another
    frames = braking(n=80, brake_at=10_000, noise=lambda k: -2.0 if k == 40 else 0.0)
    ps = run(frames)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    # a step down that stays (another object's speed, 25 m/s^2 in one reading): the window restarts from it
    frames = braking(n=80, brake_at=10_000, noise=lambda k: -2.5 if k >= 40 else 0.0)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in run(frames)))

  def test_radards_own_kalman_must_agree(self):
    cfg = lba.Config()
    frames = braking()
    for f in frames:
      f.aLeadK = max(f.aLeadK, -0.9)     # radard's estimate never under -1.0
    ps = run(frames, cfg)
    self.assertFalse(any(p.reason & lba.REASON_RAW for p in ps))
    self.assertTrue(any(p.blocked & lba.BLOCK_KALMAN for p in ps))

  def test_a_repeated_radar_reading_is_one_sample(self):
    # radard runs at 20 Hz on a 10 Hz radar: a reading repeated on the next frame is the same sample; one that is still
    # the same a radar period later is a new sample of a lead that kept its distance and speed
    a = lba.LeadBrakeAssist()
    f = lead(d=30.0, v_lead=18.0)
    a.update(f, DT_MDL, V0)
    a.update(f, 2 * DT_MDL, V0)
    self.assertEqual(a.track.n, 1)
    a.update(f, 3 * DT_MDL, V0)
    self.assertEqual(a.track.n, 2)
    for k in range(4, 24):
      a.update(f, k * DT_MDL, V0)
    self.assertEqual(a.track.n, 12)

  def test_a_new_track_or_a_dropout_restarts(self):
    frames = braking()
    a = lba.LeadBrakeAssist()
    ps = [a.update(f, (k + 1) * DT_MDL, V0) for k, f in enumerate(frames[:30])]
    swapped = [SimpleNamespace(**{**vars(f), 'radarTrackId': 8}) for f in frames[30:]]
    ps2 = [a.update(f, (k + 31) * DT_MDL, V0) for k, f in enumerate(swapped)]
    self.assertEqual(a.track.tid, 8)
    first = next(k for k, p in enumerate(ps2) if p.reason & lba.REASON_RAW)
    self.assertGreaterEqual(first, 2 * 5, "a new track needs its own 0.5 s of samples")
    # a 0.3 s dropout of new samples clears the window
    a = lba.LeadBrakeAssist()
    for k, f in enumerate(frames[:40]):
      a.update(f, (k + 1) * DT_MDL, V0)
    a.update(frames[40], 47 * DT_MDL, V0)
    self.assertEqual(len(a.track.samples), 1)
    del ps

  def test_non_finite_and_malformed_leads(self):
    a = lba.LeadBrakeAssist()
    for bad in (lead(d=math.nan), lead(a=math.inf), lead(tau=math.nan), SimpleNamespace(present=True)):
      p = a.update(bad, 1.0, V0)
      self.assertFalse(p.active)
    self.assertFalse(a.update(lead(), math.nan, V0).active)

  def test_the_tau_attack_needs_two_distinct_samples_and_never_prolongs_an_acceleration(self):
    cfg = lba.Config(use_raw=False, use_tau=True, tau_from_proj=False, tau_needs_raw=False)   # CarrotPilot's own
    tb = lba._TauBoost(cfg)
    self.assertEqual(tb.update(-2.0, -4.0, 1.0, True), 1.0)          # one sample: ordinary decay only
    self.assertEqual(tb.update(-2.0, -4.0, 1.0, False), 1.0)         # the same sample again: no confirmation
    self.assertLess(tb.update(-2.0, -4.0, 1.1, True), 0.6)           # a second, distinct one 0.1 s later: the attack
    self.assertEqual(tb.update(0.3, -4.0, 1.2, True), 1.0)           # accelerating: radard's tau back at once
    tb = lba._TauBoost(cfg)
    tb.update(-2.0, -4.0, 1.0, True)
    self.assertEqual(tb.update(-2.0, -4.0, 1.3, True), 1.0)          # 0.3 s apart: too far to confirm
    # end to end: the attack shortens radard's aLeadTau on a hard-braking lead, and only while the lead brakes
    ps = run(braking(), cfg)
    self.assertTrue(any(p.reason & lba.REASON_TAU for p in ps))
    self.assertTrue(all(p.a_lead == p.a_lead_k for p in ps))
    # the default: the attack only on samples where the raw decel counts, so never on a lead the raw decel leaves alone
    ps = run(braking(), lba.Config(raw_kalman=-10.0))
    self.assertFalse(any(p.reason for p in ps))
    tb = lba._TauBoost(lba.Config())
    tb.update(-2.0, -4.0, 1.0, True, allowed=False)
    self.assertEqual(tb.update(-2.0, -4.0, 1.1, True, allowed=False), 1.0)

  def test_quadratic_jerk_weights(self):
    for n in (3, 6, 7, 9):
      w = np.array(lba.quadratic_jerk_weights(n))
      ts = np.arange(n) - (n - 1.0)
      self.assertAlmostEqual(float(w @ (0.5 * 3.0 * ts ** 2 + 2.0 * ts + 1.0)), 3.0, places=9)   # 2nd derivative of a parabola

  def test_predicted_dv(self):
    self.assertAlmostEqual(lba.predicted_dv(0.0, 1.5), 0.0)
    self.assertLess(lba.predicted_dv(-2.0, 0.0), lba.predicted_dv(-2.0, 1.5))
    self.assertAlmostEqual(lba.predicted_dv(-1.0, 0.0), -4.1, places=6)


class TestModeAndGate(OpenpilotTestCase):
  def _cp(self, fp=CAR.HONDA_ACCORD_9G_AU, oplong=True):
    CP = structs.CarParams()
    CP.brand, CP.carFingerprint, CP.openpilotLongitudinalControl = 'honda', fp, oplong
    return CP

  def test_this_car_with_openpilot_longitudinal_only(self):
    sp = custom.CarParamsSP.new_message().as_reader()
    self.assertTrue(lba.lead_brake_applies(self._cp(), sp))
    self.assertFalse(lba.lead_brake_applies(self._cp(CAR.HONDA_CIVIC), sp))
    self.assertFalse(lba.lead_brake_applies(self._cp(oplong=False), sp))
    stock = custom.CarParamsSP.new_message()
    stock.flags = HondaFlagsSP.ELESYS_STOCK_ACC.value
    self.assertFalse(lba.lead_brake_applies(self._cp(), stock.as_reader()))
    self.assertFalse(lba.lead_brake_applies(None, None))

  def test_the_param(self):
    params = Params()
    params.remove(lba.PARAM)
    self.assertEqual(lba.read_mode(), lba.MODE_SHADOW, "default 1: shadow")
    for m in (0, 1, 2):
      params.put(lba.PARAM, m, block=True)
      self.assertEqual(lba.read_mode(), m)
    self.assertEqual(lba.read_mode(SimpleNamespace(get=lambda *a, **k: 7)), lba.MODE_SHADOW)
    self.assertEqual(lba.read_mode(SimpleNamespace(get=mock.Mock(side_effect=RuntimeError))), lba.MODE_SHADOW)
    keys = {k.decode() if isinstance(k, bytes) else k for k in params.all_keys()}
    self.assertIn(lba.PARAM, keys)
    params.remove(lba.PARAM)

  def test_mode_0_and_other_cars_build_nothing(self):
    sp = custom.CarParamsSP.new_message().as_reader()
    self.assertIsNone(lba.lead_brake_planner(self._cp(), sp, mode=lba.MODE_OFF))
    self.assertIsNone(lba.lead_brake_planner(self._cp(CAR.HONDA_CIVIC), sp, mode=lba.MODE_LIVE))
    self.assertIsInstance(lba.lead_brake_planner(self._cp(), sp, mode=lba.MODE_SHADOW), lba.LeadBrakePlanner)

  def test_the_log_struct_and_service(self):
    from openpilot.cereal.services import SERVICE_LIST
    self.assertIn('customReserved13', SERVICE_LIST)
    msg = messaging.new_message('customReserved13')
    names = set(msg.customReserved13.schema.fieldnames)
    for f in ('mode', 'active', 'reason', 'blocked', 'trackId', 'aLeadK', 'aLeadTau', 'aRaw', 'jLead', 'aLeadProj',
              'aLeadTauProj', 'aTarget', 'aTargetBase', 'aTargetAssist', 'fcwBase', 'fcwAssist', 'compared', 'episodes'):
      self.assertIn(f, names)


# ---- through the real planner ----

class _SM(dict):
  def __init__(self, services):
    super().__init__(services)
    self.valid = dict.fromkeys(services, True)
    self.logMonoTime = dict.fromkeys(services, 0)
    self.updated = dict.fromkeys(services, True)
    self.recv_frame = dict.fromkeys(services, 1)

  def all_checks(self, service_list=None) -> bool:
    return True


def build_sm(ld, e2e=0.5, enabled=True, experimental=False):
  services = {}
  for service in ("vehicleParameters", "carStateSP", "liveMapDataSP", "gpsLocationExternal", "gpsLocation"):
    services[service] = getattr(messaging.new_message(service), service)
  rs = messaging.new_message('radarState')
  L = rs.radarState.leadOne
  for k in ('present', 'radar', 'radarTrackId', 'dRel', 'yRel', 'vRel', 'vLead', 'vLeadK', 'aLeadK', 'aLeadTau', 'modelProb'):
    setattr(L, k, getattr(ld, k))
  services['radarState'] = rs.radarState.as_reader()
  cs = messaging.new_message('carState')
  cs.carState.vEgo, cs.carState.aEgo = V0, 0.0
  cs.carState.vCruise = cs.carState.vCruiseCluster = 100.0
  services['carState'] = cs.carState.as_reader()
  ctl = messaging.new_message('controlsState')
  ctl.controlsState.longControlState = structs.CarControl.Actuators.LongControlState.pid if enabled else \
    structs.CarControl.Actuators.LongControlState.off
  services['controlsState'] = ctl.controlsState.as_reader()
  ss = messaging.new_message('selfdriveState')
  ss.selfdriveState.experimentalMode = experimental
  ss.selfdriveState.enabled = enabled
  ss.selfdriveState.personality = log.LongitudinalPersonality.aggressive
  services['selfdriveState'] = ss.selfdriveState.as_reader()
  cc = messaging.new_message('carControl')
  cc.carControl.enabled = enabled
  services['carControl'] = cc.carControl.as_reader()
  m = messaging.new_message('modelV2')
  m.modelV2.orientationRate.z = [0.01] * 33
  m.modelV2.velocity.x = [V0] * 33
  m.modelV2.position.x = [float(i) for i in range(33)]
  m.modelV2.action.desiredAcceleration = e2e
  services['modelV2'] = m.modelV2.as_reader()
  return _SM(services)


class _PM:
  def __init__(self):
    self.sent: dict = {}

  def send(self, service, msg):
    self.sent.setdefault(service, []).append(msg.as_reader() if hasattr(msg, 'as_reader') else msg)


def planner(mode, fingerprint=CAR.HONDA_ACCORD_9G_AU, cfg=None, flags=0):
  from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
  CP = structs.CarParams()
  CP.brand = 'honda'
  CP.carFingerprint = fingerprint
  CP.openpilotLongitudinalControl = True
  CP.steerRatio = 15.0
  CP.wheelbase = 2.7
  CP.longitudinalActuatorDelay = 0.5
  CP_SP = custom.CarParamsSP.new_message()
  CP_SP.flags = flags
  with mock.patch.object(lba, 'read_mode', return_value=mode):
    return LongitudinalPlanner(CP, CP_SP.as_reader(), init_v=V0)


def drive(pl, frames, publish=False, **kw):
  out, pm = [], _PM()
  for f in frames:
    sm = build_sm(f, **kw)
    pl.update(cast(Any, sm))
    out.append((float(pl.output_a_target), bool(pl.fcw), [float(x) for x in pl.a_desired_trajectory]))
    if publish:
      pl.publish(cast(Any, sm), pm)
  return out, pm


class TestPlanner(OpenpilotTestCase):
  def test_shadow_changes_nothing_and_logs_what_live_would_do(self):
    frames = braking()
    base, _ = drive(planner(lba.MODE_OFF), frames)
    pl = planner(lba.MODE_SHADOW)
    self.assertIsNotNone(pl.lead_brake)
    shadow, pm = drive(pl, frames, publish=True)
    self.assertEqual(shadow, base, "mode 1 is the plan without the assist, bit for bit")
    msgs = pm.sent['customReserved13']
    self.assertEqual(len(msgs), len(frames))
    lp = pm.sent['longitudinalPlan']
    for m, p in zip(msgs, lp, strict=True):
      self.assertEqual(m.customReserved13.mode, lba.MODE_SHADOW)
      self.assertEqual(m.customReserved13.aTarget, p.longitudinalPlan.aTarget)
    act = [m.customReserved13 for m in msgs if m.customReserved13.active]
    self.assertTrue(act, "the assist fired on a lead braking at -4")
    self.assertTrue(all(a.compared for a in act))
    self.assertTrue(all(a.aTargetBase == a.aTarget or a.aTargetBase <= a.aTarget for a in act))
    self.assertLess(min(a.aTargetAssist - a.aTargetBase for a in act), -0.1, "it logs a firmer plan")
    self.assertTrue(all(a.aLeadProj <= a.aLeadK + 1e-6 for a in act))
    self.assertGreaterEqual(msgs[-1].customReserved13.episodes, 1)
    # first active frame: the shadow's plan is exactly what mode 2 publishes on that frame
    live, _ = drive(planner(lba.MODE_LIVE), frames)
    k = next(i for i, m in enumerate(msgs) if m.customReserved13.active)
    self.assertAlmostEqual(msgs[k].customReserved13.aTargetAssist, live[k][0], delta=1e-4)
    self.assertEqual(live[:k], base[:k])

  def test_live_brakes_sooner(self):
    frames = braking(n=100)
    base, _ = drive(planner(lba.MODE_OFF), frames)
    live, _ = drive(planner(lba.MODE_LIVE), frames)
    tb = next(k for k, o in enumerate(base) if o[0] < -1.0)
    tl = next(k for k, o in enumerate(live) if o[0] < -1.0)
    self.assertLess(tl, tb)
    self.assertLessEqual(min(o[0] for o in live), min(o[0] for o in base) + 1e-6)

  def test_the_comparison_solve_reproduces_the_planners_own(self):
    # mode 1 with the projection turned into radarState itself: the second MPC must land exactly on the plan
    frames = braking()
    pl = planner(lba.MODE_SHADOW)
    lb = pl.lead_brake
    orig = lb.before_mpc

    def same(radar_state, *a, **k):
      out = orig(radar_state, *a, **k)
      if lb._alt is not None:
        lb._alt = radar_state
      return out
    lb.before_mpc = same
    checked = 0
    for f in frames:
      pl.update(cast(Any, build_sm(f)))
      if lb.compared:
        checked += 1
        self.assertAlmostEqual(lb.a_assist, lb.a_base, delta=1e-5)
        self.assertEqual(lb.fcw_assist, lb.fcw_base)
    self.assertGreater(checked, 5)

  def test_every_other_car_stock_acc_and_mode_0_build_nothing(self):
    self.assertIsNone(planner(lba.MODE_LIVE, CAR.HONDA_CIVIC).lead_brake)
    self.assertIsNone(planner(lba.MODE_LIVE, flags=HondaFlagsSP.ELESYS_STOCK_ACC.value).lead_brake)
    self.assertIsNone(planner(lba.MODE_OFF).lead_brake)
    pl = planner(lba.MODE_OFF)
    _, pm = drive(pl, braking(n=30), publish=True)
    self.assertNotIn('customReserved13', pm.sent)

  def test_a_failure_turns_it_off_and_leaves_the_plan_alone(self):
    frames = braking()
    base, _ = drive(planner(lba.MODE_OFF), frames)
    pl = planner(lba.MODE_LIVE)
    with mock.patch.object(lba.LeadBrakeAssist, 'update', side_effect=RuntimeError("boom")):
      out, _ = drive(pl, frames)
    self.assertTrue(pl.lead_brake._failed)
    self.assertEqual(out, base)

  def test_one_log_line_per_episode(self):
    frames = braking(n=100)
    pl = planner(lba.MODE_SHADOW)
    with mock.patch.object(lba.cloudlog, 'event') as ev:
      drive(pl, frames + [frames[-1]] * 30)
    calls = [c for c in ev.call_args_list if c.args and c.args[0] == lba.LOG_TAG]
    self.assertEqual(len(calls), 1)
    kw = calls[0].kwargs
    self.assertTrue(kw['error'])
    self.assertEqual(kw['mode'], lba.MODE_SHADOW)
    self.assertLess(kw['delta_min'], 0.0)
