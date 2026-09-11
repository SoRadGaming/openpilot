# SP-PROTOCOL — sunnypilot ⇄ LIN-bus gateway

Two sunnypilot-only CAN frames, one in each direction, between openpilot and the aftermarket
board that sits in line with the LKAS camera and translates openpilot's steering request onto
the EPS's LIN line. Nothing else in the car sends or reads either of them.

```
sunnypilot  ──── 0x0E4 STEERING_CONTROL (100 Hz, bus 0) ───►  board
sunnypilot  ──── 0x500 SP_HUD_STATUS v3 (10 Hz, bus 0) ────►  board
sunnypilot  ◄─── 0x704 GW_ACTIVE         (10 Hz, bus 0) ────  board
sunnypilot  ◄─── 0x70B GW_STEER_GRANT    (10 Hz, bus 0) ────  board   ← v3
```

`0x0E4` is a stock Honda frame and is documented here only where the board reads it
differently from a car: byte 2 (below). The board-side contract for all of this is
`S:\Software\EPS-LKAS\docs\SP-PROTOCOL-V3.md`; this document is the openpilot side.

| | |
|---|---|
| **DBC (this side)** | `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc` |
| **DBC (board side)** | `dbc/eps-lkas-gw.dbc` in the gateway firmware repo |
| **Platform** | `HONDA_ACCORD_9G_AU` (HONDA_ELESYS) only |
| **Board firmware** | v3 needs `75aa91ee` or later. Older images reject a `0x500` above v2 **whole** |
| **Byte order** | Big-endian (Motorola), same as every Honda frame |

> ## What to drive
>
> **`serial-steering-round1`, tip, in both this repo and `opendbc_repo`.** Everything on it is
> telemetry, driver feedback and comment corrections: it does not touch `lateralParams`,
> `steerActuatorDelay`, the lateral tuning or the controller, and it leaves `0x0E4` byte 2
> bit 2 (`SERIAL_DOMAIN`) clear, so `actuators.torque` and every byte of `0x0E4`, `0x1FA`,
> `0x30C` and `0x1A6` are identical to `master`. The next drive therefore moves exactly one
> variable — the board's authority, 40 → 80 on firmware `875ba124`.
>
> **`serial-steering-round2-tuning` is NOT for that drive.** It is round 1 plus
> `steerActuatorDelay` 0.15 → 0.3, which seeds `liveDelay.lateralDelay` at 0.5 s instead of
> 0.35 s (`selfdrive/locationd/lagd.py`) and so changes `actuators.torque` from the first
> engagement. Drive it *after* authority 80 has its own log, or the two changes cannot be told
> apart. The delay is probably right; it just belongs to the drive after this one.

## Why this exists: the integrator problem

On this car nothing listens to openpilot's `STEERING_CONTROL` (`0x0E4`) until the board
engages. Until then openpilot is **open-loop**: `latActive` is true, it commands, the car does
not respond, the error never closes, and **the integrator winds up without bound**.

Measured on route `000000b3`, a 70 km/h left sweeper the stock camera held at −46.7 counts:

```
feedforward  40·f  =  -14.2 counts  LEFT    ← correct
integrator   40·i  =  +26.0 counts  RIGHT   ← 65% of the board's whole budget
net                =  +12.5 counts  RIGHT   in a LEFT curve
```

Sign agreement with the camera: 8 of 108 frames. With `i` removed: 92.6%.

The board cannot see `i` from `0x0E4`, and cannot infer it from saturation either — through
that curve openpilot was *not* saturated (output −0.31) while `i` sat at +0.65. So sunnypilot
has to (a) not wind up in the first place, and (b) tell the board the integrator value. The
two frames below are exactly those two things.

## What sunnypilot reads: `0x704 GW_ACTIVE`

| Signal | Byte | Bit | Meaning |
|---|---|---|---|
| `ENGAGED` | 6 | 0 | The board is commanding non-zero torque on the LIN line. |
| `DRY_RUN` | 5 | 7 | The board is **not actuating** — logging only. |

