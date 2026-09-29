"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): synthetic fixtures for the NSW zone tests: a local metric frame, GeoJSON-like features,
SchoolZones.json-like records, a mini-index builder, and a car that drives a polyline (with or without GPS,
feeding speed and yaw like the integration layer does).

Contains data from Transport for NSW, CC BY 4.0, modified. (The fixtures here are synthetic.)
"""

import datetime as dt
import math
import os
import shutil
import tempfile

from openpilot.sunnypilot.mapd.nsw_zones import build_index as bi
from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz

LAT0, LON0 = -33.80, 151.00
KX = 111320.0 * math.cos(math.radians(LAT0))
KY = 110574.0


def ll(x, y):
  """meters east/north of the origin -> [lon, lat]"""
  return [LON0 + x / KX, LAT0 + y / KY]


def latlon(x, y):
  lon, lat = ll(x, y)
  return lat, lon


def xy(lat, lon):
  return (lon - LON0) * KX, (lat - LAT0) * KY


def feat(coords_m, speed, typ='Default', direction='Both Directions', multi=False):
  if multi:
    geom = {'type': 'MultiLineString', 'coordinates': [[ll(*p) for p in part] for part in coords_m]}
  else:
    geom = {'type': 'LineString', 'coordinates': [ll(*p) for p in coords_m]}
  props = {'Type': typ, 'Status': 'Existing', 'Direction': direction, 'Speed': f'{speed} km/h'}
  props.update({'sz_' + k.lower(): v for k, v in props.items()})
  return {'type': 'Feature', 'geometry': geom, 'properties': props}


def school_rec(zid, name, x0, x1, y0, y1, times=('08:00 AM', '09:30 AM', '02:30 PM', '04:00 PM'), late='N', speed=40):
  ring = [ll(x0, y0), ll(x1, y0), ll(x1, y1), ll(x0, y1), ll(x0, y0)]
  return {
    'SZ_ZONE_ID': str(zid),
    'SZ_TYPE': 'School',
    'SZ_STATUS': 'Existing',
    'SZ_DIRECTION': 'Both Directions',
    'SZ_SPEED': f'{speed} km/h',
    'START_TIME_AM': times[0],
    'END_TIME_AM': times[1],
    'START_TIME_PM': times[2],
    'END_TIME_PM': times[3],
    'SCL_NAME': name,
    'LATE_OPENING_SCHOOL': late,
    'OP_CAL': "<OperatingCalendar StartDate='2026-02-09' EndDate='2026-04-02' Recurrence='* * * 1-5 *'/>",
    'json_geometry': {'type': 'Polygon', 'coordinates': [ring]},
  }


def unix_sydney(y, mo, d, h, mi=0, s=0):
  """local Sydney wall time -> unix seconds (uses the module's own rules, verified separately)."""
  naive = (dt.datetime(y, mo, d, h, mi, s) - dt.datetime(1970, 1, 1)).total_seconds()
  u = naive - 600 * 60
  return naive - nz.utc_offset_min(u, 0) * 60


ALWAYS = (lambda d, division=None: True, 'test: every day')
NEVER = (lambda d, division=None: False, 'test: no day')
UNKNOWN = (lambda d, division=None: None, 'test: unknown')

# a Tuesday morning inside the school window, and one outside it
T_IN = unix_sydney(2026, 10, 13, 8, 30)
T_OUT = unix_sydney(2026, 10, 13, 9, 31)


class MiniIndex:
  """Build features (+ school records) into a temporary index file. Use as a context or call close()."""

  def __init__(self, feats, schools=None, simplify_m=1.0):
    self.tmp = tempfile.mkdtemp(prefix='nswz_test_')
    schools = schools if schools is not None else [school_rec(9, 'FAR AWAY SCHOOL', 90000, 90100, 90000, 90100)]
    self.B = bi.build_arrays(feats, schools, simplify_m, log=lambda s: None, data_version='test')
    self.path = os.path.join(self.tmp, 'mini.npz')
    bi.write_index(self.B['arrays'], self.path)

  def close(self):
    shutil.rmtree(self.tmp, ignore_errors=True)

  def __enter__(self):
    return self

  def __exit__(self, *a):
    self.close()


def drive(m, pts, heading, speed=15.0, t0=0.0, unix=T_IN, acc=3.0, gps_ok=True, dt_s=1.0, **kw):
  """Fixes at the given points (meters), one every dt_s seconds."""
  out = []
  for i, (x, y) in enumerate(pts):
    lat, lon = latlon(x, y)
    out.append(m.update(lat, lon, heading, speed, gps_ok, acc, unix + i * dt_s, mono_time=t0 + i * dt_s, **kw))
  return out


class Car:
  """Drives a polyline (meters) at a constant speed, one update per dt seconds, like mapd at 1 Hz.
  GPS is good while `gps(s)` is True (s = distance driven); otherwise no fix is given. Yaw is fed as yaw_deg
  (the path's compass heading, plus `yaw_drift_dps` per second) unless yaw=None; 'rate' feeds yaw_rate_dps."""

  def __init__(self, m, path, speed=20.0, dt=1.0, unix=T_OUT, yaw='deg', yaw_drift_dps=0.0, lookahead=False):
    self.m, self.speed, self.dt, self.unix, self.yaw, self.drift, self.lookahead = m, speed, dt, unix, yaw, yaw_drift_dps, lookahead
    self.px = [p[0] for p in path]
    self.py = [p[1] for p in path]
    self.cum = [0.0]
    for i in range(1, len(path)):
      self.cum.append(self.cum[-1] + math.hypot(self.px[i] - self.px[i - 1], self.py[i] - self.py[i - 1]))
    self.t = 0.0
    self.s = 0.0
    self.prev_heading = None

  @property
  def length(self):
    return self.cum[-1]

  def at(self, s):
    s = min(max(s, 0.0), self.cum[-1])
    k = 0
    while k < len(self.cum) - 2 and self.cum[k + 1] < s:
      k += 1
    sl = self.cum[k + 1] - self.cum[k]
    f = (s - self.cum[k]) / sl if sl > 0 else 0.0
    x = self.px[k] + f * (self.px[k + 1] - self.px[k])
    y = self.py[k] + f * (self.py[k + 1] - self.py[k])
    hd = math.degrees(math.atan2(self.px[k + 1] - self.px[k], self.py[k + 1] - self.py[k])) % 360.0
    return x, y, hd

  def step(self, gps_ok=True, acc=3.0):
    x, y, hd = self.at(self.s)
    kw = {'lookahead': self.lookahead}
    if self.yaw == 'deg':
      kw['yaw_deg'] = (hd + self.drift * self.t) % 360.0
    elif self.yaw == 'rate':
      if self.prev_heading is None:
        kw['yaw_rate_dps'] = 0.0
      else:
        kw['yaw_rate_dps'] = nz._wrap180(hd - self.prev_heading) / self.dt + self.drift
      self.prev_heading = hd
    if gps_ok:
      lat, lon = latlon(x, y)
      r = self.m.update(lat, lon, hd, self.speed, True, acc, self.unix + self.t, mono_time=self.t, **kw)
    else:
      r = self.m.update(None, None, None, self.speed, False, None, self.unix + self.t, mono_time=self.t, **kw)
    r['_s'] = self.s
    r['_x'] = x
    r['_y'] = y
    self.t += self.dt
    self.s += self.speed * self.dt
    return r

  def run(self, gps=lambda s: True, until=None):
    out = []
    end = self.length if until is None else min(until, self.length)
    while self.s <= end:
      out.append(self.step(gps_ok=gps(self.s)))
    return out


def install_for_test(d, npz_path, data_version='test'):
  """Install an index into a data directory the way the downloader leaves it (stored copy + current.json),
  without a release to fetch from. -> the stored file's path."""
  from openpilot.sunnypilot.mapd.nsw_zones import downloader as dl
  from openpilot.sunnypilot.mapd.nsw_zones.index import load_index, sha256_file, write_stored_aligned

  name = f'{data_version}_000000000000'
  os.makedirs(os.path.join(d, name), exist_ok=True)
  stored = os.path.join(d, name, dl.STORED_NAME)
  write_stored_aligned(load_index(npz_path), stored)
  dl.write_json_atomic(
    os.path.join(d, dl.CURRENT_NAME),
    {
      'dir': name,
      'sha256': '0' * 64,
      'stored_sha256': sha256_file(stored),
      'stored_bytes': os.path.getsize(stored),
      'data_version': data_version,
      'format_version': 2,
      'installed': 0.0,
    },
  )
  return stored
