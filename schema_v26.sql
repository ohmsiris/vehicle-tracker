-- ---------------------------------------------------------------------------
-- schema_v26.sql -- เวลาทำงาน (attendance): the fingerprint machine's punches and the owner's decision on each day that needs one.
-- Run after schema_v21. Safe to run twice.
--
--   attendance_punches   every scan of the machine, one row per person and minute (re-uploading a report adds nothing twice).
--                        punched_at is the machine's clock time (Thai local time, no time zone). Shifts are worked out from these on the fly:
--                        a night shift is a scan one evening and a scan next morning, so the punches are kept as they are, not cut per day.
--   attendance_reviews   the owner's decision on one WORK DAY (the day the shift started) of one person:
--                          approve = อนุมัติ (paid in full) · half = ค่าแรงครึ่งวัน · full_penalty = ค่าแรงเต็ม + ใบเตือน + หักเงิน (deduct_amount)
--                          unpaid = ไม่จ่าย.   manual = a day the owner adds although the machine has no scan (worked, no scan).
--   attendance_uploads   one row per uploaded machine report (what was read, for the history on the screen).
-- Like every table here: row-level security on, no policies -- only the server's service key can read it.
-- ---------------------------------------------------------------------------
create table if not exists attendance_punches (
    id           uuid primary key default gen_random_uuid(),
    employee_id  uuid not null references employees (id) on delete restrict,
    punched_at   timestamp not null,
    source       text,
    created_at   timestamptz not null default now(),
    unique (employee_id, punched_at)
);
create index if not exists idx_attendance_punches_at on attendance_punches (punched_at);

create table if not exists attendance_reviews (
    id             uuid primary key default gen_random_uuid(),
    employee_id    uuid not null references employees (id) on delete restrict,
    work_date      date not null,
    decision       text not null check (decision in ('approve', 'half', 'full_penalty', 'unpaid')),
    deduct_amount  numeric(10, 2) not null default 0 check (deduct_amount >= 0),
    note           text,
    manual         boolean not null default false,
    decided_at     timestamptz not null default now(),
    unique (employee_id, work_date)
);
create index if not exists idx_attendance_reviews_date on attendance_reviews (work_date);

create table if not exists attendance_uploads (
    id             uuid primary key default gen_random_uuid(),
    filename       text,
    period_start   date not null,
    period_end     date not null,
    people         integer not null default 0,
    punches_read   integer not null default 0,
    punches_added  integer not null default 0,
    unknown_codes  text,
    created_at     timestamptz not null default now()
);

alter table attendance_punches enable row level security;
alter table attendance_reviews enable row level security;
alter table attendance_uploads enable row level security;
revoke all on attendance_punches, attendance_reviews, attendance_uploads from anon, authenticated;
