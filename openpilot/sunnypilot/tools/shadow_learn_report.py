#!/usr/bin/env python3
"""
FORK(HONDA_ACCORD_9G_AU): print what the SHADOW learners learned, from one or more routes.

    python openpilot/sunnypilot/tools/shadow_learn_report.py ROUTE [ROUTE ...] [--all-lines]

ROUTE is a route folder as the owner keeps them (<dongle>_<route>/ with parquet/logMessage.parquet, read with pandas,
or raw/*rlog* read through LogReader - used whenever pandas is not installed, as in the repo's own venv), a single
rlog/qlog file, or a text file of logged lines (one line per row, optionally prefixed by a time). Any Python with the
repo on PYTHONPATH runs it; pandas only makes route folders faster. It reads two tags out of logMessage:

  hondashadow  opendbc/sunnypilot/car/honda/shadow_learn.py (card): the brake response table (speed band x command
               band: achieved - commanded accel, and what the table would apply), the coast deceleration per speed
               band, and the launch ratio and multiplier (no lead; launches behind a lead apart). Written only on
               the Elesys Accord with the gas interceptor AND Dynamic Tuning (HondaDynamicTuningEnabled) on
  latsplit     openpilot/sunnypilot/selfdrive/locationd/lat_speed_split.py (torqued): torqued's fit below and above
               70 km/h

Routes driven before batch 2 (2026-10-04; 115 and earlier) have neither line: the report says so and moves on.

Both are DRIVE TOTALS (they start from zero at ignition), so the last line of a route is that route, and routes are
combined by their counts: brake cells and coast bands by sample-weighted means, the launch by summing its two least-
squares sums, the lateral halves by summing their moments and refitting -- which is exact. The combined section also
lists each brake cell per route, because a table should only ever be applied once separate drives agree.

Nothing the shadow learners log is used by the car. This script only reads.
"""
import argparse
import glob
import json
import math
import os
import sys

import numpy as np

TAGS = ("hondashadow", "latsplit")
MIN_CELL_SAMPLES = 250        # shadow_learn.MIN_CELL_SAMPLES (50 Hz samples)
RATE_HZ = 50


def parse_line(msg: str) -> tuple[str, dict]:
  tag, _, body = msg.strip().partition(" ")
  out = {}
  for tok in body.split():
    if "=" not in tok:
      continue
    k, v = tok.split("=", 1)
    if v.startswith("["):
      out[k] = [float(x) for x in v.strip("[]").split(",") if x]
    else:
      try:
        out[k] = float(v)
      except ValueError:
        out[k] = v
  return tag, out


def _msg_text(raw: str) -> str:
  """logMessage holds swaglog JSON ({"msg": ...}); a text dump holds the bare line, maybe after a timestamp."""
  try:
    msg = json.loads(raw).get("msg", "")
    if isinstance(msg, str):
      return msg
  except Exception:
    pass
  for tag in TAGS:
    i = raw.find(tag + " ")
    if i >= 0:
      return raw[i:]
  return ""


def _parquet_messages(pq: str) -> list | None:
  """logMessage.parquet's messages, or None without pandas (the repo's venv has none): then the rlogs are read."""
  try:
    import pandas as pd
  except ImportError:
    return None
  return [v for v in pd.read_parquet(pq, columns=["value"])["value"] if isinstance(v, str)]


def _iter_raw(path: str):
  if os.path.isfile(path) and path.endswith(".txt"):
    with open(path) as f:
      yield from f
    return
  pq = os.path.join(path, "parquet", "logMessage.parquet")
  msgs = _parquet_messages(pq) if os.path.isdir(path) and os.path.isfile(pq) else None
  if msgs is not None:
    yield from msgs
    return
  from openpilot.tools.lib.logreader import LogReader
  if os.path.isdir(path):
    files = sorted(glob.glob(os.path.join(path, "raw", "*rlog*")) or glob.glob(os.path.join(path, "*rlog*")),
                   key=lambda p: int(os.path.basename(p).split("--")[0]) if os.path.basename(p).split("--")[0].isdigit() else 0)
  else:
    files = [path]
  for fn in files:
    for m in LogReader(fn):
      if m.which() == "logMessage":
        yield m.logMessage


