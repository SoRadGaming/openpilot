"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): SpeedLimitMapStrict. On untagged OSM roads sunnypilot held the previous road's map limit for the rest
of the drive and prompted with it (43 of 141 prompts over 25 drives); in tunnels GPS is lost and mapd matched the streets
overhead (26 prompts). These run the resolver and SLA together, as plannerd does, on the non-PCM path the owner's car
takes (openpilotLongitudinalControl, not pcmCruise), and check that with the param off nothing changes.
"""
import time
from types import SimpleNamespace

from openpilot.cereal import custom
from opendbc.car.car_helpers import interfaces
from opendbc.car.honda.values import CAR as HONDA
from opendbc.car.toyota.values import CAR as TOYOTA
from openpilot.common.constants import CV
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.car.cruise_ext import VCruiseHelperSP
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode, Policy
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist, \
  PRE_ACTIVE_GUARD_PERIOD, V_CRUISE_UNSET
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver, \
  MAP_HOLD_TIMEOUT, MAP_GPS_SETTLE_TIME
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.common.test import OpenpilotTestCase

EventNameSP = custom.OnroadEventSP.EventName
SpeedLimitAssistState = custom.LongitudinalPlanSP.SpeedLimit.AssistState
SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source

V_EGO = 70 * CV.KPH_TO_MS


class FakeSM:
  """The four services the resolver reads, plus sm.valid (liveMapDataSP.valid is liveLocationKalman.gpsOK)."""
  def __init__(self, map_limit_kph: float, gps_ok: bool = True, car_limit_kph: float = 0., road_name: str = ""):
    map_limit = map_limit_kph * CV.KPH_TO_MS
    self._msgs = {
      'liveMapDataSP': SimpleNamespace(speedLimit=map_limit, speedLimitValid=map_limit > 0., speedLimitAhead=0.,
                                       speedLimitAheadValid=False, speedLimitAheadDistance=0., roadName=road_name),
      'carStateSP': SimpleNamespace(speedLimit=car_limit_kph * CV.KPH_TO_MS),
    }
    # the same fresh fix under either GPS service name
    self._gps = SimpleNamespace(unixTimestampMillis=time.monotonic() * 1e3)
    self.valid = {'liveMapDataSP': gps_ok}

  def __getitem__(self, key):
    return self._msgs.get(key, self._gps)


class Drive:
  """Resolver -> SLA -> VCruiseHelperSP, one plannerd frame per step()."""
  def __init__(self, CP, CP_SP):
    self.resolver = SpeedLimitResolver()
    self.sla = SpeedLimitAssist(CP, CP_SP)
    self.v_cruise = VCruiseHelperSP(CP, CP_SP)
    self.events_sp = EventsSP()
    self.prompts = 0  # entries into preActive: the arrow prompt the driver sees
    self.alerts = 0   # speedLimitActive / speedLimitChanged: the chimes while already active

  def step(self, map_limit_kph: float, gps_ok: bool = True, long_enabled: bool = True, v_cruise_kph: float = 80.,
           car_limit_kph: float = 0., road_name: str = "") -> None:
    self.resolver.update(V_EGO, FakeSM(map_limit_kph, gps_ok, car_limit_kph, road_name))  # ty: ignore[invalid-argument-type]
    has_speed_limit = self.resolver.speed_limit_valid or self.resolver.speed_limit_last_valid

    state_prev = self.sla.state
    self.events_sp.clear()
    self.sla.update(long_enabled, False, V_EGO, 0., v_cruise_kph * CV.KPH_TO_MS, self.resolver.speed_limit,
                    self.resolver.speed_limit_final_last, has_speed_limit, self.resolver.distance, self.events_sp,
                    map_limit_frozen=self.resolver.map_limit_frozen)
    if self.sla.state == SpeedLimitAssistState.preActive and state_prev != SpeedLimitAssistState.preActive:
      assert EventNameSP.speedLimitPreActive in self.events_sp.names
      self.prompts += 1
    if any(e in self.events_sp.names for e in (EventNameSP.speedLimitActive, EventNameSP.speedLimitChanged)):
      self.alerts += 1

    # what card's VCruiseHelperSP sees of this frame
    LP_SP = custom.LongitudinalPlanSP.new_message()
    LP_SP.speedLimit.resolver.speedLimitValid = self.resolver.speed_limit_valid
    LP_SP.speedLimit.resolver.speedLimitLastValid = self.resolver.speed_limit_last_valid
    LP_SP.speedLimit.resolver.speedLimitFinalLast = float(self.resolver.speed_limit_final_last)
    LP_SP.speedLimit.assist.state = self.sla.state
    self.v_cruise.v_cruise_cluster_kph = v_cruise_kph  # ty: ignore[invalid-assignment]
    self.v_cruise.update_speed_limit_assist(True, LP_SP)
    self.v_cruise.update_speed_limit_assist_v_cruise_non_pcm()

  def run(self, seconds: float, *args, **kwargs) -> None:
    for _ in range(round(seconds / DT_MDL)):
      self.step(*args, **kwargs)


class MapStrictTestBase(OpenpilotTestCase):
  strict = True
  car_name = HONDA.HONDA_ACCORD_9G_AU

  def setup_method(self):
    self.params = Params()
    self.params.put("IsReleaseSpBranch", True, block=True)
    self.params.put("SpeedLimitMode", int(Mode.assist), block=True)
    self.params.put("SpeedLimitPolicy", int(Policy.map_data_only), block=True)
    self.params.put_bool("IsMetric", True, block=True)
    self.params.put("SpeedLimitOffsetType", 0, block=True)
    self.params.put("SpeedLimitValueOffset", 0, block=True)
    self.params.put_bool("SpeedLimitMapStrict", self.strict, block=True)

  def _drive(self, car_name=None, pcm_cruise=False) -> Drive:
    car_name = car_name or self.car_name
    CarInterface = interfaces[car_name]
    CP = CarInterface.get_non_essential_params(car_name)
    CP_SP = CarInterface.get_non_essential_params_sp(CP, car_name)
    CP.openpilotLongitudinalControl = True
    CP.pcmCruise = pcm_cruise  # the owner's Accord: openpilot longitudinal without PCM cruise
    drive = Drive(CP, CP_SP)
    assert drive.sla.pcm_op_long == pcm_cruise
    assert drive.resolver.map_strict == self.strict and drive.sla.map_strict == self.strict
    return drive

  def _engaged_until_prompt_times_out(self, drive: Drive, limit_kph: float) -> None:
    drive.run(1., limit_kph, long_enabled=False)
    drive.run(1. + PRE_ACTIVE_GUARD_PERIOD[False] + 0.5, limit_kph)
    assert drive.prompts == 1  # the legitimate one: engaged on a 60 road with the set speed at 80
    assert drive.sla.state == SpeedLimitAssistState.inactive


class TestSpeedLimitMapStrict(MapStrictTestBase):
  strict = True

  def test_junction_gap_same_limit_no_prompt(self):
    """60 -> untagged for 4 s -> 60 again is the same road limit, not a new one."""
    drive = self._drive()
    self._engaged_until_prompt_times_out(drive, 60)

    drive.run(4., 0)
    assert drive.resolver.speed_limit_last == 60 * CV.KPH_TO_MS  # held across a gap shorter than MAP_HOLD_TIMEOUT
    drive.run(3., 60)
    assert drive.prompts == 1

  def test_junction_gap_same_limit_while_active_no_chime(self):
    drive = self._drive()
    drive.run(1., 60, v_cruise_kph=60, long_enabled=False)
    drive.run(2., 60, v_cruise_kph=60)  # set speed already 60: confirmed, straight to active
    assert drive.sla.state == SpeedLimitAssistState.active
    alerts = drive.alerts

    drive.run(4., 0, v_cruise_kph=60)
    drive.run(2., 60, v_cruise_kph=60)
    assert drive.sla.state == SpeedLimitAssistState.active
    assert drive.alerts == alerts

  def test_untagged_road_drops_carried_limit(self):
    """After MAP_HOLD_TIMEOUT on an untagged road with good GPS the carried map limit goes, and the set speed stays."""
    drive = self._drive()
    drive.run(1., 60, v_cruise_kph=60, long_enabled=False)
    drive.run(2., 60, v_cruise_kph=60)
    assert drive.sla.state == SpeedLimitAssistState.active
    assert drive.sla.output_v_target == 60 * CV.KPH_TO_MS
    drive.v_cruise.v_cruise_kph = 60.

    drive.run(MAP_HOLD_TIMEOUT + 2., 0, v_cruise_kph=60)
    assert drive.resolver.speed_limit_last == 0.
    assert drive.resolver.speed_limit_final_last == 0.
    assert drive.sla.output_v_target == V_CRUISE_UNSET
    assert drive.v_cruise.v_cruise_kph == 60.
    assert drive.prompts == 0

  def test_tagged_road_after_dropped_limit_prompts_even_if_same(self):
    drive = self._drive()
    self._engaged_until_prompt_times_out(drive, 60)
    drive.run(MAP_HOLD_TIMEOUT + 2., 0)
    assert drive.resolver.speed_limit_last == 0.
    drive.run(1., 60)
    assert drive.prompts == 2

  def test_engage_on_untagged_road_with_carried_limit(self):
    """Engaging with raw 0 and a held 60 is not a prompt; the next real limit is."""
    drive = self._drive()
    drive.run(1., 60, long_enabled=False)
    drive.run(1., 0, long_enabled=False)
    drive.run(2., 0)  # engaged, past the 0.5 s guard, limit still held
    assert drive.resolver.speed_limit_last == 60 * CV.KPH_TO_MS
    assert drive.sla.state == SpeedLimitAssistState.inactive
    assert drive.prompts == 0

    drive.run(1., 50)
    assert drive.sla.state == SpeedLimitAssistState.preActive
    assert drive.prompts == 1

  def test_engage_on_untagged_road_then_same_limit_prompts(self):
    drive = self._drive()
    drive.run(1., 60, long_enabled=False)
    drive.run(1., 0, long_enabled=False)
    drive.run(2., 0)
    assert drive.prompts == 0
    drive.run(1., 60)  # first real limit since engaging: prompts even though it is the carried number
    assert drive.prompts == 1

  def test_engage_on_untagged_road_pcm_op_long_goes_pending(self):
    drive = self._drive(TOYOTA.TOYOTA_RAV4_TSS2, pcm_cruise=True)
    drive.run(1., 60, long_enabled=False)
    drive.run(1., 0, long_enabled=False)
    drive.run(2., 0)
    assert drive.sla.state == SpeedLimitAssistState.pending
    assert drive.prompts == 0
    drive.run(1., 50)
    assert drive.sla.state == SpeedLimitAssistState.preActive

  def test_pcm_adapting_leaves_when_the_limit_is_dropped(self):
    """PCM op-long: ADAPTING leaves only when the speed error closes, and with no limit v_offset is -v_ego forever."""
    drive = self._drive(TOYOTA.TOYOTA_RAV4_TSS2, pcm_cruise=True)
    drive.run(1., 50, long_enabled=False)
    set_kph = drive.sla.target_set_speed_conv  # PCM op-long confirms at the required max set speed
    drive.run(2., 50, v_cruise_kph=set_kph)
    assert drive.sla.state == SpeedLimitAssistState.adapting  # 70 km/h on a 50 road
    drive.run(MAP_HOLD_TIMEOUT + 2., 0, v_cruise_kph=set_kph)
    assert drive.resolver.speed_limit_final_last == 0.
    assert drive.sla.state == SpeedLimitAssistState.active
    assert drive.sla.output_v_target == V_CRUISE_UNSET
    assert drive.prompts == 0

  def test_tunnel_keeps_entry_limit(self):
    """GPS lost: mapd's 50 from the street overhead is ignored and the 90 we entered with is held, past the timeout."""
    drive = self._drive()
    drive.run(1., 90, v_cruise_kph=90, long_enabled=False, road_name="M4")
    drive.run(2., 90, v_cruise_kph=90, road_name="M4")
    assert drive.sla.state == SpeedLimitAssistState.active

    for _ in range(round((MAP_HOLD_TIMEOUT + 20.) / DT_MDL)):
      drive.step(50, gps_ok=False, v_cruise_kph=90, road_name="James Street")
      assert drive.resolver.speed_limit == 90 * CV.KPH_TO_MS
      assert drive.resolver.map_limit_frozen
    assert drive.resolver.source == SpeedLimitSource.map
    assert drive.sla.state == SpeedLimitAssistState.active
    assert drive.prompts == 0

    # GPS back, but mapd still reports the street it matched in the tunnel: not a new limit yet
    drive.run(5., 50, v_cruise_kph=90, road_name="James Street")
    assert drive.resolver.speed_limit == 90 * CV.KPH_TO_MS
    assert drive.prompts == 0

    drive.run(1., 50, v_cruise_kph=90, road_name="Parramatta Road")  # re-matched: now it is a real 50
    assert drive.resolver.speed_limit == 50 * CV.KPH_TO_MS
    assert not drive.resolver.map_limit_frozen
    assert drive.prompts == 1

  def _through_tunnel(self, drive: Drive, entry_kph: float, stale_kph: float, stale_road: str, stale_s: float,
                      exit_kph: float, exit_road: str, v_cruise_kph: float) -> list[int]:
    """Engaged and active at the entry limit, 30 s without GPS, stale_s of mapd's stale match with GPS back, then the
    real road. Returns the limit each prompt asked for."""
    asked: list[int] = []

    def run(seconds, *args, **kwargs):
      for _ in range(round(seconds / DT_MDL)):
        prompts = drive.prompts
        drive.step(*args, **kwargs)
        if drive.prompts != prompts:
          asked.append(round(drive.resolver.speed_limit * CV.MS_TO_KPH))

    run(1., entry_kph, v_cruise_kph=v_cruise_kph, long_enabled=False, road_name="entry")
    run(2., entry_kph, v_cruise_kph=v_cruise_kph, road_name="entry")
    run(30., stale_kph, gps_ok=False, v_cruise_kph=v_cruise_kph, road_name=stale_road)
    run(stale_s, stale_kph, v_cruise_kph=v_cruise_kph, road_name=stale_road)
    run(3., exit_kph, v_cruise_kph=v_cruise_kph, road_name=exit_road)
    return asked

  def test_tunnel_exit_route_de(self):
    """Route de, M4 East exit, 60 frozen: 4 s of 'James Street' 50 with GPS back, then 'Western Motorway' 90."""
    assert self._through_tunnel(self._drive(), 60, 50, "James Street", 4., 90, "Western Motorway", 60) == [90]

  def test_tunnel_exit_route_fc(self):
    """Route fc, 90 frozen: 8 s of an unnamed 50 with GPS back, then 'M4 East' 60."""
    assert self._through_tunnel(self._drive(), 90, 50, "", 8., 60, "M4 East", 90) == [60]

  def test_gps_back_same_road_name_settles_after_timeout(self):
    """If mapd's road name never changes, the frozen limit is released after MAP_GPS_SETTLE_TIME."""
    drive = self._drive()
    drive.run(1., 90, long_enabled=False, road_name="M4")
    drive.run(5., 50, gps_ok=False, long_enabled=False, road_name="M4")
    drive.run(MAP_GPS_SETTLE_TIME - 1., 50, long_enabled=False, road_name="M4")
    assert drive.resolver.speed_limit == 90 * CV.KPH_TO_MS
    drive.run(2., 50, long_enabled=False, road_name="M4")
    assert drive.resolver.speed_limit == 50 * CV.KPH_TO_MS

  def test_engage_in_tunnel_does_not_prompt_frozen_limit(self):
    """Route fd: engaging in the tunnel prompted the frozen portal limit. The frozen limit is not a prompt; the first
    real limit after GPS returns is, when it differs from the frozen one."""
    drive = self._drive()
    drive.run(1., 60, long_enabled=False, road_name="portal")
    drive.run(10., 80, gps_ok=False, long_enabled=False, road_name="M4 East Tunnel")
    drive.run(3., 80, gps_ok=False, v_cruise_kph=105, road_name="M4 East Tunnel")
    assert drive.resolver.speed_limit == 60 * CV.KPH_TO_MS
    assert drive.sla.state == SpeedLimitAssistState.inactive
    assert drive.prompts == 0

    # GPS back: mapd's first messages still carry its in-tunnel match, then the real road
    drive.run(1., 80, v_cruise_kph=105, road_name="M4 East Tunnel")
    assert drive.prompts == 0
    drive.run(3., 90, v_cruise_kph=105, road_name="Western Motorway")
    assert drive.prompts == 1

  def test_engage_in_tunnel_exit_same_limit_no_prompt(self):
    drive = self._drive()
    drive.run(1., 60, long_enabled=False, road_name="portal")
    drive.run(10., 80, gps_ok=False, long_enabled=False, road_name="M4 East Tunnel")
    drive.run(3., 80, gps_ok=False, v_cruise_kph=105, road_name="M4 East Tunnel")
    drive.run(1., 80, v_cruise_kph=105, road_name="M4 East Tunnel")
    drive.run(3., 60, v_cruise_kph=105, road_name="Parramatta Road")
    assert drive.resolver.speed_limit == 60 * CV.KPH_TO_MS
    assert drive.prompts == 0

  def test_tunnel_on_untagged_road_does_not_run_the_timeout(self):
    drive = self._drive()
    drive.run(1., 60, long_enabled=False)
    drive.run(1., 0, long_enabled=False)
    drive.run(MAP_HOLD_TIMEOUT + 5., 0, gps_ok=False, long_enabled=False)
    assert drive.resolver.speed_limit_last == 60 * CV.KPH_TO_MS
    # the settle window after GPS returns pauses the timer too (1 s of it already ran before GPS was lost)
    drive.run(MAP_GPS_SETTLE_TIME + MAP_HOLD_TIMEOUT - 2., 0, long_enabled=False)
    assert drive.resolver.speed_limit_last == 60 * CV.KPH_TO_MS
    drive.run(2., 0, long_enabled=False)
    assert drive.resolver.speed_limit_last == 0.

  def test_car_sourced_limit_never_times_out(self):
    self.params.put("SpeedLimitPolicy", int(Policy.car_state_only), block=True)
    drive = self._drive()
    drive.run(1., 0, car_limit_kph=60, long_enabled=False)
    drive.run(MAP_HOLD_TIMEOUT * 3, 0, car_limit_kph=0, long_enabled=False)
    assert drive.resolver.speed_limit_last == 60 * CV.KPH_TO_MS


