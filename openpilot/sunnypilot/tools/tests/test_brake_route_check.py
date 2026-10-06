"""
FORK(HONDA_ACCORD_9G_AU): brake_route_check.py reads a route and judges the pump rule's and the brake law's proof plan.

* Its bit positions are the DBC's: frames packed by opendbc's own CANPacker decode to what was packed.
* The rules it replays are the controller's own functions (v5, C1b; its fallback transcription of C1b is the same
  rule), and its transcription of the retired C1 still reads the routes that ran C1 (flag 16).
* End to end on a synthetic route written as a real rlog: the rule is read from CarParamsSP, the replay of the rule the
  car ran matches the pump bit it logged, a hold that does not move passes and one that rolls - as the car reports a
  roll: XMISSION_SPEED, vEgo and WHEELS_MOVING up, standstill clear - aborts, an approach the driver ends with the brake
  is an engaged arrival with a take-over, the VSA's ripple onset, the learner and the JSON output come through.
* holds() on frame tables: 1 s and 3 s rolls at 0.3 m/s inside a hold (fix round 1: the old definition ended the hold
  on the first moving frame and never saw them).
* The parquet export reads to the same frames as the rlogs (when pyarrow is installed; the repo's venv has none).
* The verdicts follow the plan's thresholds, and the comparative ones wait for a baseline arm; so do C1b's acceptance
  checks (c1weak A_synth section 4), each tripping at its own threshold.
"""
import io
import json
import math
import os
import random
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np

from openpilot.cereal import messaging
from opendbc.can import CANPacker
from opendbc.car.honda import carcontroller as ccm
from openpilot.sunnypilot.tools import brake_route_check as brc

DBC = "honda_accord_au_2015_can_generated"


def _pump_stream(rule_fn, cb, v, t, six_args):
  on = []
  if six_args:
    level, trig, last = 0, 0, 0.0
    for c, ve, ts in zip(cb, v, t, strict=True):
      p, level, trig, last = rule_fn(int(c), float(ve), level, trig, last, float(ts))
      on.append(p)
  else:
    anchor, last = 0, 0.0
    for c, ve, ts in zip(cb, v, t, strict=True):
      p, anchor, last = rule_fn(int(c), float(ve), anchor, last, float(ts))
      on.append(p)
  return np.array(on, dtype=bool) & (np.asarray(cb) > 0)


