"""FORK(HONDA_ACCORD_9G_AU): the longitudinal PID's floor on HONDA_ELESYS, -3.2 instead of -4.0 (round 8, proposal 9);
-3.25 since brake320 (2026-10-24), exactly the new full brake (a brake fraction of 1.25, 320 counts).

opendbc's test_elesys.py pins get_pid_accel_limits() and elesys_pid_accel_min() itself. This drives the real
LongControl with those limits: the integrator stops winding at -3.2 minus the plan while the brake is railed (route 143
t172.9-174.6 wound it to -1.33 at -4.0), the brake command is the same full brake either way, the release after it is
quicker, and every frame whose output stays above -3.2 is bit-identical to the -4.0 floor. On a climb the floor
follows the pitch controlsd passes (round 8 review): the car controller adds the climb's term back after the PID, and
the brake stays as full as it was with -4.0.
"""
import random
from dataclasses import dataclass, field

from opendbc.car.structs import car
from opendbc.car.honda.carcontroller import compute_gb_honda_elesys
import inspect
import math

from opendbc.car import ACCELERATION_DUE_TO_GRAVITY
from opendbc.car.honda.interface import CarInterface, ELESYS_PID_ACCEL_MIN, elesys_pid_accel_min, elesys_pid_floor_applies
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
    self.assertEqual(ELESYS_PID_ACCEL_MIN, -3.25)

  def test_full_brake_windup_stops_at_the_floor_minus_the_plan(self):
    # 143 t172.9-174.6: plan -2.1 to -2.9, the car at -1.65 to -1.92 on full brake; here -2.7 against -1.85 at 16 m/s,
    # long enough for both integrators to reach their bounds
    sat = [(-2.7, -1.85, 16.0)] * 300
    old, new = _run(UPSTREAM_FLOOR, sat), _run(ELESYS_PID_ACCEL_MIN, sat)
    # the anti-windup stops the integrator on the last step that keeps it inside the limit (within one 0.006 step)
    self.assertAlmostEqual(old[-1][0], -4.0, delta=0.01)
    self.assertAlmostEqual(old[-1][1], -1.3, delta=0.01)    # wound to -4.0 - (-2.7)
    self.assertAlmostEqual(new[-1][0], -3.25, delta=0.01)
    self.assertAlmostEqual(new[-1][1], -0.55, delta=0.01)   # stops at -3.25 - (-2.7)
    # the brake is railed the whole time either way, at brake320's ceiling (1.25) and at the old one (1.0, the VSA
    # fallback): no braking is lost
    for (o_old, _), (o_new, _) in zip(old, new, strict=True):
      self.assertEqual(compute_gb_honda_elesys(o_old, 16.0), compute_gb_honda_elesys(o_new, 16.0))
      # at 1.25 the anti-windup leaves the output within one integrator step of the floor: under a count apart
      self.assertAlmostEqual(compute_gb_honda_elesys(o_old, 16.0, 1.25)[1], compute_gb_honda_elesys(o_new, 16.0, 1.25)[1],
                             delta=0.003)

  def test_the_release_after_it_is_quicker(self):
    # the plan lets go to -1.0 after the saturated stretch while the car is still at -1.85: the old integrator holds
    # the command near full brake much longer
    n = 300
    frames = [(-2.7, -1.85, 16.0)] * n + [(-1.0, -1.85, 14.0)] * 300
    old, new = _run(UPSTREAM_FLOOR, frames), _run(ELESYS_PID_ACCEL_MIN, frames)
    self.assertAlmostEqual(new[n][0] - old[n][0], 0.75, delta=0.02)   # on the release frame: 0.75 m/s^2 less windup

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

  def test_on_a_climb_the_floor_follows_the_pitch_and_the_brake_stays_full(self):
    # an emergency the car cannot meet (plan -3.5, the car at -1.9) on a climb: the PID saturates at the floor, the car
    # controller adds the climb's g*sin(pitch) back, and compute_gb_honda_elesys() must still see full brake wherever
    # the old -4.0 floor gave it. A flat -3.2 did not from about 6% (the round 8 review's finding)
    for grade in (0.0, 3.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0):
      pitch = math.atan(grade / 100.0)
      hill = ACCELERATION_DUE_TO_GRAVITY * math.sin(pitch)
      floor = elesys_pid_accel_min(_cp(), _CP_SP(), pitch)
      sat = [(-3.5, -1.9, 17.0)] * 300
      new, old = _run(floor, sat), _run(UPSTREAM_FLOOR, sat)
      for (o_new, _), (o_old, _) in zip(new, old, strict=True):
        b_new, b_old = compute_gb_honda_elesys(o_new + hill, 17.0)[1], compute_gb_honda_elesys(o_old + hill, 17.0)[1]
        self.assertGreaterEqual(b_new, b_old, grade)
      self.assertEqual(compute_gb_honda_elesys(new[-1][0] + hill, 17.0)[1], compute_gb_honda_elesys(old[-1][0] + hill, 17.0)[1])
      if grade <= 8.0:
        self.assertEqual(compute_gb_honda_elesys(new[-1][0] + hill, 17.0)[1], 1.0, grade)
      if grade <= 7.0:   # brake320's full brake (1.25) up to where -4.0 binds (about 7.6%)
        self.assertAlmostEqual(compute_gb_honda_elesys(new[-1][0] + hill, 17.0, 1.25)[1], 1.25, delta=0.005, msg=grade)
    flat = _run(ELESYS_PID_ACCEL_MIN, [(-3.5, -1.9, 17.0)] * 300)[-1][0]
    self.assertAlmostEqual(flat, -3.25, delta=0.01)
    self.assertLess(compute_gb_honda_elesys(flat + ACCELERATION_DUE_TO_GRAVITY * math.sin(math.atan(0.10)), 17.0)[1], 0.9,
                    "the flat floor's shortfall on a 10% climb, which the pitch removes")

  def test_controlsd_passes_the_pitch(self):
    # the floor is lowered in controlsd, which owns the pose: pin the hunk so a merge cannot drop it silently
    from openpilot.selfdrive.controls import controlsd
    self.assertTrue(elesys_pid_floor_applies(_cp(), _CP_SP()))
    init, ctl = inspect.getsource(controlsd.Controls.__init__), inspect.getsource(controlsd.Controls.state_control)
    self.assertTrue("elesys_pid_floor_applies(self.CP, self.CP_SP)" in init)
    hunk = "\n".join(("    if self.elesys_pid_floor:",
                       "      pitch = self.calibrated_pose.orientation.y if self.calibrated_pose is not None else None",
                       "      pid_accel_limits = (elesys_pid_accel_min(self.CP, self.CP_SP, pitch), pid_accel_limits[1])", ""))
    self.assertTrue(hunk in ctl, "the controlsd hunk")
    self.assertLess(ctl.index("get_pid_accel_limits("), ctl.index("elesys_pid_accel_min("))
    self.assertLess(ctl.index("elesys_pid_accel_min("), ctl.index("self.LoC.update("))
    # no pose yet: upstream's floor
    self.assertEqual(elesys_pid_accel_min(_cp(), _CP_SP(), None), UPSTREAM_FLOOR)
