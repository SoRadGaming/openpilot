"""FORK(COMM): the real hardware_thread with a sensor read stuck in the kernel (docs/fork/COMM-ISSUE.md section 9).

Routes 153 and 15d: deviceState stopped 4.12 s and 4.81 s because hardwared's loop sat 3.70 s in its power reads and
4.43 s in its thermal-zone reads, and selfdrived raised commIssueAvgFreq. Here a zone's read (or the battery read)
blocks in open(2) on a FIFO - a real kernel wait, with the GIL released as a sysfs read releases it - until the test
opens the other end. The receive times go through cereal's own FrequencyTracker and alive rule at selfdrived's 100 Hz.

* 4.8 s stuck (thermal, then power): deviceState keeps its 2 Hz, carries the last real readings, no alert; the stall is
  in the routes (the slow read with its name and time, the stall report, the episode).
* 10 s stuck: past MASK_MAX_S deviceState stops, as a stuck read stopped it before, and the comm check alerts; it
  comes back within a loop of the read returning.
* The thermal status never falls on old readings, and does once they are new.
"""
import contextlib
import math
import os
import queue
import tempfile
import threading
import time
from unittest import mock

import openpilot.cereal.messaging as messaging
from openpilot.cereal import log
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.hardware.base import ThermalConfig, ThermalZone
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.common.test import OpenpilotTestCase
from openpilot.common.version import terms_version, terms_version_sp, training_version
from openpilot.sunnypilot.system import sensor_snapshot
from openpilot.system.hardware import hardwared

ThermalStatus = log.DeviceState.ThermalStatus


class Stall:
  """One read blocks in open(2) on a FIFO until release() opens its write end."""

  def __init__(self, tmp: str):
    self.fifo = os.path.join(tmp, "stall")
    os.mkfifo(self.fifo)
    self.arm = threading.Event()
    self.entered = threading.Event()
    self.entered_at = math.nan
    self.released_at = math.nan
    self.held = math.nan   # the reading the sweep before the stuck one returned

  def block(self) -> None:
    self.arm.clear()
    self.entered_at = time.monotonic()
    self.entered.set()
    with open(self.fifo) as f:
      f.read()

  def release(self) -> None:
    with contextlib.suppress(OSError):
      fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)  # ENXIO if nothing waits
      os.write(fd, b"x")
      os.close(fd)
      self.released_at = time.monotonic()


class Zone(ThermalZone):
  def __init__(self, name, value, stall: Stall | None = None):
    super().__init__(name=name)
    self.value = value
    self.stall = stall
    self.last = math.nan

  def read(self) -> float:
    if self.stall is not None and self.stall.arm.is_set():
      self.stall.held = self.last
      self.stall.block()
    self.last = self.value()
    return self.last


def predict_alerts(recv: list[float], start: float, end: float) -> list[tuple[float, str]]:
  """selfdrived's view of deviceState at 100 Hz: cereal's FrequencyTracker and SubMaster's alive rule."""
  freq = SERVICE_LIST["deviceState"].frequency
  ft = messaging.FrequencyTracker(freq, 100., False)
  out, state, j, last = [], None, 0, None
  t = start
  while t < end:
    while j < len(recv) and recv[j] <= t:
      ft.record_recv_time(recv[j])
      last = recv[j]
      j += 1
    if last is not None:
      alive = (t - last) < 10. / freq
      s = "commIssue" if not alive else (None if ft.valid else "commIssueAvgFreq")
      if s is not None and s != state:
        out.append((round(t, 2), s))
      state = s
    t += 0.01
  return out


class Run:
  def __init__(self):
    self.recv: list[tuple[float, dict]] = []
    self.events: list[tuple[float, str, dict]] = []
    self.first = math.nan

  def named(self, name):
    return [(t, kw) for t, e, kw in self.events if e == name]

  def times(self):
    return [t for t, _ in self.recv]

  def gaps(self):
    ts = self.times()
    return [(a, b - a) for a, b in zip(ts, ts[1:], strict=False)]


