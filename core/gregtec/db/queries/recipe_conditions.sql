-- The metadata of the recipes in :ids (a JSON array of recipe ids): GT's own keys (coil_heat,
-- cleanroom, low_gravity, fuel_value, ...), special items and NEI container items, raw (D107).
-- nei_desc (display text) and can_be_buffered (how GT caches the recipe) are left out.
SELECT recipe_id, key, value_num, value_text
FROM recipe_meta
WHERE recipe_id IN (SELECT value FROM json_each(:ids))
  AND key NOT IN ('nei_desc', 'can_be_buffered')
ORDER BY recipe_id, key;
