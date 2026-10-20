"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): KTuner fix 1's detector (diag_tool_guard.py) on synthetic frames in card's exact can_list
shape [(logMonoTime, [(addr, dat, src), ...]), ...]. What must hold: only a tester REQUEST received from another node
on the car bus counts (never a response, never our own echo or the radar side, never the boards' ids - also when they
arrive through update() at their real rates); two within a second, parked and idle, enter; ten seconds without one,
counted only while the car bus is heard on src 0, and parked, leave - 30 s, then 60 s, for a tool that keeps coming
back within two minutes of a release (the back-off); nothing on any other car. Times are written out here, not taken
from the module's constants, so a changed constant fails.
"""
from types import SimpleNamespace

from opendbc.car import structs
from opendbc.car.honda.values import CAR, HondaSafetyFlags
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.car.diag_tool_guard import DiagToolGuard, is_tester_request, REQ_WINDOW_NS, REQ_MIN, \
  QUIET_NS, QUIET_STEPS_NS, REENTRY_NS, QUIET_GAP_CAP_NS, armed_for

GearShifter = structs.CarState.GearShifter
MS = 1_000_000
STEP_NS = 10 * MS   # card's 100 Hz
KTUNER = 0x18DA0EF1
ECU_RESPONSE = 0x18DAF10E
OTHER = 0x1A6       # any ordinary car-bus frame


def make_cp(fingerprint=CAR.HONDA_ACCORD_9G_AU, brand="honda", passive=False, stock_acc=False):
  cp = structs.CarParams()
  cp.brand = brand
  cp.carFingerprint = fingerprint
  cp.passive = passive
  cfg = structs.CarParams.SafetyConfig()
  cfg.safetyModel = structs.CarParams.SafetyModel.hondaNidec
  cfg.safetyParam = int(HondaSafetyFlags.ELESYS_STOCK_ACC if stock_acc else HondaSafetyFlags.ELESYS_SCM_STANDDOWN) | 4
  cp.safetyConfigs = [cfg]
  return cp


def cs(gear=GearShifter.park, standstill=True, can_valid=True, v_ego=0.0):
  return SimpleNamespace(gearShifter=gear, standstill=standstill, canValid=can_valid, vEgo=v_ego)


def cc(enabled=False, lat=False, lon=False):
  return SimpleNamespace(enabled=enabled, latActive=lat, longActive=lon)


DRIVING = {"gear": GearShifter.drive, "standstill": False, "v_ego": 10.0}
S = 100             # card steps in a second


class Sim:
  """Feeds a guard one card step at a time: one can message per step, plus the frames asked for."""
  def __init__(self, cp=None, inherited=False):
    self.g = DiagToolGuard(cp or make_cp(), inherited)
    self.t = 1_000 * MS
    self.records: list[tuple[str, dict]] = []

  def step(self, frames=(), rx=True, cs_kw=None, cc_kw=None, cc_alive=True, n=1, other=()):
    """other: frames on other sources in every step (our echoes, the radar side), heard whatever bus 0 does."""
    for _ in range(n):
      body = list(frames) + ([(OTHER, b"", 0)] if rx else []) + list(other)
      r = self.g.update([(self.t, body)] if body else [], cs(**(cs_kw or {})), cc(**(cc_kw or {})), cc_alive)
      if r is not None:
        self.records.append(r)
      self.t += STEP_NS
    return self.g.stock

  def requests(self, n, every_ns=STEP_NS, addr=KTUNER, src=0, **kw):
    """n requests, one every every_ns, stepping card in between."""
    per = max(1, every_ns // STEP_NS)
    for _ in range(n):
      self.step(frames=[(addr, b"\x22\xf4\x0c", src)], **kw)
      self.step(n=per - 1, **kw)
    return self.g.stock


class TestClassification(OpenpilotTestCase):
  def test_requests(self):
    for a in (0x18DA0EF1, 0x18DA0FF1, 0x18DA12F1, 0x18DA1EF1, 0x18DBEFF1, 0x18DB33F1, 0x18DA10F0, 0x18DA28FD, 0x7DF,
              *range(0x7E0, 0x7E8)):
      assert is_tester_request(a), hex(a)

  def test_not_requests(self):
    for a in (0x18DAF10E, 0x18DAF1B0, 0x18DAF160, *range(0x7E8, 0x7F0), *range(0x700, 0x720), *range(0x620, 0x630),
              0xE4, 0x1FA, 0x30C, 0x33D, 0x500, 0x1A6, 0x7DE, 0x7FF, 0x800,
              0x18DA0E10, 0x18DA0EEF, 0x18DA0EFE,     # source address outside the tester range
              0x18DAF0F1, 0x18DAFDF1,                 # tester to tester
              0x18DC0EF1, 0x18EA0EF1, 0x1CDA0EF1):    # not the 0x18DA/0x18DB diagnostic PGNs
      assert not is_tester_request(a), hex(a)


class TestSourceAndThreshold(OpenpilotTestCase):
  def test_other_sources_never_count(self):
    for src in (1, 2, 128, 129, 130, 192, 194):
      s = Sim()
      assert not s.requests(300, src=src), src
      assert s.records == []

  def test_responses_never_count(self):
    s = Sim()
    assert not s.requests(300, addr=ECU_RESPONSE)

  def test_one_request_is_not_enough(self):
    s = Sim()
    s.requests(1)
    assert not s.step(n=500)

  def test_two_within_a_second_enter(self):
    s = Sim()
    s.requests(1)
    s.step(n=98)                     # 0.99 s later
    assert s.requests(1)
    name, rec = s.records[-1]
    assert name == "diag_tool_stock" and rec["on"] and rec["ids"] == {"0x18DA0EF1": 2} and rec["requests_1s"] == 2

  def test_two_in_one_message_enter(self):
    s = Sim()
    assert s.step(frames=[(KTUNER, b"", 0), (0x18DA12F1, b"", 0)])

  def test_two_more_than_a_second_apart_do_not(self):
    s = Sim()
    s.requests(1)
    s.step(n=110)                    # 1.1 s
    assert not s.requests(1)
    assert not s.step(n=300)

  def test_eleven_bit_obd_requests_through_update(self):
    """A generic ELM327 / scan-tool dongle: 0x7DF functional, 0x7E0-0x7E7 physical - and never its answers."""
    for addr in (0x7DF, 0x7E0, 0x7E7):
      assert Sim().requests(2, addr=addr), hex(addr)
    for addr in (0x7E8, 0x7EF):
      assert not Sim().requests(300, addr=addr), hex(addr)


class TestTheBusIsNotATool(OpenpilotTestCase):
  def test_everything_else_on_src_0_at_its_real_rate(self):
    """Review B 2: what is really on src 0, through update(), parked and idle for a minute - the EPS board's 0x700-0x71F
    (0x700/0x701/0x70C at 100 Hz on route 143), the BLR board's 0x620-0x62F, the ECUs' answers to our FW query
    (0x18DAF1xx) and to a scan tool (0x7E8-0x7EF), and the car's own traffic: never stock, no record."""
    board = list(range(0x700, 0x720))
    blr = list(range(0x620, 0x630))
    answers = [0x18DAF10E, 0x18DAF127, 0x18DAF153, 0x18DAF15C, 0x18DAF160, 0x18DAF1B0, 0x18DAF130, *range(0x7E8, 0x7F0)]
    car = [0x091, 0x0E4, 0x13C, 0x156, 0x158, 0x17C, 0x1A6, 0x1FA, 0x30C, 0x33D, 0x39F, 0x500, 0x510]
    frames = [(a, b"", 0) for a in board + blr + answers + car]
    s = Sim()
    assert not s.step(frames=frames, n=60 * S)
    assert s.records == [] and len(s.g._req) == 0

  def test_our_fw_query_on_other_sources(self):
    """openpilot's own requests: src 2 through the closed relay, 128/129/130 as echoes - at a burst rate."""
    for src in (2, 128, 129, 130):
      s = Sim()
      assert not s.step(frames=[(0x18DA10F1, b"", src), (0x18DB33F1, b"", src), (0x7DF, b"", src), (0x7E0, b"", src)], n=500)
      assert s.records == [], src


