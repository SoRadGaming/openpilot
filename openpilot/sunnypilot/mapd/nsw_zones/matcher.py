"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-ZONES): the NSW speed-zone map matcher (ported from the P1 tools/nsw_zones.py, every P1 fix kept).

Contains data from Transport for NSW, CC BY 4.0, modified.

Pure Python + numpy. No network, no messaging, no tz database. The integration layer
(live_map_data/nsw_map_data.py) feeds it once per mapd tick:

    m = Matcher(index_path)                      # or Matcher(arrays) with load_index() output
    r = m.update(lat, lon, bearing_deg, speed_mps, gps_ok, h_accuracy_m, unix_time,
                 mono_time=..., yaw_deg=..., yaw_rate_dps=...)
    r['limit_kph']   -> int or None (None: publish nothing from NSW; see r['state'] / r['state_code'])
    r['ahead_kph'], r['ahead_dist_m'] -> the next different limit along the road, or None

The result dict always has the keys in RESULT_KEYS (values None when not applicable). See update().

Rules (all timings are seconds of the mono_time the caller passes, never fix counts):
  * Candidates: base segments within R = clamp(2*acc + 15, 20, 50) m. Cost = (d / max(acc, 7))^2 +
    (heading_err / 20 deg)^2; heading used above 2.5 m/s. One Way lines match only along their digitized direction.
  * A line is not a candidate once the car is max(3 m, acc/2) past either end of it, judged at the line's
    NEAREST point (a short last segment cannot keep an ended zone alive).
  * Hysteresis: the current line stays unless another is clearly cheaper for switch_s. Handover is immediate.
  * Ambiguity: a different-limit rival with a close cost lowers the confidence; below min_conf the limit is
    withheld. The incumbent bonus needs incumbent_min_s AND a clear win; a near tie is never published.
  * Co-located lines (TfNSW draws a Variable zone on top of the static zone of the same road): when a
    Variable line is part of a co-located group, the HIGHER limit of the group is published (owner's rule:
    the lower value is a peak-time reduction). Co-located static lines that disagree are a tie (withheld).
  * GPS lost (gps_ok false, no fix, or accuracy > max_acc_m) within freeze_recent_s of a confident match:
    DEAD RECKONING. Every line within dr_seed_r_m of the last confident fix that agrees with its course seeds a
    hypothesis (early in the loss, lines found along the dead-reckoned track join too: TfNSW's tunnel lines need
    not touch the line matched at the portal). Each is walked by the travelled distance (speed x time) along its
    line and on through every line that continues it or branches off it (a gap up to dr_bridge_r_m to a 60+
    line is bridged where nothing else continues). Hypotheses are scored by how well the car's heading change
    (integrated yaw: yaw_deg or yaw_rate_dps) matches each path's heading change over a sliding window of
    distance (a slow gyro bias does not matter), with a small set of odometry scale factors, plus a weak
    lateral term against the dead-reckoned track in the first 1.5 km. After dr_local_max_m, local streets
    (< 60 km/h, not Variable) are no longer followed, and paths off Variable lines slowly lose (Sydney's
    motorway tunnels are Variable lines). A path against a One Way line drawn on it is dropped (wrong way).
    The published limit is that of the line being followed where every contending hypothesis (within
    dr_decide_gap of the best) agrees; where they do not (a branch not yet resolved), the last limit is held while a
    contending branch still has it, and the LOWEST contending branch is published once none has, for up to
    dr_amb_hold_m (never a limit no candidate road has).
    It ends when the heading fits no followed line, every path ends, or at freeze_max_m (12 km) / freeze_max_s;
    then state 'dr_ended' (code 7) holds the last value it published until GPS returns. Without a yaw input only
    the matched line is followed and a branch with a different limit ends it freeze_end_slack_m past it.
  * After any GPS loss (and at start) a fix is trusted again only at accuracy <= reacq_good_m, or reacq_ok_n
    fixes in a row <= reacq_ok_m; until then dead reckoning continues. The line dead reckoning had reached
    is the continuity prior for the first match.
  * School overlay (a School line on the matched base line) applies only when the clock is plausible, the
    local date is a school-zone day for the zone's division and the local time is inside its window. Past the
    calendar's coverage every weekday is a school day. With no valid clock the state is unknown and nothing is
    published (school_unknown_policy 'withhold'). An overlay lying ov_other_margin_m closer to another parallel
    base line is that line's (a school zone on the street above a tunnel is not the tunnel's), and none applies
    while dead reckoning in a tunnel (a Variable line, or 70+ past dr_school_skip_after_m).
    Local time uses built-in NSW DST rules - no tz database needed. School Bus / Wet Weather: never applied.
  * Look-ahead: along the matched line and on through a UNIQUE continuation (or along the dead-reckoned
    path while all hypotheses agree), the first point where the published limit would change - the
    co-location rule included - or the first school zone that will be active at the estimated arrival time.
