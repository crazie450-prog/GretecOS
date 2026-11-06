-- The frontier quests (available or repeatable, and shown; D113) among the prerequisites of quest
-- :id, all the way back, for player :player: where to start on a quest past the frontier
-- (WO-2.2's quest_get nudge, D26). At most :limit, by name.
WITH RECURSIVE up(id) AS (
  SELECT prereq_id FROM quest_prereq WHERE quest_id = :id
  UNION
  SELECT p.prereq_id FROM quest_prereq AS p JOIN up ON p.quest_id = up.id
)
SELECT q.id, q.name, s.state, s.shown
FROM up
JOIN quest AS q ON q.id = up.id
JOIN quest_state AS s ON s.quest_id = q.id AND s.player_uuid = :player
WHERE s.state IN ('available', 'repeatable') AND s.shown = 1
ORDER BY q.name, q.id
LIMIT :limit;
