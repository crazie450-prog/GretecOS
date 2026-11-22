# Addon jars: one Lab sitting, then Play (D365, D373, D379, D381)

One sitting in the Lab tests every addon jar that is waiting to go into Play. Play gets a jar
only after this test passes **and** you say yes (checklist 5.2.12 and 5.2.13). Nothing in
Gregtec OS copies these jars: every step here is yours.

You asked for the newest version of every addon, as long as they all work together
(2026-11-22, D381). So this sitting tests the newest release of each addon for GTNH 2.9.0. That
includes two that their authors call work in progress or a daily build (123Technology 2.2.1.3,
Programmable Hatches 0.2.1p9). The Lab test is what decides "working".

| Jar (in `Downloads\GTNH-2.9.0`) | What it is | In the Lab it replaces | SHA256 (checked 2026-11-22) |
|---|---|---|---|
| `sciencenotleisure-0.2.7.jar` and `gtnllib-1.1.8.jar` | GT-Not-Leisure 0.2.7 and its library: new (D365, D381) | nothing; the two go in together | `17e7f8b2…2c97ce` and `93011e8d…03befa` |
| `TwistSpaceTechnology-0.8.2.jar` | Twist Space Technology, update from 0.8.0 (D373) | `TwistSpaceTechnology-0.8.0.jar` | `5d5a537a…f427ed` |
| `OTHTechnology-2.2.1.3.jar` | 123Technology, update from 2.2.1.1 (D381) | `OTHTechnology-2.2.1.1.jar` | `1e29c3bd…5b7a64` |
| `programmablehatches-0.2.1p9.jar` | Programmable Hatches, update from 0.2.1p0 (D381) | `programmablehatches-0.2.1p0.jar` | `1f6aa649…f0264e` |
| `plannh-0.2.6.jar` | PlanNH, update from 0.2.0 (D381) | `plannh-0.2.0.jar` | `b3acb053…0ec37` |

The full hashes and download addresses are in `scripts/fetch_play_files.ps1`, which checks every
jar for you in step 2. You never type a hash.

**What to know about the two newest ones.**
- **123Technology 2.2.1.3:** its author's notes say "Contains WIP Content". Since 2.2.1.2 the mod
  has a new internal id (`123technology`) and needs TST to start. GTNLLib 1.1.8 knows the new id;
  1.1.6 doesn't, which is why GTNLLib is 1.1.8 here. A world saved with 2.2.1.3 can't go back to
  2.2.1.1. Later 123Technology releases may still change its unfinished machines.
- **Programmable Hatches 0.2.1p9:** the author's table lists it as the "2.9.0 Daily", the version
  he maintains, and keeps 0.2.1p0 as the 2.9.0 release.

