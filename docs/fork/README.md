# Fork guide and upstream merge guide

This fork is sunnypilot for one car: a 2013–2015 Honda Accord V6 (AU). Its fingerprint is
`HONDA_ACCORD_9G_AU` and its platform flag is `HondaFlags.ELESYS` (the `HONDA_ELESYS` set). The car has a comma pedal
(gas interceptor) and an aftermarket **EPS-LKAS gateway board** that sits in line between the car, the EPS and the
stock LKAS camera. The board converts openpilot's `0x0E4 STEERING_CONTROL` into the camera's 9600-baud serial frame
for the EPS and reports its own state on CAN (`0x700`–`0x70F`). It can also be reflashed from the comma over CAN.

Two repositories carry the fork, and they move together:

* `SoRadGaming/sunnypilot`, branch `master` (this repo).
* `SoRadGaming/opendbc`, branch `sp-master`, which is the `opendbc_repo` submodule.

The board firmware is `S:/Software/EPS-LKAS`. It is a separate repository and is all custom code, not a fork.

This file is the index and the **upstream merge guide**. The three area documents explain what each change does and
why.

Everything below was measured against sunnypilot `10e088a2d` and opendbc `cf583b37`, compared with upstream
`sunnypilot/sunnypilot@a5f44653d` and `sunnypilot/opendbc@f95f996f`. Those two upstream heads were confirmed with
`git ls-remote` as the live heads on 2026-09-27.

---

## Documents

