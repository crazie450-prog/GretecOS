-- The tasks, then the rewards, of quest :id, one row per stack (a task with no items, such as a
-- checkbox, is one row with no stack): kind (BQ's taskID or rewardID), consume (tasks that take
-- items), and each stack's item key (registry name, meta, NBT hash), oredict name or fluid,
-- amount, and its name when the recipe DB has it.
SELECT p.part, p.idx, p.kind, p.consume, qi.slot, qi.registry_name, qi.meta, qi.nbt_hash, qi.ore,
  qi.fluid, qi.amount, coalesce(i.name_en, f.name_en) AS name
FROM (
  SELECT 'task' AS part, idx, kind, consume FROM quest_task WHERE quest_id = :id
  UNION ALL
  SELECT 'reward', idx, kind, NULL FROM quest_reward WHERE quest_id = :id
) AS p
LEFT JOIN quest_item AS qi ON qi.quest_id = :id AND qi.part = p.part AND qi.idx = p.idx
LEFT JOIN item AS i ON i.id = qi.item_id
LEFT JOIN fluid AS f ON f.id = qi.fluid_id
ORDER BY p.part = 'reward', p.idx, qi.slot;
