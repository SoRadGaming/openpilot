#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): build_index.py - TfNSW Speed Zones + School Zones -> nsw_zones.npz + manifest.json + ATTRIBUTION.txt

  python build_index.py --speedzones speed_zones.geojson --schoolzones schoolzones.zip --out DIR
                        [--previous-manifest OLD/manifest.json] [--previous-index OLD/nsw_zones.npz] [--allow-review]
                        [--simplify-m 1.0] [--stored]

Runs on a PC / in CI only, never on the device. STANDALONE: numpy + the standard library + its sibling files
index.py, school_days.py and matcher.py (loaded by path) - no openpilot imports, so a sparse checkout of this
directory is enough. Exits non-zero (2) and writes no index if a gate fails:
  * value sets: every Type / Direction / Status / Speed is a known value, geometry is (Multi)LineString
  * size: >= 400,000 base line parts, 1,000..20,000 school records
  * feature count within +-5 % of --previous-manifest (skipped, with a warning, when none is given)
  * REVIEW gates, against the previous release - the changes that could RAISE a published limit or drop school
    zones, which a person checks before they reach the cars (--allow-review turns them into warnings; the Action
    passes it only on a manual `force` run):
      school zones and School lines within +-5 %; any change in the number of Variable lines (each one drawn on a
      lower static zone publishes the higher value); base lines whose limit went up: > 20 km, or any to 90+ km/h;
      > 50 km of new or re-drawn 90+ km/h lines (--previous-index; lines are matched by identical geometry)
  * the school calendar (school_days.py) covers today + 30 days for both divisions
  * self-test: the written file decodes to exactly the arrays built, and the matcher, run on points placed on
    random lines of the new index, publishes that line's limit (or a co-located line's) for >= 85 % of them

Files written into --out:
  nsw_zones.npz       the index (deflate + delta coding, fixed member timestamps; index.load_index() decodes it)
  manifest.json       small: format version, sha256 + bytes of the npz, data version, counts, calendar coverage,
                      gates, attribution. The device reads this before downloading.
  ATTRIBUTION.txt     the CC BY 4.0 attribution
  build_report.json   diagnostics (school-time interpretations, join statistics, OP_CAL variants, log)
  nsw_zones_stored.npz  with --stored: uncompressed, 64-byte aligned, for Matcher(path, mmap=True)

Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0.
Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW.

Decoded layout (every array is a plain numpy array; no pickles):

  meta_json             uint8   UTF-8 JSON: format version, data version, attribution, grid, enums, counts
  attribution           <U      the CC BY line (also inside meta_json and the manifest)
  calendar_json         uint8   UTF-8 JSON: the school-zone day calendar (school_days.build_json) the index was
                                built with; the matcher uses it when it reaches further than its own copy

  Two independent line layers with the SAME structure, prefix "b_" (BASE = the
  matchable zones) and "o_" (OVERLAY = School / School Bus / Wet Weather lines,
  which TfNSW draws ON TOP of an unbroken base line and which are never matched
  on their own):

  {p}vlat, {p}vlon      int32   vertices, micro-degrees WGS84 (0.11 m), all parts concatenated
  {p}part_v0            int32   CSR, len P+1: part k owns vertices [part_v0[k], part_v0[k+1])
  {p}part_speed         uint8   km/h
  {p}part_type          uint8   TYPE_* code (see TYPES)
  {p}part_dir           uint8   DIR_* code (0 Both, 1 One Way, 2 None/null)
  {p}part_feat          int32   feature index in the source GeoJSON (traceability; not stable across builds)
  {p}part_school        int16   o_ only: row in the school table, -1 if the line joined no polygon
  {p}chunk_v0           int32   a chunk = up to CHUNK consecutive segments of one part; first vertex
  {p}chunk_n            uint8   segments in the chunk
  {p}chunk_part         int32   owning part
  {p}grid_keys          int32   sorted non-empty cell keys (row * ncol + col)
  {p}grid_start         int32   CSR, len K+1, into grid_items
  {p}grid_items         int32   chunk ids

  School table (one row per SchoolZones.json record), prefix "s_":
  s_zone_id int64, s_speed uint8, s_am0/s_am1/s_pm0/s_pm1 int16 minutes after local
  midnight, s_late uint8 (LATE_OPENING_SCHOOL = Western division), s_tz uint8
  (0 Sydney, 1 Broken Hill, 2 Lord Howe), s_dir uint8, s_flags uint16 (S_FLAG_*),
  s_opcal uint8 (row of opcal_*), s_name_blob uint8 + s_name_off int32 (UTF-8),
  s_bbox int32 (N,4 lat0,lon0,lat1,lon1), polygons: s_ring0 int32 (CSR school->rings),
  s_ring_v0 int32 (CSR ring->vertices), s_rlat/s_rlon int32.
  opcal_start/opcal_end int32 (V, 8) days since 1970-01-01, -1 padded -- TfNSW's own
  term calendar, kept for cross-checking only (it has known errors, see manifest).
