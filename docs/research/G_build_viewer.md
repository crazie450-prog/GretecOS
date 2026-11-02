# G — Build viewer: textures, web viewers, multiblock data, `.schematic` layout (GTNH 2.9.0)

Date: 2026-11-01. Method: read source at the tags the 2.9.0 pack ships where possible (GuideNH 1.3.55, StructureLib 1.4.47, NEI 2.8.171-GTNH, GT5-Unofficial 5.09.54.265, worldedit-gtnh v0.0.10), plus MrBruh/gtnh-process-line-solver at HEAD `03b6eb4` (2026-10-05) and five web-viewer repos. One local check was run: a Python round-trip of the recommended `.schematic` layout against a port of the worldedit-gtnh reader (`/home/claude/gscripts/schem_roundtrip.py`). Nothing was tested in-game. Builds on E §5 (schematic basics, projector, BlockRenderer6343) and A §4.2 (extractor summary); those points are not repeated here.

Legend: **CONFIRMED** = read in source or docs at the cited URL, or reproduced by running code. **UNCERTAIN** = inferred, or not testable here.

---

## 0. Recommendations
1. **Machinery viewer, high-fidelity path: GuideNH's own site exporter.** It is already in 2.9.0. It captures the real tessellated geometry and the GL texture atlas from a running client, and ships a three.js viewer with hover targets and a layer control. Feed it one GameScene per (controller, tier) through `<ImportStructureLib>`. A one-hour spike is needed to confirm the exported scene can be embedded in the dashboard (§1.2).
2. **Structure data (block + meta per cell, hatch slots, tier tables): MrBruh's gtnh-extractor** (Apache-2.0), bumped from 2.9.0-beta-3 to the 2.9.0 coordinates. Its texture manifest plus three.js previewer is the best GT-aware code to learn from (§1.3, §3.1).
3. **Fallback rendering:** NEI item-panel PNG dump, plus average-colour voxels (§1.5).
4. **Shell `.schematic`:**
   - Compact palette IDs 1..N (N ≤ 255), with `BlockMapping` and `SchematicaMapping` holding the same IDs.
   - No `AddBlocks`/`AddBlocks2`; `AddData` only when meta > 255.
   - Empty `TileEntities`/`Entities` lists, and never `gregtech:gt.blockmachines` (§4).

---

## 1. Rendering 1.7.10 modded blocks in a browser with real textures

### 1.1 The core problem
- 1.7.10 block geometry and textures come from code, not data files:
  - `Block.getIcon(side, meta)` → `IIcon` in the stitched atlas.
  - Custom renderers draw through the `Tessellator`, and tile-entity models through `ModelRenderer`.
- So a generic exporter has to capture what the client draws. That is exactly what GuideNH's scene exporter hooks (§1.2). CONFIRMED (GuideNH mixins `MixinTessellatorSceneExportCapture`, `MixinModelRendererSceneExportCapture`, and Angelica-compat `MixinVanillaModelMeshesSceneExportCapture`): https://github.com/GTNewHorizons/GuideNH/tree/1.3.55/src/main/java/com/hfstudio/guidenh/mixins
- Texture names exist only on the client: FML strips every `@SideOnly(CLIENT)` member on a dedicated server, `IIcon.getIconName()` included. CONFIRMED: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/docs/dataset-extraction/texture-resolution.md
- Measured at 2.9, server-side vs client-side extraction:

  | | Server | Client |
  |---|---|---|
  | Unresolved (block, meta) pairs | 261 | 12 |
  | Multiblocks with a texture gap | 208 of 296 | 13 of 296 |

  CONFIRMED: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/docs/dataset-extraction/client-dump-spike.md
- **Any exporter must run in a client JVM with a GL window** (it can be scripted, but not headless). CONFIRMED (same doc; `-PautoWorld=true` removes the click but not the window): https://github.com/MrBruh/gtnh-process-line-solver/blob/main/tools/gtnh-extractor/README.md

### 1.2 Option A (best fidelity): GuideNH 1.3.55 site export (in 2.9.0)
- **Commands.**
  - `/guidenhc exportsite [outDir] [--ponder-frames]` exports the loaded guides as a static site. CONFIRMED (`client/command/GuideNhClientCommand.java` @1.3.55): https://github.com/GTNewHorizons/GuideNH/blob/1.3.55/src/main/java/com/hfstudio/guidenh/client/command/GuideNhClientCommand.java
  - The command name is `guidenhc`. GuideNH 1.3.55 is tagged 2026-10-31. CONFIRMED (same source).
