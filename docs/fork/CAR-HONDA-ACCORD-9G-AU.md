# HONDA_ACCORD_9G_AU: the car port (fork area C)

This document lists everything the fork changes relative to upstream so that sunnypilot can drive a 2013-2015 Honda Accord (Australian market, V6L and Tech trims) fitted with an Elesys radar, a comma pedal (gas interceptor) and the EPS-LKAS gateway board. It is written for whoever next merges upstream into the fork. For each change it says what the change does, why it exists, which identifiers must survive, which test pins it, and how to put it back if upstream has rewritten the code around it.

The other two areas have their own documents in `docs/fork/`. Where a file is shared, this document describes only the car-specific hunks and points to the others:

- **Area A, gateway update:** updating the board's firmware from the comma over CAN. This covers the flasher, the hook, the app-slot image, the mici `gateway` page, the `EpsLkas*` params and the firmware identity fields.
- **Area B, LKAS gateway protocol:** openpilot steering the EPS through the board. This covers the 0x0E4 serial-domain path, 0x500 SP_HUD_STATUS, decoding 0x700-0x70F, driver-torque substitution, the integrator hold and the MADS hand-back.

History and specs already exist in these files. This document does not repeat them:

| document | what it is |
|---|---|
| `CHANGELOG-elesys.md` | the longitudinal work on this car, sections 1-19, with measurements |
| `FEATURES-elesys.md` | plain-English feature notes for the owner. **Its PCM-blend material is stale throughout.** The toggle was removed in sunnypilot `cb4e0c34b` / opendbc `aa73e60a`, but the file still has a `The two toggles` section, its subsection 2 (`...also blend the PCM gas above 30 km/h`), a `Gas - the PCM gas factor` line under `Seeing what it has learned`, and `Leave the PCM blend off` in `The honest summary` |
| `docs/CHANGELOG_SERIAL_STEERING.md` | the serial-steering changelog, including `Things about this car worth not rediscovering` |
| `docs/SP_GATEWAY_FIRMWARE.md`, `docs/SP_HUD_STATUS.md` | area B specs |
| `S:/OP/*.md` (outside both repos) | the owner's raw analyses: `redlight_overshoot_findings.md` (brake/gas scale, creep, `vEgoStopping`), `AEB_passthrough_signal.md` and `CMBS_AEB_bitmap.md` (AEB bits), `TSA_*.md` (why the stock ACC is stood down), `radar_firmware_bit_spec.md` and `HOW_OP_USES_RADAR.md` (radar bus). Some describe older code. Where they disagree with the repo, the repo is right |

## Baseline

| | sunnypilot fork | opendbc fork |
|---|---|---|
| repo / branch | `SoRadGaming/sunnypilot` `master` | `SoRadGaming/opendbc` `sp-master` |
| fork point (merge-base) | `31dc4d8e5` (2026-06-28) | `b9712d20` (2026-06-08) |
| HEAD documented here | `10e088a2d` (2026-09-27) | `cf583b37` (2026-09-22), the commit sp-live pins |
| upstream compared against | `refs/upstream/master` = `a5f44653d`, 549 commits ahead | `refs/upstream/master` = `f95f996f`, 173 commits ahead |

To see any change described here:

```
git -C S:/OP/sp-live diff 31dc4d8e5 HEAD -- <path>
git -C S:/OP/sp-live/opendbc_repo diff b9712d20 HEAD -- <path>
```

**Markers are not complete.** Much of the opendbc car and safety code is tagged `FORK(HONDA_ELESYS)`, `FORK(HONDA_ACCORD_9G_AU)` or plain `FORK:`, the safety code uses `HONDA_ACCORD_9G_AU` comments, and most opendbc branches test `in HONDA_ELESYS`. On the sunnypilot side only `desire_helper.py` (`FORK(HONDA_ACCORD_9G_AU)`) and `longcontrol.py` (`FORK:` x3) carry a `FORK` marker. `latcontrol.py` and `card.py` only mention `HONDA_ELESYS` / `HONDA_ACCORD_9G_AU` in prose comments. The fork hunks in the files below contain no marker and no `HONDA_ELESYS` or `HONDA_ACCORD_9G_AU` text at all, so a marker grep after a merge will not find them. The list covers area C and also the area-B hunks this document describes in 10.3-10.6:

