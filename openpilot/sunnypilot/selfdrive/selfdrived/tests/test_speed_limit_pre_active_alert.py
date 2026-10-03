"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): the comma 4's speed-limit confirm text, 'Press + (or -) to confirm speed limit'. It compared the set
speed - CS.vCruiseCluster, already km/h - converted AGAIN as if it were m/s (x3.6), so a 50 km/h set speed read as 180
and the text said 'Press -' every time '+' was needed: all four such prompts on routes 114 and 115, and 'Press +' never
once in 9 routes. The buttons, the arrow and the HUD's key were right; only the words were wrong.

What it must do now: say exactly what the confirm itself accepts (compare_cluster_target, speed_limit_assist.py), what
the car side swallows (VCruiseHelperSP.req_plus / req_minus) and what the arrow beside it shows, in km/h and mph; and
leave the PCM-long text alone.
"""
from types import SimpleNamespace

from opendbc.car.structs import car
from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.car.cruise_ext import VCruiseHelperSP
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.helpers import compare_cluster_target
from openpilot.sunnypilot.selfdrive.selfdrived import events as ev

PLUS, MINUS = "Press + to confirm speed limit", "Press - to confirm speed limit"
KM_TO_MILE = 0.621371  # the arrow's own constant (selfdrive/ui/sunnypilot/onroad/speed_limit.py)


def sm(limit_ms: float, v_cruise_deprecated: float = 0.0):
  res = SimpleNamespace(speedLimitFinalLast=limit_ms, speedLimitValid=True, speedLimitLastValid=True)
  return {'longitudinalPlanSP': SimpleNamespace(speedLimit=SimpleNamespace(resolver=res)),
          'controlsState': SimpleNamespace(deprecated=SimpleNamespace(vCruise=v_cruise_deprecated))}


def text(set_kph: float, limit_ms: float, metric: bool = True, pcm: bool = False) -> str:
  """The confirm alert's text1 on a comma 4 (IS_MICI), openpilot long."""
  cp = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=pcm)
  saved, ev.IS_MICI = ev.IS_MICI, True
  try:
    return ev.speed_limit_pre_active_alert(cp, SimpleNamespace(vCruiseCluster=float(set_kph)), sm(limit_ms), metric, 0,
                                           None).alert_text_1
  finally:
    ev.IS_MICI = saved


def kph(x: float) -> float:
  return x / 3.6


def mph(x: float) -> float:
  return x * CV.MPH_TO_MS


def direction(t: str) -> str:
  return "+" if t == PLUS else "-" if t == MINUS else ""


def icon_direction(set_kph: float, limit_ms: float, metric: bool) -> str:
  """SpeedLimitAlertRenderer.speed_limit_pre_active_icon_helper's choice of arrow, its own formula."""
  set_speed = set_kph if metric else set_kph * KM_TO_MILE
  s, lim = round(set_speed), round(limit_ms * (CV.MS_TO_KPH if metric else CV.MS_TO_MPH))
  return "+" if s < lim else "-" if s > lim else ""


