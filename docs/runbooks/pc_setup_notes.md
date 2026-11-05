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
