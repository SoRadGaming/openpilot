# Brake-lamp relay board (BLR-v1): the data side

The BLR-v1 board lights the car's brake lamps from openpilot's own `0x1FA BRAKE_COMMAND` `BRAKE_LIGHTS` bit, which the
car ignores. It sits on bus 0 and reports itself at `0x620`-`0x62F`. Its firmware, design and CAN reference live in
the board repo (`S:/Software/EPS-LKAS`, branch `blr-v1`, folder `blr/`: `docs/DESIGN.md` section E, `docs/BLR-CAN.md`).

This file covers how the board's telemetry reaches the comma and the Settings > gateway page: the DBC, the decoder,
cereal, card and the `Blr*` params. Updating the board from the comma (flasher, hook, page) is described with the
EPS-LKAS board's update in [GATEWAY-UPDATE.md](GATEWAY-UPDATE.md). Markers `FORK(BRAKE-LAMP-RELAY)`.

In push11 (2026-10-21), with the board's live image `42524898` bundled (`blr_appslot.bin`, GATEWAY-UPDATE.md §13);
`brakeLampRelay` is `CarStateSP @6`.

## The one rule

**Nothing the board sends feeds `brakePressed`, `brakeLights`, any controls decision or panda safety** (board design
E.7). Masking or inventing a pedal press from the lamp circuit would hide a real press, or make one up, at the worst
moment. `0x17C BRAKE_PRESSED` / `BRAKE_SWITCH` stay openpilot's only brake-pedal input. The tests hold this three ways:

* `opendbc/sunnypilot/car/honda/test_blr.py`: with the board's frames on the bus, a status that claims
  `DRIVER_BRAKING`, `PEDAL_CAN` and `EXT_SINK` among them, `CarState` is byte-identical frame for frame to a run without
  them, while `0x17C` alone moves `brakePressed`. The decoder `_update_blr(ret_sp, cp)` is never handed `ret`, and no
  BLR name appears in opendbc outside the decoder, the parser registration and the struct; none of the board's IDs
  appears in panda safety.
* `openpilot/sunnypilot/selfdrive/car/tests/test_blr_board.py`: `carStateSP.brakeLampRelay` is named in
  `custom.capnp` and `blr_board.py` and nowhere else in openpilot; no `Blr*` param and no `brakeLampRelay` appears in
  selfdrived, controls, MADS, the C++ pandad, opendbc's car code or panda safety.
* There are no onroad alerts for the board in this phase.

## From the wire to the page

| step | where | what |
|---|---|---|
| DBC | `opendbc/dbc/generator/honda/_sunnypilot_blr.dbc`, imported by `honda_accord_au_2015_can.dbc` | `BLR_STATUS` `0x620`, `BLR_ANALOG` `0x621`, `BLR_HEALTH` `0x622`, `BLR_COUNTERS` `0x623`, `BLR_COUNTERS2` `0x624`, `BLR_VERSION` `0x627`, `BLR_BUILD` `0x62F`. Cut verbatim from the firmware's `blr/dbc/blr.dbc`; change that first, then copy. Intel byte order, no `COUNTER`/`CHECKSUM` names. The boot channel `0x628`-`0x62A` is not copied. |
| parser | `opendbc/car/honda/carstate.py` `get_can_parsers()` | all seven registered `float("nan")` (liveness-exempt), `HONDA_ELESYS` only |
| decoder | `opendbc/sunnypilot/car/honda/carstate_ext.py` `_update_blr()` | status (`0x620`) and analog (`0x621`) valid while less than 1 s old, frame-counted; identity (`0x627`, `0x62F`) latched on first sight. Never raises. |
| cereal | `custom.capnp` `CarStateSP.brakeLampRelay @6 :BrakeLampRelay`; opendbc `structs.py` mirrors it | 23 fields, listed in the schema with their meaning |
| card | `selfdrive/car/card.py` -> `sunnypilot/selfdrive/car/blr_board.py` `BlrBoardParams` | `stage(CS_SP)` on the 100 Hz loop notes values only; `write()` on `params_thread` (10 Hz) does the writes. Neither can raise into card. |
| page | `selfdrive/ui/sunnypilot/mici/layouts/board.py` | reads the params below |

`card` is only_onroad and the page is offroad, so the page only ever sees what the params kept.

## The params

