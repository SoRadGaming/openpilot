"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): selfdrived while the car is stock on purpose (car_stock_alert.py; KTuner, 2026-10-19).

What it must do:
  * a diagnostic tool on the car bus: a silent note; an engage press is refused with "Diagnostic Tool Connected";
    a MADS paused in P stays paused and is never knocked to disabled with "TAKE CONTROL IMMEDIATELY" - not on the
    entry edge (the panda reports noOutput before selfdrived has read the reason), not on the release edge (the
    reason has cleared, pandad has not switched back), not through a 60 s start-up hold in ELM327;
  * the car bus went bus-off: one chime, then a silent "Car Bus Error" banner until restart; "CAN Bus Disconnected:
    Likely Faulty Cable" never on screen; anything engaged is disengaged by upstream's own controlsMismatch with ONE
    warningImmediate, and the text names the bus error - also when the reason reaches selfdrived after the
    disengagement;
  * every other controlsMismatch is upstream's, untouched: a non-car mode with no reason, a wrong param, rx checks,
    the mismatch counter, anything engaged; and nothing at all on any other car;
  * FCW / stock AEB and a newer equal-priority alert still take the screen.
Each frame is selfdrived.step() in miniature: the pandaStates mismatch check, this helper, selfdrived's state machine,
MADS (the real class), then create_alerts -> adjust_alerts -> AlertManager.
"""
import copy
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openpilot.cereal import log, custom
from opendbc.car import structs
from opendbc.car.honda.values import CAR
from opendbc.car.structs import car
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events, EVENTS
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, EVENTS_SP, ET, Alert, Priority, AlertSize, \
  AudibleAlert, ImmediateDisableAlert, EngagementAlert
from openpilot.sunnypilot.selfdrive.selfdrived.car_stock_alert import CarStockAlert, ENTRY_GRACE, EXIT_GRACE, \
  ANNOUNCE_FRAMES, DIAG_REPLACES, BUS_OFF_REPLACES, DISENGAGE_REPLACES

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName
State = log.SelfdriveState.OpenpilotState
MadsState = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState
SafetyModel = car.CarParams.SafetyModel
GearShifter = car.CarState.GearShifter
ButtonType = structs.CarState.ButtonEvent.Type

DIAG = EventNameSP.diagnosticToolStock
BUS_OFF = EventNameSP.carBusOffStock
ANNOUNCE = EventNameSP.carBusOffAnnounce
DISENGAGE = EventNameSP.carBusOffDisengage
OURS = (DIAG, BUS_OFF, ANNOUNCE, DISENGAGE)

ROOT = Path(__file__).parents[4]   # openpilot/
SELFDRIVED = ROOT / "selfdrive/selfdrived/selfdrived.py"
CUSTOM_CAPNP = ROOT / "cereal/custom.capnp"
ALERT_RENDERER = ROOT / "selfdrive/ui/mici/onroad/alert_renderer.py"
FONTS = ROOT / "selfdrive/assets/fonts"

STANDDOWN_PARAM = 36   # hondaNidec | NIDEC_ALT | ELESYS_SCM_STANDDOWN, what this car runs in
CAR_MODE = (SafetyModel.hondaNidec, STANDDOWN_PARAM)
NO_OUTPUT = (SafetyModel.noOutput, 0)
ELM327 = (SafetyModel.elm327, 1)
TEN_S = int(10. / DT_CTRL)

DIAG_TEXT = (EVENTS_SP[DIAG][ET.PERMANENT].alert_text_1, EVENTS_SP[DIAG][ET.PERMANENT].alert_text_2)
BUS_OFF_TEXT = (EVENTS_SP[BUS_OFF][ET.PERMANENT].alert_text_1, EVENTS_SP[BUS_OFF][ET.PERMANENT].alert_text_2)
DISENGAGE_TEXT = ("TAKE CONTROL IMMEDIATELY", EVENTS_SP[DISENGAGE][ET.IMMEDIATE_DISABLE].alert_text_2)


def make_cp(fingerprint=CAR.HONDA_ACCORD_9G_AU, passive=False):
  cp = structs.CarParams()
  cp.brand = "honda"
  cp.carFingerprint = fingerprint
  cp.passive = passive
  cfg = structs.CarParams.SafetyConfig()
  cfg.safetyModel = SafetyModel.hondaNidec
  cfg.safetyParam = STANDDOWN_PARAM
  cp.safetyConfigs = [cfg]
  return cp


def panda(mode=CAR_MODE, rx_invalid=False):
  return SimpleNamespace(safetyModel=mode[0], safetyParam=mode[1], alternativeExperience=0, safetyRxChecksInvalid=rx_invalid,
                         controlsAllowed=mode == CAR_MODE, controlsAllowedLateral=mode == CAR_MODE, faults=[])


def car_state(gear=GearShifter.park, v_ego=0.0, lkas=False):
  cs = car.CarState.new_message()
  cs.vEgo = v_ego
  cs.standstill = v_ego < 0.001
  cs.gearShifter = gear
  cs.canValid = True
  cs.cruiseState.available = True
  if lkas:
    cs.init('buttonEvents', 1)
    cs.buttonEvents[0].type = ButtonType.lkas
    cs.buttonEvents[0].pressed = True
  return cs.as_reader()


class FakeParams:
  def __init__(self, **kw):
    self.values = dict(kw)

  def get_bool(self, k, *a, **kw):
    return bool(self.values.get(k, False))


class Drive:
  """selfdrived.step() in miniature. reasons: what pandad/card have written; selfdrived's params_thread reads them on
  every 10th frame starting at read_phase, so the 10 Hz skew between the writers and this reader is in the test."""
  def __init__(self, cp=None, enabled=False, mads_state=MadsState.disabled, start_frame=TEN_S + 100, read_phase=0):
    from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem
    self.CP = cp or make_cp()
    self.helper = CarStockAlert(self.CP)
    self.params = FakeParams()
    sd = mock.MagicMock()
    sd.CP, sd.CP_SP = self.CP, structs.CarParamsSP()
    values = {"Mads": True, "MadsMainCruiseAllowed": False, "MadsUnifiedEngagementMode": True, "MadsSteeringMode": 0,
              "MadsEmergencySteerDisable": True, "MadsEmergencySteerRate": 200}
    sd.params = mock.MagicMock()
    sd.params.get_bool = mock.MagicMock(side_effect=lambda k, *a, **kw: bool(values.get(k, False)))
    sd.params.get = mock.MagicMock(side_effect=lambda k, *a, **kw: values.get(k))
    sd.events = Events()
    sd.events_sp = EventsSP()
    sd.state_machine = StateMachine()
    sd.state_machine.state = State.enabled if enabled else State.disabled
    sd.enabled = sd.enabled_prev = enabled
    sd.active = enabled
    sd.initialized = True
    sd.CS_prev = car_state()
    sd.sm = {'pandaStates': [panda()], 'carStateSP': SimpleNamespace(linbusGateway=None)}
    self.sd = sd
    self.mads = ModularAssistiveDrivingSystem(sd)
    self.mads.enabled_toggle = True
    self.mads.state_machine.state = mads_state
    self.mads.enabled = mads_state != MadsState.disabled
    self.mads.active = mads_state in (MadsState.enabled, MadsState.softDisabling, MadsState.overriding)
    if enabled:
      self.mads.state_machine.state = MadsState.enabled
      self.mads.enabled = self.mads.active = True
    self.am = AlertManager()
    self.frame = start_frame
    self.read_phase = read_phase
    self.mismatch_counter = 0
    self.shown: list[tuple[str, str, int]] = []
    self.types: list[str] = []
    self.events_seen: list[set] = []
    self.engageable: list[bool] = []

  def step(self, mode=CAR_MODE, n=1, rx_invalid=False, gear=GearShifter.park, v_ego=0.0, lkas=False, extra=(), extra_sp=(),
           mismatch_counter=0):
    sd = self.sd
    for _ in range(n):
      if self.frame % 10 == self.read_phase % 10:
        self.helper.read_params(self.params)
      sd.events.clear()
      sd.events_sp.clear()
      cs = car_state(gear, v_ego, lkas)
      if gear == GearShifter.park:
        sd.events.add(EventName.wrongGear)   # the car events raise it in P; MADS turns it into its silent pause
      for e in extra:
        sd.events.add(e)
      for e in extra_sp:
        sd.events_sp.add(e)
      ps = [panda(mode, rx_invalid)]
      sd.sm['pandaStates'] = ps
      # selfdrived.update_events(): the pandaStates loop, as upstream has it
      for i, p in enumerate(ps):
        cfg = self.CP.safetyConfigs[i]
        safety_mismatch = p.safetyModel != cfg.safetyModel or p.safetyParam != cfg.safetyParam or \
          p.alternativeExperience != self.CP.alternativeExperience
        if (safety_mismatch and self.frame * DT_CTRL > 10.) or p.safetyRxChecksInvalid or mismatch_counter >= 200:
          sd.events.add(EventName.controlsMismatch)
      # FORK hook, right after that loop
      for e in self.helper.update(sd.events, ps, mismatch_counter, sd.enabled or self.mads.active):
        sd.events_sp.add(e)
      # selfdrived.step(): its state machine, then MADS, then update_alerts()
      sd.enabled, sd.active = sd.state_machine.update(sd.events)
      self.mads.update(cs)
      clear = set()
      if ET.WARNING not in sd.state_machine.current_alert_types:
        clear.add(ET.WARNING)
      if sd.enabled:
        clear.add(ET.NO_ENTRY)
      args = [self.CP, cs, {}, True, sd.state_machine.soft_disable_timer, log.LongitudinalPersonality.standard]
      alerts = sd.events.create_alerts(sd.state_machine.current_alert_types, args)
      alerts_sp = sd.events_sp.create_alerts(sd.state_machine.current_alert_types, args)
      alerts, alerts_sp = self.helper.adjust_alerts(alerts, alerts_sp, self.am, self.frame, args)
      self.am.add_many(self.frame, alerts + alerts_sp)
      self.am.process_alerts(self.frame, clear)
      a = self.am.current_alert
      self.shown.append((a.alert_text_1, a.alert_text_2, a.audible_alert))
      self.types.append(a.alert_type)
      self.events_seen.append(set(sd.events.names) | {("sp", e) for e in sd.events_sp.names})
      # selfdriveState.engageable, leaving out P's own "Gear not D" (wrongGear), which the car raises in P anyway
      self.engageable.append(not any(ET.NO_ENTRY in EVENTS.get(e, {}) for e in sd.events.names if e != EventName.wrongGear)
                             and not any(ET.NO_ENTRY in EVENTS_SP.get(e, {}) for e in sd.events_sp.names
                                         if e != EventNameSP.silentWrongGear))
      sd.CS_prev = cs
      self.frame += 1
    return self

  @property
  def mads_state(self):
    return self.mads.state_machine.state


def sounds(shown) -> list[int]:
  """The sounds soundd would start: each change of the current alert's sound to something audible."""
  out, prev = [], AudibleAlert.none
  for _, _, s in shown:
    if s != prev and s != AudibleAlert.none:
      out.append(s)
    prev = s
  return out


