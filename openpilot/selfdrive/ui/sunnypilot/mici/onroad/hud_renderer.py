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
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import ApproachState, approach_text, box_outline  # FORK(HUD)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import hud_settings  # FORK(HUD)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app


PENDING_STALE_S = 0.25  # FORK(HUD): no frame drawn for this long - the box's state is old, not fading
APPROACH_FREE, APPROACH_MAX, APPROACH_CUT = 0, 1, 2  # FORK(HUD): what else has the top left (_approach_blocker)


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    # FORK(HUD): the compact speed-limit confirm's target, in the stock MAX number's place (hud_alerts.py, item 4)
    hud_alerts.pending_max.drawer = True
    self._pending_alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._pending: hud_alerts.PendingTarget | None = None
    self._pending_t = -1e9      # when the box was last drawn: a box older than PENDING_STALE_S is dropped, not faded
    self._pending_t0: float | None = None  # when the box was first half visible: its outline's clock (box_outline)
    self.alert_renderer = None  # set by the HUD cluster: this frame's alert, for drawing_top_icons
    # FORK(HUD): the coming limit, in the same place (round 5, option C; hud_model.ApproachState)
    self._approach = ApproachState()
    self._approach_alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._approach_t = -1e9
    self._approach_shown = None  # the last coming limit drawn: what fades out once it has gone

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()
    # FORK(HUD): every frame, what the alert renderer will draw - the standstill banner sees the prompt clear even though
    # the alert renderer stops asking once there is no alert (hud_model.StandstillBanner)
    if self.alert_renderer is not None:
      hud_alerts.standstill_banner.observe(self.alert_renderer.will_render()[0])

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self._draw_pending_max(rect)  # FORK(HUD)
    self._draw_approach(rect)  # FORK(HUD)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected

  # FORK(HUD): the pending box and the coming limit count as top icons, as the stock MAX number does: the
  # driver-monitoring face makes way for them
  def drawing_top_icons(self) -> bool:
    return super().drawing_top_icons() or self._box_top_icon() or self._approach_top_icon()

  def _box_top_icon(self) -> bool:  # FORK(HUD)
    """By THIS frame's alert (the face asks before the alert renderer publishes the box): up from the confirm's first
    frame; while the box fades out under the stock MAX number ('set speed changed'); not once anything else is up, nor
    while the confirm itself fades out - the face comes back on the frame it would with no confirm before."""
    ar = self.alert_renderer
    if ar is None:
      return self._pending_alpha.x > 1e-2 and not self._stale()
    alert, animating_out = ar.will_render()
    if alert is None or animating_out:
      return False
    if hud_alerts.box_alert(alert, hud_settings.get()):
      return True
    return hud_alerts.frees_top_icons(alert) and self._pending_alpha.x > 1e-2 and not self._stale()

  def _stale(self) -> bool:  # FORK(HUD)
    return rl.get_time() - self._pending_t > PENDING_STALE_S

  def _draw_pending_max(self, rect: rl.Rectangle) -> None:  # FORK(HUD)
    """While the compact confirm is up, the set speed it would set, in the stock MAX digits' place and size, dashed;
    its pill under it. It follows the alert's own fade, fades out (0.1 s, as the MAX number) when the alert turns into
    one that frees the MAX number ('set speed changed': the solid MAX takes its place), and goes at once when anything
    else takes the top left - a full-screen alert, a banner - so it is never drawn over another alert. Its outline
    flashes twice from the frame it is half visible, then marches (hud_model.box_outline)."""
    item = hud_alerts.pending_max.take()
    now = rl.get_time()
    if item is not None:
      self._pending = item
      self._pending_alpha.x = item.alpha
    elif self._can_draw_top_icons and not self._stale():
      self._pending_alpha.update(0.0)
    else:
      # another alert has the top left, or the road view was not drawn for a while (swiped away mid-prompt): no tail
      self._pending_alpha.x = 0.0
    self._pending_t = now
    # the stock MAX number fading out as the box comes, or in as it goes: one or the other, never both at full
    alpha = self._pending_alpha.x * (1.0 - self._set_speed_alpha_filter.x)
    if self._pending is None or alpha < 1e-2:
      if self._pending_alpha.x < 1e-2:
        self._pending = None
        self._pending_t0 = None
      return
    # the outline's clock starts when the box is first half visible, not when it is first published: a cross-fade with
    # the stock MAX number (route 10f t 183.75, the MAX '105' from engaging 2 s earlier still fading out) would swallow
    # the first flash
    if self._pending_t0 is None and alpha >= 0.5:
      self._pending_t0 = now
    # (before that, the plain dashes: the first flash is a whole one, at full brightness)
    lit, phase = box_outline(now - self._pending_t0) if self._pending_t0 is not None else (False, 0.0)
    p = self._pending
    # the stock MAX digits' place (HudRenderer._draw_set_speed) and its drop shadow's center
    hud_draw.pending_max(rl.Vector2(rect.x + 13 + 4, rect.y + 3 - 8 - 3 + 4), FONT_SIZES.set_speed, p.value, p.text,
                         p.lower, p.color, alpha=alpha, key_alpha=p.key_alpha, note=p.note, max_w=rect.width - 40,
                         shadow_c=rl.Vector2(rect.x + 81, rect.y + 81), lit=lit, phase=phase)

  # FORK(HUD): the coming limit (round 5, option C)
  def _approach_now(self):
    return self._approach.update(ui_state.sm, hud_settings.get(), started_frame=ui_state.started_frame,
                                 now=rl.get_time(), is_metric=ui_state.is_metric,
                                 speed_limit_mode_on=ui_state.speed_limit_mode != 0)

  def _approach_blocker(self) -> int:  # FORK(HUD)
    """What else has the top left this frame: APPROACH_FREE nothing; APPROACH_MAX the stock MAX number, or an alert
    that draws nothing and lets it show ('set speed changed', 'auto adjusting'): the coming limit hands over to it in
    the same place, the two cross-fading as the box and MAX do; APPROACH_CUT any other alert (a banner, a full-screen
    one - including one fading out) or the confirm's box: the coming limit goes on the frame, never drawn over it."""
    ar = self.alert_renderer
    alert = ar.will_render()[0] if ar is not None else None
    if alert is not None:
      return APPROACH_MAX if hud_alerts.frees_top_icons(alert) else APPROACH_CUT
    if self._pending is not None:
      return APPROACH_CUT
    return APPROACH_MAX if self._set_speed_alpha_filter.x > 1e-2 else APPROACH_FREE

  def _approach_top_icon(self) -> bool:  # FORK(HUD)
    """A top icon from the frame it is first drawn (the face asks before the HUD draws), and while it fades out under
    the stock MAX number, so the face never shows for a frame at the hand-over."""
    blocker = self._approach_blocker()
    if blocker == APPROACH_CUT:
      return False
    fading = self._approach_alpha.x > 1e-2 and rl.get_time() - self._approach_t <= PENDING_STALE_S
    return fading or (blocker == APPROACH_FREE and self._approach_now() is not None)

  def _draw_approach(self, rect: rl.Rectangle) -> None:  # FORK(HUD)
    """The coming limit in the MAX number's place: fades in (0.1 s, as the MAX number); fades out when the look-ahead
    and its hold end, or under the stock MAX number taking the place; goes at once when anything else takes it."""
    ap = self._approach_now()
    now = rl.get_time()
    blocker = self._approach_blocker()
    if blocker == APPROACH_CUT or now - self._approach_t > PENDING_STALE_S:
      self._approach_alpha.x = 0.0   # (or the road view was not drawn for a while: no tail)
    self._approach_alpha.update(1.0 if ap is not None and blocker == APPROACH_FREE else 0.0)
    self._approach_t = now
    if ap is not None:
      self._approach_shown = ap
    shown = self._approach_shown
    # the stock MAX number fading in as it goes: one or the other, never both at full
    alpha = self._approach_alpha.x * (1.0 - self._set_speed_alpha_filter.x)
    if shown is None or alpha < 1e-2:
      return
    from openpilot.selfdrive.ui.sunnypilot.onroad.speed_limit import SpeedLimitRenderer
    text = approach_text(SpeedLimitRenderer._format_dist(shown.dist), shown.held)
    frac = 0.0 if text == "now" else shown.frac   # 'now': the bar has drained
    hud_draw.approach_max(rl.Vector2(rect.x + 17 + 18, rect.y + 6), shown.limit, shown.lower, text, frac,
                          alpha=alpha, shadow_c=rl.Vector2(rect.x + 60, rect.y + 52))
