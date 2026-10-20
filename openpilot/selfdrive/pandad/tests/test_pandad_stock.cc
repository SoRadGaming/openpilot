// FORK(HONDA_ACCORD_9G_AU): KTuner (2026-10-19) - pandad's stay-stock wiring, the REAL pandad.cc, panda_safety.cc and
// car_bus_guard.h driven poll by poll against a fake panda (this file defines Panda's methods; panda.cc is not linked).
// Each poll is process_panda_safety(), pandad_run()'s 10 Hz pass, exactly. Built by `scons --extras`, run by
// openpilot/test_native.py.
//
// What must hold, in every scenario:
//   * no car safety mode is set on, or left in after, a poll that reads the ignition off (the 2026-10-20 review: after a
//     bus-off latch, key-off put hondaNidec back for one poll);
//   * stock is reached by leaving the car mode for NO_OUTPUT in the poll that decides it, never by changing its param;
//   * every car mode is sent as at a normal start: 0xdf (alternative experience, accepted outside a car mode only) and
//     then 0xdc with CarParams' own model and param - 36, or 68 in stock ACC mode;
//   * the bus-off latch holds until key-off (the ignition off AND pandad offroad), through a one-poll ignition dropout
//     and a new onroad session, keeps CarBusOffStock set, and does not latch on a poll fix 1 is already acting in.
#include <sys/stat.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include <capnp/serialize.h>

#include "common/params.h"
#include "common/tests/native_test.h"
#include "common/util.h"
#include "openpilot/cereal/gen/cpp/car.capnp.h"
#include "openpilot/cereal/gen/cpp/custom.capnp.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "selfdrive/pandad/car_bus_guard.h"
#include "selfdrive/pandad/pandad.h"

using SafetyModel = cereal::CarParams::SafetyModel;

namespace {

struct Call {
  std::string req;  // "0xdc" set safety mode, "0xdf" set alternative experience
  int a;
  int b;
  bool operator==(const Call &o) const { return req == o.req && a == o.a && b == o.b; }
};

bool is_car(int m) {
  return m != (int)SafetyModel::SILENT && m != (int)SafetyModel::NO_OUTPUT && m != (int)SafetyModel::ELM327 &&
         m != (int)SafetyModel::ALL_OUTPUT;
}

struct Board {  // what the panda holds
  int mode = (int)SafetyModel::NO_OUTPUT;
  uint16_t param = 0;
  uint16_t alt = 0;
  bool ignition = true;
  bool health_ok = true;
  uint32_t bus_off_cnt = 0;
  std::vector<Call> calls;  // this poll's control writes
};
Board board;

constexpr uint16_t ALT_EXP = 7;
constexpr uint16_t PARAM_SP = 8;

}  // namespace

