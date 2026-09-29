"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): the weekly OSM refresh decision (osm_auto_update.py).

The decision is a pure function, tested condition by condition. The stateful
wrapper is tested for the things that are easy to get wrong and costly on a
device: a default (never received) deviceState reads as "offroad and
unmetered", deviceState says offroad while driving in OffroadMode, offroad must
have SETTLED before it counts, it fires once per boot and never raises into
mapd_manager, and an interrupted download is not recorded as complete.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

import openpilot.cereal.messaging as messaging
from openpilot.sunnypilot.mapd import osm_auto_update as oau
from openpilot.sunnypilot.mapd.osm_auto_update import (
  COMPLETION_WINDOW_S,
  MAX_MAP_AGE_S,
  OFFROAD_SETTLE_S,
  UNMETERED_LINKS,
  OsmAutoUpdater,
  auto_update_due,
  download_progress,
  parse_downloaded_date,
)

NOW = 1_790_000_000.0   # 2026-09


def good(**over):
  old = NOW - MAX_MAP_AGE_S - 3600
  kw = {"enabled": True, "location": "AU", "requested_at": old, "completed_at": old, "now": NOW, "time_valid": True,
        "device_state_ok": True, "offroad_for": OFFROAD_SETTLE_S + 1, "network_up": True, "metered": False, "busy": False}
  kw.update(over)
  return auto_update_due(**kw)


class TestAutoUpdateDecision(unittest.TestCase):
  def test_all_conditions_met_is_due(self):
    self.assertEqual(good(), (True, ""))

  def test_each_condition_blocks_on_its_own(self):
    cases = {
      "disabled": {"enabled": False},
      "no region": {"location": ""},
      "never downloaded": {"requested_at": 0., "completed_at": 0.},
      "already requested": {"busy": True},
      "no deviceState": {"device_state_ok": False},
      "not offroad long enough": {"offroad_for": OFFROAD_SETTLE_S - 1},
      "no wifi": {"network_up": False},
      "metered": {"metered": True},
      "clock not set": {"time_valid": False},
    }
    for why, over in cases.items():
      with self.subTest(why=why):
        self.assertEqual(good(**over), (False, why))

  def test_onroad_is_never_due(self):
    # the wrapper passes a negative offroad time while deviceState says started
    self.assertFalse(good(offroad_for=-1.)[0])

  def test_the_week_boundary(self):
    self.assertFalse(good(completed_at=NOW - MAX_MAP_AGE_S + 60)[0])
    self.assertTrue(good(completed_at=NOW - MAX_MAP_AGE_S - 60)[0])

  def test_a_date_in_the_future_is_not_old(self):
    # a clock that was ahead when the date was written, then corrected
    self.assertEqual(good(completed_at=NOW + 86400), (False, "maps are recent"))

  def test_age_is_from_the_last_completed_download(self):
    """A fresh request that never finished (power off mid-download) does not make the maps recent."""
    self.assertEqual(good(requested_at=NOW - 3600, completed_at=NOW - MAX_MAP_AGE_S - 3600), (True, ""))
    self.assertEqual(good(requested_at=NOW - 3600, completed_at=NOW - 3600), (False, "maps are recent"))

  def test_no_completion_on_record_is_due(self):
    """An interrupted first refresh, or a download from before the recorder: refreshed on the next eligible boot."""
    self.assertEqual(good(requested_at=NOW - 3600, completed_at=0.), (True, ""))

  def test_download_progress(self):
    self.assertEqual(download_progress(None), (0, 0))
    self.assertEqual(download_progress("x"), (0, 0))
    self.assertEqual(download_progress({"downloaded_files": "a"}), (0, 0))
    self.assertEqual(download_progress({"downloaded_files": 357, "total_files": 357}), (357, 357))

  def test_parse_downloaded_date(self):
    self.assertEqual(parse_downloaded_date(None), 0.)
    self.assertEqual(parse_downloaded_date(""), 0.)
    self.assertEqual(parse_downloaded_date("0.0"), 0.)
    self.assertEqual(parse_downloaded_date("-5"), 0.)
    self.assertEqual(parse_downloaded_date("garbage"), 0.)
    self.assertEqual(parse_downloaded_date("1726000000.5"), 1726000000.5)

  def test_the_real_network_enum_compares(self):
    """networkType arrives as a capnp enum; the wrapper compares it with NETWORK_NONE."""
    msg = messaging.new_message('deviceState')
    self.assertEqual(msg.deviceState.networkType, oau.NETWORK_NONE)
    self.assertFalse(msg.deviceState.networkType in UNMETERED_LINKS)
    msg.deviceState.networkType = 'wifi'
    self.assertNotEqual(msg.deviceState.networkType, oau.NETWORK_NONE)
    self.assertTrue(msg.deviceState.networkType in UNMETERED_LINKS)
    msg.deviceState.networkType = 'ethernet'
    self.assertTrue(msg.deviceState.networkType in UNMETERED_LINKS)
    msg.deviceState.networkType = 'cell4G'
    self.assertFalse(msg.deviceState.networkType in UNMETERED_LINKS)


