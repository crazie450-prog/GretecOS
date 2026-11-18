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
A controller whose class name ends in `Legacy` is marked `legacy`: 2.9 keeps 60+ old
controllers as separate MTEs beside their new ones, mostly GT++ and bartworks machines (e.g.
`MTEIndustrialMixerLegacy`), not only the turbines, LCEs and boilers; those three also get a
family (`legacy turbine`, `legacy lce`, `legacy boiler`). A `legacy` line in a plan means the
planner chose the old controller: build the one the plan names, or ask for the plan to be
redesigned; the coverage report does not swap it.

## C. Capture an addon multi in the Lab (L1)

For each `needs capture` line:

1. In a **Lab** world, place the controller and build it with the StructureLib projector at the
   tier you want (`/sl_channel set <channel> <value>` first, as the plan says).
2. Capture the region around it:
   - **WorldEdit:** `//pos1`, `//pos2` around the whole structure, `//copy`, `//schem save <name>`.
   - **GuideNH region wand:** select the same box and export it.
3. Until WO-10.4's `.schematic` reader is merged, GOS reads only its own `gos-capture` JSON
   (format in `core/gregtec/build/capture.py`), and no tool writes that format yet. Keep the
   files from step 2 in `GOS_HOME\data\schematics\captures\`. They convert after WO-10.4 only
   if they kept their tile entities: a GT machine block's world meta is not the machine, which
   is in its tile entity's `mID`. WorldEdit's `//copy` + `//schem save` keeps them; check the
   GuideNH export does too before relying on it, else re-capture with WorldEdit.
4. **After WO-10.4** (blocked until then: nothing writes a `gos-capture` file today). Load a
   capture and check it:

```powershell
gos footprints capture C:\path\to\capture.json --dry-run   # reads and prints the size, cells, hatch cells
gos footprints capture C:\path\to\capture.json             # writes a lab-capture structure row
gos footprints coverage                                     # the multi now shows "capture"
```

A capture of a multi the extractor already has is refused (its footprint comes from the dump).

## D. Check base footprints against the game (L1 "cells match structure")

**After WO-10.4**, like C.4: it needs a capture file GOS can read.

Build a base multi in the Lab with the projector, capture it as in C, then:

```powershell
gos footprints compare C:\path\to\capture.json
```

Exit 0 and `every cell matches`, or the cells that differ (footprint block vs captured block).
Put any difference in the PR or an issue: it means the extractor's dump or a tier choice is off.
