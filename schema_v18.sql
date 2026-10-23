-- NOTE: this feature was removed again; see schema_v20.sql (the table is dropped).
-- ---------------------------------------------------------------------------
-- schema_v18.sql -- maintenance items of our own, added in the app (แผนซ่อมบำรุง > ตั้งค่าระยะ)
--
-- Run once in the Supabase SQL editor, AFTER schema_v10 / v11. Safe to run twice. Nothing existing changes.
--
-- The built-in items (engine oil, filters, gearbox oil ...) stay in the code. Anything else the owner learns he should be
-- doing -- a timing belt, a battery, the fire extinguisher -- is added here and then behaves exactly like a built-in item:
-- it appears in every truck's plan table and reminders, can be ticked as done, and is found on bills by its words.
--
--   km_small / km_big   how often, in km, for the small rule set (D-Max, Revo) and the big one (NLR, 6/10 wheelers);
--                       empty = this item does not apply to that kind of truck
--   months              a fixed number of months ("every 12 months"); empty = worked out from the km and each truck's group
--                       (an item with months only, no km, applies to every truck)
--   words               what a bill line says when this was done ("สายพานไทม์มิ่ง"); the name itself counts too
--   category            the repair category a line gets when the work is recorded from the plan
-- ---------------------------------------------------------------------------

create table if not exists service_custom_items (
    key         text primary key,
    label       text not null,
    group_name  text not null default 'อื่นๆ',
    category    text not null default 'อื่นๆ',
    km_small    integer check (km_small is null or km_small > 0),
    km_big      integer check (km_big is null or km_big > 0),
    months      numeric check (months is null or months > 0),
    words       text[] not null default '{}',
    sort_order  integer not null default 100,
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    check (km_small is not null or km_big is not null or months is not null)
);
create unique index if not exists uq_service_custom_items_label on service_custom_items (lower(label));
alter table service_custom_items enable row level security;
revoke all on service_custom_items from anon, authenticated;
