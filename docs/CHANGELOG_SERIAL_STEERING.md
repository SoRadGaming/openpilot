# CHANGELOG — serial steering for HONDA_ELESYS

openpilot-side changes for the 2013–2015 Accord with the EPS-LKAS gateway board,
which translates openpilot's `0x0E4` onto the car's 9600-baud LKAS serial link.
Newest first. Routes are sunnypilot routes on `15646e8515eda1a7`.

Two repositories move together: this one and `SoRadGaming/opendbc` (the
`opendbc_repo` submodule). Both land on **master**. The comma does not install
from this repo: it runs `SoRadGaming/openpilot`, branch `sunnypilot`, which a
GitHub Action there mirrors from this repo's master once master's CI build and
unit tests pass (`docs/fork/README.md`, "How the car gets updates").

The board's own history is in `S:\Software\EPS-LKAS\CHANGELOG.md`. The protocol
is `docs/SP_GATEWAY_FIRMWARE.md`.

---

## 2026-10-03 — the comma 4 HUD: speed and speed limit, the next lower limit, a stop timer, the planned stop and curve on the right; the gateway icon

**The comma 4 now shows your speed and the speed limit**, top right beside the
confidence ball: your speed as the Accord's own speedometer reads it
(`vEgoCluster`), ~35 px, next to a 60 px Australian speed sign (white, red ring,
condensed black digits). This is the design you picked from the mockups (Layout B,
"speed cluster"), drawn by the same code that rendered them.

* **The next lower limit.** When the next limit is LOWER and within 15 seconds or
  500 m (whichever comes first), a 36 px sign appears under the speed with the
  distance over a bar that shrinks toward it (your pick: both; sunnylink can make
  it the bar or the distance alone). Never a higher limit, never while stopped,
  and only while the limit on screen comes from the map. The distance uses
  sunnypilot's own rounding (`300 m`, `1.2 km`); the map sends it once a second,
  so in between it counts down with your speed and the bar shrinks smoothly
  instead of in 30 m steps on the motorway.
* **School zones keep the round sign.** You said the plate looked out of place:
  the sign is now the same circle as every other limit. When a NSW school zone is
  on, the 40 sign gets two amber lights on its rim that flash in turn, about once
  a second, and an amber **SCHOOL** under it (15 px, the smallest text in the
  mockups). Outside the zone's hours the lights are grey and there is no text.
* **Variable-limit zones** (motorways and tunnels with overhead signs) draw the
  sign like the electronic one: black face, red ring, white digits - a reminder
  that the overhead sign can show less.
* **At a stop** the speed becomes a stopwatch and the time stopped (`0:29`), and
  the orange "take control / resume driving manually" no longer covers the
  screen: it is a small banner top left. **When the car ahead moves off, the full
  orange prompt comes back** (once the radar has seen it moving for 0.4 s, so a
  noisy reading cannot do it) and stays until you go - that is the moment the
  prompt is for. **Every stop starts with the small banner again**, however many
  times the car ahead moved off earlier in the drive. A stop of an hour or more
  (`1:02:05`) shrinks the time to fit rather than running into the banner.
  With the banner setting off, the full prompt covers the screen at every engaged
  stop - and the stopwatch under it; the Stop Timer then shows only while you
  are stopped and not engaged.
* **"Press - to confirm speed limit" now shows which limit:** the green arrow is
  replaced by the limit itself with a dashed ring (not confirmed yet) beside the
  same green minus (or plus). The minus blinks as the arrow did. If you have a
  speed limit offset, it shows on the sign as a small black badge (`5` for +5),
  exactly as sunnypilot's own sign shows it: confirming sets limit + offset, and
  the plus or minus compares your set speed with that sum.
* **Settings > gateway** has its own icon: **B, the inline bridge** (the board on
  the harness, traffic both ways), which you picked. The update dialog keeps the
  download arrow. The icon is stored as an ordinary git file: the repository
  keeps PNGs in Git LFS on sunnypilot's server, which this fork cannot upload to,
  and an icon that never arrived would have stopped the comma 4's screen from
  starting. Should it ever fail to load anyway, the tile falls back to the old
  icon.

**Your right-side request, done as you settled it: "planned stop + curve".** At
the top of the right strip, above the confidence ball, one thing at a time:

* **Planned stop:** an arrow onto a stop line and how far ahead the driving
  model's speed plan comes to a standstill (`20 m`), when that is within the
  next 10 seconds and no car is in front of it (the radar's car inside that
  distance + 10 m means the plan stops behind a car - your dashboard shows
  that). Once a stop is showing, only a car within 5 m past it takes it away:
  a car parked a little past the line, coming and going on the radar, can no
  longer blank the countdown. **White with a solid line only while openpilot is
  driving your speed and the model's plan is the one it follows; grey with a
  dashed line when it is only the model's plan** and openpilot is not braking
  for it (disengaged, chill mode, or another target in charge). It appears after
  0.3 s and goes 0.5 s after, so it does not flicker, and it goes at once in the
  last metre and at a standstill, where the stopwatch takes over. Metres (whole
  metres under 20 m, then 5 m steps), or feet if the comma is set to imperial.
  **The number only counts down** - it goes back up only if the stop moves 5 m
  or more further away, so the plan's twenty re-calculations a second do not
  make it jitter (`9-10-9`, `6-7-8`).
* **It never says why.** Nothing on the comma - the model or any other message -
  knows whether the plan stops for a red light, a stop line, a give-way or a
  queue the radar has not picked up yet, let alone a light's colour (the
  research went through every field). So there are no light or sign icons, as
  you decided.