def critical(d: Drive) -> list[str]:
  return [t for t in d.types if t.endswith("/immediateDisable")]


class MadsPin(OpenpilotTestCase):
  def setUp(self):
    # upstream's mads/tests/test_mads_state_machine.py overwrites EVENTS_SP[lkasEnable] and never puts it back
    # (see test_vsa_fault_alert.TestMads); pin the real entry for these tests
    pin = mock.patch.dict(EVENTS_SP, {EventNameSP.lkasEnable: {ET.ENABLE: EngagementAlert(AudibleAlert.engage)}})
    pin.start()
    self.addCleanup(pin.stop)


class TestTheTrapIsReal(MadsPin):
  def test_upstream_knocks_a_paused_mads_to_disabled_with_a_critical_alert(self):
    """The same frames on a car this is not armed for: the mismatch disables a MADS paused in P, loudly."""
    d = Drive(cp=make_cp(fingerprint=CAR.HONDA_CIVIC), mads_state=MadsState.paused)
    d.step(n=50).step(mode=NO_OUTPUT, n=50)
    assert d.mads_state == MadsState.disabled
    assert "controlsMismatch/immediateDisable" in d.types and AudibleAlert.warningImmediate in sounds(d.shown)


class TestDiagnosticTool(MadsPin):
  def test_entry_with_the_panda_ahead_of_the_param(self):
    for lead in (1, 5, 10, 20, 40):
      for phase in (0, 3, 7):
        d = Drive(mads_state=MadsState.paused, read_phase=phase)
        d.step(n=30)
        d.step(mode=NO_OUTPUT, n=lead)          # pandad already left the car mode: card wrote the param, we have not read it
        d.params.values["DiagToolStock"] = True
        d.step(mode=NO_OUTPUT, n=300)
        assert critical(d) == [] and sounds(d.shown) == [], (lead, phase, set(d.types))
        assert d.mads_state == MadsState.paused, (lead, phase)
        assert d.shown[-1][:2] == DIAG_TEXT, d.shown[-1]
        assert EventName.controlsMismatch not in d.events_seen[-1] and EventName.carNotReady in d.events_seen[-1]
        assert not d.engageable[-1]

  def test_an_engage_press_is_refused_with_our_text(self):
    """Stopped in D with the tool still talking (it was entered in P): the MADS button and openpilot's own engagement
    are both refused, with our words. (In P the car's own "Gear not D" refusal ties with ours - also a refusal.)"""
    kw = {"gear": GearShifter.drive}
    ne = EVENTS_SP[DIAG][ET.NO_ENTRY]
    d = Drive()
    d.params.values["DiagToolStock"] = True
    d.step(mode=NO_OUTPUT, n=100, **kw)
    d.step(mode=NO_OUTPUT, lkas=True, **kw)
    assert d.mads_state == MadsState.disabled
    assert d.shown[-1] == (ne.alert_text_1, ne.alert_text_2, AudibleAlert.refuse), d.shown[-1]
    # openpilot's own engagement too (selfdrived's state machine reads only `events`: carNotReady refuses it)
    d.step(mode=NO_OUTPUT, n=400, **kw)
    d.step(mode=NO_OUTPUT, extra=(EventName.buttonEnable,), **kw)
    assert not d.sd.enabled and d.shown[-1][:2] == (ne.alert_text_1, ne.alert_text_2), d.shown[-1]
    assert not any("Car Not Ready" in s[0] + s[1] or "Controls Mismatch" in s[0] + s[1] for s in d.shown)

  def test_a_paused_mads_cannot_resume(self):
    """Out of P (the car's own "Gear not D" was what kept it paused) MADS would resume by itself (silentLkasEnable):
    our NO_ENTRY keeps it paused, with our words. Without the reason the same frames resume it."""
    for reason in (True, False):
      d = Drive(mads_state=MadsState.paused)
      d.params.values["DiagToolStock"] = reason
      d.step(mode=NO_OUTPUT if reason else CAR_MODE, n=100)
      d.step(mode=NO_OUTPUT if reason else CAR_MODE, n=50, gear=GearShifter.drive)
      if reason:
        ne = EVENTS_SP[DIAG][ET.NO_ENTRY]
        assert d.mads_state == MadsState.paused and d.shown[-1][:2] == (ne.alert_text_1, ne.alert_text_2), d.shown[-1]
        assert critical(d) == []
      else:
        assert d.mads_state == MadsState.enabled

  def test_release_with_the_panda_still_stock_for_a_while(self):
    for lag in (1, 10, 30, 100, 150):
      d = Drive(mads_state=MadsState.paused)
      d.params.values["DiagToolStock"] = True
      d.step(mode=NO_OUTPUT, n=200)
      d.params.values["DiagToolStock"] = False   # the tool went quiet: card cleared it, pandad is slower
      d.step(mode=NO_OUTPUT, n=lag)
      d.step(n=300)
      assert critical(d) == [] and sounds(d.shown) == [], (lag, set(d.types))
      assert d.mads_state == MadsState.paused
      assert d.shown[-1][:2] == ("", ""), d.shown[-1]
      assert EventName.carNotReady not in d.events_seen[-1] and d.engageable[-1], "openpilot available again"

  def test_a_start_up_hold_of_a_minute(self):
    """KTuner at key-on: the panda stays in its start-up ELM327 for the whole hold; selfdrived turns 10 s old in it."""
    d = Drive(start_frame=0)
    d.params.values["DiagToolStock"] = True
    d.step(mode=ELM327, n=6000)
    assert critical(d) == [] and sounds(d.shown) == []
    assert not any(EventName.controlsMismatch in e for e in d.events_seen)
    assert all(EventName.carNotReady in e for e in d.events_seen[1:])
    assert d.shown[-1][:2] == DIAG_TEXT and not any(d.engageable[1:])
    # the release is the normal start: the car mode, nothing on screen, engageable
    d.params.values["DiagToolStock"] = False
    d.step(mode=ELM327, n=20).step(n=300)
    assert critical(d) == [] and d.shown[-1][:2] == ("", "") and d.engageable[-1]

  def test_a_slow_normal_start_is_upstreams(self):
    """No reason: an ELM327 past selfdrived's 10 s is upstream's controlsMismatch, as before."""
    d = Drive(start_frame=0)
    d.step(mode=ELM327, n=TEN_S + 50)
    assert EventName.controlsMismatch in d.events_seen[-1] and EventName.carNotReady not in d.events_seen[-1]
    assert not d.helper.masked

  def test_the_note_sits_above_the_engine_off_lkas_fault(self):
    """144: the EPS with the engine off raises steerUnavailable ("LKAS Fault: Restart the car to engage", LOWER)."""
    d = Drive()
    d.params.values["DiagToolStock"] = True
    d.step(mode=NO_OUTPUT, n=100)
    d.step(mode=NO_OUTPUT, n=200, extra=(EventName.steerUnavailable,))
    assert d.shown[-1][:2] == DIAG_TEXT


