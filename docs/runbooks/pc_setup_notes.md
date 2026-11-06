# PC setup notes

Things learned while running the Phase 0 setup on Clayton's PC (2026-11-05). Read this before setting up the PC again or adding another account.

## Setup gotchas

### 1. winget tools that gos-dev can't run

winget installs portable packages such as uv and pnpm machine-wide, but their folders keep the installing admin's ACL, so other accounts (gos-dev) can't run them. Fix it in an admin PowerShell:

```powershell
$pkgs = 'C:\Program Files\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe','C:\Program Files\WinGet\Packages\pnpm.pnpm_Microsoft.Winget.Source_8wekyb3d8bbwe'
foreach ($p in $pkgs) { icacls $p /reset /T /C /Q }
```

"Access is denied" lines for winget's own `.db` files are harmless. Run it again after any winget upgrade of uv or pnpm.

### 2. runas windows start in system32

A `runas` window starts in `C:\Windows\system32`. `cd` into the repo before `uv python install`, so uv reads the repo's `.python-version` (3.12). Run outside the repo, it installs the newest Python instead. If that already happened (it installed 3.15 once):

```powershell
cd C:\GregtecOS-dev\GretecOS
uv python uninstall 3.15
uv python install
```

### 3. claude.exe is not on gos-dev's PATH

Claude Code's native installer puts `claude.exe` in `%USERPROFILE%\.local\bin`, which is not on gos-dev's PATH. As gos-dev, add it to the user PATH. This keeps the existing `%VAR%` entries unexpanded and does nothing if the entry is already there:

```powershell
$bin = Join-Path $env:USERPROFILE '.local\bin'
$k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
$p = $k.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
if (($p -split ';') -notcontains $bin) { $k.SetValue('Path', ($p.TrimEnd(';') + ';' + $bin).TrimStart(';'), 'ExpandString') }
$k.Close()
```

(Or add the entry by hand: as gos-dev, run `rundll32 sysdm.cpl,EditEnvironmentVariables` and edit `Path` under "User variables".) Then open a new runas window; the old one keeps the old PATH.

### 4. Claude Code login on Windows

The browser doesn't open by itself ([anthropics/claude-code#60126](https://github.com/anthropics/claude-code/issues/60126)): open the printed URL yourself. The "Paste code here" box may ignore a paste ([#71449](https://github.com/anthropics/claude-code/issues/71449)): type the code by hand.

### 5. PowerShell shortcuts that open in system32

Start-menu PowerShell shortcuts may open in `C:\Windows\system32`. That alone doesn't mean the window is elevated: an admin window says "Administrator" in its title bar.

## Intermittent startup crash (SDL3)

Seen once: the game exited with `-1073740771` during startup after an access violation in `SDL3.dll` 3.4.14 (`0xC0000005`, then `0xC000041D`), when BetterLoadingScreen creates its shared-context window. A relaunch worked. The likely cause is a race inside SDL: `WIN_OpenClipboard` reads a half-created window during `WIN_CreateWindow`'s message pump.

Nothing to do while it stays rare. If it recurs, turn off BetterLoadingScreen's threaded rendering, on Lab first:

1. Add `config/Betterloadingscreen/betterloadingscreen.cfg` to `[sync.lab] exclude` in `gos.toml`. Otherwise the next `sync_instances.py` run copies Play's file back over Lab's.
2. In Lab's `config\Betterloadingscreen\betterloadingscreen.cfg`, in the `general` block, set `B:threadedRendering=false`.
3. Launch Lab several times. If it starts cleanly and the loading screen still works, Clayton makes the same change in Play himself, then removes the exclude.

## OpenComputers log line

OpenComputers prints `Successfully applied N Internet Card filtering rules.` (or `Could not parse Internet Card filtering rules!`) to `logs\fml-client-latest.log` only when a world loads, not at the main menu. To check it, load a world, then run `check_tweaks.py`: when the line is in the log, the `oc_rules` row ends with `last launch: <the line>`. The log is rewritten at every launch, so after a launch that stopped at the main menu the line is missing and `check_tweaks.py` shows no `last launch` part. On 2026-11-05, Lab showed `Successfully applied 8 Internet Card filtering rules.` after loading a world.

## PowerShell: running a tool by its full path

- A quoted path needs `&` in front, or PowerShell just prints the string: `& "C:\GregtecOS-app\current\.venv\Scripts\gos.exe" doctor`. An unquoted path without spaces runs without it.
- Piping `gos doctor` through `Select-String` in Windows PowerShell 5.1 can garble non-ASCII text (for example `17–26` shows as `17û26`). It's only how the pipe re-encodes the text; the plain output is correct.

## gos deploy builds GregtecLink; sync installs it into Export (D98)

