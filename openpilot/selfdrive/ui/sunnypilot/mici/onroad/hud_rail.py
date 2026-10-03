"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4's right rail - the top of the 60 px strip that holds the confidence ball. ONE item at a time:

    PLANNED STOP   an arrow down onto a stop line, and how far ahead the model's speed plan comes to a stop ('20 m').
                   White with a solid line while openpilot is driving the speed on that plan; grey with a dashed line
                   when it is only the model's plan (openpilot disengaged, in chill mode, or another target in charge).
    CURVE          a curve arrow (left or right) and openpilot's target speed for it ('37 km/h'), while smart cruise
                   control is slowing for a curve or about to.

A planned stop wins over a curve. Never a reason: no message knows whether the plan stops for a light, a sign or a queue
the radar has not locked onto, so there are no light or sign icons. The rules are hud_model.RailState; this file draws.

The approved style-B rail (2026-10-03 mockups): a 48 px glyph from y 6, ~18 px figures under it. While an item shows,
the stock confidence ball - same size, math and colors - rides only the strip below it: its center is held at or
below BALL_FLOOR, so it moves down only when it would otherwise reach the item. An item fades in over FADE_S while the
ball eases down out of its way (a 90 px jump in one frame read like a sudden drop in confidence), and fades out while
the ball eases back. With both settings off nothing is drawn and the ball is not touched: the strip is the stock one,
pixel for pixel.
"""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import RailFrame, RailState, RAIL_NONE, RAIL_STOP
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings, hud_settings
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets import Widget

# layout, device pixels from the strip's top-left corner (the strip is SIDE_PANEL_WIDTH = 60 wide)
GLYPH = 48
TOP = 6
FIG_CAP = 18            # the figures' ink height
FIG_GAP = 4
FIG_MAX_W = 56
ITEM_BOTTOM = TOP + GLYPH + FIG_GAP + FIG_CAP   # 76
BALL_R = 24             # the stock ball's radius (mici/onroad/confidence_ball.py)
BALL_GAP = 14           # clear space between the item and the ball's top; > 11 px, so the ball's black corner ring
                        # (radius 35) never reaches the item either
BALL_FLOOR = ITEM_BOTTOM + BALL_GAP + BALL_R    # 114: the ball's center is never above this while an item shows
FADE_S = 0.15           # an item fades in (out) over this while the ball eases down (back up), on the UI's clock


def ease(p: float) -> float:
  """Smoothstep: 0 and 1 at the ends, gentle at both."""
  p = max(0.0, min(1.0, p))
  return p * p * (3.0 - 2.0 * p)


class HudRail(Widget):
  def __init__(self, confidence_ball=None):
    super().__init__()
    self._ball = confidence_ball
    self._state = RailState()
    self._presence = 0.0          # 0 = nothing (the stock strip) .. 1 = the item drawn in full, the ball under it
    self._last = RailFrame()      # the item last shown: what a fade-out draws
    self._t: float | None = None
    self._dt = 0.0
    self.settings: HudSettings = hud_settings.settings
    self.frame = RailFrame()

  def _update_state(self):
    now = rl.get_time()
    self._dt = 0.0 if self._t is None else min(max(now - self._t, 0.0), 0.1)  # a stalled frame does not skip the fade
    self._t = now
    self.settings = hud_settings.get()
    self.frame = self._state.update(ui_state.sm, self.settings, started_frame=ui_state.started_frame, now=now,
                                    is_metric=ui_state.is_metric)

  def _step_presence(self, shown: bool) -> float:
    """One frame of the fade; returns the eased presence, 0..1."""
    step = self._dt / FADE_S
    self._presence = min(1.0, self._presence + step) if shown else max(0.0, self._presence - step)
    return ease(self._presence)

  def _ball_floor(self, rect: rl.Rectangle, k: float):
    """Hold the ball under the item: its floor eases from the strip's top down to BALL_FLOOR as the item fades in, and
    back as it fades out. At 0 the floor is the strip's top, which the ball (center >= its radius) never goes above: the
    stock position, exactly."""
    if self._ball is not None:
      self._ball.hud_floor_y = rect.y + BALL_FLOOR * k

  def _render(self, rect: rl.Rectangle):
    f = self.frame
    if f.kind != RAIL_NONE:
      self._last = f
    k = self._step_presence(f.kind != RAIL_NONE)
    self._ball_floor(rect, k)
    f = self._last
    if k <= 0.0 or f.kind == RAIL_NONE:
      return
    cx = rect.x + rect.width - SIDE_PANEL_WIDTH / 2 - 1
    top = rect.y + TOP
    fig_cy = top + GLYPH + FIG_GAP + FIG_CAP / 2
    if f.kind == RAIL_STOP:
      hd.stop_glyph(cx, top, GLYPH, f.solid, fade=k)
      col = hd.WHITE if f.solid else hd.RAIL_GREY
    else:
      hd.curve_glyph(cx, top, GLYPH, f.curve_left, col=hd.dim(hd.WHITE, k))
      col = hd.WHITE
    hd.rail_figure(f.num, f.unit, cx, fig_cy, FIG_CAP, FIG_MAX_W, hd.dim(col, k))
