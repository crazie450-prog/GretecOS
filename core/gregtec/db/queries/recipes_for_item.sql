-- Recipes of export run :run_id that output item :item_id or fluid :fluid_id (the other NULL),
-- cited by ref (WO-2.2's recipe_find), with the map's NEI name (else the first machine that runs
-- it, else its unlocalized name) as machine. Without :all = 1, disabled and fake recipes are left out;
-- fake NULL (an NEI-only handler that doesn't say, D105) stays in. :tier_max (a GT voltage tier)
-- keeps recipes up to that tier and those with none (crafting, furnace); :maps and :handlers are
-- JSON arrays of recipe_map and nei_handler ids, or NULL for any machine. GT's recycling recipes
-- (categories gt.recipe.category.*_recycling: an item back to its material) last, then lowest
-- tier first, no tier (crafting) before tier 0, then by machine and ref; total counts every match.
SELECT r.id, r.ref, r.source,
  coalesce(m.name_en, h.name_en, (
    SELECT mc.name_en FROM machine_map AS mm JOIN machine AS mc ON mc.id = mm.machine_id
    WHERE mm.recipe_map_id = r.recipe_map_id ORDER BY mm.priority, mc.mte_id LIMIT 1),
    m.unloc_name, h.handler_name) AS machine,
  m.unloc_name AS map, r.voltage_tier, r.eut, r.duration_ticks, r.amperage, r.special_value,
  r.enabled, r.hidden, r.fake, r.category,
  coalesce(r.category, '') LIKE 'gt.recipe.category.%recycling' AS recycling,
  count(*) OVER () AS total
FROM recipe AS r
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
WHERE r.run_id = :run_id
  AND r.id IN (
    SELECT o.recipe_id FROM recipe_output AS o WHERE o.item_id = :item_id
    UNION SELECT o.recipe_id FROM recipe_output AS o WHERE o.fluid_id = :fluid_id)
  AND (:all = 1 OR (r.enabled = 1 AND coalesce(r.fake, 0) = 0))
  AND (:tier_max IS NULL OR r.voltage_tier IS NULL OR r.voltage_tier <= :tier_max)
  AND (:maps IS NULL
    OR r.recipe_map_id IN (SELECT value FROM json_each(:maps))
    OR r.nei_handler_id IN (SELECT value FROM json_each(:handlers)))
ORDER BY recycling, r.voltage_tier IS NOT NULL, r.voltage_tier, machine, r.ref
LIMIT :limit OFFSET :offset;
