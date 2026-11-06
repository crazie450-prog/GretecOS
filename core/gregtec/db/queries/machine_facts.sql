-- The facts the planner uses for machine :mte_id (GT's meta of gregtech:gt.blockmachines), one per
-- key: probe over curated over code over tooltip, then the highest confidence (WO-1.7, D109). A
-- key with a recipe map, parallel[gt.recipe.x], is its own row; prefer it on that map.
SELECT key, value, unit, source, confidence, note
FROM (
  SELECT f.*, row_number() OVER (
    PARTITION BY f.key
    ORDER BY CASE f.source WHEN 'probe' THEN 3 WHEN 'curated' THEN 2 WHEN 'code' THEN 1 ELSE 0 END
      DESC, f.confidence DESC
  ) AS pick
  FROM machine_fact AS f
  JOIN machine AS m ON m.id = f.machine_id
  WHERE m.mte_id = :mte_id
)
WHERE pick = 1
ORDER BY key;
