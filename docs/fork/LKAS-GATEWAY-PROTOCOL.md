# Fork changes, area B: the LKAS gateway protocol

How openpilot steers this car through the EPS-LKAS gateway board, and every change this fork
makes to sunnypilot and opendbc to do it. This is a maintenance document. Use it to carry
these changes across an upstream merge without losing any of them.

| | |
|---|---|
| **Scope** | openpilot → `0x0E4` / `0x500` on CAN → board → the EPS's 12 V serial link, and the board's telemetry back into openpilot |
| **Platform** | `HONDA_ACCORD_9G_AU`, the only member of `HONDA_ELESYS` (`HondaFlags.ELESYS = 1024`) |
| **sunnypilot fork** | `SoRadGaming/sunnypilot` `master`, after the 2026-09-27 upstream sync: branch `merge/upstream-2026-09-27` (`d1a14edcb` plus the post-merge review fixes). Fork point `a5f44653d` (previously `31dc4d8e5`). Every sunnypilot path is under `openpilot/` since the sync |
| **opendbc fork** | `SoRadGaming/opendbc` `sp-master`, after the sync: `8bd6e314` plus the review fixes. Fork point `f95f996f` (previously `b9712d20`) |
| **Board firmware** | `S:/Software/EPS-LKAS`. Last firmware commit `d995bc9`; the commits since change documentation and tools only (`862540c` teaches `bundle_appslot.py` the new layout). The bundled app-slot image is `d995bc95` (sp-live `1f20b7b68`) |
| **Written** | 2026-09-27, from the diffs, not from the commit messages alone. Revised after review the same day, and again for the upstream sync (section 14) |

Sibling documents in `docs/fork/` cover the other two areas. Area A, updating the board's
firmware from the comma, is `GATEWAY-UPDATE.md`. Area C is the car itself: fingerprint, tuning,
longitudinal, radar, UI settings and `STEER_THRESHOLD`. Where a file mixes areas, this
document describes only the area-B hunks and says where the rest is covered.

These existing documents are the design history and the long-form specs. This document
refers to them and does not repeat them:

