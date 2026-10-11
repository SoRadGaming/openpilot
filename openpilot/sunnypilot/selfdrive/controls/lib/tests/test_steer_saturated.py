"""
FORK(LKAS-GATEWAY): the steerSaturated decision (steer_saturated.py) - selfdrived's alert and 0x500 OP_SATURATED.

  * the helper is upstream selfdrived's block, condition for condition (a reference copy of that block below, fuzzed);
  * selfdrived.py really calls it;
  * controlsd_ext puts it - and not the raw lac.saturated - in CarControlSP.lateralControl.steerSaturated, which is
    what hondacan packs as OP_SATURATED;
  * the two lane changes that lit the board's alarm (120@486.24, 121@330.31) no longer set it.
"""
import random
import unittest
from pathlib import Path
from types import SimpleNamespace

import openpilot.cereal.messaging as messaging
from openpilot.common.realtime import DT_CTRL
from openpilot.sunnypilot.selfdrive.controls.controlsd_ext import ControlsExt
from openpilot.sunnypilot.selfdrive.controls.lib.steer_saturated import STEER_PRESSED_HOLDOFF, SteerSaturatedAlert

SELFDRIVED = Path(__file__).resolve().parents[5] / "selfdrive" / "selfdrived" / "selfdrived.py"


class _UpstreamSelfdrived:
  """selfdrived.py:498-512 as it stood at af45423ad / nsw-live d95ce71c3, verbatim apart from the names."""

  def __init__(self):
    self.last_steering_pressed_frame = 0

  def update(self, frame, CS, lac, curvature, desired_curvature, not_car=False):
    if CS.steeringPressed:
      self.last_steering_pressed_frame = frame
    recent_steer_pressed = (frame - self.last_steering_pressed_frame)*DT_CTRL < 2.0
    if lac.active and not recent_steer_pressed and not not_car:
      clipped_speed = max(CS.vEgo, 0.3)
      actual_lateral_accel = curvature * (clipped_speed**2)
      desired_lateral_accel = desired_curvature * (clipped_speed**2)
      undershooting = abs(desired_lateral_accel) / abs(1e-3 + actual_lateral_accel) > 1.2
      turning = abs(desired_lateral_accel) > 1.0
      if undershooting and turning and lac.saturated:
        return True
    return False


def _alert(n=300, pressed_at=None, active=True, saturated=True, v=20.0, curvature=0.5 / 400, desired=1.5 / 400, not_car=False):
  a = SteerSaturatedAlert()
  out = []
  for i in range(n):
    out.append(a.update(pressed_at is not None and i == pressed_at, active, saturated, v, curvature, desired, not_car))
  return out


class TestSteerSaturatedHelper(unittest.TestCase):
  def test_all_conditions_met_alerts_after_the_startup_window(self):
    out = _alert()
    hold = round(STEER_PRESSED_HOLDOFF / DT_CTRL)
    self.assertFalse(any(out[:hold]))   # selfdrived starts with last_steering_pressed_frame = 0 too
    self.assertTrue(all(out[hold:]))

  def test_each_condition_blocks_on_its_own(self):
    self.assertFalse(any(_alert(saturated=False)))
    self.assertFalse(any(_alert(active=False)))
    self.assertFalse(any(_alert(not_car=True)))
    self.assertFalse(any(_alert(desired=0.99 / 400)))                       # not turning: 0.99 m/s^2
    self.assertFalse(any(_alert(curvature=1.3 / 400, desired=1.5 / 400)))   # not undershooting: 1.5 / 1.3 < 1.2
    self.assertFalse(any(_alert(curvature=-1.3 / 400, desired=-1.5 / 400)))

  def test_a_press_holds_it_off_for_exactly_two_seconds(self):
    out = _alert(n=800, pressed_at=400)
    hold = round(STEER_PRESSED_HOLDOFF / DT_CTRL)
    self.assertTrue(out[399])
    self.assertFalse(any(out[400:400 + hold]))
    self.assertTrue(all(out[400 + hold:]))

  def test_matches_upstream_selfdrived_on_random_inputs(self):
    rng = random.Random(20261012)
    ours, ref = SteerSaturatedAlert(), _UpstreamSelfdrived()
    disagreements, fired = 0, 0
    for frame in range(20000):
      CS = SimpleNamespace(steeringPressed=rng.random() < 0.004, vEgo=rng.choice([0.0, 0.2, 5.0, 15.0, 25.0, 33.0]))
      lac = SimpleNamespace(active=rng.random() < 0.9, saturated=rng.random() < 0.6)
      curvature = rng.uniform(-3.0, 3.0) / max(CS.vEgo, 0.3) ** 2
      desired = rng.uniform(-3.0, 3.0) / max(CS.vEgo, 0.3) ** 2
      not_car = rng.random() < 0.01
      a = ours.update(CS.steeringPressed, lac.active, lac.saturated, CS.vEgo, curvature, desired, not_car, frame=frame)
      b = ref.update(frame, CS, lac, curvature, desired, not_car)
      disagreements += a != b
      fired += b
    self.assertEqual(disagreements, 0)
    self.assertGreater(fired, 100)   # the fuzz really reaches the alert

  def test_selfdrived_calls_the_helper(self):
    src = SELFDRIVED.read_text()
    self.assertTrue("self.steer_saturated.update(CS.steeringPressed, lac.active, lac.saturated, CS.vEgo, controlstate.curvature," in src)
    self.assertTrue("frame=self.sm.frame" in src)
    self.assertNotIn("undershooting", src)   # one copy of the conditions, in steer_saturated.py


