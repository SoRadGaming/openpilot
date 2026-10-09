"""
FORK(HONDA_ACCORD_9G_AU): radard's clutter guard (elesys_radar_guard.py), on logged frames.

fixtures/elesys_radar_guard_frames.json.gz holds seven windows of this car's drives (15646e8515eda1a7/<route>): per
modelV2 frame, the radar points this tree's RadarInterface decodes from the logged bus-1 CAN at the true 1/64 m/s
REL_SPEED scale, whether they are a new radar update (the radar reports at 10 Hz, radard runs at 20 Hz), the logged
modelV2 leads and model speed, and the carState speed, in the order radard saw them. Each window starts about 2 s early
so radard's lead-prob filter and tracks are warm. The same frames go through radard twice: as HONDA_ACCORD_9G_AU (guard
on) and as HONDA_ACCORD, another Nidec Honda (guard off: upstream's radard exactly). The windows pin what the guard must
remove (10f near range, c8, the 07 speed spike) or keep (c0, 10f creeping, c9), and what it knowingly gives up (06 at
night, where it holds the plan instead of braking), so a later change to the thresholds shows up here either way.
"""
import gzip
import json
import os
import unittest
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, cast

from openpilot.cereal import log
from opendbc.car.structs import car
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.controls import radard
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc
from openpilot.sunnypilot.selfdrive.controls.lib import elesys_radar_guard as guard

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'elesys_radar_guard_frames.json.gz')
with gzip.open(FIXTURE, 'rt') as f:
  WINDOWS = json.load(f)

ELESYS = 'HONDA_ACCORD_9G_AU'
OTHER_HONDA = 'HONDA_ACCORD'


class _SubMaster(dict):
  def __init__(self):
    super().__init__()
    self.seen = defaultdict(bool)
    self.recv_frame = defaultdict(int)
    self.logMonoTime = defaultdict(int)

  def all_checks(self):
    return True


def _lead(x, x_std, y, y_std, v, v_std, a, prob):
  return SimpleNamespace(x=[x], xStd=[x_std], y=[y], yStd=[y_std], v=[v], vStd=[v_std], a=[a], prob=prob)


def _track(identifier, vLead, vLeadK=None, dRel=5.0):
  return SimpleNamespace(identifier=identifier, dRel=dRel, yRel=0.0, vLead=vLead, vLeadK=vLead if vLeadK is None else vLeadK)


class _Radar:
  """Feeds a guard as radard does: one radar update, then the same update again on the next radard frame."""
  def __init__(self, g):
    self.g = g
    self.frame = 0

  def update(self, tracks, v_ego=0.0):
    self.frame += 1
    for _ in range(2):
      self.g.update_tracks(tracks, self.frame, v_ego)


def car_params(fingerprint, brand='honda'):
  return car.CarParams.new_message(brand=brand, carFingerprint=fingerprint).as_reader()


def is_hold(r):
  """A held lead: the refused track at the car's speed and the MPC's desired distance for it."""
  return (r.present and r.radar and abs(r.v_lead - r.v_ego) < 1e-6 and
          abs(r.d - (guard.STOP_DISTANCE + guard.HOLD_T_FOLLOW * r.v_ego)) < 1e-6)


