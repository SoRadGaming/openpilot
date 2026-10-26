import copy
import random

from openpilot.common.test import OpenpilotTestCase
from openpilot.selfdrive.selfdrived.events import Alert, EVENTS
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager

from openpilot.sunnypilot.selfdrive.selfdrived.events_base import EmptyAlert


class TestAlertManager(OpenpilotTestCase):

  def test_duration(self):
    """
      Enforce that an alert lasts for max(alert duration, duration the alert is added)
    """
    for duration in range(1, 100):
      alert = None
      while not isinstance(alert, Alert):
        event = random.choice([e for e in EVENTS.values() if len(e)])
        alert = random.choice(list(event.values()))

      # FORK(UPSTREAM-FIX): a copy. EVENTS' alerts are shared module objects - create_alerts() hands the very object to
      # every AlertManager - so setting the picked one's duration shortened that alert for every later test in this
      # process. In 1 run in 30 it left controlsMismatch's "TAKE CONTROL IMMEDIATELY" at 1-9 frames, and a CI worker
      # that ran this before test_car_stock_alert failed its bus-off disengagement (push15)
      alert = copy.copy(alert)
      alert.duration = duration

      # check two cases:
      # - alert is added to AM for <= the alert's duration
      # - alert is added to AM for > alert's duration
      for greater in (True, False):
        if greater:
          add_duration = duration + random.randint(1, 10)
        else:
          add_duration = random.randint(1, duration)
        show_duration = max(duration, add_duration)

        AM = AlertManager()
        for frame in range(duration+10):
          if frame < add_duration:
            AM.add_many(frame, [alert, ])
          AM.process_alerts(frame, set())

          shown = AM.current_alert != EmptyAlert
          should_show = frame <= show_duration
          assert shown == should_show, f"{frame=} {add_duration=} {duration=}"

      # check one case:
      # - if alert is re-added to AM before it ends the duration is extended
      if duration > 1:
        AM = AlertManager()
        show_duration = duration * 2
        for frame in range(duration * 2 + 10):
          if frame == 0:
            AM.add_many(frame, [alert, ])

          if frame == duration:
            # add alert one frame before it ends
            assert AM.current_alert == alert
            AM.add_many(frame, [alert, ])
          AM.process_alerts(frame, set())

          shown = AM.current_alert != EmptyAlert
          should_show = frame <= show_duration
          assert shown == should_show, f"{frame=} {duration=}"

  def test_duration_leaves_events_alone(self):
    """FORK(UPSTREAM-FIX): test_duration works on copies. Every alert in EVENTS - shared by every later test in this
    process, and by selfdrived's AlertManager - keeps every attribute."""
    def snapshot():
      return {(e, et): (id(a), dict(vars(a))) for e, ets in EVENTS.items() for et, a in ets.items() if isinstance(a, Alert)}
    before = snapshot()
    for _ in range(3):
      self.test_duration()
    assert snapshot() == before
