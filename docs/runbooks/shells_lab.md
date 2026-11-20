# Decorative shells in the Lab (WO-10.7, L6; iterating first: WO-10.10)

WO-10.7's acceptance on Clayton's PC: **a Claude-designed shell is pasted in the Lab with no tile entities, and respects keep-outs and corridors** (plan 8 row 10.7, Lab test L6). Claude Code has checked everything up to the paste:

- the procedural shell around both benchmark layouts (G3's steel line, G4's benzene line) on a real server harness run, with both starter themes against a real client run's block catalog (`tests/integration/test_shell_harness.py`);
- the validator with property tests (`tests/unit/test_shell.py`): whatever it accepts or repairs has no cell in a keep-out, corridor, chase, doorway or anywhere else inside the layout below the roof (walls stand in the one-block ring outside it, D316), none outside the bounding volume, only palette states, none with a tile entity, at most `max_blocks`;
- the architect's path with a fake model through the real Claude Code CLI (`tests/unit/tools/test_shell_architect.py`): a good design accepted, a chest refused, a keep-out breach repaired, too many blocks refused, nothing written when refused;
- the `.schematic` through WO-10.4's writer and reader, the dashboard card (vitest, Playwright at 2560x1440 and 390x844).

What is left is the real model, your own catalog, and the paste. About 30 minutes; one or two deep-lane tasks of API credit (each stops at `[budget] task_max_usd`, 2.0 by default, the hard bound; a shell is estimated at about $0.10 with Sonnet and $0.40 to $1 with Opus, D318).

**Where it runs.** GOS runs on your account and writes only `GOS_HOME` (`data/build/shells/`, `data/schematics/shells/`). It reads the **Lab** world's `level.dat` (read-only) at export. **The paste is yours, in the Lab only: never paste a shell in Play.** GOS never pastes anything and never pastes machines.

## A. Before you start

1. WO-10.3's catalog is built (runbook `palette_export.md`) and `gos theme check` passes (runbook `themes.md`).
2. A saved plan for the line to house, e.g. from the Designer (`uv run gos plan list` shows its id, `pl-...`).
3. `[paths] lab_world` names the Lab world in `gos.toml` (or pass `--world`), and worldedit-gtnh v0.0.10 is installed in the Lab (runbook `schematic_roundtrip.md`).

## B. A procedural shell, no AI (the fallback)

```powershell
uv run gos shell design <plan-id> --theme th-foundry --line-type smelting
uv run gos shell show <sh-id>
```

**Expect** `build sh-... saved in ...`, then `Shell sh-...: accepted (procedural), N blocks of 20000, X x Z blocks and Y high, theme th-foundry@v1 ...`, the palette (role, `registry@meta`, name, count) and the notes. The dashboard's **Build** view lists it with its preview (turn it with the button), and the Designer's floor plan has a **Design a shell around it** button that does the same.

## C. A Claude-designed shell (L6)

With the core running and the key stored, ask by voice or in the dashboard: **"Design a foundry shell around my steel line with pillars every four blocks and a gable roof."**

1. **Expect** the deep lane to hand it to the architect; the answer names the build (`sh-...`), its size and blocks, and says whether blocks were removed from the machines' space. `uv run gos shell list` shows it with source `architect`.
2. If it says it was refused, the first sentence says why (for example a block with a tile entity): that is the gate working. Ask again, or use the procedural shell from B.

## C2. Iterate before you export (WO-10.10)

Before anything goes near the Lab, iterate in the Build view's workbench ([`shells.md`](shells.md)):

1. After C, say **"make the roof steeper"**, then **"taller, and no windows on the north wall"**. **Expect** each to make a new version of the same design (the Versions list grows; the deep lane's answer says the version and that "undo" goes back), not a new shell.
2. Turn the AI off (Settings, No-AI) and say **"no lights"**: GOS's own reviser makes the version ("No AI, so GOS's own reviser did it"). Say **"change the roof to gothic"**: it says it can't and nothing is written. Turn the AI on again.
3. Say **"undo"**: the version before is current again. Tick two versions and **Compare**: the materials table shows the change per block.
4. In the viewer, drag the layer slider down: the layer's materials change; the machines and keep-outs show as outlines inside the shell.
5. Make the version you want current; its id (`sh-...`) is what you export in D.

## D. Export and paste in the Lab

```powershell
uv run gos shell export <sh-id>
```

**Expect** `wrote ...\data\schematics\shells\<sh-id>.schematic (WxHxL, WEOffset (-1, 0, -6))` (the shell's north-west bottom corner relative to the cell you will stand in; the numbers depend on the layout) and the paste steps, which name that cell. A `REFUSED` line names the block your Lab world doesn't have: report it. `REFUSED ... the layout changed` means the plan was re-solved or the footprints changed since the shell was designed: design it again (B or C), so it keeps clear of today's keep-outs.

Then, **in the Lab only**:

1. Copy the `.schematic` (every `-part-N` file if it was split) into the Lab instance's `config\worldedit\schematics\`.
2. In the Lab world, build or mark the line's layout first (the floor plan, or the machines from the 3D guide), then stand in the cell the paste steps name: entrance e1, the hall corridor's way in on the layout's west edge, feet in its north-most floor-level cell (for the steel line, layout x 0, y 0, z 5), the corridor cell just inside where the shell's doorway will be. `gos shell show <sh-id>` prints it for your build. The layout's north is the world's north (F3: facing north is -Z). The shell is pre-rotated: never `//rotate` it.
3. `//schem load <sh-id>` then `//paste`.
4. Check, and note each result for the PR:
   - **No tile entities pasted**: no chest, machine or any block with a GUI appeared; `//count gregtech:gt.blockmachines` in the shell's region says 0.
   - **Keep-outs and corridors respected**: walk in through the doorway; every corridor and every machine's front, sides and top are open; nothing of the shell stands inside a machine's footprint or its ring.
   - The roof sits above the tallest machine and the chases; the doorway is open.
   - Select the pasted shell: with the wand (`//wand`), left-click its bottom north-west corner block and right-click its top south-east corner block (or `//hpos1`/`//hpos2` looking at them; `//paste -s` does it where your WorldEdit has that flag). Then go back to the same block you pasted from, `//copy`, `//schem save <sh-id>-copy`, then on the PC: `uv run gos schematic compare <the .schematic> <the copy>` says `identical`.
5. Build one machine from the 3D guide inside the shell (L6's second half): its controller forms.

## E. If something is off

- A block in a machine's space after the paste: the layout you built differs from GOS's (compare the floor plan); report the build id.
- The shell sits shifted: you stood on another block, or WEOffset differs on 2.9 (V16, runbook `schematic_roundtrip.md`); report the offset you saw.
- worldedit-gtnh errors setting a block: report the block (#30 is about meta above 15; GOS keeps those out of shells).
