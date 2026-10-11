"""
FORK(HONDA_ELESYS): what the reported torque does to the lateral controller.

opendbc's Honda CarController reports carOutput.actuatorsOutput.torque = 0 while the gateway board is not
actuating. controlsd then sets steer_limited_by_safety = |actuators.torque - actuatorsOutput.torque| > 1e-2,
which freezes the integrator and keeps _check_saturation from counting, so no "turn exceeds limit" alert
fires for a car that is not being steered. While the board IS actuating the report is last_torque, so a
real saturation still alerts.

The board's lane-change ceiling (0x704 INH_BLINKER, carStateSP.linbusGateway.blinkerCapped) is deliberately NOT
reported: clipped to it, the report was "limited by safety" under the ceiling, so a real shortfall there could never
saturate, and steerSaturated - openpilot's alert, and 0x500 OP_SATURATED since lcalert - was lost for as long as the
blinker was on. A railed request under the ceiling must still saturate, and a hands-off curve must still alert.
"""
from pathlib import Path

from openpilot.cereal import log
from opendbc.car import structs
from opendbc.car.structs import car
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.carcontroller import CarController
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.parameterized import parameterized
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.common.mock.generators import generate_deviceMotion
from openpilot.selfdrive.car.helpers import convert_to_capnp
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.locationd.helpers import Pose
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces
from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_v0 import LatControlTorque as LatControlTorqueV0
from openpilot.sunnypilot.selfdrive.controls.lib.steer_saturated import SteerSaturatedAlert

CONTROLLERS = [("v1", LatControlTorque), ("v0", LatControlTorqueV0)]
CAR_NAME = HONDA.HONDA_ACCORD_9G_AU
CONTROLSD = Path(__file__).resolve().parents[5] / "selfdrive" / "controls" / "controlsd.py"


def _setup(cls):
  CarInterface = interfaces[CAR_NAME]
  CP = CarInterface.get_non_essential_params(CAR_NAME)
  CP_SP = CarInterface.get_non_essential_params_sp(CP, CAR_NAME)
  CI = CarInterface(CP, CP_SP)
  sunnypilot_interfaces.setup_interfaces(CI)
  cc_obj = CarController(CAR_NAME.config.dbc_dict, CP, CP_SP)
  lac = cls(CP.as_reader(), convert_to_capnp(CP_SP).as_reader(), CI, DT_CTRL)
  return CP, cc_obj, lac, VehicleModel(CP)


class _CarCS:
  """What the Honda CarController reads, plus the gateway state."""

  def __init__(self, actuating, capped=False):
    self.out = structs.CarState.new_message()
    self.out.vEgo = 30.0
    self.out.cruiseState.speed = 30.0
    self.v_cruise_factor = 1.0
    self.stock_brake = {"CHIME": 0, "AEB_REQ_1": 0, "AEB_REQ_2": 0, "AEB_STATUS": 0}
    self.acc_hud = {"FCM_OFF": 0, "FCM_OFF_2": 0, "FCM_PROBLEM": 0, "ICONS": 0}
    self.lkas_hud = {}
    self.scm_buttons = {"CRUISE_BUTTONS": 0, "CRUISE_SETTING": 0}
    self.is_metric = True
    self.econ_on = False
    self.out_sp = structs.CarStateSP()
    gw = self.out_sp.linbusGateway
    gw.present = True
    gw.engaged = gw.valid = gw.actuating = actuating
    gw.blinkerCapped = capped