def read_route(path: str) -> dict:
  lines = {t: [] for t in TAGS}
  for raw in _iter_raw(path):
    if not any(t in raw for t in TAGS):
      continue
    msg = _msg_text(raw)
    if not msg.startswith(TAGS):
      continue
    try:
      tag, d = parse_line(msg)
    except Exception:
      continue
    if tag in lines:
      lines[tag].append(d)
  return lines


# --- longitudinal ------------------------------------------------------------------------------------

def _bands(edges, unit, open_last=True):
  e = [f"{x:g}" for x in edges]
  out = [f"{a}-{b}" for a, b in zip(e[:-1], e[1:], strict=True)]
  if open_last:
    out.append(f">{e[-1]}")
  return [s + unit for s in out]


def brake_tables(d: dict) -> dict:
  spd, cnt = d["bspd"], d["bcnt"]
  ns, nc = len(spd), len(cnt)
  grid = {k: np.array(d[k], dtype=float).reshape(ns, nc) for k in ("bn", "be", "bsd", "bcorr", "bcb", "bacc")}
  rows = _bands(spd, " m/s")
  cols = [f"{cnt[0]:g}-{cnt[1]:g}", f"{cnt[1]:g}-{cnt[2]:g}", f">{cnt[2]:g}"] if nc == 3 else [str(c) for c in cnt]
  return {"rows": rows, "cols": [c + " cb" for c in cols], **grid}


def print_grid(title, rows, cols, cell):
  w = max(len(c) for c in cols) + 2
  print(f"  {title}")
  print("    " + " " * 12 + "".join(c.rjust(max(w, 16)) for c in cols))
  for i, r in enumerate(rows):
    print("    " + r.ljust(12) + "".join(cell(i, j).rjust(max(w, 16)) for j in range(len(cols))))


def fmt(x, spec="+.2f"):
  return "-" if x is None or not math.isfinite(x) else format(x, spec)


def report_long(name: str, d: dict) -> None:
  b = brake_tables(d)
  print("\n  BRAKE (L1): achieved - commanded accel, m/s^2 (+ = under-braking), seconds of samples in ()")
  print_grid("", b["rows"], b["cols"], lambda i, j: f"{fmt(b['be'][i, j])} ({b['bn'][i, j] / RATE_HZ:.1f}s)" if b["bn"][i, j] else "-")
  print_grid("would apply (m/s^2, <= 0 = more brake; 0 until 5 s in a cell):", b["rows"], b["cols"],
             lambda i, j: fmt(b["bcorr"][i, j]) if b["bn"][i, j] else "-")
  print_grid("mean command (counts) / achieved accel:", b["rows"], b["cols"],
             lambda i, j: f"{b['bcb'][i, j]:.0f} / {b['bacc'][i, j]:+.2f}" if b["bn"][i, j] else "-")
  rows = _bands(d["bspd"], " m/s")
  print("  COAST (no pedal, no brake): achieved accel / error vs command, by speed")
  for i, r in enumerate(rows):
    n = d["cn"][i]
    if n:
      print(f"    {r:12s} {d['cacc'][i]:+.3f} / {d['cerr'][i]:+.3f}  ({n / RATE_HZ:.1f}s)")
  if "bgain" in d:
    print(f"  measured at a live brake gain of {fmt(d['bgain'][0], '.3f')} (mean over the brake samples): the table is " +
          "the law's error at that gain, its counts the law's, before the gain")
  lrows = _bands(d["lspd"], " m/s", open_last=False)
  print(f"  LAUNCH (L2b): achieved/commanded (gravity removed), no lead, pedal seen by the PCM, {int(d['lep'])} episode(s)")
  for i, r in enumerate(lrows):
    print(f"    {r:12s} ratio {fmt(d['lratio'][i], '.3f')}  ({d['ln'][i] / RATE_HZ:.1f}s)")
  print(f"    pooled multiplier it would apply: {d['lmult']:.3f}  (1.0 = none; bounded 0.6-1.0, needs 2 s)")
  if "lnl" in d:
    print(f"    behind a lead (never in the multiplier), {int(d['lepl'])} episode(s): " +
          "  ".join(f"{r} {fmt(d['lratiol'][i], '.3f')} ({d['lnl'][i] / RATE_HZ:.1f}s)" for i, r in enumerate(lrows)))


