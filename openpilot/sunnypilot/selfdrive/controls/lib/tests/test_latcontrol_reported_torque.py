"""
FORK(HONDA_ELESYS): what the reported torque does to the lateral controller.

opendbc's Honda CarController reports carOutput.actuatorsOutput.torque = 0 while the gateway board is not
actuating. controlsd then sets steer_limited_by_safety = |actuators.torque - actuatorsOutput.torque| > 1e-2,
which freezes the integrator and keeps _check_saturation from counting, so no "turn exceeds limit" alert
fires for a car that is not being steered. While the board IS actuating the report is last_torque, so a
real saturation still alerts.
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

  def __init__(self, actuating):
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


def _closed_loop(cls, actuating, frames=1000):
  """controlsd's loop, cut down: LaC -> CarController -> the steer_limited_by_safety rule -> LaC.

  Asks for far more curvature than the car can give, so the torque controller rails at 1.0."""
  _, cc_obj, lac, VM = _setup(cls)
  lac.set_linbus_gateway(True, actuating)
  CS = car.CarState.new_message()
  CS.vEgo = 30.0
  CS.steeringPressed = False
  vehicle_params = log.VehicleParameters.new_message()
  pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
  car_cs = _CarCS(actuating)
  steer_limited_by_safety = False
  saturated, limited = [], []
  for i in range(frames):
    steer, _, lac_log = lac.update(True, CS, VM, vehicle_params, steer_limited_by_safety, 0.02, pose, False, 0.38)
    cc = structs.CarControl.new_message()
    cc.enabled = cc.latActive = True
    cc.actuators.torque = float(steer)
    cc.hudControl.speedVisible = True
    cc.hudControl.setSpeed = 30.0
    out, _ = cc_obj.update(cc.as_reader(), structs.CarControlSP(), car_cs, i * int(1e7))
    # controlsd.publish(), torque cars
    steer_limited_by_safety = abs(cc.actuators.torque - out.torque) > 1e-2
    saturated.append(bool(lac_log.saturated))
    limited.append(steer_limited_by_safety)
  return saturated, limited


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
    saturated, limited = _closed_loop(cls, actuating=False)
    self.assertFalse(any(saturated), label)
    self.assertTrue(all(limited[1:]), label)

  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_a_real_saturation_still_alerts_while_actuating(self, label, cls):
    saturated, limited = _closed_loop(cls, actuating=True)
    self.assertTrue(saturated[-1], label)
    self.assertFalse(limited[-1], label)

  def test_controlsd_still_uses_the_reported_torque(self):
    # the closed loop above copies this line; if upstream changes it, revisit the opendbc report
    src = CONTROLSD.read_text()
    self.assertIn("self.steer_limited_by_safety = abs(CC.actuators.torque - CO.actuatorsOutput.torque) > 1e-2", src)
