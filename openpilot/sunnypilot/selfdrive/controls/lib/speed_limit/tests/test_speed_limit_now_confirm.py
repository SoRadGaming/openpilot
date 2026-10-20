"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(SPEED-LIMIT): push9 - a '+'/'-' press while the comma 4's card says 'now' is the confirm for that coming limit
(speed_limit/now_confirm.py). Route 137 at 305.16 and route 134 at 313.44, 446.37, 716.18 and 717.54: the driver pressed
during 'now', got a 1 km/h step, and had to press again ~4 s later when the prompt opened.

sla-flash (owner, 2026-10-19): that press no longer moves the set speed at all - card swallows it and reports it, the
planner arms exactly what card reports, and the set speed changes ONCE, straight to the limit, when it is in force.
push9 stepped the set speed at the press AND set the limit at the sign (two changes in 7 of the 10 push9 windows).

The rig is test_speed_limit_confirm_grace's - card's VCruiseHelper at 100 Hz, selfdrived's ButtonStateTracker, the
planner's SpeedLimitAssist at 20 Hz on a clock this test owns, non-PCM, card's carStateSP report with the buttons -
plus mapd's look-ahead at 1 Hz and the planner's 'now' window (NowConfirm), fed the way
LongitudinalPlannerSP.update_now_window feeds it.
"""
from types import SimpleNamespace

from openpilot.common.parameterized import parameterized

from openpilot.cereal import custom
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.car import cruise
from openpilot.sunnypilot.selfdrive.car import cruise_ext
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.common import Mode
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.now_confirm import ARM_KEEP_S, APPROACH_HOLD_S, NEAR_M, \
  NOW_FLASH_HOLD_FRAMES, ComingLimit, NowConfirm, NowFlashHold, reached, END_CARD_OFF, END_LAPSED, END_NONE, END_OFF, \
  END_OTHER_KEY, END_OTHER_LIMIT, END_SELF_RAISED, END_TAKEN, END_WALKED_PAST
from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.tests.test_speed_limit_confirm_grace import AssistState, \
  GraceTestBase, MINUS, PLANNER_EVERY, PLUS, Rig
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

V_KPH = 60.
V = V_KPH * CV.KPH_TO_MS
NOW_BEFORE_SIGN_S = NEAR_M / V   # 'now' opens this long before the sign (under 50 m)


class NowRig(Rig):
  """Driving at 60 km/h toward a sign `sign_m` meters ahead whose limit is `ahead_kph`. mapd publishes the look-ahead
  once a second until the car reaches the sign; the limit comes into force (the held limit changes) `in_force_s` after
  the sign - 1.3-1.6 s on routes 134/137 - or never (`phantom`)."""
  def __init__(self, set_kph, limit_kph, ahead_kph, sign_m=300., in_force_s: float = 1.5, phantom: bool = False, **kw):
    super().__init__(set_kph, limit_kph, **kw)
    self.ahead_kph = ahead_kph
    self.sign_t = sign_m / V
    self.t_force = self.sign_t + in_force_s
    self.phantom = phantom
    self.now_window: list[int] = []
    self.force_frame: int | None = None   # the card frame of the planner frame the limit came into force on
    self._map = (0, False, 0.)

  def _map_now(self):
    """(map frame, look-ahead valid, distance) as mapd last published, 1 Hz."""
    k = int(self.now)
    t_msg = float(k)
    if self._map[0] != k + 1:
      dist = (self.sign_t - t_msg) * V
      self._map = (k + 1, dist > 0, dist)
    return self._map

  def step(self, events=()):
    if self.frame % PLANNER_EVERY == 0:
      t = self.frame * DT_CTRL
      if not self.phantom and t >= self.t_force and self.limit_kph != self.ahead_kph:
        self.limit_kph = self.final_kph = self.ahead_kph
        self.force_frame = self.frame
      self.now = t
      frame, valid, dist = self._map_now()
      # the planner's window, behind the card's setting (HudApproachingLimit: SpeedLimitAssist.now_card)
      self.now_window.append(self.sla.now_confirm.update(
        t, map_ok=True, map_frame=frame, ahead_valid=valid, ahead_ms=self.ahead_kph * CV.KPH_TO_MS, ahead_dist=dist,
        nsw_state=2, car_ok=True, v_ego=V, standstill=False, limit=int(self.limit_kph), is_metric=True,
        enabled=self.sla.now_card))
    super().step(events)

  def at(self, t: float):
    while self.frame * DT_CTRL < t:
      self.step()

  def tap(self, button, at: float, hold: float = 0.12):
    """Press at `at` for `hold`, then 0.1 s for the planner to hear the release."""
    self.at(at)
    self.step([(button, True)])
    self.run(hold)
    self.step([(button, False)])
    self.run(0.1)

  def engage_and_settle(self):
    self.run(1.5)
    assert self.sla.state == AssistState.active, self.sla.state

  # what the tests read
  @property
  def assist(self):
    """The planner's latest published assist (what card and the UI read)."""
    return self.plans[-1][1].speedLimit.assist

  @property
  def armed(self) -> int:
    return self.sla.now_confirm.armed

  def changes(self, since: int = 0) -> list[tuple[float, float]]:
    """The set-speed changes from card frame `since` on: [(from, to)]."""
    s = self.set_speeds
    return [(s[i - 1], s[i]) for i in range(max(since, 1), len(s)) if s[i] != s[i - 1]]

  def first_change_frame(self, since: int) -> int | None:
    s = self.set_speeds
    return next((i for i in range(max(since, 1), len(s)) if s[i] != s[i - 1]), None)


class NowConfirmBase(GraceTestBase):
  def rig(self, mocker, set_kph=60., limit_kph=60., ahead_kph=50., **kw) -> NowRig:
    rig = NowRig(set_kph, limit_kph, ahead_kph, **kw)
    self.setup_clock(mocker, rig)
    rig.engage_and_settle()
    return rig


