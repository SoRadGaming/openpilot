"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import dataclasses

import pyray as rl

from openpilot.common.constants import CV
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer, CRUISE_DISABLED_CHAR, KM_TO_MILE
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_alerts, hud_draw  # FORK(HUD)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import (Approach, ApproachState, MaxCard,  # FORK(HUD)
                                                                     approach_text, arm_flash, armed_text, box_breath,
                                                                     box_outline, ease_in_out, ease_out, event_name,
                                                                     fresh, press_words, resolver_map_limit,
                                                                     CARD_APPROACH, CARD_ENTER_S, CARD_GROW_S,
                                                                     CARD_PENDING, CARD_DONE, CARD_DONE_S, CONFIRM_EVENT,
                                                                     NOW_HALO_S)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import hud_settings, STYLE_SPLIT, STYLE_STOCK_PLUS  # FORK(HUD)
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_lane_block import BlockTurnIntent, LaneBlockTile  # FORK(HUD)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app


PENDING_STALE_S = 0.25  # FORK(HUD): no frame drawn for this long - the box's state is old, not fading
APPROACH_FREE, APPROACH_MAX, APPROACH_CUT = 0, 1, 2  # FORK(HUD): what else has the top left (_approach_blocker)
CARD_RIGHT_MARGIN = 40  # FORK(HUD): the card in MAX's place stops this far short of the road's right edge (_draw_card)


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
    self._pending_had = False   # the last frame took a box: one taken after a frame with none is a new prompt ...
    self._pending_out = False   # ... as is one taken while the alert is up again after it was fading out
    self.alert_renderer = None  # set by the HUD cluster: this frame's alert, for drawing_top_icons
    # FORK(HUD): the coming limit, in the same place (round 5; hud_model.ApproachState)
    self._approach = ApproachState()
    self._approach_alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._approach_t = -1e9
    self._approach_shown = None  # the last coming limit drawn: what fades out once it has gone
    # FORK(HUD): both drawn as a card in the style HudLimitStyle picks (hud_model.MaxCard); the card's own clocks
    self._approach_t0: float | None = None  # the coming limit's first frame on screen: the split card opens from it
    self._now_t0: float | None = None       # ... its first 'now': the red bar's glow / the outline's halo
    self._approach_seen = -1e9              # ... its last frame at least half visible, and that alpha: a confirm
    self._approach_seen_alpha = 0.0         # coming within PENDING_STALE_S of it takes its card over (no fade between)
    self._grow_t0: float | None = None      # the confirm's card turning from the coming limit's (CARD_GROW_S)
    self._grow_alpha = 0.0
    self._card_current = ""                 # the MAX the confirm's card last showed beside its target
    self._card_alpha = 0.0                  # ... and how strongly it was last drawn
    self._done_t0: float | None = None      # the press done: the card turning into the stock MAX (CARD_DONE_S) ...
    self._done_from = ""                    # ... with the MAX it replaces beside it ...
    self._done_alpha0 = 1.0                 # ... from the strength the card (or the stock MAX) had: no jump
    # FORK(SPEED-LIMIT): sla-flash - a press at 'now' held by the planner (assist.armedLimit): the card is drawn from it
    self._armed_t0: float | None = None     # its first frame armed: the number's flash runs from it (arm_flash)
    self._armed_shown: tuple | None = None  # (limit, target) of the last armed card: what fades out once the arm drops
    # FORK(HUD): a refused lane change as a tile on the blinker side (hud_lane_block.py); the wheel's turn-intent arrow
    # stays up under it
    self._lane_block = LaneBlockTile()
    self._turn_intent = BlockTurnIntent(self._lane_block)

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()
    # FORK(HUD): every frame, what the alert renderer will draw - the standstill banner sees the prompt clear even though
    # the alert renderer stops asking once there is no alert (hud_model.StandstillBanner)
    if self.alert_renderer is not None:
      hud_alerts.standstill_banner.observe(self._frame_alert()[0])
    self._lane_block_now()  # FORK(HUD): before the wheel's turn-intent arrow asks

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self._draw_pending_max(rect)  # FORK(HUD)
    self._draw_approach(rect)  # FORK(HUD)
    self._lane_block.render(rect)  # FORK(HUD): over the top-left items, which make way for it
    self.blind_spot_indicators.render(rect)
    hud_alerts.frame_alert.clear()  # FORK(HUD): the last to ask this frame

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected

  # FORK(HUD): the confirm's card, the coming limit, the confirm's end and a left lane-block tile count as top icons, as
  # the stock MAX number does: the driver-monitoring face makes way for them
  def drawing_top_icons(self) -> bool:
    return (super().drawing_top_icons() or self._box_top_icon() or self._approach_top_icon() or self._lane_block_left()
            or self._done_t0 is not None)

  # FORK(HUD): the confirm's end (_done) draws the new MAX itself until the card has become it, then hands over
  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    if self._done_t0 is None:
      super()._draw_set_speed(rect)

  def _current_text(self) -> str:  # FORK(HUD)
    """The set speed as the stock MAX number shows it (HudRenderer._draw_set_speed) - and only while it would show it:
    the stock MAX is never drawn while openpilot is not engaged, and on this non-PCM car the set speed keeps its last
    value then, so a card beside a coming limit would call a speed nothing holds 'MAX'. Not engaged, or no set speed:
    the stock disabled char, '–'."""
    if not self.is_cruise_set or not self._engaged:
      return CRUISE_DISABLED_CHAR
    return str(round(self.set_speed if ui_state.is_metric else self.set_speed * KM_TO_MILE))

  def _draw_card(self, rect: rl.Rectangle, card: MaxCard) -> None:  # FORK(HUD)
    """The card, drawn by the style's drawer (the 'now' flash's rhythm, the other thing the style decides, is picked in
    _draw_approach). It keeps left of the stop timer when the timer is up, and CARD_RIGHT_MARGIN short of the road's
    right edge otherwise (MaxCard.max_right: a card too wide squeezes its words)."""
    right = min(rect.x + rect.width - CARD_RIGHT_MARGIN, hud_alerts.cluster_edge.timer_left - hud_alerts.BANNER_GAP)
    card = dataclasses.replace(card, max_right=right)
    if hud_settings.get().limit_style == STYLE_STOCK_PLUS:
      hud_draw.stock_plus_card(card, rect.x, rect.y)
    else:
      hud_draw.split_card(card, rect.x, rect.y)

  # FORK(HUD): the lane-block tile (hud_lane_block.py)
  def _lane_block_now(self) -> LaneBlockTile:
    """This frame's tile, decided once a frame - by whoever asks first: the face, the MAX number's gate or the render."""
    alert, out = self._frame_alert() if self.alert_renderer is not None else (None, False)
    self._lane_block.update(ui_state.sm, hud_settings.get().lane_block, alert, rl.get_time(), ui_state.started_frame, out)
    return self._lane_block

  def _lane_block_left(self) -> bool:  # FORK(HUD)
    """A left tile has the top left: the face, the stock MAX number, the coming limit's card and the confirm's card -
    either style, its turn into MAX (_done) included - make way."""
    return self._lane_block_now().covers_left()

  def set_can_draw_top_icons(self, can_draw_top_icons: bool):  # FORK(HUD)
    super().set_can_draw_top_icons(can_draw_top_icons and not self._lane_block_left())

  def _frame_alert(self) -> tuple:  # FORK(HUD)
    """This frame's (alert, animating_out), looked up once a frame (hud_alerts.FrameAlert)."""
    return hud_alerts.frame_alert.get(self.alert_renderer)

  def _box_top_icon(self) -> bool:  # FORK(HUD)
    """By THIS frame's alert (the face asks before the alert renderer publishes the box): up from the confirm's first
    frame; while the box fades out under the stock MAX number ('set speed changed'); not once anything else is up, nor
    while the confirm itself fades out - the face comes back on the frame it would with no confirm before - unless the
    press has set the speed it asked for: then the card turns into MAX on this frame (_done), and the face stays away."""
    ar = self.alert_renderer
    if ar is None:
      return self._pending_alpha.x > 1e-2 and not self._stale()
    alert, animating_out = self._frame_alert()
    if alert is None or animating_out:
      return (alert is None or event_name(alert) == CONFIRM_EVENT) and self._done_due()
    if hud_alerts.box_alert(alert, hud_settings.get()):
      return True
    return hud_alerts.frees_top_icons(alert) and self._pending_alpha.x > 1e-2 and not self._stale()

  def _stale(self) -> bool:  # FORK(HUD)
    return rl.get_time() - self._pending_t > PENDING_STALE_S

  def _draw_pending_max(self, rect: rl.Rectangle) -> None:  # FORK(HUD)
    """While the compact confirm is up, the set speed it would set, in the stock MAX number's place, as the style's card
    with 'press - to set 100' (hud_draw). It follows the alert's own fade, fades out (0.1 s, as the MAX number) when the
    alert turns into one that frees the MAX number ('set speed changed': the stock MAX takes its place), and goes at once
    when anything else takes the top left - a full-screen alert, a banner - so it is never drawn over another alert. Its
    outline flashes twice from the frame it is half visible, then marches (split card) or breathes (stock-plus):
    hud_model.box_outline / box_breath. A confirm coming while the coming limit's card is up takes that card over - it
    turns into the confirm's (CARD_GROW_S) instead of fading out and in. Once the press has set the speed, the card turns
    into the stock MAX number (_done)."""
    item = hud_alerts.pending_max.take()
    now = rl.get_time()
    out = bool(self._frame_alert()[1]) if self.alert_renderer is not None else False
    if (self._done_t0 is not None or self._done_starts(item, out)) and self._done(rect, now, item, out):
      self._pending_t = now
      self._pending_had, self._pending_out = item is not None, out
      return
    if item is not None:
      # a new prompt - a box taken after a frame with none, or a confirm up again while the last one was still fading
      # out (the box never left the screen) - or the target or the key changing under one: its own two flashes, from
      # when it is half visible
      p = self._pending
      new_prompt = not self._pending_had or (self._pending_out and not out)
      if new_prompt or p is None or (p.value, p.lower) != (item.value, item.lower):
        self._pending_t0 = None
        self._grow_t0 = None
        if not self._pending_had and now - self._approach_seen <= PENDING_STALE_S:
          # the coming limit's card was up a moment ago: the confirm takes it over, and flashes once it has turned
          self._grow_t0, self._grow_alpha = now, self._approach_seen_alpha
          self._pending_t0 = now + CARD_GROW_S
      self._pending = item
      self._pending_alpha.x = item.alpha
    elif self._can_draw_top_icons and not self._stale():
      self._pending_alpha.update(0.0)
    else:
      # another alert has the top left, or the road view was not drawn for a while (swiped away mid-prompt): no tail
      self._pending_alpha.x = 0.0
    self._pending_t = now
    self._pending_had, self._pending_out = item is not None, out
    # the stock MAX number fading out as the box comes, or in as it goes: one or the other, never both at full
    alpha = self._pending_alpha.x * (1.0 - self._set_speed_alpha_filter.x)
    if item is not None and self._grow_t0 is not None and now - self._grow_t0 < CARD_GROW_S:
      alpha = max(alpha, self._grow_alpha * (1.0 - self._set_speed_alpha_filter.x))   # no dip at the hand-over
    if self._lane_block_left():  # FORK(HUD): the lane-block tile has the top left; the box comes back after it
      alpha = 0.0
    self._card_alpha = alpha if self._pending is not None and alpha >= 1e-2 else 0.0
    if self._pending is None or alpha < 1e-2:
      if self._pending_alpha.x < 1e-2:
        self._pending = None
        self._pending_t0 = self._grow_t0 = None
      return
    # the outline's clock starts when the box is first half visible, not when it is first published: a cross-fade with
    # the stock MAX number (route 10f t 183.75, the MAX '105' from engaging 2 s earlier still fading out) would swallow
    # the first flash
    if self._pending_t0 is None and alpha >= 0.5:
      self._pending_t0 = now
    # (before that, the plain outline: the first flash is a whole one, at full brightness)
    t = now - self._pending_t0 if self._pending_t0 is not None else -1.0
    lit, phase = box_outline(t) if t >= 0 else (False, 0.0)
    p = self._pending
    self._card_current = self._current_text()
    grow = ease_out((now - self._grow_t0) / CARD_GROW_S) if self._grow_t0 is not None else 1.0
    self._draw_card(rect, MaxCard(CARD_PENDING, p.value, p.lower, self._card_current, text=p.text, note=p.note,
                                  alpha=alpha, color=(p.color.r, p.color.g, p.color.b), lit=lit, phase=phase,
                                  breath=box_breath(t) if t >= 0 else 1.0, key_alpha=p.key_alpha, grow=grow))

  def _done_due(self) -> bool:  # FORK(HUD)
    """The press has set the speed the confirm asked for (the set speed, as MAX shows it, is the card's target) while
    the card is up."""
    p = self._pending
    return (p is not None and self._pending_alpha.x >= 0.5 and not self._stale() and self.is_cruise_set and
            self._current_text() == str(p.value))

  def _done_starts(self, item, out: bool) -> bool:  # FORK(HUD)
    """The card turns into MAX (_done): the confirm done, the prompt gone or fading out. Anything else (timed out,
    another alert) ends the card as before: it fades, or goes."""
    if item is not None and not out:
      return False
    confirm_fading = out and event_name(self._frame_alert()[0]) == CONFIRM_EVENT
    if self._lane_block_left():  # FORK(HUD): the lane-block tile has the top left
      return False
    return (confirm_fading or self._can_draw_top_icons) and self._done_due()

  def _done(self, rect: rl.Rectangle, now: float, item, out: bool) -> bool:  # FORK(HUD)
    """The confirm done: over CARD_DONE_S the card becomes the stock MAX number - the split card's target slides into
    the stock MAX digits' place and grows to their size, the stock-plus card dissolves round digits that are already
    there - and then the stock MAX number takes over, at full strength, for the rest of its 2.5 s (not while the prompt
    still fades out: until then the end is held). The digits never dip, and the face never shows in between, as it
    did while the prompt faded. It starts from the strength the card was last drawn at (the prompt may already be
    fading, the stock MAX may be showing) and eases to full with the turn, so its first frame is no jump. Cut on the
    frame - no tail - by another alert taking the top left, the road view not drawn for a while, or a new prompt.
    False: not drawn (the frame goes on as without it)."""
    confirm_fading = out and event_name(self._frame_alert()[0]) == CONFIRM_EVENT
    if self._done_t0 is None:
      self._done_t0, self._done_from = now, self._card_current
      self._done_alpha0 = min(1.0, max(self._card_alpha, self._set_speed_alpha_filter.x))
    elif (not (self._can_draw_top_icons or confirm_fading) or self._stale() or (item is not None and not out) or
          self._lane_block_left()):   # (the lane-block tile taking the top left cuts it as another alert does)
      self._done_t0 = None
      self._pending, self._pending_t0, self._grow_t0 = None, None, None
      self._pending_alpha.x = 0.0
      return False
    u = (now - self._done_t0) / CARD_DONE_S
    p = self._pending
    done = ease_in_out(u)
    self._draw_card(rect, MaxCard(CARD_DONE, p.value, p.lower, self._done_from, text=p.text, note=p.note,
                                  alpha=self._done_alpha0 + (1.0 - self._done_alpha0) * done,
                                  color=(p.color.r, p.color.g, p.color.b), done=done))
    if u >= 1.0 - 1e-6 and self._can_draw_top_icons:
      # landed: the stock MAX number, as it is drawn for its 2.5 s after the change, from the next frame (held landed
      # while the prompt still fades out: the stock MAX may not show before it has gone)
      self._done_t0 = None
      self._pending, self._pending_t0, self._grow_t0 = None, None, None
      self._pending_alpha.x = 0.0
      self._set_speed_alpha_filter.x = 1.0
    return True

  def _plan_assist(self):  # FORK(SPEED-LIMIT): sla-flash
    """longitudinalPlanSP's Speed Limit Assist while the plan is this drive's and still arriving, else None."""
    if not fresh(ui_state.sm, 'longitudinalPlanSP', ui_state.started_frame):
      return None
    return ui_state.sm['longitudinalPlanSP'].speedLimit.assist

  def _now_prompt(self, limit: int) -> str:  # FORK(SPEED-LIMIT): push9, sla-flash
    """'press - to set 100' in place of 'now' exactly when card would take the press as the arm for `limit`: the plan's
    own offer (assist.nowKey / nowLimit / nowTarget, with every Speed Limit Assist guard applied - speed_limit/
    now_confirm.py), openpilot engaged with a set speed (with cruise off, '-' engages instead - routes 137 88.47 and
    479.71). push9 guessed it here from the settings; the guess also said it in the prompt, on the first planner frame
    after engaging and with the mode changing under it, where the press is now an ordinary step. '' otherwise, and the
    card says 'now' as before."""
    a = self._plan_assist()
    if a is None or not (self._engaged and self.is_cruise_set) or int(a.nowKey) == 0 or int(a.nowLimit) != limit:
      return ""
    return press_words(int(a.nowKey) < 0, int(a.nowTarget))

  def _armed(self) -> tuple[int, int] | None:  # FORK(SPEED-LIMIT): sla-flash
    """(limit, target) of the press at 'now' the planner holds (assist.armedLimit / armedTarget, display units) while
    openpilot is engaged with a set speed and the plan still arriving; None otherwise."""
    a = self._plan_assist()
    if a is None or not (self._engaged and self.is_cruise_set) or int(a.armedLimit) <= 0:
      return None
    return int(a.armedLimit), int(a.armedTarget)

  # FORK(HUD): the coming limit (round 5)
  def _approach_now(self):
    return self._approach.update(ui_state.sm, hud_settings.get(), started_frame=ui_state.started_frame,
                                 now=rl.get_time(), is_metric=ui_state.is_metric,
                                 speed_limit_mode_on=ui_state.speed_limit_mode != 0)

  def _approach_blocker(self) -> int:  # FORK(HUD)
    """What else has the top left this frame: APPROACH_FREE nothing; APPROACH_MAX the stock MAX number, or an alert
    that draws nothing and lets it show ('set speed changed', 'auto adjusting'): the coming limit hands over to it in
    the same place, the two cross-fading as the box and MAX do; APPROACH_CUT any other alert (a banner, a full-screen
    one - including one fading out) or the confirm's box: the coming limit goes on the frame, never drawn over it."""
    alert = self._frame_alert()[0]
    if alert is not None and not hud_alerts.frees_top_icons(alert):
      return APPROACH_CUT
    if self._lane_block_left():  # FORK(HUD): the lane-block tile has the top left
      return APPROACH_CUT
    # nothing up, or an alert that draws nothing: what the MAX place has - the box (fading), then the stock MAX number
    # for as long as it shows (SET_SPEED_PERSISTENCE, 2.5 s, not the ~5 s the 'set speed changed' alert stays up: the
    # coming limit comes as soon as MAX has gone - route 10f t 187.8-190.2, the 70 kept back with the place empty)
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
    # (sla-flash: an armed card counts like a coming limit's, ApproachState or not)
    return fading or (blocker == APPROACH_FREE and (self._approach_now() is not None or self._armed() is not None))

  def _draw_approach(self, rect: rl.Rectangle) -> None:  # FORK(HUD)
    """The coming limit in the MAX number's place, as the style's card: fades in (0.1 s, as the MAX number; the split
    card also opens out of its MAX half, CARD_ENTER_S); fades out when the look-ahead and its hold end, or under the
    stock MAX number taking the place; goes at once when anything else takes it. 'now' glows (split card: the red bar,
    twice) or lights its outline once (stock-plus).

    sla-flash: while the planner holds a press made at 'now' (_armed), the card is drawn from THAT - the coming limit,
    held 'now', 'sets 80 at sign' and no key, its number flashing (arm_flash), everything else solid - over whatever
    ApproachState shows: ApproachState drops the coming limit at most APPROACH_HOLD_S after the look-ahead ended, the
    arm lives up to ARM_KEEP_S longer, and the card must stay until the limit is in force. When the arm drops the card
    fades (or cross-fades into the stock MAX number the take brings, as a coming limit does) with the armed words and
    the number solid; if 'now' is still open (a press the other way) it is the 'now' card again."""
    ap = self._approach_now()
    armed = self._armed()
    now = rl.get_time()
    blocker = self._approach_blocker()
    if blocker == APPROACH_CUT or now - self._approach_t > PENDING_STALE_S:
      self._approach_alpha.x = 0.0   # (or the road view was not drawn for a while: no tail)
    self._approach_alpha.update(1.0 if (ap is not None or armed is not None) and blocker == APPROACH_FREE else 0.0)
    self._approach_t = now
    if armed is not None:
      if self._armed_t0 is None:
        self._armed_t0 = now
      self._armed_shown = armed
      self._approach_shown = Approach(limit=armed[0], lower=self._armed_lower(*armed), dist=0.0, frac=0.0, held=True)
    else:
      self._armed_t0 = None
      if ap is not None:
        self._armed_shown = None
        self._approach_shown = ap
    shown = self._approach_shown
    # the stock MAX number fading in as it goes: one or the other, never both at full
    alpha = self._approach_alpha.x * (1.0 - self._set_speed_alpha_filter.x)
    if shown is None or alpha < 1e-2:
      self._approach_t0 = self._now_t0 = None
      self._armed_shown = None
      return
    from openpilot.selfdrive.ui.sunnypilot.onroad.speed_limit import SpeedLimitRenderer
    held = self._armed_shown is not None
    text = approach_text(SpeedLimitRenderer._format_dist(shown.dist), shown.held)
    is_now = held or text == "now"
    frac = 0.0 if is_now else shown.frac   # 'now': the bar has drained
    digits = 1.0
    if held:
      text = armed_text(self._armed_shown[1])   # sla-flash: the press held
      digits = arm_flash(now - self._armed_t0) if self._armed_t0 is not None else 1.0
    elif is_now:
      text = self._now_prompt(shown.limit) or text   # push9: the press 'now' takes, where it takes one
    if self._approach_t0 is None:
      self._approach_t0 = now
    self._now_t0 = (self._now_t0 if self._now_t0 is not None else now) if is_now else None
    if alpha >= 0.5:
      self._approach_seen, self._approach_seen_alpha = now, alpha
    lit = False
    if self._now_t0 is not None:
      t = now - self._now_t0
      lit = box_outline(t)[0] if hud_settings.get().limit_style == STYLE_SPLIT else t < NOW_HALO_S
    self._draw_card(rect, MaxCard(CARD_APPROACH, shown.limit, shown.lower, self._current_text(), text=text, frac=frac,
                                  alpha=alpha, lit=lit, enter=ease_out((now - self._approach_t0) / CARD_ENTER_S),
                                  armed=held, digits=digits))

  def _armed_lower(self, limit: int, target: int) -> bool:  # FORK(SPEED-LIMIT): sla-flash
    """The armed limit's chevron: lower than the limit in force (the resolver's map limit), or - with none - its set
    speed under the one held now."""
    conv = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    in_force = resolver_map_limit(ui_state.sm, ui_state.started_frame, conv)
    if in_force > 0:
      return limit < in_force
    held = round(self.set_speed if ui_state.is_metric else self.set_speed * KM_TO_MILE)
    return target < held
