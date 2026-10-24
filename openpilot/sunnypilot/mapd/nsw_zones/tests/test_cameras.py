"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(NSW-CAMERAS): the OSM camera layer - reading an Overpass answer (what counts, which way it looks), snapping onto
TfNSW's lines at build time (carriageway, approach, side road), the matcher's camera ahead on the road the car is on
(direction, carriageway, distance, continuation, waiting at a light, tunnels), the hold through a short gap, and the
builder CLI with and without a camera file.

Camera locations in real use: (c) OpenStreetMap contributors, ODbL. Contains data from Transport for NSW, CC BY 4.0,
modified. (Every fixture here is synthetic.)
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

import numpy as np

from openpilot.sunnypilot.mapd.nsw_zones import build_index as bi
from openpilot.sunnypilot.mapd.nsw_zones import cameras as cam
from openpilot.sunnypilot.mapd.nsw_zones import index as nzi
from openpilot.sunnypilot.mapd.nsw_zones import matcher as nz
from openpilot.sunnypilot.mapd.nsw_zones.tests.synth import NEVER, T_OUT, Car, MiniIndex, drive, feat, latlon, school_rec


# ----------------------------------------------------------------------------- a synthetic Overpass answer
class Osm:
  """Builds an Overpass-like JSON answer in the synth metric frame (meters east / north of the origin)."""

  def __init__(self):
    self.els = []
    self.nid = 1000

  def node(self, x, y, **tags):
    self.nid += 1
    lat, lon = latlon(x, y)
    e = {'type': 'node', 'id': self.nid, 'lat': lat, 'lon': lon}
    if tags:
      e['tags'] = tags
    self.els.append(e)
    return self.nid

  def cam(self, x, y, **tags):
    return self.node(x, y, highway='speed_camera', **tags)

  def way(self, ids, **tags):
    self.nid += 1
    byid = {e['id']: e for e in self.els if e['type'] == 'node'}
    self.els.append({'type': 'way', 'id': self.nid, 'nodes': list(ids), 'tags': {'highway': 'primary', **tags},
                     'geometry': [{'lat': byid[i]['lat'], 'lon': byid[i]['lon']} for i in ids]})
    return self.nid

  def rel(self, enforcement, device=None, frm=None, to=None, **tags):
    self.nid += 1
    mem = []
    for role, ref in (('device', device), ('from', frm), ('to', to)):
      if ref is not None:
        mem.append({'type': 'node', 'ref': ref, 'role': role})
    self.els.append({'type': 'relation', 'id': self.nid, 'members': mem, 'tags': {'type': 'enforcement', 'enforcement': enforcement, **tags}})
    return self.nid

  def doc(self):
    return {'version': 0.6, 'osm3s': {'timestamp_osm_base': '2026-10-24T01:59:36Z'}, 'elements': self.els}


def rows_by_id(rows):
  out = {}
  for r in rows:
    out.setdefault(r['id'], []).append(r)
  return out