class TestNowWindow(NowConfirmBase):
  def test_the_window_is_the_cards_now(self):
    assert NEAR_M == 50.0 and APPROACH_HOLD_S == 3.0 and ARM_KEEP_S == 2.5
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model
    # one definition: the HUD imports it, and its ApproachState is the same state machine the planner runs
    assert hud_model.NEAR_M is NEAR_M and hud_model.reached is reached
    assert type(hud_model.ApproachState().core) is ComingLimit and type(NowConfirm().coming) is ComingLimit

  def test_now_opens_under_50_m_and_never_further_out(self, mocker):
    rig = self.rig(mocker)
    rig.at(rig.sign_t - 120. / V)          # 'in 120 m'
    assert rig.now_window[-1] == 0
    rig.at(rig.sign_t - 40. / V)           # under 'Near'
    assert rig.now_window[-1] == 50
    rig.at(rig.sign_t + 1.0)               # past the sign, the look-ahead gone, held
    assert rig.now_window[-1] == 50

  def test_the_window_closes_when_the_limit_comes_into_force(self, mocker):
    rig = self.rig(mocker)
    rig.at(rig.sign_t + 1.6)
    assert rig.limit_kph == 50. and rig.now_window[-1] == 0

  def test_never_in_a_tunnel_nor_at_a_standstill(self):
    n = NowConfirm()

    def now(nsw_state: int, standstill: bool) -> int:
      n.reset()
      return n.update(0., map_ok=True, map_frame=1, ahead_valid=True, ahead_ms=50 * CV.KPH_TO_MS, ahead_dist=30.,
                      nsw_state=nsw_state, car_ok=True, v_ego=V, standstill=standstill, limit=60, is_metric=True)
    assert now(2, False) == 50
    assert now(4, False) == 0 and now(7, False) == 0, "never in a tunnel"
    assert now(2, True) == 0, "never at a standstill"

  def test_nothing_says_now_with_the_card_switched_off(self):
    """HudApproachingLimit off: the card never says 'now', so the planner has no window and drops an armed press."""
    n = NowConfirm()

    def update(t: float, enabled: bool) -> int:
      return n.update(t, map_ok=True, map_frame=1, ahead_valid=True, ahead_ms=50 * CV.KPH_TO_MS, ahead_dist=30.,
                      nsw_state=2, car_ok=True, v_ego=V, standstill=False, limit=60, is_metric=True, enabled=enabled)
    assert update(0., True) == 50
    assert n.arm(0.01, 50, False) and n.armed == 50
    assert update(0.05, False) == 0 and n.armed == 0 and n.now_limit == 0
    assert n.last_end == END_CARD_OFF and n.offer(60) == (0, 0, 0)


def _fmt(d: float) -> str:
  """SpeedLimitRenderer._format_dist, metric: what the HUD's card turns into 'in 60 m' or, under 50 m, 'now'."""
  return "Near" if d < 50 else f"{int(round(d, -1) if d < 200 else round(d, -2))} m"


class _HudSM:
  """The UI's SubMaster as hud_model reads it: carState, liveMapDataSP, longitudinalPlanSP's resolver (a map limit)."""
  def __init__(self):
    self.seen = dict.fromkeys(('carState', 'liveMapDataSP', 'longitudinalPlanSP'), True)
    self.alive = dict(self.seen)
    self.recv_frame = dict.fromkeys(self.seen, 1)
    self.d: dict = {}

  def __getitem__(self, k):
    return self.d[k]


