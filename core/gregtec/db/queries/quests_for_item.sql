-- Quest tasks and rewards that ask for or give item :registry with meta :meta (either side's
-- meta 32767 matches any meta; NBT is not compared), any member of an oredict in :ores (a JSON
-- array of names), or fluid :fluid, with each quest's state for player :player (D113), at most
-- :limit from :offset (WO-2.2's quest_for_item). Tasks first, open quests before locked and
-- done ones; total counts every match.
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
WHERE (qi.registry_name = :registry AND (qi.meta = :meta OR qi.meta = 32767 OR :meta = 32767))
  OR qi.ore IN (SELECT value FROM json_each(:ores))
  OR qi.fluid = :fluid
ORDER BY qi.part = 'reward',
  CASE s.state WHEN 'available' THEN 0 WHEN 'repeatable' THEN 1 WHEN 'locked' THEN 2 ELSE 3 END,
  q.name, q.id, qi.idx, qi.slot
LIMIT :limit OFFSET :offset;