class TestParkedAndActive(OpenpilotTestCase):
  def test_moving_never_enters_and_logs_once(self):
    s = Sim()
    assert not s.requests(500, cs_kw=DRIVING)
    assert [n for n, _ in s.records] == ["diag_tool_seen"]
    assert s.records[0][1]["stock"] is False and s.records[0][1]["parked"] is False

  def test_stopping_in_p_with_the_tool_still_talking_enters(self):
    s = Sim()
    s.requests(200, cs_kw=DRIVING)
    assert s.requests(2)

  def test_stopping_in_p_after_the_tool_stopped_does_nothing(self):
    s = Sim()
    s.requests(200, cs_kw=DRIVING)
    s.step(n=200, cs_kw=DRIVING)
    assert not s.step(n=1000)

  def test_can_invalid_blocks_entry(self):
    s = Sim()
    assert not s.requests(300, cs_kw={"can_valid": False})
    assert s.requests(2)

  def test_a_tool_before_the_car_can_is_valid_is_not_logged_as_seen(self):
    """Every KTuner route: requests from 0.01-0.2 s, the car's CAN valid (and the gear known) from 2.5-3.5 s. No
    "seen while it cannot act" for that wait - just the one entry record once the car is known to be parked."""
    s = Sim()
    s.requests(300, cs_kw={"can_valid": False, "gear": GearShifter.unknown})
    assert s.requests(2)
    assert [(n, r.get("on")) for n, r in s.records] == [("diag_tool_stock", True)], s.records

  def test_gear_and_standstill(self):
    for kw in ({"gear": GearShifter.neutral}, {"gear": GearShifter.drive}, {"standstill": False}):
      assert not Sim().requests(100, cs_kw=kw), kw

  def test_anything_engaged_blocks_entry(self):
    for kw in ({"enabled": True}, {"lat": True}, {"lon": True}):
      s = Sim()
      assert not s.requests(300, cc_kw=kw), kw
      assert s.requests(2), kw

  def test_carcontrol_not_alive_is_the_start_up(self):
    s = Sim()
    assert s.requests(2, cc_alive=False, cc_kw={"enabled": True})


