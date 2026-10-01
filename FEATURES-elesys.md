# What's new on the 2015 Accord — plain English

A guide to what changed and what you'd actually notice from the driver's seat.
No code in this one.

---

## When settings are read and saved

Short answer: **the car reads its settings once at ignition, and saves what it learns every
60 seconds while you drive.**

| | when |
|---|---|
| Reads the toggles | **once**, when the car goes onroad |
| Reads the learned values | **once**, same moment |
| Saves the learned values | **every 60 seconds** while driving |
| Re-reads anything mid-drive | **never** |

What this means in practice:

- **Flipping a toggle does nothing until you restart the car.** Turn it on, then key off and
  on again. There is no live switch.
- **What it learns IS saved during the drive**, every minute. If you key off after a 20 minute
  drive you keep what it learned. You don't lose it.
- **It never reloads mid-drive.** So if you edited a value by hand while driving, nothing
  would happen until next ignition.

Think of it as: it loads its notebook when you start the car, writes to the notebook all
drive, and only ever re-reads the notebook next time you start.

---

## The two settings

**On a comma 4** (the small screen — this is the one you have): **Settings → vehicle**. That
page is new. The comma 4 runs a different UI to the comma 3/3X, and that UI has no Cruise page
and no Vehicle page at all — which is exactly why the toggle never showed up for you no matter
what was in the Cruise panel. The page has the learning toggle, what the car has learned, and
the reset.

**On a comma 3 / 3X** (the big screen): **Settings → Vehicle**, on the Honda page, or
**Settings → Cruise** near the bottom of the list.

**In the sunnylink app**, on any device: under Vehicle → Honda Settings — both settings are
there. The app renders whatever settings list the *device* publishes, so if the Honda section
shows up in the app, that is proof the device is running this code.

### 1. "Dynamic Longitudinal Learning (Alpha)" — off by default

Turns on the self-learning: the brake correction and the hill term (it no longer learns the
throttle — see "The gas pedal" below). It also switches on the stop smoothing: the gentle brake
release, the release debounce and, since October 2026, the softer last moment of a stop. It reads as "Honda Nidec Dynamic Longitudinal Learning
(Alpha)" on the Cruise page, since that page is shared with every other car.

Remember: **flipping it does nothing until the next ignition.** Turn it on while parked, key
off, key on.

### 2. "Measured Gas Pedal Law (2013-15 Accord)" — ON by default

In sunnylink only. On: the pedal uses the throttle response measured on your car. Off: the
previous pedal law, exactly. Also read once at ignition. The comma 4 card shows which one the
car will use from the next drive (`v2 next drive` = measured, `v1 next drive` = previous).
It runs whether or not the learning toggle is on.

(The old "blend the PCM gas above 30 km/h" toggle is gone — the car's cruise computer was never
shown to respond to it.)

---

## Seeing what it has learned

On the comma 4 it is the card at the front of **Settings → vehicle**:

- **gas law** — `v2` (measured) or `v1` (previous), the law the car will use from the next
  drive, then the learned brake correction as `brake x1.00` (`x1.00` until it has learned
  anything). On another Honda the gas law does nothing, so the card shows only **brake**.
- **D / ECON / S** — minutes driven with sunnypilot's longitudinal engaged in each drive mode,
  e.g. `412 / 1.4 / 1.3 min`. This is the data a future per-mode pedal table needs. It is
  counted only while the learning toggle is on.

On the big screen it is **Settings → Vehicle → Learned Values**, and in the app the read-only
rows on the Cruise page (the mode times there are in seconds).

The six "pedal gain by speed" numbers and the "aero" number that used to be here are gone:
they could not move (see below), so they were showing nothing.

The numbers refresh about once a second on screen, and the car itself saves them roughly once
a minute while you drive, so open the page after a drive to see the day's numbers.

**RESET** (slide to confirm on the comma 4) puts the brake correction back to zero. The mode
times are kept: they are a tally of the data collected, not something learned. It asks for
confirmation first, and it is only available with the car off — the
tuner keeps it in memory while driving and would just write it back over the top a minute
later.

---

## The gas pedal: now the measured law (October 2026)

**The old problem was the opposite of what it looked like.** An earlier fit said the car needed
1.3-1.75x *more* pedal, so the pedal curve was raised and a learner was added on top. Fitting
the pedal's response properly — slope and cruise offset separately, over 51 of your drives —
says that at 20-70 km/h the raised curve gave **1.4-1.7x too much** pedal per m/s² of request.
That is the "lunge, then sag" pulling away from a roll: the car shot past what the planner
asked for in the first two seconds, then openpilot's own controller backed off.

