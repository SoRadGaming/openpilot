"""FORK(LEAD-BRAKE) + FORK(CLOSE-FOLLOW), the merge guard: the lead brake assist (leadbrake) and close follow
(closefollow) in one planner.

Below its hand-back close follow solves its own MPC and, live, blends it into the plan's trajectory by the gap's
authority. These pin how the two meet, behind a lead that brakes hard at -4 m/s^2 from 4.5 m/s (release13: inside the
safe limit; the leadbrake branch ran it at 8 m/s, inside push13's 35 km/h band):
  * close follow's MPC is handed the lead brake's view of the lead (radar_state), not sm['radarState'];
  * close follow's own guard drops the gap at once for a lead braking harder than -0.5 (GAP_LEAD_BRAKE; and holds it
    out 3 s after a hard brake), and the lead brake's raw decel needs radard's aLeadK under -1.0, so on every frame the
    projection is active close follow has no authority: the projection is never diluted, and the lead brake's
    comparison solve is never skipped;
  * the lead brake in shadow (mode 1) is still the plan without it, bit for bit, whatever close follow's mode.
Skipped until close_follow.py is in the tree; written and run on a scratch merge of closefollow d34ccbb70 and leadbrake,
where it fails if close follow's MPC still reads sm['radarState'] (the merge's resolution, docs/fork/README.md LBA).
"""
import unittest
from typing import Any, cast
from unittest import mock

from openpilot.cereal import messaging
from openpilot.common.params import Params
from openpilot.common.test import OpenpilotTestCase
import openpilot.selfdrive.controls.lib.longitudinal_planner as LP
from openpilot.sunnypilot.selfdrive.controls.lib import lead_brake_assist as lba
from openpilot.sunnypilot.selfdrive.controls.lib.tests import test_lead_brake_assist as tl
try:
  from openpilot.sunnypilot.selfdrive.controls.lib import close_follow as cf
except ImportError:
  cf = None   # branch closefollow not merged yet

V = 4.5   # m/s (16.2 km/h): inside close follow's safe limit (release13: aggressive hands back at 17.2)


def planner(lb_mode, cf_mode):
  # (the planner starts at this test's speed: tl.planner's init_v is tl.V0)
  with (mock.patch.object(LP, 'read_mode', return_value=cf_mode), mock.patch.object(lba, 'read_mode', return_value=lb_mode),
        mock.patch.object(tl, 'V0', V)):
    pl = tl.planner(lb_mode)
  # release13: close follow re-reads its switch at the first engage - the switch says the mode this test wants
  Params().put_bool("CloseFollow", cf_mode == cf.MODE_LIVE, block=True)
  return pl


def frames():
  return tl.braking(n=80, brake_at=40, a_brake=-4.0, d0=15.0, v0=V)


def drive(pl):
  seen = []
  cf_saw = []
  c = pl.close_follow
  if c is not None:
    mpc = c._close_mpc()
    orig = mpc.update

    def update(radarstate, *a, **k):
      cf_saw.append(float(radarstate.leadOne.aLeadK))
      return orig(radarstate, *a, **k)
    mpc.update = update
  pm = tl._PM()
  with mock.patch.object(tl, 'V0', V):
    for f in frames():
      sm = tl.build_sm(f)
      # push14: the dash's 1-bar mode, where close follow is live with its switch on (distance_bars.py)
      sp = messaging.new_message('selfdriveStateSP')
      sp.selfdriveStateSP.closeFollowOneBar = True
      sm['selfdriveStateSP'] = sp.selfdriveStateSP.as_reader()
      n = len(cf_saw)
      pl.update(cast(Any, sm))
      pl.publish(cast(Any, sm), pm)
      lb = pl.lead_brake
      seen.append({'live': bool(c and c.live),
                   'a': float(pl.output_a_target), 'active': bool(lb and lb.proj.active), 'a_proj': lb.proj.a_lead if lb else None,
                   'a_k': float(f.aLeadK), 'g': c.g if c else 0.0, 'cf_lead': cf_saw[n] if len(cf_saw) > n else None,
                   'compared': bool(lb and lb.compared), 'skipped': bool(lb and lb.skipped)})
  return seen


@unittest.skipIf(cf is None, "close_follow.py (branch closefollow) is not merged into this tree yet")
class TestLeadBrakeWithCloseFollow(OpenpilotTestCase):
  def test_close_follow_sees_the_projection_and_never_dilutes_it(self):
    pl = planner(lba.MODE_LIVE, cf.MODE_LIVE)
    self.assertIsNotNone(pl.close_follow)
    self.assertTrue(pl.close_follow.switch_on)
    seen = drive(pl)
    self.assertTrue(all(s['live'] for s in seen), "live from the first plan (push14: the switch and the 1-bar mode)")
    act = [s for s in seen if s['active']]
    self.assertTrue(act, "the projection fired")
    self.assertTrue(any(s['g'] > 0.0 for s in seen[:40]), "close follow had the gap while the queue was steady")
    self.assertTrue(all(s['g'] == 0.0 for s in act), "close follow drops out for a braking lead before the projection starts")
    self.assertTrue(all(s['compared'] and not s['skipped'] for s in act))
    solved = [s for s in act if s['cf_lead'] is not None]
    self.assertTrue(solved, "close follow kept solving its MPC (for its log) while the projection was active")
    self.assertTrue(all(abs(s['cf_lead'] - s['a_proj']) < 1e-6 for s in solved), "close follow's MPC got the projected lead")
    self.assertTrue(any(s['a_proj'] < s['a_k'] - 0.5 for s in solved))

  def test_shadow_lead_brake_changes_nothing_whatever_close_follow_does(self):
    for cf_mode in (cf.MODE_SHADOW, cf.MODE_LIVE):
      base = [s['a'] for s in drive(planner(lba.MODE_OFF, cf_mode))]
      shadow = drive(planner(lba.MODE_SHADOW, cf_mode))
      self.assertEqual([s['a'] for s in shadow], base, cf_mode)
      self.assertTrue(any(s['active'] for s in shadow))
