"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): three changes to how the comma 4 draws existing alerts. The alerts themselves (text, priority, sound, when
they fire) are untouched: this is drawing only, called from two marked lines in mici/onroad/alert_renderer.py and one in
augmented_road_view.py, and with the settings off those lines fall through to the stock drawing.

1. THE STANDSTILL PROMPT AS A COMPACT BANNER (HudStoppedBanner). On this Honda every stop raises 'TAKE CONTROL / Resume
   Driving Manually' (manualRestart: userPrompt, mid, silent, low priority), drawn over the whole screen for the whole
   stop. With the setting on it is a 66 px banner top left in the alert's own color instead, and the road - and the
   cluster's stop time - stay visible. The FULL alert comes back when the car ahead moves off (hud_model.StandstillBanner):
   the one moment in a stop the prompt is for. It stays full until the prompt clears; the next stop starts compact.

2. THE SPEED-LIMIT CONFIRM SHOWS THE LIMIT (HudConfirmLimit). 'Press - (or +) to confirm speed limit' draws a green
   arrow and never says which limit. With the setting on the arrow's box holds the pending limit (a sign with a dashed
   ring: not confirmed yet) beside the same green '-' or '+'. A speed-limit offset is shown on the sign as a small
   number, as sunnypilot's own sign does: the confirm sets limit + offset, and the '+' or '-' compares the set speed with
   that sum. The key keeps the arrow's blink; the sign stays solid.

3. COMPACT ALERTS (HudCompactLimitPrompts, HudCompactDisengage, HudCompactTurn; which alerts: hud_model.compact_kind).
   'Auto adjusting to speed limit', 'Set speed changed' and 'Auto adjusting to last speed limit' draw nothing over the
   road: the stock MAX number shows the new set speed (augmented_road_view lets it, frees_top_icons) and the cluster
   stays. 'Cruise off', 'lane centering off' and 'turning left / right' are banners in the alert's color. Every banner
   stops short of what the cluster drew this frame (ClusterEdge). Sounds are untouched: soundd plays them from
   selfdriveState.

4. THE COMPACT CONFIRM IN THE MAX NUMBER'S PLACE (HudCompactLimitPrompts; the owner's design A, 2026-10-06). 'Press + (or
   -) to confirm speed limit' draws, top left where the stock MAX number goes and at its size, the set speed the confirm
   would set (limit + offset, hud_model.target_speed) - with a dashed outline instead of 'MAX' (not set yet) and under
   it a pill: 'press + to confirm' and the blinking green key. This file only publishes it (PendingMax); the HUD
   renderer (sunnypilot/mici/onroad/hud_renderer.py), drawn after the alerts, draws it, and counts it as a top icon, as
   it does the stock MAX number. With an offset and no sign in the cluster (HudLimitSign, or one not yet half faded in)
   the pill also names the limit: 'limit 50 +5'. After the confirm the stock MAX number shows the new set speed, solid,
   in the same place. With no limit, a text naming no key (the PCM one) or no HUD renderer to draw it, the confirm is
   the one-line banner instead: 'press + to confirm' and the key - and, with no sign in the cluster, the pending sign,
   whatever HudConfirmLimit says. While the confirm is up the cluster's own sign is drawn pending (dashed, with
   HudConfirmLimit). A confirm with no words (the set speed already equal to the limit, a frame or two) draws nothing.
"""
import math
from dataclasses import dataclass

import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (StandstillBanner, pending_limit, target_speed,
                                                                     short_line2, compact_kind, compact_text, confirm_text,
                                                                     confirm_lower, event_name, COMPACT_NONE,
                                                                     COMPACT_QUIET, CONFIRM_EVENT)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import frees_top_icons as _frees_top_icons
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import hud_settings
from openpilot.selfdrive.ui.ui_state import ui_state

BANNER_X = 8
BANNER_Y = 6
BANNER_GAP = 14        # a banner stops this far short of the cluster
BANNER_MAX_W = 300     # ... and is never wider than this (no cluster drawn: about half the road)

# one decision for the cluster and the alert renderer
standstill_banner = StandstillBanner()


class ClusterEdge:
  """What the cluster drew this frame - HudCluster._render runs before the alert renderer and fills it in: the left
  edge of its top row (speed or stop time, and the sign), the left edge and top of its second row (the next lower
  limit), and whether it drew its sign. inf / False when it drew nothing there."""
  def __init__(self):
    self.reset()

  def reset(self):
    self.row1 = self.row2 = self.row2_top = math.inf
    self.sign = False


cluster_edge = ClusterEdge()


@dataclass(frozen=True)
class PendingTarget:
  """What the compact confirm puts in the MAX number's place this frame (hud_draw.pending_max)."""
  value: int                 # the set speed the confirm would set, display units
  text: str                  # 'press + to confirm'
  lower: bool | None         # the key: True '-', False '+', None none
  color: rl.Color            # the alert's color, for the pill
  alpha: float               # the alert's fade
  key_alpha: float           # the arrow's blink, 0..1
  note: str = ""             # 'limit 50 +5', or nothing


class PendingMax:
  """The hand-over from the alert renderer to the HUD renderer, which draws after it in the same frame: publish() every
  frame the compact confirm is drawn, take() once a frame - nothing taken = the confirm is not up (the renderer fades the
  box out). drawer: a HUD renderer that draws it exists (HudRendererSP, the sunnypilot UI's); without one the confirm
  stays the banner, so it is never drawn nowhere."""
  def __init__(self):
    self.drawer = False
    self._item: PendingTarget | None = None

  def publish(self, item: PendingTarget):
    self._item = item

  def take(self) -> PendingTarget | None:
    item, self._item = self._item, None
    return item


