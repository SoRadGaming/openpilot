"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

FORK(UPDATER): the download progress updated writes for the comma 4's software page
(download_progress.py), the AGNOS progress threaded through agnos.py, and the wiring in updated.py.
"""
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.system.updated import download_progress as dp
from openpilot.sunnypilot.system.updated.download_progress import DownloadProgress, git_fetch_with_progress, \
  download_label, PARAM, PHASE_CODE, PHASE_CHECKOUT, PHASE_OS, WRITE_INTERVAL

ROOT = Path(__file__).parents[4]   # openpilot/
UPDATED = ROOT / "system/updated/updated.py"
AGNOS = ROOT / "common/hardware/comma/agnos.py"
PARAMS_KEYS = ROOT / "common/params_keys.h"


class FakeParams:
  def __init__(self):
    self.values: dict = {}
    self.writes: list = []
    self.blocking: list = []

  def put(self, key, value, block=False):
    self.values[key] = value
    self.writes.append((key, value))
    self.blocking.append(block)

  def remove(self, key):
    self.values.pop(key, None)


class Clock:
  def __init__(self):
    self.t = 100.0

  def __call__(self):
    return self.t


class TestDownloadProgress(OpenpilotTestCase):
  def setup_method(self):
    self.params, self.clock = FakeParams(), Clock()
    self.p = DownloadProgress(self.params, clock=self.clock)

  def test_a_phase_change_is_written_at_once(self):
    self.p.set(PHASE_CODE)
    self.p.set(PHASE_CODE, 40)          # same phase, same instant: throttled
    self.p.set(PHASE_CHECKOUT)          # new phase: written
    assert self.params.writes == [(PARAM, {"phase": "code", "pct": None}), (PARAM, {"phase": "checkout", "pct": None})]

  def test_one_write_per_interval_within_a_phase(self):
    self.p.set(PHASE_OS, 0)
    for i in range(1, 2001):            # one call per 1 MB chunk, 10 ms apart
      self.clock.t += 0.01
      self.p.set(PHASE_OS, i / 20)
    n = len(self.params.writes)
    expected = 20 / WRITE_INTERVAL
    assert expected - 1 <= n <= expected + 2, f"{n} writes in 20 s"
    assert self.params.values[PARAM]["pct"] >= 95

  def test_a_phases_100_is_never_throttled(self):
    # "Receiving objects: 100%" lands well inside a second of the last write; dropping it froze the label
    # at "code 85%" through all of git's "Resolving deltas"
    self.p.set(PHASE_CODE, 85)
    self.clock.t += 0.2
    self.p.set(PHASE_CODE, 99)          # throttled
    self.clock.t += 0.1
    self.p.set(PHASE_CODE, 100)         # written
    assert self.params.writes == [(PARAM, {"phase": "code", "pct": 85}), (PARAM, {"phase": "code", "pct": 100})]
    self.clock.t += 0.1
    self.p.set(PHASE_CODE, 100)         # but only once
    assert len(self.params.writes) == 2

  def test_writes_block(self):
    # a non-blocking put is queued to a thread and lands after a remove() straight after it
    self.p.set(PHASE_CODE)
    self.p.set(PHASE_OS, 7)
    assert self.params.blocking == [True, True]

  def test_an_unchanged_value_is_not_rewritten(self):
    self.p.set(PHASE_OS, 5)
    self.clock.t += 10
    self.p.set(PHASE_OS, 5.9)           # still 5 %
    assert len(self.params.writes) == 1

  def test_clamped_to_0_100(self):
    self.p.set(PHASE_OS, 140)
    assert self.params.values[PARAM]["pct"] == 100
    self.p.set(PHASE_CODE, -3)
    assert self.params.values[PARAM]["pct"] == 0

  def test_clear_removes_it_and_the_next_write_is_immediate(self):
    self.p.set(PHASE_OS, 50)
    self.p.clear()
    assert PARAM not in self.params.values
    self.p.set(PHASE_OS, 50)
    assert self.params.values[PARAM] == {"phase": "os", "pct": 50}

  def test_a_failing_write_never_fails_the_update(self):
    params = mock.MagicMock()
    params.put.side_effect = RuntimeError("UnknownKeyName")
    params.remove.side_effect = RuntimeError("UnknownKeyName")
    p = DownloadProgress(params, clock=self.clock)
    p.set(PHASE_CODE, 3)
    p.clear()


class TestLabel(OpenpilotTestCase):
  def test_labels(self):
    d = "downloading..."
    assert download_label(d, {"phase": "code", "pct": 37}) == "downloading...\ncode 37%"
    assert download_label(d, {"phase": "code", "pct": None}) == "downloading...\ncode"
    assert download_label(d, {"phase": "checkout", "pct": None}) == "downloading...\nchecking out"
    assert download_label(d, {"phase": "os", "pct": 0}) == "downloading...\nos update 0%"
    assert download_label(d, {"phase": "os", "pct": 100}) == "downloading...\nos update 100%"

  def test_anything_malformed_is_the_plain_state(self):
    d = "downloading..."
    for bad in (None, "x", 5, [], {}, {"phase": "nope", "pct": 5}, {"pct": 5}):
      assert download_label(d, bad) == d, bad
    for pct in (101, -1, True, 4.5, "7"):
      assert download_label(d, {"phase": "os", "pct": pct}) == "downloading...\nos update", pct

  def test_other_states_are_untouched(self):
    for s in ("idle", "checking...", "finalizing update...", ""):
      assert download_label(s, {"phase": "os", "pct": 50}) == s

  def test_never_the_download_button_text(self):
    # the page sends SIGHUP (download) only when the value reads exactly "download update"
    for phase in dp.PHASE_LABELS:
      for pct in (None, 0, 50, 100):
        assert download_label("downloading...", {"phase": phase, "pct": pct}) != "download update"

  def test_fits_the_button(self):
    """BigButton's sub-label is 36 px Inter-Regular (ROMAN) in 402 - 2 * 40 = 322 px."""
    try:
      from PIL import ImageFont
      font = ImageFont.truetype(str(ROOT / "selfdrive/assets/fonts/Inter-Regular.ttf"), 36)
    except Exception as e:  # no Pillow, or the font is an LFS pointer in this checkout
      self.skipTest(f"cannot load the UI font: {e}")
    for phase in dp.PHASE_LABELS:
      for line in download_label("downloading...", {"phase": phase, "pct": 100}).split("\n"):
        assert font.getlength(line) <= 322, f"{line!r} is {font.getlength(line):.0f} px"


