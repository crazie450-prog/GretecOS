-- The quest lines the quests in :ids (a JSON array of quest ids) are in, in tab order; never a
-- HIDDEN line (the book never shows one).
SELECT e.quest_id, l.name, l.ord
FROM quest_line_entry AS e
JOIN quest_line AS l ON l.id = e.line_id
WHERE e.quest_id IN (SELECT value FROM json_each(:ids)) AND l.visibility != 'HIDDEN'
ORDER BY e.quest_id, l.ord, l.id;