def synth_route(d: str, flags: int = 64, creep: bool = False, brake_error: bool = False, press: bool = False,
                blaw_tag: str = "") -> str:
  """A 50 s engaged drive written as a real rlog: cruise, a two-step brake application, a stop, a 15 s hold at 189
  counts behind a stopped lead, a launch. The 0x1FA pump bit is what the rule the flags select would send.
  creep: the hold rolls at 0.3 m/s from 30 to 31 s the way the car reports it (carstate.py: XMISSION_SPEED above its
  floor clears standstill and is vEgo below 1 m/s; WHEELS_MOVING 1), 0.3 m toward the lead. press: the driver brakes
  from 20 s (5 m/s), which cancels openpilot longitudinal; the stop at 25 s is the driver's. flags 64: C1b; 16: the
  retired C1 (its route, as the car logged it); 0: v5."""
  packer = CANPacker(DBC)
  tag = "c1b" if flags & 64 else ("v6" if flags & 16 else "v5")
  rule = {"c1b": ccm.brake_pump_c1b_elesys, "v6": brc._c1_reference, "v5": ccm.brake_pump_hysteresis_elesys}[tag]
  n = 5000                                   # 100 Hz
  t = np.arange(n) * 0.01
  v = np.where(t < 10, 15.0, np.where(t < 25, 15.0 * (25 - t) / 15, np.where(t < 40, 0.0, 1.0 * (t - 40))))
  v = np.clip(v, 0.0, None)
  rolling = creep & (t >= 30.0) & (t < 31.0)
  v = np.where(rolling, 0.3, v)
  x_roll = np.cumsum(np.where(rolling, 0.3 * 0.01, 0.0))
  cb = np.where(t < 10, 0, np.where(t < 12, (t - 10) * 40, np.where(t < 15, 80, np.where(t < 17, 80 + (t - 15) * 35,
                np.where(t < 25, 150, np.where(t < 26, 150 + (t - 25) * 39, np.where(t < 40, 189, 0)))))))
  cb = cb.astype(int)
  la = ~(press & (t >= 20.0))
  bp = press & (t >= 20.0) & (t < 25.0)
  cb = np.where(la, cb, 0)
  a = np.r_[np.diff(v) / 0.01, 0.0]
  t0 = 1_000_000_000
  mono = t0 + 1000 + np.arange(n, dtype=np.int64) * 10_000_000
  t_rel = (mono - t0) / 1e9                  # the times the script will see, so its replay sees the same numbers
  even = np.arange(n) % 2 == 0
  pump = np.zeros(n, dtype=bool)
  pump[even] = _pump_stream(rule, cb[even], v[even], t_rel[even], tag != "v5")
  pump_held = pump | np.r_[False, pump[:-1] & ~even[1:]]   # the 50 Hz request, held through the odd frame
  msgs = []

  def add(service, mono_i, fill=None):
    m = messaging.new_message(service)
    m.logMonoTime = int(mono_i)
    if fill is not None:
      fill(getattr(m, service))
    msgs.append(m)
    return m

  def init(o):
    o.gitCommit = "abcdef1234567"
  add("initData", t0, init)
  add("carParamsSP", t0 + 1, lambda o: setattr(o, "flags", flags))
  ripple_from = None
  for i in range(n):
    vi, ai = float(v[i]), float(a[i])
    stopped = vi < 0.01

    def cs(o, vi=vi, ai=ai, stopped=stopped, i=i):
      o.vEgo, o.aEgo, o.standstill = vi, ai, stopped
      o.cruiseState.enabled = True
      o.brakePressed = bool(bp[i])
    add("carState", mono[i], cs)

    def cc(o, i=i, stopped=stopped):
      o.longActive = bool(la[i])
      o.actuators.accel = float(-cb[i] / 100.0) if cb[i] else 0.5
      o.actuators.longControlState = "stopping" if stopped and cb[i] else "pid"
      o.orientationNED = [0.0, 0.0, 0.0]
    add("carControl", mono[i], cc)
    add("controlsState", mono[i], lambda o: setattr(o, "uiAccelCmd", 0.02))
    # the pump's ripple on USER_BRAKE: 101/103 alternating every frame from 0.12 s after the request, while it lasts
    if not pump_held[i]:
      ripple_from = None
    elif ripple_from is None:
      ripple_from = i + 12
    ub = 102 if ripple_from is None or i < ripple_from else (101 if (i - ripple_from) % 2 == 0 else 103)
    xm = vi * 3.6
    frames = [packer.make_can_msg("VSA_STATUS", 0, {"USER_BRAKE": (ub * 0.015625) - 1.609375, "COMPUTER_BRAKING": int(cb[i] > 0)}),
              packer.make_can_msg("STANDSTILL", 0, {"WHEELS_MOVING": int(vi > 0.01 or xm > 0),
                                                    "BRAKE_ERROR_1": int(brake_error and 20.0 <= t[i] < 20.5)}),
              packer.make_can_msg("ENGINE_DATA", 0, {"XMISSION_SPEED": xm})]
    if even[i]:
      frames.append(packer.make_can_msg("BRAKE_COMMAND", 128, {"COMPUTER_BRAKE": int(cb[i]), "BRAKE_PUMP_REQUEST": int(pump[i])}))
    m = messaging.new_message("can", len(frames))
    m.logMonoTime = int(mono[i])
    for k, (addr, dat, bus) in enumerate(frames):
      m.can[k].address, m.can[k].dat, m.can[k].src = addr, bytes(dat), bus
    msgs.append(m)
    if i % 5 == 0:
      def gps(o, vi=vi):
        o.hasFix = True
        o.vNED = [vi, 0.0, 0.0]
      add("gpsLocationExternal", mono[i], gps)

      def rad(o, i=i):
        held = 25.0 <= t[i] < 40.0
        o.leadOne.present = bool(held)
        o.leadOne.dRel, o.leadOne.vLead = (4.0 - float(x_roll[i]), 0.0) if held else (0.0, 0.0)
      add("radarState", mono[i], rad)
      add("deviceMotion", mono[i], lambda o, vi=vi: setattr(o.velocityDevice, "x", vi))
    add("accelerometer", mono[i], lambda o, ai=ai: setattr(o.acceleration, "v", [9.81, 0.0, -ai]))
    if i % 500 == 0:
      m = messaging.new_message(None)          # logMessage is a text field: nothing to init
      m.logMonoTime = int(mono[i])
      m.logMessage = json.dumps({"msg": f"hondadyn gaslaw=v2 brake=1.005 brakec={1.005 + i / 500 * 0.001:.3f} tuner=1 " +
                                        f"pump={tag}{blaw_tag}"})
      msgs.append(m)
  import zstandard
  raw = os.path.join(d, "raw")
  os.makedirs(raw, exist_ok=True)
  with open(os.path.join(raw, "0000--rlog.zst"), "wb") as f:
    f.write(zstandard.compress(b"".join(m.to_bytes() for m in msgs), 3))
  return d


class TestDecode(unittest.TestCase):
  def test_bits_are_the_dbcs(self):
    p = CANPacker(DBC)
    for cbv, pump in ((0, 0), (1, 1), (189, 1), (255, 0), (1023, 1)):
      _, dat, _ = p.make_can_msg("BRAKE_COMMAND", 0, {"COMPUTER_BRAKE": cbv, "BRAKE_PUMP_REQUEST": pump})
      x = np.array([brc.pack8(dat)], dtype=np.uint64)
      self.assertEqual((int(brc.sig(x, 7, 10)[0]), int(brc.sig(x, 8, 1)[0])), (cbv, pump))
    _, dat, _ = p.make_can_msg("VSA_STATUS", 0, {"USER_BRAKE": 102 * 0.015625 - 1.609375, "COMPUTER_BRAKING": 1})
    x = np.array([brc.pack8(dat)], dtype=np.uint64)
    self.assertEqual((int(brc.sig(x, 7, 16)[0]), int(brc.sig(x, 23, 1)[0])), (102, 1))
    _, dat, _ = p.make_can_msg("STANDSTILL", 0, {"WHEELS_MOVING": 1, "BRAKE_ERROR_2": 1})
    x = np.array([brc.pack8(dat)], dtype=np.uint64)
    self.assertEqual((int(brc.sig(x, 12, 1)[0]), int(brc.sig(x, 11, 1)[0]), int(brc.sig(x, 9, 1)[0])), (1, 0, 1))
    _, dat, _ = p.make_can_msg("ENGINE_DATA", 0, {"XMISSION_SPEED": 12.34})
    self.assertAlmostEqual(int(brc.sig(np.array([brc.pack8(dat)], dtype=np.uint64), 7, 16)[0]) * 0.01, 12.34, places=2)


