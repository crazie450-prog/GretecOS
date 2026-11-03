# A — Recipe, item/fluid and machine data extraction for Gregtec OS (GTNH 2.8.x / 2.9.x)

Research date: 2026-11-01. Method: I cloned each repository at its current HEAD (plus specific release tags) and read the source, READMEs, issues and release pages. I ran nothing in-game, so every runtime behaviour below is inferred from source unless it is marked as reported by someone else.

**Tags.** `CONFIRMED [Sx]` means I checked it against source Sx (URLs are in §8; file paths are given where it matters). `UNCERTAIN (why)` means it is inferred, reported second-hand, or not tested.

---

## 0. Ground facts that drive every choice

**0.1 Component versions per pack.** These come from DreamAssemblerXXL release manifests. CONFIRMED [S1]

| Pack | Manifest date | NotEnoughItems | GT5-Unofficial | StructureLib | CraftTweaker |
|---|---|---|---|---|---|
| 2.8.0 | 2025-09-27 | 2.7.91 | 5.09.51.470 | 1.4.23 | – |
| 2.8.4 | 2025-12-23 | 2.8.44 | 5.09.51.482 | 1.4.23 | 3.4.2 |
| 2.9.0-beta-3 | 2026-09-06 | 2.8.130 | 5.09.54.133 | 1.4.42 | – |
| 2.9.0-RC-2 | 2026-10-04 | 2.8.155 | 5.09.54.205 | 1.4.45 | – |
| **2.9.0** | 2026-10-31 | **2.8.171** | **5.09.54.265** | 1.4.47 | 3.4.8 |

- A "2.9.0 final" manifest and a "changelog from 2.8.4 to 2.9.0" were committed on 2026-10-31 (commit message "update 2.9.0 final"). CONFIRMED [S1][S2]. When I fetched the modpack's GitHub releases page, it still showed 2.9.0-beta-3 as "Latest". UNCERTAIN whether 2.9.0 is publicly downloadable or announced yet [S3].
- 2.9.0 removes NotEnoughIds and adds EndlessIDs. CONFIRMED [S2]. Numeric item/block IDs are therefore instance-specific and must never be used as keys. Use registry names (`modid:name` plus meta).
- **The GT recipe data model differs between 2.8 and 2.9.** CONFIRMED [S4][S4b]:
  - GT 5.09.51.482 (2.8.4): only `mChances` (output chances); `mOreDictAlt` without ore IDs; the recipe metadata storage is a private field with no getter; no `RecipeChangeAudit`.
  - GT 5.09.54.265 (2.9.0): `mInputChances`, `mOutputChances`, `mFluidInputChances`, `mFluidOutputChances`, `mAltFluidInputs`, `GTRecipe_WithAlt.mOreDictIds`, `getMetadataStorage()`, and `RecipeChangeAudit`.
  - Consequence: a GT-level exporter compiled against 2.9 fails on 2.8 with `NoSuchFieldError`, and the reverse is also true. You need either a build per pack generation or reflection.
- GT's `onServerStarting` removes all IC2 recipes and unifies the outputs of IC2 machine, furnace and crafting recipes in place. CONFIRMED [S6] (`gregtech/GTMod.java`). **Run exports after a world has loaded** (integrated server started), not from the main menu.
- Recipe metadata keys in 2.9 GT include `LOW_GRAVITY`, `CLEANROOM`, `COIL_HEAT`, `FUSION_THRESHOLD`, `RESEARCH_ITEM`, `OREDICT_INPUT`, `NANO_FORGE_TIER`, `CHEMPLANT_CASING_TIER`, `QFT_FOCUS_TIER`, `COMPRESSION_TIER` and about 40 more. CONFIRMED [S4c] (`GTRecipeConstants.java`). The builder copies the "alias" keys (COIL_HEAT, FUEL_VALUE, tier keys) into `mSpecialValue`. Without an alias, `mSpecialValue` gets -100 (low gravity), -200 (cleanroom) or -300 (both). CONFIRMED [S4d] (`GTRecipeBuilder.applyDefaultSpecialValues`). Single-block machines still test `mSpecialValue == -200/-300` for cleanroom. CONFIRMED [S4e] (`MTEBasicMachine.java`). An exporter should store both raw `mSpecialValue` and the full metadata map.
- `RecipeMap.ALL_RECIPE_MAPS` is a global registry. Every map built with `RecipeMapBuilder` is in it, including addon maps. CONFIRMED [S4f] (`gregtech/api/recipe/RecipeMap.java`).

---

## 1. Exporters assessed (Q1)

### 1.0 Summary

| Tool | Last activity | 2.8.x | 2.9.x | Output | GT detail | Coverage | Verdict |
|---|---|---|---|---|---|---|---|
| NERD (AX3Lino / Hermanoid) | 2025-07-15 | Reported broken; rebuild needed | Unknown, likely broken | 1 huge JSON | GT block likely never emitted (§1.1) | All NEI handlers, per-item query | Do not use |
| **NEI built-in "All Handler Recipes"** | **2026-11-01 (NEI 2.8.172)** | Not advisable | Drop-in (2.8.171→2.8.172) | JSONL | eut, duration, totalEU only | All NEI crafting handlers that support "all" | **Use as the catch-all** |
| NESQL (D-Cysteine) | 2024-05-04 | No (old GT class names) | No | HSQLDB/Postgres | Voltage, amps, cleanroom/low-gravity, heat text | Vanilla + 61 hard-coded GT5U maps | Do not use |
| NESQL fork (ShadowTheAge) | 2026-07-20 | Older commits only | Built vs 2.9.0-beta-2 | HSQLDB → C# → data.bin | specialValue + numeric metadata, catalysts, tooltips | All GT maps that have catalysts, crafting, Thaumcraft | Reference only (heavy) |
| RecEx | 2021-07-29 | No | No | Minified JSON | – | GT 5.09.33-era | Dead |
| GTNHItemDocExporter | 2026-09-24 | – | 2.9.0-beta-3 pre-release | JSON/CSV/MD | n/a (no recipes) | Items, fluids, oredict | Optional |
| GT `RecipeChangeAudit` (dev API) | 2026-08 → 2.9.0 | Absent | Present, no caller | JSONL | Full GT fields except metadata map | All GT maps + crafting + furnace | Good basis; needs a 1-call mod |
| Gregtorio `GTNHRecipeDump` | 2026-10 | No (2.9 API) | Yes (dev env) | gz JSON (documented) | Everything, incl. metadata | All GT maps + crafting + furnace + items | **Best field coverage**; needs packaging |
| GTNH Calc Oracle (gtnh-factory-flow) | 2026-06 | Partly (reflection) | Untested on final | One JSON | eut, duration, special, OC variants | GT maps + catalysts + tooltips + crafting/smelting/TC/bees/crops | Best for machine pass; has a circuit bug |
| Learwin NEI fork | 2026-10-28 | No | Based on NEI 2.8.170 | JSONL per handler | GT fields via reflection mapping | All NEI handlers | Superseded by NEI 2.8.172 |

