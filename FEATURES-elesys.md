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

## The two toggles

Both are in **Settings → Cruise**. Both are **off** by default.

### 1. "Honda Nidec Dynamic Longitudinal Tuning (Alpha)"

The main one. Turns on the self-learning.

### 2. "...also blend the PCM gas above 30 km/h (Experimental)"

**Leave this off.** It hands part of the throttle over to the car's own cruise computer above
30 km/h. We have never confirmed that computer even responds — openpilot has sent it 250,334
"give me throttle" messages with the value set to zero, so it has literally never been asked.
Until that's tested standing still, this stays off.

It can't be switched on unless the first toggle is on, and turning the first one off clears
it automatically.

---

## The big one: it learns your car's throttle

**The problem.** Your car doesn't accelerate as hard as openpilot asks it to. We measured this
properly across your logs: to get the acceleration the planner wanted, the pedal needed to be
pushed **1.3 to 1.75 times harder** than it actually was. That gap is why the car felt slow
and lagged the speed it was aiming for.

**Two fixes, both in.**

*First*, the baseline pedal curve was raised by about 25% at 6 m/s and above (roughly 20 km/h
and up). This is a fixed change — it happens whether or not you turn any toggle on. Below
about 10 km/h nothing changed, so pulling away from a stop feels the same.

*Second*, with the toggle on, the car now **learns the remaining gap itself and remembers it**.
It keeps six separate numbers, one for each speed band — 0, 3, 6, 10, 15 and 20 m/s — because
the shortfall isn't the same at every speed. It nudges each one based on how much
acceleration it actually got versus what it asked for.

**What you'd notice:** more responsive pull-away and merging, and over a few drives it should
keep getting closer to what the planner wants rather than always running slightly behind.

**One thing to watch.** There's a safety cap — it will never push the pedal more than 1.8×
harder than the baseline. In testing against your old logs, it hit that cap and stayed there.
That's exactly why the baseline was raised: with a better starting point, it shouldn't need
to go near the cap. **If your next log shows it sitting at 1.800, tell me — the baseline needs
another step up.**

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
the lever is between positions. There's no way to tell them apart instantly. So it waits **1
second** — a real Sport selection lasts, a lever passing through doesn't. The longest
in-between moment ever seen in your logs was half a second, so a full second is comfortable.

---

## It knows about ECON now

You found the bit, and it checks out against your logs perfectly.

**Why this matters:** ECON changes how the throttle responds. If the car learned in ECON and
in normal mode and mixed the two together, it would end up with an average that's wrong in
both. So now the learning **pauses whenever ECON is on**, and pauses again for a moment
whenever you switch it.

Same thing for gear — it only learns in **Drive**. In Sport it pauses.

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

**Leave the PCM blend off** until the standing-still test.
