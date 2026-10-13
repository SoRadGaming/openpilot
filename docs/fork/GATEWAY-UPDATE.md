# Fork area A: updating the EPS-LKAS gateway board from the comma

This document covers one of the three areas in which this fork (`SoRadGaming/sunnypilot`
`master` plus `SoRadGaming/opendbc` `sp-master`) differs from upstream sunnypilot. Area A is
**updating the EPS-LKAS board's firmware over the car's CAN bus, from the comma 4, with
nothing unplugged**, plus everything that area needs:

* the board's firmware identity on screen,
* the pandad integration that lends the panda to the flasher,
* the bundled firmware image,
* the steering and vibration trace that each update records.

The other two areas are documented separately in `docs/fork/`. Area B is the LKAS gateway
protocol: openpilot steering through the board, `0x500`, and the `0x700`-`0x70F` telemetry
for control. Area C is the car: HONDA_ACCORD_9G_AU / HONDA_ELESYS. Several files carry hunks
from more than one area. This document describes only the area A hunks and names the owner
of the rest.

| | |
|---|---|
| sunnypilot fork point | `a5f44653d` (upstream, merged 2026-09-27; the previous fork point was `31dc4d8e5`). Documented at the merge branch `merge/upstream-2026-09-27` (`d1a14edcb` plus the post-merge review fixes). |
| opendbc fork point | `f95f996f` (upstream, merged 2026-09-27; previously `b9712d20`). Documented at the merge `8bd6e314` plus the review fixes, which becomes the `sp-master` the sunnypilot fork pins. |
| upstream compared against | sunnypilot `refs/upstream/master` `a5f44653d`; opendbc `refs/upstream/master` `f95f996f`. Nothing upstream is outstanding. What the merge did in this area is in §12. |
| paths | Since the merge every sunnypilot path is under `openpilot/`. Python module paths (`openpilot.sunnypilot...`) did not change. |
| board firmware repo | `S:\Software\EPS-LKAS`. This repo is not a fork; all of its code is custom. It holds the bootloader, the protocol header `inc/boot_proto.h`, the marker header `inc/gw_app_id.h`, `tools/bundle_appslot.py`, `docs/CAN-UPDATE.md` and `docs/UPDATING-FROM-THE-COMMA.md`. |
| history | The feature's introduction (the page, the flasher, the pandad wiring, the update button, the `a925fdce1` review fixes) is recorded **only in the commit messages** listed in §2.3. `docs/CHANGELOG_SERIAL_STEERING.md` covers the trace and vibration work (entries 2026-09-23 and 2026-09-24) and mentions the bundles in passing in the 2026-09-22 (`577a723e`, an area B entry) and 2026-09-27 (`d995bc95`) entries. This document is the map, not the history. |

---

## 1. What the area does

The board sits behind the dashboard. Before this area existed, reflashing it meant one of
two things:

* an SWD debugger on the board itself, or
* SSH to the comma, stopping openpilot, and running the firmware repo's
  `tools/can_update.py`.

Now the board is reflashed from **Settings > gateway > update** on the comma 4. The owner
slides to confirm, and three things happen:

1. The pandad wrapper stops the `./pandad` binary.
2. It flashes the board over panda bus 0 through the board's CAN bootloader, while it owns
   the panda and nothing else does.
3. It restarts `./pandad`.

The UI process keeps running throughout, which is why the page can show live progress.

The firmware image **ships inside sunnypilot**, at
`openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin`, so updating the board's firmware is
updating sunnypilot. The update path has no download and no network code.

The board is safe to update in this way because of its relays. For the whole session its
relays are de-energised (NC). The car is wired straight through to the LKAS camera on all four
lines, so the steering runs over plain copper. The worst outcome of a failed update is a board
sitting in its bootloader with stock LKAS working (`docs/CAN-UPDATE.md` in the firmware repo,
"Why it is safe").

---

## 2. Inventory

The second board's files and commits (branch `blr`, unreleased) are in §13.6.

### 2.1 sunnypilot fork