- sunnypilot: `selfdrive/car/helpers.py`, `common/params_keys.h`, `selfdrive/controls/controlsd.py`, `selfdrive/selfdrived/selfdrived.py`, `selfdrive/modeld/modeld.py`, `sunnypilot/modeld_v2/modeld.py`, `selfdrive/controls/lib/latcontrol_torque.py`, `sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py`, `sunnypilot/selfdrive/controls/controlsd_ext.py`, `sunnypilot/sunnylink/statsd.py`, `selfdrive/ui/sunnypilot/layouts/settings/cruise.py`, the `honda.py` brand page, mici `settings.py`, both sunnylink yaml pages and `compile_settings_ui.py`
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
| `opendbc/car/honda/interface.py` | M | transmission detection, longitudinal tuning, `steerActuatorDelay`, `steerAtStandstill`, safety param, `minEnableSpeed` | the two lateral values exist because of the gateway (B) |
| `opendbc/car/honda/radar_interface.py` | M | Elesys radar parsing | - |
| `opendbc/car/honda/carstate.py` | M | gear decode (`update_gear_elesys()`), `LKAS_PROBLEM` bus, `stockAeb`, `scm_buttons`, `econ_on` | B/A: `get_can_parsers()` registration of `GW_*`/`EPS_LIN_RAW`; `CarStateExt.update(..., ret_sp, ...)` |
| `opendbc/car/honda/carcontroller.py` | M | `compute_gb_honda_elesys()`, `brake_pump_hysteresis_elesys()`, dynamic-tuner hooks, 32-count brake release limit, SCM_BUTTONS re-send, no `LKAS_HUD` | B: `brake_release_scale()`, LDW bits, the `create_sp_hud_status()` block |
| `opendbc/car/honda/hondacan.py` | M | `create_brake_command()` units bit, `create_scm_buttons_no_cruise()` | B: `create_steering_control()` LDW, `create_sp_hud_status()` |
| `opendbc/car/car_helpers.py` | M | `skip_fw_query` | - |
| `opendbc/car/structs.py` | M | none of its own; described in 6.5 because of the capnp rule | B (`LateralControl`, `LinbusGateway` 0x704/0x70B fields, `driverTorqueStale`), A (`fw*` fields) |
| `opendbc/car/tests/routes.py` | M | test route | - |
| `opendbc/car/torque_data/substitute.toml` | M | torque data substitute | - |
| `opendbc/sunnypilot/car/car_list.json` | M | car list entry | - |
| `opendbc/dbc/generator/honda/*.dbc`, `opendbc/dbc/honda_accord_2015au_radar.dbc` | A/M | all of them, except the two in the next column | `_sunnypilot_linbus_gw.dbc` (B, A); byte 2 of 0x0E4 in `_steering_control_e.dbc` (B) |
| `opendbc/safety/modes/honda.h` | M | the stand-down safety mode | 0x500 on its TX list is B's frame |
| `opendbc/safety/tests/common.py`, `opendbc/safety/tests/test_honda.py` | M | safety tests | - |
| `opendbc/sunnypilot/car/honda/carstate_ext.py` | M | `fuelGauge` | B (gateway decode, driver torque), A (`_update_linbus_firmware`) |
| `opendbc/sunnypilot/car/honda/dynamic_tuning.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/gas_interceptor.py` | M | all | - |
| `opendbc/car/honda/tests/test_elesys.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | A | all | - |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | A | sections 1-6 and 9 | B: sections 7, 8, 10-15 (including 14b) |

### 1.2 sunnypilot fork (`sp-live`)

| file | status | area C content | other areas |
|---|---|---|---|
| `.gitmodules`, `opendbc_repo` (gitlink) | M | points the submodule at the opendbc fork (Other, 13.1) | - |
| `common/params_keys.h` | M | 9 `HondaDyn*` keys | A: `EpsLkas*` keys |
| `cereal/custom.capnp` | M | none; area C code reads `CarStateSP.driverTorqueStale` | B, A |
| `selfdrive/car/card.py` | M | `skip_fw_query=bool(fixed_fingerprint)` | A: firmware identity staging/writing, flash trace |
| `selfdrive/car/helpers.py` | M | none. The `lateralControl` rebuild in `convert_carControlSP()` is area B; it is described in 10.5 because its failure took down the car's radar path | B |
| `selfdrive/controls/lib/longcontrol.py` | M | stopping-exit debounce | - |
| `selfdrive/controls/lib/latcontrol.py`, `selfdrive/controls/lib/latcontrol_torque.py`, `sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py` | M | documented in 10.3; the reason for them is the gateway | B |
| `selfdrive/controls/controlsd.py`, `sunnypilot/selfdrive/controls/controlsd_ext.py` | M | documented in 10.4 | B |
| `selfdrive/selfdrived/selfdrived.py` | M | none; its one hunk is for MADS | B |
| `selfdrive/controls/lib/desire_helper.py`, `selfdrive/modeld/modeld.py`, `sunnypilot/modeld_v2/modeld.py` | M | `NUDGE_FIRM` | B: `driver_torque_stale` |
| `sunnypilot/mads/mads.py` | M | none (10.7 notes one rule that applies to every car) | B |
| `selfdrive/ui/sunnypilot/layouts/settings/cruise.py` | M | Honda tuner toggle | - |
| `selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py` | M | Honda brand page | - |
| `selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` | A | mici vehicle page | - |
| `selfdrive/ui/sunnypilot/mici/layouts/settings.py` | M | vehicle button | A: gateway button |
| `sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml`, `.../vehicle.yaml`, `sunnypilot/sunnylink/settings_ui.json` | M | sunnylink rows | - |
| `sunnypilot/sunnylink/statsd.py` | M | tuner telemetry | - |
| `sunnypilot/sunnylink/tools/compile_settings_ui.py` | M | UTF-8 fix (Other, 13.2) | - |
| `selfdrive/controls/tests/test_stopping_debounce.py`, `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`, `selfdrive/ui/tests/test_honda_dynamic_settings.py`, `selfdrive/car/tests/test_car_control_sp_seam.py` | A | see section 12 | - |
| `CHANGELOG-elesys.md`, `FEATURES-elesys.md`, `docs/CHANGELOG_SERIAL_STEERING.md` | A | history, listed at the top | - |

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

`FW_QUERY_CONFIG.non_essential_ecus` adds `CAR.HONDA_ACCORD_9G_AU` to both the `Ecu.eps` and the `Ecu.vsa` list, so a missing EPS or VSA response does not block a match. Upstream opendbc has since added a `fw_version_regex` to `FW_QUERY_CONFIG`. Both versions above match it (checked by eye, not by running it).

Once the car is selected in Vehicle settings, the FW query never runs (2.3). On the road this table is then used only when the car is auto-fingerprinted with no bundle set.

### 2.3 How the car is identified on the road: fixed platform, no FW query

**Files:** opendbc `opendbc/car/car_helpers.py`; sunnypilot `selfdrive/car/card.py` (one line, in the `get_car()` call).

**What it does.** `fingerprint()` and `get_car()` take a new keyword argument `skip_fw_query: bool = False`, ORed with the existing `SKIP_FW_QUERY` environment variable. When it is set together with a fixed fingerprint, `fingerprint()` logs `Fixed fingerprint %s: skipping the VIN/FW query, no OBD multiplexing`. `card.py` passes `skip_fw_query=bool(fixed_fingerprint)`, where `fixed_fingerprint` is the `platform` field of the `CarPlatformBundle` param, which is the car the user picked in Settings > Vehicle.

With the flag set, `fingerprint()` takes its existing skip branch: `vin = VIN_UNKNOWN`, `car_fw = []`, no FW candidates. The `Using cached CarParams` path is inside `if not skip_fw_query`, so it is skipped as well. The only thing that still identifies the car is the fixed platform.

**Why.** The VIN/FW query uses OBD multiplexing, which reroutes panda bus 1 to the OBD port. On this car bus 1 is the Elesys radar's bus. The radar loses the car for about 1.5 s and latches ACC and CMBS faults until the next ignition. The fault appeared only on the first ignition after an update: `CarParamsCache` is cleared on manager start, and the cached path skips the query. Evidence: route `15646e8515eda1a7/000000b5` logged `Getting VIN & FW versions` and multiplexing toggling four times in 1.5 s, while the routes either side logged `Using cached CarParams` (opendbc `05008a22`, sunnypilot `d11d2c9a8`, `CHANGELOG-elesys.md` section 17).

**Scope: this changes other cars too, and not only by skipping a query.** The code comment says the query's answer is discarded anyway with a fixed platform. That is true of the fingerprint candidate only. For **every** sunnypilot car with a `CarPlatformBundle` set, on **every** ignition (cached or not):

- `CarParams.carFw` is empty and `carVin` is `VIN_UNKNOWN`.
- Anything that `_get_params()` or `_get_params_sp()` derives from `car_fw` stops working. On Honda that includes the `Disable control if EPS mod detected` loop and sunnypilot's `HondaFlagsSP.EPS_MODIFIED` detection (both look for an `eps` FW version containing a comma), so an EPS-modified Honda with a bundle silently loses its modified torque tables. I did not survey other brands.

Cars without a bundle are unchanged. On this car nothing FW-derived is used, so the only effect here is the empty `carFw`/VIN in the logs. The car has to be selected in Vehicle settings once; with auto fingerprinting alone the query still runs and can still fault this car.

**Recommendation, not done:** if the fork is ever shared, narrow the condition to this platform. Either `skip_fw_query=fixed_fingerprint == "HONDA_ACCORD_9G_AU"` in `card.py`, or an opt-in set of platforms in opendbc that `card.py` consults.

**Re-apply.** Keep the keyword argument on both functions and the OR with the env var. Upstream has since changed the cached-params condition in `fingerprint()` to `carVin != VIN_UNKNOWN or os.environ.get("REPLAY")`. The fork's lines sit just above and below that line and should merge unchanged.

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
| `HONDA_ELESYS` | `CAR.with_flags(HondaFlags.ELESYS)` | `values.py`, next to the other `HONDA_*` sets |
| `HondaSafetyFlags.ELESYS_SCM_STANDDOWN` | `32` | `values.py`; mirrored as `HONDA_PARAM_ELESYS_SCM_STANDDOWN = 32` in `honda.h` |

`HONDA_ACCORD_9G_AU` is the only member. Almost every fork branch tests membership of the category (`in HONDA_ELESYS`) rather than the car, so a second Elesys platform would inherit all of the category behaviour. These places name the car directly instead, and a second platform would need its own entry in each:

- `FW_VERSIONS` (`fingerprints.py`) and both `FW_QUERY_CONFIG.non_essential_ecus` lists (`values.py`)
- the platform's DBC names in the `CAR` entry
- `STEER_THRESHOLD` (`values.py`)
- `minEnableSpeed` (`interface.py`)
- `NUDGE_FIRM` (`desire_helper.py`, keyed by the string)
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
| gas interceptor curve | `interp(v, [0, 10], [0.4, 1.0])` | `elesys_gas_multiplier()` | 9.2 |
| fuel | not decoded | `fuelGauge` from `FUEL_LEVEL` | 6.4 |
| `steerActuatorDelay` | 0.15 (default branch) | 0.38 | 5.1 |
| `steerAtStandstill` | False | True | 5.1 |
| `minEnableSpeed` | 25.51 mph | 19 mph (overridden by upstream now, 14.2) | 5.1 |
| stop threshold | `CP.vEgoStopping` default 0.5 | 0.8 (gone upstream, 14.2) | 5.1 |

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
| `vEgoStopping = 0.8` | same block | the car crawls at 0.55-0.7 m/s approaching a stop, so under the default 0.5 `shouldStop` never latched: the stopping state was active in 365 of 94,519 engaged frames and the stopAccel ramp never ran (`S:/OP/redlight_overshoot_findings.md`; opendbc `470cd311`) | **must be deleted when merging upstream: upstream moved `vEgoStopping` into `CarParams.deprecated`, and assigning it raises `AttributeError`. The behaviour it gave has to be re-established elsewhere (14.2)** |
| `stopAccel = -0.8` | same block | the default -2.0, on top of the creep offset, commanded cb 253 of 255 at a stop and held it for 7.8 min of a 65 min drive. The car does not need that much: 32 frames of motion in 46,815 hold frames (routes `15646e8515eda1a7` 1f and 20). -0.8 puts the hold at about cb 189-192, with about 0.8 m/s^2 of margin, roughly an 8% grade. The code comment says: if a stop ever creeps, raise this back toward -1.2 before touching the creep table | upstream still reads `CP.stopAccel` in `longcontrol.py` |
| `steerActuatorDelay = 0.38` | `if candidate in HONDA_ELESYS:` after the lateral tuning chain | the command goes to the board and out on 9600-baud serial. lagd measured 0.383 s (route `000000d3`) and 0.377 s (`000000d4`). The value matters only before lagd has converged, and when `LagdToggle` is off (opendbc `1da246ae`) | the reason is area B |
| `steerAtStandstill = True` | same block | keeps `latActive`, and with it `STEER_TORQUE_REQUEST`, alive at a stop so the board keeps the cluster's lane graphic up. The board holds its target at 0 below 5 km/h (`GW_STANDSTILL_CPH`) (opendbc `bb0fe222`). Upstream `controlsd.py` still reads `CP.steerAtStandstill` in the same `latActive` expression | the reason is area B |
| stand-down safety param | see 3.1 | see 8 | - |
| `minEnableSpeed = 19 mph` | `elif candidate in (CAR.HONDA_ODYSSEY_TWN, CAR.HONDA_ACCORD_9G_AU):` | from the original port (opendbc `04a48a0a`); no measurement recorded | **upstream now sets `stock_cp.minEnableSpeed = -1.` for every gas-interceptor car in `_get_params_sp()`, which overrides this value on this car** (14.2) |

The car takes the default lateral branch (2.4). That branch first sets `steerActuatorDelay` to 0.15, and the Elesys block then overwrites it.

### 5.2 `radar_interface.py`

- `_create_nidec_can_parser()`: for `HONDA_ELESYS` it parses `[0x400] + list(range(0x410, 0x418)) + list(range(0x420, 0x425))` at 10 Hz on bus 1, instead of 0x400, 0x430-0x439 and 0x440-0x445 at 20 Hz.
- `self.radar_type = 'Elesys' if CP.carFingerprint in HONDA_ELESYS else 'Nidec'`.
- `self.trigger_msg = 0x423` for Elesys, 0x445 otherwise. 0x423 is not the last track (0x424 is), and the reason is not recorded.
- Fault: `self.radar_fault = cpt['RADAR_STATE'] not in (104, 111, 125)` for Elesys, `!= 0x79` otherwise. `radar_wrong_config` stays `RADAR_STATE == 0x69` for both. The commits do not record where 104/111/125 came from (opendbc `04a48a0a`, `304d1d82` `Fixed Radar Range`).

Track decoding (`LONG_DIST < 255`, `dRel`, `yRel = -LAT_DIST`, `vRel`) is the shared upstream path. Upstream has since removed `self.track_id` and the `aRel`/`yvRel`/`measured` assignments in the same function. The fork's hunks do not touch those lines.

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

The state is set in `__init__`: `self.gear_shifter_last = GearShifter.unknown`, `self.gear_zero_frames = 0` and `self.SPORT_DWELL = 100`, which is 1.0 s at 100 Hz, about twice the longest transient ever observed. `update()` calls the function from an `elif self.CP.carFingerprint in HONDA_ELESYS:` placed between upstream's `manual` branch and the generic branch.

Why: without the dwell every shift would emit a phantom `GearShifter.sport`. That trips `wrongGear`, suppresses always-on DM and freezes the dynamic tuner. The logic is a separate function so it can be tested without a CAN stream. Tests: `TestElesysGearDecode` in `test_elesys.py`. It builds a real `CarState` through `CarInterface.get_params()`/`get_params_sp()` (helper `_cs()`) and covers the detents, a transient holding the last gear, a sustained zero becoming Sport, the GEAR 26 fast path, leaving Sport re-arming the dwell, and the dwell staying clear of 520 ms. It calls the function directly, so the `elif` dispatch in `update()` is not exercised.

### 6.2 `LKAS_PROBLEM` from bus 0

```python
if self.CP.carFingerprint in HONDA_ELESYS:
  ret.carFaultedNonCritical = bool(cp_cam.vl["ACC_HUD"]["ACC_PROBLEM"] or cp.vl["LKAS_HUD"]["LKAS_PROBLEM"])
