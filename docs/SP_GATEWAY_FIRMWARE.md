# LIN-bus gateway firmware: what the board receives, and how to parse it

Companion to [`SP_HUD_STATUS.md`](SP_HUD_STATUS.md) (the SP-PROTOCOL `0x500` / `0x704` contract).
That document is written from openpilot's side. This one is written from the **board's** side: every
packet the gateway receives, how to validate it, what to do with it, and what it must put on the LIN
line as a result.

Sources, in order of authority:

1. **Our own 100 Hz CAN logs** (routes `000000c4`..`000000c9`). Where a reference and a log disagree,
   the log wins.
2. **`mvl-boston/openpilot@lkas-test` + `mvl-boston/opendbc`** — a working openpilot integration of
   this exact hardware on a 2013-2017 (9th gen) Accord. Cloned to `S:\OP\refs\mvl-lkas-test`.
3. **`S:\LKAS\Software\`** — the three reference firmwares: `LKAS_EPS_V3-master` (V3),
   `LINInterfaceV2-master` (V2), `AccordManualSteering-master`.

---

## 0. Read this first: the torque domain is wrong today

Everything else in this document is detail. This is the headline.

`0x0E4 STEER_TORQUE` is a 16-bit signed CAN signal, but **the board does not use 16 bits.** Both
reference firmwares decode it identically:

```c
bigSteer     = (bytes[0] >> 4) & 0x08;   // sign: bit 15 of the int16 -> bit 3
bigSteer    |= (bytes[1] >> 5) & 0x07;   // magnitude bits 7:5, from the LOW byte
littleSteer  =  bytes[1] & 0x1F;         // magnitude bits 4:0, from the LOW byte
```

The magnitude is **the low byte only**. The sign is **bit 15 of the int16**. The high byte is
discarded. So the value the EPS actually receives is:

```
applied = sign(CAN_value) * (|CAN_value| mod 256)
```

openpilot on this car currently runs `lateralParams.torqueBP/torqueV = [[0, 2560], [0, 2560]]` (it
falls through to the `else` branch in `opendbc/car/honda/interface.py`), so `STEER_MAX = 2560`.
Measured from the logs, over every route where openpilot asserted a steering request:

| | value |
|---|---|
| frames with \|STEER_TORQUE\| > 255 | **56 %** on `c8`, 57 % on `c9`, 31-43 % on `c4`/`c5` |
| max commanded | 2560 (maps to 0 on the LIN line: `2560 & 0xFF == 0`) |
| per-frame step, CAN domain | p99 **77**, max 2560 |
| per-frame step, **as the board sees it** | p99 **222**, max **255** |
| stock camera per-frame step, same car | p50 1, p99 **5**, max 16 |
| first non-zero command openpilot sends | **76**, every engagement |
| stock camera first non-zero | **±1**, 105 of 105 onsets |

The first non-zero command is 76 because the rate limiter allows 0.03 of full scale per frame and
full scale is 2560. Four frames later the command passes 256 and wraps. **Measured time from the
first non-zero command to the first wrap: 0.03 s**, in 14 of 18 request bursts; 0.10-0.21 s in the
rest.

That matches the symptom exactly: "every live refusal arrived 0.0 to 0.5 s after the first command".
After the wrap the EPS is handed a sawtooth that jumps by up to 255 counts per frame, forever. The
stock camera has never moved more than 16 counts in a frame. Six consecutive frames of an
out-of-envelope request is the documented way to fault this EPS (see §6.3).

**Fix, on openpilot's side, one line:**

```python
elif candidate in HONDA_ELESYS:
    ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0, 239], [0, 239]]
```

This is precisely what mvl-boston does for `HONDA_ACCORD_9G`:

```python
elif candidate in (CAR.HONDA_ACCORD_9G, CAR.ACURA_TLX_1G):  # source mlocoteta
  ret.steerActuatorDelay = 0.3
  ret.lateralParams.torqueBP, ret.lateralParams.torqueV = [[0, 239], [0, 239]]
  ret.lateralTuning.pid.kiBP, ret.lateralTuning.pid.kpBP = [[0., 20], [0., 20]]
  ret.lateralTuning.pid.kpV,  ret.lateralTuning.pid.kiV  = [[0.4, 0.3], [0, 0]]
