# SP_HUD_STATUS — sunnypilot → aftermarket HUD module

A sunnypilot-only CAN frame carrying openpilot's alert state to a module sitting in line with
the LKAS camera. Nothing in the car sends or reads it.

| | |
|---|---|
| **Address** | `0x500` (1280), standard 11-bit |
| **Bus** | 2 (camera side of the comma relay) |
| **DLC** | 8 |
| **Rate** | 10 Hz |
| **Byte order** | Big-endian (Motorola), same as every Honda frame |
| **DBC** | `opendbc/dbc/generator/honda/_sunnypilot_hud.dbc` |
| **Platform** | `HONDA_ACCORD_9G_AU` (HONDA_ELESYS) only |

## Why this exists instead of taking over LKAS_HUD

The obvious design is for openpilot to send `LKAS_HUD` (`0x33D`) itself and have the module
block the camera's copy. Don't — it breaks two things that are not obvious:

1. **The lane-departure popup disappears.** `RDM_HUD` lives in `0x33D` and is produced by the
   stock camera's road-departure logic. openpilot has no equivalent, so whatever openpilot
   sent in that frame would simply never set it.
2. **openpilot goes blind to camera faults.** On this platform `carstate.py` reads
   `LKAS_PROBLEM` from `0x33D` **on bus 0**, unlike every other Honda, which reads it from the
   camera bus. If openpilot became the sender, it would read back its own frame and
   `carFaultedNonCritical` would be stuck false forever.

So the camera keeps `0x33D` untouched, and this frame carries openpilot's state alongside it.
The module merges the two.

> **Merge by OR, never by overwrite.** If you replace the camera's flag bits wholesale you can
> mask a genuine `LKAS_PROBLEM` or suppress a real road-departure warning — the same failure as
> taking the message over, just relocated into firmware where it is harder to spot.

## Layout

```
byte 0   7 6 5 4   3        2        1         0
         VERSION   OP_EN    LAT_ACT  LONG_ACT  STEER_REQ

byte 1   7        6        5    4      3      2     1 0
         LDW_L    LDW_R    FCW  SOLID  DASHED  LEAD  ALERT_LEVEL

byte 2   SET_SPEED (km/h, 0 = unavailable, saturates at 255)
byte 3-6 reserved, always 0
byte 7   7 6      5 4        3 2 1 0
         unused   COUNTER    CHECKSUM
```

| Signal | Bits | Meaning |
|---|---|---|
| `PROTOCOL_VERSION` | b0[7:4] | Currently `1`. See versioning below. |
| `OP_ENABLED` | b0[3] | openpilot is engaged. |
| `LAT_ACTIVE` | b0[2] | openpilot is steering. |
| `LONG_ACTIVE` | b0[1] | openpilot is controlling speed. |
| `STEERING_REQUIRED` | b0[0] | **openpilot** wants hands on the wheel. Not the camera's nag. |
| `LDW_LEFT` | b1[7] | Lane departure, left. |
| `LDW_RIGHT` | b1[6] | Lane departure, right. |
| `FCW` | b1[5] | Forward collision warning. Takes priority over `STEERING_REQUIRED`. |
| `SOLID_LANES` | b1[4] | Draw solid lane lines (openpilot steering). |
| `DASHED_LANES` | b1[3] | Draw dashed lane lines (lanes seen, not steering). |
| `LEAD_VISIBLE` | b1[2] | A lead vehicle is being tracked. |
| `ALERT_LEVEL` | b1[1:0] | `0` none, `1` info, `2` warning, `3` critical. |
| `SET_SPEED` | b2 | Cruise set speed in **km/h**, always — never the cluster's display units. |
| `COUNTER` | b7[5:4] | 0–3, increments each frame. |
| `CHECKSUM` | b7[3:0] | Standard Honda 4-bit checksum. |

`ALERT_LEVEL` is a severity hint for choosing a chime. It is derived from the flags, never a
substitute for them — always read the specific bit you care about.

## Validating a frame

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

`COUNTER` increments 0→1→2→3→0. A repeat means a duplicated frame; a skip means a dropped one.
Neither is fatal on its own, but a counter that stops moving while frames keep arriving means
openpilot has stopped updating, and should be treated as stale.

## Suggested merge

```c
// camera frame already in buf[], about to be forwarded
if (sp_fresh && sp_valid) {
  if (sp.STEERING_REQUIRED) { buf[1] |= (1 << 0); }   // LKAS_HUD STEERING_REQUIRED, bit 8
  // ... other bits, always OR
  honda_recompute(buf, 0x33D, 4);   // 4-bit checksum + 2-bit counter must be redone
}
```

`LKAS_HUD` (`0x33D`) is **4 bytes** on this car, not the 5 that most Honda DBCs describe. Its
`CHECKSUM` is the low nibble of byte 3 and `COUNTER` is bits 5:4 of byte 3 — the same
positions relative to the end of the frame as this message. Recompute both after any edit or
the cluster rejects the frame.

## Versioning

`PROTOCOL_VERSION` is `1`. Bump it only when the meaning of an existing bit changes. Adding a
signal in one of the reserved bytes does **not** need a bump, because a v1 receiver ignores
those bytes. Firmware should refuse to act on a version it does not recognise rather than
guessing.

## Panda

`0x500` is in the ELESYS TX allowlists in `opendbc/safety/modes/honda.h`, on bus 2, with
`check_relay = false` — nothing else on the car sends it, so there is no stock module whose
isolation needs verifying. Without that entry the panda silently blocks the frame, which is
the first thing to check if nothing arrives.

## If your module taps bus 0 instead

The bus is chosen at the one call site in `carcontroller.py`
(`self.CAN.camera`). If your module sits on the powertrain side rather than the camera side,
change that to `self.CAN.pt` and change the bus in **both** `CanMsg` entries in `honda.h` to
`0`. The frame itself is identical either way.
