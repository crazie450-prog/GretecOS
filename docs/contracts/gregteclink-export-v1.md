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
  written. WO-1.2/1.4's collection script copies a run into `GOS_HOME\data\exports\<run_id>\` with `safe_open`,
  copy-then-parse and retry ×3. Python writes nothing to Export.
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
  folder stays `.partial` in between), and the manifest last. If the world closes first, the run is finished then,
  without NEI's files.

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
| `items.jsonl` | Every (item, meta) and every (item, meta, NBT) referenced in the files above, sorted by item, meta, then NBT JSON (none first) |
| `fluids.jsonl` | Every fluid referenced in the files above, sorted by name |
| `manifest.json` | Run facts, file hashes, counts, part status. Not listed in its own `files` |
| `nei/<NEI file name>.jsonl` | On a client with NEI 2.8.172: a byte-for-byte copy of each file NEI's `dumpAll(true)` wrote to `dumps/recipes/` during this run (see below) |

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
  serialise is left out (`counts.nei_skipped`). Counts can also differ between runs of the same pack with config
  state: in the dev runtime, AE2's "Facade Crafting" had 2,403 recipes in a brand-new run folder and 2,631 in a
  reused one, where AE2 had rewritten its `config/AppliedEnergistics2/Facades.cfg` on joining the world. Compare NEI
  content, not counts.

### `manifest.json`

```json
{"format":"gregteclink-export/1","run_id":"20261105T223624Z","created_utc":"2026-11-05T22:36:24Z","complete":true,
 "mode":"one_click|autoexport|harness","world":"<world folder name, or null>","side":"client|server","deobf_env":false,
 "minecraft":"1.7.10","forge":"10.13.4.1614","java":{"version":"...","vendor":"..."},
 "pack_version":"<raw S:ModPackVersion of config/GTNewHorizons/dreamcraft.cfg, or null>",
 "gt_version":"5.09.54.265","nei_version":"<NotEnoughItems version, or null>",
 "gregteclink":{"version":"<build version: the deployed commit's first 12 hex digits, else git-derived>"},
 "mods":[{"modid","name","version","file","sha256"}],
 "files":{"gt_recipes.jsonl":{"sha256":"...","bytes":0,"lines":0}, "...":{}},
 "counts":{"recipe_maps","recipe_map_errors","recipes","recipe_errors","machines","machine_errors",
           "items","item_errors","fluids","unregistered",
           "nei_handlers"?,"nei_reported"?,"nei_skipped"?,"nei_lookup_errors"?,"nei_files"?,"nei_recipes"?},
 "options":{"recipe_owners":true},
 "parts":{"gt_dump":"ok|failed","machines":"ok|failed","refs":"ok|failed",
          "nei":"absent|skipped_server|ok|failed","autoexport":"off|ok|failed"},
 "part_errors":{"<part>":"<why it failed>"}?,
 "part_warnings":{"nei":"<what an ok NEI step left out>"}?,
 "durations_ms":{"gt_dump","machines","refs","nei_wait"?,"nei_dump"?,"nei_copy"?}}
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
- `world` is the world's folder name: `gregteclink-autoexport` for autoexport, the folder of whatever world was
  opened for `one_click`, the harness's own world for `harness`.
- `parts.nei`:
  - `absent`: no NEI ≥ 2.8.172 `RecipeJsonWriter` in the game (Export's NEI swap is missing).
  - `skipped_server`: a dedicated server, which has no NEI step.
  - `ok`: the call threw nothing, NEI logged no `Error dumping recipes`, it wrote at least one file, every copy
    succeeded, and the lines copied (`counts.nei_recipes`) equal the count NEI logged (`counts.nei_reported`).
  - `failed`: anything else, with the reason in `part_errors.nei`. Also when NEI was not ready 10 minutes after the
    world opened (NEI disabled, for example), or the world was closed before it was.
- `parts.autoexport`: `off` without `-Dgregtec.autoexport=true` (or on a dedicated server); `ok` when the
  autoexport flow finished the run itself; `failed` when its 30-minute deadline was reached or the world closed
  first (`part_errors.autoexport`). PR 1 runs may say `not_built`: treat it as `off`. An autoexport launch that
  quits with no run at all (world not prepared, never opened, no export started by the deadline) writes no
  manifest; Export's `logs/fml-client-latest.log` then has an ERROR line `autoexport: no run: <why>`.
- `part_errors` is present only when a part failed, with one entry per failed part.
- `part_warnings` is present only when a part is `ok` but left something out. Today only `nei`: NEI dropped one or
  more recipe handlers while loading them (`counts.nei_lookup_errors` > 0; NEI names them only on stderr), so their
  recipes are missing from `nei/` although the lines still equal NEI's count. **Readers that need every handler
  check that `part_warnings.nei` is absent.**
- The `nei_*` counts and the `nei_dump` and `nei_copy` durations are present only when the NEI step called
  `dumpAll`; `nei_wait` also when NEI was not ready in time. `nei_handlers` and `nei_reported` are -1 when NEI logged
  no such line. `nei_wait` is the time from entering the world until the dump started (or the wait timed out);
  `nei_dump` is NEI's call, during which the game is frozen; `nei_copy` the copy into `nei/`.

## Python readers (WO-1.3; named here only)

- `core/gregtec/ingest/gtdump.py`: `gt_recipe_maps.json`, `gt_recipes.jsonl`, `items.jsonl`, `fluids.jsonl`.
- `core/gregtec/ingest/machines.py`: `gt_machines.jsonl`.
- `core/gregtec/ingest/nei.py`: `nei/*.jsonl`, only from runs with `parts.nei == "ok"`.
