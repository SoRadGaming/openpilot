"""
FORK(CLOSE-FOLLOW): where close follow runs, and its user switch - the light half of close_follow.py (push14: moved here
unchanged, so that the four dash bars - distance_bars.py, in selfdrived and controlsd - share them without loading
close follow's MPC). close_follow.py re-exports all three.
"""
from opendbc.car.honda.values import HONDA_ELESYS
from opendbc.sunnypilot.car.honda.values_ext import HondaFlagsSP

# CloseFollow (params_keys.h, BOOL, default "1"): the user's switch - since push14 the master: on, the distance button
# reaches the 1-bar mode, where close follow is live (distance_bars.py); off, it never goes live.
SWITCH_PARAM = "CloseFollow"


def close_follow_applies(CP, CP_SP) -> bool:
  """HONDA_ELESYS with openpilot longitudinal and stock ACC mode off (the re-entry ramp's gate)."""
  try:
    if CP.brand != 'honda' or CP.carFingerprint not in HONDA_ELESYS or not CP.openpilotLongitudinalControl:
      return False
    return not (int(getattr(CP_SP, 'flags', 0)) & HondaFlagsSP.ELESYS_STOCK_ACC.value)
  except Exception:
    return False


def read_switch(params) -> bool:
  """The user's CloseFollow switch (a key nobody has written yet is its params_keys.h default, ON). Anything unreadable
  is OFF: an unknown switch never makes it live."""
  try:
    v = params.get(SWITCH_PARAM, return_default=True)
    if isinstance(v, bool):
      return v
    if isinstance(v, bytes):
      v = v.decode(errors='ignore')
    return str(v).strip() == "1"
  except Exception:
    return False