```python
actuating = ENGAGED and not DRY_RUN and fresh
```

`DRY_RUN` matters. In a dry run `ENGAGED` is still set — it reports what the board *would*
do — so `ENGAGED` alone would tell openpilot it is in control during a dry run and it would
wind up exactly as before. Every consumer reads the combined flag.

**Staleness is 500 ms**, not the 300 ms used in the other direction: `0x704` is low priority on
the board and has been observed dropping. A missing frame reads as **not actuating**.

Only those two bits are defined on the sunnypilot side. The board's own DBC carries the rest
(`INH_ENGAGE_GUARD` and friends); this file deliberately does not duplicate them, so there is
one place to change when the board's layout moves. There is also no `CHECKSUM`/`COUNTER`
signal on purpose — the board does not compute a Honda checksum for this frame, and naming a
signal `CHECKSUM` in a `honda_` DBC would make opendbc try to validate one.

### Where it lands on this side

`carstate_ext.py` decodes it into `CarStateSP.linbusGateway`:

| field | |
|---|---|
| `present` | this platform has a board at all — **False on every other car**, which is what keeps the hold below from ever engaging where there is nothing to wait for |
| `engaged`, `dryRun` | the two bits, as received |
| `valid` | a frame arrived within the last 500 ms |
| `actuating` | `engaged and not dryRun and valid` — the one flag consumers use |

Staleness is counted in frames (50 at 100 Hz) off `CANParser.ts_nanos`, which only advances
when a frame actually arrives — `CarState.update()` is not handed a clock.

### Required behaviour in the torque controllers

`controlsd` feeds `present`/`actuating` into the lateral controller every frame via
`LatControl.set_linbus_gateway()`. Both torque controllers (`latcontrol_torque.py` and the
sunnypilot `latcontrol_torque_v0.py`, which is the one that actually runs by default) then do:

```python
linbus_hold = self._linbus_integrator_gate()     # every frame, active or not
...
freeze_integrator = steer_limited_by_safety or CS.steeringPressed or CS.vEgo < 5 or linbus_hold
```

and inside the gate:

1. **Never integrate while the car is not following us.** `linbus_hold` is true whenever the
   board is not actuating.
2. **Start clean every time the board takes over.** On the rising edge of `actuating` the PID
   is reset — `i`, and the p/d history with it.

Both are needed. Freeze alone leaves whatever `i` was at when the board engages; reset alone
lets it wind during the dry run and dump it in on the first closed frame. With both, `i` is
zero until the loop is genuinely closed and only ever integrates against a car that responds.

The gate is evaluated *before* the `if active:` branch so the edge is tracked even while lateral
is inactive, and the reset lands on the exact frame the board engages.

**`latActive` is deliberately not gated on this.** The board engages on
`STEER_TORQUE_REQUEST`; if sunnypilot stopped requesting until the board was engaged, neither
side would ever start.

## What sunnypilot sends: `0x500 SP_HUD_STATUS` v3

| | |
|---|---|
| **Address** | `0x500` (1280), standard 11-bit — chosen because it is unused on every bus of this car (81 distinct addresses seen; the whole `0x500–0x5FF` range is empty) |
| **Bus** | 0 — the camera's bus, where the board sits. On this harness bus 2 is the Elesys radar branch, and the board is not on it |
| **DLC** | 8 |
| **Rate** | 10 Hz |

```
byte 0   7 6 5 4   3        2        1         0
         VERSION   OP_EN    LAT_ACT  LONG_ACT  STEER_REQ

byte 1   7        6        5    4      3      2     1 0
         LDW_L    LDW_R    FCW  SOLID  DASHED  LEAD  ALERT_LEVEL

byte 2   SET_SPEED (km/h, 0 = unavailable, saturates at 255)
byte 3   INTEGRATOR         int8, torqueState.i × 100, clipped ±127        ← v2
byte 4   bit 0 OP_SATURATED   bit 1 INTEGRATOR_FROZEN                        ← v2

byte 5   7          6         5        4 3 2      1          0
         LDW_ACT    REL_DRV   REL_BRK  OP_STATE   LAT_READY  WANT_CONTROL     ← v3
byte 6   MAX_TORQUE (serial counts, 0 = "use your own authority")             ← v3

byte 7   7 6      5 4        3 2 1 0
         unused   COUNTER    CHECKSUM
```

