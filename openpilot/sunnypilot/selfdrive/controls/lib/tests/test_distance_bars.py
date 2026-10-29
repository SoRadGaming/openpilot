"""FORK(CLOSE-FOLLOW): the Elesys Accord's four dash bars (push14, distance_bars.py).

Pinned here, through selfdrived's own distance_button() and sunnypilot's CruiseHelper (the long press): the four-step
cycle 4 -> 3 -> 2 -> 1 -> 4 with the CloseFollow switch on and its wrap; the switch off, upstream's three steps (and a
remembered 1-bar mode stepping on to 4 and cleared); what a press writes - only what changes, and nothing at all from the
params thread's reads - and that the mode comes back on the next drive; the first press after the update (aggressive, 2
bars) is 1 bar; the long press still toggles experimental mode and steps nothing; every other car, and this car without
openpilot longitudinal or in stock ACC mode, upstream's three steps and personality + 1; the press and the params thread
taken in turn under the lock. The push14 review: a personality picked in a setting - sunnylink's app or a backup
restore - leaves the 1-bar mode, cleared before the personality lands, and only the distance button enters it (no
backup carries it, sunnylink cannot write it); the personality in effect picked again changes nothing. And on the wire,
through the real Honda CarController: HUD_DISTANCE 0 / 3 / 2 / 1 for 4 / 3 / 2
/ 1 bars - the values the stock Elesys radar sends for the same bars on the owner's drives - with every other frame,
the SCM_BUTTONS stand-down among them, the same in all four modes; and in stock ACC mode no ACC_HUD at all, whatever the
bars. close follow live only at 1 bar (and shadow, the plan bit for bit, at 4, 3 and 2) is in test_close_follow.py
(TestFourDashBars).

push15 (TestASlowDisk): on a /data that holds every write for seconds to minutes, as on route 157 - push14's first
drive, where 48 of the dash's 100 changes had neither a press nor a setting behind them - the dash changes only on a
press, exactly one step each, and on a
setting that lands with none of the press's own writes in flight: the moments from that drive that went wrong (1 bar
back to 2 after 2 s and to 1 again, three presses back to the disk's stale value, the backlog draining as a burst), a
write landing during a read, and the owner's own 51 presses from that drive replayed with its stalls and its five comma
4 page picks. Every press writes through selfdrived's real ParamsWriter (its own thread); every other car still writes
on selfdrived's own Params handle, as upstream.

push15's review: a setting that picks a personality reads CloseFollowOneBar from disk to decide whether to clear it,
and in a stall that is the stale value while a press's write of it waits. The comma 4 page tapped through standard,
relaxed and aggressive just after 2 -> 1 bar put 1 bar on the dash at aggressive, close follow live, while the page
showed aggressive; sunnylink left (standard, True) on disk. Now selfdrived itself leaves the 1-bar mode on the read that
sees a personality no press wrote (TestASlowDisk, TestASettingLeavesTheOneBarMode), never on a press's own write
landing (TestTheButton), and the page queues its clear on the handle that writes its personality, so it lands first.
"""
import ast
import base64
import threading
import time
from pathlib import Path
from typing import Any, cast
from unittest import mock

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, HONDA_ELESYS
from opendbc.car.structs import car
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from opendbc.sunnypilot.car.interfaces import setup_interfaces
from openpilot.cereal import custom, log, messaging
from openpilot.common.params import ParamKeyFlag, Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.events import EVENTS, ET, Events, personality_changed_alert
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.sunnypilot.selfdrive.car.cruise_helpers import CruiseHelper, DISTANCE_LONG_PRESS
from openpilot.sunnypilot.selfdrive.controls.lib import distance_bars as db
from openpilot.sunnypilot.selfdrive.controls.lib.distance_bars import (AGGRESSIVE, ONE_BAR_NAME, RELAXED, STANDARD,
                                                                       DistanceBars, four_bars_apply, lead_distance_bars,
                                                                       next_mode)
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.sunnylink import utils as sunnylink_utils
from openpilot.sunnypilot.sunnylink.athena import sunnylinkd
from openpilot.sunnypilot.sunnylink.backups.manager import BackupManagerSP
from openpilot.sunnypilot.system.params_writer import ParamsWriter

ButtonEvent = car.CarState.ButtonEvent
ButtonType = car.CarState.ButtonEvent.Type
EventName = log.OnroadEvent.EventName
ELESYS = CAR.HONDA_ACCORD_9G_AU
ACC_HUD = 0x30C
# the stock Elesys radar's HUD_DISTANCE for each number of bars, from the owner's stock-ACC drives (distance_bars.py)
STOCK_HUD_DISTANCE = {4: 0, 3: 3, 2: 2, 1: 1}


def car_params(fingerprint=ELESYS, op_long=True, stock_acc=False, flag_only=False):
  """stock_acc: stock ACC mode as _initialize_honda sets it - the flag and openpilot longitudinal off (flag_only: the
  flag alone)."""
  CP = car.CarParams(brand='honda', carFingerprint=fingerprint,
                     openpilotLongitudinalControl=op_long and not (stock_acc and not flag_only))
  CP_SP = custom.CarParamsSP.new_message()
  if stock_acc:
    CP_SP.flags = HondaFlagsSP.ELESYS_STOCK_ACC.value
  return CP, CP_SP.as_reader()


class CountingParams:
  """selfdrived's own Params handle, counting writes (put / put_bool) by key; they land at once (block=True). The long
  press writes ExperimentalMode here, and so does every other car's press its personality (upstream's)."""

  def __init__(self):
    self.p = Params()
    self.writes: list[str] = []

  def get(self, key, *a, **k):
    return self.p.get(key, *a, **k)

  def get_bool(self, key, *a, **k):
    return self.p.get_bool(key, *a, **k)

  def put(self, key, v, *a, **k):
    self.writes.append(key)
    return self.p.put(key, v, block=True)

  def put_bool(self, key, v, *a, **k):
    self.writes.append(key)
    return self.p.put_bool(key, v, block=True)


_WRITERS: list[ParamsWriter] = []
_DISKS: list["SlowDisk"] = []


class CountingWriter:
  """selfdrived's ParamsWriter - a real one, on its own thread - counting what a press asks it to write, by key."""

  def __init__(self):
    self.w = ParamsWriter("test_distance_bars")
    self.writes: list[str] = []
    _WRITERS.append(self.w)

  def put(self, key, v):
    self.writes.append(key)
    self.w.put(key, v)

  def put_bool(self, key, v):
    self.writes.append(key)
    self.w.put_bool(key, v)

  def in_flight(self, key) -> bool:
    return self.w.in_flight(key)

  def flush(self) -> None:
    assert self.w.flush(5.), "a healthy /data: the press's writes land"


