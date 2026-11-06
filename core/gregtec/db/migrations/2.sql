-- Schema version 1 -> 2 (WO-1.5, D112). Run by gregtec.db.access before schema.sql, only on a DB at
-- version 1. Version 2's quest and quest_line have more columns than version 1's, and schema.sql
-- only creates what is missing; nothing wrote to the quest tables before version 2, so dropping
-- version 1's empty ones loses nothing.
DROP TABLE IF EXISTS quest;
DROP TABLE IF EXISTS quest_line;