def replay(window, fingerprint=ELESYS):
  """radard over one window. One row per frame: the camera lead, radard's filtered prob for it, and leadOne."""
  w = WINDOWS[window]
  rd = radard.RadarD(car_params(fingerprint), SimpleNamespace(flags=0), w['radar_delay'])
  sm = _SubMaster()
  rows = []
  for n, fr in enumerate(w['frames']):
    sm['carState'] = SimpleNamespace(vEgo=fr['v_ego'])
    if fr['new_car_state']:
      sm.recv_frame['carState'] += 1
    if fr['new_radar']:
      sm.recv_frame['radarTracks'] += 1
    sm['modelV2'] = SimpleNamespace(velocity=SimpleNamespace(x=[fr['model_v_ego']]),
                                    leadsV3=[_lead(*ld) for ld in fr['leads']])
    sm.seen['modelV2'] = True
    sm.logMonoTime['modelV2'] = n + 1
    points = [SimpleNamespace(trackId=i, dRel=d, yRel=y, vRel=v) for i, d, y, v in fr['points']]
    rd.update(cast(Any, sm), SimpleNamespace(points=points, errors={}))
    lo = rd.radar_state.leadOne
    cam = fr['leads'][0]
    rows.append(SimpleNamespace(t=fr['t'], v_ego=fr['v_ego'], cam_d=cam[0] - radard.RADAR_TO_CAMERA, cam_v=cam[4],
                                cam_prob=rd.lead_prob_filters[0].x, present=lo.present, radar=lo.radar,
                                track=lo.radarTrackId, d=lo.dRel, v_lead=lo.vLead))
  return rows


def between(rows, lo, hi):
  out = [r for r in rows if lo <= r.t <= hi]
  assert len(out), (lo, hi)
  return out


class TestGate(unittest.TestCase):
  def test_on_for_honda_elesys_only(self):
    self.assertIsInstance(radard.RadarD(car_params(ELESYS), SimpleNamespace(flags=0)).clutter_guard, guard.ElesysRadarGuard)
    self.assertIsNone(radard.RadarD(car_params(OTHER_HONDA), SimpleNamespace(flags=0)).clutter_guard)
    self.assertIsNone(radard.RadarD(car_params('TOYOTA_COROLLA_TSS2', 'toyota'), SimpleNamespace(flags=0)).clutter_guard)
    self.assertIsNone(radard.RadarD(car_params(ELESYS, 'toyota'), SimpleNamespace(flags=0)).clutter_guard)

  def test_constants_are_radards_and_the_mpcs(self):
    self.assertEqual(guard.RADAR_TO_CAMERA, radard.RADAR_TO_CAMERA)
    self.assertEqual(guard.DT, DT_MDL)
    self.assertEqual(guard.STOP_DISTANCE, long_mpc.STOP_DISTANCE)
    personalities = log.LongitudinalPersonality.schema.enumerants.values()
    self.assertEqual(guard.HOLD_T_FOLLOW, min(long_mpc.get_T_FOLLOW(p) for p in personalities))

  def test_guard_off_is_upstream(self):
    # get_lead's default is upstream's path: the override and the match see every track
    lead = _lead(20.0 + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 8.0, 1.0, 0.0, 0.9)
    trk = radard.Track(1, 0.0, radard.KalmanParams(radard.DT_MDL))
    trk.update(20.0, 0.0, -8.0, 0.0)
    cp_sp = cast(Any, SimpleNamespace(flags=0))
    got = radard.get_lead(8.0, True, {1: trk}, lead, 8.0, 0.9, car_params(OTHER_HONDA), cp_sp)
    self.assertTrue(got['radar'])


