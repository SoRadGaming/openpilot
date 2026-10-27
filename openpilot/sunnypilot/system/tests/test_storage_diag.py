"""FORK(COMM-ISSUE): the storage diagnostics hardwared puts into the routes."""
import os
import tempfile
import time
from unittest import mock

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system import storage_diag
from openpilot.sunnypilot.system.storage_diag import LoopTimer, StorageReporter, WriteSampler, cpu_state, diskstats_delta, mount_for, \
  proc_write_bytes, read_diskstats, ufs_health

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

    def mnt(path: str) -> dict:
      m = mount_for(path, mounts)
      assert m is not None
      return m

    with mock.patch("os.path.realpath", side_effect=lambda p: p):
      assert mnt("/data/params/d")["device"] == "/dev/sda12"
      assert mnt("/data/safe_staging/merged/x")["fstype"] == "overlay"
      assert mnt("/var/tmp/power_watchdog")["fstype"] == "tmpfs"
      assert mnt("/dataX/y")["mountpoint"] == "/"
      assert mnt("/data")["options"] == "rw,relatime,discard,data=ordered"
    assert mount_for("/data", os.path.join(self.tmp, "no_such_mounts")) is None

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
      assert "ufs" in r and isinstance(r["cpu"]["cpufreq"], dict)  # FORK(STORAGE)
      assert isinstance(storage_diag.disk_devices(path), list)

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
    spawned: list[str] = []
    patcher = mock.patch.object(r, "_spawn", side_effect=spawned.append)
    patcher.start()
    self.addCleanup(patcher.stop)
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


def make_tree(root: str, files: dict[str, str]) -> None:
  for rel, text in files.items():
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
      f.write(text)


