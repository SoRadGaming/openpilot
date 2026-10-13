"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The glue that lets pandad flash the EPS-LKAS board. Kept separate from
eps_lkas_flasher.py so the protocol module stays free of openpilot imports and
can be exercised on a bench.

WHY IT LIVES INSIDE pandad AT ALL. The board is updated over the car's CAN bus,
which means something has to own the panda for the duration - and pandad owns it
always, onroad and off. The obvious levers are both wrong:

  systemctl stop comma  also stops the UI, so the button would kill the screen
                        that was showing it. No progress, no result.
  pkill pandad          manager's ensure_running puts it straight back, and
                        `pandad` matches both the wrapper's proctitle and the
                        binary's argv, so it kills the wrong one too.

What is left is the window that already exists: pandad.py owns the panda
exclusively before it spawns ./pandad, and blocks on that process for the rest
of the session. Asking ./pandad to exit reopens that window, and the UI process
is untouched throughout - which is what makes real progress on screen possible.
sunnypilot already flashes third-party firmware in exactly this window
(rivian_long_flasher), though over USB DFU rather than CAN.

TWO THINGS THAT MUST NOT HAPPEN, both handled here:

  1. The panda must not be DFU-recovered as a side effect. pandad.py's loop
     alternates reset_internal_panda() and recover_internal_panda() on
     re-entry, and the second drives BOOT0 high and reflashes the panda.
     Pressing "update the board" must not silently reflash the panda first.
     skip_reset() is how the caller is told.

  2. The request must not loop. If EpsLkasFlashRequested is still set when the
     loop re-enters, the watcher fires again and the board is reflashed
     forever, with no backoff, against the gateway that steers the car.
     CLEAR_ON_MANAGER_START does not save you - manager does not restart
     between iterations of pandad's own loop. So the param is cleared BEFORE
     anything is attempted, and a retry is a fresh button press.