| key | lifetime | type | written by | content |
|---|---|---|---|---|
| `BlrBoardVersion` | PERSISTENT | STRING | card | the board's git hash, `"%08x"` (`0x627 GIT_HASH`), on change |
| `BlrBoardBuild` | PERSISTENT | JSON | card | see below, on change |
| `BlrBoardSeenAt` | PERSISTENT | STRING | card | unix seconds of the last fresh `0x620`, written at most once a minute; the latched identity alone never moves it |
| `BlrLastDrive` | PERSISTENT | JSON | card | this drive's summary, see below; on change, at most once a second |
| `BlrFlashTrace` | PERSISTENT | JSON | the update hook | card emits it into the next route once, then removes it (as `EpsLkasFlashTrace`) |
| `BlrFlashRequested`, `BlrFlashProgress`, `BlrFlashState`, `BlrFlashPhase` (JSON), `BlrFlashCancel` | CLEAR_ON_MANAGER_START | as the `EpsLkasFlash*` twins | the page and the hook | the update request and its progress |

`BlrBoardBuild`:

```json
{"dirty": false, "variant": 2,
 "bootloader": true, "bootGit": "b28113", "uid": "a5c3e1", "borEn": true, "dualBank": true}
```

* `dirty` and `variant` come with the hash on `0x627` and are always present. `variant`: 1 bench, 2 shadow, 3 live.
* The other five come from `0x62F` and are **absent until it has arrived**. Absent means "not heard yet", never "no".
  `bootloader` false means the board needs its one SWD visit before any CAN update; `dualBank` false means its
  bootloader will refuse one (NAK 9).
* A shadow image and a live image can be built from the same commit, so **"up to date" must compare the hash and the
  variant**, not the hash alone.

`BlrLastDrive`:

```json
{"t": 1760000000, "variant": 2, "faults": 160, "faultNames": ["D_SENSE_MID", "F_FEEDBACK"],
 "latch": 2, "latchName": "FEEDBACK", "wouldOn": true, "outDrive": false, "extSink": true}
```

* `t`: unix seconds when this drive first heard the board. A new drive does not touch the record until the board is
  heard, so the page keeps showing the previous drive's until then.
* `faults`: the OR over the drive of every fresh `0x620`'s bits 16-23, bit 0 first: `F_U4_NOT_SINKING`,
  `F_U4_LIMITING`, `D_LAMP_DARK`, `D_COIL_OPEN`, `D_LAMP_WITHOUT_US`, `D_SENSE_MID`, `D_EXT_SINK_HELD`, `F_FEEDBACK`
  (`F_` trips, `D_` diagnoses; the board's `0x623 FAULTS_EVER` is the same OR per power cycle). `faultNames` lists them.
* `latch` / `latchName`: 0 / `""`, or 1 `U4`, 2 `FEEDBACK`, 3 `CLOCK`, 4 `PIN_STUCK_HIGH`. A latch holds until the
  board loses power.
* `wouldOn` / `outDrive` / `extSink`: whether the board ever decided to light the lamps, ever drove its output (always
  false on a shadow image), and ever saw something other than the pedal sink the lamp relay (the ACC unit).
* "ok" is `faults == 0 and latch == 0`.

The names are the firmware's, so a value read on the device greps against `blr/docs/BLR-CAN.md`.

## Tests

```bash
cd opendbc_repo && python -m unittest opendbc.sunnypilot.car.honda.test_blr       # 15: DBC, decoder, E.7
RAYLIB_BACKEND=headless python tools/test_runner.py openpilot/sunnypilot/selfdrive/car/tests/test_blr_board.py   # 19
```

The board's bytes in both are the firmware's golden vector:
`opendbc/sunnypilot/car/honda/fixtures/blr_golden_frames.json` is a byte-for-byte copy of the board repo's
`blr/tools/golden_frames.json`, which the shipping `blr_pack()` produces and the firmware's `blr_dbc_test.py` checks
against `blr/dbc/blr.dbc`. Re-copy it whenever the firmware re-pins it. With the firmware tree reachable
(`BLR_FIRMWARE_DIR`, or the S: drive from WSL), `test_the_copies_are_the_firmwares_files` holds both copies to the
firmware's own files - `_sunnypilot_blr.dbc` line for line for the seven telemetry frames, the vector byte for byte -
so a layout changed in both of the firmware's files is caught too, not only one that disagrees with the other.
