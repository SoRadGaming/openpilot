"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): name the VSA's own fault on screen, and refuse engagement while the
VSA holds it.

carStateSP.vsaFault and vsaStoredFault come from opendbc's vsa_fault.py (HONDA_ELESYS only;
False on every other car, so this does nothing anywhere else). On 2026-10-01 the VSA - the unit
that carries out openpilot's brake requests - faulted twice and dropped a brake request part
way; the driver was shown "Cruise Fault: Restart the Car", and a restart does not clear it
(S:/OP/incident-2026-10-01/REPORT.md).

LIVE (vsaFault together with accFaulted). Nothing about the disengagement changes. accFaulted is
upstream's event, raised from 0x1B0 BRAKE_ERROR, and it stays in `events` with its
IMMEDIATE_DISABLE and NO_ENTRY: it is what disengages openpilot and MADS, exactly as before.
This adds the SP event vsaFault beside it, whose alerts say what happened, and filter_alerts()
drops upstream's accFaulted alerts on the frames it is up so the two do not compete for the
screen (an equal-priority tie goes to whichever alert type the AlertManager met first, which
would be upstream's). vsaFault's own IMMEDIATE_DISABLE and NO_ENTRY are only ever raised beside
accFaulted's, so they disable or refuse nothing that was not already disabled or refused.
vsaFault without BRAKE_ERROR has never been seen; it raises nothing here (the lamp bits that
follow it 20 ms later make it a stored fault, below).

STORED (vsaStoredFault, no live fault). The fault lamps are on outside the start-up bulb check:
the VSA is holding a fault from an earlier key cycle, and holds it until a re-check that ran at
35.3 km/h on route 113. Until then openpilot must not take the brakes. upstream's
EventName.carNotReady ("car is transiently refusing engagement", NO_ENTRY only, nothing on this
car raises it) goes into `events`, which is the only list selfdrived's own state machine reads -
an SP event cannot refuse openpilot's engagement. The SP event vsaStoredFault carries the text;
carNotReady's "Car Not Ready" is dropped by filter_alerts().

MADS IS REFUSED TOO, deliberately. MADS's state machine reads both lists, so carNotReady refuses
a lateral-only engagement as well, and that is the decision, not a side effect:
  * the EPS refuses torque while the VSA holds the fault: STEER_STATUS 2 from 1.66 s after key-on
    on both stored starts (111, 113), and a hard fault (15) 30 s later that only clears with the
    VSA. Lateral could not work, and MADS would only show as engaged while doing nothing;
  * the board's steering floor is 51.5 km/h and the fault clears at about 35 km/h, so there is
    no speed at which the board could steer with the fault still stored;
  * a live fault already refuses MADS today (accFaulted is NO_ENTRY in `events`); this keeps the
    restart that follows consistent with it.
An enabled MADS is not disabled by a stored fault (NO_ENTRY only refuses entry); a paused one
does not resume until the fault clears. Both clear by themselves when vsaStoredFault drops.

Also dropped while either is up: steerUnavailable's PERMANENT and NO_ENTRY ("LKAS Fault: Restart
the car"). The EPS escalates 30 s after the VSA on every route (EPS DTC 85-01 "VSA system
malfunction") and clears 21 ms after it, so restarting is the wrong advice there too, and its
newer banner would otherwise replace the VSA's for the rest of the drive (routes 110, 112).
steerUnavailable's IMMEDIATE_DISABLE is left alone: that is a real disengagement of steering.

SOUND, ONCE. vsaFaultAnnounce, a PERMANENT with one prompt, is raised once per fault episode,
whatever is engaged; a new episode needs REARM_FRAMES with neither flag set. It lasts
3.5 s (its EVENTS_SP entry), shorter than an ImmediateDisableAlert's 4 s, so when a live onset disengages
something the disengagement alert (HIGHEST) covers it from its first frame to its last and only
one sound plays. The banners are silent and Priority.LOWER, upstream's level for accFaulted's own
banner; a refused engagement plays upstream's refuse sound, per press, as before.
"""
from openpilot.cereal import log, custom
from openpilot.common.realtime import DT_CTRL

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName

ANNOUNCE_FRAMES = int(0.1 / DT_CTRL)    # frames the announcement is raised; its alert then runs its duration
REARM_FRAMES = int(10.0 / DT_CTRL)      # fault-free frames before a new fault is announced again

# upstream alerts whose text the VSA's replaces while it is up ("restart the car" is wrong advice for a
# fault the VSA keeps across a key cycle)
LIVE_REPLACES = ("accFaulted/immediateDisable", "accFaulted/noEntry", "accFaulted/permanent")
STORED_REPLACES = ("carNotReady/noEntry",)
EITHER_REPLACES = ("steerUnavailable/permanent", "steerUnavailable/noEntry")


class VsaFaultAlert:
  def __init__(self):
    self.live = False
    self.stored = False
    self._announced = False
    self._announce_left = 0
    self._fault_free = REARM_FRAMES

  def update(self, cs_sp, acc_faulted: bool) -> tuple[list[int], list[int]]:
    """One selfdrived frame. Returns (EventName to add to events, EventNameSP to add to events_sp).

    cs_sp: carStateSP. acc_faulted: upstream's accFaulted event is in `events` this frame.
    """
    vsa_fault = bool(getattr(cs_sp, "vsaFault", False))
    vsa_stored = bool(getattr(cs_sp, "vsaStoredFault", False))
    self.live = vsa_fault and acc_faulted
    self.stored = vsa_stored and not self.live

    events: list[int] = []
    events_sp: list[int] = []
    if self.live:
      events_sp.append(EventNameSP.vsaFault)
    elif self.stored:
      events.append(EventName.carNotReady)
      events_sp.append(EventNameSP.vsaStoredFault)

    if self.live or self.stored:
      self._fault_free = 0
      if not self._announced:
        self._announced = True
        self._announce_left = ANNOUNCE_FRAMES
    else:
      self._fault_free = min(self._fault_free + 1, REARM_FRAMES)
      if self._fault_free >= REARM_FRAMES:
        self._announced = False
      self._announce_left = 0

    if self._announce_left > 0:
      self._announce_left -= 1
      events_sp.append(EventNameSP.vsaFaultAnnounce)
    return events, events_sp

  def filter_alerts(self, alerts: list) -> list:
    """Drop the upstream alerts the VSA's own replace on this frame (see the module docstring)."""
    drop: tuple[str, ...] = ()
    if self.live:
      drop = LIVE_REPLACES + EITHER_REPLACES
    elif self.stored:
      drop = STORED_REPLACES + EITHER_REPLACES
    if not drop:
      return alerts
    return [a for a in alerts if a.alert_type not in drop]
