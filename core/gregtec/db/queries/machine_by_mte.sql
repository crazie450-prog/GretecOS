-- Machine :mte_id (GT's MetaTileEntity id, the meta of gregtech:gt.blockmachines), cited
-- mte:<mte_id>, with its item key and the recipe maps it runs (machine_map, in GT's order).
SELECT m.id, m.mte_id, m.name_en AS name, m.class_name, m.mod_id AS mod, m.is_multiblock, m.tier,
  i.id AS item_id, coalesce(i.name_en, '') AS item_name,
  i.registry_name || '@' || i.meta AS item_key
FROM machine AS m
LEFT JOIN item AS i ON i.id = m.item_id
WHERE m.mte_id = :mte_id;