def combine_long(per_route: dict) -> dict | None:
  ds = [d for d in per_route.values() if d]
  if not ds:
    return None
  out = dict(ds[0])
  n = np.sum([np.array(d["bn"]) for d in ds], axis=0)
  out["bn"] = n.tolist()
  for k in ("be", "bcb", "bacc"):
    s = sum(np.nan_to_num(np.array(d[k])) * np.array(d["bn"]) for d in ds)
    out[k] = np.where(n > 0, s / np.maximum(n, 1), np.nan).tolist()
  # pooled sd from per-route sd and means
  ss = sum((np.nan_to_num(np.array(d["bsd"])) ** 2 + np.nan_to_num(np.array(d["be"])) ** 2) * np.array(d["bn"]) for d in ds)
  m = np.array(out["be"])
  out["bsd"] = np.where(n > 1, np.sqrt(np.maximum(ss / np.maximum(n, 1) - np.nan_to_num(m) ** 2, 0)), np.nan).tolist()
  out["bcorr"] = [min(max(-e, -0.5), 0.0) if (c >= MIN_CELL_SAMPLES and math.isfinite(e)) else 0.0 for e, c in zip(m, n, strict=True)]
  cn = sum(np.array(d["cn"]) for d in ds)
  out["cn"] = cn.tolist()
  for k in ("cacc", "cerr"):
    s = sum(np.nan_to_num(np.array(d[k])) * np.array(d["cn"]) for d in ds)
    out[k] = np.where(cn > 0, s / np.maximum(cn, 1), np.nan).tolist()
  for k in ("ln", "lra", "lrr"):
    out[k] = sum(np.array(d[k]) for d in ds).tolist()
  out["lratio"] = [ra / rr if rr > 0 else float("nan") for ra, rr in zip(out["lra"], out["lrr"], strict=True)]
  out["lep"] = sum(d["lep"] for d in ds)
  ln, ra, rr = sum(out["ln"]), sum(out["lra"]), sum(out["lrr"])
  out["lmult"] = 1.0 if (ln < 100 or ra <= 0 or rr <= 0) else min(max(rr / ra, 0.6), 1.0)
  if all("lnl" in d for d in ds):
    for k in ("lnl", "lral", "lrrl"):
      out[k] = sum(np.array(d[k]) for d in ds).tolist()
    out["lratiol"] = [ra / rr if rr > 0 else float("nan") for ra, rr in zip(out["lral"], out["lrrl"], strict=True)]
    out["lepl"] = sum(d["lepl"] for d in ds)
  if all("bgain" in d for d in ds):
    w = [sum(d["bn"]) for d in ds]
    out["bgain"] = [sum(np.nan_to_num(d["bgain"][0]) * x for d, x in zip(ds, w, strict=True)) / sum(w)] if sum(w) else [float("nan")]
  return out


def agreement(per_route: dict) -> None:
  ds = {k: v for k, v in per_route.items() if v}
  if len(ds) < 2:
    return
  b0 = brake_tables(next(iter(ds.values())))
  print("\n  brake cells per route (mean error, only cells with >= 5 s):")
  for i, r in enumerate(b0["rows"]):
    for j, c in enumerate(b0["cols"]):
      vals = []
      for name, d in ds.items():
        b = brake_tables(d)
        if b["bn"][i, j] >= MIN_CELL_SAMPLES:
          vals.append(f"{name}:{b['be'][i, j]:+.2f}")
      if vals:
        print(f"    {r:10s} {c:12s} " + "  ".join(vals))


# --- lateral -----------------------------------------------------------------------------------------

def _tls(mom):
  try:
    from openpilot.sunnypilot.selfdrive.locationd.lat_speed_split import tls_fit
    return tls_fit(*mom)
  except ImportError:
    n, sx, sy, sxx, sxy, syy = mom
    if n < 2:
      return None
    _, v = np.linalg.eigh(np.array([[sxx, sx, sxy], [sx, n, sy], [sxy, sy, syy]]))
    e = v[:, 0]
    return (-e[0] / e[2], -e[1] / e[2], float("nan")) if abs(e[2]) > 1e-12 else None


