"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): sla-flash - the wiring between the processes, run for real. The rig in test_speed_limit_now_confirm
stands in for every hop (its _plan() is the planner's publish, its step() is card's and plannerd's loops, the replay's
predict() is controlsd and the carcontroller), so a hop deleted in the real code passed every test (sla-flash review 2):
card never writing its report, plannerd never reading it - every press at 'now' swallowed and lost - or the planner or
controlsd flashing the offer instead of the arm - the dash flashing through every 'now' window with no press. These run
the real card.Car.state_update, plannerd.main, LongitudinalPlannerSP.publish_longitudinal_plan_sp and
ControlsExt.state_control_ext, with only their sockets and hardware replaced.
"""
from types import SimpleNamespace
from unittest import mock

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom
from opendbc.car import structs
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.structs import car
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.now_confirm import NOW_FLASH_HOLD_FRAMES
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.tests.test_speed_limit_confirm_grace import AssistState, \
  GraceTestBase, MINUS

ButtonEvent = car.CarState.ButtonEvent


def _params():
  """The owner's car as the speed-limit rig runs it: openpilot longitudinal, not PCM cruise."""
  CarInterface = interfaces[HONDA.HONDA_ACCORD_9G_AU]
  CP = CarInterface.get_non_essential_params(HONDA.HONDA_ACCORD_9G_AU)
  CP_SP = CarInterface.get_non_essential_params_sp(CP, HONDA.HONDA_ACCORD_9G_AU)
  CP.openpilotLongitudinalControl = True
  CP.pcmCruise = False
  return CP, CP_SP


def _plan(now_key: int = 0, now_limit: int = 0, armed_kph: float = 0., now_target: int = 0):
  """A longitudinalPlanSP at 60 with Speed Limit Assist active: the key it offers at 'now', and an arm."""
  msg = messaging.new_message('longitudinalPlanSP')
  lp = msg.longitudinalPlanSP
  r = lp.speedLimit.resolver
  r.speedLimitValid = r.speedLimitLastValid = True
  r.speedLimit = r.speedLimitLast = r.speedLimitFinal = r.speedLimitFinalLast = 60 * CV.KPH_TO_MS
  a = lp.speedLimit.assist
  a.state = AssistState.active
  a.nowKey, a.nowLimit, a.nowTarget = now_key, now_limit, now_target or now_limit
  if armed_kph:
    a.armedLimit = a.armedTarget = int(armed_kph)
    a.armedSpeed = armed_kph * CV.KPH_TO_MS
  return lp


class _SM(dict):
  """A SubMaster stand-in: services by name, `alive`, and an update() that does nothing."""
  def __init__(self, alive: dict, **services):
    super().__init__(**services)
    self.alive = alive

  def update(self, *_a, **_k):
    pass


