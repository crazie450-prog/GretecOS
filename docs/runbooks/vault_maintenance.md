# Vault I/O and idle maintenance: PC steps (WO-2.5)

Clayton runs these on the gaming PC after the WO-2.5 PR is merged. They take about 25 minutes, plus one idle half hour for step 7. Decisions: D138 (vault I/O and the path guard), D139 (idle maintenance), D140 (INDEX.md and note_fts), D141 (the note-format measurement).

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
   It sends the text of every vault note (`me/` included) and two small DB tables to Anthropic's token-counting endpoint with your key, one call at a time: about 400 calls, a few minutes. It prints the totals per format and model, each kind of sample's share (`kind  model  samples  gos_tokens  share_of_gos_pct  gos_vs_smallest`) and a `verdict_<model>: keep` or `revisit` line for Haiku, Sonnet and Opus 5.5, and writes `%USERPROFILE%\GregtecOS\data\reports\note-formats-YYYYMMDD.txt`. If it ends with `count_tokens failed: ...` (rate limited or overloaded), it has already retried that call for about four minutes and kept the counts so far in `note-formats-YYYYMMDD.partial.txt`: wait ten minutes and run it again. Paste the printed head (everything above `report:`) into the PR or a chat with the PM: the format decision is recorded from those numbers (D141's rule: `keep` for every model freezes the grammar as schema version 1).

## D. The idle rule, live

6. **The PC stays awake for an idle half hour.** The daily run needs 30 minutes without input while the PC is awake, and waking it counts as input, so a PC that sleeps after 30 idle minutes never runs maintenance (no backups). Check the sleep timeout when plugged in:
   ```powershell
   powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE
   ```
   `Current AC Power Setting Index` is in seconds, in hex: `0x00000000` (never) or anything above `0x00000744` (31 minutes) is fine; `0x00000708` (30 minutes) or less is not. The same check for `HIBERNATEIDLE` in place of `STANDBYIDLE`. To change it (your call: it is a power setting), Settings > System > Power & battery > Screen, sleep & hibernate timeouts > "When plugged in, put my device to sleep after": 45 minutes or longer, or Never. If you'd rather keep a short timeout, say so in the PR (Questions): backups would then need another trigger. The health strip warns `no run for N days` once maintenance hasn't run for more than 3 days.
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

## E. If something fails

- `compact  skipped  no Anthropic key stored`: store it with `gos key set anthropic`; the logs wait until then.
- `compact  skipped  ... held after 3 refused summaries`: the summariser's answer for those periods was refused three times (usually a count or total that isn't in the logs). `inbox\ib-compact-held.md` lists them and the last reason; no more calls are made for them. `gos vault maintain --job compact` tries them again (one call each).
- `compact  skipped  the cycle's included credits are spent`: compaction waits for the next credit cycle (D48).
- `Maintenance  warn  ... no run for N days`: see step 6 (the PC sleeps before an idle half hour), or the game was running whenever you were away.
- `git  failed  ... isn't a git repo`: the vault was not made by `gos vault deploy`; deploy into a new folder.
- A `failed` job writes its reason in `%USERPROFILE%\GregtecOS\logs\core.log` (the core's run) and on the `gos vault maintain` line (a manual run).
- `inbox\ib-schema-check.md` lists the notes that don't follow `_gos/schema.md`; it disappears once they do.
