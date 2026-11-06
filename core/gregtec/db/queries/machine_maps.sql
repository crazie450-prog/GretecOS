-- The recipe maps machine :mte_id runs (machine_map), in GT's order: unlocalized name and the
-- name NEI shows for it.
SELECT rm.id, rm.unloc_name AS map, coalesce(rm.name_en, rm.unloc_name) AS name, mm.priority
FROM machine AS m
JOIN machine_map AS mm ON mm.machine_id = m.id
JOIN recipe_map AS rm ON rm.id = mm.recipe_map_id
WHERE m.mte_id = :mte_id
ORDER BY mm.priority, rm.unloc_name;