class TestGenuineMismatchesAreUpstreams(MadsPin):
  def assert_upstream(self, d: Drive, label):
    assert EventName.controlsMismatch in d.events_seen[-1], label
    assert not d.helper.masked, label

  def test_no_output_without_a_reason(self):
    d = Drive()
    d.step(mode=NO_OUTPUT, n=ENTRY_GRACE)
    assert d.helper.masked, "the entry grace"
    d.step(mode=NO_OUTPUT, n=5)
    self.assert_upstream(d, "past the entry grace")

  def test_the_wrong_param_or_mode(self):
    for mode in ((SafetyModel.hondaNidec, 4), (SafetyModel.hondaNidec, 68), (SafetyModel.allOutput, 0), (SafetyModel.silent, 0)):
      d = Drive()
      d.params.values["DiagToolStock"] = True
      d.step(mode=mode, n=100)
      self.assert_upstream(d, mode)

  def test_rx_checks_and_the_counter(self):
    for kw in ({"rx_invalid": True}, {"mismatch_counter": 200}):
      d = Drive()
      d.params.values["DiagToolStock"] = True
      d.step(mode=NO_OUTPUT, n=100, **kw)
      self.assert_upstream(d, kw)

  def test_anything_engaged(self):
    d = Drive(enabled=True)
    d.params.values["DiagToolStock"] = True
    d.step(n=20).step(mode=NO_OUTPUT)
    assert EventName.controlsMismatch in d.events_seen[-1] and not d.sd.enabled, "disengaged by upstream's event"
    d = Drive(mads_state=MadsState.enabled, enabled=False)
    d.params.values["DiagToolStock"] = True
    d.step(n=20, gear=GearShifter.drive, v_ego=10.).step(mode=NO_OUTPUT, gear=GearShifter.drive, v_ego=10.)
    assert EventName.controlsMismatch in d.events_seen[-1] and d.mads_state == MadsState.disabled

  def test_other_cars_and_dashcam_are_never_touched(self):
    for cp in (make_cp(fingerprint=CAR.HONDA_CIVIC), make_cp(passive=True)):
      d = Drive(cp=cp)
      d.params.values.update(DiagToolStock=True, CarBusOffStock=True)
      d.step(mode=NO_OUTPUT, n=200)
      assert not d.helper.armed and EventName.controlsMismatch in d.events_seen[-1]
      assert not any(("sp", e) in s for s in d.events_seen for e in OURS)
      assert EventName.carNotReady not in d.events_seen[-1]


