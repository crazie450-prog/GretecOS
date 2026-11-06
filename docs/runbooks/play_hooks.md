# Play's hooks, the stale flag and ask-on-quit export (WO-1.4)

What WO-1.4 adds (D120–D122) and the checks Clayton runs on the PC. Everything that touches Play
is a script Clayton runs himself, from GOS_APP; Claude Code never runs it on Play.

Paths below assume `GOS_APP = C:\GregtecOS-app`, Prism at `C:\Games\PrismLauncher` and GOS_HOME at
`%USERPROFILE%\GregtecOS`. Short names used below:

```powershell
$gos = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
$py  = "C:\GregtecOS-app\current\.venv\Scripts\python.exe"
$hooks = "C:\GregtecOS-app\current\scripts\install_prism_hooks.py"
```

## How it works

- **Fingerprint** (`gos fingerprint`, D120): SHA-256 over a sorted `relpath<TAB>sha256` list of
  Play's enabled mod jars and zips (any depth under `mods\`), `scripts\**\*.zs`,
  `config\GTNewHorizons\dreamcraft.cfg`, GT's recipe configs (`[fingerprint] configs` in gos.toml
  adds more) and `mmc-pack.json`. Export's sync excludes (its NEI and GregtecLink) are left out.
  Only Play is read. Hashes are cached by size and mtime, so a launch hashes only what changed.
- **Pre-launch** (Play): refuses the launch while an export holds the instance lock or GTNH-Export
  is running (Prism then says "Pre-Launch command failed with code 3" and the game doesn't start,
  V25). Then it prints whether recipe data is stale. Any other problem only prints a warning, and
  Play starts.
- **Post-exit** (Play, after a clean quit): if Play is unchanged since the last export, nothing.
  Otherwise a dialog asks "Mods changed. Export now?" (no answer in 5 minutes = No). On Yes it
  starts `gos export run` and returns: sync Play to Export, `prismlauncher --launch GTNH-Export`
  (which exports and quits by itself, D100), wait for the new run and for Export to quit, record
  Play's fingerprint for that run, write the diff report, release the lock, show a message box.
  Prism doesn't run post-exit after a crash: then nothing is asked, and you can run
  `& $gos export run` yourself with Play closed.
