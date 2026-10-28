# COMM-ISSUE: the "Communication Issue Between Processes" alert that was the storage (push12), and CPU core 4 (push16)

Area **COMM**, markers `FORK(COMM-ISSUE)` (push12) and `FORK(COMM)` / `FORK(CPU)` (push16, section 9). Not
car-specific and not gated: every device runs it. Nothing it changes is published differently; selfdrived's checks
are untouched.

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
`uptime`; 18 since push16 split `disk_sample` from `thermal`, section 9.7): the slowest, every section over 20 ms, the
total, and the `/proc/diskstats` delta of the partition and the disk since the loop's start (reads, writes, sectors
written, write ms, in flight at the end, io ms, weighted ms). At most one line per 10 s; the loops in between are
counted (`suppressed`) and the count goes out with the next line. The first two loops (the cached git calls) are
skipped.

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

Everything above is held by a test that fails when it is broken: the second review's mutations (a synchronous
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
  whether this device has it. Not acted on here. push16 ([STORAGE.md](STORAGE.md)): the device has it, but the stalls
  follow a full CPU core 4, not the free space or the deletes; the deleter no longer deletes while driving above its
  floor.
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
| `openpilot/selfdrive/car/card.py`, `helpers.py` | push16, `FORK(CPU)`: card on core 6; `convert_carControlSP()` without `to_dict()` (9.3, 9.4) |
| `openpilot/sunnypilot/system/sensor_snapshot.py` | push16, `FORK(COMM)`: `SensorWorker`, `DeviceStateGuard`, `thermal_reads` / `thermal_msg` (9.5) |
| `openpilot/sunnypilot/system/sched_diag.py` | push16, `FORK(COMM)`: `StarvationSampler` (9.6) |
| `openpilot/system/hardware/hardwared.py`, `power_monitoring.py` | push16, `FORK(COMM)`: the workers, the guard, `HARDWARED_CORES`, `calculate(current_power=)` |
| the five test files of 9.8 | |

## 9. push16: the same alert with /data idle - CPU core 4 (2026-10-28)

Branch `storage16`, the third to sixth commits (`FORK(CPU)`, `FORK(COMM)`, their tests and these documents), the
seventh, the review's (9.11), and the eighth, the second review's (9.12); STORAGE.md is the first two and part of the
eighth. Not car-specific, not gated. Nothing here changes a control law, a limit or the ACC stand-down; the move of
card changes when its frame runs, not what it computes (9.3, 9.4, 9.8).

### 9.1 What happened

* **Route 0000015d** (push15 `2984df599`, Wed 28 Oct 01:18:08 AEDT, 59 km/h): `commIssueAvgFreq`, 0.9 s of soft
  disable, no disengagement. deviceState stopped **4.81 s** (302.36 to 307.17 s); hardwared's `slow loop section`
  line (5) named **`thermal`, 4.429 s** - the thermal-zone files in /sys. The disk was busy 220 ms of those 4455 ms
  with nothing queued at the end, mapd (the /data witness) never stopped, and iowait was at most 1.9 %.
* **Route 00000153** (Sun 25 Oct, about 15:47): a **4.12 s** gap, section **`power`, 3.70 s** (three sysfs files),
  the disk busy 120 ms of 3.73 s.
* push12's fix held on both: the loop waited on no /data write. It waited inside a sysfs read, which runs the
  driver's code under the driver's locks.

### 9.2 What is measured, and what is inferred

Measured (STORAGE.md section 1 and the d1028 diagnosis; scripts and data in the session's `storage1028` and
`core16` scratch folders):

| | |
|---|---|
| who is on core 4 | card, controlsd and selfdrived, all `config_realtime_process(4, Priority.CTRL_HIGH)` = FIFO 53 (`card.py`, `controlsd.py`, `selfdrived.py`). Nothing else; IRQ load 0.4 % |
| how full | route 157 procLog: core 4 **99.7 %** busy, card **48.4 %**, selfdrived **27.6 %**, controlsd **23.4 %**. July 60 % (27 + 14 + 19); 1 Oct 91 %; 9-19 Oct 93-95 %; 14c / 14d 96.5 / 97.7 % |
| why it rose | mostly upstream's `c01b8be96` (3 Jun, "set max cpu core frequency": both clusters capped at 1689.6 MHz, `common/hardware/comma/hardware.py` `set_power_save`), on the car from route 10f on 1 Oct with AGNOS 19.7; Python-heavy processes run 1.5-1.6 times slower under it. The fork's own openpilot-side additions are about **5 points**: route 157 replayed against upstream `a5f44653d` (same opendbc), card +3.9 %, controlsd +5.9 %, selfdrived +5.6 % per frame |
| the other cores (recent drives, medians) | 0-3: 55-71 %; 5: 85-91 % (ui 73, plannerd 15, radard 1.6); 6: about 8.5 % (camerad, CFS, 7.2 %, plus its IRQ work; `/proc/stat` reads it anywhere from 1 to 18 %, an artifact of tick sampling, 9.9); 7: about 37-38 % (modeld FIFO 54 22.5, dmonitoringmodeld 14.5, the GPU IRQ). The kernel runs `isolcpus=6,7` |
| the stalls follow it | every one of the **21** stall events since 22 Oct (11 /data episodes, 10 deviceState gaps; routes 14d, 152, 153, 156, 157, 158, 15d) came after 30 s of core 4 averaging **>= 96.8 %**. Not enough on its own: stretches of 75-178 s at full load passed with no stall (155, 15a, 15b, 15d) |
| the kernel said so on 157 | "BUG: workqueue lockup - pool cpus=4 ... stuck for 30s" up to **150 s** (225.8-346.1 s, again 406-556 s); `rcu_preempt` stalls; `kworker/4:1` runnable and not run for at least 6 minutes, inside `delayed_fput`; CPU 4's `cfq_kick_queue` and `blk_timeout_work` waiting behind it again and again; `thermal_zone_device_check` pending |
| the control loop paid too | at equal FIFO priority nothing preempts, so each of the three waited for the others: CAN -> carControl median **5.8 ms** in July, 8.8 on 1 Oct, 11.5 on 14d, **19.3 ms** on 157, where **58.6 %** of the commands sent were built from the frame before last and **5.9 %** from older ones |
| hardwared on the busy cores | not pinned (default mask 0-5); its main thread last ran on core 4 in **0.91 %** and core 5 in **3.07 %** of 55,501 procLog samples |
| not the flash | ext4 errors 0, no UFS / SCSI / I/O error in 130 kernel logs; STORAGE.md section 1 |

