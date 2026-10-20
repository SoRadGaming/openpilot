#pragma once

#include <cstdint>

// FORK(HONDA_ACCORD_9G_AU): KTuner fix 2 (2026-10-19). The car bus went bus-off - or our own transmissions went
// error-passive - while the panda was in this car's safety mode: leave the car mode (pandad.cc forces NO_OUTPUT in the
// same poll, which closes the harness relay so the radar is back on the car's wire, and re-inits the stuck core) and
// stay out of it until the ignition is switched off.
//
// Why these signals, on the car-side FDCAN core only (core 2 with a flipped harness):
//   * bus_off_cnt ROSE since the last poll. It is cumulative since panda boot and survives routes (145 started at
//     2982, 147 at 0 after a panda reboot), so only an increase counts and a decrease just re-baselines. The baseline
//     moves on every poll, car mode or not, so a bus-off inside the first 100 ms of car mode is caught (145).
//   * bus_off at the poll.
//   * TEC >= 128: error-passive by OUR transmissions (REC is the other nodes' errors and is not looked at). The panda's
//     forwarded CMBS 0x1FA is already failing there.
// Every one of them was zero on 179 normal car-mode routes (47.2 h, bus-0 TEC never above 5); one of them held within
// 0.0-0.2 s of the first car-mode poll on all five incident routes (db, dc, 144, 145, 147). Buses 1 and 2 are never
// looked at: bus 2 goes error-passive at every ignition-off, bus 1 during openpilot's own FW query.
//
// Why latched until ignition off, not until the tool goes quiet: route db had no tool traffic at all and still went
// bus-off, so a re-arm would cycle; and onroad can drop and return without a key cycle, which must never put the car
// mode back mid-drive. pandad seeds it from CarBusOffStock, so a pandad restart inside the key cycle stays out too.
//
// Header-only and free of cereal on purpose: test_car_bus_guard.py compiles this exact file.
struct CarBusSample {
  bool ignition = false;         // raw ignition (line or CAN, after STARTED spoofing) - not OffroadMode
  bool onroad = false;
  bool elesys_car_mode = false;  // hondaNidec with param bit 32 (stand-down) or 64 (stock ACC mode)
  bool bus_off = false;          // the car-side core
  uint32_t bus_off_cnt = 0;
  uint8_t tec = 0;
};

class CarBusGuard {
 public:
  static constexpr uint8_t TEC_LIMIT = 128;

  explicit CarBusGuard(bool seeded = false) : latched_(seeded) {}

  // One 10 Hz poll. True only on the poll that latches.
  bool update(const CarBusSample &s) {
    if (!s.ignition) {
      cleared_ = latched_;
      latched_ = false;
      have_prev_ = false;
      return false;
    }
    cleared_ = false;
    const bool rose = have_prev_ && s.bus_off_cnt > prev_cnt_;
    prev_cnt_ = s.bus_off_cnt;  // a decrease (panda reboot) just re-baselines
    have_prev_ = true;
    if (latched_ || !s.onroad || !s.elesys_car_mode) {
      return false;
    }
    if (rose || s.bus_off || s.tec >= TEC_LIMIT) {
      latched_ = true;
      return true;
    }
    return false;
  }

  bool latched() const { return latched_; }
  bool cleared_now() const { return cleared_; }  // the poll that reset a latch (ignition off)
  uint32_t prev_bus_off_cnt() const { return prev_cnt_; }

 private:
  bool latched_ = false;
  bool have_prev_ = false;
  bool cleared_ = false;
  uint32_t prev_cnt_ = 0;
};