class TestParseOverpass(unittest.TestCase):
  def test_what_counts_and_what_is_left_out(self):
    o = Osm()
    speed = o.cam(0, 0, maxspeed='60')
    red_dev = o.node(100, 5)
    o.rel('maxspeed;traffic_signals', red_dev, o.node(50, 0), o.node(100, 0))
    red_tag = o.cam(200, 0, description='Red light speed enforcement camera')
    school = o.cam(300, 0, name='School zone speed camera')
    school_cond = o.cam(400, 0, **{'maxspeed:conditional': '40 @ (Mo-Fr 08:00-09:30,14:30-16:00; SH off)'})
    o.cam(500, 0, name='Safe-T-Cam')
    o.cam(600, 0, name='Average Speed Safety Camera')
    o.rel('average_speed', o.cam(700, 0), o.node(650, 0), o.node(750, 0))
    o.rel('mobile_phone', o.node(800, 0), o.node(750, 0), o.node(850, 0))
    o.cam(900, 0, name='Waterway Enforcement Camera - 24 Hours')
    rows, rep = cam.parse_overpass(o.doc())
    by = rows_by_id(rows)
    self.assertEqual(by[speed][0]['kind'], cam.KIND_SPEED)
    self.assertEqual(by[speed][0]['maxspeed'], 60)
    self.assertEqual(by[red_dev][0]['kind'], cam.KIND_RED_LIGHT)
    self.assertEqual(by[red_tag][0]['kind'], cam.KIND_RED_LIGHT)
    self.assertEqual(by[school][0]['kind'], cam.KIND_SCHOOL)
    self.assertEqual(by[school_cond][0]['kind'], cam.KIND_SCHOOL)
    self.assertEqual(len(rows), 5)
    for why in ('safe_t_cam', 'average_speed', 'waterway'):
      self.assertTrue((why) in (rep['excluded']))
    self.assertEqual(rep['excluded']['average_speed'], 2)  # the named node and the relation
    self.assertEqual(rep['excluded']['mobile_phone'], 1)
    self.assertEqual(rep['osm_base'], '2026-10-24T01:59:36Z')
    self.assertEqual(rep['kinds'], {'speed': 1, 'red_light': 2, 'school': 2})

  def test_direction_sources(self):
    o = Osm()
    # a relation: from (0, 0) -> to (0, 100): northbound
    r_dev = o.node(5, 60)
    o.rel('maxspeed', r_dev, o.node(0, 0), o.node(0, 100))
    # a node of a one-way road drawn westward
    a, c_ow, b = o.node(400, 0), o.cam(300, 0), o.node(200, 0)
    o.way([a, c_ow, b], oneway='yes')
    # ... and of a road one-way AGAINST its drawing (oneway=-1), drawn eastward: traffic goes west
    a2, c_ow2, b2 = o.node(200, 300), o.cam(300, 300), o.node(400, 300)
    o.way([a2, c_ow2, b2], oneway='-1')
    # a direction tag in degrees and as a cardinal point, on two-way roads
    c_deg = o.cam(0, 500, direction='135')
    c_card = o.cam(100, 500, direction='SW')
    # forward / backward along the road it sits on (drawn northward)
    a3, c_fw, b3 = o.node(600, 0), o.cam(600, 50), o.node(600, 100)
    o.way([a3, c_fw, b3])
    a4, c_bw, b4 = o.node(700, 0), o.cam(700, 50), o.node(700, 100)
    o.way([a4, c_bw, b4])
    for e in o.els:
      if e['id'] == c_fw:
        e['tags']['direction'] = 'forward'
      if e['id'] == c_bw:
        e['tags']['direction'] = 'backward'
    c_none = o.cam(900, 900)
    rows, rep = cam.parse_overpass(o.doc())
    by = rows_by_id(rows)
    self.assertEqual(by[r_dev][0]['dsrc'], cam.DSRC_RELATION)
    self.assertLess(cam.adiff(by[r_dev][0]['dir'], 0.0), 1.0)
    self.assertEqual(by[c_ow][0]['dsrc'], cam.DSRC_ONEWAY)
    self.assertLess(cam.adiff(by[c_ow][0]['dir'], 270.0), 1.0)
    self.assertLess(cam.adiff(by[c_ow2][0]['dir'], 270.0), 1.0)
    self.assertEqual((by[c_deg][0]['dsrc'], by[c_deg][0]['dir']), (cam.DSRC_TAG, 135.0))
    self.assertEqual(by[c_card][0]['dir'], 225.0)
    self.assertEqual(by[c_fw][0]['dsrc'], cam.DSRC_WAYDIR)
    self.assertLess(cam.adiff(by[c_fw][0]['dir'], 0.0), 1.0)
    self.assertLess(cam.adiff(by[c_bw][0]['dir'], 180.0), 1.0)
    self.assertIsNone(by[c_none][0]['dir'])
    self.assertEqual(by[c_none][0]['dsrc'], cam.DSRC_NONE)
    self.assertIsNone(by[c_none][0]['axis'])
    self.assertLess(min(cam.adiff(by[c_fw][0]['axis'], 0.0), cam.adiff(by[c_fw][0]['axis'], 180.0)), 1.0)
    self.assertEqual(rep['direction_sources']['oneway'], 2)

  def test_mapped_twice_is_one_camera_and_two_relations_are_two_directions(self):
    o = Osm()
    dev = o.cam(0, 0)
    o.rel('maxspeed', dev, o.node(0, -100), o.node(0, 50))    # northbound
    o.rel('maxspeed', dev, o.node(0, 100), o.node(0, -50))    # southbound: the same pole, both ways
    twin = o.cam(6, 3, direction='0')                          # the same camera again as a plain node, 7 m away
    far = o.cam(0, 200)
    rows, rep = cam.parse_overpass(o.doc())
    by = rows_by_id(rows)
    self.assertEqual(sorted(round(r['dir']) % 360 for r in by[dev]), [0, 180])
    self.assertNotIn(twin, by)
    self.assertTrue((far) in (by))
    self.assertEqual(rep['dup_removed'], 1)

  def test_a_relation_with_no_device_stands_at_its_to_node(self):
    o = Osm()
    frm, to = o.node(0, 0), o.node(0, 2000)  # the Harbour Tunnel relations are mapped like this
    o.rel('maxspeed', None, frm, to, maxspeed='80')
    rows, rep = cam.parse_overpass(o.doc())
    self.assertEqual([r['id'] for r in rows], [to])
    self.assertLess(cam.adiff(rows[0]['dir'], 0.0), 1.0)
    self.assertEqual(rows[0]['maxspeed'], 80)
    self.assertEqual(rep['to_as_device'], 1)

  def test_not_an_overpass_answer(self):
    with self.assertRaises(ValueError):
      cam.parse_overpass({'remark': 'runtime error: timeout'})
    self.assertEqual(cam.parse_direction('both'), None)
    self.assertEqual(cam.parse_direction('NNW'), 337.5)
    self.assertEqual(cam.parse_maxspeed('80kmh'), 80)