| file | status | area A content |
|---|---|---|
| `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_flasher.py` | added, all A | The bootloader protocol, the transports, the image checks and the flash trace. It has no openpilot imports. |
| `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_hook.py` | added, all A | Glue between pandad and the flasher. Contains the params handling, the watcher thread, and the version write-back. |
| `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_appslot.bin` | added, all A | The bundled board image. Currently `883a3cd1`, 49,740 bytes, SHA-256 `0753b9f6…` (board branch `push9` `883a3cd`, on `b5f08583` - `686e4603` with the lane-change cap off. It adds the owner's 2026-10-13 decisions: the cluster alarms when the board is pinned at its ceiling with no hand for 1.5 s past the debounce, even inside the 10 s quiet period, and only an alarm that started with that ceiling run counts as its alarm (2026-10-14 review fix); and a knock clears the key cycle's K2 takeover, so after an update the board is stock until the comma is heard; board `HANDOFF.md` 0p). |
| `openpilot/selfdrive/pandad/pandad.py` | modified, all A | Imports the hook, adds the `skip_panda_reset` guard, and calls `flash_if_requested()` and `watch_for_request()`. |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/board.py` | added, all A | The Settings > gateway page: the firmware card and the update button. |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | modified, **mixed** | A: the `board_btn` row. C: the `vehicle_btn` row. |
| `openpilot/selfdrive/car/card.py` | modified, **mixed** | A: `stage_board_firmware`, `write_board_firmware`, `log_flash_trace`, their call sites, five `__init__` attributes, and an unused `import json`. C: `skip_fw_query=bool(fixed_fingerprint)`. |
| `openpilot/common/params_keys.h` | modified, **mixed** | A: the seven `EpsLkas*` keys. C: the `HondaDyn*` keys. |
| `openpilot/cereal/custom.capnp` | modified, **mixed** | A: `CarStateSP.LinbusGateway` fields `@19`-`@26`. B: the struct itself and fields `@0`-`@18`, `driverTorqueStale`, and `CarControlSP.lateralControl`. |
| `openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py` | added, all A | 26 tests. |
| `openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py` | added, all A | 10 tests. |
| `openpilot/selfdrive/ui/tests/test_gateway_board_settings.py` | added, **mixed** | 24 tests are A. `test_lat_ready_means_lateral_is_enabled_not_merely_possible` is B (added in `6a4f1f5ea`). Two A tests also read C's `vehicle.py`. |
| `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | added, **mixed** | A: the firmware-identity block. B: the rest (the file was created in `d11d2c9a8` for B's `lateralControl`). |
| `docs/CHANGELOG_SERIAL_STEERING.md` | added | History; see the header table for what it does and does not cover. |

### 2.2 opendbc fork (`opendbc_repo`)

| file | status | area A content |
|---|---|---|
| `opendbc/dbc/generator/honda/_sunnypilot_linbus_gw.dbc` | added, **mixed** | A: `BO_ 1799 GW_VERSION` and `BO_ 1807 GW_BUILD`. B: `0x500`, `0x700`, `0x704`, `0x70B`. `GW_BUILD.BUILD_SP_FRESH` is a B signal on an A frame. |
| `opendbc/dbc/generator/honda/honda_accord_au_2015_can.dbc` | added, C | Only its `CM_ "IMPORT _sunnypilot_linbus_gw.dbc";` line matters to A. |
| `opendbc/car/honda/carstate.py` | modified, **mixed** | A: `("GW_VERSION", float("nan"))` and `("GW_BUILD", float("nan"))` in `get_can_parsers()`. The rest belongs to B and C. |
| `opendbc/sunnypilot/car/honda/carstate_ext.py` | modified, **mixed** | A: `_update_linbus_firmware()` and its call. B: the other decoders, plus the `ret_sp` parameter added to `update()`, which A depends on. |
| `opendbc/car/structs.py` | modified, **mixed** | A: the eight `fw*`/`boardUid` fields of `CarStateSP.LinbusGateway`. B owns the rest. |

### 2.3 Commits

sunnypilot:

| commit | what it did |
|---|---|
| `2dd8827d5` | Added the page, the identity latch in card, and the three board params (`EpsLkasBoardVersion`, `EpsLkasBoardBuild`, `EpsLkasBoardSeenAt`). Bumped the `opendbc_repo` pin to `124465ca` (the decoder). |
| `22acfb5ec` | Added the flasher module, proven on the bench. |
| `28f97ae99` | Wired the flasher into pandad. Added `EpsLkasFlashRequested`, `EpsLkasFlashProgress` and `EpsLkasFlashState` to `params_keys.h`. |
| `c489e1bc3` | Added the update button. |
| `a925fdce1` | Adversarial-review fixes, among them `SAFETY_ELM327` 15 to 3 and the str-into-JSON write in card. |
| `d7b3aff1b` | Re-bundled the image. |
| `e74935b49` | The button started showing what it would install. |
| `51c60e176` | An absent `0x70F` is no longer written as zeros. Bumped the `opendbc_repo` pin to `2619b404` (`fwBuildValid`). |
| `74a4f4d98` | Labels stopped wrapping; re-bundled the image. |
| `b648e51ca` | Ignition-off gate, font glyphs, and the button title. |
| `b3631df5d` | "up to date" disables the button. |
| `068f203cb` | Re-bundled the image (`577a723e`, the firmware that sends `BUILD_SP_FRESH`); bundling is now done by the script. Bumped the `opendbc_repo` pin to `cf583b37`. |
| `0006c3a48` | Added the steering trace; re-bundled `d43b12aa`. |
| `62f607489` | The trace reaches a route without SSH. Added `EpsLkasFlashTrace` to `params_keys.h`. |
| `f2bc2af9b` | Added the per-phase vibration measurement. |
| `fe4f4ce18` | Added the ID census. |
| `22fc22e6e` | Re-bundled the image. |
| `1f20b7b68` | Re-bundled the image. |

opendbc:

| commit | what it did |
|---|---|
| `124465ca` | Decodes `0x707`/`0x70F`: adds both frames to the DBC, the parser registration, `_update_linbus_firmware()` and the dataclass fields. |
| `2619b404` | Adds `fwBuildValid`. |
| `cf583b37` | Adds `BUILD_SP_FRESH` (bit 5) to `GW_BUILD` (`0x70F`). The signal is area B, but this is the last commit to touch an A frame's definition, so a merge of `_sunnypilot_linbus_gw.dbc` must keep it. |

### 2.4 FORK markers

Since the 2026-09 merge three area A lines carry `FORK(GATEWAY-UPDATE)`: the gateway row and
its `items.insert(3, board_btn)` in mici `settings.py`, and the watcher call in `pandad.py`.
The other area A hunks in upstream sunnypilot files (`card.py`, `params_keys.h`,
`custom.capnp`, the rest of `pandad.py`) have prose comments only. In opendbc,
`FORK(HONDA_ELESYS)` at `opendbc/car/honda/carstate.py:317` heads the gateway parser
registration that A shares with B.

To find the A hunks after a merge, grep for these strings:

* `eps_lkas`
* `eps-lkas`
* `EpsLkas`
* `skip_panda_reset`
* `board_fw`
* `flash_trace`
* `board_btn`
* `_update_linbus_firmware`
* `GW_VERSION`
* `GW_BUILD`
* `fwValid`

---

## 3. The user flow on the comma 4 (mici)

### 3.1 Where the page is

`openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` builds a row titled **gateway**
(icon `../../sunnypilot/selfdrive/assets/offroad/icon_software.png`). The row opens
`BoardLayoutMici` and is inserted with `items.insert(3, board_btn)`, after upstream's own
`items.insert(1, models_btn)` and `items.insert(5, sunnylink_btn)` and C's
`items.insert(2, vehicle_btn)`. That puts it after models (1) and vehicle (2), all counted
before the two front slots are prepended; sunnylink stays after the same base items as
upstream put it. Before the 2026-09 merge the row was at 4, after sunnylink (1), models (2)
and vehicle (3).

The panel is constructed without `back_callback`: upstream `099143ad9` removed it from its own
mici panels, and `NavWidget` pops itself on swipe-down.

The row is visible only when `board_page_visible()` is true, which means
`EpsLkasBoardVersion` is non-empty: the board has identified itself at least once. The row is
deliberately not gated on the car fingerprint, because the board is something the owner
fitted, not something the car knows about. The result is cached for `REFRESH_S = 1.0` s,
because the carousel asks every frame and `ui_state.params` has no cache.

### 3.2 The firmware card (`BoardFirmwareInfo`)

The card is 360 x 180 and holds two header/value pairs. Nothing on it is live: `card` is
`only_onroad` and this page is offroad, so the card shows what the board said the last time
it was driven.

| header | value | when |
|---|---|---|
| `firmware` | `never seen` | `EpsLkasBoardVersion` is empty |
| | `<8-hex>` | always, when known |
| | ` • <age>` appended | `EpsLkasBoardSeenAt` is set. The age is `just now` under 90 s, `N min ago`, `N h ago` or `N d ago`. It reads `unknown` if the clock went backwards (boot before NTP). |
| | ` (dirty)`, ` (stood down)` or ` (dirty, stood down)` appended | built from whichever of `build.dirty` (the hash names a commit the image was not built from) and `build.readOnly` (the INCAR_READONLY stand-down image) are set |
| `board id` | `drive once to read it` | no version yet |
| | `<6-hex uid>` or `unknown` | the uid is the folded MCU UID from `0x70F`. It reads `unknown` when `0x70F` never arrived. |
| | ` • no bootloader` appended | `build.bootloader` is false **or absent** (see §8.4) |

Two layout rules are pinned by tests:

* Every `UnifiedLabel` passes `wrap_text=False`. A wrapped header overprints the row below
  it. `board firmware` was 343 px against a 340 px limit.
* Only glyphs the font has are used. An em dash or a middle dot rendered as a wrong glyph on
  the device. The page uses only the bullet (U+2022), which is in the font's
  `EXTRA_FONT_CHARS`. The glyph test scans only `tr()` strings, and the age separator
  `f" • {age}"` (board.py line 150) is not in one, so that bullet is not checked by any test.

### 3.3 The update button (`UpdateBoardButton`)

The button is a `BigButton` titled **update**. The title must stay at 10 characters or fewer,
or it wraps and squeezes out the sub-label. The button's enabled state and sub-label are
re-evaluated every `REFRESH_S` in `refresh()`, and set imperatively with
`set_enabled(bool)`. They are never set with a callable, because `Widget.set_enabled` has one
slot and mixing the two styles means whichever ran last wins.

`_can_update()` returns `(allowed, reason)` and checks in this order. **The order is part of
the design.**

| # | test | reason shown | why it is here |
|---|---|---|---|
| 1 | `EpsLkasBoardBuild` empty, or `bootloader` false | `needs SWD once` | A board without its one-time SWD-installed bootloader ignores the whole procedure. |
| 2 | `bundled_firmware()` returned `""` | `no image` | The `.bin` is missing, or has no `APL1` marker. |
| 3 | bundled hash == board version | `up to date` | Reflashing identical firmware into the gateway that steers the car has no recovery value. A failed board would report a different version, or none (`b3631df5d`). This is tested before ignition because it is the more useful thing to tell someone standing at a parked car. |
| 4 | `not ui_state.ignition` | `ignition off` | `is_offroad()` is also true with the key out, when the board has no 12 V and the flash can only fail with "no HELLO" (`b648e51ca`). |
| 5 | `not ui_state.is_offroad()` | `use always offroad` | The update runs only offroad. With the ignition on, that means Always Offroad. |
| - | all pass | `to <bundled hash>` | The label shows what would be **installed**; the card already shows what is running. |

`bundled_firmware()` reads only the first `APP_ID_OFFSET + 16` = `0x110` bytes of the image
and decodes the marker. It imports `APP_ID_MAGIC`, `APP_ID_OFFSET` and `EPS_LKAS_APPSLOT_BIN`
from the flasher so that the screen and the bootloader cannot disagree about where the marker
is. The result is cached on the file's mtime, so a `git pull` that replaces the image is
picked up.

**`bundled_firmware()` checks only the `APL1` magic.** It does not check the origin, the
size, the initial SP or the reset vector. The button can therefore offer `to <hash>` for an
image that `check_app_slot_image()` (§6.3) will refuse. That refusal happens inside
`run_flash()`, after `./pandad` has already been stopped, and ends as
`failed: refusing this image: ...` (and see §11.2 for what the button does then). In practice
`bundle_appslot.py` refuses to bundle such an image, so this needs a hand-copied `.bin`.

Pressing the button does one of two things:

* **Not allowed:** `BigDialog("", reason)`. A disabled widget receives no clicks, so in
  practice this dialog is only reachable in the up-to-1 s between a state change and the next
  `refresh()`.
* **Allowed:** `BigConfirmationDialog("engine off\nslide to update", icon, confirm,
  exit_on_confirm=True, red=True)`. With `exit_on_confirm=False` the full-screen dialog
  stayed up forever. The words were "slide to\nupdate the gateway" until push9; since then
  they say the one thing the page cannot check itself (offroad there is no CAN): the engine
  must be off (§3.6).

The `confirm()` callback does the following:

1. It **re-checks `_can_update()` and `_busy()`**, because the dialog can sit open across an
   ignition event and the dismiss animation adds most of a second.
2. It writes `EpsLkasFlashState = ""`, `EpsLkasFlashProgress = "0"`,
   `EpsLkasFlashCancel = False` (a Cancel left from an earlier run must not cancel this one),
   `EpsLkasFlashPhase = {"p": "requested", "cancel": True}` and `EpsLkasFlashRequested = True`,
   in that order.
3. It sets `_asked = True`, shows `requested`, and (push9) opens the update screen,
   `GatewayFlashDialog` (§3.6).

### 3.4 What the sub-label says while and after it runs

The rows below are evaluated top to bottom. A pending request outranks a finished one: after
a first flash, `EpsLkasFlashState` keeps saying `ok <hash>` for the rest of the boot.

| condition | sub-label | button |
|---|---|---|
| `EpsLkasFlashState == "running"` | `in <n>` during the countdown (`EpsLkasFlashPhase` `stock`), otherwise `<EpsLkasFlashProgress>%` (clears `_asked`) | disabled |
| `_asked` or `EpsLkasFlashRequested` | `requested` | disabled |
| state starts `ok ` | `updated` | per `_can_update()`, normally `up to date` |
| state is `cancelled` | `cancelled` | per `_can_update()` |
| state starts `failed` | `failed`. The full reason is in the state param and in cloudlog. | per `_can_update()` |
| otherwise | `to <hash>`, or the refusal reason | per `_can_update()` |

`_busy()` is true when the state is `running`, when `_asked` is set, or when the request param
is set. Since BLR-v1 (branch `blr`, §13.4) it is true when that holds for **either** board, so only
one update can be in flight, and `_asked` is also cleared when the run has ended without `running`
ever being seen.

### 3.6 The update screen (push9, 2026-10-13)

The owner, after the update on 2026-10-12 (the "hum", route 134's trace): relays to stock,
a visible wait, then the update - "countdown add that" - and, for the hum, "mine is ign
powered so ign on no engine on? we can do that". The sequence already ran (the knock drops
K1 and K2; the session then sat 8 s with no data) but the page showed `0%` for at least
14.6 s of it. Now it is on screen, with the ignition on and the engine off.

`GatewayFlashDialog` (`board.py`), full screen, opened by `confirm()`. It reads
`EpsLkasFlashState`, `EpsLkasFlashPhase` and `EpsLkasFlashRequested` every `PHASE_REFRESH_S`
(0.25 s) and draws what the pure `flash_view()` makes of them: a title, a line, a progress
bar while writing, a large countdown digit (`flash_countdown()`) and a **cancel** pill bottom
right where the flasher takes it. It never drives the update: swiping it away leaves the
update running, and the button's sub-label goes on (`in 3`, `37%`).

| `EpsLkasFlashPhase.p` (written by) | title / line | Cancel | lasts |
|---|---|---|---|
| `requested` (page) | starting / stopping the car link | yes: the watcher drops the request without stopping `./pandad` | <= 1 s + `./pandad`'s exit |
| `check` (hook, then flasher) | checking / parked, engine off | yes, nothing sent yet | 1 s |
| `baseline` | checking / listening to the car | yes, nothing sent yet | 4 s |
| `click` | relays to stock / the car is on stock wiring | no | knock, HELLO, ENTER, INFO: ~0.1-0.3 s |
| `stock` | relays in stock / updating in **5 4 3 2 1** | yes, until its last 0.5 s (`CANCEL_LAST_S`): ABORT, the board keeps its firmware | 5 s (`HOLD_BEFORE_DATA_S`) |
| `erase` | erasing / making room for the new image | no | 1.4-2.6 s |
| `write` | writing x% / keep the car parked (bar) | no | ~9 s |
| `install` | installing / keep the ignition on | no | FINISH, REBOOT, the copy: ~3-4 s |
| `restart` | restarting the board / watching it start | no | the 6 s post listen |
| `ok` (hook) | done / turn the car off, then on, before driving | - | |
| `failed` (hook) | engine on / ignition off / no engine data / failed, with the reason | - | |
| `cancelled` (hook or watcher) | cancelled / the board keeps its firmware | - | |

**The gate, before a single frame is sent** (`run_flash`, in this order): the panda's own
ignition (`PandaTransport.ignition()`: `ignition_line` or `ignition_can` from `health()`;
`False` refuses with `IGNITION_OFF`, unreadable is the page's gate), stationary (the existing
1 s `moving()` on `0x158`), then the engine: the median `0x17C` `ENGINE_RPM` over that same
second, again over the last second of the 4 s baseline, and through the countdown (every
loop over the last 0.5 s, `ENGINE_WINDOW_S`, and once more just before BEGIN; started there -
during the click, the HELLO wait or the countdown - it is ABORT and `ENGINE_RUNNING`, the
installed image stays), against `ENGINE_OFF_MAX_RPM` = 400. Measured on the car: 0 with the engine off (the first 0.1 s of routes 135, 136 and 137,
before the crank), 32-188 cranking, never under 637 running (warm idle 687-706 on 134,
816-859 on 132). Over it: `failed: Turn the engine off - leave the ignition on - then update`,
which the screen shows as "engine on". No `0x17C` at all is refused in the car
(`NO_ENGINE_DATA`, "no engine data": a gate that cannot see the engine would leave the owner's
rule unenforced - the wrong bus, no PCM traffic, accessory) whenever the transport reads the
ignition (the panda); only the bench adapter, which has no car to see, is allowed through
(`run_flash(require_engine_data=...)` overrides). The countdown keeps that
proof (push9 re-review): `0x17C` seen at the gate and gone for `ENGINE_LOST_S` (1 s) - the
ignition switched off or to accessory under the update - is ABORT and `NO_ENGINE_DATA`, and the
panda's ignition reading `False` (at most every 0.2 s, and once more just before BEGIN) is ABORT
and `IGNITION_OFF`; the installed image stays. With no `0x17C` from the start (the bench) there
is nothing to lose and the countdown goes on as before.

**Cancel** is taken in `check` and `baseline` (`run_flash` returns `CANCELLED` and nothing
has been sent: the knock has not happened, the board never noticed) and in `stock`, where it is
ABORT: the bootloader closes the session and, its 800 ms listen window long past, jumps back
to the firmware it has. BEGIN has not been sent, so bank 2 is untouched - the `--dry-run` path.
From the erase on there is no cancel (`cancel: false` in the phase, the pill not drawn). The
flasher polls `should_cancel` at most every `CANCEL_POLL_S` (0.2 s), stops offering Cancel for
the countdown's last `CANCEL_LAST_S` (0.5 s), and reads it once more, unthrottled, immediately
before BEGIN - so any Cancel the screen took is ABORT, never an erase (push9 review: one at
+4.95 s used to be ignored while the screen said "canceling"). A Cancel that still loses that
race shows "too late to cancel" over erasing / writing / installing / restarting, never
"canceling". ABORT is sent with its echo awaited, then the bootloader's ACK (up to 0.5 s), so
it is on the wire before the hook's `close()` clears the panda's queues. The hook reads
`EpsLkasFlashCancel` **before** clearing it (a Cancel pressed while `./pandad` was stopping is
the owner's answer), clears it at every ending (`_finish()`), and the watcher drops a request
whose Cancel came before `./pandad` was asked to stop, so the car link never goes down for it.

**Nothing new reaches the board.** The gate, the countdown and the cancel are all comma-side;
every frame on the wire is one bootloader protocol v1 already has. An old board updates
exactly as before, and the board image needs no change for any of it.
`TestFlashScreen` (`test_eps_lkas_flasher.py`) runs the whole flasher against `SimBoard`, a
simulated firmware + bootloader on a fake clock, and pins the phases, the 5-4-3-2-1, the
5 s of stock wiring before the first data frame, every cancel point, the engine and ignition
refusals with nothing sent, and that only v1 frames are sent.

What the screen cannot know, and the trace now records (§9.2): what the board was doing at
the knock (`0x70B` STATE/REASON: split, or waiting at stock for the comma) and after the new
image started.

The UI strings are passed through `tr()`, but the fork adds no translation entries (not
verified per language).

### 3.5 Timeline of one update

1. The owner, ignition on and engine off, slides to confirm. The page shows `requested` and
   opens the update screen (§3.6).
2. Within 1 s (`WATCH_PERIOD_S`), pandad's watcher sees the request while offroad. It arms the
   panda-reset skip, then sends SIGINT to `./pandad` (or, if Cancel was pressed already, drops
   the request and writes `cancelled`).
3. pandad's loop re-enters, skips the panda reset, finds the request, clears it, reads and
   clears the Cancel, and writes `running` / `0` / phase `check`.
4. The flasher runs. It spends about 16 s in deliberate measurement phases (19 s before push9).
   The screen shows them: checking (1 s motion and engine check, 4 s baseline), relays to
   stock (the knock), relays in stock with a 5-4-3-2-1 countdown (the 5 s hold), erasing, then
   writing x% through about 194 chunks, installing, restarting the board. Until push9 it sat
   at 0% for at least 14.6 s of this. The trace of the first instrumented update spanned 30 s across all nine
   phases (`docs/CAN-UPDATE.md`); that span runs from `mark("start")` to `mark("end")` and
   does not include stopping and restarting `./pandad`.
5. As `run_flash()` returns, it hands the trace summary to the hook's `trace_out`, which
   writes `EpsLkasFlashTrace`. The hook then writes `ok <hash>`, `EpsLkasBoardVersion`,
   `EpsLkasBoardBuild` and `EpsLkasBoardSeenAt`.
6. `./pandad` is started again. The car is still in Always Offroad.
7. **Cycle the ignition before driving.** This is the standing workaround for the post-update
   idle vibration (`docs/CAN-UPDATE.md`, "The idle drone during an update"). Its cause has not
   been established.
8. On the next drive, card writes the version it decodes from `0x707` (normally unchanged) and
   emits the flash trace into that route once (§9).

---

## 4. Params

All nine are in `openpilot/common/params_keys.h`, between `InteractivityTimeout` and
`IsDevelopmentBranch`. **None carries `BACKUP`**, so none appears in a sunnylink backup.

| key | flags | type | written by | read by | why this persistence |
|---|---|---|---|---|---|
| `EpsLkasBoardVersion` | `PERSISTENT` | `STRING`, `%08x` | card `write_board_firmware()` on change; the hook after a successful flash | page card, `board_page_visible()`, `_can_update()` | card is `only_onroad` and the page is offroad. Without persistence the page could never show anything. |
| `EpsLkasBoardBuild` | `PERSISTENT` | `JSON`, a **dict**: `{dirty, appSlot, bootloader, readOnly, uid}` or `{}` | card; the hook, which merges into the existing dict so `uid` survives | page | as above |
| `EpsLkasBoardSeenAt` | `PERSISTENT` | `STRING`, unix seconds | card, at most once per 60 s; the hook | page | Lets the page say "last seen" instead of implying the value is live. |
| `EpsLkasFlashRequested` | `CLEAR_ON_MANAGER_START` | `BOOL` | UI `confirm()` sets it; the hook and the watcher clear it | watcher, hook, page | A request that survived a restart would be a request nobody made. The hook also clears it **before** attempting anything (§5.3). |
| `EpsLkasFlashProgress` | `CLEAR_ON_MANAGER_START` | `STRING`, `"0"`-`"100"` | UI (`"0"`); the hook, once per percent | page | ephemeral |
| `EpsLkasFlashState` | `CLEAR_ON_MANAGER_START` | `STRING` | UI (`""`); the hook; the watcher | page | ephemeral, but **not** cleared between updates within one boot |
| `EpsLkasFlashPhase` (push9) | `CLEAR_ON_MANAGER_START` | `JSON`, a **dict**: `{p, cancel, left?, pct?}` | UI (`requested`); the hook (`check`, and the ending); the flasher through the hook's `phase` | the update screen, the button's sub-label | ephemeral: what the screen shows now (§3.6) |
| `EpsLkasFlashCancel` (push9) | `CLEAR_ON_MANAGER_START` | `BOOL` | the screen's Cancel sets it; UI `confirm()` and the hook clear it | the watcher; the hook (read, then cleared); the flasher's `should_cancel` | a Cancel must never outlive the run it was pressed in |
| `EpsLkasFlashTrace` | `PERSISTENT` | `JSON`, a **dict** | the hook via `trace_out` | card `log_flash_trace()`, which then removes it | The flash is offroad and the emit is onroad. Clearing on manager start would lose it in between. |

**JSON-typed params take the object, never `json.dumps(...)`.** `Params.put` looks up
`PYTHON_2_CPP[(type(value), key_type)]`, which has entries for `(dict, JSON)` and
`(list, JSON)` but none for `(str, JSON)`. A pre-serialised string raises `TypeError`. Since
the 2026-09 merge the table is in upstream's ctypes implementation,
`openpilot/common/params.py` (line 82), which has the same missing `(str, JSON)` entry. Before
the merge it was in the Cython `common/params_pyx.pyx`; the rule is the same in both.

The history of this rule: the adversarial review in `a925fdce1` found card passing
`json.dumps(build)` into `EpsLkasBoardBuild` and said card **would have** died on the first
drive that decoded a `0x707`. Later comments (card.py `log_flash_trace`, and
`test_the_flash_trace_survives_to_the_next_drive`) say it did happen once; the sources
disagree. The same commit moved the write from the 100 Hz loop to `params_thread`, which has
no `try`/`except` around `write_board_firmware()`. A `TypeError` there today would not kill
card's control loop. It would end `params_thread` silently, and with it the refresh of
`IsMetric`, `ExperimentalMode`, `DynamicExperimentalControl`, the custom set-speed params and
the board writes, for the rest of that card process.

`EpsLkasFlashState` can take these values:

| value | written by |
|---|---|
| `""` | UI confirm |
| `running` | hook |
| `cancelled` | hook (a Cancel before the start, or `run_flash` returning `CANCELLED`); watcher (a Cancel before `./pandad` was stopped) |
| `ok <8-hex>` | hook, from `run_flash` |
| `failed: not while driving` | watcher or hook |
| `failed: no bundled image at <path>` | hook |
| `failed: <run_flash message>` | hook, see §6.5 |
| `failed: <ExceptionType>: <msg>` | hook, for example the `PandaTransport` constructor's `RuntimeError` |

---

## 5. pandad integration (`openpilot/selfdrive/pandad/pandad.py` + `eps_lkas_hook.py`)

### 5.1 Why pandad

The board is updated over the car's CAN bus, so the flasher needs the panda, and pandad owns
the panda at all times. The two obvious ways to take it are both wrong:

* `systemctl stop comma` also stops the UI, which kills the screen showing the progress.
* `pkill pandad` is undone by manager's `ensure_running`, and the pattern matches both the
  wrapper and the binary.

This fork uses the window that already exists. `pandad.py` owns the panda exclusively between
`flash_panda()` and `subprocess.Popen(["./pandad"])`, and then blocks in `process.wait()`.
sunnypilot already flashes third-party firmware in this window (`flash_rivian_long`).

### 5.2 The hunks in `pandad.py`

There are five:

1. `from openpilot.sunnypilot.selfdrive.pandad.eps_lkas_hook import flash_if_requested,
   watch_for_request`.
2. Before the loop: `skip_panda_reset = [False]` and
   `request_skip_panda_reset()`, which sets it.
3. At the top of each iteration: if `skip_panda_reset[0]` is set, clear it, log
   `eps-lkas: re-entry after a board flash, leaving the panda alone`, and **skip both**
   `HARDWARE.reset_internal_panda()` and `HARDWARE.recover_internal_panda()` **without
   incrementing `count`**. Otherwise run the original even/odd alternation. The reason:
   `recover_internal_panda()` drives BOOT0 high and reflashes the panda, and pressing "update
   the gateway" must not silently reflash the panda first. `count` is left alone so the
   normal alternation is undisturbed.
4. After `flash_panda(panda_serials[0])`, which guarantees the panda is out of bootstub, and
   before `Popen`: `flash_if_requested(panda_serials[0])`. Since BLR-v1 (branch `blr`, §13.3):
   `if not flash_if_requested(panda_serials[0]): flash_if_requested(panda_serials[0], target=BLR)`,
   one update session per window, with `BLR` imported from the flasher.
5. After `Popen` and before `process.wait()`: `watch_for_request(process,
   request_skip_panda_reset)`, under a `FORK(GATEWAY-UPDATE)` comment.

Since the 2026-09 merge these sit in upstream's version of the file, where `HARDWARE` comes
from `openpilot.common.hardware` and `./pandad` is started with
`cwd=os.path.join(BASEDIR, "openpilot/selfdrive/pandad")`. The loop is otherwise the same.

### 5.3 `eps_lkas_hook.py`

**`watch_for_request(process, skip_reset, params=None)`** starts a daemon thread named
`eps_lkas_watch`. The thread polls every `WATCH_PERIOD_S = 1.0` s while `process.poll() is
None`:

* If the request is set and `IsOffroad` is not set, it logs a warning, clears the request, and
  writes `failed: not while driving`. It does this only once per watcher (the `refused` flag).
  `IsOffroad` is the only onroad/offroad param upstream keeps (`ad5151b38` deleted
  `IsOnroad`); a missing or false `IsOffroad` reads as onroad, which refuses.
* If the request is set and the car is offroad, it logs, calls `skip_reset()` **first**, then
  `process.send_signal(signal.SIGINT)`, and returns. SIGINT is used because it is the signal
  pandad.py's own handler forwards "to close the relay and exit". `terminate()` is not used.
* Every exception is caught and logged.

**`flash_if_requested(panda_serial, bus=0, transport_factory=None)`** runs on **every** loop
entry and always returns normally:

1. If the request param is false or unreadable, it returns with no writes.
2. **It makes its own onroad check.** The re-entry might not be the watcher's doing; for
   example, `./pandad` could have crashed mid-drive. If onroad, it clears the request, writes
   `failed: not while driving`, and returns. The test is `not params.get_bool("IsOffroad")`.
   If `IsOffroad` cannot be read, it logs `could not read IsOffroad` and returns **without
   clearing the request**.
3. **It clears the request before anything else.** Left set, the loop would re-enter, the
   watcher would fire again, and the board would be reflashed forever with no backoff.
   `CLEAR_ON_MANAGER_START` does not protect against this, because manager does not restart
   between iterations of pandad's own loop. A retry is a fresh button press.
4. It writes `running` / `0`, loads the bundled image, builds `PandaTransport(bus=0,
   serial=panda_serial)` (or `transport_factory()` on the bench), and calls `run_flash()`
   with three callbacks:
   * `log`: `cloudlog.info("eps-lkas:" + line)`
   * `progress`: writes `EpsLkasFlashProgress`
   * `trace_out`: writes the trace summary to `EpsLkasFlashTrace` if it is non-empty
5. **On ok** it writes the following, then emits cloudlog event `eps-lkas.flashed`:
   * `EpsLkasFlashState = "ok <hash>"`
   * `EpsLkasBoardVersion = <hash>`
   * `EpsLkasBoardBuild`: the existing dict updated with `dirty` and `readOnly` from the image
     marker, and `appSlot` and `bootloader` set to `True`. **The flags must move with the
     hash**, or the page would show a new commit beside the previous image's flags.
   * `EpsLkasBoardSeenAt = now`
6. **On failure** it writes `failed: <msg>` and logs an error.
7. In a `finally` block it closes the transport.

`_existing_build()` reads the current `EpsLkasBoardBuild` and accepts either a dict or a
legacy JSON string (it `json.loads` a string), returning `{}` on anything else. The merge
preserves `uid` only if card had already decoded a `0x70F`. When it had not, the merged dict
has no `uid`, and the card reads `board id unknown` until the next drive decodes one. Through
the UI this should not be reachable, because the button requires `bootloader: true`, which
only card writes and only together with a `uid`. A hand-set request param can reach it.

### 5.4 What this code must never do

| must not | enforced by |
|---|---|
| **Raise** into pandad. An exception here means `./pandad` never starts and the car has no CAN until manager notices. | `flash_if_requested` catches everything. `run_flash` returns `(ok, msg)` and never raises. The flasher contains no `raise SystemExit` and no `sys.exit()` outside `__main__`; this rule is why it does not reuse the firmware repo's `check_app_image`/`can_update.py`, which raise `SystemExit` (a `BaseException`). Tests: `test_nothing_raises_out_of_the_flasher`, `test_run_flash_returns_rather_than_raising`, `test_hook_never_raises`. |
| **Reset or DFU-recover the panda** as a side effect | the `skip_panda_reset` guard, armed before the signal. Tests: `test_pandad_skips_the_panda_reset_on_that_re_entry`; `test_watcher_stops_pandad_offroad` asserts exactly one SIGINT and that the skip was armed, but **not their order**. The order (skip, then signal) is enforced only by the code, at `eps_lkas_hook.py` lines 126-127. To pin it, make `FakeProcess.send_signal` record whether the skip had already fired. |
| **Run onroad** | Four layers, from the outside in: the watcher's `IsOffroad` check; the hook's own `IsOffroad` check; the flasher's `moving()` check on `0x158`; the board, whose app knock handler and bootloader both NAK with `BOOT_ERR_MOVING` at 1.00 km/h or more. The UI gate is not counted as a safety layer. Test: `test_watcher_ignores_a_request_while_onroad`. |
| **Loop** | The request is cleared first. Test: `test_request_is_cleared_before_anything_is_attempted`. |
| **Stop the UI** | Only `./pandad` is signalled. |

---

## 6. The flasher (`openpilot/sunnypilot/selfdrive/pandad/eps_lkas_flasher.py`)

This is a self-contained port of the bootloader protocol. It shares no code with the firmware
repo's `tools/can_update.py`, for two reasons: that tool depends on python-can, which is not
in openpilot's lockfile, and it raises `SystemExit`.

The module imports no `can`, `usb`, `panda`, `openpilot` or `cereal` at module scope
(pinned by `test_no_hard_dependency_at_module_scope`). The transport is **injected**, so the
exact code that runs in the car also runs against a spare board on a bench candleLight.

### 6.1 The panda transport and `SAFETY_ELM327`

The constructor `PandaTransport(bus=0, serial=None)` does four things:

1. It opens `Panda(serial, cli=False)`. `cli=True` prompts on stdin when several pandas are
   attached, which would hang pandad.
2. It calls `set_safety_mode(SAFETY_ELM327, ELM327_KEEP_NORMAL_CAN)`.
3. It **reads the mode back** from `health()["safety_mode"]` and raises `RuntimeError` if
   the mode is wrong. The hook catches that error.
4. It calls `can_clear(0xFFFF)`, which discards stale queued frames before the session.

Notes on each part:

* **`SAFETY_ELM327 = 3`**, matching `opendbc/safety/declarations.h` (`#define SAFETY_ELM327
  3U`, still 3 upstream). This constant was `15` until `a925fdce1`. 15 is
  `SAFETY_VOLKSWAGEN_MQB`, and that mode had three effects:
  * its TX allowlist drops `0x710` and `0x712`, so nothing reached the board;
  * it opens the harness relay, cutting the camera off the bus;
  * the panda drops to SILENT a few seconds in.

  The visible symptom was "no HELLO - the board has no bootloader", which pointed at the
  hardware. The readback exists so that a wrong number fails loudly as a wrong number.
* **Why not `allOutput`.** `allOutput` opens the car/camera harness relay for the whole
  session, and it is compiled only under `ALLOW_DEBUG`. Honda safety modes drop
  `0x710`/`0x712`, so the flasher has to change mode.
* **`ELM327_KEEP_NORMAL_CAN = 1`.** In elm327 mode, `param == 0` switches the panda to
  `CAN_MODE_OBD_CAN2`, which remaps bus 1 onto the OBD-II port. The default param is 0, so
  the param must be non-zero.
* **8-byte frames only.** elm327 forwards only DLC 8, so `send()` pads every frame with
  `ljust(8, b"\x00")`, including the last partial DATA frame. This costs nothing: every
  length check in the bootloader is a minimum.
* **Receive.** `poll()` labels each `can_recv()` result by source: `bus` is RX, `bus + 128`
  is ECHO (the frame was actually transmitted), and `bus + 192` is REJECTED by panda safety.
  Counting the rejections (`self.rejected`) and logging the first one as
  `panda SAFETY REJECTED 0x...` is done by `Flasher._pump`, not by the transport.
* **Health.** `health()` returns `can_health(b)` for buses 0, 1 and 2 (`total_error_cnt`,
  `bus_off_cnt`, `error_passive`, `total_rx_lost_cnt`, `total_tx_lost_cnt`). This is best
  effort; a bus that raises is skipped.
* **Close.** `close()` calls `can_clear(0xFFFF)` and closes the panda. The flasher does not
  restore a safety mode; the restarted `./pandad` sets its own. The firmware repo's
  `UPDATING-FROM-THE-COMMA.md` says `./pandad` rewrites it to `NO_OUTPUT` at 10 Hz when
  offroad. That was not re-verified in this repo.

`BenchTransport(bitrate=500000)` is the bench transport, a candleLight (gs_usb, VID `1d50`
PID `606f`). It imports python-can inside `__init__`. It does one blocking read per poll: a
drain-until-empty loop cost a ~15 ms Windows timer tick per call and made a 46 KB flash take
1m44 instead of about 12 s.

### 6.2 The protocol, as the flasher speaks it

The authority is `inc/boot_proto.h` in the firmware repo. The constants are mirrored at the
top of the flasher ("Change a number there, change it here"). **Nothing checks them against
`boot_proto.h` automatically.** `test_protocol_constants_match_the_firmware` compares the
module's constants to literal values typed into the test: the `CMD_*` and `RSP_*` codes,
`MAGIC`, `CHUNK_BYTES`, `DATA_PER_FRAME`, `STATIONARY_CPH`, `APP_ID_MAGIC` and `APP_ORIGIN`.
It does not cover `PROTO_VERSION`, the CAN IDs, the NAK error codes or the HELLO status bits.
A change on the firmware side will not fail it; diff the two by hand (§12.3). The memory map,
the staging header and the power-loss behaviour are documented in `docs/CAN-UPDATE.md` in the
firmware repo and are not repeated here.

| id | direction | content |
|---|---|---|
| `0x710` `ID_HOST` | comma to board | commands |
| `0x711` `ID_BOARD` | board to comma | ACK / NAK / INFO / HELLO |
| `0x712` `ID_DATA` | comma to board | 8 raw image bytes. There is no command byte; `CMD_DATA = 0x05` is defined but never sent. |

| command | byte 0 | payload (LE) | how it is sent | answer and timeout |
|---|---|---|---|---|
| ENTER / knock | `0x01` | `EPSBOOT` (7 bytes) | fire and forget | ACK with `d[1] = 0x01` and `d[2] = PROTO_VERSION (1)`. The flasher sends ENTER repeatedly with 50 ms receive windows for up to 4 s, inside the 800 ms boot window. |
| INFO | `0x02` | none | echo-waited, 3 tries | INFO `0x82`: `d[2..3]` slot size in KB, `d[4..7]` CRC-32 of the installed slot. 2 s per try. |
| BEGIN | `0x03` | `<I` image length | echo-waited | ACK, 20 s (the board erases all of bank 2) |
| CHUNK | `0x04` | `<H` chunk index | echo-waited | none |
| DATA | (on `0x712`) | 8 bytes | echo-waited | none |
| CEND | `0x06` | `<HH` index, CRC-16/CCITT-FALSE of the chunk | echo-waited | ACK, 3 s |
| FINISH | `0x07` | `<I` zlib CRC-32 of the whole image | echo-waited | ACK, 20 s |
| REBOOT | `0x08` | none | fire and forget | a HELLO after the reset |
| ABORT | `0x09` | none | fire and forget | none. Used on a dry run, so the session does not self-reset 10 s later at an arbitrary moment. |

The board's replies have these layouts:

* **NAK** is `[0x81, cmd, err, ...]`, with `err` one of:

  | err | meaning |
  |---|---|
  | 1 | out of sequence |
  | 2 | too big |
  | 3 | CRC |
  | 4 | flash |
  | 5 | the car is not stationary |
  | 6 | chunk short |
  | 7 | version |

  For err 4, `d[3]` is the driver code and `d[4..7]` is `FLASH_SR`. `describe_nak()` renders
  all of these.
* **HELLO** is `[0x83, proto, status, git LE x4, 0]`. The status bits are `APP_OK 0x01`,
  `APP_ID 0x02`, `DIRTY 0x04` and `STAGED 0x08`.

**Flow control is the echo.** `_send()` transmits a frame and waits up to 1.5 s for one ECHO
before sending the next. Both transports report a frame only once it is on the wire, which
throttles the flasher to wire speed instead of USB speed. On gs_usb this is mandatory: the
adapter silently discards frames once its three echo slots are full.

ENTER and the knock are the exceptions. ENTER races the boot window, and after the knock the
board may stop ACKing. INFO **is** echo-waited and retried, because a fire-and-forget INFO sent
after the ENTER burst was once silently never transmitted.

**Chunking.** 256 bytes per chunk means 32 DATA frames, so each chunk is
`CHUNK + 32 x DATA + CEND` = 34 frames. The current 46,540-byte image is 182 chunks; its last
chunk is 204 bytes. Each chunk gets up to 3 attempts.

**A lost CEND ACK is not a lost chunk.** On "no ACK" the flasher re-sends only CEND first. If
the board had already programmed the chunk, re-sending the whole chunk would write into
flash that is no longer erased and fail.

The receive buffer `_rx` is a `deque(maxlen=512)` filtered to three things: the board's
`0x711` replies, the flasher's own echoes, and `0x158`. It used to take every frame on bus 0
(about 1830 frames/s in the car) and grew without bound.

**Sequence inside `run_flash()`.** The `mark` labels name the phases of §9.

1. `check_app_slot_image(image)`; refuse on failure. This happens before the `Flasher` is
   created, so a refused image records no trace.
2. `mark("start")`, then `moving()`: 1 s listening for `0x158` bytes 0-1 (big-endian, in
   0.01 km/h). 100 or more means moving, which is refused. **Silence is not motion**: no
   powertrain traffic means the ignition is off, which is the bench case.
3. `listen(PRE_CAPTURE_S = 4 s)`, `mark("knock")`, then `knock()`.
4. `wait_hello(25 s)`, `mark("hello")`. A NAK here is returned as `refused: ...`; swallowing
   it used to produce a 25 s wait and a false "no bootloader".
5. `enter()`, then `info()`.
6. `mark("hold")`, then `hold(HOLD_BEFORE_DATA_S = 8 s)`, pinging INFO every
   `HOLD_PING_S = 2 s` (the session times out after 10 s of silence). Then `mark("begin")`.
   If this is a dry run: `abort()` and return.
7. `program()`: BEGIN (erase), `mark("data")`, the chunks, `mark("finish")`, FINISH.
8. `reboot()`, `mark("reboot")`, `wait_hello(25 s)`, `mark("hello_after")`, then
   `listen(POST_CAPTURE_S = 6 s)`.
9. **Read the result back from the post-reset HELLO** rather than assuming it (see §6.5).
10. `finally`, only if the `Flasher` exists: `mark("end")`, `save_trace()`,
    `trace_out(summarise_trace())`. Each is best effort.

### 6.3 The image checks

`check_app_slot_image(image)` returns `None` or a reason string, and never raises. It checks:

* length is at least `0x110` bytes and at most `APP_SLOT_BYTES` (112 KiB);
* the marker at `APP_ID_OFFSET = 0x100` is `<4I magic, origin, git, flags>` with
  `magic == 0x314C5041` (`APL1`) and `origin == 0x08004000`;
* the initial SP is in `0x20000000`-`0x20024000`;
* the reset vector is strictly inside `0x08004000`-`0x08020000`.

The marker is what really distinguishes an app-slot image. A standalone image, linked for
`0x08000000`, passes the SP and reset-vector tests by coincidence and would then be branched
into 16 KB away from its real reset handler (`inc/gw_app_id.h`, `docs/CAN-UPDATE.md`).

`image_identity()` returns `git` (`%08x`) and the flags `dirty` (`0x01`), `readOnly` (`0x02`)
and `test` (`0x04`). A dirty image flashes, with a logged warning.

### 6.4 Where the image comes from, and the rule

**The bundled image is produced by `tools/bundle_appslot.py` in the firmware repo, and by
nothing else.** CMake captures the git hash at *configure* time, so `git commit` followed by
`cmake --build` produces an image whose `0x707` names the previous commit. It builds, flashes
and runs cleanly, and reports a commit it was not built from. That has happened once. Had the
image been copied by hand, the car would have shown `up to date` against firmware that was not
the bundled firmware.

The procedure, in the firmware repo:

1. **Create `build/incar10app` once, or whenever the options change.** Its options are the
   ones in `tools\flash-incar-stage10-appslot.bat` (Stage 10 LIVE, `-DGW_APP_SLOT=ON`,
   `-DINCAR_TEST=ON`, `-DINCAR_TEST_AUTOSTART=stage4-live` and the `GW_*` options on its
   configure line). **That script is not build-only.** After building and checking, it
   programs the board on the probe over SWD with OpenOCD
   (`program build/incar10app/eps-lkas-bringup.elf verify reset exit`), or, with `/canpush`,
   pushes over CAN with `can_update.py --knock`. Either way it installs a LIVE steering image,
   as its own banner warns. Two ways to get the build directory without flashing:
   * run the script's `cmake -S . -B build/incar10app -G Ninja ...` configure line by hand
     (copy it from the script, not from here, so the options stay current), then
     `cmake --build build/incar10app`. This skips the script's own checks: the `findstr`
     tests on `CMakeCache.txt` (app slot, autostart, cadence, cam mute, HUD merge, authority
     160, floor and keep), `tools\check_app_image.py`, and the autostart-banner check;
   * or run the script with **no probe attached**. It builds, runs all of those checks, and
     then fails at the OpenOCD step with `BUILD OR FLASH FAILED`, which is expected here.
2. `python tools\bundle_appslot.py --reconfigure`. This re-runs `cmake -S . -B
   build/incar10app` (no options, so it reuses the cache from step 1) and `cmake --build`,
   which refreshes the hash. If `build/incar10app` does not exist, that configure would run
   without the toolchain file or the options; this was not tried. The script then
   **refuses** to copy in any of these cases:
   * no `APL1` marker;
   * origin is not `0x08004000`;
   * the image is larger than 112 KiB;
   * the dirty flag is set;
   * the SP or reset vector is out of range;
   * the embedded hash is not `git rev-parse --short=8 HEAD`;
   * `git status --porcelain` is not empty.

   The two git checks are conditional. `git()` returns stdout, so if `git` cannot run,
   `rev-parse` returns `""` and the HEAD comparison is skipped (`if head and ...`), and
   `status --porcelain` also returns `""` and passes. With no working git, neither check
   happens and nothing says so.

   If none of those apply, it copies the image next to `eps_lkas_flasher.py` in
   `S:/OP/sp-live`, taking the first of `DST_CANDIDATES` that holds the flasher:
   `openpilot/sunnypilot/selfdrive/pandad/` (the nested layout since the 2026-09-27 sync),
   then the old `sunnypilot/selfdrive/pandad/`. It never creates a directory, and refuses if
   neither exists. `--dst` overrides it. This is firmware commit `862540c`; before it the
   destination was hard-coded to the old path, which nothing reads after the merge.

   (`docs/CAN-UPDATE.md` used to print this command with a literal backspace byte where `\b`
   should be. It now reads `python tools/bundle_appslot.py --reconfigure`.)
3. Commit the `.bin` in sunnypilot and push. The device picks it up on its next update, and
   the page changes from `up to date` to `to <hash>`.

The flag byte of the current bundle is `0x04` (`test`). That is expected: the Stage 10
app-slot build configures `INCAR_TEST=ON`. It is not the dirty bit.

| bundle commit | image | bytes |
|---|---|---|
| `22acfb5ec` | `b386c2c6` | 46,416 |
| `d7b3aff1b` | `9124bf37` | 46,416 |
| `74a4f4d98` | `eaf54e87` | 46,376 |
| `068f203cb` | `577a723e` | 46,400 |
| `0006c3a48` | `d43b12aa` | 46,496 |
| `22fc22e6e` | `f6077d7f` (d43b12aa plus docs only) | 46,496 |
| `1f20b7b68` | `d995bc95` | 46,540 |
| `a32984eb2` | `298727b3` (`0x70B` through the ring, `gw_active.c` only; never shipped alone) | 46,532 |
| 2026-10-06 integration | `c8221c29` (`298727b3` plus `GW_WAIT_FOR_COMMA`: K2 on stock wiring and the camera owning `0x33D` until the comma is heard, then the usual entry at a safe moment) | 49,584 |
| `lcalert` (2026-10-12) | `686e4603` (`c8221c29` plus the lane-change alarm fix: `hud_gw()`'s ceiling test `hard` does not count under the blinker cap or within 2 s of driver torque over 20, and a gated tick restarts its 300 ms debounce; the command path is unchanged, `GW_BLINK_CEIL_PCT` still 50; SHA-256 `82fefd72…`, the rollback for the next row) | 49,664 |
| `nocap` (2026-10-12) | `b5f08583` (`686e4603` with the lane-change cap off: `GW_BLINK_CEIL_PCT` 100, and the driver fade applied only when the driver is not nudging the way the board is - openpilot's request over 10 counts his way, the wire not the other way, the push under 90 counts. Against the driver, and for any push of 90 or more, it is 686e4603's fade; the hand-back, authority 160 and every other limit are unchanged. Board `HANDOFF.md` 0o, SHA-256 `d5b4b9d3…`, the rollback for the next row) | 49,660 |
| `push9` (2026-10-13/14) | **`883a3cd1`** (current; `b5f08583` plus two things, the command path unchanged: `hud_gw()` alarms once per ceiling run when the board has been pinned with no hand and no indicator for 1.5 s past the 300 ms debounce, even inside the 10 s quiet period (route 137's silent second curve), and only an alarm that started at or after the run's own start counts as the run's alarm, so a driver-monitoring alarm already sounding cannot use it up; and the knock clears the key cycle's K2 takeover, so the image the bootloader starts - new after an update, or the old one after a cancel or time-out - waits for the comma like a key-on. Board `HANDOFF.md` 0p, SHA-256 `0753b9f6…`. The first push9 bundle, `4d427d46` (49,736 bytes, `d5396d72…`), counted any alarm sounding in the run; superseded by this one, never flashed) | 49,740 |

In git the `.bin` is not in LFS; `text=auto` detects it as binary. It ships in release builds,
because `tools/release/release_files.py` does not exclude it.

### 6.5 What `run_flash()` can return

`run_flash(transport, image, log, progress, dry_run=False, knock=True, hello_timeout=25.0,
trace_out=None, phase=None, should_cancel=None) -> (ok, msg)`. `phase` and `should_cancel`
(push9) drive the update screen (§3.6).

**Every ok path returns the bare 8-hex hash.** The hook writes that value into
`EpsLkasBoardVersion`, and an English sentence there would be rendered on the page as a
firmware version. The one exception is the dry run, which returns a sentence; only the CLI
dry-runs.

| outcome | msg |
|---|---|
| ok, witnessed | `<hash>` from the post-reset HELLO: `appOk` is set and the hash equals the image's |
| ok, not witnessed | `<hash>` from the image; no confirming HELLO within 25 s. This is logged. |
| refused image | `refusing this image: <reason>` |
| ignition off (push9) | `the ignition is off - turn it on, leave the engine off, then update` (`IGNITION_OFF`); also in the countdown after ABORT (push9 re-review) |
| moving | `the car is moving - stop first` |
| engine running (push9) | `Turn the engine off - leave the ignition on - then update` (`ENGINE_RUNNING`); in the countdown after ABORT |
| no engine data (push9 review) | `no engine data on the car bus - ignition on, engine off, then update` (`NO_ENGINE_DATA`): no `0x17C` through the panda; or `0x17C` the gate saw gone for 1 s in the countdown, after ABORT (push9 re-review) |
| cancelled (push9) | `cancelled` (`CANCELLED`): before the knock nothing was sent; in the countdown ABORT was sent |
| no board | `no HELLO. Either the board has no bootloader (it needs one SWD visit), or it never reset.` |
| board NAK | `refused: <nak>`. This covers the knock, ENTER and the post-reset HELLO; for example `the car is not stationary`. |
| protocol mismatch | `board speaks protocol v<n>, this speaks v1` |
| missed window | `no answer to ENTER - the board did not reset, or it left its boot window` |
| INFO lost | `no INFO reply` |
| hold lost | `the bootloader stopped answering during the hold` |
| wire | `frame 0x... was never transmitted (no echo in 1.5s)` |
| sequence | `no ACK for <CMD> within <t>s` / `board refused <CMD>: <nak>` / `ACK for X, expected Y` / `chunk <n>: ...` |
| bad install | `installed, but the board refuses to run it (HELLO appOk=0, appId=...)` / `board reports <x> after installing <y>` |
| anything else | `<ExceptionType>: <msg>` |

### 6.6 By hand (bench, or with `./pandad` stopped)

```
python -m openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher --dry-run     # reach the bootloader, write nothing
python -m openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher --bench       # candleLight instead of the panda
   --bin <file>   image to install (default: the bundled one)
   --bus 0|2      0 car, 2 camera; 1 is refused (no transceiver on the board)
   --no-knock     wait for a manual reset instead of knocking
```

Run it from the repo root. The module path gained `openpilot.` in the 2026-09 merge; the
flasher's own docstring uses the new form.

Running the panda transport by hand on a device while `./pandad` runs has not been tried
with this module. The firmware repo's procedure for its own tool requires openpilot to be
stopped, because `./pandad` rewrites the safety mode.

The hook path was rehearsed end to end against the spare board through
`flash_if_requested(..., transport_factory=BenchTransport)` (`28f97ae99`, `d7b3aff1b`). That
is the right rehearsal after any change to the hook.

---

## 7. What the board is doing meanwhile

| step | board |
|---|---|
| before the knock | The application is running with the relays split. The comma is parked. |
| knock | The app's `gw_knock.c` checks for standstill (NAK `BOOT_ERR_MOVING` if moving). It releases the LKAS UARTs **before** dropping K1/K2; `d43b12aa` fixed a RULE 2 exposure there. Then it resets into the bootloader. HELLO arrives about 52 ms after the knock (bench). |
| session | Relays de-energised, so the car and camera are joined through K1's NC contacts and the data stream reaches both halves. The session times out after 10 s of silence. |
| REBOOT | The bootloader copies staging into the app slot and runs the app, which re-splits K1/K2 about 1 s after the HELLO. |

---

## 8. Firmware identity: from `0x707`/`0x70F` to the screen

### 8.1 On the wire

The board sends both frames on panda bus 0 (`CanBus(CP).pt`, the board's FDCAN1): once at
mode entry, once a minute, and immediately whenever the `0x0E4` serial-domain bit changes.
`gw_active.c` at `d995bc9` sends `0x707` and then `0x70F` from one block ("version beat, once
a minute, and on any domain change"), so `0x70F` carries a fresh `BUILD_SP_FRESH` only at
those moments.

**`0x707 GW_VERSION`** (`BO_ 1799`) is little-endian. `GIT_HASH` is `0|32@1+`. It is **not
sent** by a board sitting in its bootloader, which answers on `0x711` instead, nor by the
read-only image before `52ca4732`.

**`0x70F GW_BUILD`** (`BO_ 1807`) is little-endian and new in board firmware `625b782e`
("An app-slot image must SAY it is one"). Four fork comments cite it as `625b782a`, which
does not exist (§11.3). Its signals:

| signal | bits |
|---|---|
| `BUILD_DIRTY` | 0 |
| `BUILD_APP_SLOT` | 1 |
| `BUILD_BOOTLOADER` | 2. A **run-time** check of the reset vector at `0x08000000`. |
| `BUILD_READONLY` | 3 |
| `BUILD_INCAR_TEST` | 4 |
| `BUILD_SP_FRESH` | 5 (area B; added in opendbc `cf583b37`) |
| `EPS_FLOOR_CPH` | `8\|16` |
| `LIN_MAX_ABS` | `24\|8` |
| `BOARD_UID` | `32\|24` |
| `BUILD_COUNTER` | `56\|8` |

Neither frame may carry a signal named `COUNTER` or `CHECKSUM`. In a `honda_` DBC those names
make opendbc's parser enforce a Honda counter and checksum that the board does not compute,
and every frame would be dropped.

`docs/SP_GATEWAY_FIRMWARE.md` §2 still draws `0x707` at 10 Hz. The DBC comment ("NOT 10 Hz")
is the correct one.

### 8.2 opendbc

* `_sunnypilot_linbus_gw.dbc` defines both frames. It is imported by
  `honda_accord_au_2015_can.dbc`, which the generator turns into
  `honda_accord_au_2015_can_generated` at build time; the generated file is not committed.
* `carstate.py get_can_parsers()` registers both frames in `pt_msgs` with **`float("nan")`**,
  for HONDA_ELESYS only. `nan` sets `ignore_alive`, so a 1/min frame (or an absent one, on
  older firmware or no board) can never make `canValid` false. Registering the frames up
  front also means the first frame is not dropped.
* `carstate_ext._update_linbus_firmware(ret_sp, cp)` is called from `CarStateExt.update()`
  when the fingerprint is in `HONDA_ELESYS`. It **latches**. There is no staleness window,
  because silence on an identity frame means "unchanged".
  * If `cp.ts_nanos["GW_VERSION"]["GIT_HASH"] != 0`, it sets `fwGitHash =
    int(GIT_HASH)` and `fwValid = True`. The `int()` is required: CANParser returns floats,
    and pycapnp refuses a float for `UInt32`.
  * If `cp.ts_nanos["GW_BUILD"]["BUILD_COUNTER"] != 0`, it sets `fwDirty`, `fwAppSlot`,
    `fwBootloader`, `fwReadOnly`, `boardUid`, and `fwBuildValid = True`.
  * Not decoded into cereal: `BUILD_INCAR_TEST`, `BUILD_SP_FRESH`, `EPS_FLOOR_CPH`,
    `LIN_MAX_ABS`, and every `GW_VERSION` flag. Those are for Cabana and route analysis.
* `structs.py CarStateSP.LinbusGateway` has `fwValid`, `fwGitHash`, `fwDirty`, `fwAppSlot`,
  `fwBootloader`, `fwReadOnly`, `boardUid`, `fwBuildValid`, in that order, after B's fields.
  **The names must match the capnp struct.** card converts the dataclass with
  `convert_to_capnp()`, which splats it into `custom.CarStateSP.new_message(**dict)` by
  keyword, so a name mismatch raises there, in `Car.state_update()` (`CS_SP = convert_to_capnp(CS_SP)`, `openpilot/selfdrive/car/card.py:209` after the merge), on the
  first CAN cycle of a drive. Field order cannot cause a failure in a keyword splat; it is
  kept identical by convention and pinned by `test_capnp_and_dataclass_agree`.

### 8.3 cereal

`CarStateSP.linbusGateway @1 :LinbusGateway` is owned by B. The A fields are:

| field | ordinal | type |
|---|---|---|
| `fwValid` | `@19` | `Bool` |
| `fwGitHash` | `@20` | **`UInt32`**. About half of all hashes have the top bit set; `Int32` would show them as negative. |
| `fwDirty` | `@21` | `Bool` |
| `fwAppSlot` | `@22` | `Bool` |
| `fwBootloader` | `@23` | `Bool` |
| `fwReadOnly` | `@24` | `Bool` |
| `boardUid` | `@25` | `UInt32` (24 bits used) |
| `fwBuildValid` | `@26` | `Bool` |

`fwBuildValid` exists because every field above defaults to false/0. Without it, "the board
said it has no bootloader" and "the board is too old to send `0x70F`" read the same, and the
screen showed `board id 000000` for a real board (`51c60e176`).

### 8.4 card to params to UI

* `state_publish()` ends with `self.stage_board_firmware(CS_SP)`. That method **writes
  nothing**: it runs on the 100 Hz loop, and a `Params.put` is a blocking write with two
  fsyncs. When `fwValid` is set, it stages `("%08x" % fwGitHash, build)` in
  `_board_fw_pending`. `build` is `{dirty, appSlot, bootloader, readOnly, uid: "%06x"}` when
  `fwBuildValid` is set, **and `{}` otherwise**, because absence and a negative answer are
  different facts.
* `params_thread()` (10 Hz) calls `write_board_firmware()`:
  * It writes `EpsLkasBoardVersion` and `EpsLkasBoardBuild` when either value differs from
    card's in-process cache (which starts as `None`, so the first staged value of each card
    process is always written), and logs `eps-lkas board firmware <v> <build>`.
  * It writes `EpsLkasBoardSeenAt` at most every 60 s while anything is staged.

  Nothing clears the staged value, so "last seen" in fact means "the last drive in which card
  saw `0x707` at least once".
* The page renders `EpsLkasBoardBuild = {}` as `unknown` and `no bootloader`, and the button
  says `needs SWD once`. This collapse is correct: `0x70F` shipped in the same firmware
  commit as the bootloader, so a board that does not send it has none.
* A successful flash writes the same three params immediately (§5.3), so the page is correct
  before the next drive.

The new attributes in `Car.__init__` are `_board_fw_version`, `_board_fw_build`,
`_board_fw_pending`, `_board_fw_seen_at` and `_flash_trace_done`.

---

## 9. The flash steering trace

### 9.1 Why it exists

The flash runs offroad, where loggerd is not running, so nothing about it is recorded. A
driver reported the EPS humming and the wheel moving during an update, with hands off, and the
routes on either side left a 47 s hole. The panda was receiving the whole car bus throughout
and discarding it.

The trace answered the first question: route f6 showed the wheel did not move. The hum was
later identified as the V6's firing vibration, the 3rd engine order (about 44 Hz at idle) and
its 2nd harmonic, about 2.5x stronger after the flash. The owner confirms the car has active
noise cancellation and VCM active engine mounts, and the leading hypothesis is that the update
disturbs one of them. The trace now measures that on every update (`docs/CAN-UPDATE.md` in the
firmware repo; `docs/CHANGELOG_SERIAL_STEERING.md` 2026-09-23 and 2026-09-24).

### 9.2 What is captured

`_pump()` stores `(monotonic time, id, raw bytes)` into `_trace` (a `deque(maxlen=TRACE_MAX
= 24000)`) for the IDs in `TRACE_IDS = (0x156, 0x18F, 0x17C, 0x1A6, 0x70B)`. There is no decoding
in the receive path. Traced frames stay out of `_rx`.

| id | decoded as | why |
|---|---|---|
| `0x156` STEERING_SENSORS | `STEER_ANGLE` (bytes 0-1 BE, x -0.1 deg), `STEER_ANGLE_RATE` | Wheel movement. Cross-checked against `carState` on route f1. |
| `0x18F` STEER_STATUS | `STEER_TORQUE_SENSOR` (bytes 0-1 BE, x -1), 2-bit counter in byte 6 bits 5:4 | Column torque. This is the vibration witness. |
| `0x17C` POWERTRAIN_DATA | `ENGINE_RPM`, bytes 2-3 BE | **Not** `0x158`'s ENGINE_RPM, which reads about 8% low in Park (it is the torque converter). |
| `0x1A6` | byte 2 bit 6 | An accessory load that cycles about every 7 s. Amplitudes are comparable only at the same load. |
| `0x70B` GW_STEER_GRANT (push9) | `decode_grant()`: STATE byte 0, REASON byte 1, EPS_ERROR_STATE byte 3 bits 5:2, CAM_LKAS_ON byte 3 bit 7 | The board's own state, 10 Hz while its firmware runs, so in `pre` and `post` only. Per phase the summary gains `st` (the last "STATE/REASON": "8/16" = K2 at stock waiting for the comma, "0/7" = split at a standstill), `eer` (highest EPS error state) and `cam` (camera LKAS_ON seen). Before push9 nothing said whether K2 was split at the knock, or what the new image did in `post` - the one window where the wheel was ever seen to move (f6). |

There is no better instrument available. `0x1AB` does not exist on this car, and the EPS's
motor torque is reported only over the LKAS serial line, which the board stops mirroring when
it enters the bootloader.

**Phases.** Each `mark()` closes the previous phase. It snapshots `transport.health()` and
starts a new per-ID RX count, the census.

| mark | phase name | what happens during it |
|---|---|---|
| `start` | `pre` | baseline, board normal |
| `knock` | `reset` | relays drop, reset into the bootloader |
| `hello` | `enter` | ENTER, INFO |
| `hold` | `hold` | session open, pings only: the on-screen countdown (5 s since push9; 8 s before) |
| `begin` | `erase` | BEGIN, bank 2 erase |
| `data` | `data` | the chunk stream, about 770 frames/s |
| `finish` | `finish` | CRC check |
| `reboot` | `copy` | staging copied to the app slot |
| `hello_after` | `post` | app start, K1/K2 re-split (6 s) |
| `end` | | closes `post` |

The hold and the pre/post listens cost about 16 s per update (19 s before push9). They exist
to separate the knock from the data stream in time; the two were 1.7 s apart and could not be
told apart.

**The census (push9).** `CENSUS_OWN`, the IDs never counted as lost or new, is now the board's
telemetry `0x700`-`0x70F` and the bootloader's `0x711` only. It was `0x700`-`0x71F`, which also
hid any module answering the update's own `0x710`/`0x712` on the usual request + 8
(`0x718`/`0x71A`) - exactly what `new` exists to catch. Our own frames never count: echoes are
not RX.

### 9.3 The summary (`summarise_trace()`)

The summary is small enough for log lines. It is `{}` when no angle and no torque frames were
captured, and the hook then writes nothing.

| key | meaning |
|---|---|
| `n`, `dur` | frames captured, span in seconds |
| `angle_min`, `angle_max`, `angle_span`, `rate_max`, `torque_absmax` | overall figures |
| `verdict` | One of four: `no angle frames`; `wheel did not move` (span under 0.5 deg); `moved, with column torque - looks like a hand on the wheel` (peak 1000 or more); `MOVED WITH LOW COLUMN TORQUE - something drove the column` |
| `phases` | One row per phase. Fields: `p` name, `s` start, `d` duration, `rpm` mean, `ld` load-bit mean, `sd` column-torque std dev, `ang` angle range, and the fields below. |
| `phases` (cont.) | `o3`: 3rd-order amplitude in column torque. `ref`: the same fit at an off-order 2.6. `e0`/`e1`/`e2`: delta of panda `total_error_cnt` per bus across the phase. `lost`: `{id: ratio}`, IDs of 5 Hz or more before the knock that fell below 0.8x their pre-knock rate, up to 8. `new`: IDs absent before the knock with 3 or more frames in the phase, up to 8. |
| `series` | `[t, angle, column torque, rpm]` decimated to `TRACE_HZ = 10` |

The census has three exclusions: the board's own `0x700`-`0x71F`, which change across the
bootloader by design; echoes; and phases, or a pre-knock phase, shorter than 1 s.

The firing-order fit works like this:

* `eps_grid()` rebuilds `0x18F` sample times on the EPS's own 10 ms grid from the rolling
  counter. USB-batch receive times jitter by about 10 ms, which is meaningless for a 44 Hz
  fit. Dropped frames stay as holes.
* `order_amplitude()` then does a least-squares fit, with the phase tracked from rpm because
  idle wanders.
* Replayed on real f5/f6 frames, this gave 2.10 and 5.02 counts, against 2.02 and 5.04 from
  an independent analysis.

**Reading it.** The phase whose `o3` steps up is the trigger. `e0`/`e2` say whether CAN
errors came with it, and `lost`/`new` say which car messages the burst disturbed.

The first instrumented update (route `00000102` to `00000103`, 2026-09-27) showed zero errors
and no `lost`/`new`. Idle was cold (1017-1185 rpm), so `o3` was noise there. A warm-idle
update is still the test (firmware repo `docs/CAN-UPDATE.md`).

### 9.4 How the trace reaches a route without SSH

```
flasher  summarise_trace()           -> trace_out(dict), from run_flash's finally, on failure paths too
                                        (but not for an image refusal or a Flasher that failed to construct)
hook     params.put("EpsLkasFlashTrace", dict)    PERSISTENT, JSON, only if non-empty
  ... offroad; the device may reboot ...
card     params_thread, first tick of the next onroad session: log_flash_trace()
         cloudlog.warning("eps-lkas flash trace {summary without series}")
         cloudlog.warning("eps-lkas flash trace series[i] [...]")   40 samples per line
         params.remove("EpsLkasFlashTrace")        so it appears in exactly one route
```

No trace is produced when `run_flash()` returns before the `Flasher` exists: an image
refusal (`check_app_slot_image`) returns first, and a `PandaTransport` constructor failure
happens in the hook before `run_flash()` is called at all. A missing bundled image likewise
fails in the hook.

`_flash_trace_done` makes this run once per card process, and every exception is swallowed;
diagnostics must never take card down. Look for the lines in the route's `logMessage`. No
tool in either repo parses them yet.

For SSH users, `save_trace()` also writes the raw frames to
`/data/eps-lkas-trace/flash-<unixtime>.csv` (columns `t,addr,data`, with a comment header).
This is best effort: a full or read-only disk loses the trace and nothing else. Nothing
prunes that directory.

---

## 10. Tests

Upstream removed pytest (`98e7c4f98`, `ac4ab9a9b`); its `tools/test_runner.py` (also
`tools/op.sh test`) collects only `unittest.TestCase` classes. In the 2026-09 merge every test
file below became one `TestCase` class. After the merge, in WSL under the runner, all 61 tests
in the three `openpilot/selfdrive/ui/tests` files (26 + 10 + 25; 60 are area A) and the seam
test passed:

```bash
python tools/test_runner.py -v openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py \
  openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py openpilot/selfdrive/ui/tests/test_gateway_board_settings.py \
  openpilot/selfdrive/car/tests/test_car_control_sp_seam.py
```

The UI and flasher tests still parse source instead of importing raylib. They keep
`ROOT = Path(__file__).parents[3]`, which is now `openpilot/`, and use
`REPO = Path(__file__).parents[4]` for paths into `opendbc_repo/`.

### 10.1 `openpilot/selfdrive/ui/tests/test_eps_lkas_flasher.py`

This file loads the flasher by file path, without panda or python-can. It has 26 tests,
grouped below by what they pin.

**Module shape and the no-raise rule:**

| test | pins |
|---|---|
| `test_module_imports_without_panda_or_python_can` | the module imports without panda or python-can |
| `test_no_hard_dependency_at_module_scope` | no `can`, `usb`, `panda`, `openpilot` or `cereal` import at module scope |
| `test_nothing_raises_out_of_the_flasher` | AST walk: no `raise SystemExit` or `sys.exit()` outside `__main__`, and `run_flash` catches `Exception` |
| `test_run_flash_returns_rather_than_raising` | a broken transport produces a string, not an exception |

**Image:**

| test | pins |
|---|---|
| `test_image_check_rejects_a_standalone_image` | the image check refuses a standalone image |
| `test_bundled_image_is_an_app_slot_image` | `APL1`, origin, SP, reset vector, an 8-hex hash, **not dirty** |

**Protocol and panda:**

| test | pins |
|---|---|
| `test_crc16_matches_the_bootloader` | `0x29B1` for `"123456789"` |
| `test_elm327_param_is_not_zero` | `SAFETY_ELM327` equals the value **read from** `opendbc_repo/opendbc/safety/declarations.h`; the param is non-zero; `cli=False`; the `health()` readback |
| `test_commands_are_padded_to_eight_bytes` | both transports pad to 8 bytes |
| `test_protocol_constants_match_the_firmware` | the command and response codes, `MAGIC`, chunk size, `STATIONARY_CPH` and the marker constants, **against literals in the test**, not against `boot_proto.h` (§6.2) |
| `test_panda_health_is_best_effort` | `health()` skips a bus that raises |

**Motion:**

| test | pins |
|---|---|
| `test_silence_is_not_motion` | no powertrain traffic is not motion |
| `test_moving_is_detected_from_the_wire` | `0x158` speed triggers the moving refusal |

**Trace capture:**

| test | pins |
|---|---|
| `test_the_steering_trace_captures_the_two_ids_that_answer_the_question` | `TRACE_IDS`; traced frames stay out of `_rx`; the CSV format |
| `test_the_trace_is_bounded_and_never_costs_more_than_itself` | the trace is bounded; an unwritable directory or an empty trace writes nothing |
| `test_run_flash_saves_the_trace_on_the_failure_paths_too` | `save_trace` is in a `finally` |
| `test_run_flash_reports_the_trace_even_when_it_fails` | `trace_out` is always called once a `Flasher` exists, and a throwing `trace_out` does not mask the result |

**Decoders and verdict:**

| test | pins |
|---|---|
| `test_the_steering_decoders_match_carstate` | the `0x156`/`0x18F` decoders, against values from route f1 |
| `test_the_verdict_separates_a_hand_from_something_driving_the_column` | the three verdicts |
| `test_the_summary_is_small_enough_to_log_and_empty_when_there_is_nothing` | the summary is small, and `{}` when empty |
| `test_engine_rpm_comes_from_0x17c_not_0x158` | rpm source, load bit, `0x18F` counter |

**Vibration analysis:**

| test | pins |
|---|---|
| `test_the_eps_grid_ignores_usb_batching_and_keeps_dropped_frames_as_holes` | the rebuilt 10 ms grid |
| `test_the_firing_order_fit_finds_the_order_and_only_the_order` | the fit finds order 3 and not the reference |
| `test_the_phase_table_shows_which_step_the_vibration_starts_in` | the nine phase names in order; `o3` steps up in `data`; error deltas land in the right phase |
| `test_the_hold_keeps_the_bootloader_alive_and_sits_before_the_data` | the ping interval is under half the timeout; ordering info < hold < program |
| `test_the_census_names_what_the_burst_starves_and_what_starts_answering` | `lost`/`new`, the board-ID exclusion, the rate floor, echoes not counted |

### 10.2 `openpilot/selfdrive/ui/tests/test_eps_lkas_hook.py`

This file stubs `openpilot.common.params` with a `FakeParams` and uses a `FakeProcess`. It has
10 tests:

| test | pins |
|---|---|
| `test_watcher_ignores_a_request_while_onroad` | no signal, no skip while onroad (`IsOffroad=False`) |
| `test_watcher_stops_pandad_offroad` | exactly one SIGINT, and the skip was armed. It does **not** check that the skip came first (§5.4). |
| `test_watcher_does_nothing_without_a_request` | no request, no action |
| `test_request_is_cleared_before_anything_is_attempted` | the first write is the request clear; the state ends `failed...` (no panda here, so `PandaTransport` raises) but **not** `failed: not while driving`, and `running` was written, so the flash path really ran |
| `test_no_request_means_no_writes_at_all` | no request, no writes |
| `test_hook_never_raises` | a params object that raises does not escape |
| `test_pandad_skips_the_panda_reset_on_that_re_entry` | AST: the resets are in the `else` branch of the skip check |
| `test_pandad_calls_the_hook_and_the_watcher_in_the_right_places` | source order `flash_panda` < hook < `Popen` < watcher < `wait` |
| `test_the_flash_trace_survives_to_the_next_drive` | `EpsLkasFlashTrace` is `PERSISTENT` and `JSON`; no `json.dumps` in `trace_out`; card's emitter is once-only, removes the param, and catches `Exception` |
| `test_every_param_the_hook_and_the_page_use_is_registered` | every param key `eps_lkas_hook.py` and `board.py` read or write is in `params_keys.h` (added in the 2026-09 merge) |

`FakeParams` accepts any key, and its `get_bool` returns `False` for a key it was not given.
With the hook testing `not IsOffroad`, a `FakeParams` without `IsOffroad` reads as onroad, so
every `FakeParams` meant to be offroad says `IsOffroad=True`.
`test_no_request_means_no_writes_at_all` and `test_hook_never_raises` return before the onroad
read and need no key. The last test exists because `FakeParams` could not see that `IsOnroad`
had been deleted upstream; see §12.1.

### 10.3 `openpilot/selfdrive/ui/tests/test_gateway_board_settings.py`

This file parses source rather than importing the UI (raylib). It decodes the board's frames
with opendbc's own `CANParser` on `honda_accord_au_2015_can_generated` (generated in memory
from `_sunnypilot_linbus_gw.dbc`, so no build is needed). Until the 2026-09 merge it used
`cantools`, which is in neither venv, so the byte-order test skipped itself silently. It has
24 area A tests.

**Wire, schema and params:**

| test | pins |
|---|---|
| `test_dbc_decodes_a_real_board_frame` | little-endian `GIT_HASH`, `EPS_FLOOR_CPH`, `BOARD_UID`, against `gw_version_pack`/`gw_build_pack` byte layouts; negative control: the hash packed MSB-first must not decode to the same value |
| `test_frames_are_registered_liveness_exempt` | `("GW_VERSION"/"GW_BUILD", float("nan"))` in `carstate.py` |
| `test_capnp_and_dataclass_agree` | the `FW_FIELDS` exist in both, in the same order, with unique ordinals |
| `test_decoder_exists_and_is_called` | `_update_linbus_firmware` exists, is called, and uses the `ts_nanos` tests |
| `test_params_are_registered` | the board params are registered and card writes them |
| `test_json_params_are_given_objects_not_strings` | AST: no `json.dumps` or string literal into any JSON-typed key in card |
| `test_board_firmware_is_not_written_from_the_control_loop` | `write_board_firmware` is called from `params_thread` and not from `state_publish` |
| `test_flash_params_are_registered` | the request param is `CLEAR_ON_MANAGER_START` |
| `test_absence_and_a_negative_answer_are_different` | `stage_board_firmware` reads `fwBuildValid` |

**Page structure and reads:**

| test | pins |
|---|---|
| `test_panel_is_reachable` | `items.insert(<n>, board_btn)`, `self._scroller.add_widget(item)`, and `self._scroller.add_widgets(` rather than `self.add_widgets(` |
| `test_panel_reads_params_on_a_tick_not_every_frame` | params are read on the `REFRESH_S` tick |
| `test_panel_says_last_seen_not_live` | the page says "last seen" |
| `test_button_exists_and_is_on_the_page` | the button is on the page |
| `test_the_ui_shares_the_marker_constants_with_the_flasher` | `APP_ID_*` are imported, not redefined |
| `test_bundled_hash_read_agrees_with_the_full_parse` | the 0x110-byte read matches `image_identity` |

**Button behaviour:**

| test | pins |
|---|---|
| `test_confirmation_exits_on_confirm` | `exit_on_confirm=True` |
| `test_the_gate_is_rechecked_inside_the_confirm_callback` | `confirm()` calls `_can_update` and `put_bool` |
| `test_enabled_state_is_imperative_and_never_mixed` | no `set_enabled(lambda`, and `set_enabled` is called in `refresh` |
| `test_a_refusal_is_explained_not_silent` | `BigDialog`, and the three refusal strings |
| `test_the_button_says_what_it_would_install` | `bundled_firmware`, `up to date`, `no image`, `offer == version` |
| `test_up_to_date_disables_the_button` | `up to date` is in `_can_update` and comes before `ignition off` |

**Rendering** (the first two also read C's `vehicle.py`; the third reads only `board.py`):

| test | pins |
|---|---|
| `test_hand_positioned_labels_never_wrap` | every `UnifiedLabel` has `wrap_text=False` |
| `test_no_glyphs_the_baked_font_does_not_have` | every `tr()` string is in ASCII 32-126 plus `EXTRA_FONT_CHARS` from `openpilot/system/ui/lib/application.py`, the set upstream loads its fonts with since `96ca1f8ed` (before the merge it read `EXTRA_CHARS` from the deleted `selfdrive/assets/fonts/process.py`). Strings outside `tr()` are not checked. |
| `test_the_button_title_leaves_room_for_its_sub_label` | the title is 10 characters or fewer |

### 10.4 `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` (A block only)

This block drives a `structs.CarStateSP` through the real `convert_to_capnp()` and makes four
checks:

1. `fwValid`, `fwAppSlot` and `fwBootloader` convert (`linbusGateway firmware fields
   convert`);
2. `fwGitHash = 0xF1234567` survives, so the field must be `UInt32`;
3. `boardUid = 0x3F2A10` round-trips;
4. the unset `fwDirty` and `fwReadOnly` default to false.

This is the only test that exercises the dataclass-to-capnp splat card uses.

### 10.5 Not covered by any test

* `PandaTransport` against a real panda. It has been used in the car for every update since
  2026-09-23; the evidence is the traces in routes f6 and `00000103`.
* The page's rendering on the device. The update screen's states are rendered headless by
  `flash_view()`'s caller in the push9 render harness, not by a test.
* The order of skip and SIGINT in the watcher (§5.4).
* The flasher's protocol constants against `boot_proto.h` (§6.2).
* The behaviours in §11.2.

---

## 11. Other

### 11.1 Hunks in area A's files that belong to other areas

| file | hunk | owner |
|---|---|---|
| `openpilot/selfdrive/car/card.py` | `skip_fw_query=bool(fixed_fingerprint)` on `get_car()`: with the platform fixed, the VIN/FW query's OBD multiplexing costs the Elesys radar its bus (`d11d2c9a8`) | C |
| `openpilot/selfdrive/car/card.py` | `import json` at the top, **unused** since `a925fdce1`; harmless | A (leftover) |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | `VehicleLayoutMici`, `car_brand`, `vehicle_btn`, `items.insert(2, vehicle_btn)` | C |
| `openpilot/selfdrive/ui/tests/test_gateway_board_settings.py` | `test_lat_ready_means_lateral_is_enabled_not_merely_possible` | B |
| `openpilot/selfdrive/car/tests/test_car_control_sp_seam.py` | everything outside the firmware-identity block | B |
| `openpilot/cereal/custom.capnp`, `opendbc/car/structs.py`, `carstate_ext.py`, `_sunnypilot_linbus_gw.dbc` | everything not listed in §8 | B |

### 11.2 Behaviours found by reading, not yet observed

* **The button can stick at `requested`.** `_asked` is cleared only when the page observes
  `EpsLkasFlashState == "running"`. The state might never be seen as `running` in these
  cases:
  * the request is dropped onroad, which writes `failed: not while driving` and never
    `running`;
  * a flash fails within one 1 s refresh tick, for example a missing image, a refused image
    (§3.3), or a `PandaTransport` constructor error.

  In any of these the button would show `requested` and stay disabled until the UI process
  restarts. A fix would clear `_asked` on any change of state after `confirm()`. **Fixed on
  branch `blr`** (§13.4): with two boards a stuck `_asked` would have held both buttons.
* **The watcher drops only the first onroad request.** Its `refused` flag means a second
  request written while onroad is neither dropped nor answered. That request stays set and
  is honoured once the car is offroad. The UI cannot write a request onroad; a hand-written
  param can.

### 11.3 Stale references

* `openpilot/selfdrive/ui/sunnypilot/mici/layouts/board.py`, module docstring (line 17), says the
  identity is latched "by `card.publish_board_firmware()`". No such method exists; the
  methods are `stage_board_firmware` and `write_board_firmware` (§8.4).
* `docs/CHANGELOG_SERIAL_STEERING.md` (2026-09-22 entry) says "Settings → gateway → update
  firmware". The button title is now `update` (`b648e51ca`).
* `625b782a` in `openpilot/cereal/custom.capnp` (the `fwBuildValid` block),
  `opendbc/car/honda/carstate.py` `get_can_parsers`, `carstate_ext.py`
  `_update_linbus_firmware` and `_sunnypilot_linbus_gw.dbc` `CM_ BO_ 1807`: the board
  commit is `625b782e`.

---

## 12. Upstream merges

### 12.1 What the 2026-09 merge did in this area

Upstream sunnypilot `a5f44653d` was 549 commits ahead of the old fork point. It moved every
tree under a top-level `openpilot/` directory (`5edc0bd89` "mv root dirs into nested
openpilot", `37eda06c9` "move cereal into nested openpilot", `20e0f21b5` "prefix paths with
openpilot"). Python import paths (`openpilot.sunnypilot...`) did not change. Every area A
hunk survived; this was checked by reading the merged code, not only the conflict list.

| hunk | what happened |
|---|---|
| `eps_lkas_flasher.py`, `eps_lkas_hook.py`, `eps_lkas_appslot.bin` | file-location conflicts; accepted at `openpilot/sunnypilot/selfdrive/pandad/`, next to `rivian_long_flasher.py`. The flasher is unchanged apart from the `python -m openpilot.sunnypilot...` path in its docstring. The `.bin` is the same `d995bc95` blob and still passes `check_app_slot_image()`. |
| `eps_lkas_hook.py` onroad check | **`IsOnroad` no longer exists** (`ad5151b38`, "single IsOffroad param"). Upstream's `Params` raises `UnknownKeyName` on an unknown key, the hook caught it, and no flash could ever have started: the page would have sat at `requested`. Both reads are now `not params.get_bool("IsOffroad")`, and the log string names `IsOffroad`. A missing `IsOffroad` reads as onroad, which refuses. Upstream's manager writes `IsOffroad=True` (with `block=True`) before it starts pandad, and `hardwared` still forces `started=False` under `OffroadMode`, so Always Offroad still means `IsOffroad=True`. |
| `pandad.py` | content conflict; the five hunks of §5.2 were re-applied onto upstream's file (`openpilot.common.hardware`, the new `Popen` `cwd`). |
| `card.py` | merged automatically, unchanged (the unused `import json` came along). |
| `params_keys.h`, `custom.capnp` | merged automatically, unchanged; keys, flags, types and ordinals as before. Upstream `CarStateSP` is still only `speedLimit @0`. |
| mici `settings.py` and `board.py` | content conflict in `settings.py`; `board.py` was a file-location conflict. `back_callback` dropped (upstream `099143ad9`); the gateway row is at index 3 (§3.1). |
| the tests | converted to `unittest.TestCase`; `REPO` root for `opendbc_repo/`; `IsOffroad=True` in every offroad `FakeParams`; the registration test; the font test on `EXTRA_FONT_CHARS`; the DBC test on opendbc's `CANParser` instead of `cantools`. |
| opendbc (`_sunnypilot_linbus_gw.dbc`, the `nan` registration, `_update_linbus_firmware`, the eight `structs.py` fields) | unchanged. The generated `honda_accord_au_2015_can_generated.dbc` still has `BO_ 1799` and `BO_ 1807`. |
| the bundle destination | the firmware repo's `bundle_appslot.py` finds the flasher in either layout (`862540c`, §6.4), and its `docs/CAN-UPDATE.md` names the new path. |

Checked against upstream and unchanged: the ctypes `Params` table (`(dict, JSON)` works,
`(str, JSON)` raises, puts are non-blocking by default); opendbc's parser still enforces only
signals named exactly `CHECKSUM`/`COUNTER`, and still treats `nan` as `ignore_alive`; the panda
Python API at `74a0adce` (`health()["safety_mode"]` survives the repacked health packet); the
panda's ELM327 mode with a non-zero param (`CAN_MODE_NORMAL`, relay closed, `0x7xx` with DLC 8
allowed, `SAFETY_ELM327` still `3U`); every mici API `board.py` uses; the `icon_software.png`
asset. `tools/release/release_files.py` does not exclude the `.bin`.

Two behaviour changes in this area come from upstream, and are intended:

* **The UI is no longer restarted after a crash** (`03803d0c8`). A crash in `board.py` now
  leaves `ui` down until manager or the device restarts, where before it came back by itself.
* **The settings order changed**: models, vehicle, gateway, then upstream's items, with
  sunnylink where upstream put it.

### 12.2 Hook points in upstream files

These are where the next merge can collide with area A.

| upstream file | hook | what to watch for |
|---|---|---|
| `openpilot/selfdrive/pandad/pandad.py` | the five hunks of §5.2: the import; the `skip_panda_reset` state and setter; the guarded reset; `flash_if_requested(panda_serials[0])` between `flash_panda()` and `Popen`; `watch_for_request(process, request_skip_panda_reset)` between `Popen` and `wait()` | The Python wrapper must still own the panda before spawning `./pandad` and block on it. If upstream removes that window, this feature needs a new one. Do not substitute `systemctl stop comma` or `pkill` (§5.1). |
| `openpilot/selfdrive/car/card.py` | 5 attributes in `__init__`; `self.stage_board_firmware(CS_SP)` as the last line of `state_publish()`; the three methods; `write_board_firmware()` and `log_flash_trace()` in `params_thread()` | `state_publish` and `params_thread` must still exist, and `CS_SP` must still be capnp by then (`convert_to_capnp` in `state_update`). |
| `openpilot/common/params_keys.h` | the seven `EpsLkas*` entries with the flags and types in §4 | The format. Also any onroad/offroad key the hook reads (`IsOffroad` today); the registration test fails if one disappears. |
| `openpilot/cereal/custom.capnp` | the `LinbusGateway` fields `@19`-`@26` inside B's struct | If upstream ever adds `@1` or `@2` to `CarStateSP`, the fork's fields (B's `linbusGateway @1` and `driverTorqueStale @2`) must move to new ordinals, and ordinals must never be reused. **The cost:** every route the fork has already recorded carries `linbusGateway` at `@1` and `driverTorqueStale` at `@2`. After a renumber, those old routes decode those slots as whatever upstream put there, and the fork's fields read as unset. Anything that replays or analyses old fork routes then needs the old schema. |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/settings.py` | the import, `board_panel`/`board_btn`, `set_visible(board_page_visible)`, `items.insert(3, board_btn)` | Upstream's own inserts and constructor style. The test only requires some `items.insert(<n>, board_btn)`. |
| opendbc `opendbc/car/honda/carstate.py` | `GW_VERSION`/`GW_BUILD` in `pt_msgs` with `float("nan")` | shared with B |
| opendbc `opendbc/sunnypilot/car/honda/carstate_ext.py` | `_update_linbus_firmware()` and its call; it relies on B's `ret_sp` parameter | Upstream's `update(self, ret, can_parsers)` has no `ret_sp`; B carries that change. |
| opendbc `opendbc/car/structs.py` | the eight fields | `CarStateSP` is still an `auto_dataclass` upstream. |
| opendbc `_sunnypilot_linbus_gw.dbc` | `GW_VERSION`, `GW_BUILD` including `BUILD_SP_FRESH` (`cf583b37`) | fork-only file; carry it as is |
| `.gitmodules`, `opendbc_repo` pin | `.gitmodules` points `opendbc` at `https://github.com/SoRadGaming/opendbc.git`, `branch = sp-master`; the pin must be a commit carrying the decode | Merge the opendbc fork first, push it, then bump the pin. |

### 12.3 After every merge

- [ ] `test_every_param_the_hook_and_the_page_use_is_registered` passes, and
      `git grep -n IsOnroad -- openpilot` has no hits in fork code.
- [ ] `SAFETY_ELM327` in `opendbc/safety/declarations.h` is still `3U` (it is at upstream
      `f95f996f`). If it changes, `test_elm327_param_is_not_zero` fails, which is the intent.
- [ ] Diff the constants at the top of `eps_lkas_flasher.py` against the firmware repo's
      `inc/boot_proto.h` by hand: `PROTO_VERSION`, the CAN IDs, the command and response
      codes, the NAK error codes and the HELLO status bits. No test does this (§6.2).
- [ ] The panda Python API is unchanged. At upstream's pin `74a0adced` the following hold:
      `Panda(serial, cli=False)`, `set_safety_mode(mode, param)`, `health()["safety_mode"]`,
      `can_send(addr, dat, bus)`, `can_recv()` yielding `(addr, dat, src)` with echo `+128`
      and reject `+192`, `can_health(bus)`, and `can_clear(0xFFFF)`.
- [ ] `pandad.py` still has a Python wrapper that owns the panda before spawning `./pandad`
      and blocks on it (§12.2).
- [ ] `Params.put` still rejects `(str, JSON)`, and card's JSON writes still pass dicts.
- [ ] mici APIs used by `board.py` are unchanged:
      * `BigButton(text, value, icon)` and `.set_value`
      * `BigConfirmationDialog(title, icon, cb, exit_on_confirm, red)` and `BigDialog`
      * `NavScroller._scroller.add_widgets`
      * `UnifiedLabel(..., wrap_text=)`
      * `ui_state.ignition` and `ui_state.is_offroad()`
      * `EXTRA_FONT_CHARS` in `openpilot/system/ui/lib/application.py`

      All of these are present at `a5f44653d`.
- [ ] Run the four test files under `tools/test_runner.py` (§10) and check the counts: 26,
      10, 25 and 1. A module that reports 0 tests has been dropped by the runner.
- [ ] Rehearse the hook on the spare board with the bench transport (§6.6).
- [ ] When bundling, confirm `git` runs in the firmware repo, since `bundle_appslot.py`
      silently skips its HEAD and clean-tree checks without it (§6.4), and that the image
      landed next to `eps_lkas_flasher.py` in the layout the checkout has.
- [ ] In the car:
      * the gateway row appears;
      * the card shows the board's hash and a recent "last seen";
      * with a newer bundle the button reads `to <hash>`;
      * an update completes, and the next route carries `eps-lkas flash trace` lines.

---

## 13. The second board: BLR-v1, the brake-lamp board (branch `blr`, UNRELEASED)

The BLR-v1 board lights the brake lamps from openpilot's `0x1FA BRAKE_LIGHTS` bit, which the car ignores. Its data
side (DBC, decoder, cereal, card, the `Blr*` params) is [BRAKE-LAMP-RELAY.md](BRAKE-LAMP-RELAY.md). It is updated from
the comma exactly as the EPS-LKAS board is, through the same flasher, hook, pandad window and page, and **separately**:
its own button, its own params, its own target in the flasher. Markers `FORK(BRAKE-LAMP-RELAY)`. Board side: the
EPS-LKAS repo, branch `blr-v1`, folder `blr/` (`docs/DESIGN.md` section E, `docs/BLR-CAN.md`).

**Not pushed, and not bundled yet.** The branch waits for the board's bench tests and a shadow drive (the owner's
call). `blr_appslot.bin` is added only after them (board design E.5, WP-H5). Until then the lamp board's button says
`no image` and offers nothing; its card is shown as soon as the board has spoken. `eps_lkas_appslot.bin` is
byte-identical to `nsw-live`'s, and the EPS update is unchanged on the wire (`test_eps_wire_transcript_is_frozen`).

### 13.1 How the two updates are kept apart

An update of one board must never reach, start or stall the other. Five layers, each enough on its own for the
common mistakes:

| layer | EPS-LKAS | BLR-v1 | what it stops |
|---|---|---|---|
| the request | `EpsLkasFlashRequested` | `BlrFlashRequested` | a press naming the wrong board: the request **is** the board, there is no shared request with a "which board" field |
| the page | `UpdateBoardButton()` | `UpdateBoardButton(BLR)` | two updates at once: each button's `_busy()` is both boards' (§13.4) |
| the window | `flash_if_requested(serial)` | `flash_if_requested(serial, target=BLR)`, only if the EPS ran none | two sessions in one pandad window (§13.3) |
| the wire | `0x710`/`0x711`/`0x712`, `EPSBOOT` | `0x628`/`0x629`/`0x62A`, `BLRBOOT`, and HELLO **and** the ENTER ACK must say board class 2 in byte 7 | the other board's bootloader ever hearing a command; anything else answering on `0x629` is ABORTed before a byte of image |
| the image | marker `APL1` | marker `BLR1`, shadow or live, clean tree, the bootloader's vector rules, its own length/CRC-32 trailer | the other board's image: refused **by name** before a frame is sent (`this is an EPS-LKAS image ... must never be installed on the BLR-v1 lamp board`, and the reverse). An EPS image on the lamp board would drive PB10 high, its UART idle level - the brake lamps on. |

Behind all five is the board itself: the BLR bootloader refuses an `APL1` image at `CEND` 1 with NAK 8 (and closes
the session), a truncated one at `FINISH` (NAK 8), and repeats every image check before its boot copy.

**The other direction has no such backstop** (board review round 1, BOOT-7). The deployed EPS bootloader checks no
marker at `CEND`, `FINISH` or its copy, only at the jump: a `BLR1` image that got past the comma would be staged,
**erase the EPS application** and then not run - the EPS board in its bootloader, relays in bypass, the car stock and
without its LKAS function until an EPS image is pushed again. Nothing unsafe, but the comma's refusal of a `BLR1` (or
`BLB1`) image on the EPS target, by name and before a frame, is the only thing that keeps the EPS application intact.
`test_apl1_and_blr1_are_refused_on_each_others_board` pins it.

### 13.2 The flasher

`eps_lkas_flasher.py` has a `Target` per board (`EPS`, `BLR`, `TARGETS = {"eps": EPS, "blr": BLR}`): a frozen
dataclass compared by identity (`eq=False`; `errs` and `words` are read-only mappings). `run_flash(..., target=)`,
`Flasher(..., target=)`, `check_app_slot_image(image, target)`, `image_identity(image, target)`,
`load_bundled_image(target)` and `describe_hello(d, target)` all default to `EPS`, and with `EPS` every check, pace
and retry the BLR adds is off - which is what keeps the EPS transcript frozen. The module constants `board.py` and the
tests import (`APP_ID_OFFSET`, `APP_ID_MAGIC`, `ID_HOST`, ...) are unchanged. The protocol methods keep v1's channel
names `ID_HOST`/`ID_DATA`; `Flasher._wire_id()` maps them to the target's IDs and refuses anything else. There is no
`from __future__ import annotations` in the module: the tests load it unregistered, where `dataclasses` cannot
resolve string annotations.

| | `EPS` | `BLR` |
|---|---|---|
| IDs, ENTER magic | `0x710`/`0x711`/`0x712`, `EPSBOOT` | `0x628`/`0x629`/`0x62A`, `BLRBOOT` |
| marker, origin, slot | `APL1`, `0x08004000`, 112 KB | `BLR1`, `0x08004000`, 112 KB |
| `hello_class` | not checked (the EPS bootloader sends 0) | 2, on HELLO, the ENTER ACK and the post-install HELLO; anything else is ABORT and `wrong board answered on 0x629` |
| `allowed_variants` | none (no VARIANT field) | shadow (2), live (3); bench (1) is `bench image - never installed from the comma` |
| `refuse_dirty` | no (it installs, with a warning) | yes |
| `strict_image` | no | odd reset vector inside the image, reserved flag bits 1-3 zero, and the trailer (below) |
| `data_fps_max` | none: the echo's pace | 400 DATA frames/s (2.51 ms apart): the update shares bus 0 with the EPS board, which forwards it |
| `retry_naks` | any NAK re-sends the chunk | only NAK 1, 3 or 6, as `blr_can_update.py`: after NAK 8 the board has closed the session, and a retry would bury "image is not for this board" under "command out of sequence" |
| NAK texts | `ERRS` 0-7 | plus 8 `image is not for this board`, 9 `board flash is not dual-bank`, 10 `lamp output is on` |
| bundled image | `eps_lkas_appslot.bin` | `blr_appslot.bin` (not yet) |
| census own | `0x700`-`0x70F`, `0x711` | `0x620`-`0x62F` (the EPS board's `0x70x` stay in: a lamp update that starves the EPS board is news) |
| trace | `0x156`, `0x18F`, `0x17C`, `0x1A6`, `0x70B` -> `/data/eps-lkas-trace` | `0x17C`, `0x1A6`, `0x620`, `0x706`, `0x70B` -> `/data/blr-trace` |
| screen words | relays to stock / relays in stock / "turn the car off, then on, before driving" | lamp output off / brake lamps are pedal-only / nothing under "done" |

**The image checks for the BLR**, in order, each a string and never an exception: length, size, the marker's magic
(`APL1` and the BLR's own bootloader record `BLB1` refused by name), the origin, SP in SRAM, the reset vector in the
slot, then the strict rules, then VARIANT (bench, then anything not shadow or live), then dirty, then **the trailer**.
Every BLR app `.bin` ends, since the firmware review's round 1 (BOOT-6), with 12 bytes the post-build appends:
`{"BLRT", the whole file's length including these 12, CRC-32 of every byte before them}`, `0xFF`-padded in front to a
double word. Nothing else says how long an image is, so without it a truncated copy that still held its reset handler
passed every check, and the CRC-32 the host sends at `FINISH` was over exactly what it sent. `blr_trailer_problem()`
mirrors the bootloader's `img_trailer_ok()` and the firmware's `blr_hostlib.trailer_problem()`; a test runs both on
the same images whenever the firmware tree is reachable. The bootloader checks it again at `FINISH` and before the
copy, so a comma that skipped it would still install nothing.

**HELLO's ECC_FIXED (bit 6, review round 1, BOOT-2).** A flash page that a power cut left half-written reads back
with an uncorrectable ECC error, which on the G0 is an NMI; the BLR bootloader records it, erases the page on the next
boot (the staging header first, which cancels a staged image), and says so in that boot's HELLO.
`describe_hello(d, BLR)` reports it as `eccFixed`; the log's HELLO line says `ECC page repaired this boot`, and when it
is why the new image is not there after the `REBOOT`, the failure says so (`board reports <old> after installing <new>
- its bootloader repaired a flash page this boot, which cancels a staged image`).

**After the install** the HELLO must say `appOk`, the image's commit **and** its VARIANT: a shadow and a live image of
one commit share a hash, so the commit alone cannot prove the install happened. Success returns `(True, <hash>)`.

**The trace summary** (`BlrFlashTrace`, emitted by card into the next route as `blr flash trace ...`) is
`_summarise_blr()`: no steering is traced, so instead `n`, `d` (seconds), a lamp verdict (`lamp output off
throughout`, `LAMP OUTPUT ON DURING THE UPDATE` or `no lamp-board status seen`) and the per-phase table with, beside
the usual rpm, load, census and error deltas, the lamp board's last `0x620` STATE `bst` and output `bout`, and the
EPS board's `0x706` forwarding-ring peak `ring`, drop flags `sdrop` and FAULT `sflt` (non-zero: it fell back to bypass
under the burst).

By hand: `python -m openpilot.sunnypilot.selfdrive.pandad.eps_lkas_flasher --target blr --dry-run` (bus 0 only; with
no bundled image it says so and opens no transport).

### 13.3 The hook and pandad

`eps_lkas_hook.py` keeps one table of params per board, `KEYS = {EPS: EPS_KEYS, BLR: BLR_KEYS}` (`FlashKeys`, a
`NamedTuple`: the tests load the hook without registering it in `sys.modules`, where a dataclass cannot resolve its
annotations). The EPS's are the module constants of §4, unchanged.

| | EPS | BLR |
|---|---|---|
| request, progress, state, phase, cancel | `EpsLkasFlash*` | `BlrFlashRequested`, `BlrFlashProgress`, `BlrFlashState`, `BlrFlashPhase`, `BlrFlashCancel` |
| version, build, seen | `EpsLkasBoard*` | `BlrBoardVersion`, `BlrBoardBuild`, `BlrBoardSeenAt` |
| trace | `EpsLkasFlashTrace` | `BlrFlashTrace` (card emits both, `log_flash_trace`) |
| log prefix | `eps-lkas` | `blr` |

* **`flash_if_requested(panda_serial, bus=0, transport_factory=None, target=EPS) -> bool`.** Everything in §5.3,
  per board, on that board's params only: the onroad drop, clear-first, the Cancel read before it is cleared, never
  raising. It returns **True when it ran a session** (the request taken and not cancelled, whatever the outcome)
  and False otherwise (no request, dropped onroad, cancelled before the start). Two failure paths that used to
  escape into pandad's loop no longer do: a request that cannot be cleared (it returns False), and a transport
  whose `close()` raises (logged).
* **pandad.py**, in the window: `if not flash_if_requested(panda_serials[0]):
  flash_if_requested(panda_serials[0], target=BLR)`. **One session per window**: the lamp board is asked only if
  the EPS ran none. A second pending request waits for the next window, which the watcher opens once `./pandad` is
  back. The page never lets two be pending (§13.4), so this is the backstop.
* **`watch_for_request(process, skip_reset, params=None, targets=(EPS, BLR))`** checks each board in turn, each
  refused onroad (once per board) or cancelled by its own params; the first request that stands arms the skip and
  sends the one SIGINT. Still one thread, `eps_lkas_watch`.
* **On a lamp board success** it writes `BlrFlashState = ok <hash>`, `BlrBoardVersion`, `BlrBoardSeenAt` and
  `BlrBoardBuild`: card's dict, updated with the image's `dirty` and `variant` (the number, as card writes it from
  `0x627`: 2 shadow, 3 live), `bootloader: true` (it answered) and `dualBank: true` (its bootloader NAKs BEGIN with
  9 on single-bank flash, so an install proves it). `bootGit`, `uid` and `borEn` are the board's and stay as card
  read them. `BlrLastDrive` is the drive's record and is not touched. Event `blr.flashed` with the version and the
  variant.
* The update gate is the EPS's, unchanged: ignition on, stationary on `0x158`, engine off on `0x17C`, the countdown,
  Cancel, ABORT. On top of it the lamp board's application refuses the knock with NAK 5 when not stationary and NAK
  10 while its lamp output is on or an ON edge is pending, and turns its output off before it resets.

### 13.4 The page: what the lamp board's card and button show

`BoardLayoutMici` is `[BoardFirmwareInfo, UpdateBoardButton(), LampBoardInfo, UpdateBoardButton(BLR)]`. The EPS
pair is the class and the code it was. The lamp board's pair is visible only while `BlrBoardVersion` is set
(`lamp_board_seen()`, cached on the `REFRESH_S` tick like `board_page_visible()`), and the gateway row now shows when
**either** board has spoken. With the lamp board never seen the page is **pixel-identical** to `nsw-live`'s: the
integrator's render drew both from the same params on a frozen clock and compared them (renders below).

**The lamp board's card** (`LampBoardInfo`, 520 x 180: wider than the EPS's 360 so the usual lines do not scroll;
`lamp_board_lines()` is the pure part):

```
lamp board
1a2b3c4d • shadow • 12 min ago              (dirty) if built from an edited tree
board id a5c3e1 • boot b28113               • no bootloader / • not dual-bank when 0x62F said so
last drive: ok                              or the fault names, or LATCHED <reason> (a latch first)
```

* Line 1: `BlrBoardVersion`, the VARIANT by name from `BlrBoardBuild["variant"]`, and the age of `BlrBoardSeenAt`
  (when a fresh `0x620` was last heard - not when the identity latched).
* Line 2: from `0x62F` (`uid`, `bootGit`, `bootloader`, `dualBank`). What `0x62F` has not said yet is left out
  (`board id unknown`), never read as "no".
* Line 3: `BlrLastDrive` (`last_drive_text()`): `ok` (no fault and no latch), the firmware's fault names
  (`F_U4_NOT_SINKING` ... `F_FEEDBACK`), or `LATCHED U4|FEEDBACK|CLOCK|PIN_STUCK_HIGH` first, because a latch is what
  kept the lamp output off for the rest of that power cycle; `unknown` for a missing or malformed record.

**Its button** is titled `lamp board` (251 px of the 252 the title gets beside the icon; measured by a test; `brake
lamp`, 248 px, is the fallback if a font change breaks it). Its `_can_update()` order:

| # | test | reason |
|---|---|---|
| 1 | `BlrBoardBuild` has no `bootloader` key: `0x62F` not heard yet | `drive once` |
| 2 | `bootloader` false (or no build) | `needs SWD once` |
| 3 | `dualBank` false | `not dual-bank` (BEGIN would be NAK 9; the option byte is an SWD job) |
| 4 | no bundled image, or one the flasher would refuse (`check_app_slot_image(image, BLR)`: bench, `VARIANT` 0, dirty, not `BLR1`, the vector rules, no valid trailer) | `no image` |
| 5 | bundled hash **and variant** == the board's (a shadow and a live image of one commit share a hash) | `up to date` |
| 6, 7 | ignition, offroad, as the EPS | `ignition off`, `use always offroad` |
| - | all pass | `to <hash>` and, on a second line, `(shadow)` or `(live)` (one line would be 366 px against 322) |

`bundled_offer(target)` is `(commit, variant)` through the target (`target.image_path`, `target.marker_magic`,
`image_identity`), cached per image path on its mtime; `bundled_firmware(target)` is its hash. For the EPS it reads
the 0x110-byte head, as before. For the lamp board it reads the whole file (at most its slot, once per mtime) and
offers it only if the flasher's own `check_app_slot_image()` passes it: the trailer covers the whole file, and an
image the comma would refuse is no offer rather than a press that fails.

**One update at a time.** Each button's `_busy()` is true while **either** board's state is `running` or its
request is set, or either button has asked (`set_peers()`). The other board's button is then disabled and says
`busy`. The confirm re-checks it, and writes only its own board's params, in the EPS's order, then opens
`GatewayFlashDialog(target)`.

**`_asked` no longer sticks** (§11.2, first item). A non-empty state other than `running` with the request cleared
means the run is over, so `_asked` is cleared even when `running` was never seen. With two boards a stuck `_asked`
would have held both buttons busy until the UI restarted.

**The screen** is the same `GatewayFlashDialog`, from the board's own params, in its own words (`target.words`):

| phase | EPS | BLR |
|---|---|---|
| `click` | relays to stock / the car is on stock wiring | lamp output off / brake lamps are pedal-only |
| `stock` | relays in stock, updating in 5..1 | lamp output off, updating in 5..1 |
| `ok` | done / turn the car off, then on, before driving | done |

Everything else (checking, erasing, writing, the gate's refusals, Cancel) is both boards'.

**Renders** (headless raylib at 2x, the integrator's, 2026-10-14; not in the repo): the page with the lamp board never
seen (identical to `nsw-live`), seen with no image (`no image`), and seen with a fake sealed shadow image (`to
9f8e7d6c (shadow)`), its slider, the screen through the countdown to `done`, and the page mid-countdown (`in 3` beside
the EPS's `busy`).

### 13.5 Tests

| file | tests | what it pins for the lamp board |
|---|---|---|
| `test_eps_lkas_flasher.py` | 42 | `test_eps_wire_transcript_is_frozen` (H1): 16 EPS scenarios on the fake clock - a full update, the dry run, each cancel point, the engine gate - every frame sent, every phase and every log line hashed against literals captured **before** the refactor. Unmodified since. |
| `test_blr_flasher.py` | 30 + 3 skipped | the BLR target's literals, and against the firmware's headers when the tree is reachable (IDs, magic, class, NAK codes, marker, VARIANT, the trailer and ECC_FIXED); the trailer against the firmware's own `blr_hostlib`; APL1/BLR1/BLB1 refused by name; bench, dirty, VARIANT 0, the vector rules and every trailer fault refused; `BlrSim` (the bootloader and the app's knock on a fake clock): a full update, pacing <= 400/s, the class check before ENTER and before a byte, every cancel point, the engine gate, NAK 5/8/9/10, lost frames, a resident bootloader, the VARIANT read back, a truncated image refused at FINISH, an ECC repair, the trace. The 3 skipped are the bundled-image twins, which bind once `blr_appslot.bin` exists. |
| `test_eps_lkas_hook.py` (`TestTwoBoards`, 12 of 25) | | **one session per window**, run from `pandad.py`'s own statement; a cancelled EPS request leaves the window free; each board clears only its own request; the watcher on the lamp board's params (offroad, onroad, cancelled, both pending: one SIGINT); the Cancel read before it is cleared; the identity written after an install (with and without `0x62F`); no image is a clean failure; never raising; the page's and the hook's tables name the same params; the `Blr*` params have their `EpsLkas*` twins' lifetimes |
| `test_gateway_lamp_board.py` | 18 | `flash_view()` in the lamp board's words and never "relay" or "turn the car off"; the card; the last drive; `bundled_offer()` as the flasher's verdict on the whole file (a truncated copy, a bare `.bin`, a changed byte, an even reset vector: no offer); and the page driven in a headless raylib window (child process): the pair hidden until the board has spoken, the EPS pair unchanged with the lamp board present, titles that fit, the gate, `_busy()` across both, the confirm writing only its own params, the screen's Cancel, a run over inside one tick, `no image` |
| `test_blr_board.py`, opendbc `test_blr.py` | 19, 14 | the data side and E.7 ([BRAKE-LAMP-RELAY.md](BRAKE-LAMP-RELAY.md)) |
| adjusted | | `test_every_param_the_hook_and_the_page_use_is_registered` resolves the key tables and requires the `Blr*` keys; `test_the_button_title_leaves_room_for_its_sub_label` checks every target's title; `test_no_glyphs_the_baked_font_does_not_have` also checks the targets' screen words; `test_the_ui_shares_the_marker_constants_with_the_flasher` reads them through the targets and requires the lamp board's offer to be `check_app_slot_image()`'s verdict; `test_the_confirm_opens_the_screen_and_clears_a_stale_cancel` follows the confirm to `keys.*` and `GatewayFlashDialog(self._target)` |

### 13.6 Inventory and commits (branch `blr`)

sunnypilot, on `nsw-live` `55d9562b6`:

| file | status | BLR content |
|---|---|---|
| `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_flasher.py` | modified | `Target`, `EPS`, `BLR`, `TARGETS`; the BLR constants; `target=` everywhere; `blr_trailer_problem()`; `_summarise_blr()`; `--target` |
| `openpilot/sunnypilot/selfdrive/pandad/eps_lkas_hook.py` | modified | `FlashKeys`, `KEYS`, `target=`, the bool return, the watcher over both |
| `openpilot/selfdrive/pandad/pandad.py` | modified | the lamp board asked in the window if the EPS ran none (`FORK(BRAKE-LAMP-RELAY)`) |
| `openpilot/selfdrive/ui/sunnypilot/mici/layouts/board.py` | modified | `BoardKeys`, `LampBoardInfo`, `UpdateBoardButton(target)`, `bundled_offer()`, `lamp_board_seen()` |
| `openpilot/sunnypilot/selfdrive/car/blr_board.py` | added | `BlrBoardParams`: card's stage/write of the `Blr*` params |
| `openpilot/selfdrive/car/card.py` | modified | `BlrBoardParams` staged and written; `BlrFlashTrace` emitted |
| `openpilot/cereal/custom.capnp` | modified | `CarStateSP.brakeLampRelay @5 :BrakeLampRelay` |
| `openpilot/common/params_keys.h` | modified | the 10 `Blr*` keys |
| tests | added / modified | `test_blr_flasher.py`, `test_gateway_lamp_board.py`, `test_blr_board.py`; `test_eps_lkas_flasher.py` (H1), `test_eps_lkas_hook.py`, `test_gateway_board_settings.py`, `test_gateway_flash_screen.py` |

opendbc, on `dc7f1e7d`: `_sunnypilot_blr.dbc` (added) and its import in `honda_accord_au_2015_can.dbc`;
`carstate.py` (registration), `carstate_ext.py` (`_update_blr()`), `structs.py` (`BrakeLampRelay`), `test_blr.py` and
`fixtures/blr_golden_frames.json` (added), `FORK.md` section D.

| commit | what |
|---|---|
| `3e059dd8d` | H1: `test_eps_wire_transcript_is_frozen`, captured on the unmodified flasher, nothing else |
| `12825fedf` | the data side: opendbc `f0a5284a`, `brakeLampRelay`, the `Blr*` params, `blr_board.py`, card |
| `e1c4c4365` | H2: the `Target` refactor and the BLR target, `test_blr_flasher.py` |
| `1809869ec` | H3: hook, pandad, page, `test_gateway_lamp_board.py` |
| `62ea8c6cc` | H3's docs (this section's first version) |
| integration | the trailer and ECC_FIXED (firmware review round 1), the lamp board's offer as the flasher's verdict; this section, the README rows, the changelog |

To find the BLR hunks after a merge, grep for `FORK(BRAKE-LAMP-RELAY)`, `Blr`, `blr_`, `BLR_`, `brakeLampRelay`,
`_update_blr` and `lamp board`.

### 13.7 The firmware it was checked against

`blr-v1` `31c138f`, then `de2f2d1` (2026-10-14): the firmware review's round 1 (`59b1c77`..`de2f2d1`) was committed
while this branch was being integrated. What it changed for the comma: the app image trailer
(`BLR_TRAILER_MAGIC`/`BLR_TRAILER_BYTES`, BOOT-6; `BLR-CAN.md` §5 "Refuse an image without its trailer"), HELLO's
`BLR_BOOT_HELLO_ECC_FIXED` (BOOT-2) and BOOT-7 (§13.1); the bootloader's CAN filters (HW-4) and the `0x1FA` counter
rule need nothing here, and the DBC and the golden frames did not change. `test_blr_flasher.py` passes against
`de2f2d1` with the tree reachable. **Whenever the firmware moves:** re-run it with the firmware tree reachable
(`BLR_FIRMWARE_DIR` or `/mnt/s/...`): `test_blr_target_constants_match_the_firmware` and
`test_the_trailer_is_the_firmware_post_builds` fail on any difference.

### 13.8 Never

Nothing the lamp board sends or the page stores feeds `brakePressed`, `brakeLights`, a controls decision or panda
safety (board design E.7; tests in BRAKE-LAMP-RELAY.md). A failed lamp board update leaves the car's own pedal-driven
lamps: the board's application turns its output off before it resets on the knock, and its bootloader never drives
it.