class TestRules(unittest.TestCase):
  def test_the_replay_runs_the_controllers_own_functions(self):
    fns = brc.rule_functions()
    self.assertIs(fns["v5"][0], ccm.brake_pump_hysteresis_elesys)
    self.assertIs(fns["c1b"][0], ccm.brake_pump_c1b_elesys)
    # C1 is retired: no controller function, the script's own transcription reads its routes
    self.assertFalse(hasattr(ccm, "brake_pump_c1_elesys"))
    self.assertIs(fns["v6"][0], brc._c1_reference)

  def test_the_fallback_transcription_is_the_same_rule_as_the_controllers(self):
    rng = random.Random(7)
    t = np.arange(20000) * 0.02
    cb, v = np.zeros(len(t)), np.zeros(len(t))
    c, s = 0.0, 10.0
    for i in range(len(t)):
      if i % 50 == 0:
        c = rng.choice([0, 0, 5, 12, 30, 61, 90, 120, 189, 210, 255])
        s = rng.choice([0.0, 0.1, 1.0, 2.0, 3.0, 12.0, 25.0])
      cb[i], v[i] = max(0, c + rng.randint(-4, 4)) if c else 0, s
    a = brc.replay_rule("c1b", cb, v, t)
    b = brc.replay_rule("c1b", cb, v, t, {"c1b": (brc._c1b_reference, "ref")})
    self.assertGreater(a.sum(), 500)
    np.testing.assert_array_equal(a, b)
    # the three rules are three different rules on this trace
    c1 = brc.replay_rule("v6", cb, v, t)
    v5 = brc.replay_rule("v5", cb, v, t)
    self.assertTrue((a != c1).any() and (a != v5).any() and (c1 != v5).any())

  def test_c1b_against_c1_in_the_replay(self):
    # what C1b changes, visible in a replay: the first frame of an application pumps, a rise right after a burst pumps
    # (no minimum gap), and the crawl zone at cb > 100 pumps throughout
    t = 10.0 + np.arange(300) * 0.02      # the replay starts as the controller does, last_pump_ts 0: start past 0.5 s
    cb = np.r_[np.zeros(10), np.full(40, 5.0), np.zeros(50), np.full(30, 60.0), np.full(70, 70.0), np.full(100, 150.0)]
    v = np.r_[np.full(200, 10.0), np.full(100, 1.0)]
    c1, c1b = brc.replay_rule("v6", cb, v, t), brc.replay_rule("c1b", cb, v, t)
    self.assertTrue(c1b[10] and not c1[10:50].any(), "a 5-count application: C1b pumps its first frame, C1 never")
    self.assertTrue(c1b[130], "+10 at 0.6 s after the burst: C1b at once")
    self.assertFalse(c1[130], "C1: blocked by its 1 s gap")
    self.assertTrue(c1b[200:].all(), "the crawl run")
    self.assertFalse(c1[200:].all())


