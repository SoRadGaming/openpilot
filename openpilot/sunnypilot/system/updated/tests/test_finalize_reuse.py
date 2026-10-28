"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(UPDATER): fetch_update() keeps a finalized update that already holds the commit and branch it fetched
(finalize_reuse.py), and copies in every other case, as before.

The pass tests run the real Updater.check_for_update(), fetch_update() and set_params() against real git repos: a
bare remote with one submodule, OVERLAY_MERGED and BASEDIR as plain clones of it, FINALIZED in a temp folder. No
overlay (init_overlay needs root), so OVERLAY_MERGED's lower layer is not BASEDIR here.
"""
import contextlib
import hashlib
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system.updated import finalize_reuse as fr
from openpilot.sunnypilot.system.updated.finalize_reuse import CONSISTENT_FLAG, TreeId, finalized_id, reuse_finalized, tree_id

ROOT = Path(__file__).parents[4]   # openpilot/
UPDATED = ROOT / "system/updated/updated.py"
LAUNCH = ROOT.parent / "launch_chffrplus.sh"

GITCONFIG = """[user]
\tname = t
\temail = t@t
[init]
\tdefaultBranch = sunnypilot
[protocol "file"]
\tallow = always
"""
SUB = "sub"
LEFTOVER = f"{SUB}/build.o"   # a build product inside a submodule: git clean in the superproject leaves it
MARKER = ".git/kept-marker"   # in FINALIZED's .git: a fresh copy of the overlay never has it


def git(*args, cwd, **env) -> str:
  return subprocess.check_output(["git", *args], cwd=cwd, env={**os.environ, **env}, stderr=subprocess.STDOUT,
                                 encoding="utf8")


def digest(top: Path, skip: tuple[str, ...] = ()) -> dict:
  """Every path outside the .git folders and files: (type, mode, sha256) or (link, target)."""
  out = {}
  for dirpath, dirnames, filenames in os.walk(top):
    for name in sorted(dirnames + filenames):
      p = Path(dirpath, name)
      rel = str(p.relative_to(top))
      if name == ".git" or rel in skip:
        continue
      st = os.lstat(p)
      if stat.S_ISLNK(st.st_mode):
        out[rel] = ("link", os.readlink(p))
      elif stat.S_ISDIR(st.st_mode):
        out[rel] = ("dir", stat.S_IMODE(st.st_mode))
      else:
        out[rel] = ("file", stat.S_IMODE(st.st_mode), hashlib.sha256(p.read_bytes()).hexdigest())
    if ".git" in dirnames:
      dirnames.remove(".git")
  return out


class Rig:
  """The remote, the overlay's stand-in and FINALIZED for one test. Commits carry fixed dates, so two rigs make the
  same commits."""

  def __init__(self, root: str):
    self.root = Path(root)
    self.t = 1_760_000_000
    self.copies = 0
    self.fresh_n = 0
    (self.root / "gitconfig").write_text(GITCONFIG)
    os.environ["GIT_CONFIG_GLOBAL"] = str(self.root / "gitconfig")
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"

    self.sub_src = self.root / "sub_src"
    self.sub_src.mkdir()
    git("init", "-q", cwd=self.sub_src)
    (self.sub_src / "s.txt").write_text("sub 1\n")
    self.commit(self.sub_src, "sub 1")
    git("clone", "-q", "--bare", str(self.sub_src), str(self.root / "sub.git"), cwd=self.root)

    self.src = self.root / "src"
    self.src.mkdir()
    git("init", "-q", cwd=self.src)
    self.write_tree("1.0", "v1 notes")
    git("submodule", "add", "-q", "../sub.git", SUB, cwd=self.src)   # relative: the same commit in every rig
    self.commit(self.src, "c1")
    self.remote = self.root / "remote.git"
    git("clone", "-q", "--bare", str(self.src), str(self.remote), cwd=self.root)
    git("remote", "add", "origin", str(self.remote), cwd=self.src)

    staging = self.root / "staging"
    staging.mkdir()
    self.staging = staging
    self.basedir, self.merged, self.finalized = self.root / "openpilot", staging / "merged", staging / "finalized"
    for d in (self.basedir, self.merged):
      git("clone", "-q", "--recurse-submodules", "-b", "sunnypilot", str(self.remote), str(d), cwd=self.root)
    (self.merged / LEFTOVER).write_text("obj\n")
    (self.merged / "build").mkdir()
    (self.merged / "build" / "top.o").write_text("obj\n")   # ignored at the top: git clean removes it

  def commit(self, repo: Path, msg: str) -> None:
    self.t += 60
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", msg, cwd=repo, GIT_AUTHOR_DATE=f"{self.t} +1100", GIT_COMMITTER_DATE=f"{self.t} +1100")

  def write_tree(self, version: str, notes: str) -> None:
    (self.src / "openpilot/sunnypilot/common").mkdir(parents=True, exist_ok=True)
    (self.src / "openpilot/sunnypilot/common/version.h").write_text(f'#define SUNNYPILOT_VERSION "{version}"\n')
    (self.src / "CHANGELOG.md").write_text(f"{notes}\n\nolder\n")
    (self.src / ".gitignore").write_text(".overlay_init\n.overlay_consistent\nbuild/\n")
    (self.src / "run.sh").write_text(f"#!/bin/sh\necho {version}\n")
    os.chmod(self.src / "run.sh", 0o755)
    if not (self.src / "notes").is_symlink():
      os.symlink("CHANGELOG.md", self.src / "notes")

  def rev(self, r: str = "HEAD") -> str:
    return git("rev-parse", r, cwd=self.src).strip()

  def push_commit(self, version: str, notes: str) -> str:
    self.write_tree(version, notes)
    self.commit(self.src, f"v{version}")
    git("push", "-q", "origin", "HEAD:refs/heads/sunnypilot", cwd=self.src)
    return self.rev()

  def push_ref(self, rev: str, branch: str) -> None:
    git("push", "-q", "-f", "origin", f"{rev}:refs/heads/{branch}", cwd=self.src)

  def fresh(self, branch: str = "sunnypilot") -> dict:
    """What a clean clone of the remote's branch checks out."""
    self.fresh_n += 1
    d = self.root / f"fresh{self.fresh_n}"
    git("clone", "-q", "--recurse-submodules", "-b", branch, str(self.remote), str(d), cwd=self.root)
    return digest(d)

  def head(self, path: Path | None = None) -> str:
    return git("rev-parse", "HEAD", cwd=path or self.finalized).strip()

  def branch(self) -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.finalized).strip()

  def consistent(self) -> bool:
    return (self.finalized / CONSISTENT_FLAG).is_file()

  def mark(self) -> None:
    (self.finalized / MARKER).write_text("this FINALIZED was not copied again\n")

  def kept(self) -> bool:
    return (self.finalized / MARKER).is_file()

  @contextlib.contextmanager
  def updater(self, reuse: bool = True, agnos=None):
    """An Updater with updated's paths on this rig; reuse=False is the old path (always finalize_update())."""
    from openpilot.system.updated import updated
    real_copytree = shutil.copytree

    def counting_copytree(src, dst, *a, **k):
      if os.path.abspath(dst) == str(self.finalized):   # copytree recurses: count the top call only
        self.copies += 1
      return real_copytree(src, dst, *a, **k)

    with contextlib.ExitStack() as stack:
      for name, value in (("OVERLAY_MERGED", str(self.merged)), ("FINALIZED", str(self.finalized)),
                          ("STAGING_ROOT", str(self.staging)), ("BASEDIR", str(self.basedir)),
                          ("AGNOS", agnos is not None),
                          ("get_build_metadata", lambda: SimpleNamespace(tested_channel=False))):
        stack.enter_context(mock.patch.object(updated, name, value))
      if agnos is not None:
        stack.enter_context(mock.patch.object(updated, "handle_agnos_update", agnos))
      if not reuse:
        stack.enter_context(mock.patch.object(updated, "reuse_finalized", lambda *a: False))
      stack.enter_context(mock.patch.object(shutil, "copytree", counting_copytree))
      u = updated.Updater()
      u.params.put("UpdaterTargetBranch", "sunnypilot", block=True)
      yield u

  def one_pass(self, u) -> dict:
    """One online pass of main()'s loop, minus init_overlay: check, fetch, params."""
    u.check_for_update()
    u.fetch_update()
    u.set_params(True, 0, None)
    return self.state(u)

  def state(self, u) -> dict:
    p = u.params
    return {
      "UpdateAvailable": p.get_bool("UpdateAvailable"),
      "UpdaterFetchAvailable": p.get_bool("UpdaterFetchAvailable"),
      "UpdaterNewDescription": p.get("UpdaterNewDescription"),
      "UpdaterNewReleaseNotes": p.get("UpdaterNewReleaseNotes"),
      "UpdaterCurrentDescription": p.get("UpdaterCurrentDescription"),
      "UpdaterState": p.get("UpdaterState"),
      "consistent": self.consistent(),
      "head": self.head(),
      "branch": self.branch(),
    }

  def finalized_matches_the_remote(self, branch: str = "sunnypilot") -> bool:
    return digest(self.finalized, skip=(CONSISTENT_FLAG, LEFTOVER)) == self.fresh(branch)


