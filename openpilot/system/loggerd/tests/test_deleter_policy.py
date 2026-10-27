"""FORK(STORAGE): push16's deleter - parked it makes room up to ROUTINE_PERCENT from uploaded segments, driving only
below upstream's floor and then one file at a time (openpilot/sunnypilot/system/deleter_policy.py)."""
import contextlib
import math
import os
import threading
from collections import namedtuple
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import openpilot.system.loggerd.deleter as deleter
import openpilot.system.loggerd.uploader as uploader
import openpilot.sunnypilot.system.deleter_policy as policy
from openpilot.common.hardware.hw import Paths
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system.storage_diag import DISKSTATS_FIELDS
from openpilot.system.athena.athenad import UploadItem
from openpilot.system.loggerd import xattr_cache
from openpilot.system.loggerd.tests.loggerd_tests_common import UploaderTestCase

Stats = namedtuple("Stats", ['f_bavail', 'f_blocks', 'f_frsize'])
BLOCKS = 1000
FRSIZE = 64 * 2**20  # 1000 blocks of 64 MiB: 62.5 GiB, so 1 % is 640 MiB and 9 % is 5.6 GiB (above MIN_BYTES)
MB = 2**20


def queue_item(path: Path) -> dict:
  """An entry of AthenadUploadQueue as athenad writes it (UploadQueueCache.cache)."""
  return asdict(UploadItem(path=str(path), url="https://example.invalid/upload", headers={}, created_at=0, id="1"))


class FakeSampler:
  def __init__(self):
    self.samples = 1
    self.unreadable = 3

  def sample(self):
    self.samples += 1

  def top(self):
    return [{"comm": "updated", "pid": 42, "mb": 4096.}]

  def total_bytes(self):
    return 5 * 2**30


class StopAfter(threading.Event):
  """An exit event for deleter_thread: records every wait, runs hook(n, seconds) at each, and is set after `waits`."""

  def __init__(self, waits: int, hook=None):
    super().__init__()
    self.limit = waits
    self.hook = hook
    self.waits: list[float] = []

  def wait(self, timeout: float | None = None) -> bool:
    self.waits.append(float(timeout or 0.))
    if self.hook is not None:
      self.hook(len(self.waits), self.waits[-1])
    if len(self.waits) >= self.limit:
      self.set()
    return self.is_set()


