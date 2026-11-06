-- Items and fluids (or, with :kind 'item', 'fluid' or 'machine', only those) whose name or
-- registry name matches :q (FTS5 query syntax), best bm25 first, at most :limit (WO-2.2's
-- item_resolve rescores them). key is how the tools name each one: registry@meta (#<nbt8> with
-- NBT, as normalize.Ref.text), fluid:<name> or mte:<mte_id>. A machine is also an item
-- (gregtech:gt.blockmachines@<mte_id>), so the default leaves machine rows out. An item's
-- wildcard row (meta 32767: "any meta" in a recipe slot) is never what a name means.
SELECT f.kind, f.ref_id, coalesce(f.name_en, '') AS name,
  CASE f.kind
    WHEN 'item' THEN i.registry_name || '@' || i.meta
      || CASE WHEN i.nbt_hash != '' THEN '#' || substr(i.nbt_hash, 1, 8) ELSE '' END
    WHEN 'fluid' THEN 'fluid:' || fl.name
    ELSE 'mte:' || m.mte_id
  END AS key,
  coalesce(i.mod_id, m.mod_id) AS mod, i.gt_prefix, i.gt_material, bm25(name_fts) AS rank
FROM name_fts AS f
LEFT JOIN item AS i ON f.kind = 'item' AND i.id = f.ref_id
LEFT JOIN fluid AS fl ON f.kind = 'fluid' AND fl.id = f.ref_id
LEFT JOIN machine AS m ON f.kind = 'machine' AND m.id = f.ref_id
WHERE name_fts MATCH :q
  AND (f.kind = :kind OR (:kind IS NULL AND f.kind IN ('item', 'fluid')))
  AND (i.meta IS NULL OR i.meta != 32767)
ORDER BY bm25(name_fts), f.kind, f.ref_id
LIMIT :limit;
