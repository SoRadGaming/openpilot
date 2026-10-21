# CHANGELOG — serial steering for HONDA_ELESYS

openpilot-side changes for the 2013–2015 Accord with the EPS-LKAS gateway board,
which translates openpilot's `0x0E4` onto the car's 9600-baud LKAS serial link.
Newest first. Routes are sunnypilot routes on `15646e8515eda1a7`.

Two repositories move together: this one and `SoRadGaming/opendbc` (the
`opendbc_repo` submodule). Both land on **master**. The comma does not install
from this repo: it runs `SoRadGaming/openpilot`, branch `sunnypilot`, which a
GitHub Action there mirrors from this repo's master once master's CI build and
unit tests pass (`docs/fork/README.md`, "How the car gets updates").

The board's own history is in `S:\Software\EPS-LKAS\CHANGELOG.md`. The protocol
is `docs/SP_GATEWAY_FIRMWARE.md`.

---

## UNRELEASED — BLR-v1 brake-lamp board: its telemetry, and its own update from Settings > gateway (branch `blr`, not pushed)

**Not released, not pushed, and no BLR image is bundled.** The branch waits for the board's bench tests and a shadow
drive; the owner decides when it lands. `eps_lkas_appslot.bin` is byte-identical to `nsw-live`'s and the EPS
board's update is unchanged on the wire.

The owner's second board, BLR-v1, lights the brake lamps from openpilot's `0x1FA BRAKE_LIGHTS` bit, which the car
ignores. The owner asked for its firmware to be done "similar to how we did eps, with bootloader etc", for the
gateway page to update the two boards separately, and for some diagnostic data out of it. Firmware: the EPS-LKAS
repo, branch `blr-v1`, `blr/` (checked against `de2f2d1`, after the firmware review's round 1, GATEWAY-UPDATE §13.7).

* **Its diagnostics reach the routes and the comma** (`docs/fork/BRAKE-LAMP-RELAY.md`, opendbc `f0a5284a`): the
  board's `0x620`-`0x62F` decoded into `carStateSP.brakeLampRelay` (diagnostics only, liveness-exempt, `HONDA_ELESYS`
  only), and card keeps its identity and each drive's fault/latch summary in `BlrBoardVersion`, `BlrBoardBuild`,
  `BlrBoardSeenAt` and `BlrLastDrive` for the offroad page.
* **Nothing it sends feeds a brake or controls decision.** No BLR signal reaches `brakePressed`, `brakeLights`,
  selfdrived, controls, MADS or panda safety (board design E.7); tests hold it on what `CarState` does and on the
  source.