class TestBusOff(MadsPin):
  def test_parked_nothing_engaged(self):
    for phase in (0, 4, 9):
      d = Drive(read_phase=phase)
      d.step(n=20)
      d.params.values["CarBusOffStock"] = True   # pandad latched and forced NO_OUTPUT in the same poll
      d.step(mode=NO_OUTPUT, n=3, extra=(EventName.canBusMissing,))
      d.step(mode=NO_OUTPUT, n=200, extra=(EventName.canBusMissing, EventName.canError))   # CAN still recovering
      d.step(mode=NO_OUTPUT, n=800, extra=(EventName.steerUnavailable,))
      assert sounds(d.shown) == [AudibleAlert.prompt], (phase, sounds(d.shown))
      assert critical(d) == []
      assert d.shown[-1][:2] == BUS_OFF_TEXT
      assert not any("Faulty Cable" in s[0] + s[1] or "Unknown Vehicle" in s[0] + s[1] or "LKAS Fault" in s[0] + s[1]
                     for s in d.shown), {s[:2] for s in d.shown}
      ann = EVENTS_SP[ANNOUNCE][ET.PERMANENT]
      assert sum(1 for t in d.types if t == "carBusOffAnnounce/permanent") == int(ann.duration) + 1
      assert not d.engageable[-1]

  def disengage(self, enabled: bool, mads: bool, late: int):
    d = Drive(enabled=enabled, mads_state=MadsState.enabled if mads else MadsState.disabled)
    kw = {"gear": GearShifter.drive, "v_ego": 25.}
    d.step(n=50, **kw)
    assert d.sd.enabled == enabled and d.mads.active
    if late == 0:
      d.params.values["CarBusOffStock"] = True
    d.step(mode=NO_OUTPUT, n=max(late, 1), extra=(EventName.canBusMissing,), **kw)
    d.params.values["CarBusOffStock"] = True
    d.step(mode=NO_OUTPUT, n=60, extra=(EventName.canBusMissing,), **kw)
    d.step(mode=NO_OUTPUT, n=600, **kw)
    assert not d.sd.enabled and d.mads_state == MadsState.disabled
    assert sounds(d.shown) == [AudibleAlert.warningImmediate], (enabled, mads, late, sounds(d.shown))
    first = next(i for i, t in enumerate(d.types) if t.endswith("/immediateDisable"))
    within = int(4. / DT_CTRL)
    ours = [i for i in range(first, first + within) if d.shown[i][:2] == DISENGAGE_TEXT]
    assert ours and ours[0] <= first + late + 11, (enabled, mads, late, first, ours[:1])
    assert all(d.shown[i][:2] == DISENGAGE_TEXT for i in range(ours[0], first + within - 1)), "ours, to the end"
    assert d.shown[-1][:2] == BUS_OFF_TEXT
    assert not any("Faulty Cable" in s[0] + s[1] for s in d.shown)
    return d

  def test_engaged_openpilot_disengages_once_and_says_why(self):
    for late in (0, 1, 5, 10):
      self.disengage(enabled=True, mads=True, late=late)

  def test_mads_only(self):
    for late in (0, 1, 5, 10):
      self.disengage(enabled=False, mads=True, late=late)

  def test_mads_paused_in_p(self):
    d = Drive(mads_state=MadsState.paused)
    d.step(n=20)
    d.params.values["CarBusOffStock"] = True
    d.step(mode=NO_OUTPUT, n=500)
    assert critical(d) == [] and sounds(d.shown) == [AudibleAlert.prompt]
    assert d.mads_state == MadsState.paused
    # out of P it would resume by itself: it cannot, and says why
    d.step(mode=NO_OUTPUT, n=50, gear=GearShifter.drive)
    ne = EVENTS_SP[BUS_OFF][ET.NO_ENTRY]
    assert d.mads_state == MadsState.paused and d.shown[-1][:2] == (ne.alert_text_1, ne.alert_text_2), d.shown[-1]
    assert critical(d) == []

  def test_announced_once_per_session(self):
    h = CarStockAlert(make_cp())
    h.bus_off = True
    out = [h.update(Events(), [panda(NO_OUTPUT)], 0, False) for _ in range(3000)]
    assert sum(ANNOUNCE in f for f in out) == ANNOUNCE_FRAMES and ANNOUNCE in out[0]
    assert all(BUS_OFF in f and DISENGAGE not in f for f in out)
    assert DISENGAGE in h.update(Events(), [panda(NO_OUTPUT)], 0, True), "the disengagement text only while engaged"


