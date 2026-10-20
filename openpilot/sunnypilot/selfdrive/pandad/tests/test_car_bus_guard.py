"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): KTuner fix 2's latch, the C++ that ships (selfdrive/pandad/car_bus_guard.h), compiled here
with a small driver and fed every 10 Hz pandaState of the owner's routes (fixtures/ktuner/*.npz, from
openpilot/sunnypilot/tools/ktuner_fixtures.py), plus synthetic cases. The sample is built the way pandad.cc's
poll_car_bus_guard() builds it (test_stock_wiring.py pins those lines): the car-side core (2 with a flipped harness),
hondaNidec with param bit 32 or 64, raw ignition, onroad, and whether fix 1 acts in the same poll.

What must hold: no latch on a normal drive; on all five incident routes a latch at the first poll the rule holds,
within 0.3 s of the first car-mode poll; bus-off only (the counter rising, or bus_off) - never TEC; only increases of
the cumulative counter count; never outside the ELESYS car mode, nor on a poll fix 1 is acting in; latched until
key-off - the ignition off AND pandad offroad - through an onroad drop and a one-poll ignition dropout; seeded from the
param across a pandad restart. A header that does not compile FAILS here; only a machine without a C++ compiler skips.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from openpilot.common.test import OpenpilotTestCase

ROOT = Path(__file__).parents[4]   # openpilot/
HEADER = ROOT / "selfdrive/pandad/car_bus_guard.h"
FIX = Path(__file__).parent / "fixtures/ktuner"

HONDA_NIDEC, ELM327, NO_OUTPUT = 1, 3, 19
ELESYS_BITS = 32 | 64
FLIPPED = 2

DRIVER = r"""
#include <cstdio>
#include "car_bus_guard.h"
int main(int argc, char **argv) {
  CarBusGuard g(argc > 1 && argv[1][0] == '1');
  int ign, on, car, diag, bo;
  unsigned cnt;
  while (std::scanf("%d,%d,%d,%d,%d,%u", &ign, &on, &car, &diag, &bo, &cnt) == 6) {
    CarBusSample s;
    s.ignition = ign; s.onroad = on; s.elesys_car_mode = car; s.diag_stock = diag; s.bus_off = bo; s.bus_off_cnt = cnt;
    const bool latched_now = g.update(s);
    std::putchar(latched_now ? 'L' : g.cleared_now() ? 'C' : g.latched() ? 'h' : '.');
  }
  std::putchar('\n');
  return 0;
}
"""

_BIN: dict[str, Path] = {}


def driver() -> Path:
  """The driver built from the shipping header. No compiler: FileNotFoundError (the test skips). A header that does not
  compile is an AssertionError carrying the compiler's output - never a skip."""
  if "bin" not in _BIN:
    cxx = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
      raise FileNotFoundError("no C++ compiler")
    d = Path(tempfile.mkdtemp(prefix="car_bus_guard_"))
    (d / "drv.cc").write_text(DRIVER)
    r = subprocess.run([cxx, "-std=c++17", "-O1", "-Wall", "-Werror", f"-I{HEADER.parent}", str(d / "drv.cc"), "-o", str(d / "drv")],
                       capture_output=True, text=True)
    if r.returncode != 0:
      raise AssertionError(f"car_bus_guard.h does not compile ({cxx}):\n{r.stdout}{r.stderr}")
    _BIN["bin"] = d / "drv"
  return _BIN["bin"]


def run(samples, seeded=False) -> str:
  """samples: (ignition, onroad, elesys_car_mode, bus_off, bus_off_cnt, diag_stock) per poll -> one char per poll."""
  text = "".join(f"{int(a)},{int(b)},{int(c)},{int(f)},{int(d)},{int(e)}\n" for a, b, c, d, e, f in samples)
  out = subprocess.run([str(driver()), "1" if seeded else "0"], input=text, capture_output=True, text=True, check=True).stdout
  return out.strip("\n")