```

**The board should not paper over this.** A firmware that clamps instead of wrapping would hide a
control-loop bug behind a saturated actuator. See §4.4 for what to do instead: clamp *and* report it.

---

## 1. How the 2017 Accord integration actually works

Worth stating plainly, because it is much smaller than it looks. The entire mvl-boston integration is
three things:

1. **`lateralParams = [[0, 239], [0, 239]]`** — openpilot's torque domain *is* the serial domain.
   There is no conversion anywhere. `create_steering_control()` is stock and untouched.
2. **A DBC that points `STEER_STATUS` at the board**, not at the car (§7.2).
3. **`STEER_THRESHOLD[HONDA_ACCORD_9G] = 30`** — the driver-override threshold, in serial counts,
   because on their car `steeringTorque` comes from the board's 9-bit LIN value.

**panda is completely unmodified** (submodule pins `commaai/panda` upstream). The Honda safety mode
has no torque magnitude or rate check at all; its only steering check is that bytes 0-1 of `0x0E4`
are zero when controls are not allowed. That check keeps working unchanged in the 239 domain, so
**nothing in panda needs to change** for the domain switch. (The v3 draft's claim that "the panda's
Honda limits are written for CAN-domain torque" does not hold: there are no such limits.)

### 1.1 What is different on our car

| | mvl-boston 2017 Accord | ours (Accord 9G AU, ELESYS) |
|---|---|---|
| native `STEER_STATUS` on CAN | **absent** — board synthesizes it on `0x190` | **present and live** on `0x18F`, 24 982 frames/route |
| driver torque source | board's 9-bit LIN value, ±255 | car's own sensor, ±7000, p50 2693 |
| `STEER_THRESHOLD` | 30 (serial counts) | 1200 default (CAN counts) — **leave alone** |
| lateral controller | PID, `ki = 0` | torque control (`latAccelFactor` 1.69, `friction` 0.21) |
| `steerActuatorDelay` | 0.3 | 0.15 |

So we need their torque domain, and **not** their `STEER_STATUS` replacement or their
`STEER_THRESHOLD`. Our board does not have to synthesize steering feedback at all; the car already
provides it.

Two of their choices are worth copying on the first live drive:

* **`steerActuatorDelay = 0.3`.** They doubled it. The LIN round trip is real latency: openpilot's
  CAN frame lands, the board waits for the camera's next serial frame to borrow its counter, then
  transmits. That is up to one full serial period of delay before the EPS even sees the request.
* **`ki = 0`.** They run the lateral loop with no integrator at all. That is a blunt instrument, but
  it is worth knowing that a working integration of this hardware chose it. Our equivalent is already
  built and is finer-grained: the SP-PROTOCOL v2 integrator hold (`0x704` -> `freeze_integrator`),
  which zeroes the integrator until the board is genuinely actuating and resets the PID on the
  closing edge. Keep ours; do not also zero `ki`.

---

## 2. Frames the board receives

```
openpilot ──0x0E4 STEERING_CONTROL   100 Hz, DLC 5──►  board ──serial──► EPS
openpilot ──0x500 SP_HUD_STATUS v3    10 Hz, DLC 8──►  board
LKAS camera ────────── 4-byte serial frame, 100 Hz ──►  board  (see §5)
EPS ───────────────── 5-byte serial frame, 100 Hz  ──►  board  (see §6)

