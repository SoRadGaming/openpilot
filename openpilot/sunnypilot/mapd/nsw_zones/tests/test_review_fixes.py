"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): regressions from the go-live review (2026-09-29).
  * a School line on the street above a tunnel is not the tunnel's, at the geometry seen at St Mary's (Concord): the
    tunnel line at 116 deg, the School line at 101 deg 0.6 m from it, its own 50 street 0.16 m from the School line;
  * dead reckoning never publishes, or holds after it ends, a value above every branch still contending;
  * the Variable-higher rule while matched: a Variable line that only branches off here is not on top, a Variable
    zone drawn as two lines end to end is, and without a heading a One Way line is not (unless already followed).

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import math
import unittest

from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import ALWAYS, T_IN, T_OUT, Car, MiniIndex, drive, feat, latlon, school_rec


def along(b_deg, s, x0=0.0, y0=0.0):
  b = math.radians(b_deg)
  return (x0 + s * math.sin(b), y0 + s * math.cos(b))


def left(b_deg, d, p):
  b = math.radians(b_deg)
  return (p[0] - d * math.cos(b), p[1] + d * math.sin(b))


TUN_B, ST_B = 116.0, 101.0
# the tunnel (Variable 90 One Way on a Permanent 80, as TfNSW draws the M4 East), a school street crossing over it
C1 = along(TUN_B, 1500.0)  # where the School line crosses the tunnel line in plan
SCH1 = [along(ST_B, -200.0, *C1), along(ST_B, 200.0, *C1)]
ST1 = [left(ST_B, 0.16, along(ST_B, -600.0, *C1)), left(ST_B, 0.16, along(ST_B, 600.0, *C1))]
# the same crossing over a plain (non-Variable) 80 road that is matched with GPS
O2 = (0.0, -20000.0)
C2 = along(TUN_B, 500.0, *O2)
SCH2 = [along(ST_B, -200.0, *C2), along(ST_B, 200.0, *C2)]
ST2 = [left(ST_B, 0.16, along(ST_B, -600.0, *C2)), left(ST_B, 0.16, along(ST_B, 600.0, *C2))]

FEATS = [
  feat([along(TUN_B, -3000.0), along(TUN_B, 3000.0)], 90, 'Variable', 'One Way'),
  feat([along(TUN_B, -3000.0), along(TUN_B, 3000.0)], 80, 'Ordinary Permanent'),
  feat(ST1, 50),
  feat(SCH1, 40, 'School'),
  feat([along(TUN_B, -1000.0, *O2), along(TUN_B, 1500.0, *O2)], 80, 'Ordinary Permanent'),
  feat(ST2, 50),
  feat(SCH2, 40, 'School'),
  # a Permanent 80 road with a Variable 90 (One Way, eastbound) branching off it at x=1000 at 8 deg (within 1.5 m of
  # the road for its first 10.7 m)
  feat([(0, -40000), (2000, -40000)], 80, 'Ordinary Permanent'),
  feat([(1000, -40000), along(82.0, 600.0, 1000, -40000)], 90, 'Variable', 'One Way'),
  # a Permanent 80 road with a Variable 90 drawn on top as TWO lines meeting end to end at x=1000
  feat([(0, -45000), (2000, -45000)], 80, 'Ordinary Permanent'),
  feat([(100, -45000), (1000, -45000)], 90, 'Variable', 'One Way'),
  feat([(1000, -45000), (1900, -45000)], 90, 'Variable', 'One Way'),
]


def box(p0, p1, pad=30.0):
  return min(p0[0], p1[0]) - pad, max(p0[0], p1[0]) + pad, min(p0[1], p1[1]) - pad, max(p0[1], p1[1]) + pad


SCHOOLS = [
  school_rec(601, 'SCHOOL OVER THE TUNNEL', *box(*SCH1)),
  school_rec(602, 'SCHOOL OVER THE ROAD', *box(*SCH2)),
]


