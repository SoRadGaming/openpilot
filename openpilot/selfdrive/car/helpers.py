import capnp
from typing import Any

from openpilot.cereal import custom
from opendbc.car import structs

_FIELDS = '__dataclass_fields__'  # copy of dataclasses._FIELDS


def is_dataclass(obj):
  """Similar to dataclasses.is_dataclass without instance type check checking"""
  return hasattr(obj, _FIELDS)


def _asdictref_inner(obj) -> dict[str, Any] | Any:
  if is_dataclass(obj):
    ret = {}
    for field in getattr(obj, _FIELDS):  # similar to dataclasses.fields()
      ret[field] = _asdictref_inner(getattr(obj, field))
    return ret
  elif isinstance(obj, (tuple, list)):
    return type(obj)(_asdictref_inner(v) for v in obj)
  else:
    return obj


def asdictref(obj) -> dict[str, Any]:
  """
  Similar to dataclasses.asdict without recursive type checking and copy.deepcopy
  Note that the resulting dict will contain references to the original struct as a result
  """
  if not is_dataclass(obj):
    raise TypeError("asdictref() should be called on dataclass instances")

  return _asdictref_inner(obj)


def convert_to_capnp(struct: structs.CarParamsSP | structs.CarStateSP) -> capnp.lib.capnp._DynamicStructBuilder:
  struct_dict = asdictref(struct)

  if isinstance(struct, structs.CarParamsSP):
    struct_capnp = custom.CarParamsSP.new_message(**struct_dict)
  elif isinstance(struct, structs.CarStateSP):
    struct_capnp = custom.CarStateSP.new_message(**struct_dict)
  else:
    raise ValueError(f"Unsupported struct type: {type(struct)}")

  return struct_capnp


# FORK(CPU): convert_carControlSP() runs in card every frame (100 Hz), and struct.to_dict() was most of its cost: timed on
# a PC on route 157's messages, gc off as in card, 54-58 us a call before and 20-22 us on the fast path. At card's
# device / PC ratio (about 4.8) the function was about 2.6-2.8 % of the device core card runs on; the fast path saves
# about 1.6-1.8 points of it (the 43-of-55 us and "about 4 %" first given came from a cProfile run). The fast path reads
# each field as to_dict() does - _get(), an enum by _as_str(), a nested struct only when it is set: to_dict() leaves an
# unset one out and the dataclass keeps its own defaults, which are not always capnp's (LeadData.radarTrackId is -1 in
# the schema, 0 in the dataclass) - and builds the same dataclasses from the same values. Where it could differ it is
# not used: a message with params (a list the general path hands on as dicts), or a schema with anything the plan does
# not know (a new pointer field, a group or union, a nested field that is not a bool, a number or an enum); the
# general path, upstream's, runs instead. test_convert_carcontrolsp_fast.py holds the two equal, field for field and
# type for type, on every carControlSP of route 157 and on the edge cases. docs/fork/COMM-ISSUE.md section 9
_CC_SP_NESTED = {
  'mads': structs.ModularAssistiveDrivingSystem,
  'leadOne': structs.LeadData,
  'leadTwo': structs.LeadData,
  'intelligentCruiseButtonManagement': structs.IntelligentCruiseButtonManagement,
  'lateralControl': structs.CarControlSP.LateralControl,
}
_CC_SP_GENERAL_IF_SET = ('params',)
_PLAIN_KINDS = frozenset(('bool', 'int8', 'int16', 'int32', 'int64', 'uint8', 'uint16', 'uint32', 'uint64', 'float32',
                          'float64', 'enum'))


def _plain_fields(schema) -> tuple[tuple[str, bool], ...] | None:
  """(name, is_enum) of every field to_dict() gives a struct of this schema, less the DEPRECATED ones that the general
  path drops; None if any of them is not a bool, a number or an enum, or the struct has a union."""
  if schema.union_fields:
    return None
  out = []
  for name in schema.non_union_fields:
    proto = schema.fields[name].proto
    if proto.which() != 'slot' or proto.slot.type.which() not in _PLAIN_KINDS:
      return None
    if not name.endswith('DEPRECATED'):
      out.append((name, proto.slot.type.which() == 'enum'))
  return tuple(out)


