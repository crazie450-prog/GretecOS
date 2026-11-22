# Addon jars: one Lab sitting, then Play (D365, D373, D379)

One sitting in the Lab tests every addon jar that is waiting to go into Play. Play gets a jar
only after this test passes **and** you say yes (checklist 5.2.12 and 5.2.13). Nothing in
Gregtec OS copies these jars: every step here is yours.

| Jar (in `Downloads\GTNH-2.9.0`) | What it is | In the Lab it replaces | SHA256 (rechecked 2026-11-22, D379) |
|---|---|---|---|
| `sciencenotleisure-0.2.7.jar` and `gtnllib-1.1.6.jar` | GT-Not-Leisure 0.2.7 and its library: new (D365) | nothing; the two go in together | `17e7f8b2…2c97ce` and `83a3b0ab…02c9ca` |
| `TwistSpaceTechnology-0.8.2.jar` | Twist Space Technology, update from 0.8.0 (D373) | `TwistSpaceTechnology-0.8.0.jar` | `5d5a537a…f427ed` |
| `plannh-0.2.4.jar` | PlanNH, update from 0.2.0 (D373) | `plannh-0.2.0.jar` | `671e5471…10cbd7` |

The full hashes and download addresses are in `scripts/fetch_play_files.ps1`, which checks every
jar for you in step 2. You never type a hash.

**Not in this sitting** (rechecked 2026-11-22, D379; part G says what each waits for):
123Technology 2.2.1.3, Programmable Hatches v0.2.1p9-beta, worldedit-gtnh 1.0.2. The Lab and
Play keep `OTHTechnology-2.2.1.1.jar`, `programmablehatches-0.2.1p0.jar` and
`worldedit-v0.0.10.jar`.

**GTNH 2.9.1 came out on 2026-11-21.** This sitting is for 2.9.0, the pack Play has. If you
decide to move to 2.9.1 first (`pack_update.md`), do that before this sitting and ask for this
list to be checked again: TST has an open report that it won't start a server on 2.9.1 (#1430).

The building generator's mods (worldedit-gtnh, GuideNH, StructureLib, Schematica) need no new
jar. Their check is [`building_mods_check.md`](building_mods_check.md), a separate sitting.

**When.** In one go, about 1.5 hours, with Play closed until part E. From part B until part E
or F the Lab's mods differ from Play's, and the other Lab checks of checklist section 3 compare
the Lab with recipe data made from Play: run none of them in between.

**Why the old jar must go.** Two versions of one mod in a `mods` folder stop the game from
starting. **Why the sync entries come first.** `sync_instances.py` makes the Lab's mods equal to
Play's: without the entries of step 3 the next sync deletes the new jars and puts TST 0.8.0 and
PlanNH 0.2.0 back (D93 (5)).

## A. Fetch the jars (5 minutes)

1. Open a normal PowerShell window (never "Run as administrator") and paste the "Every new
   window" block from [`quick_start.md`](../quick_start.md). Then paste:
   ```powershell
   $dl = "$env:USERPROFILE\Downloads\GTNH-2.9.0"
   ```
