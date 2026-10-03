"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from openpilot.cereal import log, custom
from opendbc.car import structs

from opendbc.car.chrysler.values import RAM_DT
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP  # FORK(HONDA_ACCORD_9G_AU): stock ACC mode
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName
GearShifter = structs.CarState.GearShifter

# FORK(HONDA_ACCORD_9G_AU): how long stock ACC mode names itself on screen at the start of a drive
STOCK_ACC_ANNOUNCE_FRAMES = int(5. / DT_CTRL)


class CarSpecificEventsSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP):
    self.CP = CP
    self.CP_SP = CP_SP

    self.low_speed_alert = False
    # FORK(HONDA_ACCORD_9G_AU): stock ACC mode (HondaElesysStockAcc) only; 0 on every other car and with the toggle off
    stock_acc = CP.brand == 'honda' and bool(CP_SP.flags & HondaFlagsSP.ELESYS_STOCK_ACC)
    self.stock_acc_announce = STOCK_ACC_ANNOUNCE_FRAMES if stock_acc else 0

  def update(self, CS: structs.CarState, events: Events):
    events_sp = EventsSP()

    if self.CP.brand == 'chrysler':
      if self.CP.carFingerprint in RAM_DT:
        # remove belowSteerSpeed event from CarSpecificEvents as RAM_DT uses a different logic
        if events.has(EventName.belowSteerSpeed):
          events.remove(EventName.belowSteerSpeed)

        # TODO-SP: use if/elif to have the gear shifter condition takes precedence over the speed condition
        # TODO-SP: add 1 m/s hysteresis
        if CS.vEgo >= self.CP.minEnableSpeed:
          self.low_speed_alert = False
        if self.CP.minEnableSpeed >= 14.5 and CS.gearShifter != GearShifter.drive:
          self.low_speed_alert = True
      if self.low_speed_alert:
        events.add(EventName.belowSteerSpeed)

    elif self.CP.brand == 'toyota':
      if self.CP.openpilotLongitudinalControl:
        if CS.cruiseState.standstill and not CS.brakePressed and self.CP_SP.enableGasInterceptor:
          if events.has(EventName.resumeRequired):
            events.remove(EventName.resumeRequired)

    # FORK(HONDA_ACCORD_9G_AU): the startup indication of stock ACC mode, so the screen says which mode is running
    if self.stock_acc_announce > 0:
      self.stock_acc_announce -= 1
      events_sp.add(EventNameSP.hondaElesysStockAcc)

    return events_sp
