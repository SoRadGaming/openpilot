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
the stock confidence ball - same size, maths and colours - rides only the strip below it: its centre is held at or
below BALL_FLOOR, so it moves down only when it would otherwise reach the item, and glides back up after. With both
settings off nothing is drawn and the ball is not touched: the strip is the stock one, pixel for pixel.
"""
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (RailFrame, RailState, RAIL_NONE, RAIL_STOP,
                                                                     fmt_speed, fmt_stop_dist)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings, hud_settings
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
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
BALL_FLOOR = ITEM_BOTTOM + BALL_GAP + BALL_R    # 114: the ball's centre is never above this while an item shows
BALL_RISE_S = 0.15      # after the item goes, the ball's floor eases back up over about this long


class HudRail(Widget):
  def __init__(self, confidence_ball=None):
    super().__init__()
    self._ball = confidence_ball
    self._state = RailState()
    self._floor = FirstOrderFilter(0.0, BALL_RISE_S, 1 / gui_app.target_fps)
    self.settings: HudSettings = hud_settings.settings
    self.frame = RailFrame()

  def _update_state(self):
    self.settings = hud_settings.get()
    self.frame = self._state.update(ui_state.sm, self.settings, started_frame=ui_state.started_frame, now=rl.get_time())

  def _ball_floor(self, rect: rl.Rectangle, shown: bool):
    """Hold the ball under the item: drops at once when an item appears (the item is drawn first, so the ball must
    never sit on it, even for a frame), eases back up when it goes. Without an item the floor is the strip's top, which
    the ball (centre >= its radius) never goes above: the stock position."""
    target = rect.y + BALL_FLOOR if shown else rect.y
    if target >= self._floor.x:
      self._floor.x = target
    else:
      self._floor.update(target)
    if self._ball is not None:
      self._ball.hud_floor_y = self._floor.x

  def _render(self, rect: rl.Rectangle):
    f = self.frame
    self._ball_floor(rect, f.kind != RAIL_NONE)
    if f.kind == RAIL_NONE:
      return
    cx = rect.x + rect.width - SIDE_PANEL_WIDTH / 2 - 1
    top = rect.y + TOP
    fig_cy = top + GLYPH + FIG_GAP + FIG_CAP / 2
    if f.kind == RAIL_STOP:
      hd.stop_glyph(cx, top, GLYPH, f.solid)
      num, unit = fmt_stop_dist(f.stop_m, ui_state.is_metric)
      col = hd.WHITE if f.solid else hd.RAIL_GREY
    else:
      hd.curve_glyph(cx, top, GLYPH, f.curve_left)
      num, unit = fmt_speed(f.curve_v, ui_state.is_metric)
      col = hd.WHITE
    hd.rail_figure(num, unit, cx, fig_cy, FIG_CAP, FIG_MAX_W, col)
