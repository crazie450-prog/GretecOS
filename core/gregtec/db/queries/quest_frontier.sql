-- The frontier of player :player (WO-1.5, D113): quests that are unlocked and either not complete
-- (available) or reset by their repeat timer (repeatable), and shown (quest_state), in each quest
-- line the book lists (never a HIDDEN line; a COMPLETED line once one of its quests is done or
-- repeatable), in tab order, then top to bottom and left to right, as the book draws them. A
-- quest in two lines is listed in both. :line is a quest line's name (any case) or NULL for every
-- line; at most :limit rows (-1: all). gregtec.ingest.quests.frontier is the same rule.
SELECT l.name AS line, q.name, q.id AS quest_id, s.state, q.main, l.ord, e.x, e.y
FROM quest_state AS s
JOIN quest AS q ON q.id = s.quest_id
JOIN quest_line_entry AS e ON e.quest_id = q.id
JOIN quest_line AS l ON l.id = e.line_id
WHERE s.player_uuid = :player AND s.state IN ('available', 'repeatable') AND s.shown = 1
  AND l.visibility != 'HIDDEN'
  AND (l.visibility != 'COMPLETED' OR EXISTS (
    SELECT 1 FROM quest_line_entry AS e2
    JOIN quest_state AS s2 ON s2.quest_id = e2.quest_id AND s2.player_uuid = :player
    WHERE e2.line_id = l.id AND s2.state IN ('done', 'repeatable')))
  AND (:line IS NULL OR l.name = :line COLLATE NOCASE)
ORDER BY l.ord, e.y, e.x, q.id
LIMIT :limit;
