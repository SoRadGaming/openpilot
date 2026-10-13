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
it was driven, latched into params by card's stage_board_firmware() and
write_board_firmware(). Labeling
it honestly is the whole difference between a useful page and a misleading one --
a board that has been stood down to the read-only image transmits nothing at
all, and would otherwise show its old hash forever with no hint that it is
stale.

TWO BOARDS (FORK(BRAKE-LAMP-RELAY), BLR-v1 DESIGN E.3). The BLR-v1 brake-lamp
board gets its own card and its own update button beside the EPS-LKAS board's,
shown once it has spoken (BlrBoardVersion), from its own Blr* params - card's
blr_board.py writes them from a drive, the hook from an update. The EPS pair is
exactly what it was. The two updates are separate in every way but one: only one
can ever be in flight, because each button's _busy() is both boards'.
"""
import json
import os
import struct
import time
from typing import NamedTuple

import pyray as rl

from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.mici.widgets.dialog import BigConfirmationDialog, BigDialog, BigDialogBase
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher import (
  APP_ID_OFFSET,
  BLR,
  BLR_VARIANT_NAMES,
  CANCELLED,
  ENGINE_RUNNING,
  EPS,
  IGNITION_OFF,
  NO_ENGINE_DATA,
  Target,
  check_app_slot_image,
  image_identity,
)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.widgets.scroller import NavScroller

# params are files: read them on a tick, not every frame
REFRESH_S = 1.0
# ... except the update screen's, which shows a countdown: four reads a second
PHASE_REFRESH_S = 0.25

REQUEST_PARAM = "EpsLkasFlashRequested"
PROGRESS_PARAM = "EpsLkasFlashProgress"
STATE_PARAM = "EpsLkasFlashState"
PHASE_PARAM = "EpsLkasFlashPhase"
CANCEL_PARAM = "EpsLkasFlashCancel"


class BoardKeys(NamedTuple):
  """One board's params, as the page reads and writes them. The hook has the other half of this table
  (eps_lkas_hook.KEYS), and test_eps_lkas_hook.py holds the two to the same names: the request the page writes is
  the one the hook watches."""
  version: str
  build: str
  seen: str
  request: str
  progress: str
  state: str
  phase: str
  cancel: str


EPS_KEYS = BoardKeys(version="EpsLkasBoardVersion", build="EpsLkasBoardBuild", seen="EpsLkasBoardSeenAt",
                     request=REQUEST_PARAM, progress=PROGRESS_PARAM, state=STATE_PARAM, phase=PHASE_PARAM,
                     cancel=CANCEL_PARAM)
BLR_KEYS = BoardKeys(version="BlrBoardVersion", build="BlrBoardBuild", seen="BlrBoardSeenAt",
                     request="BlrFlashRequested", progress="BlrFlashProgress", state="BlrFlashState",
                     phase="BlrFlashPhase", cancel="BlrFlashCancel")
# the flasher's targets compare by identity (Target is eq=False), so they key this as themselves
BOARD_KEYS = {EPS: EPS_KEYS, BLR: BLR_KEYS}
# the lamp board's last drive, as card's blr_board.py writes it: {"faults", "faultNames", "latch", "latchName", ...}
LAST_DRIVE_PARAM = "BlrLastDrive"

ICON_SIZE = 110
ICON = "../../sunnypilot/selfdrive/assets/offroad/icon_software.png"


def board_firmware(keys: BoardKeys = EPS_KEYS) -> tuple[str, dict]:
  """The latched firmware identity, or ("", {}) if the board has never spoken."""
  version = ui_state.params.get(keys.version) or ""
  raw = ui_state.params.get(keys.build)
  build: dict = {}
  if raw:
    try:
      build = raw if isinstance(raw, dict) else json.loads(raw)
    except (ValueError, TypeError):
      build = {}
  return version, build


def board_last_seen(keys: BoardKeys = EPS_KEYS) -> str:
  """Human "how long ago", or "" if we have never heard from it.

  Deliberately vague at the top end. The exact minute matters when you have just
  flashed and want confirmation; after a day it only matters that it is old.
  """
  raw = ui_state.params.get(keys.seen)
  if not raw:
    return ""
  try:
    age = time.time() - int(raw)  # noqa: TID251 - the SeenAt params are wall-clock time, written on an earlier boot
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


def board_variant(build: dict, target: Target = EPS) -> str:
  """The installed image's VARIANT by name ("shadow", "live", ...), or "" for a board whose marker has none (the
  EPS). The lamp board's Build param holds the number (0x627), as card writes it."""
  if target.allowed_variants is None:
    return ""
  v = build.get("variant")
  return BLR_VARIANT_NAMES.get(v, "") if isinstance(v, int) else ""


