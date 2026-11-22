# Gregtec OS: set up your PC, from the November install to the building bake-off

One document. Every command is written out here. You never have to open a second file to finish a step.

**Where it starts.** Your PC as the early-November setup left it: git, uv and a JDK installed, your own clone of the repo, an old release in `C:\GregtecOS-app\current`, the `gos-dev` account, and Prism with GTNH-Play, GTNH-Lab and GTNH-Export. Part 0 checks each of these with one command, so nothing is taken on trust.

**Where it ends.** The current release is installed, the core service runs, the dashboard on monitor 2 is paired, the setup wizard is finished, the recipe database is built from one Export run, one plan is saved, you have toured the building tool, and you have run the shell bake-off: first with the free fake models, then (if you want) with two real ones.

**How long.** Nobody has timed any of this on your PC. These are rough guesses, so you can plan the sitting. You can stop after any part and go on another day: every part starts with the block to paste.

| Part | What | Rough time |
|---|---|---|
| 0 | Check what you have | 10 minutes |
| 1 | Install what is missing | 0 to 15 minutes |
| 2 | Redeploy | 20 to 45 minutes, mostly waiting for downloads and builds |
| 3 | Start the core | 5 minutes |
| 4 | Pair the dashboard | 5 minutes |
| 5 | Setup wizard | 10 to 15 minutes |
| 6 | Recipe data | 30 to 60 minutes; the Export game runs by itself for "a few minutes, at most 30" |
| 7 | One saved plan | 5 to 10 minutes |
| 7b | Structure data, only if step 59 says you need it | up to a few hours, unattended |
| 8 | The building tour | 15 minutes |
| 9 | The free bake-off | 15 minutes |
| 10 | The paid bake-off | 30 to 45 minutes |
| 11 | What you have now and what comes next | reading only |

**What it costs.** Parts 0 to 9 are free, with one exception: the **Test** button for your Anthropic key in the wizard (step 37) makes one tiny call, far below one cent. Part 10 spends real money at two vendors: Anthropic (the key you already have) and OpenAI (a new key, with credit you add on their site first). One bake-off run can never spend more than its cap, which is \$10 unless you change it, and your day limit (also \$10 unless you changed it) holds too. With the prices read on 2026-11-20 the project's own estimate was "likely well under \$1" for the one-request trial and "about \$1 for Claude Opus 5.5 and about \$2.50 to \$3 for GPT-6 Astra" for the full run. Prices move. The command prints its own estimate and waits for you to type `yes`. Trust that estimate, not this page.

**Marks.** `$` in a step's title = the step spends API money. `LAB` = the Lab game is open. `PLAY` = the step touches Play. In this guide only `$` occurs, on steps 37, 79 and 80. **No step is marked LAB or PLAY:** nothing here starts Lab or Play, and nothing writes to Play. A few steps read Play's files without changing them (the sync, `gos doctor`, two checks in the wizard). Each of those steps says so.

**Four rules for the whole guide.**

- Use a normal PowerShell window. Never "Run as administrator": GOS refuses to run that way.
- One game at a time. Before the Export run (step 52) close Play and Lab yourself. Nothing checks this for you yet.
- A key or token goes in only through the setup wizard or `gos key set`. Never into a file, a chat or a pull request.
- If what you see is not what "You should see" says, and no "If not" line covers it: stop there. Copy the text from the window and ask. Do not guess.

**Words used here.**

- **GOS_APP** is `C:\GregtecOS-app`: the installed program. `current` inside it is the release in use.
- **GOS_HOME** is `%USERPROFILE%\GregtecOS`: your settings, data and vault.
- **Your clone** is your own copy of the repo, the folder where you run `uv run gos deploy`.
- **Export, Lab, Play** are the three Prism instances GTNH-Export, GTNH-Lab and GTNH-Play.
- **The core** is the GOS program that runs in the background and serves the dashboard. **The supervisor** starts it and keeps it running.
- Nothing puts `gos.exe` on your PATH. In your window every command starts with `& $gos`. When a message from the program names a command that starts with `gos` or `uv run gos`, you type `& $gos` and then the same words.

---

## Part 0. Check what you have

Every command in this part only reads. Nothing is changed.

### Step 1. Open a PowerShell window and paste the block

Open PowerShell from the Start menu the normal way. Paste this. **Paste it again first in every new window:** PowerShell forgets these names when a window closes.

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

You should see: nothing printed. The window's title bar must not say "Administrator".

If not: the title says "Administrator": close the window and open PowerShell again without "Run as administrator".

### Step 2. Check git

```powershell
git --version
```

You should see: `git version 2.` followed by more numbers.

If not: "git is not recognized" would be a surprise, because the November setup used git. Install Git for Windows with its normal installer (`https://git-scm.com/download/win`), open a new window, and go back to step 1.

### Step 3. Check uv

uv is the tool that installs Python and runs the project's commands.

```powershell
uv --version
```

You should see: `uv 0.13.6` or a newer version. (0.13.6 is what the project record says your PC had.)

If not: "uv is not recognized" would also be a surprise. Stop and ask: the record does not say how uv was installed on your PC, and I will not guess.

### Step 4. Check Java

The redeploy builds the GregtecLink mod, and that build needs a Java Development Kit (JDK), version 17 or newer. The record says yours is known to Windows only through a setting named `JAVA_HOME`, so check that first.

```powershell
$env:JAVA_HOME
& "$env:JAVA_HOME\bin\java.exe" -version
```

You should see: a folder path, then a few lines with a version number of 17 or higher. (The record says Temurin 25.0.5.)

If not: the first line is empty, or the second fails. Try `java -version`. If that prints 17 or higher, you are fine: the build finds Java either way. If both fail, do step 18 in Part 1.

### Step 5. Check Node.js

New since November: the deploy now also builds the dashboard, and that needs Node.js (a program that runs JavaScript tools), version 22 or newer.

```powershell
node --version
```

You should see: `v22` or higher. (The record says Node 26.)

If not: do step 15 in Part 1.

### Step 6. Check pnpm

pnpm downloads the dashboard's building blocks. Also new since November.

```powershell
pnpm --version
```

You should see: a version number. `10.28.0` is the one the project pins. `12` and a few more digits is probably fine too: on a test machine pnpm 12 switched itself to 10.28.0 inside the project. (Not tried with your pnpm. The real proof is the line `Built the dashboard:` in step 21.)

If not: "pnpm is not recognized": do step 16 in Part 1.

### Step 7. Check Google Chrome

New since November: the dashboard opens in a Chrome window of its own. GOS looks for Chrome in three places.

```powershell
Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
```

You should see: three lines, at least one of them `True`.

If not: three times `False`: do step 17 in Part 1.

### Step 8. Look at the installed release

```powershell
Get-Content "$app\release.json"
```

You should see: a few lines of settings in curly brackets. `"commit"` starts with `362a22e` (the November release, by the project record). `"deployed_at"` is a date in early November. `"source"` is the folder of your own clone. There is a `"gregteclink"` entry and no `"dashboard"` entry yet.

If not: another commit is fine: it only means the record is behind. "Cannot find path" means `current` has no release. Then run `Test-Path C:\GregtecOS-app`. `True`: go on, the deploy still works, but find your clone's folder yourself for step 9. `False`: the November setup of GOS_APP is gone. Stop and ask.

### Step 9. Find your clone

```powershell
$clone = (Get-Content -Raw "$app\release.json" | ConvertFrom-Json).source
$clone
Test-Path "$clone\.git"
```

You should see: the folder of your clone, then `True`.

If not: `False`, or an empty line: the clone was moved. Find the folder yourself (it holds `pyproject.toml` and the folders `core` and `mod`). In step 20 put that path in place of the `$clone = ...` line, in double quotes.

### Step 10. Check the three Prism instances

```powershell
Get-ChildItem "C:\Games\PrismLauncher\instances" -Directory | Select-Object Name
```

You should see: `GTNH-Play`, `GTNH-Lab` and `GTNH-Export` in the list. Other names may be there too.

If not: Export or Lab is missing: make it in Prism with "Copy instance" from GTNH-Play, as a plain copy (do not tick "Link files"). If the whole folder is missing, Prism is somewhere else: stop and ask.

### Step 11. Check GOS_HOME and whether an old settings file exists

```powershell
$env:GOS_HOME
Test-Path "$gh"
Test-Path "$gh\config\gos.toml"
```

You should see: an empty line, then `True`, then `True` or `False`. **Write down the last answer.** Both are fine, but it decides what the dashboard shows in Part 5, and whether you do step 24.

If not: the first line prints a path: every GOS command then uses that folder as GOS_HOME. In every block of this guide, change the `$gh =` line to that path in double quotes. The second line says `False`: fine, GOS makes the folder when it first starts.

### Step 12. Check that nothing runs from GOS_APP and the two ports are free

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like 'C:\GregtecOS-app\*' } | Select-Object ProcessId, Name, ExecutablePath
Get-NetTCPConnection -LocalPort 8765,8766 -ErrorAction SilentlyContinue
```

You should see: nothing printed. (GOS has never been started on this PC.)

If not: the first command lists a program: close the window it runs in before Part 2. The second prints lines: another program uses port 8765 or 8766, which GOS needs. Note its `OwningProcess` number, stop and ask.

### Step 13. Check the gos-dev clone

Only Part 7b uses this, and you may never need Part 7b.

```powershell
Test-Path "C:\GregtecOS-dev\GretecOS\.git"
```

You should see: `True`.

If not: `False`, or "Access is denied": note it and go on. It only matters if step 59 sends you to Part 7b.

### Step 14. Check the Lab worlds

Nothing in this guide opens the Lab. This only tells you the November Lab is still as it was.

```powershell
Get-ChildItem "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft\saves" -Directory | Select-Object Name
```

You should see: `lab-ops`, `lab-structures` and `lab-progress` in the list.

If not: note what is missing and go on. No step here needs a Lab world.

---

## Part 1. Install what is missing

Do only the steps that Part 0 sent you to. If Part 0 sent you to none, go to Part 2. After any install, open a **new** PowerShell window, because the old one does not see new programs.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 15. Install Node.js (only if step 5 failed)

```powershell
winget install OpenJS.NodeJS.LTS
```

Windows may ask you to allow the change. Then open a new window, paste the block, and run:

```powershell
node --version
```

You should see: `v22` or higher.

If not: still "not recognized" in a new window: stop and ask.

### Step 16. Install pnpm (only if step 6 failed)

In a window opened after Node.js was installed:

```powershell
npm install -g pnpm@10.28.0
```

Then open a new window, paste the block, and run:

```powershell
pnpm --version
```

You should see: `10.28.0`.

If not: "npm is not recognized": Node.js is missing or the window is older than its install. Go back to step 15.

### Step 17. Install Google Chrome (only if step 7 printed False three times)

There is no command for this in the project. Download Chrome's installer in your browser from `https://www.google.com/chrome/` and run it. Then run the check again:

