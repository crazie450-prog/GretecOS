# The building generator's mods: a Lab check (D379, D380)

Phase 10 (the building generator: shells, footprints, the 3D guide, the bake-off) uses four mods
in the game. On GTNH 2.9.0 **none of them needs a new jar** (checked 2026-11-22, D379):

| Mod | Where it comes from | Version in Lab and Play | Used by |
|---|---|---|---|
| worldedit-gtnh | you added it (D88) | `worldedit-v0.0.10.jar` | pasting shells, `//copy` captures (`schematic_roundtrip.md`, `shells_lab.md`, `footprints_lab.md`) |
| GuideNH | the pack | 1.3.55 | the in-game look of the 3D guide, `/guidenhc exportsite` (WO-10.1) |
| StructureLib | the pack | 1.4.47 | the projector that builds a multi's hologram (`footprints_lab.md`) |
| Schematica-GTNH | the pack, client only | 1.12.6-GTNH | the optional ghost of a shell (`shell_bakeoff.md` step 19) |

GregtecLink is GOS's own mod and goes in with `install_gregteclink.py`, as before.

So this sitting only checks that the four are there and work, and that the sync now leaves your
Phase 10 files in the Lab alone (D380). **Nothing here changes Play.** You only read Play's
`mods` folder in step 2. About 20 minutes, Lab only. Do it after the addon jars' sitting
([`addon_lab_test.md`](addon_lab_test.md)) is finished or undone, never in the middle of it.

**Why the sync matters.** `sync_instances.py` makes the Lab's `config` folder equal to Play's.
Before D380, the next sync deleted the shells you copied into the Lab's
`config\worldedit\schematics`, your `//schem save` captures, and GuideNH's GOS page and exports
under `config\guidenh`, because Play has none of them. Now those three folders are left alone by
default. The GOS that does this is the one `gos deploy` installs, so first deploy the commit
with D380.

## A. Before you start (5 minutes; Prism and every game window closed)

1. Open a normal PowerShell window (never "Run as administrator") and paste the "Every new
   window" block from [`quick_start.md`](../quick_start.md).
2. Check the four mods are in the Lab and in Play. This only reads:
   ```powershell
   foreach ($g in $lab, "C:\Games\PrismLauncher\instances\GTNH-Play\.minecraft") {
       "== $g"
       Get-ChildItem "$g\mods" -Recurse | Where-Object Name -match 'worldedit|guidenh|structurelib|schematica' | Select-Object -ExpandProperty Name
   }
   ```
   You should see, in each list, one worldedit jar with `0.0.10` in its name, one GuideNH jar with
   `1.3.55`, one StructureLib jar with `1.4.47` and one Schematica jar with `1.12.6`. If not
   (two jars of one mod, another version, or one missing), stop and send the output. Don't
   move a jar yourself.
3. Check the sync leaves the Phase 10 folders alone (this writes nothing):
   ```powershell
   & $py "$app\scripts\sync_instances.py" --dry-run --only lab
   ```
   You should see: if the Lab already has any of `config/worldedit/schematics`,
   `config/guidenh/DefaultGuide/gos` or `config/guidenh/site-export`, the line
   `excluded (never copied or deleted):` names it, and no `delete` line names a file in one of
   them. If a `delete` line does, GOS_APP is older than D380: run `gos deploy` first (Clayton
   only, from your own clone), then repeat this step.

## B. In the Lab (15 minutes)

4. Launch **GTNH-Lab** in Prism and load `lab-ops` (Creative).
5. **worldedit-gtnh:** type `//wand`. You should get a wooden axe and no red error. Then type
   `//schem list`. You should see the list of `.schematic` files in the Lab's
   `config\worldedit\schematics` (it may be empty). If either command is unknown, stop and send
   `$lab\logs\fml-client-latest.log`.
6. **StructureLib:** take the Multiblock Structure Hologram Projector from NEI (search
   `projector`), then right-click any GT multi controller with it (an Electric Blast Furnace
   is fine). You should see the multi's hologram appear around the controller.
7. **GuideNH:** follow WO-10.1's Lab steps 2 and 3 in
   [`WO-10.1_guide_spike.md`](../reviews/WO-10.1_guide_spike.md) ("Lab steps"): the GOS guide page,
   then `/guidenhc exportsite`. You should see chat print the folder it wrote under
   `config/guidenh/site-export/`. If you did this before, the page is already there: just open
   the guide once.
8. **Schematica (optional):** copy any shell you exported (`shell_bakeoff.md` step 16) into
   `$lab\schematics\`, open Schematica's load menu (its keys are under Options > Controls) and
   load it. You should see a ghost of the building. Keep its printer off.
9. Quit the game from the menu.
10. Run step 3 again. You should see the folders that steps 5 to 8 filled (at least
    `config/guidenh/site-export` and `config/guidenh/DefaultGuide/gos`) named under `excluded`,
    and no `delete` line for them.

**Paste back** (in the PR or to the PM): the output of step 2, what steps 5 to 8 showed, and the
`excluded` line of step 10.

## C. If something fails

11. Send the newest crash report (if there is one) and the log:
    ```powershell
    Get-ChildItem "$lab\crash-reports" | Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
    "$lab\logs\fml-client-latest.log"
    ```
    Don't swap any of the four jars to fix it: three are the pack's own, and worldedit-gtnh's
    newer builds are not ready for 2.9.0 (D379).

## D. Play

There is nothing to copy into Play: Play already has all four (step 2). The
building generator never puts anything into Play's world by itself: shells reach a world only
through `gos shell export` and your own paste, in the Lab (D35, plan 7.10).

If you later say yes to worldedit-gtnh 1.0.2 (D379's question), that becomes its own pinned jar
with a Lab test and a Play step in [`addon_lab_test.md`](addon_lab_test.md), and WO-10.4's paste
test runs again on it first.