| document | what it is | caution |
|---|---|---|
| `docs/SP_HUD_STATUS.md` | the openpilot-side SP-PROTOCOL spec (`0x500`, `0x704`, `0x70B`, `0x0E4` byte 2) | partly out of date, see [section 13](#13-stale-comments-and-documents) |
| `docs/SP_GATEWAY_FIRMWARE.md` | the board-side view: what the board receives and how it parses it, and the torque-domain history | |
| `docs/CHANGELOG_SERIAL_STEERING.md` | dated history of this work, with route evidence | the 2026-09-18 entry has been superseded |
| `CHANGELOG-elesys.md` §15, §17, §18 | the v2 integrator handshake, the card crash, the wrong-bus bug | |
| board `docs/SP-PROTOCOL-V3.md`, `docs/SP-PROTOCOL.md`, `docs/OP-INTEGRATION.md`, `docs/STAGE6-SPEC.md`, `docs/EPS-FAULT-STATES.md`, `dbc/eps-lkas-gw.dbc`, `HANDOFF.md` | the board side of the contract | `SP-PROTOCOL-V3.md` §1.2 and §1.3 disagree with the code, see section 13 |

---

## 1. Architecture

```
 controlsd ──carControl + carControlSP.lateralControl──► card ─► CarController (opendbc)
     ▲                                                              │
     │ carStateSP.linbusGateway                                     │ 0x0E4 STEERING_CONTROL 100 Hz, bus 0
     │ carStateSP.driverTorqueStale                                 │ 0x500 SP_HUD_STATUS     10 Hz, bus 0
 carstate_ext (opendbc) ◄── bus 0 ──┐                               ▼
                                    │                          panda (honda.h)
   0x700 EPS_LIN_RAW    100 Hz      │                               │ car bus (bus 0)
   0x704 GW_ACTIVE       10 Hz      │                               ▼
   0x70B GW_STEER_GRANT  10 Hz      └──────────────────  EPS-LKAS board (FDCAN1, car side)
   0x707 GW_VERSION      1/min                                      │ 9600 baud 8E1 serial, LKAS→EPS, 4 bytes
   0x70F GW_BUILD        1/min                                      ▼
                                                                   EPS
```

Four facts about this car shape all of the code:

1. **Nothing in the car reads `0x0E4`.** The EPS has no CAN steering input. The stock LKAS
   camera steers it over a 12 V single-wire serial link. The board sits in line with the
   camera, reads openpilot's `0x0E4` off the car bus, and writes the camera-to-EPS serial frame
   itself. The EPS's 5-byte answer comes back to the board, and the board mirrors it onto CAN.
2. **The board is on bus 0.** On this harness the comma's bus 2 is spliced at the Elesys radar,
   not at the camera. Every frame in this document is sent and received on bus 0 (`CAN.pt`),
   never `CAN.camera`. Route `000000b9` showed `0x500` only as `src 130`, which means the board
   had never received it (sp-live `9196e138e`, opendbc `a091808d`).
3. **Until the board engages, openpilot's loop is open.** `latActive` is true and openpilot
   commands, but the car does not respond. Without the hold in [section 7](#7-the-integrator-hold-sp-protocol-v2)
   the integrator winds up. Route `000000b3` reached +0.65, pointing right in a left curve.
4. **The EPS stops updating `0x18F STEER_TORQUE_SENSOR` while it is under LKAS control.** The
   frame keeps arriving with a valid counter and checksum, but the torque value is frozen.
   Driver torque has to come from the board's mirror of the EPS's serial frame instead
   ([section 8](#8-driver-torque-the-0x18f-latch-the-mirror-and-the-stale-guard)).

The board side (serial timing, the intro, the LSB dither, the refusal holds, the HUD) is in the
firmware repo. Start with `S:/Software/EPS-LKAS/CLAUDE.md` ("LKAS serial") and `HANDOFF.md`.
Read "THE SIGN" in `HANDOFF.md` before touching the command polarity.

---

## 2. Inventory: every area-B hunk

"sp" = sunnypilot fork, "odbc" = opendbc fork. Commits are the ones that introduced or changed
the hunk. Integration-test sections ("integration §N") refer to
`test_dynamic_tuning_integration.py`, see section 12.

| repo | file | area-B hunk | commits | pinned by |
|---|---|---|---|---|
| odbc | `opendbc/car/honda/hondacan.py` | `create_steering_control(..., serial_gateway=False, ldw_left=False, ldw_right=False)`; `SP_HUD_PROTOCOL_VERSION`, `SP_OP_STATE_*`, `SP_HUD_MAX_TORQUE`, `create_sp_hud_status()`; `HONDA_ELESYS` added to the import. (`create_brake_command(..., is_metric, ...)` and `create_scm_buttons_no_cruise()` are area C) | `031743c4` `ad76c278` `02e7fd71` `1a133e47` | integration §7, §10, §11 |
| odbc | `opendbc/car/honda/carcontroller.py` | `BRAKE_RELEASE_FRAMES`, `brake_release_scale()`, `self.brake_release_frames`, the brake ceiling on `limited_torque`, the `serial_gateway` call, the `0x500` block that replaces `create_lkas_hud` on ELESYS (`release_brake`, `release_driver`, `lat_ready`, `op_state`). Everything longitudinal in this file is area C | `031743c4` `ad76c278` `a091808d` `02e7fd71` `1a133e47` `43a98b9d` | integration §7, §10, §11, §12 |
| odbc | `opendbc/car/honda/carstate.py` | `get_can_parsers()` registers `GW_ACTIVE`, `GW_STEER_GRANT`, `EPS_LIN_RAW`, `GW_VERSION`, `GW_BUILD` with `float("nan")`; `CarStateExt.update(self, ret, ret_sp, can_parsers)`. The ELESYS `carFaultedNonCritical` branch is area C but described in [section 10.1](#101-lkas_problem-read-back-carstatepy) | `ad76c278` `02e7fd71` `2cc16a02` `124465ca` | integration §8, §13 |
| odbc | `opendbc/sunnypilot/car/honda/carstate_ext.py` | `_update_linbus_gateway`, `_update_linbus_grant`, `_update_linbus_firmware` (area A), `_update_driver_torque_validity`, `_eps_lin_driver_torque_valid`, the constants, the `ret_sp` parameter. `FUEL_LEVEL_FULL` and `fuelGauge` are area C | `ad76c278` `02e7fd71` `1a133e47` `23dce590` `2cc16a02` `124465ca` `2619b404` | integration §8, §13, §14, §14b |
| odbc | `opendbc/car/structs.py` | `CarControlSP.LateralControl`, `CarStateSP.driverTorqueStale`, `CarStateSP.LinbusGateway` | `ad76c278` `02e7fd71` `23dce590` `124465ca` `2619b404` | `test_car_control_sp_seam.py` |
| odbc | `opendbc/car/honda/interface.py` | `ret.steerAtStandstill = True` for HONDA_ELESYS (it exists for the lane graphic). `steerActuatorDelay` is area C | `bb0fe222` | none |
| odbc | `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc` | new: `0x500`, `0x700`, `0x704`, `0x707`, `0x70B`, `0x70F`. **Created as `_sunnypilot_hud.dbc` in `031743c4` and renamed in `ad76c278`**: use `git log --follow` or the history starts at `ad76c278` | `031743c4` `ad76c278` `a091808d` `02e7fd71` `1a133e47` `2cc16a02` `124465ca` `43a98b9d` `cf583b37` | integration §7, §8, §13, §14b |
| odbc | `opendbc/dbc/generator/honda/_steering_control_e.dbc` | new: `0x0E4` with byte 2 split into `LDW_LEFT`/`LDW_RIGHT`/`SET_ME_X00_3`; `0x18F` with `STEER_CONTROL_ACTIVE` at bit 32 | `2f19864a` `02e7fd71` `1a133e47` | integration §11, §14 |
| odbc | `opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc` | the two `IMPORT` lines for the fragments above (the file itself is area C). `031743c4` imported `_sunnypilot_hud.dbc`; `ad76c278` rewrote the line to `_sunnypilot_linbus_gw.dbc` | `2f19864a` `031743c4` `ad76c278` | |
| odbc | `opendbc/safety/modes/honda.h` | `{0x500, 0, 8, .check_relay = false}` in both `HONDA_N_ELESYS_STANDDOWN*_TX_MSGS` lists (the lists themselves are area C) | `031743c4` `a091808d` | `test_honda.py` `TestHondaElesys*` `TX_MSGS` |
| odbc | `opendbc/safety/tests/test_honda.py` | `[0x500, 0]` in the two ELESYS `TX_MSGS` | `031743c4` `a091808d` | itself |
| odbc | `opendbc/safety/tests/common.py` (added in the 2026-09 merge) | `test_tx_hook_on_wrong_safety_mode` no longer checks `0x500` between the two `TestHondaElesys*` classes: both stand-down TX lists carry it, so each mode "allowed" the other's frame. It is still checked against every other brand, and the area C exemptions (`0x1A6`, `0x30C`) are unchanged. Tagged `FORK(LKAS-GATEWAY)` | 2026-09 merge | `test_honda.py` (942 run, OK) |
| odbc | `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` | sections 7, 8, 10–15 are protocol tests (1–6 and 9 are area C). Since the 2026-09 merge §10 sets `mads.enabled` for the `LAT_READY` case and adds the MADS-off case, §15 passes `driver_torque_stale=` by keyword, and `TestDynamicTuningIntegration` lets unittest discovery report the script | `031743c4` `ad76c278` `02e7fd71` `1a133e47` `23dce590` `2cc16a02`, 2026-09 merge | runs standalone, or under discovery |
| sp | `openpilot/cereal/custom.capnp` | `CarControlSP.lateralControl @5`; `CarStateSP.linbusGateway @1`, `driverTorqueStale @2`; `LinbusGateway @0–@18` (`@19–@26` are area A) | `176e6c07c` `27048ebaa` `98f657457` `56a404318` | seam test |
| sp | `openpilot/selfdrive/car/helpers.py` | `convert_carControlSP` rebuilds `lateralControl` | `d11d2c9a8` | seam test |
| sp | `openpilot/sunnypilot/selfdrive/controls/controlsd_ext.py` | `state_control_ext(sm, lac_log=None, LaC=None)` fills `CC_SP.lateralControl`; `run_ext(sm, pm, lac_log=None, LaC=None)` | `176e6c07c` | integration §7 (the car side only) |
| sp | `openpilot/selfdrive/controls/controlsd.py` | `'carStateSP'` in the SubMaster, the `LaC.set_linbus_gateway()` call before upstream's 3-value `LaC.update()`, `self.run_ext(self.sm, self.pm, lac_log, self.LaC)` | `176e6c07c` | none |
| sp | `openpilot/selfdrive/controls/lib/latcontrol.py` | `LINBUS_I_CARRY_MAX`, `LINBUS_I_HOLD_TAU`, the `linbus_gateway_*`/`_linbus_was_actuating`/`integrator_frozen` attributes, `set_linbus_gateway()`, `_linbus_integrator_gate()` | `176e6c07c` `39b857567` | the freeze only: `test_latcontrol_gateway_hold.py`. The carry and the decay have no test |
| sp | `openpilot/selfdrive/controls/lib/latcontrol_torque.py`, `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py` | `linbus_hold = self._linbus_integrator_gate()` plus `or linbus_hold` in `freeze_integrator` | `176e6c07c` `946b5fa21` | `test_latcontrol_gateway_hold.py` (both controllers, through the extension) |
| sp | `openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_ext_base.py` (added in the 2026-09 merge) | `or getattr(self.lac_torque, "integrator_frozen", False)` in `update_output_torque()`'s `freeze_integrator`. The extension updates the owning controller's PID a second time in the frame when Lateral Jerk (upstream `91a53aa16`) or NNLC is on, and without this that second update wound the integrator open-loop through every hold | 2026-09 merge | `test_latcontrol_gateway_hold.py` (new, same merge) |
| sp | `openpilot/selfdrive/controls/lib/desire_helper.py` | `update(..., left_edge_detected=False, right_edge_detected=False, driver_torque_stale=False)`: the fork's parameter is last, after upstream's road-edge parameters, and callers pass it by keyword. `not driver_torque_stale` in upstream's rewritten `torque_applied`. `NUDGE_FIRM`, `NUDGE_HOLD_FRAMES`, `nudge_frames` and the `DesireHelper(car_fingerprint)` constructor are area C | `56a404318`, 2026-09 merge | `TestLaneChangeNudge.test_a_stale_torque_still_confirms_nothing` and `test_driver_torque_stale_comes_after_the_road_edges`, integration §15 |
| sp | `openpilot/selfdrive/modeld/modeld.py`, `openpilot/sunnypilot/modeld_v2/modeld.py` | `"carStateSP"` in upstream's renamed SubMaster, `driver_torque_stale=sm['carStateSP'].driverTorqueStale` passed to `DH.update` by keyword after the edges. `DesireHelper(CP.carFingerprint)` is area C | `56a404318`, 2026-09 merge | none |
| sp | `openpilot/sunnypilot/mads/mads.py` | `LINBUS_REASON_DRIVER_OVERRIDE`, `self._gw_paused`, the gateway pause block, and `if self._gw_paused: return False` at the top of `should_silent_lkas_enable()` (`2cfcd3c6a`). Also the steer-rate emergency takeover (`EMERGENCY_STEER_RATE`, `EMERGENCY_STEER_RATES`, `EMERGENCY_STEER_FRAMES`, `self._fast_steer`, and since 2026-09-30 its two settings: `read_emergency_steer_rate()`, and `self.emergency_steer_disable`/`self.emergency_steer_rate` read in `__init__` and `read_params()`), see "Other" in section 9. Since the 2026-09 merge the gateway block also fires on the frame MADS is being turned on (`self.enabled or ...check_contains(ET.ENABLE)`), so an LKAS press or UEM engagement during an override no longer gives one active frame, and a `KeyError` fallback treats a `SubMaster` without `carStateSP` (upstream's MADS tests) as "no gateway" | `ef4f29432` `4932aa73c` `35622a994` `2cfcd3c6a`, 2026-09 merge, 2026-09-30 | `test_mads_gateway_pause.py` (41 tests) |
| sp | `openpilot/sunnypilot/mads/state.py` (added in the 2026-09 merge) | DISABLED branch: an ENABLE that arrives with `silentLkasDisable` goes to `paused`, not `enabled`/`overriding`. Only the gateway block can raise `silentLkasDisable` while MADS is disabled | 2026-09 merge | `test_mads_gateway_pause.py::test_turning_mads_on_during_an_override_starts_paused` |
| sp | `openpilot/selfdrive/selfdrived/selfdrived.py` | `'carStateSP'` added to upstream's SubMaster, for MADS. Since 2026-10-01 also `self.eps_latch_alert = EpsLatchAlert()` and the call in `update_events()` that adds its events to `events_sp` | `ef4f29432`, 2026-10-01 | `test_mads_gateway_pause.py::test_selfdrived_subscribes_the_gateway_state`, `test_eps_latch_alert.py::TestSelfdrivedWiring` |
| sp | `openpilot/sunnypilot/selfdrive/selfdrived/eps_latch_alert.py` (new, 2026-10-01) | `EpsLatchAlert`, `LATCH_CONFIRM_FRAMES`, `LATCH_CLEAR_FRAMES`, `ANNOUNCE_FRAMES`, `REMINDER_PERIOD_FRAMES`, `REMINDER_FRAMES`: the "restart the car" alert for `latchedUntilKeyOff`, see section 9 | 2026-10-01 | `test_eps_latch_alert.py` (18 tests) |
| sp | `openpilot/sunnypilot/selfdrive/selfdrived/events.py`, `openpilot/cereal/custom.capnp` | `OnroadEventSP.EventName` `lkasGatewayEpsLatched @26`, `lkasGatewayEpsLatchedReminder @27`, and their `ET.WARNING`-only `EVENTS_SP` entries | 2026-10-01 | `test_eps_latch_alert.py::TestEpsLatchAlertDefinitions` |
| sp | `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | the capnp ↔ dataclass seam for `lateralControl` and `linbusGateway` | `d11d2c9a8` `2dd8827d5` | itself |
| sp | `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` | two tests: `test_a_stale_torque_still_confirms_nothing` and `test_driver_torque_stale_comes_after_the_road_edges` (the rest is area C) | `10e088a2d`, 2026-09 merge | itself |
| sp | `openpilot/sunnypilot/mads/tests/test_mads_gateway_pause.py` | the gateway pause and its resume, every brake mode, the brake and regen guard, the emergency takeover beside an override, no board no pause, `selfdrived`'s subscription, the enable-frame cases, and (`TestFastWheelSetting`) the takeover's switch and threshold | `2cfcd3c6a`, 2026-09 merge, 2026-09-30 | itself |
| sp | `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_latcontrol_gateway_hold.py` (added in the 2026-09 merge) | 300 frames on `HONDA_ACCORD_9G_AU` with Lateral Jerk on and the board present but not actuating: `pid.i` stays 0.0 in both torque controllers. Control case: with no gateway the same run winds `abs(i)` above 1e-3, as upstream does | 2026-09 merge | itself |

`openpilot/selfdrive/car/card.py` has no area-B hunk. It already published `carStateSP` before the fork.
Its fork additions (`stage_board_firmware`, `write_board_firmware`, `log_flash_trace`) are
area A.

**Markers.** In opendbc, four area-B hunks carry `# FORK(HONDA_ELESYS):`: the brake-release
ceiling (`carcontroller.py`), the `pt_msgs` registration (`carstate.py`),
`create_steering_control` (`hondacan.py`) and `steerAtStandstill` (`interface.py`), and the
`0x500` exemption in `safety/tests/common.py` carries `FORK(LKAS-GATEWAY)`.
`carstate_ext.py`, `structs.py`, the `0x500` block in `carcontroller.py`,
`create_sp_hud_status`, the `0x500` entries in `honda.h` and `_sunnypilot_linbus_gw.dbc` are
unmarked, so use the greps in section 14.3, not the marker. In sunnypilot, every area-B hunk
the 2026-09 merge touched carries `FORK(LKAS-GATEWAY)` (13 lines: `controlsd.py`,
`selfdrived.py`, both `modeld.py`, `desire_helper.py`'s `update()`, `mads.py`, `state.py`,
`latcontrol_torque_ext_base.py`). The older ones (`latcontrol.py`, both torque controllers,
`controlsd_ext.py`, `helpers.py`, `custom.capnp`) have prose comments only; the phrase
"LIN-bus gateway" is their common anchor, but not all of them contain it (see the grep in
section 14.3).

---

## 3. The command: `0x0E4 STEERING_CONTROL`

### 3.1 The domain openpilot works in

openpilot does not work in serial counts. It stays in Honda's 2560 CAN domain:

| stage | value |
|---|---|
| `lateralParams.torqueBP/torqueV` | `[[0, 2560], [0, 2560]]`. The car falls through to the generic `else` in `interface.py`; the fork does not set it |
| `CarControllerParams.STEER_MAX` | 2560 |
| `STEER_DELTA_UP` / `STEER_DELTA_DOWN` | 3 and 3 (upstream, all Hondas), so 0.03 of full scale per 10 ms frame |
| `apply_torque` | `int(np.interp(-limited_torque * STEER_MAX, ...))`. **Positive means turn right**, the same sense as the camera's serial `APPLY_STEER` |
| on the wire | `0x0E4` bytes 0–1, int16 big-endian, `STEER_TORQUE` |

The board converts. With byte 2 bit 2 clear it computes
`t = op_torque * GW_LIN_AUTHORITY / GW_OP_FULL_SCALE` (2560), then multiplies by the soft-start
ramp (`t * soft / GW_SOFTSTART_FRAMES`, 0 to 1 over 2 s after each engagement), clamps to
`±GW_LIN_AUTHORITY`, and finally clips by the lower of two ceilings: `ceil_pct` (the
driver-torque fade, section 9) and `GW_BLINK_CEIL_PCT` = 50 % while an indicator is on
(`gw_active.c` lines 1981–1995 at `d995bc9`). So openpilot's full scale of ±1.0 equals the
board's authority in steady state, outside the soft start, the driver-torque fade and the
blinker ceiling, all of which can only lower it. In that steady state openpilot's saturation
flag means the same thing at every step of the authority ladder, with no change on this side.
The history of how this came about (the reference boards took only the low byte and wrapped)
is in `docs/SP_GATEWAY_FIRMWARE.md` §0.

**`SERIAL_DOMAIN`, byte 2 bit 2, must stay 0.** If it is set, the board takes `STEER_TORQUE` as
serial counts at unity gain. An openpilot still in the 2560 domain then pins the board at full
authority from the first frame. Setting the bit is only safe in the same commit that changes
`lateralParams` to `[[0, 239], [0, 239]]`. In the DBC the bit is held at zero because it sits
inside `SET_ME_X00_3`. Integration test §11 asserts it is clear.

### 3.2 Byte 2

`_steering_control_e.dbc` splits the byte that `_steering_control_a.dbc` calls `SET_ME_X00`
(7 bits):

| bit | DBC signal | openpilot sends | board at `d995bc9` |
|---|---|---|---|
| 7 | `STEER_TORQUE_REQUEST` | `CC.latActive` | the request. A level, not an edge |
| 6 | `SET_ME_X00` (`WIGGLE_DISABLE`) | 0 | the board owns the LSB dither |
| 5 | `LDW_RIGHT` | `hud_control.rightLaneDepart` | copied to serial camera→EPS byte 2 bit 5 (`op_ldw`, since board `8340c1d`) |
| 4 | `LDW_LEFT` | `hud_control.leftLaneDepart` | copied to serial byte 2 bit 4 |
| 3 | `SET_ME_X00_3` (`TX_RAW_SERIAL`) | 0 | diagnostics |
| 2 | `SET_ME_X00_3` (`SERIAL_DOMAIN`) | **0** | domain declaration, see above |
| 1 | `SET_ME_X00_3` | 0 | reserved |
| 0 | `SET_ME_X00_3` (`BLEND_DISABLE`) | 0 | the board keeps the driver-torque blend |

The LDW bits are sent whether or not lateral is active. A departure warning is a warning, not
a request. `create_steering_control()` fills `LDW_LEFT`/`LDW_RIGHT` only when
`serial_gateway=True`. The keys themselves must be gated, because no other Honda DBC defines
those signals. What the EPS does with serial byte 2 bits 5:4 is not known: the stock camera
was never seen setting them (`S:/Software/EPS-LKAS/CLAUDE.md`, LKAS→EPS table).

**Re-apply:** keep the three keyword parameters on `create_steering_control` with their
`False` defaults, so every upstream caller is unchanged. In `CarController.update()`, pass
`serial_gateway = self.CP.carFingerprint in HONDA_ELESYS` and the two `hud_control` flags.

### 3.3 Limits, as openpilot sees them

| limit | value | where |
|---|---|---|
| panda | only checks that bytes 0–1 are zero while neither `controls_allowed` nor `controls_allowed_lateral` (the MADS lateral flag) is set. No magnitude, rate or driver-torque check | `honda.h` `honda_tx_hook`, unchanged upstream code |
| openpilot rate | 0.03 × authority per frame: 4.8 serial counts at 160. The stock camera's p99 is 5 | `STEER_DELTA_*` |
| board authority | `GW_LIN_AUTHORITY` is a compile-time value. The bundled Stage 10 app-slot build uses 160 (`tools/flash-incar-stage10-appslot.bat` refuses to flash anything else). The CMake default is 80 | board `CMakeLists.txt` `GW_AUTHORITY` |
| EPS ceiling | 160 serial counts. Six consecutive frames above it and the EPS latches for the key cycle | `docs/CHANGELOG_SERIAL_STEERING.md`, "Things about this car worth not rediscovering" |
| EPS acknowledgement floor | about 50.4 km/h measured. The board does not ask below 51.50 km/h (CMake `GW_EPS_FLOOR_CPH` = 5150, `GW_EPS_ACK_CPH` in the code) and holds down to 51.00 (`GW_EPS_KEEP_CPH` = 5100) | board |
| standstill | the board holds zero below `GW_STANDSTILL_CPH` = 500 (5.00 km/h) | board |
| `0x0E4` timeout on the board | `GW_OP_TIMEOUT_MS` = 100 | board |

`MAX_TORQUE` in `0x500` is **not** a limit today. See [section 4](#4-0x500-sp_hud_status-v3).

### 3.4 `steerAtStandstill` (interface.py, `bb0fe222`)

`ret.steerAtStandstill = True` for HONDA_ELESYS. Without it, `controlsd` drops `latActive`, and
with it the `0x0E4` request bit, at every stop. When the board had no fresh `0x500` it read
that as "not armed" and blanked the lane graphic. Nothing steers at a stop, because the board
holds zero below 5 km/h. The change only keeps the request alive. Since `43a98b9d` the graphic
also follows `LAT_READY`, so this matters mainly when `0x500` is not fresh. Keep both.

**Re-apply:** inside the `if candidate in HONDA_ELESYS:` block of `_get_params`, next to the
area-C `steerActuatorDelay` line.

### 3.5 Release on the brake (carcontroller.py, `02e7fd71`)

```python
BRAKE_RELEASE_FRAMES = 20
def brake_release_scale(brake_pressed: bool, frames: int) -> tuple[float, int]
```

This is applied after the rate limit, as a ceiling on the magnitude:
`limited_torque = clip(limited_torque, -c, c)`. It is skipped at `c == 1.0`, so a frame with no
braking is bit-identical to upstream. It can only shrink the command, and it cannot latch: the
frame count is cleared, not decayed, on the first frame without the brake. The clipped value
is stored as `last_torque`, so recovery goes through `STEER_DELTA_UP`.

Why: the stock camera drops `LKAS_ON` within about 20 frames of a brake press (routes
`000000c8`/`c9`). This imitates stock. It does not avoid a fault: the EPS tolerated brake plus
torque with error state 0 (board `docs/SP-PROTOCOL-V3.md` §0).

The step is 2560/20 = 128 CAN counts per frame. That is 8 serial counts at authority 160, still
under the 10 per frame that SP-PROTOCOL-V3 §3 allows. The code comment says 4 counts, which
was true at authority 80. Integration test §12 pins the ramp, the linearity, the zero at 20
frames, the recovery and the lack of a latch.

**Re-apply:** in the upstream `CarController.update()`, straight after
`limited_torque = rate_limit(...)` and before `self.last_torque = limited_torque`, gate on
HONDA_ELESYS. Keep `self.brake_release_frames = 0` in `__init__`.

---

## 4. `0x500 SP_HUD_STATUS` v3

This is a sunnypilot-only frame. Address `0x500` was chosen because it is unused on every bus
of this car. It is sent at 10 Hz, inside the `self.frame % 10 == 0` block of `carcontroller.py`,
on **`self.CAN.pt`**, DLC 8, big-endian. It is built by `hondacan.create_sp_hud_status()`. On
HONDA_ELESYS it takes the place of the `create_lkas_hud()` call: openpilot never sends `0x33D`
on this car.

`CHECKSUM` and `COUNTER` are filled by the packer. They get the Honda 4-bit checksum and 2-bit
counter because they carry exactly those names in a `honda_` DBC. The board (`sp_hud_rx` in
`sp_hud.c`) rejects a frame with a bad checksum, a version outside
`SP_HUD_VERSION_MIN`..`SP_HUD_VERSION_MAX`, or a counter equal to the previous frame's. It
treats the channel as stale 300 ms after the last good frame (`SP_HUD_STALE_MS`).

**`PROTOCOL_VERSION` is a hard gate.** The board rejects any version above its maximum (3 at
`d995bc9`, first accepted by board firmware `75aa91ee`). A rejected `0x500` silently takes down
the lane graphic's `LAT_READY` input, the integrator guard and the HUD alerts together. Never
raise `SP_HUD_PROTOCOL_VERSION` ahead of the flashed firmware.

### 4.1 Field by field

"Board use" is read from `gw_active.c`, `sp_hud.c` and `hud.c` at `d995bc9`, for the Stage 10
image (split CAN, `GW_HUD_MERGE=ON`, so `hud_own` is true and the board builds its own `0x33D`).
`sp_hud_merge_lkas()` is the older path that edits the camera's own `0x33D` in flight. It runs
only in the `gw_active` dry-run path and in the Stage 2a gateway (`gw_split.c`), never in the
Stage 10 live image.

| byte.bit | signal | openpilot value | board use |
|---|---|---|---|
| 0.7:4 | `PROTOCOL_VERSION` | `SP_HUD_PROTOCOL_VERSION = 3` | accepts 1–3; reject otherwise |
| 0.3 | `OP_ENABLED` | `CC.enabled` | parsed, not read |
| 0.2 | `LAT_ACTIVE` | `CC.latActive` | parsed, not read |
| 0.1 | `LONG_ACTIVE` | `CC.longActive` | parsed, not read |
| 0.0 | `STEERING_REQUIRED` | `alert_steer_required` (openpilot's own nag, from `process_hud_alert`) | the board's `0x33D` alert logic (`hud_gw`: passive nag on dashed, escalation on solid). Also read by `sp_hud_merge_lkas()` |
| 1.7 / 1.6 | `LDW_LEFT` / `LDW_RIGHT` | `hud_control.left/rightLaneDepart` | the `hud_gw` escalation trigger, ORed with `LDW_ACTIVE` |
| 1.5 | `FCW` | `alert_fcw` | read only by `sp_hud_merge_lkas()` (the `gw_active` dry-run path and the Stage 2a gateway). Never read by the Stage 10 live alert, deliberately (`gw_active.c` S6-2 comment) |
| 1.4 / 1.3 | `SOLID_LANES` / `DASHED_LANES` | `CC.latActive` / `lanesVisible and not latActive` | read only by `sp_hud_merge_lkas()` when it is given no lane state of its own (`lanes < 0`): the dry-run path and Stage 2a. The live graphic comes from `LAT_READY` and the board's own state |
| 1.2 | `LEAD_VISIBLE` | `hud_control.leadVisible` | parsed, not read |
| 1.1:0 | `ALERT_LEVEL` | 3 FCW, 2 steer-required or LDW, 1 enabled, 0 none | parsed, not read |
| 2 | `SET_SPEED` | `setSpeed` in **km/h** (never display units), 0 when not visible, clipped to 0–255 | parsed, not read |
| 3 | `INTEGRATOR` | `int8(clip(round(CC_SP.lateralControl.integrator × 100), ±127))` | first-engagement guard: if the absolute value exceeds 15 (`GW_INTEG_MAX`) before the first engagement, with a fresh v2+ frame, it sets `INH_ENGAGE_GUARD` (`0x704` inhibit bit `0x40`) and `0x70B REASON` 11 (`GW_RSN_INTEGRATOR`) |
| 4.0 | `OP_SATURATED` | `CC_SP.lateralControl.saturated` | `hud_gw` escalation trigger while solid |
| 4.1 | `INTEGRATOR_FROZEN` | `CC_SP.lateralControl.integratorFrozen` | parsed, not read. It is the acknowledgement that openpilot runs the v2 hold |
| 5.0 | `WANT_CONTROL` | `CC.latActive` | parsed, not read. `0x0E4` bit 7 remains what actually asks |
| 5.1 | **`LAT_READY`** | **`CC_SP.mads.enabled or CC.latActive`** | **the lane graphic**, see [section 10](#10-the-lane-graphic) |
| 5.4:2 | `OP_STATE` | see below | parsed, not read |
| 5.5 | `RELEASE_BRAKE` | `CC.latActive and brakePressed` | parsed, not read. The matching ramp is §3.5 |
| 5.6 | `RELEASE_DRIVER` | `CC.latActive and steeringPressed` | parsed, not read. **Advisory**: openpilot has no matching ramp |
| 5.7 | `LDW_ACTIVE` | either departure | `hud_gw` trigger |
| 6 | `MAX_TORQUE` | `SP_HUD_MAX_TORQUE = 0` ("use your own authority") | **reported only**. It is folded into `0x70B AUTHORITY` (negotiating downward only, `gw_active.c` line 2435) and never into the command, which clamps to `GW_LIN_AUTHORITY`. A non-zero value would make telemetry show a cap the board is not applying |
| 7.5:4 | `COUNTER` | packer | validation |
| 7.3:0 | `CHECKSUM` | packer | validation |

`OP_STATE` is openpilot's own lateral state, not the board's:

```python
if steerFaultTemporary or steerFaultPermanent:  FAULTED (5)
elif not CC.latActive:                          READY (1) if CC.enabled or lat_ready else OFF (0)
elif release_brake or release_driver:           WITHDRAWING (4)
elif apply_torque != 0:                         ACTIVE (3)
else:                                           REQUESTING (2)
```

**`LAT_READY` history (`43a98b9d`).** It used to be `steering_available or CC.latActive`, where
`steering_available` means cruise main is on and the car is above `minSteerSpeed`. That is true
with MADS off, so the board drew dashed lanes whenever the car was moving. It is now
`mads.enabled`, which covers the states `paused`, `enabled`, `softDisabling` and `overriding`.
The `paused` state is the one that matters: MADS pauses on the board's own override hand-back
([section 9](#9-mads-and-the-boards-hand-back)), and the graphic has to stay dashed through
that pause rather than blank. `CC_SP.mads.enabled` is filled by upstream `controlsd_ext` from
`selfdriveStateSP.mads.enabled`.

**Re-apply:** in the `frame % 10` HUD block of `CarController.update()`, wrap the upstream
`create_lkas_hud` call in `if self.CP.carFingerprint not in HONDA_ELESYS:` and put the ELESYS
`else:` branch beside it. Send on `self.CAN.pt`. Keep the `steering_available` line: other cars
still use it.

### 4.2 Where `CC_SP.lateralControl` comes from

The CarController cannot see `controlsState`, so the integrator travels on `carControlSP`:

1. `controlsd.py` passes `lac_log` and `self.LaC` into `self.run_ext(self.sm, self.pm, lac_log, self.LaC)`.
2. `controlsd_ext.state_control_ext()` copies `lac_log.i` and `lac_log.saturated`, when the
   log's schema has an `i` field (torque/PID). It also copies `LaC.integrator_frozen`. The angle
   controller reports zero.
3. `cereal CarControlSP.lateralControl @5 :LateralControl { integrator @0 :Float32;
   saturated @1 :Bool; integratorFrozen @2 :Bool; }`
4. `card` converts it with `helpers.convert_carControlSP()`. **Every nested struct has to be
   rebuilt there by hand.** When `lateralControl` was first added it was not, so it arrived as a
   dict, `CC_SP.lateralControl.integrator` raised every frame, and card died (routes `b5`–`b8`,
   with ACC/CMBS faults because card also services the radar side of the split bus). Fixed in
   `d11d2c9a8`. `test_car_control_sp_seam.py` checks every nested struct in the schema against
   the converter, so the next one added cannot repeat this.
5. opendbc's `structs.CarControlSP.LateralControl` dataclass. Use attribute access. Board
   `SP-PROTOCOL-V3.md` §1.3 says "use item access". That is wrong now.

### 4.3 Panda

`0x500` is allowed on bus 0 with `check_relay = false` in `HONDA_N_ELESYS_STANDDOWN_TX_MSGS` and
`HONDA_N_ELESYS_STANDDOWN_INTERCEPTOR_TX_MSGS`. Without that entry the panda drops the frame
silently.

**Caveat, latent:** those two lists are only selected when `ELESYS_SCM_STANDDOWN` is set, and
`interface.py` sets that flag only when `ret.openpilotLongitudinalControl` is true. With
openpilot longitudinal off, the plain `HONDA_N_TX_MSGS` list applies and it has no `0x500`. The
frame would then be blocked, `BUILD_SP_FRESH` would stay 0, and `LAT_READY` would never reach
the board. This car always runs openpilot longitudinal today. If that changes, add `0x500` to
the non-standdown path for this platform too. (Found by reading `honda_nidec_init`; not
tested.)

---

## 5. Board telemetry openpilot decodes

The board transmits `0x700`–`0x70F` on the car bus. openpilot's DBC declares only what it
reads, plus a few flags. The board's `dbc/eps-lkas-gw.dbc` is the complete layout. Load it in
Cabana beside the Honda DBC to see everything.

| ID | name | rate | decoded into | signals openpilot reads |
|---|---|---|---|---|
| `0x700` | `EPS_LIN_RAW` | 100 Hz (one per EPS serial frame) | `CarState.steeringTorque` / `steeringPressed` substitution | `STEER_TORQUE` (serial counts, scale 2, **left negative**), `CHECKSUM_OK`. `EPS_LKAS_ON` and `GW_COUNTER` are declared but not read |
| `0x701` | `LKAS_LIN_RAW` | | not decoded | |
| `0x702`, `0x703` | `GW_STATUS`, `GW_STEER_CMD` | | not decoded | |
| `0x704` | `GW_ACTIVE` | 10 Hz. It lost 27–35 % of frames on early firmware (blackouts up to 94.5 s, `HANDOFF.md` "Known-broken: `0x704` telemetry transmit"); later measured at 10.0 Hz with none lost | `linbusGateway.engaged/dryRun/valid/actuating/present` | `ENGAGED` (byte 6 bit 0), `DRY_RUN` (byte 5 bit 7). The rest (`CMD_APPLY_STEER`, `DRIVER_TORQUE`, the `INH_*` bits, `AUTHORITY`) is in the board DBC only |
| `0x705`, `0x706` | `GW_CAN_DIAG`, `GW_SPLIT_STATUS` | | not decoded | |
| `0x707` | `GW_VERSION` | at mode entry, once a minute, and on a domain change | `linbusGateway.fwGitHash`, `fwValid` (area A) | `GIT_HASH`. The other flags are declared, not decoded |
| `0x708` | `GW_LIN_STATUS` | 10 Hz, after `0x704` | **not decoded** | the EPS's latest checksum-good status frame plus the camera's request. Useful in Cabana |
| `0x709`, `0x70A` | `GW_LIN_HEALTH`, `GW_CAM_ORIGIN` | | not decoded | |
| `0x70B` | `GW_STEER_GRANT` | 10 Hz, since board `75aa91ee` | `linbusGateway.grant*` and the fields below | all signals except `GRANT_COUNTER`: `STATE`, `REASON`, `AUTHORITY`, `EPS_ACK`, `EPS_LATCHED`, `EPS_ERROR_STATE`, `EPS_FRESH`, `CAM_LKAS_ON`, `APPLIED`, `MOTOR_TORQUE`, `RETRY_IN`. `GRANT_COUNTER` is declared, and named that way only so the parser does not enforce Honda counter continuity |
| `0x70C`, `0x70D`, `0x70E` | `GW_TX_EPS`, `GW_CAM_HUD`, `GW_PROBE` | | not decoded | |
| `0x70F` | `GW_BUILD` | beside `0x707`, since board `625b782e` | `linbusGateway.fw*`, `boardUid`, `fwBuildValid` (area A) | `BUILD_DIRTY`, `BUILD_APP_SLOT`, `BUILD_BOOTLOADER`, `BUILD_READONLY`, `BOARD_UID`; `BUILD_COUNTER` only as the "ever received" timestamp key. `BUILD_INCAR_TEST`, `EPS_FLOOR_CPH`, `LIN_MAX_ABS` and **`BUILD_SP_FRESH` (byte 0 bit 5) are declared but not decoded**: the board's `tools/route_flatten.py` reads `BUILD_SP_FRESH` from the raw CAN in a route |

### 5.1 Parser registration (carstate.py)

For HONDA_ELESYS, `get_can_parsers()` passes

```python
pt_msgs = [("GW_ACTIVE", float("nan")), ("GW_STEER_GRANT", float("nan")),
           ("EPS_LIN_RAW", float("nan")), ("GW_VERSION", float("nan")),
           ("GW_BUILD", float("nan"))] if CP.carFingerprint in HONDA_ELESYS else []
```

to the `Bus.pt` `CANParser`. Both halves of that are load-bearing:

* **`float("nan")` makes the message liveness-exempt** (`ignore_alive`). A message registered
  lazily through `cp.vl[...]` learns its own rate and times out at 10 × its period. That would
  drop `canValid` and stop openpilot engaging whenever the board is unplugged, sitting in its
  CAN bootloader, too old to send `0x70B`/`0x70F`, or (on early firmware) dropping `0x704`.
* **Registering before the first `update()`** means the first frame is not dropped.

**No gateway frame may have a signal named `COUNTER` or `CHECKSUM`.** In a `honda_` DBC those
names make the parser enforce Honda counter continuity and a Honda checksum, which the board
does not compute for these frames, and every frame would be dropped. That is why the counters
are named `GRANT_COUNTER`, `GW_COUNTER`, `VER_COUNTER` and `BUILD_COUNTER`. `0x500` is the
exception because openpilot sends it and wants the packer to fill them. Integration test §13
asserts the naming.

### 5.2 Decode rules (carstate_ext.py)

All five run only for HONDA_ELESYS, from `CarStateExt.update(self, ret, ret_sp, can_parsers)`.
The `ret_sp` argument is itself a fork change to the upstream signature.

| method | frame | staleness | output rules |
|---|---|---|---|
| `_update_linbus_gateway` | `0x704` | `LINBUS_GW_STALE_FRAMES = 50` carstate frames (500 ms), counted off `ts_nanos["GW_ACTIVE"]["ENGAGED"]` | `present = True`; `actuating = engaged and not dryRun and valid`. In a dry run the board still sets `ENGAGED`, so `ENGAGED` alone would claim control that does not exist |
| `_update_linbus_grant` | `0x70B` | `LINBUS_GRANT_STALE_FRAMES = 50` | `granted = valid and STATE in GRANT_STATES_STEERING` (3 INTRO, 4 ACTIVE, 5 LIMITED). **Absence is never permission.** A stale frame reports zeros. `latchedUntilKeyOff = valid and RETRY_IN == GRANT_RETRY_KEY_CYCLE (255)`, not sticky, and `EPS_LATCHED` is deliberately **not** ORed in (it is a 3 s or 60 s timed hold). The log key `(valid, STATE, REASON)` is updated on every change, but `carlog.warning("LIN-bus gateway not steering: STATE=… REASON=… RETRY_IN=… EPS_ERR=…")` is emitted only when the change lands in a valid, non-steering state. A change into INTRO/ACTIVE/LIMITED, or to invalid, is silent |
| `_update_linbus_firmware` | `0x707`, `0x70F` | **none**: latches on first sight | area A. `int()` around the values is needed because pycapnp refuses a float for UInt32 |
| `_update_driver_torque_validity` | `0x18F` + `0x700` | see section 8 | |
| `_eps_lin_driver_torque_valid` | `0x700` | `EPS_LIN_RAW_STALE_FRAMES = 25`, **counted only on frames where `0x18F` is latched** (section 8.2) | fresh and `CHECKSUM_OK` |

Staleness is counted in frames because `CarState.update()` is not handed a clock.
`ts_nanos == 0` is the parser's "never received" sentinel and always reads as stale.

`0x70B` value tables (`STATE` 0–7, `REASON` 0–15) are in `_sunnypilot_linbus_gw.dbc` `VAL_ 1803`
and in `custom.capnp`. Two traps: `REASON` 6 "brake" is defined but never emitted (a brake-time
withdrawal arrives as 1 "no request"), and `APPLIED` is quantised to 2 counts, so ±1 reads back
as 0. Details are in `docs/SP_HUD_STATUS.md`, "What sunnypilot reads: `0x70B`".

---

## 6. cereal and dataclass fields

`openpilot/cereal/custom.capnp` and opendbc `structs.py` must agree on **names**, exactly. `card`
publishes the dataclass through `convert_to_capnp()`, which does
`custom.CarStateSP.new_message(**asdictref(struct))`: the fields are matched by name.
Declaration order in `structs.py` does not matter. The fork already differs at the top level
(the dataclass declares `speedLimit`, `driverTorqueStale`, `linbusGateway`; capnp has
`linbusGateway @1`, `driverTorqueStale @2`). Ordinals exist only in `custom.capnp`. The
comments in `structs.py` that say "names and order must stay in lockstep" overstate it (section
13); keeping the same order is a readability convention only.

**`CarControlSP`** (`@0xa5cd762cd951a455`)

| field | type | meaning |
|---|---|---|
| `lateralControl @5` | `LateralControl` | |
| `  integrator @0` | Float32 | `torqueState.i` |
| `  saturated @1` | Bool | `torqueState.saturated` |
| `  integratorFrozen @2` | Bool | the gateway hold specifically (`LaC.integrator_frozen`) |

**`CarStateSP`** (`@0xb86e6369214c01c8`)

| field | type | meaning | readers |
|---|---|---|---|
| `linbusGateway @1` | `LinbusGateway` | | |
| `driverTorqueStale @2` | Bool | nothing may infer driver intent from `steeringTorque` this frame | `modeld` / `modeld_v2` → `DesireHelper` |

**`CarStateSP.LinbusGateway`**

| @ | field | type | source | readers |
|---|---|---|---|---|
| 0 | `engaged` | Bool | `0x704 ENGAGED` | |
| 1 | `dryRun` | Bool | `0x704 DRY_RUN` | |
| 2 | `valid` | Bool | `0x704` within 500 ms | |
| 3 | `actuating` | Bool | `engaged and not dryRun and valid` | `controlsd` → `LaC.set_linbus_gateway` |
| 4 | `present` | Bool | this platform has a board: always True on ELESYS, even with nothing plugged in; False elsewhere | `controlsd`, `mads`, `selfdrived` (latch alert) |
| 5 | `grantValid` | Bool | `0x70B` within 500 ms | `mads`, `selfdrived` (latch alert) |
| 6 | `grantState` | UInt8 | `STATE` | |
| 7 | `grantReason` | UInt8 | `REASON` | `mads` (== 4) |
| 8 | `granted` | Bool | `grantValid and STATE in {3,4,5}` | `mads` |
| 9 | `authority` | UInt8 | `AUTHORITY` (as reported) | |
| 10 | `epsAck` | Bool | `EPS_ACK` | |
| 11 | `epsLatched` | Bool | `EPS_LATCHED`, the refusal hold, **not** a key-cycle latch | |
| 12 | `epsErrorState` | UInt8 | `EPS_ERROR_STATE` | |
| 13 | `epsFresh` | Bool | `EPS_FRESH` | |
| 14 | `camLkasOn` | Bool | `CAM_LKAS_ON` | |
| 15 | `applied` | Int16 | `APPLIED` (quantised to 2) | |
| 16 | `motorTorque` | Int16 | `MOTOR_TORQUE` | |
| 17 | `retryIn` | UInt8 | `RETRY_IN` | |
| 18 | `latchedUntilKeyOff` | Bool | `RETRY_IN == 255` | `selfdrived` → `eps_latch_alert.py` (the "restart the car" alert, section 9) |
| 19–26 | `fwValid` … `fwBuildValid` | | `0x707` / `0x70F` | area A (`card`, `board.py`) |

Fields with no reader appear only in the route log (`carStateSP` is logged), and the only
driver-facing surface for them is the `carlog.warning` above. Since 2026-10-01
`latchedUntilKeyOff` has an alert (section 9, "Other: the EPS latch alert"); `grantReason` still
has none.

**Subscriptions added for these fields:** `controlsd` (`carStateSP`), `selfdrived` (`carStateSP`,
not in its `ignore` list, so it is part of `sm.all_checks()`), `modeld` and `modeld_v2`
(`carStateSP`). `card` publishes `carStateSP` every frame with `valid = CS.canValid`, the same as
`carState`, so this adds no new way to fail the checks.

---

## 7. The integrator hold (SP-PROTOCOL v2)

Files: `openpilot/selfdrive/controls/lib/latcontrol.py`, `openpilot/selfdrive/controls/lib/latcontrol_torque.py`,
`openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_v0.py`,
`openpilot/sunnypilot/selfdrive/controls/lib/latcontrol_torque_ext_base.py` and `controlsd.py`. Commits
`176e6c07c` (hold), `39b857567` (carry the trim), `946b5fa21` (the comments in both torque
controllers) and the 2026-09 merge (the extension). `_v0` is the one that runs on this car by default: `controlsd_ext.initialize_lateral_control()`
returns `LatControlTorqueV0` for torque tuning whenever `EnforceTorqueControl` is off, and
when it is on with `TorqueControlTune == 0.0`. The upstream `latcontrol_torque.py` runs only
when `EnforceTorqueControl` is on with another tune.

```python
# controlsd.state_control(), after actuators.curvature is set and before LaC.update():
gw = self.sm['carStateSP'].linbusGateway
self.LaC.set_linbus_gateway(bool(gw.present), bool(gw.actuating))

# both torque controllers, first line of update(), active or not:
linbus_hold = self._linbus_integrator_gate()
...
freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < 5 or linbus_hold

# LatControlTorqueExtBase.update_output_torque(), the extension's second PID update (2026-09 merge):
freeze_integrator = (self._steer_limited_by_safety or CS.steeringPressed or CS.vEgo < 5
                     or getattr(self.lac_torque, "integrator_frozen", False))
```

**The extension.** With upstream's Lateral Jerk controller (`LateralJerkTorqueController`,
`91a53aa16`, off by default) or NNLC on, `LatControlTorqueExt` updates the owning controller's
PID a second time in the same frame, and that update did not know about the hold: the
integrator wound open-loop while the board was not actuating, and `0x500 INTEGRATOR` reported
it. The merge makes that update freeze on the controller's `integrator_frozen`, which the gate
set earlier in the frame and which is always False without a gateway. `pid_log.i` is not
changed. `test_latcontrol_gateway_hold.py` pins it for both torque controllers.

`_linbus_integrator_gate()` behaves as follows:

* If `present` is False it returns False and does nothing. Every other car is bit-identical.
* **While not actuating:** it freezes the integrator, sets `integrator_frozen = True` (echoed as
  `0x500 INTEGRATOR_FROZEN`), and decays `pid.i` by `exp(-dt / LINBUS_I_HOLD_TAU)` with
  `LINBUS_I_HOLD_TAU = 30.0` s.
* **On the frame actuation starts:** it clips `pid.i` to `±LINBUS_I_CARRY_MAX = 0.25` m/s².
* It finds the PID with `getattr(self, "pid", None)`. If upstream renames `self.pid`, the
  freeze still works but the carry and the decay silently stop. Upstream master still calls
  it `self.pid` in both controllers.

Why it carries rather than resets: this car has a real, one-signed steering trim. On routes
`d3`/`d4`, `i` was positive in all 14 engagements, settling at +0.04 to +0.20. Starting from zero
took 14 s to re-learn, and the board was actuating for only 48–65 % of lateral-active time. The
first version reset the PID on takeover. `docs/SP_HUD_STATUS.md`, board `SP-PROTOCOL-V3.md`
§1.3 and the `LatControl.__init__` comment still describe that (section 13).

`latActive` is deliberately not gated on actuation. The board engages on the request bit, so
if openpilot waited for the board, neither side would start.

**Tests.** `test_latcontrol_gateway_hold.py` (added in the 2026-09 merge) runs 300 frames with
the board present and not actuating and checks that `pid.i` stays 0.0 in both torque
controllers, with a no-gateway control case that winds. It covers the freeze only: the carry,
the decay and the `integrator_frozen` echo have no test. Integration §7 only checks that the
`0x500` bytes carry whatever `CC_SP.lateralControl` holds. A replay probe after the merge ran
`LatControlTorqueV0` with a gateway and saw the freeze, the `exp(-dt/30)` decay and the 0.25
clip on the takeover frame, and bit-identical output with `present` False.

---

## 8. Driver torque: the `0x18F` latch, the mirror, and the stale guard

### 8.1 The problem

While the EPS is under LKAS control, `0x18F STEER_TORQUE_SENSOR` holds the value it had when
LKAS engaged. The frame keeps arriving at 100 Hz with a good counter and checksum, and
`canValid` stays true. Routes `dd`/`de`/`df`: frozen for up to 946 s while the wheel moved,
41–69 % of each drive. This is the EPS's own behaviour: route `0000001f`, recorded before the
board existed, shows it during a stock engagement. The consequences were a frozen integrator,
wrong driver-monitoring input, and lane changes that fired instantly in one direction and
never in the other.

### 8.2 The rule (`_update_driver_torque_validity`, opendbc `23dce590` + `2cc16a02`)

```
held    = frames STEER_TORQUE_SENSOR has been bit-identical (capped at STEER_TORQUE_STALE_FRAMES = 25)
latched = STEER_STATUS.STEER_CONTROL_ACTIVE and held >= 25          # both, not either
if latched and _eps_lin_driver_torque_valid(cp):                    # short-circuit: called ONLY when latched
    steeringTorque  = SERIAL_TORQUE_TO_CAN * EPS_LIN_RAW.STEER_TORQUE     # -64.5
    steeringPressed = abs(steeringTorque) > STEER_THRESHOLD.get(fingerprint, 1200)   # 600 on this car (area C)
    latched = False
driverTorqueStale = latched
if latched: steeringPressed = False

_eps_lin_driver_torque_valid(cp):
    ts = ts_nanos["EPS_LIN_RAW"]["STEER_TORQUE"]
    if ts != self._eps_lin_ts: self._eps_lin_ts = ts; self._eps_lin_stale = 0
    else:                      self._eps_lin_stale = min(self._eps_lin_stale + 1, 25)
    return self._eps_lin_stale < 25 and ts != 0 and EPS_LIN_RAW.CHECKSUM_OK
```

* **Both conditions are required.** `STEER_CONTROL_ACTIVE` alone would throw away good data on
  any EPS that keeps reporting. A stale value alone would fire when a driver genuinely holds a
  steady torque with LKAS off.
* **`-64.5`**: least squares over the 30,482 samples from `dd`/`de`/`df` where both signals
  were live. R² 0.9991, residual RMS 134 CAN counts. The sign flips because the serial value is
  left-negative and `steeringTorque` is left-positive. Converting into the CAN domain keeps
  `STEER_THRESHOLD`, torqued, driver monitoring and the nudge logic unchanged.
* **`steeringPressed` is forced False, not True,** when there is no usable signal. Every
  consumer treats True as "the driver is doing something", and acting on a 15-minute-old sample
  is worse than acting on none.
* `STEER_CONTROL_ACTIVE` is at **bit 32** in `_steering_control_e.dbc`. `_steering_control_a.dbc`,
  which this fragment was copied from, has it at bit 35. Commit `2f19864a` moved it without an
  explanation. Integration test §14 depends on it.

**Latent issue, found by reading the code, not by a test or a route.** Because of the
short-circuit, `_eps_lin_driver_torque_valid()` runs only on latched frames. Its frame count
therefore advances only on latched frames, and its timestamp is compared with the one it saw
on the previous *latched* frame. If the board keeps talking through an unlatched stretch and
then goes silent before the next latch, the first latched frame sees a new timestamp, resets
the count to 0, and substitutes the board's last torque for up to 24 frames (about 240 ms).
That contradicts the function's own docstring ("A board that stops talking must read as no
substitute"). Integration §14b does not catch it, because it runs latched frames back to
back. The fix is to call `_eps_lin_driver_torque_valid(cp)` unconditionally every frame, keep
the result, and AND it with `latched` afterwards. Add a §14b case: live board, unlatched frames,
board silent, then latch.

### 8.3 Consumers

* `desire_helper.DesireHelper.update(carstate, lateral_active, lane_change_prob,
  left_edge_detected=False, right_edge_detected=False, driver_torque_stale=False)`.
  `torque_applied` requires `not driver_torque_stale`. It fails closed: while stale, no nudge
  can confirm a lane change. The timer-based auto lane change still works. The `NUDGE_FIRM` /
  `NUDGE_HOLD_FRAMES` rule applied after it is area C.
* `modeld.py` and `modeld_v2/modeld.py` pass `driver_torque_stale=sm['carStateSP'].driverTorqueStale`
  **by keyword**, after upstream's `left_edge, right_edge`. Before the 2026-09 merge the fork
  passed it as the fourth positional argument, which is where upstream put
  `left_edge_detected`; a positional call there mixes the two up with no error.

With the mirror in place, `driverTorqueStale` should be true only when the board is silent or
its frame fails its checksum.

**Tests:** integration §14 and §14b (the latch rule, the substitution, the sign, `CHECKSUM_OK=0`,
a board going silent mid-engagement, a live sensor winning) and §15, which skips when run
from opendbc alone because `desire_helper` cannot be imported, and runs with the sunnypilot
tree on `PYTHONPATH`. Also `TestLaneChangeNudge.test_a_stale_torque_still_confirms_nothing`
and `test_driver_torque_stale_comes_after_the_road_edges`, which fails if the parameter is not
after the edges.

---

## 9. MADS and the board's hand-back

The board releases the wheel on driver torque. It has to: this EPS latches when it is
overpowered while commanding. The rule in `gw_active.c` at `d995bc9`, in serial counts:

| constant | value | meaning |
|---|---|---|
| `GW_DRIVER_ASSERT` | 20 | the fade starts here (and it is openpilot's `steeringPressed` level) |
| `GW_DRIVER_ZERO` | 90 | the fade ceiling `ceil_pct` reaches 0 here. Equal to `GW_DRIVER_YIELD` by design ("the fade and the release agree") |
| `GW_DRIVER_CEIL_PCT` | 0 | one straight fade, ASSERT → YIELD → nothing |
| `GW_DRIVER_YIELD` | 90 | the hand-back threshold: above it for `GW_DRIVER_DEBOUNCE_MS` = 80 ms and the wheel goes back (not under an indicator) |
| `GW_DRIVER_SNAP` | 110 | above it the wheel goes back at once |
| `GW_DRIVER_HOLD_MS` | 0 | the "held above ASSERT" rule is disabled; under an indicator it is `GW_BLINK_HOLD_MS` = 8000 |
| `GW_DRIVER_HANDBACK` | 1 | the hand-back is on |
| `GW_DRIVER_LATCH` | 0 | the override does not latch: it ends once driver torque has stayed at or below `GW_DRIVER_RELEASE` = 10 for `GW_RELEASE_DWELL_MS` = 300 ms |

It reports the override as `0x70B REASON = 4` (`GW_RSN_OVERRIDE`). openpilot has no driver
override of its own: neither `steeringPressed` nor panda's Honda safety stops the command. So
MADS has to follow the board.

`openpilot/sunnypilot/mads/mads.py`, as merged (2026-09-27):

```python
LINBUS_REASON_DRIVER_OVERRIDE = 4

def should_silent_lkas_enable(self, CS):
  if self._gw_paused:                        # FORK(LKAS-GATEWAY), 2cfcd3c6a: the pause holds
    return False
  if self.steering_mode_on_brake == MadsSteeringModeOnBrake.PAUSE and \
     (CS.brakePressed or CS.regenBraking or self.pedal_pressed_non_gas_pressed(CS)):   # upstream 79b79edd2
    return False
  ...

# in update_events(), after the emergency block:
try:
  gw = self.selfdrive.sm['carStateSP'].linbusGateway
except KeyError:
  gw = None                                  # a SubMaster without carStateSP (upstream's MADS tests)
gw_override = bool(gw is not None and gw.present and gw.grantValid and not gw.granted and
                   gw.grantReason == LINBUS_REASON_DRIVER_OVERRIDE)
if gw_override and (self.enabled or self.state_machine.check_contains(ET.ENABLE)) and not emergency:
  self._gw_paused = True
  self.transition_paused_state()             # silentLkasDisable -> State.paused
elif self._gw_paused and not gw_override:
  self._gw_paused = False                    # the generic block below resumes, through its guards

if self.should_silent_lkas_enable(CS):       # upstream
  if self.state_machine.state == State.paused:
    self.events_sp.add(EventNameSP.silentLkasEnable)
```

and in `openpilot/sunnypilot/mads/state.py`, DISABLED branch, when an ENABLE event is present:

```python
if self._events_sp.has(EventNameSP.silentLkasDisable):   # FORK(LKAS-GATEWAY)
  self.state = State.paused
elif self.check_contains(ET.OVERRIDE_LATERAL):
  self.state = State.overriding
else:
  self.state = State.enabled
```

* **Pause, not disable** (`4932aa73c`, replacing `ef4f29432`). The episodes last seconds and end
  on their own. Route `e2` had 13 of them, median 14.7 s. The driver asked for the lanes to stay
  dashed through them, and `paused` is in `ENABLED_STATES` but not `ACTIVE_STATES`, which is
  exactly that behaviour. Route `e2` against `e1` (openpilot still commanding through
  overrides): board engaged 79.7 % against 42.7 %, peak driver torque 173 against 256, and no EPS
  latch against a latch.
* **The pause holds for the whole override** (`2cfcd3c6a`). While `_gw_paused` is set,
  `should_silent_lkas_enable()` says no, the same way upstream's own pause reasons hold while
  their cause lasts. Before this, the upstream resume block lifted the pause on the next frame
  and the gateway block paused it again, so `carControl.latActive` read `0101…` for the whole of
  every override: route `00000103` from t=58.5, 31 s of `grantReason == 4`. The board ignores
  `0x0E4` while it has released to the driver, so the steering was not affected. `_gw_paused` is
  set even when something else paused MADS first, so releasing the brake mid-override does not
  resume lateral while the board still reports the driver's hands on the wheel.
* **The resume is the ordinary one.** When the board stops reporting the override the gateway
  block only clears the flag; the upstream block then resumes through
  `should_silent_lkas_enable()`. So upstream's brake/regen guard (`79b79edd2`, arrived in the
  2026-09 merge) applies to a gateway pause as it does to any other: in Pause mode, a brake
  held at the end of an override keeps MADS paused until it is released. This relies on the
  board ending reason 4 on driver torque alone (`GW_DRIVER_LATCH` is 0 in the Stage 10 image):
  it does not wait for openpilot to ask again.
* **Turning MADS on during an override starts it paused** (2026-09 merge). `self.enabled` is
  still False on the frame of an LKAS press or a unified engagement, so the gateway block used
  to wait a frame: MADS went disabled → enabled (active, one request frame) and paused on the
  next. Now the block also fires when an `ET.ENABLE` event is present, and `state.py` sends an
  ENABLE that arrives with `silentLkasDisable` to `paused`. Nothing else raises
  `silentLkasDisable` while MADS is disabled, so other cars are unaffected.
* **An emergency takeover outranks the pause.** The gateway block does not fire on a frame the
  emergency block turned MADS off (`not emergency`); a `silentLkasDisable` beside the
  `lkasDisable` would have turned "off" into "paused".
* `_gw_paused` makes sure MADS only lifts a pause it caused. A brake, gear or door pause is not
  its to resume.
* `granted` is False whenever `grantValid` is, so an old or silent board cannot trigger this.
  `present` keeps it off every other car.
* `selfdrived.py` subscribes to `carStateSP` for this.

A closed-loop replay of route `00000103` through the merged tree agrees (it was run before the
enable-frame fix, and the pre-merge fork with `2cfcd3c6a` gave the same frames): through the
same 31 s override `latActive` was true on 0.1 % of frames (3 single frames), MADS stayed
`paused` until the driver's own LKAS presses turned it off, and `0x0E4` carried a request on 3
frames against the log's 761.

**Re-apply: the position is load-bearing.** In `update_events()` the order is:

1. the upstream `not self.selfdrive.enabled and self.enabled` block (door, gear, brake-hold pauses);
2. the upstream `MadsSteeringModeOnBrake.DISENGAGE` block;
3. the fork's emergency steer-rate block ("Other" below);
4. the fork's gateway block above;
5. the upstream `if self.should_silent_lkas_enable(CS):` block;
6. the upstream lateral-mismatch check and the `events.remove(...)` calls.

Keep the gateway block after 2 and 3 and before 5, and the `_gw_paused` check first in
`should_silent_lkas_enable()`, ahead of upstream's guards. Keep the constants and
`self._gw_paused = False`, `self._fast_steer = 0` in `__init__`, the two fast-wheel settings
read in both `__init__` and `read_params()` ("Other" below), and the DISABLED branch in
`state.py`. To check on a route: look at `selfdriveStateSP.mads.state` and
`carControl.latActive` during a stretch where `carStateSP.linbusGateway.grantReason == 4`.

**Tests:** `openpilot/sunnypilot/mads/tests/test_mads_gateway_pause.py`, 41 tests in the style of
upstream's `test_mads_steering_mode.py` (`OpenpilotTestCase`, a mocked car): the pause holds
through the whole override and in every brake mode; it resumes on its own; a brake or regen
held in Pause mode outlasts the override; releasing the brake mid-override does not resume; an
emergency turns MADS off on the first override frame and during a pause; no board, no pause;
`selfdrived` subscribes the gateway state; an override does not turn MADS on; turning MADS on
during an override starts paused (LKAS button and unified engagement); and without an override
the enable is upstream's. The other 23 (`TestFastWheelSetting`) are the takeover's settings, see "Other" below.

### Other: the steer-rate emergency takeover (`35622a994`, a setting since 2026-09-30)

This is in the same function of `mads.py`, but it is not protocol: it fits none of the three
areas cleanly and is documented here because it shares `_gw_paused`. It runs before the
gateway block:

```python
EMERGENCY_STEER_RATE = 200.0                  # deg/s, the default
EMERGENCY_STEER_RATES = (150, 200, 250, 300)  # the only values MadsEmergencySteerRate may take
EMERGENCY_STEER_FRAMES = 2                    # not a setting
# __init__ and read_params():
self.emergency_steer_disable = self.params.get_bool("MadsEmergencySteerDisable")
self.emergency_steer_rate = read_emergency_steer_rate(self.params)   # outside the set -> 200
# update_events():
if self.emergency_steer_disable and abs(CS.steeringRateDeg) >= self.emergency_steer_rate:
    self._fast_steer += 1
else:
    self._fast_steer = 0
emergency = self._fast_steer >= EMERGENCY_STEER_FRAMES and self.enabled
if emergency:
    self.events_sp.add(EventNameSP.lkasDisable); self._gw_paused = False
```

It turns MADS **off** (not paused) on a fast wheel. The evidence is 18 routes and 7,438 s of the
board steering, where the maximum steering rate was 151 °/s and no frame reached 200. **It is not
gated on `present` or on the fingerprint, so it applies to every car running this fork** - but
since 2026-09-30 it can be switched off. Keep the all-car scope in mind if the fork is ever used
on another car, or if the hunk is proposed upstream.

**The settings.** Both are `PERSISTENT | BACKUP` in `params_keys.h`:

| param | type, default | meaning |
|---|---|---|
| `MadsEmergencySteerDisable` | BOOL, `"1"` | on: the takeover as it always was. Off: the counter never runs, so `emergency` is always False and the gateway block below behaves exactly as if the takeover did not exist - a swerve during an override is just an override (paused, then resumed) |
| `MadsEmergencySteerRate` | INT, `"200"` | the threshold in °/s. 150, 200, 250 or 300; anything else (a typo in a backup, an index written by a generic widget, a float, garbage) reads as 200, so it can never become hair-trigger or unreachable |

The defaults are the behaviour from before it was a setting, byte for byte. 150 sits on the
single highest sample ever recorded under assist, so at 150 a hard curve can turn MADS off.
Both are read in `__init__` and in `read_params()`, which `selfdrived`'s params thread calls
every 0.1 s, so a change applies within 0.1 s, onroad, without a restart; neither UI locks them
offroad. Turning it off mid-swerve resets the counter.

Where to set them: sunnylink, Steering > MADS Settings ("Turn Off Steering on a Fast Wheel",
with "Fast Wheel Threshold" under it, enabled only while the toggle is on); and on the comma 4
(mici), Settings > vehicle, the rows "off on swerve" (ON = a fast wheel turns steering off) and
"swerve at" (the rate). The mici has no MADS page, and the vehicle page is the fork's own file,
so this costs no upstream UI diff - but that page's button is shown only on a Honda or an
unrecognised car, so on another car the switch is in sunnylink only. The big (tici) UI has no
row for it.

The two emergency cases in `test_mads_gateway_pause.py` test it beside a gateway override, and
`TestFastWheelSetting` there tests the switch (off never fires, off leaves the gateway pause
alone, on and off apply live), every threshold (fires at it in two frames, never just below),
a swerve below a raised threshold during an override (pauses, then disables once it reaches the
threshold) and the fallback for out-of-set values. `selfdrive/ui/tests/test_mads_fast_wheel_settings.py`
keeps the registry, `mads.py`, the mici page and `settings_ui.json` in agreement. Nothing tests
it on another car.

### Other: the EPS latch alert (2026-10-01)

When the EPS latches until key-off, nothing openpilot or the board can do brings steering back
(`S:/Software/EPS-LKAS/docs/EPS-FAULT-STATES.md`, "Nothing passive clears it"). Until this, the
driver heard about it only from the board's `carlog.warning` and the cluster's LKAS lamp. Now
`selfdrived` tells them to restart the car.

```python
# selfdrived.update_events(), after the car events, every frame but dashcam's:
for e in self.eps_latch_alert.update(self.sm['carStateSP'].linbusGateway, self.active or self.mads.active):
  self.events_sp.add(e)
```

| event | type | alert |
|---|---|---|
| `lkasGatewayEpsLatched @26` | `ET.WARNING` only | "Steering Fault" / "Turn the car off and on to clear it"; userPrompt, mid, `Priority.LOW`, `AudibleAlert.prompt` (one `warning.wav`), 6 s. Once per latch. |
| `lkasGatewayEpsLatchedReminder @27` | `ET.WARNING` only | "Steering Off Until Restart"; normal, small, `Priority.LOWEST`, silent, 4 s. Every 5 minutes while latched. |

**A warning, never a fault.** No `NO_ENTRY`, no disable type, no `steerFaultPermanent`, no
carState flag: longitudinal, engagement and MADS are untouched. A WARNING is shown only while
openpilot or MADS is active (`state.py` adds `ET.WARNING` to the alert types only then), so
`eps_latch_alert.py` holds the announcement, and each reminder, until it can be shown rather
than losing it; `can_show` is the previous frame's `self.active or self.mads.active`.

**Below driver monitoring.** The announcement is `Priority.LOW`. Driver monitoring's stage 2
(`driverDistracted2`, `driverUnresponsive2`) is `Priority.MID`, and `AlertManager` breaks a
priority tie in favour of the newer alert, so a MID announcement arriving during stage 2 would
take the screen and silence its repeating sound for 6 s. LOW still wins a tie against other LOW
warnings by being newer; it can hold DM's small stage-1 "Pay Attention" (also LOW) off the screen
for those 6 s, which escalates to stage 2 on DM's own timer regardless.

**The debounce, and why it is not an edge detector.** Read back with LogReader from routes fc
and fd (2026-09-27): after the latch the board says `RETRY_IN 255` on every fresh `0x70B` frame
for the rest of the drive, but `0x70B` itself goes stale again and again - `grantValid` False for
0.1 s to 25.7 s at a time, about twenty times a route - and a stale frame reads as
`latchedUntilKeyOff` False (`carstate_ext.py` reports zeros when stale). `0x70B` also goes
stale on drives with no latch (route `00000103`: 8,300 of 66,859 frames), which is a board-side
question for another day. So:

* evidence comes only from fresh frames: `grantValid and latchedUntilKeyOff` counts towards the
  latch, `grantValid and not latchedUntilKeyOff` towards a clear, a stale frame towards neither;
* `LATCH_CONFIRM_FRAMES` = 1 s of latched evidence confirms. The board already ignores EPS error
  states under 200 ms (`GW_ERR_LATCH_MS`; the longest transient on record is 40 ms) and every
  real latch has lasted minutes;
* once confirmed, only `LATCH_CLEAR_FRAMES` = 3 s of fresh "not latched" clears it, and only a
  confirm after a clear announces again;
* `present` False (any other car) or no `carStateSP` raises nothing.

**Replayed** through the helper with each route's own `selfdriveState`/`selfdriveStateSP`: fd,
fc, f2 and ed each announce once, about 1.0 s after their first `RETRY_IN 255`, with a reminder
every 5 minutes after (fd one, fc two); `00000102` and `00000103` raise nothing.

`openpilot/sunnypilot/selfdrive/selfdrived/tests/test_eps_latch_alert.py` (19 tests): announced
once, a 0.9 s transient never, nothing without a board or on stale frames, the fc/fd stale
pattern announces once, stale frames count towards neither side, only a real clear re-arms, the
announcement and the reminder wait until they can be shown, a silent reminder every 5 minutes,
warnings only, shown only while engaged, one sound, both texts fit the mici alert renderer
(wrapped with the real Inter fonts at the renderer's own sizes, which the test also pins), the
`AlertManager` path (6 s, then 4 s; and a latch during DM stage 2 leaves "Pay Attention" on
screen throughout) and `selfdrived`'s wiring.

---

## 10. The lane graphic

The cluster's lane graphic is drawn by the board, which owns `0x33D` in the Stage 10 image
(since board `df42a0d`, Stage 6). The board's rule (`gw_active.c` lines 1849–1854 at `d995bc9`,
S16 in board `0f73062`):

```c
eps_ok = EPS status fresh && eps_ack && eps_errst == 0
wants  = op_req && op_ok && (now - op_last) <= GW_OP_TIMEOUT_MS      // 0x0E4 bit 7, i.e. latActive
op_lat = wants || (0x500 v3 && 0x500 fresh && LAT_READY)
lanes  = op_lat ? ((engaged && eps_ok) ? SOLID : DASHED) : BLANK
```

In a dry run `lanes` is -1 and the camera's own graphic stands.

Three openpilot-side pieces feed it, and losing any of them breaks it without an error:

| piece | if lost |
|---|---|
| `LAT_READY = CC_SP.mads.enabled or CC.latActive` (carcontroller) | lanes blank on every MADS pause and at stops, or show with MADS off if someone reverts it to `steering_available` |
| `0x500` on `CAN.pt`, version ≤ board max, in the panda allow-list | `op_lat` collapses to `wants`. The logs look identical to a working setup, except for `0x70F BUILD_SP_FRESH`, which stays 0 |
| `steerAtStandstill = True` | lanes blank at stops whenever `0x500` is not fresh |

Expected behaviour (`docs/CHANGELOG_SERIAL_STEERING.md`, 2026-09-22):

| state | graphic |
|---|---|
| MADS off, cruise main on, moving | blank |
| MADS on, not steering | dashed |
| board steering | solid |
| board hands back on driver torque (MADS paused) | dashed |

### 10.1 `LKAS_PROBLEM` read-back (carstate.py)

This hunk belongs to area C (it came with the port, `04a48a0a`), but the protocol depends on
it. In `CarState.update()`, in the non-Bosch branch that sets `carFaultedNonCritical` (since
the 2026-09 merge, upstream's `if not (self.CP.flags & HondaFlags.BOSCH):`):

```python
if self.CP.carFingerprint in HONDA_ELESYS:
  ret.carFaultedNonCritical = bool(cp_cam.vl["ACC_HUD"]["ACC_PROBLEM"] or cp.vl["LKAS_HUD"]["LKAS_PROBLEM"])
else:
  ret.carFaultedNonCritical = bool(cp_cam.vl["ACC_HUD"]["ACC_PROBLEM"] or cp_cam.vl["LKAS_HUD"]["LKAS_PROBLEM"])
```

On this harness `0x33D` arrives on bus 0 (`cp`), not bus 2. Since Stage 6 the frame on bus 0
is the board's own, and the board re-sources its `LKAS_PROBLEM` from the EPS's error state. So
this line is how openpilot hears an EPS error through the cluster frame. It is also why
openpilot must never send `0x33D` on this car: it would read back its own frame (the
`create_sp_hud_status` docstring says so). **Re-apply:** keep the ELESYS branch reading `cp`, not
`cp_cam`. The area-C document owns the rest of this function.

---

## 11. DBC files

| file | contents | rules |
|---|---|---|
| `_sunnypilot_linbus_gw.dbc` | `0x500 SP_HUD_STATUS` (big-endian, with Honda `COUNTER`/`CHECKSUM`), `0x704 GW_ACTIVE` (2 signals), `0x700 EPS_LIN_RAW` (4 signals, **little-endian**), `0x70B GW_STEER_GRANT` (12 signals, 11 read), `0x707 GW_VERSION` and `0x70F GW_BUILD` (little-endian, area A). The `0x70B` start bits are the Motorola spelling of the board's little-endian layout; no field crosses a byte boundary, so the two spellings name the same bits (byte 3 packs `EPS_ACK`, `EPS_LATCHED`, `EPS_ERROR_STATE` bits 5:2, `EPS_FRESH` and `CAM_LKAS_ON`) | declare only what openpilot reads; never `COUNTER`/`CHECKSUM` on a board frame. The comments carry the reasoning and should be kept, but several cite old board line numbers (section 13). Created as `_sunnypilot_hud.dbc`: `git log --follow` |
| `_steering_control_e.dbc` | `0x0E4` with byte 2 split (`SET_ME_X00` = bit 6, `LDW_RIGHT` 21, `LDW_LEFT` 20, `SET_ME_X00_3` 19\|4); `0x18F STEER_STATUS` with `STEER_CONTROL_ACTIVE` at 32 | imported only by `honda_accord_au_2015_can.dbc`, so no other platform sees these names |
| `honda_accord_au_2015_can.dbc` | `CM_ "IMPORT _steering_control_e.dbc";` and `CM_ "IMPORT _sunnypilot_linbus_gw.dbc";` plus area-C imports | the `_generated` DBC is built in memory by `opendbc.get_generated_dbcs()`, so there is no generated file to commit |
| board `dbc/eps-lkas-gw.dbc` | the full `0x700`–`0x70F` layout | its own `0x500` entry is v2-era (bytes 5–6 `RESERVED`). Use the opendbc one for `0x500` |

---

## 12. Tests and how to run them

Since the 2026-09 merge every sunnypilot test here is a `unittest.TestCase` or
`OpenpilotTestCase`, collected by upstream's `tools/test_runner.py` (pytest is gone upstream).
Run them in the openpilot venv, from the repo root (WSL `~/sp-merge` for the sync).

| test | covers | how |
|---|---|---|
| `opendbc_repo/opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` §7, §8, §10–§15 | `0x500` bytes, bus, checksum, counter, version, km/h, integrator; `0x704` → `actuating`, staleness, liveness-exempt; v3 byte 5/6 (§10: `LAT_READY` with MADS on, and without it); `0x0E4` byte 2; brake ramp; `0x70B` decode; latch and mirror; stale lane change | `python opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` from `opendbc_repo`. It prints PASS/FAIL per check and exits 1 on any failure; §15 SKIPs unless the sunnypilot tree is on `PYTHONPATH`. Under unittest discovery, `TestDynamicTuningIntegration.test_all_checks_pass` reports it |
| `opendbc_repo/opendbc/safety/tests/test_honda.py` `TestHondaElesysScmStanddownSafety`, `TestHondaElesysStanddownGasInterceptorSafety` | `[0x500, 0]` is in `TX_MSGS`; with `common.py`'s exemption, `0x500` is still refused by every other brand's mode | `python -m unittest opendbc.safety.tests.test_honda` (builds libsafety; 942 run, OK) |
| `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | every nested `CarControlSP` struct is rebuilt by `convert_carControlSP`; `lateralControl` values survive the seam; `linbusGateway` converts to capnp | `python tools/test_runner.py <file>` |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py` (`test_a_stale_torque_still_confirms_nothing`, `test_driver_torque_stale_comes_after_the_road_edges`) | stale torque never confirms; the parameter sits after upstream's road-edge parameters | runner |
| `openpilot/sunnypilot/mads/tests/test_mads_gateway_pause.py` | the gateway pause and resume (section 9), 41 tests, 23 of them the fast-wheel settings | runner |
| `openpilot/selfdrive/ui/tests/test_mads_fast_wheel_settings.py` | the fast-wheel settings agree across `params_keys.h`, `mads.py`, the mici page and `settings_ui.json`; what sunnylink writes reads back the way `mads.py` reads it; the FORK markers, 15 tests | runner |
| `openpilot/sunnypilot/selfdrive/controls/lib/tests/test_latcontrol_gateway_hold.py` | the integrator freeze through the torque-controller extension, 4 tests | runner |

After the merge all of these pass: the fork's sunnypilot targets together with upstream's
MADS, lateral and sunnylink tests gave 309 passed and 1 skipped (a sunnylink test that needs
`jsonschema`), and upstream's whole suite 1600 passed, 0 failed.

**What the seam test does not cover.** It round-trips values only for `linbusGateway`
`present`, `actuating`, `valid` and `dryRun`, and for the firmware fields. The grant fields
(`@5`–`@18`) and `driverTorqueStale` go through the seam only at their default values, as part
of the splat, which checks that the names exist. A type problem, such as a float reaching a
UInt8 field, would only show up on the car. (An end-to-end probe after the merge pushed every
gateway field at a non-default value through `convert_to_capnp()`, including negative `Int16`s
and a `UInt32` with the top bit set, without loss; it is not a test.)

**The integration script's §10 failure is fixed** (2026-09 merge). It built `CC_SP` with
`mads.enabled` False and expected `LAT_READY`, which since `43a98b9d` comes from
`mads.enabled`. §10 now sets `CC_SP.mads.enabled = True` for that case and adds a MADS-off case
that expects `LAT_READY` clear, so the rule is pinned both ways. The script also used to call
`sys.exit(1)` at import, which aborted opendbc's test discovery; that now happens only under
`__main__`.

**Gaps:** nothing tests the integrator carry and decay or the `integrator_frozen` echo,
`controlsd_ext` filling `lateralControl`, the steer-rate takeover on any car other than through
a gateway override, or the mirror-freshness case in section 8.2.

---

## 13. Stale comments and documents

These were found while writing this document, and re-checked after the 2026-09 merge: all are
still present except the last row. They are in comments and docs, so they mislead readers but
do not change behaviour. Board line numbers below are at `d995bc9`.

| where | says | actually |
|---|---|---|
| `hondacan.py` `create_steering_control` comment; `_steering_control_e.dbc` `CM_ BO_ 228`, `LDW_RIGHT`/`LDW_LEFT`; `_sunnypilot_linbus_gw.dbc` `LDW_ACTIVE`; integration test §11 comment; `docs/SP_HUD_STATUS.md` | LDW bits are inert on board firmware `875ba124` | true of `875ba124` only. Board `8340c1d` (2026-09-12) copies `0x0E4` byte 2 bits 5:4 into the serial frame, and `df42a0d` (Stage 6) feeds `LDW_*`, `STEERING_REQUIRED` and `OP_SATURATED` into the board's own `0x33D` alerts |
| board line references in fork comments: `hondacan.py` (`create_steering_control`: `gw_active.c:759-773`, `lkas_uart.c:449`; `SP_HUD_MAX_TORQUE`: `gw_active.c:1348`, `:1104,1108`); `_steering_control_e.dbc` (the same two); `_sunnypilot_linbus_gw.dbc` (`gw_active.c:234`, `:1017-1022`, `:1104,1108`, `:1267`, `:1348`, `:1356-1375`, `:1391`, `:1394`, `:1401-1406`, `sp_hud.c:88`); `carstate_ext.py` (`gw_active.c:1391`, `:1017-1022`, `:1401-1406`) | line numbers | they are `875ba124` line numbers and point at unrelated code now. At `d995bc9` the `MAX_TORQUE` fold is at `gw_active.c:2435` and the `RETRY_IN = 255` assignment at `:2497`. Cite functions or constants rather than lines when rewriting |
| `custom.capnp` (the `fwBuildValid` block), `carstate.py` `get_can_parsers`, `carstate_ext.py` `_update_linbus_firmware`, `_sunnypilot_linbus_gw.dbc` `CM_ BO_ 1807` | board firmware `625b782a` | no such commit. It is `625b782e` (`625b782ea8`, "An app-slot image must SAY it is one") |
| `openpilot/selfdrive/controls/lib/latcontrol.py` `LatControl.__init__` comment | the torque controllers "reset the PID the frame it takes over" | since `39b857567` the integrator is clipped to 0.25 and decayed with τ = 30 s instead. `946b5fa21` fixed the controllers' comments but not this one |
| `docs/SP_HUD_STATUS.md` "Required behaviour in the torque controllers"; board `docs/SP-PROTOCOL-V3.md` §1.3 code sample (`self.pid.reset()`) | the PID is reset on takeover | clipped and decayed, as above |
| `docs/SP_HUD_STATUS.md` `LAT_READY` row; board `docs/SP-PROTOCOL-V3.md` §1.2; board `gw_active.c` lane-graphic comment (line ~1844) | "would steer if the board allowed it" / `steering_available or latActive` | `mads.enabled or latActive` since `43a98b9d` |
| `docs/SP_HUD_STATUS.md` "Round 1 is telemetry only" | nothing reads `granted`/`grantReason` | `mads.py` reads both since `ef4f29432` |
| `docs/CHANGELOG_SERIAL_STEERING.md` 2026-09-18 | MADS turns off on a hand-back | it pauses since `4932aa73c`. The steer-rate takeover (`35622a994`) had no changelog entry until 2026-09-30, when it became a setting |
| board `docs/SP-PROTOCOL-V3.md` §1.2 | the board applies `min(MAX_TORQUE, authority)` | reported only, in `0x70B AUTHORITY` |
| board `docs/SP-PROTOCOL-V3.md` §1.3 | "use item access on `CC_SP.lateralControl['integrator']`" | attribute access, because `convert_carControlSP` rebuilds the dataclass |
| "0x33D is the camera's" framing: `carcontroller.py` ELESYS HUD comments ("stock camera's HUD is forwarded instead", "The stock camera keeps 0x33D. This is the side channel an in-line module reads to merge"); `hondacan.create_sp_hud_status` docstring ("a module that is already passing the camera's LKAS_HUD through can merge"); `_sunnypilot_linbus_gw.dbc` `CM_ BO_ 1280`; the comment above the ELESYS lists in `honda.h` ("the stock camera keeps it") | the camera owns `0x33D` and the board merges into it | since board `df42a0d` (Stage 6) the Stage 10 image owns `0x33D` and mutes the camera's; the merge runs only on the dry-run and Stage 2a paths. What these comments conclude (openpilot must not send `0x33D`) is still right |
| `carcontroller.py` v3 comment | "the board acts on OP_STATE and the release bits when it decides what to put in 0x70B REASON" | the board parses `OP_STATE`, `RELEASE_BRAKE` and `RELEASE_DRIVER` and reads none of them (`sp_hud.c` stores them; nothing in `gw_active.c` uses them) |
| `carcontroller.py` brake-release comment | 4 serial counts per frame | 8 at the current authority of 160 |
| `structs.py` comments in `LinbusGateway` | "names and order must stay in lockstep" | only names matter (section 6) |
| `carstate_ext.py` comment on `LINBUS_GW_STALE_FRAMES` | `GW_ACTIVE` "has been observed dropping" | true of early firmware; later measured at 10.0 Hz with none lost. The 500 ms window is still reasonable |
| integration test §10 | expected `LAT_READY` with MADS off | fixed in the 2026-09 merge (section 12) |

---

## 14. Upstream merges

### 14.1 What the 2026-09 merge did in this area

Upstream sunnypilot `a5f44653d` and opendbc `f95f996f` were merged on 2026-09-27. Every hunk in
section 2 was carried; nothing was lost, and nothing here reaches the car differently. An
open-loop replay of route `00000103` (670 s) gave byte-identical `sendcan` for `0x0E4` (66,304
frames) and `0x500` (6,631 frames, 10.00 Hz, version 3), and identical `carStateSP`, including
every `linbusGateway` field and `driverTorqueStale`.

* **The layout moved under `openpilot/`** (`5edc0bd89`, `37eda06c9`). Every sunnypilot path in
  this document gained the prefix; `docs/` and the root `.md` files did not move. Imports moved
  with it: `from openpilot.cereal import ...`. The fork's added tests were file-location
  conflicts and were accepted at the new paths.
* **`DesireHelper.update()` changed signature upstream** to
  `update(self, carstate, lateral_active, lane_change_prob, left_edge_detected=False, right_edge_detected=False)`.
  `driver_torque_stale=False` now comes after the edge parameters, both `modeld` files pass it
  by keyword, and `not driver_torque_stale` is in upstream's rewritten `torque_applied`
  (section 8.3). The opendbc integration §15 passes it by keyword too.
* **SubMaster lists were rewritten upstream** (`liveDelay` → `lateralDelay`, `liveCalibration` →
  `extrinsicsCalibration`, `roadCameraState` → `narrowRoadCameraState`, …). `'carStateSP'` was
  re-added to the lists in `controlsd.py`, `selfdrived.py`, `modeld.py` and
  `modeld_v2/modeld.py`.
* **`LaC.update()` returns three values upstream** (`14a00e0cc`); `set_linbus_gateway()` sits
  before it and `run_ext(sm, pm, lac_log, LaC)` after it. `controlsd_ext.run_ext` and
  `state_control_ext` were unchanged upstream apart from the move, so the fork's signatures
  apply as they are. Upstream renamed `update_live_torque_params` to
  `update_torque_parameters`; the hold follows it in both torque controllers.
* **MADS** (section 9): upstream `79b79edd2`'s brake/regen guard, merged with the fork's
  `2cfcd3c6a` gateway-pause fix (this was a content conflict), plus the enable-frame fix and the
  `KeyError` fallback found in review.
* **Upstream's Lateral Jerk controller** (`91a53aa16`, opt-in) and NNLC update the PID a second
  time through `LatControlTorqueExtBase`, which skipped the hold. Fixed in
  `latcontrol_torque_ext_base.py` (section 7).
* **opendbc upstream stopped using `Platforms.with_flags` in Honda.** `HONDA_ELESYS` (area C) is
  now `frozenset(c for c in CAR if c.config.flags & HondaFlags.ELESYS)`. Every area-B gate is
  still written `carFingerprint in HONDA_ELESYS`, and `hondacan.py` no longer imports it: the
  brake-command units bit is passed as `elesys=` (area C).
* Upstream's `blockPcmEnable` line landed beside the fork's `CarStateExt.update(ret, ret_sp, ...)`
  call in `carstate_ext.py` and merged cleanly; it does not affect this car.
* **Ordinals:** at `a5f44653d`, upstream `CarControlSP` ends at `@4` and `CarStateSP` has only
  `speedLimit @0`. No collision.
* `safety/tests/common.py` gained the Elesys-only `0x500` exemption (section 2), which fixed two
  cross-mode failures that predate the merge.

### 14.2 Hook points, and what breaks silently if a hunk is lost

| upstream location | fork hunk | if it is lost |
|---|---|---|
| `hondacan.create_steering_control` | `serial_gateway`/`ldw_*` kwargs | LDW never reaches the EPS. Nothing else changes |
| `carcontroller.update`, after `rate_limit` of the steer torque | brake ceiling | openpilot keeps steering through the brake. `RELEASE_BRAKE` then claims a withdrawal that is not happening |
| `carcontroller.update`, the `frame % 10` HUD block | the ELESYS branch: no `create_lkas_hud`, send `create_sp_hud_status` on `CAN.pt` | if `create_lkas_hud` comes back, openpilot tries to send its own `0x33D`, which the ELESYS panda lists do not allow. If `0x500` goes, the lane graphic falls back to `latActive` only, and the integrator guard and the board's HUD alerts go dark. **Nothing errors** |
| `carcontroller`, the `lat_ready` line | `CC_SP.mads.enabled or CC.latActive` | graphic blanks on every MADS pause, or shows with MADS off |
| `hondacan.SP_HUD_PROTOCOL_VERSION` | 3 | raising it ahead of the firmware makes the board drop every `0x500` |
| `carstate.get_can_parsers` | `pt_msgs` with `nan` | the first `0x704` is lost, and worse, a quiet board makes `canValid` false and openpilot refuses to engage |
| `carstate.update` → `CarStateExt.update(self, ret, ret_sp, can_parsers)` | `ret_sp` argument and the ELESYS calls | `linbusGateway` stays at its defaults, including `present = False`. The integrator gate switches off, so the integrator winds open-loop before every takeover again (route `b3`). MADS never follows the board, and the latch detection and torque substitution stop (see the next row) |
| `carstate_ext._update_driver_torque_validity` | latch detection plus substitution | `steeringPressed` sticks on a frozen value for whole engagements: frozen integrator, wrong driver monitoring, one-direction lane changes |
| `carstate`, `carFaultedNonCritical` | ELESYS reads `LKAS_HUD` from `cp` | openpilot stops hearing the board's `LKAS_PROBLEM` (area C owns it; section 10.1) |
| `structs.py` / `custom.capnp` | the three structs | if a name exists in the dataclass and not in capnp, `convert_to_capnp()` raises in card every frame; if it exists only in capnp, the field is never written and reads zero. The seam test catches a missing name, not a wrong type (section 12) |
| `helpers.convert_carControlSP` | `lateralControl` rebuild | **card crashes every frame** and the radar side of the split bus faults (routes `b5`–`b8`) |
| `controlsd.state_control`, before `LaC.update` | `set_linbus_gateway` | the gate sees `present=False`. The integrator winds open-loop before every takeover (route `b3`), and `INTEGRATOR_FROZEN` reads 0 |
| `controlsd.update` → `run_ext(sm, pm, lac_log, LaC)`, and `controlsd_ext.state_control_ext` | the `lateralControl` fill | `0x500 INTEGRATOR` is always 0, so the board's first-engagement guard never fires |
| `latcontrol_torque(_v0).update` | `linbus_hold` in `freeze_integrator` | as above. Check **both** controllers. `_v0` is the one that runs by default |
| `LatControlTorqueExtBase.update_output_torque` | `integrator_frozen` in `freeze_integrator` | with Lateral Jerk or NNLC on, the integrator winds open-loop through every hold. `test_latcontrol_gateway_hold.py` fails |
| `LatControl` PID attribute | `getattr(self, "pid", None)` in the gate | if upstream renames `self.pid`, the freeze survives but the carry and decay stop |
| `desire_helper.update` and both `modeld` call sites | `driver_torque_stale`, last and by keyword | a stale value confirms lane changes (only if the mirror is also down). A positional call would land in upstream's edge parameters, silently |
| `selfdrived` SubMaster | `'carStateSP'` | `mads.py`'s `KeyError` fallback then treats the car as having no gateway, and the pause silently stops working. `test_selfdrived_subscribes_the_gateway_state` catches it |
| `mads.should_silent_lkas_enable` | `if self._gw_paused: return False` first | the MADS flap comes back: `latActive` alternates frame by frame through every override (route `00000103`) |
| `mads.update_events` | the gateway block | the car goes on requesting through every board hand-back, and the cluster says "steering" while the wheel is the driver's |
| `mads/state.py`, DISABLED | `silentLkasDisable` beside an ENABLE → `paused` | turning MADS on during an override gives one active frame before the pause |
| `honda.h` ELESYS TX lists | `{0x500, 0, 8}` | panda drops `0x500` silently |

### 14.3 After every merge

1. Grep for everything this area touches. Both commands hit every area-B file in section 2;
   they also hit area-A and area-C lines and some upstream code, so compare against section 2
   rather than counting hits.

   In the sunnypilot tree:

   ```
   git grep -n -P -e "LIN-bus gateway" -e linbus -e LINBUS_ -e driverTorqueStale -e driver_torque_stale \
     -e "lateralControl\b" -e _gw_paused -e EMERGENCY_STEER -e carStateSP -e run_ext \
     -e set_linbus_gateway -e integrator_frozen -e "FORK\(LKAS-GATEWAY\)" \
     -- openpilot/cereal/custom.capnp openpilot/selfdrive openpilot/sunnypilot
   ```

   `-P` is needed for `\b`, which keeps upstream's `lateralControlState` out. `carStateSP` also
   matches upstream speed-limit and UI code.

   In opendbc:

   ```
   git grep -n -e "FORK(HONDA_ELESYS)" -e "FORK(HONDA_ACCORD_9G_AU)" -e "FORK(LKAS-GATEWAY)" -e HONDA_ELESYS -e SP_HUD \
     -e EPS_LIN_RAW -e GW_STEER_GRANT -e brake_release -e LateralControl -e LinbusGateway \
     -e driverTorqueStale -e linbus -e 0x500 -e serial_gateway \
     -- opendbc/car/honda opendbc/car/structs.py opendbc/dbc/generator/honda \
        opendbc/safety/modes/honda.h opendbc/safety/tests opendbc/sunnypilot/car/honda
   ```

   The path list is there because `0x500` also matches Ford, Hyundai and Rivian radar code.
2. Run the integration script (section 12), with the sunnypilot tree on `PYTHONPATH` so §15
   runs. There is no accepted failure.
3. Run the section 12 tests under `tools/test_runner.py`, and the opendbc Honda safety tests.
   Check the runner's counts: a module that reports 0 tests has been dropped.
4. Diff the new `custom.capnp` `CarControlSP`/`CarStateSP` against `structs.py` field by field,
   by name, and check upstream's highest ordinals.
5. Check both `modeld` `DH.update(...)` calls pass `driver_torque_stale=` by keyword, after any
   parameters upstream has added.
6. If upstream has touched `mads.py` or `mads/state.py`, re-read section 9 against the merged
   code: the `_gw_paused` check must still come first in `should_silent_lkas_enable()`.
7. On the first drive, check the route:
   * `0x500` appears as `src 128` (the panda's TX echo on bus 0) and not as `src 130` (bus 2),
     with version 3 and a moving counter;
   * `0x70F BUILD_SP_FRESH` is 1 (the board's `tools/route_flatten.py` reports it);
   * `0x500 INTEGRATOR_FROZEN` is 1 whenever `carStateSP.linbusGateway.actuating` is 0;
   * `0x0E4` byte 2 bit 2 is 0 on every frame;
   * `carStateSP.linbusGateway.valid` and `grantValid` are mostly 1, and `driverTorqueStale` is
     rare;
   * during any `grantReason == 4` stretch, MADS is `paused` and `latActive` is 0 on every
     frame (section 9);
   * the lane graphic follows the table in section 10.
