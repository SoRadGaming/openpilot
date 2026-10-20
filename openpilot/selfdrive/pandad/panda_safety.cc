#include "selfdrive/pandad/pandad.h"
#include "openpilot/cereal/messaging/messaging.h"
#include "common/swaglog.h"

void PandaSafety::configureSafetyMode(bool is_onroad, bool stay_stock, bool ignition) {
  if (is_onroad && !safety_configured_) {
    updateMultiplexingMode();
    // FORK(HONDA_ACCORD_9G_AU): hold the car mode back while the car is to stay stock (KTuner, pandad.cc). Before the first
    // car mode that is the start-up ELM327 just above; after one, send_panda_states() has forced NO_OUTPUT and the branch
    // below re-armed this one. When it ends, the unchanged lines below set the same CarParams' mode, as at every start.
    if (stay_stock) {
      stock_held_ = true;
      return;
    }
    // FORK(HONDA_ACCORD_9G_AU): and never on a poll that read the ignition off: at key-off is_onroad is still true for
    // one poll, and a re-armed (or not yet configured) pandad would set the car mode there. No re-arm, nothing else.
    if (!ignition) {
      return;
    }

    auto car_params = fetchCarParams();
    if (!car_params.empty()) {
      LOGW("got %lu bytes CarParams", car_params[0].size());
      LOGW("got %lu bytes CarParamsSP", car_params[1].size());
      setSafetyMode(car_params);
      safety_configured_ = true;
      if (stock_held_) {  // FORK(HONDA_ACCORD_9G_AU): the end of a held start-up or of a mid-drive stock spell
        LOGE("FORK leaving stock: car safety mode set");
        stock_held_ = false;
      }
    }
  } else if (is_onroad && stay_stock) {
    // FORK(HONDA_ACCORD_9G_AU): send_panda_states() forced NO_OUTPUT this pass. Re-arm, so the release re-sends 0xdf
    // (accepted only outside a car mode) and 0xdc from the same CarParams: 36, or 68 in stock ACC mode.
    safety_configured_ = false;
    stock_held_ = true;
  } else if (!is_onroad) {
    initialized_ = false;
    safety_configured_ = false;
    log_once_ = false;
    stock_held_ = false;  // FORK(HONDA_ACCORD_9G_AU)
  }
}

void PandaSafety::updateMultiplexingMode() {
  // Initialize to ELM327 without OBD multiplexing for initial fingerprinting
  if (!initialized_) {
    prev_obd_multiplexing_ = false;
    panda_->set_safety_model(cereal::CarParams::SafetyModel::ELM327, 1U);
    initialized_ = true;
  }

  // Switch between multiplexing modes based on the OBD multiplexing request
  bool obd_multiplexing_requested = params_.getBool("ObdMultiplexingEnabled");
  if (obd_multiplexing_requested != prev_obd_multiplexing_) {
    const uint16_t safety_param = obd_multiplexing_requested ? 0U : 1U;
    panda_->set_safety_model(cereal::CarParams::SafetyModel::ELM327, safety_param);
    prev_obd_multiplexing_ = obd_multiplexing_requested;
    params_.putBool("ObdMultiplexingChanged", true);
  }
}

// TODO-SP: Use structs instead of vector
std::vector<std::string> PandaSafety::fetchCarParams() {
  if (!params_.getBool("FirmwareQueryDone")) {
    return {};
  }

  if (!log_once_) {
    LOGW("Finished FW query, Waiting for params to set safety model");
    log_once_ = true;
  }

  if (!params_.getBool("ControlsReady")) {
    return {};
  }
  return {params_.get("CarParams"), params_.get("CarParamsSP")};
}

// TODO-SP: Use structs instead of vector
void PandaSafety::setSafetyMode(const std::vector<std::string> &params_string) {
  AlignedBuffer aligned_buf;
  AlignedBuffer aligned_buf_sp;

  capnp::FlatArrayMessageReader cmsg(aligned_buf.align(params_string[0].data(), params_string[0].size()));
  cereal::CarParams::Reader car_params = cmsg.getRoot<cereal::CarParams>();

  capnp::FlatArrayMessageReader cmsg_sp(aligned_buf_sp.align(params_string[1].data(), params_string[1].size()));
  cereal::CarParamsSP::Reader car_params_sp = cmsg_sp.getRoot<cereal::CarParamsSP>();

  auto safety_configs = car_params.getSafetyConfigs();
  uint16_t alternative_experience = car_params.getAlternativeExperience();
  uint16_t safety_param_sp = car_params_sp.getSafetyParam();

  cereal::CarParams::SafetyModel safety_model = safety_configs[0].getSafetyModel();
  uint16_t safety_param = safety_configs[0].getSafetyParam();

  LOGW("setting safety model: %d, param: %d, alternative experience: %d, param_sp: %d", (int)safety_model, safety_param, alternative_experience, safety_param_sp);
  panda_->set_alternative_experience(alternative_experience, safety_param_sp);
  panda_->set_safety_model(safety_model, safety_param);
}

bool PandaSafety::getOffroadMode() {
  auto offroad_mode = params_.getBool("OffroadMode");
  return offroad_mode;
}

// FORK(HONDA_ACCORD_9G_AU): KTuner fix 1 - card's diag_tool_guard.py holds the car stock while a diagnostic tool talks
bool PandaSafety::getDiagToolStock() {
  return params_.getBool("DiagToolStock");
}