class TestThresholds(unittest.TestCase):
  def test_speed_tolerance_grows_with_camera_range(self):
    for d, tol in [(5, 3.0), (20, 3.0), (40, 6.0), (60, 9.0), (67, 10.0), (120, 10.0)]:
      lead = _lead(d + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 0.0, 1.0, 0.0, 1.0)
      self.assertAlmostEqual(guard.speed_tolerance(lead), tol, places=6, msg=d)

  def test_only_slow_tracks_slower_than_the_camera_are_rejected(self):
    lead = _lead(10.0 + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 6.5, 1.0, 0.0, 1.0)
    self.assertFalse(guard.match_agrees(SimpleNamespace(vLead=0.2, dRel=10.0), lead))   # stationary, camera 6.3 faster
    self.assertTrue(guard.match_agrees(SimpleNamespace(vLead=3.6, dRel=10.0), lead))    # within 3 m/s
    self.assertTrue(guard.match_agrees(SimpleNamespace(vLead=3.0, dRel=10.0), lead))    # moving: upstream's
    slow_cam = _lead(10.0 + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 0.3, 1.0, 0.0, 1.0)
    self.assertTrue(guard.match_agrees(SimpleNamespace(vLead=2.9, dRel=10.0), slow_cam))  # faster than the camera

  def test_override_needs_a_confident_camera_lead_that_agrees(self):
    trk = _track(1, vLead=0.1)
    near_stopped = _lead(5.8 + radard.RADAR_TO_CAMERA, 0.3, 0.0, 0.3, 0.2, 0.1, 0.0, 1.0)
    far_moving = _lead(12.0 + radard.RADAR_TO_CAMERA, 0.5, 0.0, 0.3, 2.5, 0.3, 0.0, 1.0)
    g = guard.ElesysRadarGuard()
    _Radar(g).update({1: trk})
    self.assertFalse(g.override_confirmed(trk, near_stopped))  # no confident camera lead yet
    g.update_camera(0.9)
    self.assertTrue(g.override_confirmed(trk, near_stopped))
    self.assertFalse(g.override_confirmed(trk, far_moving))
    g.update_camera(0.3)
    self.assertFalse(g.override_confirmed(trk, near_stopped))

  def test_camera_confidence_has_hysteresis(self):
    # the prob of route 06 t=1303.4: one frame at 0.53-0.55 between frames under 0.45 must not let the override take a
    # stationary track. Once confident, the camera stays so down to radard's own 0.5, never below it
    g = guard.ElesysRadarGuard()
    for prob, confident in [(0.3, False), (0.53, False), (0.44, False), (0.56, True), (0.51, True), (0.53, True),
                            (0.5, False), (0.53, False), (0.55, False), (0.551, True), (0.2, False)]:
      g.update_camera(prob)
      self.assertEqual(g.camera_confident, confident, prob)


