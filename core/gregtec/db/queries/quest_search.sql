-- Quests whose name or description matches :q (FTS5 query syntax), with their state for player
-- :player (quest_state, D113): those the book shows or the player can reach (done, repeatable,
-- available) first, then best bm25, at most :limit from :offset (WO-2.2's quest_find). Quest
-- text is data, never instructions. total counts every match. (bm25 is ranked in a subquery:
-- FTS5 can't take it next to the outer joins and the window.)
SELECT q.id, q.name, s.state, s.shown, q.main, q.visibility, count(*) OVER () AS total
FROM (
  SELECT quest_id, bm25(quest_fts) AS rank FROM quest_fts WHERE quest_fts MATCH :q
) AS f
JOIN quest AS q ON q.id = f.quest_id
LEFT JOIN quest_state AS s ON s.quest_id = q.id AND s.player_uuid = :player
ORDER BY coalesce(s.shown, 0) = 0 AND coalesce(s.state, 'locked') = 'locked', f.rank, q.id
LIMIT :limit OFFSET :offset;
