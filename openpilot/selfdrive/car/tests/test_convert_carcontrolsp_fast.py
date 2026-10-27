"""FORK(CPU): convert_carControlSP()'s fast path gives what the general path (upstream's, by to_dict()) gives.

card calls it every frame; push16 skips to_dict() (helpers.py, _CC_SP_NESTED). Equal here means the same dataclasses
with the same field values of the same types, floats bit for bit: on every carControlSP of route 157 when its rlogs
are on this machine, and always on the edge cases and a seeded fuzz: nested structs unset (where the dataclass's
defaults are not capnp's: LeadData.radarTrackId), every enum value, NaN, infinities and -0.0, a DEPRECATED field with
a value, params set (the general path then runs), and an enum value the schema does not know (both raise).
"""
import dataclasses
import glob
import math
import os
import random
import struct
import unittest

from openpilot.cereal import custom
from opendbc.car import structs
from openpilot.selfdrive.car import helpers
from openpilot.selfdrive.car.helpers import convert_carControlSP, convert_carControlSP_general

ROUTE_GLOB = "/mnt/s/OP/sunny_logs/*_00000157--*/raw"
ROUTE_SEGMENTS = (1, 3, 7)


def differences(a, b, path="CC_SP") -> list[str]:
  if type(a) is not type(b):
    return [f"{path}: {type(a).__name__} {a!r} != {type(b).__name__} {b!r}"]
  if dataclasses.is_dataclass(a):
    out = []
    for f in dataclasses.fields(a):
      out += differences(getattr(a, f.name), getattr(b, f.name), f"{path}.{f.name}")
    return out
  if isinstance(a, float):
    return [] if struct.pack("<d", a) == struct.pack("<d", b) else [f"{path}: {a!r} != {b!r}"]
  if isinstance(a, (list, tuple)):
    if len(a) != len(b):
      return [f"{path}: len {len(a)} != {len(b)}"]
    return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in differences(x, y, f"{path}[{i}]")]
  if isinstance(a, dict):
    if a.keys() != b.keys():
      return [f"{path}: keys {sorted(a)} != {sorted(b)}"]
    return [d for k in a for d in differences(a[k], b[k], f"{path}.{k}")]
  return [] if a == b else [f"{path}: {a!r} != {b!r}"]


NESTED = ("mads", "leadOne", "leadTwo", "intelligentCruiseButtonManagement", "lateralControl")
SPECIAL = (0., -0., 1.5, -2.25, math.inf, -math.inf, math.nan, 1e-45, 3.4e38)


def fill(msg, rng: random.Random, set_mask: int) -> None:
  """Every plain field of the nested structs picked by set_mask, and the top-level float."""
  msg.speedLimitNowFlash = rng.choice(SPECIAL + (rng.uniform(-50, 50),))
  for i, name in enumerate(NESTED):
    if not set_mask & (1 << i):
      continue
    sub = getattr(msg, name)  # a builder's getter sets the pointer
    for fname in sub.schema.non_union_fields:
      kind = sub.schema.fields[fname].proto.slot.type.which()
      if kind == "bool":
        setattr(sub, fname, rng.random() < 0.5)
      elif kind.startswith("float"):
        setattr(sub, fname, rng.choice(SPECIAL + (rng.uniform(-200, 200),)))
      elif kind.startswith(("int", "uint")):
        setattr(sub, fname, rng.choice((0, 1, 302, -1 if kind.startswith("int") else 7)))
      elif kind == "enum":
        names = list(sub.schema.fields[fname].schema.enumerants)
        setattr(sub, fname, rng.choice(names))