class TestPolicyRules(OpenpilotTestCase):
  def test_need_thresholds(self):
    floor_bytes = deleter.MIN_BYTES
    plenty = 20 * 2**30
    need = policy.deletion_need
    # parked: up to ROUTINE_PERCENT, critical below the floor
    assert need(25., plenty, False, 10, floor_bytes) is None
    assert need(20., plenty, False, 10, floor_bytes) is None
    assert need(19.99, plenty, False, 10, floor_bytes) == policy.ROUTINE
    assert need(10., plenty, False, 10, floor_bytes) == policy.ROUTINE
    assert need(9.99, plenty, False, 10, floor_bytes) == policy.CRITICAL
    assert need(50., floor_bytes - 1, False, 10, floor_bytes) == policy.CRITICAL
    # driving: nothing above the floor, upstream's floor below it
    assert need(19.99, plenty, True, 10, floor_bytes) is None
    assert need(10., plenty, True, 10, floor_bytes) is None
    assert need(9.99, plenty, True, 10, floor_bytes) == policy.CRITICAL
    assert need(50., floor_bytes - 1, True, 10, floor_bytes) == policy.CRITICAL
    # parked: the routine work waits for PARKED_GRACE_S of it, the floor does not
    grace = policy.PARKED_GRACE_S
    assert need(15., plenty, False, 10, floor_bytes, parked_s=0.) is None
    assert need(15., plenty, False, 10, floor_bytes, parked_s=grace - 0.1) is None
    assert need(15., plenty, False, 10, floor_bytes, parked_s=grace) == policy.ROUTINE
    assert need(9.99, plenty, False, 10, floor_bytes, parked_s=0.) == policy.CRITICAL
    assert need(15., plenty, True, 10, floor_bytes, parked_s=math.inf) is None
    # the routine target sits well above the floor, the emergency below it and above the Out of Storage alert (7 %)
    assert policy.ROUTINE_PERCENT >= 2 * deleter.MIN_PERCENT
    assert 7. < policy.EMERGENCY_PERCENT < deleter.MIN_PERCENT
    # a boot straight into a drive (IsOffroad until `started`) and a short stop both stay under the grace
    assert 60. <= policy.PARKED_GRACE_S <= 300.

  def test_parked_timer(self):
    t = policy.ParkedTimer()
    assert t.parked_s(False, 100.) == 0.  # from the first pass that saw it parked: the deleter's start
    assert t.parked_s(False, 160.) == 60.
    assert t.parked_s(True, 170.) == 0.
    assert t.parked_s(False, 200.) == 0.  # a drive starts it again
    assert t.parked_s(False, 230.) == 30.

  def test_only_a_critical_delete_while_driving_is_paced(self):
    assert policy.file_pace_s(policy.CRITICAL, True, 9.5) == policy.ONROAD_FILE_PACE_S
    assert policy.file_pace_s(policy.CRITICAL, True, policy.EMERGENCY_PERCENT) == policy.ONROAD_FILE_PACE_S
    assert policy.file_pace_s(policy.CRITICAL, True, policy.EMERGENCY_PERCENT - 0.01) == 0.
    assert policy.file_pace_s(policy.CRITICAL, False, 9.5) == 0.
    assert policy.file_pace_s(policy.ROUTINE, False, 15.) == 0.
    assert policy.ONROAD_FILE_PACE_S > 5.  # /data's ext4 commit interval (commit=5 in every storage report)

  def test_next_wait(self):
    assert policy.next_wait_s(policy.ROUTINE, 0., True) == policy.ROUTINE_SEGMENT_PACE_S
    assert policy.next_wait_s(policy.ROUTINE, 0., False) == policy.IDLE_S
    assert policy.next_wait_s(policy.CRITICAL, policy.ONROAD_FILE_PACE_S, True) == policy.ONROAD_FILE_PACE_S
    assert policy.next_wait_s(policy.CRITICAL, 0., True) == policy.CRITICAL_RECHECK_S
    assert policy.next_wait_s(policy.CRITICAL, 0., False) == policy.NOTHING_DELETABLE_S
    # parked, one segment's discards per journal commit (/data's commit=5), not five
    assert policy.ROUTINE_SEGMENT_PACE_S > 5.

  def test_athena_queued(self):
    root = "/data/media/0/realdata"
    p = mock.Mock()
    p.get.return_value = [
      {"path": f"{root}/00000157--5f63262cc1--3/rlog.zst"},
      {"path": f"{root}/00000157--5f63262cc1--4/fcamera.hevc", "url": "u"},
      {"path": f"{root}/00000157--5f63262cc1--4/rlog.zst"},
      {"path": "/mnt/external_realdata/00000150--6eb87d4e2b--0/rlog.zst"},  # not under root
      {"path": f"{root}/../realdata2/x--0/rlog.zst"},
      {"path": root},
      {"path": "00000158--846ba139e9--1/rlog.zst"},  # not absolute: athenad never writes one
      {"path": 3}, {"url": "u"}, 7, None,
    ]
    assert policy.athena_queued(p, root) == {"00000157--5f63262cc1--3", "00000157--5f63262cc1--4"}
    assert policy.athena_queued(p, root + "/") == {"00000157--5f63262cc1--3", "00000157--5f63262cc1--4"}
    p.get.assert_called_with("AthenadUploadQueue")
    for value in (None, [], "[", {"path": f"{root}/x--0/rlog"}, 5):
      p.get.return_value = value
      assert policy.athena_queued(p, root) == set()
    p.get.side_effect = OSError("stalled")
    with mock.patch.object(policy, "cloudlog") as log:
      assert policy.athena_queued(p, root) == set()
    log.exception.assert_called_once()

  def test_upload_attrs_are_the_uploaders(self):
    from openpilot.sunnypilot.sunnylink import uploader as sunnylink_uploader
    assert (uploader.UPLOAD_ATTR_NAME, uploader.UPLOAD_ATTR_VALUE) in policy.UPLOAD_ATTRS
    assert (sunnylink_uploader.UPLOAD_ATTR_NAME, sunnylink_uploader.UPLOAD_ATTR_VALUE) in policy.UPLOAD_ATTRS
    # what the uploaders send without being asked is their immediate priority
    with mock.patch.object(uploader, "Api"):
      assert set(policy.AUTO_UPLOADED) == set(uploader.Uploader("0000000000000000", "/tmp").immediate_priority)

  def test_is_onroad(self):
    p = mock.Mock()
    p.get_bool.return_value = True
    assert policy.is_onroad(p) is False
    p.get_bool.return_value = False
    assert policy.is_onroad(p) is True
    p.get_bool.side_effect = OSError("stalled")
    with mock.patch.object(policy, "cloudlog"):
      assert policy.is_onroad(p) is True  # unknown counts as driving
    p.get_bool.assert_called_with("IsOffroad")


