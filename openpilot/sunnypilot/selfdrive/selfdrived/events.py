"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import openpilot.cereal.messaging as messaging
from openpilot.cereal import log, custom
from opendbc.car.structs import car
from openpilot.common.constants import CV
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import EventsBase, Priority, ET, Alert, \
  NoEntryAlert, ImmediateDisableAlert, EngagementAlert, NormalPermanentAlert, AlertCallbackType, wrong_car_mode_alert
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit import PCM_LONG_REQUIRED_MAX_SET_SPEED, CONFIRM_SPEED_THRESHOLD
from openpilot.common.hardware import HARDWARE

AlertSize = log.SelfdriveState.AlertSize
AlertStatus = log.SelfdriveState.AlertStatus
VisualAlert = car.CarControl.HUDControl.VisualAlert
AudibleAlert = car.CarControl.HUDControl.AudibleAlert
AudibleAlertSP = custom.SelfdriveStateSP.AudibleAlert
EventNameSP = custom.OnroadEventSP.EventName


# get event name from enum
EVENT_NAME_SP = {v: k for k, v in EventNameSP.schema.enumerants.items()}

IS_MICI = HARDWARE.get_device_type() == 'mici'


def speed_limit_adjust_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  speedLimit = sm['longitudinalPlanSP'].speedLimit.resolver.speedLimit
  speed = round(speedLimit * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH))
  message = f'Adjusting to {speed} {"km/h" if metric else "mph"} speed limit'
  return Alert(
    message,
    "",
    AlertStatus.normal, AlertSize.small,
    Priority.LOW, VisualAlert.none, AudibleAlert.none, 4.)


def speed_limit_pre_active_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  speed_conv = CV.MS_TO_KPH if metric else CV.MS_TO_MPH
  v_cruise_cluster = CS.vCruiseCluster
  set_speed = sm['controlsState'].deprecated.vCruise if v_cruise_cluster == 0.0 else v_cruise_cluster
  set_speed_conv = round(set_speed * speed_conv)

  speed_limit_final_last = sm['longitudinalPlanSP'].speedLimit.resolver.speedLimitFinalLast
  speed_limit_final_last_conv = round(speed_limit_final_last * speed_conv)
  alert_1_str = ""
  alert_size = AlertSize.small

  if CP.openpilotLongitudinalControl and CP.pcmCruise:
    # PCM long
    cst_low, cst_high = PCM_LONG_REQUIRED_MAX_SET_SPEED[metric]
    pcm_long_required_max = cst_low if speed_limit_final_last_conv < CONFIRM_SPEED_THRESHOLD[metric] else cst_high
    pcm_long_required_max_set_speed_conv = round(pcm_long_required_max * speed_conv)
    speed_unit = "km/h" if metric else "mph"

    alert_1_str = f"Speed Limit Assist: set to {pcm_long_required_max_set_speed_conv} {speed_unit} to engage"
  else:
    if IS_MICI:
      if set_speed_conv < speed_limit_final_last_conv:
        alert_1_str = "Press + to confirm speed limit"
      elif set_speed_conv > speed_limit_final_last_conv:
        alert_1_str = "Press - to confirm speed limit"
    else:
      alert_size = AlertSize.none

  return Alert(
    alert_1_str,
    "",
    AlertStatus.normal, alert_size,
    Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleLow, .1)


# FORK(HONDA_ACCORD_9G_AU): the speed the VSA's stored fault cleared at on route 113 (35.3 km/h; one observation - route
# 111 never moved), for the texts
VSA_CLEAR_SPEED_TEXT = {True: "35 km/h", False: "22 mph"}
VSA_LIVE_NO_ENTRY_TEXT = "VSA Fault: Brakes Degraded"
VSA_LIVE_BANNER_TEXT = ("VSA Fault", "Brakes, ACC, CMBS degraded. Have VSA codes read")


def _vsa_fault_live(sm) -> bool:
  """carStateSP.vsaFault, the live bits. vsaStoredFault's event also carries a live fault that upstream's accFaulted is
  not raised beside (CAN invalid, so no car events; or live bits without BRAKE_ERROR, never seen): then the live texts."""
  try:
    return bool(sm['carStateSP'].vsaFault)
  except Exception:
    return False


