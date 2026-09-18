# CHANGELOG — serial steering for HONDA_ELESYS

openpilot-side changes for the 2013–2015 Accord with the EPS-LKAS gateway board,
which translates openpilot's `0x0E4` onto the car's 9600-baud LKAS serial link.
Newest first. Routes are sunnypilot routes on `15646e8515eda1a7`.

Two repositories move together: this one and `SoRadGaming/opendbc` (the
`opendbc_repo` submodule). Both land on **master**, because the comma only runs
what master has.

The board's own history is in `S:\Software\EPS-LKAS\CHANGELOG.md`. The protocol
is `docs/SP_GATEWAY_FIRMWARE.md`.

---

## 2026-09-18 — `ef4f294` · MADS follows the board's hand-back

When the gateway releases the wheel on driver torque, MADS now turns off, exactly
as if the LKAS button had been pressed. The driver re-arms it when they want it.

The board *has* to release — this EPS latches when it is overpowered while
commanding. But `latActive` stayed true, the command kept going out and the
cluster kept showing lateral engaged, so the driver was told the car was steering
when the wheel was theirs. On routes dd and de the board was released and
openpilot was still asking on **100% of frames above 110 counts** of driver
torque.

Trigger is `carStateSP.linbusGateway` reason 4 (driver override) with the grant
valid and not granted. Not a hair trigger: the board debounces first, and the
episodes are real — 28 on dd and 21 on de, median **7.1 s** and **8.7 s**, and
not one under a second on either drive. `granted` is false whenever `grantValid`
is, so an old or silent board can never fire it; `present` keeps it off every
other car. `selfdrived` gained `carStateSP`, published unconditionally at 100 Hz.

## 2026-09-17 — `56a4043`, `7da0d8c` · the latched torque sensor

**The EPS stops updating `STEER_TORQUE_SENSOR` (`0x18F`) while it is under LKAS
control.** The frame keeps arriving at 100 Hz with a rolling counter and a valid
checksum, but the torque bytes hold whatever the driver was doing when the
gateway engaged. Routes dd/de/df: frozen for up to **946 s** while the wheel
moved −13.5 to +7.4°, on **41–69% of each drive**, `canValid` 1.00 throughout.

Two consequences, both fixed:

* **`carStateSP.driverTorqueStale`** — true means nothing may infer driver intent
  from `steeringTorque` this frame. `desire_helper` honours it, because a latched
  value makes every lane change in whichever direction it points fire on the
  first frame of `preLaneChange`, and every lane change the other way impossible.
  Measured: 14 of 17 confirmations landed in a single 0.05 s sample, and all 10
  failures were the direction the latched sign opposed.
* **Driver torque from the board's EPS mirror.** `EPS_LIN_RAW` (`0x700`) is one
  CAN frame per serial frame — 100 Hz, the same rate as the message it stands in
  for. Conversion measured by least squares over the 30,482 samples where both
  signals were live: `0x18F = −64.52 × STEER_TORQUE`, R² **0.9991**, residual RMS
  134 CAN counts against a 600–1200 count threshold. Converting into the CAN
  domain rather than rescaling every threshold keeps `STEER_THRESHOLD`, torqued,
  driver monitoring and the lane-change nudge working unchanged.

## 2026-09-16 — `715ea5d` · lane-change nudge threshold 600

`STEER_THRESHOLD` for `HONDA_ACCORD_9G_AU`, matching the 11G Accord and six other
modern Hondas. On route d9 the eleven `preLaneChange` windows that armed a lane
change peaked at **1556–5839** counts in the wanted direction; of the ten that did
not, six peaked between 600 and 1225 — one of them **937 after nine seconds** of
trying at 60 km/h. The gap between 1225 and 1556 is empty, so 600 catches the
weak nudges without touching one that already worked.

Why this car needs it: below the EPS's 50 km/h floor the wheel is unassisted and
a nudge easily passes 1200, which is why lane changes worked at low speed and
failed at cruise. With 160 counts of assist in the wheel the same nudge lands
around 900.

## 2026-09-16 — `bcb9f95` · lateral stays armed at a stop

`steerAtStandstill` for HONDA_ELESYS. `controlsd` computes
`standstill = abs(vEgo) <= max(minSteerSpeed, 0.3) or CS.standstill` and gates
`latActive` on it, so `STEER_TORQUE_REQUEST` on `0x0E4` dropped at every red
light — which the board reads as "not armed" and blanks the lane graphic. Stock
keeps the dashed lanes up.

Nothing steers at a stop: the board holds its command at zero below 5 km/h and
reports STANDSTILL. This only keeps the *request* alive so the graphic can follow
it.

## 2026-09-14 — `39b8575`, `1da246a` · the trim, and the delay

**The steering trim is carried across a gateway hold instead of zeroed.**
`torqueState.i` is positive in all 14 lateral engagements on routes d3/d4,
settling +0.042 to +0.204 m/s² and never once negative — a real one-signed trim,
about ten serial counts of right-hand torque against a persistent left pull. The
two engagements that started from zero took **14.2 s and 14.1 s** to reach 63% of
it, and the board was actuating for only 48% / 65% of laterally-active time, so
the re-learn was paid over and over. From the driver's seat that is the car
drifting left at every takeover and correcting itself half a minute later.

Bounded two ways: clipped to 0.25 m/s² on the takeover frame and decayed with a
30 s time constant while held. The freeze is untouched, and the freeze is what
makes open-loop windup unreachable.

**`steerActuatorDelay` 0.15 → 0.38 s.** openpilot's own learner read 0.3844 s on
d3 and 0.3829 s on d4, both `estimated`, `calPerc` 100, 5 valid blocks. `lagd`
overrides it frame by frame on a warm device, so this is load-bearing only after
a boot and on a device with `LagdToggle` off.

## 2026-09-12 — `27048eb`, `98f6574` · serial steering round 1

`carStateSP.linbusGateway` — the board's state decoded from `GW_ACTIVE` (`0x704`)
and `GW_STEER_GRANT` (`0x70B`): engaged, dry run, valid, actuating, present, the
grant state and reason, EPS acknowledgement and error state, and
`latchedUntilKeyOff`. Plus the integrator gate that holds the lateral integrator
while the board is not actuating, so nothing is integrated against a car that is
not listening.

Also in opendbc: SP-PROTOCOL v3, the LDW bits on `0x0E4` byte 2 bits 5:4, and the
brake-release ramp that imitates the stock camera's 200 ms withdrawal.

---

## Things about this car worth not rediscovering

* **openpilot has no driver override.** `steeringPressed` freezes the integrator,
  suppresses the saturation warning and arms a lane change. It never stops
  commanding. Neither does panda's Honda safety, which only checks that `0x0E4`
  is zero when controls are not allowed — there is no `driver_torque_allowance`
  as on Toyota or Hyundai. The board is the only thing in the stack that
  disengages, which is why MADS has to be told.
* **The EPS will not acknowledge below 50.4 km/h.** Measured three ways: 30
  zero-torque probe windows, 21 release events across seven routes (all
  50.16–50.79), and 2,953 torque-backed frames below 49 km/h with zero
  acknowledgements.
* **160 serial counts is the EPS's ceiling.** Six consecutive frames above it and
  it latches for the key cycle.
* **`0x18F STEER_TORQUE_SENSOR` is not trustworthy while LKAS is active.** See
  2026-09-17 above.
