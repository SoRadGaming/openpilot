"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.sunnylink.athena import sunnylinkd
from openpilot.common.test import OpenpilotTestCase


class TestSunnylinkdMethods(OpenpilotTestCase):
  def setup_method(self):
    self.saved_params = []

    self.original_save = sunnylinkd.save_param_from_base64_encoded_string

    def mock_save_param(key, value, compression=False):
      self.saved_params.append((key, value, compression))

    sunnylinkd.save_param_from_base64_encoded_string = mock_save_param  # ty: ignore[invalid-assignment]

  def teardown_method(self):
    sunnylinkd.save_param_from_base64_encoded_string = self.original_save  # ty: ignore[invalid-assignment]

  def test_saveParams_blocked(self):
    blocked_params = {
      "GithubUsername": "attacker",
      "GithubSshKeys": "ssh-rsa attacker_key",
    }

    sunnylinkd.saveParams(blocked_params)

    assert len(self.saved_params) == 0

  def test_saveParams_allowed(self):
    allowed_params = {
      "SpeedLimitOffset": "5",
      "MyCustomParam": "123"
    }

    sunnylinkd.saveParams(allowed_params)

    # verify content
    assert len(self.saved_params) == 2
    keys_saved = [p[0] for p in self.saved_params]
    assert "SpeedLimitOffset" in keys_saved
    assert "MyCustomParam" in keys_saved

  def test_saveParams_mixed(self):
    mixed_params = {
      "GithubUsername": "attacker",
      "SpeedLimitOffset": "10"
    }

    sunnylinkd.saveParams(mixed_params)

    # should save allowed one
    assert len(self.saved_params) == 1
    assert self.saved_params[0][0] == "SpeedLimitOffset"
    assert self.saved_params[0][1] == "10"

  def test_saveParams_offroad_only(self):
    # FORK(HONDA_ACCORD_9G_AU): stock ACC mode never changes under a moving car, whatever the app allows
    sunnylinkd.params.remove("IsOffroad")       # not yet known at boot: treated as onroad
    sunnylinkd.saveParams({"HondaElesysStockAcc": "MQ=="})
    sunnylinkd.params.put_bool("IsOffroad", False, block=True)
    sunnylinkd.saveParams({"HondaElesysStockAcc": "MQ==", "SpeedLimitOffset": "10"})
    assert [p[0] for p in self.saved_params] == ["SpeedLimitOffset"]

    self.saved_params.clear()
    sunnylinkd.params.put_bool("IsOffroad", True, block=True)
    sunnylinkd.saveParams({"HondaElesysStockAcc": "MQ=="})
    assert [p[0] for p in self.saved_params] == ["HondaElesysStockAcc"]

  def test_saveParams_pump_rule_and_brake_law_offroad_only(self):
    # FORK(HONDA_ACCORD_9G_AU) batch 3 fix round 1: read at ignition like stock ACC, so never written onroad
    keys = ("HondaElesysPumpC1b", "HondaElesysBrakeLawV2")
    sunnylinkd.params.put_bool("IsOffroad", False, block=True)
    sunnylinkd.saveParams(dict.fromkeys(keys, "MQ=="))
    assert self.saved_params == []
    sunnylinkd.params.put_bool("IsOffroad", True, block=True)
    sunnylinkd.saveParams(dict.fromkeys(keys, "MQ=="))
    assert sorted(p[0] for p in self.saved_params) == sorted(keys)
