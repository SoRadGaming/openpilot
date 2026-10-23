# COMM-ISSUE: the "Communication Issue Between Processes" alert that was the storage (push12)

Area **COMM**, markers `FORK(COMM-ISSUE)`. Not car-specific and not gated: every device runs it. Nothing it changes
is published differently; selfdrived's checks are untouched.

The owner, 2026-10-24: "the only critical bug" - "the stupid alert at random of low communication speed".

---

## 1. What happened

Mid-drive, at random, the comma 4 showed **TAKE CONTROL IMMEDIATELY** with "Low Communication Rate Between Processes"
(`commIssueAvgFreq`) or "Communication Issue Between Processes" (`commIssue`). Both are soft disables. In
softDisabling controlsd sets `forceDecel`, the planner then sets `v_cruise = 0`, and the car slows until the driver
takes over - so the alert brought **its own braking**: -1.1 to -1.5 m/s² at 95-110 km/h, up to about 130 brake
counts. Past 3 s of soft disable openpilot disengages.

| route (push11, sunnypilot `39cbb01be`) | deviceState gaps > 3 s, max | alert episodes | disengaged |
|---|---|---|---|
| **14d** (Thu 22 Oct 20:50 AEDT, full rlogs) | 6, **8.04 s** | 6 (7 on screen) | **2**: 21:20:53.8 at 94 km/h, 21:26:53.0 at 102 km/h |
| **152** (Sat 24 Oct 00:20, qlogs) | 1, **4.92 s** | 1 | 0 |
| 14c, 14f, 150, 151 (same build) | 0 (max 0.90 / 1.41 / 0.70 / 0.90 s) | 0 | - |
| 141, 142, 143 (push9 `55d9562b6`) | 0 (max 0.99 / 0.60 / 0.52 s) | 0 | - |

The owner bookmarked two of them: 14d at 21:19:06.7 and 152 at 00:28:01.1, 10 s and 7 s after an episode.

## 2. The cause

Measured on the route logs and read from the code (the full record: the d1024 `VERDICT.md`, `root_logs.md`,
`root_code.md` and `drive.md`; numbers here are from them).

1. **The alert is hardwared's deviceState stopping.** In every episode hardwared's publishing loop stopped for
   4.0-7.7 s between building deviceState (`hardwared.py:281` at push11) and its `/dev/kmsg` line (`:402`); the kernel's
   own timestamps on that line give the same stall. Every other service kept its rate (at most 0.09 s late). cereal's
   SubMaster logic fed only 14d's deviceState receive times puts every alert onset within 0.02 s of selfdriveState's,
   and correctly gives none for the stalls of 2.71 s and shorter. selfdrived raises `commIssueAvgFreq` for a gap over
   about 3 s (2 Hz service, 1.6 Hz floor over 20 and over 2 samples) and `commIssue` past 5 s (alive = 10 / 2 Hz).
2. **managerState was collateral.** manager runs `SubMaster([...], poll='deviceState')` with `sm.update(1000)`
   (`manager.py:133,143`), so with deviceState stopped it fell to 1 Hz (gaps 1.01-1.05 s on 14d) and failed its own
   floor about 6 s in.