def _stop_writers():
  while _DISKS:
    _DISKS.pop().open()
  while _WRITERS:
    _WRITERS.pop().stop(5.)


class BarsTestCase(OpenpilotTestCase):
  def setUp(self):
    super().setUp()
    self.addCleanup(_stop_writers)


class FakeSelfdriveD(CruiseHelper):
  """The parts of selfdrived a distance-button press goes through: CruiseHelper.update() (the long press) and then
  SelfdriveD.distance_button() itself, with selfdrived's own state names - and one pass of its params thread's
  personality read (SelfdriveD.read_personality). params is selfdrived's own handle, params_writer its ParamsWriter."""
  distance_button = SelfdriveD.distance_button
  read_personality = SelfdriveD.read_personality
  disk: "SlowDisk | None" = None

  def __init__(self, CP, CP_SP, params, writer=None):
    CruiseHelper.__init__(self, CP)
    self.params = params
    self.params_writer = writer if writer is not None else CountingWriter()
    self.events = Events()
    self.events_sp = EventsSP()
    self.experimental_mode = params.get_bool("ExperimentalMode")
    self.personality = params.get("LongitudinalPersonality", return_default=True)
    self.distance_bars = DistanceBars(CP, CP_SP, params, self.params_writer)
    self.cruise_available = True

  def frame(self, pressed=None):
    self.events.clear()
    self.events_sp.clear()
    CS = car.CarState(cruiseState={"available": self.cruise_available})
    if pressed is not None:
      CS.buttonEvents = [ButtonEvent(type=ButtonType.gapAdjustCruise, pressed=pressed)]
    CruiseHelper.update(self, CS, self.events_sp, self.experimental_mode)
    self.distance_button(CS)

  def press(self, hold=10, land=True):
    """A short press (released before DISTANCE_LONG_PRESS frames). True if it stepped the mode. land: its writes land
    before it returns (a healthy /data); on a SlowDisk they wait for it."""
    self.frame(True)
    for _ in range(hold - 1):
      self.frame()
    self.frame(False)
    if land and self.disk is None:
      self.params_writer.flush()
    return EventName.personalityChanged in self.events.names

  def bars(self) -> int:
    return lead_distance_bars(self.personality, self.distance_bars.one_bar(self.personality), self.distance_bars.applies)


class SlowDisk:
  """/data as route 157 had it (10 % free; selfdrived's writes held 50-230 s): every write selfdrived makes waits until
  land_one() lets it through, while reads are never held. Both of selfdrived's write paths wait, each in its own order:
  its Params handle's non-blocking put() / put_bool() - one FIFO per handle (Params::putNonBlocking): what push14's
  press wrote through and the long press still does (HeldParams) - and its ParamsWriter's thread, what a press writes
  through now."""

  def __init__(self, writer: CountingWriter):
    self.w = writer.w
    self.queue: list = []                    # the handle's FIFO: (write, key, value)
    self._gate = threading.Semaphore(0)
    for name in ("put", "put_bool"):
      real = getattr(self.w._params, name)

      def held(key, *a, real=real, **k):
        self._gate.acquire(timeout=10.)       # (a test gone wrong fails rather than hangs)
        return real(key, *a, **k)
      setattr(self.w._params, name, held)
    _DISKS.append(self)

  def writing(self) -> bool:
    """Something of the writer's not on disk yet."""
    with self.w._cv:
      return bool(self.w._pending) or getattr(self.w, "_writing", None) is not None

  def on_the_thread(self, key: str):
    """Until the writer's thread has taken key's write off its queue (and is held writing it)."""
    deadline = time.monotonic() + 5.
    while getattr(self.w, "_writing", None) != key:
      assert time.monotonic() < deadline, f"the writer took {key}"
      time.sleep(0.001)

  def land_one(self) -> bool:
    """One held write reaches the disk: the handle's oldest, else the writer's current one. False if none was held."""
    if self.queue:
      write, key, v = self.queue.pop(0)
      write(key, v, block=True)
      return True
    if not self.writing():
      return False
    n = self.w.written
    self._gate.release()
    deadline = time.monotonic() + 5.
    while self.w.written == n:
      assert time.monotonic() < deadline, "the writer's held write landed"
      time.sleep(0.001)
    return True

  def land_all(self) -> int:
    n = 0
    while self.land_one():
      n += 1
    return n

  def open(self):
    """The test's end: let everything through."""
    self.queue.clear()
    for _ in range(100):
      self._gate.release()


class HeldParams(CountingParams):
  """selfdrived's own Params handle on a SlowDisk: put() / put_bool() return at once and wait in the handle's FIFO."""

  def __init__(self, disk: SlowDisk):
    super().__init__()
    self.disk = disk

  def put(self, key, v, *a, **k):
    self.writes.append(key)
    self.disk.queue.append((self.p.put, key, v))

  def put_bool(self, key, v, *a, **k):
    self.writes.append(key)
    self.disk.queue.append((self.p.put_bool, key, v))


def start(personality, switch=True, one_bar=None, fingerprint=ELESYS, op_long=True, stock_acc=False, slow=False):
  """A drive's start: the params as the last drive left them, then selfdrived. slow: on a SlowDisk (sd.disk)."""
  p = Params()
  p.put("LongitudinalPersonality", int(personality), block=True)
  p.put_bool("CloseFollow", switch, block=True)
  if one_bar is None:
    p.remove("CloseFollowOneBar")
  else:
    p.put_bool("CloseFollowOneBar", one_bar, block=True)
  writer = CountingWriter()
  disk = SlowDisk(writer) if slow else None
  params = HeldParams(disk) if disk is not None else CountingParams()
  CP, CP_SP = car_params(fingerprint, op_long, stock_acc)
  sd = FakeSelfdriveD(CP, CP_SP, params, writer)
  sd.disk = disk
  return sd, params


