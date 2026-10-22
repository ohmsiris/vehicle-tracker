-- ---------------------------------------------------------------------------
-- schema_v13.sql -- snoozing a maintenance reminder
--
-- Run once in the Supabase SQL editor, AFTER schema_v10..v12. Safe to run twice.
-- Nothing existing changes.
--
--   service_snoozes  "do not remind me about this item of this truck until <date>". It is for one due date: once the item
--                    has been done again (a new due date) an old snooze no longer applies.
-- ---------------------------------------------------------------------------

create table if not exists service_snoozes (
    vehicle_id  uuid not null references vehicles (id) on delete cascade,
    item_key    text not null,
    until       date not null,
    for_due     date,                                  -- the due date that was snoozed
    created_at  timestamptz not null default now(),
    primary key (vehicle_id, item_key)
);

-- no policies: only the backend's service key can read or write
alter table service_snoozes enable row level security;
revoke all on service_snoozes from anon, authenticated;

-- Make the API pick up the new table straight away.
notify pgrst, 'reload schema';
