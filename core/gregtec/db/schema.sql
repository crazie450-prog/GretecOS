-- Gregtec OS SQLite schema, version 6 (plan section 5.3, Appendix D; WO-1.3, WO-1.5, WO-1.10, WO-4.1, WO-10.3).
-- gregtec.db.access applies this file to GOS_HOME/data/gos.sqlite when the DB's user_version is
-- older than this file's (a new DB is 0): every statement is IF NOT EXISTS, so it only adds what
-- is missing. PRAGMA user_version records the version; a DB newer than this file is refused.
-- name_fts.kind is UNINDEXED, so a search for "fluid" or "item" matches names, not the kind.
--
-- Appendix D as written, plus (D107):
--   export_run.run_key   the collected run's id (GregtecLink run_id, the folder name)
--   recipe.content_hash  (plan v1.1, section 5.2) already in Appendix D
--   recipe.ref           the citation key r:<map>/<sig12>[-n], unique per run
--   indexes for loading and pruning runs (ix_recipe_ref, ix_alt, ix_fact, ix_mm_map)
-- Version 2 (D112, WO-1.5): the quest tables gain what the frontier needs (quest_line descr and
-- visibility; quest task_logic, is_global, repeat_time), plus quest_line_entry (a quest can sit in
-- several lines), quest_item (every stack a task asks for or a reward gives, by item key) and
-- quest_state (each quest's state for the tracked player). migrations/2.sql drops version 1's
-- quest and quest_line, which nothing had written to, so this file can create them anew.
-- Version 3 (D134, WO-1.10): the crop tables (CropsNH), one set per export run like recipes.
-- Version 4 (D230, WO-4.1): the power catalog tables (rotors, generators, fuels), one set per export
-- run; new tables only, so no migration file.
-- Version 5 (D240, WO-4.5): the storage tables (cables, batteries, battery buffers, transformers,
-- LSC cells), one set per export run; new tables only, so no migration file.
-- Version 6 (D307, WO-10.3): the block catalog (block_state, block_texture), one set per export
-- run; new tables only, so no migration file.

-- Base tables [A§5.4]
CREATE TABLE IF NOT EXISTS export_run(id INTEGER PRIMARY KEY, created_at TEXT, pack_version TEXT,
  gt_version TEXT, nei_version TEXT, fingerprint TEXT NOT NULL, sources_json TEXT, notes TEXT,
  run_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS mod(run_id INT, modid TEXT, version TEXT, jar TEXT, jar_sha256 TEXT,
  PRIMARY KEY(run_id, modid));
CREATE TABLE IF NOT EXISTS item(id INTEGER PRIMARY KEY, registry_name TEXT NOT NULL, meta INT NOT NULL,
  nbt_hash TEXT NOT NULL DEFAULT '', nbt_json TEXT, mod_id TEXT, name_en TEXT, name_zh TEXT, unlocalized TEXT,
  gt_prefix TEXT, gt_material TEXT, gt_material_amount INT, UNIQUE(registry_name, meta, nbt_hash));
CREATE TABLE IF NOT EXISTS fluid(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, name_en TEXT,
  temperature INT, density INT, viscosity INT, gaseous INT);
CREATE TABLE IF NOT EXISTS ore(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS ore_member(ore_id INT, item_id INT, PRIMARY KEY(ore_id, item_id));
CREATE TABLE IF NOT EXISTS recipe_map(id INTEGER PRIMARY KEY, unloc_name TEXT UNIQUE, name_en TEXT,
  owner_mod TEXT, amperage INT, backend_class TEXT, nei_visible INT, max_item_in INT, max_item_out INT,
  max_fluid_in INT, max_fluid_out INT);
CREATE TABLE IF NOT EXISTS nei_handler(id INTEGER PRIMARY KEY, handler_name TEXT UNIQUE, name_en TEXT,
  mod TEXT, recipe_map_id INT);
CREATE TABLE IF NOT EXISTS recipe(id INTEGER PRIMARY KEY, run_id INT NOT NULL,
  source TEXT CHECK(source IN ('gt','crafting','furnace','nei','gt+nei')),
  recipe_map_id INT, nei_handler_id INT, category TEXT, eut INT, duration_ticks INT, amperage INT,
  voltage_tier INT, special_value INT, enabled INT, hidden INT, fake INT, needs_empty_output INT,
  nbt_sensitive INT, shaped_w INT, shaped_h INT, signature TEXT NOT NULL, content_hash TEXT NOT NULL,
  owners TEXT, ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recipe_input(recipe_id INT, slot INT, item_id INT, fluid_id INT, ore_id INT,
  amount INT NOT NULL, consumed INT NOT NULL DEFAULT 1, chance_bp INT NOT NULL DEFAULT 10000,
  circuit INT, PRIMARY KEY(recipe_id, slot));
CREATE TABLE IF NOT EXISTS recipe_input_alt(recipe_id INT, slot INT, item_id INT, fluid_id INT);
CREATE TABLE IF NOT EXISTS recipe_output(recipe_id INT, slot INT, item_id INT, fluid_id INT,
  amount INT NOT NULL, chance_bp INT NOT NULL DEFAULT 10000, PRIMARY KEY(recipe_id, slot));
CREATE TABLE IF NOT EXISTS recipe_meta(recipe_id INT, key TEXT, value_num REAL, value_text TEXT,
  PRIMARY KEY(recipe_id, key));
CREATE TABLE IF NOT EXISTS assembly_line_research(output_item_id INT, research_item_id INT, time INT,
  voltage INT);
CREATE TABLE IF NOT EXISTS machine(id INTEGER PRIMARY KEY, mte_id INT UNIQUE, class_name TEXT, mod_id TEXT,
  item_id INT, name_en TEXT, is_multiblock INT, tier INT, tooltip TEXT, structure_tooltip TEXT);
CREATE TABLE IF NOT EXISTS machine_map(machine_id INT, recipe_map_id INT, priority INT,
  PRIMARY KEY(machine_id, recipe_map_id));
CREATE TABLE IF NOT EXISTS machine_fact(machine_id INT, key TEXT, value TEXT, unit TEXT,
  source TEXT CHECK(source IN ('tooltip','curated','probe','code')), confidence REAL, note TEXT);
CREATE TABLE IF NOT EXISTS structure(machine_id INT, variant TEXT, size_x INT, size_y INT, size_z INT,
  blocks_json TEXT, schematic_path TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_out_item ON recipe_output(item_id);
CREATE INDEX IF NOT EXISTS ix_out_fluid ON recipe_output(fluid_id);
CREATE INDEX IF NOT EXISTS ix_in_item ON recipe_input(item_id);
CREATE INDEX IF NOT EXISTS ix_in_fluid ON recipe_input(fluid_id);
CREATE INDEX IF NOT EXISTS ix_sig ON recipe(signature);
CREATE UNIQUE INDEX IF NOT EXISTS ix_recipe_ref ON recipe(run_id, ref);
CREATE INDEX IF NOT EXISTS ix_alt ON recipe_input_alt(recipe_id, slot);
CREATE INDEX IF NOT EXISTS ix_fact ON machine_fact(machine_id);
CREATE INDEX IF NOT EXISTS ix_mm_map ON machine_map(recipe_map_id);
CREATE VIRTUAL TABLE IF NOT EXISTS name_fts USING fts5(kind UNINDEXED, ref_id UNINDEXED, name_en, name_zh,
  registry_name);

-- Tables added by GOS
-- quests (BetterQuesting, world copy; WO-1.5, D112). Ids are BQ's compact ids, cited q:<id>.
CREATE TABLE IF NOT EXISTS quest_line(id TEXT PRIMARY KEY, name TEXT, ord INT, descr TEXT,
  visibility TEXT);
CREATE TABLE IF NOT EXISTS quest_line_entry(line_id TEXT, quest_id TEXT, x INT, y INT, size_x INT,
  size_y INT, PRIMARY KEY(line_id, quest_id));
CREATE TABLE IF NOT EXISTS quest(id TEXT PRIMARY KEY, line_id TEXT, name TEXT, descr TEXT, logic TEXT,
  visibility TEXT, main INT, raw_json TEXT, task_logic TEXT, is_global INT, repeat_time INT);
CREATE TABLE IF NOT EXISTS quest_prereq(quest_id TEXT, prereq_id TEXT, PRIMARY KEY(quest_id, prereq_id));
CREATE TABLE IF NOT EXISTS quest_task(quest_id TEXT, idx INT, kind TEXT, item_id INT, fluid_id INT,
  amount INT, consume INT, raw_json TEXT, PRIMARY KEY(quest_id, idx));
CREATE TABLE IF NOT EXISTS quest_reward(quest_id TEXT, idx INT, kind TEXT, item_id INT, amount INT,
  raw_json TEXT, PRIMARY KEY(quest_id, idx));
CREATE TABLE IF NOT EXISTS quest_item(quest_id TEXT, part TEXT CHECK(part IN ('task','reward')), idx INT,
  slot INT, registry_name TEXT, meta INT, nbt_hash TEXT, ore TEXT, fluid TEXT, amount INT, item_id INT,
  fluid_id INT, PRIMARY KEY(quest_id, part, idx, slot));
CREATE INDEX IF NOT EXISTS ix_quest_item ON quest_item(registry_name, meta);
CREATE INDEX IF NOT EXISTS ix_quest_fluid ON quest_item(fluid);
CREATE TABLE IF NOT EXISTS quest_progress(player_uuid TEXT, quest_id TEXT, completed_ts INT, claimed INT,
  PRIMARY KEY(player_uuid, quest_id));
CREATE TABLE IF NOT EXISTS quest_state(player_uuid TEXT, quest_id TEXT,
  state TEXT CHECK(state IN ('done','repeatable','available','locked')), shown INT,
  PRIMARY KEY(player_uuid, quest_id));
CREATE INDEX IF NOT EXISTS ix_quest_state ON quest_state(player_uuid, state);
CREATE VIRTUAL TABLE IF NOT EXISTS quest_fts USING fts5(quest_id UNINDEXED, name, descr);
-- sessions and events
CREATE TABLE IF NOT EXISTS session(id INTEGER PRIMARY KEY, start_ts INT, end_ts INT, world TEXT,
  journal_path TEXT);
CREATE TABLE IF NOT EXISTS event(id INTEGER PRIMARY KEY, ts INT NOT NULL, session_id INT, kind TEXT NOT NULL,
  dim INT, x INT, y INT, z INT, ref TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_event_ts ON event(ts);
CREATE INDEX IF NOT EXISTS ix_event_kind ON event(kind, ts);
-- telemetry and actions
CREATE TABLE IF NOT EXISTS tel_machine(id TEXT PRIMARY KEY, name TEXT, mte_id INT, dim INT, x INT, y INT,
  z INT, line_id TEXT, last_seen INT, status TEXT);
CREATE TABLE IF NOT EXISTS tel_sample(ts INT, src TEXT, key TEXT, value REAL, text TEXT);
CREATE INDEX IF NOT EXISTS ix_tel ON tel_sample(src, key, ts);
CREATE TABLE IF NOT EXISTS action(id INTEGER PRIMARY KEY, created INT, kind TEXT, args TEXT,
  state TEXT CHECK(state IN ('proposed','confirmed','sent','done','failed','cancelled','expired')),
  confirmed_via TEXT, confirmed_ts INT, result TEXT);
-- plans (pins live in the plan JSON)
CREATE TABLE IF NOT EXISTS plan(id TEXT PRIMARY KEY, name TEXT, created INT,
  kind TEXT CHECK(kind IN ('production','power')), target TEXT, objective TEXT, status TEXT,
  json_path TEXT, metrics TEXT, gt_version TEXT, run_id INT,
  affected_run INT);   -- export run that changed or removed a cited recipe (QA:D9)
-- My base (D63, D64)
CREATE TABLE IF NOT EXISTS base_machine(machine_id INT PRIMARY KEY, count INT,
  source TEXT CHECK(source IN ('manual','event','census')), upd INT);
CREATE TABLE IF NOT EXISTS base_stock(item_id INT, fluid_id INT, qty INT, upd INT,
  PRIMARY KEY(item_id, fluid_id));
-- ore
CREATE TABLE IF NOT EXISTS ore_mix(id TEXT PRIMARY KEY, name TEXT, dims TEXT, ymin INT, ymax INT,
  weight INT, density INT, size INT, ores TEXT);
CREATE TABLE IF NOT EXISTS vein(dim INT, cx INT, cz INT, mix_id TEXT, known INT, depleted INT, src TEXT,
  PRIMARY KEY(dim, cx, cz));
-- crops (CropsNH; WO-1.10, D134): from the run's crops/ (CropsNH's NEI dumpers) and NEI's CropsNH
-- tabs, one set per export run (run_id, deleted with the run). A crop is CropsNH's id (e.g.
-- cropsnh:allium), cited ref = crop:<id>. JSON columns: soil_lists, liked_biomes and
-- alternate_seeds are lists of names, requirements a list of [kind, value, text], sub_soils a
-- list of [registry_name, meta, nbt_hash]. in_nei: shown in NEI's tab (NULL: no NEI data).
-- Items are registry name + meta (+ NBT hash); item_id when the catalog has the item. Blocks
-- (soils) are registry name + meta, meta NULL for any meta. A parent recipe's key is
-- <child>< + its parents sorted and joined by +.
CREATE TABLE IF NOT EXISTS crop(run_id INT NOT NULL, id TEXT NOT NULL, ref TEXT NOT NULL, name TEXT,
  unlocalized TEXT, flavour TEXT, creator TEXT, soil TEXT, soil_lists TEXT, tier INT,
  min_seed_bed_tier INT, liked_biomes TEXT, growth_duration INT, breeding_threshold REAL,
  crossing_threshold REAL, weeds_above INT, drop_multiplier REAL, requirements TEXT,
  alternate_seeds TEXT, sub_soils TEXT, in_nei INT, PRIMARY KEY(run_id, id));
CREATE TABLE IF NOT EXISTS crop_drop(run_id INT NOT NULL, crop_id TEXT NOT NULL, slot INT NOT NULL,
  amount INT NOT NULL, chance_bp INT NOT NULL, name TEXT, nbt TEXT, registry_name TEXT, meta INT,
  nbt_hash TEXT, item_id INT, PRIMARY KEY(run_id, crop_id, slot));
CREATE INDEX IF NOT EXISTS ix_crop_drop_item ON crop_drop(registry_name, meta);
CREATE TABLE IF NOT EXISTS crop_seed(run_id INT NOT NULL, slot INT NOT NULL, registry_name TEXT NOT NULL,
  meta INT, crop_id TEXT NOT NULL, item_id INT, PRIMARY KEY(run_id, slot));   -- alternate seeds
CREATE INDEX IF NOT EXISTS ix_crop_seed ON crop_seed(registry_name, meta);
CREATE TABLE IF NOT EXISTS crop_parent_recipe(run_id INT NOT NULL, key TEXT NOT NULL, child TEXT NOT NULL,
  requirements TEXT, machine_only INT, world_only INT, in_nei INT, PRIMARY KEY(run_id, key));
CREATE INDEX IF NOT EXISTS ix_crop_child ON crop_parent_recipe(run_id, child);
CREATE TABLE IF NOT EXISTS crop_parent(run_id INT NOT NULL, recipe_key TEXT NOT NULL, slot INT NOT NULL,
  crop_id TEXT NOT NULL, PRIMARY KEY(run_id, recipe_key, slot));
CREATE INDEX IF NOT EXISTS ix_crop_parent ON crop_parent(run_id, crop_id);
CREATE TABLE IF NOT EXISTS crop_mutation(run_id INT NOT NULL, pool TEXT NOT NULL, crop_id TEXT NOT NULL,
  PRIMARY KEY(run_id, pool, crop_id));   -- mutation pool membership; pools by display name
CREATE INDEX IF NOT EXISTS ix_crop_mutation ON crop_mutation(run_id, crop_id);
CREATE TABLE IF NOT EXISTS crop_soil(run_id INT NOT NULL, soil TEXT NOT NULL, slot INT NOT NULL,
  registry_name TEXT, meta INT, PRIMARY KEY(run_id, soil, slot));   -- registry_name NULL: no block
-- power catalog (WO-4.1, D230). From gt_power.json (runs with parts.power = ok) and the run's fuel
-- maps. rotor_size: GT's four turbine rotor sizes by the turbine item's meta (170 small, 172
-- normal, 174 large, 176 huge); rotor_material: every material GT makes turbine blades of, its
-- tool stats as the instance has them (floats are GT's float values); rotor_gt: what GT's own
-- TurbineStatCalculator gave for each new rotor in the game (key = the stat), for checks;
-- generator: every single-block generator's fuel efficiency (%), voltage and amps out;
-- generator_gt: what GT's own generator gives for each fuel of its map (eu per operation of
-- `consumed` litres for a fluid, item_eu per item), for checks; fuel_container: the fluid in each
-- fluid container a fuel map lists. fuel: every fuel recipe of GT's fuel maps, decoded by its map's
-- rule (gregtec.planner.fuels): `fluid` names the fluid it burns (the container's for a cell),
-- fuel_value is GT's FUEL_VALUE raw, value is it in `unit`; ref cites the recipe. hidden: GT's fuel
-- map hid it as a second fuel of the same fluid (its fluid lookup uses the other; an item lookup
-- still finds it), so a fluid's fuel is its row with hidden = 0.
CREATE TABLE IF NOT EXISTS rotor_size(run_id INT NOT NULL, size INT NOT NULL, name TEXT NOT NULL,
  speed_multiplier REAL NOT NULL, base_damage REAL NOT NULL, durability_multiplier REAL NOT NULL,
  PRIMARY KEY(run_id, size));
CREATE TABLE IF NOT EXISTS rotor_material(run_id INT NOT NULL, name TEXT NOT NULL, tool_speed REAL NOT NULL,
  tool_quality INT NOT NULL, durability INT NOT NULL, steam_multiplier REAL NOT NULL,
  gas_multiplier REAL NOT NULL, plasma_multiplier REAL NOT NULL, PRIMARY KEY(run_id, name));
CREATE TABLE IF NOT EXISTS rotor_gt(run_id INT NOT NULL, material TEXT NOT NULL, size INT NOT NULL,
  key TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY(run_id, material, size, key));
CREATE TABLE IF NOT EXISTS generator(run_id INT NOT NULL, mte_id INT NOT NULL, class_name TEXT NOT NULL,
  tier INT, efficiency INT NOT NULL, eu_out INT NOT NULL, amps_out INT NOT NULL, recipe_map TEXT,
  PRIMARY KEY(run_id, mte_id));
CREATE TABLE IF NOT EXISTS generator_gt(run_id INT NOT NULL, mte_id INT NOT NULL, special INT NOT NULL,
  fluid TEXT, eu INT, eu_long INT, consumed INT, registry_name TEXT, meta INT, item_eu INT,
  item_eu_long INT);
CREATE INDEX IF NOT EXISTS ix_generator_gt ON generator_gt(run_id, mte_id);
CREATE TABLE IF NOT EXISTS fuel_container(run_id INT NOT NULL, registry_name TEXT NOT NULL, meta INT NOT NULL,
  nbt_hash TEXT NOT NULL, fluid TEXT NOT NULL, mb INT NOT NULL, PRIMARY KEY(run_id, registry_name, meta, nbt_hash));
CREATE TABLE IF NOT EXISTS fuel(run_id INT NOT NULL, recipe_id INT NOT NULL, ref TEXT NOT NULL,
  map TEXT NOT NULL, gt_name TEXT NOT NULL, input TEXT NOT NULL, fluid TEXT, fluid_input INT NOT NULL,
  hidden INT NOT NULL, fuel_value INT NOT NULL, unit TEXT NOT NULL, value REAL NOT NULL,
  PRIMARY KEY(run_id, recipe_id));
CREATE INDEX IF NOT EXISTS ix_fuel_fluid ON fuel(run_id, fluid);
-- storage and distribution (WO-4.5, D240). From gt_power.json's storage. cable: every GT and GT++
-- cable's own fields (mVoltage, mAmperage, mCableLossPerMeter: EU per metre per amp; runs before
-- it have none, and gregtec.planner.distribution falls back to machine.tooltip); battery: every
-- battery* oredict member that is an IC2 electric item (ores: a JSON list of those names; a
-- wildcard member at meta 0), its tier, max charge (EU), transfer limit (EU/t) and whether it
-- takes a charge (a simulated charge; GT's one-use ZPM module doesn't); battery_buffer: every GT
-- battery buffer, its slots, voltage in/out and own store; transformer: every GT transformer
-- (GT's, its power and wet transformers, GT++'s hi-amp ones), one row per mode (down: GT's
-- default, in at the tier above; up: soft-mallet'd); lsc_cell: the LSC's cells, their block,
-- glass tier and capacity (EU, a decimal string: some are past a 64-bit integer's range).
CREATE TABLE IF NOT EXISTS cable(run_id INT NOT NULL, mte_id INT NOT NULL, voltage INT NOT NULL,
  amperage INT NOT NULL, loss INT NOT NULL, PRIMARY KEY(run_id, mte_id));
CREATE TABLE IF NOT EXISTS battery(run_id INT NOT NULL, registry_name TEXT NOT NULL, meta INT NOT NULL,
  nbt_hash TEXT NOT NULL, ores TEXT NOT NULL, tier INT NOT NULL, max_charge REAL NOT NULL,
  transfer_limit REAL NOT NULL, rechargeable INT NOT NULL, PRIMARY KEY(run_id, registry_name, meta, nbt_hash));
CREATE TABLE IF NOT EXISTS battery_buffer(run_id INT NOT NULL, mte_id INT NOT NULL, class_name TEXT NOT NULL,
  tier INT NOT NULL, slots INT NOT NULL, eu_in INT NOT NULL, eu_out INT NOT NULL, eu_store INT NOT NULL,
  PRIMARY KEY(run_id, mte_id));
CREATE TABLE IF NOT EXISTS transformer(run_id INT NOT NULL, mte_id INT NOT NULL, class_name TEXT NOT NULL,
  tier INT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('down','up')), eu_in INT NOT NULL,
  amps_in INT NOT NULL, eu_out INT NOT NULL, amps_out INT NOT NULL, PRIMARY KEY(run_id, mte_id, mode));
CREATE TABLE IF NOT EXISTS lsc_cell(run_id INT NOT NULL, name TEXT NOT NULL, registry_name TEXT NOT NULL,
  meta INT NOT NULL, glass_tier INT NOT NULL, capacity TEXT NOT NULL, PRIMARY KEY(run_id, name));
-- block catalog (WO-10.3, D307). From palette.jsonl and textures.jsonl (runs with parts.palette =
-- ok). block_state: every placeable block state, keyed by the block's registry name and the meta
-- it is placed at, one row per item variant that places it (item_name@item_meta); has_te is
-- Forge's hasTileEntity(meta) (a shell never holds such a block, plan 7.10); hidden: in no
-- creative tab; faces: a JSON list of the six faces' icon names (down, up, north, south, west,
-- east). block_texture: each icon name's PNG in the instance's resource packs and its average
-- colour (#rrggbb, alpha 0..255); error when it didn't resolve. Never a numeric block or item id.
CREATE TABLE IF NOT EXISTS block_state(run_id INT NOT NULL, registry_name TEXT NOT NULL, meta INT NOT NULL,
  item_name TEXT NOT NULL, item_meta INT NOT NULL, name TEXT, unlocalized TEXT, mod_id TEXT NOT NULL,
  has_te INT NOT NULL, opaque INT, full_cube INT, normal_cube INT, standard_render INT, liquid INT,
  falls INT NOT NULL, light INT, light_opacity INT, tab TEXT, hidden INT NOT NULL, faces TEXT NOT NULL,
  errors TEXT, PRIMARY KEY(run_id, registry_name, meta, item_name, item_meta));
CREATE INDEX IF NOT EXISTS ix_block_state_mod ON block_state(run_id, mod_id);
CREATE TABLE IF NOT EXISTS block_texture(run_id INT NOT NULL, icon TEXT NOT NULL, path TEXT NOT NULL,
  sha256 TEXT, width INT, height INT, rgb TEXT, alpha INT, error TEXT, PRIMARY KEY(run_id, icon));
-- wiki, notes, usage
CREATE TABLE IF NOT EXISTS wiki_page(title TEXT PRIMARY KEY, revid INT, fetched INT, url TEXT, text TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(title, text, content='wiki_page');
CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(path UNINDEXED, t, upd UNINDEXED, body);
CREATE TABLE IF NOT EXISTS llm_usage(ts INT, lane TEXT, model TEXT, in_tok INT, out_tok INT,
  cache_read INT, cache_write INT, usd REAL, task TEXT, ttft_ms INT);
-- devices, alerts and small app state
CREATE TABLE IF NOT EXISTS device(id TEXT PRIMARY KEY, name TEXT, paired INT, last_seen INT,
  revoked INT);   -- QA:A8
CREATE TABLE IF NOT EXISTS alert(id INTEGER PRIMARY KEY, ts INT, kind TEXT, level TEXT, msg TEXT,
  ref TEXT, state TEXT, acked_ts INT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, upd INT);   -- stale flag, tier override, credit state, log offsets

PRAGMA user_version = 6;
