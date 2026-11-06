-- Recipes of export run :run_id that output an item or fluid whose name matches :q (FTS5 query
-- syntax), by citation key, at most :limit. Planner defaults: enabled and not fake (plan 5.2).
SELECT r.ref, f.kind, f.name_en AS output, o.amount, o.chance_bp, r.source, r.eut,
  r.duration_ticks, r.voltage_tier
FROM name_fts AS f
JOIN recipe_output AS o
  ON (f.kind = 'item' AND o.item_id = f.ref_id) OR (f.kind = 'fluid' AND o.fluid_id = f.ref_id)
JOIN recipe AS r ON r.id = o.recipe_id
WHERE name_fts MATCH :q AND r.run_id = :run_id AND r.enabled = 1 AND r.fake = 0
ORDER BY r.ref, o.slot
LIMIT :limit;
