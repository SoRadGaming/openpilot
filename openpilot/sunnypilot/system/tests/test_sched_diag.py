"""FORK(COMM): sched_diag.py, the passive starvation sampler (docs/fork/COMM-ISSUE.md section 9).

The parsers on real samples (this machine's /proc/schedstat and /proc/stat, a real /proc/<pid>/stat line) and on the
4.9 kernel's formats; a fake /proc tree for the rules (a kernel worker runnable and not run, the wait per CPU, the
windows, the drive summary, files that are not there); and one run against the real /proc.
"""
import os
import tempfile
import threading
import time
from unittest import mock

from openpilot.common.swaglog import cloudlog
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system import sched_diag as sd
from openpilot.sunnypilot.system.sched_diag import StarvationSampler

# captured from this machine's kernel (WSL2, 2026-10-28), cut to two CPUs
REAL_PROC_SCHEDSTAT = """version 15
timestamp 4294957531
cpu0 0 0 0 0 0 0 2973468899 360203063 73786
domain0 0003 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
domain1 ffff 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
cpu1 0 0 0 0 0 0 1292031884 69613229 11715
domain0 0003 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
"""
REAL_PROC_STAT = """cpu  1711 0 1678 412908 795 0 391 0 0 0
cpu0 38 0 208 25697 44 0 244 0 0 0
cpu1 54 0 53 25904 57 0 47 0 0 0
intr 12345 0 0
ctxt 99
"""
REAL_PID_STAT = " ".join((
  "1064 (cat) R 1030 1030 1030 34816 1030 4194304 106 0 0 0 0 0 0 0 20 0 1 0 26103 3350528 384",
  "18446744073709551615 100716580638720 100716580656305 140731184148640 0 0 0 0 0 0 0 0 0 17 5 0 0 0 0 0",
  "100716580670096 100716580671592 100717269843968 140731184157443 140731184157463 140731184157463 140731184160747 0"))


def stat_line(pid, comm, state, ppid=2, cpu=4, policy=0, ticks=0):
  """A /proc/<pid>/stat line as kernel 4.9 writes it (52 fields)."""
  return " ".join((f"{pid} ({comm}) {state} {ppid} 0 0 0 -1 69238880 0 0 0 0 {ticks} 0 0 0 20 0 1 0 543 0 0",
                   f"18446744073709551615 0 0 0 0 0 0 0 2147483647 0 0 0 0 17 {cpu} 0 {policy} 0 0 0 0 0 0 0 0 0 0 0"))


