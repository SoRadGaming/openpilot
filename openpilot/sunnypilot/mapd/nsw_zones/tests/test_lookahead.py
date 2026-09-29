"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the look-ahead (speedLimitAhead / speedLimitAheadDistance): the next change of the published
limit along the matched line and a unique continuation, school zones active at the estimated arrival time, and
the dead-reckoned path in a tunnel.

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import unittest

from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import ALWAYS, T_IN, T_OUT, Car, MiniIndex, drive, feat, school_rec, unix_sydney

FEATS = [
  # 60 then 80 (x=500), then a Y junction at x=1500 with 70 and 90 branches (no unique continuation)
  feat([(0, 0), (500, 0)], 60, 'Ordinary Permanent'),
  feat([(500, 0), (1500, 0)], 80, 'Ordinary Permanent'),
  feat([(1500, 0), (2000, 50)], 70),
  feat([(1500, 0), (2000, -50)], 90, 'Ordinary Permanent'),
  # school zone on a 60 road at x 400..500
  feat([(0, -400), (1000, -400)], 60, 'Ordinary Permanent'),
  feat([(400, -400), (500, -400)], 40, 'School'),
  # a 90 road with an 80 Variable ramp leaving it at x=500 at 8 deg: not co-located, not "ahead" on the 90
  feat([(0, -800), (1500, -800)], 90, 'Ordinary Permanent', 'One Way'),
  feat([(500, -800), (1000, -730)], 80, 'Variable', 'One Way'),
  # a tunnel: Variable 90 then a 70 after its portal (x=3000)
  feat([(0, -1200), (3000, -1200)], 90, 'Variable', 'One Way'),
  feat([(3000, -1200), (4000, -1200)], 70, 'Ordinary Permanent'),
]
SCHOOLS = [school_rec(88, 'LOOKAHEAD SCHOOL', 380, 520, -420, -380)]


class TestLookahead(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.mi = MiniIndex(FEATS, SCHOOLS)

  @classmethod
  def tearDownClass(cls):
    cls.mi.close()

  def m(self, **kw):
    return nz.Matcher(self.mi.path, calendar=ALWAYS, **kw)

  def test_next_zone_on_the_continuation(self):
    m = self.m()
    r = drive(m, [(280, 1), (300, 1)], 90.0, speed=20.0, unix=T_OUT, lookahead=True)[-1]
    self.assertEqual(r['ahead_kph'], 80)
    self.assertAlmostEqual(r['ahead_dist_m'], 200.0, delta=21.0)
    self.assertEqual(r['ahead_kind'], 'zone')

  def test_stops_at_an_ambiguous_junction(self):
    m = self.m()
    drive(m, [(900, 1), (920, 1)], 90.0, speed=20.0, unix=T_OUT)
    la = m.lookahead(1500)
    self.assertIsNone(la['ahead_kph'])
    self.assertTrue(('different limits continue') in (la['ahead_stop']))
    self.assertLessEqual(la['ahead_path_m'], 600.0)

  def test_horizon_follows_speed(self):
    m = self.m()
    self.assertEqual(m._horizon(0.0), 150.0)
    self.assertEqual(m._horizon(20.0), 300.0)
    self.assertEqual(m._horizon(100.0), 1000.0)
    drive(m, [(40, 1), (60, 1)], 90.0, speed=5.0, unix=T_OUT)  # 75 m horizon -> 150 m minimum: 80 is 440 m away
    self.assertIsNone(m.lookahead()['ahead_kph'])

  def test_school_ahead_active_at_arrival(self):
    m = self.m()
    r = drive(m, [(180, -399), (200, -399)], 90.0, speed=15.0, unix=T_IN, lookahead=True)[-1]  # horizon 225 m
    self.assertEqual((r['ahead_kind'], r['ahead_kph']), ('school', 40))
    self.assertAlmostEqual(r['ahead_dist_m'], 200.0, delta=11.0)
    self.assertEqual(r['ahead_school_name'], 'LOOKAHEAD SCHOOL')

  def test_school_ahead_inactive_at_arrival(self):
    # 09:29:30 now, 200 m at 5 m/s: arrival 09:30:10, after the 09:30 end of the morning window
    m = self.m()
    t = unix_sydney(2026, 10, 13, 9, 29, 30)
    drive(m, [(190, -399), (200, -399)], 90.0, speed=5.0, unix=t - 1)
    la = m.lookahead(400, unix_time=t)
    self.assertNotEqual(la['ahead_kind'], 'school')

  def test_a_branch_starting_here_is_not_ahead_on_the_main_road(self):
    m = self.m()
    drive(m, [(280, -799), (300, -799)], 90.0, speed=25.0, unix=T_OUT)
    la = m.lookahead(1000)
    self.assertIsNone(la['ahead_kph'], la)

  def test_no_limit_no_look_ahead(self):
    m = self.m()
    r = drive(m, [(5000, 5000)], 90.0, unix=T_OUT, lookahead=True)[-1]
    self.assertIsNone(r['ahead_kph'])
    self.assertEqual(r['ahead_stop'], 'no limit published')

  def test_look_ahead_while_dead_reckoning(self):
    m = self.m()
    car = Car(m, [(-200, -1200), (3500, -1200)], speed=25.0, lookahead=True)
    rs = car.run(gps=lambda s: s < 400)
    d = [r for r in rs if r['state'] == 'dead_reckoning' and 2700 <= r['_x'] <= 2950]  # horizon 15 s x 25 m/s
    self.assertTrue(d)
    for r in d:
      self.assertEqual(r['ahead_kph'], 70, r['ahead_stop'])
      self.assertAlmostEqual(r['ahead_dist_m'], 3000 - r['_x'], delta=35.0)


if __name__ == '__main__':
  unittest.main()
