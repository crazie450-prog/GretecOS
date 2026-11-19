-- The metadata of the recipes in :ids (a JSON array of recipe ids): GT's own keys (coil_heat,
-- cleanroom, low_gravity, fuel_value, ...), special items and NEI container items, raw (D107).
-- nei_desc (display text), can_be_buffered (how GT caches the recipe) and unkeyed (the export's
-- marker for a recipe with no input it uses up, D364: not a condition) are left out.
SELECT recipe_id, key, value_num, value_text
FROM recipe_meta
WHERE recipe_id IN (SELECT value FROM json_each(:ids))
  AND key NOT IN ('nei_desc', 'can_be_buffered', 'unkeyed')
ORDER BY recipe_id, key;