board ──0x704 GW_ACTIVE              10 Hz────────────►  openpilot
board ──0x70B GW_STEER_GRANT         10 Hz────────────►  openpilot  (v3, new)
board ──0x707 GW_VERSION             10 Hz────────────►  openpilot
```

All CAN frames on **bus 0**. On this harness bus 2 is the Elesys radar branch and the board is not on
it; a `0x500` sent to bus 2 has never reached the board. See `SP_HUD_STATUS.md` §"Which bus".

---

## 3. The two checksums the board needs

They are different algorithms. Do not mix them up.

### 3.1 Honda CAN checksum — for `0x0E4`, `0x500`, and everything the board transmits

4 bits, in the low nibble of the last byte. From `opendbc/safety/safety_honda.h`:

```c
uint8_t honda_compute_checksum(const uint8_t *data, uint8_t len, unsigned int addr) {
  uint8_t checksum = 0U;
  while (addr > 0U) { checksum += (addr & 0xFU); addr >>= 4; }
  for (int j = 0; j < len; j++) {
    uint8_t byte = data[j];
    checksum += (byte & 0xFU) + (byte >> 4U);
    if (j == (len - 1)) { checksum -= (byte & 0xFU); }  // exclude the checksum nibble itself
  }
  return (8U - checksum) & 0xFU;
}
```

### 3.2 LIN serial checksum — for the 4-byte and 5-byte serial frames

One whole byte, always ≥ 128:

```c
uint8_t chksm(const uint8_t *data, uint8_t len) {   // len = 3 for LKAS->EPS, 4 for EPS->LKAS
  uint8_t tot = 0;
  for (uint8_t i = 0; i < len; i++) tot += data[i];
  tot = 256 - tot;
  tot %= 128;
  tot += 128;
  return tot;
}
```

---

## 4. Receiving `0x0E4 STEERING_CONTROL` (100 Hz, DLC 5)

The steering request. Standard Honda framing.

```
byte 0   STEER_TORQUE, high byte (int16 big-endian)  <-- only bit 7 (the sign) is used
byte 1   STEER_TORQUE, low byte                      <-- this is the magnitude
byte 2   flags, see 4.2
byte 3   0
byte 4   bits 5:4 COUNTER, bits 3:0 CHECKSUM
```

### 4.1 Torque extraction

```c
int16_t  can_torque = (int16_t)((bytes[0] << 8) | bytes[1]);
uint8_t  sign       = (bytes[0] >> 7) & 1U;          // 1 = left/negative
uint8_t  magnitude  = bytes[1];                      // 0..255, the ONLY magnitude bits
```

Then split for the LIN frame:

```c
uint8_t bigSteer    = (sign ? 0x08U : 0x00U) | ((magnitude >> 5) & 0x07U);  // 4 bits
uint8_t littleSteer =  magnitude & 0x1FU;                                    // 5 bits
```

### 4.2 Byte 2 bit map — **the two reference boards disagree here**

| bit | our SP-PROTOCOL v3 | LKAS_EPS_V3 | LINInterfaceV2 |
|---|---|---|---|
| 7 | `STEER_TORQUE_REQUEST` | same | same |
| 6 | `WIGGLE_DISABLE` | *not read* (always wiggles) | `disableLinWiggleBitFromCan` |
| 5 | `LDW_RIGHT` | `ldw_enable` (`\|=` into serial B2) | `LKAStoEPS_LDW_Signals` (`& 0x30`) |
| 4 | `LDW_LEFT` | same | same |
| 3 | `TX_RAW_SERIAL` | *not read* | `sendLinWholeDataFrameToCan` |
| 2 | reserved | *not read* | `sendAllLinDataFrameToCan` |
| 1 | reserved | `txAllSerial` | `sendSteerStatusFrameToCan` |
| 0 | `BLEND_DISABLE` | `torqueBlendDisable` | `sendSteerMotorTorqueFrameToCan` |

Bits 7, 5 and 4 are unanimous. **Bits 3:0 mean different things in the two references**, so there is
no "standard" to inherit. Our v3 assignment follows LKAS_EPS_V3 for bit 0 (blend disable) and defines
bit 3 itself. Implement the v3 column and ignore both references for bits 3:1.

### 4.3 Validation, and what failing it costs

Both references agree on the rules; the numbers below are theirs.

```c
// counter must increment by exactly 1 mod 4
bool counter_ok = (rx_counter == ((last_counter + 1U) & 0x03U));
// checksum over all 5 bytes
bool cksum_ok   = (honda_compute_checksum(bytes, 5, 0x0E4U) == (bytes[4] & 0x0FU));
```

| condition | action |
|---|---|
| counter or checksum bad, **1-2 times in a row** | ignore the frame, hold the previous request, increment the error count |
| counter or checksum bad, **3 times in a row** | `LinInterfaceFatalError` — stop actuating, require a restart to clear |
| no valid `0x0E4` for **> 15 ms** | set "late" (report it, keep going) |
| no valid `0x0E4` for **> 50 ms** | drop the request, stop actuating |
| no valid `0x0E4` for **> 55 ms** | fatal |

A single bad frame must **not** drop the request: at 100 Hz that would chatter the LKAS-on bit and
force a new 5-frame intro every time (§5.3).

### 4.4 Range: clamp, and say so

A well-behaved openpilot never sends more than ±239. A misbehaving one does, and today ours does on
56 % of frames. The board must not silently alias it.

```c
if (magnitude > MAX_APPLIED) {            // MAX_APPLIED = min(MAX_TORQUE from 0x500, board authority)
    magnitude = MAX_APPLIED;
    status.over_range_frames++;           // report via 0x70B REASON / a dedicated bit
}
```

Clamping rather than wrapping turns a control bug into a saturated actuator, which is survivable.
Reporting it is what lets the next log say *why* the car steered like that. Never allow the applied
value above **239** regardless of what `0x500 MAX_TORQUE` asks for: 241 sustained for 6 frames faults
the EPS, and the wiggle bit (§5.2) can add 1 to whatever the board decided.

### 4.5 The request bit is a level, not an edge

```c
bool steer_request = (bytes[2] >> 7) & 1U;
```

Additionally, **treat a zero torque value as no request even when the bit is set**. LKAS_EPS_V3 does
this explicitly:

```c
if (apply_steer == 0) {
    status->can.lkasRequest = false;   // "if we're blending we might still have a LKAS REQUEST
}                                      //  which will error EPS on high driver torque"
```

---

## 5. Transmitting the 4-byte frame to the EPS

### 5.1 Frame layout (camera -> EPS direction)

```
byte 0   bits 7:5  counter (0 or 1 only, see below)
         bit  3    torque sign (1 = negative)
         bits 2:0  torque magnitude bits 7:5
