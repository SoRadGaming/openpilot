"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): matcher tests on a synthetic mini-index (ported from the P1 tools/test_nsw_zones.py; the
dead-reckoning, owner-rule and look-ahead tests live in their own files).

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import datetime as dt
import math
import os
import shutil
import tempfile
import unittest

import numpy as np

from openpilot.sunnypilot.mapd.nsw_zones import build_index as bi
from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones import index as nz_index
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import ALWAYS, NEVER, UNKNOWN, T_IN, T_OUT, feat, latlon, school_rec, unix_sydney


def synthetic_features():
  F = []
  # A: east-west main road, 60, x 0..500, then 80 x 500..1000 (zone boundary at x=500)
  F.append(feat([(0, 0), (250, 0), (500, 0)], 60, 'Ordinary Permanent'))
  F.append(feat([(500, 0), (1000, 0)], 80, 'Ordinary Permanent'))
  # B: parallel road 20 m north, 50 (Default)
  F.append(feat([(0, 20), (1000, 20)], 50))
  # C: north-south side street crossing A at x=700, 40, High Pedestrian
  F.append(feat([(700, -300), (700, -5), (700, 5), (700, 300)], 40, 'High Pedestrian'))
  # D: One Way street digitized west->east, 30, at y=-200 (far from the others)
  F.append(feat([(0, -200), (400, -200)], 30, 'Shared', 'One Way'))
  # E: a multi-part feature (two carriageways digitized in opposite directions), 90, at y=-500 / -520
  F.append(feat([[(0, -500), (600, -500)], [(600, -520), (0, -520)]], 90, 'Default', multi=True))
  # School overlay on A, x 200..300 (polygon joined), and an unjoined one on A2 x 800..850
  F.append(feat([(200, 0), (250, 0), (300, 0)], 40, 'School'))
  F.append(feat([(800, 0), (850, 0)], 40, 'School'))
  # Wet Weather overlay on B, x 100..200
  F.append(feat([(100, 20), (200, 20)], 80, 'Wet Weather'))
  # null geometry and a null Direction, both present in the real file
  F.append({'type': 'Feature', 'geometry': None, 'properties': {'Type': 'Default', 'Status': 'Existing', 'Direction': 'Both Directions', 'Speed': '50 km/h'}})
  F.append(feat([(0, -800), (300, -800)], 70, 'Default', None))
  return F


def synthetic_schools():
  return [
    school_rec(111, 'TEST PUBLIC SCHOOL', 190, 310, -12, 12),
    school_rec(222, 'MALFORMED TIMES SCHOOL', 2000, 2100, 2000, 2100, times=('08:00 AM', '09:30  AM', '14:30 PM', '4 PM')),
    school_rec(333, 'NO TIMES SCHOOL', 3000, 3100, 3000, 3100, times=(None, None, None, None), late='Y'),
  ]


