-- Veins whose ore mix has :material in one of its layers (WO-1.6, D128; plan 7.6 ore_find).
-- :material is a GT material name or its display name, any case ("Naquadah", "tricalcium
-- phosphate"); :dim one dimension's veins, or NULL for every dimension; :x and :z the block
-- position in :dim to measure from, or NULL; at most :limit rows (-1: all). Rows: the vein's
-- centre block (x, z), dist2 (its squared horizontal distance from :x, :z, or NULL;
-- gregtec.db.ores.find turns it into blocks), the mix, the layers the material is in, the
-- mix's heights in this dimension, and known (in the player's client cache), depleted, src.
-- Live veins first, then depleted ones, each nearest first, then by dimension and chunk.
WITH hit AS (
  SELECT m.*, (
    SELECT group_concat(json_extract(o.value, '$.layer'), ',')
    FROM json_each(m.ores) AS o
    WHERE lower(json_extract(o.value, '$.material')) = lower(:material)
       OR lower(json_extract(o.value, '$.name')) = lower(:material)
  ) AS layers
  FROM ore_mix AS m
)
SELECT v.dim, v.cx * 16 + 8 AS x, v.cz * 16 + 8 AS z,
  CASE WHEN :x IS NULL OR :z IS NULL THEN NULL
    ELSE (v.cx * 16 + 8 - :x) * (v.cx * 16 + 8 - :x)
      + (v.cz * 16 + 8 - :z) * (v.cz * 16 + 8 - :z)
  END AS dist2,
  h.id AS mix_id, h.name AS mix, h.layers,
  coalesce((SELECT json_extract(d.value, '$.ymin') FROM json_each(h.dims) AS d,
    json_each(d.value, '$.ids') AS i WHERE i.value = v.dim), h.ymin) AS ymin,
  coalesce((SELECT json_extract(d.value, '$.ymax') FROM json_each(h.dims) AS d,
    json_each(d.value, '$.ids') AS i WHERE i.value = v.dim), h.ymax) AS ymax,
  v.known, v.depleted, v.src
FROM vein AS v
JOIN hit AS h ON h.id = v.mix_id
WHERE h.layers IS NOT NULL AND (:dim IS NULL OR v.dim = :dim)
ORDER BY v.depleted, dist2 IS NULL, dist2, v.dim, v.cx, v.cz
LIMIT :limit;