class TestEndToEnd(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.tmp = tempfile.TemporaryDirectory()
    cls.ok = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000aa--0000000000"))
    cls.c1 = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000af--0000000000"), flags=16)
    cls.crept = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000ab--0000000000"), creep=True, brake_error=True)
    cls.v5 = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000ac--0000000000"), flags=0)
    cls.pressed = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000ad--0000000000"), press=True)
    cls.law_off = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000ae--0000000000"), flags=64 | 32,
                              blaw_tag=" blaw=v1")

  @classmethod
  def tearDownClass(cls):
    cls.tmp.cleanup()

  def test_a_clean_route(self):
    R = brc.check_route(self.ok, force_rlog=True, workers=1)
    self.assertEqual((R["rule"], R["brake_law"], R["commit"], R["name"]), ("c1b", "v1", "abcdef123", "000000aa"))
    assert "CarParamsSP" in R["rule_how"]
    self.assertEqual(R["match"], 1.0, "the replay of the rule the car ran is what it logged")
    self.assertEqual(R["match_raw"], 1.0)
    w = R["pump"]["wire"]
    self.assertEqual((w["starts"], w["pump_s"]), (R["pump"]["c1b"]["starts"], R["pump"]["c1b"]["pump_s"]))
    self.assertGreater(w["starts"], 0)
    self.assertEqual(w["applications_unpumped"], 0)
    self.assertEqual(len(R["stops_pump"]), 1)
    self.assertLessEqual(R["stops_pump"][0]["bursts"], 1)
    self.assertEqual(R["stops_pump"][0]["late"], 0)
    self.assertTrue(R["stops_pump"][0]["design_ok"])
    self.assertEqual(len(R["holds"]), 1)
    h = R["holds"][0]
    self.assertGreater(h["s"], 13.0)
    self.assertFalse(h["moved"])
    self.assertGreaterEqual(h["delivered"], 185, "the hold's delivered pressure is reported")
    self.assertEqual((R["stops"]["arrivals"], R["stops"]["brake_pressed"], len(R["stops"]["stops"])), (1, 0, 1))
    self.assertAlmostEqual(h["radar_change"], 0.0, places=3)
    self.assertLess(h["camera_disp"], 0.01)
    self.assertAlmostEqual(R["vsa"]["comp_braking_frac"], 1.0)
    self.assertEqual(R["vsa"]["brake_error_frames"], 0)
    self.assertTrue(0.10 <= R["vsa"]["ripple_onset_median"] <= 0.16, R["vsa"])
    self.assertEqual((R["learner"]["n"], R["learner"]["first"], R["learner"]["last"]), (10, 1.005, 1.014))
    self.assertAlmostEqual(R["learner"]["delta"], 0.009, places=6)
    self.assertGreater(R["grade_s"], 30.0)
    v = {c: s for c, s, _ in brc.verdicts(brc.pool([R]), None)}
    self.assertEqual(v["a hold that moves with cb >= 100 and no planner launch"], "PASS")
    self.assertEqual(v["any BRAKE_ERROR (0x1B0)"], "PASS")
    self.assertEqual(v["a moving pump-off at cb >= 100 longer than 6.1 s"], "PASS")
    self.assertEqual(v["a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)"], "n.a.")

  def test_a_route_that_ran_the_retired_c1(self):
    # flag 16: the tool still knows C1 and replays it from its own transcription
    R = brc.check_route(self.c1, force_rlog=True, workers=1)
    self.assertEqual(R["rule"], "v6")
    self.assertEqual(R["match"], 1.0)
    w = R["pump"]["wire"]
    self.assertEqual((w["starts"], w["pump_s"]), (R["pump"]["v6"]["starts"], R["pump"]["v6"]["pump_s"]))
    self.assertNotEqual(w["pump_s"], R["pump"]["c1b"]["pump_s"])
    self.assertTrue(R["stops_pump"][0]["design_ok"])

  def test_a_hold_that_creeps_and_a_brake_error_abort(self):
    R = brc.check_route(self.crept, force_rlog=True, workers=1)
    self.assertEqual(len(R["holds"]), 1, "a roll inside a hold does not end it")
    h = R["holds"][0]
    self.assertTrue(h["moved"] and h["xmission"] and h["vEgo"] and h["wheels_moving"])
    self.assertAlmostEqual(h["moved_s"], 1.0, delta=0.05)
    self.assertAlmostEqual(h["moved_first_t"], 30.0, delta=0.05)
    self.assertAlmostEqual(h["radar_change"], 0.3, delta=0.02)
    self.assertTrue(h["suspect"])
    self.assertGreater(R["vsa"]["brake_error_frames"], 0)
    v = {c: s for c, s, _ in brc.verdicts(brc.pool([R]), None)}
    self.assertEqual(v["a hold that moves with cb >= 100 and no planner launch"], "ABORT")
    self.assertEqual(v["any BRAKE_ERROR (0x1B0)"], "ABORT")

  def test_an_approach_the_driver_ends_with_the_brake_is_a_take_over(self):
    # fix round 1: a press cancels openpilot longitudinal, so the old count (engaged through the last second) never
    # saw one and printed 0 per 100
    R = brc.check_route(self.pressed, force_rlog=True, workers=1)
    s = R["stops"]
    self.assertEqual((s["arrivals"], s["brake_pressed"], len(s["stops"])), (1, 1, 0))
    self.assertEqual(brc.pool([R])["takeovers"], 1)

  def test_the_brake_law_is_what_the_lines_say_ran(self):
    R = brc.check_route(self.law_off, force_rlog=True, workers=1)
    self.assertEqual(R["brake_law"], "v1", "flag 32 without gas law v2: the law did not run, and the lines say so")
    assert "not run" in R["brake_law_how"]
    assert any("did not run" in x for x in R["notes"])
    R = brc.check_route(self.ok, force_rlog=True, workers=1)
    self.assertEqual(R["brake_law"], "v1")      # no tag: flag 32 clear

  def test_rule_comes_from_the_log_before_the_command_line(self):
    R = brc.check_route(self.ok, rule="v5", force_rlog=True, workers=1)
    self.assertEqual(R["rule"], "c1b", "--rule never overrides CarParamsSP")
    assert any("--rule v5 ignored" in x for x in R["notes"])
    self.assertEqual(brc.what_ran(None, {}, "v6")[:2], ("v6", "--rule (the log does not say)"))
    self.assertEqual(brc.what_ran(None, {}, "c1b")[:2], ("c1b", "--rule (the log does not say)"))
    self.assertEqual(brc.what_ran(None, {"pump": "v5"}, "v6")[0], "v5")
    self.assertEqual(brc.what_ran(None, {"pump": "c1b"}, None)[0], "c1b")
    self.assertEqual([brc.what_ran(f, {}, None)[0] for f in (0, 16, 64, 64 | 32, 16 | 8, 32)], ["v5", "v6", "c1b", "c1b", "v6", "v5"])
    assert any("lines say v6" in x for x in brc.what_ran(64, {"pump": "v6"}, None)[4])
    self.assertEqual(brc.what_ran(48, {"blaw": "v2"}, None)[2], "v2")
    self.assertEqual(brc.what_ran(48, {}, None)[2], "v2")

  def test_the_v5_route_and_the_cli(self):
    out = io.StringIO()
    js = os.path.join(self.tmp.name, "out.json")
    with redirect_stdout(out):
      self.assertEqual(brc.main([self.v5, "--rlog", "--workers", "1", "--baseline", self.ok, "--json", js]), 0)
    text = out.getvalue()
    assert "pump rule v5 (CarParamsSP.flags = 0)" in text
    assert "ABORT CRITERIA" in text
    with open(js) as f:
      J = json.load(f)
    self.assertEqual(J["arm"][0]["rule"], "v5")
    self.assertEqual(J["arm"][0]["match"], 1.0)
    self.assertEqual(J["baseline"][0]["rule"], "c1b")
    self.assertTrue(all(len(x) == 3 for x in J["verdicts"]))
    assert "C1b ACCEPTANCE" in text
    self.assertTrue(J["acceptance"] and all(len(x) == 3 for x in J["acceptance"]))

  @unittest.skipUnless(brc._pyarrow(), "pyarrow is not in the repo's venv")
  def test_parquet_reads_to_the_same_frames(self):
    pa, pq, _ = brc._pyarrow()
    S = brc.load_route(self.ok, force_rlog=True, workers=1)
    pdir = os.path.join(self.ok, "parquet")
    os.makedirs(pdir, exist_ok=True)
    c = S["can"]
    rows = {}
    for t_, a_, s_, x_ in zip(c["t"], c["addr"], c["src"], c["x"], strict=True):
      rows.setdefault(int(t_), []).append({"address": int(a_), "dat": int(x_).to_bytes(8, "big").hex(), "src": int(s_)})
    pq.write_table(pa.table({"_logMonoTime": list(rows), "value": list(rows.values())}), os.path.join(pdir, "can.parquet"))
    cs = S["cs"]
    pq.write_table(pa.table({"_logMonoTime": cs["t"], "vEgo": cs["v"], "aEgo": cs["a"], "brakePressed": cs["bp"] == 1,
                             "gasPressed": cs["gp"] == 1, "standstill": cs["ss"] == 1, "cruiseState.enabled": cs["en"] == 1,
                             "stockAeb": cs["aeb"] == 1, "stockFcw": cs["fcw"] == 1}), os.path.join(pdir, "carState.parquet"))
    cc = S["cc"]
    names = {v: k for k, v in brc.LCS.items()}
    pq.write_table(pa.table({"_logMonoTime": cc["t"], "longActive": cc["la"] == 1, "actuators.accel": cc["acc"],
                             "actuators.longControlState": [names[int(x)] for x in cc["lcs"]],
                             "orientationNED": [[0.0, float(p), 0.0] for p in cc["pitch"]],
                             "hudControl.visualAlert": ["none"] * len(cc["t"])}), os.path.join(pdir, "carControl.parquet"))
    pq.write_table(pa.table({"_logMonoTime": S["ctl"]["t"], "uiAccelCmd": S["ctl"]["ui"]}), os.path.join(pdir, "controlsState.parquet"))
    r = S["rad"]
    pq.write_table(pa.table({"_logMonoTime": r["t"], "leadOne.status": r["st"] == 1, "leadOne.dRel": r["d"], "leadOne.vLead": r["vl"]}),
                   os.path.join(pdir, "radarState.parquet"))
    g = S["gps"]
    pq.write_table(pa.table({"_logMonoTime": g["t"], "hasFix": g["fix"] == 1, "vNED": np.c_[g["vn"], g["ve"], g["vd"]].tolist()}),
                   os.path.join(pdir, "gpsLocationExternal.parquet"))
    im = S["imu"]
    pq.write_table(pa.table({"_logMonoTime": im["t"], "acceleration.v": np.c_[im["x"], im["y"], im["z"]].tolist()}),
                   os.path.join(pdir, "accelerometer.parquet"))
    pq.write_table(pa.table({"_logMonoTime": S["pose"]["t"], "velocityDevice.x": S["pose"]["vx"]}), os.path.join(pdir, "livePose.parquet"))
    pq.write_table(pa.table({"_logMonoTime": [x[0] for x in S["logs"]], "value": [x[1] for x in S["logs"]]}),
                   os.path.join(pdir, "logMessage.parquet"))
    pq.write_table(pa.table({"_logMonoTime": [S["meta"]["t0"]], "gitCommit": [S["meta"]["commit"]]}), os.path.join(pdir, "initData.parquet"))
    try:
      Sp = brc.load_route(self.ok, workers=1)
      self.assertEqual(Sp["meta"]["source"], "parquet")
      self.assertEqual(Sp["meta"]["flags_sp"], 64, "CarParamsSP from the rlog: the owner's export cannot decode it")
      Fr, Fp = brc.build_frames(S), brc.build_frames(Sp)
      for k in ("t", "cb", "pump", "v", "acc", "la", "lcs", "ac", "ff", "xmission", "comp_braking", "dRel", "cam_vx", "ui"):
        np.testing.assert_array_equal(Fr[k], Fp[k], err_msg=k)
    finally:
      import shutil
      shutil.rmtree(pdir)