Inferred, not proven:

* That 157's starved CPU-4 worker is what delayed /data's I/O (a strong inference: the stalled work was CPU 4's
  block-I/O dispatch).
* **For 153 and 15d** (qlogs only, no kernel log): how a full core 4 held hardwared's sensor read. Two paths fit and
  the logs cannot tell them apart: (B) a kernel lock (a thermal zone's, the battery driver's) held by a starved
  core-4 kernel thread; or (A) a hardwared thread itself preempted on core 4 or 5 by FIFO 53 while it held the GIL,
  which stops every other hardwared thread, the publishing one too - the thermal section gives the GIL up and takes
  it back about 55 times per loop. Against (B): on 157 CPU 4's own thermal polling was stuck for 6 minutes and
  hardwared never stalled. push16 covers both without knowing which (9.5 for B, 9.6's affinity for A), and its
  diagnostics (9.6, 9.7) are built to tell them apart next time.
* Why the stalls start on 22 Oct, when mid-September builds already had core 4 saturated 33-71 % of the time with
  no gap (before push12 only a stall that overlapped a /data write could show as one).

### 9.3 card to core 6 (`FORK(CPU)`, `selfdrive/car/card.py`)

`config_realtime_process(6, Priority.CTRL_HIGH)`: the same priority, on core 6 instead of 4. From route 157's procLog
(worst case; 15d differs by about a point):

| option | core 4 | core 6 | CAN -> carControl |
|---|---|---|---|
| before (all three on 4) | 99.7 % | 8.4 % | 19.3 ms median |
| selfdrived to 6 | 72.1 % | 36.0 % | about 7.8 ms |
| **card to 6 (push16)** | **51.3 %** | **56.8 %** (about 55 with 9.4's saving) | **about 7.8 ms** |
| controlsd to 6 | 76.3 % | 31.8 % | about 9.9 ms |
| anything to 7 | - | 61-86 % on 7 | modeld (FIFO 54) takes core 7 for about 11 ms every 50 ms: rejected |

The review rebuilt these from procLog's per-process times (exact: the kernel scales them to the measured run time)
plus IRQ time, on four recent routes and three from July, with controlsd scaled up where saturation had cost it frames
(x1.032 on 157). Median / 99th percentile of 2 s windows / worst 30 s, after the move:

| route | core 4 | core 6 | core 5 (not moved) |
|---|---|---|---|
| 157 | 52.1 / 62.7 / 53.1 % | 56.7 / 70.0 / 58.4 % | 91 % median, 96.8 % worst 30 s |
| 15d (qlog, 30 s) | 51.3, max 52.3 % | 56.4, max 57.7 % | 90 %, 92.3 % |
| 14d | 51.2 / 58.2 / 53.3 % | 54.7 / 62.5 / 57.0 % | 89 %, 93.7 % |
| 155 | 49.0 / 55.9 / 52.9 % | 54.0 / 59.0 / 55.5 % | 83 %, 90.8 % |
| July 1f / 20 / 01 | 33-34 %, worst 30 s about 35 | 34-35 %, 36-37 | 67-72 % |

* card alone grew about 6 points in October; card to 6 leaves about 49 points free on core 4, selfdrived to 6 about
  28. Before the real-time load on core 4 got back to 157's (the only route of 130 with workqueue lockups) it would
  have to grow about 1.9 times; core 6 has about 1.7 times.
* card's CAN-in no longer waits behind selfdrived and controlsd; those two run one after the other anyway
  (controlsd waits for selfdriveState).
* Core 6 is camerad's, which is not real-time (`camerad/main.cc`: "doesn't need RT priority since we're using
  isolcpus"), and the camera IRQs' deferred work runs there (`kworker/6:*`, `ksoftirqd/6`, CFS too). card at FIFO 53
  preempts all of them while it runs: from CAN in to sendcan out, a July frame was busy 2.6-2.7 ms (median), 4.0-4.1
  (99.9th percentile), 6.3-8.8 ms at most; scaled by today's card (x1.76) about 4.7 ms, 7 ms and 11-15 ms of waiting
  for camerad, against a 50 ms frame. camerad keeps 18 requests queued (`VIPC_BUFFER_COUNT`) and card blocks on CAN
  every 10 ms, so a dropped frame is unlikely; and nothing else real-time has ever run on core 6. card's start (its
  imports and `Car()`, 0.65-1.19 s of CPU) runs at FIFO 53 while camerad starts its cameras (card starts 0.1-0.2 s
  after camerad; first frames at 0.8-1.6 s), which can delay camerad at the start of a drive. **Inferred, not tested on
  the device - and upstream (69e2c321e) raises `modeldLagging`, a soft disable ("TAKE CONTROL IMMEDIATELY / Driving
  Model Lagging"), once `frameDropPerc` passes 1 %: modeld's filter (10 s at 20 Hz) gives about 0.5 % per dropped
  frame, so 3 drops within a few seconds are enough.** The first drives' checks for it are strict (9.9). The fallback,
  selfdrived to 6 and card back to 4 (core 4 about 72 %, core 6 about 36 %), is ready as branch `storage16-fallback6`
  in `~/sp-long` (one commit on push16, not pushed).
* Cores 4-7 go offline when parked (`set_power_save`), so core 6 is as it was for core 4: card only runs onroad.
* `test_cpu_layout.py` holds the layout on the source: card on 6, core 4 only controlsd and selfdrived, nothing else
  real-time on 6 and camerad not real-time, so an upstream merge that moves things fails.

### 9.4 card's per-frame work: `convert_carControlSP()` without `to_dict()` (`FORK(CPU)`, `selfdrive/car/helpers.py`)

`to_dict()` was most of the function's cost. Timed directly on a PC (perf_counter, gc off as in card, 7 interleaved
runs over 5,771 carControlSP messages of route 157's segments 2, 6 and 10; the review twice, and once more here):
**54.0-58.4 us** a call before, **20.4-21.6 us** on the fast path, a saving of 33.5-36.8 us. At card's measured
device / PC ratio (about 4.8; 4.3-6.0) the whole function was **about 2.6-2.8 % of a device core** and the fast path
**saves about 1.6-1.8 points** (1.4-2.2). The first figures given here, 43 of 55 us and "about 4 %", came from the profile run (82.7 us a frame
under cProfile); the first direct timing was 61.7 against 25.6 us. The fast path reads each field
as `to_dict()` does (`_get()`, an enum by `_as_str()`) and a nested struct only when it is set: `to_dict()` leaves an
unset one out and the dataclass keeps its own defaults, which are not always capnp's (`LeadData.radarTrackId` is -1
in the schema, 0 in the dataclass). A message with `params` (a list the general path hands on as dicts), or a schema
with anything the plan does not know, runs the general path - upstream's, unchanged, renamed
`convert_carControlSP_general()`.

Proof of identity: **76,779** carControlSP messages of all 14 segments of route 157, **0 different** (field for
field, type for type, floats bit for bit); `test_convert_carcontrolsp_fast.py` (12) on segments 1, 3, 7 when the
rlogs are on the machine, all 32 set / unset combinations, every enum value, a DEPRECATED field with a value,
`params`, an unknown enum (both raise), builders, 4,000 seeded fuzz messages with NaN, infinities and -0.0; and the
process replay (9.8).

Not done, though the profile listed them (about 1.5 points together): scalar numpy in `compute_gb_honda_elesys` and
the brake / gas paths, and controlsd_ext's `fieldnames`. None is identical by construction - `np.clip` passes a NaN
bound through where `min` / `max` does not (`brake_max` is computed), `np.interp`'s edge rules would have to be copied,
and caching `fieldnames` assumes the controller's schema never changes - and core 4 no longer needs them.

### 9.5 hardwared's sensors on threads of their own (`FORK(COMM)`, `sunnypilot/system/sensor_snapshot.py`)

* **`SensorWorker`**: one section's reads on a thread of its own - `thermal` (every zone of the ThermalConfig, in
  `get_msg()`'s order; `thermal_msg()` rebuilds exactly `get_msg()`'s dict) and `power` (`power_draw`,
  `som_power_draw`). The loop asks for a sweep (`request()` never waits) and publishes the last finished one, a
  `Snapshot` replaced whole. CPython releases the GIL around open, read and close, so a worker asleep in the kernel
  holds nothing the loop needs. A read's exception still ends hardwared: it is raised on the loop.