class TestPrecedenceAndPriorities(MadsPin):
  def test_bus_off_wins(self):
    d = Drive()
    d.params.values.update(DiagToolStock=True, CarBusOffStock=True)
    d.step(mode=NO_OUTPUT, n=600)
    assert d.shown[-1][:2] == BUS_OFF_TEXT
    assert not any(s[:2] == DIAG_TEXT for s in d.shown)
    assert not any(("sp", DIAG) in e for e in d.events_seen)

  def test_fcw_and_stock_aeb_take_the_screen(self):
    for reason in ("DiagToolStock", "CarBusOffStock"):
      for e in (EventName.fcw, EventName.stockAeb):
        d = Drive()
        d.params.values[reason] = True
        d.step(mode=NO_OUTPUT, n=600)
        d.step(mode=NO_OUTPUT, n=5, extra=(e,))
        assert d.shown[-1][0] == "BRAKE!", (reason, e, d.shown[-1])

  def test_a_newer_equal_priority_alert_shows_over_the_note(self):
    """The VSA's announcement (LOW, 3.5 s) is newer than the note (LOW): it shows for its time, then the note again."""
    d = Drive()
    d.params.values["DiagToolStock"] = True
    d.step(mode=NO_OUTPUT, n=300)
    d.step(mode=NO_OUTPUT, n=10, extra_sp=(EventNameSP.vsaFaultAnnounce,))
    vsa = EVENTS_SP[EventNameSP.vsaFaultAnnounce][ET.PERMANENT]
    assert d.shown[-1][:2] == (vsa.alert_text_1, vsa.alert_text_2)
    d.step(mode=NO_OUTPUT, n=400)
    assert d.shown[-1][:2] == DIAG_TEXT

  def test_priorities(self):
    assert EVENTS_SP[DIAG][ET.PERMANENT].priority == Priority.LOW > EVENTS[EventName.steerUnavailable][ET.PERMANENT].priority
    assert EVENTS_SP[BUS_OFF][ET.PERMANENT].priority == Priority.LOWER
    assert EVENTS_SP[ANNOUNCE][ET.PERMANENT].priority == Priority.LOW
    for e in OURS:
      for a in EVENTS_SP[e].values():
        assert a.priority < Priority.MID or a.alert_text_1 == "TAKE CONTROL IMMEDIATELY", (e, a)


