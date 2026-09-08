"""The capnp -> dataclass seam that card crosses every frame.

card receives carControlSP as a capnp message and hands the CarController a
structs.CarControlSP dataclass via convert_carControlSP(). That function rebuilds
each nested struct BY HAND; a nested struct it does not know about arrives as a
plain dict, and the first attribute access on it kills card. That is exactly what
happened when lateralControl was added (routes 15646e8515eda1a7 b5-b8): card died
on CC_SP.lateralControl.integrator every frame. With card down, the panda keeps
blocking the stock SCM_BUTTONS (0x1A6) that only card re-sends to the Elesys radar,
the radar loses the car, and it latches ACC and CMBS faults.

The CarController integration test never sees this, because it builds the
dataclass directly. This test drives every nested field through the real seam,
and checks -- from the capnp schema itself -- that every nested struct in
CarControlSP is rebuilt, so the next one added cannot repeat this.

Runs under pytest, or standalone:
  PYTHONPATH=<repo>;<repo>/opendbc_repo python this_file.py
"""
import dataclasses
import sys

from cereal import custom
from opendbc.car import structs
from openpilot.selfdrive.car.helpers import convert_carControlSP, convert_to_capnp, is_dataclass


def _nested_dataclass_fields(cls) -> list[str]:
  out = []
  for f in dataclasses.fields(cls):
    if f.default_factory is not dataclasses.MISSING and is_dataclass(f.default_factory()):
      out.append(f.name)
  return out


def run_checks() -> list[tuple[str, bool, str]]:
  """Returns (name, passed, detail) for every check."""
  results = []

  def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))

  # --- CarControlSP: capnp -> dataclass, the direction card uses --------------
  msg = custom.CarControlSP.new_message()
  msg.lateralControl.integrator = 0.65
  msg.lateralControl.saturated = True
  msg.lateralControl.integratorFrozen = True
  msg.mads.enabled = True
  cc_sp = convert_carControlSP(msg.as_reader())

  for name in _nested_dataclass_fields(structs.CarControlSP):
    value = getattr(cc_sp, name)
    check(f"CarControlSP.{name} arrives as a dataclass, not a dict", is_dataclass(value), f"got {type(value).__name__}")

  # getattr with a default, so a dict arriving here (the original bug) is reported as a
  # failed check rather than an exception that stops the remaining checks from running
  integrator = getattr(cc_sp.lateralControl, "integrator", None)
  check("lateralControl.integrator survives the seam", integrator is not None and abs(integrator - 0.65) < 1e-6,
        f"{integrator!r} ({type(cc_sp.lateralControl).__name__})")
  check("lateralControl flags survive the seam",
        getattr(cc_sp.lateralControl, "saturated", False) and getattr(cc_sp.lateralControl, "integratorFrozen", False))

  # the exact expression that crashed card, on a default message too
  cc_sp_default = convert_carControlSP(custom.CarControlSP.new_message().as_reader())
  try:
    _ = cc_sp_default.lateralControl.integrator
    check("CC_SP.lateralControl.integrator on a default message does not raise", True)
  except AttributeError as e:
    check("CC_SP.lateralControl.integrator on a default message does not raise", False, str(e))

  # --- CarStateSP: dataclass -> capnp, the direction card publishes -----------
  cs_sp = structs.CarStateSP()
  cs_sp.linbusGateway.present = True
  cs_sp.linbusGateway.actuating = True
  cs_sp.linbusGateway.valid = True
  cap = convert_to_capnp(cs_sp)
  check("CarStateSP nested linbusGateway converts to capnp", bool(cap.linbusGateway.present) and bool(cap.linbusGateway.actuating))
  r = cap.as_reader()
  check("...and round-trips", r.linbusGateway.present and r.linbusGateway.actuating and r.linbusGateway.valid and not r.linbusGateway.dryRun)

  # --- every nested struct in the capnp schema must be rebuilt by the converter
  schema_nested = [n for n in custom.CarControlSP.schema.fieldnames
                   if custom.CarControlSP.schema.fields[n].proto.slot.type.which() == "struct"]
  for name in schema_nested:
    value = getattr(cc_sp, name, None)
    check(f"schema nested field '{name}' is rebuilt by convert_carControlSP", is_dataclass(value),
          f"got {type(value).__name__} -- add it to convert_carControlSP")

  return results


def test_car_control_sp_seam():
  failed = [(n, d) for n, ok, d in run_checks() if not ok]
  assert not failed, failed


if __name__ == "__main__":
  results = run_checks()
  for name, ok, detail in results:
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  {detail}"))
  failed = [n for n, ok, _ in results if not ok]
  print("\n" + "=" * 60)
  if failed:
    print(f"{len(failed)} FAILED: {failed}")
    sys.exit(1)
  print("ALL CHECKS PASSED")
