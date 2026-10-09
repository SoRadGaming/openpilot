"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): round 5 (2026-10-09, the owner's decision 5.2). Fork-owned, beside upstream's speed_limit_assist.py
rather than inside it: it needs only upstream's SPEED_LIMIT_CHANGED_HOLD_PERIOD, which upstream defines and never uses.
"""
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SPEED_LIMIT_CHANGED_HOLD_PERIOD


class SpeedLimitChangeHold:
  """A new speed limit counts only once it has held for SPEED_LIMIT_CHANGED_HOLD_PERIOD.

  plannerd runs the resolver's output through this before Speed Limit Assist, and publishes what this holds as
  longitudinalPlanSP.speedLimit.resolver - so SLA, card (cruise_ext: the set speed it writes and the button a prompt
  asks for), selfdrived's alert text and the UI all see the same limit at the same moment. Without that, card would
  rewrite the set speed on the raw change while SLA was still waiting.

  Only a change from one limit to a DIFFERENT one waits (speedLimitFinalLast, the limit SLA would set). The first limit
  (none -> one), losing it (the resolver dropped the carried limit) and the same limit coming back pass at once, as do
  changes underneath an unchanged final limit (the raw limit going 0 on an untagged stretch while the last one is
  carried). A limit that flips back within the hold never counts at all. The hold counts plannerd frames (20 Hz), so a
  limit counts on the 20th consecutive frame that carries it.
  """
  FIELDS = ('speed_limit', 'speed_limit_last', 'speed_limit_final', 'speed_limit_final_last', 'speed_limit_offset')
  HOLD_FRAMES = round(SPEED_LIMIT_CHANGED_HOLD_PERIOD / DT_MDL)

  def __init__(self):
    self.speed_limit = 0.
    self.speed_limit_last = 0.
    self.speed_limit_final = 0.
    self.speed_limit_final_last = 0.
    self.speed_limit_offset = 0.
    self._pending_final_last = 0.  # the different limit being held back (0: none)
    self._pending_frames = 0

  @property
  def speed_limit_valid(self) -> bool:
    return self.speed_limit > 0.

  @property
  def speed_limit_last_valid(self) -> bool:
    return self.speed_limit_last > 0.

  @property
  def holding(self) -> bool:
    return self._pending_final_last > 0.

  def update(self, resolver) -> None:
    raw = {f: float(getattr(resolver, f)) for f in self.FIELDS}
    new, held = raw['speed_limit_final_last'], self.speed_limit_final_last
    if self._same(new, held) or new <= 0. or held <= 0.:
      self._take(raw)
      return

    if not self._same(new, self._pending_final_last):
      self._pending_final_last = new
      self._pending_frames = 0
    self._pending_frames += 1
    if self._pending_frames >= self.HOLD_FRAMES:
      self._take(raw)

  @staticmethod
  def _same(a: float, b: float) -> bool:
    return abs(a - b) < 0.01  # m/s: a limit is a whole number of km/h or mph, so this only absorbs float noise

  def _take(self, raw: dict[str, float]) -> None:
    for f, v in raw.items():
      setattr(self, f, v)
    self._pending_final_last = 0.
    self._pending_frames = 0