def run_hardware_thread(cfg: ThermalConfig, run_s: float, script, ignition: bool = True, patches=()) -> Run:
  params = Params()
  params.put("HasAcceptedTerms", terms_version, block=True)
  params.put("HasAcceptedTermsSP", terms_version_sp, block=True)
  params.put("CompletedTrainingVersion", training_version, block=True)
  r = Run()
  pm = messaging.PubMaster(["pandaStates", "peripheralState", "selfdriveState"])
  sm = messaging.SubMaster(["deviceState"])
  stop, end_event = threading.Event(), threading.Event()
  real_event = cloudlog.event

  def record(event, *a, **kw):
    r.events.append((time.monotonic(), event, kw))
    return real_event(event, *a, **kw)

  def feeder():
    k = 0
    while not stop.is_set():
      if k % 10 == 0:
        ps = messaging.new_message("pandaStates", 1)
        ps.pandaStates[0].ignitionLine = ignition
        ps.pandaStates[0].pandaType = log.PandaState.PandaType.tres
        pm.send("pandaStates", ps)
      if k % 50 == 0:
        per = messaging.new_message("peripheralState")
        per.peripheralState.pandaType = log.PandaState.PandaType.tres
        per.peripheralState.voltage = 13000
        pm.send("peripheralState", per)
      k += 1
      time.sleep(0.01)

  with contextlib.ExitStack() as stack:
    stack.enter_context(mock.patch.object(hardwared.HARDWARE, "get_thermal_config", return_value=cfg))
    stack.enter_context(mock.patch.object(cloudlog, "event", side_effect=record))
    for p in patches:
      stack.enter_context(p)
    feed = threading.Thread(target=feeder, daemon=True)
    feed.start()
    hw = threading.Thread(target=hardwared.hardware_thread, args=(end_event, queue.Queue(maxsize=1)), daemon=True)
    hw.start()
    try:
      deadline = time.monotonic() + 30.
      while math.isnan(r.first) and time.monotonic() < deadline:
        sm.update(100)
        if sm.updated["deviceState"]:
          r.first = time.monotonic()
      assert not math.isnan(r.first), "no deviceState"
      worker = threading.Thread(target=script, args=(r,), daemon=True)
      worker.start()
      while time.monotonic() - r.first < run_s:
        sm.update(100)
        if sm.updated["deviceState"]:
          ds = sm["deviceState"]
          r.recv.append((time.monotonic(), {"cpu0": ds.cpuTempC[0] if len(ds.cpuTempC) else None,
                                            "status": ds.thermalStatus.raw, "power": ds.powerDrawW,
                                            "som": ds.somPowerDrawW}))
      worker.join(15.)
    finally:
      end_event.set()
      hw.join(15.)
      stop.set()
      feed.join(5.)
  assert not hw.is_alive()
  return r


