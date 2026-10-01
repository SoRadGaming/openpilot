# HONDA_ACCORD_9G_AU: the car port (fork area C)

This document lists everything the fork changes relative to upstream so that sunnypilot can drive a 2013-2015 Honda Accord (Australian market, V6L and Tech trims) fitted with an Elesys radar, a comma pedal (gas interceptor) and the EPS-LKAS gateway board. It is written for whoever next merges upstream into the fork. For each change it says what the change does, why it exists, which identifiers must survive, which test pins it, and how to put it back if upstream has rewritten the code around it.

The other two areas have their own documents in `docs/fork/`. Where a file is shared, this document describes only the car-specific hunks and points to the others:

- **Area A, gateway update:** updating the board's firmware from the comma over CAN. This covers the flasher, the hook, the app-slot image, the mici `gateway` page, the `EpsLkas*` params and the firmware identity fields.
- **Area B, LKAS gateway protocol:** openpilot steering the EPS through the board. This covers the 0x0E4 serial-domain path, 0x500 SP_HUD_STATUS, decoding 0x700-0x70F, driver-torque substitution, the integrator hold and the MADS hand-back.

History and specs already exist in these files. This document does not repeat them:

| document | what it is |
|---|---|
| `CHANGELOG-elesys.md` | the longitudinal work on this car, sections 1-20, with measurements |
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
| `opendbc/car/honda/values.py` | M | `HondaSafetyFlags.ELESYS_SCM_STANDDOWN`, `HondaFlags.ELESYS`, `CAR.HONDA_ACCORD_9G_AU`, `HONDA_ELESYS`, `STEER_THRESHOLD` entry, `FW_QUERY_CONFIG` non-essential ECUs | - |
| `opendbc/car/honda/fingerprints.py` | M | `FW_VERSIONS[CAR.HONDA_ACCORD_9G_AU]` | - |
| `opendbc/car/honda/interface.py` | M | transmission detection, longitudinal tuning (no `vEgoStopping` since the 2026-09 merge), `steerActuatorDelay`, `steerAtStandstill`, safety param, `minEnableSpeed`, and its exemption from the gas-interceptor `-1` in `_get_params_sp()` | the two lateral values exist because of the gateway (B) |
| `opendbc/car/honda/radar_interface.py` | M | Elesys radar parsing | - |
| `opendbc/car/honda/carstate.py` | M | gear decode (`update_gear_elesys()`, taken only when the gearbox frame has `GEAR`), `LKAS_PROBLEM` bus, `stockAeb`, `scm_buttons`, `econ_on` | B/A: `get_can_parsers()` registration of `GW_*`/`EPS_LIN_RAW`; `CarStateExt.update(..., ret_sp, ...)` |
| `opendbc/car/honda/carcontroller.py` | M | `compute_gb_honda_elesys()`, `brake_pump_hysteresis_elesys()`, dynamic-tuner hooks, 32-count brake release limit, SCM_BUTTONS re-send, no `LKAS_HUD` | B: `brake_release_scale()`, LDW bits, the `create_sp_hud_status()` block |
| `opendbc/car/honda/hondacan.py` | M | `create_brake_command()` units bit, `create_scm_buttons_no_cruise()` | B: `create_steering_control()` LDW, `create_sp_hud_status()` |
| `opendbc/car/car_helpers.py` | M | `skip_fw_query` | - |
| `opendbc/car/structs.py` | M | none of its own; described in 6.5 because of the capnp rule | B (`LateralControl`, `LinbusGateway` 0x704/0x70B fields, `driverTorqueStale`), A (`fw*` fields) |
| `opendbc/car/tests/routes.py` | M | test route | - |
| `opendbc/car/torque_data/substitute.toml` | M | torque data substitute | - |
| `opendbc/sunnypilot/car/car_list.json` | M | car list entry | - |
| `opendbc/dbc/generator/honda/*.dbc`, `opendbc/dbc/honda_accord_2015au_radar.dbc` | A/M | all of them, except the two in the next column | `_sunnypilot_linbus_gw.dbc` (B, A); byte 2 of 0x0E4 in `_steering_control_e.dbc` (B) |
| `opendbc/safety/modes/honda.h` | M | the stand-down safety mode | 0x500 on its TX list is B's frame |
| `opendbc/safety/tests/common.py`, `opendbc/safety/tests/test_honda.py` | M | safety tests | B: the Elesys-only `0x500` exemption in `common.py` |
| `opendbc/sunnypilot/car/honda/carstate_ext.py` | M | `fuelGauge` | B (gateway decode, driver torque), A (`_update_linbus_firmware`) |
| `opendbc/sunnypilot/car/honda/dynamic_tuning.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/elesys_gas.py` | A (2026-10) | the gas law v1/v2, `HondaElesysGasLawV2`, drive-mode slots and crossfade (9.2) | - |
| `opendbc/sunnypilot/car/honda/gas_interceptor.py` | M | the import and the one call into `elesys_gas.py`, and the tuner's `observe_pedal` hook | - |
| `opendbc/car/honda/tests/test_elesys.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/test_elesys_gas.py` | A (2026-10) | all (9.2) | - |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | A | sections 1-6, 9, 16 and 17 | B: sections 7, 8, 10-15 (including 14b) |

### 1.2 sunnypilot fork

