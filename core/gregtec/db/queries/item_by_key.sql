-- The items with registry name :registry and meta :meta whose NBT hash starts with :nbt ('' for
-- the item without NBT), as tools name them (registry@meta#<nbt8>). Several rows: the NBT prefix
-- is too short to tell them apart.
SELECT 'item' AS kind, i.id AS ref_id, coalesce(i.name_en, '') AS name,
  i.registry_name || '@' || i.meta
    || CASE WHEN i.nbt_hash != '' THEN '#' || substr(i.nbt_hash, 1, 8) ELSE '' END AS key,
  i.mod_id AS mod, i.gt_prefix, i.gt_material
FROM item AS i
WHERE i.registry_name = :registry AND i.meta = :meta
  AND CASE WHEN :nbt = '' THEN i.nbt_hash = '' ELSE substr(i.nbt_hash, 1, length(:nbt)) = :nbt END
ORDER BY i.nbt_hash
LIMIT 10;