class _Base(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.tmp = tempfile.mkdtemp(prefix='nswz_test_')
    cls.B = bi.build_arrays(synthetic_features(), synthetic_schools(), 1.0, log=lambda s: None)
    cls.path = os.path.join(cls.tmp, 'mini.npz')
    bi.write_index(cls.B['arrays'], cls.path)

  @classmethod
  def tearDownClass(cls):
    shutil.rmtree(cls.tmp, ignore_errors=True)

  def matcher(self, calendar=ALWAYS, **kw):
    return nz.Matcher(self.path, calendar=calendar, **kw)

  def drive(self, m, pts, heading, speed=15.0, t0=0.0, unix=T_IN, acc=3.0, gps_ok=True, dt_s=1.0):
    out = []
    for i, (x, y) in enumerate(pts):
      lat, lon = latlon(x, y)
      out.append(m.update(lat, lon, heading, speed, gps_ok, acc, unix + i * dt_s, mono_time=t0 + i * dt_s))
    return out


# ============================================================================ parsing
class TestParsing(unittest.TestCase):
  def test_canonical_times(self):
    self.assertEqual(bi.parse_time('08:00 AM', 'AM'), (480, None))
    self.assertEqual(bi.parse_time('02:30 PM', 'PM'), (870, None))
    self.assertEqual(bi.parse_time('12:00 PM', 'PM'), (720, None))
    self.assertEqual(bi.parse_time('12:01 PM', 'PM'), (721, None))
    self.assertEqual(bi.parse_time('11:59 AM', 'AM'), (719, None))

  def test_malformed_times(self):
    cases = {
      ('09:30  AM', 'AM'): 570,
      ('02:00  PM', 'PM'): 840,
      ('14:30 PM', 'PM'): 870,
      ('16:00 PM', 'PM'): 960,
      ('4 PM', 'PM'): 960,
      ('4:00 PM', 'PM'): 960,
      ('4:30 PM', 'PM'): 990,
      ('12:00 AM', 'AM'): 720,
    }
    for (raw, exp), want in cases.items():
      v, note = bi.parse_time(raw, exp)
      self.assertEqual(v, want, raw)
      self.assertTrue(note, f'{raw!r} must be reported as interpreted')
    self.assertEqual(bi.parse_time('4', 'PM')[0], 960)  # no AM/PM -> the field's half
    self.assertEqual(bi.parse_time(None, 'AM'), (None, 'missing'))
    self.assertIsNone(bi.parse_time('noon-ish', 'PM')[0])
    self.assertIsNone(bi.parse_time('25:00 PM', 'PM')[0])

  def test_speed(self):
    self.assertEqual(bi.parse_speed('50 km/h'), 50)
    self.assertEqual(bi.parse_speed('110 km/h'), 110)
    self.assertIsNone(bi.parse_speed('fast'))
    self.assertIsNone(bi.parse_speed(None))

  def test_school_records_guesses_reported(self):
    gates = []
    rows, guesses, opcal_s, opcal_e, desc = bi.build_schools(synthetic_schools(), gates, print)
    self.assertEqual(gates, [])
    by = {r['zone_id']: r for r in rows}
    self.assertEqual(by[222]['times'], [480, 570, 870, 960])
    self.assertTrue(by[222]['flags'] & bi.S_FLAG_TIME_GUESSED)
    self.assertEqual(by[333]['times'], list(bi.STD_TIMES))
    self.assertTrue(by[333]['flags'] & bi.S_FLAG_TIME_DEFAULT)
    self.assertEqual(by[333]['late'], 1)
    self.assertFalse(by[111]['flags'])
    fields = {(g['zone_id'], g['field']) for g in guesses}
    self.assertTrue((('222', 'END_TIME_AM')) in (fields))
    self.assertTrue((('222', 'START_TIME_PM')) in (fields))
    self.assertTrue((('222', 'END_TIME_PM')) in (fields))
    self.assertEqual(opcal_s[0, 0], (dt.date(2026, 2, 9) - dt.date(1970, 1, 1)).days)

  def test_unknown_values_fail_the_gate(self):
    f = feat([(0, 0), (10, 0)], 50)
    f['properties']['Type'] = 'Truck Zone'
    g = feat([(0, 0), (10, 0)], 50)
    g['properties']['Speed'] = 'fast'
    B = bi.build_arrays([f, g, feat([(0, 5), (10, 5)], 50)], [], 1.0, log=lambda s: None)
    self.assertEqual(len(B['gates']), 2)

  def test_douglas_peucker(self):
    x = np.array([0, 10, 20, 30, 40.0])
    k = bi.dp_keep(x, np.array([0, 0.3, -0.2, 0.1, 0]), 1.0)
    self.assertEqual(k.tolist(), [True, False, False, False, True])
    k = bi.dp_keep(x, np.array([0, 2.5, 5.0, 2.5, 0]), 1.0)
    self.assertEqual(k.tolist(), [True, False, True, False, True])
    # a hairpin: distance to the chord SEGMENT, not the infinite line, keeps the far end
    k = bi.dp_keep(np.array([0, 50, 100, 50, 0.5]), np.array([0, 0.2, 0, 0.3, 0.0]), 1.0)
    self.assertTrue(k[2])


class TestLocalTime(unittest.TestCase):
  def test_sydney_dst_2026(self):
    # DST starts Sun 4 Oct 2026 02:00 AEST = Sat 3 Oct 16:00 UTC; ends Sun 5 Apr 2026 03:00 AEDT = Sat 4 Apr 16:00 UTC
    def u(*a):
      return (dt.datetime(*a) - dt.datetime(1970, 1, 1)).total_seconds()

    self.assertEqual(nz.utc_offset_min(u(2026, 10, 3, 15, 59), 0), 600)
    self.assertEqual(nz.utc_offset_min(u(2026, 10, 3, 16, 0), 0), 660)
    self.assertEqual(nz.utc_offset_min(u(2026, 4, 4, 15, 59), 0), 660)
    self.assertEqual(nz.utc_offset_min(u(2026, 4, 4, 16, 0), 0), 600)
    self.assertEqual(nz.utc_offset_min(u(2026, 7, 1), 1), 570)  # Broken Hill winter
    self.assertEqual(nz.utc_offset_min(u(2026, 12, 1), 1), 630)  # Broken Hill summer
    self.assertEqual(nz.utc_offset_min(u(2026, 12, 1), 2), 660)  # Lord Howe summer (+11)
    self.assertEqual(nz.utc_offset_min(u(2026, 7, 1), 2), 630)
    self.assertEqual(nz.local_datetime(u(2026, 10, 12, 21, 30), 0), dt.datetime(2026, 10, 13, 8, 30))

  def test_against_zoneinfo_if_available(self):
    try:
      from zoneinfo import ZoneInfo

      zones = [ZoneInfo('Australia/Sydney'), ZoneInfo('Australia/Broken_Hill'), ZoneInfo('Australia/Lord_Howe')]
    except Exception:
      self.skipTest('no tz database on this machine (the matcher does not need one)')
    start = dt.datetime(2025, 1, 1, tzinfo=dt.UTC).timestamp()
    for i in range(3 * 366 * 24 * 4):  # every 15 minutes for 3 years
      uu = start + i * 900
      for tz, z in enumerate(zones):
        want = dt.datetime.fromtimestamp(uu, z).utcoffset().total_seconds() / 60
        self.assertEqual(nz.utc_offset_min(uu, tz), want, (tz, uu))

  def test_tz_for_location(self):
    self.assertEqual(nz.tz_for_location(-31.95, 141.46), 1)  # Broken Hill
    self.assertEqual(nz.tz_for_location(-33.87, 151.21), 0)  # Sydney
    self.assertEqual(nz.tz_for_location(-31.55, 159.08), 2)  # Lord Howe
    self.assertEqual(bi.school_tz(-31.95, 141.46), 1)


class TestCalendarPlug(unittest.TestCase):
  def test_default_calendar_is_the_real_one(self):
    fn, desc = nz.default_calendar()
    self.assertTrue(('school_days.py') in (desc))
    self.assertIs(fn(dt.date(2026, 10, 13), division='eastern'), True)  # Tuesday, Term 4
    self.assertIs(fn(dt.date(2026, 10, 17), division='eastern'), False)  # Saturday
    self.assertIsNone(fn(dt.date(2031, 1, 6)))  # past the published calendar

  def test_index_calendar_used_when_it_reaches_further(self):
    from openpilot.sunnypilot.mapd.nsw_zones import school_days as sd
    import json

    doc = sd.build_json()
    # an index built later carries a calendar one year longer (a copy of the last year shifted: test only)
    last = doc['coverage']['eastern']['last']
    later = str(int(last[:4]) + 1) + last[4:]
    doc['coverage'] = {k: {'first': v['first'], 'last': later} for k, v in doc['coverage'].items()}
    arrays = {'calendar_json': np.frombuffer(json.dumps(doc).encode(), np.uint8)}
    fn, desc = nz.default_calendar(arrays)
    self.assertTrue(('index calendar') in (desc))
    self.assertTrue((later) in (desc))
    # a broken embedded calendar is ignored, never fatal
    fn, desc = nz.default_calendar({'calendar_json': np.frombuffer(b'{not json', np.uint8)})
    self.assertTrue(('school_days.py') in (desc))


# ============================================================================ index file
class TestIndexFile(_Base):
  def test_round_trip_compressed_and_mmap(self):
    back = nz_index.load_index(self.path)
    for k, v in self.B['arrays'].items():
      self.assertTrue((k) in (back))
      self.assertEqual(back[k].dtype, v.dtype, k)
      self.assertTrue(np.array_equal(back[k], v), k)
    # plain np.savez (unaligned members: read into RAM) and the aligned writer (mapped)
    for i, writer in enumerate((lambda a, p: np.savez(p, **a), nz_index.write_stored_aligned)):
      stored = os.path.join(self.tmp, f'mini_stored{i}.npz')
      writer(self.B['arrays'], stored)
      mm = nz_index.load_index(stored, mmap=True)
      for k, v in self.B['arrays'].items():
        self.assertEqual(np.asarray(mm[k]).shape, v.shape, k)
        self.assertTrue(np.array_equal(np.asarray(mm[k]), v), k)
        self.assertTrue(np.asarray(mm[k]).flags.aligned, k)
      del mm
    m = nz.Matcher(stored, calendar=ALWAYS, mmap=True)
    r = self.drive(m, [(100, 2), (105, 2)], 90.0)[-1]
    self.assertEqual(r['limit_kph'], 60)
    del m

  def test_attribution_everywhere(self):
    back = nz_index.load_index(self.path)
    self.assertEqual(str(back['attribution']), 'Contains data from Transport for NSW, CC BY 4.0, modified')
    m = self.matcher()
    self.assertTrue(('Transport for NSW') in (m.attribution))

  def test_layers_and_join(self):
    a = self.B['arrays']
    self.assertEqual(self.B['null_geom'], 1)
    # base: A1, A2, B, C, D, E(2 parts), null-dir road = 8 parts; overlay: 2 School + 1 Wet Weather
    self.assertEqual(len(a['b_part_speed']), 8)
    self.assertEqual(len(a['o_part_speed']), 3)
    ps = a['o_part_school']
    self.assertEqual(sorted(ps.tolist()), [-1, -1, 0])  # school 111 joined; the other school line and Wet Weather not
    self.assertEqual(self.B['schools']['join_how']['inside'], 1)
    self.assertEqual(len(self.B['schools']['orphans']), 2)  # 222 and 333 have no line

  def test_wrong_format_version_refused(self):
    bad = dict(self.B['arrays'])
    import json

    meta = json.loads(bytes(bad['meta_json']).decode())
    meta['format_version'] = 99
    bad['meta_json'] = np.frombuffer(json.dumps(meta).encode(), np.uint8).copy()
    p = os.path.join(self.tmp, 'bad.npz')
    bi.write_index(bad, p)
    with self.assertRaises(ValueError):
      nz.Matcher(p, calendar=ALWAYS)


# ============================================================================ matching
class TestMatching(_Base):
  def test_simple_match(self):
    m = self.matcher()
    r = self.drive(m, [(100, 3), (110, 3), (120, 3)], 90.0)[-1]
    self.assertEqual(r['limit_kph'], 60)
    self.assertEqual(r['zone_type'], 'Ordinary Permanent')
    self.assertEqual(r['state'], 'matched')
    self.assertGreaterEqual(r['confidence'], 0.8)
    self.assertLess(r['dist_m'], 3.5)
    self.assertLess(r['heading_err_deg'], 1.0)

  def test_parallel_roads(self):
    m = self.matcher()
    self.assertEqual(self.drive(m, [(400, 17), (410, 17)], 90.0)[-1]['limit_kph'], 50)
    m.reset()
    # exactly between the two parallel roads, nothing to go on: do not guess
    r = self.drive(m, [(400, 10)], 90.0)[-1]
    self.assertIsNone(r['limit_kph'])
    self.assertEqual(r['state'], 'ambiguous')
    self.assertTrue((r['candidate_kph']) in ((50, 60)))

  def test_hysteresis_keeps_the_current_line(self):
    m = self.matcher()
    rs = self.drive(m, [(100, 2), (110, 2), (120, 2)], 90.0)
    self.assertEqual(rs[-1]['limit_kph'], 60)
    # GPS drifts two fixes toward the parallel road: stay on A
    rs = self.drive(m, [(130, 13), (140, 13)], 90.0, t0=3.0)
    self.assertTrue(all(r['limit_kph'] == 60 for r in rs), [r['limit_kph'] for r in rs])
    self.assertEqual(rs[0]['state'], 'hysteresis')
    # back on A for one fix resets the challenger count
    self.assertEqual(self.drive(m, [(145, 2)], 90.0, t0=5.0)[-1]['state'], 'matched')
    # really on B now: switches once B has won for switch_s (1.75 s: the 3rd fix at 1 Hz)
    rs = self.drive(m, [(150, 19), (160, 19), (170, 19), (180, 19)], 90.0, t0=6.0)
    ks = [r['limit_kph'] for r in rs]
    # 19 m from A and 1 m from B, the held line is not published (low confidence), B is not yet
    self.assertTrue(all(k in (60, None) for k in ks[:2]), ks)
    self.assertEqual([r['zone_id'] for r in rs[:2]], [rs[0]['zone_id']] * 2)
    self.assertEqual(ks[2:], [50, 50])
    self.assertTrue(('switched') in (rs[2]['reason']))

  def test_heading_rejects_the_crossing_street(self):
    m = self.matcher()
    r = self.drive(m, [(690, 1), (695, 1), (702, 1)], 90.0)[-1]  # driving east over C
    self.assertEqual(r['limit_kph'], 80)
    m.reset()
    r = self.drive(m, [(701, -30), (701, -20), (701, -8)], 0.0)[-1]  # driving north on C
    self.assertEqual(r['limit_kph'], 40)
    self.assertEqual(r['zone_type'], 'High Pedestrian')
    m.reset()
    r = self.drive(m, [(701, -30), (701, -20)], 45.0)[-1]  # 45 deg off everything near
    self.assertLessEqual(r['heading_err_deg'] or 0, 45.0)

  def test_both_directions_matches_either_heading(self):
    m = self.matcher()
    r1 = self.drive(m, [(300, 21), (290, 21)], 270.0)[-1]
    self.assertEqual(r1['limit_kph'], 50)
    self.assertEqual(r1['direction'], 'both')

  def test_one_way(self):
    m = self.matcher()
    r = self.drive(m, [(100, -199), (110, -199)], 90.0)[-1]  # with the digitized direction
    self.assertEqual(r['limit_kph'], 30)
    self.assertTrue(r['one_way_undocumented'])
    self.assertTrue(('undocumented') in (r['reason']))
    m.reset()
    r = self.drive(m, [(110, -199), (100, -199)], 270.0)[-1]  # against it
    self.assertIsNone(r['limit_kph'])
    self.assertEqual(r['state'], 'no_match')
    self.assertTrue(('One Way') in (r['reason']))

  def test_multipart_and_null_direction(self):
    m = self.matcher()
    self.assertEqual(self.drive(m, [(100, -519), (90, -519)], 270.0)[-1]['limit_kph'], 90)
    m.reset()
    r = self.drive(m, [(100, -801), (110, -801)], 90.0)[-1]
    self.assertEqual(r['limit_kph'], 70)
    self.assertEqual(r['direction'], 'none')

  def test_no_road(self):
    m = self.matcher()
    r = self.drive(m, [(5000, 5000)], 90.0)[-1]
    self.assertIsNone(r['limit_kph'])
    self.assertEqual(r['state'], 'no_match')

  def test_slow_no_heading_uses_distance_and_continuity(self):
    m = self.matcher()
    r = self.drive(m, [(100, 3)], 90.0, speed=0.5)[-1]
    self.assertEqual(r['limit_kph'], 60)
    self.assertIsNone(r['heading_err_deg'])
    self.assertTrue(('no heading') in (r['reason']))

  def test_zone_boundary_switches_once(self):
    m = self.matcher()
    xs = list(range(470, 540, 1))
    rs = self.drive(m, [(x, 1) for x in xs], 90.0, speed=15.0, dt_s=0.1)
    ks = [r['limit_kph'] for r in rs]
    changes = [i for i in range(1, len(ks)) if ks[i] != ks[i - 1]]
    self.assertEqual(len(changes), 1, ks)
    x_switch = xs[changes[0]]
    self.assertGreater(x_switch, 500)
    self.assertLessEqual(x_switch, 505)
    self.assertEqual((ks[0], ks[-1]), (60, 80))

  def test_boundary_jitter_does_not_flip(self):
    m = self.matcher()
    self.drive(m, [(480, 1), (490, 1), (500, 1)], 90.0)
    xs = [504, 499, 506, 501, 508, 503, 510]
    ks = [r['limit_kph'] for r in self.drive(m, [(x, 1) for x in xs], 90.0, t0=3.0)]
    changes = sum(1 for i in range(1, len(ks)) if ks[i] != ks[i - 1])
    self.assertLessEqual(changes, 1, ks)
    self.assertEqual(ks[-1], 80)


class TestGpsLoss(_Base):
  def test_freeze_and_timeout(self):
    m = self.matcher(freeze_max_s=60.0, freeze_max_m=1e9)
    self.drive(m, [(100, 2), (110, 2), (120, 2)], 90.0)
    rs = [m.update(None, None, None, 15.0, False, None, T_IN + 3 + i, mono_time=3.0 + i) for i in range(70)]
    self.assertEqual(rs[0]['state'], 'dead_reckoning')
    self.assertEqual(rs[0]['state_code'], 4)
    self.assertEqual(rs[0]['limit_kph'], 60)
    self.assertTrue(('dead reckoning') in (rs[0]['reason']))
    last_frozen = max(i for i, r in enumerate(rs) if r['state'] == 'dead_reckoning')
    self.assertEqual(last_frozen, 59)  # t = 62: 60 s after the last good fix at t = 2
    # capped: the last value dead reckoning published is held as its own state (7) until GPS returns
    self.assertEqual(rs[-1]['state'], 'dr_ended')
    self.assertEqual(rs[-1]['state_code'], 7)
    self.assertEqual(rs[-1]['limit_kph'], rs[last_frozen]['limit_kph'])
    self.assertTrue(('capped') in (rs[-1]['reason']))

  def test_freeze_distance_cap(self):
    m = self.matcher(freeze_max_s=600.0, freeze_max_m=300.0)
    self.drive(m, [(100, 2), (110, 2)], 90.0)
    rs = [m.update(None, None, None, 30.0, False, None, T_IN + 2 + i, mono_time=2.0 + i) for i in range(20)]
    n_frozen = sum(r['state'] == 'dead_reckoning' for r in rs)
    self.assertEqual(n_frozen, 10)  # 30 m/s * 10 s = 300 m

  def test_poor_accuracy_counts_as_loss(self):
    m = self.matcher()
    self.drive(m, [(100, 2), (110, 2)], 90.0)
    lat, lon = latlon(120, 40)  # a wild fix near B, 40 m accuracy
    r = m.update(lat, lon, 90.0, 15.0, True, 40.0, T_IN + 2, mono_time=2.0)
    self.assertEqual(r['state'], 'dead_reckoning')
    self.assertEqual(r['limit_kph'], 60)

  def test_no_freeze_of_a_stale_result(self):
    m = self.matcher(freeze_recent_s=5.0)
    self.drive(m, [(100, 2)], 90.0)
    self.drive(m, [(5000, 5000)] * 10, 90.0, t0=1.0)  # 10 s with nothing matched
    r = m.update(None, None, None, 15.0, False, None, T_IN + 11, mono_time=11.0)
    self.assertIsNone(r['limit_kph'])
    self.assertEqual(r['state'], 'gps_lost')

  def test_reacquire_after_loss(self):
    m = self.matcher()
    self.drive(m, [(100, 2), (110, 2)], 90.0)
    for i in range(5):
      m.update(None, None, None, 15.0, False, None, T_IN + 2 + i, mono_time=2.0 + i)
    r = self.drive(m, [(200, 2)], 90.0, t0=7.0)[-1]
    self.assertEqual(r['state'], 'matched')


class TestSchool(_Base):
  def test_school_inside_and_outside_hours(self):
    m = self.matcher()
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_IN)[-1]
    self.assertTrue(r['school_active'])
    self.assertEqual(r['limit_kph'], 40)
    self.assertEqual(r['base_kph'], 60)
    self.assertEqual(r['school_name'], 'TEST PUBLIC SCHOOL')
    self.assertEqual(r['school_zone_id'], 111)
    m.reset()
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_OUT)[-1]
    self.assertFalse(r['school_active'])
    self.assertEqual(r['school_state'], 'inactive')
    self.assertEqual(r['limit_kph'], 60)
    for hh, mm, want in ((7, 59, 60), (8, 0, 40), (9, 29, 40), (9, 30, 60), (14, 30, 40), (15, 59, 40), (16, 0, 60), (12, 0, 60)):
      m.reset()
      r = self.drive(m, [(245, 1)], 90.0, unix=unix_sydney(2026, 10, 13, hh, mm))[-1]
      self.assertEqual(r['limit_kph'], want, (hh, mm))

  def test_school_only_on_its_stretch(self):
    m = self.matcher()
    r = self.drive(m, [(150, 1), (160, 1)], 90.0, unix=T_IN)[-1]
    self.assertEqual(r['limit_kph'], 60)
    self.assertEqual(r['school_state'], 'none')
    m.reset()
    # the school line is on A; driving the parallel road B beside it must not pick it up
    r = self.drive(m, [(240, 19), (250, 19)], 90.0, unix=T_IN)[-1]
    self.assertEqual(r['limit_kph'], 50)
    self.assertEqual(r['school_state'], 'none')
    m.reset()
    # crossing street C near its crossing of A is perpendicular to the school... (school is at x 200-300; C at 700)
    r = self.drive(m, [(701, -30), (701, -10)], 0.0, unix=T_IN)[-1]
    self.assertEqual(r['school_state'], 'none')

  def test_calendar_says_no(self):
    m = self.matcher(calendar=NEVER)
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_IN)[-1]
    self.assertEqual(r['limit_kph'], 60)
    self.assertEqual(r['school_state'], 'inactive')

  def test_calendar_without_an_answer_takes_weekdays_as_school_days(self):
    """Past the calendar's coverage (a lapsed update) the zone is not dropped: every weekday is a school day."""
    m = self.matcher(calendar=UNKNOWN)
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_IN)[-1]  # a Tuesday, 08:30
    self.assertEqual((r['school_state'], r['limit_kph']), ('active', 40))
    self.assertTrue(('every weekday') in (r['school_reason']))
    m.reset()
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=unix_sydney(2026, 10, 17, 8, 30))[-1]  # a Saturday
    self.assertEqual((r['school_state'], r['limit_kph']), ('inactive', 60))
    m.reset()
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_OUT)[-1]  # a weekday outside the hours
    self.assertEqual((r['school_state'], r['limit_kph']), ('inactive', 60))
    # a calendar that raises is treated the same way
    m = self.matcher(calendar=(lambda d, division=None: 1 / 0, 'broken'))
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_IN)[-1]
    self.assertEqual((r['school_state'], r['limit_kph']), ('active', 40))

  def test_invalid_clock(self):
    """No valid clock: the school zone's state cannot be known. Default 'withhold': nothing published (ambiguous),
    neither the base (too high if active) nor the school limit (all night)."""
    m = self.matcher()
    r = self.drive(m, [(240, 1), (250, 1)], 90.0, unix=1000.0)[-1]
    self.assertEqual(r['school_state'], 'unknown')
    self.assertTrue(('clock') in (r['school_reason']))
    self.assertIsNone(r['limit_kph'])
    self.assertEqual((r['state'], r['state_code'], r['candidate_kph']), ('ambiguous', 3, 40))
    m = self.matcher(school_unknown_policy='apply')
    self.assertEqual(self.drive(m, [(240, 1), (250, 1)], 90.0, unix=1000.0)[-1]['limit_kph'], 40)
    m = self.matcher(school_unknown_policy='ignore')
    self.assertEqual(self.drive(m, [(240, 1), (250, 1)], 90.0, unix=1000.0)[-1]['limit_kph'], 60)
    # away from the school line an invalid clock changes nothing
    m = self.matcher()
    self.assertEqual(self.drive(m, [(150, 1), (160, 1)], 90.0, unix=1000.0)[-1]['limit_kph'], 60)

  def test_stub_calendar_weekend(self):
    m = self.matcher(calendar=(lambda d, division=None: d.weekday() < 5, 'weekdays'))
    sat = unix_sydney(2026, 10, 17, 8, 30)
    self.assertEqual(self.drive(m, [(240, 1), (250, 1)], 90.0, unix=sat)[-1]['limit_kph'], 60)
    m.reset()
    self.assertEqual(self.drive(m, [(240, 1), (250, 1)], 90.0, unix=T_IN)[-1]['limit_kph'], 40)

  def test_unjoined_school_line_uses_standard_times(self):
    m = self.matcher()
    r = self.drive(m, [(820, 1), (830, 1)], 90.0, unix=T_IN)[-1]
    self.assertEqual(r['limit_kph'], 40)
    self.assertIsNone(r['school_name'])
    self.assertTrue(('standard times') in (r['school_reason']))
    m.reset()
    r = self.drive(m, [(820, 1), (830, 1)], 90.0, unix=unix_sydney(2026, 10, 13, 14, 45))[-1]
    self.assertEqual(r['limit_kph'], 40)
    m.reset()
    r = self.drive(m, [(820, 1), (830, 1)], 90.0, unix=unix_sydney(2026, 10, 13, 16, 5))[-1]
    self.assertEqual(r['limit_kph'], 80)

  def test_malformed_times_school_windows(self):
    m = self.matcher()
    row = [i for i in range(m.n_schools) if int(m.S['zone_id'][i]) == 222][0]
    self.assertEqual(m.school_window(row), (480, 570, 870, 960))
    self.assertEqual(m.school_active(row, unix_sydney(2026, 10, 13, 15, 0))[0], 'active')
    self.assertEqual(m.school_active(row, unix_sydney(2026, 10, 13, 16, 0))[0], 'inactive')
    self.assertEqual(m.school_active(row, unix_sydney(2026, 10, 13, 9, 29))[0], 'active')

  def test_division_passed_to_calendar(self):
    seen = []
    m = self.matcher(calendar=(lambda d, division=None: seen.append(division) or True, 'spy'))
    row333 = [i for i in range(m.n_schools) if int(m.S['zone_id'][i]) == 333][0]
    row111 = [i for i in range(m.n_schools) if int(m.S['zone_id'][i]) == 111][0]
    m.school_active(row333, T_IN)
    m.school_active(row111, T_IN)
    m.school_active(-1, T_IN, *[int(round(v * 1e6)) for v in latlon(820, 0)])
    self.assertEqual(seen, ['western', 'eastern', None])

  def test_wet_weather_not_applied(self):
    m = self.matcher()
    r = self.drive(m, [(140, 19), (150, 19)], 90.0)[-1]
    self.assertEqual(r['limit_kph'], 50)
    self.assertTrue(any('Wet Weather' in n for n in r['overlay_notes']))

  def test_freeze_reevaluates_school_time(self):
    m = self.matcher()
    t_edge = unix_sydney(2026, 10, 13, 9, 29, 50)
    rs = self.drive(m, [(240, 1), (245, 1)], 90.0, unix=t_edge - 1)
    self.assertEqual(rs[-1]['limit_kph'], 40)
    r = m.update(None, None, None, 5.0, False, None, t_edge + 30, mono_time=2.0)
    self.assertEqual(r['state'], 'dead_reckoning')
    self.assertEqual(r['limit_kph'], 60)