| Signal | Bits | Meaning |
|---|---|---|
| `PROTOCOL_VERSION` | b0[7:4] | **`3`**. See versioning below — this one is a hard gate on the board. |
| `OP_ENABLED` | b0[3] | openpilot is engaged. |
| `LAT_ACTIVE` | b0[2] | openpilot is steering. |
| `LONG_ACTIVE` | b0[1] | openpilot is controlling speed. |
| `STEERING_REQUIRED` | b0[0] | **openpilot** wants hands on the wheel. Not the camera's nag. |
| `LDW_LEFT` / `LDW_RIGHT` | b1[7] / b1[6] | Lane departure, left / right. |
| `FCW` | b1[5] | Forward collision warning. Takes priority over `STEERING_REQUIRED`. |
| `SOLID_LANES` / `DASHED_LANES` | b1[4] / b1[3] | Draw solid (steering) / dashed (lanes seen, not steering). |
| `LEAD_VISIBLE` | b1[2] | A lead vehicle is being tracked. |
| `ALERT_LEVEL` | b1[1:0] | `0` none, `1` info, `2` warning, `3` critical. |
| `SET_SPEED` | b2 | Cruise set speed in **km/h**, always — never the cluster's display units. |
| `INTEGRATOR` | b3 | `int8(clip(round(torqueState.i × 100), −127, 127))`. Lets the board refuse its first engagement while `|i| > 0.15` (`GW_INTEG_MAX`). |
| `OP_SATURATED` | b4[0] | `torqueState.saturated`. |
| `INTEGRATOR_FROZEN` | b4[1] | `1` while the gateway hold in the section above is active. **The acknowledgement**: if the board sees this low while `0x704` says not-engaged, the sunnypilot side is not running this protocol. |
| `WANT_CONTROL` | b5[0] | v3. openpilot intends to steer and is asking. The redundant statement of `0x0E4 STEER_TORQUE_REQUEST`, which remains the thing that actually asks. |
| `LAT_READY` | b5[1] | v3. openpilot would steer if the board allowed it. |
| `OP_STATE` | b5[4:2] | v3. `0` off, `1` ready, `2` requesting, `3` active, `4` withdrawing, `5` faulted. **openpilot's** state machine, not the board's — the board reports its own on `0x70B STATE`. |
| `RELEASE_BRAKE` | b5[5] | v3. Withdrawing because the brake is pressed. Only ever set while `latActive`. |
| `RELEASE_DRIVER` | b5[6] | v3. The driver is steering. Only ever set while `latActive`. **Advisory** — unlike `RELEASE_BRAKE` there is no matching ramp; see below. |
| `LDW_ACTIVE` | b5[7] | v3. A lane-departure warning is being shown on either side. **Inert** — `sp_hud.c` decodes it and `sp_hud_merge_lkas()` never reads it, so no `RDM_HUD` and no chime come of it on firmware `875ba124`. |
| `MAX_TORQUE` | b6 | v3. The ceiling openpilot is willing to use, in **serial** counts. **Sent as 0**, which the board reads as "use your own authority". **Reported, not enforced** — see below. |
| `COUNTER` | b7[5:4] | 0–3, increments each frame. |
| `CHECKSUM` | b7[3:0] | Standard Honda 4-bit checksum. |

### Why `MAX_TORQUE` is 0 and not the number

The board scales `0x0E4` by `authority / 2560`, so openpilot's full scale ±1.0 maps exactly
onto the board's authority **whatever that authority is**. openpilot's saturation flag and its
anti-windup therefore stay truthful on their own as the authority ladder climbs
40 → 80 → 120 → 160, with nothing to change on this side. Sending the number here as well
would put the ladder in two places that could disagree.