class TestHolds(unittest.TestCase):
  """holds() on a frame table: a roll inside a hold, as the car reports one (carstate.py: XMISSION_SPEED above its
  0.278 m/s floor clears standstill and is vEgo below 1 m/s). The review's probe: 1 s and 3 s at 0.3 m/s."""
  DT = 0.02

  def frames(self, roll_from=None, roll_to=None, v_roll=0.3, release_at=None):
    n = int(40 / self.DT)
    t = np.arange(n) * self.DT
    v = np.zeros(n)
    if roll_from is not None:
      assert roll_to is not None
      v[(t >= roll_from) & (t < roll_to)] = v_roll
    cb = np.full(n, 189.0)
    if release_at is not None:
      cb[t >= release_at] = np.maximum(189.0 - (t[t >= release_at] - release_at) / self.DT * 32, 0)
    moving = v > 0
    return {"t": t, "cb": cb, "la": np.ones(n, bool), "v": v, "ss": (~moving).astype(float), "acc": np.full(n, -0.8),
            "xmission": v.copy(), "wheels_moving": moving.astype(float), "lead": np.ones(n, bool),
            "dRel": 3.0 - np.cumsum(v) * self.DT, "vLead": np.zeros(n), "cam_vx": v.copy(), "dt": np.full(n, self.DT),
            "pump": np.r_[np.ones(25, bool), np.zeros(n - 25, bool)], "gp": np.zeros(n), "bp": np.zeros(n)}

  def test_a_roll_inside_a_hold_is_moved(self):
    for seconds in (1.0, 3.0):
      with self.subTest(seconds=seconds):
        H = brc.holds(self.frames(20.0, 20.0 + seconds))
        self.assertEqual(len(H), 1)
        self.assertTrue(H[0]["moved"] and H[0]["xmission"] and H[0]["vEgo"] and H[0]["wheels_moving"])
        self.assertAlmostEqual(H[0]["moved_s"], seconds, delta=0.05)
        self.assertAlmostEqual(H[0]["dist_moved"], 0.3 * seconds, delta=0.02)
        self.assertAlmostEqual(H[0]["radar_change"], 0.3 * seconds, delta=0.02)
        P = brc.pool([{"name": "x", "holds": H, "gain": {"samples": {"cbl": np.zeros(0), "beyond": np.zeros(0), "ac": np.zeros(0)}},
                       "bleed": {"samples": {"err": np.zeros(0), "tsp": np.zeros(0), "cb": np.zeros(0), "run": np.zeros(0)}},
                       "stops": {"stops": [], "arrivals": 0, "brake_pressed": 0}, "learner": {"last": math.nan, "delta": math.nan},
                       "pump": {"wire": {"off_mv_cb100": 0.0, "starts": 0, "brk_s": 0.0, "pump_s": 0.0}},
                       "vsa": {"brake_error_frames": 0}, "match": 1.0, "law": {"bands": []}}])
        self.assertEqual({c: v for c, v, _ in brc.verdicts(P, None)}["a hold that moves with cb >= 100 and no planner launch"], "ABORT")

  def test_a_still_hold_and_its_release(self):
    H = brc.holds(self.frames())
    self.assertEqual(len(H), 1)
    self.assertFalse(H[0]["moved"])
    self.assertAlmostEqual(H[0]["s"], 39.98, delta=0.05)
    self.assertEqual(H[0]["delivered"], 189.0)
    # the brake let go and the car rolling as it does: the release, not creep
    F = self.frames(release_at=30.0)
    F["v"][(F["t"] >= 30.04)] = 0.3
    F["xmission"], F["ss"] = F["v"].copy(), (F["v"] == 0).astype(float)
    F["wheels_moving"] = (F["v"] > 0).astype(float)
    self.assertFalse(brc.holds(F)[0]["moved"])
    # a launch request ends the hold before the car moves
    F = self.frames(30.0, 40.0)
    F["acc"][F["t"] >= 30.0] = 0.5
    self.assertFalse(brc.holds(F)[0]["moved"])

  def test_a_hold_reached_on_the_soft_stops_cap_shows_what_was_delivered(self):
    F = self.frames()
    F["cb"][:] = 125.0
    F["cb"][F["t"] >= 1.0] = 189.0        # the rise to the hold, never pumped
    H = brc.holds(F)
    self.assertEqual(H[0]["cb"], 189.0)
    self.assertEqual(H[0]["delivered"], 125.0)

  def test_standstill_bursts_judge_c1_by_what_was_delivered(self):
    # rolling at the soft stop's cap of 125 (delivered by a moving burst), stopped from 1 s, the rise to 189 at 1.5 s
    F = self.frames()
    t = F["t"]
    F["v"] = np.where(t < 1.0, 0.5, 0.0)
    F["cb"] = np.where(t < 1.5, 125.0, 189.0)
    c1 = (t < 0.5) | ((t >= 1.5) & (t < 2.0))     # C1 now: the rise delivered by one burst at standstill
    head = t < 0.5                                 # the pseudo-code alone: nothing at standstill
    s = brc.standstill_bursts(F, c1)[0]
    self.assertEqual((s["bursts"], s["delivered_reached"], s["cb_max"], s["design_ok"], s["late"]), (1, 125.0, 189.0, True, 0))
    self.assertEqual(s["delivered_hold"], 189.0)
    s = brc.standstill_bursts(F, head)[0]
    self.assertEqual((s["bursts"], s["delivered_hold"], s["cb_hold"]), (0, 125.0, 189.0))
    # a burst on a stop reached firm with no rise to deliver breaks C1's design (a top-up), and is a late re-pump
    F["cb"][:] = 189.0
    s = brc.standstill_bursts(F, (t < 0.5) | ((t >= 30.0) & (t < 30.5)))[0]
    self.assertFalse(s["design_ok"])
    self.assertEqual(s["late"], 1)
    # C1b: an application that begins at the stop gets its first-frame burst, within the design
    F["cb"] = np.where(t < 2.0, 0.0, 189.0)
    s = brc.standstill_bursts(F, (t >= 2.0) & (t < 2.5))[0]
    self.assertEqual((s["bursts"], s["design_ok"]), (1, True))