# ----------------------------------------------------------------------------- snapping and the camera ahead
FEATS = [
  # a two-way 60 road along y=0, continuing as 70 from x=1000
  feat([(0, 0), (1000, 0)], 60, 'Ordinary Permanent'),
  feat([(1000, 0), (2000, 0)], 70, 'Ordinary Permanent'),
  # a cross street at x=500 (north-south)
  feat([(500, -400), (500, 400)], 50),
  # a divided road: two One Way carriageways 20 m apart (eastbound at y=-1000, westbound at y=-980)
  feat([(0, -1000), (2000, -1000)], 80, 'Ordinary Permanent', 'One Way'),
  feat([(2000, -980), (0, -980)], 80, 'Ordinary Permanent', 'One Way'),
  # a side road at y=-1500 running beside a main road at y=-1530 (30 m apart, both two-way)
  feat([(0, -1500), (2000, -1500)], 50),
  feat([(0, -1530), (2000, -1530)], 70, 'Ordinary Permanent'),
  # a 60 road with a school zone at x 300..500 (y=-2000)
  feat([(0, -2000), (1000, -2000)], 60, 'Ordinary Permanent'),
  feat([(300, -2000), (500, -2000)], 40, 'School'),
  # a tunnel: Variable 90, One Way eastbound (y=-3000)
  feat([(0, -3000), (4000, -3000)], 90, 'Variable', 'One Way'),
  # a divided road TfNSW draws as two Both Directions lines 13 m apart (y=-4000 and y=-3987)
  feat([(0, -4000), (2000, -4000)], 80, 'Ordinary Permanent'),
  feat([(0, -3987), (2000, -3987)], 80, 'Ordinary Permanent'),
]
SCHOOLS = [school_rec(77, 'CAMERA TEST SCHOOL', 280, 520, -2020, -1980)]


def camera_doc():
  o = Osm()
  ids = {}
  ids['east_only'] = o.cam(600, 6, direction='90')            # beside the 60 road, checks eastbound traffic
  ids['both_ways'] = o.cam(1200, -5)                          # on the 70 continuation, no direction
  ids['junction'] = o.node(505, 30)                           # red-light camera on the cross street's approach ...
  o.rel('traffic_signals', ids['junction'], o.node(500, 150), o.node(500, 5))  # ... southbound
  ids['eastbound_cw'] = o.cam(800, -1003)                     # on the eastbound carriageway, no direction
  ids['side_road'] = o.cam(1000, -1497)                       # on the side road
  ids['school'] = o.cam(400, -2004)                           # in the school zone: a school-zone camera by TfNSW
  ids['tunnel'] = o.cam(2500, -3002)                          # in the tunnel
  ids['nowhere'] = o.cam(5000, 5000)                          # on no line at all
  ids['divided_east'] = o.cam(700, -4001, direction='90')     # by the southern line, checks eastbound traffic
  ids['divided_any'] = o.cam(1500, -4001)                     # by the southern line, no direction
  return o.doc(), ids


class CameraIndex:
  """FEATS with the camera layer of camera_doc(), written to a temporary index file."""

  def __init__(self):
    self.mi = MiniIndex(FEATS, SCHOOLS)
    doc, self.ids = camera_doc()
    rows, self.parse_rep = cam.parse_overpass(doc)
    arrays = dict(self.mi.B['arrays'])
    c, self.snap_rep = bi.camera_arrays(arrays, rows, 'test', '2026-10-24T01:59:36Z', log=lambda s: None)
    arrays.update(c)
    bi.add_camera_meta(arrays, c)
    self.arrays = arrays
    self.path = os.path.join(self.mi.tmp, 'cams.npz')
    bi.write_index(arrays, self.path)

  def close(self):
    self.mi.close()


