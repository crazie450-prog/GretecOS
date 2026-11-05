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
- **Readers** take only folders without `.partial` whose manifest has `"complete": true`, check each listed file's
  `sha256`, `bytes` and `lines`, refuse any `format` other than `gregteclink-export/1`, and refuse `"mode": "harness"`
  outside tests (dev-environment data is never game data).
- The mod writes inside its own instance only. WO-1.2/1.4's collection script copies a run into
  `GOS_HOME\data\exports\<run_id>\` with `safe_open`, copy-then-parse and retry ×3. Python writes nothing to Export,
  so old runs stay there.

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
- A **machine**'s `mte_id` is GT's own constant and equals the `meta` of `gregtech:gt.blockmachines`, so its key is
  still registry name + meta.

## Files

| File | Content |
|---|---|
| `gt_recipe_maps.json` | Array sorted by `name`, one entry per `RecipeMap.ALL_RECIPE_MAPS` map |
| `gt_recipes.jsonl` | One recipe per line; maps sorted by name, recipes in backend order |
| `gt_machines.jsonl` | One line per non-null `GregTechAPI.METATILEENTITIES` entry, by `mte_id` |
| `items.jsonl` | Every (item, meta) referenced in the files above, sorted by item then meta |
| `fluids.jsonl` | Every fluid referenced in the files above, sorted by name |
| `manifest.json` | Run facts, file hashes, counts, part status. Not listed in its own `files` |
| `nei/*.jsonl` | PR 1b: a copy of NEI's `dumps/recipes/` |

### `gt_recipe_maps.json` entry

`{name, amperage, backend: <class>, frontend: <class>, ui: {max_item_in, max_item_out, max_fluid_in, max_fluid_out},
nei_register, owner_mod: <default category's mod>, categories: [{name, owner_mod}] (sorted), count, errors?: [...]}`.
Recipes store EU/t with the amperage included (`amperage` is the map's).

### `gt_recipes.jsonl` line

```json
{"map":"gt.recipe.blastfurnace","idx":0,"category":"gt.recipe.blastfurnace",
 "eut":1920,"duration":200,"special":4600,
 "in":[{"item":"gregtech:gt.metaitem.01","meta":2503,"n":5}, null],
 "in_chance":[10000,null]?, "out":[...], "out_chance":[...]?,
 "fin":[{"fluid":"liquidnitrogen","mb":2000,"alt":[{"fluid":"...","mb":2000}]?}], "fin_chance":[...]?,
 "fout":[...], "fout_chance":[...]?,
 "special_items":<value>?, "metadata":{"coil_heat":4600}?, "nei_desc":["..."]?,
 "enabled":true,"hidden":false,"fake":false,"can_be_buffered":true,
 "needs_empty_output":false,"nbt_sensitive":false,"owners":["GoodGenerator"]?}
```

- **Arrays stay positional.** `in`, `out`, `fin` and `fout` keep their nulls so the chance arrays line up with the
  slots. A null GT array is written as `[]`. A `*_chance` array is omitted when GT's is null (every slot 100%);
  values are GT's raw 1..10000.
- **Item inputs of `GTRecipe_WithAlt`** also carry `ore` (the oredict name of the slot, never its numeric ID) and
  `alt` (the slot's accepted alternatives, as refs).
- **Fluid inputs** carry `alt` from `mAltFluidInputs`.
- **`metadata`**: keys are GT's own identifiers (`coil_heat`, `cleanroom`, `GLASS`, `fuel_value`, ...), sorted, read
  by reflection. Values, raw: numbers, booleans and strings as is; ItemStack and FluidStack as refs; a Fluid as
  `{"fluid"}`; GT `Materials` as `mName`; other enums as `name()`; arrays, collections and maps as lists and objects
  (to depth 4); anything else as `{"class", "fields": {public simple-typed fields}?, "str"}`. A value that throws is
  `{"error"}`.
- **`special_items`** (`mSpecialItems`) uses the same encoding.
- **`idx`** is the position in the map's backend, for debugging only: it is not stable. Identity is WO-1.3's
  signature.
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

### `items.jsonl` line

`{item, meta, unlocalized, name, ore: [names, sorted], gt: {prefix, material, amount}?, errors: [...]?}`. `name` is
the display name in the instance's language; `gt` is `GTOreDictUnificator.getAssociation` (an `OrePrefixes` name, a
`Materials.mName` and GT's material amount). Wildcard metas often fail to name and carry `errors`
(`counts.item_errors`).

### `fluids.jsonl` line

`{fluid, unlocalized, name, temperature, density, viscosity, gaseous}`, or `{fluid, error}`.

### `manifest.json`

```json
{"format":"gregteclink-export/1","run_id":"20261105T223624Z","created_utc":"2026-11-05T22:36:24Z","complete":true,
 "mode":"one_click|autoexport|harness","side":"client|server","deobf_env":false,
 "minecraft":"1.7.10","forge":"10.13.4.1614","java":{"version":"...","vendor":"..."},
 "pack_version":"<raw S:ModPackVersion of config/GTNewHorizons/dreamcraft.cfg, or null>",
 "gt_version":"5.09.54.265","nei_version":"<NotEnoughItems version, or null>",
 "gregteclink":{"version":"<git-derived build version>"},
 "mods":[{"modid","name","version","file","sha256"}],
 "files":{"gt_recipes.jsonl":{"sha256":"...","bytes":0,"lines":0}, "...":{}},
 "counts":{"recipe_maps","recipe_map_errors","recipes","recipe_errors","machines","machine_errors",
           "items","item_errors","fluids","unregistered"},
 "options":{"recipe_owners":true},
 "parts":{"gt_dump":"ok|failed","machines":"ok|failed","refs":"ok|failed",
          "nei":"absent|skipped_server|ok|failed","autoexport":"off|not_built|ok|failed"},
 "durations_ms":{"gt_dump","machines","refs"}}
```

- `mods` lists **Export's** loaded mods, sorted by modid. `file` is relative to the game dir, or only the file name
  for a source outside it (the dev runtime); `sha256` is null for a directory or the Minecraft jar. Play's mods come
  from Python's fingerprint (§5.2.4), never from here.
- `pack_version` `2.9.x` means unknown [A§5.5 correction]: trust jar hashes over this string.
- `parts.nei`: `absent` without NEI ≥ 2.8.172's `RecipeJsonWriter`; `skipped_server` when it exists but this run
  could not call it (PR 1 never does; PR 1b calls it on the client).
- `parts.autoexport`: `not_built` when `-Dgregtec.autoexport=true` was set before PR 1b.

## Python readers (WO-1.3; named here only)

- `core/gregtec/ingest/gtdump.py`: `gt_recipe_maps.json`, `gt_recipes.jsonl`, `items.jsonl`, `fluids.jsonl`.
- `core/gregtec/ingest/machines.py`: `gt_machines.jsonl`.
- `core/gregtec/ingest/nei.py`: `nei/*.jsonl` (PR 1b).
