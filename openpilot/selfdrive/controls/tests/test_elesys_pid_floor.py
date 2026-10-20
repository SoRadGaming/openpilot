"""FORK(HONDA_ACCORD_9G_AU): the longitudinal PID's floor on HONDA_ELESYS, -3.2 instead of -4.0 (round 8, proposal 9).

opendbc's test_elesys.py pins get_pid_accel_limits() itself. This drives the real LongControl with those limits:
the integrator stops winding at -3.2 minus the plan while the brake is railed (route 143 t172.9-174.6 wound it to
-1.33 at -4.0), the brake command is the same full brake either way, the release after it is quicker, and every
frame whose output stays above -3.2 is bit-identical to the -4.0 floor.
"""
import random
from dataclasses import dataclass, field

from opendbc.car.structs import car
from opendbc.car.honda.carcontroller import compute_gb_honda_elesys
from opendbc.car.honda.interface import CarInterface, ELESYS_PID_ACCEL_MIN
from opendbc.car.honda.values import CAR, CarControllerParams
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.controls.lib import longcontrol as lc

UPSTREAM_FLOOR = CarControllerParams.NIDEC_ACCEL_MIN


@dataclass
class CruiseState:
  standstill: bool = False


@dataclass
class CS:
  vEgo: float = 15.0
  aEgo: float = 0.0
  brakePressed: bool = False
  gasPressed: bool = False
  cruiseState: CruiseState = field(default_factory=CruiseState)


def _cp():
  CP = car.CarParams.new_message()
  CP.carFingerprint = CAR.HONDA_ACCORD_9G_AU
  CP.stopAccel = -0.8
  CP.longitudinalTuning.kiBP = [0., 5., 35.]       # this car's (opendbc honda/interface.py)
  CP.longitudinalTuning.kiV = [1.2, 0.8, 0.5]
  return CP


class _CP_SP:
  enableGasInterceptor = True
  flags = 0


def _limits(v):
  return CarInterface.get_pid_accel_limits(_cp(), _CP_SP(), v, 30.0)


def _run(floor, frames):
  """frames: [(a_target, a_ego, v)] at 100 Hz, PID state throughout. Returns [(output, integrator)]."""
  loc = lc.LongControl(_cp(), _CP_SP())
  out = []
  for a_target, a_ego, v in frames:
    hi = _limits(v)[1]
    o = loc.update(True, CS(vEgo=v, aEgo=a_ego), a_target, False, (floor, hi))
    out.append((float(o), float(loc.pid.i)))
  return out


class TestElesysPidFloor(OpenpilotTestCase):
  def test_the_limits_controlsd_passes(self):
    self.assertEqual(_limits(15.0)[0], ELESYS_PID_ACCEL_MIN)
    self.assertEqual(ELESYS_PID_ACCEL_MIN, -3.2)

  def test_full_brake_windup_stops_at_the_floor_minus_the_plan(self):
    # 143 t172.9-174.6: plan -2.1 to -2.9, the car at -1.65 to -1.92 on full brake; here -2.7 against -1.85 at 16 m/s,
    # long enough for both integrators to reach their bounds
    sat = [(-2.7, -1.85, 16.0)] * 300
    old, new = _run(UPSTREAM_FLOOR, sat), _run(ELESYS_PID_ACCEL_MIN, sat)
    # the anti-windup stops the integrator on the last step that keeps it inside the limit (within one 0.006 step)
    self.assertAlmostEqual(old[-1][0], -4.0, delta=0.01)
    self.assertAlmostEqual(old[-1][1], -1.3, delta=0.01)    # wound to -4.0 - (-2.7)
    self.assertAlmostEqual(new[-1][0], -3.2, delta=0.01)
    self.assertAlmostEqual(new[-1][1], -0.5, delta=0.01)    # stops at -3.2 - (-2.7)
    # the brake is railed the whole time either way: no braking is lost
    for (o_old, _), (o_new, _) in zip(old, new, strict=True):
      self.assertEqual(compute_gb_honda_elesys(o_old, 16.0), compute_gb_honda_elesys(o_new, 16.0))

  def test_the_release_after_it_is_quicker(self):
    # the plan lets go to -1.0 after the saturated stretch while the car is still at -1.85: the old integrator holds
    # the command near full brake much longer
    n = 300
    frames = [(-2.7, -1.85, 16.0)] * n + [(-1.0, -1.85, 14.0)] * 300
    old, new = _run(UPSTREAM_FLOOR, frames), _run(ELESYS_PID_ACCEL_MIN, frames)
    self.assertAlmostEqual(new[n][0] - old[n][0], 0.8, delta=0.02)   # on the release frame: 0.8 m/s^2 less windup

    def settle(seq):  # frames after the release until the command is within 0.1 of the plan
      return next(k for k in range(n, len(seq)) if seq[k][0] >= -1.0 - 0.1) - n
    self.assertLess(settle(new), settle(old) - 100)          # more than a second sooner

  def test_above_the_floor_it_is_bit_identical(self):
    # random PID traffic that never reaches -3.2: every output and integrator value is the same
    rng = random.Random(20261020)
    frames = []
    for _ in range(40):
      a_t = rng.uniform(-2.4, 1.2)
      frames += [(a_t + rng.uniform(-0.1, 0.1), a_t + rng.uniform(-0.6, 0.6), rng.uniform(1.0, 30.0)) for _ in range(50)]
    old, new = _run(UPSTREAM_FLOOR, frames), _run(ELESYS_PID_ACCEL_MIN, frames)
    self.assertGreater(min(o for o, _ in old), ELESYS_PID_ACCEL_MIN)
    self.assertEqual(old, new)