class TestSnapping(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.ci = CameraIndex()
    cls.a = nzi.load_index(cls.ci.path)

  @classmethod
  def tearDownClass(cls):
    cls.ci.close()

  def homes(self, key):
    a = self.a
    row = int(np.nonzero(a['c_osm'] == self.ci.ids[key])[0][0])
    e = range(int(a['c_home_v0'][row]), int(a['c_home_v0'][row + 1]))
    speeds = [int(a['b_part_speed'][int(a['c_home_part'][i])]) for i in e]
    dirs = [int(a['b_part_dir'][int(a['c_home_part'][i])]) for i in e]
    brg = [int(a['c_home_brg'][i]) for i in e]
    return row, speeds, dirs, brg

  def test_round_trip_and_left_out(self):
    self.assertNotIn(self.ci.ids['nowhere'], self.a['c_osm'].tolist())
    self.assertEqual(self.ci.snap_rep['no_line'], 1)
    self.assertEqual(len(self.a['c_osm']), 9)
    self.assertTrue(np.all(np.diff(self.a['c_bypart_part']) >= 0))
    doc = json.loads(bytes(self.a['cameras_json']).decode('utf-8'))
    self.assertEqual(doc['osm_base'], '2026-10-24T01:59:36Z')
    self.assertTrue('OpenStreetMap' in doc['attribution'])
    meta = nzi.read_meta(self.a)
    self.assertTrue('OpenStreetMap' in meta['attribution_long'] and 'Transport for NSW' in meta['attribution_long'])

  def test_a_camera_at_a_junction_is_on_its_approach_not_the_cross_street(self):
    _, speeds, _, brg = self.homes('junction')
    self.assertEqual(speeds, [50])  # the cross street it stands on, southbound
    self.assertLess(cam.adiff(brg[0], 180.0), 1.0)
    _, speeds, _, brg = self.homes('east_only')
    self.assertEqual(speeds, [60])
    self.assertLess(cam.adiff(brg[0], 90.0), 1.0)

  def test_one_carriageway(self):
    _, speeds, dirs, brg = self.homes('eastbound_cw')
    self.assertEqual((speeds, dirs, brg), ([80], [nz.DIR_ONE_WAY], [-1]))
    a = self.a
    row = int(np.nonzero(a['c_osm'] == self.ci.ids['eastbound_cw'])[0][0])
    part = int(a['c_home_part'][int(a['c_home_v0'][row])])
    first = int(a['b_part_v0'][part])
    self.assertLess(int(a['b_vlon'][first]), int(a['b_vlon'][first + 1]))  # the line drawn eastward

  def test_a_known_direction_takes_the_parallel_line_too(self):
    _, speeds, _, brg = self.homes('divided_east')
    self.assertEqual((speeds, brg), ([80, 80], [90, 90]))
    _, speeds, _, brg = self.homes('divided_any')
    self.assertEqual((speeds, brg), ([80], [-1]))  # no direction: only the nearest

  def test_school_zone_camera_by_tfnsw(self):
    row, _, _, _ = self.homes('school')
    self.assertEqual(int(self.a['c_kind'][row]), cam.KIND_SCHOOL)
    self.assertEqual(self.ci.snap_rep['school_from_tfnsw'], 1)


class TestCameraAhead(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.ci = CameraIndex()

  @classmethod
  def tearDownClass(cls):
    cls.ci.close()

  def m(self, **kw):
    return nz.Matcher(self.ci.path, calendar=NEVER, **kw)

  def last(self, pts, heading, speed=15.0, **kw):
    return drive(self.m(**kw), pts, heading, speed=speed, unix=T_OUT, lookahead=True)[-1]

  def test_result_keys(self):
    r = self.last([(100, 1), (115, 1)], 90.0)
    for k in ('camera_kind', 'camera_dist_m', 'camera_id', 'camera_dir_known', 'camera_dr', 'camera_stop', 'camera_walk_m'):
      self.assertTrue((k) in (r))
    self.assertTrue(set(r) >= set(nz.RESULT_KEYS))

  def test_ahead_in_its_direction_at_its_distance(self):
    r = self.last([(280, 1), (300, 1)], 90.0)
    self.assertEqual((r['camera_kind'], r['camera_id']), (cam.KIND_SPEED, self.ci.ids['east_only']))
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)
    self.assertTrue(r['camera_dir_known'])
    self.assertEqual(r['camera_stop'], 'found')

  def test_not_for_the_other_direction(self):
    r = self.last([(920, -1), (900, -1)], 270.0)  # westbound towards the eastbound-only camera at x=600
    self.assertEqual(r['camera_kind'], 0)
    self.assertNotEqual(r['camera_id'], self.ci.ids['east_only'])

  def test_beyond_the_look_and_once_passed(self):
    r = self.last([(100, 1), (120, 1)], 90.0)  # 480 m: beyond 400
    self.assertEqual(r['camera_kind'], 0)
    # the walk takes the rest of the 60 line (880 m), but the search, and what it says, stop at the look
    self.assertEqual((r['camera_stop'], r['camera_walk_m']), ('none within 400 m (horizon)', 400.0))
    r = self.last([(590, 1), (610, 1)], 90.0)  # just passed it
    self.assertNotEqual(r['camera_id'], self.ci.ids['east_only'])

  def test_on_through_the_continuation_and_both_ways_without_a_direction(self):
    r = self.last([(880, 1), (900, 1)], 90.0)  # across the 60 -> 70 change at x=1000
    self.assertEqual(r['camera_id'], self.ci.ids['both_ways'])
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)
    self.assertFalse(r['camera_dir_known'])
    r = self.last([(1520, -1), (1500, -1)], 270.0)
    self.assertEqual(r['camera_id'], self.ci.ids['both_ways'])
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)

  def test_distance_and_the_edge_of_the_look(self):
    r = self.last([(820, 1), (840, 1)], 90.0)
    self.assertEqual(r['camera_id'], self.ci.ids['both_ways'])
    self.assertAlmostEqual(r['camera_dist_m'], 360.0, delta=3.0)
    r = self.last([(760, 1), (780, 1)], 90.0)  # 420 m
    self.assertEqual(r['camera_kind'], 0)
    m = self.m(camera_ahead_m=500.0)
    r = drive(m, [(760, 1), (780, 1)], 90.0, speed=15.0, unix=T_OUT, lookahead=True)[-1]
    self.assertAlmostEqual(r['camera_dist_m'], 420.0, delta=3.0)

  def test_a_red_light_camera_on_the_approach_and_waiting_at_the_light(self):
    m = self.m()
    out = drive(m, [(501, 300), (501, 280), (501, 260)], 180.0, speed=15.0, unix=T_OUT, lookahead=True)
    r = out[-1]
    self.assertEqual((r['camera_kind'], r['camera_id']), (cam.KIND_RED_LIGHT, self.ci.ids['junction']))
    self.assertAlmostEqual(r['camera_dist_m'], 230.0, delta=3.0)
    # stopped at the line 20 m short of it: no heading, the direction on the line is kept
    r = drive(m, [(501, 50), (501, 50)], None, speed=0.0, unix=T_OUT + 10, t0=10.0, lookahead=True)[-1]
    self.assertEqual(r['camera_id'], self.ci.ids['junction'])
    self.assertAlmostEqual(r['camera_dist_m'], 20.0, delta=3.0)
    # on the cross street (the 60 road) through the junction: not its camera
    r = self.last([(400, 1), (420, 1)], 90.0)
    self.assertNotEqual(r['camera_id'], self.ci.ids['junction'])
    # northbound on its own street, away from it: not either
    r = self.last([(501, -100), (501, -80)], 0.0)
    self.assertNotEqual(r['camera_id'], self.ci.ids['junction'])

  def test_only_its_own_carriageway(self):
    r = self.last([(480, -999), (500, -999)], 90.0)
    self.assertEqual(r['camera_id'], self.ci.ids['eastbound_cw'])
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)
    r = self.last([(1120, -981), (1100, -981)], 270.0)  # westbound, 20 m away, the camera 300 m back east-to-west
    self.assertEqual(r['camera_kind'], 0)

  def test_a_divided_road_drawn_as_two_lines(self):
    r = self.last([(380, -3987), (400, -3987)], 90.0)  # eastbound on the northern line: the camera is by the other one
    self.assertEqual(r['camera_id'], self.ci.ids['divided_east'])
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)
    r = self.last([(1020, -3987), (1000, -3987)], 270.0)  # westbound: it checks the other way
    self.assertNotEqual(r['camera_id'], self.ci.ids['divided_east'])
    r = self.last([(1180, -3987), (1200, -3987)], 90.0)  # no direction: only on the line it stands on
    self.assertNotEqual(r['camera_id'], self.ci.ids['divided_any'])
    r = self.last([(1180, -4000), (1200, -4000)], 90.0)
    self.assertEqual(r['camera_id'], self.ci.ids['divided_any'])

  def test_never_from_the_road_beside(self):
    r = self.last([(780, -1531), (800, -1531)], 90.0)  # main road, the side-road camera 30 m to the left, 200 m on
    self.assertNotEqual(r['camera_id'], self.ci.ids['side_road'])
    r = self.last([(780, -1501), (800, -1501)], 90.0)  # on the side road itself
    self.assertEqual(r['camera_id'], self.ci.ids['side_road'])
    self.assertAlmostEqual(r['camera_dist_m'], 200.0, delta=3.0)

  def test_school_kind(self):
    r = self.last([(180, -1999), (200, -1999)], 90.0)
    self.assertEqual((r['camera_kind'], r['camera_id']), (cam.KIND_SCHOOL, self.ci.ids['school']))

  def test_through_a_tunnel_by_dead_reckoning(self):
    m = self.m()
    car = Car(m, [(0, -3000), (4000, -3000)], speed=25.0, lookahead=True)
    out = car.run(gps=lambda s: s < 1500, until=2400)
    dr = [r for r in out if r['state'] == 'dead_reckoning' and r['camera_kind']]
    self.assertTrue(dr, [(r['state'], r['camera_stop']) for r in out[-10:]])
    r = dr[0]
    self.assertTrue(r['camera_dr'])
    self.assertEqual(r['camera_id'], self.ci.ids['tunnel'])
    self.assertAlmostEqual(r['camera_dist_m'], 2500.0 - r['_s'], delta=25.0)

  def test_withheld_road_and_no_layer(self):
    m = self.m()
    out = m.camera_ahead('ambiguous')
    self.assertEqual(out['camera_kind'], 0)
    self.assertTrue(out['camera_stop'].startswith('not on a known road'))
    with MiniIndex(FEATS[:1]) as mi:
      m2 = nz.Matcher(mi.path, calendar=NEVER)
      self.assertEqual(m2.n_cameras, 0)
      r = drive(m2, [(280, 1), (300, 1)], 90.0, speed=15.0, unix=T_OUT, lookahead=True)[-1]
      self.assertEqual((r['camera_kind'], r['camera_stop']), (0, 'no camera layer'))
      self.assertEqual(r['limit_kph'], 60)

  def test_turning_off_before_it_ends_the_hold(self):
    """Review finding 3 (route 14, the Parker St school camera): the car turns off at the junction before the camera.
    The tick in the turn is unsure of the road and holds the camera; the first tick matched on the cross street, whose
    search runs past where the camera stood, drops it - not 3 s later."""
    m, h = self.m(), cam.CameraHold()
    shown = []
    fixes = [((400 + 20 * i, 1), 90.0) for i in range(5)] + [((501, 15), 45.0), ((501, 35), 0.0), ((501, 55), 0.0)]
    for i, ((x, y), hd) in enumerate(fixes):
      r = drive(m, [(x, y)], hd, speed=15.0, t0=float(i), unix=T_OUT + i, lookahead=True)[-1]
      shown.append((r['state'], h.update(r, 15.0, float(i))))
    self.assertEqual([s[1]['id'] for s in shown[:5]], [self.ci.ids['east_only']] * 5)
    self.assertFalse(shown[4][1]['held'])
    self.assertNotEqual(shown[5][0], 'matched')  # in the turn
    self.assertTrue(shown[5][1]['held'])
    self.assertEqual(shown[6][0], 'matched')  # on the cross street, northbound
    self.assertEqual([s[1] for s in shown[6:]], [None, None])

  def test_a_walk_off_the_car_s_course_is_not_counted_as_searched(self):
    """Routes 08, 06 and 142 at the Great Western Hwy red-light camera, route ad at Parker St: a match snapped onto the
    very end of its line walks on from a zero-length step and takes a line the car is not on. That walk finds nothing
    but must not end a hold."""
    m = self.m()
    r = drive(m, [(280, 1), (300, 1)], 90.0, speed=15.0, unix=T_OUT, lookahead=True)[-1]
    self.assertEqual((r['camera_id'], r['camera_walk_m']), (self.ci.ids['east_only'], 400.0))
    part = m._ctx['part']

    def walk(xy, parts):
      pts = [latlon(x, y) for x, y in xy]
      la, lo = np.array([p[0] * 1e6 for p in pts]), np.array([p[1] * 1e6 for p in pts])
      m._live_path = lambda H, fwd: (la, lo, np.array(parts), 500.0, 'horizon')
      return m.camera_ahead('matched')

    h = cam.CameraHold()
    for xy, parts in (([(300, 0), (300, 0), (300, 200), (300, 500)], [part] * 4),  # north, while the car heads east
                      ([(300, 0), (300, 0), (360, 0), (360, 500)], [part, part, part + 1, part + 1])):  # off the line's end
      out = walk(xy, parts)
      self.assertEqual((out['camera_kind'], out['camera_walk_m'], out['camera_stop']), (0, None, 'none within 400 m (horizon)'))
      h.update(r, 15.0, 0.0)
      self.assertTrue(h.update(dict(out, state='matched'), 15.0, 1.0)['held'])
    out = walk([(300, 0), (300, 0), (360, 0), (760, 0)], [part] * 4)  # a zero-length step inside the line is no end
    self.assertEqual(out['camera_walk_m'], 400.0)

  def test_the_limit_look_ahead_is_unchanged_by_the_layer(self):
    with MiniIndex(FEATS, SCHOOLS) as plain:
      for pts, hd in (([(280, 1), (300, 1)], 90.0), ([(780, 1), (800, 1)], 90.0), ([(180, -1999), (200, -1999)], 90.0)):
        a = drive(nz.Matcher(plain.path, calendar=NEVER), pts, hd, speed=15.0, unix=T_OUT, lookahead=True)[-1]
        b = drive(self.m(), pts, hd, speed=15.0, unix=T_OUT, lookahead=True)[-1]
        for k in nz.RESULT_KEYS:
          if not k.startswith('camera_'):
            self.assertEqual(a[k], b[k], k)


