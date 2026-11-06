# Vault I/O and idle maintenance: PC steps (WO-2.5)

Clayton runs these on the gaming PC after the WO-2.5 PR is merged. They take about 25 minutes, plus one idle half hour for step 7. Decisions: D138 (vault I/O and the path guard), D139 (idle maintenance), D140 (INDEX.md and note_fts), D141 (the note-format measurement), D143 (your D142 answers: `me/` never measured, compaction stops at the 80% credit alert, counts computed in code, the session-end backup).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91), run in a normal PowerShell window (not "Run as administrator"). The vault is `%USERPROFILE%\GregtecOS\vault`.

## A. Deploy

1. In the profile clone: `git pull`, then `uv run gos deploy`. GOS restarts with the new code.
2. Mirror the new `_gos/schema.md` into the live vault (it now says INDEX.md is generated and gains `summary:`; notes and `me/` are not touched):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe vault deploy
   ```
   Expect `updated: C:\Users\<you>\GregtecOS\vault`.

## B. The jobs, run by hand once

3. Run every job now, whatever the idle rule says:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe vault maintain
   ```
   Expect six lines, one per job, none `failed`:
   ```text
   compact  ok  nothing old enough
   schema   ok  every note follows the schema
   index    ok  rebuilt
   fts      ok  <n> notes
   backup   ok  wrote gos-YYYYMMDD.sqlite
   git      ok  committed <hash>
   ```
   (`backup  skipped  no DB yet` is fine before the first `gos db build`.) `INDEX.md` now has the builder's rows; `%USERPROFILE%\GregtecOS\data\backups\` has today's backup. In the vault, `git log -1 --format="%s %an"` prints `GOS idle maintenance YYYY-MM-DD Gregtec OS`.
4. Run it again: `index  ok  unchanged` and `git  ok  nothing to commit`. An unchanged vault writes nothing.

## C. The token re-measurement (D141): the format decision

5. With the Anthropic key stored (`gos key status` shows it; the count_tokens endpoint is free, nothing is spent):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe vault measure
   ```
   It sends the text of every vault note except your own `me/` (never read or sent, D142 (d); `INDEX.md` goes without its `me/` rows, which copy those notes' summaries) and two small DB tables to Anthropic's token-counting endpoint with your key, one call at a time: about 400 calls, a few minutes. It prints the totals per format and model, each kind of sample's share (`kind  model  samples  gos_tokens  share_of_gos_pct  gos_vs_smallest`) and a `verdict_<model>: keep` or `revisit` line for Haiku, Sonnet and Opus 5.5, and writes `%USERPROFILE%\GregtecOS\data\reports\note-formats-YYYYMMDD.txt`. If it ends with `count_tokens failed: ...` (rate limited or overloaded), it has already retried that call for about four minutes and kept the counts so far in `note-formats-YYYYMMDD.partial.txt`: wait ten minutes and run it again. Paste the printed head (everything above `report:`) into the PR or a chat with the PM: the format decision is recorded from those numbers (D141's rule: `keep` for every model freezes the grammar as schema version 1).

## D. The idle rule, live

6. **The PC stays awake for an idle half hour.** The daily run needs 30 minutes without input while the PC is awake, and waking it counts as input, so a PC that sleeps after 30 idle minutes never runs maintenance (no backups). Check the sleep timeout when plugged in:
   ```powershell
   powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
   ```
   `Current AC Power Setting Index` is in seconds, in hex: `0x00000000` (never) or anything above `0x00000744` (31 minutes) is fine; `0x00000708` (30 minutes) or less is not. The same check for `HIBERNATEIDLE` in place of `STANDBYIDLE`. To change it (your call: it is a power setting), Settings > System > Power & battery > Screen, sleep & hibernate timeouts > "When plugged in, put my device to sleep after": 45 minutes or longer, or Never. If you keep a short timeout, the DB backup still runs at session end once the daily run is 2 days late (step 9, D142 (g)), but compaction, the index and the schema check wait. The health strip warns `no run for N days` once maintenance hasn't run for more than 3 days.
7. **Runs when idle and the game is closed.** `gos vault maintain` doesn't count as the core's daily run, but the core may already have run today. To see the scheduler do it (as Clayton, with GOS running):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe status
   ```
   shows a `Maintenance` line (`ok  not run yet; ...` or `last run ...; done for <today>`). Then make today's run due again (no job runs now):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe vault maintain --rerun-today
   ```
   Leave the PC alone for 31 minutes with GTNH closed (screen saver and lock are fine). `gos status` then shows `Maintenance  ok  last run <today>; done for <today>`, and the vault has a new `GOS idle maintenance` commit if anything changed.
8. **Waits while the game runs.** Run `gos vault maintain --rerun-today` again, start Play, and leave it at the main menu for 31 minutes without touching the PC. `gos status` shows `Maintenance ... the game is running`, and no run happens until you quit and stay idle another 30 minutes.
9. **The session-end backup (D142 (g)).** When the daily run hasn't happened today or yesterday (`kv` `maintenance.last_day`, or since the first day the core checked), each session end also writes that day's `gos-YYYYMMDD.sqlite` after its vault commit; later session ends that day find it and do nothing. Until WO-2.4 has deep-lane sessions, the session end is the core's stop (`gos stop`, a deploy, logging off), and a stopping core has 1.5 s for it before the supervisor's 5 s kill: a DB that takes longer (a 133 MB dev-runtime DB took 3.9 s in the build container) is interrupted cleanly and leaves no file. To see which happened, after GOS has been stopped on a day the daily run was 2 days late:
   ```powershell
   Select-String -Path $env:USERPROFILE\GregtecOS\logs\core.log -Pattern "session-end backup" | Select-Object -Last 3
   ```
   `"status": "ok"` with `wrote gos-YYYYMMDD.sqlite` (the file is in `data\backups\`) or `"status": "skipped"` with `cut short: out of time`. Report which in the PR: if it is cut short, the backup needs another trigger before WO-2.4 (see the PR's Questions).

## E. If something fails

- `compact  skipped  no Anthropic key stored`: store it with `gos key set anthropic`; the logs wait until then.
- `compact  skipped  ... held after 3 refused summaries`: the summariser's answer for those periods was refused three times (usually a number that is neither in the logs nor among the counts the curator computed and wrote as `n_<kind>:` lines, D142 (f)). `inbox\ib-compact-held.md` lists them and the last reason; no more calls are made for them. `gos vault maintain --job compact` tries them again (one call each).
- `compact  skipped  the cycle's spend reached the 80% credit alert`: compaction waits for the next credit cycle (D142 (e): it stops at the first of `[budget] credit_alerts_pct`, not at 100%). A run that reaches it part-way says `stopped before the rest: ...` and leaves the later periods' logs as they are.
- `Maintenance  warn  ... no run for N days`: see step 6 (the PC sleeps before an idle half hour), or the game was running whenever you were away.
- `git  failed  ... isn't a git repo`: the vault was not made by `gos vault deploy`; deploy into a new folder.
- A `failed` job writes its reason in `%USERPROFILE%\GregtecOS\logs\core.log` (the core's run) and on the `gos vault maintain` line (a manual run).
- `inbox\ib-schema-check.md` lists the notes that don't follow `_gos/schema.md`; it disappears once they do.
