"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-RELAY): the brake-lamp board alerts (brake_lamp_alert.py). What they must do: announce a board that is
offline, latched, not lighting the lamps for openpilot's braking, or leaving them stuck on - once per episode, with one
short sound, the most important first and each for its time on screen; remind at most every five minutes, silently;
never fire on a healthy drive (the pedal's own lamps, the relay letting go, the car's brake hold at standstill), on a
stale frame, without openpilot longitudinal, on another car or without a board; never hide "Take Control" or driver
monitoring; and never be anything but a warning.
"""
import re
import time
from pathlib import Path
from types import SimpleNamespace

from opendbc.car.honda.values import CAR
from openpilot.cereal import custom
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.selfdrived import brake_lamp_alert as bla
from openpilot.sunnypilot.selfdrive.selfdrived.brake_lamp_alert import BrakeLampAlert, ANNOUNCE_FRAMES, \
  ANNOUNCE_HOLD_FRAMES, CLEAR_FRAMES, FAULT_CONFIRM_FRAMES, NEVER_HEARD_FRAMES, OFFLINE_CONFIRM_FRAMES, REMINDER_FRAMES, \
  REMINDER_PERIOD_FRAMES, STUCK_ON_CONFIRM_FRAMES
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, EVENTS_SP, ET, Alert, Priority, AlertSize, \
  AlertStatus, AudibleAlert

EventNameSP = custom.OnroadEventSP.EventName
OFFLINE = EventNameSP.brakeLampBoardOffline
LATCHED = EventNameSP.brakeLampBoardLatched
NOT_LIT = EventNameSP.brakeLampsNotLighting
STUCK_ON = EventNameSP.brakeLampsStuckOn
REMIND = EventNameSP.brakeLampBoardReminder
ANNOUNCEMENTS = (OFFLINE, LATCHED, NOT_LIT, STUCK_ON)
ALL = (*ANNOUNCEMENTS, REMIND)

ROOT = Path(__file__).parents[4]   # openpilot/
SELFDRIVED = ROOT / "selfdrive/selfdrived/selfdrived.py"
ALERT_RENDERER = ROOT / "selfdrive/ui/mici/onroad/alert_renderer.py"
FONTS = ROOT / "selfdrive/assets/fonts"
S = int(1.0 / DT_CTRL)   # frames per second

LIT, DARK, VBAT = 13900, 60, 12400   # mV on the logged drives: lamps lit 13.3-14.1 V, dark under 0.1 V
D_LAMP_DARK, D_COIL_OPEN, D_LAMP_WITHOUT_US = 1 << 2, 1 << 3, 1 << 4


def blr(valid=True, state=1, latch=0, faults=0, request=False, out=False, fresh=True, lamp=DARK, analog=True):
  """carStateSP.brakeLampRelay as the helper reads it. A stale 0x620 reads all-zero, a stale 0x621 zero mV, as
  carstate_ext does."""
  if not valid:
    return SimpleNamespace(valid=False, state=0, latchReason=0, faults=0, request=False, outDrive=False, cmdFresh=False,
                           lampSnsMv=0, vbatSnsMv=0)
  return SimpleNamespace(valid=True, state=state, latchReason=latch, faults=faults, request=request, outDrive=out,
                         cmdFresh=fresh, lampSnsMv=lamp if analog else 0, vbatSnsMv=VBAT if analog else 0)


BRAKING = blr(request=True, out=True, lamp=LIT)   # openpilot braking, the board lighting the lamps


def car(brake=False, v=20.0, parking=False, aeb=False):
  return SimpleNamespace(brakePressed=brake, vEgo=v, parkingBrake=parking, stockAeb=aeb)


class FakeParams:
  def __init__(self, version="42524898", seen_at=None):
    self.values = {"BlrBoardVersion": version, "BlrBoardSeenAt": seen_at}

  def get(self, key):
    return self.values[key]


def alert(long=True, installed=True, seen_at=None, fingerprint=CAR.HONDA_ACCORD_9G_AU) -> BrakeLampAlert:
  CP = SimpleNamespace(openpilotLongitudinalControl=long, carFingerprint=fingerprint)
  return BrakeLampAlert(CP, FakeParams("42524898" if installed else None, seen_at))


def run(a: BrakeLampAlert, frames: int, b, c=None, can_show: bool = True) -> list[list[int]]:
  c = c or car()
  out = [a.update(SimpleNamespace(brakeLampRelay=b), c, can_show) for _ in range(frames)]
  assert all(set(f) <= set(ALL) for f in out), "an event that is not one of the five"
  return out


def cycles(a: BrakeLampAlert, n: int, braking, idle=None, brake_s=2.0, idle_s=8.0) -> list[list[int]]:
  out = []
  for _ in range(n):
    out += run(a, int(idle_s * S), idle or blr())
    out += run(a, int(brake_s * S), braking)
  return out


def count(frames: list[list[int]], event: int) -> int:
  return sum(event in f for f in frames)


def first(frames: list[list[int]], event: int) -> int:
  return next(i for i, f in enumerate(frames) if event in f)


def healthy_drive() -> list[tuple]:
  """(blr, car) frames like the four logged drives: openpilot braking with the board lighting the lamps (on 50 ms after
  the request, the analog frame showing them lit 0.2 s later, the output held 0.25 s after the request - the board's
  min-on), the lamps reading lit 0.2 s more as the relay lets go, driver pedal presses (the pedal's own lamps,
  D_LAMP_WITHOUT_US as on every press on this car), and 30 s at a stop on the car's brake hold: lamps lit, no pedal, no
  output."""
  out = []
  def add(n, b, c=None):
    out.extend([(b, c or car())] * n)
  for _ in range(20):
    add(4 * S, blr())
    add(5, blr(request=True))
    add(int(0.2 * S), blr(request=True, out=True, lamp=DARK))
    add(3 * S, BRAKING)
    add(int(0.25 * S), blr(out=True, lamp=LIT))
    add(int(0.2 * S), blr(lamp=LIT))
    add(2 * S, blr())
    add(int(1.5 * S), blr(faults=D_LAMP_WITHOUT_US, lamp=LIT), car(brake=True))
    add(int(0.45 * S), blr(faults=D_LAMP_WITHOUT_US, lamp=LIT))
  add(30 * S, blr(lamp=LIT), car(v=0.0))
  add(10 * S, blr())
  return out


class TestBrakeLampAlert(OpenpilotTestCase):
  def test_the_thresholds(self):
    # in seconds, as the docstring and the docs state them: the tests below use the names
    assert (OFFLINE_CONFIRM_FRAMES, NEVER_HEARD_FRAMES, FAULT_CONFIRM_FRAMES, STUCK_ON_CONFIRM_FRAMES, CLEAR_FRAMES) == \
      (1 * S, 10 * S, 1 * S, 5 * S, 3 * S)
    assert (ANNOUNCE_FRAMES, ANNOUNCE_HOLD_FRAMES, REMINDER_PERIOD_FRAMES, REMINDER_FRAMES) == (S // 10, 6 * S, 300 * S, S // 10)
    assert (bla.LAMP_LIT_MV, bla.STUCK_ON_MIN_SPEED, bla.INSTALLED_RECENT_S, bla.STATE_LATCHED) == (6000, 5.0, 14 * 86400, 4)
    assert (bla.D_LAMP_DARK, bla.D_COIL_OPEN) == (D_LAMP_DARK, D_COIL_OPEN)
    assert bla.ORDER == ("latched", "stuck on", "not lighting", "offline")
    assert all(EVENTS_SP[e][ET.WARNING].duration == ANNOUNCE_HOLD_FRAMES for e in ANNOUNCEMENTS), "its 6 s on screen"

  def test_a_healthy_drive_raises_nothing(self):
    # it starts the way a drive does when card comes up before the board: 0.5 s without a 0x620
    for installed in (True, False):
      a = alert(installed=installed)
      assert not any(run(a, S // 2, blr(valid=False)))
      assert a.conditions["offline"].towards == (S // 2 if installed else 0)
      events = [a.update(SimpleNamespace(brakeLampRelay=b), c, True) for b, c in healthy_drive()]
      assert not any(events), [e for e in events if e][:3]
      assert a.active == []

  def test_a_board_that_goes_silent(self):
    a = alert()
    run(a, S, blr())
    out = run(a, OFFLINE_CONFIRM_FRAMES - 1, blr(valid=False))
    assert not any(out), "valid is already 1 s late: one more second, not less"
    out = run(a, 30 * S, blr(valid=False))
    assert count(out, OFFLINE) == ANNOUNCE_FRAMES and OFFLINE in out[0]
    assert count(out, REMIND) == 0
    # back for less than CLEAR_FRAMES and gone again: still the same episode, no second announcement
    run(a, CLEAR_FRAMES - 1, blr())
    assert not any(run(a, 10 * S, blr(valid=False)))
    # back for CLEAR_FRAMES: cleared, and the next silence announces again
    run(a, CLEAR_FRAMES, blr())
    assert a.active == []
    out = run(a, 3 * S, blr(valid=False))
    assert count(out, OFFLINE) == ANNOUNCE_FRAMES

  def test_a_board_this_car_has_had_never_heard(self):
    a = alert(installed=True)
    out = run(a, NEVER_HEARD_FRAMES - 1, blr(valid=False))
    assert not any(out), "the board boots within a second of the bus; 10 s of grace"
    assert run(a, 1, blr(valid=False))[0] == [OFFLINE]

  def test_installed_means_lately_and_on_this_car(self):
    now = time.time()  # noqa: TID251 - wall clock, as BlrBoardSeenAt holds it
    for seen_at, expect in ((None, True), (str(now - 3600), True), (str(now - 13 * 86400), True),
                            (str(now - 15 * 86400), False), (str(now + 10 * 365 * 86400), True), ("garbage", False)):
      a = alert(seen_at=seen_at)
      assert a.installed == expect, seen_at
      assert (count(run(a, 2 * NEVER_HEARD_FRAMES, blr(valid=False)), OFFLINE) == ANNOUNCE_FRAMES) == expect, seen_at
    assert alert(fingerprint="HONDA_ACCORD_9G_AU").enabled, "CarParams carries the fingerprint as a plain string"
    # another car: nothing, whatever the params say (the decoder runs on HONDA_ELESYS only)
    a = alert(fingerprint="MOCK")
    assert not a.enabled and not a.installed
    assert not any(run(a, 2 * NEVER_HEARD_FRAMES, blr(valid=False)))
    assert not any(run(a, 10 * S, blr(state=4, latch=1)))

  def test_no_board_no_alert(self):
    a = alert(installed=False)
    assert not any(run(a, 600 * S, blr(valid=False)))
    assert a.active == []

  def test_without_openpilot_longitudinal_nothing(self):
    a = alert(long=False)
    for b in (blr(valid=False), blr(state=4, latch=2, request=True), blr(fresh=False), blr(lamp=LIT)):
      assert not any(run(a, 60 * S, b))

  def test_a_latch_is_announced_once_and_explains_the_dark_lamps(self):
    a = alert()
    run(a, S, blr())
    assert not any(run(a, FAULT_CONFIRM_FRAMES - 1, blr(state=4, latch=2, request=True)))
    out = run(a, 2 * S, blr(state=4, latch=2, request=True))
    out += cycles(a, 30, blr(state=4, latch=2, request=True), idle=blr(state=4, latch=2))
    assert count(out, LATCHED) == ANNOUNCE_FRAMES and LATCHED in out[0]
    assert count(out, NOT_LIT) == 0, "a latch explains it: one announcement over every brake"
    assert set(a.active) == {"latched", "not lighting"}
    for reason in (1, 3, 4):   # a latch reason alone counts too
      a = alert()
      assert count(run(a, 2 * S, blr(state=2, latch=reason)), LATCHED) == ANNOUNCE_FRAMES

  def test_a_trip_then_a_latch_announces_both_in_turn(self):
    # the third U4 trip latches: "not lighting" while it retries, then "board fault - turn it off and on"
    a = alert()
    run(a, S, blr())
    out = run(a, int(1.5 * S), blr(state=3, request=True))
    out += run(a, 10 * S, blr(state=4, latch=1, request=True))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES and count(out, LATCHED) == ANNOUNCE_FRAMES
    assert first(out, LATCHED) - first(out, NOT_LIT) == ANNOUNCE_HOLD_FRAMES

  def test_openpilot_braking_with_the_board_not_driving(self):
    a = alert()
    run(a, S, blr())
    assert not any(run(a, FAULT_CONFIRM_FRAMES - 1, blr(request=True)))
    out = run(a, 10 * S, blr(request=True))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES and NOT_LIT in out[0]
    # a trip retrying (FAULT) is the same reading
    a = alert()
    assert count(run(a, 2 * S, blr(state=3, request=True)), NOT_LIT) == ANNOUNCE_FRAMES

  def test_a_fault_seen_only_while_braking_is_one_episode(self):
    # 10 minutes of city driving, 2 s of openpilot braking every 10 s: one announcement, then the silent reminder
    for braking in (blr(request=True), blr(request=True, out=True, faults=D_LAMP_DARK, lamp=DARK)):
      a = alert()
      out = cycles(a, 60, braking)
      assert count(out, NOT_LIT) == ANNOUNCE_FRAMES, braking
      assert count(out, REMIND) == REMINDER_FRAMES, braking
      assert a.active == ["not lighting"]
      # it clears on 3 s of braking with the lamps driven and lit, here split over two brakes
      run(a, int(1.5 * S), BRAKING)
      run(a, 8 * S, blr())
      assert a.active == ["not lighting"]
      run(a, int(1.5 * S), BRAKING)
      assert a.active == []

  def test_the_lamps_dark_while_driven(self):
    a = alert()
    out = run(a, 2 * S, blr(request=True, out=True, faults=D_LAMP_DARK | D_LAMP_WITHOUT_US, lamp=DARK))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES
    # the bit with the output off is the board's after-image (it clears 500 ms after its condition), and the pedal's
    # D_LAMP_WITHOUT_US is every press on this car
    a = alert()
    assert not any(run(a, 10 * S, blr(faults=D_LAMP_DARK)))
    assert not any(run(a, 60 * S, blr(faults=D_LAMP_WITHOUT_US, lamp=LIT), car(brake=True)))

  def test_the_coil_open_at_idle(self):
    # the board's own row: output off, relay line low, lamps low - the coil or its feed, before openpilot ever brakes
    a = alert()
    run(a, S, blr())
    assert not any(run(a, FAULT_CONFIRM_FRAMES - 1, blr(faults=D_COIL_OPEN)))
    out = run(a, 10 * S, blr(faults=D_COIL_OPEN))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES and NOT_LIT in out[0]

  def test_a_board_that_does_not_hear_openpilot(self):
    a = alert()
    run(a, S, blr())
    assert not any(run(a, FAULT_CONFIRM_FRAMES - 1, blr(fresh=False)))
    assert NOT_LIT in run(a, 1, blr(fresh=False))[0]
    # it hears 0x1FA again: still one episode (the rest of its announcement, nothing new) until openpilot brakes with
    # the lamps lit
    out = run(a, 60 * S, blr())
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES - 1 and count(out, REMIND) == 0
    assert a.active == ["not lighting"]
    run(a, CLEAR_FRAMES, BRAKING)
    assert a.active == []

  def test_lamps_stuck_on(self):
    a = alert()
    run(a, S, blr())
    assert not any(run(a, STUCK_ON_CONFIRM_FRAMES - 1, blr(lamp=LIT)))
    out = run(a, 10 * S, blr(lamp=LIT))
    assert count(out, STUCK_ON) == ANNOUNCE_FRAMES and STUCK_ON in out[0]
    # what cannot tell never confirms: a stop (brake hold), the pedal, the parking brake (the VSA's emergency stop on
    # the switch), CMBS, the board's own output, a stale analog frame
    for b, c in ((blr(lamp=LIT), car(v=0.0)), (blr(lamp=LIT), car(v=bla.STUCK_ON_MIN_SPEED)),
                 (blr(lamp=LIT), car(brake=True)), (blr(lamp=LIT), car(parking=True)), (blr(lamp=LIT), car(aeb=True)),
                 (blr(out=True, lamp=LIT), car()), (blr(lamp=LIT, analog=False), car())):
      a = alert()
      assert not any(run(a, 60 * S, b, c)), (b, c)

  def test_a_stuck_on_episode_survives_a_stop_and_a_0_mv_reading_clears_it(self):
    a = alert()
    run(a, STUCK_ON_CONFIRM_FRAMES + S, blr(lamp=LIT))
    run(a, 60 * S, blr(lamp=LIT), car(v=0.0))
    run(a, 10 * S, blr(lamp=LIT), car(brake=True))
    assert a.active == ["stuck on"]
    run(a, CLEAR_FRAMES - 1, blr(lamp=0))   # a true 0 mV reading of dark lamps, the analog frame fresh
    assert a.active == ["stuck on"]
    run(a, 1, blr(lamp=0))
    assert a.active == []

  def test_a_stale_frame_counts_towards_neither(self):
    # 0.6 s of evidence, 0.5 s stale (not long enough to be offline), 0.5 s more: confirmed on the 1 s of evidence
    a = alert()
    run(a, S, blr())
    run(a, int(0.6 * S), blr(request=True))
    assert not any(run(a, int(0.5 * S), blr(valid=False)))
    out = run(a, int(0.5 * S), blr(request=True))
    assert first(out, NOT_LIT) == FAULT_CONFIRM_FRAMES - int(0.6 * S) - 1

  def test_the_announcement_waits_until_it_can_be_seen(self):
    a = alert()
    run(a, S, blr())
    assert not any(run(a, 30 * S, blr(request=True), can_show=False))
    assert a.active == ["not lighting"]
    out = run(a, 10 * S, blr(request=True))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES and NOT_LIT in out[0]

  def test_a_reminder_every_five_minutes_silently(self):
    a = alert()
    run(a, S, blr())
    # confirmed on the 100th frame of evidence, announced on it and the 9 after, then its 6 s on screen: this run
    # ends on the last of them
    out = run(a, FAULT_CONFIRM_FRAMES + ANNOUNCE_HOLD_FRAMES - 1, blr(fresh=False))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES
    out = run(a, REMINDER_PERIOD_FRAMES + 10, blr(fresh=False))
    assert count(out, REMIND) == REMINDER_FRAMES and count(out, NOT_LIT) == 0
    assert first(out, REMIND) == REMINDER_PERIOD_FRAMES - 1

  def test_two_conditions_announce_in_turn(self):
    a = alert()
    run(a, S, blr())
    out = run(a, 20 * S, blr(fresh=False, lamp=LIT))
    assert count(out, NOT_LIT) == ANNOUNCE_FRAMES and count(out, STUCK_ON) == ANNOUNCE_FRAMES
    assert first(out, STUCK_ON) - first(out, NOT_LIT) == ANNOUNCE_HOLD_FRAMES, "the second waits out the first's 6 s"

  def test_the_most_important_goes_first(self):
    # PIN_STUCK_HIGH: latched with the pin holding the lamps lit. Both confirm while nothing is engaged; on the next
    # engagement the latch ("turn the car off and on") comes first, stuck on 6 s later
    a = alert()
    run(a, S, blr())
    assert not any(run(a, 10 * S, blr(state=4, latch=4, lamp=LIT), can_show=False))
    assert set(a.active) == {"latched", "stuck on"}
    out = run(a, 20 * S, blr(state=4, latch=4, lamp=LIT))
    assert first(out, LATCHED) == 0 and first(out, STUCK_ON) == ANNOUNCE_HOLD_FRAMES
    # queued behind another: not lighting (1 s) is up first, then stuck on (5 s) and a latch (7 s) wait - the latch
    # goes before stuck on although it confirmed later
    a = alert()
    run(a, S, blr())
    run(a, 6 * S, blr(request=True, lamp=LIT), can_show=False)
    run(a, 2 * S, blr(state=4, latch=1, request=True, lamp=LIT), can_show=False)
    assert set(a.active) == {"not lighting", "stuck on", "latched"}
    out = run(a, 20 * S, blr(state=4, latch=1, request=True, lamp=LIT))
    assert (first(out, NOT_LIT), first(out, LATCHED), first(out, STUCK_ON)) == \
      (0, ANNOUNCE_HOLD_FRAMES, 2 * ANNOUNCE_HOLD_FRAMES)

  def test_a_clear_before_its_announcement_ends_it(self):
    # stuck on announced; not lighting confirms while nothing is engaged and clears before it was seen: never shown
    a = alert()
    run(a, S, blr())
    run(a, STUCK_ON_CONFIRM_FRAMES + ANNOUNCE_HOLD_FRAMES, blr(lamp=LIT))
    run(a, 2 * S, blr(request=True, lamp=LIT), can_show=False)
    assert set(a.active) == {"stuck on", "not lighting"}
    run(a, CLEAR_FRAMES, BRAKING, can_show=False)
    assert a.active == ["stuck on"]
    assert count(run(a, 20 * S, blr(lamp=LIT)), NOT_LIT) == 0


class TestBrakeLampAlertDefinitions(OpenpilotTestCase):
  def test_warnings_only(self):
    """Nothing that can disable, block an engagement or drop longitudinal."""
    assert set(bla.ANNOUNCE.values()) | {bla.REMIND} == set(ALL)
    for e in ALL:
      assert set(EVENTS_SP[e].keys()) == {ET.WARNING}, EVENTS_SP[e].keys()
      ev = EventsSP()
      ev.add(e)
      for et in (ET.NO_ENTRY, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.USER_DISABLE, ET.PERMANENT,
                 ET.ENABLE, ET.PRE_ENABLE, ET.OVERRIDE_LATERAL, ET.OVERRIDE_LONGITUDINAL):
        assert not ev.contains(et), (e, et)

  def test_shown_only_while_engaged(self):
    for e in ALL:
      ev = EventsSP()
      ev.add(e)
      assert ev.create_alerts([ET.PERMANENT]) == [], "disengaged: a WARNING is not shown"
      alerts = ev.create_alerts([ET.PERMANENT, ET.WARNING])
      assert len(alerts) == 1 and alerts[0].event_type == ET.WARNING

  def test_one_short_sound_then_silence(self):
    remind = EVENTS_SP[REMIND][ET.WARNING]
    assert isinstance(remind, Alert)
    assert remind.audible_alert == AudibleAlert.none and remind.priority == Priority.LOWEST
    assert remind.alert_status == AlertStatus.normal and remind.alert_size == AlertSize.small
    assert remind.alert_text_1 and not remind.alert_text_2
    for e in ANNOUNCEMENTS:
      announce = EVENTS_SP[e][ET.WARNING]
      assert isinstance(announce, Alert)
      assert announce.audible_alert == AudibleAlert.prompt, "prompt plays once; promptRepeat would loop"
      assert announce.priority == Priority.LOWER, "under steerSaturated's Take Control (LOW), LDW and driver monitoring"
      assert announce.alert_status == AlertStatus.userPrompt and announce.alert_size == AlertSize.mid
      assert announce.alert_text_1 and announce.alert_text_2

  def test_texts_use_only_glyphs_the_font_has(self):
    for e in ALL:
      a = EVENTS_SP[e][ET.WARNING]
      for s in (a.alert_text_1, a.alert_text_2):
        assert all(32 <= ord(c) <= 126 for c in s), s

  def test_texts_fit_the_mici_alert_renderer(self):
    """Wrap each text the way mici/onroad/alert_renderer.py sizes it, measured with the real font files (as
    test_eps_latch_alert.py does)."""
    try:
      from PIL import ImageFont
      for name in ("Inter-Bold.ttf", "Inter-Regular.ttf"):
        ImageFont.truetype(str(FONTS / name), 32)
    except Exception as e:  # no Pillow, or the fonts are LFS pointers in this checkout
      self.skipTest(f"cannot load the UI fonts: {e}")

    src = ALERT_RENDERER.read_text(encoding="utf-8")
    assert "ALERT_MARGIN = 18" in src
    assert re.search(r"if len\(alert_text1\) <= 12:\s*\n\s*font_size = 92 - 10", src)
    assert re.search(r"elif len\(alert_text1\) <= 16:\s*\n\s*font_size = 70", src)
    assert re.search(r"else:\s*\n\s*font_size = 64 - 10", src)
    assert re.search(r"if len\(alert_text2\) > 24:\s*\n\s*small_font_size = 32", src)
    width = 536 - 18

    def lines(text: str, font) -> int:
      n, cur = 1, ""
      for w in text.split(" "):
        cand = f"{cur} {w}".strip()
        if font.getlength(cand) <= width:
          cur = cand
        else:
          assert font.getlength(w) <= width, f"{w!r} alone is wider than the screen"
          n, cur = n + 1, w
      return n

    for e in ALL:
      a = EVENTS_SP[e][ET.WARNING]
      t1, t2 = a.alert_text_1.lower(), a.alert_text_2.lower()
      size1 = 82 if len(t1) <= 12 else 70 if len(t1) <= 16 else 54
      n1 = lines(t1, ImageFont.truetype(str(FONTS / "Inter-Bold.ttf"), size1))
      assert n1 <= 2, f"{t1!r} wraps to {n1} lines"
      height = n1 * size1 * 0.86
      if t2:
        size2 = 32 if len(t2) > 24 else 36 if len(t2) > 18 else 40
        n2 = lines(t2, ImageFont.truetype(str(FONTS / "Inter-Regular.ttf"), size2))
        assert n2 <= 2, f"{t2!r} wraps to {n2} lines"
        height += n2 * size2 * 0.86
      assert height <= 240 - 20, f"{t1!r}/{t2!r} is {height:.0f} px tall"

  def test_event_names_are_in_the_schema(self):
    names = custom.OnroadEventSP.EventName.schema.enumerants
    for name in ("brakeLampBoardOffline", "brakeLampBoardLatched", "brakeLampsNotLighting", "brakeLampsStuckOn",
                 "brakeLampBoardReminder"):
      assert names[name] in EVENTS_SP, name


def through_the_alert_manager(a: BrakeLampAlert, frames, other_events=()) -> list[str]:
  """selfdrived's path: helper -> EventsSP -> create_alerts -> AlertManager, engaged throughout. other_events: upstream
  events raised every frame beside it."""
  from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
  from openpilot.selfdrive.selfdrived.events import Events
  am, ev, ev_sp = AlertManager(), Events(), EventsSP()
  shown: list[str] = []
  for frame, b in enumerate(frames):
    ev.clear()
    ev_sp.clear()
    for e in other_events:
      ev.add(e)
    for e in a.update(SimpleNamespace(brakeLampRelay=b), car(), True):
      ev_sp.add(e)
    am.add_many(frame, ev.create_alerts([ET.PERMANENT, ET.WARNING]) + ev_sp.create_alerts([ET.PERMANENT, ET.WARNING]))
    am.process_alerts(frame, set())
    shown.append(am.current_alert.alert_text_1)
  return shown


class TestThroughTheAlertManager(OpenpilotTestCase):
  def test_on_screen_for_six_seconds_then_four_every_five_minutes(self):
    frames = [blr()] * S + [blr(fresh=False)] * (FAULT_CONFIRM_FRAMES + ANNOUNCE_HOLD_FRAMES + REMINDER_PERIOD_FRAMES + 1000)
    shown = through_the_alert_manager(alert(), frames)
    announce = [i for i, s in enumerate(shown) if s == "Brake Lights Not Lighting"]
    remind = [i for i, s in enumerate(shown) if s == "Brake Light Fault"]
    assert announce[0] == S + FAULT_CONFIRM_FRAMES - 1
    assert len(announce) == 601 and announce[-1] - announce[0] == 600, "6 s, once"
    assert len(remind) == 401 and remind[-1] - remind[0] == 400, "4 s"
    assert set(shown) == {"", "Brake Lights Not Lighting", "Brake Light Fault"}

  def test_two_announcements_each_get_their_six_seconds(self):
    frames = [blr()] * S + [blr(fresh=False, lamp=LIT)] * (20 * S)
    shown = through_the_alert_manager(alert(), frames)
    for text in ("Brake Lights Not Lighting", "Brake Lights Stuck On"):
      assert shown.count(text) >= 600, (text, shown.count(text))

  def test_it_never_hides_driver_monitoring_or_take_control(self):
    from openpilot.cereal import log
    for e, text in ((log.OnroadEvent.EventName.driverDistracted2, "Pay Attention"),
                    (log.OnroadEvent.EventName.steerSaturated, "Take Control")):
      a = alert()
      frames = [blr()] * S + [blr(valid=False)] * (OFFLINE_CONFIRM_FRAMES + 2 * S + 700)
      shown = through_the_alert_manager(a, frames, other_events=(e,))
      assert a.active == ["offline"]
      assert set(shown) == {text}, (text, set(shown))


class TestSelfdrivedWiring(OpenpilotTestCase):
  def test_selfdrived_raises_it_from_the_board_state(self):
    src = SELFDRIVED.read_text(encoding="utf-8")
    assert "self.brake_lamp_alert = BrakeLampAlert(self.CP, self.params)" in src
    call = re.search(r"for e in self\.brake_lamp_alert\.update\(self\.sm\['carStateSP'\], CS, " +
                     r"self\.active or self\.mads\.active\):\s*\n\s*self\.events_sp\.add\(e\)", src)
    assert call, "the helper is not fed carStateSP and carState, or its events do not reach events_sp"
    assert src.index("if self.CP.passive:\n      return") < call.start()
    # selfdrived itself names no board field and no Blr* param (test_blr_board.py's guard holds that too)
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    assert not re.search(r"\bBlr[A-Z]\w*|brakeLampRelay", code)


if __name__ == "__main__":
  import unittest
  unittest.main()