class FakeParams:
  def __init__(self, **values):
    self.values = dict(values)
    self.puts: list = []

  def get(self, key, **_):
    return self.values.get(key)

  def get_bool(self, key, **_):
    return bool(self.values.get(key))

  def put_bool(self, key, val, **_):
    self.puts.append((key, val))
    self.values[key] = val

  def put(self, key, val, **_):
    self.puts.append((key, val))
    self.values[key] = val

  def remove(self, key):
    self.puts.append((key, None))
    self.values.pop(key, None)


class FakeSM:
  def __init__(self):
    self.ds = SimpleNamespace(started=False, networkType=oau.log.DeviceState.NetworkType.wifi, networkMetered=False)
    self.panda = SimpleNamespace(ignitionLine=False, ignitionCan=False)
    self.seen = {'deviceState': True, 'pandaStates': True}
    self.alive = {'deviceState': True, 'pandaStates': True}

  def update(self, _timeout):
    pass

  def __getitem__(self, s):
    return self.ds if s == 'deviceState' else [self.panda]


class LappedSM(FakeSM):
  """deviceState is a conflated queue at 2 Hz: a SubMaster not updated for minutes is lapped, and its next update()
  reports nothing and alive False (cereal SubMaster.update_msgs); the one after that recovers. This double compresses
  "minutes" to "a tick": alive only when update() ran this tick AND on the tick before, so a reader that skips ticks
  and then updates once sees what a lapped one would."""

  def __init__(self):
    super().__init__()
    self.tick = 0
    self.last_read: int | None = None
    self.read_alive = False
    outer = self

    class _Alive(dict):
      def __getitem__(self, _s):
        return outer.read_alive and outer.last_read == outer.tick

    self.alive = _Alive()

  def update(self, _timeout):
    if self.last_read == self.tick:
      return  # a second read in the same tick changes nothing
    self.read_alive = self.last_read is not None and self.last_read >= self.tick - 1
    self.last_read = self.tick

  def new_tick(self):
    self.tick += 1