FAKE_GIT = r'''
import sys
w = sys.stdout.write
w("remote: Enumerating objects: 9, done.\n")
for i in (0, 50, 100):
  w(f"remote: Counting objects: {i:3d}% ({i}/100)\r")
w("remote: Counting objects: 100% (100/100), done.\n")
for i in (0, 12, 37, 99):
  w(f"Receiving objects: {i:3d}% ({i}/100), 1.20 MiB | 2.00 MiB/s\r")
w("Receiving objects: 100% (100/100), 2.50 MiB | 2.00 MiB/s, done.\n")
for i in (0, 60):
  w(f"Resolving deltas: {i:3d}% ({i}/100)\r")
w("Resolving deltas: 100% (100/100), done.\n")
w("From https://github.com/SoRadGaming/openpilot\n * branch            sunnypilot -> FETCH_HEAD\n")
sys.stdout.flush()
sys.exit(int(sys.argv[1]))
'''


class TestGitFetch(OpenpilotTestCase):
  def test_meter_and_log(self):
    pcts: list[int] = []
    out = git_fetch_with_progress([sys.executable, "-c", FAKE_GIT, "0"], "/", pcts.append)
    assert pcts == [0, 12, 37, 99, 100], "only Receiving objects is the transfer"
    assert "From https://github.com/SoRadGaming/openpilot" in out and "FETCH_HEAD" in out
    assert "Receiving objects: 100% (100/100), 2.50 MiB | 2.00 MiB/s, done." in out
    assert "Counting objects: 100% (100/100), done." in out
    assert " 37% " not in out and " 60% " not in out, "the meter's redraws do not go to cloudlog"

  def test_a_small_fetch_unpacks(self):
    script = r'import sys; sys.stdout.write("Unpacking objects:  40% (2/5)\rUnpacking objects: 100% (5/5), 1 KiB, done.\n")'
    pcts: list[int] = []
    out = git_fetch_with_progress([sys.executable, "-c", script], "/", pcts.append)
    assert pcts == [40, 100] and out == "Unpacking objects: 100% (5/5), 1 KiB, done.\n"

  def test_a_failure_raises_like_run(self):
    with self.assertRaises(subprocess.CalledProcessError) as e:
      git_fetch_with_progress([sys.executable, "-c", FAKE_GIT, "128"], "/", lambda p: None)
    assert e.exception.returncode == 128 and "FETCH_HEAD" in e.exception.output

  def test_a_real_git_fetch(self):
    if shutil.which("git") is None:
      self.skipTest("no git")
    with tempfile.TemporaryDirectory() as d:
      env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t"}

      def git(*a, cwd=d):
        subprocess.check_output(["git", *a], cwd=cwd, env=env, stderr=subprocess.STDOUT)

      src, dst = os.path.join(d, "src"), os.path.join(d, "dst")
      git("init", "-q", "-b", "sunnypilot", src)
      for i in range(60):   # 180 objects, over fetch.unpackLimit, so index-pack draws "Receiving objects"
        Path(src, f"f{i}").write_bytes(os.urandom(4096))
        git("add", ".", cwd=src)
        git("commit", "-q", "-m", str(i), cwd=src)
      git("init", "-q", dst)
      git("remote", "add", "origin", "file://" + src, cwd=dst)
      pcts: list[int] = []
      out = git_fetch_with_progress(["git", "fetch", "--progress", "origin", "sunnypilot"], dst, pcts.append)
      assert pcts and pcts[-1] == 100 and pcts == sorted(pcts), pcts
      assert "FETCH_HEAD" in out
      assert len(out.splitlines()) < 15, out


