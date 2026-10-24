# HONDA_ACCORD_9G_AU: the car port (fork area C)

This document lists everything the fork changes relative to upstream so that sunnypilot can drive a 2013-2015 Honda Accord (Australian market, V6L and Tech trims) fitted with an Elesys radar, a comma pedal (gas interceptor) and the EPS-LKAS gateway board. It is written for whoever next merges upstream into the fork. For each change it says what the change does, why it exists, which identifiers must survive, which test pins it, and how to put it back if upstream has rewritten the code around it.

The other two areas have their own documents in `docs/fork/`. Where a file is shared, this document describes only the car-specific hunks and points to the others:

- **Area A, gateway update:** updating the board's firmware from the comma over CAN. This covers the flasher, the hook, the app-slot image, the mici `gateway` page, the `EpsLkas*` params and the firmware identity fields.
- **Area B, LKAS gateway protocol:** openpilot steering the EPS through the board. This covers the 0x0E4 serial-domain path, 0x500 SP_HUD_STATUS, decoding 0x700-0x70F, driver-torque substitution, the integrator hold and the MADS hand-back.

History and specs already exist in these files. This document does not repeat them:

| document | what it is |
|---|---|
| `CHANGELOG-elesys.md` | the longitudinal work on this car, sections 1-25, with measurements |
| `FEATURES-elesys.md` | plain-English feature notes for the owner. **Its PCM-blend material is stale throughout.** The toggle was removed in sunnypilot `cb4e0c34b` / opendbc `aa73e60a`, but the file still has a `The two toggles` section, its subsection 2 (`...also blend the PCM gas above 30 km/h`), a `Gas - the PCM gas factor` line under `Seeing what it has learned`, and `Leave the PCM blend off` in `The honest summary` |
| `docs/CHANGELOG_SERIAL_STEERING.md` | the serial-steering changelog, including `Things about this car worth not rediscovering` |
| `docs/SP_GATEWAY_FIRMWARE.md`, `docs/SP_HUD_STATUS.md` | area B specs |
| `S:/OP/*.md` (outside both repos) | the owner's raw analyses: `redlight_overshoot_findings.md` (brake/gas scale, creep, `vEgoStopping`), `AEB_passthrough_signal.md` and `CMBS_AEB_bitmap.md` (AEB bits), `TSA_*.md` (why the stock ACC is stood down), `radar_firmware_bit_spec.md` and `HOW_OP_USES_RADAR.md` (radar bus). Some describe older code. Where they disagree with the repo, the repo is right |

## Baseline

| | sunnypilot fork | opendbc fork |
|---|---|---|
| repo / branch | `SoRadGaming/sunnypilot` `master` | `SoRadGaming/opendbc` `sp-master` |
| fork point (merge-base) | `a5f44653d` (upstream, merged 2026-09-27; previously `31dc4d8e5`) | `f95f996f` (upstream, merged 2026-09-27; previously `b9712d20`) |
| HEAD documented here | branch `merge/upstream-2026-09-27`: `d1a14edcb` plus the post-merge review fixes | `8bd6e314` plus the review fixes, which the sunnypilot fork pins once it lands |
| upstream compared against | `refs/upstream/master` = `a5f44653d`, 0 commits ahead | `refs/upstream/master` = `f95f996f`, 0 commits ahead |

Since the 2026-09 sync every sunnypilot path is under `openpilot/`. To see any change described here:

```
git -C ~/sp-merge diff refs/upstream/master HEAD -- <path>
git -C ~/sp-merge/opendbc_repo diff refs/upstream/master HEAD -- <path>
```

**Markers are not complete.** Much of the opendbc car and safety code is tagged `FORK(HONDA_ELESYS)`, `FORK(HONDA_ACCORD_9G_AU)` or plain `FORK:`, the safety code uses `HONDA_ACCORD_9G_AU` comments, and most opendbc branches test `in HONDA_ELESYS`. On the sunnypilot side every hunk the 2026-09 merge touched carries a `FORK(...)` marker: `drive_helpers.py`, `longitudinal_planner.py`, `longcontrol.py`, `desire_helper.py`, both `modeld.py`, `controlsd.py`, `selfdrived.py`, mici `settings.py`, `mads.py`, `state.py`, `latcontrol_torque_ext_base.py`, `joystickd.py`, `maneuversd.py` and `pandad.py`. `latcontrol.py` and `card.py` only mention `HONDA_ELESYS` / `HONDA_ACCORD_9G_AU` in prose comments. The fork hunks in the files below contain no marker and no `HONDA_ELESYS` or `HONDA_ACCORD_9G_AU` text at all, so a marker grep after a merge will not find them. The list covers area C and also the area-B hunks this document describes in 10.3-10.6:

- sunnypilot: `openpilot/selfdrive/car/helpers.py`, `openpilot/common/params_keys.h`, `openpilot/selfdrive/controls/lib/latcontrol_torque.py`, `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py`, `openpilot/sunnypilot/selfdrive/controls/controlsd_ext.py`, `openpilot/sunnypilot/sunnylink/statsd.py`, `openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py`, the `honda.py` brand page, both sunnylink yaml pages and `compile_settings_ui.py`
- opendbc: `_nidec_scm_group_a.dbc` (`CMBS_BUTTON`)

The authoritative check after a merge is the inventory in section 1 compared with `git diff refs/upstream/master HEAD --stat` in both repos (14.5), not a marker grep.

Signal positions are written `start:length` below, which is the DBC `start|length` pair (Motorola start bit).

---

## 1. Inventory

### 1.1 opendbc fork (`opendbc_repo`)

| file | status | area C content | other areas in the same file |
|---|---|---|---|
| `opendbc/car/honda/values.py` | M | `HondaSafetyFlags.ELESYS_SCM_STANDDOWN`, `HondaSafetyFlags.ELESYS_STOCK_ACC` (64, section 15), `HondaSafetyFlags.ELESYS_BRAKE_320` (128, brake320, 7.13), `HondaFlags.ELESYS`, `CAR.HONDA_ACCORD_9G_AU`, `HONDA_ELESYS`, `STEER_THRESHOLD` entry, `FW_QUERY_CONFIG` non-essential ECUs | - |
| `opendbc/car/honda/fingerprints.py` | M | `FW_VERSIONS[CAR.HONDA_ACCORD_9G_AU]` | - |
| `opendbc/car/honda/interface.py` | M | transmission detection, longitudinal tuning (no `vEgoStopping` since the 2026-09 merge), `steerActuatorDelay`, `steerAtStandstill`, the `latAccelOffset` seed, safety param, `minEnableSpeed`, and its exemption from the gas-interceptor `-1` in `_get_params_sp()`; since round 8 the PID's floor, `ELESYS_PID_ACCEL_MIN` -3.2 (-3.25 since brake320, 7.13), `elesys_pid_floor_applies()` and `elesys_pid_accel_min(CP, CP_SP, pitch=0.0)` (grade-aware since the round 8 review) in `get_pid_accel_limits()`'s interceptor branch (7.12) | the two lateral values exist because of the gateway (B) |
| `opendbc/car/honda/radar_interface.py` | M | Elesys radar parsing; since 2026-10-24 the Elesys track slots (`_update_elesys_track()`, 5.2) | - |
| `opendbc/car/honda/carstate.py` | M | gear decode (`update_gear_elesys()`, taken only when the gearbox frame has `GEAR`), `LKAS_PROBLEM` bus, `stockAeb`, `scm_buttons`, `econ_on`; `VEHICLE_DYNAMICS` registered liveness-exempt for the VSA fault monitor (6.6, 2026-10-03); `accFaulted` from `BRAKE_ERROR` also in stock ACC mode (15); `STEER_STATUS` 1 at a standstill in P is not a steering fault (6.4, 2026-10-04) | B/A: `get_can_parsers()` registration of `GW_*`/`EPS_LIN_RAW`; `CarStateExt.update(..., ret_sp, ...)` |
| `opendbc/car/honda/carcontroller.py` | M | `compute_gb_honda_elesys()`, `brake_pump_hysteresis_elesys()`, dynamic-tuner hooks, 32-count brake release limit, the soft final stop's call (7.8), the NaN-`vEgo` guard in the brake block (7.8), the `CRUISE_OVERRIDE` decision comment (7.7), SCM_BUTTONS re-send, no `LKAS_HUD`; stock ACC mode sends only `0x0E4` and `0x500` (15); pump rule C1b (7.2, 2026-10-06; it replaced batch 3's C1); brake320's `self.brake320` (`ElesysBrakeCeiling`), the earned clips and `compute_gb_honda_elesys(..., brake_max)` (7.13, 2026-10-24) | B: `brake_release_scale()`, LDW bits, the `create_sp_hud_status()` block, the reported torque (`linbus_gateway_actuating()`; since 2026-10-12 a comment on why the blinker ceiling is not reported) |
| `opendbc/car/honda/hondacan.py` | M | `create_brake_command()` units bit, `create_scm_buttons_no_cruise()` | B: `create_steering_control()` LDW, `create_sp_hud_status()` |
| `opendbc/car/car_helpers.py` | M | `skip_fw_query` | - |
| `opendbc/car/structs.py` | M | `CarStateSP.vsaFault`, `vsaStoredFault` (6.6, 2026-10-03); described in 6.5 because of the capnp rule | B (`LateralControl`, `LinbusGateway` 0x704/0x70B fields, `driverTorqueStale`), A (`fw*` fields) |
| `opendbc/car/tests/routes.py` | M | test route | - |
| `opendbc/car/torque_data/override.toml`, `opendbc/car/torque_data/substitute.toml` | M | the car's own torqued prior; the substitute line is gone (2.4) | - |
| `opendbc/sunnypilot/car/car_list.json` | M | car list entry | - |
| `opendbc/dbc/generator/honda/*.dbc`, `opendbc/dbc/honda_accord_2015au_radar.dbc` | A/M | all of them, except the two in the next column | `_sunnypilot_linbus_gw.dbc` (B, A); byte 2 of 0x0E4 in `_steering_control_e.dbc` (B) |
| `opendbc/safety/modes/honda.h` | M | the stand-down safety mode; stock ACC mode, param 64 (15); brake320, param 128: `honda_nidec_max_brake` 320 (7.13, 8.1 row 8) | 0x500 on its TX list is B's frame |
| `opendbc/safety/tests/common.py`, `opendbc/safety/tests/test_honda.py` | M | safety tests | B: the Elesys-only `0x500` exemption in `common.py` |
| `opendbc/sunnypilot/car/honda/carstate_ext.py` | M | `fuelGauge`; `_update_vsa_fault()` (6.6), which since brake320 also runs the VSA fallback (`self.brake320_fallback`, 7.13) | B (gateway decode, driver torque), A (`_update_linbus_firmware`) |
| `opendbc/sunnypilot/car/honda/vsa_fault.py` | A (2026-10-03) | `VsaFaultMonitor`: the VSA's own fault from provisional bits (6.6); since brake320 its `settled` flag (7.13) | - |
| `opendbc/sunnypilot/car/honda/brake_ceiling.py` | A (2026-10-24) | brake320: `panda_max_brake()`, the earned clips, `Brake320Fallback`, `ElesysBrakeCeiling`, `brake320_status()` (7.13) | - |
| `opendbc/car/honda/tests/test_elesys_brake320.py` | A (2026-10-24) | all (7.13) | - |
| `opendbc/sunnypilot/car/honda/test_vsa_fault.py`, `opendbc/sunnypilot/car/honda/fixtures/vsa_fault_frames.json.gz` | A (2026-10-03) | all (6.6) | - |
| `opendbc/sunnypilot/car/honda/dynamic_tuning.py` | A | all (since 2026-10 also `filtered_pitch()`, read by the soft final stop; since 2026-10-04 it builds and feeds the shadow learners, `_build_shadow()`, 9.3) | - |
| `opendbc/sunnypilot/car/honda/shadow_learn.py` | A (2026-10-04) | the shadow longitudinal learners, logged as `hondashadow` lines and applied to nothing (9.3) | - |
| `opendbc/sunnypilot/car/honda/test_shadow_learn.py` | A (2026-10-04) | all (9.3) | - |
| `opendbc/sunnypilot/car/honda/elesys_gas.py` | A (2026-10) | the gas law v1/v2, `HondaElesysGasLawV2`, drive-mode slots and crossfade; the v2 launch cap (2026-10-04) (9.2) | - |
| `opendbc/sunnypilot/car/honda/elesys_stop.py` | A (2026-10) | the soft final stop: `soft_stop_ceiling()`, `ElesysSoftStop`, `SOFT_STOP_*` (7.8); the cap is 105 counts since round 8 (125 before) | - |
| `opendbc/sunnypilot/car/honda/gas_interceptor.py` | M | the import and the one call into `elesys_gas.py`, and the tuner's `observe_pedal` hook | - |
| `opendbc/car/honda/tests/test_elesys.py` | A | all | - |
| `opendbc/car/honda/tests/test_elesys_stock_acc.py` | A (2026-10-04) | all (15.7) | - |
| `opendbc/car/honda/tests/test_elesys_radar.py` | A (2026-10-09) | all (4.8, 5.2) | - |
| `opendbc/sunnypilot/car/honda/values_ext.py` | M (2026-10-04) | `HondaFlagsSP.ELESYS_STOCK_ACC` = 8 (15) | - |
| `opendbc/sunnypilot/car/interfaces.py` | M (2026-10-04) | `_initialize_honda()`, the stock ACC mode's one writer (15.2) | - |
| `opendbc/safety/tests/libsafety/safety.c`, `opendbc/safety/tests/libsafety/libsafety_py.py` | M (2026-10-04) | the test getter `get_honda_elesys_stock_acc()` (15.7) | - |
| `opendbc/safety/sunnypilot/mads.h`, `opendbc/safety/tests/mads_common.py` | M (2026-10-04) | none: upstream files, every MADS car (8.5) | UF (`FORK(UPSTREAM-FIX)`): a lateral grant zeroes `heartbeat_engaged_mads_mismatches`; the regrant and heartbeat-traffic tests, inherited by every MADS safety class |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/test_elesys_gas.py` | A (2026-10) | all (9.2) | - |
| `opendbc/sunnypilot/car/honda/test_elesys_stop.py` | A (2026-10) | all (7.8) | - |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | A | sections 1-6, 9, 16, 17, 17b, 18 and 19 | B: sections 7, 8, 10-15 (including 14b) |

### 1.2 sunnypilot fork

| file | status | area C content | other areas |
|---|---|---|---|
| `.gitmodules`, `opendbc_repo` (gitlink) | M | points the submodule at the opendbc fork (Other, 13.1) | - |
| `openpilot/common/params_keys.h` | M | 5 `HondaDyn*` keys and `HondaElesysGasLawV2` (11.1); `HondaElesysStockAcc`, `HondaElesysStockAccSaved` (15, 2026-10-04); `DiagToolStock`, `CarBusOffStock` (16.6, 2026-10-19) | A: `EpsLkas*` keys |
| `openpilot/cereal/custom.capnp` | M | `CarStateSP.vsaFault @3`, `vsaStoredFault @4`; `OnroadEventSP.EventName` `vsaFault @28`, `vsaStoredFault @29`, `vsaFaultAnnounce @30` (6.6, 10.6; 2026-10-03); `hondaElesysStockAcc @31` (15, 2026-10-04). Area C code also reads `CarStateSP.driverTorqueStale`. Since 2026-10-19 also `OnroadEventSP.EventName` `diagnosticToolStock @33`, `carBusOffStock @34`, `carBusOffAnnounce @35`, `carBusOffDisengage @36` (16.5, 2026-10-19). Since 2026-10-24 `CustomReserved14` (service `customReserved14`, kept under its reserved name) carries brake320's ceiling and VSA fallback (7.13); `openpilot/cereal/services.py` lists it at 1 Hz, decimation 1 (qlog). | B, A |
| `openpilot/selfdrive/car/card.py` | M | `skip_fw_query=bool(fixed_fingerprint)`; `finish_long_settings_restore()` right after `CarParamsPersistent` is written (15.5, 2026-10-04); the KTuner fix 1 hooks: `DiagToolGuard` built in `__init__`, its `update()` after `sm.update(0)` in `state_update()`, the first-`controls_update` hold in `step()`, `write_diag_stock()` in `params_thread` (16.3, 2026-10-19); brake320's `Brake320Log` built in `__init__` and updated at the end of `state_publish()` (7.13, 2026-10-24) | A: firmware identity staging/writing, flash trace |
| `openpilot/selfdrive/car/helpers.py` | M | none. The `lateralControl` rebuild in `convert_carControlSP()` is area B; it is described in 10.5 because its failure took down the car's radar path | B |
| `openpilot/selfdrive/locationd/torqued.py` | M (2026-10) | the initial `latAccelOffset` from `CarParams` (2.4); since 2026-10-04 the shadow speed split's marked places (9.3; since round 8 also the driver's torque into the shadow, its own SubMaster in `main()` and torqued's window with each `tick()`); since round 8 this car's factor floor, a guarded import of `torque_floor.py` and one call after `min_lataccel_factor` (2.4) | - |
| `openpilot/sunnypilot/selfdrive/locationd/torque_floor.py` | A (round 8) | `lataccel_factor_floor()`, `ELESYS_LATACCEL_FACTOR_FLOOR` 1.10: torqued's factor floor on this car (2.4) | - |
| `openpilot/sunnypilot/selfdrive/locationd/lat_speed_split.py` | A (2026-10-04) | the shadow split of torqued's lateral fit, logged as `latsplit` lines and applied to nothing (9.3); since round 8 v2: hands x speed cells with the controller's integrator and the roll term, tagged with the build | - |
| `openpilot/sunnypilot/tools/shadow_learn_report.py` | A (2026-10-04) | reads the `hondashadow` and `latsplit` lines out of routes and prints what the shadow learners learned; never pools different builds (batch 3; since round 8 `latsplit` v2 too); read-only (9.3) | - |
| `openpilot/sunnypilot/tools/brake_route_check.py` | A (batch 3) | the pump rule's and the brake law's proof plan from a route folder: metrics, the rules replayed (v5, the retired C1, C1b), abort verdicts and C1b's acceptance checks; read-only (9.4) | - |
| `openpilot/sunnypilot/selfdrive/car/interfaces.py` | M (2026-10) | `_initialize_torque_lateral_control()` keeps `latAccelOffset` across the EnforceTorqueControl / NNLC re-run of `configure_torque_tune()` (2.4); `preserve_long_settings()` before `_cleanup_unsupported_params()`, and `HondaElesysStockAcc` in `initialize_params()` (15, 2026-10-04) | - |
| `openpilot/sunnypilot/selfdrive/car/honda_stock_acc.py` | A (2026-10-04) | the stock ACC mode's settings snapshot and restore (15.5) | - |
| `openpilot/sunnypilot/selfdrive/car/brake320_log.py`, `.../car/tests/test_brake320_log.py` | A (2026-10-24) | brake320's `customReserved14`, 1 Hz and on change (7.13) | - |
| `openpilot/sunnypilot/selfdrive/car/car_specific.py` | M (2026-10-04) | `STOCK_ACC_ANNOUNCE_FRAMES`: the stock ACC mode's startup banner (15.5) | - |
| `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py` | A (2026-09 merge) | `STOPPING_SPEED` and `STOPPING_DECEL_RATE`, keyed by fingerprint (10.1) | - |
| `openpilot/selfdrive/controls/lib/drive_helpers.py` | M (2026-09 merge) | `should_stop(..., v_ego_stopping=None)` (10.1) | - |
| `openpilot/selfdrive/controls/lib/longitudinal_planner.py` | M (2026-09 merge) | passes the car's stopping speed to both `should_stop()` calls (10.1); since round 8 the re-entry ramp, built in `__init__` and applied after the candidates' `min()` (10.8) | - |
| `openpilot/sunnypilot/selfdrive/controls/lib/reentry_ramp.py` | A (round 8) | `ReentryRamp`, `reentry_ramp_applies()`, `lead_decel()`, `REENTRY_RAMP_*`: the plan ramped in after the driver lifts off the gas (10.8) | - |
| `openpilot/selfdrive/controls/lib/longcontrol.py` | M | stopping-exit debounce; the per-car stopping ramp | - |
| `openpilot/selfdrive/controls/radard.py`, `openpilot/sunnypilot/selfdrive/controls/lib/elesys_radar_guard.py` | M, A (2026-10-09) | radard's clutter guard, `HONDA_ELESYS` only (5.3); since round 8 its long-range rule for leadOne (`get_lead(..., long_range=)`, `long_range_track()`, `long_range_agrees()`, `LR_*`) | - |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_elesys_radar_guard.py`, `.../tests/fixtures/elesys_radar_guard_frames.json.gz`; since round 8 `.../tests/fixtures/elesys_long_range_frames.json.gz` | A (2026-10-09) | all (5.3) | - |
| `openpilot/tools/joystick/joystickd.py`, `openpilot/tools/longitudinal_maneuvers/maneuversd.py` | M (2026-09 merge) | pass the car's stopping speed to `should_stop()` (10.1) | - |
| `openpilot/selfdrive/controls/lib/latcontrol.py`, `openpilot/selfdrive/controls/lib/latcontrol_torque.py`, `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py`, `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_ext_base.py` | M | documented in 10.3; the reason for them is the gateway | B |
| `openpilot/selfdrive/controls/controlsd.py`, `openpilot/sunnypilot/selfdrive/controls/controlsd_ext.py` | M | documented in 10.4; since the round 8 review also the PID floor's pitch in `controlsd.py` (7.12) | B |
| `openpilot/selfdrive/selfdrived/selfdrived.py` | M | the VSA fault alert's three hunks (10.6, 2026-10-03); the KTuner helper's four hooks (16.5, 2026-10-19) | B: `carStateSP` in the `SubMaster`, the EPS latch alert, `steerSaturated` through `steer_saturated.py` (2026-10-12) |
| `openpilot/sunnypilot/selfdrive/selfdrived/vsa_fault_alert.py`, `openpilot/sunnypilot/selfdrive/selfdrived/events.py` (the three `vsa*` entries and their two callbacks, `hondaElesysStockAcc`, and since 2026-10-19 the four KTuner entries) | A / M (2026-10-03) | `VsaFaultAlert` and its events (10.6); the stock ACC banner (15.5, 2026-10-04); the KTuner texts (16.5, 2026-10-19) | B owns the rest of `events.py` |
| `openpilot/sunnypilot/selfdrive/selfdrived/car_stock_alert.py` | A (2026-10-19) | `CarStockAlert`: the expected `controlsMismatch` while stock on purpose, the refusal, the KTuner texts (16.5) | - |
| `openpilot/sunnypilot/selfdrive/car/diag_tool_guard.py` | A (2026-10-19) | `DiagToolGuard`: fix 1's detector, a diagnostic tool talking on the car bus while parked (16.3) | - |
| `openpilot/selfdrive/pandad/pandad.cc`, `openpilot/selfdrive/pandad/panda_safety.cc`, `openpilot/selfdrive/pandad/pandad.h`, `openpilot/selfdrive/pandad/SConscript`, `openpilot/selfdrive/pandad/.gitignore` | M (2026-10-19) | the stay-stock path (leave or hold back the car mode) and fix 2's bus-off latch (16.4) | - |
| `openpilot/selfdrive/pandad/car_bus_guard.h`, `openpilot/selfdrive/pandad/tests/test_car_bus_guard.cc` | A (2026-10-19) | `CarBusGuard`, header-only, and its native test (16.4, 16.7) | - |
| `openpilot/sunnypilot/selfdrive/car/tests/test_diag_tool_guard.py`, `.../test_card_diag_hook.py`, `.../test_diag_tool_guard_replay.py`, `.../tests/fixtures/ktuner/*.npz`, `openpilot/sunnypilot/selfdrive/pandad/tests/` (`test_car_bus_guard.py`, `test_stock_wiring.py`, `fixtures/ktuner/*.npz`), `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_car_stock_alert.py`, `openpilot/sunnypilot/tools/ktuner_fixtures.py` | A (2026-10-19) | all (16.7) | - |
| `openpilot/selfdrive/controls/lib/desire_helper.py`, `openpilot/selfdrive/modeld/modeld.py`, `openpilot/sunnypilot/modeld_v2/modeld.py` | M | `NUDGE_FIRM`, `LANE_CHANGE_SPEED_FLOOR` (push13) | B: `driver_torque_stale` |
| `openpilot/sunnypilot/mads/mads.py`, `openpilot/sunnypilot/mads/state.py` | M | `mads.py` in stock ACC mode only: MADS on at any speed, and the drop-out's `speedTooLow` alert (15.5, 2026-10-04); 10.7 notes one rule that applies to every car | B |
| `openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py` | M | Honda brand page; the stock ACC toggle (15.6) | - |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` | A | mici vehicle page; the stock ACC toggle (15.6) | - |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | M | vehicle button | A: gateway button |
| `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml`, `.../vehicle.yaml`, `openpilot/sunnypilot/sunnylink/settings_ui.json` | M | sunnylink rows; `HondaElesysStockAcc` (15.6) | - |
| `openpilot/sunnypilot/sunnylink/statsd.py` | M | tuner telemetry; `HondaElesysStockAcc` | - |
| `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py` | M | UTF-8 fix (Other, 13.2) | - |
| `openpilot/sunnypilot/sunnylink/athena/sunnylinkd.py`, `openpilot/sunnypilot/sunnylink/athena/tests/test_sunnylinkd.py` | M (2026-10-04) | `OFFROAD_ONLY_PARAMS` = `HondaElesysStockAcc` (and since batch 3 fix round 1 the pump rule's key - `HondaElesysPumpC1b` since 2026-10-06; `HondaElesysBrakeLawV2` too until round 5): `saveParams()` refuses them unless `IsOffroad` is set; `test_saveParams_offroad_only`, `test_saveParams_pump_rule_offroad_only` (15.6) | - |
| `openpilot/selfdrive/car/cruise.py`, `openpilot/sunnypilot/selfdrive/car/cruise_ext.py`, `openpilot/sunnypilot/selfdrive/car/tests/test_speed_limit_confirm_buttons.py`; since 2026-10-06 also `openpilot/selfdrive/controls/plannerd.py`, `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_assist.py`, `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_speed_limit_confirm_grace.py` | M / A (2026-10-04, narrowed 2026-10-05; grace 2026-10-06; lower-limit prompt and 1 s hold 2026-10-09, with `openpilot/sunnypilot/selfdrive/controls/lib/longitudinal_planner.py` and `.../speed_limit/tests/test_speed_limit_drop_prompt.py`) | none: Speed Limit Assist, every non-PCM car; listed because 15.9 relies on it being non-PCM only | SL: a wrong-way press at the confirm prompt may lower the set speed, never raise it; the asked-for press up to 1.0 s after the prompt timed out still confirms; a lower limit is a prompt at any value, and a new limit counts after 1 s (11.3) |
| `openpilot/selfdrive/controls/tests/test_stopping_debounce.py`, `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`, `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py`, `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py`, `openpilot/selfdrive/locationd/test/test_torqued_elesys.py`, `openpilot/selfdrive/locationd/test/test_lagd_elesys.py`, `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_vsa_fault_alert.py`, `openpilot/sunnypilot/selfdrive/car/tests/test_honda_stock_acc.py`, `openpilot/sunnypilot/mads/tests/test_mads_honda_stock_acc.py`, `openpilot/sunnypilot/selfdrive/locationd/tests/test_lat_speed_split.py`, `openpilot/sunnypilot/tools/tests/test_shadow_learn_report.py`, `openpilot/sunnypilot/tools/tests/test_brake_route_check.py`; since round 8 `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_reentry_ramp.py`, `openpilot/selfdrive/controls/tests/test_elesys_pid_floor.py` | A | see section 12 and 15.7 | B: `test_latcontrol_reported_torque.py`, `test_steer_saturated.py` |
| `CHANGELOG-elesys.md`, `FEATURES-elesys.md`, `docs/CHANGELOG_SERIAL_STEERING.md` | A | history, listed at the top | - |
| `docs/fork/UPSTREAM-2026-09.md` | A | what the 2026-09 sync brought, and what it does on this car | all |

---

## 2. The car and how it is identified

### 2.1 The platform entry (`values.py`)

```python
HONDA_ACCORD_9G_AU = HondaNidecPlatformConfig(
  [
    HondaCarDocs("Honda Accord 2013-15", "V6L & Tech", support_type=SupportType.COMMUNITY, support_link="#community"),
  ],
  CarSpecs(mass=3343 * CV.LB_TO_KG, wheelbase=2.78, steerRatio=17.5, centerToFrontRatio=0.37),  # as spec
  {Bus.pt: 'honda_accord_au_2015_can_generated', Bus.radar: 'honda_accord_2015au_radar'},
  flags=HondaFlags.NIDEC_ALT_SCM_MESSAGES | HondaFlags.HAS_ALL_DOOR_STATES | HondaFlags.ELESYS,
)
```

In `class CAR` it sits between `ACURA_ILX` and `HONDA_CRV`. Its flags are the same as `ACURA_ILX` plus `ELESYS`.

- **Nidec, not Bosch.** As a `HondaNidecPlatformConfig` it gets the `hondaNidec` safety model, openpilot longitudinal (`interface.py` always turns it on for Nidec), the Nidec gas/brake frames (0x1FA, 0x30C) and the Nidec car-state paths.
- `NIDEC_ALT_SCM_MESSAGES`: car state reads `MAIN_ON`, `REVERSE_LIGHT` and similar signals from `SCM_BUTTONS` (0x1A6), and the panda gets `HondaSafetyFlags.NIDEC_ALT`.
- `HAS_ALL_DOOR_STATES`: `doorOpen` comes from `DOORS_STATUS` (0x405).
- `ELESYS`: new, see section 3.
- Both trims share the radar/ACC module, SRS, LKAS and pedal (opendbc `b7119d31`), so one entry covers both.
- `minSteerSpeed` is left out on purpose. It was 99 mph at first (lateral effectively off before the board existed), then 12 mph (`3974008f`), then removed (`2f19864a`). The EPS itself will not acknowledge below about 50.4 km/h, and the board guards standstill (area B; `docs/CHANGELOG_SERIAL_STEERING.md`, `Things about this car`).
- `docs/CARS.md` in opendbc was **not** regenerated and has no line for this car. `opendbc/car/tests/test_docs.py` does not compare against that file, so nothing fails today. I did not check whether any upstream CI job does.

### 2.2 FW fingerprint

`fingerprints.py`:

| ECU | address | version |
|---|---|---|
| `Ecu.fwdRadar` | `0x18DAB0F1` | `36707-T2M-Q640` followed by two `0x00` bytes |
| `Ecu.srs` | `0x18DA53F1` | `77959-T2A-B110` followed by two `0x00` bytes |

`FW_QUERY_CONFIG.non_essential_ecus` adds `CAR.HONDA_ACCORD_9G_AU` to both the `Ecu.eps` and the `Ecu.vsa` list, so a missing EPS or VSA response does not block a match. Upstream opendbc added a `fw_version_regex` to `FW_QUERY_CONFIG` (`d8f6d5cf`). Both versions above match it: upstream's `opendbc/car/tests/test_fw_fingerprint.py` checks the `FW_VERSIONS` entries against the regex, and opendbc's full test discovery passes after the 2026-09 merge.

Once the car is selected in Vehicle settings, the FW query never runs (2.3). On the road this table is then used only when the car is auto-fingerprinted with no bundle set.

### 2.3 How the car is identified on the road: fixed platform, no FW query

**Files:** opendbc `opendbc/car/car_helpers.py`; sunnypilot `openpilot/selfdrive/car/card.py` (one line, in the `get_car()` call).

**What it does.** `fingerprint()` and `get_car()` take a new keyword argument `skip_fw_query: bool = False`, ORed with the existing `SKIP_FW_QUERY` environment variable. When it is set together with a fixed fingerprint, `fingerprint()` logs `Fixed fingerprint %s: skipping the VIN/FW query, no OBD multiplexing`. `card.py` passes `skip_fw_query=bool(fixed_fingerprint)`, where `fixed_fingerprint` is the `platform` field of the `CarPlatformBundle` param, which is the car the user picked in Settings > Vehicle.

With the flag set, `fingerprint()` takes its existing skip branch: `vin = VIN_UNKNOWN`, `car_fw = []`, no FW candidates. The `Using cached CarParams` path is inside `if not skip_fw_query`, so it is skipped as well. The only thing that still identifies the car is the fixed platform.

**Why.** The VIN/FW query uses OBD multiplexing, which reroutes panda bus 1 to the OBD port. On this car bus 1 is the Elesys radar's bus. The radar loses the car for about 1.5 s and latches ACC and CMBS faults until the next ignition. The fault appeared only on the first ignition after an update: `CarParamsCache` is cleared on manager start, and the cached path skips the query. Evidence: route `15646e8515eda1a7/000000b5` logged `Getting VIN & FW versions` and multiplexing toggling four times in 1.5 s, while the routes either side logged `Using cached CarParams` (opendbc `05008a22`, sunnypilot `d11d2c9a8`, `CHANGELOG-elesys.md` section 17).

**Scope: this changes other cars too, and not only by skipping a query.** The code comment says the query's answer is discarded anyway with a fixed platform. That is true of the fingerprint candidate only. For **every** sunnypilot car with a `CarPlatformBundle` set, on **every** ignition (cached or not):

- `CarParams.carFw` is empty and `carVin` is `VIN_UNKNOWN`.
- Anything that `_get_params()` or `_get_params_sp()` derives from `car_fw` stops working. On Honda that includes the `Disable control if EPS mod detected` loop and sunnypilot's `HondaFlagsSP.EPS_MODIFIED` detection (both look for an `eps` FW version containing a comma), so an EPS-modified Honda with a bundle silently loses its modified torque tables. I did not survey other brands.

Cars without a bundle are unchanged. On this car nothing FW-derived is used, so the only effect here is the empty `carFw`/VIN in the logs. The car has to be selected in Vehicle settings once; with auto fingerprinting alone the query still runs and can still fault this car.

**Recommendation, not done:** if the fork is ever shared, narrow the condition to this platform. Either `skip_fw_query=fixed_fingerprint == "HONDA_ACCORD_9G_AU"` in `card.py`, or an opt-in set of platforms in opendbc that `card.py` consults.

**Re-apply.** Keep the keyword argument on both functions and the OR with the env var. Upstream changed the cached-params condition in `fingerprint()` to `carVin != VIN_UNKNOWN or os.environ.get("REPLAY")`. The fork's lines sit just above and below that line, and merged unchanged in 2026-09.

### 2.4 `car_list.json`, torque data, test route

- `opendbc/sunnypilot/car/car_list.json`: key `Honda Accord 2013-15`, `platform: HONDA_ACCORD_9G_AU`, make Honda, brand honda, model Accord, years 2013/2014/2015, package `V6L & Tech`. The sunnypilot platform picker shows this entry, and its key must match the `HondaCarDocs` name.
- **Torque data (2026-10).** The car falls through to the default `else:` branch of the lateral tuning chain in `interface.py` (`torqueBP/V = [[0, 2560], [0, 2560]]`, `configure_torque_tune()`), so it runs the torque lateral controller from its torque-data prior until torqued has learned its own values.
  - `override.toml`: `"HONDA_ACCORD_9G_AU" = [1.25, 1.25, 0.18]` (`LAT_ACCEL_FACTOR`, `MAX_LAT_ACCEL_MEASURED`, `FRICTION`), with a `FORK(HONDA_ACCORD_9G_AU)` comment; 1.1 from 2026-10-01 to 2026-10-03. Until 2026-10 `substitute.toml` mapped the car to `HONDA_ACCORD` (1.689 / 0.325 / 0.212). torqued may learn within ±30% of the prior factor and ±50% of its friction, so that window was 1.18-2.20, and the filtered factor sat on 1.18 on every route from `ed` to `103`. On this car torque 1.0 = 2560 on `0x0E4` = 160 serial counts at board authority 160, a different steering system and scale. `MAX_LAT_ACCEL_MEASURED` repeats the factor, the Honda convention in `override.toml`; it feeds only car docs, angle/curvature cars' torque bar and `test_lateral_limits`.
  - **The factor rises with speed, and no ±30% window holds it.** openpilot's own `TorqueEstimator`, replayed over the authority-160 routes, sees a raw factor of 0.67-0.9 in town (`d5`-`e2`, ~60 km/h; the EPS responds less at low speed) and 1.2-1.5 on the highway routes `fc`/`fd`/`103`. On the highway commute `10f` (2026-10-01) three methods agree on 1.63-1.66: the estimator from an empty cache (1.649-1.664), a fit on the wire (1.634) and the controller's own correction (1.65). Friction is learned at 0.16-0.23. 0.67-1.66 is a ratio of 2.5; a ±30% window spans 1.86, so the prior decides which end is cut.
  - **1.1 (2026-10-01)** gave 0.77-1.43. It held every town and `fc`/`fd`/`103` filtered value, its floor clipping up to ~24% of a town route's raw samples (d9 24.0%, fc 23.7%, e1 19.2%, e2 9.4% town first), but its ceiling pinned `10f`: from an empty cache every valid raw value was above 1.43 and the filtered factor ended at 1.396; chained after `fc` → `fd` → `103` the raw sat on the ceiling 63% of `10f`'s valid time and the filtered factor ended at 1.412.
  - **1.25 (2026-10-03)** gives 0.875-1.625, friction unchanged 0.09-0.27. Highway commuting dominates this car's engaged steering, and a feedforward that is too strong at speed (a factor learned too low) is the worse error, so the window follows the highway: `fc`/`fd`/`103` sit inside it, `10f`'s 1.63-1.66 at its ceiling, and town drives are clipped at the 0.875 floor by design (1.10 since round 8, below). Replayed (the real `TorqueEstimator`, the prior read through the real `CarInterface`, reported torque from the batch-1 `CarController`, empty cache, 0.38 s lag fallback, 1.1 alongside as the control):

    | chain | valid from | filtered factor at the end of each route | time at the limits (raw clipped / filtered within 0.03) |
    |---|---|---|---|
    | `fc` → `fd` → `103` → `10f` | 58.4 min (`fd`, 661 s in) | fc 1.250 (not valid), fd 1.352, 103 1.362, 10f 1.459 | none on any route (raw at most 1.590). 1.1: 10f 1.412, raw on the ceiling 1,845 s (63%), filtered within 0.03 of it 638 s |
    | `10f` alone | 18.1 min (1,097 s) | 1.578 | raw above the ceiling 1,262 s (69% of the valid time); filtered at most 1.578. 1.1: 1.396, 100% |
    | town first: `d5` `d6` `d8` `d9` `e1` `e2` → `fc` → `fd` → `103` → `10f` | 22.7 min (`d5`, 1,366 s in) | d5 1.218, d6 1.085, d8 0.999, d9 0.928, e1 0.896, e2 0.882, fc 0.876, fd 1.028, 103 1.083, 10f 1.353 | raw below the floor: d6 5%, d8 57%, d9/e1/e2/fc 97-100%, fd 31% (2.1 h in all); filtered within 0.03 of it: e1 434 s, all of e2 and fc, fd 902 s; never at the ceiling. 1.1: fc 0.823, 10f 1.341 with 25% of 10f's raw on its ceiling |

    A filtered factor within 0.03 of 0.875 means town driving is sitting on the floor - expected after a town week, and the highway pulls it back up (1.353 by the end of `10f` above). Within 0.03 of 1.625 means the highway is pressing on the ceiling. Lag 0.342 instead of the 0.38 fallback moves any of these by 0.006 or less.
  - **The floor is 1.10 since round 8 (2026-10-20, proposal 6): the window is 1.10-1.625.** On drives `13d`-`143` (17-19 Oct) the factor sat at 0.875-0.93, pinned by points torqued took with a hand resting on the wheel (300-1199 counts of driver torque, never `steeringPressed`: 92-97% of its points), while the car needs about 1.4-1.6 above 90 km/h; town tracking at 54-70 km/h was the same at factors 1.05-1.43 (error RMS 0.064-0.065) as at 0.875 (0.061).
    - **Where.** `openpilot/sunnypilot/selfdrive/locationd/torque_floor.py` (`lataccel_factor_floor()`, `ELESYS_LATACCEL_FACTOR_FLOOR` 1.10), called by a `FORK(HONDA_ACCORD_9G_AU)` line in `TorqueEstimator.__init__` right after upstream sets `min_lataccel_factor`; the import is guarded (if it fails, torqued keeps upstream's floor). On `HONDA_ELESYS` with torque tuning only; it only ever raises the floor and never above the prior; under sunnypilot's relaxed window (`LiveTorqueParamsRelaxedToggle`, 0-2.5) it does nothing. The prior, the ceiling and the friction window are unchanged; the decimated (qlog) estimator's window becomes 1.10-1.875.
    - **Nothing resets.** The bounds are not in the restore key (`get_restore_key()`), so the previous build's cache is restored as it is; they clip each raw estimate (`np.clip` in `get_msg()`), and torqued resets only on a NaN estimate. The restored filtered value is not clipped: it slides from the cached ~0.9 to 1.10 at the filter's decay of 250 (about 21 min of onroad time; installed on the car's cached 0.909: ~1.00 after 13 min, 1.05 after 28, 1.09 after 61). Re-filtering the car's own logged raw estimates through the new clip (the same method reproduces the logged filtered factor to 5e-5) with the floor installed before `13d`: 13d 0.883 -> 0.997, 13e -> 1.075, 13f -> 1.095, 141 -> 1.099, 142 and 143 1.100. The raw estimate was below 1.10 on every sample of all seven drives, so in town the factor sits on the new floor; only highway driving lifts it. Open loop: a higher factor commands less torque, which would move later points.
    - Tests: `test_torqued_elesys.py` `TestTorquedElesysFloor` (the window here and nowhere else, only raising and never past the prior, the relaxed toggle, the module missing, the old build's cache kept and sliding up to the filter's closed form with no reset, the learner above the floor for a higher raw), and the existing window assert now expects 1.10.
  - Live learning stays on. The learned factor moves with the town/highway mix (the outer buckets keep old points for several drives, so it leans toward town).
  - **The prior is torqued's cache key** (fingerprint, tuning, prior friction, prior factor, `VERSION`). Changing it discarded the cache once, which also dropped points learned while the board's authority went 40 → 80 → 120 → 160 → 200 → 160 → 140 → 160 under one "torque 1.0". 1.25 is a new key as well (`test_torqued_elesys.py` discards a 1.1 cache too); batch 1 had not been pushed to the car when the prior changed, so the car still makes one reset, from the substitute straight to 1.25. Nothing about the board is in that key, so **any future change of `GW_LIN_AUTHORITY` or the full scale must change this prior too**.
  - `substitute.toml` keeps a `FORK(HONDA_ACCORD_9G_AU)` comment where the line was. A car may be in only one of the three files (the loader raises "defined twice"), and `TestElesysTorquePrior.test_not_substituted` fails if a merge restores the line. sunnypilot's NNLC model lookup (`nnlc/helpers.py`) reads `substitute.toml` as a fallback; without the line it still picks `HONDA_ACCORD.json` (fuzzy, by name), and NNLC is off on this car.
  - **Offset seed.** `interface.py` sets `lateralTuning.torque.latAccelOffset = -0.43` in the `HONDA_ELESYS` block (5.1), and `torqued.py` (`FORK(HONDA_ACCORD_9G_AU)`) starts its offset from `CP` instead of a hard-coded 0.0 whenever it has no valid cache; every other car's `CP` carries 0.0, so it starts where upstream does. The torque controllers start from the same value. On `f2`/`fc`/`fd`/`103` the logged feedforward is exactly `desired - roll*g - offset`, the learned offset is -0.42 to -0.50, and it cancels about 0.4 m/s^2 of crossfall in the device roll; restarting it at 0 would cost about 60 serial counts of feedforward until torqued is valid again (23-58 min in the replays), more than `LINBUS_I_CARRY_MAX` lets the integrator carry into a takeover. sunnypilot's `setup_interfaces()` re-runs `configure_torque_tune()` with `EnforceTorqueControl` or NNLC on, which resets the offset to 0.0; since 2026-10 a `FORK(HONDA_ACCORD_9G_AU)` hunk in `openpilot/sunnypilot/selfdrive/car/interfaces.py` saves the offset before the re-run and restores it after, so the seed survives either toggle (every other car carries 0.0, or has no torque tune, and ends where upstream leaves it). With `LateralJerkTorqueController` on the offset is not read at all.
  - Tests: `TestElesysTorquePrior` (opendbc), `test_torqued_elesys.py` (sunnypilot; `TestTorquedElesysSeedWithTorqueToggles` for the re-run).
- `opendbc/car/tests/routes.py`: `CarTestRoute("15646e8515eda1a7/00000019--dd0700eac9", HONDA.HONDA_ACCORD_9G_AU)`. I did not check whether this route has been uploaded somewhere `test_routes`/`test_models` can fetch it.

---

## 3. The HONDA_ELESYS platform

### 3.1 The category and its flags

| identifier | value | where |
|---|---|---|
| `HondaFlags.ELESYS` | `1024`, the slot upstream marked `1024 is available` | `values.py` |
| `HONDA_ELESYS` | `frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)`, in upstream's style since the 2026-09 merge (it was `CAR.with_flags(HondaFlags.ELESYS)`) | `values.py`, after `HONDA_BOSCH_CANFD` |
| `HondaSafetyFlags.ELESYS_SCM_STANDDOWN` | `32` | `values.py`; mirrored as `HONDA_PARAM_ELESYS_SCM_STANDDOWN = 32` in `honda.h` |

`HONDA_ACCORD_9G_AU` is the only member. Almost every fork branch tests membership of the category (`in HONDA_ELESYS`) rather than the car, so a second Elesys platform would inherit all of the category behaviour. These places name the car directly instead, and a second platform would need its own entry in each:

- `FW_VERSIONS` (`fingerprints.py`) and both `FW_QUERY_CONFIG.non_essential_ecus` lists (`values.py`)
- the platform's DBC names in the `CAR` entry
- `STEER_THRESHOLD` (`values.py`)
- `minEnableSpeed` (`interface.py`; its gas-interceptor exemption tests the category)
- `NUDGE_FIRM` (`desire_helper.py`, keyed by the string)
- `LANE_CHANGE_SPEED_FLOOR` (`desire_helper.py`, keyed by the string; its test checks the keys are `HONDA_ELESYS`)
- `STOPPING_SPEED` and `STOPPING_DECEL_RATE` (`stopping_tune.py`, keyed by the string)
- `override.toml` (torque prior), `car_list.json` and `routes.py`

`interface.py` sets the safety flag:

```python
if candidate in HONDA_ELESYS and ret.openpilotLongitudinalControl:
  ret.safetyConfigs[-1].safetyParam |= HondaSafetyFlags.ELESYS_SCM_STANDDOWN.value
```

This interface always sets `openpilotLongitudinalControl` to `True` on Nidec, so on this car the stand-down safety mode is always on.

### 3.2 The bus layout the code assumes

This table comes from code comments and commit messages (opendbc `a091808d`, route `000000b9`; the `car_helpers.py` comment; `S:/OP/radar_firmware_bit_spec.md`). I have not checked the harness.

| panda bus | what is on it | how the code uses it |
|---|---|---|
| 0 | the car network, the LKAS camera and the gateway board | `Bus.pt` parser. openpilot's steering, brake, ACC HUD, pedal and 0x500 frames go out here. The 4-byte `LKAS_HUD` (0x33D) is read here. With the Stage 10 board image it is the board's own frame (board `df42a0d`, Stage 6: the board owns 0x33D and mutes the camera's), and its `LKAS_PROBLEM` carries the EPS's error state |
| 1 | the Elesys radar's own bus: 0x300, 0x301, 0x400, 0x410-0x417, 0x420-0x424, 0x4FF | `RadarInterface`. This is also the bus OBD multiplexing takes over (2.3) |
| 2 | the stock ACC module's side of the harness split, which is the radar's car connector | `Bus.cam` parser. On this car `cp_cam` reads the **radar**, not a camera: the stock `BRAKE_COMMAND` (0x1FA) and `ACC_HUD` (0x30C) come from here. openpilot re-sends `SCM_BUTTONS` here |

Two consequences come up throughout this document. `LKAS_PROBLEM` is read from bus 0 on this car, while every other Honda reads it from the camera bus. And 0x500 goes out on bus 0; opendbc `a091808d` moved it there from bus 2, where the board never saw it.

### 3.3 What differs from stock Honda handling

| topic | upstream Nidec | HONDA_ELESYS | section |
|---|---|---|---|
| stock ACC | openpilot replaces it and the camera's ACC frames are blocked | the Elesys radar is the ACC module. The driver's `SCM_BUTTONS` are blocked on their way to it and re-sent with `MAIN_ON=0`, so the stock ACC stands down while CMBS keeps working | 7.5, 8 |
| panda stock-AEB forward gate | `0x1FA` bit 29 (`AEB_REQ_1`) | bit 43 (MSB of `FCW`) | 8 |
| `carState.stockAeb` | `AEB_REQ_1 and COMPUTER_BRAKE > 0` | `CMBS_BRAKE or AEB_REQ_3 or AEB_REQ_2 or AEB_STATUS == 1` | 6.3 |
| `LKAS_HUD` (0x33D) | openpilot sends it | openpilot never sends it; on bus 0 it comes from the board (Stage 10 image), or from the camera when no board is fitted | 7.4 |
| `carFaultedNonCritical` | `LKAS_PROBLEM` from the camera bus | `LKAS_PROBLEM` from bus 0 | 6.2 |
| steering | 0x0E4 goes to the EPS | 0x0E4 goes to the gateway board | B |
| gas/brake split | `compute_gb_honda_nidec()` | `compute_gb_honda_elesys()` | 7.1 |
| brake pump | `brake_pump_hysteresis()` | `brake_pump_hysteresis_elesys()` | 7.2 |
| gear | `GEARBOX_AUTO` 0x1A3, or the `manual` fallback | legacy `GEARBOX_AUTO` 0x188, with a Sport dwell | 4.7, 5.1, 6.1 |
| radar tracks | 0x430-0x439 and 0x440-0x445 at 20 Hz, trigger 0x445 | 0x410-0x417 and 0x420-0x424 at 10 Hz, trigger 0x424 (0x423 until 2026-10-24), empty slot = raw `LONG_DIST` 0x7FFF | 4.8, 5.2 |
| `BRAKE_COMMAND.SET_ME_1` | constant 1 | cluster units flag | 7.3 |
| gas interceptor law | `interp(v, [0, 10], [0.4, 1.0])` | `elesys_gas.py`: v2, the measured pedal response (default), or v1, `elesys_gas_multiplier()` | 9.2 |
| fuel | not decoded | `fuelGauge` from `FUEL_LEVEL` | 6.4 |
| `steerActuatorDelay` | 0.15 (default branch) | 0.18, so both lag fallbacks (+0.2) are the measured 0.38 | 5.1 |
| `steerAtStandstill` | False | True | 5.1 |
| torqued prior | substitute or fleet value | own `[1.25, 1.25, 0.18]` in `override.toml` (1.1 until 2026-10-03) | 2.4 |
| `latAccelOffset` start | 0.0 | -0.43 (CarParams; torqued starts from it) | 2.4 |
| reported torque (`carOutput.actuatorsOutput.torque`) | `last_torque` | 0.0 while the gateway board is not actuating; deliberately NOT clipped to the board's blinker ceiling (`0x704 INH_BLINKER`): clipped, the ceiling counted as limited by safety and `steerSaturated` could not fire while the blinker was on (2026-10-12); from board `b5f08583` there is no blinker ceiling at all (area B, LKAS-GATEWAY-PROTOCOL.md 3.6) | B |
| `0x500 OP_SATURATED` | n/a | selfdrived's `steerSaturated` conditions, not the raw `lac.saturated` (2026-10-12; LKAS-GATEWAY-PROTOCOL.md 4.2) | B |
| `minEnableSpeed` | 25.51 mph, or -1 with a gas interceptor (upstream `4455464a`) | 19 mph, pedal or not | 5.1 |
| stopping speed | `should_stop()`: 0.3 m/s on the measured speed | 0.8 m/s (`stopping_tune.py`) | 10.1 |
| stopping ramp toward `stopAccel` | 1.0 m/s³ | 0.8 m/s³ (`stopping_tune.py`) | 10.1 |

---

## 4. DBCs

### 4.1 How the car DBC is assembled

`opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc` (new) is a list of imports plus one message (three since 2026-10-03: `VSA_1AA` 0x1AA and `VSA_3D9` 0x3D9, the provisional VSA-fault frames of 6.6, which carstate does not read; their Honda checksum and counter were checked on 7.6 and 0.76 million logged frames). The generator writes `opendbc/dbc/honda_accord_au_2015_can_generated.dbc`. That output is gitignored (`.gitignore`: `opendbc/dbc/*_generated.dbc`), so only the sources are committed.

| import | status | compared with `acura_ilx_2016_can.dbc` (upstream Nidec with the same flags) |
|---|---|---|
| `_community.dbc` | upstream | same |
| `_honda_elesys_base.dbc` | **new** (4.2) | replaces `_honda_common.dbc` |
| `_nidec_common.dbc` | upstream, **modified** (4.3) | same file |
| `_lkas_hud_4byte.dbc` | **new** (4.4) | replaces `_lkas_hud_5byte.dbc` |
| `_nidec_scm_group_a_elesys.dbc` | **new** (4.5) | replaces `_nidec_scm_group_a.dbc` |
| `_steering_sensors_c.dbc` | upstream | the ILX uses `_b` |
| `_steering_control_e.dbc` | **new** (4.6) | replaces `_steering_control_a.dbc` |
| `_gearbox_legacy.dbc` | **new** (4.7) | replaces `_gearbox_common.dbc` |
| `_sunnypilot_linbus_gw.dbc` | **new**, areas B and A | 0x500, 0x700, 0x704, 0x707, 0x70B, 0x70F |

The one message defined inline:

```
BO_ 545 ECON_STATUS: 3 SCM
 SG_ ECON_ON : 23|1@0+ (1,0) [0|1] "" EON
 SG_ COUNTER : 21|2@0+ (1,0) [0|3] "" EON
 SG_ CHECKSUM : 19|4@0+ (1,0) [0|15] "" EON
```

On this car 0x221 is 3 bytes at 25 Hz, not the 6-byte `ECON_STATUS` the other Honda DBCs describe. The layout was checked against 628,053 logged frames. `ECON_ON` read 0 for 7 hours because ECON was off the whole time, and the owner confirmed the bit by pressing the button (opendbc `2bc5c4db`; the comment above `LEARN_ECON` in `dynamic_tuning.py`; `CHANGELOG-elesys.md` section 2). A long comment documenting the layout used to sit on this message and was dropped in `2f19864a`. The reasoning now lives in the two places just cited.

The fragments that are copies of upstream files do not pick up upstream fixes. After every merge, diff them against their upstream originals (4.9).

### 4.2 `_honda_elesys_base.dbc` (new)

This is a copy of upstream `_honda_common.dbc` as it was at the fork point. The differences:

| message | upstream `_honda_common.dbc` | this copy |
|---|---|---|
| file header | none | `CM_ "Modified 7bit STALK_STATUS";` |
| `CAMERA_MESSAGES` 0x35E (862) | 8 bytes | 7 bytes. **`CHECKSUM 59:4` and `COUNTER 61:2` were not moved**, so they sit in byte 7, which a 7-byte frame does not have |
| `STALK_STATUS` 0x374 (884) | 8 bytes; `WIPER_SWITCH 53:2`; `COUNTER 61:2`, `CHECKSUM 59:4` (range 0-15) | 7 bytes; no `WIPER_SWITCH`; `COUNTER 53:2`, `CHECKSUM 51:4` (range 0-3) |
| `STEER_MOTOR_TORQUE` 0x1AB (427) | has `UNKNOWN_TORQUE_STATE_BIT 3:1` and a comment on it | the signal and its comment removed |
| comments on 304/316 | `CM_ SG_ 304 ...`, `CM_ SG_ 316 ...` | `CM_ BO_ 304`, `CM_ BO_ 316`. A `CM_ SG_` with no signal name made cantools reject the whole file (opendbc `02e7fd71`) |
| `VSA_STATUS` 0x1A4 (420), `VEHICLE_DYNAMICS` 0x1EA (490) | no fault bits | the provisional `VSA_FAULT_*` signals and a `CM_ SG_` for each (2026-10-03, 6.6): `VSA_FAULT_LIVE_B2_2`/`_B2_3` (18, 19), `VSA_FAULT_LAMP_B3_3`/`_B3_5`/`_B3_6`/`_B3_7` (27, 29-31), `VSA_FAULT_STORED_B4_0` (32), `VSA_FAULT_LAMP_B4_1` (33), `VSA_FAULT_LAMP_B6_0` (48); `VSA_FAULT_INERTIAL_INVALID` (50) |

The intent was to match the lengths this car sends: Honda checksum and counter positions depend on the last byte, so a wrong length would make a parser drop every frame. `STALK_STATUS` is done correctly. **`CAMERA_MESSAGES` is internally inconsistent** (7 bytes with its checksum and counter in byte 7). It is harmless today only because nothing in `opendbc/car/honda/` or `opendbc/sunnypilot/car/honda/` reads `CAMERA_MESSAGES` or `STALK_STATUS`. Fix the checksum and counter positions (by analogy with `STALK_STATUS`: `CHECKSUM 51:4`, `COUNTER 53:2`, but verify against a log) before anything parses it.

Area B notes that 0x1AB does not exist on this car at all (opendbc `23dce590`). Defining it does no harm because nothing on this platform reads it.

### 4.3 `_nidec_common.dbc` (shared, modified)

Three read-only signals, with comments, were added to `BRAKE_COMMAND` 0x1FA (506):

| signal | position | meaning |
|---|---|---|
| `CMBS_BRAKE` | `10:1` | believed to be CMBS actively braking: 0 for 7 h of logging, 1 during a real CMBS event |
| `CMBS_DISABLED` | `12:1` | a previously unmapped bit, named but not interpreted |
| `AEB_REQ_3` | `27:1` | on this car, the request bit that actually asserts during a CMBS event. `AEB_REQ_1` at bit 29 does not |

A comment was also added to `AEB_REQ_2`. **Every Nidec DBC imports this file**, so every Nidec platform now has these names. openpilot never sets them (`create_brake_command()` leaves them at 0), so nothing any other car transmits has changed. If upstream reorganises `_nidec_common.dbc`, keep the three signals or move them into an Elesys-only fragment.

### 4.4 `_lkas_hud_4byte.dbc` (new)

`LKAS_HUD` 0x33D (829), **4 bytes**, as the stock camera on this car sends it:

`LKAS_READY 0:1`, `LKAS_STATE_CHANGE 6:1`, `CAM_TEMP_HIGH 7:1`, `STEERING_REQUIRED 8:1`, `RDM_HUD 9:1`, `SOLID_LANES 10:1`, `LKAS_OFF 11:1`, `LKAS_PROBLEM 12:1`, `DTC 13:1`, `DASHED_LANES 14:1`, `BEEP 17:2`, `RDM_PROBLEM 21:1`, `RDM_HUD_2 23:1`, `CHECKSUM 27:4`, `COUNTER 29:2`.

openpilot reads only `LKAS_PROBLEM` (6.2) and never packs this message on this car.

The file carries `CM_ SG_ 829 CAMERA_OVERHEAT ...`, a comment for a signal the message does not define. This is inherited: upstream `_lkas_hud_5byte.dbc` and `_lkas_hud_8byte.dbc` carry the same line and do not define the signal either, and opendbc's own parser loads those. I did not check whether a strict cantools load accepts it (see 4.9).

### 4.5 `_nidec_scm_group_a.dbc` (shared, modified) and `_nidec_scm_group_a_elesys.dbc` (new)

**Shared file.** `SCM_BUTTONS` 0x1A6 gains `CMBS_BUTTON 11:2`, with `VAL_ 3 button_press, 2 far_distance, 1 near_distance, 0 normal`. Every group-A Nidec car sees this. It is a read-only definition: `spam_buttons_command()` packs only `CRUISE_BUTTONS` and `CRUISE_SETTING`, so no other car's transmitted frame changes. This hunk has no fork marker.

**Elesys copy**, imported instead of the shared file. It is identical to the shared file, `CMBS_BUTTON` included, plus three signals verified only on this car. The copy exists so that the shared file stays exactly what every other Nidec car has been driven on (file header comment).

| message | signal | position | meaning and evidence |
|---|---|---|---|
| `SCM_BUTTONS` 0x1A6 | `FUEL_LEVEL` | `31:8` | fuel remaining as the meter shows it. Best fit is 0.5 L per count. The meter clamps it at 105 (about 52 L of a tank of about 60 L). Found over 38 routes, Jul-Sep 2026. There is no separate low-fuel lamp bit on this bus |
| `SCM_BUTTONS` 0x1A6 | `FUEL_SENDER` | `39:8` | the same quantity before the clamp, inverted: `207 - 1.53 * FUEL_LEVEL`, RMS error 0.5 counts. Bit 39 is not a warning lamp |
| `SCM_FEEDBACK` 0x294 (660) | `ODOMETER_KM` | `31:24` | odometer in km; a 42.05 km route advanced it by 42. **Decoded but not published** anywhere. Only the integration test reads it |

Source: opendbc `a3bd42b4`, `CHANGELOG-elesys.md` section 19.

**A side effect to keep in mind:** `create_scm_buttons_no_cruise()` (7.5) re-sends `SCM_BUTTONS` to the radar by copying every signal this DBC names. Any bit of 0x1A6 that no signal covers reaches the radar as 0. So adding or removing a signal in this fragment changes the frame the radar receives.

### 4.6 `_steering_control_e.dbc` (new)

This is a copy of upstream `_steering_control_a.dbc` with two changes:

1. Byte 2 of `STEERING_CONTROL` 0x0E4 is split into `SET_ME_X00 22:1`, `LDW_RIGHT 21:1`, `LDW_LEFT 20:1` and `SET_ME_X00_3 19:4`. That is area B: the board reads byte 2 as a bit field, and bit 2, `SERIAL_DOMAIN`, must stay 0. See the area B document.
2. **`STEER_STATUS` 0x18F (399): `STEER_CONTROL_ACTIVE` moves from `35:1` to `32:1`.** This is a car-specific remap. It arrived in `2f19864a` (`Updated DBC`) without a recorded measurement. Area B's latch detection (`_update_driver_torque_validity()`, opendbc `23dce590`) depends on it, and that analysis found the torque freeze correlating 1.000 with `STEER_CONTROL_ACTIVE` decoded at bit 32. That is indirect evidence that bit 32 is right on this car.

Only `honda_accord_au_2015_can.dbc` imports this fragment.

### 4.7 `_gearbox_legacy.dbc` (new)

`GEARBOX_AUTO` at **0x188 (392)**, 6 bytes: `GEAR_SHIFTER 27:4`, `GEAR 36:5`, `COUNTER 45:2`, `CHECKSUM 43:4`, with `VAL_ 392 GEAR_SHIFTER 1 P, 2 R, 4 N, 8 D` and `VAL_ 392 GEAR 26 S, 4 D, 3 N, 2 R, 1 P`. The file also carries a copy of upstream `GEARBOX_CVT` 0x191 (401), which this car does not send.

Because this fragment gives 0x188 the name `GEARBOX_AUTO`, the default `gearbox_msg = "GEARBOX_AUTO"` in `carstate.py` resolves to 0x188 on this car and to 0x1A3 on every other Honda.

`GEAR_SHIFTER` raw 0 deliberately has no VAL entry. On this car it means both Sport and `between detents`, so mapping 0 to S would produce a phantom Sport on every shift. Over 473k frames there were 47 zero runs, median 3 frames and max 520 ms, and all of them were shift transients. S itself reads `GEAR = 26`, which is measured (6.1; this line used to say 26 had never been observed).

Upstream has since added `11 B` to the `GEARBOX_CVT` VAL table in `_gearbox_common.dbc`. The copy here will not get it. That does not matter on this car, which has no 0x191.

### 4.8 `opendbc/dbc/honda_accord_2015au_radar.dbc` (new, written by hand, not generated)

`VERSION "Accord 9G AU Radar BUS 1"`. Messages:

| message | id | notes |
|---|---|---|
| `RADAR_VEHICLE_STATE` | 0x300 (768) | `VEHICLE_SPEED 15:8` kph |
| `RADAR_VEHICLE_STATE2` | 0x301 (769) | no signals |
| `RADAR_DIAGNOSTIC` | 0x400 (1024) | `RADAR_STATE 7:8`, `NOT_READY 15:8`, `RADAR_FLAGS 23:8` |
| `RADAR_TRACK_A0`..`A7` | 0x410-0x417 (1040-1047) | `LONG_DIST 6:15` x0.0078125 m, `LAT_DIST 20:13` signed x0.0078125 m, `FLAG_B21`, `COASTING 22:1` (`FLAG_B22` until 2026-10-24), `NEW_TRACK 23:1`, `REL_SPEED 37:14` signed x0.015625 m/s (1/64; 1/128 until 2026-10-09, below), `CHECKSUM`, `COUNTER`. A0 also has `NEW_SIGNAL_1 55:5` |
| `RADAR_TRACK_B0`..`B4` | 0x420-0x424 (1056-1060) | same layout as the A tracks |
| `RADAR_STATUS_4FF` | 0x4FF (1279) | `STATUS_A`..`STATUS_D` |

The signal names `LONG_DIST`, `LAT_DIST`, `REL_SPEED`, `NEW_TRACK`, `COASTING` and `RADAR_STATE` must not change: `radar_interface.py` reads the first five by name in `_update_elesys_track()` and `RADAR_STATE` on the path it shares with Nidec. Background is in `S:/OP/radar_firmware_bit_spec.md` and `S:/OP/HOW_OP_USES_RADAR.md`. The second describes an older fork whose ranges skipped 0x417 and 0x424; the current code reads both.

**`REL_SPEED` is 1/64 m/s (fixed 2026-10-09).** Until then this file said 1/128, which halved the `vRel` of every radar track, and with it `vLead`, `vLeadK` and `aLeadK` of every radar-sourced lead. Measured on the logged bus 1 of routes 113 and 120: d(`LONG_DIST`)/dt was 2.01-2.03 x the old decode on the A group (B: 2.00-2.01 by median ratio), and stationary objects read -0.50 x vEgo. At 1/64 the slope is 0.99-1.01 on the A group of six routes (B: 1.00-1.01 by median ratio, few windows) and stationary objects read -1.00 x vEgo. These slopes hold with the car above about 4 m/s. At walking pace they are 0.93 (10f, 950 windows) and 0.83 (c9, 1,261 windows), median ratios 0.99 and 0.95: near-range stationary returns read about -vEgo while their `LONG_DIST` closes and then jumps back, so they are not physically consistent. Byte 4 bits 7:6 are never set and negative values sign-extend from bit 37, so the 14-bit signed layout was right and only the factor was wrong. `LONG_DIST` agrees with the camera (-0.2 m median under 8 m) and `0x300 VEHICLE_SPEED` reads 0.99 x vEgo in km/h; neither changes, and no other track signal is a speed. The earlier check in `S:/OP/radar_firmware_bit_spec.md` was a correlation (r = 0.994), which cannot see a constant factor. Pinned by `opendbc/car/honda/tests/test_elesys_radar.py` with logged frames; the replay is in `docs/CHANGELOG_SERIAL_STEERING.md` (2026-10-09).

**The track slots (2026-10-24).** Measured on the logged bus 1 (the radar audit of 2026-10-24 and this change; `radar_interface.py` in 5.2): a radar cycle is 0x410-0x417, 0x420-0x424, then 0x4FF, 0x300, 0x301 and 0x400, every 0.1 s, and 0x424 is its last track frame in every cycle the log holds whole (84,853 cycles on 6 routes, and 1.25 M on 126 in the review; in the CAN batch after 0x423 in 2.3-2.5% of them). `LONG_DIST` raw 0x7FFF (255.99 m) is an empty slot, the only empty pattern in 9.9 M frames; real returns reach 255.8 m. `NEW_TRACK` (byte 2 bit 7) marks a new object in the slot; set on two frames in a row, the second frame is another new object 92% of the time (1,467 repeats on 6 routes) and the same object otherwise. Byte 2 bit 6, `FLAG_B22` until 2026-10-24, is `COASTING`: the radar extrapolated the track that cycle instead of measuring it (when set, `REL_SPEED` repeats the previous frame 89.6% of the time; set on 36-38% of stationary clutter frames and 0.3% of camera-confirmed lead frames). The B slots (0x420-0x424) carry oncoming traffic 98% of the time. There is no strength or quality field. The DBC carries this as comments on `LONG_DIST`, `NEW_TRACK` and `COASTING`.

**What the true scale makes worse: stationary returns become stopped obstacles below about 36 km/h.** At 1/128 a stationary return read as a lead doing half our speed; at 1/64 it reads as stopped, and when radard makes it the lead the MPC brakes for it. Two unchanged upstream paths in `openpilot/selfdrive/controls/radard.py` do that:

- below 4 m/s, the low-speed override (`potential_low_speed_lead`, :102-105, used in `get_lead`, :173-180) takes the closest track within 1 m of the path and 0.75-25 m ahead, with no camera confirmation and no speed check. The near-range stationary returns above are what it finds at walking pace;
- whenever the camera lead is under 10 m/s, the camera match (`vel_sane`, :133) accepts a stationary track, because `|vLead - camera v| < 10`.

A plan replay (old vs new decode, episodes with the new `aTarget` at or below -2 and at least 1 m/s² under the old one): 144 on nine routes, 94 of them below 4 m/s and 93 of those on a stationary radar lead, only 2 engaged; 75 on six crawl-heavy routes (01, 06, 07, 08, 10, all of 14), 10 engaged, 6 of those with the gas pressed. Engaged without gas, route 14 t=194.3: -2.75 m/s² for 0.9 s against the old -1.17 at 7.5 m/s, on a return 12.4 m ahead and 1.4 m to the side that the car then drove past. Requests reach -3.5 m/s², past the 2.6 m/s² brake ceiling, and c8 t=2321.1 (disengaged) shows a false FCW by the same mechanism. In return, with the camera lead over 10 m/s the match check now rejects stationary returns the old decode passed (their half-scale vLead was over 3 m/s): the logged phantom brakes at route 120 t=116.6 (-2.31 at 16.5 m/s) and 10f t=2065 (-0.94 at 22 m/s) go away. Full numbers and the driving guidance are in the CHANGELOG entry.

The mitigation is radard's clutter guard, `HONDA_ELESYS` only (5.3, 2026-10-09). Until a stop-and-go drive on it is judged, engaged stop-and-go and close follow are unvalidated, and car parks and driveways are not for engaged driving.

### 4.9 DBC merge checklist

- Diff `_honda_elesys_base.dbc` against the new upstream `_honda_common.dbc`, `_steering_control_e.dbc` against `_steering_control_a.dbc`, and `_nidec_scm_group_a_elesys.dbc` against `_nidec_scm_group_a.dbc`. Port any upstream fix that applies to this car.
- Keep the three `BRAKE_COMMAND` signals in `_nidec_common.dbc` and `CMBS_BUTTON` in `_nidec_scm_group_a.dbc`.
- Before driving, regenerate the DBCs and load the generated file through opendbc's parser. The fork has also done a strict cantools load (that is how `02e7fd71` was found). If you repeat it, expect two known quirks that the opendbc parser tolerates: the dangling `CAMERA_OVERHEAT` comment (4.4) and the misplaced `CAMERA_MESSAGES` checksum/counter (4.2).

---

## 5. Interface and radar

### 5.1 `interface.py`

All of these are in `CarInterface._get_params()`.

| change | code | why | re-apply |
|---|---|---|---|
| transmission | `if candidate in HONDA_ELESYS and 0x188 in fingerprint[CAN.pt]: ret.transmissionType = TransmissionType.automatic`, placed before upstream's `elif all(msg not in fingerprint[CAN.pt] for msg in (0x191, 0x1A3))` | the car has neither 0x191 nor 0x1A3. It fell through to `manual`, and `carstate.py` then reported drive or reverse from `REVERSE_LIGHT`: drive while in Park, and S never visible. On route `15646e8515eda1a7`, 0x188 is on bus 0 at 100 Hz and decodes cleanly over 43.7 min (opendbc `2bc5c4db`). Limited to Elesys on purpose: `ACURA_RDX` has the same shape, but there is no data for it here | must come before the manual fallback |
| `longitudinalActuatorDelay = 0.6` | inside the Nidec `else:` longitudinal-tuning branch, under `if candidate in HONDA_ELESYS:` | measured for the original port (opendbc `04a48a0a`); `S:/OP/redlight_overshoot_findings.md` was run with this value | - |
| stopping speed 0.8 m/s | **not here any more**: `ret.vEgoStopping = 0.8` was in this block until the 2026-09 merge. Upstream moved `vEgoStopping` into `CarParams.deprecated`, where assigning it raises `AttributeError`, so the line was deleted and the value moved to sunnypilot's `stopping_tune.py` (10.1). A comment in the block says so | the car crawls at 0.55-0.7 m/s approaching a stop, so under the old default 0.5 `shouldStop` never latched: the stopping state was active in 365 of 94,519 engaged frames and the stopAccel ramp never ran (`S:/OP/redlight_overshoot_findings.md`; opendbc `470cd311`). Upstream's 0.3 would be worse | never re-add a `vEgoStopping` line |
| `stopAccel = -0.8` | same block | the default -2.0, on top of the creep offset, commanded cb 253 of 255 at a stop and held it for 7.8 min of a 65 min drive. The car does not need that much: 32 frames of motion in 46,815 hold frames (routes `15646e8515eda1a7` 1f and 20). -0.8 puts the hold at about cb 189-192, with about 0.8 m/s^2 of margin, roughly an 8% grade. The code comment says: if a stop ever creeps, raise this back toward -1.2 before touching the creep table | upstream still reads `CP.stopAccel` in `longcontrol.py` |
| `steerActuatorDelay = 0.18` | `if candidate in HONDA_ELESYS:` after the lateral tuning chain | the command goes to the board and out on 9600-baud serial, and the car's delay is ~0.38 s: lagd measured 0.383 s (route `000000d3`), 0.377 s (`000000d4`) and 0.342 s (12 blocks, by `000000fd`). Nothing reads this value bare. Before lagd has blocks it publishes `initial_lag` = this + 0.2, and with `LagdToggle` off `LagdToggle.update()` returns this + `LagdToggleDelay` (default 0.2; upstream `53e13a7bc`). Until 2026-10 the line was 0.38, so both fallbacks were 0.58 s, and lagd sat at 0.580 "unestimated" from `d7` to partway through `fd` (about 5 h; it learns only above 50 mph) while its own running estimate read 0.36-0.39. 0.18 lands both on 0.38. lagd's cache key is fingerprint + `VERSION`, so the change keeps a learned value. Other readers: the big UI's "Actuator Delay" line with `LagdToggle` off (0.18 + 0.20 = 0.38) and the torque extension's initial jerk time, which the live lag overwrites every frame. This replaces the scoped `lagd.py` hunk UPSTREAM-2026-09.md item 7 held in reserve. Pinned by `TestElesysSteerDelay` (opendbc) and `test_lagd_elesys.py` | the reason is area B |
| `steerAtStandstill = True` | same block | keeps `latActive`, and with it `STEER_TORQUE_REQUEST`, alive at a stop so the board keeps the cluster's lane graphic up. The board holds its target at 0 below 5 km/h (`GW_STANDSTILL_CPH`) (opendbc `bb0fe222`). Upstream `controlsd.py` still reads `CP.steerAtStandstill` in the same `latActive` expression | the reason is area B |
| `latAccelOffset = -0.43` | same block, last, guarded by `lateralTuning.which() == 'torque'` | the seed torqued and the torque controllers start from (2.4). Must ship with the `override.toml` prior, whose cache reset would otherwise restart the offset at 0 | keep it after `configure_torque_tune()`; the sunnypilot `torqued.py` hunk reads it, the sunnypilot `interfaces.py` hunk keeps it across the toggles' re-run |
| stand-down safety param | see 3.1 | see 8 | - |
| `minEnableSpeed = 19 mph` | `elif candidate in (CAR.HONDA_ODYSSEY_TWN, CAR.HONDA_ACCORD_9G_AU):`, and in `_get_params_sp()`: `stock_cp.minEnableSpeed = -1. if ret.enableGasInterceptor and candidate not in HONDA_ELESYS else stock_cp.minEnableSpeed` | from the original port (opendbc `04a48a0a`); no measurement recorded. Upstream `4455464a` sets `-1` for every gas-interceptor car; the merge exempts `HONDA_ELESYS` so this car keeps 19 mph (a replay of `CarParams` gives 8.494 m/s). With the pedal `pcmCruise` is False, so the value never gated engagement through `belowEngageSpeed`; what `-1` would have changed is the `manualRestart` warning at a standstill | keep both lines; the exemption is tagged `FORK(HONDA_ACCORD_9G_AU)` |

The car takes the default lateral branch (2.4). That branch first sets `steerActuatorDelay` to 0.15, and the Elesys block then overwrites it.

### 5.2 `radar_interface.py`

- `_create_nidec_can_parser()`: for `HONDA_ELESYS` it parses `[0x400] + list(range(0x410, 0x418)) + list(range(0x420, 0x425))` at 10 Hz on bus 1, instead of 0x400, 0x430-0x439 and 0x440-0x445 at 20 Hz.
- `self.radar_type = 'Elesys' if CP.carFingerprint in HONDA_ELESYS else 'Nidec'`.
- `self.trigger_msg = ELESYS_TRIGGER` (0x424) for Elesys, 0x445 otherwise. 0x424 is the last track frame of every radar cycle (4.8). Until 2026-10-24 it was 0x423 (the reason was not recorded), which left slot 0x424 a cycle stale whenever its frame came in the CAN batch after 0x423 (2.3-2.5% of cycles; route 141 t=102.7, an oncoming car's first frame at 94.68 m never reached radard). In those cycles the update now comes one CAN batch (about 10 ms) later. 0x400, sent after 0x424, is read a cycle late as before. A cycle whose 0x424 never comes is not published on its own: the next cycle's frames overwrite it and publish on their 0x424. The logs show that only where the radar stops mid-cycle at the end of a drive (b1 t=1719.08) or where the log itself is thinned (aa from t=903: one CAN batch every 20.5 s).
- Fault: `self.radar_fault = cpt['RADAR_STATE'] not in (104, 111, 125)` for Elesys, `!= 0x79` otherwise. `radar_wrong_config` stays `RADAR_STATE == 0x69` for both. The commits do not record where 104/111/125 came from (opendbc `04a48a0a`, `304d1d82` `Fixed Radar Range`).

Track decoding. Since 2026-10-24 the Elesys slots take their own branch, `_update_elesys_track()`; the Nidec branch is upstream's shared path (`LONG_DIST < 255`, a new trackId on every `NEW_TRACK`), untouched. In the Elesys branch:

- a slot is empty only at raw `LONG_DIST` 0x7FFF (`ELESYS_LONG_DIST_EMPTY`, exact in a float). `< 255` dropped real returns at 255.0-255.98 m (about 5 frames an hour; route dd t=804.45, a car first seen at 255.34 m);
- a new trackId when the slot was empty, when `NEW_TRACK` rises, or when `NEW_TRACK` stays set on a frame that does not continue the slot's object (`elesys_same_object()`: the range within `ELESYS_SAME_D` 1 m of the last frame's moved on 0.1 s at its vRel, the lateral position within `ELESYS_SAME_Y` 1 m, vRel within `ELESYS_SAME_V` 2 m/s; 94.5-97.5% of frames without `NEW_TRACK` meet it, 1.4-5.5% of rising ones). A held flag on the same object (route 113 t=87.8) keeps its id, and with it radard's Kalman filter and the clutter guard's per-track history (5.3). A rising `NEW_TRACK` is always a new id, even on a frame that continues the slot's object (2.4% of rising edges on an occupied slot; route dd t=2647.96), as upstream's rule gave it: the same-object check is for held flags only. It is not "a new id on a rising edge only", the audit's proposal: 92% of held flags are another object (route 113 t=31.2: three objects at 90.8, 49.0 and 81.2 m on three frames in a row), and joining them under one id fed radard's Kalman filter a speed step and let clutter inherit a track's hold count in the replay (CHANGELOG-elesys.md 25);
- `dRel = LONG_DIST`, `yRel = -LAT_DIST`, `vRel = REL_SPEED`, and `deprecated.measured = not COASTING`.

Upstream removed the `aRel`/`yvRel`/`measured` assignments in the same function (since its #3546 they are `RadarPoint.deprecated`) and moved `track_id` into the base class; the 2026-09 merge took both. The Elesys branch writes `deprecated.measured` on purpose: it is the same field (@6), so `radarTracks` in a route carries the coasting flag. Nothing reads it: radard, the clutter guard, `held_lead.py`, the planner, the UI and the replay tools take `trackId`, `dRel`, `yRel` and `vRel` only.

`vRel = REL_SPEED` goes straight through: the scale lives in the DBC (4.8). Nothing in this file, in radard or in the fork was tuned on the half-scale value that file carried until 2026-10-09. The true scale does change which radar tracks radard turns into stopped obstacles at low speed (4.8, "What the true scale makes worse"); that is radard's track selection, not this file, and the guard in 5.3 handles it.

Re-apply: keep the `radar_type` switch, the three Elesys branches (parser, trigger, fault), `_update_elesys_track()` with `elesys_same_object()`, the `ELESYS_*` constants and `new_track_last`. `test_elesys_radar.py` (12 tests) runs logged frames through `RadarInterface`: a stationary object reads -vEgo and its `dRel` changes at `vRel`; the empty slot, a car at 255.3 m (dd), the update on a late 0x424 (141), a rising `NEW_TRACK` on a frame that continues the object (dd), a held one on the same object and on three different ones (113), and `COASTING`. Nothing else covers this file directly.

### 5.3 radard's clutter guard (`elesys_radar_guard.py`, 2026-10-09)

**What.** On `HONDA_ELESYS` only, radard picks a radar track as the lead in two places, and the guard restricts both:

- **The camera match** (`get_lead()` -> `match_vision_to_track()`): a track slower than `V_CHECKED` (3 m/s) is a candidate only if the camera lead's speed minus the track's `vLead` is at most `speed_tolerance()`. That is `V_TOL_MIN` (3 m/s) up to 20 m of camera range, `V_TOL_PER_M` (0.15 m/s per meter) beyond, and at most `V_TOL_MAX` (10 m/s, upstream's `vel_sane` window) from 67 m. Between 20 and 67 m this deliberately sits below the 99th percentile of the camera's lag on real stopped traffic (5.7-9.8 m/s), because the clutter's median (7.6-8.1 m/s) is there too; a stopped car the camera still reads as fast then goes to the camera's lead (0.9 s of real-object radar lead engaged at 4 m/s and over in the 13-route replay). Faster tracks, and tracks faster than the camera, are always candidates. If no track is a candidate the lead is the camera's.
- **The low-speed override** (below 4 m/s): a **moving** track always qualifies, as upstream. Moving is counted in radar updates, not radard frames (the radar reports at 10 Hz, radard runs at 20 Hz and sees each reading twice; radard passes `recv_frame['radarTracks']` so the guard can tell), and it must be backed by the track's own range: its range plus the car's travel, its point in the world, must move away at `V_WORLD` (0.7 m/s) or more and at least `WORLD_RATIO` (half) of its radar speed. A track is **latched** as moving, for its life, once its raw `vLead` has been at least `V_MOVING` (1 m/s) for `MOVING_UPDATES` (3) updates in a row and `WORLD_SPAN` (0.5 s) of range history agrees. It is **moving now** while its `vLeadK` and its raw `vLead` have both been at least `V_MOVING_NOW` (1.5 m/s) and `V_MOVING` for `MOVING_NOW_UPDATES` (2) updates and its range history agrees; a track younger than `NOW_SPAN` (0.3 s), a cut-in, is taken on its speed alone. Only moving away counts. Any other track qualifies only if the camera lead is confident and the track is within `OVERRIDE_D_TOL` (1.5 m) of its distance or `OVERRIDE_V_TOL` (1 m/s) of its speed. Confident: radard's filtered prob rose above `CAMERA_ON` (0.55) and has not since fallen to `CAMERA_OFF` (0.5, radard's own threshold).
- **The hold** (below 4 m/s, when radard has no lead at all): the closest in-path track the override refused, once seen for `HOLD_UPDATES` (5) radar updates, becomes a lead that the planner's MPC neither accelerates toward nor brakes for: at the car's own speed, not accelerating, at the MPC's desired distance for that speed on its shortest time gap (`STOP_DISTANCE` 6 m + `HOLD_T_FOLLOW` 1.25 s x `vEgo`; both copy `long_mpc`'s, and a test pins them). The planner takes the lower of the MPC and e2e, so the plan stops accelerating toward the object; the MPC's output stays within 0.05 m/s² of 0 (0 on aggressive; -0.005 to -0.043 on standard and relaxed between 0.8 and 3.9 m/s). Its `dRel` is therefore not the track's range, and its `modelProb` is 0, so it never raises an FCW.
  - **Below the car's stopping speed (0.8 m/s, `stopping_tune.STOPPING_SPEED`) holding speed is a stop.** The MPC's output is then under `should_stop()`'s 0.1, so the planner asks to stop, and from standstill it does not launch while the track stays held, whatever the track's real range: a return 20 m ahead holds the car as one 6 m ahead would. This is intended and a test pins it. The track is something in the path that only the radar sees, and the old decode stopped for these tracks too (it braked for them at their range). In the 13-route replay the hold ran 0.35 s at standstill (under 0.1 m/s; 0.1 s engaged) in 4,015 s of standstill, and 10.65 s at 0.1-0.8 m/s (1.5 s engaged). The stop requests it adds where the second version (`7ec205c5a`) had none come to 0.8 s engaged: route 07 t=1241.9-1242.0 at 0.4-0.5 m/s, and route 08 t=72.5-73.1 from 0.8 m/s down to the stop. The old decode asked for the same stop on every one of those frames. Of those requests, only one frame (route 08) falls at standstill where the second version would have launched. If the car will not pull away with nothing visible ahead, press the gas.
  - **A held lead is a lead for the MPC only.** It is marked with `radarTrackId` = -2 - the track's id (`held_lead.py`; a track's id is 0 or more, and a vision-only lead's is -1, cereal's default). Everything else that reads `leadOne` skips it through `held_lead.real_lead()`. DEC: any lead would switch it to ACC and take e2e's stop away; the DEC-on what-if over the 13 routes is in the CHANGELOG. `longitudinalPlan.hasLead`: so a hold shows **no lead icon** on the dash (`HUD_LEAD`), and it sends no 0x500 `LEAD_VISIBLE` and no shadow_learn launch-with-lead sample. The other consumers that skip it: the e2e alerts (green light, lead departure); the onroad chevrons and path length in both renderers; the developer UI's lead distance; the comma 4 rail's planned stop and its standstill banner; and `brake_route_check.py`. To find holds in a route, look for `radarState.leadOne.radarTrackId <= -2`. A hold leaves `dRel` = 6 + 1.25 `vEgo`, `vLead` = `vEgo`, `vRel` = 0 and `aLeadK` = 0 as well; a tool that reads those as a car must check the mark first.
- **Long range (round 8, 2026-10-20, proposal 3): a stopped car the camera finds far ahead but reads as moving** (`long_range_track()`, `long_range_agrees()`). leadOne only, and only on a frame where radard has **fallen back to the camera's lead** (its match found no track, with `ready` and the filtered prob above 0.5). Then radard takes a stationary track (raw `|vLead|` under `LR_V_STATIONARY`, 1.5 m/s) within `LR_D_SHARE` (20%) of the camera's range and `LR_Y_TOL` (1 m) of its lateral position, with the car above `LR_V_EGO` (5 m/s), the camera's lead at `LR_D_MIN` (50 m) or more and radard's filtered prob for it at `LR_PROB` (0.9) or more, once the same track has met that for `LR_FRAMES` (6, 0.3 s) radard frames in a row. A frame where radard's own match took that track continues the run. Every other frame is decided as before; this bypasses upstream's `vel_sane` and the guard's tolerance for that case only. Upstream's sane-first matching (R1) is not needed for it and is not part of it. Since the round 8 review two more limits: a frame with a **moving** track (raw `vLead` above `LR_V_MOVING`, 3 m/s) within `LR_MOVING_D_SHARE` (30%) of the camera's range and `LR_MOVING_Y_TOL` (1.5 m) of its side takes nothing and breaks the run (`long_range_moving_near()`), and the rule runs only with openpilot longitudinal and stock ACC mode off (`long_range_applies()`, `RadarD.long_range`; the planner's re-entry ramp has the same gate), so in stock ACC mode radard is the clutter guard as before round 8.
  - **The veto, why** (round 8 review). "Fell back" means radard's one best match failed upstream's range or speed check, not that no radar car is there: at long range the camera's range runs short, so a real moving car at its true range fails the 25% range check and radard falls back with that car still on the radar. Route 0e1 t=887.18-887.28 (engaged, 16 m/s, just off the gas): the car it had been following, 101 m ahead at 18 m/s, against the camera's 79 m; a stationary return at 74 m, 0.7 m off the camera lead's side, met the rule on 3 frames in a row, and the car drove through its point 4.4 s later. At 6 frames it would have been a stopped car 74 m ahead at 16 m/s: full brake. Route 08 t=946.0 was the same at 15 m/s (a return at 80 m, the moving car 26% beyond the camera's 73 m). Replayed on 69 routes (the 14 clutter routes, `13d`, `141`, `142`, `143` and the review's 51): the veto ends those two runs - the only engaged runs of 3 frames or more on a return the car then drove through - and changes radard's lead on no frame of any route against the rule without it. Without the veto in stock ACC mode the rule could still change radarState and the planner's FCW, which selfdrived raises whenever openpilot is enabled.
  - **Why.** Route 143 t=168.3-170.1 (Mon 00:46:03, at night, 58 km/h): radar track 272 had a vehicle stopped in the lane at 94 -> 73 m from t=168.32, but the camera read it at 10-16 m/s, so `vel_sane` (and from 67 m the guard's own 10 m/s) set it aside, and the plan stayed at -0.3 to -0.9 m/s^2 until t=170.1; at full brake (255 counts: -1.65 to -1.92 m/s^2 there, a ~5% downhill; -2.45 on the level, measured in 7.13 - this said "about -1.8", corrected 2026-10-24) the car then needed the driver, who braked with 11 m left.
  - **Replayed** (RadarInterface, radard and the planner, open loop) on the 14 clutter routes of this section and on `13d`, `141`, `142` and `143`. On 143 it takes the stopped car from t=169.13 (00:46:04, track 272 at 82.6 m): the plan reaches -1.0 m/s^2 0.40 s earlier (t=169.73), -1.5 0.55 s earlier (170.13) and -1.8 0.10 s earlier (170.88), at most 0.54 m/s^2 firmer. Not the 0.7 s the analysis's trust-the-radar upper bound gave: this rule cannot start until the camera's prob reaches 0.9 (t=168.88). On the 14 clutter routes (about 210 engaged min): no new engaged phantom brake, no FCW, the plan's dips per minute identical on every route; radard's lead changed on one route only, c0 t=254.5-256.0, engaged, 5 frames on the real stopped queue the car then stopped 5 m behind, at most 0.10 m/s^2 firmer. On the new drives it fired engaged only on 143; disengaged on 141 t=1557.4 (6 frames, a car 86-88 m ahead pulling away from a stop) and 142 t=959.5/959.8 (3 frames, a car stopped 72-77 m ahead that then drove off); never on 13d. On the round 8 review's 51 further routes (1.42 M frames, 498 engaged minutes) it fired engaged once: d4 t=89.89-90.99, 22 frames, the plan at most -1.51 for -0.98, on a vehicle crawling at 0.4-0.7 m/s 80 m ahead that then pulled away; no FCW anywhere. **`13e`/`13f` have qlogs only, which carry no radar CAN, and cannot be replayed** (a coarse qlog check finds 10 s and 1.8 s of engaged time where it could even have applied): the owner's condition included them, so the rule is offered on condition that their rlogs, once on disk, replay clean.
  - **Where it lets go** (the 143 replay, round 8 review). The camera's range jitters, so the run breaks twice (t=170.13, the camera 21% short of the track; t=170.58, radard's own match on another return of the car for a frame) and radard falls back to the camera's moving lead for 3 frames each time (170.13-170.23 and 170.68-170.78). The plan holds -1.55 and -1.70 to -1.74 there (push9: -1.01 to -1.07 and -1.54 to -1.58). It then reaches -2.0 m/s^2 at t=171.28, 0.10 s after push9's 171.18, and is at most 0.06 m/s^2 softer than push9 from there: the planner's own speed integrates its earlier, firmer plan while the replayed car keeps push9's speed, an open-loop artifact (on the road the car would have been slower too). Holding the run through those breaks would widen the rule's reach and is not built.
  - **The fallback condition is what makes it narrow.** Without it the same test replaced a MOVING car the radar matched with a stationary return near it 8 times while engaged, at 42-110 km/h; 6 of them planned -1.8 to -3.5 m/s^2 where radard's own plan was -1.2 to 0.0 (08 t=945.5, c8 t=615.0 and t=1157.8, c9 t=1104.6, 10f t=2068.2, 142 t=998.5), with an FCW on c8. The 20% window is wide on purpose: at 55-80 m the camera's range runs 8-17% short of the radar's, and a 10% window with a 0.5 s run first fired at t=170.9 on 143, after the brake was nearly full.
  - **Drive check.** Look for engaged frames where `radarState.leadOne.radar` is true at 50 m or more with `vLead` near 0 while `modelV2.leadsV3[0]` reads several m/s: each should be a real stopped vehicle in the lane. And for `13e`/`13f`: once their rlogs are on disk, replay the rule on them before shipping it (RadarInterface on the logged CAN through radard with and without the rule, each feeding a planner, as above).

**Why.** At the true `REL_SPEED` scale (4.8) a stationary return reads as stopped, and upstream's `vel_sane` (10 m/s) and its unconfirmed low-speed override turned near-range clutter into stopped obstacles below about 36 km/h: false brakes to -3.5 m/s² and a false FCW in the plan replay (4.8). The thresholds come from radard rerun on 14 routes. They are listed with their data in the module docstring and the CHANGELOG entry. The model's `vStd` cannot set the tolerance: on the current model it reads up to 59874.

**Identifiers.** `elesys_radar_guard.enabled(CP)` (`CP.brand == 'honda'` and `CP.carFingerprint in HONDA_ELESYS`), `match_candidates()`, `match_agrees()`, `speed_tolerance()`, `slope()`, `hold_lead()`, the class `ElesysRadarGuard` (per-frame state: `update_tracks(tracks, radar_frame, v_ego)`, `update_camera()`, `world_speed()`, `range_agrees()`, `is_moving()`, `override_confirmed()`, `hold_track()`; per track a `_TrackState`), and the constants above. The module's `RADAR_TO_CAMERA`, `DT`, `STOP_DISTANCE` and `HOLD_T_FOLLOW` copy radard's, `DT_MDL` and `long_mpc`'s (its shortest `get_T_FOLLOW()`), and a test pins them equal. In `radard.py`: the import, `get_lead(..., clutter_guard=None)`, which defaults to upstream's path, the guarded lines inside it (the match's candidates, the override's `potential_tracks` / `low_speed_tracks` split, and the hold after the override), `RadarD.clutter_guard` (an `ElesysRadarGuard` on `HONDA_ELESYS`, else `None`), its `update_tracks(self.tracks, sm.recv_frame['radarTracks'], self.v_ego_hist[0])` after the tracks update and `update_camera(self.lead_prob_filters[0].x)` after the prob filters, and `clutter_guard=self.clutter_guard` on both `get_lead()` calls; since round 8 `get_lead(..., long_range=False)` with its block after the match (`clutter_guard.long_range_track(tracks, lead_msg, lead_prob, v_ego, track, fell_back=...)`), `RadarD.long_range` (`long_range_applies(CP, CP_SP)`, since the round 8 review) passed as `long_range=` on the leadOne call only, and in the guard `long_range_track()`, `long_range_agrees()`, `long_range_moving_near()`, `long_range_applies()`, `lr_track`/`lr_frames` and the `LR_*` constants. All are marked `FORK(HONDA_ACCORD_9G_AU)`. `update_tracks()` adds to its odometer only a finite `vEgo`: one NaN used to leave it at NaN for the rest of the drive, and then no track could latch as moving. The mark is in `openpilot/sunnypilot/selfdrive/controls/lib/held_lead.py` (`HELD_ID_BASE`, `held_id()`, `is_held()`, `held_track_id()`, `real_lead()`; no dependencies), and `real_lead()` is called, each time on a marked line, in `dec/dec.py` (the lead filter), `longitudinal_planner.py` (`hasLead`), `e2e_alerts_helper.py` (`has_lead`), `hud_model.py` (`StandstillBanner.compact()`, `RailState._stop()`), `ui/onroad/model_renderer.py` and `ui/mici/onroad/model_renderer.py` (`_update_leads()`, `_update_model()`), `chevron_metrics.py` (`draw_lead_status()`), `developer_ui/elements.py` (`LeadInfoElement.get_lead_status()`) and `brake_route_check.py` (both loaders).

**What it gives up.** The camera is the only discriminator for a stationary return that works in this data (track age, distance jumps and range-rate residual overlap between clutter and real objects). So below 4 m/s a stationary object that only the radar sees no longer stops the car: the guard only holds the plan for it, and the stop rests on the camera, e2e and the driver. These losses fall mostly at night: engaged real-object radar lead dropped at walking pace is 7.1 s per hour on night routes against 3.1 s/h in daylight (measured before the hold).

- **Route 06 t=1306.3-1309.8, engaged, at night, the worst case:** a stopped vehicle 20 -> 13 m ahead (tracks 2630, 2633, 2634) that the camera rated 0.03-0.45. Without the hold the plan held +0.4 to +1.0 m/s² for about 3 s where the old decode braked to -0.1/-0.4 and the true scale without the guard to -0.3/-0.5; the driver stopped about 6 m behind it. **Closed loop that is worse than the open-loop replay shows**: about 1 s of +1.0 takes the car from 3.4 to over 4 m/s, and above 4 m/s there is no low-speed override, so radard has no lead for a radar-only stationary object at all. The hold stops that escalation: the plan stays at -0.5 to 0.0 m/s² (engaged maximum -0.01 over t=1307.0-1309.35, -0.17 over t=1309.5-1310.3, where the first guard had +0.96 for 0.4 s after track 2652 appeared). It still does not stop the car for it. The stop-approach check counts this stop (ts=1312.4) as mostly disengaged because the driver took over.
- **Route 07 t=1240.7, 1.4 m/s:** a stationary return at 6 m that the camera saw only at prob 0.13-0.17 no longer brings the plan to -0.4/-0.6; it is held, and the plan follows e2e down from +0.4.
- **In daylight, a vehicle creeping 4-5 m ahead that the camera rates low** (route 10f t=2756.4-2757.2 and t=2851-2853): at walking pace this radar reads a vehicle creeping at about 1.5 m/s as 0.3-1.2 m/s, so it counts as stationary, and is only held, until it has read 1 m/s for three radar updates with its range agreeing.
- **A vehicle coming toward the car** (reversing toward it, rolling back on a hill, or oncoming traffic in the path on a curve) never counts as moving: only moving away does. With the camera unsure it is only held, where upstream brakes for it. Example, disengaged: route 14 t=145.5, track 59 at 11.3 m reading -1.55 m/s (old -3.46, true scale -3.50, guard -0.97). Latching such tracks on the same range test was tried: it admitted clutter (route c8 t=2800.2, a return at 2.6 m that read -1.2 m/s for three updates while its range jumped, then sat still: -3.5 m/s² for 0.85 s, disengaged, even with the latch limited to tracks closing at 2 m/s or less; without that limit, six more disengaged single-frame harsh brakes on tracks closing at 2.6-3.9 m/s), and gained nothing engaged (0.1 s of engaged radar lead dropped below 4 m/s, with no plan change).

Drive-test instruction: in night crawl and very close stop-and-go, cover the brake; the failure to watch for is the car pulling forward, or not slowing, with a vehicle close ahead. With the hold the car should no longer speed up toward something only the radar sees, but it does not slow for it either. During a hold the dash shows no lead. Below 0.8 m/s a hold stops the car and keeps it stopped; if it will not pull away with nothing ahead, press the gas and note the time.

**DEC.** The owner runs with Dynamic Experimental Control off (every frame of the 13 routes). With it on, DEC must not see a held lead, or it switches to ACC and drops e2e's stop; it skips held leads through `real_lead()`, and the CHANGELOG has the what-if. Any other new reader of `leadOne` needs the same check.

**Test.** `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_elesys_radar_guard.py`, 37 tests (26 before round 8, 34 as round 8 was first built), with logged frames in `fixtures/elesys_radar_guard_frames.json.gz` (each frame also says whether it carries a new radar update): 10f t=2737-2746 (near-range returns at walking pace: only held), c8 t=2318-2322.5 (the false-FCW frames), c0 t=249-266 (the stop behind a stopped queue, which must not change), 10f t=2753-2758 (a vehicle creeping 4 m ahead, held, then taken once seen moving), c9 t=286.5-289.4 (a car in the lane closer than the camera's lead, taken from its second radar update), 07 t=1126-1130.2 (a speed spike on a stationary return: never moving, only held) and 06 t=1303.5-1311 (the night give-up, held). The first two fail with the guard off; the 06, 07 and both 10f windows fail without the hold; the 07 window and the spike unit test fail without the range test (`WORLD_RATIO`); the c9 window fails with one update instead of `MOVING_NOW_UPDATES`. Since round 8 also `fixtures/elesys_long_range_frames.json.gz` (34.4 KB, the same format: 143's stopped car; c8 t=1157.8-1158.3 and c9 t=1104.6-1105.0, a moving car followed with a stationary return near it; since the round 8 review 0e1 t=884.8-888.8, a moving car radard set aside on range with clutter short of it). The long-range rule's tests: each limit just inside and just outside, the unbroken 0.3 s run on one track, radard's own match carrying the run, the fallback condition, leadTwo and other cars unchanged; 143 taken from t=169.10-169.15 and only on fallback frames; c8/c9 identical with and without the rule although every other condition holds there for 0.3 s or more; the c0 window now expects its 5 filled-in frames; since the review the veto (each of its limits just inside and outside, a moving frame breaking a run, and 0e1 unchanged even with the run cut to 2 or 3 frames, where without the veto 3 frames take the clutter) and the gate (off without openpilot longitudinal and in stock ACC mode, where 143's window is the guard without the rule). Of twelve deliberate breaks of the rule as first built, eleven fail at least one of them (each limit, the fallback condition in the guard or in radard - 4 tests each time -, the lead it applies to); the twelfth, dropping `fell_back` from the return line, changes nothing, because on those frames it returns the track radard already chose; the review's five breaks of the veto and the gate each fail too. Unit tests cover the constants against radard's and `long_mpc`'s, the hysteresis, counting in radar updates, the range-backed latch and its reset, the spike, the cut-in, a track coming toward the car, and the hold (its values, and that it applies only with no other lead). They also cover the hold through the real MPC, on every personality at 0-3.9 m/s: its output is within 0.05 of 0, and it asks to stop exactly below 0.8 m/s. Then the mark, with `hasLead` (through the real planner's `publish()`) and DEC skipping a held lead, and a non-finite `vEgo`. `test_hud_cluster.py` (the rail) and `test_brake_route_check.py` (a held lead is no stopped car) pin the two consumers there.

**Re-apply.** If upstream rewrites `get_lead()` or `RadarD.update()`: filter the candidates handed to the camera match through `match_candidates()`, add `clutter_guard.override_confirmed()` to the low-speed override's track filter, and after the override, when there is no lead, turn `clutter_guard.hold_track()` of the refused in-path tracks into the lead through `hold_lead()`; call `update_tracks()` once per frame after the tracks update, with radard's `recv_frame['radarTracks']` and the radar-aligned `vEgo` (`v_ego_hist[0]`), and `update_camera()` with leadOne's filtered prob before `get_lead()`; create the guard only when `elesys_radar_guard.enabled(CP)`. `hold_lead()` marks the held lead; if upstream adds a reader of `leadOne` (another mode switch, an alert, a UI element), give it `held_lead.real_lead()` unless it is the MPC. If upstream's MPC changes `STOP_DISTANCE` or its time gaps, the constants test fails: the hold's distance must follow. The fixture holds radar points decoded at 1/64 m/s, so it stays valid while the DBC does. The long-range rule (round 8): call `clutter_guard.long_range_track()` once per frame for leadOne only, after the camera match and before the lead is built, with the match's result and whether radard is about to fall back to the camera's lead; use its track only when it returns one.

---

## 6. Car state

### 6.1 Gear: `update_gear_elesys()` (`carstate.py`)

```python
def update_gear_elesys(self, raw: int, gear_raw: int):
  if raw in (1, 2, 4, 8):
    self.gear_shifter_last = self.parse_gear_shifter(self.shifter_values.get(raw, None))
    self.gear_zero_frames = 0
  elif raw == 0:
    self.gear_zero_frames += 1
    if gear_raw == 26 or self.gear_zero_frames >= self.SPORT_DWELL:
      self.gear_shifter_last = GearShifter.sport
  return self.gear_shifter_last
```

The state is set in `__init__`: `self.gear_shifter_last = GearShifter.unknown`, `self.gear_zero_frames = 0` and `self.SPORT_DWELL = 100`, which is 1.0 s at 100 Hz, about twice the longest transient ever observed. `update()` calls the function from an `elif self.CP.carFingerprint in HONDA_ELESYS and "GEAR" in cp.vl[self.gearbox_msg]:` placed between upstream's `manual` branch and the generic branch. The `GEAR` guard (2026-10) is for fingerprints, not the car: one with 0x191 and no 0x188 makes this car's gearbox message `GEARBOX_CVT`, which has no `GEAR`, and `update()` raised `KeyError('GEAR')` (fuzz seed 7944551990633456218, example 21 of `test_car_interfaces_69_HONDA_ACCORD_9G_AU`). Such a message now takes upstream's decode.

**`GEAR == 26` is measured now.** Across 86 routes (1406 min logged to 2026-09) S was selected on b1, dd and fc (148 s moving, 79 s engaged); the frames with `GEAR_SHIFTER = 0` read `GEAR = 26` on 16,164 frames (the S selections) and `GEAR = 0` on 2,975 (the shift transients). So the fast path is what fires; the dwell is the fallback. The comments in `carstate.py` and `_gearbox_legacy.dbc` that said 26 had never been seen were corrected.

Why: without the dwell every shift would emit a phantom `GearShifter.sport`. That trips `wrongGear`, suppresses always-on DM and freezes the dynamic tuner. The logic is a separate function so it can be tested without a CAN stream. Tests: `TestElesysGearDecode` in `test_elesys.py`. It builds a real `CarState` through `CarInterface.get_params()`/`get_params_sp()` (helper `_cs()`) and covers the detents, a transient holding the last gear, a sustained zero becoming Sport, the GEAR 26 fast path, leaving Sport re-arming the dwell, and the dwell staying clear of 520 ms. It calls the function directly, so the `elif` dispatch in `update()` is exercised only by `test_a_gearbox_message_without_gear_does_not_raise` (the fuzz case, through `CarInterface.update()`) and `test_the_real_gearbox_still_takes_the_elesys_decode`.

### 6.2 `LKAS_PROBLEM` from bus 0

```python
if self.CP.carFingerprint in HONDA_ELESYS:
  ret.carFaultedNonCritical = bool(cp_cam.vl["ACC_HUD"]["ACC_PROBLEM"] or cp.vl["LKAS_HUD"]["LKAS_PROBLEM"])
```

On this car 0x33D arrives on bus 0 (3.2). With the Stage 10 image it is the board's frame, which re-sources `LKAS_PROBLEM` from the EPS error state (LKAS-GATEWAY-PROTOCOL.md §10.1). This is also why openpilot must never send 0x33D here: it would read back its own frame, and `carFaultedNonCritical` would stay false (opendbc `031743c4`). Since the 2026-09 merge the branch sits inside upstream's rewritten condition, `if not (self.CP.flags & HondaFlags.BOSCH):`. No test covers this branch.

### 6.3 Stock AEB

```python
elif self.CP.carFingerprint in HONDA_ELESYS:
  brake_cmd = cp_cam.vl["BRAKE_COMMAND"]
  ret.stockAeb = bool(brake_cmd["CMBS_BRAKE"] or brake_cmd["AEB_REQ_3"] or
                      brake_cmd["AEB_REQ_2"] or brake_cmd["AEB_STATUS"] == 1)
  if ret.stockAeb and bool(cp_cam.vl["ACC_HUD"]["ACC_ON"] == 0):
    ret.carFaultedNonCritical = True
```

This branch sits between the Bosch branch and the generic Nidec `else`. The four bits were identified from a real CMBS event. All four read 0 across 351,809 stock `BRAKE_COMMAND` frames, so false triggers are not expected. The previous test was `(FCW >= 2 or AEB_STATUS != 0) and COMPUTER_BRAKE > 0`. Its first half fired on 110 frames, all of them warnings with `COMPUTER_BRAKE = 0`, so the test as a whole never fired (opendbc `2bc5c4db`; `CHANGELOG-elesys.md` section 3; `S:/OP/CMBS_AEB_bitmap.md`). The bits are ORed on purpose: over-triggering only stands openpilot down for a moment.

This is a different definition from the one the panda uses to forward the stock brake (8.2).

**Test coverage is weaker than the test name suggests.** `TestElesysStockAeb` pins the intended truth table and the DBC signal names, not the `carstate.py` code: its `_stock_aeb()` helper re-implements the expression (docstring: `mirrors the expression in carstate.py`), and only `test_all_four_bits_are_defined_in_the_dbc` touches real code, to check the DBC defines the four signals. A regression in the Elesys `stockAeb` branch of `carstate.py`, or in its `carFaultedNonCritical` override, would not fail any test.

### 6.4 Other car state

- `self.scm_buttons = cp.vl["SCM_BUTTONS"]` (Elesys only, inside the non-Bosch block). It is kept for the re-send (7.5).
- `self.econ_on`: `None` by default, meaning not observable. On Elesys it is `bool(cp.vl["ECON_STATUS"]["ECON_ON"])`. It is not a `CarState` field; `elesys_gas.econ_state()` (which `HondaDynamicTuner._econ_state()` calls) reads it straight off the `CarState` object, for the brake learner's gate and the drive-mode slot.
- `ret.fuelGauge = min(cp.vl["SCM_BUTTONS"]["FUEL_LEVEL"] / FUEL_LEVEL_FULL, 1.0)` with `FUEL_LEVEL_FULL = 105.0`, set in `CarStateExt.update()` (`carstate_ext.py`). It is a fraction of the gauge, not of the tank. Test: integration section 9.
- **The key-off `STEER_STATUS` 1 (2026-10-04, owner decision 2, `FORK(HONDA_ELESYS)`).** This EPS sends
  `STEER_STATUS` 1 (`DRIVER_STEERING`) in its first frames at key-on and its last at key-off. Upstream's rule takes 1 as
  both a temporary and a permanent fault, so every key-off raised "LKAS Fault: Restart the car", or TAKE CONTROL
  IMMEDIATELY while MADS was still on (115 at 1032.48, `steerUnavailable/immediateDisable`). On `HONDA_ELESYS`, and only
  at a standstill AND in P, 1 is no longer a fault; moving, or in D/R/N, it is the fault it always was, and 5/6/7 are
  faults in P too. Across 88 routes, 187 runs of 1, all at key-on or key-off at a standstill, none moving (route 011 has
  one 0.27 s run at a standstill in D, still a fault). Replayed through the real `CarInterface`, old tree against new,
  the only `CarState` difference on 115 and 10f is `steerFaultTemporary`/`steerFaultPermanent` on those parked frames
  (12 and 18), and with it `SP_HUD_STATUS.OP_STATE` (0x500) on the one or two key-off frames that carried FAULTED.
  Tests: `TestElesysKeyOffSteerStatus` in `test_elesys.py` (real frames through the real `CarInterface`).
- `CarStateExt.update()` now takes `(ret, ret_sp, can_parsers)`, and on Elesys `get_can_parsers()` registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION` and `GW_BUILD` with `float("nan")`. Both changes belong to areas B and A, but the call site in `carstate.py` must keep passing `ret_sp`.

### 6.5 `opendbc/car/structs.py`

Area C's own fields here are the two VSA flags (2026-10-03, below and 6.6). The file is listed because its relationship with `openpilot/cereal/custom.capnp` is a merge hazard. The additions:

- `CarControlSP.lateralControl: CarControlSP.LateralControl` with `integrator: float`, `saturated: bool`, `integratorFrozen: bool`, and since 2026-10-12 `steerSaturated: bool` (area B).
- `CarStateSP.driverTorqueStale: bool` (area B; read by area C's `desire_helper.py`, 10.2).
- `CarStateSP.linbusGateway: CarStateSP.LinbusGateway`: `engaged`, `dryRun`, `valid`, `actuating`, `present`, the `GW_STEER_GRANT` fields `grantValid` through `latchedUntilKeyOff` (area B), `fwValid` through `fwBuildValid` (area A), since 2026-10-06 `grantSeq` (area B: one step per `0x70B` frame that arrived, which MADS needs to end its override pause on a fresh frame), and since 2026-10-12 `blinkerCapped` (area B: `0x704 INH_BLINKER` from a fresh frame, which the `CarController` clips the reported torque on).

**The rule:** card publishes these dataclasses through `convert_to_capnp()`, which passes them into `custom.CarStateSP.new_message(**dict)` by keyword. So:

- **Field names must match** `openpilot/cereal/custom.capnp` exactly.
- **capnp ordinals must be unique and must never change:** `CarControlSP.lateralControl @5`, `CarStateSP.linbusGateway @1`, `CarStateSP.driverTorqueStale @2`, `CarStateSP.vsaFault @3`, `CarStateSP.vsaStoredFault @4`, `LinbusGateway @0`-`@28`, `LateralControl @0`-`@3`. Upstream's `CarControlSP` currently ends at `@4` and `CarStateSP` at `@0`, so there is no collision today. If upstream adds fields to either struct, the fork's fields keep their numbers and upstream's new ones must be renumbered on the fork side (or the fork's moved, which breaks old logs).
- **Dataclass field order does not matter.** It already differs: `structs.py` declares `driverTorqueStale` before `linbusGateway`, while the capnp has them the other way round. The in-code comments in `structs.py` say names and order must match; the order part is overstated.

On the way in, `openpilot/selfdrive/car/helpers.py` must rebuild every nested struct by hand (10.5).

Since 2026-10-03 area C has two fields of its own here: `CarStateSP.vsaFault` and `CarStateSP.vsaStoredFault` (`bool`, capnp `@3` and `@4`), 6.6. Their capnp/dataclass agreement - every top-level `CarStateSP` name on both sides, contiguous unique ordinals - is pinned by `TestCarStateSPAgreement` in sunnypilot's `test_vsa_fault_alert.py`.

### 6.6 The VSA's own fault (`vsa_fault.py`, provisional bits)

**Why.** On 2026-10-01 the car's VSA (the ABS / stability-control modulator, which carries out openpilot's brake requests) declared an internal fault twice, the second time 140 ms into openpilot's first brake request of the key cycle, which it dropped part-way. Honda i-HDS read **DTC 32-11, ABS solenoid valve malfunction**, with a freeze frame matching that second onset (`S:/OP/incident-2026-10-01/REPORT.md`, sections 1-3 and 8). openpilot only saw 0x1B0 `BRAKE_ERROR`, so the driver was told "Cruise Fault: Restart the Car"; the VSA keeps this fault across a key cycle and only re-checks once the car moves (route 113: cleared at 36.855 s, 35.3 km/h; route 111, parked, never). **That 35 km/h is a single observation**: the on-screen texts ("Clears Above 35 km/h", "Clears after driving above 35 km/h") use it because it is the only one; a second stored-fault drive that clears at another speed should change them. REPORT.md section 5, fix 2, asked for the fault to be named and for engagement to be refused while it is stored.

**The bits are PROVISIONAL.** No Honda DBC has them; they were named from timing on routes 110-113 and the clean 10f, then checked frame by frame against the raw rlogs and against all 166 logged routes (43.4 h). Notation `0xADDR bB.k` = byte B, bit k, k = 7 the MSB, i.e. DBC start bit 8B+k.

| bit | DBC signal | what the logs show | used for |
|---|---|---|---|
| 0x1A4 b2.2, b2.3 | `VSA_STATUS.VSA_FAULT_LIVE_B2_2`, `_B2_3` | byte 2 0x00 → 0x0C in the onset frame of both live episodes (110 t=1923.165, 112 t=128.849); never on any other route | live |
| 0x1EA b6.2 | `VEHICLE_DYNAMICS.VSA_FAULT_INERTIAL_INVALID` | the VSA zeroes and invalidates its own acceleration outputs in the onset frame; also set throughout a stored fault from the second frame after key-on | live, only with `BRAKE_ERROR` and after the start-up window |
| 0x1A4 b3.3, b6.0 | `VSA_FAULT_LAMP_B3_3`, `_B6_0` | fault lamps: 20 ms after a live onset, 2.0 s after the first 0x1A4 frame on a stored start; never in a start-up bulb check, and never set at all outside 110-113 (259 VSA sessions on 166 routes, every frame) | stored, from the first frame |
| 0x1A4 b3.6, b3.7 | `VSA_FAULT_LAMP_B3_6`, `_B3_7` | fault lamps too, but also in the start-up bulb check (b3.4-b3.7, at most 3.08 s after the first 0x1A4 frame over 143 clean starts) | stored, after the start-up window |
| 0x1A4 b4.0 | `VSA_FAULT_STORED_B4_0` | only on a stored start (byte 4 = 0x01 from the VSA's second frame, 20 ms; 0x03 after the bulb check), never on a live onset or in a bulb check; cleared with the fault (113 t=36.877) | stored, from the first frame |
| 0x1A4 b3.5, b4.1 | `VSA_FAULT_LAMP_B3_5`, `_B4_1` | on in the fault state, **and together for minutes on 45 earlier routes (comma_logs 00-87, June 2026, 4.6 h)** in which the VSA acknowledged openpilot's braking (route 69: `COMPUTER_BRAKING` on 12,172 frames of that state) | **not used** |
| 0x1AA b2.0, b2.1 | `VSA_1AA.VSA_FAULT_LIVE_B2_0`, `_B2_1` | byte 2 → 0x03 in the onset frame; b2.1 alone also flickers on three earlier routes | not used (0x1A4 + 0x1EA cover the onset frame) |
| 0x3D9 b1.0, b1.2 | `VSA_3D9.VSA_FAULT_LAMP_B1_0`, `_B1_2` | byte 1 0x80 normally, 0x81 in the bulb check, 0x85 with a fault (140 ms after a live onset; from 0.44 s on a stored start) | not used (a 5 Hz echo) |

The 0x1EA term exists for timing. The disengagement alert is created on the one frame `accFaulted` first appears, so the VSA has to be known on that frame. 0x1A4 arrives one panda batch after 0x1B0 on about 4% of cycles; 0x1A4 and 0x1EA both after it never (0 of 19,198 cycles, routes 10f-113). It counts only after the start-up window because at key-on `BRAKE_ERROR` is up on the VSA's first frame and b6.2 (on a stored start) from its second.

**`opendbc/sunnypilot/car/honda/vsa_fault.py` (new): `VsaFaultMonitor`.** Frame-counted (CarState gets no clock), 100 Hz:

- `vsaFault` = b2.2 or b2.3, or (b6.2 and `accFaulted`, once the window is over). No debounce: it only ever changes text, and it has to be there on the onset frame.
- `vsaStoredFault` = any of b3.3, b4.0, b6.0 (`FAULT_ONLY_LAMP_SIGNALS`, counted from the first frame) or b3.6, b3.7 (`BULB_LAMP_SIGNALS`, counted only after `STARTUP_WINDOW_FRAMES` = 500, 5.0 s, from the first 0x1A4 frame this CarState sees), set after `STORED_SET_FRAMES` = 50 (0.5 s) of it, cleared after `STORED_CLEAR_FRAMES` = 50 (0.5 s) without it. It is also True during a live fault (its lamps); selfdrived treats live first. Until the review of 2026-10-03 all five bits waited for the window, which put the flag 7.6 s after key-on on the car (card starts about 2.1 s in); now a stored fault is flagged 0.5 s after card's first frame.
- `VSA_SILENT_FRAMES` = 50: half a second without a new 0x1A4 frame reads both flags False and restarts the window (a quick key cycle under a running card restarts the VSA's bulb check too). Never received reads False.

**`carstate_ext.py`: `_update_vsa_fault()`**, the last call in the `HONDA_ELESYS` block of `CarStateExt.update()`, after upstream has set `accFaulted` (on this car 0x1B0 `BRAKE_ERROR_1|2`). It reads `VSA_STATUS` (already registered by upstream's `ESP_DISABLED` read) and `VEHICLE_DYNAMICS`, which **`carstate.py` registers liveness-exempt** (`float("nan")`) with the gateway frames: nothing else on any Honda reads 0x1EA, and a provisional signal must never be what costs openpilot `canValid`. For the same reason **its counter is not checked**: `get_can_parsers()` sets `ignore_counter` on its `MessageState` for `HONDA_ELESYS` (five broken counters would otherwise set `counters_valid` False); its Honda checksum still is, so a bad frame is still dropped. Across 7.6 million logged frames the one place its counter would have reached `MAX_BAD_COUNTER` (route `0000000a`, t=636) is where `VSA_STATUS` does too, with 0 checksum failures, so nothing changes on the logs (route 0a's `canValid` is identical). **It never raises**: anything unexpected sets both flags False and logs `VSA fault monitor raised` once. An exception in `CarState.update()` stops card, and with it 0x1FA, which the VSA answers with `BRAKE_ERROR` a second later.

**Measured behaviour.** Replaying `VsaFaultMonitor` over all 166 routes at 100 Hz from each route's first VSA frame flags 110-113 only. Replaying the full bus traffic of 113/0, 111/0, 10f/0, 110/32 and 112/2 through the real `CarInterface`: `vsaFault` on the same frame as `accFaulted` at both onsets (no frame with `accFaulted` and not `vsaFault` after start-up); `vsaStoredFault` 0.77 s (111) and 0.75 s (113) after the first logged CAN frame - 0.5 s after b4.0 first appears in the VSA's second frame - cleared on 113 at 37.38 s; nothing on 10f; and `canValid` False after warm-up never more often than in the car's own log (0 on 110/32, 112/2, 113/0 and 10f/0; 26 frames on 111/0 against 43 logged, where the comma went offroad at 18.8 s). Those times count from the first logged CAN frame, not key-on as card sees it: card starts 2.10 s (111) and 2.22 s (113) into the log and the parser keeps the VSA's latest frame, so on the car the flag rises 0.5 s after card's first frame, at about 2.6 s and 2.7 s, and engagement is not refused before that (cruise needs 19 mph anyway; selfdrived itself finished initialising only at 8.16 s and 8.23 s, so its first screen already named the fault - the selfdrived replay below). Re-run after the review fixes (2026-10-03) over every segment of 110-113, 0a, 10f, 14, de and comma route 69 (1.74 M updates, 0 exceptions): per update, `accFaulted`, `vsaFault` and `canValid` identical to the first version on all nine (0a included, where 0x1EA's counter is no longer checked), and `vsaStoredFault` identical except 497 more frames at the start of 111 and 113 (from 0.77 s / 0.75 s instead of 5.76 s / 5.74 s).

**What selfdrived does with it** (`openpilot/sunnypilot/selfdrive/selfdrived/vsa_fault_alert.py`, 10.6): a live fault (`vsaFault` with upstream's `accFaulted` raised) keeps upstream's disengagement exactly as it was and replaces its texts; a stored fault adds upstream's `carNotReady` (NO_ENTRY only) to `events`, which refuses openpilot **and MADS** until the VSA clears it. The MADS decision and its reasons are in the helper's docstring: the EPS refuses torque while the VSA holds the fault (STEER_STATUS 2 from 1.66 s after key-on, a hard fault 30 s later that clears only with the VSA), and the board's 51.5 km/h floor lies above the ~35 km/h clear, so lateral cannot work while it is stored.

**Merge notes.** `_honda_elesys_base.dbc` is fork-only; the new `VSA_STATUS`/`VEHICLE_DYNAMICS` signals and their comments are intended differences from `_honda_common.dbc` (4.2). `VSA_1AA` (0x1AA) and `VSA_3D9` (0x3D9) are defined inline in `honda_accord_au_2015_can.dbc` (4.1); no other Honda DBC defines either address. Every other Honda reads both flags False (`test_no_other_honda_reports_it`), and their parsers register nothing new.

Tests: `opendbc/sunnypilot/car/honda/test_vsa_fault.py` (33) with its fixture `fixtures/vsa_fault_frames.json.gz` (52 KB: every bus-0 frame of 0x1A4, 0x1EA and 0x1B0 in six windows of routes 110, 112, 111, 113, 10f and comma route 69, cut from the raw rlogs and grouped as the panda delivered them), and sunnypilot's `test_vsa_fault_alert.py` (47), section 12.

---

## 7. Longitudinal control (`carcontroller.py`, `hondacan.py`)

### 7.1 `compute_gb_honda_elesys()`

```python
def compute_gb_honda_elesys(accel, speed):
  creep = float(np.interp(speed, [0., 0.75, 1.75, 3.0, 5.0], [1.15, 0.8, 0.45, 0.3, 0.0]))
  creep *= float(np.clip(1. - max(float(accel), 0.) / 0.8, 0., 1.))
  net = float(accel) - creep
  gas = max(net, 0.) / 4.8
  brake = max(-net, 0.) / 2.6
  return float(np.clip(gas, 0.0, 1.0)), float(np.clip(brake, 0.0, 1.0))
```

`compute_gas_brake(accel, speed, CP)` dispatches to it through `elif CP.carFingerprint in HONDA_ELESYS:`, after upstream's `if CP.flags & HondaFlags.BOSCH:` (upstream `045cd8d3` made the function take `CP` instead of the fingerprint).

Why: the upstream Nidec map (`accel / 4.8`, with a creep of 0.15 below 2.3 m/s, fitted on an ILX) under-braked this car.

- Full `COMPUTER_BRAKE` gives about 2.6 m/s^2 on this car, not 4.8.
- The creep table is a grade-corrected coastdown over 76 routes.
- The creep subtraction fades out as positive demand rises to 0.8 m/s^2, so launches are not blunted.

Sources: `S:/OP/redlight_overshoot_findings.md`; opendbc `04a48a0a`, `470cd311`. With the dynamic tuner on, the learned brake gain (9.1) trims the 2.6 on the road instead of by editing the code.

Tests:

- `TestComputeGbElesys` checks a golden table: `GOLD_BRAKE_SCALE = 2.6`, `GOLD_GAS_SCALE = 4.8`, `GOLD_CREEP_BP/V`. The table is deliberately duplicated in the test, so any change fails until it is re-measured.
- `TestComputeGbNidec` checks that the upstream map is unchanged.
- `TestElesysCategory.test_dispatch` checks the dispatch.

### 7.2 The brake pump: rule C1b (`brake_pump_c1b_elesys()`, default) and v5 (`brake_pump_hysteresis_elesys()`)

Two rules, picked once per drive by `HondaFlagsSP.ELESYS_PUMP_C1B` (64). `_initialize_honda()` sets the flag from the `HondaElesysPumpC1b` setting ("Quiet pump at stops", default **on**, 11.1) at ignition, for `HONDA_ELESYS` with openpilot longitudinal only (never in stock ACC mode, section 15), so each route's `CarParamsSP.flags` records which rule it ran. `CarController.__init__` reads it into `self.elesys_pump_c1b`; the controller never reads the param. With the setting off the flag is clear and v5 runs exactly as before (byte-identical 0x1FA, below). Every other car still uses upstream's `brake_pump_hysteresis()`.

**C1b replaced rule C1 on 2026-10-06** (owner's decision after the c1weak study, below). C1 was batch 3's "Quieter brake pump" (`HondaElesysPumpV6`, flag 16, `brake_pump_c1_elesys()`), on the car on routes `120` and `121`. C1b has a **new key on purpose**: the owner had switched C1 off, and C1b is meant to start on after the update. `HondaElesysPumpV6` is no longer registered: a device keeps its file, unread, and a sunnylink restore skips it (`_apply_config()` restores only registered `BACKUP` keys). Flag 16 is **reserved** in `values_ext.py`: nothing sets it, the controller ignores it (an old `CarParamsSP` carrying it runs v5, tested), and the route tools still read it as C1 on the routes that ran it (`pump=v6`, 9.4). `test_honda_dynamic_settings.py` fails if the old key is named anywhere a setting is read, written or shown.

**What the pump is for** (study `pump2`, 2026-10, 66 current-era routes, 8.23 engaged hours, 107 stops; replaying v5 matches the logged pump bit on 99.58% of 428k braking frames). The VSA runs its motor only while `BRAKE_PUMP_REQUEST` is set: it shows as ripple on 0x1A4 `USER_BRAKE`, starting a median 120 ms after the request and stopping within 0.1 s of it clearing.

- A **rise** in the command is delivered only while the motor runs: unpumped rises of 6-25 counts gave -0.01 / -0.12 m/s² per 100 counts (aEgo / accelerometer, n=42), pumped ones -1.09 / -1.61 (n=65).
- A **steady** command holds without it: no measurable loss up to ~3 s at cb < 80 and ~2 s at 80-200. Longer holds are untested.
- A **release** needs nothing.
- A **standstill hold**, once built, held through 15-30 s pump-off gaps on 83 holds (52 s at cb 189 on a ~10% downhill, route `c8`); the 30 s top-up changed no logged signal. Building it is a rise, and needs the pump: below ~100 counts the car rolls.
- **cb ≥ 200 while moving:** unresolved. The 0.5-0.7 m/s² bleed behind `2905e73d` was inferred, not measured. The branch costs 2.6% of pump time, so it stays.

**C1b** (`brake_pump_c1b_elesys(apply_brake, v_ego, level, trig, last_pump_ts, ts) -> (pump_on, level, trig, last_pump_ts)`; the controller keeps `self.pump_level`, `self.pump_trig` and shares `self.last_pump_ts`). `level` is the command the last burst DELIVERED: it follows the command up while the pump runs, follows releases of more than 6 counts down, and is 0 after a release to 0 - so `level == 0` means "no burst yet in this application". In order:

1. `apply_brake <= 0`: off, `level = 0`.
2. Continuous: `v_ego >= 2.5 and apply_brake > 200` (`ELESYS_PUMP_C1B_FIRM`, as `2905e73d`), or `0.15 <= v_ego < 2.5 and apply_brake > 100` (`ELESYS_PUMP_C1B_CRAWL`, **v5's crawl run, restored**). The trigger moves to now and `level` to at least the command.
3. A burst running (`ts - last < ELESYS_PUMP_RUN`, 0.5 s): it extends while the command climbs by `EXT = max(2, deadband/2)` from the last trigger, so an apply ramp is one run.
4. Otherwise, the **first frame of an application** (`level == 0`): a burst, whatever the command and the speed.
5. Otherwise a burst fires when the command passes `level` by the deadband (v5's 12/6/3 counts over cb 0/60/200), **with no minimum gap**: the deadband alone gates re-triggers. At standstill (`v_ego < 0.15`) only while `level < 100` (`ELESYS_PUMP_C1B_HOLD_OK`), or for a rise of more than 15 counts over `level` (`ELESYS_PUMP_BIG_RISE`; the soft stop's rise to the hold, batch 3 fix round 1): a burst may build a hold or deliver a raised one, never refresh one.
6. Moving at `apply_brake >= 100` with no burst for 6 s (`ELESYS_PUMP_C1B_TOPUP_CB`, `_TOPUP_S`): one burst. This is the dry bound at firm braking and the **creep guard**. With the crawl run back, a hold above 100 counts that starts to roll is pumped from its first moving frame by the crawl run; the 6 s creep guard is what catches a roll at exactly 100 counts, or with an unknown speed.

So every application pumps from its first frame, a rise past the deadband over `level` is pumped the moment it arrives, with no gap to wait out, the final approach of a stop above 100 counts pumps throughout, no timer fires during a hold, and once a hold exists nothing pumps while stopped unless its command rises by more than 15 counts. Two limits on "the moment it arrives": rises are measured from `level`, which does not follow a release of 6 counts or less down and is raised to the run's peak while a burst runs, so after a small release it can sit up to 6 counts above the command and the next rise needs the deadband plus up to 6 counts more; and an application that starts within 0.5 s of the previous one's last trigger finishes that run (step 3 comes before step 4) instead of starting its own first-frame burst. And like C1, C1b has **no moving backstop below 100 counts**: v5's periodic re-prime while braking (12 s at light braking, about 9.75 s at cb 100) is gone, so a steady light command is never refreshed; step 6 is the only timer, at 100 counts or more. A non-finite `v_ego` counts as moving, outside both continuous runs, as in v5. One consequence of step 2: the continuous runs do not follow a release down, so a release inside the crawl run (the soft stop's cap of 125 after a firmer approach) leaves `level` at the higher value until the first frame outside the run, where the release-following applies - at the latest the first standstill frame, so the soft stop's rise to the hold is still measured from what was delivered (`TestBrakePumpC1b.test_the_soft_stops_rise_to_the_hold_is_delivered_once`).

**Why C1b (study `c1weak`, 2026-10-06; scratch `c1weak/A_synth.md`).** The owner felt braking was weaker on C1's two drives. The data does not show C1 braking weaker (low confidence: 2 drives, about 16 clean applications peaking at 12 counts or more, 1 engaged stop): a small softness in the first half-second (+0.07 m/s² at 0.5 s, mostly route `121`, gone by 2 s, model-dependent), no later decel onset, no more take-overs, and the hard-braking shortfall on `120`/`121` is openpilot's own 256-count ceiling. But it found three places where C1 pumped less than v5 that could plausibly cost braking, and C1b puts all three back:

- **The onset.** C1's first burst waited for the command to pass its deadband: cb 11, a median 0.12 s after the first frame and up to 2.1 s late (`121` @320.4); 435 of 1,671 applications never pumped. It is the only C1 element that lines up in time with the onset softness; whether it costs braking is unresolved either way (plausible if the 20-48-count dead zone is caliper fill the pump must supply, which the plant model cannot see).
- **The 1 s minimum gap.** The one element with a measurable cost in the model: without it, undelivered braking above the ~25-count dead zone falls 29% on the 66 routes (25% on `120`+`121`). The clean case is `120` @273.7-274.7: cb 62 -> 75 with aEgo flat at -0.55 until the burst, then about -0.85 with the command flat.
- **The crawl run.** The soft stop's 125-count cap (7.8) was sized so that v5's crawl run keeps pumping through the final approach. C1 dropped it (pump on 39% of that zone on `120`'s stop, 51% over the 66 routes); `120` @134.7 was the first logged stop with the soft stop and no crawl run, and stopped 3.0 m from the lead (v5: median 4.39 m, minimum 2.20). Not attributable to the pump on its own evidence (a 6-9% downhill, the learner cut to 0.872).

Kept from C1: no standstill top-up, the hold-build bursts (while what was delivered is under 100 counts, one per deadband-sized rise - with no minimum gap a light hold can take several, and an application that starts at the stop adds its first-frame burst), the soft stop's hold-rise burst, the creep guard / 6 s firm-braking burst and the continuous run above 200 counts.

**Replayed** (c1weak, open loop on the logged commands of the 66 v5-era routes - 8.23 engaged hours, 142.9 braking minutes, 107 stops - so only the differences between rules mean anything; "undelivered above the dead zone" is cb above max(delivered, 25) at 2.5 m/s or more under pump2's plant model, which is inferred):

| | v5 | C1 (retired) | **C1b** |
|---|---|---|---|
| pump starts | 2467 | 2022 | **2620 (+6% vs v5)** |
| pump seconds per engaged hour | 304 | 297 | **368 (+21%)** |
| undelivered above the dead zone, count·s | 3957 | 2670 | **1781 (-55% / -33%)** |
| time with >= 5 counts undelivered above the dead zone | 320 s | 220 s | **147 s** |
| share of moving braking with >= 10 raw counts undelivered | 6.62% | 5.97% | **2.48%** |
| applications never pumped / first pump > 0.3 s late | 208 / 13% | 435 / 24% | **0 / 0%** |
| crawl zone at cb > 100 with the pump on | 100% | 51% | **100%** |
| standstill bursts per stop / re-pumps > 5 s into a stop | 1.07 / 60 | 0.52 / 1 | **0.51 / 1** |
| longest moving pump-off at cb >= 100 | 5.30 s | 5.06 s | **4.00 s** |

On `120`+`121` alone: starts 56 (v5) / 43 (C1) / 66 (C1b); undelivered above the dead zone 94.3 / 49.4 / 35.9 count·s.

**As built.** `brake_pump_c1b_elesys()`, lifted verbatim from `carcontroller.py` and replayed on the same 3.42 million frames (the 66 routes and `120`/`121`), equals c1weak's replay rule (`c1weak/final/replay2.py`, `c1x(onset_first=True, min_gap=0.0, crawl=True)`) on every frame and reproduces every number in the table, as does the built v5 against c1weak's v5 (scratch `c1b/replay_c1b.py`, `report_c1b.py`). `TestBrakePumpC1b.test_matches_the_c1weak_reference` keeps a transcription of that replay rule and checks the controller's function against it on a 30,000-frame random trace.

**The cost, known and accepted.** C1b gives back C1's moving noise win - +21% pump time and +6% starts against v5 (+24% and +30% against C1): the onset burst is back on every application, light ones included (applications peaking under ~12 counts never pumped under C1), and so is the continuous whir on the final approach above 100 counts. It keeps C1's standstill win: 59 of v5's 60 standstill re-pumps more than 5 s into a stop are gone. The quieter step-down, if C1b passes its checks and the owner wants less noise, is C1-R (C1b without the first-frame burst: 2093 starts, +5% pump time); keep it only if its 0-0.5 s error stays within +0.03 of C1b's on the same roads.

**Replayed through the real `CarController`** (2026-10-06, scratch `c1b/integ/`, batch 3's harness): routes `10f`, `113`, `115`, `120` and `121` built the way card builds the car (the route's own params, `initialize_params()`, both `setup_interfaces()`), `CarInterface.update()` on every logged `can` and `CarInterface.apply()` once per logged `sendcan`, 529,870 control steps and 1,298,192 CAN frames, against `~/sp-merge` (the tree before C1b):

- **Setting off** (`HondaElesysPumpC1b` "0") against `~/sp-merge` with `HondaElesysPumpV6` "0" (v5): `CarParams` byte-identical, `CarParamsSP` identical, every CAN frame and every actuator output identical on every step of all five routes, every carlog line identical.
- **Setting on** ("1", and the registered default with the key absent - the same output): only `0x1FA` differs, and in it only `BRAKE_PUMP_REQUEST` and the checksum (2387 / 490 / 954 / 674 / 399 frames); `COMPUTER_BRAKE` and the actuators identical everywhere; `CarParamsSP` differs only in flag 64; the `hondashadow`/`hondadyn` lines say `pump=c1b` (and `pump=v5` with the setting off; `~/sp-merge` with C1 said `pump=v6`).

| route | v5: pump s / starts | C1 (`~/sp-merge`, flag 16) | C1b |
|---|---|---|---|
| `10f` | 132.1 / 153 | 127.3 / 117 | 161.9 / 173 |
| `113` | 28.1 / 33 | 29.1 / 28 | 36.2 / 37 |
| `115` | 49.9 / 54 | 49.0 / 46 | 64.6 / 59 |
| `120` | 33.6 / 36 | 34.2 / 26 | 45.5 / 42 |
| `121` | 30.6 / 20 | 30.0 / 16 | 35.6 / 24 |
| **total** | **274.3 / 296** | **269.6 / 233** | **343.8 / 335** |

On these five routes C1b runs the pump 25% longer than v5 with 13% more starts (the 66-route replay: +21% / +6%) - at the edge of acceptance check 5 below, so expect that check to be close.

**Where C1b delivers less than v5** (the review of this change, 2026-10-06: an open-loop replay of the committed `brake_pump_c1b_elesys` against the built v5 on the 66 v5-era routes and `120`/`121`, pump2's delivered-pressure model). Over 5,572 s of engaged moving braking C1b delivers 5 or more counts less than v5 for 40.9 s and 10 or more less for 12.4 s, in 172 short events (14 reaching 20 counts), 38.4 s of the 40.9 at cb < 100; the other way round, more than v5 for 436 s (5+) and 223 s (10+). The cause is the `level` caveat above, not a missing burst: after a small release v5's anchor stays at its trigger while C1b's `level` stays up to 6 counts higher, so the next rise pumps a little later (`d5` @816.2, 1.1 m/s, cb 68 -> 92: v5 fires at 76, C1b at 81, 0.28 s later; `cb` @793.9: 0.43 s later), and 31 of 1,809 applications start inside the previous run (`c8` @2332.37: one frame of request missing at cb 97). c1weak's reference rule has the same structure, so the table above already contains it. Two changes would remove most of it, each a new owner decision because each changes that table: let `level` follow every release while the pump is off (undelivered above the dead zone 1817 -> 1381 count·s, time 10+ counts below v5 12.4 -> 7.1 s, +1.6% starts, +1.7% pump time), or reset `trig` on a release / test `level == 0` before the running branch (the 31 applications; 1817 -> 1802 count·s, 15 fewer starts). Neither is in this change.

**Proving it on the car** (c1weak A_synth section 4). Same roads, alternating v5 ("Quiet pump at stops" off) and C1b, at least 6 drives per arm with at least 10 engaged stops to a stopped lead per arm. The per-event standard deviation is about 0.12 m/s²: resolving a 0.07 difference at 80% power needs about 46 clean applications per arm, 0.05 about 90 (`120` and `121` gave about 8 each). The pump is audible at onset again under C1b, so judge it on the logs, not the feel. `openpilot/sunnypilot/tools/brake_route_check.py <C1b routes> --baseline <v5 routes>` prints, besides pump2's metrics and abort criteria below, a **C1b ACCEPTANCE** block (9.4). Accept C1b when all of these hold:

1. the C1b replay matches the logged pump bit on at least 99.5% of braking frames;
2. clean applications peaking at 12 counts or more: c1weak's tracking error (kinematic aEgo minus the planner's `aTarget` 0.3 s earlier, not the actuator command) over 0-0.5 s and 0-1 s, adjusted as c1weak adjusted it (for grade, target size and the error already there before the onset), C1b's effect against v5 <= +0.03 m/s², with the upper bound of its 95% interval <= +0.05; decelerating 0.1 below the pre-onset coast baseline no more than 0.05 s later (adjusted the same way);
3. stops: the median distance to a stopped lead >= 3.5 m and none below 2.2 m, read at the stop as c1weak read it (v5: median 4.39 m, minimum 2.20; `120`'s stop 3.0 m); the raw error (aEgo minus `aTarget` 0.3 s earlier) in the last 3 s no worse than v5; the decel at wheel-zero median <= 0.6 and p90 <= 1.0 m/s² (7.8's targets);
4. driver brake take-overs per braking minute <= about 1.4 (v5's rate);
5. noise: pump seconds per engaged hour at most +25% over v5; standstill re-pumps more than 5 s into a stop at most 2 per 100 stops;
6. no `BRAKE_ERROR` and no VSA fault beyond the known 32-11 (keep checking the VSA bits; a DTC scan is manual).

**Turn the setting off** (back to v5) on any of pump2's abort criteria: a hold that moves with cb >= 100 and no planner launch; any band of the steady-command gain weaker by more than 0.10 m/s² per 100 counts (with at least 60 s of data); the 6-12 s bleed bin at cb >= 60 at least 0.10 m/s² weaker than the 0-1 s bin (20 or more stretches); the median stop distance shorter by more than 0.5 m, or any stop under 2.0 m; the brake-gain learner more than 0.04 above the off-arm mean; any `BRAKE_ERROR`, VSA/ABS lamp or new DTC; a moving pump-off at cb >= 100 longer than 6.1 s.

**C1, retired** (batch 3, 2026-10-05 to 2026-10-06; flag 16, `HondaElesysPumpV6` "Quieter brake pump", `brake_pump_c1_elesys()` in opendbc `41993458`). C1 was C1b without the first-frame burst (an application's first burst waited for the deadband: cb 11), with a 1 s minimum gap between bursts (bypassed by a rise of more than 15 and on an application's first burst) and without the crawl run. It came from pump2's pseudo-code with one deviation, the soft stop's hold-rise burst at standstill (batch 3 fix round 1, which C1b keeps). Through the real controller on `10f`/`113`/`115` (batch 3) it gave +1.6% pump time and -17.3% starts against v5. Its routes are `120` and `121`; `brake_route_check.py` replays them with its own transcription (`_c1_reference`), since no tree has the function any more.

**v5** (`brake_pump_hysteresis_elesys(apply_brake, v_ego, brake_anchor, last_pump_ts, ts) -> (pump_on, brake_anchor, last_pump_ts)`, anchor in `self.pump_brake_anchor`; runs with the setting off). Constants: `ELESYS_PUMP_RUN = 0.5`, `ELESYS_PUMP_REFRACTORY = 3.0`, `ELESYS_PUMP_DEADBAND_BP = [0., 60., 200.]`, `ELESYS_PUMP_DEADBAND_V = [12., 6., 3.]`, `ELESYS_PUMP_BIG_RISE = 15`, `ELESYS_PUMP_HOLD_REFRESH = 30.0`, `ELESYS_PUMP_MOVE_REFRESH_BP = [40., 200.]`, `ELESYS_PUMP_MOVE_REFRESH_V = [12.0, 6.0]`. C1b shares the run length, the deadband table, the big rise and both continuous runs.

1. **Continuous run** when `0.15 <= v_ego < 2.5 and apply_brake > 100`, or when `v_ego >= 2.5 and apply_brake > 200`. These branches restore commit `2905e73d` (`Fixed Pump Blind Spot on Saturated Braking`). A v4 tuning had silently reverted it: at cb >= 200, duty had fallen from 1.00 to 0.32 and the worst pump-off gap had grown from 0.16 s to 5.50 s.
2. **Re-prime** otherwise, when the command rises past a deadband that scales with the command (12 counts at light braking, 3 at firm braking). The rise has to come inside the current run or after the refractory period, or be a 15-count jump, which re-primes immediately. There is also a periodic backstop: 30 s at standstill, and 12 s down to 6 s while moving, scaled by the command. The anchor follows real releases, meaning drops of more than 6 counts.
3. **Run length:** `pump_on` stays on for `ELESYS_PUMP_RUN` after each prime, and only while `apply_brake > 0`.

Why v5 was shaped this way:

- Upstream's 20 s refresh let pressure bleed away, which was the stop-overshoot bug. Re-priming on every +1 count made the pump stutter.
- Replayed over 14 routes (405.9 min engaged, 149.2 min with brake commanded), upstream would run the pump for 48.55 min over 2302 starts. The v4 tuning, **before** the graded deadband, ran it for 38.0 min over 1619 starts. The graded deadband then took total run time from 38.0 to 33.3 min for 7.8% more starts, and light-braking run time from 15.75 to 12.10 min, with the worst moving pump-off gap unchanged. The code comment does not give a single figure for the shipped code with every later change applied.
- The block comment warns **not** to lengthen the moving backstop to quiet the pump. Going from 12 s to 20 s grows the worst dry stretch while braking and moving from 11.48 s to 18.42 s (route `00000020`). C1 dropped that backstop below cb 100, by measurement, and C1b keeps it dropped: it was an onset trigger, and C1b's first-frame burst does that job; at cb >= 100 the 6 s bound stays.

All of these numbers are open-loop replays of command traces recorded under older tunings, so only the differences between variants mean anything (code comment; `CHANGELOG-elesys.md` section 6; `FEATURES-elesys.md`, `The brake pump is quieter`).

Tests (`opendbc/car/honda/tests/test_elesys.py`): `TestBrakePumpHysteresis` (v5, 20 cases, including `test_upstream_default_unchanged`, `test_saturated_moving_braking_pumps_continuously`, `test_backstop_is_load_scaled_not_flat` and `test_standstill_hold_is_quiet`); `TestBrakePumpC1b` (22: the onset burst at the first frame of every application, one 0.5 s burst and then quiet, rises against the deadband and from the delivered level, rises with no gap, extension while climbing, a steady moving command below 100 never refreshed and the 6 s bound at 100 or more, cb > 200 continuous, the crawl run above 100 counts below 2.5 m/s and an approach through it into a firm hold and then silence, a stop reached firm never topped up, the soft stop's rise from its cap to 189 delivered once (at 125 and, since round 8, 105), a light stop's hold-build burst, a hold that rolls pumped at once, the creep guard, releases and the reset at 0 with the next application bursting at once, no pump without brake, NaN speed, and the rule against c1weak's replay rule on a 30,000-frame random trace); `TestElesysPumpRuleSelection` (3: the controller's 0x1FA follows v5 with flag 64 clear and C1b with it set, frame for frame, only the pump bit differing; flag 16, the retired C1, runs v5; flag 32 alone leaves v5); `TestElesysC1bWithTheSoftStop` (2, the real controller with the tuner on, so the soft stop runs: the rolling command capped at the soft stop's cap (105 since round 8, 125 before), the hold at 189, C1b's delivered level reaches the hold and never moves again with the 0x1FA pump bit off through the rest of the hold; and the pump bit read from the 0x1FA the controller sends is on for every rolling frame at 0.15 <= v < 2.5 m/s above 100 counts - the crawl run the cap was sized for - under C1b and under v5). The flag plumbing: `test_elesys_pump_brake_flags.py` (opendbc) and `test_honda_elesys_pump_brake.py` (sunnypilot).

### 7.3 The `BRAKE_COMMAND` units flag (`hondacan.create_brake_command()`)

Two keyword arguments go at the end of upstream's signature,
`create_brake_command(packer, CAN, apply_brake, pump_on, pcm_override, pcm_cancel_cmd, fcw, stock_brake, CP_SP, is_metric=True, elesys=False)`:

```python
imperial_unit = int(not is_metric) if elesys else 1
values = { ..., "SET_ME_1": imperial_unit, ... }
```

On this car the bit is the cluster's units flag (0 metric, 1 imperial), which is the same meaning as `ACC_HUD.IMPERIAL_UNIT`. The stock radar sends 0 on the metric AU car. On every other Nidec it stays the constant 1, and the signal keeps the name `SET_ME_1` in the shared DBC on purpose. `carcontroller.py` passes `is_metric=CS.is_metric, elesys=self.CP.carFingerprint in HONDA_ELESYS` by keyword. Tests: `TestBrakeCommandUnitsBit`.

Before the 2026-09 merge the function took `car_fingerprint` and `is_metric` positionally. Upstream `045cd8d3` removed the fingerprint parameter, so the merge moved the fork's two arguments to the end, with defaults that leave every other caller unchanged, and `hondacan.py` no longer imports `HONDA_ELESYS`.

### 7.4 No `LKAS_HUD` from openpilot

```python
if self.CP.carFingerprint not in HONDA_ELESYS:
  can_sends.extend(hondacan.create_lkas_hud(...))
else:
  ... create_sp_hud_status(...)   # area B
```

openpilot does not send 0x33D. The board builds that frame on bus 0 in the Stage 10 image. If openpilot took 0x33D over itself, three things would be lost:

- `RDM_HUD`, the road-departure popup, which openpilot cannot reproduce.
- `CAM_TEMP_HIGH` and `DTC`.
- The `LKAS_PROBLEM` check (6.2), which would end up reading openpilot's own frame.

The `else` branch belongs to area B.

### 7.5 Standing the stock ACC down: `create_scm_buttons_no_cruise()`

```python
def create_scm_buttons_no_cruise(packer, bus, scm_buttons):
  values = {s: scm_buttons[s] for s in scm_buttons if s not in ("CHECKSUM", "COUNTER")}
  values["MAIN_ON"] = 0
  values["CRUISE_BUTTONS"] = 0
  return packer.make_can_msg("SCM_BUTTONS", bus, values)
```

`CarController.update()` sends it under `if self.CP.carFingerprint in HONDA_ELESYS and self.CP.openpilotLongitudinalControl and self.frame % 4 == 0:` on `self.CAN.camera` (bus 2). That is 25 Hz. The stock frame runs at 50 Hz on bus 0 (route 10f); the panda's RX check declares 25 Hz for 0x1A6, which is a timeout bound and passes at 50 Hz. Not sent in stock ACC mode (15). It works together with the panda blocking the driver's 0x1A6 from bus 0 to bus 2 (8.1).

Why: while the stock ACC believes the main switch is off it stays in standby. It then stops issuing comfort-braking commands that openpilot would block, which is what tripped the brake system (the code says it stops the blocked ACC brake that trips TSA; background in `S:/OP/TSA_acc_disengage_approach.md` and the other `TSA_*.md`). CMBS does not depend on MAIN, so collision braking and FCW keep working. The PCM on bus 0 still sees the real buttons, so openpilot engages normally.

**Failure mode:** if card stops running, nothing re-sends 0x1A6, the radar stops hearing the buttons at all, and the car throws ACC and CMBS faults; and no 0x1FA reaches the VSA, which latches `BRAKE_ERROR` about 1.0 s later (b5-b8: 1.02-1.07 s after the last 0x1FA). This is what happened on routes b5-b8 (sunnypilot `d11d2c9a8`). An exception in `CarController.update()` does the same, which is why everything that runs in it must never raise (integration sections 17, 17b and 19).

### 7.6 Dynamic tuner hooks in `CarController`

All of these do nothing while `HondaDynamicTuningEnabled` is off. Integration section 1 pins bit-identical output with the toggle off. The gas law (9.2) is not a tuner hook: `HondaElesysGasLawV2` picks it whatever the toggle says.

| hook | code | effect with the tuner on |
|---|---|---|
| construct | `self.dynamic_tuner = HondaDynamicTuner(CP, CP_SP)` | reads the params once, when the controller is built, which is at ignition |
| pitch feedforward | `hill_accel = self.dynamic_tuner.update_state(CC, CS)`; `adjust_accel = accel + hill_accel` feeds `compute_gas_brake()` and, without an interceptor, `pcm_accel` | compensates for road grade |
| aero | `wind_brake * self.dynamic_tuner.wind_scale()` on the brake side, also passed to the interceptor; `self.dynamic_tuner.update_wind(CC, CS, float(wind_brake_ms2))` | **none since 2026-10**: `wind_scale()` returns 1.0 and `update_wind()` does nothing (9.1, retired channels). The calls stay so this upstream file needs no edit. With the tuner on that moved the brake-on point slightly: the persisted scale was about 0.80, so light braking at 25 m/s now gets about 5 counts less brake. Since 2026-10-12 the brake-side credit is ramped out at full brake on this car (7.11) |
| brake gain | `brake_gain = self.dynamic_tuner.brake_gain(CC, CS, float(apply_brake))` multiplies `apply_brake` before it is scaled to counts | learned brake scale |
| brake release limit | `if self.dynamic_tuner.enabled and CC.longActive and not CS.out.gasPressed and not CS.out.brakePressed: apply_brake = max(self.apply_brake_last - 32, apply_brake)` | the brake command can fall by at most 32 counts per 50 Hz frame, to match factory. This stops the lurch as the car lets go at a stop. It is bypassed on disengage and on driver override, and it runs before the pump logic so the Elesys anchor sees exactly what goes on the wire |
| soft final stop | `self.soft_stop = ElesysSoftStop() if (CP.carFingerprint in HONDA_ELESYS and self.dynamic_tuner.enabled) else None`; `apply_brake = self.soft_stop.update(CC, CS, apply_brake, self.dynamic_tuner)` after the brake gain, before the release limit | a brake ceiling while still rolling in the stopping state (7.8). Unlike the other hooks it is `HONDA_ELESYS`-only: built for this car with the toggle on, never for another Nidec car |
| pedal | `GasInterceptorCarController.update(..., self.dynamic_tuner)` | the tuner only observes the pedal (`observe_pedal`, per-mode data counts); see 9.2 |
| persist / log | `self.dynamic_tuner.persist(self.frame)` and `self.dynamic_tuner.log_state(self.frame)` at the end of `update()` | see 9.1 |

**Scope:** `HondaDynamicTuner._is_applicable()` accepts every Nidec car with openpilot longitudinal, not only this one. With the toggle on, another Nidec car would get the pitch term, the brake gain and the 32-count release limit. Its interceptor command is upstream's, bit for bit (the retired pedal gain used to reach it too), and it never gets the soft final stop, which is built only on `HONDA_ELESYS` (7.8).

### 7.7 Comments that record decisions

Three `FORK` comments in `carcontroller.py` record decisions rather than code:

- **The pedal/PCM crossfade was removed** (opendbc `aa73e60a`). Across 17 engaged routes, 289,625 `ACC_HUD` frames all had `PCM_GAS = 0` and `PCM_SPEED = 0`; the PCM was never shown to respond, and the interceptor is the easier actuator to control. The interceptor owns the gas at every speed. Integration section 6 pins this by decoding `PCM_GAS` from the frames the controller emits.
- **MVL's 3x faster brake rise was deliberately not ported.** Combined with the learned brake gain, it would reach full brake from a gentle request in about 0.1 s.
- **`CRUISE_OVERRIDE` (0x1FA bit 20, byte 2 bit 4) stays the constant 1** (`FORK(HONDA_ACCORD_9G_AU)` above `pcm_override = True`, 2026-10). Upstream has sent 1 on every Nidec since openpilot v0.2 (`53bccc437`, moved to the constant in `cb1cd01bd`); panda never reads the bit. The owner asked to remove it because "MVL doesn't use it": MVL does (`pcm_override = CC.longActive or CS.out.stockAeb`, MVL `e3595346`, also on `origin/accordau`), and that ran on this car in June-July 2026. Measured:
  - every openpilot 0x1FA on 81 current routes carries 1; the stock Elesys radar sets it on 6,130 of its 6,465 braking frames (94.8%), and clears it only in 9 release ramps of 0.2-1.6 s after the driver overrides;
  - VSA `COMPUTER_BRAKING` follows the command with the bit at 0 or 1 (stock frames at CB >= 50: 0.997 against 0.998);
  - 184 openpilot frames with brake and the bit at 0 reached the VSA, over 12 routes, with no `BRAKE_ERROR` within 5 s; but that is CB 4 for at most 1.74 s (route 86) and higher commands only in release ramps of 4 frames or fewer;
  - all 8 `BRAKE_ERROR` onsets since June (comma 76, 77, 7b, 84; sunny b5-b8) began 1.02-1.07 s after the last 0x1FA on bus 0: in 76, 77, 7b and 84 panda dropped openpilot's forced minimum brake (nonzero while longitudinal was not allowed), in b5-b8 nothing was sent. The VSA timeout is about 0.96-1.02 s (route 85: 0.96 s without a fault).

  So: **no measured effect on VSA braking or `BRAKE_ERROR` in the short post-disengage tails we have**, and every `BRAKE_ERROR` since June was a ~1 s 0x1FA gap. It has never been tested at 0 during sustained braking; the comment says so, so nobody reads it as permission to send 0. There is no other override bit left to remove: the owner's `ACC_OVERRIDE_STOP` (bit 21, inside `SET_ME_X00`) was added and later removed (`81b64063`, then `d9498a1a` "Removed AOC Bit"), and nothing of it remains. The owner's June notes (`S:/OP/OP_bit_construction_spec.md`, `S:/OP/ACC_HUD_BRAKE_COMMAND_stock_bitmap.md`) call this bit the one engaged-time divergence from stock (stock sets it in ~37% of engaged frames); that recommendation is closed because no consumer was found and no fault traces to it. Integration section 18 pins the bit on every `BRAKE_COMMAND`, brake 0 on the first one after `longActive` drops, and at most one nonzero `BRAKE_COMMAND` after a rising edge of `brakePressed` or `gasPressed` (panda drops every one of them, so a second would start a hole).

### 7.8 The soft final stop (`elesys_stop.py`), and a brake block that never raises

**Where.** The fork-owned `opendbc/sunnypilot/car/honda/elesys_stop.py`. `CarController.__init__` builds `self.soft_stop = ElesysSoftStop()` only for `HONDA_ELESYS` with `HondaDynamicTuningEnabled` on (the toggle of the stopping debounce and the 32-count release limit), and the Nidec brake block calls `apply_brake = self.soft_stop.update(CC, CS, apply_brake, self.dynamic_tuner)` after the learned brake gain and before the release limit. Every other car, and this car with the toggle off, never builds it: integration section 19 checks that the `BRAKE_COMMAND` bytes are identical for this car with the toggle off and for `ACURA_ILX` with it on or off.

**Why** (braking audit and skeptic review, 2026-10; 45 stops openpilot completed with no driver input, routes `3e`..`103`). The harsh final jerk is the stopping ramp reaching the full standstill hold before the car has stopped. Stopping is entered a median 0.90 s before the stop at 0.62 m/s with a near-zero command; longcontrol then ramps toward `stopAccel` -0.8 at 0.8 m/s³ and the creep table (7.1) adds +35 counts as the speed falls, so the brake is at the hold level when the wheels stop (median 185 counts against the hold's 189) while the planner asks for -0.16. Deceleration at the stop: median 0.92 m/s² (lead stops 1.02, no lead 0.48), jerk on settling a median 7.4 m/s³.

**What.** A ceiling on the brake while the car is still rolling in the stopping state. It only ever lowers the command.

| | |
|---|---|
| gate | `CC.longActive and longControlState == stopping and not gasPressed and not brakePressed`; anything else returns the command untouched and resets the state |
| entry | ceiling = `max(cap, apply_brake at entry)`, so never below what entry was commanding. Entered with the wheels already at zero: no ceiling at all. Entered faster than `SOFT_STOP_MAX_ENTRY_V` 1.2 m/s (`vEgo` on the entry frame; unknown counts as faster): no ceiling either, and one `hondastop skip=speed` line |
| cap | `SOFT_STOP_ROLL_CB` 105 (125 until round 8) + `max(0, -sin(pitch)) * 9.81 / 2.6 * 256` counts, about 17 per degree of downhill. `pitch` is the tuner's filtered pitch (`HondaDynamicTuner.filtered_pitch()`, `PITCH_RC` 0.5 s); no grade term when there is none (no pose yet, stale for `PITCH_STALE_FRAMES`, NaN). While rolling the ceiling is `max(ceiling, cap)`: a steeper downhill raises it, nothing lowers it |
| rise | `SOFT_STOP_RISE` 250 counts/s (5 per 50 Hz frame: 105 to 189 in ~0.34 s; 125 to 189 in ~0.26 s before round 8), monotone, from the first of: **settle**, `SOFT_STOP_SETTLE` 0.55 s after the wheels first read zero (`CarState.standstill`, or `vEgoRaw` at zero; below 1 m/s `vEgoRaw` is `XMISSION_SPEED`, which reads 0 below ~0.3 m/s); **moving**, the wheels turning again after reading zero; **weak**, `aEgo > -0.25` for 0.4 s, counted only once the command has sat at the ceiling for `SOFT_STOP_AT_CEILING` 0.3 s; **max_roll**, `SOFT_STOP_MAX_ROLL` 1.9 s since entry, absolute: rolling or settling, whatever the wheels do |
| done | at 255 counts the ceiling is out of the way: the hold is today's 189, byte for byte (pump bit and checksum included, from 1.5 s after wheel-zero; integration 19) |
| bound | the rise starts 1.9 s after entry at the latest and the ceiling is gone 0.60 s later (105 to 255 at 250 counts/s; 0.52 s from 125): nothing of it is left 2.50 s after the stopping state began (2.42 s before round 8) |

The skeptic review made four changes to the audit's version. The settle timer starts at wheel-zero, not at `vEgo < 0.15` (which fires 0.05 s before the wheels read zero, while the camera still sees 0.17 m/s), and lasts 0.55 s, not 0.25. Weak deceleration only counts at the ceiling: the plain 0.4 s escape fired in 6 of the 45 stops, always 0.38 s after entry, in stops still building pressure from 9-53 counts, three of them among the harshest (`ad` 394.3 at 2.74 m/s², `b0` 391.0, `d3` 829.4). The cap is grade-aware: below 2 m/s and in stopping the pitch feedforward is 0, so a flat 125 on a 2.5° downhill leaves ~0.1 m/s² of net deceleration. And moving again rises at once, with `MAX_ROLL` at 1.9 s instead of 2.5.

**After the review (2026-10), two bounds and one decision.**

- `MAX_ROLL` counts from entry. It was first written against the rolling time only, which let the ceiling bind until 1.9 + 0.55 s after entry and last to about 2.97 s; the decision was an absolute bound. On the replayed stops it changes nothing: every rise was `settle`, at most 1.82 s after entry.
- The entry-speed bound. The weak-decel escape cannot fire while the car decelerates harder than 0.25 m/s², so a stop entered fast with the brake still building would sit at the cap until `MAX_ROLL`. Measured entries reach 1.08 m/s, in the 45 audited stops and in the 32 of the review replay (median 0.58); above 1.2 m/s the stop is today's, bit for bit (integration 19 enters at 1.5 m/s).
- Re-replayed through the real `CarInterface` on the review's 14 routes (300 min, 144 min engaged; scratch `batch1/long-fix/`): every CAN frame of all five set-ups (tuner on/off, law v1/v2, ceiling on/off) is identical to the code before these two bounds, 1.8 million control steps, with the same 33 `hondastop` lines (26 `settle`, 7 `end=left`) and no `skip=speed`.
- Leaving the stopping state for the PID drops the ceiling in one frame, by decision: the gate is the stopping state, and carrying a ceiling into the PID would cap braking the planner asked for. In the review replay the largest upward step on that frame, across the stopping/PID flickers on `9e` and `a0`, was 6 counts.

**Replayed on the 45 stops** (open loop: the recorded signals through the real `soft_stop_ceiling()`; scratch `batch1/long/b2_replay/`):

- 44 entered the stopping state rolling. `9f` 303.9 reached standstill in the PID state (cb 228) and gets no ceiling. In `ad` 394.3 the driver pressed the gas 0.33 s after wheel-zero, which ends the ceiling (`end=left`).
- `standstill` and `vEgoRaw == 0` agreed on every stopping frame, and `vEgoRaw` never read nonzero again within a stop: the "moving" rule had no false trigger.
- The wheels read zero a median 0.84 s after entry (at most 1.26 s, inside `MAX_ROLL`), 0.06 s before `vEgo` < 0.02.
- Command at wheel-zero: median 170 as recorded, 137 with the ceiling (grade term included).
- 29 of the 44 are on a downhill (the grade term's median over all 45 stops is +11.5 counts; the largest cap is 164). It is grade, not brake dive: the filtered pitch at entry matches the pitch in the settled hold (r = 0.97, median difference -0.02°), and the route-mean pitch above 5 m/s is -0.18°.
- With the recorded `aEgo` (today's stronger braking) every rise was `settle`. Whether `weak` fires under the softer brake cannot be known from these logs.

**Expected, inferred and unvalidated:** deceleration at the stop ~0.5-0.6 m/s² instead of 0.92, and about +0.1-0.2 m and +0.3-0.4 s per stop. The audit's simulator failed its own validation (correlation 0.22, -0.05 and -0.05 for time, distance and deceleration), and 125 counts at the stop is outside the data (most stops sat at 170-188), so this is a hypothesis to measure (measured on push9, and the cap lowered to 105, in round 8: below). Measure it at the camera/IMU-confirmed stop: the count at wheel-zero is 125 by construction and proves nothing, and the old IMU window, 50-150 ms before wheel-zero, lands before the ceiling rises. Targets: deceleration at the stop median <= 0.6 and p90 <= 1.0 m/s²; the peak jerk in the 0-0.8 s after wheel-zero; distance to a stopped lead median >= 3.5 m (4.1 today); holds with 0 creep frames and no rollback. The first drive also carries the 2026-09 merge's stop latch (`should_stop()` on the measured `vEgo`, 10.1), and no post-merge stop exists in the logs yet.

**The cap was 125 until round 8 (2026-10-20, proposal 5: 125 -> 105).** Measured on the 10 rlog stops of drives `13d`-`142` (push9): the ceiling did what it was built for (9 of 10 `hondastop` lines `rise=settle`, the cap at wheel-zero), but the car still met the stop at a median -0.74 m/s^2 (IMU -0.86) with a median 4.6 m/s^3 snap on the IMU, against the 0.6 target above. While rolling at the cap the IMU read a median 0.82 m/s^2, so the cap itself set that decel. At about 0.012 m/s^2 per count (the batch-3 plant's brake gain at a crawl) 105 takes about 0.24 off it: estimated 0.58 at the cap, the roll from entry to wheel-zero 0.80 -> 0.99 s (median), 0.1-0.2 m longer (inferred). The closed-loop replay of the same stops through the real controller and the batch-3 plant agrees in direction: the true decel at the stop is 0.08-0.22 m/s^2 lower on the five stops where only the cap changes (median 0.70 -> 0.58), and three are unchanged. **But the snap barely moves** (corrected in the round 8 review): the jerk after wheel-zero is a median 4.78 -> 4.64 m/s^3, about 3% smaller, not the ~25% the analysis expected. **The cost:** a stop that already rolled long at 125 (13d t=380.8: 1.74 s, `max_roll`, only 0.35 m/s^2 at the cap) has too little decel at 105; in the sim its escape changes from `settle` to `weak` and the old ramp it is handed to makes it marginally harsher (jerk 4.49 -> 4.54 m/s^3) - one stop in ten. 141 t=234.7, listed with it at first, escapes by `max_roll` at either cap and comes out softer (4.56 -> 4.35, decel 0.49 -> 0.31). Those escapes are the designed fallback, and the `hondastop` line says which fired. Lengthening `SOFT_STOP_MAX_ROLL` (1.9 s) would be the fix and is not built: the owner's call. Full brake is unchanged (the cap only acts in the stopping state: on 143's window 0 brake frames differ). Open loop through the real `CarInterface` on `13d`, `141`, `142` and `143` against opendbc `dc7f1e7d` (`docs/CHANGELOG_SERIAL_STEERING.md`, round 8): every frame identical except `0x1FA` inside the stopping state's stops.

**Log.** One `carlog` line per stop, which comes back in the route as a `logMessage`: `hondastop rise=<settle|moving|weak|max_roll> t= roll= still= entry= cap= ceil=` when the ceiling starts to rise, `hondastop end=left ...` when the stop ended first (the stopping state left, a pedal, a disengage), or `hondastop skip=speed v= entry=` when it was entered too fast for a ceiling. A stopping/PID flicker adds a line per re-entry. Each `weak` or `max_roll` should show a downhill or a weak brake.

**What it leaves alone.** The pump: the cap is above 100 (105 since round 8, 125 before), so the crawl continuous run (0.15 <= v < 2.5 m/s at cb > 100) keeps the pump on through the final approach - v5's (`brake_pump_hysteresis_elesys()`, "Quiet pump at stops" off) and, since 2026-10-06, pump rule C1b's (the default, 7.2) - which is why the cap must stay above 100: for any command c, min(c, cap) > 100 exactly when c > 100, so the crawl run pumps the same frames at 105 as at 125 (`test_the_cap_is_105_and_stays_inside_the_pumps_crawl_run`), and the rise to the 189 hold is still more than `ELESYS_PUMP_BIG_RISE` over the cap. **The crawl run is back**: the retired rule C1 (batch 3 to 2026-10-06) had no such branch, the approach got ordinary bursts, and on route `120`'s stop the pump was on 39% of that zone - the first logged stop with the soft stop and no crawl run (c1weak, 7.2). The rise from the cap to 189 at standstill (105 -> 189 since round 8) is delivered under both rules: under C1b either the crawl run's last burst, still running when the wheels stop, extends through the climb, or one standstill burst delivers it - the delivery of a rise of more than 15 counts over the delivered level, or a hold-build burst when the stop was reached below 100 delivered counts (batch 3 fix round 1, 7.2); `TestElesysC1bWithTheSoftStop` drives the real controller through it with the tuner on. Under C1's pseudo-code alone that rise was never pumped and the held pressure stayed at the cap - 125 plus the grade term, 125 on any grade with a stale pitch - for the whole stop with no top-up, a hold no logged stop has tested; the route check shows the delivered pressure of every hold (9.4). The cap's value (125, then 105) itself does not depend on the pump and is unchanged. The learners: the brake learner needs the PID state and `vEgo` > 1 m/s, the per-mode counter needs the PID state, and both read the command before the ceiling (integration 19 compares the tuner's state with that of a controller without the ceiling). Stock AEB: panda forwards it whenever its brake is >= openpilot's, so a lower command can only start the forwarding earlier. Disengage: brake 0 on the first `BRAKE_COMMAND` after `longActive` drops, also while the ceiling binds. Pedals: at most one nonzero `BRAKE_COMMAND` after a `brakePressed` or `gasPressed` edge (integration 18).

**Not in this change, by decision.** The audit's P2, no coasting in the last meter (`longcontrol.py`), waits until this has been measured on its own; if it comes, it must bleed the positive integrator toward `g·sin(pitch)`, not 0, and only below 0.8 m/s. P3, a pump floor of 30 counts, was dropped: 15-29 counts do decelerate the car against a coasting baseline (0.05-0.18 m/s²), 12.9% of the brake learner's frames are below 30, and floor crossings would restart the pump through its 3 s quiet period. The owner's pump question is answered by measurement: the pump runs 28% of the time a brake command exists, against the stock ACC's 53%, with a median run of 0.66 s.

**Never raises.** `ElesysSoftStop.update()` wraps everything; on any exception it resets and returns the command untouched, and it refuses an answer above the command or below 0. A missing or non-finite `vEgoRaw` reads as the wheels at zero (today's behavior: no ceiling at entry, the settle timer after it); an unknown `aEgo` counts as not slowing (toward today's ramp, never a longer cap).

**The NaN-`vEgo` guard** (same change, `FORK(HONDA_ACCORD_9G_AU)` in the brake block). A non-finite `vEgo` made `wind_brake` NaN, and `int(np.clip(apply_brake * brake_gain * ...))` raised "cannot convert float NaN to integer": no 0x1FA, and the VSA's `BRAKE_ERROR` a second later. The block now falls back to the brake without the aero credit. Finite values are untouched, so nothing changes on any car where the old code did not raise. Upstream's `actuator_hysteresis()` already holds its last steady value through a NaN request, so mid-braking the command holds, a little firmer; with the tuner on the learned gain also fades to 1.0, because the tuner reads an unknown speed as 0 (integration 17b).

Tests: `test_elesys_stop.py` (29: every timer, the grade term, the gate and the reset, the bound from entry, the entry-speed bound on `vEgo`, random-input invariants, the wrapper never raising, one log line per stop; since round 8 the cap at 105 and inside the pumps' crawl run for every command) and integration sections 17b, 18 and 19 (19 also enters at 1.5 m/s, bit-identical to the controller without the ceiling, and at 1.15 m/s, capped).

---

### 7.9 Brake lamps during openpilot braking: no CAN path (2026-10-05)

The problem: when openpilot brakes, the stop lamps stay dark. Stock ACC braking lights them.

- **openpilot already sends the lamp bit, and it does nothing.** `BRAKE_LIGHTS` (`0x1FA` bit 39) is set on every
  openpilot braking frame (100% on routes 10f and 113). The VSA acts on those frames (`0x1A4` bit 23,
  `COMPUTER_BRAKING`, on about 99% of them), yet the lamps stay dark, so this VSA ignores bit 39. The radar never sets
  it: 0 of 6465 braking frames on the six stock-ACC drives (0e, 44, 48, 61, 81, 82).
- **Stock lights them outside CAN.** Per the 2026-09-29 investigation (archived section 7.8, below), the lamp relay line
  runs to the ACC unit. That unit is on bus 2 and never receives openpilot's `0x1FA` (the stand-down, 7.5). Log mining
  over 161 routes found no `0x1FA` bit that stock sets on most braking frames and openpilot does not; bit 28 rides with
  `AEB_REQ_1` and was never offered. No CAN signal on this car reports lamp state, so the logs alone cannot prove the
  wiring.
- **The VSA's own hill hold does not light them either** (owner, 2026-10-05). The hold shows as `0x1A4` bit 49 with
  `0x1B0` bit 41, about 1.1 s after the pedal is released on grades of 8% or more. That shows the VSA does not light
  lamps for its own holds. It does not, on its own, rule out a lamp command.
- **Decision (owner, 2026-10-05):** no CAN bit openpilot can send will light them. The test branch `brake-lamp-test`
  (a bit sweep at a standstill, param `HondaBrakeLampTest`, never driven on any route on disk) was deleted from the
  sunnypilot, openpilot and opendbc repos. It is kept as tag `archive/brake-lamp-test`: sunnypilot and openpilot
  `80a752eb`, opendbc `6002ac59`. That commit's section 7.8 has the full candidate list and the reasons for each
  exclusion; `opendbc/sunnypilot/car/honda/brake_lamp_test.py` has the code.
- **If lamps are still wanted, the route is hardware:** drive the lamp relay line from a spare EPS-LKAS board pin,
  keyed on `0x1A4` bit 23 (VSA `COMPUTER_BRAKING`, bus 0) or on openpilot's `COMPUTER_BRAKE > 0`. The wiring has not
  been checked.

### 7.10 Brake law v2: removed (round 5, 2026-10-09)

Batch 3 (2026-10-05, owner decision 2) added a measured brake law behind "Measured brake law (testing)"
(`HondaElesysBrakeLawV2`, CarParamsSP flag 32, default off, needing gas law v2): the measured coast-down replaced the
aero credit, a soft dead zone `c0(v)` and slope `k(v)` per speed band mapped the brake to counts, nothing was sent in
the coast band, the gas law's pedal-zero point moved to `coast(v) + DELTA(v)`, and the learned brake gain was held at
1.0. It failed two of the owner's acceptance criteria, both toward less brake (held out, the 20-25 m/s band was off by
-0.076 m/s^2 against +-0.05; and B4 in the simulator), was never turned on in the car, and round 5 deleted it (owner
decision D0.1). git keeps it: opendbc `8f844a79` (the law, `elesys_brake.py`, 262 lines, and `test_elesys_brake.py`,
29 tests) and `41993458` (fix round 1, its acceptance record); sunnypilot `28d880d06` and `1bed54229` (the settings);
the design, fit and proofs are this section as it stood at sunnypilot `e6a18c839`.

What is left of it:

- **Flag 32 is RESERVED** (`HondaFlagsSP.ELESYS_BRAKE_LAW_V2`, `values_ext.py`), like 16: `_initialize_honda()` never
  sets it and `CarController` ignores it, so an old CarParamsSP carrying it sends today's `0x1FA`, frame for frame
  (`test_elesys.py` `test_the_retired_brake_law_flag_runs_todays_law`). Do not reuse it.
- **`HondaElesysBrakeLawV2` is not registered.** A device keeps its file, unread, and a sunnylink restore skips it;
  `initialize_params()` no longer hands it over (`test_honda_elesys_pump_brake.py`), and
  `test_retired_keys_are_gone_everywhere` fails if a setting screen, sunnylink, statsd, the hooks or `carcontroller.py`
  name it, `BRAKE_LAW_V2_PARAM`, `brake_law_v2` or `elesys_brake` again.
- **The `blaw=` tag stays** in the `hondadyn` and `hondashadow` lines and always says `v1` (`shadow_learn.brake_law_tag()`),
  so lines keep their shape and the report never pools them with the `v2` lines of 2026-10-05 to 2026-10-09.
  `brake_route_check.py` (9.4) still reads flag 32 and `blaw=` on those routes.
- **What runs is what ran with the setting off:** today's brake law (7.1, with the learned gain of 9.1) and gas law v2's
  `elesys_pedal_v2()` on every frame (9.2). Through the real `CarInterface` on routes `10f`, `115` and `120`, CarParams,
  CarParamsSP and every sendcan frame are byte-identical to `e6a18c839` with the setting absent and with it `0`.

### 7.11 Full brake: no aero credit (2026-10-12)

Owner decision 2026-10-12 (option F of the longitudinal-fidelity study). The brake block sends
`np.clip(self.brake_last - wind_credit, 0.0, 1.0)`, where `wind_credit` is upstream's `wind_brake * wind_scale()` - the
drag the car gets for free, taken off the brake so a steady request lands the same decel at every speed. On
`HONDA_ELESYS` it is now multiplied by `self.wind_credit_scale`, which `elesys_wind_credit_scale(self.brake_last,
scale, CC.longActive)` steps once per 100 Hz frame, right after `brake_last` is rate-limited: toward 0 by
`ELESYS_CREDIT_RAMP` (0.1) while `brake_last` is at or above `ELESYS_FULL_BRAKE`, back toward 1 at the same rate below
it, and 1.0 outright whenever `longActive` is off (`FORK(HONDA_ACCORD_9G_AU)` above `process_hud_alert()`).

- **Why.** At full brake the credit only lowered the ceiling: `(0.99 - wind_brake) x 256` was 245 counts at 30 km/h,
  236 at 60 (measured 235-236 on route 121 t243-247, where the plan asked -2.25 to -3.02 m/s^2 and the car gave -2.2 to
  -2.58), 223 at 100 and 215 at 126, against 253 standing still. Now it is 253 at every speed with a brake gain of 1.0.
  What full braking gains is the credit, 2.6 x `wind_brake` m/s^2: about 0.08 at 30 km/h, 0.17 at 60, 0.30 at 100 and
  0.39 at 126.
- **Why a ramp** (review of push6's first cut, opendbc `c973ec83`). That cut returned a credit of 0 in the send where
  `brake_last` reached the ceiling: one step of up to 38 counts (0.3-0.4 m/s^2 at highway speed) that bypassed
  `brake_last`'s rise limit (0.01 a frame, about 5 counts a send), and a request hovering at the ceiling switched the
  whole credit in and out. The ramp takes it out in 10 frames (100 ms, 5 sends) and brings it back in 10, at most 7.6
  counts a send; entering from a ramped request no send rises more than 10 counts. No extra exit hysteresis:
  `actuator_hysteresis()` already holds `brake_last` at 0.99 until the request falls under 0.98, and a band keeping the
  credit off down to 0.93-0.97 helped only at moderate noise, made more steps at 0.10 m/s^2 of noise, and there held up
  to 35 counts more brake on average than the request - past "full braking".
- **Why 0.99.** `actuator_hysteresis()` holds a rising request `brake_hyst_gap` (0.01) under itself, so a request of 1.0
  leaves `brake_last` at exactly 0.99; `brake_last >= 1.0` is never true. `ELESYS_FULL_BRAKE = 0.99 - 1e-6`. A request
  of 0.999 or less settles at 0.989 or less and keeps the credit.
- **The count.** `int(0.99 x gain x 256)`: 253 at gain 1.0, 252 at 0.9974 (121's stored gain), and 255 - the code's
  `NIDEC_BRAKE_MAX - 1`, panda's `max_brake` - from a gain of 1.0062 (130's 1.0158). The 254 the study expected does
  not occur at gain 1.0.
- **What it touches.** Every frame of an application that never reaches the ceiling is bit-identical (the scale stays
  exactly 1.0); after one, the credit comes back over at most 10 frames and the release limiter (7.6) starts from the
  higher command (up to the credit, <= 38 counts, for <= ~8 sends). Entering the ceiling the command climbs by the
  credit (0 to 38 counts at 0 to 35 m/s) over 5 sends; that is above 200 counts, where v5 and C1b already run the pump
  continuously while moving, so the pump bit does not change. The soft stop (7.8) still applies after it. Stock AEB:
  panda forwards the CMBS `0x1FA` while its brake is >= openpilot's (8.2). **Corrected 2026-10-24 (brake320, 7.13):** this
  said a real CMBS brake is 320-1022 counts (`S:/OP/CMBS_AEB_bitmap.md`). Measured, the radar's request ceiling is 320
  with the pump bit on (its CMBS brakes of 1-7 July sit at 256-320); the 500-1022 values are its pump-off release
  step-downs (1022 -> 682 -> 340 -> 0), never requests. openpilot's ceiling here is at most that same 320 since brake320,
  so a CMBS brake at its ceiling is still >= anything openpilot sends and is forwarded.
- **brake320 leaves `ELESYS_FULL_BRAKE` at 0.99** (7.13): it is where the credit goes out - the old full brake. Moved to
  brake320's 1.24 it would give the credit back to every request between the two ceilings, less brake than before for
  the same request (223 instead of 253 counts for a -2.6 request at 100 km/h). A NaN `vEgo` still never
  raises: a NaN credit stays NaN whatever the scale, and the 7.8 fallback replaces the non-finite result.
- **The brake learner (9.1)** weights its error by the commanded fraction, which at full brake is now 0.99 instead of
  0.99 - wind. It needs 150 frames of a steady plan, so it rarely learns in an emergency; on routes 120, 121 and 130 no
  frame after a full-brake episode changed. It was already learning against the hardware ceiling there before; this is
  not new.

Replays and tests: `docs/CHANGELOG_SERIAL_STEERING.md` 2026-10-12 (push6). `test_elesys.py`
`TestElesysFullBrakeNoWindCredit` (15).

### 7.12 The longitudinal PID's floor: -3.2 on the level, following a climb (round 8, 2026-10-20)

**-3.25 since brake320 (2026-10-24, 7.13).** Full brake is now net -3.25 (a brake fraction of 1.25, 320 counts); -3.2
stopped 0.05 short of it (312 of 317 counts at a gain of 1.0). Everything below is as round 8 left it, with -3.25 for
-3.2. Why not the -3.85 that keeping round 8's 0.6 m/s^2 margin would give: that margin dates from before the floor
followed a climb, and below full brake it is only room for the integrator to wind. In the closed-loop sim (7.13) -3.85
reaches the same 319 counts and -2.94 m/s^2 as -3.25, and when the plan lets go mid-brake holds the brake 0.51-0.58
m/s^2 below it for 0.6-1.1 s, against 0.33-0.40 for 0.15-0.7 s.

Owner decision 2026-10-19 (proposal 9). `CarInterface.get_pid_accel_limits()` returns
`elesys_pid_accel_min(CP, CP_SP)` as the lower limit in its gas-interceptor branch (`FORK(HONDA_ACCORD_9G_AU)`):
`ELESYS_PID_ACCEL_MIN` (-3.2 m/s^2) on `HONDA_ELESYS` outside stock ACC mode (`HondaFlagsSP.ELESYS_STOCK_ACC`), and
upstream's `NIDEC_ACCEL_MIN` (-4.0) everywhere else - every other car, this car without the interceptor, and stock ACC
mode, where nothing longitudinal is sent. controlsd hands these limits to `LongControl`, whose PID clips its output
there and stops integrating once it is clipped.

**On a climb (round 8 review).** `controlsd.py` (`FORK(HONDA_ACCORD_9G_AU)`, where `elesys_pid_floor_applies(CP,
CP_SP)` - the same car, interceptor and stock-ACC test as the branch above) replaces that floor with
`elesys_pid_accel_min(CP, CP_SP, pitch)`, the pitch being `calibrated_pose.orientation.y`, the value it puts in
`CC.orientationNED[1]` and the dynamic tuner filters. The floor is -3.2 on the level and downhill, `-3.2 - g*sin(pitch)`
on a climb, never below -4.0, and -4.0 without a usable pose. Why: the tuner's pitch feedforward (`hill_accel`, up to
+1.5) is added after the PID (`carcontroller.py`, `adjust_accel = accel + hill_accel`), so on a climb it takes braking
back, and once `brake_last` drops below full the aero credit comes back too. The flat -3.2 gave full brake only up to
about 6%: held at the floor, the real `CarController` (tuner on, learned gain 1.006) settled to 234 / 224 / 205 / 186
of 254 counts at 7 / 8 / 10 / 12% at 17 m/s (222 / 212 / 193 / 174 at 27 m/s), where -4.0 gave 254. With the climb's
term taken off the floor, floor + `hill_accel` stays at or below -3.2 up to about 8%, and above that the floor is
-4.0, so it brakes as -4.0 did on every grade (254 at 0-12% at both speeds, a test). Climbs over 6% were 0.62% of
engaged time above 5 m/s on 14 routes (round 8 review's pitch scan).

- **Why.** `compute_gb_honda_elesys()` puts full brake at a net -2.6 m/s^2 (brake = -net / 2.6), so a PID output below
  about -2.6 asks for nothing more: the brake is already at its ceiling, and -4.0 only gives the integrator room to wind.
  Measured once in 42 engaged minutes (drives `13d`-`143`): 143 t=172.9-174.6, the stopped car at night, where the PID
  sat at -4.0 for 1.7 s with its integrator wound to -1.33 while `0x1FA` was already at full brake (plan -2.1 to -2.9,
  achieved -1.65 to -1.92). Had the plan let go there, that integrator would have held the brake on until it unwound.
- **Why -3.2.** On the level it is still full brake, and with the climb's term off the floor (above) it stays full
  brake on a climb as well.
- **Replayed** with the real `LongControl` on every engaged frame of `13d`, `141`, `142` and `143` (fidelity against the
  logged output: median error 0.0005 m/s^2): one stretch changes, 143 t=172.04-174.69 (265 frames; a slight downhill, so
  -3.2), where the integrator stops at -0.81 instead of -1.33; the brake was full on every one of those frames either
  way, and the driver braked at 174.68, so nothing changed on the wire. Not one frame changes on `13d`, `141` or `142`.
  The grade-aware floor, from the logged pitch, gives the same 265 frames on 143 and none on 13d, 141, 142, 10f, 129,
  12a or 137 (climbs to 12-25% in the raw pitch), and on no frame of the eight routes less brake than -4.0 with the
  climb's term added back. Above the floor the PID is upstream's to the bit.
- Tests: opendbc `test_elesys.py` `TestElesysPidAccelLimits` (6: -3.2 at every speed on this car; upstream's limits in
  stock ACC mode, without the interceptor and on every other car; -3.2 is full brake on the level with a slight climb's
  feedforward added; the floor follows a climb at 0-30% and is -3.2 downhill and -4.0 without a pitch;
  `elesys_pid_floor_applies()` exactly where the branch takes the floor; the real `CarController` held at the floor on
  0-12% climbs brakes as hard as with -4.0); sunnypilot `openpilot/selfdrive/controls/tests/test_elesys_pid_floor.py`
  (6, the real `LongControl`: the windup at full brake stops at the floor minus the plan, -0.5 against -1.3, with the same
  full brake throughout; the release after it is more than a second quicker; random PID traffic above -3.2
  bit-identical to the -4.0 floor; on 0-15% climbs the brake with the climb's term added back as full as with -4.0; and
  the controlsd hunk pinned, so a merge cannot drop it silently).

### 7.13 brake320: the brake ceiling is 320 counts on this car (2026-10-24)

Owner decision 2026-10-24: raise openpilot's brake ceiling on this car from 255 to 320 counts, now. The owner knows the
VSA logged a 32-11 solenoid fault once (2026-10-01, 6.6, `S:/OP/incident-2026-10-01`): "the VSA hasn't faulted since
that one time, it might have just overheated, so go for it ... it's a 2 day repair job worst case if it fails again".
The VSA fallback below was added for safety, not asked for. Code: `opendbc/sunnypilot/car/honda/brake_ceiling.py` (its
docstring is the design), `carcontroller.py`, `honda.h`; every hunk is `FORK(HONDA_ACCORD_9G_AU)` and says brake320.

**The measurement** (the push13 brake-cap study, today's car, level road, July-October; steady deceleration at a held
`COMPUTER_BRAKE`): 100 counts -1.1 m/s^2, 150 -1.76, 200 -2.2, 240-255 -2.45 (95% CI -2.34..-2.62), 320 about -2.95.
About 1.0 m/s^2 per 100 counts from 60 to 200 and 0.7 above 180; lag about 0.4 s. The Elesys radar's own request ceiling
is 320 with the pump bit on; above 320 it only ever sends pump-off release step-downs (7.11). 255 -> 320 is about +0.5
m/s^2, inside ISO 15622's -3.5. "Full brake is -1.8 to -2.0", which `reentry_ramp.py` and `elesys_radar_guard.py`
said, was wrong (143's -1.65 to -1.92 was full brake on a ~5% downhill); both comments now carry the measured numbers.

**What it buys: less impact speed, not a guaranteed stop.** On route 143 (the stopped car at night, a ~5% downhill)
brake320 would have stopped about 1-4 m short instead of needing the driver, replayed open loop on 143's own trajectory -
but only because the plan had already been braking hard since t~170. A plan that first reacts at the radar's firm ranges
there (64-73 m at 52-57 km/h) still hits at 10-20 km/h with the brake as measured on 143 (push12: 25-31 km/h), and on
that slope a 58 km/h stop needs about 80-90 m of warning (review closed-loop sim, 2026-10-24). On the level a stopped car
70 m ahead at 60 km/h goes from an 18.8 km/h impact to a stop 1.4 m short; 80 km/h with 70-90 m still hits either way
(Proofs, below).

**What changed**

| where | before | now | why |
|---|---|---|---|
| panda `honda.h` | `max_brake = 255` (const) | `honda_nidec_max_brake`: 320 with param bit 128 AND the stand-down (32), 255 otherwise; `honda_bosch_init()` resets it to 255 | only this car; the bit alone, stock ACC mode (64) and the 32/64 conflict stay 255 |
| `HondaSafetyFlags` | - | `ELESYS_BRAKE_320 = 128`, set beside 32 in `interface.py`; stock ACC mode clears it with 32 | the car's param is **164** (4\|32\|128), 36 before; stock ACC mode is still 68 |
| CarController ceiling | `NIDEC_BRAKE_MAX - 1` (255) | `brake_ceiling.panda_max_brake(CP)`: honda.h's rule on the same param (320 here), 255 on every other car, 255 after the VSA fallback | the car side can never send what panda refuses: a refused 0x1FA is a gap, and the VSA answers a ~1 s gap with BRAKE_ERROR |
| `NIDEC_BRAKE_MAX` (scale) | 256 | **256, unchanged** | 2.6 m/s^2 per 256 counts is the measured slope; 256 -> 320 would make every request 25% harder, more than the learner can take back (floor 0.85) |
| brake fraction clip (`compute_gb_honda_elesys()`, the brake block) | 1.0 | **earned**, in the pid state only: 1.0 up to a request of -2.45 m/s^2, rising linearly to 1.25 (320 / 256) at a full request (-3.25 net); 1.0 in every other state | a request beyond the old ceiling now asks for more; an ordinary one is clipped exactly as before |
| count clip | 255 | **earned**, in the pid state only: 255 up to a request of -2.45, rising linearly to 320 at a brake fraction of 1.24 (`BRAKE_LAST_MAX_320`, what `actuator_hysteresis()` leaves of a full request); 255 in every other state | bounds the learned gain, and keeps a standstill hold at 255 or less: see below |
| `ELESYS_FULL_BRAKE` (credit out) | 0.99 | **0.99, unchanged** | 7.11: it is the old full brake, where the credit leaves; moving it would brake less than before between the ceilings |
| full brake on the wire | 253 (gain 1.0), 255 from gain 1.0062 | 317 (gain 1.0), 320 from gain 1.0081 | `int(1.24 x gain x 256)` |
| `ELESYS_PID_ACCEL_MIN` | -3.2 | **-3.25** | 7.12 |
| `SOFT_STOP_DONE_CB` | 255, "the highest command the brake block can send" | the default stays 255; the CarController passes its own ceiling (`done_cb`, 320 or 255) | out of the way means at the real top; on any command up to 255 the stop is the same frame for frame (test). In the stopping state, the only place it acts, nothing earns above 255, so between 255 and 320 it never binds |
| brake learner (`dynamic_tuning.brake_gain()`) | weight = the fraction (<= 0.99 in practice) | the fraction capped at `BRAKE_LEARN_FRAC_MAX`, 0.99 - the most any Honda's brake block ever handed it; the shadow learners get it whole | up to the old ceiling its input is exactly what it was, and beyond it the same 0.99 push12 fed it there, so it learns what push12's learned. Capped at 1.0, as first built, the gain came out a hair apart and later ordinary braking differed by a count either way for minutes (review 2026-10-24). The shadow learner's 256 is the scale, unchanged, and now sees counts above 256 |
| `REENTRY_RAMP_MPC_FLOOR` | -2.0 | **-2.0, unchanged**; the comment corrected | the ramp only ever takes braking away; giving way 0.45-0.95 m/s^2 before the brake's reach is the safe side |
| release limiter (32 counts a send), both pump rules | - | unchanged | 317 -> 0 in ten sends; above 200 counts while moving both pumps run continuously, 320 included (tests) |
| 0x1FA `FCW`/`CHIME` (review fix) | openpilot's own FCW alert only | also the radar's warning (its 0x1FA `FCW` >= 2) while openpilot sends more than 255 | a CMBS brake of 256-319 below openpilot's no longer reaches the car with its warning; this carries it (AEB passthrough, below) |
| VSA fallback mid-brake (review fix) | - | walks down to 255 through the release limiter, 32 a send | not a 65-count cut in one send (the fallback, below) |
| brake hysteresis state (review fix) | at most 0.99 by construction | clamped to 0.99 (`BRAKE_STEADY_MAX_255`) on any frame that earns nothing | coming down from above 1.0 it settled at 1.01: 255 where push12 sent 253, e.g. a whole standstill after a hard stop; with it every frame after an episode is push12's once the limiter has walked down |

**The request, and the learned gain.** Both clips are earned by `request_fraction()`: -(the plan's accel plus the pitch
feedforward) / 2.6, without the creep offset. Up to 2.45 / 2.6 (`EARN_START_FRAC`, the old ceiling's measured -2.45) they
are the old 1.0 and 255, so the frame is push12's exactly. The creep offset is left out because on its own it took a -1.5
request at a crawl to a fraction of 1.01 (258 counts; push12 253) - found by `test_dynamic_tuning_integration.py` [2].
The learned brake gain (up to 1.6x) used to be bounded by the 255 clip too; with the earned count clip a runaway gain on
an ordinary request still stops at 255, and beyond -2.45 gets only what the request earns (267 counts for -2.6, 320
from -3.22).

**Only the pid state earns** (review fix, 2026-10-24). LongControl's stopping state holds its last output when that is
already below `stopAccel`, and the pitch term is 0 there, so as first built a stop entered from a hard brake went on
"earning" at a standstill: 267-317 counts held for the whole stop, pure hold pressure on the VSA that logged the fault.
On the logged plans that was 8 of 373 stops (routes 03, 0f, 69, 6e, 6f, 71, 89, 8a; held -2.62 to -3.02, 13-34 s each;
18% of all beyond-the-old-ceiling frames). Now the CarController passes `longControlState == pid` to
`ElesysBrakeCeiling.set_request()`, and in any other state the request earns nothing: the clips are 1.0 and 255, and on
such a frame the brake hysteresis state is clamped to the old ceiling's 0.99 (`BRAKE_STEADY_MAX_255`; coming down from
above 1.0 it would otherwise settle at 1.01 and hold 255), so the hold is push12's, frame for frame (253 at a gain of
1.0). The same clamp makes every frame after a beyond episode push12's once the release limiter has walked down. Stopping
starts below 0.8 m/s (`stopping_tune.STOPPING_SPEED`), where the extra brake was worth about 2 cm of stopping distance,
and the soft stop caps a stop entered below 1.2 m/s at 105 anyway. Test: `test_a_hard_stop_holds_no_more_than_255`.

**Two limits that stay** (review, 2026-10-24; no change). Full brake is `int(1.24 x gain x 256)` up to the earned ceiling:
317 at a gain of 1.0, 320 from 1.0081, but 269 at the learner's 0.85 floor (push12 215 there) - the -3.25 PID floor is
exactly full brake at a gain of 1.0, so nothing asks for more. The car's gains have been 0.997-1.018, and the live learner
winds out of a low gain in a long saturated brake (the review's replay at a forced 0.85: route 143 still reached 320).
And above 255 the count is not smoothed beyond the brake law's own: at a learned gain above 1 it follows
`earned_ceiling()` of the request, as fast as the plan moves (on the way down the 32-a-send limiter still applies). On
logged plans at a forced 1.6 gain that was at most +13/-8 counts a send; only a noisy synthetic plan (sigma 0.1 m/s^2 at
20 Hz) flaps it 25-36 counts a send. Today's plans are smooth enough that no rate limit is added.

**The VSA fallback** (`Brake320Fallback`, in `CarStateExt._update_vsa_fault()` on every CarState frame from card's
start, so a fault before controls start counts too). Any of these, once, takes the ceiling back to 255 for the rest of
the drive - card's life, i.e. the key cycle; the CarController reads it as `CS.brake320_fallback`. Latched before any
braking above 255, every 0x1FA from then on is the 255 controller's, byte for byte (test). Latched mid-brake above 255,
the command walks down to the 255 controller's through the release limiter, 32 counts a send (320 -> 288 -> 256 -> 255
at a high learned gain; without the tuner, which has no limiter, at once); as first built it cut 320 to 255 in one send, about -0.5 m/s^2 in 20 ms mid-brake (review
fix 2026-10-24, `ElesysBrakeCeiling.wire_ceiling()`, test `test_a_fallback_mid_brake_steps_down_through_the_limiter`).
panda allows 320 for the whole drive, so the walk is never refused. The signals:

- `carStateSP.vsaFault` or `vsaStoredFault` (6.6), counted at once (they handle start-up themselves);
- `carState.accFaulted` (0x1B0 `BRAKE_ERROR`) or `espDisabled` (0x1A4 `ESP_DISABLED`, the VSA lamp), counted only once
  the VSA fault monitor's start-up window is over (`VsaFaultMonitor.settled`, 5 s from the VSA's first frame): the bulb
  check lights `ESP_DISABLED` for up to 1.7 s on almost every route and never later (201 logged routes scanned);
- the VSA fault monitor raising (`vsaMonitorError`): without it a fault would go unseen.

On the real frames of test_vsa_fault.py's fixture it latches on 110 and 112 (`vsaFault`, the onset frame) and 111 and
113 (`vsaStoredFault`), and not on 10f's clean start (whose bulb check lights `ESP_DISABLED`), on the same bulb check
stretched to 4.08 s, or on comma route 69's other lamp state. It logs one `hondabrake fallback=<reasons> frame=<n>` line
at error level (`errorLogMessage`, in the qlog). In practice openpilot is not engageable while any of these is up (an
`accFaulted` disengages; a stored fault, `espDisabled` refuse engagement); the fallback matters once one clears inside
the drive. Residual: after a fallback the PID floor stays -3.25, so the room below full brake is 0.65 m/s^2, not 0.6.

**Logging.** `customReserved14` (card, `brake320_log.py`), once a second and on the step the ceiling or the fallback
changes, only on this car with openpilot longitudinal: `allowedCounts`, `ceilingCounts`, `vsaFallback`,
`vsaFallbackReason`, `vsaFallbackFrame`, `maxCountsLastSecond`, `sendsAbove255LastSecond`, `maxCountsDrive`,
`sendsAbove255Drive`; decimation 1, so it is in the qlog. And one `hondabrake max=<n> above255=<n> ceil= allowed=
fallback=` logMessage line for every second that sent above 255. No Params I/O.

**AEB passthrough.** Unchanged: panda forwards the radar's 0x1FA while FCW (bit 43) is set and its brake is >= the last
openpilot brake, and refuses openpilot's while it does. openpilot's ceiling is now the radar's own request ceiling, so a
CMBS brake at its ceiling (320) is forwarded over openpilot's 320; below it, openpilot's harder brake continues until it
brakes less (tests: `test_cmbs_at_the_radars_ceiling_is_forwarded_over_openpilots_320`,
`test_cmbs_is_forwarded_once_openpilot_brakes_less`). That is the one intended change (review, 2026-10-24): with
openpilot capped at 255 every CMBS request of 256 or more won the comparison, and 6 of the 27 logged CMBS events at 256 or
more peaked below 320 (262-318). Now, while openpilot brakes harder than such a request, panda keeps sending openpilot's
frame - the stronger brake reaches the car either way, up to the 20 ms comparison lag - and the radar's own frame, with
its FCW and CHIME bits, waits until openpilot brakes less. Nothing else would have warned the driver then: today's
`stockAeb` rule (CMBS_BRAKE, AEB_REQ_3, AEB_REQ_2 or AEB_STATUS 1, since 2026-08-05) fires on none of those 27 events -
their radar frames carry FCW=2, a CHIME of 1-2 on 24 of them, and the brake, nothing else (`brake320/fix/win/cmbs_bits.py`;
the 24 that logged `stockAeb` in June-July ran the older rule) - and `stockFcw` raises no alert. So, review fix: while
openpilot sends more than 255, its own 0x1FA carries the radar's FCW (2) and CHIME whenever the radar's 0x1FA has FCW >= 2
(the bit panda reads as stock AEB; `ElesysBrakeCeiling.fcw_on_the_wire()`, through upstream's
`hondacan.create_brake_command()`, which already copies the radar's CHIME for openpilot's own FCW). Up to 255, and with
no radar warning, the frame is push12's (test `test_the_radars_warning_rides_on_openpilots_frame`). The `stockAeb` rule
missing these FCW brakes is older than brake320 and not changed here. And a
release above 320 cannot take the brake from openpilot mid-brake in the data: of the 111 pump-off release step-downs in
`push13/brakecap/release_flashes.csv` only 3 carry FCW=2, all right after a 320 hold, so forwarding had already latched
(a data check, the review's, not a proof of what the radar can do).

**How the car's panda gets it.** honda.h is compiled into the panda's firmware: `panda/SConscript` builds
`panda/board/obj/panda_h7.bin.signed` from `panda/board/main.c` with `opendbc.INCLUDE_PATH` on the include path, which
inside scons is the tree's own `opendbc_repo` (`-Iopendbc_repo`). The device installs the source branch and builds it
with scons after an update. At every start `pandad.py`'s `flash_panda()` compares the panda's signature with that file's
(`Panda.get_signature_from_firmware()`, its last 128 bytes), flashes on a difference, and raises (and retries, never
starting the car mode) if the signature still differs after the flash. Measured on this tree: push12's firmware
(sha256 `631c9f66...`, signature `b023b54a...`) has no `honda_nidec_max_brake` symbol; brake320's (`191d5b11...`,
signature `7183e525...`) has it (`readelf -s`, initial 255 in `.data`). So the first start after the update flashes the
panda, and a panda still on the old firmware never runs the car mode. Do not hand-flash a panda from another tree.

**Proofs** (scratch `brake320/`):

- *Closed-loop sim* (`b320sim.py`, the skeptic sim's frame with the real LongitudinalMpc, the real LongControl with the
  floor, and the real CarController; the car is the study's measured curve, 0.4 s lag; tuner on, learned gain 1.0057).
  Stopped car appearing, 255 -> 320: 60 km/h at 70 m: impact 18.8 km/h -> stops 1.4 m short; at 90 m: both stop (5.8 /
  5.1 m; the plan never passed -3.0). 5% downhill: 70 m 32.0 -> 22.6 km/h; 90 m impact 14.7 km/h -> stops 5.5 m short.
  80 km/h at 70/90 m: both hit (59.1 -> 55.8, 47.3 -> 40.2 km/h; level), beyond the car either way. Lead braking: 30 km/h
  at -4 both stop (1.1 / 2.3 m), at -5 impact 1.7 km/h -> stops 1.2 m short; 50 km/h at -4 / -5 both hit (25.0 -> 19.4,
  29.3 -> 25.6 km/h). Peak counts 254 -> 319, brake decel 2.49 -> 2.94 m/s^2, first send above 255 1.2-2.0 s into the
  event (the 0.01/frame rise limit); jerk while moving unchanged (3.5-4.2 m/s^3 in the stops). The -3.2 floor peaks at
  314-315 and stops 0.05-1.1 m later; -3.85 above.
- *Replay* (`b320replay.py`, open loop: the logged inputs through the real LongControl and CarInterface, push12's opendbc
  against brake320's) on 143, 14c, 14d, 121, a6, 134, 131, c8, 9e (3.74 h, 2.06 engaged): 673,019 sends, 1,500 differ, 0
  with less brake. 14c and 14d: none differ. 899 differ while the request is beyond -2.45 (12 episodes: 5.8 per engaged
  hour, 10 s per engaged hour; over all 126 logged routes, from the logged accel and pitch, 117 episodes in 16.2 engaged
  hours: 7.2 per hour, 9.7 s per hour); up to +65 counts, 858 sends above 255. The rest are tails: 97 in the 0.5 s after
  an episode (the release limiter walking down from the higher command), and 504 later - the PID integrator under the
  -3.25 floor (a6, c8: +1 to +6 counts for up to 7 s, at most 0.044 m/s^2) and the brake learner a hair apart (143, 9e:
  +1 count on 219 sends). 150 has qlogs only (no CAN): its logged request crossed -2.45 once, for 0.5 s.
- *Re-run after the review fixes* (scratch `brake320/fix/`; the bullets above are brake320 as first built).
  - The review's open-loop CarController replay of every engaged span of 164 routes (21.23 engaged h, 4,648,666 sends;
    `fix/diffrep_fin.py`, `fix/cmp_fix.py`): against push12, 26,207 sends differ - 26,205 while the request is beyond
    -2.45, 2 in the 0.5 s after, **none later and none with less brake**; no send above 255 at a standstill; one in the
    stopping state, the limiter walking down at its entry (route 03: 302 -> 270 -> 254). The gas frame 0x200 is
    identical. As first built: 50,719 differ, 283 with a count less (up to 14 min after an episode), 12,870 later, and
    6,461 sends above 255 standing still (129 s; the 8 stops above).
  - `b320replay.py` (CAN -> CarState, the real LongControl and CarInterface) on the nine routes above plus 07 and 1f
    (5.38 h, 2.93 engaged, 967,945 sends; `fix/win/repcmp.py`): 2,497 differ, 1,570 beyond, 142 within 0.5 s, 785 later;
    **0 with less brake**, 0 above 255 at a standstill or outside the pid state; 14c and 14d none; at most +66 counts.
    Every later one comes from the -3.25 PID floor (its integrator after a saturated brake, and the brake learner
    weighting that slightly different output): the same code with the floor at -3.2 differs from push12 on 143, 9e, a6
    and 07 only on frames whose request is beyond -2.45 (`fix/win/f32cmp.py`).
  - The closed-loop sim (`fix/b320sim_fix.py`, the builder's with a standstill hold, the largest send-to-send drop while
    moving, and the VSA fallback injected mid-brake): every stop and lead case ends exactly as brake320 first built did
    (the 60 km/h, 90 m, 5% downhill stop 5.47 m short instead of 5.48). The fallback injected at 319 counts now steps
    down at most 32 a send (was 64 in one send); a stop at 60 km/h with 70 m then still hits at 7.1-13.0 km/h (push12
    18.8) - the price of falling back mid-brake.

**Tests.** Panda: `HondaElesysBrake320` over the stand-down classes with and without the pedal (`TestHondaElesysBrake320Safety`,
`...NoInterceptorSafety`: every inherited Nidec test at MAX_BRAKE 320 - 0-320 sent, 321 refused - plus the contract
value, the AEB tests above, the bit only with the stand-down, Bosch resetting it) and `TestHondaNidecBrake320BitAloneSafety`
(the bit alone: 255); 216 tests in the three classes; the full safety suite 9,342, line coverage 100%; the operator
mutation run on honda.h 254 of 254 killed; MISRA clean. opendbc: `test_elesys_brake320.py` (29: the params, **every Honda
platform and config driven at accel -10 with a 1.6 gain through the real panda tx hook with its own param - all allowed,
and `panda_max_brake()` equal to the panda's own limit**, the law, 320 encoded and decoded, in-range frame for frame,
beyond only more brake, the runaway gain, creep never earning it, the release limiter, the pumps, the fallback on real
frames, the telemetry, other Hondas unchanged), `test_elesys_stop.py` (+1, `done_cb`), `test_dynamic_tuning.py` [17],
and the updated `test_elesys.py` (`TestElesysFullBrakeNoWindCredit` now pins the 255 controller), `test_elesys_stock_acc.py`
(164) and `test_dynamic_tuning_integration.py` (the legal range is panda's). sunnypilot: `test_brake320_log.py` (6) and
`test_elesys_pid_floor.py` (-3.25). After the review (2026-10-24) `test_elesys_brake320.py` has 32, each new one failing on
brake320 as first built (`fix/pre.log`): `test_a_hard_stop_holds_no_more_than_255` (the standstill hold is push12's frame
for frame), `test_after_a_beyond_episode_the_learned_gain_is_push12s`, `test_the_radars_warning_rides_on_openpilots_frame`,
`test_a_fallback_mid_brake_steps_down_through_the_limiter`, and `test_beyond_it_only_more_brake` without its 2-count
slack; `test_dynamic_tuning.py` [17] pins the learner's 0.99.

**On the first drives** (the owner, from the qlog): `pandaStates.safetyParam` 164; `customReserved14` once a second with
`allowedCounts` 320, `ceilingCounts` 320, `vsaFallback` false; after a hard stop `maxCountsDrive` above 255 and a
`hondabrake max=` line in the rlog; no `BRAKE_ERROR`. Any `hondabrake fallback=` error line means the ceiling went back
to 255 for that drive: look at the VSA bits (6.6) first.

`safetyParam` 164 does not prove the panda runs brake320's firmware: the panda echoes whatever param it was given
(`main_comms.h`, `health->safety_param_pkt = current_safety_param`), so old firmware would show 164 too and refuse every
0x1FA above 255 (review, 2026-10-24). Two checks that do prove it:

- `pandaStates.safetyTxBlocked` must not grow in any second where `customReserved14.maxCountsLastSecond` is above 255. A
  refused 0x1FA counts there (`main_comms.h`, `safety_tx_blocked`), and the VSA's `BRAKE_ERROR` would follow about 1 s
  later (6.6). Any growth in such a second: stop using openpilot longitudinal and check the firmware.
- pandad's start-up line in the rlog, `Panda <serial> connected, version: ..., signature <16 hex>, expected <16 hex>`
  (`pandad.py` `flash_panda()`): after the update's first start the two must match, followed by no "Version mismatch
  after flashing" line. A signature still equal to push12's (`b023b54a...`) is the old firmware.

(In practice `flash_panda()` never starts the car mode on a signature mismatch, so old firmware should not get as far as
a 0x1FA; these checks are how a drive shows it.)

## 8. Panda safety (`opendbc/safety/modes/honda.h`)

This is the highest-risk code in the fork. It runs in the panda and decides what openpilot may put on the car's buses. Every change is gated on `honda_elesys_scm_standdown`. That flag is false for every other car, and `honda_bosch_init()` explicitly resets it to false.

### 8.1 Changes

| # | change | detail |
|---|---|---|
| 1 | state | `static bool honda_elesys_scm_standdown = false;`, set in `honda_nidec_init()` from `GET_FLAG(param, HONDA_PARAM_ELESYS_SCM_STANDDOWN)` (`= 32`) and reset in `honda_bosch_init()` |
| 2 | TX list without the pedal | `HONDA_N_ELESYS_STANDDOWN_TX_MSGS`: `{0xE4, 0, 5, check_relay}`, `{0x194, 0, 4, check_relay}`, `{0x1FA, 0, 8}`, `{0x30C, 0, 8, check_relay}`, `{0x1A6, 2, 8}`, `{0x500, 0, 8}`. 0x194 is allowed but never sent on this car (14.4) |
| 3 | TX list with the pedal | `HONDA_N_ELESYS_STANDDOWN_INTERCEPTOR_TX_MSGS`: the same list plus `{0x200, 0, 6}`. The interceptor branch used to overwrite the TX list unconditionally; it now picks the stand-down list when the flag is set |
| 4 | 0x33D removed | unlike `HONDA_N_COMMON_TX_MSGS`, neither list contains 0x33D. openpilot cannot transmit `LKAS_HUD` on this car. The panda does not block a 0x33D arriving from bus 2; the one on bus 0 is the board's |
| 5 | 0x1A6 forward block | `honda_nidec_fwd_hook()`: `if (honda_elesys_scm_standdown && (bus_num == 0) && (addr == 0x1A6)) block_msg = true;`. The driver's `SCM_BUTTONS` never reach bus 2; openpilot's re-send (7.5) replaces them |
| 6 | stock-AEB bit | `honda_rx_hook()`: `honda_stock_aeb = honda_elesys_scm_standdown ? GET_BIT(msg, 43U) : GET_BIT(msg, 29U);` on the bus-2 0x1FA. The rest of the forward logic is upstream's: forward while `honda_stock_brake >= honda_brake`, stop when the flag clears |
| 7 | `ACC_HUD` gas exception | `honda_tx_hook()` skips `longitudinal_gas_checks()` when `pcm_speed == 0 && pcm_gas == 198`, matching the factory camera. **This relaxes the check:** a 0x30C with `PCM_GAS = 198` and `PCM_SPEED = 0` now passes even while controls are not allowed. I found no current openpilot code path that sends that combination (with the interceptor fitted, `pcm_accel` is 0). Revisit whether it is still needed |
| 8 | brake320 (2026-10-24) | `static int honda_nidec_max_brake = 255;`; `HONDA_NIDEC_LONG_LIMITS.max_brake = honda_nidec_max_brake`; `honda_nidec_init()`: `honda_nidec_max_brake = (GET_FLAG(param, HONDA_PARAM_ELESYS_BRAKE_320) && honda_elesys_scm_standdown) ? 320 : 255` (`= 128`); `honda_bosch_init()` resets it to 255. The AEB forward logic is untouched (7.13) |

### 8.2 Why these bits

- **0x1A6 on bus 2** is the stand-down mechanism (7.5). It is not relay-checked because the stock frame is stopped by the forward hook, not by the relay.
- **0x500 on bus 0** is area B's `SP_HUD_STATUS`. No stock ECU sends or reads it. It moved from bus 2 to bus 0 in opendbc `a091808d`.
- **Bit 43** is the MSB of `FCW` (`43:2`). `S:/OP/AEB_passthrough_signal.md` found two things. First, bit 29 (`AEB_REQ_1`), which upstream gates on, is the wrong bit on this car. Second, `FCW` combined with the `COMPUTER_BRAKE` magnitude check separates real events from warnings. The code uses bit 43 only. That covers `FCW` values 2 and 3, but not 1, and not `AEB_STATUS`, which the same document also suggested.
- **The two AEB definitions differ.** The panda forwards the stock brake on bit 43. `carState.stockAeb` (6.3) uses `CMBS_BRAKE`/`AEB_REQ_3`/`AEB_REQ_2`/`AEB_STATUS == 1`, and its comment (and the `TestElesysStockAeb` comment) says `FCW = 2` appeared only on warnings with `COMPUTER_BRAKE = 0`. So the panda's flag can set on a warning, and because the forward test is `>=`, it can switch to forwarding the stock frame (brake 0) while openpilot is not braking. The two were written at different times (safety in `04a48a0a`, carstate in `2bc5c4db`) and were never reconciled. I have not checked on the car which one is right. Treat this as an open question, not a bug report.

### 8.3 Tests (`opendbc/safety/tests/test_honda.py`, `common.py`)

**`TestHondaElesysScmStanddownSafety(TestHondaNidecPcmAltSafety)`.** It hooks `hondaNidec` with `NIDEC_ALT | ELESYS_SCM_STANDDOWN`, and sets:

- `TX_MSGS = HONDA_N_COMMON_TX_MSGS + [[0x1A6, 2], [0x500, 0]]`
- `FWD_BLACKLISTED_ADDRS = {2: [0xE4, 0x194, 0x30C], 0: [0x1A6]}`
- `RELAY_MALFUNCTION_ADDRS = {0: (0xE4, 0x194, 0x30C)}`

It overrides three methods:

- `_send_brake_msg()` sets the stock-AEB flag through `FCW = aeb_req * 2`, which is bit 43.
- `test_acc_hud_safety_check()` checks the 198 exception over every `pcm_gas` 0-254 and `pcm_speed` 0-99.
- `test_fwd_hook()` checks that 0x1FA is blocked from bus 2 to bus 0 unless `honda_fwd_brake` is set.

**`TestHondaElesysStanddownGasInterceptorSafety(TestHondaNidecAltGasInterceptorSafety)`** is the same with `HondaSafetyFlagsSP.GAS_INTERCEPTOR`, and `[0x200, 0]` added to `TX_MSGS`.

**`common.py`** extends the `test_tx_hook_on_wrong_safety_mode` exceptions: `TestHondaElesys*` joins `TestHondaNidec*` for the VW MQB 0x30C overlap, and 0x1A6 joins the list of messages common to all Hondas. Since the 2026-09 merge it also exempts 0x500 between the two `TestHondaElesys*` classes only: both stand-down TX lists carry `SP_HUD_STATUS` for the gateway, so each mode "allowed" the other's 0x500 and the test failed twice (already at the pre-merge fork `c61cfd9b`). 0x500 is still checked against every other brand's modes.

**Gap:** both classes inherit `HONDA_N_COMMON_TX_MSGS`, which contains `[0x33D, 0]`. So `test_spam_can_buses` never asserts that 0x33D is blocked in the stand-down mode. The C lists are correct (0x33D is absent); only the test does not pin it. One fix is `TX_MSGS = [m for m in HONDA_N_COMMON_TX_MSGS if m[0] != 0x33D] + ...`.

### 8.4 Re-applying the safety changes

Upstream `honda.h` and `test_honda.py` were unchanged between the old fork point and `f95f996f`, and both merged cleanly in 2026-09 (so did `common.py`, where upstream made a 115-line change). If upstream rewrites them:

- Keep the flag value 32 in both `HondaSafetyFlags` and `honda.h`, and check that upstream has not claimed 32.
- Keep the two TX lists in step, as their comment says.
- Keep the bus-0 0x1A6 block in the forward hook, the bit-43 selection and the reset in `honda_bosch_init()`.

Then run the full safety suite and MISRA/cppcheck. The device's panda firmware is built from this file, so a change here only takes effect after a panda flash.

### 8.5 The MADS heartbeat race (`opendbc/safety/sunnypilot/mads.h`, 2026-10-04, `FORK(UPSTREAM-FIX)`)

Route 114 at 805.2: an LKAS press granted lateral in the panda inside the one tick interval after a heartbeat exit, when
`heartbeat_engaged_mads_mismatches` still read 3 (it is only cleared by a 1 Hz tick that finds no mismatch). The next
tick, before pandad's 10 Hz heartbeat could report MADS engaged, revoked the grant: 200 `0xE4` blocked and
`controlsMismatchLateral` at 807.53. The grant now zeroes the count, as `safety.h` zeroes `heartbeat_engaged_mismatches`
on the rising edge of `controls_allowed`; three fresh mismatches still exit. Not car-specific (every MADS car), the
panda's code, so it reaches the car only with a panda flash (pandad flashes the panda when its firmware differs from
the build's). Tests: `test_heartbeat_engaged_mads_regrant_*` in `mads_common.py` (every MADS safety test class) and
`test_route_114_lkas_regrant_survives_the_next_heartbeat_tick` (114's real `0x1A6` frames) in `test_honda.py`, run
under the stand-down (param 36) and stock ACC mode (68) from one mixin, `HondaElesysRoute114Regrant`;
coverage 100% on `mads.h`, MISRA clean. What the tests pin, checked by hand-made mutants rather than the operator
mutation run (which only flips increments, comparisons, boundaries, bitwise and arithmetic operators and negations,
so it never deletes or moves this statement): with the reset taken out, the three regrant tests fail; with it moved
to the top of `m_update_control_state()`, i.e. run on EVERY received frame - which on the car would switch the
heartbeat exit off, CAN arriving at ~100 Hz between the 1 Hz ticks - `test_heartbeat_engaged_mads_exits_with_can_traffic_between_ticks`
and `..._regrant_exits_with_can_traffic_between_ticks` fail (100 frames before each of three ticks; lateral must still
exit on the third). So the fix is no weaker than upstream's long-standing reset on the rising edge of `controls_allowed`. A replay of 114 and 115 through both builds of the
safety code with the panda's 1 Hz heartbeat checks simulated (the tick's place in its 100 ms window scanned): the old
code reproduces 114's 200 blocked `0xE4` exactly, the new code blocks none of them and keeps lateral from 805.24; 115
and every other moment of 114 are identical.

---

## 9. Dynamic tuning and the gas interceptor

### 9.1 `opendbc/sunnypilot/car/honda/dynamic_tuning.py` (new, `HondaDynamicTuner`)

This is self-learning longitudinal tuning, ported from MVL's `ACURA_MDX_3G` dynamic branch and restructured. It is longitudinal only; MVL's lateral `latFactors` were not ported. It is off by default.

**Channels**

| channel | what it learns | how it is applied | limits |
|---|---|---|---|
| pedal | **retired (2026-10).** Nothing. `observe_pedal()` counts, per drive-mode slot, the steady-pedal samples an offline plant fit could use | logged only | - |
| brake | a gain on the brake command, stored as an offset | `brake_gain()` in `CarController` | a PID with `BRAKE_KI = 0.5`, `BRAKE_POS_LIMIT = 0.6`, `BRAKE_NEG_LIMIT = 0.15` (so 0.85x to 1.60x). Frozen below `BRAKE_LEARN_MIN_SPEED = 1.0` m/s, and faded linearly to 1.0 below that speed |
| aero | **retired (2026-10).** Nothing | `wind_scale()` returns 1.0 | - |
| pitch | nothing learned. A feedforward of `sin(pitch) * g`, from `CC.orientationNED[1]` through `PITCH_RC = 0.5` s | added to the accel target; the filtered pitch also sets the soft final stop's grade-aware cap, through `filtered_pitch()` (7.8) | `PITCH_ACCEL_LIMIT = 1.5`. Faded out between 5 and 2 m/s, active only in `LongControlState.pid`, and decays out after `PITCH_STALE_FRAMES = 100` frames without a pose |

**Why the pedal and aero learners were retired** (2026-10, routes 9f..103, replaying the unmodified tuner over the logs):

- The pedal learner could not persist anything. The live gain moved `2e-4 * err` per admitted sample, the persisted estimate `3e-4` of the difference per sample, and each ignition reset live to persisted, so each drive's progress was thrown away. Admitted pedal time was about 1.6% of engaged time; six weeks moved the persisted gains from `[1,1,1,1.004,1.004,1]` to `[1,1,.996,.996,.999,.993]`.
- It would have learned the wrong way anyway. Its gate read `actuators.accel`, which includes openpilot's own integrator, so at 12-30 m/s it admitted frames where the integrator had lifted a ~0.36 target over the 0.4 gate and read under-delivery (+0.04 to +0.12) where the car over-delivers (-0.19 to -0.33 gated on the planner's target).
- The aero scale was a random walk: median 0.37 of its 0.7-1.5 range inside a drive, rails hit on b0/c8/ce (1.5) and de/fd (0.7), drift uncorrelated with speed mix, and it also scaled the brake-side credit. Under the v2 gas law it would lose its lever on the cruise pedal and rail.

The fix for the gas side is the measured law in 9.2, not a better learner. `HondaDynPedalGain0`-`5` and `HondaDynWindFactor` are no longer registered, read or written; a device keeps the old files on disk, unread.

**Per-mode data.** `update_state()` counts engaged seconds above `MODE_MOVING_SPEED = 1.0` m/s per slot (D, ECON, S; `elesys_gas.drive_mode_slot`), and `observe_pedal()` counts samples that pass `_learn_ok()` without the reference-mode gate plus the audit's steady-pedal rule: speed at least `ADMIT_MIN_SPEED = 3.0` m/s, a fresh pose with `|pitch| < 0.08` rad, and the last `ADMIT_WINDOW = 50` interceptor commands (1 s) all inside 0.02-0.9 with a standard deviation under 0.008. The seconds persist as running totals, `HondaDynModeSecD`/`ECON`/`S`. Those two counters run only with `HondaDynamicTuningEnabled` on, while the gas law runs either way; the sunnylink texts say so, so an owner who turns the tuner off knows the engaged ECON/S tally stops. Over the 168 routes to 2026-10-03 (learnaudit) ECON had 7.8 min moving and 1.5 min engaged, S 19.0 and 3.8: too little engaged data to fit anything, so this only collects.

**All moving time per mode (batch 3, learnaudit G5).** The engaged counter misses where ECON and S data actually comes from, manual driving (on route 115 it read `[653.5, 0, 0]`). So `update_state()` also counts every moving second above `MODE_MOVING_SPEED` per slot, engaged or not, into `mode_moving`, logged as `modemov=[D,ECON,S]`. Logging only: it is never persisted (the `HondaDynModeSec*` totals and the UI's "engaged seconds" keep their meaning) and nothing reads it. It runs with the tuner on, and in logging mode (below) with it off.

**Logging mode (batch 3, learnaudit B1).** On the Elesys Accord with the interceptor and openpilot longitudinal (`self.logging`: the shadow learners could be built), the tuner runs its logging with the toggle OFF too: the shadow learners (9.3), the pitch filter and plant model they read, the `modemov` counter, and the `hondadyn` line every 5 s marked `tuner=0`. Every live part stays behind the toggle: `update_state()` still returns a pitch feedforward of exactly 0.0, `brake_gain()` exactly 1.0, `filtered_pitch()` None, the soft stop, the stopping debounce and the release limiter are not built, and nothing is written to Params (no writer thread). Never in stock ACC mode: openpilot longitudinal is off there, so the tuner does not apply.

**Under brake law v2 (batch 3)** the brake channel was held at 1.0; that went with the law in round 5 (7.10).

**Rules that must not be relaxed.** The module header lists four:

1. Nothing learns during transients.
2. Only settled, off-rail values are persisted. The persisted value is a long EMA (`CONVERGED_TAU = 3e-4`) that advances only while the live value is off its clamps.
3. Every loaded value is re-clamped.
4. Limits are chosen for products. The brake gain and the brake rise rate multiply, so the ceiling is chosen against their product.

**Admission.** Each learner accepts a sample only when these conditions hold:

- **Plant-lag model (now only the per-mode data counter).** The retired pedal and aero learners compared `aEgo` against a first-order model of the plant's lag (`PLANT_TAU = 0.30` s), resetting whenever the filtered ramp rate exceeds `LEARN_MAX_JERK = 0.5` m/s^3 and waiting `SETTLE_FRAMES = 100`. That dwell now gates only `observe_pedal()`; `accel_ref`/`accel_error` are still logged.
- **Steady-target dwell (brake).** The brake channel keeps the old dwell (`STEADY_SETTLE_FRAMES = 150`, `STEADY_SETTLE_TOLERANCE = 0.20`). It is an integrator, and ramp samples rail it.
- **Minimum command.** Below `LEARN_MIN_CMD = 0.4` m/s^2 the gain cannot be identified.
- **Driver and state.** Learning is off while gas or brake is pressed, during `stockAeb`, and outside the PID state.
- **Drive mode.** The brake learner learns only in the reference mode: `LEARN_GEARS = ("drive",)`, `LEARN_ECON = (False,)`. An unknown gear still learns, so other Nidec cars are not locked out.

**Params** (`_PARAM_SPEC`, re-clamped on load):

- `HondaDynBrakeGain`: default **0.0**, range -0.15 to 0.6. It is an offset, so turning the toggle on changes nothing until something has been learned.
- `HondaDynModeSecD`, `HondaDynModeSecECON`, `HondaDynModeSecS`: default 0.0, range 0 to 1e9. Running totals of engaged seconds, written as loaded + this drive; counters, so rule 2 does not apply to them.

The enable key is `HondaDynamicTuningEnabled`. The values are read once in `__init__`. A background `_ParamWriter` thread writes them every `PERSIST_INTERVAL = 6000` frames (60 s), so the control loop never waits on disk. `Params` is imported lazily, so opendbc still imports without openpilot.

**Saving at the end of a drive (batch 3, learnaudit B1).** The 60 s cadence lost up to the last minute of every drive. Now `persist()` also writes the per-drive COUNTERS (`HondaDynModeSec*`) on the frame after every disengage (`update_state()` sees `longActive` fall), and `flush_at_exit()` writes them once more when card exits at ignition-off: synchronously (`_ParamWriter.write_now()`: it waits up to 1 s for a batch in flight, writes anything still queued with these values over it, writes last, and the writer thread then skips whatever it held), followed by the shadow learners' last line and a last `hondadyn` line. **The learned brake gain is not in those two writes** (fix round 1): `HondaDynBrakeGain` is what the next drive starts from, so it keeps exactly the 60 s cadence it had - the logging fixes change no actuation, not even the next drive's. A queued 60 s batch is still written at exit, as the thread would have. manager stops card with SIGINT, and card runs as a multiprocessing child, which leaves through `os._exit()`: `atexit` does not run there, multiprocessing's own finalizers do, so `_register_exit_flush()` registers both (through a weak reference; the flush runs once) and only on the device's real `Params`, never on a test's or a replay's stand-in.

**Telemetry.** Every `LOG_INTERVAL = 500` frames (5 s) it writes one `carlog.info` line tagged `hondadyn`, with these fields: `gaslaw=` (`v1`/`v2` on this car, `nidec` on another), `slot=` (`D`/`ECON`/`S`), `modesec=[D,ECON,S]` (engaged moving seconds this drive), `modeadm=[D,ECON,S]` (admitted steady-pedal samples this drive, 50 Hz), `modetot=[D,ECON,S]` (the persisted running totals), `brake=` and `brakec=` (both shown as gains, `1.0 + offset`, since opendbc `ff9f3211`), `pitch=`, `settle=`, `settles=`, `eng=`, `aref=`, `aerr=`, `stale=`, `werr=`, `gear=`, `econ=`, `modeok=`. `pedal=`, `pedalc=` and `wind=` went with their learners (2026-10). Since batch 3 also `modemov=[D,ECON,S]` (all moving seconds this drive, manual included), `tuner=` (1 = the live parts on; 0 = logging mode, where the brake fields are the stock 1.000), and the build: `pump=` (`v5`/`v6`, CarParamsSP flag 16), `blaw=` (`v1`/`v2`, flag 32), `commit=` (Params `GitCommit`, 9 characters). The comment in `dynamic_tuning.py` that called 0x37C bit 48 a momentary ECON button is corrected (it is a copy of the ECON state about 40 ms later), and so are its S figures (148 s counted sunny_logs only; S runs 1.1-3.8x D's rpm per km/h, not 1.4-2.5x). card forwards `carlog` to cloudlog, so the lines come back in a route's `logMessage`. The owner parses them with `S:/OP/sunny_logs/parse_hondadyn.py`, which is outside the repo.

**Known issues recorded in the code:**

- The pitch fade band (2-5 m/s) lies inside the PID state. On a stop approach the grade term is handed back to openpilot's integrator faster than the integrator can follow: modelled shortfall 0.115 m/s^2 on a 4% downhill, 0.249 on a 10% one. This was left alone on purpose until there is road data.
- Two comments give 0.8 m/s as the speed where stopping begins: the pitch-fade `KNOWN ISSUE` paragraph and the brake-fade comment above `brake_gain()`'s return. They used to cite `vEgoStopping`; since the 2026-09 merge they cite `stopping_tune.py`, where the value now lives (10.1).

**Removed history**, noted here so nobody restores it by accident: the PCM blend was deleted in opendbc `aa73e60a` / sunnypilot `cb4e0c34b`. That covered `HondaDynamicPcmBlendEnabled` and the learned `HondaDynGasFactor`, `HondaDynGasAlpha`, `HondaDynAverageFactor`, `HondaDynSpeedFactor` and `HondaDynSpeedAlpha`.

Tests: `test_dynamic_tuning.py` (sections 1-5 and 10-16; 6-8 went with the PCM blend, 9 with the aero learner) and sections 1-6, 16, 17 and 19 of `test_dynamic_tuning_integration.py` (see 12).

### 9.2 The gas law: `elesys_gas.py` and `gas_interceptor.py`

`gas_interceptor.py` is upstream sunnypilot's. The fork adds the `HONDA_ELESYS` import, constructs `self.elesys_gas = ElesysGasLaw()` in `__init__` for this car with an interceptor, calls `self.elesys_gas.update(CC, CS, gas, brake, wind_brake)` in place of upstream's line on this car, and calls `tuner.observe_pedal(CC, CS, self.gas, law)` when a tuner is passed. Every other car runs upstream's line bit for bit, with or without a tuner. Everything else is in the fork-owned `opendbc/sunnypilot/car/honda/elesys_gas.py`, and `gas_interceptor.py` re-exports `ELESYS_GAS_BP`, `ELESYS_GAS_V` and `elesys_gas_multiplier`.

**Which law.** `HondaElesysGasLawV2` (`PERSISTENT | BACKUP`, BOOL, default `"1"`) is read once, when `CarController` is built (ignition). Anything unreadable gives the default. The `hondadyn` line says which law ran (`gaslaw=`).

**v1** is the law that shipped before, bit for bit: `pedal = clip(gm1(v) * (gas - brake + 0.75 * wb), 0, 1)`, with `gm1 = elesys_gas_multiplier(v)` = `interp(v, [0, 3, 6, 10, 15, 20], [0.55, 0.85, 1.20, 1.55, 1.95, 2.75])` and the retired learned gain at exactly 1.0. The history of that curve: route `ac35d9891f`; opendbc `2905e73d`, `2bc5c4db`; `CHANGELOG-elesys.md` section 7.

**v2** (`elesys_pedal_v2`), with `gas = net/4.8` and `brake = -net/2.6` from `compute_gb_honda_elesys()` and `wb` the aero term:

- net >= 0: `pedal = off(v) + gas * gm2(v) / MODE_K[slot]`
- net < 0: `pedal = off(v) * (1 - brake / (0.75 * wb))`, which is zero at net = -1.95 wb, v1's pedal-zero point
- `gm2(v) = 4.8 / k(v)` with the measured `k` at `ELESYS_FF_BP = [0, 3, 6, 10, 15, 20, 25, 30]` m/s = `[8.73, 5.65, 6.8, 4.7, 3.4, 2.9, 2.0, 2.0]` m/s^2 per unit pedal. 0 and 3 m/s are v1's values exactly (4.8/0.55 and 4.8/0.85), and the table is interpolated in the same quantity v1 interpolates (`ELESYS_FF_GM`), so 0-3 m/s is v1's law up to the launch cap below: the same gain bit for bit, and the same pedal to within float rounding (the sum is grouped differently; tested to 1e-12).
- `off(v) = min(G0(v), 0.75 * wb * gm1(v))` with the measured cruise pedal `ELESYS_FF_G0 = [0.045, 0.045, 0.080, 0.102, 0.110, 0.122, 0.150, 0.211]`. Below about 16.9 m/s v1's offset is the smaller, so the offset, and the whole zero-crossing window, is v1's; above it the measured G0 takes over and the window is gentler (0.76 against 1.06 pedal per m/s^2 at 20 m/s).

| speed (m/s) | 3 | 6 | 10 | 15 | 20 | 25 | 30 |
|---|---|---|---|---|---|---|---|
| measured k | 8.7±0.6 | 6.8±0.5 | 4.7±0.4 | 3.39±0.09 | 2.89±0.09 | 2.03±0.21 | 1.98±0.15 |
| v1's k = 4.8/gm1 | 5.65 | 4.00 | 3.10 | 2.46 | 1.75 | 1.75 | 1.75 |
| measured cruise pedal G0 | 0.045 | 0.080 | 0.102 | 0.110 | 0.122 | 0.150 | 0.211 |
| v1's offset 0.75·wb·gm1 | 0.003 | 0.017 | 0.043 | 0.087 | 0.169 | 0.216 | 0.263 |

Fit: 51 routes (99..103), engaged, D, pedal steady for 1 s and lagged 0.4 s, response `aEgo + g·sin(pitch)`, route-jackknife errors; re-derived independently to within 0.01. Out of sample (fitted on 99..d9, tested on da..103) the refit law cut the integrator-load RMS from 0.214 to 0.123 m/s^2.

**Why these choices** (the skeptic review, which overrode the audit):

- v1 gave 1.4-1.7x too much pedal per m/s^2 at 6-20 m/s, and the car showed it: in demand episodes aEgo ran 1.34-1.42 against aTarget 0.95-1.15 for the first 2 s, then openpilot's integrator fell to -0.1 to -0.3. The pedal for 1.0 m/s^2 at 20 m/s goes 0.742 -> 0.467.
- The measured G0 is NOT used below ~16.9 m/s. It is 2-6x v1's there, and raising only the offset squeezes the pedal's fall to 0 into a 0.04-0.12 m/s^2 window, 1.3-4.8x steeper than today: a likely surge or limit cycle in stop-and-go. Raise it only together with moving the brake-on point to the measured coast deceleration (the brake work).
- The k table covers pedal up to about 0.25-0.28. Above that the car delivers more than the line (kickdown), so high-pedal predictions are extrapolations.
- It will feel softer pulling away from a roll at 6-20 m/s, because it removes v1's onset over-delivery. Max accel was not raised: the 1.6 m/s^2 clip only bound during launches that were already overshooting.

**Drive modes.** `mode_slot(CS)`: `S` if the gear is sport, else `ECON` if `CS.econ_on`, else `D` (unknown, P, R and N count as D). `MODE_K = {"D": 1.0, "ECON": 1.0, "S": 1.0}` multiplies k in v2, so nothing changes yet; ECON's evidence (0.61x achieved, slope 0.72-0.84x) is 70 s of mostly manual driving, and the review's advice for any prior is >= 0.85, not 1/1.3. A slot change crossfades the pedal linearly over `CROSSFADE_FRAMES = 100` interceptor frames (2 s), starting from the current blend if it changes again mid-fade, and while every multiplier is equal the output is exactly the single-slot law.

**Never raises.** `ElesysGasLaw.update()` runs inside `CarController.update()`. NaN, None and missing inputs fall back to v1 and then to 0.0; the result is always a finite float in [0, 1], and 0.0 when not `longActive`.

**Brake law v2's window (batch 3)** went with the law in round 5 (7.10): `_v2()` is `elesys_pedal_v2()` on every frame again.

**The launch cap (2026-10-04, owner decision 8, A_synth L2a).** Route 115 t 511 asked 1.6-2.0 m/s^2 from a stop and
got 2.4-2.7 (aEgo), with openpilot's integrator wound down to -0.8. Below `LAUNCH_CAP_V_END` = 6 m/s, v2 is now also
capped: `pedal = min(v2, LAUNCH_CAP_P0 + net / K(v))`, `P0` 0.08, `K` = 13.0 / 12.0 / 6.8 at 0 / 3 / 6 m/s
(`LAUNCH_CAP_BP`, `LAUNCH_CAP_K`; the 6.8 is the table's own k at 6 m/s), `k_mult` scaling `K` as it scales k. The car
answers the pedal like a hinge at launch speeds: creep up to ~0.08-0.09 pedal, then ~13 m/s^2 per unit at 1.5-3 m/s,
10.7 at 3-4.5 and 8.1 at 4.5-6 (48 routes, 099..115); v1's line through zero under-asks below ~1 m/s^2 and over-asks
above. The cap only binds above that crossing, never below net 0.85, from net 2.13 at 0 m/s, 1.70 at 0.5, 1.42 at 1.0,
1.22 at 1.5, 1.07 at 2, 0.86 at 3, 1.15 at 4, 2.01 at 5 and 3.7 at 5.5: demands below ~0.85 m/s^2 get v2's pedal
exactly, but above that, at 1-5 m/s, it can bind behind a lead too (19.5% of the replayed lead frames below 6 m/s, from
a 0.84 command up; 27.6% with no lead). It never asks for more than v2; at net <= 0 it is P0, above v2's offset, so the pedal-zero window and the brake-on point
do not move; it is continuous in speed and net and identical to v2 from 6 m/s. The single-number alternative (3 m/s
k = 8.7) was rejected: it cuts every demand and made launches behind a lead under-deliver (0.94 of target) and 0.11 s
slower to 6 m/s. Offline closed-loop replay (openpilot's PI, the fitted hinge as the plant, each launch's logged target
and grade) of the 13 clean engaged launches from a stop on 099..115 (6 with no lead, 7 behind one): achieved / target
at 0.5-4.5 m/s 1.18 -> 1.04 (no lead) and 1.11 -> 1.02 (lead), peak aEgo 2.32 -> 1.85 and 1.76 -> 1.55 m/s^2, lowest
integrator -0.44 -> -0.04, time to 6 m/s 4.04 -> 3.97 s and 5.21 -> 5.18 s; 115 t 511: 2.11 -> 1.88 m/s^2 against
1.70. Replaying 115 and 10f through the real `CarInterface`, old tree against new, the only interceptor commands that
change are 102 (115, its one launch) and 382 (10f, three episodes) frames, all at 1.3-5.0 m/s, all lower. Caution, not from the cap: in
the same model v2 itself reaches 6 m/s about 0.5 s later than v1 did, from its 3-6 m/s segment.

What the cap does NOT do, and what still needs the owner:
* **It reduces the over-delivery; it does not remove it.** Below ~1.4 m/s a normal 1.1-1.6 m/s^2 launch demand is not
  capped at all (115 t 511 at 1.0 m/s asked 1.13 and got 1.62; the cap leaves that frame alone). The model still has
  115 t 511 at 1.88 against 1.70 asked, peak 2.18 (2.82 before). The fitted hinge for 0.3-1.5 m/s has a 0.88 m/s^2
  intercept and P0 0.12, which P0 0.08 / K 13 does not represent.
* **The evidence is in-sample.** The closed-loop plant is the 48-route hinge that `LAUNCH_CAP_K` came from, only 1 of
  the 13 launches ran v2, and 10f / 110-113 have no clean no-lead launch (10f t 302 had a lead). The next drives'
  launches, and the shadow launch ratio at 0.5-3 m/s (9.3), are the real check before P0 or K is tuned. First-drive
  check: a launch behind a car pulling away must not feel sluggish.
* **Scope: 0-6 m/s, not 0-3.** Owner decision 8 named the 0-3 m/s segment, "continuous with the v2 table above 3 m/s".
  A cap ending at 3 m/s would step off v2 there (at 3 m/s and net 2.0 the capped pedal is 0.247 against v2's 0.354),
  so it runs to `LAUNCH_CAP_V_END` = 6 m/s and meets v2 on the table's own k: at 4.5 m/s, net 2.0, 0.324 -> 0.293; at
  5 m/s, net 2.5, 0.393 -> 0.373; nothing from ~5.3 m/s. It applies to any pull-away below 6 m/s above the line (a
  roundabout exit), not only from a stop. The replays change `0x200` up to 3.81 m/s (115) and 4.99 m/s (10f). The
  single-number alternative (A_synth L2a option (a), k 8.7 at 3 m/s) was rejected for the reason above. **Needs the
  owner's sign-off.**
* **v2 only.** `ElesysGasLaw.update()` sends v1 when `HondaElesysGasLawV2` is off, and falls back to v1 if v2 raises
  (it does not: `elesys_pedal_v2` catches everything and returns 0.0). v1 is kept as the exact pre-2026-10 law to go
  back to, so it is not capped: switching the measured law off brings back the uncapped launch.

Tests: `test_elesys_gas.py` (38 tests: golden pedal per breakpoint for net in {-0.5, -0.1, 0, 0.5, 1, 2}, 3 m/s with the cap; continuity at 0 and at the pedal-zero point; monotonic in net; zero at and below the brake-on point; v2 equal to v1 at or below 3 m/s up to the launch cap; `TestLaunchCap` (never more pedal than before, identical from 6 m/s, every demand up to 0.8 m/s^2 and the braking side untouched, binding on a launch, continuity in speed and net, `k_mult`, route 115's launch frames); the negative-branch slope never steeper than v1's and equal below 16.8 m/s; the crossfade bound; the slot rule; the param read; non-Elesys bit identity); integration sections 16 (both laws through the real `CarController`, the brake command identical under both) and 17 (`update()` never raises, 0x1FA on every even frame); `TestElesysGasMultiplier` in `test_elesys.py` for the v1 curve.

### 9.3 Shadow learners (2026-10-04): measured and logged, applied to nothing

Owner decision 9 after routes 114/115: the three learners A_synth proposed (L1 brake table, L2b launch multiplier,
L3 speed-split lateral factor) run in **shadow** first. They compute what they would learn and write it into the
route; nothing actuated reads them. No param, no capnp field, no setting: the longitudinal pair rides on the gas
interceptor and openpilot longitudinal, the lateral one on the car. Up to batch 2 the longitudinal pair also needed
`HondaDynamicTuningEnabled` (default off; it was on for 115 and 10f); since batch 3 it runs with the toggle off too,
in the tuner's logging mode (9.1), and every line says which (`tuner=`).

**Where.** `opendbc/sunnypilot/car/honda/shadow_learn.py` (`HondaShadowLearners`, imported and built by
`HondaDynamicTuner._build_shadow()` under a try, only on `HONDA_ELESYS` with the interceptor and openpilot longitudinal; since batch 3 with the toggle on or off) and `openpilot/sunnypilot/selfdrive/locationd/lat_speed_split.py`
(`LatSpeedSplitShadow`, built by `TorqueEstimator` only on `HONDA_ELESYS`, torque tuning, not the decimated estimator).
`carcontroller.py` is not touched: the tuner records copies of the frame's brake fraction (`brake_gain()`) and
interceptor command (`observe_pedal()`), and `update_wind()` - the last tuner call of the 50 Hz gas/brake block, kept
when the aero learner was retired - hands them to the shadow. `torqued.py` carries four marked places for it (its import
guarded: if `lat_speed_split.py` cannot be imported, torqued runs as upstream); since round 8 three more for v2 (the
driver's torque from each `carState`, the shadow's own SubMaster and its `observe()` in `main()`) and torqued's window
handed to `tick()`. `carstate_ext.py` records 0x17C
`PEDAL_GAS` as `pcm_pedal_gas` for the launch gate.

**The longitudinal signal.** Achieved net accel `aEgo + g*sin(pitch)` (gravity removed with the tuner's filtered
pitch) against the command the law was given (`actuators.accel` + the pitch term) through the tuner's 0.3 s plant
model (`cmd_ref`). **This departs from A_synth L1**, which learns from openpilot's integrator (`uiAccelCmd`) on
brake frames: the integrator is not visible in card. In a steady state the two are the same number (the integrator
settles where achieved = target, so the error is -(uiAccelCmd + the P term)); off it they are not, so this was
checked rather than assumed. A one-off replay of 115 and 10f through the real `CarInterface` (round-1 review, scratch
`integ/xcheck.py`) took every brake sample the shadow admitted and the `controlsState` at or before it:

| cell (>= 5 s, both routes) | n | `be` (plant error) | -`uiAccelCmd` |
|---|---|---|---|
| 1-5 m/s, > 100 counts | 5.0 s | -0.23 | -0.21 |
| 5-10 m/s, > 100 | 8.1 s | -0.29 | -0.11 |
| 10-15 m/s, > 100 | 8.1 s | -0.03 | +0.11 |
| 15-20 m/s, <= 60 | 12.3 s | -0.00 | +0.05 |
| 20-25 m/s, <= 60 | 69.3 s | +0.01 | +0.03 |
| 20-25 m/s, 60-100 | 5.6 s | +0.18 | +0.24 |
| > 25 m/s, <= 60 | 27.6 s | +0.14 | +0.12 |
| > 25 m/s, 60-100 | 6.5 s | +0.26 | +0.30 |

(`upAccelCmd` is 0 throughout, so -(ui + up) = -ui.) Above 20 m/s, where the only would-apply corrections are, they
agree within 0.06 m/s^2 and per sample they correlate 0.5-0.9. Below 15 m/s the plant error reads more over-braking
than the integrator, by up to 0.18 (5-10 m/s, firm), and at 10-15 m/s firm the two differ in sign (-0.03 floored to 0
against +0.11, which would ask for 0.11 more brake). So: **not validated below 15 m/s**. Before any brake cell is
applied, its value must be read against the integrator, not taken from `be` alone.

**Gates.** All: a clean second (`CLEAN_HOLD`, 50 consecutive 50 Hz frames) of `longActive`, PID state, D with ECON
off (`mode_ok`), no driver pedal, no stock AEB and a fresh pose - not just the current frame: after the driver lifts
off the gas, aEgo carries the throttle for 0.38 s median, 0.76 s p90 on 10f (56 releases), and the first version took
a +1.17 m/s^2 "coast" at 10f t 2425.8 from exactly that - and the pedal/brake command held for 0.4 s (`WINDOW`, the
measured 0.25-0.40 s plant delay).

**The launch's clean second runs through the stop (batch 3, learnaudit G6).** The brake and coast tables keep the
second of PID. The launch needs the same engaged, pedal-free, AEB-free, D, fresh-pose run in any control state, so a
launch from an openpilot-held stop is sampled from its first wheel motion (in practice from 0.5 m/s, the band's
floor, once the pedal has been on for the 0.4 s window). Before, the window opened a second after control left the
stopping state, with the car already at 0.61-1.07 m/s (115 t 511; 10f t 303 and t 2408), so the 0.5-1 m/s slice,
where the overshoot is worst, was never sampled. A driver's pedal still costs the launch a clean second. On the 115
replay it adds 4 launch samples (134 -> 138, all from that slice); nothing else in the line changes.

| table | admitted when | cells | would apply |
|---|---|---|---|
| brake (L1) | brake 4+ counts, steady within 20 counts and one band over the window, no pedal, plant-model ramp <= 0.5 m/s^3 for 0.2 s, \|pitch\| < 2 deg, v >= 1 | speed 1-5-10-15-20-25-up m/s x command <=60 / 60-100 / >100 counts | per cell `-mean error`, floored at 0 (never less braking than the law), at most -0.5 m/s^2, only from 5 s of samples, faded in over 1-2 m/s |
| coast | neither pedal nor brake over the window, same ramp/pitch gates, v >= 3 | speed bands | the drag / brake-on-point term (logged as measured coast accel and its error) |
| launch (L2b) | pedal >= 0.01 over the window and the PCM seeing it (0x17C `PEDAL_GAS` >= 1 over the window), no brake, 0.5-6 m/s, lagged command >= 0.3, ramp <= 1.0 m/s^3 for 0.2 s, \|pitch\| < 4 deg; no lead (`hudControl.leadVisible`) - behind a lead into separate sums, logged, never in the multiplier | 0.5-3 and 3-6 m/s | pooled `1 / (achieved/commanded)` of the no-lead launches, bounded 0.6-1.0 (pedal only ever taken away), only from 2 s of samples |

The jerk hold is 0.2 s rather than the tuner's 1 s `SETTLE_FRAMES`: on 115 that takes the brake table from 18.5 s to
27 s of the 107 s of brake-commanded PID time with cell means within 0.03 m/s^2 (27.2 s with the clean second too).
The launch pitch limit is wider because 115's only launch was on a -2.7 deg downhill (see below).

**The brake counts are the law's.** `brake_frac` is recorded in `brake_gain()`, before the live scalar gain, the soft
final stop ceiling and the 32-count release limiter (`carcontroller.py`), so `bcb` is not the 0x1FA count on the wire
and the error is the law's error at the gain then in force - which `bgain` logs (the mean gain over the brake
samples: 0.99-1.00 on the six routes). A table applied on top of the gain would partly double-count what the gain
corrects; read it against `bgain`.

**Lateral.** Every point torqued accepts (its own gates, so the low half is 15-19.4 m/s) is filed below or above
70 km/h. Each half keeps six running moments; torqued's total-least-squares fit is the smallest eigenvector of their
3x3 Gram matrix, so the fit is torqued's exactly (`test_lat_speed_split.py` checks it against `estimate_params()`) and
the sums add across drives. Reported raw, clipped to torqued's window around the prior, with torqued's validity rule.

**Lateral v2 (round 8, 2026-10-20, proposal 7; `latsplit v=2`).** The drives of 17-19 Oct showed that what pinned
torqued's factor at its floor was mostly a hand resting on the wheel, not town speed, and v1 mixed hands into both of
its halves. v2 files each point torqued accepts into one of nine cells, hands x speed, and still applies nothing:

- **Hands**: the largest |`carState.steeringTorque`| over torqued's own override window (the samples torqued received
  from t - `MIN_ENGAGE_BUFFER` to t + lag, on its lag-shifted clock; `add_driver_torque()` from `handle_log()`): `off`
  under 300 counts, `on` 300-1199 (a hand torqued's `steeringPressed` gate let through), `hard` 1200 and up, still not
  pressed (this car's `STEER_THRESHOLD` is 600, debounced). **Speed**: 54-70, 70-90 and 90+ km/h; torqued's `MIN_VEL`
  (15 m/s) is the floor of the first band.
- Each cell keeps the TLS moments and steer bins as v1, and six sums of the controller's integrator
  (`controlsState.lateralControlState.torqueState.i`) and the roll term (`vehicleParameters.roll` x g, the controller's
  `roll_compensation`), taken only while both are fresh (`CTL_FRESH_FRAMES` 2 torqued loops, `ROLL_FRESH_FRAMES` 10) and
  the controller is steering: per cell the mean offset, the mean roll term and the slope of one on the other. Every sum
  combines exactly across drives.
- The controller inputs come from a second, non-blocking SubMaster (`make_submaster()`, `observe()` with `update(0)`)
  that torqued's `main()` creates only when the shadow exists. torqued's own SubMaster, its `all_checks()` and its
  timing are upstream's (`test_main_keeps_torqueds_submaster_and_wires_the_shadow`).
- **Proven to apply nothing**: replayed through the real torqued over 7 drives (`13d`-`143`, the chain carried through
  `Params` from `115`), `lateralTorqueParameters` was identical with and without the shadow on every publish, and in
  the synthetic test.
- **What it reads, v2 replayed over `13d`, `141`, `142` and `143`, pooled** (scratch `r8/replayB_report.txt`):

  | band | hands-off share | factor off / on | integrator mean (on) | roll term (on) | integrator-on-roll slope (on) |
  |---|---|---|---|---|---|
  | 54-70 km/h | 11.5% | 1.261 / 0.741 | +0.024 | +0.41 | +0.21 |
  | 70-90 km/h | 5.1% | 1.236 / 1.052 | -0.014 | +0.34 | +0.13 |
  | 90+ km/h | 6.2% | - (88 points) / 1.352 | +0.24 | +0.64 | +0.08 |

  No point fell in `hard`. The hands-off share is higher than the analysis's 3-9% because torqued sees `carState` at
  20 Hz and misses short touches between samples. It decides, with more drives, whether a hands gate for torqued, a
  speed-split factor or a roll feed-forward gain is worth building; none is.

**Log.** `hondashadow` (card, via carlog): once a minute while anything was admitted and at every disengage; fields
`bn` `be` `bsd` `bcorr` `bcb` `bacc` (18 cells, speed-major), `cn` `cacc` `cerr` (6 bands), `ln` `lra` `lrr` `lratio`
`lep` `lmult` (no-lead launches), `lnl` `lral` `lrrl` `lratiol` `lepl` (behind a lead) and `bgain`. `latsplit`
(torqued, via cloudlog): once a minute while points arrive; v=1 (until round 8): `n` `fac` `clip` `off` `fric` `cal`
`valid` per half, `mom_lo`/`mom_hi`, `bins_lo`/`bins_hi`, and torqued's own factor as `main`; v=2 (round 8):
`commit=` (`GitCommit`) `board=` (`EpsLkasBoardVersion`, written by card when it decodes the drive's first `0x707`)
`prior=` `win=` (torqued's window) `main=` `bands=` `hands=` `n=` `nc=` `nx=` (points with no driver-torque sample)
`fac=` for the nine cells, then `m_<hands>_<band>=` (moments), `b_...=` (steer bins) and `c_...=` (controller sums) for
each cell that has a point; about 0.9-1 KB a line in the replays. Both are drive totals. `hondashadow` is
about 0.9 KB a line, so 1-2 KB a minute depending on how often the drive disengages (a line at each; replays: 0.7 on
115, 1.2 on 10f, 1.4 on 113); `hondadyn` is about 4 KB a minute. Read them with
`python openpilot/sunnypilot/tools/shadow_learn_report.py <route> [<route> ...]`, which also combines routes. It runs in
the repo's own venv: without pandas it reads the route's rlogs instead of `parquet/logMessage.parquet`. Routes before
batch 2 (115 and earlier) have no lines; it says so.

**What a line was measured on (batch 3, learnaudit B1).** `LOG_VERSION` 2: every `hondashadow` line starts
`v=2 commit= gaslaw= cap= pump= blaw= tuner=` (`shadow_learn.BUILD_KEYS`): the commit (Params `GitCommit`, which
manager writes at every start), the gas law (`v1`/`v2`, from the interceptor path), `cap` (1 when the law has the launch
cap, i.e. v2), the pump rule (`v5`, or `c1b` with CarParamsSP flag 64 - `pump_rule_tag()`; lines of 2026-10-05/06 say `v6`, the retired C1, flag 16), the brake law (`v1` - `brake_law_tag()`; lines of
2026-10-05 to 2026-10-09 can say `v2`, the retired brake law v2, flag 32, 7.10) and whether Dynamic Tuning's live
parts were on. The flag value is the fixed
contract (`values_ext.py` `ELESYS_PUMP_C1B = 64`; 16 and 32 reserved); the tag falls back to that literal
so they never depend on an import. A line is also attempted when card exits at ignition-off (`flush()`, 9.1); manager
stops loggerd in the same pass, so whether it reaches the route is unproven (the replays have no loggerd) - check the
first drive for a last `hondashadow`/`hondadyn` line within ~1 s of the route's end. What is certain is the persisted
counters (9.1). **The report never pools different builds**: routes combine only with
routes equal in all six tags; lines from before the tags (v=1, the batch-2 replay dumps) are their own group,
"untagged"; with more than one group it prints `NOT POOLED` and a combined section per group. A launch measured
without the cap (`cap=0`, or untagged) is printed but its multiplier is marked DISCARDED: applied on a capped car it
would correct twice. The lateral split pooled as before until round 8 - `latsplit` v=1 lines are not tagged and still
pool with each other. Since round 8 `latsplit` v=2 lines carry their own build (`commit`, the board's `0x707` hash and
torqued's factor window, which is what moves the points torqued learns from) and the report pools them only within one
(commit, board, window), printing `LATERAL NOT POOLED` when the routes come from more than one.

**Proven not to actuate.** Replaying routes 115, 10f and 113 through the real `CarInterface` with the shadow and with
`dynamic_tuning.py` at HEAD and no `shadow_learn.py` (one opendbc snapshot each): every CAN frame of 249,474 / 713,406
/ 92,552 identical, and the actuator outputs. torqued replayed over 115, 10f, 113, 110, 112 and 114 against the
reference tree: every `lateralTorqueParameters` field identical. The shadow did switch itself off once in a replay
(a harness bug fed it a wrong argument) and the CAN stayed identical, which is the never-raise path working. After the
round-1 review fixes (the clean second, the lead split, the 0x17C confirmation, `bgain`, the guarded imports), 115 and
10f replayed again against the pre-batch tree: the only differing frames are the launch cap's `0x200` (102 / 380, all
below 5 m/s, all less pedal) and `0x500` `OP_STATE` on the key-off frames (2 / 4); `CarState` identical bar the
parked key-off `steerFault*`; 110, 112, 113 and 114 replayed with no exception and the shadow on to the end; torqued
on 115 again identical in every field.

**Cost.** Longitudinal: 11-13 us per 50 Hz call on the PC (CarController.update about 280 us per 100 Hz frame), about
2% of card's controller time. Lateral: 3 us per point (at most 20 a second) and under 1 us per loop.

**What it would have learned** (replays, after the round-1 review fixes, of 115 on this code and of 10f/110/112/113/114,
which ran older builds, so those numbers describe the law that ran then):

* *Brake.* Below 20 m/s the law over-brakes slightly or is on target (115: -0.33 at 5-10 m/s > 100 counts, -0.17 at
  10-15 m/s 60-100; all six: -0.29 at 5-10 m/s > 100, 12 s): floored, nothing applied - but see the integrator check
  above, which does not confirm the sign at 10-15 m/s. Above 20 m/s (10f only) it under-brakes at light and mid
  commands: +0.14 (>25 m/s, <= 60 counts, 28 s), +0.26 (>25, 60-100), +0.18 (20-25, 60-100) - would apply -0.14 / -0.26
  / -0.18, and the integrator agrees there. Per count beyond coast that is 0.69-0.80x the 2.6/256 law, A_learning's
  0.5-0.8x; the light-command weakness only turns into an error at highway speed because below it the law's small
  coast credit (`wind_brake`) hides it.
* *Coast.* -0.26 to -0.35 m/s^2 at 10-36 m/s (A_learning: -0.26 to -0.39), 0.07-0.17 more than commanded. (The first
  version had a +1.17 "coast" at 10-15 m/s on 10f: the driver's throttle after a release.)
* *Launch.* With the lead split and the 0x17C confirmation, the six routes hold ONE no-lead launch: 115 t 511, 1.43 /
  1.35 (0.5-3 / 3-6 m/s, 2.7 s of samples) -> multiplier 0.72 (0.723) - **discarded in batch 3**: 115 ran without the
  launch cap, so the number is not to be applied to a capped car (the report now marks any pre-cap launch DISCARDED,
  above). Behind a lead (logged, never used): 1.13 / 1.24 over
  five episodes (10f 1.13 / 1.29). The first version pooled both (1.25 / 1.26 -> 0.80). One launch is not evidence;
  these all ran without the launch cap (9.2), which reduces the same over-delivery, so drives with the cap measure
  what it leaves.
  **115's launch was on a -2.7 deg downhill**: gravity gave +0.46 of the 2.4-2.7 m/s^2 aEgo, so the over-delivery
  against the command was ~1.4x, not the ~1.8x aEgo suggests.
* *Lateral.* 115 below 70: 0.854 (3,805 points, 97% calibrated), above 1.004 (402). 10f below 1.306 (822), above
  2.019 (12,390, valid). All routes summed: 1.19 below, 1.85 above. The two halves separate on 115, 10f and 110; the
  TLS slope runs above A_learning's secant fits (0.54-0.76 / 1.42-1.49) because TLS is what torqued fits.

**Batch 3's logging fixes, proven not to actuate.** Replaying 115 and 10f through the real `CarInterface` (scratch
`batch3/b3replay.py`), CP_SP flags 0 (pump v5, brake law v1), in two configurations - the tuner as the route ran it
(on) and the tuner off, where the logging mode now runs the shadow - the tree with these changes against the same tree
with `dynamic_tuning.py` and `shadow_learn.py` put back to their committed source, and against `~/sp-merge`: every CAN
frame and every actuator output identical (115: 101,825 control steps, 249,474 frames; 10f: 291,185 and 713,406), in
all eight comparisons. With the tuner off the shadow ran to the end of both routes and wrote `tuner=0` lines (14 on
115, 59 on 10f); with it on, the shadow's tables are the committed code's except the launch (115: 134 -> 138 samples,
the new window); every other carlog line is unchanged.

### 9.4 The route check: `openpilot/sunnypilot/tools/brake_route_check.py` (batch 3)

`python openpilot/sunnypilot/tools/brake_route_check.py ROUTE [ROUTE ...] [--baseline ROUTE ...] [--rule v5|v6|c1b]
[--rlog] [--json OUT]` reads route folders as the owner keeps them (`S:\OP\sunny_logs\<dongle>_<route>`: parquet
with pyarrow, which the repo's venv does not have, else `raw/*rlog*` through LogReader, one process per segment; a
segment with only a qlog is used and flagged) and prints, per route and pooled over the ROUTEs (one arm of the A/B),
pump2's section-4 metrics and abort verdicts (7.2), learnaudit's B4 brake-law acceptance metrics and, since
2026-10-06, C1b's acceptance checks (c1weak section 4, below). `--baseline` is the other arm, for the comparative
criteria. Nothing is written but `--json`.

* **Which rule ran**: CarParamsSP flag 64 = `c1b`, flag 16 = `v6` (the retired C1, routes `120`/`121`), neither = `v5`
  (read from the rlog even for a parquet route: the owner's export cannot decode CarParamsSP), else the route's
  `pump=` tag, else `--rule` (a `--rule` the log contradicts is ignored, with a warning). **Which law ran**: the route's `blaw=` tag (it says what ran: while the law existed the tuner corrected it when gas
  law v2 was off; `v1` on every line since round 5), else flag 32, which is only the setting; flag 32 with `blaw=v1` reads "v1 (v2 requested, not run)". It
  replays the controller's own
  `brake_pump_hysteresis_elesys` (v5) and `brake_pump_c1b_elesys` (C1b) - its own transcription of C1b only on a tree
  without it, tested equal to the controller's - and its transcription of the retired C1 (`_c1_reference`; no tree
  has the function any more) on the logged 0x1FA commands, and checks the rule that ran against the logged pump bit (>= 99.5% of braking frames; a mismatch one frame from a burst edge counts as a
  match - the log's timestamps jitter against the controller's frame clock by that much - and the exact share is
  printed beside it). The three replays give the v5 / C1 / C1b table for the route. Each held stop shows its bursts,
  how many came more than 5 s into it, the command and the pressure DELIVERED (pump2's model: a rise arrives only
  while the motor runs); a standstill burst is outside C1/C1b's design only as a top-up - a burst on a hold already
  delivered at 100 counts or more, with no rise of more than 15 counts over it and not an application's first frame; the
  hold-build bursts (one per deadband-sized rise while under 100 delivered counts, so a light hold can take several) and
  C1b's first-frame burst are inside it (fix round 1: the old count allowed one burst per stop and flagged C1b's own light
  holds).
* **Grade**: aEgo + g * (-vD / vH) from the GPS Doppler velocity, 2 s rolling median; the r against the
  accelerometer's forward specific force is printed per route (115 0.97, 113 0.96, 10f 0.81; pump2 median 0.88).
* **Definitions the plan left open**, chosen here and fixed for both arms: steady gain is decel beyond the measured
  coasting curve (learnaudit) per 100 counts of the command 0.3 s earlier, held +-3 for 1 s; bleed is achieved minus
  the command 0.3 s earlier, by time since the last pump frame of the application, with the stretches counted; a
  rise is above the deadband from a command flat +-3 for 0.5 s, its response the decel change 0.3-0.7 s after; a
  bite is the 0.2 s mean tracking error falling more than 0.5 m/s^2 within 1 s of a pump start, against its level
  just before (a steady over-delivery is the integrator's, not a bite); an ARRIVAL is a standstill from above 3 m/s
  with openpilot engaged 6 s before (or where it last crossed 5 m/s): a brake press cancels openpilot, so a
  take-over is counted from the start of the window (fix round 1: the old count needed openpilot through the stop and
  always read 0; route 115 has three, at 214.5, 343.2 and 500.8 s); the CLEAN stops (engaged and braking through the
  stop, no pedal) carry the stop metrics; the final approach is pump2's (from where the braked approach crossed 2.5
  m/s, v >= 0.3, aEgo + g sin(pitch) - command, the median over stops and its seconds: the GPS grade is blank below
  2 m/s); the brake RMS is achieved against the command through a 0.3 s lag on steady PID braking (not learnaudit's
  model-fit "about 0.20": compare the arms); the brake-gain learner's verdict is on each drive's own change (last -
  first `brakec`), because the arms of an A/B share one stored gain - the level is printed beside it.
* **A hold** runs from the stop (engaged, >= 100 counts, no planner launch, no pedal, the wheels at zero) for as long
  as those hold, WHATEVER the wheels do after the stop, so a hold that rolls stays one hold; motion counts from 0.5 s
  in to its end, less only a trailing release (fix round 1: the old definition ended a hold on its first moving
  frame and trimmed its last 0.5 s, so the creep verdict could never trip; tested on 1 s and 3 s rolls at 0.3 m/s).
* **One deviation, from evidence: creep.** A hold MOVED (abort) when XMISSION_SPEED, |vEgo| > 0.01 m/s (vEgo at a
  standstill carries the filter's +-1e-6 residue) or WHEELS_MOVING shows it. The plan's finer checks - the radar's
  net change to a stopped lead <= 0.1 m (median of the first 2 s against the last 2 s, lead |vLead| < 0.1) and the
  camera displacement <= 0.2 m (pose velocity integrated after the first 4 s, where the estimator settles) - mark a
  hold SUSPECT ("CHECK") instead: on today's 10f the two holds where nothing moved read 0.21 and 0.27 m on the radar.
* **Verdicts**: ABORT / PASS, or n.a. when the data is too thin (the bleed bin needs 20 stretches; the gain bands 60 s
  each) or the criterion needs the other arm (`--baseline`), and MANUAL for the post-drive scan.
* **C1b ACCEPTANCE** (2026-10-06; c1weak A_synth section 4, 7.2), printed after the abort criteria for the arm against
  `--baseline`. PASS / FAIL, n.a. without a baseline or with fewer than 10 clean applications per arm, MANUAL for the
  VSA scan. The checks are about C1b against v5, so checks 1-5 judge only an arm whose routes all ran C1b, and the
  comparative ones only against a baseline whose routes all ran v5; otherwise they read n.a. with what each arm ran
  (fix round 1: the C1 routes 120/121 gave FAIL lines that were about C1). A clean application is c1weak's: at least
  1.5 s after the previous one, engaged, at 1 m/s or more, no pedal in the 0.6 s before, peaking at 12 counts or more.
  Its tracking error is c1weak's: kinematic aEgo (interpolated to the 0x1FA frame) minus the planner's
  `longitudinalPlan.aTarget` 0.3 s earlier, over 0-0.5 s and 0-1 s, only where the window is not cut by a pedal, a
  disengage or the application's end. The verdict is on c1weak's ADJUSTED effect: an OLS over both arms' applications of
  the error on the arm, log peak, v0, the target at the onset and over 0-1 s, the grade over -0.5..+1 s (GPS, gaps from
  the pitch less its route offset) and the error already there over the 0.5 s before, the arm's coefficient with a 95%
  event bootstrap within each arm (2000 resamples, fixed seed); the raw medians and means, and the same error against the
  actuator command, are printed beside it. Fix round 1: the check had used the command 0.3 s earlier and compared raw
  means, which is not what c1weak's thresholds were derived on (on 120/121 that read a median +0.04 at 0-0.5 s where
  c1weak measured +0.100); the tool now reproduces c1weak on those routes - the same 17 applications, 0-0.5 s median
  +0.100 / mean +0.154 (n 16), 0-1 s +0.106 / +0.160 (n 15) against c1weak's +0.100 / +0.154 and +0.105 / +0.158 -
  and a test holds it there when the routes are on the machine (`TestC1weakReproduced`). The decel onset is the first
  time the grade-corrected accel holds 0.1 below its pre-onset mean (0.6-0.05 s before) for 0.2 s, within 4 s, judged
  on the effect adjusted for log peak, v0 and the target (c1weak reg2). A stop's distance is read where c1weak read it,
  at the first frame under 0.15 m/s with a lead whose vLead < 0.5 and dRel < 25 m (`120` @134.7: 3.00 m, c1weak 3.0; the
  abort criterion keeps pump2's reading 1 s after the stop, 3.42 m there); its raw error is aEgo minus `aTarget` 0.3 s
  earlier over the last 3 s, compared by medians, with c1weak's last 2 s printed beside it (`120`: -0.063, c1weak -0.07);
  the decel at wheel-zero is the existing accelerometer figure of the 0.2 s before the stop. A take-over is a brake
  press with openpilot engaged 0.1 s before and a brake command in the last second, per minute of engaged braking. Pump
  time per engaged hour is the logged pump bit's; a late re-pump is a pump start more than 5 s into a held stop.

**Today's rule, the baseline (10f, 113, 115; pump v5, brake law v1):** the replay matches the logged bit on 99.83 /
99.69 / 99.54% of braking frames exactly (99.88 / 99.84 / 99.77% with the one-frame edge tolerance), and both replays reproduce pump2's section-3 table to the decimal (10f: 123.9 s /
141 starts today, 126.0 / 111 under C1; 113: 29.1 / 32 and 31.6 / 30; 115: 50.1 / 54 and 48.7 / 46). Pooled: 24.9
starts per braking minute; longest moving pump-off at cb >= 100 2.60 s; steady-gain slope -0.81 per 100 counts
(pump2 V5 -0.79) but only 28 s of steady frames; no stretch reaches the 6-12 s bleed bin; rise response -1.37 / -1.10 /
-1.12 per 100 counts; COMPUTER_BRAKING 100%, no brake-error frame, ripple onset 0.14 / 0.19 / 0.13 s; brakec
0.996-1.001; the braking integrator +0.25 at 1-5 m/s down to -0.14 above 25 m/s; at the stop 0.97 m/s^2 and 180
counts (10f). Fix round 1 additions: 5 engaged arrivals, 3 of them driver brake take-overs (all on 115); final
approach -0.64 (10f, 2.6 s) and -1.11 m/s^2 (113, 1.5 s) by pump2's method; holds delivered a median 189 counts.
Verdicts: no hold moved (two radar-SUSPECT, above), no brake error, pump-off within 6.1 s; the gain, learner and
stop-distance comparisons waited for the C1 arm (C1 is retired; the comparison is now C1b against v5, 7.2). Three drives are far short of the plan's 4 drives, 15
braking minutes and 30 stops per arm.

---

## 10. Control-loop changes (sunnypilot)

### 10.1 Stopping: this car's tune and the exit debounce

**Files:** `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py` (new in the 2026-09 merge), `openpilot/selfdrive/controls/lib/drive_helpers.py`, `openpilot/selfdrive/controls/lib/longitudinal_planner.py`, `openpilot/selfdrive/controls/lib/longcontrol.py`, and the two tools `openpilot/tools/joystick/joystickd.py` and `openpilot/tools/longitudinal_maneuvers/maneuversd.py`.

#### The stopping tune

Upstream removed the per-car stopping tunes (`fdd1df79f`, `031b1ad0a`, `d1e143ac9`). For every car, the planner now asks to stop when `should_stop(v_ego, a_target)` = `v_ego < 0.3 and a_target < 0.1`, on the measured speed, and `LongControl` ramps toward `stopAccel` at a fixed 1.0 m/s³. `CP.vEgoStopping` and `CP.stoppingDecelRate` moved to `CarParams.deprecated` and are no longer read.

This car was tuned and proven on a 0.8 m/s stopping speed (the `vEgoStopping = 0.8` it used to set, 5.1) and the default 0.8 m/s³ `stoppingDecelRate`. The merge keeps both, for this fingerprint only:

```python
# stopping_tune.py
STOPPING_SPEED = {"HONDA_ACCORD_9G_AU": 0.8}                      # m/s; upstream 0.3
STOPPING_DECEL_RATE = {"HONDA_ACCORD_9G_AU": 0.800000011920929}   # m/s^3; upstream 1.0
```

- `drive_helpers.should_stop(v_ego, a_target, v_ego_stopping=None)`: `None` keeps upstream's 0.3.
- `LongitudinalPlanner.__init__` stores `self.v_ego_stopping = STOPPING_SPEED.get(CP.carFingerprint)` and passes it to both `should_stop()` calls, the MPC candidate and the cruise candidate.
- `LongControl.__init__` stores `self.stopping_decel_rate = STOPPING_DECEL_RATE.get(CP.carFingerprint, 1.0)`, and the stopping branch ramps with `output_accel -= self.stopping_decel_rate * DT_CTRL`.
- `joystickd.py` and `maneuversd.py` also call `should_stop()`. The fork used to give them `CP.vEgoStopping`; they now pass `STOPPING_SPEED.get(CP.carFingerprint)` (`maneuversd.py` parses `CarParams` for this).
- The ramp value is `float32(0.8)`, not `0.8`. `CP.stoppingDecelRate` was a capnp `Float32`, and the Python float is 1.2e-8 smaller; from a non-negative start that took the ramp one 0.008 m/s² step past `stopAccel` (a hold at -0.808 instead of -0.800, one `COMPUTER_BRAKE` count).
- Every car not in the tables gets `None` from `.get()` and runs upstream's values. Each hunk is tagged `FORK(HONDA_ACCORD_9G_AU)`.

**One difference from the fork.** The fork's planner tested the plan's first speed (`speeds[0] < CP.vEgoStopping`); upstream's `should_stop` tests the measured `vEgo`. Over logged engaged frames the new rule agrees with the fork's logged `shouldStop` on 97.8-100 % of plan frames per segment. In a counterfactual replay of the four stops on route `00000103`, `shouldStop` asserted at `vEgo` 0.78-0.79 m/s, and the ramp was 0.8 m/s³.

`stopAccel = -0.8` is still read from `CP` (5.1). The `starting` state is gone upstream (`031b1ad0a`), which changes nothing here: this car never set `startingState`.

#### The stopping-exit debounce

Constants: `STANDSTILL_SPEED = 0.15` m/s and `STOPPING_EXIT_DEBOUNCE = 40` frames (0.4 s).

`LongControl.__init__` reads `Params().get_bool("HondaDynamicTuningEnabled")` once. If it is true, it sets `self._stopping_debounce = STOPPING_EXIT_DEBOUNCE`.

In `update()`, the code remembers `prev_state`. After `long_control_state_trans()` runs, it checks whether the state is leaving `stopping` for `pid` while `vEgo < STANDSTILL_SPEED` and gas is not pressed. If so, it holds `stopping` until that transition has been requested for 40 consecutive frames. (Before the merge it also watched `stopping` → `starting`; upstream no longer has a `starting` state, so a launch is `stopping` → `pid`.)

A transition to `off` (a disengage) is never delayed, and neither is a gas press. The debounce is a post-step so that `long_control_state_trans()` keeps its signature.

Why: on route `2418f2eb2b` (t about 376 s), `shouldStop` blipped false for 0.5 s. The brake ramped from 0.99 to 0.48 and the car rolled forward at 0.3 m/s for about a second, then re-clamped over about 2 s because `stoppingDecelRate` was 0.8 m/s^3 (the ramp the stopping tune keeps). In that review, 17 of 22 holds showed micro-motion. Held pressure was measured not to decay, so this is not a pump problem (`CHANGELOG-elesys.md` section 9).

**Scope:** this is core openpilot code and is not gated on the car. It shares the tuner's toggle so a road test has only one switch. The code comment calls that a naming wart and says to give the debounce its own param if it is ever A/B tested on its own. Any car with that param set gets the debounce. The replay of route `00000103` did not exercise it: every launch on that route was a gas press.

Test: `openpilot/selfdrive/controls/tests/test_stopping_debounce.py`, two `OpenpilotTestCase` classes with 17 tests, importing the real `longcontrol`, `drive_helpers` and `stopping_tune` (before the merge it was a standalone script that stubbed `cereal` and `openpilot`). `TestStoppingDebounce`:

- toggle off leaves the state machine unchanged
- a blip is rejected
- a real launch proceeds after exactly 40 frames
- a gas press releases immediately
- no debounce while the car is still rolling
- the brake stays applied through the hold
- flapping cannot build up credit
- a disengage is not debounced

`TestStoppingTune`: the table holds this car's values (and `float32(0.8)` exactly) and no other car; this car ramps at its own rate and holds exactly at its `stopAccel`; another car ramps at upstream's rate; both hold at `stopAccel`; upstream's `should_stop` is unchanged without an override; this car may stop below its own speed; another car gets upstream's speed; the planner reads the table.

### 10.2 Lane-change nudge (`openpilot/selfdrive/controls/lib/desire_helper.py`, both `modeld.py`)

This car has three rules, on top of upstream's lane-change logic. Since the 2026-09 merge that logic is upstream's rewrite (`7d325d665`, `4532320fb`, `2d859a8ca`): no `DESIRES` table, no `laneChangeFinishing` state, at least 0.5 s in `laneChangeStarting`, a blinker already on at engagement enters `preLaneChange` at once (it still waits for the nudge), and an opt-in road-edge block (`RoadEdgeLaneChangeEnabled`, off by default). A replay of `DesireHelper` on route `00000103`'s inputs gives both of that route's lane changes (471.34 s left, 487.64 s right) at the same instants as the fork.

**1. `STEER_THRESHOLD` of 600** (`values.py`, `CAR.HONDA_ACCORD_9G_AU: 600`). `steeringPressed` is `abs(steeringTorque) > STEER_THRESHOLD`, where the default threshold is 1200.

- Evidence, from 21 `preLaneChange` windows on route `000000d9`: the 11 that armed a lane change peaked at 1556-5839 counts. Of the 10 that did not, six peaked between 600 and 1225, one of them at 937 after nine seconds of trying at 60 km/h.
- 600 catches those weak nudges without changing any nudge that already worked.
- Why this car needs it: below the EPS's 50 km/h floor the wheel is unassisted, so a nudge easily passes 1200. With 160 counts of gateway assist in the wheel, the same nudge lands around 900.
- 600 is the value the 11G Accord already uses (opendbc `b7d15a98`, sunnypilot `715ea5df6`).
- **Debounced since 2026-10-12** (owner decision; `carstate_ext.py` `STEERING_PRESSED_MIN_COUNT = 5`, area C inside an area-B function). At 600 the flag sat just above the noise floor - 10 serial counts on the board's mirror, 57-104 press onsets a minute on 115-130, most of them blips under 100 ms - and every blip froze the lateral integrator, restarted torqued's and lagd's 2 s no-learning window and blanked the "steer required" check for 2 s. `_update_driver_torque_validity()` now sets `steeringPressed` once per frame from the final torque (0x18F, or the `0x700` mirror) through upstream's `CarStateBase.update_steering_pressed(abs(torque) > STEER_THRESHOLD, 5)`, as Hyundai, VW, Ford, Tesla and PSA do: a press registers on its 6th consecutive frame (50 ms late), a blip of 1-5 frames never does, a press held 11 frames or more releases on its 6th clear frame. The threshold stays 600. Replayed on 115-130: onsets per engaged minute 55-99 -> 8-25, engaged time 2 s or more after a press 18-37% -> 30-81%; on d9 the 11 nudges that armed and the four of b7d15a98's six that arm today still arm, three of them 50 ms later and one 150 ms earlier; 12d at 833.2 s arms 0.85 s earlier because the debounced flag bridges a one-frame dip in the push (CHANGELOG 2026-10-12, push6).

**2. `NUDGE_FIRM`: a nudge must be firm, or held.** With the threshold at 600, a single 10 ms reading of 645 counts confirmed a lane change (route `fc`, t = 768.8), and the driver reported that touching the wheel with the blinker on started a lane change.

- Module constants: `NUDGE_FIRM = {"HONDA_ACCORD_9G_AU": 1500.}` and `NUDGE_HOLD_FRAMES = 4`.
- `DesireHelper.__init__(self, car_fingerprint: str = "")` sets `self.nudge_firm = NUDGE_FIRM.get(car_fingerprint)`.
- In `preLaneChange`, `self.nudge_frames` counts consecutive frames of torque in the wanted direction. It is reset where upstream enters `preLaneChange` (beside `lane_change_timer = 0.0`).
- When `nudge_firm` is set, the torque confirms only if `abs(steeringTorque) >= nudge_firm`, or if it has been present for 4 model frames (150 ms). The block sits before upstream's edge-aware `blindspot_detected`.

**Replay figures: the two sources disagree.** Both agree that no nudge confirms earlier than before and that three no longer confirm at all (peaks of 693, 740 and 903, held for one or two frames). They differ on the rest:

| source | windows | same instant | later | not at all |
|---|---|---|---|---|
| `FORK` comment above `NUDGE_FIRM` in `desire_helper.py` | 115 blinker windows on routes d9..fd, 66 lane changes the old rule confirmed | 29 | 29 by 50-150 ms, 4 later still | 3 |
| commit message of sunnypilot `10e088a2d` (and `docs/CHANGELOG_SERIAL_STEERING.md` 2026-09-27) | 92 recorded windows | 37 | 29 (mostly 50-150 ms, three at 0.4-0.9 s) | 3 |

The commit message says its figures came from driving the real `desire_helper` over the windows. I could not tell which set describes the code as committed. If the replay is re-run, fix the comment to match.

Cars not in the table keep upstream's single-frame rule. `NUDGE_FIRM` does not change `steeringPressed`, so DM and the lateral controller still see a light hand on the wheel (debounced, item 1).

**3. `LANE_CHANGE_SPEED_FLOOR`: no lane change starts below 52 km/h** (push13). The board asks the EPS for torque from
52.00 km/h and lets go under 51.00 (`GW_EPS_ACK_CPH` / `GW_EPS_KEEP_CPH`; the EPS itself drops out at 50.2-50.8), and
`vEgo` tracks the board's `XMISSION_SPEED` (0x158) closely there: between 45 and 60 km/h the median offset is +0.01 to
+0.04 km/h and 90% of frames are within -0.19 to +0.23 km/h (routes 12a, 12b and 141; a rare frame is up to 2.3 km/h
off), so at the floor a change can start about 0.06 s (at 1 m/s²) before the board starts asking. A lane change started
below it is the driver's hands alone, and the change is one desire pulse to the model, which goes on with it once the car
is back over the floor and the board has the wheel - after an intersection turn as much as after a lane change.

- Evidence (the logs, check-first): on routes 10f-152, 66 of 222 lane changes started below 52 km/h, 34 of them with the
  wheel past 90 degrees within 8 s (turns), and 6 of the 62 started below 50 were still running above 52.5; over all
  the sunny_logs routes 165 of 438 (10 of the 153 below 50 still running above 52.5), and 25 started between 50 and
  55 km/h (11 on 10f-152). No change that started
  at 52 or above had its blinker switched on below 52, and 7 of 590 blinker holds (4 of 273 on 10f-152) came on below 52
  and were still on at 52.
- Module constants: `LANE_CHANGE_SPEED_FLOOR = {"HONDA_ACCORD_9G_AU": 52 km/h}` and `LANE_CHANGE_FLOOR_HYST = 1 km/h`.
  `DesireHelper.__init__` sets `lane_change_speed_min` (the floor) and `lane_change_speed_drop` (the floor less the
  hysteresis); a car not in the table gets upstream's `LANE_CHANGE_SPEED_MIN` for both.
- Upstream's rule at the new numbers: the blinker arms `preLaneChange` only at or above the floor; a waiting change is
  dropped under `lane_change_speed_drop` (upstream drops it under its minimum) and in the 51-52 km/h band it waits, the
  nudge and the auto lane change starting it only at the floor; `laneChangeStarting` is untouched, so a running change
  finishes. Ending it was the other choice and is worse: modeld feeds the model the desire's rising edge only, so the
  model would carry on with the change and the state would only hide it.
- As upstream, only the blinker's rising edge arms a change. One switched on below the floor arms nothing when the car
  gets above it, and neither does one still on after its waiting change was dropped under 51 km/h: the driver switches
  it off and on again above 52. That is what costs the 3 replayed starts at 52 or above (below). 7 of 590 logged holds
  (4 of 273 on 10f-152) came on below 52 and were still on at 52.
- Arming when the car crosses the floor with the blinker held was replayed too (the same 67 routes, `prev_one_blinker`
  cleared below the floor) and is left for the owner (push13 review). It adds 6 starts, none below 52, and changes no
  lcfloor start. 3 are those lost changes back (ab 1373.97 and 1374.86, 130 986.15; push12 made them at 1374.21,
  1374.86 and 986.15). The other 3 are merges begun by hand below 52 (b0 432.30, 129 488.49, 12a 2175.63): the blinker
  came on at 44-45 km/h and push12 started the change at 44-46; the variant arms at 52.1-52.2 and starts on the next
  frame, the driver already pushing 2900-3300 counts. That is a fresh desire pulse 1.1-1.7 s into a lane change the car
  is already making, and whether the model finishes it or takes it as a second lane change cannot be told offline.
  Re-arming only a hold first armed at or above 52 would give the 3 back without the merges.
- Between 51 and 52 km/h a waiting change keeps its prompt, but a nudge there is not a refused nudge (no chime, no
  lane-block tile: those are for a blind spot or a road edge). It starts the change only if it is still held when the
  car reaches 52 (d3 977.25, below); a nudge let go in the band is gone. Starting down to 51 a change armed at 52 or
  above that has not been under 51 since is the other choice, also left for the owner. The start test is the floor and
  not the drop speed so that no change starts where the board may not be steering.
- A merge begun by hand below 52 now gets no lane change from openpilot. The board still engages at 52 on speed alone
  (it never reads openpilot's lane-change state) and follows the model's lane, which may be the old one while the car
  is still across the line; a push against the board fades it. README first drive, step 7.
- Replay (`DesireHelper` push12 and lcfloor on the logged inputs of every route with rlogs and a lane-change state, 67
  routes, 22.7 h: `carState`, `latActive`, `driverTorqueStale`, the frame's `desireState` and RELC's edge blocks; on
  routes 10f-152 push12's replay agrees with the logged state on 99.96% of frames and 194 of its 197 starts are within
  0.25 s of a logged one): 438 starts become 265. The 174 that go: 171 started below 52 km/h (89 at 32-40, 45 at 40-45,
  23 at 45-50, 10 at 50-51, 4 at 51-52; 13 were still running above 52.5) and 3 were a later change in a blinker hold
  whose waiting change lcfloor had dropped under 51 km/h, the car coming back above 52 with the blinker still on (ab
  1374.2 at 53.3 km/h and 1374.9 at 55.3, 130 986.2 at 58.0; push12 had started one of those in between). One moves: d3 977.25 at 51.8 km/h starts 0.45 s later at
  52.0. Every one of the 288 waiting changes a blinker armed at 52 or above is armed on the same frame.

Test: `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_speed_floor.py` (16 tests; README inventory).

Wiring: `openpilot/selfdrive/modeld/modeld.py` and `openpilot/sunnypilot/modeld_v2/modeld.py` construct `DesireHelper(CP.carFingerprint)`, subscribe to `carStateSP`, and call `DH.update(..., left_edge, right_edge, driver_torque_stale=sm['carStateSP'].driverTorqueStale)`. That argument is area B: a latched torque cannot confirm a nudge (`torque_applied = carstate.steeringPressed and not driver_torque_stale and ...`). It is the last parameter and passed by keyword, because upstream put its two road-edge parameters in the slot the fork used to fill positionally. The hunks carry `FORK(HONDA_ACCORD_9G_AU)` (the constructor) and `FORK(LKAS-GATEWAY)` (the rest).

Test: `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`, one `OpenpilotTestCase` with 9 tests:

- other cars keep the upstream single-frame nudge
- a brush does not confirm
- a light push confirms once held (on the 4th frame)
- letting go starts the count again
- a firm tug confirms at once
- direction still matters
- a stale torque still confirms nothing
- `driver_torque_stale` comes after the road edges (added in the merge)
- the count restarts on a new blinker

Integration section 15 covers the stale-torque case from the car side.

### 10.3 Lateral integrator hold (`latcontrol.py`, `latcontrol_torque.py`, `latcontrol_torque_v0.py`, `latcontrol_torque_ext_base.py`)

This is listed here because it lives in the control loop. The reason for it is the gateway (area B); the protocol side is in that document.

- `LatControl` gains `LINBUS_I_CARRY_MAX = 0.25` m/s^2 and `LINBUS_I_HOLD_TAU = 30.0` s; the attributes `linbus_gateway_present`, `linbus_gateway_actuating`, `_linbus_was_actuating` and `integrator_frozen`; and the methods `set_linbus_gateway(present, actuating)` and `_linbus_integrator_gate()`.
- Both torque controllers call `linbus_hold = self._linbus_integrator_gate()` at the top of `update()`. They call it on every frame, active or not, so the takeover edge is never missed. They add `or linbus_hold` to `freeze_integrator`.
- Since the 2026-09 merge, `LatControlTorqueExtBase.update_output_torque()` also freezes on the owning controller's `integrator_frozen`. With upstream's Lateral Jerk controller or NNLC on, the extension updates the same PID a second time in the frame, and without this it wound the integrator through every hold (`test_latcontrol_gateway_hold.py`, on this car's fingerprint).
- Behaviour: with no gateway (`present` is False, which is every other car) nothing changes. With a gateway, the integrator is frozen while the board is not actuating and decays with a 30 s time constant. On the frame the board starts actuating, the integrator is clipped to +-0.25 rather than zeroed.
- Why: the integrator wound up open-loop to +0.65 on route `000000b3`. Zeroing it at takeover then meant re-learning the car's steady right-hand trim (+0.04 to +0.20 on routes `000000d3`/`d4`), which took about 14 s at every takeover (sunnypilot `39b857567`, `946b5fa21`).
- `LatControlPID` and the angle controllers do not call the gate. This car uses the torque controller (2.4).

### 10.4 `controlsd.py` and `controlsd_ext.py`

- `controlsd.py` subscribes to `carStateSP`. Before `self.LaC.update()` it runs `gw = self.sm['carStateSP'].linbusGateway; self.LaC.set_linbus_gateway(bool(gw.present), bool(gw.actuating))`, and it now calls `self.run_ext(self.sm, self.pm, lac_log, self.LaC)`.
- In `controlsd_ext.py`, `state_control_ext(sm, lac_log=None, LaC=None)` and `run_ext(sm, pm, lac_log=None, LaC=None)` fill `CC_SP.lateralControl.integrator` and `saturated` from `lac_log` (only if it has an `i` field), and `integratorFrozen` from `LaC.integrator_frozen`. `create_sp_hud_status()` (area B) consumes them.
- Since the round 8 review `controlsd.py` also lowers `HONDA_ELESYS`'s PID floor on a climb: `self.elesys_pid_floor =
  CP.brand == 'honda' and elesys_pid_floor_applies(CP, CP_SP)` in `__init__`, and right after `get_pid_accel_limits()`
  the floor `elesys_pid_accel_min(CP, CP_SP, pitch)` with `calibrated_pose.orientation.y` (7.12). Both lines are marked
  `FORK(HONDA_ACCORD_9G_AU)`; on every other car the flag is false and the limits are upstream's.
- Since 2026-10-12 `controlsd_ext.py` also fills `CC_SP.lateralControl.steerSaturated` through `self.steer_saturated` (`steer_saturated.py`, the class selfdrived's `steerSaturated` block now calls, 10.6), from `carState`, `modelV2` and controlsd's own `curvature`. That, not `saturated`, is `0x500 OP_SATURATED`: the board alarmed on lane changes where openpilot's own alert never would (LKAS-GATEWAY-PROTOCOL.md 4.2).

### 10.5 `openpilot/selfdrive/car/helpers.py`

`convert_carControlSP()` now rebuilds `struct_dataclass.lateralControl = structs.CarControlSP.LateralControl(**remove_deprecated(struct_dict.get('lateralControl', {})))`. Every nested struct has to be rebuilt by hand, or it reaches the car controller as a plain dict.

Missing this line crashed card on every frame on routes b5-b8. With card down nothing re-sent `SCM_BUTTONS`, so the car threw ACC and CMBS faults (sunnypilot `d11d2c9a8`).

Test: `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py`. It also checks, from the capnp schema, that every nested struct of `CarControlSP` is rebuilt, so the next one added cannot repeat the crash. **Any new nested field in `CarControlSP` needs a matching line here.**

### 10.6 `openpilot/selfdrive/selfdrived/selfdrived.py`

Area B adds `'carStateSP'` to the `SubMaster` list, for MADS, and the EPS latch alert. card publishes `carStateSP` unconditionally at 100 Hz.

**The VSA fault alert (area C, 2026-10-03; 6.6).** Three `FORK(HONDA_ACCORD_9G_AU)` hunks: the import and `self.vsa_fault_alert = VsaFaultAlert()` in `__init__`; in `update_events()`, after the car events and the dashcam return and right *before* the EPS latch alert, `self.vsa_fault_alert.update(self.sm['carStateSP'], self.events.has(EventName.accFaulted))`, whose first list goes into `events` and second into `events_sp`, then `if self.vsa_fault_alert.cleared: self.eps_latch_alert.reset()` (so the reset lands before the latch alert's update on the same frame); and in `update_alerts()`, once both lists are made, `alerts, alerts_sp = self.vsa_fault_alert.adjust_alerts(alerts, alerts_sp, self.AM, self.sm.frame, callback_args)` before `AM.add_many()`.

**`steerSaturated` (area B, 2026-10-12, `FORK(LKAS-GATEWAY)`).** Upstream's block is unchanged in what it decides, but its conditions now live in `openpilot/sunnypilot/selfdrive/controls/lib/steer_saturated.py` (`SteerSaturatedAlert`), which `controlsd_ext` also uses for `0x500 OP_SATURATED` (10.4): `self.steer_saturated = SteerSaturatedAlert()` replaces `self.last_steering_pressed_frame`, and the block is `if self.steer_saturated.update(CS.steeringPressed, lac.active, lac.saturated, CS.vEgo, controlstate.curvature, self.sm['modelV2'].action.desiredCurvature, self.CP.notCar, frame=self.sm.frame): self.events.add(EventName.steerSaturated)`. `test_steer_saturated.py` fuzzes it against a verbatim copy of the old block (20 000 frames, 0 disagreements); on a merge that changes upstream's block, change `steer_saturated.py` to match.

**`card.py` sends `carStateSP` before `carState`** (a `FORK(HONDA_ACCORD_9G_AU)` hunk in `state_publish()`). selfdrived blocks on `carState` and then reads `carStateSP` with `sm.update(0)`; sent after `carState` (upstream's order), it could be the previous frame's, and the disengagement alert is created only on the one frame `accFaulted` first appears, so a one-frame-stale `vsaFault` there showed "Cruise Fault: Restart the Car" for 4 s (the validator's lag replay of 110 and 112; review of `0ca637a4`). Sent first, it is already in selfdrived's queue when `carState` wakes it. The car's own logs had shown the fresh case in 21 of 21 measured frames even in upstream's order, so this closes a race rather than a frequent fault; `late_alerts()` below covers whatever is left (replays, a lagging reader).

`openpilot/sunnypilot/selfdrive/selfdrived/vsa_fault_alert.py` (`VsaFaultAlert`):

| state | `events` | `events_sp` | upstream alerts dropped |
|---|---|---|---|
| live: `vsaFault`, with upstream's `accFaulted` raised | (`accFaulted`, as before) | `vsaFault` | `accFaulted/immediateDisable`, `/noEntry`, `/permanent`; `steerUnavailable/permanent`, `/noEntry`; `lkasGatewayEpsLatched/warning`, `lkasGatewayEpsLatchedReminder/warning` |
| held (`stored`): `vsaStoredFault`, or `vsaFault` without `accFaulted`, not live | `carNotReady` | `vsaStoredFault` | `carNotReady/noEntry`; `steerUnavailable/permanent`, `/noEntry`; the two EPS-latch alerts |
| first frames of a fault episode | | `vsaFaultAnnounce` for `ANNOUNCE_FRAMES` (0.1 s) | |
| live, and `accFaulted`'s IMMEDIATE_DISABLE or NO_ENTRY alert already up without `vsaFault`'s | | | `late_alerts()` creates `vsaFault`'s of that type this frame |
| the frame the last of them drops | | | `cleared`: selfdrived resets `EpsLatchAlert` |

- **Only the text of a live fault changes.** `accFaulted` is upstream's, raised from `BRAKE_ERROR`, and it still disengages openpilot and MADS on the same frame. `vsaFault`'s own `IMMEDIATE_DISABLE` and `NO_ENTRY` are only ever raised beside it, so they add no disable and no refusal. Its alerts are upstream's in everything but the words: `ImmediateDisableAlert("Stability Control (VSA) Fault")`, `NoEntryAlert("VSA Fault: Brakes Degraded")`, and a silent `Priority.LOWER` banner "VSA Fault / Brakes, ACC, CMBS degraded. Have VSA codes read". Upstream's alerts are dropped rather than outranked because the disengagement alerts tie at `HIGHEST`, and the `AlertManager` breaks a tie on priority *and* start frame in favour of the alert type it met first, which would be upstream's.
- **The onset frame.** The disengagement alert exists only on the frame `accFaulted` first appears; on the next one selfdrived is disabled and makes no IMMEDIATE_DISABLE alert. opendbc sets `vsaFault` on that same CAN frame (6.6) and card sends it first (above). If it still reaches selfdrived a frame late, `late_alerts()` creates `vsaFault`'s IMMEDIATE_DISABLE alert (or NO_ENTRY, for a press on that frame) on the first live frame while upstream's is still up and the VSA's is not: same priority, newer start frame, so the `AlertManager` shows the VSA's from then on, for its own 4 s. It is text only: no event, and the same `warningImmediate`, which soundd does not restart. Frame 0 then reads upstream's words for 10 ms.
- **A held fault refuses engagement through `carNotReady`**, upstream's NO_ENTRY-only event ("car is transiently refusing engagement"; nothing on this car raises it otherwise). It has to be a `log.OnroadEvent`: selfdrived's own state machine reads only `events`, so an SP event cannot refuse openpilot. MADS's state machine reads both lists, so **MADS is refused too, deliberately**: the EPS refuses torque while the VSA holds the fault (STEER_STATUS 2 from 1.66 s after key-on on 111 and 113, then a hard fault 30 s later that clears only with the VSA), the board steers only above 51.5 km/h while the fault clears at about 35 km/h, and a live fault already refuses MADS through `accFaulted`. An enabled MADS is not disabled by it (NO_ENTRY only refuses entry); a paused one does not resume until the fault clears. Texts: `NoEntryAlert("VSA Fault: Clears Above 35 km/h")` (22 mph imperial) and a silent banner "VSA Fault Stored / Clears after driving above 35 km/h". *Held* also covers `vsaFault` without `accFaulted`: with `CS.canValid` False selfdrived skips the car events, `accFaulted` among them (live bits without `BRAKE_ERROR` have never been seen). That case refuses through `carNotReady` too, and `vsaStoredFault`'s callbacks then show the live texts ("VSA Fault: Brakes Degraded", the "VSA Fault" banner), not "Clears Above 35 km/h"; `vsaFault` itself is not raised there, because MADS would act on its IMMEDIATE_DISABLE. The flag drops half a second after the VSA's lamp bits (opendbc's clear debounce), so a press inside that half second is still refused - on route 113 MADS was pressed 10-20 ms after it dropped. Kept: clearing at once on an all-clear frame would let a single odd frame open engagement for the half second the set debounce then takes; a refused press costs one more press.
- **`steerUnavailable`'s banner and no-entry text are dropped while either is up**; its disengagement alert is not. The EPS escalates 30 s after the VSA on every route and clears 21 ms after it (EPS DTC 85-01, "VSA system malfunction"), and on 110 and 112 its "LKAS Fault: Restart the car to engage" banner, newer at the same `LOWER` priority, replaced the cruise-fault banner for the rest of the drive.
- **The EPS-latch alert (`EpsLatchAlert`, area B) is dropped while either is up and reset when the VSA clears.** The board reports the EPS latched (0x70B) while the VSA holds its fault, because the EPS follows the VSA. On route 113 it did from 31.8 s, 0x70B then went stale from 34.5 s to 41.6 s, the VSA (and the EPS) cleared at 36.9 s, MADS engaged at 37.4 s, and the base branch's alert - still holding a confirmed latch, since stale frames count towards neither confirm nor clear - announced "Steering Fault / Turn the car off and on to clear it" with a prompt at 37.6 s (the validator's replay, both modes). `filter_alerts()` drops `lkasGatewayEpsLatched/warning` and `lkasGatewayEpsLatchedReminder/warning` while the VSA's fault is up, and on `cleared` selfdrived calls `EpsLatchAlert.reset()`, so a latch has to be confirmed again from fresh 0x70B frames (1 s); on 113 the frames after 41.6 s read not latched. A real latch after the VSA clears is still announced as before.
- **One sound per fault.** `vsaFaultAnnounce` is a `PERMANENT` (shown engaged or not), `Priority.LOW` (below driver monitoring's stage 2), `AudibleAlert.prompt`, 3.5 s: shorter than an `ImmediateDisableAlert`'s 4 s, so when a live onset disengages something the disengagement alert covers it from first frame to last and only `warningImmediate` plays. A new episode needs `REARM_FRAMES` (10 s) with neither flag. The banners are silent; a refused engagement plays upstream's `refuse`, per press, as before.

**Replayed** (the real selfdrived through `process_replay`, the logged inputs, CarParams and params, with `carStateSP.vsaFault`/`vsaStoredFault` from the real `CarInterface`; 110 from segment 13; twice each: *fresh*, `carStateSP` from the same card frame as `carState`, which is what card's new order gives, and *lag*, the previous frame's):

| route | fresh | lag |
|---|---|---|
| 110 (live onset, MADS engaged) | "TAKE CONTROL IMMEDIATELY / Stability Control (VSA) Fault" from 1923.171; identical to before the fixes | upstream's words on 1923.171 only, the VSA's from 1923.181 for its 4 s; one `warningImmediate` |
| 112 (live onset, openpilot engaged) | the VSA's from 128.857; identical | upstream's on 128.857 only, the VSA's from 128.866 |
| 111 (stored, parked) | announce with one prompt at 8.16 s (when selfdrived finished initialising), then "VSA Fault Stored"; identical | identical |
| 113 (stored, cleared at 36.9 s) | as before, except that the EPS-latch "Steering Fault / Turn the car off and on to clear it" with its prompt at 37.63 s is gone; MADS's press at 37.417 accepted | the same |

Every other entry of the four timelines (alerts, state, MADS, the VSA, latch, `carNotReady`, `accFaulted` and `steerUnavailable` events) is identical to the replay of `0ca637a4`, apart from a refused press on 110 and 112 (lag) whose alert shows one frame later, when the VSA's disengagement alert that started one frame later ends.

On every other car both flags are False, `update()` returns two empty lists, `cleared` is never True, and `adjust_alerts()` returns both lists unchanged.

### 10.7 `openpilot/sunnypilot/mads/mads.py` (area B, with one rule for every car)

Area B owns this file. One rule in it, though, is gated neither on the car nor on the gateway: a steering rate of at least `EMERGENCY_STEER_RATE = 200.0` deg/s for `EMERGENCY_STEER_FRAMES = 2` frames adds `lkasDisable` on **every** car running MADS on this fork (sunnypilot `35622a994`). It is mentioned here so that a car maintainer is not surprised by it.

Since 2026-10-06 the gateway's override pause on this car ends only on a fresh `0x70B` frame that says the override is over (`grantSeq`, 6.5), or after 3 s with no `0x70B` frame at all (`GATEWAY_SILENT_RESUME_FRAMES`): route 121 resumed at 57.40 on a stale frame (0.50 s after the last override frame), 1.9 s before the board's next frame at 59.30 (a 2.4 s gap). The rule and its reasons are in `LKAS-GATEWAY-PROTOCOL.md` (area B).

### 10.8 The re-entry ramp after the driver lifts off the gas (round 8, 2026-10-20)

Owner decision 2026-10-19 (proposal 2). **Files:** `openpilot/sunnypilot/selfdrive/controls/lib/reentry_ramp.py` (new;
`ReentryRamp`, `reentry_ramp_applies()`, `lead_decel()`, `REENTRY_RAMP_S`, `REENTRY_RAMP_JERK`,
`REENTRY_RAMP_MPC_FLOOR`, `REENTRY_RAMP_LEAD_DECEL`, `REENTRY_RAMP_MIN_SPEED`, `REENTRY_RAMP_GAS_S`) and two
`FORK(HONDA_ACCORD_9G_AU)` hunks in `openpilot/selfdrive/controls/lib/longitudinal_planner.py`: `self.reentry_ramp =
ReentryRamp(dt) if reentry_ramp_applies(CP, CP_SP) else None` in `__init__`, and right after the candidates' `min()`,
`output_a_target = self.reentry_ramp.update(reset_state, v_ego, aEgo, output_a_target, min(output_a_target_mpc,
min(a_desired_trajectory)), self.fcw, self.mpc.crash_cnt, carState.gasPressed, lead_decel((leadOne, leadTwo)))`.

**Why** (round 8 braking analysis, drives `13d`-`143`). While the driver holds the gas the planner resets to `aEgo`
every frame, but the plan it publishes can already be braking for a slowing lead (141 t=206.4: -0.66 with the foot
still on the pedal). When the foot comes off, `LongControl`'s PID restarts with the plan as its feedforward, so the
command steps from what the car is doing straight to the plan, and the only smoothing left is the brake's rise limiter
(about 2.6 m/s^3). 28 of 92 re-entries above 3 m/s went straight into a plan under -0.5; the worst felt -3.6 (141
15:52:07) and -4.0 m/s^3 (141 16:18:22, no lead, a -1.3 model plan).

**What.** For 1.5 s after a gas override ends (`reset_state` turns false with `gasPressed` seen within the last 0.5 s)
the published plan may fall by at most 1.5 m/s^3 x `DT_MDL` per frame, from `min(aEgo, 0)` on the first frame back
(what the car was doing, never a request to keep accelerating). It only raises the plan (`max(plan, limit)`), so a plan
above the limit passes bit for bit. It stops for the rest of that re-entry, passing the plan as it is, on the planner's
FCW, on the MPC's crash flag (`crash_cnt > 0`), on an MPC plan under -2.0 (urgent braking, well inside this car's full brake: -2.45 m/s^2 at 255 counts and about -2.95 at 320, measured; 7.13 - this said "about this car's full brake", corrected 2026-10-24) - its
action-time value or any point of its trajectory over the next 2.5 s -, on the plan itself (any candidate, e2e too)
under -2.0, on a lead braking harder than -2.0 (`lead_decel()`: the lowest `aLeadK` of the present leads; a present lead
without a finite one counts as braking hard), or a non-finite plan. An engagement with no gas in the last 0.5 s is not a
lift-off and gets upstream's plan. It arms only at 3 m/s or faster on the first frame back (all 92 measured re-entries
were above it; below it is the stop-and-go creep, where nothing was measured), and only on `HONDA_ELESYS` with
openpilot longitudinal and stock ACC mode off: every other car and stock ACC mode keep upstream's `longitudinalPlan` bit
for bit. One `reentryramp end= t= v= a0= plan0= short= frames=` line goes to cloudlog per re-entry where the ramp bound
(`end=` time, override, fcw, crash, mpc, plan or lead).

**The round 8 review's changes** (2026-10-20): as first built the ramp armed on every engagement (13d t=370.9:
re-engaging after the driver braked, behind a lead at `aLeadK` -3.5, -1.04 was published as -0.48), which the owner had
not chosen, and its only plan exception read the MPC's action-time value, which is itself jerk-limited and takes
0.4-0.85 s to pass -2.0 behind a hard-braking lead. In the review's closed-loop sim (the real planner, `LongControl`,
the tuner-on `CarController` and the batch-3 plant; the driver on the gas until the lift-off) that turned 11 of 192
near-misses behind a lead braking at -3 to -6 m/s^2 into collisions, at up to 3.3 m/s. Rerun with the fixes: 0 new
collisions in those 192 (worst closest approach 0.44 m shorter), 0 in 20 stopped-lead and 60 closing-on-a-slower-lead
scenarios (the following gap up to 1.6 m shorter of 10-28 m, the cost of a ramp). On real drives (open loop, the
logged radarState, 55 routes): the ramp binds on the same 20 lift-offs of `13d`-`143` frame for frame, and no longer on
the 5 engagements; over all 55 it binds on 496 lift-offs (542 re-entries before), 7 of them while `leadOne`'s `aLeadK`
read under -1.5 (10 before); the trajectory floor took out 129 t=744.5 and 751.7 (28 m/s, 26 m behind a slower car).

**Replayed** (the planner open loop on the logged inputs, against the same planner with `reentry_ramp = None`, which is
upstream's code path; fidelity against the logged plan 92-97% of frames within 0.01 m/s^2):

- 92 re-entries at 3 m/s or more on `13d`-`143`, 26 where the ramp bound. 141 t=207.8 (15:52:07): the plan used to step
  from -0.18 to -1.59 in one frame; it now ramps at 1.5 m/s^3 and meets the plan 0.9 s later. 141 t=206.4 (15:52:06):
  the steepest half-second fall goes from -2.4 to -1.5 m/s^3. The exceptions fired as designed: MPC under -2.0 at 141
  t=1784.4, the driver's brake at 142 t=1243.1.
- Elsewhere: bit-identical before each route's first re-entry, `fcw` and `shouldStop` identical on every frame (8
  routes, 13d-143 and 10f/113/120/121, 187,206 planner frames); after a re-entry where it bound, the MPC is warm-started
  from the ramped accel, which leaves up to 0.020 m/s^2 of difference in the 2 s after the window (142) and at most
  0.012 beyond them (13d; 0.006 on 141, 0.002 or less on the other six routes).
- Cost: the median braking re-entry starts braking no later; the worst 0.18 s later, at most 0.34 m further by the end
  of the window.
- Felt jerk, closed loop through the real controller and the batch-3 car model (logged / sim today / sim with the ramp):
  141 16:18:22 -3.86 / -3.10 / -2.44; 141 15:52:07 -3.62 / -3.06 / -2.42; 141 15:52:06 -1.87 / -1.94 / -1.04 m/s^3.
  **Short of the hoped-for -1.5 to -2.0 on the two worst**: the PID's integrator keeps winding against the brake's lag on
  top of the ramp. Not built, for information: a 1.0 m/s^3 ramp gives about -1.7, and also holding the integrator during
  the ramp about -2.0. (142 17:58:04, which the analysis listed at -3.63, was an engagement; that number was the
  driver's own braking at t=1243.1.)
- `13e`/`13f` (qlogs, the plan at 2 Hz) cannot be replayed; on the logged plan the ramp would bind on 2 and 17 of their
  re-entries, by at most 0.28 and 0.43 m/s^2.

**Drive check.** The `reentryramp` lines, and the IMU jerk after each lift-off; any lift-off behind a hard-braking car
where the brake came noticeably late (the line's `end=lead`, `end=plan` or `end=mpc` says the ramp let go).

Test: `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_reentry_ramp.py` (27: the window, the rate and the start,
a plan above the limit untouched, each exception for the rest of the re-entry (FCW, crash, the MPC, the plan, the lead,
non-finite ones), exactly -2.0 keeping the ramp, `lead_decel()`, the speed gate, a new override, an engagement and a cold
start getting upstream's plan, the 0.5 s gas window frame by frame, non-finite inputs, one log line; the gate on this
car, op long and not stock ACC mode; and the real `LongitudinalPlanner` with its MPC through an override and a
lift-off: at most 1.5 m/s^3 for 1.5 s here and nowhere else, every frame outside the window bit-identical, a stopped car
close ahead, an engagement, a lead braking at -2.5 and an MPC trajectory under -2.0 all getting upstream's plan). Each
of the review fixes, broken on its own (8 ways), fails at least one test, and on round 8 as first built 12 of them fail.

---

## 11. UI, sunnylink and statsd

There are two settings. `HondaDynamicTuningEnabled` can be reached from three places (round 5 removed the comma 3X Cruise panel's copy); `HondaElesysGasLawV2` (the gas law, 9.2) from sunnylink, and the mici and big UI show its value. Both take effect at the next ignition, because they are read only once. Batch 3 (2026-10-05) added two more, offroad only on the big UI, the mici page and sunnylink: the pump rule and `HondaElesysBrakeLawV2` ("Measured brake law (testing)", off), which went with brake law v2 in round 5 (7.10). The pump rule's setting is `HondaElesysPumpC1b` ("Quiet pump at stops", on; 7.2) since 2026-10-06; it replaced `HondaElesysPumpV6` ("Quieter brake pump", rule C1), which is no longer registered. Both are read once, at ignition, by opendbc's `_initialize_honda()` into `CarParamsSP.flags` (64), for `HONDA_ELESYS` with openpilot longitudinal only and never in stock ACC mode; the controller reads only the flag, so each route records which rule it ran. There are read-only views of the learned brake gain and the engaged time per drive mode.

### 11.1 Params (`openpilot/common/params_keys.h`)

| key | flags | type | default |
|---|---|---|---|
| `HondaDynamicTuningEnabled` | `PERSISTENT`, `BACKUP` | BOOL | `0` |
| `HondaDynBrakeGain` | `PERSISTENT` | FLOAT | `0.0` |
| `HondaDynModeSecD`, `HondaDynModeSecECON`, `HondaDynModeSecS` | `PERSISTENT` | FLOAT | `0.0` |
| `HondaElesysGasLawV2` | `PERSISTENT`, `BACKUP` | BOOL | `1` |
| `HondaElesysStockAcc` | `PERSISTENT` (deliberately not `BACKUP`) | BOOL | `0` |
| `HondaElesysStockAccSaved` | `PERSISTENT` | JSON | - |
| `HondaElesysPumpC1b` | `PERSISTENT`, `BACKUP` | BOOL | `1` |

`HondaDynPedalGain0`-`5` and `HondaDynWindFactor` were removed in 2026-10 with their learners, `HondaElesysPumpV6` (rule C1) on 2026-10-06, and `HondaElesysBrakeLawV2` (brake law v2) on 2026-10-09: `HondaElesysPumpC1b` is a new key so that C1b starts on even where C1 had been switched off (7.2). The learned values are not `BACKUP`. They change every 60 s and belong to one car, and a restored backup could bring back a tune learned on different hardware. The type must stay FLOAT: statsd depends on it to send numeric fields, and `_ParamWriter` counts write errors if a key is missing. The `EpsLkas*` keys in the same hunk belong to area A.

### 11.2 Big UI (comma 3/3X)

**`openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py`** is upstream's file again (round 5, 2026-10-09). It
carried a second copy of the learning toggle (`honda_dyn_toggle`, "Honda Nidec Dynamic Longitudinal Learning (Alpha)",
with `_sync_honda_dyn_toggles()`), in four upstream hunks; the owner drives a comma 4, which has no Cruise panel, so it
went. The param is unchanged and is set from the Honda panel below, the mici page and sunnylink;
`test_the_toggle_lives_on_the_honda_panel_only` keeps the Cruise panel free of it.

**`openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py`**

- `HondaSettings` was empty upstream. It now has the toggle and a `Learned Values` row with a RESET button. Reset is offroad only, sits behind a confirmation dialog, and re-checks offroad when the dialog is confirmed. It writes `RESET_KEYS` only, which is `HondaDynBrakeGain`: the `HondaDynModeSec*` totals are a tally of the data for per-mode tables, not a tune, so a brake-learner reset keeps them.
- `update_settings()` rebuilds the toggle description each frame from `DYN_DESC` and `DYN_IGNITION_NOTE`. When `ui_state.has_longitudinal_control` is false it prefixes `DYN_NO_LONG_DESC` in bold (`This feature is unavailable because sunnypilot Longitudinal Control is not enabled on this car.`). It only changes the text; the toggle itself stays settable.
- Module-level API used by the mici page: `LEARNED_DEFAULTS`, `RESET_KEYS`, `MODE_SLOTS`, `TUNING_PARAM`, `GAS_LAW_PARAM`, `GAS_LAW_DEFAULT`, `GAS_LAW_PLATFORMS`, `learned_value()`, `car_platform()`, `gas_law_applies()`, `gas_law_v2()`, `gas_law_label()`, `mode_minutes()`, `mode_time_text()`, `reset_learned_values()`.
- The readout refreshes once a second (`LEARNED_REFRESH_S`). It shows the gas law the car will run from the next drive, the engaged minutes per drive mode, and the brake value as a gain (`x` followed by `1.0 + HondaDynBrakeGain`, sunnypilot `2b2d8e0a7`). The six pedal gains and the aero factor it used to show could not move and are gone (2026-10). The gas-law line shows only when `gas_law_applies()`: the platform (`CarPlatformBundle` first, `CP.carFingerprint` second) is in `GAS_LAW_PLATFORMS`, a copy of opendbc's `HONDA_ELESYS` (`HONDA_ACCORD_9G_AU`) that a test keeps in sync. Another Honda runs upstream's pedal law whatever the setting says, so it is not shown the setting.
- The key names and defaults are duplicated here on purpose instead of importing the tuner, so a failing opendbc import cannot blank the settings screen. A test keeps the copies in sync.

### 11.3 Small UI (comma 4, mici)

The comma 4 runs the small UI. It has no Cruise or Vehicle panel, so neither page above can be reached on it (sunnypilot `d8863e59e`).

- `openpilot/selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` (new): `VehicleLayoutMici(NavScroller)`, with `HondaLearnedInfo`, the toggle as a `BigParamControl`, and reset behind `BigConfirmationDialog`. `car_brand()` gets the brand from `CarPlatformBundle` first and `CP.brand` second, cached on a 1 s tick. `HondaLearnedInfo` shows two pairs: `gas law` with `v2 next drive   brake x1.02` (the gas-law setting the car reads at the next ignition, and the brake value as a gain; the value scrolls in the 340 px card), and `D / ECON / S` with the engaged minutes (`412 / 1.4 / 1.3 min`, which fits). On a car the gas law does not apply to (`gas_law_applies()` above) the first pair is `brake` with `x1.02`. Rendered headless; they used to be the six pedal gains, which could not move, and the brake offset with the aero factor.
- `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py`: a `vehicle` `SettingsBigButton` (icon `icon_vehicle.png`) inserted with `items.insert(2, vehicle_btn)`, right after upstream's models row (index 3 before the 2026-09 merge, when sunnylink came first). It is visible when `car_brand() in ("", "honda")`, so a fingerprint that has not resolved yet never hides it. The `gateway` button at index 3 in the same hunk belongs to area A. `VehicleLayoutMici` is built without `back_callback`, following upstream `099143ad9`; `NavWidget` pops itself on swipe-down.

- **The speed-limit confirm on this car (2026-10-04, not car-gated: see `README.md`, area SL).** The Accord's RES/+
  maps to `accelCruise` and SET/- to `decelCruise` (`carstate.py`, `values.py`), and it runs non-PCM cruise, so the
  prompt is the comma 4's "Press + (or -) to confirm speed limit". Its text now says the button the confirm accepts
  (it said "-" for every "+", routes 114/115), and while it asks, a press the other way that would RAISE the set
  speed is ignored: on 114 a SET/- under the gas took the set speed from 50 to vEgo, 72.5 km/h (`cruise.py` clips a SET
  under the gas up to vEgo); now it stays 50 until the `+`. A wrong-way press that lowers the set speed, or leaves it,
  works as upstream's (115: SET/- during a `+` prompt, 60 -> 59), so for the prompt's 5 s (`PRE_ACTIVE_GUARD_PERIOD`,
  re-armed by every `speed_limit_changed`, so a flapping limit can chain prompts) the driver can always lower the set
  speed, with SET/- or with SET under the gas below it. That is owner decision 5, narrowed on 2026-10-05 (as written
  on 2026-10-04 it also blocked those lowering presses). With the comma 4 HUD's compact prompts on (default), the
  confirm is a banner top left and "set speed changed" draws nothing, so the MAX number shows the new set speed.
  Brake, cancel, main and the gas pedal are untouched, and engaging from MADS-only is not affected (the check sits in
  `_update_v_cruise_non_pcm`, which returns before it when not enabled).
- **The confirm prompt's grace (2026-10-06, not car-gated, area SL).** The asked-for button still confirms if it went
  down no more than `PRE_ACTIVE_CONFIRM_GRACE` = 1.0 s after the 5 s prompt timed out, on the same limit. On route 120
  a `+` 0.29 s late became a 1 km/h step (60 -> 61); on 121 a `-` 0.88 s late, held 1.26 s, walked 80 -> 70 -> 60 in
  10 km/h long-press steps. card decides (`cruise_ext.py`: it swallows the press - on release, or on the first
  long-press step if held, the rest of the hold swallowed too - and sets the set speed to the limit; the planner then
  goes inactive -> active on the matching set speed). The planner's own copy of the rule (`speed_limit_assist.py`,
  `update_buttons(release_toggle, pressed)`) can only accept a subset of what card accepted. The other button, a press
  after the 1.0 s and a press after the limit changed are ordinary set-speed steps; PCM cruise has no grace. Replays of
  both presses, and the tests: `CHANGELOG_SERIAL_STEERING.md`, 2026-10-06.
- **A lower limit is a prompt, never a silent rewrite (2026-10-09, round 5, not car-gated: non-PCM cruise, area SL).**
  Upstream asks for the confirm only for a new limit below 80 km/h; anything else rewrote the set speed with a 5 s
  "Auto adjusting to speed limit" chime. On the M4 eastbound that turned 110 into 100 at about -33.7950, 150.8440 and
  100 into 90 at about -33.8134, 150.9525 on every commute (routes c8, dd, fc, 10f), and on fc (812.94) the driver
  gassed back to 109 believing the set speed was still 110 and was braked again on release. Now, with Speed Limit
  Assist active, a new limit below the set speed is the "press - to confirm" prompt at any value
  (`apply_confirm_speed_threshold`, owner decision D5.1); SET/- confirms it, nothing changes the set speed until then,
  and an unanswered prompt leaves the driver's set speed. A higher limit still raises the set speed by itself, as
  upstream. And a new limit counts only once it has held for 1 s (`SpeedLimitChangeHold`, D5.2), on every car: a flicker
  shorter than that never prompts, and every prompt and rewrite comes 1 s after the map's change. Route dd's
  110/100/110/100 flips were 2.0 s apart, longer than the hold, so they are not suppressed: the replay shows two '-'
  prompts and, between them, the limit back at the set speed confirming itself (with its chime), the set speed never
  touched. Replays: `CHANGELOG_SERIAL_STEERING.md`, 2026-10-09. Two older fork rules come with the prompt, and now
  apply to every motorway drop, not only below 80 km/h: while the "press -" box is up, **+ does nothing** (for the whole
  5 s prompt, `update_speed_limit_assist_pre_active_raise_blocked`); and a **- pressed within about a second after the
  box has gone still confirms** the lower limit - 110 straight to 100 on one press (the confirm grace, 2026-10-06).
  After that second a - is an ordinary 1 km/h step. The hold itself is `speed_limit/change_hold.py` (fork-owned).
- **A press during 'now' is the confirm (push9, 2026-10-13, not car-gated: non-PCM cruise, area SL).** The card says
  'now' once the coming limit is under 50 m away (or held after mapd's look-ahead ended there), but the prompt only opened
  ~4 s later - past the sign, mapd's switch, the 1 s change hold - so a press at 'now' was a 1 km/h step and the driver
  pressed again when the prompt came (137 305.16; 134 313.44, 446.37, 716.18, 717.54). Now a + or - release at 'now', in
  the coming limit's direction, while engaged with SLA active or inactive, arms exactly that limit
  (`speed_limit/now_confirm.py`); when it comes into force SLA goes straight to active and card sets it on that frame,
  with no prompt. **sla-flash (owner, 2026-10-19: "Hold it and flash"): the press moves nothing until then.** push9's
  press was also its own 1 km/h step, so the set speed changed twice - the step at 'now', the limit at the sign - in 7
  of the 10 push9 windows, and the 2 phantoms left a step he undid by hand (13f 326.35, 141 236.62). Now card swallows
  the press (`cruise_ext._sla_now_consume`: its first action - a short press's release, a long press's first 0.5 s
  step - on the key the planner offers, `longitudinalPlanSP.speedLimit.assist.nowKey`, which carries every SLA guard)
  and reports it (`carStateSP.speedLimitNow`); the planner arms what card reports, never on its own view of the
  releases, and only on the offer it published last (`SpeedLimitAssist._now_arm_ok`: that limit on that key, an arm's
  own key only while that arm still stands, never a limit already in force). The report is a frame or two behind the
  press: review 1 found one landing after a hand-over to a DIFFERENT limit arming the announced one on the prompt for
  2.6 s (the cluster flashing 50 while the card asked + for 70), one landing after a phantom's lapse arming it for
  2.5 s more, and a restarted plannerd taking card's old tally for a press; each is now a press spent with nothing
  moved. The rest of that press, and the same key again while armed (in 'now' or in the 2.5 s after), are swallowed
  too; a long press is taken whole at its first 0.5 s step (DESIGN 15's default, his "does NOT change your set speed
  at all": a deliberate walk down before the sign is swallowed as well - the other way, long presses as ordinary
  walks, would bring the double change back on route 13e at 1043.59, his long '-' in the window). While armed the coming limit's number flashes on the card - lit 0.4 s, dim
  (25 %) 0.2 s, the Elesys radar's own rhythm - with 'sets 100 at sign' and no key, everything else solid, drawn from
  the planner's arm (`assist.armedLimit`) even once the look-ahead's hold has ended; the cluster flashes too (next
  bullet). At the sign: one change, straight to the limit, and the card cross-fades into the stock MAX number. The
  other key is still its ordinary step and disarms (`assist.armEnd` otherKey); a walk begun before 'now' (its first
  step came first) is never frozen nor armed, and if it runs past the armed target it disarms (push9 re-review: armed
  100, on to 99 - the 100 must not raise it back); any other limit is the prompt as before; the arm lapses 2.5 s after
  'now' closes (kept while the change hold holds), so a phantom 'now' (134 304.52, 674.56) moves nothing at all, and
  no arm lives longer than 12 s from its press (`now_confirm.ARM_MAX_S`, `armEnd` capped; 'now' still open, the key is
  offered again). In 'now' the card's key no longer steps the set speed. How long: 'now' opens 50 m before the sign
  (below ~12 km/h within 15 s of travel, the look-ahead's window), and a press holds until the limit is in force - in
  the replays 0.4-4.1 s after the press - or, for a phantom, 3 s past the sign plus 2.5 s: about 8.5 s at 60 km/h,
  11.5 s at 30 km/h, and the 12 s cap below that. To slow more before the sign: brake, the other key, or wait for it. While a press would count, the card says 'press - to set 100' in place of 'now'
  (both styles) - since sla-flash from the planner's own offer, so exactly when card would take it (push9 guessed it in
  the UI and also said it in the prompt, pending, on the first frame after engaging); with cruise off it still says
  'now' ('-' engages). How each arm ended is in `assist.armEnd`; `armSeq` counts them. The planner and the HUD run one state machine
  (`now_confirm.ComingLimit`, push9 review: a second copy disagreed on 10f/112/129/12a, once opening 'now' with no card on
  screen), and over 22 routes the planner's windows sit on the HUD's to the frame. The key is the way of limit + offset,
  the number the card shows; with `HudApproachingLimit` off nothing arms; a press while the card is hidden by an
  alert or the lane-block tile still counts (the cluster flash is the feedback then). Replays:
  `CHANGELOG_SERIAL_STEERING.md`, push9 and sla-flash.
- **The cluster flashes the armed set speed (sla-flash, 2026-10-19, HONDA_ELESYS only, openpilot longitudinal only).**
  While a press made at 'now' is held, openpilot's own ACC_HUD (`0x30C`, bus 0) shows the set speed it will become as
  the Elesys radar shows a speed it is building up to: `CRUISE_SPEED` = N for 4 frames, 255 (blank, `ACC_ON` still 1)
  for 2, at 10 Hz - 0.4 s / 0.2 s, the pattern of every stock flash in 13 fully-stock drives (never two numbers
  alternating). It starts lit on the first send after the arm (about 0.2 s after the press: card, planner, controlsd,
  the next 10 Hz send) and goes solid on the first send where the set speed is N (the take) or the arm has gone.
  The path: `assist.armedSpeed` (m/s) -> controlsd (`controlsd_ext.now_flash_speed`, held 2 cycles after it drops so
  the take never shows the old number for one send) -> `CarControlSP.speedLimitNowFlash` (flat, through
  `convert_carControlSP`) -> `carcontroller.py`, `hondacan.now_flash_speed`. Only `CRUISE_SPEED` (byte 3) and the
  checksum change; `hudControl.setSpeed`, `0x500 SET_SPEED` and every other byte are as before. Who reads it:

  | reader | reads openpilot's `CRUISE_SPEED`? |
  |---|---|
  | the cluster | yes - it is the point |
  | panda `honda_tx_hook` | no: the `0x30C` check reads bytes 0-2 (`PCM_SPEED`, `PCM_GAS`) only; relay and forwarding are by address |
  | honda carstate | no: it reads the radar's `0x30C` (bus 2); `cruiseState.speed` is `0x324` |
  | the PCM (`0x324 CRUISE_SPEED_PCM`) | no: it followed none of openpilot's 19 changes on route 143 |
  | selfdrived `cruiseMismatch` | no: `ACC_STATUS` |
  | the EPS-LKAS board | no: it forwards `0x30C` unparsed |
  | `0x500 SET_SPEED` | separate: `hudControl.setSpeed`, untouched |
  | stock ACC mode (`HondaElesysStockAcc`) | openpilot sends no `0x30C` there, and Speed Limit Assist is unavailable |

  The `SCM_BUTTONS` stand-down, CMBS/AEB and panda safety are untouched (`test_elesys.py` `TestElesysNowFlash`,
  `test_elesys_stock_acc.py`: the pattern, solid at the take and at a drop, 255 when not visible, imperial, every other
  frame byte-identical, other Hondas byte-identical, every flashed frame allowed by the safety tx hook and a byte-3
  change never altering its verdict, and - review 2 - a take landing in the flash's dark phase is solid N on that very
  send). The flash shows the destination, as stock does; the car still holds the old set speed until the sign, which
  the comma card says ('sets 100 at sign') and the split card's MAX half shows. The stock-plus card puts the coming
  limit in the MAX number's place, so in that style the held set speed is on neither display while armed (only the
  speedometer shows what the car holds); the owner drives the split card.

### 11.4 sunnylink

- `settings_ui_src/pages/vehicle.yaml`: a section with `id: honda` (it compiles to `vehicle_settings.honda` in `settings_ui.json`) holding the two toggles, `HondaDynamicTuningEnabled` and `HondaElesysGasLawV2` ("Measured Gas Pedal Law (2013-15 Accord)", whose description says what it does and that it applies at the next drive, and whose details say the per-mode time is counted only with the learning toggle on), each with `needs_onroad_cycle: true` and offroad-only enablement. The 2026-10 hunks there, in `cruise.yaml`, `params_keys.h` and `statsd.py` carry `FORK(HONDA_ELESYS)`. Since then the section also holds "Stock ACC (testing)" (`HondaElesysStockAcc`, 2026-10-04) and, batch 3, the pump rule - "Quiet pump at stops" (`HondaElesysPumpC1b`) since 2026-10-06, "Quieter brake pump" (`HondaElesysPumpV6`) before - both with the `offroad` macro and `needs_onroad_cycle`. Batch 3's "Measured brake law (testing)" (`HondaElesysBrakeLawV2`) was here too until round 5 (7.10).
- `settings_ui_src/pages/cruise.yaml`: a section `honda_dynamic_learning`, visible when the capability `brand == honda`. It shows `HondaDynBrakeGain` and the three `HondaDynModeSec*` keys as `widget: info` rows. They carry no `step`: the `step: 0.001` display hint that `d11d2c9a8` added is not an info-widget field, and on its own, without `min`/`max`, it failed upstream's `test_settings_schema` `test_numeric_constraints`, so it was removed in the 2026-09 merge.
- Two lessons, both now pinned by tests. First, `blocked: true` means DEVICE_ONLY and the dashboard hides the row, so it must not be used to mean read-only (`d033e3dbd`). Second, info rows did not render inside a brand's vehicle section, only on a page, so they live on the Cruise page (`4131c8778`). A key may appear in only one place.
- `settings_ui.json` is generated by `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py`. Recompile it; never merge it by hand.

**One inconsistent text left:** sunnylink's `cruise.yaml` shows the brake value as the raw param (an offset, described as `0.00 until it has learned anything. Positive adds brake, negative trims it`), while the `hondadyn` log, the big UI and (since 2026-10) the mici page show it as a gain. The toggle's `details` no longer points at rows "below"; they are on the Cruise page.

### 11.5 `openpilot/sunnypilot/sunnylink/statsd.py`

Adds `HondaDynamicTuningEnabled`, `HondaDynBrakeGain`, the three `HondaDynModeSec*` totals, `HondaElesysGasLawV2`, `HondaElesysStockAcc` and (batch 3) the pump rule's key (`HondaElesysPumpC1b` since 2026-10-06; `HondaElesysBrakeLawV2` too until round 5) to the `sp_stats` device-params list, so they can be watched without pulling a route. The toggle is included because a gain of 1.000 could mean either converged or never switched on.

### 11.6 Test

`openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` parses the files rather than importing them, so it does not need raylib. Its tests:

- `test_learned_params_are_registered_as_floats`
- `test_toggles_are_registered_and_backed_up`
- `test_mode_time_keys_cover_every_slot`
- `test_gas_law_param_is_registered_on_by_default_and_backed_up`
- `test_retired_keys_are_gone_everywhere`
- `test_statsd_reports_only_registered_honda_keys`
- `test_the_toggle_lives_on_the_honda_panel_only` (until round 5 `test_both_panels_drive_the_same_params`, when the Cruise panel had a copy)
- `test_honda_panel_publishes_its_items`
- `test_mici_page_shares_the_panel_params`
- `test_mici_settings_registers_the_vehicle_page`
- `test_sunnylink_exposes_the_same_toggles`
- `test_sunnylink_exposes_the_gas_law_toggle`
- `test_sunnylink_learned_values_are_read_only_and_on_a_page`
- `test_sunnylink_keys_are_registered_and_unique`
- `test_panel_defaults_match_the_tuner`
- since 2026-10-04, for the stock ACC toggle (15.6): `test_stock_acc_is_registered_off_and_never_restored_from_a_backup`, `test_stock_acc_reaches_the_hook_under_one_name`, `test_stock_acc_toggle_is_offroad_only_on_both_screens`, `test_sunnylink_stock_acc_is_offroad_only_and_never_the_longitudinal_macro`
- since batch 3 (2026-10-05), for the pump rule (7.2): `test_pump_rule_is_registered_backed_up_with_its_default`, `test_pump_rule_reaches_the_hook_under_one_name` (statsd included), `test_pump_rule_toggle_is_offroad_only_on_both_screens`, `test_sunnylink_exposes_pump_rule_offroad_only` (they covered the brake law too until round 5); since 2026-10-06 the key is `HondaElesysPumpC1b` and `test_retired_keys_are_gone_everywhere` also fails on `HondaElesysPumpV6` (in the params, both screens, sunnylink, statsd, `sunnylinkd.py`, `initialize_params()` and `_initialize_honda()`), and since round 5 on `HondaElesysBrakeLawV2`, `BRAKE_LAW_V2_PARAM`, `brake_law_v2` and `elesys_brake` (`carcontroller.py` included)

`test_panel_defaults_match_the_tuner` also checks the panel's `MODE_SLOTS`, `GAS_LAW_PARAM` and `GAS_LAW_DEFAULT` against `elesys_gas.py`. None of them checks the display inconsistency in 11.4.

---

## 12. Tests

| test | repo | how to run | what it pins |
|---|---|---|---|
| `opendbc/car/honda/tests/test_elesys.py` | opendbc | `python -m unittest opendbc.car.honda.tests.test_elesys` (143 tests on push10: 130 before round 8, 136 with it, and sla-flash's 7 in `TestElesysNowFlash`) | category membership and dispatch (`compute_gas_brake(accel, speed, CP)`); the upstream Nidec map untouched; the Elesys gas/brake golden table; the pump (20 cases); the gas curve; the units bit (`create_brake_command(..., is_metric=, elesys=)`); the gear dwell; the stock AEB truth table and DBC signal names (not the `carstate.py` branch, 6.3). Since 2026-10 also the lateral tune (2.4, 5.1): `TestElesysTorquePrior`, `TestElesysSteerDelay`, and the area-B `TestElesysReportedTorque`, `TestElesysReportedTorqueSeam`, `TestElesysTorqueScale`; since 2026-10-04 `TestElesysKeyOffSteerStatus` (6.4); since 2026-10-06 `TestBrakePumpC1b` (22: the onset burst at the first frame, rises with no gap, the crawl run and its edges, the firm run, standstill silence, the one hold-build and hold-rise burst, the creep guard, release, NaN speed, and the rule against c1weak's replay rule on a random trace), `TestElesysPumpRuleSelection` (4: flag 16 runs v5; since round 5 flag 32 runs today's law, 7.10) and `TestElesysC1bWithTheSoftStop` (2, with the 0x1FA pump bit through the soft stop's crawl run), 7.2 (they replaced batch 3's `TestBrakePumpC1`); since 2026-10-12 `TestElesysFullBrakeNoWindCredit` (15, 7.11) and `TestElesysSteeringPressedDebounce` (9, 10.2); since round 8 `TestElesysPidAccelLimits` (6 since the round 8 review: the floor follows a climb, 7.12), and the C1b tests with the soft stop's cap at 105 |
| `opendbc/car/honda/tests/test_elesys_pump_brake_flags.py` | opendbc | `python -m unittest opendbc.car.honda.tests.test_elesys_pump_brake_flags` (7 tests) | flag 64 from `_initialize_honda()`: off byte-identical to no hook, the setting only its own bit, the retired `HondaElesysPumpV6` / flag 16 and (round 5) `HondaElesysBrakeLawV2` / flag 32 never read or set, never in stock ACC mode, without openpilot long or on another Honda (7.2, 7.10, 11) |
| `openpilot/sunnypilot/selfdrive/car/tests/test_honda_elesys_pump_brake.py` | sunnypilot | `python tools/test_runner.py <path>` (7 tests) | the default (C1b on) reaching the hook through `initialize_params()`, the retired keys neither registered nor handed over, C1b on and flags 16/32 clear whatever their files say (7.2, 7.10, 11) |
| `opendbc/sunnypilot/car/honda/test_elesys_gas.py` | opendbc | `python -m unittest opendbc.sunnypilot.car.honda.test_elesys_gas` (38 tests) | the gas law and the launch cap, 9.2 |
| `opendbc/sunnypilot/car/honda/test_elesys_stop.py` | opendbc | `python -m unittest opendbc.sunnypilot.car.honda.test_elesys_stop` (29 tests) | the soft final stop, 7.8 (since round 8 the cap at 105, inside the pumps' crawl run) |
| `opendbc/safety/tests/test_honda.py` (`TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety`; since 2026-10-04 `TestHondaElesysStockAccSafety`, `TestHondaElesysStockAccStanddownConflictSafety`) and `common.py` | opendbc | `python -m unittest opendbc.safety.tests.test_honda`; builds `libsafety` on import (1091 run, OK, skipped=73, 2026-10-05, with stock ACC mode and batch 2; 942 after the 2026-09 merge) | section 8, 15.7 |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | opendbc | **standalone script**: `python <file>` with opendbc on `PYTHONPATH` | the tuner on its own, sections 1-5 and 10-16: toggle off is a no-op, pitch, the retired pedal and aero learners stay retired, the per-mode data counter, brake, params, importing without openpilot, three rounds of review regressions, drive-mode gating, the `hondadyn` line |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | opendbc | **standalone script**, or unittest discovery through its `TestDynamicTuningIntegration` wrapper | the real `CarController`, frame by frame: [1] toggle off matches stock, [2] toggle on, [3] gas and brake never together, [4] the standstill hold is not scaled by the learned gain, [5] a disengage unwinds the brake gain, [6] the interceptor owns the gas at every speed (decodes `PCM_GAS`), [9] fuel and odometer, [16] the gas law v1/v2 through `CarController` with the brake command identical under both, [17] `update()` never raises on odd inputs and 0x1FA goes out every even frame, [17b] a NaN `vEgo` in the brake block, [18] `CRUISE_OVERRIDE` on every `BRAKE_COMMAND`, brake 0 when `longActive` drops and at most one nonzero `BRAKE_COMMAND` after a pedal edge, [19] the soft final stop through `CarController` (only lowers, the hold byte for byte, the gate, the learners untouched, never raises). Sections 7, 8, 10-15 and 14b are area B |
| `openpilot/selfdrive/controls/tests/test_stopping_debounce.py` | sunnypilot | `python tools/test_runner.py <file>` (17 tests) | 10.1 |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_reentry_ramp.py` | sunnypilot | runner (27 tests, round 8; 20 before its review) | 10.8: the ramp's window, rate, start, exceptions (FCW, crash, the MPC's plan and trajectory, the plan, a hard-braking lead), the gas gate (an engagement is upstream's), speed gate and log line; this car only, op long only, not in stock ACC mode; the real planner and MPC through an override and a lift-off |
| `openpilot/selfdrive/controls/tests/test_elesys_pid_floor.py` | sunnypilot | runner (6 tests, round 8; 4 before its review) | 7.12: the real `LongControl` with the -3.2 floor: the windup stops at the floor, the same full brake, a quicker release, bit-identical above the floor; on a climb the floor follows the pitch and the brake stays as full as with -4.0; the controlsd hunk pinned |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | sunnypilot | runner (9 tests) | 10.2 |
| `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | sunnypilot | runner (1 test) | 10.5 |
| `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` | sunnypilot | runner (26 tests) | section 11, and 15.6 |
| `opendbc/car/honda/tests/test_elesys_stock_acc.py` | opendbc | `python -m unittest opendbc.car.honda.tests.test_elesys_stock_acc` (17 tests) | 15.2-15.3, 15.9 |
| `opendbc/car/honda/tests/test_elesys_radar.py` | opendbc | `python -m unittest opendbc.car.honda.tests.test_elesys_radar` (12 tests; 4 before 2026-10-24) | 4.8, 5.2: `REL_SPEED` 37:14 signed at 1/64 m/s on all 13 track messages; a logged frame's raw value; logged frames of route 113 through the real `RadarInterface` (a stationary object reads -vEgo and its `dRel` changes at `vRel`); a logged B-group track's range rate. All four fail on the old 1/128 DBC. Since 2026-10-24 the track slots, on logged frames of routes 113, 141 and dd: `COASTING` (not `FLAG_B22`), `NEW_TRACK` and `LONG_DIST` on all 13 messages; the update on the batch with 0x424 (141 t=102.7, where 0x424 came a batch after 0x423); a car at 255.34 m tracked under one id; a slot empty only at 0x7FFF; a rising `NEW_TRACK` on a frame that continues the slot's object is a new id (dd t=2647.96; the review's mutation check found no test pinned it); a held `NEW_TRACK` on the same object keeps its id and on three different objects gives three; measured is not `COASTING`. On push12's `radar_interface.py` the module does not import (no `elesys_same_object()`); lent that helper, seven of the eight fail there and the eighth on push12's DBC (most of them first because it publishes only on 0x423, which these frames leave out) |
| `openpilot/sunnypilot/selfdrive/car/tests/test_honda_stock_acc.py` | sunnypilot | runner (15 tests) | 15.5 |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_elesys_radar_guard.py` | sunnypilot | runner (37 tests; 26 before round 8, 34 before its review) | 5.3: logged frames through radard with the guard on and off. Since round 8 the long-range rule: 143 t=169.1 taken, c8/c9's followed car kept, each limit, the run, the fallback condition, leadTwo and other cars unchanged; since the review the moving-car veto (0e1 t=887 unchanged) and the gate (not in stock ACC mode) (`fixtures/elesys_long_range_frames.json.gz`). 10f t=2740-2745: no stationary radar lead without a confident camera lead, only the hold. c8 t=2320-2322: the camera's lead, not the stationary returns. c0 t=252-265: the stop behind a stopped queue unchanged. 10f t=2757 and c9 t=288.9: a moving vehicle at walking pace taken as upstream takes it. 07 t=1128.2: a speed spike on a stationary return never moving. 06 t=1306-1309: the night give-up, held. Also the gate and the constants, the tolerance by range, the one-sided check, the override's conditions, the camera hysteresis, counting in radar updates, the range-backed latch, a spike, a cut-in, a track coming toward the car, and the hold. The 10f near-range and c8 tests fail with the guard off; 06, 07 and both 10f windows fail without the hold; 07 fails without the range test |
| `openpilot/sunnypilot/mads/tests/test_mads_honda_stock_acc.py` | sunnypilot | runner (14 tests) | 15.5 |
| `openpilot/selfdrive/locationd/test/test_torqued_elesys.py` | sunnypilot | runner (17 tests; 11 before round 8) | 2.4: prior and seed before any point, a zero offset as upstream, a changed prior discards the cache, a reported 0 adds no point, the seed survives EnforceTorqueControl / NNLC while other cars match upstream's re-run; since round 8 the 1.10 floor (`TestTorquedElesysFloor`, 6) |
| `openpilot/selfdrive/locationd/test/test_lagd_elesys.py` | sunnypilot | runner (5 tests) | 5.1: the lag fallbacks are 0.38 s, a learned cache survives |
| `openpilot/sunnypilot/selfdrive/locationd/tests/test_lat_speed_split.py` | sunnypilot | runner (17 tests; 10 before round 8) | 9.3 lateral: the moment fit equals `estimate_params()`, exact combination across drives (moments and controller sums), the hands x speed cells from torqued's own override window, the controller sums only while fresh, torqued's points only, `lateralTorqueParameters` identical with and without, torqued's own SubMaster unchanged and the shadow's never blocking, the gating, the log cadence and its build tags, never raising, torqued as upstream when the module cannot be imported |
| `openpilot/sunnypilot/tools/tests/test_shadow_learn_report.py` | sunnypilot | runner (11 tests; 7 before round 8) | 9.3 the report: no pandas reads the rlogs, real lines parse and combine by counts, routes never overwrite each other, older lines read; (batch 3) different builds never pool, tags read as text and untagged lines group apart, a pre-cap launch is discarded; (round 8) `latsplit` v2 lines parse and report every cell, pool only within one build, and v1 lines pool apart |
| `openpilot/sunnypilot/tools/tests/test_brake_route_check.py` | sunnypilot | runner (28 tests; the parquet one runs only with pyarrow installed, the two on routes `120`/`121` only where those routes are - `BRC_SUNNY_LOGS`, default `/mnt/s/OP/sunny_logs`) | 9.4: the DBC's bits through opendbc's CANPacker, the controller's own pump functions replayed and the fallback transcription equal to C1b, C1b against C1 in a replay, a synthetic route written as a real rlog end to end (the rule from CarParamsSP - C1b, and the retired C1 from flag 16 -, replay match 1.0, a held hold passes, XMISSION motion and a brake error abort, the CLI and JSON with the acceptance block), parquet equal to rlog, every verdict and every C1b acceptance check at its threshold, the clean-application metrics on a frame table (against `aTarget`, not the command), the acceptance checks judged only for a C1b arm against a v5 baseline, c1weak's adjustment taking out a road-mix difference, C1b's own light-hold and first-frame standstill bursts inside its design and a top-up outside it, c1weak's numbers reproduced on `120`/`121`, rlog over qlog |
| `opendbc/sunnypilot/car/honda/test_shadow_learn.py` | opendbc | `python -m unittest opendbc.sunnypilot.car.honda.test_shadow_learn` (31 tests) | 9.3 longitudinal: byte-identical CAN through the real `CarController` with and without the shadow, a raising shadow switched off, one that cannot be built, bands and bounds, every gate, the clean second after an override or engagement, the 0x17C pedal confirmation, lead launches apart, the interceptor gate, the grade, the log cadence and `bgain`, garbage in; (batch 3, 9.1/9.3) a launch from a held stop sampled from first motion, the tuner off with the shadow on against a tuner with no logging mode (CAN and outputs identical), the tags against the flags and `GitCommit`, the manual moving-time counter, persisting the counters (not the learned gain, which keeps its 60 s cadence) at a disengage, the synchronous exit flush (counters only), `write_now()` never overtaken and keeping a queued 60 s batch, the exit hook only on the device |
| `opendbc/sunnypilot/car/honda/test_vsa_fault.py` | opendbc | `python -m unittest opendbc.sunnypilot.car.honda.test_vsa_fault` (33 tests) | 6.6: real frames from 110 and 112 (vsaFault on the frame accFaulted first is), 111 and 113 (stored 0.5 s after b4.0 first appears, also with card starting 2.1 s late; 113's clear), 10f (a bulb check sets nothing, nor one held 4.08 s) and comma route 69 (the b3.5+b4.1 lamp state sets nothing), all through the real `CarInterface`; the DBC decode of the onset, stored and bulb-check frames and of 0x1AA/0x3D9; `VEHICLE_DYNAMICS` liveness-exempt on this car only, its counter unchecked and its checksum checked; other Hondas read False; garbage, checksum-valid random and missing frames, and a monitor that raises, never make `update()` raise; the window (bulb bits only), debounces and silence |
| `openpilot/sunnypilot/selfdrive/selfdrived/tests/test_vsa_fault_alert.py` | sunnypilot | runner (47 tests) | 10.6: the helper's events, `cleared`, filter (both lists, the EPS-latch alerts) and `late_alerts()`; the onset with `vsaFault` one frame late (engaged, not engaged, a press on that frame); a live fault with CAN invalid (refused, live texts); the EPS-latch alert across route 113's clear (no restart advice, a real latch afterwards still announced); card sending `carStateSP` first; event classes (live adds nothing `accFaulted` does not; stored is NO_ENTRY and PERMANENT only; `carNotReady` stays NO_ENTRY only); texts (sanity rules, ASCII, no "restart", the mici renderer's fit with the real fonts); selfdrived's state machine (live disengages as before with the VSA text, stored refuses SET with the VSA text and clears, never disengages); MADS refused but never disabled; the `AlertManager` path (one sound per fault, driver monitoring keeps the screen, the EPS banner does not take over); selfdrived's wiring; the `CarStateSP` capnp/dataclass agreement and round trip |

Upstream removed pytest (`98e7c4f98`, `ac4ab9a9b`). Its `tools/test_runner.py` collects only `unittest.TestCase` classes, and in a full run drops plain `def test_*` modules without a word. In the 2026-09 merge every sunnypilot test above became a `TestCase` or `OpenpilotTestCase`. `test_stopping_debounce.py` no longer stubs `cereal` and `openpilot` in `sys.modules`: it imports the real modules, so it can no longer poison a runner process. After the merge all of them pass in WSL, and upstream's whole suite passes with them (1600 passed, 0 failed).

**The two opendbc tuner scripts still run at import.** Every check runs when the module is imported, and opendbc's `lefthook run test` runs `unittest-parallel -j4`, whose discovery imports every `test*.py` module:

- `test_dynamic_tuning_integration.py` calls `sys.exit(1)` only under `__main__` since the merge, and `TestDynamicTuningIntegration.test_all_checks_pass` asserts that the list of failures is empty, so discovery reports a real pass or fail. It still monkeypatches `dt._open_params` at import (`build()`).
- `test_dynamic_tuning.py` still calls `sys.exit(1)` at import when a check fails. It passes today, so discovery is unaffected, but a failure would appear as a module that failed to import. Give it the same wrapper the next time it is touched.

The fork's commit messages say the sunnypilot test suites could not run on the owner's Windows checkout before the merge, because the old `openpilot/common` symlinks and `cereal/car.capnp` checked out as text files there; they were run with shims or on Linux. Since the merge the tests are run in WSL (`~/sp-merge`).

---

## 13. Other changes

### 13.1 `.gitmodules` and the `opendbc_repo` gitlink

The `[submodule "opendbc"]` URL changed from `https://github.com/sunnypilot/opendbc.git` to `https://github.com/SoRadGaming/opendbc.git`, with `branch = sp-master` (sunnypilot `26edc2baa`). The gitlink pins the fork's `sp-master`: `cf583b37` before the 2026-09 merge, the merged `sp-master` after it, never upstream's opendbc commit. Every opendbc change in this document reaches the car only through this pin, so bump it in the same commit as any sunnypilot change that depends on it, as the fork's `bump opendbc` commits do.

Upstream edited other lines of `.gitmodules` (the `msgq` URL, now `sunnypilot/msgq`, and the `neural_network_data` path, now `openpilot/sunnypilot/neural_network_data`). The 2026-09 merge took those automatically and kept the fork's opendbc URL and branch. After a merge, run `git submodule sync && git submodule update --init --recursive`.

### 13.2 `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py`

Three `open()` calls get `encoding="utf-8"`, and the writer pins `newline="\n"`. On Windows the default cp1252 encoding mangled `m/s²` elsewhere in the JSON, and line endings churned between platforms (sunnypilot `d033e3dbd`). The change is not car-specific and carries no marker. Keep it unless upstream has fixed the same thing.

---

## 14. Upstream merges

The goal of every merge is to bring the branch up to date with upstream while keeping this car's behaviour, and giving every other car upstream's. The last one was the 2026-09-27 sync with sunnypilot `a5f44653d` and opendbc `f95f996f`; 14.2 and 14.3 record what it did in this area, as the reference for what each fork hunk should look like after the next one. What it changed on the road is in [UPSTREAM-2026-09.md](UPSTREAM-2026-09.md).

### 14.1 Order

1. Merge upstream opendbc into `sp-master` first, then fix, test and push.
2. Merge sunnypilot next. The `opendbc_repo` pointer always conflicts; resolve it by pinning the merged opendbc commit from step 1.
3. The device's panda firmware is built from `honda.h` (8.4). On the device, pandad reflashes the panda by itself at the first start when the firmware signature changes, as it did after the 2026-09 sync.

### 14.2 opendbc: what the 2026-09 merge did

The merge of `f95f996f` into `c61cfd9b` conflicted in five files: `carcontroller.py`, `carstate.py`, `hondacan.py`, `interface.py` and `values.py`. Everything else merged automatically, including `honda.h` and `test_honda.py`, which upstream had not touched.

Every one of those conflicts had the same cause: an upstream refactor away from the `HONDA_*` platform sets as the idiom. Upstream deleted `HONDA_NIDEC_ALT_PCM_ACCEL`, `HONDA_NIDEC_ALT_SCM_MESSAGES` and `HONDA_BOSCH_TJA_CONTROL`, turned the `HONDA_BOSCH*` sets into `frozenset(c for c in CAR if c.config.flags & ...)` defined after `DBC`, tests `CP.flags & HondaFlags.X` almost everywhere, and made functions that took `car_fingerprint` take `CP`, or nothing.

| fork code before the merge | upstream | the merged code |
|---|---|---|
| `HONDA_ELESYS = CAR.with_flags(HondaFlags.ELESYS)` | `with_flags()` still exists in sunnypilot's opendbc, but Honda no longer uses it | `HONDA_ELESYS = frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)` after `HONDA_BOSCH_CANFD`. The name is kept: `radar_interface.py`, `carstate_ext.py`, `gas_interceptor.py` and `test_elesys.py` import it and merged without a conflict, so they would have failed to import without it |
| `candidate in HONDA_ELESYS` / `self.CP.carFingerprint in HONDA_ELESYS` | `CP.flags & HondaFlags.X` | unchanged; both forms work |
| `compute_gas_brake(accel, speed, fingerprint)` with `elif fingerprint in HONDA_ELESYS` | `compute_gas_brake(accel, speed, CP)` with `if CP.flags & HondaFlags.BOSCH` | `elif CP.carFingerprint in HONDA_ELESYS: return compute_gb_honda_elesys(accel, speed)`. `TestElesysCategory.test_dispatch` passes a `CP` |
| `create_brake_command(..., car_fingerprint, stock_brake, is_metric, CP_SP)` | `create_brake_command(packer, CAN, apply_brake, pump_on, pcm_override, pcm_cancel_cmd, fcw, stock_brake, CP_SP)` | `(..., stock_brake, CP_SP, is_metric=True, elesys=False)`, called by keyword (7.3). `TestBrakeCommandUnitsBit._frame()` passes the two by keyword |
| `actuator_hysteresis(brake, braking, brake_steady, v_ego, car_fingerprint)` | `actuator_hysteresis(brake, braking, brake_steady)` | upstream's; the fork never changed this function |
| imports of the removed sets | deleted | upstream's import lines, plus `HONDA_ELESYS` where it is used |
| explicit lists in `FW_QUERY_CONFIG.non_essential_ecus` | comprehensions over flags | `CAR.HONDA_ACCORD_9G_AU` re-added to the explicit part of both the `Ecu.eps` and the `Ecu.vsa` list |
| `interface.py`: `if candidate in HONDA_ELESYS and ret.openpilotLongitudinalControl:` (safety param) | a series of `if ret.flags & HondaFlags.X:` lines | kept as it was, after upstream's lines |
| `interface.py`: `ret.vEgoStopping = 0.8` | `vEgoStopping` is in `CarParams.deprecated`; assigning it raises `AttributeError: struct has no such member`, so `_get_params()` would have thrown and card could not have built the car | **deleted**. The 0.8 m/s stopping speed is carried in sunnypilot's `stopping_tune.py` (10.1) |
| `carstate.py`: the `LKAS_PROBLEM` branch under `if self.CP.carFingerprint not in HONDA_BOSCH:` | `if not (self.CP.flags & HondaFlags.BOSCH):` | the Elesys/else split inside the new condition (6.2) |
| `_get_params_sp()`, which the fork had not touched | `stock_cp.minEnableSpeed = -1. if ret.enableGasInterceptor else ...` (`4455464a`) | `... if ret.enableGasInterceptor and candidate not in HONDA_ELESYS else ...`: this car keeps 19 mph (5.1) |

Fork lines that sat outside the conflict markers and named things upstream removed (`elif fingerprint in HONDA_ELESYS:`, `adjust_accel`, the whole `compute_gb_honda_elesys`, `... if car_fingerprint in HONDA_ELESYS else 1`) were rewritten; `ruff check` (F821) would have caught any that survived.

Other upstream changes that reached these files without a conflict:

- `carstate_ext.py`: `ret.blockPcmEnable = ret.brakeHoldActive and not self.CP_SP.enableGasInterceptor` in the hybrid brake-hold branch. It does not affect this car.
- `radar_interface.py`: upstream removed the `aRel`/`yvRel`/`measured` lines, and `track_id` moved to the base class. Taken as they are.
- `carstate.py`: `ret.brakeDEPRECATED` is `ret.deprecated.brake`.
- `_gearbox_common.dbc` gained `11 B`, which is not copied into `_gearbox_legacy.dbc` (4.7).
- `structs.py`: upstream always loads `car.capnp` from opendbc. The fork's `CarControlSP`/`CarStateSP` additions merged automatically (6.5).
- `routes.py`, `car_list.json`, `fingerprints.py` and `safety/tests/common.py` merged automatically. `common.py` (a 115-line upstream change) kept the Elesys exceptions in the right function, and gained the Elesys-only `0x500` exemption (8.3).

Checked after the merge: `TestElesysGearDecode._cs()` builds the car through `get_params()` and passes, which it would not have with the `vEgoStopping` line; `test_car_interfaces` passes for `HONDA_ACCORD_9G_AU`; the generated `honda_accord_au_2015_can_generated.dbc` is byte-identical to the pre-merge one; replaying 10 real segments (routes `00000103` and `fd`, 312 engaged frames at a standstill, two with the tuner on) through both `CarInterface`s gave identical `carState`, `carStateSP`, actuators and transmitted CAN, and zero tuner `Params` write errors under upstream's ctypes `Params`.

### 14.3 sunnypilot: what the 2026-09 merge did

**Upstream moved the whole tree under `openpilot/`**: `openpilot/selfdrive/...`, `openpilot/sunnypilot/...`, `openpilot/common/...`, `openpilot/cereal/...`. Git followed the renames of files the fork had modified. The files the fork had **added** (for this area `test_stopping_debounce.py`, `test_lane_change_nudge.py`, mici `vehicle.py`, `test_honda_dynamic_settings.py` and `test_car_control_sp_seam.py`) were file-location conflicts and were accepted at their `openpilot/` paths. The old `openpilot/common`, `openpilot/selfdrive` and `openpilot/sunnypilot` symlinks were file/directory conflicts, resolved to upstream's directories. `selfdrive/modeld/modeld.py` was a modify/delete: the fork's edits were re-made in `openpilot/selfdrive/modeld/modeld.py`.

Content conflicts in files this document covers: `desire_helper.py`, `longcontrol.py`, mici `settings.py` and `openpilot/sunnypilot/modeld_v2/modeld.py` (area C hunks), plus `controlsd.py`, `selfdrived.py` and `mads.py` (area B, see LKAS-GATEWAY-PROTOCOL.md §14). These merged automatically: `card.py`, `helpers.py`, `params_keys.h`, `custom.capnp`, `latcontrol*.py`, `controlsd_ext.py`, `vehicle.yaml`, `settings_ui.json` and `statsd.py`.

File by file:

- **`longcontrol.py`.** Upstream removed the `starting` state and its use of `CP.startingState`, `vEgoStarting` and `startAccel`; dropped `CP` and `v_ego` from `long_control_state_trans(CP_SP, active, long_control_state, should_stop, brake_pressed, cruise_standstill)`; replaced `stoppingDecelRate` with a fixed `1.0 * DT_CTRL` ramp; and made the PID integral-only. The debounce was re-applied as a post-step around the new call, on the stopping → pid edge, and the ramp reads `self.stopping_decel_rate` (10.1).
- **The stop threshold.** Upstream no longer reads `vEgoStopping` at all: `should_stop(v_ego, a_target)` returns `v_ego < 0.3 and a_target < 0.1`, below the car's 0.55-0.7 m/s approach crawl, which would have brought back the red-light behaviour `S:/OP/redlight_overshoot_findings.md` recorded before the 0.8 fix. The merge kept 0.8 m/s and 0.8 m/s³ for this car in the new `stopping_tune.py`, read by `drive_helpers.py`, `longitudinal_planner.py`, `longcontrol.py`, `joystickd.py` and `maneuversd.py` (10.1). The debounce's test and the two tuner comments that quoted `vEgoStopping = 0.8` were updated with it.
- **`desire_helper.py`.** Upstream removed `DESIRES`, `lane_change_ll_prob` and `keep_pulse_timer`, added `LANE_CHANGE_START_TIME`, and gave `update()` two new parameters, `left_edge_detected=False, right_edge_detected=False`. Re-applied: `NUDGE_FIRM` and `NUDGE_HOLD_FRAMES`, the `car_fingerprint` constructor argument, `self.nudge_frames` reset where upstream resets `lane_change_timer` on entry to `preLaneChange`, the firm-or-held test before `blindspot_detected`, and `driver_torque_stale=False` after the new parameters, passed by keyword from both modeld call sites (10.2).
- **modeld.** Both `openpilot/selfdrive/modeld/modeld.py` and `openpilot/sunnypilot/modeld_v2/modeld.py` subscribe to `carStateSP` in upstream's renamed `SubMaster`, construct `DesireHelper(CP.carFingerprint)`, and pass `driver_torque_stale=` by keyword.
- **`controlsd.py`, `selfdrived.py`.** Upstream renamed services (`liveDelay` → `lateralDelay`, `liveParameters` → `vehicleParameters`, `liveTorqueParameters` → `lateralTorqueParameters`, …). `'carStateSP'` is back in both `SubMaster` lists; the gateway lines sit before upstream's 3-value `LaC.update()`.
- **mici `settings.py`.** Upstream builds panels without `back_callback` and inserts models at 1 and sunnylink at 5. The vehicle button is inserted at 2 and area A's gateway button at 3, after upstream's two inserts (11.3). `VehicleLayoutMici` dropped its `back_callback` too.
- **The lagd comment in `interface.py`.** Corrected after the merge: before lagd has blocks it publishes `steerActuatorDelay + 0.2` = 0.58 s, its `VERSION 1` discards older caches, and it learns only above 50 mph. It still named the learner's service `liveDelay` in one place (now `lateralDelay`); only a comment. Settled in 2026-10: upstream's lagd is kept, the line is now `steerActuatorDelay = 0.18` so its fallback is 0.38 s, and the comment was rewritten with it (5.1).
- **`settings_ui.json`.** Recompiled with `compile_settings_ui.py`, never merged by hand. After the merge the eight `HondaDyn*` info rows in `cruise.yaml` also dropped `step: 0.001`, which upstream's `test_settings_schema` rejects without `min`/`max` (11.4).
- **The UI brand page, `cruise.py` and `statsd.py`** were only renamed upstream (plus a small upstream change in statsd, which now imports `openpilot.sunnypilot.system.statsd`). They came across unchanged.

### 14.4 Identifiers to preserve

| kind | identifier |
|---|---|
| platform | `CAR.HONDA_ACCORD_9G_AU`. The string `HONDA_ACCORD_9G_AU` is also a key in `desire_helper.NUDGE_FIRM`, `desire_helper.LANE_CHANGE_SPEED_FLOOR`, `stopping_tune.STOPPING_SPEED` and `STOPPING_DECEL_RATE`, `car_list.json`, `override.toml` (and a comment in `substitute.toml`) and the tests |
| flags | `HondaFlags.ELESYS = 1024`; `HondaSafetyFlags.ELESYS_SCM_STANDDOWN = 32`; `HONDA_PARAM_ELESYS_SCM_STANDDOWN = 32`; `honda_elesys_scm_standdown` |
| sets | `HONDA_ELESYS` |
| DBC names | `honda_accord_au_2015_can` (becomes `_generated`), `honda_accord_2015au_radar`, and the fragments in 4.1 |
| CAN IDs openpilot sends on this car | 0x0E4 on bus 0 (5 bytes), 0x1FA on bus 0, 0x30C on bus 0, 0x1A6 on **bus 2**, 0x200 on bus 0 (pedal), 0x500 on bus 0 (area B). Never 0x33D. The panda TX list also allows `{0x194, 0, 4}`, but this car's DBC has no 0x194 (it imports `_steering_control_e.dbc`, whose `STEERING_CONTROL` is 0x0E4) and openpilot does not send it |
| CAN IDs read | 0x188 `GEARBOX_AUTO`; 0x1A6 `SCM_BUTTONS` (including `FUEL_LEVEL`); 0x221 `ECON_STATUS`; 0x294 `SCM_FEEDBACK`; 0x33D `LKAS_HUD` on bus 0; 0x1FA and 0x30C from bus 2; 0x18F `STEER_STATUS` with `STEER_CONTROL_ACTIVE 32:1`; radar 0x400, 0x410-0x417 and 0x420-0x424 on bus 1 |
| signals | `CMBS_BRAKE`, `CMBS_DISABLED`, `AEB_REQ_3`, `CMBS_BUTTON`, `FUEL_LEVEL`, `FUEL_SENDER`, `ODOMETER_KM`, `ECON_ON`, `GEAR_SHIFTER`, `GEAR`, `SET_ME_1` (the units bit on this car) |
| functions | `compute_gb_honda_elesys`, `brake_pump_hysteresis_elesys`, `create_scm_buttons_no_cruise`, `update_gear_elesys`, `elesys_gas_multiplier`, `elesys_pedal_v1`, `elesys_pedal_v2`, `ElesysGasLaw`, `ModeCrossfade`, `mode_slot`, `read_gas_law_v2`, `soft_stop_ceiling`, `soft_stop_cap`, `wheels_read_zero`, `ElesysSoftStop`, `SoftStopState`, `HondaDynamicTuner` (`update_state`, `brake_gain`, `wind_scale`, `update_wind`, `observe_pedal`, `filtered_pitch`, `persist`, `log_state`, `debug_values`), `learned_value`, `car_platform`, `gas_law_applies`, `gas_law_v2`, `gas_law_label`, `mode_minutes`, `mode_time_text`, `reset_learned_values`, `car_brand`, `VehicleLayoutMici`; the keyword arguments `should_stop(..., v_ego_stopping=)` and `create_brake_command(..., is_metric=, elesys=)` |
| constants | `ELESYS_PUMP_*`, `ELESYS_GAS_BP`, `ELESYS_GAS_V`, `ELESYS_FF_BP`, `ELESYS_FF_K`, `ELESYS_FF_GM`, `ELESYS_FF_G0`, `MODE_K`, `CROSSFADE_FRAMES`, `GAS_LAW_PARAM`, `SOFT_STOP_*`, `WHEELS_ZERO_SPEED`, `SPORT_DWELL`, `FUEL_LEVEL_FULL`, `STEER_THRESHOLD[HONDA_ACCORD_9G_AU] = 600`, `NUDGE_FIRM`, `NUDGE_HOLD_FRAMES`, `STOPPING_SPEED`, `STOPPING_DECEL_RATE` (`float32(0.8)`), `STANDSTILL_SPEED`, `STOPPING_EXIT_DEBOUNCE`, `LINBUS_I_CARRY_MAX`, `LINBUS_I_HOLD_TAU`, and the tuner constants in 9.1 |
| CarParams values | `transmissionType = automatic`, `longitudinalActuatorDelay 0.6`, `stopAccel -0.8`, `steerActuatorDelay 0.18`, `steerAtStandstill True`, `lateralTuning.torque` 1.25 / 0.18 with `latAccelOffset -0.43`, `minEnableSpeed 19 mph` (with the gas-interceptor exemption). No `vEgoStopping`: it is deprecated upstream, and its 0.8 m/s lives in `stopping_tune.py` |
| params | see 11.1 |
| capnp fields area C code reads | `CarStateSP.driverTorqueStale @2`; `CarControlSP.lateralControl @5` (rebuilt in `helpers.py`) |
| markers | `FORK(HONDA_ELESYS)`, `FORK(HONDA_ACCORD_9G_AU)`, `FORK(LKAS-GATEWAY)`, `FORK(GATEWAY-UPDATE)`, `FORK:`, and `HONDA_ACCORD_9G_AU` in `honda.h`. Incomplete; see Baseline |
| log tags | `hondadyn`, `hondastop`, `hondashadow`, `latsplit` |

### 14.5 After every merge

1. Run `git diff refs/upstream/master HEAD --stat` in both repos and compare the file list with section 1. Every file in the inventory must still differ from upstream, and in the way this document says. As a secondary check only, `git grep -n -e 'FORK(' -e 'FORK:' -e HONDA_ELESYS -e HONDA_ACCORD_9G_AU` finds the marked hunks; it will not find the unmarked ones listed in the Baseline.
2. Build, regenerate the DBCs, and load `honda_accord_au_2015_can_generated.dbc` through the parser.
3. Safety: run the two `TestHondaElesys*` classes, the whole of `test_honda.py`, and MISRA.
4. Run `test_elesys.py`, `test_elesys_gas.py`, `test_elesys_stop.py`, the two standalone tuner scripts (directly, and the integration script with the sunnypilot tree on `PYTHONPATH` so §15 runs), and, under `tools/test_runner.py`, `test_stopping_debounce.py`, `test_lane_change_nudge.py`, `test_car_control_sp_seam.py` and `test_honda_dynamic_settings.py`. Check the runner's counts (17, 9, 1, 15). Also run opendbc's `test_car_interfaces`, `test_platform_configs` and `test_docs` against the platform.
5. If upstream has changed `should_stop()`, `LongControl` or the planner, check that this car still gets 0.8 m/s and 0.8 m/s³ and every other car upstream's values (`TestStoppingTune`), and grep for new `should_stop(` callers.
6. Recompile `settings_ui.json` and run `compile_settings_ui.py --check`.
7. On the first drive, check that:
   - the car selects `HONDA_ACCORD_9G_AU` from the bundle with no FW query in the log (`Fixed fingerprint ... skipping the VIN/FW query`)
   - there is no ACC or CMBS fault on the first ignition after the update
   - the gear reads P while parked
   - the fuel gauge is sane
   - `shouldStop` latches at about 0.8 m/s approaching a red light, and the car holds at `stopAccel` without crawling
   - with the tuner on, each stop logs one `hondastop` line, and the brake reaches the hold (189) about 0.8 s after the wheels read zero (7.8)
   - the car does not engage below 19 mph
   - with `HondaElesysStockAcc` off, `pandaStates.safetyParam == 164` (36 before brake320, 7.13) and the stand-down `0x1A6` goes out on bus 2; once per merge, one drive with it on and the checks in 15.8
   - `hondadyn` lines appear if the tuner is on, with `gaslaw=v2` (or `v1` if the setting is off) and `modesec` growing in the slot being driven
   - with the tuner on, `hondashadow` lines appear (once a minute while engaged and at each disengage), and `latsplit` lines once a minute while steering above 54 km/h; `shadow_learn_report.py` reads them (9.3)

---

## 15. Stock ACC mode (`HondaElesysStockAcc`, 2026-10-04)

The owner's request: drive on the car's own ACC with openpilot's lateral - "full stock, all bits pass through" - and log it. With the toggle on, openpilot does no longitudinal at all: the Elesys radar (panda bus 2) does gas and brake through its own `0x1FA`/`0x30C`, openpilot steers through the board (`0x0E4` and `0x500` on bus 0), and the panda forwards every frame between bus 0 and bus 2 in both directions. With the toggle off (the default) every byte is what it was: CarParams, CarParamsSP, every `sendcan` frame, every panda TX and forward decision, the stand-down included. No board change.

The investigation behind it (five reports and a synthesis, 2026-10-04) was not kept in the repo; the facts that decided the design are repeated here, with the routes they came from.

### 15.1 Why it needs its own mode

`openpilotLongitudinalControl = False` on its own produces exactly the fault the owner fears:

- if it is set late (after `_get_params`), bit 32 (`ELESYS_SCM_STANDDOWN`) is already in the safety param: the panda keeps blocking the driver's `0x1A6` to the radar while openpilot no longer re-sends it, so the radar hears no `SCM_BUTTONS` at all, and the radar's `0x1FA` (except during AEB) and `0x30C` stay blocked;
- if it is set inside `_get_params`, bit 32 drops and the panda falls back to plain Nidec, whose TX list has `0x33D` relay-checked. The board's `0x33D` on bus 0 then raises a relay malfunction, which blocks all TX **and all forwarding** - CMBS included.

So the mode is a new safety parameter bit with its own TX list and forwarding, and one hook that undoes everything `_get_params` decided from "openpilot long".

### 15.2 The one writer: `_initialize_honda()` (`opendbc/sunnypilot/car/interfaces.py`)

Called last in `setup_interfaces()`, after `get_params`/`get_params_sp` and before the `CarInterface` is built (`car_helpers.get_car`), the same slot as Toyota's `ToyotaEnforceStockLongitudinal`. It acts only when `CP.carFingerprint in HONDA_ELESYS` and the param is 1, and then sets, in one place:

| field | stock mode | toggle off (unchanged) |
|---|---|---|
| `CP_SP.flags` | `\|= HondaFlagsSP.ELESYS_STOCK_ACC` (8) | no bit 8 |
| `CP.openpilotLongitudinalControl` | False | True |
| `CP.pcmCruise` | True | False with the pedal |
| `CP.autoResumeSng` | False | True with the pedal |
| `CP_SP.enableGasInterceptor` | False | True with the pedal |
| `CP_SP.safetyParam` | `GAS_INTERCEPTOR` (2) cleared: 0 | 2 |
| `CP.safetyConfigs[-1].safetyParam` | `NIDEC_ALT \| ELESYS_STOCK_ACC` = **68**, bits 32 and 128 cleared | 164 (36 before brake320) |
| `CP.minEnableSpeed` | 19 mph, unchanged | 19 mph |

It logs `Honda ELESYS stock ACC mode: openpilot longitudinal off, all frames forwarded` through `carlog`, which card forwards to cloudlog. `HondaSafetyFlags.ELESYS_STOCK_ACC = 64` (`values.py`) must equal `HONDA_PARAM_ELESYS_STOCK_ACC = 64U` (`honda.h`); `HondaFlagsSP.ELESYS_STOCK_ACC = 8` (`values_ext.py`) is the sunnypilot-side flag that MADS, the startup banner, the settings snapshot, CarController and CarState read. The param reaches the hook through `initialize_params()` (`openpilot/sunnypilot/selfdrive/car/interfaces.py`), read once per card start, so a change applies at the next ignition and never mid-drive.

### 15.3 CarController and CarState

- `carcontroller.py`: `self.elesys_stock_acc` from `CP_SP.flags`. The longitudinal block is `if not opLong and not self.elesys_stock_acc: ... elif opLong: ...`, so stock mode takes neither branch: no `0x1FA`, `0x200`, `0x30C`, no stand-down `0x1A6`, no Bosch supplemental `0xE5` (not in this car's DBC: the packer would build empty frames and log an error every frame), and **no CANCEL/RES_ACCEL spam**: controlsd asks for cancel whenever openpilot is not engaged, but the only cancel path is a `0x1A6` on bus 0, which no Nidec TX list allows and which would collide with the SCM's own frame. The stand-down line carries an explicit `not self.elesys_stock_acc` as well. What goes out: `0x0E4` at 100 Hz and `0x500` at 10 Hz (`LONG_ACTIVE` reads 0).
- `carstate.py`: `accFaulted` from `BRAKE_ERROR_1/2` also when the flag is set, so the VSA's live fault still disengages (`vsa_fault_alert.py`); upstream computes it only with openpilot long.

### 15.4 Panda safety (`honda.h`, param 64)

- `honda_elesys_stock_acc` from `GET_FLAG(param, HONDA_PARAM_ELESYS_STOCK_ACC)`, reset in `honda_bosch_init()`.
- TX list `HONDA_N_ELESYS_STOCK_ACC_TX_MSGS`: `{0xE4, 0, 5, relay}`, `{0x194, 0, 4, relay}`, `{0x500, 0, 8}` - the only frames openpilot may send - plus two relay checks, `HONDA_N_ELESYS_STOCK_ACC_RELAY_CHECK`: `{0x1FA, 0, 8}` and `{0x30C, 0, 8}`, both `check_relay` with `disable_static_blocking`, which `honda_tx_hook()` refuses in this mode (and a refused `0x1FA` leaves `honda_brake` alone). Nothing longitudinal goes out, no `0x1A6` on either bus, no `0x33D` (so the board's `0x33D` cannot raise a relay malfunction).
- **Why the radar's frames are relay-checked.** The relay check (`safety.h`, `stock_ecu_check`) only looks at TX entries marked `check_relay`, and nothing on the car's side sends `0xE4` or `0x194` (0 frames of either on bus 0 on the passive routes 0e and 82). Without more, a harness relay that did not open - an undetected harness, a loose or flipped OBD-C cable, a failed relay; `set_intercept_relay()` does nothing while `harness.status` is `NC` - went unnoticed: bus 0 and bus 2 are then one wire, and every forward lands back on it (221,670 and 221,142 frames each way in two segments of routes 0e and 82, replayed through the first build). The radar sends only `0x1FA` (50 Hz) and `0x30C` (10 Hz), on bus 2; either one seen on bus 0 is that fault, as `0x30C` is in the stand-down list today. Now it is a relay malfunction as soon as the usual transition window has passed (`safety_mode_cnt > 1`, 1-2 s after the mode is set), which stops all TX and all forwarding - the car is then wired as without a comma - and `relayMalfunction` is raised. Replayed with the firmware's 1 Hz counter, routes 0e and 61 at param 68 trip at 2.01 s on the radar's `0x1FA` on bus 0 and forward nothing after it; the same routes made to look like an open relay (bus 2 only the radar's two IDs, bus 0 everything else) never trip and forward every frame both ways, decision for decision as the first build.
- The gas interceptor is forced off (Toyota's stock-long pattern): no `0x201` RX check, gas from `0x17C`, PCM-cruise engagement.
- `honda_nidec_fwd_hook()` blocks nothing: the radar's `0x1FA` (the ACC's brake and CMBS) and `0x30C` (the ACC's gas; relay-checked with `disable_static_blocking`, so not statically blocked either) reach bus 0, and the driver's `0x1A6` reaches the radar with the real `MAIN_ON`. `ALT_EXP_DISABLE_STOCK_AEB` has no effect in this mode.
- **Bits 32 and 64 together** are not a valid input and are never sent (one writer, and `test_never_the_stand_down_and_the_stock_bit_together`). The panda maps them to "no stand-down, transmit nothing, forward everything" - a stock car with CMBS intact - rather than letting either side win. It keeps the relay check on the radar's frames (its TX list is `HONDA_N_ELESYS_STOCK_ACC_RELAY_CHECK` alone). That mapping rests on the TX list chosen at the end of `honda_nidec_init()` and on the override at the end of `honda_nidec_fwd_hook()`; clearing `honda_elesys_scm_standdown` in that case is defense in depth that no test can see through them (mutating it away passes every test), so review those three together.
- The AEB latch reads bit 43 in this mode too; it only reports, since the forward is unconditional.

### 15.5 sunnypilot

- **Engagement follows stock ACC.** pcmCruise on a Honda: `car_events.py` raises `pcmEnable` on `ACC_STATUS` rising (measured: ACC_STATUS tracks the radar's engagement on 91.7-98.2% of engaged frames; lowest stock engagement 29.1 km/h). openpilot still refuses below `minEnableSpeed` (19 mph = 30.6 km/h), so a stock engagement at 29.1-30.6 km/h is not followed; lateral is still available.
- **MADS at any speed** (`mads.py`). pcmCruise also raises `belowEngageSpeed` (NO_ENTRY) on every frame below 19 mph, and upstream strips it only once MADS is on - so MADS could not be switched on from off below 30 km/h or at a standstill, nor while openpilot is engaged on stock ACC and slowing in traffic (MADS off because unified engagement is off, or after a fast-wheel takeover; stock ACC holds down to ~22 km/h). In stock mode MADS strips it on every frame. selfdrived's state machine has already run on that frame, so openpilot's own refusal is unchanged, and NO_ENTRY alerts are cleared while openpilot is engaged anyway.
- **A refused engagement says so.** On the frame `pcmEnable` arrives below 19 mph and openpilot refuses it, `belowEngageSpeed` is put back after MADS's own state machine has run (`_stock_acc_refused`), so selfdrived shows its NO_ENTRY "drive above" alert - with MADS off or on, and with unified engagement turning MADS on on that frame. `pcmEnable` is an edge (`ACC_STATUS` rising), so openpilot then stays disengaged for the rest of that stock ACC session, even above 30.6 km/h, and the later ~22 km/h drop-out has no openpilot alert (openpilot was never engaged); press SET/RES again above 30.6 km/h to have openpilot follow. Route 0e t=54.8 (29.1 km/h) is such an engagement.
- **The drop-out.** Stock ACC lets go by itself at 21.9-22.3 km/h (routes 0e, 44, 61, 82). car_events raises `speedTooLow` (vEgo < minEnableSpeed + 2 m/s = 37.8 km/h): openpilot disengages with upstream's non-critical "openpilot Canceled / Speed too low". MADS strips that event when it keeps lateral, which also took the alert away; in stock mode it is put back on that one frame, after MADS's own state machine has run (`_stock_acc_drop_alerts`), so the driver hears the normal disengage and lateral stays. Never `cruiseDisabled`'s critical "TAKE CONTROL IMMEDIATELY" for this drop-out.
- **A drop-out at speed** (37.8 km/h and above, by itself: a radar or VSA fault, gas and brake gone at speed) gives upstream's `cruiseDisabled`, the critical "TAKE CONTROL IMMEDIATELY / Cruise Is Off", with MADS off and - put back the same way - with MADS on, where lateral stays: the same alert either way, never silent. (With the Mads toggle on, as on this car, openpilot's engagement on SET only follows stock ACC - alerts and HUD state - and actuates nothing: lateral is MADS alone, the LKAS button, unchanged from toggle off, so "openpilot was steering and stops" applies only to a setup with the Mads toggle off.) A drop the driver caused is not one of these: on all six stock routes every drop above 37.8 km/h came 0.03-0.54 s after a brake rising edge or 0.04-0.51 s after a CANCEL press (`carState` on routes 0e, 44, 48, 61, 81, 82: 45 falling edges), and openpilot disengages on that edge (a USER_DISABLE), so it is already off when stock ACC lets go.
- **Settings survive a stock drive** (`openpilot/sunnypilot/selfdrive/car/honda_stock_acc.py`). With openpilot long off, sunnypilot deletes `ExperimentalMode`, `DynamicExperimentalControl`, `CustomAccIncrementsEnabled`, `SmartCruiseControlVision`, `SmartCruiseControlMap` and saves `SpeedLimitMode` assist as warning (card, and the UI's speed limit panel while it is open). A stock start snapshots them into `HondaElesysStockAccSaved` (JSON, not BACKUP) in `setup_interfaces`, before `_cleanup_unsupported_params` and before CarParams are written.
  - **"Deleted"** is what the deleters leave: the key gone, or - because manager writes the default of every unset key at each start, on every boot and update install - back at its default while the snapshot holds something else (DEC, the custom increments and both Smart Cruise Control keys are BOOL "0"); for `SpeedLimitMode`, warning where the snapshot holds assist. A first version restored only absent keys and lost four settings across any reboot between the stock drive and the next one.
  - A later stock start keeps a snapshot value only for a key that looks deleted and takes the current value for the rest, so a second stock drive saves nothing it deleted itself, and a snapshot left behind by a drive that ended early is refreshed with anything changed since.
  - A start with openpilot long again puts back what was deleted, and nothing else, so a choice made in between stands. It runs in `setup_interfaces` (before card reads DEC) and once more from card's params thread (`LongSettingsRestore`, 10 Hz) after `CarParamsPersistent` has held this drive's CarParams for 3 s (`SETTLE_S`): card writes it non-blocking, and the UI's params thread (5 Hz) reads it and deletes later in the same tick, so a tick that read the stock drive's CarParams can delete after any earlier restore. Only then is the snapshot forgotten; a drive that ends sooner keeps it, and the next start restores again. ExperimentalMode and DEC can read off for the first 3 s of that drive, parked.
  - A start without openpilot long and without the mode (dashcam, unrecognized car) keeps the snapshot.
  - Card never depends on it: every step is in a try/except that logs and drops the snapshot. With the toggle off and no snapshot, card init reads one param and the params thread does nothing more.
- **Startup indication.** `OnroadEventSP.EventName.hondaElesysStockAcc @31`, raised by `CarSpecificEventsSP` (`car_specific.py`) for the first 5 s of car events of a stock drive only: a silent PERMANENT banner "Stock ACC Mode / Car's cruise does gas and brake, openpilot steers", `Priority.LOW`. It refuses and disengages nothing. The comma 4 shows no startup alert of its own, which is why this is an event and not a startup-alert variant.
- **Logging.** Every stock route says so three ways: `carParamsSP.flags & 8`, `pandaStates.safetyParam == 68`, and the cloudlog line above; the banner is in `onroadEventsSP`.
- **What it costs.** openpilot cannot cancel stock ACC: it keeps running after openpilot disengages or refuses (a refusal at 29.1-30.6 km/h, `carNotReady` from the VSA, a seatbelt) until the driver presses CANCEL or brakes. Keep **`DisengageOnAccelerator` off** in this mode (it is off on this car, route 115): with it on, any press of the accelerator disengages openpilot on the rising edge while stock ACC carries on under the driver's foot and resumes after, so openpilot shows disengaged while the car's ACC drives, until the next SET/RES. The comma 4 HUD's planned-stop rail is grey and dashed (openpilot is not driving the speed) and the curve item does not show. ICBM stays unavailable, as it always was on Nidec.

### 15.6 UI and sunnylink

Title "Stock ACC (testing)" in three places, all offroad only, none with an onroad cycle (Toyota's `OnroadCycleRequested` drops pandad to ELM327 pass-through and loses lateral for seconds while moving). The device enforces it for all three: sunnylink's `offroad` macro is advice to the app only, so `sunnylinkd.saveParams()` refuses `HondaElesysStockAcc` (`OFFROAD_ONLY_PARAMS`) unless `IsOffroad` is set - an onroad cycle requested from any other settings page would otherwise apply it mid-drive, re-imposing the stand-down and the `0x1FA` block under a braking stock ACC:

- big UI: `HondaSettings.stock_acc_toggle` (`brands/honda.py`, `STOCK_ACC_PARAM`, `enabled=ui_state.is_offroad`), edge-synced like the tuner toggle;
- comma 4: `VehicleLayoutMici._stock_acc_toggle`, a `BigParamControl` disabled onroad, refreshed with the others;
- sunnylink: `vehicle.yaml` honda section, `needs_onroad_cycle: true`, `$ref: '#/macros/offroad'`. **Never the `longitudinal` macro**: `has_longitudinal_control` is false in this mode, which would lock the toggle on. `settings_ui.json` recompiled.

`HondaElesysStockAcc` is not BACKUP: a sunnylink restore must never turn the mode on behind the driver's back. statsd reports it.

### 15.7 Tests

| test | pins |
|---|---|
| `opendbc/car/honda/tests/test_elesys_stock_acc.py` (17) | toggle off: CarParams bytes and CarParamsSP identical to no hook at all, with and without the pedal, for every "off" spelling; the running values (36, SP 2, interceptor, pcmCruise False); on: 68, SP 0, no bit 32, pcmCruise, no interceptor, `autoResumeSng` False, flag 8, `minEnableSpeed` kept, and only those fields changed; never 32 and 64 together; other Hondas ignore the param; CarController over 1200 frames with cancel, resume and both alternating: only `0x0E4` every frame and `0x500` every 10th, no empty frame; toggle off still sends the long frames and the stand-down, byte-identical with and without the hook; `accFaulted` from `STANDSTILL.BRAKE_ERROR_1` in stock mode, unchanged toggle off, and not for a pcmCruise car without the flag; since the merge with batch 2 (`TestStockAccBesideBatch2`, 15.9): with the dynamic tuner's toggle on, stock mode builds no tuner and no shadow learners and still sends only `0x0E4` and `0x500`, while toggle off builds both |
| `openpilot/sunnypilot/selfdrive/car/tests/test_honda_stock_acc.py` (15) | the param reaches the hook through `initialize_params()`; with a UI model that reads `CarParamsPersistent` and deletes later in its tick, and manager's default loop as `reboot()`: a stock drive and back restores every setting; a reboot between (and between two stock drives) loses nothing; a UI tick that read the stock CarParams deleting at any params-thread tick before the settle cannot win; the snapshot stays until `CarParamsPersistent` has held this drive's bytes for `SETTLE_FRAMES`; a drive that ends sooner keeps it for the next start; a left-over snapshot is refreshed at the next stock start; a choice made in between stands; a start without openpilot long keeps the snapshot; absent settings stay absent; a broken snapshot (a wrong type, not a dict) never raises and is dropped; the banner for exactly `STOCK_ACC_ANNOUNCE_FRAMES` in stock mode only, PERMANENT and silent |
| `openpilot/sunnypilot/mads/tests/test_mads_honda_stock_acc.py` (14) | selfdrived.step in miniature (real `CarEvents`, real `StateMachine`, MADS, alerts): MADS on at 0, 1 m/s, 20 and 29 km/h and off/on again at a standstill; MADS on at 35, 29, 25 and 10 km/h while openpilot is engaged on stock ACC; upstream's refusal without the flag; `pcmEnable` engages openpilot and MADS; no engagement below 19 mph, and the refused edge shows the NO_ENTRY alert once, MADS off or on, unified engagement or not; the 22 km/h drop-out gives "openpilot Canceled / Speed too low", normal status, MADS still active for 300 frames with no critical alert; at 39, 80 and 100 km/h "Cruise Is Off" with MADS on or off; the two alerts meet at 37.8 km/h; a brake cancel (edge before the drop) is never critical; MADS off: upstream's alert; without the flag: upstream's strip |
| `openpilot/sunnypilot/sunnylink/athena/tests/test_sunnylinkd.py` (+2) | `saveParams()` refuses `HondaElesysStockAcc` onroad and while `IsOffroad` is unknown, and writes it offroad; the same for the pump rule's key (`HondaElesysPumpC1b` since 2026-10-06) (batch 3 fix round 1: it is read at ignition, so a write applied by a later onroad cycle or card restart would switch the pump rule mid-drive; `HondaElesysBrakeLawV2` was in the set until round 5). `HondaElesysGasLawV2` and `HondaDynamicTuningEnabled` have the same gap and are not in the set |
| `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` (+4) | the key BOOL "0", PERSISTENT, not BACKUP, the snapshot key JSON; one name from the panels to the hook; both screens offroad only, no onroad cycle; sunnylink offroad only, never `has_longitudinal_control`, and the description's facts |

The panda side is in `opendbc/safety/tests/test_honda.py` (8.3 and the stock-mode classes there): besides the TX list and forwarding, `RELAY_MALFUNCTION_ADDRS` includes the radar's `0x1FA`/`0x30C` on bus 0, `honda_elesys_wire()` feeds the car's and the radar's frames in the firmware's order with the relay open (no malfunction, everything forwarded) and closed (one wire, as on a passive route: the radar's first frame on bus 0 trips it, nothing forwarded after), the transition window is respected, a refused `0x1FA` does not move the AEB latch, and the 32|64 case keeps the same relay check.

### 15.8 The first stock drive

Parked, with the toggle on and the car started:

1. `pandaStates[].safetyModel == hondaNidec` and **`safetyParam == 68`**; `carParamsSP.safetyParam == 0` (no interceptor). No `relayMalfunction`, no `controlsMismatch`. (A stale panda firmware would echo 68 too and fall back to the plain Nidec list, whose `0x33D` raises the relay malfunction - loud. pandad reflashes on a signature mismatch before the mode is set.) `relayMalfunction` is a real check here: a relay that did not open shows the radar's frames on bus 0 and trips it.
2. **The relay is open**, independently of the panda's own check: from about 2 s after `pandaStates` shows hondaNidec/68 until ignition off, `0x1FA` and `0x30C` appear in `can` only with src 2 (the radar) and src 128 (forwarded onto bus 0), **never with src 0**. Outside that window src 0 is normal, because the relay is closed by design: about 450-500 `0x1FA` and 90-100 `0x30C` with src 0 during the ELM327 start-up window (until ControlsReady, ~9-10 s; routes 115, 113, 10f), and a few in the last 0.1-0.5 s after key-off, when pandad switches the panda to NO_OUTPUT (13 of 36 board-era routes).
3. `carParams.openpilotLongitudinalControl == False`, `pcmCruise == True`; `carParamsSP.flags & 8`; the log has `Honda ELESYS stock ACC mode: openpilot longitudinal off, all frames forwarded`; "Stock ACC Mode" shows for about 5 s.
4. **The accelerator works without the interceptor.** No `0x200` is sent in this mode, so the comma pedal must pass the driver's foot through: that was seen only at a standstill (178/178 and 441/441 frames with `GAS_SENSOR` `STATE` 5), and "output = input while faulted" is inferred from upstream pedal firmware that is not on disk; the stock routes 0e-82 had no pedal fitted. Press the accelerator: `0x17C` `PEDAL_GAS` follows and `GAS_SENSOR` `STATE` reads 5. Then confirm `carState.gasPressed` and that the car pulls during the first low-speed roll, before relying on it in traffic.

Driving:

5. The radar's `0x1FA` at 50 Hz and `0x30C` at 10 Hz forwarded onto bus 0: in `can` they are src 128 (the panda's transmissions on bus 0 - a forward is logged there like openpilot's own frames; src 130 holds the frames forwarded to bus 2, the real `0x1A6` at 50 Hz among them), as well as src 2 where the radar sends them. And **no** `0x1FA`, `0x30C`, `0x200` or `0x1A6` in `sendcan` - only `0x0E4` and `0x500`. The VSA never latches `BRAKE_ERROR` (a forward that stopped would latch it about 1 s after the last `0x1FA`) and stock ACC brakes.
6. No `ACC_PROBLEM` (radar `ACC_HUD`), no `TSA_ERROR` (`0x1A4` bit 33), no `BRAKE_ERROR_1/2`, no `CRUISE_FAULT_CMD`.
7. Stock ACC engages above ~30 km/h and openpilot follows (`selfdriveState.enabled` on `ACC_STATUS`); MADS can be switched on and off at any speed, standstill included, also while openpilot follows stock ACC; the drop-out at ~22 km/h gives "Speed too low" and lateral stays.
8. CMBS: the CMBS-off switch still works (the driver's `0x1A6` reaches the radar).

Then turn the toggle off, offroad, and check on the next drive that Experimental Mode, DEC, the custom ACC increments, Smart Cruise Control and Speed Limit Assist are back as they were, and that the stand-down is back (`safetyParam == 36`, `0x1A6` on bus 2 in `sendcan` with `MAIN_ON = 0`).

### 15.9 Beside batch 2 (merged 2026-10-05)

Stock ACC mode and batch 2 (sections 6.4, 8.5, 9.2, 9.3 and the comma 4 HUD's compact alerts and sign setting) were
written apart and merged together. What each part of batch 2 does in stock mode, checked on the merged tree:

- **Launch cap and shadow learners: nothing.** Both live in the dynamic tuner, whose `_is_applicable()` needs openpilot
  long, so in stock mode the tuner is off even with its toggle on, builds no shadow learners (they also need the
  interceptor) and no soft stop; the cap only shapes `0x200`, which this mode never sends. Pinned by
  `TestStockAccBesideBatch2` (`test_elesys_stock_acc.py`), which fails if `_is_applicable()` stops requiring openpilot
  long. No `hondashadow` line is written in stock mode. The steering split (`latsplit`, `lat_speed_split.py`) is
  lateral and runs as on any drive.
- **Speed Limit Assist and the wrong-way press: nothing.** With openpilot long off and `pcmCruiseSpeed`, sunnypilot
  makes assist unavailable and saves `SpeedLimitMode` as warning (the snapshot puts assist back afterwards), so the
  confirm prompt never comes up; and the wrong-way rule is non-PCM only, a path a pcmCruise car never takes
  (`cruise.py` and `update_speed_limit_assist_pre_active_raise_blocked()` both check).
- **Compact alerts.** None of this mode's own alerts is compact: `speedTooLow` (the drop-out), `cruiseDisabled`,
  `belowEngageSpeed` (a refused SET) and `hondaElesysStockAcc` (the startup banner) are not on the allow-list and draw
  full screen as designed. Replaying stock routes 0e and 61 with every HUD setting on, the only compact alerts were the
  driver's own MADS disengage, `manualSteeringRequired` (5 and 16 frames), and once on 0e `manualLongitudinalRequired`
  ("Smart/Adaptive Cruise Control: OFF"), a cancel with lane centering kept - normal, informational, the driver's doing.
- **Key-off `STEER_STATUS` (6.4): applies.** It is carstate's, in both modes. On the same replays it removes the parked
  key-on/key-off "LKAS Fault: Restart the car to engage" banners (0e 253 -> 147 frames, 61 144 -> 125); nothing else
  in the replay changed.
- **The MADS heartbeat reset (8.5): applies.** It is `mads.h`, every mode: the panda's MADS tests
  (`mads_common.py`, including the four new regrant and heartbeat-traffic tests) run under param 68 in
  `TestHondaElesysStockAccSafety`, and so does route 114's real-frame regrant test (`HondaElesysRoute114Regrant`,
  shared with the stand-down class since 2026-10-05).

## 16. A diagnostic tool on the car bus, and a car-bus bus-off (KTuner, 2026-10-19)

Branch `ktuner`, merged into push10 (2026-10-20); changelog entry "push10" in `docs/CHANGELOG_SERIAL_STEERING.md`. Every hunk is
`FORK(HONDA_ACCORD_9G_AU)`. KTuner itself changes neither opendbc nor panda
(`git diff --stat nsw-live ktuner -- opendbc_repo panda` is empty); on push10 opendbc moves to `7f34fcde` for round 8
and sla-flash, and panda stays `74a0adce`.

### 16.1 What happened

Five parked sessions with ignition on: **144, 145, 147** (2026-10-19, KTuner datalogging the engine ECU over the OBD
port) and **dc, db** (2026-09-16; dc a scan tool, db no tool traffic on the bus at all).

* For the first 6.5-9 s of every start the panda is in `elm327` param 1: the harness relay at the radar is closed (the
  radar is on the car's own wire), and the comma sends nothing. In that phase KTuner logged for 8.7-9.3 s on each route
  with bus-0 TEC 0, REC 0 and no change of `busOffCnt`.
* Then card's first `controls_update` writes `ControlsReady`, pandad sets `hondaNidec` 36, the relay opens, the comma
  transmits. With KTuner on the bus the panda's bus-0 transmissions failed at once (`lastStoredError = bit1Error`): TEC
  113 at +0.19 s and bus-off at +0.29 s on 144; `busOffCnt` rising within 0.0-0.3 s of the first car-mode poll on all.
  After 0.1-3.6 s of error frames and core resets the core stayed bus-off and silent for 136-649 s, the rest of the
  route: `canBusMissing`, and the radar, which in the car mode reaches the car only through the panda, cut off - the
  ACC and CMBS lamps. The SRS flicker is inferred from the error frames.
* A normal start (143) makes the same switch with zero bus-0 errors. It is not an ID collision (the radar's 0x1FA/0x30C
  stop 123 ms before our first frame). KTuner's requests `0x18DA0EF1` (about 115/s) and ECU 0E's answers `0x18DAF10E`
  (about 1450/s) arrive as src 0, never as our echo. The physical cause (KTuner rejecting the comma's frames, stub
  reflections, the panda's sample point) is not settled; neither fix depends on it.

### 16.2 "Stock" is a mode change, never a stand-down change

| name | panda mode | relay at the radar | the comma on the wire | when |
|---|---|---|---|---|
| START_STOCK | `elm327` p1 | closed | nothing | the first seconds of every start (unchanged) |
| CAR | `hondaNidec` 36 (stand-down) or 68 (stock ACC mode) | open | per the TX list | normal (unchanged) |
| STOCK_DIAG | `elm327` p1 held from start-up, or `noOutput` entered mid-drive | closed | nothing | fix 1 |
| STOCK_BUSOFF | `noOutput` | closed | nothing | fix 2, until key-off |

Both non-car modes release the relay and call `can_init_all()` (panda `main.c`), which also takes a stuck core out of
bus-off. The stand-down (bit 32: `SCM_BUTTONS` re-sent with MAIN_ON=0, the car's own copy blocked by the forwarding
hook) and stock ACC mode (bit 64) are not touched; with the relay closed they are simply not what is on the wire.
Section 4's traps still apply: never drop bit 32 to "un-stand-down" (relay malfunction, all TX and forwarding
blocked), never `elm327` p0 (bus 1, the radar's own bus, taken over).

### 16.3 Fix 1: a diagnostic tool talking while parked (card, `sunnypilot/selfdrive/car/diag_tool_guard.py`)

* **What counts:** a frame in card's `can_list` with **src == 0 exactly** whose address is a tester request: 29-bit
  `0x18DA<TA><SA>` / `0x18DB<TA><SA>` with SA in 0xF0-0xFD and TA outside it (seen: 0x18DA0EF1, 0FF1, 10F1, 12F1, 1EF1,
  0x18DBEFF1), or 11-bit `0x7DF` / `0x7E0-0x7E7`. Never a response (`0x18DAF1xx`, `0x7E8-0x7EF`: our own FW query is
  answered on src 0), never src 2 (our own requests seen through the closed relay), never src >= 128 (echoes,
  forwards), never the board's `0x700-0x71F` or BLR's `0x620-0x62F` (outside the patterns by construction). Across 189
  normal routes (51.5 h, 176 full-rate start-ups) no frame ever qualified.
* **Enter:** 2 qualifying frames within 1.0 s, parked (`CS.canValid`, gear P, standstill), nothing engaged
  (`carControl` not alive yet, or not enabled / latActive / longActive). dc's scan tool: 0.22 s after its first request,
  0.47 s before the switch (a 5-frame rule would miss it).
* **Leave:** 10 s without a request AND parked. The quiet time grows only across `can` messages that carry a src-0
  frame, each gap counted at most 0.1 s, so a dead car bus (a bus-off) is never quiet. The longest in-session gap seen
  is 0.181 s (KTuner) and 0.221 s (a scan tool). It never resumes while moving: resuming is entering the car mode, which
  stands down a stock ACC the driver may have engaged. A tool still talking when the car drives off keeps it stock while
  driving (KTuner and a stock car coexist), until the next time the car is in P with 10 s of quiet.
* **Back-off (review fix 2026-10-20):** a tool that enters again within 2 min (`REENTRY_NS`) of a release is the same
  tool polling slowly - every return to the car mode transmits into its bus - so that episode needs 30 s of quiet, the
  next 60 s (`QUIET_STEPS_NS`); a tool back after more than 2 min starts at 10 s again, and so does a card restart.
  With the reviewer's slow-polling tool (request + flow control every N s, 120 s parked): 11 s 21 -> 3 transitions,
  15 s 16 -> 3, 30 s 8 -> 3, 61 s at most one return a minute.
* **The action, two cases:**
  * before controls have first started, `card.step()`'s one FORK line holds the **first** `controls_update`
    (`initialized and not (self.diag_guard.stock and not self.initialized_prev)`): no `CI.init`, no `ControlsReady`, no
    `sendcan`, so the panda stays in START_STOCK. The release runs that first call exactly as a normal start;
  * after it, the line is false for good (card never stops `sendcan` mid-drive: if it stopped before pandad closed the
    relay the radar would get no `SCM_BUTTONS` for up to 0.2 s). card writes `DiagToolStock` from `params_thread`, on
    change only; pandad reads it at 10 Hz and leaves the car mode for `noOutput` (16.4). card keeps sending; the panda
    drops it all; on the way back the radar gets openpilot's `0x1A6` at once.
* **Lifetime:** the guard lives in card; it inherits `DiagToolStock` in `__init__`, so a card restart inside the drive
  keeps holding. The manager clears the param at every onroad and offroad transition, before card starts.
* **Logged:** `cloudlog.event("diag_tool_stock", on=..., ids=..., requests_1s=..., gear=..., v_ego=..., standstill=...,
  can_valid=..., controls_started=..., quiet_needed_s=..., error=True)` on each transition (`quiet_s`, `duration_s`,
  `inherited` on the release), and once per episode `"diag_tool_seen"` for a tool heard while the car is known to be moving or engaged (not
  while its CAN is still invalid at start-up). Both reach `errorLogMessage`, the qlog and the parquet.
* Armed only when `CP.brand == 'honda' and CP.carFingerprint in HONDA_ELESYS and not CP.passive`.

### 16.4 Fix 2: the car bus went bus-off (pandad, `selfdrive/pandad/car_bus_guard.h`, `pandad.cc`, `panda_safety.cc`)

* **Per 10 Hz poll** in `send_panda_states()`, before the relay decision: the car-side core (`can_health[0]`, or 2 with
  `car_harness_status == FLIPPED`), raw ignition (line or CAN, after `STARTED` spoofing; not `OffroadMode`), onroad, and
  the ELESYS car mode (`hondaNidec` with param bit 32 or 64), and whether fix 1 (`DiagToolStock`) is acting in the same
  poll. **Latch** when the first three hold, fix 1 is not acting, and `busOffCnt` rose since the previous poll, or
  `busOff` - **bus-off only**, the owner's words.
  * `busOffCnt` is cumulative since panda boot (145 started at 2982, 147 at 0 after a panda reboot): only an increase
    counts, a decrease re-baselines, and the baseline moves on every poll, car mode or not, so a bus-off inside the
    first 100 ms of the car mode is still caught (145).
  * No TEC (review fix 2026-10-20). The first build also latched at TEC >= 128 (error-passive by our own
    transmissions). That is recoverable and still forwards; in stock ACC mode at speed it would switch the relay under a
    live stock ACC on an error the panda could have recovered from; and on the five incident routes it was ahead of
    the bus-off by 0.1 s on dc and 147 only, 0 on the rest. Normal routes never had a bus-0 TEC above 5. Putting it back
    is a field and one term in `car_bus_guard.h`, and `test_stock_wiring.py` pins it out until someone decides that.
  * Not while fix 1 acts in the same poll (review A F3): a tool plugged in mid-session while parked used to latch fix 2
    when the storm and `DiagToolStock` reached pandad in the same poll, leaving openpilot off until restart after the
    tool went quiet. Fix 1 owns that episode; fix 2 judges again once fix 1 has put the car mode back.
  * Buses 1 and 2 are never read: bus 2 goes error-passive at every ignition-off (102 routes), bus 1 during
    openpilot's own FW query (21 routes).
* **Act in the same poll:** `stay_stock = DiagToolStock || latched`; `should_close_relay` gains
  `|| (stay_stock && is_car_safety_mode(mode))` - only a car mode is forced down, START_STOCK is left alone - so
  upstream's own two lines set `noOutput`. `configureSafetyMode(is_onroad, stay_stock, ignition)` still runs the
  start-up `elm327` and the OBD-multiplexing handshake card waits on, returns before `fetchCarParams()` while stock,
  and re-arms (`safety_configured_ = false`) after leaving the car mode, so the release re-sends `0xdf` (accepted only
  outside a car mode) and `0xdc` from the unchanged `setSafetyMode()`: 36, or 68 in stock ACC mode.
* **Never a car mode on a poll that read the ignition off** (review fix 2026-10-20): `configureSafetyMode()` also
  returns before `fetchCarParams()` when this poll's ignition is off or was not read - no re-arm, nothing else.
  pandad's `is_onroad` (deviceState.started) is one poll behind the ignition at key-off, so a re-armed or not yet
  configured pandad would otherwise send the car mode there: after a fix-2 latch at EVERY key-off (both reviews), after
  fix 1 released on the key-off poll, and in upstream's own case of the key turned off on the poll ControlsReady
  appears. The 10 Hz block is one function, `process_panda_safety()` (`getDiagToolStock()`, `process_panda_state()` -
  which now returns the poll's ignition - and `configureSafetyMode()`), so the native test drives exactly it.
* **The latch lasts until key-off: the ignition off AND pandad offroad** - not until onroad drops: onroad can return
  without a key cycle (overheat, `OffroadMode`), and db shows the storm comes back with no tool traffic, so a re-arm
  would cycle. The first build cleared it on the first ignition-off poll, while pandad was still onroad (above); a
  one-poll ignition dropout cleared it too, and the next onroad session went back into the fault. The latch poll writes
  `CarBusOffStock` with `putBoolNonBlocking` (no fsync in the loop), and so does any later poll that finds it missing
  while latched (the manager clears it on every ignition-on edge, a dropout included); pandad seeds the latch from it at
  start (a pandad restart stays out), removes it itself at key-off. A device reboot mid-cycle clears it (as upstream
  does on a reboot; it re-trips in one poll if the fault persists).
* **Logged (`LOGE`, so the qlog has them):** "FORK car bus off stock: core .. bus_off .. bus_off_cnt .. (prev ..) tec ..
  rec .. lec .. mode .. param .. onroad ..", "FORK stay stock: leaving car mode .. param .. (diag .., bus off ..)",
  "FORK leaving stock: car safety mode set", "FORK car bus off stock: cleared at key-off".
* **Fix 2 wins** over fix 1, and cannot fire in START_STOCK or STOCK_DIAG (it is armed in a car mode only). A tool that
  is plugged in but idle (db) gives fix 1 nothing; if the comma's frames then fail, fix 2 latches after one short storm
  and does not loop.
* **Why no panda firmware change:** `noOutput` and `elm327` p1 already release the relay, block every openpilot frame,
  disable forwarding and re-init the cores; pandad acts in the poll that sees the error; after the first 0.1-3.6 s the
  stuck core is silent anyway, so a firmware reaction would save at most one poll and cost a panda reflash and a new
  safety review.

### 16.5 selfdrived (`sunnypilot/selfdrive/selfdrived/car_stock_alert.py`) and the events

`controlsMismatch` (IMMEDIATE_DISABLE + NO_ENTRY) is raised whenever the panda's mode differs from `CP.safetyConfigs`
once selfdrived is 10 s old - which is what stock looks like - and a MADS paused in P counts as not disabled, so it
would drop to disabled with "TAKE CONTROL IMMEDIATELY" on both edges (pandad and selfdrived read the params out of
step). The helper replaces it with upstream's `carNotReady` (NO_ENTRY only: engagement is still refused) only when
nothing is engaged, every panda is in `noOutput`/`elm327`, there is a reason (or the non-car mode is under 0.5 s old,
or the reason cleared under 2 s ago), and neither `safetyRxChecksInvalid` nor `mismatch_counter >= 200` is present.
Anything else - no reason past 0.5 s, a wrong param, anything engaged - is upstream's event, untouched; a bus-off at
speed is disengaged by upstream's own `controlsMismatch`. **"Engaged" includes a MADS paused while the car moves**
(review fix 2026-10-20: on the brake in Pause mode, or the board's driver-override pause): it resumes by itself, so a
bus-off there is the critical disengagement with our text (and `carBusOffDisengage`), not one chime and a refusal when
the brake comes off. Not for a diagnostic tool: fix 1 only enters parked, and a MADS paused since then has been
refused all along - driving off with the tool still talking stays a note.

| event (`OnroadEventSP`) | types | text | priority, sound |
|---|---|---|---|
| `diagnosticToolStock @33` | PERMANENT, NO_ENTRY | "Diagnostic Tool Connected" / "openpilot paused, car is stock"; NO_ENTRY "Diagnostic Tool Connected" | LOW (above the engine-off EPS's LOWER "LKAS Fault"), silent; refuse per press |
| `carBusOffStock @34` | PERMANENT, NO_ENTRY | "Car Bus Error" / "car is stock, openpilot off until restart"; NO_ENTRY "Car Bus Error: Restart Car" | LOWER, silent |
| `carBusOffAnnounce @35` | PERMANENT | the same words | LOW, one prompt, 3.5 s, once per onroad session |
| `carBusOffDisengage @36` | IMMEDIATE_DISABLE | "TAKE CONTROL IMMEDIATELY" / "Car Bus Error: Car Is Stock" | upstream's disengagement, raised only while something is engaged (a MADS paused while moving included) |

The disengagement text has its own event because an IMMEDIATE_DISABLE in `events_sp` makes MADS's
`check_contains(ET.IMMEDIATE_DISABLE)` true and would knock a paused MADS to disabled. `adjust_alerts()` drops, while
diag: `carNotReady/noEntry`, `controlsMismatch/noEntry`; while bus-off additionally `canBusMissing` and `canError`
PERMANENT/NO_ENTRY ("Likely Faulty Cable" was the wrong advice, and three lines on the comma 4) and `steerUnavailable`
PERMANENT/NO_ENTRY; and, on a frame ours is made, the three IMMEDIATE_DISABLE alerts it names. When the reason reaches
selfdrived after the disengagement, `late_alerts()` creates ours while upstream's is up (the `vsa_fault_alert`
pattern). All alerts are static and not on the comma 4 compact-banner list, so no mici file changes. In P an engage
press can show the car's own "Gear not D" refusal instead of ours (equal priority, made first); in D ours shows.

### 16.6 Params

| key | flags | writer -> readers |
|---|---|---|
| `DiagToolStock` | CLEAR_ON_MANAGER_START, CLEAR_ON_ONROAD_TRANSITION, CLEAR_ON_OFFROAD_TRANSITION; BOOL | card (`params_thread`, on change) -> pandad (10 Hz), selfdrived (10 Hz), card itself on a restart |
| `CarBusOffStock` | CLEAR_ON_MANAGER_START, CLEAR_ON_IGNITION_ON; BOOL | pandad (the latch poll and any latched poll that finds it missing, non-blocking; removed at key-off) -> selfdrived, pandad's seed |

Neither is BACKUP or PERSISTENT. Names avoid `HondaElesysStock...`, which would read as stock ACC mode.

### 16.7 Tests and replays

| module | tests | what it pins |
|---|---|---|
| `sunnypilot/selfdrive/car/tests/test_diag_tool_guard.py` | 38 | the address table, src 0 only, 2 in 1 s, parked and idle, the quiet clock (capped gaps, a dead bus never quiet - also with our echoes and the radar side still in `can`), the back-off, everything else on src 0 at its real rate (the board's and BLR's ids, the ECUs' answers, the car's traffic) and 11-bit OBD requests through `update()`, the constants written out, never on another car or in dashcam, both ELESYS variants identical, the records |
| `sunnypilot/selfdrive/car/tests/test_card_diag_hook.py` | 10 | the hook sites in `card.py`, the exact hold line, no Params I/O on the 100 Hz path, `controls_update`'s AST hash = `nsw-live`'s, the hold and release on a bare `Car` |
| `sunnypilot/selfdrive/car/tests/test_diag_tool_guard_replay.py` | 8 | the routes below, frame by frame; and that the fixtures hold what the detector must ignore |
| `sunnypilot/selfdrive/pandad/tests/test_car_bus_guard.py` | 16 | the shipping header compiled with a small driver (skips only without a C++ compiler; a header that does not compile fails), over every logged pandaState and synthetic cases (key-off, the one-poll dropout, fix 1 in the same poll, bus-off only), against a Python oracle |
| `selfdrive/pandad/tests/test_car_bus_guard` (native, `scons --extras`, run by `test_native.py`) | 1 program | the synthetic cases in C++ |
| `selfdrive/pandad/tests/test_pandad_stock` (native, `scons --extras`, run by `test_native.py`) | 1 program, 11 scenarios | the REAL `pandad.cc` + `panda_safety.cc` + `car_bus_guard.h` against a fake panda, poll by poll through `process_panda_safety()`: no car mode set on or left in an ignition-off poll, stock = `noOutput` in the deciding poll, every car mode sent as `0xdf` then `0xdc` with CarParams' own model and param (36 / 68); a normal drive, a bus-off then key-off, a one-poll dropout and a new onroad session, a pandad restart, fix 1 held at start-up and mid-drive (36 and 68), fix 1 at key-off, fix 1 and a bus-off in one poll, stock ACC mode |
| `sunnypilot/selfdrive/pandad/tests/test_stock_wiring.py` | 17 | the pandad wiring on the source; `setSafetyMode()` and `fetchCarParams()` hash-pinned to `nsw-live`; the ELESYS bits equal opendbc's; `is_car_safety_mode` equals the panda's; no TEC in the rule; both keys registered with these flags, no duplicate; both native tests listed in `test_native.py` and built with the extras |
| `sunnypilot/selfdrive/selfdrived/tests/test_car_stock_alert.py` | 37 | selfdrived.step() in miniature with the real MADS: both edges, a 60 s start-up hold, genuine mismatches kept, bus-off parked / engaged / MADS-only / MADS paused while moving / stopped / late reason, driving off with the tool still talking, precedence, FCW and AEB, driver monitoring stage 2, texts and the mici fit, the wiring |

Fixtures (`fixtures/ktuner/*.npz`, all under 120 KB) are built by `sunnypilot/tools/ktuner_fixtures.py` from the
parquet exports (it needs pyarrow: run it with the Windows Python where the logs are). Fix 1's keep, besides every
request and 1 in 10 responses, every src-0 frame in 0x600-0x7FF and every 29-bit frame on any src, two per (id, src)
per second, chosen without the request rule (`broad_keep()`): 17,386 board frames on 143 alone.

* **Fix 1** (full-rate rlogs, one card step per logged `can` message): 143 (whole route) and b5 (our own FW query)
  never stock; 144 (whole route), 145, 147 stock from the first parked carState (3.47, 3.18, 2.57 s), 5.5-5.9 s
  before the logged first `controls_update`, held to the end (on 145 also through its last second, when the car bus is
  heard again with KTuner still talking); dc stock 0.33 s before the logged first `controls_update`; db never.
* **Fix 2** (every 10 Hz pandaState): 143 and 129 never latch; 144, 145, 147, db, dc latch on the first poll the rule
  holds, within 0.3 s of the first car-mode poll, and are held through the route's last poll - the key-off sample,
  which pandad still polls onroad (145 has none: it ends with the ignition on) - and cleared on the first poll that is
  offroad too. Over all 194 route exports (131 with car-mode polls, 38 h) only those five latch.
* **Nothing changes where nothing triggers:** card's process replay over all of route 143 (90,314 `sendcan` messages)
  is byte-identical to `nsw-live` in the stand-down (safety 36) and in stock ACC mode (68), `sendcan` and `carState`.

### 16.8 Checking a route

1. `pandaStates`: the `safetyModel` / `safetyParam` timeline. A held start shows `elm327` for the whole tool session
   with `canState0` TEC/REC 0 and no `hondaNidec` until `diag_tool_stock on=False`; a bus-off fallback shows `noOutput`
   from the latch poll on, `busOffCnt` flat after it, and no `hondaNidec` at the key-off poll.
2. `errorLogMessage`: the JSON with `diag_tool_stock` / `diag_tool_seen`, and the `FORK car bus off stock`,
   `FORK stay stock`, `FORK leaving stock` lines.
3. `selfdriveState.alertType`: `diagnosticToolStock/permanent`, `carBusOffStock/permanent`,
   `carBusOffAnnounce/permanent`, `carBusOffDisengage/immediateDisable`; `onroadEvents` has `carNotReady` and no
   `controlsMismatch` while masked. (`onroadEventsSP` is not in the parquet export; decode the qlog with the fork's
   `LogReader`.)

### 16.9 What is left open

* **The board after a bus-off at speed** keeps K2 split and steering manual until key-off if it had already taken
  over (no hand-back, board decision 2). Fix 1 at key-on never lets it take over.
* **Whether the radar latches a fault** from fix 2's 0.1-0.2 s of isolation is unknown (the OBD-multiplexing precedent
  latched after about 1.5 s). A fault the storm already latched is not undone by the fallback.
* **A tool plugged in mid-drive while parked:** fix 1 enters within about 20 ms, pandad acts up to 0.2 s later; if the
  comma's frames fail before `DiagToolStock` reaches pandad, fix 2 latches first and openpilot is off until restart.
  Since the review fixes it no longer latches in the poll where both arrive together; the earlier window remains.
* **For the owner to confirm** (design defaults, not his words): a tool that goes quiet on the road leaves openpilot
  paused until the car is next in P at a standstill (resuming is entering the car mode, which would stand down a stock
  ACC he engaged meanwhile); the back-off's 30 s / 60 s for a tool that keeps coming back; and bus-off only, without
  TEC, as he worded it.
* **A device reboot mid-cycle** after a latch puts the car mode back; it re-trips within one poll if the fault persists.
* The root cause of the failed transmissions.

### 16.10 On the car (the owner, after installing)

1. A normal start, no tool: `pandaStates` 164 (36 before brake320; or 68), no new `errorLogMessage` lines.
2. KTuner datalogging before key-on: the note appears, `elm327` for the whole session, no ACC/CMBS/SRS lamps, KTuner
   logs cleanly.
3. Stop the datalog in P: 10 s later openpilot is available as after a normal start. (Start and stop it again within
   2 minutes: 30 s this time - the back-off.)
4. Plug KTuner in after the start, parked: stock within about 0.2 s (`noOutput`), or a fix-2 latch; both are logged.
5. KTuner connected but idle (the db case): possibly a fix-2 latch with one chime; note whether ACC/CMBS still latch
   after the short isolation.
6. Steps 1-3 again in stock ACC mode (68).
7. After any fix-2 latch, the key-off: `pandaStates` stays `noOutput`/`elm327` to the end - no `hondaNidec` and no
   "FORK leaving stock" at key-off - and the next start is normal.