class TestMoving(unittest.TestCase):
  CAM = _lead(30.0 + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 8.0, 1.0, 0.0, 0.1)  # far, fast, not confident

  def test_counted_in_radar_updates_not_radard_frames(self):
    # radard runs at 20 Hz on a 10 Hz radar: a reading it sees twice is one update
    g = guard.ElesysRadarGuard()
    trk = _track(7, vLead=1.2)
    for _ in range(6):
      g.update_tracks({7: trk}, 1, 0.0)
    self.assertEqual(g.state[7].updates, 1)
    self.assertEqual(g.state[7].run_ahead, 1)
    radar = _Radar(g)
    radar.frame = 1
    radar.update({7: trk})
    self.assertEqual(g.state[7].run_ahead, 2)

  def test_a_track_seen_moving_latches_once_its_range_agrees(self):
    # a vehicle pulling away at 1.2 m/s with the car stopped: its range grows 0.12 m per radar update. It has read
    # 1 m/s for MOVING_UPDATES updates after the third, but its range history needs WORLD_SPAN (0.5 s) to confirm it
    g = guard.ElesysRadarGuard()
    radar = _Radar(g)
    trk = _track(7, vLead=1.2, vLeadK=1.2, dRel=5.0)
    for n in range(1, 7):
      trk.dRel = 5.0 + 0.12 * n
      radar.update({7: trk})
      self.assertEqual(g.override_confirmed(trk, self.CAM), n >= 6, n)
    self.assertEqual(g.moving, {7})
    trk.vLead = trk.vLeadK = 0.0
    radar.update({7: trk})
    radar.update({7: trk})
    self.assertTrue(g.override_confirmed(trk, self.CAM))  # a vehicle that stopped stays a vehicle
    radar.update({})
    radar.update({7: trk})
    self.assertFalse(g.override_confirmed(trk, self.CAM))  # a new track with the same id starts again

  def test_a_speed_spike_its_range_does_not_follow_does_not_latch(self):
    # route 07 t=1128.2-1128.8 (track 1796) and route 120 t=17.7 (track 74): a stationary return reads 2-3.9 m/s for
    # three to six radar updates while its range closes at the car's speed. No latch, and not moving now either
    g = guard.ElesysRadarGuard()
    radar = _Radar(g)
    trk = _track(74, vLead=0.1, vLeadK=0.1, dRel=12.0)
    for n, (v, vk) in enumerate([(0.1, 0.1)] * 6 + [(3.7, 1.7), (3.7, 2.6), (3.8, 3.3), (3.8, 3.7), (3.9, 4.1),
                                                     (3.9, 4.3), (0.2, 3.7), (0.2, 3.1), (0.2, 2.1)]):
      trk.vLead, trk.vLeadK, trk.dRel = v, vk, 12.0 - 0.27 * n  # the car at 2.7 m/s
      radar.update({74: trk}, v_ego=2.7)
      self.assertFalse(g.override_confirmed(trk, self.CAM), n)
    self.assertEqual(g.moving, set())

  def test_a_clearly_moving_new_track_counts_after_two_updates(self):
    # route c9 t=288.79: track 946 appears in the lane at 8.8 m doing 2.1 m/s. Too new for a range history, it is
    # taken on its speed once raw and filtered speed have both said so for MOVING_NOW_UPDATES radar updates
    g = guard.ElesysRadarGuard()
    radar = _Radar(g)
    cam = _lead(13.0 + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 7.0, 1.0, 0.0, 0.97)
    g.update_camera(0.97)
    trk = _track(946, vLead=2.1, vLeadK=2.1, dRel=8.8)
    radar.update({946: trk}, v_ego=3.9)
    self.assertFalse(g.override_confirmed(trk, cam))  # one update, and the camera's lead is 4 m on
    trk.dRel = 8.6
    radar.update({946: trk}, v_ego=3.9)
    self.assertTrue(g.override_confirmed(trk, cam))
    trk.vLead = 0.9
    radar.update({946: trk}, v_ego=3.9)
    self.assertFalse(g.override_confirmed(trk, cam))  # the raw speed fell under 1 m/s, and it is not latched

  def test_a_vehicle_coming_toward_the_car_is_not_moving(self):
    # Given up: only moving away counts. A track closing on the car in world terms (reversing, rolling back,
    # oncoming) is stationary to the guard, whatever its range history says
    g = guard.ElesysRadarGuard()
    radar = _Radar(g)
    trk = _track(5, vLead=-1.5, vLeadK=-1.5, dRel=10.0)
    for n in range(10):
      trk.dRel = 10.0 - 0.15 * n
      radar.update({5: trk})
    self.assertFalse(g.override_confirmed(trk, self.CAM))
    self.assertEqual(g.moving, set())