# ----------------------------------------------------------------------------- a camera with a direction, beside other roads
FEATS_BESIDE = [
  # a 60 road (y=0) and a separate 50 street 12 m north of it
  feat([(0, 0), (2000, 0)], 60, 'Ordinary Permanent'),
  feat([(0, 12), (2000, 12)], 50),
  # two 60 lines 14 m apart with a 50 line between them: not one road drawn twice (y=-500, -493, -486)
  feat([(0, -500), (2000, -500)], 60, 'Ordinary Permanent'),
  feat([(0, -493), (2000, -493)], 50),
  feat([(0, -486), (2000, -486)], 60, 'Ordinary Permanent'),
  # a divided road drawn as two Both Directions lines 13 m apart (y=-1000, -987), a 50 service road outside it (y=-1010)
  feat([(0, -1000), (2000, -1000)], 80, 'Ordinary Permanent'),
  feat([(0, -987), (2000, -987)], 80, 'Ordinary Permanent'),
  feat([(0, -1010), (2000, -1010)], 50),
  # two One Way carriageways 20 m apart: eastbound at y=-1500, westbound at y=-1480
  feat([(0, -1500), (2000, -1500)], 80, 'Ordinary Permanent', 'One Way'),
  feat([(2000, -1480), (0, -1480)], 80, 'Ordinary Permanent', 'One Way'),
]