**The new law uses the measured response.** What you'd notice: a softer, steadier pull from a
roll at 20-70 km/h — for the same 1 m/s² request at 72 km/h the pedal is about 0.47 instead of
0.74. Pulling away from a stop (below 11 km/h) is **exactly** the old law, and so is the
hand-over between gas and brake below about 60 km/h. If you don't like it, switch "Measured
Gas Pedal Law" off in sunnylink and the next drive is the old law exactly.

**The throttle learner is gone.** It could never keep what it learned (every ignition threw
the drive's progress away; in six weeks it moved by less than 1%), and it was reading
openpilot's own correction as a car problem, so it would have pushed the wrong way. The "aero"
learner went with it — it wandered all over its range from drive to drive. With the learning
toggle on, that makes light braking at highway speed very slightly gentler (about 5 counts
less brake at 90 km/h).

**Max acceleration was not raised.** The 1.6 m/s² limit only ever bit during launches that
were already overshooting, so raising it would only add lunge.

---

## It learns braking too, and can now go both ways

You'd hand-set the brake strength (the "2.6 divisor") to fix severe under-braking. That was
the right call — we checked, and your value is essentially spot on. The car delivers 2.30 to
2.64 m/s² per unit of brake command against the 2.6 you assumed.

What's new is that it can now **fine-tune that live instead of you editing it**. Importantly,
it can now go **both directions** — before, it could only ever add brake, so if your value
ended up slightly too strong there was no way back except another edit.

The two directions are deliberately not equal:

- it can add up to **60% more** brake
- it can take away at most **15%**

Because over-braking is something you feel immediately and can back out of, while
under-braking is the failure that started all this. A wrong learn should be a slightly soft
stop, never a missed one.

**It never touches the brake that holds you at a stop.** That one has no feedback to learn
from, and it's the one you tuned by hand. It's now locked to exactly what you set.

---

## Stops hold with less pressure

Your car was clamping the brakes at **253 out of 255** to hold at a red light — near maximum
pressure, for minutes at a time. It doesn't need that.

That's now set to hold at about **189** instead.

**Watch for this on the first drive.** This has never run on the road — every one of your 14
logged drives used the old value. If the car ever creeps forward at a light, especially on a
hill, the fix is to raise this back toward the middle. **Don't touch the creep table.**

---

## A softer last moment of the stop (October 2026)

At the end of an openpilot stop the brake used to reach the full standing hold (189) **before** the car had actually
stopped - on your logged stops it was already at 185 when the wheels stopped, while openpilot was asking for almost
nothing. That is the little jolt at the very end.

Now, with the learning toggle on, the brake is held lower (125, a little more on a downhill) for as long as the car is
still rolling in the stopping phase. About half a second after the wheels stop it rises to the usual hold in a quarter of
a second, so **how the car holds at a light does not change**.

It gives way early - back to the old behavior - if the car is not slowing, if the wheels start turning again, or
1.9 s after the stopping phase began. A stop that starts faster than about 4 km/h (1.2 m/s) does not get it at all:
none of your logged stops did. Your brake or gas pedal removes it at once.

Each stop writes one `hondastop` line to the log, saying what happened.

**This has not been on the road yet.** Watch the last half-second of each stop, and how close you stop to the car ahead.

---

## The brake pump is quieter

You said the stock behaviour sounds like a machine gun, your fix cured that but replaced it
with a constant whine.

I found the cause, and it wasn't what I first guessed. During gentle braking the brake command
drifts upward slowly, and the old logic treated every tiny rise as "keep the pump running" —
so it stayed on more or less continuously. **Nearly half of all pump running was happening
during light braking.**

Now it needs a *bigger* rise to keep running when you're braking gently, and the same small
rise as before when you're braking hard:

- gentle braking: **23% less pump running**
- firm braking: **completely unchanged**

**One thing here was a genuine mistake, now fixed.** The version you'd been running also
deleted the rule that keeps the pump on continuously during hard braking and during a firm
stop. That quietly undid your own earlier fix for exactly that problem. Measured on your real
drives, at the hardest braking the pump went from running **100% of the time to 32%**, and the
longest it ever sat off during hard braking went from **0.16 seconds to 5.5 seconds**. That's
the same signature you'd already chased once: the brakes fading at the end of a hard stop.

That rule is back. The two things turn out to be independent — one is about hard braking, the
other about gentle braking — so you get both: full pressure when you need it, and still about
a third less pump running overall than before.

I also checked and rejected the obvious alternative of just running the pump less often on a
timer. It saves almost nothing and nearly doubles the longest gap with no pump at road speed.
That's now written into the code as a "don't do this".

**Is it overused now? No** (measured October 2026 on 57 drives): the pump runs 28% of the time
the brake is being asked for. The car's own cruise control ran it 53%.

---

## It knows what gear you're in now (it didn't before)

**Your car has been telling openpilot it was in Drive while parked.** The gear signal was on
the wire the whole time, just never read — openpilot was guessing "Drive unless the reverse
lights are on".

Now Park, Reverse, Neutral, Drive and Sport all read correctly.

**Two things change because of this:**

1. openpilot will now properly refuse to engage in Park or Neutral. It should have been doing
   this all along.
2. In Sport, the always-on driver monitoring switches off. That's normal Honda behaviour and
   the same on every other Honda that reports Sport.

**Why Sport was tricky.** The gear signal uses one value, 0, for *both* Sport *and* the moment
the lever is between positions. So it waits **1 second** — a real Sport selection lasts, a
lever passing through doesn't. The longest in-between moment ever seen in your logs was half a
second, so a full second is comfortable. Since then you've driven in S, and a second signal
turns out to say "Sport" outright (`GEAR = 26`, every time), so in practice it switches
instantly; the 1 second wait is the backup.

---

## It knows about ECON now

You found the bit, and it checks out against your logs perfectly.

**Why this matters:** ECON changes how the throttle responds — your logs show it delivering
about 0.4 m/s² less for the same pedal — and Sport holds lower gears. If the car learned in
ECON or Sport and in normal mode and mixed them together, it would end up with an average
that's wrong in all of them. So the brake learning **pauses whenever ECON is on or the lever
is in S**, and pauses again for a moment whenever you switch.

The pedal now knows which mode it is in (S, else ECON, else D) and has a separate multiplier
for each — all set to "no change" for now, because there are only about 1.5 minutes of engaged
driving in ECON and in S in a month of logs, far too little to set them from. The car now
counts that time for you (the `D / ECON / S` minutes on the comma 4 card). **To make per-mode
tuning possible, drive at least 15 minutes engaged in ECON and 15 in S**, with some gentle
accelerations at 40-80 km/h, and with the learning toggle on (the minutes are only counted
then). A change of mode fades the pedal over 2 seconds rather than
stepping it.

The reason ECON looked "dead" in the old logs is simply that you never turned it on during
those 7 hours. The bit was there, just always zero.

---

## Better detection of the car's own emergency braking

Three new signals from your bit-level work: the bit that actually fires when your car's
collision system requests braking, one for when it's actively braking, and one for when it's
switched off.

openpilot now watches the "actively braking" one as well as what it already watched. This is
so it knows to get out of the way when the factory system takes over.

**I made this deliberately over-sensitive rather than under.** A false alarm just means
openpilot backs off for a moment. A miss means openpilot fights the car's emergency braking.
Easy choice.

**None of this changes anything openpilot sends** — it's all listening, not talking.

---

## Less rolling at red lights

If the car was stopped and something briefly made openpilot think it was time to go — a car
ahead creeping, a momentary glitch — it would let go of the brakes and the car would roll
forward before catching itself.

Now it waits **0.4 seconds** before releasing, so a brief glitch gets ignored but a real
launch isn't delayed.

**Pressing the accelerator always overrides this instantly.** And it only applies with the
main toggle on.

---

## Metric/imperial on the brake message

Small one. The brake message carries a flag saying whether your dash is in km/h or mph. It's
now taken straight from the cluster rather than being worked out awkwardly. Your own DBC named
this bit the same thing, which confirms it.

---

## The honest summary

**Nothing here has been tested on a car.** Every number in this document comes from one of:
computer tests, replays of your recorded drives, or driving a simulated car offline. Not one
of these changes has moved a real vehicle.

The self-learning in particular has never run on the road in any form.

**Suggested first drive: leave both toggles off.** You still get the stronger throttle, the
lighter stop-hold, the quieter pump and the correct gear reading. Those four are enough to
judge on their own, and each shows up differently so you can tell them apart:

- pulls harder from about 20 km/h up → the throttle curve
- stops hold with less pressure → watch for any creep, especially on a hill
- less pump whine when braking gently → unchanged when braking hard
- shows Park when parked → it used to say Drive

**Then turn on the learning as a separate drive**, so if something feels off you know which
change caused it.

(Since then: the PCM blend toggle was removed, and the raised throttle curve was replaced by
the measured gas law — see "The gas pedal" above.)
