"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the owner's rules (2026-09-29) and the result contract the integration layer codes against.
  * a Variable line drawn on a static line: publish the HIGHER value (the lower one is a peak-time reduction);
  * on-ramps keep TfNSW's value (no special ramp rule);
  * update() always returns every key in RESULT_KEYS; state / school codes map to the cereal enums.

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import unittest

from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import ALWAYS, NEVER, T_IN, T_OUT, Car, MiniIndex, drive, feat, school_rec

FEATS = [
  # Variable 90 (One Way, eastbound) on a two-way Permanent 80, x 200..800 (the M4 East portals)
  feat([(0, 0), (1000, 0)], 80, 'Ordinary Permanent'),
  feat([(200, 0), (800, 0)], 90, 'Variable', 'One Way'),
  # Variable 80 (a peak-time reduction) on a Permanent 90, two-way Variable
  feat([(0, -300), (1000, -300)], 90, 'Ordinary Permanent'),
  feat([(200, -300), (800, -300)], 80, 'Variable'),
  # a motorway 110 with an on-ramp (TfNSW keeps the feeding road's 70 on the ramp until the merge)
  feat([(0, -600), (2000, -600)], 110, 'Ordinary Permanent', 'One Way'),
  feat([(500, -700), (800, -640), (1000, -606)], 70, 'Ordinary Permanent', 'One Way'),
  # school zone on a 60 road (x 400..600)
  feat([(0, -900), (1000, -900)], 60, 'Ordinary Permanent'),
  feat([(400, -900), (600, -900)], 40, 'School'),
]
SCHOOLS = [school_rec(77, 'OWNER TEST SCHOOL', 380, 620, -920, -880)]