class TestReviewFixes(unittest.TestCase):
  """Regression tests for the defects the P1 review found (each test names its defect)."""

  @classmethod
  def setUpClass(cls):
    F = [
      # 1/2: a One Way Variable 90 drawn on top of a two-way Permanent 80 (x 200..800 of 0..1000)
      feat([(0, 0), (1000, 0)], 80, 'Ordinary Permanent'),
      feat([(200, 0), (800, 0)], 90, 'Variable', 'One Way'),
      # 2: two co-located static zones that disagree
      feat([(0, -300), (1000, -300)], 50),
      feat([(0, -300), (1000, -300)], 60, 'Ordinary Permanent'),
      # 1: two different-limit parallel roads 20 m apart
      feat([(0, -600), (1000, -600)], 60, 'Ordinary Permanent'),
      feat([(0, -620), (1000, -620)], 50),
      # 5: a zone whose last segment is 10 m long (a 2 m kink keeps the vertex), then the next zone
      feat([(0, -900), (490, -900), (500, -898)], 60, 'Ordinary Permanent'),
      feat([(500, -898), (1500, -698)], 80, 'Ordinary Permanent'),
      # 3: a chain for the dead reckoning: 60 (0..500) -> 80 (500..1500) -> junction with two
      # branches within 30 deg (no unique successor)
      feat([(0, -1500), (500, -1500)], 60, 'Ordinary Permanent'),
      feat([(500, -1500), (1500, -1500)], 80, 'Ordinary Permanent'),
      feat([(1500, -1500), (2000, -1450)], 70),
      feat([(1500, -1500), (2000, -1550)], 90),
      # 3: a 60 road with a 40 ramp leaving it at x=300 (20 deg) and a 60 one leaving at x=600
      feat([(0, -2000), (1000, -2000)], 60, 'Ordinary Permanent'),
      feat([(300, -2000), (600, -1891)], 40),
      feat([(0, -2200), (1000, -2200)], 60, 'Ordinary Permanent'),
      feat([(600, -2200), (900, -2091)], 60, 'Ordinary Permanent'),
      # 3: a Variable 90 in two parts (200..500, 500..800) on top of a Permanent 80 (0..1000)
      feat([(0, -2500), (1000, -2500)], 80, 'Ordinary Permanent'),
      feat([[(200, -2500), (500, -2500)], [(500, -2500), (800, -2500)]], 90, 'Variable', 'One Way', multi=True),
    ]
    cls.tmp = tempfile.mkdtemp(prefix='nswz_test2_')
    B = bi.build_arrays(F, [school_rec(9, 'X', 9000, 9100, 9000, 9100)], 1.0, log=lambda s: None)
    cls.path = os.path.join(cls.tmp, 'fix.npz')
    bi.write_index(B['arrays'], cls.path)

  @classmethod
  def tearDownClass(cls):
    shutil.rmtree(cls.tmp, ignore_errors=True)

  def m(self, **kw):
    return nz.Matcher(self.path, calendar=ALWAYS, **kw)

  drive = _Base.drive

  # ---- defect 2: co-located Variable over Permanent, decided by rule
  def test_variable_over_permanent_is_a_rule(self):
    m = self.m()
    rs = self.drive(m, [(x, 1) for x in range(100, 700, 20)], 90.0)
    ks = [r['limit_kph'] for r in rs]
    self.assertEqual(ks[0], 80)  # before the Variable line starts
    self.assertTrue(all(k == 90 for k in ks[7:]), ks)  # on top of it, eastbound: the Variable wins
    self.assertEqual(rs[-1]['zone_type'], 'Variable')
    self.assertEqual(rs[-1]['colocated'], 'resolved')
    self.assertTrue(('the higher limit') in (rs[-1]['reason']))
    self.assertTrue(all(r['confidence'] >= 0.8 for r in rs[7:]))
    m.reset()
    rs = self.drive(m, [(x, 1) for x in range(700, 300, -20)], 270.0)  # westbound: One Way does not apply
    self.assertTrue(all(r['limit_kph'] == 80 for r in rs), [r['limit_kph'] for r in rs])
    m = self.m(variable_overrides=False)
    rs = self.drive(m, [(x, 1) for x in range(300, 700, 20)], 90.0)
    self.assertTrue(all(r['limit_kph'] is None for r in rs[1:]), [r['limit_kph'] for r in rs])

  def test_colocated_static_zones_are_withheld(self):
    m = self.m()
    rs = self.drive(m, [(x, -299) for x in range(100, 600, 20)], 90.0)
    self.assertTrue(all(r['limit_kph'] is None for r in rs), [r['limit_kph'] for r in rs])
    self.assertEqual(rs[-1]['state'], 'ambiguous')
    self.assertTrue(('co-located lines disagree') in (rs[-1]['reason']))

  # ---- defect 1: incumbency never publishes a tie
  def test_exact_tie_is_never_published(self):
    for hz in (1, 10):
      m = self.m()
      n = 5 * hz
      rs = self.drive(m, [(100 + i * 15.0 / hz, -610) for i in range(n)], 90.0, dt_s=1.0 / hz)
      self.assertTrue(all(r['limit_kph'] is None for r in rs), (hz, [r['limit_kph'] for r in rs]))
      self.assertTrue(all(r['state'] == 'ambiguous' for r in rs))

  def test_bonus_needs_time_and_a_clear_win(self):
    m = self.m()
    # 9 m from the 60, 11 m from the 50: gap 0.8. A line that never won clearly gets no bonus.
    rs = self.drive(m, [(100 + 15 * i, -609) for i in range(4)], 90.0)
    self.assertTrue(all(r['limit_kph'] is None for r in rs), [r['limit_kph'] for r in rs])
    # on the 60 for 3 s (clear win), then the same 9/11 m position: now it is published
    rs = self.drive(m, [(200 + 15 * i, -602) for i in range(3)], 90.0, t0=4.0)
    self.assertEqual(rs[-1]['limit_kph'], 60)
    r = self.drive(m, [(250, -609)], 90.0, t0=7.0)[-1]
    self.assertEqual(r['limit_kph'], 60)
    self.assertTrue(0.5 < r['rival_gap'] < 1.0, r['rival_gap'])  # below clear_gap: published only thanks to the bonus

  # ---- defect 6: hysteresis in seconds, not fixes
  def test_switch_takes_the_same_time_at_1_and_10_hz(self):
    for hz in (1, 10):
      m = self.m()
      self.drive(m, [(100 + 15.0 * i / hz, -601) for i in range(4 * hz)], 90.0, dt_s=1.0 / hz)
      t0 = 4.0
      rs = self.drive(m, [(160 + 15.0 * i / hz, -619) for i in range(4 * hz)], 90.0, t0=t0, dt_s=1.0 / hz)
      first50 = next(i for i, r in enumerate(rs) if r['limit_kph'] == 50)
      self.assertAlmostEqual(first50 / hz, 2.0 if hz == 1 else 1.8, delta=0.11, msg=hz)

  # ---- defect 5: a short last segment does not keep an ended zone alive
  def test_short_last_segment_boundary(self):
    for hz in (1, 10):
      m = self.m()
      ks, xs = [], []
      x = 380.0
      v = 20.0
      t = 0.0
      while x < 700:
        y = -900 if x < 490 else -900 + 2.0 * (x - 490) / 10.0 if x < 500 else -898 + 0.2 * (x - 500)
        hd = 90.0 if x < 490 else 90.0 - math.degrees(math.atan2(0.2, 1.0))
        lat, lon = latlon(x, y)
        r = m.update(lat, lon, hd, v, True, 2.0, T_OUT + t, mono_time=t)
        ks.append(r['limit_kph'])
        xs.append(x)
        x += v / hz
        t += 1.0 / hz
      sw = [i for i in range(1, len(ks)) if ks[i] != ks[i - 1]]
      self.assertEqual(len(sw), 1, (hz, ks))
      self.assertEqual((ks[sw[0] - 1], ks[sw[0]]), (60, 80))
      self.assertLessEqual(xs[sw[0]] - 500.0, 3.0 + v / hz + 0.5, hz)

  # ---- defect 3: a hold lasts only as far as the held line, then follows a unique successor
  def test_hold_walks_the_line_and_its_successor(self):
    m = self.m()
    self.drive(m, [(380, -1499), (400, -1499)], 90.0, speed=20.0)
    rs = [m.update(None, None, None, 20.0, False, None, T_OUT + 2 + i, mono_time=2.0 + i) for i in range(80)]
    ks = [r['limit_kph'] for r in rs]
    along = [r.get('dr_m') for r in rs]
    # 100 m of the 60 left, then the 80 for 1000 m, then a Y junction (70 one way, 90 the other): no unique successor.
    # Past the junction the LOWER branch is published (never a value above a road the car may be on), and once the
    # hold ends that value is held (state 7) until GPS returns.
    for k, a in zip(ks, along, strict=False):
      if a is None:
        continue
      if a < 98:
        self.assertEqual(k, 60, a)
      elif 102 < a < 1095:
        self.assertEqual(k, 80, a)
      elif a > 1105:
        self.assertEqual(k, 70, a)
    self.assertTrue(any(k == 80 for k in ks))
    last = rs[-1]
    self.assertEqual((last['state'], last['limit_kph']), ('dr_ended', 70))
    self.assertTrue(('dead reckoning ended') in (last['reason']))
    self.assertTrue(('different limits') in (last['reason']))  # the Y junction: 70 one way, 90 the other, no yaw to choose

  def test_hold_ends_where_a_line_branches_off(self):
    m = self.m()
    self.drive(m, [(80, -1999), (100, -1999)], 90.0, speed=20.0)
    rs = [m.update(None, None, None, 20.0, False, None, T_OUT + 2 + i, mono_time=2.0 + i) for i in range(30)]
    for r in rs:
      a = r.get('dr_m')
      if a is not None and a < 195:
        self.assertEqual(r['limit_kph'], 60, a)
      if a is not None and a > 275:  # branch at 200 m + 50 m slack + one 20 m step: ended, the last value held
        self.assertEqual(r['state'], 'dr_ended', a)
        self.assertIsNotNone(r['limit_kph'], a)
        self.assertLessEqual(r['limit_kph'], 60, a)
    self.assertTrue(('different limits') in (rs[-1]['reason']))
    # a branch with the SAME limit: held to the end of the line it leaves from (x=1000), not beyond
    m.reset()
    self.drive(m, [(80, -2199), (100, -2199)], 90.0, speed=20.0)
    rs = [m.update(None, None, None, 20.0, False, None, T_OUT + 2 + i, mono_time=2.0 + i) for i in range(60)]
    held = [r['dr_m'] for r in rs if r['state'] == 'dead_reckoning']
    self.assertTrue(850 <= max(held) <= 960, max(held))
    self.assertTrue(all(r['limit_kph'] == 60 for r in rs if r['state'] == 'dead_reckoning'))

  def test_hold_follows_a_variable_line_over_a_static_one(self):
    m = self.m()
    self.drive(m, [(280, -2499), (300, -2499)], 90.0, speed=20.0)
    rs = [m.update(None, None, None, 20.0, False, None, T_OUT + 2 + i, mono_time=2.0 + i) for i in range(45)]
    seq = []
    for r in rs:
      if not seq or seq[-1][0] != r['limit_kph']:
        seq.append((r['limit_kph'], r.get('dr_m')))
    self.assertEqual([k for k, _ in seq], [90, 80], seq)
    self.assertAlmostEqual(seq[1][1], 500.0, delta=25.0)  # the Variable ends at x=800: 500 m on
    ended = next(r for r in rs if r['state'] == 'dr_ended')
    self.assertAlmostEqual(ended['dr_m'], 750.0, delta=21.0)  # the Permanent ends at x=1000, +50 m (20 m steps)
    self.assertEqual(ended['limit_kph'], 80)  # ... and its 80 is held until GPS returns

  # ---- defect 4: after a loss, a degraded fix does not re-acquire
  def test_reacquire_needs_accuracy(self):
    m = self.m()
    self.drive(m, [(380, -1499), (400, -1499)], 90.0, speed=20.0)
    for i in range(3):
      m.update(None, None, None, 20.0, False, None, T_OUT + 2 + i, mono_time=2.0 + i)
    lat, lon = latlon(470, -1480)  # 20 m off, 22 m accuracy: claims ok, not trusted
    r = m.update(lat, lon, 90.0, 20.0, True, 22.0, T_OUT + 5, mono_time=5.0)
    self.assertEqual(r['state'], 'dead_reckoning')
    self.assertTrue(('re-acquiring') in (r['reason']))
    lat, lon = latlon(490, -1499)
    r = m.update(lat, lon, 90.0, 20.0, True, 14.0, T_OUT + 6, mono_time=6.0)
    self.assertEqual(r['state'], 'dead_reckoning')  # one fix at 14 m is not enough
    lat, lon = latlon(510, -1499)
    r = m.update(lat, lon, 90.0, 20.0, True, 14.0, T_OUT + 7, mono_time=7.0)
    self.assertNotEqual(r['state'], 'dead_reckoning')  # two in a row are
    self.assertEqual(r['limit_kph'], 80)
    m.reset()
    self.drive(m, [(380, -1499), (400, -1499)], 90.0, speed=20.0)
    m.update(None, None, None, 20.0, False, None, T_OUT + 2, mono_time=2.0)
    lat, lon = latlon(440, -1499)
    self.assertEqual(m.update(lat, lon, 90.0, 20.0, True, 8.0, T_OUT + 3, mono_time=3.0)['state'], 'matched')


