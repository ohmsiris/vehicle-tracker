-- ---------------------------------------------------------------------------
-- schema_v11.sql -- maintenance programme: the groups and which group each truck is in
--
-- Run once in the Supabase SQL editor, AFTER schema_v10.sql. Safe to run twice.
-- Nothing existing changes.
--
--   service_groups  how many months one base step of km is worth: "S2 = 2 months per 10,000 km", "B6 = 6 months per
--                   20,000 km" ... (a group can instead be worked out from a km/day figure)
--   vehicle_plans   which group each truck is in -- how the truck is used (route, factory, CP ...). A truck with no row
--                   has no schedule.
-- ---------------------------------------------------------------------------

create table if not exists service_groups (
    code             text primary key,
    label            text not null,
    rule_set         text not null check (rule_set in ('รถเล็ก', 'รถใหญ่')),
    step_km          integer not null check (step_km > 0),
    months_per_step  numeric check (months_per_step is null or months_per_step > 0),
    km_per_day       numeric check (km_per_day is null or km_per_day > 0),
    sort_order       integer not null default 99,
    updated_at       timestamptz not null default now(),
    check (months_per_step is not null or km_per_day is not null)
);

create table if not exists vehicle_plans (
    vehicle_id  uuid primary key references vehicles (id) on delete cascade,
    group_code  text not null references service_groups (code) on update cascade,
    updated_at  timestamptz not null default now()
);

insert into service_groups (code, label, rule_set, step_km, months_per_step, km_per_day, sort_order) values
    ('S2',  'รถเล็ก - รถสาย (2 เดือน ต่อ 10,000 กม.)',            'รถเล็ก', 10000, 2,    null,  1),
    ('S3',  'รถเล็ก - รถหน้าโรง (3 เดือน ต่อ 10,000 กม.)',        'รถเล็ก', 10000, 3,    null,  2),
    ('S6',  'รถเล็ก - นานสุดตามคู่มือ (6 เดือน ต่อ 10,000 กม.)',  'รถเล็ก', 10000, 6,    null,  3),
    ('B4',  'ใช้กฎรถใหญ่ - NLR (4 เดือน ต่อ 20,000 กม.)',         'รถใหญ่', 20000, 4,    null,  4),
    ('B5',  'รถใหญ่ - รถสาย (5 เดือน ต่อ 20,000 กม.)',            'รถใหญ่', 20000, 5,    null,  5),
    ('B6',  'รถใหญ่ - หน้าโรง / CP (6 เดือน ต่อ 20,000 กม.)',     'รถใหญ่', 20000, 6,    null,  6),
    ('KK1', 'แก่งคอย เบอร์ 1 (บ้านนา) - ตามกม./วันของคันเอง',      'รถใหญ่', 20000, null, 242.3, 7)
on conflict (code) do nothing;

-- no policies: only the backend's service key can read or write
alter table service_groups enable row level security;
alter table vehicle_plans  enable row level security;
revoke all on service_groups, vehicle_plans from anon, authenticated;

-- Make the API pick up the new tables straight away.
notify pgrst, 'reload schema';
