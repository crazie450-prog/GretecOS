-- Items, fluids, machines, recipe maps and NEI handlers whose name or registry name matches :q
-- (FTS5 query syntax, e.g. 'steel*' or '"stainless steel"'), best match first, at most :limit.
SELECT kind, ref_id, name_en, registry_name
FROM name_fts
WHERE name_fts MATCH :q
ORDER BY bm25(name_fts), kind, ref_id
LIMIT :limit;