| file | status | area C content | other areas |
|---|---|---|---|
| `.gitmodules`, `opendbc_repo` (gitlink) | M | points the submodule at the opendbc fork (Other, 13.1) | - |
| `openpilot/common/params_keys.h` | M | 5 `HondaDyn*` keys and `HondaElesysGasLawV2` (11.1) | A: `EpsLkas*` keys |
| `openpilot/cereal/custom.capnp` | M | none; area C code reads `CarStateSP.driverTorqueStale` | B, A |
| `openpilot/selfdrive/car/card.py` | M | `skip_fw_query=bool(fixed_fingerprint)` | A: firmware identity staging/writing, flash trace |
| `openpilot/selfdrive/car/helpers.py` | M | none. The `lateralControl` rebuild in `convert_carControlSP()` is area B; it is described in 10.5 because its failure took down the car's radar path | B |
| `openpilot/sunnypilot/selfdrive/controls/lib/stopping_tune.py` | A (2026-09 merge) | `STOPPING_SPEED` and `STOPPING_DECEL_RATE`, keyed by fingerprint (10.1) | - |
| `openpilot/selfdrive/controls/lib/drive_helpers.py` | M (2026-09 merge) | `should_stop(..., v_ego_stopping=None)` (10.1) | - |
| `openpilot/selfdrive/controls/lib/longitudinal_planner.py` | M (2026-09 merge) | passes the car's stopping speed to both `should_stop()` calls (10.1) | - |
| `openpilot/selfdrive/controls/lib/longcontrol.py` | M | stopping-exit debounce; the per-car stopping ramp | - |
| `openpilot/tools/joystick/joystickd.py`, `openpilot/tools/longitudinal_maneuvers/maneuversd.py` | M (2026-09 merge) | pass the car's stopping speed to `should_stop()` (10.1) | - |
| `openpilot/selfdrive/controls/lib/latcontrol.py`, `openpilot/selfdrive/controls/lib/latcontrol_torque.py`, `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py`, `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_ext_base.py` | M | documented in 10.3; the reason for them is the gateway | B |
| `openpilot/selfdrive/controls/controlsd.py`, `openpilot/sunnypilot/selfdrive/controls/controlsd_ext.py` | M | documented in 10.4 | B |
| `openpilot/selfdrive/selfdrived/selfdrived.py` | M | none; its one hunk is for MADS | B |
| `openpilot/selfdrive/controls/lib/desire_helper.py`, `openpilot/selfdrive/modeld/modeld.py`, `openpilot/sunnypilot/modeld_v2/modeld.py` | M | `NUDGE_FIRM` | B: `driver_torque_stale` |
| `openpilot/sunnypilot/mads/mads.py`, `openpilot/sunnypilot/mads/state.py` | M | none (10.7 notes one rule that applies to every car) | B |
| `openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py` | M | Honda tuner toggle | - |
| `openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py` | M | Honda brand page | - |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` | A | mici vehicle page | - |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | M | vehicle button | A: gateway button |
| `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml`, `.../vehicle.yaml`, `openpilot/sunnypilot/sunnylink/settings_ui.json` | M | sunnylink rows | - |
| `openpilot/sunnypilot/sunnylink/statsd.py` | M | tuner telemetry | - |
| `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py` | M | UTF-8 fix (Other, 13.2) | - |
| `openpilot/selfdrive/controls/tests/test_stopping_debounce.py`, `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`, `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py`, `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | A | see section 12 | - |
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
- `opendbc/car/torque_data/substitute.toml`: `HONDA_ACCORD_9G_AU = HONDA_ACCORD`. The car falls through to the default `else:` branch of the lateral tuning chain in `interface.py` (`torqueBP/V = [[0, 2560], [0, 2560]]`, `configure_torque_tune()`). It therefore runs the torque lateral controller with the 2018+ Accord's `LAT_ACCEL_FACTOR`, `MAX_LAT_ACCEL_MEASURED` and `FRICTION` until torqued has learned its own values.
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
- `STOPPING_SPEED` and `STOPPING_DECEL_RATE` (`stopping_tune.py`, keyed by the string)
- `substitute.toml`, `car_list.json` and `routes.py`

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
| radar tracks | 0x430-0x439 and 0x440-0x445 at 20 Hz, trigger 0x445 | 0x410-0x417 and 0x420-0x424 at 10 Hz, trigger 0x423 | 4.8, 5.2 |
| `BRAKE_COMMAND.SET_ME_1` | constant 1 | cluster units flag | 7.3 |
| gas interceptor law | `interp(v, [0, 10], [0.4, 1.0])` | `elesys_gas.py`: v2, the measured pedal response (default), or v1, `elesys_gas_multiplier()` | 9.2 |
| fuel | not decoded | `fuelGauge` from `FUEL_LEVEL` | 6.4 |
| `steerActuatorDelay` | 0.15 (default branch) | 0.38 | 5.1 |
| `steerAtStandstill` | False | True | 5.1 |
| `minEnableSpeed` | 25.51 mph, or -1 with a gas interceptor (upstream `4455464a`) | 19 mph, pedal or not | 5.1 |
| stopping speed | `should_stop()`: 0.3 m/s on the measured speed | 0.8 m/s (`stopping_tune.py`) | 10.1 |
| stopping ramp toward `stopAccel` | 1.0 m/s³ | 0.8 m/s³ (`stopping_tune.py`) | 10.1 |

---

## 4. DBCs

### 4.1 How the car DBC is assembled

`opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc` (new) is a list of imports plus one message. The generator writes `opendbc/dbc/honda_accord_au_2015_can_generated.dbc`. That output is gitignored (`.gitignore`: `opendbc/dbc/*_generated.dbc`), so only the sources are committed.

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

`GEAR_SHIFTER` raw 0 deliberately has no VAL entry. On this car it means both Sport and `between detents`, so mapping 0 to S would produce a phantom Sport on every shift. Over 473k frames there were 47 zero runs, median 3 frames and max 520 ms, and all of them were shift transients. `GEAR = 26` (Sport) has never been observed (6.1).

Upstream has since added `11 B` to the `GEARBOX_CVT` VAL table in `_gearbox_common.dbc`. The copy here will not get it. That does not matter on this car, which has no 0x191.

### 4.8 `opendbc/dbc/honda_accord_2015au_radar.dbc` (new, written by hand, not generated)

`VERSION "Accord 9G AU Radar BUS 1"`. Messages:

| message | id | notes |
|---|---|---|
| `RADAR_VEHICLE_STATE` | 0x300 (768) | `VEHICLE_SPEED 15:8` kph |
| `RADAR_VEHICLE_STATE2` | 0x301 (769) | no signals |
| `RADAR_DIAGNOSTIC` | 0x400 (1024) | `RADAR_STATE 7:8`, `NOT_READY 15:8`, `RADAR_FLAGS 23:8` |
| `RADAR_TRACK_A0`..`A7` | 0x410-0x417 (1040-1047) | `LONG_DIST 6:15` x0.0078125 m, `LAT_DIST 20:13` signed x0.0078125 m, `FLAG_B21`, `FLAG_B22`, `NEW_TRACK 23:1`, `REL_SPEED 37:14` signed x0.0078125 m/s, `CHECKSUM`, `COUNTER`. A0 also has `NEW_SIGNAL_1 55:5` |
| `RADAR_TRACK_B0`..`B4` | 0x420-0x424 (1056-1060) | same layout as the A tracks |
| `RADAR_STATUS_4FF` | 0x4FF (1279) | `STATUS_A`..`STATUS_D` |

