-- The recipe of export run :run_id with citation key :ref (r:<map>/<sig12>[-n]).
SELECT r.*, m.unloc_name AS map, h.handler_name AS handler
FROM recipe AS r
LEFT JOIN recipe_map AS m ON m.id = r.recipe_map_id
LEFT JOIN nei_handler AS h ON h.id = r.nei_handler_id
WHERE r.run_id = :run_id AND r.ref = :ref;