// ---- the fake panda: Panda's public methods ----
Panda::Panda(std::string serial) {}
bool Panda::connected() { return true; }
bool Panda::comms_healthy() { return true; }
std::string Panda::hw_serial() { return "fake"; }
std::vector<std::string> Panda::list() { return {"fake"}; }
cereal::PandaState::PandaType Panda::get_hw_type() { return cereal::PandaState::PandaType::TRES; }
void Panda::set_safety_model(cereal::CarParams::SafetyModel safety_model, uint16_t safety_param) {
  board.calls.push_back({"0xdc", (int)safety_model, safety_param});
  board.mode = (int)safety_model;
  board.param = safety_param;
}
void Panda::set_alternative_experience(uint16_t alternative_experience, uint16_t safety_param_sp) {
  board.calls.push_back({"0xdf", alternative_experience, safety_param_sp});
  if (!is_car(board.mode)) board.alt = alternative_experience;  // the panda's 0xdf: outside a car mode only
}
std::string Panda::serial_read(int port_number) { return ""; }
void Panda::set_uart_baud(int uart, int rate) {}
void Panda::set_fan_speed(uint16_t fan_speed) {}
uint16_t Panda::get_fan_speed() { return 0; }
void Panda::set_ir_pwr(uint16_t ir_pwr) {}
std::optional<health_t> Panda::get_state() {
  if (!board.health_ok) return std::nullopt;
  health_t h{};
  h.flags_pkt = board.ignition ? HEALTH_FLAG_IGNITION_LINE : 0U;
  h.car_harness_status_pkt = (uint8_t)cereal::PandaState::HarnessStatus::NORMAL;
  h.safety_mode_pkt = (uint8_t)board.mode;
  h.safety_param_pkt = board.param;
  h.alternative_experience_pkt = board.alt;
  return h;
}
std::optional<can_health_t> Panda::get_can_state(uint16_t can_number) {
  can_health_t c{};
  if (can_number == 0) c.bus_off_cnt = board.bus_off_cnt;
  return c;
}
void Panda::set_loopback(bool loopback) {}
std::optional<std::vector<uint8_t>> Panda::get_firmware_version() { return std::vector<uint8_t>{}; }
bool Panda::up_to_date() { return true; }
std::optional<std::string> Panda::get_serial() { return std::string("fake"); }
void Panda::set_power_saving(bool power_saving) {}
void Panda::enable_deepsleep() {}
void Panda::send_heartbeat(bool engaged, bool engaged_mads) {}
void Panda::set_can_speed_kbps(uint16_t bus, uint16_t speed) {}
void Panda::set_can_fd_auto(uint16_t bus, bool enabled) {}
void Panda::set_data_speed_kbps(uint16_t bus, uint16_t speed) {}
void Panda::set_canfd_non_iso(uint16_t bus, bool non_iso) {}
void Panda::can_send(const capnp::List<cereal::CanData>::Reader &can_data_list) {}
bool Panda::can_receive(std::vector<can_frame> &out_vec) { return true; }
void Panda::can_reset_communications() {}

namespace {

std::string to_bytes(capnp::MallocMessageBuilder &mb) {
  auto words = capnp::messageToFlatArray(mb);
  auto bytes = words.asBytes();
  return std::string(bytes.begin(), bytes.end());
}

void put_car_params(Params &p, uint16_t safety_param) {
  capnp::MallocMessageBuilder mb;
  auto cp = mb.initRoot<cereal::CarParams>();
  auto sc = cp.initSafetyConfigs(1);
  sc[0].setSafetyModel(SafetyModel::HONDA_NIDEC);
  sc[0].setSafetyParam(safety_param);
  cp.setAlternativeExperience(ALT_EXP);
  p.put("CarParams", to_bytes(mb));
  capnp::MallocMessageBuilder mb_sp;
  mb_sp.initRoot<cereal::CarParamsSP>().setSafetyParam(PARAM_SP);
  p.put("CarParamsSP", to_bytes(mb_sp));
}

bool wait_param(Params &p, const char *key, bool want) {
  for (int i = 0; i < 400; i++) {
    if (p.getBool(key) == want) return true;
    usleep(5000);
  }
  return false;
}

std::string dump(const std::vector<Call> &calls) {
  std::string s;
  for (const auto &c : calls) s += c.req + "(" + std::to_string(c.a) + "," + std::to_string(c.b) + ") ";
  return s;
}

struct Poll {
  bool ign = true;
  bool onroad = true;
  bool diag = false;            // DiagToolStock, as card wrote it
  bool controls_ready = true;   // ControlsReady, as card wrote it
};

class Rig {
 public:
  // a pandad start; seeded: CarBusOffStock is set (pandad restarted inside a key cycle), else every scenario starts clean
  explicit Rig(uint16_t car_param = 36, bool seeded = false)
      : car_param_(car_param), panda_("fake"), pm_({"pandaStates"}), safety_(&panda_), guard_(params_.getBool("CarBusOffStock")) {
    REQUIRE(guard_.latched() == seeded);
    board = Board();
    params_.putBool("FirmwareQueryDone", true);
    params_.putBool("ObdMultiplexingEnabled", false);
    params_.putBool("OffroadMode", false);
    put_car_params(params_, car_param);
  }