class DeleterCase(UploaderTestCase):
  def make_file_with_data(self, f_dir: str, fn: str, size_mb: float = .001, lock: bool = False,
                          upload_xattr: bytes | None = None, preserve_xattr: bytes | None = None) -> Path:
    return super().make_file_with_data(f_dir, fn, size_mb, lock, upload_xattr, preserve_xattr)

  def setup_method(self):
    super().openpilot_setup_method()
    self.free_pct = 15.
    self.free_step = 0.  # percent freed per segment() directory that is gone
    self.made: list[str] = []
    self.clock = 0.  # deleter's time.monotonic() in run_thread; every wait of the exit event advances it
    patcher = mock.patch.object(os, "statvfs", side_effect=self.fake_statvfs)
    patcher.start()
    self.addCleanup(patcher.stop)

  def fake_statvfs(self, d):
    gone = sum(not (Path(Paths.log_root()) / s).exists() for s in self.made)
    pct = self.free_pct + self.free_step * gone
    return Stats(f_bavail=int(round(pct * BLOCKS / 100)), f_blocks=BLOCKS, f_frsize=FRSIZE)

  def segment(self, name: str, uploaded: bool = True, sizes: dict[str, float] | None = None, **kw) -> Path:
    """A segment with a qlog and a qcamera (uploaded or not) and an fcamera, which no uploader sends by itself."""
    sizes = sizes or {"fcamera.hevc": .05, "qcamera.ts": .01, "qlog.zst": .002}
    attr = uploader.UPLOAD_ATTR_VALUE if uploaded else None
    for fn, size in sizes.items():
      self.make_file_with_data(name, fn, size, upload_xattr=attr if fn in policy.AUTO_UPLOADED else None, **kw)
    self.made.append(name)
    return Path(Paths.log_root()) / name

  def remaining(self) -> list[str]:
    return [s for s in self.made if (Path(Paths.log_root()) / s).exists()]


class TestDeleterStep(DeleterCase):
  def test_parked_makes_room_from_uploaded_segments_only(self):
    old = self.segment(self.seg_format.format(0), uploaded=False)
    a = self.segment(self.seg_format.format(1))
    b = self.segment(self.seg_format2.format(0))
    assert deleter.deleter_step(onroad=False) == (True, str(a))
    assert deleter.deleter_step(onroad=False) == (True, str(b))
    assert deleter.deleter_step(onroad=False) == (True, None)  # what is left waits for the uploader
    assert old.exists()

  def test_parked_stops_at_the_routine_target(self):
    self.segment(self.seg_format.format(0))
    self.free_pct = policy.ROUTINE_PERCENT
    assert deleter.deleter_step(onroad=False) == (False, None)
    self.free_pct = policy.ROUTINE_PERCENT - 0.2
    assert deleter.deleter_step(onroad=False)[1] is not None

  def test_below_the_floor_the_oldest_goes_uploaded_or_not(self):
    old = self.segment(self.seg_format.format(0), uploaded=False)
    self.segment(self.seg_format.format(1))
    self.free_pct = 9.
    for onroad in (False, True):
      assert deleter.deleter_step(onroad=onroad) == (True, str(old))
      old = Path(Paths.log_root()) / self.seg_format.format(1)

  def test_driving_above_the_floor_deletes_nothing(self):
    segs = [self.segment(self.seg_format.format(i)) for i in range(3)]
    for pct in (19., 15., 10.):
      self.free_pct = pct
      assert deleter.deleter_step(onroad=True) == (False, None)
    assert all(s.exists() for s in segs)

  def test_the_drive_is_read_from_is_offroad(self):
    self.segment(self.seg_format.format(0))
    self.params.put_bool("IsOffroad", False, block=True)
    assert deleter.deleter_step() == (False, None)
    self.params.put_bool("IsOffroad", True, block=True)
    assert deleter.deleter_step()[1] is not None

  def test_routine_stops_where_upstream_order_reaches_its_last_resort(self):
    # upstream's test_delete_last, every segment uploaded: parked at 15 % only the two ordinary segments go; the
    # bookmarked one and boot / crash wait for the floor, and then go in upstream's order
    order = [
      self.segment(self.seg_format.format(1)),
      self.segment(self.seg_format2.format(0)),
      self.segment(self.seg_format.format(0), preserve_xattr=deleter.PRESERVE_ATTR_VALUE),
      self.segment("boot", sizes={"0000004a--a1b2c3d4e5.zst": .01}),
      self.segment("crash", sizes={"crash_1": .01}),
    ]
    assert [deleter.deleter_step(onroad=False)[1] for _ in range(2)] == [str(p) for p in order[:2]]
    assert deleter.deleter_step(onroad=False) == (True, None)
    assert all(p.exists() for p in order[2:])
    self.free_pct = 9.
    assert [deleter.deleter_step(onroad=False)[1] for _ in range(3)] == [str(p) for p in order[2:]]

  def test_routine_never_takes_a_bookmark_before_segments_waiting_to_upload(self):
    # the review's case: a trip on cellular, the qcameras not sent; the bookmark's qlog and qcamera were
    bookmark = self.segment(self.seg_format.format(0), preserve_xattr=deleter.PRESERVE_ATTR_VALUE)
    waiting = [self.segment(self.seg_format2.format(i), uploaded=False) for i in range(3)]
    for _ in range(2):
      assert deleter.deleter_step(onroad=False) == (True, None)
    assert bookmark.exists() and all(p.exists() for p in waiting)

  def test_routine_never_takes_boot_or_crash_logs(self):
    # their names are not the uploaders' qlog / qcamera, so they would look done even when nothing was sent
    boot = self.segment("boot", sizes={"0000004a--a1b2c3d4e5.zst": .01})
    crash = self.segment("crash", sizes={"crash_1": .01})
    self.segment(self.seg_format2.format(0), uploaded=False)
    assert deleter.deleter_step(onroad=False) == (True, None)
    assert boot.exists() and crash.exists()

  def test_routine_skips_what_athena_will_send(self):
    asked = self.segment(self.seg_format.format(0))  # its qlog and qcamera are up; its rlog was asked for in connect
    other = self.segment(self.seg_format.format(1))
    self.params.put("AthenadUploadQueue", [queue_item(asked / "rlog.zst")], block=True)
    assert deleter.deleter_step(onroad=False) == (True, str(other))
    assert deleter.deleter_step(onroad=False) == (True, None)
    self.free_pct = 9.  # the floor takes it, as upstream would
    assert deleter.deleter_step(onroad=False) == (True, str(asked))

  def test_a_malformed_upload_queue_holds_nothing(self):
    seg = self.segment(self.seg_format.format(0))
    with open(self.params.get_param_path("AthenadUploadQueue"), "w") as f:
      f.write("[{\"path\": ")
    assert self.params.get("AthenadUploadQueue") is None
    assert deleter.deleter_step(onroad=False) == (True, str(seg))

  def test_a_locked_segment_is_never_deleted(self):
    locked = Path(Paths.log_root()) / self.seg_dir
    self.make_file_with_data(self.seg_dir, "qlog.zst", lock=True, upload_xattr=uploader.UPLOAD_ATTR_VALUE)
    for pct, onroad in ((15., False), (9., False), (9., True), (7., True)):
      self.free_pct = pct
      assert deleter.deleter_step(onroad=onroad) == (True, None)
    assert locked.exists()