From WO-1.1 PR 1 on, `gos deploy` also builds the mod in `mod/` and keeps only its jar in `GOS_APP\current\mod-dist\`. This replaces copying the jar and config into Export by hand.

1. Gradle's wrapper needs a Java on `PATH` or `JAVA_HOME` to start (Java 17 or newer); Gradle then downloads the JDKs the build uses into `%USERPROFILE%\.gradle\`. If deploy stops with `ERROR: JAVA_HOME is not set and no 'java' command could be found in your PATH`, nothing was swapped: point `JAVA_HOME` at a JDK, open a new window and deploy again.
2. The first deploy takes a few minutes: Gradle also downloads Minecraft, Forge and GT. Later deploys reuse those downloads. It ends with `Built GregtecLink: ...gregteclink-<commit>.jar` and the jar's SHA256.
   Every deploy runs this build, even one whose commit doesn't change `mod/`. So every deploy needs that Java, and it needs the network whenever Gradle has something new to download (the first deploy, and after the mod's dependencies change). Without them the build fails, and deploy stops before the swap with GOS_APP unchanged.
3. Close Export, then run sync from GOS_APP, dry run first:

   ```powershell
   & "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\sync_instances.py" --dry-run
   & "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\sync_instances.py"
   ```

   Under `export:` the `gregteclink:` lines show the jar it installs, any older `gregteclink*.jar` it deletes, and `config/gregteclink.cfg` (export module on). Lab and Play get nothing from this step. Re-run sync after each deploy that changes the mod.
   If sync stops with `Write failed` because Export was still open, close it and run sync again. Sync deletes the old jar before it writes the new one, so until then Export has one GregtecLink jar or none, never two.

## WO-1.1 PR 1b: NEI swap, autoexport and the two closing checks (D100)

WO-1.1 is done when these two checks pass on the PC: (1) one click in GTNH-Export creates every file, `nei\` included; (2) Play is untouched. Everything below happens in Export only; Play is only read.

1. Close Play, Lab and Export. From your clone: `git pull`, then `uv run gos deploy`.
2. Run `& "C:\GregtecOS-app\current\.venv\Scripts\gos.exe" doctor`, then copy `<GOS_HOME>\data\env.json` to `env-before.json` next to it.
3. Run sync for Export only:

   ```powershell
   & "C:\GregtecOS-app\current\.venv\Scripts\python.exe" "C:\GregtecOS-app\current\scripts\sync_instances.py" --only export
   ```

   It should report the new `gregteclink-<commit>.jar` installed into Export, and nothing for Lab or Play.
4. **Swap NEI in Export only** (once; sync's exclude `mods/NotEnoughItems-*.jar` keeps it):
   - Download `https://github.com/GTNewHorizons/NotEnoughItems/releases/download/2.8.172-GTNH/NotEnoughItems-2.8.172-GTNH.jar`.
   - `Get-FileHash .\NotEnoughItems-2.8.172-GTNH.jar` must print `9362A063C04AAF11A668291542223725DB1147E76F483936AD965530E084104F`. If not, stop.
   - Delete `GTNH-Export\.minecraft\mods\NotEnoughItems-2.8.171-GTNH.jar` and copy the 2.8.172 jar into that folder.
   - Optional, read-only: Play's `NotEnoughItems-2.8.171-GTNH.jar` hashes to `9A822134…88B5A3`.
5. Prism → GTNH-Export → Edit → Settings → Java: tick the JVM arguments box if needed and add `-Dgregtec.autoexport=true` after the existing arguments. Optionally, Create Shortcut → Desktop. Don't touch GTNH-Play.
6. **Check (1):** click Launch on GTNH-Export and do nothing. The main menu opens, the "GregtecLink autoexport" world loads, chat shows "GregtecLink: exporting NEI's recipes; the game freezes for a while.", the game freezes during NEI's dump, then it closes by itself (a few minutes; at most 30).
7. In the newest `GTNH-Export\.minecraft\gregteclink\exports\<run_id>\` (not one ending in `.partial`), `manifest.json` should show:
   - `"mode": "autoexport"`, `"world": "gregteclink-autoexport"`, `"complete": true`;
   - `parts`: `gt_dump`, `machines` and `refs` all `ok`, `"nei": "ok"`, `"autoexport": "ok"`, and no `part_errors`;
   - `"nei_version": "2.8.172-GTNH"`;
   - `counts`: `nei_files` above 0 and `nei_recipes` equal to `nei_reported`;
   - a `nei\` folder of `.jsonl` files.

   Note the run folder's size (Properties), and any dialog that appeared during the run.
8. **V22 command line:** run `& "<your Prism folder>\prismlauncher.exe" --launch GTNH-Export`. It should produce a second run the same way. If Prism is already open, the command hands over to it and returns at once.
9. **Fallback, only if step 6 fails:** remove the flag from step 5, Launch, open "GregtecLink autoexport" from Singleplayer, wait for the chat line "GregtecLink: export … finished", then quit. The manifest should show `"mode": "one_click"` and `"nei": "ok"`. Send the newest `GTNH-Export\.minecraft\logs\fml-client-latest.log` either way.
10. **Check (2):** run `gos doctor` again, then compare Play's mods (file, enabled, sha256) between the two files; it should print nothing:

    ```powershell
    $before = (Get-Content env-before.json | ConvertFrom-Json).prism_roots.instances | Where-Object folder_name -eq 'GTNH-Play' | ForEach-Object mods
    $after = (Get-Content env.json | ConvertFrom-Json).prism_roots.instances | Where-Object folder_name -eq 'GTNH-Play' | ForEach-Object mods
    Compare-Object $before $after -Property file, enabled, sha256
    ```

    Also, read-only: `Get-ChildItem GTNH-Play\.minecraft\config\gregteclink*` shows no file with `export=true`, and `Select-String gregtec GTNH-Play\instance.cfg` prints nothing.

With the flag left in Export's settings, every launch of Export is an unattended export that quits. To use Export by hand, remove the flag first.
