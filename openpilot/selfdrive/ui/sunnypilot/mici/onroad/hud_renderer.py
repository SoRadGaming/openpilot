"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer, FONT_SIZES
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_alerts, hud_draw  # FORK(HUD)
from openpilot.system.ui.lib.application import gui_app


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    # FORK(HUD): the compact speed-limit confirm's target, in the stock MAX number's place (hud_alerts.py, item 4)
    hud_alerts.pending_max.drawer = True
    self._pending_alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._pending: hud_alerts.PendingTarget | None = None

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self._draw_pending_max(rect)  # FORK(HUD)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected

  # FORK(HUD): the pending box counts as a top icon, as the stock MAX number does: the driver-monitoring face and the
  # cluster's live speed make way for it
  def drawing_top_icons(self) -> bool:
    return super().drawing_top_icons() or self._pending_alpha.x > 1e-2

  def _draw_pending_max(self, rect: rl.Rectangle) -> None:  # FORK(HUD)
    """While the compact confirm is up, the set speed it would set, in the stock MAX digits' place and size, dashed;
    its pill under it. It follows the alert's own fade, fades out (0.1 s, as the MAX number) when the alert turns into
    one that frees the MAX number ('set speed changed': the solid MAX takes its place), and goes at once when anything
    else takes the top left - a full-screen alert, a banner - so it is never drawn over another alert."""
    item = hud_alerts.pending_max.take()
    if item is not None:
      self._pending = item
      self._pending_alpha.x = item.alpha
    elif self._can_draw_top_icons:
      self._pending_alpha.update(0.0)
    else:
      self._pending_alpha.x = 0.0
    # the stock MAX number fading out as the box comes, or in as it goes: one or the other, never both at full
    alpha = self._pending_alpha.x * (1.0 - self._set_speed_alpha_filter.x)
    if self._pending is None or alpha < 1e-2:
      if self._pending_alpha.x < 1e-2:
        self._pending = None
      return
    p = self._pending
    # the stock MAX digits' place (HudRenderer._draw_set_speed) and its drop shadow's center
    hud_draw.pending_max(rl.Vector2(rect.x + 13 + 4, rect.y + 3 - 8 - 3 + 4), FONT_SIZES.set_speed, p.value, p.text,
                         p.lower, p.color, alpha=alpha, key_alpha=p.key_alpha, note=p.note, max_w=rect.width - 40,
                         shadow_c=rl.Vector2(rect.x + 81, rect.y + 81))
