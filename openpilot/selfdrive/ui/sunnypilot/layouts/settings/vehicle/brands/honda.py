"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time

from openpilot.common.constants import CV
from openpilot.common.params import UnknownKeyName
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.base import BrandSettings
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp, toggle_item_sp
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog

# Speed breakpoints of the learned pedal gain, in m/s. Mirrors ELESYS_GAS_BP /
# PEDAL_GAIN_BP in opendbc/sunnypilot/car/honda/{gas_interceptor,dynamic_tuning}.py.
PEDAL_GAIN_BP = (0.0, 3.0, 6.0, 10.0, 15.0, 20.0)

# The learned state, with the same defaults as _PARAM_SPEC in
# opendbc/sunnypilot/car/honda/dynamic_tuning.py and as common/params_keys.h.
#
# Deliberately duplicated instead of imported: this panel is built while the
# settings screen is coming up, and the tuner drags in the whole opendbc car
# stack. An import error there (a submodule that isn't checked out, say) would
# take the settings panel off the screen rather than just breaking the tuner.
# selfdrive/ui/tests/test_honda_dynamic_settings.py keeps the two in sync.
LEARNED_DEFAULTS: dict[str, float] = {
  "HondaDynPedalGain0": 1.0,
  "HondaDynPedalGain1": 1.0,
  "HondaDynPedalGain2": 1.0,
  "HondaDynPedalGain3": 1.0,
  "HondaDynPedalGain4": 1.0,
  "HondaDynPedalGain5": 1.0,
  "HondaDynWindFactor": 1.0,
  "HondaDynBrakeGain": 0.0,
}

TUNING_PARAM = "HondaDynamicTuningEnabled"

# reading 13 params at 60 fps would be 13 file reads a frame; once a second is
# plenty for a readout that only changes once a minute anyway
LEARNED_REFRESH_S = 1.0

DYN_DESC = tr_noop("Learn this car's throttle and brake response while you drive, and correct for it. " +
                   "Also compensates the accel target for road grade. Honda Nidec with sunnypilot " +
                   "longitudinal only; has no effect on other platforms. Learned values are saved " +
                   "roughly once a minute and reloaded on the next drive.")
DYN_IGNITION_NOTE = tr_noop("Takes effect at the next ignition: the car reads this toggle once when it goes onroad.")
DYN_NO_LONG_DESC = tr_noop("This feature is unavailable because sunnypilot Longitudinal Control is not enabled on this car.")
LEARNED_TITLE = tr_noop("Learned Values")
LEARNED_ONROAD_NOTE = tr_noop("Resetting is only available while the car is off.")
RESET_CONFIRM = tr_noop("Reset everything this car has learned about its throttle and brakes back to the " +
                        "defaults? It starts learning again from scratch on the next drive.")


def learned_value(key: str) -> float:
  """One learned param, coerced to a float, with the tuner's default as the floor.

  Params.get() hands back whatever the value parses as; a param that was never
  written comes back None, and a corrupt one could come back as anything at
  all. UnknownKeyName means the params registry on this device predates the
  tuner. None of those should take a settings page down.
  """
  try:
    value = ui_state.params.get(key, return_default=True)
    return float(value) if value is not None else LEARNED_DEFAULTS[key]
  except (TypeError, ValueError, UnknownKeyName):
    return LEARNED_DEFAULTS[key]


def learned_pedal_gains() -> list[float]:
  return [learned_value(f"HondaDynPedalGain{i}") for i in range(len(PEDAL_GAIN_BP))]


def reset_learned_values() -> None:
  """Put every learned param back to its default. Offroad only -- the tuner
  holds the learned state in memory and rewrites it every 60 s, so a reset
  while driving would be undone a minute later."""
  if not ui_state.is_offroad():
    return
  try:
    for key, default in LEARNED_DEFAULTS.items():
      ui_state.params.put(key, float(default))
  except UnknownKeyName:
    # params registry predates the tuner: there is nothing learned to reset,
    # and raising out of a button callback would take the UI down
    pass


