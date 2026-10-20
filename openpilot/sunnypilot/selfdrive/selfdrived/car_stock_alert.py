"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): what selfdrived says while the car is stock on purpose (KTuner, 2026-10-19), and the one
upstream check that would otherwise call it a fault.

THE TWO REASONS, read from params in params_thread (one writer each):
  * DiagToolStock (card, diag_tool_guard.py): a diagnostic tool talks on the car bus while parked. The panda stays in
    its start-up ELM327, or pandad leaves the car mode for NO_OUTPUT. A silent note.
  * CarBusOffStock (pandad, car_bus_guard.h): the car bus went bus-off in the car mode; NO_OUTPUT until ignition off.
    One chime, then a silent banner; if anything was engaged, the disengagement says why.
The bus-off wins when both are set.

THE EXPECTED MISMATCH. selfdrived raises upstream's controlsMismatch (IMMEDIATE_DISABLE + NO_ENTRY) whenever the
panda's mode differs from CP.safetyConfigs once it is 10 s old - which is exactly what stock looks like. Left alone, a
MADS paused in P (enabled, not active) would drop to disabled with "TAKE CONTROL IMMEDIATELY / Controls Mismatch" on
both edges, because pandad and selfdrived read the params at 10 Hz each, out of step. So controlsMismatch is
replaced by upstream's carNotReady (NO_ENTRY only - engagement is still refused) when ALL of these hold:
  * nothing is engaged (openpilot, or MADS active);
  * every panda reports a mode that transmits nothing (noOutput, elm327);
  * there is a reason, or the non-car mode is younger than ENTRY_GRACE (the panda reported before this process read
    the param), or the reason cleared less than EXIT_GRACE ago (pandad has not switched back yet);
  * neither of controlsMismatch's other causes is present (safetyRxChecksInvalid, mismatch_counter >= 200).
Anything else - a non-car mode with no reason past ENTRY_GRACE, a wrong param, anything engaged - keeps upstream's
event exactly. While something is engaged it is never masked: a bus-off at speed is disengaged by upstream's own
controlsMismatch (or canBusMissing), and carBusOffDisengage only names it.

