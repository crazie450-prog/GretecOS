-- The prerequisites of quest :id with their state for player :player (D113). A prerequisite
-- missing from the book has no name and no state (it never counts as done).
SELECT p.prereq_id AS id, q.name, s.state, s.shown
FROM quest_prereq AS p
LEFT JOIN quest AS q ON q.id = p.prereq_id
LEFT JOIN quest_state AS s ON s.quest_id = p.prereq_id AND s.player_uuid = :player
WHERE p.quest_id = :id
ORDER BY q.name IS NULL, q.name, p.prereq_id;
