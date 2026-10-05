"""
FORK(HONDA_ACCORD_9G_AU): shadow_learn_report.py reads what the shadow learners log, in the Python it will be run with.

* A route folder with a parquet but no pandas (the repo's own venv) falls back to the rlogs instead of crashing.
* The hondashadow line the car writes parses, and two routes combine by their counts: brake cells and the live brake gain
  sample-weighted, the launch sums (no lead, and behind a lead apart) added.
* Lines from before the lead split and the gain (the batch-2 replay dumps) still read.
* (batch 3) Routes from different builds are never pooled, lines from before the build tags are a group of their own,
  and a launch measured without the launch cap is never offered as a multiplier.
"""
import io
import math
import os
import sys
import tempfile
from contextlib import redirect_stdout
from unittest import mock

import openpilot.tools.lib.logreader  # noqa: F401  # imported before pandas is hidden below
from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.tools import shadow_learn_report as rep
from opendbc.sunnypilot.car.honda.shadow_learn import HondaShadowLearners


def shadow_line(brake_err=0.2, gain=1.05, lead_launch=False, build=None, gas_law=""):
  """A real line from HondaShadowLearners: 300 brake samples at 12 m/s / 80 counts, then a launch."""
  from dataclasses import dataclass, field

  @dataclass
  class Act:
    accel: float = 0.0
    longControlState: int = 0   # pid

  @dataclass
  class Hud:
    leadVisible: bool = False

  @dataclass
  class CC:
    longActive: bool = True
    actuators: Act = field(default_factory=Act)
    hudControl: Hud = field(default_factory=Hud)

  @dataclass
  class Out:
    vEgo: float = 12.0
    aEgo: float = 0.0
    gasPressed: bool = False
    brakePressed: bool = False
    stockAeb: bool = False

  @dataclass
  class CS:
    out: Out = field(default_factory=Out)
    pcm_pedal_gas: float = 0.0

  from opendbc.sunnypilot.car.honda import shadow_learn as sl
  sh = HondaShadowLearners(build)
  pid = sl.LongCtrlState.pid
  for _ in range(sl.CLEAN_HOLD + 299):
    sh.update(CC(True, Act(-1.0, pid)), CS(Out(12.0, -1.0 + brake_err)), pitch=0.0, pose_fresh=True, mode_ok=True,
              cmd_ref=-1.0, brake_frac=80.0 / sl.NIDEC_BRAKE_MAX, gas_cmd=0.0, brake_gain=gain, gas_law=gas_law)
  for _ in range(sl.CLEAN_HOLD + 149):
    sh.update(CC(True, Act(1.0, pid), Hud(lead_launch)), CS(Out(2.0, 1.4), 40.0), pitch=0.0, pose_fresh=True,
              mode_ok=True, cmd_ref=1.0, brake_frac=0.0, gas_cmd=0.15, brake_gain=gain, gas_law=gas_law)
  return sh.line()


BUILD_A = {"commit": "862540c01", "pump": "v5", "blaw": "v1", "tuner": "1"}
BUILD_B = {"commit": "862540c01", "pump": "v6", "blaw": "v1", "tuner": "1"}


def write_routes(d, lines):
  paths = []
  for name, line in lines:
    p = os.path.join(d, f"{name}.txt")
    with open(p, "w") as f:
      f.write(f"{line}\n")
    paths.append(p)
  return paths


