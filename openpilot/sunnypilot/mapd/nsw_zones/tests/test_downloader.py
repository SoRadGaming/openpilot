"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the data-set downloader. The decision is a pure function, tested condition by condition; fetch() is
run against an in-memory "release" (no network), and the stateful updater against a fake deviceState/pandaStates.
The costly failures on a device: a download while driving or on cellular, a bad file installed over a good one, a
half-written file used, a download loop, a check that raises into mapd_manager, and a request that leaks anything.
"""

import hashlib
import json
import os
import shutil
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from openpilot.sunnypilot.mapd.nsw_zones import ATTRIBUTION, MODE_LIVE, MODE_OFF
from openpilot.sunnypilot.mapd.nsw_zones import downloader as dl
from openpilot.sunnypilot.mapd.nsw_zones.index import ATTRIBUTION_NAME, INDEX_NAME, MANIFEST_NAME, IndexInvalid
from openpilot.sunnypilot.mapd.nsw_zones.matcher import Matcher
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import MiniIndex, feat, latlon
from openpilot.sunnypilot.mapd.osm_auto_update import OFFROAD_SETTLE_S, log

NOW = 1_790_000_000.0  # 2026-09
BASE = "https://example.invalid/nswzones-latest/"


def due(**over):
  kw = {
    "mode": MODE_LIVE,
    "auto_enabled": True,
    "forced": False,
    "installed": True,
    "last_check": NOW - dl.MAX_AGE_S - 60,
    "now": NOW,
    "time_valid": True,
    "device_state_ok": True,
    "offroad_for": OFFROAD_SETTLE_S + 1,
    "network_up": True,
    "unmetered": True,
    "busy": False,
    "retry_wait": False,
  }
  kw.update(over)
  return dl.update_due(**kw)


class TestUpdateDue(unittest.TestCase):
  def test_all_conditions_met_is_due(self):
    self.assertEqual(due(), (True, ""))

  def test_each_condition_blocks_on_its_own(self):
    for over in (
      {"mode": MODE_OFF},
      {"auto_enabled": False},
      {"time_valid": False},
      {"device_state_ok": False},
      {"offroad_for": -1.0},
      {"offroad_for": OFFROAD_SETTLE_S - 1},
      {"network_up": False},
      {"unmetered": False},
      {"busy": True},
      {"retry_wait": True},
      {"last_check": NOW - 3600},
    ):
      ok, why = due(**over)
      self.assertFalse(ok, over)
      self.assertTrue(why, over)

  def test_the_week_boundary(self):
    self.assertFalse(due(last_check=NOW - dl.MAX_AGE_S + 60)[0])
    self.assertTrue(due(last_check=NOW - dl.MAX_AGE_S - 60)[0])
    self.assertTrue(due(last_check=0.0)[0], "never checked")

  def test_the_first_download_is_automatic(self):
    """No data installed: due with the weekly refresh off and before the clock is set - but never onroad, on
    cellular or metered, and not when the mode is off."""
    self.assertTrue(due(installed=False, auto_enabled=False, time_valid=False, last_check=NOW)[0])
    for over in ({"mode": MODE_OFF}, {"offroad_for": -1.0}, {"unmetered": False}, {"network_up": False}):
      self.assertFalse(due(installed=False, **over)[0], over)

  def test_the_button(self):
    """Forced: any mode, any age, a metered network, no settle time - but parked and with a network."""
    self.assertTrue(due(forced=True, mode=MODE_OFF, auto_enabled=False, last_check=NOW, unmetered=False, offroad_for=0.0, retry_wait=True, time_valid=False)[0])
    self.assertEqual(due(forced=True, offroad_for=-1.0), (False, "car must be parked"))
    self.assertEqual(due(forced=True, network_up=False), (False, "no network"))
    self.assertFalse(due(forced=True, busy=True)[0])
    self.assertFalse(due(forced=True, device_state_ok=False)[0])


# ============================================================================ a fake release
class FakeResp:
  def __init__(self, body: bytes | None, status: int = 200):
    self.body = body
    self.status = status
    self.closed = False

  def raise_for_status(self):
    if self.status != 200:
      raise OSError(f"HTTP {self.status}")

  def iter_content(self, n):
    for i in range(0, len(self.body or b''), n):
      yield (self.body or b'')[i : i + n]

  def close(self):
    self.closed = True


class Release:
  """base URL -> files. Records every URL requested."""

  def __init__(self, npz: bytes, manifest: dict | None = None, attribution: bytes = ATTRIBUTION.encode()):
    self.files = {INDEX_NAME: npz, ATTRIBUTION_NAME: attribution}
    m = {"format_version": 2, "sha256": hashlib.sha256(npz).hexdigest(), "bytes": len(npz), "data_version": "2026-09-29", "attribution": ATTRIBUTION}
    m.update(manifest or {})
    self.files[MANIFEST_NAME] = json.dumps(m).encode()
    self.urls: list[str] = []

  def get(self, url, timeout=None):
    self.urls.append(url)
    name = url[len(BASE) :] if url.startswith(BASE) else None
    if name not in self.files:
      return FakeResp(None, 404)
    return FakeResp(self.files[name])


def road_index():
  return MiniIndex([feat([(0, -500), (0, 500)], 60)])


class FetchTestBase(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    with road_index() as mi:
      with open(mi.path, 'rb') as f:
        cls.npz = f.read()
    with MiniIndex([feat([(0, -500), (0, 500)], 80)]) as mi:
      with open(mi.path, 'rb') as f:
        cls.npz2 = f.read()

  def setUp(self):
    self.dir = tempfile.mkdtemp(prefix='nswz_dl_')
    self.addCleanup(shutil.rmtree, self.dir, True)

  def fetch(self, release, installed_sha=None, **kw):
    return dl.fetch(self.dir, installed_sha, base_url=BASE, get=release.get, **kw)


class TestFetch(FetchTestBase):
  def test_installs_and_the_matcher_reads_it(self):
    rel = Release(self.npz)
    r = self.fetch(rel)
    self.assertTrue(r.ok and r.changed, r)
    self.assertEqual(r.data_version, "2026-09-29")
    path, cur = dl.installed_index(self.dir)
    self.assertEqual(cur['sha256'], hashlib.sha256(self.npz).hexdigest())
    self.assertEqual(cur['data_version'], "2026-09-29")
    m = Matcher(path, mmap=True)
    lat, lon = latlon(0, 0)
    res = m.update(lat, lon, 0.0, 15.0, True, 3.0, NOW, mono_time=0.0)
    self.assertEqual(res['limit_kph'], 60)
    # the directory holds the version, the switch and nothing half-written
    names = sorted(os.listdir(self.dir))
    self.assertEqual(names, sorted([dl.CURRENT_NAME, cur['dir']]))
    self.assertEqual(sorted(os.listdir(os.path.join(self.dir, cur['dir']))), sorted([dl.STORED_NAME, MANIFEST_NAME, ATTRIBUTION_NAME]))

  def test_only_the_three_fixed_urls_are_requested(self):
    """No position, no query string, no identifier: the same three GETs for every car."""
    rel = Release(self.npz)
    self.fetch(rel)
    self.assertEqual(rel.urls, [BASE + MANIFEST_NAME, BASE + INDEX_NAME, BASE + ATTRIBUTION_NAME])
    for u in rel.urls:
      self.assertFalse('?' in u or '#' in u, u)

  def test_up_to_date_downloads_nothing_more(self):
    rel = Release(self.npz)
    self.fetch(rel)
    rel.urls.clear()
    r = self.fetch(rel, installed_sha=hashlib.sha256(self.npz).hexdigest())
    self.assertTrue(r.ok)
    self.assertFalse(r.changed)
    self.assertEqual(r.why, "up to date")
    self.assertEqual(rel.urls, [BASE + MANIFEST_NAME])

  def test_a_new_version_replaces_the_old_one(self):
    self.fetch(Release(self.npz))
    old = (dl.read_current(self.dir) or {})['dir']
    rel2 = Release(self.npz2, {"data_version": "2026-10-06"})
    r = self.fetch(rel2, installed_sha=hashlib.sha256(self.npz).hexdigest())
    self.assertTrue(r.changed, r)
    cur = dl.read_current(self.dir) or {}
    self.assertNotEqual(cur['dir'], old)
    self.assertFalse(os.path.exists(os.path.join(self.dir, old)), "the old version is pruned")
    lat, lon = latlon(0, 0)
    self.assertEqual(Matcher(dl.installed_index(self.dir)[0], mmap=True).update(lat, lon, 0.0, 15.0, True, 3.0, NOW, mono_time=0.0)['limit_kph'], 80)

  def assert_nothing_changed(self, before):
    self.assertEqual(dl.read_current(self.dir), before)
    if before is not None:
      dl.installed_index(self.dir)  # still whole and verifiable
    self.assertFalse(os.path.exists(os.path.join(self.dir, '.staging')))

  def test_bad_downloads_never_replace_a_good_install(self):
    self.fetch(Release(self.npz))
    before = dl.read_current(self.dir)
    assert before is not None
    sha = before['sha256']
    corrupt = bytearray(self.npz2)
    corrupt[len(corrupt) // 2] ^= 0xFF
    cases = {
      "sha256 mismatch": Release(self.npz2, {"sha256": hashlib.sha256(b'x').hexdigest()}),
      "size": Release(self.npz2, {"bytes": len(self.npz2) + 10}),
      "too big": Release(self.npz2, {"bytes": dl.MAX_INDEX_BYTES + 1}),
      "not supported": Release(self.npz2, {"format_version": 99}),
      "corrupt, matching sha": Release(bytes(corrupt)),
      "no ATTRIBUTION.txt": Release(self.npz2),
      "not json": Release(self.npz2),
    }
    del cases["no ATTRIBUTION.txt"].files[ATTRIBUTION_NAME]
    cases["not json"].files[MANIFEST_NAME] = b'<html>'
    cases["no index"] = Release(self.npz2)
    del cases["no index"].files[INDEX_NAME]
    for name, rel in cases.items():
      r = self.fetch(rel, installed_sha=sha)
      self.assertFalse(r.ok, name)
      self.assertFalse(r.changed, name)
      self.assert_nothing_changed(before)

  def test_a_manifest_that_lies_about_the_size_cannot_fill_the_disk(self):
    rel = Release(self.npz)
    m = json.loads(rel.files[MANIFEST_NAME])
    m['bytes'] = 1000
    rel.files[MANIFEST_NAME] = json.dumps(m).encode()
    r = self.fetch(rel)
    self.assertFalse(r.ok)
    self.assert_nothing_changed(None)

  def test_an_incompatible_format_is_not_downloaded(self):
    rel = Release(self.npz, {"format_version": 99})
    r = self.fetch(rel)
    self.assertFalse(r.ok)
    self.assertEqual(rel.urls, [BASE + MANIFEST_NAME])

  def test_network_errors_are_results_not_exceptions(self):
    def boom(url, timeout=None):
      raise ConnectionError("no route")

    r = dl.fetch(self.dir, None, base_url=BASE, get=boom)
    self.assertFalse(r.ok)
    self.assertTrue(r.why.startswith("failed"), r.why)

  def test_cancel_stops_the_download_and_installs_nothing(self):
    cancel = threading.Event()
    cancel.set()
    r = self.fetch(Release(self.npz), cancel=cancel)
    self.assertFalse(r.ok)
    self.assertEqual(r.why, "cancelled")
    self.assert_nothing_changed(None)

  def test_progress_is_reported(self):
    seen = []
    self.fetch(Release(self.npz), status=lambda s, f: seen.append(s))
    self.assertEqual(seen[0], 'checking')
    assert 'downloading' in seen and 'installing' in seen

  def test_cancel_during_the_install_steps_installs_nothing(self):
    """The steps after the download (decode, the aligned copy, the smoke test) are seconds each on the device: a car
    starting in the middle of them stops the install before the new version is switched in."""
    for step in ('verify_index', 'load_index', 'write_stored_aligned', '_smoke_test'):
      cancel = threading.Event()
      real = getattr(dl, step)

      def cut(*a, _real=real, _cancel=cancel, **k):
        out = _real(*a, **k)
        _cancel.set()  # ignition while this step ran
        return out

      with mock.patch.object(dl, step, cut):
        r = self.fetch(Release(self.npz), cancel=cancel)
      self.assertEqual((r.ok, r.why), (False, "cancelled"), step)
      self.assert_nothing_changed(None)


class TestInstalledIndex(FetchTestBase):
  def test_nothing_installed(self):
    with self.assertRaises(IndexInvalid):
      dl.installed_index(self.dir)

  def test_a_damaged_file_is_refused(self):
    self.fetch(Release(self.npz))
    path, _ = dl.installed_index(self.dir)
    with open(path, 'r+b') as f:
      f.seek(os.path.getsize(path) // 2)
      b = f.read(1)
      f.seek(-1, 1)
      f.write(bytes([b[0] ^ 0xFF]))
    with self.assertRaises(IndexInvalid):
      dl.installed_index(self.dir)
    dl.installed_index(self.dir, verify=False)  # the cheap check only looks at the size

  def test_a_truncated_file_is_refused_even_unverified(self):
    self.fetch(Release(self.npz))
    path, _ = dl.installed_index(self.dir)
    with open(path, 'r+b') as f:
      f.truncate(100)
    with self.assertRaises(IndexInvalid):
      dl.installed_index(self.dir, verify=False)

  def test_mark_bad_sets_the_install_aside(self):
    self.fetch(Release(self.npz))
    cur = dl.read_current(self.dir)
    assert cur is not None
    self.assertFalse(dl.mark_bad(self.dir, "some_other_version"), "an install that finished meanwhile is kept")
    self.assertIsNotNone(dl.read_current(self.dir))
    self.assertTrue(dl.mark_bad(self.dir, cur['dir']))
    self.assertIsNone(dl.read_current(self.dir))
    # the next check downloads it again (installed_sha None), as a first download
    r = self.fetch(Release(self.npz))
    self.assertTrue(r.ok and r.changed, r.why)
    self.assertIsNotNone(dl.read_current(self.dir))

  def test_current_json_cannot_point_outside_the_directory(self):
    self.fetch(Release(self.npz))
    for bad in ("../x", "/etc", "..", ""):
      dl.write_json_atomic(os.path.join(self.dir, dl.CURRENT_NAME), {"dir": bad})
      self.assertIsNone(dl.read_current(self.dir), bad)


# ============================================================================ the stateful updater
class FakeParams:
  def __init__(self, **values):
    self.values = dict(values)

  def get(self, key, **_):
    return self.values.get(key)

  def get_bool(self, key, **_):
    return bool(self.values.get(key))

  def put(self, key, val, **_):
    self.values[key] = val

  def put_bool(self, key, val, **_):
    self.values[key] = val

  def remove(self, key):
    self.values.pop(key, None)


class FakeSM:
  def __init__(self):
    self.ds = SimpleNamespace(started=False, networkType=log.DeviceState.NetworkType.wifi, networkMetered=False)
    self.panda = SimpleNamespace(ignitionLine=False, ignitionCan=False)
    self.seen = {'deviceState': True, 'pandaStates': True}
    self.alive = {'deviceState': True, 'pandaStates': True}

  def update(self, _timeout):
    pass

  def __getitem__(self, s):
    return self.ds if s == 'deviceState' else [self.panda]


class TestUpdater(FetchTestBase):
  def setUp(self):
    super().setUp()
    self.rel = Release(self.npz)
    self.params = FakeParams(SpeedLimitNswZones=MODE_LIVE, NswZonesAutoUpdate=True)
    self.sm = FakeSM()
    self.installed = []
    self.mono = 1000.0
    for p in (
      mock.patch.object(dl, "time", SimpleNamespace(monotonic=lambda: self.mono, time=lambda: NOW)),
      mock.patch.object(dl, "wall_now", lambda: NOW),
      mock.patch.object(dl, "system_time_valid", lambda: True),
    ):
      p.start()
      self.addCleanup(p.stop)
    self.up = dl.NswZonesUpdater(self.params, self.dir, BASE, on_installed=lambda: self.installed.append(1), sm=self.sm, get=self.rel.get)

  def step(self, dt=1.0):
    self.mono += dt
    started = self.up.update()
    if self.up._thread is not None:
      self.up._thread.join(30)
    return started

  def run_until_idle(self, n=OFFROAD_SETTLE_S + 10):
    return [self.step() for _ in range(int(n))]

  def test_first_download_after_parked_settles(self):
    started = self.run_until_idle()
    self.assertEqual(started.count(True), 1)
    self.assertGreaterEqual(started.index(True), int(OFFROAD_SETTLE_S) - 1)
    self.assertEqual(self.installed, [1])
    self.assertEqual(self.params.values.get(dl.VERSION_PARAM), "2026-09-29")
    st = dl.read_status(self.dir)
    self.assertEqual((st['state'], st['last_result'], st['last_check']), ('idle', 'updated', NOW))
    # a week has not passed: nothing more, however long it stays parked
    self.assertFalse(any(self.run_until_idle(300)))

  def test_driving_cellular_or_metered_never_downloads(self):
    for setup in (
      lambda: setattr(self.sm.ds, 'started', True),
      lambda: setattr(self.sm.panda, 'ignitionCan', True),
      lambda: setattr(self.sm.ds, 'networkType', log.DeviceState.NetworkType.cell4G),
      lambda: setattr(self.sm.ds, 'networkMetered', True),
      lambda: self.sm.alive.__setitem__('pandaStates', False),
    ):
      self.sm = FakeSM()
      self.up.sm = self.sm
      setup()
      self.assertFalse(any(self.run_until_idle(200)))
    self.assertEqual(self.rel.urls, [])

  def test_a_failure_waits_before_retrying(self):
    self.rel.files.pop(INDEX_NAME)
    started = self.run_until_idle(OFFROAD_SETTLE_S + 600)
    self.assertEqual(started.count(True), 1, "a failed check must not loop")
    self.assertEqual(self.installed, [])
    self.assertFalse(dl.read_status(self.dir).get('last_check'), "a failure is not a check")
    self.mono += dl.RETRY_S
    self.assertTrue(any(self.run_until_idle(5)))

  def test_the_button_runs_now_and_is_always_answered(self):
    # installed and checked a minute ago; the button checks anyway, without waiting for the settle time
    self.run_until_idle()
    self.rel.urls.clear()
    self.params.values[dl.CHECK_PARAM] = True
    self.assertTrue(self.step())
    self.step()  # the tick that collects the result
    self.assertEqual(self.rel.urls, [BASE + MANIFEST_NAME])
    self.assertIsNone(self.params.values.get(dl.CHECK_PARAM), "the request is consumed")
    self.assertEqual(dl.read_status(self.dir)['last_result'], 'up to date')
    # while driving it is refused, consumed, and the reason recorded - it does not fire later
    self.sm.ds.started = True
    self.params.values[dl.CHECK_PARAM] = True
    self.assertFalse(self.step())
    self.assertIsNone(self.params.values.get(dl.CHECK_PARAM))
    self.assertEqual(dl.read_status(self.dir)['last_result'], 'car must be parked')
    self.sm.ds.started = False
    self.assertFalse(any(self.run_until_idle(30)))

  def test_ignition_cancels_a_download_in_flight(self):
    gate = threading.Event()
    real_get = self.rel.get

    def slow_get(url, timeout=None):
      if url.endswith(INDEX_NAME):
        gate.wait(5)
      return real_get(url, timeout)

    self.up.get = slow_get
    for _ in range(int(OFFROAD_SETTLE_S) + 2):
      self.mono += 1
      if self.up.update():
        break
    self.assertIsNotNone(self.up._thread)
    self.sm.panda.ignitionLine = True
    self.mono += 1
    self.up.update()
    self.assertTrue(self.up._cancel.is_set())
    gate.set()
    self.up._thread.join(30)
    self.mono += 1
    self.up.update()
    self.assertIsNone(dl.read_current(self.dir))
    self.assertEqual(dl.read_status(self.dir)['last_result'], 'cancelled')

  def test_an_install_finished_while_driving_loads_at_the_next_park(self):
    """The matcher is swapped only parked: an install whose last steps ran as the car started is loaded later."""
    gate = threading.Event()
    real_get = self.rel.get

    def slow_get(url, timeout=None):
      if url.endswith(ATTRIBUTION_NAME):
        gate.wait(5)  # the last download; the car starts meanwhile, after the last cancel point is passed
      return real_get(url, timeout)

    self.up.get = slow_get
    for _ in range(int(OFFROAD_SETTLE_S) + 2):
      self.mono += 1
      if self.up.update():
        break
    self.up._cancel = SimpleNamespace(is_set=lambda: False, set=lambda: None)  # ty: ignore[invalid-assignment]  # past where it can stop
    self.sm.ds.started = True
    gate.set()
    self.up._thread.join(30)
    for _ in range(5):
      self.step()
    self.assertIsNotNone(dl.read_current(self.dir), "installed")
    self.assertEqual(self.installed, [], "not loaded while driving")
    self.sm.ds.started = False
    self.step()
    self.assertEqual(self.installed, [1], "loaded once parked")
    self.step()
    self.assertEqual(self.installed, [1])

  def test_mode_off_downloads_nothing_by_itself(self):
    self.params.values['SpeedLimitNswZones'] = MODE_OFF
    self.assertFalse(any(self.run_until_idle(200)))

  def test_version_param_follows_the_install(self):
    self.params.values[dl.VERSION_PARAM] = "2020-01-01"
    dl.NswZonesUpdater(self.params, self.dir, BASE, sm=self.sm, get=self.rel.get)
    self.assertIsNone(self.params.values.get(dl.VERSION_PARAM), "nothing installed: no version")

  def test_never_raises_into_mapd_manager(self):
    def boom(*_a, **_k):
      raise RuntimeError("UnknownKeyName")

    self.params.get_bool = boom  # ty: ignore[invalid-assignment]
    for _ in range(3):
      self.assertFalse(self.step())
    self.assertTrue(self.up.disabled)


if __name__ == "__main__":
  unittest.main()
