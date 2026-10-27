# STORAGE: room on /data, and what the comma 4's storage is doing (push16)

push16, 2026-10-28, branch `storage16` on push15 (`2984df599`). Area **ST**, markers `FORK(STORAGE)`. Not car-specific,
not gated. The owner's question after push15: "i got low speed storage problem again, is comma dying?? allocate more
spare space if you think [that's] worth?"

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

So the alert needs core 4 to have room (moving card to core 6, which camerad uses 2-19 %, or reading hardwared's
sensors on a thread of their own). **That is not in push16.** push16 is the storage half, which the evidence supports
as harmless and possibly a trigger removed: no routine deletes while driving.

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
| 10-20 % | **routine**: the oldest segment whose qlog and qcamera an uploader has sent; a second between segments | nothing |
| below 10 % or 5 GiB (upstream's floor) | **critical**: the oldest segment, uploaded or not, at upstream's 0.1 s | **critical**: the same, one file at a time, the largest first, 6 s apart (`ONROAD_FILE_PACE_S`) |
| below 8 % (`EMERGENCY_PERCENT`) | critical, unpaced | critical, unpaced |

* **Upstream's order is kept** in every row: the oldest first, `.lock` never, the preserved segments and `boot` /
  `crash` last. The routine row only skips segments with a file an uploader would still send by itself (`qlog`,
  `qlog.zst`, `qcamera.ts`) and has not (`user.upload`, comma's uploader; `user.sunny.upload`, sunnylink's). The
  uploader goes oldest first, so in practice those are the newest. Their xattrs are read past `xattr_cache`'s cache:
  the uploader sets them in its own process, and a cached "not yet" would never change.
* **6 s** is longer than /data's ext4 `commit=5`, so each file's discards go out with a journal commit of their own
  instead of a whole 120 MB segment's at once. About 40 s per segment, against one recorded per minute.
* **8 %** sits above selfdrived's "Out of Storage" (below 7 %): there the pacing stops.
* **The drive is `IsOffroad`**, which manager writes on every change of `started` and the uploader also reads.
  Unknown counts as driving: the routine work waits, the floor does not.
* **The external drive path** (`/mnt/external_realdata`, when mounted) is upstream's, unchanged, under the same rows.
* **No Params writes.** The deleter reads `IsOffroad` and nothing else.

With every drive since July under 69 minutes, and about 9 GB (75 minutes of recording) between 20 % and the floor, none
of those 115 drives would have needed a delete while driving, provided the device was parked and on long enough before
each to catch up (about a second a segment). A drive that starts below 20 % - the first after the update if it
follows straight on, or several drives with no time parked between them - deletes at the floor as before, paced.

**What it costs:** about 9 GB once, the first time the device is parked with push16: roughly 75 one-minute segments
of the oldest drives, about two days of the owner's driving. After that the device keeps that much less history;
every drive's qlogs and qcameras are uploaded (connect lists both for every segment of 158-15d) and stay in connect.
What goes with them for good is what only the device had: the oldest drives' full-resolution video and rlogs.

## 4. Diagnostics in the routes

All at error level, so `errorLogMessage` and with it the qlogs keep them.

**`deleter deleted`**, after each delete: `segment`, `why` (`routine` / `critical`), `onroad`, `pace_s`, `mb`, `files`,
`seconds`, `free_pct` (before; ext4 frees the blocks a little later), `complete` (false if the deleter was stopped
mid-segment). Parked, these go to the device's own log only; nothing logged offroad reaches a route.

**`deleter parked summary`**, once per parking, 30-60 s into the next drive (`ParkedSummary`): `parked_s`,
`free_pct_parked` / `free_pct_now`, `deleted` (routine and critical counts), `deleted_mb`, `deleting_s`,
`waiting_for_upload` (the routine work stopped at segments not uploaded yet), `disk_written_mb` / `disk_writes` /
`disk_write_ms` (/data's partition over the parked time, from `/proc/diskstats`), `ext4_written_mb` (ext4's lifetime
counter, which on this 4.9 kernel also counts discards), `writers` (the five processes that wrote the most to storage
over the parked time, from `/proc/<pid>/io` sampled every 5 minutes; `WriteSampler` in `storage_diag.py`),
`writers_mb`, `procs_unreadable` (processes whose counters this user may not read: root's), and the policy's numbers.
It is there for the 57-61 GB written in each of two parked windows (26 and 27 Oct) by a writer the logs do not name.

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
* **The alert itself** (section 1): core 4's headroom, or hardwared's sensor reads off its loop. A separate change.

## 6. Tests

`openpilot/system/loggerd/tests/test_deleter_policy.py` (31): the thresholds and the pacing table; the routine row
takes only uploaded segments (comma's or sunnylink's xattr, qlog and qcamera both, past the cache) in upstream's order
and never a locked one; the floor takes the oldest uploaded or not, parked or driving; `remove_dir` largest first with
the pacing between files, stopped by the exit event with the qlog left to the last; the real `deleter_thread` driven
through a parking, a drive above and below the floor, an emergency, the drive starting mid-way, waiting for the
uploader, and every segment locked; `ParkedSummary` once per parking, 30 s in, with the counts. `test_storage_diag.py`
(+7): the UFS descriptor read from a sysfs tree with and without one, `cpu_state`, `/proc/<pid>/io` sampling (a
process from before the window, one started inside it, a reused pid, unreadable ones, a bounded table).

`test_deleter.py` (upstream's, `FORK(UPSTREAM-FIX)`): its fake `statvfs` is now patched for each test and put back.
Upstream assigned `deleter.os.statvfs` - `os.statvfs` itself - and never restored it, so any later test in the same
worker got the fake (`test_storage_diag`'s report then raised). Offer upstream.

20 mutations of the policy, the thread and the sampler (routine while driving, uploads ignored, no pacing, the cached
xattr, smallest first, the summary at once or counting driving deletes, the emergency paced, a locked segment
deleted, the drive ignored by the thread or the step, unknown as parked, lifetime write counts, qcamera or sunnylink
ignored, the delete line not at error level, the floor's bytes ignored, no wait after a paced segment, a 15 % target):
all fail at least one test.

## 7. Files

| file | |
|---|---|
| `openpilot/sunnypilot/system/deleter_policy.py` | the policy, `remove_dir`, `ParkedSummary`, the `deleter deleted` line |
| `openpilot/system/loggerd/deleter.py` | upstream's file: `deleter_step(onroad=None)` and `deleter_thread` call the policy; the external-drive path, `get_preserved_segments` and the constants are upstream's. On a merge, keep upstream's lines and re-apply the marked ones |
| `openpilot/sunnypilot/system/storage_diag.py` | `ufs_health`, `cpu_state`, `proc_write_bytes`, `WriteSampler` |
| `openpilot/system/loggerd/tests/test_deleter_policy.py`, `test_deleter.py`, `openpilot/sunnypilot/system/tests/test_storage_diag.py` | section 6 |