class TestLookahead(_Base):
  def test_next_zone_ahead(self):
    m = self.matcher()
    self.drive(m, [(290, 1), (300, 1)], 90.0, unix=T_OUT)
    la = m.lookahead(1000)
    self.assertEqual(la['ahead_kph'], 80)
    self.assertAlmostEqual(la['ahead_dist_m'], 200.0, delta=21.0)

  def test_school_ahead_when_active(self):
    m = self.matcher()
    self.drive(m, [(90, 1), (100, 1)], 90.0, unix=T_IN)
    la = m.lookahead(500)
    self.assertEqual(la['ahead_kind'], 'school')
    self.assertEqual(la['ahead_kph'], 40)
    self.assertAlmostEqual(la['ahead_dist_m'], 100.0, delta=11.0)
    m.reset()
    self.drive(m, [(90, 1), (100, 1)], 90.0, unix=T_OUT)
    la = m.lookahead(500)
    self.assertEqual(la['ahead_kind'], 'zone')
    self.assertEqual(la['ahead_kph'], 80)

  def test_westbound_runs_out_of_data(self):
    m = self.matcher()
    self.drive(m, [(110, 1), (100, 1)], 270.0, unix=T_OUT)
    la = m.lookahead(500)
    self.assertIsNone(la['ahead_kph'])
    self.assertTrue(('no unique line') in (la['ahead_stop']))

  def test_no_heading_no_lookahead(self):
    m = self.matcher()
    self.drive(m, [(100, 1)], 90.0, speed=0.0)
    la = m.lookahead()
    self.assertIsNone(la['ahead_kph'])
    self.assertEqual(la['ahead_stop'], 'no match / no direction')


if __name__ == '__main__':
  unittest.main()