class TestHelper(OpenpilotTestCase):
  def test_filters(self):
    types = ["carNotReady/noEntry", "controlsMismatch/noEntry", "controlsMismatch/immediateDisable",
             "canBusMissing/permanent", "canBusMissing/noEntry", "canBusMissing/immediateDisable", "canError/permanent",
             "canError/noEntry", "canError/immediateDisable", "steerUnavailable/permanent", "steerUnavailable/noEntry",
             "steerUnavailable/immediateDisable", "accFaulted/immediateDisable", "fcw/permanent", "doorOpen/noEntry"]
    alerts = [SimpleNamespace(alert_type=t) for t in types]
    h = CarStockAlert(make_cp())
    assert h.filter_alerts(alerts, []) == alerts, "nothing without a reason"
    h.diag = True
    assert [a.alert_type for a in h.filter_alerts(alerts, [])] == [t for t in types if t not in DIAG_REPLACES]
    h.bus_off = True
    kept = [a.alert_type for a in h.filter_alerts(alerts, [])]
    assert kept == [t for t in types if t not in BUS_OFF_REPLACES]
    assert {"steerUnavailable/immediateDisable", "accFaulted/immediateDisable", "fcw/permanent",
            "controlsMismatch/immediateDisable"} <= set(kept)
    ours = [SimpleNamespace(alert_type="carBusOffDisengage/immediateDisable")]
    kept = [a.alert_type for a in h.filter_alerts(alerts, ours)]
    assert not set(DISENGAGE_REPLACES) & set(kept) and "steerUnavailable/immediateDisable" in kept

  def test_late_alerts(self):
    am = AlertManager()
    h = CarStockAlert(make_cp())
    h.bus_off = True
    assert h.late_alerts(am, 1, [], [None] * 6) == [], "nothing of upstream's is up"
    up = copy.copy(EVENTS[EventName.controlsMismatch][ET.IMMEDIATE_DISABLE])
    assert isinstance(up, Alert), "controlsMismatch's immediate-disable alert is a plain Alert"
    up.alert_type, up.event_type = "controlsMismatch/immediateDisable", ET.IMMEDIATE_DISABLE
    am.add_many(0, [up])
    late = h.late_alerts(am, 1, [], [None] * 6)
    assert [a.alert_type for a in late] == ["carBusOffDisengage/immediateDisable"]
    assert h.late_alerts(am, 1, late, [None] * 6) == [], "already made this frame"
    am.add_many(1, late)
    assert h.late_alerts(am, 2, [], [None] * 6) == [], "already shown"
    h.bus_off = False
    fresh = AlertManager()
    fresh.add_many(0, [up])
    assert h.late_alerts(fresh, 1, [], [None] * 6) == [], "never without the bus-off reason"

  def test_exit_grace_is_bounded(self):
    h = CarStockAlert(make_cp())
    ev = Events()
    h.diag = True
    h.update(ev, [panda(NO_OUTPUT)], 0, False)
    h.diag = False
    for _ in range(EXIT_GRACE - 1):
      ev = Events()
      ev.add(EventName.controlsMismatch)
      h.update(ev, [panda(NO_OUTPUT)], 0, False)
      assert h.masked
    ev = Events()
    ev.add(EventName.controlsMismatch)
    h.update(ev, [panda(NO_OUTPUT)], 0, False)
    assert not h.masked and ev.has(EventName.controlsMismatch)

  def test_no_pandas_is_not_stock(self):
    h = CarStockAlert(make_cp())
    ev = Events()
    ev.add(EventName.controlsMismatch)
    h.diag = True
    h.update(ev, [], 0, False)
    assert not h.masked and ev.has(EventName.controlsMismatch) and ev.has(EventName.carNotReady)