```powershell
Test-Path "$env:ProgramFiles\Google\Chrome\Application\chrome.exe", "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe", "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
```

You should see: at least one `True`.

If not: Chrome went to an unusual place. GOS only looks in these three. Stop and ask.

### Step 18. Make Java findable (only if step 4 failed both ways)

The November deploy built the mod, so a JDK was on this PC then. There is no install command for Java in the project, and I cannot see where your JDK folder is. So this step has no checked command. Install any JDK of version 17 or newer with its Windows installer. If the installer offers to set `JAVA_HOME` or to add Java to PATH, say yes. Then open a new window, paste the block, and run:

```powershell
java -version
```

You should see: a version of 17 or higher.

If not: stop and ask before Part 2. Without Java the deploy stops at the mod build and changes nothing.

---

## Part 2. Redeploy

This replaces the November release in GOS_APP with today's. It builds three things: the program's own Python environment, the GregtecLink mod, and the dashboard. It needs the internet the whole time. If it fails at any point, GOS_APP stays exactly as it was.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 19. Close Prism, every game, and everything inside GOS_APP

Close Prism Launcher and any open game (Play, Lab, Export). Close every File Explorer window, editor and PowerShell window that is inside `C:\GregtecOS-app`. Then check:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like 'C:\GregtecOS-app\*' } | Select-Object ProcessId, Name, ExecutablePath
```

You should see: nothing printed.

If not: it lists a program: close the window it runs in, and run the check again.

### Step 20. Update your clone

```powershell
$clone = (Get-Content -Raw "$app\release.json" | ConvertFrom-Json).source
cd $clone
git switch main
git status --short
git pull
git log -1 --format="%h %s"
```

You should see: `Already on 'main'` or `Switched to branch 'main'`. Then nothing from `git status --short`, or only lines that start with `??` (files of your own that git does not track; the deploy ignores them). Then the pull's list of changed files. Then one line with a short commit id and a title. The id must **not** be `362a22e`: that would mean nothing was pulled.

Also look at `https://github.com/crazie450-prog/GretecOS/commits/main` in your browser. The top commit should have a green tick. A red cross means the checks failed on it: do not deploy over that, stop and ask.

If not: `git status --short` prints a line that starts with `M` or `D`: a tracked file was changed, and the deploy will refuse. Run `git restore` followed by the file name it printed, then run this step again. `git pull` fails: check the internet connection and try again.

### Step 21. Run the deploy

Stay in the clone's folder (the window's prompt shows it).

```powershell
uv run gos deploy
```

This is the long step. First uv updates the clone's own environment. Then you see two lines that start with `Building`, each followed by a lot of text from the build tools. Wait as long as text keeps appearing.

You should see: at the end, lines of this shape. `<hex>` stands for letters and digits that will differ.

```text
Building GregtecLink <hex> with mod/gradlew.bat build (the first deploy also downloads Gradle, JDKs and Minecraft: a few minutes)...
Building the dashboard with pnpm (the first deploy downloads its packages: a minute or two)...
Deployed <hex> (HEAD) to C:\GregtecOS-app\current, replacing 362a22e5cbb7.
C:\GregtecOS-app\scripts is a copy of its scripts/.
Built GregtecLink: C:\GregtecOS-app\current\mod-dist\gregteclink-<hex>.jar
  SHA256 <hex> (in release.json)
  sync_instances.py installs it into Export.
Built the dashboard: C:\GregtecOS-app\current\dashboard\dist
Run the tools that touch Play from the release, as yourself:
  C:\GregtecOS-app\current\.venv\Scripts\gos.exe doctor
  C:\GregtecOS-app\current\.venv\Scripts\python.exe C:\GregtecOS-app\current\scripts\check_tweaks.py <instance folder> [--apply]
  C:\GregtecOS-app\current\.venv\Scripts\python.exe C:\GregtecOS-app\current\scripts\sync_instances.py --dry-run
```

The three lines that matter are `Deployed ...`, `Built GregtecLink: ...` and `Built the dashboard: ...`. There is no line with the word "swap", and no line `Stopping GOS` or `Restarted GOS`: GOS is not running yet, and the deploy does not start it. You start it in Part 3. An extra last line `Couldn't delete ... (in use?); the next deploy removes it.` is harmless.

If not: a refusal is one line that starts with `Not deployed:`. Unless that line says otherwise, GOS_APP is unchanged, and you can simply run the step again after the fix.

| The line says | What it means | What to do |
|---|---|---|
| `has uncommitted changes` | a tracked file in the clone was edited | go back to step 20 |
| `isn't in origin/main` | the clone is on a branch, or was not pulled | go back to step 20 |
| `can't check origin/main` ... `Run git fetch origin` | the clone has no copy of main yet | run `git fetch origin`, then this step again |
| `pnpm isn't on PATH` | pnpm is missing, or this window is older than its install | step 16, then a new window |
| `the dashboard build failed` | pnpm or the network | read pnpm's text above the line; run the step again |
| `JAVA_HOME is not set and no 'java' command could be found`, then `the GregtecLink build failed` | Java is not findable | step 18, then a new window |
| `uv sync failed` | the network, or the disk is full | run the step again |
| `couldn't delete the build's ...` | a build tool or a virus scanner still holds a file | wait a minute, run the step again |
| `couldn't swap the release in ([WinError 5] Access is denied ...)` | something is using the folder `C:\GregtecOS-app\current` | the paragraph below |
| `isn't a folder`, `isn't the folder setup_gos_dev.ps1 makes`, or `has no readable release.json` | GOS_APP is not as the November setup left it | stop and ask |
| `Warning: gos.toml: ...` before the build (the deploy still runs) | a setting in an old `gos.toml` is wrong | finish this step, then do step 24 |
| `Can't read gos.toml: ...` | the old `gos.toml` cannot be read at all | stop and ask, with the line |

**The WinError 5 line.** This happened once on your PC in November, and running the deploy again worked at once. Since then the deploy tries each rename ten times, one second apart, and if it still fails it undoes everything, so nothing is half done. If you see the line: close every PowerShell window whose prompt is inside `C:\GregtecOS-app`, every File Explorer window open there, and any editor with a file from there. Wait one minute (a scheduled task named "GregtecOS world backup" runs a script from `C:\GregtecOS-app\scripts` every 30 minutes, and the deploy renames that folder too). Then run the step again. What exactly holds the folder on your PC is not known: this list is general Windows behaviour plus the program's own message.

More detail: [pc_setup_notes.md](runbooks/pc_setup_notes.md) ("gos deploy builds GregtecLink").

### Step 22. Check the new release

```powershell
Get-Content "$app\release.json"
Test-Path "$app\dashboard\dist\index.html"
```

You should see: `"commit"` is the long form of the id from step 20. `"deployed_at"` is today. There is a `"gregteclink"` entry and now also a `"dashboard"` entry. The last line is `True`.

If not: the commit still starts with `362a22e`: the deploy did not finish. Go back to step 21 and read its last lines.

### Step 23. Check the database engine inside the new release

SQLite is the small database engine GOS uses. A plan that holds a machine made of many blocks needs SQLite 3.43 or newer inside the release's Python (Part 7). No GOS command checks this, so check it once now.

```powershell
& $py -c "import sqlite3, sys; print(sqlite3.sqlite_version, sys.version.split()[0], sys.base_prefix)"
```

You should see: three things on one line: the SQLite version, the Python version (`3.12.` and a number), and the folder that Python lives in. The first must be `3.43.0` or higher.

If not: the first number is lower than 3.43: you can still do Parts 3 to 6, which do not need it. Stop before Part 7 and send me the whole line. There is a likely fix (make uv use its own Python 3.12, then deploy again), but nobody has tried it on Windows, so ask first.

### Step 24. Look for one leftover line in an old settings file (only if step 11 ended with True)

If step 11 ended with `False` there is no settings file yet: skip this step. Otherwise:

```powershell
Select-String -Path "$gh\config\gos.toml" -Pattern 'tailnet'
```

You should see: nothing printed.

If not: it prints a line with `"<pc>.<tailnet>.ts.net"`. That is a placeholder from the plan's example file, and the core refuses to start with it. Open the file in Notepad (`notepad "$gh\config\gos.toml"`), delete only that one entry from the `allowed_hosts` list (keep the other entries and the brackets), and save. Then run this step again.

### Step 25. Let GOS look at the three instances

`gos doctor` reads all three instances, Play included. It changes nothing in any of them. It writes one file, `env.json`, into GOS_HOME. It takes a while, because it reads every mod file.

```powershell
& $gos doctor
```

You should see: text of this shape (`<...>` differs on your PC):

```text
Prism (portable): C:\Games\PrismLauncher
  GTNH-Export   GTNH   pack=2.9.0   java=java17+   game=.minecraft   mods=<n> enabled, <m> disabled
    runs on: <java path> (<version>; global setting)
  GTNH-Lab      GTNH   pack=2.9.0   java=java17+   game=.minecraft   mods=<n> enabled, <m> disabled
    runs on: <java path> (<version>; global setting)
  GTNH-Play     GTNH   pack=2.9.0   java=java17+   game=.minecraft   mods=<n> enabled, <m> disabled
    runs on: <java path> (<version>; global setting)

GT pin 5.09.54.265: no DB yet, so no recipes (C:\Users\<you>\GregtecOS\data\gos.sqlite): run gos export collect, then gos db build

Wrote C:\Users\<you>\GregtecOS\data\env.json

VERIFY (plan section 13.2)
  V01  ...
```

