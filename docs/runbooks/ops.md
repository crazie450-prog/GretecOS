# Runbook: operations (WO-11.2)

Backups verified by a restore, the world restore, crash recovery, the cost alerts and the Claude
Code isolation re-test (plan §4.6, §10 "Ops"; D75, D332-D334). Every step that touches Play,
GOS_APP or `backups\world` is **Clayton's**, run from GOS_APP in his own account. Claude Code
(gos-dev) never runs them. Moving the core to another PC is a separate runbook:
[relocation.md](relocation.md).

## What is backed up, when and where

| what | where | made by | kept |
|---|---|---|---|
| the core DB (`data\gos.sqlite`) | `data\backups\gos-YYYYMMDD.sqlite` + `.manifest.json` (size, SHA-256, every table's rows) | the daily idle-maintenance job (WO-2.5, D139); also at session end once the daily run is 2 days late (D142 (g)) | `[maintenance] keep_backups` (7) |
| the vault (notes, `me\`, `_gos\`, `.git`) | `data\backups\vault-YYYYMMDD.zip` + `.manifest.json` (every file's size and SHA-256) | the same job (WO-11.2, D332) | the same count |
| Play's world | `backups\world\*.zip` (copies of ServerUtilities' backups) | `backup_world.ps1`, Clayton's scheduled task (WO-0.8) | 14 days, at least 12 copies |

A backup gets its manifest before it gets its name, so a power cut never leaves a backup that
looks whole. `gos vault maintain --job backup` makes today's backups now.

## 1. Check the DB and vault backups (Clayton or gos-dev; reads only)

```powershell
gos backup list
gos backup restore --verify
```

`restore --verify` copies the newest DB backup and the newest vault zip into a temporary folder,
checks the DB's hash, `PRAGMA integrity_check` and every table's row count against its manifest,
extracts the vault and checks every file's hash (none missing, none extra), prints `ok` or
`FAIL` per check, removes the folder and exits 0, or 1 with `RESTORE CHECK FAILED`. Use
`--date YYYYMMDD` for another day. Pass: exit 0.

## 2. Restore the DB or the vault (Clayton; manual, GOS stopped)

1. `gos backup restore --to C:\Temp\gos-restore` (a new or empty folder, never inside GOS_HOME's
   `data` or `vault`; the command refuses those). It verifies what it restored.
2. `gos stop --all` and close Obsidian.
3. Move the live file aside, never delete it: rename `GOS_HOME\data\gos.sqlite` (and any
   `gos.sqlite-wal`, `gos.sqlite-shm` beside it) to `*.broken`, or `GOS_HOME\vault` to
   `vault.broken`.
4. Copy `C:\Temp\gos-restore\data\gos.sqlite` to `GOS_HOME\data\`, or
   `C:\Temp\gos-restore\vault` to `GOS_HOME\vault`.
5. Log off and on (the supervisor starts at login), or `gos serve`. Check the dashboard's
   health strip and `gos doctor`.
6. Rollback: stop GOS again and put the `*.broken` copies back.

The recipe DB tables come back with the core DB; `gos db build` can rebuild them from the newest
collected export at any time.

## 3. Check a world backup (Clayton; `backups\world` is closed to gos-dev, D75)

```powershell
& "$env:GOS_APP\current\.venv\Scripts\python.exe" "$env:GOS_APP\current\scripts\verify_world_backup.py"
```

It copies the newest `backups\world\*.zip` (or `--zip FILE`) into a scratch folder in `%TEMP%`
with share-delete reads, checks every entry's CRC, `level.dat` (gzip'd NBT) and every region
file's chunks (inside the file, length and compression right, each inflating to NBT), then
removes the scratch folder. It refuses a `--work` folder in or around a Prism instance or the
backups folder, and never writes anywhere else. Pass: `ok   the world restores and reads`,
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

Tested by killing each writer hard at varied moments (`tests/unit/ops/test_crash_recovery.py`):

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
- **Power cut mid-write**: the DB keeps every committed transaction (WAL, synchronous FULL); every
  GOS file is fsynced before the rename that publishes it.

After a crash, check: `gos status`, the health strip, `gos backup restore --verify`.

## 6. Cost alerts (D48, D334)

| alert | when | where |
|---|---|---|
| heads-up | the cycle's spend passes `[budget] notice_pct` (50%) | dashboard Costs, a Discord DM (info) |
| credit alert | 80% and 100% (`credit_alerts_pct`, D48): buttons; 100% pauses to no-AI | banner, DM with buttons |
| day cap | today's spend reaches `[budget] day_max_usd` ($10) | Costs "Caps" card, a DM (warn); nothing pauses |
| task cap | a task reaches `[budget] task_max_usd`; the task already paused and asked (D156) | Costs "Caps" card, a DM (warn) |

Each is sent once per cycle, day or task, a restart included. Quiet hours hold them (they are
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
