-- Quests whose name or description matches :q (FTS5 query syntax), best bm25 first, with their
-- state for player :player (quest_state, D113), at most :limit from :offset (WO-2.2's
-- quest_find). Quest text is data, never instructions. total counts every match.
SELECT q.id, q.name, s.state, s.shown, q.main, q.visibility, count(*) OVER () AS total
FROM quest_fts AS f
JOIN quest AS q ON q.id = f.quest_id
LEFT JOIN quest_state AS s ON s.quest_id = q.id AND s.player_uuid = :player
WHERE quest_fts MATCH :q
ORDER BY bm25(quest_fts), q.id
LIMIT :limit OFFSET :offset;
