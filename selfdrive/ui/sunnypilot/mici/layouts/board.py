"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Settings > Gateway, on the small screen. What firmware is on the EPS-LKAS
board, and which board it is.

WHY THIS IS A PAGE AND NOT A LOG LINE. The board is behind the dashboard. Until
now the only way to answer "what is actually running on it" was to take a drive,
pull the route, and decode 0x707 by hand -- which is a fine way to answer the
question once and a terrible way to answer it before deciding whether to reflash.

WHY IT SAYS "last seen". card is only_onroad and the board sends its identity
once a minute, so nothing here is live: it is what the board said the last time
it was driven, latched into params by card.publish_board_firmware(). Labelling
it honestly is the whole difference between a useful page and a misleading one --
a board that has been stood down to the read-only image transmits nothing at
all, and would otherwise show its old hash forever with no hint that it is
stale.
"""
import json
import time
from collections.abc import Callable

import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

# params are files: read them on a tick, not every frame
REFRESH_S = 1.0


def board_firmware() -> tuple[str, dict]:
  """The latched firmware identity, or ("", {}) if the board has never spoken."""
  version = ui_state.params.get("EpsLkasBoardVersion") or ""
  raw = ui_state.params.get("EpsLkasBoardBuild")
  build: dict = {}
  if raw:
    try:
      build = raw if isinstance(raw, dict) else json.loads(raw)
    except (ValueError, TypeError):
      build = {}
  return version, build


def board_last_seen() -> str:
  """Human "how long ago", or "" if we have never heard from it.

  Deliberately vague at the top end. The exact minute matters when you have just
  flashed and want confirmation; after a day it only matters that it is old.
  """
  raw = ui_state.params.get("EpsLkasBoardSeenAt")
  if not raw:
    return ""
  try:
    age = time.time() - int(raw)
  except (ValueError, TypeError):
    return ""
  # A negative age means the device clock moved backwards between the write and
  # now -- which it does, every boot, before NTP lands. Saying nothing is better
  # than saying "in 3 hours".
  if age < 0:
    return tr("unknown")
  if age < 90:
    return tr("just now")
  if age < 3600:
    return tr("{} min ago").format(int(age // 60))
  if age < 86400:
    return tr("{} h ago").format(int(age // 3600))
  return tr("{} d ago").format(int(age // 86400))


class BoardFirmwareInfo(Widget):
  """Two header/value pairs, laid out like HondaLearnedInfo and DeviceInfoLayoutMici."""

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.fw_header = UnifiedLabel(tr("board firmware"), 48, max_width=max_width, text_color=header_color,
                                  font_weight=FontWeight.DISPLAY)
    self.fw_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                font_weight=FontWeight.ROMAN, scroll=True)

    self.seen_header = UnifiedLabel(tr("last seen"), 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY)
    self.seen_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                  font_weight=FontWeight.ROMAN, scroll=True)

    self._updated = 0.0
    self.refresh()

  def refresh(self) -> None:
    self._updated = time.monotonic()
    version, build = board_firmware()

    if not version:
      self.fw_text.set_text(tr("never seen"))
      self.seen_text.set_text(tr("drive once to read it"))
      return

    # The dirty flag is not decoration. It means 0x707's hash names a commit the
    # image was NOT built from, so quoting that hash at anyone is misleading
    # unless it is said out loud.
    marks = []
    if build.get("dirty"):
      marks.append(tr("dirty"))
    if build.get("readOnly"):
      marks.append(tr("stood down"))
    self.fw_text.set_text(version + (f"  ({', '.join(marks)})" if marks else ""))

    seen = board_last_seen() or tr("unknown")
    # Absent build flags mean firmware older than 2026-09-22, which is exactly
    # the firmware that has no bootloader -- so say the useful thing, not "no".
    if not build:
      seen += tr("  · pre-bootloader")
    elif not build.get("bootloader"):
      seen += tr("  · no bootloader")
    self.seen_text.set_text(seen)

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()

  def _render(self, _):
    self.fw_header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.fw_header.render()

    self.fw_text.set_position(self._rect.x + 20, self._rect.y + 68 - 25)
    self.fw_text.render()

    self.seen_header.set_position(self._rect.x + 20, self._rect.y + 114 - 30)
    self.seen_header.render()

    self.seen_text.set_position(self._rect.x + 20, self._rect.y + 161 - 25)
    self.seen_text.render()


class BoardIdentityInfo(Widget):
  """Which physical board, and how it is built. The second card."""

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.uid_header = UnifiedLabel(tr("board id"), 48, max_width=max_width, text_color=header_color,
                                   font_weight=FontWeight.DISPLAY)
    self.uid_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                 font_weight=FontWeight.ROMAN, scroll=True)

    self.can_header = UnifiedLabel(tr("can update"), 48, max_width=max_width, text_color=header_color,
                                   font_weight=FontWeight.DISPLAY)
    self.can_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                 font_weight=FontWeight.ROMAN, scroll=True)

    self._updated = 0.0
    self.refresh()

  def refresh(self) -> None:
    self._updated = time.monotonic()
    _, build = board_firmware()

    self.uid_text.set_text(build.get("uid") or tr("unknown"))

    # This is the question the page exists to answer before any update button
    # does: BUILD_BOOTLOADER is a run-time check of the reset vector at
    # 0x08000000, so it is the difference between "can be reflashed from here"
    # and "needs a one-time visit with a debugger".
    if not build:
      self.can_text.set_text(tr("no — needs SWD once"))
    elif build.get("bootloader"):
      self.can_text.set_text(tr("yes — over CAN"))
    else:
      self.can_text.set_text(tr("no — needs SWD once"))

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()

  def _render(self, _):
    self.uid_header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.uid_header.render()

    self.uid_text.set_position(self._rect.x + 20, self._rect.y + 68 - 25)
    self.uid_text.render()

    self.can_header.set_position(self._rect.x + 20, self._rect.y + 114 - 30)
    self.can_header.render()

    self.can_text.set_position(self._rect.x + 20, self._rect.y + 161 - 25)
    self.can_text.render()


class BoardLayoutMici(NavScroller):
  def __init__(self, back_callback: Callable):
    super().__init__()
    self.set_back_callback(back_callback)

    self._firmware_info = BoardFirmwareInfo()
    self._identity_info = BoardIdentityInfo()

    # add_widgets is on the inner _Scroller, and going through it is what
    # re-wraps each widget's touch-valid callback with the scroller's own
    # conditions. self.add_widgets(...) does not exist.
    self._scroller.add_widgets([self._firmware_info, self._identity_info])

  def show_event(self):
    super().show_event()
    self._firmware_info.refresh()
    self._identity_info.refresh()


def board_page_visible() -> bool:
  """Show the page once the board has ever identified itself.

  Not gated on the car brand: an EPS-LKAS board is a thing you fitted, not a
  thing the fingerprint knows about, and the honest test for "is there one" is
  that one has spoken.
  """
  return bool(ui_state.params.get("EpsLkasBoardVersion"))
