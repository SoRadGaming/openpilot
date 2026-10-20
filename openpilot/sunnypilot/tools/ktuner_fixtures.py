#!/usr/bin/env python3
"""
FORK(HONDA_ACCORD_9G_AU): builds the KTuner replay fixtures (2026-10-19) from the owner's parquet route exports.
Dev-only: it needs pyarrow, which the repo's venv does not have - run it where the logs are (the owner's Windows
Python: `python openpilot/sunnypilot/tools/ktuner_fixtures.py --sunny S:/OP/sunny_logs --seg S:/OP/seg_logs`).

Two kinds, each a small compressed .npz, committed:

  fix 1 (card's diag_tool_guard.py), from rlog parquet, in openpilot/sunnypilot/selfdrive/car/tests/fixtures/ktuner/:
    * per `can` message: its logMonoTime (0.1 ms, delta-coded) and whether it held a src-0 frame;
    * every frame whose address is a tester request (on EVERY src), and the responses 1 in 10;
    * carState gear/standstill/canValid/vEgo and carControl enabled/latActive/longActive, at their change points;
    * the logged time of the first car-mode pandaState (the switch) and of the first sendcan (the first
      controls_update).
  fix 2 (pandad's car_bus_guard.h), from the complete 10 Hz pandaStates (rlog or qlog: both have every one), in
  openpilot/sunnypilot/selfdrive/pandad/tests/fixtures/ktuner/: t, ignition, safetyModel, safetyParam, harnessStatus,
  and canState0/canState2 busOff, busOffCnt, transmitErrorCnt.
"""
import argparse
import os
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[3]
OUT_FIX1 = REPO / "openpilot/sunnypilot/selfdrive/car/tests/fixtures/ktuner"
OUT_FIX2 = REPO / "openpilot/sunnypilot/selfdrive/pandad/tests/fixtures/ktuner"
DONGLE = "15646e8515eda1a7"

# name -> (store, route, seconds of can to keep (None: all))
FIX1_ROUTES = {
  "r143": ("sunny", "00000143--24572b7714", None),   # a normal start and the whole drive
  "r144": ("sunny", "00000144--138bf0b32d", None),   # KTuner datalogging at key-on, the whole route (5 rlog segments)
  "r145": ("sunny", "00000145--8ce4d8c03e", None),   # the same, 23 s, ending in noOutput with the car bus alive again
  "r147": ("sunny", "00000147--69444c2243", None),   # the same, 145 s
  "rdc": ("sunny", "000000dc--e82ce6f473", 60),      # a scan tool coming up 0.69 s before the switch
  "rdb": ("sunny", "000000db--b4f4595c7c", 60),      # bus-off with no tool traffic at all (fix 2's case)
  "rb5": ("sunny", "000000b5--ddcf621434", None),    # openpilot's own FW query (src 2 and echoes only)
}
FIX2_ROUTES = {
  "r143": ("sunny", "00000143--24572b7714"),
  "r129": ("sunny", "00000129--6f316e9066"),
  "r144": ("sunny", "00000144--138bf0b32d"),
  "r145": ("sunny", "00000145--8ce4d8c03e"),
  "r147": ("sunny", "00000147--69444c2243"),
  "rdb": ("sunny", "000000db--b4f4595c7c"),
  "rdc": ("sunny", "000000dc--e82ce6f473"),
}
SAFETY = {"silent": 0, "hondaNidec": 1, "elm327": 3, "allOutput": 17, "noOutput": 19}
HARNESS = {"notConnected": 0, "normal": 1, "flipped": 2}
GEARS = ["unknown", "park", "drive", "neutral", "reverse", "sport", "low", "brake", "eco", "manumatic"]
NONCAR = ("silent", "noOutput", "elm327", "allOutput")
TQ_NS = 100_000   # 0.1 ms


def is_request(addr: np.ndarray) -> np.ndarray:
  sa, ta, pgn = addr & 0xFF, (addr >> 8) & 0xFF, addr >> 16
  r29 = (addr > 0x7FF) & ((pgn == 0x18DA) | (pgn == 0x18DB)) & (sa >= 0xF0) & (sa <= 0xFD) & ~((ta >= 0xF0) & (ta <= 0xFD))
  r11 = (addr == 0x7DF) | ((addr >= 0x7E0) & (addr <= 0x7E7))
  return r29 | r11