class HondaSettings(BrandSettings):
  def __init__(self):
    super().__init__()
    self._learned_text = ""
    self._learned_updated = 0.0

    self.dynamic_tuning_toggle = toggle_item_sp(
      title=tr("Dynamic Longitudinal Learning (Alpha)"),
      description=tr(DYN_DESC),
      param=TUNING_PARAM)

    self.learned_values_item = button_item_sp(
      title=tr(LEARNED_TITLE),
      button_text=tr("RESET"),
      description=lambda: self._learned_text,
      callback=self._on_reset_clicked,
      # the tuner writes the learned values every 60 s while driving, so a reset
      # onroad would just be overwritten by what is already in memory
      enabled=ui_state.is_offroad)

    self.items = [self.dynamic_tuning_toggle, self.learned_values_item]

    self._toggle_params = {
      TUNING_PARAM: self.dynamic_tuning_toggle.action_item.get_state(),
    }

  def _on_reset_clicked(self) -> None:
    gui_app.push_widget(ConfirmDialog(text=tr(RESET_CONFIRM), confirm_text=tr("Reset"), callback=self._on_reset_confirmed))

  @staticmethod
  def _on_reset_confirmed(result: int) -> None:
    # reset_learned_values re-checks offroad: the dialog can sit open across an ignition
    if result == DialogResult.CONFIRM:
      reset_learned_values()

  def _build_learned_text(self) -> str:
    gains = learned_pedal_gains()
    speed_factor = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    unit = tr("km/h") if ui_state.is_metric else tr("mph")

    # the description renderer collapses every run of whitespace, newlines
    # included -- a line break is <br>, and a tag boundary is what starts a new
    # block, so the separators here are load bearing
    bands = " | ".join(f"{round(bp * speed_factor):d}: {gain:.2f}" for bp, gain in zip(PEDAL_GAIN_BP, gains, strict=True))
    text = (f"<b>{tr('Pedal gain by speed')} ({unit})</b>" + bands + "<br>" +
            f"{tr('Aero')} x{learned_value('HondaDynWindFactor'):.2f} | " +
            # stored as an offset (0.0 = no correction), shown as a gain so it reads
            # the same way as the pedal and aero numbers either side of it
            f"{tr('Brake')} x{1.0 + learned_value('HondaDynBrakeGain'):.2f}")
    if not ui_state.is_offroad():
      text += "<br>" + tr(LEARNED_ONROAD_NOTE)
    return text

  def _sync_toggles(self) -> None:
    # keep the toggles honest: the same two params also have toggles in
    # Settings > Cruise, and ToggleSP only reads its param at construction.
    # Edge triggered on the param, never level: a tap writes its param
    # non-blocking, so a level sync would drag the toggle back to the old value
    # for the frame or two before that write lands.
    for param, item in ((TUNING_PARAM, self.dynamic_tuning_toggle),):
      value = ui_state.params.get_bool(param)
      if value != self._toggle_params[param]:
        self._toggle_params[param] = value
        item.action_item.set_state(value)

  def update_settings(self):
    self._sync_toggles()

    # <br> rather than a newline: the description renderer collapses whitespace,
    # so "\n\n" would run the two sentences together on one line
    dyn_desc = tr(DYN_DESC) + "<br>" + tr(DYN_IGNITION_NOTE)
    if not ui_state.has_longitudinal_control:
      dyn_desc = "<b>" + tr(DYN_NO_LONG_DESC) + "</b>" + dyn_desc
    if self.dynamic_tuning_toggle.description != dyn_desc:
      self.dynamic_tuning_toggle.set_description(dyn_desc)
    self.dynamic_tuning_toggle.show_description(True)

    now = time.monotonic()
    if not self._learned_text or now - self._learned_updated > LEARNED_REFRESH_S:
      self._learned_updated = now
      self._learned_text = self._build_learned_text()
    self.learned_values_item.show_description(True)