```

On this car 0x33D arrives on bus 0 (3.2). With the Stage 10 image it is the board's frame, which re-sources `LKAS_PROBLEM` from the EPS error state (LKAS-GATEWAY-PROTOCOL.md §10.1). This is also why openpilot must never send 0x33D here: it would read back its own frame, and `carFaultedNonCritical` would stay false (opendbc `031743c4`). Upstream has rewritten the surrounding condition as `if not (self.CP.flags & HondaFlags.BOSCH):`. No test covers this branch.

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
- `self.econ_on`: `None` by default, meaning not observable. On Elesys it is `bool(cp.vl["ECON_STATUS"]["ECON_ON"])`. It is not a `CarState` field; `HondaDynamicTuner._econ_state()` reads it straight off the `CarState` object.
- `ret.fuelGauge = min(cp.vl["SCM_BUTTONS"]["FUEL_LEVEL"] / FUEL_LEVEL_FULL, 1.0)` with `FUEL_LEVEL_FULL = 105.0`, set in `CarStateExt.update()` (`carstate_ext.py`). It is a fraction of the gauge, not of the tank. Test: integration section 9.
- `CarStateExt.update()` now takes `(ret, ret_sp, can_parsers)`, and on Elesys `get_can_parsers()` registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION` and `GW_BUILD` with `float("nan")`. Both changes belong to areas B and A, but the call site in `carstate.py` must keep passing `ret_sp`.

### 6.5 `opendbc/car/structs.py`

Area C adds no fields of its own here. The file is listed because its relationship with `cereal/custom.capnp` is a merge hazard. The additions:

- `CarControlSP.lateralControl: CarControlSP.LateralControl` with `integrator: float`, `saturated: bool`, `integratorFrozen: bool` (area B).
- `CarStateSP.driverTorqueStale: bool` (area B; read by area C's `desire_helper.py`, 10.2).
- `CarStateSP.linbusGateway: CarStateSP.LinbusGateway`: `engaged`, `dryRun`, `valid`, `actuating`, `present`, the `GW_STEER_GRANT` fields `grantValid` through `latchedUntilKeyOff` (area B), and `fwValid` through `fwBuildValid` (area A).

**The rule:** card publishes these dataclasses through `convert_to_capnp()`, which passes them into `custom.CarStateSP.new_message(**dict)` by keyword. So:

- **Field names must match** `cereal/custom.capnp` exactly.
- **capnp ordinals must be unique and must never change:** `CarControlSP.lateralControl @5`, `CarStateSP.linbusGateway @1`, `CarStateSP.driverTorqueStale @2`, `LinbusGateway @0`-`@26`. Upstream's `CarControlSP` currently ends at `@4` and `CarStateSP` at `@0`, so there is no collision today. If upstream adds fields to either struct, the fork's fields keep their numbers and upstream's new ones must be renumbered on the fork side (or the fork's moved, which breaks old logs).
- **Dataclass field order does not matter.** It already differs: `structs.py` declares `driverTorqueStale` before `linbusGateway`, while the capnp has them the other way round. The in-code comments in `structs.py` say names and order must match; the order part is overstated.

On the way in, `selfdrive/car/helpers.py` must rebuild every nested struct by hand (10.5).

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

`compute_gas_brake()` dispatches to it through `elif fingerprint in HONDA_ELESYS:`.

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

A new argument, `is_metric`, goes in just before `CP_SP`:

```python
imperial_unit = int(not is_metric) if car_fingerprint in HONDA_ELESYS else 1
values = { ..., "SET_ME_1": imperial_unit, ... }
```

On this car the bit is the cluster's units flag (0 metric, 1 imperial), which is the same meaning as `ACC_HUD.IMPERIAL_UNIT`. The stock radar sends 0 on the metric AU car. On every other Nidec it stays the constant 1, and the signal keeps the name `SET_ME_1` in the shared DBC on purpose. `carcontroller.py` passes `CS.is_metric`. Tests: `TestBrakeCommandUnitsBit`.

Upstream has since **removed the `car_fingerprint` parameter** from `create_brake_command()`; see 14.2.

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

All of these do nothing while `HondaDynamicTuningEnabled` is off. Integration section 1 pins bit-identical output with the toggle off.

| hook | code | effect with the tuner on |
|---|---|---|
| construct | `self.dynamic_tuner = HondaDynamicTuner(CP, CP_SP)` | reads the params once, when the controller is built, which is at ignition |
| pitch feedforward | `hill_accel = self.dynamic_tuner.update_state(CC, CS)`; `adjust_accel = accel + hill_accel` feeds `compute_gas_brake()` and, without an interceptor, `pcm_accel` | compensates for road grade |
| aero | `wind_brake * self.dynamic_tuner.wind_scale()` on the brake side, also passed to the interceptor; `self.dynamic_tuner.update_wind(CC, CS, float(wind_brake_ms2))` with `wind_brake_ms2 = interp(vEgo, [0, 13.4, 22.4, 31.3, 40.2], [0, 0.049, 0.136, 0.267, 0.441])` | learned aero scale |
| brake gain | `brake_gain = self.dynamic_tuner.brake_gain(CC, CS, float(apply_brake))` multiplies `apply_brake` before it is scaled to counts | learned brake scale |
| brake release limit | `if self.dynamic_tuner.enabled and CC.longActive and not CS.out.gasPressed and not CS.out.brakePressed: apply_brake = max(self.apply_brake_last - 32, apply_brake)` | the brake command can fall by at most 32 counts per 50 Hz frame, to match factory. This stops the lurch as the car lets go at a stop. It is bypassed on disengage and on driver override, and it runs before the pump logic so the Elesys anchor sees exactly what goes on the wire |
| pedal | `GasInterceptorCarController.update(..., self.dynamic_tuner)` | see 9.2 |
| persist / log | `self.dynamic_tuner.persist(self.frame)` and `self.dynamic_tuner.log_state(self.frame)` at the end of `update()` | see 9.1 |

**Scope:** `HondaDynamicTuner._is_applicable()` accepts every Nidec car with openpilot longitudinal, not only this one. With the toggle on, another Nidec car would get the pitch term, the aero and brake gains and the 32-count release limit. The pedal learner also needs a gas interceptor.

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

**`common.py`** extends the `test_tx_hook_on_wrong_safety_mode` exceptions: `TestHondaElesys*` joins `TestHondaNidec*` for the VW MQB 0x30C overlap, and 0x1A6 joins the list of messages common to all Hondas.

**Gap:** both classes inherit `HONDA_N_COMMON_TX_MSGS`, which contains `[0x33D, 0]`. So `test_spam_can_buses` never asserts that 0x33D is blocked in the stand-down mode. The C lists are correct (0x33D is absent); only the test does not pin it. One fix is `TX_MSGS = [m for m in HONDA_N_COMMON_TX_MSGS if m[0] != 0x33D] + ...`.

### 8.4 Re-applying the safety changes

Upstream `honda.h` and `test_honda.py` are unchanged between the fork point and `f95f996f`, so today they should merge cleanly. If upstream rewrites them:

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
| pedal | a multiplicative correction per speed breakpoint on `elesys_gas_multiplier()`, on the grid `PEDAL_GAIN_BP = ELESYS_GAS_BP = [0, 3, 6, 10, 15, 20]` m/s | `pedal_gain_at(v)` in the interceptor path | `PEDAL_GAIN_MIN = 0.5`, `PEDAL_GAIN_MAX = 1.8`, `PEDAL_LEARN_RATE = 2e-4` |
| brake | a gain on the brake command, stored as an offset | `brake_gain()` in `CarController` | a PID with `BRAKE_KI = 0.5`, `BRAKE_POS_LIMIT = 0.6`, `BRAKE_NEG_LIMIT = 0.15` (so 0.85x to 1.60x). Frozen below `BRAKE_LEARN_MIN_SPEED = 1.0` m/s, and faded linearly to 1.0 below that speed |
| aero | a scale on `wind_brake` | `wind_scale()` | `WIND_FACTOR_MIN/MAX = 0.7/1.5`, `WIND_ERR_DEADBAND = 0.05`. Learns only while `abs(accel_target) < LEARN_MIN_CMD` |
| pitch | nothing learned. A feedforward of `sin(pitch) * g`, from `CC.orientationNED[1]` through `PITCH_RC = 0.5` s | added to the accel target | `PITCH_ACCEL_LIMIT = 1.5`. Faded out between 5 and 2 m/s, active only in `LongControlState.pid`, and decays out after `PITCH_STALE_FRAMES = 100` frames without a pose |