class TestTheMapping(OpenpilotTestCase):
  def test_bars_per_mode(self):
    self.assertEqual([lead_distance_bars(p, False, True) for p in (RELAXED, STANDARD, AGGRESSIVE)], [4, 3, 2])
    self.assertEqual(lead_distance_bars(AGGRESSIVE, True, True), 1)
    # the 1-bar mode needs aggressive: a stale flag with another personality shows that personality's bars
    self.assertEqual([lead_distance_bars(p, True, True) for p in (RELAXED, STANDARD)], [4, 3])
    # every other car: upstream's personality + 1, whatever the flag
    for one_bar in (False, True):
      self.assertEqual([lead_distance_bars(p, one_bar, False) for p in (AGGRESSIVE, STANDARD, RELAXED)], [1, 2, 3])

  def test_next_mode(self):
    self.assertEqual(next_mode(RELAXED, False, True), (STANDARD, False))
    self.assertEqual(next_mode(STANDARD, False, True), (AGGRESSIVE, False))
    self.assertEqual(next_mode(AGGRESSIVE, False, True), (AGGRESSIVE, True))
    self.assertEqual(next_mode(AGGRESSIVE, True, True), (RELAXED, False))
    self.assertEqual(next_mode(AGGRESSIVE, False, False), (RELAXED, False), "switch off: upstream's wrap")

  def test_where_it_applies(self):
    self.assertTrue(four_bars_apply(*car_params()))
    self.assertFalse(four_bars_apply(*car_params(op_long=False)))
    self.assertFalse(four_bars_apply(*car_params(stock_acc=True)))
    self.assertFalse(four_bars_apply(*car_params(stock_acc=True, flag_only=True)))
    for fp in CAR:
      if fp not in HONDA_ELESYS:
        self.assertFalse(four_bars_apply(*car_params(fp)), fp)
    self.assertFalse(four_bars_apply(car.CarParams(brand='toyota', carFingerprint='TOYOTA_COROLLA',
                                                   openpilotLongitudinalControl=True), custom.CarParamsSP.new_message()))

  def test_the_message_and_the_param(self):
    sp = messaging.new_message('selfdriveStateSP')
    self.assertFalse(sp.selfdriveStateSP.closeFollowOneBar, "a route from before push14 reads off")
    self.assertEqual((db.ONE_BAR_PARAM, db.PERSONALITY_PARAM), ("CloseFollowOneBar", "LongitudinalPersonality"))
    p = Params()
    p.remove(db.ONE_BAR_PARAM)
    self.assertFalse(p.get(db.ONE_BAR_PARAM, return_default=True), "default off")
    self.assertFalse(db.read_one_bar_param(p))

  def test_the_alert(self):
    self.assertTrue(personality_changed_alert in EVENTS[EventName.personalityChanged].values())
    none = cast(Any, None)   # (the alert reads none of them)
    alert = personality_changed_alert(none, none, none, False, 0, ONE_BAR_NAME)
    self.assertEqual(alert.alert_text_1, "Driving Personality: Close Follow")
    self.assertIs(EVENTS[EventName.personalityChanged][ET.WARNING], personality_changed_alert)


class TestTheButton(BarsTestCase):
  def test_four_steps_and_the_wrap(self):
    sd, params = start(RELAXED)
    seen = [sd.bars()]
    for _ in range(8):
      self.assertTrue(sd.press())
      seen.append(sd.bars())
    self.assertEqual(seen, [4, 3, 2, 1, 4, 3, 2, 1, 4])
    # 1 bar is aggressive with the 1-bar mode, as the params say
    sd, _ = start(STANDARD)
    sd.press()
    sd.press()
    self.assertEqual((sd.personality, sd.distance_bars.one_bar_set, sd.bars()), (AGGRESSIVE, True, 1))
    p = Params()
    self.assertEqual((p.get("LongitudinalPersonality"), p.get_bool("CloseFollowOneBar")), (AGGRESSIVE, True))

  def test_the_switch_off_is_upstreams_three_steps(self):
    sd, params = start(RELAXED, switch=False)
    seen = [sd.bars()]
    for _ in range(6):
      sd.press()
      seen.append(sd.bars())
    self.assertEqual(seen, [4, 3, 2, 4, 3, 2, 4])
    self.assertNotIn("CloseFollowOneBar", sd.params_writer.writes)

  def test_a_remembered_one_bar_with_the_switch_off(self):
    sd, params = start(AGGRESSIVE, switch=False, one_bar=True)
    self.assertEqual(sd.bars(), 2, "the switch off: 2 bars, close follow not live")
    self.assertFalse(sd.distance_bars.one_bar(sd.personality))
    sd.press()
    self.assertEqual((sd.bars(), sd.personality), (4, RELAXED))
    self.assertFalse(Params().get_bool("CloseFollowOneBar"), "the press cleared it")

  def test_what_a_press_writes(self):
    sd, params = start(RELAXED)
    expect = [["LongitudinalPersonality"], ["LongitudinalPersonality"], ["CloseFollowOneBar"],
              ["LongitudinalPersonality", "CloseFollowOneBar"]]
    writer = sd.params_writer
    for want in expect:
      writer.writes.clear()
      sd.press()
      self.assertEqual(writer.writes, want)
    self.assertEqual(params.writes, [], "a press writes through the writer only (push15), never on selfdrived's handle")
    # the params thread's reads (every 0.1 s) never write
    writer.writes.clear()
    for _ in range(50):
      sd.read_personality()
    self.assertEqual((params.writes, writer.writes), ([], []))
    self.assertEqual(sd.bars(), 4)

  def test_it_persists_across_drives(self):
    sd, _ = start(STANDARD)
    sd.press()
    sd.press()
    self.assertEqual(sd.bars(), 1)
    CP, CP_SP = car_params()
    nxt = FakeSelfdriveD(CP, CP_SP, CountingParams())   # the next drive: selfdrived reads the params again
    self.assertEqual((nxt.personality, nxt.bars()), (AGGRESSIVE, 1))
    nxt.press()
    nxt2 = FakeSelfdriveD(CP, CP_SP, CountingParams())
    self.assertEqual((nxt2.personality, nxt2.bars()), (RELAXED, 4))

  def test_after_the_update(self):
    # the owner's car after the update: aggressive, no CloseFollowOneBar yet, the switch at its default (on)
    p = Params()
    p.put("LongitudinalPersonality", AGGRESSIVE, block=True)
    p.remove("CloseFollow")
    p.remove("CloseFollowOneBar")
    CP, CP_SP = car_params()
    sd = FakeSelfdriveD(CP, CP_SP, CountingParams())
    self.assertTrue(sd.distance_bars.switch_on, "CloseFollow defaults on")
    self.assertEqual(sd.bars(), 2, "his personality, now 2 bars")
    sd.press()
    self.assertEqual(sd.bars(), 1, "the first press: 1 bar, close follow")

  def test_the_long_press_is_still_experimental_mode(self):
    sd, params = start(AGGRESSIVE)
    before = (sd.personality, sd.distance_bars.one_bar_set)
    sd.frame(True)
    for _ in range(DISTANCE_LONG_PRESS + 10):
      sd.frame()
    self.assertTrue(Params().get_bool("ExperimentalMode"), "the hold toggled experimental mode")
    sd.frame(False)
    self.assertNotIn(EventName.personalityChanged, sd.events.names)
    self.assertEqual((sd.personality, sd.distance_bars.one_bar_set), before, "the release after a hold steps nothing")
    self.assertTrue(sd.press(), "the next short press steps again")
    self.assertEqual(sd.bars(), 1)

  def test_every_other_car_and_mode_is_upstreams(self):
    for kw in ({'fingerprint': CAR.HONDA_CIVIC}, {'fingerprint': CAR.HONDA_CRV_5G}, {'op_long': False},
               {'stock_acc': True}):
      with self.subTest(**kw):
        sd, params = start(RELAXED, **kw)
        self.assertFalse(sd.distance_bars.applies)
        seen = [sd.personality]
        bars = [sd.bars()]
        for _ in range(4):
          sd.press()
          seen.append(sd.personality)
          bars.append(sd.bars())
        if kw.get('op_long') is False or kw.get('stock_acc'):
          # without openpilot longitudinal (stock ACC mode among it) selfdrived never steps the personality
          self.assertEqual(seen, [RELAXED] * 5)
        else:
          self.assertEqual(seen, [RELAXED, STANDARD, AGGRESSIVE, RELAXED, STANDARD])
        self.assertEqual(bars, [p + 1 for p in seen], "upstream's personality + 1")
        self.assertNotIn("CloseFollowOneBar", params.writes)
        self.assertEqual(sd.params_writer.writes, [], "upstream's write, on selfdrived's own handle")

  def test_a_press_waits_for_the_params_thread(self):
    sd, _ = start(STANDARD)
    done = threading.Event()

    def press():
      sd.press()
      done.set()
    with sd.distance_bars.lock:   # the params thread mid-read
      t = threading.Thread(target=press)
      t.start()
      time.sleep(0.05)
      self.assertFalse(done.is_set(), "the press waits for the read")
      self.assertEqual(Params().get("LongitudinalPersonality"), STANDARD)
    t.join(2.)
    self.assertTrue(done.is_set())
    self.assertEqual((sd.personality, Params().get("LongitudinalPersonality")), (AGGRESSIVE, AGGRESSIVE))

  def test_a_presss_own_write_landing_is_never_a_pick(self):
    """push15's review: a read that sees a personality no press wrote leaves the 1-bar mode. A press's own write is
    not that: 3 -> 2 -> 1 bar with both writes landed before the params thread's next read is 1 bar on that read."""
    sd, _ = start(STANDARD)
    self.assertTrue(sd.press())
    self.assertTrue(sd.press())
    sd.read_personality()
    self.assertEqual((sd.bars(), on_disk()), (1, (AGGRESSIVE, True)))
    self.assertEqual(sd.params_writer.writes, ["LongitudinalPersonality", "CloseFollowOneBar"])


