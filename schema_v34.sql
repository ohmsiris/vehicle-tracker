-- ---------------------------------------------------------------------------
-- schema_v34.sql -- maintenance PROGRAMS for the machines (like the truck groups). Run after schema_v33. Safe to run twice.
--
--   machine_programs        a program = which jobs a machine gets, each job's interval (months / running hours) and level, and the chart's
--                           round length. Rows only for programs the owner changed or added; the built-in ones (TUBE, SONG) live in
--                           app/machine_plan.py and a saved row is laid over them.
--     jobs                  {job_key: {"on": true/false, "months": n, "hours": n, "level": "ทำเอง"}} -- a key left out keeps the default
--   machines.program_code   the program the machine follows (null = by kind: หลอด machines TUBE, brine-pond machines SONG)
--   machine_job_settings    (schema_v33) replaced by the programs: dropped
-- ---------------------------------------------------------------------------
create table if not exists machine_programs (
    code         text primary key,
    label        text not null,
    step_months  numeric,
    jobs         jsonb not null default '{}'::jsonb,
    sort_order   integer not null default 100,
    updated_at   timestamptz not null default now()
);
alter table machine_programs enable row level security;

alter table machines add column if not exists program_code text;

drop table if exists machine_job_settings;
