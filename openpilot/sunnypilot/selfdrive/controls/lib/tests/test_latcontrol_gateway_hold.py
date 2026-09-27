"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

The LIN-bus gateway's integrator hold (LatControl._linbus_integrator_gate) through the torque
controller extension. LatControlTorqueExt runs on the owning controller's PID and updates it a
second time in the frame when Lateral Jerk (or NNLC) is on, so that second update has to respect
the hold as well: while the board is not actuating the loop is open, and nothing may be
integrated against a car that is not listening.
"""
from openpilot.cereal import log
from opendbc.car.structs import car
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.vehicle_model import VehicleModel
from openpilot.common.parameterized import parameterized
from openpilot.common.params import Params
from openpilot.common.realtime import DT_CTRL
from openpilot.common.test import OpenpilotTestCase
from openpilot.common.mock.generators import generate_deviceMotion
from openpilot.selfdrive.car.helpers import convert_to_capnp
from openpilot.selfdrive.controls.lib.latcontrol_torque import LatControlTorque
from openpilot.selfdrive.locationd.helpers import Pose
from openpilot.sunnypilot.selfdrive.car import interfaces as sunnypilot_interfaces
from openpilot.sunnypilot.selfdrive.controls.lib.latcontrol_torque_v0 import LatControlTorque as LatControlTorqueV0
from openpilot.sunnypilot.selfdrive.controls.lib.tests.test_latcontrol_torque_ext import _make_model_v2

CONTROLLERS = [("v1", LatControlTorque), ("v0", LatControlTorqueV0)]


def _run(cls, present: bool, frames: int = 300) -> float:
  """3 s at 25 m/s on a small, unsaturated curvature, Lateral Jerk on, the board NOT actuating."""
  params = Params()
  params.put_bool("EnforceTorqueControl", True, block=True)
  params.put_bool("LateralJerkTorqueController", True, block=True)
  params.put_bool("NeuralNetworkLateralControl", False, block=True)

  car_name = HONDA.HONDA_ACCORD_9G_AU
  CarInterface = interfaces[car_name]
  CP = CarInterface.get_non_essential_params(car_name)
  CP_SP = CarInterface.get_non_essential_params_sp(CP, car_name)
  CI = CarInterface(CP, CP_SP)
  sunnypilot_interfaces.setup_interfaces(CI, params)
  CP_SP = convert_to_capnp(CP_SP)
  VM = VehicleModel(CP)
  controller = cls(CP.as_reader(), CP_SP.as_reader(), CI, DT_CTRL)
  assert controller.extension._jerk_aware_enabled

  controller.set_linbus_gateway(present, False)
  CS = car.CarState.new_message()
  CS.vEgo = 25.0
  CS.steeringPressed = False
  pose = Pose.from_device_motion(generate_deviceMotion().deviceMotion)
  vehicle_params = log.VehicleParameters.new_message()
  controller.extension.update_model_v2(_make_model_v2().modelV2)
  controller.extension.update_lateral_lag(0.38)
  controller.extension.update_limits()
  for _ in range(frames):
    controller.update(True, CS, VM, vehicle_params, False, 0.0002, pose, False, 0.38)
  assert controller.integrator_frozen == present
  return float(controller.pid.i)


class TestGatewayHoldThroughTheExtension(OpenpilotTestCase):
  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_lateral_jerk_does_not_wind_the_integrator_while_held(self, label, cls):
    assert _run(cls, present=True) == 0.0, f"{label}: integrated open-loop while the board was not actuating"

  @parameterized.expand(CONTROLLERS, names=["label", "cls"], ids=lambda label, cls: label)
  def test_without_a_board_it_integrates_as_upstream(self, label, cls):
    # the control: the same run does wind the integrator when there is no gateway to hold for
    assert abs(_run(cls, present=False)) > 1e-3, label
