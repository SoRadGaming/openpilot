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
import itertools
import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.common.parameterized import parameterized

from openpilot.cereal import log, custom
from opendbc.car import structs
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.mads.helpers import MadsSteeringModeOnBrake
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem, LINBUS_REASON_DRIVER_OVERRIDE, \
  EMERGENCY_STEER_RATE, EMERGENCY_STEER_RATES, EMERGENCY_STEER_FRAMES, GATEWAY_SILENT_RESUME_FRAMES, \
  read_emergency_steer_rate
from openpilot.common.test import OpenpilotTestCase

State = custom.ModularAssistiveDrivingSystem.ModularAssistiveDrivingSystemState
EventName = log.OnroadEvent.EventName
SafetyModel = structs.CarParams.SafetyModel
ButtonType = structs.CarState.ButtonEvent.Type

REASON_NO_REQUEST = 1   # what the board reports once the driver lets go and openpilot is quiet


_frames = itertools.count(1)


def gateway(override: bool, present: bool = True, grant_valid: bool = True):
  """carStateSP.linbusGateway as mads.py reads it: ONE new 0x70B frame (its own grantSeq). Passed to
  step(n=...), the same frame is then held inside its 500 ms window, as carstate holds it."""
  return SimpleNamespace(present=present, grantValid=grant_valid, granted=False, grantSeq=next(_frames),
                         grantReason=LINBUS_REASON_DRIVER_OVERRIDE if override else REASON_NO_REQUEST)


def car_state(brake: bool = False, regen: bool = False, steer_rate: float = 0.0):
  cs = structs.CarState()
  cs.vEgo = 25.0
  cs.brakePressed = brake
  cs.regenBraking = regen
  cs.steeringRateDeg = steer_rate
  cs.cruiseState.available = True
  return cs


def make_mads(mocker, steering_mode: int = MadsSteeringModeOnBrake.PAUSE, fast_wheel: bool = True,
              fast_wheel_rate=200):
  """`sd.values` is the params store: change it and call mads.read_params() to change a setting live."""
  sd = mocker.MagicMock()
  sd.CP = structs.CarParams()
  sd.CP.brand = "honda"
  sd.CP_SP = structs.CarParamsSP()
  sd.values = {"Mads": True, "MadsEmergencySteerDisable": fast_wheel,
               "MadsSteeringMode": steering_mode, "MadsEmergencySteerRate": fast_wheel_rate}
  sd.params = mocker.MagicMock()
  sd.params.get_bool = mocker.MagicMock(side_effect=lambda k, *a, **kw: bool(sd.values.get(k, False)))
  sd.params.get = mocker.MagicMock(side_effect=lambda k, *a, **kw: sd.values.get(k))
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