THE REFUSAL. An SP event cannot refuse openpilot (selfdrived's state machine reads `events` only), so carNotReady goes
into `events` while a reason is set - the vsa_fault_alert.py pattern - and its "Car Not Ready" is dropped for ours.
MADS reads both lists: the NO_ENTRY of our SP events keeps a paused MADS paused.
"""
from openpilot.cereal import log, custom
from opendbc.car import structs
from opendbc.car.honda.values import HONDA_ELESYS
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, ET

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName
SafetyModel = structs.CarParams.SafetyModel

NONCAR = (SafetyModel.noOutput, SafetyModel.elm327)   # the modes stock is reached through: relay closed, no TX
ENTRY_GRACE = int(0.5 / DT_CTRL)       # the panda reports a non-car mode before this process has read the reason
EXIT_GRACE = int(2.0 / DT_CTRL)        # the reason cleared and pandad (10 Hz) has not switched back yet
ANNOUNCE_FRAMES = int(0.1 / DT_CTRL)   # frames carBusOffAnnounce is raised; its alert then runs its 3.5 s

# upstream alerts ours replace while a reason holds: the refusal's "Car Not Ready", and "Controls Mismatch" when the
# mask could not apply (an engage press then says why the car is stock)
DIAG_REPLACES = ("carNotReady/noEntry", "controlsMismatch/noEntry")
BUS_OFF_REPLACES = DIAG_REPLACES + (
  "canBusMissing/permanent", "canBusMissing/noEntry",        # "Likely Faulty Cable": wrong advice, 3 lines on the comma 4
  "canError/permanent", "canError/noEntry",
  "steerUnavailable/permanent", "steerUnavailable/noEntry",  # "Restart the car" is ours too; newer, it would cover ours
)
# the disengagement alerts ours names instead (only on a frame ours is made, or late_alerts() below)
DISENGAGE_REPLACES = ("controlsMismatch/immediateDisable", "canBusMissing/immediateDisable", "canError/immediateDisable")


def armed_for(CP) -> bool:
  return CP.brand == 'honda' and CP.carFingerprint in HONDA_ELESYS and not CP.passive


class CarStockAlert:
  def __init__(self, CP):
    self.armed = armed_for(CP)
    self.diag = False      # DiagToolStock
    self.bus_off = False   # CarBusOffStock
    self.masked = False    # controlsMismatch was replaced on this frame
    self._noncar_frames = 0
    self._reason_off = EXIT_GRACE
    self._announced = False
    self._announce_left = 0

  def read_params(self, params) -> None:
    """selfdrived's params_thread, 10 Hz."""
    if self.armed:
      self.diag = params.get_bool("DiagToolStock")
      self.bus_off = params.get_bool("CarBusOffStock")

  def update(self, events, panda_states, mismatch_counter: int, active: bool) -> list[int]:
    """One selfdrived frame, after the pandaStates loop has raised controlsMismatch. Edits `events` (the mask and the
    refusal) and returns the EventNameSP to add. active: openpilot enabled or MADS active."""
    self.masked = False
    if not self.armed:
      return []

    reason = self.diag or self.bus_off
    noncar = len(panda_states) > 0 and all(ps.safetyModel in NONCAR for ps in panda_states)
    self._noncar_frames = min(self._noncar_frames + 1, ENTRY_GRACE + 1) if noncar else 0
    self._reason_off = 0 if reason else min(self._reason_off + 1, EXIT_GRACE)

    expected = noncar and (reason or self._noncar_frames <= ENTRY_GRACE or self._reason_off < EXIT_GRACE)
    rx_invalid = any(ps.safetyRxChecksInvalid for ps in panda_states)
    if expected and not active and mismatch_counter < 200 and not rx_invalid and events.has(EventName.controlsMismatch):
      self.masked = True
      while events.has(EventName.controlsMismatch):
        events.remove(EventName.controlsMismatch)
    if (reason or self.masked) and not events.has(EventName.carNotReady):
      events.add(EventName.carNotReady)

    out: list[int] = []
    if self.bus_off:
      out.append(EventNameSP.carBusOffStock)
      if active:
        out.append(EventNameSP.carBusOffDisengage)
      if not self._announced:
        self._announced = True
        self._announce_left = ANNOUNCE_FRAMES
      if self._announce_left > 0:
        self._announce_left -= 1
        out.append(EventNameSP.carBusOffAnnounce)
    elif self.diag:
      out.append(EventNameSP.diagnosticToolStock)
    return out

  def filter_alerts(self, alerts: list, alerts_sp: list) -> list:
    drop: tuple[str, ...] = ()
    if self.bus_off:
      drop = BUS_OFF_REPLACES
      if any(a.alert_type == "carBusOffDisengage/immediateDisable" for a in alerts_sp):
        drop += DISENGAGE_REPLACES
    elif self.diag:
      drop = DIAG_REPLACES
    if not drop:
      return alerts
    return [a for a in alerts if a.alert_type not in drop]

  def late_alerts(self, am, frame: int, alerts_sp: list, callback_args) -> list:
    """carBusOffDisengage's text when upstream's disengagement alert is already up and ours is not: the reason reached
    this process after the disengagement (pandad writes the param in the poll that latches; selfdrived reads it up to
    100 ms later). Same HIGHEST priority, newer start frame, so the AlertManager shows ours from now on. Text only: the
    disengagement already happened. The vsa_fault_alert.late_alerts() pattern."""
    if not self.armed or not self.bus_off:
      return []
    mine = "carBusOffDisengage/immediateDisable"
    if any(a.alert_type == mine for a in alerts_sp):
      return []
    shown = am.alerts.get(mine)
    if shown is not None and shown.active(frame):
      return []
    if not any((u := am.alerts.get(t)) is not None and u.active(frame) for t in DISENGAGE_REPLACES):
      return []
    only = EventsSP()
    only.add(EventNameSP.carBusOffDisengage)
    return only.create_alerts([ET.IMMEDIATE_DISABLE], callback_args)

  def adjust_alerts(self, alerts: list, alerts_sp: list, am, frame: int, callback_args) -> tuple[list, list]:
    """selfdrived's update_alerts(), between create_alerts and AM.add_many: upstream's texts out, ours in."""
    if not self.armed:
      return alerts, alerts_sp
    return self.filter_alerts(alerts, alerts_sp), alerts_sp + self.late_alerts(am, frame, alerts_sp, callback_args)
