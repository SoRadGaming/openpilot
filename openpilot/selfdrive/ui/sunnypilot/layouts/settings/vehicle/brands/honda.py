"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time

from openpilot.common.params import UnknownKeyName
from openpilot.selfdrive.ui.sunnypilot.layouts.settings.vehicle.brands.base import BrandSettings
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp, toggle_item_sp
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.confirm_dialog import ConfirmDialog

# The learned (and counted) state, with the same defaults as _PARAM_SPEC in
# opendbc/sunnypilot/car/honda/dynamic_tuning.py and as common/params_keys.h.
# The per-band pedal gains and the aero factor are gone: their learners were
# retired in 2026-10 (they could not move), and showing their last values would
# be showing numbers nothing reads.
#
# Deliberately duplicated instead of imported: this panel is built while the
# settings screen is coming up, and the tuner drags in the whole opendbc car
# stack. An import error there (a submodule that isn't checked out, say) would
# take the settings panel off the screen rather than just breaking the tuner.
# selfdrive/ui/tests/test_honda_dynamic_settings.py keeps the two in sync.
LEARNED_DEFAULTS: dict[str, float] = {
  "HondaDynBrakeGain": 0.0,
  "HondaDynModeSecD": 0.0,
  "HondaDynModeSecECON": 0.0,
  "HondaDynModeSecS": 0.0,
}

# Drive-mode slots in the tuner's order (DRIVE_MODE_SLOTS in elesys_gas.py); the
# running engaged seconds of each are HondaDynModeSec<slot>.
MODE_SLOTS = ("D", "ECON", "S")

# What RESET puts back: the learned brake correction only. The HondaDynModeSec*
# totals are a tally of the data collected for per-mode pedal tables, not a tune,
# so resetting the brake learner (after a brake job, say) keeps them.
RESET_KEYS = ("HondaDynBrakeGain",)

TUNING_PARAM = "HondaDynamicTuningEnabled"
# Which gas law the car runs, read once at ignition. Mirrors GAS_LAW_PARAM and
# GAS_LAW_DEFAULT in opendbc/sunnypilot/car/honda/elesys_gas.py; the setting itself
# is in sunnylink (Vehicle > Honda Settings).
GAS_LAW_PARAM = "HondaElesysGasLawV2"
GAS_LAW_DEFAULT = True
# The platforms the gas law applies to: HONDA_ELESYS in opendbc/car/honda/values.py
# (the 2013-15 Accord AU). Every other Honda runs upstream's pedal law whatever the
# setting says, so the readout is not shown there. Spelled out for the same reason as
# LEARNED_DEFAULTS; test_honda_dynamic_settings.py keeps it in sync with opendbc.
GAS_LAW_PLATFORMS = ("HONDA_ACCORD_9G_AU",)

# FORK(HONDA_ACCORD_9G_AU): stock ACC mode, read once at ignition by opendbc's _initialize_honda. Offroad only, so the
# mode never changes under a drive - and no OnroadCycleRequested (Toyota's pattern), which would drop lateral while
# moving. Not BACKUP in params_keys.h: a sunnylink restore never turns it on.
STOCK_ACC_PARAM = "HondaElesysStockAcc"
STOCK_ACC_TITLE = tr_noop("Stock ACC (testing)")
STOCK_ACC_DESC = tr_noop("The car's own cruise control does gas and brake; openpilot steers only. Takes effect at the " +
                         "next car start. Stock ACC works above about 30 km/h. openpilot cannot cancel stock ACC: use " +
                         "the car's CANCEL button or the brake. CMBS is unaffected. 2013-15 Accord (Elesys) only.")
STOCK_ACC_OFFROAD_NOTE = tr_noop("Can only be changed while the car is off.")

# reading 13 params at 60 fps would be 13 file reads a frame; once a second is
# plenty for a readout that only changes once a minute anyway
LEARNED_REFRESH_S = 1.0

DYN_DESC = tr_noop("Learn this car's brake response while you drive, and correct for it. " +
                   "Also compensates the accel target for road grade, and counts driving time " +
                   "in each drive mode (D, ECON, S). Honda Nidec with sunnypilot " +
                   "longitudinal only; has no effect on other platforms. Learned values are saved " +
                   "roughly once a minute and reloaded on the next drive.")
DYN_IGNITION_NOTE = tr_noop("Takes effect at the next ignition: the car reads this toggle once when it goes onroad.")
DYN_NO_LONG_DESC = tr_noop("This feature is unavailable because sunnypilot Longitudinal Control is not enabled on this car.")
LEARNED_TITLE = tr_noop("Learned Values")
LEARNED_ONROAD_NOTE = tr_noop("Resetting is only available while the car is off.")
RESET_CONFIRM = tr_noop("Reset what this car has learned about its brakes back to the default? It starts " +
                        "again from scratch on the next drive. The engaged time per drive mode is kept.")


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