# FORK(LKAS-GATEWAY): the fast-wheel takeover as a setting (MadsEmergencySteerDisable, MadsEmergencySteerRate).
class TestFastWheelSetting(OpenpilotTestCase):
  def test_the_default_is_the_behaviour_from_before_it_was_a_setting(self, mocker):
    """On at 200 deg/s for 2 frames, exactly as the hard-coded takeover did."""
    assert EMERGENCY_STEER_RATE == 200.0 and EMERGENCY_STEER_FRAMES == 2
    assert EMERGENCY_STEER_RATE in EMERGENCY_STEER_RATES
    mads, sd = make_mads(mocker)
    assert mads.emergency_steer_disable and mads.emergency_steer_rate == 200.0
    assert step(mads, sd, gateway(override=False), cs=car_state(steer_rate=199.9), n=20) == [State.enabled] * 20
    fast = car_state(steer_rate=200.0)
    assert step(mads, sd, gateway(override=False), cs=fast, n=2) == [State.enabled, State.disabled]

  @parameterized.expand([(r,) for r in EMERGENCY_STEER_RATES], names=["rate"], ids=lambda rate: str(rate))
  def test_each_threshold_fires_at_its_rate_and_not_below(self, mocker, rate):
    mads, sd = make_mads(mocker, fast_wheel_rate=rate)
    assert mads.emergency_steer_rate == float(rate)
    below = car_state(steer_rate=-(rate - 1))
    assert step(mads, sd, gateway(override=False), cs=below, n=20) == [State.enabled] * 20
    at = car_state(steer_rate=-rate)
    # one frame is a decode glitch, the second is a driver - at every threshold
    assert step(mads, sd, gateway(override=False), cs=at, n=2) == [State.enabled, State.disabled]
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  def test_off_never_turns_mads_off(self, mocker):
    mads, sd = make_mads(mocker, fast_wheel=False)
    swerve = car_state(steer_rate=1000.0)
    assert step(mads, sd, gateway(override=False), cs=swerve, n=50) == [State.enabled] * 50
    assert mads.active and mads._fast_steer == 0

  def test_off_leaves_the_gateway_pause_alone(self, mocker):
    """With the takeover off a swerve during an override is just an override: paused, then resumed."""
    mads, sd = make_mads(mocker, fast_wheel=False)
    swerve = car_state(steer_rate=EMERGENCY_STEER_RATE + 400)
    assert step(mads, sd, gateway(override=True), cs=swerve, n=30) == [State.paused] * 30
    assert mads._gw_paused and mads.enabled and not mads.active
    assert step(mads, sd, gateway(override=False)) == [State.enabled]
    assert mads.active

  @parameterized.expand([(r,) for r in EMERGENCY_STEER_RATES if r - 25 >= EMERGENCY_STEER_RATE],
                        names=["rate"], ids=lambda rate: str(rate))
  def test_a_raised_threshold_holds_during_an_override(self, mocker, rate):
    """Takeover on, threshold raised: a swerve the default would have caught, but under the set rate, is just an
    override (paused, not off). Reaching the set rate for two frames still turns MADS off, and clears the pause."""
    mads, sd = make_mads(mocker, fast_wheel_rate=rate)
    under = car_state(steer_rate=rate - 25)   # >= the 200 default, < the set rate
    assert step(mads, sd, gateway(override=True), cs=under, n=30) == [State.paused] * 30
    assert mads._gw_paused and mads.enabled and not mads.active
    at = car_state(steer_rate=rate)
    assert step(mads, sd, gateway(override=True), cs=at, n=2) == [State.paused, State.disabled]
    assert not mads._gw_paused
    assert step(mads, sd, gateway(override=False), n=10) == [State.disabled] * 10

  @parameterized.expand([(0,), (1,), (3,), (175,), (199,), (1000,), (-200,), (None,), ("abc",), (float("nan"),)],
                        names=["stored"], ids=lambda stored: repr(stored))
  def test_anything_else_reads_as_the_default(self, mocker, stored):
    mads, sd = make_mads(mocker, fast_wheel_rate=stored)
    assert read_emergency_steer_rate(sd.params) == EMERGENCY_STEER_RATE
    assert mads.emergency_steer_rate == EMERGENCY_STEER_RATE

  def test_the_values_sunnylink_and_the_ui_write_are_accepted(self, mocker):
    """Params.get returns an int for an INT key; a str or float of the same value is accepted too."""
    _, sd = make_mads(mocker)
    for stored, expected in ((150, 150.0), ("250", 250.0), (300.0, 300.0), (b"150", 150.0)):
      sd.values["MadsEmergencySteerRate"] = stored
      assert read_emergency_steer_rate(sd.params) == expected, stored

  def test_turning_it_on_applies_without_a_restart(self, mocker):
    """selfdrived's params thread calls read_params() every 0.1 s."""
    mads, sd = make_mads(mocker, fast_wheel=False)
    swerve = car_state(steer_rate=300.0)
    assert step(mads, sd, gateway(override=False), cs=swerve, n=5) == [State.enabled] * 5
    sd.values["MadsEmergencySteerDisable"] = True
    mads.read_params()
    assert step(mads, sd, gateway(override=False), cs=swerve, n=2) == [State.enabled, State.disabled]

  def test_turning_it_off_mid_swerve_does_not_fire(self, mocker):
    mads, sd = make_mads(mocker)
    swerve = car_state(steer_rate=300.0)
    assert step(mads, sd, gateway(override=False), cs=swerve) == [State.enabled]   # 1st fast frame
    sd.values["MadsEmergencySteerDisable"] = False
    mads.read_params()
    assert step(mads, sd, gateway(override=False), cs=swerve, n=20) == [State.enabled] * 20

  def test_changing_the_threshold_applies_without_a_restart(self, mocker):
    mads, sd = make_mads(mocker)
    wheel = car_state(steer_rate=220.0)
    sd.values["MadsEmergencySteerRate"] = 250
    mads.read_params()
    assert step(mads, sd, gateway(override=False), cs=wheel, n=20) == [State.enabled] * 20
    sd.values["MadsEmergencySteerRate"] = 200
    mads.read_params()
    assert step(mads, sd, gateway(override=False), cs=wheel, n=2) == [State.enabled, State.disabled]


