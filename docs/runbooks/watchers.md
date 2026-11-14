# The game watchers, the paused state, and voice with Play (WO-6.1)

What WO-6.1 adds (D257–D259) and the checks Clayton runs on the PC. Claude Code built and tested
it on fake Prism trees only; everything below that touches a game is a step Clayton runs himself,
**on GTNH-Lab first**. Play is only ever read, by the watchers that Play's own hook starts,
and only once you set `[startup] watchers_with_play = "play"` after §2–§4 pass on Lab
(until then Play's launch starts nothing; D259).

Short names (GOS_APP `C:\GregtecOS-app`, GOS_HOME `%USERPROFILE%\GregtecOS`):

```powershell
$gos  = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
$home_ = "$env:USERPROFILE\GregtecOS"
$lab  = "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft"
$ProgressPreference = 'SilentlyContinue'
```

## How it works

- **The watchers** (`gos watch run`, run by gos-supervisor as `python -m gregtec.watchers`) read
  the instance's logs every second and its save files every 2 s, only through `safe_open` (share
  read, write and delete; open, read, close; never a held handle), and a save file only once it
  has held still for a second. They turn changes into events: `quest_done`, `item_delta`,
  `dim_change`, `location`, `death`, `chat`, `vein_found`, `block_placed`/`block_broken`, `crash`,
  `error`, `game_paused`, `export_stale` (D257), and since WO-6.2 `machine_built`/`machine_removed`
  (GregtecLink's listener) and `machine_census` (the tile-entity census of changed chunks; see
  `machine_events.md`, D260), and since WO-6.3 `world_closed` (the quit line, which ends a
  session, D264). Chat and every other game text is data, never instructions.
- **Events** go to `GOS_HOME\data\events\<date>.jsonl` and to the core (`POST /api/game/events`,
  with the `watchers` token), which keeps them in its event store, in sessions, and writes Play's
  journal (WO-6.3, `event_store.md`, D263-D265).
- **Paused** (D258): `Saving and pausing game...` in the log = paused; a line only a ticking
  server writes (chat, a command's `[Clayton: ...]` feedback, a login or logout) or the next
  autosave (at most 45 s after you close the menu) = not paused. The pause's own save, its
  `Can't keep up!` warning and mods' lines never unpause it. The dashboard
  shows the "Game paused" chip and switches to the workbench. The away DM is WO-9.2's.
- **With Play** (D259): Play's pre-launch hook tells the supervisor to start the watchers and the
  voice client (`[startup] voice_with_play`, default true); post-exit stops them once the export
  question is answered or has timed out. Lab's launch does this from the start; Play's only
  with `[startup] watchers_with_play = "play"` (§6). After a crash (no post-exit) the supervisor stops them
  itself about 6 minutes after the game's Java is gone. If GOS isn't running, the hook says so and
  the game starts anyway.

## 1. Deploy and the token

0. **Don't set `watchers_with_play = "play"` until §2–§4 pass on Lab.** Leave it unset (the
   default is `"lab"`): Play then launches as before and Prism's console prints
   `GOS: note: [startup] watchers_with_play = "lab": Play is not watched yet.`
1. After the PR is merged, from your clone: `git pull`, then `uv run gos deploy`.
2. As yourself, with GOS running: `& $gos token new watchers` (stored in your Credential Manager
   as `gregtec-os/token-watchers`). Without it the watchers only write the spool file and the
   dashboard never shows "paused".
3. If voice isn't set up yet: `& $gos token new voice` (WO-5.1). Voice with Play is on by default;
   `[startup] voice_with_play = false` in gos.toml turns it off.
4. `[paths] lab_world = "<your Lab world folder>"` in gos.toml (the watchers' Lab world).
5. Lab's hooks are installed (WO-1.4's runbook `play_hooks.md` §3 step 1 only, `--instance lab`;
   its step 6 and `phase1_pc_checks.md` step 10 remove them again, so reinstall if you ran those).
   The watchers start only from Lab's pre-launch hook.

