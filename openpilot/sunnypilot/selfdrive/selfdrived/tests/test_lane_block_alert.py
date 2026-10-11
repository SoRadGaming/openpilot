"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HUD): selfdrived's side of the comma 4 lane-block tile (lane_block_alert.py). With HudLaneBlock on, on a comma 4,
a refused lane change draws no alert and is silent when it appears; a nudge the block refused chimes once. Off, or on
any other device: upstream's alerts, sounds and events.
"""
import re
from pathlib import Path
from types import SimpleNamespace

from openpilot.cereal import log, custom
from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP, EVENTS_SP, ET, Priority, AlertSize, AudibleAlert
from openpilot.sunnypilot.selfdrive.selfdrived.lane_block_alert import LaneBlockAlert, PARAM

EN = log.OnroadEvent.EventName
EN_SP = custom.OnroadEventSP.EventName
LCS, LCD = log.LaneChangeState, log.LaneChangeDirection
SELFDRIVED = Path(__file__).resolve().parents[4] / "selfdrive/selfdrived/selfdrived.py"
FPS = 100   # selfdrived


class FakeParams:
  def __init__(self, value):
    self.value = value

  def get_bool(self, key):
    assert key == PARAM
    if isinstance(self.value, Exception):
      raise self.value
    return self.value


def helper(on=True, is_mici=True) -> LaneBlockAlert:
  a = LaneBlockAlert(is_mici=is_mici)
  a.read_params(FakeParams(on))
  return a


def frame_state(edge=False, blindspot=False, refused=False, state=LCS.preLaneChange):
  return SimpleNamespace(state=state, edge=edge, blindspot=blindspot,
                         mdv2sp=SimpleNamespace(laneChangeNudgeRefused=refused, leftLaneChangeEdgeBlock=edge,
                                                rightLaneChangeEdgeBlock=False))


def drive(lba: LaneBlockAlert, frames, other=None):
  """selfdrived's per-frame path for a left lane change: 'Handle lane change' (blind spot, then road edge, else
  preLaneChange), the helper's events, create_alerts, the helper's filter, the AlertManager. other: an extra event raised
  every frame. Returns [(alert_type, audible_alert)] per frame."""
  am, ev, ev_sp = AlertManager(), Events(), EventsSP()
  out = []
  for frame, f in enumerate(frames):
    ev.clear()
    ev_sp.clear()
    if other is not None:
      ev.add(other)
    if f.state == LCS.preLaneChange:
      if f.blindspot:
        ev.add(EN.laneChangeBlocked)
      elif f.edge:
        ev_sp.add(EN_SP.laneChangeRoadEdge)
      else:
        ev.add(EN.preLaneChangeLeft)
    for e in lba.update(f.state, f.mdv2sp):
      ev_sp.add(e)
    types = [ET.WARNING]
    alerts, alerts_sp = ev.create_alerts(types), ev_sp.create_alerts(types)
    alerts, alerts_sp = lba.drop_block_alerts(alerts, alerts_sp)
    am.add_many(frame, alerts + alerts_sp)
    am.process_alerts(frame, set())
    out.append((am.current_alert.alert_type, am.current_alert.audible_alert, am.current_alert.alert_size))
  return out


def chimes(out) -> int:
  """How often soundd starts a prompt: alertSound turning to prompt from anything else."""
  sounds = [s for _, s, _ in out]
  return sum(1 for a, b in zip([AudibleAlert.none] + sounds, sounds, strict=False)
             if b == AudibleAlert.prompt and a != AudibleAlert.prompt)


def seconds(s: float, **kw):
  return [frame_state(**kw)] * int(s * FPS)


class TestTileOn(OpenpilotTestCase):
  def test_a_road_edge_block_draws_no_alert_and_is_silent(self):
    out = drive(helper(), seconds(0.5) + seconds(3.0, edge=True))
    assert not any(t.startswith("laneChangeRoadEdge") for t, _, _ in out), "no orange road-edge alert"
    assert chimes(out) == 0, "no chime when the block appears"
    assert out[-1][0] == "", "nothing on screen: the tile shows the block"

  def test_a_car_in_the_blind_spot_too(self):
    out = drive(helper(), seconds(3.0, blindspot=True))
    assert not any(t.startswith("laneChangeBlocked") for t, _, _ in out) and chimes(out) == 0

  def test_a_refused_nudge_chimes_once_with_nothing_drawn(self):
    out = drive(helper(), seconds(1.0, edge=True) + seconds(0.7, edge=True, refused=True) + seconds(1.0, edge=True))
    assert chimes(out) == 1
    sounding = [(t, size) for t, s, size in out if s == AudibleAlert.prompt]
    assert {t for t, _ in sounding} == {"laneChangeNudgeRefused/warning"}
    assert {size for _, size in sounding} == {AlertSize.none}, "a sound, no alert drawn"

  def test_each_refused_nudge_chimes_once(self):
    nudge = seconds(0.4, edge=True, refused=True)
    gap = seconds(0.6, edge=True)
    out = drive(helper(), gap + nudge + gap + nudge + gap + nudge + gap)
    assert chimes(out) == 3

  def test_only_while_the_change_waits(self):
    out = drive(helper(), seconds(1.0, refused=True, state=LCS.laneChangeStarting) + seconds(1.0, refused=True,
                                                                                             state=LCS.off))
    assert chimes(out) == 0

  def test_it_never_takes_the_place_of_an_alert_on_screen(self):
    """Priority.LOWEST: with another alert up, the tile's flash answers the nudge on its own."""
    frames = seconds(0.5, edge=True) + seconds(0.5, edge=True, refused=True)
    out = drive(helper(), frames, other=EN.steerSaturated)
    assert all(t == "steerSaturated/warning" for t, _, _ in out)

  def test_the_preLaneChange_alert_is_untouched(self):
    out = drive(helper(), seconds(1.0))
    assert out[-1][0] == "preLaneChangeLeft/warning"