The line "no DB yet, so no recipes" is right at this point. You build the database in Part 6.

If not: a line that starts with `problem:` under an instance: copy it, stop and ask. An instance is missing from the list: go back to step 10.

### Step 26. Sync, as a dry run first

The sync copies Play's `mods`, `config` and `scripts` folders into Export and Lab, and puts the GregtecLink mod file that the deploy just built into Export. It only reads Play. The dry run writes nothing at all: it shows what the real run would do. Prism and all three games must be closed.

```powershell
& $py $sync --dry-run
```

You should see: text of this shape:

```text
Play: C:\Games\PrismLauncher\instances\GTNH-Play\.minecraft
Dry run: nothing is written.
export: C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft
  excluded (never copied or deleted): ...
  nothing to do (<N> excluded)
  gregteclink: gregteclink-<hex>.jar from C:\GregtecOS-app\current (release <hex>), SHA256 matches
  delete  mods/gregteclink-362a22e5cbb7.jar
  install mods/gregteclink-<hex>.jar
lab: C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft
  ...
```

There may be lines that start with `copy`, `update`, `retime` or `delete`, and a count line in place of "nothing to do". There may be a line `write   config/gregteclink.cfg (export module on, listener and client off)`.

**Read every `delete` line under `lab:` before you go on.** The real run deletes from Lab and Export every file in those three folders that Play does not have. A file you once put into Lab by hand would go. Lab's worlds are safe: the `saves` folder is never touched.

If not: it prints `Refused, nothing written in any instance:` and a reason. Nothing was changed anywhere. The reasons you could meet:

| The reason says | What to do |
|---|---|
| `doesn't exist (check [paths] in gos.toml)` or `doesn't exist (create it with Prism's Copy instance of Play)` | go back to step 10 |
| `is a hard link (made by Prism's 'Link files' copy?)` | that instance was copied with "Link files". Stop and ask before you re-create it |
| `is a symlink or junction` | stop and ask, with the line |
| `doesn't match the SHA256 in ...release.json; deploy again` | go back to step 21 |
| anything else | stop and ask, with the line |

### Step 27. Sync for real

Only if the dry run printed no `Refused` and no `delete` line under `lab:` that surprised you:

```powershell
& $py $sync
```

You should see: the same text as the dry run without the line `Dry run: nothing is written.`, and no `Refused`.

If not: `Write failed: ...` and then `export is part synced. Close its game, then run sync again.` (or the same for `lab`): that game was open. Close it and run this step again. If a `delete` line under `lab:` named a file you want to keep, do not run the line above. Sync only Export for now, which is all this guide needs, and ask about the Lab file later:

```powershell
& $py $sync --only export
```

More detail: [pc_setup_notes.md](runbooks/pc_setup_notes.md).

---

## Part 3. Start the core

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 28. Make sure nothing old is running

```powershell
& $gos stop
```

You should see: `GOS isn't running.`

If not: it prints `Stopped (the supervisor keeps running ...)`: something had started GOS already. That is fine. Go on.

### Step 29. Start GOS in the background

```powershell
& $gos serve --detach
```

`--detach` means: start it and give the window back. Without it the window is held, and closing the window stops GOS.

You should see: `GOS started in the background (logs in C:\Users\<you>\GregtecOS\logs).`

A few seconds later a Chrome window without an address bar should open on monitor 2. Leave it for Part 4.

If not:

| It says | What to do |
|---|---|
| `Not starting GOS: gos.toml: ...` | a setting in an old `gos.toml` is wrong. If it names `allowed_hosts`, do step 24. Otherwise stop and ask, with the line |
| `Not starting GOS: GOS doesn't run elevated (as administrator) ...` | this is an Administrator window. Go back to step 1 |
| `The supervisor didn't answer within 15 s; see ...\logs\supervisor.log` | run `Get-Content "$gh\logs\supervisor.log" -Tail 20`, copy what it prints, stop and ask |

### Step 30. Check that it runs

```powershell
& $gos status
```

You should see: a table like this. The words after the first three columns differ.

```text
overall: warn
  Supervisor     ok     running  (pid <n>)
  Core           ok     running  (pid <n>)
  Discord bot    off    can't start: no [discord] owner_id, bot token or core token (logs/discord.log says why)  last exit: code 2
  Game watchers  off    starts when Play launches
  Voice          off    starts when Play launches
  Database       ok     gos.sqlite
  Anthropic key  warn   not stored: gos key set anthropic
  API credits    ok     $0.00 of $100.00 this cycle (0%)
  Maintenance    ...
  Deep lane      ...
  Presence       ...
```

**It passes when the Supervisor line and the Core line both say `ok` and `running`.** `overall: warn` is normal now, because no key is stored yet. The Discord bot line with `off` is expected: Discord is not part of this guide. The exact words of the last three lines on Windows are not known.

If not: `The core isn't answering; the supervisor says:` with `core  starting`: wait five seconds and run it again. `GOS isn't running.`: go back to step 29.

More detail: [core_service.md](runbooks/core_service.md).

---

## Part 4. Pair the dashboard

Pairing tells the core that this one browser window is yours. It is done once. It survives restarts and later deploys.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 31. Find the dashboard window

Look at monitor 2 for the Chrome window that step 29 opened.

You should see: a page with the heading **Pair this device**, a box "Name this device" that already says `Monitor 2`, and a button **Ask to pair**. The window's address is `http://gos.localhost:8765/`. F11 makes it full screen.

If not: no window opened. Open it yourself:

```powershell
& $gos dashboard
```

If that prints `Chrome isn't installed where its installers put it.`, go back to step 17. If the window is on the wrong screen or has the wrong size, move it by hand and press F11. Pairing only works on the name `gos.localhost`, never on `127.0.0.1` or `localhost`.

### Step 32. Ask to pair

In the Chrome window, leave the name `Monitor 2` and click **Ask to pair**.

You should see: the page now asks for a code: "Type the 8-digit code shown on monitor 2 (a paired dashboard), or run gos pair on the PC."

If not: the page shows an error line instead: copy it, stop and ask.

### Step 33. Get the code and type it in

In the PowerShell window:

```powershell
& $gos pair
```

You should see:

```text
Type the code on the device that asked:
  1234 5678  Monitor 2  (expires in 298 s)
```

Your code has other digits. Type the eight digits into the Chrome window (the space does not matter) and click **Pair**. The page then moves on: to the setup wizard, or to the dashboard with a banner "Setup isn't finished". Both are right. Part 5 explains which.

If not: `No device is waiting to pair.`: you have not clicked **Ask to pair** yet, or five minutes went by. Go back to step 32. The page says "wrong code; this pairing request is closed" after five wrong tries: click **Ask to pair** again and run this step again.

### Step 34. Check the pairing

Press F5 in the Chrome window. Then, in PowerShell:

```powershell
& $gos devices
```

You should see: the Chrome window does not ask to pair again. The command lists one device named `Monitor 2`.

If not: it prints `No paired devices.`: go back to step 32.

More detail: [dashboard.md](runbooks/dashboard.md) (part B).

---

## Part 5. Setup wizard

The wizard has eight screens. The list on the left shows them, and you can click any of them to jump. For this guide you need three: **Paths**, **Anthropic API key** and **Checklist**. The others are skipped or saved as they are.

Two buttons matter on the screens with boxes. **Check** shows the result rows and saves nothing. **Save and continue** saves and jumps to the next screen at once. So press **Check** first when you want to read the rows.

Rows are green (fine), yellow (can wait) or red (must be fixed before you can save or finish).

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 35. Open the wizard

What you do depends on the answer you wrote down in step 11.

- It was `False` (no settings file): the whole window already is the wizard. Do nothing.
- It was `True` (an old settings file): the dashboard shows a banner **Setup isn't finished**. Click **Open setup**.

You should see: a page titled **First-run setup** with the eight screens listed on the left, and a line near the top that says where settings go: your `gos.toml`, and "keys and tokens into Windows Credential Manager, never into a file".

If not: neither the wizard nor the banner shows: press `9` in the dashboard (the Settings view), find the panel **Setup wizard** and click **Open the setup wizard**.

### Step 36. Screen 1, Paths

Seven boxes, already filled in. Change nothing. They should say:

| Box | Should say |
|---|---|
| Prism folder | `C:\Games\PrismLauncher` |
| Prism instances folder | `C:\Games\PrismLauncher\instances` |
| Play instance | `GTNH-Play` |
| Export instance | `GTNH-Export` |
| Lab instance | `GTNH-Lab` |
| GOS_APP | `C:\GregtecOS-app` |
| GOS_HOME | `C:\Users\<you>\GregtecOS` |

The slashes may lean the other way (`C:/Games/PrismLauncher`). Both spellings are accepted.

Click **Check**. This reads a few small files of each instance, Play included, and changes none.

You should see: green rows: the two Prism folders "found"; Play, Export and Lab instance each "GTNH 2.9.0, java17+ build, game folder .minecraft"; GOS_APP "found"; GOS_HOME "there and writable"; and a row "Recipe DB and GT pin" that says "no DB yet, so no recipes". That last row is fine.

Then click **Save and continue**.

If not: a red row: the save is refused with "not saved: fix the marked fields or checks first". Read the red row. If it names a folder that Part 0 found, stop and ask, with the row's text. Never change the Play box: the wizard refuses to move Play.

### Step 37. $ Screen 2, Anthropic API key

This is the only step before Part 10 that spends anything: the **Test** button makes one tiny call to the cheapest model. It costs far below one cent.

1. Click in the **API key** box. Paste your key from the Anthropic Console (the `gregtec` workspace). It starts with `sk-ant-`. The box hides what you paste.
2. Click **Store**.
3. Click **Test** (the button appears once the key is stored).
4. Click **Continue**.

