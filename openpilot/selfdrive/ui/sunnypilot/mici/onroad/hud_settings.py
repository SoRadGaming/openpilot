"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 HUD's settings - one param per piece, set from sunnylink (Visuals > HUD).

READ AT MOST ONCE A SECOND, from the render thread: ten small param reads a second, never one per frame.

EVERY TOGGLE DEFAULTS TO ON (params_keys.h), and every toggle off gives the stock comma 4 screen - but for one fix
behind no key: the 'changing lanes' arrow takes its side from the model (mici alert_renderer.py, 2026-10-12). A key
that cannot be read at all - a build whose params library predates it - reads as OFF, so a half-installed update draws
the stock screen rather than half a HUD.

One key is not a toggle: HudLimitStyle (2026-10-12, the owner's 'd1 and d3, allow a toggle') picks how the coming limit
and the confirm are drawn in MAX's place - STYLE_SPLIT, the split card (D3, the default), or STYLE_STOCK_PLUS (D1). It
changes the drawing only (hud_draw.split_card / stock_plus_card), never what is shown or when, so it may change while
driving. Unreadable, or a value this build does not know: the split card.

Round 5 (2026-10-09, the owner's option A) took the live speed, the speed-limit sign and its zone styling, and the next
lower limit off the screen, with their settings; the stop timer stays top right. The coming limit is now announced in
the stock MAX number's place (HudApproachingLimit, hud_model.ApproachState).

FORK(NSW-CAMERAS) push13: HudCameraAlerts - 'camera (map)' right of that place, before a fixed speed, red-light or
school-zone camera on the road ahead (hud_model.CameraState). Display only; no chime (a chime needs a selfdrived event,
which this batch may not add to cereal).
"""
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

REFRESH_S = 1.0

PARAM_STOPPED_TIMER = "HudStoppedTimer"     # at a standstill a stopwatch and m:ss, top right
PARAM_STOPPED_BANNER = "HudStoppedBanner"   # 'take control / resume driving manually' as a compact banner
PARAM_PLANNED_STOP = "HudPlannedStop"       # right strip: where the model's speed plan comes to a stop, and how far
PARAM_CURVE = "HudCurve"                    # right strip: slowing for a curve, and openpilot's target speed for it
PARAM_COMPACT_LIMIT = "HudCompactLimitPrompts"  # the speed-limit prompts small: the box in MAX's place, or nothing
PARAM_COMPACT_DISENGAGE = "HudCompactDisengage"  # 'cruise off' / 'lane centering off' as a banner
PARAM_COMPACT_TURN = "HudCompactTurn"       # 'turning left / right' as a banner
PARAM_APPROACHING_LIMIT = "HudApproachingLimit"  # the coming limit (lower or higher) in MAX's place, before it changes
# a refused lane change as a tile on the blinker side, silent until a nudge is refused (hud_lane_block.py); selfdrived
# reads it too, to drop the two block alerts and chime on the refused nudge (lane_block_alert.py)
PARAM_LANE_BLOCK = "HudLaneBlock"
PARAM_LIMIT_STYLE = "HudLimitStyle"         # INT: how the coming limit and the confirm are drawn there (STYLES)
PARAM_CAMERA = "HudCameraAlerts"            # FORK(NSW-CAMERAS): 'camera (map)' before a camera on the road ahead

STYLE_SPLIT, STYLE_STOCK_PLUS = 0, 1        # D3 'split card' (the default), D1 'stock-plus'
STYLES = (STYLE_SPLIT, STYLE_STOCK_PLUS)

# every key (all BOOL) and the HudSettings field it switches: the one list
FIELDS = {PARAM_STOPPED_TIMER: "stopped_timer", PARAM_STOPPED_BANNER: "stopped_banner",
          PARAM_PLANNED_STOP: "planned_stop", PARAM_CURVE: "curve", PARAM_COMPACT_LIMIT: "compact_limit",
          PARAM_COMPACT_DISENGAGE: "compact_disengage", PARAM_COMPACT_TURN: "compact_turn",
          PARAM_APPROACHING_LIMIT: "approaching_limit", PARAM_LANE_BLOCK: "lane_block",
          PARAM_CAMERA: "camera"}  # FORK(NSW-CAMERAS)
ALL_PARAMS = tuple(FIELDS)   # the toggles
KEYS = ALL_PARAMS + (PARAM_LIMIT_STYLE,)   # every HUD key: the toggles and the style


@dataclass(frozen=True)
class HudSettings:
  stopped_timer: bool = False
  stopped_banner: bool = False
  planned_stop: bool = False
  curve: bool = False
  compact_limit: bool = False
  compact_disengage: bool = False
  compact_turn: bool = False
  approaching_limit: bool = False
  lane_block: bool = False
  camera: bool = False  # FORK(NSW-CAMERAS)
  limit_style: int = STYLE_SPLIT


ALL_OFF = HudSettings()
ALL_ON = HudSettings(**dict.fromkeys(FIELDS.values(), True))


def read_settings(params) -> HudSettings:
  """The settings as the params say now. return_default: a key nobody has written yet is its params_keys.h default
  (on), as the manager would have written it at start."""
  def flag(key: str) -> bool:
    try:
      return bool(params.get(key, return_default=True))
    except Exception:
      return False

  def style() -> int:
    try:
      v = int(params.get(PARAM_LIMIT_STYLE, return_default=True))
    except Exception:
      return STYLE_SPLIT
    return v if v in STYLES else STYLE_SPLIT

  return HudSettings(**{field: flag(key) for key, field in FIELDS.items()}, limit_style=style())


class HudSettingsReader:
  """Re-reads the params at most every REFRESH_S seconds; get() between reads returns the last answer."""
  def __init__(self, params_fn: Callable | None = None, clock: Callable[[], float] = time.monotonic):
    self._params_fn = params_fn
    self._clock = clock
    self._next = -math.inf
    self.settings = ALL_OFF
    self.reads = 0

  def _params(self):
    if self._params_fn is not None:
      return self._params_fn()
    from openpilot.selfdrive.ui.ui_state import ui_state
    return ui_state.params

  def get(self, force: bool = False) -> HudSettings:
    now = self._clock()
    if force or now >= self._next:
      self._next = now + REFRESH_S
      self.settings = read_settings(self._params())
      self.reads += 1
    return self.settings


# one reader for the cluster, the alert renderer and the HUD renderer: all read the same answer in a frame
hud_settings = HudSettingsReader()