class TestCardWritesItsReport(GraceTestBase):
  """card.Car.state_update: the plan's key reaches VCruiseHelper (with sm.alive), and the press it takes reaches the
  carStateSP card publishes (write_speed_limit_now on the builder)."""
  def _car(self, mocker, alive: bool):
    from openpilot.selfdrive.car import card as card_mod
    from openpilot.selfdrive.car.cruise import VCruiseHelper
    c = card_mod.Car.__new__(card_mod.Car)   # the real __init__ opens sockets and fingerprints a car
    c.can_sock = None
    c.can_rcv_cum_timeout_counter = 0
    c.is_metric = True
    # card's own cruise state for this car (the rig's): non-PCM, openpilot's set speed
    c.v_cruise_helper = VCruiseHelper(car.CarParams(pcmCruise=False), custom.CarParamsSP(pcmCruiseSpeed=True))
    c.v_cruise_helper.v_cruise_kph = c.v_cruise_helper.v_cruise_cluster_kph = 60.
    c.CC_prev = SimpleNamespace(enabled=True)
    cc = messaging.new_message('carControl').carControl
    cc.enabled = True
    sm = _SM({'longitudinalPlanSP': alive, 'carControl': True}, longitudinalPlanSP=_plan(-1, 50), carControl=cc)
    c.sm = sm  # ty: ignore[invalid-assignment]
    self._events: list = []

    def ci_update(_can):
      cs = car.CarState(cruiseState={"available": True}, vEgo=60 * CV.KPH_TO_MS)
      cs.buttonEvents = list(self._events)
      return cs, structs.CarStateSP()
    c.CI = SimpleNamespace(update=ci_update)  # ty: ignore[invalid-assignment]
    c.RI = SimpleNamespace(update=lambda _can: None)  # ty: ignore[invalid-assignment]
    mocker.patch.object(card_mod.messaging, 'drain_sock_raw', lambda *_a, **_k: [])
    return c

  def _press(self, c, button, frames: int = 12):
    """Down, held `frames` card frames, released: every frame's (carState, carStateSP) from card."""
    out = []
    for i in range(frames + 2):
      self._events = [ButtonEvent(type=button, pressed=True)] if i == 0 else \
                     [ButtonEvent(type=button, pressed=False)] if i == frames + 1 else []
      CS, CS_SP, _ = c.state_update()
      out.append((float(CS.vCruiseCluster), CS_SP.as_reader().speedLimitNow))
    return out

  def test_the_press_card_takes_reaches_carstatesp(self, mocker):
    c = self._car(mocker, alive=True)
    out = self._press(c, MINUS)
    assert {v for v, _ in out} == {60.}, "taken at 'now': the set speed does not move"
    n = out[-1][1]
    assert (n.consumedSeq, n.consumedLimit, n.consumedPlus) == (1, 50, False), \
      "the report on the carStateSP card publishes (card.py write_speed_limit_now)"
    assert all(r.consumedSeq == 0 for _, r in out[:-1]), "reported on the release, not before"

  def test_a_plan_no_longer_arriving_takes_nothing(self, mocker):
    """card.py passes sm.alive['longitudinalPlanSP']: a stale plan's key is not taken - an ordinary step."""
    c = self._car(mocker, alive=False)
    out = self._press(c, MINUS)
    assert out[-1][0] == 59. and out[-1][1].consumedSeq == 0, out[-1]


class TestPlannerdHandsTheReportOn(GraceTestBase):
  """plannerd.main: every loop, carStateSP.speedLimitNow goes to SpeedLimitAssist.update_now_consumed."""
  def test_the_loop_reads_cards_report(self, mocker):
    from openpilot.selfdrive.controls import plannerd

    class Stop(Exception):
      pass

    seen = []
    cs_sp = messaging.new_message('carStateSP').carStateSP
    cs_sp.speedLimitNow.consumedSeq, cs_sp.speedLimitNow.consumedLimit, cs_sp.speedLimitNow.consumedPlus = 3, 80, True
    services = {'carStateSP': cs_sp, 'selfdriveStateSP': messaging.new_message('selfdriveStateSP').selfdriveStateSP}

    class FakeSM:
      def __init__(self, *_a, **_k):
        self.updated, self.frame, self.loops = {'modelV2': False}, 0, 0

      def update(self, *_a):
        self.loops += 1
        if self.loops > 2:
          raise Stop

      def __getitem__(self, name):
        return services[name]

    planner = SimpleNamespace(sla=SimpleNamespace(update_buttons=lambda *_a: None,
                                                  update_now_consumed=lambda *a: seen.append(a)))
    mocker.patch.object(plannerd, 'config_realtime_process')
    mocker.patch.object(plannerd, 'Params', lambda: SimpleNamespace(get=lambda *_a, **_k: b''))
    mocker.patch.object(plannerd, 'get_gps_location_service', lambda _p: 'gpsLocation')
    mocker.patch.object(plannerd, 'LongitudinalPlanner', lambda *_a: planner)
    mocker.patch.object(plannerd, 'LaneDepartureWarning', mock.MagicMock())
    mocker.patch.object(plannerd.messaging, 'log_from_bytes', lambda *_a: SimpleNamespace(brand='honda'))
    mocker.patch.object(plannerd.messaging, 'SubMaster', FakeSM)
    mocker.patch.object(plannerd.messaging, 'PubMaster', mock.MagicMock())
    try:
      plannerd.main()
      raise AssertionError("plannerd's loop ended by itself")
    except Stop:
      pass
    assert seen == [(3, 80, True)] * 2, seen


