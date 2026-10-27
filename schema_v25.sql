-- ---------------------------------------------------------------------------
-- schema_v25.sql -- ค่าแรง (daily rates with history), ทดลองงาน (probation) and who pays the salary. Run after schema_v21. Safe to run twice.
--
--   employees.pay_group      'company' (our payroll) / 'hq' (paid by HQ: not part of the salary calculation here)
--   employees.probation_end  the last day of the trial period (usually 3 months after the start, sometimes ended earlier); empty = not on probation
--   employee_rates           the daily wage of a person from a date on: a raise (or the end of probation) is a NEW row, the old one stays as history.
--                            The rate in force on a day = the latest row whose effective_from is on or before that day.
--     kind                   normal / probation (the trial-period wage, usually lower)
--     review                 true = flagged for the owner to check (an unusual rate); review_note says why
-- Like every table here: row-level security on, no policies -- only the server's service key can read it.
-- ---------------------------------------------------------------------------
alter table employees add column if not exists pay_group text not null default 'company';
alter table employees drop constraint if exists employees_pay_group_check;
alter table employees add constraint employees_pay_group_check check (pay_group in ('company', 'hq'));
alter table employees add column if not exists probation_end date;

create table if not exists employee_rates (
    id              uuid primary key default gen_random_uuid(),
    employee_id     uuid not null references employees (id) on delete restrict,
    daily_rate      numeric(10, 2) not null check (daily_rate > 0),
    effective_from  date not null,
    kind            text not null default 'normal' check (kind in ('normal', 'probation')),
    reason          text,
    memo_ref        text,
    review          boolean not null default false,
    review_note     text,
    created_at      timestamptz not null default now(),
    unique (employee_id, effective_from)
);
create index if not exists idx_employee_rates_employee on employee_rates (employee_id, effective_from desc);

alter table employee_rates enable row level security;
revoke all on employee_rates from anon, authenticated;