class TestUploaded(DeleterCase):
  def test_uploaded_is_read_past_the_cache(self):
    seg = self.segment(self.seg_dir, uploaded=False)
    qlog = str(seg / "qlog.zst")
    assert xattr_cache.getxattr(qlog, uploader.UPLOAD_ATTR_NAME) is None  # cached: "not yet"
    assert policy.pending_upload(str(seg))
    for fn in ("qlog.zst", "qcamera.ts"):  # the uploader, in its own process
      os.setxattr(str(seg / fn), uploader.UPLOAD_ATTR_NAME, uploader.UPLOAD_ATTR_VALUE)
    assert xattr_cache.getxattr(qlog, uploader.UPLOAD_ATTR_NAME) is None  # this process's cache never learns it
    assert not policy.pending_upload(str(seg))

  def test_sunnylinks_upload_counts(self):
    seg = self.segment(self.seg_dir, uploaded=False)
    for fn in ("qlog.zst", "qcamera.ts"):
      os.setxattr(str(seg / fn), "user.sunny.upload", b"1")
    assert not policy.pending_upload(str(seg))

  def test_a_qcamera_still_to_go_holds_the_segment(self):
    seg = self.segment(self.seg_dir, uploaded=False)
    os.setxattr(str(seg / "qlog.zst"), uploader.UPLOAD_ATTR_NAME, uploader.UPLOAD_ATTR_VALUE)
    assert policy.pending_upload(str(seg))
    os.setxattr(str(seg / "qcamera.ts"), uploader.UPLOAD_ATTR_NAME, uploader.UPLOAD_ATTR_VALUE)
    assert not policy.pending_upload(str(seg))

  def test_files_no_uploader_sends_by_itself_hold_nothing(self):
    seg = self.segment(self.seg_dir, sizes={"fcamera.hevc": .01, "rlog.zst": .01, "ecamera.hevc": .01})
    assert not policy.pending_upload(str(seg))
    assert not policy.pending_upload(str(seg) + "_gone")