class TestOsmAutoUpdater(unittest.TestCase):
  def setUp(self):
    old = str(NOW - MAX_MAP_AGE_S - 3600)
    self.params = FakeParams(OsmAutoUpdateWeekly=True, OsmLocationName="AU", OsmDownloadedDate=old,
                             OsmLastCompleteDate=old)
    self.mem = FakeParams()
    self.up = OsmAutoUpdater(self.params, self.mem)
    self.sm = FakeSM()
    self.up.sm = self.sm  # ty: ignore[invalid-assignment]
    self.mono = 1000.0
    patches = [
      # the module's own reference to time, not the global time module
      mock.patch.object(oau, "time", SimpleNamespace(monotonic=lambda: self.mono)),
      mock.patch.object(oau, "wall_now", lambda: NOW),
      mock.patch.object(oau, "system_time_valid", lambda: True),
    ]
    for p in patches:
      p.start()
      self.addCleanup(p.stop)

  def step(self, dt=1.0):
    self.mono += dt
    return self.up.update()

  def test_fires_once_after_offroad_settles(self):
    fired = [self.step() for _ in range(int(OFFROAD_SETTLE_S) + 5)]
    self.assertEqual(fired.count(True), 1)
    self.assertGreaterEqual(fired.index(True), int(OFFROAD_SETTLE_S) - 1, "fired before offroad had settled")
    self.assertEqual(self.params.puts, [("OsmDbUpdatesCheck", True)])

    # once per boot: even with the request cleared and everything still true
    self.params.values["OsmDbUpdatesCheck"] = False
    self.assertFalse(any(self.step() for _ in range(200)))

  def test_an_unheard_device_state_is_not_offroad(self):
    """A default deviceState says started=False, networkMetered=False: the "go" answer."""
    self.sm.seen['deviceState'] = False
    self.assertFalse(any(self.step() for _ in range(200)))
    self.sm.seen['deviceState'] = True
    self.sm.alive['deviceState'] = False
    self.assertFalse(any(self.step() for _ in range(200)))
    self.assertEqual(self.params.puts, [])

  def test_going_onroad_restarts_the_settle_timer(self):
    for _ in range(int(OFFROAD_SETTLE_S) - 5):
      self.assertFalse(self.step())
    self.sm.ds.started = True           # the boot-at-ignition window closes
    self.assertFalse(self.step())
    self.sm.ds.started = False
    fired = [self.step() for _ in range(int(OFFROAD_SETTLE_S) + 5)]
    self.assertEqual(fired.count(True), 1)
    self.assertGreaterEqual(fired.index(True), int(OFFROAD_SETTLE_S) - 1)

  def test_driving_in_offroad_mode_is_not_parked(self):
    """OffroadMode: deviceState.started is False with the car driving; the panda's ignition is not."""
    self.sm.panda.ignitionLine = True
    self.assertFalse(any(self.step() for _ in range(200)))
    self.sm.panda.ignitionLine = False
    self.sm.panda.ignitionCan = True
    self.assertFalse(any(self.step() for _ in range(200)))
    self.assertEqual(self.params.puts, [])

  def test_an_unheard_panda_is_not_parked(self):
    self.sm.alive['pandaStates'] = False
    self.assertFalse(any(self.step() for _ in range(200)))

  def test_cellular_is_never_used(self):
    self.sm.ds.networkType = oau.log.DeviceState.NetworkType.cell4G
    self.assertFalse(any(self.step() for _ in range(200)))

  def test_param_off_never_fires(self):
    self.params.values["OsmAutoUpdateWeekly"] = False
    self.assertFalse(any(self.step() for _ in range(200)))
    self.assertEqual(self.params.puts, [])

  def test_a_download_in_progress_blocks_it(self):
    self.mem.values["OSMDownloadLocations"] = {"nations": ["AU"], "states": []}
    self.assertFalse(any(self.step() for _ in range(200)))

  def test_the_weekly_refresh_reads_the_submaster_every_tick(self):
    lapped = LappedSM()
    self.up.sm = lapped  # ty: ignore[invalid-assignment]
    fired = []
    for _ in range(int(OFFROAD_SETTLE_S) + 5):
      lapped.new_tick()
      fired.append(self.step())
    self.assertEqual(fired.count(True), 1)
    self.assertTrue(self.up.request_allowed, "the tick that set the request tells update_osm_db() it may act")

  def test_a_submaster_error_never_raises(self):
    def boom(_timeout):
      raise RuntimeError("msgq")
    self.sm.update = boom  # ty: ignore[invalid-assignment]
    for _ in range(3):
      self.step()
    self.assertTrue(self.up._sm_failed)

  def test_never_raises_into_mapd_manager(self):
    def boom(*_a, **_k):
      raise RuntimeError("UnknownKeyName")
    self.params.get_bool = boom  # ty: ignore[invalid-assignment]
    for _ in range(3):
      self.assertFalse(self.step())
    self.assertTrue(self.up.done, "an error must stop the checks for this boot, not repeat every second")

  def test_completion_error_never_raises(self):
    def boom(*_a, **_k):
      raise RuntimeError("UnknownKeyName")
    self.mem.get = boom  # ty: ignore[invalid-assignment]
    for _ in range(3):
      self.step()
    self.assertTrue(self.up._completion_failed)