def route_samples(name: str):
  """pandad.cc's sample from each logged pandaState (onroad: loggerd only records onroad; no fix 1 on these routes)."""
  return samples_from(dict(np.load(FIX / f"{name}.npz")))


def samples_from(z: dict):
  core = np.where(z["harness"] == FLIPPED, 2, 0)
  pick = lambda k: np.where(core == 2, z[f"c2_{k}"], z[f"c0_{k}"])  # noqa: E731
  car = (z["mode"] == HONDA_NIDEC) & ((z["param"] & ELESYS_BITS) != 0)
  n = len(car)
  s = list(zip(z["ignition"], np.ones(n, bool), car, pick("bo"), pick("cnt"), np.zeros(n, bool), strict=True))
  return z["t_ms"] / 1e3, car, s


def oracle(samples) -> int | None:
  """The rule in a few lines of Python: the first car-mode poll, fix 1 not acting, where the counter rose since the
  previous poll (any mode) or bus_off is set. TEC is not part of it."""
  prev = None
  for i, (ign, on, car, bo, cnt, diag) in enumerate(samples):
    if not ign:
      prev = None
      continue
    rose = prev is not None and cnt > prev
    prev = cnt
    if on and car and not diag and (rose or bo):
      return i
  return None


KEY_OFF = (False, False, False, False, 0, False)   # the first poll after the logged route: ignition off, pandad offroad


class GuardCase(OpenpilotTestCase):
  def setUp(self):
    try:
      driver()
    except FileNotFoundError as e:
      self.skipTest(f"no C++ compiler: {e}")


class TestRoutes(GuardCase):
  def test_normal_drives_never_latch(self):
    for name in ("r143", "r129"):
      t, car, s = route_samples(name)
      assert car.sum() > 8000, f"{name}: the car mode is most of the route"
      out = run(s)
      assert "L" not in out and oracle(s) is None, name

  def test_every_incident_route_latches_at_once(self):
    for name in ("r144", "r145", "r147", "rdb", "rdc"):
      t, car, s = route_samples(name)
      out = run(s + [KEY_OFF])
      assert out.count("L") == 1, (name, out.count("L"))
      i = out.index("L")
      assert i == oracle(s), (name, i, oracle(s))
      first_car = int(np.argmax(car))
      assert car[i] and t[i] - t[first_car] <= 0.3 + 1e-6, (name, t[i] - t[first_car])
      # latched for the rest of the key cycle: held on every logged poll - the route's own ignition-off sample too,
      # which pandad still polls onroad - and cleared once, on the first poll that is offroad as well
      assert set(out[i + 1:len(s)]) <= {"h"}, f"{name}: released before key-off"
      assert out[len(s)] == "C", (name, out[len(s):])

  def test_the_incident_routes_were_bus_off_not_only_error_passive(self):
    """The rule without TEC still latches all five within 0.3 s; with it, dc and 147 would have latched 0.1 s
    sooner. The owner's choice was "bus-off"."""
    sooner = {}
    for name in ("r144", "r145", "r147", "rdb", "rdc"):
      z = dict(np.load(FIX / f"{name}.npz"))
      t, car, s = samples_from(z)
      i = oracle(s)
      assert i is not None, name
      j = next(k for k in range(len(s)) if car[k] and z["c0_tec"][k] >= 128)
      sooner[name] = round(t[i] - t[j], 1) if j < i else 0.0
    assert sooner == {"r144": 0.0, "r145": 0.0, "r147": 0.1, "rdb": 0.0, "rdc": 0.1}, sooner


def car(cnt, bo=False, ign=True, on=True, mode=True, diag=False):
  return (ign, on, mode, bo, cnt, diag)