You should see: after **Store**, the note `Stored.`, and the line under the box says "Stored in the keyring". The box empties itself: the key is never shown again. After **Test**: `the API answered on claude-haiku-5-5 (the fast lane's model)`.

If not: storing is refused with one of these: "an Anthropic API key starts with sk-ant-" (you pasted something else); "that is an Admin API key; create a regular API key in the gregtec workspace"; "the key looks cut off or has spaces in it; copy it again". If **Test** fails, its note gives the vendor's reason (a wrong key, no credit, no network): fix that and click **Test** again. Each click is one more tiny call.

### Step 38. Screen 3, Discord bot token

Click **Skip for now**. Discord is not part of this guide.

You should see: the next screen, **ElevenLabs API key**.

If not: nothing to fix here. Click **4 ElevenLabs API key** in the list on the left.

### Step 39. Screen 4, ElevenLabs API key

Click **Skip for now**. Only a paid voice from the internet would use this key.

You should see: the next screen, **Headset**.

If not: click **5 Headset** in the list on the left.

### Step 40. Screen 5, Headset

Two drop-down lists: **Microphone** and **Spoken answers**. Voice is not part of this guide, so the choice does not matter today. If your Arctis headset is plugged in, you can pick its microphone, and for **Spoken answers** the entry whose name contains "Arctis Nova Elite" and "Chat": that saves doing it later. Otherwise leave both as they are. Click **Save and continue**.

You should see: the next screen, **Talk key**. A yellow row such as "'Chat' isn't connected now" does not stop the save.

If not: the save is refused with a red row. Voice is not needed today: click **8 Checklist** in the list on the left and go on with step 43.

### Step 41. Screen 6, Talk key

Leave **Talk key** at `F13` and **Mode** at "Hold to talk". Click **Check**. This reads Play's key settings file and changes nothing.

You should see: two rows: "Talk key in GTNH: F13 is free in Play" and "Talk key in Discord: a key Discord leaves free by default; check Settings > Keybinds". A yellow row "no options.txt in ... yet: start Play once, then check again" is fine too. Do **not** start Play for it.

Then click **Save and continue**.

If not: a red row: click **8 Checklist** in the list on the left and go on with step 43.

### Step 42. Screen 7, Wake phrase

One box. It is empty, with grey example text `hey archivist`. Leave it empty. Click **Save and continue**.

You should see: the last screen, **Checklist**.

If not: click **8 Checklist** in the list on the left.

### Step 43. Screen 8, Checklist, then Finish

The rows load by themselves.

You should see:

| Row | Expected | Colour |
|---|---|---|
| Prism folder, Prism instances folder | found | green |
| Play instance, Export instance, Lab instance | GTNH 2.9.0, java17+ build, game folder .minecraft | green |
| GOS_APP | found | green |
| GOS_HOME | there and writable | green |
| Recipe DB and GT pin | GT pin 5.09.54.265: no DB yet, so no recipes ... | green |
| Keyring | the name of Windows' key store | green |
| Anthropic API key | stored | green |
| Discord bot token | not stored: needed from WO-9.1 (Discord) | yellow, fine |
| ElevenLabs API key | not stored (not needed) | green |
| Voice settings | the voice client reads them | green |

Click **Finish setup**. The window goes back to the dashboard.

If not: **Finish setup** is grey because a row is red. Fix what the red row names, click **Re-run checks**, then **Finish setup**. If you cannot tell what it wants, copy the row's text, stop and ask.

### Step 44. Restart the core so the saved settings count

Saved settings take effect when the core next starts. (Keys work at once.)

```powershell
& $gos stop
& $gos serve
& $gos status
```

You should see: first `Stopped (the supervisor keeps running; ...)` with a short list. Then `The supervisor is running; started what was stopped:` with a short list. The window is given back at once. Then the status table, with the Supervisor and Core lines `ok` and `running` again, and now `Anthropic key  ok  stored`. The key line can take up to 30 seconds to turn.

If not: the Core line says `starting`: wait five seconds and run `& $gos status` again. `& $gos serve` holds the window and prints nothing more: then the supervisor was not running. Press Ctrl+C, then run `& $gos serve --detach`.

### Step 45. Check what the wizard wrote

```powershell
Get-Content "$gh\config\former_plays.txt"
Select-String -Path "$gh\config\gos.toml" -Pattern 'former_plays'
Select-String -Path "$gh\config\gos.toml" -Pattern 'completed_utc'
```

You should see: three lines that start with `#` and then `C:\Games\PrismLauncher\instances\GTNH-Play`. Then a line with `former_plays = ["C:\\Games\\PrismLauncher\\instances\\GTNH-Play"]` (the file doubles the backslashes). Then a line with `completed_utc = "2026-` and today's date.

This record is a safety net: every folder that has ever been Play is written down, so the sync can never be pointed at it as Export or Lab. You never need to edit it.

If not: the last command prints nothing: **Finish setup** was not clicked. Go back to step 43. The first fails with "cannot find path": the Paths screen was not saved. Go back to step 36.

More detail: [setup_wizard.md](runbooks/setup_wizard.md).

---

## Part 6. Recipe data

GOS knows no recipe until you give it your pack's own. The Export instance starts once, writes everything the pack knows into files, and closes by itself. Then two commands copy those files into GOS_HOME and build the database from them. Nothing in this part costs money, and nothing touches Play.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 46. Check that Export has today's GregtecLink

The sync in step 27 should have put it there.

```powershell
(Get-Content -Raw "$app\release.json" | ConvertFrom-Json).gregteclink.jar
Get-ChildItem "$exp\mods\gregteclink*" | Select-Object Name
```

You should see: the same file name twice, like `gregteclink-<hex>.jar`. The second command lists one file only. The November file `gregteclink-362a22e5cbb7.jar` is gone.

If not: the names differ, or there are two files: close Export and Prism and run `& $py $sync --only export`. Then run this step again.

### Step 47. Check that the export part of the mod is switched on

```powershell
Get-Content "$exp\config\gregteclink.cfg"
```

You should see:

```text
# GregtecLink in GTNH-Export: the export module only (D98).
# sync_instances.py rewrites this file at every sync; Play never gets it.

modules {
    B:client=false
    B:export=true
    B:listener=false
}
```

If not: "cannot find path": run `& $py $sync --only export`, then this step again. Without this file the mod does nothing.

### Step 48. Look at which NEI is in Export

NEI is the mod that lists recipes in the game. The export needs NEI version 2.8.172-GTNH in Export. Play keeps its own version. This swap is done once, by you, in Export only.

```powershell
Get-ChildItem "$exp\mods\NotEnoughItems-*" | Select-Object Name
```

You should see: one file. Either `NotEnoughItems-2.8.171-GTNH.jar` (the swap is still to do: go on with step 49) or `NotEnoughItems-2.8.172-GTNH.jar` (already done: skip to step 51).

If not: two files: delete the 2.8.171 one from that folder, **in Export only**, then skip to step 51. Another version: stop and ask.

### Step 49. Download NEI 2.8.172 and check it (only if step 48 showed 2.8.171)

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$nei = "$env:USERPROFILE\Downloads\NotEnoughItems-2.8.172-GTNH.jar"
Invoke-WebRequest -UseBasicParsing https://github.com/GTNewHorizons/NotEnoughItems/releases/download/2.8.172-GTNH/NotEnoughItems-2.8.172-GTNH.jar -OutFile $nei
(Get-FileHash $nei).Hash
```

The last line prints the file's fingerprint (its SHA256 hash). The first line only makes the download work in older PowerShell.

You should see: exactly `9362A063C04AAF11A668291542223725DB1147E76F483936AD965530E084104F`

If not: any other value: **stop.** Delete the downloaded file and do not copy it anywhere. "Could not create SSL/TLS secure channel": paste the first line again, then the third.

### Step 50. Swap NEI in Export only (only if step 48 showed 2.8.171)

Export and Prism must be closed. Both lines name `$exp`, the Export folder. Never do this in Play or Lab.

```powershell
Remove-Item "$exp\mods\NotEnoughItems-2.8.171-GTNH.jar"
Copy-Item "$env:USERPROFILE\Downloads\NotEnoughItems-2.8.172-GTNH.jar" "$exp\mods\"
Get-ChildItem "$exp\mods\NotEnoughItems-*" | Select-Object Name
```

You should see: one file, `NotEnoughItems-2.8.172-GTNH.jar`.

The sync never undoes this: it is told to leave NEI's file in Export alone.

If not: `Remove-Item` says the file is in use: Export is open. Close it and run the step again.

### Step 51. Add the autoexport flag to GTNH-Export in Prism (once)

With this flag, every start of GTNH-Export becomes an export that runs by itself and then closes the game. The project decided the flag stays for good. It does nothing in any other instance.

1. Open Prism Launcher. Click **GTNH-Export** once. Click **Edit**.
2. Go to **Settings**, then the **Java** tab.
3. Find the box for Java arguments (it may be labelled "JVM arguments"). Tick its check box if it is not ticked.
4. Put the cursor at the **end** of what is already in the box. Type a space, then `-Dgregtec.autoexport=true`. Keep everything that was there. If the box was not ticked before and is empty once you tick it, type only the flag, and tell me that this happened: I could not check what Prism then does with its general Java arguments.
5. Close the Edit window. **Do not open GTNH-Play's settings.**

The names of the buttons and tabs are from the project's notes. Your Prism version may word them a little differently.

Then check, in PowerShell:

```powershell
Select-String -Path "C:\Games\PrismLauncher\instances\GTNH-Export\instance.cfg" -Pattern '^JvmArgs=', '^OverrideJavaArgs='
```

You should see: a line `JvmArgs=` that ends with `-Dgregtec.autoexport=true`, and a line `OverrideJavaArgs=true`.

If not: it prints nothing, or the flag is missing: Prism may write that file only when it closes. Close Prism and run the check again. If it still differs, trust the Prism window: open the box again and look.

### Step 52. Run the export

**First close Play and Lab, if either is open.** Prism will start a second game without asking, and nothing in GOS stops it yet. Two games at once is the one thing this project never does.

Then, in Prism: click **GTNH-Export**, click **Launch**, and touch nothing. Note the time.

You should see: these five things, in this order.

1. The game loads to its main menu. This is the normal long GTNH start.
2. A world named "GregtecLink autoexport" opens by itself.
3. The chat says `GregtecLink: exporting NEI's recipes; the game freezes for a while.`
4. The game freezes. That is expected. Do not click, and do not close it.
5. It saves, leaves the world, shows the main menu for a moment and **closes by itself**.