class TestBesideOtherRoads(unittest.TestCase):
  """Review findings 1 and 4: a camera whose direction is known is homed on a second, parallel line only when that line
  is the other line of the same divided road - never on a separate street beside the road - and never on the One Way
  carriageway of the other direction, even when that one is nearer."""

  @classmethod
  def setUpClass(cls):
    cls.mi = MiniIndex(FEATS_BESIDE)
    o = Osm()
    cls.ids = {
      'street': o.cam(800, -2, direction='90'),       # 2 m off the 60 road; the 50 street is 14 m away
      'between': o.cam(800, -501, direction='90'),    # 1 m off a 60 line; the other 60 line is 15 m away, a 50 line between
      'service': o.cam(800, -1001, direction='90'),   # 1 m off the divided road's southern line, the service road 9 m south
      'one_way': o.cam(800, -1484, direction='90'),   # checks eastbound traffic, but 4 m from the WESTBOUND carriageway
    }
    rows, _ = cam.parse_overpass(o.doc())
    arrays = dict(cls.mi.B['arrays'])
    c, _ = bi.camera_arrays(arrays, rows, 'test', 'x', log=lambda s: None)
    arrays.update(c)
    cls.path = os.path.join(cls.mi.tmp, 'beside.npz')
    bi.write_index(arrays, cls.path)
    cls.a = nzi.load_index(cls.path)

  @classmethod
  def tearDownClass(cls):
    cls.mi.close()

  def homes(self, key):
    """[(limit, one way, the line's y in m)] of a camera's home lines."""
    a = self.a
    row = int(np.nonzero(a['c_osm'] == self.ids[key])[0][0])
    lat0 = latlon(0, 0)[0]
    out = []
    for e in range(int(a['c_home_v0'][row]), int(a['c_home_v0'][row + 1])):
      q = int(a['c_home_part'][e])
      y = (int(a['b_vlat'][int(a['b_part_v0'][q])]) * 1e-6 - lat0) * nz.M_PER_DEG_LAT
      out.append((int(a['b_part_speed'][q]), int(a['b_part_dir'][q]) == nz.DIR_ONE_WAY, round(y)))
    return sorted(out)

  def last(self, pts, heading):
    return drive(nz.Matcher(self.path, calendar=NEVER), pts, heading, speed=15.0, unix=T_OUT, lookahead=True)[-1]

  def test_homes(self):
    self.assertEqual(self.homes('street'), [(60, False, 0)])  # not the 50 street 14 m away
    self.assertEqual(self.homes('between'), [(60, False, -500)])  # not the 60 line beyond the 50 one
    self.assertEqual(self.homes('service'), [(80, False, -1000), (80, False, -987)])  # both lines of the road, no service road
    self.assertEqual(self.homes('one_way'), [(80, True, -1500)])  # its own carriageway only, though 16 m off

  def test_a_street_beside_the_road_never_gets_its_camera(self):
    r = self.last([(480, 12), (500, 12)], 90.0)  # eastbound on the 50 street
    self.assertEqual((r['state'], r['limit_kph']), ('matched', 50))
    self.assertNotEqual(r['camera_id'], self.ids['street'])
    r = self.last([(480, 1), (500, 1)], 90.0)  # on the road itself
    self.assertEqual(r['camera_id'], self.ids['street'])
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)

  def test_both_lines_of_a_divided_road_but_not_its_service_road(self):
    for y in (-999, -988):
      r = self.last([(480, y), (500, y)], 90.0)
      self.assertEqual(r['camera_id'], self.ids['service'], y)
      self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)
    r = self.last([(480, -1011), (500, -1011)], 90.0)  # eastbound on the service road
    self.assertEqual((r['state'], r['limit_kph']), ('matched', 50))
    self.assertNotEqual(r['camera_id'], self.ids['service'])

  def test_a_one_way_camera_on_its_own_carriageway(self):
    r = self.last([(480, -1499), (500, -1499)], 90.0)  # eastbound, on its carriageway, 16 m from the camera's node
    self.assertEqual(r['camera_id'], self.ids['one_way'])
    self.assertAlmostEqual(r['camera_dist_m'], 300.0, delta=3.0)
    r = self.last([(1120, -1481), (1100, -1481)], 270.0)  # westbound, on the carriageway the node is nearer
    self.assertNotEqual(r['camera_id'], self.ids['one_way'])


