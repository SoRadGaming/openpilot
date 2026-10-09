"""
FORK(HONDA_ACCORD_9G_AU): the mark on a HELD lead in radarState, and the test every leadOne consumer but the MPC uses.

radard's clutter guard (elesys_radar_guard.py, HONDA_ELESYS only) can publish as leadOne a track it refused as a lead:
the hold, a lead at the car's own speed and the MPC's desired distance, which only stops the plan accelerating toward
the object. Its dRel, vLead, vRel and aLeadK are made up, so it is a lead for the longitudinal MPC and nothing else:
not for DEC's mode choice, longitudinalPlan.hasLead (the dash's lead icon, 0x500, shadow_learn's launch bins), the e2e
alerts, the onroad chevrons and path, the developer UI, the comma 4 rail, or a route tool reading leadOne as a car.

The mark is radarTrackId, which radard already fills: a track's id is 0 or more, a vision-only lead's is -1 (cereal's
default), and a held lead's is HELD_ID_BASE - the track's id (-2 or less), so the held track can still be read back
from a route (held_track_id()). No schema change, and every other car never produces it.
"""

HELD_ID_BASE = -2


def held_id(track_id: int) -> int:
  """radarTrackId for a lead held on radar track track_id (>= 0)."""
  return HELD_ID_BASE - int(track_id)


def is_held(lead) -> bool:
  """Is this leadOne a held lead (made-up kinematics, for the MPC only)? Takes a cereal LeadData, a dict or None."""
  if lead is None:
    return False
  tid = lead.get('radarTrackId', -1) if isinstance(lead, dict) else getattr(lead, 'radarTrackId', -1)
  return int(tid) <= HELD_ID_BASE


def held_track_id(lead) -> int:
  """The radar track a held lead stands for (from a route, for example); -1 if it is not held."""
  if not is_held(lead):
    return -1
  tid = lead['radarTrackId'] if isinstance(lead, dict) else lead.radarTrackId
  return HELD_ID_BASE - int(tid)


def real_lead(lead) -> bool:
  """leadOne.present for every consumer but the MPC: present and not held."""
  if lead is None:
    return False
  present = lead.get('present', False) if isinstance(lead, dict) else getattr(lead, 'present', False)
  return bool(present) and not is_held(lead)
