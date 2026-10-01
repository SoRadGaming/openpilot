# Upstream sync 2026-09: what arrived, and what it does on this car

This is the catalogue of everything upstream changed between the old fork point and the 2026-09-27 sync, and what the
sync did with each item on this car. It was written before the merge from the fork at `8f64c4ad0` (a CHANGELOG commit
on top of `2cfcd3c6a`; code identical), then corrected against the merged code.

| | sunnypilot | opendbc | panda |
|---|---|---|---|
| old fork point | `31dc4d8e5` (2026-06-28) | `b9712d20` (2026-06-08) | `d994e8e8` |
| upstream merged | `a5f44653d` (2026-09-14) | `f95f996f` (2026-09-02) | `74a0adce` (upstream's pin) |
| merge commits | `6b6b2b31e` (fork `2cfcd3c6a` + upstream), then `d1a14edcb` (fork `8f64c4ad0` merged in) | `8bd6e314` (fork `c61cfd9b` + upstream) | none; the pin is upstream's |

The car is a HONDA_ACCORD_9G_AU (ELESYS, right-hand drive) with a comma pedal and openpilot longitudinal, MADS, a
comma 4 (mici UI) and the EPS-LKAS gateway board.

Scope: 529 non-merge sunnypilot commits (openpilot 0.11.2 arrived through 20 sync merges), 163 non-merge opendbc commits,
and one squashed panda sync.
- sunnypilot `CHANGELOG.md`: no new entries. Only the version string moves, 2026.002.000 -> 2026.003.000 (`a0cc313fd`).
- opendbc `RELEASES.md`: no new entries.
- openpilot `RELEASES.md` 0.11.2 notes (the fork carried an empty 0.11.2 heading):
  - a "new driving model", which is the 880M-parameter big model
  - big models on an external GPU
  - livestream and dashcam clips
  - remote body control
  - new alert sounds
  - VW ID.4 and CUPRA Born

**The rule the sync followed:** take upstream, but keep this car's behaviour wherever upstream changed it; every other car
gets upstream's behaviour. Where the fork keeps something, the code is gated on `HONDA_ACCORD_9G_AU` or `HONDA_ELESYS`
and tagged `FORK(...)`.

Car impact column:
- **changes this car**: arrives with the merge and does something on this car.
- **kept**: upstream changed it for every car, and the merge kept this car's old behaviour. Other cars get upstream's.
- **available**: opt-in, or needs extra hardware.
- **internal**: no behaviour change, but a merge or tooling concern (the column says how the merge handled it).
- **n/a**: does not apply to this car.

Unprefixed hashes are sunnypilot commits. `opendbc` and `panda` prefixes mark those repos.

---

## The decisions the merge implemented

| topic | upstream | this car after the merge | where |
|---|---|---|---|
| stopping speed | `should_stop()`: `v_ego < 0.3` for every car; `CP.vEgoStopping` is deprecated and assigning it raises | **0.8 m/s** for `HONDA_ACCORD_9G_AU` only; every other car 0.3 | `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py` `STOPPING_SPEED`; `should_stop(..., v_ego_stopping=)` in `drive_helpers.py`, both planner calls, `joystickd.py`, `maneuversd.py`. The opendbc `vEgoStopping = 0.8` line is deleted |
| stopping ramp | fixed 1.0 m/s³ | **0.8 m/s³** (exactly `float32(0.8)`, what `CP.stoppingDecelRate` delivered before) for this car only | `stopping_tune.py` `STOPPING_DECEL_RATE`, read by `LongControl` |
| stopping-exit debounce | no `starting` state; stopping goes straight to pid | kept, re-applied on the stopping → pid edge | `longcontrol.py` |
| `minEnableSpeed` | -1 for every gas-interceptor Honda (`opendbc 4455464a`) | **19 mph kept** for `HONDA_ELESYS` | opendbc `interface.py` `_get_params_sp()` |
| lane change | rewritten state machine, road-edge block, new `DesireHelper.update()` parameters | upstream's rewrite plus the fork's `NUDGE_FIRM`; `driver_torque_stale` is the last parameter and passed by keyword | `desire_helper.py`, both `modeld.py` |
| MADS | `79b79edd2` brake/regen guard in `should_silent_lkas_enable()` | the fork's gateway-pause fix (`2cfcd3c6a`) plus upstream's guard, plus an enable-frame fix found in review | `openpilot/sunnypilot/mads/mads.py`, `state.py` |
| `IsOnroad` | deleted (`ad5151b38`) | `eps_lkas_hook.py` reads `not IsOffroad` | area A |
| renamed cereal services | `lateralDelay`, `vehicleParameters`, `extrinsicsCalibration`, … | taken; `carStateSP` re-added to every fork `SubMaster` | controlsd, selfdrived, both modeld |
| tests | pytest removed; `tools/test_runner.py` collects `unittest.TestCase` only | every fork test converted | see `README.md`, Tests |

Not decided by the merge, and left to the owner: the steering-delay learner (item 7) and the cruise deceleration with
vision curve slowdown (item 3). Both are listed under [Still open](#still-open-after-the-merge).

---

## What changes on this car

### First boot after the update

1. **AGNOS 18.4 -> 19.7.**
   - `launch_env.sh` now pins `AGNOS_VERSION="19.7"` (the fork pinned 18.4). `launch_chffrplus.sh` compares `/VERSION`
     with it; when they differ, the updater downloads and flashes the new OS before openpilot starts.
   - The download blocks startup and needs internet, so do it parked. The device is presumed to be on 18.4, which is
     what the fork pinned; I could not read the device itself.
   - Upstream also relaunches the updater UI if it crashes mid-flash (`7bd6cad82`).
   - It removes the "OS update downloading" banner (`76b69af59`). That banner belonged to the background updater
     (`updated.py`), not to this boot-time path.
   - Commits: `a2ee4dfff` `e04313cc9` `d4211790d` `38ef0d239` `f7bd4002d` `ed777cf5e` `3a55f31dc` `9e0293671` `ab7284fc8` `59f01d212` `10502adf9` `6249f4d5b` `76b69af59` `7bd6cad82`
2. **Internal panda reflashed.**
   - The panda firmware changes: a compact health packet, the H7 temperature sensor, GPIO bounds checks and cleanups.
     `pandad.py flash_panda()` sees a signature mismatch and reflashes.
   - The new `HEALTH_PACKET_VERSION` makes `p.health()` throw in the one-time heartbeat-lost check at pandad start. That
     is logged once as `pandad.uncaught_exception`, and the check is skipped for that boot.
   - Honda safety (`honda.h`) is unchanged by upstream; the fork's `ELESYS_SCM_STANDDOWN` code merged without a
     conflict. `mads.h` gains only a cppcheck comment.
   - The EPS-LKAS `flash_if_requested()` still runs after `flash_panda()`; the merge kept that order.
   - Commits: `70df7f227` `f5bb85547` `panda 75aa44b` `panda dd8a5b3` `panda 74a0adc`

### Longitudinal (comma pedal, openpilot long)

3. **The cruise set speed is handled outside the longitudinal MPC.** Changes this car; not changed by the merge.
   - `get_cruise_accel()` returns `v_cruise - v_ego`, clipped to [`A_CRUISE_MIN` = -1.2 m/s², max].
     - In chill, max is speed-dependent and is further cut by the turn (lateral-accel) and coast limits.
     - In experimental, max is `ACCEL_MAX`.
   - The result is jerk-limited in both modes (0.6 to 1.6 m/s³ by speed; `3d09a47a4`).
   - The lead MPC no longer receives `v_cruise`.
   - The planner takes the minimum of the lead MPC, cruise and (only when `is_e2e()`) e2e candidates, and stops if any of
     them says stop.
   - It seeds the MPC from `output_a_target`. The turn and coast limits now apply only to the cruise candidate, not to
     the final output.
   - `forceDecel` (DM no-response or a soft disable) now reaches the car as `v_cruise = 0` through this path: at most
     -1.2 m/s², jerk-limited.
   - **With vision curve slowdown on (`SmartCruiseControlVision`), which this car has,** a curve entry now brakes at up to
     1.2 m/s² at the plan instead of about 0.35–0.6. A closed-loop replay of route `00000103` gave a different brake
     command (`0x1FA`) on 6.6 % of frames and gas (`0x200`) on 13.9 %, mainly from this. This is upstream's intended
     behaviour and the merge does not change it. A car-only floor on the cruise deceleration (for example -0.6 in
     `stopping_tune.py`) is possible but was not implemented; road-test first.
   - Commits: `736f3b1a0` `3d09a47a4` `91d0f3309`
4. **Per-car stopping tunes are gone upstream. Kept for this car.**
   - Upstream: the stop criterion is `should_stop(v_ego, a_target)` = `v_ego < 0.3 and a_target < 0.1` on the measured
     speed, the ramp toward `stopAccel` is a fixed 1.0 m/s³, and `vEgoStopping`, `stoppingDecelRate`, `startingState`,
     `startAccel` and `vEgoStarting` moved to `CP.deprecated` (assigning one raises
     `AttributeError: struct has no such member`).
   - The merge deleted `ret.vEgoStopping = 0.8` from opendbc `honda/interface.py` and carries this car's values in
     `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py`:
     - `STOPPING_SPEED["HONDA_ACCORD_9G_AU"] = 0.8`, passed as `should_stop(..., v_ego_stopping=...)` by both planner calls
       (MPC and cruise candidates), and by `joystickd.py` and `maneuversd.py`. `None` keeps upstream's 0.3.
     - `STOPPING_DECEL_RATE["HONDA_ACCORD_9G_AU"] = 0.800000011920929`, which is `float32(0.8)`: exactly what the
       capnp `Float32` `stoppingDecelRate` delivered before. The Python float 0.8 took the ramp one 0.008 m/s² step past
       `stopAccel` (-0.808, one `COMPUTER_BRAKE` count).
   - The one real difference: the fork tested the plan's first speed (`speeds[0] < CP.vEgoStopping`); upstream's
     `should_stop` tests measured `vEgo`. Over logged engaged frames the new rule agreed with the fork's logged
     `shouldStop` on 97.8–100 % of plan frames per segment. In a counterfactual replay of the four stops on route
     `00000103`, `shouldStop` asserted at `vEgo` 0.78–0.79 m/s.
   - `stopAccel = -0.8` (the `HONDA_ELESYS` branch) is still read.
   - The `starting` state is gone. That is no change here: this car never set `startingState`.
   - The stopping-exit debounce (`bde472984`) was re-applied on the stopping → pid edge. `test_stopping_debounce.py`
     is now a `TestCase` with 17 tests, including the table and the exact hold at `stopAccel`.
   - Commits: `fdd1df79f` `031b1ad0a` `9c21b92c4` `bdc8e4b02` `opendbc d4c6f68c` `opendbc c536b211`
5. **`minEnableSpeed` becomes -1 upstream. Kept at 19 mph for this car.**
   - Upstream's `_get_params_sp` sets it to -1 for any car with the gas interceptor (`opendbc 4455464a`).
   - The merge exempts `HONDA_ELESYS`:
     `stock_cp.minEnableSpeed = -1. if ret.enableGasInterceptor and candidate not in HONDA_ELESYS else ...`. A replay
     of `CarParams` gives `minEnableSpeed` 8.494 m/s (19 mph).
   - With the pedal, `pcmCruise` is False, so `belowEngageSpeed` never fired anyway. The visible effect of upstream's -1
     would have been that `manualRestart` ("TAKE CONTROL / Resume Driving Manually", when `vEgo < 0.001`) stopped
     appearing at a standstill while openpilot long is engaged. With 19 mph kept, that is unchanged.
   - Commit: `opendbc 4455464a`

### Lateral and MADS

6. **MADS Pause holds while the brake is held.** Changes this car, in Pause mode only.
   - Upstream adds `CS.brakePressed or CS.regenBraking` to the PAUSE check in `should_silent_lkas_enable()`.
   - The fork checked only `pedal_pressed_non_gas_pressed()`, which follows the `pedalPressed` event. selfdrived raises
     that on every braking frame while moving, but at a standstill only on the frame the brake goes down. So with the
     brake held through a stop, lateral resumed on the first frame with `CS.standstill` true.
   - The merged `should_silent_lkas_enable()` returns False while `_gw_paused` (the fork's `2cfcd3c6a`, which fixed the
     "MADS flap" through a board override), then applies upstream's brake/regen guard. A gateway pause therefore also
     waits for the brake to be released.
   - Review added one more fork change: MADS turned on (LKAS button or unified engagement) **during** a board override
     now starts in `paused`, instead of one active frame first (`mads.py` gateway block, `state.py` DISABLED branch).
   - A pause-mode replay of route `00000103` (the pause bit patched into the logged `pandaStates`) matched the fork
     except at the stop at 218.2–226.5 s with the brake held: the fork resumed lateral at standstill with the brake down,
     the merge stays paused until the release at 226.52 s. **That route was recorded with `MadsSteeringMode = 0`
     (remain active), not Pause,** so check which mode the device is set to; in "remain active" this item changes
     nothing.
   - The MADS state machine (`openpilot/sunnypilot/mads/state.py`) still resolves USER_DISABLE before IMMEDIATE_DISABLE.
   - Upstream's `test_mads_steering_mode.py` (`OpenpilotTestCase`, 23 tests) passes; the fork's
     `test_mads_gateway_pause.py` (18 tests) was converted to the same style.
   - Commits: `79b79edd2` `ac4ab9a9b`
7. **Steering-delay learner (lagd).** Changes this car once. **Accepted** (2026-09-27): it is a one-time
   relearn, the same thing a fresh install does, and the car relearns ~0.38 s on its first highway stretch.
   - It learns only above 50 mph (22.35 m/s, about 80 km/h). It was 15 m/s (54 km/h).
   - It clamps to 0.15–0.65 s. It was 1.0 s.
   - It versions its cache (`VERSION = 1`). The fork's stored `LiveDelay` has no version, reads as 0, and is deleted on
     first start.
   - Until 5 valid blocks are learned again, lagd publishes its `initial_lag = steerActuatorDelay + 0.2 = 0.58 s` (0.38 s
     since 2026-10; see Superseded below). That is not the 0.38 s the fork had learned (0.383/0.377 s on routes d3/d4).
     The +0.2 was already in the fork's lagd; what is new is losing the learned value and relearning only above 80 km/h.
   - `LAT_SMOOTH_SECONDS` is 0.0, so nothing is added on top.
   - The merge changed only the comment in opendbc `interface.py` (it used to say the fallback was 0.38). Options for
     the owner: accept it; a `HONDA_ELESYS`-scoped hunk in `lagd.py`; or settings only (incomplete without the hunk).
   - **Superseded (2026-10).** lagd sat at the 0.58 s fallback for about 5 h of driving (routes d7 to fd) before it had
     5 blocks above 80 km/h. opendbc now sets `steerActuatorDelay = 0.18` for `HONDA_ELESYS`, so `initial_lag` is the
     measured 0.38 s with no `lagd.py` hunk (CAR-HONDA-ACCORD-9G-AU.md 5.1).
   - Commits: `900a896c6` `5a7b710d9` `c16039e0b`
8. **LagdToggle OFF now uses the fixed delay.**
   - This only matters if LagdToggle is OFF on the device; the default is ON.
   - The fork's `get_lat_delay()` returned lagd's value in both states.
   - Upstream, OFF returns `steerActuatorDelay + LagdToggleDelay` = 0.38 + 0.2 (default) = **0.58 s fixed**. ON is
     unchanged. Since 2026-10 it is 0.18 + 0.2 = 0.38 s (item 7).
   - It feeds modeld, modeld_v2, torqued and controlsd_ext. controlsd itself always uses `lateralDelay`.
   - Commit: `53e13a7bc`
9. **paramsd ignores reverse gear** when learning steer ratio, stiffness and offset. Commit: `612d97cfd`
10. **Lane-change state machine simplified.** Changes this car; the fork's nudge rule is kept on top.
    - `laneChangeFinishing` is removed.
    - The car stays at least 0.5 s in `laneChangeStarting`, until the lane-change probability is below 2 %.
    - The timeout resets on each transition.
    - A blinker that was already on when lateral engages now enters `preLaneChange` straight away (it still waits for
      the nudge).
    - The merge re-applied `NUDGE_FIRM` / `NUDGE_HOLD_FRAMES` in upstream's `preLaneChange` branch, and
      `driver_torque_stale` as the last `update()` parameter, passed by keyword from both modeld files.
    - A replay of `DesireHelper` on route `00000103`'s inputs gives both lane changes (471.34 s left, 487.64 s right) at
      the same instants as the fork. The only difference is the missing `laneChangeFinishing` state.
    - Commits: `7d325d665` `4532320fb`

### Driver monitoring

11. **DM policy.** Changes this car.

    | | Fork | Upstream |
    |---|---|---|
    | Vision (camera) alerts | 3 / 5 / 11 s | 5 / 8 / 13 s |
    | Wheel-touch alerts | 15 / 24 / 30 s | 5 / 15 / 25 s |
    | Countdown holds short of the first alert | at a standstill | below 10 km/h (`_ALERT_MIN_SPEED` 2.8 m/s) |
    | Lockout trigger | 3 red alerts or 30 s of red | 2 red alerts, or one red alert unanswered for 5 s |
    | Lockout length | until the next ignition cycle | 1, 5, 15, 30 min, counted per ignition cycle (`DriverLockoutCount`) |

    - The no-entry alert reads "Too Distracted" with the minutes left.
    - `forceDecel` from DM now fires on `noResponseForceDecel` (5 s of unanswered red) instead of at the first red frame.
      A selfdrive soft disable still forces decel, as before. On this openpilot-long car the DM slowdown therefore starts
      5 s later.
    - The first distraction alert (`driverDistracted1`) gains a sound; see item 14.
    - DM's idea of "engaged" (`selfdriveState.enabled or carControl.latActive`) is the same as in the fork.
    - Commits: `f9c555a3f` `e9e5548ed` `069506aa3` `c8786d930`
12. **New DM model**: first "Zoom Zoom", then a model with a sleep head running in shadow mode. The LFS blob changes
    (`bd3f2c85` -> `1e592e21`). Commits: `4eb515b32` `eecff7385`
13. **Right-hand-drive face icon fixed** on the mici driver-state renderer.
    - The yaw sign is now flipped only for LHD. The fork (`b29d0a17a`) negated it unconditionally, so the icon was
      mirrored on this car.
    - The fix depends on DM having detected RHD (`isRHD`), which is expected in an AU car.
    - Commit: `b5b156825`

### Alerts, sounds and process supervision

14. **Sounds.** The comma 4 uses the base sound table; the tizi overrides do not apply.
    - `engage.wav`, `disengage.wav` and `refuse.wav` are re-recorded (new blobs).
    - The prompt and warning alerts now play different, new files:

      | Alert | Fork file | Upstream file |
      |---|---|---|
      | `prompt`, `promptRepeat` | `prompt.wav` | `warning.wav` |
      | `promptDistracted` | `prompt_distracted.wav` | `dm_warning.wav` |
      | `warningSoft` | `warning_soft.wav` | `critical.wav` |
      | `warningImmediate` | `warning_immediate.wav` | `dm_critical.wav` |

    - **`pre_alert.wav` is new**, with a new `AudibleAlert.preAlert`. It is attached to `driverDistracted1`, the first
      "Pay Attention" alert (duration 2 on mici), which was silent in the fork.
    - Looping alerts now finish the current loop instead of cutting off mid-tone. Volume is frozen while an alert plays.
    - sunnypilot's `promptSingleLow` / `promptSingleHigh` blobs are identical in fork and upstream.
    - A "complete" sound was added (`78909dac7`) and removed again (`799551c2d`); net zero.
    - Commits: `5472e69e3` `3601b850c` `f9c555a3f` `24d373062`
15. **Alert behaviour.**
    - "sunnypilot Unavailable" (startup pending) waits 10 s after going onroad instead of 5 (`ec86732af`).
    - `86753ea93`:
      - mici alerts draw line 2 whenever there is one; the old length gate is gone.
      - The swapped font-size branches are fixed.
      - "Fan Malfunction", "Harness Relay Malfunction" and a third hardware alert now say "Contact comma.ai/support".
    - `c9b9fd56e` removes `usbError`, `lowBattery` and `deviceFalling`. Invalid `pandaStates` no longer raises "USB
      Error"; it now falls through to the generic `commIssue`.
    - `8a80bd70e`: when a fault and a user disable land on the same frame, IMMEDIATE_DISABLE now wins over USER_DISABLE
      ("cruise faults should not disable silently").
      - This is in selfdrived's own state machine, which drives the openpilot (longitudinal) engagement here.
      - MADS lateral, which follows `openpilot/sunnypilot/mads/state.py`, still resolves USER_DISABLE first.
    - Commits: `ec86732af` `86753ea93` `c9b9fd56e` `8a80bd70e`
16. **The UI is no longer restarted after a crash.**
    - `restart_if_crash` and the restart path are removed. The fork had `restart_if_crash=True` for `ui`.
    - A crash in any comma 4 UI code, including the fork's `board.py` and `vehicle.py` pages, leaves `ui` not running.
      selfdrived raises `processNotRunning` (NO_ENTRY plus SOFT_DISABLE), because only `mapd` is ignored.
    - `ui` is `always_run`, so it stays dead until manager or the device restarts.
    - **The merge trap in item 19 was avoided:** no fork page passes `back_callback=` any more, and the mici UI was
      built and rendered under Xvfb with the vehicle and gateway rows in place.
    - Commit: `03803d0c8`

### comma 4 screen

17. **Screensaver, on by default.**
    - `ScreenSaverEnabled` defaults to "1" and `ScreenSaverTimeout` to 300 s.
    - When the display would go off offroad, a bouncing sunnypilot logo shows for 300 s, then the display turns off.
    - A touch or ignition dismisses it.
    - It runs on mici (not gated by `sunnypilot_ui()`). The toggle exists only in the tici UI and sunnylink.
    - Commits: `2a16b0fbb` `d48cfa730`
18. **Experimental mode needs a one-time confirmation.**
    - Turning it on in Settings > Toggles opens a confirmation page.
    - The home long-press does nothing until `ExperimentalModeConfirmed` is set.
    - The fork's comma 4 path never set that param (only the tici UI did), so expect the page once, unless the param is
      already set on the device.
    - Commit: `4db21dd4f`
19. **Settings.**
    - New "software" tile, with version, update check and a branch switcher.
    - The branch picker and update button are fixed; "install now" replaces "install update".
    - Models moves to position 1 and sunnylink to position 5, with its own icon and the Audiowide font.
    - Swipe-down works in the sunnylink and Models panels.
    - `099143ad9` removes the `back_callback` parameter from `SunnylinkLayoutMici` and `ModelsLayoutMici`. Left in, the
      fork's `back_callback=` kwargs would have raised `TypeError` in `SettingsLayoutSP` and killed the UI for good
      (item 16).
    - **Merge:** the fork's `settings.py` takes upstream's two inserts unchanged and adds its own after them:
      `items.insert(2, vehicle_btn)` and `items.insert(3, board_btn)`. The order is models, vehicle, gateway, then the
      base items, with sunnylink where upstream put it. The fork's `VehicleLayoutMici` and `BoardLayoutMici` dropped their
      `back_callback` too; `NavWidget` pops itself on swipe-down.
    - Commits: `c0456590f` `9f43d2477` `e571e21d1` `839d3f500` `9fa7ef3d1` `633d17cd1` `da8ce858e` `df4566ef2` `099143ad9`
20. **Models panel.**
    - An offroad-only "refresh models" tile and a slide-to-confirm "clear cache" tile.
    - The panel shows the name of the model actually driving.
    - A pick made during a download is queued instead of cancelling it.
    - Chunked downloads resume, and there is a "verifying" status.
    - Commits: `a5f44653d` `7430f245c` `2d6cc4c06` `760c19d3f`
21. **Home and branding.**
    - The home label reads "sunnypilot" in the Audiowide font.
    - The "car unrecognized" offroad alert points to sunnylink.ai and community.sunnypilot.ai.
    - "Excessive actuation" points to community.sunnypilot.ai only.
    - Commits: `6909aa95f` `c6595fd99`
22. **Widget and onroad fixes.**
    - BigButton titles no longer run under the icon (this matters for buttons that show a value).
    - Labels align correctly next to icons (`c21b0821d`).
    - The onroad camera zooms to fill and centre when the image would otherwise be smaller than the view.
    - There is no false "NO PANDA" on wake: it now uses `sm.alive['pandaStates']`.
    - Commits: `d9c4120f8` `084747c75` `c21b0821d` `e2e3703ae` `5ab7e4847`

### Device, logs and the EPS-LKAS hook

23. **pandad SPI turnaround.** pandad busy-waits for at least 400 µs of SPI idle before each transfer and between header
    and data on the comma 4's internal panda link. Commit: `aa0ac919b`
24. **Performance settings.**
    - Whenever power save is off (onroad, and during builds), both CPU policies get `scaling_max_freq = 1689600`. Nothing
      writes it back offroad, so the cap persists until reboot.
    - camerad forces the BPS clock to 600 MHz.
    - Watch for `*Lagging` events in the first routes.
    - Commits: `c01b8be96` `062cb672e`
25. **Routes stop losing about 0.5 s at start.** loggerd no longer SHA-256 hashes the boot partitions for each route's
    initData (the boot log still does). Commit: `85d364d4d`
26. **Log service renames.** The capnp ordinals are unchanged, so old logs still decode. The names change:

    | Old name | New name |
    |---|---|
    | `liveDelay` | `lateralDelay` |
    | `liveParameters` | `vehicleParameters` |
    | `liveTorqueParameters` | `lateralTorqueParameters` (field `liveValid` -> `valid`) |
    | `livePose` | `deviceMotion` |
    | `liveCalibration` | `extrinsicsCalibration` |
    | `liveTracks` | `radarTracks` |
    | `roadCameraState` | `narrowRoadCameraState` |
    | `driverCameraState` | `cabinCameraState` |
    | road/driver EncodeIdx and EncodeData services | `narrowRoad*` / `cabin*` |
    | `androidLog` | `operatingSystemLog` |

    - **The owner's scripts:**
      - `S:/OP/gas_fit2.py` and `validate_chunk.py` read `livePose.parquet`, and `torque_fit.py` reads
        `livePose.parquet`.
      - Those files come from `S:/OP/comma_logs.py`, which decodes with the old schema copy in `S:/OP/cereal`
        (`livePose @129`). Because ordinals are unchanged, new logs still come out under the old names, and the scripts
        keep working as long as that copy is kept.
      - If the copy is refreshed from upstream, `livePose.parquet` becomes `deviceMotion.parquet`.
    - **Merge:** the fork's `SubMaster` hunks in `controlsd.py`, `selfdrived.py` and both `modeld.py` files took
      upstream's names and re-added `carStateSP`.
    - The stored `LiveParametersV2` and `LiveTorqueParameters` are binary-compatible.
    - Commits: `6d5d1f691` `fedea9fe8` `d8bd6c784` (`9738db0e3` only moves `VisionStreamType` and renames nothing).
27. **Remote "Take Snapshot" removed** from athena. sunnylinkd shares athena's dispatcher, so it disappears there too.
    Commit: `c91731b13`
28. **`IsOnroad` param deleted**; only `IsOffroad` remains.
    - Left alone, `eps_lkas_hook.py` would have raised `UnknownKeyName` on both reads, logged, and never flashed: the
      in-app board update would have been dead, failing safe.
    - **Merge:** both reads are `not params.get_bool("IsOffroad")`. A missing `IsOffroad` reads as onroad, which refuses.
      Every offroad `FakeParams` in `test_eps_lkas_hook.py` says `IsOffroad=True`, and a new test checks that every
      param the hook and the gateway page read is registered in `params_keys.h`.
    - Commit: `ad5151b38`

### Checked and NOT changing

- **Default driving model.**
  - Rebel Legion (`289534674`) was reverted (`9225dac43`), and so was Rebellious Hope (`93f5aa469` / `b361e952c`). The
    tip's `driving_supercombo.onnx` has the same LFS object as when `a40fa3a0b` created it.
  - "model16 deep" (`f02d134f4` / `4024d13dd`) touched only the big models.
  - `a40fa3a0b` repackages `driving_vision` + `driving_on_policy` into one `driving_supercombo.onnx`. It is 60.88 MB,
    against 46.88 + 14.06 MB, and the commit only rewires the inputs.
  - **Not verified:** that the weights are identical. The combined file's LFS object is not downloaded locally.
  - `LAT_SMOOTH_SECONDS = 0.0` and `LONG_SMOOTH_SECONDS = 0.3` are identical.
- **Honda and MADS panda safety.** `honda.h` is untouched upstream, and `mads.h` gains only a cppcheck comment. The
  shared `lateral.h` refactor does not change Honda's check. The panda packs the lateral/longitudinal-allowed bits
  differently, with the same logic.
- **Planner cadence.** The long planner still runs once per `modelV2`. plannerd now polls `modelV2` rather than
  `carState`.
- **Livestream.** It never runs while driving. athena sets `IsLiveStreaming` only when offroad, and the param is cleared
  at ignition-on.
- **FCW.** `ec72ee096` deprecates radard's per-lead `fcw` flag (and `aRel`, `dPath`, `vLat`). On this car FCW alerts
  come from `longitudinalPlan.fcw` (the MPC crash counter), as before. The per-lead flag was only copied into
  `CarControlSP`, and upstream's `controlsd_ext` reads it from `deprecated.fcw`.
- **DM "engaged" definition.** It is `selfdriveState.enabled or carControl.latActive` in both.
- **The command path.** An open-loop replay of route `00000103` (670 s) through card, selfdrived, controlsd, plannerd
  and radard in both trees gave byte-identical `sendcan` on every address (`0x0E4`, `0x1A6` bus 2, `0x1FA`, `0x200`,
  `0x30C`, `0x500`), identical `carState`/`carStateSP` (bar `cumLagMs`), and matching `CarParams`: `minEnableSpeed`
  8.494 m/s, `stopAccel` -0.8, `steerActuatorDelay` 0.38 (0.18 since 2026-10), `longitudinalActuatorDelay` 0.6,
  `safetyParam` 36. Closed loop, `0x0E4` was byte-identical on 66,463 of 66,463 frames; brake and gas differ only
  through item 3.

---

## Still open after the merge

| item | what is open | who decides |
|---|---|---|
| lagd (item 7) | 0.58 s steering delay until relearned above 80 km/h; old cache discarded | **accepted** as a one-time relearn. **Closed 2026-10:** `steerActuatorDelay` 0.18 makes the fallback 0.38 s; no `lagd.py` hunk |
| cruise deceleration (item 3) | up to 1.2 m/s² into curves with vision curve slowdown on | owner: road-test; optional car-only floor in `stopping_tune.py` |
| MADS mode (item 6) | route `00000103` ran with "remain active", not Pause | owner: check the device setting |
| steer-rate emergency takeover | the fork's `EMERGENCY_STEER_RATE` in `mads.py` still applies to every car | known, documented in LKAS-GATEWAY-PROTOCOL.md §9 |
| AGNOS version on the device | presumed 18.4; not read | check at first boot |
| combined driving model weights | not compared (LFS object not local) | none needed unless driving changes |

---

## MADS

| Item | What | Commits | Car impact |
|---|---|---|---|
| Pause holds while the brake is held | Adds `CS.brakePressed or CS.regenBraking` to the PAUSE check. The test arrived pytest-style (242 lines) and was migrated to `OpenpilotTestCase` (261 lines) | `79b79edd2` `ac4ab9a9b` | **changes this car** in Pause mode (item 6). Merged with the fork's `_gw_paused` guard ahead of it |
| MADS state machine | Unchanged upstream; USER_DISABLE is still checked before IMMEDIATE_DISABLE | - | internal. The fork adds one branch in DISABLED (an ENABLE with `silentLkasDisable` goes to `paused`) |
| Panda MADS bits packed | `controls_allowed_sp_pkt` replaces two bytes. pandad maps them back to the same `controlsAllowedLateral` and `controlsAllowedLongitudinal` | `panda 74a0adc` `6e69da403` | internal (logic identical) |
| Heartbeat helpers narrowed (Python panda lib) | `send_heartbeat(engaged)` and `set_alternative_experience(alt)` now always send param2=0. The C++ pandad still sends `engaged_mads` | `panda 74a0adc` | internal (no fork callers) |
| Tesla MADS screen button | Adds `TeslaMadsScreenButton`; `get_mads_limited_brands(CP, CP_SP, params)` gains `params` | `1a07e4722` `opendbc 156b5773` `opendbc 4c64e8a9` | n/a |
| opendbc mads.h | cppcheck suppression comment only | `opendbc f95f996f` | internal |

## Longitudinal control

| Item | What | Commits | Car impact |
|---|---|---|---|
| Cruise target outside the MPC, jerk-limited | See item 3 | `736f3b1a0` `3d09a47a4` `91d0f3309` | **changes this car**, most visibly into curves with vision curve slowdown |
| Per-car stopping and starting tunes removed | See item 4. `vEgoStopping`, `stoppingDecelRate`, `startingState`, `startAccel` and `vEgoStarting` move to `CP.deprecated`; setting or reading them raises | `fdd1df79f` `031b1ad0a` `9c21b92c4` `opendbc d4c6f68c` | **kept**: 0.8 m/s and 0.8 m/s³ through `stopping_tune.py`; the Honda `vEgoStopping` line is deleted; the stopping debounce is re-applied |
| All-speed long for gas-interceptor Hondas | See item 5 | `opendbc 4455464a` | **kept**: 19 mph for `HONDA_ELESYS` |
| Long PID kp deprecated | `PIDController(0.0, ki...)`. Honda Nidec set only ki | `bdc8e4b02` `opendbc c536b211` | internal. Upstream's `longcontrol.py` taken |
| plannerd validity and SLA buttons | Polls `modelV2`. `longitudinalPlan.valid` and `driverAssistance.valid` are now `sm.all_checks()` over every subscribed service except `liveMapDataSP`, `carStateSP`, `selfdriveStateSP` and GPS. SLA gets button releases from a `selfdriveStateSP` bitmask (`ButtonStateTracker`) | `978ec800f` `50b860c92` `0265ae5f7` `7801bdf0c` `9163d1cb7` `a9ffebe96` `2c334ede4` | internal. Only failure modes change: an invalid `vehicleParameters`, `modelV2` or `carControl` now also invalidates the plan |
| LDW blinker cooldown uses DT_MDL | Pairs with the `modelV2` poll; still 5 s | `d3058de5c` | internal |
| Long control and planner cleanups | State-machine rewrite (equivalent), dead trajectory parsing and MPC state removed | `d1e143ac9` `f42dbbb01` `4fd4ddb43` | internal. The fork's stopping debounce (`bde472984`) re-applied |
| Radar lead fields deprecated | radard drops per-lead `fcw`, `aRel`, `dPath`, `vLat` (moved to `deprecated`) | `ec72ee096` | internal (FCW still comes from the planner MPC) |
| Radar errors only block op-long cars | `canError`, `radarFault` and `radarTempUnavailable` are gated on `openpilotLongitudinalControl` | `433c52f62` | n/a (this op-long car is still covered, as before) |
| SCC-Map curve math fix | `/ 2 * a` becomes `/ (2 * a)`; braking distance was about 11x too small | `fd22de1c9` | available (if SCC-M is on with mapd) |

## Lateral control

| Item | What | Commits | Car impact |
|---|---|---|---|
| lagd: 50 mph minimum, 0.65 s cap, versioned cache | See item 7. Falls back to 0.58 s until relearned (0.38 s since 2026-10) | `900a896c6` `5a7b710d9` `c16039e0b` | **changes this car** once; accepted |
| LagdToggle OFF branch fix | See item 8 | `53e13a7bc` | **changes this car only if LagdToggle is OFF** (fixed 0.58 s; 0.38 s since 2026-10) |
| paramsd ignores reverse | See item 9 | `612d97cfd` | **changes this car** |
| Lateral Jerk Torque Controller | `LateralJerkTorqueController`, default off. Error and feed-forward in torque space with friction compensation. Mutually exclusive with NNLC | `91a53aa16` `fc4699a74` `f531952be` | available. **Fixed in the merge:** `LatControlTorqueExtBase.update_output_torque()` updates the owning controller's PID a second time in the frame and ignored the gateway hold, so with Lateral Jerk or NNLC on the integrator wound open-loop while the board was not actuating. It now also freezes on `integrator_frozen` (`test_latcontrol_gateway_hold.py`) |
| Lateral maneuvers tool | Adds 20 and 30 mph jitter maneuvers, curvature 0.004, aborts on gas, offroad-only toggle | `f9cc67896` | available (the 20/30 mph maneuvers are below where this EPS has accepted torque) |
| Curvature lateral controller | For `steerControlType.curvature` cars. `LaC.update()` now returns `steer, lateral_output, lac_log` | `14a00e0cc` | n/a. The fork's `set_linbus_gateway` and `run_ext` calls in `controlsd.py` were re-applied around the 3-value call |
| Lateral MPC lib removed | acados lateral MPC deleted | `92f3d24c8` | internal |

## Lane change

| Item | What | Commits | Car impact |
|---|---|---|---|
| State machine simplified; blinker arms on engage | See item 10 | `7d325d665` `4532320fb` | **changes this car**. The fork's firm/held nudge gate (`10e088a2d`) is re-applied in the new `preLaneChange` branch |
| Road-edge lane-change block | `RoadEdgeLaneChangeEnabled`, default off. Above 20 mph, a road edge with no lane line on the signalled side for 1 s blocks the change | `2d859a8ca` | available (tici UI or sunnylink only; no mici toggle). The positional-argument hazard (`driver_torque_stale` in the 4th slot) is fixed: it is the last parameter and passed by keyword everywhere, including opendbc integration §15 |
| keepLeft/keepRight pulse removed | Dead code for this fork | `ddc8d3713` | internal |

## Driver monitoring

| Item | What | Commits | Car impact |
|---|---|---|---|
| Escalation and lockout overhaul | See item 11 | `f9c555a3f` `e9e5548ed` `069506aa3` `c8786d930` | **changes this car**. cereal fields renamed (`lockoutMinutesRemaining`); `alertSound` moved |
| New DM model | See item 12 | `4eb515b32` `eecff7385` | **changes this car** |
| RHD face icon fix (mici) | See item 13 | `b5b156825` | **changes this car** |
| `CC.driverMonitoringEscalation` | The car's own DM escalation; only VW consumes it | `cc110d1eb` | n/a |

## Driving model

| Item | What | Commits | Car impact |
|---|---|---|---|
| Default model churn (nets to zero) | Rebel Legion and its revert, Rebellious Hope and its revert. model16 deep and its revert touched only the big models | `289534674` `9225dac43` `93f5aa469` `b361e952c` `f02d134f4` `4024d13dd` | internal (the final file equals the one `a40fa3a0b` created) |
| Combined single ONNX | `driving_vision` + `driving_on_policy` -> `driving_supercombo.onnx`; feature buffer handled inside the graph | `a40fa3a0b` | internal. Weights not compared (LFS object not local); size matches the two old files |
| Stock modeld runner rework | Warp and policy fused into one tinygrad JIT; only the mici resolution is compiled; policy enqueued in the graph; tinygrad bumps; faster chunk loading | `cb85ac1f0` `68be77739` `63548ce10` `b2bb71b4b` `bdac9efa1` `d09a411cb` `9ef3cfdf9` `cd2c590d5` `268126f37` `8c927d0fc` `8a3bbcdd4` `4fc5e308f` `97215b17d` `f1977eaa9` `c20263d98` `4d4d6803e` | internal. The main regression surface. The fork's three `modeld.py` edits (nudge gate, `carStateSP`, `driver_torque_stale`) were re-applied by hand to `openpilot/selfdrive/modeld/modeld.py` |
| sunnypilot custom-model manager and modeld_v2 | Two model slots (qcom and chestnut), downloads by ref, catalogue v17 -> v22, hash checks that reset bad selections, requests-based downloads, chunked bundles, a new compiled format, spatial-feature inputs, a tolerant unpickler, and a camera offset sheared on the horizon | `da28afca9` `25c25047b` `cefe5737b` `ab389498a` `98ed8111f` `047ae41c0` `6135084c9` `45814e331` `b67898fac` `211f990f6` `6405f15d5` `59833c500` `94a32493e` `2f4744d39` `5bdc0c23a` `347238b30` | available (only with a custom model selected; that selection is likely reset to default after the merge). The fork's two `modeld_v2/modeld.py` hunks were re-applied |
| `modelDataV2SP.valid` set correctly | It was always published invalid | `5484f7f4a` | internal (removes a false invalid from commIssue dumps) |
| No-wide-camera intrinsics fix | Uses road-camera intrinsics when the wide stream is missing | `8e0ba7a91` | n/a |
| Fork CI can build its own models | `docs_repo` input on the model workflows | `c57f9a7f4` | internal |

## External GPU ("chestnut") and the big model

Initial USB-GPU support (`52e182611`, 2026-05-19) was already in the fork: `modeld.py` had the big-model path, and
`big_driving_vision/on_policy.onnx` shipped. Upstream renames it "chestnut" and finishes it.

| Item | What | Commits | Car impact |
|---|---|---|---|
| Chestnut support, big model, alerts, icons | An 880M-parameter big model on a USB eGPU, with big-to-small fallback, a firmware flasher and updater, telemetry, offroad and onroad alerts, a unified eGPU icon, a loaded chime, and big models in sunnypilot's selector. Big models: Lebowski, Be Right Here, BMRLNAP, TGC | 73 commits: `682b6a20d` `de197ba6f` `4a13639cf` `5cfdb2f4d` `980fb79c1` `a67cdf9a5` `8b88f7dd6` `15f201cae` `1d4558c06` `b742b96c4` `06af2abe6` `d40df6f82` `4cdc16031` `fa75fdd85` `79658800c` `7d5596d5c` `c9f160204` `4adbb8574` `a2e422eee` `94ed0608e` `0de7fbf33` `4f46433e2` `a49c26092` `b742557d6` `5ecd05aed` `5ae100aa1` `8edce0da4` `97542f838` `dcbd66ad8` `391132465` `a996f8ef9` `6ad353211` `5e3d17c72` `fb555fdef` `28560d6cf` `516ec1e68` `dd2214a78` `e32b1a344` `855c99bf9` `ad5afe222` `b755d3227` `d252ab5b4` `2002a64d3` `5406aba1a` `5701fcad0` `155d206b2` `fe47e752f` `bf74ce544` `d86686fff` `a7f32be2f` `04847f380` `7ef4304e2` `caee482fe` `d4e29ec94` `e3ba6492f` `1c54ff853` `47a752459` `3ec980477` `294635beb` `3040f5538` `952d2cab8` `0088cb7ef` `179105736` `b392328c3` `0bac3c903` `6335db69b` `aac9d9ecf` `24a9b6dae` `fa0c6876d` `724971afc` `8196b743a` `a2c554805` `0fbca979d` | available (needs the chestnut accessory; dormant without it). The code lives in `modeld.py`, `hardwared`, selfdrived (`commIssue` is suppressed while the big model settles) and the mici HUD |

## comma 4 (mici) UI

| Item | What | Commits | Car impact |
|---|---|---|---|
| Settings: software tile, branch switcher, reorder, swipe fix | See item 19 | `c0456590f` `9f43d2477` `e571e21d1` `839d3f500` `9fa7ef3d1` `633d17cd1` `da8ce858e` `df4566ef2` `099143ad9` | **changes this car**. The `back_callback` hazard is resolved: the fork's pages no longer pass it |
| Models panel rework | See item 20 | `a5f44653d` `7430f245c` `2d6cc4c06` `760c19d3f` | **changes this car** |
| Screensaver | See item 17 | `2a16b0fbb` `d48cfa730` | **changes this car** |
| Experimental mode confirmation | See item 18 | `4db21dd4f` | **changes this car** |
| Home branding and alert links | See item 21 | `6909aa95f` `c6595fd99` | **changes this car** |
| BigButton labels, label alignment, camera fill, no false NO PANDA | See item 22 | `d9c4120f8` `084747c75` `c21b0821d` `e2e3703ae` `5ab7e4847` | **changes this car** |
| Startup-pending 10 s; alert layout and wording | See item 15 | `ec86732af` `86753ea93` | **changes this car** |
| USB-device icon on home | Shows if the SoC USB-C reports a CC orientation and no chestnut is found after 10 s | `36561258f` | n/a in normal use. The EPS-LKAS board's OBD-C leaves CC1/CC2 unconnected, so it should not trigger this |
| raygui removed, raylib bumped | Own text-alignment enums; `GuiStyleContext` deleted | `31ea1850f` `318257fa3` | internal (the fork's mici pages do not use the removed APIs) |

## Other UI and sounds

| Item | What | Commits | Car impact |
|---|---|---|---|
| New and re-mapped alert sounds, soundd fixes | See item 14. `pre_alert` is a new sound on the first DM alert | `5472e69e3` `3601b850c` `24d373062` `f9c555a3f` | **changes this car** |
| "complete" sound | Added, then removed | `78909dac7` `799551c2d` | internal (nets to zero) |
| Font loading at runtime, vendored QR code | Noto for CJK and Thai, emoji font dropped. `selfdrive/assets/fonts/process.py` is deleted | `96ca1f8ed` `66b3590b6` `b77da0069` `8a8860880` | internal. The fork's glyph test now reads `EXTRA_FONT_CHARS` from `openpilot/system/ui/lib/application.py` |
| Emoji and PIL removed | | `f29eb5d25` `df90fc252` `4dab10e98` | internal |
| comma 3/3X-only fixes | Models-panel freeze, scroll speed, thinner path when not steering, model download row, developer UI crash, camera-offset slider | `63a2a3868` `78a766eb6` `4667241fe` `086530b7c` `97468e4fa` `20ba774ea` `73fc74083` `ee3583df3` | n/a |

## Process supervision and events

| Item | What | Commits | Car impact |
|---|---|---|---|
| UI not restarted after a crash | See item 16 | `03803d0c8` | **changes this car** |
| IMMEDIATE_DISABLE checked before USER_DISABLE | In selfdrived `state.py` only; `openpilot/sunnypilot/mads/state.py` keeps its order | `8a80bd70e` | **changes this car**, for the openpilot-long engagement only; MADS lateral unchanged |
| Dead events removed | `usbError`, `lowBattery`, `deviceFalling`; invalid `pandaStates` now shows as `commIssue` | `c9b9fd56e` | **changes this car** (alert wording only) |
| Excessive-actuation check skipped for notCar | | `3447ec178` | n/a |
| `carNotReady` event; `car_specific.py` -> `car_events.py` | Only VW MEB sets it | `6ac27d491` `c9f0d296b` | n/a (rename is internal) |

## Settings and params

| Item | What | Commits | Car impact |
|---|---|---|---|
| `IsOnroad` removed | See item 28 | `ad5151b38` | internal after the merge: the EPS-LKAS hook reads `not IsOffroad` |
| New params | `ScreenSaverEnabled` (default on), `ScreenSaverTimeout` 300, `ExperimentalModeConfirmed`, `RoadEdgeLaneChangeEnabled`, `DriverLockoutCount`, `Mapd_ClearCache` | various | see the items above |
| Params binding to ctypes, manager preimport removed, old migrations removed | The ctypes `Params` keeps the same `PYTHON_2_CPP` table: `(dict, JSON)` works, `(str, JSON)` still raises | `74ac5ef9a` `caa9e770c` `e124d6df9` `d9596fa99` `1262e2689` | internal. The tuner's `Params` writes ran with zero errors in a replay |
| sunnylink schema test | `test_settings_schema` `test_numeric_constraints` rejects `step` without `min`/`max` | - | internal. The fork's eight `HondaDyn*` info rows dropped their `step: 0.001` |

## Hardware and device

| Item | What | Commits | Car impact |
|---|---|---|---|
| Panda firmware: compact health packet, H7 temperature sensor | See item 2. Also GPIO bounds checks and MISRA/const cleanups | `70df7f227` `f5bb85547` `panda 75aa44b` `panda dd8a5b3` `panda 74a0adc` | **changes this car** (reflash on first boot) |
| pandad SPI turnaround | See item 23 | `aa0ac919b` | **changes this car** |
| CPU cap 1.69 GHz, BPS clock 600 MHz | See item 24. The cap persists until reboot | `c01b8be96` `062cb672e` | **changes this car** |
| camerad IFE change and its revert; spectra naming; loggerd rotates only on streaming cameras; livestream resolution | | `122a8ca00` `ebc77e4c9` `17150b6db` `be191eb8a` `cf790746c` `438035ec1` | internal (the IFE change nets to zero) |
| u-blox AssistNow via comma proxy, background retry | | `b7c333cf3` `0f40ca1d8` | unconfirmed: only if `UbloxAvailable` is true on this comma 4 (`/dev/ttyHS0`); otherwise qcomgpsd runs |
| Cellular DNS fallback | 8.8.8.8 / 1.1.1.1 when the modem reports none | `d79267fa2` | only with a SIM in use |
| Modem and eSIM | Hex ICCIDs, SGP.22 notifications, lazy LPA import | `e10c0fd96` `a75cd2d27` `2f4981045` `e6939dbed` `927e822b5` | internal |
| tici -> comma hardware rename | `HardwareComma`, `COMMA_HARDWARE`; `agnos.json` symlink kept | `cbda3f799` `d02355b1a` `f8d50e062` | internal. `pandad.py` imports `HARDWARE` from `openpilot.common.hardware` |
| USB device logging in hardwared | | `e1e9efb96` `cf8371c2c` | internal |
| Panda CAN driver and build | const/bool cleanups; no pycryptodome; spidev declared | `panda 697b04d` `panda 1a40b79` `panda d9ed70b` `panda 9da8467` `panda a63aed5` | internal |

## Updater and install

| Item | What | Commits | Car impact |
|---|---|---|---|
| AGNOS 18.4 -> 19.7, background-update banner removed, updater relaunch | See item 1 | `a2ee4dfff` `e04313cc9` `d4211790d` `38ef0d239` `f7bd4002d` `ed777cf5e` `3a55f31dc` `9e0293671` `ab7284fc8` `59f01d212` `10502adf9` `6249f4d5b` `76b69af59` `7bd6cad82` | **changes this car** (blocking download at first boot; needs internet) |
| Sync after build; spinner catches OSError | | `b1cdf387b` `f1746f2e2` | internal (background robustness) |
| comma 3 split launch reverted | | `bf1b93f75` | n/a |

## Logging, uploads and sunnylink

| Item | What | Commits | Car impact |
|---|---|---|---|
| Route logs start about 0.5 s sooner | See item 25 | `85d364d4d` | **changes this car** |
| Service and field renames | See item 26 | `6d5d1f691` `fedea9fe8` `d8bd6c784` | **changes this car** (log names). The fork's `SubMaster` lists were rewritten; the owner's parquet scripts are unaffected while `S:/OP/cereal` keeps the old schema |
| Remote snapshot removed | See item 27 | `c91731b13` | **changes this car** (feature gone, connect and sunnylink) |
| openpilot statsd removed | sunnypilot keeps `StatLogSP` in `openpilot.sunnypilot.system.statsd` | `fddb9fb31` | internal. The fork's `sunnylink/statsd.py` imports from `openpilot.sunnypilot.system.statsd` |
| Livestream from comma connect | libdatachannel webrtcd, keyframes, 5-min timeout, localhost bind, cleared at ignition | `45d8bcd7f` `0038d84e1` `3939f1e92` `f3b1f97af` `586dd4c61` `291315ab2` `37390743c` `15fb1a809` `0240a62fd` `cd052f124` `14cff000e` `866cd01f3` `98e5f547e` `f257544f1` `b9f25f8a4` `3f93b0012` `554d8d211` `555f48c5d` `dcf9d25bf` `a8d1a280c` `5b36799ee` `20fdc3d82` `3c90b66b6` `351701689` `02b6cef15` `ee6f37462` | available (parked, paired with connect) |
| Dashcam clips from comma connect | athena clip RPCs, hardware transcoder | `3643dfbef` `88c8f5a52` `7dbdff832` `f09211fcc` `503701690` | available |
| OSM maps clear via sunnylink | `Mapd_ClearCache` param | `4075befc5` | available |
| In-house JSON-RPC for athena and sunnylinkd | | `60924556f` | internal (re-check sunnylink remote functions after the merge) |
| Thumbnails via ffmpeg, libyuv dropped | | `cb82722dd` `60716edc3` `1e49eac4d` `9877f6ac0` | internal |
| Small reliability fixes | swaglog deletes the oldest logs, temp-file cleanup, Params exit hang, wifi log spam, list_view | `5fadc71a3` `0819f5c0f` `4e9e9190a` `bd7b419a4` `faf966adb` | internal (background) |
| feedbackd removed | Bookmarks still work | `765d6fff9` | internal |
| timed compares GPS to UTC | | `2e4de38eb` | internal |
| Version 2026.003.000 | | `a0cc313fd` | internal |

## Safety (panda / opendbc safety)

| Item | What | Commits | Car impact |
|---|---|---|---|
| `lateral.h` dedup | Shared torque-request and inactive-angle helpers | `opendbc e9b5152e` `opendbc a3ed7d18` | internal (Honda's check is unchanged) |
| Release-build safety test, MISRA fixes | Compiles the fork's `ELESYS_SCM_STANDDOWN` code in release mode | `opendbc 72b8736b` `opendbc e5d654da` `opendbc ccfda5f8` | internal. `safety/tests/common.py` merged; the fork also exempts `0x500` between the two `TestHondaElesys*` classes only (both stand-down lists carry it), which fixed two failures that predate the merge |
| Ford curvature fixes, native curvature | | `opendbc af6b8cbd` `opendbc ce45daae` `opendbc 5020b567` `opendbc 7343ffe7` | n/a |
| Toyota UNSUPPORTED_DSU frequency check | | `opendbc 8b7e933b` | n/a |

## Honda (opendbc)

| Item | What | Commits | Car impact |
|---|---|---|---|
| Honda platform sets rewritten | `HONDA_BOSCH*` become `frozenset(... flags & ...)`. `HONDA_NIDEC_ALT_PCM_ACCEL`, `HONDA_NIDEC_ALT_SCM_MESSAGES` and `HONDA_BOSCH_TJA_CONTROL` are removed; code uses `CP.flags & HondaFlags.X` | `opendbc 57506094` `opendbc 44f2987c` | internal. `HONDA_ELESYS` is now `frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)` in the same block, and every fork import of the removed sets is gone |
| Honda helpers take `CP` or nothing instead of the fingerprint | `create_brake_command(..., fcw, stock_brake, CP_SP)` drops `car_fingerprint` and `is_metric`; `compute_gas_brake(accel, speed, CP)`, `spam_buttons_command(..., CP)`, `create_acc_commands(..., CP)`; `actuator_hysteresis(brake, braking, brake_steady)` drops `v_ego` and the fingerprint | `opendbc 045cd8d3` | internal. `create_brake_command` gained `is_metric=True, elesys=False` at the end, passed by keyword; `compute_gas_brake(CP)` has an `elif CP.carFingerprint in HONDA_ELESYS` branch; `test_elesys.py` updated |
| car.capnp `deprecated` groups | `ret.brakeDEPRECATED` -> `ret.deprecated.brake`; INDI/LQR and the starting/stopping fields are deprecated; opendbc always loads its own car.capnp | `opendbc 78a1c9e7` `opendbc 7744a36e` `opendbc 472a8958` `opendbc 2ecf53cf` `opendbc 5f31b975` `opendbc 7e3a0703` `157c7080c` `e1c69719c` | internal. `carstate.py` uses `ret.deprecated.brake`; `vEgoStopping` is gone from `interface.py` |
| RadarPoint and LeadData cleanup | `aRel`, `yvRel` and `measured` deprecated; `track_id` in the base class; `LeadData.status` -> `present` | `opendbc 402335de` `opendbc a5e400e3` `45b53cf66` `ec72ee096` `2b1fd8906` `680dd7cf3` | internal. Upstream's removal taken in `radar_interface.py` |
| Cached CarParams VIN compare | `is not` -> `!=` | `opendbc 719e1b78` `opendbc f95568e0` | internal (fixed fingerprint skips it) |
| FW version regex test | The fork's `36707-T2M-Q640` and `77959-T2A-B110` match | `opendbc d8f6d5cf` | internal |
| Nidec hybrid brake-hold block | Hybrid and no interceptor only | `opendbc 063414f6` `9e0d89968` | n/a |
| B gear in `_gearbox_common.dbc` | This car uses `_gearbox_legacy` | `opendbc efb346e9` | n/a |
| Other Honda models | City, Civic, Accord Hybrid 2026, CR-V, Ridgeline, Prologue, Clarity; `car_list.json` | `opendbc e2e50874` `opendbc ec907487` `opendbc 93118594` `opendbc 0cd9f4d1` `opendbc 56fe4648` `opendbc 07067947` `opendbc 6911a683` | n/a. `car_list.json` still carries "Honda Accord 2013-15" |

## Other car support

| Item | What | Commits | Car impact |
|---|---|---|---|
| VW MEB, ID.4 and CUPRA Born | Lateral, long, safety hardening, DBC unification, fingerprints | `opendbc 97e1b355` `9bad7e5d` `b72c1fd5` `1be203c2` `d159d939` `0aea38cb` `1bacd87e` `66ee827f` `a843dd15` `188d5f43` `58bfe143` `17ef489d` `aedd88ef` `b906c148` `c83c882c` `a0febba3` `5b27a531` `e994baba` `25cac1eb` `d36a5e11` `193d7f68` `be1c0034` `4c63e118` `2a0a420e` `f7e2a4ad` `42995b01` `31d3a562` `97d45520` `7ffcd457` `9433a278` `46a45dd5` `cfe1c0f7` `51119e35` `bf2d6ad0` `f4fddc13` `f16e1c19` `bae42fc3` `a2a96d8b` `b0685818` `c9b31d21` `4cd4b8cc` `4634ef32` `e91d0199` `e23d405a` `8d0eb4c0` `adf39790` `bc7aaf9b` `40f67fae` `90627fdf` `0fee7550` `6be8f216` `33ea616a` `8df9db3d` `8c0b1367`; sp bumps `b614253ee` `cc83d8615` `e4d152e1a` `e43de7331` `5bcff3f87` `ef3ec30fe` `7661e03d1` `2feca929a` `5b3d5f74e` | n/a |
| Other brands | MG/SAIC port, SafetyModel ids for BYD/Volvo/BMW, Subaru alpha long off, GM non-ACC fingerprints off, Hyundai/Kia, Nissan, Rivian, Toyota radar, Chrysler Trackhawk | `opendbc 07f32b37` `dc9642ce` `b4ef5e1c` `ae445c9b` `1559e869` `bbe66947` `eed2166c` `8e71f9ec` `d4275572` `ffa13083` `4e561d84` `b8eded24` `c067db1b` `0a17aca8` `db413dde` `7e38b683` `ba51a6de` `7b7ade56` `3eecccb0` `75889fd9` `7689bdd2` | n/a |
| comma body | Face, layout, joystick | `0fdfaf737` `66300306f` `49c3b8fc3` `05f42f752` | n/a |

## Repo layout, build, tests and tools

| Item | What | Commits | Car impact |
|---|---|---|---|
| Nested `openpilot/` layout | `selfdrive`, `system`, `common`, `cereal`, `tools` and `sunnypilot` move under `openpilot/`; `hardware`, `version` and `esim` move to `openpilot/common/`. `docs/`, `opendbc_repo`, `panda` and the launch scripts stay at the root | `20e0f21b5` `5edc0bd89` `8a9ca1546` `addca46f6` `1474d5474` `37eda06c9` `560cde61a` `df1663c58` `cea5273f1` `160942dfd` `3b4077d31` `f0f7b877c` `cb6e422c9` `f48e99b33` `b2a708490` `62b97fabf` `3c4790c08` | internal. The biggest merge cost: every fork code file moved under `openpilot/`, and 13 fork-added files were file-location conflicts |
| pytest replaced with a unittest runner | `tools/test_runner.py` collects only `unittest.TestCase` classes; pytest is gone from `pyproject.toml` and `uv.lock`. Plain `def test_*` modules are dropped without a word in a full run, or exit 5 on their own | `98e7c4f98` `ac4ab9a9b` | internal. Every fork test is now a `TestCase`/`OpenpilotTestCase`: 121 tests in 9 modules, all collected |
| ty rules stricter | `[tool.ty.rules]` no longer ignores `invalid-argument-type` or `unsupported-operator`; `ty check openpilot` is part of `lint.sh` | - | internal. The 9 errors it found were all in fork tests and are fixed |
| Dependency slimming | psutil, pyserial, aiohttp, crcmod and eigen dropped; comma-deps from PyPI | `1ab1ed745` `0d93f0e82` `6392096de` `b827c0f55` `f283f6703` `39117a587` `3b3f5967e` `bf5540c36` `503531ab3` `cddb6103e` `a26254304` `a52da0d3d` `960c98835` | internal (checked: no fork file imports them). `cantools` is in neither venv, which is why the DBC byte-order test now uses opendbc's `CANParser` |
| msgq submodule back to sunnypilot/msgq | | `48a5bdc8e` | internal (`git submodule sync`) |
| opendbc test infra | `test_models` moved in; hypothesis and jinja dropped | `opendbc 58e07d4a` `opendbc 9641b182` `opendbc d7c9aff7` | internal |
| Cabana, replay and PC tools | Cabana moves off Qt, replay progress, jotpluggler, clip wide camera, op.sh quoting | `cbf750de2` `7cc48b5bc` `9b9e3ea60` `30f358eb5` `131e473f3` `0f9c753e6` `6e0f4f463` `46f612224` `5419f57b3` `5645370f8` `0e3205948` `7cf55c3b7` `a04c045cd` `5d23a78c7` `3f49e2d33` `6b47a5b6b` `29e7f362e` `fafcee04f` `57b5eb311` `4cefe7239` | internal (rebuild Cabana; re-check `dbc/eps-lkas-gw.dbc`) |

## Corrections made after checking the code

These were found while the catalogue was being written, before the merge.

- **Steering-delay fallback.** An earlier draft said lagd falls back to `steerActuatorDelay` 0.38 s. It publishes
  `steerActuatorDelay + 0.2` = 0.58 s until 5 blocks above ~80 km/h are learned, and the fork's learned 0.38 s is
  discarded by the new cache version.
- **Sounds.** An earlier draft called `pre_alert` a re-recording and said every alert sounds different.
  - `pre_alert` is a new sound on an alert that is silent in the fork.
  - engage, disengage and refuse are re-recorded, and prompt/warning map to new files.
  - sunnypilot's `promptSingleLow` / `promptSingleHigh` are unchanged.
- **IMMEDIATE_DISABLE order.** This applies only to selfdrived's state machine (the openpilot-long engagement). MADS
  lateral still resolves USER_DISABLE first.
- **MADS flap mechanics.** `pedalPressed` is raised on every braking frame while moving, and only on the brake's rising
  edge at a standstill. The fork therefore resumed at the first standstill frame with the brake held.
- **Panda reflash cause.** The firmware signature mismatch triggers the reflash. The health-packet version bump is what
  makes the one-time heartbeat check throw.
- **Owner's log scripts.** They read parquet files that `comma_logs.py` produces with the old schema in `S:/OP/cereal`, so
  the renames do not break them unless that schema copy is refreshed.
- **`IsOnroad` failure is not literally silent.** It would have logged an exception every watch period, but shown nothing
  to the user and left the request set.
- **Excessive-actuation alert** points only to community.sunnypilot.ai.
- **`c21b0821d`** is a label-alignment fix, not an alert change.
- **`9738db0e3`** renames nothing.
- **Chestnut.** The eGPU support is not new: its first commit was already in the fork.
- **Test runner.** Fork tests did not "run zero tests and pass" across the board. Plain test functions are dropped
  silently in a full run, or give exit 5 on their own, and pytest-importing files fail to collect.
- **Settings merge.** The `back_callback` conflict was not just textual. Leaving it in would have crashed the UI at start,
  and nothing restarts it.
- **Unverified and marked:** the combined driving model's weights, and the device's current AGNOS version.

### Corrections made after the merge

The pre-merge catalogue described upstream's behaviour. On this car the merge changed four of its conclusions:

- Item 4 said the per-car stopping tune is gone. For this car it is kept (0.8 m/s, 0.8 m/s³) in `stopping_tune.py`.
- Item 5 said `minEnableSpeed` becomes -1. For `HONDA_ELESYS` it stays 19 mph.
- Item 6 described the fork's MADS flap as today's behaviour. It was fixed in `2cfcd3c6a` before the merge, and the
  brake guard now applies together with that fix.
- Item 28 and the Lateral Jerk, road-edge and `back_callback` hazards were resolved in the merge (see each item).

### Reader disagreements resolved earlier

- **Rebel Legion as a new default model:** it was reverted, and the final file equals the one `a40fa3a0b` created.
- **Livestream "including while driving":** wrong. `IsLiveStreaming` is set by athena only when `IsOffroad`, and it is
  `CLEAR_ON_IGNITION_ON`.
- **`vEgoStopping` "ignored" versus "raises":** it raises (tested with pycapnp against upstream's car.capnp).
- **`with_flags` removed:** the Honda sets are gone, but the helper still exists in sunnypilot's opendbc.
- **plannerd "changes this car":** the planner still steps once per `modelV2`. Only the validity scope and the SLA button
  path change.
- **`minEnableSpeed = -1` "engages from standstill"** (old fork docs): with the pedal, `pcmCruise` is False, so the 19 mph
  never gated engagement. The observable effect would only have been the `manualRestart` warning, and 19 mph is kept.

Noise not listed: 146 sunnypilot commits, 46 opendbc commits and about 11 panda-repo commits were folded away. They are
CI and workflows, test-only changes, lint, docs, bot dependency bumps and add-then-revert pairs. The 20 sunnypilot
sync-merge commits are not counted. There were 0 new sunnypilot or opendbc changelog entries.
