"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): a refused lane change as a tile on the blinker side (HudLaneBlock; the owner's option 3, 2026-10-12).

While the blinker asks for a lane change that DesireHelper refuses - a road edge on that side (RELC) or a car in the
blind spot - selfdrived raises laneChangeRoadEdge / laneChangeBlocked. Upstream the comma 4 draws them as an orange
alert over the road (the road edge's fills the whole view with three lines and no side) and chimes each time one
appears. With the setting on, selfdrived drops both alerts (sunnypilot/selfdrive/selfdrived/lane_block_alert.py) and
this tile shows the block instead:

  * 92 x 100 px in the top corner on the blinker side - where the stock green lane-change arrow goes - with the arrow
    dimmed and struck through in orange and the reason under it: EDGE (a road edge) or CAR (a car in the blind spot);
  * silent. A nudge toward the blinker that the block refused (modelDataV2SP.laneChangeNudgeRefused, from
    DesireHelper) flashes its rim twice, and selfdrived chimes once for it - both on the flag's rising edge, which
    DesireHelper lets come at most once in 2 s, so the flash and the chime are always a pair;
  * when the block clears and the stock 'steer left / right' or 'changing lanes' alert comes, the tile goes on the frame
    and that alert's green arrow takes the same place (never a struck-through arrow over a green one); a block that
    clears with no alert to follow (the blinker off) fades out in 0.15 s. Meanwhile the turn-intent arrow over the wheel
    stays up (BlockTurnIntent).

Truthful only: it is drawn while modelV2 says the change is waiting (preLaneChange) and the block selfdrived acts on
is set - carState's blind spot first, then modelDataV2SP's road edge, selfdrived's own order - never predicted. Any
alert on screen other than the lane-change ones wins: the tile goes on the frame (as the coming limit does), and while
that alert is up a refused nudge has no answer at all - no flash, as the tile is not drawn, and no chime, as the chime
(Priority.LOWER) never takes the place of an alert on screen. That alert has the screen and the sound; the refusal
itself is unchanged and the tile is back when the alert has gone. A left tile has the top left: the driver-monitoring
face, the stock MAX number, the coming limit and the confirm's box make way while it shows.
"""
from dataclasses import dataclass

import pyray as rl

from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import TurnIntent
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_alerts, hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import event_name
from openpilot.system.ui.lib.application import gui_app, FontWeight

LaneChangeState = log.LaneChangeState
LaneChangeDirection = log.LaneChangeDirection

LEFT, RIGHT = "left", "right"
EDGE, CAR = "EDGE", "CAR"

# the tile (device px)
TILE_W, TILE_H = 92, 100
MARGIN = 8               # from the content rect's top and side edges; the ball's strip is never drawn on
RADIUS = 18              # concentric with the content rect's corner
FILL = rl.Color(0, 0, 0, 230)
ORANGE = rl.Color(255, 115, 0, 255)       # ALERT_COLORS[userPrompt]: today's block alerts
TEXT = rl.Color(255, 255, 255, 229)       # the stock alert text (0.9 white)
ARROW_OFF = rl.Color(170, 170, 170, 255)  # the refused arrow: dimmed white
STRIKE_UNDER = rl.Color(0, 0, 0, 255)
RIM = 3.0
RIM_LIT = 4.5
ARROW_W, ARROW_H, SHAFT = 60, 46, 19      # the stock turn-signal arrow's core (64 x 56, shaft 24), a little flatter
ARROW_HEAD = 0.46                         # of the arrow's length
ARROW_CY = 42                             # from the tile's top
STRIKE, STRIKE_W, STRIKE_UNDER_W = 23, 6.0, 9.0
WORD_CY = 82                              # the reason's ink center, from the tile's top
WORD_SIZE = 28                            # Bold 28 nominal: ~20 px caps
HALO = ((18, 30), (15, 45), (12, 65), (9, 95), (6, 140))   # the flash: pending_max's lit strokes, in orange

FLASH_S = 0.18           # a refused nudge: the rim flashes twice, 0.18 s on, 0.18 s off
FLASHES = 2
FADE_IN_S = 0.1          # first-order, as the stock MAX number
FADE_OUT_S = 0.15        # linear: gone in 0.15 s, so the green arrow that takes the place is clear at once
STALE_S = 0.25           # no frame drawn for this long: the tile's state is old, not fading (as the confirm's box)

# the alerts the tile hands over to, or that leave the top corners free: they never cut it
HANDOVER = ("preLaneChangeLeft", "preLaneChangeRight", "laneChange")


@dataclass(frozen=True)
class Block:
  side: str        # LEFT / RIGHT: the blinker's
  word: str        # EDGE / CAR
  refused: bool    # a nudge toward it is being refused (DesireHelper.nudge_refused)


def lane_block(sm, started_frame: int = 0) -> Block | None:
  """The block selfdrived acts on now (selfdrived.py, 'Handle lane change'), or None."""
  if sm.recv_frame['modelV2'] < started_frame:
    return None
  meta = sm['modelV2'].meta
  if meta.laneChangeState != LaneChangeState.preLaneChange:
    return None
  if meta.laneChangeDirection == LaneChangeDirection.left:
    side = LEFT
  elif meta.laneChangeDirection == LaneChangeDirection.right:
    side = RIGHT
  else:
    return None
  cs = sm['carState']
  fresh_sp = sm.recv_frame['modelDataV2SP'] >= started_frame
  md = sm['modelDataV2SP']
  if cs.leftBlindspot if side == LEFT else cs.rightBlindspot:
    word = CAR
  elif fresh_sp and (md.leftLaneChangeEdgeBlock if side == LEFT else md.rightLaneChangeEdgeBlock):
    word = EDGE
  else:
    return None
  return Block(side, word, bool(fresh_sp and md.laneChangeNudgeRefused))


def flash_lit(t: float) -> bool:
  """The rim t s after a refused nudge: FLASHES flashes of FLASH_S, FLASH_S apart."""
  return 0.0 <= t < 2 * FLASH_S * FLASHES and int(t / FLASH_S) % 2 == 0


def cuts_tile(alert) -> bool:
  """An alert on screen that takes the tile's place at once: any but the lane-change ones and those drawn as nothing."""
  return alert is not None and event_name(alert) not in HANDOVER and not hud_alerts.frees_top_icons(alert)


def hands_over(alert, alert_out: bool) -> bool:
  """A lane-change alert the tile hands over to is up (not animating out): its green arrow has the tile's corner."""
  return alert is not None and not alert_out and event_name(alert) in HANDOVER


def tile_x(rect: rl.Rectangle, side: str) -> float:
  return rect.x + MARGIN if side == LEFT else rect.x + rect.width - MARGIN - TILE_W


class LaneBlockTile:
  """One per HUD renderer. update() any number of times a frame - the face, the MAX number's gate, the confirm's box,
  the coming limit and the HUD's own state all ask - and it steps once: a frame is a gui_app frame and a SubMaster
  frame, and render() ends it (for a sim that advances neither, as hud_alerts.FrameAlert). `now` is read on the
  frame's first call only, and render() draws at that time."""
  def __init__(self):
    self._alpha = FirstOrderFilter(0.0, FADE_IN_S, 1 / gui_app.target_fps)
    self.block: Block | None = None   # this frame's block, with the setting on
    self._shown: Block | None = None  # the last block drawn: what fades out
    self._cut = False
    self._refused = False
    self._flash_t0: float | None = None
    self._key = None
    self._t = -1e9

  def update(self, sm, on: bool, alert, now: float, started_frame: int = 0, alert_out: bool = False) -> None:
    """alert, alert_out: this frame's alert and whether it is animating out (AlertRenderer.will_render())."""
    key = (gui_app.frame, sm.frame)
    if key == self._key:
      return
    self._key = key
    dt, self._t = now - self._t, now
    if dt > STALE_S:
      self._alpha.x = 0.0     # the view was not drawn for a while (swiped away): no tail
    b = lane_block(sm, started_frame) if on else None
    self._cut = cuts_tile(alert)
    if b is not None and b.refused and not self._refused and not self._cut:
      self._flash_t0 = now
    self._refused = b is not None and b.refused
    self.block = b
    if self._cut or (b is None and hands_over(alert, alert_out)):
      self._alpha.x = 0.0     # an alert has the screen, or the green arrow has the corner: gone on the frame, no tail
    if b is not None and not self._cut:
      if self._shown is not None and self._shown.side != b.side:
        self._alpha.x = 0.0   # the other blinker: a new tile, not a slide
      self._shown = b
    if b is not None and not self._cut:
      self._alpha.update(1.0)   # in: first-order, as the stock MAX number
    else:
      self._alpha.x = max(0.0, self._alpha.x - max(0.0, dt) / FADE_OUT_S)   # out: linear, gone in FADE_OUT_S
    if self._alpha.x < 1e-2 and (b is None or self._cut):
      self._alpha.x = 0.0
      self._shown = None

  @property
  def alpha(self) -> float:
    return self._alpha.x

  def side(self) -> str | None:
    """The side the tile is drawn on this frame (also while it fades out), or None."""
    return self._shown.side if self._shown is not None and self._alpha.x > 1e-2 else None

  def covers_left(self) -> bool:
    return self.side() == LEFT

  def render(self, rect: rl.Rectangle) -> None:
    """Draws this frame's tile, and ends the frame: the next update() steps."""
    self._key = None
    b = self._shown
    if b is None or self._alpha.x < 1e-2:
      return
    lit = self._flash_t0 is not None and flash_lit(self._t - self._flash_t0)
    draw_tile(tile_x(rect, b.side), rect.y + MARGIN, b.side, b.word, self._alpha.x, lit)


def _arrow(cx: float, cy: float, side: str, col: rl.Color):
  """The lane-change arrow from primitives: a rounded shaft into a triangular head, pointing to the side."""
  k = -1 if side == LEFT else 1
  tip = cx + k * ARROW_W / 2
  base = tip - k * ARROW_W * ARROW_HEAD
  tail = cx - k * ARROW_W / 2
  x0, x1 = sorted((tail, base - k * 2))    # into the head by 2 px: no seam
  rl.draw_rectangle_rounded(rl.Rectangle(x0, cy - SHAFT / 2, x1 - x0, SHAFT), 8 / SHAFT, 8, col)
  hd._tri(rl.Vector2(tip, cy), rl.Vector2(base, cy - ARROW_H / 2), rl.Vector2(base, cy + ARROW_H / 2), col)


def _band(x: float, y: float, out: float, thick: float, col: rl.Color):
  """A rounded band round the tile, from `out` px outside its edge to `thick - out` px inside. raylib draws a rounded
  outline from 0.5 px inside the rectangle it is given to thick - 0.5 px outside it."""
  k = thick - out - 0.5
  r = max(0.0, RADIUS - (thick - out))
  w, h = TILE_W - 2 * k, TILE_H - 2 * k
  rl.draw_rectangle_rounded_lines_ex(rl.Rectangle(x + k, y + k, w, h), min(1.0, 2 * r / min(w, h)), 12, thick, col)


def draw_tile(x: float, y: float, side: str, word: str, alpha: float = 1.0, lit: bool = False) -> tuple:
  """The tile at (x, y): black, an orange rim (thicker with a halo while lit), the struck arrow and the word.
  Returns the (x0, y0, x1, y1) of the tile itself."""
  cx = x + TILE_W / 2
  if lit:
    for th, al in HALO:   # each stroke from th - 1.5 px outside the tile to 1.5 px inside it (under the fill)
      _band(x, y, th - 1.5, th, rl.Color(ORANGE.r, ORANGE.g, ORANGE.b, int(al * alpha)))
  rl.draw_rectangle_rounded(rl.Rectangle(x, y, TILE_W, TILE_H), 2 * RADIUS / TILE_W, 12, hd.a(FILL, alpha))
  if lit:
    _band(x, y, (RIM_LIT - RIM) / 2, RIM_LIT, hd.a(ORANGE, alpha))
  else:
    _band(x, y, 0.0, RIM, hd.a(ORANGE, alpha))
  ay = y + ARROW_CY
  _arrow(cx, ay, side, hd.a(ARROW_OFF, alpha))
  p0, p1 = rl.Vector2(cx - STRIKE, ay - STRIKE), rl.Vector2(cx + STRIKE, ay + STRIKE)
  hd._stroke([p0, p1], STRIKE_UNDER_W, hd.a(STRIKE_UNDER, alpha))
  hd._stroke([p0, p1], STRIKE_W, hd.a(ORANGE, alpha))
  size, sx = hd._fit(word, WORD_SIZE, TILE_W - 16)
  hd.text_ink(word, size, cx, y + WORD_CY, hd.a(TEXT, alpha), sx=sx, w=FontWeight.BOLD)
  return x, y, x + TILE_W, y + TILE_H


class BlockTurnIntent(TurnIntent):
  """The stock turn-intent arrow over the wheel, kept up while the tile's block holds the change: upstream it shows only
  on preLaneChangeLeft/Right, which selfdrived does not raise while a block is up."""
  def __init__(self, tile: LaneBlockTile):
    super().__init__()
    self._tile = tile

  def _update_state(self) -> None:
    b = self._tile.block
    if b is None:
      super()._update_state()
      return
    left = b.side == LEFT
    if not self._pre:
      self._turn_intent_rotation_filter.x = self.FADE_IN_ANGLE if left else -self.FADE_IN_ANGLE
    self._pre = True
    self._turn_intent_direction = -1 if left else 1
    self._turn_intent_alpha_filter.update(1)
    self._turn_intent_rotation_filter.update(0)