pending_max = PendingMax()


def banner_max_w(rect: rl.Rectangle, h: float) -> float:
  """How wide a banner h tall may be: up to the cluster's rows it would meet, less BANNER_GAP."""
  right = cluster_edge.row1
  if rect.y + BANNER_Y + h > cluster_edge.row2_top:
    right = min(right, cluster_edge.row2)
  return max(80.0, min(BANNER_MAX_W, right - BANNER_GAP - (rect.x + BANNER_X)))


def frees_top_icons(alert) -> bool:
  """augmented_road_view: the alert up draws nothing (compact), so the stock MAX number may show the set speed."""
  return alert is not None and _frees_top_icons(alert, hud_settings.get())


def draw_compact(ar, alert) -> bool:
  """Called by AlertRenderer._render with the alert it is about to draw. Draws it compact and returns True, or returns
  False and the stock full-screen drawing runs: the standstill banner first, then the compact groups."""
  if draw_compact_standstill(ar, alert):
    return True
  s = hud_settings.get()
  if compact_kind(alert, s) == COMPACT_NONE:
    return False
  name = event_name(alert)
  if name in COMPACT_QUIET or (name == CONFIRM_EVENT and not confirm_text(alert.text1)):
    return True   # nothing over the road (a wordless confirm too: no empty pill)
  from openpilot.selfdrive.ui.mici.onroad.alert_renderer import ALERT_COLORS, AlertStatus
  color = ALERT_COLORS.get(alert.status, ALERT_COLORS[AlertStatus.normal])
  x, y, alpha = ar._rect.x + BANNER_X, ar._rect.y + BANNER_Y, ar._alpha_filter.x
  if name == CONFIRM_EVENT:
    _draw_confirm(ar, alert, s, x, y, color, alpha)
  else:
    line1, line2 = compact_text(alert)
    h = hd.BANNER_H if line2 else hd.BANNER_LINE_H
    hd.compact_banner(x, y, line1, line2, color, alpha=alpha, max_w=banner_max_w(ar._rect, h))
  return True


def _draw_confirm(ar, alert, s, x: float, y: float, color: rl.Color, alpha: float):
  # the key's direction from the text, so the two can never disagree (sunnypilot's arrow rounds differently at .5 ties:
  # a 72.5 km/h set speed on route 114); its blink from the arrow. Only a text naming no key (the PCM one) takes the
  # arrow's direction.
  _, icon, icon_alpha, _, _ = ar.speed_limit_pre_active_icon_helper()
  lower = confirm_lower(alert.text1)
  names_key = lower is not None
  if lower is None:
    lower = True if icon.id == ar.arrow_down.id else False if icon.id == ar.arrow_up.id else None
  # the owner's design A: the target in the MAX number's place, drawn by the HUD renderer after the alerts. Only for a
  # '+' / '-' text: the PCM one asks for another set speed than the target
  target = target_speed(ui_state.sm, ui_state.is_metric) if names_key and pending_max.drawer else 0
  if target > 0:
    note = ""
    if not cluster_edge.sign:   # the limit is on screen nowhere else: name it when the target is not the limit itself
      limit, offset = pending_limit(ui_state.sm, ui_state.is_metric)
      if offset and limit > 0:
        note = f"limit {limit} {'+' if offset > 0 else '-'}{abs(offset)}"
    pending_max.publish(PendingTarget(target, confirm_text(alert.text1), lower, color, alpha, icon_alpha / 255.0, note))
    return
  value, offset = 0, 0
  if not cluster_edge.sign:   # no limit on screen otherwise (owner decision 7, whatever HudConfirmLimit says)
    value, offset = pending_limit(ui_state.sm, ui_state.is_metric)
  h = hd.CONFIRM_H if value > 0 else hd.BANNER_LINE_H
  hd.confirm_banner(x, y, confirm_text(alert.text1), lower, color, alpha=alpha, key_alpha=(icon_alpha / 255.0) * alpha,
                    value=value, offset=offset, max_w=banner_max_w(ar._rect, h))


def draw_compact_standstill(ar, alert) -> bool:
  """Called by AlertRenderer._render with the alert it is about to draw. Draws the banner and returns True, or returns
  False and the stock full-screen drawing runs."""
  if not standstill_banner.compact(alert, hud_settings.get(), ui_state.sm, ui_state.started_frame, rl.get_time()):
    return False
  from openpilot.selfdrive.ui.mici.onroad.alert_renderer import ALERT_COLORS, AlertStatus
  color = ALERT_COLORS.get(alert.status, ALERT_COLORS[AlertStatus.normal])
  hd.standstill_banner(ar._rect.x + BANNER_X, ar._rect.y + BANNER_Y, alert.text1, short_line2(alert.text2), color,
                       alpha=ar._alpha_filter.x)
  return True


def draw_pending_limit(ar, alert_layout) -> bool:
  """Called by AlertRenderer._draw_icons. True when it drew the pending limit in the arrow's place."""
  icon = alert_layout.icon
  if icon is None or not hud_settings.get().confirm_limit:
    return False
  tid = icon.texture.id
  if tid not in (ar.arrow_up.id, ar.arrow_down.id):
    return False
  value, offset = pending_limit(ui_state.sm, ui_state.is_metric)
  if value <= 0:
    return False
  size = icon.texture.width
  bx = ar._rect.x + ar._rect.width - icon.margin_x - size
  by = ar._rect.y + icon.margin_y
  full = ar._alpha_filter.x
  hd.pending_icon(bx, by, size, value, lower=tid == ar.arrow_down.id, key_alpha=(icon.alpha / 255.0) * full, sign_alpha=full,
                  offset=offset)
  return True
