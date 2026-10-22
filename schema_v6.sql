-- ---------------------------------------------------------------------------
-- schema_v6.sql -- the vehicle roster: what each truck is for, and its driver
--
-- Run once in the Supabase SQL editor, AFTER schema_v2..v5. Safe to run twice.
-- Nothing existing changes. It adds:
--   * drivers            one row per driver (name, phone, branch)
--   * vehicles.usage     what the truck is used for: 'รถสาย', 'รถหน้าโรง', ...
--   * vehicles.main_driver_id   the truck's main driver
--   * vehicles.notes     free notes ("sold", "waiting for plates" ...)
-- Trucks are never deleted (their repair history must stay); "removing" one just
-- sets active = false, which the app does from the roster page.
-- ---------------------------------------------------------------------------

create table if not exists drivers (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    phone       text,
    branch      text,
    notes       text,
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

drop trigger if exists trg_drivers_updated_at on drivers;
create trigger trg_drivers_updated_at
    before update on drivers
    for each row execute function set_updated_at();

alter table drivers enable row level security;          -- no policies: only the backend's service key can read or write
revoke all on drivers from anon, authenticated;

alter table vehicles add column if not exists usage          text;
alter table vehicles add column if not exists main_driver_id uuid references drivers (id) on delete set null;
alter table vehicles add column if not exists notes          text;

create index if not exists idx_vehicles_main_driver on vehicles (main_driver_id);

-- Make the API pick up the new table and columns straight away.
notify pgrst, 'reload schema';
