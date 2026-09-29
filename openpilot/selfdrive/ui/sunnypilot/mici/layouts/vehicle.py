"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The small-screen counterpart of Settings > Vehicle. The big UI has Cruise and
Vehicle panels; this UI has neither, so without this page there is no way to
reach the Honda dynamic longitudinal tuner on a mici device at all.

Same params as the big UI and as the sunnylink schema -- one setting, three
front ends.

FORK(LKAS-GATEWAY): also the fast-wheel takeover in mads.py (MadsEmergencySteerDisable
and its threshold, MadsEmergencySteerRate). The mici has no MADS page; this is the
fork's own page, so it costs no upstream diff. The same two params are in sunnylink
under Steering > MADS Settings.
"""
import time

import pyray as rl

from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigMultiToggle, BigParamControl
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.honda import (
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

# The fast-wheel takeover (mads.py). Spelled out here rather than imported, so the settings
# page never pulls in selfdrived's MADS; test_mads_fast_wheel_settings.py keeps the copy honest.
FAST_WHEEL_PARAM = "MadsEmergencySteerDisable"
FAST_WHEEL_RATE_PARAM = "MadsEmergencySteerRate"
FAST_WHEEL_RATES = (150, 200, 250, 300)   # deg/s, mads.EMERGENCY_STEER_RATES
FAST_WHEEL_DEFAULT = 200                  # deg/s, mads.EMERGENCY_STEER_RATE


def fast_wheel_rate_label(rate: int) -> str:
  return f"{rate}°/s"  # the degree sign is in EXTRA_FONT_CHARS


def read_fast_wheel_rate() -> int:
  """The threshold as mads.py reads it: anything outside FAST_WHEEL_RATES is the default."""
  try:
    rate = float(ui_state.params.get(FAST_WHEEL_RATE_PARAM, return_default=True))
  except (TypeError, ValueError):
    return FAST_WHEEL_DEFAULT
  return int(rate) if rate in FAST_WHEEL_RATES else FAST_WHEEL_DEFAULT


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
  """Two header/value pairs, laid out like SunnylinkInfo and CurrentModelInfo.

  wrap_text=False ON EVERY LABEL, because these are hand-positioned at fixed
  offsets in a 180 px card and a wrapped header silently overprints the value
  under it. "learned pedal gain" measures ~410 px against a 340 px max_width,
  so it wraps - and the four labels then need 236 px of a 180 px card. The card
  this was copied from (DeviceInfoLayoutMici) passes the flag on all four; the
  copy dropped it.
  """

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.gain_header = UnifiedLabel(tr("learned pedal gain"), 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.gain_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                  font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self.trim_header = UnifiedLabel(tr("brake / aero"), 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.trim_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color, font_weight=FontWeight.ROMAN, wrap_text=False)

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


class FastWheelRateToggle(BigMultiToggle):
  """Steps through FAST_WHEEL_RATES and writes the rate itself. BigMultiParamToggle would
  store the option's INDEX, which mads.py would read as 0..3 deg/s and replace with 200."""

  def __init__(self):
    super().__init__(tr("swerve at"), [fast_wheel_rate_label(r) for r in FAST_WHEEL_RATES],
                     select_callback=self._on_select)
    self.refresh()

  def _on_select(self, label: str) -> None:
    rate = FAST_WHEEL_RATES[self._options.index(label)]
    ui_state.params.put(FAST_WHEEL_RATE_PARAM, rate, block=True)

  def refresh(self) -> None:
    self.set_value(fast_wheel_rate_label(read_fast_wheel_rate()))


class VehicleLayoutMici(NavScroller):
  # No back_callback: NavWidget pops itself on swipe-down, and a pop_widget
  # callback on top of that popped Settings too (upstream 099143ad9).
  def __init__(self):
    super().__init__()

    self._learned_info = HondaLearnedInfo()

    self._learning_toggle = BigParamControl(tr("dynamic longitudinal learning"), TUNING_PARAM)

    self._reset_btn = BigButton(tr("reset learned values"))
    self._reset_btn.set_click_callback(self._on_reset_clicked)
    # the tuner rewrites the learned values every 60 s while driving, so a reset
    # onroad would just be undone
    self._reset_btn.set_enabled(ui_state.is_offroad)

    # the fast-wheel takeover: on/off, then the rate. mads.py re-reads both every 0.1 s, so
    # neither is gated on offroad. The title names the action, so ON reads as "steering turns
    # off on a swerve" and cannot be read as "the fast-wheel feature is off"
    self._fast_wheel_toggle = BigParamControl(tr("off on swerve"), FAST_WHEEL_PARAM,
                                              toggle_callback=self._on_fast_wheel_toggled)
    self._fast_wheel_rate = FastWheelRateToggle()

    self._scroller.add_widgets([self._learned_info, self._learning_toggle, self._reset_btn,
                                self._fast_wheel_toggle, self._fast_wheel_rate])

    self._refreshed = 0.0

  def _on_reset_clicked(self) -> None:
    icon = gui_app.texture("../../sunnypilot/selfdrive/assets/offroad/icon_vehicle.png", 110, 110)
    gui_app.push_widget(BigConfirmationDialog(tr("slide to reset what this car has learned"), icon,
                                              confirm_callback=self._on_reset_confirmed, red=True))

  def _on_fast_wheel_toggled(self, checked: bool) -> None:
    # the rate means nothing with the takeover off
    self._fast_wheel_rate.set_enabled(checked)

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
    self._learning_toggle.refresh()
    self._fast_wheel_toggle.refresh()
    self._fast_wheel_rate.refresh()
    self._fast_wheel_rate.set_enabled(ui_state.params.get_bool(FAST_WHEEL_PARAM))

  def _update_state(self):
    super()._update_state()
    if time.monotonic() - self._refreshed > REFRESH_S:
      self._refresh_toggles()
