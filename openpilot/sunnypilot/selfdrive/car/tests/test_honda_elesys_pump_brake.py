"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): the brake pump rule C1b (HondaElesysPumpC1b, "Quiet pump at stops", on by default) reaching
opendbc's _initialize_honda the way card hands it over, as CarParamsSP.flags ELESYS_PUMP_C1B (64). The retired rule C1's
key (HondaElesysPumpV6) and flag (16), and the retired brake law v2's key (HondaElesysBrakeLawV2, removed 2026-10-09) and
flag (32), are never handed over or set.
"""
import os

from opendbc.car import gen_empty_fingerprint
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from opendbc.sunnypilot.car.interfaces import setup_interfaces as opendbc_setup_interfaces
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.car.interfaces import initialize_params

PUMP = "HondaElesysPumpC1b"
RETIRED_KEYS = ("HondaElesysPumpV6", "HondaElesysBrakeLawV2")
PUMP_BIT = HondaFlagsSP.ELESYS_PUMP_C1B.value
RETIRED_BITS = HondaFlagsSP.ELESYS_PUMP_V6.value | HondaFlagsSP.ELESYS_BRAKE_LAW_V2.value


def car_params(params: Params, car=CAR.HONDA_ACCORD_9G_AU):
  """What card gets from get_car: get_params, get_params_sp, then opendbc's hooks with initialize_params()."""
  fp = gen_empty_fingerprint()
  fp[0][0x188] = 8
  fp[0][0x201] = 6
  CP = CarInterface.get_params(car, fp, [], False, False, False)
  CP_SP = CarInterface.get_params_sp(CP, car, fp, [], False, False, False)
  opendbc_setup_interfaces(CarInterface, CP, CP_SP, initialize_params(params))
  return CP, CP_SP


def write_retired(params: Params, value: bytes) -> None:
  """The retired keys' files as an older build left them on the device: unregistered, so written by hand."""
  folder = os.path.dirname(params.get_param_path(PUMP))
  for key in RETIRED_KEYS:
    with open(os.path.join(folder, key), "wb") as f:
      f.write(value)


class TestElesysPumpBrakeLawPlumbing(OpenpilotTestCase):
  def test_default_c1b_on(self):
    params = Params()
    handed = initialize_params(params)
    assert {PUMP: True} in handed
    assert not any(k in p for p in handed for k in RETIRED_KEYS), "the retired keys are never handed over"
    CP, CP_SP = car_params(params)
    assert CP.openpilotLongitudinalControl
    assert CP_SP.flags & (PUMP_BIT | RETIRED_BITS) == PUMP_BIT

  def test_the_retired_keys_are_not_registered(self):
    keys = {k.decode() if isinstance(k, bytes) else k for k in Params().all_keys()}
    assert PUMP in keys
    assert not set(RETIRED_KEYS) & keys

  def test_c1b_is_on_whatever_the_retired_keys_say(self):
    # the owner switched "Quieter brake pump" (C1) off, or "Measured brake law (testing)" on: the files stay on the
    # device, unregistered and unread, and the pump key starts at its default - on
    params = Params()
    for value in (b"0", b"1"):
      write_retired(params, value)
      _, CP_SP = car_params(params)
      assert CP_SP.flags & (PUMP_BIT | RETIRED_BITS) == PUMP_BIT, value

  def test_the_setting_moves_its_own_bit(self):
    params = Params()
    write_retired(params, b"1")
    for pump in (False, True, False):
      params.put_bool(PUMP, pump, block=True)
      _, CP_SP = car_params(params)
      assert bool(CP_SP.flags & PUMP_BIT) == pump and not CP_SP.flags & RETIRED_BITS, pump

  def test_off_matches_a_drive_without_the_setting(self):
    # with it off, CarParams and CarParamsSP are exactly what the hook produced before this setting existed
    params = Params()
    params.put_bool(PUMP, False, block=True)
    CP, CP_SP = car_params(params)
    fp = gen_empty_fingerprint()
    fp[0][0x188] = 8
    fp[0][0x201] = 6
    base = CarInterface.get_params(CAR.HONDA_ACCORD_9G_AU, fp, [], False, False, False)
    base_sp = CarInterface.get_params_sp(base, CAR.HONDA_ACCORD_9G_AU, fp, [], False, False, False)
    without = [p for p in initialize_params(params) if PUMP not in p]
    opendbc_setup_interfaces(CarInterface, base, base_sp, without)
    assert CP.to_bytes() == base.to_bytes()
    assert repr(CP_SP) == repr(base_sp)

  def test_never_in_stock_acc_mode(self):
    params = Params()
    params.put_bool("HondaElesysStockAcc", True, block=True)
    CP, CP_SP = car_params(params)
    assert not CP.openpilotLongitudinalControl
    assert CP_SP.flags & (PUMP_BIT | RETIRED_BITS) == 0

  def test_other_hondas_never_get_the_bit(self):
    params = Params()
    params.put_bool(PUMP, True, block=True)
    _, CP_SP = car_params(params, CAR.HONDA_CIVIC)
    assert CP_SP.flags & (PUMP_BIT | RETIRED_BITS) == 0
