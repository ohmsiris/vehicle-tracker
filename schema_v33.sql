-- ---------------------------------------------------------------------------
-- schema_v33.sql -- the machine maintenance programme (ภาพรวมเครื่องจักร). Run after schema_v9. Safe to run twice.
--
--   machine_events.job_key        which planned job a report was ("overhaul", "oil", "cooling" ...) -- the plan counts from the latest such report
--   machine_events.hours_at       the hour-meter reading when it was done (optional; makes the "hours since" exact)
--   machines.hours_per_day        how long the machine is assumed to run when there is no hour meter (default 24)
--   machines.days_per_week        ... and how many days a week (default 7, the brine-pond machines 6)
--   machines.never_overhauled     the owner's fact "this compressor has never been overhauled" (cleared when an overhaul is logged)
--   machine_parts.shared_machine_ids   the OTHER machines a part serves (a grinder shared by machines 1 and 2): shown on each of them as "ใช้ร่วมกับ"
--   machine_readings              hour-meter readings (several per machine); the running rate is worked out from them
--   machine_job_settings          the owner's changes to a job's interval / level (a job with no row uses the defaults in app/machine_plan.py)
-- ---------------------------------------------------------------------------
alter table machine_events add column if not exists job_key text;
alter table machine_events add column if not exists hours_at numeric;
alter table machines add column if not exists hours_per_day numeric;
alter table machines add column if not exists days_per_week numeric;
alter table machines add column if not exists never_overhauled boolean not null default false;
alter table machine_parts add column if not exists shared_machine_ids uuid[] not null default '{}';

create table if not exists machine_readings (
    id          uuid primary key default gen_random_uuid(),
    machine_id  uuid not null references machines (id) on delete cascade,
    read_on     date not null,
    hours       numeric not null check (hours >= 0),
    notes       text,
    created_at  timestamptz not null default now()
);
create index if not exists machine_readings_machine_idx on machine_readings (machine_id, read_on desc);
create index if not exists machine_events_job_idx on machine_events (machine_id, job_key, event_date desc);

create table if not exists machine_job_settings (
    job_key          text primary key,
    interval_months  numeric,
    interval_hours   numeric,
    level            text,
    updated_at       timestamptz not null default now()
);

alter table machine_readings enable row level security;
alter table machine_job_settings enable row level security;