class TestAgnosProgress(OpenpilotTestCase):
  """agnos.py only threads a callback through; the weighting is by partition size."""

  def test_weighted_by_partition_size(self):
    from openpilot.common.hardware.comma import agnos
    manifest = [{"name": "small", "size": 10}, {"name": "flashed", "size": 30}, {"name": "system", "size": 960}]

    def fake_flash(slot, partition, cloudlog, standalone=False, progress_cb=None):
      assert progress_cb is not None
      for f in ((1.0,) if partition["name"] == "flashed" else (0.0, 0.5, 1.0)):
        progress_cb(f)

    seen: list[float] = []
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
      json.dump(manifest, f)
      f.flush()
      with mock.patch.object(agnos, "flash_partition", fake_flash), mock.patch.object(agnos.subprocess, "run"):
        agnos.flash_agnos_update(f.name, 0, mock.MagicMock(), progress_cb=seen.append)
    assert [round(x, 9) for x in seen] == [0.0, 0.005, 0.01, 0.04, 0.04, 0.52, 1.0]
    assert seen == sorted(seen)

  def test_no_callback_is_the_upstream_call(self):
    from openpilot.common.hardware.comma import agnos
    calls = []
    with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
      json.dump([{"name": "boot", "size": 1}], f)
      f.flush()
      with mock.patch.object(agnos, "flash_partition", lambda *a, **k: calls.append(k)), \
           mock.patch.object(agnos.subprocess, "run"):
        agnos.flash_agnos_update(f.name, 0, mock.MagicMock())
    assert len(calls) == 1

  def _extract(self, data: bytes, content_length: bool):
    from openpilot.common.hardware.comma import agnos
    import hashlib
    comp = lzma.compress(data)
    chunks = [comp[i:i + 1000] for i in range(0, len(comp), 1000)]
    resp = mock.MagicMock()
    resp.headers = {"Content-Length": str(len(comp))} if content_length else {}
    resp.iter_content.return_value = iter(chunks)
    seen: list[float] = []
    with tempfile.TemporaryDirectory() as d:
      path = os.path.join(d, "part")
      partition = {"name": "boot", "url": "http://x", "size": len(data), "sparse": False,
                   "hash": hashlib.sha256(data).hexdigest(), "hash_raw": hashlib.sha256(data).hexdigest()}
      with mock.patch.object(agnos.requests, "get", return_value=resp), \
           mock.patch.object(agnos, "get_partition_path", return_value=path):
        agnos.extract_compressed_image(0, partition, mock.MagicMock(), progress_cb=seen.append)
      assert Path(path).read_bytes() == data
    return seen, len(chunks)

  def test_extract_reports_compressed_bytes_received(self):
    seen, n_chunks = self._extract(os.urandom(300_000), content_length=True)
    assert len(seen) == n_chunks, "one report per chunk received, not per chunk written"
    assert seen[-1] == 1.0 and seen == sorted(seen)

  def test_extract_without_content_length_reports_bytes_written(self):
    seen, _ = self._extract(os.urandom(3_000_000), content_length=False)
    assert seen and seen == sorted(seen) and seen[-1] == 1.0


