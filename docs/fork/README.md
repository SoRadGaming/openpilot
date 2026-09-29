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
why. [UPSTREAM-2026-09.md](UPSTREAM-2026-09.md) is what the last upstream sync brought and what it does on this car.

Everything below describes the fork **after the 2026-09-27 sync** with upstream sunnypilot `a5f44653d` and opendbc
`f95f996f`, read from the merged code: sunnypilot branch `merge/upstream-2026-09-27` (merge commits `6b6b2b31e` and
`d1a14edcb`) and opendbc `8bd6e314`, each with the post-merge review fixes. The version of this file before the sync
measured the fork at sunnypilot `10e088a2d` and opendbc `cf583b37` against the old fork points `31dc4d8e5` and
`b9712d20`; git history has it.

---

## Documents

| document | covers |
|---|---|
| [GATEWAY-UPDATE.md](GATEWAY-UPDATE.md) | **A. Updating the board's firmware from the comma over CAN.** Covers `eps_lkas_flasher.py`, `eps_lkas_hook.py`, the bundled `eps_lkas_appslot.bin` and how it is produced, the `pandad.py` hook, Settings > gateway (mici), the `EpsLkas*` params, the firmware identity in `card.py`, cereal and `GW_VERSION`/`GW_BUILD`, and the flash trace. |
| [LKAS-GATEWAY-PROTOCOL.md](LKAS-GATEWAY-PROTOCOL.md) | **B. openpilot steering the EPS through the board.** Covers the `0x0E4` path, `0x500 SP_HUD_STATUS`, decoding `0x700`/`0x704`/`0x70B`, `carStateSP.linbusGateway`, the integrator hold, driver-torque substitution and the stale-torque guard, and the MADS hand-back. |
| [CAR-HONDA-ACCORD-9G-AU.md](CAR-HONDA-ACCORD-9G-AU.md) | **C. The car.** Covers the platform, fingerprints, DBCs, radar, safety, longitudinal (pump, gas curve, dynamic tuner, stopping tune and debounce), gear/ECON/AEB/fuel decode, steering threshold and the lane-change nudge, UI/sunnylink/statsd. |
| [NSW-SPEED-ZONES.md](NSW-SPEED-ZONES.md) | **NSW speed zones.** The car's speed limit in NSW from Transport for NSW's open data instead of OSM: the matcher, tunnel dead reckoning, school zones and calendar, the weekly data Action and release, the downloader, modes and params, and what the owner must check. Area **NSW**, markers `FORK(NSW-ZONES)`. |
| [UPSTREAM-2026-09.md](UPSTREAM-2026-09.md) | The 2026-09 upstream sync: every upstream change since the old fork point, what it does on this car, what the merge kept, and what is still open. |
| [../CHANGELOG_SERIAL_STEERING.md](../CHANGELOG_SERIAL_STEERING.md) | History, newest first, of the serial-steering and gateway work (areas A and B), and the driver-facing summary of the 2026-09 sync. |
| [../SP_HUD_STATUS.md](../SP_HUD_STATUS.md) | SP-PROTOCOL from openpilot's side: `0x500`, `0x704`, `0x70B`, `0x0E4` byte 2. |
| [../SP_GATEWAY_FIRMWARE.md](../SP_GATEWAY_FIRMWARE.md) | What the board receives and how it parses it, written from the board's side. |
| [../../CHANGELOG-elesys.md](../../CHANGELOG-elesys.md) | History of the longitudinal work (area C), sections 1–19. |
| [../../FEATURES-elesys.md](../../FEATURES-elesys.md) | Plain-English guide for the driver. |
| `S:/Software/EPS-LKAS`: `CLAUDE.md`, `HANDOFF.md`, `docs/SP-PROTOCOL.md` (SP-PROTOCOL v1/v2, which the "SP_HUD_STATUS v2" comments in `custom.capnp` refer to), `docs/SP-PROTOCOL-V3.md`, `docs/CAN-UPDATE.md`, `docs/UPDATING-FROM-THE-COMMA.md`, `docs/EPS-FAULT-STATES.md`, `dbc/eps-lkas-gw.dbc`, `tools/bundle_appslot.py` | The board's side of every contract above. |
| [`opendbc_repo/FORK.md`](https://github.com/SoRadGaming/opendbc/blob/sp-master/FORK.md) (local: `opendbc_repo/FORK.md`) | Short index for the opendbc fork on its own. |

---

## How the car gets updates

The comma does **not** install from this repo. The comma installer and updater only speak
`github.com/<user>/openpilot`, so the device was installed from `install.soradgaming.com/fork/SoRadGaming/sunnypilot`
and tracks **`SoRadGaming/openpilot`, branch `sunnypilot`**:

```
SoRadGaming/sunnypilot  master ──(its CI: "tests" workflow)──►  mirror-sunnypilot.yaml  ──►  SoRadGaming/openpilot  sunnypilot ──► the comma
                        pre-*  ─────────────────────────────►   (in SoRadGaming/openpilot,  ──►                        pre-*      ──► branch picker
                                                                  on its master branch)
```

The mirror workflow (`.github/workflows/mirror-sunnypilot.yaml` on `SoRadGaming/openpilot` **master**, which is otherwise
the owner's old openpilot longitudinal fork and must not be written) runs on a best-effort 15-minute schedule. GitHub
throttles it, and in practice it has run every 3–6 hours. Its rules, all documented at the top of the file:

* **`sunnypilot` moves only for a commit that built.** The jobs `build release` and `unit tests` of this repo's `tests`
  workflow must have succeeded for that exact commit. A commit with no CI run is held, not passed. `force` on a manual
  run skips the gate.
* **Fast-forward only**, and every push is leased. A hand push or rollback on `sunnypilot` is held rather than
  overwritten; `overwrite` on a manual run replaces it.
* **`pre-*` branches here are mirrored under the same name, create-only**, so the device's branch picker offers them as
  rollbacks. `pre-upstream-2026-09` is the fork as it was before the 2026-09 sync.
* **Upstream syncs change `.github/workflows/`, and GitHub's Actions token may not push that.** The run then goes red and
  its summary gives the two commands to push it by hand. Alternatively, add a `MIRROR_TOKEN` secret to
  `SoRadGaming/openpilot` (a fine-grained token for that repo only, with Contents and Workflows read and write), and the
  mirror retries with it.

After pushing master, start the mirror instead of waiting for the schedule. A manual run waits up to 20 minutes for the CI
to finish:

```bash
gh workflow run mirror-sunnypilot.yaml -R SoRadGaming/openpilot
```

Then check what the car will get:

```bash
git ls-remote https://github.com/SoRadGaming/openpilot.git sunnypilot
```

This repo's own CI also runs `static analysis`, which fails on the 22 ruff findings the fork carried in from before the
sync (card.py, board.py, eps_lkas_flasher.py, eps_lkas_hook.py). The mirror does not wait on that job.

---

## Where the forks stand (2026-09-27, after the sync)

| | sunnypilot | opendbc |
|---|---|---|
| fork repo / branch | `SoRadGaming/sunnypilot` `master` | `SoRadGaming/opendbc` `sp-master` |
| fork HEAD | branch `merge/upstream-2026-09-27`: `6b6b2b31e` (upstream merged into fork `2cfcd3c6a`), then `d1a14edcb` (fork `8f64c4ad0`, a changelog commit, merged in), then the review-fix commit that also carries these documents. It becomes `master` when it lands (Step 8). | `8bd6e314` (upstream merged into fork `c61cfd9b`), then the review-fix commit. It becomes `sp-master`, and sunnypilot pins it. |
| upstream | `sunnypilot/sunnypilot` `master` → `refs/upstream/master` = `a5f44653d` (2026-09-14) | `sunnypilot/opendbc` `master` → `refs/upstream/master` = `f95f996f` (2026-09-02) |
| **fork point** (merge-base) | **`a5f44653d`**: upstream's head itself, because it was merged | **`f95f996f`** |
| upstream commits not in the fork | 0 (on 2026-09-27) | 0 |
| fork commits since the fork point | 71 at `d1a14edcb` (68 excluding merges). A merge keeps history, so this is every fork commit since the old fork point plus the two merge commits; it is not a measure of how much the fork carries. Use the diff for that. | 40 at `8bd6e314` (39 excluding merges) |
| files the fork changes | 45 code files under `openpilot/`, 10 documents, `.gitmodules` and the `opendbc_repo` pointer | 31 code files and `FORK.md` |
| previous fork point | `31dc4d8e5` (2026-06-28) | `b9712d20` (2026-06-08) |

The submodule pointer upstream pins is `f95f996f`, which is upstream opendbc's head, so the two forks are again in step
with each other.

**Where the work was done.** The sync was done in a WSL clone, `~/sp-merge` (Ubuntu-24.04), with remotes `origin`
(`SoRadGaming/sunnypilot`) and `win` (`/mnt/s/OP/sp-live`, the Windows checkout). Its `opendbc_repo` fetches every
branch, so `origin/sp-master` exists there. See [Working in WSL](#working-in-wsl).

**The Windows opendbc clone does not track `sp-master`.** `S:/OP/sp-live/opendbc_repo` is a submodule clone that fetches
only `master` (`remote.origin.fetch = +refs/heads/master:refs/remotes/origin/master`). There is no local
`origin/sp-master`, and `origin/HEAD` points at `origin/master` = `fe144714`, which is the fork's GitHub default branch
and **not** the branch sunnypilot uses. Check the remote with `git ls-remote origin sp-master`, and push with the
explicit `git push origin sp-master`.

### Reproducing these numbers

Upstream is fetched into a private ref namespace rather than a remote, so `git fetch origin` never touches it:

```bash
cd ~/sp-merge
git fetch --no-tags https://github.com/sunnypilot/sunnypilot.git +master:refs/upstream/master
git -C opendbc_repo fetch --no-tags https://github.com/sunnypilot/opendbc.git +master:refs/upstream/master

git merge-base HEAD refs/upstream/master                         # a5f44653d until upstream moves on
git -C opendbc_repo merge-base HEAD refs/upstream/master         # f95f996f

git rev-list --count refs/upstream/master..HEAD                  # 71 at d1a14edcb
git rev-list --count HEAD..refs/upstream/master                  # 0 on 2026-09-27; this is the next merge's size
git -C opendbc_repo rev-list --count refs/upstream/master..HEAD  # 40 at 8bd6e314
git -C opendbc_repo rev-list --count HEAD..refs/upstream/master  # 0 on 2026-09-27

git diff --name-status refs/upstream/master HEAD                 # the change set
git -C opendbc_repo diff --name-status refs/upstream/master HEAD

# conflict risk of one file: upstream commits that touched it since the fork point
MB=$(git merge-base HEAD refs/upstream/master)
git rev-list --count $MB..refs/upstream/master -- openpilot/selfdrive/car/card.py
MB=$(git -C opendbc_repo merge-base HEAD refs/upstream/master)
git -C opendbc_repo rev-list --count $MB..refs/upstream/master -- opendbc/car/honda/values.py
```

Every fork path is under `openpilot/` now, so one path per file is enough. If upstream moves a tree again, give both
the old and the new path, as the 2026-09 guide had to.

**Windows / Git Bash:** run `export MSYS_NO_PATHCONV=1` before any command with a `ref:path` argument, for example
`git show refs/upstream/master:openpilot/cereal/custom.capnp`, and before any `wsl.exe` command line with a `/mnt/c`
path. Without it, MSYS rewrites the argument into a Windows path and git answers "Not a valid object name". Paths
passed after `--` are not affected.

### Previewing the next merge without touching the repos

`git merge-tree` computes the merge with no working tree and no index. Run it in a throw-away shared clone so the merge
objects are written there and not into the real repos:

```bash
P=/tmp/merge-preview; rm -rf $P
git clone -q --shared --bare ~/sp-merge $P/sp.git
git -C $P/sp.git fetch -q ~/sp-merge refs/upstream/master:refs/upstream/master HEAD:refs/fork/head
git -C $P/sp.git merge-tree --write-tree --name-only refs/fork/head refs/upstream/master

git clone -q --shared --bare ~/sp-merge/opendbc_repo $P/odbc.git
git -C $P/odbc.git fetch -q ~/sp-merge/opendbc_repo refs/upstream/master:refs/upstream/master HEAD:refs/fork/head
git -C $P/odbc.git merge-tree --write-tree --name-only refs/fork/head refs/upstream/master
# first output line is a tree id; `git -C $P/sp.git show <tree>:<path>` prints a file with its conflict markers
```

The "Last merge" column below comes from exactly this, re-run on the two commits the 2026-09 merge actually joined
(`2cfcd3c6a` with `a5f44653d`, and `c61cfd9b` with `f95f996f`).

---

## The 2026-09 sync: what was done

The pre-merge version of this section listed what upstream had changed underneath the fork. All of it is now handled.
The behaviour side (what the driver sees) is in [UPSTREAM-2026-09.md](UPSTREAM-2026-09.md); this is the code side.

**sunnypilot**

* **Nested `openpilot/` layout** (`5edc0bd89`, `37eda06c9`). Every fork code path moved under `openpilot/`. The 13
  files the fork had added outside `docs/` were file-location conflicts and were accepted at git's `openpilot/` path.
  The three old `openpilot/common`, `openpilot/selfdrive`, `openpilot/sunnypilot` symlinks were file/directory
  conflicts and resolved to upstream's real directories. `selfdrive/modeld/modeld.py` was a modify/delete: the fork's
  three edits were re-applied by hand to `openpilot/selfdrive/modeld/modeld.py`. `docs/`, `CHANGELOG-elesys.md` and
  `FEATURES-elesys.md` did not move.
* **Imports.** `from openpilot.cereal import ...`, `openpilot.common.hardware`, and the `./pandad` working directory
  `openpilot/selfdrive/pandad`.
* **Renamed cereal services** (`6d5d1f691`). The fork's `SubMaster` lists in `controlsd.py`, `selfdrived.py` and both
  `modeld.py` files took upstream's names and re-added `carStateSP`.
* **`IsOnroad` is gone** (`ad5151b38`). `eps_lkas_hook.py` reads `not params.get_bool("IsOffroad")` at both sites.
* **pytest is gone** (`98e7c4f98`, `ac4ab9a9b`). Every fork test is a `unittest.TestCase` or `OpenpilotTestCase` and is
  collected by `tools/test_runner.py`. See [Tests](#tests).
* **Fonts load at runtime** (`96ca1f8ed`). The glyph test reads `EXTRA_FONT_CHARS` from
  `openpilot/system/ui/lib/application.py`.
* **Lane change rewritten** (`7d325d665`, `4532320fb`, `2d859a8ca`). Upstream's structure, plus `NUDGE_FIRM`, plus
  `driver_torque_stale` as the last `DesireHelper.update()` parameter, passed by keyword.
* **Longitudinal.** Upstream removed per-car stopping tunes (`fdd1df79f`, `031b1ad0a`). This car keeps 0.8 m/s and
  0.8 m/s³ through the new `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py`, read by `drive_helpers.should_stop()`,
  the planner, `LongControl`, `joystickd.py` and `maneuversd.py`. The stopping-exit debounce was re-applied on the
  stopping → pid edge (there is no `starting` state any more).
* **`LaC.update()`** returns `steer, lateral_output, lac_log`; the fork's gateway lines sit before it.
* **MADS.** The gateway-pause fix from `2cfcd3c6a` plus upstream's brake/regen guard (`79b79edd2`), and an enable-frame
  fix in `mads.py` and `state.py` found in review.
* **mici settings.** `back_callback` is gone from every fork page (`099143ad9`); the vehicle and gateway rows are
  inserted at 2 and 3, after upstream's own inserts.
* **Review fixes after the merge:** the Lateral Jerk / NNLC extension now respects the gateway integrator hold
  (`latcontrol_torque_ext_base.py`); `joystickd.py` and `maneuversd.py` pass the car's stopping speed; the stopping ramp
  is exactly `float32(0.8)`; the eight `HondaDyn*` sunnylink info rows dropped `step: 0.001`, which upstream's schema
  test rejects; the 9 `ty` errors upstream's stricter rules found in fork tests are fixed.

**opendbc**

* **Honda platform sets** (`57506094`). `HONDA_ELESYS = frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)`
  sits after `HONDA_BOSCH_CANFD`; the imports of the removed `HONDA_NIDEC_ALT_*` and `HONDA_BOSCH_TJA_CONTROL` sets are
  gone.
* **Signatures** (`045cd8d3`). `compute_gas_brake(accel, speed, CP)` has an `elif CP.carFingerprint in HONDA_ELESYS`
  branch. `create_brake_command(..., stock_brake, CP_SP, is_metric=True, elesys=False)` takes the fork's two arguments
  last, by keyword.
* **`vEgoStopping`** is deleted from `interface.py` (it is `CarParams.deprecated` and assigning it raises).
* **`minEnableSpeed`** (`4455464a`): `_get_params_sp()` keeps 19 mph for `HONDA_ELESYS`.
* **Tests.** `test_elesys.py` uses the new signatures. The integration script's §10 now sets `mads.enabled` and pins
  both cases, §15 passes `driver_torque_stale=` by keyword, and a `TestCase` wrapper lets discovery report it.
  `safety/tests/common.py` exempts `0x500` between the two `TestHondaElesys*` classes only.

---

## Inventory

Area: **A** gateway update, **B** LKAS gateway protocol, **C** the car. **—** means repository plumbing that fits none
of the three; it is covered in this file.

**Upstream commits** is the conflict risk: the number of upstream commits that touched the file since the fork point.
The fork point is now upstream's own head, so on 2026-09-27 it is **0 for every file**. The column is kept for the next
merge: re-run the command in [Reproducing these numbers](#reproducing-these-numbers) after fetching upstream, and
anything above 0 is where the next merge can conflict.

**Last merge** is what happened to the file in the 2026-09 merge:

* *clean*: upstream never touched the file.
* *moved*: only upstream's rename touched it.
* *auto*: git merged both sides without conflict.
* *new→moved*: the fork added the file in a directory upstream renamed. Git reported a file-location conflict and
  proposed the `openpilot/` path, which was accepted.
* *CONFLICT*: a content conflict, resolved by hand.
* *modify/delete*: upstream deleted the old path; the fork's edits were re-applied to the new one.
* *added in the merge*: the file carries a fork change for the first time, made during or after the merge.

### sunnypilot: 67 files and the submodule pointer

Area **SL** is the speed-limit hardening of 2026-09-29 (`2e7866503`): not car-specific, gated by `SpeedLimitMapStrict` and `OsmAutoUpdateWeekly`, markers `FORK(SPEED-LIMIT)`.

| St | Path | Area | What the fork changes | Upstream commits | Last merge |
|---|---|---|---|---|---|
| M | `.gitmodules` | — | Points the `opendbc` submodule URL at `SoRadGaming/opendbc` and adds `branch = sp-master`. | 0 | auto |
| M | `opendbc_repo` (gitlink) | — | Pins the merged `sp-master`, not upstream's `f95f996f`. | 0 | submodule conflict |
| A | `CHANGELOG-elesys.md` | C | History of the longitudinal work. | 0 | clean |
| A | `FEATURES-elesys.md` | C | Plain-English guide for the driver. | 0 | clean |
| A | `docs/CHANGELOG_SERIAL_STEERING.md` | B (+A) | Serial-steering and gateway changelog. | 0 | clean |
| A | `docs/SP_GATEWAY_FIRMWARE.md` | B | Board-side receive spec. | 0 | clean |
| A | `docs/SP_HUD_STATUS.md` | B | SP-PROTOCOL, openpilot side. | 0 | clean |
| A | `docs/fork/README.md`, `GATEWAY-UPDATE.md`, `LKAS-GATEWAY-PROTOCOL.md`, `CAR-HONDA-ACCORD-9G-AU.md` | — | These documents. | 0 | clean |
| A | `docs/fork/UPSTREAM-2026-09.md` | — | What the 2026-09 sync brought. | 0 | added in the merge |
| M | `openpilot/cereal/custom.capnp` | A+B | Adds `CarControlSP.lateralControl @5` (B), `CarStateSP.linbusGateway @1` (fields @0–@18 B, @19–@26 A) and `CarStateSP.driverTorqueStale @2` (B). | 0 | auto |
| M | `openpilot/common/params_keys.h` | A+B+C+SL | Adds 7 `EpsLkas*` keys (A), 9 `HondaDyn*` keys (C), and `SpeedLimitMapStrict`, `OsmAutoUpdateWeekly`, `OsmLastCompleteDate` (SL), and `MadsEmergencySteerDisable`, `MadsEmergencySteerRate` (B, the fast-wheel takeover's settings). | 0 | auto |
| M | `openpilot/selfdrive/car/card.py` | A+C | A: `stage_board_firmware()`, `write_board_firmware()` and `log_flash_trace()`, called from `params_thread` (staging from `state_publish`). C: `get_car(..., skip_fw_query=bool(fixed_fingerprint))`. | 0 | auto |
| M | `openpilot/selfdrive/car/helpers.py` | B | `convert_carControlSP()` rebuilds `lateralControl`. | 0 | auto |
| A | `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | B (+A) | Every nested `CarControlSP` struct, and the firmware fields, through the capnp→dataclass seam (1 test). | 0 | new→moved |
| M | `openpilot/selfdrive/controls/controlsd.py` | B | Subscribes `carStateSP`, calls `LaC.set_linbus_gateway(present, actuating)` before `LaC.update()`, and calls `run_ext(sm, pm, lac_log, LaC)`. | 0 | CONFLICT |
| M | `openpilot/selfdrive/controls/lib/desire_helper.py` | B+C | B: `update(..., driver_torque_stale=False)` last, by keyword. C: `NUDGE_FIRM`, `NUDGE_HOLD_FRAMES` and `DesireHelper(car_fingerprint)`. | 0 | CONFLICT |
| M | `openpilot/selfdrive/controls/lib/drive_helpers.py` | C | `should_stop(v_ego, a_target, v_ego_stopping=None)`: a per-car stopping speed; `None` keeps upstream's 0.3. | 0 | added in the merge |
| M | `openpilot/selfdrive/controls/lib/latcontrol.py` | B | Adds `LINBUS_I_CARRY_MAX`, `LINBUS_I_HOLD_TAU`, `set_linbus_gateway()`, `_linbus_integrator_gate()` and `integrator_frozen`. | 0 | auto |
| M | `openpilot/selfdrive/controls/lib/latcontrol_torque.py` | B | `freeze_integrator ... or linbus_hold`. | 0 | auto |
| M | `openpilot/selfdrive/controls/lib/longcontrol.py` | C | Stopping-exit debounce (`STANDSTILL_SPEED`, `STOPPING_EXIT_DEBOUNCE`), keyed on `HondaDynamicTuningEnabled`; the stopping ramp from `STOPPING_DECEL_RATE`. | 0 | CONFLICT |
| M | `openpilot/selfdrive/controls/lib/longitudinal_planner.py` | C | Passes `v_ego_stopping=STOPPING_SPEED.get(CP.carFingerprint)` to both `should_stop()` calls. | 0 | added in the merge |
| A | `openpilot/selfdrive/controls/tests/test_stopping_debounce.py` | C | The debounce and the stopping tune (17 tests). | 0 | new→moved, then rewritten |
| M | `openpilot/selfdrive/modeld/modeld.py` | B+C | Subscribes `carStateSP`, calls `DesireHelper(CP.carFingerprint)` and passes `driver_torque_stale=` into `DH.update`. | 0 | modify/delete |
| M | `openpilot/selfdrive/pandad/pandad.py` | A | Adds `flash_if_requested()` before `./pandad`, `watch_for_request()` after it, and `skip_panda_reset`. | 0 | CONFLICT |
| M | `openpilot/selfdrive/selfdrived/selfdrived.py` | B | Subscribes `carStateSP`, which `mads.py` reads. | 0 | CONFLICT |
| M | `openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py` | C | Honda dynamic-learning toggle on the Cruise panel. | 0 | moved |
| M | `openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py` | C | `HondaSettings`: toggle, learned values and reset (`LEARNED_DEFAULTS`, `PEDAL_GAIN_BP`, `reset_learned_values`). | 0 | moved |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/board.py` | A | Settings > gateway page: `BoardLayoutMici`, `UpdateBoardButton`, `board_page_visible`, `bundled_firmware`. | 0 | new→moved |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/maps.py` | SL | Settings > maps: `MapsLayoutMici`, `MapDataInfo` (one card per data set), `UpdateOsmButton` (parked only; writes `OsmDbUpdatesCheck`), the "update weekly" toggle. | 0 | new |
| M | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | A+C+SL | Adds a "vehicle" row (C) and a "gateway" row (A) with `items.insert(2, ...)` and `items.insert(3, ...)`, a form two tests pin, and a "maps" row (SL) with `items.insert(4, ...)`. | 0 | CONFLICT |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` | C (+B) | mici vehicle page: `VehicleLayoutMici`, `car_brand()`, `HondaLearnedInfo`; and (B) the fast-wheel rows, "off on swerve" and "swerve at", the rate (`FastWheelRateToggle`). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py` | A | Flasher protocol, image checks and trace (26 tests). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py` | A | pandad hook ordering, the onroad refusal and param registration (10 tests). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_gateway_board_settings.py` | A (+B) | DBC, capnp, params, page and button gates (25 tests), including `test_lat_ready_means_lateral_is_enabled_not_merely_possible` (B). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` | C | Params, UI and sunnylink in sync with the tuner (11 tests). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_mads_fast_wheel_settings.py` | B | The fast-wheel settings in agreement across `params_keys.h`, `mads.py`, the mici page and sunnylink (15 tests). | 0 | new |
| A | `openpilot/selfdrive/ui/tests/test_maps_settings.py` | SL | The maps page contract: gates, confirm flow, dates, glyphs (source-parsing tests). | 0 | new |
| M | `openpilot/sunnypilot/mads/mads.py` | B | Pauses on a gateway driver override (`LINBUS_REASON_DRIVER_OVERRIDE`, `_gw_paused`), holds the pause in `should_silent_lkas_enable()`, and also fires on the frame MADS is turned on. Adds the fast-wheel disable (`EMERGENCY_STEER_RATE` 200, `EMERGENCY_STEER_FRAMES` 2), which applies to **every** car; since 2026-09-30 a setting (`MadsEmergencySteerDisable`, default on; `MadsEmergencySteerRate` 150/200/250/300, default 200), read in `__init__` and `read_params()`. | 0 | CONFLICT |
| M | `openpilot/sunnypilot/mads/state.py` | B | DISABLED branch: an ENABLE that arrives with `silentLkasDisable` goes to `paused`. | 0 | added in the merge |
| A | `openpilot/sunnypilot/mads/tests/test_mads_gateway_pause.py` | B | The gateway pause, resume, brake modes, emergency and enable-frame cases, and the fast-wheel settings (41 tests). | 0 | new→moved |
| M | `openpilot/sunnypilot/mapd/mapd_manager.py` | SL | Three marked lines: runs `OsmAutoUpdater` each tick and clears `OsmLastCompleteDate` with the maps. | 0 | new |
| A | `openpilot/sunnypilot/mapd/osm_auto_update.py` | SL | `auto_update_due()` (pure) and `OsmAutoUpdater`: weekly refresh when parked (no ignition on any panda) on unmetered wi-fi/ethernet, once per boot; `record_completion()` writes `OsmLastCompleteDate`. | 0 | new |
| A | `openpilot/sunnypilot/mapd/tests/test_osm_auto_update.py` | SL | The refresh decision and the completion recorder. | 0 | new |
| M | `openpilot/sunnypilot/modeld_v2/modeld.py` | B+C | The same three edits as `modeld.py`. | 0 | CONFLICT |
| M | `openpilot/sunnypilot/selfdrive/controls/controlsd_ext.py` | B | Fills `CC_SP.lateralControl` from `lac_log` and `LaC`. | 0 | auto |
| M | `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_ext_base.py` | B | `update_output_torque()` also freezes on the owning controller's `integrator_frozen`. | 0 | added in the merge |
| M | `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py` | B | The same integrator hold as `latcontrol_torque.py`. | 0 | auto |
| M | `openpilot/sunnypilot/selfdrive/controls/lib/longitudinal_planner.py` | SL | Passes `resolver.map_limit_frozen` into `SpeedLimitAssist.update()`. | 0 | new |
| M | `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_assist.py` | SL | With `SpeedLimitMapStrict`: prompts only for a real new limit (`_last_nonzero_limit`), no prompt when engaging on a carried or frozen limit, PCM ADAPTING with no limit goes ACTIVE. | 0 | new |
| M | `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_resolver.py` | SL | With `SpeedLimitMapStrict`: `MAP_HOLD_TIMEOUT` (drop a carried map limit after 10 s untagged with good GPS), GPS-loss freeze held until the road name changes or `MAP_GPS_SETTLE_TIME` after GPS returns, `map_limit_frozen`. `sm.valid` is read only in strict mode. | 0 | new |
| M | `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_speed_limit_assist.py`, `test_speed_limit_resolver.py` | SL | The existing suites run with strict on (base) and off (subclasses). | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_speed_limit_map_strict.py` | SL | Resolver + SLA + cruise on the non-PCM path: carried limit, junction gap, engage untagged, tunnels, PCM contrast. | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py` | C | `STOPPING_SPEED` and `STOPPING_DECEL_RATE`, keyed by fingerprint. | 0 | added in the merge |
| A | `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | C (+B) | `NUDGE_FIRM` rules; a stale torque confirms nothing; `driver_torque_stale` comes after the road edges (9 tests). | 0 | new→moved |
| A | `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_latcontrol_gateway_hold.py` | B | The hold through the torque-controller extension with Lateral Jerk on (4 tests). | 0 | added in the merge |
| A | `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin` | A | Board app-slot image: 46,540 bytes, marker `APL1`, origin `0x08004000`, commit `d995bc95`, flags `0x04` (INCAR_TEST). | 0 | new→moved |
| A | `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_flasher.py` | A | Portable bootloader protocol, `PandaTransport` (ELM327), `BenchTransport`, and the steering/vibration trace. | 0 | new→moved |
| A | `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_hook.py` | A | pandad glue: `flash_if_requested()`, `watch_for_request()`. | 0 | new→moved |
| M | `openpilot/sunnypilot/sunnylink/settings_ui.json` | C+B | Compiled output of the YAML files below. | 0 | auto, then recompiled |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml` | C+SL | `honda_dynamic_learning` read-only info section (C); the "Strict Map Speed Limits" toggle in the speed limit settings (SL). | 0 | moved |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/steering.yaml` | B | MADS Settings: "Turn Off Steering on a Fast Wheel" (`MadsEmergencySteerDisable`) with "Fast Wheel Threshold" (`MadsEmergencySteerRate`) under it. | 0 | new |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/vehicle.yaml` | C | `honda` section with the toggle. | 0 | auto |
| M | `openpilot/sunnypilot/sunnylink/statsd.py` | C | Reports `HondaDynamicTuningEnabled` and the 8 learned values. | 0 | auto |
| M | `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py` | C (Other) | Reads and writes UTF-8 with an LF newline, so compiling on Windows matches CI. | 0 | moved |
| M | `openpilot/tools/joystick/joystickd.py` | C | Passes the car's stopping speed to `should_stop()`. | 0 | added in the merge |
| M | `openpilot/tools/longitudinal_maneuvers/maneuversd.py` | C | Parses `CarParams` and passes the car's stopping speed to `should_stop()`. | 0 | added in the merge |

### opendbc: 31 files and `FORK.md`

| St | Path | Area | What the fork changes | Upstream commits | Last merge |
|---|---|---|---|---|---|
| A | `FORK.md` | — | Short fork index. | 0 | clean |
| M | `opendbc/car/car_helpers.py` | C | `skip_fw_query` argument on `fingerprint()` and `get_car()`. | 0 | auto |
| M | `opendbc/car/honda/carcontroller.py` | B+C | B: brake-release ceiling (`BRAKE_RELEASE_FRAMES`, `brake_release_scale`), `serial_gateway` LDW bits, `SP_HUD_STATUS` send with `lat_ready` and `op_state`, `LKAS_HUD` not sent. C: `compute_gb_honda_elesys` (dispatched from `compute_gas_brake(accel, speed, CP)`), `brake_pump_hysteresis_elesys` and `ELESYS_PUMP_*`, dynamic-tuner hooks (`hill_accel`/`adjust_accel`, `brake_gain`, `wind_scale`, the 32-count brake release), `SCM_BUTTONS` re-sent on `CAN.camera` every 4th frame when `openpilotLongitudinalControl`, `pcm_accel` computed from `adjust_accel`, and a `FORK:` comment explaining why there is no PCM crossfade. | 0 | CONFLICT |
| M | `opendbc/car/honda/carstate.py` | A+B+C | A/B: registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION` and `GW_BUILD` liveness-exempt (`nan`), and calls `CarStateExt.update(ret, ret_sp, ...)`. C: `update_gear_elesys` / `SPORT_DWELL`, ELESYS `stockAeb` (and `carFaultedNonCritical = True` when stock AEB fires with `ACC_HUD.ACC_ON == 0`), `LKAS_PROBLEM` read from bus 0 inside upstream's `if not (self.CP.flags & HondaFlags.BOSCH):`, `scm_buttons`, `econ_on`. | 0 | CONFLICT |
| M | `opendbc/car/honda/fingerprints.py` | C | `FW_VERSIONS[HONDA_ACCORD_9G_AU]`: fwdRadar `36707-T2M-Q640`, srs `77959-T2A-B110`. | 0 | auto |
| M | `opendbc/car/honda/hondacan.py` | B+C | B: `create_steering_control(serial_gateway, ldw_left, ldw_right)`, `SP_HUD_PROTOCOL_VERSION`=3, `SP_OP_STATE_*`, `SP_HUD_MAX_TORQUE`=0, `create_sp_hud_status()`. C: `create_brake_command(..., is_metric=True, elesys=False)` units bit, `create_scm_buttons_no_cruise()`. | 0 | CONFLICT |
| M | `opendbc/car/honda/interface.py` | C (+B) | Gearbox `0x188` → automatic. Long tuning: `longitudinalActuatorDelay` 0.6, `stopAccel` -0.8 (the stopping speed lives in sunnypilot's `stopping_tune.py`). `steerActuatorDelay` 0.38, `steerAtStandstill` True, `ELESYS_SCM_STANDDOWN` safety parameter, `minEnableSpeed` 19 mph, and its exemption from the gas-interceptor -1 in `_get_params_sp()`. | 0 | CONFLICT |
| M | `opendbc/car/honda/radar_interface.py` | C | Elesys radar parser (`0x400`, `0x410`–`0x417`, `0x420`–`0x424` at 10 Hz), trigger `0x423`, `RADAR_STATE` ok in (104, 111, 125). | 0 | auto |
| A | `opendbc/car/honda/tests/test_elesys.py` | C | 52 unittest tests: gas/brake map, pump, gas curve, units bit, gear, AEB. | 0 | clean |
| M | `opendbc/car/honda/values.py` | C | `HondaSafetyFlags.ELESYS_SCM_STANDDOWN`=32, `HondaFlags.ELESYS`=1024, `CAR.HONDA_ACCORD_9G_AU`, `HONDA_ELESYS` (a frozenset), `STEER_THRESHOLD` 600, `non_essential_ecus`. | 0 | CONFLICT |
| M | `opendbc/car/structs.py` | A+B | `CarControlSP.LateralControl`, `CarStateSP.driverTorqueStale`, `CarStateSP.LinbusGateway` (control fields and `fw*` fields). | 0 | auto |
| M | `opendbc/car/tests/routes.py` | C | `CarTestRoute("15646e8515eda1a7/00000019--dd0700eac9", HONDA_ACCORD_9G_AU)`. | 0 | auto |
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
| M | `opendbc/safety/modes/honda.h` | C (+B) | `ELESYS_SCM_STANDDOWN` (param 32): TX lists with `0x1A6` on bus 2 and `0x500` on bus 0 (B), and **without** `0x33D`; AEB bit 43; the `pcm_gas` 198 exception; blocking `0x1A6` bus 0→2; `honda_bosch_init()` resets `honda_elesys_scm_standdown = false`. | 0 | clean |
| M | `opendbc/safety/tests/common.py` | C (+B) | Scanned-range exceptions for `TestHondaElesys` and `0x1A6`, and `0x500` between the two `TestHondaElesys*` classes only. | 0 | auto |
| M | `opendbc/safety/tests/test_honda.py` | C | `TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety`. | 0 | clean |
| M | `opendbc/sunnypilot/car/car_list.json` | C | `"Honda Accord 2013-15"` → `HONDA_ACCORD_9G_AU`. | 0 | auto |
| M | `opendbc/sunnypilot/car/honda/carstate_ext.py` | A+B+C | A: `_update_linbus_firmware`. B: `_update_linbus_gateway`, `_update_linbus_grant`, `_update_driver_torque_validity`, `_eps_lin_driver_torque_valid`. C: `fuelGauge`. | 0 | auto |
| A | `opendbc/sunnypilot/car/honda/dynamic_tuning.py` | C | `HondaDynamicTuner` (self-learning longitudinal). | 0 | clean |
| M | `opendbc/sunnypilot/car/honda/gas_interceptor.py` | C | Imports `HONDA_ELESYS`; `ELESYS_GAS_BP`/`ELESYS_GAS_V`, `elesys_gas_multiplier()`, and the `tuner` hooks (`pedal_gain_at`, `update_pedal`). | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | C | Script-style tests of the tuner. | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | C+B | Script-style checks through `CarController`, with a `TestCase` wrapper. Sections [7], [8] and [10]–[15] are B. | 0 | clean, then edited for the new signatures |

**2026-09 merge summary.**

* **sunnypilot:**
  * 8 content conflicts: `controlsd.py`, `desire_helper.py`, `longcontrol.py`, `pandad.py`, `selfdrived.py`, mici
    `settings.py`, `mads.py` (the fork's `2cfcd3c6a` against upstream's `79b79edd2`), `modeld_v2/modeld.py`.
  * 1 modify/delete: `selfdrive/modeld/modeld.py`.
  * 1 submodule conflict: `opendbc_repo`.
  * 13 file-location conflicts: every file the fork had added outside `docs/` and the two root `.md` files.
  * 3 file/directory conflicts: the old `openpilot/common`, `openpilot/selfdrive` and `openpilot/sunnypilot` symlinks
    against upstream's real directories.
  * Everything else merged or moved cleanly.
* **opendbc:** 5 content conflicts, all in `opendbc/car/honda/`: `carcontroller.py`, `carstate.py`, `hondacan.py`,
  `interface.py`, `values.py`.

---

## Collision checks

Checked on 2026-09-27 in the merged trees. They cover what a merge can break without a textual conflict. Repeat them
after every merge.

### `openpilot/cereal/custom.capnp`

| struct | fork adds | upstream's highest ordinal at `a5f44653d` | collision |
|---|---|---|---|
| `CarControlSP` | `lateralControl @5 :LateralControl`. Nested `LateralControl { integrator @0 :Float32; saturated @1 :Bool; integratorFrozen @2 :Bool }` | `@4` (`intelligentCruiseButtonManagement`) | **none** |
| `CarStateSP` | `linbusGateway @1 :LinbusGateway`, `driverTorqueStale @2 :Bool` | `@0` (`speedLimit`) | **none** |
| `CarStateSP.LinbusGateway` (fork-only) | `engaged @0`, `dryRun @1`, `valid @2`, `actuating @3`, `present @4`, `grantValid @5`, `grantState @6 :UInt8`, `grantReason @7 :UInt8`, `granted @8`, `authority @9 :UInt8`, `epsAck @10`, `epsLatched @11`, `epsErrorState @12 :UInt8`, `epsFresh @13`, `camLkasOn @14`, `applied @15 :Int16`, `motorTorque @16 :Int16`, `retryIn @17 :UInt8`, `latchedUntilKeyOff @18` (all B). `fwValid @19`, `fwGitHash @20 :UInt32`, `fwDirty @21`, `fwAppSlot @22`, `fwBootloader @23`, `fwReadOnly @24`, `boardUid @25 :UInt32`, `fwBuildValid @26` (all A). | n/a | none |

The merge kept every fork ordinal. No upstream struct is named `LateralControl` or `LinbusGateway`.

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

An end-to-end probe after the merge also pushed a `CarStateSP` with every gateway field set to a non-default value
(negative `Int16`s, a `UInt32` hash with the top bit set) through `convert_to_capnp()` without loss.

### `openpilot/common/params_keys.h`

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
| `MadsEmergencySteerDisable` | PERSISTENT, BACKUP | BOOL | "1" | B |
| `MadsEmergencySteerRate` | PERSISTENT, BACKUP | INT | "200" | B |

Upstream's file at `a5f44653d` has 264 entries and none of the 16 A and C names; the merged file has 280. The fork's two
blocks sit between stable neighbours: `HideVEgoUI`/`IntelligentCruiseButtonManagement` and
`InteractivityTimeout`/`IsDevelopmentBranch`. Upstream still defines the `FLOAT` and `JSON` types. The two B keys came
after that merge (2026-09-30) and sit inside upstream's `// MADS params` block, between `Mads` and
`MadsMainCruiseAllowed`. The 3 SL keys are named in the `params_keys.h` row of the file table above; the 5 NSW keys
are in [NSW-SPEED-ZONES.md](NSW-SPEED-ZONES.md).

**Why a name collision would be silent.** The table is an `std::unordered_map` initializer list. A duplicate key
compiles without complaint, and only one entry survives. After every merge, check:

```bash
grep -oE '\{"[A-Za-z0-9_]+"' openpilot/common/params_keys.h | sort | uniq -d    # must print nothing
```

`test_every_param_the_hook_and_the_page_use_is_registered` (`test_eps_lkas_hook.py`) also fails if a key the gateway
hook or page reads is missing from this file, which is how `IsOnroad` would have been caught.

### Flag bits and safety parameters (opendbc)

| identifier | fork value | upstream at `f95f996f` |
|---|---|---|
| `HondaFlags.ELESYS` | 1024 | the slot upstream marked "1024 is available"; free |
| `HondaSafetyFlags.ELESYS_SCM_STANDDOWN` | 32 | highest upstream is `BOSCH_CANFD` = 16; free |
| `HONDA_PARAM_ELESYS_SCM_STANDDOWN` (`honda.h`) | 32 | Nidec params 4 / SP 1, 2; Bosch 1, 2, 8, 16; free |
| `SAFETY_ELM327` (the flasher's panda mode) | 3 | still `3U` in `opendbc/safety/declarations.h` |

The Python flag and the C parameter must stay equal.

### DBC generator includes and CAN IDs

`honda_accord_au_2015_can.dbc` imports nine fragments:

* `_community.dbc`, `_nidec_common.dbc` and `_steering_sensors_c.dbc` are upstream-owned.
* The other six (`_honda_elesys_base`, `_lkas_hud_4byte`, `_nidec_scm_group_a_elesys`, `_steering_control_e`,
  `_gearbox_legacy`, `_sunnypilot_linbus_gw`) are fork-only, and nothing else imports them.

No includes collide.

**Drift risk.** `_honda_elesys_base.dbc`, `_nidec_scm_group_a_elesys.dbc` and `_steering_control_e.dbc` are copies of
`_honda_common.dbc`, `_nidec_scm_group_a.dbc` and `_steering_control_a.dbc`. Upstream fixes to the originals will not
reach this car. Upstream had 0 commits on any of those originals, or on the three upstream-owned imports, between the
old fork point and `f95f996f`. Diff them on every merge:

```bash
cd opendbc/dbc/generator/honda
diff _honda_common.dbc _honda_elesys_base.dbc
diff _nidec_scm_group_a.dbc _nidec_scm_group_a_elesys.dbc
```

In the merged tree these are the intended differences; anything else in the output is drift:

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
nothing to commit. The merged generated `honda_accord_au_2015_can_generated` is byte-identical to the pre-merge one.

### `.gitmodules` and the submodule pointer

The merged file keeps the fork's `opendbc` lines (`https://github.com/SoRadGaming/opendbc.git`, `branch = sp-master`)
and takes upstream's other changes: `msgq` from `sunnypilot/msgq`, and the `neural_network_data` submodule (the
section is still named `[submodule "sunnypilot/neural_network_data"]`) at path
`openpilot/sunnypilot/neural_network_data`. After merging, run
`git submodule sync && git submodule update --init --recursive`.

The pointer always conflicts. Resolve it to the merged `sp-master` commit, never to upstream's opendbc commit.

### Traps the 2026-09 merge hit

These merged with no conflict marker, or sat next to one as plain context, and then broke. All of them are fixed.
They are listed because the same kind of thing will happen again.

* **A param upstream deleted.** `eps_lkas_hook.py` read `IsOnroad`. Upstream's `Params` raises `UnknownKeyName`, the
  hook caught it, and the update feature would have died quietly. The tests passed because `FakeParams` accepts any
  key. There is now a registration test.
* **A field upstream deprecated.** `ret.vEgoStopping = 0.8` raises `AttributeError: struct has no such member` on
  upstream's `car.capnp`, so `_get_params()` would have thrown and card could not build the car.
* **opendbc context lines that named things upstream removed.** In the trial merge these sat outside the conflict
  markers: `elif fingerprint in HONDA_ELESYS:` below `compute_gas_brake`, `adjust_accel`, the whole
  `compute_gb_honda_elesys`, and `... if car_fingerprint in HONDA_ELESYS else 1` in `hondacan.py`. `ruff check` (F821)
  catches any that survive.
* **`HONDA_ELESYS` consumers that never conflict.** `radar_interface.py`, `gas_interceptor.py`, `carstate_ext.py` and
  `test_elesys.py` import `HONDA_ELESYS` and merge cleanly; they fail to import unless `values.py` re-creates it.
* **Positional arguments.** Upstream added two parameters to `DesireHelper.update()` in the slot the fork used. Passed
  positionally, `driver_torque_stale` landed in `left_edge_detected`, and integration §15 kept passing while testing
  the road-edge block instead of the stale-torque guard.
* **A constructor argument upstream removed.** `back_callback=` on `SunnylinkLayoutMici`/`ModelsLayoutMici` would have
  raised `TypeError` at UI start, and upstream no longer restarts a crashed UI.
* **A float that used to be a `Float32`.** Moving `stoppingDecelRate` into a Python table as `0.8` took the stopping
  ramp one step past `stopAccel` (-0.808 instead of -0.800). The table holds `float32(0.8)`.
* **Callers the decision did not list.** `joystickd.py` and `maneuversd.py` also call `should_stop()`; the fork had
  given them `CP.vEgoStopping`, and they needed the car's value too.
* **A second PID update.** Upstream's Lateral Jerk controller (and NNLC) runs the owning controller's PID again in
  `latcontrol_torque_ext_base.py`, which skipped the gateway integrator hold.
* **Upstream's own tests.** They build a `SubMaster` without `carStateSP`, so `mads.py` falls back to "no gateway" on a
  `KeyError`.
* **Test paths.** The tests keep `ROOT = Path(__file__).parents[3]`, which is now `openpilot/`, and use
  `REPO = parents[4]` for `opendbc_repo/`. `fonts/process.py` is gone (use `EXTRA_FONT_CHARS`), and `cantools` is in
  neither venv (use opendbc's `CANParser`).
* **Upstream's stricter checks.** `ty` found 9 errors in fork tests; sunnylink's schema test rejected `step` without
  `min`/`max`.

### Tests

**sunnypilot.** Every fork test is a `unittest.TestCase` or `OpenpilotTestCase`, is collected by `tools/test_runner.py`
(also `tools/op.sh test`), and does no work at import time. There are 121 tests in 9 modules:

| module | tests |
|---|---|
| `openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py` | 26 |
| `openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py` | 10 |
| `openpilot/selfdrive/ui/tests/test_gateway_board_settings.py` | 25 |
| `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` | 11 |
| `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | 1 |
| `openpilot/selfdrive/controls/tests/test_stopping_debounce.py` | 17 |
| `openpilot/sunnypilot/mads/tests/test_mads_gateway_pause.py` | 18 |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | 9 |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_latcontrol_gateway_hold.py` | 4 |

`test_stopping_debounce.py` no longer stubs `sys.modules`; it imports the real `longcontrol`, `drive_helpers` and
`stopping_tune`.

**opendbc.** `test_elesys.py` and the safety tests are ordinary unittest modules. The two tuner scripts are not:

* `test_dynamic_tuning_integration.py` still runs its checks when it is imported, and still monkeypatches
  `dt._open_params` at import. It no longer exits at import: `sys.exit(1)` runs only under `__main__`, and
  `TestDynamicTuningIntegration.test_all_checks_pass` asserts that the list of failures is empty, so unittest discovery
  reports a real pass or fail.
* `test_dynamic_tuning.py` still calls `sys.exit(1)` at import if a check fails. It passes today, so discovery is not
  affected, but a failure would show up as a module that failed to import. Wrap it the same way when it is next
  touched.

### Behaviour on this car after the sync

Where upstream changed something this car depended on, the merge kept this car's behaviour and gave every other car
upstream's:

* stopping at 0.8 m/s with a 0.8 m/s³ ramp (`stopping_tune.py`), and the stopping-exit debounce;
* `minEnableSpeed` 19 mph;
* the firm-or-held lane-change nudge on top of upstream's new lane-change logic;
* the gateway MADS pause, now also held while the brake is held in Pause mode (upstream's guard).

What does change on this car (DM timings and sounds, the cruise target outside the MPC, lagd, the screensaver, AGNOS
19.7 and a panda reflash, and more) is in [UPSTREAM-2026-09.md](UPSTREAM-2026-09.md), with what to check on the first
drive in [../CHANGELOG_SERIAL_STEERING.md](../CHANGELOG_SERIAL_STEERING.md) (2026-09-27, upstream sync).

---

## Merge procedure

This is the recipe for the **next** merge. It is written for the layout the fork has now; the notes on how each
conflicting file was resolved in 2026-09 describe the shape each fork hunk should end up in.

Merge, do not rebase. The fork's documents and changelogs cite fork commit hashes (sunnypilot `6a4f1f5`, `ef4f294`,
`2cfcd3c6a`; opendbc `43a98b9d`, `2cc16a02`, …), and a rebase would invalidate every one of them. (Board hashes such as
`d995bc95` live in `S:/Software/EPS-LKAS` and are not affected either way.)

### Working in WSL

Do the sunnypilot half on Linux or WSL. The build and most tests need it. The 2026-09 sync used
`~/sp-merge` in WSL Ubuntu-24.04 with its own venv (`~/sp-merge/.venv`).

* **Environment.** In every script:

  ```bash
  export PATH="$HOME/nosudo:$HOME/.local/bin:$PATH"
  cd ~/sp-merge && source .venv/bin/activate
  ```

  `~/nosudo/sudo` is a two-line wrapper, `exec /usr/bin/sudo -n "$@"`. tinygrad's device probe calls `sudo` during the
  build; with no terminal for a password it would hang, and with `-n` it fails at once and the probe moves on. Keep
  the wrapper for builds only.
* **Fetching from the Windows checkout.** `/mnt/s/OP/sp-live` is owned by a different user as far as WSL's git is
  concerned, and git refuses to serve it ("dubious ownership"). Run the remote side with the check off:

  ```bash
  git fetch --upload-pack="git -c safe.directory=* upload-pack" win master
  ```

* **Pushing.** A branch that contains upstream carries upstream's Git LFS pointers (models, sounds) whose objects the
  clone never downloaded, so the LFS pre-push hook fails trying to upload them. Push with
  `GIT_LFS_SKIP_PUSH=1 git push origin <branch>`. Before a device updates from that branch, check that a fresh clone of
  it can `git lfs pull` the models (not verified in 2026-09).
* **Calling WSL from Git Bash.** Put the commands in a script file and run it with
  `MSYS_NO_PATHCONV=1 wsl.exe -d Ubuntu-24.04 -- bash /mnt/c/.../script.sh`; keep `$` out of inline `-c` strings,
  which Git Bash expands first. If `wsl.exe` fails with `HCS_E_CONNECTION_TIMEOUT`, `wsl --shutdown` and try again.
* **Editing through `\\wsl.localhost\...`** drops the executable bit. After editing, `git diff --summary` must show no
  mode changes; `chmod 755` anything that was `100755` (for example `joystickd.py`, `maneuversd.py`, opendbc
  `interface.py`).

### Step 0: snapshot

```bash
git -C ~/sp-merge status --short; git -C ~/sp-merge/opendbc_repo status --short     # both must be empty
# fetch upstream as in "Reproducing these numbers"
D=$(date +%Y%m%d)
git -C ~/sp-merge tag fork/pre-merge-$D
git -C ~/sp-merge/opendbc_repo tag fork/pre-merge-$D
```

Run the merge preview, read upstream's changes for this car (write the next `UPSTREAM-<date>.md` the same way), and
record the baseline test results before changing anything. See Step 6 for the commands; the baseline has no known
failure today.

### Step 1: opendbc first

sunnypilot's tests read opendbc sources: `test_gateway_board_settings.py` reads `carcontroller.py`, `carstate.py`,
`carstate_ext.py`, `structs.py` and the gateway DBC. The pinned pointer must also exist on origin before sunnypilot
can point at it. So opendbc goes first.

```bash
cd ~/sp-merge/opendbc_repo
git switch sp-master && git switch -c merge/upstream-$D
git merge refs/upstream/master
```

Resolve conflicts in this order, because each file supplies names to the next. The shape each fork hunk has now:

1. **`values.py`.** `HondaFlags.ELESYS = 1024` and `HondaSafetyFlags.ELESYS_SCM_STANDDOWN = 32`; the `CAR` entry;
   `HONDA_ELESYS = frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)` after the `HONDA_BOSCH*` sets;
   `STEER_THRESHOLD[HONDA_ACCORD_9G_AU] = 600`; `CAR.HONDA_ACCORD_9G_AU` in the `Ecu.eps` and `Ecu.vsa`
   `non_essential_ecus` lists.
2. **`hondacan.py`.** `create_brake_command(..., stock_brake, CP_SP, is_metric=True, elesys=False)` with
   `imperial_unit = int(not is_metric) if elesys else 1`; `create_steering_control(..., serial_gateway=False,
   ldw_left=False, ldw_right=False)`; `create_scm_buttons_no_cruise()`; the `SP_*` constants and
   `create_sp_hud_status()`.
3. **`carstate.py`.** The `pt_msgs` registration; the `LKAS_PROBLEM` Elesys branch (reads `cp`) inside upstream's
   non-Bosch condition; `scm_buttons`/`econ_on`; the gear and `stockAeb` branches; `CarStateExt.update(ret, ret_sp, ...)`.
4. **`interface.py`.** The transmission test before upstream's manual fallback; the Elesys long-tuning block
   (`longitudinalActuatorDelay`, `stopAccel`; never `vEgoStopping`); `steerActuatorDelay`/`steerAtStandstill`; the
   safety parameter; `minEnableSpeed` 19 mph, and the `candidate not in HONDA_ELESYS` exemption on the gas-interceptor
   `-1` in `_get_params_sp()`.
5. **`carcontroller.py`.** `compute_gb_honda_elesys()` and the `elif CP.carFingerprint in HONDA_ELESYS` branch of
   `compute_gas_brake(accel, speed, CP)`; `adjust_accel = accel + hill_accel` before `compute_gas_brake`; the
   `create_brake_command(..., is_metric=CS.is_metric, elesys=self.CP.carFingerprint in HONDA_ELESYS)` call; the brake
   ceiling after `rate_limit`; the HUD branch; the dynamic-tuner hooks.
6. **Tests.** Keep `test_elesys.py` on the current signatures, and every `DesireHelper.update()` call in the integration
   script passing `driver_torque_stale=` by keyword.

Then run these checks:

```bash
git grep -n -E "FORK(\(|:)" -- opendbc | wc -l                           # 31 after the 2026-09 merge (18 before it)
ruff check opendbc/car/honda opendbc/sunnypilot/car/honda                # F821 catches a leftover fingerprint / adjust_accel
PYTHONPATH=. python -c "import opendbc.car.honda.interface, opendbc.car.honda.carcontroller, opendbc.car.honda.radar_interface, opendbc.sunnypilot.car.honda.gas_interceptor, opendbc.sunnypilot.car.honda.carstate_ext"
PYTHONPATH=. python opendbc/sunnypilot/car/platform_list.py && git diff --exit-code opendbc/sunnypilot/car/car_list.json
PYTHONPATH=. python opendbc/dbc/generator/generator.py   # writes git-ignored *_generated.dbc; a broken include fails here
```

A plain `git grep -n FORK -- opendbc` prints 35: the extra four are upstream Tesla DBC value strings (`LEFT_FORK_*`,
`DAS_CANCEL_FORK`, …) in `tesla_can.dbc` and `tesla_model3_vehicle.dbc`. Use the pattern above.

Then the tests (Step 6), or `./test.sh`, which runs `uv lock --check` and then, through lefthook, ruff, ty, codespell,
cpplint, MISRA and `unittest-parallel -j4`. Commit the merge. Then fast-forward and push:

```bash
git switch sp-master && git merge --ff-only merge/upstream-$D && GIT_LFS_SKIP_PUSH=1 git push origin sp-master
git ls-remote origin sp-master      # must print the new head
```

### Step 2: sunnypilot

```bash
cd ~/sp-merge
git switch master && git switch -c merge/upstream-$D
git merge refs/upstream/master
```

Clear the structural conflicts first:

* **`opendbc_repo`:** `git -C opendbc_repo checkout <the sp-master sha from Step 1> && git add opendbc_repo`.
* **Any file-location or modify/delete conflict** means upstream moved or deleted a directory the fork uses again:
  accept git's new location for fork-added files, and re-apply edits to a deleted file at its replacement by hand.
* Then run `git submodule sync && git submodule update --init --recursive`.

Then the content conflicts, callee before callers. The shape each fork hunk has now:

1. **`openpilot/selfdrive/controls/lib/drive_helpers.py`.** `should_stop(v_ego, a_target, v_ego_stopping=None)`;
   `None` keeps upstream's threshold. If upstream changes `should_stop`, keep the override parameter last.
2. **`openpilot/selfdrive/controls/lib/desire_helper.py`.** `NUDGE_FIRM`/`NUDGE_HOLD_FRAMES` with their
   `FORK(HONDA_ACCORD_9G_AU)` comment; `__init__(self, car_fingerprint: str = "")`; `self.nudge_frames = 0` where
   upstream enters `preLaneChange`; `and not driver_torque_stale` in `torque_applied`; the `NUDGE_FIRM` block before
   `blindspot_detected`; `driver_torque_stale=False` as the **last** `update()` parameter.
3. **Both modeld files** (`openpilot/selfdrive/modeld/modeld.py`, `openpilot/sunnypilot/modeld_v2/modeld.py`).
   Upstream's `SubMaster` list plus `"carStateSP"`; `DH = DesireHelper(CP.carFingerprint)`;
   `DH.update(..., left_edge, right_edge, driver_torque_stale=sm['carStateSP'].driverTorqueStale)`.
4. **`openpilot/selfdrive/controls/controlsd.py`.** `'carStateSP'` in the `SubMaster`; the two gateway lines
   (`gw = ...`, `self.LaC.set_linbus_gateway(...)`) before `self.LaC.update(...)`;
   `self.run_ext(self.sm, self.pm, lac_log, self.LaC)`.
5. **`openpilot/selfdrive/selfdrived/selfdrived.py`.** `'carStateSP'` in the `SubMaster`.
6. **`openpilot/selfdrive/controls/lib/longcontrol.py`.** The `STOPPING_DECEL_RATE` import and
   `self.stopping_decel_rate = STOPPING_DECEL_RATE.get(CP.carFingerprint, 1.0)`; `prev_state` before
   `long_control_state_trans(...)`; the debounce post-step on `stopping → pid`; the ramp
   `output_accel -= self.stopping_decel_rate * DT_CTRL`.
7. **`openpilot/selfdrive/controls/lib/longitudinal_planner.py`.** `self.v_ego_stopping = STOPPING_SPEED.get(CP.carFingerprint)`
   and `v_ego_stopping=self.v_ego_stopping` in every `should_stop()` call. Grep for other `should_stop(` callers
   (`joystickd.py`, `maneuversd.py` today) and give them the same.
8. **`openpilot/selfdrive/pandad/pandad.py`.** The import; `skip_panda_reset` and `request_skip_panda_reset()`; the
   guarded reset; `flash_if_requested(panda_serials[0])` after `flash_panda()`; `watch_for_request(process,
   request_skip_panda_reset)` after `Popen` and before `process.wait()`.
9. **`openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py`.** Keep upstream's inserts unchanged and add the
   fork's after them with numeric indices:

   ```python
   items.insert(1, models_btn)       # upstream
   items.insert(5, sunnylink_btn)    # upstream
   items.insert(2, vehicle_btn)      # FORK(HONDA_ACCORD_9G_AU): right after models
   items.insert(3, board_btn)        # FORK(GATEWAY-UPDATE): right after vehicle
   ```

   That gives `[first, models, vehicle, gateway, …, sunnylink, …]`. Keep the numeric form:
   `test_mici_settings_registers_the_vehicle_page` and `test_panel_is_reachable` assert
   `items\.insert\(\d+,\s*vehicle_btn\)` and `items\.insert\(\d+,\s*board_btn\)`. Construct panels the way upstream
   does (no `back_callback` since `099143ad9`).
10. **`openpilot/sunnypilot/mads/mads.py` and `state.py`.** In `should_silent_lkas_enable()`, `if self._gw_paused: return False`
    comes first, ahead of upstream's guards. In `update_events()` the order is: upstream's pauses, the DISENGAGE block,
    the emergency block, the gateway block (with the `KeyError` fallback and
    `self.enabled or self.state_machine.check_contains(ET.ENABLE)`), then upstream's `should_silent_lkas_enable` block.
    In `state.py` DISABLED, `silentLkasDisable` beside an ENABLE goes to `paused`.

Then review these auto-merged files by reading the fork hunks, not only the conflict list:

* `openpilot/cereal/custom.capnp`: ordinals as in the table above.
* `openpilot/common/params_keys.h`: every key in the params table above present (7 A, 9 C, 2 B), plus the 3 SL keys
  and the 5 `FORK(NSW-ZONES)` keys; the duplicate check above prints nothing.
* `openpilot/selfdrive/car/card.py`: `skip_fw_query=`, `stage_board_firmware(CS_SP)` at the end of `state_publish`,
  and `write_board_firmware()` plus `log_flash_trace()` in `params_thread`.
* `openpilot/selfdrive/car/helpers.py`: the `lateralControl` rebuild.
* `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_hook.py`: every param it reads still exists (the registration test
  checks this).
* `latcontrol.py`, `latcontrol_torque.py`, `latcontrol_torque_v0.py` and `latcontrol_torque_ext_base.py`: the gate is
  still called at the top of `update()`, and every PID update in the frame honours it.
* `controlsd_ext.py`.

### Step 3: tests for new upstream code

The harness itself was fixed in 2026-09. What to do each time:

* Check that the runner still collects all 9 fork modules with the counts in [Tests](#tests). A module that reports 0
  tests has been dropped silently.
* Write any new fork test as a `TestCase` or `OpenpilotTestCase`, with no work at import time. Use `REPO`
  (`parents[4]`) for paths into `opendbc_repo/`.
* If upstream adds a caller of something the fork overrides per car (as `joystickd`/`maneuversd` call `should_stop`),
  give it the car's value and a test.

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

It prints 235 at the merged tree with the review fixes (194 at `2cfcd3c6a` over the old paths, 191 at `10e088a2d`).
Compare it with the same pattern on `fork/pre-merge-$D`; a large drop means a hunk was lost. For opendbc,
`git grep -n -E "FORK(\(|:)|linbus|LIN-bus|HONDA_ELESYS|HondaDyn|driverTorqueStale" -- opendbc | wc -l` prints 210
(199 at `c61cfd9b`).

`git grep -n -E "FORK(\(|:)" -- openpilot | wc -l` prints 37. `git grep -n IsOnroad -- openpilot` must print nothing in
fork code (today it hits only a comment and a docstring in `test_eps_lkas_hook.py`, and a binary).

### Step 6: tests

**sunnypilot**, from the repo root in the venv:

```bash
export RAYLIB_BACKEND=headless
python tools/test_runner.py -v \
  openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py \
  openpilot/selfdrive/ui/tests/test_gateway_board_settings.py openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py \
  openpilot/selfdrive/car/tests/test_car_control_sp_seam.py openpilot/selfdrive/controls/tests/test_stopping_debounce.py \
  openpilot/sunnypilot/mads/tests openpilot/sunnypilot/selfdrive/controls/lib/tests openpilot/sunnypilot/sunnylink
# or: tools/op.sh test <same paths>
python tools/test_runner.py -j 12        # upstream's whole suite, as CI's `op test`
ty check openpilot                       # part of lint.sh; must print "All checks passed!"
```

After the 2026-09 merge: the targeted run gave 309 passed, 1 skipped (`test_settings_changes`, which needs
`jsonschema`); the whole suite collected 1682 tests, 1600 passed, 45 skipped, 1 xfailed, 0 failed. `ruff check openpilot`
reports 22 findings, all older than the merge (see [Pre-existing issues](#pre-existing-issues)).

**opendbc**, from `opendbc_repo` in the same venv:

```bash
python -m unittest opendbc.car.honda.tests.test_honda opendbc.car.honda.tests.test_elesys   # 53 tests (52 in test_elesys)
python -m unittest opendbc.safety.tests.test_honda                                          # builds libsafety; 942 run, OK (skipped=69)
python -m unittest opendbc.car.tests.test_car_interfaces -k HONDA_ACCORD_9G_AU
python -m unittest discover -s opendbc/sunnypilot/car -t .                                  # 23 tests, including the integration script
python opendbc/sunnypilot/car/honda/test_dynamic_tuning.py                                  # ALL CHECKS PASSED
python opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py                      # ALL CHECKS PASSED; §15 SKIPs without openpilot
PYTHONPATH=$HOME/sp-merge python opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py   # §15 runs
python -m unittest discover                                                                 # 9493 run, OK (skipped=1268)
./test.sh                                                                                   # everything, as lefthook runs it
```

### Step 7: on the car

The driver-facing list is in [../CHANGELOG_SERIAL_STEERING.md](../CHANGELOG_SERIAL_STEERING.md) (2026-09-27, upstream
sync). The technical checks:

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
   dashed lanes. Through the whole override `carControl.latActive` stays 0; it must not alternate frame by frame.
   Turning MADS on during an override starts it paused. A fast wheel of 200 deg/s or more (the default of
   `MadsEmergencySteerRate`) turns MADS off, unless `MadsEmergencySteerDisable` is off. In Pause
   mode, with the brake held, a gateway pause does **not** resume until the brake is released.
6. **Brake.** With lateral active, a brake press walks the command to zero in about 0.2 s.
7. **Lane change.** A brush with the blinker on does not start a lane change; a firm tug or a held push does.
8. **Longitudinal.**
   * Stock ACC stays stood down.
   * `shouldStop` asserts at about 0.8 m/s approaching a stop, and stops hold at `stopAccel` without rolling.
   * It does not engage below 19 mph.
   * Gear reads P/R/N/D.
   * The `HondaDyn*` values change after 60 s when the tuner is on.
9. **Flashing, only if a new board image is bundled.** Update through Settings > gateway. The next drive's log should
   carry "eps-lkas flash trace".

### Step 8: land it

Opendbc `sp-master` should already be pushed from Step 1 (confirm with `git ls-remote origin sp-master`). Fast-forward
sunnypilot `master` to the merge branch and push it with `GIT_LFS_SKIP_PUSH=1`. The car does not see master directly
([How the car gets updates](#how-the-car-gets-updates)). A sync changes `.github/workflows/`, so expect the mirror to
hold it and push it to `SoRadGaming/openpilot` `sunnypilot` by hand with the commands from the run summary, unless a
`MIRROR_TOKEN` is set. Before landing, also push a `pre-<date>` snapshot of the old master to this repo; the mirror
publishes it as a rollback on the device. Bring `S:/OP/sp-live` up to date from master: its layout changes with the
merge.

The firmware repo's `tools/bundle_appslot.py` finds `eps_lkas_flasher.py` in either layout (nested first, since
`862540c`), and `docs/CAN-UPDATE.md` names the nested path, so nothing there needs changing unless upstream moves the
tree again.

Update this file (the fork points become the new upstream heads automatically, then the counts and the inventory), and
write the next `UPSTREAM-<date>.md`.

---

## Conventions for carrying custom code

### What the fork already does

* **Markers.**
  * opendbc: `FORK(HONDA_ACCORD_9G_AU)` ×14, `FORK(HONDA_ELESYS)` ×10, `FORK(LKAS-GATEWAY)` ×1, bare `FORK:` ×6 (31
    lines).
  * sunnypilot: `FORK(HONDA_ACCORD_9G_AU)` ×18, `FORK(LKAS-GATEWAY)` ×13, `FORK(GATEWAY-UPDATE)` ×3, bare `FORK:` ×3
    (37 lines), in `drive_helpers.py`, `longitudinal_planner.py`, `longcontrol.py`, `desire_helper.py`, both
    `modeld.py`, `controlsd.py`, `selfdrived.py`, `pandad.py`, mici `settings.py`, `mads.py`, `state.py`,
    `latcontrol_torque_ext_base.py`, `joystickd.py` and `maneuversd.py`.
  * `git grep -n -E "FORK(\(|:)"` lists them. A plain `git grep FORK` also hits upstream Tesla DBC strings.
  * The older sunnypilot hunks still carry prose markers only: "LIN-bus gateway:", "HONDA_ELESYS:", "EPS-LKAS".
* **Per-car gating, so other cars keep upstream behaviour:**
  * `HondaFlags.ELESYS` / `HONDA_ELESYS` gates the car-specific opendbc branches: gas curve, pump, brake units bit,
    gear, AEB, `LKAS_PROBLEM`, the `SCM_BUTTONS` re-send, the serial-gateway steering, `LKAS_HUD` suppression, the
    radar parser, the `carstate_ext` gateway decode, and the `minEnableSpeed` exemption.
  * The dynamic-tuner hooks are **not** ELESYS-gated. In `carcontroller.py` (`hill_accel`/`adjust_accel`, which also
    feeds `pcm_accel`; `brake_gain`; `wind_scale`; the 32-count brake release) and `gas_interceptor.py`
    (`pedal_gain_at`, `update_pedal`) they are gated by the tuner itself: `HondaDynamicTuningEnabled`, and
    `HondaDynamicTuner._is_applicable()` = `openpilotLongitudinalControl and carFingerprint not in HONDA_BOSCH`. So they
    reach any Nidec Honda with openpilot longitudinal once the toggle is on. With the toggle off they are no-ops.
  * Per-fingerprint tables: `STEER_THRESHOLD`, `NUDGE_FIRM`, `STOPPING_SPEED`, `STOPPING_DECEL_RATE`.
  * `carStateSP.linbusGateway.present` gates the integrator hold (in both torque controllers and the extension) and the
    MADS pause. It is False on every platform outside `HONDA_ELESYS`. On `HONDA_ELESYS` it is True every frame, with or
    without a board fitted: it means "this platform can have a board", not "a board answered". On this platform with no
    board, or a silent one, `actuating` is False, so the integrator is held and decays for the whole drive. That is
    intended (with no board nothing follows `0x0E4`, so the loop is open), but the hold is not evidence that a board is
    fitted; use `valid` or `fwValid` for that.
  * `grantValid` gating `granted`, so absence is never permission. The MADS pause needs `grantValid`, so it cannot fire
    without a board.
  * The `HondaDynamicTuningEnabled` toggle; the tuner is inert when it is off.
  * The `ELESYS_SCM_STANDDOWN` safety parameter.
  * The gateway page stays hidden until a board has identified itself.
* **Car-only DBC fragments.** `_steering_control_e`, `_lkas_hud_4byte`, `_gearbox_legacy`, `_honda_elesys_base`,
  `_nidec_scm_group_a_elesys` and `_sunnypilot_linbus_gw` are imported only by `honda_accord_au_2015_can.dbc`, rather
  than edits to shared fragments.
* **New code lives in new files and on sunnypilot's extension points.**
  * New files: `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_*.py`, `stopping_tune.py`,
    `opendbc/sunnypilot/car/honda/dynamic_tuning.py`, and the mici `board.py` / `vehicle.py`.
  * Extension points: `CarStateExt` (`carstate_ext.py`), `GasInterceptorCarController`, `ControlsExt`
    (`controlsd_ext.py`).
* **Parameters added at the end of upstream signatures, with defaults, passed by keyword:** `should_stop(...,
  v_ego_stopping=)`, `DesireHelper.update(..., driver_torque_stale=)`, `create_brake_command(..., is_metric=, elesys=)`.
* **Portable tests.** The UI and flasher tests parse source text instead of importing raylib, so they run without a
  display; all tests are `TestCase`s.

### Where the fork does not follow those conventions

These are the hunks to look at first when judging whether a merge changed another car.

* **`mads.py` emergency fast-wheel disable** (`EMERGENCY_STEER_RATE`) is **not gated** on the car. It applies to every car
  with MADS enabled, but since 2026-09-30 it is a setting: `MadsEmergencySteerDisable` (default **on**, which is the
  old behaviour) turns it off, and `MadsEmergencySteerRate` picks 150, 200 (default), 250 or 300 deg/s. Both are in
  sunnylink (Steering > MADS Settings) and on the mici's Settings > vehicle page, which is shown only on a Honda or an
  unrecognised car - on another car the switch is in sunnylink only.
* **`card.py` passes `skip_fw_query=bool(fixed_fingerprint)`.** Every car whose platform the user picked skips the
  VIN/FW query and runs with empty `carFw`/VIN, not just this car.
* **`longcontrol.py`** reads a Honda parameter (`HondaDynamicTuningEnabled`) in a file every car runs. It is inert
  unless that parameter is set.
* **The dynamic tuner** reaches every Nidec Honda with openpilot longitudinal when its toggle is on (see above).
* **Shared DBC fragments.** `_nidec_common.dbc` and `_nidec_scm_group_a.dbc` gained read-only signals.
* **Core files without `FORK(...)` markers.** `card.py`, `helpers.py`, `latcontrol.py`, `latcontrol_torque.py`,
  `latcontrol_torque_v0.py`, `controlsd_ext.py`, `params_keys.h`, `custom.capnp` and the settings/sunnylink files use
  prose markers or none.

### Recommendations

1. Put a `FORK(<area>)` marker on every hunk in a file the fork does not own. Use `FORK(GATEWAY-UPDATE)`,
   `FORK(LKAS-GATEWAY)`, or `FORK(HONDA_ACCORD_9G_AU)`/`FORK(HONDA_ELESYS)`. Then `git grep -n -E "FORK(\(|:)"` is the
   whole carry-set. The 2026-09 merge tagged everything it touched; the files in the list above are what is left.
2. Add parameters **at the end** of upstream signatures, with defaults, and pass them **by keyword**.
3. When a fork line sits next to upstream code that is likely to move, anchor it to a named widget or line rather than
   a numeric index, and write the test to accept that. Today the mici settings tests pin the numeric form, so change
   both together.
4. Keep logic in fork-owned modules and leave one call line in the core file, as `stopping_tune.py` does. Good
   candidates:
   * the MADS gateway pause, into a helper under `openpilot/sunnypilot/mads/`;
   * `stage_board_firmware`, `write_board_firmware` and `log_flash_trace`, into a module under
     `openpilot/sunnypilot/selfdrive/car/`;
   * the integrator gate, as a mixin.
5. Gate the two ungated changes above: `EMERGENCY_STEER_RATE` (now at least a setting an owner can turn off), and
   `skip_fw_query` via a per-platform set. Otherwise document them as deliberate all-car behaviour.
6. Write new tests as `unittest.TestCase`, with no work done at import time and no `parents[n]` paths that reach
   outside the tree they test.
7. Before each merge, check upstream's highest ordinal in `CarControlSP` and `CarStateSP`, and keep the fork's fields
   last.

---

## Pre-existing issues

These are not caused by the merge.

**Fixed since the previous version of this file:**

* The integration script's §10 failure: it now sets `mads.enabled` for the `LAT_READY` case and adds the MADS-off case.
* The MADS flap through a board override: fixed in `2cfcd3c6a`, before the merge.
* The two `test_tx_hook_on_wrong_safety_mode` failures between the Elesys stand-down modes on `0x500`.
* The DBC byte-order test that skipped itself (no `cantools`).
* The `controlsd.py` comment on `present`: it now says False on every platform outside `HONDA_ELESYS`.

**Still open:**

* **Unused import.** `card.py` adds `import json` and never uses it.
* **Stale docstring.** `board.py` (line 17) cites `card.publish_board_firmware()`; the functions are
  `stage_board_firmware()` and `write_board_firmware()`.
* **Stale comment.** The comment in `LatControl.__init__` still says the PID is reset on takeover. The code clips it
  to `LINBUS_I_CARRY_MAX` and decays it with `LINBUS_I_HOLD_TAU` instead, as `_linbus_integrator_gate`'s docstring
  explains.
* **Lint.** `ruff check openpilot` reports 22 findings, all in fork files and all older than the merge: 17 in
  `eps_lkas_flasher.py`, 2 in `card.py`, 2 in `eps_lkas_hook.py`, 1 in `board.py`. opendbc `ty check` reports 4
  `invalid-assignment` errors in the tuner scripts.
* **Tuner scripts.** `test_dynamic_tuning.py` exits at import on a failure, and the integration script still runs its
  checks and a monkeypatch at import (see [Tests](#tests)).
* **Test gaps.** Neither Elesys safety class asserts that `0x33D` is blocked (CAR-HONDA-ACCORD-9G-AU.md §8.3), and the
  latent driver-torque freshness case in LKAS-GATEWAY-PROTOCOL.md §8.2 has no test.