class BoardFirmwareInfo(Widget):
  """One card: which firmware, and which board.

  TWO PAIRS, SHORT HEADERS. These are hand-positioned at fixed offsets in a
  180 px box and UnifiedLabel sizes its rect once at construction, so a header
  wider than max_width wraps and its second line lands on the value beneath it.
  "board firmware" was 343 px against a 340 px limit and did exactly that.
  wrap_text=False is the belt; short headers are the braces.

  "can update" used to be a third row on a second card. It is gone: every board
  that ships has a bootloader, so the row said "yes" forever. The FLAG still
  gates the update button - a board stood down to a standalone image has no
  bootloader, and the button must not offer what it cannot do.
  """

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 360, 180))

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.fw_header = UnifiedLabel(tr("firmware"), 48, max_width=max_width, text_color=header_color,
                                  font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.fw_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self.id_header = UnifiedLabel(tr("board id"), 48, max_width=max_width, text_color=header_color,
                                  font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.id_text = UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                                font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False)

    self._updated = 0.0
    self.refresh()

  def refresh(self) -> None:
    self._updated = time.monotonic()
    version, build = board_firmware()

    if not version:
      self.fw_text.set_text(tr("never seen"))
      self.id_text.set_text(tr("drive once to read it"))
      return

    # The age belongs with the hash, because a hash with no age invites you to
    # believe it is current. card is only_onroad and this page is offroad, so
    # nothing here is ever live - and in the one state the update runs in
    # (always-offroad, ignition on) card is not running at all.
    age = board_last_seen()
    marks = []
    if build.get("dirty"):
      marks.append(tr("dirty"))
    if build.get("readOnly"):
      marks.append(tr("stood down"))
    txt = version + (f" • {age}" if age else "")
    self.fw_text.set_text(txt + (f" ({', '.join(marks)})" if marks else ""))

    uid = build.get("uid") or tr("unknown")
    # No bootloader is worth saying here, because it is why the button refuses.
    self.id_text.set_text(uid + ("" if build.get("bootloader") else tr(" • no bootloader")))

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()

  def _render(self, _):
    self.fw_header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.fw_header.render()
    self.fw_text.set_position(self._rect.x + 20, self._rect.y + 43)
    self.fw_text.render()
    self.id_header.set_position(self._rect.x + 20, self._rect.y + 84)
    self.id_header.render()
    self.id_text.set_position(self._rect.x + 20, self._rect.y + 136)
    self.id_text.render()


def last_drive_text(record) -> str:
  """What BlrLastDrive says about the lamp board's last drive: "ok", its fault names, or "LATCHED <reason>" - a
  latch first, because it is what kept the lamp output off for the rest of that power cycle. Pure, and never raises:
  the record is a param, and a page that throws on a bad one is worse than one that says "unknown"."""
  if not isinstance(record, dict):
    return tr("unknown")
  try:
    latch = int(record.get("latch") or 0)
    if latch:
      return tr("LATCHED {}").format(record.get("latchName") or latch)
    names = [str(n) for n in (record.get("faultNames") or [])]
    if names:
      return ", ".join(names)
    faults = int(record.get("faults") or 0)
    return f"{faults:#04x}" if faults else tr("ok")
  except (ValueError, TypeError):
    return tr("unknown")


def lamp_board_lines(version: str, build: dict, age: str, record) -> tuple[str, str, str]:
  """The lamp board's card, from its params. Pure.

    <hash> • shadow|live • <age> (dirty)
    board id <uid24> • boot <bootgit>          (• no bootloader, • not dual-bank: why its button refuses)
    last drive: ok | <fault names> | LATCHED <reason>

  What 0x62F has not said yet is left out ("board id unknown"), never read as "no": card adds its keys to
  BlrBoardBuild only once the frame has arrived."""
  if not version:
    return tr("never seen"), tr("drive once to read it"), ""
  variant = board_variant(build, BLR)
  fw = " • ".join(p for p in (version, variant, age) if p) + (f" ({tr('dirty')})" if build.get("dirty") else "")
  uid = build.get("uid")
  ident = tr("board id {}").format(uid) if uid else tr("board id unknown")
  if "bootloader" in build:
    if not build.get("bootloader"):
      ident += tr(" • no bootloader")
    elif build.get("bootGit"):
      ident += " • " + tr("boot {}").format(build["bootGit"])
  if build.get("dualBank") is False:
    ident += tr(" • not dual-bank")
  return fw, ident, tr("last drive: {}").format(last_drive_text(record))


class LampBoardInfo(Widget):
  """The BLR-v1 lamp board's card: one header, three lines (lamp_board_lines()).

  Hand-positioned like BoardFirmwareInfo, with the same rules: wrap_text=False on every label (a wrapped line would
  print over the one beneath it) and scroll=True on the values, which can run past the card. WIDER than the EPS's
  card, 520 px, so the usual lines stand still: "1a2b3c4d • shadow • 12 min ago" is 481 px at 32 pt and
  "board id a5c3e1 • boot b28113" 445, against 340 in a 360 card. Nothing here is live either: card writes the
  params on a drive, and this page is offroad."""
  W = 520

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, self.W, 180))

    header_color = rl.Color(255, 255, 255, int(255 * 0.9))
    value_color = rl.Color(255, 255, 255, int(255 * 0.9 * 0.65))
    max_width = int(self._rect.width - 20)

    self.header = UnifiedLabel(tr("lamp board"), 48, max_width=max_width, text_color=header_color,
                               font_weight=FontWeight.DISPLAY, wrap_text=False)
    self.lines = [UnifiedLabel("", 32, max_width=max_width, text_color=value_color,
                               font_weight=FontWeight.ROMAN, scroll=True, wrap_text=False) for _ in range(3)]

    self._updated = 0.0
    self.refresh()

  def refresh(self) -> None:
    self._updated = time.monotonic()
    version, build = board_firmware(BLR_KEYS)
    record = ui_state.params.get(LAST_DRIVE_PARAM) if version else None
    if isinstance(record, str):
      try:
        record = json.loads(record)
      except ValueError:
        record = None
    for label, text in zip(self.lines, lamp_board_lines(version, build, board_last_seen(BLR_KEYS) if version else "",
                                                        record), strict=True):
      label.set_text(text)

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()

  def _render(self, _):
    self.header.set_position(self._rect.x + 20, self._rect.y - 10)
    self.header.render()
    for i, label in enumerate(self.lines):
      label.set_position(self._rect.x + 20, self._rect.y + 43 + 44 * i)
      label.render()


