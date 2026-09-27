"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The EPS-LKAS gateway's driver-override pause (HONDA_ELESYS). While the board reports that
the driver has the wheel (0x70B, grantReason 4), MADS must sit in `paused` - enabled, so the
cluster keeps its dashed lanes, but not active, so openpilot stops asking. Route 00000103
t=58.5 showed it flapping instead: paused, enabled, paused, enabled... on consecutive frames
for 31 s, because the generic silent-resume lifted the pause every other frame.
"""
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.common.parameterized import parameterized

from openpilot.cereal import log, custom
from opendbc.car import structs
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.mads.helpers import MadsSteeringModeOnBrake
from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem, LINBUS_REASON_DRIVER_OVERRIDE, \
  EMERGENCY_STEER_RATE
from openpilot.common.test import OpenpilotTestCase

State = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState
EventName = log.OnroadEvent.EventName
SafetyModel = structs.CarParams.SafetyModel
ButtonType = structs.CarState.ButtonEvent.Type

REASON_NO_REQUEST = 1   # what the board reports once the driver lets go and openpilot is quiet


def gateway(override: bool, present: bool = True, grant_valid: bool = True):
  """carStateSP.linbusGateway as mads.py reads it."""
  return SimpleNamespace(present=present, grantValid=grant_valid, granted=False,
                         grantReason=LINBUS_REASON_DRIVER_OVERRIDE if override else REASON_NO_REQUEST)


def car_state(brake: bool = False, regen: bool = False, steer_rate: float = 0.0):
  cs = structs.CarState()
  cs.vEgo = 25.0
  cs.brakePressed = brake
  cs.regenBraking = regen
  cs.steeringRateDeg = steer_rate
  cs.cruiseState.available = True
  return cs


def make_mads(mocker, steering_mode: int = MadsSteeringModeOnBrake.PAUSE):
  sd = mocker.MagicMock()
  sd.CP = structs.CarParams()
  sd.CP.brand = "honda"
  sd.CP_SP = structs.CarParamsSP()
  sd.params = mocker.MagicMock()
  sd.params.get_bool = mocker.MagicMock(side_effect=lambda k: {"Mads": True}.get(k, False))
  sd.params.get = mocker.MagicMock(return_value=steering_mode)
  sd.events = Events()
  sd.events_sp = EventsSP()
  sd.enabled = False
  sd.enabled_prev = False
  sd.initialized = True
  sd.CS_prev = car_state()
  ps = mocker.MagicMock()
  ps.controlsAllowedLateral = True
  ps.safetyModel = SafetyModel.hondaNidec
  sd.sm = {'pandaStates': [ps], 'carStateSP': SimpleNamespace(linbusGateway=gateway(False))}

  mads = ModularAssistiveDrivingSystem(sd)
  mads.enabled_toggle = True
  mads.steering_mode_on_brake = steering_mode
  mads.state_machine.state = State.enabled
  mads.enabled = True
  mads.active = True
  return mads, sd


def step(mads, sd, gw, cs=None, brake: bool = False, n: int = 1) -> list:
  """Run n frames; return MADS's state after each one. `brake` also raises selfdrived's pedalPressed."""
  states = []
  for _ in range(n):
    frame_cs = cs if cs is not None else car_state(brake=brake)
    sd.sm['carStateSP'] = SimpleNamespace(linbusGateway=gw)
    if brake:
      sd.events.add(EventName.pedalPressed)
    mads.update(frame_cs)
    sd.CS_prev = frame_cs
    sd.events.clear()
    sd.events_sp.clear()
    states.append(mads.state_machine.state)
  return states


class TestGatewayPause(OpenpilotTestCase):
  def test_the_pause_holds_through_the_whole_override(self, mocker):
    """Route 00000103 t=58.5: this read paused, enabled, paused, enabled..."""
    mads, sd = make_mads(mocker)
    states = step(mads, sd, gateway(override=True), n=300)
    assert states == [State.paused] * 300
    assert mads.enabled and not mads.active   # dashed lanes, no request

  def test_it_resumes_on_its_own_when_the_board_lets_go(self, mocker):
    mads, sd = make_mads(mocker)
    step(mads, sd, gateway(override=True), n=50)
    assert step(mads, sd, gateway(override=False)) == [State.enabled]
    assert mads.active

  @parameterized.expand([MadsSteeringModeOnBrake.REMAIN_ACTIVE, MadsSteeringModeOnBrake.PAUSE], names=["steering_mode"])
  def test_the_pause_holds_in_every_brake_mode(self, mocker, steering_mode):
    mads, sd = make_mads(mocker, steering_mode)
    assert step(mads, sd, gateway(override=True), n=100) == [State.paused] * 100

  def test_a_brake_held_in_pause_mode_outlasts_the_override(self, mocker):
    """The board lets go while the brake is still pressed: stay paused until it is released."""
    mads, sd = make_mads(mocker, MadsSteeringModeOnBrake.PAUSE)
    step(mads, sd, gateway(override=True), n=20)
    assert step(mads, sd, gateway(override=False), brake=True, n=20) == [State.paused] * 20
    assert step(mads, sd, gateway(override=False)) == [State.enabled]

  @parameterized.expand([(True, False), (False, True)], names=["brake", "regen"])
  def test_a_brake_held_without_a_pedal_event_outlasts_the_override(self, mocker, brake, regen):
    """Upstream 79b79edd2: in Pause mode brakePressed or regenBraking alone blocks the silent
    resume, with no pedalPressed event (at a standstill selfdrived raises none). The gateway's
    resume only clears its own flag, so that guard holds the pause after the board lets go."""
    mads, sd = make_mads(mocker, MadsSteeringModeOnBrake.PAUSE)
    step(mads, sd, gateway(override=True), n=20)
    held = car_state(brake=brake, regen=regen)
    assert step(mads, sd, gateway(override=False), cs=held, n=20) == [State.paused] * 20
    assert not mads._gw_paused   # the board has let go; only the brake holds the pause now
    assert step(mads, sd, gateway(override=False)) == [State.enabled]

  def test_releasing_the_brake_mid_override_does_not_resume(self, mocker):
    """Paused by the brake first, then the driver takes the wheel: the brake's release is not the end."""
    mads, sd = make_mads(mocker, MadsSteeringModeOnBrake.PAUSE)
    assert step(mads, sd, gateway(override=False), brake=True) == [State.paused]
    step(mads, sd, gateway(override=True), brake=True, n=10)
    assert step(mads, sd, gateway(override=True), n=50) == [State.paused] * 50
    assert step(mads, sd, gateway(override=False)) == [State.enabled]

  def test_an_emergency_takeover_turns_mads_off_even_on_the_first_override_frame(self, mocker):
    """A swerve fires the steer-rate takeover and the board's override on the same frame.
    lkasDisable must win: MADS off, not paused (a silentLkasDisable beside it would pause)."""
    mads, sd = make_mads(mocker)
    swerve = car_state(steer_rate=EMERGENCY_STEER_RATE + 50)
    step(mads, sd, gateway(override=False), cs=swerve)          # 1st fast frame: not yet
    assert step(mads, sd, gateway(override=True), cs=swerve) == [State.disabled]
    # and it stays off: the override ending is not a reason to come back
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  def test_an_emergency_during_a_pause_turns_mads_off(self, mocker):
    mads, sd = make_mads(mocker)
    step(mads, sd, gateway(override=True), n=20)
    swerve = car_state(steer_rate=-(EMERGENCY_STEER_RATE + 50))
    assert step(mads, sd, gateway(override=True), cs=swerve, n=2)[-1] == State.disabled
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  @parameterized.expand([("not_present", gateway(override=True, present=False)),       # every other car
                         ("grant_not_valid", gateway(override=True, grant_valid=False))],  # no 0x70B heard
                        names=["label", "gw"], ids=lambda label, gw: label)
  def test_no_board_no_pause(self, mocker, label, gw):
    mads, sd = make_mads(mocker)
    assert step(mads, sd, gw, n=20) == [State.enabled] * 20, label

  def test_selfdrived_subscribes_the_gateway_state(self):
    """mads.py treats an sm without carStateSP as no gateway, so losing the subscription would be silent."""
    spec = importlib.util.find_spec("openpilot.selfdrive.selfdrived.selfdrived")
    assert spec is not None and spec.origin is not None
    src = Path(spec.origin).read_text()
    services = re.search(r"messaging\.SubMaster\(\[(.*?)\]", src, re.DOTALL)
    assert services is not None and "'carStateSP'" in services.group(1)

  def test_mads_off_is_not_turned_on_by_an_override(self, mocker):
    mads, sd = make_mads(mocker)
    mads.state_machine.state = State.disabled
    mads.enabled = mads.active = False
    assert step(mads, sd, gateway(override=True), n=10) == [State.disabled] * 10
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  @parameterized.expand([("lkas_button",), ("unified_engagement",)], names=["how"], ids=lambda how: how)
  def test_turning_mads_on_during_an_override_starts_paused(self, mocker, how):
    """MADS off, the board already reporting the driver's hands on the wheel, and the driver
    turns MADS on. It used to go enabled (active, one request frame) on the press and only
    pause on the next frame, because the gateway block needed self.enabled. It must come on
    paused: enabled for the dashed lanes, never active, and resume when the board lets go."""
    mads, sd = make_mads(mocker)
    mads.state_machine.state = State.disabled
    mads.enabled = mads.active = False
    step(mads, sd, gateway(override=True), n=5)

    press = car_state()
    if how == "lkas_button":
      press.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.lkas, pressed=True)]
    else:
      mads.unified_engagement_mode = True
      sd.events.add(EventName.buttonEnable)
    assert step(mads, sd, gateway(override=True), cs=press) == [State.paused]
    assert mads.enabled and not mads.active, "active on the press frame: one request while the driver has the wheel"
    assert step(mads, sd, gateway(override=True), n=20) == [State.paused] * 20
    assert step(mads, sd, gateway(override=False)) == [State.enabled]
    assert mads.active

  @parameterized.expand([("not_present", gateway(override=True, present=False)),
                         ("no_override", gateway(override=False))],
                        names=["label", "gw"], ids=lambda label, gw: label)
  def test_turning_mads_on_without_an_override_is_upstream(self, mocker, label, gw):
    mads, sd = make_mads(mocker)
    mads.state_machine.state = State.disabled
    mads.enabled = mads.active = False
    press = car_state()
    press.buttonEvents = [structs.CarState.ButtonEvent(type=ButtonType.lkas, pressed=True)]
    assert step(mads, sd, gw, cs=press) == [State.enabled], label
    assert mads.active and not mads._gw_paused
