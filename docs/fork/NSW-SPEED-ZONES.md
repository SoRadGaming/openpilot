# NSW speed zones: the map speed limit from Transport for NSW

In New South Wales the fork takes the map speed limit from **Transport for NSW's (TfNSW) own speed-zone data** instead
of OpenStreetMap. That data has every signed zone in the state, the 50 and 100 km/h default limits written out, and
the school zones with their hours. A matcher on the device places the car on those lines once a second. In tunnels it
keeps following the lines by dead reckoning. The result is published as the ordinary map speed limit, so the resolver,
the Speed Limit Assist (SLA) and the HUD work as before. Where TfNSW has no line, OSM is used as before.

It exists because OSM failed this car in three ways:

- **Local streets.** OSM tags few of them in Australia, and sunnypilot carried the previous road's limit onto untagged
  streets.
- **Tunnels.** In the M4 East and Rozelle tunnels, OSM snapped to the streets overhead.
- **No refresh.** The device's OSM tiles were never updated.

On the owner's 25 drives (14–27 Sep 2026), TfNSW had a limit 96 % of the time with GPS against 70 % for OSM, and
93 % against 46 % under 60 km/h. Where both had a limit they agreed 95 % of the time. The replay report is
`S:/OP/nsw-speedzones/report/REPORT.md`.

