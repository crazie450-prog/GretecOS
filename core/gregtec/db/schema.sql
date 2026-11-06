-- Gregtec OS SQLite schema, version 2 (plan section 5.3, Appendix D; WO-1.3, WO-1.5).
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
-- wiki, notes, usage
CREATE TABLE IF NOT EXISTS wiki_page(title TEXT PRIMARY KEY, revid INT, fetched INT, url TEXT, text TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(title, text, content='wiki_page');
CREATE VIRTUAL TABLE IF NOT EXISTS note_fts USING fts5(path UNINDEXED, t, upd UNINDEXED, body);
CREATE TABLE IF NOT EXISTS llm_usage(ts INT, lane TEXT, model TEXT, in_tok INT, out_tok INT,
  cache_read INT, cache_write INT, usd REAL, task TEXT);
-- devices, alerts and small app state
CREATE TABLE IF NOT EXISTS device(id TEXT PRIMARY KEY, name TEXT, paired INT, last_seen INT,
  revoked INT);   -- QA:A8
CREATE TABLE IF NOT EXISTS alert(id INTEGER PRIMARY KEY, ts INT, kind TEXT, level TEXT, msg TEXT,
  ref TEXT, state TEXT, acked_ts INT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, upd INT);   -- stale flag, tier override, credit state, log offsets

PRAGMA user_version = 2;