### 1.1 NotEnoughRecipeDumps (NERD)
- **Maintenance.** Hermanoid wrote the original. AX3Lino's fork PR #3 ("forcefully output in .json, regardless of requested mode") was merged 2025-07-15. The latest tag is `v1.0.1-beta` on AX3Lino (2025-07-15). Hermanoid's only release is `v1.0-beta` (2024-01-15), which predates that fix. CONFIRMED [S15][S16].
- **Builds against** NEI 2.6.52-GTNH and GT5U 5.09.50.111, i.e. the GTNH 2.7 era. CONFIRMED [S15] (`dependencies.gradle`).
- **Known failures.** Issue #2 reports "RecipeDumper received an unexpected mode!" on GTNH 2.6.1, 2.8.0-beta2 and 2.8.4. One user fixed 2.8.4 by rebuilding with updated dependencies and Java 17. The maintainer said a run took about two hours and that he lacks time to continue. CONFIRMED [S17] (reports by others: UNCERTAIN for your setup).
- **How it works.** It iterates every stack in the NEI item panel (`ItemList.items`) and asks every crafting handler for recipes that make that item. Every recipe therefore appears once per output, so there are duplicates, and the run is slow. CONFIRMED [S15] (`RecipeDumper.java`).
- **Output.** A JSON file in `.minecraft/dumps/` shaped as `{version, queries:[{queryItem, handlers:[{id,name,tabName,recipes:[{generic:{ingredients,otherStacks,outItem}, gtDefault?}]}]}]}`. A `*_stacks` side file holds item details (numeric id, regName, unlocalized name, display name, NBT) and fluids. CONFIRMED [S15].
- **The GT-specific block is likely dead on GT ≥ 5.09.50.** The GT dumper registers for the handler id `"gregtech.nei.GT_NEI_DefaultHandler"`. NEI's default `getHandlerId()` returns the class name, which is now `gregtech.nei.GTNEIDefaultHandler`, and GT does not override it. The registry lookup therefore falls back to the generic dumper, and EU/t, duration and special value are lost. CONFIRMED in source [S15 `GTDefaultRecipeDumper.java`, `DumperRegistry.java`][S13 `IRecipeHandler.java`][S4 `GTNEIDefaultHandler.java`]; runtime effect UNCERTAIN (not run).
- **Other defects in the source.** CONFIRMED [S15]:
  - The GT fluid-display item is detected by the hard-coded numeric id `4356`, which is instance-specific and worse under EndlessIDs.
  - Stack counts of 0 and 1 are collapsed to "1", so non-consumed inputs such as programmed circuits look like consumed ×1.
  - Only the displayed permutation (`PositionedStack.item`) is written. Oredict alternatives and slot chances are lost.
- **Conflict with new NEI.** NERD registers the NEI option `tools.dump.recipes`, the same name as NEI 2.8.172's built-in dumper. NEI logs "Replacing option" and adds both rows. CONFIRMED [S13] (`OptionList.java`); visible behaviour UNCERTAIN.
- **How it is run.** Inventory → NEI options → Tools → Data Dumps → Recipes. CONFIRMED [S15].

### 1.2 NEI built-in recipe export (new, official)
- Added by PR #1100 "Add NEI Recipe Exporting" (Armisael5, merged by slprime on 2026-11-01) and released as **NEI 2.8.172-GTNH** on 2026-11-01. CONFIRMED [S10][S11].
- It is the **only commit** between 2.8.171-GTNH (what 2.9.0 ships) and 2.8.172-GTNH (the tag's parent is 2.8.171's commit `030795e`). CONFIRMED [S12] (git tags). Swapping the NEI jar 2.8.171 → 2.8.172 in a 2.9.0 instance therefore adds only the exporter. UNCERTAIN until tested in your instance. It is not yet in any pack manifest; daily.json still lists 2.8.171 [S1].
- **How it is run.**
  - Data Dumps → "All Handler Recipes" → **Combined** writes `.minecraft/dumps/recipes.jsonl`.
  - **Separate** writes `.minecraft/dumps/recipes/<handler display name>.jsonl` and deletes the existing files in that folder first.
  - The debug Handler Info widget (Shift+D per the PR) has an "Export" button that exports the currently filtered recipes of one handler.
  - `RecipeJsonWriter.dumpAll(boolean)` is `public static`, so a companion mod can call it.
  - CONFIRMED [S12] (`RecipeJsonWriter.java`, `config/RecipeDumper.java`, `DebugHandlerWidget.java`, `en_US.lang`).
- **Format.** One object per line: `{handler, ingredients:[…], results:[…], gt?:{eut, durationTicks, totalEU}}`. CONFIRMED [S12].
  - Each stack is NEI's raw stack NBT: `strId` (registry name), `Damage`, `Count`, optional `tag`, plus `name`.
  - Optional per-slot fields: `chance` (only if ≠ 10000, units 1/10000), `anyOf` (every accepted permutation, which is how oredict alternatives appear), and `returns` (crafting container item).
  - GT fluid-display stacks become `{neiFluidName:<fluid>, Count:<mB>}` [S13 `GTFluidStackStringifyHandler.java`, `FluidDisplayStackStringifyHandler.java`].
  - NBT numbers inside `tag` are strings with a type suffix (`"1B"`, `"3S"`, `"10L"`, `"1.0F"`) [S13 `util/NBTJson.java`].
  - Programmed circuits keep `Count: 0` because GT's non-consumed stacks have stack size 0 [S4 `GTNEIDefaultHandler.FixedPositionedStack.isNotConsumed`]. UNCERTAIN until observed.
- **Coverage.** Every registered crafting handler, loaded with NEI's "all" mode (`TemplateRecipeHandler.getAllRecipeHandler()` loads the handler's transfer-rect "see all" query). CONFIRMED [S14]. Handlers that do not support that query can export nothing; Learwin reports this for "weird" handlers such as gendustry with the same approach. UNCERTAIN [S20]. The PR author says there are "quirks with specific handlers". CONFIRMED [S11].
- **Missing fields.** GT special value (heat), cleanroom/low gravity, recipe metadata, amperage, oredict names, and recipes NEI hides. CONFIRMED [S12] (only `mEUt`/`mDuration` are read, via reflection).
- **Handler name.** For GT recipes `handler` should be the GT recipe-category id, e.g. `gt.recipe.assembler`. NEI resolves HandlerInfo by class name and then by overlay id, and GT registers HandlerInfos by category name. UNCERTAIN, inferred from source [S12 `GuiRecipeTab.getHandlerInfo`][S4 `NEIGTConfig.java`].
- **Thread and duration.** The export runs from the GUI click handler with no worker thread, so expect a client freeze. UNCERTAIN; I have no timing data.

