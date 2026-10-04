"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): stock ACC mode (HondaElesysStockAcc) keeps the driver's longitudinal settings.

With openpilot longitudinal off, sunnypilot deletes the settings that need it: ExperimentalMode (selfdrived, the UI),
DynamicExperimentalControl, CustomAccIncrementsEnabled, SmartCruiseControlVision and SmartCruiseControlMap
(_cleanup_unsupported_params, the UI), and it saves SpeedLimitMode assist as warning (speed_limit/helpers.py, and the
UI's speed limit panel). Right for a car that never has openpilot long; wrong for this one, where stock ACC is a mode
the owner turns on for a drive and off again. So:

* A stock-mode start snapshots them into HondaElesysStockAccSaved, before anything has deleted them (setup_interfaces,
  ahead of _cleanup_unsupported_params; CarParams are not written yet, so neither selfdrived nor the UI has seen
  openpilot long off). A key that already looks deleted keeps the value an earlier snapshot holds, so a second stock
  drive saves nothing it took itself, and a snapshot left behind by a drive that ended early is refreshed with any
  setting changed since.
* "Deleted" is what the deleters leave behind: the key gone, or - after a reboot, when manager writes the default of
  every unset key - back at its default while the snapshot holds something else; for SpeedLimitMode, warning where
  the snapshot holds assist.
* A start with openpilot long again puts back what was deleted, and nothing else, so a choice the driver made in
  between stands. That runs before this drive's settings are read (setup_interfaces) and once more from card's params
  thread (LongSettingsRestore), when CarParamsPersistent has shown this drive's CarParams for SETTLE_S: until then the
  UI (5 Hz, its own thread, reading a non-blocking write) can still act on the stock drive's CarParams and delete the
  keys again. Only then is the snapshot forgotten; a drive that ends sooner keeps it for the next start.
* A start without openpilot long and without stock mode (dashcam, an unrecognized car) leaves the snapshot alone:
  its own cleanup would only delete the keys again.
* None of this may stop card: every step is guarded, and a snapshot that cannot be used is dropped.
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

# how long CarParamsPersistent must hold this drive's CarParams before the last restore: 15 of the UI's 5 Hz ticks
SETTLE_S = 3.0
PARAMS_THREAD_DT = 0.1   # card's params thread
SETTLE_FRAMES = round(SETTLE_S / PARAMS_THREAD_DT)


def stock_acc_active(CP: structs.CarParams, CP_SP: structs.CarParamsSP) -> bool:
  return CP.brand == "honda" and bool(CP_SP.flags & HondaFlagsSP.ELESYS_STOCK_ACC)


def _snapshot(params: Params) -> dict | None:
  try:
    snapshot = params.get(SNAPSHOT_PARAM)
  except Exception:
    cloudlog.exception("honda stock ACC: unreadable settings snapshot")
    return None
  return snapshot if isinstance(snapshot, dict) else None


def _forget(params: Params) -> None:
  try:
    params.remove(SNAPSHOT_PARAM)
  except Exception:
    cloudlog.exception("honda stock ACC: could not remove the settings snapshot")


def _deleted(params: Params, key: str, saved) -> bool:
  if key == SPEED_LIMIT_MODE:
    return bool(saved == SpeedLimitMode.assist and params.get(key, return_default=True) == SpeedLimitMode.warning)
  current = params.get(key)
  if current is None:
    return True
  default = params.get_default_value(key)
  return default is not None and current == default and saved != default


def _restore(snapshot: dict, params: Params) -> list[str]:
  restored = []
  for key in PRESERVED_KEYS:
    saved = snapshot.get(key)
    if saved is not None and _deleted(params, key, saved):
      params.put(key, saved, block=True)
      restored.append(key)
  return restored


def preserve_long_settings(CP: structs.CarParams, CP_SP: structs.CarParamsSP, params: Params | None = None) -> None:
  """card, setup_interfaces: before _cleanup_unsupported_params, and before card reads DynamicExperimentalControl."""
  if params is None:
    params = Params()

  try:
    snapshot = _snapshot(params)
    if stock_acc_active(CP, CP_SP):
      saved: dict = {}
      for key in PRESERVED_KEYS:
        earlier = None if snapshot is None else snapshot.get(key)
        saved[key] = earlier if earlier is not None and _deleted(params, key, earlier) else params.get(key)
      if saved != snapshot:
        params.put(SNAPSHOT_PARAM, saved, block=True)
        cloudlog.warning(f"honda stock ACC: longitudinal settings saved {saved}")
    elif snapshot is not None and CP.openpilotLongitudinalControl:
      cloudlog.warning(f"honda stock ACC: longitudinal settings restored {_restore(snapshot, params)}")
  except Exception:
    cloudlog.exception("honda stock ACC: settings snapshot failed, dropped")
    _forget(params)


class LongSettingsRestore:
  """card's params thread (10 Hz): the last restore, once nothing can still delete what it puts back. See above."""

  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP, cp_bytes: bytes, params: Params):
    self.params = params
    self.cp_bytes = cp_bytes
    self.settled = 0
    self.pending = bool(CP.openpilotLongitudinalControl and not stock_acc_active(CP, CP_SP) and _snapshot(params) is not None)

  def update(self) -> None:
    if not self.pending:
      return
    try:
      if self.params.get("CarParamsPersistent") != self.cp_bytes:
        self.settled = 0
        return
      self.settled += 1
      if self.settled < SETTLE_FRAMES:
        return
      self.pending = False
      snapshot = _snapshot(self.params)
      if snapshot is not None:
        restored = _restore(snapshot, self.params)
        _forget(self.params)
        cloudlog.warning(f"honda stock ACC: settings snapshot done, restored again {restored}")
    except Exception:
      self.pending = False
      cloudlog.exception("honda stock ACC: settings restore failed, snapshot dropped")
      _forget(self.params)