The project's notes say this takes "a few minutes, at most 30". Nobody has timed it on the full pack. Please note how long it took.

If not: Export sits at its main menu and nothing happens: the flag is missing or mistyped. Close Export and go back to step 51. A screen about a duplicate mod at the start: there are two NEI files. Close Export and go back to step 48. The game is still open 35 minutes after the click: close it, and read the result in the next step anyway.

### Step 53. Read the result of the run

Paste this function once per window. It only reads.

```powershell
function Show-NewestRun {                       # the newest finished Export run's manifest, read-only
  $r = Get-ChildItem "$exp\gregteclink\exports" -Directory | Where-Object Name -notlike '*.partial' | Sort-Object Name | Select-Object -Last 1
  $m = Get-Content -Raw "$($r.FullName)\manifest.json" | ConvertFrom-Json
  "run: {0}   size: {1:N0} MB" -f $r.Name, ((Get-ChildItem $r.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
  $m | Select-Object mode, world, complete, nei_version | Format-List
  "parts:"; $m.parts | Format-List; "part_errors:"; $m.part_errors | Format-List
  "part_warnings:"; $m.part_warnings | Format-List; "counts:"; $m.counts | Format-List; "java:"; $m.java | Format-List
  "durations_ms:"; $m.durations_ms | Format-List
  "files:"; $m.files.PSObject.Properties.Name | Where-Object { $_ -like 'crops/*' -or $_ -eq 'gt_ore_mixes.json' }
}
```

Then run it:

```powershell
Show-NewestRun
```

You should see: a long list. Read these six things, in this order. `complete : True` alone is **not** enough.

| # | Look for | It must be |
|---|---|---|
| 1 | the `run:` line | a date and time from just now. The time is in UTC, so the hour differs from your clock |
| 2 | `mode` and `complete` | `autoexport` and `True` |
| 3 | `nei_version` | `2.8.172-GTNH` |
| 4 | under `parts:` | `nei : ok`, `oredict : ok`, `palette : ok` and `autoexport : ok`. Also `gt_dump`, `machines` and `refs` should say `ok` |
| 5 | under `part_errors:` | nothing |
| 6 | under `counts:` | `palette_errors : 0` |

Also good to see: under `java:`, `charset : UTF-8`. Lines under `part_warnings:` do not fail the run. Go on, but keep the text. A `texture_errors` count above 0 is not a failure either. Please keep the `size:` line and the `durations_ms:` list: nobody has measured them on the full pack.

If not:

| You see | Cause | What to do |
|---|---|---|
| the `run:` line is old, or the function fails because there is no run | the export never started | run `Select-String -Path "$exp\logs\fml-client-latest.log" -Pattern 'autoexport: no run'`. It prints the reason. Then go back to step 51 |
| `nei : absent` | NEI was not swapped | go back to step 48, then step 52 |
| `nei : failed`, with a line under `part_errors:` | NEI's part failed or was not ready in time | do step 52 once more with nothing else heavy running. If it fails again, keep the file `$exp\logs\fml-client-latest.log`, stop and ask |
| `complete : False`, or `autoexport : failed` | the run hit its time limit | the same |
| `palette : failed` or `absent` | an old GregtecLink file | go back to step 46, then step 52 |
| `oredict : failed` | that part failed | do step 52 once more |

### Step 54. Copy the run into GOS_HOME

Run this alone and wait for it to end. Do not join it to the next command with `;`, and do not pipe it into another command.

```powershell
& $gos export collect
```

You should see: text of this shape. `<N>` are counts from your pack.

```text
Export: C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft\gregteclink\exports
Run <run id>: created <UTC time>, mode autoexport, GregtecLink <hex>, GT 5.09.54.265, NEI 2.8.172-GTNH
  GT: <N> recipe maps, <N> recipes, <N> machines, <N> items, <N> fluids
  NEI: ok, <N> files, <N> recipes
Collected into C:\Users\<you>\GregtecOS\data\exports\<run id>
  <N> files, <X> MB, each matching the manifest's sha256, size and line count.
```

Check two things: the run id is the one from the `run:` line in step 53, and the line says `NEI: ok`. A line `skipped ... .partial: still being written ...` is harmless.

If not: `Not collected: no complete run in ...`: there is no finished run. Go back to step 52. It names an older run, with a line `skipped ...: incomplete` above: the new run did not complete. Go back to step 53. Running it twice is safe: the second time says `Already collected`.

### Step 55. Build the database

Again alone, and not piped.

```powershell
& $gos db build
```

You should see: a long report. Its first lines:

```text
Built C:\Users\<you>\GregtecOS\data\gos.sqlite from run <run id> (export_run <n>)
  created <UTC time>, mode autoexport, GT 5.09.54.265, NEI 2.8.172-GTNH, GregtecLink <hex>
Recipes: <N> citation keys: GT <N>, GT+NEI <N>, NEI only <N> (crafting <N>, furnace <N>, other handlers <N>)
```

and near its end:

```text
Block catalog: <N> block states (<N> item variants) from <N> mods, <N> with a tile entity (never in a shell); <N> textures, <N> with an average colour
  by mod: ...
Report: C:\Users\<you>\GregtecOS\data\reports\db-<run id>.txt
```

How long it takes on the full pack is not known. The project's notes allow about five minutes for this step and the one before together.

If not:

| It says | What it means | What to do |
|---|---|---|
| `Not built: no collected run in ...` | step 54 did not work | go back to step 54 |
| `Not built: run ...: parts.oredict is 'failed' ...` | that run cannot be used | go back to step 52, then collect and build again |
| `Not built: ... collision ...` and a file name ending in `.tsv` | two different recipes got the same name. This has never been tried on the full pack, so it can happen | **stop.** Nothing was written. Send me the `.tsv` file the message names |
| `Not built: database is locked` | another program was writing | run the step again |
| lines that start with `!!!` at the very end | the pack's GregTech version is not the one GOS is built for | stop and ask, with those lines |

### Step 56. Check the report and the block catalog

The report was also saved as a file. Read four kinds of line from it, then ask GOS whether its two building themes find their blocks.

```powershell
Select-String -Path "$gh\data\reports\db-*.txt" -Pattern '^Block catalog', '^Collision test', '^  NEI:', '^WARNING'
& $gos theme check
```

You should see: from the first command, lines that each start with the report's file name. Among them: one `Block catalog: <N> block states ...`, one `Collision test: passed, ...`, and one `NEI: <N> lines: ...`. A line `WARNING: GT categories with recipes only in NEI ...` is expected and fine.

From the second command, two lines:

```text
ok      th-foundry v1 (catalog run ...)
ok      th-marble-works v1 (catalog run ...)
```

If not: `Block catalog: none`, `WARNING: block catalog not loaded`, or `theme check` says `no block catalog in the DB: ...`: the run was made without the block part. Go back to step 46, then do steps 52 to 55 again. `NEI: not used`: go back to step 48 and do the same. `WARNING: <n> blocks didn't read`, `WARNING: power data not loaded` or `WARNING: crops not loaded`: the database is built, but stop and send me the line.

The database is the file `gos.sqlite` in `%USERPROFILE%\GregtecOS\data`. You do not need to restart GOS: the dashboard notices the new data by itself.

More detail: [phase1_pc_checks.md](runbooks/phase1_pc_checks.md) (the long form of this part, with the checks for the other accounts).

---

## Part 7. One saved plan

A plan is GOS's answer to "how do I make this item at this rate": the machines, the recipes and what they use. The building tool needs one saved plan to build around. Making and saving a plan this way is free: no AI is asked.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

### Step 57. Make and save one plan

"steel ingot" at tier MV is the example the project's own tests use. Any other item works the same way: put its name between the quotes.

```powershell
& $gos plan "steel ingot" --tier MV --routes 1 --save
```

`--save` is what writes the plan. Without it the plan is only printed.

You should see: a first line like this, then a long description of the route. The first plan on a new database can take a few minutes: wait.

```text
saved pl-<10 letters and digits>: C:\Users\<you>\GregtecOS\data\plans\pl-<the same>.json
```

If not:

| It prints | What it means | What to do |
|---|---|---|
| `...\data\gos.sqlite doesn't exist` and a hint to run the database build first | no database yet | go back to step 55 |
| a question and a numbered list of items | the words fit more than one item | run it again with the exact name from the list between the quotes |
| a line that starts with `refused:` | the planner cannot make that item at that tier | read the sentence. Try another item or tier |
| `no such function: octet_length` | the SQLite inside the release is too old | go back to step 23 |
| under the `saved` line: `the vault note wasn't written: there is no vault at ...` | you have no vault folder yet | harmless here: the plan is saved. Go on |

### Step 58. Note the plan's id

```powershell
& $gos tool plan_list
```

You should see: a small table: one line per saved plan, with its name, then `plan:pl-` and ten letters and digits, then `production` and `proposed`. "proposed" is fine: nothing here needs an accepted plan.

Now put your plan's id into a name that the later commands use. Type your own id between the quotes, starting with `pl-`:

```powershell
$plan = "pl-0000000000"
$plan
```

In a new window, set `$plan` again the same way after you paste the block.

If not: the table is empty: the plan was not saved. Go back to step 57 and look for the word `--save`.

### Step 59. See whether every machine of the plan can be placed

The building tool first lays the plan's machines out on the ground. A machine made of many blocks (a multiblock) can only be placed when GOS knows its shape. Those shapes come from a separate, long step (Part 7b). This command tells you whether you need it.

```powershell
& $gos layout $plan
```

You should see: a first line like `Layout lo-... of plan:pl-...: <X> x <Z> blocks, <Y> high; <N> machines, ...`, then one line per placed machine. Decide by what else it prints:

