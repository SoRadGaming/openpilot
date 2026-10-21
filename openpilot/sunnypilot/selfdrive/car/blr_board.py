"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BRAKE-LAMP-RELAY): the BLR-v1 brake-lamp board's params, written by card.

The board lights the brake lamps from openpilot's own 0x1FA BRAKE_LIGHTS bit and reports itself on bus 0 (0x620-0x62F,
opendbc _sunnypilot_blr.dbc), which carstate_ext.py decodes into carStateSP.brakeLampRelay. card is only_onroad and the
Settings > gateway page is offroad, so what the page shows has to outlive the drive in params - the EpsLkasBoard* story
(card.py stage_board_firmware / write_board_firmware), told again for a second board with its own keys:

  BlrBoardVersion  "%08x", the board's git hash (0x627), as EpsLkasBoardVersion
  BlrBoardBuild    {"dirty", "variant"} from 0x627, plus {"bootloader", "bootGit", "uid", "borEn", "dualBank"} once
                   0x62F has arrived - absent keys mean "not heard yet", never "no"
  BlrBoardSeenAt   unix seconds as a string: when a fresh 0x620 last arrived, written at most once a minute
  BlrLastDrive     this drive's summary: {"t", "variant", "faults", "faultNames", "latch", "latchName", "wouldOn",
                   "outDrive", "extSink"} - written on change, at most once a second, from fresh status frames only

Two halves, for the reason EpsLkas has two: stage() runs on card's 100 Hz control loop and only notes values; write()
runs on params_thread at 10 Hz and does the blocking Params writes. Neither may ever raise into card.

DIAGNOSTICS ONLY. Nothing here, and nothing the board sends, may feed brakePressed, brakeLights, a controls decision
or panda safety (DESIGN E.7): this class reads brakeLampRelay to describe the board and writes params, and that is all.
"""
import time

from opendbc.sunnypilot.car.honda.carstate_ext import BLR_FAULT_SIGNALS, BLR_LATCH_REASONS
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

VERSION_PARAM = "BlrBoardVersion"
BUILD_PARAM = "BlrBoardBuild"
SEEN_AT_PARAM = "BlrBoardSeenAt"
LAST_DRIVE_PARAM = "BlrLastDrive"

SEEN_AT_S = 60.0          # BlrBoardSeenAt's heartbeat, EpsLkasBoardSeenAt's
LAST_DRIVE_MIN_S = 1.0    # BlrLastDrive: on change, at most once a second


class BlrBoardParams:
  def __init__(self, params: Params):
    self.params = params
    self._version: str | None = None
    self._build: dict | None = None
    self._pending: tuple[str, dict] | None = None
    self._seen_at: float | None = None
    self._heard = False
    self._heard_wall: int | None = None
    # (faults OR, latch reason, wouldOn ever, outDrive ever, extSink ever) over this drive's fresh 0x620s
    self._drive: tuple[int, int, bool, bool, bool] | None = None
    self._drive_t: int | None = None
    self._drive_written: dict | None = None
    self._drive_written_at: float | None = None
    self._stage_error_logged = False
    self._write_error_logged = False

  def stage(self, CS_SP) -> None:
    """card's 100 Hz control loop: note the board's identity and fold one frame into the drive. No I/O here.

    The identity (0x627 + 0x62F) is latched by carstate_ext.py, so fwValid / buildValid mean "heard this drive".
    The drive summary reads only a fresh 0x620 (valid): a stale one is all-zero by construction and must not
    read as a clean drive.
    """
    try:
      blr = CS_SP.brakeLampRelay
      if blr.fwValid:
        # VARIANT and DIRTY ride with the hash on 0x627, so they are always known with it. The 0x62F keys are
        # added only once 0x62F has arrived: absence is "unknown", never "no bootloader" (stage_board_firmware()).
        build = {"dirty": bool(blr.fwDirty), "variant": int(blr.variant)}
        if blr.buildValid:
          build.update(bootloader=bool(blr.bootPresent), bootGit=f"{blr.bootGitLo24:06x}",
                       uid=f"{blr.boardUid24:06x}", borEn=bool(blr.optBorEn), dualBank=bool(blr.optDualBank))
        self._pending = (f"{blr.fwGitHash:08x}", build)

      if blr.valid:
        self._heard = True
        d = self._drive or (0, 0, False, False, False)
        drive = (d[0] | int(blr.faults), int(blr.latchReason) or d[1], d[2] or bool(blr.wouldOn),
                 d[3] or bool(blr.outDrive), d[4] or bool(blr.extSink))
        if drive != self._drive:
          self._drive = drive
    except Exception:
      if not self._stage_error_logged:
        self._stage_error_logged = True
        cloudlog.exception("blr: stage raised; the board's params are not updated")

  def write(self) -> None:
    """card's params_thread: the blocking writes.

    Identity on change. SeenAt at most once a minute, holding the time a fresh 0x620 last arrived, so "last seen"
    means heard rather than remembered - the latched identity alone never moves it. BlrLastDrive on change, at most
    once a second, and not at all until this drive has heard the board, so the previous drive's record stands.
    """
    try:
      self._write()
    except Exception:
      if not self._write_error_logged:
        self._write_error_logged = True
        cloudlog.exception("blr: writing the board's params raised")

  def _write(self) -> None:
    pending = self._pending
    if pending is not None:
      version, build = pending
      if version != self._version or build != self._build:
        # THE DICT, NOT json.dumps(build): BlrBoardBuild is JSON-typed, and Params.put has no (str, JSON) entry.
        self.params.put(VERSION_PARAM, version)
        self.params.put(BUILD_PARAM, build)
        self._version, self._build = version, build
        cloudlog.info(f"blr board firmware {version} {build}")

    now = time.monotonic()
    if self._heard:
      # heard since the last tick, 0.1 s ago: the time of the hearing, not of the write a minute later
      self._heard = False
      self._heard_wall = int(time.time())  # noqa: TID251 - wall clock, kept across boots
    if self._heard_wall is not None and (self._seen_at is None or now - self._seen_at > SEEN_AT_S):
      self.params.put(SEEN_AT_PARAM, str(self._heard_wall))
      self._seen_at, self._heard_wall = now, None

    drive = self._drive
    if drive is None or (self._drive_written_at is not None and now - self._drive_written_at < LAST_DRIVE_MIN_S):
      return
    if self._drive_t is None:
      self._drive_t = int(time.time())  # noqa: TID251 - wall clock: when this drive first heard the board
    faults, latch, would_on, out_drive, ext_sink = drive
    record = {
      "t": self._drive_t,
      "variant": int(pending[1]["variant"]) if pending is not None else 0,
      "faults": faults,
      "faultNames": [name for i, name in enumerate(BLR_FAULT_SIGNALS) if faults >> i & 1],
      "latch": latch,
      "latchName": BLR_LATCH_REASONS.get(latch, str(latch)) if latch else "",
      "wouldOn": would_on,
      "outDrive": out_drive,
      "extSink": ext_sink,
    }
    if record != self._drive_written:
      self.params.put(LAST_DRIVE_PARAM, record)   # the dict, as above: BlrLastDrive is JSON-typed
      self._drive_written, self._drive_written_at = record, now
      cloudlog.info(f"blr last drive {record}")
