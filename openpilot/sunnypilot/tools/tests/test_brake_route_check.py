"""
FORK(HONDA_ACCORD_9G_AU): brake_route_check.py reads a route and judges the pump rule's and the brake law's proof plan.

* Its bit positions are the DBC's: frames packed by opendbc's own CANPacker decode to what was packed.
* The rules it replays are the controller's own functions (and its fallback transcription of C1 is the same rule).
* End to end on a synthetic route written as a real rlog: the rule is read from CarParamsSP, the replay of the rule the
  car ran matches the pump bit it logged, a hold that does not move passes and one whose XMISSION_SPEED moves aborts,
  the VSA's ripple onset, the learner and the JSON output come through.
* The parquet export reads to the same frames as the rlogs (when pyarrow is installed; the repo's venv has none).
* The verdicts follow the plan's thresholds, and the comparative ones wait for a baseline arm.
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


def _pump_stream(rule_fn, cb, v, t, v6):
  on = []
  if v6:
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


def synth_route(d: str, flags: int = 16, creep: bool = False, brake_error: bool = False) -> str:
  """A 50 s engaged drive written as a real rlog: cruise, a two-step brake application, a stop, a 15 s hold at 189
  counts behind a stopped lead, a launch. The 0x1FA pump bit is what the rule the flags select would send."""
  packer = CANPacker(DBC)
  v6 = bool(flags & 16)
  rule = ccm.brake_pump_c1_elesys if v6 else ccm.brake_pump_hysteresis_elesys
  n = 5000                                   # 100 Hz
  t = np.arange(n) * 0.01
  v = np.where(t < 10, 15.0, np.where(t < 25, 15.0 * (25 - t) / 15, np.where(t < 40, 0.0, 1.0 * (t - 40))))
  v = np.clip(v, 0.0, None)
  cb = np.where(t < 10, 0, np.where(t < 12, (t - 10) * 40, np.where(t < 15, 80, np.where(t < 17, 80 + (t - 15) * 35,
                np.where(t < 25, 150, np.where(t < 26, 150 + (t - 25) * 39, np.where(t < 40, 189, 0)))))))
  cb = cb.astype(int)
  a = np.r_[np.diff(v) / 0.01, 0.0]
  t0 = 1_000_000_000
  mono = t0 + 1000 + np.arange(n, dtype=np.int64) * 10_000_000
  t_rel = (mono - t0) / 1e9                  # the times the script will see, so its replay sees the same numbers
  even = np.arange(n) % 2 == 0
  pump = np.zeros(n, dtype=bool)
  pump[even] = _pump_stream(rule, cb[even], v[even], t_rel[even], v6)
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

    def cs(o, vi=vi, ai=ai, stopped=stopped):
      o.vEgo, o.aEgo, o.standstill = vi, ai, stopped
      o.cruiseState.enabled = True
    add("carState", mono[i], cs)

    def cc(o, i=i, stopped=stopped):
      o.longActive = True
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
    xm = 0.5 if (creep and 30.0 <= t[i] < 31.0) else vi * 3.6
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

      def rad(o, stopped=stopped):
        o.leadOne.present = bool(stopped)
        o.leadOne.dRel, o.leadOne.vLead = (4.0, 0.0) if stopped else (0.0, 0.0)
      add("radarState", mono[i], rad)
      add("deviceMotion", mono[i], lambda o, vi=vi: setattr(o.velocityDevice, "x", vi))
    add("accelerometer", mono[i], lambda o, ai=ai: setattr(o.acceleration, "v", [9.81, 0.0, -ai]))
    if i % 500 == 0:
      m = messaging.new_message(None)          # logMessage is a text field: nothing to init
      m.logMonoTime = int(mono[i])
      m.logMessage = json.dumps({"msg": "hondadyn gaslaw=v2 brake=1.005 brakec=1.005 tuner=1 pump=v6"})
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
    self.assertIs(fns["v6"][0], ccm.brake_pump_c1_elesys)

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
    a = brc.replay_rule("v6", cb, v, t)
    b = brc.replay_rule("v6", cb, v, t, {"v6": (brc._c1_reference, "ref")})
    self.assertGreater(a.sum(), 500)
    np.testing.assert_array_equal(a, b)


class TestEndToEnd(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.tmp = tempfile.TemporaryDirectory()
    cls.ok = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000aa--0000000000"))
    cls.crept = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000ab--0000000000"), creep=True, brake_error=True)
    cls.v5 = synth_route(os.path.join(cls.tmp.name, "15646e8515eda1a7_000000ac--0000000000"), flags=0)

  @classmethod
  def tearDownClass(cls):
    cls.tmp.cleanup()

  def test_a_clean_route(self):
    R = brc.check_route(self.ok, force_rlog=True, workers=1)
    self.assertEqual((R["rule"], R["brake_law"], R["commit"], R["name"]), ("v6", "v1", "abcdef123", "000000aa"))
    assert "CarParamsSP" in R["rule_how"]
    self.assertEqual(R["match"], 1.0, "the replay of the rule the car ran is what it logged")
    w = R["pump"]["wire"]
    self.assertEqual((w["starts"], w["pump_s"]), (R["pump"]["v6"]["starts"], R["pump"]["v6"]["pump_s"]))
    self.assertGreater(w["starts"], 0)
    self.assertEqual(len(R["stops_pump"]), 1)
    self.assertLessEqual(R["stops_pump"][0]["bursts"], 1)
    self.assertEqual(len(R["holds"]), 1)
    h = R["holds"][0]
    self.assertGreater(h["s"], 13.0)
    self.assertFalse(h["moved"])
    self.assertAlmostEqual(h["radar_change"], 0.0, places=3)
    self.assertLess(h["camera_disp"], 0.01)
    self.assertAlmostEqual(R["vsa"]["comp_braking_frac"], 1.0)
    self.assertEqual(R["vsa"]["brake_error_frames"], 0)
    self.assertTrue(0.10 <= R["vsa"]["ripple_onset_median"] <= 0.16, R["vsa"])
    self.assertEqual((R["learner"]["n"], R["learner"]["last"]), (10, 1.005))
    self.assertGreater(R["grade_s"], 30.0)
    v = {c: s for c, s, _ in brc.verdicts(brc.pool([R]), None)}
    self.assertEqual(v["a hold that moves with cb >= 100 and no planner launch"], "PASS")
    self.assertEqual(v["any BRAKE_ERROR (0x1B0)"], "PASS")
    self.assertEqual(v["a moving pump-off at cb >= 100 longer than 6.1 s"], "PASS")
    self.assertEqual(v["a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)"], "n.a.")

  def test_a_hold_that_creeps_and_a_brake_error_abort(self):
    R = brc.check_route(self.crept, force_rlog=True, workers=1)
    self.assertTrue(R["holds"][0]["moved"] and R["holds"][0]["xmission"])
    self.assertGreater(R["vsa"]["brake_error_frames"], 0)
    v = {c: s for c, s, _ in brc.verdicts(brc.pool([R]), None)}
    self.assertEqual(v["a hold that moves with cb >= 100 and no planner launch"], "ABORT")
    self.assertEqual(v["any BRAKE_ERROR (0x1B0)"], "ABORT")

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
    self.assertEqual(J["baseline"][0]["rule"], "v6")
    self.assertTrue(all(len(x) == 3 for x in J["verdicts"]))

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
      self.assertEqual(Sp["meta"]["flags_sp"], 16, "CarParamsSP from the rlog: the owner's export cannot decode it")
      Fr, Fp = brc.build_frames(S), brc.build_frames(Sp)
      for k in ("t", "cb", "pump", "v", "acc", "la", "lcs", "ac", "ff", "xmission", "comp_braking", "dRel", "cam_vx", "ui"):
        np.testing.assert_array_equal(Fr[k], Fp[k], err_msg=k)
    finally:
      import shutil
      shutil.rmtree(pdir)


class TestVerdicts(unittest.TestCase):
  @staticmethod
  def arm(gain=-0.8, bleed6=0.0, stretches=25, dist=4.0, learner=1.0, off=3.0, moved=0, suspect=0, berr=0):
    return {"routes": ["x"], "gain": [{"s": 100.0, "per100": gain}] * len(brc.GAIN_BANDS), "slope100": gain,
            "bleed60": [{"err": 0.0, "stretches": 99}, {"err": 0.0, "stretches": 50}, {"err": 0.0, "stretches": 30},
                        {"err": bleed6, "stretches": stretches}, {"err": math.nan, "stretches": 0}],
            "stop_dist_median": dist, "stop_dist_min": dist, "stop_dist_n": 10, "learner_mean": learner,
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
             ("the brake-gain learner more than 0.04 above the other arm's mean", {"learner": 1.05}),
             ("any BRAKE_ERROR (0x1B0)", {"berr": 1}),
             ("a moving pump-off at cb >= 100 longer than 6.1 s", {"off": 6.2}),
             ("a hold that moves with cb >= 100 and no planner launch", {"moved": 1})]
    for crit, kw in cases:
      with self.subTest(crit=crit, kw=kw):
        self.assertEqual(self.verdict(self.arm(**kw), base)[crit], "ABORT")
    # just inside
    for kw in ({"gain": -0.71}, {"bleed6": 0.09}, {"dist": 3.6}, {"learner": 1.03}, {"off": 6.1}):
      with self.subTest(inside=kw):
        assert "ABORT" not in self.verdict(self.arm(**kw), base).values()
    self.assertEqual(self.verdict(self.arm(suspect=1), base)["a hold that moves with cb >= 100 and no planner launch"], "CHECK")

  def test_too_little_data_or_no_baseline_is_not_a_pass(self):
    v = self.verdict(self.arm(bleed6=0.5, stretches=19))
    self.assertEqual(v["the 6-12 s bleed bin at cb >= 60 weaker than 0-1 s by >= 0.10 (>= 20 stretches)"], "n.a.")
    self.assertEqual(v["a steady-gain band weaker by > 0.10 per 100 counts (>= 60 s each)"], "n.a.")
    self.assertEqual(v["the brake-gain learner more than 0.04 above the other arm's mean"], "n.a.")
    self.assertEqual(v["a VSA/ABS lamp or a new DTC"], "MANUAL")


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
