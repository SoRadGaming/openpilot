"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(LKAS-GATEWAY): the "restart the car" alert for an EPS latched until key-off
(eps_latch_alert.py). What it must do: announce a real latch once, with one short sound;
remind at most every five minutes, silently; never fire on a transient, on a stale frame or
without a board; and never be anything but a warning.
"""
import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal import custom
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.selfdrived.eps_latch_alert import EpsLatchAlert, LATCH_CONFIRM_FRAMES, \
  LATCH_CLEAR_FRAMES, ANNOUNCE_FRAMES, REMINDER_PERIOD_FRAMES, REMINDER_FRAMES
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, EVENTS_SP, ET, Alert, Priority, AlertSize, \
  AlertStatus, AudibleAlert

EventNameSP = custom.OnroadEventSP.EventName
ANNOUNCE = EventNameSP.lkasGatewayEpsLatched
REMIND = EventNameSP.lkasGatewayEpsLatchedReminder

ROOT = Path(__file__).parents[4]   # openpilot/
SELFDRIVED = ROOT / "selfdrive/selfdrived/selfdrived.py"
ALERT_RENDERER = ROOT / "selfdrive/ui/mici/onroad/alert_renderer.py"
FONTS = ROOT / "selfdrive/assets/fonts"


def gw(latched: bool, fresh: bool = True, present: bool = True):
  """carStateSP.linbusGateway as the helper reads it. A stale 0x70B reads as not latched, as carstate_ext does."""
  return SimpleNamespace(present=present, grantValid=present and fresh, latchedUntilKeyOff=present and fresh and latched)


def run(alert: EpsLatchAlert, frames: int, g, can_show: bool = True) -> list[list[int]]:
  return [alert.update(g, can_show) for _ in range(frames)]


def count(frames: list[list[int]], event: int) -> int:
  return sum(event in f for f in frames)


class TestEpsLatchAlert(OpenpilotTestCase):
  def setup_method(self):
    self.a = EpsLatchAlert()

  def test_a_latch_is_announced_once(self):
    out = run(self.a, LATCH_CONFIRM_FRAMES - 1, gw(True))
    assert count(out, ANNOUNCE) == 0, "announced before the latch was confirmed"
    out = run(self.a, int(60 / 0.01), gw(True))
    assert self.a.latched
    assert count(out, ANNOUNCE) == ANNOUNCE_FRAMES
    assert ANNOUNCE in out[0], "the confirming frame announces"
    assert count(out, REMIND) == 0, "no reminder inside the first five minutes"

  def test_a_transient_is_not_announced(self):
    # the board itself ignores EPS errors under 200 ms; even 0.9 s of RETRY_IN 255 is not a latch
    for _ in range(5):
      out = run(self.a, LATCH_CONFIRM_FRAMES - 10, gw(True)) + run(self.a, 50, gw(False))
      assert count(out, ANNOUNCE) == 0 and count(out, REMIND) == 0
    assert not self.a.latched

  def test_nothing_without_a_board(self):
    for g in (None, gw(True, present=False), gw(True, fresh=False)):
      a = EpsLatchAlert()
      out = run(a, 10 * LATCH_CONFIRM_FRAMES, g)
      assert not a.latched and not any(out), g

  def test_stale_grant_frames_neither_confirm_nor_clear(self):
    # Route fd after its latch: fresh latched frames, then 0x70B stale for 0.1-25 s at a time, ~20 times a drive.
    out = run(self.a, 140, gw(True))                        # 1.4 s latched: confirmed at 1.0 s
    for stale_s, latched_s in ((3.1, 4.8), (25.7, 0.6), (0.1, 13.0), (5.6, 1.3), (12.0, 9.0)):
      out += run(self.a, int(stale_s / 0.01), gw(False, fresh=False))
      assert self.a.latched, "a stale frame cleared the latch"
      out += run(self.a, int(latched_s / 0.01), gw(True))
    assert count(out, ANNOUNCE) == ANNOUNCE_FRAMES, "the latch was announced more than once"

  def test_stale_frames_do_not_count_towards_a_confirm_either(self):
    out = run(self.a, LATCH_CONFIRM_FRAMES // 2, gw(True)) + run(self.a, 500, gw(False, fresh=False))
    assert count(out, ANNOUNCE) == 0
    out = run(self.a, LATCH_CONFIRM_FRAMES - LATCH_CONFIRM_FRAMES // 2, gw(True))
    assert count(out, ANNOUNCE) == 1, "latched evidence on both sides of a stale gap adds up"

  def test_only_a_real_clear_rearms_it(self):
    run(self.a, 200, gw(True))
    out = run(self.a, LATCH_CLEAR_FRAMES - 1, gw(False)) + run(self.a, 200, gw(True))
    assert self.a.latched and count(out, ANNOUNCE) == 0, "2.99 s of 'not latched' re-armed it"
    out = run(self.a, LATCH_CLEAR_FRAMES, gw(False))
    assert not self.a.latched and not any(out)
    out = run(self.a, 200, gw(True))
    assert count(out, ANNOUNCE) == ANNOUNCE_FRAMES, "a new latch after a real clear is announced"

  def test_the_announcement_waits_until_it_can_be_seen(self):
    out = run(self.a, 30 * 100, gw(True), can_show=False)
    assert self.a.latched and not any(out), "raised while no WARNING could be shown: it would be lost"
    out = run(self.a, 100, gw(True), can_show=True)
    assert count(out, ANNOUNCE) == ANNOUNCE_FRAMES and ANNOUNCE in out[0]

  def test_a_silent_reminder_every_five_minutes(self):
    out = run(self.a, LATCH_CONFIRM_FRAMES + 3 * REMINDER_PERIOD_FRAMES + 100, gw(True))
    assert count(out, ANNOUNCE) == ANNOUNCE_FRAMES
    assert count(out, REMIND) == 3 * REMINDER_FRAMES
    first = next(i for i, f in enumerate(out) if REMIND in f)
    last_announce = max(i for i, f in enumerate(out) if ANNOUNCE in f)
    assert first - last_announce >= REMINDER_PERIOD_FRAMES - 1

  def test_a_reminder_waits_until_it_can_be_seen(self):
    run(self.a, LATCH_CONFIRM_FRAMES + 50, gw(True))
    out = run(self.a, REMINDER_PERIOD_FRAMES + 1000, gw(True), can_show=False)
    assert not any(out)
    out = run(self.a, 50, gw(True), can_show=True)
    assert count(out, REMIND) == REMINDER_FRAMES and REMIND in out[0]
    assert count(out, ANNOUNCE) == 0


class TestEpsLatchAlertDefinitions(OpenpilotTestCase):
  def test_warnings_only(self):
    """Nothing that can disable, block an engagement or drop longitudinal."""
    for e in (ANNOUNCE, REMIND):
      assert set(EVENTS_SP[e].keys()) == {ET.WARNING}, EVENTS_SP[e].keys()
      ev = EventsSP()
      ev.add(e)
      for et in (ET.NO_ENTRY, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.USER_DISABLE, ET.PERMANENT,
                 ET.ENABLE, ET.PRE_ENABLE, ET.OVERRIDE_LATERAL, ET.OVERRIDE_LONGITUDINAL):
        assert not ev.contains(et), (e, et)

  def test_shown_only_while_engaged(self):
    for e in (ANNOUNCE, REMIND):
      ev = EventsSP()
      ev.add(e)
      assert ev.create_alerts([ET.PERMANENT]) == [], "disengaged: a WARNING is not shown"
      alerts = ev.create_alerts([ET.PERMANENT, ET.WARNING])
      assert len(alerts) == 1 and alerts[0].event_type == ET.WARNING

  def test_one_short_sound_then_silence(self):
    announce = EVENTS_SP[ANNOUNCE][ET.WARNING]
    remind = EVENTS_SP[REMIND][ET.WARNING]
    assert isinstance(announce, Alert) and isinstance(remind, Alert)
    assert announce.audible_alert == AudibleAlert.prompt, "prompt plays once; promptRepeat would loop"
    assert remind.audible_alert == AudibleAlert.none
    assert remind.priority == Priority.LOWEST
    # driver monitoring's stage 2 is MID and a priority tie goes to the newer alert, so MID would hide it
    assert announce.priority < Priority.MID, "must never hide driver monitoring's stage 2, stage 3 or an FCW"
    assert announce.alert_status == AlertStatus.userPrompt and announce.alert_size == AlertSize.mid
    assert remind.alert_status == AlertStatus.normal and remind.alert_size == AlertSize.small
    # upstream's own sanity rules (selfdrive/selfdrived/tests/test_alerts.py)
    assert announce.alert_text_1 and announce.alert_text_2
    assert remind.alert_text_1 and not remind.alert_text_2

  def test_texts_use_only_glyphs_the_font_has(self):
    for e in (ANNOUNCE, REMIND):
      a = EVENTS_SP[e][ET.WARNING]
      for s in (a.alert_text_1, a.alert_text_2):
        assert all(32 <= ord(c) <= 126 for c in s), s

  def test_texts_fit_the_mici_alert_renderer(self):
    """Wrap each text the way mici/onroad/alert_renderer.py sizes it, measured with the real font files."""
    try:
      from PIL import ImageFont
      for name in ("Inter-Bold.ttf", "Inter-Regular.ttf"):
        ImageFont.truetype(str(FONTS / name), 32)
    except Exception as e:  # no Pillow, or the fonts are LFS pointers in this checkout
      self.skipTest(f"cannot load the UI fonts: {e}")

    src = ALERT_RENDERER.read_text(encoding="utf-8")
    # the renderer's sizing, so a change there fails here instead of clipping on the car
    assert "ALERT_MARGIN = 18" in src
    assert re.search(r"if len\(alert_text1\) <= 12:\s*\n\s*font_size = 92 - 10", src)
    assert re.search(r"elif len\(alert_text1\) <= 16:\s*\n\s*font_size = 70", src)
    assert re.search(r"else:\s*\n\s*font_size = 64 - 10", src)
    assert re.search(r"if len\(alert_text2\) > 24:\s*\n\s*small_font_size = 32", src)
    width = 536 - 18   # mici is 536 x 240; the text rect is the width less ALERT_MARGIN

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

    for e in (ANNOUNCE, REMIND):
      a = EVENTS_SP[e][ET.WARNING]
      t1, t2 = a.alert_text_1.lower(), a.alert_text_2.lower()   # the renderer lowercases both
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
    # create_alerts() raises KeyError for an event with no EVENTS_SP entry, so both must have one
    names = custom.OnroadEventSP.EventName.schema.enumerants
    assert names["lkasGatewayEpsLatched"] == ANNOUNCE and names["lkasGatewayEpsLatchedReminder"] == REMIND
    assert ANNOUNCE in EVENTS_SP and REMIND in EVENTS_SP


class TestThroughTheAlertManager(OpenpilotTestCase):
  def test_on_screen_for_six_seconds_then_four_every_five_minutes(self):
    """selfdrived's own path: helper -> EventsSP -> create_alerts -> AlertManager, engaged throughout."""
    from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
    a, am, ev = EpsLatchAlert(), AlertManager(), EventsSP()
    shown: list[str] = []
    for frame in range(LATCH_CONFIRM_FRAMES + REMINDER_PERIOD_FRAMES + 1000):
      ev.clear()
      for e in a.update(gw(True), True):
        ev.add(e)
      am.add_many(frame, ev.create_alerts([ET.PERMANENT, ET.WARNING]))
      am.process_alerts(frame, set())
      shown.append(am.current_alert.alert_text_1)
    announce = [i for i, s in enumerate(shown) if s == "Steering Fault"]
    remind = [i for i, s in enumerate(shown) if s == "Steering Off Until Restart"]
    # AlertEntry.active() is `frame <= end_frame`, so an alert of N frames is on screen for N + 1
    assert announce[0] == LATCH_CONFIRM_FRAMES - 1
    assert len(announce) == 601 and announce[-1] - announce[0] == 600, "6 s, once"
    assert len(remind) == 401 and remind[-1] - remind[0] == 400, "4 s"
    assert set(shown) == {"", "Steering Fault", "Steering Off Until Restart"}

  def test_it_never_hides_driver_monitoring(self):
    """A latch while DM stage 2 is on screen: DM keeps the screen (and its sound) throughout."""
    from openpilot.cereal import log
    from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
    from openpilot.selfdrive.selfdrived.events import Events
    dm = log.OnroadEvent.EventName.driverDistracted2
    a, am, ev, ev_sp = EpsLatchAlert(), AlertManager(), Events(), EventsSP()
    shown: list[str] = []
    for frame in range(LATCH_CONFIRM_FRAMES + 700):
      ev.clear()
      ev_sp.clear()
      ev.add(dm)
      for e in a.update(gw(True), True):
        ev_sp.add(e)
      am.add_many(frame, ev.create_alerts([ET.PERMANENT, ET.WARNING]) + ev_sp.create_alerts([ET.PERMANENT, ET.WARNING]))
      am.process_alerts(frame, set())
      shown.append(am.current_alert.alert_text_1)
    assert a.latched
    assert set(shown) == {"Pay Attention"}, set(shown)

  def test_disengaging_clears_it_from_the_screen(self):
    # selfdrived clears WARNING alerts when nothing is active (update_alerts' clear_event_types)
    from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
    a, am, ev = EpsLatchAlert(), AlertManager(), EventsSP()
    for frame in range(LATCH_CONFIRM_FRAMES + 100):
      ev.clear()
      for e in a.update(gw(True), True):
        ev.add(e)
      am.add_many(frame, ev.create_alerts([ET.PERMANENT, ET.WARNING]))
      am.process_alerts(frame, set())
    assert am.current_alert.alert_text_1 == "Steering Fault"
    am.process_alerts(LATCH_CONFIRM_FRAMES + 100, {ET.WARNING})
    assert am.current_alert.alert_text_1 == ""


class TestSelfdrivedWiring(OpenpilotTestCase):
  def test_selfdrived_raises_it_from_the_gateway_state(self):
    src = SELFDRIVED.read_text(encoding="utf-8")
    assert "self.eps_latch_alert = EpsLatchAlert()" in src
    call = re.search(r"for e in self\.eps_latch_alert\.update\(self\.sm\['carStateSP'\]\.linbusGateway, " +
                     r"self\.active or self\.mads\.active\):\s*\n\s*self\.events_sp\.add\(e\)", src)
    assert call, "the helper is not fed carStateSP.linbusGateway, or its events do not reach events_sp"
    # carStateSP must stay in selfdrived's SubMaster (mads.py needs it too)
    assert re.search(r"'carStateSP'\]", src)
    # it runs after the dashcam early return, on every other frame
    assert src.index("if self.CP.passive:\n      return") < call.start()


if __name__ == "__main__":
  import unittest
  unittest.main()