- **No line says `not placed: no footprint`.** Every machine has a place. Skip Part 7b and go to Part 8.
- **Some lines say `not placed: no footprint`, and the first line counts 1 machine or more.** The tour and the bake-off will run, but the building will have no room for the machines those lines name. For a first look that is enough: go to Part 8. For a real building, do Part 7b first.
- **The first line counts 0 machines.** The building tool will refuse this plan ("the layout places no machine: nothing to house"). Do Part 7b, or go back to step 57 with another item.

Which case your plan is in depends on your pack's recipes. I do not guess it.

If not: `plan ... isn't saved`: the id in `$plan` is not right. Go back to step 58.

More detail: [designer.md](runbooks/designer.md) (the dashboard's Designer makes plans too).

---

## Part 7b. Structure data (only if step 59 sent you here)

Skip this whole part if step 59 printed no `not placed: no footprint` line.

This part runs as the `gos-dev` account. It fetches a small helper project, builds it for your pack's version, starts a game server without a window, and writes down the shape of every multiblock of the base pack. It reads and writes no Prism instance. It downloads several GB the first time and can take long: it stops itself after 4 hours at the latest. Start it only when the Export run of step 52 has finished. Do not sign out of Windows while it runs: that would end it.

Multiblocks from addon mods are not covered by it. They need the Lab, which is outside this guide.

**Paste this first in every new window of your own** (the gos-dev window does not need it):

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

Then set `$plan` again if this is a new window, with your own id from step 58:

```powershell
$plan = "pl-0000000000"
```

### Step 60. Open a gos-dev window and update its clone

In your own window:

```powershell
runas /user:gos-dev powershell
```

It asks for gos-dev's password and opens a second window. That window starts in `C:\Windows\system32`. In the **new** window:

```powershell
cd C:\GregtecOS-dev\GretecOS
git switch main
git pull
uv sync
java -version
```

You should see: `Already on 'main'` or `Switched to branch 'main'`, the pull's result, uv's result without an error, and a Java version of 17 or higher.

If not: `java -version` fails: run `$env:JAVA_HOME` in that window. If it prints a folder, you are fine. If it prints nothing, stop and ask: the extract needs Java in the gos-dev account. `git` or `uv` "is not recognized" in that window: stop and ask.

### Step 61. Extract the structures (in the gos-dev window)

```powershell
uv run gos structures extract --out C:\GregtecOS-dev\structures\2.9.0-pc1
```

Leave the window alone until it ends.

You should see: `Fetching gtnh-extractor ...`, then `Re-pinned to GTNH 2.9.0 ...`, then a line about running the dev server, then for a long time the build's own text. At the end:

```text
The dev server loaded <...>.
Dumped <N> controllers (<V> variants) to ...; <F> not dumped, <R> set aside by the reader.
Next: gos structures load C:\GregtecOS-dev\structures\2.9.0-pc1
```

`<N>` should be 250 or more.

If not: `Extract failed: ...`: copy the message, stop and ask. `... is not empty; name a new --out folder.`: a first try left files behind. Run the command again with `2.9.0-pc2` in place of `2.9.0-pc1`, and use that name in the next step too.

### Step 62. Load the structures into your database (in your own window)

```powershell
& $gos structures load C:\GregtecOS-dev\structures\2.9.0-pc1
```

You should see:

```text
Loaded into C:\Users\<you>\GregtecOS\data\gos.sqlite
structures: run 1 from C:\GregtecOS-dev\structures\2.9.0-pc1\multiblocks
  pack 2.9.0, GT 5.09.54.265
  controllers <N> (with dimensions and cells), variants <V>, cells <N>, hatch slots <N>
  by package: ...
  linked to machine rows: <N>
```

The number after `linked to machine rows:` must be above 0. A few lines `not dumped by the extractor:` are normal.

If not: `linked to machine rows: 0`: the database of Part 6 is missing or empty. Go back to step 56.

### Step 63. Lay the plan out again

```powershell
& $gos layout $plan
```

You should see: the same first line as in step 59 with a higher machine count, and fewer or no `not placed: no footprint` lines.

If not: lines still say `not placed: no footprint`: those machines come from an addon mod, or the extract could not write them down. The tour still runs if the first line counts 1 machine or more. Otherwise go back to step 57 with another item.

To make the saved plan count the multiblocks' own blocks in its effort figures, save the plan once more (step 57) after this load. For the building tour that is not needed.

More detail: [phase1_pc_checks.md](runbooks/phase1_pc_checks.md) (its part on structures).

---

## Part 8. The building tour

The building tool draws a shell: a building around a plan's machines. Everything in this part uses GOS's own generator. It is free and asks no AI. Nothing here touches a world: a shell is only a drawing until you export it and paste it in the Lab, and that is not part of this guide.

One button costs money and is **not** part of the tour: **Ask the architect**. It sends your words to the AI. Do not click it today.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

Then set `$plan` again if this is a new window, with your own id from step 58:

```powershell
$plan = "pl-0000000000"
```

### Step 64. Open the Build view

The core must be running. Check, and start it if it is not:

```powershell
& $gos status
```

In the dashboard window click **Build** in the list on the left (it has no number key). Press F5 once: the list of plans is read only when the view opens, so a plan saved after that shows only after a reload.

You should see: in PowerShell, the Supervisor and Core lines `ok` and `running`. In the dashboard, a panel named **Shell workbench** with a **Plan** list, a **Theme** list, a box for words and the buttons **Design new**, **Revise** and **Ask the architect**.

If not: `GOS isn't running.`: run `& $gos serve --detach`, then this step again. The **Plan** list holds only "Pick a plan": run `& $gos tool plan_list`. If your plan is listed there, press F5 in the dashboard. If not, go back to step 57. The **Theme** list holds only one blank entry: run `& $gos theme list`. It should print `th-foundry` and `th-marble-works`. If it does, press F5. If it prints nothing, the release is incomplete: go back to step 21.

### Step 65. Design a first shell

In **Plan** pick your plan. In **Theme** pick **Foundry**. In the words box type:

```text
a heavy foundry around the line
```

Click **Design new**.

You should see: a progress line with the stages, and a few seconds later the building drawn below, with the note "Designed: its first version is current."

If not: the page says "the layout places no machine: nothing to house": go back to step 59. It says "no block catalog in the DB ...": go back to step 56. The page does not react at all: do the same from PowerShell, which makes the same thing:

```powershell
& $gos shell design $plan --theme th-foundry --line-type smelting --prompt "a heavy foundry around the line"
```

That prints a line `build sh-... saved in ...` and a line `Shell sh-...: accepted (procedural), ...`. Press F5 in the dashboard to see it.

### Step 66. Change it with words, twice

Type this in the words box and click **Revise**:

```text
make the roof steeper
```

Then this, and click **Revise** again:

```text
taller, and no windows on the north wall
```

You should see: after each click a note that starts with "New version:" and says what changed (for example the roof's style and the new height). The **Versions** list now holds v1, v2 and v3, and v3 is the current one.

If not: "Refused, nothing written; the current version stays: GOS's reviser can't do '...'": the free reviser knows a small set of changes. Type the words exactly as above. The same from PowerShell (keep the double quotes, because of the comma):

```powershell
& $gos shell revise "make the roof steeper"
& $gos shell revise "taller, and no windows on the north wall"
& $gos shell versions
```

### Step 67. Undo: make an older version current

In the **Versions** list click **Make current** on v2.

You should see: v2 is marked as the current one. Nothing is deleted: v3 is still in the list.

If not: from PowerShell, `& $gos shell undo` makes the version before the current one current again, and `& $gos shell versions` shows the list with a `*` on the current one.

### Step 68. Compare two versions

Tick **compare** on two versions in the list.

You should see: a panel "Compare v... and v..." with both buildings side by side and a table of the blocks each uses.

If not: only one tick is set: tick a second version.

### Step 69. Look inside with the 3D viewer

Drag the picture to turn it. Use the mouse wheel or the zoom buttons to zoom. Drag the **Layer** slider down to see the building one layer at a time.

You should see: the label "Layer N of M" changing as you drag, and dashed outlines inside the building. Those are your machines and the free space they need.

If not: the picture stays empty: press F5 and pick the version again in the **Versions** list. If it stays empty, note it and go on: Part 9 does not need the viewer to work for your own shell.

More detail: [shells.md](runbooks/shells.md), and [shell_bakeoff.md](runbooks/shell_bakeoff.md) (its part C shows how a shell gets into the Lab).

---

## Part 9. The free bake-off

A bake-off gives the same six building requests to several designers and lets you pick the better building without knowing who drew it. In this part the designers are three small programs inside GOS, the "fake models". Nothing leaves the PC and nothing is spent. `fake-plain` draws a simple hall. `fake-ornate` draws a fancier one. `fake-sloppy` gets its first answer wrong on purpose, so you can see GOS's check refuse it and the repair round fix it.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

Then set `$plan` again if this is a new window, with your own id from step 58:

```powershell
$plan = "pl-0000000000"
```

### Step 70. See which models you can name

```powershell
& $gos shell bakeoff models
```

You should see:

```text
label	provider	model	effort	price	key
fake-plain	fake	fake-plain	default	free	none needed
fake-ornate	fake	fake-ornate	default	free	none needed
fake-sloppy	fake	fake-sloppy	default	free	none needed
# run cap: $10.00, and 1,000,000 tokens for models without a price ([bakeoff] cap_usd, cap_tokens)
```

If not: `Can't read gos.toml: Invalid statement (at line 1, column 1)`: the settings file was saved in a form GOS cannot read. Open it in Notepad, choose "Save as", set the encoding to "UTF-8" (not "UTF-8 with BOM"), and save over it.

### Step 71. Run the free bake-off

```powershell
& $gos shell bakeoff run $plan --models "fake-plain,fake-ornate,fake-sloppy"
```

It asks nothing, because it is free.

You should see: a few lines about what it will do, among them exactly this one:

```text
Contacts: nothing. Fake models only: free.
```

Then a line `Run bo-<date>-<time>-<4 letters or digits> (Ctrl+C stops it cleanly):`, then one line per building (36 of them), each naming a request and a letter A, B or C, never a model. Then:

```text
Run bo-...: done. 48 calls, $0.00.
Next: look at it in the Build view's Bake-off panel, rate it there or with `gos shell bakeoff rate bo-...`, then `gos shell bakeoff report bo-...`. To remove it: `gos shell bakeoff delete bo-...`.
```

The number of calls can differ. Now put the run's name into a name for the next steps. Type your own between the quotes:

```powershell
$run = "bo-00000000-000000-0000"
```

If not:

| It says | What to do |
|---|---|
| `case foundry-small can't be set up: [Errno 2] No such file or directory: ...` | the id in `$plan` is not a saved plan. Go back to step 58 |
| `case foundry-small can't be set up: no block catalog in the DB: ...` | go back to step 56 |
| `case foundry-small can't be set up:` and a reason about the layout (`the layout places no machine: nothing to house`) | go back to step 59 |
| `no bake-off model '...'` | a typing mistake in the names after `--models`. No spaces after the commas |

### Step 72. Look at the buildings and rate them

In the dashboard's **Build** view, scroll down to the panel **Shell bake-off**. It appears by itself a few seconds after a run exists. At its top are the six requests by name. For each request:

1. Look at the answers side by side. They are named **A**, **B** and **C**. Which model is which letter is hidden, and it is shuffled for every request.
2. Click **After the follow-up** to see each building after the change that was asked. Click **The first design** to go back.
3. Pick one: **A is better**, **B is better**, **C is better**, **Tie** or **None is good**.
4. Click the next request's name at the top.

You should see: the top right of the panel says "blind: the models are A, B, C until you have rated". A tick appears on each request you have rated. After the sixth, the text beside the button at the bottom says "every case is rated".

If not: the panel is not there: wait five seconds, then press F5. If it stays away, rate from PowerShell. This shows the run, then asks for each pick in turn (type a letter, `tie` or `bad`, then Enter):

```powershell
& $gos shell bakeoff show $run
& $gos shell bakeoff rate $run
```

### Step 73. Name the models and read the report

Click **Name the models and show the report**. Or, from PowerShell:

```powershell
& $gos shell bakeoff report $run
```

You should see: a table with one line per model: your picks, how many answers passed GOS's check at the first try, how many passed in the end, the repair rounds, blocks, cost (free here). The sign that everything worked is the `fake-sloppy` line: `0 of 12` in the column "first try" and `12 of 12` in the column "passed". The command also prints `Written:` and the path of the report file, which ends in `.md` and sits in `%USERPROFILE%\GregtecOS\data\reports`. In that file's second table, the column "why refused" says for `fake-sloppy` why its first try was sent back (a chest, which a shell may not hold).

After this the picks for this run cannot be changed.

If not: it refuses with `... case(s) are not rated yet (...)`: go back to step 72 and rate the requests it names. The `fake-sloppy` numbers differ: that is not a setup failure. Keep the report file and tell me what it shows.

### Step 74. Remove the practice run

```powershell
& $gos shell bakeoff delete $run
```

It lists what it will remove (the run's folder and its two report files, nothing else) and waits. Type `yes` and press Enter.

You should see: `Removed run bo-...` The **Shell bake-off** panel leaves the Build view a few seconds later. Your own shells from Part 8 stay.

If not: you typed something else: it says `Nothing removed.` Run the step again. `... is not all removed yet`: another program held one of its files. Run the step again.

More detail: [shell_bakeoff.md](runbooks/shell_bakeoff.md) (Part 1).

---

## Part 10. The paid bake-off

The same six requests, now sent to two real models: one from Anthropic and one from OpenAI. This is the only part that spends real money. A model gets words and numbers and gives back a list of building steps. It gets no tools, runs no code and reads no file. What is sent: the request words, the theme, the names of the allowed blocks and the boxes of your layout. No key, file or world leaves the PC.

What holds the cost: each run stops at its cap (\$10 unless you change it), and it is also held to what your day limit still leaves (`[budget] day_max_usd`, \$10 unless you changed it). Before every paid run the command prints its estimate and waits for `yes`. Anything else typed there stops it with nothing spent.

**Paste this first in every new window:**

```powershell
$ProgressPreference = 'SilentlyContinue'
$app   = "C:\GregtecOS-app\current"
$gos   = "$app\.venv\Scripts\gos.exe"
$py    = "$app\.venv\Scripts\python.exe"
$sync  = "$app\scripts\sync_instances.py"
$gh    = "$env:USERPROFILE\GregtecOS"
$exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
```

Then set `$plan` again if this is a new window, with your own id from step 58:

```powershell
$plan = "pl-0000000000"
```

### Step 75. Check which keys are stored

```powershell
& $gos key status
```

You should see: a first line that names Windows' key store, then four lines. `anthropic: stored` (from the wizard) and `openai: not stored` are the two that matter.

If not: `anthropic: not stored`: store it now with `& $gos key set anthropic`. It asks `anthropic key (input hidden): `. Paste the key and press Enter.

### Step 76. Store an OpenAI key

In your browser, at `https://platform.openai.com/`, make an API key and add a little credit (OpenAI's API is paid in advance). Then:

```powershell
& $gos key set openai
```

It prints `openai key (input hidden): `. Paste the key (nothing shows) and press Enter. The key goes into Windows Credential Manager, never into a file.

You should see: `openai: stored in the OS keyring`

If not: `Not stored: that is an Anthropic key; paste the OpenAI one (platform.openai.com, API keys).`, or `Not stored: the key looks cut off or has spaces in it; copy it again.`, or `Nothing entered; nothing stored.`: run the step again with the right key.

### Step 77. Name the two models in gos.toml

Open the settings file:

```powershell
notepad "$gh\config\gos.toml"
```

Add this at the very end, then save:

```toml
[bakeoff]
cap_usd = 10.0             # the most one run may spend

[[bakeoff.models]]
label = "opus"             # your short name for it
provider = "anthropic"
model = "claude-opus-5-5"  # GOS knows this one's price already
effort = "high"            # how hard it thinks: the same word for both models

[[bakeoff.models]]
label = "astra"
provider = "openai"
model = "gpt-6-astra"
effort = "high"
input_usd = 10.00          # price per million tokens, from OpenAI's pricing page
output_usd = 50.00
cached_input_usd = 1.00
```

**The OpenAI model id and its three prices are yours to check on the day.** The values above are what OpenAI's pages listed on 2026-11-20 and 2026-11-21. The id is on `https://developers.openai.com/api/docs/models` ("Model ID"). The prices are on `https://developers.openai.com/api/docs/pricing` (Standard: "Input", "Output", "Cached input", per 1M tokens). Type what you read there. Never put a key into this file: GOS refuses a model block that holds one.

You should see: Notepad saves without a question.

If not: nothing to see yet. The next step tells you whether GOS can read the file. A mistake in this block never stops the core. It only stops the bake-off commands.

### Step 78. Check that GOS reads the block and sees both keys

```powershell
& $gos shell bakeoff models
```

You should see: the three fake lines as before, and two new lines of this shape, each ending in `stored`:

```text
opus	anthropic	claude-opus-5-5	high	$<price> in / $<price> out per million tokens	stored
astra	openai	gpt-6-astra	high	$10.00 in / $50.00 out per million tokens	stored
```

If not:

| It says | What to do |
|---|---|
| a line ends `not stored (uv run gos key set openai)` | go back to step 76 |
| `Can't read gos.toml: Invalid statement (at line 1, column 1)` | Notepad saved the file as "UTF-8 with BOM". Choose "Save as", set the encoding to "UTF-8", save over it |
| `Can't read gos.toml: ...` with another reason | the reason names the line or the key that is wrong. Compare with the block in step 77 |
| `effort must be one of ...` or `has no key ...` | a typing mistake in the block. The message lists what is allowed |

### Step 79. $ Run one request first

`--limit 1` sends only the first of the six requests, so you see it work for little money.

```powershell
& $gos shell bakeoff run $plan --models "opus,astra" --limit 1
```

You should see: lines of this shape. Read them before you answer.

```text
Bake-off: 1 cases x 2 models, a design and a follow-up each: 4 answers, at most 12 calls with the repair rounds (2 a step).
Each call: at most 16000 output tokens (thinking included); the briefs are about <N> tokens each.
  opus: anthropic claude-opus-5-5, effort high: ... : likely about $<X>, worst case $<X>
  astra: openai gpt-6-astra, effort high: ... : likely about $<X>, worst case $<X>
Contacts: api.anthropic.com, api.openai.com. Sent: the briefs (the request words, the theme, the palette's block names and the layout's boxes). No key, file or world leaves this PC otherwise.
Hard cap for this run: $<X>. It stops before a call that could pass it, with what it has so far.
Type yes to spend up to $<X>:
```

Type `yes` and press Enter to spend it. Then a line `Run bo-... (Ctrl+C stops it cleanly):`, one line per building, and `Run bo-...: done. <N> calls, $<X>.`

If not: you typed anything but `yes`: it says `Not run.` and nothing was sent or spent. A line that starts with `Not run:` gives the reason, and nothing is spent in any of these cases:

| The reason | What to do |
|---|---|
| `no anthropic key is stored ...` or `no openai key is stored ...` | go back to step 75 or step 76 |
| `the AI is off (...)` | the AI is switched off, or paused because the month's credits are used up. The words in the brackets say which. For the pause: click **Resume AI** on the dashboard's banner. Then run the step again |
| `today's spend ... has reached the day cap ...` | run it another day, or raise `day_max_usd` under `[budget]` in `gos.toml` |
| `this run's cap ... is less than one answer can cost ...` | the cap is too low. Raise `cap_usd` in the block of step 77 |

The run ends with `stopped (a model could not be asked at all (a refused key, a wrong model id or no credit), so nothing more was spent on the others)`: one model refused its very first request. Do step 83 now.

### Step 80. $ Run all six requests

The same day or later. Ctrl+C stops it cleanly: what is finished stays.

```powershell
& $gos shell bakeoff run $plan --models "opus,astra"
```

You should see: the same kind of estimate, now for 6 cases. If you already spent something today (step 79 did), a line `Today's limit ([budget] day_max_usd) leaves $<X>: this run's cap is lowered from $10.00 to that, so the day's limit holds too.` stands just above the "Hard cap" line. That is normal. Type `yes`. It then takes a few minutes per model and ends with `Run bo-...: done. <N> calls, $<X>.` If it reaches the cap first, it stops cleanly and says so.

Put this run's name into a name for the next steps. Type your own between the quotes:

```powershell
$run2 = "bo-00000000-000000-0000"
```

If not: the same `Not run:` reasons as in step 79. To give this one run a lower limit, add `--cap-usd 6` (or another number) to the end of the command.

### Step 81. Rate it blind

Rate exactly as in step 72. With more than one run, the **Shell bake-off** panel has a list of runs at its top: pick the full run there (the newest is first). Take your time, and look at both the first design and the one after the follow-up. Do not open the file `run.json` in the run's folder before you have rated: it is the only place that knows which letter is which.

You should see: "every case is rated" beside the button at the bottom of the panel.

If not: the panel does not show the run: rate from PowerShell with `& $gos shell bakeoff rate $run2`.

### Step 82. Read the report

Click **Name the models and show the report**, or:

```powershell
& $gos shell bakeoff report $run2
```

You should see: one line per model, now with its real name, your picks, how many answers passed at the first try and in the end, the repair rounds, tokens, cost and seconds. Your usual **Costs** view in the dashboard (key `8`) counts these runs too, under the lane named `bakeoff`.

How to read it: **picked** is the only number about looks. **first try**, **passed** and **repair rounds** say how well a model followed the rules. Six requests are a first look, not proof: 4 to 2 can be chance.

If not: `... case(s) are not rated yet`: go back to step 81.

### Step 83. Only if a run ended with "stopped" or still says "running"

Skip this step if both paid runs ended with `done`.

**A run ended with "stopped (a model could not be asked at all ...)".** Set `$run2` to that run's name as in step 80. Then ask for the report at once. `--reveal` names the models before rating, which is fine for a run you will throw away:

```powershell
& $gos shell bakeoff report $run2 --reveal
```

You should see: a line "The run did not finish (stopped: ...)". It names the model and what the vendor said. Fix that one thing: a refused key (store it again, step 76 or `& $gos key set anthropic`), no credit (add credit on the vendor's site), or a refused request (the `model` id or the `effort` word in step 77 is not one that vendor takes today). Then remove the stopped run and start again at step 79:

```powershell
& $gos shell bakeoff delete $run2
```

**A run still says `running` although no window runs it** (the window was closed or the PC restarted). What it finished is kept. Wait until 15 minutes have passed since it last did anything, then close it. It says what it does and waits for `yes`. Nothing is deleted:

```powershell
& $gos shell bakeoff close $run2
```

After that, rate it and read its report as in steps 81 and 82. The report then says "cancelled".

If not: `close` answers `was last heard from ... minute(s) ago: it may be going in another window`: it is still running somewhere, or it stopped less than 15 minutes ago. Stop it there with Ctrl+C, or wait and run the line again.

More detail: [shell_bakeoff.md](runbooks/shell_bakeoff.md) (Part 2 and "If something is off").

---

## Part 11. What you have now and what comes next

**What you have now.**

- Today's release in `C:\GregtecOS-app\current`, with the GregtecLink mod and the dashboard built into it.
- The core service running, and the dashboard on monitor 2 paired for good.
- The setup wizard finished: your paths and the record of Play in `gos.toml`, and your Anthropic key in Windows Credential Manager.
- Export set up to export by itself: NEI 2.8.172 and the autoexport flag. **From now on every start of GTNH-Export is an export that closes itself.**
- The recipe database, built from one run of your own pack.
- One saved plan, your own shell designs from the tour, and (if you did Part 10) a bake-off report that compares two real models on your plan.

**Three things to know until the later sittings are done.**

- **GOS does not start by itself yet.** After a restart of the PC, open PowerShell, paste the block and run `& $gos serve --detach`. Starting at login is set up in a later sitting.
- **Keep closing Play and Lab by hand before every Export start.** The parts that would stop a second game for you are not installed yet.
- **Do not start your survival world on the strength of this guide.** The world-backup test (Session 2 of the quick start) must pass first.

**What this guide skipped, and why that was safe.** The whole road to day one is in [quick_start.md](quick_start.md), one line per step, with the long form in [deployment_checklist.md](deployment_checklist.md). This guide did the deploy and setup steps of its Session 1, the first Export run and your own database from Session 2, and Session 13b. It left out:

| Skipped | Where it is in the quick start | Why it was safe to skip here |
|---|---|---|
| Updating the gos-dev clone | Session 1 | Nothing in this guide runs as gos-dev, except Part 7b, which updates that clone itself |
| gos-dev's own database, its power commands and palette checks | Session 2 | Each account builds its own database straight from Export. Yours does not use gos-dev's. Those steps feed gos-dev's later tests |
| The core-service acceptance checks with Play at its main menu (a PLAY step), and `gos launch` | Session 2 | They test that the supervisor sees a running game and refuses a second one. No data step needs that. Skipping it is safe only with the rule above: one game at a time, closed by hand |
| The Lab tweaks and the Lab's Prism hooks | Session 2 | They matter when you play in the Lab. This guide never opens it |
| Vault deploy, maintenance and measuring | Session 2 | A plan saves without them, the two starter themes are built in, and the database can always be rebuilt with `& $gos db build` |
| The world-backup test | Session 2 | Not needed for recipe data or buildings. **It must pass before the survival world starts** |
| Structure data, if you did not need Part 7b | Session 3 | Only plans with multiblocks need it. Step 59 told you |
| The Lab checks of recipes, quests, ores and crops, and the pack-update rehearsal | Session 3 | They prove the data against the game. The building tool does not depend on them |
| The dashboard checks and the paid evals | Session 4 | They test the question-answering lanes, which this guide does not use |
| The planner and power checks | Sessions 5 and 6 | A saved plan was enough for a building. Those sessions prove the planner's numbers in the Lab |
| Voice | Sessions 7 and 8 | Not used here. The wizard's voice screens were saved as they were |
| GregtecLink in the game, screen reading, the wiki | Session 9 | Not used here |
| Watchers, events, quests, OpenComputers | Sessions 10 and 11 | Not used here |
| Discord and the phone | Session 12 | Not used here. The Discord rows in the wizard and in `gos status` stay yellow or off until then |
| Pasting a shell into the Lab, footprints and themes by voice | Session 13 | The bake-off touches no world, so it can come first. Exporting a shell and pasting it is Lab work |
| Backups, safety and the start at login | Session 14 | See the three things above |
| The fake day one, the Play steps and the addon jars | Sessions 15 to 17 | They come last. Most of the PLAY steps are there |

---

## Not checked on a real PC

This guide was put together from the code at commit `3367d4f` and from test runs on a Linux machine. Nothing in it was run on Windows or on your PC. Where it mattered, the steps above are worded so that they do not lean on an unchecked detail. This is the list of what nobody could check:

- **Every PowerShell line.** No PowerShell was available. Lines taken from the project's runbooks and scripts were read against the code. Lines written for this guide were never run: the checks in Part 0, the `$clone` line, the SQLite line in step 23 (its Python part was run on Linux), the `Select-String` on the saved report in step 56, and the quoting of comma lists such as `--models "opus,astra"`.
- **`uv run gos deploy` itself.** Its steps, its printed lines and its refusals are from the code and its unit tests. How long it takes on your PC, and how much disk the release needs on Windows (about 1 GB on Linux), are not known.
- **Your PC's real state:** the tool versions, whether Node and pnpm are still installed, whether a winget pnpm 12 switches itself to 10.28.0 on Windows, whether the dashboard builds with Node 26 (the project's checks use Node 22), whether Chrome is installed, the old release's commit, and whether an old `gos.toml` exists and holds the placeholder host.
- **What holds `C:\GregtecOS-app\current`** when the swap fails with WinError 5.
- **The Chrome window:** that it opens on monitor 2, its place and size, and whether another browser can be paired in its place.
- **Some exact words on Windows:** the Maintenance, Deep lane and Presence lines of `gos status`, the name of the key store in the wizard's Keyring row and in `gos key status`, and whether the wizard's Paths boxes show backslashes or forward slashes.
- **The wizard on real hardware:** the real list of audio devices, storing a key in Windows Credential Manager, and the **Test** call against the real service.
- **The names of Prism's buttons and tabs** for the Java arguments in your Prism version, and the two names `JvmArgs` and `OverrideJavaArgs` in `instance.cfg`.
- **The Export run on the full pack:** how long it takes and how big a run is. Nobody has measured it. The same goes for the time of `gos export collect` and `gos db build`, and for whether the full pack's build passes the collision test. It has never been tried with the pack's addon mods.
- **The SQLite version inside your release's Python.** Probably new enough, since the project's Windows checks run the same query, but I cannot see which Python your uv picked. The fix for an old one is untested.
- **Your plan on the real pack:** whether "steel ingot" at MV resolves without a question, how long the first plan takes, and whether its machines include a multiblock. That last one is game data and is not guessed here: step 59 answers it.
- **Part 7b on your PC:** how long the extract takes, and that the gos-dev account has git and a Java of 17 or newer.
- **The dashboard in a real browser:** how the Build view and the bake-off panel look, the panel appearing by itself, drag, zoom and the layer slider, and the Costs view showing the lane `bakeoff`. The buttons' names were read in the source, and what they do was driven through the program's own interface.
- **Any real vendor call:** that the ids `claude-opus-5-5` and `gpt-6-astra` are accepted today, OpenAI's prices, real answers, real run time and real cost. The paid steps were run only against the project's fake vendors. The words a real vendor sends when it refuses a key may differ from the ones shown here.
- **Notepad's way of saving** `gos.toml`, and the "UTF-8 with BOM" fix for it.
- **That the supervisor notices a running Play on your PC.** The test for that is one of the skipped steps, which is why this guide says: close Play and Lab by hand.
