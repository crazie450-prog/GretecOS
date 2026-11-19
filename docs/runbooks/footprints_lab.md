# Footprints: the extractor dump, coverage and Lab captures (WO-10.2, WO-10.9, L1)

Plan 7.10 "Footprint library", D305, D306, D350, D351. Everything here runs against `GOS_HOME/data/gos.sqlite`
or in the **Lab**. Nothing touches Play, and GOS never pastes a machine: footprints are what you
build by hand from the 3D guide (D35).

## A. Load the base pack's structures (once per pack version)

From gos-dev's clone (D125), then load the folder it wrote into your own DB from `GOS_APP`:

```powershell
$ProgressPreference = 'SilentlyContinue'
uv run gos structures extract --out C:\GregtecOS-dev\data\structures\2.9.0-new   # gos-dev; downloads several GB the first time
gos structures load C:\GregtecOS-dev\data\structures\2.9.0-new                   # Clayton, from GOS_APP
gos footprints list | Select-Object -Last 3
```

`list` ends with `N footprints (x4 facings)` and any structure rows that make none (should be none).

## B. Coverage: which multis need a Lab capture

```powershell
gos footprints coverage          # accepted/building/running plans and My base, then the catalog totals
gos footprints coverage --all    # also every catalog multi without a footprint
```

Each needed multi is `footprint` (the extractor's), `capture` (yours) or `needs capture`.
A controller whose class name ends in `Legacy` is marked `legacy`: 2.9 keeps 60+ old
controllers as separate MTEs beside their new ones, mostly GT++ and bartworks machines (e.g.
`MTEIndustrialMixerLegacy`), not only the turbines, LCEs and boilers; those three also get a
family (`legacy turbine`, `legacy lce`, `legacy boiler`). A `legacy` line in a plan means the
planner chose the old controller: build the one the plan names, or ask for the plan to be
redesigned; the coverage report does not swap it.

## C. Capture an addon multi in the Lab (L1)

**Where it runs.** The building and the WorldEdit commands are yours, in a **Lab** world only. The
`gos footprints` commands run in **your own account**, as in section B and the G10 PC steps, so the
capture lands in the DB your plans, layouts and guides read: `$gos` is
`C:\GregtecOS-app\current\.venv\Scripts\gos.exe`. GOS only reads the `.schematic` (copied to
memory, never held, never written) and never pastes. worldedit-gtnh isn't in the pack: the Lab
needs `worldedit-gtnh-v0.0.10.jar` in its `mods` (as Play has, D88).

For each `needs capture` line:

1. In a **Lab** world (a creative flat world is best), place the controller and build the multi
   with the StructureLib projector at the tier you want (`/sl_channel set <channel> <value>` first,
   as the plan says), with its hatches. Note which way the controller's front faces.
2. Stand clear of it, then `//pos1` and `//pos2` on two opposite corners of a box around the whole
   structure (or `//wand` and click them; one block of air around it is fine, air is dropped),
   then `//copy`, then `//schem save <name>`. WorldEdit's `//copy` + `//schem save` keeps the tile
   entities, and GOS needs them: a `gregtech:gt.blockmachines` block's world meta is not the
   machine, its tile entity's `mID` is. Keep only one multi in the box (other machines are
   placed hatches to GOS; a GT pipe or cable is refused).
3. Load it, a dry run first:

```powershell
$ProgressPreference = 'SilentlyContinue'
$gos = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
$lab = "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft\config\worldedit\schematics"
& $gos footprints capture "$lab\<name>.schematic" --dry-run   # the size, cells, hatch cells, facing
& $gos footprints capture "$lab\<name>.schematic"             # writes a lab-capture structure row
& $gos footprints coverage                                       # the multi now shows "capture"
```

   **Expect** `gregtech:gt.blockmachines@<mte> t1: WxHxL, N cells, K hatch cells (captured facing
   <facing>, worldedit-copy)`, then `loaded: ...|t1|north`. GOS finds the controller as the one GT
   tile entity whose MTE id is a multiblock in your DB's machine catalog, and its facing from the
   controller's saved `mFacing`; the hatches get their kind (`InputBus`, `Energy`, ...) from their
   class, or `Captured` for a class it doesn't know (an ME bus, a TecTech or GT++ hatch). Options,
   when GOS asks for them (D350, D351):
   - `--controller x,y,z`: the controller's cell in the box when none or several cells are a
     multiblock controller; `uv run gos schematic read` (as gos-dev) or `& $gos schematic read
     "$lab\<name>.schematic"` lists the GT tile entities with their cells and MTE ids.
   - `--facing north|east|south|west`: where the front faced, when the file saved no horizontal
     facing (a `--facing` that disagrees with the saved one is refused).
   - `--trigger N` and `--channel <channel>=<value>` (repeatable): the projector's trigger stack
     size and the `/sl_channel` values you built with; they name the variant.
   - `--source-world "<Lab world folder>"`: only for a `.schematic` without a `BlockMapping` (an old
     save); its ids are named from that world's `level.dat`.

   Refused, with the reason (exit 1): no controller, or two without `--controller`; no facing; a
   GT machine block without its tile entity (copy again with WorldEdit), a pipe, or tile entities
   that disagree with the blocks; a box over 64 a side; only air; a block whose id has no name;
   and a multi the extractor already has (its footprint comes from the dump). A `gos-capture`
   JSON file (format in `core/gregtec/build/capture.py`) still loads the same way: GOS tells the
   two kinds apart by their content, not the extension.

## D. Check base footprints against the game (L1 "cells match structure")

Build a base multi in the Lab with the projector (same tier channels as a footprint, e.g. the
smallest), capture it as in C.2, then:

```powershell
& $gos footprints compare "$lab\<name>.schematic"     # add --trigger/--channel as built
```

Exit 0 and `every cell matches`, or the cells that differ (footprint block vs captured block).
Put any difference in the PR or an issue: it means the extractor's dump or a tier choice is off.
