"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Settings > maps, on the small screen. How old the map data on the device is,
and a way to refresh it.

WHY THIS PAGE EXISTS. The OSM tiles that drive the map speed limit are fetched
once, when a region is chosen, and refreshed only by the comma 3/3X OSM panel's
"Database Update". This UI has no OSM panel, so on a comma 4 the tiles were
never refreshed at all and nothing on the device said how old they were.

WHAT THE DATE MEANS. OsmDownloadedDate is written by mapd_manager when a
download is REQUESTED, not when it completes, so a download cut short by a
power off still moves it. The page shows OsmLastCompleteDate instead: the
request date of the last download mapd finished, recorded by
osm_auto_update.record_completion(). With no completion on record (a download
from before the recorder existed) the header reads "requested" instead of
"updated", and when a later request never completed the button says "incomplete".

WHEN IT MAY RUN. Offroad is not enough: with OffroadMode on, deviceState says
offroad while the car is driving, so the button also wants the ignition off.
The weekly refresh runs parked and on wi-fi, and a phone hotspot IS wi-fi: it
reads unmetered unless the connection is marked metered. The toggle says so.

ONE DATA SET PER CARD. Each map data set is a MapDataInfo card followed by its
own update button, appended to the scroller in MapsLayoutMici. A second data set
is a second (card, button) pair, and nothing about the OSM pair has to change
for it.
"""
import datetime
import platform
import time
from collections.abc import Callable

import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.ui.mici.widgets.button import BigButton, BigParamControl
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigDialog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

# params are files: read them on a tick, not every frame
REFRESH_S = 1.0

# the same request param the comma 3/3X OSM panel writes; mapd_manager consumes it
UPDATE_PARAM = "OsmDbUpdatesCheck"
AUTO_UPDATE_PARAM = "OsmAutoUpdateWeekly"
COMPLETE_PARAM = "OsmLastCompleteDate"

ICON_SIZE = 110
ICON = "../../sunnypilot/selfdrive/assets/offroad/icon_map.png"

# OSMDownloadLocations lives in /dev/shm, which is why a reboot drops a
# download in progress (and why the page warns about it)
_mem_params = None


def mem_params() -> Params:
  global _mem_params
  if _mem_params is None:
    _mem_params = Params("/dev/shm/params") if platform.system() != "Darwin" else ui_state.params
  return _mem_params


def age_text(age: float) -> str:
  """Human "how long ago". Vague at the top end on purpose: a map is old or it
  is not, and the minute does not matter."""
  # A negative age means the clock moved backwards since the write, which it
  # does before NTP lands. Saying nothing is better than "in 3 h".
  if age < 0:
    return ""
  if age < 3600:
    return tr("just now")
  if age < 86400:
    return tr("{} h ago").format(int(age // 3600))
  return tr("{} d ago").format(int(age // 86400))


def osm_region() -> str:
  """The chosen region's name, or "" when none is chosen."""
  params = ui_state.params
  code = params.get("OsmLocationName") or ""
  if not code:
    return ""
  name = params.get("OsmLocationTitle") or code
  state = params.get("OsmStateTitle") or ""
  if code == "US" and state:
    return f"{name}, {state}"
  return name


def param_date(key: str) -> float:
  """A unix-seconds date param, or 0. when it is missing or unreadable."""
  try:
    ts = float(ui_state.params.get(key) or 0)
  except (TypeError, ValueError):
    return 0.
  return ts if ts > 0 else 0.


def osm_date() -> tuple[str, str]:
  """(header, date) for the card: ("updated", "2026-09-12 • 17 d ago") for the last completed download,
  ("requested", ...) when only a request is on record - it may never have finished - or ("updated", "never").
  The header carries the difference so the date line always fits the card."""
  when, header = param_date(COMPLETE_PARAM), tr("updated")
  if when <= 0:
    when, header = param_date("OsmDownloadedDate"), tr("requested")
    if when <= 0:
      return tr("updated"), tr("never")
  day = datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d")
  age = age_text(datetime.datetime.now().timestamp() - when)
  return header, f"{day} • {age}" if age else day


