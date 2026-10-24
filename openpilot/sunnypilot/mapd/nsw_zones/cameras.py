#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-CAMERAS): fixed speed, red-light(-speed) and school-zone cameras from OpenStreetMap, for a DISPLAY-ONLY alert
on the comma 4 HUD ('camera (map)'). Nothing here, nor anything fed by it, changes the car's speed.

Three parts, all pure Python (stdlib; numpy only in CameraHold's caller):

  parse_overpass(doc)  CI / PC: an Overpass answer (OVERPASS_QUERY) -> camera rows, classified and directed. The builder
                       (build_index.py --cameras) snaps them onto TfNSW's base lines and stores them in the index (c_*)
  fetch(out)           CI / PC: runs OVERPASS_QUERY against the public Overpass API and writes the answer. The weekly
                       Action calls `python cameras.py fetch --out FILE`. NEVER on the device: the car never asks a map
                       service anything (it would learn where the car is); it only downloads the finished index
  CameraHold           the device (nsw_map_data.py): the matcher's next camera ahead, held through a short gap

WHAT COUNTS (OSM tags, 2026-10; ~418 highway=speed_camera nodes and ~190 type=enforcement relations in NSW):
  * KIND_RED_LIGHT  a relation with enforcement=traffic_signals (alone or with maxspeed: NSW's red-light speed cameras),
                    or a node whose tags say red light / combo / enforcement or speed_camera = traffic_signals
  * KIND_SCHOOL     name or description saying school, or maxspeed:conditional with a timed 40; the builder also marks a
                    speed camera that sits on a TfNSW School line
  * KIND_SPEED      every other highway=speed_camera node, and enforcement=maxspeed relations
  Left out, and counted in the report: Safe-T-Cam (heavy-vehicle monitoring), average-speed (point-to-point) sites -
  NSW enforces them on heavy vehicles, and on light vehicles only on two trial lengths (Pacific Hwy Kew - Lake Innes,
  Hume Hwy Coolac - Gundagai, since 1 Jul 2025), and OSM does not describe their zones (3 relations, ~40 unpaired
  points), so no zone is shown - mobile-phone and seatbelt cameras, waterway cameras, parking / bus-lane / access
  enforcement. Mobile speed cameras are not in OSM and never shown.

DIRECTION (the traffic the camera checks, a compass bearing, or none = both ways), best source first:
  1 DSRC_RELATION   an enforcement relation's from -> to (the wiki's definition)
  2 DSRC_ONEWAY     the camera is a node of a one-way road (oneway=yes/-1, motorway, roundabout)
  3 DSRC_TAG        direction= on the node: degrees or a cardinal point. On the 2026-10 data it agreed with (1) or (2)
                    within 30 deg for 88 of 107 cameras that have both and was the opposite for 15: the camera's
                    facing, not the traffic's. Kept (a camera without it alerts both ways); the opposite ones are a
                    known miss.
  4 DSRC_WAYDIR     direction=forward/backward on a node of a road, relative to that road's drawing
  0 DSRC_NONE       nothing: both directions of the road it is snapped to
The road's own axis (the bearing of the OSM road the node sits on, either way) is kept too: the builder uses it to
keep a camera off a cross street at a junction.

Speed camera locations (c) OpenStreetMap contributors, available under the Open Database License (ODbL) 1.0,
https://www.openstreetmap.org/copyright. The index that carries them says so (ATTRIBUTION.txt, the manifest, meta).
"""

import json
import math
import os
import re
import sys
import time

OSM_ATTRIBUTION = "Speed camera locations (c) OpenStreetMap contributors, ODbL 1.0 (openstreetmap.org/copyright)."
OSM_LICENCE = "Open Database License (ODbL) 1.0, https://opendatacommons.org/licenses/odbl/1-0/"

KIND_NONE, KIND_SPEED, KIND_RED_LIGHT, KIND_SCHOOL = 0, 1, 2, 3
KIND_NAMES = ('none', 'speed', 'red_light', 'school')

DSRC_NONE, DSRC_RELATION, DSRC_ONEWAY, DSRC_TAG, DSRC_WAYDIR = 0, 1, 2, 3, 4
DSRC_NAMES = ('none', 'relation', 'oneway', 'tag', 'way_dir')

# Overpass QL: NSW's speed cameras and enforcement relations, the members of those relations, and the roads the camera
# nodes sit on (with geometry: their bearing at the node). One request, ~0.6 MB, ~20 s on the public instance.
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_QUERY = """[out:json][timeout:300];
area["ISO3166-2"="AU-NSW"]["admin_level"="4"]->.nsw;
(
  node["highway"="speed_camera"](area.nsw);
  relation["type"="enforcement"](area.nsw);
)->.cams;
.cams out body;
node(r.cams)->.mem_n;
way(r.cams)->.mem_w;
.mem_n out body;
.mem_w out geom;
node.cams["highway"="speed_camera"];
way(bn)["highway"]->.parents;
.parents out geom;
"""
USER_AGENT = "SoRadGaming-openpilot-nswcameras/1 (+https://github.com/SoRadGaming/openpilot)"

CARDINAL = {'N': 0.0, 'NNE': 22.5, 'NE': 45.0, 'ENE': 67.5, 'E': 90.0, 'ESE': 112.5, 'SE': 135.0, 'SSE': 157.5,
            'S': 180.0, 'SSW': 202.5, 'SW': 225.0, 'WSW': 247.5, 'W': 270.0, 'WNW': 292.5, 'NW': 315.0, 'NNW': 337.5}
# enforcement= values (split on ';'), normalized: OSM has typos of traffic_signals in NSW
ENF_ALIAS = {'traffic_signal': 'traffic_signals', 'traffig_signals': 'traffic_signals', 'red_light_camera': 'traffic_signals',
             'red_light': 'traffic_signals'}
ENF_USED = {'maxspeed', 'traffic_signals'}
ONEWAY_YES = {'yes', '1', 'true'}
DEDUP_M = 12.0  # two rows this close, with compatible directions, are one camera mapped twice ...
DEDUP_DIRECTED_M = 25.0  # ... or this close when both directions are known and agree (a pole and a node on the road)
MIN_DIR_CHORD_M = 5.0  # a relation's from/to closer than this gives no direction


# ============================================================================ geometry
def bearing(lat1, lon1, lat2, lon2) -> float:
  """Initial great-circle bearing, degrees clockwise from north, 0..360."""
  p1, p2 = math.radians(lat1), math.radians(lat2)
  dl = math.radians(lon2 - lon1)
  y = math.sin(dl) * math.cos(p2)
  x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
  return math.degrees(math.atan2(y, x)) % 360.0


def dist_m(lat1, lon1, lat2, lon2) -> float:
  kx = 111320.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
  return math.hypot((lon2 - lon1) * kx, (lat2 - lat1) * 110574.0)


def adiff(a, b) -> float:
  """|a - b| on the circle, 0..180."""
  return abs((a - b + 180.0) % 360.0 - 180.0)


def parse_direction(v):
  """A direction= value -> degrees, 'forward', 'backward' or None (both / unknown / unparsable)."""
  if v is None:
    return None
  s = str(v).strip()
  if not s:
    return None
  if s.lower() in ('forward', 'backward'):
    return s.lower()
  if s.upper() in CARDINAL:
    return CARDINAL[s.upper()]
  try:
    d = float(s)
  except ValueError:
    return None
  return d % 360.0 if math.isfinite(d) else None


def parse_maxspeed(v) -> int:
  m = re.match(r'\s*(\d{2,3})', str(v or ''))
  return int(m.group(1)) if m else 0


# ============================================================================ Overpass answer -> rows
def _text(tags) -> str:
  return ' '.join(str(tags.get(k, '')) for k in ('name', 'description', 'note', 'speed_camera', 'enforcement', 'camera:type')).lower()


def _excluded(tags) -> str | None:
  t = _text(tags)
  if 'safe-t-cam' in t or 'safe t cam' in t or 'safetcam' in t:
    return 'safe_t_cam'
  if 'average' in t or tags.get('speed_camera') == 'average_speed':
    return 'average_speed'
  if 'waterway' in t:
    return 'waterway'
  if 'mobile phone' in t or 'mobile_phone' in t or 'seatbelt' in t:
    return 'mobile_phone'
  return None


def _school(tags) -> bool:
  if 'school' in _text(tags):
    return True
  cond = str(tags.get('maxspeed:conditional', ''))
  return bool(re.search(r'\b40\s*@', cond)) and bool(re.search(r'\d{1,2}:\d{2}', cond))


def _node_kind(tags) -> int:
  t = _text(tags)
  if ('red light' in t or 'red-light' in t or 'redlight' in t or 'combo' in t or tags.get('enforcement') == 'traffic_signals'
      or tags.get('speed_camera') == 'traffic_signals'):
    return KIND_RED_LIGHT
  if _school(tags):
    return KIND_SCHOOL
  return KIND_SPEED


def _enforcement(tags) -> set:
  out = set()
  for v in str(tags.get('enforcement', '')).split(';'):
    v = v.strip().lower()
    if v:
      out.add(ENF_ALIAS.get(v, v))
  return out


def _way_bearing_at(way, i) -> float | None:
  """The bearing of a way (as drawn) at its i-th node, from its neighbors. None without geometry."""
  g = way.get('geometry') or []
  if len(g) < 2 or i >= len(g):
    return None
  a, b = g[max(i - 1, 0)], g[min(i + 1, len(g) - 1)]
  if a is None or b is None or (a['lat'] == b['lat'] and a['lon'] == b['lon']):
    return None
  return bearing(a['lat'], a['lon'], b['lat'], b['lon'])


def _oneway(tags) -> int:
  """+1 one-way as drawn, -1 one-way against the drawing, 0 two-way."""
  ow = str(tags.get('oneway', '')).lower()
  if ow == '-1':
    return -1
  if ow in ONEWAY_YES:
    return 1
  if ow == 'no':
    return 0
  if tags.get('highway') in ('motorway', 'motorway_link') or tags.get('junction') in ('roundabout', 'circular'):
    return 1
  return 0


def parse_overpass(doc):
  """An Overpass JSON answer to OVERPASS_QUERY -> (rows, report).

  rows: list of dicts {id (OSM node id), lat, lon, kind (KIND_*), dir (degrees or None = both ways), dsrc (DSRC_*),
  axis (the road's bearing at the node, either way, or None), maxspeed (km/h, 0 unknown)}. One row per camera and
  direction; a camera checking both ways of a road with one relation per direction is two rows. report: counts."""
  if not isinstance(doc, dict) or not isinstance(doc.get('elements'), list):
    raise ValueError('not an Overpass JSON answer (no elements list)')
  nodes, ways, rels = {}, {}, {}
  for e in doc['elements']:
    if not isinstance(e, dict) or 'id' not in e:
      continue
    store = {'node': nodes, 'way': ways, 'relation': rels}.get(e.get('type'))
    if store is None:
      continue
    old = store.get(e['id'])
    if old is None:
      store[e['id']] = dict(e)
    else:  # the same element twice (a member and a camera): merge what each copy has
      for k, v in e.items():
        if k not in old or (k == 'tags' and not old.get('tags')):
          old[k] = v

  # the roads each node sits on: [(way, index)]
  parents: dict[int, list] = {}
  for w in ways.values():
    tags = w.get('tags') or {}
    if 'highway' not in tags:
      continue
    for i, n in enumerate(w.get('nodes') or []):
      parents.setdefault(n, []).append((w, i))

  def axis_of(nid):
    """The bearing (as drawn) of the first road through the node, or None."""
    for w, i in parents.get(nid, []):
      b = _way_bearing_at(w, i)
      if b is not None:
        return b
    return None

  report = {'osm_base': (doc.get('osm3s') or {}).get('timestamp_osm_base'), 'speed_camera_nodes': 0, 'relations': len(rels),
            'excluded': {}, 'no_device': 0, 'device_not_node': 0, 'dup_removed': 0}

  def exclude(why):
    report['excluded'][why] = report['excluded'].get(why, 0) + 1

  rows = []
  in_relation = set()
  # ---- relations first: they carry the direction
  for r in rels.values():
    tags = r.get('tags') or {}
    if tags.get('type') != 'enforcement':
      continue
    enf = _enforcement(tags)
    why = _excluded(tags)
    if 'average_speed' in enf:
      why = 'average_speed'
    if why is None and not (enf & ENF_USED):
      why = 'enforcement=' + (';'.join(sorted(enf)) or 'none')
    members = r.get('members') or []
    devs = [m['ref'] for m in members if m.get('role') == 'device' and m.get('type') == 'node' and m.get('ref') in nodes]
    if any(m.get('role') == 'device' and m.get('type') != 'node' for m in members):
      report['device_not_node'] += 1
    if why is not None:
      exclude(why)
      in_relation.update(devs)  # a device of an excluded relation is not counted again as a plain node
      continue
    frm = [m['ref'] for m in members if m.get('role') == 'from' and m.get('type') == 'node' and m.get('ref') in nodes]
    to = [m['ref'] for m in members if m.get('role') == 'to' and m.get('type') == 'node' and m.get('ref') in nodes]
    if not devs and to:
      # no device mapped: the check ends at `to`, which is where a point camera stands (the wiki: a device on the
      # road IS the end of the check); the Harbour Tunnel / Eastern Distributor relations are mapped like this
      devs, to = to[:1], []
      report['to_as_device'] = report.get('to_as_device', 0) + 1
    if not devs:
      report['no_device'] += 1
      continue
    red = 'traffic_signals' in enf
    for dv in devs:
      in_relation.add(dv)
      dn = nodes[dv]
      dtags = dn.get('tags') or {}
      if _excluded(dtags):
        exclude(_excluded(dtags))
        continue
      d = None
      if frm:
        a = nodes[frm[0]]
        b = nodes[to[0]] if to else dn
        if dist_m(a['lat'], a['lon'], b['lat'], b['lon']) >= MIN_DIR_CHORD_M:
          d = bearing(a['lat'], a['lon'], b['lat'], b['lon'])
      kind = KIND_RED_LIGHT if red or _node_kind(dtags) == KIND_RED_LIGHT else (KIND_SCHOOL if _school(dtags) or _school(tags) else KIND_SPEED)
      ax = axis_of(dv)
      rows.append({'id': int(dv), 'lat': float(dn['lat']), 'lon': float(dn['lon']), 'kind': kind, 'dir': d,
                   'dsrc': DSRC_RELATION if d is not None else DSRC_NONE, 'axis': ax,
                   'maxspeed': parse_maxspeed(tags.get('maxspeed') or dtags.get('maxspeed'))})

  # ---- plain camera nodes
  for nid, n in nodes.items():
    tags = n.get('tags') or {}
    if tags.get('highway') != 'speed_camera':
      continue
    report['speed_camera_nodes'] += 1
    if nid in in_relation:
      continue
    why = _excluded(tags)
    if why is not None:
      exclude(why)
      continue
    ax = axis_of(nid)
    d, src = None, DSRC_NONE
    for pw, pi in parents.get(nid, []):
      o = _oneway(pw.get('tags') or {})
      b = _way_bearing_at(pw, pi)
      if o and b is not None:
        d, src = (b if o > 0 else (b + 180.0) % 360.0), DSRC_ONEWAY
        break
    if d is None:
      pd = parse_direction(tags.get('direction') or tags.get('camera:direction'))
      if isinstance(pd, float):
        d, src = pd, DSRC_TAG
      elif pd in ('forward', 'backward') and ax is not None:
        d, src = (ax if pd == 'forward' else (ax + 180.0) % 360.0), DSRC_WAYDIR
    rows.append({'id': int(nid), 'lat': float(n['lat']), 'lon': float(n['lon']), 'kind': _node_kind(tags), 'dir': d, 'dsrc': src,
                 'axis': ax, 'maxspeed': parse_maxspeed(tags.get('maxspeed'))})

  rows = dedup(rows, report)
  report['rows'] = len(rows)
  report['kinds'] = {KIND_NAMES[k]: sum(1 for x in rows if x['kind'] == k) for k in (KIND_SPEED, KIND_RED_LIGHT, KIND_SCHOOL)}
  report['direction_sources'] = {DSRC_NAMES[k]: sum(1 for x in rows if x['dsrc'] == k) for k in range(len(DSRC_NAMES))}
  return rows, report


def _same_way(a, b) -> bool:
  if a['dir'] is None or b['dir'] is None:
    return True
  return adiff(a['dir'], b['dir']) <= 45.0


def dedup(rows, report=None):
  """Rows within DEDUP_M of each other with compatible directions (or within DEDUP_DIRECTED_M when both directions
  are known and agree) are one camera mapped twice (a camera node and a relation's device beside it). The
  better-directed one is kept (relation, one-way road, tag, way direction, none); a red-light or school kind wins over
  plain speed. The same node with two directions (one relation per direction)
  stays two rows."""
  rank = {DSRC_RELATION: 0, DSRC_ONEWAY: 1, DSRC_TAG: 2, DSRC_WAYDIR: 3, DSRC_NONE: 4}
  order = sorted(range(len(rows)), key=lambda i: (rank[rows[i]['dsrc']], rows[i]['id']))
  kept: list[dict] = []
  removed = 0
  for i in order:
    r = rows[i]
    twin = None
    for k in kept:
      if k['id'] == r['id'] and k['dir'] is not None and r['dir'] is not None and adiff(k['dir'], r['dir']) > 45.0:
        continue  # one node, two directions
      d = dist_m(k['lat'], k['lon'], r['lat'], r['lon'])
      both = k['dir'] is not None and r['dir'] is not None and adiff(k['dir'], r['dir']) <= 45.0
      if (d <= DEDUP_M and _same_way(k, r)) or (d <= DEDUP_DIRECTED_M and both):
        twin = k
        break
    if twin is None:
      kept.append(dict(r))
      continue
    removed += 1
    if r['kind'] != KIND_SPEED and twin['kind'] == KIND_SPEED:
      twin['kind'] = r['kind']
    if not twin['maxspeed']:
      twin['maxspeed'] = r['maxspeed']
    if twin['axis'] is None:
      twin['axis'] = r['axis']
  if report is not None:
    report['dup_removed'] = report.get('dup_removed', 0) + removed
  return sorted(kept, key=lambda r: (r['id'], -1.0 if r['dir'] is None else r['dir']))


def load_overpass(path):
  with open(path, encoding='utf-8') as f:
    return json.load(f)


# ============================================================================ fetch (CI / PC only)
def fetch(out_path, url=OVERPASS_URL, timeout=400.0, tries=4, wait_s=30.0, log=print):
  """POST OVERPASS_QUERY and write the answer to out_path (via a temp file). Raises after `tries` failures. Checks the
  answer is Overpass JSON with elements and a data timestamp, not a rate-limit or error page."""
  import urllib.parse
  import urllib.request

  body = urllib.parse.urlencode({'data': OVERPASS_QUERY}).encode()
  last = None
  for k in range(tries):
    if k:
      time.sleep(wait_s * k)
    try:
      req = urllib.request.Request(url, data=body, headers={'User-Agent': USER_AGENT})
      with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
      doc = json.loads(raw.decode('utf-8'))
      if not isinstance(doc.get('elements'), list) or not doc['elements']:
        raise ValueError(f"no elements (remark: {doc.get('remark')!r})")
      if not (doc.get('osm3s') or {}).get('timestamp_osm_base'):
        raise ValueError('no osm3s.timestamp_osm_base')
      if doc.get('remark') and 'error' in str(doc['remark']).lower():
        raise ValueError(f"Overpass remark: {doc['remark']!r}")
      tmp = out_path + '.tmp'
      with open(tmp, 'wb') as f:
        f.write(raw)
      os.replace(tmp, out_path)
      log(f"cameras: {len(raw):,} bytes, {len(doc['elements'])} elements, OSM data of {doc['osm3s']['timestamp_osm_base']}")
      return doc
    except Exception as e:  # network, HTTP 429/504, a truncated or error answer: try again
      last = e
      log(f'cameras: attempt {k + 1} of {tries} failed: {e!r}')
  raise RuntimeError(f'Overpass fetch failed after {tries} attempts: {last!r}')


# ============================================================================ the device: hold through a short gap
class CameraHold:
  """The matcher's next camera ahead, once a second (matcher result keys camera_*), made steady for the HUD:

  * a camera found is published as found (its id, kind and distance along the road);
  * when the next tick finds none (a tick of hysteresis, a heading-less fix at a junction, the walk cut short) but the
    camera was found within HOLD_S and is still ahead by the distance run down at the car's speed, it is published as
    HELD with that distance - for at most HOLD_S;
  * once that run-down distance reaches 0 the camera is behind the car: it goes, and is not held again;
  * nothing is held across a change of state the matcher calls unsafe (no match, ambiguous) for longer than HOLD_S.
  update() -> None or dict(id, kind, dist_m, dir_known, dr, held)."""

  HOLD_S = 3.0

  def __init__(self):
    self.last: dict | None = None
    self._t = None  # mono time of the last found camera

  def reset(self):
    self.last = None
    self._t = None

  def update(self, r, speed_mps, mono_time):
    found = None
    if r is not None and r.get('camera_kind') and r.get('camera_dist_m') is not None:
      found = {'id': int(r.get('camera_id') or 0), 'kind': int(r['camera_kind']), 'dist_m': float(r['camera_dist_m']),
               'dir_known': bool(r.get('camera_dir_known')), 'dr': bool(r.get('camera_dr')), 'held': False}
    now = float(mono_time)
    if found is not None:
      self.last, self._t = dict(found), now
      return found
    if self.last is None or self._t is None:
      return None
    age = now - self._t
    run = self.last['dist_m'] - max(float(speed_mps or 0.0), 0.0) * age
    if age > self.HOLD_S or run <= 0.0:
      self.reset()
      return None
    return dict(self.last, dist_m=run, held=True)


# ============================================================================ CLI
def main(argv=None):
  import argparse

  ap = argparse.ArgumentParser(description='NSW speed cameras from OpenStreetMap (CI / PC only, never on the device).')
  sub = ap.add_subparsers(dest='cmd', required=True)
  f = sub.add_parser('fetch', help='run the Overpass query and write its JSON answer')
  f.add_argument('--out', required=True)
  f.add_argument('--url', default=OVERPASS_URL)
  f.add_argument('--tries', type=int, default=4)
  s = sub.add_parser('summary', help='parse an Overpass answer and print what the builder would take')
  s.add_argument('path')
  a = ap.parse_args(argv)
  if a.cmd == 'fetch':
    try:
      fetch(a.out, url=a.url, tries=a.tries)
    except RuntimeError as e:
      print(f'::error::{e}', file=sys.stderr)
      return 1
    return 0
  rows, rep = parse_overpass(load_overpass(a.path))
  print(json.dumps(rep, indent=1))
  return 0


if __name__ == '__main__':
  sys.exit(main())