class TestHold(unittest.TestCase):
  def test_hold_lead_is_at_the_cars_speed_and_the_mpcs_distance(self):
    held = guard.hold_lead({'dRel': 14.2, 'yRel': 0.1, 'vRel': -2.9, 'vLead': 0.1, 'vLeadK': 0.0, 'aLeadK': -0.4,
                            'radar': True, 'radarTrackId': 2633, 'present': True}, 2.9)
    self.assertAlmostEqual(held['dRel'], long_mpc.STOP_DISTANCE + 1.25 * 2.9)
    self.assertEqual((held['vLead'], held['vLeadK'], held['vRel'], held['aLeadK']), (2.9, 2.9, 0.0, 0.0))
    self.assertEqual((held['radarTrackId'], held['present']), (2633, True))

  def _get_lead(self, updates, cam_prob):
    g = guard.ElesysRadarGuard()
    radar = _Radar(g)
    trk = radard.Track(3, 0.0, radard.KalmanParams(radard.DT_MDL))
    trk.update(8.0, 0.2, -3.0, 0.0)
    for _ in range(updates):
      radar.update({3: trk}, v_ego=3.0)
    cam = _lead(30.0 + radard.RADAR_TO_CAMERA, 1.0, 0.0, 0.5, 8.0, 1.0, 0.0, cam_prob)
    g.update_camera(cam_prob)
    cp_sp = cast(Any, SimpleNamespace(flags=0))
    return radard.get_lead(3.0, True, {3: trk}, cam, 3.0, cam_prob, car_params(ELESYS), cp_sp, clutter_guard=g)

  def test_a_refused_track_holds_the_plan_only_with_no_other_lead(self):
    got = self._get_lead(guard.HOLD_UPDATES, 0.1)  # stationary at 8 m, no camera: refused by the override
    self.assertTrue(got['present'] and got['radar'])
    self.assertEqual(got['radarTrackId'], 3)
    self.assertEqual((got['vLead'], got['aLeadK']), (3.0, 0.0))
    self.assertAlmostEqual(got['dRel'], guard.STOP_DISTANCE + guard.HOLD_T_FOLLOW * 3.0)
    self.assertFalse(self._get_lead(guard.HOLD_UPDATES - 1, 0.1)['present'])  # not seen long enough
    got = self._get_lead(guard.HOLD_UPDATES, 0.9)  # the camera's confident lead, 30 m on at 8 m/s, is the lead
    self.assertTrue(got['present'] and not got['radar'])
    self.assertAlmostEqual(got['vLead'], 8.0)


