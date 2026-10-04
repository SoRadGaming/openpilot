"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): stock ACC mode (HondaElesysStockAcc) on the sunnypilot side - the param reaching opendbc's
hook, the longitudinal settings surviving a stock drive (honda_stock_acc.py), and the startup indication.
"""
from openpilot.cereal import custom, messaging
from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.car.structs import car
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from opendbc.sunnypilot.car.interfaces import setup_interfaces as opendbc_setup_interfaces
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.events import ET, Events
from openpilot.sunnypilot.selfdrive.car.car_specific import CarSpecificEventsSP, STOCK_ACC_ANNOUNCE_FRAMES
from openpilot.sunnypilot.selfdrive.car.honda_stock_acc import (PRESERVED_KEYS, SETTLE_FRAMES, SNAPSHOT_PARAM, STOCK_ACC_PARAM,
                                                               LongSettingsRestore, preserve_long_settings, stock_acc_active)
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


def ui_tick(params: Params):
  """One tick of the UI's params thread (ui_state.py, 5 Hz): CarParamsPersistent is read first, the deleting comes
  later in the same tick. Returns that second half, to be run whenever the race puts it."""
  cp_bytes = params.get("CarParamsPersistent")
  has_long = cp_bytes is not None and messaging.log_from_bytes(cp_bytes, car.CarParams).openpilotLongitudinalControl
  return (lambda: None) if has_long else (lambda: ui_deletes(params))


def reboot(params: Params) -> None:
  """manager_init, on every boot and every update install: each unset key that has a default gets it."""
  for key in params.all_keys():
    default = params.get_default_value(key)
    if default is not None and params.get(key) is None:
      params.put(key, default, block=True)


def drive_start(params: Params, stale_ui_at: int | None = None, settle: bool = True):
  """card's order: the hook, setup_interfaces (preserve, then the cleanup), CarParamsPersistent written, then the
  params thread at 10 Hz. stale_ui_at: a UI tick that read the previous drive's CarParams deletes only after that many
  params thread ticks of this drive. settle: the drive lasts long enough for LongSettingsRestore to finish."""
  stale = ui_tick(params)
  CP, CP_SP = car_params(params)
  preserve_long_settings(CP, CP_SP, params)
  _cleanup_unsupported_params(CP, CP_SP, params)
  cp_bytes = CP.to_bytes()
  params.put("CarParamsPersistent", cp_bytes, block=True)
  restore = LongSettingsRestore(CP, CP_SP, cp_bytes, params)
  for tick in range(SETTLE_FRAMES if settle else SETTLE_FRAMES - 1):
    if tick == stale_ui_at:
      stale()
    if tick % 2 == 0:
      ui_tick(params)()
    restore.update()
  return CP, CP_SP, restore


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

  def _stock(self, on: bool):
    self.params.put_bool(STOCK_ACC_PARAM, on, block=True)

  def test_a_stock_drive_and_back_keeps_every_setting(self):
    p = self.params
    self._stock(True)
    drive_start(p)
    # the stock drive: everything that needs openpilot long is gone or downgraded, and saved
    assert all(p.get(k) is None for k in OWNER if k != "SpeedLimitMode")
    assert p.get("SpeedLimitMode") == SpeedLimitMode.warning
    assert p.get(SNAPSHOT_PARAM) == OWNER
    # a second stock drive must not save the deleted state over the first snapshot
    drive_start(p)
    assert p.get(SNAPSHOT_PARAM) == OWNER

    self._stock(False)
    CP, _, restore = drive_start(p)
    assert CP.openpilotLongitudinalControl
    assert owner_values(p) == OWNER
    assert p.get(SNAPSHOT_PARAM) is None and not restore.pending

  def test_a_reboot_in_between_keeps_every_setting(self):
    # manager writes the default of every unset key at each start: a deleted bool comes back as False, not absent
    p = self.params
    self._stock(True)
    drive_start(p)
    reboot(p)
    assert p.get("DynamicExperimentalControl") is False
    drive_start(p)          # a second stock drive after the reboot saves nothing it deleted itself
    assert p.get(SNAPSHOT_PARAM) == OWNER
    reboot(p)
    self._stock(False)
    drive_start(p)
    assert owner_values(p) == OWNER

  def test_the_ui_deleting_late_cannot_win(self):
    # a UI tick that read the stock drive's CarParams and deletes after card's first restore, right up to the last
    # params thread tick before the settle: the settle puts everything back, and only then is the snapshot gone
    for stale_ui_at in (0, SETTLE_FRAMES // 2, SETTLE_FRAMES - 1):
      p = self.params
      for k, v in OWNER.items():
        p.put(k, v, block=True)
      self._stock(True)
      drive_start(p)
      self._stock(False)
      drive_start(p, stale_ui_at=stale_ui_at)
      assert owner_values(p) == OWNER, stale_ui_at
      assert p.get(SNAPSHOT_PARAM) is None

  def test_the_snapshot_is_kept_until_card_sees_its_own_car_params(self):
    p = self.params
    self._stock(True)
    drive_start(p)
    self._stock(False)
    CP, CP_SP = car_params(p)
    preserve_long_settings(CP, CP_SP, p)
    cp_bytes = CP.to_bytes()
    restore = LongSettingsRestore(CP, CP_SP, cp_bytes, p)
    assert restore.pending
    for _ in range(3 * SETTLE_FRAMES):      # CarParamsPersistent still the stock drive's (the write has not landed)
      restore.update()
    assert restore.pending and p.get(SNAPSHOT_PARAM) == OWNER
    p.put("CarParamsPersistent", cp_bytes, block=True)
    for _ in range(SETTLE_FRAMES - 1):
      restore.update()
    assert restore.pending and p.get(SNAPSHOT_PARAM) == OWNER
    ui_deletes(p)
    restore.update()
    assert not restore.pending and p.get(SNAPSHOT_PARAM) is None
    assert owner_values(p) == OWNER

  def test_a_drive_that_ends_before_the_settle_keeps_the_snapshot(self):
    p = self.params
    self._stock(True)
    drive_start(p)
    self._stock(False)
    drive_start(p, stale_ui_at=0, settle=False)
    assert p.get(SNAPSHOT_PARAM) == OWNER
    reboot(p)
    drive_start(p)
    assert owner_values(p) == OWNER
    assert p.get(SNAPSHOT_PARAM) is None

  def test_a_left_over_snapshot_is_refreshed_at_the_next_stock_start(self):
    # the snapshot of a drive that ended before the settle must not undo a setting changed since
    p = self.params
    self._stock(True)
    drive_start(p)
    self._stock(False)
    drive_start(p, settle=False)
    p.put_bool("SmartCruiseControlMap", True, block=True)
    p.put_bool("ExperimentalMode", False, block=True)
    self._stock(True)
    drive_start(p)
    self._stock(False)
    drive_start(p)
    assert owner_values(p) == {**OWNER, "SmartCruiseControlMap": True, "ExperimentalMode": False}

  def test_toggle_off_with_no_stock_drive_touches_nothing(self):
    _, _, restore = drive_start(self.params)
    assert not restore.pending
    assert owner_values(self.params) == OWNER
    assert self.params.get(SNAPSHOT_PARAM) is None

  def test_a_choice_made_in_between_stands(self):
    p = self.params
    self._stock(True)
    drive_start(p)
    p.put("SpeedLimitMode", int(SpeedLimitMode.off), block=True)         # turned off during the stock drive: not assist again
    self._stock(False)
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
    self._stock(True)
    CP, CP_SP, _ = drive_start(p)
    self._stock(False)
    CP.openpilotLongitudinalControl = False
    CP_SP.flags &= ~HondaFlagsSP.ELESYS_STOCK_ACC.value
    preserve_long_settings(CP, CP_SP, p)
    restore = LongSettingsRestore(CP, CP_SP, b"", p)
    assert not restore.pending
    assert p.get(SNAPSHOT_PARAM) == OWNER
    drive_start(p)
    assert owner_values(p) == OWNER

  def test_absent_settings_stay_absent(self):
    p = self.params
    p.remove("ExperimentalMode")
    p.remove("SpeedLimitMode")
    self._stock(True)
    drive_start(p)
    self._stock(False)
    drive_start(p)
    assert p.get("ExperimentalMode") is None
    assert p.get("SpeedLimitMode") is None

  def test_a_broken_snapshot_never_stops_card(self):
    # card's startup must not depend on this feature: a snapshot it cannot use is dropped, at either step
    p = self.params
    self._stock(False)
    for broken in ({"ExperimentalMode": [1, 2]}, ["not", "a", "dict"], {"SpeedLimitMode": "assist"}):
      p.remove("ExperimentalMode")
      p.put(SNAPSHOT_PARAM, broken, block=True)
      CP, CP_SP = car_params(p)
      preserve_long_settings(CP, CP_SP, p)
      assert p.get(SNAPSHOT_PARAM) in (None, broken), broken
      p.put(SNAPSHOT_PARAM, broken, block=True)
      cp_bytes = CP.to_bytes()
      p.put("CarParamsPersistent", cp_bytes, block=True)
      restore = LongSettingsRestore(CP, CP_SP, cp_bytes, p)
      for _ in range(SETTLE_FRAMES):
        restore.update()
      assert not restore.pending
      assert p.get(SNAPSHOT_PARAM) in (None, broken) and not isinstance(p.get(SNAPSHOT_PARAM), dict), broken
    p.put(SNAPSHOT_PARAM, {"ExperimentalMode": [1, 2]}, block=True)
    CP, CP_SP = car_params(p)
    preserve_long_settings(CP, CP_SP, p)
    assert p.get(SNAPSHOT_PARAM) is None, "an unusable snapshot is dropped, not retried at every start"


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