**Rules that must not be relaxed.** The module header lists four:

1. Nothing learns during transients.
2. Only settled, off-rail values are persisted. The persisted value is a long EMA (`CONVERGED_TAU = 3e-4`) that advances only while the live value is off its clamps.
3. Every loaded value is re-clamped.
4. Limits are chosen for products. The brake gain and the brake rise rate multiply, so the ceiling is chosen against their product.

**Admission.** Each learner accepts a sample only when these conditions hold:

- **Plant-lag model (pedal and aero).** These learners compare `aEgo` against a first-order model of the plant's lag (`PLANT_TAU = 0.30` s). They reset whenever the filtered ramp rate exceeds `LEARN_MAX_JERK = 0.5` m/s^3, and wait `SETTLE_FRAMES = 100`. This replaced a steady-target dwell that let in only 0.7-19.6 s per route and left the pedal gains at 1.000 after two weeks (opendbc `16b9ff5f`, `CHANGELOG-elesys.md` section 12).
- **Steady-target dwell (brake).** The brake channel keeps the old dwell (`STEADY_SETTLE_FRAMES = 150`, `STEADY_SETTLE_TOLERANCE = 0.20`). It is an integrator, and ramp samples rail it.
- **Minimum command.** Below `LEARN_MIN_CMD = 0.4` m/s^2 the gain cannot be identified.
- **Driver and state.** Learning is off while gas or brake is pressed, during `stockAeb`, and outside the PID state.
- **Drive mode.** Learning happens only in the reference mode: `LEARN_GEARS = ("drive",)`, `LEARN_ECON = (False,)`. An unknown gear still learns, so other Nidec cars are not locked out.

**Params** (`_PARAM_SPEC`, re-clamped on load):

- `HondaDynPedalGain0` to `HondaDynPedalGain5`: default 1.0, range 0.5-1.8.
- `HondaDynWindFactor`: default 1.0, range 0.7-1.5.
- `HondaDynBrakeGain`: default **0.0**, range -0.15 to 0.6. It is an offset, so turning the toggle on changes nothing until something has been learned.

The enable key is `HondaDynamicTuningEnabled`. The values are read once in `__init__`. A background `_ParamWriter` thread writes them every `PERSIST_INTERVAL = 6000` frames (60 s), so the control loop never waits on disk. `Params` is imported lazily, so opendbc still imports without openpilot.

**Telemetry.** Every `LOG_INTERVAL = 500` frames (5 s) it writes one `carlog.info` line tagged `hondadyn`, with these fields: `pedal=`, `pedalc=`, `brake=` and `brakec=` (both shown as gains, `1.0 + offset`, since opendbc `ff9f3211`), `wind=`, `pitch=`, `settle=`, `settles=`, `eng=`, `aref=`, `aerr=`, `stale=`, `werr=`, `gear=`, `econ=`, `modeok=`. card forwards `carlog` to cloudlog, so the lines come back in a route's `logMessage`. The owner parses them with `S:/OP/sunny_logs/parse_hondadyn.py`, which is outside the repo.

**Known issues recorded in the code:**

- The pitch fade band (2-5 m/s) lies inside the PID state. On a stop approach the grade term is handed back to openpilot's integrator faster than the integrator can follow: modelled shortfall 0.115 m/s^2 on a 4% downhill, 0.249 on a 10% one. This was left alone on purpose until there is road data.
- Two comments contradict each other about ECON. An older paragraph says ECON is not gated because it is not observable yet; a later one says it is mapped and live. The later one is correct, and `LEARN_ECON = (False,)` is in force.
- Two comments quote `vEgoStopping = 0.8` as the speed where stopping begins: the pitch-fade `KNOWN ISSUE` paragraph and the brake-fade comment above `brake_gain()`'s return. Both go stale on upstream, which no longer has a per-car stop threshold (14.2).

**Removed history**, noted here so nobody restores it by accident: the PCM blend was deleted in opendbc `aa73e60a` / sunnypilot `cb4e0c34b`. That covered `HondaDynamicPcmBlendEnabled` and the learned `HondaDynGasFactor`, `HondaDynGasAlpha`, `HondaDynAverageFactor`, `HondaDynSpeedFactor` and `HondaDynSpeedAlpha`.

Tests: `test_dynamic_tuning.py` (sections 1-5 and 9-16; sections 6-8 were removed with the PCM blend) and sections 1-6 of `test_dynamic_tuning_integration.py` (see 12).

### 9.2 `opendbc/sunnypilot/car/honda/gas_interceptor.py`

**The curve.** New `ELESYS_GAS_BP = [0., 3., 6., 10., 15., 20.]`, `ELESYS_GAS_V = [0.55, 0.85, 1.20, 1.55, 1.95, 2.75]` and `elesys_gas_multiplier(v_ego)`. On `HONDA_ELESYS` these replace upstream's `interp(vEgo, [0, 10], [0.4, 1.0])`.

Why:

- On this car the pedal-to-accel gain falls with speed.
- July drives delivered only 50-65% of the commanded accel at 3-14 m/s.
- August plant identification, over about 200k settled frames, said 1.30-1.75x more command was needed, while the interceptor command never went above 0.9.
- The top three breakpoints were raised by about 1.25x: one measured step, not the full ratio.

Sources: route `ac35d9891f`; opendbc `2905e73d`, `2bc5c4db`; `CHANGELOG-elesys.md` section 7. The code comment says what to expect in the next logs and when to take a second step.

**The tuner hook.** `GasInterceptorCarController.update()` gains a `tuner=None` argument. When a tuner is passed, the gas multiplier is multiplied by `tuner.pedal_gain_at(v)`, and afterwards `tuner.update_pedal(CC, CS, self.gas)` is called.

**The shared grid.** `ELESYS_GAS_BP` is the single source for the tuner's grid. The UI hard-codes the same breakpoints, and a test checks they agree (section 11).

Tests: `TestElesysGasMultiplier` checks the golden curve, that it is monotonic, and that the mid band sits above the old curve.

---

## 10. Control-loop changes (sunnypilot)

### 10.1 Stopping-exit debounce (`selfdrive/controls/lib/longcontrol.py`)

New constants: `STANDSTILL_SPEED = 0.15` m/s and `STOPPING_EXIT_DEBOUNCE = 40` frames (0.4 s).

`LongControl.__init__` reads `Params().get_bool("HondaDynamicTuningEnabled")` once. If it is true, it sets `self._stopping_debounce = STOPPING_EXIT_DEBOUNCE`.

In `update()`, the code remembers `prev_state`. After `long_control_state_trans()` runs, it checks whether the state is leaving `stopping` for `pid` or `starting` while `vEgo < STANDSTILL_SPEED` and gas is not pressed. If so, it holds `stopping` until that transition has been requested for 40 consecutive frames.

A transition to `off` (a disengage) is never delayed, and neither is a gas press. The debounce is a post-step so that `long_control_state_trans()` keeps its signature.

Why: on route `2418f2eb2b` (t about 376 s), `shouldStop` blipped false for 0.5 s. The brake ramped from 0.99 to 0.48 and the car rolled forward at 0.3 m/s for about a second, then re-clamped over about 2 s because `stoppingDecelRate` was 0.8 m/s^3. In that review, 17 of 22 holds showed micro-motion. Held pressure was measured not to decay, so this is not a pump problem (`CHANGELOG-elesys.md` section 9).

**Scope:** this is core openpilot code and is not gated on the car. It shares the tuner's toggle so a road test has only one switch. The code comment calls that a naming wart and says to give the debounce its own param if it is ever A/B tested on its own. Any car with that param set gets the debounce.

Test: `selfdrive/controls/tests/test_stopping_debounce.py`, a standalone script that stubs `cereal` and `openpilot` (see section 12 for the collection hazard). Its sections cover:

- toggle off leaves the state machine unchanged
- a blip is rejected
- a real launch is delayed by exactly 40 frames
- a gas press releases immediately
- no debounce while the car is still rolling
- the brake stays applied through the hold
- flapping cannot build up credit
- a disengage is not debounced
- a car with `startingState` is debounced into `starting`

### 10.2 Lane-change nudge (`selfdrive/controls/lib/desire_helper.py`, both `modeld.py`)

This car has two rules.

**1. `STEER_THRESHOLD` of 600** (`values.py`, `CAR.HONDA_ACCORD_9G_AU: 600`). `steeringPressed` is `abs(steeringTorque) > STEER_THRESHOLD`, where the default threshold is 1200.

- Evidence, from 21 `preLaneChange` windows on route `000000d9`: the 11 that armed a lane change peaked at 1556-5839 counts. Of the 10 that did not, six peaked between 600 and 1225, one of them at 937 after nine seconds of trying at 60 km/h.
- 600 catches those weak nudges without changing any nudge that already worked.
- Why this car needs it: below the EPS's 50 km/h floor the wheel is unassisted, so a nudge easily passes 1200. With 160 counts of gateway assist in the wheel, the same nudge lands around 900.
- 600 is the value the 11G Accord already uses (opendbc `b7d15a98`, sunnypilot `715ea5df6`).

