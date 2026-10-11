"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): how the comma 4 draws existing alerts. The alerts themselves (text, priority, sound, when they fire) are
untouched: this is drawing only, called from one marked line in mici/onroad/alert_renderer.py and one in
augmented_road_view.py, and with the settings off those lines fall through to the stock drawing.

1. THE STANDSTILL PROMPT AS A COMPACT BANNER (HudStoppedBanner). On this Honda every stop raises 'TAKE CONTROL / Resume
   Driving Manually' (manualRestart: userPrompt, mid, silent, low priority), drawn over the whole screen for the whole
   stop. With the setting on it is a 66 px banner top left in the alert's own color instead, and the road - and the
   stop timer - stay visible. The FULL alert comes back when the car ahead moves off (hud_model.StandstillBanner): the
   one moment in a stop the prompt is for. It stays full until the prompt clears; the next stop starts compact.

2. COMPACT ALERTS (HudCompactLimitPrompts, HudCompactDisengage, HudCompactTurn; which alerts: hud_model.compact_kind).
   'Auto adjusting to speed limit', 'Set speed changed' and 'Auto adjusting to last speed limit' draw nothing over the
   road: the stock MAX number shows the new set speed (augmented_road_view lets it, frees_top_icons). 'Cruise off',
   'lane centering off' and 'turning left / right' are banners in the alert's color. Every banner stops short of the
   stop timer when it is drawn (ClusterEdge). Sounds are untouched: soundd plays them from selfdriveState.

3. THE COMPACT CONFIRM IN THE MAX NUMBER'S PLACE (HudCompactLimitPrompts; 2026-10-06, restyled 2026-10-12). 'Press +
   (or -) to confirm speed limit' draws, top left where the stock MAX number goes, the set speed the confirm would set
   (limit + offset, hud_model.target_speed) as a card in the style HudLimitStyle picks (the split card: the MAX held now
   small beside it, the target dashed, a footer; stock-plus: the target in the stock MAX digits on a solid card), with
   'press - to set 100' and the blinking green key (hud_model.confirm_prompt: the key the alert names, the speed the press
   gives). Its outline flashes twice when it appears, then marches (split card) or breathes (stock-plus) while the
   prompt waits. This file only publishes it (PendingMax); the HUD renderer (sunnypilot/mici/onroad/hud_renderer.py),
   drawn after the alerts, draws it, and counts it as a top icon, as it does the stock MAX number. With an offset the
   card also names the limit: 'limit 50 +5' - there is no speed-limit sign on screen since round 5. After the confirm
   the stock MAX number shows the new set speed in the same place (the split card's target slides there first). With no
   limit, a text naming no key (the PCM one) or no HUD renderer to draw it, the confirm is the one-line banner instead:
   'press + to set 55' ('press + to confirm' with no limit), the key and the pending limit as a dashed sign. A confirm
   with no words (the set speed already equal to the limit, a frame or two) draws nothing. With the setting off the
   confirm is sunnypilot's own full-screen alert, exactly as stock (the old drawing of the limit in the arrow's box was
   removed in round 5).
