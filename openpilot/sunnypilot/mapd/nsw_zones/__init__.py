"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): speed limits from Transport for NSW's Speed Zones and School Zones open data.

  index.py        load + validate an index file (format version, sha256 from the manifest, attribution)
  matcher.py      the map matcher: GPS fix -> NSW zone limit, dead reckoning through tunnels, look-ahead
  school_days.py  the NSW school-zone day calendar (terms, development days, public holidays)
  build_index.py  the builder CLI (PC / CI only; numpy + stdlib, no openpilot imports)
  downloader.py   the device side of the weekly release: fetch, verify, install, and when to do it

Every module here is pure Python + numpy and never touches the network or messaging; the car-side
wiring lives in live_map_data/nsw_map_data.py and the downloader.

Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0.
Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW.
"""

ATTRIBUTION = (
  "Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0. "
  + "Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW."
)
ATTRIBUTION_SHORT = "Contains data from Transport for NSW, CC BY 4.0, modified"

# SpeedLimitNswZones (params_keys.h): 0 off, 1 log only (nswZone filled, the published limit stays OSM), 2 live.
MODE_PARAM = "SpeedLimitNswZones"
MODE_OFF = 0
MODE_LOG_ONLY = 1
MODE_LIVE = 2


def read_mode(params) -> int:
  """SpeedLimitNswZones as 0/1/2. Anything unreadable or out of range is OFF: an unknown mode must not publish."""
  try:
    mode = int(params.get(MODE_PARAM, return_default=True))
  except (TypeError, ValueError):
    return MODE_OFF
  return mode if mode in (MODE_OFF, MODE_LOG_ONLY, MODE_LIVE) else MODE_OFF
