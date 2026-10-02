"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4's speed cluster - top right, left of the confidence ball:

    [speed | stopwatch m:ss] [speed-limit sign]
          [distance / bar] [next lower limit]

The design the owner approved on 2026-10-03 (Layout B "speed cluster", v2 sizes): a 60 px Australian roundel, ~35 px
speed digits from carState.vEgoCluster (the Accord's own speedometer reading, as the stock HUD uses), and a 36 px sign
for the next LOWER limit within 15 s or 500 m. At a standstill the speed becomes a stopwatch and the time stopped.
A NSW school zone keeps the round sign and adds amber lamps (and SCHOOL) - hud_draw.school_cue; a Variable zone is drawn
as the electronic sign.

WHAT IS SHOWN, from which message, and what a missing message does: hud_model.py decides; this file only draws.

WHERE IT IS DRAWN: under the alerts, and the whole cluster fades out (fast) while an alert is up - except the compact
standstill banner (hud_alerts.py), which leaves the top right free. The 60 px strip on the right (the confidence ball)
is outside the content rect and is never painted. While the stock MAX number shows (2.5 s after a set-speed change) the
speed digits hide, so two big numbers are never on screen together.

Every piece has its own param (hud_settings.py); all of them off draws nothing at all.
"""
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import HudFrame, build_frame, received, SCHOOL_NONE, SCHOOL_ACTIVE
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings, hud_settings, NEXT_BAR, NEXT_TEXT, NEXT_BOTH
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget

# layout, device pixels from the content rect's top-right corner (the content rect is 476 wide on the comma 4)
SIGN_D = 60
SIGN_MARGIN = 10        # sign to the content edge
ROW_CY = 36             # the sign spans 6..66
GAP = 10                # speed digits to the sign
SPEED_SIZE = 50         # nominal; ~35 px digits
ROW2_CY = 77            # the next-limit sign spans 59..95: at or above the horizon, clear of the overtaking lane
NEXT_D = 36
STOPWATCH_SIZE = 28


def _format_dist(d: float) -> str:
  # sunnypilot's own rounding for "AHEAD" (metric: Near / 300 m / 1.2 km; imperial in ft / mi)
  from openpilot.selfdrive.ui.sunnypilot.onroad.speed_limit import SpeedLimitRenderer
  return SpeedLimitRenderer._format_dist(d)


class HudCluster(Widget):
  def __init__(self, hud_renderer=None, alert_renderer=None):
    super().__init__()
    self._hud_renderer = hud_renderer
    self._alert_renderer = alert_renderer
    self._alpha = FirstOrderFilter(0.0, 0.05, 1 / gui_app.target_fps)
    self._stop_start: float | None = None
    self._v_ego_cluster_seen = False
    self.settings: HudSettings = hud_settings.settings
    self.frame = HudFrame()

  def _max_visible(self) -> bool:
    return bool(self._hud_renderer is not None and self._hud_renderer.drawing_top_icons())

  def _alert_covers(self) -> bool:
    if self._alert_renderer is None:
      return False
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_alerts import standstill_banner
    alert, gone = self._alert_renderer.will_render()
    return alert is not None and not gone and not standstill_banner.compact(alert, self.settings, ui_state.sm)

  def _update_state(self):
    self.settings = hud_settings.get()
    sm = ui_state.sm
    now = rl.get_time()
    if received(sm, 'carState', ui_state.started_frame):
      cs = sm['carState']
      self._v_ego_cluster_seen = self._v_ego_cluster_seen or cs.vEgoCluster != 0.0
      if cs.standstill:
        if self._stop_start is None:
          self._stop_start = now
      else:
        self._stop_start = None
    else:
      self._stop_start = None
    stopped_s = (now - self._stop_start) if self._stop_start is not None else None
    self.frame = build_frame(sm, self.settings, started_frame=ui_state.started_frame, is_metric=ui_state.is_metric,
                             speed_limit_mode_on=ui_state.speed_limit_mode != 0, stopped_s=stopped_s,
                             v_ego_cluster_seen=self._v_ego_cluster_seen, max_visible=self._max_visible())

  def _render(self, rect: rl.Rectangle):
    f = self.frame
    if not f.visible:
      return
    alpha = self._alpha.update(0.0 if self._alert_covers() else 1.0)
    if alpha < 0.01:
      return

    sign_cx = rect.x + rect.width - SIGN_MARGIN - SIGN_D / 2
    cy = rect.y + ROW_CY
    # the digits keep their place while the limit comes and goes; with no sign at all they take the sign's place
    speed_r = sign_cx - SIGN_D / 2 - GAP if f.sign_slot else rect.x + rect.width - SIGN_MARGIN

    if f.next_limit:
      hd.soft_disc(speed_r - 40, rect.y + ROW2_CY, 52, 0.35 * alpha)

    if f.limit:
      hd.sign(sign_cx, cy, SIGN_D, f.limit, alpha=alpha, electronic=f.electronic, held=f.limit_held)
      if f.school != SCHOOL_NONE:
        hd.school_cue(sign_cx, cy, SIGN_D, f.school == SCHOOL_ACTIVE, rl.get_time(), alpha=alpha)

    if f.timer_s is not None:
      w, _ = hd.big_digits(speed_r, cy, hd.fmt_mmss(f.timer_s), SPEED_SIZE, alpha=alpha)
      hd.stopwatch_glyph(speed_r - w - GAP, cy, size=STOPWATCH_SIZE, alpha=alpha)
    elif f.speed is not None:
      hd.big_digits(speed_r, cy, str(f.speed), SPEED_SIZE, alpha=alpha)

    if f.next_limit:
      hd.next_row(speed_r, rect.y + ROW2_CY, f.next_limit, f.next_frac, _format_dist(f.next_dist),
                  bar=f.next_mode in (NEXT_BAR, NEXT_BOTH), text=f.next_mode in (NEXT_TEXT, NEXT_BOTH), d=NEXT_D,
                  alpha=alpha)