# FORK(LKAS-GATEWAY): the pause ends on a NEW 0x70B frame, never on the old one going stale.
GRANT_WINDOW = 50              # carstate_ext LINBUS_GRANT_STALE_FRAMES: 500 ms at carstate's 100 Hz
OVERRIDE = (0, LINBUS_REASON_DRIVER_OVERRIDE)   # IDLE, driver override
RELEASED = (0, REASON_NO_REQUEST)               # IDLE, no request: the driver let go, openpilot quiet
STEERING = (5, 15)                              # LIMITED, soft start: what route 121 sent at 59.30


class Board:
  """carStateSP.linbusGateway as carstate_ext._update_linbus_grant builds it, one MADS frame (10 ms) at a
  time: grantSeq steps only when a frame arrives, grantValid holds for 500 ms after it, and a stale frame
  reports nothing."""
  def __init__(self):
    self.seq, self.age, self.state, self.reason = 0, None, 0, 0

  def tick(self, frame=None):
    if frame is not None:
      self.seq += 1
      self.age = 0
      self.state, self.reason = frame
    elif self.age is not None:
      self.age += 1
    valid = self.age is not None and self.age < GRANT_WINDOW
    return SimpleNamespace(present=True, grantValid=valid, granted=valid and self.state in (3, 4, 5),
                           grantReason=self.reason if valid else 0, grantSeq=self.seq)


def frames(t0: float, t1: float, frame) -> list:
  """The board's 10 Hz frames from t0 up to (not including) t1, as (time, frame)."""
  return [(round(t0 + 0.1 * i, 3), frame) for i in range(round((t1 - t0) * 10))]


def drive(mads, sd, board, arrivals, until: float, start: float = 0.0) -> list:
  """Run MADS at 100 Hz from `start` to `until` with these 0x70B arrivals; return the state on every frame."""
  at = {round((t - start) * 100): f for t, f in arrivals}
  return [step(mads, sd, board.tick(at.get(i)))[0] for i in range(round((until - start) * 100))]


def first(states: list, state, start: float = 0.0) -> float | None:
  """When `state` first appears, in seconds."""
  return round(start + states.index(state) / 100, 2) if state in states else None


