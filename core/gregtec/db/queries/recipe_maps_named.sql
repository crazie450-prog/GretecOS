-- The recipe maps and NEI handlers :name names exactly (any case): a map's unlocalized name or
-- the name NEI shows for it, an NEI handler's id or name, or a machine's name or mte:<mte_id>
-- (then every map it runs). kind is 'map' (recipe_map.id) or 'handler' (nei_handler.id).
SELECT 'map' AS kind, rm.id
FROM recipe_map AS rm
WHERE rm.unloc_name = :name COLLATE NOCASE OR rm.name_en = :name COLLATE NOCASE
UNION
SELECT 'handler', h.id
FROM nei_handler AS h
WHERE h.handler_name = :name COLLATE NOCASE OR h.name_en = :name COLLATE NOCASE
UNION
SELECT 'map', mm.recipe_map_id
FROM machine AS m
JOIN machine_map AS mm ON mm.machine_id = m.id
WHERE m.name_en = :name COLLATE NOCASE OR 'mte:' || m.mte_id = :name
ORDER BY 1, 2;
