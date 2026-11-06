-- The veins of dimension :dim (WO-1.6, D128), west to east, then north to south, at most :limit
-- rows (-1: all): the centre block (x, z), the mix and its ores in layer order (primary,
-- secondary, between, sporadic), the mix's heights in this dimension, and known (in the
-- player's client cache), depleted, src. A vein whose mix the loaded export lacks keeps its name.
SELECT v.cx * 16 + 8 AS x, v.cz * 16 + 8 AS z, v.mix_id, coalesce(m.name, v.mix_id) AS mix,
  (SELECT group_concat(
      coalesce(json_extract(o.value, '$.name'), json_extract(o.value, '$.material')), ', ')
    FROM json_each(m.ores) AS o) AS ores,
  coalesce((SELECT json_extract(d.value, '$.ymin') FROM json_each(m.dims) AS d,
    json_each(d.value, '$.ids') AS i WHERE i.value = v.dim), m.ymin) AS ymin,
  coalesce((SELECT json_extract(d.value, '$.ymax') FROM json_each(m.dims) AS d,
    json_each(d.value, '$.ids') AS i WHERE i.value = v.dim), m.ymax) AS ymax,
  v.known, v.depleted, v.src
FROM vein AS v
LEFT JOIN ore_mix AS m ON m.id = v.mix_id
WHERE v.dim = :dim
ORDER BY v.cx, v.cz
LIMIT :limit;
