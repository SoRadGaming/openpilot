"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): KTuner fix 1 (2026-10-19). While another diagnostic tool talks on the car bus and the car is
parked, the car stays stock: the comma transmits nothing and the radar stays on the car's own wire.

THE INCIDENT. Routes 144, 145 and 147 (and db/dc on 2026-09-16): parked, ignition on, KTuner datalogging the engine ECU
over the OBD port. The moment the panda left its start-up ELM327 for hondaNidec and openpilot started transmitting,
the panda's car-bus transmissions failed (bit errors), it went bus-off within 0.1-3.6 s and its car-bus core stayed
dead for the rest of the route. With the harness relay open the radar was cut off from the car: ACC and CMBS lamps,
"CAN Bus Disconnected", a flicker of the SRS lamp. In the ELM327 phase before that, with the relay closed and the comma
sending nothing, KTuner and the car coexisted for 8.7-9.3 s on every route with zero bus errors. That start-up state is
what this keeps.

WHAT COUNTS. A tester REQUEST received from another node on the car bus: card's can_list frame with src == 0 exactly,
whose address is a 29-bit 0x18DA/0x18DB physical/functional request from an off-board tester (source address 0xF0-0xFD,
target outside it: KTuner's 0x18DA0EF1, a scan tool's 0x18DBEFF1), or an 11-bit OBD request 0x7DF/0x7E0-0x7E7. Never:
  * responses (0x18DAF1xx, 0x7E8-0x7EF) - openpilot's own FW query is answered on src 0;
  * src 2 - openpilot's own requests seen through the closed relay at start-up;
  * src >= 128 - our echoes, forwards and rejects;
  * the EPS board's 0x700-0x71F and the BLR board's 0x620-0x62F, which are outside these patterns by construction.
Across 189 normal routes (51.5 h, 176 start-ups) no frame ever qualified.

ENTER: at least REQ_MIN qualifying frames within REQ_WINDOW_NS, the car parked (CAN valid, gear P, standstill) and
nothing engaged (carControl enabled/latActive/longActive all false, or carControl not alive yet).
LEAVE: QUIET_NS without a qualifying frame AND parked. The quiet time only grows across `can` messages that carry at
least one src-0 frame, each gap counted at most QUIET_GAP_CAP_NS, so a dead car-bus receiver (a bus-off) never reads as
quiet. Never while moving: resuming is entering the car mode, which stands down a stock ACC the driver may have engaged.

HOW card USES IT (card.py, FORK lines):
  * before controls first start, `stock` holds card's first controls_update - no CI.init, no ControlsReady, no
    sendcan, so pandad keeps the start-up ELM327 (relay closed). The release is the normal start, later;
  * after controls have started, card writes DiagToolStock from params_thread and pandad leaves the car mode for
    NO_OUTPUT (pandad.cc). card keeps sending; the panda drops it all;
  * each transition is returned as one record for cloudlog (errorLogMessage, in the qlog).