### 1.3 NESQL Exporter (D-Cysteine) and WebNEI
- **Maintenance.** v0.5.2 is the last commit (2024-05-04); the README says "Still WIP". CONFIRMED [S18].
- **Builds against** GT5U 5.09.45.161 and NEI 2.5.27. It imports `gregtech.api.util.GT_Recipe`, `GT_Values` and `GT_LanguageManager`, which current GT no longer has (now `GTRecipe`, `GTValues`, `GTLanguageManager`, with no shims found). CONFIRMED [S18][S4]. So it will not load on 2.8 or 2.9 without porting. UNCERTAIN (not run, but class absence is definitive).
- **Output.** An HSQLDB database (PostgreSQL optional since 0.5.2), about 600 MB, about 60 minutes, about 150k icons. Run: two jars, a creative SP world, open the NEI list, `/nesql`, remove BugTorch; output in `.minecraft/nesql`. CONFIRMED [S18] (README).
- **Data.** NEI item list, oredict, fluids and containers, crafting and furnace, a **hard-coded enum of 61 core GT5U maps** (no GT++/BartWorks/GoodGenerator/TecTech or addon maps), Avaritia, quests and Thaumcraft aspects. CONFIRMED [S18] (`plugin/gregtech/util/RecipeMap.java`).
- **GT fields.** Voltage tier, voltage, amperage, duration, `requiresCleanroom`/`requiresLowGravity` (decoded from `mSpecialValue` -200/-100/-300), special items, mod owners, and an `additionalInfo` text with EBF/DTPF heat and coil, fusion start EU, and the NEI description. Chanced outputs are stored as probabilities; inputs as reverse-unified item groups. CONFIRMED [S18] (`GregTechRecipeFactory.java`, `GregTechRecipeProcessor.java`).
- **WebNEI.** OrderedSet86/webnei-solid (last commit 2024-10-25) consumes it via PostgreSQL. Its README points to a prebuilt **GTNH 2.2.8** database. CONFIRMED [S19].

### 1.4 ShadowTheAge's NESQL fork and community dumps
- **Maintenance.** `0.5.7-ShadowTheAge`, last commit 2026-07-20. "Update for GTNH 2.9.0" (2026-07-19) builds against NEI 2.8.111 and GT 5.09.54.20 (= 2.9.0-beta-2). CONFIRMED [S21].
- **Lineage.** RealSilverMoon added auto-detection of GT maps (2024-03); z5882852 added GTNH 2.7.0 support (2025-01); ShadowTheAge added catalysts, Thaumcraft recipes, `recipeSpecialValue` (2025-09), recipe metadata (2025-10) and per-line tooltips (2026-04). CONFIRMED [S21] (git log).
- **Coverage.** It iterates `ALL_RECIPE_MAPS` but **skips maps with no NEI catalysts**. Metadata is stored as numeric key/value pairs. CONFIRMED [S21] (`GregTechRecipeMap.java`, `GregTechRecipeMetadata.java`). It works against 2.9.0 final (GT .265). UNCERTAIN.
- **Pipeline.** Feeds ShadowTheAge's calculator: NESQL dump → C# tool → `data.bin` plus atlas. CONFIRMED [S22].
- **Published dumps.** `ShadowTheAge/gtnh-data` has branches 2.8.0-v4/v5/v6 and 2.9.0-v7. CONFIRMED [S23]. They are base pack only, not addons.
  - The data has **no license** ("derived from Minecraft and mod content"). The 2.9.0-v7 export is inferred to be 2.9.0-beta-2. CONFIRMED [S24].
  - GTNH Atlas records 221,760 recipes, 50,811 items and 1,476 fluids for that dump, and 190,080 recipes for 2.8.0-v5. CONFIRMED [S25].
  - Useful as a cross-check, not as your source (no TST/GTNL/123Tech/PH).

### 1.5 RecEx
- "No longer maintained", last commit 2021-07-29, GPL-3. Builds against GT 5.09.33.31 using `GT_Recipe.GT_Recipe_Map`, so it is incompatible with 2.8 and 2.9. Output: minimized JSON in `RecEx-Records/`, from a GUI on keybind K. CONFIRMED [S26]. Its viewer NotEnoughProduction is archived. CONFIRMED [S27].

### 1.6 GTNHItemDocExporter (HOMEFTW)
- v1.0.0 (2026-05-03), then pre-release `v1.0.0-gtnh2.9.0-beta-3` (2026-09-24). Baseline 2.9.0-beta-3 / NEI 2.8.130; CraftTweaker 3.4.8 is runtime-only. The README says **in-game export on beta-3 was not verified**. CONFIRMED [S28].
- **Output** in `<instance>/gtnh_item_doc_exporter/`. CONFIRMED [S28] (`export/*Entry.java`):
  - `item_index.json`: modId, registryId, meta, CraftTweaker expression, zh and en names, unlocalized name, isBlock, guid, nbtSummary.
  - `fluid_index.json`: name, CraftTweaker expression, names, temperature, density, viscosity, gaseous.
  - `ore_dictionary_index.json`: oreName, CraftTweaker expression, itemCount, items.
  - CSV and Markdown copies, plus `last_export.log`.
- **Trigger.** Automatically on world join once NEI's list is ready, or `/itemdoc export`. CONFIRMED [S28].
- **Limits.** No recipes, and NBT only as a summary. Optional, mainly for Chinese names and CraftTweaker expressions.

