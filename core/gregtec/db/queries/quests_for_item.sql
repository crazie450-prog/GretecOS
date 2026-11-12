-- Quest tasks and rewards that ask for or give item :registry with meta :meta (either side's
-- meta 32767 matches any meta; NBT is not compared), any member of an oredict in :ores (a JSON
-- array of names), or fluid :fluid, with each quest's state for player :player (D113), at most
-- :limit from :offset (WO-2.2's quest_for_item). Tasks first, open quests before locked and
-- done ones; total counts every match it lists.
-- Spoilers (D268): for a quest past the frontier (not done, repeatable or available), what it
-- asks for is listed from spoiler level :level 1, what it gives at 2, or when Clayton named the
-- item himself (:named); else its rows are not listed at all (which items a locked quest asks
-- for is what level 1 shows, so a model can't probe for them with items it chose).
SELECT qi.quest_id, q.name, s.state, s.shown, qi.part, qi.idx, qi.slot, p.kind, p.consume,
  qi.registry_name, qi.meta, qi.amount, qi.ore, qi.fluid, count(*) OVER () AS total
FROM quest_item AS qi
JOIN quest AS q ON q.id = qi.quest_id
LEFT JOIN quest_state AS s ON s.quest_id = qi.quest_id AND s.player_uuid = :player
LEFT JOIN (
  SELECT quest_id, 'task' AS part, idx, kind, consume FROM quest_task
  UNION ALL
  SELECT quest_id, 'reward', idx, kind, NULL FROM quest_reward
) AS p ON p.quest_id = qi.quest_id AND p.part = qi.part AND p.idx = qi.idx
WHERE ((qi.registry_name = :registry AND (qi.meta = :meta OR qi.meta = 32767 OR :meta = 32767))
    OR qi.ore IN (SELECT value FROM json_each(:ores))
    OR qi.fluid = :fluid)
  AND (coalesce(s.state, 'locked') IN ('done', 'repeatable', 'available')
    OR :level >= (CASE qi.part WHEN 'reward' THEN 2 ELSE 1 END)
    OR :named)
ORDER BY qi.part = 'reward',
  CASE s.state WHEN 'available' THEN 0 WHEN 'repeatable' THEN 1 WHEN 'locked' THEN 2 ELSE 3 END,
  -- a quest's name orders it only where quest_for_item shows that name; a locked, unshown
  -- quest goes by id (a hidden name must not show through the order, D268, as quest_prereqs)
  CASE WHEN coalesce(s.state, 'locked') IN ('done', 'repeatable', 'available') OR s.shown
    OR :level >= 1 THEN q.name END,
  q.id, qi.idx, qi.slot
LIMIT :limit OFFSET :offset;