READ_DT = 0.107   # selfdrived's params thread on route 157: 21 reads in 2.24 s (t = 109.46 -> 111.70)
ORDER = {4: 3, 3: 2, 2: 1, 1: 4}   # one press, the dash's bars before -> after


class Drive:
  """Route time: the params thread's read every READ_DT, and presses between them; every change of the dash's bars is
  recorded with what made it (a press, or a read)."""

  def __init__(self, sd: FakeSelfdriveD, t: float = 0.):
    self.sd, self.t, self.next_read = sd, t, t + READ_DT
    self.dash: list[tuple[float, int, str]] = [(t, sd.bars(), 'start')]

  def _note(self, why: str):
    if self.sd.bars() != self.dash[-1][1]:
      self.dash.append((round(self.t, 2), self.sd.bars(), why))

  def run_to(self, t: float):
    while self.next_read <= t:
      self.t = self.next_read
      self.sd.read_personality()
      self._note('read')
      self.next_read += READ_DT
    self.t = max(self.t, t)

  def press(self, hold: int = 10) -> bool:
    stepped = self.sd.press(hold, land=False)
    self._note('press')
    return stepped

  def bars(self, why: str) -> list[int]:
    return [b for _, b, w in self.dash if w == why]

  def unprompted(self) -> list[tuple[float, int, str]]:
    """The dash's changes no press made."""
    return [d for d in self.dash if d[2] == 'read']


def on_disk() -> tuple:
  p = Params()
  return p.get("LongitudinalPersonality"), p.get_bool("CloseFollowOneBar")


def page_pick(personality):
  """A tap on the comma 4 settings page's personality, as it lands: its select callback (toggles.py _on_personality)
  clears CloseFollowOneBar if the DISK has it set, and then the toggle writes the personality, on one handle, in that
  order (TestTheComma4Page)."""
  p = Params()
  if p.get_bool("CloseFollowOneBar"):
    p.put_bool("CloseFollowOneBar", False, block=True)
  p.put("LongitudinalPersonality", int(personality), block=True)


# Route 157 (push14's first drive, 2026-10-27, the servo to home): the owner's 51 distance-button presses as carState
# logged them, (down, up) in s from the route's start - 47 short, and 4 holds (experimental mode) of 0.96-1.58 s
R157_PRESSES = (
  (63.49, 63.63), (65.31, 65.45), (66.26, 66.34), (66.77, 66.89), (109.34, 109.46), (127.54, 127.64), (128.03, 128.18),
  (128.88, 129.04), (134.60, 134.74), (136.52, 136.64), (139.66, 139.80), (145.01, 145.14), (148.67, 148.79),
  (148.92, 149.00), (149.12, 149.18), (154.22, 154.38), (155.32, 155.48), (156.41, 156.53), (159.94, 160.09),
  (161.06, 161.20), (164.56, 164.73), (165.62, 165.76), (166.12, 166.26), (166.96, 167.13), (169.94, 170.10),
  (178.66, 179.72), (181.82, 182.78), (184.34, 184.48), (185.06, 185.18), (227.07, 227.25), (227.81, 227.96),
  (262.65, 262.77), (263.84, 264.02), (270.98, 271.13), (274.55, 274.67), (274.73, 274.85), (376.37, 376.55),
  (397.90, 398.08), (401.36, 402.40), (403.94, 405.52), (412.58, 412.73), (521.27, 521.40), (523.69, 523.87),
  (526.51, 526.61), (530.95, 531.15), (532.99, 533.18), (537.93, 538.07), (539.09, 539.27), (545.80, 545.92),
  (548.17, 548.31), (680.66, 680.72))
# when /data held selfdrived's writes on that drive, as its own reads show (the first press's write took 50 s; the
# backlog drained at 366.95-369.36 and up to 420.6, and again at 528-572): nothing lands inside, everything held drains
# at the end, one write per read; outside, a write lands before the next read
R157_STALLS = ((63.0, 420.6), (521.0, 572.2))
# the comma 4 settings page's personality picks, as they landed (the touch log has the owner tapping it at 420.8-442.8)
R157_PICKS = ((427.14, STANDARD), (427.77, RELAXED), (429.27, AGGRESSIVE), (430.55, STANDARD), (439.55, AGGRESSIVE))
R157_START, R157_END = 4.08, 798.43   # (aggressive, 2 bars, the switch on; experimental mode on)