class TestOwnerRules(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.mi = MiniIndex(FEATS, SCHOOLS)

  @classmethod
  def tearDownClass(cls):
    cls.mi.close()

  def m(self, **kw):
    return nz.Matcher(self.mi.path, calendar=kw.pop('calendar', ALWAYS), **kw)

  def test_variable_over_static_publishes_the_higher_value(self):
    m = self.m()
    rs = drive(m, [(x, 1) for x in range(300, 700, 20)], 90.0, unix=T_OUT)
    self.assertTrue(all(r['limit_kph'] == 90 for r in rs), [r['limit_kph'] for r in rs])
    self.assertEqual(rs[-1]['colocated'], 'resolved')
    self.assertTrue(rs[-1]['variable'])

  def test_lower_variable_is_a_peak_reduction(self):
    for heading, xs in ((90.0, range(300, 700, 20)), (270.0, range(700, 300, -20))):
      m = self.m()
      rs = drive(m, [(x, -299) for x in xs], heading, unix=T_OUT)
      self.assertTrue(all(r['limit_kph'] == 90 for r in rs), (heading, [r['limit_kph'] for r in rs]))
      self.assertTrue(all(r['state'] in ('matched', 'hysteresis') for r in rs))

  def test_one_way_variable_does_not_apply_against_its_direction(self):
    m = self.m()
    rs = drive(m, [(x, 1) for x in range(700, 300, -20)], 270.0, unix=T_OUT)
    self.assertTrue(all(r['limit_kph'] == 80 for r in rs), [r['limit_kph'] for r in rs])

  def test_on_ramp_keeps_tfnsw_value(self):
    m = self.m()
    rs = drive(m, [(600, -680), (650, -670), (700, -660), (750, -650)], 78.0, speed=20.0, unix=T_OUT)
    self.assertEqual(rs[-1]['limit_kph'], 70)
    m.reset()
    rs = drive(m, [(1300, -600), (1350, -600), (1400, -600)], 90.0, speed=25.0, unix=T_OUT)
    self.assertEqual(rs[-1]['limit_kph'], 110)

  def test_look_ahead_reports_the_variable_value(self):
    m = self.m()
    drive(m, [(40, 1), (60, 1)], 90.0, speed=20.0, unix=T_OUT)
    la = m.lookahead(500)
    self.assertEqual(la['ahead_kph'], 90)
    self.assertAlmostEqual(la['ahead_dist_m'], 140.0, delta=21.0)

  # ---- the result contract
  def test_every_result_has_every_key(self):
    m = self.m()
    rs = []
    rs += drive(m, [(100, 1), (120, 1)], 90.0, unix=T_IN)  # matched
    rs += drive(m, [(5000, 5000)], 90.0, t0=2.0)  # no NSW line
    m.reset()
    rs.append(m.update(None, None, None, 15.0, False, None, T_IN, mono_time=3.0))  # gps lost, nothing to hold
    m.reset()
    rs += drive(m, [(100, 1), (120, 1)], 90.0, unix=T_IN)
    rs.append(m.update(None, None, None, 15.0, False, None, T_IN + 2, mono_time=2.0, yaw_deg=90.0))  # dead reckoning
    for lookahead in (True, False):
      rs.append(m.update(*nz_latlon(140, 1), 90.0, 15.0, True, 3.0, T_IN + 3, mono_time=3.0, lookahead=lookahead))
    for r in rs:
      self.assertEqual(set(r), set(nz.RESULT_KEYS), r['state'])
    states = {r['state'] for r in rs}
    self.assertTrue({'matched', 'no_match', 'gps_lost', 'dead_reckoning'} <= states, states)

  def test_state_and_school_codes(self):
    self.assertEqual(nz.STATE_CODES['matched'], 2)
    self.assertEqual(nz.STATE_CODES['hysteresis'], 2)
    self.assertEqual(nz.STATE_CODES['ambiguous'], 3)
    self.assertEqual(nz.STATE_CODES['low_confidence'], 3)
    self.assertEqual(nz.STATE_CODES['dead_reckoning'], 4)
    self.assertEqual(nz.STATE_CODES['no_match'], 1)
    self.assertEqual(nz.STATE_CODES['gps_lost'], 1)
    self.assertEqual(nz.SCHOOL_CODES, {'none': 0, 'inactive': 1, 'active': 2, 'unknown': 3})
    m = self.m()
    r = drive(m, [(480, -899), (500, -899)], 90.0, unix=T_IN)[-1]
    self.assertEqual((r['limit_kph'], r['school_state'], r['school_code']), (40, 'active', 2))
    self.assertEqual(r['zone_type_code'], nz.TYPES.index('Ordinary Permanent'))
    m = self.m(calendar=NEVER)
    r = drive(m, [(480, -899), (500, -899)], 90.0, unix=T_IN)[-1]
    self.assertEqual((r['limit_kph'], r['school_code']), (60, 1))
    m = self.m()
    r = drive(m, [(480, -899), (500, -899)], 90.0, unix=1000.0)[-1]  # clock not set: nothing (ambiguous)
    self.assertEqual((r['limit_kph'], r['school_code'], r['state_code'], r['candidate_kph']), (None, 3, 3, 40))
    self.assertEqual(nz.STATE_CODES['dr_ended'], 7)

  def test_school_zone_is_never_ambiguous_silent(self):
    """A School overlay lies on the base line; it must never make the match ambiguous."""
    m = self.m()
    car = Car(m, [(300, -899), (700, -899)], speed=10.0, unix=T_IN)
    rs = car.run()
    self.assertTrue(all(r['limit_kph'] is not None for r in rs), [r['state'] for r in rs])
    on = [r['limit_kph'] for r in rs if 420 <= r['_x'] <= 580]
    self.assertTrue(on and all(k == 40 for k in on), on)
    off = [r['limit_kph'] for r in rs if r['_x'] >= 640]
    self.assertTrue(off and all(k == 60 for k in off), off)


def nz_latlon(x, y):
  from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import latlon

  return latlon(x, y)


if __name__ == '__main__':
  unittest.main()