**2. `NUDGE_FIRM`: a nudge must be firm, or held.** With the threshold at 600, a single 10 ms reading of 645 counts confirmed a lane change (route `fc`, t = 768.8), and the driver reported that touching the wheel with the blinker on started a lane change.

- New module constants: `NUDGE_FIRM = {"HONDA_ACCORD_9G_AU": 1500.}` and `NUDGE_HOLD_FRAMES = 4`.
- `DesireHelper.__init__(self, car_fingerprint: str = "")` sets `self.nudge_firm = NUDGE_FIRM.get(car_fingerprint)`.
- In `preLaneChange`, `self.nudge_frames` counts consecutive frames of torque in the wanted direction. It is reset on entry to `preLaneChange`.
- When `nudge_firm` is set, the torque confirms only if `abs(steeringTorque) >= nudge_firm`, or if it has been present for 4 model frames (150 ms).

**Replay figures: the two sources disagree.** Both agree that no nudge confirms earlier than before and that three no longer confirm at all (peaks of 693, 740 and 903, held for one or two frames). They differ on the rest:

| source | windows | same instant | later | not at all |
|---|---|---|---|---|
| `FORK` comment above `NUDGE_FIRM` in `desire_helper.py` | 115 blinker windows on routes d9..fd, 66 lane changes the old rule confirmed | 29 | 29 by 50-150 ms, 4 later still | 3 |
| commit message of sunnypilot `10e088a2d` (and `docs/CHANGELOG_SERIAL_STEERING.md` 2026-09-27) | 92 recorded windows | 37 | 29 (mostly 50-150 ms, three at 0.4-0.9 s) | 3 |

The commit message says its figures came from driving the real `desire_helper` over the windows. I could not tell which set describes the code as committed. If the replay is re-run, fix the comment to match.

Cars not in the table keep upstream's single-frame rule. `steeringPressed` itself does not change, so DM and the lateral controller still see a light hand on the wheel.

Wiring: `selfdrive/modeld/modeld.py` and `sunnypilot/modeld_v2/modeld.py` construct `DesireHelper(CP.carFingerprint)`, subscribe to `carStateSP`, and pass `sm['carStateSP'].driverTorqueStale` as the new fourth argument of `DH.update()`. That argument is area B: a latched torque cannot confirm a nudge (`torque_applied = carstate.steeringPressed and not driver_torque_stale and ...`). Neither modeld hunk carries a marker.

Test: `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` has 8 pytest functions:

- other cars are unchanged
- a brush is rejected
- a held light push confirms on the 4th frame
- letting go restarts the count
- a firm tug confirms at once
- direction still matters
- a stale torque confirms nothing
- the count restarts on a new blinker

Integration section 15 covers the stale-torque case from the car side.

### 10.3 Lateral integrator hold (`latcontrol.py`, `latcontrol_torque.py`, `latcontrol_torque_v0.py`)

This is listed here because it lives in the control loop. The reason for it is the gateway (area B); the protocol side is in that document.

- `LatControl` gains `LINBUS_I_CARRY_MAX = 0.25` m/s^2 and `LINBUS_I_HOLD_TAU = 30.0` s; the attributes `linbus_gateway_present`, `linbus_gateway_actuating`, `_linbus_was_actuating` and `integrator_frozen`; and the methods `set_linbus_gateway(present, actuating)` and `_linbus_integrator_gate()`.
- Both torque controllers call `linbus_hold = self._linbus_integrator_gate()` at the top of `update()`. They call it on every frame, active or not, so the takeover edge is never missed. They add `or linbus_hold` to `freeze_integrator`.
- Behaviour: with no gateway (`present` is False, which is every other car) nothing changes. With a gateway, the integrator is frozen while the board is not actuating and decays with a 30 s time constant. On the frame the board starts actuating, the integrator is clipped to +-0.25 rather than zeroed.
- Why: the integrator wound up open-loop to +0.65 on route `000000b3`. Zeroing it at takeover then meant re-learning the car's steady right-hand trim (+0.04 to +0.20 on routes `000000d3`/`d4`), which took about 14 s at every takeover (sunnypilot `39b857567`, `946b5fa21`).
- `LatControlPID` and the angle controllers do not call the gate. This car uses the torque controller (2.4).

### 10.4 `controlsd.py` and `controlsd_ext.py`

- `controlsd.py` subscribes to `carStateSP`. Before `self.LaC.update()` it runs `gw = self.sm['carStateSP'].linbusGateway; self.LaC.set_linbus_gateway(bool(gw.present), bool(gw.actuating))`, and it now calls `self.run_ext(self.sm, self.pm, lac_log, self.LaC)`.
- In `controlsd_ext.py`, `state_control_ext(sm, lac_log=None, LaC=None)` and `run_ext(sm, pm, lac_log=None, LaC=None)` fill `CC_SP.lateralControl.integrator` and `saturated` from `lac_log` (only if it has an `i` field), and `integratorFrozen` from `LaC.integrator_frozen`. `create_sp_hud_status()` (area B) consumes them.

### 10.5 `selfdrive/car/helpers.py`

`convert_carControlSP()` now rebuilds `struct_dataclass.lateralControl = structs.CarControlSP.LateralControl(**remove_deprecated(struct_dict.get('lateralControl', {})))`. Every nested struct has to be rebuilt by hand, or it reaches the car controller as a plain dict.

Missing this line crashed card on every frame on routes b5-b8. With card down nothing re-sent `SCM_BUTTONS`, so the car threw ACC and CMBS faults (sunnypilot `d11d2c9a8`).

Test: `selfdrive/car/tests/test_car_control_sp_seam.py`. It also checks, from the capnp schema, that every nested struct of `CarControlSP` is rebuilt, so the next one added cannot repeat the crash. **Any new nested field in `CarControlSP` needs a matching line here.**

### 10.6 `selfdrive/selfdrived/selfdrived.py`

The only change adds `'carStateSP'` to the `SubMaster` list, for MADS (area B). card publishes `carStateSP` unconditionally at 100 Hz.

### 10.7 `sunnypilot/mads/mads.py` (area B, with one rule for every car)

Area B owns this file. One rule in it, though, is gated neither on the car nor on the gateway: a steering rate of at least `EMERGENCY_STEER_RATE = 200.0` deg/s for `EMERGENCY_STEER_FRAMES = 2` frames adds `lkasDisable` on **every** car running MADS on this fork (sunnypilot `35622a994`). It is mentioned here so that a car maintainer is not surprised by it.

---

## 11. UI, sunnylink and statsd

There is one setting, `HondaDynamicTuningEnabled`, which can be reached from four places, plus read-only views of the eight learned values. The toggle takes effect at the next ignition, because both the tuner and `LongControl` read it only once.

### 11.1 Params (`common/params_keys.h`)

| key | flags | type | default |
|---|---|---|---|
| `HondaDynamicTuningEnabled` | `PERSISTENT`, `BACKUP` | BOOL | `0` |
| `HondaDynPedalGain0` .. `HondaDynPedalGain5` | `PERSISTENT` | FLOAT | `1.0` |
| `HondaDynWindFactor` | `PERSISTENT` | FLOAT | `1.0` |
| `HondaDynBrakeGain` | `PERSISTENT` | FLOAT | `0.0` |

The learned values are not `BACKUP`. They change every 60 s and belong to one car, and a restored backup could bring back a tune learned on different hardware. The type must stay FLOAT: statsd depends on it to send numeric fields, and `_ParamWriter` counts write errors if a key is missing. The `EpsLkas*` keys in the same hunk belong to area A.

### 11.2 Big UI (comma 3/3X)

**`selfdrive/ui/sunnypilot/layouts/settings/cruise.py`**

- Adds `self.honda_dyn_toggle`, titled `Honda Nidec Dynamic Longitudinal Learning (Alpha)`, after the custom ACC items.
- The description strings are `HONDA_DYN_DESC`, `HONDA_DYN_VEHICLE_NOTE` and `HONDA_DYN_IGNITION_NOTE`.
- `_sync_honda_dyn_toggles()` edge-syncs the toggle from the param. It is needed because `ToggleSP` reads its param only once, at construction, and the same param is edited from other places.
- The toggle is not gated on brand.

**`selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py`**

- `HondaSettings` was empty upstream. It now has the toggle and a `Learned Values` row with a RESET button. Reset is offroad only, sits behind a confirmation dialog, and re-checks offroad when the dialog is confirmed.
- `update_settings()` rebuilds the toggle description each frame from `DYN_DESC` and `DYN_IGNITION_NOTE`. When `ui_state.has_longitudinal_control` is false it prefixes `DYN_NO_LONG_DESC` in bold (`This feature is unavailable because sunnypilot Longitudinal Control is not enabled on this car.`). It only changes the text; the toggle itself stays settable.
- Module-level API used by the mici page: `PEDAL_GAIN_BP`, `LEARNED_DEFAULTS`, `TUNING_PARAM`, `learned_value()`, `learned_pedal_gains()`, `reset_learned_values()`.
- The readout refreshes once a second (`LEARNED_REFRESH_S`), shows speeds in the `IsMetric` units, and shows the brake value as a gain (`x` followed by `1.0 + HondaDynBrakeGain`, sunnypilot `2b2d8e0a7`).
- The key names and defaults are duplicated here on purpose instead of importing the tuner, so a failing opendbc import cannot blank the settings screen. A test keeps the copies in sync.

