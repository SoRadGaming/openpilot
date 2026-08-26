"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The small-screen counterpart of Settings > Vehicle. The big UI has Cruise and
Vehicle panels; this UI has neither, so without this page there is no way to
reach the Honda dynamic longitudinal tuner on a mici device at all.

Same params as the big UI and as the sunnylink schema -- one setting, three
front ends.
"""
import time
from collections.abc import Callable

import pyray as rl

from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigParamControl
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.honda import (
  PCM_BLEND_PARAM,
  PEDAL_GAIN_BP,
  TUNING_PARAM,
  learned_pedal_gains,
  learned_value,
  reset_learned_values,
)
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

# params are files: read the learned state on a tick, not every frame
REFRESH_S = 1.0


_brand_cache: list = ["", 0.0]


def car_brand() -> str:
  """Same resolution the big UI's VehicleLayout uses: the selected platform
  first, the fingerprint second.

  Cached on a tick. The settings row asks for this every frame to decide
  whether to draw itself, and CarPlatformBundle is a JSON param -- a file read
  and a parse -- so reading it 60 times a second to answer a question that
  changes once per fingerprint would be silly.
  """
  now = time.monotonic()
  if now - _brand_cache[1] > REFRESH_S:
    _brand_cache[1] = now
    brand = ""
    if bundle := ui_state.params.get("CarPlatformBundle"):
      brand = bundle.get("brand", "")
    elif ui_state.CP is not None and ui_state.CP.carFingerprint != "MOCK":
      brand = ui_state.CP.brand
    _brand_cache[0] = brand
  return _brand_cache[0]


class HondaLearnedInfo(Widget):
  """Two header/value pairs, laid out like SunnylinkInfo and CurrentModelInfo."""

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.gain_header = UnifiedLabel(tr("learned pedal gain"), 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY)
    self.gain_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                  font_weight=FontWeight.ROMAN, scroll=True)

    self.trim_header = UnifiedLabel(tr("brake / aero"), 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY)
    self.trim_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color, font_weight=FontWeight.ROMAN)

    self._updated = 0.0
    self.refresh()

  def refresh(self) -> None:
    self._updated = time.monotonic()
    speed_factor = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    unit = tr("km/h") if ui_state.is_metric else tr("mph")
    gains = " ".join(f"{gain:.2f}" for gain in learned_pedal_gains())
    bands = " ".join(f"{round(bp * speed_factor):d}" for bp in PEDAL_GAIN_BP)
    self.gain_text.set_text(f"{gains}  ({bands} {unit})")
    self.trim_text.set_text(f"{learned_value('HondaDynBrakeGain'):+.2f}   " +
                            f"x{learned_value('HondaDynWindFactor'):.2f}")

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()

  def _render(self, _):
    self.gain_header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.gain_header.render()

    self.gain_text.set_position(self._rect.x + 20, self._rect.y + 68 - 25)
    self.gain_text.render()

    self.trim_header.set_position(self._rect.x + 20, self._rect.y + 114 - 30)
    self.trim_header.render()

    self.trim_text.set_position(self._rect.x + 20, self._rect.y + 161 - 25)
    self.trim_text.render()


class VehicleLayoutMici(NavScroller):
  def __init__(self, back_callback: Callable):
    super().__init__()
    self.set_back_callback(back_callback)

    self._learned_info = HondaLearnedInfo()

    self._learning_toggle = BigParamControl(tr("dynamic longitudinal learning"), TUNING_PARAM,
                                            toggle_callback=self._on_learning_toggled)
    self._pcm_blend_toggle = BigParamControl(tr("blend pcm gas above 30 km/h"), PCM_BLEND_PARAM)
    # the interlock has to hold on both edges: the child cannot be armed while
    # the parent is off, and turning the parent off clears it below
    self._learning_on = ui_state.params.get_bool(TUNING_PARAM)
    self._pcm_blend_toggle.set_enabled(lambda: self._learning_on)

    self._reset_btn = BigButton(tr("reset learned values"))
    self._reset_btn.set_click_callback(self._on_reset_clicked)
    # the tuner rewrites the learned values every 60 s while driving, so a reset
    # onroad would just be undone
    self._reset_btn.set_enabled(ui_state.is_offroad)

    self._scroller.add_widgets([self._learned_info, self._learning_toggle, self._pcm_blend_toggle, self._reset_btn])

    self._refreshed = 0.0

  def _on_learning_toggled(self, checked: bool) -> None:
    # BigParamControl writes its own param after this returns, so track the new
    # state here rather than reading it back; only the child needs clearing
    self._learning_on = checked
    if not checked:
      ui_state.params.put_bool(PCM_BLEND_PARAM, False, block=True)
      self._pcm_blend_toggle.set_checked(False)

  def _on_reset_clicked(self) -> None:
    icon = gui_app.texture("../../sunnypilot/selfdrive/assets/offroad/icon_vehicle.png", 110, 110)
    gui_app.push_widget(BigConfirmationDialog(tr("slide to reset what this car has learned"), icon,
                                              confirm_callback=self._on_reset_confirmed, red=True))

  def _on_reset_confirmed(self) -> None:
    reset_learned_values()  # re-checks offroad: the dialog can sit open across an ignition
    self._learned_info.refresh()

  def show_event(self):
    super().show_event()
    self._refresh_toggles()
    self._learned_info.refresh()

  def _refresh_toggles(self) -> None:
    # the same two params are also set from the big UI's panels and from the
    # sunnylink app, and each toggle only reads its param when it is built
    self._refreshed = time.monotonic()
    self._learning_on = ui_state.params.get_bool(TUNING_PARAM)
    self._learning_toggle.refresh()
    self._pcm_blend_toggle.refresh()

  def _update_state(self):
    super()._update_state()
    if time.monotonic() - self._refreshed > REFRESH_S:
      self._refresh_toggles()
