#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json
import platform
import os
import glob
import shutil
from datetime import datetime

from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
from openpilot.sunnypilot.mapd.live_map_data.osm_map_data import OsmMapData
from openpilot.common.hardware.hw import Paths
from openpilot.sunnypilot.mapd import MAPD_PATH
from openpilot.sunnypilot.mapd.mapd_installer import VERSION, update_installed_version
from openpilot.sunnypilot.mapd.osm_auto_update import OsmAutoUpdater  # FORK(SPEED-LIMIT): weekly OSM refresh
# FORK(NSW-ZONES): the NSW zone modules are imported inside main_thread(), not here: the manager imports this module
# (process_config, for MAPD_PATH), so an error in them must cost the feature, never openpilot's start.

# PFEIFER - MAPD {{
params = Params()
mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else params
# }} PFEIFER - MAPD


def get_files_for_cleanup() -> list[str]:
  paths = [
    f"{Paths.mapd_root()}/db",
    f"{Paths.mapd_root()}/v*"
  ]
  files_to_remove = []
  for path in paths:
    if os.path.exists(path):
      files = glob.glob(path + '/**', recursive=True)
      files_to_remove.extend(files)
  # check for version and mapd files
  if not os.path.isfile(MAPD_PATH):
    files_to_remove.append(MAPD_PATH)
  return files_to_remove


def cleanup_old_osm_data(files_to_remove: list[str]) -> None:
  for file in files_to_remove:
    # Remove trailing slash if path is file
    if file.endswith('/') and os.path.isfile(file[:-1]):
      file = file[:-1]
    # Try to remove as file or symbolic link first
    if os.path.islink(file) or os.path.isfile(file):
      os.remove(file)
    elif os.path.isdir(file):  # If it's a directory
      shutil.rmtree(file, ignore_errors=False)


def clear_downloaded_maps() -> None:
  """Deletes downloaded OSM map data and resets params."""
  path = f"{Paths.mapd_root()}/offline"
  if os.path.exists(path):
    shutil.rmtree(path, ignore_errors=True)

  for param in ("OsmDownloadedDate", "OsmLocal", "OsmLocationName", "OsmLocationTitle",
                "OsmStateName", "OsmStateTitle"):
    params.remove(param)
  params.remove("OsmLastCompleteDate")  # FORK(SPEED-LIMIT): osm_auto_update.record_completion() writes it

  cloudlog.info("mapd: downloaded maps cleared")


def request_refresh_osm_location_data(nations: list[str], states: list[str] | None = None) -> None:
  params.put("OsmDownloadedDate", str(datetime.now().timestamp()), block=True)
  params.put_bool("OsmDbUpdatesCheck", False, block=True)

  osm_download_locations = {
    "nations": nations,
    "states": states or []
  }

  print(f"Downloading maps for {json.dumps(osm_download_locations)}")
  mem_params.put("OSMDownloadLocations", osm_download_locations, block=True)


def filter_nations_and_states(nations: list[str], states: list[str] | None = None) -> tuple[list[str], list[str]]:
  """Filters and prepares nation and state data for OSM map download.

  If the nation is 'US' and a specific state is provided, the nation 'US' is removed from the list.
  If the nation is 'US' and the state is 'All', the 'All' is removed from the list.
  The idea behind these filters is that if a specific state in the US is provided,
  there's no need to download map data for the entire US. Conversely,
  if the state is unspecified (i.e., 'All'), we intend to download map data for the whole US,
  and 'All' isn't a valid state name, so it's removed.

  Parameters:
  nations (list): A list of nations for which the map data is to be downloaded.
  states (list, optional): A list of states for which the map data is to be downloaded. Defaults to None.

  Returns:
  tuple: Two lists. The first list is filtered nations and the second list is filtered states.
  """

  if "US" in nations and states and not any(x.lower() == "all" for x in states):
    # If a specific state in the US is provided, remove 'US' from nations
    nations.remove("US")
  elif "US" in nations and states and any(x.lower() == "all" for x in states):
    # If 'All' is provided as a state (case invariant), remove those instances from states
    states = [x for x in states if x.lower() != "all"]
  elif "US" not in nations and states and any(x.lower() == "all" for x in states):
    states.remove("All")
  return nations, states or []


