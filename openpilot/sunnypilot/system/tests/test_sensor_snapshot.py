"""FORK(COMM): sensor_snapshot.py's rules on their own (the real hardware_thread is test_hardwared_sensor_stall.py)."""
import math
import threading
import time
from unittest import mock

from openpilot.common.hardware.base import ThermalConfig, ThermalZone
from openpilot.common.swaglog import cloudlog
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system import sensor_snapshot as ss
from openpilot.sunnypilot.system.sensor_snapshot import DeviceStateGuard, SensorWorker, Snapshot


class Events:
  def __init__(self):
    self.lines: list[tuple[str, dict]] = []

  def __call__(self, event, *args, **kw):
    self.lines.append((event, kw))

  def named(self, name):
    return [kw for e, kw in self.lines if e == name]


def snap(seq, t):
  return Snapshot(seq, t, t + 0.001, {})


class FakeWorker:
  def __init__(self, name: str):
    self.name = name
    self.current: tuple[str, float, int] | None = None
    self.tid: int | None = threading.get_native_id()


class TestGuard(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.ev = Events()
    p = mock.patch.object(cloudlog, "event", side_effect=self.ev)
    p.start()
    self.addCleanup(p.stop)

  def test_publish_rule_by_age(self):
    g = DeviceStateGuard([FakeWorker("thermal"), FakeWorker("power")])
    for age, send, stale in ((0.5, True, False), (1.5, True, False), (1.51, True, True), (4.9, True, True),
                             (6.0, True, True), (6.01, False, True), (30., False, True)):
      d = g.update(100. + age, {"thermal": snap(1, 100.), "power": snap(1, 100. + age - 0.5)})
      assert (d.send, d.stale) == (send, stale), (age, d)
      assert math.isclose(d.age, age), (age, d.age)

  def test_the_oldest_worker_decides(self):
    g = DeviceStateGuard([FakeWorker("thermal"), FakeWorker("power")])
    d = g.update(10., {"thermal": snap(5, 9.5), "power": snap(1, 3.)})
    assert not d.send and d.stale and math.isclose(d.age, 7.)

  def test_no_snapshot_is_never_sent(self):
    g = DeviceStateGuard([FakeWorker("thermal")])
    d = g.update(10., {"thermal": None})
    assert not d.send and d.stale

  def test_each_stale_snapshot_is_named(self):
    """The thermal rule asks whether the thermal snapshot is stale, not whether any is: a slow power read alone must
    not hold the thermal status while the temperatures are new (review of push16; route 153's stall was a power read)."""
    g = DeviceStateGuard([FakeWorker("thermal"), FakeWorker("power")])
    d = g.update(10., {"thermal": snap(9, 9.5), "power": snap(2, 6.)})
    assert d.send and d.stale and d.stale_names == frozenset({"power"}), d
    d = g.update(10., {"thermal": snap(9, 8.), "power": snap(2, 6.)})
    assert d.stale_names == frozenset({"thermal", "power"}), d
    d = g.update(10., {"thermal": snap(9, 9.5), "power": snap(3, 9.6)})
    assert not d.stale and d.stale_names == frozenset(), d
    d = g.update(10., {"thermal": None, "power": snap(3, 9.6)})
    assert not d.send and d.stale_names == frozenset({"thermal"}), d

  def test_thermal_status_never_falls_while_stale(self):
    for prev in range(3):
      for new in range(3):
        assert DeviceStateGuard.thermal_status(prev, new, True) == max(prev, new)
        assert DeviceStateGuard.thermal_status(prev, new, False) == new

  def test_an_episode_is_logged_once_masked(self):
    w = FakeWorker("thermal")
    g = DeviceStateGuard([w])
    t0 = 1000.
    for k in range(10):  # a read stuck from t0: the last sweep started at t0 - 0.5
      w.current = ("cpu[0]:cpu0-gold-usr", t0, 7)
      g.update(t0 + 0.5 * k, {"thermal": snap(6, t0 - 0.5)})
    w.current = None
    g.update(t0 + 5.0, {"thermal": snap(8, t0 + 4.8)})
    g.update(t0 + 5.5, {"thermal": snap(9, t0 + 5.0)})
    masked = self.ev.named("deviceState masked a sensor stall")
    assert len(masked) == 1 and not self.ev.named("deviceState held: sensors stale"), self.ev.lines
    m = masked[0]
    assert m["read"] == "cpu[0]:cpu0-gold-usr" and m["worker"] == "thermal" and m["held_s"] == 0.
    assert math.isclose(m["max_age_s"], 5.0, abs_tol=1e-6)
    assert all(kw.get("error") is True for _, kw in self.ev.lines)

  def test_holding_is_logged_when_it_starts_and_when_it_ends(self):
    w = FakeWorker("power")
    g = DeviceStateGuard([w])
    sends = []
    for k in range(26):
      w.current = ("som_power_draw", 0., 3) if k < 22 else None
      s = snap(2, -0.5) if k < 22 else snap(4, 11.)
      sends.append(g.update(0.5 * k, {"power": s}).send)
    held = self.ev.named("deviceState held: sensors stale")
    resumed = self.ev.named("deviceState resumed")
    assert len(held) == 1 and held[0]["read"] == "som_power_draw" and held[0]["worker"] == "power"
    assert len(resumed) == 1 and resumed[0]["held_s"] > 4.
    assert sends[:12] == [True] * 12 and not any(sends[12:22]) and all(sends[22:]), sends

  def test_episodes_are_rate_limited_and_counted(self):
    g = DeviceStateGuard([FakeWorker("thermal")], min_log_interval=10.)
    t = 0.
    for _ in range(4):  # four 2.5 s episodes, 5 s apart
      for k in range(6):
        g.update(t + 0.5 * k, {"thermal": snap(1, t - 0.5)})
      t += 5.
      g.update(t - 2., {"thermal": snap(2, t - 2.1)})
    masked = self.ev.named("deviceState masked a sensor stall")
    assert len(masked) == 2, masked
    assert masked[1]["suppressed"] == 1 and masked[1]["episodes"] == 3

  def test_a_stuck_read_gets_one_report_from_its_own_thread(self):
    w = FakeWorker("thermal")
    g = DeviceStateGuard([w], kthread_view=lambda: {"t": 1., "threads": [{"pid": 9, "comm": "kworker/4:1", "cpu_bound": 4,
                                                                          "state": "R"}]})
    w.current = ("gpu[0]:gpu0-usr", 100., 11)
    for k in range(8):
      g.update(100. + 0.5 * k, {"thermal": snap(10, 99.5)})
    for t in g.report_threads:
      t.join(5.)
    assert len(g.report_threads) == 1 and g.report_threads[0].ident != threading.get_ident()
    rep = self.ev.named("hardwared sensor stall report")
    assert len(rep) == 1, self.ev.lines
    r = rep[0]
    assert r["read"] == "gpu[0]:gpu0-usr" and r["stuck_s"] >= 1.5 and r["kthreads"]["threads"][0]["comm"] == "kworker/4:1"
    assert r["thread"]["tid"] == w.tid and r["thread"]["stat"]["state"] in ("R", "S") and r["threads"]
    assert r["kthread_stacks"] is None  # sudo only on the device

  def test_the_sudo_stacks_cover_cores_4_5_and_6(self):
    """card runs on core 6 since push16: its kernel threads are in the report's stacks, as 4's and 5's are."""
    kthreads = {"t": 1., "threads": [{"pid": 10 + c, "comm": f"kworker/{c}:1", "cpu_bound": c, "state": "R"}
                                     for c in (0, 4, 5, 6, 7)]}
    kthreads["threads"].append({"pid": 30, "comm": "kworker/6:2", "cpu_bound": 6, "state": "S"})  # not runnable
    with mock.patch.object(ss.subprocess, "run", return_value=mock.Mock(stdout="[<0>] worker_thread", stderr="")) as run:
      out = ss._sudo_stacks(kthreads)
    assert out is not None and set(out) == {"kworker/4:1:14", "kworker/5:1:15", "kworker/6:1:16"}, out
    assert [c.args[0][-1] for c in run.call_args_list] == ["/proc/14/stack", "/proc/15/stack", "/proc/16/stack"]

  def test_a_new_stuck_read_gets_a_new_report(self):
    w = FakeWorker("thermal")
    g = DeviceStateGuard([w])
    w.current = ("a", 0., 1)
    g.update(2., {"thermal": snap(0, -0.5)})
    w.current = ("a", 3., 2)
    g.update(5., {"thermal": snap(1, 2.9)})
    for t in g.report_threads:
      t.join(5.)
    assert len(self.ev.named("hardwared sensor stall report")) == 2


class TestSensorWorker(OpenpilotTestCase):
  """A sweep publishes `latest` first and logs its slow reads after, on the worker's thread, so a test that has seen a
  sweep must still wait for that sweep's logging (wait_until). To keep a test from passing by luck when it does not,
  the gap is widened on purpose: task_schedstat(), which the worker calls between the two, takes 30 ms here."""

  def setUp(self):
    super().setUp()
    self.ev = Events()
    p = mock.patch.object(cloudlog, "event", side_effect=self.ev)
    p.start()
    self.addCleanup(p.stop)
    real = ss.task_schedstat

    def slow_schedstat(tid):
      time.sleep(0.03)
      return real(tid)
    p = mock.patch.object(ss, "task_schedstat", side_effect=slow_schedstat)
    p.start()
    self.addCleanup(p.stop)

  def wait_seq(self, w, seq, timeout=5.):
    deadline = time.monotonic() + timeout
    while (w.latest is None or w.latest.seq < seq) and time.monotonic() < deadline:
      time.sleep(0.005)
    assert w.latest is not None and w.latest.seq >= seq, w.latest

  def wait_until(self, cond, timeout=5.):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
      time.sleep(0.005)
    assert cond(), self.ev.lines

  def test_sweeps_are_numbered_and_replaced_whole(self):
    n = [0]

    def read():
      n[0] += 1
      return float(n[0])
    w = SensorWorker("t", [("a", read), ("b", lambda: 2.)])
    w.start()
    self.addCleanup(w.stop)
    w.request()
    self.wait_seq(w, 1)
    first = w.latest
    w.request()
    self.wait_seq(w, 2)
    assert first.values == {"a": 1., "b": 2.} and first.seq == 1 and w.latest.values == {"a": 2., "b": 2.}
    assert first.t_start <= first.t_end <= w.latest.t_start

  def test_requests_during_a_sweep_merge_into_one(self):
    gate = threading.Event()
    calls = [0]

    def slow():
      calls[0] += 1
      gate.wait(5.)
      return 1.
    w = SensorWorker("t", [("slow", slow)], slow_read_s=10.)
    w.start()
    self.addCleanup(w.stop)
    w.request()
    time.sleep(0.05)
    for _ in range(5):
      w.request()  # never waits
    gate.set()
    self.wait_seq(w, 2)
    time.sleep(0.1)
    assert calls[0] == 2 and w.latest.seq == 2

  def test_request_never_waits(self):
    gate = threading.Event()
    w = SensorWorker("t", [("stuck", lambda: gate.wait(10.) and 1.)], slow_read_s=10.)
    w.start()
    self.addCleanup(gate.set)
    self.addCleanup(w.stop)
    w.request()
    time.sleep(0.05)
    t = time.monotonic()
    for _ in range(100):
      w.request()
      w.age(time.monotonic())
      w.raise_if_failed()
    assert time.monotonic() - t < 0.05
    assert w.current is not None and w.current[0] == "stuck" and w.latest is None and math.isinf(w.age(time.monotonic()))

  def test_a_slow_read_is_logged_with_its_name_and_time(self):
    w = SensorWorker("power", [("power_draw", lambda: 1.), ("som_power_draw", lambda: time.sleep(0.4) or 2.)])
    w.start()
    self.addCleanup(w.stop)
    w.request()
    self.wait_seq(w, 1)
    self.wait_until(lambda: w.slow_sweeps == 1 and w._last_log > 0.)  # the line is written after the publish
    lines = self.ev.named("hardwared slow sensor read")
    assert len(lines) == 1, self.ev.lines
    line = lines[0]
    assert line["error"] is True and line["worker"] == "power"
    assert [r[0] for r in line["reads"]] == ["som_power_draw"] and 0.38 <= line["reads"][0][1] < 2.
    if line["self_s"] is not None:  # schedstat: a sleep is neither run time nor waiting for a CPU
      assert line["self_s"]["slept"] >= 0.3, line

  def test_a_read_under_the_threshold_is_not_logged(self):
    w = SensorWorker("t", [("a", lambda: time.sleep(0.2) or 1.)])
    w.start()
    self.addCleanup(w.stop)
    w.request()
    self.wait_seq(w, 1)
    w.request()
    self.wait_seq(w, 2)  # one thread: sweep 2 published means sweep 1's logging is over
    assert not self.ev.named("hardwared slow sensor read") and w.slow_sweeps == 0

  def test_slow_reads_are_rate_limited_and_counted(self):
    w = SensorWorker("t", [("a", lambda: time.sleep(0.32) or 1.)], min_log_interval=10.)
    w.start()
    self.addCleanup(w.stop)
    for k in range(1, 4):
      w.request()
      self.wait_seq(w, k)
    # sweep 3 is published before its slow read is counted: wait for that (suppressed is its last step), then end the
    # interval. Ending it earlier let sweep 3 log instead of sweep 4 (the reviewer's 2 in 10 failures)
    self.wait_until(lambda: w.slow_sweeps == 3 and w.suppressed == 2)
    w._last_log = -1e9  # the interval is over
    w.request()
    self.wait_seq(w, 4)
    self.wait_until(lambda: len(self.ev.named("hardwared slow sensor read")) == 2 and w.suppressed == 0)
    lines = self.ev.named("hardwared slow sensor read")
    assert len(lines) == 2 and lines[1]["suppressed"] == 2 and lines[1]["slow_sweeps"] == 4, lines
    assert lines[0]["slow_sweeps"] == 1 and lines[0]["suppressed"] == 0, lines

  def test_a_sweep_is_as_old_as_its_start(self):
    """A sweep that sat in a stuck read lands with the time it started: its first readings are that old, so the guard
    must see it stale on arrival (an age from the sweep's end would make 4.8 s old readings look new)."""
    gate = threading.Event()
    w = SensorWorker("t", [("first", lambda: 1.), ("stuck", lambda: gate.wait(5.) and 2.)], slow_read_s=10.)
    w.start()
    self.addCleanup(gate.set)
    self.addCleanup(w.stop)
    t_req = time.monotonic()
    w.request()
    time.sleep(0.6)
    gate.set()
    self.wait_seq(w, 1)
    now = time.monotonic()
    s = w.latest
    assert s.values == {"first": 1., "stuck": 2.}
    assert s.t_start - t_req < 0.3 and s.t_end - s.t_start >= 0.55 and w.age(now) >= 0.55, (s, now - t_req)
    assert DeviceStateGuard([w], fresh_s=0.5).update(now, {"t": s}).stale

  def test_a_read_that_raises_is_raised_on_the_loop(self):
    def bad():
      raise ValueError("invalid literal for int()")
    w = SensorWorker("t", [("a", bad)])
    w.start()
    w.request()
    assert w.wait_first(5.)
    assert isinstance(w.error, ValueError)
    with self.assertRaises(ValueError):
      w.raise_if_failed()
    w.thread.join(2.)
    assert not w.thread.is_alive()

  def test_wait_first_returns_with_the_first_sweep(self):
    w = SensorWorker("t", [("a", lambda: 3.)])
    w.start()
    self.addCleanup(w.stop)
    w.request()
    assert w.wait_first(5.) and w.latest.values == {"a": 3.}


class TestPowerMonitoringGivenPower(OpenpilotTestCase):
  def test_parked_it_integrates_the_power_it_is_given(self):
    """FORK(COMM): hardwared passes the power worker's reading; calculate() must not read the sensor itself."""
    from openpilot.system.hardware import power_monitoring as pmod
    pm = pmod.PowerMonitoring()
    t = [1000.]
    with mock.patch.object(pmod.HARDWARE, "get_current_power_draw", side_effect=AssertionError("read the sensor")), \
         mock.patch.object(pmod.time, "monotonic", side_effect=lambda: t[0]):
      pm.calculate(12500, False, current_power=4.0)
      t[0] += 3600.
      pm.calculate(12500, False, current_power=4.0)
    assert abs(pm.get_power_used() - 4e6) < 1., pm.get_power_used()  # 4 W for an hour, in uWh

  def test_without_it_the_sensor_is_read_as_upstream(self):
    from openpilot.system.hardware import power_monitoring as pmod
    pm = pmod.PowerMonitoring()
    t = [1000.]
    with mock.patch.object(pmod.HARDWARE, "get_current_power_draw", return_value=2.0) as read, \
         mock.patch.object(pmod.time, "monotonic", side_effect=lambda: t[0]):
      pm.calculate(12500, False)
      t[0] += 3600.
      pm.calculate(12500, False)
    assert read.called and abs(pm.get_power_used() - 2e6) < 1.


class TestThermalReads(OpenpilotTestCase):
  def test_the_sweep_rebuilds_get_msg(self):
    n = [0.]

    class Z(ThermalZone):
      def read(self) -> float:
        n[0] += 1.
        return n[0]

    cfg = ThermalConfig(cpu=[Z(f"cpu{i}") for i in range(3)], gpu=[Z("gpu0"), Z("gpu1")], dsp=Z("hvx"), memory=Z("ddr"),
                        pmic=[Z("pm0"), Z("pm1")], intake=Z("intake"), exhaust=None, gnss=Z("gnss"), bottomSoc=Z("soc"))
    reads = ss.thermal_reads(cfg)
    names = [r[0] for r in reads]
    assert names[:4] == ["cpu[0]:cpu0", "cpu[1]:cpu1", "cpu[2]:cpu2", "gpu[0]:gpu0"] and "dsp:hvx" in names
    assert len(names) == len(set(names)) == 12
    n[0] = 0.
    values = {name: fn() for name, fn in reads}
    n[0] = 0.
    assert ss.thermal_msg(cfg, values) == cfg.get_msg()

  def test_an_empty_config(self):
    assert ss.thermal_reads(ThermalConfig()) == [] and ss.thermal_msg(ThermalConfig(), {}) == {} == ThermalConfig().get_msg()