- **Capture and file format.**
  - Each scene's Tessellator output is recorded: positions, UVs, colour, brightness, normals.
  - The bound texture is read back with `GL11.glGetTexImage`.
  - Scenes are written as `scenes/*.scene.gz`, using AE2 GuideME's FlatBuffers `ExpScene` schema (`guideme.flatbuffers.scene.*`).
  - CONFIRMED (`GuideSiteSceneTessellatorCapture.java`, `GuideSiteSceneExporter.java`): https://github.com/GTNewHorizons/GuideNH/tree/1.3.55/src/main/java/com/hfstudio/guidenh/guide/siteexport/site
- **Bundled viewer.**
  - `model-viewer/modelViewer.js` plus a vendored bundle carrying the "Copyright 2010-2023 Three.js Authors" header.
  - Features: hover-target serialisation (`GuideSiteSceneHoverTargetSerializer`), a `visibleLayer` range control, a block-stats toggle and a "StructureLib hatches" toggle.
  - CONFIRMED: https://github.com/GTNewHorizons/GuideNH/tree/1.3.55/src/main/resources/assets/guidenh/siteexport/model-viewer
- **Scene input.**
  - `<ImportStructureLib controller="gregtech:gt.blockmachines:<meta>" tier=… channelName=… value=… facing=… rotation=… flip=… formed=…/>`
  - `<ImportStructure src=…>` accepts SNBT or binary NBT with `palette` + `blocks`.
  - GameScene has `allowLayerSlider` (default true).
  - CONFIRMED: https://github.com/GTNewHorizons/GuideNH/blob/1.3.55/wiki/GameScene.md
- **Licence:** GuideNH is LGPL-3.0; three.js is MIT. CONFIRMED (`LICENSE.txt`): https://github.com/GTNewHorizons/GuideNH
- **Why this beats a hand-built texture pipeline.** It captures whatever the client actually draws, so it handles generically:
  - GT `ITexture` layer stacks, tints and glow layers;
  - custom renderers (pipes, frames, glass);
  - Angelica-replaced meshes.
  - UNCERTAIN (inferred from the capture design, not tested).
- **Open points.**
  - **UNCERTAIN:** whether a private, local-only guide pack (Markdown pages with one GameScene per controller and tier) is enough to drive `exportsite`.
  - **UNCERTAIN:** whether the exported viewer's layer and tier state is precomputed per state or switchable at runtime. The viewer keys state as `layer=…|ponder=…`.
  - **UNCERTAIN:** atlas size and per-scene file size. The GTNH block atlas is large.
  - **UNCERTAIN:** what the hover payload contains. It is item-tooltip templates plus StructureLib metadata; check whether that is enough to "identify each block".

### 1.3 Option B: MrBruh's texture manifest + three.js previewer
- **Texture manifest (`TextureDumper`).**
  - For each MTE and each (block, meta) it records the ordered bottom-to-top `ITexture` layer stack per side and active state.
  - Each layer is `{icon, rgba, glow}` plus the asset path inside the mod jar.
  - Unknown shapes are recorded as `gaps` and never guessed.
  - Optional join keys: `recipe_map`, `tier`, `electric`.
  - CONFIRMED: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/tools/gtnh-extractor/README.md
- **Previewer.**
  - Fetches PNGs from the GTNH Nexus jar at preview time (textures are LGPL and never committed).
  - Pre-bakes each face's layer stack into one PNG (`bake.py`) and packs an atlas (`atlas.py`).
  - Draws one textured cube per block of the multiblock.
  - CONFIRMED (`previewer/textures.py`, `bake.py`, `atlas.py`): https://github.com/MrBruh/gtnh-process-line-solver/tree/main/src/gtnh_solver/previewer
- **Scope limit.** It covers GT casings and machines in multiblocks, not arbitrary decorative blocks from other mods. Pipes and cables are drawn as boxes or bars from two baked looks. CONFIRMED (same files).

