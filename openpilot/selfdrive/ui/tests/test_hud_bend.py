"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(BENDS): the comma 4's curve item and the bend ease (hud_model.bend_target). In live mode the ease
(sunnypilot/selfdrive/controls/lib/smart_cruise_control/bend_ease.py) takes over the planner's target as sccVision while
SCC-V itself may be idle or asking for more; the item then shows where the ease's target is heading (its vGoal), and
nothing changes in shadow mode, off, or on a car that publishes no customReserved11.
"""
import ast
from collections import defaultdict
from pathlib import Path

from openpilot.cereal import messaging
from openpilot.cereal.services import SERVICE_LIST
from openpilot.common.constants import CV
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model as HM
from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_settings as HS

UI_STATE_SP = Path(__file__).parents[1] / "sunnypilot/ui_state.py"

KPH = CV.MS_TO_KPH
STARTED = 5   # the drive's started_frame; put() receives at frame 10


class FakeSM:
  """What hud_model reads of a SubMaster: this drive's messages, all arriving."""
  def __init__(self):
    self.data = {}
    self.seen = defaultdict(bool)
    self.alive = defaultdict(bool)
    self.recv_frame = defaultdict(int)

  def __getitem__(self, svc):
    if svc not in self.data:
      self.data[svc] = getattr(messaging.new_message(svc), svc)
    return self.data[svc]

  def put(self, svc):
    self.seen[svc] = self.alive[svc] = True
    self.recv_frame[svc] = 10
    return self[svc]


def hud_sm(v=76.5 / KPH, src='sccVision', vision=None, vision_v=90 / KPH, bend=(2, True, True, 66.6 / KPH)):
  """The car at 76.5 km/h (fd t=539's start speed). vision: SCC-V's state (None: disabled) and its own vTarget.
  bend: customReserved11 (mode, limiting, active, vGoal), None for a car that publishes none."""
  sm = FakeSM()
  cs = sm.put('carState')
  cs.vEgo = cs.vEgoCluster = v
  sm.put('carControl').longActive = True
  p = sm.put('longitudinalPlanSP')
  p.longitudinalPlanSource = src
  if vision is not None:
    p.smartCruiseControl.vision.state = vision
    p.smartCruiseControl.vision.active = vision in ('entering', 'turning', 'leaving')
    p.smartCruiseControl.vision.vTarget = vision_v
  if bend is not None:
    b = sm.put(HM.BEND_SERVICE)
    b.mode, b.limiting, b.active, b.vGoal = bend
    b.vTarget = v - 0.6
  return sm


class TestCurveItemAndTheBendEase(OpenpilotTestCase):
  def test_live_and_limiting_shows_where_it_is_heading(self):
    for vision in (None, 'enabled', 'entering', 'turning', 'leaving'):
      v = HM.curve_target(hud_sm(vision=vision), STARTED)
      assert v is not None, vision
      self.assertAlmostEqual(v * KPH, 66.6, places=3, msg=f"its vGoal, not SCC-V's ({vision})")

  def test_otherwise_the_stock_rule(self):
    # shadow (it changes nothing), off, not limiting, between episodes, no message (every other car): SCC-V's own rule -
    # its target while it enters or turns, nothing in its other states
    for bend in ((1, True, True, 66.6 / KPH), (0, False, False, 0.0), (2, False, True, 66.6 / KPH),
                 (2, True, False, 66.6 / KPH), (2, True, True, 0.0), None):
      self.assertIsNone(HM.curve_target(hud_sm(vision=None, bend=bend), STARTED), bend)
      self.assertIsNone(HM.curve_target(hud_sm(vision='leaving', bend=bend), STARTED), bend)
      v = HM.curve_target(hud_sm(vision='entering', bend=bend), STARTED)
      self.assertAlmostEqual((v or 0.0) * KPH, 90.0, places=3, msg=str(bend))

  def test_only_while_it_is_the_planners_source(self):
    for src in ('cruise', 'sccMap', 'speedLimitAssist'):
      self.assertIsNone(HM.curve_target(hud_sm(src=src), STARTED), src)

  def test_a_stale_message_shows_nothing(self):
    sm = hud_sm()
    sm.alive[HM.BEND_SERVICE] = False
    self.assertIsNone(HM.curve_target(sm, STARTED))
    sm = hud_sm()
    sm.recv_frame[HM.BEND_SERVICE] = STARTED - 1   # last drive's
    self.assertIsNone(HM.curve_target(sm, STARTED))

  def test_on_the_rail(self):
    # slowing toward 66.6 from 76.5: the curve item, in 5 km/h steps; the ease only holding the accel (its goal at or
    # over the car's speed - the accel cap) is nothing to slow for, so nothing shows
    rail, t, f = HM.RailState(), 100.0, HM.RailFrame()
    for _ in range(20):
      t += 0.05
      f = rail.update(hud_sm(), HS.ALL_ON, started_frame=STARTED, now=t, is_metric=True)
    self.assertEqual(f.kind, HM.RAIL_CURVE)
    self.assertEqual((f.num, f.unit), ("65", "km/h"))
    rail, f = HM.RailState(), HM.RailFrame()
    for _ in range(40):
      t += 0.05
      f = rail.update(hud_sm(bend=(2, True, True, 77.0 / KPH)), HS.ALL_ON, started_frame=STARTED, now=t, is_metric=True)
    self.assertEqual(f.kind, HM.RAIL_NONE)

  def test_the_ui_subscribes(self):
    # the UI's SubMaster takes sm_services_ext (ui_state.py); read from the source, constructing UIStateSP starts sunnylink
    tree = ast.parse(UI_STATE_SP.read_text(encoding="utf-8"))
    lists = [ast.literal_eval(n.value) for n in ast.walk(tree) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Attribute) and t.attr == 'sm_services_ext' for t in n.targets)]
    self.assertEqual(len(lists), 1)
    self.assertTrue(HM.BEND_SERVICE in lists[0], lists[0])
    self.assertTrue(HM.BEND_SERVICE in SERVICE_LIST)