**Not in this sitting.**
- **worldedit-gtnh** stays on `worldedit-v0.0.10.jar` (D381; part G says why).
- **GTNH 2.9.1** (released 2026-11-21) waits for a TST release that loads on it. TST 0.8.2 stops
  with an error while it registers its recipes on 2.9.1 (TST #1430). That code runs at every
  game start, single-player too, not only on a server. Until TST fixes it, all the addons
  together means 2.9.0. When the fix comes, the move to 2.9.1 is the pack update
  ([`pack_update.md`](pack_update.md)), and it can be done in a running world.

The building generator's mods (worldedit-gtnh, GuideNH, StructureLib, Schematica) need no new
jar. Their check is [`building_mods_check.md`](building_mods_check.md), a separate sitting.

**When.** In one go, about 1.5 hours, with Play closed until part E. From part B until part E
or F the Lab's mods differ from Play's, and the other Lab checks of checklist section 3 compare
the Lab with recipe data made from Play: run none of them in between.

**Why the old jar must go.** Two versions of one mod in a `mods` folder stop the game from
starting. **Why the sync entries come first.** `sync_instances.py` makes the Lab's mods equal to
Play's: without the entries of step 3 the next sync deletes the new jars and puts Play's old
ones back (D93 (5)).

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
   Worked: an `ok` line for each of the six new jars, no `FAILED` line. The files you already
   had say `(already here)`. At the end, "NOT into Play yet (D365)" lists the two GT-Not-Leisure
   jars and "NOT into Play yet (D373)" lists the other four, each with "(replaces ...)". If a
   line says `FAILED`, run it again; if it fails twice, stop and send the output.

## B. Prepare the Lab (10 minutes; Prism and every game window closed)

3. Tell the sync to leave these jars alone. Open your settings file:
   ```powershell
   notepad "$gh\config\gos.toml"
   ```
   Find the line `[sync.lab]`. If it is not there, add these two lines at the end of the file.
   If it is there, keep what its `exclude` list already has and add the six entries:
   ```toml
   [sync.lab]
   exclude = ["mods/sciencenotleisure-*", "mods/gtnllib-*", "mods/TwistSpaceTechnology-*", "mods/OTHTechnology-*", "mods/programmablehatches-*", "mods/plannh-*"]
   ```
   Save and close Notepad. (Phase 10's Lab files in `config\worldedit\schematics` and
   `config\guidenh` are already left alone by default, D380: they need no entry here.)
4. Keep a copy of the Lab world, so a failed test costs nothing:
   ```powershell
   Copy-Item "$lab\saves\lab-ops" "$env:USERPROFILE\Downloads\lab-ops-before-addon-test" -Recurse
   ```
5. Take the four old jars out of the Lab and put the six new ones in:
   ```powershell
   Remove-Item "$lab\mods\TwistSpaceTechnology-0.8.0.jar", "$lab\mods\OTHTechnology-2.2.1.1.jar", "$lab\mods\programmablehatches-0.2.1p0.jar", "$lab\mods\plannh-0.2.0.jar"
   Copy-Item "$dl\sciencenotleisure-0.2.7.jar", "$dl\gtnllib-1.1.8.jar", "$dl\TwistSpaceTechnology-0.8.2.jar", "$dl\OTHTechnology-2.2.1.3.jar", "$dl\programmablehatches-0.2.1p9.jar", "$dl\plannh-0.2.6.jar" "$lab\mods\"
   Get-ChildItem "$lab\mods" | Where-Object Name -match 'twist|othtech|programmable|plannh|notleisure|gtnllib' | Select-Object Name
   ```
   Worked: exactly six names are listed: `gtnllib-1.1.8.jar`, `OTHTechnology-2.2.1.3.jar`,
   `plannh-0.2.6.jar`, `programmablehatches-0.2.1p9.jar`, `sciencenotleisure-0.2.7.jar`,
   `TwistSpaceTechnology-0.8.2.jar`. If an old jar is still listed, delete it before you go on.
6. Check that the sync will leave them alone (this writes nothing):
   ```powershell
   & $py "$app\scripts\sync_instances.py" --dry-run --only lab
   ```
   Worked: the line `excluded (never copied or deleted):` names the six new jars (and Play's
   four old ones), and no `delete` or `copy` line names a `TwistSpaceTechnology`,
   `OTHTechnology`, `programmablehatches`, `plannh`, `sciencenotleisure` or `gtnllib` jar. If one
   does, fix step 3 first.

## C. The test (about 1 hour, Lab only)

7. Launch **GTNH-Lab** in Prism and wait for the main menu. If the game closes or shows a crash
   screen here, go to part D.
8. On the main menu click **Mods**. Worked: Twist Space Technology says **0.8.2**, 123Technology
   **2.2.1.3**, Programmable Hatches **0.2.1p9**, PlanNH **0.2.6**, and GT Not Leisure and
   GTNLLib (**1.1.8**) are in the list.
9. Load the world `lab-ops`. If it asks about missing blocks or items, answer **No** (don't load),
   and send a screenshot of the list: 123Technology's new id should have been converted, and a
   world with missing blocks would lose them. Worked: it loads without that question.
10. Press E and type `@twist` in NEI's search box, then `@123`, then `@leisure`, then
    `@programmable`. Worked: each mod's items show, and their recipes open.
11. **TST 0.8.2:** build one TST multiblock (any; the one you built on 2026-11-05 is fine), let
    it form and run one recipe to the end.
12. **123Technology 2.2.1.3:** build one 123Technology multiblock and run one recipe. If the one
    you built with 2.2.1.1 is still in `lab-ops`, check it is still there and still forms first.
13. **GT-Not-Leisure 0.2.7:** build one GTNL multiblock, let it form and run one recipe.
14. **Programmable Hatches 0.2.1p9 and #323** (D93 (3)): build a GTNL large centrifuge with a
    Programmable Hatches dual input hatch, and run a recipe through it. Worked: no crash, and the
    machine still works after you leave the world and come back.
15. **PlanNH 0.2.6:** press F8. Open a chart you made with 0.2.0. Worked: it opens and looks as
    you left it. Add one recipe to it, close the planner, open it again: the change is there.
16. **Memory** (GTNL #575, a leak with TST, is still open): press F3 and note the memory line
    now, after 10 minutes and after 20 to 30 minutes in the world. Worked: it goes up and down
    but does not climb steadily toward the limit.
17. Quit the game from the menu.

**Paste back** (in the PR or to the PM): which multiblock and recipe for steps 11, 12 and 13;
the result of step 14; the three memory readings; anything odd you saw. Then answer 5.2.12
(GT-Not-Leisure) and 5.2.13 (the four updates) in the checklist.

## D. If the game crashes or a step fails

18. Find the newest crash report and the log:
    ```powershell
    Get-ChildItem "$lab\crash-reports" | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
    "$lab\logs\fml-client-latest.log"
    ```
    Send both files (the crash report may not exist if the game only froze: then send the log).
    They hold no keys.
19. Take the new jars out **one at a time**, in this order, and launch the Lab after each. If
    the crash report's first lines name one of the mods (search it for `twist`, `othtech`,
    `proghatches`, `plannh`, `leisure`), start with that one. Two rules: 123Technology 2.2.1.3
    needs TST, so if TST comes out, 123Technology goes back to 2.2.1.1 with it. And if you take
    out GTNLLib, GT-Not-Leisure comes out with it.
    1. PlanNH back to 0.2.0:
       ```powershell
       Remove-Item "$lab\mods\plannh-0.2.6.jar"
       Copy-Item "$dl\plannh-0.2.0.jar" "$lab\mods\"
       ```
    2. Programmable Hatches back to 0.2.1p0:
       ```powershell
       Remove-Item "$lab\mods\programmablehatches-0.2.1p9.jar"
       Copy-Item "$dl\programmablehatches-0.2.1p0.jar" "$lab\mods\"
       ```
    3. 123Technology back to 2.2.1.1:
       ```powershell
       Remove-Item "$lab\mods\OTHTechnology-2.2.1.3.jar"
       Copy-Item "$dl\OTHTechnology-2.2.1.1.jar" "$lab\mods\"
       ```
    4. TST back to 0.8.0 (123Technology must be on 2.2.1.1 by now, step 3):
       ```powershell
       Remove-Item "$lab\mods\TwistSpaceTechnology-0.8.2.jar"
       Copy-Item "$dl\TwistSpaceTechnology-0.8.0.jar" "$lab\mods\"
       ```
    5. GT-Not-Leisure out (both jars):
       ```powershell
       Remove-Item "$lab\mods\sciencenotleisure-0.2.7.jar", "$lab\mods\gtnllib-1.1.8.jar"
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
be added to a running world. These lines are yours to paste. They are step 5's lines with
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
24. Yes to 5.2.13 for 123Technology 2.2.1.3 (only together with TST 0.8.2, step 23):
    ```powershell
    Remove-Item "$play\mods\OTHTechnology-2.2.1.1.jar"
    Copy-Item "$dl\OTHTechnology-2.2.1.3.jar" "$play\mods\"
    ```
25. Yes to 5.2.13 for Programmable Hatches 0.2.1p9:
    ```powershell
    Remove-Item "$play\mods\programmablehatches-0.2.1p0.jar"
    Copy-Item "$dl\programmablehatches-0.2.1p9.jar" "$play\mods\"
    ```
26. Yes to 5.2.13 for PlanNH 0.2.6:
    ```powershell
    Remove-Item "$play\mods\plannh-0.2.0.jar"
    Copy-Item "$dl\plannh-0.2.6.jar" "$play\mods\"
    ```
27. Yes to 5.2.12 for GT-Not-Leisure:
    ```powershell
    Copy-Item "$dl\sciencenotleisure-0.2.7.jar", "$dl\gtnllib-1.1.8.jar" "$play\mods\"
    ```
28. Check Play has one jar per mod:
    ```powershell
    Get-ChildItem "$play\mods" | Where-Object Name -match 'twist|othtech|programmable|plannh|notleisure|gtnllib' | Select-Object Name
    ```
    Worked: one `TwistSpaceTechnology`, one `OTHTechnology`, one `programmablehatches` and one
    `plannh` jar, in the versions you chose, plus the two GT-Not-Leisure jars if you said yes.
29. Launch **GTNH-Play** to the main menu, click **Mods** and check the versions, then quit. Do
    not create a world. If GOS asks "Mods changed. Export now?", answer **Export now**: the
    recipe data is then rebuilt from the new jars ([`addon_watcher.md`](addon_watcher.md)).
30. Open `gos.toml` again (step 3) and take out the entries for the jars Play now has. Then let
    the sync make the Lab and Export equal to Play:
    ```powershell
    & $py "$app\scripts\sync_instances.py"
    ```
    Worked: "Refused" is never printed.

## F. A no, or not yet: put the Lab back

31. Open `gos.toml` (step 3), take out the entries for the jars that are **not** going into
    Play, save, and run the sync:
    ```powershell
    & $py "$app\scripts\sync_instances.py" --only lab
    ```
    It deletes those new jars from the Lab and copies Play's versions back.
32. If `lab-ops` was opened with a jar that is now gone, put the world copy back (step 21).

**Rolling Play back later.** The old jars stay in `Downloads\GTNH-2.9.0`, and
`fetch_play_files.ps1` still fetches and checks them. With Prism closed, do steps 23 to 26 the
other way round (remove the new jar, copy the old one in). What a world loses when you do:
- **TST:** a world saved with TST 0.8.2 that holds one of its newer machines (Bottled Chaos,
  the Mega Digester) loses those machines under 0.8.0.
- **123Technology:** a world saved with 2.2.1.3 loses all of 123Technology's blocks and items
  under 2.2.1.1, because of the new id. Don't roll 123Technology back in a survival world.

## G. What waits

`& $gos pack watch` reports each of these on every run, with the line "newest release ... isn't
in the installed jar's name: a newer release? Check it in Lab before Play". That line is
expected until the addon is pinned here. Nothing to do on your side until then.

- **worldedit-gtnh 1.0.2** (2026-11-21): stays out for now (D381). It is a rewrite, built
  against newer CodeChickenCore, Carpenter's Blocks and NEI than 2.9.0 or 2.9.1 ship.
  - **Its EndlessIDs support doesn't change anything for GOS.** GOS's shell files name every
    block in `BlockMapping`, which v0.0.10 already reads, and 1.0.2's new tags only matter
    for block ids above 65,535.
  - **What it would change:** blocks with meta above 15 could be pasted (#30, closed since
    1.0.0), so themes could use more blocks; big pastes are spread over several game ticks
    instead of freezing the game; and a few copy and paste bugs are fixed.
  - **What it would cost:** GOS's paste test has to be ported to the new WorldEdit first, its
    schematic reader has to learn the new tags, and the meta limit only lifts after a Lab test.
  - It comes back when a stable GTNH ships the mods it is built against.
- **GTNH 2.9.1:** waits for a TST release that loads on 2.9.1 (#1430, above). PlanNH 0.3.0 is
  the 2.9.1 build of PlanNH, and GT-Not-Leisure's development branch already builds against
  2.9.1. When TST's fix is out, the addon list is checked again for 2.9.1.