class TestSynthetic(GuardCase):
  def test_counter_carried_over_then_one_more(self):
    s = [car(2982, mode=False)] * 80 + [car(2983)] + [car(2990)] * 5
    assert run(s) == "." * 80 + "L" + "h" * 5

  def test_a_counter_decrease_re_baselines(self):
    assert run([car(2982), car(0), car(0), car(1)]) == "...L"

  def test_bus_off_flag(self):
    assert run([car(5, bo=True)]) == "L"
    assert run([car(5)] * 3) == "..."

  def test_never_outside_the_car_mode_or_offroad_or_while_fix_1_acts(self):
    for kw in ({"mode": False}, {"on": False}, {"ign": False}, {"diag": True}):
      assert set(run([car(i, bo=True, **kw) for i in range(20)])) == {"."}, kw

  def test_fix_1_in_the_same_poll_then_fix_2(self):
    """Review A F3: a tool plugged in mid-session - fix 1 forces the car mode down in the poll the storm shows. No
    latch; once fix 1 has put the car mode back, a bus-off is fix 2's."""
    out = run([car(1), car(2, diag=True), car(3, diag=True, mode=False), car(3, mode=False), car(3), car(4)])
    assert out == ".....L", out

  def test_latched_through_an_onroad_drop(self):
    out = run([car(1), car(2), car(2, on=False), car(2), car(2)])
    assert out == ".Lhhh", out

  def test_key_off_is_ignition_off_and_offroad(self):
    """Review A F1 / review B 1: on the first ignition-off poll pandad is still onroad - held there; cleared on the
    first poll that is offroad too."""
    out = run([car(1), car(2), car(2, ign=False), car(2, ign=False, on=False), car(2, ign=False, on=False), car(2)])
    assert out == ".LhC..", out

  def test_a_one_poll_ignition_dropout_keeps_the_latch(self):
    """The ignition off for one poll; hardwared drops started meanwhile, so the next poll has the ignition back and
    pandad offroad. Not a key cycle: held, and held into the next onroad session."""
    out = run([car(1), car(2), car(2, ign=False), car(2, on=False), car(2, on=False), car(3), car(4)])
    assert out == ".Lhhhhh", out

  def test_seeded(self):
    assert run([car(9)] * 3, seeded=True) == "hhh"
    assert run([car(9, ign=False), car(9, ign=False, on=False), car(9)], seeded=True) == "hC."

  def test_the_param_bits(self):
    """pandad.cc's ELESYS test on the logged params: 36 and 68 arm, upstream Nidec's 4 and the start-up ELM327's 1
    do not."""
    for param, armed in ((36, True), (68, True), (4, False), (0, False), (1, False)):
      assert ((param & ELESYS_BITS) != 0) == armed, param

  def synthetic_route(self, harness, c0_rise=0, c2_rise=0, c0_tec=0, c2_tec=0):
    n = 100
    z = {"t_ms": np.arange(n) * 100, "ignition": np.ones(n, bool), "mode": np.full(n, HONDA_NIDEC, np.uint8),
         "param": np.full(n, 36, np.uint16), "harness": np.full(n, harness, np.uint8)}
    for b, rise, tec in ((0, c0_rise, c0_tec), (2, c2_rise, c2_tec)):
      z[f"c{b}_bo"] = np.zeros(n, bool)
      z[f"c{b}_cnt"] = np.full(n, 391, np.uint32)
      z[f"c{b}_cnt"][-3:] += rise
      z[f"c{b}_tec"] = np.full(n, tec, np.uint8)
    return samples_from(z)[2]

  def test_bus_2_is_not_the_car_bus(self):
    """Every route: the radar side goes error-passive at ignition-off, the radar unpowered; bus 2 is never read."""
    assert "L" not in run(self.synthetic_route(harness=1, c2_rise=1, c2_tec=255))
    assert "L" in run(self.synthetic_route(harness=1, c0_rise=1))

  def test_error_passive_alone_is_not_bus_off(self):
    assert "L" not in run(self.synthetic_route(harness=1, c0_tec=255))

  def test_flipped_harness_reads_core_2(self):
    assert "L" not in run(self.synthetic_route(harness=FLIPPED, c0_rise=1))
    assert "L" in run(self.synthetic_route(harness=FLIPPED, c2_rise=1))