**It is live by default** (`SpeedLimitNswZones` = 2). The owner decided this on 2026-09-29, with no shadow period. It
can be switched to log-only or off at any time (see [Modes and params](#modes-and-params)).

| where | what |
|---|---|
| `openpilot/sunnypilot/mapd/nsw_zones/` | The package, pure Python and numpy. `matcher.py` is the map matcher, dead reckoning and look-ahead. `school_days.py` is the school-zone day calendar. `index.py` loads and verifies an index file. `build_index.py` is the builder (CI and PC only). `downloader.py` is the device side of the release. `tests/` holds the tests. |
| `openpilot/sunnypilot/mapd/live_map_data/nsw_map_data.py` | `NswZoneMapData(OsmMapData)`, the liveMapDataSP publisher when the mode is not off. |
| `openpilot/sunnypilot/mapd/mapd_manager.py` | Chooses the publisher and runs the downloader tick. |
| `openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/speed_limit_resolver.py` | Age fix, tunnel pass-through (states 4 and 7), settle window. |
| `openpilot/cereal/custom.capnp` | `LiveMapDataSP.nswZone @6`. **Merge hazard:** it sits on an upstream struct at the next free ordinal. If upstream sunnypilot ever adds its own `@6` to `LiveMapDataSP`, one side must be renumbered, and routes logged before that decode wrongly with the other schema. Check this on every upstream merge. |
| `openpilot/common/params_keys.h` | `SpeedLimitNswZones`, `NswZonesAutoUpdate`, `NswZonesUpdateCheck`, `NswZonesVersion`, `Offroad_NswZonesStale`. |
| `openpilot/selfdrive/selfdrived/alerts_offroad.json` | `Offroad_NswZonesStale`: data over 60 days old, or the school calendar ending within 60 days. |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/maps.py` | The "nsw zones" pair on the maps page. |
| `openpilot/sunnypilot/sunnylink/settings_ui_src/pages/cruise.yaml` | In sunnylink: the mode, both weekly updates and both "update now" controls (NSW and OSM). |
| `openpilot/sunnypilot/mapd/osm_auto_update.py` | `is_offroad()` / `is_parked()`, shared with the downloader, and `answer_request()`, the offroad gate on an OSM update request (area **SL**). |
| `openpilot/sunnypilot/sunnylink/tests/test_settings_changes.py` | `TestMapDataControls`, appended at the end of the file, marked FORK(NSW-ZONES). The file is upstream's (sunnypilot SDUI #1780, #1830): **merge hazard**, keep the block at the end. |
| **`SoRadGaming/openpilot`**: `.github/workflows/nsw-speedzones.yaml` (master) | The weekly Action that builds the index and publishes it as release assets. |

Every upstream file that was touched carries a `FORK(NSW-ZONES)` marker.

This builds on the speed-limit hardening of 2026-09-29 (area **SL** in [README.md](README.md), `SpeedLimitMapStrict`).
Its three fixes are referred to here as:

- **fix A:** a carried map limit is dropped after 10 s of "no limit" with good GPS.
- **fix B:** the map limit is frozen while GPS is lost.
- **fix C:** the maps page with the OSM update button.

All three stay active. They govern OSM's values, and fix A also drops a carried limit when NSW withholds one.

---

## Data source, license and attribution

**Source:** the TfNSW Open Data Hub dataset "Speed Zones" (CKAN package `4253a054-b377-4b5b-83d1-71385bb6ff33`,
<https://opendata.transport.nsw.gov.au/data/dataset/4253a054-b377-4b5b-83d1-71385bb6ff33>). Two of its resources are
used:

| resource | id | size | refreshed |
|---|---|---|---|
| Speed Zones - GeoJSON Format (`speed_zones.geojson`) | `bc2da977-65d2-4caa-a73a-48d0c8bf1100` | ~369 MB, 447,687 line features, WGS84 | daily, ~18:40 UTC |
| School Zones data (`schoolzones.zip` → `SchoolZones.json`) | `b6d7d02c-5625-461c-8b44-bba1a6ef3e0f` | ~3.7 MB, 3,301 zones: polygons, AM/PM times, the Western-division flag | about monthly |

The SHP, KML, JSON and CSV copies of the same data are not used.

**License:** CC BY 4.0 (Hub Terms cl. 4(b)). The attribution is fixed text. It goes in the release notes,
`manifest.json`, `ATTRIBUTION.txt`, the index file itself (`meta_json`, `attribution`), the maps page and the sunnylink
description:

> Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0. Modified: filtered, simplified,
> re-encoded. Not endorsed by Transport for NSW.

The short form, where space is tight, is "Contains data from Transport for NSW, CC BY 4.0, modified". Rules that follow
from the Hub terms:

- No TfNSW logos anywhere, and nothing that implies TfNSW endorses the fork (cl. 6(c)/(d)).
- The Hub wants its users registered (cl. 2(c)). The owner is the registered user; the build plan made registration a
  condition of P0. The Action downloads anonymously from the public links, the same ones the Hub's own download buttons
  use.

The school-day calendar in `school_days.py` also carries:

- Term dates © State of New South Wales (Department of Education), CC BY 4.0.
- Public holidays © State of New South Wales, CC BY 4.0.

`ATTRIBUTION.txt` repeats both.

**Not in the data.** Roadworks and temporary limits are missing: Live Traffic Hazards is event based and needs an API
key. Variable (gantry) zones carry only a single static value. The layer has no road names and no z-levels.

**Never query a TfNSW service from the car.** The public ArcGIS FeatureServer would learn where the car is. The device
only ever downloads the three release files below.

---

## How the data reaches the car

```
TfNSW Open Data Hub ──(weekly, GitHub Actions on SoRadGaming/openpilot)──► build_index.py + gates
        │                                                                       │
        │                                              release nswzones-YYYY-MM-DD  (kept)
        │                                              release nswzones-latest      (moved to the newest)
        │                                                   nsw_zones.npz · manifest.json · ATTRIBUTION.txt
        ▼                                                                       │
 (nothing from the car ever goes here)                  comma, parked, Wi-Fi ◄──┘  manifest first, then the index
                                                          /data/media/0/nswzones/   → matcher in mapd_manager
```

### The Action: `.github/workflows/nsw-speedzones.yaml` on SoRadGaming/openpilot master

The Action lives in SoRadGaming/openpilot, next to `mirror-sunnypilot.yaml`, and **not in this repo**. A workflow file
here would ride the mirror into the `sunnypilot` branch, and GitHub's Actions token may not push workflow files. It
runs **Sundays 09:23 UTC** (19:23 AEST / 20:23 AEDT), well away from TfNSW's daily rebuild at ~18:40 UTC. It can also be
started by hand. The comment at the top of the file is the full reference. In short:

1. **Sparse checkout of `SoRadGaming/sunnypilot@master`**: only `openpilot/sunnypilot/mapd/nsw_zones/` and
   `openpilot/common/` (for the package's `__init__.py` chain). numpy is pinned to this repo's `uv.lock`, the same
   version as on the device.
2. **The builder's unit tests.** These are every `tests/test_*.py` whose imports stay inside `build_index`, `index`,
   `school_days`, `matcher` and `tests/synth.py`. The downloader's tests need cereal and params, so they run in this
   repo's CI instead. A failing test means nothing is published.
3. **Finds the previous release.** It uses `nswzones-latest`. If that is missing or has no manifest, it falls back to
   the newest dated release. Its `manifest.json` feeds the ±5 % feature-count gate.
4. **Downloads both files.** Their current URLs come from CKAN `package_show`, looked up by resource id; the known
   direct URLs are the fallback. The files are checked to be a zip and JSON, not an HTML error page, and their sizes are
   compared with CKAN's.
5. **`build_index.py`.** It exits 2 when a gate fails, and then nothing is published and the run is red. The gates:
   - every Type, Direction, Status and Speed is a known value, and every geometry is a (Multi)LineString;
   - at least 400,000 base line parts, and between 1,000 and 20,000 school records;
   - the feature count is within ±5 % of the previous release;
   - the school calendar covers today + 30 days;
   - the self-test: the written file decodes exactly, and on 300 random lines the matcher publishes the right limit at
     least 85 % of the time and a wrong one at most 5 %;
   - **the REVIEW gates**, against the previous release. They catch the changes that could *raise* a limit the car
     publishes (SLA follows a new limit of 80 or more by itself) or drop school zones, which a ±5 % gate on 447k
     features cannot see:
     - school zones, or School lines, more than ±5 % from the previous release;
     - any change in the number of Variable lines (each one drawn over a lower static zone publishes the higher value);
     - base lines whose limit went **up**: more than 20 km in total, or any at all to 90+ km/h (lines are matched by
       identical geometry against the previous release's index);
     - more than 50 km of new or re-drawn 90+ km/h lines.

     A weekly run never publishes past them. Read the review section of the run summary (it lists the raised lines
     with their location); if the change is right, run the Action by hand with `force=true`, which lets them through
     and records `review.allowed_by_hand` in the manifest.
6. **Unchanged data is not republished.** The file's sha256 changes with every build, because it embeds the data version
   and the calendar's build time. So the Action compares the decoded arrays with those two fields left out. If nothing
   else changed, it publishes nothing, devices download nothing, and the run is green. When it does publish, the notes
   say what changed: feature counts by type, school zones, calendar coverage, and which parts of the index changed.
7. **Publishes, in a second job.** The build job (read-only token) hands the three files to a publish job (write
   token) that has no checkout and runs none of sunnypilot's code: it checks the files with a verifier written into the
   workflow itself (size, sha256, format, every member's CRC, the metadata inside the index, the attribution), then
   publishes. The dated release `nswzones-<data version>` goes first, then `nswzones-latest`. An existing release is
   updated in place: the index first, then `ATTRIBUTION.txt`, then `manifest.json`. A device that looks part way through
   either sees the old sha256 or a pair that fails its sha256 check. Either way it keeps what it has. New releases are
   created with `--latest=false`, so they never become the repo's "Latest release". Tags point at SoRadGaming/openpilot
   master, never into the `sunnypilot` branch the device fetches, and never `release/*`.
8. **Checks the result like a device.** It downloads the three files of `nswzones-latest` anonymously and runs the same
   verifier on them.

By hand:

```bash
gh workflow run nsw-speedzones.yaml -R SoRadGaming/openpilot                          # a normal run
gh workflow run nsw-speedzones.yaml -R SoRadGaming/openpilot -f dry_run=true          # build and gate, publish nothing
gh workflow run nsw-speedzones.yaml -R SoRadGaming/openpilot -f force=true            # publish even if unchanged; lets REVIEW findings through
gh workflow run nsw-speedzones.yaml -R SoRadGaming/openpilot -f skip_count_gate=true  # no ±5 % gate (see below)
```

Use `skip_count_gate` only after checking that TfNSW's data really changed that much, for example after a long pause.
The run summary lists the inputs, the gates, the decision and the published tags.

**First deployment**, in this order:

1. The package must be on this repo's `master`, because the Action builds from there.
2. Add the workflow file to SoRadGaming/openpilot `master`.
3. Start it with `-f dry_run=true`. The summary shows whether GitHub's runner could download from the Hub and whether
   every gate passed.
4. Start it again without inputs. The first release has no previous release, so the ±5 % and REVIEW gates are
   skipped and the builder says so. Check that the release page shows the attribution.
5. On the car, parked on Wi-Fi, the first download happens by itself. The maps page then shows the data date, or you
   can press **update**.

**Credentials:** none. The workflow's `GITHUB_TOKEN` creates and edits the releases, and only the publish job gets
write access (`contents: write`); the build job, which runs code from this repo, has `contents: read`. Two jobs because
inside one job any step can change the environment of the later ones (`$GITHUB_ENV`, `$GITHUB_PATH`), so "only the gh
steps get the token" would be no boundary. Actions are pinned by commit SHA, and numpy is installed with
`--require-hashes` from the hashes in this repo's `uv.lock`. Leave the repo setting "immutable releases" **off**: it
would stop `nswzones-latest` from being updated.

**Release assets** (device URL base `https://github.com/SoRadGaming/openpilot/releases/download/nswzones-latest/`):

| asset | what |
|---|---|
| `nsw_zones.npz` | The index, ~22 MB, deflate plus delta coding (layout in the `build_index.py` docstring). It is format 2, which embeds the school calendar (`calendar_json`) and the data version. |
| `manifest.json` | ~1.6 KB. The device reads it first. It holds `format_version`, `sha256`, `bytes`, `data_version`, `attribution`, feature and type counts, calendar coverage, source file sizes and sha256s, and gate results. |
| `ATTRIBUTION.txt` | The attribution and licenses. |

The **data version** is the UTC date of the build that published new content, for example `2026-09-29`. It is what the
maps page shows.

### The device: `nsw_zones/downloader.py`, run from `mapd_manager`

- **When.** The first download is automatic as soon as the mode is not off; the index is ~22 MB. After that the device
  checks weekly (`NswZonesAutoUpdate`, default on), counted from the last successful check. The maps page button, or
  "Update NSW Speed Zones Now" in sunnylink, forces a check (`NswZonesUpdateCheck`).
- **Where.** Automatic checks (the first download and the weekly one) run only **parked**: offroad and no panda
  ignition, settled for a minute - the same rule as the OSM auto-update - and only on **unmetered** Wi-Fi or ethernet.
  A forced check needs only **offroad** (deviceState heard, alive and not started) and any network: pressing it is the
  owner's choice of 22 MB, and offroad nothing is controlling the car, so **Always Offroad with the car on counts**. A
  request that arrives onroad is answered `offroad only` in `status.json` and cleared; it does not run later. A failed
  automatic check waits an hour before the next.
- **Cancelling.** Going onroad (deviceState started) cancels any check in flight, and nothing is installed. Turning the
  ignition on also cancels an **automatic** check - automatic stays parked-only - but not one somebody asked for.
  **OSM is different:** mapd has no cancel, so an OSM download started offroad keeps running after the car drives off,
  on whatever network is up (see the OSM button below).
- **Loading.** A new index replaces the running matcher, and with it the matcher's state, so it is loaded only
  **offroad**: at once when the check ran offroad, or when the device is next offroad if the install finished after it
  went onroad (its last steps cannot be cancelled).
- **How.**
  1. It reads `manifest.json` first. Nothing more is downloaded if its sha256 is the installed one, or if its format
     version is not one this code reads. After a format bump, a car on older software keeps the data it has until its
     software is updated.
  2. It streams the index into a staging directory with a byte cap and a running sha256, then verifies size, sha256,
     format and attribution.
  3. It converts the index to an uncompressed, 64-byte-aligned copy that the matcher memory-maps: ~68 MB on disk,
     almost no RAM, instant load.
  4. It smoke-tests that copy with the matcher.
  5. It installs it as `/data/media/0/nswzones/<data_version>_<sha12>/`. `current.json`, written atomically, names the
     installed version. `NswZonesVersion` holds the data version, and `status.json` feeds the maps page. Starting the
     car cancels the install between any two of these steps; once the new version is switched in it is kept, and the
     running matcher picks it up at the **next park**, never mid-drive (a new matcher would lose the tunnel state).
- **Repair.** An installed copy that fails its sha256 check when mapd loads it is set aside (`current.bad.json`), so the
  device counts as having no data and downloads a fresh copy by the first-download rule.
- **Priority.** The download, decode and sha256 threads drop mapd_manager's real-time priority (SCHED_FIFO 5) first.
- **Privacy.** The device only GETs those three fixed URLs. It sends no position, route or identifier.

---

## The matcher: what the car publishes

`nsw_zones/matcher.py`, fed by `NswZoneMapData` once per mapd tick (1 Hz, the rate that was validated). Its inputs:

- position, course and accuracy from `gpsLocationExternal`, fresh within 1.5 s and with a fix. `gps_ok` also needs
  `liveLocationKalman.gpsOK`.
- speed from `carState.vEgo`.
- yaw from `degrees(deviceMotion.orientationNED.z)` while it is valid. `deviceMotion` is upstream's new name for
  `livePose`.
- the wall clock, but only while `system_time_valid()`.

The docstring at the top of `matcher.py` is the reference, and `Matcher.DEFAULTS` holds every tunable.

### Matching

- **Candidates** are base lines within clamp(2 × accuracy + 15, 20, 50) m. The cost is distance and heading error, and
  heading counts above 2.5 m/s. **One Way** lines match only along their drawn direction; the P1 review found 531 of 537
  samples follow it. Two-way lines accept either direction.
- **Base versus overlay.** School, School Bus and Wet Weather lines are drawn *on top of* an unbroken base line. They are
  never matched on their own, only applied as conditions on the base line.
- **Hysteresis.** The current line stays unless another is clearly cheaper for 1.75 s. A line cannot win a coin toss and
  then keep itself by the "same line" bonus.
- **Ambiguity.** When a line with a different limit is about as close, the limit is **withheld**: state 3, and 0 is
  published. The resolver's carried-limit timer (fix A) then drops the old value. This is "silence when unsure". One
  exception: when OSM's limit **is** NSW's own best guess, that value is published (two sources agree). On the
  Western Distributor after the city-bound Rozelle exit this turned 15 s of silence into 60.
- **A one-tick no-match** right after a match (within 3 s: a turn can reject every line by heading for one fix) keeps
  the NSW limit instead of handing over to OSM's, which used to flick through for a second (60 → 80 → 60 on the Great
  Western Hwy, two prompts).
- **Zone ends** are judged at the line's nearest point, max(3 m, accuracy/2) past its end.

### The owner's rules (2026-09-29)

| rule | what the matcher does |
|---|---|
| **Variable drawn over a static line: publish the higher.** The lower value is a peak-hour reduction. | When a Variable line is part of a co-located group (within 1.5 m and 10°, and also present 15 m back - itself, or a Variable line of the same limit where TfNSW drew the zone as two lines end to end), the **higher** limit of the group is published, whichever line carries it. V90 over P80 gives 90, and V80 over P90 gives 90. This applies **everywhere in NSW**, not only on the M4 (13.7 km of Variable-over-static in the data, P1 section 7), in live matching, dead reckoning and the look-ahead. A Variable line that only branches off here is not on top, and without a heading (below 2.5 m/s) a One Way Variable line is not either, unless it is the line already being followed. Co-located static lines that disagree are a tie and are withheld. |
| **Tunnels: keep going, do not go silent.** They are mostly 90, and 80 on the merges. | TfNSW draws the tunnels, and Sydney's motorway tunnels are Variable lines. When GPS is lost, the matcher **dead-reckons** along them (below) and publishes the followed line's limit. |
| **On-ramps: the road's own zone continues to the merge.** | No special rule. TfNSW's value is published; for example, The Northern Road ramp onto the M4 reads 70 until the merge. |

### Tunnels: dead reckoning (state 4)

When GPS is lost (no fix, `gpsOK` false, or accuracy over 25 m) within 10 s of a confident match:

- **Seeds.** Every line within 40 m of the last fix that runs within 35° of the course. During the first 600 m, lines
  found along the track reconstructed from yaw and speed join too, because TfNSW's tunnel lines need not touch the line
  matched at the portal.
- **Walking.** Each hypothesis walks the travelled distance (speed × time) along its line and on through every
  continuation and branch. Where nothing else continues, it bridges a gap of up to 100 m to a line of 60 km/h or more.
- **Scoring.** The car's heading change over a 250 m sliding window is compared with each path's, at odometry scales
  from 0.97 to 1.03. Heading *change* is used because the gyro's absolute yaw drifted 7–14° over 440 s on the owner's
  drives. In the first 1.5 km, a weak lateral term against the dead-reckoned track is added.
- **Tunnel priors.**
  - Local streets (under 60 km/h and not Variable) are not followed past 600 m.
  - Paths off Variable lines slowly lose.
  - A path that runs the wrong way against a One Way line drawn on it is dropped.
- **Publishing.** The followed line's limit is published when every hypothesis within 12 of the best cost agrees. At an
  unresolved branch the **last limit is held** while a contending branch still has it, for up to 2 km. Once **no**
  contending branch has it (the line the car was on has ended or been left), the **lowest** contending branch is
  published: never a limit no candidate road has. (Publishing the lowest contender at every disagreement was tried: on
  the M4 East replay contenders disagree for a tick or two all the way, and it flipped 90/80 every second and dipped to
  60 - 30 SLA prompts in the tunnels against 10.) Without yaw, only the matched line is followed, as in P1.
- **When it ends** (no path fits the heading, every path ends, paths disagree past 2 km, 12 km or 30 min, an error) and
  GPS is still lost: **state 7**, which publishes the last value dead reckoning published - by the rule above never
  above a branch that was still contending - until GPS returns. It is its own state so the resolver passes it through
  instead of re-freezing an older value, and OSM's surface street never gets through. Publishing nothing instead would
  change nothing (the resolver carries the last limit anyway), and clearing the limit would send the car to its set
  speed.
- **GPS return.** A fix is trusted again at accuracy ≤ 10 m, or at two fixes in a row ≤ 15 m. The line dead reckoning
  reached is the continuity prior for the first match.
- **School lines are not applied while dead reckoning in a tunnel** (on a Variable line, or a 70+ line more than 150 m
  into the loss): a school zone is on a surface street. See [School zones](#school-zones).
- **Resolver.** In states 4 and 7 the resolver passes the published value through instead of applying fix B's GPS-loss
  freeze (see [Resolver](#resolver)).

In the replay of the owner's four passes (1 Hz, with yaw), the published limit was never missing underground. It was
always that of the TfNSW line the car was actually on. Whether those lines match the signs is open questions 1–3 below.

| pass | what the car publishes | error when GPS returned |
|---|---|---|
| City-bound (Thu 17 Sep 11:24, Fri 25 Sep 15:29) | 90 on the M4 East main line (V90 over P80) to ~7.7 km, then 80 on the Rozelle ramp (Variable 80) to 9.66 km, then 60 for the last ~250 m before the City West Link / Anzac Bridge portal (V60) | 41–43 m |
| Outbound (Thu 17 Sep 20:21, Fri 25 Sep 20:20) | 60 for the first ~0.6 km from the Rozelle portal (V60), then 80 on the ramp to 2.3 km, then 90 on the M4 East westbound to the end (~10 km) | 25–26 m |

The same sequences come out with yaw rate instead of yaw, and with `controlsState.curvature × vEgo`.

### School zones

- A **School** overlay applies to the matched base line only when all of these hold:
  - the clock is plausible;
  - the local date is a school-zone day for the zone's division;
  - the local time is inside its AM or PM window.

  The published limit is then min(base, school), 40 as a rule.
- **Whose school zone.** An overlay that lies at least 0.25 m closer to another parallel base line than to the matched
  one belongs to that line - at any distance: St Mary's PS (Concord) is drawn 0.55-0.70 m from the M4 East line in plan
  and 0.16 m from its own street, and the first version of this rule ignored everything under 1 m, so the car read 40
  for a second inside the tunnel (route fc, a school day). A line that is the same road drawn twice (on top 15 m either
  way) does not count as another. And while dead reckoning in a tunnel no School line is applied at all.
- **Times** come from `SchoolZones.json`.
  - The builder records every time string it had to interpret (16 in the 2026-09 data) in `build_report.json`.
  - 620 School line parts join no polygon and use TfNSW's standard 08:00–09:30 / 14:30–16:00.
- **Calendar** (`school_days.py`, from TfNSW's school-zones page and the Department of Education's calendars):
  - A zone day is (a term day OR a school development day) AND Monday to Friday AND NOT a public holiday.
  - The Bank Holiday (first Monday in August) is a school day, and development days count.
  - The Eastern and Western divisions differ at the start of Term 1; `LATE_OPENING_SCHOOL` = Y means Western.
  - TfNSW's own `OP_CAL` field is wrong (Term 1 swapped, development days missing, holidays not excluded), so it is kept
    only as a cross-check.
- **Local time** uses built-in NSW DST rules, so no tz database is needed. Broken Hill is 30 min behind.
- **No valid clock:** the school state is 3 ("unknown") and the limit is **withheld** (state 3, 0 published; while
  dead reckoning the school limit is published). Publishing the base limit there would be the too-high answer.
- **A date past the calendar** (a lapsed update): every weekday is taken as a school day, so the zone still applies in
  its hours - on holidays too, the safe error. An offroad alert (`Offroad_NswZonesStale`) warns 60 days before the
  calendar ends, and when the data is more than 60 days old.
- **Coverage today** runs to 2028-01-28 (Eastern) and 2028-02-04 (Western).
  - The index carries its own copy of the calendar (`calendar_json`), and the matcher uses whichever copy, the index's
    or the code's, reaches further. A data release can therefore extend the calendar without a software update.
  - **To add a year:** paste the new verbatim strings from DoE, TfNSW and the public-holiday list into `TFNSW_TABLE`,
    `DOE_CALENDAR` and `PUBLIC_HOLIDAYS` in `school_days.py`, then run `tests/test_school_days.py`. Push to master, and
    the next Action run publishes an index with the new calendar: its `calendar_json` changed, so it counts as changed.
- **School Bus and Wet Weather** overlays are never applied. The dry value is published: in rain, at a "when wet"
  sign, the car follows the dry limit (3 Wet Weather lines in the data).

### Look-ahead

The look-ahead follows the matched line and any unique continuation, or the dead-reckoned path while the contending
hypotheses agree. It finds the first point where the published limit would change, co-location rule included,
sampling every 20 m. It also reports a school zone that will be active at the estimated arrival time. The horizon is
15 s × speed, clamped to 150–1000 m. It stops at a junction where lines with different limits continue. The result
goes out as `speedLimitAhead` / `speedLimitAheadDistance` while matched (state 2) and in `nswZone.speedLimitAhead` always.
**Nothing acts on it:** the resolver makes no early switch under NSW live (see [Resolver](#resolver)). Since
2026-10-03 the comma 4 **shows** the published one - `speedLimitAhead*`, never `nswZone`'s - when it is lower and within
15 s or 500 m ([On the comma 4 screen](#on-the-comma-4-screen-hud)); `nswZone.speedLimitAhead` is still only logged.

---

## Modes and params

| param | type, default | meaning |
|---|---|---|
| `SpeedLimitNswZones` | INT, **2**, PERSISTENT \| BACKUP | 0 off: upstream's `OsmMapData` exactly, and `nswZone` is never set. 1 log only: OSM is published and `nswZone` says what NSW would have published. **2 live**: NSW where it matches, OSM elsewhere. Switching from 0 to 1 or 2 needs a restart; changes between 1 and 2, and to 0, apply live. **The off switch is in sunnylink only** (Cruise → "NSW Speed Zones"); the device has no control for it. |
| `NswZonesAutoUpdate` | BOOL, 1, PERSISTENT \| BACKUP | The weekly check. The first download happens regardless. |
| `NswZonesUpdateCheck` | BOOL, CLEAR_ON_MANAGER_START | Set by the maps page button or sunnylink, consumed (and cleared, taken or refused) by the downloader. |
| `NswZonesVersion` | STRING, PERSISTENT | The installed data version. |

**What mode 2 publishes as `liveMapDataSP.speedLimit`:**

| state | publishes |
|---|---|
| matched (2) | the NSW limit, and its next limit |
| dead reckoning (4) | the NSW limit, no next limit (0 if it has none - never OSM) |
| dead reckoning ended, GPS still lost (7) | the last value dead reckoning published, no next limit |
| ambiguous (3) | 0, or OSM's value when it equals NSW's best guess |
| no match within 3 s of a match (1) | the NSW limit held |
| no match, or GPS lost with nothing to dead-reckon (1) | OSM |
| error (5) | OSM |
| no data file (6) | OSM |

`roadName` is always OSM's. Every NSW call is wrapped: an exception publishes OSM, sets state 5 and counts in
`nswZone.errors`. The index loads in a background thread; until it is there the state is 6 and OSM is published.

**cereal** `LiveMapDataSP.nswZone @6`: `state`, `speedLimit`, `speedLimitAhead`, `speedLimitAheadDistance`, `zoneType`
(index into `matcher.TYPES`, 255 none), `schoolZone` (0 none, 1 inactive, 2 active, 3 unknown), `matchDistance`,
`headingError`, `candidates`, `dataVersion`, `osmSpeedLimit`, `errors`, `holdDistance`, `mode`, `variable`,
`confidence`, `hypotheses`. Speeds are m/s, like `speedLimit`; `nswZone.speedLimit` is what NSW published (mode 2)
or would have (mode 1). Every route therefore records what NSW said next to what OSM said.

### Resolver

These changes apply only when the message itself says NSW is live - `nswZone.mode` = 2 - **and** it carries an NSW
state (not 0). The resolver does not read `SpeedLimitNswZones` for this: its copy lags mapd by up to 3 s, and a switch
to log-only in a tunnel would otherwise let OSM's surface-street value through the freeze. Mode 0, and logs without
`nswZone` such as process replay, keep upstream's behavior exactly.

- **Age.** The map data's age is taken from `liveMapDataSP`'s `logMonoTime`. Upstream computes
  `time.monotonic() - gps.unixTimestampMillis`, which never fires. The GPS fix's own age is not used, because it is old
  by design while dead reckoning.
- **No early switch to the next limit.** Upstream's look-ahead never fires on a car (the same time-base bug), and making
  it fire is a behavior change of its own: on the owner's four M4 East / Rozelle passes NSW's look-ahead reported lower
  limits that never came (80 or 60 ahead in the main tunnel while dead reckoning, 80 at the west portal while matched),
  and each would have dropped the limit for 1-3 s. So under NSW live the current limit is used as it is.
- **Tunnels (fix B).** In states 4 and 7 the NSW limit passes through instead of being frozen. Once NSW has matched a
  trusted fix after GPS returns (state 2), the OSM settle window is cleared.

---

## On the comma 4 screen (HUD)

Since 2026-10-03 the comma 4's speed cluster (`selfdrive/ui/sunnypilot/mici/onroad/hud_*.py`, area **HUD** in
[README.md](README.md), markers `FORK(HUD)`) draws three things from this feature. Each needs the limit on screen -
the resolver's `speedLimitLast`, source map - to **be** the NSW limit mapd published: `nswZone.mode` 2 (live) and
`round(nswZone.speedLimit)` equal to it. In log-only mode, or while the resolver holds another value, nothing below is
drawn: `nswZone` then describes a limit that is not the one shown.

| on screen | from | setting (sunnylink Visuals → HUD) |
|---|---|---|
| Two amber lamps on the sign's rim, flashing in turn about once a second, and SCHOOL under it | `nswZone.schoolZone` 2 (active); the sign shows the published limit, 40 as a rule | School Zone Lights (`HudSchoolZoneCue`) |
| The same lamps unlit (grey), no text | `schoolZone` 1 (inactive) | the same |
| Nothing | `schoolZone` 3 (unknown): nothing is published then, so there is no NSW limit on screen to mark | - |
| The electronic sign: black face, red ring, white digits | `nswZone.variable` (a Variable zone: the published value is its static maximum, and the overhead sign may show less) | Electronic Sign in Variable Zones (`HudVariableLimitSign`) |
| The next lower limit (small sign, distance and/or bar) | `liveMapDataSP.speedLimitAhead*`: what mapd published - only while matched (state 2), so **never while dead reckoning** | Next Lower Limit (`HudNextLimit`) |

The sign keeps its round shape in a school zone: the owner rejected the NSW plate shape in the mockups ("keep the same
shape as all the others"). The lamp and label drawing is one function, `hud_draw.school_cue()`, so it can be restyled
alone.

## The maps page (mici)

Settings has a **maps** page. Below the OSM pair (fix C) is a second pair, **nsw zones**:

- **The card.** Its first line is the attribution, which scrolls because it is long. It is the license's requirement,
  not a summary. Below it is the data version and its age, for example `2026-09-29 • 3 d ago`, or `none` before the first
  download. This is the data's own date, not the download date.
- **The update button.** It works whenever the device is **offroad** - Always Offroad with the car on included - and
  asks for a slide to confirm ("check zones"). A phone hotspot is fine. It shows `checking`, `downloading 40%` and
  `installing` while running. Afterwards it shows `updated`, `up to date`, `retry later`, `starting up`,
  or `failed` for an hour, or that the check was stopped; onroad it says `offroad only`. When idle its
  second line is the feature's mode: `zones live`, `zones log only` or `zones off`. The status comes from
  `status.json`.
- **The OSM update button** follows the same rule for **starting** (offroad, and a region chosen), and says
  `offroad only` onroad. It does **not** follow the NSW rule for stopping: mapd has no cancel (upstream's
  "TODO-SP: introduce CANCEL database download with mapd"), so a ~270 MB download started in Always Offroad with the
  car on keeps running if you then leave Always Offroad and drive, on whatever network is up, cellular included.
  Both buttons used to want the ignition off as well, so in Always Offroad with the car on they said
  `car must be parked`; only the automatic downloads keep that rule now.
- **A refusal made onroad is not shown offroad.** A sunnylink tap while driving is answered `offroad only` in
  `status.json`; once the device is offroad again the NSW button is enabled and shows its mode line, not that answer.
- **The "update weekly" toggle** between the two pairs is the **OSM** weekly update only. NSW's weekly check
  (`NswZonesAutoUpdate`) and the mode are set in sunnylink.

## sunnylink

Cruise → Speed Limits → Speed Limit Settings holds, under "NSW Speed Zones":

| item | param | widget | notes |
|---|---|---|---|
| Update NSW Speed Zones Weekly | `NswZonesAutoUpdate` | toggle | Dimmed (unavailable) when the mode is off. |
| Update NSW Speed Zones Now | `NswZonesUpdateCheck` | toggle, `offroad_only` | The maps page's NSW button. Dimmed (unavailable) when the mode is off, like the weekly toggle: a tap would otherwise fetch ~22 MB for a feature that is off. The device itself still takes a request in any mode (the maps page button works with zones off, to fetch data before turning them on). |
| Update OSM Maps Weekly | `OsmAutoUpdateWeekly` | toggle | The maps page's "update weekly" toggle. |
| Update OSM Maps Now | `OsmDbUpdatesCheck` | toggle, `offroad_only` | The maps page's OSM button. Needs a region already chosen. Driving off does not stop a download that has started. |

- **How sunnylink writes them.** `sunnylinkd.saveParams` writes any param that is not in its `BLOCKED_PARAMS`, whatever
  its flags, converting the value by the param's type (`utils.save_param_from_base64_encoded_string`). Both request
  params are `CLEAR_ON_MANAGER_START` BOOLs and were already writable; nothing in `params_keys.h` or `sunnylinkd`
  changed.
- **Why toggles.** The schema lists a `button` widget with an `action` field, but nothing in this tree uses one or says
  what the app does with it; a `toggle` is known to write a BOOL. The device clears the param as soon as it has taken
  the request (the downloader removes it; `update_osm_db()` writes it false), or refused it, within a second or so.
  That clear does not bump `ParamsVersion` (only `saveParams` does), so **in the app the switch stays on until the app
  next loads the settings, then reads off.** Switching it off within a second or so of switching it on cancels the
  request; after that the device has already taken it, and switching it on again asks again.
- **The same rule as the buttons, enforced on the device.** `offroad_only` greys the items out in the app while
  driving, but the device does not trust that: the NSW downloader answers an onroad request `offroad only` and clears
  it, and `osm_auto_update.answer_request()` clears an OSM request that is onroad, has no region, or arrives while a
  download is running - before upstream's `update_osm_db()`, which would start a download for any request, sees it.
  `update_osm_db()` then acts only on a request that gate checked in the same tick (`request_allowed`): a sunnylink
  write that lands between the two reads waits a tick for its check instead of starting a download unchecked.
- **What that changes upstream.** The gate runs for every user, feature on or off, so it also applies to the comma
  3/3X OSM panel's "Database Update": pressed onroad (upstream starts a download) the request is now cleared, and the
  panel shows "Downloading Maps..." for about a second and reverts, with no reason shown. `already downloading` rests on
  `OSMDownloadLocations` in `/dev/shm`: if mapd died mid-download and left it set, requests are refused until a reboot
  clears it (upstream would delete the tiles and start again). Both are accepted; the comma 4 has no such panel.

---

## Tests

| suite | covers |
|---|---|
| `sunnypilot/mapd/nsw_zones/tests/`: `test_matcher`, `test_school_days`, `test_dead_reckoning`, `test_owner_rules`, `test_lookahead`, `test_index`, `test_build_index`, `test_review_fixes` | See the list below. |
| `nsw_zones/tests/test_downloader.py` | The device side. When a check is due: the first download, the week, the button, parked, unmetered, and waiting after a failure. Only the three fixed URLs are requested. Up to date downloads nothing more, and an incompatible format is not downloaded. Damaged, truncated or oversized files never replace a good install. A forced check runs offroad with the ignition on (Always Offroad) and is refused and cleared onroad or before deviceState is heard. Going onroad cancels any download in flight, and an install between any two of its steps; ignition cancels an automatic one but not a forced one. An install finished while driving is loaded when the device is next offroad. A damaged install is set aside and fetched again. It never raises into mapd_manager. |
| `sunnypilot/mapd/tests/test_nsw_map_data.py` | The publisher. Mode 0 is `OsmMapData` byte for byte, and switching to 0 stops NSW at once. Log only publishes OSM exactly. Live publishes the NSW limit and each state's value (the held value in state 7, never OSM; OSM agreeing with NSW's best guess in state 3; the 3 s no-match hold), and carries the school code. A matcher exception gives OSM plus the error count, and a failed setup leaves a working `OsmMapData`. It also covers a missing or damaged file (set aside), reload after an install, the inputs, the stale-data alert and the capnp field. |
| `.../speed_limit/tests/test_speed_limit_nsw.py` | The resolver. Dead reckoning (4) and its ended hold (7) pass the freeze and are never re-frozen to an older value, while GPS loss without them is still frozen, and the OSM publisher keeps fix B. The message's mode decides, not the param. An NSW match ends the settle window, stale data is dropped, and there is no early switch. Log-only and off are unchanged, including upstream's age. |
| `selfdrive/ui/tests/test_maps_settings.py` | The maps page pair: both buttons gate on offroad only (no ignition), with a reason that fits the value line and matches what the device answers; an onroad refusal is not shown once offroad; `update_osm_db()` is handed `request_allowed`. |
| `sunnypilot/mapd/tests/test_osm_auto_update.py` (`TestRequestGate`, `TestUpdateOsmDb`) | An OSM request runs offroad with the ignition on, and is cleared onroad, before deviceState is heard, with no region or during a download; an error refuses every later request. The SubMaster is read on every tick, also after the weekly latch (a double that goes stale when not read, as a lapped conflated queue does). `update_osm_db()` leaves a request it was not told was checked for the next tick. |
| `sunnypilot/sunnylink/tests/test_settings_changes.py` (`TestMapDataControls`) | The four sunnylink items: where they sit, toggles over BOOL params, `offroad_only` on the "Now" pair, NSW "Now" dimmed with the mode off like its weekly sibling, the descriptions, and a real `saveParams` / `getParams` round trip showing the request written and, once cleared, read back as off. |

What the 132 builder and matcher tests cover:

- **Matching.** The P1 matching cases, the owner's rules, and the look-ahead.
- **Tunnels.** Synthetic dead reckoning:
  - main line, branch by heading, gyro drift, and no yaw;
  - an unresolved branch: the last limit while a branch has it, the lowest branch once none does, the state-7 hold,
    and the distance cap;
  - a stop mid-tunnel, a line found along the track, and a bridged gap;
  - a school zone above a tunnel (and St Mary's geometry: 0.6 m from the tunnel line, 0.16 m from its own street,
    at every tick offset), the wrong-way flag, and re-acquiring after the tunnel.
- **Calendar.** Bank Holiday, development days, public holidays, DST, Broken Hill, and the `OP_CAL` audit.
- **Index files.** Verification, including sha, size, format and a corrupt file.
- **Builder.** The CLI end to end: the gates, the ±5 % previous-manifest gate, the REVIEW gates (a raise to 90+, a new
  Variable line, School lines dropped; `--allow-review`), the calendar gate, and a standalone run with `openpilot`
  imports blocked.
- **Variable-over-static while matched.** A Variable line branching off is not on top; one drawn as two lines does not
  flicker; without a heading a One Way line is not on top unless already followed.
- **School calendar lapse and clock.** Weekdays taken as school days past the calendar; no valid clock withholds.

```bash
RAYLIB_BACKEND=headless python tools/test_runner.py openpilot/sunnypilot/mapd/nsw_zones/tests \
  openpilot/sunnypilot/mapd/tests/test_nsw_map_data.py \
  openpilot/sunnypilot/selfdrive/controls/lib/speed_limit/tests/test_speed_limit_nsw.py \
  openpilot/selfdrive/ui/tests/test_maps_settings.py
```

**Validation on real drives** was done offline on the owner's PC, and the drives never left it:

- The P1 replay of 25 drives (`S:/OP/nsw-speedzones/report/`).
- The four M4 East and Rozelle tunnel passes with yaw.
- A regression of the live matcher against P1: coverage with GPS 96.0 %, agreement with OSM 95.2 %.
- All 25 drives end to end: `NswZoneMapData` (mode 2) at mapd's real publish times, then the resolver and SLA at 20 Hz
  on the recorded plannerd inputs, strict on. After the go-live review fixes: 128 SLA prompts (mode 0: 72; recorded in
  the car: 141), 9 of them in tunnels; A-B-A flips of the published limit within 30 s: 5 (OSM alone: 12); no OSM
  fall-through between NSW matches; coverage with GPS 95.9 % (OSM 69.6 %), agreement 95.1 %; the tunnel sequences as
  in the table above, and no school zone inside a tunnel.

---

## Rebuilding by hand

**Normally:** `gh workflow run nsw-speedzones.yaml -R SoRadGaming/openpilot -f force=true`, then press **update** on
the maps page while parked.

**Without the Action**, for example if GitHub's runners cannot reach the Hub, build in WSL (numpy only; ~70 s and 3.2 GB
of RAM on the owner's PC). The builder runs from any checkout of the package directory:

```bash
cd ~/sp-merge && source .venv/bin/activate
mkdir -p /tmp/nsw && cd /tmp/nsw
curl -fLO https://opendata.transport.nsw.gov.au/data/dataset/4253a054-b377-4b5b-83d1-71385bb6ff33/resource/bc2da977-65d2-4caa-a73a-48d0c8bf1100/download/speed_zones.geojson
curl -fLO https://opendata.transport.nsw.gov.au/data/dataset/4253a054-b377-4b5b-83d1-71385bb6ff33/resource/b6d7d02c-5625-461c-8b44-bba1a6ef3e0f/download/schoolzones.zip
gh release download nswzones-latest -R SoRadGaming/openpilot -p manifest.json -D prev   # for the ±5 % gate
python ~/sp-merge/openpilot/sunnypilot/mapd/nsw_zones/build_index.py \
  --speedzones speed_zones.geojson --schoolzones schoolzones.zip --out out --previous-manifest prev/manifest.json
echo "exit $?"   # 0 = written and verified; 2 = a gate failed (out/build_report.json says which), nothing written
```

Publish the result the way the Action does. The dated release comes first. Replace `nswzones-latest`'s files one by
one: the index first, the manifest last.

```bash
DV=$(python -c "import json; print(json.load(open('out/manifest.json'))['data_version'])")
gh release create "nswzones-$DV" out/nsw_zones.npz out/ATTRIBUTION.txt out/manifest.json -R SoRadGaming/openpilot \
  --target master --latest=false --title "NSW speed zones $DV" \
  --notes "Contains data from Transport for NSW (Speed Zones, School Zones), licensed CC BY 4.0. Modified: filtered, simplified, re-encoded. Not endorsed by Transport for NSW."
for f in out/nsw_zones.npz out/ATTRIBUTION.txt out/manifest.json; do
  gh release upload nswzones-latest "$f" -R SoRadGaming/openpilot --clobber
done
```

Check the published pair as the device would:

```bash
mkdir -p dl && for f in manifest.json nsw_zones.npz; do
  curl -fL -o "dl/$f" "https://github.com/SoRadGaming/openpilot/releases/download/nswzones-latest/$f"
done
PYTHONPATH=~/sp-merge/openpilot/sunnypilot/mapd/nsw_zones python -c \
  "import index; print(index.verify_index('dl/nsw_zones.npz', 'dl/manifest.json'))"
```

On a PC, `--stored` also writes the ~68 MB memory-mappable copy for `Matcher(path, mmap=True)`.

---

## Known limits

- **The data can be wrong or late.** TfNSW says its app data "may not be up-to-date". Its forum reports misplaced and
  non-existent school zones, and limits change only once the signs are up. **The signs always apply.**
- **Variable zones read their static maximum.** The gantries may show less: at peak times, for incidents, in the
  tunnels. With a set speed of 80 or more and a new limit of 80 or more, the SLA follows by itself; below that it only
  prompts. Wet Weather zones publish the dry value. There are no roadworks or temporary limits.
- **Tunnels.** Only the M4 East and Rozelle tunnels are validated. These have not been driven: the M8, the M4-M8 link,
  the M5 East, Lane Cove, the Cross City Tunnel, the Eastern Distributor, the Harbour Tunnel and NorthConnex.
  - An unresolved branch holds the last limit while a branch has it (up to 2 km), else the lowest branch; after that,
    or at 12 km / 30 min, the last value is held (state 7) until GPS returns.
  - Dead reckoning can publish an **increase** (the outbound 80 → 90 at the Rozelle merge is one). With a set speed of
    80 or more and a new limit of 80 or more, SLA follows it by itself. A wrong branch in an unvalidated tunnel could
    therefore raise the target until GPS returns. Making increases "prompt only" was considered and not done: in SLA a
    pending prompt releases the target to the set speed, which is no safer.
  - At the M4 East west portal exit there is one ambiguous tick (0) on the first trusted fix, as in P1.
- **Wrong road.** Parallel, service, stacked and co-linear roads are told apart only by heading and continuity. A car
  park within ~5 m of a road takes that road's limit.
- **Undocumented coding.** TfNSW does not document the Variable-over-static layering or what One Way means. The rules
  here come from the data (section 7 of the P1 report).
- **Timing on the device is not measured yet.** Estimated from about 5× the PC figures:
  - ~13 ms mean per update in normal driving;
  - ~20 ms mean while dead reckoning;
  - a worst tick of up to ~0.5 s at the dense Rozelle portal in the first seconds.
- **The calendar runs out.** Coverage ends 2028-01-28 (Eastern). From **2027-12-29** the Action's calendar gate fails,
  and the weekly run goes red until the 2028 dates are added to `school_days.py`. The car warns from 60 days before the
  end (`Offroad_NswZonesStale`), and past it takes every weekday as a school day.
  **Maintenance: early December 2027**, paste the 2028 DoE / TfNSW / public-holiday strings into `school_days.py`
  (see [School zones](#school-zones)).
- **Schools.** 620 School line parts have no TfNSW polygon and use the standard times. Zone ids change between builds.
- **The Action.**
  - GitHub delays scheduled runs, and disables schedules in public repos after 60 days without activity.
  - Whether the Hub accepts downloads from GitHub's (US) runners was **not tested before the first run**. If it blocks
    them, the download step fails with a clear error; build by hand instead (above).
  - Dated releases accumulate, one per week with changes (~22 MB each). Delete old ones by hand if that ever matters.
  - The data version is the UTC build date.
- **No shadow period.** The mode went straight to live. The route logs (`nswZone` against `osmSpeedLimit`) are how to
  check it after the fact.
- **Repo-wide lint** (`scripts/lint/lint.sh`) fails on master for reasons outside this feature: ruff on the EPS-LKAS
  files (`card.py`, the mici `board.py`, `eps_lkas_flasher.py`, `eps_lkas_hook.py`) and codespell on the LinbusGateway
  comments in `custom.capnp`. The NSW files are clean.

## For the owner to check

Most important first. "The car follows" means SLA changes the speed by itself (set speed 80 or more, new limit 80 or
more); below that it only asks.

1. **Tunnels the car has not been through with this**: M8, the M4-M8 link, M5 East, Lane Cove, Cross City, the Eastern
   Distributor, the Harbour Tunnel, NorthConnex. On the first drive through each, watch that the limit on the HUD matches
   the signs, especially any *increase* underground (the car follows 80 → 90 by itself). In the route, check
   `liveMapDataSP.nswZone.state` is 4 underground, `holdDistance` grows, and there is no state 7 early on.
2. **Outbound M4 East**: 60 for the first ~600 m from the Rozelle / City West Link portal, then 80 on the ramp, then 90
   from ~2.3 km, where the car goes 80 → 90 by itself. Is the 90 sign there?
3. **City-bound**: 90 for ~7.7 km, then 80 on the Rozelle ramp for ~2 km (the car drops 90 → 80 by itself), then 60 for
   the last ~250 m. Are those the signs? And is any stretch of the M4 East main tunnel signed 80 (Haberfield ramps,
   Wattle St merges)? TfNSW draws Variable 90 over Permanent 80 there, so the car reads 90.
4. **On-ramps.** To be sure what you meant by "onramps are all the road's speed limit": on the Northern Road ramp onto
   the M4 the car says **70** (the Northern Road's limit) until the merge, **not 110**. It only asks (70 is below 80).
   Is that right?
5. **Variable = the higher value, everywhere.** Your M4 answer is applied wherever TfNSW draws a Variable line over a
   fixed one in NSW (13.7 km in the data), not only the M4. OK? Watch the gantries: when they show less, the car still
   reads the higher value and, at 80+, follows it.
6. **Great Western Hwy 60/80 change points** (P1 row 3): they cause ~10 extra prompts on your drives. And the turn from
   the Great Western Hwy into **Carlisle Ave** reads 70 for 3-4 s, then 60: is there a 70 there?
7. **Western Distributor after the city-bound Rozelle exit**: 60 (NSW's best guess, and OSM's) against a 50 line peeling
   off. The car now shows 60 when OSM agrees, and nothing for up to ~9 s where it does not. Is 60 right?
8. **Other P1 rows**: Wentworth Park Rd and Glebe St 40, Mulgoa Rd 50, M4 100/110 change points, the M4 service-centre
   off-ramp (NSW 50, OSM 40).
9. **Engaging on local streets** now asks for 50 (about 50 new prompts over your 25 drives), where OSM had no limit. OK?
10. **School zones** on roads with a service road or parallel street beside them (Great Western Hwy at Kingswood PS,
    Bringelly Rd) should still read 40 in school hours. And near Concord in the M4 East, city-bound on a school day,
    there must be **no** 40 any more.
11. **Wet Weather zones** (3 in NSW) publish the dry value; in rain the car follows the dry limit there.
12. **The off switch** is in sunnylink only: Cruise → "NSW Speed Zones" → Off takes effect at once (turning it back on
    needs a reboot). There is no control on the device.
13. **First download**: ~22 MB, by itself about a minute after parking on Wi-Fi (a phone hotspot counts unless marked
    metered). OK?
14. **The Action**: is your Open Data Hub registration in place (it downloads on your behalf)? Run it once with
    `dry_run=true`, check the Hub download worked from GitHub, then once for real and check the release shows the
    attribution. A REVIEW gate failure is not an error in the Action: read the summary, and publish with `force=true`
    only if the raised limits are right.
15. **On the device**: the maps page's nsw zones pair (attribution scrolling, "2026-09-29 • 3 d ago", `zones live`),
    and the time a dead-reckoning tick takes on the comma 4 (estimated ~20 ms mean, worst ~0.5 s at the Rozelle portal).
16. **December 2027**: a reminder to add the 2028 school dates (the car warns 60 days ahead).
