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
| [../../CHANGELOG-elesys.md](../../CHANGELOG-elesys.md) | History of the longitudinal work (area C), sections 1–20. |
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
  rollbacks. The mirror never deletes one; removing a snapshot is done by hand, in both repos.
* **There is no `pre-*` branch today.** `pre-upstream-2026-09`, the fork as it was before the 2026-09 sync, was deleted
  from `SoRadGaming/sunnypilot` and `SoRadGaming/openpilot` on 2026-10-01. It could not have installed from the device
  anyway: the `updated.py` the car now runs compares AGNOS versions and, when they differ, reads the target branch's
  manifest at `openpilot/system/hardware/comma/agnos.json`. That tree is AGNOS 18.4 in the old, un-nested layout, with
  its manifest elsewhere, so the AGNOS step would have failed and the update with it. The sync has since proved itself
  on the car, so nothing needed it. **Before offering the next snapshot,** check what the running `updated.py` does
  with it. If `agnos.json` moved (or the tree's layout changed), it cannot install, and the snapshot is a git ref for
  reference, not a rollback. If only `AGNOS_VERSION` changed and the manifest is where `updated.py` looks, it installs
  by flashing the snapshot's older AGNOS: that is a downgrade, not a failure. Try it on the device before calling it a
  rollback.
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

### sunnypilot: 114 files and the submodule pointer

Area **SL** is the speed-limit hardening of 2026-09-29 (`2e7866503`): not car-specific, gated by `SpeedLimitMapStrict` and `OsmAutoUpdateWeekly`, markers `FORK(SPEED-LIMIT)`. Batch 2 (2026-10-04) adds two ungated fixes to the confirm prompt: its comma 4 text compared the set speed in the wrong unit and said "Press -" whenever "+" was needed (upstream bug, offer upstream), and a cruise button the other way while it asks is now ignored when it would raise the set speed (a press that lowers it works as upstream; non-PCM cruise only).

Area **UF** is a fix to an upstream bug, carried until upstream fixes it, markers `FORK(UPSTREAM-FIX)`. On each merge,
check whether upstream changed the same function; if it did, take upstream's version and drop ours.

Area **UPD** is the comma 4 software page of 2026-10-01: the download progress and the highlighted "download update" button. Not car-specific and not gated (it is a display, every device gets it), markers `FORK(UPDATER)`.

Area **HUD** is the comma 4 onroad HUD of 2026-10-03: the speed cluster (speed, speed-limit sign, next lower limit, the
NSW school-zone lamps and the electronic Variable-zone sign, the stop timer), the compact standstill banner, the pending
limit in the speed-limit confirm, the gateway tile's icon, and - the same day, the owner's right-side request - the
**right rail**: at the top of the confidence ball's strip, one item at a time, the model's **planned stop** (where its
speed plan reaches standstill, white only while openpilot is driving the speed on that plan, else grey and dashed) or
the **curve** smart cruise control is slowing for (its target speed); the stock ball rides below the item. Not
car-specific: every comma 4 draws it, on by default, with nine settings (`Hud*`, sunnylink Visuals → HUD); all off is
the stock screen pixel for pixel (`test_hud_render.py`). Batch 2 (2026-10-04) adds four: **compact alerts**
(`HudCompactLimitPrompts`, `HudCompactDisengage`, `HudCompactTurn` - named normal alerts drawn as a banner top left, or
not at all for "set speed changed"; an allow-list by event name, proven by a test never to take a critical, prompt,
steer-required, AEB or FCW alert) and **Speed Limit Sign** (`HudLimitSign`: always / school and variable zones / off;
without the sign the speed, stop time and next limit slide into the corner). On 2026-10-06 (hud3) the compact
speed-limit confirm puts the set speed it would set in the stock MAX number's place, at its size, dashed, with its
"press + to confirm" pill under it (the owner's design A; drawn by the sunnypilot subclass `HudRendererSP`, no upstream
change), and a fourteenth setting, **Current Speed** (`HudCurrentSpeed`), leaves the live speed out. The logic lives in
`selfdrive/ui/sunnypilot/mici/onroad/hud_*.py`; the upstream files carry three imports, two constructions, two calls and
the top-icons condition (`augmented_road_view.py`), one import and two calls (`alert_renderer.py`) and a floor for the
ball's position (`confidence_ball.py`, unset = stock), each with a fall-through to the stock drawing. Markers
`FORK(HUD)`. The changelog entries are in `docs/CHANGELOG_SERIAL_STEERING.md` (2026-10-03, 2026-10-04, 2026-10-06). The gateway icon is the fork's first PNG of its own: `.gitattributes`
exempts it from Git LFS by path, because LFS here is sunnypilot's GitLab, which the fork cannot push to - see
**Pushing** below before adding any other file an LFS pattern matches.

**Stock ACC mode** (C, 2026-10-04, markers `FORK(HONDA_ACCORD_9G_AU)`): the `HondaElesysStockAcc` toggle, offroad only.
The car's ACC does gas and brake, openpilot steers only, the panda forwards every frame (param 68). Off by default, and
off is byte-identical to before. All of it - the hook, the panda branch, MADS, the settings snapshot, the banner, the
toggles - is in [CAR-HONDA-ACCORD-9G-AU.md](CAR-HONDA-ACCORD-9G-AU.md) section 15.

| St | Path | Area | What the fork changes | Upstream commits | Last merge |
|---|---|---|---|---|---|
| M | `.gitattributes` | HUD | One `FORK(HUD)` block at the end: `openpilot/sunnypilot/selfdrive/assets/icons_mici/gateway.png -filter binary`, so the fork's icon is a plain git object, not an LFS pointer whose object would never reach the car. Upstream file: a pattern upstream adds above it cannot undo it (later lines win). | 0 | new |
| M | `.gitmodules` | — | Points the `opendbc` submodule URL at `SoRadGaming/opendbc` and adds `branch = sp-master`. | 0 | auto |
| M | `opendbc_repo` (gitlink) | — | Pins the merged `sp-master`, not upstream's `f95f996f`. | 0 | submodule conflict |
| A | `CHANGELOG-elesys.md` | C | History of the longitudinal work. | 0 | clean |
| A | `FEATURES-elesys.md` | C | Plain-English guide for the driver. | 0 | clean |
| A | `docs/CHANGELOG_SERIAL_STEERING.md` | B (+A) | Serial-steering and gateway changelog. | 0 | clean |
| A | `docs/SP_GATEWAY_FIRMWARE.md` | B | Board-side receive spec. | 0 | clean |
| A | `docs/SP_HUD_STATUS.md` | B | SP-PROTOCOL, openpilot side. | 0 | clean |
| A | `docs/fork/README.md`, `GATEWAY-UPDATE.md`, `LKAS-GATEWAY-PROTOCOL.md`, `CAR-HONDA-ACCORD-9G-AU.md` | — | These documents. | 0 | clean |
| A | `docs/fork/UPSTREAM-2026-09.md` | — | What the 2026-09 sync brought. | 0 | added in the merge |
| M | `openpilot/cereal/custom.capnp` | A+B+C | Adds `CarControlSP.lateralControl @5` (B), `CarStateSP.linbusGateway @1` (fields @0–@18 B, @19–@26 A) and `CarStateSP.driverTorqueStale @2` (B). Since 2026-10-01 also `OnroadEventSP.EventName` `lkasGatewayEpsLatched @26` and `lkasGatewayEpsLatchedReminder @27` (B). Since 2026-10-03 also `CarStateSP.vsaFault @3`, `vsaStoredFault @4` and `OnroadEventSP.EventName` `vsaFault @28`, `vsaStoredFault @29`, `vsaFaultAnnounce @30` (C, the VSA's own fault). Since 2026-10-04 also `OnroadEventSP.EventName` `hondaElesysStockAcc @31` (C, the stock ACC mode's startup banner). | 0 | auto |
| M | `openpilot/common/params_c.cc` | UF | `params_keys_by_flag()` points its results into the handle's own key list. It used to fill them from `return_string()`, which keeps one string per thread, so `Params().all_keys(flag)` returned garbage for every flag but `ALL` and sunnylink backup/restore (`all_keys(ParamKeyFlag.BACKUP)`) got corrupt keys or a `UnicodeDecodeError`. The function came in with sunnypilot's 2026-08-13 sync (`7461f70fd`) on top of upstream's ctypes params (`74ac5ef9a`); commaai has no by-flag path. Unfixed upstream as of sunnypilot `a5f44653d`. | 0 | added in the merge |
| M | `openpilot/common/hardware/comma/agnos.py` | UPD | An optional `progress_cb` (last, default `None`) on `extract_compressed_image()`, `flash_partition()` and `flash_agnos_update()`, and `on_chunk` on `StreamingDecompressor`: compressed bytes received over `Content-Length` per 1 MB chunk, weighted across partitions by size. Without a callback every call is upstream's. Upstream file. | 0 | new |
| M | `openpilot/common/params_keys.h` | A+B+C+SL+UPD | Adds 7 `EpsLkas*` keys (A), 5 `HondaDyn*` keys and `HondaElesysGasLawV2` (C; the 7 pedal-gain and aero keys were removed in 2026-10), and `SpeedLimitMapStrict`, `OsmAutoUpdateWeekly`, `OsmLastCompleteDate` (SL), and `MadsEmergencySteerDisable`, `MadsEmergencySteerRate` (B, the fast-wheel takeover's settings), and `UpdaterDownloadProgress` (UPD), and the nine `Hud*` settings (HUD, 2026-10-03) and four more (`HudCompactDisengage`, `HudCompactLimitPrompts`, `HudCompactTurn`, `HudLimitSign`; HUD, 2026-10-04) and `HudCurrentSpeed` (HUD, 2026-10-06), and `HondaElesysStockAcc`, `HondaElesysStockAccSaved` (C, stock ACC mode, 2026-10-04), and `HondaElesysPumpV6`, `HondaElesysBrakeLawV2` (C, batch 3, 2026-10-05). | 0 | auto |
| M | `openpilot/common/tests/test_params.py` | UF | `test_params_all_keys_by_flag`: `all_keys(flag)` for `BACKUP`, `PERSISTENT` and `CLEAR_ON_MANAGER_START` equals the keys `params_keys.h` gives that flag, all ASCII. | 0 | added in the merge |
| M | `openpilot/selfdrive/car/card.py` | A+C | A: `stage_board_firmware()`, `write_board_firmware()` and `log_flash_trace()`, called from `params_thread` (staging from `state_publish`). C: `get_car(..., skip_fw_query=bool(fixed_fingerprint))`; since 2026-10-03 (`FORK(HONDA_ACCORD_9G_AU)`) `state_publish()` sends `carStateSP` before `carState`, so selfdrived, which blocks on `carState` and polls `carStateSP` with `sm.update(0)`, never reads the previous frame's (the VSA fault's onset alert depends on it). Since 2026-10-04 (C) a `LongSettingsRestore` built right after `CarParamsPersistent` is written and updated from `params_thread`: the stock ACC mode's settings put back for good once `CarParamsPersistent` has held this drive's CarParams for 3 s (the UI deletes from what it last read). | 0 | auto |
| M | `openpilot/selfdrive/car/cruise.py` | SL | `_update_v_cruise_non_pcm()` (2026-10-05, `FORK(SPEED-LIMIT)`): keeps the set speed from before a button's change (`v_cruise_kph_prev`), and after upstream's clip restores it when `update_speed_limit_assist_pre_active_raise_blocked()` (`cruise_ext.py`) says a wrong-way press at the confirm prompt raised it. Two marked places; with no prompt up every press is upstream's. Upstream file. | 0 | new (2026-10-05) |
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
| M | `openpilot/selfdrive/locationd/torqued.py` | C (+B) | `FORK(HONDA_ACCORD_9G_AU)`: the initial `latAccelOffset` is `CP.lateralTuning.torque.latAccelOffset` when the tuning is torque, else 0.0 (2026-10). `configure_torque_tune()` sets 0.0, so only `HONDA_ELESYS` (-0.43) differs from upstream. Since 2026-10-04 also the shadow speed-split factor (`lat_speed_split.py`): one guarded import (if it fails, a stand-in `make_lat_speed_split()` returns None and torqued runs as upstream), `self.lat_split` built in `__init__`, each point torqued accepts handed to it in `handle_log()`, and its once-a-minute `tick()` in `main()`; four marked places, nothing it computes is read back. | 0 | new |
| A | `openpilot/selfdrive/locationd/test/test_lagd_elesys.py` | C | The lag fallbacks on this car are 0.38 s (`steerActuatorDelay` 0.18 + 0.2), and a learned cache survives the change (5 tests). | 0 | new |
| A | `openpilot/selfdrive/locationd/test/test_torqued_elesys.py` | C (+B) | The car's own prior and offset seed in torqued, the cache reset a changed prior forces, a reported 0 adds no point, the seed through the EnforceTorqueControl / NNLC re-run (11 tests). | 0 | new |
| M | `openpilot/selfdrive/modeld/modeld.py` | B+C | Subscribes `carStateSP`, calls `DesireHelper(CP.carFingerprint)` and passes `driver_torque_stale=` into `DH.update`. | 0 | modify/delete |
| M | `openpilot/selfdrive/pandad/pandad.py` | A | Adds `flash_if_requested()` before `./pandad`, `watch_for_request()` after it, and `skip_panda_reset`. | 0 | CONFLICT |
| M | `openpilot/selfdrive/selfdrived/selfdrived.py` | B+C | Subscribes `carStateSP`, which `mads.py` reads. Since 2026-10-01 also builds `EpsLatchAlert` and, in `update_events()` after the car events, adds its events to `events_sp` from `carStateSP.linbusGateway` and `self.active or self.mads.active`. Since 2026-10-03 (C, `FORK(HONDA_ACCORD_9G_AU)`) also builds `VsaFaultAlert`; in `update_events()`, after the car events and right before the latch alert, calls `vsa_fault_alert.update(self.sm['carStateSP'], self.events.has(EventName.accFaulted))`, adds its first list to `events` (`carNotReady`, which refuses engagement while the VSA holds a fault) and its second to `events_sp`, and calls `self.eps_latch_alert.reset()` on the frame the VSA's fault clears (`vsa_fault_alert.cleared`); and in `update_alerts()`, once both alert lists are made, `alerts, alerts_sp = self.vsa_fault_alert.adjust_alerts(alerts, alerts_sp, self.AM, self.sm.frame, callback_args)` before `AM.add_many()`. | 0 | CONFLICT |
| M | `openpilot/selfdrive/ui/mici/layouts/settings/software.py` | UPD | `CheckUpdateButton`: the progress label from `download_label()` (via `_download_label()`) while `UpdaterState` is "downloading..." - after a tap, and also in IDLE for updated's own background download, where it takes precedence over "failed to update" and "download update" - and the "download update" highlight (`DOWNLOAD_READY_GREEN`, `_set_download_ready`, `_handle_background`, `_draw_content`), on only while that text waits for its tap. | 0 | new |
| M | `openpilot/selfdrive/ui/mici/onroad/alert_renderer.py` | HUD | Imports `hud_alerts`; in `_render()` the standstill prompt, and since 2026-10-04 the named compact alerts, can be drawn compact (`hud_alerts.draw_compact()`, which keeps the confirm fade running) before the stock background; in `_draw_icons()`, after the turn-signal blink update, the confirm arrow can be the pending limit (`hud_alerts.draw_pending_limit()`). Both answer False with their setting off, and the stock drawing runs. | 0 | new |
| M | `openpilot/selfdrive/ui/mici/onroad/augmented_road_view.py` | HUD | Imports and builds `HudCluster(hud_renderer, alert_renderer)` and renders it inside the content rect, before (under) the alert renderer. Imports and builds `HudRail(confidence_ball)` and renders it after the scissor ends, just before the confidence ball (it sets the ball's floor). Since 2026-10-04 imports `hud_alerts` and lets the stock MAX number show while a compact "set speed changed" draws nothing: `set_can_draw_top_icons(alert_to_render is None or hud_alerts.frees_top_icons(alert_to_render))` (False with the setting off: stock). | 0 | new |
| M | `openpilot/selfdrive/ui/mici/onroad/confidence_ball.py` | HUD | Two lines: `self.hud_floor_y = -math.inf` in `__init__`, and `dot_height = max(dot_height, self.hud_floor_y)` in `_render()` - the right rail holds the ball under its item; unset, the stock position. | 0 | new |
| M | `openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py` | C | Honda dynamic-learning toggle on the Cruise panel. | 0 | moved |
| M | `openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py` | C | `HondaSettings`: toggle, learned values (gas law on the Accord AU only, engaged time per drive mode, brake gain) and reset of the brake gain only (`LEARNED_DEFAULTS`, `RESET_KEYS`, `MODE_SLOTS`, `GAS_LAW_PARAM`, `GAS_LAW_PLATFORMS`, `car_platform`, `gas_law_applies`, `gas_law_label`, `mode_minutes`, `reset_learned_values`). Since 2026-10-04 the "Stock ACC (testing)" toggle (`STOCK_ACC_PARAM`, offroad only); batch 3 (2026-10-05) "Quieter brake pump" and "Measured brake law (testing)" (`PUMP_V6_PARAM`, `BRAKE_LAW_V2_PARAM`, offroad only, kept in sync through `_synced_toggles`). | 0 | moved |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/board.py` | A | Settings > gateway page: `BoardLayoutMici`, `UpdateBoardButton`, `board_page_visible`, `bundled_firmware`. | 0 | new→moved |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/maps.py` | SL | Settings > maps: `MapsLayoutMici`, `MapDataInfo` (one card per data set), `UpdateOsmButton` (offroad only, Always Offroad with the car on included; writes `OsmDbUpdatesCheck`), the "update weekly" toggle. | 0 | new |
| M | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | A+C+SL | Adds a "vehicle" row (C) and a "gateway" row (A) with `items.insert(2, ...)` and `items.insert(3, ...)`, a form two tests pin, and a "maps" row (SL) with `items.insert(4, ...)`. The gateway tile's icon is `icons_mici/gateway.png` (HUD, 2026-10-03; it was `offroad/icon_software.png`), loaded by `gateway_icon()`, which falls back to `icon_software.png` if it raises or loads empty - the tile is built at UI start - and the `cloudlog` import it logs with. | 0 | CONFLICT |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` | C (+B) | mici vehicle page: `VehicleLayoutMici`, `car_brand()`, `HondaLearnedInfo` (the gas law from the next drive with the brake gain - only the brake on another Honda - and engaged minutes per drive mode, since 2026-10); and (B) the fast-wheel rows, "off on swerve" and "swerve at", the rate (`FastWheelRateToggle`). Since 2026-10-04 the "stock acc (testing)" toggle, offroad only; batch 3 (2026-10-05) "quieter brake pump" and "measured brake law (testing)", offroad only. | 0 | new→moved |
| A | `openpilot/selfdrive/ui/sunnypilot/mici/onroad/hud_alerts.py`, `hud_cluster.py`, `hud_draw.py`, `hud_model.py`, `hud_rail.py`, `hud_settings.py` | HUD | The comma 4 HUD. `hud_settings`: the fourteen params, read at most once a second, off when unreadable. `hud_model`: what is shown (pure, no raylib): `build_frame()` (the next limit only while the limit on screen is from the map, its distance run down by `vEgo` between the 1 Hz map messages; the NSW cues only in `nswZone.state` 2 or 4; since 2026-10-04 `zone`, `sign_shown` and `sign_slot` by `HudLimitSign`, the zone fact apart from the two cue settings; since 2026-10-06 no `speed` with `HudCurrentSpeed` off, nothing else changed), `SignSlot` (zones mode debounced: 0.3 s on, 12 s off), the compact alerts (`COMPACT_LIMIT`/`COMPACT_QUIET`/`COMPACT_DISENGAGE`/`COMPACT_TURN` by event name, each under its one recorded event type in `COMPACT_TYPES`; `compact_kind()` - only a listed name under that type AND `AlertStatus.normal` AND `VisualAlert.none` AND not an 'openpilot Unavailable' (either line) - `frees_top_icons()`, `confirm_pending()`, the banners' words), `StandstillBanner` (the full prompt again once a fresh `radarState` lead has moved off for 0.4 s; `observe()`, called by the cluster every frame, ends it when the prompt clears), `pending_limit()` (limit and offset), `target_speed()` (what the confirm would set, as MAX will show it: `speedLimitFinalLast` to 0.1 km/h and clamped to the cruise range as `cruise_ext.py` stores it - `cruise_min_kph()`, `CRUISE_MIN_KPH`/`CRUISE_MAX_KPH` - then converted and rounded as `HudRenderer._draw_set_speed`; 0 with no limit), and the right rail: `RailState` (the planned stop from `modelV2` velocity/position - under 0.5 m/s within 10 s, no `radarState` lead within it + 10 m to appear, + 5 m once shown (a band: route 110's countdown blinked on one threshold), white only with `carControl.longActive` and `longitudinalPlan` source `e2e`; the curve from smart cruise control entering/turning while it is the limiting target AND its target is under the car's speed (appears 2 km/h under, goes 3 km/h over), hidden on the frame when the driver takes the speed back (`driver_has_speed()`: smart cruise overriding, or `longActive` off); 0.3 s on / 0.5 s off debounce; hard hides at a standstill, under 1 m, setting off or a message missing; the figures held - `stop_figure()` counts down freely and up only by 5 m or more, `curve_figure()` in 5 km/h (5 mph) steps with a 1-unit margin; `RailFrame.num`/`unit` carry them), `plan_stop_m()`, `curve_left()` (the model frame is z down: positive yaw = right), `fmt_stop_dist()` (whole meters under 20 m), `fmt_speed()`. `hud_cluster`: `HudCluster`, the widget; hidden until a full-screen alert has faded out, never under a compact one (whose banner stops short of what it drew: `hud_alerts.cluster_edge`); its sign dashed while the compact confirm is up (the offset badge low on the right of the ring when the school lamps are drawn, since 2026-10-06); the speed slides into the sign's place and back, the sign fading in over the last 25 px. `hud_rail`: `HudRail`, the widget in the ball's strip; it sets the ball's floor (eased down to y 114 while an item fades in over 0.15 s on the UI clock, back up while it fades out; the fade dims toward the black strip, so it stays opaque). `hud_alerts`: the banner and the pending-limit icon; `draw_compact()` (the standstill banner, then the compact groups: nothing for a set-speed change or a confirm with no words; since 2026-10-06 the confirm publishes its target (`PendingTarget`, through `pending_max`) for the HUD renderer to draw in the MAX number's place - with 'limit 50 +5' in the pill when the target is not the limit and there is no sign in the cluster, the '+5' being the target less the limit; `confirm_target()` and `box_alert()` answer 'is the box up for this alert' for both the drawing and the HUD renderer's top-icon test - and only with no limit, a text naming no key or no HUD renderer to draw it is it the banner with the key the text names and - with no sign in the cluster, or one not yet half faded in - the pending sign, whatever `HudConfirmLimit` says; the disengage and turn banners) and `frees_top_icons()`. `hud_draw`: ink-box text, condensed digits, the AU and electronic signs, `school_cue()`, the stopwatch, the next-limit row, the banners (`compact_banner()`, `confirm_banner()`, `key_glyph()`), the confirm's target in the MAX number's place (`pending_max()`, `dashed_rrect()`; 2026-10-06), sunnypilot's offset badge, the rail's stop and curve glyphs (raylib primitives, no image files) and its figures. | 0 | new |
| M | `openpilot/selfdrive/ui/sunnypilot/mici/onroad/hud_renderer.py` | HUD | sunnypilot's `HudRendererSP` (2026-10-06, design A): after the stock HUD it draws the compact confirm's target in the stock MAX number's place - `hud_draw.pending_max()` with the stock digits' position, size and font - from what `hud_alerts.pending_max` published this frame; it follows the alert's fade, fades out (0.1 s) when the MAX number is freed ('set speed changed'), is cut at once when another alert takes the top left, is dropped rather than faded when the view was not drawn for 0.25 s (`PENDING_STALE_S`), and crossfades with the stock MAX number. `drawing_top_icons()` also counts it, decided from THIS frame's alert (`_box_top_icon()`: `hud_alerts.box_alert()` on the alert renderer's `will_render()`, which `HudCluster` hands it as `alert_renderer`) - up from the confirm's first frame, while it fades under the stock MAX number, not while the confirm itself fades out nor once another alert is up - so the driver-monitoring face and the cluster's speed make way exactly while the box is the prompt, and come back on the frame they would without it. It sets `pending_max.drawer`; without it the confirm stays the banner. Nine `FORK(HUD)` markers; the upstream `mici/onroad/hud_renderer.py` is untouched. | 0 | new |
| A | `openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py` | A | Flasher protocol, image checks and trace (26 tests). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py` | A | pandad hook ordering, the onroad refusal and param registration (10 tests). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_gateway_board_settings.py` | A (+B) | DBC, capnp, params, page and button gates (25 tests), including `test_lat_ready_means_lateral_is_enabled_not_merely_possible` (B). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` | C | Params, UI, sunnylink and statsd in sync with the tuner and the gas law; no retired key named anywhere; RESET keeps the mode times; the gas-law readout gated to `HONDA_ELESYS`; since batch 3 the pump and brake-law settings registered, handed to the hook, offroad only on both screens and in sunnylink (26 tests). | 0 | new→moved |
| A | `openpilot/selfdrive/ui/tests/test_hud_cluster.py` | HUD | The HUD's rules (next-limit window and direction, school and Variable cues only on the published NSW limit, the timer, MAX, missing and stale messages, imperial), the settings and their once-a-second read, the banner's lead-departure rule through two stops in the UI's real call order, sunnylink, the `FORK(HUD)` markers and fall-throughs, the gateway icon and that git stores it as a PNG, not an LFS pointer; the right rail's rules - where the plan stops, the debounce both ways, white vs grey, a lead inside the stop and the band once it shows (route 110's parked car past the line), standstill and under 1 m, the stop figure counting up only by 5 m, the curve states and the limiting source, the curve only while its target is under the car's speed (route 10f's motorway lane change), its 5 km/h steps, the driver taking the speed back, the map curve, the arrow's direction, priority, each toggle, missing and stale messages for every service, the units; since 2026-10-04 the sign setting (Always = the old rules, Off keeps the limit and the next limit, zones regardless of the cue settings and debounced over route 10f's gaps) and the compact alerts, with the SAFETY walk of every event in `EVENTS` and `EVENTS_SP` (a listed alert is normal, LOW or below, no visual; no critical, userPrompt, steer-required, AEB or FCW alert is ever compact; a listed name raised otherwise is stock; the UI's own critical alerts have no name; since round-1 review each listed name has exactly its recorded event type in both tables, and a new type of a listed name - or an 'openpilot Unavailable' under the recorded one - is stock); since 2026-10-06 `HudCurrentSpeed` off hiding only the speed in every sign mode, debounced and stopped, `target_speed()` as the confirm stores it and MAX shows it (the 0.1 km/h step, the cruise range's floor and top, an mph tie, a percentage offset; the constants checked against `cruise_ext.py`, its minimum set speed and the HUD renderer's `KM_TO_MILE`), the Current Speed item under the cluster's, the confirm's target drawn by the sunnypilot subclass with the upstream HUD renderer untouched, and the box and the banner asking one question (`confirm_target()`) with the pill's note adding up to the target (95 tests). | 0 | new |
| A | `openpilot/selfdrive/ui/tests/test_hud_render.py` | HUD | The real `AugmentedRoadView` in a headless raylib window (child process; skips where no window opens), 20 states and three sequences (two stops in one drive, an alert fading out, a planned stop coming, fading in, fading out and going): every setting off equals the stock drawing pixel for pixel, an alert's own setting off draws it as stock, each piece is where it should be, the rail only in the ball's strip with the ball held under its item (eased, not dropped in one frame) and back after, its two settings off = the stock strip, and the gateway tile falls back when its PNG is an LFS pointer; since 2026-10-04 the compact alerts ('set speed changed' pixel-identical to no alert with the MAX number free, the banners top left and short of the cluster with nothing below them, the confirm's pending sign on the cluster or in the banner, a critical alert exactly stock, each group's setting off exactly stock; since round-1 review the banner's key follows its text against the arrow, a wordless confirm draws no banner, the banner carries the sign with `HudConfirmLimit` off and while the cluster's sign is still fading in) and the sign setting (zones outside = off, inside = always, off in the corner, the slide); since 2026-10-06 design A (the confirm's target pixel-identical to the stock MAX digits in their ink box, ~81 px, the pill under it and no banner, a top icon, 'limit 50 +5' with an offset and no sign, today's banner with no limit, the solid stock MAX after the confirm equal to the frame without a confirm, a critical alert stock from its first frame (top left) with the live speed back in the fading cluster, the offset badge clear of the school-zone lamps and SCHOOL) and Current Speed off (only the digits change; the stop time stays); since round-1 review the frame a confirm ends on - into a disengage or turn banner, into today's banner when the limit drops, into no alert, the view coming back after 5 s with no alert or with 'set speed changed' - against the same drive with the confirm drawn as today's banner (`pending_max.drawer` off, the unchanged build's screen): the speed back on the first frame, the cluster identical from it, the whole frame identical from it (with no alert, once the prompt's own fade top left is over), and the confirm's first frame already hiding the speed (43 tests). | 0 | new |
| A | `openpilot/selfdrive/ui/tests/test_mads_fast_wheel_settings.py` | B | The fast-wheel settings in agreement across `params_keys.h`, `mads.py`, the mici page and sunnylink (15 tests). | 0 | new |
| A | `openpilot/selfdrive/ui/tests/test_maps_settings.py` | SL | The maps page contract: gates, confirm flow, dates, glyphs (source-parsing tests). | 0 | new |
| A | `openpilot/selfdrive/ui/tests/test_software_update_button.py` | UPD | The real `CheckUpdateButton` in a headless raylib window, in a child process: the label per phase, a background download's label, the highlight on and off, the tap's signal, onroad (6 tests; skipped where no headless window opens). | 0 | new |
| M | `openpilot/sunnypilot/mads/mads.py` | B+C | C (2026-10-04, stock ACC mode only, `elesys_stock_acc` from `HondaFlagsSP.ELESYS_STOCK_ACC`): strips `belowEngageSpeed` on every frame, so MADS turns on at any speed, openpilot engaged or not; after MADS's own state machine puts back, on the frame stock ACC drops out by itself, upstream's `speedTooLow` or `cruiseDisabled` (`_stock_acc_drop_alerts`), so the alert is the one MADS-off gets and lateral stays, and on a refused `pcmEnable` frame `belowEngageSpeed` (`_stock_acc_refused`), so the refusal shows. B: Pauses on a gateway driver override (`LINBUS_REASON_DRIVER_OVERRIDE`, `_gw_paused`), holds the pause in `should_silent_lkas_enable()`, and also fires on the frame MADS is turned on. Adds the fast-wheel disable (`EMERGENCY_STEER_RATE` 200, `EMERGENCY_STEER_FRAMES` 2), which applies to **every** car; since 2026-09-30 a setting (`MadsEmergencySteerDisable`, default on; `MadsEmergencySteerRate` 150/200/250/300, default 200), read in `__init__` and `read_params()`. | 0 | CONFLICT |
| A | `openpilot/sunnypilot/mads/tests/test_mads_honda_stock_acc.py` | C | MADS and selfdrived in stock ACC mode, frame by frame through the real `CarEvents` and `StateMachine`: MADS at any speed (also while engaged), `pcmEnable` and its refusal's alert, the drop-out's alerts below and above 37.8 km/h, a brake cancel (14 tests). | - | new |
| M | `openpilot/sunnypilot/mads/state.py` | B | DISABLED branch: an ENABLE that arrives with `silentLkasDisable` goes to `paused`. | 0 | added in the merge |
| A | `openpilot/sunnypilot/mads/tests/test_mads_gateway_pause.py` | B | The gateway pause, resume, brake modes, emergency and enable-frame cases, and the fast-wheel settings (41 tests). | 0 | new→moved |
| M | `openpilot/sunnypilot/mapd/mapd_manager.py` | SL | Marked lines: runs `OsmAutoUpdater` each tick and clears `OsmLastCompleteDate` with the maps; `update_osm_db(request_allowed=True)` acts on `OsmDbUpdatesCheck` only when `OsmAutoUpdater` checked it this tick (upstream's call, with no argument, is unchanged). | 0 | new |
| A | `openpilot/sunnypilot/mapd/osm_auto_update.py` | SL | `auto_update_due()` (pure) and `OsmAutoUpdater`: weekly refresh when parked (no ignition on any panda) on unmetered wi-fi/ethernet, once per boot; `record_completion()` writes `OsmLastCompleteDate`; `answer_request()` clears an `OsmDbUpdatesCheck` (button, OSM panel, sunnylink) that is onroad, has no region or arrives mid-download, before `update_osm_db()` sees it. `is_offroad()`/`is_parked()` are shared with the NSW downloader. | 0 | new |
| A | `openpilot/sunnypilot/mapd/tests/test_osm_auto_update.py` | SL | The refresh decision and the completion recorder, and the request gate (`TestRequestGate`, `TestUpdateOsmDb`). | 0 | new |
| M | `openpilot/sunnypilot/modeld_v2/modeld.py` | B+C | The same three edits as `modeld.py`. | 0 | CONFLICT |
| A | `openpilot/sunnypilot/selfdrive/assets/icons_mici/gateway.png` | HUD | The gateway tile's icon, option "B, inline bridge" (128×88, white on transparent, the harness stubs at 50%). A plain git object, not Git LFS like the other PNGs here: `.gitattributes` exempts it (see that row). The update dialog keeps `icon_software.png`. | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/locationd/lat_speed_split.py` | C | SHADOW speed-split lateral factor (2026-10-04, owner decision 9, A_synth L3): torqued's own TLS fit below and above 70 km/h from six running moments per half (`SplitHalf`, `tls_fit`), logged as a `latsplit` line once a minute; applies nothing. Elesys Accord, live (not decimated) estimator only; never raises. | - | new (2026-10-04) |
| A | `openpilot/sunnypilot/selfdrive/locationd/tests/test_lat_speed_split.py` | C | The moment fit equals `estimate_params()`, moments combine exactly across drives, the split, only torqued's points, `lateralTorqueParameters` identical with and without it, the gating, the log cadence, never raising, torqued running as upstream when the module cannot be imported (10 tests). | - | new (2026-10-04) |
| M | `openpilot/sunnypilot/selfdrive/car/car_specific.py` | C | 2026-10-04, `FORK(HONDA_ACCORD_9G_AU)`: `STOCK_ACC_ANNOUNCE_FRAMES`; `CarSpecificEventsSP` raises `hondaElesysStockAcc` for the first 5 s of car events of a stock ACC drive only. Upstream file. | 0 | new |
| M | `openpilot/sunnypilot/selfdrive/car/cruise_ext.py` | SL | `update_speed_limit_assist_pre_active_raise_blocked(button_type, v_cruise_kph_prev)` (2026-10-04, narrowed 2026-10-05), a new method; `update_speed_limit_assist_pre_active_confirmed()` is upstream's again. True while the confirm prompt is up (preActive, this or the previous frame) on non-PCM cruise when a `+`/`-` press the OTHER way than asked has RAISED the set speed (route 114: `-` under the gas, 50 to vEgo 72.5 km/h; `+` during a `-` prompt), and `cruise.py` then keeps the set speed it had. A wrong-way press that lowers the set speed or leaves it is upstream's set-speed change (115: `-`, 60 to 59; SET under the gas below the set speed), so for the prompt's `PRE_ACTIVE_GUARD_PERIOD` (5 s, re-armed by every limit change) the driver can still lower it. Neither confirms (the planner takes only the asked-for button). Outside preActive, and on PCM cruise, nothing changes; brake, cancel, main and the gas pedal are untouched. Owner decision 5, narrowed. | 0 | new (2026-10-04) |
| A | `openpilot/sunnypilot/selfdrive/car/honda_stock_acc.py` | C | 2026-10-04: the stock ACC mode's settings snapshot (`HondaElesysStockAccSaved`) and restore, `preserve_long_settings()` (setup_interfaces) and `LongSettingsRestore` (card's params thread, `SETTLE_S`); "deleted" includes back-at-default after manager's default loop; every step guarded. | - | new |
| M | `openpilot/sunnypilot/selfdrive/car/interfaces.py` | C | `FORK(HONDA_ACCORD_9G_AU)` in `_initialize_torque_lateral_control()`: keeps `latAccelOffset` across the EnforceTorqueControl / NNLC re-run of `configure_torque_tune()`, which resets it to 0.0 (2026-10). Only `HONDA_ELESYS` carries a nonzero seed (-0.43); every other car ends where upstream leaves it. Since 2026-10-04: `preserve_long_settings()` before `_cleanup_unsupported_params()` in `setup_interfaces()`, and `HondaElesysStockAcc` in `initialize_params()` (stock ACC mode). Batch 3 (2026-10-05): `HondaElesysPumpV6` and `HondaElesysBrakeLawV2` in `initialize_params()`. | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/car/tests/test_honda_elesys_pump_brake.py` | C | Batch 3 (2026-10-05): `HondaElesysPumpV6` (default on) and `HondaElesysBrakeLawV2` (default off) reaching `_initialize_honda()` through `initialize_params()` as flags 16 and 32; both off identical to no settings; never in stock ACC mode or on another Honda (5 tests). | - | new |
| A | `openpilot/sunnypilot/selfdrive/car/tests/test_honda_stock_acc.py` | C | The param reaching the hook, the snapshot and restore (the UI's race at every tick before the settle, a reboot in between, a drive that ends early, a left-over snapshot, a broken one), the banner (15 tests). | - | new |
| A | `openpilot/sunnypilot/selfdrive/car/tests/test_speed_limit_confirm_buttons.py` | SL | The buttons while asked: a wrong-way press that would raise the set speed is ignored (114's `-` under the gas, `+` during a `-` prompt, SET under the gas just above the set speed, short and long), one that lowers it works (115's `-`, repeated `-`, a long `-`, SET under the gas below the set speed); the right press confirms as before, also after a wrong one; outside preActive, at the limit and on a PCM car nothing changes (12 tests; 5 fail on the code before batch 2, and 5 on the unnarrowed rule of 2026-10-04). | - | new (2026-10-04) |
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
| A | `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_latcontrol_reported_torque.py` | B | The zero torque the Honda `CarController` reports while the board is not actuating keeps `_check_saturation` from firing (closed loop through the real `CarController`, both torque controllers), and a real saturation still alerts while actuating (7 tests). | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin` | A | Board app-slot image: 46,540 bytes, marker `APL1`, origin `0x08004000`, commit `d995bc95`, flags `0x04` (INCAR_TEST). | 0 | new→moved |
| A | `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_flasher.py` | A | Portable bootloader protocol, `PandaTransport` (ELM327), `BenchTransport`, and the steering/vibration trace. | 0 | new→moved |
| A | `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_hook.py` | A | pandad glue: `flash_if_requested()`, `watch_for_request()`. | 0 | new→moved |
| A | `openpilot/sunnypilot/selfdrive/selfdrived/eps_latch_alert.py` | B | `EpsLatchAlert`: the "restart the car" alert for an EPS latched until key-off. Debounced on fresh `0x70B` frames only (1 s to confirm, 3 s of fresh "not latched" to clear, stale frames count for neither); the announcement once per latch, held until a WARNING can be shown; then a silent reminder every 5 minutes. Since 2026-10-03 (C) also `reset()`, which selfdrived calls when the VSA's own fault clears: the EPS follows the VSA, so a latch the board reported during the VSA fault has to be confirmed again from fresh frames. | 0 | new |
| M | `openpilot/sunnypilot/selfdrive/selfdrived/events.py` | B+C+SL | SL (2026-10-04): `speed_limit_pre_active_alert()`'s comma 4 text picks '+' or '-' with `compare_cluster_target(set_speed * KPH_TO_MS, ...)` - the confirm's own comparison - instead of upstream's `round(set_speed * MS_TO_KPH)` of a value already in km/h, which said "Press -" whenever "+" was needed; one import, one hunk, `FORK(SPEED-LIMIT)`; offer upstream. B+C: `EVENTS_SP` entries for the two events: `ET.WARNING` only. Announcement: "Steering Fault" / "Turn the car off and on to clear it", userPrompt, mid, `Priority.LOW` (below driver monitoring's MID stage 2, which a tie with a newer alert would hide), `AudibleAlert.prompt`, 6 s. Reminder: "Steering Off Until Restart", normal, small, `Priority.LOWEST`, silent, 4 s. Since 2026-10-03 (C) also `vsaFault` (IMMEDIATE_DISABLE "Stability Control (VSA) Fault", NO_ENTRY "VSA Fault: Brakes Degraded", a silent `Priority.LOWER` PERMANENT banner), `vsaStoredFault` (callbacks `vsa_stored_fault_no_entry_alert` and `vsa_stored_fault_permanent_alert`, "Clears Above 35 km/h" / 22 mph, or `vsaFault`'s two texts while `carStateSP.vsaFault` is set (`_vsa_fault_live`); NO_ENTRY and PERMANENT only) and `vsaFaultAnnounce` (PERMANENT, `Priority.LOW`, `AudibleAlert.prompt`, 3.5 s), and `VSA_CLEAR_SPEED_TEXT`, `VSA_LIVE_NO_ENTRY_TEXT`, `VSA_LIVE_BANNER_TEXT`. Since 2026-10-04 (C) `hondaElesysStockAcc`: PERMANENT only, "Stock ACC Mode" / "Car's cruise does gas and brake, openpilot steers", normal, mid, `Priority.LOW`, silent, 1 s. | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_speed_limit_pre_active_alert.py` | SL | The confirm text: routes 114/115's cases, imperial, a grid (8-145 km/h in 0.1 steps, metric and imperial limits) against `compare_cluster_target` and `VCruiseHelperSP.req_plus/req_minus`, the arrow's own formula (equal but for exact .5 ties), the unset-cluster fallback, the PCM text and the alert itself unchanged (7 tests; 5 fail on the code before). | - | new (2026-10-04) |
| A | `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_eps_latch_alert.py` | B | The debounce, the stale-frame pattern of routes fc/fd, the deferral, the reminder, warnings only, the text against the mici alert renderer's sizing with the real fonts, the AlertManager path (including that driver monitoring's stage 2 keeps the screen) and selfdrived's wiring (19 tests). | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_vsa_fault_alert.py` | C | The VSA fault alert: the helper's events and filter, the event classes (live adds nothing `accFaulted` does not; stored is NO_ENTRY and PERMANENT only), the texts (no "restart", the mici renderer's fit with the real fonts), selfdrived's state machine and MADS (stored refuses both, never disables), the AlertManager path and its sounds, the onset with `vsaFault` one frame late (`late_alerts()`), a live fault with CAN invalid, the EPS-latch alert across route 113's clear, selfdrived's and card's wiring, and the `CarStateSP` capnp/dataclass agreement and round trip (47 tests). | 0 | new |
| A | `openpilot/sunnypilot/selfdrive/selfdrived/vsa_fault_alert.py` | C | `VsaFaultAlert`: a live VSA fault (`carStateSP.vsaFault` with upstream's `accFaulted`) keeps `accFaulted`'s disengagement and adds `vsaFault`, whose texts replace `accFaulted`'s; a held one (`vsaStoredFault`, or `vsaFault` without `accFaulted`) adds `carNotReady` to `events`, refusing openpilot and, deliberately, MADS until the VSA clears it; `adjust_alerts()` drops from both alert lists the upstream alerts these replace (and, while either is up, `steerUnavailable`'s banner and no-entry and the EPS-latch alerts) and adds `late_alerts()`, vsaFault's disengagement or no-entry alert when upstream's `accFaulted` one is already up because `vsaFault` arrived a frame late; `cleared` (one frame) makes selfdrived reset the EPS-latch alert; `vsaFaultAnnounce` once per fault. The MADS decision is in its docstring and `CAR-HONDA-ACCORD-9G-AU.md` 10.6. | 0 | new |
| M | `openpilot/sunnypilot/sunnylink/athena/sunnylinkd.py` | C | `FORK(HONDA_ACCORD_9G_AU)` (2026-10-04): `OFFROAD_ONLY_PARAMS` = `HondaElesysStockAcc`, and since batch 3 fix round 1 `HondaElesysPumpV6` and `HondaElesysBrakeLawV2`; `saveParams()` skips them unless `IsOffroad` is set, so the device enforces what sunnylink's `offroad` macro only advises. Upstream file. | 0 | new |
| M | `openpilot/sunnypilot/sunnylink/athena/tests/test_sunnylinkd.py` | C | `test_saveParams_offroad_only` appended (2026-10-04); `test_saveParams_pump_rule_and_brake_law_offroad_only` (batch 3 fix round 1). Upstream file. | 0 | new |
| M | `openpilot/sunnypilot/sunnylink/settings_ui.json` | C+B+HUD | Compiled output of the YAML files below. | 0 | auto, then recompiled |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml` | C+SL | `honda_dynamic_learning` read-only info section (C); the "Strict Map Speed Limits" toggle in the speed limit settings (SL); the NSW mode, the weekly updates and the "update now" toggles for NSW zones and OSM maps (NSW-ZONES, see NSW-SPEED-ZONES.md). | 0 | moved |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/steering.yaml` | B | MADS Settings: "Turn Off Steering on a Fast Wheel" (`MadsEmergencySteerDisable`) with "Fast Wheel Threshold" (`MadsEmergencySteerRate`) under it. | 0 | new |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/vehicle.yaml` | C | `honda` section with three toggles: dynamic learning, the gas law (`HondaElesysGasLawV2`), and since 2026-10-04 "Stock ACC (testing)" (`HondaElesysStockAcc`, the `offroad` macro, never `longitudinal`); batch 3 (2026-10-05) "Quieter brake pump" (`HondaElesysPumpV6`) and "Measured brake law (testing)" (`HondaElesysBrakeLawV2`), the same way. | 0 | auto |
| M | `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/visuals.yaml` | HUD | The `hud_comma4` section, "HUD" (visible for `device_type` mici only), with the fourteen `Hud*` items; Current Speed (2026-10-06) and Speed Limit Sign (a `multiple_button`: Off / School & Variable Zones / Always) right under Speed and Speed Limit; those two and Next Lower Limit hide while Speed and Speed Limit is off, School Zone Lights and Electronic Sign also while the sign is Off; Planned Stop, Curve Speed and the three Compact items work without it. Upstream file: a new section between `hud_elements` and `developer_ui`. | 0 | new |
| M | `openpilot/sunnypilot/sunnylink/statsd.py` | C | Reports `HondaDynamicTuningEnabled`, `HondaDynBrakeGain`, the three `HondaDynModeSec*` totals, `HondaElesysGasLawV2` and `HondaElesysStockAcc`; batch 3 adds `HondaElesysPumpV6` and `HondaElesysBrakeLawV2`. | 0 | auto |
| M | `openpilot/sunnypilot/sunnylink/tests/test_settings_changes.py` | SL | `TestMapDataControls` appended at the end of the file, marked FORK(NSW-ZONES): the four map-data items in the speed limit settings and a real `saveParams` round trip. Upstream file (sunnypilot SDUI #1780, #1830): **merge hazard**. | 0 | new |
| A | `openpilot/sunnypilot/tools/brake_route_check.py` | C | Batch 3: given route folders (parquet with pyarrow, else the rlogs, one process per segment), prints the pump study's proof-plan metrics (pump2 section 4: the rule replayed against the logged pump bit, both rules replayed on the same commands, standstill bursts per stop, steady-command gain, bleed, rise response, stops, creep, the brake learner, the VSA) and the brake law's acceptance metrics (learnaudit B4), pooled over the arm, with a verdict on every abort criterion; `--baseline` routes are the other arm. Replays the controller's own pump functions. Fix round 1: a hold runs from the stop for as long as it is held, so a roll inside it is MOVED; take-overs are counted from arrivals engaged 6 s out; the final approach is pump2's pitch-corrected one; the rule match allows a burst edge one frame off; each hold shows its delivered pressure; the learner verdict is on the per-drive change; the brake law is the route's `blaw=` tag, `--rule` only when the log cannot say. Read-only (CAR doc 9.4). | - | new (batch 3) |
| A | `openpilot/sunnypilot/tools/tests/test_brake_route_check.py` | C | Batch 3: bit positions against opendbc's CANPacker, the replayed rules are the controller's, the fallback C1 transcription equals it, a synthetic route written as a real rlog end to end (rule from CarParamsSP, replay match 1.0, a hold that holds passes, one whose XMISSION_SPEED moves and a brake error abort, the CLI and its JSON), parquet equal to rlog (with pyarrow), every verdict at its threshold, rlog/qlog choice; fix round 1: a roll inside a hold as the car reports it (XMISSION, vEgo and WHEELS_MOVING up, standstill clear) aborts on a route and on 1 s / 3 s frame tables, a release or a launch is not creep, a brake take-over is an arrival, the brake law from the tags, `--rule` never overrides the log, standstill bursts judged by the delivered level (17 tests). | - | new (batch 3) |
| A | `openpilot/sunnypilot/tools/shadow_learn_report.py` | C | Reads the `hondashadow` and `latsplit` lines out of one or more routes (parquet `logMessage` with pandas, else the rlogs; or a text dump) and prints what the shadow learners learned, per route and combined (2026-10-04). Since batch 3 never pools different builds (the line's commit, gas law, cap, pump rule, brake law, tuner on/off; untagged v=1 lines are their own group) and marks a launch measured without the cap DISCARDED. Read-only. | - | new (2026-10-04) |
| A | `openpilot/sunnypilot/tools/tests/test_shadow_learn_report.py` | C | A route folder without pandas reads the rlogs; real `hondashadow` lines parse and combine by their counts (brake cells, the brake gain, the launch sums with and without a lead); routes whose names end alike never overwrite each other; older lines still read; (batch 3) different builds never pool, tags parse as text and untagged lines group apart, a pre-cap launch is discarded (7 tests). | - | new (2026-10-04) |
| M | `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py` | C (Other) | Reads and writes UTF-8 with an LF newline, so compiling on Windows matches CI. | 0 | moved |
| A | `openpilot/sunnypilot/system/updated/download_progress.py` | UPD | `DownloadProgress` (writes `UpdaterDownloadProgress` with blocking puts, so a `clear()` straight after a write wins; one write per second within a phase, but a phase change and a phase's 100% at once; a failed write never fails the update), `git_fetch_with_progress()` (git's Receiving/Unpacking meter; returns and raises what `run()` did, minus the meter's redraws) and `download_label()` (the page's text). | 0 | new |
| A | `openpilot/sunnypilot/system/updated/tests/test_download_progress.py` | UPD | Throttle, labels, a fake and a real `git fetch --progress`, the AGNOS weighting and chunk reports through `agnos.py`, the wiring in `updated.py`, a round trip and a clear-straight-after-set through the built `Params`, and `Updater.fetch_update()` itself with git, AGNOS and finalize patched out (27 tests). | 0 | new |
| M | `openpilot/system/updated/updated.py` | UPD | `self.progress = DownloadProgress(...)`; `fetch_update()` sets the phases "code", "checkout" and "os", fetches with `git_fetch_with_progress(["git", "fetch", "--progress", ...])` and still logs "git fetch success: …", passes `progress_cb` through `handle_agnos_update()`, and clears the param before "finalizing update..." and on every return to idle. | 0 | new |
| M | `openpilot/tools/joystick/joystickd.py` | C | Passes the car's stopping speed to `should_stop()`. | 0 | added in the merge |
| M | `openpilot/tools/longitudinal_maneuvers/maneuversd.py` | C | Parses `CarParams` and passes the car's stopping speed to `should_stop()`. | 0 | added in the merge |

### opendbc: 50 files and `FORK.md`

Five of them arrived after the 2026-09 sync, with the 2026-10 work: `elesys_gas.py` and `test_elesys_gas.py` (the gas law), `elesys_stop.py` and `test_elesys_stop.py` (the soft final stop), and `torque_data/override.toml` (the car's own torque prior). Three more on 2026-10-03: `vsa_fault.py`, `test_vsa_fault.py` and its fixture `fixtures/vsa_fault_frames.json.gz` (the VSA's own fault). Batch 2 (2026-10-04) adds `shadow_learn.py` and `test_shadow_learn.py` (the shadow learners) and carries fork changes in two upstream files for the first time, `opendbc/safety/sunnypilot/mads.h` and `opendbc/safety/tests/mads_common.py` (the MADS heartbeat race, area UF). Stock ACC mode (2026-10-04) adds `car/honda/tests/test_elesys_stock_acc.py` and carries fork changes in four more upstream files: `opendbc/sunnypilot/car/interfaces.py`, `opendbc/sunnypilot/car/honda/values_ext.py`, `opendbc/safety/tests/libsafety/safety.c` and `libsafety_py.py`. Batch 3 (2026-10-05) adds `car/honda/tests/test_elesys_pump_brake_flags.py` (the two flags), and `elesys_brake.py` and `test_elesys_brake.py` (brake law v2).

| St | Path | Area | What the fork changes | Upstream commits | Last merge |
|---|---|---|---|---|---|
| A | `FORK.md` | — | Short fork index. | 0 | clean |
| M | `opendbc/car/car_helpers.py` | C | `skip_fw_query` argument on `fingerprint()` and `get_car()`. | 0 | auto |
| M | `opendbc/car/honda/carcontroller.py` | B+C | B: brake-release ceiling (`BRAKE_RELEASE_FRAMES`, `brake_release_scale`), the reported torque (`linbus_gateway_actuating()`; `new_actuators.torque = 0.0` while the board is not actuating, 2026-10), `serial_gateway` LDW bits, `SP_HUD_STATUS` send with `lat_ready` and `op_state`, `LKAS_HUD` not sent. C: `compute_gb_honda_elesys` (dispatched from `compute_gas_brake(accel, speed, CP)`), `brake_pump_hysteresis_elesys` and `ELESYS_PUMP_*`, since batch 3 (2026-10-05) pump rule C1 (`brake_pump_c1_elesys`, `ELESYS_PUMP_C1_*`, `self.elesys_pump_v6`, `self.pump_level`, `self.pump_trig`) picked by `HondaFlagsSP.ELESYS_PUMP_V6`, dynamic-tuner hooks (`hill_accel`/`adjust_accel`, `brake_gain`, `wind_scale`, the 32-count brake release), the soft final stop's construction and call (`ElesysSoftStop`, `HONDA_ELESYS` with the tuner on only), the NaN-`vEgo` guard in the brake block, the `CRUISE_OVERRIDE` decision comment (kept at 1), since batch 3 brake law v2's call sites (`brake_law_v2_enabled()` -> `self.elesys_brake_v2`, picked by `HondaFlagsSP.ELESYS_BRAKE_LAW_V2` with gas law v2; `blaw = law_frame(...)` feeding `actuator_hysteresis`, `blaw.brake_frac()` in place of the aero credit, `self.elesys_gas.window`), `SCM_BUTTONS` re-sent on `CAN.camera` every 4th frame when `openpilotLongitudinalControl`, `pcm_accel` computed from `adjust_accel`, and a `FORK:` comment explaining why there is no PCM crossfade. Since 2026-10-04 (C) `elesys_stock_acc`: in stock ACC mode neither longitudinal branch runs (`elif self.CP.openpilotLongitudinalControl:`), so only `0x0E4` and `0x500` go out - no cancel/resume spam, no `0xE5` - and the stand-down carries an explicit `not self.elesys_stock_acc`. | 0 | CONFLICT |
| M | `opendbc/car/honda/carstate.py` | A+B+C | A/B: registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION` and `GW_BUILD` liveness-exempt (`nan`), and calls `CarStateExt.update(ret, ret_sp, ...)`. C: `update_gear_elesys` / `SPORT_DWELL` (taken only when the gearbox frame has `GEAR`; the fuzz fix of 2026-10), ELESYS `stockAeb` (and `carFaultedNonCritical = True` when stock AEB fires with `ACC_HUD.ACC_ON == 0`), `LKAS_PROBLEM` read from bus 0 inside upstream's `if not (self.CP.flags & HondaFlags.BOSCH):`, `scm_buttons`, `econ_on`; since 2026-10-03 `VEHICLE_DYNAMICS` registered liveness-exempt with the gateway frames, for the VSA fault monitor, with its counter check off (`ignore_counter`; the checksum stays); since 2026-10-04 (`FORK(HONDA_ELESYS)`) `STEER_STATUS` 1 is not a fault on `HONDA_ELESYS` at a standstill in P (the key-on/key-off frames). Since 2026-10-04 (stock ACC mode) `accFaulted` from `BRAKE_ERROR` also with `HondaFlagsSP.ELESYS_STOCK_ACC`. | 0 | CONFLICT |
| M | `opendbc/car/honda/fingerprints.py` | C | `FW_VERSIONS[HONDA_ACCORD_9G_AU]`: fwdRadar `36707-T2M-Q640`, srs `77959-T2A-B110`. | 0 | auto |
| M | `opendbc/car/honda/hondacan.py` | B+C | B: `create_steering_control(serial_gateway, ldw_left, ldw_right)`, `SP_HUD_PROTOCOL_VERSION`=3, `SP_OP_STATE_*`, `SP_HUD_MAX_TORQUE`=0, `create_sp_hud_status()`. C: `create_brake_command(..., is_metric=True, elesys=False)` units bit, `create_scm_buttons_no_cruise()`. | 0 | CONFLICT |
| M | `opendbc/car/honda/interface.py` | C (+B) | Gearbox `0x188` → automatic. Long tuning: `longitudinalActuatorDelay` 0.6, `stopAccel` -0.8 (the stopping speed lives in sunnypilot's `stopping_tune.py`). `steerActuatorDelay` 0.18 (both lag fallbacks add 0.2, giving the measured 0.38), `steerAtStandstill` True, `latAccelOffset` seed -0.43 (2026-10), `ELESYS_SCM_STANDDOWN` safety parameter, `minEnableSpeed` 19 mph, and its exemption from the gas-interceptor -1 in `_get_params_sp()`. | 0 | CONFLICT |
| M | `opendbc/car/honda/radar_interface.py` | C | Elesys radar parser (`0x400`, `0x410`–`0x417`, `0x420`–`0x424` at 10 Hz), trigger `0x423`, `RADAR_STATE` ok in (104, 111, 125). | 0 | auto |
| A | `opendbc/car/honda/tests/test_elesys.py` | C (+B) | 76 unittest tests: gas/brake map, pump, gas curve, units bit, gear, AEB; since 2026-10 also the torque prior and offset seed, the reported torque (fake CS and `0x704` through the real `CarInterface`), the 2560 scale and the steering delay; since 2026-10-04 the key-off `STEER_STATUS` 1 (`TestElesysKeyOffSteerStatus`: real frames through the real `CarInterface`, a fault only parked); batch 3 (2026-10-05) pump rule C1 (`TestBrakePumpC1`, 18) and the controller's choice of rule (`TestElesysPumpRuleSelection`, 2). | 0 | clean |
| A | `opendbc/car/honda/tests/test_elesys_pump_brake_flags.py` | C | Batch 3 (2026-10-05): flags 16 and 32 from `_initialize_honda()` - the contract values, both off byte-identical to no hook (missing, `"0"`, 0, `False`, `None`, `b"0"`), each setting only its own bit, never in stock ACC mode or without openpilot longitudinal, never on another Honda (6 tests). | - | new |
| A | `opendbc/car/honda/tests/test_elesys_stock_acc.py` | C | Stock ACC mode: toggle-off CarParams/CarParamsSP and sends identical to no hook, the stock values (68), CarController over 1200 frames sends only `0x0E4` and `0x500`, `accFaulted`; since the merge with batch 2 (2026-10-05) `TestStockAccBesideBatch2`: with the dynamic tuner's toggle on, stock ACC mode builds no tuner and no shadow learners and still sends only `0x0E4` and `0x500` (17 tests). | - | new |
| M | `opendbc/car/honda/values.py` | C | `HondaSafetyFlags.ELESYS_SCM_STANDDOWN`=32, `HondaFlags.ELESYS`=1024, `CAR.HONDA_ACCORD_9G_AU`, `HONDA_ELESYS` (a frozenset), `STEER_THRESHOLD` 600, `non_essential_ecus`. Since 2026-10-04 `HondaSafetyFlags.ELESYS_STOCK_ACC`=64. | 0 | CONFLICT |
| M | `opendbc/car/structs.py` | A+B+C | `CarControlSP.LateralControl`, `CarStateSP.driverTorqueStale`, `CarStateSP.LinbusGateway` (control fields and `fw*` fields); since 2026-10-03 `CarStateSP.vsaFault` and `vsaStoredFault` (C). | 0 | auto |
| M | `opendbc/car/tests/routes.py` | C | `CarTestRoute("15646e8515eda1a7/00000019--dd0700eac9", HONDA_ACCORD_9G_AU)`. | 0 | auto |
| M | `opendbc/car/torque_data/override.toml` | C | `"HONDA_ACCORD_9G_AU" = [1.25, 1.25, 0.18]`, the car's own torqued prior, with a `FORK(HONDA_ACCORD_9G_AU)` comment (2026-10; 1.1 until 2026-10-03, learnable window now 0.875-1.625). **Any change of the board's authority or full scale must change it.** | 0 | new |
| M | `opendbc/car/torque_data/substitute.toml` | C | A `FORK(HONDA_ACCORD_9G_AU)` comment where `HONDA_ACCORD_9G_AU = HONDA_ACCORD` was (removed 2026-10; it held torqued at its 1.18 floor). A merge must not bring the line back: `test_elesys.py` fails if it does. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_gearbox_legacy.dbc` | C | `GEARBOX_AUTO` `0x188`, `GEARBOX_CVT`. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_honda_elesys_base.dbc` | C | This car's modified copy of `_honda_common.dbc` (differences listed under [DBC generator includes](#dbc-generator-includes-and-can-ids)); since 2026-10-03 also the provisional `VSA_FAULT_*` signals on `VSA_STATUS` and `VEHICLE_DYNAMICS`. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_lkas_hud_4byte.dbc` | C | 4-byte `LKAS_HUD` `0x33D`. | 0 | clean |
| M | `opendbc/dbc/generator/honda/_nidec_common.dbc` | C (**shared**) | `BRAKE_COMMAND` read-only signals `CMBS_BRAKE`, `CMBS_DISABLED` and `AEB_REQ_3`. They appear in every Nidec DBC. | 0 | clean |
| M | `opendbc/dbc/generator/honda/_nidec_scm_group_a.dbc` | C (**shared**) | `SCM_BUTTONS.CMBS_BUTTON` (read-only). | 0 | clean |
| A | `opendbc/dbc/generator/honda/_nidec_scm_group_a_elesys.dbc` | C | Copy of group A plus `FUEL_LEVEL`, `FUEL_SENDER`, `ODOMETER_KM`. | 0 | clean |
| A | `opendbc/dbc/generator/honda/_steering_control_e.dbc` | B+C | `0x0E4` 5-byte with `LDW_RIGHT`, `LDW_LEFT`, `SET_ME_X00_3` (B). `STEER_STATUS` `0x18F` with `STEER_CONTROL_ACTIVE` (C, read by B). | 0 | clean |
| A | `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc` | A+B | `0x500 SP_HUD_STATUS`, `0x700 EPS_LIN_RAW`, `0x704 GW_ACTIVE` and `0x70B GW_STEER_GRANT` (B). `0x707 GW_VERSION` and `0x70F GW_BUILD` (A). | 0 | clean |
| A | `opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc` | C | Generator top file: the 9 imports plus `ECON_STATUS` `0x221`; since 2026-10-03 also `VSA_1AA` (`0x1AA`) and `VSA_3D9` (`0x3D9`), provisional VSA-fault frames carstate does not read. | 0 | clean |
| A | `opendbc/dbc/honda_accord_2015au_radar.dbc` | C | Elesys radar DBC (hand-written, not generated). | 0 | clean |
| M | `opendbc/safety/modes/honda.h` | C (+B) | `ELESYS_SCM_STANDDOWN` (param 32): TX lists with `0x1A6` on bus 2 and `0x500` on bus 0 (B), and **without** `0x33D`; AEB bit 43; the `pcm_gas` 198 exception; blocking `0x1A6` bus 0→2; `honda_bosch_init()` resets `honda_elesys_scm_standdown = false`. Since 2026-10-04 `ELESYS_STOCK_ACC` (param 64): TX `0xE4`/`0x194` relay-checked and `0x500` only, plus the radar's `0x1FA`/`0x30C` on bus 0 as relay checks only (`HONDA_N_ELESYS_STOCK_ACC_RELAY_CHECK`, `disable_static_blocking`, refused by `honda_tx_hook()`), interceptor forced off, the forward hook blocks nothing; 32 and 64 together transmit nothing, keep that relay check and forward everything. | 0 | clean |
| M | `opendbc/safety/tests/common.py` | C (+B) | Scanned-range exceptions for `TestHondaElesys` and `0x1A6`, and `0x500` between the two `TestHondaElesys*` classes only. | 0 | auto |
| M | `opendbc/safety/tests/libsafety/libsafety_py.py` | C | Declares `get_honda_elesys_stock_acc()` (2026-10-04, stock ACC mode). | 0 | new |
| M | `opendbc/safety/tests/libsafety/safety.c` | C | `get_honda_elesys_stock_acc()`, the test getter for the stock ACC flag, which has no other observable effect after a Bosch init (2026-10-04). | 0 | new |
| M | `opendbc/safety/tests/test_honda.py` | C+UF | `TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety`. Since 2026-10-04 `TestHondaElesysStockAccSafety` (param 68: TX list, forward-all, interceptor ignored, engagement, flag reset, the relay check on the radar's frames with `honda_elesys_wire()` open and closed) and `TestHondaElesysStockAccStanddownConflictSafety` (4\|32\|64: transmit nothing, forward everything, the same relay check). Since 2026-10-04 (UF, `FORK(UPSTREAM-FIX)`) `test_route_114_lkas_regrant_survives_the_next_heartbeat_tick` with route 114's 50 real `0x1A6` frames, since 2026-10-05 in the mixin `HondaElesysRoute114Regrant`, so it runs under the stand-down (36) and stock ACC mode (68). | 0 | clean |
| M | `opendbc/safety/sunnypilot/mads.h` | UF | `m_update_control_state()`: the lateral grant zeroes `heartbeat_engaged_mads_mismatches` (2026-10-04, `FORK(UPSTREAM-FIX)`), as upstream's `safety.h` does for the longitudinal count; a grant inside the tick interval after a heartbeat exit was revoked by the stale count (route 114, `controlsMismatchLateral`). Every MADS car; needs a panda flash. Offer upstream. | 0 | new (2026-10-04) |
| M | `opendbc/safety/tests/mads_common.py` | UF | `test_heartbeat_engaged_mads_regrant_is_not_revoked_by_a_stale_count`, `..._still_exits_on_three_fresh_mismatches`, and `test_heartbeat_engaged_mads_exits_with_can_traffic_between_ticks` / `..._regrant_exits_with_can_traffic_between_ticks` (a second of CAN before each 1 Hz tick: a reset on every received frame would switch the exit off) (2026-10-04, `FORK(UPSTREAM-FIX)`), inherited by every MADS safety test class. | 0 | new (2026-10-04) |
| M | `opendbc/sunnypilot/car/car_list.json` | C | `"Honda Accord 2013-15"` → `HONDA_ACCORD_9G_AU`. | 0 | auto |
| M | `opendbc/sunnypilot/car/honda/carstate_ext.py` | A+B+C | A: `_update_linbus_firmware`. B: `_update_linbus_gateway`, `_update_linbus_grant`, `_update_driver_torque_validity`, `_eps_lin_driver_torque_valid`. C: `fuelGauge`; since 2026-10-03 `_update_vsa_fault()` (never raises; reads `VSA_STATUS` and `VEHICLE_DYNAMICS`); since 2026-10-04 `self.pcm_pedal_gas` (0x17C `PEDAL_GAS` on Elesys, nan elsewhere; not a `CarState` field), the shadow launch learner's pedal confirmation. | 0 | auto |
| M | `opendbc/sunnypilot/car/honda/values_ext.py` | C | `HondaFlagsSP.ELESYS_STOCK_ACC` = 8 (2026-10-04); `ELESYS_PUMP_V6` = 16 and `ELESYS_BRAKE_LAW_V2` = 32 (batch 3, 2026-10-05). | 0 | new |
| M | `opendbc/sunnypilot/car/interfaces.py` | C | `_initialize_honda()` (2026-10-04): the stock ACC mode's one writer, called last in `setup_interfaces()`. Batch 3 (2026-10-05): with openpilot longitudinal and not stock ACC mode it sets `ELESYS_PUMP_V6` / `ELESYS_BRAKE_LAW_V2` from `HondaElesysPumpV6` / `HondaElesysBrakeLawV2` (`_param_is_on()`; a missing key is off). Upstream sunnypilot file. | 0 | new |
| A | `opendbc/sunnypilot/car/honda/vsa_fault.py` | C | `VsaFaultMonitor`: `carStateSP.vsaFault` (0x1A4 b2.2/b2.3, or 0x1EA b6.2 with `accFaulted` after the start-up window) and `vsaStoredFault` (0x1A4 b3.3/b4.0/b6.0 from the first frame, b3.6/b3.7 - which the bulb check lights - after a 5 s start-up window; 0.5 s set and clear debounce, False on a 0.5 s silence). The bits are provisional; the measured basis is in its docstring. | - | new (2026-10-03) |
| A | `opendbc/sunnypilot/car/honda/test_vsa_fault.py`, `opendbc/sunnypilot/car/honda/fixtures/vsa_fault_frames.json.gz` | C | Real frames from routes 110, 112, 111, 113, 10f and comma route 69 through the real `CarInterface`, the DBC decode, the parser registration, other Hondas, never raising, 0x1EA's counter, a stored start with card 2.1 s late, 10f's bulb check stretched past the longest seen, the monitor's timing (33 tests). The fixture is 52 KB of real bus-0 frames. | - | new (2026-10-03) |
| A | `opendbc/sunnypilot/car/honda/dynamic_tuning.py` | C | `HondaDynamicTuner` (self-learning longitudinal: the brake gain and the pitch term; the pedal and aero learners were retired in 2026-10), the per-drive-mode data counter (`observe_pedal`), and `filtered_pitch()` for the soft final stop. Since 2026-10-04 builds the shadow learners (`self.shadow` from `_build_shadow()`, which imports `shadow_learn.py` itself under a try: Elesys Accord with the interceptor and the toggle on, else None), records copies of the frame's brake fraction and the brake gain it returns (`brake_gain()`) and pedal (`observe_pedal()`), and feeds them from `update_wind()`, the last tuner call of the 50 Hz block (`_shadow_update()`, never raises). Batch 3: a logging mode with the toggle off (the shadow learners, the pitch filter they read, `modemov` and a `tuner=0` hondadyn line; nothing applied or persisted), all moving time per drive mode (`mode_moving`, logging only), the build tags (`build`: pump rule and brake law from CP_SP flags 16/32, commit), totals persisted at every disengage and at card's exit (`flush_at_exit()`, `_ParamWriter.write_now()`), and the 0x37C / S comments corrected; the brake gain held at exactly 1.0, learning nothing, while brake law v2 runs (`brake_law_v2`, `set_brake_law_v2()`; the stored gain kept). | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/shadow_learn.py` | C | SHADOW longitudinal learners (2026-10-04, owner decision 9, A_synth L1/L2b): the brake response table (speed band x command band, achieved - commanded accel), the coast deceleration per speed band, the launch ratio and the bounded multiplier it would apply (no-lead launches with the pedal confirmed on 0x17C; behind a lead logged apart); every sample only after a clean second (engaged PID, no driver pedal, no stock AEB); the live brake gain logged beside the table; logged as a `hondashadow` line once a minute and at each disengage; applied to nothing. Batch 3: `v=2` lines carry the build tags (`BUILD_KEYS`: commit, gas law, cap, pump rule, brake law, tuner), the launch's clean second runs through the stop (sampled from first wheel motion), and `flush()` gives the last line at card's exit. | - | new (2026-10-04) |
| A | `opendbc/sunnypilot/car/honda/test_shadow_learn.py` | C | Byte-identical CAN through the real `CarController` with and without the shadow, a raising shadow switched off, one that cannot be built leaving the controller working, bands, bounds, every gate, nothing admitted until a clean second after any override or engagement, the 0x17C pedal confirmation, lead launches apart, the interceptor gate, the grade, the log cadence and `bgain`, garbage in; (batch 3) a launch from a held stop sampled from first motion, the tuner off with the shadow on changing no CAN against a tuner without logging mode, the tags against the flags and `GitCommit`, the manual moving-time counter, persisting at a disengage, the synchronous exit flush, `write_now()` never overtaken, the exit hook only on the device (30 tests). | - | new (2026-10-04) |
| A | `opendbc/sunnypilot/car/honda/elesys_gas.py` | C | This car's gas law: v1 (`elesys_gas_multiplier`, `ELESYS_GAS_BP`/`ELESYS_GAS_V`) and v2 (`ELESYS_FF_*`, `elesys_pedal_v2`, and since 2026-10-04 the launch cap below 6 m/s, `LAUNCH_CAP_*`, `elesys_launch_cap` - v2 only: v1 and the exception fallback to v1 stay uncapped), picked by `HondaElesysGasLawV2`; drive-mode slots, `MODE_K` and the crossfade (`ElesysGasLaw`). Batch 3: `elesys_pedal_v2_window()` and `ElesysGasLaw.window`, the pedal window brake law v2 moves (set only on its frames; None leaves v2 exactly as before). | - | new (2026-10) |
| A | `opendbc/sunnypilot/car/honda/elesys_stop.py` | C | The soft final stop: a brake ceiling while still rolling in the stopping state (`soft_stop_ceiling`, `SOFT_STOP_*`), wrapped for `CarController` by `ElesysSoftStop` (the `hondastop` log line, never raises). | - | new (2026-10) |
| A | `opendbc/sunnypilot/car/honda/elesys_brake.py` | C | Batch 3 (2026-10-05): brake law v2, "Measured brake law (testing)" (`HondaElesysBrakeLawV2`, flag 32, default off): the measured coast curve replaces the aero credit, a soft dead zone `c0(v)` and slope `k(v)` per band, nothing sent in the coast band, the gas law's pedal-zero point at `coast + DELTA` and G0 on the current pedal calibration; today's path below 4 m/s, outside PID and on non-finite input; needs gas law v2 (`law_frame()`, `BrakeLawV2Frame`, `brake_law_v2_enabled()`; CAR doc 7.10). | - | new (batch 3) |
| M | `opendbc/sunnypilot/car/honda/gas_interceptor.py` | C | Imports `HONDA_ELESYS` and `elesys_gas` (re-exporting the v1 names); on `HONDA_ELESYS` builds `ElesysGasLaw` and calls it instead of upstream's line; the `tuner` hook `observe_pedal`. Every other car runs upstream's line. All hunks `FORK(HONDA_ELESYS)`/`FORK:`. | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | C | Script-style tests of the tuner. | 0 | clean |
| A | `opendbc/sunnypilot/car/honda/test_elesys_gas.py` | C | The gas law, its shape conditions, the crossfade, the slots and the param; since 2026-10-04 the launch cap (`TestLaunchCap`, route 115's launch) (38 tests). | - | new (2026-10) |
| A | `opendbc/sunnypilot/car/honda/test_elesys_stop.py` | C | The soft final stop: every timer, the grade term, the gate, the bound from entry, the entry-speed bound, random-input invariants, never raising (28 tests). | - | new (2026-10) |
| A | `opendbc/sunnypilot/car/honda/test_elesys_brake.py` | C | Batch 3: brake law v2 against the fit's golden values, the coast band and brake-on jump, monotonic and continuous, today's path where it must be, and through the real `CarController` (flag clear never builds a frame, gas law v1 vetoes it, the gain held at 1.0, the stop unchanged, never raising) (29 tests). | - | new (batch 3) |
| A | `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | C+B | Script-style checks through `CarController`, with a `TestCase` wrapper. Sections [7], [8] and [10]–[15] are B; [16], [17], [17b], [18] and [19] (2026-10) are the gas law, the never-raise checks, the NaN-`vEgo` brake block, `CRUISE_OVERRIDE` with brake 0 after a disengage or a pedal, and the soft final stop. | 0 | clean, then edited for the new signatures |

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
| `CarStateSP` | `linbusGateway @1 :LinbusGateway`, `driverTorqueStale @2 :Bool`; since 2026-10-03 `vsaFault @3 :Bool`, `vsaStoredFault @4 :Bool` (C) | `@0` (`speedLimit`) | **none** |
| `CarStateSP.LinbusGateway` (fork-only) | `engaged @0`, `dryRun @1`, `valid @2`, `actuating @3`, `present @4`, `grantValid @5`, `grantState @6 :UInt8`, `grantReason @7 :UInt8`, `granted @8`, `authority @9 :UInt8`, `epsAck @10`, `epsLatched @11`, `epsErrorState @12 :UInt8`, `epsFresh @13`, `camLkasOn @14`, `applied @15 :Int16`, `motorTorque @16 :Int16`, `retryIn @17 :UInt8`, `latchedUntilKeyOff @18` (all B). `fwValid @19`, `fwGitHash @20 :UInt32`, `fwDirty @21`, `fwAppSlot @22`, `fwBootloader @23`, `fwReadOnly @24`, `boardUid @25 :UInt32`, `fwBuildValid @26` (all A). | n/a | none |
| `OnroadEventSP.EventName` (2026-10-01) | `lkasGatewayEpsLatched @26`, `lkasGatewayEpsLatchedReminder @27` (B) | `@25` (`bigModelReady`) | **none** today. Upstream's next event takes `@26`: give it the ordinal and move these two up. An event is only an enum value, so a renumber costs nothing but the meaning of `onroadEventsSP` in older routes. Since 2026-10-03 also `vsaFault @28`, `vsaStoredFault @29`, `vsaFaultAnnounce @30` (C); the same rule applies to them. Since 2026-10-04 also `hondaElesysStockAcc @31` (C), same rule. |

The merge kept every fork ordinal. No upstream struct is named `LateralControl` or `LinbusGateway`.

**The standing risk.** If upstream adds a field to `CarControlSP` or `CarStateSP`, it takes `@5`, or `@1`/`@2`. A
duplicate ordinal is loud: the capnp compile fails. The fix is to give upstream its ordinal and move the fork field to
the next free one. Routes recorded before the renumber then decode that field wrong.

**The dataclass twin.** `opendbc/car/structs.py` mirrors these structs. What must match is the **names**:
`convert_to_capnp()` calls `custom.CarStateSP.new_message(**asdictref(struct))`, which is keyword-based, so a missing or
extra name raises when card publishes, on a drive. The order is not load-bearing, and the fork already differs at the
top level (`structs.py` lists `speedLimit`, `driverTorqueStale`, `linbusGateway`; capnp has `linbusGateway @1`,
`driverTorqueStale @2`). Three tests guard this, each narrower than its name suggests:

* `test_capnp_and_dataclass_agree` (`test_gateway_board_settings.py`) checks `LinbusGateway` only: the eight `fw*`
  fields exist on both sides and in the same relative order, and no `LinbusGateway` ordinal is duplicated.
* `test_car_control_sp_seam` (`test_car_control_sp_seam.py`) checks that every nested `CarControlSP` struct is rebuilt
  by `convert_carControlSP()` in `helpers.py`, and round-trips `linbusGateway` including the `fw*` fields.
* Since 2026-10-03, `TestCarStateSPAgreement` (`test_vsa_fault_alert.py`) checks the **top level** of `CarStateSP`: the
  same set of field names in `custom.capnp` and `structs.py`, ordinals unique and contiguous from `@0`, the two VSA
  fields `Bool`, and a round trip of both through `convert_to_capnp()`. It still does not look inside `LinbusGateway`.

An end-to-end probe after the merge also pushed a `CarStateSP` with every gateway field set to a non-default value
(negative `Int16`s, a `UInt32` hash with the top bit set) through `convert_to_capnp()` without loss.

### `openpilot/common/params_keys.h`

| key | flags | type | default | area |
|---|---|---|---|---|
| `HondaDynamicTuningEnabled` | PERSISTENT, BACKUP | BOOL | "0" | C |
| `HondaDynBrakeGain` | PERSISTENT | FLOAT | "0.0" | C |
| `HondaDynModeSecD`, `HondaDynModeSecECON`, `HondaDynModeSecS` | PERSISTENT | FLOAT | "0.0" | C (2026-10) |
| `HondaElesysGasLawV2` | PERSISTENT, BACKUP | BOOL | "1" | C (2026-10) |
| `HondaElesysStockAcc` | PERSISTENT (deliberately not BACKUP: a restore must never turn stock ACC mode on) | BOOL | "0" | C (2026-10-04) |
| `HondaElesysStockAccSaved` | PERSISTENT | JSON | – | C (2026-10-04), the settings snapshot |
| `HondaElesysPumpV6` | PERSISTENT, BACKUP | BOOL | "1" | C (batch 3, 2026-10-05): the brake pump rule, 1 = C1 ("Quieter brake pump"), 0 = v5. Read once at ignition by `_initialize_honda()` into `HondaFlagsSP.ELESYS_PUMP_V6` (16) |
| `HondaElesysBrakeLawV2` | PERSISTENT, BACKUP | BOOL | "0" | C (batch 3, 2026-10-05): the brake law, 1 = v2 ("Measured brake law (testing)"). Read once at ignition into `HondaFlagsSP.ELESYS_BRAKE_LAW_V2` (32); runs only with gas law v2, and holds the brake gain at 1.0 (CAR doc 7.10) |
| `EpsLkasBoardVersion` | PERSISTENT | STRING | – | A |
| `EpsLkasBoardBuild` | PERSISTENT | JSON | – | A |
| `EpsLkasBoardSeenAt` | PERSISTENT | STRING | – | A |
| `EpsLkasFlashRequested` | CLEAR_ON_MANAGER_START | BOOL | – | A |
| `EpsLkasFlashProgress` | CLEAR_ON_MANAGER_START | STRING | – | A |
| `EpsLkasFlashState` | CLEAR_ON_MANAGER_START | STRING | – | A |
| `EpsLkasFlashTrace` | PERSISTENT | JSON | – | A |
| `MadsEmergencySteerDisable` | PERSISTENT, BACKUP | BOOL | "1" | B |
| `MadsEmergencySteerRate` | PERSISTENT, BACKUP | INT | "200" | B |
| `UpdaterDownloadProgress` | CLEAR_ON_MANAGER_START | JSON | – | UPD |
| `HudSpeedCluster`, `HudSchoolZoneCue`, `HudVariableLimitSign`, `HudStoppedTimer`, `HudStoppedBanner`, `HudConfirmLimit`, `HudPlannedStop`, `HudCurve` | PERSISTENT, BACKUP | BOOL | "1" | HUD (2026-10-03) |
| `HudNextLimit` | PERSISTENT, BACKUP | INT | "3" (0 off, 1 bar, 2 distance, 3 both) | HUD (2026-10-03) |
| `HudCompactLimitPrompts`, `HudCompactDisengage`, `HudCompactTurn` | PERSISTENT, BACKUP | BOOL | "1" | HUD (2026-10-04) |
| `HudLimitSign` | PERSISTENT, BACKUP | INT | "2" (0 off, 1 school and variable zones, 2 always) | HUD (2026-10-04) |
| `HudCurrentSpeed` | PERSISTENT, BACKUP | BOOL | "1" | HUD (2026-10-06) |

The shadow learners of 2026-10-04 (`shadow_learn.py`, `lat_speed_split.py`) add **no** key: they ride on the gas
interceptor and openpilot longitudinal (longitudinal; up to batch 2 also on `HondaDynamicTuningEnabled`, since batch 3
with it on or off) and on the car (lateral), keep per-drive totals in memory and write nothing. The batch-3 logging
fixes add no key either: the tags read `GitCommit` and the CarParamsSP flags, and the moving-time counter is logged,
never stored.

Upstream's file at `a5f44653d` has 264 entries and none of the 16 A and C names; the merged file has 280. The fork's two
blocks sit between stable neighbours: `HideVEgoUI`/`IntelligentCruiseButtonManagement` and
`InteractivityTimeout`/`IsDevelopmentBranch`. Upstream still defines the `FLOAT` and `JSON` types. The two B keys came
after that merge (2026-09-30) and sit inside upstream's `// MADS params` block, between `Mads` and
`MadsMainCruiseAllowed`. The 3 SL keys are named in the `params_keys.h` row of the file table above; the 5 NSW keys
are in [NSW-SPEED-ZONES.md](NSW-SPEED-ZONES.md). The UPD key (2026-10-01) sits in upstream's `Updater*` run, between
`UpdaterCurrentReleaseNotes` and `UpdaterFetchAvailable`; MVL's unmerged branch calls its version `UpdaterProgress`
(an INT), so the name is deliberately different. The file has 291 entries on 2026-10-01 (290 on `nsw-live`, plus this one) and no duplicate. The nine HUD keys
(2026-10-03) sit in one marked block between `HondaElesysGasLawV2` and `IntelligentCruiseButtonManagement`, in
alphabetical order (the right rail's `HudCurve` and `HudPlannedStop` among them, under a second marker); no upstream
or sunnypilot key starts with `Hud`, and the duplicate check below still prints nothing. On 2026-10-03 the file has 297
entries (288 on `nsw-live` at `8b7ec463a`, plus the nine HUD keys); the `grep -oE` below prints one more, the
`{"phase"` inside the `UpdaterDownloadProgress` comment. Batch 2 (2026-10-04) adds four HUD keys to the same block, still
alphabetical (`HudCompact*` before `HudConfirmLimit`, `HudLimitSign` between `HudCurve` and `HudNextLimit`), with two
more marker comments so every key has one within ten lines (`test_hud_cluster.py` checks it). With stock ACC mode's two
`HondaElesysStockAcc*` keys beside them (2026-10-05 merge) the file has 303 entries and no duplicate. `HudCurrentSpeed`
(2026-10-06) sits between `HudConfirmLimit` and `HudCurve`, under its own marker comment.

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
| `HondaSafetyFlags.ELESYS_STOCK_ACC` / `HONDA_PARAM_ELESYS_STOCK_ACC` (`honda.h`) | 64 (2026-10-04) | free; 32 and 64 together are never sent and the panda reads them as forward-all, transmit-none |
| `HondaFlagsSP.ELESYS_STOCK_ACC` (`values_ext.py`) | 8 (2026-10-04) | sunnypilot uses 1, 2, 4; free |
| `HondaFlagsSP.ELESYS_PUMP_V6`, `ELESYS_BRAKE_LAW_V2` (`values_ext.py`) | 16, 32 (batch 3, 2026-10-05) | free; a sunnypilot merge that takes either must move ours |
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
  * the comments on 304 and 316 are `CM_ BO_` instead of the original `CM_ SG_`;
  * since 2026-10-03, nine provisional `VSA_FAULT_*` signals on `VSA_STATUS` (`0x1A4`) and `VSA_FAULT_INERTIAL_INVALID` on
    `VEHICLE_DYNAMICS` (`0x1EA`), with a `CM_ SG_` for each.
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

The 2026-10 lateral batch added three more: `openpilot/selfdrive/locationd/test/test_torqued_elesys.py` (11),
`openpilot/selfdrive/locationd/test/test_lagd_elesys.py` (5) and
`openpilot/sunnypilot/selfdrive/controls/lib/tests/test_latcontrol_reported_torque.py` (7).
Added since (2026-10-01): `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_eps_latch_alert.py` (19),
`openpilot/sunnypilot/system/updated/tests/test_download_progress.py` (27) and
`openpilot/selfdrive/ui/tests/test_software_update_button.py` (6; it opens a headless raylib window in a child process
and skips where none opens). The two font-metric tests skip where Pillow or the LFS fonts are missing. Added on 2026-10-03:
`openpilot/sunnypilot/selfdrive/selfdrived/tests/test_vsa_fault_alert.py` (47), whose font-metric test skips the same way.
Added 2026-10-03 (HUD): `openpilot/selfdrive/ui/tests/test_hud_cluster.py` (71, with the right rail's 26) and
`openpilot/selfdrive/ui/tests/test_hud_render.py` (24, with the rail's 8; the real onroad view in a headless raylib
window in a child process, about two minutes, skipping where none opens; `HUD_RENDER_OUT=<dir>` keeps its PNGs).
Batch 2 (2026-10-04, UI): `test_hud_cluster.py` is 90 after the round-1 review (88 before it; the sign setting and the compact alerts, with the allow-list's
SAFETY walk of every event) and `test_hud_render.py` 35 (32 before; about four minutes now); new
`openpilot/sunnypilot/selfdrive/selfdrived/tests/test_speed_limit_pre_active_alert.py` (7) and
`openpilot/sunnypilot/selfdrive/car/tests/test_speed_limit_confirm_buttons.py` (12 since the 2026-10-05 narrowing; 9 before).

`test_stopping_debounce.py` no longer stubs `sys.modules`; it imports the real `longcontrol`, `drive_helpers` and
`stopping_tune`.

Added 2026-10-04 (shadow learners): `openpilot/sunnypilot/selfdrive/locationd/tests/test_lat_speed_split.py` (10),
`openpilot/sunnypilot/tools/tests/test_shadow_learn_report.py` (4; 7 since batch 3), and in opendbc `opendbc/sunnypilot/car/honda/test_shadow_learn.py` (an ordinary unittest module).
Added in batch 3 (route check): `openpilot/sunnypilot/tools/tests/test_brake_route_check.py` (10; its parquet test runs
only where pyarrow is installed, which the repo's venv is not).

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
  **The flip side: a file the fork adds must never be an LFS pointer.** `.gitattributes` puts every `*.png`, `*.svg`,
  `*.ttf`, `*.wav` (and more) into LFS, the skip above means the fork never uploads an LFS object, and the LFS server
  is sunnypilot's anyway - so a new fork PNG would reach the car as a 130-byte pointer. Exempt each such file by path
  at the end of `.gitattributes` (as `FORK(HUD)` does for `icons_mici/gateway.png`), then `git rm --cached` and
  `git add` it again; `git lfs ls-files` must not list it.
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
git grep -n -E "FORK(\(|:)" -- opendbc | wc -l                           # 49 since the 2026-10 batch (31 after the 2026-09 merge, 18 before it)
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
* `openpilot/common/params_keys.h`: every key in the params table above present (7 A, 8 C, 2 B, 1 UPD, 13 HUD), plus the 3 SL keys
  and the 5 `FORK(NSW-ZONES)` keys; the duplicate check above prints nothing.
* `openpilot/selfdrive/car/card.py`: `skip_fw_query=`, `stage_board_firmware(CS_SP)` at the end of `state_publish`,
  and `write_board_firmware()` plus `log_flash_trace()` in `params_thread`.
* `openpilot/selfdrive/car/helpers.py`: the `lateralControl` rebuild.
* `openpilot/selfdrive/car/cruise.py`: `v_cruise_kph_prev` taken just before the set-speed change in
  `_update_v_cruise_non_pcm()`, and the `update_speed_limit_assist_pre_active_raise_blocked()` restore after the clip,
  last in that method (`test_speed_limit_confirm_buttons.py` fails if either is lost).
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
`git grep -n -E "FORK(\(|:)|linbus|LIN-bus|HONDA_ELESYS|HondaDyn|driverTorqueStale" -- opendbc | wc -l` prints 247
since the 2026-10 batch (210 after the 2026-09 merge, 199 at `c61cfd9b`).

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
python -m unittest opendbc.car.honda.tests.test_honda opendbc.car.honda.tests.test_elesys   # 97 tests (96 in test_elesys; batch 3 added the pump rule's 20)
python -m unittest opendbc.sunnypilot.car.honda.test_elesys_gas                            # 38 tests: the gas law and the launch cap
python -m unittest opendbc.sunnypilot.car.honda.test_elesys_stop                           # 28 tests: the soft final stop
python -m unittest opendbc.car.honda.tests.test_elesys_stock_acc                          # 17 tests: stock ACC mode
python -m unittest opendbc.car.honda.tests.test_elesys_pump_brake_flags                   # 6 tests: the pump and brake-law flags (batch 3)
python -m unittest opendbc.sunnypilot.car.honda.test_elesys_brake                          # 29 tests: brake law v2 (batch 3)
python -m unittest opendbc.sunnypilot.car.honda.test_shadow_learn                          # 30 tests: the shadow learners and the batch-3 logging fixes
python -m unittest opendbc.safety.tests.test_honda                                          # builds libsafety; 1091 run, OK (skipped=73) (2026-10-05, stock ACC mode + batch 2)
python -m unittest opendbc.car.tests.test_car_interfaces -k HONDA_ACCORD_9G_AU
python -m unittest discover -s opendbc/sunnypilot/car -t .                                  # 143 tests (2026-10-05, stock ACC mode + batch 2), including the integration script
python opendbc/sunnypilot/car/honda/test_dynamic_tuning.py                                  # ALL CHECKS PASSED
python opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py                      # ALL CHECKS PASSED; §15 SKIPs without openpilot
PYTHONPATH=$HOME/sp-merge python opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py   # §15 runs
python -m unittest discover                                                                 # 10231 run, OK (skipped=1272) (2026-10-05, stock ACC mode + batch 2)
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
   * With `HondaElesysStockAcc` off (the default), stock ACC stays stood down (`pandaStates.safetyParam == 36`).
   * `shouldStop` asserts at about 0.8 m/s approaching a stop, and stops hold at `stopAccel` without rolling.
   * With the tuner on, each openpilot stop logs one `hondastop` line, the brake stays at the soft-stop ceiling until
     the wheels read zero and reaches the hold (189) about 0.8 s later (`CAR-HONDA-ACCORD-9G-AU.md` 7.8). No 0x1FA gap
     over 0.1 s, and `BRAKE_ERROR` only in the first `0x1B0` frame.
   * It does not engage below 19 mph.
   * Gear reads P/R/N/D.
   * The `HondaDyn*` values change after 60 s when the tuner is on.
9. **Flashing, only if a new board image is bundled.** Update through Settings > gateway. The next drive's log should
   carry "eps-lkas flash trace".
10. **Stock ACC mode, once per merge.** One drive with `HondaElesysStockAcc` on: the checks in
    [CAR-HONDA-ACCORD-9G-AU.md](CAR-HONDA-ACCORD-9G-AU.md) 15.8 (`safetyParam == 68`, no relay malfunction, `0x1FA`/`0x30C`
    never src 0, the accelerator through the pedal, only `0x0E4`/`0x500` in `sendcan`, no `BRAKE_ERROR`), then off
    again and the settings are back.

### Step 8: land it

Opendbc `sp-master` should already be pushed from Step 1 (confirm with `git ls-remote origin sp-master`). Fast-forward
sunnypilot `master` to the merge branch and push it with `GIT_LFS_SKIP_PUSH=1`. The car does not see master directly
([How the car gets updates](#how-the-car-gets-updates)). A sync changes `.github/workflows/`, so expect the mirror to
hold it and push it to `SoRadGaming/openpilot` `sunnypilot` by hand with the commands from the run summary, unless a
`MIRROR_TOKEN` is set. Before landing, also push a `pre-<date>` snapshot of the old master to this repo; the mirror
publishes it as a rollback on the device. Only call it a rollback if the merged `updated.py` can install it: when the
merge moves `agnos.json` or changes the tree's layout, it cannot (that is why `pre-upstream-2026-09` was deleted on
2026-10-01, see [How the car gets updates](#how-the-car-gets-updates)); keep it as a tag instead. When the merge only
changes `AGNOS_VERSION`, installing the snapshot flashes its older AGNOS, a downgrade; try it before calling it a
rollback. Bring
`S:/OP/sp-live` up to date from master: its layout changes with the merge.

The firmware repo's `tools/bundle_appslot.py` finds `eps_lkas_flasher.py` in either layout (nested first, since
`862540c`), and `docs/CAN-UPDATE.md` names the nested path, so nothing there needs changing unless upstream moves the
tree again.

Update this file (the fork points become the new upstream heads automatically, then the counts and the inventory), and
write the next `UPSTREAM-<date>.md`.

---

## Conventions for carrying custom code

### What the fork already does

* **Markers.**
  * opendbc: `FORK(HONDA_ACCORD_9G_AU)` ×23, `FORK(HONDA_ELESYS)` ×19, `FORK(LKAS-GATEWAY)` ×1, bare `FORK:` ×6 (49
    lines since the 2026-10 batch: the gas law added six (`gas_interceptor.py` and the headers of `elesys_gas.py` and its
    test), the braking work seven (five in `carcontroller.py` - the soft final stop's import, construction and call, the
    NaN-`vEgo` guard, the `CRUISE_OVERRIDE` decision - and the headers of `elesys_stop.py` and its test), and the lateral
    work five (the two `torque_data` comments, the `latAccelOffset` seed and the two reported-torque hunks)).
  * sunnypilot: `FORK(HONDA_ACCORD_9G_AU)` ×18, `FORK(LKAS-GATEWAY)` ×13, `FORK(GATEWAY-UPDATE)` ×3, bare `FORK:` ×3
    (37 lines), in `drive_helpers.py`, `longitudinal_planner.py`, `longcontrol.py`, `desire_helper.py`, both
    `modeld.py`, `controlsd.py`, `selfdrived.py`, `pandad.py`, mici `settings.py`, `mads.py`, `state.py`,
    `latcontrol_torque_ext_base.py`, `joystickd.py` and `maneuversd.py`. The 2026-10 longitudinal work added
    `FORK(HONDA_ELESYS)` ×6 on its hunks in `params_keys.h` (2), `statsd.py` (1), sunnylink's `vehicle.yaml` (2)
    and `cruise.yaml` (1); the older hunks in those files still carry prose only.
    On 2026-10-01, with SL, NSW-ZONES, the fast-wheel settings and the 2026-10 batch, `git grep -n -E "FORK(\(|:)" -- openpilot`
    prints 218 lines (156 before the batch): `FORK(UPDATER)` marks `agnos.py`, `updated.py`, `params_keys.h` and the
    mici `software.py`, and `FORK(LKAS-GATEWAY)` now also marks the latch alert in `selfdrived.py`, `custom.capnp`
    and the sunnypilot `events.py`. In those upstream files every changed line is marked or sits under a marker in
    its own `git diff -U0` hunk, except `updated.py`'s deleted `# TODO: show agnos download progress`.
  * The HUD (2026-10-03) adds `FORK(HUD)` on its hunks in `augmented_road_view.py` (6: cluster and rail, three each),
    the mici `confidence_ball.py` (2), the mici
    `alert_renderer.py` (3), `params_keys.h` (2, over the block and over the rail's two keys), the mici `settings.py` (3: the `cloudlog` import,
    `gateway_icon()` and the tile's comment), sunnylink's `visuals.yaml` (1) and `.gitattributes` (1, the LFS
    exemption); its new files carry it in their headers. Batch 2 (2026-10-04) adds two in `augmented_road_view.py`
    (the `hud_alerts` import and the top-icons line, 8 in all), two in `params_keys.h` (4 in all) and reworded the
    `alert_renderer.py` hook's; `FORK(SPEED-LIMIT)` on the sunnypilot `events.py` text fix (2: import and hunk) and
    `cruise_ext.py`'s wrong-button block (1).
  * `git grep -n -E "FORK(\(|:)"` lists them. A plain `git grep FORK` also hits upstream Tesla DBC strings.
  * The older sunnypilot hunks still carry prose markers only: "LIN-bus gateway:", "HONDA_ELESYS:", "EPS-LKAS".
* **Per-car gating, so other cars keep upstream behaviour:**
  * `HondaFlags.ELESYS` / `HONDA_ELESYS` gates the car-specific opendbc branches: gas curve, pump, brake units bit,
    gear, AEB, `LKAS_PROBLEM`, the `SCM_BUTTONS` re-send, the serial-gateway steering, `LKAS_HUD` suppression, the
    radar parser, the `carstate_ext` gateway decode, and the `minEnableSpeed` exemption.
  * The dynamic-tuner hooks are **not** ELESYS-gated. In `carcontroller.py` (`hill_accel`/`adjust_accel`, which also
    feeds `pcm_accel`; `brake_gain`; `wind_scale`, a constant 1.0 since 2026-10; the 32-count brake release) and
    `gas_interceptor.py` (`observe_pedal`, which only counts) they are gated by the tuner itself: `HondaDynamicTuningEnabled`, and
    `HondaDynamicTuner._is_applicable()` = `openpilotLongitudinalControl and carFingerprint not in HONDA_BOSCH`. So they
    reach any Nidec Honda with openpilot longitudinal once the toggle is on. With the toggle off they are no-ops.
  * The soft final stop (`elesys_stop.py`) is gated both ways: `CarController` builds it only on `HONDA_ELESYS`, and only
    with the tuner toggle on. Another Nidec car never gets it, toggle or not.
  * Brake law v2 (`elesys_brake.py`, batch 3) runs only with CarParamsSP flag 32 (set by `_initialize_honda()` on
    `HONDA_ELESYS` with openpilot longitudinal, never in stock ACC mode) and gas law v2; `brake_law_v2_enabled()` checks
    the car, the stock ACC flag and the gas law again. With it off `law_frame()` is never called.
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
  * The gas law (`elesys_gas.py`) is `HONDA_ELESYS`-gated in `gas_interceptor.py`, and `HondaElesysGasLawV2` picks v1 or
    v2 on that car only. Every other car's interceptor command is upstream's line, bit for bit, tuner or not.
  * The `ELESYS_SCM_STANDDOWN` safety parameter.
  * Stock ACC mode (2026-10-04): `HondaElesysStockAcc` acts only on `HONDA_ELESYS` (`_initialize_honda()`), and everything
    downstream keys on what it sets - `HondaFlagsSP.ELESYS_STOCK_ACC` (CarController, CarState, MADS, the banner, the
    settings snapshot, each also checking `brand == honda` where the flag value could be another brand's) and safety
    param 64. Another car, or the toggle off, takes none of those paths.
  * The gateway page stays hidden until a board has identified itself.
* **Car-only DBC fragments.** `_steering_control_e`, `_lkas_hud_4byte`, `_gearbox_legacy`, `_honda_elesys_base`,
  `_nidec_scm_group_a_elesys` and `_sunnypilot_linbus_gw` are imported only by `honda_accord_au_2015_can.dbc`, rather
  than edits to shared fragments.
* **New code lives in new files and on sunnypilot's extension points.**
  * New files: `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_*.py`, `stopping_tune.py`,
    `opendbc/sunnypilot/car/honda/dynamic_tuning.py`, `opendbc/sunnypilot/car/honda/elesys_gas.py`,
    `opendbc/sunnypilot/car/honda/elesys_stop.py`, `opendbc/sunnypilot/car/honda/elesys_brake.py`, and the mici
    `board.py` / `vehicle.py`.
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
* **The software page and `updated.py` (UPD, 2026-10-01)** are not gated on anything: every device running this fork
  writes `UpdaterDownloadProgress` and shows the progress and the highlight. `agnos.py` behaves as upstream's when no
  callback is passed, which is every caller but `updated.py`.
* **The comma 4 HUD (2026-10-03)** is not gated on the car: every comma 4 running this fork draws the speed cluster,
  the compact standstill banner, the pending limit in the confirm prompt and the right rail's planned stop and curve,
  all on by default. Each has a setting
  (sunnylink Visuals → HUD), and with all of them off the screen is the stock one pixel for pixel. The school lamps and the
  electronic sign need NSW Speed Zones on Live, so they appear in NSW only. The compact alerts (2026-10-04) reach every
  comma 4 too, but only for the named normal alerts; nothing critical or asking for the wheel can be one.
* **The speed-limit confirm fixes (SL, 2026-10-04)** reach every car with Speed Limit Assist: the comma 4 text (every
  car with openpilot long on non-PCM cruise; PCM long keeps its own text), and the wrong-direction press that would raise the set
  speed (non-PCM cruise only; a press that lowers it, and a PCM car's buttons, are as upstream's).
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
