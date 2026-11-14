-- Block states of the block catalog (WO-10.3, D307; the architect's block_find): run :run's
-- block_state rows merged per registry name + meta (the items that place one state: variants),
-- in registry name, then meta order. :w1, :w2, :w3 are words that must each be in the display
-- name or the registry name, any case ('' for none; LIKE wildcards in them are escaped by the
-- caller with \); :mod one mod (the registry domain) or NULL; :plain 1 leaves out states with a
-- tile entity; :hidden 1 keeps states in no creative tab; :opaque 1 or 0 or NULL; :light_min a
-- light level or NULL. top_rgb and side_rgb are the average colours of the up and north faces'
-- textures. :limit rows from :offset.
WITH s AS (
  SELECT b.registry_name, b.meta,
    MIN(b.name) AS name,
    MIN(b.mod_id) AS mod,
    COUNT(*) AS variants,
    MAX(b.has_te) AS te,
    MIN(b.opaque) AS opaque,
    MIN(b.full_cube) AS full_cube,
    MAX(b.light) AS light,
    MAX(b.falls) AS falls,
    MAX(b.liquid) AS liquid,
    MIN(b.hidden) AS hidden,
    MIN(b.tab) AS tab,
    MIN(b.faces) AS faces
  FROM block_state AS b
  WHERE b.run_id = :run
    AND (:mod IS NULL OR lower(b.mod_id) = lower(:mod))
    AND (:w1 = '' OR coalesce(b.name, '') || ' ' || b.registry_name LIKE '%' || :w1 || '%' ESCAPE '\')
    AND (:w2 = '' OR coalesce(b.name, '') || ' ' || b.registry_name LIKE '%' || :w2 || '%' ESCAPE '\')
    AND (:w3 = '' OR coalesce(b.name, '') || ' ' || b.registry_name LIKE '%' || :w3 || '%' ESCAPE '\')
  GROUP BY b.registry_name, b.meta
  HAVING (:plain = 0 OR MAX(b.has_te) = 0)
    AND (:hidden = 1 OR MIN(b.hidden) = 0)
    AND (:opaque IS NULL OR MIN(b.opaque) = :opaque)
    AND (:light_min IS NULL OR MAX(b.light) >= :light_min)
)
SELECT s.registry_name || '@' || s.meta AS block, s.name, s.mod, s.variants, s.te, s.opaque,
  s.full_cube, s.light, s.falls, s.liquid, s.hidden, s.tab,
  (SELECT t.rgb FROM block_texture AS t
    WHERE t.run_id = :run AND t.icon = json_extract(s.faces, '$[1]')) AS top_rgb,
  (SELECT t.rgb FROM block_texture AS t
    WHERE t.run_id = :run AND t.icon = json_extract(s.faces, '$[2]')) AS side_rgb
FROM s
ORDER BY s.registry_name, s.meta
LIMIT :limit OFFSET :offset