### 11.3 Small UI (comma 4, mici)

The comma 4 runs the small UI. It has no Cruise or Vehicle panel, so neither page above can be reached on it (sunnypilot `d8863e59e`).

- `selfdrive/ui/sunnypilot/mici/layouts/vehicle.py` (new): `VehicleLayoutMici(NavScroller)`, with `HondaLearnedInfo`, the toggle as a `BigParamControl`, and reset behind `BigConfirmationDialog`. `car_brand()` gets the brand from `CarPlatformBundle` first and `CP.brand` second, cached on a 1 s tick. `HondaLearnedInfo` shows the pedal gains with their speeds in the user's units, then the brake value as a **signed offset** (`+0.12`, not a gain) and the aero factor as `x1.00`.
- `selfdrive/ui/sunnypilot/mici/layouts/settings.py`: a `vehicle` `SettingsBigButton` (icon `icon_vehicle.png`) inserted at index 3. It is visible when `car_brand() in ("", "honda")`, so a fingerprint that has not resolved yet never hides it. The `gateway` button at index 4 in the same hunk belongs to area A.

### 11.4 sunnylink

- `settings_ui_src/pages/vehicle.yaml`: a section with `id: honda` (it compiles to `vehicle_settings.honda` in `settings_ui.json`) holding the toggle, with `needs_onroad_cycle: true` and offroad-only enablement.
- `settings_ui_src/pages/cruise.yaml`: a section `honda_dynamic_learning`, visible when the capability `brand == honda`. It shows the eight learned keys as `widget: info` rows with `step: 0.001`. The labels are in km/h (0, 11, 22, 36, 54, 72), converted from the m/s breakpoints.
- Two lessons, both now pinned by tests. First, `blocked: true` means DEVICE_ONLY and the dashboard hides the row, so it must not be used to mean read-only (`d033e3dbd`). Second, info rows did not render inside a brand's vehicle section, only on a page, so they live on the Cruise page (`4131c8778`). A key may appear in only one place.
- `settings_ui.json` is generated by `sunnypilot/sunnylink/tools/compile_settings_ui.py`. Recompile it; never merge it by hand.

**Two stale or inconsistent texts** (not fixed; fix them the next time these files are touched):

- The toggle's `details` in `vehicle.yaml` still says what it has learned is `on the read-only rows below`. Those rows moved to the Cruise page in `4131c8778`.
- The brake value is shown three different ways. The `hondadyn` log and the big UI show a gain (`1.0 + offset`); the mici page shows a signed offset; sunnylink's `cruise.yaml` shows the raw param with the description `0.00 until it has learned anything. Positive adds brake, negative trims it`. The param itself stays an offset (9.1).

### 11.5 `sunnypilot/sunnylink/statsd.py`

Adds `HondaDynamicTuningEnabled` and the eight learned keys to the `sp_stats` device-params list, so convergence can be watched without pulling a route. The toggle is included because a gain of 1.000 could mean either converged or never switched on.

### 11.6 Test

`selfdrive/ui/tests/test_honda_dynamic_settings.py` parses the files rather than importing them, so it does not need raylib. Its tests:

- `test_learned_params_are_registered_as_floats`
- `test_toggles_are_registered_and_backed_up`
- `test_pedal_gain_breakpoints_match_the_learned_gains`
- `test_both_panels_drive_the_same_params`
- `test_honda_panel_publishes_its_items`
- `test_mici_page_shares_the_panel_params`
- `test_mici_settings_registers_the_vehicle_page`
- `test_sunnylink_exposes_the_same_toggles`
- `test_sunnylink_learned_values_are_read_only_and_on_a_page`
- `test_sunnylink_keys_are_registered_and_unique`
- `test_panel_defaults_match_the_tuner`

None of them checks the display inconsistencies in 11.4.

---

## 12. Tests

| test | repo | how to run | what it pins |
|---|---|---|---|
| `opendbc/car/honda/tests/test_elesys.py` | opendbc | unittest or pytest | category membership and dispatch; the upstream Nidec map untouched; the Elesys gas/brake golden table; the pump (20 cases); the gas curve; the units bit; the gear dwell; the stock AEB truth table and DBC signal names (not the `carstate.py` branch, 6.3) |
| `opendbc/safety/tests/test_honda.py` (`TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety`) and `common.py` | opendbc | pytest or unittest; needs the built `libsafety` | section 8 |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` | opendbc | **standalone script**: `python <file>` with opendbc on `PYTHONPATH` | the tuner on its own, sections 1-5 and 9-16: toggle off is a no-op, pitch, breakpoint weights, pedal, brake, wind, params, importing without openpilot, three rounds of review regressions, drive-mode gating, aero kept apart from pedal and brake |
| `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | opendbc | **standalone script** | the real `CarController`, frame by frame: [1] toggle off matches stock, [2] toggle on, [3] gas and brake never together, [4] the standstill hold is not scaled by the learned gain, [5] a disengage unwinds the brake gain, [6] the interceptor owns the gas at every speed (decodes `PCM_GAS`), [9] fuel and odometer. Sections 7, 8, 10-15 and 14b are area B |
| `selfdrive/controls/tests/test_stopping_debounce.py` | sunnypilot | **standalone script**; stubs `cereal` and `openpilot` | 10.1 |
| `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | sunnypilot | pytest | 10.2 |
| `selfdrive/car/tests/test_car_control_sp_seam.py` | sunnypilot | pytest | 10.5 |
| `selfdrive/ui/tests/test_honda_dynamic_settings.py` | sunnypilot | pytest | section 11 |

**The three standalone scripts are a hazard for the normal test runners.** They have no `test_` functions and no `__main__` guard: every check runs at import time and the script calls `sys.exit(1)` at module level on failure. Their file names still match `test_*.py`, so the runners import them:

- **`test_stopping_debounce.py` poisons the pytest process.** sunnypilot's `pyproject.toml` has `testpaths` including `selfdrive` and `python_files = test_*.py`, so a plain `pytest` collects it. At import it unconditionally replaces `cereal`, `openpilot`, `openpilot.common`, `openpilot.common.realtime`, `openpilot.common.pid`, `openpilot.common.params`, `openpilot.selfdrive`, `openpilot.selfdrive.controls`, `openpilot.selfdrive.controls.lib`, `openpilot.selfdrive.controls.lib.drive_helpers`, `openpilot.selfdrive.modeld` and `openpilot.selfdrive.modeld.constants` in `sys.modules` with stubs. Every later test in the same worker then imports the stubs, even when all of this script's checks pass.
- **The two opendbc tuner scripts** do not touch `sys.modules`, but opendbc's `lefthook run test` runs `unittest-parallel -j4`, whose discovery imports every `test*.py` module. Both scripts therefore execute in full during discovery, and a failing check exits the discovery process instead of being reported as a failed test. pytest would do the same at collection.

Fix, not done yet: rename them (for example `check_*.py`), or move the stubs and checks under `if __name__ == "__main__":`, or add them to the runners' ignore lists. Until then, run them directly, and exclude `test_stopping_debounce.py` from any pytest run that covers `selfdrive/controls/tests`.

The fork's commit messages say the sunnypilot pytest suites cannot run on the owner's Windows checkout, because the symlinked `openpilot/common` and `cereal/car.capnp` check out as text; they were run with shims or on Linux. I did not run any test while writing this document.

---

## 13. Other changes

### 13.1 `.gitmodules` and the `opendbc_repo` gitlink

The `[submodule "opendbc"]` URL changed from `https://github.com/sunnypilot/opendbc.git` to `https://github.com/SoRadGaming/opendbc.git`, with `branch = sp-master` (sunnypilot `26edc2baa`). The gitlink pins `cf583b37`. Every opendbc change in this document reaches the car only through this pin, so bump it in the same commit as any sunnypilot change that depends on it, as the fork's `bump opendbc` commits do.

Upstream has also edited `.gitmodules` since the fork point (the `msgq` URL and the `neural_network_data` path). Those are different lines from the fork's, so it is expected to merge automatically; I did not verify that. Keep the fork's opendbc URL and branch in whatever upstream's file now says.

### 13.2 `sunnypilot/sunnylink/tools/compile_settings_ui.py`

Three `open()` calls get `encoding="utf-8"`, and the writer pins `newline="\n"`. On Windows the default cp1252 encoding mangled `m/s²` elsewhere in the JSON, and line endings churned between platforms (sunnypilot `d033e3dbd`). The change is not car-specific and carries no marker. Keep it unless upstream has fixed the same thing.

---

## 14. Upstream merge

The goal is to bring the branch up to date with upstream while keeping the custom code. This section covers what a trial merge shows and what upstream has changed underneath the fork. The conflict lists come from a scratch trial merge made for this documentation run and were reproduced by a reviewer with `git merge-tree`. The upstream code changes described below were read directly with `git diff` against `refs/upstream/master`.

### 14.1 Order

1. Merge upstream opendbc into `sp-master` first, then fix and test.
2. Merge sunnypilot next. The trial merge reports a submodule conflict on `opendbc_repo` (the fork pins `cf583b37`, upstream `f95f996f`). Resolve it by pinning the merged opendbc commit from step 1.
3. Flash panda firmware built from the merged tree before driving (8.4).

### 14.2 opendbc: expected conflicts and what moved

A trial merge of `refs/upstream/master` (`f95f996f`) into `cf583b37` conflicts in five files: `carcontroller.py`, `carstate.py`, `hondacan.py`, `interface.py` and `values.py`. Everything else in the inventory merges automatically, including `honda.h` and `test_honda.py`, which upstream has not touched.

