"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the comma 4 HUD's settings - one param per piece, set from sunnylink (Visuals > HUD).

READ AT MOST ONCE A SECOND, from the render thread: nine small param reads a second, never one per frame.

EVERY KEY DEFAULTS TO ON (params_keys.h), and every key off gives exactly the stock comma 4 screen. A key that cannot be
read at all - a build whose params library predates it - reads as OFF, so a half-installed update draws the stock
screen rather than half a HUD.
"""
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

# HudNextLimit: how the next LOWER limit is drawn beside its small sign
NEXT_OFF = 0
NEXT_BAR = 1
NEXT_TEXT = 2
NEXT_BOTH = 3
NEXT_MODES = (NEXT_OFF, NEXT_BAR, NEXT_TEXT, NEXT_BOTH)

REFRESH_S = 1.0

PARAM_SPEED_CLUSTER = "HudSpeedCluster"     # speed + speed-limit sign, top right
PARAM_NEXT_LIMIT = "HudNextLimit"           # 0 off, 1 bar, 2 distance, 3 both
PARAM_SCHOOL_CUE = "HudSchoolZoneCue"       # amber lamps + SCHOOL on an active NSW school zone, grey lamps inactive
PARAM_VARIABLE_SIGN = "HudVariableLimitSign"  # a Variable (gantry) zone drawn as the electronic sign
PARAM_STOPPED_TIMER = "HudStoppedTimer"     # at a standstill the speed becomes a stopwatch and m:ss
PARAM_STOPPED_BANNER = "HudStoppedBanner"   # 'take control / resume driving manually' as a compact banner
PARAM_CONFIRM_LIMIT = "HudConfirmLimit"     # the speed-limit confirm alert shows the pending limit
PARAM_PLANNED_STOP = "HudPlannedStop"       # right strip: where the model's speed plan comes to a stop, and how far
PARAM_CURVE = "HudCurve"                    # right strip: slowing for a curve, and openpilot's target speed for it

BOOL_PARAMS = (PARAM_SPEED_CLUSTER, PARAM_SCHOOL_CUE, PARAM_VARIABLE_SIGN, PARAM_STOPPED_TIMER, PARAM_STOPPED_BANNER,
               PARAM_CONFIRM_LIMIT, PARAM_PLANNED_STOP, PARAM_CURVE)
ALL_PARAMS = BOOL_PARAMS + (PARAM_NEXT_LIMIT,)


@dataclass(frozen=True)
class HudSettings:
  speed_cluster: bool = False
  next_limit: int = NEXT_OFF
  school_cue: bool = False
  variable_sign: bool = False
  stopped_timer: bool = False
  stopped_banner: bool = False
  confirm_limit: bool = False
  planned_stop: bool = False
  curve: bool = False


ALL_OFF = HudSettings()
ALL_ON = HudSettings(True, NEXT_BOTH, True, True, True, True, True, True, True)


def read_settings(params) -> HudSettings:
  """The settings as the params say now. return_default: a key nobody has written yet is its params_keys.h default
  (on), as the manager would have written it at start."""
  def flag(key: str) -> bool:
    try:
      return bool(params.get(key, return_default=True))
    except Exception:
      return False

  try:
    raw = params.get(PARAM_NEXT_LIMIT, return_default=True)
    mode = int(raw) if raw is not None else NEXT_OFF
    if mode not in NEXT_MODES:
      mode = NEXT_BOTH  # not a value sunnylink writes: the default
  except Exception:
    mode = NEXT_OFF

  return HudSettings(
    speed_cluster=flag(PARAM_SPEED_CLUSTER),
    next_limit=mode,
    school_cue=flag(PARAM_SCHOOL_CUE),
    variable_sign=flag(PARAM_VARIABLE_SIGN),
    stopped_timer=flag(PARAM_STOPPED_TIMER),
    stopped_banner=flag(PARAM_STOPPED_BANNER),
    confirm_limit=flag(PARAM_CONFIRM_LIMIT),
    planned_stop=flag(PARAM_PLANNED_STOP),
    curve=flag(PARAM_CURVE),
  )


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


# one reader for the cluster and the alert renderer: the two read the same answer in a frame
hud_settings = HudSettingsReader()