class TestWiring(OpenpilotTestCase):
  def test_param_is_registered(self):
    src = PARAMS_KEYS.read_text(encoding="utf-8")
    assert re.search(r'\{"UpdaterDownloadProgress", \{CLEAR_ON_MANAGER_START, JSON\}\}', src)

  def test_real_params_round_trip(self):
    """Through the built Params (OpenpilotTestCase runs each test under its own params prefix)."""
    from openpilot.common.params import Params
    params = Params()
    assert params.get_type(PARAM) == params.get_type("LiveParameters"), "registered as JSON in the built params"
    p = DownloadProgress(params)
    p.set(PHASE_OS, 42.7)
    params_value = Params().get(PARAM)   # the write blocks, so it is there at once
    assert params_value == {"phase": "os", "pct": 42}
    assert download_label("downloading...", params_value) == "downloading...\nos update 42%"
    p.clear()
    assert Params().get(PARAM) is None

  def test_real_params_clear_straight_after_set(self):
    """The "every partition already flashed" path: progress_cb(1.0), then clear() within a millisecond.
    With a non-blocking put the queued write landed after the remove, every time."""
    from openpilot.common.params import Params
    for i in range(50):
      p = DownloadProgress(Params())
      p.set(PHASE_OS, 99)
      p.set(PHASE_OS, 100)
      p.clear()
      time.sleep(0.002)   # long enough for a queued write to land
      assert Params().get(PARAM) is None, f"the param outlived clear() on run {i}"

  def test_fetch_update_through_real_params(self):
    """Updater.fetch_update() itself, with git, AGNOS and finalize patched out and the built Params under
    the test prefix: the phases in order, a phase's 100% kept, and the param gone before finalizing."""
    from openpilot.common.params import Params
    from openpilot.system.updated import updated
    seen: list = []

    def snap(where):
      seen.append((where, Params().get(PARAM)))

    def fake_run(cmd, cwd=None):
      if cmd[:2] == ["git", "checkout"]:
        snap("checkout")
      return ""

    def fake_fetch(cmd, cwd, on_percent):
      assert cmd == ["git", "fetch", "--progress", "origin", "sunnypilot"] and cwd == updated.OVERLAY_MERGED
      snap("fetch start")
      assert Params().get("UpdaterState") == "downloading..."
      for pct in (10, 55, 100):
        on_percent(pct)
      snap("fetch done")
      return "From https://github.com/SoRadGaming/openpilot\n"

    def fake_agnos(progress_cb=None):
      assert progress_cb is not None, "fetch_update must pass its progress callback"
      progress_cb(0.3)
      snap("os")
      progress_cb(1.0)   # the last partition, then straight back to fetch_update's clear()

    def fake_finalize():
      snap("finalize")
      seen.append(("state", Params().get("UpdaterState")))

    with mock.patch.object(updated, "run", fake_run), \
         mock.patch.object(updated, "git_fetch_with_progress", fake_fetch), \
         mock.patch.object(updated, "handle_agnos_update", fake_agnos), \
         mock.patch.object(updated, "finalize_update", fake_finalize), \
         mock.patch.object(updated, "set_consistent_flag", lambda consistent: None), \
         mock.patch.object(updated, "setup_git_options", lambda cwd: None), \
         mock.patch.object(updated, "AGNOS", True):
      u = updated.Updater()
      u.params.put("UpdaterTargetBranch", "sunnypilot", block=True)
      u.fetch_update()

    assert seen == [
      ("fetch start", {"phase": "code", "pct": None}),
      ("fetch done", {"phase": "code", "pct": 100}),
      ("checkout", {"phase": "checkout", "pct": None}),
      ("os", {"phase": "os", "pct": 30}),
      ("finalize", None),
      ("state", "finalizing update..."),
    ], seen

  def test_updated(self):
    src = UPDATED.read_text(encoding="utf-8")
    # the fetch has a meter, and cloudlog still gets git's output
    assert re.search(r'git_fetch_with_progress\(\["git", "fetch", "--progress", "origin", branch\], OVERLAY_MERGED', src)
    assert 'cloudlog.info("git fetch success: %s", git_fetch_output)' in src
    assert 'cloudlog.info("git reset success: %s", \'\\n\'.join(r))' in src
    assert "handle_agnos_update(progress_cb=" in src
    assert "flash_agnos_update(manifest_path, target_slot_number, cloudlog, progress_cb=progress_cb)" in src
    # phases in order, then cleared before "finalizing" and on the way back to idle
    i_code = src.index("self.progress.set(PHASE_CODE)")
    i_state = src.index('self.params.put("UpdaterState", "downloading..."')
    i_checkout = src.index("self.progress.set(PHASE_CHECKOUT)")
    i_os = src.index("self.progress.set(PHASE_OS")
    i_final = src.index('self.params.put("UpdaterState", "finalizing update..."')
    assert i_code < i_state < i_checkout < i_os < i_final
    assert "self.progress.clear()" in src[i_os:i_final]
    assert re.search(r'updater\.progress\.clear\(\).*\n\s*params\.put\("UpdaterState", "idle", block=True\)', src)

  def test_agnos_keeps_its_signatures(self):
    src = AGNOS.read_text(encoding="utf-8")
    for sig in ("def extract_compressed_image(target_slot_number: int, partition: dict, cloudlog,",
                "def flash_partition(target_slot_number: int, partition: dict, cloudlog, standalone=False,",
                "def flash_agnos_update(manifest_path: str, target_slot_number: int, cloudlog, standalone=False,"):
      assert sig in src, sig
    assert src.count("progress_cb: Callable[[float], None] | None = None") == 3, "a new parameter, last, with a default"
