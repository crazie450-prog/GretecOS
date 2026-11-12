-- The quests that list quest :id as a prerequisite (what completing it unlocks, WO-6.4), with
-- player :player's state and whether the book shows them; the quest tools decide what to name.
SELECT p.quest_id AS id, q.name, s.state, s.shown
FROM quest_prereq AS p
JOIN quest AS q ON q.id = p.quest_id
LEFT JOIN quest_state AS s ON s.quest_id = p.quest_id AND s.player_uuid = :player
WHERE p.prereq_id = :id
-- Shown quests first by name; the rest by id, so a hidden name doesn't even set the order.
ORDER BY s.shown IS NOT 1, CASE WHEN s.shown = 1 THEN q.name END, p.quest_id;
