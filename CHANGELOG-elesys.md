# Honda ELESYS (2015 Accord AU) — longitudinal work

Everything below is uncommitted working-tree state. Two repos: the parent
(`sunnypilot`) and the submodule (`opendbc_repo`). **Commit `opendbc_repo` first**, then the
parent, so the parent records the new submodule SHA — committing only the parent leaves the
pointer stale and nothing changes for a build.

Grouped so it can go in as one commit per side, or split by section.

---

## 1. Gear detection — the car reported "drive" while parked

`opendbc/car/honda/interface.py`, `opendbc/car/honda/carstate.py`,
`opendbc/dbc/generator/honda/_gearbox_legacy.dbc`

`transmissionType` was decided by looking for `0x191` / `0x1A3` only. This car has neither —
its gearbox is the legacy `GEARBOX_AUTO` at **`0x188`**, from `_gearbox_legacy.dbc`, which no
other platform imports. So it fell through to `manual`, and carstate then hardcoded
`gearShifter = drive|reverse` off `REVERSE_LIGHT`. **The car told openpilot it was in Drive
while sitting in Park.**

- `interface.py`: added a `0x188` branch, **scoped to `HONDA_ELESYS`**. `ACURA_RDX` is
  misdetected the same way but is an upstream platform with no data here; widening the test
  would silently change its gear reporting too.
- `carstate.py`: new `update_gear_elesys()`. Raw `GEAR_SHIFTER` is one-hot — P=1, R=2, N=4,
  D=8 — and **raw 0 is ambiguous: it means both Sport and "lever between detents"**. Only
  duration separates them, so 0 holds the last detent and becomes `sport` after a **1.0 s
  dwell** (`SPORT_DWELL = 100`), ~2× the longest transient ever observed. `GEAR == 26` is
  wired as an instant fast path but has never been seen on this car, so the dwell is what is
  relied on. Leaving Sport is immediate and re-arms the counter.
- `_gearbox_legacy.dbc`: dropped the `0 "S"` VAL entry, since a decoder that maps 0 straight
  to Sport emits a phantom Sport on every shift. Documented why in a `CM_`.

Measured basis: 473k frames of `0x188`. Every 0 run in the recording was a transient — 47 of
them, median 3 frames (30 ms), max 520 ms, all below 0.5 m/s — because Sport was simply never
selected. Across all 14 routes (418 min): D 92.5%, P 6.2%, R 1.2%, N 0.03%, S 0.

Verified on real CAN through the real decode path: park 2.68%, reverse 8.04%, drive 89.02%,
neutral 0.24%, sport 0.

**Behaviour change:** openpilot will now correctly refuse to engage in P/N. In Sport,
always-on DM is suppressed (`always_on_valid = always_on and not wrong_gear`); Sport itself
stays engageable since Honda's `DRIVABLE_GEARS` includes it.

## 2. ECON — mapped and wired

`opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc`, `carstate.py`,
`opendbc/sunnypilot/car/honda/dynamic_tuning.py`

`0x221` is `ECON_STATUS` on other Hondas but is a **3-byte** frame here, not the 6-byte one
the other DBCs describe — so their `ECON_ON_2` (37|2) and COUNTER/CHECKSUM (45|2 / 43|4) fall
past the end of the frame. Correct layout for this car:

```
BO_ 545 ECON_STATUS: 3 SCM
 SG_ ECON_ON  : 23|1@0+
 SG_ COUNTER  : 21|2@0+
 SG_ CHECKSUM : 19|4@0+
```

Validated against 628,053 logged bus-0 frames: bytes 0–1 always `0x00`, byte 2 exactly
`(COUNTER << 4) | (3 - COUNTER)`, giving the four payloads `000003 / 000012 / 000021 /
000030` at 25.00% each, with COUNTER decoding 0,1,2,3 exactly once. `ECON_ON` reads 0 in all
four — ECON was simply off for the whole 7 hours, which is why the bit looked dead.

ECON-**on** payloads the parser accepts (for confirming on the next log):
`00008b / 00009a / 0000a9 / 0000b8`.