class TestReuseThroughFetchUpdate(OpenpilotTestCase):
  def setup_method(self):
    if shutil.which("git") is None:
      self.skipTest("no git")
    self.tmp = tempfile.TemporaryDirectory()
    self.rig = Rig(self.tmp.name)

  def teardown_method(self):
    self.tmp.cleanup()

  def test_the_first_pass_copies(self):
    r = self.rig
    with r.updater() as u:
      s = r.one_pass(u)
    assert r.copies == 1
    assert s["consistent"] and s["head"] == r.rev() and s["branch"] == "sunnypilot"
    assert r.finalized_matches_the_remote()
    assert (r.finalized / LEFTOVER).is_file() and not (r.finalized / "build").exists(), "what a copy takes, as before"
    assert s["UpdateAvailable"] is False and s["UpdaterNewDescription"].startswith(f"1.0 / sunnypilot / {r.rev()[:7]} / ")

  def test_nothing_new_keeps_the_finalized_copy(self):
    r = self.rig
    with r.updater() as u, mock.patch.object(fr, "cloudlog") as log:
      first = r.one_pass(u)
      r.mark()
      second = r.one_pass(u)
      third = r.one_pass(u)
    assert r.copies == 1, f"{r.copies} copies of an unchanged tree"
    assert r.kept()
    assert second == first and third == first, "the params, the flag, the commit and the branch as after a copy"
    assert r.finalized_matches_the_remote()
    lines = [c.args[0] for c in log.info.call_args_list]
    assert lines == ["finalizing in full: no consistent finalized update",
                     f"finalized update already at {r.rev()[:7]} on sunnypilot, kept it",
                     f"finalized update already at {r.rev()[:7]} on sunnypilot, kept it"], lines
    log.exception.assert_not_called()

  def test_a_new_commit_is_copied(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      r.mark()
      c2 = r.push_commit("1.1", "v2 notes")
      s = r.one_pass(u)
      assert r.copies == 2 and not r.kept()
      assert s["head"] == c2 and s["consistent"] and r.finalized_matches_the_remote()
      assert s["UpdateAvailable"] is True, "BASEDIR is on the old commit"
      assert s["UpdaterNewDescription"].startswith(f"1.1 / sunnypilot / {c2[:7]} / ")
      assert b"v2 notes" in s["UpdaterNewReleaseNotes"]
      r.mark()
      again = r.one_pass(u)
    assert r.copies == 2 and r.kept() and again == s, "and then kept while nothing is new"

  def test_a_branch_switch_at_the_same_commit_is_copied(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      r.mark()
      r.push_ref(r.rev(), "other")
      u.params.put("UpdaterTargetBranch", "other", block=True)
      s = r.one_pass(u)
    assert r.copies == 2 and not r.kept()
    assert s["branch"] == "other" and s["head"] == r.rev() and s["consistent"]
    assert s["UpdateAvailable"] is True, "BASEDIR is on another branch"
    assert r.finalized_matches_the_remote("other")

  def test_a_rollback_is_copied(self):
    r = self.rig
    c1 = r.rev()
    with r.updater() as u:
      r.one_pass(u)
      r.push_commit("1.1", "v2 notes")
      r.one_pass(u)
      r.mark()
      r.push_ref(c1, "sunnypilot")   # a force-push back
      s = r.one_pass(u)
    assert r.copies == 3 and not r.kept()
    assert s["head"] == c1 and s["consistent"] and s["UpdateAvailable"] is False
    assert r.finalized_matches_the_remote()

  def test_a_partial_copy_without_its_flag_is_copied_again(self):
    """Route 158: a drive start killed a copy half way. FINALIZED's HEAD is the fetched commit, its flag is not set."""
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      (r.finalized / CONSISTENT_FLAG).unlink()
      (r.finalized / "CHANGELOG.md").unlink()
      shutil.rmtree(r.finalized / "openpilot")
      assert r.head() == r.rev()
      s = r.one_pass(u)
    assert r.copies == 2 and s["consistent"] and r.finalized_matches_the_remote()

  def test_a_broken_finalized_git_is_copied(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      (r.finalized / ".git" / "HEAD").write_text("garbage\n")
      s = r.one_pass(u)
    assert r.copies == 2 and s["consistent"] and s["head"] == r.rev() and r.finalized_matches_the_remote()

  def test_a_finalized_without_git_inside_another_repo_is_copied(self):
    """git would answer for the repo above it: the commit must be FINALIZED's own."""
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      shutil.copytree(r.finalized / ".git", r.staging / ".git", symlinks=True)   # same commit, one folder up
      shutil.rmtree(r.finalized / ".git")
      s = r.one_pass(u)
    assert r.copies == 2 and s["consistent"] and (r.finalized / ".git").is_dir() and r.finalized_matches_the_remote()

  def test_a_failure_while_keeping_it_copies(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      (r.finalized / ".git" / "index.lock").write_text("")   # git reset --hard fails on it
      s = r.one_pass(u)
    assert r.copies == 2 and s["consistent"] and not (r.finalized / ".git" / "index.lock").exists()
    assert r.finalized_matches_the_remote()

  def test_an_edited_or_deleted_tracked_file_is_restored(self):
    """finalize_update()'s two resets run on a kept FINALIZED too."""
    r = self.rig
    with r.updater() as u:
      first = r.one_pass(u)
      r.mark()
      (r.finalized / "CHANGELOG.md").write_text("edited by hand, longer than before\n")
      (r.finalized / "run.sh").unlink()
      (r.finalized / SUB / "s.txt").write_text("edited inside the submodule\n")
      s = r.one_pass(u)
    assert r.copies == 1 and r.kept()
    assert s == first and r.finalized_matches_the_remote()

  def test_a_moved_submodule_is_copied(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      (r.finalized / SUB / "s.txt").write_text("a commit of its own\n")
      r.commit(r.finalized / SUB, "moved")
      s = r.one_pass(u)
    assert r.copies == 2 and s["consistent"] and r.finalized_matches_the_remote()

  def test_an_untracked_file_is_copied_away(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      (r.finalized / "stray.txt").write_text("not in the tree\n")
      r.one_pass(u)
    assert r.copies == 2 and not (r.finalized / "stray.txt").exists()

  def test_a_changed_git_config_is_copied(self):
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      git("config", "remote.origin.url", "/somewhere/else.git", cwd=r.finalized)
      r.one_pass(u)
    assert r.copies == 2
    assert (r.finalized / ".git/config").read_bytes() == (r.merged / ".git/config").read_bytes()

  def test_a_failed_fetch_leaves_no_flag_and_then_copies(self):
    """As before: the flag is cleared before the fetch, so a pass that fails installs nothing at the next boot."""
    r = self.rig
    with r.updater() as u:
      r.one_pass(u)
      r.mark()
      os.rename(r.remote, r.root / "away.git")
      with self.assertRaises(subprocess.CalledProcessError):
        u.fetch_update()
      assert not r.consistent() and r.copies == 1 and r.kept()
      os.rename(r.root / "away.git", r.remote)
      s = r.one_pass(u)
    assert r.copies == 2 and s["consistent"] and not r.kept()

  def test_agnos_clearing_the_flag_still_ends_flagged(self):
    """handle_agnos_update() clears the flag when the AGNOS versions differ; the decision is from before that, and a
    kept FINALIZED is flagged again after it, as finalize_update() does."""
    from openpilot.system.updated import updated
    calls = []

    def fake_agnos(progress_cb=None):
      calls.append(r.consistent())
      updated.set_consistent_flag(False)

    r = self.rig
    with r.updater(agnos=fake_agnos) as u:
      r.one_pass(u)
      r.mark()
      s = r.one_pass(u)
    assert calls == [False, False], "the flag was already clear when AGNOS ran, as before"
    assert r.copies == 1 and r.kept() and s["consistent"]

  def test_an_agnos_failure_finalizes_nothing(self):
    r = self.rig

    def failing_agnos(progress_cb=None):
      raise subprocess.CalledProcessError(1, ["flash"])

    with r.updater() as u:
      r.one_pass(u)
    r.mark()
    with r.updater(agnos=failing_agnos) as u, self.assertRaises(subprocess.CalledProcessError):
      u.fetch_update()
    assert not r.consistent() and r.copies == 1 and r.kept()

  def test_the_same_as_the_old_path(self):
    """Every pass of a scenario on two rigs, one kept as now and one always copied as before: the same params, flag,
    commit and branch, the same files (the submodule's build product included), refs and .git/config."""
    with tempfile.TemporaryDirectory() as other:
      new, old = self.rig, Rig(other)

      def snapshot(rig: Rig, s: dict) -> tuple:
        config = (rig.finalized / ".git/config").read_text().replace(str(rig.root), "<root>")
        refs = git("for-each-ref", "--format=%(refname) %(objectname)", cwd=rig.finalized)
        return s, digest(rig.finalized), config, refs

      steps = [
        lambda rig, u: None,
        lambda rig, u: None,
        lambda rig, u: rig.push_commit("1.1", "v2 notes"),
        lambda rig, u: None,
        lambda rig, u: (rig.push_ref(rig.rev(), "other"), u.params.put("UpdaterTargetBranch", "other", block=True)),
        lambda rig, u: (u.params.put("UpdaterTargetBranch", "sunnypilot", block=True), rig.push_ref(rig.rev("HEAD~1"), "sunnypilot")),
        lambda rig, u: None,
      ]

      def scenario(rig: Rig, reuse: bool) -> list:
        # one rig at a time: updater() patches updated's module constants
        out = []
        with rig.updater(reuse=reuse) as u:
          for step in steps:
            step(rig, u)
            out.append(snapshot(rig, rig.one_pass(u)))
        return out

      a, b = scenario(new, True), scenario(old, False)
      for i, (x, y) in enumerate(zip(a, b, strict=True)):
        assert x == y, f"pass {i + 1}:\n{x}\n{y}"
      assert old.copies == 7 and new.copies == 4, (old.copies, new.copies)


class FakeGit:
  """run(cmd, cwd) from a table of (first args, cwd) -> output; records every call."""

  def __init__(self, table: dict, default: str | None = None):
    self.table, self.default, self.calls = table, default, []

  def __call__(self, cmd, cwd=None):
    self.calls.append((tuple(cmd), cwd))
    for (prefix, where), out in self.table.items():
      if tuple(cmd[:len(prefix)]) == prefix and where in (None, cwd):
        if isinstance(out, BaseException):
          raise out
        return out
    if self.default is None:
      raise subprocess.CalledProcessError(128, cmd, output="fatal: not in the table")
    return self.default


SHA1, SHA2 = "1" * 40, "2" * 40


def fake_repo(commit=SHA1, branch="sunnypilot", cdup="\n", subs="sub x y\n", where=None) -> dict:
  return {
    (("git", "rev-parse", "--show-cdup"), where): cdup,
    (("git", "rev-parse", "HEAD"), where): commit + "\n",
    (("git", "rev-parse", "--abbrev-ref", "HEAD"), where): branch + "\n",
    (("git", "submodule", "--quiet", "foreach"), where): subs,
  }


class TestDecision(OpenpilotTestCase):
  def setup_method(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.merged, self.finalized = os.path.join(self.tmp.name, "merged"), os.path.join(self.tmp.name, "finalized")
    for d in (self.merged, self.finalized):
      os.makedirs(os.path.join(d, ".git"))
      Path(d, ".git", "config").write_text("[core]\n\tcheckStat = minimal\n")
    self.flags: list[bool] = []

  def teardown_method(self):
    self.tmp.cleanup()

  def run_ok(self, merged: dict | None = None, finalized: dict | None = None, extra: dict | None = None) -> FakeGit:
    """A merged and a FINALIZED at SHA1 on sunnypilot, alike in everything; extra's entries are matched first."""
    table = dict(extra or {})
    base = {**fake_repo(where=self.merged, **(merged or {})), **fake_repo(where=self.finalized, **(finalized or {})),
            (("git", "reset", "--hard"), self.finalized): "",
            (("git", "submodule", "foreach", "--recursive", "git", "reset", "--hard"), self.finalized): "",
            (("git", "status", "--porcelain", "--ignore-submodules=all"), self.finalized): ""}
    for k, v in base.items():
      table.setdefault(k, v)
    return FakeGit(table)

  def reuse(self, before, run) -> bool:
    return reuse_finalized(before, self.merged, self.finalized, run, self.flags.append)

  def test_same_commit_branch_and_flag_keeps_it(self):
    run = self.run_ok()
    assert self.reuse(TreeId(SHA1, "sunnypilot"), run) is True
    assert self.flags == [True]
    assert (("git", "reset", "--hard"), self.finalized) in run.calls

  def test_anything_else_finalizes_and_touches_nothing(self):
    before = TreeId(SHA1, "sunnypilot")
    cases = {
      "not consistent": (None, self.run_ok()),
      "another commit": (before, self.run_ok(merged={"commit": SHA2})),
      "a rollback": (TreeId(SHA2, "sunnypilot"), self.run_ok()),
      "another branch": (before, self.run_ok(merged={"branch": "other"})),
      "the overlay detached": (before, self.run_ok(merged={"branch": "HEAD"})),
      "the overlay unreadable": (before, FakeGit({})),
    }
    for name, (b, run) in cases.items():
      self.flags.clear()
      assert self.reuse(b, run) is False, name
      assert self.flags == [], name
      assert not any(c[0][:2] == ("git", "reset") for c in run.calls), f"{name}: FINALIZED reset before deciding"

  def test_finalized_not_what_a_copy_would_be_finalizes(self):
    before = TreeId(SHA1, "sunnypilot")
    cases = {
      "its commit after the resets": self.run_ok(finalized={"commit": SHA2}),
      "its branch after the resets": self.run_ok(finalized={"branch": "other"}),
      "its submodules": self.run_ok(finalized={"subs": "sub x z\n"}),
      "git status": self.run_ok(extra={(("git", "status"), self.finalized): "?? stray\n"}),
      "a reset fails": self.run_ok(extra={(("git", "reset", "--hard"), self.finalized): subprocess.CalledProcessError(128, "reset")}),
      "git missing": self.run_ok(extra={(("git", "status"), self.finalized): FileNotFoundError("git")}),
    }
    for name, run in cases.items():
      self.flags.clear()
      assert self.reuse(before, run) is False, name
      assert self.flags == [], name

  def test_a_different_git_config_finalizes(self):
    Path(self.finalized, ".git", "config").write_text("[remote \"origin\"]\n\turl = /else\n")
    assert self.reuse(TreeId(SHA1, "sunnypilot"), self.run_ok()) is False and self.flags == []
    shutil.rmtree(os.path.join(self.finalized, ".git"))
    assert self.reuse(TreeId(SHA1, "sunnypilot"), self.run_ok()) is False and self.flags == []

  def test_a_flag_that_cannot_be_set_finalizes(self):
    def failing(consistent):
      raise OSError("read-only")
    assert reuse_finalized(TreeId(SHA1, "sunnypilot"), self.merged, self.finalized, self.run_ok(), failing) is False

  def test_a_keyboard_interrupt_is_not_swallowed(self):
    """manager stops updated with SIGINT at onroad: it must die with the flag unset, not carry on."""
    run = self.run_ok(extra={(("git", "status"), self.finalized): KeyboardInterrupt()})
    with self.assertRaises(KeyboardInterrupt):
      self.reuse(TreeId(SHA1, "sunnypilot"), run)
    assert self.flags == []
    Path(self.finalized, CONSISTENT_FLAG).touch()
    with self.assertRaises(KeyboardInterrupt):
      finalized_id(self.finalized, FakeGit({(("git",), None): KeyboardInterrupt()}))

  def test_tree_id_wants_a_full_sha_a_branch_and_the_repo_top(self):
    def tid(**kw):
      return tree_id("/x", FakeGit(fake_repo(**kw)))
    assert tid() == TreeId(SHA1, "sunnypilot")
    assert tid(commit="abcdef0123" * 4) == TreeId("abcdef0123" * 4, "sunnypilot")
    for bad in ({"commit": ""}, {"commit": SHA1[:39]}, {"commit": "ABCDEF0123" * 4}, {"commit": SHA1 + "0"},
                {"commit": "HEAD"}, {"branch": ""}, {"branch": "HEAD"}, {"cdup": "../\n"}):
      assert tid(**bad) is None, bad

  def test_tree_id_of_a_real_repo(self):
    if shutil.which("git") is None:
      self.skipTest("no git")
    env = {"GIT_CONFIG_GLOBAL": os.path.join(self.tmp.name, "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
    Path(env["GIT_CONFIG_GLOBAL"]).write_text(GITCONFIG)
    os.environ.update(env)
    d = os.path.join(self.tmp.name, "real")
    git("init", "-q", d, cwd=self.tmp.name)
    Path(d, "f").write_text("x\n")
    git("add", "f", cwd=d)
    git("commit", "-qm", "c", cwd=d)
    from openpilot.system.updated.updated import run
    sha = git("rev-parse", "HEAD", cwd=d).strip()
    assert tree_id(d, run) == TreeId(sha, "sunnypilot")
    os.makedirs(os.path.join(d, "inner"))
    assert tree_id(os.path.join(d, "inner"), run) is None, "a folder inside the repo is not the repo"
    git("checkout", "-q", "--detach", cwd=d)
    assert tree_id(d, run) is None
    assert str(TreeId(sha, "sunnypilot")) == f"{sha[:7]} on sunnypilot"

  def test_finalized_id_reads_nothing_without_the_flag(self):
    run = FakeGit(fake_repo())
    assert finalized_id(self.finalized, run) is None and run.calls == []
    assert finalized_id(os.path.join(self.tmp.name, "missing"), run) is None and run.calls == []
    Path(self.finalized, CONSISTENT_FLAG).touch()
    assert finalized_id(self.finalized, run) == TreeId(SHA1, "sunnypilot")

  def test_finalized_id_never_raises(self):
    Path(self.finalized, CONSISTENT_FLAG).touch()
    for err in (subprocess.CalledProcessError(128, "git"), FileNotFoundError("git"), OSError("EIO"), UnicodeDecodeError("utf-8", b"", 0, 1, "x")):
      assert finalized_id(self.finalized, FakeGit({(("git",), None): err})) is None, err

  def test_the_progress_tests_empty_git_never_matches(self):
    """test_download_progress's fetch_update test answers "" to every git command."""
    def empty(cmd, cwd=None):
      return ""
    Path(self.finalized, CONSISTENT_FLAG).touch()
    assert finalized_id(self.finalized, empty) is None
    assert self.reuse(TreeId(SHA1, "sunnypilot"), empty) is False and self.flags == []

  def test_never_imports_updated(self):
    """updated.py imports this module; importing updated back would be circular."""
    src = Path(fr.__file__).read_text(encoding="utf-8")
    assert "system.updated.updated" not in src and "import updated" not in src


class TestWiring(OpenpilotTestCase):
  def test_fetch_update_records_before_the_clear_and_decides_after_the_state(self):
    src = UPDATED.read_text(encoding="utf-8")
    body = src[src.index("  def fetch_update(self)"):src.index("\ndef main()")]
    i_record = body.index("finalized_before = finalized_id(FINALIZED, run)")
    i_clear = body.index("set_consistent_flag(False)")
    i_state = body.index('self.params.put("UpdaterState", "finalizing update...", block=True)')
    i_decide = body.index("if not reuse_finalized(finalized_before, OVERLAY_MERGED, FINALIZED, run, set_consistent_flag):")
    i_finalize = body.index("      finalize_update()\n")
    i_success = body.index('cloudlog.info("finalize success!")')
    assert i_record < i_clear < i_state < i_decide < i_finalize < i_success
    assert body.count("set_consistent_flag(False)") == 1, "the flag is cleared during the fetch, as before"
    for i in (i_record, i_decide):
      assert body[:i].rstrip().splitlines()[-1].lstrip().startswith("# FORK(UPDATER): "), "marked like the fork's other lines"

  def test_main_still_clears_the_flag_at_every_start(self):
    """What makes a set flag mean "finalized by this process": without it a FINALIZED from before a restart could be
    kept."""
    src = UPDATED.read_text(encoding="utf-8")
    main = src[src.index("\ndef main()"):]
    assert re.search(r"# invalidate old finalized update\n\s+set_consistent_flag\(False\)\n", main)
    assert main.index("set_consistent_flag(False)") < main.index("while True:")
    init = src[src.index("\ndef init_overlay()"):src.index("\ndef finalize_update()")]
    assert init.index("set_consistent_flag(False)") < init.index('run(["sudo", "rm", "-rf", STAGING_ROOT])')

  def test_finalize_update_is_upstreams(self):
    src = UPDATED.read_text(encoding="utf-8")
    fin = src[src.index("\ndef finalize_update()"):src.index("\ndef handle_agnos_update(")]
    for line in ("set_consistent_flag(False)", "shutil.rmtree(FINALIZED)", "shutil.copytree(OVERLAY_MERGED, FINALIZED, symlinks=True)",
                 'run(["git", "reset", "--hard"], FINALIZED)',
                 'run(["git", "submodule", "foreach", "--recursive", "git", "reset", "--hard"], FINALIZED)',
                 "set_consistent_flag(True)"):
      assert line in fin, line
    reuse_src = Path(fr.__file__).read_text(encoding="utf-8")
    assert 'run(["git", "reset", "--hard"], finalized)' in reuse_src
    assert 'run(["git", "submodule", "foreach", "--recursive", "git", "reset", "--hard"], finalized)' in reuse_src

  def test_the_flag_is_the_one_the_boot_swap_reads(self):
    src = UPDATED.read_text(encoding="utf-8")
    assert f'consistent_file = Path(os.path.join(FINALIZED, "{CONSISTENT_FLAG}"))' in src
    assert f'"${{STAGING_ROOT}}/finalized/{CONSISTENT_FLAG}"' in LAUNCH.read_text(encoding="utf-8")