Pure: no I/O, no clock, the logMonoTime of each can message is the time base, so a replay is deterministic.
"""
from collections import Counter, deque

from opendbc.car import structs
from opendbc.car.honda.values import HONDA_ELESYS

GearShifter = structs.CarState.GearShifter

REQ_WINDOW_NS = 1_000_000_000         # the requests that count must lie within this
REQ_MIN = 2                           # dc's scan tool: 2 in 1 s is 0.22 s after its first request, 0.47 s before the switch
QUIET_NS = 10_000_000_000             # no request for this long (and parked) ends it
QUIET_GAP_CAP_NS = 100_000_000        # one gap in car-bus RX counts at most this much toward the quiet time
CAR_BUS_SRC = 0                       # a frame received from another node on the car bus
TESTER_SA = range(0xF0, 0xFE)         # ISO 15765-2 / J1939 off-board tester source addresses
MAX_IDS = 16                          # distinct request addresses kept for the log record


def is_tester_request(addr: int) -> bool:
  """A diagnostic request from an off-board tester: 29-bit 0x18DA<TA><SA> / 0x18DB<TA><SA> with SA 0xF0-0xFD and TA not,
  or 11-bit 0x7DF / 0x7E0-0x7E7. Responses (SA is the ECU, TA the tester; 0x7E8-0x7EF) are not."""
  if addr > 0x7FF:
    if (addr >> 16) not in (0x18DA, 0x18DB):
      return False
    sa, ta = addr & 0xFF, (addr >> 8) & 0xFF
    return sa in TESTER_SA and ta not in TESTER_SA
  return addr == 0x7DF or 0x7E0 <= addr <= 0x7E7


def armed_for(CP) -> bool:
  """This car only (the fork's per-car gating, elesys_radar_guard.enabled()), and never in dashcam mode."""
  return CP.brand == 'honda' and CP.carFingerprint in HONDA_ELESYS and not CP.passive


def parked(CS) -> bool:
  return bool(CS.canValid) and CS.gearShifter == GearShifter.park and bool(CS.standstill)


class DiagToolGuard:
  def __init__(self, CP, inherited_stock: bool = False):
    self.armed = armed_for(CP)
    # card restarted inside a drive while it held: keep holding until the leave rule says otherwise
    self.stock = self.armed and bool(inherited_stock)
    self._inherited = self.stock
    self._req: deque[int] = deque(maxlen=1024)   # logMonoTime of each qualifying frame in the window
    self._quiet_ns = 0
    self._last_rx_ns: int | None = None          # the last can message with a src-0 frame
    self._ids: Counter[int] = Counter()           # request addresses this episode, for the log
    self._other_ids = 0
    self._since_ns: int | None = None             # entered stock at (None: inherited, time unknown)
    self._seen_logged = False                     # "seen while not parked/idle" logged for this episode
    self._now_ns = 0

  def update(self, can_list, CS, CC, cc_alive: bool, controls_started: bool = False) -> tuple[str, dict] | None:
    """One card step. can_list: card's [(logMonoTime, [(addr, dat, src), ...]), ...]. Returns (event, fields) for
    cloudlog.event on a transition ("diag_tool_stock"), and once per episode for a tool heard while the car is known to
    be moving or engaged, so it cannot act ("diag_tool_seen"); else None."""
    if not self.armed:
      return None

    for nanos, frames in can_list:
      self._now_ns = nanos
      rx = req = False
      for addr, _dat, src in frames:
        if src != CAR_BUS_SRC:
          continue
        rx = True
        if addr >= 0x7DF and is_tester_request(addr):   # 29-bit ids are all above 0x7FF
          req = True
          self._req.append(nanos)
          if addr in self._ids or len(self._ids) < MAX_IDS:
            self._ids[addr] += 1
          else:
            self._other_ids += 1
      if req:
        self._quiet_ns = 0
      elif rx and self._last_rx_ns is not None and nanos > self._last_rx_ns:
        self._quiet_ns += min(nanos - self._last_rx_ns, QUIET_GAP_CAP_NS)
      if rx:
        self._last_rx_ns = nanos

    while self._req and self._now_ns - self._req[0] > REQ_WINDOW_NS:
      self._req.popleft()

    is_parked = parked(CS)
    active = bool(cc_alive and (CC.enabled or CC.latActive or CC.longActive))

    if not self.stock:
      if len(self._req) >= REQ_MIN:
        if is_parked and not active:
          self.stock = True
          self._since_ns = self._now_ns
          self._seen_logged = False
          return "diag_tool_stock", self._record(CS, controls_started, on=True)
        # "seen, but it cannot act" is for a car that is known to be moving or engaged. Before the car's CAN is valid
        # (the first ~2.5-3.5 s of every start, KTuner already talking) the gear is not known yet: wait, say nothing.
        if bool(CS.canValid) and not self._seen_logged:
          self._seen_logged = True
          return "diag_tool_seen", self._record(CS, controls_started, stock=False, parked=is_parked, active=active)
      elif self._quiet_ns >= QUIET_NS:
        self._seen_logged = False   # the tool went away; a new one is a new episode
        self._forget_ids()
      return None

    if self._quiet_ns >= QUIET_NS and is_parked:
      rec = self._record(CS, controls_started, on=False, quiet_s=round(self._quiet_ns / 1e9, 2),
                         duration_s=None if self._since_ns is None else round((self._now_ns - self._since_ns) / 1e9, 2),
                         inherited=self._inherited)
      self.stock = False
      self._inherited = False
      self._since_ns = None
      self._forget_ids()
      return "diag_tool_stock", rec
    return None

  @property
  def quiet_s(self) -> float:
    return self._quiet_ns / 1e9

  def _forget_ids(self) -> None:
    self._ids.clear()
    self._other_ids = 0

  def _record(self, CS, controls_started: bool, **extra) -> dict:
    ids = {f"0x{a:X}": n for a, n in self._ids.most_common()}
    if self._other_ids:
      ids["other"] = self._other_ids
    rec = {"ids": ids, "requests_1s": len(self._req), "gear": str(CS.gearShifter), "v_ego": round(float(CS.vEgo), 2),
           "standstill": bool(CS.standstill), "can_valid": bool(CS.canValid), "controls_started": bool(controls_started)}
    rec.update(extra)
    return rec