byte 1   bits 7:5  0b101  (0xA0) = LKAS ON
         bits 4:0  torque magnitude bits 4:0
byte 2   0x80 = LKAS ON, 0xC0 = LKAS OFF
         bits 5:4  LDW left / right
byte 3   chksm(byte0, byte1, byte2)      // the LIN checksum, §3.2
```

**The counter comes from the camera, not from the board.** Both references borrow
`incomingMsg.counterBit` from the camera's inbound frame. LINInterfaceV2 says it outright:

> Since everything is triggered by the stock LKAS (MCU), the counter bits are synced on those
> messages. So if there is no LKAStoEPS message from the stock MCU, nothing will be sent by this
> device.

This is a structural fact worth designing around: **the board is a slave to the camera's frame
timing.** Its 100 Hz is the camera's 100 Hz. If the camera stops talking, the board must not
free-run. It also means the frame sync heuristic `(byte >> 4) < 4` only works while the counter stays
0 or 1, which is why the LKAS-off table has exactly two rows.

The LKAS-off frames, verbatim from both references:

```c
uint8_t lkas_off[2][4] = { {0x00, 0x80, 0xC0, 0xC0},
                           {0x20, 0x80, 0xC0, 0xA0} };   // indexed by counter
```

### 5.2 The wiggle bit — exactly one side may own it

A non-zero request must never repeat the same LSB on consecutive frames. Confirmed in our own logs:
a non-zero stock value never repeats on consecutive frames, **0 of 254 653**.

```c
if (magnitude != 0 && !wiggle_disable) {
    littleSteer = (littleSteer & 0x1EU) | ((last_lsb ^ 1U) & 0x01U);
}
last_lsb = littleSteer & 0x01U;
```

The two references split on ownership. LKAS_EPS_V3 always does it on the board. LINInterfaceV2 has a
CAN bit to disable it, and its notes say it was removed there because "OP should do this". Our v3
keeps it on the board and gives openpilot `WIGGLE_DISABLE` to take it back.

**Whichever side owns it, exactly one may.** Two dithering stages produce a 2-count square wave. And
note the consequence for limits: the wiggle can raise the magnitude by 1, which is how a firmware
capped at 240 ended up sending 241 and faulting the EPS.

### 5.3 The intro: 5 zero frames

On every transition from LKAS-off to LKAS-on, send 5 frames of **zero torque with the LKAS-on bits
set** before any real value.

```c
if (!lkas_on_prev) intro_countdown = 5;
if (intro_countdown > 0) { bigSteer = 0; littleSteer = 0; intro_countdown--; }
```

Both references do this. Our own stock captures measure the intro at 3 to 64 zero frames, never fewer
than 3, so 5 is inside the stock envelope.

This is also why openpilot must assert its request with zero torque and hold it ~200 ms before
commanding: torque sent during the intro is swallowed, and if openpilot ramps during those frames the
first value the EPS actually sees is already well off zero.

---

## 6. Receiving the 5-byte frame from the EPS

### 6.1 Layout

```
byte 0   bit  4    error state, bit 3 (the MSB)
         bit  3    driver torque sign / bit 8
         bits 2:0  driver torque bits 7:5