"""
import math
from dataclasses import dataclass

import pyray as rl

from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (StandstillBanner, pending_limit, target_speed,
                                                                     short_line2, compact_kind, compact_text, confirm_text,
                                                                     confirm_lower, confirm_prompt, event_name,
                                                                     COMPACT_NONE, COMPACT_QUIET, CONFIRM_EVENT)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import frees_top_icons as _frees_top_icons
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import hud_settings
from openpilot.selfdrive.ui.ui_state import ui_state

BANNER_X = 8
BANNER_Y = 6
BANNER_GAP = 14        # a banner stops this far short of the stop timer
BANNER_MAX_W = 300     # ... and is never wider than this (no timer drawn: about half the road)

# one decision for the cluster and the alert renderer
standstill_banner = StandstillBanner()


class ClusterEdge:
  """What the cluster drew this frame - HudCluster._render runs before the alert renderer and fills it in: the left
  edge of the stop timer (the stopwatch glyph). inf when it drew nothing."""
  def __init__(self):
    self.reset()

  def reset(self):
    self.timer_left = math.inf


cluster_edge = ClusterEdge()


class FrameAlert:
  """This frame's (alert, animating_out), as AlertRenderer.will_render() gives it, looked up ONCE a frame for every
  FORK(HUD) question: the face's (drawing_top_icons: the box, the coming limit), the cluster's, the standstill banner's
  observe() and the coming limit's drawing. They all see the answer as it was BEFORE the alert renderer drew this frame
  (its render clears its previous alert once the fade has ended): one decision, never two that disagree on the frame an
  alert finishes fading. A new frame is a new gui_app frame or SubMaster frame, a new selfdriveState message, or
  clear() - which HudRendererSP calls once it has drawn, the last to ask (a sim that advances neither frame counter)."""
  def __init__(self):
    self.clear()

  def clear(self):
    self._key: tuple | None = None
    self._ss = None
    self._value: tuple = (None, False)

  def get(self, ar) -> tuple:
    if ar is None:
      return None, False
    from openpilot.system.ui.lib.application import gui_app
    ss = ui_state.sm['selfdriveState']
    key = (id(ar), gui_app.frame, ui_state.sm.frame)
    if key != self._key or ss is not self._ss:
      self._key, self._ss, self._value = key, ss, ar.will_render()
    return self._value


frame_alert = FrameAlert()


@dataclass(frozen=True)
class PendingTarget:
  """What the compact confirm puts in the MAX number's place this frame (HudRendererSP: a hud_model.MaxCard)."""
  value: int                 # the set speed the confirm would set, display units
  text: str                  # 'press + to set 55'
  lower: bool | None         # the key: True '-', False '+', None none
  color: rl.Color            # the alert's color, for the card
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
  """How wide a banner may be: up to the stop timer, less BANNER_GAP. (h, the banner's height: every banner is within
  the timer's row.)"""
  return max(80.0, min(BANNER_MAX_W, cluster_edge.timer_left - BANNER_GAP - (rect.x + BANNER_X)))


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


def confirm_target(alert) -> int:
  """The set speed the compact confirm `alert` puts in the MAX number's place, or 0 when it is drawn as the banner: no
  limit, a text naming no key (the PCM one asks for another set speed than the target) or no HUD renderer to draw it."""
  if not pending_max.drawer or confirm_lower(alert.text1) is None:
    return 0
  return _target()


def _target() -> int:
  """The set speed the confirm would set (hud_model.target_speed), as this car stores it; 0 with no limit."""
  cp_sp = getattr(ui_state, "CP_SP", None)
  return target_speed(ui_state.sm, ui_state.is_metric, cp_sp.pcmCruiseSpeed if cp_sp is not None else True)


def box_alert(alert, s) -> bool:
  """alert, drawn this frame, is the compact confirm as the box: draw_compact -> _draw_confirm publishes it. The HUD
  renderer asks this before the alert renderer has drawn (the face and the cluster come first), so whether the box is a
  top icon is this frame's fact, not the last frame's fade."""
  return (alert is not None and event_name(alert) == CONFIRM_EVENT and compact_kind(alert, s) != COMPACT_NONE and
          bool(confirm_text(alert.text1)) and confirm_target(alert) > 0)


def _draw_confirm(ar, alert, s, x: float, y: float, color: rl.Color, alpha: float):
  # the key's direction from the text, so the two can never disagree (sunnypilot's arrow rounds differently at .5 ties:
  # a 72.5 km/h set speed on route 114); its blink from the arrow. Only a text naming no key (the PCM one) takes the
  # arrow's direction.
  _, icon, icon_alpha, _, _ = ar.speed_limit_pre_active_icon_helper()
  lower = confirm_lower(alert.text1)
  if lower is None:
    lower = True if icon.id == ar.arrow_down.id else False if icon.id == ar.arrow_up.id else None
  # the target in the MAX number's place, drawn by the HUD renderer after the alerts
  target = confirm_target(alert)
  if target > 0:
    # the limit is on screen nowhere else: name it when the target is not the limit itself
    note = ""
    limit, _ = pending_limit(ui_state.sm, ui_state.is_metric)
    d = target - limit          # what the number adds to the limit, so the note always adds up to it (a % offset
    if limit > 0 and d:         # rounds apart from the target; the cruise range can clamp it)
      note = f"limit {limit} {'+' if d > 0 else '-'}{abs(d)}"
    pending_max.publish(PendingTarget(target, confirm_prompt(alert.text1, target), lower, color, alpha, icon_alpha / 255.0,
                                      note))
    return
  value, offset = pending_limit(ui_state.sm, ui_state.is_metric)   # no limit on screen otherwise (owner decision 7)
  h = hd.CONFIRM_H if value > 0 else hd.BANNER_LINE_H
  hd.confirm_banner(x, y, confirm_prompt(alert.text1, _target()), lower, color, alpha=alpha,
                    key_alpha=(icon_alpha / 255.0) * alpha,
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