> **`MAX_TORQUE` is reported by the board, not applied by it.** On firmware `875ba124` the
> field is read in exactly one place — `gw_active.c:1348` — and only to compute the
> `AUTHORITY` byte the board puts in `0x70B`. The command path is unconditional:
> `gw_active.c:1104,1108` clamp to the compile-time `GW_LIN_AUTHORITY`. So setting
> `MAX_TORQUE = 40` today would make `0x70B` report a ceiling of 40 while the board went on
> commanding up to 80 — actuation and telemetry wrong in opposite directions, with the log
> looking like the cap worked. It costs nothing while the value is 0, which is the other
> reason it is 0. **Do not use it as a probe-drive limiter** until the board clamps to the
> negotiated value at `gw_active.c:1104,1108`.

This is also why `lateralParams.torqueBP/torqueV` stays `[[0, 2560], [0, 2560]]` and why
`0x0E4` byte 2 bit 2 (`SERIAL_DOMAIN`) stays **clear**. Changing the domain and raising the
authority in the same drive would confound the one measurement the drive is for.

`ALERT_LEVEL` is a severity hint for choosing a chime, derived from the flags — never a
substitute for them.

`INTEGRATOR_FROZEN` echoes the *gateway* hold specifically, not every reason the integrator
might be frozen (driver steering, speed under 5 m/s, safety limiting). That is what the spec
asks for and what makes the acknowledgement check clean.

### Why a side channel rather than taking over LKAS_HUD

The obvious design is for openpilot to send `LKAS_HUD` (`0x33D`) itself and have the board
block the camera's copy. Two things break that are not obvious:

1. **The lane-departure popup disappears.** `RDM_HUD` lives in `0x33D` and is produced by the
   stock camera's road-departure logic. openpilot has no equivalent.
2. **openpilot goes blind to camera faults.** On this platform `carstate.py` reads
   `LKAS_PROBLEM` from `0x33D` **on bus 0**, unlike every other Honda. If openpilot became the
   sender it would read back its own frame and `carFaultedNonCritical` would be stuck false.

So the camera keeps `0x33D` untouched and this frame rides alongside it; the board merges.

> **Merge by OR, never by overwrite.** Replacing the camera's flag bits wholesale can mask a
> genuine `LKAS_PROBLEM` or suppress a real road-departure warning — the same failure as
> taking the message over, relocated into firmware where it is harder to spot.

## What sunnypilot also sends: `0x0E4 STEERING_CONTROL` byte 2

Nothing in this car reads `0x0E4` — the EPS has no CAN steering input and the board consumes
the frame. The board reads byte 2 as a bit field, and openpilot fills exactly two of its bits.

| Bit | Name | openpilot |
|---|---|---|
| 7 | `STEER_TORQUE_REQUEST` | `latActive`, as on every Honda |
| 6 | `WIGGLE_DISABLE` | 0 — the board owns the command LSB alternation |
| 5 | `LDW_RIGHT` | `hudControl.rightLaneDepart`. Specified to reach serial byte 2 bit 5; **inert today** |
| 4 | `LDW_LEFT` | `hudControl.leftLaneDepart`. Specified to reach serial byte 2 bit 4; **inert today** |
| 3 | `TX_RAW_SERIAL` | 0 — diagnostics only |
| 2 | **`SERIAL_DOMAIN`** | **0**, and it must stay 0 while openpilot is in the 2560 domain |
| 1 | reserved | 0 |
| 0 | `BLEND_DISABLE` | 0 — the board still owns the driver-torque blend |

> **`SERIAL_DOMAIN` is the dangerous one.** Set, it tells the board to take `STEER_TORQUE` as
> serial counts at unity gain into its authority clamp. An openpilot that sets it without also
> changing `lateralParams` to `[[0, 239], [0, 239]]` pins the board at full authority from the
> first frame. The two must change together, in a commit of their own. In the DBC the bit is
> held at zero by `SET_ME_X00_3`.