class TestRemoveDir(DeleterCase):
  SIZES = {"fcamera.hevc": .5, "ecamera.hevc": .2, "qcamera.ts": .05, "qlog.zst": .01}

  def test_one_file_at_a_time_largest_first(self):
    seg = self.segment(self.seg_dir, sizes=self.SIZES)
    seen: list[list[str]] = []

    def wait(t: float) -> bool:
      assert t == policy.ONROAD_FILE_PACE_S
      seen.append(sorted(os.listdir(seg)))
      return False

    nbytes, files, complete = policy.remove_dir(str(seg), policy.ONROAD_FILE_PACE_S, wait)
    assert (files, complete) == (4, True) and not seg.exists()
    assert nbytes == sum(int(s * MB / 128) * 128 for s in self.SIZES.values())
    assert seen == [["ecamera.hevc", "qcamera.ts", "qlog.zst"], ["qcamera.ts", "qlog.zst"], ["qlog.zst"]]

  def test_unpaced_never_waits(self):
    seg = self.segment(self.seg_dir, sizes=self.SIZES)
    calls: list[float] = []
    assert policy.remove_dir(str(seg), 0., lambda t: calls.append(t) or False)[1:] == (4, True)
    assert calls == [] and not seg.exists()

  def test_the_exit_event_stops_it(self):
    seg = self.segment(self.seg_dir, sizes=self.SIZES)
    nbytes, files, complete = policy.remove_dir(str(seg), policy.ONROAD_FILE_PACE_S, lambda t: True)
    assert (files, complete) == (1, False)
    assert sorted(os.listdir(seg)) == ["ecamera.hevc", "qcamera.ts", "qlog.zst"]  # the qlog stays to the last

  def test_a_subdirectory_goes_too(self):
    seg = self.segment(self.seg_dir, sizes={"qlog.zst": .01})
    self.make_file_with_data(os.path.join(self.seg_dir, "sub"), "x.bin", .02)
    assert policy.remove_dir(str(seg), 0., lambda t: False)[1:] == (2, True)
    assert not seg.exists()

  def test_a_file_that_appears_meanwhile_goes_too(self):
    seg = self.segment(self.seg_dir, sizes=self.SIZES)

    def wait(t: float) -> bool:
      if not (seg / "late.bin").exists():
        (seg / "late.bin").write_bytes(b"x" * 4096)  # after the listing: rmdir finds the directory not empty
      return False

    nbytes, files, complete = policy.remove_dir(str(seg), policy.ONROAD_FILE_PACE_S, wait)
    assert (files, complete) == (4, True)
    assert not seg.exists()


class FakeTime:
  """deleter's `time` in run_thread: monotonic() is the test's clock."""

  def __init__(self, case: "DeleterCase"):
    self.case = case

  def monotonic(self) -> float:
    return self.case.clock