class FakeProc:
  def __init__(self, root: str, self_pid: int = 4242):
    self.root = root
    self.self_pid = self_pid
    self.thread(self_pid, self_pid, "hardwared", (5_000_000_000, 100_000_000, 900))
    self.write(f"{self_pid}/stat", stat_line(self_pid, "hardwared", "S", ppid=1, cpu=1))
    self.write(f"{self_pid}/schedstat", "5000000000 100000000 900\n")

  def write(self, rel, text):
    path = os.path.join(self.root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
      f.write(text)

  def task(self, pid, comm, state, cpu=4, schedstat=(0, 0, 0), ppid=2, policy=0, ticks=0):
    self.write(f"{pid}/stat", stat_line(pid, comm, state, ppid=ppid, cpu=cpu, policy=policy, ticks=ticks))
    if schedstat is not None:
      self.write(f"{pid}/schedstat", "{} {} {}\n".format(*schedstat))

  def thread(self, pid, tid, comm, schedstat):
    self.write(f"{pid}/task/{tid}/stat", stat_line(tid, comm, "S", ppid=1, cpu=2))
    self.write(f"{pid}/task/{tid}/schedstat", "{} {} {}\n".format(*schedstat))

  def stat(self, cpus: dict[int, tuple[int, int]]):
    """cpus: n -> (busy ticks, idle ticks)."""
    lines = ["cpu  0 0 0 0 0 0 0 0 0 0"] + [f"cpu{n} {b} 0 0 {i} 0 0 0 0 0 0" for n, (b, i) in sorted(cpus.items())]
    self.write("stat", "\n".join(lines) + "\n")

  def schedstat(self, cpus: dict[int, int], run: dict[int, int] | None = None):
    """cpus: n -> ns its tasks waited; run: n -> ns its tasks ran (1000 if not given)."""
    run = run or {}
    lines = ["version 15", "timestamp 1"] + [f"cpu{n} 0 0 0 0 0 0 {run.get(n, 1000)} {d} 10" for n, d in sorted(cpus.items())]
    self.write("schedstat", "\n".join(lines) + "\n")


class Events:
  def __init__(self):
    self.lines: list[tuple[str, dict]] = []

  def __call__(self, event, *a, **kw):
    self.lines.append((event, kw))

  def named(self, name):
    return [kw for e, kw in self.lines if e == name]


class TestParsers(OpenpilotTestCase):
  def test_real_proc_schedstat(self):
    got = sd.parse_proc_schedstat(REAL_PROC_SCHEDSTAT)
    assert got == {0: (2973468899, 360203063, 73786), 1: (1292031884, 69613229, 11715)}

  def test_proc_schedstat_absent_malformed_or_unknown(self):
    assert sd.parse_proc_schedstat(None) is None
    assert sd.parse_proc_schedstat("") is None
    assert sd.parse_proc_schedstat(REAL_PROC_SCHEDSTAT.replace("version 15", "version 14")) is None
    assert sd.parse_proc_schedstat("version x\ncpu0 1 2") is None
    assert sd.parse_proc_schedstat("version 15\ntimestamp 1\ncpu0 0 0 0\n") is None  # a short cpu line

  def test_real_proc_stat(self):
    got = sd.parse_proc_stat(REAL_PROC_STAT)
    assert got == {0: (38 + 208 + 244, 38 + 208 + 25697 + 44 + 244), 1: (54 + 53 + 47, 54 + 53 + 25904 + 57 + 47)}
    assert sd.parse_proc_stat(None) is None and sd.parse_proc_stat("intr 1\n") is None

  def test_pid_stat_real_and_4_9(self):
    assert sd.parse_pid_stat(REAL_PID_STAT) == {"comm": "cat", "state": "R", "ppid": 1030, "ticks": 0, "cpu": 5,
                                                "policy": 0}
    k = sd.parse_pid_stat(stat_line(77, "kworker/4:1H", "R", cpu=4, ticks=12))
    assert k == {"comm": "kworker/4:1H", "state": "R", "ppid": 2, "ticks": 12, "cpu": 4, "policy": 0}
    weird = sd.parse_pid_stat(stat_line(78, "a) (b", "S", ppid=1, policy=1))  # a comm with brackets in it
    assert weird is not None and weird["comm"] == "a) (b" and weird["policy"] == 1
    assert sd.parse_pid_stat(None) is None and sd.parse_pid_stat("12 (x) R 1") is None and sd.parse_pid_stat("junk") is None

  def test_pid_schedstat(self):
    assert sd.parse_pid_schedstat("123 456 7\n") == (123, 456, 7)
    assert sd.parse_pid_schedstat("0 0 0") == (0, 0, 0)
    assert sd.parse_pid_schedstat(None) is None and sd.parse_pid_schedstat("1 2") is None
    assert sd.parse_pid_schedstat("a b c") is None

  def test_bound_threads(self):
    assert sd.bound_cpu("kworker/4:1") == 4 and sd.bound_cpu("kworker/12:0H") == 12 and sd.bound_cpu("ksoftirqd/5") == 5
    for comm in ("kworker/u16:3", "kworker/u17:2H", "migration/4", "card", "kworker/4:1-events"):
      assert sd.bound_cpu(comm) is None, comm


class SamplerCase(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.tmp = tempfile.mkdtemp()
    self.fp = FakeProc(self.tmp)
    self.ev = Events()
    p = mock.patch.object(cloudlog, "event", side_effect=self.ev)
    p.start()
    self.addCleanup(p.stop)

  def sampler(self):
    return StarvationSampler(proc=self.tmp, self_pid=self.fp.self_pid)


class TestProbe(SamplerCase):
  def test_everything_there(self):
    self.fp.stat({0: (1, 1)})
    self.fp.schedstat({0: 1})
    self.fp.write("pressure/cpu", "some avg10=0.00\n")
    caps = self.sampler().caps
    assert caps["pid_schedstat"] is True and caps["proc_schedstat"] is True and caps["proc_stat"] is True
    assert caps["pressure"] is True and caps["sched_debug"] is False and caps["own_stack"] is False

  def test_nothing_there(self):
    caps = StarvationSampler(proc=os.path.join(self.tmp, "missing"), self_pid=1).caps
    assert caps["pid_schedstat"] is None and not caps["proc_schedstat"] and not caps["proc_stat"] and not caps["pressure"]

  def test_compiled_in_but_off(self):
    self.fp.write(f"{self.fp.self_pid}/schedstat", "0 0 0\n")
    assert self.sampler().caps["pid_schedstat"] is False

  def test_the_real_proc(self):
    caps = sd.probe()
    assert set(caps) == {"pid_schedstat", "proc_schedstat", "proc_stat", "pressure", "sched_debug", "own_stack", "affinity"}
    assert caps["proc_stat"] is True and caps["affinity"]


class TestStuck(SamplerCase):
  def test_a_runnable_kworker_that_never_runs_is_logged_once(self):
    s = self.sampler()
    s.started = True
    self.fp.task(101, "kworker/4:1", "R", cpu=4, schedstat=(1_000, 50_000, 3))
    self.fp.task(102, "kworker/5:0", "S", cpu=5, schedstat=(9_000, 1_000, 8))
    for t in (0., 2., 4., 6.):
      s.poll(t)
    lines = self.ev.named("kthread runnable, not run")
    assert len(lines) == 1, self.ev.lines
    line = lines[0]
    assert line["comm"] == "kworker/4:1" and line["cpu"] == 4 and line["for_s"] >= sd.STUCK_LOG_S and line["error"] is True
    view = s.view()
    stuck = [t for t in view["threads"] if t["pid"] == 101]
    assert stuck and stuck[0]["state"] == "R" and stuck[0]["stuck_s"] >= 4. and stuck[0]["cpu_bound"] == 4
    # it runs: the episode is over; stuck again later, logged again
    self.fp.task(101, "kworker/4:1", "S", cpu=4, schedstat=(9_000, 4_000_050_000, 4))
    s.poll(8.)
    self.fp.task(101, "kworker/4:1", "R", cpu=4, schedstat=(9_000, 4_000_050_000, 4))
    for t in (20., 22., 24.):
      s.poll(t)
    assert len(self.ev.named("kthread runnable, not run")) == 2

  def test_the_view_covers_cores_4_5_and_6(self):
    """The stall report's view: the cores the real-time processes fill, card's core 6 among them since push16."""
    s = self.sampler()
    s.started = True
    for pid, comm, cpu in ((110, "kworker/0:1", 0), (114, "kworker/4:1", 4), (115, "kworker/5:0H", 5),
                           (116, "kworker/6:2", 6), (126, "ksoftirqd/6", 6), (117, "kworker/7:1", 7)):
      self.fp.task(pid, comm, "S", cpu=cpu, schedstat=(1, 1, 1))
    s.poll(0.)
    v = s.view()
    assert sorted(t["comm"] for t in v["threads"]) == ["ksoftirqd/6", "kworker/4:1", "kworker/5:0H", "kworker/6:2"], v
    assert sd.VIEW_CPUS == (4, 5, 6)

  def test_a_kworker_that_runs_between_samples_is_not_stuck(self):
    s = self.sampler()
    s.started = True
    for k, t in enumerate((0., 2., 4., 6., 8.)):
      self.fp.task(101, "kworker/4:1", "R", cpu=4, schedstat=(1_000 + k, 0, 3 + k))
      s.poll(t)
    assert not self.ev.named("kthread runnable, not run")

  def test_without_schedstat_the_ticks_decide(self):
    s = self.sampler()
    s.started = True
    for t in (0., 2., 4.):
      self.fp.task(101, "kworker/4:1", "R", cpu=4, schedstat=None, ticks=7)
      s.poll(t)
    assert len(self.ev.named("kthread runnable, not run")) == 1

  def test_lines_are_rate_limited(self):
    s = self.sampler()
    s.started = True
    for pid in (101, 102, 103):
      self.fp.task(pid, f"kworker/4:{pid - 100}", "R", cpu=4, schedstat=(1, 1, 1))
    for t in (0., 2., 4.):
      s.poll(t)
    assert len(self.ev.named("kthread runnable, not run")) == 1  # three threads, one line in 10 s


class TestWindows(SamplerCase):
  def run_window(self, s, t0, kwait_ns: dict[int, int], busy=None):
    """One 60 s window from t0: each bound thread's run delay grows by kwait_ns[cpu] over it."""
    for k in range(31):
      t = t0 + 2. * k
      for cpu, total in kwait_ns.items():
        self.fp.task(200 + cpu, f"kworker/{cpu}:0", "S", cpu=cpu, schedstat=(10 + k, int(total * k / 30), 5 + k))
      if busy is not None:
        self.fp.stat({c: (int(b * k * 100), int((100 - b) * k * 100)) for c, b in busy.items()})
      s.poll(t)

  def test_a_starved_cpu_makes_a_line(self):
    s = self.sampler()
    s.started = True
    self.fp.task(300, "card", "S", ppid=1, cpu=6, policy=1, schedstat=(0, 0, 0))
    self.run_window(s, 0., {4: 1_500_000_000, 0: 100_000_000}, busy={4: 99, 6: 55})
    lines = self.ev.named("sched starvation")
    assert len(lines) == 1, self.ev.lines
    line = lines[0]
    assert line["error"] is True and line["window_s"] == 60.
    assert line["cpu"]["4"]["kwait_s"] == 1.5 and line["cpu"]["4"]["kwait_max_s"] == 1.5 and line["cpu"]["0"]["kwait_s"] == 0.1
    assert line["cpu"]["4"]["busy"] == 99.0 and line["cpu"]["6"]["busy"] == 55.0
    assert "card:300" in line["rt"] and any(k.startswith("hardwared:") for k in line["hardwared"])
    assert line["caps"]["pid_schedstat"] is True and line["prev_drive"] is None

  def test_the_first_window_and_every_tenth_are_a_baseline(self):
    s = self.sampler()
    s.started = True
    for w in range(12):
      self.run_window(s, 60. * w + (0. if w == 0 else 2.), {4: 10_000_000})
    base = self.ev.named("sched baseline")
    assert [b["window"] for b in base] == [1, 10], [b["window"] for b in base]
    assert "caps" in base[0] and "caps" not in base[1] and not self.ev.named("sched starvation")

  def test_a_drive_summary_when_it_ends_and_in_the_next_drive(self):
    s = self.sampler()
    s.started = True
    self.run_window(s, 0., {4: 2_000_000_000})
    self.run_window(s, 62., {4: 2_000_000_000})
    s.started = False
    s.poll(130.)
    summ = self.ev.named("sched drive summary")
    assert len(summ) == 1 and summ[0]["error"] is True, self.ev.lines
    d = summ[0]
    assert d["windows"] == 3 and d["starved_windows"] == 2 and d["worst"]["cpu"] == 4  # the third is the drive's last 10 s
    assert d["cpu"]["4"]["kwait_s"] >= 3.9
    s.poll(132.)  # parked: nothing
    n = len(self.ev.lines)
    s.started = True
    self.run_window(s, 200., {4: 1_000})
    first = [kw for e, kw in self.ev.lines[n:] if e in ("sched baseline", "sched starvation")]
    assert first and first[0]["prev_drive"]["windows"] == 3

  def test_parked_nothing_is_read(self):
    s = self.sampler()
    with mock.patch.object(sd, "read_text", side_effect=AssertionError("read while parked")):
      for t in (0., 2., 4.):
        s.poll(t)
    assert not self.ev.lines

  def test_missing_files_are_tolerated(self):
    s = StarvationSampler(proc=os.path.join(self.tmp, "nothing"), self_pid=1)
    s.started = True
    for t in range(0, 64, 2):
      s.poll(float(t))
    line = self.ev.named("sched baseline")
    assert len(line) == 1 and line[0]["cpu"] == {} and line[0]["rt"] == {}

  def test_a_thread_that_goes_away(self):
    s = self.sampler()
    s.started = True
    self.fp.task(101, "kworker/4:1", "R", cpu=4, schedstat=(1, 1, 1))
    s.poll(0.)
    os.remove(os.path.join(self.tmp, "101", "stat"))
    s.poll(2.)
    self.fp.task(101, "bash", "R", cpu=4, ppid=1)  # the pid reused
    s.poll(4.)
    assert not self.ev.named("kthread runnable, not run")

  def test_rq_wait_from_proc_schedstat(self):
    s = self.sampler()
    s.started = True
    for k in range(31):
      self.fp.schedstat({4: 2_000_000_000 * k // 30, 0: 0})
      self.fp.task(204, "kworker/4:0", "S", cpu=4, schedstat=(1, 0, 1))
      s.poll(2. * k)
    line = self.ev.named("sched baseline")[0]
    assert line["cpu"]["4"]["rq_wait_s"] == 2.0 and line["cpu"]["0"]["rq_wait_s"] == 0.0, line["cpu"]

  def test_run_share_from_proc_schedstat(self):
    """/proc/stat's busy is tick-sampled and aliases with the 100 Hz loops on a core that is not full (the review:
    route 01's core 4 read 28-52 % with 60 % of real run time); /proc/schedstat's run time is exact, per window and
    for the drive. Here core 4 runs 51 % and core 6 57 % of the time, while /proc/stat says 30 and 80."""
    s = self.sampler()
    s.started = True
    for k in range(31):
      t = 2. * k
      self.fp.schedstat({4: 0, 6: 0, 0: 0}, run={4: int(0.51 * t * 1e9), 6: int(0.57 * t * 1e9), 0: 0})
      self.fp.stat({4: (30 * k, 70 * k), 6: (80 * k, 20 * k)})
      self.fp.task(204, "kworker/4:0", "S", cpu=4, schedstat=(1, 0, 1))
      s.poll(t)
    cpu = self.ev.named("sched baseline")[0]["cpu"]
    assert (cpu["4"]["run_pct"], cpu["6"]["run_pct"], cpu["0"]["run_pct"]) == (51.0, 57.0, 0.0), cpu
    assert (cpu["4"]["busy"], cpu["6"]["busy"]) == (30.0, 80.0), cpu
    for k in range(31, 46):  # 30 s more at 25 % on core 4, then the drive ends
      t = 2. * k
      self.fp.schedstat({4: 0, 6: 0, 0: 0}, run={4: int((0.51 * 60. + 0.25 * (t - 60.)) * 1e9), 6: int(0.57 * t * 1e9),
                                                 0: 0})
      s.poll(t)
    s.started = False
    s.poll(90.)
    d = self.ev.named("sched drive summary")[0]["cpu"]
    assert d["4"]["run_pct"] == round(100. * (0.51 * 60. + 0.25 * 30.) / 90., 1) and d["6"]["run_pct"] == 57.0, d

  def test_without_proc_schedstat_run_share_is_none(self):
    s = self.sampler()
    s.started = True
    for k in range(31):
      self.fp.stat({4: (50 * k, 50 * k)})
      s.poll(2. * k)
    cpu = self.ev.named("sched baseline")[0]["cpu"]
    assert cpu["4"]["run_pct"] is None and cpu["4"]["busy"] == 50.0, cpu


class TestThread(SamplerCase):
  def test_a_poll_that_raises_does_not_stop_the_thread(self):
    s = StarvationSampler(proc=self.tmp, self_pid=self.fp.self_pid, poll_s=0.01)
    calls = []

    def boom(now):
      calls.append(now)
      raise RuntimeError("bad /proc")
    with mock.patch.object(s, "poll", side_effect=boom), mock.patch.object(cloudlog, "exception") as exc:
      s.start()
      deadline = time.monotonic() + 5.
      while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
      s.stop()
      s.thread.join(2.)
    assert len(calls) >= 3 and exc.called and not s.thread.is_alive()

  def test_against_the_real_proc(self):
    s = StarvationSampler()
    s.started = True
    s.poll(time.monotonic())
    s.poll(time.monotonic() + 2.)
    v = s.view()
    assert isinstance(v["threads"], list) and v["t"] is not None
    s.started = False
    s.poll(time.monotonic() + 4.)
    assert self.ev.named("sched drive summary")  # a 4 s drive: its summary, and nothing raised
    assert threading.current_thread().is_alive()