The LDW bits are sent whether or not openpilot is steering: a lane-departure warning is a
warning, not a request.

> **The LDW bits currently reach nothing.** `SP-PROTOCOL-V3` section 4 specifies the board
> copying byte 2 bits 5:4 into serial camera-to-EPS byte 2 bits 5:4, and firmware `875ba124`
> does not implement it. `gw_active.c:759-773` is the whole of the board's `0x0E4` parse and
> reads only bit 7 and bit 2; `lkas_uart.c:449` builds serial byte 2 as
> `0x80 | (lkas_on ? 0 : 0x40)`, hard-zeroing bits 5:4 on every transmitted frame. The `0x500`
> `LDW_ACTIVE` path is no better: `sp_hud.c` decodes it and `sp_hud_merge_lkas()` never reads
> it, so `RDM_HUD` and `BEEP` are never raised from it either. openpilot sends the bits anyway
> — they are harmless and let the firmware side land without another openpilot change — but a
> lane departure produces **nothing the driver can see or hear** until the board decodes them.
> Checking `0x0E4` in the comma log verifies openpilot's transmission and nothing further.

## Releasing on the brake

The stock camera drops `LKAS_ON` within about 20 frames (0.2 s) of a brake press — measured
three times on route `000000c8`. `carcontroller.py` imitates that with a ceiling on |torque|
that walks linearly to zero over `BRAKE_RELEASE_FRAMES = 20` and snaps back the frame the
brake lifts.

It is **imitation of stock, not fault avoidance**: the same logs show the EPS tolerating brake
plus a non-zero torque request on 63 frames with error state 0 throughout, so the older
"braking while requesting torque latches an EPS fault" claim is not reproduced.

Two properties are deliberate and worth not breaking:

* **It can only reduce the command.** It is `clip(x, -c, c)` with `c ∈ [0, 1]`, applied after
  the ordinary rate limit and skipped entirely at `c == 1.0`, so a non-braking frame is
  bit-identical to what it was before.
* **It cannot latch.** The only state is a frame count, and the count is *cleared*, not
  decayed, the first frame `brakePressed` is false. Recovery afterwards goes through
  `STEER_DELTA_UP` like any other request.

A ceiling rather than a gain because a gain compounds with the rate limiter — which keeps
pulling back toward the full request — and makes the first steps of the withdrawal bigger than
the last. The ceiling makes it exactly linear at 2560/20 = 128 CAN counts per frame, which the
board scales to 4 serial counts at authority 80: under the stock camera's p99 of 5 and well
under the 16 it has ever stepped.

### `RELEASE_DRIVER` is advisory; there is no matching ramp

`RELEASE_BRAKE` has an action. `RELEASE_DRIVER` does not: on `steeringPressed` openpilot sets
`OP_STATE = WITHDRAWING` and `RELEASE_DRIVER = 1` while `apply_torque` and
`STEER_TORQUE_REQUEST` stay exactly what they were the frame before. That is deliberate —
openpilot keeps steering through `steeringPressed` as it does on every Honda, and the
driver-torque blend and the override gate (`INH_DRIVER_OVERRIDE`) are the board's. So the bit
means "the driver is on the wheel", not "I am ramping out". Firmware `875ba124` stores it and
never reads it, so nothing disagrees today; **the moment the board acts on it, the ramp has to
be added on this side in the same change**, or the two sides will each believe the other is
holding the corner.

## Validating `0x500`

Identical to any stock Honda message, so existing Honda checksum code works unchanged:

```c
uint8_t honda_checksum(uint16_t addr, const uint8_t *d, uint8_t len) {
  uint16_t s = 0;
  uint16_t a = addr;
  while (a) { s += a & 0xF; a >>= 4; }
  for (uint8_t i = 0; i < len; i++) {
    uint8_t x = d[i];
    if (i == len - 1) { x >>= 4; }   // last byte: high nibble only, checksum lives in the low one
    s += (x & 0xF) + (x >> 4);
  }
  return (uint8_t)((8 - s) & 0xF);   // the +3 extended-ID case cannot apply: 0x500 <= 0x7FF
}

bool sp_hud_valid(const uint8_t *d) {
  return (d[7] & 0x0F) == honda_checksum(0x500, d, 8);
}
```