class TestDeleterThread(DeleterCase):
  def run_thread(self, waits: int, hook=None, grace: float | None = 0.) -> tuple[StopAfter, list]:
    """Runs the real deleter_thread for `waits` waits of its exit event, each advancing self.clock by its timeout.
    grace patches PARKED_GRACE_S (0: the routine work starts on the first parked pass); None keeps the real one."""
    def tick(n: int, t: float) -> None:
      self.clock += t
      if hook is not None:
        hook(n, t)

    ev = StopAfter(waits, tick)
    with contextlib.ExitStack() as stack:
      log = stack.enter_context(mock.patch.object(policy, "cloudlog"))
      stack.enter_context(mock.patch.object(policy, "WriteSampler", FakeSampler))
      stack.enter_context(mock.patch.object(deleter, "time", FakeTime(self)))
      if grace is not None:
        stack.enter_context(mock.patch.object(policy, "PARKED_GRACE_S", grace))
      deleter.deleter_thread(ev)
    self.log = log
    events = [c for c in log.event.call_args_list if c.args[0] == "deleter deleted"]
    for c in events:
      assert c.kwargs["error"] is True
    return ev, events

  def summaries(self) -> list:
    return [c for c in self.log.event.call_args_list if c.args[0] == "deleter parked summary"]

  def set_onroad(self, onroad: bool) -> None:
    self.params.put_bool("IsOffroad", not onroad, block=True)

  def test_driving_above_the_floor_only_waits(self):
    self.params.put_bool("IsOffroad", False, block=True)
    for i in range(3):
      self.segment(self.seg_format.format(i))
    ev, events = self.run_thread(4)
    assert ev.waits == [policy.IDLE_S] * 4 and events == [] and len(self.remaining()) == 3

  def test_driving_below_the_floor_one_file_at_a_time(self):
    self.params.put_bool("IsOffroad", False, block=True)
    self.free_pct, self.free_step = 9.2, 0.5  # two segments bring it back over 10 %
    segs = [self.segment(self.seg_format.format(i), uploaded=False) for i in range(3)]
    ev, events = self.run_thread(8)
    pace = policy.ONROAD_FILE_PACE_S
    # 3 files a segment: 2 waits inside it, 1 after it; then room enough
    assert ev.waits[:6] == [pace] * 6 and ev.waits[6:] == [policy.IDLE_S] * 2
    assert [s.exists() for s in segs] == [False, False, True]
    assert [(e.kwargs["why"], e.kwargs["onroad"], e.kwargs["pace_s"], e.kwargs["files"], e.kwargs["complete"]) for e in events] == \
           [(policy.CRITICAL, True, pace, 3, True)] * 2
    assert [e.kwargs["segment"] for e in events] == [self.seg_format.format(0), self.seg_format.format(1)]
    assert events[0].kwargs["mb"] > 0 and events[0].kwargs["free_pct"] == 9.2

  def test_an_emergency_is_not_paced(self):
    self.params.put_bool("IsOffroad", False, block=True)
    self.free_pct, self.free_step = 7.4, 2.
    for i in range(2):
      self.segment(self.seg_format.format(i))
    ev, events = self.run_thread(5)
    # 7.4 %: unpaced; 9.4 %: paced again (2 files apart, then after the segment); 11.4 %: room
    assert ev.waits == [policy.CRITICAL_RECHECK_S] + [policy.ONROAD_FILE_PACE_S] * 3 + [policy.IDLE_S]
    assert [e.kwargs["pace_s"] for e in events] == [0., policy.ONROAD_FILE_PACE_S]

  def test_parked_a_segment_at_a_time_until_the_drive_starts(self):
    self.free_pct, self.free_step = 12., 1.
    for i in range(6):
      self.segment(self.seg_format.format(i))

    def hook(n: int, t: float) -> None:
      if n == 2:
        self.params.put_bool("IsOffroad", False, block=True)  # ignition on after the second segment

    ev, events = self.run_thread(5, hook)
    assert ev.waits == [policy.ROUTINE_SEGMENT_PACE_S] * 2 + [policy.IDLE_S] * 3
    assert len(self.remaining()) == 4
    assert [(e.kwargs["why"], e.kwargs["onroad"], e.kwargs["pace_s"]) for e in events] == [(policy.ROUTINE, False, 0.)] * 2

  def test_parked_up_to_the_target_then_idle(self):
    self.free_pct, self.free_step = 18., 1.
    for i in range(4):
      self.segment(self.seg_format.format(i))
    ev, events = self.run_thread(4)
    assert ev.waits == [policy.ROUTINE_SEGMENT_PACE_S] * 2 + [policy.IDLE_S] * 2
    assert len(events) == 2 and len(self.remaining()) == 2

  def test_parked_waits_for_the_uploader(self):
    segs = [self.segment(self.seg_format.format(i), uploaded=False) for i in range(2)]

    def hook(n: int, t: float) -> None:
      if n == 2:
        for fn in ("qlog.zst", "qcamera.ts"):
          os.setxattr(str(segs[0] / fn), uploader.UPLOAD_ATTR_NAME, uploader.UPLOAD_ATTR_VALUE)

    ev, events = self.run_thread(4, hook)
    assert ev.waits == [policy.IDLE_S, policy.IDLE_S, policy.ROUTINE_SEGMENT_PACE_S, policy.IDLE_S]
    assert [s.exists() for s in segs] == [False, True]
    assert [e.kwargs["segment"] for e in events] == [self.seg_format.format(0)]

  def test_parked_below_the_floor_is_upstreams_pace(self):
    self.free_pct, self.free_step = 9., 0.6
    for i in range(3):
      self.segment(self.seg_format.format(i), uploaded=False)
    ev, events = self.run_thread(3)
    # two critical deletes at upstream's 0.1 s; at 10.2 % only uploaded segments go, and none is
    assert ev.waits == [policy.CRITICAL_RECHECK_S] * 2 + [policy.IDLE_S]
    assert [e.kwargs["why"] for e in events] == [policy.CRITICAL] * 2 and len(self.remaining()) == 1

  def test_all_locked_below_the_floor(self):
    self.free_pct = 9.
    self.make_file_with_data(self.seg_dir, "qlog.zst", lock=True)
    ev, events = self.run_thread(2)
    assert ev.waits == [policy.NOTHING_DELETABLE_S] * 2 and events == []

  def test_the_thread_skips_what_athena_will_send(self):
    asked, other = (self.segment(self.seg_format.format(i)) for i in range(2))
    self.params.put("AthenadUploadQueue", [queue_item(asked / "fcamera.hevc")], block=True)
    ev, events = self.run_thread(3)
    assert [e.kwargs["segment"] for e in events] == [other.name] and asked.exists()
    assert ev.waits == [policy.ROUTINE_SEGMENT_PACE_S, policy.IDLE_S, policy.IDLE_S]

  def test_a_drive_straight_after_boot_deletes_nothing(self):
    # manager writes IsOffroad=True at its start and clears it once `started`: the first seconds look parked
    for i in range(3):
      self.segment(self.seg_format.format(i))

    def hook(n: int, t: float) -> None:
      if self.clock >= 60.:
        self.set_onroad(True)

    ev, events = self.run_thread(6, hook, grace=None)
    assert events == [] and len(self.remaining()) == 3
    assert ev.waits == [policy.IDLE_S] * 6

  def test_each_parking_waits_out_the_grace(self):
    for i in range(4):
      self.segment(self.seg_format.format(i))
    grace, pace, idle = policy.PARKED_GRACE_S, policy.ROUTINE_SEGMENT_PACE_S, policy.IDLE_S
    first = math.ceil(grace / idle) * idle  # the first pass at or after the grace
    seen: list[tuple[float, int]] = []

    first_parking = [idle] * int(first / idle) + [pace]  # the grace's waits, then the one after the first delete

    def hook(n: int, t: float) -> None:
      seen.append((self.clock, len(self.remaining())))
      if n == len(first_parking):
        self.set_onroad(True)   # a drive, right after the first delete
      elif n == len(first_parking) + 1:
        self.set_onroad(False)  # and parked again, one pass later: the grace again

    ev, events = self.run_thread(2 * len(first_parking) + 1, hook, grace=None)
    assert ev.waits == first_parking + [idle] + first_parking
    assert [n for c, n in seen if c <= first] == [4] * int(first / idle)  # nothing before the grace
    deletes_at = [c for (c, n), (_, prev) in zip(seen[1:], seen, strict=False) if n < prev]
    drive_end = first + pace + idle
    assert deletes_at == [first + pace, drive_end + first + pace]  # one each parking, a full grace after it began
    assert len(events) == 2 and len(self.remaining()) == 2

  def test_the_parking_reaches_the_next_drives_summary(self):
    # parked: the grace, two routine deletes, then held by a segment the uploader has not sent; then a drive
    done = [self.segment(self.seg_format.format(i)) for i in range(2)]
    waiting = self.segment(self.seg_format2.format(0), uploaded=False)
    drive_at = 300.

    def hook(n: int, t: float) -> None:
      if self.clock >= drive_at:
        self.set_onroad(True)

    ev, events = self.run_thread(20, hook, grace=None)
    assert [e.kwargs["segment"] for e in events] == [p.name for p in done] and waiting.exists()
    self.log.exception.assert_not_called()
    (c,) = self.summaries()
    kw = c.kwargs
    assert kw["error"] is True and kw["deleted"] == {policy.ROUTINE: 2, policy.CRITICAL: 0}
    assert kw["deleted_mb"] > 0 and kw["deleting_s"] == 0.
    assert kw["waiting_for_upload"] is True  # still so 30 s into the drive
    assert kw["parked_s"] is not None and drive_at <= kw["parked_s"] < drive_at + policy.IDLE_S
    assert kw["policy"]["grace_s"] == policy.PARKED_GRACE_S and kw["policy"]["routine_pace_s"] == policy.ROUTINE_SEGMENT_PACE_S

  def test_a_parking_that_reached_the_target_is_not_waiting(self):
    self.free_pct, self.free_step = 18., 1.
    for i in range(2):
      self.segment(self.seg_format.format(i))
    self.segment(self.seg_format2.format(0), uploaded=False)

    def hook(n: int, t: float) -> None:
      if n == 3:
        self.set_onroad(True)

    self.run_thread(6, hook)
    (c,) = self.summaries()
    assert c.kwargs["deleted"][policy.ROUTINE] == 2 and c.kwargs["waiting_for_upload"] is False

  def test_a_parking_held_only_by_a_bookmark_is_not_waiting(self):
    self.segment(self.seg_format.format(1))
    bookmark = self.segment(self.seg_format.format(0), preserve_xattr=deleter.PRESERVE_ATTR_VALUE)

    def hook(n: int, t: float) -> None:
      if n == 3:
        self.set_onroad(True)

    ev, events = self.run_thread(6, hook)
    assert ev.waits[:3] == [policy.ROUTINE_SEGMENT_PACE_S, policy.IDLE_S, policy.IDLE_S] and bookmark.exists()
    (c,) = self.summaries()
    assert c.kwargs["deleted"][policy.ROUTINE] == 1 and c.kwargs["waiting_for_upload"] is False

  def test_stopped_mid_segment_says_so(self):
    self.set_onroad(True)
    self.free_pct = 9.2
    seg = self.segment(self.seg_format.format(0), uploaded=False)
    ev, events = self.run_thread(1)  # the exit event, set at the first wait: inside the paced segment
    (e,) = events
    assert (e.kwargs["files"], e.kwargs["complete"], e.kwargs["why"]) == (1, False, policy.CRITICAL)
    assert sorted(os.listdir(seg)) == ["qcamera.ts", "qlog.zst"]


