"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): dead reckoning through tunnels on synthetic TfNSW-like lines. The car drives a polyline at a
steady speed with one update a second (mapd's rate), GPS is taken away over the "tunnel", and yaw is fed the way
the integration layer feeds it.

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import unittest

from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import ALWAYS, NEVER, T_IN, Car, MiniIndex, feat, latlon, school_rec

# A tunnel: a One Way Variable 90 with a two-way Permanent 80 drawn on top (as TfNSW draws the M4 East),
# a Variable 80 branch leaving at x=6000 towards the north-east (like the Rozelle ramps), and surface lines
# after both portals.
M_PTS = [(0, 0), (2000, 0), (4000, 200), (6000, 200), (8000, 0)]
R_PTS = [(6000, 200), (6300, 373), (6600, 673), (7000, 1073)]
TUNNEL = [
  feat([(-1500, 0), (0, 0)], 80, 'Ordinary Permanent'),  # portal approach
  feat(M_PTS, 90, 'Variable', 'One Way'),
  feat(M_PTS, 80, 'Ordinary Permanent'),
  feat(R_PTS, 80, 'Variable', 'One Way'),
  feat([(8000, 0), (9500, 0)], 70, 'Ordinary Permanent'),  # after the main portal
  feat([(7000, 1073), (7700, 1773)], 60, 'Ordinary Permanent'),  # after the branch portal
  # two parallel branches 5 m apart that heading cannot tell apart (different limits), leaving at x=3000
  feat([(3000, 100), (3400, 331), (4200, 793)], 80, 'Variable', 'One Way'),
  feat([(3002.5, 95.67), (3402.5, 326.67), (4202.5, 788.67)], 70, 'Variable', 'One Way'),
  # a surface street with a school zone right above the tunnel, 4 m to the north of it in plan (x 1000..1400)
  feat([(900, 4), (1500, 4)], 50),
  feat([(1000, 4), (1400, 4)], 40, 'School'),
  # a tunnel line that does not touch the surface line at its portal (x 20.., 15 m south), and a surface
  # line that turns north at the portal
  feat([(-1500, -3000), (0, -3000), (0, -2000)], 50),
  feat([(20, -3015), (3000, -3015)], 80, 'Variable', 'One Way'),
  # a ramp that ends 60 m short of the main line it joins (TfNSW's ramps need not touch)
  feat([(-1500, -6000), (1000, -6000)], 80, 'Variable', 'One Way'),
  feat([(1060, -5988), (4000, -5988)], 90, 'Variable', 'One Way'),
  # wrong way: a two-way Permanent 80 with a One Way Variable 90 WESTBOUND on top
  feat([(0, -9000), (2000, -9000)], 80, 'Ordinary Permanent'),
  feat([(2000, -9000), (0, -9000)], 90, 'Variable', 'One Way'),
]
SCHOOLS = [school_rec(501, 'SCHOOL ABOVE THE TUNNEL', 950, 1450, -30, 40)]
MAIN = [(-800, 0)] + M_PTS + [(9000, 0)]
BRANCH = [(-800, 0)] + M_PTS[:4] + R_PTS[1:] + [(7500, 1573)]
PARALLEL = [(-800, 0), (0, 0), (2000, 0), (3000, 100), (3400, 331), (4200, 793)]


def in_tunnel(x0=100.0, x1=None):
  return lambda s: not (x0 + 800 <= s <= (x1 if x1 is not None else 1e9))


class TestDeadReckoning(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.mi = MiniIndex(TUNNEL, SCHOOLS)

  @classmethod
  def tearDownClass(cls):
    cls.mi.close()

  def m(self, **kw):
    return nz.Matcher(self.mi.path, calendar=kw.pop('calendar', ALWAYS), **kw)

  def run_car(self, path, gps, **kw):
    mk = {k: kw.pop(k) for k in list(kw) if k in nz.Matcher.DEFAULTS}
    car = Car(self.m(**mk), path, **kw)
    return car.run(gps=gps)

  def dr(self, rs):
    return [r for r in rs if r['state'] == 'dead_reckoning']

  # ---- the main tunnel: 90 all the way (Variable 90 over Permanent 80 -> the higher)
  def test_main_tunnel_holds_the_tunnel_limit(self):
    for yaw in ('deg', 'rate'):
      rs = self.run_car(MAIN, lambda s: s < 900 or s > 8900, speed=22.0, yaw=yaw)
      tun = [r for r in rs if 1000 <= r['_s'] <= 8700]
      self.assertTrue(tun and all(r['state'] == 'dead_reckoning' for r in tun), yaw)
      self.assertTrue(all(r['limit_kph'] == 90 for r in tun), (yaw, [(round(r['_s']), r['limit_kph']) for r in tun if r['limit_kph'] != 90]))
      self.assertTrue(all(r['state_code'] == 4 for r in tun))
      self.assertTrue(all(r['dr_yaw'] for r in tun))
      after = [r for r in rs if r['_s'] > 9000]
      self.assertEqual(after[-1]['state'], 'matched', yaw)
      self.assertEqual(after[-1]['limit_kph'], 70)
      # the branch at x=6000 was never taken: no 80 published anywhere
      self.assertNotIn(80, {r['limit_kph'] for r in tun})

  # ---- the branch: chosen by the heading change, the 80 after it; the last limit held while unresolved
  def test_branch_chosen_by_heading(self):
    for yaw in ('deg', 'rate'):
      rs = self.run_car(BRANCH, lambda s: s < 900, speed=22.0, yaw=yaw)
      d = self.dr(rs)
      split = 800 + 6002  # distance driven to the branch point
      before = [r for r in d if r['_s'] < split - 50]
      after = [r for r in d if split + 400 <= r['_s'] <= split + 1300]
      self.assertTrue(all(r['limit_kph'] == 90 for r in before), yaw)
      self.assertTrue(after and all(r['limit_kph'] == 80 for r in after), (yaw, [(round(r['_s']), r['limit_kph']) for r in after]))
      # never silent, never a wrong value in between: 90 is held until the branch is resolved
      between = [r['limit_kph'] for r in d if split - 50 <= r['_s'] < split + 400]
      self.assertTrue(set(between) <= {90, 80}, between)
      self.assertEqual(between[-1], 80, yaw)
      self.assertTrue(all(r['limit_kph'] is not None for r in d if r['_s'] <= split + 1300))

  def test_branch_with_gyro_drift(self):
    rs = self.run_car(BRANCH, lambda s: s < 900, speed=22.0, yaw='deg', yaw_drift_dps=0.05)  # 17 deg over the drive
    d = self.dr(rs)
    self.assertTrue(all(r['limit_kph'] == 90 for r in d if r['_s'] < 6700))
    self.assertTrue(all(r['limit_kph'] == 80 for r in d if 7250 <= r['_s'] <= 8100))

  def test_branch_without_yaw_ends_the_hold(self):
    """Without yaw nothing tells the branches apart: the first one with a different limit (the parallel 80 / 70 pair
    at x=3000) ends dead reckoning 50 m past it. The main line (90) is still one of the branches, so 90 is held, and
    kept (state 7) once dead reckoning ends."""
    rs = self.run_car(BRANCH, lambda s: s < 900, speed=22.0, yaw=None)
    d = self.dr(rs)
    fork = 800 + 3000
    self.assertTrue(d and all(r['limit_kph'] == 90 for r in d))
    self.assertFalse(any(r['dr_yaw'] for r in d))
    self.assertLess(max(r['_s'] for r in d), fork + 100)  # at most the slack past the branch point
    late = [r for r in rs if r['_s'] > fork + 100]
    self.assertTrue(late and all(r['state'] == 'dr_ended' and r['limit_kph'] == 90 for r in late))
    self.assertTrue(('different limits') in (late[-1]['reason']))

  def test_unresolvable_branch_publishes_the_lowest_branch_then_ends(self):
    """Parallel Variable 80 / 70 branches leave the 90 tunnel line: once the 90 line is no longer a contender and the
    two cannot be told apart, the LOWEST contending branch is published - never the 90 that neither road has - and it
    is held (state 7) once the hold ends, until GPS returns."""
    rs = self.run_car(PARALLEL, lambda s: s < 900, speed=22.0, yaw='deg', dr_amb_hold_m=300.0)
    d = self.dr(rs)
    self.assertTrue(all(r['limit_kph'] == 90 for r in d if r['_s'] < 800 + 3000 - 30))
    after = [r for r in d if r['_s'] > 800 + 3000 + 60]
    self.assertTrue(after and all(r['limit_kph'] == 70 for r in after), [(round(r['_s']), r['limit_kph']) for r in after])
    self.assertTrue(any('lowest contending branch' in r['reason'] for r in d))
    self.assertNotIn(90, [r['limit_kph'] for r in rs if r['_s'] > 800 + 3000 + 60])
    end = [r for r in rs if r['_s'] > 800 + 3000 + 450]
    self.assertTrue(end and all(r['state'] == 'dr_ended' and r['limit_kph'] == 70 for r in end))
    self.assertTrue(('unresolved') in (end[-1]['reason']))

  def test_distance_cap(self):
    rs = self.run_car(MAIN, lambda s: s < 900, speed=22.0, freeze_max_m=1500.0)
    d = self.dr(rs)
    self.assertLessEqual(max(r['dr_m'] for r in d), 1500)
    late = [r for r in rs if r['_s'] > 900 + 1600]
    self.assertTrue(all(r['state'] == 'dr_ended' and r['limit_kph'] == 90 for r in late))
    self.assertTrue(('capped') in (late[0]['reason']))

  def test_default_cap_is_12_km(self):
    self.assertEqual(nz.Matcher.DEFAULTS['freeze_max_m'], 12000.0)

  def test_stopped_in_the_tunnel(self):
    """Stop-and-go traffic: 3 min stationary mid-tunnel while the (uncorrected) gyro drifts, then drive on."""
    m = self.m()
    car = Car(m, BRANCH, speed=22.0, yaw='deg', yaw_drift_dps=0.2)
    rs = car.run(gps=lambda s: s < 900, until=3500)
    v = car.speed
    car.speed = 0.0
    for _ in range(180):
      rs.append(car.step(gps_ok=False))
    car.speed = v
    rs += car.run(gps=lambda s: False)
    d = self.dr(rs)
    self.assertTrue(all(r['limit_kph'] == 90 for r in d if r['_s'] < 6700))
    self.assertTrue(all(r['limit_kph'] == 80 for r in d if 7250 <= r['_s'] <= 8100), [(round(r['_s']), r['limit_kph']) for r in d if r['_s'] > 7000])

  # ---- TfNSW's tunnel line does not touch the line matched at the portal: found along the dead-reckoned track
  def test_tunnel_line_found_along_the_track(self):
    path = [(-800, -3000), (0, -3000), (60, -3014), (2900, -3015)]
    rs = self.run_car(path, lambda s: s < 700, speed=20.0)
    d = self.dr(rs)
    self.assertTrue(all(r['limit_kph'] == 80 for r in d if r['_s'] > 1300), [(round(r['_s']), r['limit_kph'], r['state']) for r in d])
    self.assertTrue(any(r['limit_kph'] == 80 for r in d))

  # ---- a ramp ending short of the main line it joins
  def test_gap_to_the_main_line_is_bridged(self):
    path = [(-1300, -6000), (1000, -6000), (1060, -5988), (3900, -5988)]
    rs = self.run_car(path, lambda s: s < 400, speed=20.0)
    d = self.dr(rs)
    self.assertTrue(all(r['limit_kph'] == 80 for r in d if r['_s'] < 2250))
    self.assertTrue(all(r['limit_kph'] == 90 for r in d if 2500 < r['_s'] < 5100), [(round(r['_s']), r['limit_kph']) for r in d if r['_s'] > 2250])

  # ---- a school zone on the street above the tunnel is not the tunnel's
  def test_school_zone_above_the_tunnel_is_not_applied(self):
    for gps in (lambda s: s < 900, lambda s: True):
      m = self.m(calendar=ALWAYS)
      car = Car(m, MAIN, speed=15.0, unix=T_IN)
      rs = car.run(gps=gps, until=2600)
      on = [r for r in rs if 1850 <= r['_s'] <= 2150]
      self.assertTrue(on and all(r['limit_kph'] == 90 for r in on), [(round(r['_s']), r['limit_kph'], r['state']) for r in on])
    # ... while a car on the surface street gets it
    m = self.m(calendar=ALWAYS)
    rs = Car(m, [(900, 4), (1500, 4)], speed=12.0, unix=T_IN).run()
    self.assertTrue((40) in ([r['limit_kph'] for r in rs]))
    m = self.m(calendar=NEVER)
    rs = Car(m, [(900, 4), (1500, 4)], speed=12.0, unix=T_IN).run()
    self.assertNotIn(40, [r['limit_kph'] for r in rs])

  # ---- a path along a two-way line against a One Way line drawn on it is a wrong-way path
  def test_wrong_way_is_detected(self):
    m = self.m()
    L = m.L['b']
    lat, lon = latlon(1000, -9000)
    part = next(
      q
      for q in range(len(L['part_speed']))
      if int(L['part_speed'][q]) == 80 and int(L['part_dir'][q]) == 0 and abs(int(L['vlat'][int(L['part_v0'][q])]) - lat * 1e6) < 5
    )
    kph, src = m._effective_limit(part, lat * 1e6, lon * 1e6, 1.0, 0.0)  # eastbound
    self.assertTrue(m._wrong_way)
    self.assertEqual(kph, 80)
    kph, src = m._effective_limit(part, lat * 1e6, lon * 1e6, -1.0, 0.0)  # westbound: the Variable applies
    self.assertFalse(m._wrong_way)
    self.assertEqual(kph, 90)

  # ---- GPS back: the accuracy rule, then the line dead reckoning had reached is the continuity prior
  def test_reacquire_after_the_tunnel(self):
    m = self.m()
    car = Car(m, MAIN, speed=22.0)
    car.run(gps=lambda s: s < 900, until=5000)
    r = car.step(gps_ok=True, acc=22.0)
    self.assertEqual(r['state'], 'dead_reckoning')
    self.assertTrue(('re-acquiring') in (r['reason']))
    r = car.step(gps_ok=True, acc=14.0)
    self.assertEqual(r['state'], 'dead_reckoning')
    r = car.step(gps_ok=True, acc=14.0)
    self.assertEqual(r['state'], 'matched')
    self.assertEqual(r['limit_kph'], 90)

  def test_no_dead_reckoning_from_a_stale_match(self):
    m = self.m(freeze_recent_s=5.0)
    car = Car(m, MAIN, speed=22.0)
    car.run(until=500)
    for _ in range(8):  # 8 s of fixes that match nothing (far away)
      m.update(-33.0, 150.0, 90.0, 22.0, True, 3.0, T_IN, mono_time=car.t)
      car.t += 1.0
    r = car.step(gps_ok=False)
    self.assertEqual(r['state'], 'gps_lost')
    self.assertIsNone(r['limit_kph'])

  def test_dead_reckoning_result_has_every_key(self):
    m = self.m()
    car = Car(m, MAIN, speed=22.0)
    rs = car.run(gps=lambda s: s < 900, until=1500)
    for r in rs:
      keys = set(r) - {'_s', '_x', '_y'}
      self.assertEqual(keys, set(nz.RESULT_KEYS))
    d = self.dr(rs)
    self.assertTrue(d)
    self.assertTrue(all(r['dr_m'] is not None and r['dr_hyps'] >= 1 for r in d))


if __name__ == '__main__':
  unittest.main()
