# Schematic round trip in the Lab (WO-10.4)

WO-10.4's acceptance, on Clayton's PC: **paste → `//copy` → identical blocks, including IDs > 4095; meta > 15 behaviour documented** (plan 8 row 10.4, V16). Claude Code has checked the format against worldedit-gtnh v0.0.10's and Schematica 1.12.6-GTNH's source, round-tripped GOS's writer and reader as properties (sizes, 1 to 700 block types, 16-bit metas) and run the **schematic harness**: worldedit-gtnh v0.0.10 in the dev server pastes GOS-written shells and copies them back, and every cell comes back the same (D310). The dev runtime has no EndlessIDs, so its ids stay below 4096 and its metas below 16; only the Lab, with the real pack, checks ids above 4095, meta above 15 and the paste offset in game. About 20 minutes.

**Where commands run.** The `gos` commands run as **gos-dev** in its dev clone and write only gos-dev's `GOS_HOME`. The game part is in the **Lab** (you paste and copy by hand; GOS never writes into the Lab and never pastes). Nothing here touches Play. worldedit-gtnh isn't in the pack (plan 7.10): the Lab needs `worldedit-gtnh-v0.0.10.jar` in its `mods` (as Play has, D88).

## A. A test shell from the Lab's own blocks

1. As gos-dev, after WO-10.3's catalog check (runbook `palette_export.md`): write a palette with plain blocks from vanilla, GT and an addon. Pick blocks whose numeric id in the Lab is **above 4095** (EndlessIDs gives most modded blocks such ids; `F3` + `H` shows a block's id on its tooltip), and keep metas at 15 or less:

   ```powershell
   cd C:\GregtecOS-dev\GretecOS
   @"
   minecraft:stone@0
   minecraft:glass@0
   minecraft:wool@14
   gregtech:gt.blockcasings@2
   gregtech:gt.blockcasings5@0
   gregtech:gt.blockglass1@0
   bartworks:BW_GlasBlocks@0
   GoodGenerator:MAR_Casing@0
   "@ | Set-Content -Encoding ascii "$env:TEMP\roundtrip.txt"
   uv run gos schematic sample "$env:TEMP\roundtrip.txt" --out "$env:TEMP\gos_roundtrip.schematic"
   ```

2. **Expect** `wrote ...\gos_roundtrip.schematic (3x1x3, WEOffset (-1, 0, 1))`, then the notes (`8 block types: ids 1..8 in BlockMapping and SchematicaMapping, no AddBlocks`, `pre-rotated: paste as is, without //rotate`). Exit code 1 with `REFUSED` lines: a block the Lab world doesn't have, isn't in the catalog, has a tile entity or a meta above 15; change it and run again.
3. Copy `%TEMP%\gos_roundtrip.schematic` into `C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft\config\worldedit\schematics\` yourself (Explorer; without `--out`, `sample` writes to gos-dev's `GOS_HOME\data\schematics\shells\`).

## B. Paste, check the offset, copy

1. Open a **creative test world** in the Lab (a fresh flat world is best). Stand on a block you can find again (place a gold block and stand on it), facing south, with clear space around.
2. `//schem load gos_roundtrip`, then `//paste` (no `-a`, no `//rotate`).
3. **The paste offset (VERIFY).** GOS writes `WEOffset (-1, 0, 1)` for a 3-wide shell: its min corner (west, bottom, north) should be **1 block west of you, at your feet's level, 1 block south of you**. Press `F3` and look at the corners: the shell fills x from yours − 1 to yours + 1, y = your feet's y, z from yours + 1 to yours + 3. Note what you see if it isn't there.
4. Without moving: `//pos1` and `//pos2` on two opposite corners of the shell (or `//wand` and click them), then `//copy`, then `//schem save gos_roundtrip_copy`.

## C. Compare

1. As gos-dev (GOS only reads the Lab's file):

   ```powershell
   $lab = "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft\config\worldedit\schematics"
   uv run gos schematic compare "$env:TEMP\gos_roundtrip.schematic" "$lab\gos_roundtrip_copy.schematic"
   uv run gos schematic read "$lab\gos_roundtrip_copy.schematic"
   ```

2. **Expect** from `compare`: `offset: (-1, 0, 1) pasted, (-1, 0, 1) copied` and `identical: 3x1x3, 8 block states in every cell`, exit code 0. From `read`: `written by worldedit, AddBlocks low-first` when any id is above 255 (WorldEdit writes the Lab's own ids; GOS turns them into names through the file's `BlockMapping`), and each state of the palette once. A differing offset with identical blocks means you moved between paste and copy: copy again from the gold block. `DIFFERENT` lines are the criterion not met: send them back with the `read` output.
3. **Ids above 4095:** the `read` output doesn't show numeric ids (GOS never keys on them). To confirm the file holds some, run `uv run python -c "from gregtec.ingest import nbt; import sys; print(nbt.read(open(sys.argv[1],'rb').read())[1].get('BlockMapping'))" "$lab\gos_roundtrip_copy.schematic"`: ids above 4095 show as numbers above 4095 (or negative above 32767), and `AddBlocks2` exists in the file.

## D. Meta above 15 (worldedit-gtnh #30, V16)

GOS keeps meta > 15 blocks out of shells (`gos palette check` and `gos schematic sample` refuse them) until #30 is resolved. To document the behaviour on 2.9.0 (no GOS file needed):

1. In the same test world: `//pos1` / `//pos2` on one block, then `//set gregtech:gt.blockores:824` (a GT ore with meta 824, #30's example) and note the chat output.
2. Place a GT frame box of a material whose meta is above 15 from creative (any frame box: its meta is the GT material id), `//copy` it, `//schem save meta_test`, then `uv run gos schematic read "$lab\meta_test.schematic"`.
3. **Expect** (to record, not to pass): either `//set` errors as #30 reports, or it works; and `read` lists `gregtech:gt.blockframes@<meta above 15>` when WorldEdit saved the meta (its `AddData`). Send both outputs back; the reviewer records the behaviour in V16 and decides whether D309's meta rule can be relaxed.

Send back the `compare` and `read` outputs, what you saw for the offset, and section D's two outputs.