class TestEventClasses(OpenpilotTestCase):
  def test_event_names_are_in_the_schema(self):
    names = custom.OnroadEventSP.EventName.schema.enumerants
    assert (names["diagnosticToolStock"], names["carBusOffStock"], names["carBusOffAnnounce"], names["carBusOffDisengage"]) == \
      (33, 34, 35, 36)
    src = CUSTOM_CAPNP.read_text(encoding="utf-8")
    for n, o in (("diagnosticToolStock", 33), ("carBusOffStock", 34), ("carBusOffAnnounce", 35), ("carBusOffDisengage", 36)):
      assert re.search(rf"^\s+{n} @{o};", src, re.M), n
      assert EventNameSP.schema.enumerants[n] in EVENTS_SP

  def test_types(self):
    assert set(EVENTS_SP[DIAG]) == {ET.PERMANENT, ET.NO_ENTRY}, "fix 1 never disables: it is entered with nothing engaged"
    assert set(EVENTS_SP[BUS_OFF]) == {ET.PERMANENT, ET.NO_ENTRY}, "an IMMEDIATE_DISABLE here would knock a paused MADS"
    assert set(EVENTS_SP[ANNOUNCE]) == {ET.PERMANENT}
    assert set(EVENTS_SP[DISENGAGE]) == {ET.IMMEDIATE_DISABLE}
    assert set(EVENTS[EventName.carNotReady]) == {ET.NO_ENTRY}, "the refusal carrier must stay NO_ENTRY only"

  def test_sounds_and_durations(self):
    assert EVENTS_SP[DIAG][ET.PERMANENT].audible_alert == AudibleAlert.none, "a note, as asked"
    assert EVENTS_SP[BUS_OFF][ET.PERMANENT].audible_alert == AudibleAlert.none
    ann = EVENTS_SP[ANNOUNCE][ET.PERMANENT]
    up = ImmediateDisableAlert("x")
    assert ann.audible_alert == AudibleAlert.prompt and ann.duration < up.duration
    imm = EVENTS_SP[DISENGAGE][ET.IMMEDIATE_DISABLE]
    assert (imm.priority, imm.audible_alert, imm.duration, imm.alert_status) == \
      (up.priority, up.audible_alert, up.duration, up.alert_status), "upstream's disengagement, only the text differs"