Reject any frame whose checksum fails. Treat the message as **stale after 300 ms** (3 missed
frames at 10 Hz) and fall back to passing the camera's `LKAS_HUD` through untouched — a stale
side channel must never latch an alert on.

`COUNTER` increments 0→1→2→3→0. A counter that stops moving while frames keep arriving means
openpilot has stopped updating; treat as stale.

### Merging into LKAS_HUD

`LKAS_HUD` (`0x33D`) is **4 bytes** on this car, not the 5 most Honda DBCs describe. Its
`STEERING_REQUIRED` is byte 1 bit 0; `CHECKSUM` is the low nibble of byte 3 and `COUNTER` is
bits 5:4 of byte 3. Recompute both after any edit or the cluster rejects the frame.

## What sunnypilot reads: `0x70B GW_STEER_GRANT` (v3)

`0x704` says whether the board is actuating and has no room left to say **why** it is not.
"openpilot is asking and the car is not turning" is the one failure the driver cannot diagnose
from the seat, so v3 adds a second frame at 10 Hz on bus 0, DLC 8.

| Signal | Byte | Meaning |
|---|---|---|
| `STATE` | 0 | `0` idle, `1` ready, `2` requested, `3` intro, `4` active, `5` limited, `6` refused, `7` board fault |
| `REASON` | 1 | `0` none, `1` no request, `2` openpilot stale/malformed, `3` speed too low, `4` driver override, `5` blinker, `6` brake (**defined, never emitted** — see below), `7` standstill, `8` EPS refused (latched), `9` EPS not acknowledging, `10` serial checksum errors, `11` integrator too large, `12` camera fault, `13` board fault, `14` dry run, `15` soft start in progress |
| `AUTHORITY` | 2 | The board's ceiling in serial counts, **as the board reports it** — `MAX_TORQUE` is folded into this byte only, not into the command |
| `EPS_ACK` / `EPS_LATCHED` / `EPS_ERROR_STATE` / `EPS_FRESH` / `CAM_LKAS_ON` | 3 | bits 0, 1, 5:2, 6, 7. `EPS_ERROR_STATE` `4` is this EPS's refusal code, and it latches for the key cycle. **`EPS_LATCHED` is not a latch** — see below |
| `APPLIED` | 4 | int8, serial counts actually on the wire, scale 2 — so **quantised to 2 counts** |
| `MOTOR_TORQUE` | 5 | int8, the EPS's own motor torque, scale 4 |
| `RETRY_IN` | 6 | Seconds until a new request is considered. `0` now, **`255` not this key cycle** |
| `GRANT_COUNTER` | 7 | Free-running, +1 per frame. **Not named `COUNTER`** — see below |

It reaches openpilot on `carStateSP.linbusGateway`, alongside the `0x704` fields:

```
grantValid  grantState  grantReason  granted  authority
epsAck  epsLatched  epsErrorState  epsFresh  camLkasOn
applied  motorTorque  retryIn  latchedUntilKeyOff
```

* **`granted`** is `grantValid and STATE ∈ {intro, active, limited}`. **Absence is never
  permission**: the board only began sending `0x70B` in firmware `75aa91ee`, so an older image
  simply has no grant, and `grantValid` false forces `granted` false.
* **`latchedUntilKeyOff`** is `RETRY_IN == 255`, **and nothing else**. The EPS has given up for
  this key cycle and only an ignition cycle clears it, so the driver should be told rather than
  the request retried into a dead EPS for the rest of the drive. It is deliberately **not
  sticky on this side** — it is whatever the board's latest fresh frame says — so a one-frame
  glitch cannot strand the driver, and the board clearing it clears this.
