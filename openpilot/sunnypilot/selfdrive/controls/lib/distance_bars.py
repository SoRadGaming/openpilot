"""
FORK(CLOSE-FOLLOW): four dash bars (push14, 2026-10-26). The owner: "OP actually has 3 modes but my car has 4 supported
on the dash so we could map 4 3 2 to driving modes then 1 bar to the new setting".

THE DASH. This car's cluster shows the follow distance as 1-4 bars, from ACC_HUD's 2-bit HUD_DISTANCE (0x30C byte 5
bits 7:6): 1, 2 and 3 are that many bars and 0 is 4 (upstream hondacan: "wraps to 0 at 4 bars" - the packer masks 4 to
0). The owner's own drives bear it out (the Elesys radar's own ACC_HUD, bus 2):
  * the order: the radar steps its HUD_DISTANCE 3 -> 2 -> 1 -> 0 -> 3, one step per press of the distance button (91
    steps on 25 routes, never any other step);
  * 1 closer than 2: on a drive the stock ACC drove itself (route 81: no openpilot ACC_HUD on the bus, so the radar's own
    reached the dash and its gas and brake the car), the steady gap behind a car was 1.22 s at 1 and 1.60 s at 2;
  * 0 the longest: steadily behind a car at 1.3-2.1 s, the radar's own speed request (PCM_SPEED) braked in 60 % of the
    frames at 0 against 5 % at 1, 7 % at 2 and 2 % at 3 - at 0 it wanted more than 2.1 s;
  so with one step closer per press 1-3 are that many bars and 0 is four. Where no openpilot ACC_HUD was on the bus
  (none within 1 s) the radar put all four on the dash with ACC_ON (0: 129 frames, 1: 24,209, 2: 19,627, 3: 573). A gap measured while
  openpilot sends its own ACC_HUD (src 128) is openpilot's, not the radar's: leave those drives out of any such count.
Upstream sends personality + 1 - aggressive 1 bar, standard 2, relaxed 3 - and never 4.

THE MODES on this car: 4 bars relaxed, 3 standard, 2 aggressive, 1 aggressive with close follow LIVE. Each press of the
steering wheel's distance button steps one closer and wraps, 4 -> 3 -> 2 -> 1 -> 4 (the stock radar's own order), while
the CloseFollow switch is on - the switch is the master: off, the button steps 4 -> 3 -> 2 -> 4 (upstream's three
personalities in upstream's order) and close follow never goes live. Close follow is live only at 1 bar; at 4, 3 and 2
it runs in shadow - computed and logged, the plan and every CAN frame as with close follow off (close_follow.py).
Holding the button is upstream's experimental-mode toggle (sunnypilot's cruise_helpers.py), unchanged: the release that
ends a hold steps nothing, as upstream.

THE STATE: upstream's LongitudinalPersonality and CloseFollowOneBar (BOOL, PERSISTENT, default off) - the 1-bar mode as
the button left it. Both are written only by a press (press(), selfdrived) or a setting, never from a loop, and both
persist across drives like the personality: after the update the owner keeps his personality (aggressive, now 2 bars)
and his first press takes him to 1 bar. The mode in effect (one_bar()) is the switch on AND CloseFollowOneBar AND the
personality aggressive AND the car where close follow runs; selfdrived publishes it as
selfdriveStateSP.closeFollowOneBar, controlsd turns it into the dash's bars (lead_distance_bars()), and plannerd's close
follow is live only while it is set and its own read of the switch is on (re-read on the plan the mode comes on). A
press always steps from the mode in effect, so a remembered 1-bar mode with the switch off (2 bars on the dash) steps to
4 and is cleared.
ONLY THE BUTTON ENTERS IT (the push14 review). A personality picked in a setting leaves the 1-bar mode: the comma 4's
settings page (its toggles.py, every tap a change) and - personality_picked() - sunnylink's app and a backup restore,
both through sunnylink's save_param_from_base64_encoded_string(); the personality in effect picked again changes
nothing. No backup carries CloseFollowOneBar (not BACKUP) and sunnylink cannot write it (BLOCKED_PARAMS), so a restore
or the app never turns close follow live: the button does.

GATED: close_follow_applies() - HONDA_ELESYS with openpilot longitudinal, stock ACC mode off. Every other car, and this
car in stock ACC mode (the radar's own ACC_HUD reaches the dash; openpilot sends none, and the radar hears the
driver's real buttons), keep upstream's three steps and personality + 1, bit for bit.

THE RACE. selfdrived's params thread re-reads LongitudinalPersonality and CloseFollowOneBar every 0.1 s and takes what
is on disk, while a press writes them from the 100 Hz loop, and a write can take minutes to reach /data. Route 14c (t =
365.0-365.15: aggressive, standard, aggressive) showed it for one read; route 157 (push14's first drive, /data at 10 %
free) for minutes: selfdrived's writes waited 50-230 s in its Params handle's queue (hardwared's ParamsWriter logged
single writes of 1.3-48.9 s). push14 kept a press's value for 20 reads (2 s) or until a read showed that value, so the
stale disk won after 2 s, a stale disk that happened to hold the press's value dropped the guard at once, and when the
queue drained every old step came back to the dash in turn: 48 of its 100 HUD_DISTANCE changes had neither a press nor
a setting behind them.
push15: a press writes through selfdrived's ParamsWriter (one write per key, the newest value - not one per press - and
a slow write logged), and a read takes the press's own value for exactly as long as the writer has that key in flight
(ParamsWriter.in_flight, asked BEFORE the read, so a write that lands in between is read back, never the stale value).
No count and no guess: the dash changes on a press, and otherwise only when a setting (the comma 4's page, sunnylink, a
restore) lands with none of the press's own writes of that key in flight - then it applies on the next read. A setting
that lands while a press's write of the same key is still in flight is overwritten when that write lands: the last
write to land wins, on disk and on the dash alike (with a healthy /data that window is a few ms). On this car the
thread's reads and assignment and a press's step and writes hold LOCK (selfdrived takes it around both).
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


def personality_picked(params, personality) -> bool:
  """A setting is about to write LongitudinalPersonality (sunnylink's app or a backup restore; the comma 4's page has its
  own, toggles.py): a change of the personality leaves the 1-bar mode - CloseFollowOneBar cleared, once and before the
  personality lands, so no read of selfdrived's sees the new personality with the old mode. The personality in effect
  picked again changes nothing. A write on a settings change, never from a loop; on every other car the flag is never
  set, so nothing is written. True if it cleared the flag."""
  try:
    if int(personality) == int(params.get(PERSONALITY_PARAM, return_default=True)):
      return False
    if not read_one_bar_param(params):
      return False
    params.put_bool(ONE_BAR_PARAM, False, block=True)
    return True
  except Exception:
    return False


class DistanceBars:
  """selfdrived's handle. applies: this car (four_bars_apply). switch_on and one_bar_set are CloseFollow and
  CloseFollowOneBar as last read or written. writer: selfdrived's ParamsWriter, which a press writes through (THE RACE).
  read_params() (the params thread, every 0.1 s) and press() (the loop, on a distance-button release) are called with
  LOCK held - selfdrived takes it around each, with its own assignment of the personality - and only on this car."""

  def __init__(self, CP, CP_SP, params, writer):
    self.applies = four_bars_apply(CP, CP_SP)
    self.lock = threading.Lock()
    self.writer = writer
    self.switch_on = False
    self.one_bar_set = False
    self._mine: dict[str, int | bool] = {}   # key -> the value the last press wrote through the writer
    if self.applies:
      self.read_params(params)

  def _read(self, key, read):
    """key as selfdrived takes it: the press's own value while the writer has its write in flight, else read() - the
    disk. in_flight is asked first: a write that lands between the two is then read back, never the stale value."""
    if key in self._mine and self.writer.in_flight(key):
      return self._mine[key]
    return read()

  def read_params(self, params):
    """Reads only (LOCK held): CloseFollow, CloseFollowOneBar and - returned, for selfdrived's own - the personality."""
    self.switch_on = read_switch(params)
    self.one_bar_set = bool(self._read(ONE_BAR_PARAM, lambda: read_one_bar_param(params)))
    return self._read(PERSONALITY_PARAM, lambda: params.get(PERSONALITY_PARAM, return_default=True))

  def one_bar(self, personality) -> bool:
    """The 1-bar mode in effect: this car, the switch on, CloseFollowOneBar set and the personality aggressive."""
    return self.applies and self.switch_on and self.one_bar_set and int(personality) == AGGRESSIVE

  def press(self, personality) -> int:
    """A press (LOCK held): the next mode (next_mode) from the mode in effect. Writes the personality and
    CloseFollowOneBar only where they change, through the writer (it returns at once). Returns the new personality."""
    p, one = next_mode(personality, self.one_bar(personality), self.switch_on)
    if p != int(personality):
      self.writer.put(PERSONALITY_PARAM, p)
      self._mine[PERSONALITY_PARAM] = p
    if one != self.one_bar_set:
      self.writer.put_bool(ONE_BAR_PARAM, one)
      self._mine[ONE_BAR_PARAM] = one
      self.one_bar_set = one
    return p
