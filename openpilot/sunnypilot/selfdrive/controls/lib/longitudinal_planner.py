"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import time

from openpilot.cereal import messaging, custom
from opendbc.car import structs
from openpilot.common.constants import CV
from openpilot.selfdrive.car.cruise import V_CRUISE_MAX
from openpilot.sunnypilot.selfdrive.controls.lib.dec.dec import DynamicExperimentalController
from openpilot.sunnypilot.selfdrive.controls.lib.e2e_alerts_helper import E2EAlertsHelper
from openpilot.sunnypilot.selfdrive.controls.lib.smart_cruise_control.smart_cruise_control import SmartCruiseControl
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_assist import SpeedLimitAssist
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.change_hold import SpeedLimitChangeHold  # FORK(SPEED-LIMIT)
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.speed_limit_resolver import SpeedLimitResolver
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP
from openpilot.sunnypilot.models.helpers import get_active_bundle

DecState = custom.LongitudinalPlanSP.DynamicExperimentalControl.DynamicExperimentalControlState
LongitudinalPlanSource = custom.LongitudinalPlanSP.LongitudinalPlanSource
SpeedLimitSource = custom.LongitudinalPlanSP.SpeedLimit.Source


class LongitudinalPlannerSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP, mpc):
    self.events_sp = EventsSP()
    self.resolver = SpeedLimitResolver()
    self.dec = DynamicExperimentalController(CP, mpc)
    self.scc = SmartCruiseControl()
    self.resolver = SpeedLimitResolver()
    self.sla = SpeedLimitAssist(CP, CP_SP)
    self.limit_hold = SpeedLimitChangeHold()  # FORK(SPEED-LIMIT): what SLA, card and the UI take as the limit
    self.generation = int(model_bundle.generation) if (model_bundle := get_active_bundle()) else None
    self.source = LongitudinalPlanSource.cruise
    self.e2e_alerts_helper = E2EAlertsHelper()

    self.output_v_target = 0.
    self.output_a_target = 0.

  def is_e2e(self, sm: messaging.SubMaster) -> bool:
    experimental_mode = sm['selfdriveState'].experimentalMode
    if not self.dec.active():
      return experimental_mode

    return experimental_mode and self.dec.mode() == "blended"

  def update_targets(self, sm: messaging.SubMaster, v_ego: float, a_ego: float, v_cruise: float) -> tuple[float, float]:
    CS = sm['carState']
    v_cruise_cluster_kph = min(CS.vCruiseCluster, V_CRUISE_MAX)
    v_cruise_cluster = v_cruise_cluster_kph * CV.KPH_TO_MS

    long_enabled = sm['carControl'].enabled
    long_override = sm['carControl'].cruiseControl.override

    # Smart Cruise Control
    self.scc.update(sm, long_enabled, long_override, v_ego, a_ego, v_cruise)

    # Speed Limit Resolver
    self.resolver.update(v_ego, sm)
    # FORK(SPEED-LIMIT): a new limit counts once it has held for 1 s (speed_limit/change_hold.py)
    self.limit_hold.update(self.resolver)
    limit = self.limit_hold

    # FORK(SPEED-LIMIT): push9 - the 'now' window the comma 4's card shows, so a press in it can be the confirm
    self.update_now_window(sm)

    # Speed Limit Assist
    has_speed_limit = limit.speed_limit_valid or limit.speed_limit_last_valid
    self.sla.update(long_enabled, long_override, v_ego, a_ego, v_cruise_cluster, limit.speed_limit,
                    limit.speed_limit_final_last, has_speed_limit, self.resolver.distance, self.events_sp,
                    map_limit_frozen=self.resolver.map_limit_frozen)  # FORK(SPEED-LIMIT)

    targets = {
      LongitudinalPlanSource.cruise: (v_cruise, a_ego),
      LongitudinalPlanSource.sccVision: (self.scc.vision.output_v_target, self.scc.vision.output_a_target),
      LongitudinalPlanSource.sccMap: (self.scc.map.output_v_target, self.scc.map.output_a_target),
      LongitudinalPlanSource.speedLimitAssist: (self.sla.output_v_target, self.sla.output_a_target),
    }

    self.source = min(targets, key=lambda k: targets[k][0])
    self.output_v_target, self.output_a_target = targets[self.source]
    return self.output_v_target, self.output_a_target

  def update_now_window(self, sm: messaging.SubMaster) -> None:
    """FORK(SPEED-LIMIT): push9 (speed_limit/now_confirm.py). The same inputs the HUD's ApproachState reads: mapd's
    look-ahead (liveMapDataSP, run down by vEgo between its 1 Hz messages), the limit in force when it is a map limit
    (the held limit this planner publishes), carState. Missing or stale: no window. The card switched off
    (HudApproachingLimit, SpeedLimitAssist.now_card): no window either. The resolver's offset and the car's
    pcmCruiseSpeed give the set speed a press would confirm, the number - and the key - the card shows."""
    if not all(hasattr(sm, a) for a in ('seen', 'alive', 'recv_frame')):
      # the longitudinal-maneuver plant and the DEC gate tests pass a plain mapping: no map look-ahead, so no window
      self.sla.now_confirm.reset()
      return
    lmd, cs = sm['liveMapDataSP'], sm['carState']
    limit = self.limit_hold
    conv = CV.MS_TO_KPH if self.sla.is_metric else CV.MS_TO_MPH
    in_force = int(round(limit.speed_limit_last * conv)) \
      if (limit.speed_limit_valid or limit.speed_limit_last_valid) and self.resolver.source == SpeedLimitSource.map else 0
    self.sla.now_confirm.update(
      time.monotonic(), map_ok=bool(sm.seen['liveMapDataSP'] and sm.alive['liveMapDataSP']),
      map_frame=sm.recv_frame['liveMapDataSP'], ahead_valid=bool(lmd.speedLimitAheadValid),
      ahead_ms=float(lmd.speedLimitAhead), ahead_dist=float(lmd.speedLimitAheadDistance),
      nsw_state=int(lmd.nswZone.state), car_ok=bool(sm.seen['carState'] and sm.alive['carState']),
      v_ego=float(cs.vEgo), standstill=bool(cs.standstill), limit=max(0, in_force), is_metric=self.sla.is_metric,
      holding=limit.holding, enabled=self.sla.now_card, offset_type=int(self.resolver.offset_type or 0),
      offset_value=float(self.resolver.offset_value or 0.0), pcm_cruise_speed=bool(getattr(self.sla.CP_SP, 'pcmCruiseSpeed', True)))

  def update(self, sm: messaging.SubMaster) -> None:
    self.events_sp.clear()
    self.dec.update(sm)
    self.e2e_alerts_helper.update(sm, self.events_sp)

  def publish_longitudinal_plan_sp(self, sm: messaging.SubMaster, pm: messaging.PubMaster) -> None:
    plan_sp_send = messaging.new_message('longitudinalPlanSP')

    plan_sp_send.valid = sm.all_checks(service_list=['carState', 'controlsState'])

    longitudinalPlanSP = plan_sp_send.longitudinalPlanSP
    longitudinalPlanSP.longitudinalPlanSource = self.source
    longitudinalPlanSP.vTarget = float(self.output_v_target)
    longitudinalPlanSP.aTarget = float(self.output_a_target)
    longitudinalPlanSP.events = self.events_sp.to_msg()

    # Dynamic Experimental Control
    dec = longitudinalPlanSP.dec
    dec.state = DecState.blended if self.dec.mode() == 'blended' else DecState.acc
    dec.enabled = self.dec.enabled()
    dec.active = self.dec.active()

    # Smart Cruise Control
    smartCruiseControl = longitudinalPlanSP.smartCruiseControl
    # Vision Control
    sccVision = smartCruiseControl.vision
    sccVision.state = self.scc.vision.state
    sccVision.vTarget = float(self.scc.vision.output_v_target)
    sccVision.aTarget = float(self.scc.vision.output_a_target)
    sccVision.currentLateralAccel = float(self.scc.vision.current_lat_acc)
    sccVision.maxPredictedLateralAccel = float(self.scc.vision.max_pred_lat_acc)
    sccVision.enabled = self.scc.vision.is_enabled
    sccVision.active = self.scc.vision.is_active
    # Map Control
    sccMap = smartCruiseControl.map
    sccMap.state = self.scc.map.state
    sccMap.vTarget = float(self.scc.map.output_v_target)
    sccMap.aTarget = float(self.scc.map.output_a_target)
    sccMap.enabled = self.scc.map.is_enabled
    sccMap.active = self.scc.map.is_active

    # Speed Limit
    speedLimit = longitudinalPlanSP.speedLimit
    resolver = speedLimit.resolver
    # FORK(SPEED-LIMIT): the held limit (SpeedLimitChangeHold), so card, selfdrived and the UI agree with SLA.
    # Distance and source stay live. The raw limit is liveMapDataSP's / carStateSP's.
    limit = self.limit_hold
    resolver.speedLimit = float(limit.speed_limit)
    resolver.speedLimitLast = float(limit.speed_limit_last)
    resolver.speedLimitFinal = float(limit.speed_limit_final)
    resolver.speedLimitFinalLast = float(limit.speed_limit_final_last)
    resolver.speedLimitValid = limit.speed_limit_valid
    resolver.speedLimitLastValid = limit.speed_limit_last_valid
    resolver.speedLimitOffset = float(limit.speed_limit_offset)
    resolver.distToSpeedLimit = float(self.resolver.distance)
    resolver.source = self.resolver.source
    assist = speedLimit.assist
    assist.state = self.sla.state
    assist.enabled = self.sla.is_enabled
    assist.active = self.sla.is_active
    assist.vTarget = float(self.sla.output_v_target)
    assist.aTarget = float(self.sla.output_a_target)
    # FORK(SPEED-LIMIT): sla-flash - the key card may take as a 'now' arm, and the arm held (speed_limit/now_confirm.py)
    assist.nowKey, assist.nowLimit, assist.nowTarget = self.sla.now_offer
    nc = self.sla.now_confirm
    unit = CV.KPH_TO_MS if self.sla.is_metric else CV.MPH_TO_MS
    assist.armedLimit, assist.armedTarget = nc.armed, nc.armed_target
    assist.armedSpeed = float(nc.armed_target * unit) if nc.armed else 0.
    assist.armSeq, assist.armEnd = nc.arm_seq & 0xFFFF, nc.last_end

    # E2E Alerts
    e2eAlerts = longitudinalPlanSP.e2eAlerts
    e2eAlerts.greenLightAlert = self.e2e_alerts_helper.green_light_alert
    e2eAlerts.leadDepartAlert = self.e2e_alerts_helper.lead_depart_alert

    pm.send('longitudinalPlanSP', plan_sp_send)