# ----------------------------------------------------------------------------- the hold
class TestCameraHold(unittest.TestCase):
  def r(self, dist, cid=7, kind=cam.KIND_SPEED):
    return {'camera_kind': kind, 'camera_dist_m': dist, 'camera_id': cid, 'camera_dir_known': True, 'camera_dr': False}

  def test_held_through_a_gap_then_dropped(self):
    h = cam.CameraHold()
    self.assertEqual(h.update(self.r(300.0), 20.0, 0.0)['dist_m'], 300.0)
    out = h.update({'camera_kind': 0}, 20.0, 1.0)
    self.assertTrue(out['held'])
    self.assertAlmostEqual(out['dist_m'], 280.0)
    self.assertAlmostEqual(h.update(None, 20.0, 3.0)['dist_m'], 240.0)
    self.assertIsNone(h.update(None, 20.0, 3.5))  # past HOLD_S
    self.assertIsNone(h.update(None, 20.0, 4.0))

  def test_never_past_the_camera(self):
    h = cam.CameraHold()
    h.update(self.r(15.0), 20.0, 0.0)
    self.assertIsNone(h.update(None, 20.0, 1.0))  # run down past it: behind the car
    self.assertIsNone(h.update(None, 0.0, 1.5))

  def none(self, state, walk):
    return {'state': state, 'camera_kind': 0, 'camera_walk_m': walk}

  def test_matched_elsewhere_ends_it_at_once(self):
    h = cam.CameraHold()
    h.update(self.r(370.0), 7.0, 0.0)
    self.assertIsNone(h.update(self.none('matched', 400.0), 7.0, 1.0))  # searched past 370 m: another road
    self.assertIsNone(h.update(None, 7.0, 1.5))  # and not held again
    h.update(self.r(120.0), 7.0, 2.0)
    self.assertIsNone(h.update(self.none('matched', 120.0), 7.0, 3.0))  # exactly as far as it stood

  def test_kept_while_the_road_or_the_search_is_in_doubt(self):
    h = cam.CameraHold()
    h.update(self.r(300.0), 10.0, 0.0)
    for t, state, walk in ((1.0, 'hysteresis', 400.0), (1.5, 'low_confidence', None), (2.0, 'matched', 120.0),
                           (2.5, 'matched', None), (2.8, 'dead_reckoning', 400.0)):
      out = h.update(self.none(state, walk), 10.0, t)  # unsure of the line, the walk cut short at a junction, no heading
      self.assertTrue(out is not None and out['held'], (state, walk))
      self.assertAlmostEqual(out['dist_m'], 300.0 - 10.0 * t)

  def test_a_new_camera_replaces_the_held_one(self):
    h = cam.CameraHold()
    h.update(self.r(100.0, cid=1), 10.0, 0.0)
    out = h.update(self.r(350.0, cid=2, kind=cam.KIND_RED_LIGHT), 10.0, 1.0)
    self.assertEqual((out['id'], out['kind'], out['held']), (2, cam.KIND_RED_LIGHT, False))