The signal names `LONG_DIST`, `LAT_DIST`, `REL_SPEED`, `NEW_TRACK` and `RADAR_STATE` must not change: `radar_interface.py` reads them by name on the path it shares with Nidec. Background is in `S:/OP/radar_firmware_bit_spec.md` and `S:/OP/HOW_OP_USES_RADAR.md`. The second describes an older fork whose ranges skipped 0x417 and 0x424; the current code reads both.

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
| `steerActuatorDelay = 0.38` | `if candidate in HONDA_ELESYS:` after the lateral tuning chain | the command goes to the board and out on 9600-baud serial. lagd measured 0.383 s (route `000000d3`) and 0.377 s (`000000d4`). The value matters only before lagd has converged, when lagd publishes it plus 0.2 s (0.58 s), and when `LagdToggle` is off (opendbc `1da246ae`; since upstream `53e13a7bc` that is also this plus the toggle's 0.2 s offset). Since the 2026-09 merge lagd discards the fork's learned value (cache `VERSION 1`) and relearns only above 50 mph, so the 0.58 s lasts until then; whether to keep that is open (UPSTREAM-2026-09.md items 7 and 8) | the reason is area B |
| `steerAtStandstill = True` | same block | keeps `latActive`, and with it `STEER_TORQUE_REQUEST`, alive at a stop so the board keeps the cluster's lane graphic up. The board holds its target at 0 below 5 km/h (`GW_STANDSTILL_CPH`) (opendbc `bb0fe222`). Upstream `controlsd.py` still reads `CP.steerAtStandstill` in the same `latActive` expression | the reason is area B |
| stand-down safety param | see 3.1 | see 8 | - |
| `minEnableSpeed = 19 mph` | `elif candidate in (CAR.HONDA_ODYSSEY_TWN, CAR.HONDA_ACCORD_9G_AU):`, and in `_get_params_sp()`: `stock_cp.minEnableSpeed = -1. if ret.enableGasInterceptor and candidate not in HONDA_ELESYS else stock_cp.minEnableSpeed` | from the original port (opendbc `04a48a0a`); no measurement recorded. Upstream `4455464a` sets `-1` for every gas-interceptor car; the merge exempts `HONDA_ELESYS` so this car keeps 19 mph (a replay of `CarParams` gives 8.494 m/s). With the pedal `pcmCruise` is False, so the value never gated engagement through `belowEngageSpeed`; what `-1` would have changed is the `manualRestart` warning at a standstill | keep both lines; the exemption is tagged `FORK(HONDA_ACCORD_9G_AU)` |

The car takes the default lateral branch (2.4). That branch first sets `steerActuatorDelay` to 0.15, and the Elesys block then overwrites it.

### 5.2 `radar_interface.py`

- `_create_nidec_can_parser()`: for `HONDA_ELESYS` it parses `[0x400] + list(range(0x410, 0x418)) + list(range(0x420, 0x425))` at 10 Hz on bus 1, instead of 0x400, 0x430-0x439 and 0x440-0x445 at 20 Hz.
- `self.radar_type = 'Elesys' if CP.carFingerprint in HONDA_ELESYS else 'Nidec'`.
- `self.trigger_msg = 0x423` for Elesys, 0x445 otherwise. 0x423 is not the last track (0x424 is), and the reason is not recorded.
- Fault: `self.radar_fault = cpt['RADAR_STATE'] not in (104, 111, 125)` for Elesys, `!= 0x79` otherwise. `radar_wrong_config` stays `RADAR_STATE == 0x69` for both. The commits do not record where 104/111/125 came from (opendbc `04a48a0a`, `304d1d82` `Fixed Radar Range`).

Track decoding (`LONG_DIST < 255`, `dRel`, `yRel = -LAT_DIST`, `vRel`) is the shared upstream path. Upstream removed the `aRel`/`yvRel`/`measured` assignments in the same function and moved `track_id` into the base class; the 2026-09 merge took both. The fork's hunks do not touch those lines.

Re-apply: keep the `radar_type` switch and the three Elesys branches. No test covers this file directly.

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
- `CarStateExt.update()` now takes `(ret, ret_sp, can_parsers)`, and on Elesys `get_can_parsers()` registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION` and `GW_BUILD` with `float("nan")`. Both changes belong to areas B and A, but the call site in `carstate.py` must keep passing `ret_sp`.

### 6.5 `opendbc/car/structs.py`

Area C adds no fields of its own here. The file is listed because its relationship with `openpilot/cereal/custom.capnp` is a merge hazard. The additions:

- `CarControlSP.lateralControl: CarControlSP.LateralControl` with `integrator: float`, `saturated: bool`, `integratorFrozen: bool` (area B).
- `CarStateSP.driverTorqueStale: bool` (area B; read by area C's `desire_helper.py`, 10.2).
- `CarStateSP.linbusGateway: CarStateSP.LinbusGateway`: `engaged`, `dryRun`, `valid`, `actuating`, `present`, the `GW_STEER_GRANT` fields `grantValid` through `latchedUntilKeyOff` (area B), and `fwValid` through `fwBuildValid` (area A).

**The rule:** card publishes these dataclasses through `convert_to_capnp()`, which passes them into `custom.CarStateSP.new_message(**dict)` by keyword. So:

- **Field names must match** `openpilot/cereal/custom.capnp` exactly.
- **capnp ordinals must be unique and must never change:** `CarControlSP.lateralControl @5`, `CarStateSP.linbusGateway @1`, `CarStateSP.driverTorqueStale @2`, `LinbusGateway @0`-`@26`. Upstream's `CarControlSP` currently ends at `@4` and `CarStateSP` at `@0`, so there is no collision today. If upstream adds fields to either struct, the fork's fields keep their numbers and upstream's new ones must be renumbered on the fork side (or the fork's moved, which breaks old logs).
- **Dataclass field order does not matter.** It already differs: `structs.py` declares `driverTorqueStale` before `linbusGateway`, while the capnp has them the other way round. The in-code comments in `structs.py` say names and order must match; the order part is overstated.

On the way in, `openpilot/selfdrive/car/helpers.py` must rebuild every nested struct by hand (10.5).

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

### 7.2 `brake_pump_hysteresis_elesys()`

The signature is `brake_pump_hysteresis_elesys(apply_brake, v_ego, brake_anchor, last_pump_ts, ts) -> (pump_on, brake_anchor, last_pump_ts)`. The controller keeps the anchor in `self.pump_brake_anchor`.

Constants: `ELESYS_PUMP_RUN = 0.5`, `ELESYS_PUMP_REFRACTORY = 3.0`, `ELESYS_PUMP_DEADBAND_BP = [0., 60., 200.]`, `ELESYS_PUMP_DEADBAND_V = [12., 6., 3.]`, `ELESYS_PUMP_BIG_RISE = 15`, `ELESYS_PUMP_HOLD_REFRESH = 30.0`, `ELESYS_PUMP_MOVE_REFRESH_BP = [40., 200.]`, `ELESYS_PUMP_MOVE_REFRESH_V = [12.0, 6.0]`.

What it does, in order:

1. **Continuous run** when `0.15 <= v_ego < 2.5 and apply_brake > 100`, or when `v_ego >= 2.5 and apply_brake > 200`. These branches restore commit `2905e73d` (`Fixed Pump Blind Spot on Saturated Braking`). A v4 tuning had silently reverted it: at cb >= 200, duty had fallen from 1.00 to 0.32 and the worst pump-off gap had grown from 0.16 s to 5.50 s.
2. **Re-prime** otherwise, when the command rises past a deadband that scales with the command (12 counts at light braking, 3 at firm braking). The rise has to come inside the current run or after the refractory period, or be a 15-count jump, which re-primes immediately. There is also a periodic backstop: 30 s at standstill, and 12 s down to 6 s while moving, scaled by the command. The anchor follows real releases, meaning drops of more than 6 counts.
3. **Run length:** `pump_on` stays on for `ELESYS_PUMP_RUN` after each prime, and only while `apply_brake > 0`.

Why:

- Upstream's 20 s refresh let pressure bleed away, which was the stop-overshoot bug. Re-priming on every +1 count made the pump stutter.
- Replayed over 14 routes (405.9 min engaged, 149.2 min with brake commanded), upstream would run the pump for 48.55 min over 2302 starts. The v4 tuning, **before** the graded deadband, ran it for 38.0 min over 1619 starts. The graded deadband then took total run time from 38.0 to 33.3 min for 7.8% more starts, and light-braking run time from 15.75 to 12.10 min, with the worst moving pump-off gap unchanged. The code comment does not give a single figure for the shipped code with every later change applied.
- The block comment warns **not** to lengthen the moving backstop to quiet the pump. Going from 12 s to 20 s grows the worst dry stretch while braking and moving from 11.48 s to 18.42 s (route `00000020`).

All of these numbers are open-loop replays of command traces recorded under older tunings, so only the differences between variants mean anything (code comment; `CHANGELOG-elesys.md` section 6; `FEATURES-elesys.md`, `The brake pump is quieter`).

Only `HONDA_ELESYS` calls this function; every other car still uses upstream's `brake_pump_hysteresis()`. Tests: `TestBrakePumpHysteresis` has 20 cases, including `test_upstream_default_unchanged`, `test_saturated_moving_braking_pumps_continuously`, `test_backstop_is_load_scaled_not_flat` and `test_standstill_hold_is_quiet`.

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

`CarController.update()` sends it under `if self.CP.carFingerprint in HONDA_ELESYS and self.CP.openpilotLongitudinalControl and self.frame % 4 == 0:` on `self.CAN.camera` (bus 2). That is 25 Hz, the stock frame's rate; the panda's RX check expects 25 Hz for 0x1A6 on bus 0. It works together with the panda blocking the driver's 0x1A6 from bus 0 to bus 2 (8.1).

Why: while the stock ACC believes the main switch is off it stays in standby. It then stops issuing comfort-braking commands that openpilot would block, which is what tripped the brake system (the code says it stops the blocked ACC brake that trips TSA; background in `S:/OP/TSA_acc_disengage_approach.md` and the other `TSA_*.md`). CMBS does not depend on MAIN, so collision braking and FCW keep working. The PCM on bus 0 still sees the real buttons, so openpilot engages normally.

**Failure mode:** if card stops running, nothing re-sends 0x1A6, the radar stops hearing the buttons at all, and the car throws ACC and CMBS faults. This is what happened on routes b5-b8 (sunnypilot `d11d2c9a8`).

### 7.6 Dynamic tuner hooks in `CarController`

All of these do nothing while `HondaDynamicTuningEnabled` is off. Integration section 1 pins bit-identical output with the toggle off. The gas law (9.2) is not a tuner hook: `HondaElesysGasLawV2` picks it whatever the toggle says.

| hook | code | effect with the tuner on |
|---|---|---|
| construct | `self.dynamic_tuner = HondaDynamicTuner(CP, CP_SP)` | reads the params once, when the controller is built, which is at ignition |
| pitch feedforward | `hill_accel = self.dynamic_tuner.update_state(CC, CS)`; `adjust_accel = accel + hill_accel` feeds `compute_gas_brake()` and, without an interceptor, `pcm_accel` | compensates for road grade |
| aero | `wind_brake * self.dynamic_tuner.wind_scale()` on the brake side, also passed to the interceptor; `self.dynamic_tuner.update_wind(CC, CS, float(wind_brake_ms2))` | **none since 2026-10**: `wind_scale()` returns 1.0 and `update_wind()` does nothing (9.1, retired channels). The calls stay so this upstream file needs no edit. With the tuner on that moved the brake-on point slightly: the persisted scale was about 0.80, so light braking at 25 m/s now gets about 5 counts less brake |
| brake gain | `brake_gain = self.dynamic_tuner.brake_gain(CC, CS, float(apply_brake))` multiplies `apply_brake` before it is scaled to counts | learned brake scale |
| brake release limit | `if self.dynamic_tuner.enabled and CC.longActive and not CS.out.gasPressed and not CS.out.brakePressed: apply_brake = max(self.apply_brake_last - 32, apply_brake)` | the brake command can fall by at most 32 counts per 50 Hz frame, to match factory. This stops the lurch as the car lets go at a stop. It is bypassed on disengage and on driver override, and it runs before the pump logic so the Elesys anchor sees exactly what goes on the wire |
| pedal | `GasInterceptorCarController.update(..., self.dynamic_tuner)` | the tuner only observes the pedal (`observe_pedal`, per-mode data counts); see 9.2 |
| persist / log | `self.dynamic_tuner.persist(self.frame)` and `self.dynamic_tuner.log_state(self.frame)` at the end of `update()` | see 9.1 |

**Scope:** `HondaDynamicTuner._is_applicable()` accepts every Nidec car with openpilot longitudinal, not only this one. With the toggle on, another Nidec car would get the pitch term, the brake gain and the 32-count release limit. Its interceptor command is upstream's, bit for bit (the retired pedal gain used to reach it too).

### 7.7 Comments that record decisions

Two `FORK:` comments in `carcontroller.py` record decisions rather than code:

- **The pedal/PCM crossfade was removed** (opendbc `aa73e60a`). Across 17 engaged routes, 289,625 `ACC_HUD` frames all had `PCM_GAS = 0` and `PCM_SPEED = 0`; the PCM was never shown to respond, and the interceptor is the easier actuator to control. The interceptor owns the gas at every speed. Integration section 6 pins this by decoding `PCM_GAS` from the frames the controller emits.
- **MVL's 3x faster brake rise was deliberately not ported.** Combined with the learned brake gain, it would reach full brake from a gentle request in about 0.1 s.

---

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
| pitch | nothing learned. A feedforward of `sin(pitch) * g`, from `CC.orientationNED[1]` through `PITCH_RC = 0.5` s | added to the accel target | `PITCH_ACCEL_LIMIT = 1.5`. Faded out between 5 and 2 m/s, active only in `LongControlState.pid`, and decays out after `PITCH_STALE_FRAMES = 100` frames without a pose |

**Why the pedal and aero learners were retired** (2026-10, routes 9f..103, replaying the unmodified tuner over the logs):

- The pedal learner could not persist anything. The live gain moved `2e-4 * err` per admitted sample, the persisted estimate `3e-4` of the difference per sample, and each ignition reset live to persisted, so each drive's progress was thrown away. Admitted pedal time was about 1.6% of engaged time; six weeks moved the persisted gains from `[1,1,1,1.004,1.004,1]` to `[1,1,.996,.996,.999,.993]`.
- It would have learned the wrong way anyway. Its gate read `actuators.accel`, which includes openpilot's own integrator, so at 12-30 m/s it admitted frames where the integrator had lifted a ~0.36 target over the 0.4 gate and read under-delivery (+0.04 to +0.12) where the car over-delivers (-0.19 to -0.33 gated on the planner's target).
- The aero scale was a random walk: median 0.37 of its 0.7-1.5 range inside a drive, rails hit on b0/c8/ce (1.5) and de/fd (0.7), drift uncorrelated with speed mix, and it also scaled the brake-side credit. Under the v2 gas law it would lose its lever on the cruise pedal and rail.

The fix for the gas side is the measured law in 9.2, not a better learner. `HondaDynPedalGain0`-`5` and `HondaDynWindFactor` are no longer registered, read or written; a device keeps the old files on disk, unread.

**Per-mode data.** `update_state()` counts engaged seconds above `MODE_MOVING_SPEED = 1.0` m/s per slot (D, ECON, S; `elesys_gas.drive_mode_slot`), and `observe_pedal()` counts samples that pass `_learn_ok()` without the reference-mode gate plus the audit's steady-pedal rule: speed at least `ADMIT_MIN_SPEED = 3.0` m/s, a fresh pose with `|pitch| < 0.08` rad, and the last `ADMIT_WINDOW = 50` interceptor commands (1 s) all inside 0.02-0.9 with a standard deviation under 0.008. The seconds persist as running totals, `HondaDynModeSecD`/`ECON`/`S`. In a month there were 87 s of engaged ECON and 79 s of engaged S, about 11 s and 28 s of it admissible: too little to fit anything, so this only collects.

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

**Telemetry.** Every `LOG_INTERVAL = 500` frames (5 s) it writes one `carlog.info` line tagged `hondadyn`, with these fields: `gaslaw=` (`v1`/`v2` on this car, `nidec` on another), `slot=` (`D`/`ECON`/`S`), `modesec=[D,ECON,S]` (engaged moving seconds this drive), `modeadm=[D,ECON,S]` (admitted steady-pedal samples this drive, 50 Hz), `modetot=[D,ECON,S]` (the persisted running totals), `brake=` and `brakec=` (both shown as gains, `1.0 + offset`, since opendbc `ff9f3211`), `pitch=`, `settle=`, `settles=`, `eng=`, `aref=`, `aerr=`, `stale=`, `werr=`, `gear=`, `econ=`, `modeok=`. `pedal=`, `pedalc=` and `wind=` went with their learners (2026-10). card forwards `carlog` to cloudlog, so the lines come back in a route's `logMessage`. The owner parses them with `S:/OP/sunny_logs/parse_hondadyn.py`, which is outside the repo.

**Known issues recorded in the code:**

- The pitch fade band (2-5 m/s) lies inside the PID state. On a stop approach the grade term is handed back to openpilot's integrator faster than the integrator can follow: modelled shortfall 0.115 m/s^2 on a 4% downhill, 0.249 on a 10% one. This was left alone on purpose until there is road data.
- Two comments give 0.8 m/s as the speed where stopping begins: the pitch-fade `KNOWN ISSUE` paragraph and the brake-fade comment above `brake_gain()`'s return. They used to cite `vEgoStopping`; since the 2026-09 merge they cite `stopping_tune.py`, where the value now lives (10.1).

**Removed history**, noted here so nobody restores it by accident: the PCM blend was deleted in opendbc `aa73e60a` / sunnypilot `cb4e0c34b`. That covered `HondaDynamicPcmBlendEnabled` and the learned `HondaDynGasFactor`, `HondaDynGasAlpha`, `HondaDynAverageFactor`, `HondaDynSpeedFactor` and `HondaDynSpeedAlpha`.

Tests: `test_dynamic_tuning.py` (sections 1-5 and 10-16; 6-8 went with the PCM blend, 9 with the aero learner) and sections 1-6, 16 and 17 of `test_dynamic_tuning_integration.py` (see 12).

### 9.2 The gas law: `elesys_gas.py` and `gas_interceptor.py`

`gas_interceptor.py` is upstream sunnypilot's. The fork adds the `HONDA_ELESYS` import, constructs `self.elesys_gas = ElesysGasLaw()` in `__init__` for this car with an interceptor, calls `self.elesys_gas.update(CC, CS, gas, brake, wind_brake)` in place of upstream's line on this car, and calls `tuner.observe_pedal(CC, CS, self.gas, law)` when a tuner is passed. Every other car runs upstream's line bit for bit, with or without a tuner. Everything else is in the fork-owned `opendbc/sunnypilot/car/honda/elesys_gas.py`, and `gas_interceptor.py` re-exports `ELESYS_GAS_BP`, `ELESYS_GAS_V` and `elesys_gas_multiplier`.

**Which law.** `HondaElesysGasLawV2` (`PERSISTENT | BACKUP`, BOOL, default `"1"`) is read once, when `CarController` is built (ignition). Anything unreadable gives the default. The `hondadyn` line says which law ran (`gaslaw=`).

**v1** is the law that shipped before, bit for bit: `pedal = clip(gm1(v) * (gas - brake + 0.75 * wb), 0, 1)`, with `gm1 = elesys_gas_multiplier(v)` = `interp(v, [0, 3, 6, 10, 15, 20], [0.55, 0.85, 1.20, 1.55, 1.95, 2.75])` and the retired learned gain at exactly 1.0. The history of that curve: route `ac35d9891f`; opendbc `2905e73d`, `2bc5c4db`; `CHANGELOG-elesys.md` section 7.

**v2** (`elesys_pedal_v2`), with `gas = net/4.8` and `brake = -net/2.6` from `compute_gb_honda_elesys()` and `wb` the aero term:

- net >= 0: `pedal = off(v) + gas * gm2(v) / MODE_K[slot]`
- net < 0: `pedal = off(v) * (1 - brake / (0.75 * wb))`, which is zero at net = -1.95 wb, v1's pedal-zero point
- `gm2(v) = 4.8 / k(v)` with the measured `k` at `ELESYS_FF_BP = [0, 3, 6, 10, 15, 20, 25, 30]` m/s = `[8.73, 5.65, 6.8, 4.7, 3.4, 2.9, 2.0, 2.0]` m/s^2 per unit pedal. 0 and 3 m/s are v1's values exactly (4.8/0.55 and 4.8/0.85), and the table is interpolated in the same quantity v1 interpolates (`ELESYS_FF_GM`), so 0-3 m/s is v1's law: the same gain bit for bit, and the same pedal to within float rounding (the sum is grouped differently; tested to 1e-12).
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

Tests: `test_elesys_gas.py` (31 tests: golden pedal per breakpoint for net in {-0.5, -0.1, 0, 0.5, 1, 2}; continuity at 0 and at the pedal-zero point; monotonic in net; zero at and below the brake-on point; v2 equal to v1 at or below 3 m/s; the negative-branch slope never steeper than v1's and equal below 16.8 m/s; the crossfade bound; the slot rule; the param read; non-Elesys bit identity); integration sections 16 (both laws through the real `CarController`, the brake command identical under both) and 17 (`update()` never raises, 0x1FA on every even frame); `TestElesysGasMultiplier` in `test_elesys.py` for the v1 curve.

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

This car has two rules, on top of upstream's lane-change logic. Since the 2026-09 merge that logic is upstream's rewrite (`7d325d665`, `4532320fb`, `2d859a8ca`): no `DESIRES` table, no `laneChangeFinishing` state, at least 0.5 s in `laneChangeStarting`, a blinker already on at engagement enters `preLaneChange` at once (it still waits for the nudge), and an opt-in road-edge block (`RoadEdgeLaneChangeEnabled`, off by default). A replay of `DesireHelper` on route `00000103`'s inputs gives both of that route's lane changes (471.34 s left, 487.64 s right) at the same instants as the fork.

**1. `STEER_THRESHOLD` of 600** (`values.py`, `CAR.HONDA_ACCORD_9G_AU: 600`). `steeringPressed` is `abs(steeringTorque) > STEER_THRESHOLD`, where the default threshold is 1200.

- Evidence, from 21 `preLaneChange` windows on route `000000d9`: the 11 that armed a lane change peaked at 1556-5839 counts. Of the 10 that did not, six peaked between 600 and 1225, one of them at 937 after nine seconds of trying at 60 km/h.
- 600 catches those weak nudges without changing any nudge that already worked.
- Why this car needs it: below the EPS's 50 km/h floor the wheel is unassisted, so a nudge easily passes 1200. With 160 counts of gateway assist in the wheel, the same nudge lands around 900.
- 600 is the value the 11G Accord already uses (opendbc `b7d15a98`, sunnypilot `715ea5df6`).

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

Cars not in the table keep upstream's single-frame rule. `steeringPressed` itself does not change, so DM and the lateral controller still see a light hand on the wheel.

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

### 10.5 `openpilot/selfdrive/car/helpers.py`

`convert_carControlSP()` now rebuilds `struct_dataclass.lateralControl = structs.CarControlSP.LateralControl(**remove_deprecated(struct_dict.get('lateralControl', {})))`. Every nested struct has to be rebuilt by hand, or it reaches the car controller as a plain dict.

Missing this line crashed card on every frame on routes b5-b8. With card down nothing re-sent `SCM_BUTTONS`, so the car threw ACC and CMBS faults (sunnypilot `d11d2c9a8`).

Test: `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py`. It also checks, from the capnp schema, that every nested struct of `CarControlSP` is rebuilt, so the next one added cannot repeat the crash. **Any new nested field in `CarControlSP` needs a matching line here.**

### 10.6 `openpilot/selfdrive/selfdrived/selfdrived.py`

The only change adds `'carStateSP'` to the `SubMaster` list, for MADS (area B). card publishes `carStateSP` unconditionally at 100 Hz.

### 10.7 `openpilot/sunnypilot/mads/mads.py` (area B, with one rule for every car)

Area B owns this file. One rule in it, though, is gated neither on the car nor on the gateway: a steering rate of at least `EMERGENCY_STEER_RATE = 200.0` deg/s for `EMERGENCY_STEER_FRAMES = 2` frames adds `lkasDisable` on **every** car running MADS on this fork (sunnypilot `35622a994`). It is mentioned here so that a car maintainer is not surprised by it.

---

## 11. UI, sunnylink and statsd

There are two settings. `HondaDynamicTuningEnabled` can be reached from four places; `HondaElesysGasLawV2` (the gas law, 9.2) from sunnylink, and the mici and big UI show its value. Both take effect at the next ignition, because they are read only once. There are read-only views of the learned brake gain and the engaged time per drive mode.

### 11.1 Params (`openpilot/common/params_keys.h`)

| key | flags | type | default |
|---|---|---|---|
| `HondaDynamicTuningEnabled` | `PERSISTENT`, `BACKUP` | BOOL | `0` |
| `HondaDynBrakeGain` | `PERSISTENT` | FLOAT | `0.0` |
| `HondaDynModeSecD`, `HondaDynModeSecECON`, `HondaDynModeSecS` | `PERSISTENT` | FLOAT | `0.0` |
| `HondaElesysGasLawV2` | `PERSISTENT`, `BACKUP` | BOOL | `1` |

`HondaDynPedalGain0`-`5` and `HondaDynWindFactor` were removed in 2026-10 with their learners. The learned values are not `BACKUP`. They change every 60 s and belong to one car, and a restored backup could bring back a tune learned on different hardware. The type must stay FLOAT: statsd depends on it to send numeric fields, and `_ParamWriter` counts write errors if a key is missing. The `EpsLkas*` keys in the same hunk belong to area A.

### 11.2 Big UI (comma 3/3X)

**`openpilot/selfdrive/ui/sunnypilot/layouts/settings/cruise.py`**

- Adds `self.honda_dyn_toggle`, titled `Honda Nidec Dynamic Longitudinal Learning (Alpha)`, after the custom ACC items.
- The description strings are `HONDA_DYN_DESC`, `HONDA_DYN_VEHICLE_NOTE` and `HONDA_DYN_IGNITION_NOTE`.
- `_sync_honda_dyn_toggles()` edge-syncs the toggle from the param. It is needed because `ToggleSP` reads its param only once, at construction, and the same param is edited from other places.
- The toggle is not gated on brand.

**`openpilot/selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py`**

- `HondaSettings` was empty upstream. It now has the toggle and a `Learned Values` row with a RESET button. Reset is offroad only, sits behind a confirmation dialog, and re-checks offroad when the dialog is confirmed.
- `update_settings()` rebuilds the toggle description each frame from `DYN_DESC` and `DYN_IGNITION_NOTE`. When `ui_state.has_longitudinal_control` is false it prefixes `DYN_NO_LONG_DESC` in bold (`This feature is unavailable because sunnypilot Longitudinal Control is not enabled on this car.`). It only changes the text; the toggle itself stays settable.
- Module-level API used by the mici page: `LEARNED_DEFAULTS`, `MODE_SLOTS`, `TUNING_PARAM`, `GAS_LAW_PARAM`, `GAS_LAW_DEFAULT`, `learned_value()`, `gas_law_v2()`, `gas_law_label()`, `mode_minutes()`, `mode_time_text()`, `reset_learned_values()`.
- The readout refreshes once a second (`LEARNED_REFRESH_S`). It shows the gas law the car will run from the next drive, the engaged minutes per drive mode, and the brake value as a gain (`x` followed by `1.0 + HondaDynBrakeGain`, sunnypilot `2b2d8e0a7`). The six pedal gains and the aero factor it used to show could not move and are gone (2026-10). Reset also zeroes the mode times.
- The key names and defaults are duplicated here on purpose instead of importing the tuner, so a failing opendbc import cannot blank the settings screen. A test keeps the copies in sync.

### 11.3 Small UI (comma 4, mici)

The comma 4 runs the small UI. It has no Cruise or Vehicle panel, so neither page above can be reached on it (sunnypilot `d8863e59e`).

- `openpilot/selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` (new): `VehicleLayoutMici(NavScroller)`, with `HondaLearnedInfo`, the toggle as a `BigParamControl`, and reset behind `BigConfirmationDialog`. `car_brand()` gets the brand from `CarPlatformBundle` first and `CP.brand` second, cached on a 1 s tick. `HondaLearnedInfo` shows two pairs: `gas law` with `v2   brake x1.02` (the gas-law setting, and the brake value as a gain), and `D / ECON / S` with the engaged minutes (`412 / 1.4 / 1.3 min`). Both values fit the 340 px card (rendered headless); they used to be the six pedal gains, which could not move, and the brake offset with the aero factor.
- `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py`: a `vehicle` `SettingsBigButton` (icon `icon_vehicle.png`) inserted with `items.insert(2, vehicle_btn)`, right after upstream's models row (index 3 before the 2026-09 merge, when sunnylink came first). It is visible when `car_brand() in ("", "honda")`, so a fingerprint that has not resolved yet never hides it. The `gateway` button at index 3 in the same hunk belongs to area A. `VehicleLayoutMici` is built without `back_callback`, following upstream `099143ad9`; `NavWidget` pops itself on swipe-down.

### 11.4 sunnylink

- `settings_ui_src/pages/vehicle.yaml`: a section with `id: honda` (it compiles to `vehicle_settings.honda` in `settings_ui.json`) holding the two toggles, `HondaDynamicTuningEnabled` and `HondaElesysGasLawV2` ("Measured Gas Pedal Law (2013-15 Accord)", whose description says what it does and that it applies at the next drive), each with `needs_onroad_cycle: true` and offroad-only enablement.
- `settings_ui_src/pages/cruise.yaml`: a section `honda_dynamic_learning`, visible when the capability `brand == honda`. It shows `HondaDynBrakeGain` and the three `HondaDynModeSec*` keys as `widget: info` rows. They carry no `step`: the `step: 0.001` display hint that `d11d2c9a8` added is not an info-widget field, and on its own, without `min`/`max`, it failed upstream's `test_settings_schema` `test_numeric_constraints`, so it was removed in the 2026-09 merge.
- Two lessons, both now pinned by tests. First, `blocked: true` means DEVICE_ONLY and the dashboard hides the row, so it must not be used to mean read-only (`d033e3dbd`). Second, info rows did not render inside a brand's vehicle section, only on a page, so they live on the Cruise page (`4131c8778`). A key may appear in only one place.
- `settings_ui.json` is generated by `openpilot/sunnypilot/sunnylink/tools/compile_settings_ui.py`. Recompile it; never merge it by hand.

**One inconsistent text left:** sunnylink's `cruise.yaml` shows the brake value as the raw param (an offset, described as `0.00 until it has learned anything. Positive adds brake, negative trims it`), while the `hondadyn` log, the big UI and (since 2026-10) the mici page show it as a gain. The toggle's `details` no longer points at rows "below"; they are on the Cruise page.

### 11.5 `openpilot/sunnypilot/sunnylink/statsd.py`

Adds `HondaDynamicTuningEnabled`, `HondaDynBrakeGain`, the three `HondaDynModeSec*` totals and `HondaElesysGasLawV2` to the `sp_stats` device-params list, so they can be watched without pulling a route. The toggle is included because a gain of 1.000 could mean either converged or never switched on.

### 11.6 Test

`openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` parses the files rather than importing them, so it does not need raylib. Its tests:

- `test_learned_params_are_registered_as_floats`
- `test_toggles_are_registered_and_backed_up`
- `test_mode_time_keys_cover_every_slot`
- `test_gas_law_param_is_registered_on_by_default_and_backed_up`
- `test_retired_keys_are_gone_everywhere`
- `test_statsd_reports_only_registered_honda_keys`
- `test_both_panels_drive_the_same_params`
- `test_honda_panel_publishes_its_items`
- `test_mici_page_shares_the_panel_params`
- `test_mici_settings_registers_the_vehicle_page`
- `test_sunnylink_exposes_the_same_toggles`
- `test_sunnylink_exposes_the_gas_law_toggle`
- `test_sunnylink_learned_values_are_read_only_and_on_a_page`
- `test_sunnylink_keys_are_registered_and_unique`
- `test_panel_defaults_match_the_tuner`

`test_panel_defaults_match_the_tuner` also checks the panel's `MODE_SLOTS`, `GAS_LAW_PARAM` and `GAS_LAW_DEFAULT` against `elesys_gas.py`. None of them checks the display inconsistency in 11.4.

---

## 12. Tests

| test | repo | how to run | what it pins |
|---|---|---|---|
| `opendbc/car/honda/tests/test_elesys.py` | opendbc | `python -m unittest opendbc.car.honda.tests.test_elesys` (55 tests) | category membership and dispatch (`compute_gas_brake(accel, speed, CP)`); the upstream Nidec map untouched; the Elesys gas/brake golden table; the pump (20 cases); the v1 gas curve, and that v2's slope deliberately is not it; the units bit (`create_brake_command(..., is_metric=, elesys=)`); the gear dwell, and a gearbox frame without `GEAR` (the fuzz case); the stock AEB truth table and DBC signal names (not the `carstate.py` branch, 6.3) |
| `opendbc/sunnypilot/car/honda/test_elesys_gas.py` | opendbc | `python -m unittest opendbc.sunnypilot.car.honda.test_elesys_gas` (31 tests) | the gas law, 9.2 |
| `opendbc/safety/tests/test_honda.py` (`TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety`) and `common.py` | opendbc | `python -m unittest opendbc.safety.tests.test_honda`; builds `libsafety` on import (942 run, OK, after the merge) | section 8 |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | opendbc | **standalone script**: `python <file>` with opendbc on `PYTHONPATH` | the tuner on its own, sections 1-5 and 10-16: toggle off is a no-op, pitch, the retired pedal and aero learners stay retired, the per-mode data counter, brake, params, importing without openpilot, three rounds of review regressions, drive-mode gating, the `hondadyn` line |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | opendbc | **standalone script**, or unittest discovery through its `TestDynamicTuningIntegration` wrapper | the real `CarController`, frame by frame: [1] toggle off matches stock, [2] toggle on, [3] gas and brake never together, [4] the standstill hold is not scaled by the learned gain, [5] a disengage unwinds the brake gain, [6] the interceptor owns the gas at every speed (decodes `PCM_GAS`), [9] fuel and odometer, [16] the gas law v1/v2 through `CarController` with the brake command identical under both, [17] `update()` never raises on odd inputs and 0x1FA goes out every even frame. Sections 7, 8, 10-15 and 14b are area B |
| `openpilot/selfdrive/controls/tests/test_stopping_debounce.py` | sunnypilot | `python tools/test_runner.py <file>` (17 tests) | 10.1 |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | sunnypilot | runner (9 tests) | 10.2 |
| `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | sunnypilot | runner (1 test) | 10.5 |
| `openpilot/selfdrive/ui/tests/test_honda_dynamic_settings.py` | sunnypilot | runner (11 tests) | section 11 |

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
- **The lagd comment in `interface.py`.** Corrected after the merge: before lagd has blocks it publishes `steerActuatorDelay + 0.2` = 0.58 s, its `VERSION 1` discards older caches, and it learns only above 50 mph. It still names the learner's service `liveDelay` in one place (now `lateralDelay`); only a comment. Whether to keep upstream's lagd behaviour on this car is open (UPSTREAM-2026-09.md item 7).
- **`settings_ui.json`.** Recompiled with `compile_settings_ui.py`, never merged by hand. After the merge the eight `HondaDyn*` info rows in `cruise.yaml` also dropped `step: 0.001`, which upstream's `test_settings_schema` rejects without `min`/`max` (11.4).
- **The UI brand page, `cruise.py` and `statsd.py`** were only renamed upstream (plus a small upstream change in statsd, which now imports `openpilot.sunnypilot.system.statsd`). They came across unchanged.

### 14.4 Identifiers to preserve

| kind | identifier |
|---|---|
| platform | `CAR.HONDA_ACCORD_9G_AU`. The string `HONDA_ACCORD_9G_AU` is also a key in `desire_helper.NUDGE_FIRM`, `stopping_tune.STOPPING_SPEED` and `STOPPING_DECEL_RATE`, `car_list.json`, `substitute.toml` and the tests |
| flags | `HondaFlags.ELESYS = 1024`; `HondaSafetyFlags.ELESYS_SCM_STANDDOWN = 32`; `HONDA_PARAM_ELESYS_SCM_STANDDOWN = 32`; `honda_elesys_scm_standdown` |
| sets | `HONDA_ELESYS` |
| DBC names | `honda_accord_au_2015_can` (becomes `_generated`), `honda_accord_2015au_radar`, and the fragments in 4.1 |
| CAN IDs openpilot sends on this car | 0x0E4 on bus 0 (5 bytes), 0x1FA on bus 0, 0x30C on bus 0, 0x1A6 on **bus 2**, 0x200 on bus 0 (pedal), 0x500 on bus 0 (area B). Never 0x33D. The panda TX list also allows `{0x194, 0, 4}`, but this car's DBC has no 0x194 (it imports `_steering_control_e.dbc`, whose `STEERING_CONTROL` is 0x0E4) and openpilot does not send it |
| CAN IDs read | 0x188 `GEARBOX_AUTO`; 0x1A6 `SCM_BUTTONS` (including `FUEL_LEVEL`); 0x221 `ECON_STATUS`; 0x294 `SCM_FEEDBACK`; 0x33D `LKAS_HUD` on bus 0; 0x1FA and 0x30C from bus 2; 0x18F `STEER_STATUS` with `STEER_CONTROL_ACTIVE 32:1`; radar 0x400, 0x410-0x417 and 0x420-0x424 on bus 1 |
| signals | `CMBS_BRAKE`, `CMBS_DISABLED`, `AEB_REQ_3`, `CMBS_BUTTON`, `FUEL_LEVEL`, `FUEL_SENDER`, `ODOMETER_KM`, `ECON_ON`, `GEAR_SHIFTER`, `GEAR`, `SET_ME_1` (the units bit on this car) |
| functions | `compute_gb_honda_elesys`, `brake_pump_hysteresis_elesys`, `create_scm_buttons_no_cruise`, `update_gear_elesys`, `elesys_gas_multiplier`, `elesys_pedal_v1`, `elesys_pedal_v2`, `ElesysGasLaw`, `ModeCrossfade`, `mode_slot`, `read_gas_law_v2`, `HondaDynamicTuner` (`update_state`, `brake_gain`, `wind_scale`, `update_wind`, `observe_pedal`, `persist`, `log_state`, `debug_values`), `learned_value`, `gas_law_v2`, `gas_law_label`, `mode_minutes`, `mode_time_text`, `reset_learned_values`, `car_brand`, `VehicleLayoutMici`; the keyword arguments `should_stop(..., v_ego_stopping=)` and `create_brake_command(..., is_metric=, elesys=)` |
| constants | `ELESYS_PUMP_*`, `ELESYS_GAS_BP`, `ELESYS_GAS_V`, `ELESYS_FF_BP`, `ELESYS_FF_K`, `ELESYS_FF_GM`, `ELESYS_FF_G0`, `MODE_K`, `CROSSFADE_FRAMES`, `GAS_LAW_PARAM`, `SPORT_DWELL`, `FUEL_LEVEL_FULL`, `STEER_THRESHOLD[HONDA_ACCORD_9G_AU] = 600`, `NUDGE_FIRM`, `NUDGE_HOLD_FRAMES`, `STOPPING_SPEED`, `STOPPING_DECEL_RATE` (`float32(0.8)`), `STANDSTILL_SPEED`, `STOPPING_EXIT_DEBOUNCE`, `LINBUS_I_CARRY_MAX`, `LINBUS_I_HOLD_TAU`, and the tuner constants in 9.1 |
| CarParams values | `transmissionType = automatic`, `longitudinalActuatorDelay 0.6`, `stopAccel -0.8`, `steerActuatorDelay 0.38`, `steerAtStandstill True`, `minEnableSpeed 19 mph` (with the gas-interceptor exemption). No `vEgoStopping`: it is deprecated upstream, and its 0.8 m/s lives in `stopping_tune.py` |
| params | see 11.1 |
| capnp fields area C code reads | `CarStateSP.driverTorqueStale @2`; `CarControlSP.lateralControl @5` (rebuilt in `helpers.py`) |
| markers | `FORK(HONDA_ELESYS)`, `FORK(HONDA_ACCORD_9G_AU)`, `FORK(LKAS-GATEWAY)`, `FORK(GATEWAY-UPDATE)`, `FORK:`, and `HONDA_ACCORD_9G_AU` in `honda.h`. Incomplete; see Baseline |
| log tag | `hondadyn` |

### 14.5 After every merge

1. Run `git diff refs/upstream/master HEAD --stat` in both repos and compare the file list with section 1. Every file in the inventory must still differ from upstream, and in the way this document says. As a secondary check only, `git grep -n -e 'FORK(' -e 'FORK:' -e HONDA_ELESYS -e HONDA_ACCORD_9G_AU` finds the marked hunks; it will not find the unmarked ones listed in the Baseline.
2. Build, regenerate the DBCs, and load `honda_accord_au_2015_can_generated.dbc` through the parser.
3. Safety: run the two `TestHondaElesys*` classes, the whole of `test_honda.py`, and MISRA.
4. Run `test_elesys.py`, the two standalone tuner scripts (directly, and the integration script with the sunnypilot tree on `PYTHONPATH` so §15 runs), and, under `tools/test_runner.py`, `test_stopping_debounce.py`, `test_lane_change_nudge.py`, `test_car_control_sp_seam.py` and `test_honda_dynamic_settings.py`. Check the runner's counts (17, 9, 1, 15). Also run opendbc's `test_car_interfaces`, `test_platform_configs` and `test_docs` against the platform.
5. If upstream has changed `should_stop()`, `LongControl` or the planner, check that this car still gets 0.8 m/s and 0.8 m/s³ and every other car upstream's values (`TestStoppingTune`), and grep for new `should_stop(` callers.
6. Recompile `settings_ui.json` and run `compile_settings_ui.py --check`.
7. On the first drive, check that:
   - the car selects `HONDA_ACCORD_9G_AU` from the bundle with no FW query in the log (`Fixed fingerprint ... skipping the VIN/FW query`)
   - there is no ACC or CMBS fault on the first ignition after the update
   - the gear reads P while parked
   - the fuel gauge is sane
   - `shouldStop` latches at about 0.8 m/s approaching a red light, and the car holds at `stopAccel` without crawling
   - the car does not engage below 19 mph
   - `hondadyn` lines appear if the tuner is on, with `gaslaw=v2` (or `v1` if the setting is off) and `modesec` growing in the slot being driven