def _ext():
  ext = ControlsExt.__new__(ControlsExt)   # the real __init__ blocks on CarParamsSP
  ext.CP = SimpleNamespace(notCar=False)
  ext.steer_saturated = SteerSaturatedAlert()
  ext.curvature = 0.0
  return ext


def _sm(v_ego, pressed, desired_curvature):
  sm = {s: getattr(messaging.new_message(s), s) for s in ('radarState', 'selfdriveStateSP')}
  cs = messaging.new_message('carState').carState
  cs.vEgo = v_ego
  cs.steeringPressed = pressed
  mv = messaging.new_message('modelV2').modelV2
  mv.action.desiredCurvature = desired_curvature
  sm['carState'] = cs
  sm['modelV2'] = mv
  return sm


def _lac(active=True, saturated=True):
  lac = messaging.new_message('controlsState').controlsState.lateralControlState.init('torqueState')
  lac.active = active
  lac.saturated = saturated
  lac.i = 0.1
  return lac


class TestControlsExtOpSaturated(unittest.TestCase):
  def _run(self, frames, v, desired_la, actual_la, saturated=True, press_frames=()):
    ext = _ext()
    ext.curvature = actual_la / v ** 2
    out = []
    for i in range(frames):
      cc_sp = ext.state_control_ext(_sm(v, i in press_frames, desired_la / v ** 2), _lac(saturated=saturated))
      out.append((bool(cc_sp.lateralControl.saturated), bool(cc_sp.lateralControl.steerSaturated)))
    return out

  def test_raw_saturated_is_still_logged_but_not_sent(self):
    out = self._run(400, 20.7, -0.82, -0.38)             # 121@330.6: undershooting, but desired 0.82 < 1.0
    self.assertTrue(all(o[0] for o in out))
    self.assertFalse(any(o[1] for o in out))

  def test_a_recent_nudge_blocks_it(self):
    out = self._run(400, 16.0, 1.4, 0.4, press_frames=range(250, 260))   # turning and undershooting, nudged
    self.assertTrue(out[249][1])
    self.assertFalse(any(o[1] for o in out[250:]))   # 2 s from the last press frame (259) is past the end

  def test_openpilots_own_alert_conditions_still_reach_the_board(self):
    out = self._run(400, 25.0, -1.6, -0.5)
    self.assertTrue(out[-1][1])
    self.assertFalse(self._run(400, 25.0, -1.6, -0.5, saturated=False)[-1][1])

  def test_no_lateral_log_leaves_it_false(self):
    ext = _ext()
    cc_sp = ext.state_control_ext(_sm(20.0, False, 0.01), None)
    self.assertFalse(cc_sp.lateralControl.steerSaturated)


if __name__ == "__main__":
  unittest.main()