def is_response(addr: np.ndarray) -> np.ndarray:
  sa, ta, pgn = addr & 0xFF, (addr >> 8) & 0xFF, addr >> 16
  r29 = (addr > 0x7FF) & ((pgn == 0x18DA) | (pgn == 0x18DB)) & (ta >= 0xF0) & (ta <= 0xFD) & ~((sa >= 0xF0) & (sa <= 0xFD))
  return r29 | ((addr >= 0x7E8) & (addr <= 0x7EF))


def base_dir(store: str, route: str, sunny: Path, seg: Path) -> Path:
  return (sunny if store == "sunny" else seg) / f"{DONGLE}_{route}" / "parquet"


def change_points(t: np.ndarray, *cols: np.ndarray) -> np.ndarray:
  keep = np.ones(len(t), bool)
  if len(t) > 1:
    same = np.ones(len(t) - 1, bool)
    for c in cols:
      same &= c[1:] == c[:-1]
    keep[1:] = ~same
  return keep


def panda_states(base: Path) -> dict:
  pf = pq.ParquetFile(base / "pandaStates.parquet")
  paths = {pf.schema.column(i).path for i in range(len(pf.schema))}
  fields = ["ignitionLine", "ignitionCan", "safetyModel", "safetyParam", "harnessStatus"]
  cs = ["busOff", "busOffCnt", "transmitErrorCnt"]
  want = ["_logMonoTime"] + [f"value.list.element.{f}" for f in fields] + \
         [f"value.list.element.canState{b}.{f}" for b in (0, 2) for f in cs]
  tb = pf.read(columns=[w for w in want if w in paths])
  t = tb["_logMonoTime"].to_numpy()
  lst = tb["value"].combine_chunks()
  first = pc.list_parent_indices(lst).to_numpy()
  _, idx = np.unique(first, return_index=True)   # the first panda of each message
  st = lst.flatten()
  out = {"t": t[np.unique(first)]}
  for f in fields:
    a = st.field(f).to_numpy(zero_copy_only=False)[idx]
    out[f] = a
  for b in (0, 2):
    s = st.field(f"canState{b}")
    for f in cs:
      out[f"c{b}_{f}"] = s.field(f).to_numpy(zero_copy_only=False)[idx]
  o = np.argsort(out["t"], kind="stable")
  return {k: v[o] for k, v in out.items()}