class TestReviewFixes(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.mi = MiniIndex(FEATS, SCHOOLS)

  @classmethod
  def tearDownClass(cls):
    cls.mi.close()

  def m(self, **kw):
    return nz.Matcher(self.mi.path, calendar=kw.pop('calendar', ALWAYS), **kw)

  # ---- St Mary's (Concord): the School line 0.6 m from the tunnel line, its own street 0.16 m from it
  def test_school_line_closer_to_its_own_street_is_not_the_roads(self):
    """Matched with GPS on a plain 80 road: at the points where the School line is 0.6 m away (0.16 m from its own
    street) the school zone is the street's. The old rule ignored everything under 1 m and published 40."""
    a = 0.6 / math.sin(math.radians(TUN_B - ST_B))  # along the road from the crossing
    for sgn in (-1.0, 1.0):
      m = self.m()
      pts = [along(TUN_B, 500.0 + sgn * a + k, *O2) for k in (-60.0, -40.0, -20.0, 0.0)]
      rs = drive(m, pts, TUN_B, speed=20.0, unix=T_IN)
      r = rs[-1]
      self.assertEqual(r['state'], 'matched', r['reason'])
      self.assertEqual((r['limit_kph'], r['school_state']), (80, 'none'), (sgn, r['reason']))
    # ... while a car on the street gets it
    m = self.m()
    rs = drive(m, [along(ST_B, s, *left(ST_B, 0.16, C2)) for s in (-60.0, -40.0, -20.0, 0.0)], ST_B, speed=12.0, unix=T_IN)
    self.assertEqual((rs[-1]['limit_kph'], rs[-1]['school_state']), (40, 'active'))

  def test_school_over_the_tunnel_is_never_applied_while_dead_reckoning(self):
    """Through the tunnel at 1 Hz with GPS lost, over every tick position near the crossing (the verifier's point:
    the core's fix held only where its own ticks happened to land)."""
    for offset in range(0, 22, 3):  # shifts where the 1 Hz ticks land relative to the crossing
      m = self.m()
      path = [along(TUN_B, -2800.0 + offset), along(TUN_B, 2800.0)]
      rs = Car(m, path, speed=22.0, unix=T_IN).run(gps=lambda s: s < 400)
      d = [r for r in rs if r['state'] == 'dead_reckoning']
      self.assertTrue(d)
      self.assertNotIn(40, [r['limit_kph'] for r in d], offset)
      self.assertTrue(all(r['limit_kph'] == 90 for r in d), (offset, sorted({r['limit_kph'] for r in d}, key=str)))

  # ---- the Variable-higher rule while matched
  def test_a_variable_line_branching_off_is_not_on_top(self):
    # never its 90 (further on the branch is a different-limit line close by: withheld, as at any junction)
    for x0 in (900, 901, 902, 903, 904):
      m = self.m()
      rs = drive(m, [(x, -40000) for x in range(x0, 1200, 5)], 90.0, speed=20.0, dt_s=0.25, unix=T_OUT)
      self.assertNotIn(90, [r['limit_kph'] for r in rs], x0)
      self.assertTrue(all(r['limit_kph'] == 80 for r, x in zip(rs, range(x0, 1200, 5), strict=False) if x <= 1012), x0)

  def test_a_variable_zone_drawn_as_two_lines_does_not_flicker(self):
    m = self.m()
    rs = drive(m, [(x, -45000) for x in range(600, 1500, 5)], 90.0, speed=20.0, dt_s=0.25, unix=T_OUT)
    self.assertTrue(all(r['limit_kph'] == 90 for r in rs), [(600 + 5 * i, r['limit_kph']) for i, r in enumerate(rs) if r['limit_kph'] != 90])

  def test_without_a_heading_a_one_way_variable_is_not_on_top(self):
    # crawling westbound (below min_heading_speed), against the One Way Variable 90: the Permanent 80
    m = self.m()
    rs = drive(m, [(x, -45000) for x in range(800, 700, -2)], 270.0, speed=1.0, unix=T_OUT)
    self.assertTrue(all(r['limit_kph'] == 80 for r in rs if r['limit_kph'] is not None), [r['limit_kph'] for r in rs])
    # ... but a Variable line already being followed (picked at speed) is kept through a crawl
    m = self.m()
    drive(m, [(x, -45000) for x in range(400, 500, 20)], 90.0, speed=20.0, unix=T_OUT)
    rs = drive(m, [(x, -45000) for x in range(500, 520, 2)], 90.0, speed=1.0, t0=5.0, unix=T_OUT)
    self.assertTrue(all(r['limit_kph'] == 90 for r in rs), [r['limit_kph'] for r in rs])


class TestDeadReckoningEnd(unittest.TestCase):
  """What the car gets once dead reckoning gives up while GPS is still lost."""

  def test_ended_state_holds_the_last_published_value(self):
    feats = [feat([(0, 0), (5000, 0)], 90, 'Variable', 'One Way'), feat([(0, 0), (5000, 0)], 80, 'Ordinary Permanent')]
    with MiniIndex(feats) as mi:
      m = nz.Matcher(mi.path, calendar=ALWAYS, freeze_max_m=1000.0)
      rs = Car(m, [(0, 0), (4900, 0)], speed=25.0).run(gps=lambda s: s < 300)
      ended = [r for r in rs if r['state'] == 'dr_ended']
      self.assertTrue(ended)
      self.assertTrue(all(r['limit_kph'] == 90 and r['state_code'] == 7 for r in ended))
      self.assertTrue(all(r['ahead_kph'] is None for r in ended))
      # GPS back: matched again
      lat, lon = latlon(4950, 0)
      r = m.update(lat, lon, 90.0, 25.0, True, 3.0, T_OUT + 500, mono_time=500.0)
      self.assertEqual((r['state'], r['limit_kph']), ('matched', 90))


if __name__ == '__main__':
  unittest.main()
