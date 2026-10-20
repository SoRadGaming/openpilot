#pragma once

#include <string>

#include "common/params.h"
#include "selfdrive/pandad/panda.h"

void pandad_main_thread(std::string serial);

// deprecated devices
static const std::vector<cereal::PandaState::PandaType> SUPPORTED_PANDA_TYPES = {
  cereal::PandaState::PandaType::RED_PANDA,
  cereal::PandaState::PandaType::TRES,
  cereal::PandaState::PandaType::CUATRO,
};


class PandaSafety {
public:
  PandaSafety(Panda *panda) : panda_(panda) {}
  void configureSafetyMode(bool is_onroad, bool stay_stock = false, bool ignition = true);  // FORK(HONDA_ACCORD_9G_AU): stay_stock, ignition
  bool getOffroadMode();
  bool getDiagToolStock();  // FORK(HONDA_ACCORD_9G_AU)

private:
  void updateMultiplexingMode();
  std::vector<std::string> fetchCarParams();
  void setSafetyMode(const std::vector<std::string> &params_string);

  bool initialized_ = false;
  bool log_once_ = false;
  bool safety_configured_ = false;
  bool prev_obd_multiplexing_ = false;
  bool stock_held_ = false;  // FORK(HONDA_ACCORD_9G_AU): the car mode was held back or left for stock this drive
  Panda *panda_;
  Params params_;
};

// FORK(HONDA_ACCORD_9G_AU): pandad_run()'s 10 Hz panda-state pass (pandad.cc), driven by tests/test_pandad_stock.cc
class PubMaster;
class CarBusGuard;
void process_panda_safety(Panda *panda, PubMaster *pm, PandaSafety *panda_safety, CarBusGuard *bus_guard, bool engaged,
                          bool engaged_mads, bool is_onroad, bool spoofing_started, bool always_offroad);