class TestShadowLearnReport(OpenpilotTestCase):
  def test_a_route_folder_without_pandas_reads_the_rlogs(self):
    with tempfile.TemporaryDirectory() as d:
      os.makedirs(os.path.join(d, "parquet"))
      os.makedirs(os.path.join(d, "raw"))
      open(os.path.join(d, "parquet", "logMessage.parquet"), "wb").close()
      read = []
      fake_lr = mock.Mock(side_effect=lambda fn: read.append(fn) or [])
      with mock.patch.dict(sys.modules, {"pandas": None}), \
           mock.patch("openpilot.tools.lib.logreader.LogReader", fake_lr):
        open(os.path.join(d, "raw", "0--rlog.zst"), "wb").close()
        lines = rep.read_route(d)
      self.assertEqual(lines, {"hondashadow": [], "latsplit": []})
      self.assertEqual([os.path.basename(f) for f in read], ["0--rlog.zst"], "the rlogs, not a crash on 'import pandas'")

  def test_lines_parse_and_routes_combine(self):
    a, b = shadow_line(0.2, 1.05), shadow_line(0.4, 1.15, lead_launch=True)
    with tempfile.TemporaryDirectory() as d:
      paths = []
      for name, line in (("aaaa0001", a), ("aaaa0002", b)):
        p = os.path.join(d, f"{name}.txt")
        with open(p, "w") as f:
          f.write(f"12.5 {line}\n")
        paths.append(p)
      last = {os.path.basename(p): rep.read_route(p)["hondashadow"][-1] for p in paths}
      c = rep.combine_long(last)
      assert c is not None
      out = io.StringIO()
      with redirect_stdout(out):
        self.assertEqual(rep.main(paths), 0)
    da, db = last["aaaa0001.txt"], last["aaaa0002.txt"]
    i = max(range(len(c["bn"])), key=lambda k: c["bn"][k])
    self.assertEqual(c["bn"][i], da["bn"][i] + db["bn"][i])
    self.assertAlmostEqual(c["be"][i], 0.3, places=3)
    self.assertAlmostEqual(c["bgain"][0], 1.10, places=3)
    self.assertGreater(da["ln"][0], 100)
    self.assertEqual((db["ln"][0], da["lnl"][0]), (0.0, 0.0), "route b's launch was behind a lead")
    self.assertEqual((c["ln"][0], c["lnl"][0]), (da["ln"][0], db["lnl"][0]))
    self.assertEqual((c["lep"], c["lepl"]), (1.0, 1.0))
    self.assertAlmostEqual(c["lratio"][0], 1.4, places=3)
    self.assertAlmostEqual(c["lratiol"][0], 1.4, places=3)
    text = out.getvalue()
    for words in ("COMBINED over 2 routes", "behind a lead", "live brake gain"):
      assert words in text, words

  def test_routes_never_overwrite_each_other(self):
    # text dumps named alike once cut to 8 characters ('..._hondashadow.txt') used to share one name, and the combined
    # tables were the last route alone
    with tempfile.TemporaryDirectory() as d:
      paths = []
      for i, err in enumerate((0.1, 0.3)):
        p = os.path.join(d, f"0000011{i}_hondashadow.txt")
        with open(p, "w") as f:
          f.write(f"{shadow_line(err)}\n")
        paths.append(p)
      out = io.StringIO()
      with redirect_stdout(out), mock.patch.object(rep, "combine_long", wraps=rep.combine_long) as comb:
        rep.main(paths)
      per_route = comb.call_args[0][0]
    self.assertEqual(len(per_route), 2, list(per_route))
    self.assertEqual(rep.route_name("/x/15646e8515eda1a7_00000115--754172f934", {}), "00000115")
    self.assertEqual(rep.route_name("/x/15646e8515eda1a7_00000115--754172f934/", {"00000115": 1}), "00000115'")

  def test_different_builds_are_never_pooled(self):
    # batch 3: two routes that differ only in the pump rule are reported apart; the two of build A pool
    with tempfile.TemporaryDirectory() as d:
      paths = write_routes(d, [("aaaa0001", shadow_line(0.1, build=BUILD_A, gas_law="v2")),
                               ("aaaa0002", shadow_line(0.3, build=BUILD_A, gas_law="v2")),
                               ("aaaa0003", shadow_line(0.9, build=BUILD_B, gas_law="v2"))])
      out = io.StringIO()
      with redirect_stdout(out), mock.patch.object(rep, "combine_long", wraps=rep.combine_long) as comb:
        self.assertEqual(rep.main(paths), 0)
    text = out.getvalue()
    assert "NOT POOLED: the routes come from 2 different builds" in text
    self.assertEqual(comb.call_count, 1, "only build A has two routes to pool")
    pooled = comb.call_args[0][0]
    self.assertEqual(sorted(pooled), ["aaaa0001", "aaaa0002"])
    c = rep.combine_long(pooled)
    assert c is not None
    i = max(range(len(c["bn"])), key=lambda k: c["bn"][k])
    self.assertAlmostEqual(c["be"][i], 0.2, places=3, msg="0.9 from the other build must not be in the mean")
    assert "pump=v6" in text

  def test_tags_parse_as_text_and_untagged_lines_are_their_own_build(self):
    tag, d = rep.parse_line(shadow_line(build={"commit": "123456789", "pump": "v5", "blaw": "v2", "tuner": "0"}, gas_law="v2"))
    self.assertEqual((d["commit"], d["gaslaw"], d["cap"], d["pump"], d["blaw"], d["tuner"]), ("123456789", "v2", "1", "v5", "v2", "0"))
    self.assertEqual(rep.build_key(d), ("123456789", "v2", "1", "v5", "v2", "0"))
    old = " ".join(t for t in shadow_line().split() if t.split("=")[0] not in rep.BUILD_KEYS).replace(" v=2 ", " v=1 ")
    _, od = rep.parse_line(old)
    self.assertEqual(rep.build_key(od), rep.UNTAGGED)
    groups = rep.group_by_build({"a": d, "b": od, "c": None, "d": d})
    self.assertEqual(groups, {rep.build_key(d): ["a", "d"], rep.UNTAGGED: ["b"]})

  def test_a_launch_without_the_cap_is_discarded(self):
    # route 115's 0.72 was measured on a pre-cap build: printed, never offered as a multiplier
    cases = {"v1 law (no cap)": (shadow_line(build=BUILD_A, gas_law="v1"), False),
             "untagged": (shadow_line().replace(" v=2 ", " v=1 "), False),
             "v2 with the cap": (shadow_line(build=BUILD_A, gas_law="v2"), True)}
    for name, (line, usable) in cases.items():
      with self.subTest(name):
        _, d = rep.parse_line(line)
        self.assertEqual(rep.launch_usable(d), usable)
        out = io.StringIO()
        with redirect_stdout(out):
          rep.report_long("x", d)
        self.assertEqual("DISCARDED" in out.getvalue(), not usable, out.getvalue())

  def test_older_lines_still_read(self):
    old = " ".join(t for t in shadow_line().split() if t.split("=")[0] not in ("lnl", "lral", "lrrl", "lratiol", "lepl", "bgain"))
    tag, d = rep.parse_line(old)
    self.assertEqual(tag, "hondashadow")
    out = io.StringIO()
    with redirect_stdout(out):
      rep.report_long("x", d)
      c = rep.combine_long({"a": d, "b": d})
    assert c is not None
    assert "lnl" not in c
    self.assertTrue(math.isfinite(c["lratio"][0]))