class TestSpeedLimitPreActiveText(OpenpilotTestCase):
  def test_the_route_cases(self):
    # routes 114 / 115: every '+' prompt said '-'; the '-' prompts were right
    for set_kph, limit_kph in ((50, 80), (60, 80), (54.4, 60), (59, 80), (72.5, 80), (22, 80), (23, 80)):
      assert text(set_kph, kph(limit_kph)) == PLUS, (set_kph, limit_kph)
    for set_kph, limit_kph in ((105, 50), (80, 60), (55, 50), (60, 50), (105, 60)):
      assert text(set_kph, kph(limit_kph)) == MINUS, (set_kph, limit_kph)
    assert text(60, kph(60)) == "", "already at the limit: nothing to press"

  def test_imperial(self):
    # the set speed is km/h whatever the units: 40 mph is 64.4 km/h in vCruiseCluster
    assert text(64.37, mph(45), metric=False) == PLUS
    assert text(72.42, mph(40), metric=False) == MINUS
    assert text(64.37, mph(40), metric=False) == ""
    assert text(40 * CV.MPH_TO_KPH, mph(41), metric=False) == PLUS

  def test_the_text_says_what_the_confirm_accepts_and_the_car_swallows(self):
    helper = VCruiseHelperSP(car.CarParams(pcmCruise=False), custom.CarParamsSP(pcmCruiseSpeed=True))
    checked = 0
    for metric, limits in ((True, [kph(x) for x in range(20, 115, 5)]), (False, [mph(x) for x in range(15, 75, 5)])):
      for limit_ms in limits:
        lp = SimpleNamespace(speedLimit=SimpleNamespace(resolver=sm(limit_ms)['longitudinalPlanSP'].speedLimit.resolver,
                                                        assist=SimpleNamespace(state=0)))
        for i in range(80, 1451):
          set_kph = i / 10
          d = direction(text(set_kph, limit_ms, metric))
          plus, minus = compare_cluster_target(set_kph * CV.KPH_TO_MS, limit_ms, metric)
          assert d == ("+" if plus else "-" if minus else ""), (set_kph, limit_ms, metric)
          helper.v_cruise_cluster_kph = set_kph  # ty: ignore[invalid-assignment]
          helper.update_speed_limit_assist(metric, lp)
          assert (helper.req_plus, helper.req_minus) == (plus, minus), (set_kph, limit_ms, metric)
          checked += 1
    assert checked > 40000

  def test_the_text_agrees_with_the_arrow(self):
    # The arrow rounds the km/h figure itself; the text (and the confirm) round it after a round trip through m/s. The
    # two can differ only on an exact .5 of the display figure (72.5 km/h is reachable: SET under the gas takes vEgo to
    # 0.1 km/h), where Python's round-half-even and the round trip's last bit disagree - and there the confirm treats
    # the set speed as at the limit anyway. Everywhere else: the same answer.
    ties = mismatches = 0
    for metric, limits in ((True, [kph(x) for x in range(20, 115, 5)]), (False, [mph(x) for x in range(15, 75, 5)])):
      for limit_ms in limits:
        for i in range(80, 1451):
          set_kph = i / 10
          if direction(text(set_kph, limit_ms, metric)) != icon_direction(set_kph, limit_ms, metric):
            figure = set_kph if metric else set_kph * KM_TO_MILE
            if abs(figure % 1.0 - 0.5) < 1e-3:
              ties += 1
            else:
              mismatches += 1
    assert mismatches == 0 and ties < 20, (mismatches, ties)

  def test_the_unset_cluster_falls_back_as_before(self):
    # vCruiseCluster 0: controlsState.deprecated.vCruise (km/h), as the arrow does
    cp = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=False)
    saved, ev.IS_MICI = ev.IS_MICI, True
    try:
      a = ev.speed_limit_pre_active_alert(cp, SimpleNamespace(vCruiseCluster=0.0), sm(kph(80), 50.0), True, 0, None)
    finally:
      ev.IS_MICI = saved
    assert a.alert_text_1 == PLUS

  def test_the_pcm_long_text_is_unchanged(self):
    t = text(50, kph(80), pcm=True)
    assert t.startswith("Speed Limit Assist: set to ") and t.endswith(" km/h to engage"), t
    assert text(50, mph(50), metric=False, pcm=True).endswith(" mph to engage")

  def test_the_alert_itself_is_unchanged(self):
    cp = SimpleNamespace(openpilotLongitudinalControl=True, pcmCruise=False)
    saved, ev.IS_MICI = ev.IS_MICI, True
    try:
      a = ev.speed_limit_pre_active_alert(cp, SimpleNamespace(vCruiseCluster=50.0), sm(kph(80)), True, 0, None)
    finally:
      ev.IS_MICI = saved
    assert (a.alert_status, a.alert_size, a.priority, a.visual_alert, a.duration) == \
      (ev.AlertStatus.normal, ev.AlertSize.small, ev.Priority.LOW, ev.VisualAlert.none, 10)
    saved, ev.IS_MICI = ev.IS_MICI, False
    try:
      a = ev.speed_limit_pre_active_alert(cp, SimpleNamespace(vCruiseCluster=50.0), sm(kph(80)), True, 0, None)
    finally:
      ev.IS_MICI = saved
    assert a.alert_size == ev.AlertSize.none, "the comma 3X draws its own sign: no text, as before"
