"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): KTuner fix 2's latch, the C++ that ships (selfdrive/pandad/car_bus_guard.h), compiled here
with a small driver and fed every 10 Hz pandaState of the owner's routes (fixtures/ktuner/*.npz, from
openpilot/sunnypilot/tools/ktuner_fixtures.py), plus synthetic cases. The sample is built the way pandad.cc's
poll_car_bus_guard() builds it (test_stock_wiring.py pins those lines): the car-side core (2 with a flipped harness),
hondaNidec with param bit 32 or 64, raw ignition, onroad.

What must hold: no latch on a normal drive; on all five incident routes a latch at the first poll the rule holds,
within 0.3 s of the first car-mode poll; only increases of the cumulative counter count; never outside the ELESYS car
mode; latched until ignition off, through an onroad drop; seeded from the param across a pandad restart.
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
TEC_LIMIT = 128

DRIVER = r"""
#include <cstdio>
#include "car_bus_guard.h"
int main(int argc, char **argv) {
  CarBusGuard g(argc > 1 && argv[1][0] == '1');
  int ign, on, car, bo;
  unsigned cnt, tec;
  while (std::scanf("%d,%d,%d,%d,%u,%u", &ign, &on, &car, &bo, &cnt, &tec) == 6) {
    CarBusSample s;
    s.ignition = ign; s.onroad = on; s.elesys_car_mode = car; s.bus_off = bo; s.bus_off_cnt = cnt; s.tec = (uint8_t)tec;
    const bool latched_now = g.update(s);
    std::putchar(latched_now ? 'L' : g.cleared_now() ? 'C' : g.latched() ? 'h' : '.');
  }
  std::putchar('\n');
  return 0;
}
"""

_BIN: dict[str, Path] = {}


def driver() -> Path:
  if "bin" not in _BIN:
    cxx = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
    if cxx is None:
      raise FileNotFoundError("no C++ compiler")
    d = Path(tempfile.mkdtemp(prefix="car_bus_guard_"))
    (d / "drv.cc").write_text(DRIVER)
    subprocess.run([cxx, "-std=c++17", "-O1", "-Wall", "-Werror", f"-I{HEADER.parent}", str(d / "drv.cc"), "-o", str(d / "drv")],
                   check=True, capture_output=True)
    _BIN["bin"] = d / "drv"
  return _BIN["bin"]


def run(samples, seeded=False) -> str:
  """samples: (ignition, onroad, elesys_car_mode, bus_off, bus_off_cnt, tec) per poll -> one char per poll."""
  text = "".join(f"{int(a)},{int(b)},{int(c)},{int(d)},{int(e)},{int(f)}\n" for a, b, c, d, e, f in samples)
  out = subprocess.run([str(driver()), "1" if seeded else "0"], input=text, capture_output=True, text=True, check=True).stdout
  return out.strip("\n")


def route_samples(name: str):
  """pandad.cc's sample from each logged pandaState (onroad: loggerd only records onroad)."""
  return samples_from(dict(np.load(FIX / f"{name}.npz")))


def samples_from(z: dict):
  core = np.where(z["harness"] == FLIPPED, 2, 0)
  pick = lambda k: np.where(core == 2, z[f"c2_{k}"], z[f"c0_{k}"])  # noqa: E731
  car = (z["mode"] == HONDA_NIDEC) & ((z["param"] & ELESYS_BITS) != 0)
  s = list(zip(z["ignition"], np.ones(len(car), bool), car, pick("bo"), pick("cnt"), pick("tec"), strict=True))
  return z["t_ms"] / 1e3, car, s


def oracle(samples) -> int | None:
  """The rule in ten lines of Python: the first car-mode poll where the counter rose since the previous poll (any mode),
  or bus-off, or TEC >= 128."""
  prev = None
  for i, (ign, on, car, bo, cnt, tec) in enumerate(samples):
    if not ign:
      prev = None
      continue
    rose = prev is not None and cnt > prev
    prev = cnt
    if on and car and (rose or bo or tec >= TEC_LIMIT):
      return i
  return None