class TestASlowDisk(BarsTestCase):
  """push15: route 157's /data held selfdrived's writes for up to minutes, and push14's guard (a press's value for 20
  reads, or until a read showed that value) let the stale disk back onto the dash - 48 changes with neither a press nor
  a setting. Now a read keeps the press's value exactly while its write is in flight in selfdrived's ParamsWriter."""

  def test_1_bar_holds_while_its_write_waits(self):
    """t = 109.46-114.07: 2 -> 1 bar, its CloseFollowOneBar write 4.6 s on the way. push14: 2 bars after 21 reads
    (111.70) and 1 bar again when the write landed (114.07), no press either time."""
    sd, _ = start(AGGRESSIVE, slow=True)
    d = Drive(sd)
    self.assertTrue(d.press())
    d.run_to(4.6)
    self.assertEqual(on_disk(), (AGGRESSIVE, False), "still held")
    self.assertEqual(sd.disk.land_all(), 1)
    d.run_to(8.0)
    self.assertEqual(d.dash[1:], [(0., 1, 'press')])
    self.assertEqual(on_disk(), (AGGRESSIVE, True))

  def test_three_presses_back_to_the_stale_value(self):
    """t = 127.64-129.60: 1 -> 4 -> 3 -> 2 bars in 1.4 s, every write held. The third press asked for aggressive, which
    the disk still showed from before the first: push14 took that read as its write landed, dropped the guard, and
    showed 4 bars when the first held write (relaxed) landed - 0.56 s after the driver chose 2."""
    sd, _ = start(AGGRESSIVE, one_bar=True, slow=True)
    d = Drive(sd)
    self.assertEqual(sd.bars(), 1)
    for t in (0.0, 0.55, 1.40):
      d.run_to(t)
      d.press()
    d.run_to(1.96)
    self.assertTrue(sd.disk.land_one(), "relaxed, the first press's write")
    d.run_to(3.5)
    while sd.disk.land_one():
      d.run_to(d.t + READ_DT)
    d.run_to(d.t + 1.)
    self.assertEqual(d.bars('press'), [4, 3, 2])
    self.assertEqual(d.unprompted(), [])
    self.assertEqual(on_disk(), (AGGRESSIVE, False))

  def test_the_backlog_drains_without_a_flicker(self):
    """t = 366.95-369.36: the writes held since 134-275 s landed in 2.4 s and push14 put each one on the dash in turn,
    3 2 1 3 1 3 1 3 1 4 3 2 1, no press. Here 8 presses in a stall, then the drain read by read: nothing moves, and
    the disk gets one write per key and the one that was on its way - not one per press."""
    sd, _ = start(RELAXED, slow=True)
    d = Drive(sd)
    for i in range(8):
      d.run_to(i * 1.0)
      d.press()
    self.assertEqual(d.bars('press'), [3, 2, 1, 4, 3, 2, 1, 4])
    d.run_to(12.0)
    landed = 0
    while sd.disk.land_one():
      landed += 1
      d.run_to(d.t + READ_DT)
    d.run_to(d.t + 1.)
    self.assertEqual(d.unprompted(), [])
    self.assertLessEqual(landed, 3)
    self.assertEqual((sd.bars(), on_disk()), (4, (RELAXED, False)))

  def test_a_write_that_lands_during_a_read(self):
    """The read asks the writer before it reads the disk: a write landing between the two is read back. Asked after,
    the stale value read just before the landing would reach the dash for a read (0.1 s)."""
    sd, params = start(AGGRESSIVE, slow=True)
    sd.press(land=False)                 # 2 -> 1 bar: CloseFollowOneBar True on its way
    disk = cast(SlowDisk, sd.disk)
    get = params.get

    def get_then_land(key, *a, **k):
      v = get(key, *a, **k)
      if key == "CloseFollowOneBar" and disk.writing():
        disk.land_all()                  # the write lands right after this read of the stale value
      return v
    with mock.patch.object(params, "get", side_effect=get_then_land):
      for _ in range(3):
        sd.read_personality()
        self.assertEqual(sd.bars(), 1)
      disk.land_all()
      sd.read_personality()
    self.assertEqual((sd.bars(), on_disk()), (1, (AGGRESSIVE, True)))

  def test_a_setting_applies_once_none_of_the_presss_writes_is_in_flight(self):
    sd, _ = start(STANDARD, slow=True)
    d = Drive(sd)
    d.press()                                                      # 3 -> 2 bars: aggressive on its way
    Params().put("LongitudinalPersonality", RELAXED, block=True)   # a comma 4 page pick landing before it...
    d.run_to(1.0)
    self.assertEqual(sd.bars(), 2, "...while the press's own write is in flight")
    sd.disk.land_all()                                             # ...then the press's: the last to land wins
    d.run_to(2.0)
    self.assertEqual((sd.bars(), on_disk()), (2, (AGGRESSIVE, False)))
    Params().put("LongitudinalPersonality", RELAXED, block=True)   # with nothing of the press's in flight: at once
    d.run_to(2.0 + READ_DT)
    self.assertEqual([b for _, b, _ in d.unprompted()], [4])
    # sunnylink at 1 bar (personality_picked, then the personality): 3 bars on the next read, the 1-bar mode left
    d.run_to(3.0)
    for _ in range(3):
      d.press()
    self.assertEqual(sd.bars(), 1)
    sd.disk.land_all()
    d.run_to(4.0)
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(STANDARD)})
    d.run_to(4.0 + READ_DT)
    self.assertEqual((sd.bars(), on_disk()), (3, (STANDARD, False)))
    self.assertEqual([b for _, b, _ in d.unprompted()], [4, 3])

  def test_page_picks_while_the_1_bar_write_waits(self):
    """push15's review: 2 -> 1 bar writes only CloseFollowOneBar, and it waits. The comma 4 page then reads the disk's
    stale False, clears nothing, and is tapped standard, relaxed, aggressive. push15 kept the press's True through the
    picks - 3, 4 and then 1 bar, close follow live while the page showed aggressive - and (aggressive, True) was on disk
    once it landed. The read that sees a personality no press wrote leaves the 1-bar mode: 3, 4, 2 bars, and the
    writer's newer False lands after the True that was already being written."""
    sd, _ = start(AGGRESSIVE, one_bar=False, slow=True)
    disk = cast(SlowDisk, sd.disk)
    d = Drive(sd)
    self.assertTrue(d.press())
    disk.on_the_thread("CloseFollowOneBar")
    self.assertEqual(sd.bars(), 1)
    for i, p in enumerate((STANDARD, RELAXED, AGGRESSIVE)):
      d.run_to(1.0 + i)
      page_pick(p)
      d.run_to(1.0 + i + READ_DT)
    self.assertEqual([b for _, b, _ in d.unprompted()], [3, 4, 2])
    self.assertFalse(sd.distance_bars.one_bar(sd.personality), "close follow not live")
    self.assertEqual(sd.params_writer.writes, ["CloseFollowOneBar"] * 2, "the press's True, then selfdrived's False")
    self.assertEqual(on_disk(), (AGGRESSIVE, False), "both still held")
    self.assertEqual(disk.land_all(), 2)
    d.run_to(6.0)
    self.assertEqual([b for _, b, _ in d.unprompted()], [3, 4, 2], "nothing as they land")
    self.assertEqual(on_disk(), (AGGRESSIVE, False))
    self.assertTrue(d.press(), "the button still enters it")
    self.assertEqual(sd.bars(), 1)

  def test_a_sunnylink_pick_while_the_1_bar_write_waits(self):
    """The same through sunnylink's app: personality_picked() reads the stale False and clears nothing. push15 left
    (standard, True) on disk, so an aggressive picked there later was 1 bar, close follow live. Here the True waits
    behind another of selfdrived's writes (DriverTooDistracted), so selfdrived's newer False takes its place in the
    writer's queue and the True never reaches the disk."""
    sd, _ = start(AGGRESSIVE, one_bar=False, slow=True)
    disk = cast(SlowDisk, sd.disk)
    d = Drive(sd)
    sd.params_writer.w.put_bool("DriverTooDistracted", True)
    disk.on_the_thread("DriverTooDistracted")
    self.assertTrue(d.press())
    self.assertEqual(sd.bars(), 1)
    d.run_to(1.0)
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(STANDARD)})
    d.run_to(1.0 + READ_DT)
    self.assertEqual(sd.bars(), 3)
    landed = []
    while disk.land_one():
      landed.append(on_disk())
    self.assertEqual(landed, [(STANDARD, False)] * 2, "DriverTooDistracted, then the False; never the True")
    d.run_to(3.0)
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(AGGRESSIVE)})
    d.run_to(3.0 + READ_DT)
    self.assertEqual((sd.bars(), sd.distance_bars.one_bar(sd.personality)), (2, False))
    self.assertEqual([b for _, b, _ in d.unprompted()], [3, 2])
    self.assertEqual(on_disk(), (AGGRESSIVE, False))

  def test_every_other_car_keeps_upstreams_write(self):
    """Every other car: upstream's press and its non-blocking write on selfdrived's own handle - nothing through the
    writer - and upstream's read, which puts the disk's value back until that write lands."""
    sd, params = start(RELAXED, fingerprint=CAR.HONDA_CIVIC, slow=True)
    disk = cast(SlowDisk, sd.disk)
    sd.press(land=False)
    self.assertEqual(sd.personality, STANDARD)
    self.assertEqual((params.writes, sd.params_writer.writes), (["LongitudinalPersonality"], []))
    sd.read_personality()
    self.assertEqual(sd.personality, RELAXED)
    disk.land_all()
    sd.read_personality()
    self.assertEqual(sd.personality, STANDARD)

  def test_route_157(self):
    """The owner's 51 presses from the drive home, its two stalls and his five comma 4 page picks: every short press
    is exactly one step, 4 -> 3 -> 2 -> 1 -> 4; the holds toggle experimental mode and step nothing; and the dash
    changes otherwise only on the page's picks, on the read after each landed. On push14 the same drive gives 2 bars
    back after the first 1-bar press, 4 bars at 129.6 s and a burst of old steps at the end of each stall."""
    sd, params = start(AGGRESSIVE, one_bar=False, slow=True)
    disk = cast(SlowDisk, sd.disk)
    d = Drive(sd, R157_START)
    events = sorted([(up, 'press', up - down) for down, up in R157_PRESSES] + [(t, 'pick', p) for t, p in R157_PICKS] +
                    [(end, 'drain', 0) for _, end in R157_STALLS])
    steps, holds = [], []
    for t, what, x in events:
      d.run_to(t)
      if what == 'drain':
        while disk.land_one():
          d.run_to(d.t + READ_DT)
      elif what == 'pick':
        page_pick(x)
      else:
        before = sd.bars()
        stepped = d.press(hold=max(1, round(x / 0.01)))
        (holds if x >= 0.5 else steps).append((t, before, sd.bars(), stepped))
        if not any(a <= t < b for a, b in R157_STALLS):
          disk.land_all()
    d.run_to(R157_END)
    self.assertEqual(len(steps), 47)
    self.assertEqual([s for s in steps if not s[3] or ORDER[s[1]] != s[2]], [], "one step per short press")
    self.assertEqual([h for h in holds if h[3] or h[1] != h[2]], [], "a hold steps nothing")
    unprompted = d.unprompted()
    self.assertEqual([b for _, b, _ in unprompted], [{RELAXED: 4, STANDARD: 3, AGGRESSIVE: 2}[p] for _, p in R157_PICKS],
                     unprompted)
    for (t, _, _), (tp, _) in zip(unprompted, R157_PICKS, strict=True):
      self.assertTrue(0 <= t - tp < READ_DT + 0.01, f"{t}: the read after the pick at {tp} landed")
    self.assertEqual(disk.land_all(), 0)
    self.assertEqual(on_disk(), (sd.personality, sd.distance_bars.one_bar_set))


