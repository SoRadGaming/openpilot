// FORK(HONDA_ACCORD_9G_AU): KTuner fix 2 - the car-bus guard's synthetic cases, natively (scons --extras, run by
// openpilot/test_native.py). The same header is compiled and driven over real routes by
// openpilot/sunnypilot/selfdrive/pandad/tests/test_car_bus_guard.py; tests/test_pandad_stock.cc drives it inside the real
// pandad pass.
#include "common/tests/native_test.h"
#include "selfdrive/pandad/car_bus_guard.h"

static CarBusSample car(uint32_t cnt, bool bus_off = false) {
  CarBusSample s;
  s.ignition = true;
  s.onroad = true;
  s.elesys_car_mode = true;
  s.bus_off_cnt = cnt;
  s.bus_off = bus_off;
  return s;
}

static CarBusSample key(uint32_t cnt, bool ignition, bool onroad) {
  CarBusSample s = car(cnt);
  s.ignition = ignition;
  s.onroad = onroad;
  return s;
}

static void test_car_bus_guard() {
  {  // a normal drive: a flat counter never latches
    CarBusGuard g;
    for (int i = 0; i < 1000; i++) REQUIRE(!g.update(car(316)));
    REQUIRE(!g.latched());
  }
  {  // route 145: 2982 carried over through elm327, +1 on the very first car-mode poll
    CarBusGuard g;
    CarBusSample elm = car(2982);
    elm.elesys_car_mode = false;
    for (int i = 0; i < 80; i++) REQUIRE(!g.update(elm));
    REQUIRE(g.update(car(2983)));
    REQUIRE(g.latched());
    REQUIRE(!g.update(car(2990)));  // true only on the poll that latches
    REQUIRE(g.latched());
  }
  {  // a panda reboot (147 started at 0): a decrease re-baselines, it is not a bus-off
    CarBusGuard g;
    REQUIRE(!g.update(car(2982)));
    REQUIRE(!g.update(car(0)));
    REQUIRE(!g.update(car(0)));
    REQUIRE(g.update(car(1)));
  }
  {  // bus_off alone latches
    CarBusGuard g;
    REQUIRE(g.update(car(5, true)));
  }
  {  // only the ELESYS car mode, onroad, ignition on, and not while fix 1 acts
    for (int k = 0; k < 4; k++) {
      CarBusGuard g;
      CarBusSample s = car(7, true);
      if (k == 0) s.elesys_car_mode = false;  // elm327, noOutput, hondaNidec param 4
      if (k == 1) s.onroad = false;
      if (k == 2) s.ignition = false;
      if (k == 3) s.diag_stock = true;
      for (uint32_t i = 0; i < 10; i++) {
        s.bus_off_cnt = 7 + i;
        REQUIRE(!g.update(s));
      }
      REQUIRE(!g.latched());
    }
  }
  {  // fix 1 acting in the same poll: no latch; once fix 1 has put the car mode back, a bus-off is fix 2's
    CarBusGuard g;
    REQUIRE(!g.update(car(1)));
    CarBusSample diag = car(2);
    diag.diag_stock = true;
    REQUIRE(!g.update(diag));
    REQUIRE(!g.latched());
    REQUIRE(!g.update(car(2)));  // the rise during fix 1 moved the baseline
    REQUIRE(g.update(car(3)));
  }
  {  // latched survives onroad dropping and returning, and a one-poll ignition dropout
    CarBusGuard g;
    REQUIRE(!g.update(car(1)));
    REQUIRE(g.update(car(2)));
    REQUIRE(!g.update(key(2, true, false)));  // onroad dropped, ignition on
    REQUIRE(g.latched());
    REQUIRE(!g.update(car(2)));
    REQUIRE(!g.update(key(2, false, true)));  // ignition off, pandad still onroad
    REQUIRE(g.latched() && !g.cleared_now());
    REQUIRE(!g.update(key(2, true, false)));  // ignition back; hardwared dropped started meanwhile
    REQUIRE(g.latched());
    REQUIRE(!g.update(car(2)));
    REQUIRE(g.latched());
  }
  {  // key-off: the ignition off AND offroad clears it, cleared_now() on that poll only
    CarBusGuard g;
    REQUIRE(!g.update(car(1)));
    REQUIRE(g.update(car(2)));
    REQUIRE(!g.update(key(2, false, true)));  // the first ignition-off poll: is_onroad still true
    REQUIRE(g.latched() && !g.cleared_now());
    REQUIRE(!g.update(key(2, false, false)));
    REQUIRE(!g.latched() && g.cleared_now());
    REQUIRE(!g.update(key(2, false, false)));
    REQUIRE(!g.cleared_now());
    REQUIRE(!g.update(car(2)));  // a new key cycle, the counter unchanged: no latch
    REQUIRE(!g.latched());
  }
  {  // seeded (pandad restarted inside the key cycle): latched from the first poll; key-off clears it
    CarBusGuard a(true);
    REQUIRE(a.latched());
    REQUIRE(!a.update(car(9)));
    REQUIRE(a.latched());
    CarBusGuard b(true);
    REQUIRE(!b.update(key(9, false, true)));
    REQUIRE(b.latched());
    REQUIRE(!b.update(key(9, false, false)));
    REQUIRE(!b.latched() && b.cleared_now());
  }
}

int main() {
  return run_native_test(test_car_bus_guard);
}