def build_fix2(name: str, base: Path, out: Path) -> None:
  ps = panda_states(base)
  t0 = int(ps["t"][0])
  np.savez_compressed(
    out / f"{name}.npz",
    t_ms=((ps["t"] - t0) // 1_000_000).astype(np.int32),
    ignition=(ps["ignitionLine"].astype(bool) | ps["ignitionCan"].astype(bool)),
    mode=np.array([SAFETY.get(m, 255) for m in ps["safetyModel"]], np.uint8),
    param=ps["safetyParam"].astype(np.uint16),
    harness=np.array([HARNESS.get(h, 0) for h in ps["harnessStatus"]], np.uint8),
    **{f"c{b}_{k}": ps[f"c{b}_{f}"].astype(dt) for b in (0, 2)
       for k, f, dt in (("bo", "busOff", bool), ("cnt", "busOffCnt", np.uint32), ("tec", "transmitErrorCnt", np.uint8))},
  )


def build_fix1(name: str, base: Path, keep_s: float | None, out: Path) -> None:
  can = pq.ParquetFile(base / "can.parquet").read(columns=["_logMonoTime", "value.list.element.address",
                                                           "value.list.element.src"])
  t = can["_logMonoTime"].to_numpy()
  lst = can["value"].combine_chunks()
  parent = pc.list_parent_indices(lst).to_numpy()
  st = lst.flatten()
  addr = st.field("address").to_numpy().astype(np.int64)
  src = st.field("src").to_numpy().astype(np.int64)
  o = np.argsort(t, kind="stable")
  rank = np.empty_like(o)
  rank[o] = np.arange(len(o))
  t = t[o]
  parent = rank[parent]
  t0 = int(t[0])
  n = len(t) if keep_s is None else int(np.searchsorted(t, t0 + int(keep_s * 1e9)))
  t = t[:n]
  sel = parent < n
  parent, addr, src = parent[sel], addr[sel], src[sel]

  has0 = np.zeros(n, bool)
  has0[parent[src == 0]] = True
  req = is_request(addr)
  resp = is_response(addr)
  resp_keep = np.zeros(len(addr), bool)
  for s in np.unique(src[resp]):
    w = np.flatnonzero(resp & (src == s))
    resp_keep[w[::10]] = True
  keep = req | resp_keep
  fr_msg, fr_addr, fr_src = parent[keep], addr[keep], src[keep]
  o2 = np.lexsort((fr_addr, fr_msg))

  tq = (t - t0) // TQ_NS
  dt = np.diff(tq, prepend=tq[0])
  big = np.flatnonzero(dt > 0xFFFF)

  cs = pq.read_table(base / "carState.parquet", columns=["_logMonoTime", "gearShifter", "standstill", "canValid", "vEgo"])
  cs_t = cs["_logMonoTime"].to_numpy()
  co = np.argsort(cs_t, kind="stable")
  cs_t = cs_t[co]
  gear = np.array([GEARS.index(g) if g in GEARS else 0 for g in cs["gearShifter"].to_numpy(zero_copy_only=False)[co]], np.uint8)
  sst = cs["standstill"].to_numpy(zero_copy_only=False)[co].astype(bool)
  cv = cs["canValid"].to_numpy(zero_copy_only=False)[co].astype(bool)
  v = cs["vEgo"].to_numpy()[co]
  end = t0 + int(keep_s * 1e9) if keep_s is not None else cs_t[-1] + 1
  cpk = change_points(cs_t, gear, sst, cv) & (cs_t < end)

  cc = pq.read_table(base / "carControl.parquet", columns=["_logMonoTime", "enabled", "latActive", "longActive"])
  cc_t = cc["_logMonoTime"].to_numpy()
  ko = np.argsort(cc_t, kind="stable")
  cc_t = cc_t[ko]
  en, lat, lon = (cc[c].to_numpy(zero_copy_only=False)[ko].astype(bool) for c in ("enabled", "latActive", "longActive"))
  ccp = change_points(cc_t, en, lat, lon) & (cc_t < end)

  ps = panda_states(base)
  car = np.array([m not in NONCAR for m in ps["safetyModel"]])
  switch = int(ps["t"][np.argmax(car)]) - t0 if car.any() else -1
  sc = pq.read_table(base / "sendcan.parquet", columns=["_logMonoTime"])["_logMonoTime"].to_numpy()
  first_sendcan = int(sc.min()) - t0 if len(sc) else -1

  np.savez_compressed(
    out / f"{name}.npz",
    dt_q=np.minimum(dt, 0xFFFF).astype(np.uint16), big_i=big.astype(np.uint32), big_v=dt[big].astype(np.uint64),
    has0=has0,
    fr_msg=fr_msg[o2].astype(np.uint32), fr_addr=fr_addr[o2].astype(np.uint32), fr_src=fr_src[o2].astype(np.uint8),
    cs_t_us=((cs_t[cpk] - t0) // 1000).astype(np.int64), cs_gear=gear[cpk], cs_standstill=sst[cpk], cs_can_valid=cv[cpk],
    cs_v=v[cpk].astype(np.float32),
    cc_t_us=((cc_t[ccp] - t0) // 1000).astype(np.int64), cc_enabled=en[ccp], cc_lat=lat[ccp], cc_long=lon[ccp],
    switch_ns=np.int64(switch), first_sendcan_ns=np.int64(first_sendcan), gears=np.array(GEARS),
  )


def main():
  ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
  ap.add_argument("--sunny", type=Path, default=Path(os.environ.get("SUNNY_LOGS", "S:/OP/sunny_logs")))
  ap.add_argument("--seg", type=Path, default=Path(os.environ.get("SEG_LOGS", "S:/OP/seg_logs")))
  ap.add_argument("--only", nargs="*", help="fixture names to build (default: all)")
  args = ap.parse_args()
  OUT_FIX1.mkdir(parents=True, exist_ok=True)
  OUT_FIX2.mkdir(parents=True, exist_ok=True)
  for name, (store, route, keep_s) in FIX1_ROUTES.items():
    if not args.only or name in args.only:
      build_fix1(name, base_dir(store, route, args.sunny, args.seg), keep_s, OUT_FIX1)
      print("fix1", name, (OUT_FIX1 / f"{name}.npz").stat().st_size)
  for name, (store, route) in FIX2_ROUTES.items():
    if not args.only or name in args.only:
      build_fix2(name, base_dir(store, route, args.sunny, args.seg), OUT_FIX2)
      print("fix2", name, (OUT_FIX2 / f"{name}.npz").stat().st_size)


if __name__ == "__main__":
  main()
