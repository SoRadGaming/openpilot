"""
Per-car longitudinal stopping tune, for HONDA_ACCORD_9G_AU (this fork's car).

Upstream removed the per-car stopping tunes (fdd1df79f, 031b1ad0a, d1e143ac9): the planner's
should_stop is v_ego < 0.3 and a_target < 0.1 for every car (drive_helpers.should_stop), and
LongControl ramps toward stopAccel at a fixed 1.0 m/s^3. CarParams.vEgoStopping and
stoppingDecelRate are no longer read.

This car was tuned and proven on vEgoStopping 0.8 (opendbc honda/interface.py) and the default
stoppingDecelRate 0.8, and the stopping-exit debounce in longcontrol.py reasons from that ramp.
These tables keep both for the fingerprints listed. Every car not listed gets None from .get()
and keeps upstream's values.
"""

# fingerprint -> m/s: below this speed the planner may ask to stop (upstream: 0.3 for every car)
STOPPING_SPEED = {
  "HONDA_ACCORD_9G_AU": 0.8,
}

# fingerprint -> m/s^3: how fast LongControl ramps the output toward stopAccel while stopping
# (upstream: 1.0 for every car)
STOPPING_DECEL_RATE = {
  # float32(0.8), exactly what CP.stoppingDecelRate (a capnp Float32) delivered before the merge.
  # The Python float 0.8 is 1.2e-8 short of it, which from a non-negative start takes the ramp one
  # 0.008 m/s^2 step past stopAccel (-0.808 instead of -0.800, one COMPUTER_BRAKE count).
  "HONDA_ACCORD_9G_AU": 0.800000011920929,
}