def _b64(v) -> str:
  """A value as sunnylink's app sends it to saveParams and a backup stores it: base64 of its text."""
  return base64.b64encode(str(v).encode()).decode()


class RecordingParams(CountingParams):
  """CountingParams as sunnylink's save path uses its own Params() (get_type too): every write, in order."""

  def get_type(self, key):
    return self.p.get_type(key)


class TestASettingLeavesTheOneBarMode(BarsTestCase):
  """The push14 review: a personality picked in a setting - sunnylink's app (saveParams) or a backup restore, both
  through sunnylink's save_param_from_base64_encoded_string(), as on the comma 4's page (toggles.py) - leaves the 1-bar
  mode, and only the distance button enters it."""

  def test_a_sunnylink_pick_leaves_the_1_bar_mode(self):
    sd, _ = start(AGGRESSIVE, one_bar=True)
    self.assertEqual(sd.bars(), 1)
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(STANDARD)})
    sd.read_personality()
    self.assertEqual(sd.bars(), 3)
    self.assertFalse(Params().get_bool("CloseFollowOneBar"), "left on disk too, for the next drive")
    # the review's case: aggressive picked there afterwards is 2 bars, close follow not live
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(AGGRESSIVE)})
    sd.read_personality()
    self.assertEqual((sd.personality, sd.bars()), (AGGRESSIVE, 2))
    self.assertFalse(sd.distance_bars.one_bar(sd.personality))
    self.assertTrue(sd.press(), "the button still enters it")
    self.assertEqual(sd.bars(), 1)

  def test_the_personality_in_effect_picked_again_changes_nothing(self):
    sd, _ = start(AGGRESSIVE, one_bar=True)
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(AGGRESSIVE)})
    sd.read_personality()
    self.assertEqual(sd.bars(), 1, "an app sending a page's unchanged values changes nothing")
    self.assertTrue(Params().get_bool("CloseFollowOneBar"))

  def test_the_mode_is_cleared_before_the_personality_lands(self):
    start(AGGRESSIVE, one_bar=True)
    rec = RecordingParams()
    with mock.patch.object(sunnylink_utils, 'Params', return_value=rec):
      sunnylink_utils.save_param_from_base64_encoded_string("LongitudinalPersonality", _b64(STANDARD))
    self.assertEqual(rec.writes, ["CloseFollowOneBar", "LongitudinalPersonality"],
                     "no read of selfdrived's ever sees the new personality with the old mode")
    self.assertEqual((Params().get("LongitudinalPersonality"), Params().get_bool("CloseFollowOneBar")), (STANDARD, False))

  def test_a_backup_restore_never_enters_the_1_bar_mode(self):
    keys = {flag: Params().all_keys(flag) for flag in (ParamKeyFlag.BACKUP, ParamKeyFlag.PERSISTENT)}
    self.assertNotIn(b"CloseFollowOneBar", keys[ParamKeyFlag.BACKUP], "never in a backup")
    assert b"CloseFollowOneBar" in keys[ParamKeyFlag.PERSISTENT], "kept across drives"
    assert b"LongitudinalPersonality" in keys[ParamKeyFlag.BACKUP]
    m = BackupManagerSP.__new__(BackupManagerSP)   # its restore step alone: no sunnylink, no PubMaster
    m.params = Params()
    # a backup that carries the 1-bar mode (one from cfbars before this fix) restored over 3 bars: aggressive, 2 bars
    sd, _ = start(STANDARD, one_bar=False)
    m._apply_config({"CloseFollowOneBar": _b64(1), "LongitudinalPersonality": _b64(AGGRESSIVE)})
    sd.read_personality()
    self.assertEqual((sd.personality, sd.bars()), (AGGRESSIVE, 2))
    # restored over 1 bar with another personality: that personality, and the 1-bar mode left
    sd, _ = start(AGGRESSIVE, one_bar=True)
    m._apply_config({"LongitudinalPersonality": _b64(RELAXED)})
    sd.read_personality()
    self.assertEqual(sd.bars(), 4)
    self.assertFalse(Params().get_bool("CloseFollowOneBar"))

  def test_sunnylink_cannot_set_the_1_bar_mode(self):
    assert "CloseFollowOneBar" in sunnylinkd.BLOCKED_PARAMS, "only the distance button enters it"
    sd, _ = start(AGGRESSIVE, one_bar=False)
    sunnylinkd.saveParams({"CloseFollowOneBar": _b64(1)})
    sd.read_personality()
    self.assertEqual(sd.bars(), 2)
    self.assertFalse(Params().get_bool("CloseFollowOneBar"))

  def test_every_other_car_writes_nothing(self):
    sd, _ = start(RELAXED, fingerprint=CAR.HONDA_CIVIC)
    sunnylinkd.saveParams({"LongitudinalPersonality": _b64(STANDARD)})
    self.assertIsNone(Params().get("CloseFollowOneBar"), "never set there, so never written")
    sd.read_personality()
    self.assertEqual(sd.bars(), STANDARD + 1)

  def test_a_pick_that_clears_nothing_still_leaves_the_1_bar_mode(self):
    """push15's review: whatever writes a personality without clearing CloseFollowOneBar - a setting that read the
    flag stale, or the comma 3X's settings page, which has no clear - selfdrived leaves the 1-bar mode itself, on the
    read that sees it, so an aggressive picked afterwards is 2 bars, never close follow live. push15 left the flag."""
    sd, _ = start(AGGRESSIVE, one_bar=True)
    self.assertEqual(sd.bars(), 1)
    Params().put("LongitudinalPersonality", STANDARD, block=True)
    sd.read_personality()
    self.assertEqual(sd.bars(), 3)
    sd.params_writer.flush()
    self.assertEqual((on_disk(), sd.params_writer.writes), ((STANDARD, False), ["CloseFollowOneBar"]))
    Params().put("LongitudinalPersonality", AGGRESSIVE, block=True)
    sd.read_personality()
    self.assertEqual((sd.bars(), sd.distance_bars.one_bar(sd.personality)), (2, False))
    self.assertEqual(sd.params_writer.writes, ["CloseFollowOneBar"], "once, and nothing for a pick at 2 bars")