### 1.4 GT-specific complications (any custom pipeline must handle these)
- **Machines share one block.** Every GT machine, hatch, cable and pipe is `gregtech:gt.blockmachines`; the machine's identity is `mID` in the tile entity. Per face it is an `ITexture` stack:
  - tier casing (tinted);
  - overlay(s);
  - `_ACTIVE` and `_GLOW` variants.

  A sided wrapper has 6 entries; multi wrappers and copied-block leaves also occur. CONFIRMED (MrBruh extractor README above; GT5U `GTMod.java` @5.09.54.265).
- **Active vs inactive:**
  - Controllers render differently when running.
  - GuideNH has `--gt-active-controller` for this.
  - Glow layers need an emissive material, or must be baked full-bright.
  - CONFIRMED (GuideNH `wiki/Structure-Export.md`; MrBruh `glow` flag).
- **Tint:** the RGBA multiply comes from material or dye. Spray paint changes both the casing multiply and, on hatches, the overlay sprite (`OVERLAY_PIPE_COLORS`). CONFIRMED (texture-resolution.md §Colorization).
- **UV quirks:**
  - GT flips U on the DOWN face (`GTRenderedTexture.getFlipped`).
  - `.extFacing()` overlays rotate only with the controller's wrench `IAlignment` rotation; the default is NORMAL.
  - CONFIRMED (texture-resolution.md §Overlay rotation).
- **Material-indexed blocks.** In 2.9, `gt.blockframes` (and `gt.sheetmetal`) metadata is a GT material ID: `MATERIAL_MASK` 0xFFF, plus `MTE_BIT` 0x1000 when the block carries a TE. So meta can exceed 15. CONFIRMED (texture-resolution.md; MrBruh `schematic/core.py` docstring): https://github.com/MrBruh/gtnh-process-line-solver/blob/main/src/gtnh_solver/schematic/core.py
- **Hand-made icon tables drift.** At 2.9, five transcribed casing-table entries no longer resolve. Ask the live sprite instead of keeping tables. CONFIRMED (client-dump-spike.md).
- **Animated textures:** GuideNH has `GuideSiteTextureAnimations`. CONFIRMED (file exists in `siteexport/site`). UNCERTAIN: how GT's animated sprites appear in the export.

