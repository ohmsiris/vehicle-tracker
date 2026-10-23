-- NOTE: this feature was removed again; see schema_v20.sql (the table is dropped).
-- schema_v19.sql -- an item of our own can be for the small trucks only, the big trucks only, or both
-- (it is added from the รถเล็ก / รถใหญ่ list it belongs to). Run after schema_v18. Safe to run twice.
-- Items made before this keep working as they did: 'all' = km decides which kind of truck it is for (months only = every truck).
alter table service_custom_items add column if not exists applies_to text not null default 'all';
alter table service_custom_items drop constraint if exists service_custom_items_applies_to_check;
alter table service_custom_items add constraint service_custom_items_applies_to_check check (applies_to in ('all', 'small', 'big'));