* **Updated separately** (`docs/fork/GATEWAY-UPDATE.md` §13): its own button, `lamp board`, beside the EPS's on
  Settings > gateway, shown once the board has spoken, with its own card (`<hash> • shadow|live • <age>`, `board id
  <uid> • boot <git>`, `last drive: ok | <faults> | LATCHED <reason>`); its own params (`BlrFlash*`); its own target
  in the one flasher (IDs `0x628`-`0x62A`, `BLRBOOT`, board class 2 on HELLO and the ENTER ACK, marker `BLR1` - each
  board's image refused on the other by name - shadow or live only, never dirty, DATA paced to 400 frames/s); its own
  screen words (lamp output off - brake lamps pedal-only; no relays, no "turn the car off, then on"). The one link:
  only one update can ever be in flight - the other board's button says `busy` - and pandad runs at most one session
  per window, the lamp board's only if the EPS ran none.
* **"Up to date" is the hash and the variant**: a shadow and a live image of one commit share a hash.
* **The firmware review's round 1**, on the comma: every BLR app image now ends with a 12-byte `BLRT` length/CRC-32
  trailer, and the comma refuses - and never offers - an image without a valid one, as the board's bootloader does at
  `FINISH`; and a HELLO saying the bootloader repaired a flash page that failed ECC (which cancels a staged image) is
  logged and named in the failure it explains. The review also found that the EPS board's bootloader would install a
  BLR image (erasing the EPS app) and only refuse to run it: the comma's refusal by name is the only barrier there.
* **With no image bundled** the button says `no image` and offers nothing; with the board never seen the page is
  pixel-identical to today's.
* **Shared fixes**: the update button no longer sticks at `requested` when a run ends inside one refresh tick
  (GATEWAY-UPDATE §11.2), and the hook no longer lets a failing request clear or transport `close()` escape into
  pandad's loop.
* **The two reviews' fixes** (GATEWAY-UPDATE §13.2, §13.5, §13.7). The EPS update's **error paths** are frozen now as
  its happy path was - 22 of them, NAKs and lost ACKs, refusals, a lost FINISH ACK, odd HELLOs and images, each with
  its trace file - captured on `nsw-live`'s flasher; nothing on the EPS wire changed. On the lamp board, as its own host
  tool does: a FINISH whose ACK is lost is asked again inside the session (it used to report a failure while the board
  installed the image anyway), INFO must say its 112 KB slot, and a bootloader already sitting resident is taken at the
  knock's ENTER ACK instead of after a 10 s timeout. With the firmware tree reachable the comma's update now also runs
  against the **shipping bootloader** compiled for the host, and the DBC and golden-frame copies are held to the
  firmware's own files. A stuck lamp-board `running` holding the EPS button at `busy` until a reboot is accepted and
  written down.

Commits: sunnypilot `3e059dd8d` (the EPS wire transcript frozen first), `12825fedf`, `e1c4c4365`, `1809869ec`,
`62ea8c6cc` and the integration commits; opendbc `f0a5284a`. Before landing: the bench tests, the shadow drive, then
WP-H5 bundles `blr_appslot.bin` and the three skipped bundled-image tests bind.

---

## 2026-10-20 — push10: round 8, KTuner coexistence and the 'now' flash in one update (board `883a3cd1` unchanged)

The owner, 2026-10-20: "Keep it all in main btw I don't want to bother installing and testing. Different branches", and,
offered a push as soon as all three were reviewed and merged with every test passing: "Push when ready (Recommended)".
So push10 is three features in one update to master. Each was built on push9 (`nsw-live` `55d9562b6`) in a branch of
its own, reviewed there twice by independent agents and fixed: round 8 (`round8` `9e0f11e80`), KTuner (`ktuner`
`0c8f364b8`) and sla-flash (`sla-flash` `3931db219`). They are merged into `push10` in that order, round 8 (the largest)
first; opendbc `push10` (`7f34fcde`) is `dc7f1e7d` with opendbc's `round8` (`1c3e53ad`) and `sla-flash` (`f8e1dc09`)
merged. **Not driven yet.**

* **The board image does not change.** `eps_lkas_appslot.bin` is the same blob as push9's: board `883a3cd1`, 49,740
  bytes, SHA-256 `0753b9f6c4e18d1340ce0b5ce40f9ae5344ba8917caa7be557f18635a978de78`, so the gateway page has nothing to
  install. The panda stays `74a0adce` and is not reflashed. The BLR brake-lamp board (branch `blr`) is not part of it.
* **Not edited by any of the three:** the stock-ACC stand-down (`SCM_BUTTONS` `MAIN_ON = 0`) and stock ACC mode,
  CMBS/AEB and panda safety. KTuner is armed in both car modes (pandad's `ELESYS_PARAM_BITS` is bit 32 or bit 64, and
  `diag_tool_guard.armed_for()` does not exclude stock ACC mode), so while a tool talks parked, or after a car-bus
  bus-off, either mode (safety 36 or 68) is held at its start-up `elm327` or left for `noOutput`: by design, the car
  fully stock (`docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 16.2). Replayed below on route 143, which has neither, in both
  modes.

### What is in it

* **Round 8** (its section below). The owner chose, on 2026-10-19, six of the analysis's ten proposals: "Ramp after gas
  lift-off", "Softer stop", "No windup at full brake", "Build it, test hard first", "Learner floor 0.875 -> 1.10" and
  "Steering logging v2".
  * The plan ramps back in, at most 1.5 m/s^3 for 1.5 s, after a gas lift-off - not after an engagement, and not behind
    a lead braking harder than -2.0 m/s^2 or a plan or MPC trajectory under -2.0. The review's closed-loop sim: 0 new
    collisions in 192 hard-braking-lead scenarios (11 as first built); open loop it binds on the same 20 lift-offs of
    13d-143, frame for frame.
  * The soft stop's cap 125 -> 105 counts: decel at the stop a median 0.70 -> 0.58 m/s^2 (closed loop), the snap only
    about 3% smaller (4.78 -> 4.64 m/s^3), one stop in ten marginally harsher (13d t=380.8).
  * The longitudinal PID's floor -4.0 -> -3.2, lowered on a climb by g*sin(pitch) and never below -4.0: full brake on
    every grade (254 counts at 0-12%). On 13d-143 only 143 t=172.04-174.69 changes (265 frames, nothing on the wire).
  * radard: where it fell back to the camera's lead, a stationary track the camera's far (>= 50 m), confident lead
    confirms for 0.3 s stands for it, unless a moving track is near the camera's lead; off in stock ACC mode and
    without openpilot longitudinal. 143: -1.5 m/s^2 0.55 s earlier; 69 routes: no new FCW. **Offered on condition:**
    13e/13f have qlogs only and could not be replayed.
  * torqued's factor floor on this car 0.875 -> 1.10 (nothing resets; it slides up over about 20 min onroad).
  * latsplit v2: torqued's fit by hands x speed, logging only (`lateralTorqueParameters` identical on 7 drives).
* **KTuner** (its section below). After routes 144, 145 and 147: "the last two where I'm parked OP errored ... it cause
  acc and cmbs ... and constantly for a split second airbag light error". The owner chose both fixes: fix 1 as "an
  on-screen note", fix 2 "openpilot off until restart", on a bus-off only.
  * Fix 1 (card): a diagnostic tool's requests on the car bus while parked keep the car stock - at key-on card holds
    its first `controls_update`, so the panda stays in its start-up `elm327`; later `DiagToolStock` makes pandad leave
    the car mode. Back by itself after 10 s of quiet in P (30 s, then 60 s, for a tool back within 2 minutes). 144,
    145 and 147 are stock from the first parked carState; 143 and b5 never; no frame qualified on 189 normal routes.
  * Fix 2 (pandad): a car-bus bus-off puts the car stock in the same poll, until the key is switched off. 144, 145,
    147, db and dc latch, 0.0-0.3 s after the first car-mode poll; 143 and 129 never; of 194 route exports only
    those five.
  * On screen: "Diagnostic Tool Connected / openpilot paused, car is stock"; "Car Bus Error / car is stock, openpilot
    off until restart" with one chime, or "TAKE CONTROL IMMEDIATELY / Car Bus Error: Car Is Stock" if engaged.
* **sla-flash** (its section below). After the push9 drives: "I don't like the speed limit how setting it changes it
  to one different of speed than it swaps once you past 50m if you press early". He chose "Hold it and flash" and
  "Comma screen and dash".
  * A press at 'now' moves nothing; the coming limit flashes on the comma card (0.4 s lit, 0.2 s dim) and in the
    cluster (openpilot's own `0x30C` `CRUISE_SPEED`, 4 frames N / 2 blank, the Elesys radar's own rhythm) until it
    comes into force, and then the set speed changes once, straight to it. An arm lasts 12 s at most.
  * Replays: all 10 'now' arms of 134, 137, 13d, 141, 142 and 143 hold and change the set speed once (141's phantom
    lapses with nothing to undo), the `0x30C` flash in the exact 4/2 pattern; without a press the new code equals the
    logged build on every set speed, SLA state and `0x30C` frame of 12 routes.

### Where the three meet

* **They share no code path.** Round 8 is the planner, controlsd's PID floor, radard, torqued and opendbc's soft stop;
  KTuner is card's guard, pandad and selfdrived's alerts; sla-flash is card's cruise, the speed-limit planner, the HUD,
  `controlsd_ext.py` and opendbc's `0x30C` send. Files two of them touch: `card.py` (KTuner's guard and sla-flash's
  report, separate hunks), `custom.capnp`, opendbc's `carcontroller.py` (round 8's comment on the crawl run, sla-flash's
  `ACC_HUD` send) and `test_elesys.py`, and the docs.
* **cereal ordinals do not collide:** KTuner takes `OnroadEventSP.EventName` `@33`-`@36`; sla-flash
  `LongitudinalPlanSP.SpeedLimit.Assist` `@5`-`@12`, `CarStateSP.speedLimitNow @5` and `CarControlSP.speedLimitNowFlash
  @6`; round 8 adds none. (`blr`'s `CarStateSP.brakeLampRelay` must now take `@6`: docs/fork/README.md.)
* **While the car is stock on purpose (KTuner) nothing of round 8 or sla-flash reaches the car:** the panda is in
  `noOutput` or its start-up `elm327`, so no openpilot frame goes out - `0x30C`'s flash and `0x1FA`'s soft stop
  included. Fix 1 only ever enters parked with nothing engaged, and a 'now' window never offers a key with openpilot
  disengaged; fix 2 at speed is a disengagement, and a disengagement disarms a held press (`armEnd` off).
* The `0x30C` flash (sla-flash) and the soft stop's cap (round 8) are different frames: in the replay below only
  `0x1FA` differs, and only in the stopping state.

### push10: the merge, the gates and the replays

* **Conflicts only in the docs, each side kept:** `CHANGELOG-elesys.md`, where all three had written a section 22
  (round 8 is 22, KTuner 23, sla-flash 24); this changelog (one entry now); the file tables of the CAR doc and the fork
  README. And the `opendbc_repo` gitlink, round8 `1c3e53ad` against sla-flash `f8e1dc09`, resolved to opendbc's own
  merge of both (no conflict there). The code merged without a conflict: after the three merges every code file was the
  branch's own byte for byte, except `card.py` and `custom.capnp`, which are exactly KTuner's with sla-flash's patch
  applied.
* **One test needed the merge:** sla-flash's `test_speed_limit_now_wiring` runs the real `Car.state_update` on a bare
  `Car`, and on push10 `state_update` also runs KTuner's guard (`'Car' object has no attribute 'diag_guard'`, 2
  errors); the rig now gives it the real `DiagToolGuard`, armed for this car (test only). opendbc `7f34fcde` drops a
  `ty: ignore` that sla-flash's new test did not need.
* **Gates:** `scons -j16` clean; the full suite (`tools/test_runner.py -j 12`) 2882 passed, 46 skipped, 1 xfailed
  (the first run, on the merges alone: 2880 passed and the 2 errors above; nothing failed under load, and locationd, the
  livestream session and logmessaged pass alone too, 65 + 4); each branch's own test files on the merge: round 8 115
  passed, KTuner 131 (with `test_native.py`'s 5: `test_car_bus_guard` and `test_pandad_stock` run, not skipped),
  sla-flash 280; opendbc's suite 10,321 tests OK (1,272 skipped); ruff clean on both; ty the same 4 diagnostics on
  `openpilot` as `nsw-live`, in the same files and at the same lines except `card.py`'s, two lines lower (132 -> 134,
  under KTuner's and sla-flash's imports; run with `PYTHONPATH` on each tree), and on opendbc the same 5 as
  `dc7f1e7d`; codespell no new hits (`scripts/lint/lint.sh`'s file set: 34 hits, the same 34 as `nsw-live`; the changed
  docs and opendbc files: none new); the repo's lint (`ruff`, indentation, files over 120 KB - none added, the largest
  added file 75,785 bytes -, shebangs, nomerge) passes; `compile_settings_ui --check` clean; no file mode changed; no
  `params_keys.h` duplicate.
* **[R] KTuner's card replay of route 143** (process replay, 90,314 `sendcan` messages), push10's `card.py` against
  `nsw-live`'s and `sla-flash`'s in the push10 tree: identical in the stand-down (safety 36: 221,271 frames, `sendcan`
  `15965bfe…`, `carState` `0a122d13…`) and in stock ACC mode (68: 99,346 frames, `ceea515c…`, `5e33815c…`) - the same
  digests KTuner recorded against `nsw-live`.
* **[R] Round 8's open-loop `CarInterface` replay of 13d, 141, 142 and 143**, push9 (`nsw-live` with opendbc
  `dc7f1e7d`) against push10 (506,468 control steps, 1,240,852 frames): every frame identical - `0x0E4`, `0x1A6` (the
  stand-down), `0x200`, `0x30C`, `0x500` - except `0x1FA` on 754 steps, all in the stopping state of the 10 stops,
  at most 20 counts lower, with the same `hondastop` reasons; stock ACC mode identical on all four (557,116 frames).
  The same numbers as round 8's own integration replay.
* **[R] sla-flash's 'now' replays** (`scratchpad/slaflash/REPLAYS.md`, closed loop on 12 routes) re-run on push10: every
  per-route result byte-identical to sla-flash's own run after its review fixes; all 10 arms PASS; unexplained
  intervals 0 on every route.

### Round 8: the lift-off ramp, a softer stop, the PID floor, a stopped car at long range, the steering floor, latsplit v2

Branch `round8` (`9e0f11e80`): sunnypilot on `55d9562b6` (push9), opendbc branch `round8` (`1c3e53ad`) on `dc7f1e7d`;
merged into push10. The board is unchanged (`883a3cd1`). From the analysis of drives 13d-143 (17-19 Oct, push9; full
rlogs for 13d, 141, 142 and 143, qlogs only for 13e and 13f). The owner chose, on 2026-10-19, six of its ten proposals: "Ramp after gas
lift-off", "Softer stop", "No windup at full brake", "Build it, test hard first" (the long-range stopped car, offered
only if it passed the clutter set), "Learner floor 0.875 -> 1.10" and "Steering logging v2". Not chosen and not built:
the S-mode engine-brake allowance, the softer launch cap, DEC, and the radar matching changes R1 and R3 on their own
(proposal 3 did not need R1, so R1 is not in it). Unchanged by all of it: the stock-ACC stand-down (`SCM_BUTTONS`
`MAIN_ON = 0`) and stock ACC mode, CMBS/AEB, panda safety, pump rule C1b, the brake learner, the D brake law and the
aero-credit ramp. Design and identifiers: `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 2.4, 5.3, 7.8, 7.12, 9.3 and 10.8.

Labels as in the analysis: [M] measured in the logs, [R] replayed on the logged inputs (open loop unless it says closed
loop), [I] inferred.

#### 1. The plan ramps in after a gas override (planner, `reentry_ramp.py`; proposal 2)

* **What.** For 1.5 s after the gas override ends (the gas pressed within the last 0.5 s; since the round 8 review an
  engagement is upstream's), the published plan may fall at most 1.5 m/s^3, from min(aEgo, 0) on the first frame back
  (starting from the last published plan would not help: it is often already -1.6 while the foot is on the gas). It
  only ever raises the plan. It ends for that re-entry, passing the plan as it is, on the planner's FCW, the MPC's
  crash flag, or an MPC plan under -2.0 - and since the review also on the MPC's trajectory under -2.0 within 2.5 s,
  on any plan under -2.0 (e2e too), or on a lead braking harder than -2.0 (aLeadK). **Added beyond the proposal:** it
  arms only at 3 m/s or faster, the range the 92 measured re-entries covered; and the review's three extra exceptions,
  without which the proposal's three let a hard-braking lead turn near-misses into collisions (below). HONDA_ELESYS
  with openpilot longitudinal only; stock ACC mode and every other car get upstream's plan. One `reentryramp end= t= v=
  a0= plan0= short= frames=` line per re-entry where it bound.
* **[R] Replay, the planner on 13d/141/142/143** (fidelity: 92-97% of frames within 0.01 of the logged plan): the 92
  re-entries of the analysis; the ramp bound on 26.
  * 141 t=207.8 (Sun 15:52:07): the plan stepped -0.18 -> -1.59 in one frame; it now ramps at 1.5 m/s^3 and meets the
    plan 0.9 s later. 141 t=206.4 (15:52:06): the steepest half-second fall -2.4 -> -1.5 m/s^3.
  * The exceptions fired as designed: an MPC plan under -2.0 at 141 t=1784.4; the driver's brake at 142 t=1243.1.
  * Untouched elsewhere (integration check, 8 routes - 13d, 141, 142, 143, 10f, 113, 120, 121 - 187,206 planner
    frames against the same planner with the ramp off, upstream's code path): every frame before each route's first
    re-entry bit-identical (86-2,866 frames per route), `fcw` and `shouldStop` identical on every frame. After a
    re-entry where it bound, the MPC is warm-started from the ramped accel: up to 0.020 m/s^2 in the 2 s after the
    window (142), and beyond that at most 0.012 (13d), 0.006 (141) and 0.002 or less on the other six routes.
  * Cost: the median braking re-entry starts braking no later; the worst 0.18 s later, at most 0.34 m further by the
    end of the window.
* **[R] Felt jerk, closed loop through the real controller and the batch-3 car model** (m/s^3):

  | re-entry | logged | sim, today | sim, with the ramp |
  |---|---|---|---|
  | 141 16:18:22 | -3.86 | -3.10 | -2.44 |
  | 141 15:52:07 | -3.62 | -3.06 | -2.42 |
  | 141 15:52:06 | -1.87 | -1.94 | -1.04 |
  | 142 17:58:04 | -0.22 | -0.05 | -0.05 |

  **Short of the hoped-for -1.5 to -2.0 on the two worst.** What is left is the PID's integrator winding against the
  brake's lag on top of the ramp. Tried for information only, not built: a 1.0 m/s^3 ramp gives about -1.7, and also
  holding the integrator during the ramp about -2.0. 142 17:58:04 was an engagement, not a lift-off: the -3.63 the
  analysis gave it was the driver's own braking at t=1243.1 (and since the review the ramp leaves engagements alone).
* **Since the round 8 review** (the numbers above still hold: open loop on the logged radarState, the fixed ramp binds
  on the same 20 lift-offs of 13d-143 frame for frame, the three worst included, and no longer on 5 engagements -
  13d t=115.2 and 370.9, 141 t=226.9 and 668.5, 142 t=1242.5): [R] closed loop, the review's sim rerun (the real
  planner, LongControl, the tuner-on CarController and the batch-3 plant; the driver on the gas until the lift-off):
  behind a lead braking at -2 to -6 m/s^2 from 0.5 s before to 0.7 s after the lift-off (192 scenarios, 12-27 m/s,
  1.0-2.0 s gaps) 0 new collisions (11 as first built, at up to 3.3 m/s), the closest approach at worst 0.44 m shorter
  (6.10); 20 stopped-lead scenarios at most 0.29 m; 60 lift-offs closing on a slower lead at constant speed no
  collision either way, the gap they settle to up to 1.6 m shorter of 10-28 m. [R] Open loop on 55 routes (13d-143 and
  the review's 51): it binds on 496 lift-offs (542 re-entries as first built), 7 of them with leadOne's aLeadK under -1.5
  (10); the trajectory floor took out 129 t=744.5 and 751.7 (28 m/s, 26 m behind a slower car, up to 1.36 m/s^2 held
  back as first built).
* 13e/13f (qlogs, the plan at 2 Hz) cannot be replayed; on the logged plan the ramp would bind on 2 and 17 re-entries,
  by at most 0.28 and 0.43 m/s^2.

#### 2. Softer stop: the soft-stop cap 125 -> 105 counts (opendbc `elesys_stop.py`; proposal 5)

* **The pump is unchanged.** 105 stays above C1b's and v5's crawl threshold (100): min(c, 105) > 100 exactly when
  c > 100, so the crawl run pumps the same frames (a test walks every command 0-255), and the rise to the 189 hold is
  still more than `ELESYS_PUMP_BIG_RISE` over the cap. [R] In the sim every crawl frame above 100 counts pumped with
  either cap, and the rise to the hold still pumps.
* **Full brake is unchanged**: the cap acts only in the stopping state (143's full-brake window: 0 brake frames differ).
* **[M/I] Decel at the stop** (the 10 logged stops): while rolling at the cap it was a median 0.82 m/s^2 on the IMU;
  at 105 about 0.58, and entry to wheel-stop 0.80 -> 0.99 s (median). [R] Closed loop: 0.08-0.22 m/s^2 lower decel at
  the stop on the five stops where only the cap changes (median 0.70 -> 0.58); three unchanged.
* **[R] The snap is only about 3% smaller, not the ~25% the analysis expected** (corrected in the round 8 review): in
  the closed loop the jerk after wheel-stop is a median 4.78 -> 4.64 m/s^3.
* **The cost: one stop in ten becomes marginally harsher.** 13d t=380.8 already rolled 1.74 s at 125 with only
  0.35 m/s^2 at the cap; at 105 it has too little decel, its escape changes from `settle` to `weak`, and the old brake
  ramp it is handed to gives 4.54 against 4.49 m/s^3. (141 t=234.7, listed with it as first written, escapes by
  `max_roll` at either cap and comes out softer: 4.56 -> 4.35, decel 0.49 -> 0.31.) Lengthening the 1.9 s escape timer
  (`SOFT_STOP_MAX_ROLL`) would be the fix; it is the owner's call and not built. Road check: the `hondastop rise=`
  reason on every stop (`weak`, or `max_roll` while rolling, is that case), decel at the IMU-confirmed stop (target
  median <= 0.6), the gap at the stop (>= 3.5 m).
* The bound: the rise from 105 to 189 takes about 0.34 s, the ceiling is gone 0.60 s after the rise starts, nothing of
  it is left 2.50 s after entry (2.42 at 125).

#### 3. No windup at full brake: the PID floor -4.0 -> -3.2 (opendbc `interface.py`; proposal 9)

* `get_pid_accel_limits()` returns -3.2 on HONDA_ELESYS outside stock ACC mode; `NIDEC_ACCEL_MIN` (-4.0) everywhere
  else. On the level -3.2 is still full brake (that starts at -2.6 net).
* **On a climb, since the round 8 review.** The tuner's pitch feedforward is added after the PID and takes braking back
  on a climb, so the flat -3.2 cut full brake from about 6%: held at the floor, the real CarController gave 234 / 224 /
  205 / 186 of 254 counts at 7 / 8 / 10 / 12% (17 m/s). controlsd now lowers the floor by the climb's g*sin(pitch),
  from the pose it hands the tuner, never below -4.0 and -4.0 without a pose: full brake as with -4.0 on every grade
  (254 at 0-12%, a test). Climbs over 6% were 0.62% of engaged time above 5 m/s on 14 routes.
* [R] The real controller on every engaged frame of 13d, 141, 142 and 143 (median error against the logged output
  0.0005 m/s^2): one stretch changes, 143 t=172.04-174.69 (265 frames; a slight downhill), where the integrator stops at
  -0.81 instead of -1.33. The brake was at full on every one of those frames either way, and the driver braked at
  174.68, so nothing changed on the wire. Not one frame changes on 13d, 141 or 142. The grade-aware floor from the logged
  pitch changes the same 265 frames and none on 13d, 141, 142, 10f, 129, 12a or 137, and on no frame of the eight brakes
  less than -4.0 with the climb's term added back.

#### 4. A stopped car at long range the camera reads as moving (radard; proposal 3) - **included, on condition: 13e/13f could not be replayed**

* **The rule** (`elesys_radar_guard.long_range_track()`, HONDA_ELESYS, leadOne only): only on a frame where radard has
  fallen back to the camera's lead, a stationary track (|vLead| < 1.5 m/s) within 20% of the camera's range and 1 m of
  its lateral position, with the camera's filtered prob >= 0.9, its range >= 50 m and the car above 5 m/s, once the same
  track has held 6 frames (0.3 s), stands for the lead; a frame where radard matched that track itself continues the
  run. It bypasses upstream's `vel_sane` and the guard's tolerance for that case only. R1 (sane-first matching) is not
  needed and is not bundled. **Since the round 8 review:** a frame with a moving track (raw vLead above 3 m/s) within
  30% of the camera's range and 1.5 m of its side takes nothing and breaks the run, and the rule runs only with
  openpilot longitudinal and stock ACC mode off (the ramp's gate).
* **[R] 143** (RadarInterface, radard and the planner, open loop): it fires at t=169.13 (Mon 00:46:04), on track 272 at
  82.6 m.

  | plan reaches | push9 | round 8 | earlier by |
  |---|---|---|---|
  | -1.0 m/s^2 | t=170.13 | t=169.73 | 0.40 s |
  | -1.5 m/s^2 | t=170.68 | t=170.13 | 0.55 s |
  | -1.8 m/s^2 | t=170.98 | t=170.88 | 0.10 s |
  | -2.0 m/s^2 | t=171.18 | t=171.28 | -0.10 s |

  At most 0.54 m/s^2 firmer. **Short of the ~0.7 s expected**: that figure assumed the radar's speed was trusted from
  t=168.3, and this rule cannot start until the camera's prob reaches 0.9 at t=168.88. **And -2.0 comes 0.10 s later**
  (left out as first written): the camera's range jitters, the run breaks twice (t=170.13, 170.58) and radard falls back
  to the camera's moving lead for 3 frames each time, the plan holding -1.55 and -1.70 to -1.74 there; from 171.2 it is
  at most 0.06 m/s^2 softer than push9, because the planner's own speed integrates its earlier, firmer plan while the
  replayed car keeps push9's speed (open loop). Holding the run through those breaks would widen the rule and is not
  built. [I] When the driver braked, the
  car would have been 0.4 m/s slower with 1.8 m more room; without him it would still have reached the stopped car, at
  about 2.7 m/s instead of about 4.5.
* **[R] The 14-route clutter set** (01, 06, 07, 08, 10, 14, c0, c8, c9, 10f, 113, 115, 120, 121; about 210 engaged
  min): **zero new engaged phantom brakes, no FCW**; plan dips per minute identical to push9 on every route. radard's
  lead changed on one route only: c0 t=254.5-256.0, engaged, 5 frames on the real stopped queue the car then stopped
  5 m behind, at most 0.10 m/s^2 firmer.
* **[R] The new drives, every firing:** 143 t=169.13-170.08, engaged, the stopped car; 141 t=1557.4, 6 frames, not
  engaged (the driver braking; a car 86-88 m ahead pulling away from a stop at 0.7-1.4 m/s, which push9 also took on
  some frames); 142 t=959.5 and 959.8, 3 frames, not engaged (a car stopped 72-77 m ahead that push9 took on alternate
  frames; it then drove off and was followed); 13d: none. **13e/13f have qlogs only, which carry no radar CAN, so
  they were not replayed** - only screened: a coarse check found 10 s and 1.8 s of engaged time where it could even
  have applied, the same range as the full-log drives (1.7-12.7 s), where it fired engaged only behind real stopped
  cars. The owner's condition named them, so the rule is offered on condition that their rlogs replay clean.
* **[R] Since the round 8 review: 69 routes** (the 14, the four, and the review's 51 - 1.42 M frames, 498 engaged min):
  with the veto radard's lead is identical, frame for frame, to the rule as first built on every route; the veto ends
  the only two engaged runs of 3 frames or more on a return the car then drove through: 0e1 t=887.18 (16 m/s, the
  car being followed 101 m ahead at 18 m/s against the camera's 79 m, a return at 74 m) and 08 t=946.0 (15 m/s, a
  return at 80 m, the moving car 26% beyond the camera's 73 m). At 6 frames each would have been a stopped car 74-80 m
  ahead: full brake. The rule fired engaged on one review route: d4 t=89.89-90.99, 22 frames, the plan at most -1.51
  for -0.98, on a vehicle crawling at 0.4-0.7 m/s 80 m ahead that then pulled away. No new FCW on any route.
* **[R] Without the fallback condition** it swapped a car it was following for a stationary return 8 times while
  engaged at 42-110 km/h; six planned -1.8 to -3.5 m/s^2 where push9 planned -1.2 to 0.0 (08 t=945.5, c8 t=615.0 and
  t=1157.8, c9 t=1104.6, 10f t=2068.2, 142 t=998.5), and c8 at 110 km/h raised an FCW. The analysis's "5-50 times per
  route" came from a prototype that also skipped the lateral check.
* Tests: 6 synthetic (each limit just inside and outside, the 0.3 s run, the fallback condition, leadTwo and other
  cars unchanged), 2 on logged frames (143's stopped car taken; c8/c9 following a moving car with a stationary return
  near it, unchanged), c0 updated for its 5 filled-in frames; a second fixture, 25.6 KB. Broken deliberately 12 ways:
  removing the fallback condition (in the guard or in radard) fails 4 tests each time, every limit change and the
  wrong lead fail too; the one edit that passed (dropping `fell_back` from the return line) changes nothing, because
  on those frames it returns the track radard already chose.

#### 5. The steering learner's floor 0.875 -> 1.10 (torqued, `torque_floor.py`; proposal 6)

* The window is 1.10-1.625 live (1.10-1.875 for the qlog estimator), the 1.25 prior unchanged. HONDA_ELESYS with
  torque tuning only; it only raises upstream's floor and never above the prior; under sunnypilot's relaxed toggle the
  0-2.5 window stands; if the module fails to import torqued keeps upstream's window.
* **Confirmed in the code: nothing resets.** The restore key is (fingerprint, tuning, friction prior, factor prior,
  `VERSION`), without the bounds, so the cache is restored as it is; a low raw estimate is clipped (`np.clip`), never
  reset (`reset()` runs only on a NaN estimate); the restored filtered value is not clipped, so it slides up at decay
  250, about 21 min of onroad time. Installed on the car's cached 0.909: about 1.00 after 13 min onroad, 1.05 after
  28, 1.09 after 61.
* **[R] The car's own raw estimates re-filtered through the new clip** (the method reproduces the logged filtered
  factor to 5e-5), installed before 13d: 13d 0.883 -> 0.997, 13e -> 1.075, 13f -> 1.095, 141 -> 1.099, 142 and 143
  1.100 (logged: 0.879, 0.876, 0.875, 0.879, 0.928, 0.909). The raw estimate was below 1.10 on every sample of all
  seven drives, so in town the factor sits on the floor; only highway driving lifts it. Through the real torqued
  (chained from 115, the cache carried between drives): 13d 0.8826 -> 0.9966, 1.0985 at the end of 143. Open loop: a
  higher factor commands less torque, which would move later points.

#### 6. latsplit v2, logging only (`lat_speed_split.py`; proposal 7)

* Points split by hands - the largest |`steeringTorque`| over torqued's own override window: off (< 300), on
  (300-1199), hard (>= 1200) - and speed (54-70, 70-90, 90+ km/h): 9 cells, each with the TLS moments, steer bins and
  the controller's integrator and roll term (only while both are fresh), all combining exactly across drives. Lines
  are tagged `commit=` `board=` `win=` and pooled by the report only within one build; v1 lines still read. The
  controller inputs come from a second, non-blocking SubMaster only this car creates; torqued's own is unchanged.
* **Proof it applies nothing:** `lateralTorqueParameters` identical with and without the shadow on every publish of 7
  replayed drives.
* [R] Pooled over 13d, 141, 142 and 143: hands-off share 11.5% / 5.1% / 6.2% (54-70 / 70-90 / 90+); factor hands-off
  against hands-on 1.261 / 0.741 at 54-70 and 1.236 / 1.052 at 70-90 (90+: 1.352 hands-on, 88 points hands-off); no
  point in `hard`. The hands-off share is above the analysis's 3-9% because torqued sees `carState` at 20 Hz and misses
  short touches.

#### Integration

* **[R] The real `CarInterface`, open loop on the logged CAN and carControl** of 13d, 141, 142 and 143 (506,468
  control steps, 1,240,852 frames), opendbc `round8` against `dc7f1e7d`: every frame identical - `0x0E4`, `0x1A6`
  (the stand-down), `0x200`, `0x30C`, `0x500` - except `0x1FA` on 754 steps, all inside the stopping state of the 10
  stops (13d 1, 141 2, 142 7), at most 20 counts lower (the cap), with the same `hondastop` reasons. **Stock ACC mode**
  (`HondaElesysStockAcc` through the real `_initialize_honda()`): every frame of all four routes identical (557,116
  frames; only `0x0E4` and `0x500` are sent). The planner's ramp is not built in stock ACC mode, and the PID floor is
  upstream's there. radard's long-range rule followed the clutter guard (HONDA_ELESYS in either mode) as first built;
  in stock ACC mode radarState reaches the CAN only through `0x500`, but the planner's FCW also reaches selfdrived's
  alert whenever openpilot is enabled, so **since the round 8 review the rule is off in stock ACC mode and without
  openpilot longitudinal** (the ramp's gate; a test replays 143 in stock ACC mode and gets the guard without the rule).
  The clutter guard itself stays on in stock ACC mode, as before round 8.
* Gates: `scons -j16`; the full sunnypilot suite (`tools/test_runner.py`, with `PYTHONPATH` on this tree) 2671 passed,
  45 skipped, 1 xfailed; opendbc's suite 10,310 tests OK (1,272 skipped); ruff clean on both; ty the same 4
  diagnostics as `nsw-live` on `openpilot` (and opendbc's own 5 as `dc7f1e7d`), at the same places; codespell no new
  hits; `compile_settings_ui --check` clean (no settings changed); the touched tests pass at every one of the round's
  commits.
* **Tests in a second worktree must run with `PYTHONPATH=$HOME/<tree>:$HOME/<tree>/opendbc_repo`**: the venv's
  editable installs map `openpilot` and `opendbc` to `~/sp-merge`, and without it `tools/test_runner.py` silently
  tests `~/sp-merge`'s code.

#### Round 8 review (2026-10-20)

Two independent safety reviews of `nsw-live..round8`; each finding was re-checked before anything changed, and every
fix has tests that fail on round 8 as first built (checked by loading the committed modules behind shims that only add
the new names). Replays and sims are the fix agent's own, with the reviewers' harnesses where they existed; the first
build's numbers were reproduced exactly before the fixes were measured.

* **The lift-off ramp delayed braking behind a hard-braking lead (review A, high) - confirmed, fixed.** Reproduced:
  11 of 192 sim near-misses became collisions. Fixed in section 1 (the lead, plan and trajectory exceptions): 0 new
  collisions. Comfort unchanged on 13d-143: with the planner in the loop the published plan differs from the first
  build only at the 5 engagements (where it is now upstream's) and in their MPC warm-start tails (at most 0.020 m/s^2);
  `fcw` and `shouldStop` identical on every frame.
* **The ramp armed on every engagement (review B) - confirmed, fixed:** only after the gas (0.5 s).
* **The PID floor cut full brake on climbs over ~6% (both) - confirmed, fixed:** grade-aware from controlsd's pitch,
  never below -4.0 (section 3).
* **"Fell back" did not mean "no radar car" (review A) - confirmed, fixed:** the moving-car veto (section 4); it ends
  the 0e1 near miss and a second one found on 08, and changes radard's lead on no frame of 69 routes against the rule
  as first built. 0e1 is a must-not-fire fixture.
* **The long-range rule ran in stock ACC mode (review A, low) - confirmed, fixed:** the ramp's gate.
* **Proposal 3's acceptance condition was only partly met (review B) - confirmed, not fixable here:** 13e/13f still have
  0 rlogs on disk (29 and 33 qlogs, checked 2026-10-20), and qlogs carry no radar CAN. The rule stays on the branch,
  documented as offered on condition that their rlogs replay clean; it is one commit plus the radard half of the review
  fix if the owner prefers it out until then.
* **The soft stop's docs overstated it (review B) - confirmed, docs fixed:** the snap is ~3% smaller (4.78 -> 4.64
  m/s^3), not ~25%; one stop in ten (13d t=380.8, settle -> weak) is marginally harsher, not two (141 t=234.7 comes out
  softer). Behavior unchanged.
* **The rule lets go of 143's car twice, and -2.0 comes 0.10 s later (review B) - confirmed, documented:** both
  reproduced exactly (section 4). Not changed: holding the run through the breaks would widen the rule, and the -2.0
  shift is an open-loop artifact of at most 0.06 m/s^2.
* **A doc splice in the CAR doc 5.3 "Test." paragraph (review B) - confirmed, fixed.**
* Review A's soft-stop residuals (info), the torqued floor and latsplit v2: no finding, nothing changed.
* Gates after the review fixes: `scons -j16` clean; the full sunnypilot suite (`tools/test_runner.py -j 12`, with
  `PYTHONPATH` on this tree) 2683 passed, 46 skipped, 1 xfailed (2671 / 45 / 1 as first built: the 12 new tests, and
  one more environment skip outside the touched tests; a second, verbose run had `test_logmessaged.test_big_log` fail
  once on log-file timing, and it passes alone); opendbc's suite 10,313 tests OK (1,272 skipped); ruff clean on both;
  ty the same 4 diagnostics on `openpilot` and 5 on opendbc as round 8 as first built, at the same places; codespell no
  new hits; `compile_settings_ui --check` clean. 18 deliberate breaks of the review fixes (8 in the ramp, 5 in the veto
  and its gate, 5 in the floor) each fail at least one test.

### KTuner and the comma together: stock while a diagnostic tool talks, stock after a car-bus bus-off

Branch `ktuner` (`0c8f364b8`, from `nsw-live` `55d9562b6`); merged into push10. KTuner changes **neither** opendbc nor
the panda: the panda stays `74a0adce` and is not reflashed by this update.

**What happened.** Routes 144, 145 and 147 (2026-10-19), parked, ignition on, KTuner datalogging the engine ECU over
the OBD port: "the last two where I'm parked OP errored ... it cause acc and cmbs ... and constantly for a split second
airbag light error". For the first 6.5-9 s of each start the panda sat in its start-up `elm327` (harness relay closed,
the comma sending nothing) and KTuner and the car shared the bus with **zero** errors. The moment it switched to
`hondaNidec` (relay open at the radar, the comma transmitting) its car-bus transmissions failed (`bit1Error`), TEC 113
at +0.19 s, bus-off at +0.29 s on 144 (on 145 the core went silent at +3.6 s), and its car-bus core stayed dead for the rest of the route:
"CAN Bus Disconnected", and the radar - which in that mode reaches the car only through the panda - cut off, hence the
ACC/CMBS lamps (the SRS flicker is inferred: the error frames of the recovery attempts). Two earlier parked sessions on
2026-09-16 are the same failure: `dc` (a scan tool) and `db`, which had no tool traffic on the bus at all. Why the
comma's frames fail with KTuner on the bus (KTuner rejecting them, stub reflections, the sample point) is not settled.

**The owner chose both fixes.** Full design and data: `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` section 16.

* **Fix 1 - a diagnostic tool talking on the car bus while parked: stay stock.** card
  (`sunnypilot/selfdrive/car/diag_tool_guard.py`) watches for tester **requests** received from another node on the car
  bus (src 0 exactly: `0x18DA/0x18DB` with a tester source address, or `0x7DF`/`0x7E0-0x7E7`; never a response, never
  our own query's echo or its copy through the relay, never the board's `0x700-0x71F` or BLR's `0x620-0x62F`). Two
  within a second, parked (CAN valid, P, standstill) with nothing engaged, and the car stays stock:
  * at key-on card holds its **first** `controls_update` (no `CI.init`, no `ControlsReady`), so the panda simply stays
    in the start-up `elm327` - exactly the state KTuner coexisted with on every route;
  * later in a drive card writes `DiagToolStock` and pandad leaves the car mode for `noOutput` (relay closed);
  * it resumes **by itself** 10 s after the last request - counted only while the car bus is heard, so a dead bus is
    never "quiet" - and only in P at a standstill. The release is the normal start (`0xdf`, `0xdc`, the same 36 or 68).
    A tool that comes back within 2 minutes of a release (one that polls slowly) needs 30 s of quiet the next time,
    then 60 s, so it cannot switch the relay every few seconds.
* **Fix 2 - the car bus went bus-off: stock at once, until the key is turned off.** pandad
  (`selfdrive/pandad/car_bus_guard.h`), in the poll that sees it, on the car-side core only (2 with a flipped
  harness), while the panda is in this car's mode (hondaNidec, param bit 32 or 64): `busOffCnt` rose since the last
  poll, or `busOff` - bus-off only, as chosen. It forces `noOutput` in that same poll - relay closed, so the radar is
  back on the car's wire, and `can_init_all()` restarts the stuck core - writes `CarBusOffStock` (non-blocking), and
  stays out of the car mode until the key is switched off (the ignition off AND pandad offroad; a pandad restart
  re-reads the param, so it stays out too). db shows why it latches: the storm comes back with no tool traffic at all.
  It does not latch on a poll in which fix 1 is already taking the car out of its mode.
* **"Stock" is always a mode change, never the stand-down.** The ELESYS `SCM_BUTTONS` stand-down (MAIN_ON=0) and stock
  ACC mode (param 68) are not edited at all; while the relay is closed they are simply not what is on the wire.

**What you'll see** (selfdrived, `sunnypilot/selfdrive/selfdrived/car_stock_alert.py`; events `diagnosticToolStock
@33`, `carBusOffStock @34`, `carBusOffAnnounce @35`, `carBusOffDisengage @36`):

| | screen | sound |
|---|---|---|
| KTuner at key-on | "Diagnostic Tool Connected / openpilot paused, car is stock" | none |
| an engage press meanwhile (in D) | "Diagnostic Tool Connected / openpilot unavailable" (in P the car's own "Gear not D" may show instead - also a refusal) | refuse |
| the tool quiet for 10 s in P | the note goes; openpilot available as after any start | none |
| bus-off, parked (or stopped with MADS paused) | "Car Bus Error / car is stock, openpilot off until restart" | one chime |
| bus-off while engaged, or while moving with MADS paused (on the brake, or the board's override pause) | "TAKE CONTROL IMMEDIATELY / Car Bus Error: Car Is Stock", then the banner | one warning |

"CAN Bus Disconnected: Likely Faulty Cable" no longer appears after a bus-off. Upstream's `controlsMismatch`, which a
stock panda would otherwise raise (and which knocked a paused MADS to disabled with a critical alarm), is replaced by
the refusal `carNotReady` only while nothing is engaged, every panda is in `noOutput`/`elm327`, and there is a reason
(or a sub-second edge); any other mismatch is upstream's, unchanged. FCW and stock AEB still take the screen.

**After a bus-off at speed the board keeps steering manual until key-off** if it had already taken over (it has no
hand-back, board `HANDOFF.md` decision 2); ACC, CMBS and AEB are the car's own again. With KTuner at key-on the board
never takes over, so the car is fully stock, LKAS included.

**Evidence** (replays on the owner's routes; fixtures under 120 KB each):

* Fix 1 on every frame of the full-rate rlogs: **143** (normal start, whole 15-min drive) and **b5** (openpilot's own FW
  query): never stock. **144** (whole route), **145**, **147**: stock from the first parked carState (3.47 s, 3.18 s,
  2.57 s), 5.5-5.9 s before the logged first `controls_update`, held to the end. **dc**: stock 0.33 s before the
  logged first `controls_update`. **db**: never (it is fix 2's). On 189 normal routes (51.5 h) no frame ever qualified.
* Fix 2, the shipping C++ header driven over every 10 Hz pandaState: **143** and **129** (3551 s) never latch; **144,
  145, 147, db, dc** latch on the first poll the rule holds, 0.0-0.3 s after the first car-mode poll, and stay latched
  through the route's last (key-off) poll. Over all 194 route exports (131 with car-mode polls, 38 h) only those five
  latch.
* **Byte-identical where nothing triggers:** card replayed over all of route 143 (process replay, 90,314 `sendcan`
  messages) gives the same `sendcan` bytes and `carState` as `nsw-live`, in the stand-down (safety 36) **and** in
  stock ACC mode (68). `controls_update`, `setSafetyMode()` and `fetchCarParams()` are hash-pinned to `nsw-live`.

**Review fixes (2026-10-20, two independent reviews).**

* **Key-off after a bus-off latch put the car mode back for one poll** (both reviews; reproduced against the real
  `pandad.cc`). On the first ignition-off poll pandad is still onroad (hardwared learns the ignition from that very
  poll), the latch cleared there, and `configureSafetyMode()` sent `hondaNidec` 36 for up to 100 ms: two relay clicks,
  card's frames on the bus, a false "leaving stock" in the log, at every key-off after a latch. Now the latch clears
  only when the ignition is off **and** pandad is offroad (a one-poll ignition dropout keeps it, and pandad puts
  `CarBusOffStock` back if the manager's ignition-on edge cleared it meanwhile), and `configureSafetyMode()` never
  sets a car mode on a poll that read the ignition off - which also covers fix 1 releasing on the key-off poll and
  upstream's own key-off-while-fingerprinting edge.
* **Fix 2 no longer latches in the poll fix 1 is already acting in** (a tool plugged in mid-session while parked):
  fix 1 owns that episode, and fix 2 judges again once fix 1 has put the car mode back.
* **Bus-off only** (the owner's words): the TEC >= 128 (error-passive) term is gone. It is recoverable, still
  forwards, and caught dc and 147 only 0.1 s sooner; it would have switched the relay under a live stock ACC in stock
  ACC mode on a recoverable error.
* **Fix 1 back-off:** a tool polling every 11 s switched the relay 21 times in two minutes (1 s of car mode in every
  11, each into the tool's bus); now 3. The quiet needed grows 10 s, 30 s, 60 s for a tool back within 2 minutes of a
  release, and starts at 10 s again after that.
* **A MADS paused while moving** (on the brake, or the board's override pause) now counts as engaged for a bus-off:
  upstream's critical disengagement, with our text, instead of one chime and a refusal when the brake comes off. A
  diagnostic tool's note is unchanged (fix 1 only ever enters parked).
* Tests that would have missed these: pandad's stock sequence is now RUN, not only read - the new native
  `selfdrive/pandad/tests/test_pandad_stock` links the real `pandad.cc` and `panda_safety.cc` against a fake panda and
  drives `process_panda_safety()`, pandad_run's exact 10 Hz pass (now one function), through eleven scenarios; both
  native tests are in `test_native.py`. The fix-1 replay fixtures now keep every src-0 frame in 0x600-0x7FF and every
  29-bit frame (two per id per second), chosen without the request rule, so a detector that counted the board's ids
  or the ECUs' answers fails on route 143. A header that does not compile now fails `test_car_bus_guard.py` instead of
  skipping it. 18 mutations of the fixes, all caught.

Tests: 126 new (`test_diag_tool_guard.py` 38, `test_card_diag_hook.py` 10, `test_diag_tool_guard_replay.py` 8,
`test_car_bus_guard.py` 16, `test_stock_wiring.py` 17, `test_car_stock_alert.py` 37) plus the native
`selfdrive/pandad/tests/test_car_bus_guard` and `test_pandad_stock`. Not driven yet: the checks for the first drives
are in section 16.10 of the car document.

### sla-flash: a press at 'now' holds, flashes, and sets the limit once

Branch `sla-flash` (`3931db219`; opendbc `sla-flash` `f8e1dc09`), on `nsw-live` `55d9562b6`; merged into push10.

The owner after the push9 drives (2026-10-19): "I don't like the speed limit how setting it changes it to one different
of speed than it swaps once you past 50m if you press early". push9's press at 'now' was also its ordinary 1 km/h
step, so the set speed changed twice - the step at the press, the limit at the sign: 7 of the 10 push9 'now' windows
(13d-143), and in the 2 phantoms he undid the step by hand (13f 326.35, 141 236.62). He chose "Hold it and flash" and
"Comma screen and dash":

* **The press moves nothing.** card takes it - its first action (a short press's release, a long press's first 0.5 s
  step) on the key the planner offers at 'now' (`longitudinalPlanSP.speedLimit.assist.nowKey`, with every Speed Limit
  Assist guard push9's press had) - and reports it (`carStateSP.speedLimitNow`); the planner arms exactly what card
  reports (never a limit already in force). The rest of that press and the same key again while armed are swallowed
  too. When the limit comes into force the set speed changes once, straight to it. The other key is its ordinary step
  and disarms; a walk begun before 'now' is left alone; a phantom lapses 2.5 s after 'now' with nothing to undo.
* **The comma card flashes the coming limit's number** while the press is held - lit 0.4 s, dim (25 %) 0.2 s - with
  'sets 100 at sign' and no key; everything else on the card is solid, both styles; drawn from the planner's arm even
  after the look-ahead's hold has ended; it cross-fades into the stock MAX number at the take. The 'press - to set
  100' words now come from the planner's offer, so they show exactly when card would take the press.
* **The cluster flashes it too:** openpilot's own ACC_HUD `CRUISE_SPEED` = N for 4 frames, blank for 2 (the Elesys
  radar's own flash for a speed it is building up to), solid at the take (`CarControlSP.speedLimitNowFlash`; opendbc
  `CHANGELOG-elesys.md` 24). Only byte 3 and its checksum change; panda, carstate, the PCM, the board, the stand-down,
  stock ACC mode and CMBS/AEB are untouched.
* In 'now' the card's key no longer steps the set speed - a long press included, taken whole at its first 0.5 s step.
  A press holds until the limit is in force (0.4-4.1 s in the replays) or, for a phantom, 3 s past the sign plus
  2.5 s: about 8.5 s at 60 km/h, 11.5 s at 30 km/h, and never more than 12 s from the press (the cap, below). To slow
  more before the sign: brake, the other key, or wait. Logged for the next drive: `assist.armSeq` / `armEnd` (taken,
  lapsed, otherKey, walkedPast, off, cardOff, otherLimit, selfRaised, capped), `carControlSP.speedLimitNowFlash`,
  `sendcan 0x30C` byte 3 (`scratchpad/slaflash/nowlog.py` lists them per route).

Replays (card + selfdrived + plannerd closed loop on the logged inputs, NEW against the logged build; the old copy
reproduces the logged set speed on 13d/142/143 frame for frame, on 141 but 8 frames, on 134/137 but 6 - prompt
confirms a planner frame early or late, none at a 'now' press; `scratchpad/slaflash/REPLAYS.md`):

| route | press | NEW (sla-flash) | logged build | |
|---|---|---|---|---|
| 134 (push8) | 313.56 + | 50 held, 315.51 50->80 | 50->51, prompt, 80 | one change |
| 134 | 446.47 - | held, 447.54 ->60 | 79->78, prompt, 60 | one change |
| 134 | 716.34 + 717.66 + | held, 719.53 ->80 | 60->61->62, prompt, 80 | one change |
| 137 (push8) | 305.28 + | 61 held, 305.69 61->80 | 61->62, 305.73 80 | one change |
| 13d | 211.34 + 214.22 + | 60 held, 214.76 60->80 | 60->61->62->80 | one change |
| 13d | 601.91 - | 81 held, 602.83 81->80 | 81->80 at the press | one change, 0.9 s later |
| 141 | 231.90 -, phantom | 80 held, lapsed 235.90 | 80->79, then his walk | nothing to undo |
| 142 | 304.04 + / 479.00 - | held, 305.87 50->80 / 480.84 79->60 | a step, then the limit | one change each |
| 143 | 496.71 + | 60 held, 500.79 60->80 | 60->61->80 | one change |
| 13e, 13f (qlog) | 4 presses | listed: one change each; 13f's 326.35 - (his undo) is the other key | | listed |

In every arm the predicted 0x30C flashes from the first send after the press (about 0.1 s) to the take in the exact
4 / 2 pattern. Without a 'now' press NEW equals the logged build on every set speed, SLA state and 0x30C frame of all
12 routes (unexplained differences: 0).

Review fixes (two reviews; nothing on the wire changed):

* **The planner arms a report only on the offer it published last** - that limit on that key, an arm's own key only
  while that arm stands, never a limit in force. card's report is a frame or two behind the press, and three late
  reports armed what they should not: after a hand-over to a different limit (the cluster flashing 50 for 2.6 s on the
  prompt for a 70, or an arm for 50 under a 40 the press had confirmed), after a phantom's lapse (2.5 s more of
  flashing), and after a plannerd restart (card's old tally). Each is now a press spent with nothing moved.
* **An arm lasts at most 12 s from its press** (`ARM_MAX_S`, `armEnd` capped): at a crawl 'now' can open 15 s of
  travel before the sign, and the cluster flash would run past anything stock shows (8.6 s at most in 13 stock drives).
  'now' still open, the key is offered again; at the sign the prompt asks as before.
* **Tests that run the real hops:** card's `state_update` writing the report, plannerd's loop reading it, the planner
  publishing the arm (never the offer) as `armedSpeed`, controlsd flashing the arm only and only while the plan
  arrives, disengaging disarming, and the cluster going solid at a take that lands in the flash's dark phase. A
  mutation of each of those passed every test before.
* Known: on the stock-plus card the held set speed shows on neither display while armed (the card takes the MAX
  number's place and the cluster shows the target); the split card's MAX half shows it.

---

## 2026-10-13 — push9: the update screen, a press at 'now' sets the limit, board `883a3cd1`

After the first drives on push8 (routes 132-137) the owner asked for four things and chose each in chat:

* **The board update screen** ("countdown add that ... ign on no engine on? we can do that"). The 2026-10-12 update
  hummed: the flasher's own trace (emitted into route 134 at 3.41 s) shows the relays went to stock wiring at the knock
  and the session then held 8 s with no data - the engine's 3rd-order firing vibration in the column FELL in that hold
  (0.95 against 1.35 before) - and rose about 2.5x from the erase on, through the copy, with no data on the bus and no
  hand on the wheel. The EPS was never commanded and logged no fault. Same step as the 2026-09-23 updates. So:
  * **A gate before anything is sent:** ignition on (panda), stationary (`0x158`, as before), engine off (`0x17C`
    ENGINE_RPM median under 400 over the first second, and again at the end of the baseline). Engine running:
    "Turn the engine off - leave the ignition on - then update". No `0x17C` at all (the bench) is logged and allowed.
  * **The stages on screen** (`mici/layouts/board.py`, params `EpsLkasFlashPhase` / `EpsLkasFlashCancel`): check,
    baseline, "relays to stock wiring" (the knock), a **5-second countdown "relays in stock - updating in 5..1"** (the
    old 8 s hold, now 5 s; INFO keep-alive pings at 2 s and 4 s inside the bootloader's 10 s), erase, write %, install,
    restart, done / failed.
  * **Cancel** in check, baseline and the countdown. In the countdown it is ABORT: BEGIN was never sent, bank 2 is
    untouched, the bootloader jumps back to the image it has. No Cancel from the erase on.
  * The trace adds `0x70B` (STATE/REASON in `pre` and `post`) and the census no longer counts `0x712-0x71F` as ours.
  * **Nothing new reaches the board**: the frames on the wire are the ones every bootloader since the first one
    understands, so the board in the car updates exactly as before.
* **A press during 'now' is the confirm** (see `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 11.3 and
  `speed_limit/now_confirm.py`). 'now' meant the limit was under 50 m away, not in force; the prompt opened 4.0-4.4 s
  later and a press in between was a 1 km/h step. Now a + or - release at 'now', the coming limit's way, engaged, SLA
  active or inactive, arms exactly that limit; when it comes into force SLA goes straight to active and card sets it.
  The card says "press - to set N" (or "+") instead of 'now' while a press would count. Replays (planner re-run on
  the logged inputs, NEW against OLD):
  * 137: 1 frame differs, 305.68 (the press at 305.16 armed 80: NEW goes straight to active where OLD showed
    preActive for one frame before that press's 0.5 s hold happened to confirm it).
  * 134: 69 frames differ, all after the 'now' presses at 313.60 (80), 446.50 (60) and 716.37/717.71 (80) - SLA goes
    active with the new limit when it comes into force instead of waiting in preActive for a second press. The phantom
    'now' windows (304.52-308.52 and 674.56-678.49) had no press and set nothing; 812.76 (a '-' while disengaged)
    armed nothing.
  * 136: 0 frames differ.
* **Board `883a3cd1`** (board `HANDOFF.md` 0p):
  * **The cluster alarms when the board is pinned at its ceiling with no hand**: once per ceiling run, 1.5 s past the
    300 ms debounce, even inside the 10 s quiet period. Only an alarm that started at or after the run's own start
    counts as the run's alarm (an unrelated one already sounding - driver monitoring, `op_saturated`, LDW - cannot
    use it up), and a single 10 ms dip below 90% starts a new run, on purpose. Route 137's second curve (645.9-649.6 at 160 counts, 52 km/h,
    1.1 m drift, openpilot's own alert at 648.95) was silent because curve A's alarm had ended 6.3 s earlier. 24-route
    replay: 33 alarms -> 34 (137 at 647.57; 12a's moves from 1414.11 to 1407.10); d1's 145.26 double chime does not
    come back. `hud_test` section 14.
  * **After a knock the board is stock until the comma is heard**: the knock clears the key cycle's "K2 taken" bit, so
    the image the bootloader starts waits like a key-on instead of re-splitting K2 0.3 s after start (the window where
    route f6's column moved 1.4 deg). The update TO this image is knocked by `b5f08583`, which keeps the bit - this
    applies from the next update on (and to a cancelled or timed-out one on this image).
  * 49,740 bytes, SHA-256 `0753b9f6c4e18d1340ce0b5ce40f9ae5344ba8917caa7be557f18635a978de78`, `APL1` at `0x08004000`,
    flags `0x04`, `0x707` = `883a3cd1`. Rollback: `b5f08583` (push8, flashed and driven on 134-137 and 139). The first
    push9 bundle, `4d427d46` (49,736 bytes, `d5396d72…`), is superseded and was never flashed.
* **How to update now:** parked, **ignition on, engine off**; Settings -> Gateway; the screen counts down 5 s with the
  relays already on stock wiring, then erase / write / install / restart.

Not reviewed by an independent agent this round (the session hit its agent limit); the code was checked by the main
session, all targeted suites pass (427 tests), ruff/codespell/compile-check clean, ty has the same 4 pre-existing
diagnostics.

---


### push9 review (2026-10-14)

- **One 'now' for the card and the planner.** The planner's window was a second copy of the
  HUD's and disagreed on 4 of 22 routes: lost far out it ran the distance down for 3 s instead
  of 2 and opened 'now' with no card on screen (112 at 2170.65), it lacked the "distance only
  counts down" hysteresis (10f, 129: 'now' on the card, a plain 1 km/h step in the planner) and
  the 15 s / 500 m window (12a). The state machine is now `now_confirm.ComingLimit`; the HUD's
  `ApproachState` and the planner's `NowConfirm` both run it. Replayed over the 22 routes: 0
  differing frames (was 10f/112/129/12a); 134/136/137's now-confirm replay is unchanged.
- **The key is the target's way.** With an offset, '-'/'+' and "nothing to press" are decided on
  limit + offset (the number the card shows) on the card and in the planner alike: 100 + 10%
  with MAX at 110 asks for nothing; at 105 it is '+'.
- **Only with the card on.** With `HudApproachingLimit` off nothing arms. A press while the card
  is hidden (MAX, an alert, the lane-block tile) still counts - plannerd cannot see the screen.
- **Update screen.** A Cancel in the countdown's last instant is honored (Cancel leaves the
  screen 0.5 s before the end and is read once more right before BEGIN); "too late to cancel"
  replaces "canceling" once the erase has begun; "canceling" while starting. The engine is
  watched through the countdown (started then: ABORT, "engine on"). No `0x17C` through the
  panda is refused ("no engine data"). ABORT waits for its echo and ACK.

### push9 re-review (2026-10-14)

- **Pressed past the target disarms.** Armed 100 from 102, '-' to 101 and again to 99: the arm
  stayed, and the 100 coming into force would have RAISED the set speed back to 100 (the mirror
  image: '+' past an armed 80 would have lowered it). `NowConfirm.track()` now sees the set speed
  every frame after that frame's presses and disarms once it is past the armed target - the
  ordinary prompt then, as for any unarmed change. ON the target it stays armed: the confirm
  writes the number the set speed already is and goes straight to active, where a disarm would
  flash the prompt for a frame. 134/136/137's now-confirm replay is unchanged.
- **The countdown keeps the gate's proof.** It stopped only for the engine starting; `0x17C`
  disappearing under it (the ignition switched off or to accessory) read as "not running" and
  the erase went ahead. Now `0x17C` seen at the gate and gone for `ENGINE_LOST_S` (1 s) is
  ABORT and "no engine data", and the panda's ignition reading off is ABORT and "ignition off" -
  before BEGIN, the old image stays. The bench (no `0x17C` from the start) goes on as before.
- **Board `883a3cd1` replaces `4d427d46` in the bundle.** The no-hand pin alarm is once per
  ceiling run, and the first build let *any* alarm sounding during the run count as that run's -
  including a driver-monitoring steer_req alarm that had started before the run and was about to
  hit its own 5 s cap, after which the ceiling was never heard. Now only an alarm that started at
  or after the run's start counts (board `hud.c`, `hud_test` 14f). A 10 ms dip below 90% starts a
  new run, deliberately (no hysteresis). A steer_req onset within 10 s of a pinned alarm is
  silent, as before (the existing quiet period). The 24-route replay is unchanged (34 alarms,
  every onset identical to `4d427d46`). 4 bytes larger; board `HANDOFF.md` 0p.

## 2026-10-12 — push8: the board's lane-change cap is off (board `b5f08583`, nocap)

The owner, on hearing the board halves its torque on every indicated lane change: "On lane change remove steer
caps". He was told why it was there (S10-4 took it off, route da latched the EPS, S14-1 put it back), and chose: no
cap; the board's driver-torque fade only while he pushes against openpilot's command; hard pushes either way
still hand back. **Board only. Nothing openpilot computes or sends changes.** This entry is the bundled image
and the wording that assumed the cap. push8 is push7 (`1982fe6a1`) plus this; opendbc stays `dc7f1e7d`.

* **The image.** `eps_lkas_appslot.bin` is board `b5f08583` (branch `nocap`, on `686e4603`), 49,660 bytes,
  SHA-256 `d5b4b9d34af8d1f41a26fd33a2982637546c21f31819cff619be1f2c4fb7446c`, `APL1` at `0x08004000`, flags
  `0x04`, built with `tools/flash-incar-stage10-appslot.bat`'s flag set including `GW_WAIT_FOR_COMMA`.
  * `GW_BLINK_CEIL_PCT` goes 50 -> 100.
  * The driver fade (100 % at 20 counts to nothing at 90) is skipped while the driver nudges the way the board is
    going: openpilot's request over 10 counts his way, the wire not against him, the push under 90.
  * Against the driver, and for any push of 90 or more, the fade is 686e4603's.
  * The hand-back (110 at once, 90 for 80 ms outside the indicator), authority 160, the step limits, the floor,
    the counter rules and the lc-alarm HUD gating are unchanged.
  * Rollback: `686e4603`, SHA-256 `82fefd72…` (push7).
* **Replayed** open loop from the raw CAN over every board-engaged lane change on d9, da and 10f-130 (board
  `HANDOFF.md` 0o). On the 33 of 10f-130:
  * mean command 44.6 -> 52.6 counts against a request of 55.9;
  * frames held below the request 21 % -> 7 %;
  * about +0.45 m/s^2 at the median peak;
  * no frame over 100 counts against a push of more than 50.
  * Route da's latch (the event that put the cap back) happened under a LEFT indicator, so `686e4603` would have
    held 80 there. On the yank's frames `686e4603` gives 80, 80, 80, 80, 80, 76, 36, 8, 0 and `b5f08583` gives
    160, 160, 152, 131, 108, 76, 36, 8, 0: for the first ~40 ms nocap pushes against the driver with up to twice
    the torque of the image it replaces, and it enters the yank at da's own logged 158-160. The two agree from
    about 55 counts of driver torque up; both are at 0 by 92 and hand back at 113 (logged on da: 102, 62, then 42
    into the 141). What da did not have is the S12/S13 fade, hand-back and 40-per-frame down-step, not the cap.
  * **A light hand against the board under the indicator** (20-55 counts) now meets up to 160 counts, twice
    `686e4603`'s 80. Push harder and it fades as before.
* **openpilot.** It never saw the cap: the reported torque was always the request (section 3.6), so under the
  indicator report and wire now agree. That removes the cause of 120/121's `lac.saturated`, which `OP_SATURATED`
  no longer carries anyway (lcalert).
  * `carStateSP.linbusGateway.blinkerCapped` still decodes `0x704 INH_BLINKER`. The board still sets that bit
    under the indicator (it suspends its 80 ms hand-back and its own ceiling alarm there), so from this image
    the field reads True with nothing capped. The name is now historical; the capnp comment says so, and nothing
    acts on it.
  * opendbc's comments that still describe a ceiling are left for its next commit: `carstate_ext.py`
    `_update_linbus_gateway`, `structs.py` `blinkerCapped`, `carcontroller.py` above the reported torque, and
    `FORK.md`. This branch does not move the submodule.
* **Also in push8:** three British spellings in comments that CI's codespell flagged (`signaled`, `honored` x2),
  and the docstring of `test_latcontrol_reported_torque.py`. No code changes; `sendcan` replayed on 120 and 130 is
  identical to push7.
* **What to watch on the first drive** (board `HANDOFF.md` 0o):
  * `0x704 CMD_APPLY_STEER` above 80 under `INH_BLINKER`;
  * no EPS error state, B0.4 latch or `0x70B` REASON 8/9 in or after a lane change;
  * no `RDM_HUD` + `BEEP` 1 rising during a lane change.

---

## 2026-10-12 — push7: the MAX card and the lane-block tile together (integration)

push6 (`c8f8be771`: the lane-change alarm fix with board `686e4603`, full brake without the aero credit, the
`steeringPressed` debounce), then the MAX card in two styles (`hudstyles` `7c3296719`, the next entry but one), then the
lane-block tile (`edgetile` `80ec54c4e`, the next entry), merged in that order. opendbc stays at push6's `dc7f1e7d`:
neither branch moves the submodule. The bundled board image is push6's (`686e4603`, SHA-256 `82fefd72…`).

* **Where the two meet: the top left.** The tile's hand-over hooks were written against round 5's drawing
  (`_draw_pending_max`, `_approach_blocker`, `drawing_top_icons`); the MAX card replaced that drawing with one card
  in either style (`_draw_card`, `hud_model.MaxCard`). In the merge a LEFT tile puts the card away in both styles,
  as the tile intended: the coming limit's card goes on the tile's first frame and comes back once the tile has faded
  out (the split card opens out of its MAX half again, as after any cut); the confirm's card is hidden while the tile
  shows; the card's turn into MAX (`_done`) is never started under a left tile and is cut on the frame one comes, as
  another alert cuts it; the face stays away (the tile is a top icon). A right tile leaves the card drawn: the coming
  limit's card ends at x 272 (split card) or 194 (stock-plus) at most on the M4 frames, and the tile starts at 376.
  The confirm's prompt is an alert, which cuts the tile on its first frame, so in practice only the coming limit's
  card ever shares the screen with a tile.
* **Settings.** Ten HUD keys: nine toggles (`HudLaneBlock` new) and the `HudLimitStyle` option. `params_keys.h` has
  302 entries (`Params().all_keys()` agrees). `settings_ui.json` was recompiled from the merged `visuals.yaml`
  (byte-identical to git's own merge of it).
* **Tests.** `test_hud_lane_block.py` 25 (+3: a left tile over the coming limit's card in both styles - the card gone
  on the tile's first frame and pixel-identical to the same drive with no coming limit, back only once the tile has
  faded out; a right tile leaving it drawn and clear; the confirm's card cutting the tile). `test_hud_cluster.py`
  103, `test_hud_render.py` 51, `test_lane_change_nudge_refused.py` 18, `test_lane_block_alert.py` 15. Full runner:
  2575 passed, 45 skipped, 1 xfailed.
* **Nothing sent changes.** Replayed against push6 on routes 115, 120, 121 and 130: the closed loop (controlsd with
  this tree's CarInterface) sends 310,388 sendcan frames (760,458 CAN messages) identical to push6's, and the open loop
  (the logged carControl) 312,208, also identical. DesireHelper's state, direction, desire, lane turn and timer are
  identical on 62,366 model frames, also with RELC forced on; there `nudge_refused` rises 3 times (120 once, 121
  twice). The chime's alert has no visual alert and can only take the place of LOWER/LOWEST alerts, none of which has
  one; dropping the two block alerts could only uncover a LOW alert with a visual (`steerSaturated`, `ldw`,
  `steerTempUnavailableSilent`, `driverUnresponsive1`) that was up at the same time, and none of the four routes
  logged a block alert at all.
* **Renders.** Re-rendered on push7 and on each branch tip with the same harness: the MAX-card scenes (route 10f, both
  styles, 1x and 2x) 195 of 195 PNGs pixel-identical to `hudstyles`, with identical frame logs; the tile scenes
  (routes 120, 114, 115) 20 of 20 identical to `edgetile`. The meeting case, a left and a right block injected over
  the M4 approach and a left block during the confirm, is rendered in both styles.

---

## 2026-10-12 — a refused lane change is a tile on the blinker side (the owner's option 3)

The owner kept the refusal (lane changes toward a detected road edge stay refused; upstream PR #2049 declined) and
asked for a better display of it. Of the three mock-ups he chose option 3, the blinker-side tile. Nothing here changes
when a lane change is refused or started: RELC, `NUDGE_FIRM` and the stale-torque guard are untouched.

* **The tile (comma 4, `HudLaneBlock`, on by default; sunnylink Visuals → HUD → Lane Change Block Tile).** While the
  blinker asks for a change that a road edge or a car in the blind spot refuses, a 92 x 100 px tile sits in the top
  corner on the blinker's side - the stock green arrow's place - with the arrow dimmed and struck through in orange
  and EDGE or CAR under it. It replaces the orange road-edge alert (which filled the whole road view with three lines
  and no side) and the orange blind-spot band. It is drawn only while modelV2 says the change waits (preLaneChange)
  and the same block selfdrived acts on is set, in selfdrived's order. When the block clears, the tile goes on the
  frame the stock 'steer left / right' (or 'changing lanes') comes, and that alert's green arrow takes the same
  corner - never a struck-through arrow over the green one; with no alert to follow (the blinker off) it fades out in
  0.15 s. The turn-intent arrow over the wheel now stays up during the block (upstream hid it). Any other alert takes
  the screen at once. A left tile has the top left: the face, the stock MAX number, the coming limit and the confirm's box make
  way while it shows (`hud_lane_block.py`; hooks in `HudRendererSP`).
* **Silent until a nudge is refused.** The block no longer chimes when it appears. A nudge toward the blinker that
  would have started the change but for the block (`DesireHelper.nudge_refused`, published as
  `modelDataV2SP.laneChangeNudgeRefused @3`) flashes the tile's rim twice and plays one `prompt`
  (`laneChangeNudgeRefused @32`, size none, `Priority.LOWER`: above `steerOverride`, below every alert at LOW and
  up; the few LOWER or LOWEST banners - such as experimental mode switched, bookmark saved, startup, the EPS-latched reminder -
  it preempts for its 0.2 s). One nudge is one refusal: the flag holds until the wheel has been let go for 0.2 s
  (`NUDGE_RELEASE_FRAMES`; frames with a stale torque do not count as let go); a stale gateway torque is no nudge. And
  it rises at most once in 2 s (`NUDGE_REFUSED_GAP_FRAMES`): a nudge inside that is refused silently and must be let go
  before the next one counts. The limit is in DesireHelper, not selfdrived, so that the chime and the flash - both on
  the flag's rising edge - are always a pair. While another alert (LOW and up) is on screen a refused nudge has no answer at all:
  the chime does not play and the tile is not drawn; that alert has the screen and the sound. selfdrived drops the two
  block alerts and raises the chime only with the setting on and only on a comma 4 (`lane_block_alert.py`); the events
  are still raised and logged. With the setting off, and on the comma 3X, selfdrived is upstream; the arrow fix below
  is behind no setting and applies to every comma 4.
* **Upstream fix in passing (mici `alert_renderer.py`).** The 'changing lanes' arrow took its side from the last icon
  drawn. After a block there is no 'steer left / right' before the change, so the arrow was missing (after today's
  road-edge alert too) or on an earlier change's side. It now takes the side from `modelV2.meta.laneChangeDirection`.
* **Replayed** on the logged routes with this build's RELC (enabled) and DesireHelper and selfdrived's lane-change
  logic: route 120 t 485.6-489.25 right (refused at the median; one chime at 487.8 for the push of 487.7-488.4, which
  dips under steeringPressed for 50 ms at 488.0; the edge clears at 489.25, 'steer right' with the green arrow, the
  change starts on the next nudge at 490.15); route 114 t 773.1 left (the kerb lane, refused with a chime at 773.25);
  route 115 t 837.45 left (the blinker still on after a finished change, before a turn: the tile, silent - his
  torque points the other way - until the speed drops under 32 km/h at 840.85). Renders of the real UI are in the
  session's scratchpad (`edgetile/renders/sheet.png`).
* **Review fixes (same day).** The chime had no rate limit: replayed over all 100 logged routes with RELC forced on,
  route d8 t 452.0-457.3 chimed five times in 5.3 s, two of them 0.4 s apart from one wobble, and a hand pulsing on
  the wheel could chime 25 times in 10 s. Now at most once in 2 s (above), with route d8's wobble as a test. The tile
  stepped its fade once per caller per frame (4-6 times on the device: alpha 0.60 after the first frame instead of
  0.14); it now steps once a frame. It also faded out over the green arrow for about 9 frames when the change was
  allowed; it now goes on the frame the arrow comes. `Priority.LOWEST` -> `LOWER` for the chime, a stale torque no
  longer counts as letting go, and the notes on what happens under another alert are corrected.
* **Tests.** New `test_lane_change_nudge_refused.py` (18), `test_lane_block_alert.py` (15) and `test_hud_lane_block.py`
  (22, 9 of them in a headless window). `test_hud_cluster.py` counts nine settings. The 332 HUD renders (PNGs) of
  `test_hud_render.py` are pixel-identical to `d95ce71c3`'s.

---

## 2026-10-12 — the MAX card: two styles, D1 and D3, behind one setting; "press - to set 100"

The owner picked D1, then D3, then "do d1 and d3, I like both, allow a toggle" (the drafts of 2026-10-12). So the
coming limit and the speed-limit confirm are now one card in the stock MAX number's corner, drawn in the style a new
setting picks. Design A's dashed box and pill, its marching dashes (V3) and option C's dashed box and pill are gone.
Nothing here changes what is shown or when: the rules (`ApproachState`, the confirm's fade, cut and top-icon logic) are
round 5's. Only the drawing changes, plus two hand-overs that no longer fade through nothing.

* **Limit Change Style** (`HudLimitStyle`, sunnylink Visuals → HUD, next to Approaching Limit). It is an INT, 0 or 1,
  default 0. It is drawing only, read live like the other `Hud*` keys (at most once a second), and fine to change
  while driving.
  * **Split Card** (0, D3, the default). One black card. The MAX held now is small on the left (40 px digits, "MAX"
    under them) and the coming limit is big on the right (62 px), with a chevron and "in 300 m". A 4 px red bar along
    the top of the coming half fills as the change comes, and at "now" it is full and glows twice; the caption becomes
    a white "now" chip. While the confirm waits, the target is in a dashed outline (two flashes, then the march) and a
    footer reads "press - to set 100" with the green key. On the press the target slides into the stock MAX digits'
    place and grows to their size (81 px) over 0.3 s while the card fades, and the stock MAX number takes over where
    it lands. The draft's single 62 px MAX card is not built, nor its mini "MAX" under TAKE CONTROL (an idea only, not
    approved).
  * **Stock-plus** (1, D1). The number always sits where the stock MAX digits are. While a limit is coming, a
    see-through card with a thin outline holds the limit at 96 (69 px digits), a chevron, "in 300 m" and a 6 px bar
    that drains. At "now" the outline goes full white with one 0.2 s halo. While the confirm waits, a solid card in the
    alert's color holds the target in the stock MAX digits exactly, and "press [-] to set 100" sits on MAX's own line
    with the key in place of the "-". Its outline flashes twice, then breathes once a second (alpha 170 to 255), and
    the key never blinks below half. On the press the card and prompt dissolve round the digits while "MAX" and its
    shadow come in.
* **"press - to set 100"** replaces "press - to confirm" wherever the fork draws the prompt: the card, and the one-line
  banner it falls back to ("press + to set 55"; with no limit there is no number, so it stays "press + to confirm").
  The key is the one the alert's text names, which is the button the confirm accepts since batch 2. The number is the
  set speed that press gives (`target_speed`). sunnypilot's own full-screen alert (the setting off) keeps its own text.
* **The coming limit's card turns into the confirm's.** A confirm arriving within 0.25 s of the coming limit's card
  takes that card over, with no fade out and in. Stock-plus grows to MAX size in 0.25 s; the split card grows its
  footer. The flashes follow.
* **The card turns into the stock MAX number after the press.** Before, the confirm's box faded out with the prompt,
  and the stock MAX could only fade in after the prompt had gone. On route 10f's M4 (HYPO, press at 777.0) that left
  about 0.35 s with the digits dipping, during which the driver-monitoring face counted as free to show. Now, once the
  set speed is the card's target, the card turns into MAX over 0.3 s (`CARD_DONE_S`). It is held landed while the
  prompt finishes fading, and then the stock MAX is drawn at full for the rest of its 2.5 s. The face stays away
  throughout. Another alert, a new prompt, or the view gone for 0.25 s cut it on the frame, as before.

Kept: Approaching Limit stays the switch for the coming limit in both styles. With it off there is no coming card,
and the confirm still uses the style's card. The stock MAX 2.5 s after a manual change still holds the coming limit
back, which then comes in when MAX goes (the D3 draft's 62 → 40 shrink for this case is not needed). Banners,
critical and no-entry alerts, the stop timer and standstill are untouched.

Code: `hud_model.MaxCard` is one frame of the card (approach, waiting, done), with the clocks `CARD_ENTER_S`,
`CARD_GROW_S`, `CARD_DONE_S` and `NOW_HALO_S`, plus `box_breath()`, `confirm_prompt()` and `prompt_parts()`.
`HudRendererSP` decides the card once and draws it through `_draw_card()`, which picks the style's drawer (the 'now'
flash's rhythm, the only other thing the style decides, is picked in `_draw_approach()`). The two drawers are `hud_draw.split_card()` (with `split_layout()`) and `hud_draw.stock_plus_card()`, and they replace
`pending_max()` and `approach_max()`. The stock `_draw_set_speed()` is overridden only to skip drawing while the card
turns into MAX.

Tests: `test_hud_cluster.py` covers the key (registered INT "0", backed up, the default, the range, unknown and
unreadable values meaning the split card, read live), the sunnylink item (an option with Split Card and Stock-plus,
json in step with the yaml), the prompt wording and the breathing. `test_hud_render.py` runs every earlier state in
the default style. Its new expectations: stock-plus through the same states; stock-plus's waiting target pixel-equal
to the stock MAX digits; the outline (two flashes, then the march or the breath, with the digits never changing); the
hand-over with no fade; the card turning into MAX in 6-7 frames with no frame for the face, then pixel-equal to the
drive with no card; 20 states neither card draws, pixel-equal across the styles; and the style switched while a card
is coming, waiting and turning into MAX. The renders of the base commit `d95ce71c3` and of this build, both styles,
over route 10f (M4 110 → 100, Bringelly Rd 50 → 70, stopped, the confirm prompt held) are compared frame by frame:
every capture neither style draws a card in is pixel-identical to the base commit's (18 of 18 in each style). The
frames after the press differ by design, until about 1.5 s after it: the stock MAX is at full from the card's end,
where the base faded it in only after the prompt had gone.

Review fixes, a second commit on the same branch (the review said ship, with these findings):

* **No "MAX" while openpilot is not engaged.** The coming limit needs no engagement, and on this non-PCM car the set
  speed keeps its last value while disengaged, so the split card's left half said "110 MAX" for a speed nothing
  held. The stock MAX is only drawn while engaged; the card now follows it and shows the stock disabled char "–"
  there (`_current_text()`). The coming limit itself is unchanged. Stock-plus never drew that half. A confirm that
  is done while disengaged no longer turns into a MAX the stock HUD would not show: its card fades as before.
* **The stock-plus confirm card has a width limit.** "press - to set 110" with "limit 100 +10" made a card reaching
  x 388, over the stop timer at a standstill. The card now stops 40 px short of the road's right edge, or 14 px short of
  the timer when it is up, and squeezes its row to fit (to 0.8 wide, then smaller, as the banners do). Moving, no
  confirm needs the squeeze; stopped with the timer up, any confirm with the offset note does. The split card is
  narrower than either limit: its right edge is at x 280 in this widest case, inside even an hour-long stop's 286.
* **The turn into MAX starts where the card was.** If the prompt was already fading when the press set the speed,
  the first frame of the turn jumped back to full. It now starts at the strength the card was last drawn at and
  eases to full with the turn.
* `hud_draw` takes the stock MAX's sizes from upstream's `FONT_SIZES` instead of copies. The fork guide now lists
  the upstream `HudRenderer` internals the subclass relies on, and a test names them.

Tests: the not-engaged coming limit in both styles; the widest confirm moving, stopped and stopped past an hour,
in both styles, with the timer pixel-identical to no confirm; the turn into MAX from a prompt 1 and 2 frames into
its fade; and the upstream names.

---

## 2026-10-12 — full brake without the aero credit; `steeringPressed` debounced (push6)

The owner's two decisions of 2026-10-12, both "yes, next push": (F) no wind allowance at full braking, and (A) a
5-frame debounce on "hands on wheel", with `STEER_THRESHOLD` 600 unchanged. opendbc `push6`, sunnypilot `push6` (both
from `lcalert`). Not pushed, not flashed; no board change.

**(F) Full brake reaches the ceiling at any speed** (opendbc `carcontroller.py`, `ELESYS_FULL_BRAKE`,
`elesys_wind_credit_scale()`; car doc 7.11). The brake block sends `brake_last - wind_brake`: upstream takes the drag
the car gets for free off the brake. At full brake that only lowered the ceiling - 245 counts at 30 km/h, 236 at 60
(measured 235-236 on 121), 223 at 100, 215 at 126, against 253 standing still. Once `brake_last` sits at its ceiling
the credit is now ramped out, on `HONDA_ELESYS` only. The ceiling is 0.99, not the 1.0 the study named:
`actuator_hysteresis()` holds a rising request 0.01 under itself, so a request of 1.0 leaves `brake_last` at exactly
0.99 and `brake_last >= 1.0` is never true. Full brake is therefore `int(0.99 x gain x 256)`: **253 counts** at a
brake gain of 1.0 (not the 254 the study expected), 255 (the code's `NIDEC_BRAKE_MAX - 1` and panda's `max_brake`)
from a learned gain of 1.0062. What full braking gains is the credit, 2.6 x `wind_brake` m/s²: **about 0.08 at
30 km/h, 0.17 at 60, 0.30 at 100 and 0.39 at 126** (the curve stops growing at 35 m/s).

* **A ramp, not a step** (review follow-up). The first cut dropped the whole credit in the send where `brake_last`
  reached the ceiling: a step of up to 38 counts (0.3-0.4 m/s² at highway speed) that bypassed `brake_last`'s own
  rise limit (0.01 per frame, about 5 counts a send), and a request hovering at the ceiling switched it in and out
  whole. Now the credit is multiplied by a scale that walks toward 0 by 0.1 per 100 Hz frame while `brake_last` is at
  the ceiling and back toward 1 at the same rate below it (`ELESYS_CREDIT_RAMP`): out in 10 frames (100 ms, 5 sends),
  back in 10, at most 7.6 counts a send. It is 1.0 whenever longitudinal is not active, so every engagement starts with
  the whole credit. No extra exit hysteresis (tested, below).
* Every frame of an application that never reaches the ceiling is bit-identical (the scale stays exactly 1.0); after
  one, the credit comes back over at most 10 frames and the release limiter starts from the higher command (up to
  the credit, <= 38 counts, for <= ~8 sends). Both pump rules already run the pump continuously above 200 counts
  while moving, so the pump bit does not change. The soft stop's ceiling still applies after it. Stock AEB is
  untouched: panda forwards the CMBS `0x1FA` while its brake is >= openpilot's, and a real CMBS brake is 320-1022
  counts.
* **One interaction, kept:** the brake learner weights its error by the commanded fraction, which at full brake is now
  0.99 instead of 0.99 - wind. It is gated on 150 frames of a steady plan, so it rarely runs during an emergency; on
  the replays below no frame after a full-brake episode changed.

Replayed open loop (this table is the first cut, the step; the ramp's replay follows it): this tree's `CarInterface`
driven by each route's CAN, `CarController.apply()` on the LOGGED `carControl` with the route's own stored brake gain
and CarParamsSP flags; old = `lcalert`. Old reproduces the logged `COMPUTER_BRAKE` exactly on 98.4-99.6% of frames.

| route | full-brake `0x1FA` frames | where | logged / old → new | other `0x1FA` frames |
|---|---|---|---|---|
| 120 | 18 | t 114.8-115.2, 57→54 km/h, plan -3.0 to -3.1 | 237-238 → 253 (gain 0.9996) | identical (26 050) |
| 121 | 244 | t 239.9-244.7, 61→20 km/h, plan -2.5 to -4.0 (the study's t243-247 ask) | 235-248 → 252 (gain 0.9974) | identical (23 231) |
| 130 | 62 | t 47.5-48.8, 33→23 km/h, plan -2.3 to -2.6 | 248-252 → 255 (gain 1.0158, clipped) | identical (55 588) |
| 115, 12d | 0 | - | - | identical (50 913, 71 014) |

130's 77 km/h stop at about 376-390 s never reached full brake (plan -2.25 m/s² at most, `brake_last` 0.878, 208
counts) and is unchanged. The pump bit and `0x0E4` are identical on every frame of all five routes. Closed loop (the
lateral harness below, which also runs longcontrol) gives the same picture with a gain of 1.0: 120 21 frames 237-238
→ 253, 121 242 frames 235-249 → 253, 130 62 frames 245-248 → 253, nothing else in `0x1FA`.

**The ramp, replayed** (the same harnesses; step = push6's first cut, opendbc `c973ec83`; ramp = this tree). `0x1FA`
is identical to the step build on every send outside the full-brake episodes, and so are the pump bit and `0x0E4` on
every frame; inside them only the entry and exit windows differ (sends 20 ms apart):

| route | episode | step: entry / exit | ramp: entry / exit | largest step in the episode, step → ramp |
|---|---|---|---|---|
| 120 | t 114.8-115.3, 57→53 km/h, 18 sends at full | 232, 253 / 253, 234 | 232, 238, 242, 245, 248, 251, 253 / 253, 248, 245, 242, 233, 229 | +21 → +6 in, -19 → -9 out (then the request's own drop, 229 → 214; 228 → 214 in the other two builds) |
| 121 | t 239.9-244.7, 61→20 km/h, 244 sends | 234, 252 / 252, 0 | 234, 238, 242, 245, 249, 252 / 252, 0 | +18 → +4 in; out is the disengagement, 252 → 0 in both |
| 130 | t 47.5-48.9, 33→23 km/h, 62 sends | 248, 255 / 255, 244 | 248, 250, 252, 254, 255 / 255, 249, 244, 242, 241, 239 | +7 → +2 in, -11 → -6 out |

Full brake still reaches 253 / 252 / 255 (each route's stored gain). Sends differing from the step build: 10 / 4 / 8,
all in those windows; 115 and 12d never reach full brake and are identical in all three builds. Against `lcalert` the
ramp differs on 23 / 244 / 67 sends: the full-brake ones and the 5 ramp-back sends on 120 and 130, nothing after
them. Closed loop (gain 1.0): 8 / 5 / 9 sends differ from the step build, all in the entry and exit windows (largest
step 18 → 5 and 22 → 16 on 120, 18 → 4 on 121, 9 → 3 and 13 → 8 on 130); `0x0E4` and `0x500` are identical to the
step build.

The reviewer's boundary tests, through the real controller; sends stepping 10 counts or more, lcalert / step / ramp:
a request hovering at the boundary with Gaussian noise (60 and 110 km/h, 0.02-0.10 m/s², tuner on and off, 12 cases)
200 / 1020 / 267, largest step 27 / 52 / 27; the routes' own hard-braking request jitter re-centred on the boundary
(19 windows x 4 offsets, 56 128 sends) 35 / 163 / 43 at 60 km/h and 34 / 168 / 57 at 110, largest 21 / 32-38 / 21-23;
a ramped request into full brake, largest rise 6 / 39 / 8 at 126 km/h; a crude closed loop (I-only longcontrol, a
lagged car with the real aero) ramped onto the boundary at 60-126 km/h, 0 / 1-3 / 0 such sends, largest 1 / 14-35 /
3-7, with the same decel as the step. An exit band (credit kept off down to `brake_last` 0.97 / 0.95 / 0.93) on 15
hover cases with the tuner on: 332 / 332 / 231 sends against the ramp's 359, but at 0.10 m/s² of noise up to 132
against 64, and there 5-35 counts more brake on average than the request. Not taken.

**(A) `steeringPressed` is debounced** (opendbc `carstate_ext.py`, `STEERING_PRESSED_MIN_COUNT = 5`; car doc 10.2,
LKAS-GATEWAY-PROTOCOL 8.2). `_update_driver_torque_validity()` now decides `steeringPressed` once per frame from the
FINAL torque - 0x18F's, or the board's `0x700` mirror substituted for a latched 0x18F - through upstream's
`CarStateBase.update_steering_pressed(abs(torque) > STEER_THRESHOLD, 5)`, the count Hyundai, VW, Ford, Tesla and PSA
use. One counter sees every frame on both paths, so a push that spans the switch from 0x18F to the mirror is not cut.
It overwrites upstream's `carstate.py` line (no double application). A latched value with no board frame counts as not
pressed and the output is False outright, as before. The counter climbs on a pressed frame, falls on any other, is
clipped to 0..11 and reads pressed above 5: **a press registers on its 6th consecutive frame (50 ms late), a blip of
1-5 frames never does, and a press held 11 frames or more releases on its 6th clear frame.** Single dropped frames
inside a hold no longer flick the flag. Other Hondas keep upstream's single-frame flag; the threshold stays 600.

Replayed: this tree's CarState on each route's CAN (old reproduces the logged flag on 99.5-99.8% of frames). Engaged
= `latActive` above 52 km/h; "free" = engaged time 2 s or more after the last press, which is what torqued and lagd
learn on and when the "steer required" check is armed; MADS = rising/falling edges of the flag while MADS is enabled
or overriding, i.e. its `overriding` entries and exits (the logged state machine's own count in brackets).

| route | pressed old → new | onsets per engaged minute | free | MADS override entries / exits |
|---|---|---|---|---|
| 115 | 8.9 → 6.4% | 80.4 → 11.5 | 37.2 → 81.1% | 974/972 → 257/252 (976) |
| 120 | 7.3 → 4.5% | 80.5 → 8.2 | 29.1 → 78.3% | 551/550 → 100/99 (549) |
| 121 | 19.0 → 16.8% | 99.2 → 20.3 | 29.4 → 64.2% | 513/509 → 154/148 (519) |
| 12d | 49.0 → 48.9% | 55.3 → 23.8 | 17.9 → 29.5% | 596/590 → 309/304 (599) |
| 130 | 40.7 → 40.3% | 70.0 → 24.9 | 22.0 → 37.3% | 783/777 → 373/366 (783) |

As the study predicted, the night routes (blips) change most and 12d/130 (long holds) least in pressed time, but
every route loses 55-90% of its onsets.

**Lane-change nudges** (this tree's `DesireHelper` on the replayed CarState, the logged `latActive` and lane-change
probability). Route d9, 28 logged `preLaneChange` windows: the 11 that armed in the log arm old and new, 9 at the same
instant and 2 50 ms later (216.8 s, 248.3 s). Of the six that opendbc `b7d15a98` rescued (logged 0x18F peaks
600-1225): 609.7 s (937) arms 150 ms EARLIER, 768.8 and 854.5 s unchanged, 850.8 s 50 ms later, and 889.9 s (644)
and 70.8 s (1225, 32 km/h, under the lane-change speed) arm neither old nor new - at 889.9 the board's mirror shows
the push going left on a right blinker. The four with a wrong-way logged 0x18F (450.7, 460.6, 727.7, 757.7 s) arm on
the mirror, unchanged. One re-arm is lost (866.3 s, inside a window both had already armed at 860.65 s; the push
crossed 600 one model frame before the blinker went off). Routes 115/120/121/12d/130 (36 windows): every arm kept,
6 of them 50 ms later, and **12d at 833.2 s arms 0.85 s EARLIER**: a push that started at 1935 counts dipped for one frame
(1935, 1419, 129, 645, 516); the old flag dropped on the dip and reset `NUDGE_HOLD_FRAMES`, the debounced one bridged
it, and the lane change armed in the push's release tail. That is the only timing change over 50 ms in the wanted
direction.

**Closed loop, lateral** (lcalert's harness: the real controlsd lateral step against the real `CarInterface`, now fed
the replayed `steeringPressed`): the debounce changes when the integrator is frozen, so `0x0E4` moves a little through
most of the engaged time. 120: 80.2% of `0x0E4` frames differ, median 1.8, max 5.6 serial counts (of 160); 121: 63.0%,
median 1.5, max 6.4; 130: 48.8%, median 0.2, max 1.25. 94.5/92.6/79.7% of the differing frames are within 10 s of a
frame where the flag differs. Lateral error is unchanged to 4 decimals (mean |desired - actual| 0.0533 / 0.0764 /
0.0736 m/s² old and new); selfdrived `steerSaturated` 0 old and new; `lac.saturated` 53 → 72 (120), 77 → 83 (121),
0 → 0 (130). `0x500` differs only in `INTEGRATOR` and `OP_STATE`/`RELEASE_DRIVER`, which follow the flag, and
`OP_SATURATED` does not change. The board parses `OP_STATE`/`RELEASE_DRIVER` and reads neither. It **does** read
`INTEGRATOR`: the bundled board (`686e4603`, `gw_active.c` ~1975 and ~2744) refuses the first engagement of a drive
while |i x 100| > 15 (`GW_INTEG_MAX`, `0x70B` REASON 11). On the closed-loop replays the debounce moved `INTEGRATOR`
by at most 3 / 4 / 1 units (120 / 121 / 130), and only after the board's first engagement (36.6 / 25.7 / 278.9 s):
before it, 0 of 365 / 256 / 2777 frames differ, so that inhibit decides exactly as before. The ramp (above) changes
nothing in `0x500`.

**Tests.** opendbc `test_elesys.py` 106 → 130: `TestElesysFullBrakeNoWindCredit` (15: 253 at 30/60/100/126 km/h
where the old rule gave 245/236/223/215, reached within 5 sends and climbing only; with the tuner on, 0.99 x the
learned gain, 252-255; the 0.99 threshold against `actuator_hysteresis()`; a ramped request into full brake rises at
most 10 counts a send where the step rule rose 31-39 at 100-126 km/h; leaving full brake for just below it at
126 km/h drops at most 10 a send where the step dropped 32+, and is the old rule's again 10 frames later; a request
hovering at the ceiling (60-126 km/h, noise 0.02-0.05 m/s²) never steps more than 20 counts or the old rule's own
largest step + 8, and steps 10+ counts under half as often as the step rule; an application that never reaches the
ceiling bit-identical to the old rule, tuner on and off, at 15-126 km/h; every send with the scale at 1.0
bit-identical on a light/firm/full/release trace; with the tuner on only the ramp, the release limiter or the
learner carry a difference past full brake; the release limiter still bounds every drop at 32; a disengagement
resets the scale; NaN speed still sends; the pump at full brake; the scale function; the CR-V keeps upstream's
credit) and `TestElesysSteeringPressedDebounce` (9: threshold still 600; 1-5 frame blips both
signs; the 6th frame presses; release after a long and a short press; chatter inside a hold; the `0x700` path the
same; one counter across the 0x18F → mirror switch and back; latched with no board is False; other Hondas
undebounced). The 15 full-brake tests error on `lcalert` and 14 of them on the step build, neither of which has the
scale; the first cut's 13 of 19 failing on `lcalert` stands for the rest. opendbc Honda 159, sunnypilot Honda 132,
`test_car_interfaces` 270, safety Honda 1091, the integration script: all pass.

---

## 2026-10-12 — lane changes: `OP_SATURATED` is openpilot's own alert; the board's blinker ceiling stays unreported (lcalert)

The owner's decision of 2026-10-11, after the lane-change investigation: fix the alert on both sides now (this entry
is the openpilot half; the board's alarm is a board change), and raise the board's 50 % lane-change ceiling to 75 %
later, as its own image. **Nothing here changes `GW_BLINK_CEIL_PCT` or anything the board commands, and the CAN
bytes openpilot sends differ from `nsw-live`'s only in `0x500 OP_SATURATED`.** opendbc `lcalert`, sunnypilot `lcalert`
(from `nsw-live`).

**What the owner gets.** On some lane changes the dash lights the lane-departure lamp with a held chime: the board's
case (ii) escalation, `hud.c` `allow && (hard || ((op_saturated || steer_req) && solid) || ldw)`. openpilot itself
raised no steering alert on any lane change on 10f-121. Two causes, and this fixes the one openpilot owns:

* **`OP_SATURATED` was the raw timer.** While a turn signal is on, and 500 ms after, the board clamps the command to
  50 % of its authority (80 of 160 counts; `0x704 INH_BLINKER`). openpilot cannot see that, so on a lane change the
  output can pin at 1.0 (0.88 s on 120, 1.09 s on 121) and after 0.8 s `lac.saturated` sets. controlsd_ext sent
  `lac.saturated` as `0x500 OP_SATURATED`, and the board alarms on it. openpilot's own `steerSaturated` also needs no
  steering press in 2 s, desired lateral accel over 1.0 m/s² and undershooting: of 195 saturated frames on
  115/120/121, all were within 2 s of a press and none was over 1.0. 120 at 486.24 s and 121 at 330.31 s alarmed
  this way.
* Not this fix: 10f at 1318.23 s and 2047.85 s alarmed through the board's own `hard` rule (|command| at 90 % of
  authority for 300 ms) after a one-touch indicator lifted the ceiling mid-change. That is the board half, bundled
  here (below).

**`OP_SATURATED`** (sunnypilot `steer_saturated.py`, `selfdrived.py`, `controlsd_ext.py`; opendbc `hondacan.py`,
`structs.py`; `custom.capnp` `CarControlSP.LateralControl.steerSaturated @3`). selfdrived's `steerSaturated`
conditions moved, unchanged, into `SteerSaturatedAlert`; selfdrived calls it with its own `sm.frame`, and
controlsd_ext calls it with the same inputs (carState, `modelV2.action.desiredCurvature`, controlsd's curvature, the
lateral log) one frame earlier than selfdrived sees them. Its answer is `lateralControl.steerSaturated`, and that is
what `OP_SATURATED` now carries. It is the same decision as the `steerSaturated` event, not as the displayed
`STEERING_REQUIRED`: the event can be set while another alert outranks it. `saturated` is still logged as before.
Same bit, same position: the board needs no change to decode it.

**The blinker ceiling is NOT reported - tried and dropped on review.** lcalert's first commit (opendbc `3451d86c`)
also clipped carOutput's reported torque to ±0.5 while `0x704 INH_BLINKER` was set. Review found that this made the
ceiling "limited by safety" in controlsd, so `_check_saturation` counted down for as long as the blinker was on:
`lac.saturated` could not set, and neither could `steerSaturated` - openpilot's "Turn Exceeds Steering Limit" and,
from this branch on, `OP_SATURATED`. Blinker left on, or waiting for a gap in `preLaneChange`, in a highway curve,
hands off: the board gives half of what is asked, the car drifts out, and nothing would have warned. The clip also
added nothing to the alarm fix (`steerSaturated` alone is 0 on every logged lane change, below), and it was a second
copy of `GW_BLINK_CEIL_PCT` in opendbc that the 75 % image would have silently contradicted. opendbc `50509801`
removes it; the report is `last_torque` again (0 while the board is not actuating), exactly as on `nsw-live`.
`INH_BLINKER` is still decoded into `carStateSP.linbusGateway.blinkerCapped @28` (fresh frames only) and logged, so a
route shows when the cap was on; nothing acts on it. LKAS-GATEWAY-PROTOCOL.md 3.6 has the reasoning and what an
honest torque bar would need instead.

**Replayed** (closed loop: the real controlsd lateral step - `state_control`, `publish`, `run_ext` - against the real
`CarInterface` driven by the route's CAN, carOutput fed back from the simulated CarController; old = `nsw-live` /
`batch1` through the same harness, which reproduces the logged `lac.saturated` within 0-2 frames):

| route | `lac.saturated` frames | `0x500 OP_SATURATED` frames old → new (logged) | `0x0E4`, reported torque, integrator | selfdrived `steerSaturated` |
|---|---|---|---|---|
| 120 | 53, unchanged (487.14-487.69, all under `blinkerCapped`) | 5 → 0 (6) | identical on all 51 972 frames | 0 → 0 |
| 121 | 79, unchanged (330.57-331.35, all under `blinkerCapped`) | 8 → 0 (7) | identical on all 46 578 frames | 0 → 0 |
| 115 | 64, unchanged (925.57-926.21, not a lane change, wheel pressed) | 7 → 0 (7) | identical on all 100 983 frames | 0 → 0 |
| 10f | 1, unchanged | 0 → 0 (0) | identical on all 290 874 frames | 0 → 0 |

* `0x500` differs from the old code only in `OP_SATURATED` (and its checksum). Everything else openpilot computes or
  sends is bit-identical, so torqued, lagd and the torque bar see exactly what they saw on `nsw-live`.
* selfdrived's `steerSaturated`: none old or new on any of the four routes (none in the logs either), and the new
  helper agreed with a verbatim copy of the old block on every frame (45 523 + 41 050 + 89 454 + 257 246).

**Tests.** sunnypilot: new `test_steer_saturated.py` (9: each condition, the 2 s window, a 20 000-frame fuzz against
the old block, selfdrived calls the helper, controlsd_ext fills `steerSaturated` and 121 at 330.6 s does not set it);
`test_latcontrol_reported_torque.py` 11 (closed loop through the real Honda `CarController`, both torque controllers:
a railed request under the blinker ceiling still saturates exactly as without it, hands off in a curve it raises
`steerSaturated`, and `0x500 OP_SATURATED` follows it frame for frame; a raw saturation without the alert's conditions
is not sent); the seam test carries both new fields. opendbc: `test_elesys.py` 106 (`TestElesysBlinkerCeilingNotReported`,
2: report and wire under `blinkerCapped` are those of the uncapped run, not actuating still 0; and `INH_BLINKER`
through the real parser without clipping in `TestElesysReportedTorqueSeam`); the integration script's §7
(`OP_SATURATED` follows `steerSaturated`, not `saturated`) and §8 (`INH_BLINKER` decode, held inside the window,
cleared on staleness).

**The board half, bundled.** `eps_lkas_appslot.bin` is now board `686e4603` (branch `lc-alarm` on `c8221c29`, 49,664
bytes, SHA-256 `82fefd72c7e09ab43ef8c3f8b3f3388ef90b08ad03a826569c5a4e88b7f717f1`, `APL1` at `0x08004000`, flags
`0x04`). Its `hud_gw()` no longer counts `hard` while the blinker ceiling is in force (500 ms dwell included) or within
2 s of |driver torque| over 20 counts, and a gated tick restarts the 300 ms debounce; `op_saturated`, `steer_req`,
`ldw`, the command path and `GW_BLINK_CEIL_PCT` 50 are unchanged (board `HANDOFF.md` 0n). Replayed from the raw CAN
of 10f-121 it removes 10f's two lane-change `hard` alarms (and three non-lane-change ones within 0.33 s of a hand),
delays one by 0.41 s, and leaves 120/121's `op_saturated` pair to the change above. **The two halves go together:**
with `hard` gated, 115 at 925.06 s becomes a new `OP_SATURATED` edge on the old openpilot (`hard` used to stay true
across it), and this entry's `OP_SATURATED` is 0 on the whole of 115.

---

## 2026-10-09 — round 5 and the radar fix together (integration)

Round 5 (build A, the next entry down, with opendbc `b24613cf`: brake law v2 removed) and the radar fix (the two entries
below it: radard's clutter guard, and the `REL_SPEED` scale in opendbc `83c8b5b0`) are merged into one tree, with
opendbc carrying both. They touch different code. These are the places they share, and how each was checked:

* **The HUD's lead readers.** Round 5 rewrote the HUD (options A and C: the sign and next-limit settings are gone).
  The radar fix makes every `leadOne` reader except the MPC use `real_lead()`, so a held lead (`vLead` = `vEgo`,
  marked in `radarTrackId`) is not a car. Two readers survive round 5's rewrite: the right rail and the standstill
  banner's departure rule. Both still call `real_lead()`. The rail already had a held-lead test. The banner now has
  one too: a held lead moving off at 2 m/s leaves the prompt compact, and the same track without the mark brings the
  full prompt back. Swapping the banner's `real_lead()` for `present` fails it.
* **What the car is sent.** Routes 115 and 120 were replayed through the controls, with the route's settings and with
  the defaults. sendcan (251,014 and 129,327 frames), CarParams and CarParamsSP are byte-identical to the radar fix
  alone and to round 5 alone. carState is identical to the radar fix alone.
* **radard.** Routes 10f, 06 and c0 were replayed through radard and the planner. Every frame is identical to the
  radar fix alone: 58,437, 27,717 and 15,607 frames, of which 1,384, 480 and 336 have a held lead. aTarget,
  shouldStop, FCW and hasLead are included in that comparison.
* **Speed Limit Assist.** Round 5's motorway replay gives the same events, to the frame, as round 5 alone.
* **The screen.** The 191 HUD renders are pixel-identical to round 5's, and their 25 frame logs are identical. The
  logged routes carry no held lead, so the renders cannot show one. The banner's new test covers that case.

Three fixes for things CI would have refused. None of them changes what runs:

* `longitudinal_planner.py` and `tools/brake_route_check.py` have a shebang but lost their executable bit in the
  radar fix's commits, which `check_shebang_scripts_are_executable` refuses. The bit is restored. opendbc had the
  reverse problem: `test_elesys_radar.py` was executable with no shebang. That bit is now cleared.
* The radar guard's fixture `elesys_radar_guard_frames.json.gz` was 198 KB, over the 120 KB that
  `check_added_large_files` allows. It is now 104 KB and still holds the same seven windows and every frame. It is
  stored by column, with times in whole ms, the speeds and the model's leads as float32 text, and radar points as
  integers in the DBC's steps, written only on new radar updates. All 42,116 values of the 1,002 frames decode equal
  to the old file's. The guard's 26 tests pass, and every documented mutation still fails its test.
* codespell reads `assertIn` as a misspelling. The guard's odometer test now uses `assertEqual` on the whole moving
  set, which is stricter.

The test counts in `docs/fork/README.md` and the CAR doc are corrected (test_elesys 103, test_brake_route_check 28,
test_shadow_learn 31, opendbc 49 files and `FORK.md`).

## 2026-10-09 — round 5 (build A): a lower speed limit asks, sunnylink shows the NSW data, and the HUD announces the coming limit

Owner decisions 5, 3, 1, 1b, 2 and 0 (D0.1, D0.3, D0.4 and the lint hygiene) of round 5. Nothing here changes how the car follows,
brakes or steers, other than Speed Limit Assist no longer lowering the set speed by itself.

* **Speed Limit Assist: a lower limit is a prompt, never a silent rewrite (decision 5, area SL, non-PCM cruise).** With
  SLA active and the set speed at the limit, upstream asked only for a new limit below 80 km/h; 110 -> 100 and
  100 -> 90 rewrote the set speed with nothing but a 5 s chime alert, at the same two places on every M4 commute.
  `apply_confirm_speed_threshold` now also asks when `not pcm_op_long` and the new limit is below the set speed. A higher
  limit still raises the set speed by itself.
* **A new limit counts once it has held for 1 s (decision 5, every car).** `SpeedLimitChangeHold`
  (`speed_limit/change_hold.py`, fork-owned beside upstream's `speed_limit_assist.py`; upstream's unused
  `SPEED_LIMIT_CHANGED_HOLD_PERIOD`) sits between the resolver and SLA in
  plannerd, and what it holds is what `longitudinalPlanSP.speedLimit.resolver` publishes, so card (the set speed it
  writes, the button a prompt asks for), selfdrived's alert text and the UI see the same limit as SLA; otherwise card
  would rewrite on the raw change while SLA waited. Only a change from one limit to a different one waits (20 plannerd
  frames); the first limit, losing it and the same one coming back pass at once.
* **Replayed** on the logged motorway windows: the resolver output from the log -> the hold -> SLA -> card's
  `VCruiseHelperSP`, the driver's own button changes applied, everything else the replay's. On e6a18c839 the same
  harness reproduces every logged event to the frame (a check on the harness); on this build:

  | route, t | log (e6a18c839 and earlier) | this build |
  |---|---|---|
  | fc 812.94, 110 -> 100 | set speed 100, chime | prompt at 813.89, times out 818.89, set speed stays 110 |
  | fc 1337.94, 100 -> 90 | set speed 90 | prompt at 1338.89, set speed stays 100 |
  | 10f 771.07, 110 -> 100 | set speed 100 | prompt at 772.01, set speed stays 110 |
  | 10f 1172.06, 100 -> 90 | set speed 90 | prompt at 1173.01, set speed stays 100 |
  | c8 747.64, 110 -> 100 | set speed 100 | prompt at 748.59, set speed stays 110 |
  | c8 1156.65, 100 -> 90 | set speed 90 | prompt at 1157.60, set speed stays 100 |
  | dd 728.30 / 730.31 / 732.31, 110/100/110/100 | set speed 100, 110, 100 | prompt 729.25; the limit back at 110 confirms itself 731.26 (its chime); prompt 733.25; set speed stays 110 |
  | dd 1091.31, 100 -> 90 | set speed 90 | prompt at 1092.26, set speed stays 100 |
  | de 1793.69, 90 -> 100 (rising) | set speed 100 | set speed 100 at 1794.59, no prompt |
  | de 2178.68, 100 -> 110 (rising) | set speed 110 | set speed 110 at 2179.58, no prompt |

  **dd's flip-flop is not suppressed by the 1 s hold**: each value held 2.0 s, longer than the hold. What the change
  does there is keep the set speed at 110 throughout; the driver sees two prompts with a self-confirm between them. A
  hold longer than 2.0 s would suppress it; 1 s was the decision. No limit in the decoded windows flipped back within
  1 s, so on these routes the hold only delays each prompt and each rise by 1 s.
* **sunnylink: the NSW data (decision 3, area NSW).** A new param `NswZonesStatus` (PERSISTENT, STRING, not BACKUP)
  holds one line, the last check's result and the device's time, e.g. `updated to 2026-10-04 · 2026-10-09 18:02 AEDT`.
  The downloader writes it when a check ends (weekly or asked for) and when a request is refused, never for progress,
  inside a try; it bumps `ParamsVersion` after it (blocking: two reports are two bumps), and when it clears a request
  it has taken. sunnylinkd's own `ParamsVersion` bump never worked on this device - it wrote a str to the INT param and
  the `TypeError` was swallowed (routes 115, 120, 121 have no `ParamsVersion` in `initData`) - and now writes the INT
  (`FORK(NSW-ZONES)`, an upstream bug). A request while driving is refused, `offroad only`, and not queued (D3.1). In the app, Cruise
  has its own "NSW Speed Zones" section (D3.2): the mode, Update NSW Speed Zones Now, the weekly toggle, and info rows
  "Installed NSW data" (`NswZonesVersion`) and "Last NSW check" (`NswZonesStatus`), both in `BLOCKED_PARAMS`. Every
  route's `initData` now carries the last check's result. That the app re-reads on `ParamsVersion` is still to be
  confirmed on the phone; until then the app's text promises only that the switch and the result show the next time
  it loads the settings.
* **The comma 4 HUD, option A (decision 1, area HUD).** The live speed, the speed-limit sign (with its school-zone
  lamps and electronic-sign styling) and the next-lower-limit row are gone from the top right, with their five settings
  and `HudNextLimit`; the stop timer stays, in the corner where it was drawn with the sign off. Moving, the top right is
  empty: on routes mw/stopped/tunnel the new build with Approaching Limit off is pixel-identical to e6a18c839 with
  Current Speed, Speed Limit Sign and Next Lower Limit off. Nothing on screen marks a school zone now (D1.3, accepted);
  a school 40 still raises the confirm prompt. Four things the removal would have broken are kept: the standstill
  banner is told every frame what the alert renderer draws (`StandstillBanner.observe()`, now in
  `HudRendererSP._update_state`), the cluster still hands the alert renderer to the HUD renderer, and the next-limit
  data rules live on in the coming limit (below). The confirm pill's 'limit 50 +5' note and the banner's pending sign
  now always show (there is no sign elsewhere) - today's sign-off behavior. A test fails if a removed key is read
  anywhere under `openpilot/`.
* **The confirm box flashes, then its dashes march (decision 1b, V3).** From the frame the dashed box is half visible
  its outline flashes solid twice (0.18 s lit, 0.18 s dashed: 2.8 a second, under 3 Hz), then the dashes walk
  clockwise at 30 px/s while the prompt waits. Only the outline moves: in the render test the digits are identical,
  frame by frame, to a still box. `hud_draw.dashed_rrect(phase=)` and `hud_model.box_outline()`. A critical alert still
  takes the screen on its next frame, mid-flash included. A new prompt flashes again - also one raised while the last
  box is still fading out, as on route dd's flip-flop - and so does a target or key changing under one.
* **The coming limit in the MAX number's place (decision 2, option C; new setting Approaching Limit,
  `HudApproachingLimit`, BOOL "1").** A limit lower OR higher than the one in force, from mapd's published look-ahead,
  once within 15 s or 500 m: the limit in the MAX font at 64 px in a dashed box, a down or up chevron, and an
  'in 300 m' pill that drains ('Near', under 50 m, drawn 'now'; the distance only counts down, unless mapd's figure
  comes back more than a second's run-down + 10 m higher, which is news). When the look-ahead ends AT the change (the
  distance left, run down by the car's speed, within 2 s of travel or under 50 m) it is held - 'now' - until the
  resolver's limit becomes it (3 s at most), then hands over in the same place: to the confirm box, or cross-fading
  into the stock MAX number. Lost further out (a missed 1 Hz message, the window shrinking as the car slows) it stays a
  distance, running down, for 2 s at most - never a jump to 'now'. It waits while the stock MAX number shows (a 'set
  speed changed' alert holds it back only that long, 2.5 s, not the alert's 5 s), is cut on the frame by any other
  alert, and counts as a top icon, so the driver-monitoring face makes way for it (about 13 s on the M4 approach,
  762.1-774.75).
  Never at a standstill, for a car-sourced limit, or in a tunnel (dead reckoning, or GPS still lost after it).
  Rendered over route 10f: the M4 110 -> 100 is announced from 762.1 ('in 300 m') to 774.7 and hands over to MAX 100
  at 774.75 as logged; with this build's Speed Limit Assist (the 100 counting 0.95 s later, then 'press -') it is held
  'now' 774.7-775.65 and the box takes the place at 775.7 - no frame with nothing, also with the prompt reaching the
  UI 1, 2 or 3 frames after the new limit (the coming limit fades while the box comes; the face never shows). Bringelly
  Rd's 50 -> 70, never shown before, is announced from 187.8 - as soon as the stock MAX number from the earlier confirm
  has gone - with the up chevron; braking for the red light there, 'now' shows only under mapd's own 50 m and for the
  3 s hold after its look-ahead went (196.7-199.7), and mapd's 65 m at 199.7 is drawn 'in 70 m' (fix round 1: before,
  '70 now' stood from 194.2 to 202.2 for a limit that never came). The M4 East tunnel shows nothing, also with mapd made
  to publish a limit ahead there, and a held 'now' ends on the frame dead reckoning starts. School and variable zones
  ahead are phase 2 (they need two new `liveMapDataSP` fields).
* **The old full-screen confirm drawing is gone (decision 0, D0.3).** `HudConfirmLimit`, `draw_pending_limit()`,
  `pending_icon()` and the `_draw_icons` hunk in `alert_renderer.py`: with Compact Speed Limit Prompts off the confirm is
  sunnypilot's own alert, pixel for pixel.
* **Settings (sunnylink Visuals > HUD):** eight toggles now - Stop Timer, Compact Take Control Banner When Stopped,
  Approaching Limit, Compact Speed Limit Prompts, Compact Disengage Notices, Compact Turn Notices, Planned Stop, Curve
  Speed. Removed params: `HudSpeedCluster`, `HudCurrentSpeed`, `HudLimitSign`, `HudSchoolZoneCue`,
  `HudVariableLimitSign`, `HudNextLimit`, `HudConfirmLimit` (a stored value on the device is left unread).
* **Watch on the next drive:** the M4 eastbound 110 -> 100 - the 100 top left from ~400 m with the bar draining, 'now'
  at the sign, then the flashing 'press -' box (or MAX 100); that the face's absence for those ~13 s does not bother
  you; that the box's march is not distracting on a long prompt (V2, flash only, is a one-line change). And two older
  rules that now come up at every motorway drop, not only below 80 km/h: while the 'press -' box is up, **+ does
  nothing** (the whole 5 s); and a **- pressed within about a second after the box has gone still confirms** - 110 to
  100 in one press. After that second a - is an ordinary 1 km/h step.
* **Tests.** `test_speed_limit_drop_prompt.py` (new, 19: card + selfdrived + the hold + SLA; seven of them fail with
  the old rule), the `Drive` harness in `test_speed_limit_map_strict.py` now has the hold; `test_downloader.py`
  `TestSunnylinkReport`; `test_settings_changes.py` `TestMapDataControls` for the new section. HUD:
  `test_hud_cluster.py` (the timer, `ApproachState` - window, both directions, the hold only once the change is
  reached and its five ends, a gap far out as a running distance, the window shrinking, route 10f's red light second by
  second, tunnels, car-sourced limits, the count-down and the jump it takes - `box_outline()`, the frame's alert
  looked up once a frame, the eight settings, `TestRetiredKeys`, the markers) and `test_hud_render.py` (rewritten: the
  top right empty while moving, the coming limit and its hand-overs to the box and to MAX, a missed look-ahead far out,
  the outline strip against a still box, a second prompt and a new target flashing again, a critical alert
  mid-flash, every setting off = stock); `test_sunnylinkd.py` and `test_downloader.py` write `ParamsVersion` and
  `NswZonesStatus` through the real params library.
* **Brake law v2 is gone (decision 0, D0.1).** "Measured brake law (testing)" (`HondaElesysBrakeLawV2`, batch 3) failed
  acceptance and never ran in the car. Deleted: `elesys_brake.py` and its 29 tests, its three call sites in
  `carcontroller.py`, `elesys_pedal_v2_window()` and the tuner's brake-gain hold (git keeps them: opendbc `8f844a79`,
  `41993458`), with its toggle on the big UI, the mici vehicle page and sunnylink, and its place in
  `initialize_params()`, `OFFROAD_ONLY_PARAMS` and statsd. The key is no longer registered: a stored value stays on the
  device unread, and a sunnylink restore skips it. CarParamsSP flag 32 is reserved like 16 - never set, ignored by the
  controller - and the `blaw=` tag on `hondadyn`/`hondashadow` lines always says `v1`. Gas law v1 and pump v5 stay as
  switch-backs (D0.2). **Proof:** through the real `CarInterface` on routes 10f, 115 and 120, CarParams, CarParamsSP and
  every sendcan frame are byte-identical to e6a18c839 with the setting absent and with it 0 (CAR doc 7.10).
* **The comma 3X Cruise panel's Honda toggle is gone (D0.4).** `layouts/settings/cruise.py` is upstream's file again
  (four hunks, 35 lines). `HondaDynamicTuningEnabled` itself is unchanged and is still set from Settings > Vehicle >
  Honda, the mici vehicle page and sunnylink.
* **Lint (decision 0, hygiene).** `ruff check openpilot` is clean (22 findings before): `card.py`'s unused
  `import json`, unused `noqa` comments and implicit string joins in `eps_lkas_flasher.py` / `eps_lkas_hook.py`, and
  `zip(..., strict=False)`, which behaves as before. The four `time.time()` calls ruff bans are wall-clock on purpose (a
  timestamp kept across boots and compared on a later one, a file name), so they carry `# noqa: TID251` with the reason
  rather than becoming `time.monotonic()`. `board.py`'s docstring now names `stage_board_firmware()` and
  `write_board_firmware()`.
* **Tests (part 3).** `test_elesys.py` `test_the_retired_brake_law_flag_runs_todays_law` (flag 32 sends today's 0x1FA,
  with either pump rule and with the retired C1 flag; flags 32 and 64 keep their numbers - it absorbs the older
  `test_brake_law_flag_alone_leaves_the_pump_on_v5`), `shadow_learn.brake_law_tag()` a plain `'-'` / `'v1'`; `test_elesys_pump_brake_flags.py` and `test_honda_elesys_pump_brake.py` cover the one pump
  flag and both retired keys (not registered, not handed over, their files set nothing); in
  `test_honda_dynamic_settings.py`, `test_retired_keys_are_gone_everywhere` also fails on `HondaElesysBrakeLawV2`,
  `BRAKE_LAW_V2_PARAM`, `brake_law_v2` or `elesys_brake` (`carcontroller.py` included), and
  `test_the_toggle_lives_on_the_honda_panel_only` replaces the two-panel check.

## 2026-10-09 — radard's clutter guard for the true radar speed

The scale fix (next entry down) makes a stationary radar return read as stopped, and two upstream paths in radard can
make such a return the lead below about 36 km/h ("What can get worse" there). radard now has a guard against that, on
`HONDA_ELESYS` only: `CarParams.brand` is `honda` and the fingerprint is in `HONDA_ELESYS`. Every other car runs
upstream's radard unchanged. The guard is `openpilot/sunnypilot/selfdrive/controls/lib/elesys_radar_guard.py`, called
from the `FORK(HONDA_ACCORD_9G_AU)` places in `openpilot/selfdrive/controls/radard.py`. It is a separate change from
the scale fix, in four commits: the guard; its review fixes (moving tracks, the camera hysteresis, the give-ups
written down); a second review round (a track counts as moving only when its own range agrees, counted in radar
updates, and the hold); and a third (the held lead marked, so that only the MPC sees it, what the hold does at
walking pace written down, and a non-finite vEgo). Nothing has reached the car.

* **The camera match.** A radar track slower than 3 m/s may stand for the camera's lead only if the camera does not
  say that lead is faster than the track by more than a tolerance. The tolerance is 3 m/s up to 20 m of camera range,
  then grows 0.15 m/s per meter, up to upstream's 10 m/s from 67 m. Tracks at 3 m/s or more, and any track faster
  than the camera's lead, are left to upstream: the guard only removes leads that would brake harder than the camera
  asks. Disagreeing tracks are removed before radard picks its match, so a track that agrees can still be matched. If
  none agrees, the lead is the camera's.
* **The low-speed override** (below 4 m/s) takes a **moving** track exactly as upstream does. Moving is counted in
  radar updates (the radar reports at 10 Hz; radard runs at 20 Hz and sees every reading twice, so the first version's
  "5 frames" was 2.5 readings) and must be backed by the track's own range: its point in the world (range plus the
  car's travel) must move away at 0.7 m/s or more, and at least half as fast as the radar says. A track is latched as
  moving, for the rest of its life (a car that stops is still a car), once its raw speed has read 1 m/s or more for 3
  radar updates in a row and 0.5 s of its range agrees. It is moving now while its filtered speed `vLeadK` has read
  1.5 m/s and its raw speed 1 m/s for 2 updates and its range agrees; a track too new to have 0.3 s of range (a
  cut-in) is taken on its speed alone. Only moving away counts. Any other track the override takes only when radard
  has a confident camera lead and the track is within 1.5 m of that lead's distance or within 1 m/s of its speed.
  "Confident" has hysteresis: radard's filtered prob must rise above 0.55 and stays confident until it falls to
  radard's own 0.5, so a single frame just over 0.5 does not switch it on. Otherwise the lead is what the camera path
  gave: the camera's own lead, or none.
* **The hold.** When that leaves radard with no lead at all, the closest in-path track the override refused, once
  seen for 5 radar updates, still stops the plan accelerating toward it. It becomes a lead at the car's own speed,
  not accelerating, at the MPC's desired distance for that speed on its shortest time gap (6 m + 1.25 s x vEgo, from
  `long_mpc`). The planner takes the lower of the MPC and e2e, so the MPC holds the plan at about 0 (aggressive) or
  eases off slightly (standard, relaxed); it does not brake for the object as for a stopped car. Its distance is not
  the track's range, and its `modelProb` is 0, so it raises no FCW. **Below the car's stopping speed (0.8 m/s)
  holding speed is a stop**: the planner asks to stop and, from standstill, does not pull away while the track stays
  held, whatever its range (CAR doc 5.3). **A held lead is a lead for the MPC only.** It is marked (`radarTrackId` =
  -2 - the track's id, `held_lead.py`), and every other reader of `leadOne` skips it. These are DEC, `hasLead` (the
  dash's lead icon, 0x500, shadow_learn), the e2e alerts, the onroad chevrons and path, the developer UI, the comma
  4 rail and `brake_route_check.py`. So during a hold the dash shows no lead.
* **Stopped cars are kept.** A stopped car that the camera also sees as stopped or slow is matched as before, at any
  range. The camera's lag on a stopped queue far ahead is inside the tolerance. On route c0, track 809 runs from 117 m
  to 5 m while the camera reads it at 8-12 m/s beyond 65 m; every frame upstream matched to it is unchanged.

* **Why these numbers.** radard was rerun on 14 routes: 01, 06, 07, 08, 10, all of 14, c0, c8, c9, 10f, 113, 115,
  120 and 121, 479,301 frames. A stationary track counts as clutter when the car drove past its point within 5 s and
  the track never moved.
  - **Moving radar matches, camera faster than radar, 99th percentile:** 1.6 m/s within 20 m, 2.0 at 20-30 m, 2.3
    at 30-45 m, 4.2 at 45-60 m, 5.0 at 60-80 m. The tolerance is above that at every range.
  - **Clutter matches:** the camera is faster by a median 3.6 m/s at 10-20 m and 7.6-8.1 m/s at 20-45 m. There are 3
    clutter frames beyond 45 m.
  - **Stationary tracks that are not clutter, 99th percentile:** 2.4 m/s within 20 m, then 5.7-9.8 m/s at 20-80 m,
    because the camera is slow to see that traffic has stopped. That is why the tolerance grows with range and
    reaches upstream's 10 m/s. Between 20 and 67 m it deliberately stays below that 99th percentile, because the
    clutter's median is in the same place: a stopped car there that the camera still reads as fast goes to the
    camera's lagging lead, roughly as with the old half-scale decode. In the replay below that costs 0.9 s of radar
    lead on a real object engaged at 4 m/s and over (2.7 s in all), and no engaged stop brakes later than the old
    decode for it.
  - **Not "a few vStd".** On the current model (routes 10f-121) `leadsV3` `vStd` and `xStd` read up to 59874, so a
    tolerance in vStd would pass everything. The tolerance is in m/s.
  - **The override.** Without a confident camera lead it took a track on 9,056 frames, 56% of them clutter. With one,
    a track within 1.5 m of the camera lead's distance is clutter on 2-9% of frames, and one within 1 m/s of its
    speed on 2%. At 1.5-2 m/s that rises to 27%.
  - **Moving.** Of 548 tracks that latch as moving with the car under 4 m/s (third commit), the track's world speed
    over 0.75 s either side of the latch is 0.7 m/s or more on 533 and under 0.3 m/s on 5, all 30-59 m away and at
    least 1.1 m off the path. A plain per-frame `vLead >= 1` test is worse: it flickers on vehicles creeping at about
    1 m/s, and it takes noise spikes on stationary tracks. A count of speed readings alone is not enough either: route
    07 t=1128.23-1128.78, track 1796, a return that appeared 0.2 s earlier at 8.8 m, read 3.7-3.9 m/s for six radar
    updates while its point in the world stayed within 11755.0-11755.9 m, and the car later drove through that point.
    The second version latched it and the replay braked to -3.5 for 1 s (disengaged). Its range said 1.4 m/s against
    the radar's 3.9, so the half-the-radar-speed test rejects it. Route 120 t=17.7 (track 74: 0.1, then 2.0 for
    three readings, then 0.2 m/s) does not latch either.

* **Through radard** on the same routes, guard off against guard on:
  - radar leads counted as clutter: 6,186 frames → 801 (engaged 310 → 121);
  - radar leads on moving tracks handed to the camera: 214 of 213,707 frames (0.1%);
  - radar/camera switches of the lead: 6,323 → 6,102.

* **The plan replay.** Each tree's `RadarInterface`, radard and `LongitudinalPlanner` ran on the logged model and car
  state (`replay_plan.py`, the replay used for the scale fix), three ways: the old decode, the true scale without the
  guard, and the true scale with it. 13 routes (121 has almost no leads), 210 min engaged.
  - **FCW frames:** 0 / 3 (c8 t=2321.1, disengaged) / 0.
  - **Engaged episodes with `aTarget` at or below -2 and at least 1 m/s² below the old decode:** 6 without the guard,
    3 with it. The 3 left are the same with and without the guard, and all are real moving leads: route 10 t=111.2
    (4.7 m/s), route 14 t=2325.4 (6.5 m/s) and t=2906.0 (17.1 m/s). Engaged or not: 226 → 96.
  - **The named events:**

    | event | true scale, no guard | guard | logged (old decode) |
    |---|---|---|---|
    | route 14 t=194.3-195.4, 7.8 m/s | -2.75 | +0.96 to -0.42, on the camera's lead at 8 m/s | -0.2 to -2.3 |
    | route 06 t=1209.5, 3.5 m/s | -2.14 | -0.38 to +0.43 (driver on the gas at t=1210.1) | -1.4 |
    | c9 t=2379.0, 8.4 m/s | -3.34 to -3.50 | +0.39 to +0.48 | -3.0 to -3.2 |
    | c8 t=2320.8-2321.1, 7 m/s, disengaged | -3.50 and an FCW | -0.9 to +0.1 | -2.5 to -2.8 |

    c9 and c8 are false brakes that the logged plan already had, so the guard also removes phantoms the car has
    today.
  - **Moving vehicles at walking pace are kept.** Below 4 m/s, a moving radar lead that the true scale uses and the
    guard does not: 5.4 s in all, 0.45 s engaged (29.3 s and 1.5 s before the moving-track rule). The plan matches
    the true scale's on c9 t=288.8-289.3 (a car in the lane at 8.8 m doing 2.1 m/s, closer than the camera's lead:
    -1.26, it was +0.23 to -0.18), 08 t=1207.8 (a car at 3.8 m closing at 2.4 m/s: -1.85, it was -0.68) and 10f
    t=2757.2-2757.5 (a vehicle creeping 4 m ahead that the camera rates 0.4-0.5). Not taken: c8 t=2829.9, a track
    that sat still for 1.5 s, jumped 1.1 m and read 1.5 m/s for two radar frames, at a point the car then drove
    through; and the frames before a creeping vehicle has read 1 m/s for 0.25 s (below). Disengaged, the second
    version braked harder than the first in 9 episodes, 6 of them a single frame. That review said "all on a track
    that had been seen moving"; that was wrong for one of them: route 07 track 1796 (above) never moved. It, c9
    t=180.1 (track 611) and 10f t=2800.96 (track 6716) were the latched-clutter episodes the next review found.
  - **Flicker.** The camera hysteresis changes 18 frames on the 14 routes, none engaged. The one-frame dips the review
    found at route 06 t=1265.5 and t=1303.45 come from radard's own camera match (upstream's prob > 0.5, on a slow
    track the camera agrees with), and route 14 t=195.04 is the camera's own lead; the guard does not change them.
    Disengaged one-frame dips of 1.5 m/s² or more: 62 with the first version of the guard, 65 now.
  - **What the scale fix gained is kept.** The phantoms it removed at speed (route 120 t=116.6, 10f t=2065) stay
    removed. The c0 approach to a stopped queue is identical, frame for frame, except one frame at t=253.96, where
    another stationary track of the queue at 68.8 m is matched.
  - **Engaged time on a stationary radar lead at 0.3-4 m/s:** old 77 s, true scale 161 s, guard 113 s. On 98.0% of
    the guard's frames the camera has a confident lead within 3 m of it, under 1.5 m/s: these are stops behind
    stopped cars.
  - **Stops behind a car from above 3 m/s:** 88, 36 of them mostly engaged. On those 36 the plan's -0.5 and -1.0
    m/s² onsets are never later than the old decode's, and its firmest value is never more than 0.06 m/s² softer.
    That count leaves out the stop at route 06 ts=1312.4, which is the night case below: its approach was classed
    as mostly disengaged because the driver took over, but all 19 of its frames that are softer than the old decode
    were engaged, the worst by 1.41 m/s². Route 10 ts=443.0 has 12 engaged frames up to 0.48 softer; the true scale
    without the guard is the same there.
  - **Unchanged elsewhere.** Engaged `aTarget` is identical with and without the guard on 98.7% of frames. Closing on
    a slower radar lead, it is within 0.2 m/s² on 98.0%; the rest are the frames in the next bullet.

* **The third commit, replayed** the same way (13 routes, 210 min engaged; against the second version):
  - **FCW:** 0. **Engaged episodes at or below -2 and 1 below the old decode:** the same 3. Engaged or not: 96 -> 73.
  - **Route 06 t=1307.0-1309.35** (the night case below), engaged: the plan's maximum is -0.01, it was +1.00; over
    t=1306.35-1309.3 it stays at -0.53 to -0.01. **t=1309.5-1310.3**, when track 2652 starts to move: -0.17, it was
    +0.96 (the planner had been seeing no lead the frame before; now it has been seeing the held one).
  - **Engaged, the plan at +0.2 or more where the old decode brakes, by 0.5 or more**, on a radar lead the true scale
    has, below 4 m/s: on objects the car stopped short of 2.4 s -> 0.2 s; on clutter it drove through 6.45 -> 0.35 s.
    The review's count of the same (radar leads the guard dropped): 2.15 s -> 0, and its engaged episodes where the
    plan sits 0.5 or more above the old decode on such a lead: 12 -> 1 (route 06 t=1265.2, two frames, clutter).
  - **The hold** ran 44.7 s engaged: 30.7 s on clutter the car drove through, 9.85 s on objects it stopped short of,
    3.8 s on tracks that moved later. Engaged, the plan is 0.5 m/s^2 or more firmer than the second version on 7.3 s,
    never by 1 to below -1, at worst -0.83 (route 06 t=1265.5, clutter, where the old decode braked to -0.78 and the
    true scale to -1.28), and it is never 0.5 or more softer. Near standstill what decides a launch is the stop
    request (`shouldStop`), not `aTarget`, and the hold asks to stop below 0.8 m/s. Engaged, it adds 0.8 s of stop
    requests where the second version had none. These are route 07 t=1241.9-1242.0 at 0.4-0.5 m/s and route 08
    t=72.5-73.1 from 0.8 m/s down to the stop, and the old decode asked for the same stop on every one of those
    frames. At standstill, where the second version would have launched, the hold keeps the car stopped on one frame
    (0.05 s, route 08); over all 4,015 s of standstill the hold ran 0.35 s. (The figure this line
    gave before, 0 s with the plan at 0.05 or less where the second version gave over 0.2, measured `aTarget`, which
    does not gate a launch.)
  - **Moving leads:** below 4 m/s, a radar lead reading 1 m/s or more that the true scale uses and the guard does not
    take: 5.4 s -> 9.55 s, engaged 0.45 -> 0.55 s. The range test refuses more of them, not fewer. (This line said
    4.7 s and 0 before, because it counted a held lead of the same track as taken; a held lead's speed and distance
    are made up.) 5.05 s of the 9.55 are held, including all 0.55 s engaged. Part of it is intended: spike tracks such
    as 07's 1796 and 120's 74 read as moving. Engaged, the plan there is never softer than the second version's.
    Route 06 t=1209.7 plans -0.65 against +0.26 (old decode -1.1), route 120 t=17.7 +0.51 against +0.65, and the other
    three episodes are identical.
    Route 07 t=1128.2-1130.1: -0.36, it was -3.50. c9 t=180.0-180.3: -0.42, it was -2.18 (track
    611 no longer latches). 10f t=2800.8-2801.2 stays at -2.09: track 6716's own range moves
    forward 1.3 m in 1 s, so by every test here it was moving; the car then drove through its point.
  - **c9 t=288.79**, the car in the lane: taken from its second radar update, 0.1 s later than the true scale.

* **The fourth commit, replayed** the same way (13 routes, 469,942 frames, against the third commit):
  - **The plan does not change.** `aTarget`, `shouldStop`, FCW and every `leadOne` field but `radarTrackId` are
    identical on every frame. `radarTrackId` carries the mark on exactly the 7,768 held frames (388.4 s, 44.7 s
    engaged), each as -2 - the held track's id.
  - **`hasLead`** (the dash's lead icon) is off on those frames: 44.6 s less lead shown engaged. It toggles 2,705
    times (third commit 3,043, second 2,737, old decode 3,121).
  - **DEC, what-if** (sunnypilot's real `DynamicExperimentalController` over the 13 routes, fed each version's
    `leadOne`; DEC was off on every frame of them). With the third commit DEC would sit in ACC where the second
    version is blended on 26.65 s engaged. On 2.1 s of that its plan is 0.5 m/s² or more above the second version's,
    and on 0.8 s of that the second version brakes: route 08 t=71.5-71.8, an e2e stop from 4 m/s with tracks at 16-20 m
    in the path, gives +0.47 against -0.84. Closed loop, the car would roll on toward the stop at walking pace. With
    the mark DEC skips the held lead: 0 s in ACC where the second version is blended, 0 s with its plan 0.5 or more
    above the second version's, and its mode differs from the second version's on 0.25 s engaged.
  - **A non-finite `vEgo`** no longer poisons the guard's odometer. It used to stay NaN for the rest of the drive,
    and then no track latched as moving (found by the review's fuzzer).

* **What the guard gives up.** The camera is the only thing in this data that tells a stationary return from a real
  stationary object (track age, distance jumps and range-rate residual all overlap between the two). So below 4 m/s
  a stationary object that only the radar sees no longer stops the car: since the third commit the guard holds the
  plan for it (no acceleration toward it), and the stop rests on the camera, e2e and the driver. Engaged, the guard drops about as much real-object radar lead as clutter at walking pace, and **the losses
  fall mostly at night**: real objects 7.1 s per engaged hour on night routes against 3.1 s/h in daylight (clutter
  8.9 against 0.7 s/h). 9 engaged episodes plan at least 0.5 m/s² softer than the true scale on a real object.
  - **Route 06 t=1306.3-1309.8, engaged, at night, 2.6-3.4 m/s: the worst case.** Something wide and stopped in the
    path (tracks 2630, 2633 and 2634 side by side, 20 m closing to 13 m), and the camera reads junk at prob 0.03-0.45.
    The old and true-scale plans brake to -0.6; with the first two versions of the guard the plan held +0.4 to +1.0
    m/s² for about 3 s. **Once the car is actually driving on that, it is worse than the open-loop replay shows**:
    about 1 s of +1.0 takes the car from 3.4 to over 4 m/s, and above 4 m/s there is no low-speed override, so radard
    has no lead for a radar-only stationary object at all. The hold (third commit) stops that: the plan stays at -0.5
    to 0.0. It still does not stop for the vehicle. Track 2652 then starts moving off at 2 m/s and the guard takes it
    from t=1309.8. The driver stopped about 6 m behind the vehicle; the camera only found a stopped lead at 5.6 m once
    the car had stopped.
  - **Route 07 t=1240.7, 1.4 m/s.** A stationary return at 6 m, which the camera saw at the same distance with prob
    0.13-0.17. The old and the true-scale plans brake to -0.4/-0.6, and the logged car stopped. With the guard the
    plan holds +0.4 and falls to -0.27 over 1.2 s, as e2e brakes; then the driver took over.
  - **Route 06 t=1209.4-1210.0.** A car 7 m ahead that reads stationary until t=1209.7, then moves off at 2.2 m/s;
    camera prob 0.29-0.53. Old -0.5 to -1.1; with the guard -0.4 to +0.4 until it takes the moving track at
    t=1209.9. In the log, the driver pressed the gas there.
  - **In daylight, a vehicle creeping close ahead.** At walking pace this radar reads a vehicle creeping at about
    1.5 m/s as 0.3-1.2 m/s, so until a track has read 1 m/s for three radar updates, with its range agreeing, it
    counts as stationary and needs the camera. Route 10f t=2756.4-2757.2: a vehicle 4-5 m ahead, camera prob
    0.27-0.48; the true scale brakes to -1.5/-2.0, the second version had no lead (+0.6 to +0.9), the hold gives +0.3 to
    +0.45; from t=2757.25 the guard takes track 6567. Route 10f t=2851-2853 (a vehicle 5-8 m ahead reading 0.5-1.0
    m/s, which then drives off) is never taken as moving.
  - **A vehicle coming toward the car** (reversing toward it, rolling back on a hill, or oncoming in the path on a
    curve) never counts as moving; with the camera unsure it is only held, where upstream brakes. Route 14 t=145.5,
    disengaged: track 59 at 11.3 m reading -1.55 m/s, old -3.46, true scale -3.50, guard -0.97. Latching such tracks
    on the same range test was tried and dropped: it admitted clutter (route c8 t=2800.2, a return at 2.6 m that read
    -1.2 m/s for three updates as its range jumped, then sat still: -3.5 for 0.85 s, disengaged, even when limited to
    tracks closing at 2 m/s or less; without that limit, six more disengaged one-frame harsh brakes on tracks closing
    at 2.6-3.9 m/s), and it gained nothing engaged (0.1 s of engaged radar lead, with no plan change).

* **How to judge it.** On the first drive with stop-and-go traffic:
  - the firm false braking at 10-36 km/h and the FCWs in town should be gone;
  - **in night crawl and very close stop-and-go, cover the brake.** The failure to watch for is the car pulling
    forward, or not slowing, with a vehicle close ahead, most likely in the dark or when the vehicle ahead is
    creeping. With the hold the car should no longer speed up toward something only the radar sees, but it does not
    slow for it either. Brake, and note the time;
  - replay that route through `replay_plan.py`: the stationary radar lead at walking pace should come with a confident
    camera lead at the same distance, and a moving radar lead should be taken as upstream takes it.

  The scale fix's interim guidance (next entry down) still applies until such a drive: no engaged driving in car
  parks or driveways, and engaged stop-and-go and close follow are unvalidated.

* **Tests.** `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_elesys_radar_guard.py` (new, 26 tests) uses
  logged frames in `fixtures/elesys_radar_guard_frames.json.gz`: 10f t=2737-2746 and t=2753-2758, c8 t=2318-2322.5,
  c0 t=249-266, c9 t=286.5-289.4, 07 t=1126-1130.2 and 06 t=1303.5-1311. Each frame says whether it carries a new
  radar update. Each window goes through radard with the guard on and off.
  - **10f, near-range returns at walking pace:** without the guard every frame of t=2740.25-2745.55 is a stationary
    radar lead, more than 40 of them under 6 m. With it, while the camera is not confident the returns are only
    held, and the 5 radar frames left agree with a slow camera lead and are farther away than it.
  - **c8, the false-FCW frames:** the camera's lead at over 5 m/s instead of the stationary tracks. The real car that
    cuts in at t=2321.19 (track 5751, 7.8-8.0 m/s) is matched with and without the guard.
  - **c0, the stop behind a stopped queue:** every frame upstream matched to track 809, from 117 m to the stop at
    5 m, is unchanged. That includes the frames where the camera lags at 8-12 m/s, and below 4 m/s through the
    low-speed override.
  - **10f, a vehicle creeping 4 m ahead:** t=2757.25-2757.47, with the camera under 0.5, the lead is moving track 6567,
    as without the guard. Also pinned: before the track has been seen moving it is only held.
  - **c9, a car in the lane closer than the camera's lead:** track 946 at 8-8.8 m doing 2.1 m/s while the camera is
    sure of a car 9-20 m ahead; from t=288.89, its second radar update, the same leadOne as without the guard.
  - **07, a speed spike on a stationary return:** t=1128.2-1129.6, track 1796 never counts as moving; it is only held.
    Without the guard it is the lead at 3.7-3.9 m/s.
  - **06 at night, the known give-up:** t=1306.35-1309.30 the held lead with the guard, a stationary radar lead without
    it; then moving track 2652 with both. If a change brings the radar's stop back here, check route 10f's near-range
    clutter too.
  - **Unit tests:** the gate (`HONDA_ACCORD_9G_AU` only, not another Honda or another brand), the constants against
    radard's, `DT_MDL` and `long_mpc`'s, the tolerance at each range, the one-sided check, the override's conditions,
    the camera hysteresis (route 06 t=1303.45's single frame at 0.53), counting in radar updates, the range-backed
    latch and its reset, a speed spike whose range does not follow (routes 07 and 120), a cut-in taken after two
    updates, a track coming toward the car (not moving), and the hold (its values, and only with no other lead).
    Fourth commit: the hold through the real MPC, on every personality at 0-3.9 m/s (within 0.05 of 0; a stop
    request exactly below 0.8 m/s); the mark; `hasLead` through the real planner's `publish()` and DEC both skipping
    a held lead; a NaN and an inf `vEgo`. `test_hud_cluster.py` (the rail's planned stop) and `test_brake_route_check.py`
    (28: a held lead is no stopped car) pin those two consumers.

  The 10f near-range and c8 tests fail with the guard off. The creeping-10f and c9 tests fail on the first version
  of the guard (no moving-track rule). The 06, 07 and both 10f windows fail without the hold; the 07 window and the
  spike unit test fail without the range test. The c0 test passes either way: it pins that the guard changes
  nothing there. Without the mark, 9 guard tests fail. Without its `real_lead()`, the DEC test, the `hasLead` test
  and the `brake_route_check` test each fail. Without the finite check, the odometer test fails.

Under the hood: no opendbc change; the submodule stays at `83c8b5b0`. The guard's thresholds and the data behind them
are in the module docstring. The mark is `openpilot/sunnypilot/selfdrive/controls/lib/held_lead.py`; its readers are
listed in CAR doc 5.3. Docs: CAR doc 4.8 and 5.3, `docs/fork/README.md`.

## 2026-10-09 — the radar's relative speed was read at half its value

The Elesys radar's track speed (`REL_SPEED`, in the hand-written `honda_accord_2015au_radar.dbc`) was decoded at
1/128 m/s. The radar sends it at 1/64 m/s. So openpilot saw every radar track closing or pulling away at half its real
speed. The same went for `vLead`, `vLeadK` and `aLeadK` of every lead that came from the radar, which is 84-97% of the
lead time while moving on the six routes below. The DBC now says 1/64 on all 13 track messages (0x410-0x417 and
0x420-0x424). Nothing else changes. Nothing has reached the car.

* **What it did on the road.** Route 000000c0 closing on a stopped queue at 15 m/s: the radar put the car ahead at
  7.4 m/s, half of the 14.7 m/s closing speed. The planner's lead model therefore had it moving away at half our speed
  while it was standing still. The model's own lead swung between 0.8 and 8 m/s until t≈256 and read 0.1-0.6 m/s
  after it; experimental mode's e2e plan did most of the braking. Replayed with the fix, the radar lead reads -0.15 to
  +0.25 m/s through the whole approach:

  | t (s) | vEgo | model lead v | logged radar vLead | fixed vLead | dRel |
  |---|---|---|---|---|---|
  | 253 | 14.7 | 7.6 | 7.36 | -0.03 | 105 m |
  | 256 | 12.0 | 6.8 | 6.04 | 0.03 | 64 m |
  | 259 | 8.4 | 0.3 | 4.35 | 0.18 | 33 m |
  | 262 | 4.6 | 0.1 | 2.49 | 0.25 | 13.5 m |
  | 264 | 1.6 | 0.1 | 0.95 | 0.16 | 6.4 m |

  t is from the first logged event; the earlier study's clock is about 2 s behind it, so its "t≈251" is this table's 253.

* **The logged CAN.** On routes 113 and 120, the bus-1 track frames were decoded through each tree's own DBC
  (`CANParser`). For each 1 s window of one track, the rate of change of `LONG_DIST` was fitted against `REL_SPEED`:

  | route | group | before: slope (median ratio) | after: slope (median ratio) | windows |
  |---|---|---|---|---|
  | 113 | A (0x410-0x417) | 2.010 (2.021) | **1.005** (1.010) | 7464 |
  | 113 | B (0x420-0x424) | 2.073 (2.009) | **1.036** (1.004) | 267 |
  | 120 | A | 2.025 (2.012) | **1.012** (1.006) | 9939 |
  | 120 | B | 2.370 (1.998) | **1.185** (0.999) | 19 |

  B has few windows: its tracks are oncoming cars, and 120's 19 windows give a loose fit, so the median ratio is the
  better measure there.
  - **Stationary objects** (windows whose range rate is -vEgo) read `REL_SPEED` = -0.498 x vEgo before and
    -0.997/-0.996 x vEgo after.
  - **On the other routes** (c0, 10f, 115, 121) the A-group slope after the fix is 0.990-1.011 and B's median ratio is
    1.002-1.011.
  - **Only the factor was wrong.** Each fixed value is exactly 2x the old one on every frame, and `LONG_DIST` is
    identical. Byte 4 bits 7:6 are never set and negative values sign-extend from bit 37, so the 14-bit signed layout
    stands. The other speed on the radar bus, `0x300 VEHICLE_SPEED`, reads 0.99 x vEgo in km/h and was already
    right.

* **Through radard.** On six routes (c0, 10f, 113, 115, 120, 121) the logged CAN was replayed through this tree's
  `RadarInterface`, and radard was run on the logged `modelV2`/`carState`, once with the fixed decode and once with
  the old one. The old-decode replay reproduces the logged `radarState.leadOne` on 95.3-99.8% of frames.
  - For radar leads, the rate of change of `dRel` against `vRel` goes from 2.01-2.04 to **1.00-1.02** (five routes;
    121 has almost no leads).
  - The radar-or-camera choice barely moves: the radar-sourced share of the lead changes by -1.4 to +0.2 points, and
    the source differs on 0-1.5% of lead frames. The camera match in radard uses `vRel`, but its speed term rarely
    changed which track it picked.

* **What changes in the car.** Every radar lead now has its real speed relative to us.
  - **Closing on slower or stopped traffic,** the longitudinal MPC sees the real closing speed and brakes earlier.
    Before, it saw about vEgo²/20 m more room than there was, 11 m at 15 m/s.
  - **Behind a lead that pulls away,** it sees the lead's real speed and follows sooner.
  - **FCW** (upstream's crash check on the MPC's lead trajectory) can now fire on a fast closing that it could not see
    before.
  - **Experimental mode hides some of the benefit, none of the harm.** e2e is the plan in about half of low-speed
    following, and the planner takes the lowest plan, so the earlier braking shows most in chill mode or wherever the
    radar lead's plan is the one that binds. For the same reason a harsher false MPC plan (below) always wins in
    either mode.
  - **The stop and the pull-off are not camera-only.** The radar reports no tracks while the car is stopped, but they
    come back at 1-5 km/h, and below 4 m/s radard's low-speed override takes the closest radar track within 1 m of the
    path and 0.75-25 m ahead without asking the camera. On six crawl-heavy routes (01, 06, 07, 08, 10 and all of 14:
    8.1 min of engaged driving at 0.3-4 m/s) the replayed lead is a stationary radar track for 120 s with the fix,
    against about 19 s with the old decode.

* **What can get worse: false hard braking on stationary returns below about 36 km/h.** A stationary radar return
  used to read as a lead doing half our speed. Now it reads as stopped, which is correct, so when radard makes it the
  lead the MPC brakes for it as for a stopped car. Two unchanged upstream paths in radard let that happen:
  - **below 4 m/s, the low-speed override** (`openpilot/selfdrive/controls/radard.py` `potential_low_speed_lead`,
    :102-105, used in `get_lead`, :173-180). It needs no camera confirmation and checks no speed;
  - **whenever the camera lead is under 10 m/s, the camera match** (`vel_sane`, :133) accepts a stationary track:
    `|vLead - camera v| < 10` passes. At speed the fix does the opposite (next bullet).

  At walking pace the near-range returns are often clutter. On 10f at t=2740-2745, tracks 0x410 and 0x414 sit at 4-6 m
  reading -2.5 to -3.0 m/s (about -vEgo); their distance closes, then jumps back. Such tracks jump by more than 0.4 m
  in 4-10% of frames, against 1% for moving tracks, and `FLAG_B21`/`FLAG_B22` do not tell them apart. Measured by
  replaying the plan with the old and the new decode, counting episodes where the new `aTarget` is at or below -2 and
  at least 1 m/s² below the old one:
  - **Nine routes** (c0, c8, c9, 10f, 113, 115, 120, 121, the first 963 s of 14): 144 episodes. 94 below 4 m/s, 93 of
    them on a stationary radar lead and 67 where the car drove past the point within 3 s (63 s of such frames in
    0.28 h of crawling, about 6%); 39 at 4-10 m/s; 11 at 10 m/s and above. Only 2 engaged.
  - **Six crawl-heavy routes** (01, 06, 07, 08, 10, all 76 min of 14; 0.59 h engaged below 10 m/s): 75 episodes, 10
    engaged, 6 of those with the gas pressed. Engaged without gas: route 14 t=194.3-195.4, -2.75 m/s² for 0.9 s
    against the old -1.17 at 7.5 m/s, on a stationary return 12.4 m ahead and 1.4 m to the side while the camera lead
    was 17.8 m ahead doing 8.2 m/s, and the car drove past it; route 06 t=1209.5, -2.14 against -1.07. (Route 10
    t=110.6 is a real slower lead.)
  - The plan can ask for -3.5 m/s², past the car's 2.6 m/s² brake ceiling. On c8 at t=2321.1 (disengaged) the same
    mechanism raises a false FCW on clutter, -3.5 for 0.8 s: the only new FCW in the replay.
  - Engaged, off the gas and at walking pace, the plan itself rarely moved: of the 120 s above, the new `aTarget` was
    below -1.5 and 0.5 under the old one for 0.7 s. The owner's gas overrides at the same spots (old plan -1.4 to
    -1.9) suggest he already overrides milder phantom braking there.

  The fix is radard's clutter guard, the next entry up, a separate commit on the same day. The guard rejects a
  camera match whose track is much slower than a confident camera lead, and it requires the camera before the
  low-speed override takes a stationary track. **Until a stop-and-go drive on the guard is judged:** no engaged driving in car
  parks or driveways, and treat engaged stop-and-go and close follow as unvalidated. Without the guard, expect
  occasional firm false braking below about 36 km/h, most of it at walking pace, and override it with the gas.

* **What it removes at speed.** When the camera lead is over 10 m/s, the same match check now rejects stationary
  returns that the old decode passed (their half-scale vLead was over 3 m/s, which `vel_sane` also accepts):
  - route 120 t=116.6, engaged at 16.5 m/s: a stationary return read at 7.55 m/s made the logged plan brake at
    -2.31; with the fix the plan is -0.10 to -0.49;
  - 10f t=2065, engaged at 22 m/s: logged -0.94 from a return read at 11.1 m/s, -0.05 with the fix.

* **Checked, and left alone: nothing was tuned on the half-scale value.**
  - `radar_interface.py` passes `REL_SPEED` straight through.
  - radard is upstream's, and its camera match and Kalman filter assume the true scale.
  - In the fork, two places read a lead's speed:
    - the HUD's standstill banner (`LEAD_DEPART_MS`, 1 m/s);
    - `brake_route_check.py`'s stopped-lead tests (`vLead` < 0.5 and < 0.1).

    Both are physical thresholds that apply at or after a stop, where the radar has no tracks. Nothing in opendbc's
    Honda code reads a lead's speed.
  - **Logs recorded before this change still carry the half-scale `vRel`/`vLead` in `radarState`.** Any analysis of
    those routes that reads the radar lead's speed (the c1weak study, `brake_route_check.py` on older routes) reads
    the old numbers. That affects nothing at the stop itself, for the reason above.

* **How to judge it.** Drive it in chill mode and in experimental mode, and replay the first route recorded on this
  build, with stop-and-go traffic in it: the radar lead's d(`dRel`)/dt against `vRel` should be about 1.0 above
  4 m/s. Watch for:
  - earlier, smoother braking toward slower and stopped traffic, and none of the phantom braking at speed above;
  - quicker following when the lead pulls away;
  - sudden braking with nothing in the path at walking pace and at 10-36 km/h, and FCW in town: the false brakes
    above. Override with the gas and note the time.

  The walking-pace numbers come from the six crawl-heavy routes: the nine-route replay holds only 1.1 min of engaged
  driving at 0.3-4 m/s. On those six routes the old-decode replay matches the logged plan within 0.1 on only 68-94%
  of engaged walking-pace frames (99-100% on 10f-121), so the magnitudes there are approximate.

* **Tests.** `opendbc/car/honda/tests/test_elesys_radar.py` (new, 4 tests) uses logged frames of route 113:
  - the signal definition on all 13 track messages;
  - a raw value;
  - a stationary object through the real `RadarInterface`, which reads -vEgo, with its `dRel` changing at `vRel`;
  - an oncoming B-group track's range rate.

  All four fail on the old DBC.

Under the hood: opendbc `83c8b5b0`, `REL_SPEED : 37|14@0- (0.015625,0) [-128|128]` with a `CM_` per track message.
Docs: CAR doc 4.8 and 5.2, `docs/fork/README.md`, opendbc `FORK.md`. The root cause was found in round 5's
close-follow study (item 4) and confirmed independently by its review. The plan replay and the low-speed false brakes
came from the fix's own review. The close-follow study's fixed-radar baseline is the logged lead with its speed
doubled, not a radard replay on the new decode, so it does not include these false obstacles.

## 2026-10-06 — the three 2026-10-06 changes together (integration)

The pump rule C1b, the screen's confirm target and fixes 4 (the MADS resume and the confirm grace, board `298727b3`)
are merged into one tree, with opendbc carrying both `grantSeq` and C1b. They touch different code; what they share
was checked:

* **The confirm grace and the box.** A press up to 1 s after the prompt timed out still confirms (fixes 4). The box
  goes with the prompt as today's banner does - the speed and the face back as the prompt ends, the box fading out
  where the banner faded - and a confirm in the grace is the stock solid MAX number, the same screen as if no prompt
  had been up. A press inside the fade (route 120's: down 0.29 s, released and confirmed 0.41 s after the timeout) fades the box out under the MAX number as a confirm inside
  the prompt does. A new render test, `test_hud_render.py`, covers both timings.
* **C1b and stock ACC mode.** Stock ACC mode still never sets the pump flag: replayed with C1b on and off, nothing it
  sends differs, and it is the same as before C1b.
* **MADS and the rest.** The MADS resume reads only `grantSeq`; neither the pump nor the screen reads or writes it.
  Route 121 replayed on the merged tree resumes at 59.30 on the first fresh `0x70B`, as on fixes 4 alone.

Nothing else changes: the same tests, routes and screens give the same results as on each change alone.

## 2026-10-06 — the brake pump: "Quiet pump at stops" (rule C1b, on) replaces "Quieter brake pump"

You felt braking was weaker on the two drives with the quieter pump (rule C1, routes 120 and 121), so the brake data
was checked (study c1weak). It does not show C1 braking weaker - the confidence is low, two drives - but it found
three places where C1 pumped less than the rule before it, and the new rule puts all three back while keeping C1's
quiet stops. Nothing has reached the car.

* **The pump runs from the first moment of every brake application again.** C1 waited until the command passed its
  deadband (about 11 counts, a median 0.12 s and up to 2.1 s late), and about a quarter of applications never pumped.
* **Every rise past the small deadband (12/6/3 counts) is pumped the moment it arrives.** C1 waited 1 s after a burst
  before the next unless the rise was big; that gap was the one part of C1 with a measurable cost (about 29% more braking left undelivered).
* **The pump runs continuously again while creeping into a stop above 100 counts** (below 9 km/h). The soft stop's
  125-count cap was sized for exactly that run; C1 had dropped it, and 120's stop was the first logged one without it.
* **Kept from C1:** no top-ups while stopped - one burst to build the hold, or to deliver the soft stop's rise to the
  full hold, and nothing more however long you wait - plus the creep guard and the continuous run above 200 counts.
* **The cost:** the pump is as busy as before while moving - replayed on the 66 routes before C1, about 21% more pump
  time and 6% more starts than the old rule (24% / 30% more than C1); on five routes run through the car's own
  code it was +25% / +13%, right at the 25% limit below. You will hear the onset burst on every brake
  application again, and the whir on the final approach. Stops stay quiet: 59 of the old rule's 60 re-pumps more than
  5 s into a stop are gone. Like C1, it has no timed refresh while braking lightly (below 100 counts): in short light
  moments it can pump a few counts later than the old rule (replayed: about 12 s of 93 braking minutes 10 or more counts
  behind it, against 223 s ahead of it; CAR doc 7.2).
* **The setting is new and on by default:** Settings > Vehicle > Honda (or the mici vehicle page, or sunnylink >
  Vehicle > Honda), "Quiet pump at stops", offroad only, read at the next car start. It is a new key
  (`HondaElesysPumpC1b`), so it starts on even though you switched "Quieter brake pump" off; that setting is gone.
  Off = the old rule (v5), exactly as before.
* **How to judge it:** same roads, alternating it off and on, at least 6 drives each way with 10 stops behind a stopped
  car each way. `python openpilot/sunnypilot/tools/brake_route_check.py <on routes> --baseline <off routes>` now prints
  a "C1b ACCEPTANCE" block: the first half-second of braking no more than 0.03 m/s² softer than the old rule (the
  study's own measure: against the planner's target, adjusted for grade and target), stops at
  least 3.5 m behind a stopped car (none under 2.2 m), the stop itself no harsher than 0.6 m/s² (median), no more
  driver brake take-overs than the old rule's ~1.4 per braking minute, pump time at most 25% over the old rule, at most
  2 late re-pumps per 100 stops, no brake error. Judge it on those numbers, not on the sound. Turn it off on any of the
  old abort criteria (a hold that rolls, a stop under 2 m, a brake error or a VSA/ABS lamp).

Under the hood: `brake_pump_c1b_elesys()` (CarParamsSP flag 64, the `hondashadow`/`hondadyn` tag `pump=c1b`) replaces
`brake_pump_c1_elesys()`. Flag 16 is kept reserved - nothing sets it, an old CarParams carrying it runs the old rule -
and the route check still reads routes 120/121 as C1. With the setting off, every CAN frame matches the build before
this change; with it on, only the pump request bit (and its checksum) differs. The rule as built matches the study's
replay on all 3.42 million frames of its routes and reproduces its table (CAR doc 7.2).

After review (fix round 1, same day): the route check's acceptance block now measures what the study measured - the
error against the planner's target rather than the brake command, adjusted for grade, target size and the error already
there (on 120/121 it now reproduces the study's numbers, and a test keeps it so), the stop distance where the car stops,
verdicts only for an on-arm that ran C1b against an off-arm that ran the old rule, and a light hold's several build
bursts no longer flagged as breaking the design. A test now reads the pump bit the controller actually sends through
the soft stop's final approach. No change to the rule or to anything the car runs.

## 2026-10-06 — the screen: the speed a confirm would set, big, where MAX goes; a Current Speed setting

From the owner's pick of four mock-ups (design A) and his request to be able to drop the speed. Drawing only:
nothing here changes what openpilot does, when an alert fires or what it sounds like.

* **"Press + (or -) to confirm speed limit" shows the speed it would set, big, top left.** With Compact Speed
  Limit Prompts on, the set speed the confirm would give you - the limit plus any offset, kept to 0.1 km/h and
  within the cruise range as the confirm stores it, then shown as MAX will show it (so a 10 km/h shared zone on a
  car whose cruise starts at 30 shows 30, and mph rounds as MAX does) - is drawn where the stock MAX number goes,
  at the same size (the same digits, pixel for pixel: about 81 px tall, against 16 px for the little sign in
  today's banner). A dashed
  box round it instead of "MAX" means "not set yet", and "press + to confirm" with the blinking green key sits
  right under it. Press it and the dashed number turns into the usual solid "50 MAX" in the same place. While it
  is up it counts as the MAX number does: the driver-monitoring face and your speed top right make way, so there
  are never two big numbers. With an offset and no sign top right (Speed Limit Sign Off, or Zones outside a zone)
  the pill also names the limit: "press + to confirm  limit 50 +5" - the "+5" is the big number less the limit, so
  the note always adds up to it (a percentage offset rounds on its own). Nothing stays on the left: the box is only
  there while the prompt is, and if you swipe away from the road view and come back after it ended, it is gone - it
  does not fade out from where it was.
* **When it falls back to the old banner:** no limit to show, a confirm text that names no button (the PCM
  "set to ... to engage" one), or a screen without sunnypilot's HUD renderer. Compact Speed Limit Prompts off is
  the stock full-screen prompt, unchanged.
* **A critical alert during the prompt takes the screen on its first frame** - the box is not faded over it, and
  your speed is back in the cluster fading out under the alert on that same frame, exactly as with no prompt
  before it - and any other alert that takes the top left (a turn or disengage banner, or the old banner when the
  limit is lost mid-prompt) removes it at once too, with your speed back top right on that frame. When the prompt
  simply ends, the box fades out where today's banner faded out, and your speed and the driver-monitoring face come
  back on the frame they come back today (the face used to wait ~0.5 s for the box). Whether the box is up is
  decided from the alert of the frame being drawn, so on the prompt's first frame your speed has already made way.
* **New setting: Current Speed** (Visuals > HUD, right under Speed and Speed Limit, on = the screen as before).
  Off, your speed is not drawn top right - it is on the car's own dash. Nothing else moves: the sign, the next
  lower limit and the stop time stay exactly where they are (with Speed Limit Sign Off they sit in the corner on
  their own), and the stop time still replaces the speed at a standstill (Stop Timer is its own setting).
* **Fixed: the offset badge on a school-zone sign.** During a confirm with a speed limit offset, the small offset
  number on the sign top right sat on the school zone's right-hand lamp. With the lamps on the sign it now sits low
  on the right of the ring, clear of the digits and of SCHOOL; without them it is where it was.
* **New param:** `HudCurrentSpeed` (BOOL, "1"), backed up with your settings. Every HUD setting off is still the
  stock screen pixel for pixel, and with today's settings every screen that is not a compact confirm prompt is
  pixel-identical to before - checked on the render tests and on 94 replays of route 10f against the unchanged
  build (batch 2's scenes, also with Current Speed set on explicitly; the motorway, tunnel, school and queue
  moments in each sign mode; "set speed changed" and "auto adjusting to speed limit"; everything off, also with
  only Current Speed on; the prompts full screen; a confirm with no limit; a critical alert's first frame and
  1.5 s in after a confirm). 62 are pixel-identical; the 32 that differ are exactly the compact confirm prompts
  and Current Speed off. A review then replayed the frames a confirm ends on (into a turn, cruise-off or critical
  alert, into the old banner when the limit drops, into no alert 1 to 40 frames on): every one is pixel-identical
  to the unchanged build from its first frame, apart from the prompt's own fade-out top left when no alert follows
  it - the box fading where the banner faded.

## 2026-10-06 — board `298727b3` bundled · `0x70B` at 10 Hz

The gateway page now offers board firmware **`298727b3`** (EPS-LKAS branch `fix-70b`). It is `d995bc95` plus one
change in `src/gw_active.c`, with the same stage configuration (LIVE, split CAN, HUD merge, HUD own, camera mute,
authority 160, EPS floor 51.5 km/h, app slot). On `d995bc95`, `0x708` and `0x70B` went straight into the 3-deep
CAN Tx FIFO in the same millisecond as `0x704`. About one `0x70B` in five found the FIFO full and was dropped, but
it was still counted as sent: 7.79 / 7.84 Hz on 120 / 121, with gaps up to 12.3 s.

Every diagnostic except `0x704` now waits its turn on the board's camera→car ring, the path the 100 Hz mirrors
already used without loss. `GRANT_COUNTER` and the other frame counters only advance when a frame is actually
queued. Nothing changes on the LIN side, in `0x0E4`, or in forwarded traffic.

What to check on the first drive: the board's `0x707` shows `0x298727b3`, and `0x70B` arrives at 10 Hz with no
gap over 0.3 s. The MADS change below does not need this image, but this image is what makes `0x70B` fresh enough
for MADS to resume on time.

Built from a clean tree at `298727b3` (`flash-incar-stage10-appslot.bat`, build and checks only) and copied by
`bundle_appslot.py`: hash == HEAD, APL1 marker, origin 0x08004000, 46,532 bytes.

## 2026-10-06 — fixes 4: MADS waits for the board to say the override is over, and a confirm press up to 1 s late still counts

From the first-drive review of routes 120 and 121 (`A_synth` §3 issue 2, and the two late presses at the
speed-limit prompt). Nothing here changes what openpilot sends to the steering, the brakes or the ACC stand-down.
It goes with the board's `fix-70b` image (`298727b3`, `S:\Software\EPS-LKAS\CHANGELOG.md`), which gets `0x70B` onto
the bus at 10 Hz, but does not need it: with the d995bc95 image still in the car it is the more useful half.

* **After you take the wheel, lateral comes back when the board says the override is over.** Until now MADS
  resumed when the board's last "driver override" frame (`0x70B` reason 4) was more than 0.5 s old - the window in
  which openpilot treats a `0x70B` frame as current. On d995bc95 about one `0x70B` frame in five never reached the
  bus (gaps up to 9.9 s on 120 and 12.3 s on 121). On 121 the last override frame arrived at 56.90, MADS resumed at
  57.40 on no news at all, and the board's next frame came at 59.30. That frame happened to say the override was
  over; it need not have. Now the pause ends only on a NEW `0x70B` frame that says anything other than driver
  override. The replay of 121 resumes at 59.30, on that frame.
* **If the board stops sending `0x70B` during an override, lateral comes back after 3 s.** Waiting for a frame that
  never comes would leave lateral paused, lanes dashed, for the rest of the drive. So after 3 s with no `0x70B` frame
  at all MADS treats the board as absent and does what it does on a car without one: the ordinary resume (a brake held
  in Pause mode still holds it). The log gets one line, "MADS: no 0x70B for 3.0 s in a driver-override pause,
  resuming". If the board speaks again and still reports the override, MADS pauses again on that frame. Why 3 s: it
  is longer than 121's 2.4 s gap, so that resume waits for the board; after the board fix a 3 s silence is 30 frames
  lost in a row, a board that has stopped rather than a busy bus; and on the older images most silences that long
  were much longer anyway (routes 102-121: 18 of the 21 gaps over 2 s after an override frame were over 3 s, the
  longest 29 s on 110), so a longer wait would only keep lateral off longer. The board stays the authority meanwhile:
  it does not steer through your hands, so resuming into an override that is in fact still on costs a paused -
  enabled - paused round trip when its next frame arrives, not torque against you.
* **A press up to 1 s after the speed-limit prompt times out still confirms it.** The "Press + (or -) to confirm"
  prompt lasts 5 s. On 120 you pressed `+` 0.29 s after it ended: the set speed went 60 -> 61 and you then held `+` up
  to 80. On 121 you pressed `-` 0.88 s after it ended and held it: the set speed walked 80 -> 70 -> 60 in long-press
  steps and stopped at the limit only because 60 is a multiple of 10. Now the button the prompt asked for still
  confirms if it went down within 1.0 s of the timeout, for the same limit: on 120 the set speed goes 60 -> 80 when
  you let go, on 121 80 -> 60 on the first long-press step, and the rest of the hold does nothing. The other button
  never confirms; a press after the 1.0 s, or after the limit changed, is an ordinary set-speed step as before; inside
  the prompt nothing changed. Non-PCM cruise (this car) only.

**What changed, for the next merge.**

* `0x70B` gets a frame count: `carStateSP.linbusGateway.grantSeq @27 :UInt32` (sunnypilot `custom.capnp`, opendbc
  `structs.py`), stepped by `carstate_ext._update_linbus_grant()` once per frame that actually arrived, never while the
  500 ms window only holds the last one. `grantValid` cannot tell the two apart. A consumer keeps the value it last
  saw, so a `carStateSP` that selfdrived misses loses nothing.
* `mads.py` (`FORK(LKAS-GATEWAY)`): the gateway block clears `_gw_paused` only on a frame whose `grantSeq` it has not
  seen that says anything but driver override, or after `GATEWAY_SILENT_RESUME_FRAMES` (300 MADS frames, 3 s) with no
  new frame (`_gw_grant_seq`, `_gw_silent`). Entering the pause is unchanged.
* The grace (`FORK(SPEED-LIMIT)`, `PRE_ACTIVE_CONFIRM_GRACE = 1.0` in `speed_limit_assist.py`). card decides
  (`cruise_ext.py`): it sees the buttons at 100 Hz, dates each press by when it went down (`cruise.py` passes the
  button timer), swallows the asked-for press that went down within 1.0 s of card seeing the prompt time out on the
  same limit, and sets the set speed to the limit; the planner then confirms on the set speed matching, as upstream
  does from inactive. The planner's own grace (`update_buttons(release_toggle, pressed)`, with `plannerd.py` passing
  `selfdriveStateSP.buttonsPressed`) is the same rule on its 20 Hz view and can only accept a subset of what card
  accepted, so a press is either swallowed and confirmed or a plain step that confirms nothing - never lost, never
  both.

**Tests.** `test_mads_gateway_pause.py` 55 (41 + 14: a normal release, override gaps of 0.3-2.99 s held, 3/5/12 s
resumed by the fallback and re-paused by the next override frame, lost release frames, a board gone silent - one log
line, no flapping - the fallback still held by the brake, and route 121's frames); 10 of the 14 fail on the previous
`mads.py` (the 0.3 s gap, the normal release, the constant and the brake case pass on both). New
`test_speed_limit_confirm_grace.py` (30): card, selfdrived's button tracker and the planner together - both directions
0-0.95 s late, a long press that confirms and never walks, after the grace, the wrong button short and held, wrong
then right, a changed limit, a new prompt, inside the prompt as before, card and planner agreeing at every 10 ms from
0.80 to 1.20 s late under three lags, the planner dating a held press, no card grace on a PCM car. opendbc
`test_dynamic_tuning_integration.py`: four `grantSeq` checks in the `0x70B` section. Full sunnypilot suite 2387
passed, 45 skipped, 1 xfailed; ruff and ty clean on the changed files.

**Replays**, closed loop through the real code: route 121 46-62 s through card's `CarInterface` and MADS - the old
`mads.py` resumes at 57.40, the new one at 59.30 on the first fresh frame, both identical everywhere else; routes 120
174-186 s and 121 62-76 s through card's `VCruiseHelper` and the planner's `SpeedLimitAssist` on the logged buttons
and resolver - with the grace switched off the replay reproduces the logged set speeds and states exactly, with it
120 confirms at 182.52 (60 -> 80) and 121 at 71.63 (80 -> 60).

**On the next drive:** after a driver override, MADS goes back to enabled only on a `0x70B` frame with reason other
than 4 (in the log: `carStateSP.linbusGateway.grantSeq` steps on that frame); no "no 0x70B for 3.0 s" line in the log
with the fix-70b board, where a 3 s silence should not happen; a confirm press just after the prompt clears confirms
without a 1 km/h step.

---

## 2026-10-05 — batch 3 after review (fix round 1): one pump change for you to confirm, the brake law is not accepted

A review of batch 3 (the entry below, corrected where it was wrong) found these. Nothing has reached the car.

* **Decide: the quieter pump now delivers the soft stop's hold.** With Dynamic Tuning on, the soft stop holds the brake
  at about 125 counts while the car rolls and raises it to the full 189 hold half a second after the wheels stop. The
  study's rule only let the pump run at a standstill to build a hold from under 100 counts, so that last rise was
  never pumped and - since braking only rises while the pump runs - the car would have sat on ~125 for the whole stop,
  never topped up. No logged stop has held that low. The rule now also runs one burst at a standstill when the brake
  rises more than 15 counts above what was delivered, as the previous rule did; a steady hold is still never topped up.
  On the 66 study routes that is one extra burst in 107 stops. Say if you would rather have the study's rule as
  written (CAR doc 7.2).
* **The measured brake law is not accepted, and the descriptions now say so.** It fails two of your acceptance
  checks, both toward less brake: the 72-90 km/h band is off by 0.076 m/s² (limit 0.05), and in the simulator
  openpilot's correction while braking misses its ±0.05 limit, slowing about 0.18 m/s² less than asked in the first
  second of braking (0.09 today). The simulator cannot judge stops, holds or the pump (its coast-down is the law's
  own, it gets the stop wrong by about 0.5 m/s², its pump model changes nothing), so "stops unchanged" was not
  evidence and is gone. The suggested fixes were tried: braking 0.05 earlier passes the correction check on the
  nominal plant (+8% brake applications) but brakes where coasting would do and still fails when the coast-down is
  0.1 off - as today's law does; the soft-toe width changes nothing. Nothing adopted; it stays off and needs your
  sign-off. "Stops are unchanged" now reads "stops use the current law with the learned correction at x1.00".
* **The learned brake gain is saved only once a minute again.** Batch 3 also saved it at every disengage and at
  ignition-off, which changes where the next drive starts - not "logging only". Now those two saves carry the
  drive-mode counters only.
* **The route check could not see a hold that rolls** - its creep verdict always read PASS: it ended a hold on the
  first moving frame. A hold now runs from the stop for as long as it is held. Also fixed: driver brake take-overs
  always read 0 (a press cancels openpilot, so those stops never counted; 115 has three); the final-approach number
  now uses pump2's own method; a one-frame burst-edge jitter no longer fails a correct rule (115 sat 0.04% above the
  line); the brake law is read from the route's own lines (the setting alone can be on without the law running);
  `--rule` no longer overrides the log; the learner verdict compares each drive's own change; each hold shows the
  brake pressure actually delivered.
* **The two settings can no longer be changed from sunnylink while driving** (the device refuses, as for stock ACC
  mode), so a later onroad cycle cannot switch the pump rule or the brake law mid-drive.
* The end-of-drive log line is attempted at ignition-off but may not reach the route (loggerd stops in the same
  pass); the saved counters are what to rely on. A sunnylink restore can bring the brake law setting back on.

## 2026-10-05 — batch 3: a quieter brake pump (on), and a measured brake law (off, for testing)

* **The brake pump runs only to build pressure** ("Quieter brake pump", `HondaElesysPumpV6`, **on by default**;
  rule C1, `CAR-HONDA-ACCORD-9G-AU.md` 7.2). Measured on this car: a rise in braking arrives only while the
  pump runs, while a steady brake, a release and a built standstill hold need nothing. So each application gets
  one short burst at about 12 counts, each further rise one more (after a 1 s gap unless it is big), firm
  braking above 9 km/h still runs it continuously, and braking at 100+ counts while moving gets a burst at
  least every 6 s - which also catches a hold that starts to roll. Gone: the 30 s top-up while stopped, the
  continuous run while creeping into a stop, and the light-braking backstop. Replayed over the 66 current
  routes: 18% fewer pump starts for 2% less pump time; at stops, no re-pump once the car is held (corrected after
  review: one burst still delivers the soft stop's rise to the hold, above). The cost:
  applications too light to matter (under about 0.1 m/s²) never pump. Turn it off to go back to the previous
  rule; pump2's abort list (7.2) says when.
* **Replayed through the real controller** on routes 10f, 113 and 115: both rules reproduce the study's table,
  the C1 code matches the study's reference rule on every frame, and the previous rule reproduces the pump bit
  the car actually sent. With both new settings off, every CAN frame is the same as before batch 3 (432,675
  control steps).
* **"Measured brake law (testing)"** (`HondaElesysBrakeLawV2`, **off by default**; `elesys_brake.py`, CAR doc 7.10).
  Today's law assumes the car coasts at almost nothing and that the first brake count already brakes; measured, the
  car coasts at 0.3-0.5 m/s² above 18 km/h and the brake does nothing for its first ~20-40 counts. So a third of
  openpilot's braking asked for slowing that coasting alone gives, at a few counts that only ran the pump. With the
  setting on, above 14 km/h in normal control the brake comes on only below the measured coast-down, starts at the
  low end of the dead zone (8-32 counts) and follows a measured slope per speed band; between the coast-down and the
  point where any pedal would end coasting, neither pedal nor brake is sent. The pedal's zero point moves with it,
  using a pedal offset re-measured after the pedal's own recalibration of 2026-09-16/17 (which also means gas law v2's
  tables want a refit on routes from 000000dd). Below 14 km/h, in the stopping state and at a standstill nothing
  changes, and the learned brake gain is held at x1.00 while the setting is on (the stored value is kept). It needs
  gas law v2 and stays off without it.
  - Fitted offline on routes up to 10f: held-out (110-115) error 0.158 m/s² against today's 0.284 (pass, <= 0.17);
    every speed band within 0.015 except 72-90 km/h at -0.076 on two routes (fails the 0.05 rule), and nothing held
    out above 90 km/h. In the closed-loop simulator: a third fewer brake applications, none of the light ones (87 ->
    0), 37% less brake time while moving, 7% fewer pump starts and 13% less pump time, overall tracking as good or
    better (worse while braking). The cost: in the first second of braking it slows 0.18 m/s² less than asked (today
    0.09), and openpilot's correction while braking misses B4's ±0.05 (corrected after review: it fails two
    acceptance checks, and the simulator cannot judge stops; above).
  - The simulator was rerun with the real controller code in the loop: on all 17 routes it reproduces the fit's
    numbers exactly (pump bit on every frame, brake count on all but 4 of 2,027,232 frames, by one count). With the
    setting off, 115 and 10f replayed through the real car code give every CAN frame and output identical to before
    (392,010 control steps, tuner on and off). 29 tests.
* Both settings are offroad only and take effect at the next drive: the car reads them once, at ignition, into
  the drive's car parameters (flags 16 and 32), so every route records which pump rule and brake law it ran.
  Never in stock ACC mode.
* **A route check for the A/B** (`openpilot/sunnypilot/tools/brake_route_check.py`, CAR doc 9.4): give it route
  folders (`S:\OP\sunny_logs\<route>`) and it prints the pump study's proof-plan metrics and a PASS / ABORT on
  each abort criterion, plus the brake law's acceptance numbers; `--baseline` takes the other arm's routes. On
  today's 10f, 113 and 115 it reproduces the study's table exactly, the logged pump bit matches today's rule on
  99.5-99.8% of braking frames, nothing aborts, and three of the comparisons wait for C1 drives. One reading
  differs from the plan: a hold counts as moved when the car's own wheel, gearbox or speed signal moves; the
  radar and camera limits only flag a hold to look at, because today's held stops already read 0.2-0.3 m of radar
  change with nothing moving.
* **Logging you can trust, no change to how the car drives** (learnaudit B1, G5, G6): every `hondashadow` line now
  says what it was measured on - commit, gas law, launch cap, pump rule, brake law, tuner on/off - and
  `shadow_learn_report.py` will not pool different builds; route 115's 0.72 launch multiplier is discarded
  (measured without the cap). The shadow learners also run with Dynamic Tuning off (logging only), the launch is
  measured from the first wheel motion, the ECON/S time counts manual driving too (`modemov`, logged only), and
  the drive's totals are saved at every disengage and at ignition-off rather than only once a minute (corrected
  after review: the counters only - the learned brake gain keeps its once-a-minute save). Replayed
  through the real controller on 115 and 10f with the tuner on and off: every CAN frame and actuator output
  identical with and without these changes, and against the pre-batch tree (392,010 control steps).

## 2026-10-04 — batch 2 after review: what changed, and two things for you to decide

A review of batch 2 (the three entries below, already corrected) found these; all fixed
before anything reached the car.

* **Decide: the launch cap reaches 3-6 m/s.** You named 0-3 m/s. Stopping it at 3 m/s would
  make the pedal jump there, so it fades into the measured law by 6 m/s instead. It only
  ever takes pedal away. It reduces the launch surge rather than removing it, and it is
  part of the "Measured Gas Pedal Law" only. Please confirm, and on the first drive check
  that pulling away behind a car does not feel sluggish.
* **Narrowed: the wrong-way button during the confirm prompt.** While the prompt
  is up, the other button is ignored only when it would RAISE the set speed ("-" under
  the gas above your set speed, as on 114, or "+" while it asks for "-"). A press that lowers
  the set speed always works, so "-", and SET under the gas below your set speed, still slow
  you down (115's 60 -> 59 happens again). Brake and cancel work as always.
* **Decide later: the brake table below 54 km/h.** It measures the brake differently from
  openpilot's own integrator, and the two only agree at highway speed (entry below).
* **Compact alerts can no longer be fooled by a new alert type.** Each listed alert is
  allowed only as the one kind it is today, and never when it says "openpilot unavailable";
  a test fails if sunnypilot ever adds another kind under a listed name. (Before, an
  "unavailable" alert under a listed name would have been drawn small, or not at all.)
* **The confirm banner.** Its green key now always matches its words (they could disagree for
  a frame at a .5 km/h set speed); with no words (set speed already the limit) nothing is
  drawn instead of an empty black box; with the sign hidden it always shows the limit, also
  with "Show the Limit in the Confirm Prompt" off, and also while the sign is still fading in.
* **The panda's heartbeat fix is now tested with CAN traffic** between its once-a-second
  checks, so a test fails if the fix were ever to switch the check off.
* **The shadow learners take cleaner data:** a full clean second after you touch a pedal or
  re-engage (your throttle was being counted as coasting), launches only with no car ahead
  and with the pedal confirmed by the engine computer, and the brake gain in force logged
  with the brake table. They can no longer take the car's controls down if their own code
  fails to load. Still nothing the car does reads them.
* **The report tool** runs in the repo's own Python (no pandas: it reads the rlogs) and no
  longer mixes up routes whose file names end alike.

---

## 2026-10-04 — batch 2, the car: lane keeping that stays on after a quick re-press, no fault at key-off, a gentler launch

From routes 114 and 115 (`A_synth` F1, F5, L2a; your decisions 1, 2 and 8).

* **A quick LKAS re-press no longer loses the steering two seconds later** (114 at 805 s:
  "TAKE CONTROL IMMEDIATELY", controls mismatch). After the panda drops lane keeping
  because openpilot turned it off, its mismatch count stays full until its next once-a-second
  check; a press inside that second was granted and then taken away by that check, before
  openpilot's own "on" could reach the panda. The grant now starts the count from zero, the way
  the panda already does for cruise. Three real mismatches in a row still turn it off. This is
  the panda's own code (`mads.h`), so it takes effect when the panda is flashed with the new
  build - pandad does that by itself at start-up when the panda's firmware differs. Replayed
  through the old and new panda code: 114's 200 blocked steering frames become 0, nothing else
  on 114 or 115 changes.
* **No "LKAS Fault" at key-off.** The EPS sends a "driver steering" status in its last frames
  as you switch off (and its first as you switch on), which openpilot read as a steering fault.
  It is now ignored only when the car is stopped AND in P; moving, or in any other gear, it is a
  fault as before.
* **A gentler launch from a stop.** At 115 t 511 the car asked for 1.6-2.0 m/s^2 and pulled
  2.4-2.7. Below 6 m/s the pedal is now capped by a line measured on 48 routes. It never adds
  pedal; demands below about 0.85 m/s^2 get exactly the pedal they got before, but above that,
  between 1 and 5 m/s, it can trim the pedal behind a car too (on about a fifth of the lead
  frames below 6 m/s in the replay). It **reduces** the over-delivery, it does not remove it:
  below ~1.4 m/s a normal launch demand is not capped at all, and the model still has 115's
  launch at 1.88 against 1.70 asked (peak 2.18, was 2.82). Over the 13 clean launches from a
  stop on 099-115 (modeled, with the same fit the cap came from): achieved/asked 1.18 -> 1.04
  with no car ahead and 1.11 -> 1.02 behind one, peak 2.32 -> 1.85 m/s^2, and 6 m/s reached no
  later (0.03-0.07 s sooner). Replaying 115 and 10f through the car code, the gas command
  changes only on 102 / 382 frames, all below 5 m/s, all less pedal. **It reaches 3-6 m/s**,
  not only the 0-3 m/s you named, so that it meets the measured law at 6 m/s instead of
  stepping off it at 3 - please confirm that is acceptable. **It is part of the measured law
  (v2) only**: with "Measured Gas Pedal Law" off the launch is uncapped, as before. First
  drive: a launch behind a car pulling away must not feel sluggish.

---

## 2026-10-04 — batch 2, learning in shadow: a brake table, a launch multiplier and a split steering factor, logged and not used

From routes 114 and 115 (`A_synth` L1, L2b, L3; your decision 9). **Nothing the car does
changes.** Three learners now watch the drives and write what they WOULD learn into the
route; no command, setting or stored value reads them. The brake and launch ones run only with
**Dynamic Tuning (learning) on** - they read its pitch and plant model - and the gas
interceptor fitted; the steering one runs on every drive. After a few drives the numbers say
whether each one is worth switching on.

* **Brakes (L1).** One brake gain cannot describe this brake: light presses deliver less
  per count than firm ones. A table now measures, per speed band and per brake strength
  (up to 60 counts, 60-100, over 100), how far the car's real deceleration is from what
  was asked for, and what it would add - only ever more braking, at most 0.5 m/s^2, and
  only from a cell with 5 s of clean data. It also measures how fast the car slows with
  neither pedal nor brake (the coasting drag), which decides where the brake should come
  on. It collects about 34 s a drive on 115 (the old gain learner took 7 s).
* **Launches (L2b).** It measures how much harder the car pulls away from a stop than it
  was told to, from 0.5 to 6 m/s, and the pedal multiplier that would cancel it - only
  ever less pedal, at most 40% less. Only launches with no car ahead and with the pedal
  confirmed by the engine computer count; launches behind a car are logged apart.
* **Clean data only.** Nothing is taken until a full second of engaged control with no pedal
  of yours: after you lift off the gas, your throttle stays in the car's acceleration for up
  to ~0.8 s (10f), and the first version counted that as the brake law's coasting.
* **Steering (L3).** It runs the same fit openpilot's steering learner does, but separately
  below and above 70 km/h, to see whether town and highway really need different factors
  (one factor swings 1.13 after town to 1.41 after highway).

**What it would have learned on your last drives** (replayed offline; 10f, 110-114 ran the
older build):

* Brakes: below 72 km/h the brakes are on target or slightly strong, so nothing would be
  added. Above 72 km/h (10f) light and medium presses are weak: it would add 0.14-0.26 m/s^2.
  Coasting slows the car 0.26-0.35 m/s^2, a little more than the brake law assumes. Checked
  against openpilot's own longitudinal integrator on the same moments: above 72 km/h the two
  agree within 0.06; below 54 km/h they do not (up to 0.18 apart, and of opposite sign at
  36-54 km/h with firm braking), so nothing below that should be applied from this table alone.
* Launches: of the six routes only one launch counts (no car ahead, pedal confirmed): 115's,
  1.4x harder than asked, so it would cut the launch pedal to 0.72. Behind a car the car pulls
  1.1-1.2x (kept apart, never used). One launch is not enough to act on. These drives ran before the launch cap (entry above), which reduces the same
  over-delivery, so from the next drive this number shows what the cap leaves. **115's launch was on a 2.7 degree downhill**: the slope gave 0.46 m/s^2 of the
  2.4-2.7 the car showed, more than half of what looked like over-acceleration there.
* Steering: on 115, 0.85 below 70 km/h and 1.00 above (few points); on 10f 1.31 below and
  2.02 above. Town and highway do separate, by about a third.

**Proven not to change anything:** routes 115, 10f and 113 replayed through the real car
code with and without the learners send the same bytes on every one of 1.05 million CAN
frames (115 and 10f again after the review fixes); the steering learner's published values are identical on six routes. Cost: about
2% of the car-control loop's time, and 1-2 KB of log a minute (more on a drive with many
disengagements: a line goes out at each).

**To read them:** `python openpilot/sunnypilot/tools/shadow_learn_report.py <route folder>
[more routes]` prints each route's tables and all of them combined. The log lines are
`hondashadow` (needs dynamic learning on) and `latsplit`. Details: `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 9.3.

---

## 2026-10-04 — batch 2, the screen: "Press +" when it means +, a wrong button cannot raise the set speed, smaller alerts, a speed limit sign setting

From routes 114 and 115 (`A_synth` F4, F4b, U1, U2). Nothing here touches steering,
braking or the ACC stand-down; the cruise change only ignores a button.

* **The confirm prompt says the right button.** "Press - to confirm speed limit"
  showed every time "+" was needed - all four such prompts on 114 and 115, and
  "Press +" never once in nine routes. The text compared your set speed (already
  km/h) converted again as if it were m/s, so 50 km/h read as 180. It now uses the
  exact comparison the confirm itself accepts, so the words say the button the confirm
  takes (a test checks every set speed from 8 to 145 km/h in 0.1 steps, km/h and mph),
  and the compact banner's green key follows the words. The bug is upstream
  sunnypilot's; worth offering them.
* **A press the wrong way while it asks cannot raise the set speed.** On 114 you
  pressed "-" while it wanted "+", with your foot on the gas: the set speed jumped
  from 50 to 72.5 (SET under the gas takes the current speed) and the prompt stayed.
  Now, while the prompt is up, a press the other way that would RAISE the set speed
  does nothing - no change, no confirm: "-" under the gas above your set speed, or "+"
  while it asks for "-". A press that lowers the set speed works as always, so you can
  always slow down: on 115, "-" while it asked for "+" took 60 to 59, and still does,
  and SET under the gas below your set speed lowers it as before. A wrong-way press
  never confirms, so the prompt stays until the right press or it times out; the right
  one confirms as before. Outside the prompt the buttons are exactly as they were.
* **Smaller alerts** (three new settings in sunnylink Visuals > HUD, all on):
  * **Compact Speed Limit Prompts.** "Auto adjusting to speed limit" and "Set speed
    changed" no longer cover the road for 5 s: nothing is drawn, the new set speed
    shows as the MAX number top left (as after any change - the full alert used to
    hide it), and your speed and sign stay. On 115 that was 57 of the 77 seconds of
    full-screen speed-limit prompts; the other 20 were the confirm prompts, now a
    one-line banner: "press + to confirm" top left with the blinking green key, and the sign top right turns dashed until you
    confirm. The sounds are unchanged.
  * **Compact Disengage Notices.** "Cruise off" (the cancel button with lane
    centering kept) and "lane centering off" (the LKAS button) are a small banner
    top left.
  * **Compact Turn Notices.** "Turning left / right" is a small one-line banner -
    these were the longest full-screen alerts on 114 (95 s).
  * **What can never be small:** anything red or orange, "take control", anything
    that wants the wheel, AEB, FCW, "openpilot unavailable", openpilot ending it by
    itself ("speed too low"), any fault. Only alerts named on a short list qualify,
    each only as the one kind it is today, and only while they are the plain black
    kind and not an "openpilot unavailable"; a test walks every alert openpilot and
    sunnypilot have and fails if that ever stops being true, or if a listed alert
    gains another kind. A critical alert during a compact one takes the screen as
    before.
* **Speed Limit Sign** (new setting, under Speed and Speed Limit): **Always** (the
  default - the screen exactly as before), **School & Variable Zones** (the sign
  only in a NSW school zone while it is on, or a variable limit zone; it stays
  ~12 s after, so the speed does not jump at every limit change - one zone on 10f
  broke up 10 times), or **Off**. Without the sign, your speed, the stop time and
  the next lower limit slide into the corner, and the confirm banner carries the
  limit it is asking about (whatever "Show the Limit in the Confirm Prompt" says). On 114 and 115 there was no school or variable zone,
  so in Zones mode the speed would have sat in the corner the whole way.
* **New params:** `HudCompactLimitPrompts`, `HudCompactDisengage`,
  `HudCompactTurn` (BOOL, "1") and `HudLimitSign` (INT, "2"), backed up with your
  settings. All HUD settings off is still the stock screen pixel for pixel.

---

## 2026-10-04 — Stock ACC mode: the car's own cruise does gas and brake, openpilot steers

**A new toggle, "Stock ACC (testing)"** (Settings > Vehicle > Honda on both screens, and sunnylink's Vehicle > Honda
Settings). Off by default, and with it off nothing changes - not one byte of what the car or the panda sees. With it
on, from the next car start:

* **The car's own ACC drives the speed.** The Elesys radar does gas and brake exactly as it does without a comma: every
  frame passes between the car and the radar, both ways (panda safety param 68). openpilot sends only its steering
  (`0x0E4`) and the board's HUD frame (`0x500`), so lateral is as before, through the board.
* **CMBS is unaffected** - the radar's brake frame always reaches the car - and the CMBS-off switch still works.
* **openpilot follows stock ACC**: it engages when you SET (stock ACC works above about 30 km/h) - that only drives
  its alerts and HUD state; steering is MADS, the LKAS button, exactly as today - and shows "Speed too
  low" when stock ACC lets go by itself at about 22 km/h. That is the normal disengage, not a take-control alarm, and
  **lateral stays on**. If stock ACC lets go by itself at speed (37.8 km/h or more: a fault), openpilot says "TAKE
  CONTROL IMMEDIATELY / Cruise Is Off", with or without MADS - gas and brake are gone. A SET below 30.6 km/h that
  openpilot does not follow shows its "drive above" alert; press SET/RES again above that speed to have it follow.
* **MADS can be switched on and off at any speed**, standstill included, also while openpilot follows stock ACC.
* **openpilot cannot cancel stock ACC.** If openpilot disengages, or refuses to engage (between 29 and 30.6 km/h, a VSA
  fault), stock ACC keeps going: cancel it with the car's CANCEL button or the brake. Keep "disengage on accelerator"
  off in this mode.
* **Your settings come back.** Experimental Mode, Dynamic Experimental Control, the custom ACC increments, Smart Cruise
  Control and Speed Limit Assist need openpilot's longitudinal and are unavailable in this mode. They are saved at the
  first stock drive and put back as they were at the first drive after you turn the mode off - also across a reboot
  or an update in between.
* **A harness relay that did not open is caught.** The panda checks for the radar's own frames on the car's side, so a
  loose cable or a failed relay is a relay malfunction (the car then runs as without a comma) instead of every frame
  being sent back onto the same wire.
* **Every stock drive says so**: "Stock ACC Mode" on screen for about 5 s at the start, `carParamsSP.flags & 8`,
  `pandaStates.safetyParam` 68, and a log line `Honda ELESYS stock ACC mode: openpilot longitudinal off, all frames
  forwarded`.

The toggle can only be changed with the car off - the device refuses it from sunnylink while driving too (it is read
once at ignition, so the mode never changes during a drive) - and it is not part of a sunnylink backup, so a restore
can never turn it on. No board change.

**First drive with it on:** `pandaStates.safetyParam` 68 and no relay malfunction; from ~2 s after the panda reports
68 until key-off, `0x1FA`/`0x30C` never with src 0 in `can` (only src 2 and the forwarded src 128; src 0 during the
~10 s start-up window and just after key-off is normal); parked, the accelerator moves `PEDAL_GAS` with `GAS_SENSOR` `STATE` 5;
only `0x0E4` and `0x500` in `sendcan`; the radar's `0x1FA` (50 Hz) and `0x30C` (10 Hz) forwarded onto bus 0; no
`ACC_PROBLEM`, `TSA_ERROR` or `BRAKE_ERROR`.
The full list is `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` section 15.8; the design, section 15.

**With batch 2 (merged 2026-10-05):** in this mode the launch cap, the shadow brake and launch learners and the
speed-limit confirm prompt (with its wrong-way rule) do nothing - they all need openpilot's longitudinal - and none of
this mode's own alerts ("Speed too low", "Cruise Is Off", "drive above", the startup banner) is drawn compact. The
key-off fix and the lane-keeping re-press fix apply here too. Details: section 15.9.

---

## 2026-10-03 — the comma 4 HUD: speed and speed limit, the next lower limit, a stop timer, the planned stop and curve on the right; the gateway icon

**The comma 4 now shows your speed and the speed limit**, top right beside the
confidence ball: your speed as the Accord's own speedometer reads it
(`vEgoCluster`), ~35 px, next to a 60 px Australian speed sign (white, red ring,
condensed black digits). This is the design you picked from the mockups (Layout B,
"speed cluster"), drawn by the same code that rendered them.

* **The next lower limit.** When the next limit is LOWER and within 15 seconds or
  500 m (whichever comes first), a 36 px sign appears under the speed with the
  distance over a bar that shrinks toward it (your pick: both; sunnylink can make
  it the bar or the distance alone). Never a higher limit, never while stopped,
  and only while the limit on screen comes from the map. The distance uses
  sunnypilot's own rounding (`300 m`, `1.2 km`); the map sends it once a second,
  so in between it counts down with your speed and the bar shrinks smoothly
  instead of in 30 m steps on the motorway.
* **School zones keep the round sign.** You said the plate looked out of place:
  the sign is now the same circle as every other limit. When a NSW school zone is
  on, the 40 sign gets two amber lights on its rim that flash in turn, about once
  a second, and an amber **SCHOOL** under it (15 px, the smallest text in the
  mockups). Outside the zone's hours the lights are grey and there is no text.
* **Variable-limit zones** (motorways and tunnels with overhead signs) draw the
  sign like the electronic one: black face, red ring, white digits - a reminder
  that the overhead sign can show less.
* **At a stop** the speed becomes a stopwatch and the time stopped (`0:29`), and
  the orange "take control / resume driving manually" no longer covers the
  screen: it is a small banner top left. **When the car ahead moves off, the full
  orange prompt comes back** (once the radar has seen it moving for 0.4 s, so a
  noisy reading cannot do it) and stays until you go - that is the moment the
  prompt is for. **Every stop starts with the small banner again**, however many
  times the car ahead moved off earlier in the drive. A stop of an hour or more
  (`1:02:05`) shrinks the time to fit rather than running into the banner.
  With the banner setting off, the full prompt covers the screen at every engaged
  stop - and the stopwatch under it; the Stop Timer then shows only while you
  are stopped and not engaged.
* **"Press - to confirm speed limit" now shows which limit:** the green arrow is
  replaced by the limit itself with a dashed ring (not confirmed yet) beside the
  same green minus (or plus). The minus blinks as the arrow did. If you have a
  speed limit offset, it shows on the sign as a small black badge (`5` for +5),
  exactly as sunnypilot's own sign shows it: confirming sets limit + offset, and
  the plus or minus compares your set speed with that sum.
* **Settings > gateway** has its own icon: **B, the inline bridge** (the board on
  the harness, traffic both ways), which you picked. The update dialog keeps the
  download arrow. The icon is stored as an ordinary git file: the repository
  keeps PNGs in Git LFS on sunnypilot's server, which this fork cannot upload to,
  and an icon that never arrived would have stopped the comma 4's screen from
  starting. Should it ever fail to load anyway, the tile falls back to the old
  icon.

**Your right-side request, done as you settled it: "planned stop + curve".** At
the top of the right strip, above the confidence ball, one thing at a time:

* **Planned stop:** an arrow onto a stop line and how far ahead the driving
  model's speed plan comes to a standstill (`20 m`), when that is within the
  next 10 seconds and no car is in front of it (the radar's car inside that
  distance + 10 m means the plan stops behind a car - your dashboard shows
  that). Once a stop is showing, only a car within 5 m past it takes it away:
  a car parked a little past the line, coming and going on the radar, can no
  longer blank the countdown. **White with a solid line only while openpilot is
  driving your speed and the model's plan is the one it follows; grey with a
  dashed line when it is only the model's plan** and openpilot is not braking
  for it (disengaged, chill mode, or another target in charge). It appears after
  0.3 s and goes 0.5 s after, so it does not flicker, and it goes at once in the
  last metre and at a standstill, where the stopwatch takes over. Metres (whole
  metres under 20 m, then 5 m steps), or feet if the comma is set to imperial.
  **The number only counts down** - it goes back up only if the stop moves 5 m
  or more further away, so the plan's twenty re-calculations a second do not
  make it jitter (`9-10-9`, `6-7-8`).
* **It never says why.** Nothing on the comma - the model or any other message -
  knows whether the plan stops for a red light, a stop line, a give-way or a
  queue the radar has not picked up yet, let alone a light's colour (the
  research went through every field). So there are no light or sign icons, as
  you decided.
* **Curve:** a curve arrow, left or right (from the model's predicted path), and
  openpilot's own target speed for the curve, in 5 km/h steps as curve signs
  are (`35 km/h`), while Smart Cruise Control is slowing the car for a curve
  (vision: entering or turning; map: turning) AND it is what limits your speed
  AND its target is below your speed - it appears once the target is 2 km/h
  under it and goes once it is 3 km/h over. On the motorway, where its target
  is your own speed, it shows nothing. It goes **the moment you press the gas
  or the brake** (openpilot is no longer driving the speed), not half a second
  later. Not when your set speed is already lower.
* A planned stop comes before a curve.
* **The confidence ball keeps its size, colours and maths.** While an item shows
  it is held just under it - it moves down only if it would otherwise reach the
  item. The item fades in over 0.15 s while the ball eases down out of its way
  (it used to jump 90 px in one frame, which could read as a sudden drop in
  confidence), and fades out while the ball eases back. With both settings off
  the strip is today's, pixel for pixel.
* **On your 1 Oct drive** (49 min) it would have shown 2 planned stops and 4
  curves, nothing else. The red light on Bringelly Rd: `20 m` grey 4.4 s before
  you stopped, counting down a metre at a time to `2 m` - grey because you were
  driving; engaged on the model's plan it is white. Creeping in the Pyrmont queue
  at 13 km/h: grey `11 m` down to `6 m` (you did not stop: the plan is the
  model's intent, not a promise). The plan touching a stop for one frame 11 s
  later, engaged: ignored. The Northern Road curve: a left arrow, `50`, `45`,
  `40 km/h`, gone the moment you pressed the gas, then `40` to `30` in the
  turn. On the motorway at 84 km/h, through a lane change, where it used to
  show `87 km/h` (the target was your own speed): nothing.
* **On route 110** (before the VSA fault), engaged at a light with no car ahead:
  white `40 m`, counting down to `1 m` without a gap. Before this fix it went
  blank for 0.65 s at `7 m` - a car stopped 8-12 m past the line came and went on
  the radar.

**Everything is a setting**, in sunnylink under **Visuals → HUD** (shown for a
comma 4 only), all on by default: Speed and Speed Limit, Next Lower Limit
(Off / Bar / Distance / Both), School Zone Lights, Electronic Sign in Variable
Zones, Stop Timer, Compact Take Control Banner When Stopped, Show the Limit
in the Confirm Prompt, Planned Stop and Curve Speed. Changes show within a second
(the screen reads them once a second, never per frame). **All of them off is
today's screen, pixel for pixel** - checked over eleven real moments of your
1 Oct drive and in the tests.

* **What it never covers:** the confidence ball itself (only held under the
  right rail's item), the driver-monitor icon (except under
  the stop banner), the wheel, the lanes. The whole group fades out while any
  alert is up and comes back only once the alert has faded away, so the two never
  overlap; and the speed hides for the 2.5 s the stock "MAX" number shows after a
  set-speed change, so two big numbers are never on screen together.
* **Only what is true:** the sign is the limit sunnypilot is using (grey digits
  while it is only holding the last one, as sunnypilot's own sign does); the
  school lights and the electronic look appear only when that limit IS the NSW
  limit (NSW Speed Zones on Live, the road matched or dead-reckoned - never an
  ambiguous match); the next limit is only what the map published.
  **In a tunnel, while the position is dead-reckoned, there is no next limit:**
  mapd deliberately does not publish one there (it reported drops that never came
  on your tunnel passes). The mockup showed one, from the logged look-ahead; the
  build does not.
* **Not built, as you decided:** no left-side bar, no traffic lights or light
  colours, no stop or give-way signs (from the map or otherwise), no following
  distance, no lane-change item - the right rail is the planned stop and the
  curve, and nothing else. The car-ahead icon is dropped, as you asked. The
  mockups' "set 70" line (set speed below the limit) was not among your picks and
  is not built.
* **What does not change:** no alert's text, sound, priority or timing; nothing
  that drives the car. The screen only.
* **Tests:** 71 (the rules, the settings and their once-a-second read, the
  banner through two stops in the order the screen really calls it, the right
  rail's rules - where the plan stops, the debounce both ways, white vs grey, a
  car inside the stop and the parked car past the line, standstill, the number
  only counting down, the curve states, the curve only while the car is slowing
  for it, its 5 km/h steps, the gas and the brake, priority, each toggle, every
  message missing or stale - sunnylink, the markers, the icon - including that
  git holds the PNG itself) and 24 that draw the real onroad view in a headless
  window per state - including "every setting off = the stock pixels", "an
  alert's own setting off = that alert as stock", two stops in one drive, an
  alert fading out, an hour-long stop, the gateway icon replaced by an LFS
  pointer, the rail's item only in the ball's strip, fading in and out, the ball
  held under it and back after, and "Planned Stop and Curve Speed off = the
  stock strip".
* **New params:** `HudSpeedCluster`, `HudNextLimit`, `HudSchoolZoneCue`,
  `HudVariableLimitSign`, `HudStoppedTimer`, `HudStoppedBanner`,
  `HudConfirmLimit`, `HudPlannedStop`, `HudCurve` (backed up with your settings).
  The first start after this update rebuilds, as any `params_keys.h` change does.
* **Watch on the next drive:** the speed matches the dashboard; the next-limit
  sign appears only for drops and only in the last 15 s / 500 m; at every stop
  behind a car - the second and third too - the small banner, the timer
  counting, and the full prompt when that car moves off. A school zone in its
  hours, if your route has one. Driving yourself, at a red light with no car
  ahead: the planned stop grey and dashed, counting down. **Only once the VSA is
  repaired (DTC 32-11) and you use openpilot's speed control again - not before,
  as you decided:** engaged in experimental mode at a red light with no car
  ahead, the planned stop white and counting down; a sharp curve engaged, the
  arrow pointing the right way and the speed close to what the car slows to. The
  white stop and the curve cannot appear without openpilot driving the speed, so
  there is nothing to test there until then.

---

## 2026-10-03 — The VSA's own fault, named on screen; no engagement while the VSA holds it

**What this is for.** On 2026-10-01 the car's VSA (the ABS / stability-control
unit, the one that carries out openpilot's brake requests) faulted twice; Honda
i-HDS read DTC 32-11, "ABS solenoid valve malfunction"
(`S:/OP/incident-2026-10-01/REPORT.md`). openpilot showed "TAKE CONTROL
IMMEDIATELY / Cruise Fault: Restart the Car", and restarting is exactly what does
not clear it: the VSA keeps the fault across a key cycle and re-checks only once
the car is moving (route 113 cleared at 35.3 km/h). Until the module is repaired,
do not use openpilot cruise; this update makes openpilot say so, and refuse.

* **A live fault** (the VSA faulting while you drive): the disengagement is
  exactly what it was - upstream's `accFaulted`, from BRAKE_ERROR, still drops
  openpilot and MADS on the same frame. Only the words change: "TAKE CONTROL
  IMMEDIATELY / Stability Control (VSA) Fault", then a silent banner "VSA Fault /
  Brakes, ACC, CMBS degraded. Have VSA codes read", and a refused engagement
  reads "VSA Fault: Brakes Degraded". 30 s later the EPS escalates (it follows
  the VSA, EPS DTC 85-01); its "LKAS Fault: Restart the car" banner no longer
  replaces the VSA's for the rest of the drive. The VSA's words are there from
  the first frame of the disengagement: card now sends `carStateSP` before
  `carState`, and should the VSA's flag still reach selfdrived one frame late,
  its alert takes over upstream's "Cruise Fault: Restart the Car" from the next
  frame (no second sound).
* **A stored fault** (the lamps already on at key-on): openpilot cannot be
  engaged - neither cruise **nor MADS** - until the VSA clears it. The banner
  reads "VSA Fault Stored / Clears after driving above 35 km/h", and SET, RES or
  the LKAS button get "VSA Fault: Clears Above 35 km/h" (35 km/h is what route 113
  showed - one observation). The moment the VSA clears (half a second later on
  screen; a press inside that half second is still refused, the next one is not),
  everything is available again; nothing engages by itself, so press LKAS (or SET)
  once it has. The flag is set about half a second after openpilot's car process
  starts (about 2.6 s after key-on), well before openpilot finishes starting up
  (about 8 s on routes 111 and 113), so its first screen already names the fault.
  MADS is refused
  on purpose: the EPS refuses torque while the VSA holds the fault (and hard-faults
  30 s after key-on), and the board steers only above 51.5 km/h, well past where
  the fault clears - so there is no speed at which lateral could work with it
  stored. An already-enabled MADS is not switched off by it.
* **No "turn the car off and on" after the VSA clears.** The board reports the
  EPS latched while the VSA holds its fault (the EPS follows the VSA), and its
  0x70B can then go quiet for seconds; on route 113 the EPS-latch alert announced
  "Steering Fault / Turn the car off and on to clear it" as MADS engaged, 0.7 s
  after the EPS had cleared with the VSA. That alert is now hidden while the VSA
  fault is up and forgets any latch when it clears; a real latch afterwards is
  confirmed again from fresh frames and announced as before.
* **Sound:** one prompt per fault - "Stability Control Fault / Brakes, ACC, CMBS
  degraded" for 3.5 s - and none if the fault disengaged something, because then
  the disengagement alarm is the one sound. Banners are silent. A refused SET
  still gets upstream's refuse tone, per press.
* **In the logs:** `carStateSP.vsaFault` (faulting now) and
  `carStateSP.vsaStoredFault` (fault lamps on outside the start-up bulb check), on
  every route including qlogs; the events `vsaFault`, `vsaStoredFault` and
  `vsaFaultAnnounce` in `onroadEventsSP`; a stored fault also shows upstream's
  `carNotReady` in `onroadEvents` (that is the event that refuses the engagement).
* **The bits are provisional** (named from timing; no Honda DBC has them), so the
  rules are narrow: live = 0x1A4 byte 2 bits 2-3, or 0x1EA's inertial-invalid bit
  with BRAKE_ERROR; stored = 0x1A4 b3.3/b4.0/b6.0 (never seen in a start-up bulb
  check, nor anywhere outside routes 110-113) from the first frame, or b3.6/b3.7
  (which the bulb check lights) after a 5 s start-up window, for 0.5 s. 0x1EA's
  counter is not checked, so it can never cost openpilot `canValid`. Two lamp
  bits of this fault (b3.5, b4.1) are deliberately left out: they also sit on for minutes at a time on 45 earlier drives (June
  2026) while the VSA braked normally. Replayed over all 166 logged routes, only 110-113 set
  either flag. Nothing else on any car changes: every other Honda reads both flags
  False, and on every other car the new code adds nothing.
* **Watch:** a normal drive should never show any VSA text, and `carNotReady`
  should never appear in `onroadEvents`. If one does without the cluster's VSA
  lamps, report the route - that is a provisional bit misread.
* **Tests:** opendbc `test_vsa_fault.py` (33: real frames from routes 110, 111,
  112, 113, 10f and the other lamp state on route 69, through the real
  `CarInterface`, a stored start with card starting 2.1 s late, 10f's bulb check
  stretched past the longest seen; 0x1EA's counter; garbage and missing frames; a
  monitor that raises; other Hondas); sunnypilot `test_vsa_fault_alert.py` (47:
  the events, both state machines and MADS, the AlertManager path and its sounds,
  the onset with the VSA's flag one frame late, a live fault with CAN invalid,
  the EPS-latch alert across route 113's clear, the mici text fit, selfdrived's
  and card's wiring, the `CarStateSP` capnp/dataclass agreement). Details:
  `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 6.6 and 10.6.

---

## 2026-10-01 — Longitudinal (braking): a softer final stop; CRUISE_OVERRIDE stays 1

**The last moment of an openpilot stop should no longer grab.** Measured on the
45 stops openpilot completed by itself (routes 3e-103): the brake was already at
the full standstill hold (185 of the hold's 189 counts) when the wheels stopped,
while openpilot was asking for almost nothing (-0.16 m/s²). That is the jolt at
the end: 0.92 m/s² at the stop on average, 1.02 behind a stopped car. Now, while
the car is still rolling in the stopping phase, the brake is held at 125 counts
instead, plus about 17 per degree of downhill; 0.55 s after the wheels read zero
it rises to the usual hold in about a quarter of a second.

* **Where:** it rides on **Dynamic Longitudinal Learning** (the same toggle as
  the stop-release debounce and the gentle brake release), so with the toggle
  off nothing changes. No new setting.
* **What you will feel:** a gentler settle at the end of a stop, and a slightly
  longer final roll: about 0.1-0.2 m and 0.3-0.4 s more. That is an estimate,
  not a measurement - the next drive measures it.
* **What does not change:** the hold itself (189, byte for byte once it has
  risen), launches, all braking before the stopping phase, the brake pump (it
  still runs continuously on the final approach), the learners, and stock
  emergency braking (a lower openpilot brake can only let the car's own AEB
  through earlier). A stop that starts at a standstill gets no ceiling, and
  neither does one that starts faster than 1.2 m/s (about 4 km/h; your 45
  logged stops, and the 32 in the review replay, started at 1.08 m/s at most).
* **When it gives way early** (back to today's brake, rising 5 counts per frame):
  the wheels start turning again after reading zero; the car is not slowing
  (weaker than 0.25 m/s² for 0.4 s, counted only once the brake has sat at the
  ceiling for 0.3 s, so a stop that is still building pressure is not handed
  back); or 1.9 s after the stopping phase began, whatever the wheels are doing.
  So in the worst case it is gone 2.4 s after the stopping phase began (every
  replayed stop rose on `settle`, at most 1.82 s in). The brake pedal, the gas
  pedal and a disengage remove it at once, and the brake is 0 on the first frame
  after a disengage. If openpilot leaves the stopping phase before the car stops
  (to brake harder, say), the ceiling goes at once: it never limits that braking.
* **On your 45 logged stops** (the recorded signals replayed through the new
  code): 44 would have had the ceiling; the brake when the wheels read zero
  drops from 170 to 137 counts (median); 29 of the 44 are on a downhill, and the
  pitch the code uses matches the pitch once stopped (it is the road, not the
  car nosing down under braking); the wheel-zero signal never flickered.
* **New log line:** one per stop, `hondastop rise=<why> t= roll= still= entry=
  cap= ceil=` (`why` = `settle` normally; `moving`, `weak` or `max_roll` when it
  gave way early) or `hondastop end=left` when the stop ended first, or
  `hondastop skip=speed v=` when it started too fast to get a ceiling.
* **The pump question - answered, no change:** the pump is not overused. It runs
  28% of the time a brake command exists; stock ACC ran it 53%. Runs are short
  (median 0.66 s). A suggested "no pump below 30 counts" was dropped: light
  commands do brake the car, and it would have restarted the pump through its
  quiet period.
* **CRUISE_OVERRIDE stays 1 (you asked to remove it).** MVL *does* use it
  (`CC.longActive or CS.out.stockAeb`), and you drove that version in
  June-July. In the logs the bit makes no measurable difference to the VSA's
  braking or to BRAKE_ERROR - but sending 0 has only ever happened in short
  tails after a disengage, never during sustained braking, so removing it would
  be an untested change with nothing to gain. Every BRAKE_ERROR since June was a
  ~1 s silence on 0x1FA, not this bit: on drive 84 the panda was dropping a
  forced minimum brake (so nothing reached the car for a second); on b5-b8
  nothing was sent. The bit is now documented in the code, and a test pins it.
  (The other override bit you tried, `ACC_OVERRIDE_STOP`, was removed earlier,
  in `d9498a1a`; nothing of it is left.)
* **Also fixed:** a speed reading of NaN made the brake code raise, which would
  have meant no brake message and a BRAKE_ERROR a second later. It now holds the
  brake instead. Never seen on the road; found by tests.
* **Not in this update:** the "no coasting in the last meter" change for stops
  without a lead car waits until this one is measured, so each drive tests one
  thing.
* **Your part:** when stopping or stopped, take over with the brake or the gas,
  not the cancel button. Cancel drops the brake at once (the panda insists), and
  the car then creeps - that is the "rolls" in two of your logged cases.
* **Watch on the next drive:** deceleration at the moment the car actually stops
  (camera/IMU, not wheel speed) - target a median of 0.6 m/s² or less and 1.0
  for the worst tenth (today 0.92 and 1.53); no jerk spike in the 0.8 s after the wheels read zero; at
  least 3.5 m to a stopped car ahead (4.1 today); one `hondastop` line per stop,
  nearly all `settle`, and every `weak`/`max_roll` on a downhill or a slow brake,
  and any `skip=speed` a stop that really did start fast;
  no creep while held and no rollback at launch; no 0x1FA gap over 0.1 s; and
  BRAKE_ERROR only in the first frame after power-up. The first drive also
  carries the upstream sync's change to when the stopping phase starts (behind
  a stopped car it should now start at 0.80 m/s, about 3 km/h); no stop since
  the sync is in the logs yet.
* **Tests:** 28 new soft-stop tests (every timer, the hill term, the gate, the
  entry-speed bound, the 1.9 s bound counted from entry, random inputs, never
  raising); through the real controller it only ever lowers the brake, the hold
  is byte-for-byte today's, the learners never see it, other cars, the toggle
  off and a stop started at 1.5 m/s are bit-identical; CRUISE_OVERRIDE is 1
  on every brake message, the brake is 0 after a disengage, and at most one
  nonzero brake message follows a brake or gas press. Details:
  `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 7.7-7.8. Commits: opendbc `cf9ad7ad`
  (the NaN-`vEgo` guard), `c65d5033` (`CRUISE_OVERRIDE`), `8b00f3cc` (the soft
  final stop), `66de8d56` (`FORK.md`), `3a131bf3` (after review: the 1.9 s bound
  counted from entry, the entry-speed bound).
* **Review replay** (the real controller driven from 14 logged routes, 300 min,
  144 min engaged): no exception, no 0x1FA gap, CRUISE_OVERRIDE 1 throughout,
  brake 0 after every one of 453 disengages; the brake when the wheels read zero
  136 counts median with the ceiling against 173 without; the hold 2 s later
  189 in every stop, as today. Re-run after the review fixes: every CAN frame
  identical to the reviewed code in all five set-ups (1.8 million control
  steps), and the same 33 `hondastop` lines - no stop started above 1.2 m/s
  and none reached the 1.9 s bound.

---

## 2026-10-01 — Longitudinal (pedal): the measured gas law; the pedal and aero learners retired

**The comma pedal now asks for the throttle this car actually needs.** The old
law gave 1.4-1.7x too much pedal per m/s² at 22-72 km/h, so a request from a
roll first over-delivered (aEgo 1.34-1.42 against a target of 0.95-1.15 for the
first 2 s) and then sagged while openpilot's own controller unwound. The new
law (v2) uses the pedal response measured on 51 of your routes. It is on by
default; turn it off to get the previous law back exactly.

* **Where:** sunnylink, Vehicle → Honda Settings: **Measured Gas Pedal Law
  (2013-15 Accord)**. Offroad only; it applies at the next drive (the car reads
  it once at ignition). The comma 4's Settings → vehicle card shows which law
  the car will use (`v2`/`v1`), and the `hondadyn` log line says which one ran
  (`gaslaw=`).
* **What you will feel:** a softer, steadier pull from a roll at 20-70 km/h:
  for 1.0 m/s² at 72 km/h the pedal goes 0.742 → 0.467. The lunge-then-sag is
  what goes away; sunnypilot's controller adds whatever is really missing.
  Above about 60 km/h the cruise pedal is the measured one, a little lower than
  before (0.122 against 0.169 at 72 km/h).
* **What does not change:** every launch below 11 km/h (v2 *is* the old law
  there, to within float rounding); the hand-over between gas and brake below about 60 km/h
  (same offset, same slope through the gap - a steeper gap there would risk
  surging in stop-and-go); the point where the pedal reaches zero and where the
  brake comes on (the aero change below moves the latter slightly, with the
  learning toggle on); the brake command itself (identical under both laws); max
  accel (still 1.6 m/s² - raising it was dropped: the 1.6 clip only bound during
  launches that were already overshooting). Every other car keeps upstream's
  pedal law.
* **The self-learning pedal gain and aero factor are gone** (the "Dynamic
  Longitudinal Learning" toggle). The pedal gain could not save anything (its
  progress was reset at every ignition: six weeks moved it by 0.007) and was
  steered by openpilot's own integrator toward the wrong answer. The aero factor
  wandered 0.7-1.5 from drive to drive. **With the learning toggle on, this
  moves the brake-on point slightly:** your saved aero factor was about 0.80
  and is now 1.0, so light braking at 90 km/h gets about 5 counts less brake
  (the brake comes on at 0.27 instead of 0.21 m/s² of net decel). The brake
  learner and the hill term are unchanged.
* **Drive modes (ECON / D / S):** the car now knows which one it is in (S if
  the lever is in S, else ECON if ECON is on, else D) and has a per-mode pedal
  multiplier, all 1.0 today, so nothing changes yet. A change of mode fades the
  pedal over 2 s instead of stepping. There are only 87 s of engaged ECON and
  79 s of engaged S in a month of logs - not enough to fit anything - so the car
  now counts engaged time and steady-pedal samples per mode, in the `hondadyn`
  line (`slot=`, `modesec=`, `modeadm=`, `modetot=`) and on the comma 4 card
  (`D / ECON / S`, minutes). These are counted only while **Dynamic
  Longitudinal Learning** is on (the gas law itself runs either way). **Your
  part:** with that toggle on, drive at least 15 minutes engaged in ECON and 15
  in S, including gentle accelerations at 40-80 km/h.
* **On screen:** the comma 4 card that showed six "learned pedal gain" numbers
  (which could not move) now shows `gas law` - the law the car will use from
  the next drive, e.g. `v2 next drive` - with the brake gain, and the engaged
  minutes per mode. Another Honda (the law does nothing there) sees only the
  brake gain. sunnylink's Cruise page shows engaged seconds per mode instead of
  the pedal gains and the aero factor. **RESET** puts back the brake correction
  only; the minutes per mode are kept (they are a tally of the data, not a tune).
* **Params:** new `HondaElesysGasLawV2` (backed up, default on) and
  `HondaDynModeSecD`/`ECON`/`S`; `HondaDynPedalGain0`-`5` and
  `HondaDynWindFactor` are no longer registered (the old files stay on the
  device, unread). `parse_hondadyn.py` needs no change: the line lost `pedal=`,
  `pedalc=` and `wind=`, and the script skips missing fields.
* **Also fixed:** a fingerprint without the gearbox's `GEAR` signal made
  `CarState` raise (found by fuzzing; the real car always has it). And S is now
  confirmed: the gearbox reports `GEAR = 26` whenever S is selected (16,164
  frames on b1, dd and fc), so the comments that said it had never been seen
  are corrected.
* **Watch on the next drive:** openpilot's integrator (`uiAccelCmd`) at
  demand above 0.4 m/s² should move toward zero (it was -0.21 to -0.23 at
  43-79 km/h), and the first 2 s of a request should no longer overshoot the
  target. At cruise above 60 km/h it should average within about ±0.05; below
  60 km/h it will stay where it was (+0.06 to +0.19), because that offset was
  deliberately left alone until the brake-side work. Check that the pedal does
  not hunt at 15-55 km/h in stop-and-go, and flag any pedal command at or above
  0.9 (the highest seen was 0.761).
* **Tests:** 31 new gas-law tests (golden pedal at every breakpoint, continuity,
  monotonic, nothing below the brake-on point, identical to the old law below
  11 km/h, the gap never steeper, the 2 s fade bound, the modes, the setting,
  other cars bit-identical); the real `CarController` under both laws and with
  NaN/odd inputs (never raises, `0x1FA` every frame it should); the tuner
  checks rewritten for the retirement; 7 new settings checks. Details:
  `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 9.1-9.2. Commits: opendbc `d2d482ed`,
  `9e076b33`; sunnypilot `db3fa190f`, `d93559f21` (after review: the readout only on
  this car, "next drive" on the card, RESET keeps the minutes, `FORK(...)`
  markers).
* **Review replay** (same inputs through both laws, engaged pedal frames): below
  11 km/h identical (51,742 frames, largest difference 0); 11-22 km/h 0.75x the
  old law's mean pedal, 22-36 km/h 0.69x, 36-108 km/h 0.71-0.84x, above 108
  km/h 0.80x; the brake identical under both laws on every route. Open loop: what
  sunnypilot's own controller then adds is for the next drive to show.

---

## 2026-10-01 — lateral batch: the car's own torque numbers, an honest torque report, a 0.38 s delay fallback

**openpilot's learned steering strength was stuck on a floor borrowed from
another car.** torqued started from the 2018+ Accord's values (via
`substitute.toml`), so it could only learn a lateral-accel factor between
1.18 and 2.20, and it sat on 1.18 on every route from ed to 103. This car
is a different steering system (torque 1.0 = 2560 on `0x0E4` = 160 serial
counts at board authority 160), and torqued's own estimator sees a raw
factor of 0.67 in town to 1.47 on the highway, 0.79-1.35 filtered.

> **2026-10-03: the prior is now `[1.25, 1.25, 0.18]`** (opendbc `e49da403`),
> changed before this batch reached the car. Route 10f, a clean highway
> commute, puts the car's factor at 1.63-1.66 - the estimator from an empty
> cache, a fit on the wire and the controller's own correction agree - and
> 1.1's 1.43 ceiling pinned it there (chained after fc → fd → 103 the raw sat
> on the ceiling 63% of 10f). fc/fd/103 give 1.2-1.5, town routes 0.67-0.9.
> Highway commuting dominates the engaged steering, and a feedforward that is
> too strong at speed is the worse error, so the window now follows the
> highway: **0.875-1.625** (friction unchanged, 0.09-0.27). Town drives are
> clipped at the 0.875 floor by design.
>
> * Replay with 1.25 (the real `TorqueEstimator`, empty cache): fc → fd →
>   103 → 10f is valid 58 min in and ends 1.352 / 1.362 / 1.459, never at a
>   limit; 10f alone is valid at 18 min and ends 1.578, its raw above the
>   ceiling 69% of the time; town first (d5..e2 → fc → fd → 103 → 10f) is
>   valid at 23 min, sits on the floor through e2 and fc (0.882, 0.876) and
>   climbs back to 1.353 by the end of 10f.
> * Day one asks for about 5% *less* torque per m/s² than the 1.183 the car
>   sat on through 103 (10f ended at 1.302), not 7.5% more, so the
>   frames-at-160 estimate below (made for 1.1) should be a high bound.
> * Still one reset: the car goes from the substitute straight to 1.25.
> * Read the checks below with 1.250 / 0.875 / 1.625. A filtered factor
>   within 0.03 of 0.875 is town driving on the floor - now expected, not a
>   warning; within 0.03 of 1.625 is the highway pressing on the ceiling.
> * Details and the full replay table: `docs/fork/CAR-HONDA-ACCORD-9G-AU.md`
>   2.4.

* **Its own prior:** `[1.1, 1.1, 0.18]` in opendbc `override.toml`. torqued
  can now learn 0.77-1.43 (friction 0.09-0.27). Live learning stays on.
  That window holds every filtered value in the replays, but not every raw
  one: its floor clips up to ~24% of a town route's raw samples (d9, fc),
  its ceiling ~1% of a highway route's (fd). No ±30% window can hold
  0.67-1.47 (a ratio of 2.2 against 1.86), so 1.1 is a compromise.
  Day one asks for about 7.5% more torque per m/s² than today's 1.183; the
  board still clamps at 160.
* **One reset, on the first drive.** The new prior throws torqued's saved
  points away once, including points learned at authorities 40-200, when
  "torque 1.0" meant something else each time. Until it has enough points
  again (about 25-60 min of steering above 54 km/h in the replays) it uses
  the prior.
* **The offset survives the reset.** torqued's learned offset (-0.42 to
  -0.50 on every route) cancels about 0.4 m/s² of road crossfall. After a
  reset it restarted at 0, which would have meant a pull after every
  engagement on that drive. The car now carries -0.43 in its CarParams and
  torqued starts from it (`torqued.py`, `FORK(HONDA_ACCORD_9G_AU)`; every
  other car still starts at 0). It also survives EnforceTorqueControl and
  NNLC: their re-run of `configure_torque_tune()` would reset it to 0, and
  sunnypilot's `interfaces.py` (`FORK(HONDA_ACCORD_9G_AU)`) now keeps it.
* **Torque is reported only while the board steers.** While
  `linbusGateway.actuating` is false, `carOutput` reports 0 torque. torqued
  stops learning from frames nothing followed, no "turn exceeds limit"
  alert can fire, and the torque bar shows 0. What goes on the wire is
  unchanged (`0x0E4`, `torqueOutputCan`, the ramp).
* **Steering-delay fallback 0.58 → 0.38 s.** `steerActuatorDelay` is 0.18;
  lagd's fallback and the LagdToggle-off delay both add 0.2. lagd sat at
  0.58 for about 5 h (d7 to fd) before it relearned. Its learned 0.342 is
  kept. With LagdToggle off, Settings shows "0.18 s + 0.20 s = 0.38 s".
* **Unchanged:** full scale stays 2560 = 160 counts (nothing above 160 has
  been sent since d8), and the PID gains and friction threshold.
* **For the future:** any change to the board's `GW_LIN_AUTHORITY` or full
  scale must change the `override.toml` prior too. The prior is torqued's
  cache key, so changing it is what discards points learned at the old
  scale.
* **NNLC:** without the substitute line, its model lookup still picks
  `HONDA_ACCORD.json` (fuzzy). NNLC is off on this car.
* **Check on the next drive:**
  * `lateralTorqueParameters` starts at 1.100 / -0.43 / 0.18 with 0 points.
    It may not be valid by the end of the first drive.
  * The filtered factor within 0.03 of 0.77 (below 0.80) means town driving
    is pressing on the floor (the replays' lowest was 0.793); within 0.03 of
    1.43 the same on the highway (highest 1.347).
  * `carOutput.actuatorsOutput.torque` is 0 whenever `actuating` is false,
    and otherwise matches `carControl.actuators.torque`.
  * No pull at takeovers. The integrator should stay near +0.05 on
    straights.
  * `lateralDelay` reads 0.342 "estimated", or 0.38 if ever unestimated.
  * More frames at 160 are possible, because a lower factor asks for more
    torque: the review replay estimates 0.9-1.0% of actuating frames above
    54 km/h today, 1.0-1.6% on day one. Still nothing above 160.
* **Commits:** opendbc `d288838a` (prior + seed), `bddc6395` (report +
  comment), `1f7c50fd` (delay), `f60e7704` (review: window wording,
  sturdier tests). sunnypilot `c4a6262f7`, `7e9ffe547`, `71b071a2c`,
  `4e606d119` (the seed survives EnforceTorqueControl / NNLC).
* **Tests:** 17 new in `test_elesys.py` (prior, seed, report incl. `0x704`
  through the real CarInterface, the 2560 scale, the delay, the brake
  release step on the wire), `test_torqued_elesys.py` (11),
  `test_latcontrol_reported_torque.py` (7), `test_lagd_elesys.py` (5).
* **Replay (review):** the real CarInterface over 11 routes (4.07 h) put
  byte-identical sends on the wire to the parent commit on every frame, with
  no exceptions, no `0x1FA` gap and no nonzero brake after `longActive`
  dropped.

---

## 2026-10-01 — "restart the car" when the EPS latches; download progress; the rollback branch is gone

**When the EPS latches until key-off, the comma 4 now says so.** Once per latch,
with one short sound: **"Steering Fault"** / "Turn the car off and on to clear
it". After that, a silent **"Steering Off Until Restart"** every 5 minutes for
as long as the latch lasts.

* **Only a warning.** It drops nothing and blocks nothing: cruise keeps working,
  you can still engage, MADS stays as it was. It just tells you steering is gone
  until the key cycle.
* **Never over driver monitoring.** If the EPS latches while the orange "Pay
  Attention" / "Touch Steering Wheel" is up, that stays on screen with its sound;
  the steering fault waits its turn.
* **When it fires:** the board reports the key-cycle latch (`RETRY_IN 255`) for
  1 s of fresh frames. Not on the 3 s "no acknowledgment" hold, not on a
  transient, not when the board is silent, and never on another car.
* **Once, not twenty times.** After a real latch the board's `0x70B` frame keeps
  dropping out for 0.1-25 s at a time (fc, fd: about twenty gaps a drive), and a
  dropped frame reads as "not latched". Only fresh frames count either way, and
  it re-arms only after 3 s of fresh "not latched".
* **It waits until it can be seen:** warnings show only while openpilot or MADS
  is engaged, so a latch confirmed while you are disengaged is announced when you
  engage again.
* **Checked against your drives:** fd, fc, f2 and ed each announce once, about
  1 s after the latch; 102 and 103 raise nothing.

**Settings > software shows what a download is doing.** After you tap "download
update", and also while the comma downloads an update on its own in the
background, the button reads "downloading..." plus one of:

* **code NN%** - fetching the branch, git's own object count. Usually seconds;
  an update under 100 objects shows no number.
* **checking out** - checkout and submodules. No number, because git gives none.
* **os update NN%** - only when the update brings a new AGNOS. The share of the
  image downloaded, by size: the system image is 99% of it, so the number now
  moves with the download. MVL's version counted the seven OS partitions the
  same, so the six small ones raced it to ~91% in seconds and the system image
  crawled the rest. Still no time estimate - nothing here measures time.

Each meter's 100% is always shown, and the progress is gone before "finalizing
update..." as before. The git output still goes to the log.

**"download update" stands out** when an update has been found and is waiting
for your second tap: a green disc behind the icon, a brighter button and white
text (from MVL's branch). Only in that state, so it never mixes with the
progress text.

**The rollback branch `pre-upstream-2026-09` was deleted** from
SoRadGaming/sunnypilot and SoRadGaming/openpilot on 2026-10-01. It could not have
been installed from the device: the updater now on the car reads the AGNOS
manifest at `openpilot/system/hardware/comma/agnos.json`, and that branch is
AGNOS 18.4 with the manifest somewhere else, so the update would have failed. The
upstream sync proved good, so nothing needs it. `pre-*` snapshots still work the
same way; `docs/fork/README.md` now says to check a snapshot can install before
calling it a rollback (a moved `agnos.json` cannot install; a different AGNOS
version alone installs by downgrading AGNOS).

* **Tests:** 19 for the alert (debounce, the fc/fd gaps, deferral, reminder,
  warnings only, never over driver monitoring, the text against the comma 4's
  alert renderer with the real fonts, selfdrived's wiring), 27 for the progress
  (throttle, labels, a real `git fetch`, the AGNOS weighting through `agnos.py`,
  the real `Params`, `fetch_update()` itself), 6 that drive the real button in a
  headless window.
* **New param:** `UpdaterDownloadProgress` (cleared at every manager start).
  The first start after this update rebuilds, as any `params_keys.h` change does.

---

## 2026-09-30 — the fast-wheel takeover is now a setting

**Turning the wheel fast turns MADS steering off (not a pause), and you can now
switch that off or change how fast "fast" is.** Nothing changes unless you
change it: it is on by default at 200°/s, exactly as before.

* **Where:** sunnylink, Steering → MADS Settings: **Turn Off Steering on a Fast
  Wheel**, with **Fast Wheel Threshold** under it. On the comma 4, Settings →
  vehicle: **off on swerve** (ON means a fast wheel turns steering off) and
  **swerve at** (the threshold; tap to step through).
* **Thresholds:** 150, 200 (default), 250 or 300°/s, held for two frames
  (20 ms). In two hours of logged lane keeping on this car the wheel never went
  above 151°/s, so 150 can turn steering off on a hard curve. Any other stored
  value reads as 200.
* **Off:** a fast wheel alone never turns MADS off. The gateway's own pause on
  driver torque is unchanged - a swerve during an override just pauses and
  resumes like any other override.
* **Applies at once,** onroad or off, within 0.1 s; no reboot.
* It still applies to every car, not just this one (`docs/fork/README.md`,
  "Where the fork does not follow those conventions").
* **Tests:** 23 new MADS cases (every threshold, off, a raised threshold
  during an override, live changes, bad values) and 15 settings-agreement
  checks. `LKAS-GATEWAY-PROTOCOL.md` section 9 "Other" has the details.
  This is also the takeover's first changelog entry: it arrived with
  `35622a994` and had none.

---

## 2026-09-30 — map updates from offroad mode, and from sunnylink (`1b05c21d8`)

**Parked with the car on and the device in offroad mode, the update buttons said
"car must be parked".** They checked the ignition, not whether openpilot was
driving. Offroad, openpilot controls nothing, so:

* **Update now** (NSW zones and OSM maps) works whenever the device is
  offroad, ignition on or off. Onroad it answers "offroad only".
* A newly downloaded NSW index is loaded whenever the device is offroad.
  Going onroad cancels a check that is still running.
* **Automatic** downloads are unchanged: parked with the ignition off, on
  unmetered Wi-Fi or ethernet.
* **sunnylink**, Cruise → speed limit: **Update NSW Speed Zones Now**,
  **Update OSM Maps Now** (offroad only; the switch flips back once the
  device takes the request) and **Update OSM Maps Weekly**.
* An OSM request is checked before the download starts: refused onroad, with
  no region set, or while one is already running. A request made long after
  the weekly update was no longer dropped.
* Driving off does not stop an OSM download that has already started - mapd
  has no cancel. The docs and the sunnylink text say so.

## 2026-09-29 — NSW speed zones: the car's limit from Transport for NSW data

**In NSW the speed limit now comes from Transport for NSW's own speed-zone data**
(CC BY 4.0) instead of OpenStreetMap. It covers every public road, local streets
included, plus school zones with their real times and school days. OSM is still
used outside NSW and wherever NSW has no match. It is live by default. Switch it
with **NSW Speed Zones** in sunnylink (Cruise): Live, Log only or Off.

* **Coverage on your 25 drives:** NSW has a limit 96% of the time with GPS,
  against OSM's 70%. On roads under 60 km/h it is 93% against 46%. Where both
  have a limit they agree 95% of the time.
* **Tunnels:** the car follows the tunnel on wheel speed and heading when GPS
  drops out. City-bound through the M4 East it reads 90, then 80 on the Rozelle
  ramp. No street limit from above ever reaches the car underground.
* **Your rules:**
  * a Variable (peak) limit drawn over a fixed one gives the higher, normal speed;
  * on-ramps keep the road's own zone until the merge.
* **Data:** a weekly GitHub Action in `SoRadGaming/openpilot` builds it from
  TfNSW's files and publishes it as the `nswzones-latest` release (about 22 MB).
  The comma downloads it by itself, parked on Wi-Fi. Settings → maps shows its
  date and has an update button.
* **Checks:** a weekly build that would RAISE limits stops for a human look.

Replayed over all 25 drives: 128 prompts. With OSM only it is 72; the car
recorded 141. Most of the new prompts are real limits on streets OSM had
nothing for. The list of things to check on the road is at the end of
`docs/fork/NSW-SPEED-ZONES.md`.

## 2026-09-29 — speed limits: no carried limit, no tunnel prompts, a maps page (`2e7866503`)

**The map speed limit was wrong off motorways.** Across 25 drives there were 141
prompts:

* 43 were the previous road's limit, carried onto a road with no limit in OSM
  for the rest of the drive.
* 26 came from the M4 East, M8 and Iron Cove tunnels. GPS drops out there, and
  mapd matched the streets overhead (40 or 50 while you were doing 80).
* Behind both, the comma 4's OSM tiles had never been refreshed. It has no OSM
  screen.

Now, with **Strict Map Speed Limits** (sunnylink, cruise → speed limit; on by
default):

* **Only a real new limit prompts.** A road with no limit, or the same limit
  coming back after a junction, is not one. A carried limit is dropped after
  10 s on a road with no limit and good GPS, and engaging on such a road does
  not prompt for it.
* **Tunnels hold the limit you entered with.** It holds until the map has found
  the road again: the road name changes, or 10 s pass after GPS returns.
* **Settings → maps** shows when the OSM maps were last updated, and has an
  update button (parked only; keep the device on until it finishes). With
  **update weekly** on, which is the default, it refreshes by itself when
  parked on wi-fi. A phone hotspot counts as wi-fi unless it is marked metered.

Replayed over all 25 drives: 72 prompts instead of 141. Every prompt from the
road you were actually on is kept, none are carried over and none come from the
tunnels. Every tunnel exit prompts the correct next limit. With the setting off
the car behaves exactly as before.

Still coming: NSW's own speed-zone data (every road including local streets,
plus school zones), replacing OSM in NSW. That is being built and tested
offline first.

## 2026-09-27 — upstream sync: sunnypilot `a5f44653d`, opendbc `f95f996f`

**The fork now runs on current upstream sunnypilot (openpilot 0.11.2, sunnypilot
2026.003.000).** Merged as sunnypilot `6b6b2b31e` / `d1a14edcb` and opendbc
`8bd6e314`, plus the fixes from the review after it. The rule was: take upstream,
but keep this car's behaviour wherever upstream changed it; every other car gets
upstream's. The full list, item by item, is `docs/fork/UPSTREAM-2026-09.md`.

**First boot: do it parked, with internet.**

* The OS updates from AGNOS 18.4 to 19.7 before openpilot starts. It downloads
  first and blocks startup until it is done.
* The internal panda is reflashed (new firmware). Honda safety is unchanged.
* The first start builds the new tree, which takes a while.
* Expect the Experimental-mode confirmation page once, if you turn it on.

**Kept as it was on this car:**

* **Stopping.** Upstream now starts a stop below 0.3 m/s and ramps at 1.0 m/s³
  for every car. This car keeps 0.8 m/s and 0.8 m/s³, the tune its red-light
  holds were proven on, plus the stopping-exit debounce. The one difference: the
  0.8 m/s is now checked against the measured speed rather than the plan's.
* **Engage speed.** Upstream lets gas-interceptor Hondas engage from standstill;
  this car keeps its 19 mph minimum.
* **Lane changes.** Upstream's new lane-change logic, with the firm-or-held nudge
  on top. A brush still does not start one.
* **The gateway.** Steering commands, `0x500`, the board telemetry and the
  in-app board update are unchanged. A replay of route `00000103` sent
  byte-identical `0x0E4` and `0x500`.

**What changes for you:**

* **MADS in Pause mode:** with the brake held, lateral stays paused until you
  release it, including at a standstill and at the end of a board override
  (upstream's brake guard). Route `00000103` was recorded with "remain active",
  where this changes nothing, so check which mode the device is set to.
* **Turning MADS on while you have the wheel** during a board override now
  starts it paused. Before, it was active for one frame first.
* **Curves with vision curve slowdown on** can brake harder: up to 1.2 m/s²
  where the old planner used about 0.35–0.6. The set speed is now handled outside
  the longitudinal MPC and jerk-limited. Road-test it.
* **Steering delay:** the delay learner drops what it had learned (0.38 s),
  uses 0.58 s until it has relearned, and now learns only above 80 km/h. Accepted
  as a one-time relearn: the first stretch of highway puts it back near 0.38 s.
* **Driver monitoring:** camera alerts at 5 / 8 / 13 s (were 3 / 5 / 11), wheel
  alerts at 5 / 15 / 25 s (were 15 / 24 / 30), a new sound on the first "Pay
  Attention", and timed lockouts (1, 5, 15, 30 min) after two red alerts or one
  ignored for 5 s. The DM slowdown starts 5 s later. The right-hand-drive face
  icon is no longer mirrored.
* **Sounds:** engage, disengage and refuse are re-recorded, and the warning
  alerts play new files.
* **Screen:** a screensaver shows for 5 minutes before the display turns off
  offroad. Settings are reordered: models, vehicle, gateway, then the rest, with
  a new software tile.
* **If the screen UI crashes it no longer restarts by itself;** that needs a
  reboot.
* **Log names:** `livePose` is `deviceMotion`, `liveDelay` is `lateralDelay`,
  and so on. Old logs still decode; the parquet scripts in `S:/OP` keep working
  while `S:/OP/cereal` keeps the old schema.
* Routes start about 0.5 s sooner, and remote "Take Snapshot" is gone.

**Check on the first drive:**

* No ACC/CMBS fault on the first ignition, and Settings > gateway shows the
  board's hash.
* Stops latch at about 0.8 m/s and hold without rolling; no engagement below
  19 mph.
* During a board override (`0x70B` reason 4) MADS stays paused and
  `latActive` stays 0 on every frame.
* In Pause mode, a held brake keeps lateral paused until released.
* A brush with the blinker on does not change lanes; a firm tug or a held push
  does.
* Curve entries with vision curve slowdown: how hard it brakes.
* Steering feel before and after the first stretch above 80 km/h (the delay
  learner).
* No `*Lagging` events in the first routes (a new CPU cap applies onroad).

The fork's own code moved with upstream's layout: every sunnypilot path is now
under `openpilot/`, and the board image is
`openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin` (the firmware
repo's `bundle_appslot.py` finds either layout since `862540c`). All fork tests
are `unittest` test cases now, run with `tools/test_runner.py`, and pass with
upstream's whole suite.

## 2026-09-27 — MADS stays paused through the whole board override (`2cfcd3c6a`)

**While you had the wheel, openpilot kept asking to steer on every other frame.**
When the board hands the wheel back (`0x70B` reason 4, driver override), MADS
pauses: lanes stay dashed, lateral goes inactive. On the next frame the generic
silent-resume saw nothing holding the pause (no brake, no gear event) and
re-enabled it, and the frame after that the gateway paused it again. Route
`00000103` from t=58.5: 31 s of override with `carControl.latActive` reading
`0101…`. The steering was not affected, because the board ignores `0x0E4` while it
has let go, but openpilot's request and the board's view disagreed the whole time.

* The pause now holds for as long as the board reports the override, the same
  way a held brake holds a brake pause.
* When the board lets go, MADS resumes through the normal path, so a brake still
  held in Pause mode keeps it paused until you release it. That is also what
  upstream's new brake guard expects.
* Releasing the brake in the middle of an override no longer resumes.
* A fast-wheel emergency takeover on the same frame as the override turns MADS
  **off**, as designed. Before, the pause beside it turned "off" into "paused".

This is safe with the Stage 10 board image: its override latch is compiled out
(`GW_DRIVER_LATCH 0`), so reason 4 ends on your hands and the release dwell alone.
The board does not wait for openpilot to ask again.

Eleven tests in `sunnypilot/mads/tests/test_mads_gateway_pause.py`. Five of them
fail on the old code.

## 2026-09-27 — a lane-change nudge must be firm, or held

**Just touching the wheel with the blinker on started a lane change.** The
nudge threshold is 600 counts (lowered from 1200 on 2026-09-16 so weak
deliberate nudges at cruise work), and the lane-change logic confirmed on a
single model frame: route fc t=768.8 went on one 10 ms reading of 645.

Across 115 blinker windows on routes d9..fd, the pushes that confirmed lane
changes split two ways — **firm tugs that are often brief** (3765–4773 counts for
only 50–100 ms) and **light pushes that are held**. The brushes are the ones
that are both light and brief. So now, for this car only:

* **firm** (1500 or more in the wanted direction) confirms at once, as before;
* **light** (over 600, under 1500) must be held for 4 model frames — 150 ms.

Run through the real `desire_helper` on 92 recorded windows: 37 confirm at the
same instant, 29 later (mostly 50–150 ms, three at 0.4–0.9 s — weak pushes
hovering at the threshold), and **3 no longer confirm: peaks of 693, 740 and
903 held for one or two frames**. None confirms earlier, none confirms where the
old rule did not, and every other car keeps upstream behaviour (the rule is
keyed by fingerprint in `NUDGE_FIRM`).

`steeringPressed` stays at 600: driver monitoring and the lateral controller
should still see a light hand. Only the lane-change confirmation changed.
Eight tests in `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`.

## 2026-09-27 — board `d995bc95` · the pothole latch

**LKAS faulting until restart after a big pothole** is the EPS's state-8 latch,
and both reported cases were the **same stretch of motorway**, 127 m apart in
opposite directions — fixed potholes on the regular route. The EPS's motor
torque swings hard against the column, it goes silent for 60–70 ms, and it comes
back refusing LKAS for the key cycle. Nothing the board or openpilot did started
it, and nothing passive clears it.

Board firmware `d995bc95` fixes the one thing the board did wrong inside every
such silence — its request counter froze while the EPS kept counting, feeding it
2–5 wrong-parity requests — as an experiment; see the board's CHANGELOG and
`docs/EPS-FAULT-STATES.md`. Every answered frame is unchanged. The next pass over
those holes with LKAS engaged is the test. **Until then, take over or hold the
wheel through that stretch.**

It goes in with the next board update, which also runs the per-phase idle
vibration measurement.

## 2026-09-24 — the update "hum" is the engine, and the next update measures it

**The trace worked.** It came out in route f6 with no SSH and said the wheel did
not move during the flash (0.6–0.7°, one 0.1° step). Together with f5 (same key
cycle, before) and a phone video of the whole update, the hum turned out to be
**engine firing vibration**, not the EPS: the V6's 3rd order (~44 Hz) and its
harmonic (~88 Hz), pitch-locked to idle rpm, **2.5× stronger after the flash**
at matched rpm and load, ~24× louder in the cabin, held until the engine was
restarted. The EPS neither makes nor amplifies it (column-to-body ratio 16.7 vs
16.5) and reports nothing. Full write-up: `docs/CAN-UPDATE.md` in the board repo.

The video puts the step **1.7 s after the knock, as the data stream begins**,
with rpm unchanged. So the update is still the suspect — as something that
knocks an engine vibration or noise control into a fallback — but which step of
it could not be told apart. **Workaround: cycle the ignition after an update.**

The flasher now makes every update answer that itself:

* **5 s baseline** before the knock, and **6 s after** the post-reboot HELLO
  through the app start and K2 re-split, which nothing recorded before.
* **An 8 s hold** with the bootloader session open and no data, between INFO and
  BEGIN — so the knock and the data stream are 8 s apart, not 1.7 s. INFO every
  2 s keeps the session alive; the board is in bypass throughout.
* **Engine rpm from `0x17C`**, and the accessory-load bit (`0x1A6` byte 2
  bit 6). Not `0x158`'s ENGINE_RPM field, which reads ~8% low in Park — the
  torque converter — and once produced "it cannot be the engine".
* **The firing-order amplitude per phase**, fitted in the column torque with its
  phase tracked from rpm, on the EPS's own 10 ms grid rebuilt from the `0x18F`
  counter. USB-batch receive times jitter ~10 ms, which would make a 44 Hz fit
  meaningless. Replayed over real f5/f6 frames it gives 2.10 and 5.02 counts
  against the independent 2.02 and 5.04.
* **Panda CAN error counters** per bus at every step.

**Confirmed by the owner: the car has active noise cancellation and VCM active
control engine mounts** — and the update left both fingerprints: structure up
2.5× (the mounts), cabin sound at 88 Hz up ~24× (ANC). So each phase also carries
a census of every car-bus ID against the pre-knock baseline: `lost` names any ID
that slowed below 80% or vanished, `new` any that appeared (something answering
our frames). On real f5→f6 traffic, 72 IDs, it reports nothing.

It arrives in the next route's `eps-lkas flash trace` line as a `phases` table:
`pre reset enter hold erase data finish copy post`, each with `rpm`, `ld`,
`o3` (and an off-order `ref`), `sd`, `ang` and error deltas `e0`/`e2`. **The
phase whose `o3` steps up is the trigger.** The update sits at 0% for ~13 s
before moving; that is the baseline and the hold.

## 2026-09-23 — board `d43b12aa` · the flash now records what it does to the steering

**A driver reported the EPS humming and the wheel moving slightly, left and
right, while the board was being reflashed over CAN in park — hands off the
wheel.** Nothing can say what happened, because nothing was watching: the
flash runs offroad, loggerd is not running, and the routes either side of it
end and begin outside the window. It is a 47.25 s hole with no data in it.

Meanwhile the panda was receiving the entire car bus for all of it — about
100,000 frames on bus 0 — and `_pump` discarded every one. The flasher now
keeps two of them:

```
0x156 STEERING_SENSORS   STEER_ANGLE (0.1 deg) + STEER_ANGLE_RATE, 100 Hz
0x18F STEER_STATUS       STEER_TORQUE_SENSOR (column torque), 100 Hz
```

kept as raw timestamped bytes — no decoding in the receive path, no opendbc
import in a module that has to load on a bench. Bounded at 24,000 frames
(~2 min of both), captured in a `finally` so a failed flash keeps its trace
too.

**And it comes out without SSH, because not everyone has it.** The first cut
wrote a CSV to `/data/eps-lkas-trace/` and stopped there, which is useless to
anyone who cannot fetch a file off the device. The trace now travels the way
everything else does:

```
flasher  decodes it into a summary + a 10 Hz series   (summarise_trace)
hook     puts that in EpsLkasFlashTrace, PERSISTENT   (survives the restart)
card     emits it to cloudlog on the next drive, once, then clears the param
```

So it lands in an ordinary route. Flash, drive, send the route — nothing else.
The CSV is still written for anyone who does have SSH.

The summary states the discriminator outright rather than leaving it to be
re-derived: `wheel did not move`, `moved, with column torque - looks like a
hand on the wheel`, or `MOVED WITH LOW COLUMN TORQUE - something drove the
column`. The decoders are cross-checked against `carState` on route f1 — 2.5°
against `steeringAngleDeg`, −74..0 against `steeringTorque` — and a test pins
both.

`EpsLkasFlashTrace` is PERSISTENT on purpose: `CLEAR_ON_MANAGER_START`, which
the other three flash params use, would throw the trace away at exactly the
wrong moment. It is written as a **dict**, not `json.dumps`, for the reason
that killed `card` once before.

Those two also separate the two candidate causes without a scope: **angle
moving while column torque stays in the low hundreds means something drove the
column; torque in the thousands leading the angle means a hand on the wheel.**
There is no third instrument available — `0x1AB STEER_MOTOR_TORQUE` does not
exist on this car, and the EPS's own motor torque is reported only over the
LKAS serial link, which the board stops mirroring the instant it enters its
bootloader.

**What is already ruled out.** The comma cannot reach the EPS: torque reaches
it only over the 12 V LKAS serial line, and the OBD-C connector carries CAN,
SBU and VBUS only. `SAFETY_ELM327` *closes* the harness relay rather than
opening it, so the stock camera stayed connected to the car bus throughout,
and the panda's relay does not click during the flash at all. And the key
cycle spans the whole window with neither EPS key-cycle latch set, so whatever
the noise was, it did not fault the EPS.

The board half of this is firmware `d43b12aa`, which also fixes a real RULE-2
exposure in the knock handler — it used to drop relay K2 while both LKAS
transmitters were still enabled. See the board's own CHANGELOG, and
`docs/EPS-FAULT-STATES.md` for why "the random LKAS error" is a different
fault from the two the code was hardened against.

## 2026-09-22 — `6a4f1f5`, board `577a723e` · the lane graphic, and how to tell whether the fix landed

**The dash lane graphic was not tracking sunnypilot.** Two rules were wanted:
the graphic follows lateral being *enabled*, and it goes solid only while the
car is actually steering. The board already implemented exactly that. The
signal it was given did not mean what the name said.

`carcontroller.py` was packing

```python
lat_ready = steering_available or CC.latActive
```

and `steering_available` is `cruiseState.available and vEgo > minSteerSpeed` —
cruise main on and moving faster than the minimum. That is true with **MADS
off**, so the graphic came up whenever the car was rolling with cruise main
lit, which is most of a drive. Now:

```python
lat_ready = CC_SP.mads.enabled or CC.latActive
```

`mads.enabled` is the MADS state machine being in one of its enabled states,
which is what "lateral is enabled" actually means. No firmware change was
needed for this — the board reads the bit in one place and the rule there was
already right.

What to expect:

| | graphic |
|---|---|
| MADS off, cruise main on, moving | blank |
| MADS on, not steering | dashed |
| board steering | solid |
| driver overrides, assist hands back | dashed, not blank |
| assist without override | stays solid |

**And a bit to prove it, because two drives could not.** The board's decision is
`op_lat = wants || (sp.has_req && fresh && sp.lat_ready)`, so everything past
the first term depends on `0x500 SP_HUD_STATUS` arriving at all. If it never
arrives, `op_lat` collapses to `latActive` and fixing `lat_ready` changes
nothing — and the two cases produce **identical logs**. Both attempts to tell
them apart from routes `ed`/`ee` came back inconclusive:

* `0x70B REASON` — the only 0x500-derived codes are `integrator_too_large` and
  `brake`, and neither occurred. Absence of a reason is not absence of a frame.
* `0x70B AUTHORITY` vs `0x707 AUTHORITY` — both 160, and they always will be:
  `hondacan.py` sends `SP_HUD_MAX_TORQUE = 0` and the board only folds
  `max_torque` when it is non-zero, so the two can never differ.

Board firmware `577a723e` adds **`GW_BUILD.BUILD_SP_FRESH`** (`0x70F` byte 0
bit 5): 0x500 is arriving and fresh, sampled when the beat is sent. Over a
drive, never-set means never arrived. `route_flatten.py` says so outright
rather than leaving it to be re-derived.

**This needs the board reflashed** — Settings → gateway → update firmware. The
card will read `to 577a723e`.

## 2026-09-18 — `ef4f294` · MADS follows the board's hand-back

When the gateway releases the wheel on driver torque, MADS now turns off, exactly
as if the LKAS button had been pressed. The driver re-arms it when they want it.

The board *has* to release — this EPS latches when it is overpowered while
commanding. But `latActive` stayed true, the command kept going out and the
cluster kept showing lateral engaged, so the driver was told the car was steering
when the wheel was theirs. On routes dd and de the board was released and
openpilot was still asking on **100% of frames above 110 counts** of driver
torque.

Trigger is `carStateSP.linbusGateway` reason 4 (driver override) with the grant
valid and not granted. Not a hair trigger: the board debounces first, and the
episodes are real — 28 on dd and 21 on de, median **7.1 s** and **8.7 s**, and
not one under a second on either drive. `granted` is false whenever `grantValid`
is, so an old or silent board can never fire it; `present` keeps it off every
other car. `selfdrived` gained `carStateSP`, published unconditionally at 100 Hz.

## 2026-09-17 — `56a4043`, `7da0d8c` · the latched torque sensor

**The EPS stops updating `STEER_TORQUE_SENSOR` (`0x18F`) while it is under LKAS
control.** The frame keeps arriving at 100 Hz with a rolling counter and a valid
checksum, but the torque bytes hold whatever the driver was doing when the
gateway engaged. Routes dd/de/df: frozen for up to **946 s** while the wheel
moved −13.5 to +7.4°, on **41–69% of each drive**, `canValid` 1.00 throughout.

Two consequences, both fixed:

* **`carStateSP.driverTorqueStale`** — true means nothing may infer driver intent
  from `steeringTorque` this frame. `desire_helper` honours it, because a latched
  value makes every lane change in whichever direction it points fire on the
  first frame of `preLaneChange`, and every lane change the other way impossible.
  Measured: 14 of 17 confirmations landed in a single 0.05 s sample, and all 10
  failures were the direction the latched sign opposed.
* **Driver torque from the board's EPS mirror.** `EPS_LIN_RAW` (`0x700`) is one
  CAN frame per serial frame — 100 Hz, the same rate as the message it stands in
  for. Conversion measured by least squares over the 30,482 samples where both
  signals were live: `0x18F = −64.52 × STEER_TORQUE`, R² **0.9991**, residual RMS
  134 CAN counts against a 600–1200 count threshold. Converting into the CAN
  domain rather than rescaling every threshold keeps `STEER_THRESHOLD`, torqued,
  driver monitoring and the lane-change nudge working unchanged.

## 2026-09-16 — `715ea5d` · lane-change nudge threshold 600

`STEER_THRESHOLD` for `HONDA_ACCORD_9G_AU`, matching the 11G Accord and six other
modern Hondas. On route d9 the eleven `preLaneChange` windows that armed a lane
change peaked at **1556–5839** counts in the wanted direction; of the ten that did
not, six peaked between 600 and 1225 — one of them **937 after nine seconds** of
trying at 60 km/h. The gap between 1225 and 1556 is empty, so 600 catches the
weak nudges without touching one that already worked.

Why this car needs it: below the EPS's 50 km/h floor the wheel is unassisted and
a nudge easily passes 1200, which is why lane changes worked at low speed and
failed at cruise. With 160 counts of assist in the wheel the same nudge lands
around 900.

## 2026-09-16 — `bcb9f95` · lateral stays armed at a stop

`steerAtStandstill` for HONDA_ELESYS. `controlsd` computes
`standstill = abs(vEgo) <= max(minSteerSpeed, 0.3) or CS.standstill` and gates
`latActive` on it, so `STEER_TORQUE_REQUEST` on `0x0E4` dropped at every red
light — which the board reads as "not armed" and blanks the lane graphic. Stock
keeps the dashed lanes up.

Nothing steers at a stop: the board holds its command at zero below 5 km/h and
reports STANDSTILL. This only keeps the *request* alive so the graphic can follow
it.

## 2026-09-14 — `39b8575`, `1da246a` · the trim, and the delay

**The steering trim is carried across a gateway hold instead of zeroed.**
`torqueState.i` is positive in all 14 lateral engagements on routes d3/d4,
settling +0.042 to +0.204 m/s² and never once negative — a real one-signed trim,
about ten serial counts of right-hand torque against a persistent left pull. The
two engagements that started from zero took **14.2 s and 14.1 s** to reach 63% of
it, and the board was actuating for only 48% / 65% of laterally-active time, so
the re-learn was paid over and over. From the driver's seat that is the car
drifting left at every takeover and correcting itself half a minute later.

Bounded two ways: clipped to 0.25 m/s² on the takeover frame and decayed with a
30 s time constant while held. The freeze is untouched, and the freeze is what
makes open-loop windup unreachable.

**`steerActuatorDelay` 0.15 → 0.38 s.** openpilot's own learner read 0.3844 s on
d3 and 0.3829 s on d4, both `estimated`, `calPerc` 100, 5 valid blocks. `lagd`
overrides it frame by frame on a warm device, so this is load-bearing only after
a boot and on a device with `LagdToggle` off.

## 2026-09-12 — `27048eb`, `98f6574` · serial steering round 1

`carStateSP.linbusGateway` — the board's state decoded from `GW_ACTIVE` (`0x704`)
and `GW_STEER_GRANT` (`0x70B`): engaged, dry run, valid, actuating, present, the
grant state and reason, EPS acknowledgement and error state, and
`latchedUntilKeyOff`. Plus the integrator gate that holds the lateral integrator
while the board is not actuating, so nothing is integrated against a car that is
not listening.

Also in opendbc: SP-PROTOCOL v3, the LDW bits on `0x0E4` byte 2 bits 5:4, and the
brake-release ramp that imitates the stock camera's 200 ms withdrawal.

---

## Things about this car worth not rediscovering

* **openpilot has no driver override.** `steeringPressed` freezes the integrator,
  suppresses the saturation warning and arms a lane change. It never stops
  commanding. Neither does panda's Honda safety, which only checks that `0x0E4`
  is zero when controls are not allowed — there is no `driver_torque_allowance`
  as on Toyota or Hyundai. The board is the only thing in the stack that
  disengages, which is why MADS has to be told.
* **The EPS will not acknowledge below 50.4 km/h.** Measured three ways: 30
  zero-torque probe windows, 21 release events across seven routes (all
  50.16–50.79), and 2,953 torque-backed frames below 49 km/h with zero
  acknowledgements.
* **160 serial counts is the EPS's ceiling.** Six consecutive frames above it and
  it latches for the key cycle.
* **`0x18F STEER_TORQUE_SENSOR` is not trustworthy while LKAS is active.** See
  2026-09-17 above.