class TestQuiet(OpenpilotTestCase):
  def enter(self):
    s = Sim()
    assert s.requests(50)
    return s

  def test_ten_seconds_quiet_in_p_releases(self):
    s = self.enter()
    s.step(n=998)                               # 9.98 s after the last request's step
    assert s.g.stock
    assert not s.step(n=2)
    name, rec = s.records[-1]
    assert name == "diag_tool_stock" and rec["on"] is False and 10.0 <= rec["quiet_s"] < 10.1
    assert rec["duration_s"] is not None and rec["inherited"] is False and rec["quiet_needed_s"] == 10.0

  def test_quiet_while_moving_waits_for_park(self):
    s = self.enter()
    s.step(n=3000, cs_kw=DRIVING)
    assert s.g.stock, "never resumes while moving"
    assert not s.step()
    assert s.records[-1][1]["on"] is False

  def test_a_dead_car_bus_is_not_quiet(self):
    s = self.enter()
    s.step(n=3000, rx=False)                    # 30 s with no src-0 frame at all (bus-off)
    assert s.g.stock and s.g.quiet_s == 0
    s.step(n=990)                               # the 30 s hole counts 0.1 s, then 989 x 10 ms: 9.99 s
    assert s.g.stock
    assert not s.step(n=2)

  def test_a_dead_car_bus_with_the_rest_still_talking_is_not_quiet(self):
    """Review B 5: in the car a dead bus 0 still leaves can messages - our echoes (128), the radar side (2) and its
    forwards (130), our own sendcan. Only src-0 frames count toward the quiet."""
    s = self.enter()
    others = [(0x1FA, b"", 2), (0x30C, b"", 2), (0xE4, b"", 128), (0x1A6, b"", 130), (0x18DA0EF1, b"", 130)]
    s.step(n=3000, rx=False, other=others)
    assert s.g.stock and s.g.quiet_s == 0

  def test_gaps_count_at_most_the_cap(self):
    s = self.enter()
    for _ in range(50):                         # a src-0 frame every 2 s, 100 s: 50 x 0.1 s = 5 s of quiet
      s.step(n=199, rx=False)
      s.step()
    assert s.g.stock and abs(s.g.quiet_s - 50 * QUIET_GAP_CAP_NS / 1e9) < 0.11

  def test_one_request_resets_the_clock(self):
    s = self.enter()
    s.step(n=900)
    s.requests(1)
    s.step(n=900)
    assert s.g.stock
    s.step(n=200)
    assert not s.g.stock

  def test_tester_present_every_three_seconds_holds(self):
    s = self.enter()
    for _ in range(40):
      s.requests(1)
      s.step(n=300)
    assert s.g.stock