class TestParkedSummary(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.disk = {"sda12": self.counters(writes=100, write_ms=10)}
    self.kb = [1000]
    p = mock.patch.object(policy, "WriteSampler", FakeSampler)
    p.start()
    self.addCleanup(p.stop)
    for name, fn in (("disk_devices", lambda path: ["sda12", "sda"]),
                     ("read_diskstats", lambda names: {k: dict(v) for k, v in self.disk.items()}),
                     ("_ext4_written_kb", lambda part: self.kb[0])):
      p = mock.patch.object(policy, name, side_effect=fn)
      p.start()
      self.addCleanup(p.stop)
    p = mock.patch.object(policy, "cloudlog")
    self.log = p.start()
    self.addCleanup(p.stop)

  @staticmethod
  def counters(**kw) -> dict[str, int]:
    return dict.fromkeys(DISKSTATS_FIELDS, 0) | kw

  def summaries(self):
    return [c for c in self.log.event.call_args_list if c.args[0] == "deleter parked summary"]

  def test_one_summary_per_parking_once_the_drive_is_recording(self):
    s = policy.ParkedSummary("/data/media/0/realdata", 10)
    s.update(False, 19.5, 0.)
    for _ in range(3):
      s.deleted(policy.ROUTINE, False, 120 * MB, 0.5)
    s.deleted(policy.CRITICAL, False, 118 * MB, 0.3)
    s.deleted(policy.CRITICAL, True, 999 * MB, 9.)  # while driving: its own line, not the parked summary's
    s.pass_done(False, False)
    s.pass_done(False, True)  # the last parked pass: held by an upload
    self.disk["sda12"] = self.counters(writes=2100, sectors_written=2 * 2**21, write_ms=4010)  # 2 GiB
    self.kb[0] = 1000 + 3 * 2**20
    s.update(False, 20.1, 400.)  # parked: sampled after WRITERS_SAMPLE_S
    assert s.sampler is not None and s.sampler.samples == 2
    s.update(True, 20.1, 1000.)  # ignition on: the last sample, nothing logged yet
    s.pass_done(True, False)  # the drive's passes say nothing about the parking
    s.update(True, 20.0, 1000. + policy.SUMMARY_DELAY_S - 1)
    assert self.summaries() == []
    s.update(True, 20.0, 1000. + policy.SUMMARY_DELAY_S)
    s.update(True, 19.9, 1100.)
    self.log.exception.assert_not_called()
    (c,) = self.summaries()
    kw = c.kwargs
    assert kw["error"] is True and kw["parked_s"] == 1000.
    assert kw["deleted"] == {policy.ROUTINE: 3, policy.CRITICAL: 1} and kw["deleted_mb"] == 478.
    assert kw["deleting_s"] == 1.8 and kw["free_pct_parked"] == 19.5 and kw["free_pct_now"] == 20.
    assert kw["disk_written_mb"] == 2048. and kw["disk_writes"] == 2000 and kw["disk_write_ms"] == 4000
    assert kw["ext4_written_mb"] == 3072.
    assert kw["writers"] == [{"comm": "updated", "pid": 42, "mb": 4096.}] and kw["writers_mb"] == 5120.
    assert kw["procs_unreadable"] == 3 and kw["policy"]["routine_pct"] == policy.ROUTINE_PERCENT
    assert kw["policy"]["floor_pct"] == 10 and kw["waiting_for_upload"] is True

    s.update(False, 18., 2000.)  # the next parking, nothing deleted
    s.update(True, 18., 2600.)
    s.update(True, 18., 2600. + policy.SUMMARY_DELAY_S)
    assert len(self.summaries()) == 2
    kw = self.summaries()[1].kwargs
    assert kw["deleted"] == {policy.ROUTINE: 0, policy.CRITICAL: 0} and kw["parked_s"] == 600.
    assert kw["waiting_for_upload"] is False  # a new parking starts without it

  def test_started_while_driving_has_nothing_to_say(self):
    s = policy.ParkedSummary("/data/media/0/realdata", 10)
    s.update(True, 15., 0.)
    s.update(True, 15., 100.)
    s.deleted(policy.CRITICAL, True, MB, 1.)
    assert self.summaries() == []

  def test_a_failure_is_logged_not_raised(self):
    s = policy.ParkedSummary("/data/media/0/realdata", 10)
    with mock.patch.object(policy, "read_diskstats", side_effect=RuntimeError("boom")):
      s.update(False, 15., 0.)
    self.log.exception.assert_called_once()
    s.update(False, 15., 1.)  # and it starts again
    assert s.window is not None
