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
from types import SimpleNamespace

import pytest
from pytest_mock import MockerFixture

from cereal import log, custom
from opendbc.car import structs
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.mads.helpers import MadsSteeringModeOnBrake
from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem, LINBUS_REASON_DRIVER_OVERRIDE, \
  EMERGENCY_STEER_RATE

State = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState
EventName = log.OnroadEvent.EventName
SafetyModel = structs.CarParams.SafetyModel

REASON_NO_REQUEST = 1   # what the board reports once the driver lets go and openpilot is quiet


def gateway(override: bool, present: bool = True, grant_valid: bool = True):
  """carStateSP.linbusGateway as mads.py reads it."""
  return SimpleNamespace(present=present, grantValid=grant_valid, granted=False,
                         grantReason=LINBUS_REASON_DRIVER_OVERRIDE if override else REASON_NO_REQUEST)


def car_state(brake: bool = False, steer_rate: float = 0.0):
  cs = structs.CarState()
  cs.vEgo = 25.0
  cs.brakePressed = brake
  cs.steeringRateDeg = steer_rate
  cs.cruiseState.available = True
  return cs


def make_mads(mocker: MockerFixture, steering_mode: int = MadsSteeringModeOnBrake.PAUSE):
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
  mads.state_machine.state = State.enabled
  mads.enabled = True
  mads.active = True
  return mads, sd


def step(mads, sd, gw, cs=None, brake: bool = False, n: int = 1) -> list:
  """Run n frames; return MADS's state after each one."""
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


class TestGatewayPause:
  @pytest.fixture(autouse=True)
  def setup(self, mocker: MockerFixture):
    self.mocker = mocker

  def test_the_pause_holds_through_the_whole_override(self):
    """Route 00000103 t=58.5: this read paused, enabled, paused, enabled..."""
    mads, sd = make_mads(self.mocker)
    states = step(mads, sd, gateway(override=True), n=300)
    assert states == [State.paused] * 300
    assert mads.enabled and not mads.active   # dashed lanes, no request

  def test_it_resumes_on_its_own_when_the_board_lets_go(self):
    mads, sd = make_mads(self.mocker)
    step(mads, sd, gateway(override=True), n=50)
    assert step(mads, sd, gateway(override=False)) == [State.enabled]
    assert mads.active

  @pytest.mark.parametrize("steering_mode", [MadsSteeringModeOnBrake.REMAIN_ACTIVE, MadsSteeringModeOnBrake.PAUSE])
  def test_the_pause_holds_in_every_brake_mode(self, steering_mode):
    mads, sd = make_mads(self.mocker, steering_mode)
    assert step(mads, sd, gateway(override=True), n=100) == [State.paused] * 100

  def test_a_brake_held_in_pause_mode_outlasts_the_override(self):
    """The board lets go while the brake is still pressed: stay paused until it is released."""
    mads, sd = make_mads(self.mocker, MadsSteeringModeOnBrake.PAUSE)
    step(mads, sd, gateway(override=True), n=20)
    assert step(mads, sd, gateway(override=False), brake=True, n=20) == [State.paused] * 20
    assert step(mads, sd, gateway(override=False)) == [State.enabled]

  def test_releasing_the_brake_mid_override_does_not_resume(self):
    """Paused by the brake first, then the driver takes the wheel: the brake's release is not the end."""
    mads, sd = make_mads(self.mocker, MadsSteeringModeOnBrake.PAUSE)
    assert step(mads, sd, gateway(override=False), brake=True) == [State.paused]
    step(mads, sd, gateway(override=True), brake=True, n=10)
    assert step(mads, sd, gateway(override=True), n=50) == [State.paused] * 50
    assert step(mads, sd, gateway(override=False)) == [State.enabled]

  def test_an_emergency_takeover_turns_mads_off_even_on_the_first_override_frame(self):
    """A swerve fires the steer-rate takeover and the board's override on the same frame.
    lkasDisable must win: MADS off, not paused (a silentLkasDisable beside it would pause)."""
    mads, sd = make_mads(self.mocker)
    swerve = car_state(steer_rate=EMERGENCY_STEER_RATE + 50)
    step(mads, sd, gateway(override=False), cs=swerve)          # 1st fast frame: not yet
    assert step(mads, sd, gateway(override=True), cs=swerve) == [State.disabled]
    # and it stays off: the override ending is not a reason to come back
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  def test_an_emergency_during_a_pause_turns_mads_off(self):
    mads, sd = make_mads(self.mocker)
    step(mads, sd, gateway(override=True), n=20)
    swerve = car_state(steer_rate=-(EMERGENCY_STEER_RATE + 50))
    assert step(mads, sd, gateway(override=True), cs=swerve, n=2)[-1] == State.disabled
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  @pytest.mark.parametrize("gw", [gateway(override=True, present=False),       # every other car
                                  gateway(override=True, grant_valid=False)],  # no 0x70B heard
                           ids=["not_present", "grant_not_valid"])
  def test_no_board_no_pause(self, gw):
    mads, sd = make_mads(self.mocker)
    assert step(mads, sd, gw, n=20) == [State.enabled] * 20

  def test_mads_off_is_not_turned_on_by_an_override(self):
    mads, sd = make_mads(self.mocker)
    mads.state_machine.state = State.disabled
    mads.enabled = mads.active = False
    assert step(mads, sd, gateway(override=True), n=10) == [State.disabled] * 10
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10