2. Fetch and check the jars:
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$app\scripts\fetch_play_files.ps1"
   ```
   Worked: an `ok` line for each of the four new jars, no `FAILED` line. The files you already
   had say `(already here)`. At the end, "NOT into Play yet (D365)" lists the two GT-Not-Leisure
   jars and "NOT into Play yet (D373)" lists the other two, each with "(replaces ...)". If a
   line says `FAILED`, run it again; if it fails twice, stop and send the output.

## B. Prepare the Lab (10 minutes; Prism and every game window closed)

3. Tell the sync to leave these jars alone. Open your settings file:
   ```powershell
   notepad "$gh\config\gos.toml"
   ```
   Find the line `[sync.lab]`. If it is not there, add these two lines at the end of the file.
   If it is there, keep what its `exclude` list already has and add the four entries:
   ```toml
   [sync.lab]
   exclude = ["mods/sciencenotleisure-*", "mods/gtnllib-*", "mods/TwistSpaceTechnology-*", "mods/plannh-*"]
   ```
   Save and close Notepad. (Phase 10's Lab files in `config\worldedit\schematics` and
   `config\guidenh` are already left alone by default, D380: they need no entry here.)
4. Keep a copy of the Lab world, so a failed test costs nothing:
   ```powershell
   Copy-Item "$lab\saves\lab-ops" "$env:USERPROFILE\Downloads\lab-ops-before-addon-test" -Recurse
   ```
5. Take the two old jars out of the Lab and put the four new ones in:
   ```powershell
   Remove-Item "$lab\mods\TwistSpaceTechnology-0.8.0.jar", "$lab\mods\plannh-0.2.0.jar"
   Copy-Item "$dl\sciencenotleisure-0.2.7.jar", "$dl\gtnllib-1.1.6.jar", "$dl\TwistSpaceTechnology-0.8.2.jar", "$dl\plannh-0.2.4.jar" "$lab\mods\"
   Get-ChildItem "$lab\mods" | Where-Object Name -match 'twist|plannh|notleisure|gtnllib' | Select-Object Name
   ```
   Worked: exactly four names are listed: `gtnllib-1.1.6.jar`, `plannh-0.2.4.jar`,
   `sciencenotleisure-0.2.7.jar`, `TwistSpaceTechnology-0.8.2.jar`. If an old jar is still
   listed, delete it before you go on.
6. Check that the sync will leave them alone (this writes nothing):
   ```powershell
   & $py "$app\scripts\sync_instances.py" --dry-run --only lab
   ```
   Worked: the line `excluded (never copied or deleted):` names the four new jars (and Play's
   two old ones), and no `delete` or `copy` line names a `TwistSpaceTechnology`, `plannh`,
   `sciencenotleisure` or `gtnllib` jar. If one does, fix step 3 first.

## C. The test (about 1 hour, Lab only)

7. Launch **GTNH-Lab** in Prism and wait for the main menu. If the game closes or shows a crash
   screen here, go to part D.
8. On the main menu click **Mods**. Worked: Twist Space Technology says **0.8.2**, PlanNH says
   **0.2.4**, and GT Not Leisure and GTNLLib are in the list.
9. Load the world `lab-ops`. Worked: it loads, with no "missing blocks" question.
10. Press E and type `@twist` in NEI's search box, then `@leisure`. Worked: TST's items show
    for the first, GT-Not-Leisure's for the second, and their recipes open.
11. **TST 0.8.2:** build one TST multiblock (any; the one you built on 2026-11-05 is fine), let
    it form and run one recipe to the end.
12. **123Technology (unchanged, 2.2.1.1):** build one 123Technology multiblock and run one
    recipe. It is not updated, but it looks up TST's things by name, so check it once with the
    new TST.
13. **GT-Not-Leisure 0.2.7:** build one GTNL multiblock, let it form and run one recipe.
14. **Programmable Hatches #323** (D93 (3)): build a GTNL large centrifuge with a Programmable
    Hatches dual input hatch, and run a recipe through it. Worked: no crash, and the machine
    still works after you leave the world and come back.
15. **PlanNH 0.2.4:** press F8. Open a chart you made with 0.2.0. Worked: it opens and looks as
    you left it. Add one recipe to it, close the planner, open it again: the change is there.
16. **Memory** (GTNL #575, a leak with TST, is still open): press F3 and note the memory line
    now, after 10 minutes and after 20 to 30 minutes in the world. Worked: it goes up and down
    but does not climb steadily toward the limit.
17. Quit the game from the menu.

**Paste back** (in the PR or to the PM): which multiblock and recipe for steps 11, 12 and 13;
the result of step 14; the three memory readings; anything odd you saw. Then answer 5.2.12
(GT-Not-Leisure) and 5.2.13 (TST 0.8.2 and PlanNH 0.2.4) in the checklist.

## D. If the game crashes or a step fails

18. Find the newest crash report and the log:
    ```powershell
    Get-ChildItem "$lab\crash-reports" | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
    "$lab\logs\fml-client-latest.log"
    ```
    Send both files (the crash report may not exist if the game only froze: then send the log).
    They hold no keys.
19. Take the new jars out **one at a time**, in this order, and launch the Lab after each. If
    the crash report's first lines name one of the mods (search it for `twist`, `plannh`,
    `leisure`), start with that one.
    1. PlanNH back to 0.2.0:
       ```powershell
       Remove-Item "$lab\mods\plannh-0.2.4.jar"
       Copy-Item "$dl\plannh-0.2.0.jar" "$lab\mods\"
       ```
    2. TST back to 0.8.0:
       ```powershell
       Remove-Item "$lab\mods\TwistSpaceTechnology-0.8.2.jar"
       Copy-Item "$dl\TwistSpaceTechnology-0.8.0.jar" "$lab\mods\"
       ```
    3. GT-Not-Leisure out (both jars):
       ```powershell
       Remove-Item "$lab\mods\sciencenotleisure-0.2.7.jar", "$lab\mods\gtnllib-1.1.6.jar"
       ```
20. Say which removal made the crash stop. That jar is not going into Play; the others can
    still be tested (go back to step 7).
21. If `lab-ops` was opened with a jar you then took out, put the world copy back before you
    load it again (the newer mod's machines would be missing):
    ```powershell
    Remove-Item "$lab\saves\lab-ops" -Recurse
    Copy-Item "$env:USERPROFILE\Downloads\lab-ops-before-addon-test" "$lab\saves\lab-ops" -Recurse
    ```

## E. After a pass and your yes: into Play (15 minutes; Prism closed)

Do this **before the survival world starts**. GT-Not-Leisure is a world gate (D02): it cannot
be added to a running world. These lines are yours to paste; they are the same as step 5, with
Play's folder. Paste only the lines for what you said yes to.

22. Name Play's folder:
    ```powershell
    $play = "C:\Games\PrismLauncher\instances\GTNH-Play\.minecraft"
    ```
23. Yes to 5.2.13 for TST 0.8.2:
    ```powershell
    Remove-Item "$play\mods\TwistSpaceTechnology-0.8.0.jar"
    Copy-Item "$dl\TwistSpaceTechnology-0.8.2.jar" "$play\mods\"
    ```
24. Yes to 5.2.13 for PlanNH 0.2.4:
    ```powershell
    Remove-Item "$play\mods\plannh-0.2.0.jar"
    Copy-Item "$dl\plannh-0.2.4.jar" "$play\mods\"
    ```
25. Yes to 5.2.12 for GT-Not-Leisure:
    ```powershell
    Copy-Item "$dl\sciencenotleisure-0.2.7.jar", "$dl\gtnllib-1.1.6.jar" "$play\mods\"
    ```
26. Check Play has one jar per mod:
    ```powershell
    Get-ChildItem "$play\mods" | Where-Object Name -match 'twist|plannh|notleisure|gtnllib' | Select-Object Name
    ```
    Worked: one `TwistSpaceTechnology` jar and one `plannh` jar, in the versions you chose.
27. Launch **GTNH-Play** to the main menu, click **Mods** and check the versions, then quit. Do
    not create a world. If GOS asks "Mods changed. Export now?", answer **Export now**: the
    recipe data is then rebuilt from the new jars ([`addon_watcher.md`](addon_watcher.md)).
28. Open `gos.toml` again (step 3) and take out the entries for the jars Play now has. Then let
    the sync make the Lab and Export equal to Play:
    ```powershell
    & $py "$app\scripts\sync_instances.py"
    ```
    Worked: "Refused" is never printed.

## F. A no, or not yet: put the Lab back

29. Open `gos.toml` (step 3), take out the entries for the jars that are **not** going into
    Play, save, and run the sync:
    ```powershell
    & $py "$app\scripts\sync_instances.py" --only lab
    ```
    It deletes those new jars from the Lab and copies Play's versions back.
30. If `lab-ops` was opened with a jar that is now gone, put the world copy back (step 21).

**Rolling Play back later.** The old jars stay in `Downloads\GTNH-2.9.0`, and
`fetch_play_files.ps1` still fetches and checks them. With Prism closed, do steps 23 and 24 the
other way round (remove the new jar, copy the old one in). A world saved with TST 0.8.2 that
holds one of its newer machines (Bottled Chaos, the Mega Digester) loses those machines under
0.8.0.

## G. What waits

`& $gos pack watch` reports each of these on every run, with the line "newest release ... isn't
in the installed jar's name: a newer release? Check it in Lab before Play". That line is
expected until the addon is pinned here. Nothing to do on your side until then.

Rechecked on 2026-11-22 (D379): no newer 123Technology or Programmable Hatches release; worldedit-gtnh now has 1.0.2.

- **123Technology 2.2.1.3** waits for a release its author does not call work in progress. His
  notes on 2.2.1.3 say "Contains WIP Content", and on 2.2.1.2 "WIP DO NOT USE". Since 2.2.1.2
  the mod's internal id changed (with a converter for a world's old blocks and items), it now
  needs TST to start, and it is built against TST 0.8.0.
- **Programmable Hatches v0.2.1p9-beta** waits for the author's README to name a newer version
  for GTNH 2.9.0. Today its table still says 0.2.1p0 for 2.9.0 and lists the newer builds under
  "2.9.0 Daily" (D88's rule).
- **worldedit-gtnh 1.0.2** (2026-11-21) waits for a build made for GTNH 2.9.0's mods, or for
  the stable GTNH that ships the mods it is built against. It is a rewrite (1.0.0 and 1.0.1 on
  2026-11-10), built against newer CodeChickenCore, Carpenter's Blocks and NEI than 2.9.0 or
  2.9.1 ship, and GOS's own paste test still runs on v0.0.10. 1.0.2 adds EndlessIDs schematic
  save and load (upstream #24, closed), and #30 (meta above 15) is closed since 1.0.0, but
  neither helps while Play has v0.0.10. The shell paste keeps v0.0.10 and its limits (meta 15
  at most, V16). Trying 1.0.2 in the Lab anyway is your call (D379).
- **PlanNH 0.2.6** (2026-11-22) is built on exactly 2.9.0's libraries and could replace 0.2.4 in
  this sitting if you prefer it (D379). Until you say so, the sitting tests 0.2.4. PlanNH 0.3.0
  is for 2.9.1.
