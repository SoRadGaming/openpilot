"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): loading and validating an index file: format version, sha256 and size from the manifest,
attribution, corrupt files, and the aligned (mmap-able) copy.

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from openpilot.sunnypilot.mapd.nsw_zones import index as nzi
from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import ALWAYS, MiniIndex, drive, feat


class TestIndex(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.mi = MiniIndex([feat([(0, 0), (1000, 0)], 60, 'Ordinary Permanent')])

  @classmethod
  def tearDownClass(cls):
    cls.mi.close()

  def setUp(self):
    self.tmp = tempfile.mkdtemp(prefix='nswz_idx_')
    self.npz = os.path.join(self.tmp, nzi.INDEX_NAME)
    shutil.copy(self.mi.path, self.npz)
    self.man = os.path.join(self.tmp, nzi.MANIFEST_NAME)
    self.write_manifest()

  def tearDown(self):
    shutil.rmtree(self.tmp, ignore_errors=True)

  def write_manifest(self, **over):
    m = {
      'format_version': nzi.FORMAT_VERSION,
      'sha256': nzi.sha256_file(self.npz),
      'bytes': os.path.getsize(self.npz),
      'data_version': '2026-09-29',
      'attribution': 'Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0.',
    }
    m.update(over)
    for k in [k for k, v in m.items() if v is None]:
      del m[k]
    with open(self.man, 'w', encoding='utf-8') as f:
      json.dump(m, f)
    return m

  def test_verify_ok(self):
    info = nzi.verify_index(self.npz, self.man)
    self.assertEqual(info.format_version, nzi.FORMAT_VERSION)
    self.assertEqual(info.data_version, '2026-09-29')
    self.assertEqual(info.sha256, nzi.sha256_file(self.npz))
    self.assertTrue(('Transport for NSW') in (info.attribution))
    # without a manifest: the file alone (format + attribution inside)
    info = nzi.verify_index(self.npz)
    self.assertEqual(info.data_version, 'test')

  def test_sha_mismatch(self):
    self.write_manifest(sha256='0' * 64)
    with self.assertRaisesRegex(nzi.IndexInvalid, 'sha256'):
      nzi.verify_index(self.npz, self.man)

  def test_size_mismatch(self):
    self.write_manifest(bytes=12)
    with self.assertRaisesRegex(nzi.IndexInvalid, 'size'):
      nzi.verify_index(self.npz, self.man)

  def test_unsupported_format_in_manifest(self):
    m = self.write_manifest(format_version=99)
    self.assertFalse(nzi.manifest_compatible(m))
    self.assertTrue(nzi.manifest_compatible({'format_version': nzi.FORMAT_VERSION}))
    self.assertFalse(nzi.manifest_compatible({'format_version': 'x'}))
    with self.assertRaisesRegex(nzi.IndexInvalid, 'not supported'):
      nzi.verify_index(self.npz, self.man)

  def test_manifest_missing_fields(self):
    self.write_manifest(sha256=None)
    with self.assertRaisesRegex(nzi.IndexInvalid, 'lacks'):
      nzi.verify_index(self.npz, self.man)
    with open(self.man, 'w') as f:
      f.write('not json')
    with self.assertRaises(nzi.IndexInvalid):
      nzi.read_manifest(self.man)

  def test_corrupt_file(self):
    with open(self.npz, 'wb') as f:
      f.write(os.urandom(4096))
    self.write_manifest()
    with self.assertRaises(nzi.IndexInvalid):
      nzi.verify_index(self.npz, self.man)
    with self.assertRaises(nzi.IndexInvalid):
      nz.Matcher(self.npz)
    with self.assertRaises(nzi.IndexInvalid):
      nzi.verify_index(os.path.join(self.tmp, 'missing.npz'))

  def test_wrong_format_version_inside_the_file(self):
    a = nzi.load_index(self.npz)
    meta = nzi.read_meta(a)
    meta['format_version'] = 99
    a['meta_json'] = np.frombuffer(json.dumps(meta).encode(), np.uint8).copy()
    bad = os.path.join(self.tmp, 'bad.npz')
    nzi.write_npz(a, bad)
    with self.assertRaisesRegex(nzi.IndexInvalid, 'format 99'):
      nzi.load_index(bad)

  def test_format_1_is_still_read(self):
    a = nzi.load_index(self.npz)
    meta = nzi.read_meta(a)
    meta['format_version'] = 1
    a['meta_json'] = np.frombuffer(json.dumps(meta).encode(), np.uint8).copy()
    del a['calendar_json']  # the P1 index had no embedded calendar
    old = os.path.join(self.tmp, 'p1.npz')
    nzi.write_npz(a, old)
    m = nz.Matcher(old, calendar=None)
    self.assertTrue(('school_days.py') in (m.calendar_desc))

  def test_missing_member(self):
    a = nzi.load_index(self.npz)
    del a['b_part_speed']
    bad = os.path.join(self.tmp, 'bad.npz')
    nzi.write_npz(a, bad)
    with self.assertRaisesRegex(nzi.IndexInvalid, 'b_part_speed'):
      nzi.load_index(bad)

  def test_stored_copy_maps_and_matches(self):
    a = nzi.load_index(self.npz)
    stored = os.path.join(self.tmp, 'stored.npz')
    nzi.write_stored_aligned(a, stored)
    b = nzi.load_index(stored, mmap=True)
    for k, v in a.items():
      self.assertTrue(np.array_equal(np.asarray(b[k]), v), k)
    m = nz.Matcher(stored, calendar=ALWAYS, mmap=True)
    self.assertEqual(drive(m, [(100, 1), (120, 1)], 90.0)[-1]['limit_kph'], 60)
    with self.assertRaises(nzi.IndexInvalid):
      nzi.load_index(self.npz, mmap=True)  # compressed: cannot be mapped

  def test_identical_arrays_identical_bytes(self):
    a = nzi.load_index(self.npz)
    p1, p2 = os.path.join(self.tmp, 'a.npz'), os.path.join(self.tmp, 'b.npz')
    nzi.write_npz(a, p1)
    nzi.write_npz(a, p2)
    self.assertEqual(nzi.sha256_file(p1), nzi.sha256_file(p2))


if __name__ == '__main__':
  unittest.main()