"""

import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import re
import sys
import time
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name):
  """Load a sibling module of this file by path, registered as 'nsw_zones_<name>' (works without the openpilot
  package on sys.path; matcher.py falls back to these names for its own imports)."""
  key = 'nsw_zones_' + name
  mod = sys.modules.get(key)
  if mod is not None:
    return mod
  spec = importlib.util.spec_from_file_location(key, os.path.join(HERE, name + '.py'))
  if spec is None or spec.loader is None:
    raise ImportError(f'cannot load {name}.py next to build_index.py')
  mod = importlib.util.module_from_spec(spec)
  sys.modules[key] = mod
  spec.loader.exec_module(mod)
  return mod


nz_index = _sibling('index')
school_days = _sibling('school_days')

FORMAT_VERSION = nz_index.FORMAT_VERSION
ATTRIBUTION = "Contains data from Transport for NSW, CC BY 4.0, modified"
ATTRIBUTION_LONG = (
  "Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0. "
  + "Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW."
)

TYPES = ['Default', 'Ordinary Permanent', 'School', 'High Pedestrian', 'Shared', 'Local Traffic', 'Variable', 'School Bus', 'Wet Weather', 'Toll Plaza']
TYPE_CODE = {t: i for i, t in enumerate(TYPES)}
OVERLAY_TYPES = {'School', 'School Bus', 'Wet Weather'}
DIRS = ['Both Directions', 'One Way', None]  # code 2 = null in the source
DIR_CODE = {d: i for i, d in enumerate(DIRS)}
STATUSES = {'Existing'}

S_FLAG_TIME_GUESSED = 1  # a time string was malformed and had to be interpreted
S_FLAG_TIME_DEFAULT = 2  # times missing -> standard 08:00-09:30 / 14:30-16:00
S_FLAG_NO_LINE = 4  # polygon matched no School line in the main layer
S_FLAG_WINDOW_ODD = 8  # parsed windows fail the sanity check (order / range)

LINE_FLAG_NO_POLYGON = 1

CHUNK = 8  # segments per chunk
GRID_DLAT_E6 = 1000  # 0.001 deg  ~ 111 m
GRID_DLON_E6 = 1200  # 0.0012 deg ~ 110 m at 33.9 S
GRID_SAMPLE_M = 20.0  # sample spacing when rasterizing segments; query pads by half
M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON_EQ = 111320.0

STD_TIMES = (8 * 60, 9 * 60 + 30, 14 * 60 + 30, 16 * 60)


# ----------------------------------------------------------------------------- helpers
sha256_file = nz_index.sha256_file


def dp_keep(x, y, tol):
  """Douglas-Peucker (distance to the chord SEGMENT, so hairpins are safe). Returns a bool mask."""
  n = len(x)
  keep = np.zeros(n, bool)
  keep[0] = keep[-1] = True
  if n <= 2:
    return keep
  stack = [(0, n - 1)]
  while stack:
    i, j = stack.pop()
    if j <= i + 1:
      continue
    dx = x[j] - x[i]
    dy = y[j] - y[i]
    px = x[i + 1 : j] - x[i]
    py = y[i + 1 : j] - y[i]
    L2 = dx * dx + dy * dy
    if L2 > 0:
      t = np.clip((px * dx + py * dy) / L2, 0.0, 1.0)
      d = np.hypot(px - t * dx, py - t * dy)
    else:
      d = np.hypot(px, py)
    k = int(np.argmax(d))
    if d[k] > tol:
      m = i + 1 + k
      keep[m] = True
      stack.append((i, m))
      stack.append((m, j))
  return keep


def parse_speed(s):
  m = re.fullmatch(r'\s*(\d{1,3})\s*km/h\s*', s or '')
  return int(m.group(1)) if m else None


_TIME_RE = re.compile(r'^(\d{1,2})(?::(\d{2}))?\s*([AP]M)?$')


def parse_time(raw, expect):
  """
  Parse a SchoolZones.json time string. `expect` is 'AM' or 'PM' (which field it came from).
  Returns (minutes after midnight or None, note or None). A note means the string was not in
  the canonical 'hh:mm AM' form and had to be interpreted; the note says how.
  """
  if raw is None or str(raw).strip() == '':
    return None, 'missing'
  s = ' '.join(str(raw).split()).upper()
  m = _TIME_RE.match(s)
  if not m:
    return None, f'unparsable {raw!r}'
  h = int(m.group(1))
  mi = int(m.group(2)) if m.group(2) else 0
  ap = m.group(3)
  notes = []
  if raw != s or not re.fullmatch(r'\d{2}:\d{2} [AP]M', raw):
    if '  ' in raw:
      notes.append('double space')
    if m.group(2) is None:
      notes.append('no minutes')
    elif len(m.group(1)) == 1:
      notes.append('one-digit hour')
  if mi > 59:
    return None, f'minutes out of range {raw!r}'
  if ap is None:
    ap = expect
    notes.append(f'no AM/PM, assumed {expect}')
  if h > 12:
    if h > 23:
      return None, f'hour out of range {raw!r}'
    notes.append(f'24-hour value with {ap} suffix, read as {h:02d}:{mi:02d}')
    return h * 60 + mi, '; '.join(notes)
  if ap == 'AM' and h == 12:
    if expect == 'AM' and mi == 0:
      # '12:00 AM' as the END of the morning window: midnight is impossible there. The one
      # record that has it (849925) starts its PM window at 12:01 PM, so it means noon.
      notes.append("'12:00 AM' read as 12:00 noon (end of a morning window)")
      return 12 * 60, '; '.join(notes)
    h = 0
  elif ap == 'PM' and h < 12:
    h += 12
  return h * 60 + mi, ('; '.join(notes) or None)


def point_in_rings(plat, plon, rings):
  """Even-odd point-in-polygon over a list of (lat, lon) int rings (holes handled by parity).
  plat/plon: arrays. Returns bool array."""
  inside = np.zeros(len(plat), bool)
  py = plat.astype(np.float64)[:, None]
  px = plon.astype(np.float64)[:, None]
  for rlat, rlon in rings:
    y0 = rlat.astype(np.float64)[None, :]
    x0 = rlon.astype(np.float64)[None, :]
    y1 = np.roll(y0, -1, axis=1)
    x1 = np.roll(x0, -1, axis=1)
    cond = (y0 > py) != (y1 > py)
    with np.errstate(divide='ignore', invalid='ignore'):
      xint = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
    cross = cond & (px < xint)
    inside ^= (np.count_nonzero(cross, axis=1) % 2).astype(bool)
  return inside


def dist_points_to_rings_m(plat, plon, rings):
  """min distance (m) from each point to any ring edge."""
  lat0 = float(np.mean(plat)) * 1e-6
  kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(lat0))
  ky = 1e-6 * M_PER_DEG_LAT
  best = np.full(len(plat), np.inf)
  px = plon.astype(np.float64)[:, None] * kx
  py = plat.astype(np.float64)[:, None] * ky
  for rlat, rlon in rings:
    ax = rlon.astype(np.float64)[None, :-1] * kx
    ay = rlat.astype(np.float64)[None, :-1] * ky
    bx = rlon.astype(np.float64)[None, 1:] * kx
    by = rlat.astype(np.float64)[None, 1:] * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    with np.errstate(divide='ignore', invalid='ignore'):
      t = np.where(L2 > 0, ((px - ax) * dx + (py - ay) * dy) / L2, 0.0)
    t = np.clip(t, 0, 1)
    d = np.hypot(px - (ax + t * dx), py - (ay + t * dy))
    best = np.minimum(best, d.min(axis=1))
  return best


def school_tz(lat, lon):
  """0 Australia/Sydney, 1 Australia/Broken_Hill (Yancowinna County), 2 Australia/Lord_Howe."""
  if lon > 158.0:
    return 2
  if 141.0 <= lon <= 142.0 and -32.5 <= lat <= -31.3:
    return 1
  return 0


# ----------------------------------------------------------------------------- layers
class LayerBuilder:
  def __init__(self, name):
    self.name = name
    self.lat, self.lon = [], []
    self.speed, self.type, self.dir, self.feat = [], [], [], []
    self.nv_in = 0
    self.nv_out = 0
    self.dropped_parts = 0

  def add(self, coords, speed, tcode, dcode, fidx, simplify_m):
    a = np.asarray(coords, dtype=np.float64)
    if a.ndim != 2 or a.shape[0] < 2:
      self.dropped_parts += 1
      return
    self.nv_in += a.shape[0]
    lat = np.round(a[:, 1] * 1e6).astype(np.int64)
    lon = np.round(a[:, 0] * 1e6).astype(np.int64)
    # drop consecutive duplicates (after quantisation)
    keep = np.ones(len(lat), bool)
    keep[1:] = (np.diff(lat) != 0) | (np.diff(lon) != 0)
    lat, lon = lat[keep], lon[keep]
    if len(lat) < 2:
      self.dropped_parts += 1
      return
    if simplify_m > 0 and len(lat) > 2:
      kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(lat[0] * 1e-6))
      x = (lon - lon[0]) * kx
      y = (lat - lat[0]) * (1e-6 * M_PER_DEG_LAT)
      k = dp_keep(x, y, simplify_m)
      lat, lon = lat[k], lon[k]
    self.nv_out += len(lat)
    self.lat.append(lat.astype(np.int32))
    self.lon.append(lon.astype(np.int32))
    self.speed.append(speed)
    self.type.append(tcode)
    self.dir.append(dcode)
    self.feat.append(fidx)

  def finish(self, grid):
    P = len(self.lat)
    if P == 0:
      z32 = np.zeros(0, np.int32)
      return {
        'vlat': z32,
        'vlon': z32.copy(),
        'part_v0': np.zeros(1, np.int32),
        'part_speed': np.zeros(0, np.uint8),
        'part_type': np.zeros(0, np.uint8),
        'part_dir': np.zeros(0, np.uint8),
        'part_feat': z32.copy(),
        'chunk_v0': z32.copy(),
        'chunk_n': np.zeros(0, np.uint8),
        'chunk_part': z32.copy(),
        'grid_keys': z32.copy(),
        'grid_start': np.zeros(1, np.int32),
        'grid_items': z32.copy(),
      }, {
        'parts': 0,
        'vertices_in': self.nv_in,
        'vertices_out': 0,
        'segments': 0,
        'chunks': 0,
        'dropped_parts': self.dropped_parts,
        'length_km': 0.0,
        'grid_cells': 0,
        'grid_items': 0,
        'chunks_per_cell_max': 0,
        'chunks_per_cell_p99': 0.0,
      }
    lens = np.fromiter((len(v) for v in self.lat), dtype=np.int64, count=P)
    part_v0 = np.zeros(P + 1, np.int64)
    np.cumsum(lens, out=part_v0[1:])
    vlat = np.concatenate(self.lat)
    vlon = np.concatenate(self.lon)
    # chunks
    nseg = lens - 1
    nch = (nseg + CHUNK - 1) // CHUNK
    C = int(nch.sum())
    chunk_part = np.repeat(np.arange(P, dtype=np.int64), nch)
    k = np.arange(C, dtype=np.int64) - np.repeat(np.cumsum(nch) - nch, nch)
    chunk_v0 = part_v0[chunk_part] + k * CHUNK
    chunk_n = np.minimum(CHUNK, nseg[chunk_part] - k * CHUNK)
    # segments
    S = int(chunk_n.sum())
    seg_chunk = np.repeat(np.arange(C, dtype=np.int64), chunk_n)
    seg_v = chunk_v0[seg_chunk] + (np.arange(S, dtype=np.int64) - np.repeat(np.cumsum(chunk_n) - chunk_n, chunk_n))
    la0 = vlat[seg_v].astype(np.float64)
    lo0 = vlon[seg_v].astype(np.float64)
    la1 = vlat[seg_v + 1].astype(np.float64)
    lo1 = vlon[seg_v + 1].astype(np.float64)
    kx = 1e-6 * M_PER_DEG_LON_EQ * np.cos(np.radians(la0 * 1e-6))
    L = np.hypot((lo1 - lo0) * kx, (la1 - la0) * 1e-6 * M_PER_DEG_LAT)
    ns = np.ceil(L / GRID_SAMPLE_M).astype(np.int64) + 1
    N = int(ns.sum())
    samp_seg = np.repeat(np.arange(S, dtype=np.int64), ns)
    idx = np.arange(N, dtype=np.int64) - np.repeat(np.cumsum(ns) - ns, ns)
    frac = idx / (ns[samp_seg] - 1)
    slat = la0[samp_seg] + frac * (la1 - la0)[samp_seg]
    slon = lo0[samp_seg] + frac * (lo1 - lo0)[samp_seg]
    row = np.floor((slat - grid['lat0_e6']) / GRID_DLAT_E6).astype(np.int64)
    col = np.floor((slon - grid['lon0_e6']) / GRID_DLON_E6).astype(np.int64)
    assert row.min() >= 0 and col.min() >= 0 and row.max() < grid['nrow'] and col.max() < grid['ncol']
    key = row * grid['ncol'] + col
    pair = np.unique((key << 32) | seg_chunk[samp_seg])
    keys_all = pair >> 32
    items = (pair & 0xFFFFFFFF).astype(np.int32)
    gkeys, gfirst = np.unique(keys_all, return_index=True)
    gstart = np.append(gfirst, len(items)).astype(np.int32)
    seg_len_total_km = float(L.sum() / 1000.0)
    per_cell = np.diff(gstart)
    return {
      'vlat': vlat,
      'vlon': vlon,
      'part_v0': part_v0.astype(np.int32),
      'part_speed': np.array(self.speed, np.uint8),
      'part_type': np.array(self.type, np.uint8),
      'part_dir': np.array(self.dir, np.uint8),
      'part_feat': np.array(self.feat, np.int32),
      'chunk_v0': chunk_v0.astype(np.int32),
      'chunk_n': chunk_n.astype(np.uint8),
      'chunk_part': chunk_part.astype(np.int32),
      'grid_keys': gkeys.astype(np.int32),
      'grid_start': gstart,
      'grid_items': items,
    }, {
      'parts': P,
      'vertices_in': self.nv_in,
      'vertices_out': int(len(vlat)),
      'segments': S,
      'chunks': C,
      'dropped_parts': self.dropped_parts,
      'length_km': round(seg_len_total_km, 1),
      'grid_cells': int(len(gkeys)),
      'grid_items': int(len(items)),
      'chunks_per_cell_max': int(per_cell.max()),
      'chunks_per_cell_p99': float(np.percentile(per_cell, 99)),
    }


# ----------------------------------------------------------------------------- schools
def load_schools(zpath, log):
  z = zipfile.ZipFile(zpath)
  names = [n for n in z.namelist() if n.lower().endswith('.json')]
  if len(names) != 1:
    raise SystemExit(f'expected one .json in {zpath}, found {names}')
  info = z.getinfo(names[0])
  data = json.loads(z.read(names[0]))
  head = data[0] if data and isinstance(data[0], dict) and 'generated' in data[0] else None
  recs = data[1:] if head else data
  return recs, {
    'member': names[0],
    'member_size': info.file_size,
    'member_zip_datetime': '{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}'.format(*info.date_time),
    'generated': head.get('generated') if head else None,
  }


_OPCAL_RE = re.compile(r"StartDate='(\d{4}-\d{2}-\d{2})'\s+EndDate='(\d{4}-\d{2}-\d{2})'\s+Recurrence='([^']*)'")


def build_schools(recs, gates, log):
  import ast

  rows = []
  guesses = []
  opcal_variants = {}
  for r in recs:
    zid = r.get('SZ_ZONE_ID')
    name = (r.get('SCL_NAME') or '').strip()
    sp = parse_speed(r.get('SZ_SPEED'))
    if sp is None:
      gates.append(f'school {zid}: unparsable SZ_SPEED {r.get("SZ_SPEED")!r}')
      sp = 40
    flags = 0
    times = []
    for fld, exp in (('START_TIME_AM', 'AM'), ('END_TIME_AM', 'AM'), ('START_TIME_PM', 'PM'), ('END_TIME_PM', 'PM')):
      v, note = parse_time(r.get(fld), exp)
      times.append(v)
      if note and note != 'missing':
        flags |= S_FLAG_TIME_GUESSED
        read_as = None if v is None else '{:02d}:{:02d}'.format(*divmod(v, 60))
        guesses.append({'zone_id': zid, 'school': name, 'field': fld, 'raw': r.get(fld), 'read_as': read_as, 'note': note})
    if any(v is None for v in times):
      missing = [f for f, v in zip(('START_TIME_AM', 'END_TIME_AM', 'START_TIME_PM', 'END_TIME_PM'), times, strict=False) if v is None]
      times = [v if v is not None else s for v, s in zip(times, STD_TIMES, strict=False)]
      flags |= S_FLAG_TIME_DEFAULT
      guesses.append(
        {
          'zone_id': zid,
          'school': name,
          'field': ','.join(missing),
          'raw': None,
          'read_as': 'standard 08:00-09:30 / 14:30-16:00',
          'note': 'time field(s) missing; TfNSW standard school-zone times assumed',
        }
      )
    am0, am1, pm0, pm1 = times
    if not (5 * 60 <= am0 < am1 <= pm0 < pm1 <= 19 * 60):
      flags |= S_FLAG_WINDOW_ODD
      guesses.append(
        {
          'zone_id': zid,
          'school': name,
          'field': 'windows',
          'raw': [r.get(k) for k in ('START_TIME_AM', 'END_TIME_AM', 'START_TIME_PM', 'END_TIME_PM')],
          'read_as': ['{:02d}:{:02d}'.format(*divmod(v, 60)) for v in times],
          'note': 'windows fail sanity check (order or outside 05:00-19:00); kept as read',
        }
      )
    opcal = r.get('OP_CAL') or ''
    if opcal not in opcal_variants:
      opcal_variants[opcal] = len(opcal_variants)
    g = r.get('json_geometry')
    if isinstance(g, str):
      g = ast.literal_eval(g)
    if not g or g.get('type') not in ('Polygon', 'MultiPolygon'):
      gates.append(f'school {zid}: geometry {None if not g else g.get("type")}')
      continue
    polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']
    rings = []
    for poly in polys:
      for ring in poly:
        a = np.asarray(ring, np.float64)
        rings.append((np.round(a[:, 1] * 1e6).astype(np.int32), np.round(a[:, 0] * 1e6).astype(np.int32)))
    allat = np.concatenate([q[0] for q in rings])
    allon = np.concatenate([q[1] for q in rings])
    clat, clon = float(allat.mean()) * 1e-6, float(allon.mean()) * 1e-6
    d = r.get('SZ_DIRECTION')
    if d not in DIR_CODE:
      gates.append(f'school {zid}: unknown SZ_DIRECTION {d!r}')
      d = None
    try:
      zid_i = int(zid)
    except (TypeError, ValueError):
      gates.append(f'school: non-integer SZ_ZONE_ID {zid!r}')
      zid_i = -1
    rows.append(
      {
        'zone_id': zid_i,
        'name': name,
        'speed': sp,
        'times': times,
        'late': 1 if r.get('LATE_OPENING_SCHOOL') == 'Y' else 0,
        'tz': school_tz(clat, clon),
        'dir': DIR_CODE[d],
        'flags': flags,
        'opcal': opcal_variants[opcal],
        'rings': rings,
        'bbox': (int(allat.min()), int(allon.min()), int(allat.max()), int(allon.max())),
      }
    )
  ids = [r['zone_id'] for r in rows]
  if len(set(ids)) != len(ids):
    log(f'WARNING: {len(ids) - len(set(ids))} duplicate SZ_ZONE_IDs')
  # OP_CAL variants
  V = len(opcal_variants)
  opcal_s = np.full((V, 8), -1, np.int32)
  opcal_e = np.full((V, 8), -1, np.int32)
  opcal_desc = []
  for s, i in opcal_variants.items():
    ranges = _OPCAL_RE.findall(s)
    opcal_desc.append({'index': i, 'records': sum(1 for r in rows if r['opcal'] == i), 'ranges': [(a, b, rec) for a, b, rec in ranges]})
    for j, (a, b, _rec) in enumerate(ranges[:8]):
      opcal_s[i, j] = (dt.date.fromisoformat(a) - dt.date(1970, 1, 1)).days
      opcal_e[i, j] = (dt.date.fromisoformat(b) - dt.date(1970, 1, 1)).days
  return rows, guesses, opcal_s, opcal_e, opcal_desc


def join_school_lines(o, rows, log):
  """For every overlay line of type School, pick the school polygon it lies in."""
  n = len(rows)
  bb = np.array([r['bbox'] for r in rows], np.int64).reshape(-1, 4)
  pad = 400  # ~40 m in micro-degrees
  P = len(o['part_speed'])
  part_school = np.full(P, -1, np.int16)
  how = {'inside': 0, 'near': 0, 'none': 0, 'not_school': 0, 'speed_mismatch': 0}
  unmatched = []
  school_has_line = np.zeros(n, bool)
  for k in range(P):
    if o['part_type'][k] != TYPE_CODE['School']:
      how['not_school'] += 1
      continue
    a, b = o['part_v0'][k], o['part_v0'][k + 1]
    la, lo = o['vlat'][a:b], o['vlon'][a:b]
    cand = np.nonzero((bb[:, 0] - pad <= la.max()) & (bb[:, 2] + pad >= la.min()) & (bb[:, 1] - pad <= lo.max()) & (bb[:, 3] + pad >= lo.min()))[0]
    best, best_in, best_d = -1, 0, np.inf
    # densify the line a little so a short line whose vertices sit on the polygon edge still counts
    t = np.linspace(0, 1, 5)[:-1]
    dla = (la[:-1, None] + (la[1:] - la[:-1])[:, None] * t[None, :]).ravel()
    dlo = (lo[:-1, None] + (lo[1:] - lo[:-1])[:, None] * t[None, :]).ravel()
    dla = np.append(dla, la[-1])
    dlo = np.append(dlo, lo[-1])
    for c in cand:
      inside = int(point_in_rings(dla, dlo, rows[c]['rings']).sum())
      if inside > best_in:
        best, best_in = c, inside
    if best >= 0:
      how['inside'] += 1
    else:
      for c in cand:
        d = float(dist_points_to_rings_m(dla, dlo, rows[c]['rings']).min())
        if d < best_d:
          best, best_d = c, d
      if best >= 0 and best_d <= 30.0:
        how['near'] += 1
      else:
        best = -1
        how['none'] += 1
        unmatched.append(
          {
            'part': int(k),
            'feature': int(o['part_feat'][k]),
            'lat': round(float(la.mean()) * 1e-6, 5),
            'lon': round(float(lo.mean()) * 1e-6, 5),
            'speed': int(o['part_speed'][k]),
            'nearest_polygon_m': None if not np.isfinite(best_d) else round(best_d, 1),
          }
        )
    if best >= 0:
      part_school[k] = best
      school_has_line[best] = True
      if rows[best]['speed'] != int(o['part_speed'][k]):
        how['speed_mismatch'] += 1
  for i, r in enumerate(rows):
    if not school_has_line[i]:
      r['flags'] |= S_FLAG_NO_LINE
  orphans = [{'zone_id': r['zone_id'], 'school': r['name']} for i, r in enumerate(rows) if not school_has_line[i]]
  return part_school, how, unmatched, orphans


def school_arrays(rows, opcal_s, opcal_e):
  n = len(rows)
  names = [r['name'].encode('utf-8') for r in rows]
  off = np.zeros(n + 1, np.int32)
  off[1:] = np.cumsum([len(b) for b in names])
  blob = np.frombuffer(b''.join(names), np.uint8).copy()
  ring0 = [0]
  ring_v0 = [0]
  rlat, rlon = [], []
  for r in rows:
    for la, lo in r['rings']:
      rlat.append(la)
      rlon.append(lo)
      ring_v0.append(ring_v0[-1] + len(la))
    ring0.append(len(ring_v0) - 1)
  t = np.array([r['times'] for r in rows], np.int16).reshape(-1, 4)
  return {
    's_zone_id': np.array([r['zone_id'] for r in rows], np.int64),
    's_speed': np.array([r['speed'] for r in rows], np.uint8),
    's_am0': t[:, 0],
    's_am1': t[:, 1],
    's_pm0': t[:, 2],
    's_pm1': t[:, 3],
    's_late': np.array([r['late'] for r in rows], np.uint8),
    's_tz': np.array([r['tz'] for r in rows], np.uint8),
    's_dir': np.array([r['dir'] for r in rows], np.uint8),
    's_flags': np.array([r['flags'] for r in rows], np.uint16),
    's_opcal': np.array([r['opcal'] for r in rows], np.uint8),
    's_name_blob': blob,
    's_name_off': off,
    's_bbox': np.array([r['bbox'] for r in rows], np.int32).reshape(-1, 4),
    's_ring0': np.array(ring0, np.int32),
    's_ring_v0': np.array(ring_v0, np.int32),
    's_rlat': np.concatenate(rlat) if rlat else np.zeros(0, np.int32),
    's_rlon': np.concatenate(rlon) if rlon else np.zeros(0, np.int32),
    'opcal_start': opcal_s,
    'opcal_end': opcal_e,
  }


# ----------------------------------------------------------------------------- transport encoding
def encode_for_transport(arrays):
  """
  Smaller file, same information. Reversed by nsw_zones.load_index():
    {p}vlat/{p}vlon  -> {p}vlat_d/{p}vlon_d   int32 running delta (np.cumsum restores it exactly)
    {p}part_v0       -> {p}part_len          vertices per part (uint16 if it fits)
    {p}grid_keys     -> {p}grid_keys_d       int32 running delta
    {p}grid_start    -> {p}grid_count        uint8/uint16 items per cell
    {p}chunk_*       -> dropped; derived from part_len and meta.chunk at load
  """
  out = {}
  for k, v in arrays.items():
    p, _, name = k.partition('_')
    if p in ('b', 'o') and name in ('vlat', 'vlon', 'grid_keys'):
      d = np.empty_like(v)
      if len(v):
        d[0] = v[0]
        np.subtract(v[1:], v[:-1], out=d[1:])
      out[k + '_d'] = d
    elif p in ('b', 'o') and name == 'part_v0':
      n = np.diff(v)
      out[p + '_part_len'] = n.astype(np.uint16) if n.size == 0 or n.max() < 65536 else n.astype(np.int32)
    elif p in ('b', 'o') and name == 'grid_start':
      n = np.diff(v)
      out[p + '_grid_count'] = n.astype(np.uint8) if n.size == 0 or n.max() < 256 else n.astype(np.uint16)
    elif p in ('b', 'o') and name.startswith('chunk_'):
      continue
    else:
      out[k] = v
  return out


# ----------------------------------------------------------------------------- build
def build_arrays(feats, recs, simplify_m=1.0, log=print, data_version=None, calendar_doc=None):
  """GeoJSON features (list of dicts) + SchoolZones records -> everything the index file holds.
  Used by main() on the real data and by the unit tests on synthetic data. data_version: the string the device
  shows (default: today's UTC date). calendar_doc: school_days.build_json() output (default: built here)."""
  gates = []  # hard failures
  warnings = []
  from collections import Counter

  cnt_type, cnt_dir, cnt_status, cnt_speed, cnt_geom = Counter(), Counter(), Counter(), Counter(), Counter()
  mismatch_sz = 0
  null_geom = 0
  base = LayerBuilder('base')
  over = LayerBuilder('overlay')
  t = time.monotonic()
  for fi, f in enumerate(feats):
    p = f.get('properties') or {}
    typ, st, d, sps = p.get('Type'), p.get('Status'), p.get('Direction'), p.get('Speed')
    for k, v in (('sz_type', typ), ('sz_status', st), ('sz_direction', d), ('sz_speed', sps)):
      if k in p and p[k] != v:
        mismatch_sz += 1
        break
    cnt_type[typ] += 1
    cnt_dir[d] += 1
    cnt_status[st] += 1
    cnt_speed[sps] += 1
    g = f.get('geometry')
    if g is None:
      null_geom += 1
      continue
    cnt_geom[g.get('type')] += 1
    if typ not in TYPE_CODE:
      gates.append(f'feature {fi}: unknown Type {typ!r}')
      continue
    if d not in DIR_CODE:
      gates.append(f'feature {fi}: unknown Direction {d!r}')
      continue
    if st not in STATUSES:
      gates.append(f'feature {fi}: unknown Status {st!r}')
      continue
    sp = parse_speed(sps)
    if sp is None or not (5 <= sp <= 130):
      gates.append(f'feature {fi}: bad Speed {sps!r}')
      continue
    if g['type'] == 'LineString':
      parts = [g['coordinates']]
    elif g['type'] == 'MultiLineString':
      parts = g['coordinates']
    else:
      gates.append(f'feature {fi}: geometry {g["type"]}')
      continue
    L = over if typ in OVERLAY_TYPES else base
    for coords in parts:
      L.add(coords, sp, TYPE_CODE[typ], DIR_CODE[d], fi, simplify_m)
  log(f'parsed + simplified in {time.monotonic() - t:.1f} s: base {len(base.lat)} parts, overlay {len(over.lat)} parts')
  if mismatch_sz:
    warnings.append(f'{mismatch_sz} features whose sz_* duplicate differs from the main property')
  if len(gates) > 50:
    gates = gates[:50] + ['... and more']

  # grid frame from the data (+ margin), micro-degrees
  alllat = np.concatenate(base.lat + over.lat)
  alllon = np.concatenate(base.lon + over.lon)
  lat0 = (int(alllat.min()) // GRID_DLAT_E6 - 2) * GRID_DLAT_E6
  lon0 = (int(alllon.min()) // GRID_DLON_E6 - 2) * GRID_DLON_E6
  nrow = (int(alllat.max()) - lat0) // GRID_DLAT_E6 + 3
  ncol = (int(alllon.max()) - lon0) // GRID_DLON_E6 + 3
  assert nrow * ncol < 2**31
  bbox = [int(alllat.min()), int(alllon.min()), int(alllat.max()), int(alllon.max())]
  del alllat, alllon
  grid = {'lat0_e6': lat0, 'lon0_e6': lon0, 'dlat_e6': GRID_DLAT_E6, 'dlon_e6': GRID_DLON_E6, 'nrow': nrow, 'ncol': ncol, 'pad_m': GRID_SAMPLE_M / 2.0}

  t = time.monotonic()
  b_arr, b_stats = base.finish(grid)
  o_arr, o_stats = over.finish(grid)
  log(f'layers + grid in {time.monotonic() - t:.1f} s')
  log(f'  base    {b_stats}')
  log(f'  overlay {o_stats}')

  # ---- schools
  t = time.monotonic()
  rows, guesses, opcal_s, opcal_e, opcal_desc = build_schools(recs, gates, log)
  part_school, join_how, unmatched_lines, orphans = join_school_lines(o_arr, rows, log)
  o_arr['part_school'] = part_school
  s_arr = school_arrays(rows, opcal_s, opcal_e)
  log(
    f'schools: {len(rows)} records, {len(guesses)} time interpretations, join {join_how}, {len(orphans)} polygons without a line, '
    + f'in {time.monotonic() - t:.1f} s'
  )

  meta = {
    'format_version': FORMAT_VERSION,
    'attribution': ATTRIBUTION,
    'attribution_long': ATTRIBUTION_LONG,
    'data_version': data_version or dt.datetime.now(dt.UTC).strftime('%Y-%m-%d'),
    'types': TYPES,
    'overlay_types': sorted(OVERLAY_TYPES),
    'dirs': ['Both Directions', 'One Way', 'None'],
    'tz': ['Australia/Sydney', 'Australia/Broken_Hill', 'Australia/Lord_Howe'],
    'grid': grid,
    'chunk': CHUNK,
    'simplify_m': simplify_m,
    'coord_scale': 1e-6,
    'bbox_e6': bbox,
    'school_flags': {'TIME_GUESSED': S_FLAG_TIME_GUESSED, 'TIME_DEFAULT': S_FLAG_TIME_DEFAULT, 'NO_LINE': S_FLAG_NO_LINE, 'WINDOW_ODD': S_FLAG_WINDOW_ODD},
    'std_times_min': STD_TIMES,
    'counts': {'base': b_stats, 'overlay': o_stats, 'schools': len(rows)},
  }
  if calendar_doc is None:
    calendar_doc = school_days.build_json()
  cal_small = {k: v for k, v in calendar_doc.items() if k not in ('source_strings', 'sources', 'op_cal_audit')}
  arrays = {
    'meta_json': np.frombuffer(json.dumps(meta).encode(), np.uint8).copy(),
    'attribution': np.array(ATTRIBUTION),
    'calendar_json': np.frombuffer(json.dumps(cal_small, ensure_ascii=False).encode('utf-8'), np.uint8).copy(),
  }
  arrays.update({'b_' + k: v for k, v in b_arr.items()})
  arrays.update({'o_' + k: v for k, v in o_arr.items()})
  arrays.update(s_arr)

  return {
    'arrays': arrays,
    'meta': meta,
    'gates': gates,
    'warnings': warnings,
    'b_stats': b_stats,
    'o_stats': o_stats,
    'counts': {'type': cnt_type, 'dir': cnt_dir, 'status': cnt_status, 'geom': cnt_geom, 'speed': cnt_speed},
    'null_geom': null_geom,
    'schools': {'rows': rows, 'guesses': guesses, 'opcal_desc': opcal_desc, 'join_how': join_how, 'unmatched_lines': unmatched_lines, 'orphans': orphans},
  }


# ----------------------------------------------------------------------------- writing
def write_index(arrays, path):
  """Write the transport (deflate, delta-coded) file atomically, with fixed member timestamps."""
  nz_index.write_npz(encode_for_transport(arrays), path, compress=True)


# ----------------------------------------------------------------------------- gates
def calendar_gate(today, days=30):
  """-> (ok, coverage dict, message). The school calendar must cover today + `days` for both divisions."""
  try:
    cal = school_days.build_calendar()
  except Exception as e:
    return False, {}, f'school calendar does not build: {e!r}'
  cov = {k: (a.isoformat(), b.isoformat()) for k, (a, b) in cal.coverage.items()}
  need = today + dt.timedelta(days=days)
  short = [k for k, (a, b) in cal.coverage.items() if not (a <= today and need <= b)]
  if short:
    return False, cov, f'school calendar ends {min(cal.coverage[k][1] for k in short)}, before {need} (today + {days} d): update school_days.py'
  return True, cov, f'school calendar covers {today} .. {need}'


def feature_count_gate(count, previous_manifest, tol=0.05):
  """-> (ok, message, previous count or None)."""
  if not previous_manifest:
    return True, 'no previous manifest given: feature-count check skipped', None
  try:
    with open(previous_manifest, encoding='utf-8') as f:
      prev = json.load(f)
    pc = int(prev['feature_count'])
  except (OSError, ValueError, KeyError, TypeError) as e:
    return False, f'previous manifest unreadable ({e!r})', None
  if pc <= 0:
    return False, f'previous manifest has feature_count {pc}', pc
  change = (count - pc) / pc
  if abs(change) > tol:
    return False, f'feature count {count} differs from the previous build ({pc}) by {change * 100:+.1f} % (limit +-{tol * 100:.0f} %)', pc
  return True, f'feature count {count} vs previous {pc} ({change * 100:+.2f} %)', pc


REVIEW = 'REVIEW: '  # a gate a person must look at: fails the automatic weekly publish, passes with --allow-review
REVIEW_TOL = 0.05  # schools and School lines within +-5 % of the previous release
REVIEW_RAISED_KM = 20.0  # base lines whose limit went UP: more than this many km ...
REVIEW_RAISED_MOTORWAY_KPH = 90  # ... or any at all ending at this or more (SLA follows a new limit >= 80 by itself)
REVIEW_NEW_FAST_KM = 50.0  # new or re-drawn base lines of REVIEW_RAISED_MOTORWAY_KPH or more


def review_gates(counts, previous_manifest, tol=REVIEW_TOL):
  """Checks against the previous release's manifest for changes that could RAISE a published limit or drop school
  zones - the ones a +-5 % feature-count gate on 447k features cannot see. -> (failures, notes).
  counts: this build's manifest 'counts' ({'schools', 'types': {name: features}})."""
  if not previous_manifest:
    return [], ['no previous manifest given: the review gates (schools, School lines, Variable lines) are skipped']
  try:
    with open(previous_manifest, encoding='utf-8') as f:
      prev = json.load(f)
  except (OSError, ValueError) as e:
    return [f'{REVIEW}previous manifest unreadable ({e!r})'], []
  pc = prev.get('counts') if isinstance(prev, dict) else None
  if not isinstance(pc, dict):
    return [], ['previous manifest has no counts (an older builder): the review gates are skipped']
  pt = pc.get('types') or {}
  fails, notes = [], []
  nt = counts.get('types') or {}
  for label, old, new in (('school zones', pc.get('schools'), counts.get('schools')), ('School lines', pt.get('School'), nt.get('School', 0))):
    if not isinstance(old, int) or old <= 0 or not isinstance(new, int):
      notes.append(f'{label}: nothing to compare ({old!r} -> {new!r})')
      continue
    ch = (new - old) / old
    msg = f'{label} {old} -> {new} ({ch * 100:+.1f} %)'
    if abs(ch) > tol:
      fails.append(f'{REVIEW}{msg}, more than +-{tol * 100:.0f} %')
    else:
      notes.append(msg)
  ov, nv = pt.get('Variable', 0), nt.get('Variable', 0)
  if ov != nv:
    # every new Variable line drawn over a lower static zone RAISES the published limit (the owner's rule)
    fails.append(f'{REVIEW}Variable lines {ov} -> {nv}: each one on a static zone publishes the higher value; check them')
  else:
    notes.append(f'Variable lines unchanged ({nv})')
  return fails, notes


def _base_parts(A):
  """Per base part: identity key (end points, vertex count, type, direction), limit, length in m."""
  pv0 = np.asarray(A['b_part_v0'], np.int64)
  vlat = np.asarray(A['b_vlat'], np.int64)
  vlon = np.asarray(A['b_vlon'], np.int64)
  a, b = pv0[:-1], pv0[1:] - 1
  key = np.stack([vlat[a], vlon[a], vlat[b], vlon[b], b - a + 1, np.asarray(A['b_part_type'], np.int64), np.asarray(A['b_part_dir'], np.int64)], axis=1)
  kx = M_PER_DEG_LON_EQ * np.cos(np.radians(vlat[:-1] * 1e-6)) * 1e-6
  seg = np.hypot(np.diff(vlon) * kx, np.diff(vlat) * 1e-6 * M_PER_DEG_LAT)
  seg = np.append(seg, 0.0)
  seg[b] = 0.0  # no segment from a part's last vertex to the next part's first
  length = np.add.reduceat(seg, a) if len(a) else np.zeros(0)
  return key, np.asarray(A['b_part_speed'], np.int64), length


def limit_diff(prev_arrays, new_arrays):
  """Which base lines' limits went UP since the previous index. A line is the same line when its end points, vertex
  count, type and direction are identical (the builder is deterministic, so unchanged source features give identical
  parts). -> report dict: raised_km, raised_parts, raised_fast_km (new limit >= REVIEW_RAISED_MOTORWAY_KPH),
  new_km / new_fast_km (lines with no identical predecessor), examples."""
  ok, sk, lk = _base_parts(prev_arrays)
  nk, sn, ln = _base_parts(new_arrays)
  keys_all = np.concatenate([ok, nk])
  _, first, inv = np.unique(keys_all, axis=0, return_index=True, return_inverse=True)
  inv = inv.ravel()
  io, inn = inv[: len(ok)], inv[len(ok) :]
  K = len(first)
  # per identical line (TfNSW has a few drawn twice with different limits): the highest and lowest limit, before/after
  omax, omin = np.full(K, -1, np.int64), np.full(K, 1 << 30, np.int64)
  nmax, nmin = np.full(K, -1, np.int64), np.full(K, 1 << 30, np.int64)
  np.maximum.at(omax, io, sk)
  np.minimum.at(omin, io, sk)
  np.maximum.at(nmax, inn, sn)
  np.minimum.at(nmin, inn, sn)
  length = np.concatenate([lk, ln])[first]
  ktype = keys_all[first, 5]
  both = (omax >= 0) & (nmax >= 0)
  raised = both & (nmax > omax)
  lowered = both & (nmin < omin)
  new = (omax < 0) & (nmax >= 0)
  fast = nmax >= REVIEW_RAISED_MOTORWAY_KPH
  names = TYPES
  ex = []
  for i in np.nonzero(raised)[0][np.argsort(-length[raised])][:10]:
    ex.append(
      {
        'type': names[int(ktype[i])] if int(ktype[i]) < len(names) else int(ktype[i]),
        'from': int(omax[i]),
        'to': int(nmax[i]),
        'km': round(float(length[i]) / 1000.0, 3),
        'lat': round(int(keys_all[first[i], 0]) * 1e-6, 5),
        'lon': round(int(keys_all[first[i], 1]) * 1e-6, 5),
      }
    )
  ln = length
  return {
    'raised_parts': int(raised.sum()),
    'raised_km': round(float(ln[raised].sum()) / 1000.0, 3),
    'raised_fast_km': round(float(ln[raised & fast].sum()) / 1000.0, 3),
    'new_parts': int(new.sum()),
    'new_km': round(float(ln[new].sum()) / 1000.0, 3),
    'new_fast_km': round(float(ln[new & fast].sum()) / 1000.0, 3),
    'lowered_km': round(float(ln[lowered].sum()) / 1000.0, 3),
    'raised_examples': ex,
  }


def limit_raise_gates(prev_index, new_arrays):
  """-> (failures, report). Loads the previous release's index (--previous-index) and applies the REVIEW_* limits."""
  if not prev_index:
    return [], {'skipped': 'no previous index given'}
  try:
    prev = nz_index.load_index(prev_index)
  except (nz_index.IndexInvalid, OSError, ValueError) as e:
    return [f'{REVIEW}previous index unreadable, raised limits cannot be checked ({e})'], {'error': str(e)}
  rep = limit_diff(prev, new_arrays)
  fails = []
  if rep['raised_km'] > REVIEW_RAISED_KM:
    fails.append(f"{REVIEW}{rep['raised_km']:.1f} km of speed-zone lines now have a HIGHER limit (> {REVIEW_RAISED_KM:.0f} km)")
  if rep['raised_fast_km'] > 0:
    fails.append(f"{REVIEW}{rep['raised_fast_km']:.2f} km of lines were raised to {REVIEW_RAISED_MOTORWAY_KPH}+ km/h")
  if rep['new_fast_km'] > REVIEW_NEW_FAST_KM:
    fails.append(f"{REVIEW}{rep['new_fast_km']:.1f} km of new or re-drawn {REVIEW_RAISED_MOTORWAY_KPH}+ km/h lines (> {REVIEW_NEW_FAST_KM:.0f} km)")
  return fails, rep


def self_test(arrays, path, n=300, seed=20260929, log=print):
  """The written file must decode to exactly `arrays`, and the matcher must publish the right limit on random
  lines. -> (ok, report dict)."""
  rep = {}
  back = nz_index.load_index(path)
  bad = [k for k, v in arrays.items() if k not in back or back[k].dtype != v.dtype or not np.array_equal(back[k], v)]
  rep['round_trip_mismatch'] = bad
  if bad:
    return False, rep
  matcher = _sibling('matcher')
  m = matcher.Matcher(back, calendar=(lambda d, division=None: False, 'self-test: no school days'))
  L = m.L['b']
  vlat, vlon, pv0 = L['vlat'], L['vlon'], L['part_v0']
  P = len(pv0) - 1
  seg_part = np.repeat(np.arange(P), np.diff(pv0))  # vertex -> part
  last_v = np.zeros(len(vlat), bool)
  last_v[pv0[1:] - 1] = True
  cand = np.nonzero(~last_v)[0]
  la0, lo0 = vlat[cand].astype(np.float64), vlon[cand].astype(np.float64)
  la1, lo1 = vlat[cand + 1].astype(np.float64), vlon[cand + 1].astype(np.float64)
  kx = 1e-6 * matcher.M_PER_DEG_LON_EQ * np.cos(np.radians(la0 * 1e-6))
  seglen = np.hypot((lo1 - lo0) * kx, (la1 - la0) * 1e-6 * matcher.M_PER_DEG_LAT)
  cand = cand[seglen >= 20.0]
  rng = np.random.default_rng(seed)
  pick = rng.choice(cand, size=min(n, len(cand)), replace=False) if len(cand) else []
  counts = {'exact': 0, 'colocated': 0, 'withheld': 0, 'wrong': 0, 'no_match': 0}
  wrong = []
  for v in pick:
    v = int(v)
    q = int(seg_part[v])
    lat_e6 = (float(vlat[v]) + float(vlat[v + 1])) * 0.5
    lon_e6 = (float(vlon[v]) + float(vlon[v + 1])) * 0.5
    kxx = matcher.M_PER_DEG_LON_EQ * math.cos(math.radians(lat_e6 * 1e-6))
    ux = (float(vlon[v + 1]) - float(vlon[v])) * 1e-6 * kxx
    uy = (float(vlat[v + 1]) - float(vlat[v])) * 1e-6 * matcher.M_PER_DEG_LAT
    brg = math.degrees(math.atan2(ux, uy)) % 360
    m.reset()
    r = m.update(lat_e6 * 1e-6, lon_e6 * 1e-6, brg, 15.0, True, 3.0, 1790000000.0, mono_time=0.0, lookahead=False)
    own = int(L['part_speed'][q])
    if r['limit_kph'] is None:
      counts['no_match' if r['state'] == 'no_match' else 'withheld'] += 1
      continue
    if r['limit_kph'] == own:
      counts['exact'] += 1
      continue
    n_ = max(math.hypot(ux, uy), 1e-9)
    eff, _ = m._effective_limit(q, lat_e6, lon_e6, ux / n_, uy / n_)
    # a line of another limit drawn on top of this one (the Variable rule), or lying within the matcher's noise
    seg = m._segments(L, int(round(lat_e6)), int(round(lon_e6)), 3.0)
    near = set()
    if seg is not None:
      sv, sp, ax, ay, bx, by, _, _ = seg
      d = m._project(ax, ay, bx, by)[-1]
      near = {int(L['part_speed'][int(sp[i])]) for i in np.nonzero(d <= 3.0)[0]}
    if r['limit_kph'] == eff or r['limit_kph'] in near:
      counts['colocated'] += 1
    else:
      counts['wrong'] += 1
      if len(wrong) < 10:
        wrong.append({'part': q, 'own': own, 'published': r['limit_kph'], 'state': r['state']})
  tot = max(len(pick), 1)
  good = (counts['exact'] + counts['colocated']) / tot
  rep.update(points=len(pick), counts=counts, good_share=round(good, 4), wrong_share=round(counts['wrong'] / tot, 4), wrong_examples=wrong)
  ok = good >= 0.85 and counts['wrong'] / tot <= 0.05
  log(f'self-test: {counts} -> {good * 100:.1f} % right, {counts["wrong"] / tot * 100:.1f} % wrong ({"PASS" if ok else "FAIL"})')
  return ok, rep


# ----------------------------------------------------------------------------- main
def _src_info(path):
  st = os.stat(path)
  return {'file': os.path.basename(path), 'bytes': st.st_size, 'sha256': sha256_file(path)}


def load_features(path):
  """The GeoJSON FeatureCollection's features (the Action downloads the GeoJSON resource)."""
  with open(path, 'rb') as f:
    gj = json.load(f)
  if not isinstance(gj, dict) or gj.get('type') != 'FeatureCollection' or not isinstance(gj.get('features'), list):
    raise SystemExit(f'{path}: not a GeoJSON FeatureCollection')
  return gj['features']


ATTRIBUTION_TXT = (
  ATTRIBUTION_LONG
  + '\n\n'
  + 'Source: Transport for NSW Open Data Hub - Speed Zones (incl. the school zones resource).\n'
  + 'Licence: Creative Commons Attribution 4.0 International, https://creativecommons.org/licenses/by/4.0/\n'
  + 'Term dates (c) State of New South Wales (Department of Education), CC BY 4.0. '
  + 'Public holidays (c) State of New South Wales, CC BY 4.0 - www.nsw.gov.au.\n'
)


def _write_json(path, obj):
  with open(path + '.tmp', 'w', encoding='utf-8', newline='\n') as f:
    json.dump(obj, f, indent=1, ensure_ascii=False)
    f.write('\n')
  os.replace(path + '.tmp', path)


def main(argv=None):
  ap = argparse.ArgumentParser(description='Build the NSW speed-zone index (nsw_zones.npz + manifest.json + ATTRIBUTION.txt).')
  ap.add_argument('--speedzones', required=True, help='TfNSW Speed Zones GeoJSON (FeatureCollection, WGS84)')
  ap.add_argument('--schoolzones', required=True, help='TfNSW schoolzones.zip (SchoolZones.json inside)')
  ap.add_argument('--out', required=True, help='output directory')
  ap.add_argument('--previous-manifest', default=None, help='manifest.json of the previous release (feature count within +-5 %%)')
  ap.add_argument('--previous-index', default=None, help='nsw_zones.npz of the previous release (the raised-limits review gate)')
  ap.add_argument(
    '--allow-review', action='store_true', help='REVIEW gates (raised limits, Variable lines, school counts) warn instead of failing: a person checked them'
  )
  ap.add_argument('--simplify-m', type=float, default=1.0, help='Douglas-Peucker tolerance, meters (0 = off)')
  ap.add_argument('--data-version', default=None, help='version string shown on the device (default: today, UTC)')
  ap.add_argument('--today', default=None, help='YYYY-MM-DD for the calendar gate (default: today, UTC)')
  ap.add_argument('--stored', action='store_true', help='also write nsw_zones_stored.npz (uncompressed, mmap-able)')
  ap.add_argument('--self-test-points', type=int, default=300)
  ap.add_argument('--min-base-parts', type=int, default=400000, help='size gate (lower it only for test data)')
  ap.add_argument('--min-schools', type=int, default=1000, help='size gate (lower it only for test data)')
  ap.add_argument('--allow-gate-failures', action='store_true', help='write the files anyway (never in CI)')
  a = ap.parse_args(argv)

  T0 = time.monotonic()
  msgs = []

  def log(s):
    print(s, flush=True)
    msgs.append(s)

  now = dt.datetime.now(dt.UTC)
  today = dt.date.fromisoformat(a.today) if a.today else now.date()
  data_version = a.data_version or now.strftime('%Y-%m-%d')
  os.makedirs(a.out, exist_ok=True)
  gates, warnings = [], []

  t = time.monotonic()
  feats = load_features(a.speedzones)
  n_features = len(feats)
  log(f'loaded {n_features} features in {time.monotonic() - t:.1f} s')
  recs, zinfo = load_schools(a.schoolzones, log)
  cal_doc = school_days.build_json(a.schoolzones)
  B = build_arrays(feats, recs, a.simplify_m, log, data_version=data_version, calendar_doc=cal_doc)
  del feats
  arrays = B['arrays']
  gates += B['gates']
  warnings += B['warnings']
  rows = B['schools']['rows']

  # ---- gates on the data
  if len(arrays['b_part_speed']) < a.min_base_parts:
    gates.append(f'only {len(arrays["b_part_speed"])} base parts (expected >= {a.min_base_parts})')
  if not (a.min_schools <= len(rows) <= 20000):
    gates.append(f'{len(rows)} school records (expected {a.min_schools}..20,000)')
  ok_fc, msg_fc, prev_count = feature_count_gate(n_features, a.previous_manifest)
  log(msg_fc)
  if not ok_fc:
    gates.append(msg_fc)
  elif prev_count is None:
    warnings.append(msg_fc)
  ok_cal, cal_cov, msg_cal = calendar_gate(today)
  log(msg_cal)
  if not ok_cal:
    gates.append(msg_cal)
  # changes that could RAISE a published limit or drop school zones: a person looks before they reach the cars
  counts_now = {'schools': len(rows), 'types': {str(k): v for k, v in B['counts']['type'].items()}}
  rv_fails, rv_notes = review_gates(counts_now, a.previous_manifest)
  lr_fails, lr_rep = limit_raise_gates(a.previous_index, arrays)
  for m_ in rv_notes:
    log(m_)
  if 'raised_km' in lr_rep:
    log(
      f"limits vs the previous index: {lr_rep['raised_km']} km raised ({lr_rep['raised_fast_km']} km to "
      + f"{REVIEW_RAISED_MOTORWAY_KPH}+), {lr_rep['lowered_km']} km lowered, {lr_rep['new_km']} km new or re-drawn"
    )
  elif not a.previous_index:
    warnings.append('no previous index given: the raised-limits review gate is skipped')
  for g in rv_fails + lr_fails:
    (warnings if a.allow_review else gates).append(g)
  review = {'failures': rv_fails + lr_fails, 'notes': rv_notes, 'limit_diff': lr_rep, 'allowed_by_hand': bool(a.allow_review)}

  # ---- write under a temporary name, self-test, then publish
  npz = os.path.join(a.out, nz_index.INDEX_NAME)
  tmp = npz + '.new'
  t = time.monotonic()
  write_index(arrays, tmp)
  log(f'wrote {os.path.getsize(tmp) / 1e6:.2f} MB (deflate, delta-coded) in {time.monotonic() - t:.1f} s')
  try:
    ok_st, st_rep = self_test(arrays, tmp, n=a.self_test_points, log=log)
  except Exception as e:  # a crash in the self-test is a failed gate, not a pass
    ok_st, st_rep = False, {'error': repr(e)}
  if not ok_st:
    gates.append(f'self-test failed: {st_rep}')

  report = {
    'data_version': data_version,
    'built_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
    'gates': {'passed': not gates, 'failures': gates, 'warnings': warnings},
    'self_test': st_rep,
    'review': review,
    'feature_counts': {
      'type': {str(k): v for k, v in B['counts']['type'].most_common()},
      'direction': {str(k): v for k, v in B['counts']['dir'].most_common()},
      'status': {str(k): v for k, v in B['counts']['status'].most_common()},
      'geometry': {str(k): v for k, v in B['counts']['geom'].most_common()},
      'speed': {str(k): v for k, v in sorted(B['counts']['speed'].items(), key=lambda kv: parse_speed(kv[0]) or 0)},
      'null_geometry': B['null_geom'],
    },
    'layers': {'base': B['b_stats'], 'overlay': B['o_stats']},
    'schools': {
      'records': len(rows),
      'late_opening_Y': int(sum(r['late'] for r in rows)),
      'line_join': B['schools']['join_how'],
      'lines_without_polygon': len(B['schools']['unmatched_lines']),
      'polygons_without_line': B['schools']['orphans'],
      'time_interpretations': B['schools']['guesses'],
      'op_cal_variants': B['schools']['opcal_desc'],
      'op_cal_audit': cal_doc.get('op_cal_audit', {}).get('finding'),
    },
    'log': msgs,
  }

  if gates:
    log('GATE FAILURES:\n  ' + '\n  '.join(str(g) for g in gates))
    report['log'] = msgs
    _write_json(os.path.join(a.out, 'build_report.json'), report)
    if not a.allow_gate_failures:
      os.remove(tmp)
      return 2

  os.replace(tmp, npz)
  sha = sha256_file(npz)
  manifest = {
    'format_version': FORMAT_VERSION,
    'data_version': data_version,
    'sha256': sha,
    'bytes': os.path.getsize(npz),
    'file': nz_index.INDEX_NAME,
    'built_utc': report['built_utc'],
    'attribution': ATTRIBUTION_LONG,
    'licence': 'CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)',
    'feature_count': n_features,
    'counts': {
      'base_parts': int(len(arrays['b_part_speed'])),
      'overlay_parts': int(len(arrays['o_part_speed'])),
      'schools': len(rows),
      'types': report['feature_counts']['type'],
    },
    'calendar_coverage': {k: {'first': v[0], 'last': v[1]} for k, v in cal_cov.items()},
    'sources': {
      'speed_zones': dict(_src_info(a.speedzones), features=n_features),
      'school_zones': dict(_src_info(a.schoolzones), records=len(recs), generated=zinfo.get('generated')),
    },
    'parameters': {'simplify_m': a.simplify_m, 'chunk_segments': CHUNK},
    'gates': {'passed': not gates, 'failures': gates, 'warnings': warnings, 'self_test_good_share': st_rep.get('good_share')},
    'review': {k: lr_rep.get(k) for k in ('raised_km', 'raised_fast_km', 'lowered_km', 'new_km', 'new_fast_km')}
    | {'flagged': rv_fails + lr_fails, 'allowed_by_hand': bool(a.allow_review)},
    'builder': 'openpilot/sunnypilot/mapd/nsw_zones/build_index.py',
  }
  _write_json(os.path.join(a.out, nz_index.MANIFEST_NAME), manifest)
  with open(os.path.join(a.out, nz_index.ATTRIBUTION_NAME), 'w', encoding='utf-8', newline='\n') as f:
    f.write(ATTRIBUTION_TXT + f'Data version: {data_version}. Built {report["built_utc"]} by {manifest["builder"]}.\n')
  # the published pair must verify exactly as the device will verify it
  info = nz_index.verify_index(npz, os.path.join(a.out, nz_index.MANIFEST_NAME))
  if a.stored:
    stored = os.path.join(a.out, 'nsw_zones_stored.npz')
    nz_index.write_stored_aligned(arrays, stored)
    log(f'wrote {stored}: {os.path.getsize(stored) / 1e6:.2f} MB (stored, mmap-able)')
  report['output'] = {'bytes': manifest['bytes'], 'sha256': sha, 'verified_data_version': info.data_version}
  report['build_seconds'] = round(time.monotonic() - T0, 1)
  log(f'{npz}: {manifest["bytes"] / 1e6:.2f} MB, sha256 {sha[:16]}..., verified; total {report["build_seconds"]} s')
  report['log'] = msgs
  _write_json(os.path.join(a.out, 'build_report.json'), report)
  return 0 if not gates else 2


if __name__ == '__main__':
  sys.exit(main())
