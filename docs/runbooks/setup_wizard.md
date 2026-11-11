# Setup wizard (WO-2.9)

The first-run setup wizard configures GOS from the dashboard alone (plan 7.14 rule 8, D167-D169).

## What it does

1. **Paths:** Prism folder, instances folder, Play / Export / Lab instance names, GOS_APP, GOS_HOME. Checked with `gos doctor`'s checks before saving.
2. **Anthropic API key:** stored in Windows Credential Manager; **Test** makes one tiny fast-lane call (metered as `setup-test`).
3. **Discord bot token** (optional until WO-9.1): Credential Manager.
4. **Headset:** microphone (`[voice] input_device`) and the output for spoken answers (`[voice] output_endpoint`, the Arctis Nova Elite's Chat endpoint, D56).
5. **Talk key** and mode (QA:V8, D180).
6. **Wake phrase** (optional, PM08; WO-5.4 has the last word).
7. **Checklist:** doctor's checks with Re-run; **Finish setup** writes `[setup] completed_utc` to gos.toml.

Saved settings take effect at GOS's next start (log in again, or `gos stop` then `gos serve`). Secrets work at once. Re-open the wizard any time: Settings > Setup wizard (`#/setup`).

The dashboard opens on the wizard alone only on a first run (no gos.toml yet). An install whose gos.toml has no `[setup]` table (one from before this wizard) shows a "Setup isn't finished" banner with **Open setup** and **Dismiss** instead; the wizard then has a **Later** button.

The wizard never moves Play: once gos.toml names Play, changing the Play instance or the instances folder is refused ("edit gos.toml by hand"), and Export or Lab can never be Play's folder. GOS_HOME and GOS_APP can never be inside Play or hold it, and a check never opens, probes or creates a file of GOS's own in or around any Play, Prism's instances folder, Export or Lab, links and junctions followed: that covers GOS_HOME's data, logs, vault, backups and run folders too (one of them linked into Play is an error), the GOS_HOME write probe and the DB's GT pin (read immutable, so SQLite leaves no -wal or -shm file). `gos doctor` likewise reads no DB and writes no env.json through a GOS_HOME that leads into an instances folder (D223).

### Former Plays (D223)

Every folder that has ever been Play for this install is on record, in two places that are read together: `[setup] former_plays` in gos.toml and `former_plays.txt` beside gos.toml (`GOS_HOME\config\former_plays.txt`). The wizard adds gos.toml's Play and the running core's Play to both at every save and Finish (and to the file at every check once gos.toml exists), before it writes the new Play. The core adds the Play it runs with every time it starts, and `sync_instances.py` and `gos export run` add the Play they sync from before they write (a dry run writes nothing), so a Play set only by hand is on record too. Every entry is the folder resolved, however `[paths]` spells it. The wizard refuses to write gos.toml or the record in or around any Play or instance, the ones being saved included; if gos.toml itself lies there, the paths step and the checklist show an error: start GOS with GOS_HOME (or `--config`) outside them. It never removes an entry. Export or Lab in or around a folder on record is refused by the wizard, by Finish and by `sync_instances.py` (`gos launch` syncs, and the pack-update Lab export), whatever `[paths]` says now. So moving Play by hand, swapping Play and Lab by hand, restarting, or deleting gos.toml never turns the old Play into a sync target.

To clear an entry on purpose (only when that folder is no longer a Play you keep, e.g. you deleted that instance and want its name for Lab): close GOS (`gos stop`), delete the line from `former_plays.txt` **and** the path from `[setup] former_plays` in gos.toml, then start GOS. Both must go; one left behind still blocks sync. A record file that can't be read stops sync and the core with a message naming it.

## Clayton's PC steps (deployment test)

```powershell
$ProgressPreference = 'SilentlyContinue'
cd <your own clone>
git pull
uv run gos deploy                # installs into GOS_APP and builds the dashboard
```

Then open the dashboard on monitor 2 (`http://gos.localhost:8765/`), pair it if asked (`gos pair` shows the code), and walk the wizard:

- Paths: every row green for Play, Export and Lab.
- Store the Anthropic key and press **Test**: "the API answered on claude-haiku-5-5".
- Headset: pick the Arctis Nova Elite **Chat** endpoint for spoken answers; check the microphone.
- Talk key: keep F13 (or your mapped key); the "Talk key in GTNH" row reads Play's options.txt (read only) and should say it is free. Check Discord's Keybinds page by hand.
- Finish, then `gos stop` and `gos serve` so the core reads the new gos.toml.
- Compare the wizard with the approved "First-run setup wizard" board (D92) and note differences.

## Tests

- `uv run pytest tests/unit/setup` (gos.toml edits, steps, secrets, routes; fake keyring and fake Prism tree).
- `pnpm -C dashboard test` (`tests/setup.test.tsx`).
- `pnpm -C dashboard e2e` (`e2e/setup.spec.ts`: an empty GOS_HOME configured by the wizard alone, against `serve_core.py --fresh`; then `gos doctor --prism-root <fake Prism> --gos-home <fresh GOS_HOME> --no-hash` must exit 0, and a Play/Lab swap is refused).