MICI = Path(db.__file__).parents[4] / "selfdrive/ui/mici"


def _method(path: Path, cls: str, name: str) -> ast.FunctionDef:
  for node in ast.walk(ast.parse(path.read_text())):
    if isinstance(node, ast.ClassDef) and node.name == cls:
      for f in node.body:
        if isinstance(f, ast.FunctionDef) and f.name == name:
          return f
  raise AssertionError(f"{cls}.{name} not in {path}")


def _calls(node: ast.AST) -> list[str]:
  return [ast.unparse(c) for c in ast.walk(node) if isinstance(c, ast.Call)]


class TestTheComma4Page(OpenpilotTestCase):
  """The comma 4's settings page, parsed and never imported (it pulls in raylib; test_mads_fast_wheel_settings.py does
  the same)."""

  def test_the_pages_clear_lands_before_its_personality(self):
    """A tap runs the toggle's select callback (toggles.py _on_personality: CloseFollowOneBar cleared when set) and then
    BigMultiParamToggle's own write of the personality, both non-blocking so the UI never waits on /data. Params queues
    per handle (Params::putNonBlocking: one FIFO and one thread for each), so the clear goes through the toggle's own
    handle, queued ahead of the personality. push14 and push15 put it on ui_state.params, another handle and another
    queue: on a stalled /data the personality could land first."""
    toggles, button = MICI / "layouts/settings/toggles.py", MICI / "widgets/button.py"
    init = _calls(_method(toggles, "TogglesLayoutMici", "__init__"))
    self.assertTrue(any(c.startswith("BigMultiParamToggle('driving personality', 'LongitudinalPersonality'") and
                        c.endswith("select_callback=self._on_personality)") for c in init), init)
    writes = [c for c in _calls(_method(toggles, "TogglesLayoutMici", "_on_personality")) if ".put" in c]
    self.assertEqual(writes, ["self._personality_toggle._params.put_bool('CloseFollowOneBar', False)"])
    select = _calls(_method(button, "BigMultiToggle", "_handle_mouse_release"))
    self.assertTrue("self._select_callback(self.value)" in select, select)
    release = _method(button, "BigMultiParamToggle", "_handle_mouse_release")
    self.assertEqual([ast.unparse(s) for s in release.body],
                     ["super()._handle_mouse_release(mouse_pos)", "new_idx = self._options.index(self.value)",
                      "self._params.put(self._param, new_idx)"], "the callback first, then the personality, one handle")


