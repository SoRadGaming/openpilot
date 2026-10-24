"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): brake320's customReserved14 (brake320_log.py, card.py). What must hold: the service is
logged and in the qlog; the struct carries exactly what brake_ceiling.brake320_status() reports; card sends it once
a second and on the step the ceiling or the VSA fallback changes; only this car with openpilot longitudinal gets a
Brake320Log (every other car and stock ACC mode: none, no socket); it never raises; and card calls it from
state_publish with no Params I/O.
"""
from typing import Any, cast
import ast
import logging
from pathlib import Path
from unittest import mock

from opendbc.car import gen_empty_fingerprint
from opendbc.car.carlog import carlog
from opendbc.car.honda.interface import CarInterface
from opendbc.car.honda.values import CAR
from opendbc.sunnypilot.car.honda.brake_ceiling import brake320_status
from opendbc.sunnypilot.car.interfaces import setup_interfaces
from openpilot.cereal import custom, log
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.car import brake320_log
from openpilot.sunnypilot.selfdrive.car.brake320_log import Brake320Log, PUBLISH_EVERY

ROOT = Path(__file__).parents[4]   # openpilot/
CARD = ROOT / "selfdrive/car/card.py"
FIELDS = ("allowedCounts", "ceilingCounts", "vsaFallback", "vsaFallbackReason", "vsaFallbackFrame", "maxCountsLastSecond",
          "sendsAbove255LastSecond", "maxCountsDrive", "sendsAbove255Drive")


def make_ci(car=CAR.HONDA_ACCORD_9G_AU, stock_acc=False):
  fp = gen_empty_fingerprint()
  fp[0][0x201] = 6
  fp[0][0x188] = 8
  CP = CarInterface.get_params(car, fp, [], False, False, False)
  CP_SP = CarInterface.get_params_sp(CP, car, fp, [], False, False, False)
  setup_interfaces(CarInterface, CP, CP_SP, [{"HondaElesysStockAcc": stock_acc}] if stock_acc else [])
  with mock.patch.object(carlog, "level", logging.CRITICAL):
    return CarInterface(CP, CP_SP)


class FakePM:
  def __init__(self):
    self.sent = []

  def send(self, name, msg):
    self.sent.append((name, msg.as_reader() if hasattr(msg, "as_reader") else msg))


class TestBrake320Log(OpenpilotTestCase):
  def test_the_service_is_logged_in_the_qlog(self):
    svc = SERVICE_LIST["customReserved14"]
    self.assertTrue(svc.should_log)
    self.assertEqual(svc.frequency, 1.)
    self.assertEqual(svc.decimation, 1)
    self.assertIn("customReserved14", log.Event.schema.fields)

  def test_the_struct_is_the_status(self):
    self.assertEqual(tuple(custom.CustomReserved14.schema.fieldnames), FIELDS)
    self.assertEqual(set(cast(dict, brake320_status(make_ci()))), set(FIELDS))

  def test_one_a_second_and_on_change(self):
    ci = make_ci()
    pm = FakePM()
    b = Brake320Log.create(ci, pm)
    self.assertIsNotNone(b)
    for _ in range(3 * PUBLISH_EVERY):
      b.update()
    self.assertEqual(len(pm.sent), 3)
    m = pm.sent[0][1]
    self.assertEqual(pm.sent[0][0], "customReserved14")
    self.assertTrue(m.valid)
    r = m.customReserved14
    self.assertEqual((r.allowedCounts, r.ceilingCounts, r.vsaFallback, r.vsaFallbackReason), (320, 320, False, ""))
    # the fallback latches mid-second: sent on that very step, then on the 1 Hz beat again
    ci.CS.brake320_fallback.reason, ci.CS.brake320_fallback.latched_frame = "vsaFault", 4321
    b.update()
    self.assertEqual(len(pm.sent), 4)
    r = pm.sent[-1][1].customReserved14
    self.assertEqual((r.ceilingCounts, r.vsaFallback, r.vsaFallbackReason, r.vsaFallbackFrame), (255, True, "vsaFault", 4321))
    for _ in range(PUBLISH_EVERY):
      b.update()
    self.assertEqual(len(pm.sent), 5)
    # the counts the CarController noted
    ci.CC.brake320.last_max, ci.CC.brake320.last_above, ci.CC.brake320.drive_max, ci.CC.brake320.drive_above = 317, 50, 320, 812
    for _ in range(PUBLISH_EVERY):
      b.update()
    r = pm.sent[-1][1].customReserved14
    self.assertEqual((r.maxCountsLastSecond, r.sendsAbove255LastSecond, r.maxCountsDrive, r.sendsAbove255Drive), (317, 50, 320, 812))

  def test_only_this_car_with_openpilot_long(self):
    for ci in (make_ci(CAR.HONDA_CRV), make_ci(CAR.ACURA_ILX), make_ci(stock_acc=True)):
      self.assertIsNone(Brake320Log.create(ci, FakePM()))
    with mock.patch.object(brake320_log.messaging, "PubMaster") as pm:
      self.assertIsNone(Brake320Log.create(make_ci(CAR.HONDA_CRV)))
      pm.assert_not_called()

  def test_never_raises(self):
    b = Brake320Log.create(make_ci(), FakePM())
    with mock.patch.object(brake320_log, "brake320_status", side_effect=RuntimeError("boom")):
      b.update()

    class BadPM:
      def send(self, *a):
        raise RuntimeError("socket")
    cast(Any, b).pm = BadPM()
    b.update()
    with mock.patch.object(brake320_log, "brake320_status", side_effect=RuntimeError("boom")):
      self.assertIsNone(Brake320Log.create(make_ci(), FakePM()))

  def test_card_calls_it_from_state_publish(self):
    tree = ast.parse(CARD.read_text(encoding="utf-8"))
    car = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Car")
    meth = {n.name: ast.unparse(n) for n in car.body if isinstance(n, ast.FunctionDef)}
    self.assertIn("self.brake320_log = Brake320Log.create(self.CI)", meth["__init__"])
    self.assertIn("if self.brake320_log is not None:\n        self.brake320_log.update()", meth["state_publish"])
    for name in ("state_publish", "state_update", "step", "controls_update"):
      self.assertNotIn("brake320_log.create", meth[name].lower().replace("brake320log.create", "x"))
    src = (ROOT / "sunnypilot/selfdrive/car/brake320_log.py").read_text(encoding="utf-8")
    self.assertNotIn("openpilot.common.params", src)
    self.assertNotIn(".put", src)
