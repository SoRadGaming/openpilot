"""
FORK(HONDA_ACCORD_9G_AU): a higher floor for torqued's lateral-accel factor on the Elesys Accord.

torqued lets its factor sit anywhere in (1 -/+ FACTOR_SANITY) x the prior: 0.875-1.625 around this car's 1.25. On
the drives of 17-19 Oct (13d-143) it was pinned at 0.875 by points taken with a hand resting on the wheel (300-1199
counts of driver torque, never "pressed": 92-97% of what it learned from), while the car needs ~1.4-1.6 above
90 km/h - so the feed-forward there was ~1.7x too strong and the wheel busier on straights. Town tracking at 54-70
km/h was the same at factors 1.05-1.43 as at 0.875 (error RMS 0.064-0.065 against 0.061), so the floor goes to
ELESYS_LATACCEL_FACTOR_FLOOR (1.10) and the window becomes 1.10-1.625. The prior, the ceiling, the friction window
and everything torqued learns are unchanged.

Why nothing resets. torqued's restore key is (fingerprint, tuning, friction prior, factor prior, VERSION): the
bounds are not in it, so the cache written by the previous build - points, filtered values and decay - is
restored as it is. The bounds only clip each RAW estimate before torqued's filter (np.clip in get_msg()); an
estimate below the floor is clipped to it, never a reset. The restored filtered value is not clipped either: it
slides from the cached ~0.9 to 1.10 with the filter's time constant (decay 250 at DT_MDL, ~21 min of onroad time).

Only on HONDA_ELESYS with torque tuning, only ever RAISING the floor, never above the prior, and not when
sunnypilot's relaxed window (LiveTorqueParamsRelaxedToggle) is in force: that window is the driver's own choice.
Any failure returns torqued's own floor.
"""

ELESYS_LATACCEL_FACTOR_FLOOR = 1.10


def lataccel_factor_floor(CP, prior: float, upstream_floor: float, upstream_window: bool) -> float:
  """torqued's min_lataccel_factor for this car: max(upstream's, 1.10), capped at the prior; upstream's otherwise.

  upstream_window: torqued's factor_sanity is its own FACTOR_SANITY / FACTOR_SANITY_QLOG (False under the relaxed
  toggle)."""
  try:
    from opendbc.car.honda.values import HONDA_ELESYS
    if not upstream_window or CP.carFingerprint not in HONDA_ELESYS or CP.lateralTuning.which() != 'torque':
      return upstream_floor
    floor = min(ELESYS_LATACCEL_FACTOR_FLOOR, float(prior))
    return max(float(upstream_floor), floor)
  except Exception:
    return upstream_floor