| document | covers |
|---|---|
| [GATEWAY-UPDATE.md](GATEWAY-UPDATE.md) | **A. Updating the board's firmware from the comma over CAN.** Covers `eps_lkas_flasher.py`, `eps_lkas_hook.py`, the bundled `eps_lkas_appslot.bin` and how it is produced, the `pandad.py` hook, Settings > gateway (mici), the `EpsLkas*` params, the firmware identity in `card.py`, cereal and `GW_VERSION`/`GW_BUILD`, and the flash trace. |
| [LKAS-GATEWAY-PROTOCOL.md](LKAS-GATEWAY-PROTOCOL.md) | **B. openpilot steering the EPS through the board.** Covers the `0x0E4` path, `0x500 SP_HUD_STATUS`, decoding `0x700`/`0x704`/`0x70B`, `carStateSP.linbusGateway`, the integrator hold, driver-torque substitution and the stale-torque guard, and the MADS hand-back. |
| [CAR-HONDA-ACCORD-9G-AU.md](CAR-HONDA-ACCORD-9G-AU.md) | **C. The car.** Covers the platform, fingerprints, DBCs, radar, safety, longitudinal (pump, gas curve, dynamic tuner, stopping debounce), gear/ECON/AEB/fuel decode, steering threshold and the lane-change nudge, UI/sunnylink/statsd. |
| [../CHANGELOG_SERIAL_STEERING.md](../CHANGELOG_SERIAL_STEERING.md) | History, newest first, of the serial-steering and gateway work (areas A and B). |
| [../SP_HUD_STATUS.md](../SP_HUD_STATUS.md) | SP-PROTOCOL from openpilot's side: `0x500`, `0x704`, `0x70B`, `0x0E4` byte 2. |
| [../SP_GATEWAY_FIRMWARE.md](../SP_GATEWAY_FIRMWARE.md) | What the board receives and how it parses it, written from the board's side. |
| [../../CHANGELOG-elesys.md](../../CHANGELOG-elesys.md) | History of the longitudinal work (area C), sections 1–19. |
| [../../FEATURES-elesys.md](../../FEATURES-elesys.md) | Plain-English guide for the driver. |
| `S:/Software/EPS-LKAS`: `CLAUDE.md`, `HANDOFF.md`, `docs/SP-PROTOCOL.md` (SP-PROTOCOL v1/v2, which the "SP_HUD_STATUS v2" comments in `custom.capnp` refer to), `docs/SP-PROTOCOL-V3.md`, `docs/CAN-UPDATE.md`, `docs/UPDATING-FROM-THE-COMMA.md`, `docs/EPS-FAULT-STATES.md`, `dbc/eps-lkas-gw.dbc`, `tools/bundle_appslot.py` | The board's side of every contract above. |
| [`opendbc_repo/FORK.md`](https://github.com/SoRadGaming/opendbc/blob/sp-master/FORK.md) (local: `opendbc_repo/FORK.md`) | Short index for the opendbc fork on its own. |

---

## Where the forks stand (2026-09-27)

| | sunnypilot | opendbc |
|---|---|---|
| fork repo / branch | `SoRadGaming/sunnypilot` `master` | `SoRadGaming/opendbc` `sp-master` |
| fork HEAD | `10e088a2d` (2026-09-27) | `cf583b37` (2026-09-22). This is `refs/heads/sp-master` on `SoRadGaming/opendbc` (checked with `git ls-remote origin sp-master`) and the commit sunnypilot pins. |
| upstream | `sunnypilot/sunnypilot` `master` → `refs/upstream/master` = `a5f44653d` (2026-09-14) | `sunnypilot/opendbc` `master` → `refs/upstream/master` = `f95f996f` (2026-09-02) |
| **fork point** (merge-base) | **`31dc4d8e5`** (2026-06-28, "ci: fix cereal validation for upstream directory restructure (#1869)") | **`b9712d20`** (2026-06-08, "Revert "deprecate carState.brake" for Honda Gas Interceptor (#481)") |
| fork commits since fork point | 66 (65 + one PR merge) | 38 |
| upstream commits not in the fork | 549 (529 excluding merges) | 173 (163 excluding merges) |
| files changed by the fork | 42 files + the `opendbc_repo` submodule pointer | 31 files |

`opendbc_repo/FORK.md` was added by a docs-only commit on `sp-master` after `cf583b37`, and the
`opendbc_repo` pointer was bumped to it. Every opendbc figure in this document still describes `cf583b37`; the docs
commit changes no code.

The fork-point submodule pointer is `b9712d20`, which is exactly the opendbc fork point. Upstream sunnypilot now pins
opendbc `f95f996f`, which is upstream opendbc's head. The two forks have diverged in step with each other.

**The local opendbc clone does not track `sp-master`.** It is a submodule clone that fetches only `master`
(`remote.origin.fetch = +refs/heads/master:refs/remotes/origin/master`). So there is no local `origin/sp-master`
(`git rev-parse origin/sp-master` fails), and `origin/HEAD` points at `origin/master` = `fe144714`. That is the
fork's GitHub default branch, and it is **not** the branch sunnypilot uses. The local `sp-master` has no upstream
configured. Check the remote with `git ls-remote origin sp-master`, and push with the explicit
`git push origin sp-master`.

### Reproducing these numbers

Upstream is fetched into a private ref namespace rather than a remote, so `git fetch origin` never touches it:

```bash
git -C S:/OP/sp-live              fetch --no-tags https://github.com/sunnypilot/sunnypilot.git +master:refs/upstream/master
git -C S:/OP/sp-live/opendbc_repo fetch --no-tags https://github.com/sunnypilot/opendbc.git    +master:refs/upstream/master

git -C S:/OP/sp-live              merge-base HEAD refs/upstream/master        # 31dc4d8e5...
git -C S:/OP/sp-live/opendbc_repo merge-base HEAD refs/upstream/master        # b9712d20...

git -C S:/OP/sp-live rev-list --count 31dc4d8e5..HEAD                         # 66
git -C S:/OP/sp-live rev-list --count HEAD..refs/upstream/master              # 549
git -C S:/OP/sp-live/opendbc_repo rev-list --count b9712d20..HEAD             # 38
git -C S:/OP/sp-live/opendbc_repo rev-list --count HEAD..refs/upstream/master # 173

git -C S:/OP/sp-live              diff --name-status 31dc4d8e5 HEAD           # the change set
git -C S:/OP/sp-live/opendbc_repo diff --name-status b9712d20 HEAD

# conflict risk of one file. sunnypilot: give BOTH the old and the new path, because upstream moved it
git -C S:/OP/sp-live rev-list --count 31dc4d8e5..refs/upstream/master -- selfdrive/car/card.py openpilot/selfdrive/car/card.py
git -C S:/OP/sp-live/opendbc_repo rev-list --count b9712d20..refs/upstream/master -- opendbc/car/honda/values.py
```

**Windows / Git Bash:** run `export MSYS_NO_PATHCONV=1` before any command with a `ref:path` argument, for example
`git show refs/upstream/master:openpilot/cereal/custom.capnp`. Without it, MSYS rewrites the argument into a Windows
path list and git answers "Not a valid object name". Paths passed after `--` are not affected.

### Previewing the merge without touching the repos

`git merge-tree` (git 2.38 or later; this machine has 2.45.1) computes the merge with no working tree and no index.
Run it in a throw-away shared clone so the merge objects are written there and not into the real repos:

```bash
export MSYS_NO_PATHCONV=1
P=/c/tmp/merge-preview                                   # anywhere outside the repos
git clone -q --shared --bare S:/OP/sp-live "$P/sp.git"
git -C "$P/sp.git" fetch -q S:/OP/sp-live refs/upstream/master:refs/upstream/master HEAD:refs/fork/head
git -C "$P/sp.git" merge-tree --write-tree --name-only refs/fork/head refs/upstream/master

git clone -q --shared --bare S:/OP/sp-live/opendbc_repo "$P/odbc.git"
git -C "$P/odbc.git" fetch -q S:/OP/sp-live/opendbc_repo refs/upstream/master:refs/upstream/master HEAD:refs/fork/head
git -C "$P/odbc.git" merge-tree --write-tree --name-only refs/fork/head refs/upstream/master
# first output line is a tree id; `git -C "$P/sp.git" show <tree>:<path>` prints a file with its conflict markers
```

The "Trial merge" columns below come from exactly this, run on 2026-09-27.

---

## What upstream changed that the fork runs into

Read this list before starting. Most of the work in the next merge comes from these upstream changes, not from the
fork's own diff.

**sunnypilot**

* **Nested `openpilot/` layout.** Upstream commits `5edc0bd89` ("mv root dirs into nested openpilot") and `37eda06c9`
  ("move cereal/ into nested openpilot") are commaai commits from 2026-06-21. They reached upstream sunnypilot through
  the sync merge `14318d2f0` on 2026-07-16. After them, `cereal/`, `common/`, `selfdrive/` and `sunnypilot/` live under
  `openpilot/`. At the fork point it was the other way round: `openpilot/common`, `openpilot/selfdrive`,
  `openpilot/sunnypilot`, `openpilot/system` and `openpilot/tools` were five symlinks (mode `120000`) to the root
  directories, and they still are at the fork's HEAD. On this Windows checkout (`core.symlinks=false`) those symlinks
  are small text files, which is why `openpilot.*` does not import here. Almost every fork path moves: `X` becomes
  `openpilot/X`. The top-level `docs/`, `CHANGELOG-elesys.md` and `FEATURES-elesys.md` do not move.
* **Imports moved with it.** `from cereal import ...` becomes `from openpilot.cereal import ...`.
  `openpilot.system.hardware` becomes `openpilot.common.hardware`. The `./pandad` working directory becomes
  `openpilot/selfdrive/pandad`.
* **Renamed cereal services** (`6d5d1f691`, 2026-08-10):
  * `liveCalibration` → `extrinsicsCalibration`
  * `livePose` → `deviceMotion`
  * `liveParameters` → `vehicleParameters`
  * `liveTorqueParameters` → `lateralTorqueParameters`
  * `liveDelay` → `lateralDelay`
  * `liveTracks` → `radarTracks`

  Every `SubMaster` list the fork edited conflicts because of this.
* **pytest is gone.** `98e7c4f98` ("replace pytest with lil custom runner") and `ac4ab9a9b` ("migrate sunnypilot tests
  to unittest and remove pytest") removed it. `tools/test_runner.py` (also `tools/op.sh test`) collects only
  `unittest.TestCase` classes, through `unittest.TestLoader.loadTestsFromName`. See [Tests](#tests).
* **`IsOnroad` is gone.** `ad5151b38` ("single IsOffroad param") deleted it; manager now writes only `IsOffroad`
  (`CLEAR_ON_MANAGER_START`, `BOOL`). `eps_lkas_hook.py` reads `IsOnroad` twice (lines 112 and 162), so after the merge
  every update request stops at `requested` and never flashes. It is the only param key the fork reads that upstream
  no longer has. See GATEWAY-UPDATE.md §12.3 item 1.
* **Fonts load on the fly.** `96ca1f8ed` deleted `selfdrive/assets/fonts/process.py`, which
  `test_gateway_board_settings.py` reads.
* **Lane-change logic rewritten.** `7d325d665` "Simplify lane change logic", `4532320fb`, and `2d859a8ca` "Road Edge
  Lane Change Controller" removed the `DESIRES` table, made `laneChangeStarting` timer-based, and added
  `left_edge_detected` and `right_edge_detected` to `DesireHelper.update()`. Upstream's `DesireHelper.__init__()`
  takes no arguments.
* **Longitudinal state machine simplified.** `d1e143ac9`, `031b1ad0a` "remove starting state", `fdd1df79f` "remove
  per-car stopping tunes" and `bdc8e4b02` "deprecate long kp" made these changes:
  * `long_control_state_trans(CP_SP, active, long_control_state, should_stop, brake_pressed, cruise_standstill)` no
    longer takes `CP` or `v_ego`, and never produces `LongCtrlState.starting` (stopping goes straight to pid).
  * `vEgoStopping`, `stoppingDecelRate` and `startingState` are no longer read. The stopping ramp is a fixed
    1.0 m/s³.
  * `stopAccel` is still read.
* **`LaC.update()`** in controlsd now returns `steer, lateral_output, lac_log`.

**opendbc**

* **Three Honda platform sets are gone.** `57506094` removed commaai's `Platforms.with_flags` helper. Upstream
  sunnypilot's opendbc still has `with_flags` (in `opendbc/car/__init__.py`), but its Honda `values.py` no longer
  uses it. It keeps `HONDA_BOSCH`, `HONDA_BOSCH_ALT_RADAR`, `HONDA_BOSCH_RADARLESS` and `HONDA_BOSCH_CANFD` as
  `frozenset(c for c in CAR if c.config.flags & ...)`, defined after `DBC = CAR.create_dbc_map()`. It removed
  `HONDA_NIDEC_ALT_PCM_ACCEL`, `HONDA_NIDEC_ALT_SCM_MESSAGES` and `HONDA_BOSCH_TJA_CONTROL`. The Honda code now tests
  `CP.flags & HondaFlags.X`.
* **Signatures changed.**
  * `compute_gas_brake(accel, speed, CP)` now takes `CP`, not the fingerprint.
  * `create_brake_command(packer, CAN, apply_brake, pump_on, pcm_override, pcm_cancel_cmd, fcw, stock_brake, CP_SP)`
    no longer takes `car_fingerprint` (`045cd8d3`).
  * `spam_buttons_command(..., CP)` also takes `CP`.
* **`FW_QUERY_CONFIG`** lists its Bosch members with a comprehension instead of `*HONDA_BOSCH_*` sets, and every
  `FwQueryConfig` now has a `fw_version_regex` (`d8f6d5cf`).
* **All-speed longitudinal for gas-interceptor Hondas.** `4455464a` sets `minEnableSpeed = -1` when the interceptor
  is present. That overrides this car's 19 mph. See [Behaviour upstream changes on this car](#behaviour-upstream-changes-on-this-car).

---

## Inventory

Area: **A** gateway update, **B** LKAS gateway protocol, **C** the car. **—** means repository plumbing that fits none
of the three; it is covered in this file.

**Upstream commits** is the conflict risk: the number of upstream commits that touched the file since the fork point.
For sunnypilot it counts the old and the new path together. For a file upstream moved, that count includes the move
commit itself, so **1** means "moved, otherwise untouched".

**Trial merge** is the result of the preview above:

* *clean*: upstream never touched the file.
* *moved*: only upstream's rename touched it.
* *auto*: git merged both sides without conflict.
* *new→moved*: the fork added the file in a directory upstream renamed. Git reports a file-location conflict and
  proposes the `openpilot/` path.
* *CONFLICT (n)*: a content conflict with n hunks.

### sunnypilot: 42 files and the submodule pointer

| St | Path (fork-point path) | Area | What the fork changes | Upstream commits | Trial merge |
|---|---|---|---|---|---|
| M | `.gitmodules` | — | Points the `opendbc` submodule URL at `SoRadGaming/opendbc` and adds `branch = sp-master`. | 2 | auto |
| M | `opendbc_repo` (gitlink) | — | Moves the submodule pointer `b9712d20` → `cf583b37`. | 37 | submodule conflict |
| A | `CHANGELOG-elesys.md` | C | History of the longitudinal work. | 0 | clean |
| A | `FEATURES-elesys.md` | C | Plain-English guide for the driver. | 0 | clean |
| A | `docs/CHANGELOG_SERIAL_STEERING.md` | B (+A) | Serial-steering and gateway changelog. | 0 | clean |
| A | `docs/SP_GATEWAY_FIRMWARE.md` | B | Board-side receive spec. | 0 | clean |
| A | `docs/SP_HUD_STATUS.md` | B | SP-PROTOCOL, openpilot side. | 0 | clean |
| M | `cereal/custom.capnp` | A+B | Adds `CarControlSP.lateralControl @5` (B), `CarStateSP.linbusGateway @1` (fields @0–@18 B, @19–@26 A) and `CarStateSP.driverTorqueStale @2` (B). | 8 | auto (→ `openpilot/cereal/`) |
| M | `common/params_keys.h` | A+C | Adds 7 `EpsLkas*` keys (A) and 9 `HondaDyn*` keys (C). | 35 | auto (moved) |
| M | `selfdrive/car/card.py` | A+C | A: `stage_board_firmware()`, `write_board_firmware()` and `log_flash_trace()`, called from `params_thread` (staging from `state_publish`). C: `get_car(..., skip_fw_query=bool(fixed_fingerprint))`. | 6 | auto |
| M | `selfdrive/car/helpers.py` | B | `convert_carControlSP()` rebuilds `lateralControl`. | 2 | auto |
| A | `selfdrive/car/tests/test_car_control_sp_seam.py` | B (+A) | Tests every nested `CarControlSP` struct, and the firmware fields, through the capnp→dataclass seam. | 0 | new→moved |
| M | `selfdrive/controls/controlsd.py` | B | Subscribes `carStateSP`, calls `LaC.set_linbus_gateway(present, actuating)`, and calls `run_ext(sm, pm, lac_log, LaC)`. | 10 | **CONFLICT (2)** |
| M | `selfdrive/controls/lib/desire_helper.py` | B+C | B: `update(..., driver_torque_stale)`. C: `NUDGE_FIRM`, `NUDGE_HOLD_FRAMES` and `DesireHelper(car_fingerprint)`. | 8 | **CONFLICT (4)** |
| M | `selfdrive/controls/lib/latcontrol.py` | B | Adds `LINBUS_I_CARRY_MAX`, `LINBUS_I_HOLD_TAU`, `set_linbus_gateway()`, `_linbus_integrator_gate()` and `integrator_frozen`. | 3 | auto |
| M | `selfdrive/controls/lib/latcontrol_torque.py` | B | `freeze_integrator ... or linbus_hold`. | 5 | auto |
| M | `selfdrive/controls/lib/longcontrol.py` | C | Stopping-exit debounce (`STANDSTILL_SPEED`, `STOPPING_EXIT_DEBOUNCE`), keyed on `HondaDynamicTuningEnabled`. | 9 | **CONFLICT (1)** |
| A | `selfdrive/controls/tests/test_stopping_debounce.py` | C | Script-style checks of the debounce. | 0 | new→moved |
| M | `selfdrive/modeld/modeld.py` | B+C | Subscribes `carStateSP`, calls `DesireHelper(CP.carFingerprint)` and passes `driverTorqueStale` into `DH.update`. | 54 | **modify/delete**: git did not detect the rename |
| M | `selfdrive/pandad/pandad.py` | A | Adds `flash_if_requested()` before `./pandad`, `watch_for_request()` after it, and `skip_panda_reset`. | 5 | **CONFLICT (1)** |
| M | `selfdrive/selfdrived/selfdrived.py` | B | Subscribes `carStateSP`, which `mads.py` reads. | 33 | **CONFLICT (1)** |
| M | `selfdrive/ui/sunnypilot/layouts/settings/cruise.py` | C | Honda dynamic-learning toggle on the Cruise panel. | 1 | moved |
| M | `selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py` | C | `HondaSettings`: toggle, learned values and reset (`LEARNED_DEFAULTS`, `PEDAL_GAIN_BP`, `reset_learned_values`). | 1 | moved |
| A | `selfdrive/ui/sunnypilot/mici/layouts/board.py` | A | Settings > gateway page: `BoardLayoutMici`, `UpdateBoardButton`, `board_page_visible`, `bundled_firmware`. | 0 | new→moved |
| M | `selfdrive/ui/sunnypilot/mici/layouts/settings.py` | A+C | Adds a "vehicle" button (C) and a "gateway" button (A) with `items.insert(<int>, ...)`, a form two tests pin. | 3 | **CONFLICT (1)** |
| A | `selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` | C | mici vehicle page: `VehicleLayoutMici`, `car_brand()`, `HondaLearnedInfo`. | 0 | new→moved |
| A | `selfdrive/ui/tests/test_eps_lkas_flasher.py` | A | Flasher protocol, image checks and trace (26 tests). | 0 | new→moved |
| A | `selfdrive/ui/tests/test_eps_lkas_hook.py` | A | pandad hook ordering and the onroad refusal (9 tests). | 0 | new→moved |
| A | `selfdrive/ui/tests/test_gateway_board_settings.py` | A (+B) | DBC, capnp, params, page and button gates (25 tests), including `test_lat_ready_means_lateral_is_enabled_not_merely_possible` (B). | 0 | new→moved |
| A | `selfdrive/ui/tests/test_honda_dynamic_settings.py` | C | Params, UI and sunnylink in sync with the tuner (11 tests). | 0 | new→moved |
| M | `sunnypilot/mads/mads.py` | B | Pauses on a gateway driver override (`LINBUS_REASON_DRIVER_OVERRIDE`, `_gw_paused`) and resumes by itself. Adds the fast-wheel disable (`EMERGENCY_STEER_RATE` 200, `EMERGENCY_STEER_FRAMES` 2), which applies to **every** car. | 2 | auto |
| M | `sunnypilot/modeld_v2/modeld.py` | B+C | The same three edits as `modeld.py`. | 13 | **CONFLICT (2)**: the `SubMaster` list and the `DH.update` call. `DesireHelper(CP.carFingerprint)` merges on its own. |
| M | `sunnypilot/selfdrive/controls/controlsd_ext.py` | B | Fills `CC_SP.lateralControl` from `lac_log` and `LaC`. | 2 | auto |
| M | `sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py` | B | The same integrator hold as `latcontrol_torque.py`. | 3 | auto |
| A | `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | C (+B) | `NUDGE_FIRM` rules; a stale torque confirms nothing. | 0 | new→moved |
| A | `sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin` | A | Board app-slot image: 46,540 bytes, marker `APL1`, origin `0x08004000`, commit `d995bc95`, flags `0x04` (INCAR_TEST). | 0 | new→moved |
| A | `sunnypilot/selfdrive/pandad/eps_lkas_flasher.py` | A | Portable bootloader protocol, `PandaTransport` (ELM327), `BenchTransport`, and the steering/vibration trace. | 0 | new→moved |
| A | `sunnypilot/selfdrive/pandad/eps_lkas_hook.py` | A | pandad glue: `flash_if_requested()`, `watch_for_request()`. | 0 | new→moved |
| M | `sunnypilot/sunnylink/settings_ui.json` | C | Compiled output of the two YAML files below. | 5 | auto |
| M | `sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml` | C | `honda_dynamic_learning` read-only info section. | 1 | moved |
| M | `sunnypilot/sunnylink/settings_ui_src/pages/vehicle.yaml` | C | `honda` section with the toggle. | 2 | auto |
| M | `sunnypilot/sunnylink/statsd.py` | C | Reports `HondaDynamicTuningEnabled` and the 8 learned values. | 4 | auto |
| M | `sunnypilot/sunnylink/tools/compile_settings_ui.py` | C (Other) | Reads and writes UTF-8 with an LF newline, so compiling on Windows matches CI. | 1 | moved |

### opendbc: 31 files

| St | Path | Area | What the fork changes | Upstream commits | Trial merge |
|---|---|---|---|---|---|
| M | `opendbc/car/car_helpers.py` | C | `skip_fw_query` argument on `fingerprint()` and `get_car()`. | 3 | auto |
| M | `opendbc/car/honda/carcontroller.py` | B+C | B: brake-release ceiling (`BRAKE_RELEASE_FRAMES`, `brake_release_scale`), `serial_gateway` LDW bits, `SP_HUD_STATUS` send with `lat_ready` and `op_state`, `LKAS_HUD` not sent. C: `compute_gb_honda_elesys`, `brake_pump_hysteresis_elesys` and `ELESYS_PUMP_*`, dynamic-tuner hooks (`hill_accel`/`adjust_accel`, `brake_gain`, `wind_scale`, the 32-count brake release), `SCM_BUTTONS` re-sent on `CAN.camera` every 4th frame when `openpilotLongitudinalControl`, `pcm_accel` computed from `adjust_accel`, and a `FORK:` comment explaining why there is no PCM crossfade (history in `CHANGELOG-elesys.md` section 14; no upstream code was removed). | 4 | **CONFLICT (5)** |
| M | `opendbc/car/honda/carstate.py` | A+B+C | A/B: registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION` and `GW_BUILD` liveness-exempt (`nan`), and calls `CarStateExt.update(ret, ret_sp, ...)`. C: `update_gear_elesys` / `SPORT_DWELL`, ELESYS `stockAeb` (and `carFaultedNonCritical = True` when stock AEB fires with `ACC_HUD.ACC_ON == 0`), `LKAS_PROBLEM` read from bus 0, `scm_buttons`, `econ_on`. | 3 | **CONFLICT (3)** |
| M | `opendbc/car/honda/fingerprints.py` | C | `FW_VERSIONS[HONDA_ACCORD_9G_AU]`: fwdRadar `36707-T2M-Q640`, srs `77959-T2A-B110`. | 3 | auto |
| M | `opendbc/car/honda/hondacan.py` | B+C | B: `create_steering_control(serial_gateway, ldw_left, ldw_right)`, `SP_HUD_PROTOCOL_VERSION`=3, `SP_OP_STATE_*`, `SP_HUD_MAX_TORQUE`=0, `create_sp_hud_status()`. C: `create_brake_command(..., is_metric, ...)` units bit, `create_scm_buttons_no_cruise()` (copies `SCM_BUTTONS` with `MAIN_ON = 0` and `CRUISE_BUTTONS = 0`). | 4 | **CONFLICT (2)** |
| M | `opendbc/car/honda/interface.py` | C (+B) | Gearbox `0x188` → automatic. Long tuning: `longitudinalActuatorDelay` 0.6, `vEgoStopping` 0.8, `stopAccel` -0.8. `steerActuatorDelay` 0.38, `steerAtStandstill` True, `ELESYS_SCM_STANDDOWN` safety parameter, `minEnableSpeed` 19 mph. | 3 | **CONFLICT (1)** |
| M | `opendbc/car/honda/radar_interface.py` | C | Elesys radar parser (`0x400`, `0x410`–`0x417`, `0x420`–`0x424` at 10 Hz), trigger `0x423`, `RADAR_STATE` ok in (104, 111, 125). | 4 | auto |
| A | `opendbc/car/honda/tests/test_elesys.py` | C | 52 unittest tests: gas/brake map, pump, gas curve, units bit, gear, AEB. | 0 | clean |
| M | `opendbc/car/honda/values.py` | C | `HondaSafetyFlags.ELESYS_SCM_STANDDOWN`=32, `HondaFlags.ELESYS`=1024, `CAR.HONDA_ACCORD_9G_AU`, `HONDA_ELESYS`, `STEER_THRESHOLD` 600, `non_essential_ecus`. | 8 | **CONFLICT (2)** |
| M | `opendbc/car/structs.py` | A+B | `CarControlSP.LateralControl`, `CarStateSP.driverTorqueStale`, `CarStateSP.LinbusGateway` (control fields and `fw*` fields). | 2 | auto |
| M | `opendbc/car/tests/routes.py` | C | `CarTestRoute("15646e8515eda1a7/00000019--dd0700eac9", HONDA_ACCORD_9G_AU)`. | 8 | auto |
| M | `opendbc/car/torque_data/substitute.toml` | C | `HONDA_ACCORD_9G_AU = HONDA_ACCORD`. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_gearbox_legacy.dbc` | C | `GEARBOX_AUTO` `0x188`, `GEARBOX_CVT`. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_honda_elesys_base.dbc` | C | This car's modified copy of `_honda_common.dbc` (differences listed under [DBC generator includes](#dbc-generator-includes-and-can-ids)). | 0 | clean |
| A | `opendbc/dbc/generator/honda/_lkas_hud_4byte.dbc` | C | 4-byte `LKAS_HUD` `0x33D`. | 0 | clean |
| M | `opendbc/dbc/generator/honda/_nidec_common.dbc` | C (**shared**) | `BRAKE_COMMAND` read-only signals `CMBS_BRAKE`, `CMBS_DISABLED` and `AEB_REQ_3`. They appear in every Nidec DBC. | 0 | clean |
| M | `opendbc/dbc/generator/honda/_nidec_scm_group_a.dbc` | C (**shared**) | `SCM_BUTTONS.CMBS_BUTTON` (read-only). | 0 | clean |
| A | `opendbc/dbc/generator/honda/_nidec_scm_group_a_elesys.dbc` | C | Copy of group A plus `FUEL_LEVEL`, `FUEL_SENDER`, `ODOMETER_KM`. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_steering_control_e.dbc` | B+C | `0x0E4` 5-byte with `LDW_RIGHT`, `LDW_LEFT`, `SET_ME_X00_3` (B). `STEER_STATUS` `0x18F` with `STEER_CONTROL_ACTIVE` (C, read by B). | 0 | clean |
| A | `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc` | A+B | `0x500 SP_HUD_STATUS`, `0x700 EPS_LIN_RAW`, `0x704 GW_ACTIVE` and `0x70B GW_STEER_GRANT` (B). `0x707 GW_VERSION` and `0x70F GW_BUILD` (A). | 0 | clean |
| A | `opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc` | C | Generator top file: the 9 imports plus `ECON_STATUS` `0x221`. | 0 | clean |
| A | `opendbc/dbc/honda_accord_2015au_radar.dbc` | C | Elesys radar DBC (hand-written, not generated). | 0 | clean |
| M | `opendbc/safety/modes/honda.h` | C (+B) | `ELESYS_SCM_STANDDOWN` (param 32): TX lists with `0x1A6` on bus 2 and `0x500` on bus 0 (B), and **without** `0x33D` (openpilot never sends `LKAS_HUD` on this car; the board's Stage 10 image owns it); AEB bit 43; the `pcm_gas` 198 exception; blocking `0x1A6` bus 0→2; `honda_bosch_init()` resets `honda_elesys_scm_standdown = false`. | 0 | clean |
| M | `opendbc/safety/tests/common.py` | C | Scanned-range exceptions for `TestHondaElesys` and `0x1A6`. | 3 | auto |
| M | `opendbc/safety/tests/test_honda.py` | C | `TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety`. | 0 | clean |
| M | `opendbc/sunnypilot/car/car_list.json` | C | `"Honda Accord 2013-15"` → `HONDA_ACCORD_9G_AU`. | 5 | auto |
| M | `opendbc/sunnypilot/car/honda/carstate_ext.py` | A+B+C | A: `_update_linbus_firmware`. B: `_update_linbus_gateway`, `_update_linbus_grant`, `_update_driver_torque_validity`, `_eps_lin_driver_torque_valid`. C: `fuelGauge`. | 1 | auto |
| A | `opendbc/sunnypilot/car/honda/dynamic_tuning.py` | C | `HondaDynamicTuner` (self-learning longitudinal). | 0 | clean |
| M | `opendbc/sunnypilot/car/honda/gas_interceptor.py` | C | Imports `HONDA_ELESYS`; `ELESYS_GAS_BP`/`ELESYS_GAS_V`, `elesys_gas_multiplier()`, and the `tuner` hooks (`pedal_gain_at`, `update_pedal`). | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | C | Script-style tests of the tuner. | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | C+B | Script-style tests through `CarController`. Sections [7], [8] and [10]–[15] are B. | 0 | clean |

**Trial-merge summary.**

* **sunnypilot:**
  * 7 content conflicts: `controlsd.py`, `desire_helper.py`, `longcontrol.py`, `pandad.py`, `selfdrived.py`, mici
    `settings.py`, `modeld_v2/modeld.py`.
  * 1 modify/delete: `selfdrive/modeld/modeld.py`.
  * 1 submodule conflict: `opendbc_repo`.
  * 12 file-location conflicts: every fork-added file outside `docs/` and the two root `.md` files (12 of the 17 files
    the fork adds). The other five do not move and merge clean.
  * 3 file/directory conflicts: the fork point's `openpilot/common`, `openpilot/selfdrive` and `openpilot/sunnypilot`
    symlinks against upstream's real directories. The other two symlinks, `openpilot/system` and `openpilot/tools`,
    did not conflict; the fork changed nothing under `system/` or `tools/`.
  * Everything else merged or moved cleanly.
* **opendbc:** 5 content conflicts, all in `opendbc/car/honda/`: `carcontroller.py`, `carstate.py`, `hondacan.py`,
  `interface.py`, `values.py`.

---

## Collision checks

These were checked on 2026-09-27 against `a5f44653d` and `f95f996f`. They cover what a merge can break without a
textual conflict.

### `cereal/custom.capnp` (→ `openpilot/cereal/custom.capnp`)

| struct | fork adds | upstream's highest ordinal today | collision |
|---|---|---|---|
| `CarControlSP` | `lateralControl @5 :LateralControl`. Nested `LateralControl { integrator @0 :Float32; saturated @1 :Bool; integratorFrozen @2 :Bool }` | `@4` (`intelligentCruiseButtonManagement`) | **none** |
| `CarStateSP` | `linbusGateway @1 :LinbusGateway`, `driverTorqueStale @2 :Bool` | `@0` (`speedLimit`) | **none** |
| `CarStateSP.LinbusGateway` (fork-only) | `engaged @0`, `dryRun @1`, `valid @2`, `actuating @3`, `present @4`, `grantValid @5`, `grantState @6 :UInt8`, `grantReason @7 :UInt8`, `granted @8`, `authority @9 :UInt8`, `epsAck @10`, `epsLatched @11`, `epsErrorState @12 :UInt8`, `epsFresh @13`, `camLkasOn @14`, `applied @15 :Int16`, `motorTorque @16 :Int16`, `retryIn @17 :UInt8`, `latchedUntilKeyOff @18` (all B). `fwValid @19`, `fwGitHash @20 :UInt32`, `fwDirty @21`, `fwAppSlot @22`, `fwBootloader @23`, `fwReadOnly @24`, `boardUid @25 :UInt32`, `fwBuildValid @26` (all A). | n/a | none |

Upstream's changes to `custom.capnp` since the fork point are all elsewhere in the file:

* the `Cxx` import path
* `SelfdriveStateSP.buttonsPressed @2` and `buttonsReleaseToggle @3`
* `ModelManagerSP` `verifying`, `Chunk`, `chunks @3` and `chunked`
* `OnroadEventSP` `laneChangeRoadEdge @24` and `bigModelReady @25`
* `ModelDataV2SP.leftLaneChangeEdgeBlock @1` and `rightLaneChangeEdgeBlock @2`

No upstream struct is named `LateralControl` or `LinbusGateway`. The trial merge kept all fork ordinals.

**The standing risk.** If upstream adds a field to `CarControlSP` or `CarStateSP`, it takes `@5`, or `@1`/`@2`. A
duplicate ordinal is loud: the capnp compile fails. The fix is to give upstream its ordinal and move the fork field to
the next free one. Routes recorded before the renumber then decode that field wrong.

**The dataclass twin.** `opendbc/car/structs.py` mirrors these structs. What must match is the **names**:
`convert_to_capnp()` calls `custom.CarStateSP.new_message(**asdictref(struct))`, which is keyword-based, so a missing or
extra name raises when card publishes, on a drive. The order is not load-bearing, and the fork already differs at the
top level (`structs.py` lists `speedLimit`, `driverTorqueStale`, `linbusGateway`; capnp has `linbusGateway @1`,
`driverTorqueStale @2`). Two tests guard this, both narrower than their names suggest:

* `test_capnp_and_dataclass_agree` (`test_gateway_board_settings.py`) checks `LinbusGateway` only: the eight `fw*`
  fields exist on both sides and in the same relative order, and no `LinbusGateway` ordinal is duplicated.
* `test_car_control_sp_seam` (`test_car_control_sp_seam.py`) checks that every nested `CarControlSP` struct is rebuilt
  by `convert_carControlSP()` in `helpers.py`, and round-trips `linbusGateway` including the `fw*` fields.

### `common/params_keys.h` (→ `openpilot/common/params_keys.h`)

| key | flags | type | default | area |
|---|---|---|---|---|
| `HondaDynamicTuningEnabled` | PERSISTENT, BACKUP | BOOL | "0" | C |
| `HondaDynPedalGain0` … `HondaDynPedalGain5` | PERSISTENT | FLOAT | "1.0" | C |
| `HondaDynWindFactor` | PERSISTENT | FLOAT | "1.0" | C |
| `HondaDynBrakeGain` | PERSISTENT | FLOAT | "0.0" | C |
| `EpsLkasBoardVersion` | PERSISTENT | STRING | – | A |
| `EpsLkasBoardBuild` | PERSISTENT | JSON | – | A |
| `EpsLkasBoardSeenAt` | PERSISTENT | STRING | – | A |
| `EpsLkasFlashRequested` | CLEAR_ON_MANAGER_START | BOOL | – | A |
| `EpsLkasFlashProgress` | CLEAR_ON_MANAGER_START | STRING | – | A |
| `EpsLkasFlashState` | CLEAR_ON_MANAGER_START | STRING | – | A |
| `EpsLkasFlashTrace` | PERSISTENT | JSON | – | A |

The upstream file has 264 entries and none of these 16 names. Upstream still defines the `FLOAT` and `JSON` types. The
file had 35 upstream commits, but it auto-merged, because the fork's two blocks sit between stable neighbours:
`HideVEgoUI`/`IntelligentCruiseButtonManagement` and `InteractivityTimeout`/`IsDevelopmentBranch`.

**Why a name collision would be silent.** The table is an `std::unordered_map` initializer list. A duplicate key
compiles without complaint, and only one entry survives. After every merge, check:

```bash
grep -oE '\{"[A-Za-z0-9_]+"' openpilot/common/params_keys.h | sort | uniq -d    # must print nothing
```

### Flag bits and safety parameters (opendbc)

| identifier | fork value | upstream at `f95f996f` |
|---|---|---|
| `HondaFlags.ELESYS` | 1024 | still commented "1024 is available"; free |
| `HondaSafetyFlags.ELESYS_SCM_STANDDOWN` | 32 | highest is `BOSCH_CANFD` = 16; free |
| `HONDA_PARAM_ELESYS_SCM_STANDDOWN` (`honda.h`) | 32 | Nidec params 4 / SP 1, 2; Bosch 1, 2, 8, 16; free |
| `SAFETY_ELM327` (the flasher's panda mode) | 3 | still 3 in `opendbc/safety/declarations.h` |

The Python flag and the C parameter must stay equal.

### DBC generator includes and CAN IDs

`honda_accord_au_2015_can.dbc` imports nine fragments:

* `_community.dbc`, `_nidec_common.dbc` and `_steering_sensors_c.dbc` are upstream-owned. Upstream has 0 commits on
  any of them since the fork point.
* The other six (`_honda_elesys_base`, `_lkas_hud_4byte`, `_nidec_scm_group_a_elesys`, `_steering_control_e`,
  `_gearbox_legacy`, `_sunnypilot_linbus_gw`) are fork-only, and nothing else imports them.

No includes collide.

**Drift risk.** `_honda_elesys_base.dbc` and `_nidec_scm_group_a_elesys.dbc` are copies of `_honda_common.dbc` and
`_nidec_scm_group_a.dbc`. Upstream fixes to the originals will not reach this car. Neither original changed upstream
since the fork point. Diff them on every merge:

```bash
cd opendbc/dbc/generator/honda
diff _honda_common.dbc _honda_elesys_base.dbc
diff _nidec_scm_group_a.dbc _nidec_scm_group_a_elesys.dbc
```

At `cf583b37` these are the intended differences; anything else in the output is drift:

* `_honda_elesys_base.dbc`:
  * a header comment, `CM_ "Modified 7bit STALK_STATUS"`;
  * `STEER_MOTOR_TORQUE` (`0x1AB`) has no `UNKNOWN_TORQUE_STATE_BIT` signal and no comment for it;
  * `CAMERA_MESSAGES` (`0x35E`) is 7 bytes instead of 8;
  * `STALK_STATUS` (`0x374`) is 7 bytes, has no `WIPER_SWITCH`, and its `COUNTER`/`CHECKSUM` sit at bits 53/51
    instead of 61/59;
  * the comments on 304 and 316 are `CM_ BO_` instead of the original `CM_ SG_`.
* `_nidec_scm_group_a_elesys.dbc`: a header comment, `ODOMETER_KM` (`0x294`), `FUEL_LEVEL` and `FUEL_SENDER`
  (`0x1A6`), and their comments.

The fork also edits two **shared** fragments, `_nidec_common.dbc` and `_nidec_scm_group_a.dbc`. It only adds read-only
signals, so nothing any other car transmits changes.

**CAN IDs.** `0x500` and `0x700`–`0x70F` are defined only in `_sunnypilot_linbus_gw.dbc`. Upstream has no Honda pt
DBC that uses them. `acura_ilx_2016_nidec.dbc` has `BO_ 1280 XXX_115`, but that is a radar DBC and not in this
car's pt DBC. The bootloader IDs `0x710`–`0x712` are in no DBC; only the flasher uses them.

The generated `*_generated.dbc` files are git-ignored and built in memory (`opendbc.get_generated_dbcs()`), so there is
nothing to commit.

### `.gitmodules` and the submodule pointer

Upstream changed the `msgq` URL (to `sunnypilot/msgq`) and moved the existing `neural_network_data` submodule: the
section `[submodule "sunnypilot/neural_network_data"]` keeps its name, and its `path` changed from
`sunnypilot/neural_network_data` to `openpilot/sunnypilot/neural_network_data` (the new line is tab-indented). The
fork changed only the `opendbc` URL and branch. The trial merge kept the fork's opendbc lines. After merging, run
`git submodule sync && git submodule update --init --recursive`.

The pointer itself always conflicts. Resolve it to the merged `sp-master` commit, never to upstream's `f95f996f`.

### Code that merges and then breaks

These hunks merge with no conflict marker, or sit next to one as plain context, and then break.

* **`eps_lkas_hook.py` reads `IsOnroad`**, which upstream deleted. The hook is a new→moved file with no conflict
  marker. Upstream's `Params` raises `UnknownKeyName`, which the hook catches, so the update feature dies quietly.
  `test_eps_lkas_hook.py` still passes, because its `FakeParams` accepts any key.
* **opendbc context lines that name things upstream removed.** In the trial merge these fork lines sit *outside* the
  conflict markers, so taking upstream's side of the neighbouring hunk leaves them pointing at nothing:
  * `carcontroller.py`, just below hunk 2: `elif fingerprint in HONDA_ELESYS:`. Upstream's `compute_gas_brake` has
    no `fingerprint` parameter.
  * `carcontroller.py`: `pcm_accel = int(np.clip((adjust_accel / 1.44) ...` and the `else:` branch's
    `adjust_accel = 0.0`. The only assignment on the active path, `adjust_accel = accel + hill_accel`, is on the fork
    side of hunk 4.
  * `carcontroller.py`: the whole `def compute_gb_honda_elesys` is on the fork side of hunk 2.
  * `hondacan.py`: `imperial_unit = int(not is_metric) if car_fingerprint in HONDA_ELESYS else 1`. Upstream's
    `create_brake_command` has no `car_fingerprint` parameter.

  `ruff check` (rule F821, part of `./test.sh`) reports an undefined name for any of these that survive.
* **`HONDA_ELESYS` consumers that never conflict.** `radar_interface.py`, `gas_interceptor.py`, `carstate_ext.py` and
  `test_elesys.py` import `HONDA_ELESYS` from `values.py` and merge cleanly. They fail to import unless the
  `values.py` resolution re-creates that name. The fork's conflicting import lines also name
  `HONDA_NIDEC_ALT_SCM_MESSAGES`, `HONDA_NIDEC_ALT_PCM_ACCEL` and `HONDA_BOSCH_TJA_CONTROL`, which no longer exist.
* **`test_elesys.py` uses the old signatures.** It calls `compute_gas_brake(a, v, CAR.X)` and the old
  `create_brake_command(..., car, {}, is_metric, CP_SP)`.
* **Positional argument shift in `DesireHelper.update()`, opendbc side.** Upstream's signature is now
  `update(self, carstate, lateral_active, lane_change_prob, left_edge_detected=False, right_edge_detected=False)`.
  `test_dynamic_tuning_integration.py` section [15] passes `stale` as the 4th positional argument. After the merge it
  lands in `left_edge_detected`. With the left blinker on, that blocks the lane change through the road-edge rule,
  so all three checks still pass, but they now test the road-edge block instead of the stale-torque guard (by
  inspection, not run). It must pass `driver_torque_stale=` by keyword. Section [15] only runs where
  `openpilot.selfdrive.controls.lib.desire_helper` imports; in opendbc on its own it prints SKIP.
* **Test paths.** The UI tests set `ROOT = Path(__file__).parents[3]`. After the move that resolves to `openpilot/`,
  which is still right for their `cereal/`, `common/`, `selfdrive/` and `sunnypilot/` paths. What breaks:
  * `ROOT / "opendbc_repo/..."` in `test_gateway_board_settings.py`, `test_honda_dynamic_settings.py` and
    `test_eps_lkas_flasher.py`;
  * `ROOT / "selfdrive/assets/fonts/process.py"`, because the file is deleted upstream;
  * `test_car_control_sp_seam.py`, which imports `from cereal import custom`;
  * `test_stopping_debounce.py`, which stubs a top-level `cereal` module, but upstream `longcontrol.py` imports
    `from opendbc.car.structs import car`.

The modeld files do **not** auto-merge. Their edits are in the conflict steps below.

### Tests

**Collection.** Upstream's `tools/test_runner.py` collects only `unittest.TestCase` classes. The fork's sunnypilot
tests `test_eps_lkas_flasher.py`, `test_eps_lkas_hook.py`, `test_gateway_board_settings.py`,
`test_honda_dynamic_settings.py`, `test_lane_change_nudge.py` and `test_car_control_sp_seam.py` are plain pytest
`def test_*` functions. They would be **collected as zero tests and pass silently**. Convert them to `TestCase`
classes, as upstream did for its own tests in `ac4ab9a9b`.

**Import-time side effects.**

* `test_stopping_debounce.py` runs its checks when it is imported and calls `sys.exit(1)` on failure. Upstream's
  runner imports every `test_*.py` under `openpilot/` in its own process and catches only `Exception`, so a
  `SystemExit` is not caught. The file also replaces `sys.modules['openpilot']` and several `openpilot.*` modules with
  stubs. By inspection, not run: importing it during collection would break the collection of every later module in
  that process.
* opendbc's `test_dynamic_tuning.py` and `test_dynamic_tuning_integration.py` do the same (checks at import,
  `sys.exit(1)`). They are not under `openpilot/`, so sunnypilot's runner never sees them. opendbc's `./test.sh` runs
  `unittest-parallel -j4`, whose discovery imports them; unittest's loader turns an import-time exception, including
  `SystemExit`, into a failed-import error. By inspection, not run: with the known section [10] failure (see
  [Pre-existing issues](#pre-existing-issues-found-while-writing-this)), `./test.sh` fails at HEAD today.

Wrap these checks in a `TestCase` and restore `sys.modules`, or rename the files out of the `test_*.py` pattern and run
them by hand.

### Behaviour upstream changes on this car

These are not conflicts. They arrive with the merge and change how the car drives, so verify them on the car.

1. **`minEnableSpeed` becomes -1.** Upstream opendbc `4455464a` adds
   `stock_cp.minEnableSpeed = -1. if ret.enableGasInterceptor else ...` in `_get_params_sp`. This car has the pedal,
   so the fork's `minEnableSpeed = 19 mph` for `HONDA_ACCORD_9G_AU` is overridden. Longitudinal can then engage from a
   standstill. Keep it or add an ELESYS exception; decide deliberately.
2. **Stopping.** Upstream no longer reads `vEgoStopping` (the fork sets 0.8) or `stoppingDecelRate`; the stopping ramp
   is a fixed 1.0 m/s³. The fork's debounce comment reasons from the default `stoppingDecelRate` of 0.8
   (`opendbc/car/interfaces.py`); the fork never sets it for this car. `stopAccel` (-0.8) is still read. Upstream also
   never produces `LongCtrlState.starting` any more, so the debounce only ever sees stopping → pid. Re-check stops and
   holds on the car.
3. **Lane change.** The road-edge block, the timer-based `laneChangeStarting` and entering `preLaneChange` before
   engagement all change the state machine that `NUDGE_FIRM` was tuned against, using the replay of routes d9..fd.
4. **MADS gateway pause, and upstream's brake guard.** Two problems, both in `mads.py`:
   * **(a) Today, MADS flaps through every board override. This is confirmed on a route.** On the second frame of an
     override MADS is already `paused`. Upstream's generic block `if self.should_silent_lkas_enable(CS): ...
     silentLkasEnable` (an `ET.ENABLE` event) then moves it back to `enabled`, because `should_silent_lkas_enable()`
     does not know about `_gw_paused`. The gateway block pauses it again on the next frame. Route `00000103` from
     t=58.5 has a 31 s `grantReason == 4` stretch in which `carControl.latActive` reads `0101…` frame by frame
     (LKAS-GATEWAY-PROTOCOL.md §9). The board ignores `0x0E4` while it has released, so the steering was not affected.
   * **(b) After the merge.** Upstream `79b79edd2` adds `CS.brakePressed or CS.regenBraking` to
     `should_silent_lkas_enable()`. The fork's own resume (`elif self._gw_paused and not gw_override:`) adds
     `silentLkasEnable` directly and bypasses that guard.

   Fix both in one change: add `if self._gw_paused: return False` at the top of `should_silent_lkas_enable()`, and in
   the gateway resume add `silentLkasEnable` only if `self.should_silent_lkas_enable(CS)` is true after `_gw_paused`
   has been cleared. On the car, check that during a `grantReason == 4` stretch MADS stays `paused` and `latActive`
   stays 0. Also check that with the brake held in Pause mode the pause does not lift until the brake is released.

### Other things checked, no collision

* **sunnylink section ids.** Upstream `vehicle.yaml` has `hyundai`, `subaru`, `tesla` and `toyota`, but no `honda`.
  Upstream `cruise.yaml` has no `honda_dynamic_learning`.
* **`car_list.json`.** Upstream has no key `"Honda Accord 2013-15"`.
* **FW regex.** The fork's FW strings `36707-T2M-Q640\x00\x00` and `77959-T2A-B110\x00\x00` match upstream's new
  Honda `fw_version_regex`.
* **UI widgets the fork uses** all still exist upstream with the same names: `BigButton`, `BigParamControl`,
  `BigConfirmationDialog`, `BigDialog`, `NavScroller`, `UnifiedLabel(wrap_text=...)`, `FontWeight.DISPLAY`,
  `toggle_item_sp`, `button_item_sp`, `ConfirmDialog`, `SettingsBigButton`, `ui_state.ignition` and
  `ui_state.is_offroad`. Upstream mici `settings.py` still re-adds its list through `self._scroller.add_widget(item)`,
  which `test_panel_is_reachable` asserts.
* **Icons.** `icon_vehicle.png` and `icon_software.png` still exist.
* **Imports.** Every `openpilot.*` import the fork adds resolves in upstream's tree.
* **Panda API used by the flasher.** `Panda(serial, cli=)`, `set_safety_mode(mode, param)`,
  `health()["safety_mode"]`, `can_health()` counters, and `can_send`/`can_recv`/`can_clear` are unchanged in upstream's
  panda `74a0adce`. The ELM327 mode's "param != 0 keeps `CAN_MODE_NORMAL`" is unchanged in `board/main.c`.
* **Integrator hold.** `PIDController.i`, `lac_log.i` and `lac_log.saturated`, which the hold relies on, are unchanged.

---

## Merge procedure

Merge, do not rebase. The fork's documents and changelogs cite fork commit hashes (sunnypilot `6a4f1f5`, `ef4f294`;
opendbc `43a98b9d`, `2cc16a02`, …), and a rebase would invalidate every one of them. (Board hashes such as `d995bc95`
live in `S:/Software/EPS-LKAS` and are not affected either way.)

Do the sunnypilot half on Linux or WSL. The build and the cereal tests need it, and on Windows the fork point's
`openpilot/*` symlinks are plain text files.

### Step 0: snapshot

```bash
git -C S:/OP/sp-live status --short; git -C S:/OP/sp-live/opendbc_repo status --short     # both must be empty
# fetch upstream as in "Reproducing these numbers"
D=$(date +%Y%m%d)
git -C S:/OP/sp-live tag fork/pre-merge-$D
git -C S:/OP/sp-live/opendbc_repo tag fork/pre-merge-$D
```

Run the merge preview, compare it with the tables above, and record the baseline test results before changing
anything. See Step 6 for the commands; the baseline has one known failure.

### Step 1: opendbc first

sunnypilot's tests read opendbc sources: `test_gateway_board_settings.py` reads `carcontroller.py`, `carstate.py`,
`carstate_ext.py`, `structs.py` and the gateway DBC. The pinned pointer must also exist on origin before sunnypilot
can point at it. So opendbc goes first.

```bash
cd S:/OP/sp-live/opendbc_repo
git switch sp-master && git switch -c merge/upstream-$D
git merge refs/upstream/master
```

Resolve the conflicts in this order, because each file supplies names to the next:

1. **`values.py`** (2 hunks).
   * Hunk 1: the fork side is the eight `CAR.with_flags(...)` lines; upstream's side is empty. Drop the fork side.
     Upstream's `frozenset` block sits just below, after `DBC = CAR.create_dbc_map()`, outside the markers. Add one line
     there after `HONDA_BOSCH_CANFD`:
     `HONDA_ELESYS = frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)  # FORK(HONDA_ELESYS)`.
   * Hunk 2 (`FW_QUERY_CONFIG`): take upstream's comprehension form and add `CAR.HONDA_ACCORD_9G_AU` to the `Ecu.eps`
     and `Ecu.vsa` lists.
2. **`hondacan.py`** (2 hunks).
   * Imports: take upstream's line and add `HONDA_ELESYS`.
   * `create_brake_command`: keep upstream's parameters and append the fork's at the end, with defaults:
     `(..., stock_brake, CP_SP, is_metric=True, elesys=False)`.
   * In the body, outside the markers, change `imperial_unit = int(not is_metric) if car_fingerprint in HONDA_ELESYS else 1`
     to `imperial_unit = int(not is_metric) if elesys else 1`.
3. **`carstate.py`** (3 hunks).
   * Imports: take upstream's line and add `HONDA_ELESYS`.
   * `LKAS_PROBLEM`: keep the ELESYS branch (read from `cp`, bus 0) inside upstream's
     `if not (self.CP.flags & HondaFlags.BOSCH):`.
   * Put the `scm_buttons`/`econ_on` block before upstream's `if self.CP.flags & HondaFlags.BOSCH_RADARLESS:`.
4. **`interface.py`** (1 hunk). Imports: take upstream's line and add `HONDA_ELESYS`.
5. **`carcontroller.py`** (5 hunks).
   * Hunk 1, imports: take upstream's line and add `HONDA_ELESYS`.
   * Hunk 2: keep the whole `def compute_gb_honda_elesys(accel, speed)` from the fork side, then take upstream's
     `def compute_gas_brake(accel, speed, CP):` / `if CP.flags & HondaFlags.BOSCH:`. The fork's
     `elif fingerprint in HONDA_ELESYS:` sits just below the markers as merged context; rewrite it to
     `elif CP.carFingerprint in HONDA_ELESYS:`.
   * Hunk 3 (`__init__`): take upstream's `self.tja_control = bool(CP.flags & HondaFlags.BOSCH_TJA_CONTROL)` and keep
     the fork's `self.dynamic_tuner = HondaDynamicTuner(CP, CP_SP)` with its `FORK:` comment.
   * Hunk 4 (`if CC.longActive:`): keep the fork's `adjust_accel = accel + hill_accel`, then
     `gas, brake = compute_gas_brake(adjust_accel, CS.out.vEgo, self.CP)`. The later `pcm_accel` line reads
     `adjust_accel`.
   * Hunk 5 (the `create_brake_command` call): `..., alert_fcw, CS.stock_brake, self.CP_SP, is_metric=CS.is_metric, elesys=self.CP.carFingerprint in HONDA_ELESYS)`.
6. **Tests.** Update `test_elesys.py` for the two new signatures: `TestElesysCategory.test_dispatch` (pass a `CP`) and
   `TestBrakeCommandUnitsBit` (drop `car`, pass `is_metric=`/`elesys=` by keyword).

Then run these checks:

```bash
git grep -n -E "HONDA_NIDEC_ALT_SCM_MESSAGES|HONDA_NIDEC_ALT_PCM_ACCEL|HONDA_BOSCH_TJA_CONTROL" -- opendbc   # must be empty
git grep -n -E "FORK(\(|:)" -- opendbc | wc -l                           # 18 before the merge
ruff check opendbc/car/honda opendbc/sunnypilot/car/honda                # F821 catches a leftover fingerprint / adjust_accel
PYTHONPATH=. python -c "import opendbc.car.honda.interface, opendbc.car.honda.carcontroller, opendbc.car.honda.radar_interface, opendbc.sunnypilot.car.honda.gas_interceptor, opendbc.sunnypilot.car.honda.carstate_ext"
PYTHONPATH=. python opendbc/sunnypilot/car/platform_list.py && git diff --exit-code opendbc/sunnypilot/car/car_list.json
PYTHONPATH=. python opendbc/dbc/generator/generator.py   # writes git-ignored *_generated.dbc; a broken include fails here
```

A plain `git grep "FORK"` prints 22 at `cf583b37`: the extra four are upstream Tesla DBC value strings (`LEFT_FORK_*`,
`DAS_CANCEL_FORK`, …) in `tesla_can.dbc` and `tesla_model3_vehicle.dbc`. Use the pattern above.

Then the tests. See Step 6 for the opendbc list, or run `./test.sh`, which runs `uv lock --check` and then, through
lefthook, ruff, ty, codespell, cpplint, MISRA and `unittest-parallel -j4`. Commit the merge. Then fast-forward and
push:

```bash
git switch sp-master && git merge --ff-only merge/upstream-$D && git push origin sp-master
git ls-remote origin sp-master      # must print the new head
```

### Step 2: sunnypilot

```bash
cd S:/OP/sp-live
git switch master && git switch -c merge/upstream-$D
git merge refs/upstream/master
```

Clear the structural conflicts first:

* **`opendbc_repo`:** `git -C opendbc_repo checkout <the sp-master sha from Step 1> && git add opendbc_repo`.
* **File/directory** on `openpilot/common`, `openpilot/selfdrive` and `openpilot/sunnypilot`: keep upstream's
  directories, and delete any `openpilot/<dir>~…` leftovers git created for the old symlinks.
* **The 12 new→moved files:** accept git's `openpilot/...` location for each one (`git add` the new path, and
  `git rm` the old path if it is still present).
* **`selfdrive/modeld/modeld.py` (modify/delete):** upstream's `openpilot/selfdrive/modeld/modeld.py` has
  `DH = DesireHelper()` and no `carStateSP`. Apply all three fork edits to it by hand (see item 2 below), then
  `git rm selfdrive/modeld/modeld.py`.
* Then run `git submodule sync && git submodule update --init --recursive`.

Then resolve the content conflicts in this order. The callee comes before its callers.

1. **`openpilot/selfdrive/controls/lib/desire_helper.py`** (4 hunks).
   * Take upstream's structure: no `DESIRES`, keep `LANE_CHANGE_START_TIME`.
   * Add `NUDGE_FIRM` and `NUDGE_HOLD_FRAMES` with their `FORK(HONDA_ACCORD_9G_AU)` comment.
   * Keep `__init__(self, car_fingerprint: str = "")`, which sets `nudge_firm` and `nudge_frames` (it merges as
     context).
   * Use `update(self, carstate, lateral_active, lane_change_prob, left_edge_detected=False, right_edge_detected=False, driver_torque_stale=False)`.
   * On entering `preLaneChange`, take upstream's `self.lane_change_timer = 0.0` and add `self.nudge_frames = 0`; drop
     the fork side's `self.lane_change_ll_prob = 1.0`.
   * `and not driver_torque_stale` in `torque_applied` merges as context; check it is there.
   * Put the `NUDGE_FIRM` block before upstream's edge-aware `blindspot_detected`.
2. **Both modeld files** (`openpilot/sunnypilot/modeld_v2/modeld.py`, 2 hunks, and
   `openpilot/selfdrive/modeld/modeld.py`, by hand).
   * Take upstream's `SubMaster` list (renamed services) and add `"carStateSP"`.
   * `DH = DesireHelper(CP.carFingerprint)`. In `modeld_v2` this line survives the merge on its own; in
     `selfdrive/modeld` change upstream's `DesireHelper()`.
   * Call `DH.update(sm['carState'], sm['carControl'].latActive, lane_change_prob, left_edge, right_edge, driver_torque_stale=sm['carStateSP'].driverTorqueStale)`.
3. **`openpilot/selfdrive/controls/controlsd.py`** (2 hunks).
   * Take upstream's `SubMaster` list (the renamed services) and add `'carStateSP'`.
   * Put the two gateway lines (`gw = ...`, `self.LaC.set_linbus_gateway(...)`) before upstream's
     `steer, lateral_output, lac_log = self.LaC.update(...)`.
   * Check that `self.run_ext(self.sm, self.pm, lac_log, self.LaC)` survived.
4. **`openpilot/selfdrive/selfdrived/selfdrived.py`.** Take upstream's list and add `'carStateSP'`.
5. **`openpilot/selfdrive/controls/lib/longcontrol.py`.** Take upstream's `long_control_state_trans(...)` call and add
   `prev_state = self.long_control_state` before it. Upstream no longer produces `LongCtrlState.starting`, so the
   fork's `leaving_stop` tuple is harmless.
6. **`openpilot/selfdrive/pandad/pandad.py`.** Take upstream's `Popen(..., cwd=os.path.join(BASEDIR, "openpilot/selfdrive/pandad"))`
   and add `watch_for_request(process, request_skip_panda_reset)` after it, with its comment. Check that the
   `flash_if_requested(...)` call and the `skip_panda_reset` re-entry branch survived.
7. **`openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py`.** Keep upstream's two lines unchanged and add the
   fork's two after them, with numeric indices:

   ```python
   items.insert(1, models_btn)       # upstream
   items.insert(5, sunnylink_btn)    # upstream
   items.insert(2, vehicle_btn)      # FORK(HONDA_ACCORD_9G_AU): right after models
   items.insert(3, board_btn)        # FORK(GATEWAY-UPDATE): right after vehicle
   ```

   That gives `[first, models, vehicle, gateway, …, sunnylink, …]`: upstream's buttons keep their places relative to
   the base items. Keep the numeric form: `test_mici_settings_registers_the_vehicle_page`
   (`test_honda_dynamic_settings.py`) and `test_panel_is_reachable` (`test_gateway_board_settings.py`) assert
   `items\.insert\(\d+,\s*vehicle_btn\)` and `items\.insert\(\d+,\s*board_btn\)`. Inserting relative to a widget
   (`items.index(models_btn) + 1`) is more robust, but only together with relaxing both regexes in the same commit.

Then review these auto-merged files by reading the fork hunks, not only the conflict list:

* `openpilot/cereal/custom.capnp`: ordinals as in the table above.
* `openpilot/common/params_keys.h`: all 16 keys present; the duplicate check above prints nothing.
* `openpilot/selfdrive/car/card.py`: `skip_fw_query=`, `stage_board_firmware(CS_SP)` at the end of `state_publish`,
  and `write_board_firmware()` plus `log_flash_trace()` in `params_thread`.
* `openpilot/selfdrive/car/helpers.py`: the `lateralControl` rebuild.
* `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_hook.py`: replace both `params.get_bool("IsOnroad")` reads with
  `not params.get_bool("IsOffroad")`, and update the log string on line 168. The test changes are in
  GATEWAY-UPDATE.md §12.3 item 1.
* `latcontrol.py`, `latcontrol_torque.py` and `latcontrol_torque_v0.py`: the gate is still called at the top of
  `update()`.
* `controlsd_ext.py`.
* `mads.py`: the emergency block still runs before the gateway pause, and the gateway resume honours upstream's new
  brake guard (see [Behaviour](#behaviour-upstream-changes-on-this-car) item 4). This needs a code change; the merge
  does not flag it.

### Step 3: fix the test harness for the new layout

* **`ROOT` in the four UI tests.** Keep `ROOT = Path(__file__).parents[3]`. After the move it is `openpilot/`, which is
  right for every `cereal/`, `common/`, `selfdrive/` and `sunnypilot/` path these files build (for example
  `test_gateway_board_settings.py` lines 31–35, `test_honda_dynamic_settings.py` lines 23–29, `test_eps_lkas_hook.py`
  lines 22–23 and its `pkg("openpilot", ROOT)` stubs). Changing `ROOT` itself breaks all of those. Add a separate
  `REPO = Path(__file__).parents[4]`, or a walk up to the directory that holds `.gitmodules`, and use it only for the
  `opendbc_repo/...` paths: `DBC`, `CARSTATE`, `CARSTATE_EXT`, `STRUCTS` and `CARCONTROLLER` in
  `test_gateway_board_settings.py`, `TUNER` in `test_honda_dynamic_settings.py`, and the `declarations.h` read in
  `test_eps_lkas_flasher.py`.
* Re-point `test_no_glyphs_the_baked_font_does_not_have` at `EXTRA_FONT_CHARS` in
  `openpilot/system/ui/lib/application.py`. Upstream builds its glyph set from ASCII 32-126 plus that constant, and
  `fonts/process.py`/`EXTRA_CHARS` are gone (`96ca1f8ed`). The bullet (U+2022) the page uses is in it
  (GATEWAY-UPDATE.md §12.3 item 3).
* In `test_car_control_sp_seam.py`, use `from openpilot.cereal import custom`.
* In `test_stopping_debounce.py`:
  * stub `opendbc.car.structs` (with `car`) instead of `cereal`;
  * remove the last check, "startingState car is still debounced into starting", or change it to expect
    `LongCtrlState.pid`. It asserts `launch[-1] == lc.LongCtrlState.starting`, which upstream never produces after
    `031b1ad0a`, so it fails whatever the ramp is. It also sets `cp.startingState`, which upstream no longer reads;
  * re-check the remaining expectations against the fixed 1.0 m/s³ ramp.
* In opendbc `test_dynamic_tuning_integration.py` section [15], pass `driver_torque_stale=stale` by keyword in both
  `dh.update(...)` calls. Otherwise it keeps passing through the road-edge block and no longer tests the stale-torque
  guard.
* If Step 2.7 used relative inserts, relax the regexes in `test_mici_settings_registers_the_vehicle_page` and
  `test_panel_is_reachable` in the same commit.
* Convert the pytest-function tests to `unittest.TestCase`. Stop the script-style tests from running at import time.
  See [Tests](#tests).

### Step 4: regenerate and rebuild

```bash
python openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py --check   # settings_ui.json must match its YAML
scons -j$(nproc)                                                             # params_keys.h and custom.capnp rebuild most of the tree
```

On the device, the first start after the update runs the build. Expect it to be long.

### Step 5: grep for the carry-set

```bash
git grep -n -E "FORK(\(|:)|linbus|LIN-bus|EpsLkas|eps_lkas|HondaDyn|driverTorqueStale|NUDGE_FIRM" -- openpilot | wc -l
```

Compare the count with the same pattern on `fork/pre-merge-$D` over the old paths
(`git grep -n -E "..." fork/pre-merge-$D -- cereal common selfdrive sunnypilot | wc -l`, which prints 191 at
`10e088a2d`). A large drop means a hunk was lost. For opendbc,
`git grep -n -E "FORK(\(|:)|linbus|LIN-bus|HONDA_ELESYS|HondaDyn|driverTorqueStale" -- opendbc | wc -l` prints 199 at
`cf583b37`.

`git grep -n IsOnroad -- openpilot` must print nothing in fork code.

### Step 6: tests

**Baseline, on the current fork.** pytest still exists here.

```bash
# Linux, openpilot environment, repo root
pytest selfdrive/ui/tests/test_eps_lkas_flasher.py selfdrive/ui/tests/test_eps_lkas_hook.py \
       selfdrive/ui/tests/test_gateway_board_settings.py selfdrive/ui/tests/test_honda_dynamic_settings.py \
       selfdrive/car/tests/test_car_control_sp_seam.py \
       sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py
python selfdrive/controls/tests/test_stopping_debounce.py

# Windows: the four UI tests parse source instead of importing openpilot, and run like this.
# Verified 2026-09-27: 71 passed (26 + 9 + 25 + 11).
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --noconftest -c /dev/null --rootdir . \
       selfdrive/ui/tests/test_eps_lkas_flasher.py selfdrive/ui/tests/test_eps_lkas_hook.py \
       selfdrive/ui/tests/test_gateway_board_settings.py selfdrive/ui/tests/test_honda_dynamic_settings.py
python selfdrive/controls/tests/test_stopping_debounce.py     # ALL CHECKS PASSED
```

On Windows, `test_car_control_sp_seam.py` and `test_lane_change_nudge.py` need the openpilot environment.
`openpilot.*` does not import there, and loading cereal's schema under pycapnp was seen to crash the interpreter (exit
0xC0000409; not re-verified).

**opendbc**, from `opendbc_repo` with `PYTHONPATH=.`. It uses unittest, not pytest.

```bash
python -m unittest opendbc.car.honda.tests.test_elesys           # 52 tests; verified on Windows 2026-09-27
python -m unittest -k Elesys opendbc.safety.tests.test_honda     # builds libsafety with `cc`; Linux/macOS only
python -m unittest opendbc.sunnypilot.car.tests.test_car_list    # needs jinja2
python opendbc/sunnypilot/car/honda/test_dynamic_tuning.py       # ALL CHECKS PASSED
python opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py   # 1 known failure at HEAD, see below
./test.sh                                                        # everything, Linux/macOS
```

**After the merge**, sunnypilot tests run under upstream's runner, and only after Step 3:

```bash
python tools/test_runner.py openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py \
  openpilot/selfdrive/ui/tests/test_gateway_board_settings.py openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py \
  openpilot/selfdrive/car/tests/test_car_control_sp_seam.py openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py \
  openpilot/selfdrive/controls/tests/test_stopping_debounce.py -v
# or: tools/op.sh test <same paths>
```

A runner report of "0 tests" for any of these means the `TestCase` conversion has not been done.

### Step 7: on the car

Parked, with the ignition on and Always Offroad:

* Settings > gateway appears and shows the firmware hash and board id.
* The button says "up to date", unless a new `eps_lkas_appslot.bin` was bundled.

First drive:

1. **No ACC/CMBS fault on the first ignition after the update.** The log should contain "skipping the VIN/FW query, no
   OBD multiplexing".
2. **Gateway state in `carStateSP.linbusGateway`.** `valid` and `grantValid` are true within a second (`present` is
   always true on this platform, so it proves nothing). `fwValid` and `fwBuildValid` are true within about a minute.
3. **The board receives `0x500`.** On `0x70F`, `BUILD_SP_FRESH` is 1 and `0x500 PROTOCOL_VERSION` is 3.
4. **Steering.** Lateral engages and `grantState` reaches 3–5. While the board is not actuating,
   `carControlSP.lateralControl.integratorFrozen` is true and the torque integrator does not integrate: it decays toward
   zero with `LINBUS_I_HOLD_TAU` (30 s). On the frame the board takes over it is clipped to ±`LINBUS_I_CARRY_MAX`
   (0.25 m/s²), not reset.
5. **Driver override.** A driver override pauses MADS (not off) and it resumes by itself, and the cluster keeps the
   dashed lanes. Through the whole override `carControl.latActive` stays 0; it must not alternate frame by frame. A fast wheel of 200 deg/s or more turns MADS off. With the brake held in Pause mode, a gateway pause
   does **not** resume until the brake is released.
6. **Brake.** With lateral active, a brake press walks the command to zero in about 0.2 s.
7. **Lane change.** A brush with the blinker on does not start a lane change; a firm tug or a held push does.
8. **Longitudinal.**
   * Stock ACC stays stood down.
   * Stops hold without rolling.
   * Re-check the items under [Behaviour upstream changes on this car](#behaviour-upstream-changes-on-this-car):
     engage speed, stopping, lane change, and the MADS brake guard.
   * Gear reads P/R/N/D.
   * The `HondaDyn*` values change after 60 s when the tuner is on.
9. **Flashing, only if a new board image is bundled.** Update through Settings > gateway. The next drive's log should
   carry "eps-lkas flash trace".

### Step 8: land it

Opendbc `sp-master` should already be pushed from Step 1 (confirm with `git ls-remote origin sp-master`). Fast-forward
sunnypilot `master` to the merge branch and push it; the device runs what `master` has.

In the firmware repo, change `DEFAULT_DST` in `tools/bundle_appslot.py` to
`S:/OP/sp-live/openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin`, and fix the same path in
`docs/CAN-UPDATE.md`. Otherwise the next bundle is written where nothing reads it (GATEWAY-UPDATE.md §12.3 item 4). Update this file: the fork
points (they become the new upstream heads automatically), the counts and the inventory.

---

## Conventions for carrying custom code

### What the fork already does

* **Markers.**
  * opendbc: `FORK(HONDA_ELESYS):` ×10, `FORK(HONDA_ACCORD_9G_AU):` ×2, bare `FORK:` ×6 (18 in all).
  * sunnypilot: `FORK(HONDA_ACCORD_9G_AU):` ×1 (`desire_helper.py`), `FORK:` ×3 (`longcontrol.py`).
  * `git grep -n -E "FORK(\(|:)"` lists them. A plain `git grep FORK` also hits upstream Tesla DBC strings.
  * Most sunnypilot core hunks carry prose markers instead: "LIN-bus gateway:", "HONDA_ELESYS:", "EPS-LKAS".
* **Per-car gating, so other cars keep upstream behaviour:**
  * `HondaFlags.ELESYS` / `HONDA_ELESYS` gates the car-specific opendbc branches: gas curve, pump, brake units bit,
    gear, AEB, `LKAS_PROBLEM`, the `SCM_BUTTONS` re-send, the serial-gateway steering, `LKAS_HUD` suppression, the
    radar parser and the `carstate_ext` gateway decode.
  * The dynamic-tuner hooks are **not** ELESYS-gated. In `carcontroller.py` (`hill_accel`/`adjust_accel`, which also
    feeds `pcm_accel`; `brake_gain`; `wind_scale`; the 32-count brake release) and `gas_interceptor.py`
    (`pedal_gain_at`, `update_pedal`) they are gated by the tuner itself: `HondaDynamicTuningEnabled`, and
    `HondaDynamicTuner._is_applicable()` = `openpilotLongitudinalControl and carFingerprint not in HONDA_BOSCH`. So they
    reach any Nidec Honda with openpilot longitudinal once the toggle is on. With the toggle off they are no-ops.
  * Per-fingerprint tables: `STEER_THRESHOLD`, `NUDGE_FIRM`.
  * `carStateSP.linbusGateway.present` gates the integrator hold and the MADS pause. It is False on every platform
    outside `HONDA_ELESYS`. On `HONDA_ELESYS` it is True every frame, with or without a board fitted: it means "this
    platform can have a board", not "a board answered". On this platform with no board, or a silent one, `actuating` is
    False, so the integrator is held and decays for the whole drive. That is intended (with no board nothing follows
    `0x0E4`, so the loop is open), but the hold is not evidence that a board is fitted; use `valid` or `fwValid` for
    that.
  * `grantValid` gating `granted`, so absence is never permission. The MADS pause needs `grantValid`, so it cannot fire
    without a board.
  * The `HondaDynamicTuningEnabled` toggle; the tuner is inert when it is off.
  * The `ELESYS_SCM_STANDDOWN` safety parameter.
  * The gateway page stays hidden until a board has identified itself.
* **Car-only DBC fragments.** `_steering_control_e`, `_lkas_hud_4byte`, `_gearbox_legacy`, `_honda_elesys_base`,
  `_nidec_scm_group_a_elesys` and `_sunnypilot_linbus_gw` are imported only by `honda_accord_au_2015_can.dbc`, rather
  than edits to shared fragments.
* **New code lives in new files and on sunnypilot's extension points.**
  * New files: `sunnypilot/selfdrive/pandad/eps_lkas_*.py`, `opendbc/sunnypilot/car/honda/dynamic_tuning.py`, and the
    mici `board.py` / `vehicle.py`.
  * Extension points: `CarStateExt` (`carstate_ext.py`), `GasInterceptorCarController`, `ControlsExt`
    (`controlsd_ext.py`).
* **Portable tests.** The UI and flasher tests parse source text instead of importing raylib or openpilot, so they run
  anywhere.

### Where the fork does not follow those conventions

These are the hunks to look at first when judging whether a merge changed another car.

* **`mads.py` emergency fast-wheel disable** (`EMERGENCY_STEER_RATE`) is **not gated**. It applies to every car with
  MADS enabled.
* **`card.py` passes `skip_fw_query=bool(fixed_fingerprint)`.** Every car whose platform the user picked skips the
  VIN/FW query and runs with empty `carFw`/VIN, not just this car.
* **`longcontrol.py`** reads a Honda parameter (`HondaDynamicTuningEnabled`) in a file every car runs. It is inert
  unless that parameter is set.
* **The dynamic tuner** reaches every Nidec Honda with openpilot longitudinal when its toggle is on (see above).
* **Shared DBC fragments.** `_nidec_common.dbc` and `_nidec_scm_group_a.dbc` gained read-only signals.
* **Core files without `FORK(...)` markers.** The latcontrol base-class gate, controlsd, card, pandad, selfdrived, the
  modeld files and helpers all use prose markers only.

### Recommendations

1. Put a `FORK(<area>)` marker on every hunk in a file the fork does not own. Use `FORK(GATEWAY-UPDATE)`,
   `FORK(LKAS-GATEWAY)`, or `FORK(HONDA_ACCORD_9G_AU)`/`FORK(HONDA_ELESYS)`. Then `git grep -n -E "FORK(\(|:)"` is the
   whole carry-set.
2. Add parameters **at the end** of upstream signatures, with defaults, and pass them **by keyword**. Section [15] of
   `test_dynamic_tuning_integration.py` is the cost of a positional argument: after the merge it still passes, testing
   the wrong thing.
3. When a fork line sits next to upstream code that is likely to move, anchor it to a named widget or line rather than
   a numeric index, and write the test to accept that. Today the mici settings tests pin the numeric form, so change
   both together.
4. Keep logic in fork-owned modules and leave one call line in the core file. Good candidates:
   * the MADS gateway pause, into a helper under `sunnypilot/mads/`;
   * `stage_board_firmware`, `write_board_firmware` and `log_flash_trace`, into a module under
     `sunnypilot/selfdrive/car/`;
   * the integrator gate, as a mixin.
5. Gate the two ungated changes above: `EMERGENCY_STEER_RATE`, and `skip_fw_query` via a per-platform set. Otherwise
   document them as deliberate all-car behaviour.
6. Write new tests as `unittest.TestCase`, with no work done at import time and no `parents[n]` paths that reach
   outside the tree they test.
7. Before each merge, check upstream's highest ordinal in `CarControlSP` and `CarStateSP`, and keep the fork's fields
   last.

---

## Pre-existing issues found while writing this

These are not caused by any merge; they are true of HEAD today.

* **Failing check in `test_dynamic_tuning_integration.py`.** Section [10] fails the check "v3: enabled, lateral
  available, not asking -> READY and LAT_READY" with `b5=0x04`: `OP_STATE` is READY but `LAT_READY` is clear. The test
  was last changed in `2cc16a02`, before `43a98b9d` made `LAT_READY` mean `CC_SP.mads.enabled or CC.latActive`. The
  test's frame never sets `mads.enabled`. Fix the test, not the code. The sunnypilot test
  `test_lat_ready_means_lateral_is_enabled_not_merely_possible` already pins the new meaning. Because the file exits
  at import, this likely also fails opendbc's `./test.sh` (by inspection, not run).
* **MADS flaps during a board override (confirmed, route `00000103` t=58.5).** Upstream's generic
  `should_silent_lkas_enable` block re-enables a gateway pause on the next frame, and the gateway block re-pauses it,
  so `latActive` alternates frame by frame. The fork's own resume also ignores the pedal and gear guards, and after the
  merge it will ignore upstream's brake guard too. See [Behaviour](#behaviour-upstream-changes-on-this-car) item 4.
* **Inaccurate comment.** `controlsd.py` says "present is False on every car without a board". It is False on every
  platform outside `HONDA_ELESYS` and always True on `HONDA_ELESYS`. The capnp comment and card's comment say it
  correctly.
* **Unused import.** `card.py` adds `import json` and never uses it.
* **Stale docstring.** `board.py` cites `card.publish_board_firmware()`; the functions are
  `stage_board_firmware()` and `write_board_firmware()`.
* **Stale comment.** The comment in `LatControl.__init__` still says the PID is reset on takeover. The code clips it
  to `LINBUS_I_CARRY_MAX` and decays it with `LINBUS_I_HOLD_TAU` instead, as `_linbus_integrator_gate`'s docstring
  explains.
