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
"""
from __future__ import annotations

import signal
import threading
import time

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher import (
  PandaTransport,
  load_bundled_image,
  run_flash,
)

REQUEST_PARAM = "EpsLkasFlashRequested"
PROGRESS_PARAM = "EpsLkasFlashProgress"
STATE_PARAM = "EpsLkasFlashState"
VERSION_PARAM = "EpsLkasBoardVersion"

WATCH_PERIOD_S = 1.0


def _clear_request(params: Params) -> None:
  params.put_bool(REQUEST_PARAM, False)


def watch_for_request(process, skip_reset, params: Params | None = None) -> threading.Thread:
  """Watch for a flash request and ask ./pandad to stand down for it.

  ONLY OFFROAD. Taking CAN away from a moving car is not something a param
  write should be able to do, whatever the UI thinks it is gating on - the
  request simply is not honoured until the car is off the road. The board and
  the flasher each refuse on vehicle speed as well, but those checks run after
  ./pandad has already been stopped, which is far too late to be the only one.

  SIGINT, not terminate(): it is the signal pandad.py's own handler forwards,
  under the comment "signal pandad to close the relay and exit", so the relay
  is released the way it is on any other shutdown.
  """
  params = params or Params()

  def run() -> None:
    while process.poll() is None:
      try:
        if params.get_bool(REQUEST_PARAM):
          if params.get_bool("IsOnroad"):
            cloudlog.warning("eps-lkas: flash requested while onroad, ignoring")
          else:
            cloudlog.info("eps-lkas: flash requested, asking pandad to exit")
            skip_reset()
            process.send_signal(signal.SIGINT)
            return
      except Exception:
        cloudlog.exception("eps-lkas: watcher")
      time.sleep(WATCH_PERIOD_S)

  t = threading.Thread(target=run, daemon=True, name="eps_lkas_watch")
  t.start()
  return t


def flash_if_requested(panda_serial: str, bus: int = 0, transport_factory=None) -> None:
  """Called from pandad.py once it owns the panda and before ./pandad starts.

  Returns normally whatever happens. Anything raised here would take the
  wrapper down with it, ./pandad would never start, and the car would have no
  CAN at all until manager noticed.

  transport_factory exists so this whole path - the param handling, the
  progress writes, the version write-back - can be exercised against a spare
  board on a bench adapter. Without it the only way to find out whether the
  ordering here is right would be to try it on the car.
  """
  params = Params()
  try:
    if not params.get_bool(REQUEST_PARAM):
      return
  except Exception:
    cloudlog.exception("eps-lkas: could not read the request param")
    return

  # BEFORE anything else. See the note at the top of this file.
  _clear_request(params)

  transport = None
  try:
    params.put(STATE_PARAM, "running")
    params.put(PROGRESS_PARAM, "0")
    cloudlog.info("eps-lkas: starting board flash")

    image, err = load_bundled_image()
    if image is None:
      params.put(STATE_PARAM, f"failed: {err}")
      cloudlog.error(f"eps-lkas: {err}")
      return

    factory = transport_factory or (lambda: PandaTransport(bus=bus, serial=panda_serial))
    transport = factory()
    ok, msg = run_flash(
      transport, image,
      log=lambda s: cloudlog.info(f"eps-lkas:{s}"),
      progress=lambda p: params.put(PROGRESS_PARAM, str(p)),
    )

    if ok:
      params.put(STATE_PARAM, f"ok {msg}")
      # The settings page reads this. Writing it here rather than waiting for
      # card to see a 0x707 on the next drive is what makes the page show the
      # new firmware at the moment it is asked to, instead of the old one.
      params.put(VERSION_PARAM, msg)
      cloudlog.event("eps-lkas.flashed", version=msg)
    else:
      params.put(STATE_PARAM, f"failed: {msg}")
      cloudlog.error(f"eps-lkas: flash failed: {msg}")
  except Exception as e:
    try:
      params.put(STATE_PARAM, f"failed: {type(e).__name__}: {e}")
    except Exception:
      pass
    cloudlog.exception("eps-lkas: flash_if_requested")
  finally:
    if transport is not None:
      transport.close()
