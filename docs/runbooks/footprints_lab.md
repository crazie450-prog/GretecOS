# Footprints: the extractor dump, coverage and Lab captures (WO-10.2, L1)

Plan 7.10 "Footprint library", D305, D306. Everything here runs against `GOS_HOME/data/gos.sqlite`
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
Legacy turbines, LCEs and boilers are marked `legacy` (they are separate controllers in 2.9).

## C. Capture an addon multi in the Lab (L1)

For each `needs capture` line:

1. In a **Lab** world, place the controller and build it with the StructureLib projector at the
   tier you want (`/sl_channel set <channel> <value>` first, as the plan says).
2. Capture the region around it:
   - **WorldEdit:** `//pos1`, `//pos2` around the whole structure, `//copy`, `//schem save <name>`.
   - **GuideNH region wand:** select the same box and export it.
3. Until WO-10.4's `.schematic` reader is merged, GOS reads only its own `gos-capture` JSON
   (format in `core/gregtec/build/capture.py`). Keep the files from step 2 in
   `GOS_HOME\data\schematics\captures\`; once WO-10.4 lands they convert without re-capturing.
4. Load a capture and check it:

```powershell
gos footprints capture C:\path\to\capture.json --dry-run   # reads and prints the size, cells, hatch cells
gos footprints capture C:\path\to\capture.json             # writes a lab-capture structure row
gos footprints coverage                                     # the multi now shows "capture"
```

A capture of a multi the extractor already has is refused (its footprint comes from the dump).

## D. Check base footprints against the game (L1 "cells match structure")

Build a base multi in the Lab with the projector, capture it as in C, then:

```powershell
gos footprints compare C:\path\to\capture.json
```

Exit 0 and `every cell matches`, or the cells that differ (footprint block vs captured block).
Put any difference in the PR or an issue: it means the extractor's dump or a tier choice is off.
