"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): the KTuner wiring in pandad (pandad.cc, panda_safety.cc, pandad.h) and its two params,
checked on the source the way test_eps_lkas_hook.py checks pandad.py - pandad itself needs a panda to run.

What must hold:
  * stock is reached by LEAVING the car safety mode (NO_OUTPUT, the relay closed), never by changing its param: only a
    car mode is forced down, the start-up ELM327 is left alone, and the stand-down's bit 32 / stock ACC's bit 64 are
    only ever read;
  * configureSafetyMode() still runs the start-up ELM327 and the OBD multiplexing handshake card waits on, holds the car
    mode back while stock, re-arms after leaving it, and on release runs the unchanged lines: setSafetyMode() and
    fetchCarParams() are nsw-live's byte for byte, so the release sends the same 0xdf / 0xdc as every start;
  * fix 2's sample is built from the car-side core, raw ignition and the ELESYS car mode, and its latch acts in the
    same poll, is seeded from CarBusOffStock and clears it at ignition off;
  * both params are registered with the flags the design needs, and the table has no duplicate.
"""
import hashlib
import re
from pathlib import Path

from opendbc.car.honda.values import HondaSafetyFlags
from openpilot.common.test import OpenpilotTestCase

ROOT = Path(__file__).parents[4]   # openpilot/
REPO = Path(__file__).parents[5]
PANDAD_CC = ROOT / "selfdrive/pandad/pandad.cc"
PANDA_SAFETY_CC = ROOT / "selfdrive/pandad/panda_safety.cc"
PANDAD_H = ROOT / "selfdrive/pandad/pandad.h"
GUARD_H = ROOT / "selfdrive/pandad/car_bus_guard.h"
PARAMS_KEYS = ROOT / "common/params_keys.h"
PANDA_MAIN = REPO / "panda/board/main.c"

# sha256 of the two functions' text at nsw-live 55d9562b6. They carry the car mode to the panda (0xdf alternative
# experience + safetyParam SP, then 0xdc model + param: 36, or 68 in stock ACC mode). A change here must be re-reviewed
# against the stand-down and stock ACC mode, then these pins updated.
SET_SAFETY_MODE_SHA256 = "da318ffece128c4e9e5a35673ee04424588bbe40868afce2cdca080289eb7eae"
FETCH_CAR_PARAMS_SHA256 = "40f371347f7280733a0b5d09634ee3faed3ee4272e898419942029a305ed19d3"


def function(src: str, signature: str) -> str:
  """The text of a top-level C++ function, from its signature to its closing brace at column 0."""
  start = src.index(signature)
  end = src.index("\n}\n", start) + 3
  return src[start:end]


def sha(text: str) -> str:
  return hashlib.sha256(text.encode()).hexdigest()


def in_sequence(text: str, *lines: str) -> bool:
  """These lines, each whole, one after the other with only whitespace between them."""
  return re.search(r"\s+".join(re.escape(line) for line in lines), text) is not None


class TestPandaSafety(OpenpilotTestCase):
  def src(self) -> str:
    return PANDA_SAFETY_CC.read_text(encoding="utf-8")

  def configure(self) -> str:
    return function(self.src(), "void PandaSafety::configureSafetyMode(bool is_onroad, bool stay_stock) {")

  def test_the_hold_comes_after_the_start_up_elm327_and_before_the_car_params(self):
    body = self.configure()
    first = body.index("if (is_onroad && !safety_configured_) {")
    mux = body.index("updateMultiplexingMode();", first)
    hold = body.index("if (stay_stock) {", mux)
    ret = body.index("return;", hold)
    fetch = body.index("auto car_params = fetchCarParams();")
    assert first < mux < hold < ret < fetch, body

  def test_the_original_block_is_there_verbatim(self):
    body = self.configure()
    assert in_sequence(body, "auto car_params = fetchCarParams();", "if (!car_params.empty()) {",
                       'LOGW("got %lu bytes CarParams", car_params[0].size());',
                       'LOGW("got %lu bytes CarParamsSP", car_params[1].size());',
                       "setSafetyMode(car_params);", "safety_configured_ = true;"), body

  def test_leaving_the_car_mode_re_arms(self):
    body = self.configure()
    rearm = body.index("} else if (is_onroad && stay_stock) {")
    assert body.index("safety_configured_ = false;", rearm) < body.index("} else if (!is_onroad) {")
    # the offroad reset is upstream's, plus our flag
    off = body[body.index("} else if (!is_onroad) {"):]
    for line in ("initialized_ = false;", "safety_configured_ = false;", "log_once_ = false;"):
      assert line in off, line

  def test_set_safety_mode_and_fetch_car_params_are_nsw_live(self):
    src = self.src()
    got = {
      "setSafetyMode": sha(function(src, "void PandaSafety::setSafetyMode(const std::vector<std::string> &params_string) {")),
      "fetchCarParams": sha(function(src, "std::vector<std::string> PandaSafety::fetchCarParams() {")),
    }
    want = {"setSafetyMode": SET_SAFETY_MODE_SHA256, "fetchCarParams": FETCH_CAR_PARAMS_SHA256}
    assert got == want, f"changed: {got} - re-review the release path against the stand-down and stock ACC mode"

  def test_the_header(self):
    h = PANDAD_H.read_text(encoding="utf-8")
    assert "void configureSafetyMode(bool is_onroad, bool stay_stock = false);" in h
    assert "bool getDiagToolStock();" in h
    assert 'return params_.getBool("DiagToolStock");' in self.src()


class TestPandad(OpenpilotTestCase):
  def src(self) -> str:
    return PANDAD_CC.read_text(encoding="utf-8")

  def test_only_a_car_mode_is_forced_down(self):
    src = self.src()
    assert "bool should_close_relay = !ignition_local || !is_onroad || (stay_stock && is_car_safety_mode(health.safety_mode_pkt));" \
      in src
    assert "const bool stay_stock = diag_stock || bus_guard->latched();" in src
    # forced down to NO_OUTPUT by upstream's own two lines
    assert in_sequence(src, "if (should_close_relay && (health.safety_mode_pkt != (uint8_t)(cereal::CarParams::SafetyModel::NO_OUTPUT))) {",
                       "panda->set_safety_model(cereal::CarParams::SafetyModel::NO_OUTPUT);")

  def test_is_car_safety_mode_is_the_pandas(self):
    """The same four non-car modes as the panda's own is_car_safety_mode() (board/main.c)."""
    fn = function(self.src(), "static bool is_car_safety_mode(uint8_t m) {")
    ours = set(re.findall(r"SafetyModel::(\w+)", fn))
    assert ours == {"SILENT", "NO_OUTPUT", "ELM327", "ALL_OUTPUT"}, ours
    panda = PANDA_MAIN.read_text(encoding="utf-8")
    pf = panda[panda.index("bool is_car_safety_mode(uint16_t mode) {"):]
    pf = pf[:pf.index("}")]
    assert set(re.findall(r"mode != SAFETY_(\w+)", pf)) == {"SILENT", "NOOUTPUT", "ALLOUTPUT", "ELM327"}, pf

  def test_the_elesys_bits_are_opendbcs(self):
    m = re.search(r"static constexpr uint16_t ELESYS_PARAM_BITS = (\d+)U \| (\d+)U;", self.src())
    assert m, "ELESYS_PARAM_BITS"
    assert int(m.group(1)) | int(m.group(2)) == HondaSafetyFlags.ELESYS_SCM_STANDDOWN | HondaSafetyFlags.ELESYS_STOCK_ACC
    assert {int(m.group(1)), int(m.group(2))} == {int(HondaSafetyFlags.ELESYS_SCM_STANDDOWN), int(HondaSafetyFlags.ELESYS_STOCK_ACC)}

  def test_the_bus_guard_sample(self):
    fn = function(self.src(), "static void poll_car_bus_guard(")
    assert "health.car_harness_status_pkt == (uint8_t)cereal::PandaState::HarnessStatus::FLIPPED) ? 2 : 0;" in fn
    assert "const can_health_t &c = can_health[core];" in fn
    assert "s.ignition = (health.flags_pkt & (HEALTH_FLAG_IGNITION_LINE | HEALTH_FLAG_IGNITION_CAN)) != 0U;" in fn
    assert "always_offroad" not in fn, "raw ignition: OffroadMode must not clear the latch"
    assert in_sequence(fn, "s.elesys_car_mode = health.safety_mode_pkt == (uint8_t)cereal::CarParams::SafetyModel::HONDA_NIDEC &&",
                       "(health.safety_param_pkt & ELESYS_PARAM_BITS) != 0U;")
    for line in ("s.bus_off = c.bus_off != 0U;", "s.bus_off_cnt = c.bus_off_cnt;", "s.tec = c.transmit_error_cnt;",
                 "s.onroad = is_onroad;"):
      assert line in fn, line
    assert 'params.putBoolNonBlocking("CarBusOffStock", true);' in fn, "no fsync in the 100 Hz loop"
    assert 'params.remove("CarBusOffStock");' in fn
    assert fn.index("if (guard->update(s)) {") < fn.index("putBoolNonBlocking") < fn.index("if (guard->cleared_now()) {") < \
      fn.index('params.remove("CarBusOffStock");')

  def test_the_guard_acts_in_the_poll_that_sees_it(self):
    """poll_car_bus_guard() runs before should_close_relay in the same send_panda_states(): a latch forces NO_OUTPUT in
    that very poll, whatever card and selfdrived are doing."""
    fn = function(self.src(), "std::optional<bool> send_panda_states(")
    assert fn.index("poll_car_bus_guard(bus_guard, health, can_health, is_onroad);") < fn.index("bool should_close_relay =")
    assert fn.index("if (spoofing_started) {") < fn.index("poll_car_bus_guard(")

  def test_the_10hz_block(self):
    src = self.src()
    block = src[src.index("// Process panda state at 10 Hz"):src.index("// Send out peripheralState at 2Hz")]
    order = ["always_offroad = panda_safety.getOffroadMode();", "diag_stock = panda_safety.getDiagToolStock();",
             "process_panda_state(panda, &pm, engaged, engaged_mads, is_onroad, spoofing_started, always_offroad, diag_stock, &bus_guard);",
             "panda_safety.configureSafetyMode(is_onroad, diag_stock || bus_guard.latched());"]
    pos = [block.index(s) for s in order]
    assert pos == sorted(pos), block
    assert 'CarBusGuard bus_guard(Params().getBool("CarBusOffStock"));' in src, "seeded across a pandad restart"
    assert '#include "selfdrive/pandad/car_bus_guard.h"' in src

  def test_the_guard_header_is_dependency_free(self):
    h = GUARD_H.read_text(encoding="utf-8")
    assert re.findall(r"#include <([\w/.]+)>", h) == ["cstdint"] and '#include "' not in h
    assert "static constexpr uint8_t TEC_LIMIT = 128;" in h


