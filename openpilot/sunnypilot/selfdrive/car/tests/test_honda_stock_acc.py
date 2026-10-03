"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): stock ACC mode (HondaElesysStockAcc) on the sunnypilot side - the param reaching opendbc's
hook, the longitudinal settings surviving a stock drive (honda_stock_acc.py), and the startup indication.
"""
from openpilot.cereal import custom
from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from opendbc.sunnypilot.car.interfaces import setup_interfaces as opendbc_setup_interfaces
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.events import ET, Events
from openpilot.sunnypilot.selfdrive.car.car_specific import CarSpecificEventsSP, STOCK_ACC_ANNOUNCE_FRAMES
from openpilot.sunnypilot.selfdrive.car.honda_stock_acc import (PRESERVED_KEYS, SNAPSHOT_PARAM, STOCK_ACC_PARAM,
                                                               finish_long_settings_restore, preserve_long_settings,
                                                               stock_acc_active)
from openpilot.sunnypilot.selfdrive.car.interfaces import _cleanup_unsupported_params, initialize_params
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode as SpeedLimitMode
from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP, AlertStatus, AudibleAlert

EventNameSP = custom.OnroadEventSP.EventName

OWNER = {
  "ExperimentalMode": True,
  "DynamicExperimentalControl": True,
  "CustomAccIncrementsEnabled": True,
  "SmartCruiseControlVision": True,
  "SmartCruiseControlMap": False,
  "SpeedLimitMode": int(SpeedLimitMode.assist),
}


def car_params(params: Params, car=CAR.HONDA_ACCORD_9G_AU):
  """What card gets from get_car: get_params, get_params_sp, then opendbc's hooks with initialize_params()."""
  fp = gen_empty_fingerprint()
  fp[0][0x188] = 8
  fp[0][0x201] = 6
  CP = CarInterface.get_params(car, fp, [], False, False, False)
  CP_SP = CarInterface.get_params_sp(CP, car, fp, [], False, False, False)
  opendbc_setup_interfaces(CarInterface, CP, CP_SP, initialize_params(params))
  return CP, CP_SP


def ui_deletes(params: Params) -> None:
  """selfdrived and the UI (ui_state._enforce_constraints) while CarParams say openpilot long is off."""
  for key in ("ExperimentalMode", "DynamicExperimentalControl", "CustomAccIncrementsEnabled",
              "SmartCruiseControlVision", "SmartCruiseControlMap"):
    params.remove(key)


def drive_start(params: Params, stale_ui: bool = False):
  """card's order: the hook, setup_interfaces (preserve, then the cleanup), CarParams written, then the finish.
  stale_ui: the UI, still on the previous drive's CarParams, deletes in between."""
  CP, CP_SP = car_params(params)
  preserve_long_settings(CP, CP_SP, params)
  _cleanup_unsupported_params(CP, CP_SP, params)
  if not CP.openpilotLongitudinalControl or stale_ui:
    ui_deletes(params)
  finish_long_settings_restore(CP, CP_SP, params)
  return CP, CP_SP


def owner_values(params: Params) -> dict:
  return {k: params.get(k) for k in PRESERVED_KEYS}


class TestStockAccParamPlumbing(OpenpilotTestCase):
  def test_the_param_reaches_the_hook(self):
    params = Params()
    assert {STOCK_ACC_PARAM: False} in initialize_params(params)
    CP, CP_SP = car_params(params)
    assert CP.openpilotLongitudinalControl and not stock_acc_active(CP, CP_SP)
    params.put_bool(STOCK_ACC_PARAM, True, block=True)
    CP, CP_SP = car_params(params)
    assert not CP.openpilotLongitudinalControl and stock_acc_active(CP, CP_SP)
    assert CP.safetyConfigs[-1].safetyParam == 68

  def test_other_cars_never_count_as_stock_acc(self):
    params = Params()
    params.put_bool(STOCK_ACC_PARAM, True, block=True)
    CP, CP_SP = car_params(params, CAR.HONDA_CIVIC)
    assert not stock_acc_active(CP, CP_SP)