class TestTileOffOrNotAComma4(OpenpilotTestCase):
  def test_off_is_upstream(self):
    out = drive(helper(on=False), seconds(1.0, edge=True) + seconds(0.5, edge=True, refused=True))
    road_edge = [(t, s) for t, s, _ in out if t.startswith("laneChangeRoadEdge")]
    assert road_edge and road_edge[0][1] == AudibleAlert.prompt, "today's alert, with its chime"
    assert not any(t.startswith("laneChangeNudgeRefused") for t, _, _ in out), "and no nudge chime"

  def test_a_comma_3x_keeps_its_alerts(self):
    a = helper(on=True, is_mici=False)
    assert not a.on
    out = drive(a, seconds(1.0, blindspot=True))
    assert any(t.startswith("laneChangeBlocked") for t, _, _ in out)

  def test_an_unreadable_param_is_off(self):
    a = LaneBlockAlert(is_mici=True)
    a.read_params(FakeParams(KeyError(PARAM)))
    assert not a.on


class TestTheEvent(OpenpilotTestCase):
  def test_a_sound_and_nothing_else(self):
    entry = EVENTS_SP[EN_SP.laneChangeNudgeRefused]
    assert set(entry) == {ET.WARNING}
    a = entry[ET.WARNING]
    assert a.alert_size == AlertSize.none and a.alert_text_1 == "" and a.alert_text_2 == ""
    assert a.audible_alert == AudibleAlert.prompt and a.priority == Priority.LOWEST

  def test_in_the_schema(self):
    names = custom.OnroadEventSP.EventName.schema.enumerants
    assert names["laneChangeNudgeRefused"] == EN_SP.laneChangeNudgeRefused
    assert "laneChangeNudgeRefused" in custom.ModelDataV2SP.schema.fields


class TestSelfdrivedWiring(OpenpilotTestCase):
  def test_selfdrived_uses_it(self):
    src = SELFDRIVED.read_text(encoding="utf-8")
    assert "self.lane_block_alert = LaneBlockAlert()  # FORK(HUD)" in src
    call = re.search(r"for e in self\.lane_block_alert\.update\(self\.sm\['modelV2'\]\.meta\.laneChangeState, " +
                     r"self\.sm\['modelDataV2SP'\]\):  # FORK\(HUD\)\s*\n\s*self\.events_sp\.add\(e\)", src)
    assert call, "the helper is not fed the lane-change state, or its events do not reach events_sp"
    assert src.index("# Handle lane change") < call.start() < src.index("# Handle lane turn")
    alerts = src[src.index("  def update_alerts(self"):src.index("  def publish_selfdriveState")]
    f = alerts.index("self.lane_block_alert.drop_block_alerts(alerts, alerts_sp)")
    assert alerts.index("self.events_sp.create_alerts(") < f < alerts.index("self.AM.add_many("), \
      "filtered after both lists are made, before the AlertManager"
    thread = src[src.index("  def params_thread(self"):src.index("  def run(self")]
    assert "self.lane_block_alert.read_params(self.params)  # FORK(HUD)" in thread

  def test_modeld_publishes_the_flag(self):
    root = SELFDRIVED.parents[2]
    for path in (root / "selfdrive/modeld/modeld.py", root / "sunnypilot/modeld_v2/modeld.py"):
      src = path.read_text(encoding="utf-8")
      assert "mdv2sp_send.modelDataV2SP.laneChangeNudgeRefused = DH.nudge_refused  # FORK(HUD)" in src, path
      assert src.index("DH.update(") < src.index("laneChangeNudgeRefused = DH.nudge_refused") < \
        src.index("pm.send('modelDataV2SP'"), path


if __name__ == "__main__":
  import unittest
  unittest.main()