## 2. Lab: start, pause, stop (V29, the 5 s acceptance)

1. Launch GTNH-Lab from Prism. Prism's console (if shown) prints
   `GOS: the game watchers and voice start (lab).` `& $gos status` lists **Game watchers** and
   **Voice** as running.
2. Load the Lab world, walk a few blocks, then press **Esc**. Start a stopwatch when the menu
   opens. **Pass:** the dashboard's header shows **Game paused** within 5 s.
   Also note when the line appears in the log (V29's delay):
   `Get-Content "$lab\logs\latest.log" -Tail 3 -Wait` in a second window shows
   `[Server thread/INFO]: Saving and pausing game...`. If it appears more than 3 s after Esc, say
   so in the PR: the fallback is a GregtecLink client signal (D258).
3. Press **Esc** again and stay in the menu for 60 s. **Pass:** the chip stays on the whole
   minute (the pause's save may log `Can't keep up!`; that must not clear it).
4. Close the menu. **Pass:** the chip goes away within about 45 s (the next autosave).
   Optional, to record in the PR: type a chat line (`hi`) right after closing the menu and
   note whether the chip clears sooner (`<Clayton> hi` on the Server thread in latest.log).
5. Quit Lab normally. **Pass:** `& $gos status` shows both stopped: `stopped: lab exited`.

## 3. Events (V05, V06)

1. Play the Lab world for a few minutes: chat a line, pick up and drop items, change dimension,
   complete a quest (`/bq_admin complete <id>` is fine), die once, prospect a vein.
2. `Get-Content "$home_\data\events\$(Get-Date -Format yyyy-MM-dd).jsonl" | Select-Object -Last 20`
   shows those as events.
3. **V05:** `Select-String -Path "$lab\logs\latest.log","$lab\logs\fml-client-latest.log" -Pattern '\[CHAT\]' | Select-Object -First 3`
   tells which log has the chat (the watchers read both; record which in the PR).
4. **V06:** with ServerUtilities world logging on in Lab (WO-0.6:
   `C:\GregtecOS-app\current\.venv\Scripts\python.exe C:\GregtecOS-app\current\scripts\check_tweaks.py C:\Games\PrismLauncher\instances\GTNH-Lab`
   shows `su_logging ok`), place and break a block, press Esc (a universe save), then
   `Test-Path "$lab\logs\world.log"` is `True` and the spool has `block_placed` and
   `block_broken`. If the file is elsewhere, or a broken block's line reads differently, paste one
   line in the PR.

## 4. No save errors in a 2 h session (V27, the acceptance)

1. Play Lab for 2 hours with the watchers running (step 2.1), pausing now and then (each pause is
   a full save).
2. Then, with Lab closed:
   ```powershell
   Select-String -Path "$lab\logs\latest.log","$lab\logs\fml-client-latest.log" -Pattern 'An error occurred while saving|Exception while saving|Failed to save|Could not save|AccessDeniedException|FileSystemException|being used by another process'
   ```
   **Pass:** no lines. Also `Get-Content "$home_\logs\watchers.log" | Select-String '"level": "ERROR"'`
   is empty.
3. Play's first real session repeats this check, after §6 (the watchers start with Play's own
   hook; nothing else changes).

## 5. Crash fallback (D259)

1. Launch Lab, load the world, then end its `javaw.exe` in Task Manager (a crash: Prism runs no
   post-exit).
2. **Pass:** within about 7 minutes `& $gos status` shows the watchers and voice stopped with
   `client went away (no post-exit)`.

## 6. Turn it on for Play (D259)

Only after §2–§4 passed on Lab: add to gos.toml

```toml
[startup]
watchers_with_play = "play"
```

then restart the supervisor so it reads it: `& $gos stop --all`, then sign out and in (or
`install_login_start.ps1 -StartNow`, core_service.md step 2). The next Play launch prints
`GOS: the game watchers and voice start (play).` A supervisor restart in the middle of a
session leaves the watchers stopped until the next launch (D259's known gap).