Decoded in carstate as `CS.econ_on` (on the CarState object, not `CS.out` — `CarState` is a
capnp struct we don't extend). The tuner reads it via `_econ_state()`; `LEARN_ECON = (False,)`
means learners freeze whenever ECON is on, and an ECON change resets the dwell.

The ECON **button** at `0x37C` bit 48 is deliberately unused: it's momentary, what gates
learning is the state, and `0x37C` is `CRUISE_PARAMS` in the shared `_nidec_common.dbc` so a
signal there would reach every Nidec Honda for no benefit.

## 3. AEB / CMBS bits

`opendbc/dbc/generator/honda/_nidec_common.dbc`, `carstate.py`

Three previously-unmapped bits in `BRAKE_COMMAND`, from owner bit-level analysis:

| signal | bit | meaning |
|---|---|---|
| `CMBS_BRAKE` | 10 | CMBS actively braking |
| `CMBS_DISABLED` | 12 | CMBS disabled |
| `AEB_REQ_3` | 27 | the request bit that actually asserts on this car (`AEB_REQ_1` at 29 does not) |

They went into the shared `_nidec_common.dbc` because the generator can't override an
imported message; there's precedent there already (`BRAKE_PUMP_REQUEST_HYBRID`,
`COMPUTER_BRAKE_HYBRID`). **Read-only — openpilot never sets them, so nothing any platform
transmits changes.** `AEB_STATUS`'s existing VAL table already had `1 "aeb_braking"`.

`stockAeb` for ELESYS now ORs in `CMBS_BRAKE`. Deliberately **widened, not narrowed**: a
false `stockAeb` only stands openpilot down, a missed one lets it fight the factory system.
`AEB_REQ_3` is defined but not yet in the test — it wants a logged event to size against.

## 4. `is_metric` on BRAKE_COMMAND

`opendbc/car/honda/hondacan.py`

Bit 31 is the cluster units flag on ELESYS (0 = metric, 1 = imperial) — same meaning as
`ACC_HUD`'s `IMPERIAL_UNIT`, and named exactly that in the owner's own DBC. It's a reserved
constant 1 on every other Nidec Honda, which is why the shared DBC still calls it `SET_ME_1`.
Replaced the conditional reassignment with a one-line derivation matching the `ACC_HUD` idiom.

Verified on the wire: ELESYS metric→0, imperial→1; `HONDA_CIVIC` stays at 1.

## 5. Dynamic longitudinal tuner (new, off by default)

`opendbc/sunnypilot/car/honda/dynamic_tuning.py` (new, ~950 lines),
`carcontroller.py`, `common/params_keys.h`, `cruise.py`

Self-learning longitudinal tuning ported from MVL's ACURA_MDX_3G branch and restructured.
Gated behind **`HondaDynamicTuningEnabled`** (default off) and its child
**`HondaDynamicPcmBlendEnabled`** (default off). Both are read once at CarController
construction, so they take effect next ignition.

Channels: per-speed-band pedal gain, PCM gas (behind the blend toggle), aero, brake gain,
pitch feedforward. 15 new `HondaDyn*` params.

Design rules that differ from MVL's original, each the result of a failure mode found in
review:

1. **Nothing learns during transients** — every learner behind a 150-frame dwell.
2. **Only settled, off-rail values persist** — a railed excursion never reaches disk.
3. **Every loaded value is re-clamped** — a hand-edited param can't reach an actuator.
4. **Limits are products, not individuals** — the brake gain and rise rate multiply.
5. **Nothing wound up in one engagement crosses into the next.**