class TestVerdicts(unittest.TestCase):
  @staticmethod
  def arm(gain=-0.8, bleed6=0.0, stretches=25, dist=4.0, learner=1.0, off=3.0, moved=0, suspect=0, berr=0, delta=0.0):
    return {"routes": ["x"], "gain": [{"s": 100.0, "per100": gain}] * len(brc.GAIN_BANDS), "slope100": gain,
            "bleed60": [{"err": 0.0, "stretches": 99}, {"err": 0.0, "stretches": 50}, {"err": 0.0, "stretches": 30},
                        {"err": bleed6, "stretches": stretches}, {"err": math.nan, "stretches": 0}],
            "stop_dist_median": dist, "stop_dist_min": dist, "stop_dist_n": 10, "learner_mean": learner,
            "learner_delta_mean": delta,
            "off_mv_cb100": off, "moved_holds": moved, "suspect_holds": suspect, "holds": 5, "brake_error_frames": berr}

  def verdict(self, P, B=None):
    return {c: s for c, s, _ in brc.verdicts(P, B)}

  def test_every_criterion_trips_at_its_threshold(self):
    base = self.arm()
    assert "ABORT" not in self.verdict(self.arm(), base).values()
    cases = [("a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)", {"gain": -0.69}),
             ("the 6-12 s bleed bin at cb >= 60 weaker than 0-1 s by >= 0.10 (>= 20 stretches)", {"bleed6": 0.10}),
             ("the median stop distance shorter by > 0.5 m, or any stop under 2.0 m", {"dist": 3.4}),
             ("the median stop distance shorter by > 0.5 m, or any stop under 2.0 m", {"dist": 1.9}),
             ("the brake-gain learner more than 0.04 above the other arm's mean", {"delta": 0.05}),
             ("any BRAKE_ERROR (0x1B0)", {"berr": 1}),
             ("a moving pump-off at cb >= 100 longer than 6.1 s", {"off": 6.2}),
             ("a hold that moves with cb >= 100 and no planner launch", {"moved": 1})]
    for crit, kw in cases:
      with self.subTest(crit=crit, kw=kw):
        self.assertEqual(self.verdict(self.arm(**kw), base)[crit], "ABORT")
    # just inside
    # the level alone does not trip it: both arms of an A/B share one stored gain
    for kw in ({"gain": -0.71}, {"bleed6": 0.09}, {"dist": 3.6}, {"delta": 0.03}, {"learner": 1.10}, {"off": 6.1}):
      with self.subTest(inside=kw):
        assert "ABORT" not in self.verdict(self.arm(**kw), base).values()
    self.assertEqual(self.verdict(self.arm(suspect=1), base)["a hold that moves with cb >= 100 and no planner launch"], "CHECK")

  def test_too_little_data_or_no_baseline_is_not_a_pass(self):
    v = self.verdict(self.arm(bleed6=0.5, stretches=19))
    self.assertEqual(v["the 6-12 s bleed bin at cb >= 60 weaker than 0-1 s by >= 0.10 (>= 20 stretches)"], "n.a.")
    self.assertEqual(v["a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)"], "n.a.")
    self.assertEqual(v["the brake-gain learner more than 0.04 above the other arm's mean"], "n.a.")
    self.assertEqual(v["a VSA/ABS lamp or a new DTC"], "MANUAL")