  // One 10 Hz pass; returns this poll's control writes. Checks the invariants that hold in every scenario.
  std::vector<Call> poll(const Poll &p, uint32_t bus_off_cnt_rise = 0) {
    params_.putBool("DiagToolStock", p.diag);
    params_.putBool("ControlsReady", p.controls_ready);
    board.ignition = p.ign;
    board.bus_off_cnt += bus_off_cnt_rise;
    board.calls.clear();
    process_panda_safety(&panda_, &pm_, &safety_, &guard_, false, false, p.onroad, false, false);
    usleep(2000);
    const std::vector<Call> calls = board.calls;
    for (size_t i = 0; i < calls.size(); i++) {
      if (calls[i].req != "0xdc" || !is_car(calls[i].a)) continue;
      REQUIRE(p.ign);                                   // never a car mode on an ignition-off poll
      REQUIRE(calls[i].a == (int)SafetyModel::HONDA_NIDEC && calls[i].b == car_param_);  // CarParams' own, never edited
      REQUIRE(i > 0 && calls[i - 1] == (Call{"0xdf", ALT_EXP, PARAM_SP}));  // the start-up bytes, in their order
    }
    if (!p.ign) REQUIRE(!is_car(board.mode));            // and never left in one
    return calls;
  }

  bool car() const { return is_car(board.mode); }
  int mode() const { return board.mode; }
  bool latched() const { return guard_.latched(); }
  Params &params() { return params_; }

  // a start: the start-up ELM327 while card fingerprints, then the car mode
  std::vector<Call> start() {
    poll({true, true, false, false});
    REQUIRE(board.mode == (int)SafetyModel::ELM327 && board.param == 1);
    auto calls = poll({});
    REQUIRE(car());
    return calls;
  }

  // the key switched off: the first poll with pandad still onroad, then offroad. A latch's CarBusOffStock (written
  // non-blocking) has landed by then, as it has in the car, 100 ms a poll.
  void key_off() {
    REQUIRE(wait_param(params_, "CarBusOffStock", guard_.latched()));
    poll({false, true});
    REQUIRE(!guard_.cleared_now());
    poll({false, false});
    REQUIRE(!guard_.latched() && wait_param(params_, "CarBusOffStock", false));
  }

