"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-RELAY): tell the driver when the BLR-v1 brake-lamp board stops doing its job (2026-10-31, the owner's
call: "fault if our custom board starts failing").

The car ignores openpilot's 0x1FA BRAKE_LIGHTS; the BLR-v1 board lights the brake lamps from it (docs/fork/
BRAKE-LAMP-RELAY.md). The board fails off on its own - command timeout, watchdog, trips, latches - and the pedal lights
the lamps without it, so a failing board costs only the lamps of openpilot's own braking. Until now that reached
nobody onroad: carStateSP.brakeLampRelay and the offroad page's BlrLastDrive, after the drive. Four conditions:

* offline: no 0x620 for 2 s once the board was heard this drive (`valid` is already false 1 s after the last frame),
  or for 10 s from the start when this car has had a board lately (BlrBoardVersion, and BlrBoardSeenAt within
  INSTALLED_RECENT_S) and it has not been heard since.
* latched: the board reports LATCHED or a latch reason (U4, feedback, clock, pin stuck high) for 1 s. Its output
  stays off until the car is switched off.
* not lighting, for 1 s: openpilot asks for the lamps and the board is not driving them (a trip, degraded CAN, a
  shadow image), or it drives them with D_LAMP_DARK (a fuse, the relay), or the board reports D_COIL_OPEN at idle (the
  relay coil or its feed), or it has not heard a valid 0x1FA for 150 ms (openpilot sends it at 50 Hz; in the stock
  pass-through modes the radar's own keeps the board fresh). A frame where openpilot is not braking cannot tell
  whether the board would light the lamps, so it counts neither way: a fault that shows only while braking stays one
  episode for the drive, and only 3 s of braking with the lamps driven and lit clears it.
* stuck on, for 5 s: the lamps read lit, the board is not driving them, the pedal is up, no parking brake or CMBS,
  and the car moves above 5 m/s - a shorted output, a coil wire to ground, the lamp feed stuck on. Not at standstill:
  the car's brake hold lights the lamps there.

On the four drives with the board (15e-161, 2026-10-29) each of these held for 0 s, and the lamps read lit with no
output and no pedal for at most 0.20 s (the relay letting go).

Evidence for latched, not lighting and stuck on comes only from fresh frames; a stale 0x620 counts towards offline and
towards nothing else, and a frame that cannot tell counts towards neither a confirm nor a clear. Each condition
confirms on its own count and clears after CLEAR_FRAMES of evidence the other way. Each confirm announces once (sound,
6 s), the most important first, and the next waits until the last has had its 6 s on screen; a latch explains a board
that does not light the lamps, so not lighting is not announced on top of it. Then a silent reminder every
REMINDER_PERIOD_FRAMES while any condition holds.

These are WARNINGS, never faults, like eps_latch_alert.py: no NO_ENTRY, SOFT_DISABLE or disable event and no carState
flag, so nothing the board sends can drop longitudinal, block an engagement or touch the brake path (DESIGN E.7). Shown
only while openpilot or MADS is active, held until then; Priority.LOWER, under steerSaturated's "Take Control" and
driver monitoring. Off without openpilot longitudinal (stock ACC mode) and on every car but HONDA_ELESYS.
"""
import time

from opendbc.car.honda.values import HONDA_ELESYS
from openpilot.cereal import custom
from openpilot.common.realtime import DT_CTRL
from openpilot.common.swaglog import cloudlog

EventNameSP = custom.OnroadEventSP.EventName

STATE_LATCHED = 4                               # carStateSP.brakeLampRelay.state
D_LAMP_DARK, D_COIL_OPEN = 1 << 2, 1 << 3       # .faults bits (opendbc carstate_ext.py BLR_FAULT_SIGNALS)
LAMP_LIT_MV = 6000                              # .lampSnsMv: lit 13.3-14.1 V, dark under 0.1 V on the logged drives
STUCK_ON_MIN_SPEED = 5.0                        # m/s
INSTALLED_RECENT_S = 14 * 24 * 3600             # a board last heard longer ago than this has been taken out

OFFLINE_CONFIRM_FRAMES = int(1.0 / DT_CTRL)     # with `valid` false: 2 s without a 0x620
NEVER_HEARD_FRAMES = int(10.0 / DT_CTRL)        # a board this car has had, not heard since card started
FAULT_CONFIRM_FRAMES = int(1.0 / DT_CTRL)       # latched, not lighting
STUCK_ON_CONFIRM_FRAMES = int(5.0 / DT_CTRL)
CLEAR_FRAMES = int(3.0 / DT_CTRL)               # evidence the other way before a condition counts as cleared
ANNOUNCE_FRAMES = int(0.1 / DT_CTRL)            # frames an announcement is raised while it can be shown
ANNOUNCE_HOLD_FRAMES = int(6.0 / DT_CTRL)       # its time on screen (events.py) before the next one is raised
REMINDER_PERIOD_FRAMES = int(300.0 / DT_CTRL)   # a silent reminder every 5 minutes while a condition holds
REMINDER_FRAMES = int(0.1 / DT_CTRL)

OFFLINE, LATCHED, NOT_LIGHTING, STUCK_ON = "offline", "latched", "not lighting", "stuck on"
ORDER = (LATCHED, STUCK_ON, NOT_LIGHTING, OFFLINE)   # which queued announcement goes first
ANNOUNCE = {OFFLINE: EventNameSP.brakeLampBoardOffline, LATCHED: EventNameSP.brakeLampBoardLatched,
            NOT_LIGHTING: EventNameSP.brakeLampsNotLighting, STUCK_ON: EventNameSP.brakeLampsStuckOn}
REMIND = EventNameSP.brakeLampBoardReminder


class _Condition:
  """Counts evidence for and against one condition: True, False or None (this frame cannot tell)."""
  def __init__(self):
    self.active = False
    self.towards = 0     # evidence frames towards a confirm
    self.against = 0     # evidence frames towards a clear

  def update(self, seen, confirm_frames: int) -> str | None:
    """Returns 'confirmed' or 'cleared' on the frame it changes, else None."""
    if seen is True:
      self.towards = min(self.towards + 1, confirm_frames)
      self.against = 0
    elif seen is False:
      self.towards = 0
      self.against = min(self.against + 1, CLEAR_FRAMES)
    if not self.active and self.towards >= confirm_frames:
      self.active = True
      return "confirmed"
    if self.active and self.against >= CLEAR_FRAMES:
      self.active = False
      return "cleared"
    return None


def board_installed(params) -> bool:
  """card writes BlrBoardVersion when it first hears a board and BlrBoardSeenAt (unix s) at most once a minute while it
  hears one. A board last heard over INSTALLED_RECENT_S ago counts as taken out; a clock not set yet counts as recent."""
  try:
    if not params.get("BlrBoardVersion"):
      return False
    seen_at = params.get("BlrBoardSeenAt")
    if not seen_at:
      return True
    return time.time() - float(seen_at) < INSTALLED_RECENT_S  # noqa: TID251 - wall clock, as blr_board writes it
  except Exception:
    cloudlog.exception("brake lamp alert: the board params are unreadable; only a board heard this drive is watched")
    return False


class BrakeLampAlert:
  def __init__(self, CP, params=None):
    self.enabled = bool(CP.openpilotLongitudinalControl) and CP.carFingerprint in HONDA_ELESYS
    self.installed = self.enabled and params is not None and board_installed(params)
    self.reset()

  def reset(self) -> None:
    self.heard = False
    self.conditions = {name: _Condition() for name in ANNOUNCE}
    self._pending: list[str] = []   # confirmed conditions still to announce
    self._current = ""              # the condition being announced
    self._announce_left = 0
    self._hold_left = 0
    self._reminder_left = 0
    self._since_shown = 0

  @property
  def active(self) -> list[str]:
    return [name for name, c in self.conditions.items() if c.active]

  def _evidence(self, blr, cs) -> dict:
    valid = bool(blr.valid)
    self.heard = self.heard or valid
    offline = (not valid) if (self.heard or self.installed) else None   # None: no board on this car, as far as known
    if not valid:
      return {OFFLINE: offline, LATCHED: None, NOT_LIGHTING: None, STUCK_ON: None}

    latched = blr.state == STATE_LATCHED or blr.latchReason != 0
    analog = blr.vbatSnsMv > 0      # 0x621 less than 1 s old: the decoder zeroes all three when it is not
    lit = blr.lampSnsMv > LAMP_LIT_MV

    if not blr.cmdFresh:
      not_lighting = True
    elif blr.request or blr.outDrive:
      if (blr.request and not blr.outDrive) or (blr.outDrive and blr.faults & D_LAMP_DARK):
        not_lighting = True
      elif blr.outDrive and (lit or not analog):
        not_lighting = False   # driven, and lit (or no analog reading to say otherwise, and no dark bit)
      else:
        not_lighting = None    # driven, the lamps not reading lit yet
    elif blr.faults & D_COIL_OPEN:
      not_lighting = True      # the board's idle diagnosis: output off, relay line low, lamps low
    else:
      not_lighting = None      # openpilot not braking: nothing to tell whether the board would light them

    stuck_on = None
    if analog and not blr.outDrive and not cs.brakePressed and not cs.parkingBrake and not cs.stockAeb and \
       cs.vEgo > STUCK_ON_MIN_SPEED:
      stuck_on = lit
    return {OFFLINE: offline, LATCHED: latched, NOT_LIGHTING: not_lighting, STUCK_ON: stuck_on}

  def update(self, cs_sp, cs, can_show: bool) -> list[int]:
    """One selfdrived frame. Returns the EventNameSP events to add this frame.

    cs_sp: carStateSP (its brakeLampRelay is read here, nowhere else in selfdrived). cs: carState.
    can_show: ET.WARNING alerts would be shown, i.e. openpilot or MADS is active.
    """
    if not self.enabled:
      return []
    blr = cs_sp.brakeLampRelay
    evidence = self._evidence(blr, cs)
    confirm = {OFFLINE: OFFLINE_CONFIRM_FRAMES if self.heard else NEVER_HEARD_FRAMES, LATCHED: FAULT_CONFIRM_FRAMES,
               NOT_LIGHTING: FAULT_CONFIRM_FRAMES, STUCK_ON: STUCK_ON_CONFIRM_FRAMES}
    for name, c in self.conditions.items():
      change = c.update(evidence[name], confirm[name])
      if change == "confirmed":
        board = (f"state={blr.state} latch={blr.latchReason} faults=0x{blr.faults:02x} request={blr.request} " +
                 f"outDrive={blr.outDrive} cmdFresh={blr.cmdFresh} lampSnsMv={blr.lampSnsMv}")
        cloudlog.warning(f"brake lamp alert: {name} ({board})")
        if name == LATCHED and NOT_LIGHTING in self._pending:
          self._pending.remove(NOT_LIGHTING)
        # a latch explains a board that does not light the lamps: one announcement, not two
        if not (name == NOT_LIGHTING and self.conditions[LATCHED].active):
          self._pending.append(name)
      elif change == "cleared":
        cloudlog.warning(f"brake lamp alert: {name} cleared")
        if name in self._pending:
          self._pending.remove(name)
        if name == self._current:
          self._announce_left = self._hold_left = 0

    if not self.active:
      self._pending.clear()
      self._announce_left = self._hold_left = self._reminder_left = 0
      return []

    events: list[int] = []
    if self._announce_left == 0 and self._hold_left == 0 and self._pending:
      self._pending.sort(key=ORDER.index)
      self._current = self._pending.pop(0)
      self._announce_left = ANNOUNCE_FRAMES
      self._hold_left = ANNOUNCE_HOLD_FRAMES
    if self._announce_left > 0 or self._hold_left > 0:
      if can_show:
        self._since_shown = 0
        self._hold_left = max(self._hold_left - 1, 0)
        if self._announce_left > 0:
          self._announce_left -= 1
          events.append(ANNOUNCE[self._current])
      return events

    self._since_shown += 1
    if self._reminder_left == 0 and self._since_shown >= REMINDER_PERIOD_FRAMES:
      self._reminder_left = REMINDER_FRAMES
    if self._reminder_left > 0 and can_show:
      self._reminder_left -= 1
      self._since_shown = 0
      events.append(REMIND)
    return events
