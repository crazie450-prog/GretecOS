-- Recipes of export run :run_id that take an item in :items (a JSON array of item ids: the item
-- and its wildcard-meta row), fluid :fluid_id, or any member of an oredict in :ores (a JSON
-- array of ore ids), shown or as an accepted alternative, consumed or not (WO-2.2's
-- recipe_uses). Without :all = 1, disabled and fake recipes are left out (fake NULL stays in, as
-- in recipes_for_item). By machine, then lowest tier, then ref; total counts every match.
SELECT r.id, r.ref, r.source, coalesce(m.name_en, h.name_en, m.unloc_name, h.handler_name) AS machine,
  m.unloc_name AS map, r.voltage_tier, r.eut, r.duration_ticks, r.amperage, r.special_value,
  r.enabled, r.hidden, r.fake, count(*) OVER () AS total
FROM recipe AS r
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
WHERE r.run_id = :run_id
  AND r.id IN (
    SELECT i.recipe_id FROM recipe_input AS i WHERE i.item_id IN (SELECT value FROM json_each(:items))
    UNION SELECT i.recipe_id FROM recipe_input AS i WHERE i.fluid_id = :fluid_id
    UNION SELECT i.recipe_id FROM recipe_input AS i WHERE i.ore_id IN (SELECT value FROM json_each(:ores))
    UNION SELECT a.recipe_id FROM recipe_input_alt AS a WHERE a.item_id IN (SELECT value FROM json_each(:items))
    UNION SELECT a.recipe_id FROM recipe_input_alt AS a WHERE a.fluid_id = :fluid_id)
  AND (:all = 1 OR (r.enabled = 1 AND coalesce(r.fake, 0) = 0))
ORDER BY machine, r.voltage_tier IS NOT NULL, r.voltage_tier, r.ref
LIMIT :limit OFFSET :offset;
