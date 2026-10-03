"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): MADS and selfdrived in stock ACC mode (HondaElesysStockAcc). The car's ACC does the
longitudinal, so CarParams are pcmCruise with openpilot long off, and car_events raises belowEngageSpeed below
19 mph, pcmEnable on ACC_STATUS rising, and speedTooLow/cruiseDisabled whenever it is off. Each frame here is
selfdrived.step() in miniature: the real car events, selfdrived's real state machine, then MADS, then the alerts.
"""
from types import SimpleNamespace

from openpilot.cereal import log, custom
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.structs import car
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from opendbc.sunnypilot.car.interfaces import setup_interfaces
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.car.car_events import CarEvents
from openpilot.selfdrive.selfdrived.events import ET, Events
from openpilot.selfdrive.selfdrived.state import StateMachine
from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

State = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState
EventName = log.OnroadEvent.EventName
AlertStatus = log.SelfdriveState.AlertStatus
ButtonType = structs.CarState.ButtonEvent.Type
SafetyModel = structs.CarParams.SafetyModel

KPH = 1 / 3.6
DROP_OUT_SPEED = 22.0 * KPH      # where stock ACC lets go by itself on this car (routes 0e, 44, 61, 82)


def stock_car_params(stock: bool = True):
  fp = gen_empty_fingerprint()
  fp[0][0x188] = 8
  fp[0][0x201] = 6
  CP = CarInterface.get_params(CAR.HONDA_ACCORD_9G_AU, fp, [], False, False, False)
  CP_SP = CarInterface.get_params_sp(CP, CAR.HONDA_ACCORD_9G_AU, fp, [], False, False, False)
  setup_interfaces(CarInterface, CP, CP_SP, [{"HondaElesysStockAcc": "1"}])
  if not stock:
    # the same pcmCruise CarParams without the mode's flag: upstream's MADS rules, for contrast
    CP_SP.flags &= ~HondaFlagsSP.ELESYS_STOCK_ACC.value
  return CP, CP_SP


def car_state(v_ego: float, cruise: bool, lkas_button: bool = False):
  cs = car.CarState.new_message()
  cs.vEgo = v_ego
  cs.standstill = v_ego < 0.001
  cs.gearShifter = car.CarState.GearShifter.drive
  cs.canValid = True
  cs.cruiseState.available = True
  cs.cruiseState.enabled = cruise
  if lkas_button:
    cs.init('buttonEvents', 1)
    cs.buttonEvents[0].type = ButtonType.lkas
    cs.buttonEvents[0].pressed = True
  return cs.as_reader()


class Drive:
  def __init__(self, mocker, stock: bool = True):
    self.CP, self.CP_SP = stock_car_params(stock)
    sd = mocker.MagicMock()
    sd.CP, sd.CP_SP = self.CP, self.CP_SP
    # MADS defaults: on, MAIN may engage it, unified engagement, steering stays on the brake
    sd.values = {"Mads": True, "MadsMainCruiseAllowed": True, "MadsUnifiedEngagementMode": True,
                 "MadsSteeringMode": 0, "MadsEmergencySteerDisable": True, "MadsEmergencySteerRate": 200}
    sd.params = mocker.MagicMock()
    sd.params.get_bool = mocker.MagicMock(side_effect=lambda k, *a, **kw: bool(sd.values.get(k, False)))
    sd.params.get = mocker.MagicMock(side_effect=lambda k, *a, **kw: sd.values.get(k))
    sd.events = Events()
    sd.events_sp = EventsSP()
    sd.state_machine = StateMachine()
    sd.enabled = sd.active = sd.enabled_prev = False
    sd.initialized = True
    ps = mocker.MagicMock()
    ps.controlsAllowedLateral = True
    ps.safetyModel = SafetyModel.hondaNidec
    gw = SimpleNamespace(present=True, grantValid=True, granted=True, grantReason=1)   # the board steering, no override
    sd.sm = {'pandaStates': [ps], 'carStateSP': SimpleNamespace(linbusGateway=gw)}
    sd.CS_prev = car_state(0.0, False)
    self.sd = sd
    self.mads = ModularAssistiveDrivingSystem(sd)
    self.car_events = CarEvents(self.CP)
    self.alerts: list = []

  def step(self, cs, n: int = 1):
    """selfdrived.step(): car events, selfdrived's state machine, MADS, then the alerts from what is left."""
    sd = self.sd
    for _ in range(n):
      sd.events.clear()
      sd.events_sp.clear()
      cc = car.CarControl.new_message()
      sd.events.add_from_msg(self.car_events.update(cs, sd.CS_prev, cc.as_reader()).to_msg())
      if sd.initialized:
        sd.enabled, sd.active = sd.state_machine.update(sd.events)
      self.mads.update(cs)
      args = [self.CP, cs, {}, True, sd.state_machine.soft_disable_timer, log.LongitudinalPersonality.standard]
      self.alerts = sd.events.create_alerts(sd.state_machine.current_alert_types, args) + \
                    sd.events_sp.create_alerts(sd.state_machine.current_alert_types, args)
      sd.CS_prev = cs
    return self