class TestRequestGate(unittest.TestCase):
  """OsmDbUpdatesCheck from a button or sunnylink: runs offroad (OffroadMode with the car on included), refused and
  cleared before update_osm_db() otherwise. The weekly refresh itself stays parked-only (TestOsmAutoUpdater)."""

  def setUp(self):
    self.params = FakeParams(OsmAutoUpdateWeekly=False, OsmLocationName="AU", OsmDbUpdatesCheck=True)
    self.mem = FakeParams()
    self.up = OsmAutoUpdater(self.params, self.mem)
    self.sm = FakeSM()
    self.up.sm = self.sm  # ty: ignore[invalid-assignment]

  def answer(self):
    return self.up.answer_request()

  def assert_kept(self):
    self.assertEqual(self.answer(), "")
    self.assertTrue(self.params.values.get("OsmDbUpdatesCheck"), "a request that may run is left for update_osm_db()")

  def assert_refused(self, why):
    self.assertEqual(self.answer(), why)
    self.assertNotIn("OsmDbUpdatesCheck", self.params.values, "a refused request is cleared, not left to fire later")

  def test_the_refusal_is_pure(self):
    def refusal(device_state_ok=True, offroad=True, location="AU", downloading=False):
      return oau.request_refusal(device_state_ok=device_state_ok, offroad=offroad, location=location, downloading=downloading)

    self.assertEqual(refusal(), "")
    self.assertEqual(refusal(device_state_ok=False), "no deviceState")
    self.assertEqual(refusal(offroad=False), oau.OFFROAD_ONLY)
    self.assertEqual(refusal(location=""), "no region")
    self.assertEqual(refusal(downloading=True), "already downloading")

  def test_parked_runs(self):
    self.assert_kept()

  def test_offroad_mode_with_the_car_on_runs(self):
    """The owner's complaint: parked in OffroadMode with the ignition on, the button said "car must be parked"."""
    self.sm.panda.ignitionLine = True
    self.sm.panda.ignitionCan = True
    self.assert_kept()

  def test_onroad_is_refused_and_cleared(self):
    self.sm.ds.started = True
    self.assert_refused(oau.OFFROAD_ONLY)

  def test_an_unheard_device_state_is_refused(self):
    """A default deviceState reads started=False: unheard is not offroad."""
    self.sm.seen['deviceState'] = False
    self.assert_refused("no deviceState")
    self.params.values["OsmDbUpdatesCheck"] = True
    self.sm.seen['deviceState'] = True
    self.sm.alive['deviceState'] = False
    self.assert_refused("no deviceState")

  def test_no_region_is_refused(self):
    """sunnylink can ask on a device that has never had a region: upstream would request nation ""."""
    self.params.values["OsmLocationName"] = ""
    self.assert_refused("no region")

  def test_a_download_in_progress_is_not_restarted(self):
    self.mem.values["OSMDownloadLocations"] = {"nations": ["AU"], "states": []}
    self.assert_refused("already downloading")

  def test_no_request_touches_nothing(self):
    self.params.values.pop("OsmDbUpdatesCheck")
    self.sm.ds.started = True
    self.assertEqual(self.answer(), "")
    self.assertEqual(self.params.puts, [])

  def test_a_request_after_the_weekly_latch_is_not_refused_as_unheard(self):
    """The weekly refresh has fired (or given up) for this boot, then minutes pass: the SubMaster must still have been
    read on every tick, or the lapped deviceState reads unheard and a button press is refused "no deviceState"."""
    lapped = LappedSM()
    self.up.sm = lapped  # ty: ignore[invalid-assignment]
    self.up.done = True
    self.params.values.pop("OsmDbUpdatesCheck")
    for _ in range(300):
      lapped.new_tick()
      self.up.update()
    self.params.values["OsmDbUpdatesCheck"] = True
    lapped.new_tick()
    self.up.update()
    self.assertTrue(self.params.values.get("OsmDbUpdatesCheck"), "an offroad request was thrown away")
    self.assertTrue(self.up.request_allowed)

  def test_request_allowed_only_for_a_request_checked_this_tick(self):
    """update_osm_db() acts only when request_allowed: a request written after the check waits a tick."""
    self.up.update()
    self.assertTrue(self.up.request_allowed)
    self.params.values.pop("OsmDbUpdatesCheck")
    self.up.update()
    self.assertFalse(self.up.request_allowed, "no request was checked this tick")
    self.params.values["OsmDbUpdatesCheck"] = True
    self.sm.ds.started = True
    self.up.update()
    self.assertFalse(self.up.request_allowed, "a refused request")

  def test_update_answers_even_after_the_weekly_latch(self):
    """update() runs the gate every tick, also once the weekly refresh has fired or given up for the boot."""
    self.up.done = True
    self.sm.ds.started = True
    self.assertFalse(self.up.update())
    self.assertNotIn("OsmDbUpdatesCheck", self.params.values)

  def test_an_error_fails_closed(self):
    real_get = self.params.get

    def boom(key, **_):
      if key == "OsmLocationName":
        raise RuntimeError("UnknownKeyName")
      return real_get(key)

    self.params.get = boom  # ty: ignore[invalid-assignment]
    self.assertTrue(self.answer())
    self.assertNotIn("OsmDbUpdatesCheck", self.params.values)
    # every later request is refused for the rest of the boot, however offroad the car is
    self.params.get = real_get  # ty: ignore[invalid-assignment]
    self.params.values["OsmDbUpdatesCheck"] = True
    self.assertTrue(self.answer())
    self.assertNotIn("OsmDbUpdatesCheck", self.params.values)

  def test_is_offroad_and_is_parked(self):
    self.assertTrue(oau.is_offroad(self.sm) and oau.is_parked(self.sm))
    self.sm.panda.ignitionCan = True
    self.assertTrue(oau.is_offroad(self.sm), "OffroadMode with the car on is offroad")
    self.assertFalse(oau.is_parked(self.sm), "but not parked")
    self.sm.ds.started = True
    self.assertFalse(oau.is_offroad(self.sm) or oau.is_parked(self.sm))