def last_request_incomplete() -> bool:
  """A request newer than the last completed download, with nothing downloading: it was cut short."""
  completed = param_date(COMPLETE_PARAM)
  return completed > 0 and param_date("OsmDownloadedDate") > completed


def osm_status() -> tuple[str, str, str]:
  """(region line, date header, date line)."""
  # There is no region picker on this UI, and sunnylink's settings schema has
  # none either; the region comes from a comma 3/3X or a restored backup.
  region = osm_region() or tr("no region set")
  return (region, *osm_date())


class MapDataInfo(Widget):
  """One card: which data set, and how old it is.

  TWO PAIRS, SHORT HEADERS, wrap_text=False on every label. These are hand
  positioned at fixed offsets in a 180 px box, and UnifiedLabel sizes its rect
  once at construction, so a header wider than max_width wraps and its second
  line overprints the value beneath it (see board.py).
  """

  def __init__(self, header: str, date_header: str, status: Callable[[], tuple[str, str, str]]):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))
    self._status = status

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.name_header = UnifiedLabel(header, 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.name_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                  font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self.date_header = UnifiedLabel(date_header, 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.date_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                  font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self._updated = 0.0
    self.refresh()

  def refresh(self) -> None:
    self._updated = time.monotonic()
    name, date_header, date = self._status()
    self.name_text.set_text(name)
    self.date_header.set_text(date_header)
    self.date_text.set_text(date)

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()

  def _render(self, _):
    self.name_header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.name_header.render()
    self.name_text.set_position(self._rect.x + 20, self._rect.y + 43)
    self.name_text.render()
    self.date_header.set_position(self._rect.x + 20, self._rect.y + 84)
    self.date_header.render()
    self.date_text.set_position(self._rect.x + 20, self._rect.y + 136)
    self.date_text.render()


class UpdateOsmButton(BigButton):
  """Re-download the OSM tiles for the chosen region.

  WHAT IT DOES: writes OsmDbUpdatesCheck, the param the comma 3/3X OSM panel
  writes. mapd_manager sees it within a second, re-requests the region, and
  mapd downloads it in the background (Australia: ~270 MB, 357 tiles).

  OFFROAD ONLY. A download started onroad competes with logging for the
  network and, on a hotspot, is metered data nobody asked for.

  THE GATE IS RE-CHECKED INSIDE THE CONFIRM CALLBACK: the slide-to-confirm can
  sit open across an ignition. The enabled state is set imperatively, every
  tick, for the reason board.py gives (one slot, last writer wins).
  """

  def __init__(self):
    super().__init__(tr("update"), "", gui_app.texture(ICON, 70, 70))
    self.set_click_callback(self._on_click)
    self._updated = 0.0
    self._asked = False        # we wrote the request; waiting for mapd_manager
    self._asked_at = 0.0
    self.refresh()

  # -- gating ---------------------------------------------------------------
  @staticmethod
  def _can_update() -> tuple[bool, str]:
    """(allowed, why not). The reason is the sub-label, because a dead button
    with no explanation is the worst of both."""
    if not ui_state.params.get("OsmLocationName"):
      return False, tr("no region set")
    # is_offroad() is True while driving in OffroadMode; the panda's ignition is not
    if not ui_state.is_offroad() or ui_state.ignition:
      return False, tr("car must be parked")
    return True, ""

  @staticmethod
  def _progress() -> tuple[int, int]:
    raw = ui_state.params.get("OSMDownloadProgress")
    if not isinstance(raw, dict):
      return 0, 0
    try:
      return int(raw.get("downloaded_files", 0)), int(raw.get("total_files", 0))
    except (TypeError, ValueError):
      return 0, 0

  def _downloading(self) -> bool:
    return bool(mem_params().get("OSMDownloadLocations"))

  def _busy(self) -> bool:
    """Requested and not yet picked up, or picked up and still downloading."""
    return self._asked or ui_state.params.get_bool(UPDATE_PARAM) or self._downloading()

  def _on_click(self) -> None:
    allowed, why = self._can_update()
    if not allowed:
      gui_app.push_widget(BigDialog("", why))
      return

    def confirm() -> None:
      ok, _ = self._can_update()
      if not ok or self._busy():
        return
      ui_state.params.put_bool(UPDATE_PARAM, True)
      self._asked = True
      self._asked_at = time.monotonic()
      self.set_value(tr("starting"))
      # The download list is a /dev/shm param: a reboot or power off before it
      # finishes drops it, and the date above has already moved.
      gui_app.push_widget(BigDialog(tr("updating maps"), tr("keep the device on until it finishes")))

    gui_app.push_widget(BigConfirmationDialog(
      tr("slide to\nupdate maps"), gui_app.texture(ICON, ICON_SIZE, ICON_SIZE),
      confirm, exit_on_confirm=True))

  # -- what the sub-label says ----------------------------------------------
  def refresh(self) -> None:
    """In flight outranks finished, and finished outranks idle.

    OSMDownloadProgress is cleared on manager start, so "updated" and "failed"
    only ever describe a download from this boot.
    """
    self._updated = time.monotonic()
    downloading = self._downloading()
    requested = ui_state.params.get_bool(UPDATE_PARAM)
    done, total = self._progress()

    if downloading:
      self._asked = False
      self.set_value(tr("{}/{} tiles").format(done, total) if total > 0 else tr("downloading"))
    elif requested or self._asked:
      # mapd_manager looks once a second; until it does, nothing else says so.
      # Once it has consumed the request, give the download list a few seconds
      # to appear, then stop claiming something is pending.
      self.set_value(tr("starting"))
      if not requested and time.monotonic() - self._asked_at > 10:
        self._asked = False
    elif total > 0 and done >= total:
      self.set_value(tr("updated"))
    elif total > 0:
      self.set_value(tr("failed"))
    elif last_request_incomplete():
      # a download from an earlier boot that never finished (OSMDownloadProgress does not survive a restart)
      self.set_value(tr("incomplete"))
    else:
      allowed, why = self._can_update()
      self.set_value(osm_region() if allowed else why)

    self.set_enabled(self._can_update()[0] and not self._busy())

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()


class MapsLayoutMici(NavScroller):
  # No back_callback: NavWidget pops itself on swipe-down, and a pop_widget
  # callback on top of that popped Settings too (upstream 099143ad9).
  def __init__(self):
    super().__init__()

    self._osm_info = MapDataInfo(tr("osm maps"), tr("updated"), osm_status)
    self._osm_update_btn = UpdateOsmButton()
    self._auto_update_toggle = BigParamControl(tr("update weekly"), AUTO_UPDATE_PARAM,
                                               toggle_callback=self._on_auto_update_toggled)
    self._auto_update_toggle.set_value(tr("parked, on wi-fi"))

    # add_widgets is on the inner _Scroller; self.add_widgets(...) does not exist.
    # A second data set goes here as another (MapDataInfo, its update button) pair.
    self._scroller.add_widgets([self._osm_info, self._osm_update_btn, self._auto_update_toggle])

  @staticmethod
  def _on_auto_update_toggled(checked: bool) -> None:
    if checked:
      body = tr("runs parked and on wi-fi only. a phone hotspot counts as wi-fi unless it is marked metered.")
      gui_app.push_widget(BigDialog(tr("weekly update"), body))

  def show_event(self):
    super().show_event()
    self._osm_info.refresh()
    self._osm_update_btn.refresh()
    # also set from sunnylink; the toggle only reads its param when built
    self._auto_update_toggle.refresh()
