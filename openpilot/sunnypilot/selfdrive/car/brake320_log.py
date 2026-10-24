"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(HONDA_ACCORD_9G_AU): brake320's customReserved14, from card (opendbc/sunnypilot/car/honda/brake_ceiling.py).

card calls update() once per 100 Hz step, after carState has gone out. It sends customReserved14 every PUBLISH_EVERY
steps (1 Hz) and on the step the ceiling or the VSA fallback changes, so the first drives show the counts above 255 in
the qlog (decimation 1) and a fallback is on the route the step it latches. Only where brake320_status() says the car
carries brake320 (HONDA_ACCORD_9G_AU with openpilot longitudinal): every other car, and this one in stock ACC mode,
gets no Brake320Log, no socket and no message. No Params I/O. Never raises: diagnostics must not take card down.
"""
from openpilot.cereal import messaging
from opendbc.sunnypilot.car.honda.brake_ceiling import brake320_status

PUBLISH_EVERY = 100   # card steps (100 Hz): 1 Hz


class Brake320Log:
  def __init__(self, CI, pm):
    self.CI = CI
    self.pm = pm
    self.steps = 0
    self.last_key = None

  @staticmethod
  def create(CI, pm=None) -> "Brake320Log | None":
    """A Brake320Log for this CarInterface, or None where there is no brake320 to report."""
    try:
      if brake320_status(CI) is None:
        return None
      return Brake320Log(CI, pm if pm is not None else messaging.PubMaster(['customReserved14']))
    except Exception:
      return None

  def update(self) -> None:
    try:
      st = brake320_status(self.CI)
      if st is None:
        return
      key = (st["ceilingCounts"], st["vsaFallback"], st["vsaFallbackReason"])
      due = self.steps % PUBLISH_EVERY == 0 or key != self.last_key
      self.steps += 1
      if not due:
        return
      self.last_key = key
      msg = messaging.new_message('customReserved14', valid=True)
      out = msg.customReserved14
      for k, v in st.items():
        setattr(out, k, v)
      self.pm.send('customReserved14', msg)
    except Exception:
      pass