class TestParams(OpenpilotTestCase):
  def test_registered_with_these_flags(self):
    keys = PARAMS_KEYS.read_text(encoding="utf-8")
    assert '{"DiagToolStock", {CLEAR_ON_MANAGER_START | CLEAR_ON_ONROAD_TRANSITION | CLEAR_ON_OFFROAD_TRANSITION, BOOL}},' in keys
    assert '{"CarBusOffStock", {CLEAR_ON_MANAGER_START | CLEAR_ON_IGNITION_ON, BOOL}},' in keys
    for k in ("DiagToolStock", "CarBusOffStock"):
      line = next(ln for ln in keys.splitlines() if f'{{"{k}"' in ln)
      assert "BACKUP" not in line and "PERSISTENT" not in line, line

  def test_no_duplicate_keys(self):
    names = re.findall(r'\{"([A-Za-z0-9_]+)"', PARAMS_KEYS.read_text(encoding="utf-8"))
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert dupes == [], dupes

  def test_the_params_work(self):
    """Registered in the build (the Params binding is compiled from params_keys.h): a put and a remove round-trip."""
    import tempfile
    from openpilot.common.params import Params
    p = Params(tempfile.mkdtemp(prefix="ktuner_params_"))
    for k in ("DiagToolStock", "CarBusOffStock"):
      p.put_bool(k, True, block=True)
      assert p.get_bool(k)
      p.remove(k)
      assert not p.get_bool(k)