class TestCardAndPlannerAgree(GraceTestBase):
  """push9 review: the HUD's card and the planner's window are one state machine, so on the same frames they say 'now'
  for the same limit on the same frames. Each scenario failed on the second copy NowConfirm had before."""
  @staticmethod
  def _run(frames):
    """frames: (t, map message this frame?, look-ahead valid, coming kph, its distance m, v m/s, limit in force kph).
    Returns (the HUD's 'now' limit, the planner's) per frame, 0 when not 'now'."""
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad import hud_model as hm
    sm, st, nc = _HudSM(), hm.ApproachState(), NowConfirm()
    settings = hm.HudSettings(approaching_limit=True)
    source = custom.LongitudinalPlanSP.SpeedLimit.Source.map
    out = []
    lmd = None
    for t, msg, valid, ahead_kph, dist, v, limit_kph in frames:
      if msg or lmd is None:
        lmd = SimpleNamespace(speedLimitAheadValid=valid, speedLimitAhead=ahead_kph * CV.KPH_TO_MS,
                              speedLimitAheadDistance=dist, nswZone=SimpleNamespace(state=2))
        sm.recv_frame['liveMapDataSP'] += 1
      sm.d['liveMapDataSP'] = lmd
      sm.d['carState'] = SimpleNamespace(vEgo=v, standstill=False)
      res = SimpleNamespace(speedLimitValid=True, speedLimitLastValid=True, source=source,
                            speedLimitLast=limit_kph * CV.KPH_TO_MS)
      sm.d['longitudinalPlanSP'] = SimpleNamespace(speedLimit=SimpleNamespace(resolver=res))
      ap = st.update(sm, settings, started_frame=0, now=t, is_metric=True, speed_limit_mode_on=True)
      hud = ap.limit if ap is not None and hm.approach_text(_fmt(ap.dist), ap.held) == "now" else 0
      pl = nc.update(t, map_ok=True, map_frame=sm.recv_frame['liveMapDataSP'], ahead_valid=bool(lmd.speedLimitAheadValid),
                     ahead_ms=float(lmd.speedLimitAhead), ahead_dist=float(lmd.speedLimitAheadDistance), nsw_state=2,
                     car_ok=True, v_ego=v, standstill=False, limit=limit_kph, is_metric=True)
      out.append((hud, pl))
    return out

  def test_lost_far_out_at_speed_never_opens_now(self):
    """Route 112 at 2170.65: 'in 60 m'... then the look-ahead lost 120 m out at 105 km/h. The card drops it after
    APPROACH_GAP_S; the planner's copy ran it down for APPROACH_HOLD_S and opened 'now' with nothing on screen."""
    v = 105 / 3.6
    frames = [(i * 0.05, i % 20 == 0, i < 20, 60, 120. + v * (1 - i // 20 * 1.0) if i < 20 else 0., v, 110)
              for i in range(100)]
    out = self._run(frames)
    assert [h for h, _ in out] == [p for _, p in out]
    assert not any(p for _, p in out), "no 'now' anywhere: the change was never reached"

  def test_the_bounce_back_over_50_m_stays_now(self):
    """Route 10f t 194.64: run down under 50 m between messages ('now'), then mapd's next figure 51 m. The card holds
    the run-down distance (it only counts down); the planner's copy closed 'now' and a press there was a 1 km/h step."""
    v = 10.
    frames = []
    for i in range(40):
      t = i * 0.05
      frames.append((t, i in (0, 20), True, 70, 58. if i < 20 else 51., v, 50))
    out = self._run(frames)
    assert [h for h, _ in out] == [p for _, p in out]
    assert all(p == 70 for _, p in out[18:]), [p for _, p in out]

  def test_at_walking_pace_only_inside_the_window(self):
    """Route 12a at 3150.93: at 2 m/s the window is 30 m (15 s of travel). A change 40 m off is not shown - nor 'now'."""
    frames = [(i * 0.05, i % 20 == 0, True, 60, 40. - 2. * (i // 20), 2., 70) for i in range(60)]
    out = self._run(frames)
    assert [h for h, _ in out] == [p for _, p in out]
    assert out[0] == (0, 0), "40 m out at 2 m/s is outside the 30 m window"


class TestNowConfirm(NowConfirmBase):
  """sla-flash: the press at 'now' holds - no set-speed change - and the limit is set once, the moment it is in force."""
  @parameterized.expand([(MINUS, 60., 50.), (PLUS, 50., 60.)], names=["button", "limit_kph", "ahead_kph"])
  def test_a_press_at_now_sets_the_limit_the_moment_it_is_in_force(self, mocker, button, limit_kph, ahead_kph):
    rig = self.rig(mocker, set_kph=limit_kph, limit_kph=limit_kph, ahead_kph=ahead_kph)
    rig.tap(button, rig.sign_t - 30. / V)
    assert rig.set_speeds[-1] == limit_kph, "sla-flash: the press moves no set speed until the limit is in force"
    assert rig.armed == int(ahead_kph) and rig.card.now_consumed_seq == 1
    a = rig.assist
    assert (a.armedLimit, a.armedTarget, a.armSeq) == (int(ahead_kph), int(ahead_kph), 1)
    assert abs(a.armedSpeed - ahead_kph * CV.KPH_TO_MS) < 1e-4 and a.nowKey == (1 if button == PLUS else -1)
    t_force = rig.t_force
    rig.at(t_force + 0.3)
    assert rig.set_speeds[-1] == ahead_kph, rig.set_speeds[-1]
    assert set(rig.set_speeds) == {limit_kph, ahead_kph}, "one change, straight to the limit"
    assert AssistState.preActive not in rig.states, "a prompt opened: the press did not count"
    # set within two planner frames of the limit coming into force
    first = next(i for i, v in enumerate(rig.set_speeds) if v == ahead_kph)
    assert first * DT_CTRL - t_force <= 2 * PLANNER_EVERY * DT_CTRL + 0.02, first * DT_CTRL - t_force
    assert rig.assist.armedLimit == 0 and rig.assist.armedSpeed == 0. and rig.assist.armEnd == END_TAKEN
    rig.run(2.)
    assert rig.confirmed and rig.set_speeds[-1] == ahead_kph

  @parameterized.expand([(b, s) for b in (PLUS, MINUS) for s in ("active", "inactive")], names=["button", "sla_at_press"])
  def test_no_change_at_the_press_and_one_at_the_sign(self, mocker, button, sla_at_press):
    """Both keys, SLA active or inactive at the press: nothing moves at the press, one change when the limit is in force,
    never the prompt."""
    limit, ahead = (50., 60.) if button == PLUS else (60., 50.)
    rig = self.rig(mocker, set_kph=limit, limit_kph=limit, ahead_kph=ahead)
    start = limit
    if sla_at_press == "inactive":
      # an ordinary step the other way, further out than 'now', takes SLA active -> inactive
      rig.tap(MINUS if button == PLUS else PLUS, rig.sign_t - 200. / V)
      start = limit - 1. if button == PLUS else limit + 1.
      assert rig.sla.state == AssistState.inactive and rig.set_speeds[-1] == start and rig.armed == 0
    n = len(rig.set_speeds)
    rig.tap(button, rig.sign_t - 30. / V)
    assert rig.set_speeds[-1] == start and rig.armed == int(ahead)
    assert rig.sla.state == (AssistState.active if sla_at_press == "active" else AssistState.inactive)
    rig.at(rig.t_force + 0.3)
    assert rig.changes(n) == [(start, ahead)], rig.changes(n)
    assert AssistState.preActive not in rig.states[n:]
    assert rig.sla.state == AssistState.active and rig.sla.now_confirm.last_end == END_TAKEN

  @parameterized.expand([(MINUS,), (PLUS,)], names=["button"])
  def test_a_long_press_at_now_is_taken_whole(self, mocker, button):
    """Held 1.3 s in 'now': taken at its first long-press step (0.5 s), the rest of the hold swallowed, one change."""
    limit, ahead = (60., 50.) if button == MINUS else (50., 60.)
    rig = self.rig(mocker, set_kph=limit, limit_kph=limit, ahead_kph=ahead)
    n = len(rig.set_speeds)
    rig.tap(button, rig.sign_t - 45. / V, hold=1.3)
    assert rig.frame * DT_CTRL < rig.sign_t, "the hold ran past the sign: the test proves nothing"
    assert rig.set_speeds[-1] == limit and rig.armed == int(ahead) and rig.card.now_consumed_seq == 1
    rig.at(rig.t_force + 0.3)
    assert rig.changes(n) == [(limit, ahead)]
    assert AssistState.preActive not in rig.states[n:]

  @parameterized.expand([(1.3, 50.), (1.6, 45.)], names=["hold", "walked_to"])
  def test_a_walk_begun_before_now_is_not_taken(self, mocker, hold, walked_to):
    """A long '-' that went down 0.6 s before 'now' opened: its first step came before 'now', so it is the driver's own
    walk - it keeps walking (never frozen halfway) and nothing is armed. On the target (50) the change confirms itself
    through the prompt's own 'already there' rule; past it (45) the prompt asks '+' as before."""
    rig = self.rig(mocker)                       # 60, a 50 coming
    n = len(rig.set_speeds)
    rig.tap(MINUS, rig.sign_t - NOW_BEFORE_SIGN_S - 0.6, hold=hold)
    assert rig.card.now_consumed_seq == 0 and rig.sla.now_confirm.arm_seq == 0 and rig.armed == 0
    assert rig.set_speeds[-1] == walked_to, rig.changes(n)
    assert len(rig.changes(n)) >= 2, "the walk was frozen"
    rig.at(rig.t_force + 0.5)
    if walked_to == 50.:
      assert rig.sla.state == AssistState.active and rig.set_speeds[-1] == 50.
    else:
      assert rig.sla.state == AssistState.preActive and rig.set_speeds[-1] == walked_to

  def test_the_same_key_again_while_armed_moves_nothing(self, mocker):
    """The card's key again - inside 'now', and after 'now' has closed while the arm still waits (ARM_KEEP_S): taken
    again, nothing moves, the arm is unchanged (armSeq stays 1). The limit comes in 4.5 s after the sign, past the
    3 s 'now' hold."""
    rig = self.rig(mocker, in_force_s=4.5)
    n = len(rig.set_speeds)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.armed == 50 and rig.sla.now_confirm.arm_seq == 1
    for _ in range(3):
      rig.tap(MINUS, 0.)
    rig.tap(MINUS, rig.sign_t + 0.5)             # still 'now' (held)
    rig.at(rig.sign_t + APPROACH_HOLD_S + 0.3)
    assert rig.now_window[-1] == 0 and rig.armed == 50, "'now' has closed, the arm waits"
    assert rig.assist.nowKey == -1, "while armed the plan offers the arm's key, 'now' or not"
    rig.tap(MINUS, rig.sign_t + APPROACH_HOLD_S + 0.6)
    assert rig.set_speeds[-1] == 60. and rig.sla.now_confirm.arm_seq == 1 and rig.card.now_consumed_seq == 6
    rig.at(rig.t_force + 0.3)
    assert rig.changes(n) == [(60., 50.)]
    assert AssistState.preActive not in rig.states[n:] and rig.sla.now_confirm.last_end == END_TAKEN

  def test_the_wrong_way_does_not_arm(self, mocker):
    rig = self.rig(mocker)                       # 60 now, 50 coming: '-' asked
    rig.tap(PLUS, rig.sign_t - 30. / V)
    assert rig.armed == 0 and rig.set_speeds[-1] == 61., "an ordinary step"
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive, "the ordinary prompt, as before"

  def test_a_press_the_other_way_disarms(self, mocker):
    """The way out: the other key is its ordinary step, disarms (armEnd otherKey), and the change is the prompt."""
    rig = self.rig(mocker)
    rig.tap(MINUS, rig.sign_t - 45. / V)
    assert rig.armed == 50 and rig.set_speeds[-1] == 60.
    rig.tap(PLUS, rig.sign_t - 20. / V)
    assert rig.armed == 0 and rig.set_speeds[-1] == 61. and rig.sla.now_confirm.last_end == END_OTHER_KEY
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive and rig.set_speeds[-1] == 61.

  def test_further_out_than_now_is_an_ordinary_step(self, mocker):
    rig = self.rig(mocker)
    rig.tap(MINUS, rig.sign_t - 120. / V)        # 'in 120 m'
    assert rig.armed == 0 and rig.assist.nowKey == 0
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive and rig.set_speeds[-1] == 59.

  @parameterized.expand([(-1.6, END_LAPSED), (0.2, END_OTHER_LIMIT)], names=["changed_at", "end"])
  def test_only_that_exact_limit(self, mocker, changed_at, end):
    """What actually comes into force is 40: the ordinary prompt, nothing moved. mapd changing its mind before the sign
    closes the 50's 'now' and the arm lapses before the change; after it (the look-ahead gone, 'now' held), the 40
    coming into force ends the arm."""
    rig = self.rig(mocker, ahead_kph=50.)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.armed == 50 and rig.set_speeds[-1] == 60.
    rig.at(rig.sign_t + changed_at)
    rig.ahead_kph = 40.
    rig.at(rig.t_force + 0.3)
    assert rig.limit_kph == 40.
    assert rig.sla.state == AssistState.preActive, "a different limit is the ordinary prompt"
    assert set(rig.set_speeds) == {60.} and rig.sla.now_confirm.last_end == end

  def test_a_phantom_sets_nothing(self, mocker):
    """Route 134 at 304.52 and 674.56: a limit announced that never came into force. The arm lapses ARM_KEEP_S after
    'now' closes - and nothing was moved, so there is nothing to undo (13f 326.35, 141 236.62: the driver undid push9's
    step by hand). The same number coming into force much later is the ordinary prompt."""
    rig = self.rig(mocker, phantom=True)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.armed == 50
    rig.at(rig.sign_t + APPROACH_HOLD_S + ARM_KEEP_S + 0.2)
    assert rig.armed == 0 and rig.sla.now_confirm.last_end == END_LAPSED and rig.assist.armedSpeed == 0.
    assert set(rig.set_speeds) == {60.} and rig.limit_kph == 60.
    rig.limit_kph = rig.final_kph = 50.          # much later, the 50 after all
    rig.run(0.5)
    assert rig.sla.state == AssistState.preActive

  def test_a_higher_limit_sla_raises_by_itself_still_one_change(self, mocker):
    """SLA ACTIVE at 80 with a 100 coming: the press is taken (the card offered it), SLA raises the set speed by itself
    at the change as upstream (a limit >= 80 with the set speed >= 80), the arm ends selfRaised - one change."""
    rig = self.rig(mocker, set_kph=80., limit_kph=80., ahead_kph=100.)
    n = len(rig.set_speeds)
    rig.tap(PLUS, rig.sign_t - 30. / V)
    assert rig.armed == 100 and rig.set_speeds[-1] == 80.
    rig.at(rig.t_force + 0.3)
    assert rig.changes(n) == [(80., 100.)] and rig.sla.now_confirm.last_end == END_SELF_RAISED
    assert AssistState.preActive not in rig.states[n:]

  def test_minus_with_the_gas_down_does_not_go_to_v_ego(self, mocker):
    """'-' with the gas pressed takes the set speed UP to vEgo (route 114); taken at 'now' it moves nothing."""
    rig = self.rig(mocker, set_kph=50., limit_kph=50., ahead_kph=40.)   # 60 km/h, 50 set, a 40 coming
    rig.gas = True
    n = len(rig.set_speeds)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    rig.gas = False
    assert rig.set_speeds[-1] == 50. and rig.armed == 40
    rig.at(rig.t_force + 0.3)
    assert rig.changes(n) == [(50., 40.)]

  def test_disengaging_disarms(self, mocker):
    """rev2: braking or a cancel while armed disengages, and that ends the arm (armEnd off) on the planner's next
    frame: the flash stops (armedSpeed 0), the key is no longer offered, nothing moved."""
    rig = self.rig(mocker)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.armed == 50 and rig.assist.armedSpeed > 0.
    rig.engaged = False
    rig.run(0.06)
    assert rig.armed == 0 and rig.sla.now_confirm.last_end == END_OFF
    assert rig.assist.armedLimit == 0 and rig.assist.armedSpeed == 0. and rig.assist.nowKey == 0
    assert set(rig.set_speeds) == {60.}

  def test_not_while_cruise_is_off(self, mocker):
    """With cruise off '-' engages (routes 137 88.47, 479.71): a press while not engaged arms nothing, and a report of
    one would arm nothing either."""
    rig = self.rig(mocker)
    sla = rig.sla
    sla.update(False, False, V, 0., 60 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, True, 0., EventsSP())
    assert sla.state == AssistState.disabled and sla.now_offer == (0, 0, 0)
    rig.now = rig.sign_t - 1.
    sla.now_confirm.now_limit = 50
    sla.update_buttons(sla._release_toggle_prev ^ (1 << MINUS), 0)
    sla.update_now_consumed(sla._now_seq_prev + 1, 50, False)
    sla.update(False, False, V, 0., 59 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, 60 * CV.KPH_TO_MS, True, 0., EventsSP())
    assert sla.now_confirm.armed == 0 and sla.now_offer == (0, 0, 0)

  def test_nothing_changes_before_now(self, mocker):
    """Without a press at 'now', every frame is what it was before push9: the prompt at the change. The plan offers a
    key only inside the window."""
    rig = self.rig(mocker)
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive and set(rig.set_speeds) == {60.}
    keys = [(w, p.speedLimit.assist.nowKey, p.speedLimit.assist.armedLimit) for w, (_, p) in zip(rig.now_window,
                                                                                                 rig.plans[1:], strict=True)]
    assert all(w for w, k, _ in keys if k), "a key offered outside the window"
    assert any(k for _, k, _ in keys) and not any(a for _, _, a in keys)
    assert rig.card.now_consumed_seq == 0 and rig.sla.now_confirm.last_end == END_NONE

  def test_a_pcm_car_never_arms(self, mocker):
    rig = NowRig(60., 60., 50., pcm_cruise=True)
    self.setup_clock(mocker, rig)
    rig.run(1.5)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.armed == 0 and rig.sla.now_offer == (0, 0, 0)
    assert rig.sla._now_presses == [], "the PCM path must not queue presses for the whole drive"
    rig.sla.update_now_consumed(7, 50, False)   # never on a PCM car - and even a report arms nothing
    rig.run(0.1)
    assert rig.sla._now_arm is None and rig.armed == 0


class TestNowOff(NowConfirmBase):
  """Where the feature cannot apply, the plan never offers a key (nowKey 0) and every press is what it was."""
  def _press_and_check(self, rig: NowRig, stepped: float):
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.card.now_consumed_seq == 0 and rig.armed == 0
    assert rig.set_speeds[-1] == stepped
    assert not any(p.speedLimit.assist.nowKey for _, p in rig.plans[1:])

  def test_the_card_switched_off(self, mocker):
    self.params.put_bool("HudApproachingLimit", False, block=True)
    rig = self.rig(mocker)
    assert not rig.sla.now_card
    self._press_and_check(rig, 59.)

  @parameterized.expand([(Mode.off,), (Mode.information,), (Mode.warning,)], names=["mode"])
  def test_speed_limit_mode_not_assist(self, mocker, mode):
    self.params.put("SpeedLimitMode", int(mode), block=True)
    rig = NowRig(60., 60., 50.)
    self.setup_clock(mocker, rig)
    rig.run(1.5)
    self._press_and_check(rig, 59.)

  def test_disengaged(self, mocker):
    rig = self.rig(mocker)
    rig.engaged = False
    rig.run(0.2)
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.card.now_consumed_seq == 0 and rig.armed == 0 and rig.set_speeds[-1] == 60.
    assert rig.assist.nowKey == 0

  def test_a_stale_plan_in_card(self, mocker):
    """card's plan no longer arriving (sm.alive false): card takes nothing - the press is an ordinary step - and the
    planner, hearing no report, arms nothing; the change is the prompt."""
    rig = self.rig(mocker)
    rig.plan_alive = False
    rig.tap(MINUS, rig.sign_t - 30. / V)
    assert rig.assist.nowKey == -1, "the planner still offers it"
    assert rig.card.now_consumed_seq == 0 and rig.armed == 0 and rig.set_speeds[-1] == 59.
    rig.at(rig.t_force + 0.3)
    assert rig.sla.state == AssistState.preActive


class TestTheHandOverRace(NowConfirmBase):
  """5.3 of the design: card takes a press on plan j's key; the planner hears of it on j+1 or j+2, and the window may
  have closed - the limit in force - in between. Every press from 0.3 s before the hand-over to 0.2 s after it, at every
  plan and button lag from 1 to 4 card frames: either card let it through (an ordinary step, or the prompt's own
  confirm) or card took it and the set speed changed exactly once, to the limit, by in-force + 0.3 s - and the prompt,
  if the hand-over beat the report, for no more than 2 planner frames. Never a press taken and lost here (a report the
  hand-over beat is refused, and the prompt takes the press's release), never an arm for the limit already in force.
  The hand-over to a DIFFERENT limit is TestTheArmIsTheOffer's."""
  @parameterized.expand([(p, b) for p in (1, 2, 3, 4) for b in (1, 2, 3, 4)], names=["plan_lag", "button_lag"])
  def test_the_race(self, mocker, plan_lag, button_lag):
    outcomes = set()
    for k in range(-30, 21):
      rig = NowRig(60., 60., 50., sign_m=60., plan_lag=plan_lag, button_lag=button_lag)
      self.setup_clock(mocker, rig)
      rig.engage_and_settle()
      release = rig.t_force + k * 0.01
      n = len(rig.set_speeds)
      rig.tap(MINUS, release - 0.12)
      rig.at(rig.t_force + 0.6)
      taken = rig.card.now_consumed_seq > 0
      ch = rig.changes(n)
      pre = sum(1 for s in rig.states[n:] if s == AssistState.preActive)
      where = f"release {k * 10:+d} ms from the hand-over: taken={taken} changes={ch} preActive frames={pre}"
      if taken:
        assert ch == [(60., 50.)], where
        first = rig.first_change_frame(n)
        assert first is not None and first * DT_CTRL <= rig.t_force + 0.3, where
        assert pre <= 2 * PLANNER_EVERY, where
        outcomes.add("taken")
      elif ch == [(60., 59.)]:
        outcomes.add("step")
      else:
        # after the hand-over: the prompt, and the press is the prompt's own confirm - one change too
        assert ch == [(60., 50.)] and pre > 0, where
        outcomes.add("prompt")
      assert rig.force_frame is not None
      # never an arm for the limit already in force (nor taken twice)
      assert rig.sla.now_confirm.arm_seq <= 1, where
      assert all(p.speedLimit.assist.armedLimit != int(p.speedLimit.resolver.speedLimitLast * CV.MS_TO_KPH + 0.5)
                 for _, p in rig.plans[1:]), where
    assert "taken" in outcomes and "prompt" in outcomes, outcomes


class OtherLimitRig(NowRig):
  """mapd announced `ahead_kph`, but what comes into force at t_force is `force_kph`, a different limit."""
  def __init__(self, *a, force_kph: float, **kw):
    super().__init__(*a, **kw)
    self.force_kph = force_kph

  def step(self, events=()):
    if self.frame % PLANNER_EVERY == 0 and self.force_frame is None and self.frame * DT_CTRL >= self.t_force:
      self.limit_kph = self.final_kph = self.force_kph
      self.force_frame = self.frame
      self.phantom = True   # NowRig must not switch to ahead_kph after this
    super().step(events)


class TestTheArmIsTheOffer(NowConfirmBase):
  """rev1 (A2, A3, A6): card's report reaches the planner a frame or two after card took the press, and by then the
  planner may have withdrawn the key it offered. The planner arms a report only on the offer it published last
  (SpeedLimitAssist._now_arm_ok): that limit and that key, and - when the offer was the arm's own key - that arm still
  standing. Nothing here moves the set speed more than once."""
  @parameterized.expand([(f, p, b) for f in (70., 40.) for p, b in ((1, 1), (2, 4), (4, 4))],
                        names=["force_kph", "plan_lag", "button_lag"])
  def test_a_different_limit_winning_the_hand_over_arms_nothing(self, mocker, force_kph, plan_lag, button_lag):
    """A2: '-' at 'now' for the 50 mapd announced, and 70 (or 40) comes into force inside the report's trip. The report
    lands on the prompt for the other limit and must not arm the 50 there: the cluster would flash 50 while the comma
    card asks for 70, and with the 40 confirmed, an arm for 50 under it."""
    for k in range(-5, 3):
      rig = OtherLimitRig(60., 60., 50., sign_m=60., plan_lag=plan_lag, button_lag=button_lag, force_kph=force_kph)
      self.setup_clock(mocker, rig)
      rig.engage_and_settle()
      n = len(rig.set_speeds)
      rig.tap(MINUS, rig.t_force + k * 0.01 - 0.12)
      rig.at(rig.t_force + 4.0)
      where = f"release {k * 10:+d} ms from the hand-over to {force_kph:.0f}: taken={rig.card.now_consumed_seq}"
      assert rig.force_frame is not None
      armed_after = [(f, int(p.speedLimit.assist.armedLimit), str(p.speedLimit.assist.state)) for f, p in rig.plans[1:]
                     if f >= rig.force_frame and p.speedLimit.assist.armedLimit]
      assert not armed_after, f"{where}: armed after the hand-over {armed_after[:2]} .. ({len(armed_after)} plans)"
      assert len(rig.changes(n)) <= 1, f"{where}: {rig.changes(n)}"

  @parameterized.expand([(1,), (4,)], names=["lag"])
  def test_a_press_at_the_lapse_does_not_arm_again(self, mocker, lag):
    """A3: a phantom's arm, and the card's key again in the last moments before it lapses. card takes it (the plan
    still offers the arm's key) but the report arrives after the lapse: it must not arm afresh - another 2.5 s of
    flashing for a limit that never came."""
    base = NowRig(60., 60., 50., phantom=True, plan_lag=lag, button_lag=lag)
    self.setup_clock(mocker, base)
    base.engage_and_settle()
    base.tap(MINUS, base.sign_t - 30. / V)
    while base.armed:
      base.step()
    lapse_frame = base.frame
    for k in range(-8, 2):
      rig = NowRig(60., 60., 50., phantom=True, plan_lag=lag, button_lag=lag)
      self.setup_clock(mocker, rig)
      rig.engage_and_settle()
      rig.tap(MINUS, rig.sign_t - 30. / V)
      rig.tap(MINUS, lapse_frame * DT_CTRL + k * 0.01 - 0.12)
      rig.run(3.0)
      where = f"release {k * 10:+d} ms from the lapse: taken={rig.card.now_consumed_seq}"
      assert rig.sla.now_confirm.arm_seq == 1 and rig.sla.now_confirm.last_end == END_LAPSED, where
      assert not any(p.speedLimit.assist.armedLimit for f, p in rig.plans[1:] if f >= lapse_frame), where
      assert min(rig.set_speeds) >= 59., where

  def test_a_report_from_before_a_planner_restart_arms_nothing(self, mocker):
    """A6: plannerd restarted while card ran on. The first report the new planner hears carries card's old seq, which
    looks new to it: it must not arm that old limit (a flash for nothing) - it offered nothing yet."""
    rig = NowRig(60., 60., 50.)
    self.setup_clock(mocker, rig)
    rig.card.now_consumed_seq, rig.card.now_consumed_limit = 7, 50   # card's tally from before the restart
    rig.run(1.0)
    assert rig.sla.now_confirm.arm_seq == 0 and rig.armed == 0
    assert not any(p.speedLimit.assist.armedLimit for _, p in rig.plans[1:])


class TestNowPressWithOffset(GraceTestBase):
  """The key is decided on the set speed the limit gives with the user's offset - the number the card shows - in the
  planner (NowConfirm.offer, the key card takes) as in push9's card words (hud_model.now_prompt)."""
  @staticmethod
  def _now(ahead: int, limit: int, offset_type: int = 0, offset_value: float = 0.) -> NowConfirm:
    n = NowConfirm()
    n.update(0., map_ok=True, map_frame=1, ahead_valid=True, ahead_ms=ahead * CV.KPH_TO_MS, ahead_dist=30., nsw_state=2,
             car_ok=True, v_ego=V, standstill=False, limit=limit, is_metric=True, offset_type=offset_type,
             offset_value=offset_value)
    return n

  def test_the_offer_and_the_card_words_agree(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import confirm_lower, now_prompt

    def press_words(lower: bool, target: int) -> str:   # what the card draws from the offer (hud_model.press_words)
      return f"press {'-' if lower else '+'} to set {target}"
    for set_kph in (100, 105, 109, 110, 111, 120):
      for offset_type, offset_value in ((0, 0.), (1, 5.), (2, 10.)):
        words = now_prompt(100, set_kph, is_metric=True, offset_type=offset_type, offset_value=offset_value)
        key, lim, target = self._now(100, 110, offset_type, offset_value).offer(set_kph)
        if not words:
          assert (key, lim, target) == (0, 0, 0), (set_kph, offset_type)
          continue
        assert key == (-1 if confirm_lower(words) else 1) and lim == 100, (set_kph, offset_type, words, key)
        assert press_words(key < 0, target) == words, (set_kph, offset_type, words, target)
        # armed, either key: the arm's key and its target, whatever the set speed
        for plus in (True, False):
          n = self._now(100, 110, offset_type, offset_value)
          assert n.arm(0.01, 100, plus)
          assert n.offer(set_kph) == ((1 if plus else -1), 100, target)

  def test_already_at_the_target_offers_nothing(self):
    # 100 + 10% = 110, the set speed already: the card asks for nothing, and no key is offered
    assert self._now(100, 110, 2, 10.).offer(110) == (0, 0, 0)
    # 105, between the limit and limit + offset: '+' (to 110), not '-'
    assert self._now(100, 110, 2, 10.).offer(105) == (1, 100, 110)
    assert self._now(100, 110).offer(0) == (0, 0, 0), "no set speed (cruise off): nothing"

  def test_past_the_armed_target_disarms(self):
    """push9 re-review (N1): armed 100, a walk card let through down to 99 - the 100 must not raise it back. track()
    sees where the presses ended. The mirror image for '+'."""
    n = self._now(100, 110)
    n.arm(0.0, 100, False)
    n.track(101)
    assert n.armed == 100, "still above the target: armed"
    n.track(99)
    assert n.armed == 0 and n.last_end == END_WALKED_PAST and not n.take(100)
    # on the target it stays armed (nothing to move), and goes past it from there
    n = self._now(100, 110)
    n.arm(0.0, 100, False)
    n.track(100)
    assert n.armed == 100
    n.press(0.2, False, 100)
    assert n.armed == 100, "the arm's key at the target disarms nothing (card swallows it)"
    n.track(99)
    assert n.armed == 0
    n = self._now(80, 60)                        # '+': past it the other way would LOWER the set speed
    n.arm(0.0, 80, True)
    n.track(80)
    assert n.armed == 80
    n.track(81)
    assert n.armed == 0 and not n.take(80)
    n = self._now(100, 110)
    n.arm(0.0, 100, False)
    n.track(0)                                   # no set speed (cruise off): not a press past it
    assert n.armed == 100 and n.take(100) and n.last_end == END_TAKEN


class TestNowConfirmUnit(GraceTestBase):
  """NowConfirm's sla-flash rules on plain values."""
  @staticmethod
  def _now(ahead: int = 50, limit: int = 60) -> NowConfirm:
    return TestNowPressWithOffset._now(ahead, limit)

  def test_arm_and_its_reasons(self):
    n = self._now()
    assert n.last_end == END_NONE and n.arm_seq == 0
    assert not n.arm(0., 0, False), "no limit: nothing to arm"
    assert n.arm(0.1, 50, False) and (n.armed, n.armed_target, n.arm_seq) == (50, 50, 1)
    assert n.arm(0.3, 50, False) and n.arm_seq == 1 and n.armed_at == 0.1, "the same arm again: nothing changes"
    n.press(0.4, False, 60)
    assert n.armed == 50, "a press of the arm's key never disarms"
    n.press(0.5, True, 60)
    assert n.armed == 0 and n.last_end == END_OTHER_KEY
    assert n.arm(0.6, 50, False) and n.arm_seq == 2
    n.disarm()
    assert n.last_end == END_OFF
    n.disarm(END_LAPSED)
    assert n.last_end == END_OFF, "nothing armed: the last reason stays"
    assert n.arm(0.7, 50, False) and not n.take(40) and n.last_end == END_OTHER_LIMIT
    assert n.arm(0.8, 50, False) and n.take(50) and n.last_end == END_TAKEN and n.arm_seq == 4

  def test_a_repeated_arm_does_not_restart_the_lapse(self):
    n = NowConfirm()

    def update(t: float, valid: bool) -> int:
      return n.update(t, map_ok=True, map_frame=1 if valid else 2, ahead_valid=valid, ahead_ms=50 * CV.KPH_TO_MS,
                      ahead_dist=30., nsw_state=2, car_ok=True, v_ego=V, standstill=False, limit=60, is_metric=True)
    update(0., True)
    assert n.arm(0., 50, False)
    t = 0.05
    while True:
      update(t, False)
      if not n.armed:
        break
      n.arm(t, 50, False)   # reported again every frame: the lapse runs on
      t += 0.05
      assert t < APPROACH_HOLD_S + ARM_KEEP_S + 1.0, "the repeated arm kept it alive"
    assert t > APPROACH_HOLD_S + ARM_KEEP_S - 0.1, t
    assert n.last_end == END_LAPSED and n.arm_seq == 1

  def test_the_flash_hold(self):
    h = NowFlashHold()
    assert NOW_FLASH_HOLD_FRAMES == 2
    assert [h.update(x) for x in (0., 22.2, 22.2)] == [0., 22.2, 22.2]
    assert [h.update(0.) for _ in range(4)] == [22.2, 22.2, 0., 0.], "held exactly 2 updates after it drops"
    h.update(13.9)
    assert [h.update(0.), h.update(16.7), h.update(0.), h.update(0.), h.update(0.)] == [13.9, 16.7, 16.7, 16.7, 0.], \
      "a re-arm restarts the hold, with the new value at once"

  def test_the_first_action_is_cruise_long_press(self):
    assert cruise_ext.NOW_FIRST_ACTION_FRAMES == cruise.CRUISE_LONG_PRESS

  def test_an_arm_for_the_limit_in_force_is_refused(self, mocker):
    """The hand-over beat card's report: the limit card took the press for is already the one in force (the frame
    before's) - no arm, or it would flash for 2.5 s after the prompt had confirmed it. (Each report is put on an offer
    the planner made for it - rev1: only an offered press arms, TestTheArmIsTheOffer - so this is the in-force rule
    alone.)"""
    rig = Rig(60., 50.)
    self.setup_clock(mocker, rig)
    rig.run(1.0)
    sla = rig.sla
    sla.now_offer = (-1, 50, 50)
    sla.update_now_consumed(1, 50, False)
    rig.run(0.06)
    assert sla.now_confirm.armed == 0 and sla.now_confirm.arm_seq == 0
    sla.now_offer = (-1, 40, 40)
    sla.update_now_consumed(2, 40, True)    # the offered limit on the other key: not what was offered
    rig.run(0.06)
    assert sla.now_confirm.armed == 0 and sla.now_confirm.arm_seq == 0
    sla.now_offer = (-1, 40, 40)
    sla.update_now_consumed(3, 40, False)   # another limit, the offered key: armed (target 40)
    rig.run(0.06)
    assert sla.now_confirm.armed == 40

  def test_an_arm_is_held_at_most_arm_max_s(self):
    """rev1 A5: at a crawl 'now' (under 50 m) has no time bound, and the cluster would flash N / blank all that time -
    the stock radar's longest flash in 13 stock drives was 8.6 s. An arm is dropped ARM_MAX_S after the press, 'now' or
    not (armEnd capped): nothing was moved, and at the sign the prompt asks as before. 'now' still open, the key is
    offered again."""
    from openpilot.sunnypilot.selfdrive.controls.lib.speed_limit.now_confirm import ARM_MAX_S, END_CAPPED
    assert ARM_MAX_S == 12.0, "longer than every arm the replays took (4.1 s) and the longest 'now' they offered (10.3 s)"
    n = NowConfirm()

    def update(t: float) -> int:   # crawling at 2.5 m/s (its window 37.5 m), the 50 30 m ahead: 'now' the whole time
      return n.update(t, map_ok=True, map_frame=int(t) + 1, ahead_valid=True, ahead_ms=50 * CV.KPH_TO_MS, ahead_dist=30.,
                      nsw_state=2, car_ok=True, v_ego=2.5, standstill=False, limit=60, is_metric=True)
    assert update(0.) == 50 and n.arm(0., 50, False)
    t = 0.05
    while t < ARM_MAX_S - 0.01:
      assert update(t) == 50 and n.armed == 50, t
      t += 0.05
    update(ARM_MAX_S + 0.05)
    assert n.armed == 0 and n.last_end == END_CAPPED and n.now_limit == 50
    assert n.offer(60) == (-1, 50, 50), "'now' still open: a new press arms again"
    assert n.arm(ARM_MAX_S + 0.1, 50, False) and n.arm_seq == 2


class TestCardTakes(GraceTestBase):
  """card's side (cruise_ext.VCruiseHelperSP): which press it takes, on plain plans."""
  @staticmethod
  def _card(pcm: bool = False):
    from openpilot.selfdrive.car.cruise import VCruiseHelper
    from opendbc.car.structs import car
    c = VCruiseHelper(car.CarParams(pcmCruise=pcm), custom.CarParamsSP(pcmCruiseSpeed=not pcm))
    c.v_cruise_kph = c.v_cruise_cluster_kph = 60.
    return c

  @staticmethod
  def _plan(key: int, limit: int, state=AssistState.active):
    msg = custom.LongitudinalPlanSP.new_message()
    r = msg.speedLimit.resolver
    r.speedLimitValid = r.speedLimitLastValid = True
    r.speedLimit = r.speedLimitLast = r.speedLimitFinal = r.speedLimitFinalLast = 60 * CV.KPH_TO_MS
    msg.speedLimit.assist.state = state
    msg.speedLimit.assist.nowKey, msg.speedLimit.assist.nowLimit = key, limit
    return msg.as_reader()

  def test_the_first_action_on_the_offered_key(self):
    c = self._card()
    plan = self._plan(-1, 50)
    c.update_speed_limit_assist(True, plan)
    assert not c.update_speed_limit_assist_pre_active_confirmed(PLUS, 10), "the other key"
    assert not c.update_speed_limit_assist_pre_active_confirmed(MINUS, 100), "a walk's later step (not its first)"
    assert c.update_speed_limit_assist_pre_active_confirmed(MINUS, 50), "a long press's first step"
    assert (c.now_consumed_seq, c.now_consumed_limit, c.now_consumed_plus) == (1, 50, False)
    for _ in range(50):                          # 0.5 s on: that press's next long-press step
      c.update_speed_limit_assist(True, plan)
    assert c.update_speed_limit_assist_pre_active_confirmed(MINUS, 100), "the rest of the press is taken with it"
    assert c.now_consumed_seq == 1, "and not reported again"
    for _ in range(30):
      c.update_speed_limit_assist(True, plan)
    assert c.update_speed_limit_assist_pre_active_confirmed(MINUS, 12), "a new short press, on its release"
    assert c.now_consumed_seq == 2

  def test_never_without_the_offer(self):
    for plan, alive, pcm in ((self._plan(0, 0), True, False), (self._plan(-1, 0), True, False),
                             (self._plan(-1, 50), False, False), (self._plan(-1, 50), True, True)):
      c = self._card(pcm)
      c.update_speed_limit_assist(True, plan, plan_alive=alive)
      assert not c.update_speed_limit_assist_pre_active_confirmed(MINUS, 10)
      assert c.now_consumed_seq == 0

  def test_never_inside_the_prompt(self):
    """preActive (the prompt): the press is the prompt's own - the asked-for key confirms it, the other is a step."""
    c = self._card()
    c.update_speed_limit_assist(True, self._plan(1, 80, AssistState.preActive))
    assert not c.update_speed_limit_assist_pre_active_confirmed(PLUS, 10)
    assert c.now_consumed_seq == 0

  def test_the_report_on_the_builder(self):
    """card writes the report on the carStateSP builder it publishes (card.py), and it arrives."""
    from openpilot.cereal import messaging
    from opendbc.car import structs
    from openpilot.selfdrive.car.helpers import convert_to_capnp
    c = self._card()
    c.update_speed_limit_assist(True, self._plan(1, 80))
    assert c.update_speed_limit_assist_pre_active_confirmed(PLUS, 9)
    cs_sp = convert_to_capnp(structs.CarStateSP())
    cruise_ext.write_speed_limit_now(cs_sp, c)
    msg = messaging.new_message('carStateSP')
    msg.carStateSP = cs_sp
    r = messaging.log_from_bytes(msg.to_bytes()).carStateSP.speedLimitNow
    assert (r.consumedSeq, r.consumedLimit, r.consumedPlus) == (1, 80, True)
    assert convert_to_capnp(structs.CarStateSP()).as_reader().speedLimitNow.consumedSeq == 0


class TestNowPrompt(GraceTestBase):
  """The card's words at 'now' (hud_model.now_prompt): the key the press takes and the set speed it gives."""
  def test_the_words(self):
    from openpilot.selfdrive.ui.sunnypilot.mici.onroad.hud_model import confirm_lower, now_prompt, prompt_parts
    assert now_prompt(50, 60, is_metric=True) == "press - to set 50"
    assert now_prompt(80, 60, is_metric=True) == "press + to set 80"
    assert now_prompt(60, 60, is_metric=True) == "", "the set speed is already it: nothing to press"
    assert now_prompt(0, 60, is_metric=True) == "" and now_prompt(50, 0, is_metric=True) == ""
    # the set speed it gives is the limit with the offset, as the resolver adds it
    assert now_prompt(50, 60, is_metric=True, offset_type=1, offset_value=5) == "press - to set 55"
    # the key is the target's way, not the limit's: 100 + 10% is 110, already the set speed - nothing to press; from
    # 105 it is '+', though the limit itself is lower (push9 review)
    assert now_prompt(100, 110, is_metric=True, offset_type=2, offset_value=10) == ""
    assert now_prompt(100, 105, is_metric=True, offset_type=2, offset_value=10) == "press + to set 110"
    assert now_prompt(100, 120, is_metric=True, offset_type=2, offset_value=10) == "press - to set 110"
    assert now_prompt(35, 45, is_metric=False) == "press - to set 35"
    # the card's drawers read the key out of the words, as they do the confirm's
    t = now_prompt(50, 60, is_metric=True)
    assert prompt_parts(t) == ("press", "to set 50") and confirm_lower(t) is True
    assert confirm_lower(now_prompt(80, 60, is_metric=True)) is False