class TestUpdateOsmDb(unittest.TestCase):
  """mapd_manager.update_osm_db(request_allowed): upstream's consumer, told whether the request was checked."""

  def setUp(self):
    from openpilot.sunnypilot.mapd import mapd_manager as mm
    self.mm = mm
    self.params = FakeParams(OsmDbUpdatesCheck=True, OsmLocationName="AU", OsmStateName="All")
    self.mem = FakeParams(OSMDownloadBounds="x", LastGPSPosition="{}")
    self.started: list = []
    patches = [
      mock.patch.object(mm, "params", self.params),
      mock.patch.object(mm, "mem_params", self.mem),
      mock.patch.object(mm, "cleanup_old_osm_data", lambda _files: None),
      mock.patch.object(mm, "get_files_for_cleanup", list),
      mock.patch.object(mm, "request_refresh_osm_location_data", lambda n, s: self.started.append((n, s))),
    ]
    for p in patches:
      p.start()
      self.addCleanup(p.stop)

  def test_a_checked_request_starts_the_download(self):
    self.mm.update_osm_db(True)
    self.assertEqual(len(self.started), 1)

  def test_an_unchecked_request_waits(self):
    """A sunnylink write between answer_request() and update_osm_db() is left for the next tick's check."""
    self.mm.update_osm_db(False)
    self.assertEqual(self.started, [])
    self.assertTrue(self.params.values["OsmDbUpdatesCheck"], "left in place for the next tick, not dropped")

  def test_upstreams_call_is_unchanged(self):
    self.mm.update_osm_db()
    self.assertEqual(len(self.started), 1)


class TestCompletionRecord(unittest.TestCase):
  """OsmLastCompleteDate: the request date of the last download mapd finished."""
  REQUESTED = "1789990000.5"

  def setUp(self):
    self.params = FakeParams(OsmDownloadedDate=self.REQUESTED)
    self.mem = FakeParams()
    self.up = OsmAutoUpdater(self.params, self.mem)
    self.mono = 1000.0
    p = mock.patch.object(oau, "time", SimpleNamespace(monotonic=lambda: self.mono))
    p.start()
    self.addCleanup(p.stop)

  def tick(self, n=1):
    for _ in range(n):
      self.mono += 1.
      self.up.record_completion()

  def downloading(self, done, total):
    self.mem.values["OSMDownloadLocations"] = {"nations": ["AU"], "states": []}
    self.params.values["OSMDownloadProgress"] = {"downloaded_files": done, "total_files": total}
    self.tick()

  def finished(self):
    self.mem.values["OSMDownloadLocations"] = None
    self.tick()

  def test_a_finished_download_is_recorded_with_its_request_date(self):
    self.downloading(10, 357)
    self.assertFalse("OsmLastCompleteDate" in self.params.values)
    self.downloading(357, 357)
    self.finished()
    self.assertEqual(self.params.values["OsmLastCompleteDate"], self.REQUESTED)

  def test_progress_written_after_the_list_is_dropped(self):
    self.downloading(350, 357)
    self.finished()
    self.params.values["OSMDownloadProgress"] = {"downloaded_files": 357, "total_files": 357}
    self.tick(2)
    self.assertEqual(self.params.values["OsmLastCompleteDate"], self.REQUESTED)

  def test_an_interrupted_download_is_not_recorded(self):
    self.downloading(100, 357)
    self.finished()
    self.tick(int(COMPLETION_WINDOW_S) + 5)
    self.assertFalse("OsmLastCompleteDate" in self.params.values)
    # progress that turns up long after the download ended is not trusted either
    self.params.values["OSMDownloadProgress"] = {"downloaded_files": 357, "total_files": 357}
    self.tick(5)
    self.assertFalse("OsmLastCompleteDate" in self.params.values)

  def test_nothing_downloaded_this_boot_records_nothing(self):
    self.params.values["OSMDownloadProgress"] = {"downloaded_files": 357, "total_files": 357}
    self.tick(20)
    self.assertEqual(self.params.puts, [])

  def test_recorded_even_with_the_weekly_refresh_off(self):
    self.params.values["OsmAutoUpdateWeekly"] = False
    self.downloading(357, 357)
    self.finished()
    self.assertEqual(self.params.values["OsmLastCompleteDate"], self.REQUESTED)


if __name__ == "__main__":
  unittest.main()
