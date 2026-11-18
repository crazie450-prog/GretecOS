# Build a multi in the Lab from the 3D guide (WO-10.8, L6)

WO-10.8's acceptance on Clayton's PC: **you build one multiblock in the Lab from the guide alone**
(plan 8 row 10.8, Lab test L6). Claude Code has checked everything up to the building:

- the guide's data for every footprint and facing equals the footprint (cells, BOM, materials per
  layer, hatch slots), on the fixture and on a real server-harness DB
  (`tests/unit/test_build_guide_data.py`, `tests/integration/test_build_guide_harness.py`);
- the view (vitest, and Playwright at 2560x1440 and 390x844): turn, zoom, hover label with the
  registry name, layer slider, materials per layer equal to the footprint's counts, hatch markers,
  a checklist that survives a reload, contrast in all twelve themes;
- the GuideNH frame on its own origin: the frame can't reach the dashboard (`window.parent` is
  another origin, the core refuses `/api` with the guide's Origin), with a hand-made stand-in export.

What is left is your own footprints, your catalog, your GuideNH export and the building. About 30
minutes plus the build itself.

**Where it runs.** GOS reads its own DB and `GOS_HOME/data/guide-site`; it writes only the
checklist (in its own DB). **You build by hand, in the Lab.** GOS never pastes machines.

## A. Before you start

1. Footprints are in the DB: `uv run gos footprints list` shows the multi you want to build (runbook
   `footprints_lab.md`; an addon multi needs a Lab capture first).
2. Optional, for names and colours: the block catalog (runbook `palette_export.md`). Without it the
   guide shows registry names only and says so.
3. Optional, for the in-game look: your GuideNH export copied to `GOS_HOME\data\guide-site`
   (runbook of WO-10.1, `docs/reviews/WO-10.1_guide_spike.md`, Lab steps).

## B. Open the guide

1. Start the core as usual and open the dashboard on monitor 2.
2. **Build** view: pick the multi in **Multiblock**. Or open it from the Designer (a plan's machine
   node, **Build guide**) or from **My base** (a machine row, **Build guide**).
3. **Expect** the multi drawn in 3D, its size, `registry@meta` of the controller and an `mte:` chip.
   Drag to turn it, the wheel or **+ / −** to zoom, **Reset view** to go back.
4. Hover a block. **Expect** `<name> · <registry name>@<meta> · layer N · ... of the controller`, and
   for a hatch cell `takes Input Bus, ...`. Never a number on its own.
5. If the export is in place, the **In-game look** panel below shows GuideNH's page. Its address
   starts `http://guide.gos.localhost:8765/guide/<token>/`. Open the browser's developer tools on the
   frame and run `window.parent.fetch` : **expect** a SecurityError (the frame is isolated).

## C. Build it (L6)

1. Pick the front's facing in **Front faces** to match where the controller will look, and the coil
   or glass tier in its picker if the multi has one.
2. Move the **Layer** slider to 1. The **Layer 1: materials** panel lists what that layer takes.
   Place it in the Lab, then tick **Layer 1** in the **Checklist**.
3. Repeat for each layer. Cells outlined in the accent colour take a hatch (the **Hatch slots** panel
   lists the kinds); dashed cells stay air until a hatch goes in.
4. Reload the page or open the Build view on your phone: **expect** the ticks still there.
5. Place the hatches the multi needs, then the controller.

**Pass:** the controller forms (no structure error in its GUI or the Scanner), built from the guide
alone. **Record** in the PR or the issue: the multi, its facing and tier, whether every layer's
materials matched what you placed, and anything the guide got wrong (a hatch slot, a block, a
layer). If it fails, note the layer and position from the hover label.

When done, **Start the next one** clears the checklist for that multi.

## D. On the phone

Over the tailnet the guide works the same (tap a block instead of hovering). The in-game look is
not shown there: the tailnet name has no guide host, so the frame is PC only (D319).