TWO BOARDS (FORK(BRAKE-LAMP-RELAY), BLR-v1 DESIGN E.2). The brake-lamp board is
updated the same way, through the same window, with its own params (BLR_KEYS:
BlrFlashRequested, BlrFlashState, ...) and the flasher's BLR target. Each
request names its board by being that board's param, so nothing here ever has
to decide which board a request was for, and each board clears only its own.
Everything above holds per board: the onroad refusal, clear-first, the Cancel
read before it is cleared, never raising. And ONE update per window: pandad.py
asks the EPS first and the lamp board only if the EPS ran nothing; a second
request waits for the next window, which the watcher opens once ./pandad is
back. The page never lets both be pending at once (board.py _busy()).
"""
from __future__ import annotations

import signal
import threading
import time
from typing import NamedTuple

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher import (
  BLR,
  BLR_VARIANT_NAMES,
  CANCELLED,
  EPS,
  PandaTransport,
  image_identity,
  load_bundled_image,
  run_flash,
)

REQUEST_PARAM = "EpsLkasFlashRequested"
PROGRESS_PARAM = "EpsLkasFlashProgress"
STATE_PARAM = "EpsLkasFlashState"
# push9: what the update screen shows (the flasher's phase, as JSON) and its
# Cancel button. Both CLEAR_ON_MANAGER_START.
PHASE_PARAM = "EpsLkasFlashPhase"
CANCEL_PARAM = "EpsLkasFlashCancel"
VERSION_PARAM = "EpsLkasBoardVersion"
BUILD_PARAM = "EpsLkasBoardBuild"
SEEN_PARAM = "EpsLkasBoardSeenAt"
# The flash runs offroad with loggerd stopped, so nothing it logs is recorded
# anywhere. This param is how the steering trace survives to the next drive,
# where card emits it into an ordinary route.
TRACE_PARAM = "EpsLkasFlashTrace"

WATCH_PERIOD_S = 1.0


class FlashKeys(NamedTuple):
  """One board's params, and the prefix of its log lines. board.py has the page's half of this table (BOARD_KEYS),
  and test_eps_lkas_hook.py holds the two to the same names. A NamedTuple, not a dataclass: the tests load this
  file without registering it in sys.modules, where a dataclass cannot resolve its own annotations."""
  request: str
  progress: str
  state: str
  phase: str
  cancel: str
  version: str
  build: str
  seen: str
  trace: str
  log: str


EPS_KEYS = FlashKeys(request=REQUEST_PARAM, progress=PROGRESS_PARAM, state=STATE_PARAM, phase=PHASE_PARAM,
                     cancel=CANCEL_PARAM, version=VERSION_PARAM, build=BUILD_PARAM, seen=SEEN_PARAM,
                     trace=TRACE_PARAM, log="eps-lkas")
# FORK(BRAKE-LAMP-RELAY): the lamp board's twins (params_keys.h; card writes the Board ones from a drive)
BLR_KEYS = FlashKeys(request="BlrFlashRequested", progress="BlrFlashProgress", state="BlrFlashState",
                     phase="BlrFlashPhase", cancel="BlrFlashCancel", version="BlrBoardVersion",
                     build="BlrBoardBuild", seen="BlrBoardSeenAt", trace="BlrFlashTrace", log="blr")
# the flasher's targets compare by identity (Target is eq=False), so they key this as themselves
KEYS = {EPS: EPS_KEYS, BLR: BLR_KEYS}

# BlrBoardBuild's "variant" is the marker's number (card writes 0x627's), image_identity() gives its name
_BLR_VARIANT_IDS = {name: v for v, name in BLR_VARIANT_NAMES.items()}


def _clear_request(params: Params, k: FlashKeys = EPS_KEYS) -> None:
  params.put_bool(k.request, False)


def _finish(params: Params, state: str, k: FlashKeys = EPS_KEYS) -> None:
  """The terminal state, on both params the page reads, and the Cancel spent:
  a Cancel pressed after the last cancellable phase must not cancel the next
  run."""
  params.put(k.state, state)
  params.put(k.phase, {"p": state.split(":", 1)[0].split(" ", 1)[0], "cancel": False})
  params.put_bool(k.cancel, False)


def _existing_build(params: Params, k: FlashKeys = EPS_KEYS) -> dict:
  """Whatever card last decoded, so the board UID survives a flash."""
  try:
    raw = params.get(k.build)
    if isinstance(raw, dict):
      return dict(raw)
    if raw:
      import json
      return dict(json.loads(raw))
  except Exception:
    pass
  return {}


def _build_after_flash(target, ident: dict, build: dict) -> dict:
  """The board's Build param once `ident`'s image is installed: what card decoded, with the flags the install just
  proved. THE FLAGS MUST MOVE WITH THE HASH - see the ok path in flash_if_requested().

  The lamp board: the image's dirty flag and VARIANT (as card writes it, the number), a bootloader (it answered),
  and dual-bank flash (its bootloader refuses BEGIN with NAK 9 on a board that is not). Its bootGit, uid and borEn
  are the board's, not the image's, and stay as card last read them."""
  if target is BLR:
    build.update({"dirty": ident["dirty"], "variant": _BLR_VARIANT_IDS.get(ident.get("variant", ""), 0),
                  "bootloader": True, "dualBank": True})
  else:
    build.update({"dirty": ident["dirty"], "readOnly": ident["readOnly"],
                  "appSlot": True, "bootloader": True})
  return build


def watch_for_request(process, skip_reset, params: Params | None = None, targets=(EPS, BLR)) -> threading.Thread:
  """Watch for a flash request and ask ./pandad to stand down for it.

  ONLY OFFROAD. Taking CAN away from a moving car is not something a param
  write should be able to do, whatever the UI thinks it is gating on - the
  request simply is not honored until the car is off the road. The board and
  the flasher each refuse on vehicle speed as well, but those checks run after
  ./pandad has already been stopped, which is far too late to be the only one.

  SIGINT, not terminate(): it is the signal pandad.py's own handler forwards,
  under the comment "signal pandad to close the relay and exit", so the relay
  is released the way it is on any other shutdown.

  Every board in `targets` (the EPS first), each by its own params and each
  refused or cancelled on its own; the first request that stands stops ./pandad
  once, and any other waits for the next window.
  """
  params = params or Params()

  refused: set = set()

  def run() -> None:
    while process.poll() is None:
      for target in targets:
        k = KEYS.get(target)
        if k is None:
          continue
        try:
          if params.get_bool(k.request):
            # IsOffroad is the only onroad/offroad param upstream keeps (ad5151b38).
            # A missing or false IsOffroad reads as onroad, which refuses.
            if not params.get_bool("IsOffroad"):
              # Once, not once a second. And the request is dropped rather than
              # latched for the rest of the drive: the module's own rule is that
              # a retry is a fresh button press, and a request that fires the
              # moment the car is parked is not one anybody made.
              if k not in refused:
                refused.add(k)
                cloudlog.warning(f"{k.log}: flash requested while onroad, dropping it")
                _clear_request(params, k)
                params.put(k.state, "failed: not while driving")
            elif params.get_bool(k.cancel):
              # Cancelled on the screen before pandad was asked to stop: drop the
              # request here, and the car link never goes down at all.
              cloudlog.info(f"{k.log}: flash cancelled before it started")
              _clear_request(params, k)
              _finish(params, CANCELLED, k)
            else:
              cloudlog.info(f"{k.log}: flash requested, asking pandad to exit")
              skip_reset()
              process.send_signal(signal.SIGINT)
              return
        except Exception:
          cloudlog.exception(f"{k.log}: watcher")
      time.sleep(WATCH_PERIOD_S)

  t = threading.Thread(target=run, daemon=True, name="eps_lkas_watch")
  t.start()
  return t


def flash_if_requested(panda_serial: str, bus: int = 0, transport_factory=None, target=EPS) -> bool:
  """Called from pandad.py once it owns the panda and before ./pandad starts.

  Returns normally whatever happens. Anything raised here would take the
  wrapper down with it, ./pandad would never start, and the car would have no
  CAN at all until manager noticed.

  True once it has taken a request for `target`'s board and not found it
  cancelled - the update session is this window's, whatever its outcome - and
  False when there was no session: no request, a request dropped onroad, or one
  cancelled before it began. pandad.py asks the next board only on False, so a
  window holds at most one session.

  transport_factory exists so this whole path - the param handling, the
  progress writes, the version write-back - can be exercised against a spare
  board on a bench adapter. Without it the only way to find out whether the
  ordering here is right would be to try it on the car.
  """
  try:
    k = KEYS[target]
  except (KeyError, TypeError):
    cloudlog.error(f"eps-lkas: no params for board {getattr(target, 'name', target)!r}")
    return False
  params = Params()
  try:
    if not params.get_bool(k.request):
      return False
  except Exception:
    cloudlog.exception(f"{k.log}: could not read the request param")
    return False

  # ITS OWN ONROAD CHECK, not just the watcher's. This runs on EVERY re-entry
  # of pandad's loop - including ones the watcher did not cause, such as a
  # ./pandad crash mid-drive. Without this, a request left over from a press
  # made while parked would be honored at road speed by an exit nothing here
  # asked for.
  try:
    if not params.get_bool("IsOffroad"):
      cloudlog.warning(f"{k.log}: request present but the car is onroad, dropping it")
      _clear_request(params, k)
      params.put(k.state, "failed: not while driving")
      return False
  except Exception:
    cloudlog.exception(f"{k.log}: could not read IsOffroad")
    return False

  # BEFORE anything else. See the note at the top of this file.
  try:
    _clear_request(params, k)
  except Exception:
    cloudlog.exception(f"{k.log}: could not clear the request")
    return False

  transport = None
  try:
    # READ, THEN CLEAR. A Cancel pressed while ./pandad was stopping is the
    # owner's answer; clearing first would lose it.
    if params.get_bool(k.cancel):
      cloudlog.info(f"{k.log}: flash cancelled before it started")
      _finish(params, CANCELLED, k)
      return False
    params.put(k.state, "running")
    params.put(k.progress, "0")
    params.put(k.phase, {"p": "check", "cancel": True})
    cloudlog.info(f"{k.log}: starting board flash")

    image, err = load_bundled_image(target)
    if image is None:
      _finish(params, f"failed: {err}", k)
      cloudlog.error(f"{k.log}: {err}")
      return True

    factory = transport_factory or (lambda: PandaTransport(bus=bus, serial=panda_serial))
    transport = factory()
    ok, msg = run_flash(
      transport, image,
      log=lambda s: cloudlog.info(f"{k.log}:{s}"),
      progress=lambda p: params.put(k.progress, str(p)),
      trace_out=lambda d: params.put(k.trace, d) if d else None,
      phase=lambda d: params.put(k.phase, d),
      should_cancel=lambda: params.get_bool(k.cancel),
      target=target,
    )

    if not ok and msg == CANCELLED:
      _finish(params, CANCELLED, k)
      cloudlog.info(f"{k.log}: flash cancelled, the board keeps its image")
    elif ok:
      _finish(params, f"ok {msg}", k)
      # The settings page reads all three. Writing them here rather than
      # waiting for card to see a 0x707 on the next drive is what makes the
      # page show the new firmware at the moment it is asked to.
      #
      # THE FLAGS MUST MOVE WITH THE HASH. Writing only the version left the
      # page showing a new commit beside the PREVIOUS image's flags - which,
      # for the very first CAN update a board ever receives, would have meant a
      # fresh hash sitting next to "no bootloader".
      ident = image_identity(image, target)
      build = _build_after_flash(target, ident, _existing_build(params, k))
      params.put(k.version, msg)
      params.put(k.build, build)
      params.put(k.seen, str(int(time.time())))  # noqa: TID251 - wall clock, kept across boots (board.py reads it)
      cloudlog.event(f"{k.log}.flashed", version=msg, **({"variant": ident["variant"]} if "variant" in ident else {}))
    else:
      _finish(params, f"failed: {msg}", k)
      cloudlog.error(f"{k.log}: flash failed: {msg}")
  except Exception as e:
    try:
      _finish(params, f"failed: {type(e).__name__}: {e}", k)
    except Exception:
      pass
    cloudlog.exception(f"{k.log}: flash_if_requested")
  finally:
    if transport is not None:
      try:
        transport.close()
      except Exception:
        cloudlog.exception(f"{k.log}: closing the transport")
  return True
