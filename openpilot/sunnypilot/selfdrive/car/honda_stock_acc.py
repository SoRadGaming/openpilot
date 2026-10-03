"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): stock ACC mode (HondaElesysStockAcc) keeps the driver's longitudinal settings.

With openpilot longitudinal off, sunnypilot deletes the settings that need it: ExperimentalMode (selfdrived, the UI),
DynamicExperimentalControl, CustomAccIncrementsEnabled, SmartCruiseControlVision and SmartCruiseControlMap
(_cleanup_unsupported_params, the UI), and it saves SpeedLimitMode assist as warning (speed_limit/helpers.py). Right
for a car that never has openpilot long; wrong for this one, where stock ACC is a mode the owner turns on for a drive
and off again. So:

* The first stock-mode start snapshots them into HondaElesysStockAccSaved, before anything has deleted them
  (setup_interfaces, ahead of _cleanup_unsupported_params; CarParams are not written yet, so neither selfdrived nor
  the UI has seen openpilot long off). A second stock drive keeps the first snapshot.
* The first start with openpilot long again puts back what the deleters took - a key that is gone, and assist where
  warning was saved over it - and nothing else, so a choice the driver made in between stands. That runs before
  this drive's settings are read, and once more after card has written CarParamsPersistent: until then the UI, at
  5 Hz, still sees the stock drive's CarParams and deletes the keys again. Then the snapshot is forgotten.
* A start without openpilot long and without stock mode (dashcam, an unrecognized car) leaves the snapshot alone:
  its own cleanup would only delete the keys again.
"""
from opendbc.car import structs
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode as SpeedLimitMode

STOCK_ACC_PARAM = "HondaElesysStockAcc"
SNAPSHOT_PARAM = "HondaElesysStockAccSaved"

# removed outright while openpilot long is off
REMOVED_KEYS = ("ExperimentalMode", "DynamicExperimentalControl", "CustomAccIncrementsEnabled",
                "SmartCruiseControlVision", "SmartCruiseControlMap")
# assist is saved as warning while openpilot long is off
SPEED_LIMIT_MODE = "SpeedLimitMode"
PRESERVED_KEYS = (*REMOVED_KEYS, SPEED_LIMIT_MODE)


def stock_acc_active(CP: structs.CarParams, CP_SP: structs.CarParamsSP) -> bool:
  return CP.brand == "honda" and bool(CP_SP.flags & HondaFlagsSP.ELESYS_STOCK_ACC)


def _snapshot(params: Params) -> dict | None:
  try:
    snapshot = params.get(SNAPSHOT_PARAM)
  except Exception:
    cloudlog.exception("honda stock ACC: unreadable settings snapshot")
    return None
  return snapshot if isinstance(snapshot, dict) else None


def _restore(snapshot: dict, params: Params, speed_limit_mode: bool) -> list[str]:
  restored = []
  for key in REMOVED_KEYS:
    value = snapshot.get(key)
    if value is not None and params.get(key) is None:
      params.put(key, value, block=True)
      restored.append(key)
  if speed_limit_mode and snapshot.get(SPEED_LIMIT_MODE) == SpeedLimitMode.assist and \
     params.get(SPEED_LIMIT_MODE, return_default=True) == SpeedLimitMode.warning:
    params.put(SPEED_LIMIT_MODE, int(SpeedLimitMode.assist), block=True)
    restored.append(SPEED_LIMIT_MODE)
  return restored


def preserve_long_settings(CP: structs.CarParams, CP_SP: structs.CarParamsSP, params: Params | None = None) -> None:
  """card, setup_interfaces: before _cleanup_unsupported_params, and before card reads DynamicExperimentalControl."""
  if params is None:
    params = Params()

  snapshot = _snapshot(params)
  if stock_acc_active(CP, CP_SP):
    if snapshot is None:
      saved = {key: params.get(key) for key in PRESERVED_KEYS}
      params.put(SNAPSHOT_PARAM, saved, block=True)
      cloudlog.warning(f"honda stock ACC: longitudinal settings saved {saved}")
  elif snapshot is not None and CP.openpilotLongitudinalControl:
    cloudlog.warning(f"honda stock ACC: longitudinal settings restored {_restore(snapshot, params, True)}")


def finish_long_settings_restore(CP: structs.CarParams, CP_SP: structs.CarParamsSP, params: Params | None = None) -> None:
  """card, right after CarParamsPersistent is written: the UI stops deleting from here, so put back anything it took
  in between (never SpeedLimitMode, which only card and plannerd touch, both from this drive's CarParams), then
  forget the snapshot."""
  if params is None:
    params = Params()

  if stock_acc_active(CP, CP_SP) or not CP.openpilotLongitudinalControl:
    return
  snapshot = _snapshot(params)
  if snapshot is not None:
    restored = _restore(snapshot, params, False)
    params.remove(SNAPSHOT_PARAM)
    cloudlog.warning(f"honda stock ACC: settings snapshot done, restored again {restored}")