class TestMadsStockAccEngagement(OpenpilotTestCase):
  def test_the_car_params_are_the_mode(self, mocker):
    d = Drive(mocker)
    assert d.CP.pcmCruise and not d.CP.openpilotLongitudinalControl
    assert d.mads.elesys_stock_acc
    assert not Drive(mocker, stock=False).mads.elesys_stock_acc

  def test_mads_turns_on_at_a_standstill_and_below_19_mph(self, mocker):
    for v_ego in (0.0, 1.0, 20.0 * KPH, 29.0 * KPH):
      d = Drive(mocker)
      d.step(car_state(v_ego, False))
      assert d.sd.events.has(EventName.belowEngageSpeed) is False, "MADS strips it while openpilot is off"
      d.step(car_state(v_ego, False, lkas_button=True))
      assert d.mads.enabled and d.mads.active, f"MADS refused at {v_ego:.2f} m/s"
      assert not d.sd.enabled, "openpilot itself is not engaged by the LKAS button"
      # and it stays on through the frames that follow, stock ACC still off
      d.step(car_state(v_ego, False), n=200)
      assert d.mads.enabled and d.mads.active

  def test_mads_off_and_on_again_at_a_standstill(self, mocker):
    d = Drive(mocker)
    d.step(car_state(0.0, False, lkas_button=True))
    assert d.mads.enabled
    d.step(car_state(0.0, False, lkas_button=True))
    assert not d.mads.enabled
    d.step(car_state(0.0, False, lkas_button=True))
    assert d.mads.enabled and d.mads.active

  def test_without_the_mode_upstream_still_refuses_below_min_enable_speed(self, mocker):
    # the gate is the stock ACC flag: on any other pcmCruise car belowEngageSpeed refuses MADS from off, as upstream
    d = Drive(mocker, stock=False)
    d.step(car_state(0.0, False, lkas_button=True))
    assert not d.mads.enabled

  def test_openpilot_follows_stock_acc(self, mocker):
    d = Drive(mocker)
    d.step(car_state(80 * KPH, False), n=10)
    assert not d.sd.enabled and not d.mads.enabled
    d.step(car_state(80 * KPH, True))
    assert d.sd.events.has(EventName.pcmEnable)
    assert d.sd.enabled, "openpilot engages on ACC_STATUS rising"
    assert d.mads.enabled and d.mads.active, "and lateral comes with it (unified engagement)"
    d.step(car_state(80 * KPH, True), n=100)
    assert d.sd.enabled and d.mads.active

  def test_openpilot_still_refuses_below_19_mph(self, mocker):
    # minEnableSpeed stays 19 mph: a stock engagement at 29 km/h is not followed, lateral is still allowed
    d = Drive(mocker)
    d.step(car_state(29 * KPH, False))
    d.step(car_state(29 * KPH, True))
    assert not d.sd.enabled


class TestMadsStockAccDropOut(OpenpilotTestCase):
  def _engaged(self, mocker, stock: bool = True) -> Drive:
    d = Drive(mocker, stock)
    d.step(car_state(80 * KPH, False))
    d.step(car_state(80 * KPH, True))
    d.step(car_state(DROP_OUT_SPEED, True), n=50)
    assert d.sd.enabled and d.mads.active
    return d

  def test_drop_out_is_speed_too_low_and_lateral_stays(self, mocker):
    d = self._engaged(mocker)
    d.step(car_state(DROP_OUT_SPEED, False))
    assert not d.sd.enabled, "openpilot follows stock ACC out"
    assert d.mads.enabled and d.mads.active, "MADS keeps steering"
    texts = [(a.alert_text_1, a.alert_text_2) for a in d.alerts]
    assert ("openpilot Canceled", "Speed too low") in texts, texts
    for a in d.alerts:
      assert a.alert_status != AlertStatus.critical, f"critical alert on a drop-out: {a.alert_text_1} / {a.alert_text_2}"
      assert "TAKE CONTROL" not in a.alert_text_1
    alert = next(a for a in d.alerts if a.alert_text_2 == "Speed too low")
    assert alert.alert_type.endswith(ET.IMMEDIATE_DISABLE)
    assert alert.alert_status == AlertStatus.normal

    # the frames after: lateral still on, no repeated disengagement alert, nothing critical
    for _ in range(300):
      d.step(car_state(DROP_OUT_SPEED - 2.0, False))
      assert d.mads.enabled and d.mads.active
      assert not any(a.alert_status == AlertStatus.critical for a in d.alerts)
      assert not any(a.alert_text_2 == "Speed too low" for a in d.alerts)

  def test_drop_out_with_mads_off_is_upstream(self, mocker):
    # lateral was never on: selfdrived's own speedTooLow, MADS not involved
    d = self._engaged(mocker)
    d.mads.state_machine.state = State.disabled
    d.mads.enabled = d.mads.active = False
    d.step(car_state(DROP_OUT_SPEED, False))
    assert not d.sd.enabled and not d.mads.enabled
    assert any(a.alert_text_2 == "Speed too low" for a in d.alerts)

  def test_the_alert_comes_back_only_in_the_mode(self, mocker):
    # the same frames without the flag: upstream MADS strips speedTooLow on that frame, alert and all
    d = self._engaged(mocker, stock=False)
    d.step(car_state(DROP_OUT_SPEED, False))
    assert not d.sd.enabled and d.mads.enabled
    assert not any(a.alert_text_2 == "Speed too low" for a in d.alerts)