Deliberately not ported from MVL: the `nidec_pid` (it's a second integrator in series with
openpilot's own long PI on the same error), the per-5mph lateral `latFactors`, and MVL's
top-of-file `Params` import (which breaks standalone opendbc — the reference repo can't even
collect its own tests because of it).

Defects found and fixed during review, all with regression tests:

- brake integrator survived a disengage → 1.6× applied open-loop on re-engage for the full
  dwell. Now unwound to the converged estimate whenever `longActive` goes false.
- the learned gain reached the **standstill hold**, which has no feedback and is what
  `stopAccel` was hand-tuned against — gain 0.33 put the hold back at cb 251, 0.50 railed it.
  Hold is now pinned at cb 189 regardless of what's been learned.
- the PCM channel had **no `LEARN_MIN_CMD` gate** — `gas_alpha` railed on grade residual at
  settled cruise in ~3 min and persisted it.
- `gas_factor`'s gradient was weighted by `adjust_accel` while it multiplies `gas_accel`, so
  it **inverted** across the whole mild-lift-off band (a +0.30 error drove it 1.00 → 0.84).
- `_get_float` raised `KeyError` outside its try — a missing param entry would have taken out
  `CarController.__init__`.
- **brake gain made two-sided** (`BRAKE_NEG_LIMIT = 0.15`, so 0.85×–1.60×) so it can actually
  trim the `/2.6` divisor live rather than only ever adding brake. Asymmetric on purpose:
  over-braking is a comfort problem you feel, under-braking is the failure that started this.
- **learners freeze outside gear D and whenever ECON is on**; a mode change resets the dwell.
- **aero learner confined** to the command band where the pedal and brake learners are silent,
  plus a 0.05 noise deadband — it was railing at `WIND_FACTOR_MAX` on 6 of 13 routes because
  the accel error on this car isn't zero-mean.
- the **dwell now watches `target + hill_accel`**, not the planner target alone. The pitch
  fade swings across a stop approach while the target sits still, which fed the brake learner
  +0.0074 per downhill stop against −0.0015 per uphill one — a one-signed artifact.
- the brake gain now **fades out** below `BRAKE_LEARN_MIN_SPEED` rather than being cut at
  standstill. The first version of the standstill fix cured the hold but left a cliff right
  above it: with a converged gain of 0.60, cb 189 at 0.0005 m/s and cb **255** at 0.0020 m/s —
  a 66-count step across 1.5 mm/s with no hysteresis. It also applied the gain open-loop
  through the whole 0.001–1.0 m/s band where the learner is frozen, and above 0.8 m/s not even
  the `stopping` clamp caught it. Ramping instead of cutting fixes both; the step is now 0.

Known and documented, **not** fixed: the pitch feedforward uses a low-pass where Toyota uses
a high-pass, and its 2–5 m/s fade sits inside the PID state. Changing it alters how the car
brakes on grades and wants road data first. The learner-corruption half is handled.

## 6. Brake pump — two independent mechanisms, don't confuse them

`opendbc/car/honda/carcontroller.py`

**(a) Continuous-run branches RESTORED.** The v4 experiment deleted `v >= 2.5 and cb > 200`
and `0.15 <= v < 2.5 and cb > 100`, which silently reverted commit `2905e73d1` *"Fixed Pump
Blind Spot on Saturated Braking"*. The v4 argument was about *frequency* ("2.1% of moving-brake
time"), but the frames it dropped are the maximum-demand ones — and once the command is firm
the rise-trigger is structurally dead, because there is nothing left to rise to. Measured
v3 → v4 on the real 13-route command stream:

| | v3 duty | v4 duty | v3 worst gap | v4 worst gap |
|---|---|---|---|---|
| cb ≥ 200 (firm/railed) | 1.00 | 0.32 | **0.16 s** | **5.50 s** |
| stop approach v < 2.5 | 0.72 | 0.26 | 7.00 s | 10.22 s |

That is the exact signature `2905e73d1` was written to cure (~3 s gaps at max demand bled
0.5–0.7 m/s², achieved/commanded slope 0.59–0.86, then +0.5–1.0 m/s² over target into the
stop). With the branches back: **cb ≥ 200 duty 1.00, worst gap 0.16 s** — identical to v3.

**(b) Graded re-prime deadband, new.** `ELESYS_PUMP_DEADBAND_BP/_V = [0, 60, 200] → [12, 6, 3]`
(was a flat 3). `rise and in_run` re-primes *inside* the current run, so a command drifting up
by ≥3 counts every 0.5 s pins the pump on. Right on a real apply ramp, wrong during ordinary
gentle deceleration: a flat deadband put **48.2% of all moving pump run time into cb < 60** at
a local duty of 0.27–0.37. Graded, that falls to **15.75 → 12.10 min (−23%)** with the worst
gap unchanged.

The two are **orthogonal** — (a) is firm braking, (b) is light braking — which is why both are
in. Net: total pump run **58.9 → 38.1 min** against v3, with firm-braking pressure identical.

Ruled out with numbers: creep cancellation causes only ~5% of run time; stretching the
periodic backstop 12 → 20 s saves 0.3 min of 38.0 **and nearly doubles the worst dry stretch**
(11.48 → 18.42 s), so it must not ship — now recorded as a do-not-do in the code.

Corrected a wrong figure in the existing comment: upstream is **29.9 starts per minute of
brake-commanded time**, not 75. Also recorded that every number in that block is an open-loop
replay over traces from earlier pump tunings, so only the A/B deltas transfer.

## 7. Gas curve raised

`opendbc/sunnypilot/car/honda/gas_interceptor.py`

`ELESYS_GAS_V`: `[0.55, 0.85, 1.10, 1.25, 1.55, 2.20]` → `[0.55, 0.85, 1.20, 1.55, 1.95, 2.75]`
at breakpoints `[0, 3, 6, 10, 15, 20]` m/s. **The launch band is unchanged.**

Settled-frame plant identification (~200k frames, 3 routes, 2 tunes; demand =
`aEgo + g·sin(pitch) + aero(v)` regressed on the interceptor command) says the command
actually needed was **1.38× at 3–6 m/s, 1.30–1.47× at 6–10, 1.59–1.70× at 10–15, 1.51× at
15–20, 1.50–1.75× at 20+**. Nothing physical was capping it: `actuatorsOutput.gas` never
reached 0.9 on any of the 13 engaged routes.

Raised the top three breakpoints ~1.25× — one measured step, not the full ratio, because the
PI and the pitch feedforward close part of the rest.

`PEDAL_GAIN_MAX` stays at 1.8 deliberately. Chained replay across all 13 drives had the
learner **pinning at 1.800** on the 10 and 15 m/s breakpoints while the 20 m/s cell barely
moved; raising the clamp would just chase the same gap more slowly. Fixing the base curve is
what brings the residual back inside it.

**This is the one change that alters how the car accelerates.** Envelope: +0.01–0.04 at
6 m/s, +0.04–0.13 at 10, +0.06–0.19 at 15, +0.09–0.18 at 20+. At 20+ m/s the interceptor now
saturates at accel ≈1.5 where the old curve saturated at ≈2.0 — rare in practice, but hard
highway merges will command full pedal.

## 8. `stopAccel = -0.8` (ELESYS)

`opendbc/car/honda/interface.py`

The default −2.0 lands on top of the ~1.15 m/s² creep offset that
`compute_gb_honda_elesys` already adds, so the standstill hold commanded **cb 253 of 255** —
near max hydraulic pressure — measured directly from `0x1FA COMPUTER_BRAKE` over 23,651
decoded hold frames. −0.8 lands it near **cb 189**.

**Never run on the road:** `carParams.stopAccel` was −2.0 on all 14 logged routes.

If a stop ever creeps, raise toward −1.2 first — do not touch the creep table.

## 9. Stopping-exit debounce

`selfdrive/controls/lib/longcontrol.py`, `selfdrive/controls/tests/test_stopping_debounce.py`

Holds the `stopping` state for 0.4 s so a single frame of `shouldStop` going false — a lead
creeping, a model blip — doesn't release the brake and let the car roll at a light.

Only fires with `HondaDynamicTuningEnabled` set. Only debounces transitions toward a
**launch** (`pid`/`starting`); an earlier version matched any exit from `stopping`, which
included `stopping → off`, so a disengage kept reporting `stopping` and commanding
`stopAccel` for 39 frames — in a file every platform runs. Driver gas always releases
immediately.

Verified byte-identical to upstream with the toggle off.

## 10. UI + params

`selfdrive/ui/sunnypilot/layouts/settings/cruise.py`, `common/params_keys.h`

Two toggles under Cruise. Fixed a crash: the callback took no argument but `Toggle` invokes
it with the new state — every tap raised `TypeError`. And it read the param back to decide,
but `ToggleSP` writes the param *after* the callback returns, so it saw the **old** value —
turning the parent off would have left the PCM blend armed. The child is now `enabled`-gated
on the parent so the interlock holds on both edges.

15 new params. Toggles are `PERSISTENT | BACKUP`; learned values are `PERSISTENT` only — they
change every 60 s, so backing them up would keep a sunnylink backup permanently dirty and
could restore a tune learned on different hardware.

### 10a. A Honda page under Vehicle, so the toggle is findable

`selfdrive/ui/sunnypilot/layouts/settings/vehicle/brands/honda.py`, `cruise.py`

`HondaSettings` was one of the brand classes that never populated `items` (as most still
don't), so **Settings → Vehicle showed nothing on a Honda** and the only way to the tuner was
the bottom of the Cruise list, where a Honda-specific toggle is not where anyone looks for
it. The brand page now carries:

- the same two toggles (same params, so this is a second door onto one setting, not a second
  setting), with the ignition note in the description — the tuner reads them once at onroad,
  so a flip does nothing until the next key cycle;
- **Learned Values** — the six pedal gains laid out against their speed bands, plus the gas,
  aero and brake terms, refreshed once a second (13 params at 60 fps would be 13 file reads a
  frame). Speeds follow `IsMetric`;
- **RESET** — writes every learned param back to its `_PARAM_SPEC` default behind a
  confirmation. Offroad only, re-checked when the dialog returns: the tuner holds the learned
  state in memory and rewrites it every 60 s, so a reset while driving would be undone a
  minute later.

Both panels now re-sync their toggles from the param, **edge-triggered on the param, never
level**: `ToggleSP` reads its param once at construction, so without this the two copies drift
apart until the UI restarts, and with a level sync a tap would snap back for the frame or two
before its non-blocking write lands.

The panel hardcodes the key names and defaults rather than importing the tuner. Importing it
would mean an opendbc import running while the settings screen is being built — the failure
mode being fixed here is an empty settings page, so the fix must not be able to cause one.
`test_honda_dynamic_settings.py` is what keeps the copy honest.

## 11. Tests

- `opendbc/car/honda/tests/test_elesys.py` — 44 tests (was 36). Added pump deadband scaling,
  apply-ramp priming, and 6 gear-decode tests. **Note:** these were initially appended below
  `unittest.main()` and silently never ran; when actually collected, 5 of 6 failed on a real
  bug (capnp enums come back as bare ints, so `str()` gave `'2'` not `'drive'`). Put new tests
  **above** the `__main__` block.
- `opendbc/sunnypilot/car/honda/test_dynamic_tuning.py` — unit checks incl. three rounds of
  review regressions and drive-mode gating.
- `opendbc/sunnypilot/car/honda/test_dynamic_tuning_integration.py` (new) — drives the real
  `CarController`: toggle-off is stock, gas and brake never concurrent, standstill hold is
  gain-invariant, disengage unwinds, crossfade inert with the blend off.
- `selfdrive/controls/tests/test_stopping_debounce.py` (new) — incl. disengage-is-not-debounced.
- `selfdrive/ui/tests/test_honda_dynamic_settings.py` (new) — parses (never imports, so it
  runs without raylib) the two panels, `params_keys.h` and the tuner, and asserts they agree
  on the key names, the defaults, the speed bands, the param types and flags, and that the
  Honda brand page actually publishes its items.

---

## Status

All four suites pass, ruff clean on both repos, all 39 DBCs regenerate, cross-platform sweep
shows no other Honda affected.

**Untested on road: essentially all of it.** Every number above comes from unit tests, an
offline controller harness, or replays over logged CAN. Nothing here has moved a car, and the
dynamic tuner has never executed on the road in any form.

The settings side is the exception to "untested": the panels are covered by the parse-level
test above, but nothing rendered them — this container has no raylib and no display, so the
first look at the actual page is on the device.

Suggested first flash: **both toggles off**, which gives the gas curve, `stopAccel`, the
quieter pump and the corrected gear/ECON/AEB decode. Then enable the tuner separately so the
two are attributable. Leave `HondaDynamicPcmBlendEnabled` off until the stationary PCM test —
openpilot has sent 250,334 `ACC_HUD` frames with `PCM_GAS = 0`, so it has never once been
established that the PCM responds to it at all.