# ----------------------------------------------------------------------------- the builder CLI
class TestBuilderCameras(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.mkdtemp(prefix='nswz_cams_')
    self.gj = os.path.join(self.tmp, 'speed_zones.geojson')
    with open(self.gj, 'w', encoding='utf-8') as f:
      json.dump({'type': 'FeatureCollection', 'features': FEATS}, f)
    self.zip = os.path.join(self.tmp, 'schoolzones.zip')
    with zipfile.ZipFile(self.zip, 'w') as z:
      z.writestr('SchoolZones.json', json.dumps([{'generated': '2026-09-07T00:00:00'}] + SCHOOLS))
    self.cams = os.path.join(self.tmp, 'osm_cameras.json')
    with open(self.cams, 'w', encoding='utf-8') as f:
      json.dump(camera_doc()[0], f)

  def tearDown(self):
    shutil.rmtree(self.tmp, ignore_errors=True)

  def main(self, out, *extra):
    argv = ['--speedzones', self.gj, '--schoolzones', self.zip, '--out', out, '--min-base-parts', '1', '--min-schools', '1',
            '--self-test-points', '20', '--today', '2026-10-24', '--min-cameras', '3', *extra]
    with contextlib.redirect_stdout(io.StringIO()):
      return bi.main(argv)

  def read(self, out, name):
    with open(os.path.join(out, name), encoding='utf-8') as f:
      return json.load(f) if name.endswith('.json') else f.read()

  def test_with_cameras_the_files_carry_the_layer_and_the_osm_attribution(self):
    out = os.path.join(self.tmp, 'out')
    self.assertEqual(self.main(out, '--cameras', self.cams), 0)
    man = self.read(out, nzi.MANIFEST_NAME)
    self.assertEqual(man['cameras']['rows'], 9)
    self.assertEqual(man['cameras']['origin'], 'overpass')
    self.assertEqual(man['cameras']['osm_base'], '2026-10-24T01:59:36Z')
    self.assertTrue('Transport for NSW' in man['attribution'] and 'OpenStreetMap' in man['attribution'])
    self.assertEqual(man['format_version'], nzi.FORMAT_VERSION)  # older cars read it as before
    txt = self.read(out, nzi.ATTRIBUTION_NAME)
    self.assertTrue('OpenStreetMap contributors' in txt and 'ODbL' in txt and 'Transport for NSW' in txt)
    info = nzi.verify_index(os.path.join(out, nzi.INDEX_NAME), os.path.join(out, nzi.MANIFEST_NAME))
    self.assertTrue('OpenStreetMap' in info.attribution)
    m = nz.Matcher(os.path.join(out, nzi.INDEX_NAME), calendar=NEVER)
    self.assertEqual(m.n_cameras, 9)

  def test_without_cameras_it_is_todays_index(self):
    out = os.path.join(self.tmp, 'plain')
    self.assertEqual(self.main(out), 0)
    man = self.read(out, nzi.MANIFEST_NAME)
    self.assertNotIn('cameras', man)
    self.assertEqual(man['attribution'], bi.ATTRIBUTION_LONG)
    self.assertNotIn('OpenStreetMap', self.read(out, nzi.ATTRIBUTION_NAME))
    a = nzi.load_index(os.path.join(out, nzi.INDEX_NAME))
    self.assertFalse(any(k.startswith('c_') or k == 'cameras_json' for k in a))
    rep = self.read(out, 'build_report.json')
    self.assertTrue(any('no --cameras file given' in w for w in rep['gates']['warnings']))

  def test_a_broken_fetch_carries_the_previous_cameras_over_and_never_fails(self):
    prev = os.path.join(self.tmp, 'prev')
    self.assertEqual(self.main(prev, '--cameras', self.cams), 0)
    bad = os.path.join(self.tmp, 'bad.json')
    for content in ('<html>rate limited</html>', json.dumps({'elements': [], 'osm3s': {}})):
      with open(bad, 'w') as f:
        f.write(content)
      out = os.path.join(self.tmp, 'next')
      shutil.rmtree(out, ignore_errors=True)
      self.assertEqual(self.main(out, '--cameras', bad, '--previous-index', os.path.join(prev, nzi.INDEX_NAME),
                                 '--previous-manifest', os.path.join(prev, nzi.MANIFEST_NAME)), 0)
      man = self.read(out, nzi.MANIFEST_NAME)
      self.assertEqual(man['cameras']['rows'], 9)
      self.assertTrue(man['cameras']['origin'].startswith('carried over'))
      self.assertEqual(man['cameras']['osm_base'], '2026-10-24T01:59:36Z')
    # far fewer than the previous release: carried over too
    doc = camera_doc()[0]
    doc['elements'] = [e for e in doc['elements'] if e.get('tags', {}).get('highway') != 'speed_camera'][:]
    with open(bad, 'w') as f:
      json.dump(doc, f)
    out = os.path.join(self.tmp, 'fewer')
    self.assertEqual(self.main(out, '--cameras', bad, '--min-cameras', '1', '--previous-index', os.path.join(prev, nzi.INDEX_NAME),
                               '--previous-manifest', os.path.join(prev, nzi.MANIFEST_NAME)), 0)
    rep = self.read(out, 'build_report.json')
    self.assertTrue(any('more than 25 % fewer' in w for w in rep['gates']['warnings']))
    self.assertTrue(self.read(out, nzi.MANIFEST_NAME)['cameras']['origin'].startswith('carried over'))
    # and with nothing to carry over: no layer, still a good build
    out = os.path.join(self.tmp, 'none')
    self.assertEqual(self.main(out, '--cameras', bad, '--min-cameras', '50'), 0)
    self.assertNotIn('cameras', self.read(out, nzi.MANIFEST_NAME))


if __name__ == '__main__':
  unittest.main()
