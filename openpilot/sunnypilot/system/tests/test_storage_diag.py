"""FORK(COMM-ISSUE): the storage diagnostics hardwared puts into the routes."""
import os
import tempfile
import time
from unittest import mock

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system import storage_diag
from openpilot.sunnypilot.system.storage_diag import LoopTimer, StorageReporter, diskstats_delta, mount_for, read_diskstats

MOUNTS = """\
/dev/root / ext4 ro,relatime 0 0
tmpfs /var tmpfs rw,nosuid,nodev,size=131072k 0 0
/dev/sda12 /data ext4 rw,relatime,discard,data=ordered 0 0
none /data/safe_staging/merged overlay rw,relatime 0 0
"""
DISKSTATS = """\
   8       0 sda 100 1 2000 30 400 5 9000 700 2 900 1200
   8      12 sda12 50 0 1000 10 300 3 8000 600 1 800 1100
 259       0 nvme0n1 1 2 3 4 5 6 7 8 9 10 11
"""


class TestStorageDiag(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.tmp = tempfile.mkdtemp()

  def write(self, name, text):
    path = os.path.join(self.tmp, name)
    with open(path, "w") as f:
      f.write(text)
    return path

  def test_mount_for_is_the_longest_mount_point(self):
    mounts = self.write("mounts", MOUNTS)
    with mock.patch("os.path.realpath", side_effect=lambda p: p):
      assert mount_for("/data/params/d", mounts)["device"] == "/dev/sda12"
      assert mount_for("/data/safe_staging/merged/x", mounts)["fstype"] == "overlay"
      assert mount_for("/var/tmp/power_watchdog", mounts)["fstype"] == "tmpfs"
      assert mount_for("/dataX/y", mounts)["mountpoint"] == "/"
      assert mount_for("/data", mounts)["options"] == "rw,relatime,discard,data=ordered"

  def test_diskstats_and_their_delta(self):
    path = self.write("diskstats", DISKSTATS)
    before = read_diskstats(["sda", "sda12"], path)
    assert before["sda12"]["writes"] == 300 and before["sda"]["weighted_ms"] == 1200
    after_text = DISKSTATS.replace("sda12 50 0 1000 10 300 3 8000 600 1 800 1100", "sda12 50 0 1000 10 340 3 8800 4600 7 4800 9100")
    after = read_diskstats(["sda", "sda12"], self.write("diskstats2", after_text))
    d = diskstats_delta(before, after)
    assert d["sda12"]["writes"] == 40 and d["sda12"]["write_ms"] == 4000 and d["sda12"]["io_ms"] == 4000
    assert d["sda12"]["in_flight"] == 7  # the value at the end, not a delta
    assert d["sda"]["writes"] == 0
    assert read_diskstats([], path) == {}

  def test_storage_report_never_raises(self):
    for path in ("/", self.tmp, "/no/such/path"):
      r = storage_diag.storage_report(path)
      assert r["path"] == path and "vm" in r and "fstrim" in r

  def test_loop_timer_logs_a_slow_section_at_most_once_per_interval(self):
    clock = [100.]
    with mock.patch.object(storage_diag.time, "monotonic", side_effect=lambda: clock[0]), \
         mock.patch.object(storage_diag, "cloudlog") as log:
      timer = LoopTimer("hardwared", self.tmp, threshold=0.3, min_interval=10.)

      def loop(*sections):
        timer.start()
        for name, dt in sections:
          clock[0] += dt
          timer.mark(name)
        return timer.finish(count=1)

      for _ in range(storage_diag.SKIP_FIRST_LOOPS):
        assert not loop(("thermal", 2.0))  # start-up loops are not reported
      assert not loop(("thermal", 0.01), ("send", 0.29))
      assert log.event.call_count == 0

      assert loop(("thermal", 0.01), ("tici_alert", 7.7), ("send", 0.001))
      assert log.event.call_count == 1
      args, kw = log.event.call_args
      assert args == ("hardwared slow loop section",) and kw["error"] is True
      assert kw["slowest"] == "tici_alert" and kw["slowest_s"] == 7.7
      assert kw["sections"] == {"tici_alert": 7.7} and kw["suppressed"] == 0 and kw["count"] == 1

      assert loop(("startup_params", 0.5))  # 0.5 s later: counted, not logged
      assert loop(("startup_params", 0.5))
      assert log.event.call_count == 1
      clock[0] += 10.
      assert loop(("free_space", 0.4))
      assert log.event.call_count == 2
      assert log.event.call_args[1]["suppressed"] == 2 and log.event.call_args[1]["slow_loops"] == 4

  def test_storage_reporter_once_at_start_and_once_per_onroad_start(self):
    r = StorageReporter(self.tmp, onroad_delay=30.)
    spawned = []
    r._spawn = spawned.append
    now = time.monotonic()
    r.update(None, now)
    r.update(None, now + 1)
    assert spawned == ["start"]
    r.update(now + 2, now + 20)  # onroad, not yet 30 s in
    assert spawned == ["start"]
    r.update(now + 2, now + 33)
    r.update(now + 2, now + 60)
    assert spawned == ["start", "onroad"]
    r.update(None, now + 70)
    r.update(now + 80, now + 111)  # the next drive
    assert spawned == ["start", "onroad", "onroad"]