* **`EPS_LATCHED` is not a latch, and is deliberately not ORed into the above.** Byte 3 bit 1
  is the board's `refusing` flag (`gw_active.c:1391`), a *timed* hold with two lengths
  (`gw_active.c:1017-1022`): `GW_REFUSE_HOLD_MS` = 60 s when the EPS reports an error state,
  but `GW_NOACK_HOLD_MS` = **3 s** when it merely fails to acknowledge within
  `GW_ACK_TIMEOUT_MS` — which this EPS does routinely below about 60 km/h (`HANDOFF.md` 0e).
  `RETRY_IN` is what separates them: the board emits 255 only while `refusing && eps_errst != 0`
  (`gw_active.c:1401-1406`) and otherwise counts the remaining hold down in seconds. Telling
  the driver to cycle the ignition over a three-second hold he cannot even reach the key for is
  the failure this distinction prevents. `carStateSP.epsLatched` carries the bit through as
  "the board is inside its refusal hold" and nothing stronger.
* **`applied` is quantised to 2 counts.** The board packs `(int8)(cmd / 2)` with C truncation
  toward zero (`gw_active.c:1394`), so the `±1` that `SP-PROTOCOL-V3` rule 3 requires of every
  engage onset reads back as `0`. Any "openpilot is asking and nothing is on the wire" check
  built on `applied` will misfire on the first frames of every ramp — use `grantState`, or
  `0x704 CMD_APPLY_STEER`, which is a full int16.
* **`REASON = 6` "brake" is defined in the protocol and never emitted.** `GW_RSN_BRAKE` exists
  at `gw_active.c:234`; the reason ladder at `gw_active.c:1356-1375` has no brake branch and
  the inhibit mask has no brake bit, so a brake-time withdrawal arrives as `1` "no request".
  Do not build a UI that waits for 6, and do not read a `1` during braking as a missing `0x0E4`.
* A change of `(valid, STATE, REASON)` is logged once through `carlog`, so the reason is in the
  route log even where nothing renders it.

> **Round 1 is telemetry only — the car does not yet speak.** Everything above lands on
> `carStateSP.linbusGateway` and in the route log, and the only consumer of that struct in the
> tree is `selfdrive/controls/controlsd.py`, which reads `present` and `actuating` to hold the
> integrator. `granted`, `grantReason`, `retryIn`, `authority`, `epsErrorState` and
> `latchedUntilKeyOff` have **no reader**: the single `carlog.warning` is the whole
> driver-facing surface. So when the board refuses on the next drive, the driver is still shown
> nothing — the refusal is diagnosable afterwards, from the log, which is the point of this
> round. Wiring `grantReason` / `latchedUntilKeyOff` into an alert is round 2, together with
> the board-side work the three "not implemented" notes above call for.

Stale after 50 carstate frames (500 ms), counted in frames because `CarState.update()` is not
handed a clock. A stale frame reports zeros, not the last thing it heard.

### The CANParser trap — read this before adding another gateway message

`0x704` and `0x70B` are registered **explicitly, with `float("nan")`**, in
`carstate.py get_can_parsers()`. Both halves of that matter:

* `nan` sets `ignore_alive` (`opendbc/can/parser.py:179`), and `MessageState.valid()` returns
  `True` immediately for such a message (`:107-109`), so it can never be the state that makes
  `can_valid` false (`:200-211`). A message reached **lazily** through `cp.vl["..."]` is
  registered by `VLDict` on first access (`:117-125`) with `freq=None`, learns its own rate
  after three frames and takes a **10× period timeout** (`:92-96`, `:181-186`). A gap longer
  than that marks the parser invalid, which feeds `canValid` and makes openpilot **refuse to
  engage**. These frames are allowed to be absent: the board can be unplugged, its low-priority
  telemetry has been observed blacked out for **94.5 s**, and `0x70B` does not exist at all on
  older firmware.
* Registering before the first `update()` means the first frame is not dropped. `VLDict` only
  registers on first access, which for a message read from `CarStateExt` is after that frame's
  packets have already been parsed.

