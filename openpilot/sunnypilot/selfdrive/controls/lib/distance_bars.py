"""
FORK(CLOSE-FOLLOW): four dash bars (push14, 2026-10-26). The owner: "OP actually has 3 modes but my car has 4 supported
on the dash so we could map 4 3 2 to driving modes then 1 bar to the new setting".

THE DASH. This car's cluster shows the follow distance as 1-4 bars, from ACC_HUD's 2-bit HUD_DISTANCE (0x30C byte 5
bits 7:6): 1, 2 and 3 are that many bars and 0 is 4 (upstream hondacan: "wraps to 0 at 4 bars" - the packer masks 4 to
0). The owner's own drives under the Elesys radar's stock ACC bear it out: the radar steps its HUD_DISTANCE
3 -> 2 -> 1 -> 0 -> 3 at each press of the distance button (91 steps on 25 routes, never any other step), and following
a car steadily under the stock ACC the gap is shortest at 1 (median 1.23 s, 7 routes) and longest at 0 (1.86 s), with 2
between (1.59 s). Upstream sends personality + 1 - aggressive 1 bar, standard 2, relaxed 3 - and never 4.

THE MODES on this car: 4 bars relaxed, 3 standard, 2 aggressive, 1 aggressive with close follow LIVE. Each press of the
steering wheel's distance button steps one closer and wraps, 4 -> 3 -> 2 -> 1 -> 4 (the stock radar's own order), while
the CloseFollow switch is on - the switch is the master: off, the button steps 4 -> 3 -> 2 -> 4 (upstream's three
personalities in upstream's order) and close follow never goes live. Close follow is live only at 1 bar; at 4, 3 and 2
it runs in shadow - computed and logged, the plan and every CAN frame as with close follow off (close_follow.py).
Holding the button is upstream's experimental-mode toggle (sunnypilot's cruise_helpers.py), unchanged: the release that
ends a hold steps nothing, as upstream.

THE STATE: upstream's LongitudinalPersonality and CloseFollowOneBar (BOOL, PERSISTENT | BACKUP, default off) - the
1-bar mode as the button left it. Both are written only by a press (press(), selfdrived) or a setting, never from a loop,
and both persist across drives like the personality: after the update the owner keeps his personality (aggressive, now 2
bars) and his first press takes him to 1 bar. The mode in effect (one_bar()) is the switch on AND CloseFollowOneBar AND
the personality aggressive AND the car where close follow runs; selfdrived publishes it as
selfdriveStateSP.closeFollowOneBar, controlsd turns it into the dash's bars (lead_distance_bars()), and plannerd's close
follow is live only while it is set and its own read of the switch is on. A press always steps from the mode in effect,
so a remembered 1-bar mode with the switch off (2 bars on the dash) steps to 4 and is cleared. A personality picked on
the comma 4's settings page leaves the 1-bar mode (its toggles.py clears CloseFollowOneBar); one picked in sunnylink
does too unless it is aggressive, which with CloseFollowOneBar still set is the 1-bar mode again.

GATED: close_follow_applies() - HONDA_ELESYS with openpilot longitudinal, stock ACC mode off. Every other car, and this
car in stock ACC mode (the radar's own ACC_HUD reaches the dash; openpilot sends none, and the radar hears the
driver's real buttons), keep upstream's three steps and personality + 1, bit for bit.

THE RACE. selfdrived's params thread re-reads LongitudinalPersonality every 0.1 s while a press writes it from the 100 Hz
loop - and Params.put() is non-blocking (a queued write) - so a read before the write lands puts the old personality
back for 0.1 s (route 14c, t = 365.0-365.15: aggressive, standard, aggressive; one ACC_HUD frame can carry it). On this
car the thread's reads and assignment and a press's step and writes hold LOCK (selfdrived takes it around both), and a
read that disagrees with a press's own write that has not landed yet keeps the press's value (_pending), for at most
PENDING_READS reads - after that the param wins, as it does at once for a value nobody pressed for (a setting).
"""
import threading

from openpilot.cereal import log
from openpilot.sunnypilot.selfdrive.controls.lib.close_follow_gate import close_follow_applies, read_switch

Personality = log.LongitudinalPersonality
AGGRESSIVE, STANDARD, RELAXED = int(Personality.aggressive), int(Personality.standard), int(Personality.relaxed)