def _closed_loop(cls, actuating, frames=1000, capped=False, actual_lat_accel=0.45):
  """controlsd's loop, cut down: LaC -> CarController -> the steer_limited_by_safety rule -> LaC.

  Asks for far more curvature than the car can give, so the torque controller rails at 1.0. Also runs
  controlsd_ext's steerSaturated (SteerSaturatedAlert, hands off, the car making actual_lat_accel of the 18 m/s^2
  asked for) into CarControlSP, and reads OP_SATURATED back out of the 0x500 the CarController sends."""
  _, cc_obj, lac, VM = _setup(cls)
  lac.set_linbus_gateway(True, actuating)
  CS = car.CarState.new_message()
  CS.vEgo = 30.0
  CS.steeringPressed = False
  vehicle_params = log.VehicleParameters.new_message()
  pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
  car_cs = _CarCS(actuating, capped)
  steer_limited_by_safety = False
  alert = SteerSaturatedAlert()
  saturated, limited, steer_saturated, op_saturated = [], [], [], []
  for i in range(frames):
    steer, _, lac_log = lac.update(True, CS, VM, vehicle_params, steer_limited_by_safety, 0.02, pose, False, 0.38)
    cc = structs.CarControl.new_message()
    cc.enabled = cc.latActive = True
    cc.actuators.torque = float(steer)
    cc.hudControl.speedVisible = True
    cc.hudControl.setSpeed = 30.0
    cc_sp = structs.CarControlSP()
    cc_sp.lateralControl.saturated = bool(lac_log.saturated)
    cc_sp.lateralControl.steerSaturated = alert.update(False, bool(lac_log.active), bool(lac_log.saturated), CS.vEgo,
                                                       actual_lat_accel / CS.vEgo ** 2, 0.02, False)
    out, sends = cc_obj.update(cc.as_reader(), cc_sp, car_cs, i * int(1e7))
    # controlsd.publish(), torque cars
    steer_limited_by_safety = abs(cc.actuators.torque - out.torque) > 1e-2
    saturated.append(bool(lac_log.saturated))
    limited.append(steer_limited_by_safety)
    steer_saturated.append(cc_sp.lateralControl.steerSaturated)
    hud = [bytes(s[1]) for s in sends if s[0] == 0x500]
    op_saturated.append(bool(hud[0][4] & 0x01) if hud else None)
  return saturated, limited, steer_saturated, op_saturated


class TestReportedTorqueAndSaturation(OpenpilotTestCase):
  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_steer_limited_by_safety_blocks_saturation(self, label, cls):
    _, _, lac, VM = _setup(cls)
    CS = car.CarState.new_message()
    CS.vEgo = 30.0
    CS.steeringPressed = False
    vehicle_params = log.VehicleParameters.new_message()
    pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
    for _ in range(1000):
      _, _, lac_log = lac.update(True, CS, VM, vehicle_params, True, 0.02, pose, True, 0.38)
      self.assertFalse(lac_log.saturated, label)
    # the control: the same frames without the safety limit do saturate
    for _ in range(1000):
      _, _, lac_log = lac.update(True, CS, VM, vehicle_params, False, 0.02, pose, True, 0.38)
    self.assertTrue(lac_log.saturated, label)

  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_no_saturation_alert_while_the_board_is_not_steering(self, label, cls):
    saturated, limited, steer_saturated, _ = _closed_loop(cls, actuating=False)
    self.assertFalse(any(saturated), label)
    self.assertFalse(any(steer_saturated), label)
    self.assertTrue(all(limited[1:]), label)

  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_a_real_saturation_still_alerts_while_actuating(self, label, cls):
    saturated, limited, *_ = _closed_loop(cls, actuating=True)
    self.assertTrue(saturated[-1], label)
    self.assertFalse(limited[-1], label)

  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_a_shortfall_under_the_blinker_ceiling_still_alerts(self, label, cls):
    # Blinker on (or left on) in a curve, hands off: the board gives half its authority and the car makes
    # 0.45 m/s^2 of the curve. The ceiling is not "limited by safety", so the railed request saturates as it
    # does without the ceiling, steerSaturated follows, and 0x500 carries it to the board as OP_SATURATED.
    capped = _closed_loop(cls, actuating=True, capped=True)
    self.assertEqual(capped, _closed_loop(cls, actuating=True, capped=False), label)   # the ceiling changes nothing
    saturated, limited, steer_saturated, op_saturated = capped
    self.assertFalse(limited[-1], label)                     # the rate limiter's ramp only, never the ceiling
    self.assertTrue(saturated[-1], label)
    self.assertTrue(steer_saturated[-1], label)
    sent = [(s, o) for s, o in zip(steer_saturated, op_saturated, strict=True) if o is not None]
    self.assertTrue(all(s == o for s, o in sent), label)    # OP_SATURATED is steerSaturated, frame for frame
    self.assertTrue(any(o for _, o in sent) and not all(o for _, o in sent), label)

  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_raw_saturation_without_the_alert_conditions_is_not_sent(self, label, cls):
    # the lane-change case (120@486.24, 121@330.31): saturated, but not undershooting what was asked for, so
    # neither steerSaturated nor OP_SATURATED
    saturated, _, steer_saturated, op_saturated = _closed_loop(cls, actuating=True, capped=True, actual_lat_accel=18.0)
    self.assertTrue(saturated[-1], label)
    self.assertFalse(any(steer_saturated), label)
    self.assertFalse(any(o for o in op_saturated if o is not None), label)

  def test_controlsd_still_uses_the_reported_torque(self):
    # the closed loop above copies this line; if upstream changes it, revisit the opendbc report
    src = CONTROLSD.read_text()
    self.assertTrue("self.steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2" in src)
