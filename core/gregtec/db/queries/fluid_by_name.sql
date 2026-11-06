-- The fluid with FluidRegistry name :name, as tools name it (fluid:<name>).
SELECT 'fluid' AS kind, f.id AS ref_id, coalesce(f.name_en, '') AS name, 'fluid:' || f.name AS key,
  NULL AS mod, NULL AS gt_prefix, NULL AS gt_material
FROM fluid AS f
WHERE f.name = :name;
