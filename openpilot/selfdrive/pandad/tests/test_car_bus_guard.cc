// FORK(HONDA_ACCORD_9G_AU): KTuner fix 2 - the car-bus guard's synthetic cases, natively (scons --extras). The same
// header is compiled and driven over real routes by openpilot/sunnypilot/selfdrive/pandad/tests/test_car_bus_guard.py.
#include "common/tests/native_test.h"
#include "selfdrive/pandad/car_bus_guard.h"

static CarBusSample car(uint32_t cnt, uint8_t tec = 0, bool bus_off = false) {
  CarBusSample s;
  s.ignition = true;
  s.onroad = true;
  s.elesys_car_mode = true;
  s.bus_off_cnt = cnt;
  s.tec = tec;
  s.bus_off = bus_off;
  return s;
}

static void test_car_bus_guard() {
  {  // a normal drive: a flat counter and a low TEC never latch
    CarBusGuard g;
    for (int i = 0; i < 1000; i++) REQUIRE(!g.update(car(316, (uint8_t)(i % 6))));
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
  {  // TEC: 127 no, 128 yes; bus_off alone latches
    CarBusGuard a;
    REQUIRE(!a.update(car(5, 127)));
    REQUIRE(a.update(car(5, 128)));
    CarBusGuard b;
    REQUIRE(b.update(car(5, 0, true)));
  }
  {  // only the ELESYS car mode, onroad, ignition on
    for (int k = 0; k < 3; k++) {
      CarBusGuard g;
      CarBusSample s = car(7, 255, true);
      if (k == 0) s.elesys_car_mode = false;  // elm327, noOutput, hondaNidec param 4
      if (k == 1) s.onroad = false;
      if (k == 2) s.ignition = false;
      for (int i = 0; i < 10; i++) REQUIRE(!g.update(s));
      REQUIRE(!g.latched());
    }
  }
  {  // latched survives onroad dropping and returning; ignition off clears it, cleared_now() on that poll only
    CarBusGuard g;
    REQUIRE(!g.update(car(1)));
    REQUIRE(g.update(car(2)));
    CarBusSample off = car(2);
    off.onroad = false;
    REQUIRE(!g.update(off));
    REQUIRE(g.latched());
    REQUIRE(!g.update(car(2)));
    REQUIRE(g.latched());
    CarBusSample ign = car(2);
    ign.ignition = false;
    REQUIRE(!g.update(ign));
    REQUIRE(!g.latched() && g.cleared_now());
    REQUIRE(!g.update(ign));
    REQUIRE(!g.cleared_now());
    REQUIRE(!g.update(car(2)));  // a new key cycle, the counter unchanged: no latch
    REQUIRE(!g.latched());
  }
  {  // seeded (pandad restarted inside the key cycle): latched from the first poll; an ignition-off first poll clears it
    CarBusGuard a(true);
    REQUIRE(a.latched());
    REQUIRE(!a.update(car(9)));
    REQUIRE(a.latched());
    CarBusGuard b(true);
    CarBusSample ign = car(9);
    ign.ignition = false;
    REQUIRE(!b.update(ign));
    REQUIRE(!b.latched() && b.cleared_now());
  }
}

int main() {
  return run_native_test(test_car_bus_guard);
}