def flash_view(state: str, phase: dict | None, requested: bool, cancel_sent: bool,
               target: Target = EPS) -> tuple[str, str, int | None, bool]:
  """(title, line, write %, Cancel shown) of the update screen, from the params. Pure.

  The phases are the flasher's (run_flash): check, baseline, click, stock (the
  countdown, its seconds in "left"), erase, write ("pct"), install, restart; the
  hook writes the terminal state - "ok <hash>", "failed: why", "cancelled" - to
  EpsLkasFlashState. Cancel is offered only where the flasher takes it ("cancel"
  in the phase: before the knock, and in the countdown - ABORT before anything is
  erased), and never again once pressed. A Cancel pressed is "canceling" until
  the flasher answers; if the erase began first (the flasher reads it once more
  right before BEGIN, so only a press in that last instant), the screen says it
  was too late - never "canceling" while bank 2 is being written.

  The board's own words (target.words, the flasher's) name the knock, the
  countdown and the line under "done": the EPS's relays to stock wiring and its
  "turn the car off, then on", the lamp board's lamp output off - the brake lamps
  pedal-only - and nothing under "done". Every other word is both boards'."""
  w = target.words
  if state.startswith("ok"):
    return tr("done"), tr(w["done_line"]), None, False
  if state == CANCELLED:
    return tr("cancelled"), tr("the board keeps its firmware"), None, False
  if state.startswith("failed"):
    why = state.split(":", 1)[1].strip() if ":" in state else ""
    if why == ENGINE_RUNNING:
      return tr("engine on"), tr("turn the engine off - leave the ignition on - then update"), None, False
    if why == IGNITION_OFF:
      return tr("ignition off"), tr("turn the ignition on, engine off, then update"), None, False
    if why == NO_ENGINE_DATA:
      return tr("no engine data"), tr("the car bus is quiet - ignition on, engine off, then update"), None, False
    return tr("failed"), why, None, False
  if state != "running":
    if requested:
      if cancel_sent:
        return tr("starting"), tr("canceling"), None, False
      return tr("starting"), tr("stopping the car link"), None, True
    return "", "", None, False
  ph = phase if isinstance(phase, dict) else {}
  p = ph.get("p", "check")
  can = bool(ph.get("cancel")) and not cancel_sent
  if p == "check":
    view = (tr("checking"), tr("parked, engine off"))
  elif p == "baseline":
    view = (tr("checking"), tr("listening to the car"))
  elif p == "click":
    view = (tr(w["click"]), tr(w["click_line"]))
  elif p == "stock":
    view = (tr(w["stock"]), tr("updating in {}").format(int(ph.get("left") or 1)))
  elif p == "erase":
    view = (tr("erasing"), tr("making room for the new image"))
  elif p == "write":
    pct = int(ph.get("pct") or 0)
    return tr("writing {}%").format(pct), tr("too late to cancel") if cancel_sent else tr("keep the car parked"), pct, False
  elif p == "install":
    view = (tr("installing"), tr("keep the ignition on"))
  elif p == "restart":
    view = (tr("restarting the board"), tr("watching it start"))
  else:
    view = (tr("updating"), "")
  if cancel_sent:
    # from the erase on the update runs to the end: say so, never "canceling"
    late = p in ("erase", "install", "restart")
    return view[0], tr("too late to cancel") if late else tr("canceling"), None, False
  return view[0], view[1], None, can


