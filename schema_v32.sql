-- ---------------------------------------------------------------------------
-- schema_v32.sql -- a mark on a truck: "its repair history is brought up to date". Run after schema_v8. Safe to run twice.
--
--   vehicles.history_current_on   the day the owner marked the truck's history as caught up with the latest bills (null = not marked yet).
--                                 Shown as a small tick next to the truck wherever it is listed; set / cleared on the truck's edit page.
-- ---------------------------------------------------------------------------
alter table vehicles add column if not exists history_current_on date;
