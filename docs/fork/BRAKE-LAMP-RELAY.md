# Brake-lamp relay board (BLR-v1): the data side

The BLR-v1 board lights the car's brake lamps from openpilot's own `0x1FA BRAKE_COMMAND` `BRAKE_LIGHTS` bit, which the
car ignores. It sits on bus 0 and reports itself at `0x620`-`0x62F`. Its firmware, design and CAN reference live in
the board repo (`S:/Software/EPS-LKAS`, branch `blr-v1`, folder `blr/`: `docs/DESIGN.md` section E, `docs/BLR-CAN.md`).

This file covers how the board's telemetry reaches the comma and the Settings > gateway page: the DBC, the decoder,
cereal, card and the `Blr*` params. Updating the board from the comma (flasher, hook, page) is described with the
EPS-LKAS board's update in [GATEWAY-UPDATE.md](GATEWAY-UPDATE.md). Markers `FORK(BRAKE-LAMP-RELAY)`.

In push11 (2026-10-21), with the board's live image `42524898` bundled (`blr_appslot.bin`, GATEWAY-UPDATE.md §13);
`brakeLampRelay` is `CarStateSP @6`. Since push18 (2026-11-01) the bundled image is live `e8854f9e`, **frame layout 2**:
the debug telemetry dropped (0.48% -> 0.23% of bus 0) - 0x620 is 4 bytes at 10 Hz plus on change, 0x621 2.5 Hz, 0x622
slim and slow, 0x623/0x624 sent by the bench image only, the identity 1 Hz for 30 s and then every 10 s. Every field
the decoder reads kept its bit, so either layout decodes right; routes from before it are read with the firmware's
`blr/tools/blr_layout1.dbc`.

## The one rule

**Nothing the board sends feeds `brakePressed`, `brakeLights`, any controls decision or panda safety** (board design
E.7). Masking or inventing a pedal press from the lamp circuit would hide a real press, or make one up, at the worst
moment. `0x17C BRAKE_PRESSED` / `BRAKE_SWITCH` stay openpilot's only brake-pedal input. The tests hold this two ways:

* `opendbc/sunnypilot/car/honda/test_blr.py`: with the board's frames on the bus, a status that claims
  `DRIVER_BRAKING`, `PEDAL_CAN` and `EXT_SINK` among them, `CarState` is byte-identical frame for frame to a run without
  them, while `0x17C` alone moves `brakePressed`. The decoder `_update_blr(ret_sp, cp)` is never handed `ret`, and no
  BLR name appears in opendbc outside the decoder, the parser registration, the struct and `brake_switch_mirror.py`
  (the one deliberate exception, below); none of the board's IDs appears in panda safety.
* `openpilot/sunnypilot/selfdrive/car/tests/test_blr_board.py`: `carStateSP.brakeLampRelay` is named in
  `custom.capnp`, `blr_board.py` and `brake_lamp_alert.py` and nowhere else in openpilot; no `Blr*` param and no
  `brakeLampRelay` appears in selfdrived, controls, MADS, the C++ pandad, opendbc's car code or panda safety, except in
  `brake_lamp_alert.py`. `TestTheOneSelfdrivedReader` holds that module to naming only its five warnings, writing
  nothing it reads, and having its events read by nothing on a controls path; selfdrived touches it exactly twice.

Two readers act on the board onroad:

* **The 0x17C mirror to the radar** (2026-10-29, the owner's call; opendbc `brake_switch_mirror.py`, opendbc
  `FORK.md` section E): the one deliberate exception to E.7. Bus 2 only: `BRAKE_SWITCH` is forced to 1 in the copy the
  radar gets while the board reports its output on and no dark-lamp fault, so the radar never sees lit lamps with no
  brake switch. openpilot's own `brakePressed`, and panda safety's view of the car's 0x17C on bus 0, are untouched.
* **The onroad warnings** (2026-10-31, below): report only.

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

## Onroad alerts

`openpilot/sunnypilot/selfdrive/selfdrived/brake_lamp_alert.py` (2026-10-31, the owner's call), called by selfdrived
next to the EPS latch alert. **Warnings only**: no NO_ENTRY or disable event and no carState flag, so a failing board
costs the lamps of openpilot's own braking and nothing else. Only on HONDA_ELESYS with openpilot longitudinal (not in
stock ACC mode).

