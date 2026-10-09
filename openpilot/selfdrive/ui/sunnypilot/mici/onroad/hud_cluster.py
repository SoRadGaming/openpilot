"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4's stop timer - top right, left of the confidence ball, in the corner:

    [stopwatch m:ss]

After a second at a standstill (HudStoppedTimer), a stopwatch and how long the car has been stopped, ~35 px digits. It
is all that is left of the 2026-10-03 speed cluster: round 5 (2026-10-09, the owner's option A) removed the live speed,
the speed-limit sign and the next lower limit (hud_model.py); the timer stays exactly where it was drawn with the sign
off, in the corner.

WHERE IT IS DRAWN: under the alerts, and it fades out (fast) while an alert is up, and comes back only once the alert
has finished fading out, so the two never overlap - except a compact alert (hud_alerts.py), which leaves the top right
free: its banner stops short of the timer (it reads hud_alerts.cluster_edge, filled in here). The 60 px strip on the
right (the confidence ball) is outside the content rect and is never painted.

This widget also hands the alert renderer to the HUD renderer (HudRendererSP asks this frame's alert whether the
confirm's box or the coming limit is up) - it is built with both, in augmented_road_view.py.
"""
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_draw as hd
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import HudFrame, build_frame, compact_kind, received, \
  COMPACT_NONE
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_settings import HudSettings, hud_settings
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_alerts import cluster_edge, frame_alert, standstill_banner
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget

# layout, device pixels from the content rect's top-right corner (the content rect is 476 wide on the comma 4)
MARGIN = 10             # the timer to the content edge
ROW_CY = 36
GAP = 10                # the stopwatch glyph to the digits
TIMER_SIZE = 50         # nominal; ~35 px digits
STOPWATCH_SIZE = 28
TIMER_WIDEST = "59:59"  # the stop time up to this is drawn at full size; "1:00:00" and up shrink to its width, so the
                        # stopwatch never reaches into the compact banner


def timer_size(txt: str) -> float:
  """The stop time's nominal size: TIMER_SIZE up to the width of TIMER_WIDEST, smaller beyond it."""
  tw, max_w = hd.ink(txt, TIMER_SIZE)[0], hd.ink(TIMER_WIDEST, TIMER_SIZE)[0]
  return TIMER_SIZE if tw <= max_w else TIMER_SIZE * max_w / tw


class HudCluster(Widget):
  def __init__(self, hud_renderer=None, alert_renderer=None):
    super().__init__()
    self._alert_renderer = alert_renderer
    self._alpha = FirstOrderFilter(0.0, 0.05, 1 / gui_app.target_fps)
    self._stop_start: float | None = None
    self._alert = None
    self._now = 0.0
    self.settings: HudSettings = hud_settings.settings
    self.frame = HudFrame()
    # the sunnypilot HUD renderer asks this frame's alert whether the confirm's box (or the coming limit) is up, and
    # tells the standstill banner every frame what the alert renderer will draw (hud_renderer.py)
    if hud_renderer is not None and hasattr(hud_renderer, "alert_renderer"):
      hud_renderer.alert_renderer = alert_renderer

  def _alert_covers(self) -> bool:
    """An alert is drawn this frame - including the previous one while it fades out, so the timer comes back only
    once it has gone - and it is not the compact banner."""
    alert = self._alert
    return alert is not None and not standstill_banner.compact(alert, self.settings, ui_state.sm, ui_state.started_frame,
                                                               self._now) and compact_kind(alert, self.settings) == COMPACT_NONE

  def _update_state(self):
    self.settings = hud_settings.get()
    sm = ui_state.sm
    now = self._now = rl.get_time()
    # what the alert renderer will draw this frame (it renders after the cluster)
    self._alert = frame_alert.get(self._alert_renderer)[0]
    if received(sm, 'carState', ui_state.started_frame) and sm['carState'].standstill:
      if self._stop_start is None:
        self._stop_start = now
    else:
      self._stop_start = None
    stopped_s = (now - self._stop_start) if self._stop_start is not None else None
    self.frame = build_frame(sm, self.settings, started_frame=ui_state.started_frame, stopped_s=stopped_s)

  def _render(self, rect: rl.Rectangle):
    cluster_edge.reset()
    f = self.frame
    # the fade runs every frame, the timer shown or not: it appears at once after its second (as it did beside the
    # speed), and an alert that comes or goes during a stop still hides it until it has faded out
    alpha = self._alpha.update(0.0 if self._alert_covers() else 1.0)
    if f.timer_s is None or alpha < 0.01:
      return
    right, cy = rect.x + rect.width - MARGIN, rect.y + ROW_CY
    txt = hd.fmt_mmss(f.timer_s)
    w, _ = hd.big_digits(right, cy, txt, timer_size(txt), alpha=alpha)
    sw = hd.stopwatch_glyph(right - w - GAP, cy, size=STOPWATCH_SIZE, alpha=alpha)
    cluster_edge.timer_left = right - w - GAP - sw