class TestAcceptance(unittest.TestCase):
  """C1b's acceptance checks (c1weak A_synth section 4)."""

  @staticmethod
  def onset_frames(err=0.0, late=0.0, n_apps=12):
    """n_apps clean applications, 4 s apart at 15 m/s: cb 0 -> 40, the command -0.5 from the onset; aEgo follows it
    0.3 s later (so the error is 0) plus `err`, and the grade-corrected decel leaves the coast line `late` s later."""
    dt = 0.02
    n = int((4.0 * n_apps + 4.0) / dt)
    t = np.arange(n) * dt
    cb, acc, a = np.zeros(n), np.zeros(n), np.zeros(n)
    for k in range(n_apps):
      t0 = 2.0 + 4.0 * k
      on = (t >= t0) & (t < t0 + 2.0)
      cb[on], acc[on] = 40.0, -0.5
      a[(t >= t0 + 0.3 + late) & (t < t0 + 2.3 + late)] = -0.5
      a[on] += err
    z = np.zeros(n)
    return {"t": t, "cb": cb, "la": np.ones(n, bool), "v": np.full(n, 15.0), "bp": z, "gp": z, "acc": acc, "a": a,
            "ac": a.copy(), "pump": cb > 0, "dt": np.full(n, dt)}

  def test_onsets_measure_the_error_and_the_decel_time(self):
    O = brc.onsets(self.onset_frames())
    self.assertEqual(len(O), 12)
    self.assertAlmostEqual(O[0]["err05"], 0.0, places=6)
    self.assertAlmostEqual(O[0]["d_dec10"], 0.3, delta=0.021)
    O = brc.onsets(self.onset_frames(err=0.1))
    self.assertAlmostEqual(np.mean([o["err05"] for o in O]), 0.1, delta=0.01)
    self.assertAlmostEqual(np.mean([o["err10"] for o in O]), 0.1, delta=0.01)
    O = brc.onsets(self.onset_frames(late=0.1))
    self.assertAlmostEqual(O[0]["d_dec10"], 0.4, delta=0.021)
    self.assertAlmostEqual(O[0]["err05"], 0.1, delta=0.01)   # 0.1 s of 0.5 s at +0.5: a late response is an error too
    # not clean: a pedal in the 0.6 s before, or too slow
    F = self.onset_frames()
    F["gp"] = ((F["t"] > 1.6) & (F["t"] < 1.9)).astype(float)
    self.assertEqual(len(brc.onsets(F)), 11)
    F = self.onset_frames()
    F["v"][:] = 2.0
    self.assertEqual(brc.onsets(F), [])

  @staticmethod
  def arm(err05=0.0, err10=0.0, dec=0.3, n=20, dist=4.4, dmin=3.0, last3=0.0, wz=0.5, tk=1, brk_min=2.0, pump_h=300.0,
          late=0, stops=100, berr=0, match=1.0, spread=0.02):
    rng = np.random.default_rng(1)
    on = [{"err05": err05 + spread * x, "err10": err10 + spread * x, "d_dec10": dec + spread * x} for x in rng.standard_normal(n)]
    return {"routes": ["x"], "rules": ["c1b"], "match_min": match, "onsets": on, "stop_dist_median": dist, "stop_dist_min": dmin,
            "stop_dist_n": 10, "stop_err_last3": [last3] * 10, "decel_at_stop": [wz] * 10, "takeovers_brk": tk,
            "takeovers_brk_min": brk_min, "pump_s_per_eng_h": pump_h, "late_repumps": late, "stops_held": stops,
            "brake_error_frames": berr}

  def verdict(self, P, B=None):
    return {c: s for c, s, _ in brc.acceptance(P, B)}

  def test_every_check_trips_at_its_threshold(self):
    base = self.arm()
    v = self.verdict(self.arm(), base)
    self.assertEqual(set(v.values()), {"PASS", "MANUAL"})
    E05 = f"2. clean applications, tracking error 0-0.5 s: arm minus baseline <= +{brc.ACCEPT_ONSET_DIFF} (95% upper <= +{brc.ACCEPT_ONSET_HI})"
    E10 = E05.replace("0-0.5 s", "0-1 s")
    cases = [("1. the replay of the rule matches the logged pump bit on >= 99.5% of braking frames", {"match": 0.994}),
             (E05, {"err05": 0.04}),
             (E05, {"err05": 0.025, "spread": 0.2}),                # inside on the point, outside on the upper bound
             (E10, {"err10": 0.04}),
             ("2. clean applications, decel 0.1 below the pre-onset baseline no more than 0.05 s later", {"dec": 0.36}),
             ("3. stops: median distance to a stopped lead >= 3.5 m, none below 2.2 m", {"dist": 3.4}),
             ("3. stops: median distance to a stopped lead >= 3.5 m, none below 2.2 m", {"dmin": 2.1}),
             ("3. stops: raw tracking error over the last 3 s no worse than the baseline (medians; + = less decel than asked)",
              {"last3": 0.01}),
             ("3. stops: decel at wheel-zero median <= 0.6, p90 <= 1.0 m/s^2", {"wz": 0.61}),
             ("4. driver brake take-overs <= 1.4 per minute of engaged braking", {"tk": 3}),
             ("5. pump time per engaged hour at most +25% over the baseline", {"pump_h": 376.0}),
             ("5. standstill re-pumps more than 5 s into a stop <= 2 per 100 stops", {"late": 3}),
             ("6. no BRAKE_ERROR (0x1B0)", {"berr": 1})]
    for crit, kw in cases:
      with self.subTest(crit=crit, kw=kw):
        self.assertEqual(self.verdict(self.arm(**kw), base)[crit], "FAIL")
    for kw in ({"err05": 0.02}, {"dec": 0.34}, {"dist": 3.5, "dmin": 2.2}, {"wz": 0.6}, {"tk": 2}, {"pump_h": 374.0},
               {"late": 2}, {"last3": -0.05}):
      with self.subTest(inside=kw):
        assert "FAIL" not in self.verdict(self.arm(**kw), base).values()

  def test_comparative_checks_wait_for_a_baseline_and_enough_applications(self):
    v = self.verdict(self.arm())
    for crit, verdict in v.items():
      if crit.startswith(("2.", "5. pump")) or "no worse than the baseline" in crit:
        self.assertEqual(verdict, "n.a.", crit)
    v = self.verdict(self.arm(n=9, err05=0.5), self.arm())
    self.assertEqual(v[f"2. clean applications, tracking error 0-0.5 s: arm minus baseline <= +{brc.ACCEPT_ONSET_DIFF} " +
                       f"(95% upper <= +{brc.ACCEPT_ONSET_HI})"], "n.a.")
    self.assertEqual(v["6. no VSA fault beyond the known 32-11"], "MANUAL")


class TestFiles(unittest.TestCase):
  def test_an_rlog_per_segment_and_a_qlog_only_where_there_is_none(self):
    with tempfile.TemporaryDirectory() as d:
      raw = os.path.join(d, "raw")
      os.makedirs(raw)
      for name in ("0--rlog.zst", "0--qlog.zst", "1--qlog.zst", "10--rlog.zst", "2--rlog.zst"):
        open(os.path.join(raw, name), "wb").close()
      files, qonly = brc.rlog_files(d)
      self.assertEqual([os.path.basename(f) for f in files], ["0--rlog.zst", "1--qlog.zst", "2--rlog.zst", "10--rlog.zst"])
      self.assertEqual([os.path.basename(f) for f in qonly], ["1--qlog.zst"])


if __name__ == "__main__":
  unittest.main()