* **`DeviceStateGuard`**, once a loop, by the snapshots' ages (normally one loop, 0.5 s):
  * up to **`FRESH_S` 1.5 s**: as always;
  * the **thermal** snapshot older: the thermal status may rise but not fall (`thermal_status()`, applied after
    upstream's band logic). The filters are fed the held readings, so they settle on the last real one and never below
    it. Only the thermal snapshot's own age counts (`Decision.stale_names`): the status comes from the temperatures,
    and the first version, which used the oldest of both, let a slow power read alone hold the status at overheated
    or critical with the temperatures new (found in review; 9.8);
  * any snapshot past **`MASK_MAX_S` 6 s**: deviceState is not sent - what a stuck read did before - and selfdrived's
    checks see a longer stall as they always did. The rest of the loop keeps running (started, shutdown, params),
    which a stuck read used to freeze.
* `PowerMonitoring.calculate(..., current_power=None)` takes the worker's reading (upstream file; `None` reads the
  sensor, as upstream).
* Why stop sending rather than send `valid=False`: stopping is what every consumer has always seen; hardwared has
  never sent an invalid deviceState, and manager's watchdog kick and the other readers were never tested with one.
* Thermal margin: across 127 routes (after each one's first minute) the filtered maximum rose at most 4.1 C in 8 s
  and 4.7 C in 13 s, the hottest was 91.0 C (route ac) against the mici's 100 C for overheated, and thermalStatus was
  ok in all 285,749 samples. The kernel's own thermal protection does not depend on openpilot.

The bench (the real `hardware_thread`, one zone read stuck D seconds in open(2) on a FIFO, a real kernel wait;
"before" is 48c366fae's hardwared imported against this tree; alerts from cereal's FrequencyTracker and alive rule at
selfdrived's 100 Hz, seconds after the read stuck):

| D (stuck) | before: gap, alerts | push16: longest gap (from), alerts | lines |
|---|---|---|---|
| 3.7 s (153's) | 4.20 s; commIssueAvgFreq at 3.71 | **0.51 s**; none | slow sensor read, stall report, masked |
| 4.8 s (15d's) | 5.30 s; commIssue 4.50, AvgFreq 4.81 | **0.51 s**; none | the same |
| 7.5 s | 8.01 s; commIssue 4.50 | 2.52 s (5.07); AvgFreq 7.60 * | + held, resumed |
| 9 s | 9.50 s; commIssue 4.49 | 4.05 s (5.06); AvgFreq 9.12 | + held, resumed |
| 12 s | 12.51 s; commIssue 4.50 | 7.07 s (5.07); commIssue 10.08 | + held, resumed |

\* The bench's loop ran at 0.52 s (its pandaStates feeder); at the car's 0.50 s the 20-sample average passes a pause up
to 3.0 s, so in the car stalls up to about 7.5-8 s should pass with no alert. Longer ones alert as before, about
`MASK_MAX_S` later.

### 9.6 hardwared off cores 4 and 5, and the starvation sampler

* **`HARDWARED_CORES = [0, 1, 2, 3]`**, set at the very top of `hardwared.main()` before any thread starts, so every
  thread inherits it (path A of 9.2). Cores 0-3 never go offline (`set_power_save` takes 4-7); athenad, the uploader
  and sunnylinkd already use the same mask. Core 6 was not used: isolated, the scheduler never balances onto it.
* **`sunnypilot/system/sched_diag.py`, `StarvationSampler`**: passive, procfs only, on its own thread every 2 s while
  driving (nothing logged parked reaches a route).
  * `/proc/<pid>/schedstat` (time on a CPU, time runnable and waiting, times run) of every per-CPU kernel thread
    (`kworker/N:M`, `kworker/N:MH`, `ksoftirqd/N`), found by a `/proc/*/stat` scan every 30 s (never `cmdline`, which
    takes the target's mmap lock): each CPU's housekeeping wait, and a thread runnable at two samples that did not
    run between them (its wait is only counted once it runs, so a stuck one would otherwise show nothing).
  * `/proc/stat`: each CPU's busy share (`busy`). It is sampled at the timer tick, and the 100 Hz and 20 Hz loops alias
    against the tick on a core that is not full: July's route 01 read core 4 at 28-52 % in every 2-minute block while
    card, controlsd and selfdrived ran 60 % of it by their own task times; 155's core 6 read 1 % for 6 minutes, then
    18 %, with camerad steady at 7 %. `/proc/schedstat` per CPU, where the kernel has it: its tasks' run time
    (`run_pct`, exact; added in review) and wait (`rq_wait_s`).
  * Once a window (60 s): the SCHED_FIFO / SCHED_RR processes' and hardwared's own threads' run time and wait.
  * Every file is optional and probed once (`caps` in the first line of each start: `pid_schedstat` true / false
    (compiled in but reading `0 0 0`) / null, `proc_schedstat`, `proc_stat`, `pressure`, `sched_debug`, `own_stack`,
    and hardwared's `affinity`, which proves the 0-3 mask took effect). Expected on this kernel (4.9, from
    agnos-kernel-sdm845's `tici_defconfig`, assumed to match the 2 Sep build): per-pid schedstat yes
    (`TASK_DELAY_ACCT`), `/proc/schedstat` and `sched_debug` no, `/proc/pressure` no (4.9).
  * Cost: about 90 small reads every 2 s (the stat and schedstat of about 45 per-CPU kernel threads) and about 600
    stat reads every 30 s, on cores 0-3.
* **The stall report** (`DeviceStateGuard`, from its own thread, once per read stuck 1.5 s, and at most one per worker
  per 10 s - a read the limit holds back is reported once it allows, if still stuck, and the others are counted in
  `suppressed`; the limit added in the second review, 9.12): the reading thread's
  state, CPU, `wchan`, schedstat and kernel stack (its own process), every hardwared thread, the sampler's view of the
  kernel threads bound to cores 4, 5 and 6 (`VIEW_CPUS`; 6 added in review, since card runs there now), and on the
  device the kernel stacks of the runnable ones by `sudo -n`, at most once per 10 minutes. A stack in a lock with a
  core-4 (or 5, or 6) kworker runnable is path B; a hardwared thread in R on core 4 or 5 is path A.

### 9.7 Lines in the routes

All `cloudlog.event(..., error=True)`, so `errorLogMessage` and the qlogs keep them.

| line | when | fields |
|---|---|---|
| `hardwared slow sensor read` | a read over 0.3 s (at most one line per 10 s, the rest counted) | `worker`, `reads` [[name, s]], `sweep_s`, `self_s` (`ran`, `waited_for_cpu`, `slept`: a read blocked in the kernel sleeps; a starved thread waits for a CPU), `cpu`, `slow_sweeps`, `suppressed` |
| `hardwared sensor stall report` | a read still running 1.5 s (at most one per worker per 10 s; a read held back is reported once that allows, if still stuck) | `worker`, `read`, `stuck_s`, `thread` (`stat`, `wchan`, `schedstat`, `stack`), `threads`, `kthreads`, `kthread_stacks`, `suppressed` (stuck reads of this worker not reported since its last report) |
| `deviceState masked a sensor stall` | snapshots fresh again after a stale spell that never stopped deviceState | `max_age_s`, `worker`, `read`, `stale_s`, `held_s` 0, `episodes`, `suppressed` |
| `deviceState held: sensors stale` | deviceState stops (once per spell) | `age_s`, `worker`, `read`, `mask_max_s` |
| `deviceState resumed` | fresh again after deviceState stopped | as masked, with `held_s` |
| `sched starvation` | a 60 s window in which a CPU's bound kernel threads waited >= 1 s in all, or one was runnable and not run >= 2 s | `cpu` {N: `busy`, `run_pct`, `kwait_s`, `kwait_max_s`, `stuck_max_s`, `rq_wait_s`}, `rt` {comm:pid: `exec_s`, `delay_s`}, `hardwared` {thread: ...}, `window`, `drive_s`, `polls`, `slow_polls` |
| `kthread runnable, not run` | as it happens, once per thread and spell (one per 10 s) | `comm`, `pid`, `cpu`, `last_cpu`, `for_s`, `suppressed` |
| `sched baseline` | the first window of each drive and every 10th, unless a CPU was flagged in it (then it is a `sched starvation`) | as `sched starvation`. The drive's first window line, whichever of the two it is, also carries `prev_drive`, and the first line after each hardwared start `caps` |
| `sched drive summary` | the drive ends (and again as `prev_drive` in the next drive's first line) | `drive_s`, `windows`, `starved_windows`, `cpu` (busy, run share, waits for the whole drive), `worst`, `polls`, `slow_polls` |

`hardwared slow loop section` (5) now names 18 sections: `disk_sample` was split from `thermal`, which no longer
reads a sensor.

### 9.8 Tests and proof

| test | holds |
|---|---|
| `system/hardware/tests/test_hardwared_sensor_stall.py` (8) | the real `hardware_thread`, one read stuck in open(2) on a FIFO, every other caller of it waiting as under a driver lock: 4.8 s thermal, 4.8 s power and 4.8 s parked power (where PowerMonitoring integrates it) each keep deviceState's gaps under 1 s with the last real readings, predict no alert, and log the slow read, the stall report (the thread asleep in the kernel) and the masked spell; statlog samples a power reading once (none while the power read is stuck) and, parked, `offroadPowerUsageUwh` integrates the worker's readings loop by loop; 10 s stops deviceState only after the mask, predicts an alert, comes back within a loop of the read, with held and resumed lines; 101 C then one reading of 40 C then a 5.3 s stall: critical throughout, and falling once readings are new; the same with ignition on and a 10 s stall: past the mask, with nothing sent, the loop runs on and keeps the device offroad with Offroad_TemperatureTooHigh up until readings are new; 101 C, then 40 C kept new while the power read sticks 5.3 s: the status falls during the stall; the sampler started, told of the drive and stopped with both workers |
| `sunnypilot/system/tests/test_sensor_snapshot.py` (27) | the guard on a fake clock (the publish rule at 0.5 / 1.5 / 1.51 / 4.9 / 6.0 / 6.01 s, the oldest worker decides, each stale snapshot named, never cooler while stale, an episode logged once, held / resumed, rate limit, one report per stuck read, at most one per worker per 10 s with a held-back read reported later, the report's sudo stacks of cores 4-6 through `_sudo_stacks`); the worker (numbering, merged requests, `request()` never waits, slow reads logged with name and time and not under 0.3 s, rate limit, a sweep as old as its start and named stale, a read's exception raised on the loop), with the gap between a sweep's publish and its logging widened to 30 ms on purpose and every wait on a condition, not a fixed sleep; PowerMonitoring with and without a given power; `thermal_msg() == get_msg()` |
| `sunnypilot/system/tests/test_sched_diag.py` (26) | the parsers on this machine's real `/proc/schedstat`, `/proc/stat` and a real `/proc/<pid>/stat` line, and on 4.9's formats; absent, malformed and unknown-version files; the probe; a runnable kworker that never runs (and one that does), with and without schedstat; the view of cores 4-6; the starvation, baseline and drive-summary lines; `run_pct` against an aliased `busy`, per window (the first and a later one) and per drive, and without `/proc/schedstat`; nothing read while parked; a pid that goes away; the real /proc |
| `sunnypilot/system/tests/test_cpu_layout.py` (6) | the core layout (9.3) and hardwared's mask: never offlined, off 4-5, set before any thread starts, a failed call harmless |
| `selfdrive/car/tests/test_convert_carcontrolsp_fast.py` (12) | 9.4 |

79 tests in all: the first version's 67, 8 from its review (9.11) and 4 from the second review (9.12).

**Against 48c366fae** every one of the first 67 tests fails: four of the five files cannot import what they test, and
the layout tests fail on the layout. With only 48c366fae's `hardwared.py`, `power_monitoring.py`, `card.py` and
`helpers.py` put back and the new modules kept, the 5 stall tests, the given-power test, the 6 layout tests and the 12
conversion tests fail; the new modules' own unit tests (42) and the upstream power path pass there, as they should.

**Deliberate breakages: 37 of 37 fail at least one test** - card back on core 4; the fast path reading unset structs,
handing enums on raw, keeping DEPRECATED fields, ignoring params, sharing a default object; the thermal, power or
battery read back on the loop; `calculate()` reading the sensor parked or ignoring the given power; no age cap;
sending whatever the guard says; the thermal rule removed or ignoring staleness; never stale; no affinity call, cores
4-5 in the mask, a thread started before the call; a read's exception swallowed; slow reads never logged or not at
error level; the held line every loop; no stall report; episodes not rate-limited; requests lost during a sweep;
colliding zone keys; the stuck detector off or ignoring runs; `/proc/schedstat`'s fields shifted; a missing file
raising; the starvation line not at error level, or never; sampling while parked; no `prev_drive`; no drive summary;
a `self` key in a line (cloudlog.event's own argument, so a TypeError on the device: the sampler's first version had
it, and its test caught it). Writing them found three holes in the first version's tests, closed in `e4560a6e5`: a
stuck read now holds every caller, as a driver lock would; the parked power path; `calculate()`'s given power.

**Process replay**, route 157, all 14 segments, radard + plannerd + controlsd + card in closed loop, every output
message compared byte for byte with logMonoTime and the wall-clock fields zeroed, 48c366fae's `card.py` and
`helpers.py` against push16's in the same tree and build: **all 655,859 output messages of 17 services identical**,
191,895 sendcan frames among them.

### 9.9 What to check on the first drives

* The drive's first sched window line - a `sched baseline`, or a `sched starvation` if a CPU (most likely 5) was
  flagged in the first minute - carries `caps` (on the first line after each hardwared start: which files this kernel
  has, and `affinity` `[0, 1, 2, 3]`) and `prev_drive`. Baselines come only in minutes with no starvation.
* **How full cores 4 and 6 are: from procLog's per-process CPU, not from `busy`.** Expected: controlsd + selfdrived
  about 51 % (core 4), card about 46-48 % and camerad about 7 % (core 6). Each `sched` line's `rt` gives the same for
  the real-time processes (`exec_s` / `window_s`), and `run_pct` is exact where `caps.proc_schedstat` is true. `busy`
  (`/proc/stat`) aliases on a core that is no longer full (9.6), so a healthy drive can show `cpu.4.busy` anywhere from
  about 30 to 80 %; ordinary tasks with the default 0-5 mask (mapd's main thread, proclogd, kswapd0) will also take
  some of core 4's freed time. Neither hurts the kernel threads.
* No `sched starvation` or `kthread runnable, not run` for CPU 4 or 6. **CPU 5 may well show them**: it is not moved,
  runs 89-91 % (9.10), and was the expected next hot spot; a line there is information, not a push16 fault.
* card's timing: not `carState.cumLagMs` itself. It is card's Ratekeeper lag, never reset, so it drifts by (100 Hz -
  the CAN rate) x 600 ms a minute, and since 1 Oct (route 10f) pandad's CAN packets arrive at 99.52-99.57 Hz (July
  100.000): 257-293 ms a minute whatever card does (1f 0, 155 257, 157 293 ms/min; pandad runs on core 3, which
  push16 does not touch). Judge card by `cumLagMs`' short-term residual - against a 2 s rolling median, on every 10th
  sample as the qlog keeps them: its p99 was 0.9 ms in July and 3.9-4.5 ms on 14d / 157 / 15d, and should be back to
  about 2 ms or less - and, with an rlog, by the latency from CAN to carControl, which should be back near 8 ms.
* **The camera on core 6, strictly** (card at FIFO 53 now preempts camerad there, 9.3), over the whole drive and its
  first 10 s on their own:
  * no `modeldLagging` ("Driving Model Lagging"), `cameraFrameRate` or `cameraMalfunction` in `onroadEvents` (qlog);
  * the **maximum** of `drivingModelData.frameDropPerc` (qlog, 2 Hz; `modelV2`'s is rlog only): 0 on 157, 14d and 155;
    July's 1f reached 0.49 once (one frame) with card on core 4; `modeldLagging` fires above 1 %, about 3 drops in a
    few seconds;
  * no camerad `frame ID skipped` or `requests skipped` (LOGE, so `errorLogMessage`, in the qlogs); its `clearing and
    requeuing` is LOGW, rlog only, and is logged once per camera as the cameras start (3 lines before the first frame
    on 157 and 155: normal; one later is not);
  * `narrowRoadCameraState` / `wideRoadCameraState` / `cabinCameraState` (upstream's new names for road, wide and
    driver): `frameId` steps of 1 in an rlog (20 in a qlog, which keeps every 20th), and `processingTime` against the
    baseline (157, 14d, 155; 99.9th percentile / maximum): narrow 23.7-23.9 / 24.1-24.3 ms, wide 24.4 / 24.7-24.8 ms,
    cabin 30.1 / 30.2 ms. A qlog keeps every 20th frame, so its "99.9th percentile" is close to the maximum of about
    900 samples.
  Any frame drop, `frameId` skip, camera event or skip line: switch to `storage16-fallback6` (9.3) at once.
  `processingTime` more than about 1 ms over the baseline maximum (narrow 24.3, wide 24.8, cabin 30.2 ms) is a watch:
  if a second drive repeats it, switch.
* Any `hardwared slow sensor read` names the read; its `self_s.slept` against `waited_for_cpu`, and the stall report's
  `thread.wchan` / `stack` and `kthreads`, say which path of 9.2 it was.
* Pass condition, after the start-up phase (selfdriveInitializing plus about 20 s: every drive shows `commIssue` /
  `commIssueAvgFreq` there, while selfdrived, posenet and locationd settle, and 14d's even named deviceState after a
  2.1 s gap at 8.6 s): no `commIssue` / `commIssueAvgFreq` listing deviceState unless a `deviceState held: sensors
  stale` line comes first; **no `modeldLagging` or `cameraFrameRate`**, and `drivingModelData.frameDropPerc` never above
  0.5 (one isolated dropped frame gives 0.495, as July's 1f had once); no `sched starvation` for CPU 4 or 6.

### 9.10 Still open

* Which of 9.2's two paths held 153 and 15d. Requested rlogs of 153 and 15d (STORAGE.md) would show the kernel log.
* Why the stalls began on 22 Oct.
* **Core 5 is now the most loaded core, and push16 does not touch it.** The UI (FIFO 53) with plannerd and radard
  (FIFO 51): 89-91 % median on 157, 15d and 14d (83 % on 155), 90.8-96.8 % in the worst 30 s, 100 % at the 95th
  percentile of 2 s windows on 157; at 90 % or more in 65 / 50 / 38 % of 30 s windows on 157 / 15d / 14d. Its
  real-time share alone (from per-process times) is 86-91 %. Headroom to saturation about x1.03-1.10, against x1.88
  for core 4 and x1.71 for core 6 after the move: a 3-10 % rise in the UI or plannerd, or any further throttling of the
  big cluster, saturates it, and then CPU 5's kernel threads starve the same way and plannerd waits behind the UI. In
  July core 5 ran 68-71 %, so the UI grew from about 54 % to 66-74 %. hardwared's move frees only 0.04-0.16 points
  there. Next step to consider, as a separate, tested change: plannerd and radard to core 4 (core 4 about 52 + 17 =
  69 %, core 5 the UI's 70 % plus ordinary tasks), or cutting the UI's cost. The sampler reports it meanwhile.
* Other sysfs reads still on hardwared's loop: `get_gpu_usage_percent()` (`system_stats`) and
  `get_screen_brightness()` (`brightness`). No slow section has named them since push12; `slow loop section` would.
* Whether to lift the big cluster's frequency cap instead (more heat; undoes an upstream choice). Not done.
* Found in review, older than push16 and left for a change of its own: `StatLogSP.__del__`
  (`sunnypilot/system/statsd.py`) calls `zctx.term()` on the shared zmq context while `comma_statlog`'s socket is still
  open, so `term()` blocks forever at interpreter exit. A test file that touches `statlog` (`test_power_monitoring.py`
  at 48c366fae, `test_sensor_snapshot.py`) never exits when run alone in one process (`tools/test_runner.py -j 1`);
  the suite's pool workers end through `os._exit`, so it does not see it. A device process using statlog could hang
  on exit the same way until manager kills it. The fix would close the socket first, or `term(linger=0)`.

### 9.11 The review (2026-10-28)

Two reviews of the first version (`10676cab5`): one of the code, one re-deriving the core loads from procLog. Neither
found a defect in control outputs, the ACC stand-down or CMBS. Each finding below was reproduced or refuted first; every
code change has a test that fails without it.

| finding | evidence | what changed |
|---|---|---|
| A flaky test: `test_slow_reads_are_rate_limited_and_counted` failed 2 in 10 alone and 4 of 5 parallel runs in review | A worker publishes `latest` and only then logs the sweep's slow reads; the test ended the log interval as soon as it saw sweep 3, while sweep 3's logging could still be running. Reproduced every time by widening that gap to 30 ms (`task_schedstat()`, called between the two, slowed): this test failed exactly as in review (sweep 3 logged, suppressed 1), and so did `test_a_slow_read_is_logged_with_its_name_and_time`, which read the line straight after the publish (20 of 20 passed alone here without the widening) | The tests wait for the logging itself (`wait_until`); the no-line test waits for the next sweep. All of `TestSensorWorker` now runs with the 30 ms gap on purpose, so a test that does not wait cannot pass by luck. The code is unchanged: logging before publishing would delay the snapshot |
| A slow power read alone held the thermal status from falling | Reproduced on the real `hardware_thread`: parked at 101 C (critical), the temperatures new at 40 C, the battery-power read stuck 5.3 s - the status stayed critical in all 11 messages of the stall | The thermal rule uses the thermal snapshot's own age (`Decision.stale_names`); the oldest snapshot still decides sending and the 6 s cap. Test: the status now falls during that stall |
| The stall report's view and sudo stacks covered cores 4 and 5, not card's new core 6 | Read from the code | `VIEW_CPUS = (4, 5, 6)`, which `_sudo_stacks` now takes as its default |
| Mutation gaps: 6 of the review's 20 mutations survived (R1 a sweep's age from its end; R2 power statlog sampled on held readings; R3 / R4 the sampler never told of drives / never started; R5 workers not stopped; R14 the parked integration fed `som_power_draw`) | Re-run here | Tests for each: a sweep as old as its start; no power sample while the power read is stuck; `offroadPowerUsageUwh` against the worker's readings; the sampler started, polling with `started` true, and the sampler and both workers stopped at the end. All 6 now fail a test, as do 9 new mutations of this review's own changes (the thermal rule back on the oldest snapshot, or never applied; every snapshot named stale, or none; the view or the sudo stacks back on 4-5; `run_pct` over a fixed 60 s, from the wait, or missing from the drive summary): **15 of 15** |
| card at FIFO 53 on camerad's core 6, and upstream's 1 % `modeldLagging` | Confirmed from the source (camerad not real-time, its IRQs on 6, `frameDropPerc > 1` a soft disable, about 0.5 % per dropped frame) and the routes (baselines in 9.9) | Not a code change: the strict camera checks and the pass condition in 9.9, and the fallback ready as branch `storage16-fallback6` |
| Core 5 is now the hot spot | Confirmed: 9.10 | Documented; the next step is a separate change |
| `/proc/stat`'s `busy` aliases on a core that is not full | Confirmed in the review's numbers (route 01: 28-52 % against 60 %; 155's core 6) | 9.9 judges by procLog and the `rt` lines; `run_pct` from `/proc/schedstat` added where the kernel has it (the probe expects it absent on 4.9) |
| The converter's cost overstated (about 4 %) | Re-timed here with the review's script: 58.4 against 21.6 us a call, a saving of 36.8 us, 1.77 points at x4.8 (the review: 33.5-36.7 us) | 9.4 and `helpers.py`'s comment: about 2.6-2.8 % of a device core, saving 1.6-1.8 points |
| `StatLogSP.__del__` hangs a single-process test run at exit | Confirmed from the review's faulthandler dump; older than push16 | Left for its own change (9.10) |

### 9.12 The second review (2026-10-28)

Three checks of `e76c253c6`, the whole branch: the code and its tests (a flake hunt of 160 file runs, ten mutations of
the review's changes), a replay against push15 (all 72 segments of routes 14d and 155 through radard, plannerd,
selfdrived, controlsd and card, the full 2984df599 tree against the full e76c253c6 tree: 4,424,185 messages, every
one of the 1,043,592 CAN frames sent identical; the 96 that differ are carParamsSP, whose NNLC model path names each
replay's worktree), and a first-drive check script (`check16.py`, in the session's scratch folder: the verdict table
of 9.9 from a route). None found a fault in steering, braking, the ACC stand-down or CMBS. Each finding was reproduced
or refuted first; every code change has a test that fails without it (on e76c253c6's code: 10 of the new or changed
tests fail, 5 failed and 5 errors).

| finding | evidence | what changed |
|---|---|---|
| The stall report had no rate limit (low) | Reproduced: a read stuck 2 s in every sweep, back to back, for an hour of loops gave 1,800 reports (one per sweep, each a few KB and a thread); every other push16 line is limited to one per 10 s | At most one per worker per 10 s (`_check_stuck`); a read the limit holds back is counted once in the next report's `suppressed` and reported itself once the limit allows if it is still stuck - the stall long enough to stop deviceState is the one worth a report. 360 an hour in the same run. Tests: three thermal reports in 30 s of 2 s stalls with `suppressed` 0 / 4 / 4 and the power worker's on its own; a held-back read reported 8.5 s into its stall |
| Two worker tests had 15-20 ms of slack against a slow thread wake-up (low) | Reproduced with an extra delay before each sweep's first read (`probe.py`): `test_request_never_waits` 0 of 3 at +20 and +50 ms, `test_a_sweep_is_as_old_as_its_start` 1 of 3 at +20 ms, 0 of 3 at +50 | Both wait until the worker is inside its stuck read instead of sleeping 50 ms / 0.6 s from the request: 3 of 3 at +0, +20, +50 and +100 ms; 8 of 8 runs of both files beside the stall tests at -j 5 |
| Mutations that passed every test: M5 (stale names aged from a sweep's end), M6 (a window's `run_pct` over the drive's span), M8 (the report asking `_sudo_stacks` for cores 4-5), M10 (the thermal rule only while deviceState is sent) (low) | Re-run here on e76c253c6: all four survive | Tests: the landed sweep named stale; the drive's second window's `run_pct` (25 %, not the mutant's 8 %); the report's sudo stacks of cores 4-6, with `sudo_stacks` on; the real `hardware_thread`, ignition on, 101 C then 40 C and a 10 s stall - past the mask the loop runs on unsent and the temperature alert stays up until readings are new. All four now fail a test. M1 (`>=` at `FRESH_S`) is equivalent and left |
| card at FIFO 53 on camerad's core 6 (medium) | Confirmed from the source (9.3); no log can rule it in or out before a drive | No code change: 9.9's camera checks (made stricter, below) and the fallback `storage16-fallback6`, rebuilt on this commit. Not done: moving `config_realtime_process(6, ...)` after `Car()` - fingerprinting would then run as an ordinary task on cores 0-5, a change to card's start no replay can check |
| A parked delete could take a file athenad was uploading (low; STORAGE.md 3) | Confirmed from athenad: four upload threads, and `UploadQueueCache.cache()` writes only the files still waiting, so the ones being sent drop out of `AthenadUploadQueue` when another upload ends; a failed upload's retry then finds the file gone (`FileNotFoundError`, caught, the request dropped) | `UploadHold`: the whole route of anything in the queue is held from routine deletes until 30 minutes after the queue last listed it, read on every pass of the deleter, driving too. Tests: the route held after it leaves the queue and released after the hold; a route listed while driving held once parked |
| A routine delete counted sunnylink's upload mark (low; STORAGE.md 3) | Confirmed from `uploaded()`; no effect today (`EnableSunnylinkUploader` is absent from route 15d's initData) | Only comma's `user.upload` counts: the owner's routes come from connect. The shorter on-device rlog window was already in the CHANGELOG ("The device keeps a little less history") |
| 9.9 expected `carState.cumLagMs` back near July's (medium) | Confirmed: it is card's Ratekeeper lag, never reset, so it drifts with pandad's CAN rate - from the parquet logs here, route 1f (July) 99.999 Hz and 0 ms a minute, 155 99.569 Hz and 257, 157 99.515 Hz and 293, each slope equal to the rate's prediction | 9.9 judges card by `cumLagMs`' short-term residual and the CAN -> carControl latency |
| 18 wording, precision and cross-reference fixes (low) | Each checked against the code, the routes or the documents | The first sched line may be a `sched starvation` (9.7, 9.9, CHANGELOG); start-up `commIssue` is normal (9.9); the camera's requeuing lines at start and a `processingTime` threshold (9.9); the kernel names only waits of 30 s or more, so 153's and 15d's rlogs can support, not prove (CHANGELOG, STORAGE); the new lines' cadence, card on core 6 not "a core of its own", "downloaded" not "on the device", core 4 about half free, the parked summary's unreadable root writers, the camera risk's numbers, the overheat level rather than the temperatures, 5 minutes not 3 after a 30-minute drive, requests held only above the floor and once they reach the device (CHANGELOG, STORAGE); cross-references (9, 9.2), core 7 about 37-38 % (9.2), about 90 reads every 2 s (9.6), hardwared off the real-time cores (CHANGELOG), "Four new things", a stray parenthesis (STORAGE 1) |
| `PowerMonitoring.calculate()`'s `put("CarBatteryCapacity")` on the deviceState loop (noted as pre-existing) | Refuted: `put()` without `block=True` is asynchronous on its own handle (3.2's table) | Nothing |

On this commit: the full suite 3498 passed (the 8 new tests among them); the guard and worker tests 20 of 20 alone and
10 of 10 beside 8 CPU burners; route 157's process replay (9.8) identical again, all 655,859 messages; mutations of
this commit's code 13 of 13 killed (5 of the stall-report limit, 8 of the deleter's hold and upload mark).
