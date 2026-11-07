# GregtecLink export contract: `gregteclink-export/1`

What GregtecLink's export module (WO-1.1, `mod/`) writes, for the readers in WO-1.2/1.3/1.4. Plan §5.2 step 2.

**Principle: the mod dumps raw game facts; Python normalises** [A§5.3]. Circuit detection, voltage tiers, chance
basis points, signatures, content hashes and the NBT hash are all computed in WO-1.3, in one place. The mod resolves
only what needs the live game: registry names, ore names, display names and GT's unification data.

## Location and lifecycle

- `<Export gameDir>/gregteclink/exports/<run_id>/`. `run_id` is `-Dgregtec.runId` (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`)
  or the UTC time `YYYYMMDDTHHMMSSZ`; an existing name gets `-2`, `-3`, ...
- Files are written into `<run_id>.partial/`. `manifest.json` is written last; then the folder is renamed to
  `<run_id>/`. A run whose part failed is still renamed, with `"complete": false`.
- The rename is tried 3 times, waiting 1 s and then 2 s, because on Windows a virus scanner or the indexer often still
  holds a file that was just written. If all 3 fail, the folder stays `<run_id>.partial/` (its manifest may still say
  `"complete": true`), the log says so, and readers skip it until it is renamed by hand. The next successful run
  deletes it (see Pruning).
- **Readers** take only folders without `.partial` whose manifest has `"complete": true`, check each listed file's
  `sha256`, `bytes` and `lines`, refuse any `format` other than `gregteclink-export/1`, and refuse `"mode": "harness"`
  outside tests (dev-environment data is never game data).
- The mod writes inside its own instance only: the export root must be exactly the game dir's real path plus
  `gregteclink/exports`, so a link or junction at `gregteclink` or `exports` stops the export before anything is
  written. `gos export collect` (WO-1.2, `core/gregtec/ingest/collect.py`, D101) copies the newest complete run into
  `GOS_HOME\data\exports\<run_id>\` with `safe_open`, verifying every file before it is written, and keeps the newest
  `[export] keep_collected` there (gos.toml, default 5, D102). Python writes nothing to Export.
- **Pruning** (D98): after a run's rename succeeds, the mod keeps the newest 10 complete runs (manifest
  `"complete": true`, newest by `created_utc`, then by name), the new run among them when it is complete. It deletes
  older complete runs and every `.partial` folder an earlier run left, including one whose rename failed. It also keeps the newest 3 incomplete runs (manifest `"complete": false`) and deletes older
  ones. It never deletes the new run or a folder whose manifest is missing or unreadable, and works only on folders
  directly in `exports/`, each resolved through the guard; a link or junction inside one is deleted, never followed.
  It first renames a doomed run to `<run_id>.partial/` in one atomic step, so a run it can't finish deleting
  (Windows: a file in it is open) is left as a `.partial` folder that readers skip and the next run deletes, never
  as a complete run with files missing. A run it can't rename stays whole. Both are logged. In the dev runtime a run
  is about 50 MB without NEI's files and about 145 MB with them (the full pack is larger), so a collector should
  copy a run soon after it appears: ten later runs remove it.
- On a client, the GT files are written when the world's server starts, NEI's files once NEI has loaded (the
  folder stays `.partial` in between), CropsNH's crop files right after NEI's (WO-1.10), and the manifest last. If
  the world closes first, the run is finished then, without NEI's or the crop files.

## Keys

- **Item ref**: `{"item": "<registry name>", "meta": <damage>, "n": <stackSize>, "nbt": {…}?}`. The registry name is
  `GameData.getItemRegistry().getNameForObject(item)`. `meta` 32767 (wildcard) is kept raw. `n: 0` is raw too: it
  means not consumed (molds, programmed circuits).
- An item with no registry name is `{"item": null, "class": "<item class>", ...}` and counts in
  `counts.unregistered`.
- **Fluid ref**: `{"fluid": "<FluidRegistry name>", "mb": <amount>, "nbt": {…}?}`; an unregistered fluid is
  `"fluid": null` and counts in `counts.unregistered`.
- **Never written**: numeric item, block or fluid IDs, or numeric oredict IDs. (NBT is game data and is copied as
  is; some mods store numeric IDs inside it.)
- **NBT** is canonical JSON in NEI's `NBTJson` typing, so one parser reads both sources: compound keys sorted; ints
  bare; byte, short, long, float and double as strings with a `B`, `S`, `L`, `F` or `D` suffix; strings as is; lists
  and int arrays as arrays, byte arrays as arrays of `"<n>B"`; an empty list or array as
  `{"__custom_type": "net.minecraft.nbt.NBTTagList"}` (or `NBTTagIntArray`, `NBTTagByteArray`).
- **`__item`** is the one key that is not game NBT. A compound shaped like a saved ItemStack (numeric `id`, `Count`
  and `Damage`, e.g. the `output` of a data stick) also gets `"__item": "<registry name>"` (null if the ID has none).
  The numeric `id` is kept, but it is an EndlessIDs per-instance ID that means nothing once the game has exited.
  **WO-1.3** hashes NBT for item keys with `id` dropped from every compound that has `__item`, so no key depends on a
  numeric ID; NEI's NBT for the same stack is mapped the same way through its own id-to-name table. Removing every
  `__item` gives NEI's `NBTJson` back. Numeric IDs in NBT of any other shape are not resolved.
- A **machine**'s `mte_id` is GT's own constant and equals the `meta` of `gregtech:gt.blockmachines`, so its key is
  still registry name + meta.

## Files

| File | Content |
|---|---|
| `gt_recipe_maps.json` | Array sorted by `name`, one entry per `RecipeMap.ALL_RECIPE_MAPS` map |
| `gt_recipes.jsonl` | One recipe per line; maps sorted by name, each map's lines sorted by their JSON (see `idx`) |
| `gt_machines.jsonl` | One line per non-null `GregTechAPI.METATILEENTITIES` entry, by `mte_id` |
| `gt_ore_mixes.json` | GT's worldgen ore layers (`WorldgenGTOreLayer.sList`) and the instance's dimension IDs (WO-1.6, D126) |
| `oredict.jsonl` | The full ore dictionary: one line per oredict name, sorted by name, with its members (D145) |
| `items.jsonl` | Every (item, meta) and every (item, meta, NBT) referenced in the files above (the ore blocks of `gt_ore_mixes.json` and the members of `oredict.jsonl` too), sorted by item, meta, then NBT JSON (none first) |
| `fluids.jsonl` | Every fluid referenced in the files above, sorted by name |
| `manifest.json` | Run facts, file hashes, counts, part status. Not listed in its own `files` |
| `nei/<NEI file name>.jsonl` | On a client with NEI 2.8.172: a byte-for-byte copy of each file NEI's `dumpAll(true)` wrote to `dumps/recipes/` during this run (see below) |
| `crops/<dumper>.<csv\|txt>` | On a client with CropsNH: a byte-for-byte copy of the file each of CropsNH's NEI data dumpers wrote to `dumps/` during this run (WO-1.10, D134; see below) |

### `gt_recipe_maps.json` entry

`{name, amperage, backend: <class>, frontend: <class>, ui: {max_item_in, max_item_out, max_fluid_in, max_fluid_out},
nei_register, owner_mod: <default category's mod>, categories: [{name, owner_mod}] (sorted), count, errors?: [...]}`.
Recipes store EU/t with the amperage included (`amperage` is the map's).

### `gt_recipes.jsonl` line

```json
{"map":"gt.recipe.blastfurnace","idx":0,"category":"gt.recipe.blastfurnace",
 "eut":1920,"duration":200,"special":4600,
 "in":[{"item":"gregtech:gt.metaitem.01","meta":2503,"n":5}, null],
 "in_chance":[10000,2500]?, "out":[...], "out_chance":[...]?,
 "fin":[{"fluid":"liquidnitrogen","mb":2000,"alt":[{"fluid":"...","mb":2000}]?}], "fin_chance":[...]?,
 "fout":[...], "fout_chance":[...]?,
 "special_items":<value>?, "metadata":{"coil_heat":4600}?, "nei_desc":["..."]?,
 "enabled":true,"hidden":false,"fake":false,"can_be_buffered":true,
 "needs_empty_output":false,"nbt_sensitive":false,"owners":["GoodGenerator"]?}
```

- **Arrays stay positional.** `in`, `out`, `fin` and `fout` keep their nulls so the chance arrays line up with the
  slots. A null GT array is written as `[]`. A `*_chance` array is omitted when GT's is null (every slot 100%);
  values are GT's raw 1..10000.
- **Slot order is GT's and is not always stable.** Some recipes build their outputs from a HashMap, so the same
  recipe lists them in a different order on each run (in the dev runtime: all 43 `gt.recipe.eyeofharmony` recipes).
  WO-1.3 compares outputs as a multiset of (ref, chance) pairs, never by position, and an "unchanged export" check
  compares normalised content, not raw file hashes.
- **Item inputs of `GTRecipe_WithAlt`** also carry `ore` (the oredict name of the slot, never its numeric ID) and
  `alt` (the slot's accepted alternatives, as refs).
- **Fluid inputs** carry `alt` from `mAltFluidInputs`.
- **`metadata`**: keys are GT's own identifiers (`coil_heat`, `cleanroom`, `GLASS`, `fuel_value`, ...), sorted, read
  by reflection. Values, raw: numbers, booleans and strings as is; ItemStack and FluidStack as refs; a Fluid as
  `{"fluid"}`; GT `Materials` as `mName`; other enums as `name()`; arrays, collections and maps as lists and objects
  (to depth 4); anything else as `{"class", "fields"?, "str"?}`. `fields` holds every public instance field, encoded
  the same way (so an ItemStack field is a ref); a field that throws is `{"error"}`. `str` is the value's
  `toString()`, only when its class overrides it, with identity hashes (`pkg.Class@1b6d3586`) cut to the class name.
  Object's own `toString()` is only a class name and an identity hash, so it is left out. A class with neither public
  fields nor its own `toString()` is just `{"class"}` (in the dev runtime: Eye of Harmony's `special_items`, whose
  data GT keeps in private fields). A value that throws is `{"error"}`. No value depends on the JVM run.
- **`special_items`** (`mSpecialItems`) uses the same encoding.
- **`idx`** is the line's position within its map, after sorting. GT registers recipes in a different order on every
  run (and keeps each map's categories in a HashMap keyed by identity), so the mod sorts each map's lines by their
  JSON, which also groups them by `category`. An unchanged pack therefore gives the same lines in the same order,
  apart from the unstable slot order above. Identity is still WO-1.3's signature, never `idx`.
- **`owners`** are mod IDs, in the order GT recorded them. The export module turns GT's recipe-owner capture on in
  preInit, so recipes added before that (in GT's own preInit) have none; `owners` is omitted when GT kept no list.
- A recipe that fails to serialise becomes `{"map", "idx", "error"}` and counts in `counts.recipe_errors`.

### `gt_machines.jsonl` line

`{mte_id, item: "gregtech:gt.blockmachines", meta, class, mod, meta_name, name, tier?, multiblock, recipe_map?,
recipe_maps: [...]?, flags: {batch_mode, input_separation, void_protection, recipe_locking}?, tooltip: [...],
structure_tooltip: [...]?, errors: [...]?}`

- `mod`: the mod whose source jar holds the class; when several mods share a jar, the one whose `@Mod` class package
  is the longest prefix of the machine's package (ties go to the smallest modid), else `"unknown"`.
- `tier` for tiered machine bases; `multiblock` = `MTEMultiBlockBase`; `flags` for multiblocks only;
  `recipe_maps` for `RecipeMapWorkable` machines.
- `tooltip` lines are raw, with `§` codes. For multiblocks it is the primary description and `structure_tooltip`
  the secondary one; shift-dependent text may differ between client and server.
- A line with `errors` counts in `counts.machine_errors`.

### `gt_ore_mixes.json`

Plan §5.2 step 6 (D126): the registry GT's worldgen draws ore veins from, so the numbers are the ones that generate.

```json
{"layers":[{"name":"ore.mix.naquadah","localized":"Naquadah","min_y":10,"max_y":90,"weight":30,"density":4,"size":32,
  "dims":["EndAsteroid","asteroids","barnarda4","..."],"dim_heights":{"Twilight Forest":[15,40]}?,
  "ores":{"primary":{"material":"Naquadah","name":"Naquadah","class":"gregtech.api.enums.Materials","stone":"Stone",
                     "ore":{"item":"gregtech:gt.blockores2","meta":324,"n":1}},
          "secondary":{...},"between":{...},"sporadic":{...}},"errors":[...]?}],
 "dimensions":[{"dim":0,"provider":"net.minecraft.world.WorldProviderSurface","name":"Overworld"}]}
```

- `layers`: one per `WorldgenGTOreLayer.sList` entry (GT 5.09.54.265 registers every `OreMixes` mix there, all enabled), sorted by `name`. `name` is GT's `mWorldGenName`, the key VisualProspecting stores veins by; `localized` is the mix's display name (`OreMixBuilder.getLocalizedName`, the name VP shows), null when GT has no builder for the layer.
- `min_y`/`max_y` are the layer's `veinHeight` **after** GT's own fix-up (a range under 9 blocks is widened to `min + 9`), which is what worldgen uses; `dim_heights` holds the per-dimension overrides (`dimVeinHeights`), only when the layer has some. `weight`, `density` and `size` are `mWeight`, `mDensity` and `mSize` as GT stores them.
- `dims` are the dimension names GT matches a world by (`DimensionDef.getDimensionName`: a Galacticraft body's name such as `moon`, else the provider's `getDimensionName()` such as `Overworld`, `Nether`, `Twilight Forest`), sorted. `EndAsteroid` is not a dimension but GT's name for The End's outer chunks (more than 16 chunks from 0, 0 and away from Hardcore Ender Expansion's and Draconic Evolution's islands, `DimensionDef.getEffectiveDefForChunk`): no `dimensions` entry has that name, and its veins are dim 1's.
- `ores`: the four layers. `material` is the `IOreMaterial`'s internal name (a GT `Materials.mName`, a bartworks Werkstoff or a GT++ material name), `name` its display name, `class` its class. `ore` is an item ref of the ore block in plain stone (`StoneType.Stone`), or in the material's first valid stone when it has no plain-stone ore (`stone` says which), as `OreManager.getStack` gives it; the ref is recorded in `items.jsonl` like every other. Never a numeric ID.
- `dimensions`: every registered dimension ID (`DimensionManager.getStaticDimensionIDs()`), with its provider class and the name GT's worldgen would match it by (resolved as VisualProspecting's `DimensionNameResolver` does: the loaded world's provider, else a new provider instance; Galacticraft's celestial body by reflection). Several IDs can share a name. A dimension that fails to resolve carries `error` instead of `name`.
- A layer whose part fails to serialise keeps the rest and lists `errors` (`counts.ore_mix_errors`).

### `oredict.jsonl` line

`{"ore": "<oredict name>", "members": [<item ref without "n">, ...]}`, or `{"ore", "error"}` when the mod couldn't
read the name's members (`counts.oredict_errors`). D142 (h), D145.

- One line per name in `OreDictionary.getOreNames()`, sorted by name, each once (an empty name, which no `ore:<name>` could
  cite, is left out). A name with no members (Forge and GT
  register many names that never get one: 40,717 names and 24,039 members in the dev runtime) has `"members": []`.
- `members` are the stacks `OreDictionary.getOres(name)` holds, as item refs (`item`, `meta`, `nbt`?), never a
  numeric item or oredict ID. Each distinct ref is listed once, sorted by registry name (unregistered last), meta,
  then NBT JSON (none first). **Wildcard meta 32767 is kept raw**: `minecraft:planks@32767` in `plankWood` stands for
  every meta of the item, as NEI expands it. `nbt` is the same canonical JSON as every ref's; Python takes the NBT hash
  (WO-1.3). An unregistered member is `{"item": null, "class"}` and counts in `counts.unregistered`.
- Every member is also a line of `items.jsonl` (named there), so its `ore` list holds the name too.

### `items.jsonl` line

`{item, meta, nbt?, unlocalized, name, ore: [names, sorted], gt: {prefix, material, amount}?, errors: [...]?}`.
`name` is the display name in the instance's language; `gt` is `GTOreDictUnificator.getAssociation` (an `OrePrefixes`
name, a `Materials.mName` and GT's material amount). Wildcard metas often fail to name and carry `errors`
(`counts.item_errors`).

Every referenced (item, meta) has a line without `nbt`. Every distinct NBT a ref carries also gets its own line with
that `nbt`, named from the stack with its tag, because `name` and `unlocalized` can depend on it (bartworks
`BioLabParts` cultures, for example). This matches A§5.4's `UNIQUE(registry_name, meta, nbt_hash)`.

### `fluids.jsonl` line

`{fluid, unlocalized, name, temperature, density, viscosity, gaseous}`, or `{fluid, error}`.

### `nei/*.jsonl` (NEI's format, copied as is)

NEI 2.8.172's own recipe export (PR #1100), the same files as Data Dumps → All Handler Recipes → Separate. GregtecLink
calls `RecipeJsonWriter.dumpAll(true)` on the client thread once NEI has loaded, then copies every `.jsonl` file NEI
wrote (regular files only, never a link) into `nei/`. The format is NEI's, not GregtecLink's:

- One recipe per line: `{"handler", "ingredients": [stack...], "results": [stack...], "gt": {"eut", "durationTicks",
  "totalEU"}?}`. Lines end with the platform's line separator (`\r\n` on Windows); `lines` in the manifest counts `\n`.
- A stack is `{"strId": "<registry name>", "Damage", "Count", "name", "tag"?, "chance"?, "anyOf"?, "returns"?,
  "neiFluidName"?}`, with no numeric `id`. `tag` is NEI's `NBTJson` (typed strings like `"1B"`), the same typing as
  GregtecLink's `nbt`. `chance` is 1/10000 and only present below 10000. `anyOf` lists every accepted stack of the
  slot (oredict alternatives), without `Count`. `returns` is a crafting grid's container item. GT fluid display stacks
  carry `neiFluidName` and `Count` in mB. Non-consumed stacks (molds, programmed circuits) keep `Count: 0`.
- `handler` is NEI's handler id: for GT handlers the recipe **category** id, e.g. `gt.recipe.assembler` or
  `gt.recipe.category.alloy_smelter_molding`, so one GT map can appear as several handlers. **Readers key on each
  line's `handler`, never on the file name**: file names are NEI's cleaned handler display names in the instance's
  language, with `_2`, `_3` added for duplicate names (two handlers named "Blast Furnace", for example).
- Coverage limits: a handler hidden in NEI's config (Export mirrors Play's `config/NEI/`) is not dumped; a handler that
  fails to load is dropped by NEI without notice (it counts in `counts.nei_lookup_errors`); a recipe NEI can't
  serialise is left out (`counts.nei_skipped`). For the dump, GregtecLink has NEI load every handler on the client
  thread, one after another (D188), so the same pack gives the same handlers and the same `anyOf` stacks on every
  run. Runs made before D188 could drop a GT handler or, with no flag at all, miss some of a GT slot's `anyOf`
  stacks: NEI loaded GT's handlers in parallel, which GT 5.09.54.265 doesn't support. Counts can also differ between runs of the same pack with config
  state: in the dev runtime, AE2's "Facade Crafting" had 2,403 recipes in a brand-new run folder and 2,631 in a
  reused one, where AE2 had rewritten its `config/AppliedEnergistics2/Facades.cfg` on joining the world. Compare NEI
  content, not counts.

### `crops/*` (CropsNH's format, copied as is; WO-1.10)

CropsNH 2.0.140 (GTNH 2.9.0's; LGPL-3.0, linked at runtime only) registers NEI data dumpers under NEI's Tools, Data
Dumps, Crops NH (`tools.dump.cropsnh.<name>` in NEI's option list; the classes are
`com.gtnewhorizon.cropsnh.compatibility.NEI.dumpers.*Dumper`). Right after NEI's recipe dump, on the client thread,
GregtecLink calls `dumpFile()` on every `DataDumper` in that list, as the Dump buttons do: each writes
`<gameDir>/dumps/<name>.<ext>` (NEI's `DataDumper` names the file after the part of the option name after its last
dot) and posts a chat line. GregtecLink copies each file it wrote (regular files only, written since the call
started minus 2 s, never through a link) into `crops/<name>.<ext>`. The format is CropsNH's, written with the JVM's
default charset (`manifest.java.charset`) and the platform's line separator; the files Python reads:

| File | Dumper | Content |
|---|---|---|
| `cropRegistry.csv` | `CropRegistryDumper` | Header `ID,Name,Unlocalised Name,...,Drop Table,Alternate Seeds` (22 columns), one crop per line: CropsNH's crop id (`cropsnh:allium`), names, tier, soil type, light and sub-soil requirements as English text joined by ` \| `, drops as `N x <display name>[<NBT>] @ <value / 100>.<value % 100>%` (CropsNH's basis points, not zero-padded) joined by ` \| `. CSV quoting with `"` |
| `deterministicMutations.csv` | `DeterministicMutationRegistryDumper` | The parent-combination recipes: `output,parent1..parent4,conditions`, crops by display name, then one column per condition (sub-soil text, `Requires the crop breeder`, `MachineCatalyst(<item> x N \| ore:<name> x N)`) |
| `mutationPools.csv` | `MutationPoolRegistryDumper` | Header `Crop,<pool display names>`, one line per crop in any pool, `TRUE`/`FALSE` per pool |
| `soilRegistry.txt` | `SoilRegistryDumper` | Each soil list's name, then `- <block registry name>[:<meta>]` lines (no meta: any meta), `# empty` for a list with no block; blank lines between lists |
| `alternateSeed.csv` | `AlternateSeedDumper` | `Item,Meta,CropCard`: an item that plants a crop (meta `*` for any) and the crop id |

The other six (`bootProtection.csv`, `fertilizerFluids.csv`, `fertilizerItems.csv`, `fertilizerUnitFluids.csv`,
`hydrationFluids.csv`, `weedEXFluids.csv`) are copied too and not read yet. Drops and catalysts are named, not keyed,
in these files: readers key a drop from NEI's crop page of the same crop (handler `cropsnhCrops`, whose stacks have
registry names), and NEI's other CropsNH tabs (`cropsnhBreeding`, `mutationPool`, `cropsnhAlternateSeeds`) hold the
same crops by the seed's NBT `crop` (the crop id) (D136). `mutationPool` has no `cropsnh` prefix: readers take its
lines as CropsNH's only when they show `cropsnh:genericSeed` stacks alone. Neither the files nor NEI's lines carry a numeric ID.

### `manifest.json`

```json
{"format":"gregteclink-export/1","run_id":"20261105T223624Z","created_utc":"2026-11-05T22:36:24Z","complete":true,
 "mode":"one_click|autoexport|harness","world":"<world folder name, or null>","side":"client|server","deobf_env":false,
 "minecraft":"1.7.10","forge":"10.13.4.1614","java":{"version":"...","vendor":"...","charset":"UTF-8"},
 "pack_version":"<raw S:ModPackVersion of config/GTNewHorizons/dreamcraft.cfg, or null>",
 "gt_version":"5.09.54.265","nei_version":"<NotEnoughItems version, or null>",
 "gregteclink":{"version":"<build version: the deployed commit's first 12 hex digits, else git-derived>"},
 "mods":[{"modid","name","version","file","sha256"}],
 "files":{"gt_recipes.jsonl":{"sha256":"...","bytes":0,"lines":0}, "...":{}},
 "counts":{"recipe_maps","recipe_map_errors","recipes","recipe_errors","machines","machine_errors",
           "ore_mixes","ore_mix_errors","dimensions","oredict","oredict_members","oredict_errors",
           "items","item_errors","fluids","unregistered",
           "nei_handlers"?,"nei_reported"?,"nei_skipped"?,"nei_lookup_errors"?,"nei_files"?,"nei_recipes"?,
           "crop_dumpers"?,"crop_files"?},
 "options":{"recipe_owners":true},
 "parts":{"gt_dump":"ok|failed","machines":"ok|failed","ore_mixes":"ok|failed","oredict":"ok|failed",
          "refs":"ok|failed",
          "nei":"absent|skipped_server|ok|failed","crops":"absent|skipped_server|ok|failed",
          "autoexport":"off|ok|failed"},
 "part_errors":{"<part>":"<why it failed>"}?,
 "part_warnings":{"nei":"<what an ok NEI step left out>","crops":"<an optional crop dumper that failed>"}?,
 "durations_ms":{"gt_dump","machines","ore_mixes","oredict","refs","nei_wait"?,"nei_dump"?,"nei_copy"?,"crops_dump"?,
                 "crops_copy"?}}
```

- `mods` lists **Export's** loaded mods, sorted by modid. `file` is relative to the game dir, or only the file name
  for a source outside it (the dev runtime); `sha256` is null when the source is not a readable file: a directory, or
  a name with no file behind it. In the dev runtime that is the `minecraft.jar` reported by `mcp` and the coremods'
  dummy containers (Forge and FML are hashed there, from the recompiled Minecraft jar). Which entries are null in a
  real instance shows in the first Export run on the PC. Play's mods come from Python's fingerprint (§5.2.4), never
  from here.
- `counts.items` is the number of lines in `items.jsonl`, NBT variants included.
- `pack_version` `2.9.x` means unknown [A§5.5 correction]: trust jar hashes over this string.
- `complete` covers the GT parts and the manifest, as before: a failed NEI step leaves `complete: true`. **Readers that
  need NEI's data check `parts.nei == "ok"`** and use `nei/` only then.
- `parts.ore_mixes` (WO-1.6) is not part of `complete` either: a failed ore-mix pass leaves the recipe parts usable.
  **Readers of `gt_ore_mixes.json` check `parts.ore_mixes == "ok"`**. Runs made before WO-1.6 have no such part and
  no file.
- `parts.oredict` (D145) is not part of `complete` either. **Readers of `oredict.jsonl` check
  `parts.oredict == "ok"`**. For a run made before D145 (no such part, no file) `gos db build` names NEI slots from
  `items.jsonl`'s `ore` lists, as before; a run whose `parts.oredict` is `failed` and whose `parts.nei` is `ok` it
  refuses, because those lists name fewer slots and its NEI-only recipes would get other citation keys for one run,
  and `gos export run` doesn't record it as an export. `counts.oredict` is the number of lines (names),
  `counts.oredict_members` the members listed in all of them.
- `world` is the world's folder name: `gregteclink-autoexport` for autoexport, the folder of whatever world was
  opened for `one_click`, the harness's own world for `harness`.
- `parts.nei`:
  - `absent`: no NEI ≥ 2.8.172 `RecipeJsonWriter` in the game (Export's NEI swap is missing).
  - `skipped_server`: a dedicated server, which has no NEI step.
  - `ok`: the call threw nothing, NEI logged no `Error dumping recipes`, it wrote at least one file, every copy
    succeeded, and the lines copied (`counts.nei_recipes`) equal the count NEI logged (`counts.nei_reported`).
  - `failed`: anything else, with the reason in `part_errors.nei`. Also when NEI was not ready 10 minutes after the
    world opened (NEI disabled, for example), or the world was closed before it was, or NEI's handler lists could
    not be swapped for the serial load (D188).
- `parts.crops` (WO-1.10, D134) is not part of `complete`; **readers of `crops/` check `parts.crops == "ok"`**.
  Runs made before WO-1.10 have no such part and no files.
  - `absent`: CropsNH isn't loaded.
  - `skipped_server`: a dedicated server, which has no NEI to run the dumpers.
  - `ok`: every dumper Python reads (`alternateSeed`, `cropRegistry`, `deterministicMutations`, `mutationPools`,
    `soilRegistry`) is registered and, for each of them, `dumpFile()` threw nothing, NEI's logger
    (`NEIClientConfig.logger`) got no `Error dumping ...` line during its call (NEI's `DataDumper` catches its own
    exceptions and logs that), it wrote its file, not empty, and the copy succeeded. Then `counts.crop_dumpers` (registered)
    equals `counts.crop_files` (copied) unless one of the other six failed, which only adds `part_warnings.crops`.
  - `failed`: anything else, with the reason in `part_errors.crops`; also when NEI's recipe step didn't run (NEI
    absent or not ready, the world closed first, the autoexport deadline), since the crop step runs right after it.
  - A file already in `dumps/` that is a link or not a regular file is refused (the dumper would write through it).
- `java.charset` (WO-1.10) is the JVM's default charset, which CropsNH's dumpers write in; runs without it are
  UTF-8.
- `parts.autoexport`: `off` without `-Dgregtec.autoexport=true` (or on a dedicated server); `ok` when the
  autoexport flow finished the run itself; `failed` when its 30-minute deadline was reached or the world closed
  first (`part_errors.autoexport`). PR 1 runs may say `not_built`: treat it as `off`. An autoexport launch that
  quits with no run at all (world not prepared, never opened, no export started by the deadline) writes no
  manifest; Export's `logs/fml-client-latest.log` then has an ERROR line `autoexport: no run: <why>`.
- `part_errors` is present only when a part failed, with one entry per failed part.
- `part_warnings` is present only when a part is `ok` but left something out. Today only `nei`: NEI dropped one or
  more recipe handlers while loading them (`counts.nei_lookup_errors` > 0; NEI names them only on stderr), so their
  recipes are missing from `nei/` although the lines still equal NEI's count. Since D188 the handlers load one by
  one on the client thread, so this is a handler that fails there too, not a race between NEI's threads. **Readers
  that need every handler check that `part_warnings.nei` is absent.** And `crops` (WO-1.10): one of CropsNH's dumpers that Python doesn't
  read failed, so its file is missing from `crops/`.
- The `nei_*` counts and the `nei_dump` and `nei_copy` durations are present only when the NEI step called
  `dumpAll`; `nei_wait` also when NEI was not ready in time. `nei_handlers` and `nei_reported` are -1 when NEI logged
  no such line. `nei_wait` is the time from entering the world until the dump started (or the wait timed out);
  `nei_dump` is NEI's call, during which the game is frozen; `nei_copy` the copy into `nei/`.
- The `crop_*` counts and the `crops_dump` and `crops_copy` durations are present only when the crop step ran:
  `crop_dumpers` once it found CropsNH's dumpers, the rest once it called them.

## Python readers (WO-1.3)

- `core/gregtec/ingest/gtdump.py`: `gt_recipe_maps.json`, `gt_recipes.jsonl`, `items.jsonl`, `fluids.jsonl`, and
  `oredict.jsonl` (D145), only from runs with `parts.oredict == "ok"`; `gos db build` loads it into `ore` and
  `ore_member` and names NEI-only oredict slots from it.
- `core/gregtec/ingest/machines.py`: `gt_machines.jsonl`.
- `core/gregtec/ingest/nei.py`: `nei/*.jsonl`, only from runs with `parts.nei == "ok"`.
- `core/gregtec/ingest/ore_mixes.py` (WO-1.6): `gt_ore_mixes.json`, only from runs with `parts.ore_mixes == "ok"`;
  `gos ores load` (`core/gregtec/db/ores.py`, D128) loads it into `ore_mix`.
- `core/gregtec/ingest/crops.py` (WO-1.10): `crops/*`, only from runs with `parts.crops == "ok"`, plus NEI's
  CropsNH tabs when `parts.nei == "ok"`; `gos db build` loads it into the crop tables (`core/gregtec/db/crops.py`,
  D136).
- `core/gregtec/ingest/normalize.py` (keys, NBT hash, identity and content hashes, D104) and
  `core/gregtec/ingest/merge.py` (NEI into GT, the collision test, D105, D106); `gos db build`
  (`core/gregtec/db/build.py`, D107) loads a collected run into `GOS_HOME\data\gos.sqlite`.
- NEI picks the stack it displays for an oredict slot anew on each run, and some handlers show a random example, so
  no key or content hash uses a displayed stack (D104), and the handlers whose lines aren't recipes (AE2 facades,
  NEI's fuel list, the GT ore plugin's vein pages, Tinkers' tool materials, CropsNH's crop-stick tabs) are left out
  of the recipe tables (D105, D136). Their files stay in the collected run. GT keys the alternatives of a slot by
  its displayed item, except in the maps whose displayed item changes between runs (CropsNH's Crop Breeder, D135).