Every one of these conflicts has the same cause: an upstream refactor that stops using the `HONDA_*` platform sets as the idiom.

- Upstream deleted `HONDA_NIDEC_ALT_PCM_ACCEL`, `HONDA_NIDEC_ALT_SCM_MESSAGES` and `HONDA_BOSCH_TJA_CONTROL`.
- It turned `HONDA_BOSCH`, `HONDA_BOSCH_ALT_RADAR`, `HONDA_BOSCH_RADARLESS` and `HONDA_BOSCH_CANFD` into `frozenset(c for c in CAR if c.config.flags & ...)`, defined after `DBC`.
- Almost all code now tests `CP.flags & HondaFlags.X` or `ret.flags & HondaFlags.X`.
- Functions that used to take `car_fingerprint` now take `CP`, or nothing.

How to carry the fork across:

| fork code | upstream now | what to do |
|---|---|---|
| `HONDA_ELESYS = CAR.with_flags(HondaFlags.ELESYS)` | `with_flags()` still exists, but upstream no longer uses it | either keep the line, or define `HONDA_ELESYS = frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)` next to the others. Keep the name: `radar_interface.py`, `carstate_ext.py`, `gas_interceptor.py` and `test_elesys.py` import it |
| `candidate in HONDA_ELESYS` / `self.CP.carFingerprint in HONDA_ELESYS` | `CP.flags & HondaFlags.X` | in files upstream converted, prefer `CP.flags & HondaFlags.ELESYS` to match. Both forms work |
| `compute_gas_brake(accel, speed, fingerprint)` with `elif fingerprint in HONDA_ELESYS` | `compute_gas_brake(accel, speed, CP)` with `if CP.flags & HondaFlags.BOSCH` | add `elif CP.flags & HondaFlags.ELESYS: return compute_gb_honda_elesys(accel, speed)`. Also update `TestElesysCategory.test_dispatch`, which passes a fingerprint |
| `create_brake_command(..., car_fingerprint, stock_brake, is_metric, CP_SP)` | `create_brake_command(packer, CAN, apply_brake, pump_on, pcm_override, pcm_cancel_cmd, fcw, stock_brake, CP_SP)`, with no fingerprint | the units bit has to know the car: add `CP` (or a bool) back, together with `is_metric`. Also update `TestBrakeCommandUnitsBit._frame()`, which calls it positionally |
| `actuator_hysteresis(brake, braking, brake_steady, v_ego, car_fingerprint)` | `actuator_hysteresis(brake, braking, brake_steady)` | the fork never changed this function; take upstream's version |
| imports of `HONDA_NIDEC_ALT_PCM_ACCEL` and `HONDA_BOSCH_TJA_CONTROL` in `carcontroller.py` | deleted | take upstream's import line, and add `HONDA_ELESYS` if you keep the set |
| explicit lists in `FW_QUERY_CONFIG.non_essential_ecus` | rewritten as comprehensions over flags | re-add `CAR.HONDA_ACCORD_9G_AU` to the explicit part of both the `Ecu.eps` and the `Ecu.vsa` list |
| `interface.py`: `if candidate in HONDA_ELESYS and ret.openpilotLongitudinalControl:` (safety param) | that block is now a series of `if ret.flags & HondaFlags.X:` lines | re-add it as `if ret.flags & HondaFlags.ELESYS and ret.openpilotLongitudinalControl:` |
| `interface.py`: `ret.vEgoStopping = 0.8` | `vEgoStopping` is in `CarParams.deprecated` in upstream's `car.capnp` | **delete the line.** opendbc's `CarParams` is the capnp struct, and assigning a deprecated field raises `AttributeError: struct has no such member`, so `_get_params()` throws and card cannot build the car. Then deal with the stop threshold (below) |
| `carstate.py`: the `LKAS_PROBLEM` branch under `if self.CP.carFingerprint not in HONDA_BOSCH:` | `if not (self.CP.flags & HondaFlags.BOSCH):` | keep the Elesys/else split inside the new condition |

**The stop threshold is gone upstream, and with it the fix for the red-light crawl.** At the fork point the planner passed `self.CP.vEgoStopping` into `get_accel_from_plan()`, so this car's 0.8 took effect. Upstream no longer reads `vEgoStopping` at all: `openpilot/selfdrive/controls/lib/drive_helpers.py` has `should_stop(v_ego, a_target)` returning `v_ego < 0.3 and a_target < 0.1`, used by `longitudinal_planner.py` for the MPC and cruise candidates. That is lower than the old default of 0.5, and this car crawls at 0.55-0.7 m/s approaching a stop, so after the merge `stopping` will rarely latch and the stopAccel ramp will not run: the behaviour `S:/OP/redlight_overshoot_findings.md` recorded before the 0.8 fix. Do not just delete the line and drive. Either:

- carry a fork change in `should_stop()` (and its callers) that takes a per-car threshold, for example a fork constant keyed by fingerprint or a `CarParamsSP` field, giving 0.8 on this car; or
- re-test stops on the car with upstream's 0.3 and the model's own `shouldStop`, and record the result before deciding.

The debounce rationale (10.1), its test stub (which declares `vEgoStopping: float = 0.8`), and the two tuner comments that quote `vEgoStopping = 0.8` (9.1) all need revisiting with whichever you choose.

Other upstream behaviour changes that reach this car even where nothing conflicts:

- **`minEnableSpeed`**: upstream `_get_params_sp()` now sets `stock_cp.minEnableSpeed = -1. if ret.enableGasInterceptor else stock_cp.minEnableSpeed` (opendbc upstream `4455464a`). This car has a pedal, so the fork's 19 mph becomes -1, which means engaging from standstill. Decide this deliberately. If 19 mph has to stay, re-assert it for `HONDA_ELESYS` after that line.
- `carstate_ext.py`: upstream added `ret.blockPcmEnable = ret.brakeHoldActive and not self.CP_SP.enableGasInterceptor` in the hybrid brake-hold branch. It merges automatically and does not affect this car.
- `radar_interface.py`: upstream removed `self.track_id` and the `aRel`/`yvRel`/`measured` lines. It merges automatically.
- `_gearbox_common.dbc` gained `11 B`, which is not copied into `_gearbox_legacy.dbc` (4.7).
- `structs.py`: upstream now always loads `car.capnp` from opendbc. The fork's `CarControlSP`/`CarStateSP` additions sit in the dataclass section and merge automatically. Check the capnp names and ordinals (6.5).
- `routes.py`, `car_list.json`, `fingerprints.py` and `safety/tests/common.py` merge automatically. Upstream made a 115-line change to `common.py`, so check that the Elesys exceptions still sit in the right function.

Tests to re-check against upstream signatures after the merge: `TestElesysCategory.test_dispatch` and `TestBrakeCommandUnitsBit._frame()` (both need edits, see the table), and `TestElesysGearDecode._cs()`, which calls `CarInterface.get_params(CAR, fp, [], False, False, False)` and `get_params_sp(...)` positionally. Those two signatures are unchanged at `f95f996f`, but `_cs()` goes through `_get_params()`, so it is the first test that will fail if the `vEgoStopping` line survives. I did not check whether upstream changed the `CarState` constructor it also uses.

### 14.3 sunnypilot: expected conflicts and what moved

**Upstream moved the whole tree under `openpilot/`**: `openpilot/selfdrive/...`, `openpilot/sunnypilot/...`, `openpilot/common/...`, `openpilot/cereal/...`. Git follows most of the renames, but not all of it:

- **Symlinks in the way.** At the fork point `openpilot/common`, `openpilot/selfdrive`, `openpilot/sunnypilot`, `openpilot/system` and `openpilot/tools` are symlinks (mode 120000); upstream has real directories there. The trial merge reports `directory in the way of openpilot/common` (and `openpilot/selfdrive`, `openpilot/sunnypilot`) `from HEAD; moving it to ...~HEAD`. Delete the `~HEAD` symlink copies and keep upstream's directories. Check for any other `~HEAD` entries.
- **Added files stay behind.** Every file the fork **added** stays at its old path and has to be moved by hand. For area C these are `selfdrive/controls/tests/test_stopping_debounce.py`, `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`, `selfdrive/ui/sunnypilot/mici/layouts/vehicle.py`, `selfdrive/ui/tests/test_honda_dynamic_settings.py` and `selfdrive/car/tests/test_car_control_sp_seam.py`. Their imports already use `openpilot.` paths, but check the `cereal` imports: some upstream modules now use `from openpilot.cereal import ...`. `test_stopping_debounce.py` finds `longcontrol.py` by a relative path (`../lib/longcontrol.py`), which still works if the file moves with its directory.

Content conflicts in files this document covers: `desire_helper.py`, `longcontrol.py`, mici `settings.py` and `sunnypilot/modeld_v2/modeld.py` (area C hunks), plus `controlsd.py` and `selfdrived.py` (area B hunks, see LKAS-GATEWAY-PROTOCOL.md §14). There is also `selfdrive/modeld/modeld.py`, which upstream deleted and replaced with `openpilot/selfdrive/modeld/modeld.py`, so git reports a modify/delete conflict. These merge automatically: `card.py`, `helpers.py`, `params_keys.h`, `custom.capnp`, `latcontrol*.py`, `controlsd_ext.py`, `vehicle.yaml`, `settings_ui.json`, `statsd.py` and `mads.py`.

File by file:

