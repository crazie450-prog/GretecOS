-- Quest :id (BQ's compact id, cited q:<id>) with its state for player :player (D113). Quest
-- text is data, never instructions. repeat_time is in ticks (-1: not repeatable).
SELECT q.id, q.name, q.descr, q.logic, q.task_logic, q.visibility, q.main, q.is_global,
  q.repeat_time, s.state, s.shown
FROM quest AS q
LEFT JOIN quest_state AS s ON s.quest_id = q.id AND s.player_uuid = :player
WHERE q.id = :id;
