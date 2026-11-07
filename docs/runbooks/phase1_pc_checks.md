# Phase 1 PC checks: one ordered sitting

One checklist for every PC step the merged Phase 1 and 2 work orders need. It starts where the PC is now: Phase 0 done and G0 signed off (D99), GOS_APP deployed at an older commit, Export holding GregtecLink `362a22e`, and Export's NEI not yet swapped. The steps are in dependency order. Each one says **who** runs it, the commands, what to expect and what to paste back. The linked runbook sections have the details, so this page doesn't repeat them.

Allow a long afternoon. Most of the time is game launches and two idle half hours. Step 8a runs unattended in the background from the end of step 3, and step 13 (signing out) comes last, once 8a has finished.

## Before you start

**What is deployed.** This page follows `main` at `cf11f8c` (PR #40). Four PRs are still open. If you merge any of them before step 1 and before gos-dev's `git pull` below, the lines marked with its tag also apply:

| Tag | PR | What it adds to these checks |
|---|---|---|
| **[#44]** | `wo/1.1c-oredict` (D145) | `parts.oredict` in the manifest and an oredict source in `gos db build`'s report |
| **[#41]** | `wo/2.5b-d142-vault` (D143) | `vault measure` skips `me/`, plus the session-end DB backup check |
| [#42], [#43] | ore ranking, IOF/EIG/LMA facts | nothing new to check on the PC |

**Two kinds of window.** Keep one of each open (steps 8a and 10 each open a second one). Re-paste the block if you open a new window, because PowerShell forgets these variables when a window closes.

- **Clayton** (a normal PowerShell window, *not* "Run as administrator"):

  ```powershell
  $gos   = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
  $py    = "C:\GregtecOS-app\current\.venv\Scripts\python.exe"
  $sync  = "C:\GregtecOS-app\current\scripts\sync_instances.py"
  $hooks = "C:\GregtecOS-app\current\scripts\install_prism_hooks.py"
  $prism = "C:\Games\PrismLauncher\prismlauncher.exe"
  $exp   = "C:\Games\PrismLauncher\instances\GTNH-Export\.minecraft"
  $gh    = "$env:USERPROFILE\GregtecOS"          # your GOS_HOME
  function Show-NewestRun {                       # the newest finished Export run's manifest, read-only
    $r = Get-ChildItem "$exp\gregteclink\exports" -Directory | Where-Object Name -notlike '*.partial' | Sort-Object Name | Select-Object -Last 1
    $m = Get-Content -Raw "$($r.FullName)\manifest.json" | ConvertFrom-Json
    "run: {0}   size: {1:N0} MB" -f $r.Name, ((Get-ChildItem $r.FullName -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
    $m | Select-Object mode, world, complete, nei_version | Format-List
    "parts:"; $m.parts | Format-List; "part_errors:"; $m.part_errors | Format-List
    "part_warnings:"; $m.part_warnings | Format-List; "counts:"; $m.counts | Format-List; "java:"; $m.java | Format-List
    "files:"; $m.files.PSObject.Properties.Name | Where-Object { $_ -like 'crops/*' -or $_ -eq 'gt_ore_mixes.json' }
  }
  ```

- **gos-dev**. From your window, run `runas /user:gos-dev powershell`. It opens in `system32` ([pc_setup_notes §2](pc_setup_notes.md#2-runas-windows-start-in-system32)). Then:

  ```powershell
  cd C:\GregtecOS-dev\GretecOS
  git switch main
  git pull
  uv sync
  ```

  The `wo/1.5-quests` and `wo/1.6-ores` branch switches in the older runbooks are no longer needed, because both are on `main`.

**Rules for the whole sitting.** gos-dev never touches Play. Anything that touches Play (Play's hooks, launching Play, unticking a mod) is your own step, run from GOS_APP. gos-dev's `gos` commands write only gos-dev's own GOS_HOME and `C:\GregtecOS-dev`; `gos launch` asks your supervisor to start Lab or Export.

## What each step closes

| Step | Who | Closes (VERIFY ids are from `core/gregtec/verify.tsv`) |
|---|---|---|
| 1 Deploy and sync | Clayton | prerequisite for everything (D98 jar install) |
| 2 NEI swap and autoexport flag | Clayton | prerequisite for 3 |
| 3 Export run and manifest | Clayton | **V03**, **V22**; WO-1.1 checks (1) and (2); the manifest half of WO-1.6 and WO-1.10 |
| 4 Collect and DB build | gos-dev (4a), Clayton (4b) | WO-1.2, WO-1.3 and WO-1.7 on a full-pack run; D108's addon modids; full-pack sizes for D102 and D107; the coverage half of **V33** |
| 5 Quests | gos-dev and the Lab | WO-1.5 acceptance |
| 6 Ores | gos-dev and the Lab | **V07** (Lab check); WO-1.6 acceptance |
| 7 Crop spot checks | gos-dev and the Lab | **V33** (rest); WO-1.10 acceptance |
| 8 Structures | gos-dev (extract, started after step 3), Clayton (load) | **V09**; WO-1.8 |
| 9 Core service, pairing, `gos launch` | Clayton, then gos-dev | **V24**; WO-2.1 acceptance (login start in step 13); WO-0.8's "`gos launch lab` starts Lab" |
| 10 Play's hooks | Clayton | **V25** (PC confirmation); WO-1.4 acceptance |
| 11 Vault | Clayton | WO-2.5; the D141 format decision (only with #41) |
| 12 World-backup job | Clayton | WO-0.8's last acceptance item (D97; must pass before G11) |
| 13 Login start | Clayton, last | WO-2.1's login-start check ([core_service §B.8](core_service.md#b-acceptance-checks-8-wo-21)) |

V08 is already resolved. V26, V27 and V29 belong to later WOs and aren't touched here.

---

## 1. Deploy current `main` and sync Export (Clayton, about 10 min)

Details: [pc_setup_notes: gos deploy builds GregtecLink](pc_setup_notes.md#gos-deploy-builds-gregteclink-sync-installs-it-into-export-d98).

- [ ] Close Prism and every game (Play, Lab, Export).
- [ ] In your clone: `git switch main`, `git pull`, then `uv run gos deploy`.
  Expect `Built GregtecLink: ...gregteclink-<commit>.jar` with its SHA256. GOS isn't running yet, so there is no `Stopping GOS` line. A `JAVA_HOME is not set` error means nothing was swapped: fix Java and deploy again.
- [ ] `Test-Path C:\GregtecOS-app\scripts\prism_prelaunch.ps1, C:\GregtecOS-app\scripts\prism_postexit.ps1, C:\GregtecOS-app\scripts\install_login_start.ps1, C:\GregtecOS-app\scripts\backup_world.ps1` should print `True` four times.
- [ ] Save the "before" snapshot for WO-1.1 check (2). Do this **before** the sync:
  ```powershell
  & $gos doctor
  Copy-Item "$gh\data\env.json" "$gh\data\env-before.json"
  ```
- [ ] Sync Export only, dry run first:
  ```powershell
  & $py $sync --only export --dry-run
  & $py $sync --only export
  ```
  Under `export:` expect `gregteclink: <jar> from ... (release <12 hex>), SHA256 matches`, then `delete  mods/gregteclink-362a22e5cbb7.jar` and `install mods/gregteclink-<commit>.jar`, and a `write config/gregteclink.cfg` line only if that file has changed since the last sync. Lab and Play get nothing. If you see `Write failed`, Export is still open: close it and sync again.

**Paste back:** the deployed commit, the `Built GregtecLink` line, and the sync's `gregteclink:` lines.

## 2. Swap NEI and set the autoexport flag in Export (Clayton, about 10 min, done once)

Details: [mod/README: NEI 2.8.172 in Export](../../mod/README.md#nei-28172-in-export-manual-swap-d100) and [pc_setup_notes: WO-1.1 PR 1b](pc_setup_notes.md#wo-11-pr-1b-nei-swap-autoexport-and-the-two-closing-checks-d100) steps 4 and 5.

- [ ] Download the jar and check its hash:
  ```powershell
  $nei = "$env:USERPROFILE\Downloads\NotEnoughItems-2.8.172-GTNH.jar"
  Invoke-WebRequest -UseBasicParsing https://github.com/GTNewHorizons/NotEnoughItems/releases/download/2.8.172-GTNH/NotEnoughItems-2.8.172-GTNH.jar -OutFile $nei
  (Get-FileHash $nei).Hash   # must be 9362A063C04AAF11A668291542223725DB1147E76F483936AD965530E084104F, else stop
  ```
- [ ] Swap it in **Export only**:
  ```powershell
  Remove-Item "$exp\mods\NotEnoughItems-2.8.171-GTNH.jar"
  Copy-Item $nei "$exp\mods\"
  Get-ChildItem "$exp\mods\NotEnoughItems-*"   # only the 2.8.172 jar
  ```
  Sync's exclude `mods/NotEnoughItems-*.jar` keeps it from now on.
- [ ] In Prism, open GTNH-Export → Edit → Settings → Java. Tick JVM arguments if it isn't ticked, then add `-Dgregtec.autoexport=true` after the existing arguments. **Don't touch GTNH-Play.** With the flag set, every Export launch is an unattended export that quits, so remove the flag before you use Export by hand.

**Paste back:** the hash line.

## 3. One Export run and its manifest (Clayton, 15 to 40 min)

Details: [pc_setup_notes: WO-1.1 PR 1b](pc_setup_notes.md#wo-11-pr-1b-nei-swap-autoexport-and-the-two-closing-checks-d100) steps 6 to 10, [crops_check §A.4](crops_check.md#a-a-full-pack-export-with-the-crop-step-after-the-pr-is-merged) and [ores_check §B.4](ores_check.md#b-ore-mixes-from-a-full-pack-export-after-the-pr-is-merged).

- [ ] **Check (1), V22:** click **Launch** on GTNH-Export and do nothing. The "GregtecLink autoexport" world loads, chat says the game freezes for NEI's dump, then the game saves, leaves the world and closes by itself. That takes a few minutes, at most 30. Note any dialog that appears.
- [ ] Run `Show-NewestRun` and check each item:
  - [ ] `mode : autoexport`, `world : gregteclink-autoexport`, `complete : True`, `nei_version : 2.8.172-GTNH` (**V03**).
  - [ ] parts: `gt_dump`, `machines`, `refs`, `nei` and `autoexport` all `ok`. `ore_mixes : ok` and `crops : ok`. **[#44]** `oredict : ok`.
  - [ ] No `part_errors`. Paste any `part_warnings` (for example `nei` dropped a handler).
  - [ ] counts: `nei_files` is above 0 and `nei_recipes` equals `nei_reported`. `ore_mixes` is above 100 with `ore_mix_errors` 0. `crop_dumpers` equals `crop_files` (11). **[#44]** `oredict` is above 0 and `oredict_errors` is 0.
  - [ ] java: `charset : UTF-8`.
  - [ ] files: `crops/cropRegistry.csv`, `crops/deterministicMutations.csv`, `crops/mutationPools.csv`, `crops/soilRegistry.txt`, `crops/alternateSeed.csv` (and six more `crops/` files) and `gt_ore_mixes.json`.
  - [ ] Note the **size** line. D102 and D107 need the full-pack size, which nobody has measured yet.
- [ ] **V22, command line:** with Prism closed, run `& $prism --launch GTNH-Export`. A second run should appear the same way (if Prism was open, the command returns at once). `Show-NewestRun` should show a newer `run:`.
- [ ] **Check (2), Play untouched:**
  ```powershell
  & $gos doctor
  cd "$gh\data"
  $before = (Get-Content env-before.json | ConvertFrom-Json).prism_roots.instances | Where-Object folder_name -eq 'GTNH-Play' | ForEach-Object mods
  $after  = (Get-Content env.json        | ConvertFrom-Json).prism_roots.instances | Where-Object folder_name -eq 'GTNH-Play' | ForEach-Object mods
  Compare-Object $before $after -Property file, enabled, sha256     # prints nothing
  cd C:\Games\PrismLauncher\instances
  Get-ChildItem GTNH-Play\.minecraft\config\gregteclink*            # no file with export=true
  Select-String gregtec GTNH-Play\instance.cfg                      # prints nothing
  ```
- If check (1) fails, use the fallback in [pc_setup_notes step 9](pc_setup_notes.md#wo-11-pr-1b-nei-swap-autoexport-and-the-two-closing-checks-d100) and send `$exp\logs\fml-client-latest.log`.
- [ ] Step 3 is done: start step 8a now, in a second gos-dev window.

**Paste back:** `Show-NewestRun`'s full output for the first run, the second run's `run:` line, and the output of check (2) (it should be empty).

## 4. Collect and build the DB

### 4a. In gos-dev's GOS_HOME (gos-dev, about 10 min)

Details: [crops_check §A.5 and §A.6](crops_check.md#a-a-full-pack-export-with-the-crop-step-after-the-pr-is-merged).

- [ ] In the gos-dev window:
  ```powershell
  uv run gos export collect
  $s = Get-Date; uv run gos db build; "db build took {0:N0} s" -f ((Get-Date) - $s).TotalSeconds
  ```
- [ ] collect prints `Export: ...`, the run's NEI status, and `Collected into ...\data\exports\<run_id>` followed by its `<N> files, <X> MB` size.
- [ ] db build's counts report should show:
  - [ ] `Recipes: ...`, an `NEI: ...` line (not `NEI: not used`), and `Collision test: passed`.
  - [ ] A `WARNING: GT categories with recipes only in NEI ...` line is expected and not a failure: the planner skips those recipes and the report lists them (D142 (1)).
  - [ ] **[#44]** `NEI-only oredict slots: ... names from the run's full ore dictionary (<N> names)`.
  - [ ] `Addons`: TST (`TwistSpaceTechnology`), GT-Not-Leisure (`sciencenotleisure`) and 123Technology (`othtech`) each have an `ok` line, not `MISSING` or `not loaded`. Note any `no GT recipe in <map>` lines under them. Programmable Hatches shows `ok ... (it adds to GT's own maps, none expected)`. This is the first full-pack check of D108's modids.
  - [ ] `Machine facts (WO-1.7): ...` has `Base multiblocks (GT's jar): ... missing 0`, and there is no `WARNING: <n> base multiblocks have neither the facts nor a flag` line (WO-1.7's acceptance: every base multi has parallels and OC rules at ≥ 0.8, or is flagged).
  - [ ] `Crops: <N> crops ...` with N at least 131 and every drop keyed. This is the **V33 coverage line**.
  - [ ] `NEI's CropsNH tabs: Crop Breeding <B> recipes with <K> crops, ... ; all <S> crops they show are in the crop dump`, with B, K and S all above 0.
  - [ ] No `WARNING: crop data:`, no `requirement texts not recognised`, no `NOT in the crop dump` and no `they show no crop`.
- [ ] Still in the gos-dev window, the DB's size: `(Get-Item $HOME\GregtecOS\data\gos.sqlite).Length` (use `$env:GOS_HOME\data\gos.sqlite` if gos-dev has GOS_HOME set).

### 4b. In your own GOS_HOME (Clayton, about 5 min)

This step isn't in any runbook. Step 8b (structures load links machines through the recipe DB) and step 11 (`vault measure` counts two DB tables, the biggest part of D141's total) both read **your** DB, so build it now from GOS_APP:

- [ ] `& $gos export collect` and then `& $gos db build`. Expect the same report as 4a.

**Paste back:** collect's output (its `files, MB` line is the collected run's size) and db build's whole printed report from 4a, the `db build took` line, and the `gos.sqlite` size. For 4b, only whether it matched 4a.

## 5. Quest frontier and tier (gos-dev plus the Lab, about 25 min)

Details: [quests_check](quests_check.md), sections 1 to 5. The world is `lab-progress`.

- [ ] Launch GTNH-Lab and open `lab-progress`. Turn the book's **View Mode** off. Run `/bq_admin reset all` and then `/clear`.
- [ ] Before every load, press **Esc** in game and leave the menu open until the load finishes.
- [ ] State A: `uv run gos quests load --instance lab --world lab-progress`, then `uv run gos quests frontier`. Expect `3814 quests in 46 quest lines`, `0 quests complete`, `Tier detected: stone` and one quest, *Your First Night*.
- [ ] State B: `/bq_admin complete AAAAAAAAAAAAAAAAAAAARg`. Then load, `frontier --line "Tier 1 - LV"` and `--line "Tier 0.5 - Steam"`. Expect `LV`, 4 quests.
- [ ] State C: `/bq_admin complete AAAAAAAAAAAAAAAAAAAAoA`. Then load and `frontier --line "Tier 3 - HV"`. Expect `HV`, 17 quests, 4 of them in HV.
- [ ] Override: `uv run gos quests tier --set MV`, then `uv run gos quests tier --clear`.
- [ ] Repeatable: follow §5 (Chef Training), then `frontier --line "Feeding Yourself"`. `load` says `4 quests complete, 1 of them repeatable again` and `Frontier: 21 quests`, and Chef Training's row says `repeatable`.
- [ ] Compare each chapter with the book.

**Paste back:** for each state, whether the tier and each compared chapter matched. For any mismatch, give the chapter, the quest, what the book showed, and the `frontier --line` output.

## 6. Ore veins in two dimensions (gos-dev plus the Lab, about 20 min)

Details: [ores_check §A](ores_check.md#a-veins-in-two-dimensions) and [§B.5 and §B.6](ores_check.md#b-ore-mixes-from-a-full-pack-export-after-the-pr-is-merged). Step 4a already collected a run with ore mixes, so parts A and B happen together here. Skip §0's branch switch and §B steps 1 to 4.

- [ ] In the Lab, create a new world `lab-ores` (Creative, Default world type, cheats on). Right-click one GT ore vein in the Overworld and one in the Nether, with an empty hand and **no Shift**, and note the chat lines. Then **Save and Quit to Title**.
- [ ] In the gos-dev window:
  ```powershell
  uv run gos ores load --world lab-ores
  uv run gos ores list
  uv run gos ores list --dim 0
  uv run gos ores list --dim -1
  ```
- [ ] `load` starts with `Ore mixes: <N> from export run <run_id> (<M> dimensions named)`, with no `no name` notes. It then shows `VisualProspecting world id: lab-ores_<uuid>`, a `server cache: ...` line with at least 2 dimension files and a `client cache (player ...):` line, both under `GTNH-Lab\.minecraft\visualprospecting\` (**V07**), and `Veins:` lines for `dim -1 (Nether)` and `dim 0 (Overworld)`, each with `1 known` or more. There is no `warning: veins of mixes this export doesn't have` line.
- [ ] `ores list` fills the `name` column. `list --dim 0` shows each vein's four ores (primary, secondary, between, sporadic) and a `y` range.
- [ ] The vein you clicked has `known` 1 and the mix chat named. Its `x`/`z` is 8 blocks west and north of chat's position, and its four ores match chat's `You may find:`.

**Paste back:** the output of the four commands and your two chat lines. If `load` says `no server cache yet` or `no client cache`, list the folders under `GTNH-Lab\.minecraft\visualprospecting\`.

## 7. Ten crop spot checks against NEI (gos-dev plus the Lab, about 30 min)

Details: [crops_check §B](crops_check.md#b-ten-spot-checks-against-nei-in-the-lab). The table there says which NEI tabs to open for each crop.

- [ ] In the gos-dev window:
  `uv run gos crops show wheat allium "rubber bonsai" "stone lily" tine redstraw "aluminium oreberry" necrobloom "oil berry" trollplant`
- [ ] In the Lab (any world), for each crop, search NEI for `<crop> Seeds`, press **R** and **U**, and compare with `gos crops show`. Compare against NEI, not against any number in a runbook.
- [ ] Optional: the coverage scroll in §B.4.

**Paste back:** for each crop, "match" or what differs (NEI's value next to `gos crops show`'s).

## 8. Structures (V09)

### 8a. Extract (gos-dev, unattended, up to a few hours)

This step reads and writes no Prism instance. **Start it once step 3 is done**, not before: its Gradle build, dev server and several-GB download share RAM and CPU with the Export client, which must have NEI ready within 10 minutes and finish within 30. Run it in a second gos-dev window and let it run alongside steps 4 to 12. Steps 9 and 10 also run Export: if one of those exports comes back incomplete or times out while 8a runs, retry it once 8a has finished. It needs `git` and a Java 17+ on gos-dev's `PATH` or `JAVA_HOME`. It stops itself after 4 h.

**Don't sign out (step 13) while 8a runs.** Signing out ends gos-dev's `runas` windows and a running 8a. A re-run then needs a new `--out` (for example `2.9.0-pc2`), because extract refuses a folder that isn't empty.

- [ ] From your window, `runas /user:gos-dev powershell` opens the second gos-dev window (in `system32`). In it:
  ```powershell
  cd C:\GregtecOS-dev\GretecOS
  uv run gos structures extract --out C:\GregtecOS-dev\structures\2.9.0-pc1
  ```
  The output goes in `C:\GregtecOS-dev` (D125), which you can read, and not in gos-dev's profile. The folder must be new or empty.
- [ ] Expect `Re-pinned to GTNH 2.9.0 ...`, then `The dev server loaded <pins>.`, then `Dumped <N> controllers (<V> variants) to ...; <F> not dumped, <R> set aside by the reader.` and `Next: gos structures load ...`. `<N>` must be at least 250 (WO-1.8 acceptance). `Extract failed: ...` means V09 stays open.

### 8b. Load into your DB (Clayton, 1 min)

- [ ] `& $gos structures load C:\GregtecOS-dev\structures\2.9.0-pc1` should print `Loaded into ...\GregtecOS\data\gos.sqlite` and the counts. The loader only reads JSON (D125).
- [ ] `linked to machine rows` is above 0 (it reads step 4b's DB).

**Paste back:** extract's last lines (from `The dev server loaded`), including every `not dumped` and `set aside` line, and load's output. If extract failed, paste its error and, from the gos-dev window, the last 50 lines of the dev server's log: `Get-Content <build>\run\server\logs\fml-server-latest.log -Tail 50`, where `<build>` is the build folder in the error (by default `$HOME\GregtecOS\data\cache\gtnh-extractor\build`, in gos-dev's profile, which you can't read from your own window).

## 9. Core service, pairing and `gos launch` (Clayton, then gos-dev, about 15 min)

Details: [core_service §A and §B](core_service.md#a-deploy-and-start-at-login-as-clayton) and [§C](core_service.md#c-v24-gos-launch-from-gos-dev). Step 1 already deployed, so skip §A.1.

- [ ] Clayton: `powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\install_login_start.ps1 -StartNow` should print `GOS starts at login: ...`.
- [ ] `& $gos key status` shows the Anthropic key stored. Then `& $gos status` shows `overall: ok`, with Supervisor, Core, Database, the key and API credits all `ok`. If GOS isn't running, check `$gh\logs\supervisor.log`, `core.log` and `core.stderr.log`, and look for `refusing to start`.
- [ ] Restart: `Stop-Process -Id <core pid> -Force; Start-Sleep 10; & $gos status` should show a new pid, `restarts: 1  last exit: code <non-zero>`.
- [ ] Host and Origin (§B.5): `evil.example` is refused for both, and the plain `/health` answers `ok`.
- [ ] Pairing (§B.6): `curl.exe -s http://127.0.0.1:8765/api/health` returns `not paired`, and Chrome shows the same at `http://gos.localhost:8765/api/health`. `& $gos pair` prints `No device is waiting to pair.` and `& $gos devices` prints `No paired devices.` Pairing itself gets a screen in WO-2.6.
- [ ] Run `& $gos stop`, then `& $gos status`, then `& $gos serve`, and it comes back. Then `& $gos stop --all` (the supervisor stops too) and the `install_login_start.ps1 -StartNow` command above again: `& $gos status` shows both processes running. The sign-out check is step 13, at the end.
- [ ] gos-dev (**V24**): add `[launch] home = "C:/Users/<you>/GregtecOS"` and the `[core]` block from §C.9 to gos-dev's `%USERPROFILE%\GregtecOS\config\gos.toml`. Use `$env:GOS_HOME\config\` instead if gos-dev has GOS_HOME set, and edit any existing `[core]` rather than adding a second one. Then:
  - [ ] `uv run gos launch lab` prints `Asked Prism Launcher to launch GTNH-Lab`, and Lab starts. Close it.
  - [ ] Right after `uv run gos launch export`, within 30 s, run `uv run gos launch lab`: it fails with `... s ago; wait`. Then let the export finish and quit. `uv run gos launch play` fails with `invalid choice`.
  - [ ] Clayton: start Play and wait for the main menu. gos-dev: `uv run gos launch lab` must fail with `refused: GTNH-Play is running`. Quit Play.

**Paste back:** the four `curl.exe` lines, Chrome's `/api/health` result, `gos pair` and `gos devices`, the `gos status` output after the restart, and each `gos launch` result (V24 is resolved if all of them match).

## 10. Play's hooks: Lab first, then Play (Clayton, about 45 min)

Details: [play_hooks §2 to §5](play_hooks.md#2-fingerprint-read-only). This step needs step 2's NEI swap: without it, every ask-on-quit export is `incomplete`.

- [ ] Fingerprint: run `& $gos fingerprint`, then `& $gos fingerprint --no-cache` (it hashes everything again), then `& $gos fingerprint --list | Select-String config/`. The same hash both times, the last line says `recipe data is stale: no export is recorded yet`, and `dreamcraft.cfg` plus the five GT configs are listed. If you know other configs that change recipes (TST, GTNL, 123Tech, PH), send their paths.
- [ ] **V25 on Lab** (Prism closed): run `& $py $hooks --instance lab --dry-run`, then `& $py $hooks --instance lab`. Then:
  - [ ] Open Prism: GTNH-Lab → Edit → Settings → Custom commands shows the two commands, with "Custom commands" ticked.
  - [ ] In a second Clayton window (paste the variables block): `& $gos lock hold --minutes 5`. Launch Lab. It must **not start**, and the console shows `GOS: not launching: the instance lock is held by test ...` and `Pre-Launch command failed with code 3.` Press Ctrl+C in the lock window.
  - [ ] Launch Lab again. It starts, then quit from the main menu. The console shows `Post-Launch command ran successfully.`
  - [ ] Close Prism. Rename `C:\GregtecOS-app\scripts\prism_prelaunch.ps1` to `prism_prelaunch.ps1.off`. Open Prism and launch Lab: it starts, and the console shows `GOS: C:\GregtecOS-app\scripts\prism_prelaunch.ps1 is missing (run gos deploy); ...` and `Pre-Launch command ran successfully.`, with no `#< CLIXML` lines. Quit Lab, close Prism, rename it back.
  - [ ] `& $py $hooks --instance lab --uninstall`.
- [ ] **Play** (your step, Prism closed): run `& $py $hooks --dry-run`, then `& $py $hooks`.
- [ ] Acceptance (§5):
  - [ ] Launch Play: the console shows `GOS: recipe data is stale: no export is recorded yet`. Quit and answer **Yes** to the dialog: the console shows `GOS: export started (pid ...)`. Export runs, and the message box says `First recorded export`. While Export runs, try to launch Play: it must be refused (the lock).
  - [ ] Launch Play again: the console says `recipe data matches Play`. Quit: no dialog.
  - [ ] Untick PlanNH and launch Play: the console says `stale ... 1 removed`. Quit and answer **Yes**. Then run `& $gos export diff`.
  - [ ] Re-tick PlanNH, launch, quit and answer **Yes**, so the last export matches Play.

**Paste back:** each console line and message box above, the output of `gos export diff`, `$gh\logs\export-*.log`, and the files in `$gh\data\reports\export-diff-*`.

## 11. Vault: deploy, maintain, measure, idle rule (Clayton, about 25 min plus two idle half hours)

Details: [vault_maintenance](vault_maintenance.md), sections B to D. GOS must be running (step 9), and your DB should be built (step 4b).

- [ ] `& $gos vault deploy` should print `updated: ...\GregtecOS\vault`.
- [ ] `& $gos vault maintain` should print six job lines, none `failed`, with `backup ok wrote gos-YYYYMMDD.sqlite`. `$gh\data\backups\` holds today's file, and `git -C "$gh\vault" log -1 --format="%s %an"` prints `GOS idle maintenance YYYY-MM-DD Gregtec OS`. Run maintain again: `index ok unchanged` and `git ok nothing to commit`.
- [ ] **D141, only with [#41]:** run `vault measure` only once #41 (D143) is merged and deployed. On `main` alone it sends `me/` (D141), which D142 (d) rules out: skip it and say so in the paste-back. With #41, `& $gos vault measure` makes about 400 free `count_tokens` calls, takes a few minutes and never reads `me/`. If it ends with `count_tokens failed`, wait 10 min and run it again.
- [ ] Power settings: `powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`, then the same with `HIBERNATEIDLE`. The AC index must be `0x00000000` or above `0x00000744`.
- [ ] Idle run: `& $gos vault maintain --rerun-today`. Leave the PC alone for 31 min with GTNH closed. `& $gos status` should then show `Maintenance ok last run <today>; done for <today>`.
- [ ] Game running: `--rerun-today` again, then start Play and leave it at the main menu for 31 min. `gos status` should show `... the game is running`, with no run.
- [ ] **[#41]** Session-end backup: this only shows on a day when the daily run is 2 days late, so it may not be testable in this sitting. When it is, run `& $gos stop`, then `Select-String -Path "$gh\logs\core.log" -Pattern "session-end backup" | Select-Object -Last 3`. Expect `ok ... wrote` or `skipped ... cut short`.

**Paste back:** both `vault maintain` outputs. From `vault measure`, **everything printed above `report:`**; D141's format decision is recorded from those numbers. Without #41, say that you skipped it. Also the two `powercfg` AC values (and whether you'll change them), the two `Maintenance` lines, and **[#41]** the session-end line if you saw one.

## 12. World-backup job test, before G11 (Clayton, 5 min after 30+ min of Lab play)

D97 deferred this from G0. The job (`\GregtecOS\GregtecOS world backup`, every 30 min, as you) targets Play, which has no world yet. So test the same script against **Lab** with a scratch destination. ServerUtilities writes a backup zip every 30 minutes (`backup_timer`), and the Lab sessions in steps 5 to 7 are shorter than that and paused often, so they probably made none. Keep one Lab world open and unpaused for 35+ min (for example, leave `lab-progress` open after step 7), then check.

- [ ] `Get-ChildItem C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft\backups\*.zip` should list at least one zip. If it lists none, keep a Lab world open and unpaused for another 35 min and check again.
- [ ] With Lab closed:
  ```powershell
  $t = "$env:TEMP\gos-lab-backup-test"
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\scripts\backup_world.ps1 -Instance C:\Games\PrismLauncher\instances\GTNH-Lab -Dest $t -LogFile "$t.log"
  ```
  Expect `Copied <name>.zip (<bytes> bytes).` and `Done: <n> copied, 0 removed.`. A second run prints `Done: 0 copied, 0 removed.`
- [ ] The scheduled task itself: `Get-ScheduledTask -TaskPath '\GregtecOS\' -TaskName 'GregtecOS world backup' | Get-ScheduledTaskInfo` should show a recent `LastRunTime` and `LastTaskResult` 0. `Get-Content "$gh\logs\backup_world.log" -Tail 3` should show `No backups folder at ... yet; nothing to do.` until Play has a world.
- [ ] Clean up: `Remove-Item -Recurse $t, "$t.log"`.

**Paste back:** both script outputs, plus the task's `LastRunTime` and `LastTaskResult`.

## 13. Login start (Clayton, 5 min, last)

Details: [core_service §B.8](core_service.md#b-acceptance-checks-8-wo-21). Do this only once 8a has finished (step 8): signing out ends every process in your session, gos-dev's `runas` windows and a running extract included.

- [ ] Sign out and back in. Open a new window, paste the variables block, and run `& $gos status`: Supervisor and Core are running, with no window opened.

**Paste back:** that `gos status` output.

---

## What to paste back

One message per step is fine. Claude Code turns these into VERIFY and decision updates.

1. **Deploy:** the commit, the `Built GregtecLink` line and the sync's `gregteclink:` lines.
2. **NEI swap:** the `Get-FileHash` line.
3. **Export (V03, V22):** `Show-NewestRun`'s full output for the first run (size included), any dialog, the CLI run's `run:` line, and the output of check (2) (should be empty).
4. **DB:** collect's output, db build's whole report (Recipes, NEI, oredict [#44], Addons, Machine facts, Crops and CropsNH-tabs lines), the build time, and the `gos.sqlite` size (collect's `files, MB` line gives the run's). Whether 4b matched 4a.
5. **Quests:** tier match for stone, LV and HV; each compared chapter, Feeding Yourself included; details for any mismatch.
6. **Ores (V07):** the output of `ores load` and the three `ores list` commands, plus your two chat lines.
7. **Crops (V33):** "match" or the difference for each of the ten crops.
8. **Structures (V09):** extract's closing lines and load's output, or the error and log tail.
9. **Core (V24):** the four `curl.exe` lines, Chrome's `/api/health` result, `gos pair` and `gos devices`, the `gos status` output after the restart, and each `gos launch` result.
10. **Hooks (V25):** the Lab console lines, Play's console lines and message boxes, `gos export diff`, `export-*.log` and the `export-diff-*` reports.
11. **Vault (D141):** both `vault maintain` outputs, the head of `vault measure` [#41] (or that you skipped it), the `powercfg` values, the two `Maintenance` lines and the session-end line [#41].
12. **Backup job (D97):** both `backup_world.ps1` outputs, plus the task's `LastRunTime` and `LastTaskResult`.
13. **Login start:** `gos status` after signing back in.