class TestThePlanCarriesTheArmNotTheOffer(GraceTestBase):
  """LongitudinalPlannerSP.publish_longitudinal_plan_sp: nowKey/nowLimit/nowTarget are the offer; armedLimit/
  armedTarget/armedSpeed are the ARM only - what controlsd flashes. The offer alone must never reach armedSpeed."""
  def _publish(self, planner):
    sent = {}
    sm = SimpleNamespace(all_checks=lambda **_: True)
    pm = SimpleNamespace(send=lambda name, msg: sent.update({name: msg}))
    planner.publish_longitudinal_plan_sp(sm, pm)
    return sent['longitudinalPlanSP'].longitudinalPlanSP.speedLimit.assist

  def _planner(self, is_metric: bool, ahead: int, limit: int, v: float):
    from openpilot.sunnypilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlannerSP
    CP, CP_SP = _params()
    planner = LongitudinalPlannerSP(CP, CP_SP, mock.MagicMock())
    planner.resolver.distance = 0.   # (set by its update(), which this does not run)
    planner.sla.is_metric = is_metric
    unit = CV.KPH_TO_MS if is_metric else CV.MPH_TO_MS
    nc = planner.sla.now_confirm
    assert nc.update(0., map_ok=True, map_frame=1, ahead_valid=True, ahead_ms=ahead * unit, ahead_dist=30.,
                     nsw_state=2, car_ok=True, v_ego=v, standstill=False, limit=limit, is_metric=is_metric) == ahead
    return planner, unit

  def test_metric(self):
    planner, unit = self._planner(True, 50, 60, 60 * CV.KPH_TO_MS)
    planner.sla.now_offer = planner.sla.now_confirm.offer(60)
    a = self._publish(planner)
    assert (a.nowKey, a.nowLimit, a.nowTarget) == (-1, 50, 50), "the offer"
    assert (a.armedLimit, a.armedTarget, a.armedSpeed, a.armSeq) == (0, 0, 0., 0), "no arm, no flash"
    assert planner.sla.now_confirm.arm(0.1, 50, False)
    a = self._publish(planner)
    assert (a.armedLimit, a.armedTarget, a.armSeq) == (50, 50, 1)
    assert abs(a.armedSpeed - 50 * unit) < 1e-4

  def test_imperial(self):
    planner, unit = self._planner(False, 30, 40, 40 * CV.MPH_TO_MS)
    planner.sla.now_offer = planner.sla.now_confirm.offer(40)
    assert self._publish(planner).armedSpeed == 0.
    assert planner.sla.now_confirm.arm(0.1, 30, False)
    a = self._publish(planner)
    assert (a.armedLimit, a.armedTarget) == (30, 30) and abs(a.armedSpeed - 30 * CV.MPH_TO_MS) < 1e-4


class TestControlsdFlashesTheArmOnly(GraceTestBase):
  """controlsd_ext: CarControlSP.speedLimitNowFlash is the plan's armedSpeed - never its offer - while the plan is
  still arriving, held NOW_FLASH_HOLD_FRAMES cycles after it drops; it is set by state_control_ext itself."""
  @staticmethod
  def _ext():
    from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_steer_saturated import _ext
    return _ext()

  @staticmethod
  def _sm(plan, alive=True):
    from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_steer_saturated import _sm
    base = _sm(20.0, False, 0.0)
    return _SM({'longitudinalPlanSP': alive}, longitudinalPlanSP=plan, **base)

  def _flash(self, ext, plans, alive=True):
    return [float(ext.state_control_ext(self._sm(p, alive), None).speedLimitNowFlash) for p in plans]

  def test_the_offer_never_flashes(self):
    ext = self._ext()
    assert self._flash(ext, [_plan(-1, 50)] * 5) == [0.] * 5, "'press - to set 50' offered, nothing armed"

  def test_the_arm_flashes_and_is_held_after_it_drops(self):
    ext = self._ext()
    v = 50 * CV.KPH_TO_MS
    out = self._flash(ext, [_plan(-1, 50, armed_kph=50.)] * 3 + [_plan()] * (NOW_FLASH_HOLD_FRAMES + 2))
    assert [round(x, 4) for x in out] == [round(v, 4)] * (3 + NOW_FLASH_HOLD_FRAMES) + [0.] * 2, out

  def test_a_plan_no_longer_arriving_never_flashes(self):
    ext = self._ext()
    assert self._flash(ext, [_plan(-1, 50, armed_kph=50.)] * 3, alive=False) == [0.] * 3

  def test_a_capnp_default_plan(self):
    ext = self._ext()
    plan = messaging.new_message('longitudinalPlanSP').longitudinalPlanSP
    assert self._flash(ext, [plan]) == [0.]
    assert custom.CarControlSP.new_message().speedLimitNowFlash == 0.
