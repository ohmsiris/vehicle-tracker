-- v90: a person's position history (owner, 24 Nov 2026 -- "a button to promote or demote someone so the previous record keeps").
-- "เปลี่ยนตำแหน่ง" on an employee's page writes one row here (the old and the new position and branch, the day it takes effect, why) and
-- then changes employees.position / branch; the plain "แก้ไข" stays for correcting a typo and writes nothing here.
create table if not exists employee_role_changes (
    id             bigint generated always as identity primary key,
    employee_id    uuid not null references employees (id) on delete cascade,
    change_date    date not null,
    kind           text not null check (kind in ('promote', 'demote', 'move')),     -- เลื่อนตำแหน่ง / ลดตำแหน่ง / ย้ายงาน
    from_position  text,
    to_position    text,
    from_branch    text,
    to_branch      text,
    reason         text,
    created_at     timestamptz not null default now()
);
create index if not exists idx_employee_role_changes_employee on employee_role_changes (employee_id, change_date desc);

alter table public.employee_role_changes enable row level security;
revoke all on public.employee_role_changes from anon, authenticated;