class TestGatewayPauseNeedsAFreshFrame(OpenpilotTestCase):
  def test_the_silent_limit_is_3_s(self):
    assert GATEWAY_SILENT_RESUME_FRAMES * DT_CTRL == 3.0

  def test_a_normal_release_resumes_on_the_first_released_frame(self, mocker):
    mads, sd = make_mads(mocker)
    states = drive(mads, sd, Board(), frames(0.0, 2.0, OVERRIDE) + frames(2.0, 3.0, RELEASED), 3.0)
    assert states[:200] == [State.paused] * 200
    assert first(states, State.enabled) == 2.0, "on the frame that said so"
    assert states[200:] == [State.enabled] * 100
    assert mads.active

  @parameterized.expand([(g,) for g in (0.3, 0.6, 1.0, 2.4, 2.99)], names=["gap"], ids=lambda gap: f"{gap}s")
  def test_a_gap_in_the_override_shorter_than_3_s_holds_the_pause(self, mocker, gap):
    """The frame goes stale 0.5 s into the gap. That used to be the resume; now only a new frame is."""
    mads, sd = make_mads(mocker)
    last = 1.0                                  # the last override frame before the gap
    after = round(last + gap, 3)                # the next frame to arrive
    arrivals = frames(0.0, last + 0.1, OVERRIDE) + frames(after, after + 1.0, OVERRIDE) + \
               frames(after + 1.0, after + 1.5, RELEASED)
    states = drive(mads, sd, Board(), arrivals, after + 1.5)
    assert first(states, State.enabled) == round(after + 1.0, 2), f"paused across the {gap} s gap, resumed on the release"

  @parameterized.expand([(g,) for g in (3.0, 5.0, 12.0)], names=["gap"], ids=lambda gap: f"{gap}s")
  def test_a_gap_of_3_s_or_more_resumes_as_with_no_board_and_the_next_override_pauses_again(self, mocker, gap):
    """The fallback: no 0x70B for 3 s is no board, and a car with no board has no gateway pause. When the
    board speaks again, an override frame pauses on arrival, as any override does."""
    mads, sd = make_mads(mocker)
    last = 1.0
    after = round(last + gap + 0.01, 3)         # just past the limit, so the resume shows before the board returns
    arrivals = frames(0.0, last + 0.1, OVERRIDE) + frames(after, after + 1.0, OVERRIDE) + \
               frames(after + 1.0, after + 1.5, RELEASED)
    states = drive(mads, sd, Board(), arrivals, after + 1.5)
    assert first(states, State.enabled) == last + 3.0, "3.0 s after the last frame, not 0.5"
    assert states[round(after * 100)] == State.paused, "the next override frame pauses again"
    assert states[round((after + 1.0) * 100):] == [State.enabled] * 50

  def test_lost_release_frames_do_not_resume_on_staleness(self, mocker):
    """The board let go at 1.1 s and its first 2.3 s of 'released' frames never reached the bus."""
    mads, sd = make_mads(mocker)
    arrivals = frames(0.0, 1.1, OVERRIDE) + frames(3.4, 4.0, RELEASED)
    states = drive(mads, sd, Board(), arrivals, 4.0)
    assert first(states, State.enabled) == 3.4, "on the first released frame that arrived, not at 1.5 (stale)"

  def test_a_board_that_goes_silent_resumes_once_after_3_s_and_says_so(self, mocker):
    log = mocker.patch("openpilot.sunnypilot.mads.mads.cloudlog")
    mads, sd = make_mads(mocker)
    states = drive(mads, sd, Board(), frames(0.0, 1.0, OVERRIDE), 20.0)
    assert states[:390] == [State.paused] * 390, "paused for 3 s after the last frame at 0.9"
    assert states[390:] == [State.enabled] * (2000 - 390), "then lateral comes back, and stays: no flapping"
    assert mads.active and not mads._gw_paused
    assert log.warning.call_count == 1

  def test_the_fallback_is_the_ordinary_resume_a_held_brake_still_holds(self, mocker):
    mads, sd = make_mads(mocker, MadsSteeringModeOnBrake.PAUSE)
    board = Board()
    drive(mads, sd, board, frames(0.0, 1.0, OVERRIDE), 1.0)
    held = [step(mads, sd, board.tick(), brake=True)[0] for _ in range(400)]
    assert held == [State.paused] * 400 and not mads._gw_paused, "the board is gone; the brake is not"
    assert step(mads, sd, board.tick()) == [State.enabled]

  def test_route_121_resumes_on_the_boards_frame_not_at_57_40(self, mocker):
    """0x70B as it reached the bus on route 121 (the d995bc95 board, its Tx-FIFO losses included): the last
    driver-override frame at 56.897, nothing for 2.4 s, then LIMITED / soft start from 59.298. MADS resumed at
    57.40 - the old frame's 500 ms window running out. Now it resumes on the 59.298 frame."""
    mads, sd = make_mads(mocker)
    ov = [56.092, 56.192, 56.296, 56.396, 56.496, 56.596, 56.695, 56.897]
    steer = [59.298, 59.397, 59.498, 59.598]
    arrivals = [(t, OVERRIDE) for t in ov] + [(t, STEERING) for t in steer]
    states = drive(mads, sd, Board(), arrivals, 60.0, start=56.09)[1:]   # from the 56.092 frame on
    assert states[:320] == [State.paused] * 320, "paused from 56.10 to 59.29, through 57.40"
    assert first(states, State.enabled, start=56.10) == 59.3
