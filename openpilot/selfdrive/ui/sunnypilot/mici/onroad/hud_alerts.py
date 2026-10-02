"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): two changes to how the comma 4 draws existing alerts. The alerts themselves (text, priority, sound, when
they fire) are untouched: this is drawing only, called from two marked lines in mici/onroad/alert_renderer.py, and with
the settings off those lines fall through to the stock drawing.

1. THE STANDSTILL PROMPT AS A COMPACT BANNER (HudStoppedBanner). On this Honda every stop raises 'TAKE CONTROL / Resume
   Driving Manually' (manualRestart: userPrompt, mid, silent, low priority), drawn over the whole screen for the whole
   stop. With the setting on it is a 66 px banner top left in the alert's own colour instead, and the road - and the
   cluster's stop time - stay visible. The FULL alert comes back when the car ahead moves off (hud_model.StandstillBanner):
   the one moment in a stop the prompt is for. It stays full until the prompt clears.

2. THE SPEED-LIMIT CONFIRM SHOWS THE LIMIT (HudConfirmLimit). 'Press - (or +) to confirm speed limit' draws a green
   arrow and never says which limit. With the setting on the arrow's box holds the pending limit (a sign with a dashed
   ring: not confirmed yet) beside the same green '-' or '+'. The key keeps the arrow's blink; the sign stays solid.
"""
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import StandstillBanner, pending_limit, short_line2
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import hud_settings
from openpilot.selfdrive.ui.ui_state import ui_state

BANNER_X = 8
BANNER_Y = 6

# one decision for the cluster and the alert renderer
standstill_banner = StandstillBanner()


def draw_compact_standstill(ar, alert) -> bool:
  """Called by AlertRenderer._render with the alert it is about to draw. Draws the banner and returns True, or returns
  False and the stock full-screen drawing runs."""
  if not standstill_banner.compact(alert, hud_settings.get(), ui_state.sm):
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
  value = pending_limit(ui_state.sm, ui_state.is_metric)
  if value <= 0:
    return False
  size = icon.texture.width
  bx = ar._rect.x + ar._rect.width - icon.margin_x - size
  by = ar._rect.y + icon.margin_y
  full = ar._alpha_filter.x
  hd.pending_icon(bx, by, size, value, lower=tid == ar.arrow_down.id, key_alpha=(icon.alpha / 255.0) * full, sign_alpha=full)
  return True
