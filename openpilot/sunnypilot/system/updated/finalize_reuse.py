"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(UPDATER): no fresh copy of a finalized update that already holds the commit and branch just fetched.

Every fetch_update() ends in finalize_update(): rmtree(FINALIZED), then copytree of the whole overlay - about
2.5 GB with its .git - even when the fetch brought nothing new. On unmetered Wi-Fi updated fetches every 1.5 h, so a
night parked at home was about ten identical copies and about 58 GB of writes, discards included
(docs/fork/STORAGE.md section 7).

FINALIZED is kept instead of copied only when all of these hold; anything else, an error included, is
finalize_update() exactly as before:

* its consistent flag was set when this pass began (finalized_id(), called before fetch_update() clears the flag).
  main() clears the flag every time updated starts and a recreated overlay deletes FINALIZED, so a set flag means a
  finalize of this same process, from this same overlay; nothing else in openpilot writes FINALIZED;
* its commit (40 hex digits) and branch (a name, not a detached "HEAD") are those the overlay holds after this
  pass's checkout - FETCH_HEAD, exactly what the copy would take;
* after finalize_update()'s own two resets, run on it as after a copy: the same commit and branch again, every
  submodule at the overlay's HEAD, the same .git/config byte for byte, and `git status` clean outside the submodules,
  untracked and ignored files included.

What git cannot see from the stat data is trusted, as nothing but updated writes FINALIZED: a same-size edit with its
mtime put back, an object deleted inside .git. The copy never checked those in its source either.

Then the flag is set again, as finalize_update() would. Not refreshed is what nothing reads: .git's reflogs and
FETCH_HEAD, and the ignored files inside submodule folders (build products), as copied from BASEDIR by the first copy
- BASEDIR's change only at a boot, and a boot deletes FINALIZED. UpdateAvailable, UpdaterNewDescription, the release
notes and the boot swap (launch_chffrplus.sh) are the same either way.

The first pass after each updated start still copies (main() cleared the flag), and so does the pass after a failed
one (the overlay is recreated): about one copy per parking online instead of one per pass.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from openpilot.common.swaglog import cloudlog

CONSISTENT_FLAG = ".overlay_consistent"  # updated.py's set_consistent_flag()
SHA = re.compile(r"[0-9a-f]{40}")

Run = Callable[[list[str], str], str]  # updated.py's run(cmd, cwd)


@dataclass(frozen=True)
class TreeId:
  commit: str
  branch: str

  def __str__(self) -> str:
    return f"{self.commit[:7]} on {self.branch}"


def tree_id(path: str, run: Run) -> TreeId | None:
  """The commit and branch checked out at the top of the repo at path. None unless both are certain and path is
  that repo's top (git would otherwise answer for a repo above it). Raises what run() raises."""
  if run(["git", "rev-parse", "--show-cdup"], path).strip() != "":
    return None
  commit = run(["git", "rev-parse", "HEAD"], path).strip()
  branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], path).strip()
  if SHA.fullmatch(commit) is None or not branch or branch == "HEAD":
    return None
  return TreeId(commit, branch)


def finalized_id(finalized: str, run: Run) -> TreeId | None:
  """FINALIZED's commit and branch if its consistent flag is set, else None. Never raises an Exception."""
  try:
    if not Path(finalized, CONSISTENT_FLAG).is_file():
      return None
    return tree_id(finalized, run)
  except Exception:
    cloudlog.exception("updated: could not read the finalized update")
    return None


def submodule_heads(path: str, run: Run) -> str:
  return run(["git", "submodule", "--quiet", "foreach", "--recursive", 'echo "$displaypath $sha1 $(git rev-parse HEAD)"'], path)


def difference(merged: str, finalized: str, fetched: TreeId, run: Run) -> str:
  """What a fresh copy of merged would hold and FINALIZED does not, or "" if nothing that is read later."""
  if tree_id(finalized, run) != fetched:
    return "commit or branch"
  if submodule_heads(finalized, run) != submodule_heads(merged, run):
    return "submodules"
  if Path(finalized, ".git", "config").read_bytes() != Path(merged, ".git", "config").read_bytes():
    return ".git/config"
  # ignored files too: the overlay's git clean -xdff removed every one at the top, so a copy holds none there;
  # untracked ones whatever status.showUntrackedFiles says
  status = ["git", "status", "--porcelain", "--untracked-files=normal", "--ignored", "--ignore-submodules=all"]
  if run(status, finalized).strip():
    return "git status"
  return ""


def reuse_finalized(before: TreeId | None, merged: str, finalized: str, run: Run,
                    set_consistent_flag: Callable[[bool], None]) -> bool:
  """Ready FINALIZED to be swapped in again without copying it, if it already is what this pass would copy.

  before is finalized_id() from the start of the pass. True: kept, with its consistent flag set again, as
  finalize_update() leaves it. False: the caller runs finalize_update(). Never raises an Exception; a
  KeyboardInterrupt (manager's SIGINT at onroad) leaves the flag unset, as it does in a copy.
  """
  if before is None:
    cloudlog.info("finalizing in full: no consistent finalized update")
    return False
  try:
    fetched = tree_id(merged, run)
    if fetched is None or fetched != before:
      cloudlog.info(f"finalizing in full: finalized update at {before}, fetched {fetched}")
      return False
    # finalize_update()'s own steps after its copy
    run(["git", "reset", "--hard"], finalized)
    run(["git", "submodule", "foreach", "--recursive", "git", "reset", "--hard"], finalized)
    why = difference(merged, finalized, fetched, run)
    if why:
      cloudlog.info(f"finalizing in full: finalized update at {fetched} differs in {why}")
      return False
    set_consistent_flag(True)
    cloudlog.info(f"finalized update already at {fetched}, kept it")
    return True
  except Exception:
    cloudlog.exception("updated: could not reuse the finalized update, finalizing in full")
    return False