- **Files** (all in GOS_HOME): `data\locks\instance.lock`, `data\fingerprint\` (state, listings),
  `data\cache\fingerprint.json`, `data\reports\export-diff-<old>-<new>.txt`,
  `logs\export-<time>.log`.

## 1. Deploy

1. Close Prism. From your clone: `git pull`, then `uv run gos deploy` (after the PR is merged).
2. `Test-Path C:\GregtecOS-app\scripts\prism_prelaunch.ps1, C:\GregtecOS-app\scripts\prism_postexit.ps1`
   prints `True` twice.

## 2. Fingerprint (read-only)

```powershell
& $gos fingerprint
& $gos fingerprint --no-cache
& $gos fingerprint --list | Select-String config/
```

- Both runs print the same `Play (...): <64 hex>` line (deterministic). The first one takes a few
  seconds (it hashes every mod); the second hashes everything again on purpose.
- The file count is about Play's mod count plus a few configs and scripts. Note any `note:` lines.
- `--list | Select-String config/` shows which configs are in it: `dreamcraft.cfg` and the five
  GT files. If you know other configs that change recipes (TST, GTNL, 123Tech, PH), send their
  paths (see Questions in the PR).
- The last line says `recipe data is stale: no export is recorded yet` until the first export.

## 3. V25 on Lab first

1. Close Prism. Install the hooks on Lab, dry run first:

   ```powershell
   & $py $hooks --instance lab --dry-run
   & $py $hooks --instance lab
   ```

   It prints the two commands and `written (the old file is ...instance.cfg.gos-backup-...)`.
2. Open Prism: GTNH-Lab → Edit → Settings → Custom commands shows them, with "Custom commands"
   ticked.
3. In a PowerShell window: `& $gos lock hold --minutes 5`. Launch GTNH-Lab.
   **Expected:** the game does not start. Lab's console shows
   `GOS: not launching: the instance lock is held by test ...` and
   `Pre-Launch command failed with code 3.` This confirms V25 on the PC.
4. Press Ctrl+C in the lock window (`Released.`). Launch Lab again: it starts, and the console shows
   `GOS: not Play: no stale check.` Quit Lab from the main menu: the console shows
   `GOS: not Play: nothing to do after exit.`
5. Close Prism and remove Lab's hooks again: `& $py $hooks --instance lab --uninstall`.

## 4. Install on Play (you run this yourself)

```powershell
& $py $hooks --dry-run
& $py $hooks
```

Prism must be closed (the script refuses otherwise, since Prism would overwrite the file).
`instance.cfg.gos-backup-<time>` next to Play's `instance.cfg` is the old file.

## 5. Acceptance on Play (plan §8, WO-1.4)

1. **First export.** Launch Play. Its console shows
   `GOS: recipe data is stale: no export is recorded yet`. Quit to the desktop from the main menu.
   The dialog "Gregtec OS: mods changed. Export now?" appears: click **Yes**. Export starts by
   itself, exports (a few minutes, at most 30) and quits. A message box `Export <run> done.`
   follows; this first time it says nothing to diff against. If Export hasn't started a minute
   after Yes, send the newest `%USERPROFILE%\GregtecOS\logs\export-*.log` (the export runs as its
   own process after the hook returns; this is the first check that Windows lets it outlive
   the hook).
   - **While Export runs**, click Launch on GTNH-Play: it must not start, and its console shows
     `GOS: not launching: the instance lock is held by export ...` (Play won't launch while Export
     holds the lock).
2. **Not stale after the export.** Launch Play: the console shows
   `GOS: recipe data matches Play (export <run>, fingerprint ...)`.
3. **An unchanged quit runs nothing.** Quit Play: no dialog; the console shows
   `GOS: Play unchanged since export <run>: nothing to do.`
4. **A changed mod jar.** Close Play. In Prism, GTNH-Play → Edit → Mods, untick PlanNH (the optional
   client-only mod), close the window. Launch Play: the console shows
   `GOS: recipe data is stale: Play changed since export <run> (0 added, 1 removed, 0 changed; ...)`.
   Quit: the dialog asks; **Yes**. After Export quits, the message box shows the diff summary and
   the report path. Then:

   ```powershell
   & $gos export diff
   ```

   prints the same report: `Play: 0 added, 1 removed, 0 changed` with a `- mods/<PlanNH's jar>`
   line, and PlanNH under Export's mods as removed.
5. Re-tick PlanNH. Launch Play (stale again: `1 added`), quit, **Yes** once more, so the last
   export matches Play again. Or answer No: recipe data then stays stale until the next export.
6. Send `%USERPROFILE%\GregtecOS\logs\export-*.log` and the reports in
   `%USERPROFILE%\GregtecOS\data\reports\`.

## Notes

- WO-1.1's check (2) (`Select-String gregtec GTNH-Play\instance.cfg` prints nothing) now prints
  the two hook lines, which name `GregtecOS-app\scripts\prism_*.ps1`. That is expected: the check
  is that `-Dgregtec.autoexport` is not in Play.
- **Before deploying a commit older than WO-1.4**, remove Play's hooks (`& $py $hooks --uninstall`,
  Prism closed): such a release has no `prism_prelaunch.ps1`, and a pre-launch command that can't
  run stops Play from launching (V25).
- A missing or broken GOS release (no `gos.exe`) never stops Play: the wrapper prints a warning
  and the game starts. Only the lock or a running Export does.
- `[policy] export_on_quit = "never"` in gos.toml turns the question off (the stale line stays).
- To stop an export: close GTNH-Export (or kill it in Prism). The export then fails, the lock is
  released and recipe data stays stale.