def vsa_stored_fault_no_entry_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  if _vsa_fault_live(sm):
    return NoEntryAlert(VSA_LIVE_NO_ENTRY_TEXT)
  return NoEntryAlert(f"VSA Fault: Clears Above {VSA_CLEAR_SPEED_TEXT[bool(metric)]}")


def vsa_stored_fault_permanent_alert(CP: car.CarParams, CS: car.CarState, sm: messaging.SubMaster, metric: bool, soft_disable_time: int, personality) -> Alert:
  text1, text2 = VSA_LIVE_BANNER_TEXT if _vsa_fault_live(sm) else \
    ("VSA Fault Stored", f"Clears after driving above {VSA_CLEAR_SPEED_TEXT[bool(metric)]}")
  return Alert(
    text1, text2,
    AlertStatus.normal, AlertSize.mid,
    Priority.LOWER, VisualAlert.none, AudibleAlert.none, .2)


class EventsSP(EventsBase):
  def __init__(self):
    super().__init__()
    self.event_counters = dict.fromkeys(EVENTS_SP.keys(), 0)

  def get_events_mapping(self) -> dict[int, dict[str, Alert | AlertCallbackType]]:
    return EVENTS_SP

  def get_event_name(self, event: int):
    return EVENT_NAME_SP[event]

  def get_event_msg_type(self):
    return custom.OnroadEventSP.Event