def flash_countdown(state: str, phase: dict | None, cancel_sent: bool) -> int | None:
  """The countdown's seconds while it runs (the 'stock' phase), for the screen to draw large; None otherwise."""
  if state != "running" or cancel_sent or not isinstance(phase, dict) or phase.get("p") != "stock":
    return None
  return int(phase.get("left") or 1)


def _fit_size(text: str, weight: FontWeight, size: int, max_w: float, min_size: int) -> int:
  """The largest size from `size` down to `min_size` at which text fits max_w on one line."""
  while size > min_size and measure_text_cached(gui_app.font(weight), text, size).x > max_w:
    size -= 2
  return size


def _wrap(text: str, weight: FontWeight, size: int, max_w: float, max_lines: int, rest_w: float | None = None) -> list[str]:
  """Word-wrapped to max_w (rest_w for the lines after the first), at most max_lines (the last one cut with '...')."""
  lines: list[str] = []
  cur = ""
  for word in text.split():
    t = f"{cur} {word}".strip()
    room = max_w if not lines or rest_w is None else rest_w
    if cur and measure_text_cached(gui_app.font(weight), t, size).x > room:
      lines.append(cur)
      cur = word
    else:
      cur = t
  if cur:
    lines.append(cur)
  if len(lines) > max_lines:
    lines = lines[:max_lines]
    last = lines[-1]
    room = max_w if len(lines) == 1 or rest_w is None else rest_w
    while last and measure_text_cached(gui_app.font(weight), last + "...", size).x > room:
      last = last[:-1]
    lines[-1] = last.rstrip() + "..."
  return lines


class FlashCancelButton(Widget):
  """The update screen's Cancel: a pill, bottom right."""
  W, H = 156, 64

  def __init__(self, on_click):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, self.W, self.H))
    self.set_click_callback(on_click)

  def _render(self, rect: rl.Rectangle):
    pressed = self.is_pressed
    rl.draw_rectangle_rounded(rect, 1.0, 16, rl.Color(255, 255, 255, int(255 * (0.35 if pressed else 0.2))))
    font = gui_app.font(FontWeight.BOLD)
    txt = tr("cancel")
    sz = measure_text_cached(font, txt, 36)
    rl.draw_text_ex(font, txt, rl.Vector2(rect.x + (rect.width - sz.x) / 2, rect.y + (rect.height - sz.y) / 2), 36, 0,
                    rl.Color(255, 255, 255, int(255 * 0.9)))