class TestHardwaredSensorStall(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    hardwared.prev_offroad_states.clear()
    self.addCleanup(hardwared.prev_offroad_states.clear)
    self.tmp = tempfile.mkdtemp()
    self.stall = Stall(self.tmp)
    self.addCleanup(self.stall.release)  # never leave a worker in open(2)

  def thermal_cfg(self, value, stall_zone=True):
    return ThermalConfig(cpu=[Zone("cpu0", value, self.stall if stall_zone else None), Zone("cpu1", value)],
                         gpu=[Zone("gpu0", value)], memory=Zone("ddr", value), pmic=[Zone("pm", value)])

  def stall_for(self, seconds, after=3.):
    def script(r: Run):
      time.sleep(after)
      self.stall.arm.set()
      assert self.stall.entered.wait(5.), "the read never stuck"
      time.sleep(seconds - (time.monotonic() - self.stall.entered_at))
      self.stall.release()
    return script

  def check_masked(self, r: Run, worker: str, read: str, key: str, stuck: float):
    s = self.stall
    assert not math.isnan(s.entered_at) and not math.isnan(s.released_at)
    assert stuck - 0.1 <= s.released_at - s.entered_at <= stuck + 0.3
    gaps = r.gaps()
    assert max(g for _, g in gaps) < 1.0, f"deviceState stopped {max(g for _, g in gaps):.2f} s"
    inside = [m for t, m in r.recv if s.entered_at + 0.1 < t < s.released_at]
    assert len(inside) >= int(stuck / 0.5) - 1, len(inside)
    # the last real reading, held (Float32 on the wire)
    assert {round(m[key], 3) for m in inside} == {round(s.held, 3)}, (s.held, {m[key] for m in inside})
    assert not predict_alerts(r.times(), r.first + 5., r.times()[-1])
    slow = r.named("hardwared slow sensor read")
    assert any(kw["worker"] == worker and kw["reads"][0][0] == read and kw["reads"][0][1] >= stuck - 0.3 and kw["error"]
               for _, kw in slow), slow
    masked = r.named("deviceState masked a sensor stall")
    assert len(masked) == 1 and masked[0][1]["read"] == read and masked[0][1]["held_s"] == 0., masked
    assert masked[0][1]["max_age_s"] >= stuck - 0.5 and masked[0][0] > s.released_at
    assert not r.named("deviceState held: sensors stale")
    report = r.named("hardwared sensor stall report")
    assert len(report) == 1 and report[0][1]["read"] == read and report[0][0] < s.released_at, report
    assert report[0][1]["thread"]["stat"]["state"] == "S", report  # asleep in the kernel, in open(2)

  def test_a_4_8_s_thermal_stall_is_masked(self):
    n = [0]

    def value():
      n[0] += 1
      return 50. + 0.01 * (n[0] % 50)
    r = run_hardware_thread(self.thermal_cfg(value), 11., self.stall_for(4.8))
    self.check_masked(r, "thermal", "cpu[0]:cpu0", "cpu0", 4.8)

  def test_a_4_8_s_power_stall_is_masked(self):
    n = [0]

    def som():
      if self.stall.arm.is_set():
        self.stall.held = round(n[0] * 0.25, 2)
        self.stall.block()
      n[0] += 1
      return round(n[0] * 0.25, 2)
    r = run_hardware_thread(self.thermal_cfg(lambda: 45., stall_zone=False), 11., self.stall_for(4.8),
                            patches=[mock.patch.object(hardwared.HARDWARE, "get_som_power_draw", side_effect=som)])
    self.check_masked(r, "power", "som_power_draw", "som", 4.8)

  def test_a_stall_past_the_limit_stops_deviceState_as_before(self):
    r = run_hardware_thread(self.thermal_cfg(lambda: 50.), 16., self.stall_for(10.))
    s = self.stall
    start, gap = max(r.gaps(), key=lambda g: g[1])
    assert gap >= 3.5, gap
    assert start >= s.entered_at + sensor_snapshot.MASK_MAX_S - 1.5, (start - s.entered_at)  # masked first
    after = [t for t in r.times() if t > s.released_at]
    assert after and after[0] - s.released_at < 1.0, "deviceState did not come back within a loop of the read"
    alerts = predict_alerts(r.times(), r.first + 5., r.times()[-1])
    assert alerts and alerts[0][1] in ("commIssueAvgFreq", "commIssue"), alerts  # selfdrived alerts, as before
    held = r.named("deviceState held: sensors stale")
    assert len(held) == 1 and held[0][0] < s.released_at and held[0][1]["read"] == "cpu[0]:cpu0", held
    resumed = r.named("deviceState resumed")
    assert len(resumed) == 1 and resumed[0][1]["held_s"] >= 3., resumed

  def test_the_thermal_status_never_falls_on_old_readings(self):
    """Parked (no ignition), 101 C: critical. One real reading of 40 C, then the next read sticks 5.3 s. The filtered
    temperature falls toward 40 and without the rule the status would fall (critical -> overheated) about 2.5 s into
    the stall; it holds critical until the readings are new again, and then falls."""
    drop = threading.Event()
    state = {"v": 101.}

    def value():
      return state["v"]

    class DropZone(Zone):
      def read(self) -> float:
        if drop.is_set() and state["v"] == 101.:
          state["v"] = 40.
          assert self.stall is not None
          self.stall.arm.set()  # the next read of this zone sticks
          self.last = 40.
          return 40.
        return super().read()

    cfg = ThermalConfig(cpu=[DropZone("cpu0", value, self.stall)], gpu=[Zone("gpu0", value)], memory=Zone("ddr", value),
                        pmic=[Zone("pm", value)])

    def script(r: Run):
      time.sleep(3.)
      drop.set()
      assert self.stall.entered.wait(5.)
      time.sleep(5.3 - (time.monotonic() - self.stall.entered_at))
      self.stall.release()

    r = run_hardware_thread(cfg, 13., script, ignition=False)
    s = self.stall
    before = [m["status"] for t, m in r.recv if t < s.entered_at]
    assert before and before[-1] == ThermalStatus.critical, before
    inside = [m["status"] for t, m in r.recv if s.entered_at < t < s.released_at]
    assert len(inside) >= 9 and set(inside) == {ThermalStatus.critical}, inside
    after = [m["status"] for t, m in r.recv if t > s.released_at + 0.3]
    assert after and min(after) < ThermalStatus.critical, after  # new readings: it falls