byte 1   bit  5    EPS_LKAS_ON  (the EPS's own view of whether LKAS is engaged)
         bits 4:0  driver torque bits 4:0
byte 2   bits 5:4  motor torque bits 9:8
         bit  3    motor torque bit 7
         bits 2:0  error state, bits 2:0
byte 3   bits 6:0  motor torque bits 6:0
byte 4   chksm(byte0..byte3)
```

Frame sync is the same heuristic: a byte with `(b >> 4) < 4` starts a frame.

### 6.2 Driver torque

```c
uint8_t lsb = ~(((data[0] << 5) & 0xE0U) | (data[1] & 0x1FU));
uint8_t msb = (~(data[0] >> 3)) & 0x01U;
int16_t driver_torque = (int16_t)((msb << 8) | lsb);   // 9-bit, inverted so left is positive
```

The inversion is deliberate: openpilot wants left positive. **We do not need this value** — our car
reports driver torque natively on `0x18F` — but the board should still decode it, because it is the
input to any on-board blend and to the "driver is fighting" gate.

### 6.3 Error state: the field that matters

```c
uint8_t eps_error = (((data[0] >> 4) & 0x01U) << 3) | (data[2] & 0x07U);
```

This is `0x70B EPS_ERROR_STATE`. Known values:

| value | bits set | meaning |
|---|---|---|
| 0 | none | normal. The EPS error state was **0 on all 111k+ status frames** of both stock routes. |
| **11** | B0O4, B2O1, B2O0 | over-range. Reached by sending 241 for 6 consecutive frames. Measured by mlocoteta, 2021-03-04. |
| **4** | B2O2 only | **the one we keep getting.** Distinct from 11: the over-range bits are clear. |

Error state 4 remains unexplained by anything measured at 10 Hz. §0 is the strongest candidate we
have: the request the EPS actually received was a ±255 sawtooth stepping up to 255 counts per frame,
which no stock frame has ever resembled. **That hypothesis is testable on the next drive, and testing
it is free:** change the torque domain, drive, and see whether 4 comes back.

Two rules that are **not** the cause, both checked against our own logs and both wrong:

* **"Braking while requesting torque latches an EPS fault"** (AccordManualSteering notes).
  Not reproduced. On `c8` the stock camera requested non-zero torque on 63 frames with the brake
  pressed, and the error state stayed 0 throughout. Stock *does* release fast, dropping LKAS-on
  within about 20 frames of a brake press. Treat brake as a release rule, not a fault rule.
* **"Driver torque above `steerThreshold` (30) latches an EPS fault"** (`LKAS_EPS_V3/common.h`).
  Not reproduced, and `steerThreshold` appears nowhere in the V3 sources — it is dead code there. It
  is live in *openpilot*, as `STEER_THRESHOLD[HONDA_ACCORD_9G] = 30`, where it is the driver-override
  threshold in serial counts. Stock requests torque with `steeringPressed` true on about half of all
  frames.

### 6.4 Forward it

Both references write every received EPS byte straight back out to the camera, unmodified, before
doing anything else with it. The camera must keep seeing a coherent EPS stream or it will fault on
its own.

---

## 7. What the board transmits back

### 7.1 Already specified

`0x704 GW_ACTIVE`, `0x707 GW_VERSION` and the v3 `0x70B GW_STEER_GRANT` are defined in
`SP_HUD_STATUS.md` and in `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc`. The one rule worth
repeating, because the whole integrator contract rests on it: **in a dry run the board still sets
`ENGAGED`**. `ENGAGED` alone means "the board has an opinion", not "the loop is closed". Every
consumer reads `ENGAGED && !DRY_RUN`.

`0x70B EPS_ERROR_STATE` is the field from §6.3, and `0x70B APPLIED` is the value actually put on the
LIN line **after** the clamp, the wiggle and the intro. Reporting the commanded value there would
hide exactly the bug this document exists to find.

### 7.2 What we deliberately do **not** copy

mvl-boston's board synthesizes a whole `STEER_STATUS` on `0x190` and openpilot reads its driver
torque and fault state from there:

```
BO_ 400 STEER_STATUS: 5 EPS
 SG_ STEER_TORQUE_SENSOR : 0|9@1- ...      # driver torque, serial counts
 SG_ LIN_INTERFACE_FATAL_ERROR : 10|1@0+
 SG_ LATE_MESSAGE : 11|1@0+
 SG_ STEER_STATUS : 12|4@1+                # the EPS error state from §6.3
 SG_ LKAS_ALLOWED : 9|1@0+
 SG_ STEER_CONTROL_ACTIVE : 23|16@0-       # the board's applied torque
```

They need it because their car has no EPS on CAN. **Ours does** — `0x18F` is live at 24 982
frames/route with real driver torque and a real status enum. Overriding it with a board-synthesized
copy would throw away the car's own sensor and put a second writer on an address openpilot already
reads. Keep `0x18F`, keep `STEER_THRESHOLD` at the 1200 default, and carry the board's own state on
`0x70B` where it does not collide with anything.

Two bugs in their `txSteerStatus()` worth not inheriting, for anyone reading that code as a model:

* `msg.buf[1] |= (!lkasAllowed << 5) & 0x10;` is always 0. `!x` is 0 or 1, `<< 5` gives 0 or 0x20,
  and `& 0x10` clears it. The intent was `<< 4`, to force a temporary steer fault when LKAS is not
  allowed. As written the forced fault never fires.
* The signal named `LKAS_ALLOWED` carries `!lkasAllowed`. The sense is inverted relative to the name.

---

## 8. Timing and fault summary

Everything the board must time, in one place.

| what | limit | on breach |
|---|---|---|
| `0x0E4` gap | 15 ms | flag late |
| `0x0E4` gap | 50 ms | drop request, stop actuating |
| `0x0E4` gap | 55 ms | fatal, restart to clear |
| `0x0E4` bad counter/checksum | 3 consecutive | fatal |
| `0x500` gap | 300 ms | treat HUD channel as stale; does **not** stop steering |
| camera serial gap | 50 ms | stop actuating |
| EPS serial gap | 50 ms | stop actuating |
| LKAS off -> on | — | 5 zero-torque frames before any request |
| applied magnitude | 239 hard, `min(MAX_TORQUE, authority)` soft | clamp and report |
| applied step | ≤ 10 counts/frame (stock p99 is 5) | clamp and report |

`0x500` going stale must not stop steering. It carries HUD state and the `MAX_TORQUE` negotiation;
losing it should fall back to the board's own authority, not drop the car out of lane keeping
mid-corner. `0x0E4` is the safety-critical channel.

---

## 9. openpilot-side checklist

Not the board's job, but the board's behaviour is undefined until these land. In dependency order:

1. **`lateralParams = [[0, 239], [0, 239]]` for `HONDA_ELESYS`.** §0. Nothing else on this list
   matters until this one is done.
2. **Re-tune after (1), not before.** `STEER_MAX` is the denominator of the whole lateral loop.
   With the shared Honda `STEER_DELTA_UP/DOWN = 3` and `DT_CTRL = 0.01`, the rate limiter allows
   0.03 of full scale per frame, which in a 239 domain is **7.17 counts/frame** — inside the v3
   ceiling of 10, above the stock p99 of 5. If you want stock-shaped, that needs a per-car override
   of 2 (4.78 counts/frame); the constant is shared across all Honda today.
3. **Consider `steerActuatorDelay = 0.3`**, as mvl-boston uses. The LIN round trip is real.
4. **`0x500` v3**: `PROTOCOL_VERSION = 3`, byte 5 state bits, byte 6 `MAX_TORQUE`. Start
   `MAX_TORQUE` at 40, the board's current authority.
5. **`0x70B GW_STEER_GRANT`** into `carStateSP`, and the request state machine from v3 §2
   (ask with zero torque, hold 200 ms, ramp from ±1, withdraw by ramping, 500 ms before re-request).
6. **Keep the v2 integrator hold.** It is already built and already verified: on `b9`, all 2419
   `0x500` frames were v2, `INTEGRATOR` stayed within ±0.02 for the whole drive and
   `INTEGRATOR_FROZEN` was 1 throughout while the board reported `DRY_RUN`.
7. **No panda change.** §1.

---

## 10. What to check in the next log

* `0x0E4 STEER_TORQUE` never exceeds ±239, and the per-frame step never exceeds 10. Both are
  one-line checks over `can.parquet` and both fail today.
* The first non-zero command after a request is ±1, not 76.
* `0x70B EPS_ERROR_STATE` stays 0. If 4 returns *after* the domain fix, the domain was not the cause
  and the answer is in the 100 Hz serial mirror (`0x700`/`0x701`) in the five frames before it — that
  mirror must be enabled in the live image.
* `0x70B STATE` reaches `ACTIVE`, with `REASON` explaining every second in which it does not.
* `0x704`: `ENGAGED && !DRY_RUN` true while steering, and `INTEGRATOR_FROZEN` 1 whenever it is not.
