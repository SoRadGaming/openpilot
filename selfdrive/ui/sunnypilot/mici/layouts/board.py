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
import os
import struct
import time
from collections.abc import Callable

import pyray as rl

from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigDialog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher import (
  APP_ID_MAGIC,
  APP_ID_OFFSET,
  EPS_LKAS_APPSLOT_BIN,
)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

# params are files: read them on a tick, not every frame
REFRESH_S = 1.0

REQUEST_PARAM = "EpsLkasFlashRequested"
PROGRESS_PARAM = "EpsLkasFlashProgress"
STATE_PARAM = "EpsLkasFlashState"

ICON_SIZE = 110
ICON = "../../sunnypilot/selfdrive/assets/offroad/icon_software.png"


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
                                  font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.fw_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self.seen_header = UnifiedLabel(tr("last seen"), 48, max_width=max_width, text_color=header_color,
                                    font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.seen_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                  font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

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
                                   font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.uid_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                 font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self.can_header = UnifiedLabel(tr("can update"), 48, max_width=max_width, text_color=header_color,
                                   font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.can_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                 font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

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


class UpdateBoardButton(BigButton):
  """Reflash the gateway board over CAN, from here.

  WHAT THIS BUTTON ACTUALLY DOES: writes one param. pandad's wrapper is
  watching for it, asks the pandad binary to stand down, flashes the board in
  the window where nothing else owns the panda, and restarts it. The UI process
  is untouched throughout, which is why progress can be shown at all.

  THE GATE IS RE-CHECKED INSIDE THE CONFIRM CALLBACK, not only before the
  dialog opens. A full-screen slide-to-confirm can sit open across an ignition
  event, and the device.py precedent makes exactly this point about engagement.
  Between the slide completing and the callback running there is also most of a
  second of dismiss animation, which is plenty of time for the car to become a
  car that is on.

  IMPERATIVE enabled STATE, set every tick. Widget.set_enabled has ONE slot and
  is a plain assignment with no save or restore anywhere, so mixing a callable
  and a bool means whichever came last wins forever. Every in-tree button that
  tracks offroad does it this way, from inside _update_state.
  """

  def __init__(self):
    super().__init__(tr("update firmware"), "", gui_app.texture(ICON, 70, 70))
    self.set_click_callback(self._on_click)
    self._updated = 0.0
    self._asked = False        # we wrote the request; waiting for pandad
    self.refresh()

  # -- gating ---------------------------------------------------------------
  @staticmethod
  def _can_update() -> tuple[bool, str]:
    """(allowed, why not). The reason is shown, because a dead button with no
    explanation is the worst of both."""
    _version, build = board_firmware()
    if not build:
      return False, tr("needs a debugger once")
    if not build.get("bootloader"):
      return False, tr("needs a debugger once")
    if not ui_state.is_offroad():
      return False, tr("not while driving")
    return True, ""

  def _on_click(self) -> None:
    allowed, why = self._can_update()
    if not allowed:
      gui_app.push_widget(BigDialog("", why))
      return

    def confirm() -> None:
      # Re-checked here: the dialog can sit open across an ignition, and the
      # dismiss animation adds most of a second on top.
      ok, _ = self._can_update()
      if not ok or self._busy():
        return
      # Clear the previous run's terminal state, so the sub-label does not show
      # a stale "updated" while this one is pending.
      ui_state.params.put(STATE_PARAM, "")
      ui_state.params.put(PROGRESS_PARAM, "0")
      ui_state.params.put_bool(REQUEST_PARAM, True)
      self._asked = True
      self.set_value(tr("requested"))

    gui_app.push_widget(BigConfirmationDialog(
      tr("slide to\nupdate the gateway"), gui_app.texture(ICON, ICON_SIZE, ICON_SIZE),
      confirm, exit_on_confirm=True, red=True))

  # -- what the sub-label says ----------------------------------------------
  def refresh(self) -> None:
    """A PENDING REQUEST OUTRANKS A FINISHED ONE.

    EpsLkasFlashState is not cleared between updates, so after one flash it
    says "ok <hash>" for the rest of the boot. Testing it first meant the
    second press of the session read that stale terminal state, cleared the
    in-flight flag and re-enabled the button while pandad was still waiting for
    the ./pandad binary to exit - so the button looked idle, and pressing it
    again wrote a request that was already pending.

    The strings are kept short because the sub-label is one line of 322 px at
    36 pt (BigButton._width_hint: 402 minus 2x40 padding). An earlier version
    of this comment claimed "updated b386c2c6" did not fit; measured, it is
    ~312 px and does fit. Short is still right - there is no room for a hash
    AND a word - but the number was made up and is now not.
    """
    self._updated = time.monotonic()
    params = ui_state.params
    state = params.get(STATE_PARAM) or ""
    requested = self._asked or params.get_bool(REQUEST_PARAM)

    if state == "running":
      self._asked = False
      self.set_value(tr("{}%").format(params.get(PROGRESS_PARAM) or "0"))
    elif requested:
      # pandad only looks once a second, and only acts when the ./pandad binary
      # next exits. Saying nothing here reads as a button that did nothing.
      self.set_value(tr("requested"))
    elif state.startswith("ok "):
      self.set_value(tr("updated"))
    elif state.startswith("failed"):
      # The whole reason is in the log and in the state param; one short line
      # cannot carry it.
      self.set_value(tr("failed"))
    else:
      allowed, why = self._can_update()
      if not allowed:
        self.set_value(why)
      else:
        # WHAT IT WOULD INSTALL, not what is running -- the card to the left
        # already says what is running. Without this the button could not tell
        # you whether there was anything to install at all.
        version, _build = board_firmware()
        offer = bundled_firmware()
        if not offer:
          self.set_value(tr("no image"))
        elif offer == version:
          self.set_value(tr("up to date"))
        else:
          self.set_value(tr("to {}").format(offer))

    self.set_enabled(self._can_update()[0] and not self._busy())

  def _busy(self) -> bool:
    """In flight, by any measure the UI can see.

    EpsLkasFlashRequested is included because there is a window - from the
    confirm write until pandad next looks, up to a second, plus however long
    the ./pandad binary takes to exit - where nothing else says anything is
    happening.
    """
    params = ui_state.params
    return (params.get(STATE_PARAM) or "") == "running"         or self._asked or params.get_bool(REQUEST_PARAM)

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()


class BoardLayoutMici(NavScroller):
  def __init__(self, back_callback: Callable):
    super().__init__()
    self.set_back_callback(back_callback)

    self._firmware_info = BoardFirmwareInfo()
    self._identity_info = BoardIdentityInfo()
    self._update_btn = UpdateBoardButton()

    # add_widgets is on the inner _Scroller, and going through it is what
    # re-wraps each widget's touch-valid callback with the scroller's own
    # conditions. self.add_widgets(...) does not exist.
    self._scroller.add_widgets([self._firmware_info, self._identity_info, self._update_btn])

  def show_event(self):
    super().show_event()
    self._firmware_info.refresh()
    self._identity_info.refresh()
    self._update_btn.refresh()


# (hash, mtime) of the bundled image. Keyed on mtime so a git pull that
# replaces the file is picked up without re-reading it every tick.
_bundled_cache: list = ["", -1.0]


def bundled_firmware() -> str:
  """The commit of the image that would be installed, or "".

  READ FROM THE IMAGE ITSELF, at the same offset and with the same magic the
  bootloader checks, so the screen and the board can never disagree about what
  is on offer. Only the first 0x110 bytes are touched -- there is no reason to
  pull 46 KB off disk to answer a question about sixteen of them.

  The image ships inside sunnypilot: there is no download, and no separate
  firmware channel. Updating the board's firmware IS updating sunnypilot.
  """
  try:
    mtime = os.path.getmtime(EPS_LKAS_APPSLOT_BIN)
  except OSError:
    return ""
  if mtime != _bundled_cache[1]:
    _bundled_cache[1] = mtime
    _bundled_cache[0] = ""
    try:
      with open(EPS_LKAS_APPSLOT_BIN, "rb") as fh:
        head = fh.read(APP_ID_OFFSET + 16)
      if len(head) >= APP_ID_OFFSET + 16:
        magic, _origin, git, _flags = struct.unpack(
          "<4I", head[APP_ID_OFFSET:APP_ID_OFFSET + 16])
        if magic == APP_ID_MAGIC:
          _bundled_cache[0] = f"{git:08x}"
    except OSError:
      pass
  return _bundled_cache[0]


_visible_cache: list = [False, 0.0]


def board_page_visible() -> bool:
  """Show the page once the board has ever identified itself.

  Not gated on the car brand: an EPS-LKAS board is a thing you fitted, not a
  thing the fingerprint knows about, and the honest test for "is there one" is
  that one has spoken.

  CACHED ON A TICK. The settings carousel asks this every frame to decide
  whether to draw the row, and ui_state.params has no cache - so the
  uncached version was a file read sixty times a second to answer a question
  that changes once in the life of an installation. car_brand() in vehicle.py
  is cached for exactly the same reason.
  """
  now = time.monotonic()
  if now - _visible_cache[1] > REFRESH_S:
    _visible_cache[1] = now
    _visible_cache[0] = bool(ui_state.params.get("EpsLkasBoardVersion"))
  return _visible_cache[0]