class TestConvertCarControlSPFast(unittest.TestCase):
  def assert_same(self, msg, what=""):
    fast, general = convert_carControlSP(msg), convert_carControlSP_general(msg)
    diff = differences(fast, general)
    assert not diff, f"{what}: {diff[:5]}"

  def test_the_plan_covers_the_schema(self):
    """The fast path is in use (a schema change it does not know turns it off; that is the general path, not a bug)."""
    assert helpers._CC_SP_PLAN is not None
    plain, nested = helpers._CC_SP_PLAN
    assert [n for n, _, _ in nested] == list(NESTED)
    assert ("speedLimitNowFlash", False) in plain

  def test_default_and_unset_messages(self):
    for mask in range(1 << len(NESTED)):
      msg = custom.CarControlSP.new_message()
      for i, name in enumerate(NESTED):
        if mask & (1 << i):
          getattr(msg, name)  # set, all defaults
      self.assert_same(msg.as_reader(), f"mask {mask:05b}")

  def test_unset_lead_keeps_the_dataclass_default(self):
    """capnp's default radarTrackId is -1; the general path never sees it for an unset lead, nor may the fast one."""
    r = custom.CarControlSP.new_message().as_reader()
    assert r.leadOne.radarTrackId == -1
    assert convert_carControlSP(r).leadOne.radarTrackId == 0 == convert_carControlSP_general(r).leadOne.radarTrackId

  def test_every_enum_value(self):
    for name in ("mads", "intelligentCruiseButtonManagement"):
      sub = getattr(custom.CarControlSP.new_message(), name)
      for fname in sub.schema.non_union_fields:
        if sub.schema.fields[fname].proto.slot.type.which() != "enum":
          continue
        for value in sub.schema.fields[fname].schema.enumerants:
          msg = custom.CarControlSP.new_message()
          setattr(getattr(msg, name), fname, value)
          self.assert_same(msg.as_reader(), f"{name}.{fname}={value}")
          got = getattr(getattr(convert_carControlSP(msg.as_reader()), name), fname)
          assert type(got) is str and got == value

  def test_a_deprecated_field_with_a_value_is_dropped(self):
    msg = custom.CarControlSP.new_message()
    msg.leadOne.aLeadDEPRECATED = 3.5
    self.assert_same(msg.as_reader())
    assert convert_carControlSP(msg.as_reader()).leadOne.aLeadDEPRECATED == 0.

  def test_params_take_the_general_path(self):
    msg = custom.CarControlSP.new_message()
    p = msg.init("params", 2)
    p[0].key, p[0].type, p[0].value = "a", "bool", b"1"
    p[1].key = "b"
    self.assert_same(msg.as_reader())
    assert isinstance(convert_carControlSP(msg.as_reader()).params[0], dict)  # the general path's, as it always was

  def test_an_unknown_enum_value_raises_in_both(self):
    msg = custom.CarControlSP.new_message()
    msg.mads.state = 200  # no such enumerant
    errors = []
    for fn in (convert_carControlSP, convert_carControlSP_general):
      try:
        fn(msg.as_reader())
        errors.append(None)
      except Exception as e:
        errors.append(type(e))
    assert errors[0] is not None and errors[0] is errors[1], errors

  def test_fuzz(self):
    rng = random.Random(1028)
    for i in range(4000):
      msg = custom.CarControlSP.new_message()
      fill(msg, rng, rng.randrange(1 << len(NESTED)))
      self.assert_same(msg.as_reader(), f"fuzz {i}")

  def test_builders_too(self):
    msg = custom.CarControlSP.new_message()
    fill(msg, random.Random(5), (1 << len(NESTED)) - 1)
    self.assert_same(msg, "builder")

  def test_route_157(self):
    raw = glob.glob(ROUTE_GLOB)
    if not raw:
      self.skipTest("route 157's rlogs are not on this machine")
    from openpilot.tools.lib.logreader import LogReader
    n = 0
    for seg in ROUTE_SEGMENTS:
      path = os.path.join(raw[0], f"{seg:04d}--rlog.zst")
      if not os.path.exists(path):
        continue
      for m in LogReader(path):
        if m.which() == "carControlSP":
          self.assert_same(m.carControlSP, f"segment {seg} t={m.logMonoTime}")
          n += 1
    assert n > 10000, n

  def test_the_result_is_a_fresh_object_each_call(self):
    """The car layer may keep or change what it gets; the fast path must not hand out shared objects."""
    r = custom.CarControlSP.new_message().as_reader()
    a, b = convert_carControlSP(r), convert_carControlSP(r)
    assert a is not b and a.mads is not b.mads and a.leadOne is not b.leadOne and a.params is not b.params

  def test_dataclass_types(self):
    assert isinstance(convert_carControlSP(custom.CarControlSP.new_message().as_reader()), structs.CarControlSP)


if __name__ == "__main__":
  unittest.main()