3. **gpsLocationExternal was noise.** It never gapped in the stall windows (max 0.27 s on 14d's full rlog). It runs at
   about 6.5 Hz against its declared 10 Hz, so it always fails the 8 Hz floor, and selfdrived ignores it
   (`selfdrived.py:106`, with alertDebug and lateralManeuverPlan); it appears in the commIssue line only because the
   line prints every service.
4. **What the loop waited on: the params lock.** `Params::remove` takes `flock(LOCK_EX)` on `<params>/.lock` even for an
   absent key (`params.cc:171-172`), and every `Params::put` holds that lock across `rename` and `fsync(dir)`
   (`params.cc:155-161`); an ext4 directory fsync waits for the journal commit. In the stalled span the loop took the
   lock at `:375` (`set_offroad_alert("Offroad_TiciSupport", False)` - a remove, every loop) and at `:381` (the
   temperature alert, a remove whenever the filtered temperature's 0.1 °C text changed: **26-30 % of loops**, about
   once every two seconds), while its own async writer held the lock inside `fsync(dir)` about 4 times a second (`:437`
   GithubRunnerSufficientVoltage and `:502` NetworkMetered, every loop: 8.2 fsyncs/s). hardwared showed a 9th thread
   (the on-demand async writer, stuck in a put) in 21 of 23 procLog samples inside stalls against 9 % outside.
5. **The trigger: /data journal stalls of several seconds.** `jbd2/sda12-8` in D state and per-core iowait of 21-51 %
   in every alerting stall on 14d - the only one of 160 routes with full procLog to show it (42 samples with a core
   over 20 % iowait; 14c tops out at 8.1 %). mapd_manager, logmessaged and the deleter were caught by the same stalls
   (mapd's release came within 40 ms of hardwared's in all six on 14d). `/data` is the UFS `sda12`, 89 GB, 90 % full, at the deleter's 10 % floor.
   **Why it stalled is not known** - nothing in the logs could say, which is what section 5 is for.
6. **Not the build.** push9 to push11 changed nothing in hardwared, params, manager, loggerd, the deleter or the
   messaging; the same build drove clean on 14c, 14f, 150 and 151. This is upstream hardwared turning any journal
   stall over about 3 s into a TAKE CONTROL alert, exposed by this device's storage.

## 3. The fix

**The rule:** a loop that publishes something selfdrived checks for liveness makes no Params write that can wait on
/data - no `remove()`, no `put(..., block=True)`, no `set_offroad_alert()`, and no put on a temporary `Params()` (its
destructor waits for the async write). The writes still happen, the same writes, on another thread.

### 3.1 `ParamsWriter` (`openpilot/sunnypilot/system/params_writer.py`)

`put`, `put_bool`, `remove` and `offroad_alert` return at once; one daemon thread per writer applies them with the
same blocking calls upstream made, so what lands on disk is what upstream writes, milliseconds later (or, in a stall,
when the stall ends - the writer waits instead of the loop).

* Per key the **newest request wins**: a key asked for again before it was written is written once, with the newer
  value, in the place of its first request (it does not move behind the keys asked for since); different keys are
  written in the order first asked for. So a put then a remove of one key is the remove, and IsEngaged True then
  False is False.
* `if_changed=True` skips a put identical to the last one requested through this writer.
* An unknown key or a value of the wrong type **raises on the caller**, as `Params.put` does.
* Nothing is left in a queue. `Params.put(block=False)` was not used for the on-change writes because
  `Params::putNonBlocking` can strand a value until the next put on the same handle when it arrives just as that
  handle's writer thread finishes - harmless when the same key is rewritten every loop, never repaired when it is
  written only on change.
* **When the process ends** it waits up to 3 s (`FLUSH_AT_EXIT_S`) for what is pending. That is a
  `multiprocessing.util.Finalize` (priority 10), not an `atexit` handler: manager starts every `PythonProcess` with
  multiprocessing (fork), and such a child leaves through `os._exit` once its `main()` returns (`popen_fork`), which
  skips atexit - multiprocessing's `_bootstrap` runs its finalizers instead, and a plain interpreter runs them from
  atexit. The first review of push12 caught it: with atexit, a manager-style child that put while another process held
  the lock and was then SIGINTed exited at 0.81 s with nothing on disk (a plain interpreter flushed and exited at 2.04
  s). `stop()` cancels the finalizer. On top of it, hardwared's loop flushes for up to 5 s when it ends and selfdrived
  flushes in `run()`'s `finally` (3.3); manager SIGKILLs a daemon 5 s after its SIGINT.
* **A slow write is logged** (section 5): over 0.3 s, `params writer slow write`. After the fix a /data stall holds
  this thread, not the loop, so this is where the stall shows in the routes.

### 3.2 hardwared (`openpilot/system/hardware/hardwared.py`)

| push11 line | push11 | push12 |
|---|---|---|
| 375 | `set_offroad_alert("Offroad_TiciSupport", ...)`: a remove() every loop | `set_alert(...)` = `set_offroad_alert_if_changed(..., writer=params_writer)`: on change, on the writer |
| 381 | `set_offroad_alert_if_changed("Offroad_TemperatureTooHigh", show, extra_text=f"{temp:.1f}C")` | the same through `set_alert`; `set_offroad_alert_if_changed` now drops a **hidden** alert's text, which set_offroad_alert never shows, so the moving temperature is not a change. A shown alert still follows its text. This also covers chestnut's `Offroad_ChestnutOverheated` (`f"{tempC:.0f} °C"` while hidden) |
| 314 | chestnut status alerts via `set_offroad_alert_if_changed` | via `set_alert` |
| 258 | `put_bool("OnroadCycleRequested", False, block=True)` | `params_writer.put_bool(...)` |
| 392, 398 | `put_bool("IsEngaged", ..., block=True)` at the onroad edge and every engage edge | `params_writer.put_bool(...)` |
| 437, 502 | `put_bool("GithubRunnerSufficientVoltage" / "NetworkMetered", ...)` every loop (its own async writer, 4 lock holds a second) | `params_writer.put_bool(..., if_changed=...)`: on change, and once a minute (`count % 120 == 0`, the Uptime cadence) in case something else rewrote it (NetworkMetered is a BACKUP key that a sunnylink restore can write) |
| 498 | `put("LastOffroadStatusPacket", dat, block=True)` at the onroad edge | `params_writer.put(...)` |
| 512-513 | `put("UptimeOffroad" / "UptimeOnroad", ..., block=True)` every 60 s | `params_writer.put(...)` |
| 453 | `put_bool("DoShutdown", True, block=True)` | **unchanged**: offroad only (`power_monitor.should_shutdown`), and manager must find it set before it shuts down |
| - | `PowerMonitoring`'s `put("CarBatteryCapacity")` every 10 s | **unchanged**: non-blocking on its own long-lived handle, whose C++ thread does the waiting |
| - | about 14 param reads a loop | **unchanged**: reads take no lock (section 7) |

### 3.3 selfdrived (`openpilot/selfdrive/selfdrived/selfdrived.py`)

selfdrived is not on its own list, but selfdriveState paces controlsd (`poll='selfdriveState'`, so carControl and
the steering commands stop with it) and the UI raises its own TAKE CONTROL when it goes missing; its 100 Hz loop made
blocking writes mid-drive: `remove("DriverTooDistracted")` when a driver-monitoring lockout ends, and
`set_offroad_alert()` (a blocking put) for `Offroad_DriverMonitoringUncertain`, `Offroad_Recalibration` and
`Offroad_ExcessiveActuation` - the last one in the iteration that then adds the `excessiveActuation` event, so a slow
commit would have **delayed that disable**. All four, and the lockout flag's put, go through `self.params_writer`.
`__init__`'s writes (before the first selfdriveState) and the non-blocking puts on its long-lived handle
(`LongitudinalPersonality`, CruiseHelper's `ExperimentalMode`) are unchanged.

**The trade, stated:** upstream had `Offroad_ExcessiveActuation` on disk *before* `self.excessive_actuation = True`.
Now the in-process latch and the `excessiveActuation` event come at once - which is what matters while driving - and
the param lands when the writer gets the lock: milliseconds normally, up to the length of a stall otherwise. That
param is PERSISTENT and is what blocks re-engaging after a selfdrived restart (`selfdrived.py:94`) and going onroad
again (`hardwared.py:376`). `run()`'s `finally` flushes the writer for up to 3 s (`FLUSH_AT_EXIT_S`), and the writer's
own exit finalizer does the same, so a normal stop (manager's SIGINT at key-off) writes it; only a crash or SIGKILL of
selfdrived while a stall still holds the writer can lose it. The same holds for `Offroad_Recalibration`,
`Offroad_DriverMonitoringUncertain` and `DriverTooDistracted`.

### 3.4 dmonitoringd's policy (`openpilot/selfdrive/monitoring/policy.py`)

`DriverMonitoring._update_events` wrote `Params().put("DriverLockoutCount", n)` at every lockout: a non-blocking put
on a **temporary** handle, whose destructor waits for the write - flock, two fsyncs - on the loop that publishes
driverMonitoringState. The policy now keeps one handle for its life (`self._params`) and the put returns at once.
`test_monitoring.py` shows both: the temporary waits out a held lock (> 0.7 s of a 1 s hold), the lockout returns in
under 0.2 s.

### 3.5 What was not done

* **selfdrived's checks are not loosened.** A real hang must still raise the alert.
* **mapd_manager is not touched.** Its `liveMapDataSP` gaps are the free in-log witness of a storage stall (18 on 14d,
  17 of them with a deviceState gap alongside), and neither selfdrived nor plannerd checks it (plannerd lists it in
  `ignore_alive`, `ignore_avg_freq` and `ignore_valid`), so its own `set_offroad_alert` every second cannot raise this
  alert.
* No storage change (free-space floor, fstrim, mount options): section 5 first says what the storage is doing.

## 4. The audit: every publisher selfdrived checks

selfdrived's SubMaster (`selfdrived.py:113-121`) checks alive, frequency and valid for every service but the ignored
ones (`accelerometer`, `gyroscope`, the GPS service, `alertDebug`, `lateralManeuverPlan`, `modelDataV2SP`,
`longitudinalPlanSP`). For each publisher, the Params writes and file writes **on the thread that publishes**, at
push11:

| service | publisher (thread) | writes on that thread at push11 | push12 |
|---|---|---|---|
| deviceState 2 Hz | hardwared `hardware_thread` | section 3.2 | **fixed** (3.2) |
| selfdriveState (paces controlsd; the UI checks it) | selfdrived main loop | section 3.3 | **fixed** (3.3) |
| driverMonitoringState 20 Hz | dmonitoringd | `Params().put("DriverLockoutCount")` on a temporary at a lockout; `IsRhdDetected` non-blocking on a long-lived handle every 5 min | **fixed** (3.4) |
| managerState 2 Hz | manager `manager_thread` | `IsOffroad` (block) and `clear_all` only at the onroad / offroad / ignition edges; a daemon's pid (block) only when it (re)starts one; `LastManagerExitReason` at shutdown; `power_watchdog` each loop to `/var/tmp`, which is **tmpfs** on AGNOS 19.7 (initData `df -h`: `/var` tmpfs 128M) | no change: nothing mid-drive; its 1 Hz in the stalls was deviceState's (2.2) |
| pandaStates 10 Hz, peripheralState 2 Hz | pandad (C++) | `CarBusOffStock`: `putBoolNonBlocking` on a static handle while latched, `remove` only at key-off (ignition off and pandad offroad, `car_bus_guard.h`); `ObdMultiplexingChanged` at fingerprinting | no change |
| modelV2 20 Hz | modeld / modeld_v2 | `Chestnut*` puts and removes before the loop; two non-blocking puts on its long-lived handle if the big model fails | no change |
| extrinsicsCalibration 4 Hz | calibrationd | `CalibrationParams` non-blocking, long-lived handle | no change |
| carOutput, carStateSP 100 Hz | card main loop | `ControlsReady` non-blocking once; every other write is on card's own `params_thread` (EpsLkasBoard*, Blr*, DiagToolStock, the flash-trace remove, stock-ACC restore); the CarParams writes are before the loop | no change |
| longitudinalPlan, driverAssistance 20 Hz | plannerd | none, but for `SpeedLimitMode` (block) on brands where speed-limit assist is not allowed (not this car) and `get_sanitize_int_param`'s put of an out-of-range stored value (once) | no change |
| deviceMotion 20 Hz | locationd | none | - |
| lateralDelay 4 Hz | lagd | `LiveDelay` once a minute and `LagdValueCache` every 3 s, non-blocking on long-lived handles; a remove before the loop | no change |
| vehicleParameters 20 Hz | paramsd | `LiveParametersV2` once a minute non-blocking; a remove before the loop | no change |
| lateralTorqueParameters 4 Hz | torqued | `LiveTorqueParameters` once a minute non-blocking; a remove before the loop | no change |
| radarState 20 Hz | radard | none | - |
| controlsState, carControl 100 Hz | controlsd | none | - |
| road / wide / cabin camera states 20 Hz | camerad (C++) | none (reads two debug params) | - |
| userBookmark | ui | on demand (0 Hz, always alive) | - |
| *ignored:* gpsLocationExternal | ubloxd (pigeond feeds it) | none | - |
| *ignored:* accelerometer, gyroscope | sensord | none | - |
| *not checked:* liveMapDataSP | mapd_manager | `set_offroad_alert` every 1 s, globs on /data | left alone (3.5) |
| *not checked:* sunnylinkd, athenad, updated, uploader, loggerd | - | blocking puts of their own | nothing to do: they hold the lock at times, and after push12 no checked loop waits for it |

A "non-blocking put on a long-lived handle" hands the write to that handle's C++ thread and returns; that thread holds
the lock through the fsync, but no checked loop takes the lock any more, so it no longer matters who holds it.
`test_comm_issue_guards.py` holds the three fixed loops to the rule on their source, so an upstream merge that brings a
blocking write back fails a test.

## 5. Diagnostics in the routes (`openpilot/sunnypilot/system/storage_diag.py`)

The owner has no SSH, so the storage's side has to arrive in the routes. All three lines below are
`cloudlog.event(..., error=True)`: they go to `errorLogMessage`, which the qlogs keep. cloudlog sends with
`zmq.NOBLOCK`, so logging never waits either.

**`storage report`**, once when hardwared starts and once per onroad start, 30 s in (loggerd is recording by then),
collected on a thread of its own, for the filesystem holding the params (`/data`):

| field | answers |
|---|---|
| `mount` | the `/proc/mounts` line: device, options (`discard`? `noatime` / `relatime`? `data=`?) |
| `ext4_options` | `/proc/fs/ext4/<part>/options`: every effective option, defaults included (`commit=`, `barrier`, `data=ordered`, `discard` / `nodiscard`) |
| `vm` | every `/proc/sys/vm/dirty_*` and `swappiness`: how much dirty data writeback lets pile up |
| `fstrim` | the timer unit, whether it is wanted, `systemctl is-enabled` / `is-active`, the last run (systemd's stamp file): does TRIM ever reach the UFS |
| `queue` | `/sys/block/<disk>/queue/`: scheduler, nr_requests, `discard_max_bytes` (0 = the device takes no discard), write_cache |
| `space` | total and available MB, available %, free inodes % |
| `ext4` | lifetime / session write kB, delayed-allocation blocks, error count |
| `jbd2` | `/proc/fs/jbd2/<part>-8/info`: transactions, average commit time, time waiting for / locked transactions |
| `diskstats` | the partition's and the disk's `/proc/diskstats` counters |

**`hardwared slow loop section`**, when any section of hardwared's loop takes over 0.3 s (the loop is timed in 17
named sections: `pandaStates`, `thermal`, `free_space`, `system_stats`, `brightness`, `chestnut`, `startup_params`,
`tici_alert`, `temp_alert`, `engaged`, `power_save`, `power`, `athena_ping`, `send`, `statlog`, `status_packet`,
`uptime`): the slowest, every section over 20 ms, the total, and the `/proc/diskstats` delta of the partition and the
disk since the loop's start (reads, writes, sectors written, write ms, in flight at the end, io ms, weighted ms). At
most one line per 10 s; the loops in between are counted (`suppressed`) and the count goes out with the next line.
The first two loops (the cached git calls) are skipped.

**`params writer slow write`**, when one of a `ParamsWriter`'s writes takes over 0.3 s (`params_writer.py`; added
after the first review of push12). The fix took the waiting out of the loop, so a params stall no longer makes a
loop section slow: in 82.5 s of injected stalls the reviewer saw no `slow loop section` line at all. The stall now
sits in the writer's thread, and this line is its trace: `writer` (`hardwared_params` or `selfdrived_params`), `key`,
`op` (`put`, `put_bool`, `remove`, `alert`), `op_s` (the write itself), `since_request_s` (from the first request for
that key), `pending` (the keys queued behind it), `disk` (the partition's and the disk's `/proc/diskstats` delta over
the write) and `slow_writes` / `suppressed`. At most one line per 10 s per writer; the slow writes in between are
counted into the next line. In the bench run (6.1) each patched fault produced exactly one, for the IsEngaged write
that waited the window out (5.54 s).

To read them: `errorLogMessage` in any route's qlog, events `storage report`, `params writer slow write` and
`hardwared slow loop section`. A `params writer slow write` with `disk.<part>.write_ms` or `weighted_ms` near its
`op_s` is the UFS itself being slow; one with small disk counters is a wait on someone else's lock or journal commit.
A `hardwared slow loop section` that is **not** a params write - `startup_params` or `athena_ping` (reads),
`free_space` (statvfs) - means the residual risk of section 7 is real.

## 6. The proof

### 6.1 Bench: the real hardwared, faults injected (`openpilot/system/hardware/tests/comm_issue/`)

`fault_injection.py` runs the **real** hardwared - its `main()`, so `hw_state_thread` and `hardware_thread` - in a
child process: unpatched (`hardwared.py` from push11 `39cbb01be`, imported against this tree) and patched (this tree),
with Params on a scratch root (`PARAMS_ROOT`) and its own `OPENPILOT_PREFIX`. The parent feeds pandaStates (10 Hz,
ignition on), peripheralState (2 Hz, 13 V) and selfdriveState (100 Hz); the thermal zones drift (a ±0.3 °C sine and
±0.6 °C sample noise), so the filtered temperature's 0.1 °C text moves in about a fifth of the loops (17-22 % in the
recorded runs; 26-30 % on 14d) and `:381` fires as it did in the car. Faults, 8 s each:

* **F1** - another process holds `<params>/.lock` with `flock(LOCK_EX)`; the engage state changes 2 s in.
* **F2** - `fscount.c` (LD_PRELOAD) makes every directory fsync that starts inside the window return at its end: one
  slow journal commit, no other process involved; the same engage edge.
* **F3** - F1 and F2 together, with two engage edges and hardwared's 60 s Uptime write inside the window.

The engage edges are there so the patched hardwared has writes to make inside every window. The parent stamps every
deviceState on arrival, runs **cereal's own SubMaster** on the stream at selfdrived's 100 Hz (alive and freq_ok, as
`selfdrived.py:472-478` turns them into commIssue / commIssueAvgFreq), and replays the receive times through
`vf_model.py`'s copy of that logic. `fscount.c` records every fsync and flock(LOCK_EX) with its thread id; the child
records every Params call with its thread, and the diagnostics' cloudlog lines.

The recorded run (2026-10-24 04:49-04:54 AEDT, push12 `2e36bee6d`, WSL2 ext4; times in s after the first deviceState,
every window 8 s: F1 and F2 10.0-18.0, F3 55.5-63.5). The first recorded run (`ab7d00788`, 03:29) gave the same
figures to within 0.02 s; this one adds the last column:

| fault | variant | deviceState max gap | alerts, cereal SubMaster live | alerts, vf_model replay | hardware_thread after its first loop: flock(LOCK_EX) / fsync / blocking Params writes | its longest wait | writer threads' longest flock / dir fsync | temperature alert writes / text changes / loops | `params writer slow write` lines |
|---|---|---|---|---|---|---|---|---|---|
| F1 | unpatched | **8.05 s** | commIssue 14.96, commIssueAvgFreq 18.01 | commIssue 14.96, commIssueAvgFreq 18.01 | 44 / 8 / 44 | 7.55 s | 0.00 / 0.01 s | 8 / 7 / 36 | (no writer) |
| F1 | patched | **0.50 s** | none | none | **0 / 0 / 0** | 0 | 7.04 / 0.00 s | 1 / 11 / 51 | 1: 18.01, IsEngaged, 5.54 s |
| F2 | unpatched | **7.55 s** | commIssue 15.46, commIssueAvgFreq 18.01 | commIssue 15.47, commIssueAvgFreq 18.02 | 45 / 8 / 45 | 7.05 s | 0.00 / 7.55 s | 8 / 7 / 37 | (no writer) |
| F2 | patched | **0.50 s** | none | none | **0 / 0 / 0** | 0 | 5.54 / 7.54 s | 1 / 11 / 51 | 1: 18.01, IsEngaged, 5.55 s |
| F3 | unpatched | **8.05 s** | commIssue 60.46, commIssueAvgFreq 63.51 | commIssue 60.46, commIssueAvgFreq 63.52 | 164 / 8 / 164 | 7.55 s | 0.01 / 0.02 s | 28 / 27 / 136 | (no writer) |
| F3 | patched | **0.50 s** | none | none | **0 / 0 / 0** | 0 | 5.54 / 0.00 s | 1 / 25 / 151 | 1: 63.50, IsEngaged, 5.54 s |

The slow-write lines' disk deltas are small (7-31 writes, 7-16 write ms) because the bench's stall is injected, not
the disk's; in the car a UFS stall would show in `write_ms` and `weighted_ms`. No `hardwared slow loop section` line
appeared in any run: the loop never waited.

* **Unpatched**, every fault: one gap of 7.5-8 s, commIssue 5 s after the last deviceState and commIssueAvgFreq when
  the late one lands - the 14d pattern (6.8-8.0 s, commIssue then AvgFreq). In F1 the loop waited 7.55 s in one
  flock; in F2 no other process was involved: hardwared's own async writer sat 7.55 s in the slow directory fsync
  holding the lock, and the loop waited 7.05 s behind it (root_code's scenario C). Every loop made a blocking write
  (TiciSupport), and the temperature alert was written on 22 % of them. In F3 the loop was already stopped when the
  engage edges and the Uptime boundary came, so it saw neither edge and made its 60 s Uptime write at 67.4 s.
* **Patched**, every fault: the gaps stay 0.50 s and nothing is predicted. hardware_thread made **no flock and no fsync
  in the whole run** - its first loop included - and no blocking Params write; it made only reads (about 31 a second)
  and PowerMonitoring's non-blocking put. The stall moved to the other threads - ParamsWriter's and the C++ writer
  thread of PowerMonitoring's long-lived handle, up to 7.04 s on the lock (F1) and 7.54 s in the fsync (F2).
  ParamsWriter's own longest write was the engage edge's IsEngaged, 5.54 s, and that is its one `params writer slow
  write` line per fault; in F3 it took that put at 57.96, waited 5.54 s, then wrote the coalesced IsEngaged (the
  second edge's False) and the Uptime pair requested at the 60 s boundary, both at 63.52. The temperature alert was
  written once, on the first loop, though its text moved on 17-22 % of the loops.

PASS (all six): unpatched shows the stall and the alert in every fault; patched keeps every gap under 0.75 s, predicts
no alert, its publishing thread makes no flock(), no fsync() and no blocking Params write after its first loop, and a
`params writer slow write` line for a write of at least 1 s is logged inside the window. The raw result is
`comm_issue/RESULT.json`; rerun it from the repo root (gcc and git needed, about 5 minutes):

```
python openpilot/system/hardware/tests/comm_issue/fault_injection.py --json /tmp/fi.json
```

**Harder stalls (the first review's harness, re-run on `2e36bee6d`, patched; its push11 runs alerted in all three).**
S1, three 12 s lock+fsync windows 2 s apart with the 120-loop Uptime write and 7 engage edges inside; S2, a 12 s
fsync-only window across the 240-loop Uptime write, then a 12 s lock-only one; S3, fifteen 1.5 s stalls every 2 s
with 15 engage edges. deviceState's longest gap **0.501 / 0.501 / 0.500 s**, no alert from either check, no flock,
fsync or blocking Params call on hardware_thread, and IsEngaged / Uptime on disk as expected afterwards. The writer
logged **3 / 2 / 2** `params writer slow write` lines - one per long window (IsEngaged, 9.65-11.36 s) and, for S3's
flapping, two lines with 4 slow writes counted in between - and there was no `slow loop section` line.

### 6.2 Unit tests (in the suite)

| test | holds |
|---|---|
| `sunnypilot/system/tests/test_params_writer.py` (15) | writes land; with `<params>/.lock` held every call returns in < 0.1 s and the writer waits; newest wins in first-request order; a key asked for again keeps its first place; put-then-remove is the remove; `if_changed`; `offroad_alert` stores exactly what `set_offroad_alert` stores; caller mistakes raise on the caller; a failed write does not stop the writer; **a slow write is logged** with its key, times and disk delta, not under the threshold, at most once per interval with the rest counted; **a pending write lands when a manager-style (fork) child ends** and when a plain interpreter ends; `stop()` cancels the exit flush. The lock is held from a helper thread with a timed release (5 s), so a writer that wrote on its caller's thread fails these tests instead of hanging the suite |
| `system/hardware/tests/test_hardwared_comm_issue.py` (4) | a hidden alert's text is not a change, a shown one follows it, no writer is upstream; **the real `hardware_thread`, the params lock held 4 s with an engage edge inside and a storage report made to take 3 s: deviceState max gap < 1.5 s, no blocking Params write from the loop, the report on a thread of its own**, TiciSupport / TemperatureTooHigh / NetworkMetered written at most once, GithubRunnerSufficientVoltage only on change. Run against push11's hardwared (a scratch negative control) it fails: a 4.1 s gap |
| `sunnypilot/system/tests/test_storage_diag.py` (5) | the mount lookup (longest mount point), the diskstats delta (in_flight as the end value), the slow-section line at most once per 10 s with the loops in between counted, the report once at start and once per onroad start, and the report never raising |
| `selfdrive/monitoring/test_monitoring.py` (+2) | a temporary handle's put waits out a held lock; the lockout's does not |
| `sunnypilot/system/tests/test_comm_issue_guards.py` (5) | hardware_thread, selfdrived's loop methods and the DM policy contain no blocking Params write (DoShutdown excepted), the guard flags push11's calls, and selfdrived's `run()` flushes its writer in its `finally` |

Every behaviour above is held by a test that fails when it is broken: the second review's mutations (a synchronous
writer, a writer that moves a re-requested key to the end, IsEngaged / Uptime / LastOffroadStatusPacket /
OnroadCycleRequested back to blocking, NetworkMetered or GithubRunnerSufficientVoltage every loop, the temperature or
TiciSupport change reverted, the alerts without the writer, the DM policy on a temporary handle, a synchronous storage
report, the slow-section rate limit or threshold removed) and the fix round's (no slow-write line, its rate limit or
threshold removed, selfdrived without its flush) are all caught; the atexit-only exit flush fails the fork test.

### 6.3 In the car (after shipping)

On any route where liveMapDataSP gaps by more than 2 s, or a `params writer slow write` (the stall, now that the
loops do not wait for it) or `hardwared slow loop section` line appears, deviceState must keep every gap under 1 s
and there must be no commIssue or commIssueAvgFreq. Stalls hit 2 of the last ~110 routes, so this takes weeks of
driving; until then 6.1 is the proof.

## 7. What is still open

* **Why sda12 stalls.** Candidates the logs could test are ruled out (write volume, deletions, uptime, temperature,
  network, AGNOS). The leading hypothesis is write-latency spikes on a 90 %-full UFS with no TRIM reaching it; the
  `storage report` answers the TRIM, mount and writeback questions. Raise the free-space floor or schedule fstrim only
  after it has. A second lead, from the first review: AGNOS's fstab (agnos-builder master,
  `userspace/root/etc/fstab`) mounts /data `discard,noatime,nodiratime,nosuid,nodev,nofail`. With online `discard`,
  ext4 issues the freed blocks' discards from the journal commit, and on a near-full flash device those can take
  seconds - a known cause of long commits. The report's `mount`, `ext4_options` and `queue.discard_max_bytes` say
  whether this device has it. Not acted on here.
* **Reads on the loop.** hardwared still reads about 14 params a loop, and `get_build_metadata()` reads two files under
  `/data/openpilot` twice a loop on a git install. Reads take no lock and the files are in the page cache. The atime
  concern - with `relatime` the first read after a file changes would update its atime, which needs a journal handle -
  is retired by the same fstab: /data is `noatime,nodiratime`, so a page-cached read takes no journal handle. The
  first route's `storage report` mount line confirms it on this device's AGNOS; a slow `startup_params` /
  `athena_ping` section would still show it.
* `statvfs(/data)` (the free-space section) does not take a journal handle.
* `DoShutdown` stays a blocking write, offroad only.
* Other daemons still stall in a storage stall (mapd_manager, logmessaged, the deleter); none is checked by selfdrived.

## 8. Files

| file | |
|---|---|
| `openpilot/sunnypilot/system/params_writer.py` | `ParamsWriter`, its exit flush and its `params writer slow write` line |
| `openpilot/sunnypilot/system/storage_diag.py` | `StorageReporter`, `LoopTimer`, `storage_report()`, `disk_devices()` |
| `openpilot/system/hardware/hardwared.py` | 3.2 and the diagnostics' hooks |
| `openpilot/selfdrive/selfdrived/selfdrived.py` | 3.3 |
| `openpilot/selfdrive/monitoring/policy.py` | 3.4 |
| `openpilot/system/hardware/tests/comm_issue/fault_injection.py`, `fscount.c`, `RESULT.json` | 6.1, not run by the suite |
| the five test files of 6.2 | |