def _interface_params(fingerprint=ELESYS, stock_acc=False):
  fp = gen_empty_fingerprint()
  fp[0][0x188] = 8            # GEARBOX_AUTO, as on the car
  fp[0][0x201] = 6            # GAS_SENSOR: the comma pedal
  CP = CarInterface.get_params(fingerprint, fp, [], False, False, False)
  CP_SP = CarInterface.get_params_sp(CP, fingerprint, fp, [], False, False, False)
  setup_interfaces(CarInterface, CP, CP_SP, [{"HondaElesysStockAcc": "1" if stock_acc else "0"}])
  return CP, CP_SP


class _FakeCS:
  def __init__(self):
    self.out = structs.CarState.new_message()
    self.out.vEgo = 5.0
    self.out.cruiseState.enabled = True
    self.out.cruiseState.available = True
    self.out.cruiseState.speed = 25.0
    self.v_cruise_factor = 1.0
    self.stock_brake = {"CHIME": 0, "AEB_REQ_1": 0, "AEB_REQ_2": 0, "AEB_STATUS": 0}
    self.acc_hud = {"FCM_OFF": 0, "FCM_OFF_2": 0, "FCM_PROBLEM": 0, "ICONS": 0}
    self.lkas_hud = {}
    self.scm_buttons = {"CRUISE_BUTTONS": 0, "CRUISE_SETTING": 0, "MAIN_ON": 1}
    self.powertrain_data = {}  # FORK(BRAKE-SWITCH-MIRROR): the car's 0x17C, for the mirror to the radar
    self.is_metric = True
    self.econ_on = False
    self.out_sp = structs.CarStateSP()


def _sends(CP, CP_SP, bars, frames=100):
  CI = CarInterface(CP, CP_SP)
  cs = _FakeCS()
  out = []
  for i in range(frames):
    cc = structs.CarControl.new_message()
    cc.enabled = cc.latActive = cc.longActive = True
    cc.actuators.accel = 0.3
    cc.hudControl.speedVisible = cc.hudControl.leadVisible = True
    cc.hudControl.setSpeed = 25.0
    cc.hudControl.leadDistanceBars = bars
    _, sends = CI.CC.update(cc.as_reader(), structs.CarControlSP(), cs, i * int(1e7))  # ty: ignore[invalid-argument-type]
    out.append([(m[0], bytes(m[1]), m[2]) if isinstance(m, tuple) else (m.address, bytes(m.dat), m.src) for m in sends])
  return out


class TestOnTheWire(OpenpilotTestCase):
  def test_hud_distance_per_mode(self):
    CP, CP_SP = _interface_params()
    self.assertTrue(four_bars_apply(CP, CP_SP))
    modes = {'relaxed': (RELAXED, False), 'standard': (STANDARD, False), 'aggressive': (AGGRESSIVE, False),
             'close follow': (AGGRESSIVE, True)}
    runs = {}
    for name, (p, one_bar) in modes.items():
      bars = lead_distance_bars(p, one_bar, four_bars_apply(CP, CP_SP))
      runs[name] = (bars, _sends(CP, CP_SP, bars))
    self.assertEqual({n: b for n, (b, _) in runs.items()}, {'relaxed': 4, 'standard': 3, 'aggressive': 2, 'close follow': 1})
    ref = runs['aggressive'][1]
    for name, (bars, frames) in runs.items():
      with self.subTest(mode=name):
        huds = [dat for sends in frames for a, dat, bus in sends if a == ACC_HUD and bus == 0]
        self.assertEqual(len(huds), 10, "10 Hz")
        self.assertEqual({(d[5] >> 6) & 3 for d in huds}, {STOCK_HUD_DISTANCE[bars]})
        # every other frame - the SCM_BUTTONS stand-down (0x1A6 on bus 2, MAIN_ON 0) among them - as in every mode
        self.assertEqual([[m for m in s if m[0] != ACC_HUD] for s in frames], [[m for m in s if m[0] != ACC_HUD] for s in ref])
        stand_down = [m for s in frames for m in s if m[0] == 0x1A6]
        self.assertEqual(len(stand_down), 25)
        self.assertTrue(all(m[2] == 2 for m in stand_down))
        # ...and in ACC_HUD itself only HUD_DISTANCE (byte 5, bits 7:6) and the checksum (byte 7, low nibble)
        for a, b in zip((d for s in frames for m in s if m[0] == ACC_HUD for d in [m[1]]),
                        (d for s in ref for m in s if m[0] == ACC_HUD for d in [m[1]]), strict=True):
          diff = bytes(x ^ y for x, y in zip(a, b, strict=True))
          self.assertEqual(diff[:5] + diff[6:7], bytes(6))
          self.assertEqual(diff[5] & 0x3F, 0)
          self.assertEqual(diff[7] & 0xF0, 0)

  def test_stock_acc_mode_sends_no_acc_hud_whatever_the_bars(self):
    CP, CP_SP = _interface_params(stock_acc=True)
    self.assertFalse(CP.openpilotLongitudinalControl)
    self.assertFalse(four_bars_apply(CP, CP_SP))
    runs = [_sends(CP, CP_SP, bars) for bars in (1, 2, 3, 4)]
    for frames in runs:
      self.assertFalse(any(a in (ACC_HUD, 0x1A6) for s in frames for a, _, _ in s), "the radar's own ACC_HUD and buttons")
      self.assertEqual(frames, runs[0])

  def test_other_hondas_send_personality_plus_one(self):
    CP, CP_SP = _interface_params(CAR.HONDA_CIVIC)
    self.assertTrue(CP.openpilotLongitudinalControl, "a Nidec Civic: openpilot longitudinal, openpilot's ACC_HUD")
    self.assertFalse(four_bars_apply(CP, CP_SP))
    for p in (AGGRESSIVE, STANDARD, RELAXED):
      bars = lead_distance_bars(p, True, four_bars_apply(CP, CP_SP))
      self.assertEqual(bars, p + 1)
      huds = [dat for s in _sends(CP, CP_SP, bars, 20) for a, dat, _ in s if a == ACC_HUD]
      self.assertEqual(len(huds), 2)
      self.assertEqual({(d[5] >> 6) & 3 for d in huds}, {p + 1})
