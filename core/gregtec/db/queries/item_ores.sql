-- The oredict names the items in :items (a JSON array of item ids) are members of.
SELECT DISTINCT o.id, o.name
FROM ore_member AS om
JOIN ore AS o ON o.id = om.ore_id
WHERE om.item_id IN (SELECT value FROM json_each(:items))
ORDER BY o.name;