- **`longcontrol.py`.** Upstream made these changes:
  - Removed the `starting` state logic and its use of `CP.startingState`, `vEgoStarting` and `startAccel`.
  - Dropped `CP` and `v_ego` from `long_control_state_trans(CP_SP, active, long_control_state, should_stop, brake_pressed, cruise_standstill)`.
  - Replaced `stoppingDecelRate` with a fixed `1.0 * DT_CTRL` ramp.
  - Made the PID integral-only.
  - The stop decision it receives now comes from the fixed 0.3 m/s `should_stop()` (14.2).

  The debounce is a post-step, so it re-applies around the new call. Keep `prev_state`, the `leaving_stop` test (the `starting` state simply never occurs any more) and the 40-frame hold. Then re-validate on the car: both the car's stop behaviour and the debounce rationale depend on the old stop threshold and the old 0.8 m/s^3 re-clamp rate the comment quotes. In `test_stopping_debounce.py`, update or drop the `startingState` case, and update the stubs, which mimic the old signature, the old `from cereal import car` import and the old `vEgoStopping`.
- **`desire_helper.py`.** Upstream removed `DESIRES`, `lane_change_ll_prob` and `keep_pulse_timer`, added `LANE_CHANGE_START_TIME`, and gave `update()` two new parameters, `left_edge_detected=False, right_edge_detected=False`. Re-apply:
  - `NUDGE_FIRM` and `NUDGE_HOLD_FRAMES`
  - the `car_fingerprint` constructor argument
  - `self.nudge_frames`, reset in the same place where upstream now resets `lane_change_timer` on entry to `preLaneChange`
  - the firm-or-held test
  - `driver_torque_stale=False`, added after the new parameters and passed by keyword from both modeld call sites
- **modeld.** Re-make the fork's `selfdrive/modeld/modeld.py` change in `openpilot/selfdrive/modeld/modeld.py`, and again in `openpilot/sunnypilot/modeld_v2/modeld.py`: subscribe to `carStateSP`, construct `DesireHelper(CP.carFingerprint)`, and pass `driverTorqueStale`. Then resolve the modify/delete conflict by deleting the old path.
- **`controlsd.py`.** Upstream renamed services (`liveDelay` to `lateralDelay`, `liveParameters` to `vehicleParameters`, `liveTorqueParameters` to `lateralTorqueParameters`) and the names returned by `LaC.update()`. Re-add three things: `'carStateSP'` in the new `SubMaster` list, the `set_linbus_gateway()` call before `LaC.update()`, and `self.run_ext(self.sm, self.pm, lac_log, self.LaC)`. Upstream's `run_ext` still takes `(sm, pm)`, so the wider signature in `controlsd_ext.py` merges automatically.
- **`selfdrived.py`.** Re-add `'carStateSP'` to the `SubMaster` list.
- **mici `settings.py`.** Upstream now builds panels without a `back_callback` (`SunnylinkLayoutMici()`), inserts models at index 1 and sunnylink at index 5, and has added a device panel. Re-add the vehicle button, and area A's gateway button, at indices that fit the new order. Adapt the `VehicleLayoutMici` constructor to however upstream now builds `NavScroller` pages.
- **The lagd comment in `interface.py`** refers to `liveDelay`, which upstream now calls `lateralDelay`. This only affects a comment.
- **`settings_ui.json`.** Never merge it by hand. After the yaml files merge, re-run `compile_settings_ui.py` and its roundtrip test.
- **The UI brand page, `cruise.py` and `statsd.py`** were only renamed upstream (plus a small upstream change in statsd). They should come across unchanged. None of them carries a marker, so check them against the inventory.

### 14.4 Identifiers to preserve

| kind | identifier |
|---|---|
| platform | `CAR.HONDA_ACCORD_9G_AU`. The string `HONDA_ACCORD_9G_AU` is also a key in `desire_helper.NUDGE_FIRM`, `car_list.json`, `substitute.toml` and the tests |
| flags | `HondaFlags.ELESYS = 1024`; `HondaSafetyFlags.ELESYS_SCM_STANDDOWN = 32`; `HONDA_PARAM_ELESYS_SCM_STANDDOWN = 32`; `honda_elesys_scm_standdown` |
| sets | `HONDA_ELESYS` |
| DBC names | `honda_accord_au_2015_can` (becomes `_generated`), `honda_accord_2015au_radar`, and the fragments in 4.1 |
| CAN IDs openpilot sends on this car | 0x0E4 on bus 0 (5 bytes), 0x1FA on bus 0, 0x30C on bus 0, 0x1A6 on **bus 2**, 0x200 on bus 0 (pedal), 0x500 on bus 0 (area B). Never 0x33D. The panda TX list also allows `{0x194, 0, 4}`, but this car's DBC has no 0x194 (it imports `_steering_control_e.dbc`, whose `STEERING_CONTROL` is 0x0E4) and openpilot does not send it |
| CAN IDs read | 0x188 `GEARBOX_AUTO`; 0x1A6 `SCM_BUTTONS` (including `FUEL_LEVEL`); 0x221 `ECON_STATUS`; 0x294 `SCM_FEEDBACK`; 0x33D `LKAS_HUD` on bus 0; 0x1FA and 0x30C from bus 2; 0x18F `STEER_STATUS` with `STEER_CONTROL_ACTIVE 32:1`; radar 0x400, 0x410-0x417 and 0x420-0x424 on bus 1 |
| signals | `CMBS_BRAKE`, `CMBS_DISABLED`, `AEB_REQ_3`, `CMBS_BUTTON`, `FUEL_LEVEL`, `FUEL_SENDER`, `ODOMETER_KM`, `ECON_ON`, `GEAR_SHIFTER`, `GEAR`, `SET_ME_1` (the units bit on this car) |
| functions | `compute_gb_honda_elesys`, `brake_pump_hysteresis_elesys`, `create_scm_buttons_no_cruise`, `update_gear_elesys`, `elesys_gas_multiplier`, `HondaDynamicTuner` (`update_state`, `brake_gain`, `wind_scale`, `update_wind`, `pedal_gain_at`, `update_pedal`, `persist`, `log_state`, `debug_values`), `learned_value`, `learned_pedal_gains`, `reset_learned_values`, `car_brand`, `VehicleLayoutMici` |
| constants | `ELESYS_PUMP_*`, `ELESYS_GAS_BP`, `ELESYS_GAS_V`, `SPORT_DWELL`, `FUEL_LEVEL_FULL`, `STEER_THRESHOLD[HONDA_ACCORD_9G_AU] = 600`, `NUDGE_FIRM`, `NUDGE_HOLD_FRAMES`, `STANDSTILL_SPEED`, `STOPPING_EXIT_DEBOUNCE`, `LINBUS_I_CARRY_MAX`, `LINBUS_I_HOLD_TAU`, and the tuner constants in 9.1 |
| CarParams values | `transmissionType = automatic`, `longitudinalActuatorDelay 0.6`, `stopAccel -0.8`, `steerActuatorDelay 0.38`, `steerAtStandstill True`, `minEnableSpeed 19 mph` (overridden upstream, 14.2). `vEgoStopping 0.8` must be **deleted** on upstream; its effect (a 0.8 m/s stop threshold) has to be carried some other way or consciously dropped (14.2) |
| params | see 11.1 |
| capnp fields area C code reads | `CarStateSP.driverTorqueStale @2`; `CarControlSP.lateralControl @5` (rebuilt in `helpers.py`) |
| markers | `FORK(HONDA_ELESYS)`, `FORK(HONDA_ACCORD_9G_AU)`, `FORK:`, and `HONDA_ACCORD_9G_AU` in `honda.h`. Incomplete; see Baseline |
| log tag | `hondadyn` |

### 14.5 After the merge

1. Run `git diff refs/upstream/master HEAD --stat` in both repos and compare the file list with section 1 (adjusting for upstream's `openpilot/` move). Every file in the inventory must still differ from upstream, and in the way this document says. As a secondary check only, `git grep -n -e 'FORK(HONDA_ELESYS)' -e 'FORK(HONDA_ACCORD_9G_AU)' -e 'FORK:' -e HONDA_ELESYS -e HONDA_ACCORD_9G_AU` finds the marked hunks; it will not find the unmarked ones listed in the Baseline.
2. Build, regenerate the DBCs, and load `honda_accord_au_2015_can_generated.dbc` through the parser.
3. Safety: run the two `TestHondaElesys*` classes, the whole of `test_honda.py`, and MISRA.
4. Convert the sunnypilot pytest-function tests (`test_lane_change_nudge.py`, `test_car_control_sp_seam.py`, `test_honda_dynamic_settings.py`) to `unittest.TestCase` first. Upstream's `tools/test_runner.py` collects only `TestCase` classes, so bare `def test_*` functions run as zero tests and "pass" (README, Tests). Then run `test_elesys.py`, the two standalone tuner scripts (directly), `test_stopping_debounce.py` (directly, not under pytest; section 12), `test_lane_change_nudge.py`, `test_car_control_sp_seam.py` and `test_honda_dynamic_settings.py`. Also run opendbc's `test_car_interfaces`, `test_platform_configs` and `test_docs` against the new platform.
5. Recompile `settings_ui.json`.
6. On the first drive, check that:
   - the car selects `HONDA_ACCORD_9G_AU` from the bundle with no FW query in the log (`Fixed fingerprint ... skipping the VIN/FW query`)
   - there is no ACC or CMBS fault on the first ignition after the update
   - the gear reads P while parked
   - the fuel gauge is sane
   - stops at red lights latch the stopping state and hold without crawling (14.2)
   - the car does or does not engage below 19 mph, as you decided (14.2)
   - `hondadyn` lines appear if the tuner is on