def update_osm_db() -> None:
  if params.get_bool("OsmDbUpdatesCheck"):
    cleanup_old_osm_data(get_files_for_cleanup())
    country = params.get("OsmLocationName", return_default=True)
    state = params.get("OsmStateName", return_default=True)
    filtered_nations, filtered_states = filter_nations_and_states([country], [state])
    request_refresh_osm_location_data(filtered_nations, filtered_states)

  if not mem_params.get("OSMDownloadBounds"):
    mem_params.put("OSMDownloadBounds", "", block=True)

  if not mem_params.get("LastGPSPosition"):
    mem_params.put("LastGPSPosition", "{}", block=True)


# FORK(NSW-ZONES)
def update_nsw_alert(nsw_map_sp, shown: str) -> str:
  """Offroad_NswZonesStale while the data is over 60 days old or the school calendar ends within 60 days. Set only on a
  change (it is a param write). Never raises. -> the text now shown ('' none)."""
  try:
    from openpilot.common.time_helpers import system_time_valid
    text = (nsw_map_sp.stale_alert(datetime.now().date()) or "") if system_time_valid() else shown
    if text != shown:
      set_offroad_alert("Offroad_NswZonesStale", bool(text), text or None)
    return text
  except Exception:
    cloudlog.exception("mapd: NSW zones alert failed")
    return shown


def main_thread():
  update_installed_version(VERSION, params)
  config_realtime_process([0, 1, 2, 3], 5)

  rk = Ratekeeper(1, print_delay_threshold=None)
  # FORK(NSW-ZONES): chosen once, here: liveMapDataSP has one publisher. Off (0) is upstream's OsmMapData exactly;
  # NswZoneMapData follows later changes between 1 and 2 (and to 0) itself, turning it on from off needs a restart.
  # Only the IMPORT can fail over to OsmMapData: once NswZoneMapData's constructor has made the publisher it never
  # raises (a failed NSW setup leaves it publishing OSM), so there is never a second liveMapDataSP PubMaster.
  nsw_map_sp = None
  nsw_updater = None
  try:
    from openpilot.sunnypilot.mapd.nsw_zones import MODE_OFF, read_mode
    nsw_on = read_mode(params) != MODE_OFF
  except Exception:
    cloudlog.exception("mapd: NSW zones unavailable, publishing OSM only")
    nsw_on = False
  if nsw_on:
    try:
      from openpilot.sunnypilot.mapd.live_map_data.nsw_map_data import NswZoneMapData  # FORK(NSW-ZONES)
    except Exception:
      cloudlog.exception("mapd: NSW zones failed to import, publishing OSM only")
    else:
      nsw_map_sp = NswZoneMapData()  # FORK(NSW-ZONES): never raises once it has made the publisher
  live_map_sp: OsmMapData = nsw_map_sp if nsw_map_sp is not None else OsmMapData()
  auto_updater = OsmAutoUpdater(params, mem_params)  # FORK(SPEED-LIMIT): gated by OsmAutoUpdateWeekly, once per boot
  # FORK(NSW-ZONES): first download automatic, weekly after, the maps page button; parked only. A new index is loaded
  # into the running matcher at the next park (the updater defers it if the car has started meanwhile).
  try:
    from openpilot.sunnypilot.mapd.nsw_zones.downloader import NswZonesUpdater
    nsw_updater = NswZonesUpdater(params, on_installed=nsw_map_sp.reload if nsw_map_sp is not None else None)
  except Exception:
    cloudlog.exception("mapd: the NSW zones updater failed to start")
  nsw_alert = ""

  # Create folder needed for OSM
  try:
    os.mkdir(Paths.mapd_root())
  except FileExistsError:
    pass
  except PermissionError:
    cloudlog.exception(f"mapd: failed to make {Paths.mapd_root()}")

  while True:
    show_alert = bool(get_files_for_cleanup() and params.get_bool("OsmLocal"))
    set_offroad_alert("Offroad_OSMUpdateRequired", show_alert, "This alert will be cleared when new maps are downloaded.")

    if params.get("Mapd_ClearCache"):
      clear_downloaded_maps()
      params.remove("Mapd_ClearCache")

    auto_updater.update()  # FORK(SPEED-LIMIT): may set OsmDbUpdatesCheck; must run before update_osm_db()
    update_osm_db()
    if nsw_updater is not None:
      nsw_updater.update()  # FORK(NSW-ZONES): never raises; the download runs in its own thread
    live_map_sp.tick()
    if nsw_map_sp is not None and rk.frame % 60 == 0:  # FORK(NSW-ZONES): old data / an expiring school calendar
      nsw_alert = update_nsw_alert(nsw_map_sp, nsw_alert)
    rk.keep_time()


def main():
  main_thread()


if __name__ == "__main__":
  main()