### 1.5 Option C: fallbacks
- **NEI 2.8.171-GTNH (the pack's NEI): Options → Tools → Data Dumps → Item Panel.**
  - PNG mode writes `dumps/itempanel_icons/<display name>.png` at 16·2ⁿ px. Duplicate names get `_2`, `_3` suffixes.
  - CSV/JSON/NBT modes write `Item Name` (registry), `Item ID`, `Item meta`, `Has NBT` and `Display Name`, in the same `ItemPanels.itemPanel.getItems()` order.
  - CONFIRMED (`config/GuiItemIconDumper.java`, `config/ItemPanelDumper.java`, both present at tag 2.8.171-GTNH): https://github.com/GTNewHorizons/NotEnoughItems/tree/master/src/main/java/codechicken/nei/config
  - UNCERTAIN: joining PNG ↔ (registry, meta) needs both dumps over the same panel filter, matched by order and display name. It is fragile but workable.
  - GuideNH's site export also writes item icons (`GuideSiteItemIconExporter`). CONFIRMED (file exists).
- **Coloured voxels.**
  - Average each (block, meta) icon or texture to one colour and draw instanced cubes.
  - deepslate's `VoxelRenderer.ts` (MIT) is a reference implementation. CONFIRMED: https://github.com/misode/deepslate/tree/main/src/render
  - Good enough for shell previews and for "which layer am I on" while building.
- **Other 1.7.10 icon/atlas dumpers:** none verified in the time available. UNCERTAIN.

---

## 2. Open-source web viewers to reuse or learn from

| Viewer | Licence | Formats | Tech | Layer slicing / hover | Reuse for 1.7.10 GTNH |
|---|---|---|---|---|---|
| **MrBruh `gtnh_solver.previewer`** | Apache-2.0 | own scene JSON (from extractor dataset) | three.js 0.160.0 via unpkg import map; single self-contained HTML | layer slider; raycast hover name tags | **Best to learn from**: GT-aware texture baking, atlas, hover |
| **GuideNH site-export model viewer** | LGPL-3.0 (three.js MIT) | `.scene.gz` (GuideME FlatBuffers `ExpScene`) | three.js | `visibleLayer` control; hover targets | **Best fidelity**; data comes straight from the client |
| deepslate (misode) | MIT | NBT read/write; structures | own WebGL renderer (not three.js); `VoxelRenderer` | none built in | NBT library, culling and meshing ideas, voxel fallback |
| Lodestone (mattzh72; derived from deepslate) | MIT | `.litematic` | three.js `ThreeStructureRenderer`; chunked meshing, occlusion culling, transparency sorting | none built in | three.js meshing patterns |
| EngineHub SchematicWebViewer | MIT | formats supported by SchematicJS (modern, DataVersion-based) | needs a complete resource pack (blockstates + models) | none | UI reference only |
| prismarine-schematic | MIT | Sponge v1–v3 (read; writes v2); MCEdit (read only) | node lib | n/a | Its MCEdit reader maps numeric IDs through the **vanilla** legacy table and replaces unknown IDs with stone. It decodes `AddBlocks` with **even index = high nibble** |
| prismarine-viewer | MIT | vanilla worlds | three.js | n/a | vanilla only |

- CONFIRMED (licences from each repo's LICENSE/package.json; features from READMEs and source):
  - https://github.com/MrBruh/gtnh-process-line-solver (`previewer/__init__.py`, `previewer/html.py`)
  - https://github.com/GTNewHorizons/GuideNH
  - https://github.com/misode/deepslate
  - https://github.com/mattzh72/lodestone
  - https://github.com/EngineHub/SchematicWebViewer
  - https://github.com/PrismarineJS/prismarine-schematic
  - https://github.com/PrismarineJS/prismarine-viewer
- UNCERTAIN: prismarine-viewer's details were not read beyond its licence.
- Takeaway: every generic viewer resolves blocks through 1.13+ blockstate/model JSON or the vanilla legacy ID table. None can draw modded 1.7.10 blocks. Reuse their NBT parsing, meshing and UI, and take textures and geometry from §1. (Inference from the READMEs above.)
- No generic viewer has a "layer + hover-to-identify" UI matching the request. Both GTNH-specific viewers have layer control and hover. CONFIRMED (above).

---

## 3. Exact multiblock structures

### 3.1 MrBruh `gtnh-extractor` (tools/gtnh-extractor in gtnh-process-line-solver)
- **What it is.**
  - A Forge 1.7.10 dev mod.
  - It boots a headless dedicated server with GT5U + StructureLib, places every `IConstructable` controller facing NORTH, and sweeps the trigger stack size.
  - Each step runs a hint pass (`construct(trigger, true)` through a recording proxy) and a block pass (`construct(trigger, false)`, then a scan).
  - It emits one JSON per controller plus `_meta.json`.
  - CONFIRMED: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/tools/gtnh-extractor/README.md
- **Schema v3.** CONFIRMED (README + real files `data/multiblocks/gregtech_gt_blockmachines_1169.json`, `_998.json`): https://github.com/MrBruh/gtnh-process-line-solver/blob/main/data/multiblocks/README.md
  - `controller`: `registry_name`, `meta`, `display_name`, `source_class`, `facing_convention` (front = NORTH (−Z); `d=[dx,dy,dz]` are offsets from the controller).
  - `variants[]` (one per shape-changing stack size): `trigger_stack_size`, `channels`, `blocks[{d, block, meta}]`, `hints[{d, hint}]`, `hatch_slots[{d, kinds[], output_layer?}]`, `bbox`.
    - `kinds` holds GT `HatchElement` names (`Energy`, `InputBus`, …).
    - `output_layer` is set for distillation-tower layers.
  - `substitutions[channel]` holds `[{channel_value, block, meta}]` for tier-only channels: `coil`, `glass`, `pipe`, `casing`, `machine_casing`, …
  - `failures`.
  - Coils have their own stack-size sweep, keyed on blocks that implement `IHeatingCoil`. The EBF gets a 14-tier table.
- **Hatches.**
  - On GT ≥ 5.09.54.133, the block pass swaps `GTCreativeHatchSource.instance` for an empty copy, so the scan stays a casing shell.
  - Hatch-eligible cells keep their fallback casing in `blocks` and are listed in `hatch_slots`.
  - CONFIRMED (README).
- **Licence.** Code is Apache-2.0. The repo commits only *trimmed* example multiblocks (cut from 2.9.0-beta-2); full dumps are local-only. Textures are not vendored. CONFIRMED (`LICENSE`, `NOTICE`, `data/multiblocks/README.md`).
- **Pins.** 2.8.4 (GT 5.09.51.482, 208 controllers); 2.9.0-beta-2 (296); **2.9.0-beta-3 = current pin** (GT 5.09.54.133, StructureLib 1.4.42, CropsNH 2.0.114). CONFIRMED (`gtnh.lock.json`, README).
- **Is it 2.9.0-compatible?**
  - **Not pinned to 2.9.0 final** (GT 5.09.54.265, StructureLib 1.4.47; E §0). CONFIRMED.
  - The documented bump is: rewrite three coordinates in `dependencies.gradle` + `gtnh.lock.json`, and re-clone reference sources first, because API drift shows up as a bare `NoSuchMethodError`. CONFIRMED (README "Before the next bump").
  - UNCERTAIN: whether it runs unchanged on 5.09.54.265.
- **Known gaps.**
  - `meta.14003` and `meta.15755` fail on client-only classes.
  - Six controllers can come out with untranslated names (patched from `GregTech.lang`).
  - Addon multis appear only if their jars are on the dev runtime (A §4.2: UNCERTAIN).
  - CONFIRMED for the first two (README).
- **Cost.** JDK 25 plus auto-provisioned 8/17 toolchains, network access to the GTNH Nexus, a multi-GB first download, then about a minute per boot. CONFIRMED (README).

### 3.2 GuideNH structure tooling (in 2.9.0)
- **`/exportStructure structureLib [modid:name:meta] …`** writes orthographic PNGs plus `manifest.json`.
  - Options include `--tier`, `--channel coil=1-4`, `--layers each` (one PNG per Y layer), `--facing/--rotation/--flip`, `--gt-place-hatches` and `--gt-active-controller`.
  - Without a controller argument it discovers all StructureLib controllers.
  - CONFIRMED: https://github.com/GTNewHorizons/GuideNH/blob/1.3.55/wiki/Structure-Export.md
  - These are **images, not block lists**. They are useful as a zero-code "layer cards" fallback for the dashboard.
- **Region wand export** (`RegionWandExporter`) writes SNBT/NBT `{size, palette[{Name}], blocks[{pos, state, meta?, nbt?}], entities}`. This captures any in-world build, addon multis included, as block + meta per position. CONFIRMED: https://github.com/GTNewHorizons/GuideNH/blob/1.3.55/src/main/java/com/hfstudio/guidenh/guide/internal/item/RegionWandExporter.java

### 3.3 StructureLib developer tools (1.4.47, LGPL-3.0)
- `/sl_channel set <channel> <value>` | `get`/`unset <channel>` | `wipe`/`getall` configures the held projector's channels. This beats crafting NBT with `/give` (E §5.5 step 3).
- `/sl_registry_debug`.
- `-Dstructurelib.debug=true` turns on per-element check logging. It is also on automatically in a deobfuscated dev environment.
- The **instrument API** (`StructureLibAPI.enableInstrument(id)` → `StructureEvent.StructureElementVisitedEvent`) maps each cell to the element that visited it. This is how the extractor finds hatch slots.
- `ItemDebugConstructableTrigger` is the debug projector.
- CONFIRMED (`command/CommandConfigureChannels.java`, `command/CommandRegistryDebug.java`, `StructureLib.java`, `StructureEvent.java`, `item/`, `LICENSE.LESSER`): https://github.com/GTNewHorizons/StructureLib/tree/1.4.47/src/main/java/com/gtnewhorizon/structurelib

### 3.4 Recommendation
- Base-pack GT/GT++/TecTech multis: run gtnh-extractor (bumped to 2.9.0, ideally `runClient -PautoWorld=true` so the texture manifest is complete) to get `blocks` / `hatch_slots` / `substitutions`.
- Addon multis (TST, GTNL, 123Tech): build them with the projector in a creative copy, then use the GuideNH region wand or `//copy` + `//schem save`.
- The dashboard's layer-by-layer view slices `blocks` by `dy`. Hover info comes from (block, meta) → localized name, from the palette/NEI dump, plus `hatch_slots.kinds`.

---

## 4. `.schematic` NBT layout for worldedit-gtnh on 2.9.0 + EndlessIDs

### 4.1 What the reader (v0.0.10) requires
CONFIRMED (`SchematicReader.java` @v0.0.10): https://github.com/GTNewHorizons/worldedit-gtnh/blob/v0.0.10/src/main/java/com/sk89q/worldedit/extent/clipboard/io/SchematicReader.java
- The root named tag must be called **`Schematic`**. The file is gzipped NBT (WorldEdit's normal schematic I/O; UNCERTAIN, the stream wrapper was not re-read).
- Required:
  - `Blocks` (byte[]), `Data` (byte[]).
  - `Materials` = `"Alpha"`.
  - `Width`/`Height`/`Length` (short).
  - **`TileEntities` (list)**: a missing one throws "missing a TileEntities tag".
- Optional:
  - `WEOriginX/Y/Z`, `WEOffsetX/Y/Z` (int). From v0.0.10 each triple is read independently (#18). Paste origin = origin − offset.
  - `Entities`.
  - `AddBlocks`, `AddBlocks2`, `AddData`.
  - `BlockMapping`, `ItemMapping`.
- **Cell index** = `y*W*L + z*W + x`.
- **Block ID** = `Blocks & 0xFF`, then:
  - `AddBlocks` adds bits 8–11 (even index = **low** nibble);
  - `AddBlocks2` adds bits 12–15, same nibble order.
- Then the ID is remapped through `BlockMapping` and masked `& 0xFFFF`. **Meta** = `(Data & 0xFF) | (AddData & 0xFF) << 8`.
- **`BlockMapping` / `ItemMapping`** are compounds of `"modid:name"` → TAG_Short (source ID). The reader computes `Block.getIdFromBlock(Block.getBlockFromName(name))` for the current world and maps source → current.
  - Item remapping applies only inside TE compounds shaped like item stacks (`id`+`Count`+`Damage`, `Item`+`Count`+`Meta`, or an int `id`), plus Carpenter's-style door keys.
- **Version history.**
  - **v0.0.8** (2025-04-20; PR #14, commit `656b5d2`) added the mappings, `AddBlocks2` and `AddData`.
  - **v0.0.9** (2026-03-06; PR #23, commit `6586412`) fixed `AddBlocks2` decoding. It had been shifted `<<8`/`<<4` instead of `<<12`/`<<8`, so IDs > 4095 decoded wrongly on v0.0.8.
  - **v0.0.10** (2026-07-19).
  - CONFIRMED (git history): https://github.com/GTNewHorizons/worldedit-gtnh/commit/6586412f50c19050b41122f1fffcf23d6ca3bf5c · https://github.com/GTNewHorizons/worldedit-gtnh/commit/656b5d20a468fa91d8b1cde900b3b9c719ca1f54
- **Upstream writer behaviour.** It always writes `BlockMapping`/`ItemMapping` and the *world's* numeric IDs. It writes `AddData` only when meta > 15. CONFIRMED (`SchematicWriter.java` @v0.0.10).

### 4.2 Nibble-order conflict
`AddBlocks` packs two cells per byte, and readers disagree on which nibble an even index uses:

| Reader | Even index uses |
|---|---|
| worldedit-gtnh | low nibble |
| Schematica-GTNH (E §5.1) | high nibble |
| MrBruh's reader (measured on real Schematica saves) | high nibble |
| prismarine-schematic | high nibble |

CONFIRMED: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/src/gtnh_solver/schematic/read.py · https://github.com/PrismarineJS/prismarine-schematic/blob/master/lib/mceditSchematic.js

### 4.3 Layout to write (img2schem shells and machine footprints for preview or ghost)

| Tag | Type | Value |
|---|---|---|
| root name | — | `Schematic` (gzip) |
| `Width`,`Height`,`Length` | Short | X, Y, Z size |
| `Materials` | String | `Alpha` |
| `Blocks` | ByteArray[W·H·L] | **compact palette ID 1..N (N ≤ 255), 0 = air**, at `(y·L+z)·W+x` |
| `Data` | ByteArray | meta & 0xFF |
| `AddData` | ByteArray | meta >> 8. Only if some meta > 255 (e.g. 2.9 GT frame boxes, meta = material ID). worldedit-gtnh only |
| `TileEntities` | List[Compound] | **empty**, but must be present |
| `Entities` | List[Compound] | empty |
| `WEOriginX/Y/Z`, `WEOffsetX/Y/Z` | Int | origin 0,0,0. Offset = the min corner's position relative to the player at paste time; e.g. `(-⌊W/2⌋, 0, 1)` = centred in X, starting one block south of the player. Derived from writer `offset = min − origin` and reader `origin = min − offset`; UNCERTAIN in-game |
| `BlockMapping` | Compound name→Short | `minecraft:air`:0, plus each palette name → its compact ID |
| `ItemMapping` | Compound | empty (no TEs) |
| `SchematicaMapping` | Compound name→Short | same as `BlockMapping`, so Schematica-GTNH can ghost the same file |

- **Why compact IDs.**
  - A palette under 256 entries needs no `AddBlocks`/`AddBlocks2`. That sidesteps the nibble-order conflict and the v0.0.8 decode bug.
  - The mapping does the real ID resolution by name at paste time, so EndlessIDs IDs > 4095 come out right.
  - MrBruh's Schematica exporter already does this ("Block ids are ours to choose… allocated compactly from 1"). CONFIRMED (`schematic/core.py`).
- **Round-trip check.** CONFIRMED by running: `/home/claude/gscripts/schem_roundtrip.py` writes this layout with nbtlib 2.0.4 and decodes it with a Python port of the v0.0.10 reader. Fake target IDs 5123 and 30001 and meta 305 all came back exactly. UNCERTAIN: not pasted in-game.
- **Requirements and limits.**
  - Needs worldedit-gtnh ≥ v0.0.8 (mapping support), and EndlessIDs' `BaseBlock` mixin for current IDs > 4095 and meta > 15 (E §5.2). Prefer ≥ v0.0.9 anyway.
  - UNCERTAIN: which jar the player has installed; WorldEdit is not in the pack.
  - The current ID is cast to short and masked to 16 bits, so target IDs must be ≤ 65535. EndlessIDs' default 32k-ID range fits (E §5.2). UNCERTAIN if the pack config raises it.
- **Unknown names.** `Block.getBlockFromName` returns null for a name that isn't registered, and the outcome on paste is untested (probably ID −1 → 65535). UNCERTAIN. Validate the palette against the world's `level.dat` `FML.ItemData` before writing (E §5.2).
- **More than 255 distinct blocks:** emit `AddBlocks` in **WorldEdit order** (even = low nibble). Accept that Schematica will misread that file, or emit a separate Schematica file. (Design choice derived from §4.2.)
- **Schematica limits.** It keeps only 4 meta bits. For 2.9 GT frames it writes the low nibble of the material and shows the wrong frame material. CONFIRMED (MrBruh `core.py` docstring, measured on the maintainer's saves).

### 4.4 Why GT machine tile entities must not be pasted
- **Breaks the legit-build rule.**
  - Every GT machine, hatch, cable and pipe is block `gregtech:gt.blockmachines`.
  - World meta selects only the TE class: 0–3 and 12–15 → `BaseMetaTileEntity`, 4–11 → `BaseMetaPipeEntity`.
  - The machine itself is `mID` in the TE NBT, alongside `mStoredEnergy`, `mFacing`, `mOwnerName`, `mOwnerUuid` and `mColor`.
  - So pasting a TE creates a working, possibly charged machine for free.
  - CONFIRMED (GT5U @5.09.54.265): https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/GTMod.java · https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.54.265/src/main/java/gregtech/api/metatileentity/BaseMetaTileEntity.java
- **Wrong-machine risk.**
  - The mappings remap block IDs and item-stack-shaped compounds only, never `mID`.
  - A wrong Data nibble "rebuilds a cable as a machine".
  - CONFIRMED (reader code §4.1; MrBruh `core.py`).
  - UNCERTAIN: how stable `mID` is across pack versions.
- **Covers break.** GT stores covers by the world's numeric item ID, and Schematica never remaps TE NBT. A wrong ID loses or swaps the cover, or can crash the client drawing it. CONFIRMED (MrBruh `core.py`). GuideNH ships a `GtCoverIdNormalizer` for the same reason. CONFIRMED (file exists).
- **Ownership.** The paster's owner fields are copied. From GT 5.09.54.133, removing a controller throws if it has no owner. CONFIRMED as documented by the extractor author (README API table); not re-read in GT.
- **Rotation.** `//rotate` uses metadata-only rotation mappings, so TE `mFacing` likely stays wrong. UNCERTAIN (E §5.3).
- **Rule for img2schem.**
  - Export `Block.hasTileEntity(meta)` with the palette and allow only entries where it is false.
  - Hard-deny `gregtech:gt.blockmachines`.
  - GT casings, coils and glass are plain blocks and safe in shells. CONFIRMED (E §5.3).
  - UNCERTAIN: other mods' TE-backed decorative blocks (e.g. Carpenter's Blocks) would need TE NBT and `ItemMapping`. Exclude them initially.
- **Legit-build helpers that place nothing.**
  - The web viewer.
  - A Schematica ghost of the footprint (Schematica-GTNH ships in 2.9.0; E §0). Keep its printer off.
  - The hologram projector's hint mode (E §5.4).

---

## 5. Suggested architecture (2nd-monitor dashboard)
1. **Export pass, once per pack version, in a creative copy of the instance.**
   - gtnh-extractor `runClient -PautoWorld=true` produces the structures plus the texture manifest.
   - NEI item-panel CSV + PNG dump provides the palette, names and icons.
   - Optionally, GuideNH `exportsite` over a generated guide pack gives exact scenes.
2. **Dashboard (three.js).**
   - Load the structure JSON for the chosen (controller, tier). Apply `substitutions` for coils and glass.
   - Draw textured cubes from baked atlases (MrBruh approach), or embed the GuideNH scene viewer.
   - Layer slider = filter by `dy`. Raycast hover → (block, meta) → name, plus hatch kinds when the cell is in `hatch_slots`.
3. **Shells.** img2schem writes §4.3 files, with TE-free palettes only.

## 6. Open questions / spikes
- Which worldedit-gtnh version is installed (must be ≥ v0.0.8; v0.0.9+ preferred)? Is the EndlessIDs block-ID limit changed from the default?
- **GuideNH spike (about 1 h):**
  1. Write one local guide page with `<ImportStructureLib controller="gregtech:gt.blockmachines:1000" tier=…>`.
  2. Run `/guidenhc exportsite`.
  3. Check embedding, hover payload, layer behaviour and file sizes.
- **Extractor bump to 2.9.0:** GT 5.09.54.265, StructureLib 1.4.47. Does it build and dump cleanly?
- **In-game paste test of the §4.3 file:** a casing, a vanilla block, a block with ID > 4095, and a GT frame with meta > 255. Check worldedit-gtnh and Schematica-GTNH.

## 7. Sources (accessed 2026-11-01)
- worldedit-gtnh v0.0.10 reader/writer, commits #14/#23/#18: https://github.com/GTNewHorizons/worldedit-gtnh
- MrBruh gtnh-process-line-solver (Apache-2.0):
  - repo: https://github.com/MrBruh/gtnh-process-line-solver
  - extractor README: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/tools/gtnh-extractor/README.md
  - dataset README: https://github.com/MrBruh/gtnh-process-line-solver/blob/main/data/multiblocks/README.md
  - texture docs: https://github.com/MrBruh/gtnh-process-line-solver/tree/main/docs/dataset-extraction
  - previewer: https://github.com/MrBruh/gtnh-process-line-solver/tree/main/src/gtnh_solver/previewer
  - schematic exporter: https://github.com/MrBruh/gtnh-process-line-solver/tree/main/src/gtnh_solver/schematic
- GuideNH 1.3.55 (LGPL-3.0):
  - repo: https://github.com/GTNewHorizons/GuideNH
  - Structure-Export wiki: https://github.com/GTNewHorizons/GuideNH/blob/1.3.55/wiki/Structure-Export.md
  - GameScene wiki: https://github.com/GTNewHorizons/GuideNH/blob/1.3.55/wiki/GameScene.md
- StructureLib 1.4.47 (LGPL-3.0): https://github.com/GTNewHorizons/StructureLib
- NEI 2.8.171-GTNH: https://github.com/GTNewHorizons/NotEnoughItems
- GT5-Unofficial 5.09.54.265: https://github.com/GTNewHorizons/GT5-Unofficial
- BlockRenderer6343 (LGPL-3.0; no export feature found): https://github.com/GTNewHorizons/BlockRenderer6343
- Web viewers:
  - https://github.com/misode/deepslate
  - https://github.com/mattzh72/lodestone
  - https://github.com/EngineHub/SchematicWebViewer
  - https://github.com/PrismarineJS/prismarine-schematic
  - https://github.com/PrismarineJS/prismarine-viewer