class GatewayFlashDialog(BigDialogBase):
  """The update, on screen (push9, owner 2026-10-13: "countdown add that").

  Full screen, opened by the confirm. Everything here is read from params on a
  PHASE_REFRESH_S tick - the flasher runs in pandad's process, and params are the
  only thing both sides share. It never drives the update: swiping it away leaves
  the update running, and the button's sub-label goes on telling it.

  The order the owner sees: checking (parked, engine off) -> relays to stock (the
  click) -> relays in stock, updating in 5..1 (Cancel = ABORT, the board keeps
  its firmware) -> erasing -> writing x% -> installing -> restarting the board ->
  done, with "turn the car off, then on, before driving".

  For the lamp board (target=BLR) the same screen, from its own params and in its
  own words: lamp output off (brake lamps pedal-only) -> lamp output off, updating
  in 5..1 -> ... -> done."""
  PAD = 30
  TITLE_SIZE = 64
  LINE_SIZE = 36
  COUNT_SIZE = 96   # the countdown's seconds, beside "updating in"

  def __init__(self, target: Target = EPS):
    super().__init__()
    self._target = target
    self._keys = BOARD_KEYS[target]
    self._cancel = self._child(FlashCancelButton(self._on_cancel))
    self._cancel_sent = False
    self._next = 0.0
    self._view: tuple[str, str, int | None, bool] = ("", "", None, False)
    self._count: int | None = None
    self.refresh()

  def _on_cancel(self) -> None:
    if not self._view[3] or self._cancel_sent:
      return
    ui_state.params.put_bool(self._keys.cancel, True)
    self._cancel_sent = True
    self.refresh()

  def refresh(self) -> None:
    self._next = time.monotonic() + PHASE_REFRESH_S
    params = ui_state.params
    phase = params.get(self._keys.phase)
    if isinstance(phase, str):
      try:
        phase = json.loads(phase)
      except ValueError:
        phase = None
    state = params.get(self._keys.state) or ""
    view = flash_view(state, phase, params.get_bool(self._keys.request), self._cancel_sent, self._target)
    if not view[0] and self._view[0]:
      # the hook clears the request a moment before it writes "running": keep
      # the last view for that tick rather than flash an empty screen
      return
    self._view = view
    self._count = flash_countdown(state, phase, self._cancel_sent)

  def _update_state(self):
    super()._update_state()
    if time.monotonic() >= self._next:
      self.refresh()

  def _render(self, rect: rl.Rectangle):
    title, line, pct, can = self._view
    x, w = rect.x + self.PAD, rect.width - 2 * self.PAD
    white = rl.Color(255, 255, 255, int(255 * 0.9))
    grey = rl.Color(255, 255, 255, int(255 * 0.6))
    tfont = gui_app.font(FontWeight.DISPLAY)
    tsize = _fit_size(title, FontWeight.DISPLAY, self.TITLE_SIZE, w, 40)
    rl.draw_text_ex(tfont, title, rl.Vector2(x, rect.y + 28), tsize, 0, white)
    lfont = gui_app.font(FontWeight.ROMAN)
    y = rect.y + 28 + tsize + 14
    if self._count is not None:
      # the countdown, large, under "updating in" - Cancel beside it, bottom right
      rl.draw_text_ex(lfont, tr("updating in"), rl.Vector2(x, y), self.LINE_SIZE, 0, grey)
      rl.draw_text_ex(gui_app.font(FontWeight.DISPLAY), str(self._count), rl.Vector2(x - 4, y + self.LINE_SIZE - 8),
                      self.COUNT_SIZE, 0, white)
    else:
      # the second line keeps clear of Cancel, bottom right; the first runs the whole width
      rest_w = w - (FlashCancelButton.W + 16 if can else 0)
      for ln in _wrap(line, FontWeight.ROMAN, self.LINE_SIZE, w, 2, rest_w):
        rl.draw_text_ex(lfont, ln, rl.Vector2(x, y), self.LINE_SIZE, 0, grey)
        y += self.LINE_SIZE + 4
    if pct is not None:
      bar = rl.Rectangle(x, rect.y + rect.height - 52, w, 16)
      rl.draw_rectangle_rounded(bar, 1.0, 8, rl.Color(255, 255, 255, int(255 * 0.15)))
      if pct > 0:
        rl.draw_rectangle_rounded(rl.Rectangle(bar.x, bar.y, max(bar.height, w * min(pct, 100) / 100), bar.height),
                                  1.0, 8, rl.Color(255, 255, 255, int(255 * 0.9)))
    if can:
      self._cancel.render(rl.Rectangle(rect.x + rect.width - self.PAD - FlashCancelButton.W,
                                       rect.y + rect.height - 18 - FlashCancelButton.H,
                                       FlashCancelButton.W, FlashCancelButton.H))


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

  ONE PER BOARD (target): the EPS's, titled "update", and the lamp board's,
  titled "lamp board" - each with its own params and its own gate, and each
  _busy() while EITHER board's update is in flight (set_peers()).
  """

  def __init__(self, target: Target = EPS):
    super().__init__(tr(target.words["title"]), "", gui_app.texture(ICON, 70, 70))
    self._target = target
    self._keys = BOARD_KEYS[target]
    # the page's update buttons, this one among them: _busy() is all of theirs (BoardLayoutMici sets it)
    self._peers: list[UpdateBoardButton] = [self]
    self.set_click_callback(self._on_click)
    self._updated = 0.0
    self._asked = False        # we wrote the request; waiting for pandad
    self.refresh()

  def set_peers(self, peers: list["UpdateBoardButton"]) -> None:
    self._peers = list(peers)

  # -- gating ---------------------------------------------------------------
  def _can_update(self) -> tuple[bool, str]:
    """(allowed, why not). The reason is shown, because a dead button with no
    explanation is the worst of both."""
    version, build = board_firmware(self._keys)
    lamp = self._target is BLR
    # THE LAMP BOARD'S 0x62F has not been heard (it follows 0x627 within a second): whether it has a bootloader is
    # not known, and an absent key is never "no" - so neither "needs SWD once" nor an offer.
    if lamp and build and "bootloader" not in build:
      return False, tr("drive once")
    if not build or not build.get("bootloader"):
      return False, tr("needs SWD once")
    # Its bootloader refuses BEGIN on a single-bank flash (NAK 9), and the option byte is an SWD job.
    if lamp and not build.get("dualBank"):
      return False, tr("not dual-bank")

    # NOTHING TO INSTALL IS A REASON TO BE DISABLED, and it goes before the
    # ignition and offroad tests because it is the more useful thing to say to
    # someone standing at a parked car.
    #
    # This was deliberately left ENABLED at first, on the reasoning that
    # reinstalling is a legitimate recovery action. That reasoning does not
    # hold: if the board is reporting this hash then it is running this image
    # and there is nothing to recover from - a board that had actually failed
    # would be reporting a different version, or none. What the live button
    # really offered was a pointless reflash of the gateway that steers the
    # car, one press away, with a success message still on screen.
    #
    # The lamp board's shadow and live images of one commit share a hash, so
    # for it "up to date" is the hash AND the variant.
    offer, offer_variant = bundled_offer(self._target)
    if not offer:
      return False, tr("no image")
    if offer == version and offer_variant == board_variant(build, self._target):
      return False, tr("up to date")

    # IGNITION BEFORE OFFROAD, and the order is the whole point.
    #
    # is_offroad() is `not started`, and started is `deviceState.started and
    # ignition` - so it is TRUE for both "ignition off" and "ignition on with
    # Always Offroad". Those are opposite situations: the second is the state
    # this update is designed for, and the first is one where the board has no
    # 12 V and the bus is dead, so the flash can only fail with "no HELLO".
    # Testing offroad alone enabled the button in the one state guaranteed not
    # to work.
    #
    # ui_state.ignition is latched - it is only reassigned while pandaStates
    # is arriving and the panda type is known, and never cleared if the panda
    # goes away. Upstream gates on it the same way (device.py), and a stale
    # reading costs a clear failure message rather than anything worse.
    if not ui_state.ignition:
      return False, tr("ignition off")
    if not ui_state.is_offroad():
      return False, tr("use always offroad")
    return True, ""

  def _offer_text(self) -> str:
    """The "to <hash>" sub-label: WHAT IT WOULD INSTALL. The lamp board's says which variant, on a second line -
    "to 1a2b3c4d (shadow)" is 366 px against the sub-label's 322."""
    offer, variant = bundled_offer(self._target)
    return tr("to {}").format(offer) + (f"\n({tr(variant)})" if variant else "")

  def _on_click(self) -> None:
    allowed, why = self._can_update()
    if not allowed:
      gui_app.push_widget(BigDialog("", why))
      return

    keys = self._keys

    def confirm() -> None:
      # Re-checked here: the dialog can sit open across an ignition, and the
      # dismiss animation adds most of a second on top.
      ok, _ = self._can_update()
      if not ok or self._busy():
        return
      # Clear the previous run's terminal state, so the sub-label does not show
      # a stale "updated" while this one is pending.
      ui_state.params.put(keys.state, "")
      ui_state.params.put(keys.progress, "0")
      # a Cancel left over from an earlier run must not cancel this one, and the
      # screen must not open on the last run's phase
      ui_state.params.put_bool(keys.cancel, False)
      ui_state.params.put(keys.phase, {"p": "requested", "cancel": True})
      ui_state.params.put_bool(keys.request, True)
      self._asked = True
      self.set_value(tr("requested"))
      # the update on screen: phases, the countdown, Cancel (push9)
      gui_app.push_widget(GatewayFlashDialog(self._target))

    # ENGINE OFF, IGNITION ON (owner, 2026-10-13). Said here, before the slide,
    # because offroad the page has no CAN to check it with: the flasher checks
    # 0x17C before it sends a frame, and the screen then says why it stopped.
    gui_app.push_widget(BigConfirmationDialog(
      tr("engine off\nslide to update"), gui_app.texture(ICON, ICON_SIZE, ICON_SIZE),
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
    keys = self._keys
    state = params.get(keys.state) or ""
    if self._asked and state not in ("", "running") and not params.get_bool(keys.request):
      # TAKEN AND ALREADY OVER. confirm() wrote "" before the request, so any other state is this run's outcome -
      # one that ended inside a refresh tick (dropped onroad, a refusal at the gate) without ever being seen as
      # "running", the only place _asked used to clear. Kept, it would say "requested" for good and, with two
      # boards, hold BOTH buttons busy.
      self._asked = False
    requested = self._asked or params.get_bool(keys.request)

    if state == "running":
      self._asked = False
      phase = params.get(keys.phase)
      if isinstance(phase, dict) and phase.get("p") == "stock":
        self.set_value(tr("in {}").format(int(phase.get("left") or 1)))   # the countdown, with the screen swiped away
      else:
        self.set_value(tr("{}%").format(params.get(keys.progress) or "0"))
    elif requested:
      # pandad only looks once a second, and only acts when the ./pandad binary
      # next exits. Saying nothing here reads as a button that did nothing.
      self.set_value(tr("requested"))
    elif state.startswith("ok "):
      self.set_value(tr("updated"))
    elif state == CANCELLED:
      self.set_value(tr("cancelled"))
    elif state.startswith("failed"):
      # The whole reason is in the log and in the state param; one short line
      # cannot carry it.
      self.set_value(tr("failed"))
    else:
      # WHAT IT WOULD INSTALL, not what is running -- the card beside it
      # already says what is running. _can_update() carries the reasons it
      # cannot, "up to date" among them.
      allowed, why = self._can_update()
      if allowed and self._busy():
        # the other board's update is in flight, and only one ever runs
        self.set_value(tr("busy"))
      else:
        self.set_value(self._offer_text() if allowed else why)

    self.set_enabled(self._can_update()[0] and not self._busy())

  def _busy(self) -> bool:
    """In flight, by any measure the UI can see - on EITHER board, so only one
    update can ever be started.

    EpsLkasFlashRequested is included because there is a window - from the
    confirm write until pandad next looks, up to a second, plus however long
    the ./pandad binary takes to exit - where nothing else says anything is
    happening. The same goes for BlrFlashRequested, and for each button's own
    "we asked" (the peers').
    """
    params = ui_state.params
    for keys in BOARD_KEYS.values():
      if (params.get(keys.state) or "") == "running" or params.get_bool(keys.request):
        return True
    return any(b._asked for b in self._peers)

  def _update_state(self):
    if time.monotonic() - self._updated > REFRESH_S:
      self.refresh()


class BoardLayoutMici(NavScroller):
  # No back_callback: NavWidget pops itself on swipe-down, and a pop_widget
  # callback on top of that popped Settings too (upstream 099143ad9).
  def __init__(self):
    super().__init__()

    self._firmware_info = BoardFirmwareInfo()
    self._update_btn = UpdateBoardButton()
    # FORK(BRAKE-LAMP-RELAY): the BLR-v1 lamp board's card and button, after the EPS pair, shown once it has spoken.
    # The two buttons are each other's peers: one update at a time, across both boards.
    self._lamp_info = LampBoardInfo()
    self._lamp_update_btn = UpdateBoardButton(BLR)
    self._lamp_info.set_visible(lamp_board_seen)
    self._lamp_update_btn.set_visible(lamp_board_seen)
    for btn in (self._update_btn, self._lamp_update_btn):
      btn.set_peers([self._update_btn, self._lamp_update_btn])

    # add_widgets is on the inner _Scroller, and going through it is what
    # re-wraps each widget's touch-valid callback with the scroller's own
    # conditions. self.add_widgets(...) does not exist.
    self._scroller.add_widgets([self._firmware_info, self._update_btn, self._lamp_info, self._lamp_update_btn])

  def show_event(self):
    super().show_event()
    self._firmware_info.refresh()
    self._update_btn.refresh()
    self._lamp_info.refresh()
    self._lamp_update_btn.refresh()


# image path -> (mtime, commit, variant) of a board's bundled image. Keyed on mtime so a git pull that replaces the
# file is picked up without re-reading it every tick.
_bundled_cache: dict[str, tuple[float, str, str]] = {}


def bundled_offer(target: Target = EPS) -> tuple[str, str]:
  """(commit, variant) of the image that would be installed on `target`'s board, or ("", "") for none.

  READ FROM THE IMAGE ITSELF, at the same offset and with the same magic the
  bootloader checks, so the screen and the board can never disagree about what
  is on offer. For the EPS only the first 0x110 bytes are touched -- there is no
  reason to pull 46 KB off disk to answer a question about sixteen of them.

  The image ships inside sunnypilot: there is no download, and no separate
  firmware channel. Updating the board's firmware IS updating sunnypilot.

  The lamp board's (target.image_path, blr_appslot.bin) is not bundled until its
  bench tests pass: no file is no offer, and the button says "no image". An
  image the comma would never install is no offer either, rather than a press
  that fails, and for the lamp board that is a question about the WHOLE file: it
  goes through the flasher's own check_app_slot_image() - a bench VARIANT, a
  dirty tree, the bootloader's vector rules, and the image's own length/CRC-32
  trailer, which a truncated copy does not have (BLR review round 1, BOOT-6). At
  most its 112 KB slot, read once per mtime. Its variant is "shadow" or "live".
  The EPS marker has no variant ("").
  """
  try:
    mtime = os.path.getmtime(target.image_path)
  except OSError:
    return "", ""
  cached = _bundled_cache.get(target.image_path)
  if cached is not None and cached[0] == mtime:
    return cached[1], cached[2]
  offer = ("", "")
  try:
    with open(target.image_path, "rb") as fh:
      # the EPS: the marker's 16 bytes; a target with a VARIANT (the lamp board): the whole file, one byte past its
      # slot so the check can say it would not fit
      head = fh.read(APP_ID_OFFSET + 16 if target.allowed_variants is None else target.slot_bytes + 1)
    if len(head) >= APP_ID_OFFSET + 16:
      magic, _origin, _git, _flags = struct.unpack("<4I", head[APP_ID_OFFSET:APP_ID_OFFSET + 16])
      if magic == target.marker_magic:
        ident = image_identity(head, target)
        if target.allowed_variants is None:
          offer = (ident["git"], "")
        elif check_app_slot_image(head, target) is None:
          offer = (ident["git"], ident["variant"])
  except OSError:
    pass
  _bundled_cache[target.image_path] = (mtime, *offer)
  return offer


def bundled_firmware(target: Target = EPS) -> str:
  """The commit of the image that would be installed, or "" (bundled_offer())."""
  return bundled_offer(target)[0]


_visible_cache: list = [False, 0.0]
_lamp_seen_cache: list = [False, 0.0]


def board_page_visible() -> bool:
  """Show the page once either board has ever identified itself.

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
    _visible_cache[0] = bool(ui_state.params.get(EPS_KEYS.version)) or bool(ui_state.params.get(BLR_KEYS.version))
  return _visible_cache[0]


def lamp_board_seen() -> bool:
  """The lamp board's card and button are shown once it has identified itself (BlrBoardVersion). Cached on a tick,
  like board_page_visible(): the scroller asks every frame."""
  now = time.monotonic()
  if now - _lamp_seen_cache[1] > REFRESH_S:
    _lamp_seen_cache[1] = now
    _lamp_seen_cache[0] = bool(ui_state.params.get(BLR_KEYS.version))
  return _lamp_seen_cache[0]
