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

import pyray as rl

from openpilot.common.constants import CV
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigParamControl
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

# FORK(BRAKE-LAMP-TEST): stop-lamp bit test on HONDA_ACCORD_9G_AU. The car reads the param every
# 0.2 s and applies entry N only once openpilot has held the car at a standstill for 1 s with no
# pedal pressed, at one stop per setting; manager clears the param at the end of every drive.
# Hardcoded rather than imported, like the tuner's names above, so a broken opendbc can never
# take this page off the screen; selfdrive/ui/tests/test_brake_lamp_test_settings.py keeps the
# copy honest against CANDIDATES in opendbc/sunnypilot/car/honda/brake_lamp_test.py.
# ASCII only, <= 14 chars.
LAMP_TEST_PARAM = "HondaBrakeLampTest"
LAMP_TEST_PLATFORM = "HONDA_ACCORD_9G_AU"
LAMP_TEST_LABELS = (
  "1FA bit 23", "1FA bit 22", "1FA bits 21+22", "1FA bits 21+23", "1FA bits 22+23", "1FA bits 21-23",
  "1FA bit 21", "30C bit 42",
  "30C bit 38", "1FA bit 44", "1FA bit 32", "1FA bit 33", "1FA bit 34", "1FA bit 35", "1FA bit 36",
  "1FA bit 37", "1FA bit 38",
)
# entries from here on are CMBS-adjacent unknowns (risky=True on the car side): stepping into one
# asks for a slide to confirm
LAMP_TEST_RISKY_FROM = 9


def lamp_test_text(entry: int) -> str:
  if 1 <= entry <= len(LAMP_TEST_LABELS):
    # "N: label" -- the widest, "4: 1FA bits 21+22", is ~295 px in Inter 36 against the 322 px
    # a BigButton gives its value; an "N/21" prefix would not fit
    return f"{entry}: {LAMP_TEST_LABELS[entry - 1]}"
  return "off"


def lamp_test_entry() -> int:
  try:
    entry = int(ui_state.params.get(LAMP_TEST_PARAM) or 0)
  except (TypeError, ValueError):
    return 0
  return entry if 0 <= entry <= len(LAMP_TEST_LABELS) else 0


def lamp_test_visible() -> bool:
  # the car ignores the param everywhere else; hidden until the car has been fingerprinted
  CP = ui_state.CP
  return CP is not None and CP.carFingerprint == LAMP_TEST_PLATFORM


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

    # FORK(BRAKE-LAMP-TEST): tap "lamp test" to step to the next entry, "lamp test back" to step
    # back, "lamp test off" to clear it at once. Stepping into a risky entry asks for a slide
    # first. Usable onroad: this UI keeps settings open at a standstill and pops back to the road
    # view when the car moves.
    self._lamp_entry = lamp_test_entry()
    self._lamp_btn = BigButton(tr("lamp test"), lamp_test_text(self._lamp_entry))
    self._lamp_btn.set_click_callback(lambda: self._step_lamp_entry((self._lamp_entry + 1) % (len(LAMP_TEST_LABELS) + 1)))
    self._lamp_back_btn = BigButton(tr("lamp test back"))
    self._lamp_back_btn.set_click_callback(lambda: self._step_lamp_entry(max(self._lamp_entry - 1, 0)))
    self._lamp_off_btn = BigButton(tr("lamp test off"))
    self._lamp_off_btn.set_click_callback(lambda: self._set_lamp_entry(0))
    for btn in (self._lamp_btn, self._lamp_back_btn, self._lamp_off_btn):
      btn.set_visible(lamp_test_visible)

    self._scroller.add_widgets([self._learned_info, self._learning_toggle, self._reset_btn,
                                self._lamp_btn, self._lamp_back_btn, self._lamp_off_btn])

    self._refreshed = 0.0

  def _step_lamp_entry(self, entry: int) -> None:
    if entry < LAMP_TEST_RISKY_FROM or entry == self._lamp_entry:
      self._set_lamp_entry(entry)
      return
    icon = gui_app.texture("../../sunnypilot/selfdrive/assets/offroad/icon_vehicle.png", 110, 110)
    gui_app.push_widget(BigConfirmationDialog(tr("slide to test") + f" {lamp_test_text(entry)}", icon,
                                              confirm_callback=lambda: self._set_lamp_entry(entry), red=True))

  def _set_lamp_entry(self, entry: int) -> None:
    self._lamp_entry = entry
    ui_state.params.put(LAMP_TEST_PARAM, entry, block=True)  # the 1 s refresh must not read back a stale value
    self._lamp_btn.set_value(lamp_test_text(entry))

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
    self._learning_toggle.refresh()
    # FORK(BRAKE-LAMP-TEST): the entry can also be changed from sunnylink
    entry = lamp_test_entry()
    if entry != self._lamp_entry:
      self._lamp_entry = entry
      self._lamp_btn.set_value(lamp_test_text(entry))

  def _update_state(self):
    super()._update_state()
    if time.monotonic() - self._refreshed > REFRESH_S:
      self._refresh_toggles()
