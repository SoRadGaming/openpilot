"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): the sound and the alerts of a refused lane change, with the comma 4's lane-block tile on (HudLaneBlock; the
owner's option 3, 2026-10-12). The refusal itself is untouched: DesireHelper still never starts a change toward a road
edge (RELC) or a car in the blind spot, and selfdrived still raises laneChangeBlocked / laneChangeRoadEdge for it.

With the tile on, on a comma 4 only:
  * the two block alerts are dropped before the AlertManager sees them - the tile (hud_lane_block.py) shows the block
    instead, so nothing covers the road and nothing chimes when the block appears; the events are still raised and
    logged (onroadEvents / onroadEventsSP);
  * a nudge the block refused (modelDataV2SP.laneChangeNudgeRefused, from DesireHelper, which alone knows the nudge,
    NUDGE_FIRM and a stale gateway torque) raises laneChangeNudgeRefused on its rising edge: one size-none alert with
    one 'prompt' chime per refused nudge, never repeated while it is held. DesireHelper lets the flag rise at most once
    in 2 s (NUDGE_REFUSED_GAP_FRAMES) - the limit lives there, not here, because the UI flashes the tile on the same
    rising edge: one source, so the flash and the chime are always a pair.
  * with another alert on screen (LOW and up) the chime (Priority.LOWER) does not play and the tile is not drawn: a
    refused nudge then has no answer, and that alert has the screen and the sound. A banner at LOWER or LOWEST
    (such as experimental mode switched, bookmark saved, startup, the EPS-latched reminder) is the exception: the chime takes
    its place for 0.2 s and plays, and the tile stays undrawn.
With it off, or on any other device (the comma 3X keeps its own alerts), selfdrived is as upstream.
"""
from openpilot.cereal import log, custom
from openpilot.sunnypilot.selfdrive.selfdrived.events import IS_MICI

PARAM = "HudLaneBlock"
LaneChangeState = log.LaneChangeState
EventNameSP = custom.OnroadEventSP.EventName

# the alerts the tile replaces, by the AlertManager's alert_type prefix ("<event name>/<event type>")
DROPPED = ("laneChangeBlocked/",)
DROPPED_SP = ("laneChangeRoadEdge/",)


class LaneBlockAlert:
  def __init__(self, is_mici: bool = IS_MICI):
    self.is_mici = is_mici
    self.on = False
    self._refused = False

  def read_params(self, params) -> None:
    """From selfdrived's params thread. Unreadable (a params library that predates the key) is off: the stock alerts."""
    try:
      self.on = self.is_mici and bool(params.get_bool(PARAM))
    except Exception:
      self.on = False

  def update(self, lane_change_state, mdv2sp) -> list:
    """The SP events for this frame: laneChangeNudgeRefused on the frame a refused nudge is first seen."""
    refused = self.on and lane_change_state == LaneChangeState.preLaneChange and mdv2sp.laneChangeNudgeRefused
    rising = refused and not self._refused
    self._refused = refused
    return [EventNameSP.laneChangeNudgeRefused] if rising else []

  def drop_block_alerts(self, alerts: list, alerts_sp: list) -> tuple[list, list]:
    """selfdrived's update_alerts(): the two block alerts dropped while the tile is on."""
    if not self.on:
      return alerts, alerts_sp
    return ([a for a in alerts if not a.alert_type.startswith(DROPPED)],
            [a for a in alerts_sp if not a.alert_type.startswith(DROPPED_SP)])