| alert | when | why it matters |
|---|---|---|
| "Brake Light Board Offline" | no 0x620 for 2 s once the board was heard this drive; or 10 s from the start when this car has had a board lately (`BlrBoardVersion` set and `BlrBoardSeenAt` under 14 days old) and it has not been heard | unpowered, unplugged, crashed, stuck in its bootloader, its transmitter off after the 3rd bus-off |
| "Brake Light Board Fault" / "Turn the car off and on to clear it" | LATCHED or a latch reason for 1 s | its output is off until key-off |
| "Brake Lights Not Lighting" | for 1 s: openpilot asks for the lamps and the board is not driving them; or it drives them with `D_LAMP_DARK`; or it reports `D_COIL_OPEN` at idle (its own row: output off, relay line low, lamps low); or no valid 0x1FA for 150 ms (openpilot's comes at 50 Hz; in the stock pass-through modes the radar's own keeps the board fresh) | a trip, degraded CAN, a shadow image, a fuse, the relay or its coil, a board that cannot hear openpilot |
| "Brake Lights Stuck On" | 5 s of the lamps lit (`lampSnsMv` over 6 V) with the output off, the pedal up, no parking brake, no CMBS, over 5 m/s | a shorted output (U4 drain to source), a coil wire to ground, the lamp feed stuck on |
| "Brake Light Fault" (small, silent) | every 5 minutes while any of the above holds | |

Each condition counts evidence for and against itself and clears after 3 s of evidence the other way:

* A stale 0x620 counts towards offline and towards nothing else.
* A frame that cannot tell counts towards neither: the pedal down, a stop (the car's brake hold lights the lamps
  there), the analog frame stale (`vbatSnsMv` 0; a true 0 mV lamp reading is dark), and for not lighting every frame
  where openpilot is not braking. So a fault that shows only while openpilot brakes is one episode for the drive -
  one chime, then the reminder - and only 3 s in all of braking with the lamps driven and lit clears it.

Each confirm announces once with one short sound (6 s on screen), held until openpilot or MADS is active. Several queue:
the most important first (latched, stuck on, not lighting, offline), each after the last has had its 6 s. A latch
explains a board that does not light the lamps, so not lighting is not announced on top of it. The announcements are
`Priority.LOWER`, under steerSaturated's "Take Control", LDW and driver monitoring. Each confirm is a `cloudlog.warning`
line `brake lamp alert: <condition> (<the board's fields>)`, each clear `brake lamp alert: <condition> cleared`.

On the four drives with the board (15e-161, 2026-10-29) each condition held for 0 s, and the lamps read lit with no
output and no pedal for at most 0.20 s. Replayed through the module those drives raise nothing; with a fault injected
into the second half of each (silence, a latch, dark lamps, no command, lamps stuck on) each raises exactly its own
alert, once, and on the 11.6-minute route 161 one silent reminder.

Not covered:

* A blank or erased chip runs the ST ROM bootloader, which can light the lamps through PB10's pull-up but sends no
  0x620. It reads only as "Offline", whose words name the opposite failure.
* A board in a reset loop (watchdog, brown-out) comes back in under the decoder's 1 s, so it raises nothing.
  `BLR_HEALTH` (0x622: reset cause, warm resets) and the counters (0x623/0x624) are still not decoded.
* Shown only while openpilot or MADS is active: lamps stuck on while driving with neither are not announced until one
  engages.
* `D_LAMP_WITHOUT_US` fires on every pedal press on this car (the pedal does not pull the coil line low as the design
  assumed), so it is not used, and `BlrLastDrive` never reads "ok" for that reason.
* A U4 that trips three times during one long brake announces "Not Lighting" while it retries, then "Board Fault" once
  it latches: two chimes for one failure, the second with the instruction that matters.

## Tests

```bash
cd opendbc_repo && python -m unittest opendbc.sunnypilot.car.honda.test_blr       # 15: DBC, decoder, E.7
RAYLIB_BACKEND=headless python tools/test_runner.py openpilot/sunnypilot/selfdrive/car/tests/test_blr_board.py   # 23
python -m unittest openpilot/sunnypilot/selfdrive/selfdrived/tests/test_brake_lamp_alert.py                      # 32
```

The board's bytes in both are the firmware's golden vector:
`opendbc/sunnypilot/car/honda/fixtures/blr_golden_frames.json` is a byte-for-byte copy of the board repo's
`blr/tools/golden_frames.json`, which the shipping `blr_pack()` produces and the firmware's `blr_dbc_test.py` checks
against `blr/dbc/blr.dbc`. Re-copy it whenever the firmware re-pins it. With the firmware tree reachable
(`BLR_FIRMWARE_DIR`, or the S: drive from WSL), `test_the_copies_are_the_firmwares_files` holds both copies to the
firmware's own files - `_sunnypilot_blr.dbc` line for line for the seven telemetry frames, the vector byte for byte -
so a layout changed in both of the firmware's files is caught too, not only one that disagrees with the other.
