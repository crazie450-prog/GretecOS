-- The recipe maps and NEI handlers whose name, or the name of a machine that runs them, matches
-- :q (FTS5 query syntax): kind 'map' (recipe_map.id) or 'handler' (nei_handler.id).
SELECT 'map' AS kind, f.ref_id AS id
FROM name_fts AS f
WHERE name_fts MATCH :q AND f.kind = 'recipe_map'
UNION
SELECT 'handler', f.ref_id
FROM name_fts AS f
WHERE name_fts MATCH :q AND f.kind = 'nei_handler'
UNION
SELECT 'map', mm.recipe_map_id
FROM name_fts AS f
JOIN machine_map AS mm ON mm.machine_id = f.ref_id
WHERE name_fts MATCH :q AND f.kind = 'machine'
ORDER BY 1, 2;