"""

import datetime as _dt
import json
import math
from typing import Any

import numpy as np

try:  # package import (the car) or standalone by path (build_index.py self-test, replay tools)
  from . import school_days as _school_days
  from .index import load_index, read_meta
except ImportError:  # loaded by path (build_index._sibling registers the siblings under these names)
  import nsw_zones_school_days as _school_days  # type: ignore[no-redef]
  from nsw_zones_index import load_index, read_meta  # type: ignore[no-redef]

ATTRIBUTION = "Contains data from Transport for NSW, CC BY 4.0, modified"

M_PER_DEG_LAT = 110574.0
M_PER_DEG_LON_EQ = 111320.0

TYPES = ['Default', 'Ordinary Permanent', 'School', 'High Pedestrian', 'Shared', 'Local Traffic', 'Variable', 'School Bus', 'Wet Weather', 'Toll Plaza']
T_SCHOOL, T_VARIABLE, T_SCHOOL_BUS, T_WET = 2, 6, 7, 8
DIR_NAMES = ['both', 'one_way', 'none']
DIR_ONE_WAY = 1

CLOCK_MIN_UNIX = 1735689600  # 2025-01-01: anything earlier is an unset clock
STD_TIMES = (8 * 60, 9 * 60 + 30, 14 * 60 + 30, 16 * 60)

# result['state'] -> the cereal NswZone.state code (0 off, 1 no-match/OSM, 2 matched, 3 ambiguous,
# 4 dead-reckoning, 5 error, 6 no data, 7 dead reckoning ended). 'off' / 'error' / 'no_data' are set by the
# integration layer.
STATE_CODES = {
  'no_match': 1,  # no NSW line here: use OSM
  'gps_lost': 1,  # no position and no dead reckoning was started (no recent confident match): OSM (fix B freezes it)
  'matched': 2,
  'hysteresis': 2,  # matched, holding the current line through a short contrary spell
  'ambiguous': 3,  # a different-limit line is as likely: publish 0 (fix A's timer drops the carried limit)
  'low_confidence': 3,
  'dead_reckoning': 4,
  # Dead reckoning gave up while GPS is still lost (paths unresolved past dr_amb_hold_m, the 12 km / 30 min caps, the
  # heading fits no followed line, every path ended, an error). limit_kph is then the LAST value dead reckoning
  # published - which is never above any branch that was still contending (see _dr_step) - held until GPS returns.
  # It is its own state so that nothing downstream mistakes it for a live match, or re-freezes a value by accident.
  'dr_ended': 7,
}
SCHOOL_CODES = {'none': 0, 'inactive': 1, 'active': 2, 'unknown': 3}

RESULT_KEYS = (
  # what to publish
  'limit_kph',  # int km/h or None: the NSW limit incl. an active school zone
  'state',  # matched | hysteresis | ambiguous | low_confidence | no_match | dead_reckoning | dr_ended | gps_lost
  'state_code',  # STATE_CODES[state]
  'reason',  # human-readable why (for logs)
  # the zone
  'base_kph',  # the zone's own limit before any school overlay (None when unknown)
  'zone_type',  # TYPES name or None
  'zone_type_code',  # index into TYPES, 255 = none
  'variable',  # the published value comes from a Variable line (static value; gantries may show less)
  'direction',  # 'both' | 'one_way' | 'none' | None
  'one_way_undocumented',  # matched a One Way line along its digitized direction (TfNSW coding undocumented)
  'zone_id',  # base part index in THIS index file (not stable across builds)
  'feature_id',  # source feature index (traceability; not stable across builds)
  'colocated',  # None | 'resolved' (Variable-over-static rule applied) | 'unresolved' (static lines disagree)
  # match quality
  'dist_m',  # distance from the fix to the matched line (None while dead-reckoning)
  'heading_err_deg',  # heading error vs the matched line (None without heading)
  'n_candidates',  # candidate lines considered
  'radius_m',  # search radius used
  'confidence',  # 0..1
  'confidence_level',  # high | medium | low | none
  'rival_kph',  # the closest different-limit line (None if none)
  'rival_gap',  # its cost gap (larger = clearer win)
  'candidate_kph',  # the best guess when withheld (ambiguous / low_confidence), else None
  # school
  'school_state',  # none | inactive | active | unknown
  'school_code',  # SCHOOL_CODES[school_state] (cereal schoolZone)
  'school_active',  # bool
  'school_apply',  # bool: the school limit is applied
  'school_kph',  # the school zone's limit (None if no school line here)
  'school_name',
  'school_zone_id',
  'school_reason',
  'overlays',  # list of dict(type, kph, school_row) overlay lines at the snapped point
  'overlay_notes',  # list of str (School Bus / Wet Weather not applied, ...)
  # dead reckoning
  'dr_m',  # distance travelled since the last confident fix while dead-reckoning (cereal holdDistance)
  'dr_s',  # seconds since the last confident fix while dead-reckoning
  'dr_hyps',  # surviving path hypotheses
  'dr_yaw',  # True if a yaw input is steering the branch choice
  'dr_scale',  # odometry scale of the best hypothesis
  # look-ahead (None when not computed / nothing found)
  'ahead_kph',  # next different limit along the road
  'ahead_dist_m',  # distance to it (m)
  'ahead_kind',  # 'zone' | 'school'
  'ahead_zone_type',
  'ahead_school_name',
  'ahead_path_m',  # how far the walk got
  'ahead_stop',  # why the walk ended
)


def _blank():
  r = dict.fromkeys(RESULT_KEYS)
  r.update(
    state='no_match',
    reason='',
    school_state='none',
    school_active=False,
    school_apply=False,
    overlays=[],
    overlay_notes=[],
    confidence=0.0,
    confidence_level='none',
    one_way_undocumented=False,
    variable=False,
    n_candidates=0,
    dr_yaw=False,
  )
  return r


def _finish(r):
  r['state_code'] = STATE_CODES.get(r['state'], 1)
  r['school_code'] = SCHOOL_CODES.get(r.get('school_state') or 'none', 0)
  zt = r.get('zone_type')
  r['zone_type_code'] = TYPES.index(zt) if zt in TYPES else 255
  return r


def _wrap180(a):
  return (a + 180.0) % 360.0 - 180.0


# ============================================================================ local time
_TZ_STD_MIN = (600, 570, 630)  # Sydney +10:00, Broken Hill +09:30, Lord Howe +10:30
_TZ_DST_MIN = (60, 60, 30)
_EPOCH = _dt.datetime(1970, 1, 1)


def _first_sunday(y, m):
  d = _dt.date(y, m, 1)
  return d + _dt.timedelta(days=(6 - d.weekday()) % 7)


def utc_offset_min(unix, tz=0):
  """NSW rules since 2008: DST from the first Sunday in October 02:00 standard time to the first
  Sunday in April 03:00 daylight time (Lord Howe: +30 min DST, ends 02:00 daylight time)."""
  std = _TZ_STD_MIN[tz]
  lst = _EPOCH + _dt.timedelta(seconds=unix + std * 60)
  y = lst.year
  start = _dt.datetime.combine(_first_sunday(y, 10), _dt.time(2, 0))
  end = _dt.datetime.combine(_first_sunday(y, 4), _dt.time(2, 0))
  if tz == 2:
    end -= _dt.timedelta(minutes=30)
  dst = lst < end or lst >= start
  return std + (_TZ_DST_MIN[tz] if dst else 0)


def local_datetime(unix, tz=0):
  return _EPOCH + _dt.timedelta(seconds=unix + utc_offset_min(unix, tz) * 60)


def tz_for_location(lat, lon):
  if lon > 158.0:
    return 2
  if 141.0 <= lon <= 142.0 and -32.5 <= lat <= -31.3:
    return 1
  return 0


# ============================================================================ calendar
def _calendar_end(cal):
  return max(c[1] for c in cal.coverage.values())


def default_calendar(arrays=None):
  """-> (fn, description). fn(date, division='eastern'|'western'|None) -> True / False / None.
  The index file carries the calendar it was built with (calendar_json); school_days.py carries the one this
  software shipped with. The one reaching further into the future is used, so a data update can extend it."""
  cands = []
  try:
    cands.append((_school_days.get_calendar(), 'school_days.py (shipped with the software)'))
  except Exception as e:  # a broken calendar must not take the matcher down
    code_err = repr(e)
  else:
    code_err = None
  if arrays is not None and 'calendar_json' in arrays:
    try:
      doc = json.loads(bytes(np.asarray(arrays['calendar_json'])).decode('utf-8'))
      cands.append((_school_days.calendar_from_json(doc), f'index calendar (generated {doc.get("generated_utc")})'))
    except Exception:
      pass
  if not cands:
    return (lambda d, division=None: None), f'no school calendar ({code_err}): school state unknown'
  cal, desc = max(cands, key=lambda c: _calendar_end(c[0]))

  def fn(d, division=None):
    return cal.is_school_zone_day(d, division)

  fn.coverage_end = _calendar_end(cal)
  fn.coverage_first_end = min(c[1] for c in cal.coverage.values())  # the division whose coverage ends first
  return fn, f'{desc}, valid to {_calendar_end(cal)}'


# ============================================================================ matcher
class Matcher:
  DEFAULTS: dict[str, Any] = {
    "r_min_m": 20.0,
    "r_max_m": 50.0,
    "r_acc_k": 2.0,
    "r_add_m": 15.0,
    "sigma_d_min_m": 7.0,
    "sigma_h_deg": 20.0,
    "max_heading_err_deg": 45.0,
    "min_heading_speed": 2.5,
    "boundary_min_m": 3.0,
    "switch_margin": 1.0,
    "switch_s": 1.75,
    "switch_min_fixes": 2,  # 1.75 s = the 3rd fix at 1 Hz
    "amb_gap": 2.0,
    "incumbent_bonus": 1.5,
    "incumbent_min_s": 1.75,
    "clear_gap": 1.0,
    "tie_gap": 0.25,
    "est_s": 2.75,
    "head_conf_s": 0.75,
    "colocated_m": 1.5,
    "colocated_deg": 10.0,
    "variable_overrides": True,  # co-located group with a Variable line -> the higher limit
    "min_conf": 0.5,
    "max_acc_m": 25.0,
    "reacq_good_m": 10.0,
    "reacq_ok_m": 15.0,
    "reacq_ok_n": 2,
    "freeze_max_s": 1800.0,  # dead reckoning: time cap (slow tunnel traffic is fine)
    "freeze_max_m": 12000.0,  # dead reckoning: distance cap
    "freeze_recent_s": 10.0,  # the last confident match must be this recent at the loss
    "freeze_end_slack_m": 50.0,  # past a path end / an unresolvable branch (no yaw)
    "dr_max_hyp": 24,
    "dr_min_stage_m": 20.0,  # a line shorter than this (a stub between two zones) takes the limit of the line before it
    "dr_local_kph": 60,  # lines below this limit (and not Variable) are local streets, not tunnels: ...
    "dr_local_max_m": 600.0,  # ... dead reckoning follows or joins them only this far into a loss
    "dr_seed_r_m": 40.0,  # seeds: every line this close to the last confident fix...
    "dr_seed_head_deg": 35.0,  # ... running within this of the car's course
    "dr_seed_sigma_deg": 10.0,  # prior: initial heading error vs the GPS course
    "dr_reseed_max_m": 600.0,  # early in a loss, lines found along the dead-reckoned track join as hypotheses
    "dr_reseed_r_m": 25.0,
    "dr_reseed_head_deg": 25.0,
    "dr_reseed_step_m": 20.0,
    "dr_lat_sigma_m": 25.0,  # lateral agreement with the dead-reckoned track (yaw + speed from the last fix) ...
    "dr_lat_grow": 0.05,  # ... its tolerance grows with the distance travelled (heading drift) ...
    "dr_lat_max_m": 1500.0,  # ... and it is used only this far into a loss
    "dr_lat_rot_deg": (-6.0, -3.0, 0.0, 3.0, 6.0),  # track rotations tried (GPS course error at the loss)
    "dr_window_m": 250.0,  # heading change compared over this much travel
    "dr_eval_min_m": 30.0,
    "dr_sigma_deg": 7.0,
    "dr_clip_deg": 15.0,
    "dr_prune": 40.0,  # drop a hypothesis this much costlier than the best
    "dr_decide_gap": 12.0,  # only hypotheses this close to the best must agree on the limit
    "dr_novar_per_m": 0.08,  # cost per meter on a non-Variable line beyond dr_local_max_m (a tunnel prior)
    "dr_lost_deg": 25.0,  # the best path's recent heading residual (rms) above this: the car has left every followed line
    "dr_lost_m": 150.0,  # ... over this much travel
    "dr_scales": (0.97, 0.98, 0.99, 1.0, 1.01, 1.02, 1.03),
    "dr_scale_sigma": 0.015,
    "dr_amb_hold_m": 2000.0,  # hold the last limit this far through an unresolved branch (with yaw)
    "dr_ahead_m": 120.0,  # keep every path built this far ahead of the car
    "dr_branch_r_m": 8.0,
    "dr_join_r_m": 6.0,
    "dr_gap_r_m": 20.0,  # a line continuing the path may start this far from its end (within 25 deg) ...
    "dr_bridge_r_m": 100.0,  # ... and, where nothing else continues, a 60+ km/h line this far (within 25 deg):
    "dr_bridge_deg": 25.0,  # TfNSW's tunnel ramps need not touch the main line they join
    "dr_min_move_mps": 0.3,  # yaw changes while (nearly) stationary are gyro drift: ignored
    "reset_gap_s": 30.0,
    "ov_tol_m": 6.0,
    "ov_slack_m": 1.0,
    "ov_max_angle_deg": 25.0,
    "ov_other_margin_m": 0.25,  # an overlay this much closer to another parallel base line is that line's
    "dr_school_skip_kph": 70,  # while dead reckoning on a Variable line, or one of this limit or more ...
    "dr_school_skip_after_m": 150.0,  # ... this far into a loss (a tunnel, not an urban canyon): no school overlay
    "lookahead_s": 15.0,
    "lookahead_min_m": 150.0,
    "lookahead_max_m": 1000.0,
    "lookahead_step_m": 20.0,
    "hysteresis_q": 0.7,
    # a School line whose state cannot be known (the clock is not set): 'withhold' publishes nothing while matched
    # (ambiguous, candidate_kph = the school limit) and the school limit while dead reckoning; 'apply' always the
    # school limit; 'ignore' the base limit (the P1 behavior, never the default: it is the too-high answer)
    "school_unknown_policy": 'withhold',
  }

  def __init__(self, index, calendar=None, mmap=False, **params):
    """index: a path (load_index) or an already-decoded dict of arrays. calendar: None (default_calendar),
    a function fn(date, division=None) or a (fn, description) tuple."""
    self.p: dict[str, Any] = dict(self.DEFAULTS)
    unknown = set(params) - set(self.p)
    if unknown:
      raise TypeError(f'unknown parameters {sorted(unknown)}')
    self.p.update(params)
    a = index if isinstance(index, dict) else load_index(index, mmap=mmap)
    self.meta = read_meta(a)
    self.attribution = self.meta.get('attribution', ATTRIBUTION)
    self.data_version = str(self.meta.get('data_version') or str(self.meta.get('built_utc', ''))[:10])
    g = self.meta['grid']
    self._g = (int(g['lat0_e6']), int(g['lon0_e6']), int(g['dlat_e6']), int(g['dlon_e6']), int(g['ncol']), float(g['pad_m']))
    self.L = {p: {k[2:]: v for k, v in a.items() if k.startswith(p + '_')} for p in ('b', 'o')}
    self.S = {k[2:]: v for k, v in a.items() if k.startswith('s_')}
    self.n_schools = len(self.S['zone_id'])
    if calendar is None:
      self.calendar, self.calendar_desc = default_calendar(a)
    elif isinstance(calendar, tuple):
      self.calendar, self.calendar_desc = calendar
    else:
      self.calendar, self.calendar_desc = calendar, getattr(calendar, '__name__', 'custom')
    # the last date every division's school calendar covers (None: unknown). Past it, weekdays are taken as school days.
    self.calendar_end = getattr(self.calendar, 'coverage_first_end', None)
    self.reset()

  # ------------------------------------------------------------------ state
  def reset(self):
    self.cur_part = -1
    self._cur_t0 = None  # mono time the current line became current
    self._cur_clear = False  # has it beaten every different-limit rival by >= clear_gap?
    self._contra_t0 = None  # start of the current spell with a different-limit rival as cheap or cheaper
    self._chal = -1
    self._chal_n = 0
    self._chal_t0 = None
    self._last_t = None
    self._last_spd = None
    self._odo = 0.0  # distance travelled (speed x time), m, over every update
    self._yaw = 0.0  # integrated yaw while moving, deg, clockwise positive, arbitrary zero
    self._yaw_raw = None  # last absolute yaw_deg input (for differencing)
    self._yaw_ok = False
    self._hist = []  # recent (odo, yaw or None, t): feeds the dead-reckoning history
    self._last_good: tuple[dict, dict, float, float] | None = None  # (result, ctx, t, odo)
    self._bad_since = None
    self._dr: dict[str, Any] | None = None  # dead-reckoning state during a GPS loss
    self._reacq_pending = True  # a fix must prove its accuracy before it is matched (start / after a loss)
    self._reacq_n = 0
    self._ctx: dict[str, Any] | None = None  # geometry context of the last match (for the look-ahead)
    self.last_n_segments = 0
    self.last_n_candidates = 0

  def _set_current(self, part, t):
    if part != self.cur_part:
      self.cur_part = part
      self._cur_t0 = t
      self._cur_clear = False
      self._contra_t0 = None

  # ------------------------------------------------------------------ geometry helpers
  def _chunks_near(self, L, lat_e6, lon_e6, r_m):
    lat0, lon0, dla, dlo, ncol, pad = self._g
    coslat = math.cos(math.radians(lat_e6 * 1e-6))
    rr = r_m + pad
    ra = rr / M_PER_DEG_LAT * 1e6
    ro = rr / (M_PER_DEG_LON_EQ * coslat) * 1e6
    r0 = int((lat_e6 - ra - lat0) // dla)
    r1 = int((lat_e6 + ra - lat0) // dla)
    c0 = int((lon_e6 - ro - lon0) // dlo)
    c1 = int((lon_e6 + ro - lon0) // dlo)
    keys = (np.arange(r0, r1 + 1, dtype=np.int64)[:, None] * ncol + np.arange(c0, c1 + 1, dtype=np.int64)[None, :]).ravel()
    gk = L['grid_keys']
    if len(gk) == 0:
      return np.zeros(0, np.int64)
    keys = keys.astype(gk.dtype)  # same dtype, or numpy converts the whole haystack per call
    pos = np.searchsorted(gk, keys)
    pos[pos >= len(gk)] = len(gk) - 1
    hit = pos[gk[pos] == keys]
    if len(hit) == 0:
      return np.zeros(0, np.int64)
    gs = L['grid_start']
    items = L['grid_items']
    if len(hit) == 1:
      return np.asarray(items[gs[hit[0]] : gs[hit[0] + 1]], np.int64)
    return np.unique(np.concatenate([items[gs[h] : gs[h + 1]] for h in hit]).astype(np.int64))

  def _segments(self, L, lat_e6, lon_e6, r_m):
    """All segments of chunks near the point, in a local metric frame centered on the point."""
    ch = self._chunks_near(L, lat_e6, lon_e6, r_m)
    if len(ch) == 0:
      return None
    n = L['chunk_n'][ch].astype(np.int64)
    tot = int(n.sum())
    sv = np.repeat(L['chunk_v0'][ch].astype(np.int64), n) + (np.arange(tot) - np.repeat(np.cumsum(n) - n, n))
    part = np.repeat(L['chunk_part'][ch].astype(np.int64), n)
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(lat_e6 * 1e-6))
    ky = 1e-6 * M_PER_DEG_LAT
    vlat, vlon = L['vlat'], L['vlon']
    ax = (vlon[sv].astype(np.float64) - lon_e6) * kx
    ay = (vlat[sv].astype(np.float64) - lat_e6) * ky
    bx = (vlon[sv + 1].astype(np.float64) - lon_e6) * kx
    by = (vlat[sv + 1].astype(np.float64) - lat_e6) * ky
    return sv, part, ax, ay, bx, by, kx, ky

  @staticmethod
  def _project(ax, ay, bx, by, qx=0.0, qy=0.0):
    dx = bx - ax
    dy = by - ay
    L2 = dx * dx + dy * dy
    with np.errstate(divide='ignore', invalid='ignore'):
      traw = np.where(L2 > 0, ((qx - ax) * dx + (qy - ay) * dy) / L2, 0.0)
    t = np.clip(traw, 0.0, 1.0)
    px = ax + t * dx
    py = ay + t * dy
    d = np.hypot(px - qx, py - qy)
    return dx, dy, np.sqrt(L2), traw, t, px, py, d

  def _pick_colocated(self, group_parts):
    """Co-located group (base part ids) -> (part whose limit is published, resolved?). A group with a Variable
    line publishes its HIGHEST limit (owner's rule: the lower value is a peak reduction); ties prefer the
    Variable line. Without a Variable line, disagreeing static lines are unresolved (None)."""
    L = self.L['b']
    types = [int(L['part_type'][q]) for q in group_parts]
    speeds = [int(L['part_speed'][q]) for q in group_parts]
    if len(set(speeds)) == 1:
      return group_parts[0], True
    if self.p['variable_overrides'] and T_VARIABLE in types:
      best = max(range(len(group_parts)), key=lambda i: (speeds[i], types[i] == T_VARIABLE))
      return group_parts[best], True
    return None, False

  def _present_along(self, parts, lat_e6, lon_e6, dirs, back_m=15.0):
    """The parts (base ids) that lie within colocated_m + 0.5 of the point back_m behind (lat_e6, lon_e6) along EVERY
    unit direction (ux, uy) in dirs - themselves, or a line of the same limit and type (a Variable zone drawn as
    several lines meets itself end to end: the next one only starts here, but the road is the same). -> set."""
    P = self.p
    L = self.L['b']
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(lat_e6 * 1e-6))
    ky = 1e-6 * M_PER_DEG_LAT
    keep = set(parts)
    for ux, uy in dirs:
      blat, blon = lat_e6 - back_m * uy / ky, lon_e6 - back_m * ux / kx
      seg = self._segments(L, int(round(blat)), int(round(blon)), P['colocated_m'] + 1.0)
      if seg is None:
        return set()
      bq = (blon - round(blon)) * kx, (blat - round(blat)) * ky
      bd = self._project(seg[2], seg[3], seg[4], seg[5], bq[0], bq[1])[-1]
      there = {int(q) for q in seg[1][bd <= P['colocated_m'] + 0.5]}
      kinds = {(int(L['part_speed'][q]), int(L['part_type'][q])) for q in there}
      keep = {q for q in keep if q in there or (int(L['part_speed'][q]), int(L['part_type'][q])) in kinds}
    return keep

  def _effective_limit(self, part, lat_e6, lon_e6, ux, uy):
    """The limit published on `part` at a point (micro-degrees, float ok) travelling along (ux, uy):
    the part's own limit, or the co-location rule's pick. -> (kph or None, part it comes from).
    Sets self._wrong_way when a One Way line lies on top of `part` pointing against the travel direction."""
    self._wrong_way = False
    P = self.p
    L = self.L['b']
    seg = self._segments(L, int(round(lat_e6)), int(round(lon_e6)), 12.0)
    own = int(L['part_speed'][part])
    if seg is None:
      return own, part
    sv, sp, ax, ay, bx, by, kx, ky = seg
    # the segment frame is centered on the ROUNDED point
    qx, qy = (lon_e6 - round(lon_e6)) * kx, (lat_e6 - round(lat_e6)) * ky
    dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by, qx, qy)
    mine = np.nonzero(sp == part)[0]
    if len(mine):
      # measure from the point ON `part` (a dead-reckoned path can run a few meters beside its line, e.g. on the
      # chord that joins a branch)
      j = mine[np.argmin(d[mine])]
      shx, shy = float(px[j]) - qx, float(py[j]) - qy
      qx, qy = float(px[j]), float(py[j])
      lat_e6, lon_e6 = lat_e6 + shy / ky, lon_e6 + shx / kx
      dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by, qx, qy)
    pv0 = L['part_v0']
    first = sv == pv0[sp]
    last = (sv + 2) == pv0[sp + 1]
    inside = ~((first & (traw < 0.0)) | (last & (traw > 1.0)))
    with np.errstate(invalid='ignore', divide='ignore'):
      cosd = (dx * ux + dy * uy) / np.maximum(slen, 1e-9)
    ow = L['part_dir'][sp] == DIR_ONE_WAY
    par = np.abs(cosd) >= math.cos(math.radians(P['colocated_deg']))
    dir_ok = ~ow | (cosd > 0)
    if int(L['part_dir'][part]) != DIR_ONE_WAY:
      against = (d <= P['colocated_m'] + 0.5) & inside & par & ow & (cosd < 0) & (sp != part)
      self._wrong_way = bool(np.any(against))
    k = np.nonzero((d <= P['colocated_m'] + 0.5) & inside & par & dir_ok & (sp != part))[0]
    if len(k) == 0:
      return own, part
    cand = sorted({int(sp[i]) for i in k})
    # ... and 15 m back along the direction of travel: a line that merely starts (branches off) here is not on top
    keep = self._present_along(cand, lat_e6, lon_e6, [(ux, uy)])
    cand = [q for q in cand if q in keep]
    if not cand:
      return own, part
    group = [part] + cand
    pick, ok = self._pick_colocated(group)
    if not ok:
      return None, part
    return int(L['part_speed'][pick]), pick

  # ------------------------------------------------------------------ main entry
  def update(self, lat, lon, bearing_deg, speed_mps, gps_ok, h_accuracy_m, unix_time, mono_time=None, yaw_deg=None, yaw_rate_dps=None, lookahead=True):
    """One fix / tick. All arguments may be None when unknown.
      lat, lon        WGS84 degrees of the fix
      bearing_deg     GPS course over ground (deg from north, clockwise); used above min_heading_speed
      speed_mps       vehicle speed (carState.vEgo); drives the odometry while dead-reckoning
      gps_ok          False when the fix must not be used (no fix, locationd gpsOK false, stale)
      h_accuracy_m    horizontal accuracy of the fix
      unix_time       wall clock, seconds (school zones; None / implausible -> school state 'unknown')
      mono_time       monotonic seconds (all timing rules); defaults to unix_time
      yaw_deg         an integrated heading, degrees CLOCKWISE-positive, any zero (e.g. degrees of
                      livePose.orientationNED.z); only its changes while moving are used. Preferred over
                      yaw_rate_dps: it needs no integration here, so a 1 Hz caller loses nothing between ticks.
      yaw_rate_dps    yaw rate, degrees/s CLOCKWISE-positive (e.g. degrees of livePose.angularVelocityDevice.z);
                      integrated here with the update interval
                      (both signs checked against the GPS course on the owner's drives: slope 0.9-1.0)
      lookahead       also fill the ahead_* keys
    -> dict with every key in RESULT_KEYS."""
    r = self._update(lat, lon, bearing_deg, speed_mps, gps_ok, h_accuracy_m, unix_time, mono_time, yaw_deg, yaw_rate_dps)
    if lookahead:
      if r['state'] == 'dr_ended':
        r['ahead_stop'] = 'dead reckoning ended'
      elif r['limit_kph'] is not None:
        r.update(self.lookahead(unix_time=unix_time))
      else:
        r['ahead_stop'] = 'no limit published'
    return _finish(r)

  def _integrate(self, t, spd, yaw_deg, yaw_rate_dps):
    P = self.p
    dt = 0.0 if self._last_t is None else min(max(t - self._last_t, 0.0), 5.0)
    if self._last_t is not None and t - self._last_t > P['reset_gap_s']:
      self.cur_part = -1
      self._chal = -1
    self._last_t = t
    v0 = self._last_spd if self._last_spd is not None else spd
    ds = 0.5 * ((v0 or 0.0) + (spd or 0.0)) * dt
    self._last_spd = spd
    self._odo += ds
    moving = (spd or 0.0) >= P['dr_min_move_mps']
    if yaw_deg is not None and math.isfinite(yaw_deg):
      if self._yaw_raw is not None and moving:
        self._yaw += _wrap180(yaw_deg - self._yaw_raw)
      self._yaw_raw = float(yaw_deg)
      self._yaw_ok = True
    elif yaw_rate_dps is not None and math.isfinite(yaw_rate_dps):
      if moving:
        self._yaw += float(yaw_rate_dps) * dt
      self._yaw_raw = None
      self._yaw_ok = True
    else:
      self._yaw_ok = False
    self._hist.append((self._odo, self._yaw if self._yaw_ok else None, t))
    if len(self._hist) > 400:
      del self._hist[:100]
    return dt

  def _update(self, lat, lon, bearing_deg, speed_mps, gps_ok, h_accuracy_m, unix_time, mono_time, yaw_deg, yaw_rate_dps):
    P = self.p
    t = float(mono_time if mono_time is not None else (unix_time if unix_time is not None else 0.0))
    spd = None if speed_mps is None or not math.isfinite(speed_mps) else float(speed_mps)
    self._integrate(t, spd, yaw_deg, yaw_rate_dps)

    acc = h_accuracy_m if (h_accuracy_m is not None and math.isfinite(h_accuracy_m) and h_accuracy_m > 0) else None
    pos_ok = lat is not None and lon is not None and math.isfinite(lat) and math.isfinite(lon)
    bad = []
    if not gps_ok:
      bad.append('gps not ok')
    if not pos_ok:
      bad.append('no position')
    if acc is None:
      bad.append('no accuracy')
    elif acc > P['max_acc_m']:
      bad.append(f'accuracy {acc:.0f} m > {P["max_acc_m"]:.0f} m')
    if bad:
      self._reacq_pending = True
      self._reacq_n = 0
      return self._dead_reckon(t, unix_time, '; '.join(bad))
    if self._reacq_pending:
      # coming back from a loss (or starting): the first fixes out of a tunnel are often 15-25 m out while
      # claiming to be fine; do not let them pick a surface street
      if acc <= P['reacq_good_m']:
        self._reacq_pending = False
      elif acc <= P['reacq_ok_m']:
        self._reacq_n += 1
        if self._reacq_n >= P['reacq_ok_n']:
          self._reacq_pending = False
      else:
        self._reacq_n = 0
      if self._reacq_pending:
        return self._dead_reckon(t, unix_time, f're-acquiring: accuracy {acc:.0f} m not yet trusted')
    if self._bad_since is not None and self._dr is not None and not self._dr['ended'] and self._dr.get('part') is not None:
      # continuity prior after a loss: the line dead reckoning had reached (not an established line)
      self.cur_part = -1
      self._set_current(int(self._dr['part']), t)
    self._bad_since = None
    self._dr = None
    return self._match(lat, lon, bearing_deg, spd, acc, unix_time, t)

  # ------------------------------------------------------------------ dead reckoning
  def _dead_reckon(self, t, unix_time, why):
    P = self.p
    if self._bad_since is None:
      self._bad_since = t
    lg = self._last_good
    if lg is None:
      return self._empty('gps_lost', f'{why}; nothing to hold')
    res, ctx, tg, odo_g = lg
    along = self._odo - odo_g
    fresh = (self._bad_since - tg) <= P['freeze_recent_s']
    if not fresh:
      return self._empty('gps_lost', f'{why}; last confident match too old to hold ({self._bad_since - tg:.0f} s before the loss)')
    if (t - tg) > P['freeze_max_s'] or along > P['freeze_max_m']:
      if self._dr is None:
        self._dr = self._dr_init(res, ctx, odo_g, tg)
      self._dr_end(f'capped after {t - tg:.0f} s / {along:.0f} m')
      return self._dr_ended(why, along, t - tg)
    if self._dr is None:
      self._dr = self._dr_init(res, ctx, odo_g, tg)
    D = self._dr
    if D['ended']:
      return self._dr_ended(why, along, t - tg)
    if self._yaw_ok and D['yaw']:
      if not D['hist'] or D['hist'][-1][0] < along - 1e-3:
        D['hist'].append((along, self._yaw))
      self._dr_track(D)
    elif D['yaw']:
      D['yaw_gaps'] += 1
    try:
      self._dr_reseed(D, along)
      out = self._dr_step(D, along)
    except Exception as e:  # never let a geometry corner case take the matcher down
      self._dr_end(f'failed ({e!r})')
      return self._dr_ended(why, along, t - tg)
    if out is None:
      return self._dr_ended(why, along, t - tg)
    kph, h, how = out
    r = _blank()
    L = self.L['b']
    part = int(h['cur_part'])
    src = int(h['cur_src'])
    ptype = int(L['part_type'][src])
    ux, uy = h['cur_u']
    # A school zone is on a surface street. While dead reckoning in a tunnel - a Variable line, or a 70+ line well
    # into the loss - a School line lying over the followed line belongs to the street above it, not to the tunnel
    # (St Mary's, Concord: 0.6 m from the M4 East line in plan, route fc). A short urban-canyon loss keeps it.
    tunnel = ptype == T_VARIABLE or (kph is not None and kph >= P['dr_school_skip_kph'] and along >= P['dr_school_skip_after_m'])
    ovs = self._overlays_at(int(round(h['cur_lat'])), int(round(h['cur_lon'])), 0.0, 0.0, ux, uy, own_part=part)
    if tunnel and any(o['type'] == T_SCHOOL for o in ovs):
      ovs = [o for o in ovs if o['type'] != T_SCHOOL]
      how += '; a School line here is not applied (dead reckoning in a tunnel)'
    sch = self._school_eval(ovs, unix_time, math.degrees(math.atan2(ux, uy)) % 360.0)
    lim = self._apply(kph, sch)
    if sch['school_state'] == 'unknown' and P['school_unknown_policy'] == 'withhold' and kph is not None and sch['school_kph']:
      lim = min(kph, sch['school_kph'])  # never silent while dead reckoning: the lower value
    D['pub_kph'] = lim
    r.update(sch)
    r.update(
      state='dead_reckoning',
      base_kph=kph,
      limit_kph=lim,
      zone_type=TYPES[ptype] if ptype < len(TYPES) else str(ptype),
      variable=ptype == T_VARIABLE,
      direction=DIR_NAMES[int(L['part_dir'][part])],
      one_way_undocumented=int(L['part_dir'][part]) == DIR_ONE_WAY,
      zone_id=part,
      feature_id=int(L['part_feat'][part]),
      colocated='resolved' if src != part else None,
      confidence=round(float(res.get('confidence') or 0.0), 3),
      confidence_level='dead_reckoning',
      overlays=[{"type": TYPES[o['type']], "kph": o['speed'], "school_row": o['school']} for o in ovs],
      dr_m=round(along),
      dr_s=round(t - tg, 1),
      dr_hyps=len(D['hyps']),
      dr_yaw=bool(D['yaw']),
      dr_scale=h.get('scale'),
      n_candidates=0,
    )
    r['reason'] = f'dead reckoning: {why}; {how}'
    return r

  def _dr_track(self, D):
    """Extend the dead-reckoned track (x, y in D's frame) to the newest history sample."""
    if D.get('psi0') is None or len(D['hist']) < 2:
      return
    y0 = D['hist'][0][1]
    T = D['track']
    k = len(T)
    while k < len(D['hist']):
      (d0, w0), (d1, w1) = D['hist'][k - 1], D['hist'][k]
      psi = math.radians(D['psi0'] + 0.5 * (w0 + w1) - y0)
      ds = d1 - d0
      T.append((d1, T[-1][1] + ds * math.sin(psi), T[-1][2] + ds * math.cos(psi)))
      k += 1

  def _dr_reseed(self, D, along):
    """Early in a loss, a line close to the dead-reckoned track and running with it that no hypothesis follows joins
    as a new hypothesis (TfNSW's tunnel lines need not touch the line matched at the portal). Its path so far is the
    track itself; it starts at the best current cost plus its distance from the track."""
    P = self.p
    if not D['yaw'] or D.get('psi0') is None or along > P['dr_reseed_max_m'] or len(D['track']) < 2:
      return
    if along - D.get('reseed_at', -1e9) < P['dr_reseed_step_m']:
      return
    D['reseed_at'] = along
    L = self.L['b']
    _, tx, ty = D['track'][-1]
    psi = (D['psi0'] + D['hist'][-1][1] - D['hist'][0][1]) % 360.0
    qlat = D['lat0'] + ty / D['ky']
    qlon = D['lon0'] + tx / D['kx']
    seg = self._segments(L, int(round(qlat)), int(round(qlon)), P['dr_reseed_r_m'])
    if seg is None:
      return
    sv, sp, ax, ay, bx, by, kx, ky = seg
    ox, oy = (qlon - round(qlon)) * kx, (qlat - round(qlat)) * ky
    dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by, ox, oy)
    sb = np.degrees(np.arctan2(dx, dy)) % 360.0
    best_cost = min((h['cost'] for h in D['hyps'] if math.isfinite(h['cost'])), default=0.0)
    covered = set()
    for h in D['hyps']:
      covered |= {q for _, q in h['stages']}
    added = 0
    for i in np.argsort(d):
      if d[i] > P['dr_reseed_r_m'] or added >= 3:
        break
      q = int(sp[i])
      if q in covered or slen[i] <= 0 or not self._dr_allowed(q, 1e9):
        continue
      for fwd in (True, False):
        if not fwd and int(L['part_dir'][q]) == DIR_ONE_WAY:
          continue
        e = abs(_wrap180(psi - (sb[i] if fwd else (sb[i] + 180.0) % 360.0)))
        if e > P['dr_reseed_head_deg']:
          continue
        covered.add(q)
        h = self._hyp_new(D, q, int(sv[i]), 0.0, 0.0, fwd, 0.0)
        # its path so far: the dead-reckoned track, then the line from its next vertex
        pts = D['track']
        h['x'] = [t[1] for t in pts]
        h['y'] = [t[2] for t in pts]
        h['lat'] = [D['lat0'] + t[2] / D['ky'] for t in pts]
        h['lon'] = [D['lon0'] + t[1] / D['kx'] for t in pts]
        cum = [0.0]
        for k in range(1, len(pts)):
          cum.append(cum[-1] + math.hypot(pts[k][1] - pts[k - 1][1], pts[k][2] - pts[k - 1][2]))
        h['cum'] = cum
        a, b = int(L['part_v0'][q]), int(L['part_v0'][q + 1])
        v = int(sv[i])
        self._hyp_append(D, h, self._join_ids(D, h, list(range(v + 1, b)) if fwd else list(range(v, a - 1, -1))))
        h['stages'] = [(0.0, q)]
        h['checked'] = cum[-1]
        h['prior'] = best_cost + 2.0 + (float(d[i]) / P['dr_lat_sigma_m']) ** 2
        h['reseeded'] = True
        D['hyps'].append(h)
        added += 1
        break

  def _dr_end(self, note=None):
    if self._dr is not None:
      self._dr['ended'] = True
      if note:
        self._dr['note'] = note
    self.cur_part = -1

  def _dr_init(self, res, ctx, odo_g, tg):
    P = self.p
    L = self.L['b']
    ky = 1e-6 * M_PER_DEG_LAT
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(ctx['lat_e6'] * 1e-6))
    # the car's integrated yaw from the last confident fix on (a rolling history covers the time to the loss)
    hist = [(o - odo_g, y) for o, y, tt in self._hist if tt >= tg - 1e-6 and y is not None]
    yaw_on = bool(hist) and self._yaw_ok
    D = {
      "lat0": ctx['lat_e6'],
      "lon0": ctx['lon_e6'],
      "kx": kx,
      "ky": ky,
      "hist": hist if yaw_on else [],
      "yaw": yaw_on,
      "yaw_gaps": 0,
      "hyps": [],
      "ended": False,
      "note": '',
      "last_kph": res.get('base_kph'),
      "pub_kph": res.get('limit_kph'),  # the last value published (the match's, until dead reckoning publishes one)
      "amb_from": None,
      "part": None,
      "next_id": 0,
      "tg": tg,
    }
    brg = ctx.get('fwd_heading')
    if brg is None and ctx.get('fwd_known') is not None:
      sgn = 1.0 if ctx['fwd_known'] else -1.0
      brg = math.degrees(math.atan2(sgn * ctx['ux'], sgn * ctx['uy'])) % 360.0
    D['psi0'] = brg
    D['track'] = [(0.0, 0.0, 0.0)]  # dead-reckoned (along, x, y) in D's frame
    # without a yaw input nothing can tell parallel lines apart: follow only the line matched at the loss (P1)
    seg = self._segments(L, ctx['lat_e6'], ctx['lon_e6'], P['dr_seed_r_m'] if yaw_on else 1.0 + math.hypot(ctx['px'], ctx['py']))
    seen = set()
    if seg is not None:
      sv, sp, ax, ay, bx, by, _, _ = seg
      dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by)
      sb = np.degrees(np.arctan2(dx, dy)) % 360.0
      for i in np.argsort(d):
        if d[i] > P['dr_seed_r_m']:
          break
        q = int(sp[i])
        if slen[i] <= 0 or (not yaw_on and q != ctx['part']):
          continue
        for fwd in (True, False):
          if (q, fwd) in seen or (not fwd and int(L['part_dir'][q]) == DIR_ONE_WAY):
            continue
          if brg is None:
            if q != ctx['part']:
              continue
            prior = 0.0
          else:
            e = abs(_wrap180(brg - (sb[i] if fwd else (sb[i] + 180.0) % 360.0)))
            if e > P['dr_seed_head_deg']:
              continue
            prior = (e / P['dr_seed_sigma_deg']) ** 2
          seen.add((q, fwd))
          D['hyps'].append(self._hyp_new(D, q, int(sv[i]), float(px[i]), float(py[i]), fwd, prior))
    if not D['hyps']:
      D['ended'] = True
      D['note'] = 'no line to follow from the last confident fix'
    return D

  # ---- hypothesis paths (meters in the frame of the last confident fix)
  def _hyp_new(self, D, part, sv, px, py, fwd, prior):
    L = self.L['b']
    pv0 = L['part_v0']
    a, b = int(pv0[part]), int(pv0[part + 1])
    h = {
      "id": D['next_id'],
      "x": [float(px)],
      "y": [float(py)],
      "cum": [0.0],
      "stages": [(0.0, part)],
      "parts": {part},
      "end_part": part,
      "done": False,
      "checked": 0.0,
      "spawned": set(),
      "prior": float(prior),
      "cost": float(prior),
      "scale": 1.0,
      "hd": None,
      "note": '',
    }
    h['lat'] = [D['lat0'] + py / D['ky']]
    h['lon'] = [D['lon0'] + px / D['kx']]
    D['next_id'] += 1
    # direction of the seed segment (arrival direction if the snap is already at the line's end)
    bx, by = float(L['vlon'][sv + 1] - L['vlon'][sv]) * D['kx'], float(L['vlat'][sv + 1] - L['vlat'][sv]) * D['ky']
    n = max(math.hypot(bx, by), 1e-9)
    h['u0'] = (bx / n, by / n) if fwd else (-bx / n, -by / n)
    self._hyp_append(D, h, list(range(sv + 1, b)) if fwd else list(range(sv, a - 1, -1)))
    return h

  def _hyp_append(self, D, h, ids):
    L = self.L['b']
    vlat, vlon = L['vlat'], L['vlon']
    for v in ids:
      la, lo = float(vlat[v]), float(vlon[v])
      x, y = (lo - D['lon0']) * D['kx'], (la - D['lat0']) * D['ky']
      s = math.hypot(x - h['x'][-1], y - h['y'][-1])
      if s <= 0.05:
        continue
      h['x'].append(x)
      h['y'].append(y)
      h['lat'].append(la)
      h['lon'].append(lo)
      h['cum'].append(h['cum'][-1] + s)
    h['hd'] = None

  def _join_ids(self, D, h, ids):
    """Vertices to append when h's path moves onto another line: the leading ones too close to the path end are
    dropped, so the connecting segment runs along the new line instead of sideways (a sideways jump of a few
    meters would read as a large heading change)."""
    if not ids:
      return ids
    L = self.L['b']
    ex, ey = h['x'][-1], h['y'][-1]
    pts = [((float(L['vlon'][v]) - D['lon0']) * D['kx'], (float(L['vlat'][v]) - D['lat0']) * D['ky']) for v in ids[:12]]
    d0 = min(math.hypot(x - ex, y - ey) for x, y in pts[:2])
    need = max(10.0, 3.0 * d0)
    k = 0
    while k < len(pts) - 1 and math.hypot(pts[k][0] - ex, pts[k][1] - ey) < need:
      k += 1
    return ids[k:]

  def _hyp_clone(self, D, h, up_to=None):
    """Copy of h, cut at path distance `up_to` (None = the whole path)."""
    c = dict(h)
    for k in ('x', 'y', 'lat', 'lon', 'cum'):
      c[k] = list(h[k])
    c['stages'] = list(h['stages'])
    c['parts'] = set(h['parts'])
    c['spawned'] = set(h['spawned'])
    c['id'] = D['next_id']
    c['hd'] = None
    D['next_id'] += 1
    if up_to is not None and up_to < c['cum'][-1]:
      cum = c['cum']
      k = max(int(np.searchsorted(cum, up_to, side='right')) - 1, 0)
      f = (up_to - cum[k]) / max(cum[k + 1] - cum[k], 1e-9)
      pt = [c[n][k] + f * (c[n][k + 1] - c[n][k]) for n in ('x', 'y', 'lat', 'lon')]
      for n in ('x', 'y', 'lat', 'lon', 'cum'):
        del c[n][k + 1 :]
      if f > 1e-6:
        for n, val in zip(('x', 'y', 'lat', 'lon'), pt, strict=True):
          c[n].append(val)
        c['cum'].append(up_to)
      c['stages'] = [s for s in c['stages'] if s[0] <= up_to]
      c['end_part'] = c['stages'][-1][1]
      c['done'] = False
      c['checked'] = min(c['checked'], up_to)
    return c

  def _hyp_extend(self, D, h):
    """Append the lines that continue h's path end. -> list of extra hypotheses (one per further option);
    h itself takes the first option. No option -> h['done']."""
    L = self.L['b']
    if len(h['x']) >= 2:
      prev_lat, prev_lon = h['lat'][-2], h['lon'][-2]
    else:  # snapped onto the line's last vertex: arrive along the seed segment
      prev_lat = h['lat'][-1] - h['u0'][1] * 10.0 / D['ky']
      prev_lon = h['lon'][-1] - h['u0'][0] * 10.0 / D['kx']
    opts = self._next_lines(h['end_part'], h['lat'][-1], h['lon'][-1], prev_lat, prev_lon, exclude=h['parts'])
    if not opts:
      h['done'] = True
      h['note'] = 'no line continues the path'
      return []
    pv0 = L['part_v0']
    opts = [o for o in opts if self._dr_allowed(o[0], h['cum'][-1])]
    if not opts:
      opts = [
        o
        for o in self._next_lines(h['end_part'], h['lat'][-1], h['lon'][-1], prev_lat, prev_lon, exclude=h['parts'], bridge=True)
        if self._dr_allowed(o[0], h['cum'][-1])
      ][:3]
    if not opts:
      h['done'] = True
      h['note'] = 'nothing but local streets continues the path'
      return []
    base = self._hyp_clone(D, h) if len(opts) > 1 else None
    out = []
    for i, (q, vnext, fwd) in enumerate(opts):
      hh = h if i == 0 else self._hyp_clone(D, base)
      a, b = int(pv0[q]), int(pv0[q + 1])
      s1 = hh['cum'][-1]
      self._hyp_append(D, hh, self._join_ids(D, hh, list(range(vnext, b)) if fwd else list(range(vnext, a - 1, -1))))
      if hh['cum'][-1] <= s1:
        hh['done'] = True
        hh['note'] = 'degenerate continuation'
      hh['stages'].append((s1, q))
      hh['parts'].add(q)
      hh['end_part'] = q
      if i > 0:
        out.append(hh)
    return out

  def _dr_allowed(self, q, s):
    """May dead reckoning follow base part q from path distance s on? Long outages happen in tunnels, which are
    motorway / arterial lines; a local street is followed only early in a loss."""
    L = self.L['b']
    P = self.p
    if s <= P['dr_local_max_m']:
      return True
    return int(L['part_speed'][q]) >= P['dr_local_kph'] or int(L['part_type'][q]) == T_VARIABLE

  def _next_lines(self, part, end_lat, end_lon, prev_lat, prev_lon, exclude=(), bridge=False):
    """Every base line the road may continue on beyond the end point (micro-degrees) of `part`, arriving from
    prev: lines leaving a vertex near the end point and lines passing through it, within 35 deg of the
    arrival direction. A gap up to dr_gap_r_m is bridged only when nothing is closer.
    -> list of (part, next vertex, forward?), best aligned first, one per (part, direction)."""
    P = self.p
    L = self.L['b']
    arr = None
    best = {}
    passes = ((P['dr_bridge_r_m'], P['dr_bridge_deg']),) if bridge else ((P['dr_join_r_m'], 35.0), (P['dr_gap_r_m'], 25.0))
    for r_m, max_ang in passes:
      elat, elon = int(round(end_lat)), int(round(end_lon))
      seg = self._segments(L, elat, elon, r_m)
      if seg is None:
        continue
      sv, sp, ax, ay, bx, by, kx, ky = seg
      # the segment frame is centered on the ROUNDED end point
      ox, oy = (end_lon - elon) * kx, (end_lat - elat) * ky
      ax, ay, bx, by = ax - ox, ay - oy, bx - ox, by - oy
      if arr is None:
        arr = math.degrees(math.atan2((end_lon - prev_lon) * kx, (end_lat - prev_lat) * ky)) % 360.0
      dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by)
      dA = np.hypot(ax, ay)
      dB = np.hypot(bx, by)
      for i in range(len(sv)):
        q = int(sp[i])
        if q == part or q in exclude or (bridge and int(L['part_speed'][q]) < 60):
          continue
        v = int(sv[i])
        ow = int(L['part_dir'][q]) == DIR_ONE_WAY
        bf = math.degrees(math.atan2(bx[i] - ax[i], by[i] - ay[i])) % 360.0
        bb = (bf + 180.0) % 360.0
        opts = []
        if dA[i] <= r_m:
          opts.append((v + 1, True, bf, dA[i]))
        if dB[i] <= r_m and not ow:
          opts.append((v, False, bb, dB[i]))
        if 0.0 < traw[i] < 1.0 and d[i] <= r_m and dA[i] > r_m and dB[i] > r_m:
          opts.append((v + 1, True, bf, d[i]))
          if not ow:
            opts.append((v, False, bb, d[i]))
        for vn, fwd, brg, dist in opts:
          angle = abs(_wrap180(brg - arr))
          if angle > max_ang:
            continue
          key = (q, fwd)
          score = angle + 2.0 * dist
          if key not in best or score < best[key][0]:
            best[key] = (score, q, vn, fwd)
    return [(q, vn, fwd) for _, q, vn, fwd in sorted(best.values())]

  def _walk_part(self, D, q, v, t, fwd, dist):
    """The point `dist` m along base part q from (segment v, fraction t) in direction fwd, in D's metric frame
    (stops at the part's end). -> (x, y)."""
    L = self.L['b']
    pv0 = L['part_v0']
    a, b = int(pv0[q]), int(pv0[q + 1])
    vlat, vlon = L['vlat'], L['vlon']
    fx = lambda k: (float(vlon[k]) - D['lon0']) * D['kx']  # noqa: E731
    fy = lambda k: (float(vlat[k]) - D['lat0']) * D['ky']  # noqa: E731
    x = fx(v) + t * (fx(v + 1) - fx(v))
    y = fy(v) + t * (fy(v + 1) - fy(v))
    k = v + 1 if fwd else v
    left = dist
    while a <= k < b and left > 0:
      nx, ny = fx(k), fy(k)
      sl = math.hypot(nx - x, ny - y)
      if sl >= left:
        f = left / max(sl, 1e-9)
        return x + f * (nx - x), y + f * (ny - y)
      left -= sl
      x, y = nx, ny
      k = k + 1 if fwd else k - 1
    return x, y

  def _followed(self, D, q, x, y, others, r=12.0):
    """Does another hypothesis already follow line q past the point (x, y) (D's frame)?"""
    for o in others:
      if q in o['parts'] and len(o['x']) >= 2 and self._dist_to_poly(x, y, np.asarray(o['x']), np.asarray(o['y'])) <= r:
        return True
    return False

  def _hyp_scan(self, D, h, up_to, step=10.0, ahead=40.0, others=()):
    """Lines branching off h's path between h['checked'] and up_to (m): a line within dr_branch_r_m of the path,
    running within 45 deg of it, whose point `ahead` m further along ITSELF is at least 3 m further from the path
    (it diverges: a parallel or merging line does not). Each becomes a new hypothesis leaving the path there.
    -> list of new hypotheses."""
    P = self.p
    if up_to <= h['checked']:
      return []
    L = self.L['b']
    r = P['dr_branch_r_m']
    x, y, cum = np.asarray(h['x']), np.asarray(h['y']), np.asarray(h['cum'])
    if len(cum) < 2:
      h['checked'] = up_to
      return []
    out = []
    s = h['checked']
    cos45 = math.cos(math.radians(45.0))
    pv0 = L['part_v0']
    while s <= up_to:
      k = min(max(int(np.searchsorted(cum, s, side='right')) - 1, 0), len(cum) - 2)
      sl = cum[k + 1] - cum[k]
      f = (s - cum[k]) / sl if sl > 0 else 0.0
      qx = x[k] + f * (x[k + 1] - x[k])
      qy = y[k] + f * (y[k + 1] - y[k])
      ux, uy = (x[k + 1] - x[k]) / max(sl, 1e-9), (y[k + 1] - y[k]) / max(sl, 1e-9)
      qlat = D['lat0'] + qy / D['ky']
      qlon = D['lon0'] + qx / D['kx']
      seg = self._segments(L, int(round(qlat)), int(round(qlon)), r)
      if seg is not None:
        sv, sp, ax, ay, bx, by, kx, ky = seg
        ox, oy = (qlon - round(qlon)) * kx, (qlat - round(qlat)) * ky
        dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by, ox, oy)
        # the walked path near here (-30 .. +ahead+30 m), in D's frame
        j0 = max(int(np.searchsorted(cum, s - 30.0)) - 1, 0)
        j1 = min(int(np.searchsorted(cum, s + ahead + 30.0)) + 1, len(cum) - 1)
        wx, wy = x[j0 : j1 + 1], y[j0 : j1 + 1]
        seen = set()
        for i in np.argsort(d):
          if d[i] > r:
            break
          q = int(sp[i])
          if q in h['parts'] or slen[i] <= 0 or (D['yaw'] and not self._dr_allowed(q, 1e9)):
            # with yaw, a branch is only ever taken onto a motorway / arterial line (see _dr_allowed): a turn into a
            # local street shows as the heading fitting no path. Without yaw every branch counts (it ends the hold
            # where the limits start to differ, as P1 did).
            continue
          ow = int(L['part_dir'][q]) == DIR_ONE_WAY
          cosd = (dx[i] * ux + dy[i] * uy) / slen[i]
          for fwd in (True,) if ow else (True, False):
            if (q, fwd) in h['spawned'] or (q, fwd) in seen:
              continue
            if (cosd if fwd else -cosd) < cos45:
              continue
            seen.add((q, fwd))
            jx = qx + (px[i] - ox)
            jy = qy + (py[i] - oy)
            d_here = self._dist_to_poly(jx, jy, wx, wy)
            zx, zy = self._walk_part(D, q, int(sv[i]), float(tcl[i]), fwd, ahead)
            d_ahead = self._dist_to_poly(zx, zy, wx, wy)
            if d_ahead < 5.0 or d_ahead - d_here < 3.0:
              continue  # parallel, co-located or merging: not a branch
            h['spawned'].add((q, fwd))
            if self._followed(D, q, jx, jy, [o for o in others if o is not h] + out):
              continue  # another hypothesis is on that line here already (lines converging at a portal)
            c = self._hyp_clone(D, h, up_to=s)
            c['spawned'].add((q, fwd))
            a, b = int(pv0[q]), int(pv0[q + 1])
            s1 = c['cum'][-1]
            v = int(sv[i])
            # on along the line from its next vertex (no sideways join vertex: it would be a heading spike)
            self._hyp_append(D, c, self._join_ids(D, c, list(range(v + 1, b)) if fwd else list(range(v, a - 1, -1))))
            if c['cum'][-1] > s1:
              c['stages'].append((s1, q))
              c['parts'].add(q)
              c['end_part'] = q
              c['checked'] = s1
              c['hd'] = None
              out.append(c)
      s += step
    h['checked'] = float(up_to)
    return out

  @staticmethod
  def _dist_to_poly(zx, zy, wx, wy):
    if len(wx) < 2:
      return math.hypot(zx - wx[0], zy - wy[0]) if len(wx) else float('inf')
    ax, ay, bx, by = wx[:-1], wy[:-1], wx[1:], wy[1:]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    with np.errstate(divide='ignore', invalid='ignore'):
      t = np.clip(np.where(L2 > 0, ((zx - ax) * dx + (zy - ay) * dy) / L2, 0.0), 0.0, 1.0)
    return float(np.min(np.hypot(ax + t * dx - zx, ay + t * dy - zy)))

  @staticmethod
  def _hyp_heading(h):
    """(grid, unwrapped heading in degrees) of h's path: the bearing of the chord from s-10 m to s+10 m on a 5 m
    grid, so short segments and vertex kinks make no heading spikes. Cached until the path changes."""
    if h['hd'] is None:
      x, y, cum = np.asarray(h['x']), np.asarray(h['y']), np.asarray(h['cum'])
      if len(cum) < 2 or cum[-1] <= 0:
        h['hd'] = (np.array([0.0]), np.array([0.0]))
      else:
        g = np.arange(0.0, cum[-1] + 5.0, 5.0)
        s0 = np.clip(g - 10.0, 0.0, cum[-1])
        s1 = np.clip(g + 10.0, 0.0, cum[-1])
        b = np.arctan2(np.interp(s1, cum, x) - np.interp(s0, cum, x), np.interp(s1, cum, y) - np.interp(s0, cum, y))
        h['hd'] = (g, np.degrees(np.unwrap(b)))
    return h['hd']

  def _hyp_cost(self, D, h, along):
    """Heading-profile cost of h over the car's history, minimized over the odometry scales.
    -> (cost, scale) ; cost inf if the path is too short for every scale."""
    P = self.p
    end = h['cum'][-1]
    scales = P['dr_scales'] if D['yaw'] else (1.0,)  # without yaw nothing can estimate an odometry error
    valid = [c for c in scales if not (h['done'] and c * along > end + P['freeze_end_slack_m'])]
    if not valid:
      return math.inf, None
    if not D['yaw'] or len(D['hist']) < 2:
      c = min(valid, key=lambda c: abs(c - 1.0))
      return h['prior'] + h.get('novar_m', 0.0) * P['dr_novar_per_m'], c
    H = np.asarray(D['hist'], np.float64)
    Dk, Pk = H[:, 0], H[:, 1]
    sel = Dk >= P['dr_eval_min_m']
    if not sel.any():
      c = min(valid, key=lambda c: abs(c - 1.0))
      return h['prior'] + h.get('novar_m', 0.0) * P['dr_novar_per_m'], c
    Dw = np.maximum(Dk - P['dr_window_m'], 0.0)
    order = np.argsort(Dk, kind='stable')
    Pw = np.interp(Dw, Dk[order], Pk[order])
    dpsi = (Pk - Pw)[sel]
    w = np.clip(np.diff(np.concatenate([[0.0], Dk]))[sel] / 20.0, 0.0, 3.0)
    mid, hdg = self._hyp_heading(h)
    recent = Dk[sel] >= along - P['dr_lost_m']
    if along > P['dr_lat_max_m'] + 50.0 and h.get('lat_final') is not None:
      lat_cost = h['lat_final']  # the track it is compared with no longer grows
    else:
      lat_cost = self._hyp_lateral(D, h)
      h['lat_final'] = lat_cost if along > P['dr_lat_max_m'] + 50.0 else None
    best = (math.inf, None)
    h['recent_rms'] = None
    for c in valid:
      s = np.minimum(c * Dk[sel], end)
      sw = np.minimum(c * Dw[sel], end)
      dh = np.interp(s, mid, hdg) - np.interp(sw, mid, hdg)
      r2 = (dpsi - dh) ** 2
      rr = np.minimum(r2, P['dr_clip_deg'] ** 2)
      cost = (
        float(np.sum(w * rr)) / P['dr_sigma_deg'] ** 2
        + ((c - 1.0) / P['dr_scale_sigma']) ** 2
        + h['prior']
        + lat_cost
        + h.get('novar_m', 0.0) * P['dr_novar_per_m']
      )
      if recent.any() and along >= P['dr_lost_m']:
        rr_ = float(np.sqrt(np.mean(r2[recent])))
        h['recent_rms'] = rr_ if h['recent_rms'] is None else min(h['recent_rms'], rr_)
      if cost < best[0]:
        best = (cost, c)
    return best

  def _hyp_lateral(self, D, h):
    """Lateral disagreement of h's path with the dead-reckoned track (early in the loss only, where the track is
    good to a few meters): sum over track points of min((d / sigma)^2, 9), sigma growing with the distance."""
    P = self.p
    T = [t for t in D.get('track', []) if t[0] <= P['dr_lat_max_m']]
    if not T or len(h['x']) < 2:
      return 0.0
    T = np.asarray(T, np.float64)
    x, y, cum = np.asarray(h['x']), np.asarray(h['y']), np.asarray(h['cum'])
    m = cum <= P['dr_lat_max_m'] * 1.1 + 200.0
    m[:2] = True
    x, y = x[m], y[m]
    ax, ay, bx, by = x[:-1][None, :], y[:-1][None, :], x[1:][None, :], y[1:][None, :]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    sig = P['dr_lat_sigma_m'] + P['dr_lat_grow'] * T[:, 0]
    w = np.clip(np.diff(np.concatenate([[0.0], T[:, 0]])) / 20.0, 0.0, 3.0)
    w[0] = 1.0
    best = math.inf
    for th in np.radians(P['dr_lat_rot_deg']):
      c, sn = math.cos(th), math.sin(th)
      qx = (c * T[:, 1] + sn * T[:, 2])[:, None]
      qy = (-sn * T[:, 1] + c * T[:, 2])[:, None]
      with np.errstate(divide='ignore', invalid='ignore'):
        t = np.clip(np.where(L2 > 0, ((qx - ax) * dx + (qy - ay) * dy) / L2, 0.0), 0.0, 1.0)
      d = np.min(np.hypot(ax + t * dx - qx, ay + t * dy - qy), axis=1)
      best = min(best, float(np.sum(w * np.minimum((d / sig) ** 2, 4.0))))
    return best

  def _hyp_at(self, D, h, s):
    """Point, direction, part and effective limit of h at path distance s."""
    cum = h['cum']
    if len(cum) < 2:
      k, f = 0, 0.0
      ux, uy = 0.0, 1.0
      lat, lon = h['lat'][0], h['lon'][0]
    else:
      s = min(max(s, 0.0), cum[-1])
      k = min(max(int(np.searchsorted(cum, s, side='right')) - 1, 0), len(cum) - 2)
      sl = cum[k + 1] - cum[k]
      f = (s - cum[k]) / sl if sl > 0 else 0.0
      lat = h['lat'][k] + f * (h['lat'][k + 1] - h['lat'][k])
      lon = h['lon'][k] + f * (h['lon'][k + 1] - h['lon'][k])
      ux, uy = (h['x'][k + 1] - h['x'][k]) / max(sl, 1e-9), (h['y'][k + 1] - h['y'][k]) / max(sl, 1e-9)
    part = h['stages'][0][1]
    st = h['stages']
    for j, (s0, q) in enumerate(st):
      if s0 <= s + 1e-6:
        s1 = st[j + 1][0] if j + 1 < len(st) else h['cum'][-1]
        if j == 0 or s1 - s0 >= self.p['dr_min_stage_m'] or (j + 1 == len(st) and not h['done']):
          part = q
    kph, src = self._effective_limit(part, lat, lon, ux, uy)
    if self._wrong_way:
      h['wrong_way'] = h.get('wrong_way', 0) + 1
    return lat, lon, (ux, uy), part, kph, src

  def _dr_step(self, D, along):
    """Advance every hypothesis to `along` m. -> (kph, best hypothesis, how) or None (D['note'] says why)."""
    P = self.p
    smax = along * max(P['dr_scales']) + P['dr_ahead_m']
    # 1) build every path far enough ahead, spawning a hypothesis per option at junctions and branch-offs
    todo = list(D['hyps'])
    guard = 0
    while todo and guard < 200:
      guard += 1
      h = todo.pop()
      new = []
      while not h['done'] and h['cum'][-1] < smax:
        new += self._hyp_extend(D, h)
        if len(D['hyps']) + len(new) > 2 * P['dr_max_hyp']:
          break
      new += self._hyp_scan(D, h, min(smax, h['cum'][-1]), others=D['hyps'] + new)
      D['hyps'].extend(new)
      todo.extend(new)
      if len(D['hyps']) > 2 * P['dr_max_hyp']:
        break
    # 2) score, drop the dead and the clearly worse, merge duplicates, cap the count
    alive = []
    for h in D['hyps']:
      h['cost'], h['scale'] = self._hyp_cost(D, h, along)
      if h['scale'] is None:
        continue
      s = h['scale'] * along
      if s > h['cum'][-1] + P['freeze_end_slack_m']:
        continue
      alive.append(h)
    if not alive:
      D['ended'] = True
      D['note'] = 'every followed line ended' + (f' ({D["hyps"][0]["note"]})' if D['hyps'] and D['hyps'][0]['note'] else '')
      D['hyps'] = []
      return None
    best = min(h['cost'] for h in alive)
    alive = [h for h in alive if h['cost'] <= best + P['dr_prune']]
    rms = [h['recent_rms'] for h in alive if h.get('recent_rms') is not None]
    if D['yaw'] and len(rms) == len(alive) and min(rms) > P['dr_lost_deg']:
      D['ended'] = True
      D['note'] = f"the car's heading fits none of the followed lines (best {min(rms):.0f} deg rms over the last {P['dr_lost_m']:.0f} m)"
      D['hyps'] = []
      return None
    step = max(along - D.get('prev_along', along), 0.0)
    D['prev_along'] = along
    L = self.L['b']
    for h in alive:
      lat, lon, u, part, kph, src = self._hyp_at(D, h, h['scale'] * along)
      h.update(cur_lat=lat, cur_lon=lon, cur_u=u, cur_part=part, cur_kph=kph, cur_src=src)
      # a long loss is a tunnel, and Sydney's motorway tunnels are Variable (VSL) lines: a path on anything else
      # (a surface street above the tunnel) slowly loses to one on a Variable line
      if along > P['dr_local_max_m'] and int(L['part_type'][src]) != T_VARIABLE:
        h['novar_m'] = h.get('novar_m', 0.0) + step
    alive = [h for h in alive if h.get('wrong_way', 0) < 2] or alive
    alive.sort(key=lambda h: h['cost'])
    for h in alive:
      s_h = h['scale'] * along
      cur0 = max((x for x, _ in h['stages'] if x <= s_h + 1e-6), default=0.0)
      h['rest'] = tuple(q for s0, q in h['stages'] if s0 >= cur0 - 1e-6)
    kept = []
    for h in alive:
      dup = False
      for k in kept:
        if k['rest'] == h['rest'] and (k['cur_u'][0] * h['cur_u'][0] + k['cur_u'][1] * h['cur_u'][1]) > 0.98:
          dx = (k['cur_lon'] - h['cur_lon']) * D['kx']
          dy = (k['cur_lat'] - h['cur_lat']) * D['ky']
          if math.hypot(dx, dy) < 15.0:
            dup = True
            break
      if not dup:
        kept.append(h)
    if len(kept) > P['dr_max_hyp']:
      if not D['yaw']:
        D['ended'] = True
        D['note'] = f'{len(kept)} possible paths and no yaw input to choose'
        D['hyps'] = []
        return None
      first, rest, seen_parts = [], [], set()
      for h in kept:
        (rest if h['cur_part'] in seen_parts else first).append(h)
        seen_parts.add(h['cur_part'])
      kept = sorted((first + rest)[: P['dr_max_hyp']], key=lambda h: h['cost'])
    D['hyps'] = kept
    h0 = kept[0]
    D['part'] = h0['cur_part']
    # 3) the limit: every CONTENDING hypothesis (within dr_decide_gap of the best; the rest are kept alive only to
    #    recover from a wrong turn) must agree; otherwise hold the last limit for a while
    contenders = [h for h in kept if h['cost'] <= kept[0]['cost'] + P['dr_decide_gap']] if D['yaw'] else kept
    limits = {h['cur_kph'] for h in contenders}
    if len(limits) == 1 and None not in limits:
      kph = kept[0]['cur_kph']
      D['last_kph'] = kph
      D['amb_from'] = None
      how = f'{along:.0f} m along {len(contenders)} path(s) agreeing ({len(kept)} kept)' + (' (yaw)' if D['yaw'] else ' (no yaw input)')
      return kph, h0, how
    if D['amb_from'] is None:
      D['amb_from'] = along
    hold_m = P['dr_amb_hold_m'] if D['yaw'] else P['freeze_end_slack_m']
    if D['last_kph'] is None or along - D['amb_from'] > hold_m:
      D['ended'] = True
      D['note'] = f'paths with different limits ({sorted(str(x) for x in limits)}) unresolved for {along - D["amb_from"]:.0f} m'
      D['hyps'] = []
      return None
    # Never a limit no candidate road has. While a contending branch still carries the last limit (the car may well be
    # on it) that limit is held: contenders disagree for a tick or two all through a real tunnel, and publishing the
    # lowest of them instead made the M4 East replay flip 90/80 every second for 20 s and dip to 60 (30 SLA prompts
    # against 10). Once NO contending branch has it - the line the car was on has ended or been left, e.g. parallel
    # Variable 80 / 70 branches off a 90 - the LOWEST contending branch is published: holding 90 there would be a
    # speed no candidate road has (SLA follows a new limit >= 80 by itself), for up to dr_amb_hold_m.
    known = [x for x in limits if x is not None]
    kph = D['last_kph'] if (not known or D['last_kph'] in known) else min(known)
    how = (
      f'{along:.0f} m; paths disagree ({", ".join(str(x) for x in sorted(limits, key=lambda v: -1 if v is None else v))}) '
      + f'for {along - D["amb_from"]:.0f} m: holding '
      + ('the last limit' if kph == D['last_kph'] else f'the lowest contending branch ({kph}, last {D["last_kph"]})')
    )
    return kph, h0, how

  def dr_debug(self):
    """Snapshot of the dead-reckoning hypotheses (for replay tools and tests), or None."""
    D = self._dr
    if D is None:
      return None
    return {
      "ended": D['ended'],
      "note": D['note'],
      "yaw": D['yaw'],
      "hist": len(D['hist']),
      "last_kph": D['last_kph'],
      "hyps": [
        {
          "id": h['id'],
          "cost": round(h['cost'], 2),
          "scale": h['scale'],
          "part": h.get('cur_part'),
          "kph": h.get('cur_kph'),
          "lat": h.get('cur_lat'),
          "lon": h.get('cur_lon'),
          "parts": [q for _, q in h['stages']],
        }
        for h in D['hyps']
      ],
    }

  def _dr_ended(self, why, along, dt_s):
    """Dead reckoning has given up and GPS is still lost: hold the last value it published (never above a branch that
    was contending, see _dr_step) until GPS returns, as its own state. Holding is the safer of the two answers left:
    publishing nothing lets the resolver carry the same value anyway, and a cleared limit sends the car to its set
    speed."""
    D = self._dr
    cap = D.get('pub_kph') if D is not None else None
    note = D['note'] if D is not None else ''
    r = self._empty(
      'dr_ended',
      f'{why}; dead reckoning ended ({note}); holding {cap} km/h, the last limit it published, until GPS returns',
      dr_m=round(along),
      dr_s=round(dt_s, 1),
    )
    r.update(limit_kph=cap, base_kph=cap, confidence_level='dr_ended')
    return r

  def _empty(self, state, reason, **extra):
    r = _blank()
    r.update(state=state, reason=reason, n_candidates=self.last_n_candidates)
    r.update(extra)
    return r

  # ------------------------------------------------------------------ matching
  def _match(self, lat, lon, bearing_deg, spd, acc, unix_time, t):
    P = self.p
    L = self.L['b']
    lat_e6 = int(round(lat * 1e6))
    lon_e6 = int(round(lon * 1e6))
    R = min(max(P['r_acc_k'] * acc + P['r_add_m'], P['r_min_m']), P['r_max_m'])
    seg = self._segments(L, lat_e6, lon_e6, R)
    self.last_n_segments = 0 if seg is None else len(seg[0])
    self.last_n_candidates = 0
    if seg is None:
      self.cur_part = -1
      self._ctx = None
      return self._empty('no_match', f'no NSW line within {R:.0f} m', radius_m=round(R, 1))
    sv, part, ax, ay, bx, by, kx, ky = seg
    dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by)
    heading_ok = bearing_deg is not None and math.isfinite(bearing_deg) and spd is not None and spd >= P['min_heading_speed']
    pdir = L['part_dir'][part]
    oneway = pdir == DIR_ONE_WAY
    brg = np.degrees(np.arctan2(dx, dy)) % 360.0
    if heading_ok:
      diff = np.abs((bearing_deg - brg + 180.0) % 360.0 - 180.0)  # 0..180 vs digitized direction
      err = np.where(oneway, diff, np.minimum(diff, 180.0 - diff))
      fwd = diff <= 90.0
    else:
      err = np.zeros(len(d))
      fwd = np.ones(len(d), bool)
    # overshoot past either end of the part
    pv0 = L['part_v0']
    first = sv == pv0[part]
    last = (sv + 2) == pv0[part + 1]
    over = np.where(first & (traw < 0), -traw * slen, 0.0)
    over = np.maximum(over, np.where(last & (traw > 1), (traw - 1) * slen, 0.0))
    margin = max(P['boundary_min_m'], acc / 2.0)
    sig_d = max(acc, P['sigma_d_min_m'])
    cost = (d / sig_d) ** 2
    if heading_ok:
      cost = cost + (err / P['sigma_h_deg']) ** 2
    inr = d <= R
    ok = inr & (over <= margin)
    # a line whose NEAREST point is past one of its ends is behind or ahead of the car. Its other segments
    # must not keep it alive with a clamped distance (a short last segment used to hold an ended zone for a
    # few fixes). Deciding on the nearest segment keeps loops (end meets start) intact.
    ii = np.nonzero(inr)[0]
    if len(ii):
      o2 = ii[np.lexsort((d[ii], part[ii]))]
      f2 = np.ones(len(o2), bool)
      f2[1:] = part[o2][1:] != part[o2][:-1]
      near = o2[f2]
      past = part[near[over[near] > margin]]
      if len(past):
        ok &= ~np.isin(part, past)
    n_rej_head = 0
    n_rej_oneway = 0
    if heading_ok:
      hk = err <= P['max_heading_err_deg']
      n_rej_oneway = int(np.count_nonzero(ok & ~hk & oneway & (np.minimum(err, 180 - err) <= P['max_heading_err_deg'])))
      n_rej_head = int(np.count_nonzero(ok & ~hk))
      ok &= hk
    idx = np.nonzero(ok)[0]
    if len(idx) == 0:
      self.cur_part = -1
      self._ctx = None
      why = f'no NSW line within {R:.0f} m'
      if n_rej_head:
        why += f' agreeing with heading ({n_rej_head} rejected by heading'
        why += f', {n_rej_oneway} of them One Way against its digitized direction' if n_rej_oneway else ''
        why += ')'
      return self._empty('no_match', why, radius_m=round(R, 1))
    # best segment per part
    o = idx[np.lexsort((cost[idx], part[idx]))]
    firsts = np.ones(len(o), bool)
    firsts[1:] = part[o][1:] != part[o][:-1]
    cand = o[firsts]
    cand = cand[np.argsort(cost[cand], kind='stable')]
    self.last_n_candidates = len(cand)
    cparts = part[cand]
    best = cand[0]
    prev_part = self.cur_part  # the line followed before this fix (-1 none)
    inc = None
    if self.cur_part >= 0:
      w = np.nonzero(cparts == self.cur_part)[0]
      if len(w):
        inc = cand[w[0]]
    state = 'matched'
    note = ''
    if inc is None:
      chosen = best
      if self.cur_part >= 0:
        note = 'handover (previous line ended or was left)'
      self._chal, self._chal_n, self._chal_t0 = -1, 0, None
    elif best == inc:
      chosen = inc
      self._chal, self._chal_n, self._chal_t0 = -1, 0, None
    elif cost[best] + P['switch_margin'] < cost[inc]:
      bp = int(part[best])
      if self._chal != bp:
        self._chal, self._chal_n, self._chal_t0 = bp, 0, t
      self._chal_n += 1
      won_s = t - self._chal_t0
      if self._chal_n >= P['switch_min_fixes'] and won_s >= P['switch_s']:
        chosen = best
        note = f'switched to a better line after {won_s:.1f} s'
        self._chal, self._chal_n, self._chal_t0 = -1, 0, None
      else:
        chosen = inc
        state = 'hysteresis'
        note = f'holding the current line; a better one has won for {won_s:.1f}/{P["switch_s"]} s'
    else:
      chosen = inc
      self._chal, self._chal_n, self._chal_t0 = -1, 0, None

    # Lines with a different limit that touch the chosen one at the car are not rivals in the "which road" sense:
    #  * sequential - the next / previous zone of the same road (one of the two is past its end, within the
    #    boundary margin): the boundary rule above decides, so it does not lower the confidence;
    #  * co-located - TfNSW draws a (One Way) Variable zone ON TOP of the static zone of the same carriageway.
    #    Which one wins is a rule, never an accident of cost or incumbency: the HIGHER limit of a group that
    #    contains a Variable line (owner, 2026-09-29: the lower one is a peak-time reduction).
    speeds = L['part_speed'][cparts].astype(np.int64)
    colo_note = None
    colo_group = np.zeros(0, np.int64)
    seq_group = np.zeros(0, np.int64)
    cs = int(L['part_speed'][int(part[chosen])])
    dif = cand[speeds != cs]
    if len(dif):
      pd_ = np.hypot(px[dif] - px[chosen], py[dif] - py[chosen])
      cosang = np.abs(dx[dif] * dx[chosen] + dy[dif] * dy[chosen]) / np.maximum(slen[dif] * slen[chosen], 1e-9)
      endish = (first & (traw <= 0.0)) | (last & (traw >= 1.0))  # at or past an end of its line
      beyond = endish[dif] | endish[chosen]
      seq_group = dif[beyond & (pd_ <= margin + P['colocated_m']) & (cosang >= math.cos(math.radians(30.0)))]
      colo = dif[~beyond & (pd_ <= P['colocated_m']) & (cosang >= math.cos(math.radians(P['colocated_deg'])))]
      if len(colo) and not heading_ok:
        # no heading: a One Way line's direction cannot be checked, so it is not on top of this road unless it is
        # the line already being followed (picked while the heading was known)
        colo = colo[~oneway[colo] | (part[colo] == prev_part)]
      if len(colo):
        # ... and it must be on top 15 m back too: a line that only starts here (branches off) is not (the rule
        # _effective_limit applies while dead reckoning)
        cu = np.array([dx[chosen], dy[chosen]]) / max(float(slen[chosen]), 1e-9)
        if heading_ok:
          dirs = [cu if bool(fwd[chosen]) else -cu]
        elif self._ctx is not None and self._ctx.get('part') == int(part[chosen]) and self._ctx.get('fwd_known') is not None:
          dirs = [cu if self._ctx['fwd_known'] else -cu]
        else:
          dirs = [cu, -cu]  # direction unknown: on top both ways
        keep = self._present_along([int(part[c]) for c in colo], lat_e6 + py[chosen] / ky, lon_e6 + px[chosen] / kx, dirs)
        colo = colo[np.array([int(part[c]) in keep for c in colo], bool)]
      if len(colo):
        # the rest of the group: lines of the chosen line's limit lying on it too
        same = cand[(speeds == cs) & (cand != chosen)]
        if len(same):
          pds = np.hypot(px[same] - px[chosen], py[same] - py[chosen])
          cs2 = np.abs(dx[same] * dx[chosen] + dy[same] * dy[chosen]) / np.maximum(slen[same] * slen[chosen], 1e-9)
          same = same[~endish[same] & (pds <= P['colocated_m']) & (cs2 >= math.cos(math.radians(P['colocated_deg'])))]
        group = np.concatenate([[chosen], colo, same]).astype(np.int64)
        gtypes = L['part_type'][part[group]].astype(np.int64)
        gspeeds = L['part_speed'][part[group]].astype(np.int64)
        describe = ', '.join(f'{TYPES[int(ty)]} {int(s)}' for ty, s in zip(gtypes, gspeeds, strict=True))
        pick, resolved = self._pick_colocated([int(part[g]) for g in group])
        if resolved:
          pk = [g for g in group if int(part[g]) == pick]
          newc = min(pk, key=lambda g: cost[g])
          colo_note = f'co-located lines ({describe}): the higher limit of a Variable-over-static pair is published'
          chosen = newc
          colo_group = group
        else:
          colo_note = f'co-located lines disagree ({describe})'

    cp = int(part[chosen])
    self._set_current(cp, t)
    cur_s = t - self._cur_t0

    # confidence
    my_speed = int(L['part_speed'][cp])
    rmask = speeds != my_speed
    if len(colo_group):
      rmask &= ~np.isin(cand, colo_group)
    if len(seq_group):
      rmask &= ~np.isin(cand, seq_group)
    rival = cand[rmask]
    dd = float(d[chosen])
    rival_kph = rival_gap = None
    if len(rival):
      gap = float(cost[rival[0]] - cost[chosen])
      rival_kph = int(L['part_speed'][int(part[rival[0]])])
      rival_gap = round(gap, 3)
      if gap >= P['clear_gap']:
        self._cur_clear = True
      if gap >= P['tie_gap']:
        self._contra_t0 = None
        if self._cur_clear and cur_s >= P['incumbent_min_s']:
          gap += P['incumbent_bonus']
        q_amb = min(max(gap / P['amb_gap'], 0.0), 1.0)
      else:
        # a rival as cheap or cheaper: no bonus, ever. Only a line that is established and won clearly rides out
        # a short contrary spell (GPS noise; roads are not left without a junction); a line picked on a near
        # tie is withheld.
        if self._contra_t0 is None:
          self._contra_t0 = t
        if self._cur_clear and cur_s >= P['est_s'] and (t - self._contra_t0) < P['switch_s']:
          q_amb = P['hysteresis_q']
          if state == 'matched':
            state = 'hysteresis'
            note = (note + '; ' if note else '') + f'a {rival_kph} km/h line is as close; holding the established line for up to {P["switch_s"]} s'
        else:
          q_amb = min(max(gap / P['amb_gap'], 0.0), 1.0)
    else:
      q_amb = 1.0
      self._cur_clear = True
      self._contra_t0 = None
    q_dist = 1.0 if dd <= 10.0 else max(0.3, 1.0 - 0.7 * (dd - 10.0) / max(R - 10.0, 1.0))
    if heading_ok:
      e = float(err[chosen])
      q_head = 1.0 if e <= 15.0 else max(0.4, 1.0 - 0.6 * (e - 15.0) / 30.0)
    else:
      q_head = 0.8 if cur_s >= P['head_conf_s'] else 0.6
    conf = q_dist * q_head * q_amb
    level = 'high' if conf >= 0.8 else 'medium' if conf >= P['min_conf'] else 'low'

    # overlays on the snapped point
    ux, uy = float(dx[chosen] / max(slen[chosen], 1e-9)), float(dy[chosen] / max(slen[chosen], 1e-9))
    fwd_now = bool(fwd[chosen]) if heading_ok else None
    fwd_heading = float(bearing_deg) if heading_ok else None
    ovs = self._overlays_at(lat_e6, lon_e6, float(px[chosen]), float(py[chosen]), ux, uy, own_part=int(part[chosen]))
    sch = self._school_eval(ovs, unix_time, fwd_heading)

    ptype = int(L['part_type'][cp])
    base = my_speed
    is_ow = bool(oneway[chosen])
    r = _blank()
    r.update(
      base_kph=base,
      zone_type=TYPES[ptype] if ptype < len(TYPES) else str(ptype),
      confidence=round(conf, 3),
      confidence_level=level,
      state=state,
      dist_m=round(dd, 1),
      heading_err_deg=round(float(err[chosen]), 1) if heading_ok else None,
      zone_id=cp,
      feature_id=int(L['part_feat'][cp]),
      direction=DIR_NAMES[int(pdir[chosen])],
      one_way_undocumented=is_ow,
      n_candidates=len(cand),
      radius_m=round(R, 1),
      variable=ptype == T_VARIABLE,
      rival_kph=rival_kph,
      rival_gap=rival_gap,
      colocated=('resolved' if len(colo_group) else 'unresolved') if colo_note else None,
    )
    r.update(sch)
    r['overlays'] = [{"type": TYPES[o['type']], "kph": o['speed'], "school_row": o['school']} for o in ovs]
    reasons = []
    if note:
      reasons.append(note)
    if colo_note:
      reasons.append(colo_note)
    if rival_kph is not None and q_amb < 1.0:
      reasons.append(f'rival line at {rival_kph} km/h, cost gap {rival_gap:.2f}')
    if not heading_ok:
      reasons.append('no heading (slow or missing bearing)')
    if is_ow:
      reasons.append('One Way line: matched along its digitized direction (TfNSW coding undocumented)')
    if ptype == T_VARIABLE:
      reasons.append('Variable zone: static value, the signs may show less')
    withhold = sch['school_state'] == 'unknown' and P['school_unknown_policy'] == 'withhold' and bool(sch['school_kph'])
    if conf < P['min_conf']:
      state = 'ambiguous' if q_amb < 0.5 else 'low_confidence'
      r['state'] = state
      r['limit_kph'] = None
      reasons.insert(0, f'confidence {conf:.2f} < {P["min_conf"]}; best guess {self._apply(base, sch)} km/h not published')
      r['candidate_kph'] = self._apply(base, sch)
    elif withhold:
      # a school zone whose hours cannot be judged (the clock is not set): neither the base limit (too high if the
      # zone is active) nor the school limit (all night) - nothing, as for any other ambiguity
      r['state'] = 'ambiguous'
      r['limit_kph'] = None
      r['candidate_kph'] = min(base, int(sch['school_kph']))
      reasons.insert(0, f'school zone state unknown ({sch["school_reason"]}): {base} or {sch["school_kph"]} km/h, not published')
    else:
      r['limit_kph'] = self._apply(base, sch)
    r['reason'] = '; '.join(reasons) if reasons else 'matched'

    prev = self._ctx
    if fwd_now is not None:
      fwd_known = fwd_now
    elif prev is not None and prev['part'] == cp:
      fwd_known = prev.get('fwd_known')  # slow on the same line: the direction has not changed
    else:
      fwd_known = None
    ctx = {
      "part": cp,
      "sv": int(sv[chosen]),
      "t": float(tcl[chosen]),
      "lat_e6": lat_e6,
      "lon_e6": lon_e6,
      "px": float(px[chosen]),
      "py": float(py[chosen]),
      "ux": ux,
      "uy": uy,
      "fwd": fwd_now,
      "fwd_known": fwd_known,
      "fwd_heading": fwd_heading,
      "overlays": ovs,
      "unix": unix_time,
      "spd": spd,
      "kph": base,
    }
    self._ctx = ctx
    if r['limit_kph'] is not None:
      self._last_good = (r, ctx, t, self._odo)
    return r

  # ------------------------------------------------------------------ overlays
  def _overlays_at(self, lat_e6, lon_e6, qx, qy, ux, uy, own_part=None):
    """Overlay lines (School / School Bus / Wet Weather) lying on the matched base line at the snapped point
    (qx, qy) meters from (lat_e6, lon_e6), parallel to the base segment. With own_part, an overlay that lies
    clearly closer to ANOTHER parallel base line belongs to that line (a school zone on the street above a
    tunnel, or on a service road beside a motorway) and is skipped."""
    P = self.p
    L = self.L['o']
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(lat_e6 * 1e-6))
    ky = 1e-6 * M_PER_DEG_LAT
    clat = lat_e6 + int(round(qy / ky))
    clon = lon_e6 + int(round(qx / kx))
    seg = self._segments(L, clat, clon, P['ov_tol_m'] + P['ov_slack_m'])
    if seg is None:
      return []
    sv, part, ax, ay, bx, by, _, _ = seg
    dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by)
    slack = P['ov_slack_m'] / np.maximum(slen, 1e-6)
    within = (traw >= -slack) & (traw <= 1 + slack)
    with np.errstate(invalid='ignore', divide='ignore'):
      cosang = np.abs(dx * ux + dy * uy) / np.maximum(slen, 1e-9)
    par = cosang >= math.cos(math.radians(P['ov_max_angle_deg']))
    k = np.nonzero((d <= P['ov_tol_m']) & within & par)[0]
    out = []
    seen = set()
    for i in k[np.argsort(d[k])]:
      pp = int(part[i])
      if pp in seen:
        continue
      seen.add(pp)
      if own_part is not None and self._overlay_elsewhere(clat, clon, float(px[i]), float(py[i]), float(d[i]), ux, uy, own_part):
        continue
      out.append(
        {
          "part": pp,
          "type": int(L['part_type'][pp]),
          "speed": int(L['part_speed'][pp]),
          "dir": int(L['part_dir'][pp]),
          "school": int(L['part_school'][pp]),
          "brg": float(math.degrees(math.atan2(dx[i], dy[i])) % 360.0),
          "lat": clat,
          "lon": clon,
        }
      )
    return out

  def _overlay_elsewhere(self, clat, clon, ox, oy, d_own, ux, uy, own_part):
    """True if the overlay point (ox, oy) m from (clat, clon) - own_part's point is the origin, d_own away - lies at
    least ov_other_margin_m closer to another parallel base line than to own_part. A relative test at any distance:
    the M4 East line runs 0.55-0.70 m from St Mary's (Concord) School line in plan, and the school's own street
    0.16 m from it (the old test ignored everything under 1 m). A line that is the same road drawn twice (co-located
    with own_part here AND 15 m either way along it, e.g. a Variable line on a static one) is not 'another' line."""
    P = self.p
    B = self.L['b']
    seg = self._segments(B, clat, clon, d_own + 1.0)
    if seg is None:
      return False
    sv, sp, ax, ay, bx, by, _, _ = seg
    dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by, ox, oy)
    with np.errstate(invalid='ignore', divide='ignore'):
      par = np.abs(dx * ux + dy * uy) / np.maximum(slen, 1e-9) >= math.cos(math.radians(25.0))
    closer = sorted({int(q) for q in sp[(sp != own_part) & par & (d <= d_own - P['ov_other_margin_m'])]})
    if not closer:
      return False
    # the same road twice: close to own_part's line 15 m back AND 15 m ahead as well (only the sides own_part reaches)
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(clat * 1e-6))
    ky = 1e-6 * M_PER_DEG_LAT
    same: set[int] | None = None
    for sgn in (-1.0, 1.0):
      la, lo = clat + sgn * 15.0 * uy / ky, clon + sgn * 15.0 * ux / kx
      s2 = self._segments(B, int(round(la)), int(round(lo)), P['colocated_m'] + 1.0)
      if s2 is None:
        continue
      q = (lo - round(lo)) * kx, (la - round(la)) * ky
      d2 = self._project(s2[2], s2[3], s2[4], s2[5], q[0], q[1])[-1]
      own_d = d2[s2[1] == own_part]
      if len(own_d) == 0 or float(own_d.min()) > P['colocated_m'] + 0.5:
        continue  # own_part does not reach there
      near = {int(x) for x in s2[1][d2 <= float(own_d.min()) + P['colocated_m'] + 0.5]}
      same = near if same is None else (same & near)
    if same is None:
      return True  # nothing to compare along the road: the distances decide
    return any(q not in same for q in closer)

  def school_window(self, row):
    S = self.S
    if row < 0:
      return STD_TIMES
    return int(S['am0'][row]), int(S['am1'][row]), int(S['pm0'][row]), int(S['pm1'][row])

  def school_name(self, row):
    if row < 0:
      return None
    o = self.S['name_off']
    return bytes(np.asarray(self.S['name_blob'][o[row] : o[row + 1]])).decode('utf-8', 'replace')

  def school_active(self, row, unix_time, lat_e6=None, lon_e6=None):
    """-> ('active' | 'inactive' | 'unknown', why). row = school-table row or -1 (a School line that joined no
    polygon: TfNSW standard times, division unknown, tz from location)."""
    if unix_time is None or not math.isfinite(unix_time) or unix_time < CLOCK_MIN_UNIX:
      return 'unknown', 'clock not valid'
    if row >= 0:
      tz = int(self.S['tz'][row])
      div = 'western' if int(self.S['late'][row]) else 'eastern'
    else:
      tz = tz_for_location((lat_e6 or 0) * 1e-6, (lon_e6 or 0) * 1e-6)
      div = None  # unknown division: the calendar's conservative union
    loc = local_datetime(float(unix_time), tz)
    assumed = ''
    try:
      day = self.calendar(loc.date(), division=div)
    except Exception as e:
      day, assumed = None, f' (calendar error {e!r})'
    if day is None:
      # A date the calendar does not cover (school_days.py and the index's calendar both end; a software update adds
      # the next year) or a broken calendar. Not 'unknown' - that published the base limit through every school zone
      # once the calendar lapsed. School zones run on weekdays, so every weekday is taken as a school day: the
      # zone is then also applied on holidays, the lower (safe) error.
      assumed = assumed or f' (calendar has no answer for {loc.date()}: every weekday taken as a school day)'
      day = loc.weekday() < 5
    if not day:
      return 'inactive', f'{loc.date()} is not a school-zone day{assumed}'
    mins = loc.hour * 60 + loc.minute + loc.second / 60.0
    am0, am1, pm0, pm1 = self.school_window(row)
    if am0 <= mins < am1 or pm0 <= mins < pm1:
      return 'active', (
        f'{loc:%a %H:%M} inside {am0 // 60:02d}:{am0 % 60:02d}-{am1 // 60:02d}:{am1 % 60:02d} / '
        + f'{pm0 // 60:02d}:{pm0 % 60:02d}-{pm1 // 60:02d}:{pm1 % 60:02d}{assumed}'
      )
    return 'inactive', f'{loc:%a %H:%M} outside the school-zone hours{assumed}'

  def _school_eval(self, ovs, unix_time, heading):
    best = None
    state = 'none'
    why = None
    name = None
    zid = None
    notes = []
    for o in ovs:
      if o['type'] == T_SCHOOL:
        if o['dir'] == DIR_ONE_WAY and heading is not None:
          diff = abs((heading - o['brg'] + 180.0) % 360.0 - 180.0)
          if diff > 90.0:
            notes.append('One Way school line, other direction')
            continue
        st, w = self.school_active(o['school'], unix_time, o['lat'], o['lon'])
        rank = {'active': 3, 'unknown': 2, 'inactive': 1}[st]
        if best is None or rank > best[0] or (rank == best[0] and o['speed'] < best[1]):
          best = (rank, o['speed'])
          state, why = st, w
          name = self.school_name(o['school']) if o['school'] >= 0 else None
          zid = int(self.S['zone_id'][o['school']]) if o['school'] >= 0 else None
          if o['school'] < 0:
            why = (why or '') + ' (line has no TfNSW polygon: standard times assumed)'
      elif o['type'] == T_SCHOOL_BUS:
        notes.append(f'School Bus {o["speed"]} km/h overlay not applied (depends on bus lights)')
      elif o['type'] == T_WET:
        notes.append(f'Wet Weather {o["speed"]} km/h overlay not applied (dry value published)')
    apply = state == 'active' or (state == 'unknown' and self.p['school_unknown_policy'] == 'apply')
    return {
      "school_active": state == 'active',
      "school_apply": apply,
      "school_state": state,
      "school_name": name,
      "school_zone_id": zid,
      "school_kph": best[1] if best else None,
      "school_reason": why,
      "overlay_notes": notes,
    }

  @staticmethod
  def _apply(base, sch):
    if base is None:
      return None
    if sch.get('school_apply') and sch.get('school_kph'):
      return min(base, sch['school_kph'])
    return base

  # ------------------------------------------------------------------ look-ahead
  def _horizon(self, spd):
    P = self.p
    return float(min(max(P['lookahead_s'] * (spd or 0.0), P['lookahead_min_m']), P['lookahead_max_m']))

  def lookahead(self, horizon_m=None, unix_time=None):
    """The next change of the published limit ahead, along the matched line and a UNIQUE continuation of it
    (or the dead-reckoned path while every hypothesis agrees). -> dict with the ahead_* keys of RESULT_KEYS
    (ahead_kph None = no change found within ahead_path_m; ahead_stop says why the walk ended)."""
    out = dict.fromkeys(('ahead_kph', 'ahead_dist_m', 'ahead_kind', 'ahead_zone_type', 'ahead_school_name', 'ahead_path_m', 'ahead_stop'))
    try:
      if self._dr is not None and not self._dr['ended'] and self._bad_since is not None:
        res = self._lookahead_dr(horizon_m, unix_time)
      elif self._bad_since is None and self._ctx is not None:
        res = self._lookahead_live(horizon_m, unix_time)
      else:
        res = None
    except Exception as e:  # the look-ahead is advisory: never fail the update for it
      out['ahead_stop'] = f'error {e!r}'
      return out
    if res is None:
      out['ahead_stop'] = 'no match / no direction'
      return out
    out.update(res)
    return out

  def _walk_limits(self, la, lo, parts, cur_kph, H):
    """Sample the effective limit every lookahead_step_m along a walked path (micro-degree float arrays with the
    part of each vertex). -> (dist, kph, zone type) of the first change, or None."""
    P = self.p
    L = self.L['b']
    if len(la) < 2:
      return None
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(la[0] * 1e-6))
    ky = 1e-6 * M_PER_DEG_LAT
    seglen = np.hypot(np.diff(lo) * kx, np.diff(la) * ky)
    cum = np.concatenate([[0.0], np.cumsum(seglen)])
    up_to = min(H, cum[-1])
    # the stage (part) boundaries first, then a sample grid: a boundary is where a zone changes
    marks = set(np.arange(P['lookahead_step_m'], up_to + 1e-6, P['lookahead_step_m']).round(1).tolist())
    for j in range(1, len(parts)):
      if parts[j] != parts[j - 1] and cum[j - 1] <= up_to:
        marks.add(round(float(cum[j - 1]) + 1.0, 1))
    for s in sorted(marks):
      if s > up_to:
        break
      k = min(max(int(np.searchsorted(cum, s, side='right')) - 1, 0), len(seglen) - 1)
      if seglen[k] <= 0:
        continue
      f = (s - cum[k]) / seglen[k]
      plat = la[k] + f * (la[k + 1] - la[k])
      plon = lo[k] + f * (lo[k + 1] - lo[k])
      ux = (lo[k + 1] - lo[k]) * kx / seglen[k]
      uy = (la[k + 1] - la[k]) * ky / seglen[k]
      q = int(parts[k + 1])
      kph, src = self._effective_limit(q, plat, plon, ux, uy)
      if kph is not None and kph != cur_kph:
        return float(s), kph, TYPES[int(L['part_type'][src])]
    return None

  def _lookahead_live(self, horizon_m, unix_time):
    ctx = self._ctx
    if ctx is None or ctx['fwd'] is None:
      return None
    H = float(horizon_m or self._horizon(ctx['spd']))
    L = self.L['b']
    pv0 = L['part_v0']
    vlat, vlon = L['vlat'], L['vlon']
    part = ctx['part']
    cur_kph = ctx['kph']
    unix = unix_time if unix_time is not None else ctx['unix']
    spd = max(ctx['spd'] or 0.0, 5.0)
    a, b = int(pv0[part]), int(pv0[part + 1])
    s = ctx['sv']
    start_lat = ctx['lat_e6'] + ctx['py'] / (1e-6 * M_PER_DEG_LAT)
    start_lon = ctx['lon_e6'] + ctx['px'] / (1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(ctx['lat_e6'] * 1e-6)))
    idx = np.arange(s + 1, b) if ctx['fwd'] else np.arange(s, a - 1, -1)
    path_lat = [np.array([start_lat]), vlat[idx].astype(np.float64)]
    path_lon = [np.array([start_lon]), vlon[idx].astype(np.float64)]
    path_part = [np.array([part]), np.full(len(idx), part)]
    walked = self._pathlen(np.concatenate(path_lat), np.concatenate(path_lon))
    if len(idx):
      end_lat, end_lon = float(vlat[idx[-1]]), float(vlon[idx[-1]])
    else:
      end_lat, end_lon = start_lat, start_lon
    prev_lat = float(vlat[idx[-2]]) if len(idx) >= 2 else start_lat
    prev_lon = float(vlon[idx[-2]]) if len(idx) >= 2 else start_lon
    walked, stop = self._walk_unique(part, end_lat, end_lon, prev_lat, prev_lon, walked, H, path_lat, path_lon, path_part)
    la = np.concatenate(path_lat)
    lo = np.concatenate(path_lon)
    pp = np.concatenate(path_part)
    return self._ahead_from_path(la, lo, pp, cur_kph, H, walked, stop, unix, spd, ctx['fwd_heading'])

  def _walk_unique(self, part, end_lat, end_lon, prev_lat, prev_lon, walked, H, path_lat, path_lon, path_part):
    """Extend a walked path (lists of arrays, appended in place) through UNIQUE continuations (_next_line) until
    the horizon. -> (walked, why the walk stopped)."""
    L = self.L['b']
    pv0 = L['part_v0']
    vlat, vlon = L['vlat'], L['vlon']
    hops = 0
    while walked < H and hops < 12:
      hops += 1
      nxt, why = self._next_line(part, end_lat, end_lon, prev_lat, prev_lon)
      if nxt is None:
        return walked, why
      q, vnext, fwd = nxt
      a2, b2 = int(pv0[q]), int(pv0[q + 1])
      ids = np.arange(vnext, b2) if fwd else np.arange(vnext, a2 - 1, -1)
      if len(ids) == 0:
        return walked, 'degenerate successor'
      la = np.concatenate([[end_lat], vlat[ids].astype(np.float64)])
      lo = np.concatenate([[end_lon], vlon[ids].astype(np.float64)])
      walked += self._pathlen(la, lo)
      path_lat.append(la[1:])
      path_lon.append(lo[1:])
      path_part.append(np.full(len(ids), q))
      prev_lat, prev_lon = (float(la[-2]), float(lo[-2]))
      end_lat, end_lon = float(la[-1]), float(lo[-1])
      part = q
    return walked, 'horizon'

  def _ahead_from_path(self, la, lo, pp, cur_kph, H, walked, stop, unix, spd, heading):
    nxt: dict[str, Any] | None = None
    ch = self._walk_limits(la, lo, pp, cur_kph, H)
    if ch is not None:
      nxt = {"ahead_kph": ch[1], "ahead_dist_m": round(ch[0], 1), "ahead_kind": 'zone', "ahead_zone_type": ch[2], "ahead_school_name": None}
      stop = 'found'
    up_to = min(H, nxt['ahead_dist_m'] if nxt else walked)
    sch = self._school_ahead(la, lo, up_to, unix, spd, heading)
    if sch is not None and (nxt is None or sch['dist_m'] < nxt['ahead_dist_m']) and cur_kph is not None and sch['next_kph'] < cur_kph:
      nxt = {
        "ahead_kph": sch['next_kph'],
        "ahead_dist_m": sch['dist_m'],
        "ahead_kind": 'school',
        "ahead_zone_type": 'School',
        "ahead_school_name": sch['school_name'],
      }
      stop = 'found'
    out = dict(nxt) if nxt else {}
    out['ahead_path_m'] = round(min(walked, H), 1)
    out['ahead_stop'] = stop
    return out

  def _lookahead_dr(self, horizon_m, unix_time):
    D = self._dr
    if D is None or not D['hyps'] or self._last_good is None:
      return None
    best = D['hyps'][0]['cost']
    kph = {h.get('cur_kph') for h in D['hyps'] if not D['yaw'] or h['cost'] <= best + self.p['dr_decide_gap']}
    if len(kph) != 1 or None in kph:
      return {"ahead_path_m": 0.0, "ahead_stop": 'dead reckoning: paths disagree'}
    h = D['hyps'][0]
    along = self._odo - self._last_good[3]
    s0 = (h['scale'] or 1.0) * along
    H = float(horizon_m or self._horizon(self._last_spd))
    cum = np.asarray(h['cum'])
    if len(cum) < 2:
      return None
    keep = cum > s0
    parts = np.array([self._stage_part(h, s) for s in cum])
    path_lat = [np.array([h['cur_lat']]), np.asarray(h['lat'])[keep]]
    path_lon = [np.array([h['cur_lon']]), np.asarray(h['lon'])[keep]]
    path_part = [np.array([h['cur_part']]), parts[keep]]
    walked = float(cum[-1] - s0)
    stop = 'horizon'
    if walked < H:
      # beyond the part of the path built so far: on through unique continuations, as for a live match
      if h['done']:
        stop = 'end of the dead-reckoned path'
      else:
        la_, lo_ = h['lat'], h['lon']
        walked, stop = self._walk_unique(h['end_part'], la_[-1], lo_[-1], la_[-2], lo_[-2], walked, H, path_lat, path_lon, path_part)
    spd = max(self._last_spd or 0.0, 5.0)
    ux, uy = h['cur_u']
    return self._ahead_from_path(
      np.concatenate(path_lat),
      np.concatenate(path_lon),
      np.concatenate(path_part),
      h['cur_kph'],
      H,
      walked,
      stop,
      unix_time,
      spd,
      math.degrees(math.atan2(ux, uy)) % 360.0,
    )

  @staticmethod
  def _stage_part(h, s):
    part = h['stages'][0][1]
    for s0, q in h['stages']:
      if s0 <= s - 1e-6:
        part = q
    return part

  @staticmethod
  def _pathlen(la, lo):
    if len(la) < 2:
      return 0.0
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(la[0] * 1e-6))
    return float(np.hypot(np.diff(lo) * kx, np.diff(la) * 1e-6 * M_PER_DEG_LAT).sum())

  def _next_line(self, part, end_lat, end_lon, prev_lat, prev_lon, r_m=4.0):
    """The base line the road UNIQUELY continues on beyond the end point (micro-degrees) of `part`, arriving from
    prev (look-ahead). Options are lines leaving a vertex near the end point AND lines passing straight through it
    (a static zone under an ending Variable one). It must be unique within 30 deg, and no line with a different
    limit may leave within 45 deg - except lines that all run the same way (within 5 deg) from the point with a
    Variable line among them: the co-location rule then picks the higher limit.
    -> ((part, next vertex, forward?), None) or (None, why)."""
    L = self.L['b']
    P = self.p
    elat, elon = int(round(end_lat)), int(round(end_lon))
    seg = self._segments(L, elat, elon, r_m)
    if seg is None:
      return None, 'no unique line beyond the end of the held one (nothing there)'
    sv, sp, ax, ay, bx, by, kx, ky = seg
    arr = math.degrees(math.atan2((end_lon - prev_lon) * kx, (end_lat - prev_lat) * ky)) % 360.0
    dx, dy, slen, traw, tcl, px, py, d = self._project(ax, ay, bx, by)
    dA = np.hypot(ax, ay)
    dB = np.hypot(bx, by)
    opts = []  # (part, next vertex, forward?, bearing)
    for i in range(len(sv)):
      q = int(sp[i])
      if q == part:
        continue
      v = int(sv[i])
      ow = int(L['part_dir'][q]) == DIR_ONE_WAY
      bf = math.degrees(math.atan2(bx[i] - ax[i], by[i] - ay[i])) % 360.0
      bb = (bf + 180.0) % 360.0
      if dA[i] <= r_m:
        opts.append((q, v + 1, True, bf))
      if dB[i] <= r_m and not ow:
        opts.append((q, v, False, bb))
      if 0.0 < traw[i] < 1.0 and d[i] <= r_m and dA[i] > r_m and dB[i] > r_m:
        opts.append((q, v + 1, True, bf))
        if not ow:
          opts.append((q, v, False, bb))
    if not opts:
      return None, 'no unique line beyond the end of the held one (nothing there)'

    def adiff(b1, b2):
      return abs((b1 - b2 + 180.0) % 360.0 - 180.0)

    good = sorted(((adiff(o[3], arr), o) for o in opts if adiff(o[3], arr) <= 30.0), key=lambda x: x[0])
    if not good:
      return None, 'no unique line beyond the end of the held one (nothing within 30 deg)'
    near = [o for o in opts if adiff(o[3], arr) <= 45.0]
    pick = good[0][1]
    if len({int(L['part_speed'][o[0]]) for o in near}) > 1:
      var = [o for o in near if int(L['part_type'][o[0]]) == T_VARIABLE]
      if P['variable_overrides'] and var and all(any(adiff(o[3], w[3]) <= 5.0 for w in var) for o in near):
        pk, ok = self._pick_colocated(sorted({o[0] for o in near}))
        pko = [o for _, o in good if o[0] == pk]
        if ok and pko:
          return (pko[0][0], pko[0][1], pko[0][2]), None
      return None, 'no unique line beyond the end of the held one (lines with different limits continue)'
    return (pick[0], pick[1], pick[2]), None

  def _school_ahead(self, la, lo, up_to_m, unix, spd, heading):
    if len(la) < 2 or up_to_m <= 0:
      return None
    kx = 1e-6 * M_PER_DEG_LON_EQ * math.cos(math.radians(la[0] * 1e-6))
    ky = 1e-6 * M_PER_DEG_LAT
    seglen = np.hypot(np.diff(lo) * kx, np.diff(la) * ky)
    cum = np.concatenate([[0.0], np.cumsum(seglen)])
    s = np.arange(0.0, min(up_to_m, cum[-1]) + 1e-6, 10.0)
    if len(s) == 0:
      return None
    slat = np.interp(s, cum, la)
    slon = np.interp(s, cum, lo)
    # cheap prefilter: which samples fall in (or next to) an overlay grid cell
    lat0, lon0, dla, dlo, ncol, pad = self._g
    r = ((slat - lat0) // dla).astype(np.int64)
    c = ((slon - lon0) // dlo).astype(np.int64)
    gk = self.L['o']['grid_keys']
    if len(gk) == 0:
      return None
    near = np.zeros(len(s), bool)
    for drr in (-1, 0, 1):
      for dcc in (-1, 0, 1):
        kk = ((r + drr) * ncol + (c + dcc)).astype(gk.dtype)
        pos = np.clip(np.searchsorted(gk, kk), 0, len(gk) - 1)
        near |= gk[pos] == kk
    seg_i = np.clip(np.searchsorted(cum, s, side='right') - 1, 0, len(seglen) - 1)
    for j in np.nonzero(near)[0]:
      i = seg_i[j]
      if seglen[i] <= 0:
        continue
      ux = (lo[i + 1] - lo[i]) * kx / seglen[i]
      uy = (la[i + 1] - la[i]) * ky / seglen[i]
      ovs = self._overlays_at(int(round(slat[j])), int(round(slon[j])), 0.0, 0.0, ux, uy)
      if not ovs:
        continue
      eta = None if unix is None else unix + s[j] / spd
      hd = math.degrees(math.atan2(ux, uy)) % 360.0
      sch = self._school_eval(ovs, eta, hd)
      if sch['school_state'] == 'active':
        return {"next_kph": sch['school_kph'], "dist_m": round(float(s[j]), 1), "kind": 'school', "zone_type": 'School', "school_name": sch['school_name']}
    return None