class TestStockAccSettings(OpenpilotTestCase):
  def setup_method(self):
    self.params = Params()
    for k, v in OWNER.items():
      self.params.put(k, v, block=True)

  def test_a_stock_drive_and_back_keeps_every_setting(self):
    p = self.params
    p.put_bool(STOCK_ACC_PARAM, True, block=True)
    drive_start(p)
    # the stock drive: everything that needs openpilot long is gone or downgraded, and saved
    assert all(p.get(k) is None for k in OWNER if k != "SpeedLimitMode")
    assert p.get("SpeedLimitMode") == SpeedLimitMode.warning
    assert p.get(SNAPSHOT_PARAM) == OWNER
    # a second stock drive must not save the deleted state over the first snapshot
    drive_start(p)
    assert p.get(SNAPSHOT_PARAM) == OWNER

    p.put_bool(STOCK_ACC_PARAM, False, block=True)
    CP, _ = drive_start(p, stale_ui=True)    # the UI races the restore, and loses
    assert CP.openpilotLongitudinalControl
    assert owner_values(p) == OWNER
    assert p.get(SNAPSHOT_PARAM) is None

  def test_toggle_off_with_no_stock_drive_touches_nothing(self):
    drive_start(self.params)
    assert owner_values(self.params) == OWNER
    assert self.params.get(SNAPSHOT_PARAM) is None

  def test_a_choice_made_in_between_stands(self):
    p = self.params
    p.put_bool(STOCK_ACC_PARAM, True, block=True)
    drive_start(p)
    p.put("SpeedLimitMode", int(SpeedLimitMode.off), block=True)         # turned off during the stock drive: not assist again
    p.put_bool(STOCK_ACC_PARAM, False, block=True)
    drive_start(p)
    assert p.get("SpeedLimitMode") == SpeedLimitMode.off
    # and after the restore the snapshot is gone, so a later change is never reverted
    p.put_bool("ExperimentalMode", False, block=True)
    p.put("SpeedLimitMode", int(SpeedLimitMode.warning), block=True)
    drive_start(p)
    assert p.get_bool("ExperimentalMode") is False
    assert p.get("SpeedLimitMode") == SpeedLimitMode.warning

  def test_a_drive_without_openpilot_long_keeps_the_snapshot(self):
    # a dashcam or unrecognized start would only delete them again: wait for a start with openpilot long
    p = self.params
    p.put_bool(STOCK_ACC_PARAM, True, block=True)
    CP, CP_SP = drive_start(p)
    p.put_bool(STOCK_ACC_PARAM, False, block=True)
    CP.openpilotLongitudinalControl = False
    CP_SP.flags &= ~HondaFlagsSP.ELESYS_STOCK_ACC.value
    preserve_long_settings(CP, CP_SP, p)
    finish_long_settings_restore(CP, CP_SP, p)
    assert p.get(SNAPSHOT_PARAM) == OWNER
    drive_start(p)
    assert owner_values(p) == OWNER

  def test_absent_settings_stay_absent(self):
    p = self.params
    p.remove("ExperimentalMode")
    p.remove("SpeedLimitMode")
    p.put_bool(STOCK_ACC_PARAM, True, block=True)
    drive_start(p)
    p.put_bool(STOCK_ACC_PARAM, False, block=True)
    drive_start(p)
    assert p.get("ExperimentalMode") is None
    assert p.get("SpeedLimitMode") is None


class TestStockAccStartupIndication(OpenpilotTestCase):
  def _events(self, CP, CP_SP, frames):
    car_events = CarSpecificEventsSP(CP, CP_SP)
    return [car_events.update(None, Events()).has(EventNameSP.hondaElesysStockAcc) for _ in range(frames)]

  def test_shown_at_the_start_of_a_stock_drive_only(self):
    params = Params()
    params.put_bool(STOCK_ACC_PARAM, True, block=True)
    CP, CP_SP = car_params(params)
    seen = self._events(CP, CP_SP, STOCK_ACC_ANNOUNCE_FRAMES + 100)
    assert seen == [True] * STOCK_ACC_ANNOUNCE_FRAMES + [False] * 100
    params.put_bool(STOCK_ACC_PARAM, False, block=True)
    CP, CP_SP = car_params(params)
    assert not any(self._events(CP, CP_SP, STOCK_ACC_ANNOUNCE_FRAMES + 100))

  def test_the_alert_is_a_quiet_banner(self):
    alerts = EVENTS_SP[EventNameSP.hondaElesysStockAcc]
    assert set(alerts) == {ET.PERMANENT}, "it must never refuse, disengage or warn"
    alert = alerts[ET.PERMANENT]
    assert alert.alert_text_1 == "Stock ACC Mode"
    assert alert.alert_status == AlertStatus.normal
    assert alert.audible_alert == AudibleAlert.none
