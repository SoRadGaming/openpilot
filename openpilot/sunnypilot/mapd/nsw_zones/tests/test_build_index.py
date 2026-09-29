"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the builder CLI end to end on synthetic GeoJSON + schoolzones.zip: the three published files,
the manifest the device verifies, and every gate that must stop a publish.

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

import numpy as np

from openpilot.sunnypilot.mapd.nsw_zones import build_index as bi
from openpilot.sunnypilot.mapd.nsw_zones import index as nzi
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import feat, school_rec

FEATS = [
  feat([(0, 0), (500, 0)], 60, 'Ordinary Permanent'),
  feat([(500, 0), (1000, 0)], 80, 'Ordinary Permanent'),
  feat([(0, 50), (1000, 50)], 50),
  feat([(200, 0), (300, 0)], 40, 'School'),
  feat([(0, -300), (1000, -300)], 90, 'Variable', 'One Way'),
  feat([(0, -300), (1000, -300)], 80, 'Ordinary Permanent'),
]


class TestBuilderCli(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.mkdtemp(prefix='nswz_build_')
    self.gj = os.path.join(self.tmp, 'speed_zones.geojson')
    self.write_geojson(FEATS)
    self.zip = os.path.join(self.tmp, 'schoolzones.zip')
    with zipfile.ZipFile(self.zip, 'w') as z:
      recs = [{'generated': '2026-09-07T00:00:00'}, school_rec(111, 'CLI TEST SCHOOL', 180, 320, -15, 15)]
      z.writestr('SchoolZones.json', json.dumps(recs))
    self.out = os.path.join(self.tmp, 'out')

  def tearDown(self):
    shutil.rmtree(self.tmp, ignore_errors=True)

  def write_geojson(self, feats):
    with open(self.gj, 'w', encoding='utf-8') as f:
      json.dump({'type': 'FeatureCollection', 'features': feats}, f)

  def main(self, *extra, today='2026-09-29'):
    argv = [
      '--speedzones',
      self.gj,
      '--schoolzones',
      self.zip,
      '--out',
      self.out,
      '--min-base-parts',
      '1',
      '--min-schools',
      '1',
      '--self-test-points',
      '20',
      '--today',
      today,
      *extra,
    ]
    with contextlib.redirect_stdout(io.StringIO()):
      return bi.main(argv)

  def test_build_writes_the_three_files_and_they_verify(self):
    self.assertEqual(self.main('--data-version', '2026-09-29'), 0)
    for n in (nzi.INDEX_NAME, nzi.MANIFEST_NAME, nzi.ATTRIBUTION_NAME, 'build_report.json'):
      self.assertTrue(os.path.exists(os.path.join(self.out, n)), n)
    with open(os.path.join(self.out, nzi.MANIFEST_NAME), encoding='utf-8') as f:
      man = json.load(f)
    for k in nzi.MANIFEST_REQUIRED + ('feature_count', 'calendar_coverage', 'sources', 'gates'):
      self.assertTrue((k) in (man))
    self.assertEqual(man['format_version'], nzi.FORMAT_VERSION)
    self.assertEqual(man['feature_count'], len(FEATS))
    self.assertTrue(man['gates']['passed'])
    self.assertNotIn(self.tmp, json.dumps(man))  # no local paths are published
    info = nzi.verify_index(os.path.join(self.out, nzi.INDEX_NAME), os.path.join(self.out, nzi.MANIFEST_NAME))
    self.assertEqual(info.data_version, '2026-09-29')
    with open(os.path.join(self.out, nzi.ATTRIBUTION_NAME), encoding='utf-8') as f:
      txt = f.read()
    self.assertTrue(('Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0.') in (txt))
    self.assertTrue(('Not endorsed by Transport for NSW.') in (txt))
    a = nzi.load_index(os.path.join(self.out, nzi.INDEX_NAME))
    cal = json.loads(bytes(a['calendar_json']).decode('utf-8'))
    self.assertEqual(cal['format'], 'nsw_school_days')
    self.assertEqual(nzi.read_meta(a)['data_version'], '2026-09-29')

  def test_previous_manifest_feature_count_gate(self):
    prev = os.path.join(self.tmp, 'prev.json')
    with open(prev, 'w') as f:
      json.dump({'feature_count': len(FEATS)}, f)
    self.assertEqual(self.main('--previous-manifest', prev), 0)
    with open(prev, 'w') as f:
      json.dump({'feature_count': 1000}, f)
    shutil.rmtree(self.out)
    self.assertEqual(self.main('--previous-manifest', prev), 2)
    self.assertFalse(os.path.exists(os.path.join(self.out, nzi.INDEX_NAME)))  # nothing to publish
    with open(os.path.join(self.out, 'build_report.json'), encoding='utf-8') as f:
      rep = json.load(f)
    self.assertFalse(rep['gates']['passed'])
    self.assertTrue(('feature count') in (' '.join(rep['gates']['failures'])))
    with open(prev, 'w') as f:
      f.write('garbage')
    self.assertEqual(self.main('--previous-manifest', prev), 2)

  def test_review_gates_stop_a_raise_until_a_person_allows_it(self):
    """Changes that could RAISE a published limit (SLA follows a new limit >= 80 by itself) or drop school zones fail
    the automatic publish; --allow-review (the Action's manual force) lets them through, recorded in the manifest."""
    self.assertEqual(self.main('--data-version', '2026-09-22'), 0)
    prev = os.path.join(self.tmp, 'prev')
    shutil.copytree(self.out, prev)
    shutil.rmtree(self.out)
    pm, pi = os.path.join(prev, nzi.MANIFEST_NAME), os.path.join(prev, nzi.INDEX_NAME)

    def run(feats, *extra):
      self.write_geojson(feats)
      shutil.rmtree(self.out, ignore_errors=True)
      rc = self.main('--previous-manifest', pm, '--previous-index', pi, *extra)
      with open(os.path.join(self.out, 'build_report.json'), encoding='utf-8') as f:
        return rc, json.load(f)

    rc, rep = run(FEATS)
    self.assertEqual(rc, 0, rep['gates'])
    self.assertEqual(rep['review']['limit_diff']['raised_km'], 0.0)
    # a small raise below 90: reported, not blocked
    small = [feat([(0, 0), (500, 0)], 70, 'Ordinary Permanent')] + FEATS[1:]
    rc, rep = run(small)
    self.assertEqual(rc, 0, rep['gates'])
    self.assertAlmostEqual(rep['review']['limit_diff']['raised_km'], 0.5, delta=0.01)
    # a raise to 90+: blocked, nothing written ...
    fast = FEATS[:1] + [feat([(500, 0), (1000, 0)], 90, 'Ordinary Permanent')] + FEATS[2:]
    rc, rep = run(fast)
    self.assertEqual(rc, 2)
    self.assertFalse(os.path.exists(os.path.join(self.out, nzi.INDEX_NAME)))
    self.assertTrue(any(g.startswith(bi.REVIEW) and '90+' in g for g in rep['gates']['failures']), rep['gates'])
    # ... unless a person allowed it
    rc, rep = run(fast, '--allow-review')
    self.assertEqual(rc, 0)
    with open(os.path.join(self.out, nzi.MANIFEST_NAME), encoding='utf-8') as f:
      man = json.load(f)
    self.assertTrue(man['review']['allowed_by_hand'] and man['review']['flagged'])
    self.assertGreater(man['review']['raised_fast_km'], 0.4)
    # a new Variable line (the owner's rule publishes it over the static zone below): blocked
    rc, rep = run(FEATS + [feat([(0, 50), (1000, 50)], 60, 'Variable')])
    self.assertEqual(rc, 2)
    self.assertTrue(any('Variable lines 1 -> 2' in g for g in rep['gates']['failures']), rep['gates'])
    # School lines halved: blocked
    rc, rep = run([f for f in FEATS if f['properties']['Type'] != 'School'])
    self.assertEqual(rc, 2)
    self.assertTrue(any('School lines 1 -> 0' in g for g in rep['gates']['failures']), rep['gates'])

  def test_limit_diff_matches_lines_by_geometry(self):
    B0 = bi.build_arrays(FEATS, [school_rec(111, 'X', 180, 320, -15, 15)], 1.0, log=lambda s: None, data_version='a')
    moved = FEATS[:2] + [feat([(0, 55), (1000, 55)], 90)] + FEATS[3:]  # the 50 street re-drawn 5 m away, now 90
    B1 = bi.build_arrays(moved, [school_rec(111, 'X', 180, 320, -15, 15)], 1.0, log=lambda s: None, data_version='b')
    d = bi.limit_diff(B0['arrays'], B1['arrays'])
    self.assertEqual(d['raised_km'], 0.0)  # not the same geometry: a new line ...
    self.assertAlmostEqual(d['new_fast_km'], 1.0, delta=0.01)  # ... counted as new 90+ km
    d = bi.limit_diff(B0['arrays'], B0['arrays'])
    self.assertEqual((d['raised_km'], d['new_km'], d['lowered_km']), (0.0, 0.0, 0.0))

  def test_feature_count_gate_function(self):
    self.assertTrue(bi.feature_count_gate(100, None)[0])
    tmp = os.path.join(self.tmp, 'p.json')
    for prev, ok in ((100, True), (104, True), (106, False), (94, False)):
      with open(tmp, 'w') as f:
        json.dump({'feature_count': prev}, f)
      self.assertEqual(bi.feature_count_gate(100, tmp)[0], ok, prev)

  def test_calendar_gate(self):
    ok, cov, msg = bi.calendar_gate(__import__('datetime').date(2026, 9, 29))
    self.assertTrue(ok, msg)
    ok, cov, msg = bi.calendar_gate(__import__('datetime').date(2028, 1, 20))
    self.assertFalse(ok)
    self.assertTrue(('update school_days.py') in (msg))
    self.assertEqual(self.main(today='2028-01-20'), 2)

  def test_unknown_values_fail(self):
    bad = FEATS + [feat([(0, 900), (100, 900)], 50, 'Hover Car Lane')]
    self.write_geojson(bad)
    self.assertEqual(self.main(), 2)
    bad = FEATS + [feat([(0, 900), (100, 900)], 50, 'Default', 'Sideways')]
    self.write_geojson(bad)
    self.assertEqual(self.main(), 2)
    f = feat([(0, 900), (100, 900)], 50)
    f['properties']['Speed'] = '500 km/h'
    self.write_geojson(FEATS + [f])
    self.assertEqual(self.main(), 2)

  def test_size_gates(self):
    with contextlib.redirect_stdout(io.StringIO()):
      rc = bi.main(['--speedzones', self.gj, '--schoolzones', self.zip, '--out', self.out, '--today', '2026-09-29', '--self-test-points', '20'])
    self.assertEqual(rc, 2)  # 6 lines, 1 school: far below the real-data sizes

  def test_not_a_feature_collection(self):
    with open(self.gj, 'w') as f:
      json.dump([1, 2, 3], f)
    with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
      self.main()

  def test_self_test_on_a_small_index(self):
    B = bi.build_arrays(FEATS, [school_rec(111, 'X', 180, 320, -15, 15)], 1.0, log=lambda s: None, data_version='t')
    p = os.path.join(self.tmp, 'x.npz')
    bi.write_index(B['arrays'], p)
    ok, rep = bi.self_test(B['arrays'], p, n=20, log=lambda s: None)
    self.assertTrue(ok, rep)
    self.assertEqual(rep['round_trip_mismatch'], [])
    # a file that does not decode to the arrays built fails
    B['arrays']['b_part_speed'] = B['arrays']['b_part_speed'] + np.uint8(1)
    ok, rep = bi.self_test(B['arrays'], p, n=5, log=lambda s: None)
    self.assertFalse(ok)
    self.assertTrue(('b_part_speed') in (rep['round_trip_mismatch']))

  def test_runs_standalone_without_openpilot(self):
    """The Action runs build_index.py from a sparse checkout: it must not need the openpilot package."""
    here = os.path.dirname(os.path.abspath(bi.__file__))
    env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
    code = (
      'import sys, runpy; sys.modules["openpilot"] = None; '  # any "import openpilot..." now fails
      + f'sys.argv = ["build_index.py", "--speedzones", {self.gj!r}, "--schoolzones", {self.zip!r}, "--out", {self.out!r}, '
      + '"--min-base-parts", "1", "--min-schools", "1", "--self-test-points", "10", "--today", "2026-09-29"]; '
      + f'runpy.run_path({os.path.join(here, "build_index.py")!r}, run_name="__main__")'
    )
    r = subprocess.run([sys.executable, '-c', code], cwd=self.tmp, env=env, capture_output=True, text=True, timeout=300)
    self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
    self.assertTrue(os.path.exists(os.path.join(self.out, nzi.INDEX_NAME)))


if __name__ == '__main__':
  unittest.main()
