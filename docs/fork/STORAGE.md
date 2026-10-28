# STORAGE: room on /data, and what the comma 4's storage is doing (push16, push17)

push16, 2026-10-28, branch `storage16` on push15 (`2984df599`). Area **ST**, markers `FORK(STORAGE)`. Not car-specific,
not gated. The owner's question after push15: "i got low speed storage problem again, is comma dying?? allocate more
spare space if you think [that's] worth?"

push17, 2026-10-29, branch `updater17` on push16 (`c5aec4728`): the writer behind the 57-61 GB parked nights, updated
copying its whole 2.5 GB tree on every pass, and the fix (section 7). Area **UPD**, markers `FORK(UPDATER)`.

## 1. What the owner saw, and what it was

* **It was not a storage alert.** On route 0000015d (Wed 28 Oct 01:18:08, 59 km/h) the alert was "TAKE CONTROL
  IMMEDIATELY / Low Communication Rate Between Processes" (`commIssueAvgFreq`, 0.9 s of soft disable, no
  disengagement). The only storage alert in the code is "Out of Storage", below 7 % free (`selfdrived.py`); the
  lowest free space on any of these routes was 9.89 %.
* **The trigger was hardwared, not /data.** deviceState stopped for 4.81 s because hardwared's `thermal` section (the
  thermal-zone reads in /sys) took 4.43 s. Over those 4.46 s the disk was busy 220 ms and had nothing queued at the
  end, and mapd (the /data witness) did not stop. Route 00000153 (Sun 25 Oct) had the same alert the same way: the
  `power` section took 3.70 s, the disk was busy 120 ms.
* **What the stalls have in common is CPU core 4.** card, controlsd and selfdrived all run there at real-time
  priority (FIFO 53). Together they used about 60 % of the core in July, 64-68 % in September, 91-95 % from 1 Oct
  (upstream's 1.69 GHz cap on both CPU clusters and AGNOS 19.7 arrived on the same drive, route 10f) and 99.5 % on route
  157, where core 4 was at 98 % or more for one stretch of 516 s. Every one of the 21 stall events on routes 14d, 152,
  153, 156, 157, 158 and 15d came after 30 s of core 4 averaging at least 96.8 % - though stretches of 75-178 s at full
  load also passed with no stall. On 157 the kernel itself said so: a kernel worker on CPU 4 was runnable and not run for at
  least 6 minutes, in the middle of the final close of a file (`delayed_fput`), with CPU 4's block-I/O dispatch work
  waiting behind it. That it delayed /data's I/O is a strong inference, not a measurement; for 153 and 15d (qlogs
  only, no kernel log) the path from core 4 to the slow sensor read is not proven.
* **The flash is not dying, as far as anything on the device can say.** ext4's error count is 0 in every storage
  report, the kernel logs of all 130 local routes (9 Jul - 27 Oct) have no UFS, SCSI or I/O errors and no ext4 or
  journal errors, and journal commits average 2.9-5.4 ms. Inside one of 157's stalls a 21 MB backlog drained in
  0.51 s (42 MB/s) once it reached the device: the waiting happened before the flash. Nothing logs the UFS's own wear
  estimate yet; push16 adds it to the storage report where the kernel exposes it (section 4).
