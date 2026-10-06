"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): the brake pump rule C1b (HondaElesysPumpC1b, "Quiet pump at stops", on by default) and the
brake law (HondaElesysBrakeLawV2, off by default) reaching opendbc's _initialize_honda the way card hands them over, as
CarParamsSP.flags ELESYS_PUMP_C1B (64) and ELESYS_BRAKE_LAW_V2 (32). The retired rule C1's key (HondaElesysPumpV6) and
flag (16) are never handed over or set.
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
LAW = "HondaElesysBrakeLawV2"
PUMP_BIT = HondaFlagsSP.ELESYS_PUMP_C1B.value
RETIRED_BIT = HondaFlagsSP.ELESYS_PUMP_V6.value
LAW_BIT = HondaFlagsSP.ELESYS_BRAKE_LAW_V2.value


def car_params(params: Params, car=CAR.HONDA_ACCORD_9G_AU):
  """What card gets from get_car: get_params, get_params_sp, then opendbc's hooks with initialize_params()."""
  fp = gen_empty_fingerprint()
  fp[0][0x188] = 8
  fp[0][0x201] = 6
  CP = CarInterface.get_params(car, fp, [], False, False, False)
  CP_SP = CarInterface.get_params_sp(CP, car, fp, [], False, False, False)
  opendbc_setup_interfaces(CarInterface, CP, CP_SP, initialize_params(params))
  return CP, CP_SP


class TestElesysPumpBrakeLawPlumbing(OpenpilotTestCase):
  def test_defaults_c1b_on_brake_law_off(self):
    params = Params()
    handed = initialize_params(params)
    assert {PUMP: True} in handed and {LAW: False} in handed
    assert not any("HondaElesysPumpV6" in p for p in handed), "the retired C1 key is never handed over"
    CP, CP_SP = car_params(params)
    assert CP.openpilotLongitudinalControl
    assert CP_SP.flags & (PUMP_BIT | LAW_BIT | RETIRED_BIT) == PUMP_BIT

  def test_c1b_is_on_whatever_the_retired_key_says(self):
    # the owner switched "Quieter brake pump" (C1) off: its file stays on the device, unregistered and unread, and the
    # new key starts at its default - on
    params = Params()
    with open(os.path.join(os.path.dirname(params.get_param_path(PUMP)), "HondaElesysPumpV6"), "wb") as f:
      f.write(b"0")
    _, CP_SP = car_params(params)
    assert CP_SP.flags & (PUMP_BIT | RETIRED_BIT) == PUMP_BIT

  def test_each_setting_moves_its_own_bit(self):
    params = Params()
    for pump, law in ((False, False), (False, True), (True, True), (True, False)):
      params.put_bool(PUMP, pump, block=True)
      params.put_bool(LAW, law, block=True)
      _, CP_SP = car_params(params)
      assert bool(CP_SP.flags & PUMP_BIT) == pump and bool(CP_SP.flags & LAW_BIT) == law, (pump, law)

  def test_both_off_matches_a_drive_without_the_settings(self):
    # with both off, CarParams and CarParamsSP are exactly what the hook produced before these settings existed
    params = Params()
    params.put_bool(PUMP, False, block=True)
    params.put_bool(LAW, False, block=True)
    CP, CP_SP = car_params(params)
    fp = gen_empty_fingerprint()
    fp[0][0x188] = 8
    fp[0][0x201] = 6
    base = CarInterface.get_params(CAR.HONDA_ACCORD_9G_AU, fp, [], False, False, False)
    base_sp = CarInterface.get_params_sp(base, CAR.HONDA_ACCORD_9G_AU, fp, [], False, False, False)
    without = [p for p in initialize_params(params) if not ({PUMP, LAW} & set(p))]
    opendbc_setup_interfaces(CarInterface, base, base_sp, without)
    assert CP.to_bytes() == base.to_bytes()
    assert repr(CP_SP) == repr(base_sp)

  def test_never_in_stock_acc_mode(self):
    params = Params()
    params.put_bool("HondaElesysStockAcc", True, block=True)
    params.put_bool(LAW, True, block=True)
    CP, CP_SP = car_params(params)
    assert not CP.openpilotLongitudinalControl
    assert CP_SP.flags & (PUMP_BIT | LAW_BIT | RETIRED_BIT) == 0

  def test_other_hondas_never_get_the_bits(self):
    params = Params()
    params.put_bool(LAW, True, block=True)
    _, CP_SP = car_params(params, CAR.HONDA_CIVIC)
    assert CP_SP.flags & (PUMP_BIT | LAW_BIT) == 0
