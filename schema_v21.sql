-- ---------------------------------------------------------------------------
-- schema_v21.sql -- พนักงาน (employees): the staff of both branches in one table
--
-- Run once in the Supabase SQL editor, AFTER schema_v6 (drivers). Safe to run twice. Nothing existing changes.
--
--   code        the staff code on the pay sheets (SB-FR001 factory, SB-PD033 drivers, SB-KK062 Kaeng Khoi) -- unique
--   position    ตำแหน่ง / แผนก as written on the roster (ผลิต, พขร.รถสาย, ช่าง ...)
--   status      'active' or 'resigned'. An employee is never deleted once they have loans / documents (those point
--               at them): "removing" one = ลาออก, with the date and the reason.
--   driver_id   a พขร. is also a row of `drivers` (who drives which truck): the link keeps the two lists in step
--
-- The loans, warning letters, memos and payslip checks come in later files and point at employees.id.
-- Like every table here: row-level security on, no policies -- only the server's service key can read it.
-- ---------------------------------------------------------------------------
create table if not exists employees (
    id           uuid primary key default gen_random_uuid(),
    code         text not null,
    full_name    text not null,
    nickname     text,
    branch       text not null,
    position     text,
    status       text not null default 'active' check (status in ('active', 'resigned')),
    start_date   date,
    end_date     date,
    end_reason   text,
    phone        text,
    driver_id    uuid references drivers (id) on delete set null,
    notes        text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

create unique index if not exists uq_employees_code on employees (upper(code));
create index if not exists idx_employees_branch_status on employees (branch, status);

drop trigger if exists trg_employees_updated_at on employees;
create trigger trg_employees_updated_at
    before update on employees
    for each row execute function set_updated_at();

alter table employees enable row level security;
revoke all on employees from anon, authenticated;