PERSONALITY_PARAM = "LongitudinalPersonality"
ONE_BAR_PARAM = "CloseFollowOneBar"
ONE_BAR = 1                                        # the dash's bars in the 1-bar mode
FOUR_BARS = {RELAXED: 4, STANDARD: 3, AGGRESSIVE: 2}  # the dash's bars for each personality on this car
ONE_BAR_NAME = "close follow"                      # the personality alert's name for it: "Driving Personality: Close Follow"
PENDING_READS = 20                                 # the params thread's reads (2 s) a press's own write has to land


def four_bars_apply(CP, CP_SP) -> bool:
  """This car's four dash bars and the 1-bar mode: where close follow runs (close_follow_applies)."""
  return close_follow_applies(CP, CP_SP)


def lead_distance_bars(personality, one_bar: bool, four_bars: bool) -> int:
  """hudControl.leadDistanceBars. four_bars (four_bars_apply): 4 / 3 / 2 bars for relaxed / standard / aggressive and 1
  in the 1-bar mode (one_bar, which needs aggressive); everywhere else upstream's personality + 1."""
  p = int(personality)
  if not four_bars:
    return p + 1
  if one_bar and p == AGGRESSIVE:
    return ONE_BAR
  return FOUR_BARS.get(p, p + 1)


def next_mode(personality, one_bar: bool, switch_on: bool) -> tuple[int, bool]:
  """One press, from the mode in effect: (personality, the 1-bar mode) after it. 1 bar -> 4 (relaxed); 2 (aggressive)
  -> 1 while the switch is on; otherwise upstream's step, one personality closer and relaxed after aggressive."""
  p = int(personality)
  if one_bar:
    return RELAXED, False
  if switch_on and p == AGGRESSIVE:
    return AGGRESSIVE, True
  return (p - 1) % 3, False


def read_one_bar_param(params) -> bool:
  """CloseFollowOneBar (a key nobody wrote is its default, off). Anything unreadable is off."""
  try:
    v = params.get(ONE_BAR_PARAM, return_default=True)
    if isinstance(v, bool):
      return v
    if isinstance(v, bytes):
      v = v.decode(errors='ignore')
    return str(v).strip() == "1"
  except Exception:
    return False


class DistanceBars:
  """selfdrived's handle. applies: this car (four_bars_apply). switch_on and one_bar_set are CloseFollow and
  CloseFollowOneBar as last read or written. read_params() (the params thread, every 0.1 s) and press() (the loop, on a
  distance-button release) are called with LOCK held - selfdrived takes it around each, with its own assignment of the
  personality - and only on this car."""

  def __init__(self, CP, CP_SP, params):
    self.applies = four_bars_apply(CP, CP_SP)
    self.lock = threading.Lock()
    self.switch_on = False
    self.one_bar_set = False
    self._pending: dict[str, list] = {}   # key -> [the value a press wrote, reads left before a read wins]
    if self.applies:
      self.read_params(params)

  def _landed(self, key, v):
    """v as read, unless a press's own write of key has not landed yet (Params.put is non-blocking): then the press's
    value, for at most PENDING_READS reads. Reading the press's value back clears it."""
    p = self._pending.get(key)
    if p is None:
      return v
    if v == p[0] or p[1] <= 0:
      del self._pending[key]
      return v
    p[1] -= 1
    return p[0]

  def read_params(self, params):
    """Reads only (LOCK held): CloseFollow, CloseFollowOneBar and - returned, for selfdrived's own - the personality."""
    self.switch_on = read_switch(params)
    self.one_bar_set = bool(self._landed(ONE_BAR_PARAM, read_one_bar_param(params)))
    return self._landed(PERSONALITY_PARAM, params.get(PERSONALITY_PARAM, return_default=True))

  def one_bar(self, personality) -> bool:
    """The 1-bar mode in effect: this car, the switch on, CloseFollowOneBar set and the personality aggressive."""
    return self.applies and self.switch_on and self.one_bar_set and int(personality) == AGGRESSIVE

  def press(self, personality, params) -> int:
    """A press (LOCK held): the next mode (next_mode) from the mode in effect. Writes the personality and
    CloseFollowOneBar only where they change, as upstream's press does (non-blocking). Returns the new personality."""
    p, one = next_mode(personality, self.one_bar(personality), self.switch_on)
    if p != int(personality):
      params.put(PERSONALITY_PARAM, p)
      self._pending[PERSONALITY_PARAM] = [p, PENDING_READS]
    if one != self.one_bar_set:
      params.put_bool(ONE_BAR_PARAM, one)
      self._pending[ONE_BAR_PARAM] = [one, PENDING_READS]
      self.one_bar_set = one
    return p
