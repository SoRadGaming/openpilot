"""
FORK(HONDA_ACCORD_9G_AU): radard's clutter guard (elesys_radar_guard.py), on logged frames.

fixtures/elesys_radar_guard_frames.json.gz holds three windows of this car's drives (15646e8515eda1a7/<route>): per
modelV2 frame, the radar points this tree's RadarInterface decodes from the logged bus-1 CAN at the true 1/64 m/s
REL_SPEED scale, the logged modelV2 leads and model speed, and the carState speed, in the order radard saw them. Each
window starts about 2 s early so radard's lead-prob filter and tracks are warm. The same frames go through radard twice:
as HONDA_ACCORD_9G_AU (guard on) and as HONDA_ACCORD, another Nidec Honda (guard off: upstream's radard exactly).
"""
import gzip
import json
import os
import unittest
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, cast

from opendbc.car.structs import car
from openpilot.selfdrive.controls import radard
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


def car_params(fingerprint, brand='honda'):
  return car.CarParams.new_message(brand=brand, carFingerprint=fingerprint).as_reader()


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
    self.assertTrue(radard.RadarD(car_params(ELESYS), SimpleNamespace(flags=0)).clutter_guard)
    self.assertFalse(radard.RadarD(car_params(OTHER_HONDA), SimpleNamespace(flags=0)).clutter_guard)
    self.assertFalse(radard.RadarD(car_params('TOYOTA_COROLLA_TSS2', 'toyota'), SimpleNamespace(flags=0)).clutter_guard)
    self.assertFalse(radard.RadarD(car_params(ELESYS, 'toyota'), SimpleNamespace(flags=0)).clutter_guard)

  def test_radar_to_camera_is_radards(self):
    self.assertEqual(guard.RADAR_TO_CAMERA, radard.RADAR_TO_CAMERA)

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
    trk = SimpleNamespace(dRel=5.0, vLead=0.1)
    near_stopped = _lead(5.8 + radard.RADAR_TO_CAMERA, 0.3, 0.0, 0.3, 0.2, 0.1, 0.0, 1.0)
    far_moving = _lead(12.0 + radard.RADAR_TO_CAMERA, 0.5, 0.0, 0.3, 2.5, 0.3, 0.0, 1.0)
    self.assertTrue(guard.override_confirmed(trk, near_stopped, 0.9))
    self.assertFalse(guard.override_confirmed(trk, near_stopped, 0.5))  # radard's own "> 0.5"
    self.assertFalse(guard.override_confirmed(trk, far_moving, 0.9))


class TestLoggedFrames(unittest.TestCase):
  def test_10f_near_range_stationary_returns_at_walking_pace(self):
    # Route 10f t=2740.25-2745.55, 2.7-3.3 m/s: the camera's lead fades (filtered prob 0.12-0.58, at 3-13 m, under
    # 2.5 m/s) while near-range stationary returns close from 13 m to 3.7 m and jump back
    off = between(replay('near_range_10f', OTHER_HONDA), 2740.25, 2745.55)
    on = between(replay('near_range_10f'), 2740.25, 2745.55)
    self.assertTrue(all(r.radar and abs(r.v_lead) < 1.0 for r in off))
    self.assertGreater(sum(r.d < 6.0 for r in off), 40)  # upstream's low-speed override braking for them

    for r in on:
      if r.cam_prob <= guard.CAMERA_PROB:
        self.assertFalse(r.present, r.t)  # no confident camera lead, no lead: the override needs the camera
      elif r.radar:
        self.assertLessEqual(r.cam_v - r.v_lead, 3.0, r.t)  # a slow camera lead, which the track agrees with
        self.assertGreater(r.d, r.cam_d, r.t)                # and no closer than it
    radar_on = [r for r in on if r.radar]
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


if __name__ == '__main__':
  unittest.main()
