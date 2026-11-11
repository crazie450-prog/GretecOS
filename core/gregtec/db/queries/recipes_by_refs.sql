-- The recipes of export run :run_id cited by the keys in :refs (a JSON array of r: ids), for the
-- dashboard's recipe cards (WO-2.6, D160): the columns recipes_for_item gives a recipe, machine
-- named the same way (the map's NEI name, else the first machine that runs it, else its
-- unlocalized name), in the order of :refs.
SELECT r.id, r.ref, r.source,
  coalesce(m.name_en, h.name_en, (
    SELECT mc.name_en FROM machine_map AS mm JOIN machine AS mc ON mc.id = mm.machine_id
    WHERE mm.recipe_map_id = r.recipe_map_id ORDER BY mm.priority, mc.mte_id LIMIT 1),
    m.unloc_name, h.handler_name) AS machine,
  m.unloc_name AS map, r.voltage_tier, r.eut, r.duration_ticks, r.amperage, r.special_value,
  r.enabled, r.hidden, r.fake, r.category,
  coalesce(r.category, '') LIKE 'gt.recipe.category.%recycling' AS recycling
FROM json_each(:refs) AS j
JOIN recipe AS r ON r.run_id = :run_id AND r.ref = j.value
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
ORDER BY j.key;
