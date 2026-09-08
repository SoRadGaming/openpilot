# SP-PROTOCOL — sunnypilot ⇄ LIN-bus gateway

Two sunnypilot-only CAN frames, one in each direction, between openpilot and the aftermarket
board that sits in line with the LKAS camera and translates openpilot's steering request onto
the EPS's LIN line. Nothing else in the car sends or reads either of them.

```
sunnypilot  ──── 0x500 SP_HUD_STATUS v2 (10 Hz, bus 2) ────►  board
sunnypilot  ◄─── 0x704 GW_ACTIVE         (10 Hz, bus 0) ────  board
```

| | |
|---|---|
| **DBC (this side)** | `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc` |
| **DBC (board side)** | `dbc/eps-lkas-gw.dbc` in the gateway firmware repo |
| **Platform** | `HONDA_ACCORD_9G_AU` (HONDA_ELESYS) only |
| **Byte order** | Big-endian (Motorola), same as every Honda frame |

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

## What sunnypilot sends: `0x500 SP_HUD_STATUS` v2

| | |
|---|---|
| **Address** | `0x500` (1280), standard 11-bit — chosen because it is unused on every bus of this car (81 distinct addresses seen; the whole `0x500–0x5FF` range is empty) |
| **Bus** | 2 (camera side of the comma relay) |
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
byte 5-6 reserved, always 0
byte 7   7 6      5 4        3 2 1 0
         unused   COUNTER    CHECKSUM
```

| Signal | Bits | Meaning |
|---|---|---|
| `PROTOCOL_VERSION` | b0[7:4] | **`2`**. See versioning below. |
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
| `COUNTER` | b7[5:4] | 0–3, increments each frame. |
| `CHECKSUM` | b7[3:0] | Standard Honda 4-bit checksum. |

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

## Versioning

`PROTOCOL_VERSION` is `2`. The board accepts v1 and v2 and treats v1 as "no controller state",
so either side can update first. Bump the version only when the meaning of an existing bit
changes; adding a signal in a reserved byte does not need one, because an older receiver
ignores those bytes. Firmware should refuse to act on a version it does not recognise.

## Panda

`0x500` is in both ELESYS TX allowlists in `opendbc/safety/modes/honda.h`, on bus 2, with
`check_relay = false`. Without that entry the panda silently blocks the frame — the first thing
to check if nothing arrives. `0x704` is receive-only and needs no safety entry.

## Verifying it worked

On the next drive with v2 deployed, in the comma log:

1. `0x500 INTEGRATOR` should sit near **0 for the whole drive** in a dry run (frozen from the
   start), instead of ramping to +0.85 as on `000000b3`.
2. `INTEGRATOR_FROZEN` should be `1` whenever `0x704 ENGAGED && !DRY_RUN` is `0`.
3. In the board's own `0x704`, `INH_ENGAGE_GUARD` should be **0** when openpilot first
   requests — if it stays `1`, look at `INTEGRATOR`.

`carStateSP.linbusGateway` is in the route log too, so `present`/`valid`/`actuating` can be
checked straight from the parquet without decoding CAN.

## If the board taps bus 0 instead of bus 2

The TX bus is the one call site in `carcontroller.py` (`self.CAN.camera`). Change that to
`self.CAN.pt` and the bus in **both** `CanMsg` entries in `honda.h` to `0`. The frame is
identical either way. `0x704` is parsed off bus 0 (`Bus.pt`) already.
