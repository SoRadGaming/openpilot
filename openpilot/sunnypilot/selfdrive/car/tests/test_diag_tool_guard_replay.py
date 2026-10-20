"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): KTuner fix 1's detector replayed over the owner's real routes, one card step per logged
`can` message, with the logged carState and carControl. Fixtures: fixtures/ktuner/*.npz, built from the parquet
exports of the full-rate rlogs by openpilot/sunnypilot/tools/ktuner_fixtures.py.

  r143   a normal start and the whole 15-minute drive: never stock, so controls start on the logged frame
  rb5    openpilot's own FW query (requests on src 2, echoes on 128/129, answers on src 0): never stock
  r144   KTuner datalogging from key-on, the whole 5-minute route: stock from the first parked carState, long before
         the logged first controls_update and switch, and held to the end (after the logged switch the car bus is dead -
         the bus-off this prevents - and a dead bus is never "quiet")
  r145   the same, 23 s; the route ends in noOutput with the car bus heard again, KTuner still talking: still held
  r147   the same, 145 s
  rdc    a scan tool coming up 0.69 s before the switch: stock before the logged first controls_update
  rdb    the bus-off with no tool traffic at all: never stock (that one is fix 2's)
"""
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from opendbc.car import structs
from opendbc.car.honda.values import CAR
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.car.diag_tool_guard import DiagToolGuard

FIX = Path(__file__).parent / "fixtures/ktuner"
TQ_NS = 100_000          # the fixtures' time quantum, 0.1 ms
CS_LAG_NS = 5_000_000    # card publishes carState from a can message a few ms after its logMonoTime
OTHER = 0x1A6            # stands for "this message held at least one src-0 frame"
GEAR = structs.CarState.GearShifter.schema.enumerants   # name -> the capnp ordinal card's CarState compares equal to


def elesys_cp():
  cp = structs.CarParams()
  cp.brand = "honda"
  cp.carFingerprint = CAR.HONDA_ACCORD_9G_AU
  return cp


@cache
def replay(name: str) -> dict:
  z = np.load(FIX / f"{name}.npz")
  dt = z["dt_q"].astype(np.int64)
  dt[z["big_i"].astype(np.int64)] = z["big_v"].astype(np.int64)
  t = np.cumsum(dt) * TQ_NS
  n = len(t)
  bounds = np.searchsorted(z["fr_msg"], np.arange(n + 1))
  gears = [str(g) for g in z["gears"]]
  cs_t = z["cs_t_us"] * 1000
  cc_t = z["cc_t_us"] * 1000
  # carState / carControl in force at each can message: the change points, looked up all at once
  ks = np.searchsorted(cs_t, t + CS_LAG_NS, side="right") - 1
  js = np.searchsorted(cc_t, t, side="right") - 1
  start_cs = SimpleNamespace(gearShifter=GEAR["unknown"], standstill=True, canValid=False, vEgo=0.0)
  css = [SimpleNamespace(gearShifter=GEAR[gears[g]], standstill=bool(s), canValid=bool(v), vEgo=float(ve))
         for g, s, v, ve in zip(z["cs_gear"], z["cs_standstill"], z["cs_can_valid"], z["cs_v"], strict=True)]
  ccs = [SimpleNamespace(enabled=bool(e), latActive=bool(la), longActive=bool(lo))
         for e, la, lo in zip(z["cc_enabled"], z["cc_lat"], z["cc_long"], strict=True)]
  fr_addr, fr_src, has0 = z["fr_addr"].tolist(), z["fr_src"].tolist(), z["has0"].tolist()
  bounds_l, ks_l, js_l, t_l = bounds.tolist(), ks.tolist(), js.tolist(), t.tolist()

  g = DiagToolGuard(elesys_cp())
  stock = np.zeros(n, bool)
  records = []
  for i in range(n):
    a, b = bounds_l[i], bounds_l[i + 1]
    frames = [(fr_addr[x], b"", fr_src[x]) for x in range(a, b)]
    if has0[i]:
      frames.append((OTHER, b"", 0))
    k, j = ks_l[i], js_l[i]
    r = g.update([(t_l[i], frames)], css[k] if k >= 0 else start_cs, ccs[max(j, 0)], j >= 0)
    if r is not None:
      records.append((t_l[i] / 1e9, r))
    stock[i] = g.stock
  first = int(np.argmax(stock)) if stock.any() else -1
  first_parked = next((tu / 1e6 for tu, gi, st, cv in zip(z["cs_t_us"], z["cs_gear"], z["cs_standstill"], z["cs_can_valid"],
                                                          strict=True) if gears[gi] == "park" and st and cv), None)
  return {"t": t / 1e9, "stock": stock, "records": records, "first_stock_s": t[first] / 1e9 if first >= 0 else None,
          "switch_s": float(z["switch_ns"]) / 1e9, "first_sendcan_s": float(z["first_sendcan_ns"]) / 1e9,
          "first_parked_s": first_parked, "has0": z["has0"]}


class TestReplay(OpenpilotTestCase):
  def test_a_normal_drive_is_never_stock(self):
    r = replay("r143")
    assert r["t"][-1] > 900, "the whole route"
    assert not r["stock"].any() and r["records"] == []

  def test_our_own_fw_query_is_never_a_tool(self):
    r = replay("rb5")
    assert not r["stock"].any() and r["records"] == []

  def held_from_key_on(self, name: str, min_lead_s: float):
    r = replay(name)
    assert r["first_stock_s"] is not None, name
    assert abs(r["first_stock_s"] - r["first_parked_s"]) < 0.05, (name, r["first_stock_s"], r["first_parked_s"])
    assert r["first_stock_s"] < r["first_sendcan_s"] - min_lead_s, (name, r["first_stock_s"], r["first_sendcan_s"])
    assert r["first_sendcan_s"] < r["switch_s"], name
    assert r["stock"][np.argmax(r["stock"]):].all(), f"{name}: held to the end of the route"
    assert [rec[0] for _, rec in r["records"]] == ["diag_tool_stock"] and r["records"][0][1][1]["on"], name
    on = r["records"][0][1][1]
    assert on["controls_started"] is False and on["requests_1s"] >= 2, (name, on)
    assert any(k.startswith("0x18DA") and k.endswith("F1") for k in on["ids"]), (name, on["ids"])
    return r

  def test_ktuner_at_key_on_is_held_from_the_first_parked_frame(self):
    r = self.held_from_key_on("r144", min_lead_s=5.0)
    assert r["t"][-1] > 290, "the whole route, all five segments"

  def test_ktuner_routes_145_and_147(self):
    for name in ("r145", "r147"):
      self.held_from_key_on(name, min_lead_s=5.0)

  def test_145_ends_with_the_car_bus_heard_again_and_still_held(self):
    """145's last second: the panda went to noOutput (relay closed, can_init_all) and the car bus came back with
    KTuner still talking - the state this fix keeps from key-on. Still held, never released into it."""
    r = replay("r145")
    alive = np.flatnonzero(r["has0"])
    gaps = np.diff(r["t"][alive])
    assert gaps.max() > 5.0, "the dead stretch after the logged switch"
    assert r["t"][alive[-1]] > r["t"][alive[np.argmax(gaps)]] + 5.0, "heard again at the end"
    assert r["stock"][-1]

  def test_a_scan_tool_before_the_switch_is_held(self):
    r = replay("rdc")
    assert r["first_stock_s"] is not None
    assert r["first_stock_s"] < r["first_sendcan_s"], (r["first_stock_s"], r["first_sendcan_s"])
    assert r["switch_s"] - r["first_stock_s"] > 0.4, (r["switch_s"], r["first_stock_s"])
    assert r["stock"][np.argmax(r["stock"]):].all()

  def test_a_bus_off_without_a_tool_is_not_fix_1s(self):
    r = replay("rdb")
    assert not r["stock"].any() and r["records"] == []