EVENTS_SP: dict[int, dict[str, Alert | AlertCallbackType]] = {
  # sunnypilot
  EventNameSP.lkasEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.engage),
  },

  EventNameSP.lkasDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.disengage),
  },

  EventNameSP.manualSteeringRequired: {
    ET.USER_DISABLE: Alert(
      "Automatic Lane Centering is OFF",
      "Manual Steering Required",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.disengage, 1.),
  },

  EventNameSP.manualLongitudinalRequired: {
    ET.WARNING: Alert(
      "Smart/Adaptive Cruise Control: OFF",
      "Manual Speed Control Required",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },

  EventNameSP.silentLkasEnable: {
    ET.ENABLE: EngagementAlert(AudibleAlert.none),
  },

  EventNameSP.silentLkasDisable: {
    ET.USER_DISABLE: EngagementAlert(AudibleAlert.none),
  },

  EventNameSP.silentBrakeHold: {
    ET.WARNING: EngagementAlert(AudibleAlert.none),
    ET.NO_ENTRY: NoEntryAlert("Brake Hold Active"),
  },

  EventNameSP.silentWrongGear: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: Alert(
      "Gear not D",
      "openpilot Unavailable",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 0.),
  },

  EventNameSP.silentReverseGear: {
    ET.PERMANENT: Alert(
      "Reverse\nGear",
      "",
      AlertStatus.normal, AlertSize.full,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, .2, creation_delay=0.5),
    ET.NO_ENTRY: NoEntryAlert("Reverse Gear"),
  },

  EventNameSP.silentDoorOpen: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: NoEntryAlert("Door Open"),
  },

  EventNameSP.silentSeatbeltNotLatched: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: NoEntryAlert("Seatbelt Unlatched"),
  },

  EventNameSP.silentParkBrake: {
    ET.WARNING: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 0.),
    ET.NO_ENTRY: NoEntryAlert("Parking Brake Engaged"),
  },

  EventNameSP.controlsMismatchLateral: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Controls Mismatch: Lateral"),
    ET.NO_ENTRY: NoEntryAlert("Controls Mismatch: Lateral"),
  },

  EventNameSP.experimentalModeSwitched: {
    ET.WARNING: NormalPermanentAlert("Experimental Mode Switched", duration=1.5)
  },

  EventNameSP.wrongCarModeAlertOnly: {
    ET.WARNING: wrong_car_mode_alert,
  },

  EventNameSP.pedalPressedAlertOnly: {
    ET.WARNING: NoEntryAlert("Pedal Pressed")
  },

  EventNameSP.laneTurnLeft: {
    ET.WARNING: Alert(
      "Turning Left",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },

  EventNameSP.laneTurnRight: {
    ET.WARNING: Alert(
      "Turning Right",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },

  EventNameSP.speedLimitActive: {
    ET.WARNING: Alert(
      "Auto adjusting to speed limit",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 5.),
  },

  EventNameSP.speedLimitChanged: {
    ET.WARNING: Alert(
      "Set speed changed",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 5.),
  },

  EventNameSP.speedLimitPreActive: {
    ET.WARNING: speed_limit_pre_active_alert,
  },

  EventNameSP.speedLimitPending: {
    ET.WARNING: Alert(
      "Auto adjusting to last speed limit",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlertSP.promptSingleHigh, 5.),
  },

  EventNameSP.e2eChime: {
    ET.PERMANENT: Alert(
      "",
      "",
      AlertStatus.normal, AlertSize.none,
      Priority.MID, VisualAlert.none, AudibleAlert.prompt, 3.),
  },

  EventNameSP.laneChangeRoadEdge: {
    ET.WARNING: Alert(
      "Lane Change Unavailable: Road Edge",
      "",
      AlertStatus.userPrompt, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 0.1),
  },

  EventNameSP.bigModelReady: {
    ET.PERMANENT: Alert(
      "Big Model Ready",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 2.),
  },

  # FORK(LKAS-GATEWAY): the EPS latched until key-off. ET.WARNING only - no NO_ENTRY or any disable type,
  # so longitudinal and engagement are untouched. Raised by eps_latch_alert.py: the first once per latch
  # with one prompt, then the silent reminder at most every 5 minutes. Priority.LOW, below driver
  # monitoring's stage 2 (MID): a tie goes to the newer alert, so MID would let a latch hide it for 6 s.
  EventNameSP.lkasGatewayEpsLatched: {
    ET.WARNING: Alert(
      "Steering Fault",
      "Turn the car off and on to clear it",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 6.),
  },

  EventNameSP.lkasGatewayEpsLatchedReminder: {
    ET.WARNING: Alert(
      "Steering Off Until Restart",
      "",
      AlertStatus.normal, AlertSize.small,
      Priority.LOWEST, VisualAlert.none, AudibleAlert.none, 4.),
  },

  # FORK(HONDA_ACCORD_9G_AU): the VSA's own fault, raised by vsa_fault_alert.py, which also drops the upstream
  # alerts these replace ("Cruise Fault: Restart the Car" - a restart does not clear it). vsaFault is raised only
  # beside upstream's accFaulted, which keeps doing the disengaging, so its IMMEDIATE_DISABLE/NO_ENTRY add nothing
  # accFaulted does not already do; it only names the cause. vsaStoredFault refuses nothing by itself either:
  # carNotReady, beside it in `events`, is what selfdrived's state machine reads; it is also the event for a live
  # fault without accFaulted (CAN invalid), and then shows the live texts (_vsa_fault_live). The banners are silent and
  # Priority.LOWER (upstream's level for accFaulted's own banner); the one sound is vsaFaultAnnounce's, once per
  # fault, 3.5 s - shorter than a disengagement alert, so a live onset that disengages plays only that one.
  EventNameSP.vsaFault: {
    ET.IMMEDIATE_DISABLE: ImmediateDisableAlert("Stability Control (VSA) Fault"),
    ET.NO_ENTRY: NoEntryAlert(VSA_LIVE_NO_ENTRY_TEXT),
    ET.PERMANENT: Alert(
      *VSA_LIVE_BANNER_TEXT,
      AlertStatus.normal, AlertSize.mid,
      Priority.LOWER, VisualAlert.none, AudibleAlert.none, .2),
  },

  EventNameSP.vsaStoredFault: {
    ET.NO_ENTRY: vsa_stored_fault_no_entry_alert,
    ET.PERMANENT: vsa_stored_fault_permanent_alert,
  },

  EventNameSP.vsaFaultAnnounce: {
    ET.PERMANENT: Alert(
      "Stability Control Fault",
      "Brakes, ACC, CMBS degraded",
      AlertStatus.userPrompt, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.prompt, 3.5),
  },

  # FORK(HONDA_ACCORD_9G_AU): stock ACC mode (HondaElesysStockAcc), raised by car_specific.py for the first seconds of
  # every drive in that mode only. PERMANENT and silent: it shows engaged or not, and refuses or disengages nothing.
  EventNameSP.hondaElesysStockAcc: {
    ET.PERMANENT: Alert(
      "Stock ACC Mode",
      "Car's cruise does gas and brake, openpilot steers",
      AlertStatus.normal, AlertSize.mid,
      Priority.LOW, VisualAlert.none, AudibleAlert.none, 1.),
  },
}