class TestStorageDiagPush16(OpenpilotTestCase):
  """FORK(STORAGE): the UFS's wear estimate and the CPU's state in the storage report, and the deleter's WriteSampler."""

  def setUp(self):
    super().setUp()
    self.tmp = tempfile.mkdtemp()

  def ufs_sysfs(self, health: bool) -> str:
    host = os.path.join(self.tmp, "devices/platform/soc/1d84000.ufshc")
    block = os.path.join(host, "host0/target0:0:0/0:0:0:0/block/sda")
    os.makedirs(block)
    files = {"string_descriptors/product_name": "KLUDG4UHDC-B0E1\n", "string_descriptors/manufacturer_name": "SAMSUNG\n",
             "clkscale_enable": "1\n"}
    if health:
      files.update({"health_descriptor/life_time_estimation_a": "0x01\n", "health_descriptor/life_time_estimation_b": "0x02\n",
                    "health_descriptor/eol_info": "0x01\n", "string_descriptors/serial_number": "not logged\n"})
    make_tree(host, files)
    os.makedirs(os.path.join(self.tmp, "class/block"))
    os.symlink(block, os.path.join(self.tmp, "class/block/sda"))
    return os.path.join(self.tmp, "class/block")

  def test_ufs_health_reads_the_health_descriptor(self):
    u = ufs_health("sda", self.ufs_sysfs(health=True))
    assert u is not None and u["host"].endswith("1d84000.ufshc")
    assert u["health_descriptor"] == {"eol_info": "0x01", "life_time_estimation_a": "0x01", "life_time_estimation_b": "0x02"}
    assert u["string_descriptors"]["product_name"] == "KLUDG4UHDC-B0E1"
    assert "serial_number" not in u["string_descriptors"] and "entries" not in u

  def test_ufs_health_without_one_lists_what_the_kernel_has(self):
    u = ufs_health("sda", self.ufs_sysfs(health=False))
    assert u is not None and u["health_descriptor"] is None
    assert u["entries"] == ["clkscale_enable", "host0", "string_descriptors"]
    assert ufs_health(None) is None
    assert ufs_health("nvme9n9", os.path.join(self.tmp, "nothing")) == {"host": None}

  def test_cpu_state(self):
    make_tree(self.tmp, {
      "cpufreq/policy0/related_cpus": "0 1 2 3\n", "cpufreq/policy0/scaling_max_freq": "1689600\n",
      "cpufreq/policy4/related_cpus": "4 5 6 7\n", "cpufreq/policy4/cpuinfo_max_freq": "2841600\n",
      "cpufreq/policy4/scaling_max_freq": "1689600\n", "cpufreq/policy4/scaling_cur_freq": "1689600\n",
      "kernel/sched_rt_runtime_us": "950000\n", "kernel/sched_rt_period_us": "1000000\n",
      "sched_features": "GENTLE_FAIR_SLEEPERS START_DEBIT RT_RUNTIME_SHARE NO_LB_MIN\n",
    })
    paths = (os.path.join(self.tmp, "cpufreq"), os.path.join(self.tmp, "kernel"), os.path.join(self.tmp, "sched_features"))
    c = cpu_state(*paths)
    assert sorted(c["cpufreq"]) == ["policy0", "policy4"]
    assert c["cpufreq"]["policy4"]["scaling_max_freq"] == "1689600" and c["cpufreq"]["policy4"]["cpuinfo_max_freq"] == "2841600"
    assert c["cpufreq"]["policy0"]["scaling_cur_freq"] is None
    assert c["sched_rt_runtime_us"] == "950000" and c["rt_runtime_share"] is True
    make_tree(self.tmp, {"sched_features": "NO_RT_RUNTIME_SHARE\n"})
    assert cpu_state(*paths)["rt_runtime_share"] is False
    assert cpu_state(sched_features=os.path.join(self.tmp, "no_debugfs"))["rt_runtime_share"] is None

  def proc(self, pid: int, comm: str, start: int, written: int | None) -> None:
    fields = ["S"] + ["0"] * 18 + [str(start)] + ["0"] * 10  # starttime, field 22, is the 20th after the comm
    files = {f"proc/{pid}/stat": f"{pid} ({comm}) " + " ".join(fields) + "\n"}
    if written is not None:
      files[f"proc/{pid}/io"] = f"rchar: 1\nwchar: 2\nsyscr: 3\nsyscw: 4\nread_bytes: 5\nwrite_bytes: {written}\ncancelled_write_bytes: 0\n"
    make_tree(self.tmp, files)

  def test_proc_write_bytes(self):
    self.proc(10, "loggerd", 100, 4096)
    self.proc(11, "kworker/u16:2", 50, None)  # root's: io unreadable
    self.proc(12, "a (weird) name", 900, 7)
    make_tree(self.tmp, {"proc/self": "", "proc/13/io": "write_bytes: 1\n"})  # not a pid; a pid without stat (gone)
    got, unreadable = proc_write_bytes(os.path.join(self.tmp, "proc"))
    assert got == {(10, 100): ("loggerd", 4096), (12, 900): ("a (weird) name", 7)} and unreadable == 1
    assert proc_write_bytes(os.path.join(self.tmp, "no_proc")) == ({}, 0)

  def test_write_sampler_counts_the_window_only(self):
    hz = os.sysconf("SC_CLK_TCK")
    make_tree(self.tmp, {"uptime": "50.00 10.00\n"})
    self.proc(10, "loggerd", 10 * hz, 1 * 2**20)   # there before the window: counts from its first sample
    self.proc(30, "idle", 5 * hz, 9 * 2**20)       # there before, writes nothing since
    w = WriteSampler(os.path.join(self.tmp, "proc"), os.path.join(self.tmp, "uptime"))
    self.proc(10, "loggerd", 10 * hz, 3 * 2**20)
    self.proc(20, "updated", 60 * hz, 40 * 2**20)  # started inside the window: all of it counts
    w.sample()
    self.proc(10, "loggerd", 70 * hz, 1 * 2**20)   # pid 10 again, a new process: a row of its own
    w.sample()
    assert w.samples == 3
    assert w.top() == [{"comm": "updated", "pid": 20, "mb": 40.}, {"comm": "loggerd", "pid": 10, "mb": 2.},
                       {"comm": "loggerd", "pid": 10, "mb": 1.}]
    assert w.total_bytes() == 43 * 2**20

  def test_write_sampler_without_uptime_counts_from_first_samples(self):
    self.proc(20, "updated", 10**9, 40 * 2**20)
    w = WriteSampler(os.path.join(self.tmp, "proc"), os.path.join(self.tmp, "no_uptime"))
    self.proc(20, "updated", 10**9, 41 * 2**20)
    w.sample()
    assert w.top() == [{"comm": "updated", "pid": 20, "mb": 1.}]

  def test_write_sampler_stays_bounded(self):
    make_tree(self.tmp, {"uptime": "1.0 1.0\n"})
    w = WriteSampler(os.path.join(self.tmp, "proc"), os.path.join(self.tmp, "uptime"))
    many = {(i, 10**9 + i): ("x", i) for i in range(storage_diag.WRITERS_TRACKED_MAX + 1)}
    with mock.patch.object(storage_diag, "proc_write_bytes", return_value=(many, 0)):
      w.sample()
    assert len(w.seen) == storage_diag.WRITERS_TRACKED_MAX // 2
    assert w.top(1) == [{"comm": "x", "pid": storage_diag.WRITERS_TRACKED_MAX, "mb": 0.0}]