* **Curve:** a curve arrow, left or right (from the model's predicted path), and
  openpilot's own target speed for the curve, in 5 km/h steps as curve signs
  are (`35 km/h`), while Smart Cruise Control is slowing the car for a curve
  (vision: entering or turning; map: turning) AND it is what limits your speed
  AND its target is below your speed - it appears once the target is 2 km/h
  under it and goes once it is 3 km/h over. On the motorway, where its target
  is your own speed, it shows nothing. It goes **the moment you press the gas
  or the brake** (openpilot is no longer driving the speed), not half a second
  later. Not when your set speed is already lower.
* A planned stop comes before a curve.
* **The confidence ball keeps its size, colours and maths.** While an item shows
  it is held just under it - it moves down only if it would otherwise reach the
  item. The item fades in over 0.15 s while the ball eases down out of its way
  (it used to jump 90 px in one frame, which could read as a sudden drop in
  confidence), and fades out while the ball eases back. With both settings off
  the strip is today's, pixel for pixel.
* **On your 1 Oct drive** (49 min) it would have shown 2 planned stops and 4
  curves, nothing else. The red light on Bringelly Rd: `20 m` grey 4.4 s before
  you stopped, counting down a metre at a time to `2 m` - grey because you were
  driving; engaged on the model's plan it is white. Creeping in the Pyrmont queue
  at 13 km/h: grey `11 m` down to `6 m` (you did not stop: the plan is the
  model's intent, not a promise). The plan touching a stop for one frame 11 s
  later, engaged: ignored. The Northern Road curve: a left arrow, `50`, `45`,
  `40 km/h`, gone the moment you pressed the gas, then `40` to `30` in the
  turn. On the motorway at 84 km/h, through a lane change, where it used to
  show `87 km/h` (the target was your own speed): nothing.
* **On route 110** (before the VSA fault), engaged at a light with no car ahead:
  white `40 m`, counting down to `1 m` without a gap. Before this fix it went
  blank for 0.65 s at `7 m` - a car stopped 8-12 m past the line came and went on
  the radar.

**Everything is a setting**, in sunnylink under **Visuals → HUD** (shown for a
comma 4 only), all on by default: Speed and Speed Limit, Next Lower Limit
(Off / Bar / Distance / Both), School Zone Lights, Electronic Sign in Variable
Zones, Stop Timer, Compact Take Control Banner When Stopped, Show the Limit
in the Confirm Prompt, Planned Stop and Curve Speed. Changes show within a second
(the screen reads them once a second, never per frame). **All of them off is
today's screen, pixel for pixel** - checked over eleven real moments of your
1 Oct drive and in the tests.

* **What it never covers:** the confidence ball itself (only held under the
  right rail's item), the driver-monitor icon (except under
  the stop banner), the wheel, the lanes. The whole group fades out while any
  alert is up and comes back only once the alert has faded away, so the two never
  overlap; and the speed hides for the 2.5 s the stock "MAX" number shows after a
  set-speed change, so two big numbers are never on screen together.
* **Only what is true:** the sign is the limit sunnypilot is using (grey digits
  while it is only holding the last one, as sunnypilot's own sign does); the
  school lights and the electronic look appear only when that limit IS the NSW
  limit (NSW Speed Zones on Live, the road matched or dead-reckoned - never an
  ambiguous match); the next limit is only what the map published.
  **In a tunnel, while the position is dead-reckoned, there is no next limit:**
  mapd deliberately does not publish one there (it reported drops that never came
  on your tunnel passes). The mockup showed one, from the logged look-ahead; the
  build does not.
* **Not built, as you decided:** no left-side bar, no traffic lights or light
  colours, no stop or give-way signs (from the map or otherwise), no following
  distance, no lane-change item - the right rail is the planned stop and the
  curve, and nothing else. The car-ahead icon is dropped, as you asked. The
  mockups' "set 70" line (set speed below the limit) was not among your picks and
  is not built.
* **What does not change:** no alert's text, sound, priority or timing; nothing
  that drives the car. The screen only.
* **Tests:** 71 (the rules, the settings and their once-a-second read, the
  banner through two stops in the order the screen really calls it, the right
  rail's rules - where the plan stops, the debounce both ways, white vs grey, a
  car inside the stop and the parked car past the line, standstill, the number
  only counting down, the curve states, the curve only while the car is slowing
  for it, its 5 km/h steps, the gas and the brake, priority, each toggle, every
  message missing or stale - sunnylink, the markers, the icon - including that
  git holds the PNG itself) and 24 that draw the real onroad view in a headless
  window per state - including "every setting off = the stock pixels", "an
  alert's own setting off = that alert as stock", two stops in one drive, an
  alert fading out, an hour-long stop, the gateway icon replaced by an LFS
  pointer, the rail's item only in the ball's strip, fading in and out, the ball
  held under it and back after, and "Planned Stop and Curve Speed off = the
  stock strip".
* **New params:** `HudSpeedCluster`, `HudNextLimit`, `HudSchoolZoneCue`,
  `HudVariableLimitSign`, `HudStoppedTimer`, `HudStoppedBanner`,
  `HudConfirmLimit`, `HudPlannedStop`, `HudCurve` (backed up with your settings).
  The first start after this update rebuilds, as any `params_keys.h` change does.
* **Watch on the next drive:** the speed matches the dashboard; the next-limit
  sign appears only for drops and only in the last 15 s / 500 m; at every stop
  behind a car - the second and third too - the small banner, the timer
  counting, and the full prompt when that car moves off. A school zone in its
  hours, if your route has one. Driving yourself, at a red light with no car
  ahead: the planned stop grey and dashed, counting down. **Only once the VSA is
  repaired (DTC 32-11) and you use openpilot's speed control again - not before,
  as you decided:** engaged in experimental mode at a red light with no car
  ahead, the planned stop white and counting down; a sharp curve engaged, the
  arrow pointing the right way and the speed close to what the car slows to. The
  white stop and the curve cannot appear without openpilot driving the speed, so
  there is nothing to test there until then.

---

## 2026-10-03 — The VSA's own fault, named on screen; no engagement while the VSA holds it

**What this is for.** On 2026-10-01 the car's VSA (the ABS / stability-control
unit, the one that carries out openpilot's brake requests) faulted twice; Honda
i-HDS read DTC 32-11, "ABS solenoid valve malfunction"
(`S:/OP/incident-2026-10-01/REPORT.md`). openpilot showed "TAKE CONTROL
IMMEDIATELY / Cruise Fault: Restart the Car", and restarting is exactly what does
not clear it: the VSA keeps the fault across a key cycle and re-checks only once
the car is moving (route 113 cleared at 35.3 km/h). Until the module is repaired,
do not use openpilot cruise; this update makes openpilot say so, and refuse.

* **A live fault** (the VSA faulting while you drive): the disengagement is
  exactly what it was - upstream's `accFaulted`, from BRAKE_ERROR, still drops
  openpilot and MADS on the same frame. Only the words change: "TAKE CONTROL
  IMMEDIATELY / Stability Control (VSA) Fault", then a silent banner "VSA Fault /
  Brakes, ACC, CMBS degraded. Have VSA codes read", and a refused engagement
  reads "VSA Fault: Brakes Degraded". 30 s later the EPS escalates (it follows
  the VSA, EPS DTC 85-01); its "LKAS Fault: Restart the car" banner no longer
  replaces the VSA's for the rest of the drive. The VSA's words are there from
  the first frame of the disengagement: card now sends `carStateSP` before
  `carState`, and should the VSA's flag still reach selfdrived one frame late,
  its alert takes over upstream's "Cruise Fault: Restart the Car" from the next
  frame (no second sound).
* **A stored fault** (the lamps already on at key-on): openpilot cannot be
  engaged - neither cruise **nor MADS** - until the VSA clears it. The banner
  reads "VSA Fault Stored / Clears after driving above 35 km/h", and SET, RES or
  the LKAS button get "VSA Fault: Clears Above 35 km/h" (35 km/h is what route 113
  showed - one observation). The moment the VSA clears (half a second later on
  screen; a press inside that half second is still refused, the next one is not),
  everything is available again; nothing engages by itself, so press LKAS (or SET)
  once it has. The flag is set about half a second after openpilot's car process
  starts (about 2.6 s after key-on), well before openpilot finishes starting up
  (about 8 s on routes 111 and 113), so its first screen already names the fault.
  MADS is refused
  on purpose: the EPS refuses torque while the VSA holds the fault (and hard-faults
  30 s after key-on), and the board steers only above 51.5 km/h, well past where
  the fault clears - so there is no speed at which lateral could work with it
  stored. An already-enabled MADS is not switched off by it.
* **No "turn the car off and on" after the VSA clears.** The board reports the
  EPS latched while the VSA holds its fault (the EPS follows the VSA), and its
  0x70B can then go quiet for seconds; on route 113 the EPS-latch alert announced
  "Steering Fault / Turn the car off and on to clear it" as MADS engaged, 0.7 s
  after the EPS had cleared with the VSA. That alert is now hidden while the VSA
  fault is up and forgets any latch when it clears; a real latch afterwards is
  confirmed again from fresh frames and announced as before.
* **Sound:** one prompt per fault - "Stability Control Fault / Brakes, ACC, CMBS
  degraded" for 3.5 s - and none if the fault disengaged something, because then
  the disengagement alarm is the one sound. Banners are silent. A refused SET
  still gets upstream's refuse tone, per press.
* **In the logs:** `carStateSP.vsaFault` (faulting now) and
  `carStateSP.vsaStoredFault` (fault lamps on outside the start-up bulb check), on
  every route including qlogs; the events `vsaFault`, `vsaStoredFault` and
  `vsaFaultAnnounce` in `onroadEventsSP`; a stored fault also shows upstream's
  `carNotReady` in `onroadEvents` (that is the event that refuses the engagement).
* **The bits are provisional** (named from timing; no Honda DBC has them), so the
  rules are narrow: live = 0x1A4 byte 2 bits 2-3, or 0x1EA's inertial-invalid bit
  with BRAKE_ERROR; stored = 0x1A4 b3.3/b4.0/b6.0 (never seen in a start-up bulb
  check, nor anywhere outside routes 110-113) from the first frame, or b3.6/b3.7
  (which the bulb check lights) after a 5 s start-up window, for 0.5 s. 0x1EA's
  counter is not checked, so it can never cost openpilot `canValid`. Two lamp
  bits of this fault (b3.5, b4.1) are deliberately left out: they also sit on for minutes at a time on 45 earlier drives (June
  2026) while the VSA braked normally. Replayed over all 166 logged routes, only 110-113 set
  either flag. Nothing else on any car changes: every other Honda reads both flags
  False, and on every other car the new code adds nothing.
* **Watch:** a normal drive should never show any VSA text, and `carNotReady`
  should never appear in `onroadEvents`. If one does without the cluster's VSA
  lamps, report the route - that is a provisional bit misread.
* **Tests:** opendbc `test_vsa_fault.py` (33: real frames from routes 110, 111,
  112, 113, 10f and the other lamp state on route 69, through the real
  `CarInterface`, a stored start with card starting 2.1 s late, 10f's bulb check
  stretched past the longest seen; 0x1EA's counter; garbage and missing frames; a
  monitor that raises; other Hondas); sunnypilot `test_vsa_fault_alert.py` (47:
  the events, both state machines and MADS, the AlertManager path and its sounds,
  the onset with the VSA's flag one frame late, a live fault with CAN invalid,
  the EPS-latch alert across route 113's clear, the mici text fit, selfdrived's
  and card's wiring, the `CarStateSP` capnp/dataclass agreement). Details:
  `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 6.6 and 10.6.

---

## 2026-10-01 — Longitudinal (braking): a softer final stop; CRUISE_OVERRIDE stays 1

**The last moment of an openpilot stop should no longer grab.** Measured on the
45 stops openpilot completed by itself (routes 3e-103): the brake was already at
the full standstill hold (185 of the hold's 189 counts) when the wheels stopped,
while openpilot was asking for almost nothing (-0.16 m/s²). That is the jolt at
the end: 0.92 m/s² at the stop on average, 1.02 behind a stopped car. Now, while
the car is still rolling in the stopping phase, the brake is held at 125 counts
instead, plus about 17 per degree of downhill; 0.55 s after the wheels read zero
it rises to the usual hold in about a quarter of a second.

* **Where:** it rides on **Dynamic Longitudinal Learning** (the same toggle as
  the stop-release debounce and the gentle brake release), so with the toggle
  off nothing changes. No new setting.
* **What you will feel:** a gentler settle at the end of a stop, and a slightly
  longer final roll: about 0.1-0.2 m and 0.3-0.4 s more. That is an estimate,
  not a measurement - the next drive measures it.
* **What does not change:** the hold itself (189, byte for byte once it has
  risen), launches, all braking before the stopping phase, the brake pump (it
  still runs continuously on the final approach), the learners, and stock
  emergency braking (a lower openpilot brake can only let the car's own AEB
  through earlier). A stop that starts at a standstill gets no ceiling, and
  neither does one that starts faster than 1.2 m/s (about 4 km/h; your 45
  logged stops, and the 32 in the review replay, started at 1.08 m/s at most).
* **When it gives way early** (back to today's brake, rising 5 counts per frame):
  the wheels start turning again after reading zero; the car is not slowing
  (weaker than 0.25 m/s² for 0.4 s, counted only once the brake has sat at the
  ceiling for 0.3 s, so a stop that is still building pressure is not handed
  back); or 1.9 s after the stopping phase began, whatever the wheels are doing.
  So in the worst case it is gone 2.4 s after the stopping phase began (every
  replayed stop rose on `settle`, at most 1.82 s in). The brake pedal, the gas
  pedal and a disengage remove it at once, and the brake is 0 on the first frame
  after a disengage. If openpilot leaves the stopping phase before the car stops
  (to brake harder, say), the ceiling goes at once: it never limits that braking.
* **On your 45 logged stops** (the recorded signals replayed through the new
  code): 44 would have had the ceiling; the brake when the wheels read zero
  drops from 170 to 137 counts (median); 29 of the 44 are on a downhill, and the
  pitch the code uses matches the pitch once stopped (it is the road, not the
  car nosing down under braking); the wheel-zero signal never flickered.
* **New log line:** one per stop, `hondastop rise=<why> t= roll= still= entry=
  cap= ceil=` (`why` = `settle` normally; `moving`, `weak` or `max_roll` when it
  gave way early) or `hondastop end=left` when the stop ended first, or
  `hondastop skip=speed v=` when it started too fast to get a ceiling.
* **The pump question - answered, no change:** the pump is not overused. It runs
  28% of the time a brake command exists; stock ACC ran it 53%. Runs are short
  (median 0.66 s). A suggested "no pump below 30 counts" was dropped: light
  commands do brake the car, and it would have restarted the pump through its
  quiet period.
* **CRUISE_OVERRIDE stays 1 (you asked to remove it).** MVL *does* use it
  (`CC.longActive or CS.out.stockAeb`), and you drove that version in
  June-July. In the logs the bit makes no measurable difference to the VSA's
  braking or to BRAKE_ERROR - but sending 0 has only ever happened in short
  tails after a disengage, never during sustained braking, so removing it would
  be an untested change with nothing to gain. Every BRAKE_ERROR since June was a
  ~1 s silence on 0x1FA, not this bit: on drive 84 the panda was dropping a
  forced minimum brake (so nothing reached the car for a second); on b5-b8
  nothing was sent. The bit is now documented in the code, and a test pins it.
  (The other override bit you tried, `ACC_OVERRIDE_STOP`, was removed earlier,
  in `d9498a1a`; nothing of it is left.)
* **Also fixed:** a speed reading of NaN made the brake code raise, which would
  have meant no brake message and a BRAKE_ERROR a second later. It now holds the
  brake instead. Never seen on the road; found by tests.
* **Not in this update:** the "no coasting in the last meter" change for stops
  without a lead car waits until this one is measured, so each drive tests one
  thing.
* **Your part:** when stopping or stopped, take over with the brake or the gas,
  not the cancel button. Cancel drops the brake at once (the panda insists), and
  the car then creeps - that is the "rolls" in two of your logged cases.
* **Watch on the next drive:** deceleration at the moment the car actually stops
  (camera/IMU, not wheel speed) - target a median of 0.6 m/s² or less and 1.0
  for the worst tenth (today 0.92 and 1.53); no jerk spike in the 0.8 s after the wheels read zero; at
  least 3.5 m to a stopped car ahead (4.1 today); one `hondastop` line per stop,
  nearly all `settle`, and every `weak`/`max_roll` on a downhill or a slow brake,
  and any `skip=speed` a stop that really did start fast;
  no creep while held and no rollback at launch; no 0x1FA gap over 0.1 s; and
  BRAKE_ERROR only in the first frame after power-up. The first drive also
  carries the upstream sync's change to when the stopping phase starts (behind
  a stopped car it should now start at 0.80 m/s, about 3 km/h); no stop since
  the sync is in the logs yet.
* **Tests:** 28 new soft-stop tests (every timer, the hill term, the gate, the
  entry-speed bound, the 1.9 s bound counted from entry, random inputs, never
  raising); through the real controller it only ever lowers the brake, the hold
  is byte-for-byte today's, the learners never see it, other cars, the toggle
  off and a stop started at 1.5 m/s are bit-identical; CRUISE_OVERRIDE is 1
  on every brake message, the brake is 0 after a disengage, and at most one
  nonzero brake message follows a brake or gas press. Details:
  `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 7.7-7.8. Commits: opendbc `cf9ad7ad`
  (the NaN-`vEgo` guard), `c65d5033` (`CRUISE_OVERRIDE`), `8b00f3cc` (the soft
  final stop), `66de8d56` (`FORK.md`), `3a131bf3` (after review: the 1.9 s bound
  counted from entry, the entry-speed bound).
* **Review replay** (the real controller driven from 14 logged routes, 300 min,
  144 min engaged): no exception, no 0x1FA gap, CRUISE_OVERRIDE 1 throughout,
  brake 0 after every one of 453 disengages; the brake when the wheels read zero
  136 counts median with the ceiling against 173 without; the hold 2 s later
  189 in every stop, as today. Re-run after the review fixes: every CAN frame
  identical to the reviewed code in all five set-ups (1.8 million control
  steps), and the same 33 `hondastop` lines - no stop started above 1.2 m/s
  and none reached the 1.9 s bound.

---

## 2026-10-01 — Longitudinal (pedal): the measured gas law; the pedal and aero learners retired

**The comma pedal now asks for the throttle this car actually needs.** The old
law gave 1.4-1.7x too much pedal per m/s² at 22-72 km/h, so a request from a
roll first over-delivered (aEgo 1.34-1.42 against a target of 0.95-1.15 for the
first 2 s) and then sagged while openpilot's own controller unwound. The new
law (v2) uses the pedal response measured on 51 of your routes. It is on by
default; turn it off to get the previous law back exactly.

* **Where:** sunnylink, Vehicle → Honda Settings: **Measured Gas Pedal Law
  (2013-15 Accord)**. Offroad only; it applies at the next drive (the car reads
  it once at ignition). The comma 4's Settings → vehicle card shows which law
  the car will use (`v2`/`v1`), and the `hondadyn` log line says which one ran
  (`gaslaw=`).
* **What you will feel:** a softer, steadier pull from a roll at 20-70 km/h:
  for 1.0 m/s² at 72 km/h the pedal goes 0.742 → 0.467. The lunge-then-sag is
  what goes away; sunnypilot's controller adds whatever is really missing.
  Above about 60 km/h the cruise pedal is the measured one, a little lower than
  before (0.122 against 0.169 at 72 km/h).
* **What does not change:** every launch below 11 km/h (v2 *is* the old law
  there, to within float rounding); the hand-over between gas and brake below about 60 km/h
  (same offset, same slope through the gap - a steeper gap there would risk
  surging in stop-and-go); the point where the pedal reaches zero and where the
  brake comes on (the aero change below moves the latter slightly, with the
  learning toggle on); the brake command itself (identical under both laws); max
  accel (still 1.6 m/s² - raising it was dropped: the 1.6 clip only bound during
  launches that were already overshooting). Every other car keeps upstream's
  pedal law.
* **The self-learning pedal gain and aero factor are gone** (the "Dynamic
  Longitudinal Learning" toggle). The pedal gain could not save anything (its
  progress was reset at every ignition: six weeks moved it by 0.007) and was
  steered by openpilot's own integrator toward the wrong answer. The aero factor
  wandered 0.7-1.5 from drive to drive. **With the learning toggle on, this
  moves the brake-on point slightly:** your saved aero factor was about 0.80
  and is now 1.0, so light braking at 90 km/h gets about 5 counts less brake
  (the brake comes on at 0.27 instead of 0.21 m/s² of net decel). The brake
  learner and the hill term are unchanged.
* **Drive modes (ECON / D / S):** the car now knows which one it is in (S if
  the lever is in S, else ECON if ECON is on, else D) and has a per-mode pedal
  multiplier, all 1.0 today, so nothing changes yet. A change of mode fades the
  pedal over 2 s instead of stepping. There are only 87 s of engaged ECON and
  79 s of engaged S in a month of logs - not enough to fit anything - so the car
  now counts engaged time and steady-pedal samples per mode, in the `hondadyn`
  line (`slot=`, `modesec=`, `modeadm=`, `modetot=`) and on the comma 4 card
  (`D / ECON / S`, minutes). These are counted only while **Dynamic
  Longitudinal Learning** is on (the gas law itself runs either way). **Your
  part:** with that toggle on, drive at least 15 minutes engaged in ECON and 15
  in S, including gentle accelerations at 40-80 km/h.
* **On screen:** the comma 4 card that showed six "learned pedal gain" numbers
  (which could not move) now shows `gas law` - the law the car will use from
  the next drive, e.g. `v2 next drive` - with the brake gain, and the engaged
  minutes per mode. Another Honda (the law does nothing there) sees only the
  brake gain. sunnylink's Cruise page shows engaged seconds per mode instead of
  the pedal gains and the aero factor. **RESET** puts back the brake correction
  only; the minutes per mode are kept (they are a tally of the data, not a tune).
* **Params:** new `HondaElesysGasLawV2` (backed up, default on) and
  `HondaDynModeSecD`/`ECON`/`S`; `HondaDynPedalGain0`-`5` and
  `HondaDynWindFactor` are no longer registered (the old files stay on the
  device, unread). `parse_hondadyn.py` needs no change: the line lost `pedal=`,
  `pedalc=` and `wind=`, and the script skips missing fields.
* **Also fixed:** a fingerprint without the gearbox's `GEAR` signal made
  `CarState` raise (found by fuzzing; the real car always has it). And S is now
  confirmed: the gearbox reports `GEAR = 26` whenever S is selected (16,164
  frames on b1, dd and fc), so the comments that said it had never been seen
  are corrected.
* **Watch on the next drive:** openpilot's integrator (`uiAccelCmd`) at
  demand above 0.4 m/s² should move toward zero (it was -0.21 to -0.23 at
  43-79 km/h), and the first 2 s of a request should no longer overshoot the
  target. At cruise above 60 km/h it should average within about ±0.05; below
  60 km/h it will stay where it was (+0.06 to +0.19), because that offset was
  deliberately left alone until the brake-side work. Check that the pedal does
  not hunt at 15-55 km/h in stop-and-go, and flag any pedal command at or above
  0.9 (the highest seen was 0.761).
* **Tests:** 31 new gas-law tests (golden pedal at every breakpoint, continuity,
  monotonic, nothing below the brake-on point, identical to the old law below
  11 km/h, the gap never steeper, the 2 s fade bound, the modes, the setting,
  other cars bit-identical); the real `CarController` under both laws and with
  NaN/odd inputs (never raises, `0x1FA` every frame it should); the tuner
  checks rewritten for the retirement; 7 new settings checks. Details:
  `docs/fork/CAR-HONDA-ACCORD-9G-AU.md` 9.1-9.2. Commits: opendbc `d2d482ed`,
  `9e076b33`; sunnypilot `db3fa190f`, `d93559f21` (after review: the readout only on
  this car, "next drive" on the card, RESET keeps the minutes, `FORK(...)`
  markers).
* **Review replay** (same inputs through both laws, engaged pedal frames): below
  11 km/h identical (51,742 frames, largest difference 0); 11-22 km/h 0.75x the
  old law's mean pedal, 22-36 km/h 0.69x, 36-108 km/h 0.71-0.84x, above 108
  km/h 0.80x; the brake identical under both laws on every route. Open loop: what
  sunnypilot's own controller then adds is for the next drive to show.

---

## 2026-10-01 — lateral batch: the car's own torque numbers, an honest torque report, a 0.38 s delay fallback

**openpilot's learned steering strength was stuck on a floor borrowed from
another car.** torqued started from the 2018+ Accord's values (via
`substitute.toml`), so it could only learn a lateral-accel factor between
1.18 and 2.20, and it sat on 1.18 on every route from ed to 103. This car
is a different steering system (torque 1.0 = 2560 on `0x0E4` = 160 serial
counts at board authority 160), and torqued's own estimator sees a raw
factor of 0.67 in town to 1.47 on the highway, 0.79-1.35 filtered.

> **2026-10-03: the prior is now `[1.25, 1.25, 0.18]`** (opendbc `e49da403`),
> changed before this batch reached the car. Route 10f, a clean highway
> commute, puts the car's factor at 1.63-1.66 - the estimator from an empty
> cache, a fit on the wire and the controller's own correction agree - and
> 1.1's 1.43 ceiling pinned it there (chained after fc → fd → 103 the raw sat
> on the ceiling 63% of 10f). fc/fd/103 give 1.2-1.5, town routes 0.67-0.9.
> Highway commuting dominates the engaged steering, and a feedforward that is
> too strong at speed is the worse error, so the window now follows the
> highway: **0.875-1.625** (friction unchanged, 0.09-0.27). Town drives are
> clipped at the 0.875 floor by design.
>
> * Replay with 1.25 (the real `TorqueEstimator`, empty cache): fc → fd →
>   103 → 10f is valid 58 min in and ends 1.352 / 1.362 / 1.459, never at a
>   limit; 10f alone is valid at 18 min and ends 1.578, its raw above the
>   ceiling 69% of the time; town first (d5..e2 → fc → fd → 103 → 10f) is
>   valid at 23 min, sits on the floor through e2 and fc (0.882, 0.876) and
>   climbs back to 1.353 by the end of 10f.
> * Day one asks for about 5% *less* torque per m/s² than the 1.183 the car
>   sat on through 103 (10f ended at 1.302), not 7.5% more, so the
>   frames-at-160 estimate below (made for 1.1) should be a high bound.
> * Still one reset: the car goes from the substitute straight to 1.25.
> * Read the checks below with 1.250 / 0.875 / 1.625. A filtered factor
>   within 0.03 of 0.875 is town driving on the floor - now expected, not a
>   warning; within 0.03 of 1.625 is the highway pressing on the ceiling.
> * Details and the full replay table: `docs/fork/CAR-HONDA-ACCORD-9G-AU.md`
>   2.4.

* **Its own prior:** `[1.1, 1.1, 0.18]` in opendbc `override.toml`. torqued
  can now learn 0.77-1.43 (friction 0.09-0.27). Live learning stays on.
  That window holds every filtered value in the replays, but not every raw
  one: its floor clips up to ~24% of a town route's raw samples (d9, fc),
  its ceiling ~1% of a highway route's (fd). No ±30% window can hold
  0.67-1.47 (a ratio of 2.2 against 1.86), so 1.1 is a compromise.
  Day one asks for about 7.5% more torque per m/s² than today's 1.183; the
  board still clamps at 160.
* **One reset, on the first drive.** The new prior throws torqued's saved
  points away once, including points learned at authorities 40-200, when
  "torque 1.0" meant something else each time. Until it has enough points
  again (about 25-60 min of steering above 54 km/h in the replays) it uses
  the prior.
* **The offset survives the reset.** torqued's learned offset (-0.42 to
  -0.50 on every route) cancels about 0.4 m/s² of road crossfall. After a
  reset it restarted at 0, which would have meant a pull after every
  engagement on that drive. The car now carries -0.43 in its CarParams and
  torqued starts from it (`torqued.py`, `FORK(HONDA_ACCORD_9G_AU)`; every
  other car still starts at 0). It also survives EnforceTorqueControl and
  NNLC: their re-run of `configure_torque_tune()` would reset it to 0, and
  sunnypilot's `interfaces.py` (`FORK(HONDA_ACCORD_9G_AU)`) now keeps it.
* **Torque is reported only while the board steers.** While
  `linbusGateway.actuating` is false, `carOutput` reports 0 torque. torqued
  stops learning from frames nothing followed, no "turn exceeds limit"
  alert can fire, and the torque bar shows 0. What goes on the wire is
  unchanged (`0x0E4`, `torqueOutputCan`, the ramp).
* **Steering-delay fallback 0.58 → 0.38 s.** `steerActuatorDelay` is 0.18;
  lagd's fallback and the LagdToggle-off delay both add 0.2. lagd sat at
  0.58 for about 5 h (d7 to fd) before it relearned. Its learned 0.342 is
  kept. With LagdToggle off, Settings shows "0.18 s + 0.20 s = 0.38 s".
* **Unchanged:** full scale stays 2560 = 160 counts (nothing above 160 has
  been sent since d8), and the PID gains and friction threshold.
* **For the future:** any change to the board's `GW_LIN_AUTHORITY` or full
  scale must change the `override.toml` prior too. The prior is torqued's
  cache key, so changing it is what discards points learned at the old
  scale.
* **NNLC:** without the substitute line, its model lookup still picks
  `HONDA_ACCORD.json` (fuzzy). NNLC is off on this car.
* **Check on the next drive:**
  * `lateralTorqueParameters` starts at 1.100 / -0.43 / 0.18 with 0 points.
    It may not be valid by the end of the first drive.
  * The filtered factor within 0.03 of 0.77 (below 0.80) means town driving
    is pressing on the floor (the replays' lowest was 0.793); within 0.03 of
    1.43 the same on the highway (highest 1.347).
  * `carOutput.actuatorsOutput.torque` is 0 whenever `actuating` is false,
    and otherwise matches `carControl.actuators.torque`.
  * No pull at takeovers. The integrator should stay near +0.05 on
    straights.
  * `lateralDelay` reads 0.342 "estimated", or 0.38 if ever unestimated.
  * More frames at 160 are possible, because a lower factor asks for more
    torque: the review replay estimates 0.9-1.0% of actuating frames above
    54 km/h today, 1.0-1.6% on day one. Still nothing above 160.
* **Commits:** opendbc `d288838a` (prior + seed), `bddc6395` (report +
  comment), `1f7c50fd` (delay), `f60e7704` (review: window wording,
  sturdier tests). sunnypilot `c4a6262f7`, `7e9ffe547`, `71b071a2c`,
  `4e606d119` (the seed survives EnforceTorqueControl / NNLC).
* **Tests:** 17 new in `test_elesys.py` (prior, seed, report incl. `0x704`
  through the real CarInterface, the 2560 scale, the delay, the brake
  release step on the wire), `test_torqued_elesys.py` (11),
  `test_latcontrol_reported_torque.py` (7), `test_lagd_elesys.py` (5).
* **Replay (review):** the real CarInterface over 11 routes (4.07 h) put
  byte-identical sends on the wire to the parent commit on every frame, with
  no exceptions, no `0x1FA` gap and no nonzero brake after `longActive`
  dropped.

---

## 2026-10-01 — "restart the car" when the EPS latches; download progress; the rollback branch is gone

**When the EPS latches until key-off, the comma 4 now says so.** Once per latch,
with one short sound: **"Steering Fault"** / "Turn the car off and on to clear
it". After that, a silent **"Steering Off Until Restart"** every 5 minutes for
as long as the latch lasts.

* **Only a warning.** It drops nothing and blocks nothing: cruise keeps working,
  you can still engage, MADS stays as it was. It just tells you steering is gone
  until the key cycle.
* **Never over driver monitoring.** If the EPS latches while the orange "Pay
  Attention" / "Touch Steering Wheel" is up, that stays on screen with its sound;
  the steering fault waits its turn.
* **When it fires:** the board reports the key-cycle latch (`RETRY_IN 255`) for
  1 s of fresh frames. Not on the 3 s "no acknowledgment" hold, not on a
  transient, not when the board is silent, and never on another car.
* **Once, not twenty times.** After a real latch the board's `0x70B` frame keeps
  dropping out for 0.1-25 s at a time (fc, fd: about twenty gaps a drive), and a
  dropped frame reads as "not latched". Only fresh frames count either way, and
  it re-arms only after 3 s of fresh "not latched".
* **It waits until it can be seen:** warnings show only while openpilot or MADS
  is engaged, so a latch confirmed while you are disengaged is announced when you
  engage again.
* **Checked against your drives:** fd, fc, f2 and ed each announce once, about
  1 s after the latch; 102 and 103 raise nothing.

**Settings > software shows what a download is doing.** After you tap "download
update", and also while the comma downloads an update on its own in the
background, the button reads "downloading..." plus one of:

* **code NN%** - fetching the branch, git's own object count. Usually seconds;
  an update under 100 objects shows no number.
* **checking out** - checkout and submodules. No number, because git gives none.
* **os update NN%** - only when the update brings a new AGNOS. The share of the
  image downloaded, by size: the system image is 99% of it, so the number now
  moves with the download. MVL's version counted the seven OS partitions the
  same, so the six small ones raced it to ~91% in seconds and the system image
  crawled the rest. Still no time estimate - nothing here measures time.

Each meter's 100% is always shown, and the progress is gone before "finalizing
update..." as before. The git output still goes to the log.

**"download update" stands out** when an update has been found and is waiting
for your second tap: a green disc behind the icon, a brighter button and white
text (from MVL's branch). Only in that state, so it never mixes with the
progress text.

**The rollback branch `pre-upstream-2026-09` was deleted** from
SoRadGaming/sunnypilot and SoRadGaming/openpilot on 2026-10-01. It could not have
been installed from the device: the updater now on the car reads the AGNOS
manifest at `openpilot/system/hardware/comma/agnos.json`, and that branch is
AGNOS 18.4 with the manifest somewhere else, so the update would have failed. The
upstream sync proved good, so nothing needs it. `pre-*` snapshots still work the
same way; `docs/fork/README.md` now says to check a snapshot can install before
calling it a rollback (a moved `agnos.json` cannot install; a different AGNOS
version alone installs by downgrading AGNOS).

* **Tests:** 19 for the alert (debounce, the fc/fd gaps, deferral, reminder,
  warnings only, never over driver monitoring, the text against the comma 4's
  alert renderer with the real fonts, selfdrived's wiring), 27 for the progress
  (throttle, labels, a real `git fetch`, the AGNOS weighting through `agnos.py`,
  the real `Params`, `fetch_update()` itself), 6 that drive the real button in a
  headless window.
* **New param:** `UpdaterDownloadProgress` (cleared at every manager start).
  The first start after this update rebuilds, as any `params_keys.h` change does.

---

## 2026-09-30 — the fast-wheel takeover is now a setting

**Turning the wheel fast turns MADS steering off (not a pause), and you can now
switch that off or change how fast "fast" is.** Nothing changes unless you
change it: it is on by default at 200°/s, exactly as before.

* **Where:** sunnylink, Steering → MADS Settings: **Turn Off Steering on a Fast
  Wheel**, with **Fast Wheel Threshold** under it. On the comma 4, Settings →
  vehicle: **off on swerve** (ON means a fast wheel turns steering off) and
  **swerve at** (the threshold; tap to step through).
* **Thresholds:** 150, 200 (default), 250 or 300°/s, held for two frames
  (20 ms). In two hours of logged lane keeping on this car the wheel never went
  above 151°/s, so 150 can turn steering off on a hard curve. Any other stored
  value reads as 200.
* **Off:** a fast wheel alone never turns MADS off. The gateway's own pause on
  driver torque is unchanged - a swerve during an override just pauses and
  resumes like any other override.
* **Applies at once,** onroad or off, within 0.1 s; no reboot.
* It still applies to every car, not just this one (`docs/fork/README.md`,
  "Where the fork does not follow those conventions").
* **Tests:** 23 new MADS cases (every threshold, off, a raised threshold
  during an override, live changes, bad values) and 15 settings-agreement
  checks. `LKAS-GATEWAY-PROTOCOL.md` section 9 "Other" has the details.
  This is also the takeover's first changelog entry: it arrived with
  `35622a994` and had none.

---

## 2026-09-30 — map updates from offroad mode, and from sunnylink (`1b05c21d8`)

**Parked with the car on and the device in offroad mode, the update buttons said
"car must be parked".** They checked the ignition, not whether openpilot was
driving. Offroad, openpilot controls nothing, so:

* **Update now** (NSW zones and OSM maps) works whenever the device is
  offroad, ignition on or off. Onroad it answers "offroad only".
* A newly downloaded NSW index is loaded whenever the device is offroad.
  Going onroad cancels a check that is still running.
* **Automatic** downloads are unchanged: parked with the ignition off, on
  unmetered Wi-Fi or ethernet.
* **sunnylink**, Cruise → speed limit: **Update NSW Speed Zones Now**,
  **Update OSM Maps Now** (offroad only; the switch flips back once the
  device takes the request) and **Update OSM Maps Weekly**.
* An OSM request is checked before the download starts: refused onroad, with
  no region set, or while one is already running. A request made long after
  the weekly update was no longer dropped.
* Driving off does not stop an OSM download that has already started - mapd
  has no cancel. The docs and the sunnylink text say so.

## 2026-09-29 — NSW speed zones: the car's limit from Transport for NSW data

**In NSW the speed limit now comes from Transport for NSW's own speed-zone data**
(CC BY 4.0) instead of OpenStreetMap. It covers every public road, local streets
included, plus school zones with their real times and school days. OSM is still
used outside NSW and wherever NSW has no match. It is live by default. Switch it
with **NSW Speed Zones** in sunnylink (Cruise): Live, Log only or Off.

* **Coverage on your 25 drives:** NSW has a limit 96% of the time with GPS,
  against OSM's 70%. On roads under 60 km/h it is 93% against 46%. Where both
  have a limit they agree 95% of the time.
* **Tunnels:** the car follows the tunnel on wheel speed and heading when GPS
  drops out. City-bound through the M4 East it reads 90, then 80 on the Rozelle
  ramp. No street limit from above ever reaches the car underground.
* **Your rules:**
  * a Variable (peak) limit drawn over a fixed one gives the higher, normal speed;
  * on-ramps keep the road's own zone until the merge.
* **Data:** a weekly GitHub Action in `SoRadGaming/openpilot` builds it from
  TfNSW's files and publishes it as the `nswzones-latest` release (about 22 MB).
  The comma downloads it by itself, parked on Wi-Fi. Settings → maps shows its
  date and has an update button.
* **Checks:** a weekly build that would RAISE limits stops for a human look.

Replayed over all 25 drives: 128 prompts. With OSM only it is 72; the car
recorded 141. Most of the new prompts are real limits on streets OSM had
nothing for. The list of things to check on the road is at the end of
`docs/fork/NSW-SPEED-ZONES.md`.

## 2026-09-29 — speed limits: no carried limit, no tunnel prompts, a maps page (`2e7866503`)

**The map speed limit was wrong off motorways.** Across 25 drives there were 141
prompts:

* 43 were the previous road's limit, carried onto a road with no limit in OSM
  for the rest of the drive.
* 26 came from the M4 East, M8 and Iron Cove tunnels. GPS drops out there, and
  mapd matched the streets overhead (40 or 50 while you were doing 80).
* Behind both, the comma 4's OSM tiles had never been refreshed. It has no OSM
  screen.

Now, with **Strict Map Speed Limits** (sunnylink, cruise → speed limit; on by
default):

* **Only a real new limit prompts.** A road with no limit, or the same limit
  coming back after a junction, is not one. A carried limit is dropped after
  10 s on a road with no limit and good GPS, and engaging on such a road does
  not prompt for it.
* **Tunnels hold the limit you entered with.** It holds until the map has found
  the road again: the road name changes, or 10 s pass after GPS returns.
* **Settings → maps** shows when the OSM maps were last updated, and has an
  update button (parked only; keep the device on until it finishes). With
  **update weekly** on, which is the default, it refreshes by itself when
  parked on wi-fi. A phone hotspot counts as wi-fi unless it is marked metered.

Replayed over all 25 drives: 72 prompts instead of 141. Every prompt from the
road you were actually on is kept, none are carried over and none come from the
tunnels. Every tunnel exit prompts the correct next limit. With the setting off
the car behaves exactly as before.

Still coming: NSW's own speed-zone data (every road including local streets,
plus school zones), replacing OSM in NSW. That is being built and tested
offline first.

## 2026-09-27 — upstream sync: sunnypilot `a5f44653d`, opendbc `f95f996f`

**The fork now runs on current upstream sunnypilot (openpilot 0.11.2, sunnypilot
2026.003.000).** Merged as sunnypilot `6b6b2b31e` / `d1a14edcb` and opendbc
`8bd6e314`, plus the fixes from the review after it. The rule was: take upstream,
but keep this car's behaviour wherever upstream changed it; every other car gets
upstream's. The full list, item by item, is `docs/fork/UPSTREAM-2026-09.md`.

**First boot: do it parked, with internet.**

* The OS updates from AGNOS 18.4 to 19.7 before openpilot starts. It downloads
  first and blocks startup until it is done.
* The internal panda is reflashed (new firmware). Honda safety is unchanged.
* The first start builds the new tree, which takes a while.
* Expect the Experimental-mode confirmation page once, if you turn it on.

**Kept as it was on this car:**

* **Stopping.** Upstream now starts a stop below 0.3 m/s and ramps at 1.0 m/s³
  for every car. This car keeps 0.8 m/s and 0.8 m/s³, the tune its red-light
  holds were proven on, plus the stopping-exit debounce. The one difference: the
  0.8 m/s is now checked against the measured speed rather than the plan's.
* **Engage speed.** Upstream lets gas-interceptor Hondas engage from standstill;
  this car keeps its 19 mph minimum.
* **Lane changes.** Upstream's new lane-change logic, with the firm-or-held nudge
  on top. A brush still does not start one.
* **The gateway.** Steering commands, `0x500`, the board telemetry and the
  in-app board update are unchanged. A replay of route `00000103` sent
  byte-identical `0x0E4` and `0x500`.

**What changes for you:**

* **MADS in Pause mode:** with the brake held, lateral stays paused until you
  release it, including at a standstill and at the end of a board override
  (upstream's brake guard). Route `00000103` was recorded with "remain active",
  where this changes nothing, so check which mode the device is set to.
* **Turning MADS on while you have the wheel** during a board override now
  starts it paused. Before, it was active for one frame first.
* **Curves with vision curve slowdown on** can brake harder: up to 1.2 m/s²
  where the old planner used about 0.35–0.6. The set speed is now handled outside
  the longitudinal MPC and jerk-limited. Road-test it.
* **Steering delay:** the delay learner drops what it had learned (0.38 s),
  uses 0.58 s until it has relearned, and now learns only above 80 km/h. Accepted
  as a one-time relearn: the first stretch of highway puts it back near 0.38 s.
* **Driver monitoring:** camera alerts at 5 / 8 / 13 s (were 3 / 5 / 11), wheel
  alerts at 5 / 15 / 25 s (were 15 / 24 / 30), a new sound on the first "Pay
  Attention", and timed lockouts (1, 5, 15, 30 min) after two red alerts or one
  ignored for 5 s. The DM slowdown starts 5 s later. The right-hand-drive face
  icon is no longer mirrored.
* **Sounds:** engage, disengage and refuse are re-recorded, and the warning
  alerts play new files.
* **Screen:** a screensaver shows for 5 minutes before the display turns off
  offroad. Settings are reordered: models, vehicle, gateway, then the rest, with
  a new software tile.
* **If the screen UI crashes it no longer restarts by itself;** that needs a
  reboot.
* **Log names:** `livePose` is `deviceMotion`, `liveDelay` is `lateralDelay`,
  and so on. Old logs still decode; the parquet scripts in `S:/OP` keep working
  while `S:/OP/cereal` keeps the old schema.
* Routes start about 0.5 s sooner, and remote "Take Snapshot" is gone.

**Check on the first drive:**

* No ACC/CMBS fault on the first ignition, and Settings > gateway shows the
  board's hash.
* Stops latch at about 0.8 m/s and hold without rolling; no engagement below
  19 mph.
* During a board override (`0x70B` reason 4) MADS stays paused and
  `latActive` stays 0 on every frame.
* In Pause mode, a held brake keeps lateral paused until released.
* A brush with the blinker on does not change lanes; a firm tug or a held push
  does.
* Curve entries with vision curve slowdown: how hard it brakes.
* Steering feel before and after the first stretch above 80 km/h (the delay
  learner).
* No `*Lagging` events in the first routes (a new CPU cap applies onroad).

The fork's own code moved with upstream's layout: every sunnypilot path is now
under `openpilot/`, and the board image is
`openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin` (the firmware
repo's `bundle_appslot.py` finds either layout since `862540c`). All fork tests
are `unittest` test cases now, run with `tools/test_runner.py`, and pass with
upstream's whole suite.

## 2026-09-27 — MADS stays paused through the whole board override (`2cfcd3c6a`)

**While you had the wheel, openpilot kept asking to steer on every other frame.**
When the board hands the wheel back (`0x70B` reason 4, driver override), MADS
pauses: lanes stay dashed, lateral goes inactive. On the next frame the generic
silent-resume saw nothing holding the pause (no brake, no gear event) and
re-enabled it, and the frame after that the gateway paused it again. Route
`00000103` from t=58.5: 31 s of override with `carControl.latActive` reading
`0101…`. The steering was not affected, because the board ignores `0x0E4` while it
has let go, but openpilot's request and the board's view disagreed the whole time.

* The pause now holds for as long as the board reports the override, the same
  way a held brake holds a brake pause.
* When the board lets go, MADS resumes through the normal path, so a brake still
  held in Pause mode keeps it paused until you release it. That is also what
  upstream's new brake guard expects.
* Releasing the brake in the middle of an override no longer resumes.
* A fast-wheel emergency takeover on the same frame as the override turns MADS
  **off**, as designed. Before, the pause beside it turned "off" into "paused".

This is safe with the Stage 10 board image: its override latch is compiled out
(`GW_DRIVER_LATCH 0`), so reason 4 ends on your hands and the release dwell alone.
The board does not wait for openpilot to ask again.

Eleven tests in `sunnypilot/mads/tests/test_mads_gateway_pause.py`. Five of them
fail on the old code.

## 2026-09-27 — a lane-change nudge must be firm, or held

**Just touching the wheel with the blinker on started a lane change.** The
nudge threshold is 600 counts (lowered from 1200 on 2026-09-16 so weak
deliberate nudges at cruise work), and the lane-change logic confirmed on a
single model frame: route fc t=768.8 went on one 10 ms reading of 645.

Across 115 blinker windows on routes d9..fd, the pushes that confirmed lane
changes split two ways — **firm tugs that are often brief** (3765–4773 counts for
only 50–100 ms) and **light pushes that are held**. The brushes are the ones
that are both light and brief. So now, for this car only:

* **firm** (1500 or more in the wanted direction) confirms at once, as before;
* **light** (over 600, under 1500) must be held for 4 model frames — 150 ms.

Run through the real `desire_helper` on 92 recorded windows: 37 confirm at the
same instant, 29 later (mostly 50–150 ms, three at 0.4–0.9 s — weak pushes
hovering at the threshold), and **3 no longer confirm: peaks of 693, 740 and
903 held for one or two frames**. None confirms earlier, none confirms where the
old rule did not, and every other car keeps upstream behaviour (the rule is
keyed by fingerprint in `NUDGE_FIRM`).

`steeringPressed` stays at 600: driver monitoring and the lateral controller
should still see a light hand. Only the lane-change confirmation changed.
Eight tests in `sunnypilot/selfdrive/controls/lib/tests/test_lane_change_nudge.py`.

## 2026-09-27 — board `d995bc95` · the pothole latch

**LKAS faulting until restart after a big pothole** is the EPS's state-8 latch,
and both reported cases were the **same stretch of motorway**, 127 m apart in
opposite directions — fixed potholes on the regular route. The EPS's motor
torque swings hard against the column, it goes silent for 60–70 ms, and it comes
back refusing LKAS for the key cycle. Nothing the board or openpilot did started
it, and nothing passive clears it.

Board firmware `d995bc95` fixes the one thing the board did wrong inside every
such silence — its request counter froze while the EPS kept counting, feeding it
2–5 wrong-parity requests — as an experiment; see the board's CHANGELOG and
`docs/EPS-FAULT-STATES.md`. Every answered frame is unchanged. The next pass over
those holes with LKAS engaged is the test. **Until then, take over or hold the
wheel through that stretch.**

It goes in with the next board update, which also runs the per-phase idle
vibration measurement.

## 2026-09-24 — the update "hum" is the engine, and the next update measures it

**The trace worked.** It came out in route f6 with no SSH and said the wheel did
not move during the flash (0.6–0.7°, one 0.1° step). Together with f5 (same key
cycle, before) and a phone video of the whole update, the hum turned out to be
**engine firing vibration**, not the EPS: the V6's 3rd order (~44 Hz) and its
harmonic (~88 Hz), pitch-locked to idle rpm, **2.5× stronger after the flash**
at matched rpm and load, ~24× louder in the cabin, held until the engine was
restarted. The EPS neither makes nor amplifies it (column-to-body ratio 16.7 vs
16.5) and reports nothing. Full write-up: `docs/CAN-UPDATE.md` in the board repo.

The video puts the step **1.7 s after the knock, as the data stream begins**,
with rpm unchanged. So the update is still the suspect — as something that
knocks an engine vibration or noise control into a fallback — but which step of
it could not be told apart. **Workaround: cycle the ignition after an update.**

The flasher now makes every update answer that itself:

* **5 s baseline** before the knock, and **6 s after** the post-reboot HELLO
  through the app start and K2 re-split, which nothing recorded before.
* **An 8 s hold** with the bootloader session open and no data, between INFO and
  BEGIN — so the knock and the data stream are 8 s apart, not 1.7 s. INFO every
  2 s keeps the session alive; the board is in bypass throughout.
* **Engine rpm from `0x17C`**, and the accessory-load bit (`0x1A6` byte 2
  bit 6). Not `0x158`'s ENGINE_RPM field, which reads ~8% low in Park — the
  torque converter — and once produced "it cannot be the engine".
* **The firing-order amplitude per phase**, fitted in the column torque with its
  phase tracked from rpm, on the EPS's own 10 ms grid rebuilt from the `0x18F`
  counter. USB-batch receive times jitter ~10 ms, which would make a 44 Hz fit
  meaningless. Replayed over real f5/f6 frames it gives 2.10 and 5.02 counts
  against the independent 2.02 and 5.04.
* **Panda CAN error counters** per bus at every step.

**Confirmed by the owner: the car has active noise cancellation and VCM active
control engine mounts** — and the update left both fingerprints: structure up
2.5× (the mounts), cabin sound at 88 Hz up ~24× (ANC). So each phase also carries
a census of every car-bus ID against the pre-knock baseline: `lost` names any ID
that slowed below 80% or vanished, `new` any that appeared (something answering
our frames). On real f5→f6 traffic, 72 IDs, it reports nothing.

It arrives in the next route's `eps-lkas flash trace` line as a `phases` table:
`pre reset enter hold erase data finish copy post`, each with `rpm`, `ld`,
`o3` (and an off-order `ref`), `sd`, `ang` and error deltas `e0`/`e2`. **The
phase whose `o3` steps up is the trigger.** The update sits at 0% for ~13 s
before moving; that is the baseline and the hold.

## 2026-09-23 — board `d43b12aa` · the flash now records what it does to the steering

**A driver reported the EPS humming and the wheel moving slightly, left and
right, while the board was being reflashed over CAN in park — hands off the
wheel.** Nothing can say what happened, because nothing was watching: the
flash runs offroad, loggerd is not running, and the routes either side of it
end and begin outside the window. It is a 47.25 s hole with no data in it.

Meanwhile the panda was receiving the entire car bus for all of it — about
100,000 frames on bus 0 — and `_pump` discarded every one. The flasher now
keeps two of them:

```
0x156 STEERING_SENSORS   STEER_ANGLE (0.1 deg) + STEER_ANGLE_RATE, 100 Hz
0x18F STEER_STATUS       STEER_TORQUE_SENSOR (column torque), 100 Hz
```

kept as raw timestamped bytes — no decoding in the receive path, no opendbc
import in a module that has to load on a bench. Bounded at 24,000 frames
(~2 min of both), captured in a `finally` so a failed flash keeps its trace
too.

**And it comes out without SSH, because not everyone has it.** The first cut
wrote a CSV to `/data/eps-lkas-trace/` and stopped there, which is useless to
anyone who cannot fetch a file off the device. The trace now travels the way
everything else does:

```
flasher  decodes it into a summary + a 10 Hz series   (summarise_trace)
hook     puts that in EpsLkasFlashTrace, PERSISTENT   (survives the restart)
card     emits it to cloudlog on the next drive, once, then clears the param
```

So it lands in an ordinary route. Flash, drive, send the route — nothing else.
The CSV is still written for anyone who does have SSH.

The summary states the discriminator outright rather than leaving it to be
re-derived: `wheel did not move`, `moved, with column torque - looks like a
hand on the wheel`, or `MOVED WITH LOW COLUMN TORQUE - something drove the
column`. The decoders are cross-checked against `carState` on route f1 — 2.5°
against `steeringAngleDeg`, −74..0 against `steeringTorque` — and a test pins
both.

`EpsLkasFlashTrace` is PERSISTENT on purpose: `CLEAR_ON_MANAGER_START`, which
the other three flash params use, would throw the trace away at exactly the
wrong moment. It is written as a **dict**, not `json.dumps`, for the reason
that killed `card` once before.

Those two also separate the two candidate causes without a scope: **angle
moving while column torque stays in the low hundreds means something drove the
column; torque in the thousands leading the angle means a hand on the wheel.**
There is no third instrument available — `0x1AB STEER_MOTOR_TORQUE` does not
exist on this car, and the EPS's own motor torque is reported only over the
LKAS serial link, which the board stops mirroring the instant it enters its
bootloader.

**What is already ruled out.** The comma cannot reach the EPS: torque reaches
it only over the 12 V LKAS serial line, and the OBD-C connector carries CAN,
SBU and VBUS only. `SAFETY_ELM327` *closes* the harness relay rather than
opening it, so the stock camera stayed connected to the car bus throughout,
and the panda's relay does not click during the flash at all. And the key
cycle spans the whole window with neither EPS key-cycle latch set, so whatever
the noise was, it did not fault the EPS.

The board half of this is firmware `d43b12aa`, which also fixes a real RULE-2
exposure in the knock handler — it used to drop relay K2 while both LKAS
transmitters were still enabled. See the board's own CHANGELOG, and
`docs/EPS-FAULT-STATES.md` for why "the random LKAS error" is a different
fault from the two the code was hardened against.

## 2026-09-22 — `6a4f1f5`, board `577a723e` · the lane graphic, and how to tell whether the fix landed

**The dash lane graphic was not tracking sunnypilot.** Two rules were wanted:
the graphic follows lateral being *enabled*, and it goes solid only while the
car is actually steering. The board already implemented exactly that. The
signal it was given did not mean what the name said.

`carcontroller.py` was packing

```python
lat_ready = steering_available or CC.latActive
```

and `steering_available` is `cruiseState.available and vEgo > minSteerSpeed` —
cruise main on and moving faster than the minimum. That is true with **MADS
off**, so the graphic came up whenever the car was rolling with cruise main
lit, which is most of a drive. Now:

```python
lat_ready = CC_SP.mads.enabled or CC.latActive
```

`mads.enabled` is the MADS state machine being in one of its enabled states,
which is what "lateral is enabled" actually means. No firmware change was
needed for this — the board reads the bit in one place and the rule there was
already right.

What to expect:

| | graphic |
|---|---|
| MADS off, cruise main on, moving | blank |
| MADS on, not steering | dashed |
| board steering | solid |
| driver overrides, assist hands back | dashed, not blank |
| assist without override | stays solid |

**And a bit to prove it, because two drives could not.** The board's decision is
`op_lat = wants || (sp.has_req && fresh && sp.lat_ready)`, so everything past
the first term depends on `0x500 SP_HUD_STATUS` arriving at all. If it never
arrives, `op_lat` collapses to `latActive` and fixing `lat_ready` changes
nothing — and the two cases produce **identical logs**. Both attempts to tell
them apart from routes `ed`/`ee` came back inconclusive:

* `0x70B REASON` — the only 0x500-derived codes are `integrator_too_large` and
  `brake`, and neither occurred. Absence of a reason is not absence of a frame.
* `0x70B AUTHORITY` vs `0x707 AUTHORITY` — both 160, and they always will be:
  `hondacan.py` sends `SP_HUD_MAX_TORQUE = 0` and the board only folds
  `max_torque` when it is non-zero, so the two can never differ.

Board firmware `577a723e` adds **`GW_BUILD.BUILD_SP_FRESH`** (`0x70F` byte 0
bit 5): 0x500 is arriving and fresh, sampled when the beat is sent. Over a
drive, never-set means never arrived. `route_flatten.py` says so outright
rather than leaving it to be re-derived.

**This needs the board reflashed** — Settings → gateway → update firmware. The
card will read `to 577a723e`.

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