class TestGating(OpenpilotTestCase):
  def test_other_cars_and_passive_never_arm(self):
    for cp in (make_cp(fingerprint=CAR.HONDA_CIVIC), make_cp(brand="toyota"), make_cp(passive=True)):
      assert not armed_for(cp)
      s = Sim(cp)
      assert not s.requests(300) and s.records == []
      assert not Sim(cp, inherited=True).g.stock

  def test_both_elesys_variants_identical(self):
    out = []
    for stock_acc in (False, True):
      s = Sim(make_cp(stock_acc=stock_acc))
      trace = [s.requests(3), s.step(n=1200), s.requests(5, cs_kw=DRIVING), s.step(n=1200, cs_kw=DRIVING), s.step(n=5)]
      out.append((trace, [(n, {k: v for k, v in r.items() if k != "duration_s"}) for n, r in s.records]))
    assert out[0] == out[1]

  def test_inherited_holds_until_the_leave_rule(self):
    s = Sim(inherited=True)
    assert s.g.stock
    s.step(n=QUIET_NS // STEP_NS - 5)
    assert s.g.stock
    s.step(n=10)
    assert not s.g.stock
    rec = s.records[-1][1]
    assert rec["inherited"] is True and rec["duration_s"] is None

  def test_inherited_while_moving_never_releases_while_moving(self):
    s = Sim(inherited=True)
    s.step(n=5000, cs_kw=DRIVING)
    assert s.g.stock


class TestRecords(OpenpilotTestCase):
  def test_one_record_per_transition(self):
    s = Sim()
    s.requests(500, every_ns=20 * MS)
    s.step(n=13_500)                  # released 10 s in, and back more than 2 min after that: 10 s again
    s.requests(500, every_ns=20 * MS)
    s.step(n=1100)
    names = [(n, r.get("on")) for n, r in s.records]
    assert names == [("diag_tool_stock", True), ("diag_tool_stock", False)] * 2, names
    on = s.records[0][1]
    for k in ("ids", "requests_1s", "gear", "v_ego", "standstill", "can_valid", "controls_started"):
      assert k in on, k
    off = s.records[1][1]
    assert off["ids"] == {"0x18DA0EF1": 500} and "quiet_s" in off and "duration_s" in off

  def test_ids_are_bounded(self):
    s = Sim()
    for ta in range(0x40):
      s.step(frames=[(0x18DA00F1 | (ta << 8), b"", 0)])
    on = s.records[0][1]
    s.step(n=1100)
    off = s.records[1][1]
    assert len(on["ids"]) <= 17 and len(off["ids"]) == 17 and off["ids"]["other"] == 0x40 - 16


class TestConstants(OpenpilotTestCase):
  def test_the_numbers(self):
    """What the design and the owner's words say, written out."""
    assert (REQ_MIN, REQ_WINDOW_NS, QUIET_NS, QUIET_GAP_CAP_NS) == (2, 1_000_000_000, 10_000_000_000, 100_000_000)
    assert QUIET_STEPS_NS == (10_000_000_000, 30_000_000_000, 60_000_000_000) and REENTRY_NS == 120_000_000_000


class TestBackOff(OpenpilotTestCase):
  """Review A F2: a tool that does one exchange (request + its flow control: 2 frames) every N s, parked."""
  @staticmethod
  def slow_tool(period_s: float, total_s: float = 120.0):
    s = Sim()
    trans = []
    for k in range(int(total_s * S)):
      if k % int(period_s * S) == 0:
        s.step(frames=[(KTUNER, b"", 0), (KTUNER, b"", 0)])
      else:
        s.step()
      if s.records and (not trans or len(s.records) > trans[-1][0]):
        trans.append((len(s.records), round(k / S, 2), s.records[-1][1]["on"]))
    return [(t, on) for _, t, on in trans]

  def test_an_eleven_second_poller_no_longer_flaps(self):
    """It switched the relay 21 times in two minutes, 1 s of car mode in every 11 s; now: in, out once, back for good."""
    assert self.slow_tool(11.0) == [(0.0, True), (10.0, False), (11.0, True)]

  def test_the_quiet_grows_10_30_60_and_stops_there(self):
    t = self.slow_tool(61.0, total_s=400.0)
    rel = [(on_t, on) for on_t, on in t]
    assert rel == [(0.0, True), (10.0, False), (61.0, True), (91.0, False), (122.0, True), (182.0, False), (183.0, True),
                   (243.0, False), (244.0, True), (304.0, False), (305.0, True), (365.0, False), (366.0, True)], rel

  def test_a_tool_back_after_two_minutes_starts_at_ten_seconds(self):
    s = Sim()
    assert s.requests(2)
    s.step(n=11 * S)
    assert not s.g.stock
    s.step(n=121 * S)                 # more than REENTRY after the release
    assert s.requests(2) and s.records[-1][1]["quiet_needed_s"] == 10.0
    s.step(n=10 * S)
    assert not s.g.stock

  def test_back_within_two_minutes_needs_thirty(self):
    s = Sim()
    assert s.requests(2)
    s.step(n=11 * S)
    assert not s.g.stock
    s.step(n=100 * S)                 # 110 s after the release
    assert s.requests(2) and s.records[-1][1]["quiet_needed_s"] == 30.0
    s.step(n=29 * S)
    assert s.g.stock
    s.step(n=2 * S)
    assert not s.g.stock and s.records[-1][1]["quiet_needed_s"] == 30.0

  def test_inherited_starts_at_ten_seconds(self):
    s = Sim(inherited=True)
    s.step(n=10 * S + 5)
    assert not s.g.stock
