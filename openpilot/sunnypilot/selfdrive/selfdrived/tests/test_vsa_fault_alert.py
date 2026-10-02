"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): the VSA's own fault on screen (vsa_fault_alert.py).

What it must do: a live VSA fault keeps upstream's immediate disable exactly as it was and says
"VSA" instead of "Cruise Fault: Restart the Car"; a stored one refuses openpilot's engagement
(and MADS's - the decision, see the helper) until the VSA clears it, saying how it clears; one
prompt per fault, silent banners after it; and nothing at all on any other car or without the flags.
"""
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from openpilot.cereal import log, custom
from opendbc.car import structs
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events, EVENTS
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, EVENTS_SP, ET, Alert, Priority, AlertSize, \
  AudibleAlert, ImmediateDisableAlert, EngagementAlert
from openpilot.sunnypilot.selfdrive.selfdrived.vsa_fault_alert import VsaFaultAlert, ANNOUNCE_FRAMES, REARM_FRAMES, \
  LIVE_REPLACES, STORED_REPLACES, EITHER_REPLACES

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName
State = log.SelfdriveState.OpenpilotState
MadsState = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState

LIVE = EventNameSP.vsaFault
STORED = EventNameSP.vsaStoredFault
ANNOUNCE = EventNameSP.vsaFaultAnnounce

ROOT = Path(__file__).parents[4]   # openpilot/
REPO = Path(__file__).parents[5]   # the repo root, which holds opendbc_repo/
SELFDRIVED = ROOT / "selfdrive/selfdrived/selfdrived.py"
CUSTOM_CAPNP = ROOT / "cereal/custom.capnp"
STRUCTS = REPO / "opendbc_repo/opendbc/car/structs.py"
ALERT_RENDERER = ROOT / "selfdrive/ui/mici/onroad/alert_renderer.py"
FONTS = ROOT / "selfdrive/assets/fonts"


def cs_sp(live=False, stored=False):
  return SimpleNamespace(vsaFault=live, vsaStoredFault=stored)


def made(event: int, et: str, metric: bool = True) -> Alert:
  """The alert an EVENTS_SP entry produces: the Alert itself, or what its callback returns (they only read metric)."""
  entry: Any = EVENTS_SP[event][et]
  return entry if isinstance(entry, Alert) else entry(None, None, None, metric, 0, None)


class Frame:
  """selfdrived's per-frame path for the events this touches: events -> state machine -> alerts -> AlertManager."""
  def __init__(self, enabled=False):
    self.helper = VsaFaultAlert()
    self.sm = StateMachine()
    self.sm.state = State.enabled if enabled else State.disabled
    self.am = AlertManager()
    self.events = Events()
    self.events_sp = EventsSP()
    self.frame = 0
    self.shown: list[tuple[str, str, int]] = []

  def step(self, live=False, stored=False, acc_faulted=False, extra=(), extra_sp=()):
    self.events.clear()
    self.events_sp.clear()
    if acc_faulted:
      self.events.add(EventName.accFaulted)
    for e in extra:
      self.events.add(e)
    for e in extra_sp:
      self.events_sp.add(e)
    ev, ev_sp = self.helper.update(cs_sp(live, stored), self.events.has(EventName.accFaulted))
    for e in ev:
      self.events.add(e)
    for e in ev_sp:
      self.events_sp.add(e)
    enabled, _ = self.sm.update(self.events)
    clear = set()
    if ET.WARNING not in self.sm.current_alert_types:
      clear.add(ET.WARNING)
    if enabled:
      clear.add(ET.NO_ENTRY)
    alerts = self.helper.filter_alerts(self.events.create_alerts(self.sm.current_alert_types, [None] * 6))
    alerts_sp = self.events_sp.create_alerts(self.sm.current_alert_types, [None, None, None, True, 0, None])
    self.am.add_many(self.frame, alerts + alerts_sp)
    self.am.process_alerts(self.frame, clear)
    a = self.am.current_alert
    self.shown.append((a.alert_text_1, a.alert_text_2, a.audible_alert))
    self.frame += 1
    return enabled


def sounds(shown) -> list[int]:
  """The sounds soundd would start: each change of the current alert's sound to something audible."""
  out, prev = [], AudibleAlert.none
  for _, _, s in shown:
    if s != prev and s != AudibleAlert.none:
      out.append(s)
    prev = s
  return out


class TestHelper(OpenpilotTestCase):
  def test_nothing_without_the_flags(self):
    h = VsaFaultAlert()
    for acc in (False, True):
      for _ in range(500):
        assert h.update(cs_sp(), acc) == ([], [])
    alerts = [SimpleNamespace(alert_type=t) for t in LIVE_REPLACES + STORED_REPLACES + EITHER_REPLACES]
    assert h.filter_alerts(alerts) == alerts

  def test_a_struct_without_the_fields_is_no_fault(self):
    h = VsaFaultAlert()
    assert h.update(SimpleNamespace(), True) == ([], [])
    assert h.update(None, True) == ([], [])

  def test_live_needs_accfaulted(self):
    h = VsaFaultAlert()
    ev, ev_sp = h.update(cs_sp(live=True), True)
    assert ev == [] and LIVE in ev_sp and ANNOUNCE in ev_sp
    h = VsaFaultAlert()
    assert h.update(cs_sp(live=True), False) == ([], []), "no BRAKE_ERROR: nothing is raised from the live bits alone"

  def test_stored_raises_the_carrier_and_the_text(self):
    h = VsaFaultAlert()
    ev, ev_sp = h.update(cs_sp(stored=True), False)
    assert ev == [EventName.carNotReady]
    assert STORED in ev_sp and ANNOUNCE in ev_sp

  def test_live_wins_over_stored(self):
    h = VsaFaultAlert()
    ev, ev_sp = h.update(cs_sp(live=True, stored=True), True)
    assert ev == [] and LIVE in ev_sp and STORED not in ev_sp

  def test_announced_once_per_episode(self):
    h = VsaFaultAlert()
    out = [h.update(cs_sp(stored=True), False)[1] for _ in range(3000)]
    out += [h.update(cs_sp(live=True, stored=True), True)[1] for _ in range(3000)]   # same episode
    assert sum(ANNOUNCE in f for f in out) == ANNOUNCE_FRAMES and ANNOUNCE in out[0]
    # a clear shorter than REARM_FRAMES is the same episode
    out = [h.update(cs_sp(), False)[1] for _ in range(REARM_FRAMES - 1)] + [h.update(cs_sp(stored=True), False)[1] for _ in range(50)]
    assert not any(ANNOUNCE in f for f in out)
    # a real clear re-arms it
    out = [h.update(cs_sp(), False)[1] for _ in range(REARM_FRAMES)] + [h.update(cs_sp(stored=True), False)[1] for _ in range(50)]
    assert sum(ANNOUNCE in f for f in out) == ANNOUNCE_FRAMES

  def test_filter(self):
    types = ["accFaulted/immediateDisable", "accFaulted/noEntry", "accFaulted/permanent", "carNotReady/noEntry",
             "steerUnavailable/permanent", "steerUnavailable/noEntry", "steerUnavailable/immediateDisable",
             "doorOpen/noEntry", "driverDistracted2/warning"]
    alerts = [SimpleNamespace(alert_type=t) for t in types]
    h = VsaFaultAlert()
    h.update(cs_sp(live=True), True)
    assert [a.alert_type for a in h.filter_alerts(alerts)] == \
      ["carNotReady/noEntry", "steerUnavailable/immediateDisable", "doorOpen/noEntry", "driverDistracted2/warning"]
    h = VsaFaultAlert()
    h.update(cs_sp(stored=True), False)
    assert [a.alert_type for a in h.filter_alerts(alerts)] == \
      ["accFaulted/immediateDisable", "accFaulted/noEntry", "accFaulted/permanent", "steerUnavailable/immediateDisable",
       "doorOpen/noEntry", "driverDistracted2/warning"]


class TestEventClasses(OpenpilotTestCase):
  def test_live_carries_only_what_accfaulted_already_does(self):
    assert set(EVENTS_SP[LIVE]) == {ET.IMMEDIATE_DISABLE, ET.NO_ENTRY, ET.PERMANENT}
    assert set(EVENTS[EventName.accFaulted]) == {ET.IMMEDIATE_DISABLE, ET.NO_ENTRY, ET.PERMANENT}, \
      "upstream's accFaulted changed: re-check that vsaFault adds no disable it does not have"

  def test_stored_refuses_but_never_disables(self):
    assert set(EVENTS_SP[STORED]) == {ET.NO_ENTRY, ET.PERMANENT}
    assert set(EVENTS[EventName.carNotReady]) == {ET.NO_ENTRY}, "the carrier must stay NO_ENTRY only"
    assert set(EVENTS_SP[ANNOUNCE]) == {ET.PERMANENT}

  def test_upstream_accfaulted_is_unchanged(self):
    a = EVENTS[EventName.accFaulted][ET.IMMEDIATE_DISABLE]
    assert a.alert_text_2 == "Cruise Fault: Restart the Car" and a.priority == Priority.HIGHEST

  def test_sounds_and_priorities(self):
    imm = EVENTS_SP[LIVE][ET.IMMEDIATE_DISABLE]
    up = ImmediateDisableAlert("x")
    assert (imm.priority, imm.audible_alert, imm.duration, imm.alert_status) == \
      (up.priority, up.audible_alert, up.duration, up.alert_status), "the disable alert is upstream's, only the text differs"
    ann = EVENTS_SP[ANNOUNCE][ET.PERMANENT]
    assert ann.audible_alert == AudibleAlert.prompt, "prompt plays once; promptRepeat would loop"
    assert ann.priority < Priority.MID, "must never hide driver monitoring's stage 2"
    assert ann.duration < up.duration, "shorter than a disengagement alert, so a disengagement covers it entirely"
    live_banner = EVENTS_SP[LIVE][ET.PERMANENT]
    assert live_banner.audible_alert == AudibleAlert.none and live_banner.priority == Priority.LOWER
    stored_banner = made(STORED, ET.PERMANENT)
    assert stored_banner.audible_alert == AudibleAlert.none and stored_banner.priority == Priority.LOWER

  def test_units(self):
    assert "35 km/h" in made(STORED, ET.PERMANENT).alert_text_2
    assert "22 mph" in made(STORED, ET.PERMANENT, metric=False).alert_text_2
    t = made(STORED, ET.NO_ENTRY)
    assert "35 km/h" in t.alert_text_1 + t.alert_text_2

  def test_event_names_are_in_the_schema(self):
    names = custom.OnroadEventSP.EventName.schema.enumerants
    assert names["vsaFault"] == LIVE and names["vsaStoredFault"] == STORED and names["vsaFaultAnnounce"] == ANNOUNCE
    for e in (LIVE, STORED, ANNOUNCE):
      assert e in EVENTS_SP


def typed_alerts() -> list[tuple[str, Alert]]:
  out = []
  for e in (LIVE, STORED, ANNOUNCE):
    for et, a in EVENTS_SP[e].items():
      if isinstance(a, Alert):
        out.append((et, a))
      else:
        out += [(et, made(e, et, metric=True)), (et, made(e, et, metric=False))]
  return out


def all_alerts() -> list[Alert]:
  return [a for _, a in typed_alerts()]


class TestTexts(OpenpilotTestCase):
  def test_upstream_sanity_rules(self):
    for a in all_alerts():
      if a.alert_size == AlertSize.small:
        assert a.alert_text_1 and not a.alert_text_2, a
      elif a.alert_size == AlertSize.mid:
        assert a.alert_text_1 and a.alert_text_2, a
      assert a.creation_delay == 0.

  def test_only_glyphs_the_font_has(self):
    for a in all_alerts():
      for s in (a.alert_text_1, a.alert_text_2):
        assert all(32 <= ord(c) <= 126 for c in s), s

  def test_no_restart_advice(self):
    for a in all_alerts():
      assert "restart" not in (a.alert_text_1 + a.alert_text_2).lower(), "a restart does not clear a VSA fault"

  def test_texts_fit_the_mici_alert_renderer(self):
    """Wrap each text the way mici/onroad/alert_renderer.py sizes it, measured with the real font files."""
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

    # on mici a NoEntryAlert swaps its two texts (events_base.NoEntryAlert): the reason is text 1 there
    for et, a in typed_alerts():
      t1, t2 = (a.alert_text_2, a.alert_text_1) if et == ET.NO_ENTRY else (a.alert_text_1, a.alert_text_2)
      t1, t2 = t1.lower(), t2.lower()   # the renderer lowercases both
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


class TestStateMachines(OpenpilotTestCase):
  def test_live_disengages_exactly_as_before_and_names_the_vsa(self):
    f = Frame(enabled=True)
    assert not f.step(live=True, acc_faulted=True)
    assert State.disabled == f.sm.state and ET.IMMEDIATE_DISABLE in f.sm.current_alert_types
    assert f.shown[-1][:2] == ("TAKE CONTROL IMMEDIATELY", EVENTS_SP[LIVE][ET.IMMEDIATE_DISABLE].alert_text_2)
    # without the VSA flag the very same frame is upstream's, untouched
    g = Frame(enabled=True)
    assert not g.step(live=False, acc_faulted=True)
    assert g.shown[-1][:2] == ("TAKE CONTROL IMMEDIATELY", "Cruise Fault: Restart the Car")

  def test_live_onset_while_engaged_plays_one_sound(self):
    f = Frame(enabled=True)
    for _ in range(1000):
      f.step(live=True, acc_faulted=True)
    assert sounds(f.shown) == [AudibleAlert.warningImmediate], sounds(f.shown)
    banner = EVENTS_SP[LIVE][ET.PERMANENT]
    assert f.shown[-1][:2] == (banner.alert_text_1, banner.alert_text_2)
    assert not any(s[0] == EVENTS_SP[ANNOUNCE][ET.PERMANENT].alert_text_1 for s in f.shown), \
      "the announcement surfaced after the disengagement alert"

  def test_live_onset_while_not_engaged_prompts_once(self):
    f = Frame(enabled=False)
    for _ in range(1000):
      f.step(live=True, acc_faulted=True)
    assert sounds(f.shown) == [AudibleAlert.prompt]
    assert f.shown[0][0] == EVENTS_SP[ANNOUNCE][ET.PERMANENT].alert_text_1

  def test_the_eps_escalation_does_not_take_the_screen(self):
    """Routes 110 and 112: 30 s after the VSA, steerUnavailable's 'LKAS Fault: Restart the car' banner took over."""
    f = Frame(enabled=False)
    for _ in range(500):
      f.step(live=True, acc_faulted=True)
    for _ in range(500):
      f.step(live=True, acc_faulted=True, extra=(EventName.steerUnavailable,))
    banner = EVENTS_SP[LIVE][ET.PERMANENT]
    assert f.shown[-1][:2] == (banner.alert_text_1, banner.alert_text_2)

  def test_stored_refuses_openpilot_with_the_vsa_text(self):
    f = Frame(enabled=False)
    for _ in range(600):
      f.step(stored=True)
    assert not f.step(stored=True, extra=(EventName.buttonEnable,))
    assert f.sm.state == State.disabled and ET.NO_ENTRY in f.sm.current_alert_types
    text = made(STORED, ET.NO_ENTRY)
    assert f.shown[-1][:2] == (text.alert_text_1, text.alert_text_2), f.shown[-1]
    assert "Car Not Ready" not in f.shown[-1][0] + f.shown[-1][1]

  def test_stored_clears_and_openpilot_engages(self):
    f = Frame(enabled=False)
    for _ in range(600):
      f.step(stored=True)
    f.step()
    assert f.step(extra=(EventName.buttonEnable,)), "engagement is refused for no longer than the fault is stored"

  def test_stored_does_not_disengage(self):
    f = Frame(enabled=True)
    for _ in range(300):
      assert f.step(stored=True)
    assert f.sm.state == State.enabled

  def test_stored_prompts_once_then_stays_silent(self):
    f = Frame(enabled=False)
    for _ in range(3000):
      f.step(stored=True)
    assert sounds(f.shown) == [AudibleAlert.prompt]
    banner = made(STORED, ET.PERMANENT)
    assert f.shown[-1][:2] == (banner.alert_text_1, banner.alert_text_2)
    announce = [i for i, s in enumerate(f.shown) if s[0] == EVENTS_SP[ANNOUNCE][ET.PERMANENT].alert_text_1]
    assert announce[0] == 0 and len(announce) == int(EVENTS_SP[ANNOUNCE][ET.PERMANENT].duration) + 1

  def test_driver_monitoring_keeps_the_screen(self):
    """The announcement is LOW: driver monitoring's stage 2 (MID) is never hidden by it."""
    f = Frame(enabled=True)
    for _ in range(600):
      f.step(stored=True, extra=(EventName.driverDistracted2,))
    assert {s[0] for s in f.shown} == {"Pay Attention"}, {s[0] for s in f.shown}


class TestMads(OpenpilotTestCase):
  """MADS is refused too while the fault is stored (the decision in vsa_fault_alert.py), never disabled by it."""

  def make(self, mocker, state):
    from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem
    # upstream's sunnypilot/mads/tests/test_mads_state_machine.py overwrites EVENTS_SP[0] - which is lkasEnable -
    # with whatever event types its case needs, and never puts it back; in a full run a worker that ran it first
    # hands this class a LKAS button that is no longer an ENABLE. Pin the real entry for the duration of the test.
    pin = mock.patch.dict(EVENTS_SP, {EventNameSP.lkasEnable: {ET.ENABLE: EngagementAlert(AudibleAlert.engage)}})
    pin.start()
    self.addCleanup(pin.stop)
    sd = mocker.MagicMock()
    sd.CP = structs.CarParams()
    sd.CP.brand = "honda"
    sd.CP_SP = structs.CarParamsSP()
    values = {"Mads": True, "MadsEmergencySteerDisable": True, "MadsSteeringMode": 0, "MadsEmergencySteerRate": 200}
    sd.params = mocker.MagicMock()
    sd.params.get_bool = mocker.MagicMock(side_effect=lambda k, *a, **kw: bool(values.get(k, False)))
    sd.params.get = mocker.MagicMock(side_effect=lambda k, *a, **kw: values.get(k))
    sd.events = Events()
    sd.events_sp = EventsSP()
    sd.enabled = False
    sd.enabled_prev = False
    sd.initialized = True
    sd.state_machine = StateMachine()
    cs = structs.CarState()
    cs.vEgo = 10.0
    cs.cruiseState.available = True
    sd.CS_prev = cs
    sd.sm = {'pandaStates': [], 'carStateSP': SimpleNamespace(linbusGateway=None)}
    mads = ModularAssistiveDrivingSystem(sd)
    mads.enabled_toggle = True
    mads.state_machine.state = state
    mads.enabled = state != MadsState.disabled
    return mads, sd, cs

  def run_frame(self, mads, sd, cs, stored: bool, press_lkas: bool):
    sd.events.clear()
    sd.events_sp.clear()
    h = VsaFaultAlert()
    ev, ev_sp = h.update(cs_sp(stored=stored), False)
    for e in ev:
      sd.events.add(e)
    for e in ev_sp:
      sd.events_sp.add(e)
    cs.buttonEvents = [structs.CarState.ButtonEvent(type=structs.CarState.ButtonEvent.Type.lkas, pressed=True)] if press_lkas else []
    sd.state_machine.current_alert_types = [ET.PERMANENT]
    mads.update(cs)
    return mads.state_machine.state

  def test_lkas_press_is_refused_while_stored(self, mocker):
    mads, sd, cs = self.make(mocker, MadsState.disabled)
    assert self.run_frame(mads, sd, cs, stored=True, press_lkas=True) == MadsState.disabled
    assert ET.NO_ENTRY in sd.state_machine.current_alert_types

  def test_lkas_press_engages_without_it(self, mocker):
    mads, sd, cs = self.make(mocker, MadsState.disabled)
    assert self.run_frame(mads, sd, cs, stored=False, press_lkas=True) == MadsState.enabled

  def test_an_enabled_mads_is_not_disabled_by_it(self, mocker):
    mads, sd, cs = self.make(mocker, MadsState.enabled)
    for _ in range(100):
      assert self.run_frame(mads, sd, cs, stored=True, press_lkas=False) == MadsState.enabled


class TestCarStateSPAgreement(OpenpilotTestCase):
  """card splats structs.CarStateSP into custom.CarStateSP.new_message(**dict): a name on one side only raises
  on a drive. The same check test_capnp_and_dataclass_agree makes for LinbusGateway, for the top level."""

  def test_names_match(self):
    capnp_src = CUSTOM_CAPNP.read_text()
    block = capnp_src[capnp_src.index("struct CarStateSP "):]
    block = block[:block.index("\n  struct LinbusGateway")]
    capnp_fields = {m.group(1): (int(m.group(2)), m.group(3)) for m in re.finditer(r"^  (\w+) @(\d+) :(\w+);", block, re.M)}

    src = STRUCTS.read_text()
    sblock = src[src.index("class CarStateSP:"):]
    sblock = sblock[:sblock.index("\n  @auto_dataclass")]
    struct_fields = re.findall(r"^  (\w+): [\w'.]+ = ", sblock, re.M)

    assert set(capnp_fields) == set(struct_fields), (sorted(capnp_fields), sorted(struct_fields))
    assert capnp_fields["vsaFault"][1] == "Bool" and capnp_fields["vsaStoredFault"][1] == "Bool"
    ordinals = [o for o, _ in capnp_fields.values()]
    assert len(ordinals) == len(set(ordinals)) and sorted(ordinals) == list(range(len(ordinals))), ordinals

  def test_round_trip(self):
    from openpilot.selfdrive.car.helpers import convert_to_capnp
    for live, stored in ((True, False), (False, True), (True, True), (False, False)):
      c = structs.CarStateSP()
      c.vsaFault, c.vsaStoredFault = live, stored
      r = convert_to_capnp(c).as_reader()
      assert (r.vsaFault, r.vsaStoredFault) == (live, stored)


class TestSelfdrivedWiring(OpenpilotTestCase):
  def test_wired_in_update_events_and_update_alerts(self):
    src = SELFDRIVED.read_text(encoding="utf-8")
    assert "self.vsa_fault_alert = VsaFaultAlert()" in src
    call = re.search(r"self\.vsa_fault_alert\.update\(self\.sm\['carStateSP'\], self\.events\.has\(EventName\.accFaulted\)\)", src)
    assert call, "the helper is not fed carStateSP and whether accFaulted is raised"
    # after the car events (accFaulted must already be in events) and after the dashcam early return
    assert src.index("car_events = self.car_events.update(") < call.start()
    assert src.index("if self.CP.passive:\n      return") < call.start()
    # before selfdrived's own state machine reads events
    assert call.start() < src.index("def step(")
    # the filter sits between creating upstream's alerts and handing them to the AlertManager
    flt = src.index("self.vsa_fault_alert.filter_alerts(")
    assert src.index("alerts = self.events.create_alerts(") < flt < src.index("self.AM.add_many(")
