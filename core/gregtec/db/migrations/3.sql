-- Schema version 2 -> 3 (WO-1.10, D134). Run by gregtec.db.access before schema.sql, only on a DB
-- at version 2 (a version-1 DB runs 2.sql first, then this). Version 3 adds the crop tables and
-- changes no existing one, so the quest, recipe and ore data are kept. No released version had
-- crop tables; dropping any a pre-release build of WO-1.10 made lets schema.sql create them with
-- version 3's columns. A crop table holds only data `gos db build` rewrites from a collected run.
DROP TABLE IF EXISTS crop;
DROP TABLE IF EXISTS crop_drop;
DROP TABLE IF EXISTS crop_seed;
DROP TABLE IF EXISTS crop_parent_recipe;
DROP TABLE IF EXISTS crop_parent;
DROP TABLE IF EXISTS crop_mutation;
DROP TABLE IF EXISTS crop_soil;
