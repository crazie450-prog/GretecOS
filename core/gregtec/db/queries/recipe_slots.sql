-- The inputs and outputs of the recipes in :ids (a JSON array of recipe ids), slot by slot, for
-- WO-2.2's recipe cards: part 'in' or 'out', the item or fluid (key as the tools name it, and its
-- name), amount (items, or mB for fluids), chance in basis points, consumed and circuit (inputs),
-- the slot's oredict name and how many other stacks it accepts (recipe_input_alt).
SELECT s.recipe_id, s.part, s.slot, s.amount, s.chance_bp, s.consumed, s.circuit,
  o.name AS ore, s.fluid_id IS NOT NULL AS is_fluid,
  CASE WHEN s.fluid_id IS NOT NULL THEN 'fluid:' || f.name
    ELSE i.registry_name || '@' || i.meta
      || CASE WHEN i.nbt_hash != '' THEN '#' || substr(i.nbt_hash, 1, 8) ELSE '' END
  END AS key,
  coalesce(i.name_en, f.name_en, '') AS name,
  CASE WHEN s.part = 'in' THEN (
    SELECT count(*) FROM recipe_input_alt AS a
    WHERE a.recipe_id = s.recipe_id AND a.slot = s.slot
      AND NOT (a.item_id IS s.item_id AND a.fluid_id IS s.fluid_id)) ELSE 0 END AS alts
FROM (
  SELECT recipe_id, 'in' AS part, slot, item_id, fluid_id, ore_id, amount, consumed, chance_bp,
    circuit
  FROM recipe_input WHERE recipe_id IN (SELECT value FROM json_each(:ids))
  UNION ALL
  SELECT recipe_id, 'out', slot, item_id, fluid_id, NULL, amount, 1, chance_bp, NULL
  FROM recipe_output WHERE recipe_id IN (SELECT value FROM json_each(:ids))
) AS s
LEFT JOIN item AS i ON i.id = s.item_id
LEFT JOIN fluid AS f ON f.id = s.fluid_id
LEFT JOIN ore AS o ON o.id = s.ore_id
ORDER BY s.recipe_id, s.part, s.slot;