And **neither frame may carry a signal named `COUNTER` or `CHECKSUM`**. In a `honda_` DBC those
names make the parser enforce Honda counter continuity and a Honda checksum
(`opendbc/can/dbc.py:218-227`, `parser.py:66-73`) that the board does not compute for these
frames — every frame would be dropped. That is why the counter is `GRANT_COUNTER`.

## Versioning

`PROTOCOL_VERSION` is `3`. The board accepts v1, v2 and v3; v1 means "no controller state" and
v2 means "no control request", so either side can update first *downward*. Bump the version
only when the meaning of an existing bit changes; adding a signal in a reserved byte does not
need one, because an older receiver ignores those bytes.

> **The version is a hard gate on the receiver, so it must never lead the flashed firmware.**
> The board rejects a frame whose version is above the maximum it knows
> (`sp_hud.c sp_hud_rx` / `SP_HUD_VERSION_MAX`) — and rejecting `0x500` silently takes the HUD
> merge *and* the board's integrator guard down with it. Board firmware `75aa91ee` is the first
> that accepts 3. Flash the board first, then raise `SP_HUD_PROTOCOL_VERSION`.

## Panda

`0x500` is in both ELESYS TX allowlists in `opendbc/safety/modes/honda.h`, on bus 0, with
`check_relay = false`. Without that entry the panda silently blocks the frame — the first thing
to check if nothing arrives. `0x704` is receive-only and needs no safety entry.

## Verifying it worked

On the next drive with v3 deployed, in the comma log:

1. `0x500 PROTOCOL_VERSION` is **3**, checksum and counter 100%, and the board's `0x704` /
   `0x70B` keep arriving — a version the board rejected would show up as the HUD merge going
   dead, not as a missing frame.
2. `0x500 INTEGRATOR` should sit near **0 for the whole drive** in a dry run (frozen from the
   start), instead of ramping to +0.85 as on `000000b3`.
3. `INTEGRATOR_FROZEN` should be `1` whenever `0x704 ENGAGED && !DRY_RUN` is `0`.
4. In the board's own `0x704`, `INH_ENGAGE_GUARD` should be **0** when openpilot first
   requests — if it stays `1`, look at `INTEGRATOR`.
5. `0x70B` present at 10 Hz, `STATE` reaching `active`, and `REASON` explaining every second in
   which it does not.
6. `0x500 OP_STATE` walks `ready → requesting → active` and back, and `MAX_TORQUE` is 0 on
   every frame.
7. `0x0E4` byte 2 bit 2 (`SERIAL_DOMAIN`) is **0** on every frame, and bits 5:4 track the lane
   departure warnings **in openpilot's own transmitted frame**. That is all this check can
   prove: the board does not decode those bits yet, so do not also expect a warning at the
   cluster or on the serial line.
8. Brake presses during an engagement: the command reaches 0 within 200 ms, and comes back
   afterwards.

`carStateSP.linbusGateway` is in the route log too, so `present`/`valid`/`actuating` can be
checked straight from the parquet without decoding CAN.

## Which bus, and how that was found out

Up to route `000000b9` the frame went out on `CAN.camera` (bus 2). On most Hondas that is the
camera's bus, but on this car the comma harness splits the **Elesys radar** off, so bus 2 is
the radar branch and the board — in line with the LKAS camera — is on bus 0 with everything
else. The route log makes it unambiguous: `0x500` appears only as `src 130` (the panda's own
TX echo on bus 2) and never as `src 0`, while the board's `0x704` arrives as `src 0`. The board
had therefore never received a single `SP_HUD_STATUS`; the lanes seen on the cluster were the
camera's stock frame forwarded intact, not a merge.

The fix is one argument at the call site in `carcontroller.py` (`self.CAN.camera` →
`self.CAN.pt`) plus the bus in **both** `CanMsg` entries in `honda.h`. The frame itself is
identical. `0x704` was parsed off bus 0 (`Bus.pt`) all along, which is why the integrator
hold worked while the HUD merge did not.