 private:
  uint16_t car_param_;
  Params params_;
  Panda panda_;
  PubMaster pm_;
  PandaSafety safety_;
  CarBusGuard guard_;
};

const std::vector<Call> CAR_MODE_36 = {{"0xdf", ALT_EXP, PARAM_SP}, {"0xdc", (int)SafetyModel::HONDA_NIDEC, 36}};
const std::vector<Call> TO_NO_OUTPUT = {{"0xdc", (int)SafetyModel::NO_OUTPUT, 0}};

// one Rig at a time below: each holds the pandaStates publisher

void normal_drive() {
  {
    Rig r;
    REQUIRE(r.start() == CAR_MODE_36);                    // the reference: what every release must re-send
    for (int i = 0; i < 5; i++) REQUIRE(r.poll({}).empty() && r.car());
    REQUIRE(r.poll({false, true}) == TO_NO_OUTPUT);     // key-off: is_onroad still true on the first poll
    REQUIRE(r.poll({false, false}).empty() && !r.car());
  }
  {  // the upstream edge: the key turned off on the very poll ControlsReady appears - no car mode there either
    Rig r;
    r.poll({true, true, false, false});
    REQUIRE(r.poll({false, true, false, true}) == TO_NO_OUTPUT && !r.car());
    REQUIRE(r.poll({false, false}).empty());
  }
  {  // a poll whose health read failed sets nothing; the next one does
    Rig r;
    r.poll({true, true, false, false});
    board.health_ok = false;
    REQUIRE(r.poll({}).empty() && !r.car());
    board.health_ok = true;
    REQUIRE(r.poll({}) == CAR_MODE_36);
  }
}

void bus_off_then_key_off() {  // review A F1 / review B 1
  Rig r;
  r.start();
  REQUIRE(r.poll({}, 1) == TO_NO_OUTPUT && r.latched());     // the latch acts in the poll that sees it
  for (int i = 0; i < 3; i++) REQUIRE(r.poll({}, 1).empty() && r.mode() == (int)SafetyModel::NO_OUTPUT);
  REQUIRE(wait_param(r.params(), "CarBusOffStock", true));
  REQUIRE(r.poll({false, true}).empty());                   // KEY OFF, pandad still onroad: nothing (was 0xdf + 0xdc 36)
  REQUIRE(r.latched());
  REQUIRE(r.poll({false, false}).empty() && !r.latched());  // offroad too: key-off, cleared
  REQUIRE(wait_param(r.params(), "CarBusOffStock", false));
  REQUIRE(r.start() == CAR_MODE_36);                        // the next key cycle is a normal start
}

void bus_off_then_ignition_dropout() {  // review A F1, its scenario B
  Rig r;
  r.start();
  REQUIRE(r.poll({}, 1) == TO_NO_OUTPUT && r.latched());
  REQUIRE(wait_param(r.params(), "CarBusOffStock", true));
  REQUIRE(r.poll({false, true}).empty() && r.latched());    // one poll of ignition off
  REQUIRE(r.poll({true, false}).empty() && r.latched());    // hardwared dropped started; the ignition is back
  r.params().remove("CarBusOffStock");                      // the manager's ignition-on edge clears it
  REQUIRE(r.poll({true, false}).empty());
  REQUIRE(wait_param(r.params(), "CarBusOffStock", true));  // pandad puts it back while latched
  for (int i = 0; i < 10; i++) {                            // a new onroad session, card ready again
    r.poll({});
    REQUIRE(!r.car() && r.latched());
  }
  REQUIRE(r.mode() == (int)SafetyModel::ELM327);            // its start-up ELM327, relay closed
  r.key_off();
}

void seeded_after_a_pandad_restart() {
  Params().putBool("CarBusOffStock", true);
  Rig r(36, true);                                          // pandad restarted inside the key cycle
  for (int i = 0; i < 5; i++) {
    r.poll({});
    REQUIRE(!r.car() && r.latched());
  }
  REQUIRE(r.poll({false, true}) == TO_NO_OUTPUT && r.latched());  // from its start-up ELM327, as at every key-off
  REQUIRE(r.poll({false, false}).empty() && !r.latched());
  REQUIRE(wait_param(r.params(), "CarBusOffStock", false));
}

void diag_held_start_up_then_release(uint16_t param) {
  Rig r(param);
  const std::vector<Call> car_mode = {{"0xdf", ALT_EXP, PARAM_SP}, {"0xdc", (int)SafetyModel::HONDA_NIDEC, param}};
  r.poll({true, true, true, false});                        // card holds its first controls_update
  REQUIRE(r.mode() == (int)SafetyModel::ELM327);
  for (int i = 0; i < 5; i++) REQUIRE(r.poll({true, true, true, false}).empty());
  REQUIRE(r.poll({true, true, true, true}).empty());        // released: ControlsReady lands before the param clears
  REQUIRE(r.poll({}) == car_mode && r.car());               // the same bytes as a normal start
  REQUIRE(r.poll({}).empty());
}

void diag_mid_drive_then_release(uint16_t param) {
  Rig r(param);
  const std::vector<Call> car_mode = {{"0xdf", ALT_EXP, PARAM_SP}, {"0xdc", (int)SafetyModel::HONDA_NIDEC, param}};
  REQUIRE(r.start() == car_mode);
  REQUIRE(r.poll({true, true, true}) == TO_NO_OUTPUT);      // left in the poll that reads the param
  for (int i = 0; i < 5; i++) REQUIRE(r.poll({true, true, true}).empty() && !r.car());
  REQUIRE(r.poll({}) == car_mode && r.car());               // the release re-sends the start-up bytes
  for (int i = 0; i < 3; i++) REQUIRE(r.poll({}).empty() && r.car());
  REQUIRE(r.poll({true, true, true}) == TO_NO_OUTPUT);      // and again: every episode the same
  REQUIRE(r.poll({}) == car_mode);
}

void diag_then_key_off() {
  {
    Rig r;
    r.start();
    REQUIRE(r.poll({true, true, true}) == TO_NO_OUTPUT);
    REQUIRE(r.poll({false, true, true}).empty());           // key-off with the tool still talking
    REQUIRE(r.poll({false, false, false}).empty() && !r.car());
  }
  {  // card's release on the very key-off poll: still nothing
    Rig r;
    r.start();
    REQUIRE(r.poll({true, true, true}) == TO_NO_OUTPUT);
    REQUIRE(r.poll({false, true, false}).empty() && !r.car());
    REQUIRE(r.poll({false, false}).empty());
  }
}

void diag_and_bus_off_in_one_poll() {  // review A F3
  Rig r;
  r.start();
  REQUIRE(r.poll({true, true, true}, 1) == TO_NO_OUTPUT && !r.latched());  // fix 1 acts; fix 2 leaves it to fix 1
  REQUIRE(r.poll({true, true, true}, 1).empty() && !r.latched());
  REQUIRE(r.poll({}) == CAR_MODE_36);                       // the tool went quiet: back in the car mode
  REQUIRE(r.poll({}, 1) == TO_NO_OUTPUT && r.latched());    // and now a bus-off is fix 2's
  REQUIRE(r.poll({}).empty() && !r.car());
  r.key_off();
}

void stock_acc_mode_bus_off() {
  Rig r(68);
  r.start();
  REQUIRE(r.poll({}, 1) == TO_NO_OUTPUT && r.latched());
  for (int i = 0; i < 3; i++) REQUIRE(r.poll({}).empty() && !r.car());
  r.key_off();
}

const char *scenario = "";
#define SCENARIO(call) (scenario = #call, call)

void test_pandad_stock() {
  SCENARIO(normal_drive());
  SCENARIO(bus_off_then_key_off());
  SCENARIO(bus_off_then_ignition_dropout());
  SCENARIO(seeded_after_a_pandad_restart());
  SCENARIO(diag_held_start_up_then_release(36));
  SCENARIO(diag_held_start_up_then_release(68));
  SCENARIO(diag_mid_drive_then_release(36));
  SCENARIO(diag_mid_drive_then_release(68));
  SCENARIO(diag_then_key_off());
  SCENARIO(diag_and_bus_off_in_one_poll());
  SCENARIO(stock_acc_mode_bus_off());
}

}  // namespace

int main() {
  // a private params dir and msgq prefix, so the suite's parallel tests and a running openpilot are never touched
  char dir[] = "/tmp/test_pandad_stock_XXXXXX";
  if (mkdtemp(dir) == nullptr) return 2;
  const std::string prefix = "test_pandad_stock_" + std::to_string(getpid());
  setenv("PARAMS_ROOT", dir, 1);
  setenv("OPENPILOT_PREFIX", prefix.c_str(), 1);
  const std::string shm = "/dev/shm/msgq_" + prefix;
  mkdir(shm.c_str(), 0775);
  const int rc = run_native_test(test_pandad_stock);
  if (rc != 0) std::fprintf(stderr, "in %s; the last poll's writes: %s\n", scenario, dump(board.calls).c_str());
  const std::string cleanup = "rm -rf '" + std::string(dir) + "' '" + shm + "'";
  const int cleaned = std::system(cleanup.c_str());
  return rc != 0 ? rc : (cleaned != 0 ? 3 : 0);
}
