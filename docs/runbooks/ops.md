# Runbook: operations (WO-11.2)

Backups verified by a restore, the world restore, crash recovery, the cost alerts and the Claude
Code isolation re-test (plan §4.6, §10 "Ops"; D75, D332-D334). Every step that touches Play,
GOS_APP or `backups\world` is **Clayton's**, run from GOS_APP in his own account. Claude Code
(gos-dev) never runs them. **`gos` in your own window** is GOS_APP's copy: paste `$gos = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"` once, then type `& $gos ...` wherever this page writes `gos ...` for you (nothing puts `gos.exe` on your PATH, and `uv run gos` in your clone would run the clone's code, not the deployed one). Moving the core to another PC is a separate runbook:
[relocation.md](relocation.md).

## What is backed up, when and where

| what | where | made by | kept |
|---|---|---|---|
| the core DB (`data\gos.sqlite`) | `data\backups\gos-YYYYMMDD.sqlite` + `.manifest.json` (size, SHA-256, every table's rows) | the daily idle-maintenance job (WO-2.5, D139); also at session end once the daily run is 2 days late (D142 (g)) | `[maintenance] keep_backups` (7) |
| the vault (notes, `me\`, `_gos\`, `.git`) | `data\backups\vault-YYYYMMDD.zip` + `.manifest.json` (every file's size and SHA-256) | the same job (WO-11.2, D332) | the same count |
| Play's world | `backups\world\*.zip` (copies of ServerUtilities' backups) | `backup_world.ps1`, Clayton's scheduled task (WO-0.8) | 14 days, at least 12 copies |

A backup gets its manifest before it gets its name, so a power cut never leaves a backup that
looks whole. `gos vault maintain --job backup` makes today's backups now.

Backups made before WO-11.2 had no manifest: the first backup run after deploying gives every
DB backup still kept one (it vouches only that the file is unchanged from then on). Vault zips
did not exist before WO-11.2, so every one has its manifest.

## 1. Check the DB and vault backups (Clayton or gos-dev; reads only)

```powershell
gos backup list
gos backup restore --verify
```

`restore --verify` copies the newest DB backup and the newest vault zip into a temporary folder,
checks the DB's hash, `PRAGMA integrity_check` and every table's row count against its manifest,
extracts the vault and checks every file's hash (none missing, none extra), prints `ok` or
`FAIL` per check, removes the folder and exits 0, or 1 with `RESTORE CHECK FAILED`. Use
`--date YYYYMMDD` for another day. Pass: exit 0. If the newest DB and vault backups are from
different days (a vault snapshot cut short), it says so (`backup days`) and checks each.

## 2. Restore the DB or the vault (Clayton; manual, GOS stopped)

1. `gos backup restore --to C:\Temp\gos-restore` (a new or empty folder; the command refuses
   one in or around GOS_HOME's `data`, `vault` or `backups`, Prism or GOS_APP). It verifies
   what it restored. It restores the DB and the vault as a pair from one day: if the newest of
   each are from different days it fails `backup days`; add `--date YYYYMMDD`
   (`gos backup list` shows the days).
2. `gos stop --all` and close Obsidian.
3. Move the live file aside, never delete it: rename `GOS_HOME\data\gos.sqlite` (and any
   `gos.sqlite-wal`, `gos.sqlite-shm` beside it) to `*.broken`, or `GOS_HOME\vault` to
   `vault.broken`.
4. Copy `C:\Temp\gos-restore\data\gos.sqlite` to `GOS_HOME\data\`, or
   `C:\Temp\gos-restore\vault` to `GOS_HOME\vault`.
5. If you replaced the vault: re-run `setup_gos_dev.ps1` (as in the PC runbook) so gos-dev can
   read the vault again. Its read ACL was on the old folder (D82), and a copied-in folder does
   not have it.
6. Log off and on (the supervisor starts at login), or `gos serve`. Check the dashboard's
   health strip and `gos doctor`.
7. Rollback: stop GOS again and put the `*.broken` copies back (and re-run
   `setup_gos_dev.ps1` if it was the vault).

The recipe DB tables come back with the core DB; `gos db build` can rebuild them from the newest
collected export at any time.

## 3. Check a world backup (Clayton; `backups\world` is closed to gos-dev, D75)

```powershell
& "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\verify_world_backup.py"
```

It copies the newest `backups\world\*.zip` (or `--zip FILE`) into a scratch folder in `%TEMP%`
with share-delete reads, checks every entry's CRC, `level.dat` (gzip'd NBT) and every region
file's chunks (inside the file, length and compression right, each inflating to NBT), then
removes the scratch folder. It refuses a `--work` folder in or around Prism, the backups
folder, GOS_APP, the vault or `data`, and never writes anywhere else. It needs free space for
the copy and twice the world's unpacked size; short of that, or if the zip can't be read
(locked, access denied), it prints `FAIL` and exits 1 (`--work` on a bigger drive). Pass: `ok   the world restores and reads`,
exit 0. `--keep --work C:\Temp\world-check` keeps the restored world to look at.

## 4. Restore Play's world from a backup (Clayton only; manual, never scripted)

Only after step 3 passed on the zip you mean to use.

1. Close Minecraft. `gos stop` (the watchers read the world).
2. In Prism, Play > Folder > `saves\`: rename `<world>` to `<world>.before-restore` (keep it
   until the restored world has been played).
3. `--keep --work C:\Temp\world-check` from step 3 left the world in
   `C:\Temp\world-check\world`: copy that folder into `saves\` as `<world>` (the name
   `[paths] world` gives).
4. Start Play, load the world, look around the base, quit. Then `gos serve`.
5. Rollback: close the game, delete the restored `<world>`, rename `<world>.before-restore` back.

## 5. Crash recovery: what to expect

Tested by killing each writer hard at varied moments (`tests/unit/ops/test_crash_recovery.py`).
The core and the supervisor are killed as real processes; for the watchers, the Discord bot
and voice, their state writers (the spool, tails, the bot's `alerts.json`, the confirm path)
are killed in a child process, not the whole process:

- **Core killed** (crash, `taskkill /F`, power cut): the supervisor restarts it (1 s, then
  back-off). On start a proposal still waiting is *expired* and a confirmed action not yet sent
  is *failed* ("the core restarted before sending it; nothing ran"), both said and shown; one
  already sent stays sent and is never sent again (a result, or "may have run" after 5 min).
  Half-written vault temp files are removed; notes are always whole.
- **Supervisor killed**: the core, voice, watchers and the bot leave on their own (their stdin
  closes). Log off and on (the Run key starts it, D116), or `gos serve`; nothing is stuck.
- **Watchers killed**: events are in the spool and are sent again; the core stores each once.
- **Discord bot killed**: a DM it posted but hadn't marked is posted again (at least once, D295);
  a button pressed twice finds the action or credit choice already answered.
- **Voice killed**: nothing to recover; a "confirm" it sent counts once, a repeat is refused.
- **Power cut mid-write**: the DB keeps every committed transaction (WAL with
  `synchronous=FULL`, set on every connection and tested); every GOS file is fsynced before the
  rename that publishes it (tested by the call order, not by cutting power).

After a crash, check: `gos status`, the health strip, `gos backup restore --verify`.

## 6. Cost alerts (D48, D334)

| alert | when | where |
|---|---|---|
| heads-up | the cycle's spend passes `[budget] notice_pct` (50%) | dashboard Costs, a Discord DM (info) |
| credit alert | 80% and 100% (`credit_alerts_pct`, D48): buttons; 100% pauses to no-AI | banner, DM with buttons |
| day cap | today's spend reaches `[budget] day_max_usd` ($10) | Costs "Caps" card, a DM (warn); nothing pauses |
| task cap | a deep-lane task pauses at `[budget] task_max_usd` (or 2x, 3x after approvals) and asks (D156); clears when answered | Costs "Caps" card, a DM (warn) per pause |

Each is sent once per cycle, day or task pause, a restart included. Quiet hours hold them (they are
below `err`). Check the routing with `gos alerts routed` while one is active.

## Clayton's PC steps

1. After merging and `gos deploy`: `gos vault maintain --job backup`, then
   `gos backup restore --verify`. Expect exit 0 with both `ok   db row counts` and
   `ok   vault file hashes`.
2. Run step 3 (world backup check) once. Expect `ok   the world restores and reads`.
3. Once, on a copy, rehearse step 4 with Lab's world (Lab > `saves\`), not Play's, to learn it.
4. **Isolation re-test (D75, V24):** in a gos-dev PowerShell, from `C:\GregtecOS-dev\GretecOS`:
   ```powershell
   $env:GOS_LIVE_ISOLATION = '1'
   $env:GOS_CLAYTON_HOME = 'C:\Users\<you>\GregtecOS'
   uv run pytest tests/security/test_isolation_live.py -v
   ```
   Then, as gos-dev, confirm `Get-ChildItem C:\Users\<you>\GregtecOS\backups\world` is refused.
   Pass: every test passes, the listing is denied.
5. Cost alerts with Discord set up: `gos alerts test` reaches your DMs; the next 50% heads-up
   will arrive the same way. Change `[budget] day_max_usd` or `notice_pct` if the defaults
   don't suit you (pending your answer, D334).
6. Tick §10's Ops line: "DB and off-instance world backups restored once; Claude Code isolation
   re-tested; cost dashboard".