def all_alerts() -> list[tuple[str, Alert]]:
  out = []
  for e in OURS:
    for et, a in EVENTS_SP[e].items():
      assert isinstance(a, Alert), "static, so test_hud_cluster.every_alert() walks them"
      out.append((et, a))
  return out


class TestTexts(OpenpilotTestCase):
  def test_the_words(self):
    assert DIAG_TEXT == ("Diagnostic Tool Connected", "openpilot paused, car is stock")
    assert BUS_OFF_TEXT == ("Car Bus Error", "car is stock, openpilot off until restart")
    assert DISENGAGE_TEXT[1] == "Car Bus Error: Car Is Stock"

  def test_upstream_sanity_rules(self):
    for _, a in all_alerts():
      if a.alert_size == AlertSize.small:
        assert a.alert_text_1 and not a.alert_text_2, a
      elif a.alert_size == AlertSize.mid:
        assert a.alert_text_1 and a.alert_text_2, a
      assert a.creation_delay == 0.
      for s in (a.alert_text_1, a.alert_text_2):
        assert all(32 <= ord(c) <= 126 for c in s), s

  def test_texts_fit_the_mici_alert_renderer(self):
    """Wrapped the way mici/onroad/alert_renderer.py sizes them, measured with the real font files
    (test_vsa_fault_alert.TestTexts' method)."""
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

    for et, a in all_alerts():
      t1, t2 = (a.alert_text_2, a.alert_text_1) if et == ET.NO_ENTRY else (a.alert_text_1, a.alert_text_2)
      t1, t2 = t1.lower(), t2.lower()
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


class TestSelfdrivedWiring(OpenpilotTestCase):
  def test_wired(self):
    src = SELFDRIVED.read_text(encoding="utf-8")
    assert "from openpilot.sunnypilot.selfdrive.selfdrived.car_stock_alert import CarStockAlert  # FORK(HONDA_ACCORD_9G_AU)" in src
    assert "self.car_stock_alert = CarStockAlert(self.CP)" in src
    call = src.index("for e in self.car_stock_alert.update(self.events, self.sm['pandaStates'], self.mismatch_counter, " +
                     "self.enabled or self.mads.active):\n      self.events_sp.add(e)")
    # after the pandaStates loop has raised controlsMismatch, before num_events and the catch-all reads `events`
    assert src.index("self.events.add(EventName.controlsMismatch)") < src.index("self.events.add(EventName.relayMalfunction)") < call
    assert call < src.index("num_events = len(self.events)")
    # the texts: after the VSA's adjust_alerts, before the AlertManager
    adj = src.index("alerts, alerts_sp = self.car_stock_alert.adjust_alerts(alerts, alerts_sp, self.AM, self.sm.frame, callback_args)")
    assert src.index("alerts, alerts_sp = self.vsa_fault_alert.adjust_alerts(") < adj < src.index("self.AM.add_many(self.sm.frame, alerts + alerts_sp)")
    # the reasons: read in params_thread, never on the 100 Hz path
    pt = src[src.index("  def params_thread(self, evt):"):src.index("  def run(self):")]
    assert "self.car_stock_alert.read_params(self.params)" in pt
    assert src.count("car_stock_alert.read_params(") == 1