def _carControlSP_plan():
  """The fast path's plan from the schema, or None when the schema has something it does not handle exactly."""
  top = custom.CarControlSP.schema
  if top.union_fields:
    return None
  plain, sample = [], custom.CarControlSP.new_message()
  for name in top.non_union_fields:
    proto = top.fields[name].proto
    if proto.which() != 'slot':
      return None
    kind = proto.slot.type.which()
    if kind in _PLAIN_KINDS:
      if not name.endswith('DEPRECATED'):
        plain.append((name, kind == 'enum'))
    elif not ((name in _CC_SP_NESTED and kind == 'struct') or name in _CC_SP_GENERAL_IF_SET):
      return None
  nested = []
  for name, cls in _CC_SP_NESTED.items():
    if name not in top.fields:
      return None
    fields = _plain_fields(getattr(sample, name).schema)
    if fields is None:
      return None
    nested.append((name, cls, fields))
  return tuple(plain), tuple(nested)


_CC_SP_PLAN = _carControlSP_plan()


def convert_carControlSP(struct: capnp.lib.capnp._DynamicStructReader) -> structs.CarControlSP:
  """FORK(CPU): the general path's dataclass, without to_dict() (see _CC_SP_NESTED)."""
  plan = _CC_SP_PLAN
  if plan is None or any(struct._has(name) for name in _CC_SP_GENERAL_IF_SET):
    return convert_carControlSP_general(struct)
  plain, nested = plan
  out = structs.CarControlSP(**{n: (struct._get(n)._as_str() if e else struct._get(n)) for n, e in plain})
  for name, cls, fields in nested:
    if struct._has(name):
      sub = struct._get(name)
      setattr(out, name, cls(**{n: (sub._get(n)._as_str() if e else sub._get(n)) for n, e in fields}))
    else:
      setattr(out, name, cls())
  return out


def convert_carControlSP_general(struct: capnp.lib.capnp._DynamicStructReader) -> structs.CarControlSP:
  # FORK(CPU): upstream's convert_carControlSP (with the fork's lateralControl), unchanged but for its name
  # TODO: recursively handle any car struct as needed
  def remove_deprecated(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.endswith('DEPRECATED')}

  struct_dict = struct.to_dict()
  struct_dataclass = structs.CarControlSP(**remove_deprecated({k: v for k, v in struct_dict.items() if not isinstance(k, dict)}))

  struct_dataclass.mads = structs.ModularAssistiveDrivingSystem(**remove_deprecated(struct_dict.get('mads', {})))
  # struct_dataclass.params = [structs.CarControlSP.Param(**remove_deprecated(p)) for p in struct_dict.get('params', [])]
  struct_dataclass.leadOne = structs.LeadData(**remove_deprecated(struct_dict.get('leadOne', {})))
  struct_dataclass.leadTwo = structs.LeadData(**remove_deprecated(struct_dict.get('leadTwo', {})))
  struct_dataclass.intelligentCruiseButtonManagement = structs.IntelligentCruiseButtonManagement(
    **remove_deprecated(struct_dict.get('intelligentCruiseButtonManagement', {}))
  )
  # Every nested struct has to be rebuilt here by hand, or it reaches the CarController as
  # a plain dict. lateralControl was missed when it was added and card crashed on
  # CC_SP.lateralControl.integrator every frame (routes b5-b8), taking the whole car
  # with it -- with card down nothing services the radar side of the split bus, and the
  # car throws ACC/CMBS faults. test_car_control_sp_seam.py covers this seam now.
  struct_dataclass.lateralControl = structs.CarControlSP.LateralControl(
    **remove_deprecated(struct_dict.get('lateralControl', {}))
  )

  return struct_dataclass