def report_lat(d: dict) -> None:
  prior = d.get("prior", float("nan"))
  main = fmt(d.get('main', float('nan')), '.3f')
  print(f"\n  LATERAL (L3): torqued-style fit split at {d.get('split', 19.44) * 3.6:.0f} km/h; prior {prior:.3f}, " +
        f"torqued's own (filtered) factor {main}")
  for i, h in enumerate(("lo", "hi")):
    label = "below" if h == "lo" else "above"
    cal = "-" if not math.isfinite(d['cal'][i]) else f"{int(d['cal'][i])}%"
    valid = "-" if not math.isfinite(d['valid'][i]) else str(int(d['valid'][i]))
    print(f"    {label}: {int(d['n'][i]):6d} points ({d['n'][i] / 20 / 60:.1f} min)  factor {fmt(d['fac'][i], '.3f')}  " +
          f"clipped {fmt(d['clip'][i], '.3f')}  offset {fmt(d['off'][i], '+.3f')}  friction {fmt(d['fric'][i], '.3f')}  " +
          f"cal {cal}  valid {valid}")
  lo, hi = d["fac"]
  if math.isfinite(lo) and math.isfinite(hi):
    for kmh in (50, 60, 70, 80, 90, 100):
      f = float(np.interp(kmh / 3.6, [60 / 3.6, 80 / 3.6], [lo, hi]))
      print(f"      at {kmh:3d} km/h a split torqued would use {f:.3f}")


def combine_lat(per_route: dict) -> dict | None:
  ds = [d for d in per_route.values() if d]
  if not ds:
    return None
  out = dict(ds[-1])
  for i, h in enumerate(("lo", "hi")):
    mom = list(np.sum([d[f"mom_{h}"] for d in ds], axis=0))
    bins = list(np.sum([d[f"bins_{h}"] for d in ds], axis=0))
    out[f"mom_{h}"], out[f"bins_{h}"] = mom, bins
    out["n"] = list(out["n"])
    out["n"][i] = mom[0]
    fit = _tls(mom) if mom[0] >= 200 else None
    for k, x in zip(("fac", "off", "fric"), fit if fit else (float("nan"),) * 3, strict=True):
      out[k] = list(out[k])
      out[k][i] = x
    out["clip"] = list(out["clip"])
    out["clip"][i] = min(max(out["fac"][i], 0.7 * out["prior"]), 1.3 * out["prior"]) if fit else float("nan")
    out["cal"] = list(out["cal"])
    out["cal"][i] = float("nan")
    out["valid"] = list(out["valid"])
    out["valid"][i] = float("nan")
  out["main"] = float("nan")
  return out


def route_name(path: str, taken) -> str:
  """'00000115' for a route folder or segment (<dongle>_<route>--...), the file's own name for anything else; never one
  already taken, so routes cannot overwrite each other in the combined tables."""
  base = os.path.basename(os.path.normpath(path))
  name = base.split("--")[0][-8:] if "--" in base else os.path.splitext(base)[0]
  while name in taken:
    name += "'"
  return name


def main(argv=None) -> int:
  ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  ap.add_argument("routes", nargs="+")
  ap.add_argument("--all-lines", action="store_true", help="also print how the totals grew, line by line")
  args = ap.parse_args(argv)

  long_last, lat_last = {}, {}
  for path in args.routes:
    name = route_name(path, long_last)
    lines = read_route(path)
    print(f"\n=== {path}: {len(lines['hondashadow'])} hondashadow line(s), {len(lines['latsplit'])} latsplit line(s)")
    long_last[name] = lines["hondashadow"][-1] if lines["hondashadow"] else None
    lat_last[name] = lines["latsplit"][-1] if lines["latsplit"] else None
    if long_last[name]:
      report_long(name, long_last[name])
    else:
      print("  no hondashadow line: tuner off, another car, a build without the shadow, or no admitted sample")
    if lat_last[name]:
      report_lat(lat_last[name])
    else:
      print("  no latsplit line: no torqued point this drive, another car, or a build without the shadow")
    if args.all_lines:
      for d in lines["hondashadow"]:
        print(f"    long: brake {sum(d['bn']) / RATE_HZ:.1f}s coast {sum(d['cn']) / RATE_HZ:.1f}s launch {sum(d['ln']) / RATE_HZ:.1f}s")
      for d in lines["latsplit"]:
        print(f"    lat: n {d['n']} fac {d['fac']}")

  if len(args.routes) > 1:
    print(f"\n=== COMBINED over {len(args.routes)} routes (weighted by samples; lateral refit from summed moments)")
    c = combine_long(long_last)
    if c:
      report_long("all", c)
      agreement(long_last)
    c = combine_lat(lat_last)
    if c:
      report_lat(c)
  return 0


if __name__ == "__main__":
  sys.exit(main())