* **More free space would not have prevented any of it.** Stalls came at 10.9-12.8 % free too, and without deletes
  (152, 153 and 156 never reached the deleter's floor; on 158 an 8.4 s stall came 425 s before the first delete). 155
  deleted 29 segments with no stall. The one link: on 157, four of six stall episodes began 6-13 s after a delete
  (p about 0.03); 15d and 158 show none.

So the alert needs core 4 to have room (moving card to core 6, where camerad uses about 7 %, 8.5 % with its IRQ work;
/proc/stat's 2-19 % there is tick aliasing), or hardwared's sensors read on threads of their own. **Both are in
push16's third and fourth commits, documented in
[COMM-ISSUE.md](COMM-ISSUE.md) section 9** (measured against inferred, the bench, the new lines). This document is the
storage half, which the evidence supports as harmless and possibly a trigger removed: no routine deletes while
driving.

## 2. The deleter before push16 (upstream's)

`openpilot/system/loggerd/deleter.py`, `always_run`: when free space is below 10 % (`MIN_PERCENT`) or 5 GiB
(`MIN_BYTES`) it removes the oldest segment directory it can - never one with a `.lock` file, the 5 newest preserved
(bookmarked) segments and the two before each, and the `boot` / `crash` folders last - with `shutil.rmtree`, checks
again 0.1 s later, and otherwise sleeps 30 s. It never asks whether the car is driving.

This device has sat at that floor since 19 July. A one-minute segment is 118-121 MB, so at the floor it deletes
about one a minute of driving: **1,453 deletes while driving over the 115 local drives since 19 July** (43 an hour;
16 on 15d's 15.7 minutes). /data is mounted `discard` (every storage report), so each delete's freed blocks go to the
UFS as discards from the next journal commits.

## 3. What push16 changes (`openpilot/sunnypilot/system/deleter_policy.py`)

| | parked (IsOffroad) | driving |
|---|---|---|
| free >= 20 % (`ROUTINE_PERCENT`) | nothing | nothing |
| 10-20 % | **routine**, after 2 minutes parked (`PARKED_GRACE_S`): the oldest ordinary segment no upload is waiting for (comma's uploader has sent its qlog and qcamera; no file of its route in athenad's queue in the last 30 minutes, `UPLOAD_HOLD_S`); 6 s between segments (`ROUTINE_SEGMENT_PACE_S`). Never a preserved segment or `boot` / `crash` | nothing |
| below 10 % or 5 GiB (upstream's floor) | **critical**: the oldest segment, uploaded or not, at upstream's 0.1 s | **critical**: the same, one file at a time, the largest first, 6 s apart (`ONROAD_FILE_PACE_S`) |
| below 8 % (`EMERGENCY_PERCENT`) | critical, unpaced | critical, unpaced |

* **Upstream's order is kept** in every row: the oldest first, `.lock` never. The preserved (bookmarked) segments and
  `boot` / `crash` are upstream's last resort, taken only below its floor and after every ordinary segment; the routine
  row never takes them. (Boot and crash logs are not named like the uploaders' files, so an upload check would pass
  them even when nothing in them was sent.)
* **The routine row skips segments an upload is waiting for**: a file comma's uploader sends by itself (`qlog`,
  `qlog.zst`, `qcamera.ts`) that it has not marked sent (`user.upload`), or any segment of a route athenad's
  `AthenadUploadQueue` has listed (an rlog or a full video asked for in connect) in the last 30 minutes
  (`UploadHold`). Only comma's mark counts: the owner pulls his routes from connect, and sunnylink's
  (`user.sunny.upload`; its uploader is off on this device) would let a qlog go that connect never got. The uploader
  goes oldest first, so in practice those are the newest. The xattrs are read past `xattr_cache`'s cache: the
  uploader sets them in its own process, and a cached "not yet" would never change. Why a route, and why 30 minutes
  after it leaves the queue: athenad's queue lists only the files still waiting - its four upload threads take theirs
  out, and the queue it writes after each upload leaves out the ones still being sent. Deleting one of those is
  harmless while its upload goes on (athenad holds it open), but if that upload fails, the retry finds the file gone
  and athenad drops the request. The files taken before the deleter read the queue are usually of a route still
  listed; the deleter reads the queue on every pass, driving too (every 30 s at most). A queue that is missing or not
  valid JSON counts as empty, as athenad itself starts with an empty one then.
* **2 minutes parked first**, counted from the deleter's own start. manager writes `IsOffroad=True` when it starts and
  clears it only once the drive has `started`, so a boot straight into a drive (the update, then away) looks parked for
  its first seconds; a short stop (fuel, a drop-off) is the same. Either would otherwise begin the drive with the
  discards of whatever was deleted in the last seconds before it.
* **6 s** is longer than /data's ext4 `commit=5`. While driving, each file's discards go out with a journal commit of
  their own instead of a whole 120 MB segment's at once: about 40 s per segment, against one recorded per minute.
  Parked, each commit carries one segment's discards, not five.
* **8 %** sits above selfdrived's "Out of Storage" (below 7 %): there the pacing stops.
* **The drive is `IsOffroad`**, which manager writes on every change of `started` and the uploader also reads.
  Unknown counts as driving: the routine work waits, the floor does not.
* **The external drive path** (`/mnt/external_realdata`, when mounted) is upstream's, unchanged, under the same rows.
* **No Params writes.** The deleter reads `IsOffroad` and `AthenadUploadQueue`, and nothing else.

With every drive since July under 69 minutes, and about 9 GB (75 minutes of recording) between 20 % and the floor, none
of those 115 drives would have needed a delete while driving, provided the device was parked and on long enough before
each to catch up: 2 minutes, then 6 s per minute driven before (5 minutes in all after a 30-minute drive: the
2-minute wait and 30 segments at 6 s). A drive that starts
below 20 % - the first after the update if it follows straight on, or several drives with no time parked between
them - deletes at the floor as before, paced.

**What it costs:** about 9 GB once, the first time the device is parked and on with push16 for about 10 minutes (2
minutes' wait, then 75 segments at 6 s): roughly 75 one-minute segments of the oldest drives, about two days of the
owner's driving. After that the device keeps that much less history; every drive's qlogs and qcameras are uploaded
(connect lists both for every segment of 158-15d) and stay in connect. What goes with them for good is what only the
device had: the oldest drives' full-resolution video and rlogs. Bookmarked segments and the boot / crash logs are not
touched above the floor, and nothing queued in connect is deleted before athenad has sent it while there is room
(above the floor; below it upstream's oldest-first rule applies, queued or not). The queue lives on the device, so a
request reaches it only while the comma is on and online.

How far back the device reaches: on route 157 (27 Oct) the floor was deleting route 131 (12-13 Oct), so about two
weeks; push16 shortens that by about two days. The rlogs of 152, 153, 158 and 15d are on the device only (connect has
their qlogs, not their rlogs). They carry the kernel log, which names any CPU-4 kernel worker stuck 30 s or more
around the alerts and the work queued behind it (the kernel's workqueue watchdog reports nothing shorter), so they can
support or weaken the core-4 path for 153 and 15d (section 1), not prove it. Asked for from connect within about a
week, with the comma on and online, they are queued and then held until sent. 15d's and 158's have since been
downloaded and read: [COMM-ISSUE.md](COMM-ISSUE.md) 9.13.

## 4. Diagnostics in the routes

All at error level, so `errorLogMessage` and with it the qlogs keep them.

**`deleter deleted`**, after each delete: `segment`, `why` (`routine` / `critical`), `onroad`, `pace_s`, `mb`, `files`,
`seconds`, `free_pct` (before; ext4 frees the blocks a little later), `complete` (false if the deleter was stopped
mid-segment). Parked, these go to the device's own log only; nothing logged offroad reaches a route.

**`deleter parked summary`**, once per parking, 30-60 s into the next drive (`ParkedSummary`): `parked_s`,
`free_pct_parked` / `free_pct_now`, `deleted` (routine and critical counts), `deleted_mb`, `deleting_s`,
`waiting_for_upload` (the last parked pass's routine work stopped short of 20 % at segments an upload is waiting for;
false if it reached 20 %, or stopped at bookmarks or boot / crash only), `disk_written_mb` / `disk_writes` /
`disk_write_ms` (/data's partition over the parked time, from `/proc/diskstats`), `ext4_written_mb` (ext4's lifetime
counter, which on this 4.9 kernel also counts discards), `writers` (the five processes that wrote the most to storage
over the parked time, from `/proc/<pid>/io` sampled every 5 minutes; `WriteSampler` in `storage_diag.py`),
`writers_mb`, `procs_unreadable` (processes whose counters this user may not read: root's), and the policy's numbers
(`routine_pct`, `floor_pct`, `emergency_pct`, `pace_s`, `routine_pace_s`, `grace_s`).
It is there for the 57-61 GB written in each of two parked windows (26 and 27 Oct) by a writer the logs do not name -
almost certainly updated (section 7), whose fix in push17 this line is also the proof of.

**`storage report`** (push12's, at hardwared's start and 30 s into each drive) gains:

* `ufs`: the UFS host above /data's disk in sysfs and, where the kernel has them, its `health_descriptor`
  (`life_time_estimation_a` / `_b`: 0x01 = 0-10 % of its life used ... 0x0A = 90-100 %, 0x0B = past it; `eol_info`:
  0x01 normal, 0x02 warning, 0x03 urgent) and `string_descriptors` (maker, product, revision; not the serial). Without
  a health descriptor, `entries` lists what the host does offer, for the next step (a root-only debugfs read, or
  ufs-utils).
* `cpu`: every cpufreq policy's `cpuinfo_max_freq`, `scaling_max_freq` (the cap), `scaling_cur_freq`, governor and
  CPUs; `sched_rt_runtime_us` / `_period_us`; `rt_runtime_share` (from debugfs, `None` when it is root's only).

## 5. Not done, and why

* **A higher floor at all times (20-25 %).** It would delete the same 9-14 GB once, and then delete once a minute
  while driving at the new floor, exactly as now. The stalls do not depend on the free space.
* **fstrim, or `nodiscard` plus a trim when parked.** Online `discard` already tells the UFS about every freed block,
  so fstrim would only find what discard missed, and on a nearly full device it can keep the UFS busy for a long time.
  AGNOS lets openpilot run `sudo` without a password (`hardware.py` uses it), so a one-shot `sudo fstrim /data` while
  parked would be possible and leaves nothing behind, but nothing here says it would help. Changing the mount means a
  remount at every boot; not done.
* **The alert itself** (section 1): core 4's headroom and hardwared's sensor reads off its loop. Not the deleter's
  business: push16's `FORK(CPU)` / `FORK(COMM)` commits, [COMM-ISSUE.md](COMM-ISSUE.md) section 9.

## 6. Tests

`openpilot/system/loggerd/tests/test_deleter_policy.py` (49): the thresholds, the grace and the pacing table; the
routine row takes only uploaded segments (comma's xattr, qlog and qcamera both, past the cache; sunnylink's alone is
not enough) in upstream's order, never a locked one, never one of a route with a file in athenad's queue (a
malformed queue holds nothing; the whole route, held 30 minutes after the queue last listed it, `route_of` and
`UploadHold` on their own and in the thread, a route listed while driving held once parked), and
never a bookmarked segment or `boot` / `crash`, which the floor then takes last, in upstream's order; the floor takes
the oldest uploaded or not, parked or driving; `remove_dir` largest first with the pacing between files, stopped by the
exit event with the qlog left to the last, and a file that appears meanwhile removed too; the real `deleter_thread` on
a fake clock (every wait advances it) driven through a parking, a drive above and below the floor, an emergency, the
drive starting mid-way, waiting for the uploader, every segment locked, a boot straight into a drive (nothing
deleted), a stop, a drive and a second stop (each waits out the grace), the exit event in the middle of a paced
segment (`complete` false), and a whole parking into the next drive's `deleter parked summary` (the routine count, the
size, `waiting_for_upload` still true 30 s into the drive, and false when the parking reached 20 % or stopped at a
bookmark); `ParkedSummary` once per parking, 30 s in, with the counts. `test_storage_diag.py`
(+7): the UFS descriptor read from a sysfs tree with and without one, `cpu_state`, `/proc/<pid>/io` sampling (a
process from before the window, one started inside it, a reused pid, unreadable ones, a bounded table).

`test_deleter.py` (upstream's, `FORK(UPSTREAM-FIX)`): its fake `statvfs` is now patched for each test and put back.
Upstream assigned `deleter.os.statvfs` - `os.statvfs` itself - and never restored it, so any later test in the same
worker got the fake (`test_storage_diag`'s report then raised). Offer upstream.

41 mutations of the policy, the thread and the sampler (routine while driving, uploads ignored, no pacing, the cached
xattr, smallest first, the summary at once or counting driving deletes, the emergency paced, a locked segment
deleted, the drive ignored by the thread or the step, unknown as parked, lifetime write counts, qcamera or sunnylink
ignored, the delete line not at error level, the floor's bytes ignored, no wait after a paced segment, a 15 % target;
the thread no longer feeding the summary its passes, deletes or upload state, `complete` always true, no fallback for
a file that appears, the exit event ignored in `remove_dir` or by the thread; routine taking bookmarks or boot /
crash, the floor skipping them; no grace, a grace a drive does not restart, a segment a second, the thread passing
no parked time; athenad's queue ignored, read per file, trusted when it is not a list or points outside /data's log
root, or raising): all fail at least one test.

**The review** (second commit on `storage16`) changed four things, each with tests that failed first on the first
commit: (1) the routine row took bookmarked segments and then `boot` / `crash` while newer ordinary segments still
waited to upload - upstream does that only below its floor; (2) it deleted a segment a second from the first second
parked, so up to five segments' discards per commit, and on the first pass after a boot, just before the drive; now 2
minutes parked first and 6 s between segments; (3) the tests never ran the thread into a parked summary, and the one
that does found a bug: `waiting_for_upload` was set by every pass, so by the time the summary was logged, 30 s into
the drive, the drive's passes had always made it false; now only parked passes set it, and only when an upload is what
held the work; (4) the routine row did not know athenad's upload queue, so a file asked for in connect could be deleted
before it was sent.

**The second review** (eighth commit; COMM-ISSUE.md 9.12) changed two more, each with tests that failed first on
`e76c253c6`: (5) athenad's queue lists only the files still waiting, so a file being uploaded dropped out of it as
soon as another upload ended, and a routine delete could take it - lost for good if that upload then failed; now the
whole route is held until 30 minutes after the queue last listed it, and the queue is read on every pass, driving too
(`UploadHold`); (6) sunnylink's upload mark counted as uploaded, so with its uploader on, a qlog connect never got
could go; now only comma's counts (one of the 41 mutations above, "sunnylink ignored", is now the rule). Mutations of
the new code - a hold per segment instead of per route, the hold dropped once out of the queue or never
dropped, the thread not passing or not updating its hold, updating it only parked, sunnylink's mark counted again,
the routine row ignoring the hold - all 8 fail a test.

## 7. The parked writer: updated copying itself every 1.5 h (push17)

**What was writing.** On the two parked nights measured, /data took 56.6 GB of writes (Mon 26 Oct 00:54-15:45) and
60.9 GB (Tue 27 Oct 01:40-16:53), with 26.8 and 28.1 GB read and 230,558 / 230,818 write requests; the parkings with
no network (github.com unreachable, `UpdateFailedCount` 79-103) wrote 1.6-4.4 GB. The best-supported writer is
`updated` - a strong inference, since nothing per process named it before push16's parked summary:

* Its loop calls `fetch_update()` on every pass on an unmetered network - every 1.5 h after a success, every 5 minutes
  after a failure - and `fetch_update()` always ended in `finalize_update()`: `rmtree(FINALIZED)`, then `copytree` of
  the whole overlay, `.git` included, into `/data/safe_staging/finalized`, whether or not the fetch brought anything.
  Stock code since comma's "good updater experience" (#25724, 2022) dropped the old "nothing new from git at this time"
  skip; sunnypilot and comma's current `updated.py` do the same, so there is no upstream fix to port.
* The tree is about 2.5 GiB (2.65 GB: the free-space jump each time it was deleted, on 9 parkings with no network). A
  copy writes that, and deleting the one before is counted again as discards (ext4's counter here includes them).
  initData's first-to-last fetch span was 13.7 h on both nights, 10 passes: ten copies and ten discards are about
  49-53 GB, and everything else about 2-5 GB. Ruled out or minor: the deleter, loggerd, the uploader, mapd / OSM, the
  NSW zones, models and backups.
* Across the 50 local routes of 1-28 Oct (boots 49-64): 12 clean online parkings, 69 completed passes, of which 2-3
  fetched a commit FINALIZED did not already hold (`af45423`, `faa0632`, `2984df5`) - about 96 % of the copies were of
  an identical tree. Home nights ran 9-11 passes (12.3-16.3 h), daytime online parkings 1-3.
* Route 158 began with `UpdaterState` "finalizing update...": the drive (16:53:24) killed a copy about 1.4 of its
  2.5 GiB in, with no consistent flag. A FINALIZED at the fetched commit can be a partial tree, so only the flag can
  say it is whole.
* Retention was normal (free space sits about 2.5-3 points lower while FINALIZED exists): the cost was flash wear and
  parked power, not room.

**What push17 changes** (`openpilot/sunnypilot/system/updated/finalize_reuse.py`; in `updated.py` an import and two
marked places in `fetch_update()`): a pass keeps FINALIZED, instead of deleting and copying it again, when it already
is what the copy would make. All of these must hold; anything else, an error included, is `finalize_update()` exactly
as before, with the reason logged (`finalizing in full: ...`):

| condition | how |
|---|---|
| FINALIZED's consistent flag was set when this pass began | read before `fetch_update()` clears it (`finalized_id()`). `main()` clears the flag every time updated starts and a recreated overlay deletes FINALIZED, so a set flag means a finalize by this same process, from this same overlay |
| its commit and branch are the overlay's after this pass's checkout (FETCH_HEAD: what the copy would take) | a full 40-digit commit, a branch name (not a detached `HEAD`), FINALIZED the top of its own repo |
| after `finalize_update()`'s own two resets, run on it as after a copy | the same commit and branch again, every submodule at the overlay's HEAD, `.git/config` byte for byte the overlay's, and `git status` clean outside the submodules |

Then the flag is set again, as `finalize_update()` would, and updated logs `finalized update already at <sha7> on
<branch>, kept it` (offroad, so in the device's own log only). The flag stays cleared through the fetch as before, so a
power loss or reboot mid-pass installs nothing, and a drive starting during the resets (manager's SIGINT) leaves it
cleared. The same either way: `UpdateAvailable`, `UpdaterFetchAvailable`, `UpdaterNewDescription`, the release notes,
FINALIZED's files, refs and `.git/config`, and the boot swap. Not refreshed on a kept pass, and read by nothing:
`.git`'s reflogs and FETCH_HEAD, and the ignored build products inside submodule folders, as copied from BASEDIR by the
pass that made FINALIZED (BASEDIR's change only at a boot, and a boot deletes FINALIZED).

**What still copies**, as before: the first pass after updated starts (after every drive and every boot: `main()`
clears the flag); the pass after a failed one (offline, or a Wi-Fi drop: the overlay is recreated and FINALIZED with
it); and every new commit, branch switch or rollback. So a night parked online is about one copy instead of about ten:
about 8 GB of counted writes instead of 57-61 GB, 13-14 GB if a push lands overnight (the device evidence's model -
the copy, its discard, the deleter's matching delete of old routes and about 0.2 GB/h of background writes - which
fits both measured nights, 56 against 56.6 GB and 60 against 60.9). Of the 69 passes counted, push17 would still have
copied 12-15 (17-22 %), nearly all the first pass of a parking.

**The boot swap is unchanged.** `launch_chffrplus.sh` swaps in any flagged FINALIZED, at the running commit too, and
that boot then rebuilds (a swapped tree has no build products). It needs the comma to power down with a finished copy
flagged (after `MaxTimeOffroad`'s 30 h, or by hand), which is rare, and push17 makes it neither more nor less likely.
Skipping the copy whenever the fetched commit is the *running* one would also save each parking's first copy, but it
changes what a boot does and leaves stale FINALIZED trees and descriptions behind: not done.

**On the car.** push16's `deleter parked summary` after an online night (section 4): `disk_written_mb` /
`ext4_written_mb` about 8 GB (13-14 GB if a push landed) instead of about 55-60 GB. updated is killed at the drive's
start, so it is not in the drive's procLog; its writes go under the process name `openpilot.system...` cut to the
kernel's 15 characters, which 12 or more daemons share, and the rmtree's discards under no process at all. A check without code: home Wi-Fi marked metered for one night
(updated logs "skipping fetch, connection metered", for up to 3 days) should show about 2-4 GB.

**Tests** (`openpilot/sunnypilot/system/updated/tests/test_finalize_reuse.py`, 33): the real `check_for_update()`,
`fetch_update()` and `set_params()` against real git repos - a bare remote with a submodule, the overlay and BASEDIR as
plain clones, FINALIZED in a temp folder (no overlay: that needs root). The first pass copies; nothing new keeps it,
with the same params, flag, commit and branch (no copytree, a marker in its `.git` survives, the kept line logged); a
new commit, a branch switch at the same commit and a force-push rollback copy; a partial copy without its flag (route
158), a broken `.git`, a FINALIZED without `.git` inside another repo, a stale `index.lock`, a moved submodule, an
untracked file and a changed `.git/config` all copy; an edited or deleted tracked file is restored by the resets; a
failed fetch leaves no flag and the next pass copies; AGNOS clearing the flag still ends flagged, an AGNOS failure
finalizes nothing; and a seven-pass scenario run kept and always copied is identical in params, flag, files, refs and
config (4 copies against 7). The decision on its own with a fake git: each condition, an error at every step, a flag
that cannot be set, a KeyboardInterrupt let through, and the `""` that `test_download_progress.py`'s fetch test answers
to every command. The wiring: the record before the flag is cleared, `main()` still clearing it at every start,
`finalize_update()` unchanged, the flag the boot swap reads. Against push16's `updated.py` the 6 tests of what is new
fail (4 failures, 2 errors) and the other 27, the cases that copy in both, pass. Outside the suite, the real
`init_overlay()` (overlayfs under `unshare -rm`, a sudo shim) through 15 passes - an updated restart, a failed check, a
failed fetch, a rollback: push17 copied 6 times and push16 11, with the flag, `UpdateAvailable` and the descriptions
identical pass for pass.

## 8. Files

| file | |
|---|---|
| `openpilot/sunnypilot/system/deleter_policy.py` | the policy, `ParkedTimer`, `athena_queued`, `route_of`, `UploadHold`, `remove_dir`, `ParkedSummary`, the `deleter deleted` line |
| `openpilot/system/loggerd/deleter.py` | upstream's file: `deleter_step(onroad=None)` and `deleter_thread` call the policy (the thread keeps an `UploadHold`, updated every pass); the external-drive path, `get_preserved_segments` and the constants are upstream's. On a merge, keep upstream's lines and re-apply the marked ones |
| `openpilot/sunnypilot/system/storage_diag.py` | `ufs_health`, `cpu_state`, `proc_write_bytes`, `WriteSampler` |
| `openpilot/system/loggerd/tests/test_deleter_policy.py`, `test_deleter.py`, `openpilot/sunnypilot/system/tests/test_storage_diag.py` | section 6 |
| `openpilot/sunnypilot/system/updated/finalize_reuse.py` | push17, `FORK(UPDATER)`: `finalized_id()`, `reuse_finalized()`, `tree_id()`, `difference()` (section 7) |
| `openpilot/system/updated/updated.py` | upstream's file: the import and two marked places in `fetch_update()` - FINALIZED read before its flag is cleared, and `finalize_update()` only when it cannot be kept. On a merge, keep upstream's lines and re-apply these |
| `openpilot/sunnypilot/system/updated/tests/test_finalize_reuse.py` | section 7 |