class TestLoggedFrames(unittest.TestCase):
  def test_10f_near_range_stationary_returns_at_walking_pace(self):
    # Route 10f t=2740.25-2745.55, 2.7-3.3 m/s: the camera's lead fades (filtered prob 0.12-0.58, at 3-13 m, under
    # 2.5 m/s) while near-range stationary returns close from 13 m to 3.7 m and jump back
    off = between(replay('near_range_10f', OTHER_HONDA), 2740.25, 2745.55)
    on = between(replay('near_range_10f'), 2740.25, 2745.55)
    self.assertTrue(all(r.radar and abs(r.v_lead) < 1.0 for r in off))
    self.assertGreater(sum(r.d < 6.0 for r in off), 40)  # upstream's low-speed override braking for them

    for r in on:
      self.assertTrue(r.present, r.t)
      if r.cam_prob <= guard.CAMERA_OFF:
        self.assertTrue(is_hold(r), r.t)  # no confident camera lead: the returns only hold the plan
      elif r.radar and not is_hold(r):
        self.assertLess(abs(r.v_lead), 1.0, r.t)             # none of these returns is moving
        self.assertLessEqual(r.cam_v - r.v_lead, 3.0, r.t)  # a slow camera lead, which the track agrees with
        self.assertGreater(r.d, r.cam_d, r.t)                # and no closer than it
    radar_on = [r for r in on if r.radar and not is_hold(r)]
    self.assertLessEqual(len(radar_on), 5)
    self.assertEqual({r.track for r in radar_on}, {6539})   # t=2741.11-2741.31: 7.5-7.9 m, camera at 3-4 m doing 1.4-2

  def test_c8_false_fcw_frames(self):
    # Route c8 t=2320.39-2321.14, 6.7-7.3 m/s, disengaged: returns 5743/5744/5746 at 11 -> 5 m, stationary, matched to a
    # camera lead at 5-9 m doing 5.3-6.8 m/s. The car drove through them; replayed, the planner raised a false FCW
    off = between(replay('false_fcw_c8', OTHER_HONDA), 2320.39, 2321.14)
    on = between(replay('false_fcw_c8'), 2320.39, 2321.14)
    self.assertTrue(all(r.radar and r.track in (5743, 5744, 5746) and abs(r.v_lead) < 0.5 for r in off))
    for r in on:
      self.assertTrue(r.present and not r.radar, r.t)  # the camera's lead instead
      self.assertGreater(r.v_lead, 5.0, r.t)
      self.assertAlmostEqual(r.d, r.cam_d, places=3)

    # t=2321.19 on: a real car (track 5751, 7.8-8.0 m/s) cuts in at 8 m; both match it
    on_after = between(replay('false_fcw_c8'), 2321.19, 2322.5)
    off_after = between(replay('false_fcw_c8', OTHER_HONDA), 2321.19, 2322.5)
    for a, b in zip(on_after, off_after, strict=True):
      self.assertEqual((a.radar, a.track, a.v_lead), (b.radar, b.track, b.v_lead))
      self.assertEqual(a.track, 5751)
      self.assertTrue(7.5 < a.v_lead < 9.0, a.t)

  def test_c0_stop_behind_a_stopped_queue(self):
    # Route c0 t=252-265.2, engaged: closing from 15 m/s on a stopped queue, then stopping behind its last car.
    # Track 809 is that car, at 117 m to 5.1 m, -0.2 to +0.4 m/s. The camera reads it at 8-12 m/s beyond 65 m and
    # 0.1-0.4 m/s from 50 m in; the guard keeps every frame upstream matched to it, from 4 m/s down through the
    # low-speed override as well
    on, off = replay('stopped_queue_c0'), replay('stopped_queue_c0', OTHER_HONDA)
    kept = [(a, b) for a, b in zip(on, off, strict=True) if b.radar and b.track == 809 and a.t <= 265.2]
    self.assertGreater(len(kept), 230)
    for a, b in kept:
      self.assertEqual((a.radar, a.track, a.d, a.v_lead), (b.radar, b.track, b.d, b.v_lead), a.t)
      self.assertLess(abs(a.v_lead), 0.5, a.t)
    self.assertGreater(sum(a.v_ego < 4.0 for a, _ in kept), 50)
    self.assertTrue(any(a.cam_v - a.v_lead > 8.0 for a, _ in kept))  # the camera still lagging at range
    stop = between(on, 264.9, 265.2)
    self.assertTrue(all(r.radar and r.track == 809 and r.d < 5.5 and abs(r.d - r.cam_d) < 1.0 for r in stop))
    # every frame but one is identical. At t=253.96 the camera reads 10.1 m/s at 87 m, so track 809 is out of the
    # guard's 10 m/s (upstream's vel_sane rejected it too and took the camera); with it out, another stationary track
    # of the queue, at 68.8 m, is radard's best match and passes upstream's checks
    self.assertLessEqual(sum((a.radar, a.track, a.d, a.v_lead) != (b.radar, b.track, b.d, b.v_lead)
                             for a, b in zip(on, off, strict=True)), 1)

  def test_10f_vehicle_creeping_close_ahead(self):
    # Route 10f t=2756.4-2757.5, daylight, 3.0-4.0 m/s: a vehicle creeps 4-5 m ahead (tracks 6568, 6566, then 6567,
    # rising from 0.2 to 1.4 m/s) and the camera rates it 0.26-0.48. Once a track has read 1 m/s or more for
    # MOVING_UPDATES radar updates and its range agrees it is a vehicle, and the low-speed override takes it with no
    # camera, exactly as upstream does
    on = replay('creeping_10f')
    off = replay('creeping_10f', OTHER_HONDA)
    taken = [(a, b) for a, b in zip(on, off, strict=True) if 2757.25 <= a.t <= 2757.47]
    self.assertGreaterEqual(len(taken), 4)
    for a, b in taken:
      self.assertLess(a.cam_prob, 0.5, a.t)  # the camera is not confident: this is the moving-track path
      self.assertTrue(a.radar and a.track == 6567 and a.v_lead > 1.0 and a.d < 4.5, a.t)
      self.assertEqual((a.radar, a.track, a.d, a.v_lead), (b.radar, b.track, b.d, b.v_lead), a.t)
    # Given up: before then the tracks read 0.2-1.2 m/s and count as stationary, so with the camera unsure they only
    # hold the plan (upstream brakes for tracks 6568, 6566 and 6567 here)
    for r in between(on, 2756.40, 2757.21):
      self.assertTrue(is_hold(r) and r.track in (6566, 6567, 6568), r.t)

  def test_c9_car_in_lane_closer_than_the_camera_lead(self):
    # Route c9 t=288.79-289.39, 3.8-3.9 m/s: track 946 appears in the lane at 8.8 m doing 2.1 m/s while the camera is
    # sure (prob 0.93-0.97) of a car 9-20 m ahead doing 6-9 m/s. The driver braked at t=289.29. Moving, so the
    # override takes it as upstream does, although the camera's lead is elsewhere: from its second radar update
    on = between(replay('cut_in_c9'), 288.79, 289.39)
    off = between(replay('cut_in_c9', OTHER_HONDA), 288.79, 289.39)
    for a, b in zip(on, off, strict=True):
      self.assertTrue(b.radar and b.track == 946 and 2.0 < b.v_lead < 2.2 and b.d < 9.0, b.t)
      self.assertGreater(a.cam_prob, 0.9, a.t)
      if a.t < 288.87:
        self.assertTrue(a.present and not a.radar, a.t)  # its first update: the camera's lead
      else:
        self.assertEqual((a.radar, a.track, a.d, a.v_lead), (b.radar, b.track, b.d, b.v_lead), a.t)
        self.assertGreater(a.cam_d - a.d, 0.8, a.t)

  def test_07_speed_spike_on_a_stationary_return(self):
    # Route 07 t=1128.23-1128.78, 2.6 m/s, disengaged: a new return (track 1796) at 7-9 m reads 3.7-3.9 m/s for six
    # radar updates while its range closes at the car's speed; the car later drove through its point. Upstream takes
    # it (and, once it reads 0.2 m/s again, brakes for it to -3.5 in the plan replay). The guard never counts it as
    # moving: at most it holds the plan
    on = between(replay('speed_spike_07'), 1128.2, 1129.6)
    off = between(replay('speed_spike_07', OTHER_HONDA), 1128.2, 1129.6)
    self.assertTrue(any(b.radar and b.track in (1796, 1797) and b.v_lead > 3.5 for b in off))
    self.assertTrue(all(b.radar and b.track in (1796, 1797) for b in off))
    for a in on:
      self.assertTrue(is_hold(a), a.t)

  def test_06_known_give_up_stopped_vehicle_at_night(self):
    # Route 06 t=1306.35-1309.30, engaged, at night, 2.6-3.4 m/s: something wide and stopped in the path (tracks 2633,
    # 2634, 2650, 2652; 20 m closing to 10 m) that only the radar sees; the camera reads junk at prob 0.04-0.45.
    # Upstream brakes for it. The guard cannot tell it from clutter (stationary, no confident camera lead), so it
    # does not brake for it: it holds the plan (the plan replay: -0.5 to 0.0 where the first guard went to +1.0) until
    # track 2652 starts moving off at t=1309.75; the driver stopped about 6 m behind it. This is the documented give-up
    # (CAR doc 5.3): if a change brings the radar's stop back here, check that it does not bring back route 10f's
    # near-range clutter too
    on = between(replay('night_stop_06'), 1306.35, 1309.30)
    off = between(replay('night_stop_06', OTHER_HONDA), 1306.35, 1309.30)
    for a, b in zip(on, off, strict=True):
      self.assertTrue(is_hold(a) and a.track in (2633, 2650, 2652), a.t)
      self.assertLess(a.cam_prob, guard.CAMERA_ON, a.t)
      self.assertTrue(b.radar and b.track in (2633, 2650, 2652) and abs(b.v_lead) < 1.0, b.t)
    # once 2652 moves, the guard follows it like upstream
    for a in between(replay('night_stop_06'), 1309.75, 1310.80):
      self.assertTrue(a.radar and a.track == 2652 and a.v_lead > 1.4, a.t)


if __name__ == '__main__':
  unittest.main()