### 1.7 Other tools found (2025–2026), see also Q2
- **GT5U `RecipeChangeAudit`.** Added 2026-08-11 by boubou19 in PR #7743. CONFIRMED [S5]:
  - Present in GT 5.09.54.265 (2.9.0) and absent in 5.09.51.482 (2.8.4).
  - No caller in GT at HEAD, so it is a developer API: `RecipeChangeAudit.dump(dir, desc)` writes `<instance>/dumps/<dir>/recipes.jsonl`.
  - Captures every `ALL_RECIPE_MAPS` recipe with map, category, item and fluid IO (registry id, damage, amount, GT prefix/material, NBT), all four chance arrays, duration, eut, specialValue, the enabled/hidden/fake/canBeBuffered/needsEmptyOutput/nbtSensitive flags, fluid input alternatives, oredict alternatives and **numeric** oredict IDs. Also vanilla crafting (inputs via GT's mixin accessor), furnace, and GT's buffered crafting list.
  - Missing: metadata map, ore names, catalysts, tooltips, assembly-line research.
- **Gregtorio `tools/gtnh_dump`.** GPL-3.0. A small dev mod, `GTNHRecipeDump.java` (489 lines), run in a GT5U dev environment with `gradlew runServer`. CONFIRMED [S29]:
  - It runs at `FMLServerStartingEvent` with `after:*`, so after GT's unification, then shuts the server down unless `-Dgtnhdump.keepRunning=true`.
  - "Format 1" is documented in the README:
    - per map: amperage, backend, UI slot counts;
    - per recipe: eut, duration, special, category; inputs with `nc` (not consumed), `circuit`, `ore` name, `chance`, nbt; outputs with chance; fluids in/out; `fin_alt`; special_items; **`meta`** (every metadata key, e.g. `coil_heat`); `nei_desc`; hidden/fake/disabled; owners;
    - plus assembly-line research, shaped/shapeless crafting with ore names, furnace, and an `items` table (unlocalized name, English name, ore names, GT prefix/material/amount).
  - Its committed dump: GT 5.09.54.244-3 (2026-10-24) + NHCore 2.9.97, with 109,185 recipes in 168 maps, 40,798 crafting and 5,883 furnace recipes, 2.5 MB gzipped.
  - The dev environment lacks Railcraft/Forestry/Thaumcraft-gated recipes.
  - It uses 2.9-only fields, so it will not load on 2.8. CONFIRMED [S29][S4b]. Packaging it as a standalone mod for a real instance is untested by anyone I found. UNCERTAIN.
  - Its `nei_to_dump.py` merges an NEI dump into it by a key of sorted (id-or-ore, count, not-consumed) inputs, sorted outputs and fluids, without the numbers. CONFIRMED [S29b].
- **GTNH Calculation Oracle** (Samiracle64/gtnh-factory-flow, MIT). The Forge mod `gtnhcalcoracle` builds against GT 5.09.52.258; the repo HEAD is 2026-06-03. CONFIRMED [S30][S31]:
  - `-Dgtnh.oracle.autorun=true` exports after about 220 client ticks **at the main menu**, or on dedicated-server start, then exits the JVM. Output: `GTNH-Calc-Oracle/gtnh-oracle-<ts>.json` (`dev.gtnhplanner.oracle.v1`).
  - Contents: loadedMods; oredict; every `ALL_RECIPE_MAPS` map with catalysts (from `GregTechAPI.METATILEENTITIES` → `getAvailableRecipeMaps()`/`getRecipeMap()`, with each machine's tooltip lines); recipes with eut, duration, specialValue, IO, chances and non-consumed special items; per-tier OverclockCalculator variants with EBF-heat (hard-coded coil table), pyrolyse, cracker and perfect-OC profiles; crafting, smelting, Thaumcraft aspects, Forestry bees, IC2 crops.
  - A GitHub Actions pipeline polls stable and daily packs and runs headless under Xvfb.
  - **Bug:** `itemStack()` returns null when `stackSize <= 0`, so **programmed circuits and other non-consumed inputs are dropped** (line ~1707). CONFIRMED [S30].
  - Client autorun at the main menu runs before GT's server-start unification [S6]. The effect on crafting and IC2 data is UNCERTAIN.
  - Behaviour on 2.9.0 final is UNCERTAIN (built against .52).
- **Learwin's NEI fork** (`data-export` branch, d987255, 2026-10-28, based on NEI 2.8.170, LGPL-3.0). `/recipeid dump|fast-dump` writes per-handler JSONL into `.minecraft/recipeid/` plus `handlerformat/<Handler>.json` reflective field maps (GT exposes `mRecipe`: `mEUt`, `mDuration`, chances). CONFIRMED [S20][S32]. The author's in-code notes: the per-item dump took about 1 hour, about 400 MB, with up to 75% duplicates; the fast dump took about 2.5 minutes and about 100 MB and "may skip" odd handlers. UNCERTAIN (author's own measurements). Mostly superseded by NEI 2.8.172.
- **CraftTweaker (MineTweaker 3.4.x, shipped in 2.8.4 and 2.9.0 [S1]).** `/minetweaker recipes` (every crafting recipe as a ZenScript line, oredict as `<ore:…>`), `recipes furnace`, `names`, `liquids`, `oredict`, `mods` and `blocks` all log to `logs/minetweaker.log`. CONFIRMED [S33]. A zero-install fallback for crafting and oredict.
- **NEI built-in dumps.** The Item Panel dump (CSV/NBT/JSON/PNG: registry name, numeric id, meta, has-NBT, display name) and the Handler dump (recipe name, handler class, overlay id, mod, icon). CONFIRMED [S13] (`config/ItemPanelDumper.java`, `HandlerDumper.java`).
- **PlanNH** (sbancuz, active 2026-10-22) is an in-game planner, not an exporter. CONFIRMED [S34].

---

## 2. Q2 — Is there something newer to use instead?

Yes. Three 2025–2026 developments replace the tools you listed:
1. **NEI 2.8.172-GTNH's official JSONL recipe export** (2026-11-01). It is the catch-all for every NEI-visible handler, and it is a one-commit drop-in for 2.9.0. CONFIRMED [S10][S12].
2. **GT-level dumps that iterate `ALL_RECIPE_MAPS`:**
   - Gregtorio's format-1 dumper has the most complete fields.
   - GT's own `RecipeChangeAudit` is in 2.9.0, but has no caller.
   - The MIT Calc Oracle includes catalysts, tooltips and overclock variants.
   - CONFIRMED [S29][S5][S30].
3. **Community dumps** (ShadowTheAge/gtnh-data, gtnh-factory-flow datasets) exist but are **base pack only** and unlicensed (Shadow). Use them for regression checks, never as the source for your addon instance. CONFIRMED [S23][S24][S31].

None of these is a published dump of an instance with your addons. You must export from your own instance.

---

## 3. Q3 — Do the addons register recipes through GT RecipeMaps?

| Addon (default branch) | Pack target | Maps via GT `RecipeMapBuilder` | Custom NEI recipe handler | Notes |
|---|---|---|---|---|
| Twist Space Technology (`main`) | `setGtnhVersion("2.9.0-RC-2")` | **42** (`common/recipeMap/GTCMRecipe.java`) | None (only custom `RecipeMapFrontend` subclasses, `nei/NEI_Config.java` hides items) | Some maps are visual/fake (`OreProcessingVisualRecipeMap`, `NetherInterfaceVisualRecipeMap`, `*FakeRecipes`); 68 machine classes extend `GTCM_MultiMachineBase` |
| GT-Not-Leisure (`dev-290`; also `dev-28x`, `dev-27x`) | GT 5.09.54.265 / NEI 2.8.171 | **64** (`common/material/GTNLRecipeMaps.java`) | `NuclearReactorGuideHandler`, a text guide, not a recipe | Mixin swaps a GT map's NEI frontend (`MixinGTPPRecipeMaps`); fake maps such as `gtnl.recipe.cheat_ore_processing`, `cactus_wonder_fake`; structures stored as `.mb` text grids |
| 123Technology (`main`) | `gtnhVersion = "2.9.0-RC-2"` | **22** (`common/recipemap/Recipemaps.java`) | None found | `OTH_RecipeMapBackend` |
| Programmable Hatches (`290-daily-latest`; also `2.7.x`, `master`) | GT 5.09.54.264 / NEI 2.8.171 | **0** | None found | Its own item recipes go into standard GT maps (assembler, circuit assembler, mixer) via `GTValues.RA`, plus crafting |

All counts are CONFIRMED [S35a–d] by source at HEAD on 2026-11-01. The counts are unique `RecipeMapBuilder.of("…")` names, excluding commented-out code.

What this means:
- All addon recipe maps land in `RecipeMap.ALL_RECIPE_MAPS`. Exporters that iterate it (Gregtorio, Oracle, `RecipeChangeAudit`, and Shadow's fork if the map has a catalyst) catch them. Exporters with hard-coded lists (D-Cysteine NESQL, RecEx) do not. CONFIRMED [S4f][S18].
- GT creates an NEI handler for every recipe category whose map has `registerNEI` set (the default). Maps that opt out are invisible to NEI-level dumps: in GT these are `ic.recipe.recycler`, `gt.recipe.furnace`, `gt.recipe.efrblasting`, `gt.recipe.efrsmelting`, `gt.recipe.microwave` and `bw.recipe.BacteriaVat`; TST and GTNL use `disableRegisterNEI` once each. CONFIRMED [S4 `NEIGTConfig.registerHandlers`, `RecipeMaps.java`, `BartWorksRecipeMaps.java`].
- GT builds its NEI catalyst index from `GregTechAPI.METATILEENTITIES` → `RecipeMapWorkable.getAvailableRecipeMaps()`. Every GT-based multiblock is a `RecipeMapWorkable` (`MTEMultiBlockBase` → `IControllerWithOptionalFeatures` → `IRecipeLockable` → `RecipeMapWorkable`), so addon machines appear as catalysts automatically. CONFIRMED [S4 `NEIGTConfig.generateRecipeCatalystIndex`, interface files].
- Net effect: for these four addons a GT-level dump captures all their processing recipes, and an NEI-level dump adds nothing addon-specific except the GTNL text guide. CONFIRMED (source).
- **Caveat:** for visual/fake maps the real machine logic computes outputs in code, so the exported recipes are illustrative. UNCERTAIN how far each machine deviates.

---

## 4. Q4 — Per-machine facts (parallels, overclock, tiers, coils, structure)

**4.1 There is no data registry.**
- Parallels, speed bonus, EU modifiers, perfect-OC flags and tier-skip limits are set in each machine's Java code (`createProcessingLogic()`, `createOverclockCalculator()`, `getMaxParallelRecipes()`). CONFIRMED [S36][S37].
  - Example, EBF: machine heat = coil heat (`1 + 900·ordinal`, so Cupronickel = 1801 K) + `100 × (voltage tier − MV)`, with heat overclocking enabled. CONFIRMED [S36] (`MTEElectricBlastFurnace.java`, `HeatingCoilLevel.java`).
- `OverclockCalculator` constants: 900 K per heat discount, 1800 K per heat perfect-OC, discount exponent 0.95. Setters exist for speed boost, EU discount, max overclocks, tier skips, laser OC and amperage OC. CONFIRMED [S37].
- `MultiblockTooltipBuilder` helpers (`addStaticParallelInfo`, `addVoltageParallelInfo`, `addStaticSpeedInfo`, `addStaticEuEffInfo`, `addPerfectOCInfo`, `addMaxTierSkips`, `beginStructureBlock(w,h,l,hollow)`, `addCasingInfoMin`…) only append formatted, localized strings. Raw numbers are not kept. The tooltip is readable via `getInformation()`, `getStructureInformation()` and `getStructureHint()`. CONFIRMED [S38].
- Adoption of the structured parallel helpers (measured by grep): 27 of 260 GT5U files that build tooltips, and 0 in TST, GTNL and 123Tech. CONFIRMED (count at HEAD). So tooltip parsing will be mostly free text.

**4.2 Ways to capture the facts.**
1. **Machine pass at runtime.** Iterate `METATILEENTITIES` and record each machine's stack (registry/meta), class, owning mod, `getAvailableRecipeMaps()`, tooltip lines and structure lines, plus `supportsBatchMode`, `supportsVoidProtection`, `supportsInputSeparation` and `supportsSingleRecipeLocking`. The Oracle already does the stack/maps/tooltip part. CONFIRMED [S30][S4]. Then parse parallels, speed and EU% from the text into `machine_fact` rows with a confidence value. UNCERTAIN quality for addon machines.
2. **Structures from StructureLib.**
   - MrBruh's `gtnh-extractor` (Apache-2.0) boots a headless GT5U dev server and runs every controller's `construct()`. It records blocks (`registry@meta`), hologram hints, allowed hatch kinds per cell, channel and coil tier substitution tables, and size variants. It covers 296 controllers in about 10 minutes, pinned to 2.9.0-beta-3, with a 2.8.4 pin of 208 controllers. CONFIRMED [S39].
   - gtnh-base-planner turns this into a 244-multiblock catalog. CONFIRMED [S40].
   - Addon controllers would only appear if their jars are added to the dev runtime. UNCERTAIN.
3. **In-game capture.**
   - The StructureLib hologram projector, used while sneaking in creative, calls `construct(stack, false)` and places the whole structure; the stack size selects tier or length. CONFIRMED [S41] (`ConstructableUtility.java`). Then `//copy` and `//schem save` with WorldEdit.
   - GuideNH 1.3.55 (in 2.9.0) has `/guidenhc exportstructure` (PNG renders plus `manifest.json`; the wiki documents `/exportStructure structureLib …`) and a region wand that exports selections as structure SNBT. CONFIRMED [S42][S43][S1].
4. **Curated tables.** ShadowTheAge's `src/machines.ts` (MIT, about 1,900 lines) hand-codes overclockers and per-machine parallels and speeds for the base pack. CONFIRMED [S22b]. The Oracle's OC profiles are a simpler set [S30]. Neither covers your addons.
5. **Runtime probing.** Form a structure, fit a creative energy hatch, run a probe recipe and read the parallel count and duration. I found no existing tool that does this. UNCERTAIN.

---

## 5. Q5 — Recommended pipeline

**5.1 Topology.**
- Keep your play instance untouched.
- In Prism, use "Copy instance" to create **Gregtec-Export**: same `mods/`, `config/` and `scripts/`, its own saves, one creative void world.
- Put every exporter jar only in the copy.
- Re-sync mods, config and scripts from the play instance before each export.
- Exporting from your actual pack is required because GTNL mixins and addon versions change the data (§3).

**5.2 Exporters (2.9.x path).**
1. Replace the NEI jar 2.8.171 → **2.8.172** in the export copy, or wait for a pack build that ships it. Run Data Dumps → All Handler Recipes → **Separate**, and copy `dumps/recipes/` out before the next run because it is wiped.
2. **Gregtec companion mod** (one small Java mod, built with GTNH's ExampleMod buildscript, which needs JDK 25 to run Gradle per [S28][S29]). After the integrated server starts:
   - (a) GT-level dump in Gregtorio's **format-1** shape. Either reuse it (GPL-3.0) or re-implement the same fields.
   - (b) Machine pass (§4.2.1).
   - (c) Call `RecipeJsonWriter.dumpAll(true)` reflectively, if present, so that NEI step is automated too.
   - (d) Write a `manifest.json`: pack version, loaded mods with versions and jar names, GT and NEI versions, fingerprint, counts.
   - (e) Optionally exit.
   - Lower-effort alternatives: call GT's `RecipeChangeAudit.dump()` (fewer fields, numeric ore IDs), or use the Oracle jar after patching the stackSize ≤ 0 drop and its main-menu timing.
3. **Optional:**
   - Item names in zh and CraftTweaker expressions from GTNHItemDocExporter.
   - Structures, only for the machines you plan to build: hologram projector plus WorldEdit, or gtnh-extractor for the base pack.
   - Fallback for crafting and oredict without any mod: `/minetweaker recipes`, `oredict`, `names`, `liquids`.

**5.2b 2.8.x path.**
- Same topology, but:
  - the GT exporter must target the 5.09.51 API (`mChances` only, no input or fluid-output chances, no ore IDs, metadata via reflection);
  - there is no `RecipeChangeAudit`;
  - do not put NEI 2.8.172 into 2.8.4 (NEI 2.8.44; that is a 128-version jump). UNCERTAIN.
- For non-GT handlers, either rebuild NERD as in issue #2 [S17] or write a small NEI plugin using `Recipe.of(handler, i)`, which exists in NEI 2.8.44. CONFIRMED [S13b]. NEI 2.8.44 lacks the "all" loader, so you would call each handler's transfer-rect query yourself.

**5.3 Normalization rules** (Python, which fits your stated skills).
- **Item key** = (`registry_name`, `meta`, canonical-NBT hash). Never numeric IDs.
- **Fluids** by registry name. Map NEI fluid-display stacks (`neiFluidName`) to fluids.
- **Programmed circuit** = `gregtech:gt.integrated_circuit` with stack size 0. Store `circuit = meta` and `consumed = 0`. Treat any stack-size-0 input as non-consumed.
- **Chances** in basis points (10000 = 100%). EU/t and ticks raw. Amperage per map.
- **Voltage tier** = smallest *t* with 8·4^t ≥ eut ÷ amperage.
- **Decode `mSpecialValue` per map, but prefer metadata keys:** `coil_heat` (EBF/DTPF), `fusion_threshold`, and -100/-200/-300 for low gravity/cleanroom on single-block maps. Keep the raw value too.
- **Recipe signature** = sha256 of sorted (id-or-ore, amount, consumed) inputs + sorted outputs + sorted fluids, with no numbers, as in Gregtorio's `gt_key` [S29b]. Use it to (a) merge NEI rows into GT rows (GT wins for numbers, NEI adds non-GT handlers), and (b) diff consecutive exports.
- **Keep flags** hidden, fake, disabled and nbt_sensitive. Default the planner to `enabled && !fake`, but keep fake rows for display.
- **Parse NEI's typed-NBT strings** (`"5B"` etc.) when reading `tag`.

**5.4 SQLite schema sketch**
```sql
CREATE TABLE export_run(id INTEGER PRIMARY KEY, created_at TEXT, pack_version TEXT, gt_version TEXT,
  nei_version TEXT, fingerprint TEXT NOT NULL, sources_json TEXT, notes TEXT);
CREATE TABLE mod(run_id INT, modid TEXT, version TEXT, jar TEXT, jar_sha256 TEXT, PRIMARY KEY(run_id, modid));
CREATE TABLE item(id INTEGER PRIMARY KEY, registry_name TEXT NOT NULL, meta INT NOT NULL,
  nbt_hash TEXT NOT NULL DEFAULT '', nbt_json TEXT, mod_id TEXT, name_en TEXT, name_zh TEXT, unlocalized TEXT,
  gt_prefix TEXT, gt_material TEXT, gt_material_amount INT, UNIQUE(registry_name, meta, nbt_hash));
CREATE TABLE fluid(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, name_en TEXT,
  temperature INT, density INT, viscosity INT, gaseous INT);
CREATE TABLE ore(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE ore_member(ore_id INT, item_id INT, PRIMARY KEY(ore_id, item_id));
CREATE TABLE recipe_map(id INTEGER PRIMARY KEY, unloc_name TEXT UNIQUE, name_en TEXT, owner_mod TEXT,
  amperage INT, backend_class TEXT, nei_visible INT, max_item_in INT, max_item_out INT,
  max_fluid_in INT, max_fluid_out INT);
CREATE TABLE nei_handler(id INTEGER PRIMARY KEY, handler_name TEXT UNIQUE, name_en TEXT, mod TEXT,
  recipe_map_id INT);
CREATE TABLE recipe(id INTEGER PRIMARY KEY, run_id INT NOT NULL,
  source TEXT CHECK(source IN ('gt','crafting','furnace','nei','gt+nei')),
  recipe_map_id INT, nei_handler_id INT, category TEXT, eut INT, duration_ticks INT, amperage INT,
  voltage_tier INT, special_value INT, enabled INT, hidden INT, fake INT, needs_empty_output INT,
  nbt_sensitive INT, shaped_w INT, shaped_h INT, signature TEXT NOT NULL, owners TEXT);
CREATE TABLE recipe_input(recipe_id INT, slot INT, item_id INT, fluid_id INT, ore_id INT,
  amount INT NOT NULL, consumed INT NOT NULL DEFAULT 1, chance_bp INT NOT NULL DEFAULT 10000,
  circuit INT, PRIMARY KEY(recipe_id, slot));
CREATE TABLE recipe_input_alt(recipe_id INT, slot INT, item_id INT, fluid_id INT);  -- oredict/anyOf/fin_alt
CREATE TABLE recipe_output(recipe_id INT, slot INT, item_id INT, fluid_id INT, amount INT NOT NULL,
  chance_bp INT NOT NULL DEFAULT 10000, PRIMARY KEY(recipe_id, slot));
CREATE TABLE recipe_meta(recipe_id INT, key TEXT, value_num REAL, value_text TEXT,
  PRIMARY KEY(recipe_id, key));            -- coil_heat, cleanroom, low_gravity, fusion_threshold, nei_desc...
CREATE TABLE assembly_line_research(output_item_id INT, research_item_id INT, time INT, voltage INT);
CREATE TABLE machine(id INTEGER PRIMARY KEY, mte_id INT UNIQUE, class_name TEXT, mod_id TEXT,
  item_id INT, name_en TEXT, is_multiblock INT, tier INT, tooltip TEXT, structure_tooltip TEXT);
CREATE TABLE machine_map(machine_id INT, recipe_map_id INT, priority INT, PRIMARY KEY(machine_id, recipe_map_id));
CREATE TABLE machine_fact(machine_id INT, key TEXT, value TEXT, unit TEXT,
  source TEXT CHECK(source IN ('tooltip','curated','probe','code')), confidence REAL, note TEXT);
CREATE TABLE structure(machine_id INT, variant TEXT, size_x INT, size_y INT, size_z INT,
  blocks_json TEXT, schematic_path TEXT, source TEXT);
CREATE INDEX ix_out_item ON recipe_output(item_id);  CREATE INDEX ix_out_fluid ON recipe_output(fluid_id);
CREATE INDEX ix_in_item ON recipe_input(item_id);    CREATE INDEX ix_in_fluid ON recipe_input(fluid_id);
CREATE INDEX ix_sig ON recipe(signature);
CREATE VIRTUAL TABLE name_fts USING fts5(kind, ref_id UNINDEXED, name_en, name_zh, registry_name);
```

**5.5 Detecting changes and re-exporting**
- **Where to look.** Prism's game root is `minecraft/` unless only `.minecraft/` exists. CONFIRMED [S44]. Prism disables a mod by appending `.disabled` to the file name. CONFIRMED [S45].
- **Pack version.** Read `S:ModPackVersion=` in `config/GTNewHorizons/dreamcraft.cfg` and `displayedModpackVersion=` in `config/DreamCoreMod.properties`. The pack builder writes both. CONFIRMED [S46][S47]. Trust jar hashes over this string.
  - *Correction 2026-11-03 (PR #4 source check):* the modpack repo's own `2.9.0` tag holds the placeholder `S:ModPackVersion=2.9.x`, which is also the coremod's default. Only the built Prism zips carry `2.9.0`. Treat `2.9.x` as unknown. Source: https://raw.githubusercontent.com/GTNewHorizons/GT-New-Horizons-Modpack/2.9.0/config/GTNewHorizons/dreamcraft.cfg
- **Fingerprint** = sha256 over sorted `relpath \t sha256` lines for:
  - every enabled `*.jar`/`*.zip` under `mods/`, recursively (covers `mods/1.7.10/`; whether FML scans that folder is UNCERTAIN, so hash it anyway);
  - `scripts/**/*.zs`;
  - `dreamcraft.cfg`;
  - a whitelist of recipe-affecting configs (GT, TST, GTNL, 123Tech files; exact names to be confirmed in your instance; UNCERTAIN);
  - `mmc-pack.json`.
  Cache the hash per path by (size, mtime) so a launch costs milliseconds.
- **Trigger.** Prism custom commands run pre-launch and post-exit with `INST_DIR`, `INST_MC_DIR`, `INST_NAME`, `INST_JAVA` and `INST_JAVA_ARGS` in the environment. CONFIRMED [S44]. Suggested flow:
  - The **pre-launch** command on the play instance computes the fingerprint and compares it with `export_run.fingerprint`. If it changed, it marks the database stale (Gregtec OS can warn: "recipes are out of date").
  - The **post-exit** command then syncs mods, config and scripts to the export copy and launches it with `-Dgregtec.autoexport=true`. The companion mod exports after world load and exits, and the Python normalizer loads a new `export_run` and prints a diff by signature (added, removed and changed recipes per mod).
  - Loading a world without input needs a small auto-world harness; MrBruh's extractor mentions one for its client run [S39]. Otherwise you click into the world once. UNCERTAIN.
  - Never run the two clients at once.

---

## 6. Open risks
1. NEI 2.8.172 is one day old and not yet in any pack. It has "quirks with specific handlers" and may skip handlers that lack "all" support. Untested by me.
2. GT exporters must match the GT API generation (2.8 vs 2.9 field changes), or use reflection. GT's API keeps moving (class renames in the 5.09.50 era already broke NESQL, RecEx and NERD's GT path).
3. Parallels and overclock modifiers live in code. Tooltip parsing is lossy, especially for addon machines, so expect hand-curated `machine_fact` rows with confidence values.
4. Visual/fake addon maps do not equal runtime behaviour.
5. Exporting at the main menu misses GT's server-start unification. Always export in-world.
6. Licences: Gregtorio is GPL-3.0 (fine privately, copyleft if you distribute), the Oracle is MIT, gtnh-extractor is Apache-2.0, and ShadowTheAge's data has no licence (do not redistribute).
7. Sizes and times: NERD-style per-item dumps run to hundreds of MB and hours; NESQL is about 600 MB and 60 minutes. Prefer the "all"-mode and GT-level dumps.
8. On a multiplayer server, server-side configs can make server recipes differ from a client-side export. UNCERTAIN until we know where you play.

## 7. Questions only the player can answer
1. Exact pack version (2.8.x, 2.9.0 final, or a 2.9 beta/daily): what does `config/GTNewHorizons/dreamcraft.cfg` say? Which Java version?
2. Exact jar names and versions of TST, GTNL (and its required libraries), Programmable Hatches and 123Technology, and where you got them (the addon branches are pack-specific).
3. Singleplayer or a server? If a server, can you get its config folder?
4. Is a separate "Gregtec-Export" Prism instance OK, and is swapping NEI 2.8.171 → 2.8.172 there acceptable?
5. Will you build one small Java mod (JDK 25 for Gradle), or do you want only prebuilt jars plus Python?
6. Have you changed any configs or `.zs` scripts that alter recipes? Game language (en_US)?
7. Do you need item icons in the UI (this adds a rendering pass)? Is a 5–30 minute export after each addon update acceptable?

---

## 8. Sources
- S1 DreamAssemblerXXL manifests: https://github.com/GTNewHorizons/DreamAssemblerXXL/tree/master/releases/manifests (2.8.0–2.9.0, daily.json)
- S2 2.8.4→2.9.0 changelog: https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/releases/changelogs/changelog%20from%202.8.4%20to%202.9.0.md (commit "update 2.9.0 final", 2026-10-31)
- S3 Modpack releases page: https://github.com/GTNewHorizons/GT-New-Horizons-Modpack/releases
- S4 GT5-Unofficial (HEAD 2026-11-01): https://github.com/GTNewHorizons/GT5-Unofficial — `src/main/java/gregtech/api/util/GTRecipe.java`, `gregtech/nei/GTNEIDefaultHandler.java`, `gregtech/nei/NEIGTConfig.java`, `gregtech/api/recipe/RecipeMaps.java`, `bartworks/API/recipe/BartWorksRecipeMaps.java`
- S4b GT 5.09.51.482 GTRecipe: https://github.com/GTNewHorizons/GT5-Unofficial/blob/5.09.51.482/src/main/java/gregtech/api/util/GTRecipe.java
- S4c https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/api/util/GTRecipeConstants.java
- S4d https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/api/util/GTRecipeBuilder.java
- S4e https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/api/metatileentity/implementations/MTEBasicMachine.java
- S4f https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/api/recipe/RecipeMap.java
- S5 https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/api/util/RecipeChangeAudit.java (added in PR #7743, 2026-08-11; present at tag 5.09.54.265)
- S6 https://github.com/GTNewHorizons/GT5-Unofficial/blob/master/src/main/java/gregtech/GTMod.java (`onServerStarting`)
- S10 https://github.com/GTNewHorizons/NotEnoughItems/releases/tag/2.8.172-GTNH
- S11 https://github.com/GTNewHorizons/NotEnoughItems/pull/1100
- S12 https://github.com/GTNewHorizons/NotEnoughItems/blob/master/src/main/java/codechicken/nei/recipe/RecipeJsonWriter.java (+ `config/RecipeDumper.java`, `recipe/debug/DebugHandlerWidget.java`, `recipe/GuiRecipeTab.java`; tags 2.8.171-GTNH/2.8.172-GTNH)
- S13 NEI sources: `recipe/IRecipeHandler.java`, `config/OptionList.java`, `config/ItemPanelDumper.java`, `config/HandlerDumper.java`, `util/NBTJson.java`, `recipe/stackinfo/*` at https://github.com/GTNewHorizons/NotEnoughItems
- S13b NEI 2.8.44 `Recipe.java`: https://github.com/GTNewHorizons/NotEnoughItems/blob/2.8.44-GTNH/src/main/java/codechicken/nei/recipe/Recipe.java
- S14 NEI `recipe/GuiCraftingRecipe.java`, `recipe/TemplateRecipeHandler.java` (same repo)
- S15 https://github.com/AX3Lino/NotEnoughRecipeDumps (`dependencies.gradle`, `RecipeDumper.java`, `dumpers/*`, `stack_serialization/RecipeDumpContext.java`)
- S16 https://github.com/Hermanoid/NotEnoughRecipeDumps/releases ; https://github.com/AX3Lino/NotEnoughRecipeDumps/releases
- S17 https://github.com/Hermanoid/NotEnoughRecipeDumps/issues/2
- S18 https://github.com/D-Cysteine/nesql-exporter
- S19 https://github.com/OrderedSet86/webnei-solid
- S20 https://github.com/Rykon00/Gregtorio/issues/714
- S21 https://github.com/ShadowTheAge/nesql-exporter
- S22 https://github.com/ShadowTheAge/gtnh/blob/main/export/README.md ; S22b https://github.com/ShadowTheAge/gtnh/blob/main/src/machines.ts
- S23 https://github.com/ShadowTheAge/gtnh-data (branches)
- S24 https://github.com/MrBruh/gtnh-shadow-convert
- S25 https://github.com/Milky-way-Galaxy/gtnhnei/blob/main/docs/datasets.md
- S26 https://github.com/bigbass1997/RecEx ; S27 https://github.com/bigbass1997/NotEnoughProduction
- S28 https://github.com/HOMEFTW/GTNHItemDocExporter (README, `src/main/java/.../export/`, releases)
- S29 https://github.com/Rykon00/Gregtorio/tree/main/tools/gtnh_dump (README.md, `src/gtnhdump/GTNHRecipeDump.java`); S29b `tools/gtnh_dump/nei_to_dump.py`
- S30 https://github.com/Samiracle64/gtnh-factory-flow/blob/main/tools/dataset-pipeline/gtnh-calc-oracle/src/main/java/dev/gtnhplanner/calcoracle/GtnhCalcOracleExporter.java (+ `GtnhCalcOracleMod.java`, `ClientAutorunHandler.java`)
- S31 https://github.com/Samiracle64/gtnh-factory-flow/blob/main/tools/dataset-pipeline/README.md
- S32 https://github.com/Learwin/NotEnoughItems/blob/data-export/src/main/java/codechicken/nei/commands/CommandRecipeId.java
- S33 https://github.com/GTNewHorizons/CraftTweaker/blob/master/src/main/java/minetweaker/MineTweakerImplementationAPI.java
- S34 https://github.com/sbancuz/PlanNH
- S35a https://github.com/Nxer/Twist-Space-Technology-Mod (`common/recipeMap/GTCMRecipe.java`, `nei/`, `dependencies.gradle`)
- S35b https://github.com/ABKQPO/GT-Not-Leisure/tree/dev-290 (`common/material/GTNLRecipeMaps.java`, `compat/nei/NuclearReactorGuideHandler.java`, `mixins/late/gregtech/MixinGTPPRecipeMaps.java`, `multiblock/*.mb`)
- S35c https://github.com/CallmeSHaobe/123Technology (`common/recipemap/Recipemaps.java`)
- S35d https://github.com/reobf/Programmable-Hatches-Mod/tree/290-daily-latest
- S36 GT `common/tileentities/machines/multi/MTEElectricBlastFurnace.java`, `api/enums/HeatingCoilLevel.java`
- S37 GT `api/util/OverclockCalculator.java`
- S38 GT `api/util/MultiblockTooltipBuilder.java`
- S39 https://github.com/MrBruh/gtnh-process-line-solver/blob/main/tools/gtnh-extractor/README.md
- S40 https://github.com/ryazhenkofc/gtnh-base-planner/blob/main/tools/game-dump/README.md
- S41 https://github.com/GTNewHorizons/StructureLib/blob/master/src/main/java/com/gtnewhorizon/structurelib/alignment/constructable/ConstructableUtility.java
- S42 https://github.com/GTNewHorizons/GuideNH/wiki/Structure-Export ; S43 https://github.com/GTNewHorizons/GuideNH (`client/command/GuideNhClientCommand.java`, `guide/internal/item/RegionWandExporter.java`, tag 1.3.55)
- S44 https://github.com/PrismLauncher/PrismLauncher/blob/develop/launcher/minecraft/MinecraftInstance.cpp ; S45 `launcher/minecraft/mod/Resource.cpp`
- S46 https://github.com/GTNewHorizons/DreamAssemblerXXL/blob/master/src/daxxl/defs.py (+ `assembler/platforms/generic_assembler.py`)
- S47 https://github.com/GTNewHorizons/NewHorizonsCoreMod/blob/master/src/main/java/com/dreammaster/config/CoreModConfig.java