def gas_law_v2() -> bool:
  """The gas-law setting as the car will read it at the next ignition; the registered
  default if it is unset, unreadable, or the registry predates it."""
  try:
    value = ui_state.params.get(GAS_LAW_PARAM, return_default=True)
    return GAS_LAW_DEFAULT if value is None else bool(value)
  except (TypeError, ValueError, UnknownKeyName):
    return GAS_LAW_DEFAULT


def car_platform() -> str:
  """The platform the car runs as: the selected platform first, the fingerprint second
  (as hyundai.py and subaru.py resolve it). '' when neither is known."""
  try:
    if bundle := ui_state.params.get("CarPlatformBundle"):
      return str(bundle.get("platform", "") or "")
    if ui_state.CP is not None:
      return str(ui_state.CP.carFingerprint)
  except Exception:
    pass
  return ""


def gas_law_applies() -> bool:
  """True on a car the gas-law setting does something on (GAS_LAW_PLATFORMS)."""
  return car_platform() in GAS_LAW_PLATFORMS


def gas_law_label(short: bool = False) -> str:
  if short:
    return "v2" if gas_law_v2() else "v1"
  return tr("v2, measured") if gas_law_v2() else tr("v1, previous")


def mode_minutes() -> dict[str, str]:
  """Engaged, moving minutes per drive mode, as the tuner last saved them, formatted:
  tenths below ten minutes, which is where ECON and S live."""
  out = {}
  for slot in MODE_SLOTS:
    m = learned_value(f"HondaDynModeSec{slot}") / 60.0
    out[slot] = f"{m:.0f}" if m >= 10.0 else f"{m:.1f}"
  return out


def mode_time_text() -> str:
  """'D 412 min | ECON 1.4 min | S 1.3 min'"""
  return " | ".join(f"{slot} {m} {tr('min')}" for slot, m in mode_minutes().items())


def reset_learned_values() -> None:
  """Put the learned brake correction (RESET_KEYS) back to its default; the
  drive-mode totals are kept. Offroad only -- the tuner holds the learned state
  in memory and rewrites it every 60 s, so a reset while driving would be undone
  a minute later."""
  if not ui_state.is_offroad():
    return
  try:
    for key in RESET_KEYS:
      ui_state.params.put(key, float(LEARNED_DEFAULTS[key]))
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

    self.stock_acc_toggle = toggle_item_sp(
      title=tr(STOCK_ACC_TITLE),
      description=tr(STOCK_ACC_DESC),
      param=STOCK_ACC_PARAM,
      enabled=ui_state.is_offroad)

    self.items = [self.dynamic_tuning_toggle, self.learned_values_item, self.stock_acc_toggle]

    self._toggle_params = {
      TUNING_PARAM: self.dynamic_tuning_toggle.action_item.get_state(),
      STOCK_ACC_PARAM: self.stock_acc_toggle.action_item.get_state(),
    }

  def _on_reset_clicked(self) -> None:
    gui_app.push_widget(ConfirmDialog(text=tr(RESET_CONFIRM), confirm_text=tr("Reset"), callback=self._on_reset_confirmed))

  @staticmethod
  def _on_reset_confirmed(result: int) -> None:
    # reset_learned_values re-checks offroad: the dialog can sit open across an ignition
    if result == DialogResult.CONFIRM:
      reset_learned_values()

  def _build_learned_text(self) -> str:
    # the description renderer collapses every run of whitespace, newlines
    # included -- a line break is <br>, and a tag boundary is what starts a new
    # block, so the separators here are load bearing
    # the gas law only exists on GAS_LAW_PLATFORMS; another Honda would be shown a setting it ignores
    text = (f"<b>{tr('Gas law')}</b>" + gas_law_label() + " " + tr("(from the next drive)") + "<br>"
            if gas_law_applies() else "")
    text += (f"<b>{tr('Engaged time by drive mode')}</b>" + mode_time_text() + "<br>" +
             # stored as an offset (0.0 = no correction), shown as a gain
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
    for param, item in ((TUNING_PARAM, self.dynamic_tuning_toggle), (STOCK_ACC_PARAM, self.stock_acc_toggle)):
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

    stock_acc_desc = tr(STOCK_ACC_DESC) + ("" if ui_state.is_offroad() else "<br>" + tr(STOCK_ACC_OFFROAD_NOTE))
    if self.stock_acc_toggle.description != stock_acc_desc:
      self.stock_acc_toggle.set_description(stock_acc_desc)
    self.stock_acc_toggle.show_description(True)

    now = time.monotonic()
    if not self._learned_text or now - self._learned_updated > LEARNED_REFRESH_S:
      self._learned_updated = now
      self._learned_text = self._build_learned_text()
    self.learned_values_item.show_description(True)
