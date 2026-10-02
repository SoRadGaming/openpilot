"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(LKAS-GATEWAY): tell the driver when the EPS has latched until key-off.

The EPS-LKAS gateway board reports `RETRY_IN == 255` on 0x70B when the EPS has refused
LKAS for the rest of the key cycle (carStateSP.linbusGateway.latchedUntilKeyOff). Nothing
the board or openpilot can do brings steering back: only switching the ignition off and on
clears it (S:/Software/EPS-LKAS/docs/EPS-FAULT-STATES.md, "Nothing passive clears it").
Until now that reached the driver only as the board's log line and the cluster's LKAS lamp.

This is a WARNING, never a fault. It raises no NO_ENTRY, SOFT_DISABLE, IMMEDIATE_DISABLE or
USER_DISABLE event and sets no carState fault flag, so it cannot drop longitudinal, block
engagement or turn MADS off. Longitudinal keeps working with the EPS latched, which is the
whole point of not using steerFaultPermanent here.

What the board actually sends after a latch (routes fc and fd, 2026-09-27, read back with
LogReader): `latchedUntilKeyOff` is True on every fresh 0x70B frame for the rest of the drive,
but 0x70B itself goes stale again and again - grantValid False for 0.1 s to 25.7 s at a time,
about 20 times per route - and a stale frame reads as "not latched". A plain edge detector
would announce the latch twenty times. So:

* evidence comes only from fresh frames: grantValid True with latchedUntilKeyOff True counts
  towards the latch, grantValid True with it False counts towards a clear, and a stale frame
  counts towards neither (absence is not news either way);
* it is confirmed after LATCH_CONFIRM_FRAMES of latched evidence. The board already ignores
  EPS error states shorter than 200 ms (GW_ERR_LATCH_MS; the longest transient seen was 40 ms)
  and every real latch has lasted minutes, so 1 s costs nothing and rules out a glitch;
* once confirmed it is only cleared by LATCH_CLEAR_FRAMES of fresh "not latched" frames, and
  only a confirm after a clear announces again.

The announcement (sound, 6 s) waits until it can be seen. ET.WARNING alerts are shown only
while openpilot or MADS is active, so if the latch is confirmed while neither is, the
announcement is held until the driver engages again rather than being lost. After it, a
silent, lowest-priority reminder every REMINDER_PERIOD_FRAMES while the latch lasts, also
held until it can be seen. Nothing repeats with sound.
"""
from openpilot.cereal import custom
from openpilot.common.realtime import DT_CTRL

EventNameSP = custom.OnroadEventSP.EventName

LATCH_CONFIRM_FRAMES = int(1.0 / DT_CTRL)      # fresh latched evidence before announcing
LATCH_CLEAR_FRAMES = int(3.0 / DT_CTRL)        # fresh not-latched evidence before it counts as cleared
ANNOUNCE_FRAMES = int(0.1 / DT_CTRL)           # frames the announcement is raised while it can be shown
REMINDER_PERIOD_FRAMES = int(300.0 / DT_CTRL)  # a silent reminder every 5 minutes while latched
REMINDER_FRAMES = int(0.1 / DT_CTRL)


class EpsLatchAlert:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    """Forget everything: a latch has to be confirmed again from fresh 0x70B frames.

    FORK(HONDA_ACCORD_9G_AU): selfdrived calls this when the VSA's own fault clears (vsa_fault_alert.py). The EPS
    refuses torque while the VSA holds a fault and clears with it, so a latch the board reported during the VSA fault
    says nothing about the EPS afterwards - and 0x70B can go stale for seconds right then (route 113: 34.5-41.6 s), so
    the 3 s of fresh "not latched" frames a clear needs may not come before the driver engages.
    """
    self.latched = False         # confirmed: the EPS is latched until key-off
    self._latch_evidence = 0     # fresh latched frames towards a confirm
    self._clear_evidence = 0     # fresh not-latched frames towards a clear
    self._announce_left = 0      # announcement frames still to raise while visible
    self._reminder_left = 0      # reminder frames still to raise while visible
    self._since_shown = 0        # frames since the announcement or the last reminder was raised

  def update(self, gw, can_show: bool) -> list[int]:
    """One selfdrived frame. Returns the EventNameSP events to add this frame.

    gw: carStateSP.linbusGateway, or None when there is none to read.
    can_show: ET.WARNING alerts would be shown, i.e. openpilot or MADS is active.
    """
    present = gw is not None and gw.present
    fresh = present and gw.grantValid
    if fresh and gw.latchedUntilKeyOff:
      self._latch_evidence = min(self._latch_evidence + 1, LATCH_CONFIRM_FRAMES)
      self._clear_evidence = 0
    elif fresh:
      self._latch_evidence = 0
      self._clear_evidence = min(self._clear_evidence + 1, LATCH_CLEAR_FRAMES)

    if not self.latched and self._latch_evidence >= LATCH_CONFIRM_FRAMES:
      self.latched = True
      self._announce_left = ANNOUNCE_FRAMES
      self._reminder_left = 0
      self._since_shown = 0
    elif self.latched and self._clear_evidence >= LATCH_CLEAR_FRAMES:
      self.latched = False
      self._announce_left = 0
      self._reminder_left = 0

    if not self.latched or not present:
      return []

    events: list[int] = []
    if self._announce_left > 0:
      if can_show:
        self._announce_left -= 1
        self._since_shown = 0
        events.append(EventNameSP.lkasGatewayEpsLatched)
      return events

    self._since_shown += 1
    if self._reminder_left == 0 and self._since_shown >= REMINDER_PERIOD_FRAMES:
      self._reminder_left = REMINDER_FRAMES
    if self._reminder_left > 0 and can_show:
      self._reminder_left -= 1
      self._since_shown = 0
      events.append(EventNameSP.lkasGatewayEpsLatchedReminder)
    return events