class TestSpeedLimitMapStrictOff(MapStrictTestBase):
  """With SpeedLimitMapStrict off, the same drives behave as upstream: held limits prompt and tunnels take mapd's limit."""
  strict = False

  def test_junction_gap_same_limit_prompts(self):
    drive = self._drive()
    self._engaged_until_prompt_times_out(drive, 60)
    drive.run(4., 0)
    drive.run(3., 60)
    assert drive.prompts > 1  # upstream prompts again with the held 60 as soon as the road goes untagged

  def test_untagged_road_holds_carried_limit(self):
    drive = self._drive()
    drive.run(1., 60, long_enabled=False)
    drive.run(MAP_HOLD_TIMEOUT * 3, 0, long_enabled=False)
    assert drive.resolver.speed_limit_last == 60 * CV.KPH_TO_MS

  def test_engage_on_untagged_road_prompts_with_carried_limit(self):
    drive = self._drive()
    drive.run(1., 60, long_enabled=False)
    drive.run(1., 0, long_enabled=False)
    drive.run(2., 0)
    assert drive.sla.state == SpeedLimitAssistState.preActive
    assert drive.prompts == 1

  def test_tunnel_takes_map_limit(self):
    drive = self._drive()
    drive.run(1., 90, long_enabled=False)
    drive.run(1., 50, gps_ok=False, long_enabled=False)
    assert drive.resolver.speed_limit == 50 * CV.KPH_TO_MS
    assert not drive.resolver.map_limit_frozen

  def test_tunnel_exit_takes_the_stale_match(self):
    drive = self._drive()
    drive.run(1., 90, long_enabled=False, road_name="M4")
    drive.run(5., 50, gps_ok=False, long_enabled=False, road_name="James Street")
    drive.run(1., 50, long_enabled=False, road_name="James Street")
    assert drive.resolver.speed_limit == 50 * CV.KPH_TO_MS

  def test_never_reads_sm_valid(self):
    """Upstream's plant harness (longitudinal_maneuvers/plant.py) hands plannerd a plain dict, which has no .valid."""
    drive = self._drive()
    sm = FakeSM(60)
    del sm.valid
    drive.resolver.update(V_EGO, sm)  # ty: ignore[invalid-argument-type]
    assert drive.resolver.speed_limit == 60 * CV.KPH_TO_MS
