"""FORK(CLOSE-FOLLOW): the Elesys Accord's four dash bars (push14, distance_bars.py).

Pinned here, through selfdrived's own distance_button() and sunnypilot's CruiseHelper (the long press): the four-step
cycle 4 -> 3 -> 2 -> 1 -> 4 with the CloseFollow switch on and its wrap; the switch off, upstream's three steps (and a
remembered 1-bar mode stepping on to 4 and cleared); what a press writes - only what changes, and nothing at all from the
params thread's reads - and that the mode comes back on the next drive; the first press after the update (aggressive, 2
bars) is 1 bar; the long press still toggles experimental mode and steps nothing; every other car, and this car without
openpilot longitudinal or in stock ACC mode, upstream's three steps and personality + 1; the press and the params thread
taken in turn under the lock. And on the wire, through the real Honda CarController: HUD_DISTANCE 0 / 3 / 2 / 1 for 4 / 3 / 2
/ 1 bars - the values the stock Elesys radar sends for the same bars on the owner's drives - with every other frame,
the SCM_BUTTONS stand-down among them, the same in all four modes; and in stock ACC mode no ACC_HUD at all, whatever the
bars. close follow live only at 1 bar (and shadow, the plan bit for bit, at 4, 3 and 2) is in test_close_follow.py
(TestFourDashBars).
"""
import threading
import time
from typing import Any, cast

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR, HONDA_ELESYS
from opendbc.car.structs import car
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from opendbc.sunnypilot.car.interfaces import setup_interfaces
from openpilot.cereal import custom, log, messaging
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.events import EVENTS, ET, Events, personality_changed_alert
from openpilot.selfdrive.selfdrived.selfdrived import SelfdriveD
from openpilot.sunnypilot.selfdrive.car.cruise_helpers import CruiseHelper, DISTANCE_LONG_PRESS
from openpilot.sunnypilot.selfdrive.controls.lib import distance_bars as db
from openpilot.sunnypilot.selfdrive.controls.lib.distance_bars import (AGGRESSIVE, ONE_BAR_NAME, RELAXED, STANDARD,
                                                                       DistanceBars, four_bars_apply, lead_distance_bars,
                                                                       next_mode)
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

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
  """Params, counting writes (put / put_bool) by key. Its writes land at once (block=True) unless lag: then they wait
  in `queued` for land() - Params.put() is non-blocking on the device, and the params thread can read before."""

  def __init__(self, lag=False):
    self.p = Params()
    self.writes: list[str] = []
    self.lag = lag
    self.queued: list = []

  def get(self, key, *a, **k):
    return self.p.get(key, *a, **k)

  def get_bool(self, key, *a, **k):
    return self.p.get_bool(key, *a, **k)

  def put(self, key, v, *a, **k):
    self.writes.append(key)
    if self.lag:
      self.queued.append((self.p.put, key, v))
      return None
    return self.p.put(key, v, block=True)

  def put_bool(self, key, v, *a, **k):
    self.writes.append(key)
    if self.lag:
      self.queued.append((self.p.put_bool, key, v))
      return None
    return self.p.put_bool(key, v, block=True)

  def land(self):
    for fn, key, v in self.queued:
      fn(key, v, block=True)
    self.queued.clear()


class FakeSelfdriveD(CruiseHelper):
  """The parts of selfdrived a distance-button press goes through: CruiseHelper.update() (the long press) and then
  SelfdriveD.distance_button() itself, with selfdrived's own state names - and one pass of its params thread's
  personality read (SelfdriveD.read_personality)."""
  distance_button = SelfdriveD.distance_button
  read_personality = SelfdriveD.read_personality

  def __init__(self, CP, CP_SP, params):
    CruiseHelper.__init__(self, CP)
    self.params = params
    self.events = Events()
    self.events_sp = EventsSP()
    self.experimental_mode = params.get_bool("ExperimentalMode")
    self.personality = params.get("LongitudinalPersonality", return_default=True)
    self.distance_bars = DistanceBars(CP, CP_SP, params)
    self.cruise_available = True

  def frame(self, pressed=None):
    self.events.clear()
    self.events_sp.clear()
    CS = car.CarState(cruiseState={"available": self.cruise_available})
    if pressed is not None:
      CS.buttonEvents = [ButtonEvent(type=ButtonType.gapAdjustCruise, pressed=pressed)]
    CruiseHelper.update(self, CS, self.events_sp, self.experimental_mode)
    self.distance_button(CS)

  def press(self, hold=10):
    """A short press (released before DISTANCE_LONG_PRESS frames). True if it stepped the mode."""
    self.frame(True)
    for _ in range(hold - 1):
      self.frame()
    self.frame(False)
    return EventName.personalityChanged in self.events.names

  def bars(self) -> int:
    return lead_distance_bars(self.personality, self.distance_bars.one_bar(self.personality), self.distance_bars.applies)


def start(personality, switch=True, one_bar=None, fingerprint=ELESYS, op_long=True, stock_acc=False, lag=False):
  """A drive's start: the params as the last drive left them, then selfdrived."""
  p = Params()
  p.put("LongitudinalPersonality", int(personality), block=True)
  p.put_bool("CloseFollow", switch, block=True)
  if one_bar is None:
    p.remove("CloseFollowOneBar")
  else:
    p.put_bool("CloseFollowOneBar", one_bar, block=True)
  params = CountingParams(lag)
  CP, CP_SP = car_params(fingerprint, op_long, stock_acc)
  return FakeSelfdriveD(CP, CP_SP, params), params


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


class TestTheButton(OpenpilotTestCase):
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
    self.assertNotIn("CloseFollowOneBar", params.writes)

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
    for want in expect:
      params.writes.clear()
      sd.press()
      self.assertEqual(params.writes, want)
    # the params thread's reads (every 0.1 s) never write
    params.writes.clear()
    for _ in range(50):
      sd.read_personality()
    self.assertEqual(params.writes, [])
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

  def test_a_read_before_the_write_lands(self):
    # route 14c t=365.0-365.15 (upstream's race): the thread read the param before the press's write landed and put the
    # old personality back for 0.1 s. Here the read keeps the press's values until its writes land...
    sd, params = start(AGGRESSIVE, lag=True)
    sd.press()
    self.assertEqual(sd.bars(), 1)
    self.assertFalse(Params().get_bool("CloseFollowOneBar"), "not landed yet")
    for _ in range(db.PENDING_READS):
      sd.read_personality()
      self.assertEqual(sd.bars(), 1)
    params.land()
    sd.read_personality()
    self.assertEqual(sd.bars(), 1)
    self.assertEqual(sd.distance_bars._pending, {})
    sd.press()                      # 1 -> 4: both params
    for _ in range(3):
      sd.read_personality()
      self.assertEqual((sd.personality, sd.bars()), (RELAXED, 4))
    params.land()
    sd.read_personality()
    self.assertEqual(sd.distance_bars._pending, {})
    # ...a setting nobody pressed for applies on the next read...
    Params().put("LongitudinalPersonality", STANDARD, block=True)
    sd.read_personality()
    self.assertEqual(sd.bars(), 3)
    # ...and a write that never lands loses to the param after PENDING_READS reads
    sd.press()
    for _ in range(db.PENDING_READS):
      sd.read_personality()
    self.assertEqual(sd.personality, AGGRESSIVE)
    sd.read_personality()
    self.assertEqual(sd.personality, STANDARD)


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