class GuardCase(OpenpilotTestCase):
  def setUp(self):
    try:
      driver()
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
      self.skipTest(f"cannot build the driver: {e}")


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
      out = run(s)
      assert out.count("L") == 1, (name, out.count("L"))
      i = out.index("L")
      assert i == oracle(s), (name, i, oracle(s))
      first_car = int(np.argmax(car))
      assert car[i] and t[i] - t[first_car] <= 0.3 + 1e-6, (name, t[i] - t[first_car])
      # latched for the rest of the key cycle: held on every poll until the route's own ignition-off sample (the last
      # pandaState of a route that ends at key-off), which clears it once
      ign = np.array([p[0] for p in s], bool)
      off = np.flatnonzero(~ign[i + 1:])
      end = i + 1 + (int(off[0]) if len(off) else len(out) - i - 1)
      assert set(out[i + 1:end]) <= {"h"}, f"{name}: released before ignition off"
      if len(off):
        assert out[end] == "C" and set(out[end + 1:]) <= {"."}, (name, out[end:])


def car(cnt, tec=0, bo=False, ign=True, on=True, mode=True):
  return (ign, on, mode, bo, cnt, tec)


class TestSynthetic(GuardCase):
  def test_counter_carried_over_then_one_more(self):
    s = [car(2982, mode=False)] * 80 + [car(2983)] + [car(2990)] * 5
    assert run(s) == "." * 80 + "L" + "h" * 5

  def test_a_counter_decrease_re_baselines(self):
    assert run([car(2982), car(0), car(0), car(1)]) == "...L"

  def test_tec(self):
    assert run([car(5, 127)] * 3) == "..."
    assert run([car(5, 127), car(5, 128)]) == ".L"
    assert run([car(5, 0, bo=True)]) == "L"

  def test_never_outside_the_car_mode_or_offroad(self):
    for kw in ({"mode": False}, {"on": False}, {"ign": False}):
      assert set(run([car(i, 255, bo=True, **kw) for i in range(20)])) == {"."}, kw

  def test_latched_through_an_onroad_drop_until_ignition_off(self):
    out = run([car(1), car(2), car(2, on=False), car(2), car(2, ign=False), car(2, ign=False), car(2), car(2)])
    assert out == ".Lhh" + "C." + "..", out

  def test_seeded(self):
    assert run([car(9)] * 3, seeded=True) == "hhh"
    assert run([car(9, ign=False), car(9)], seeded=True) == "C."

  def test_the_param_bits(self):
    """pandad.cc's ELESYS test on the logged params: 36 and 68 arm, upstream Nidec's 4 and the start-up ELM327's 1
    do not."""
    for param, armed in ((36, True), (68, True), (4, False), (0, False), (1, False)):
      assert ((param & ELESYS_BITS) != 0) == armed, param

  def synthetic_route(self, harness, c0_tec_end=0, c2_tec_end=0):
    n = 100
    z = {"t_ms": np.arange(n) * 100, "ignition": np.ones(n, bool), "mode": np.full(n, HONDA_NIDEC, np.uint8),
         "param": np.full(n, 36, np.uint16), "harness": np.full(n, harness, np.uint8)}
    for b, tec_end in ((0, c0_tec_end), (2, c2_tec_end)):
      z[f"c{b}_bo"] = np.zeros(n, bool)
      z[f"c{b}_cnt"] = np.full(n, 391, np.uint32)
      z[f"c{b}_tec"] = np.zeros(n, np.uint8)
      z[f"c{b}_tec"][-3:] = tec_end
    return samples_from(z)[2]

  def test_bus_2_at_ignition_off_is_not_the_car_bus(self):
    """Every route: the radar side goes error-passive (TEC 128) at ignition-off, the radar unpowered."""
    assert "L" not in run(self.synthetic_route(harness=1, c2_tec_end=128))
    assert "L" in run(self.synthetic_route(harness=1, c0_tec_end=128))

  def test_flipped_harness_reads_core_2(self):
    assert "L" not in run(self.synthetic_route(harness=FLIPPED, c0_tec_end=128))
    assert "L" in run(self.synthetic_route(harness=FLIPPED, c2_tec_end=128))
