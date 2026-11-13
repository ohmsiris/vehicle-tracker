-- v76: each person's pay, month by month, from HQ's payroll sheets (owner, 13 Nov 2026) -- the employee page's pay table and chart.
create table if not exists employee_pay_months (
  id bigserial primary key,
  month text not null,                         -- the pay month "2026-10" (period 26 Sep - 25 Oct)
  grp text not null,                           -- drivers / factory / kk (which HQ sheet)
  code text not null,                          -- SB-PD012 ...
  employee_id uuid references employees(id) on delete set null,
  name text,
  dept text,
  days numeric(6, 2),
  parts jsonb not null default '{}',           -- wage, commission, bag, bonus, ot, other
  refund numeric(12, 2) not null default 0,    -- savings paid back / back pay: on the sheet, not this month's pay
  gross numeric(12, 2),                        -- รวมรับ (with the refund, as printed)
  deductions jsonb not null default '{}',      -- savings, advance, loan, common, card, shortage, other, sso
  deduct_total numeric(12, 2),
  net numeric(12, 2),
  source text,                                 -- xlsx / pdf
  file text,
  flags jsonb not null default '[]',
  created_at timestamptz not null default now(),
  unique (month, code)
);
create index if not exists employee_pay_months_emp on employee_pay_months (employee_id);
alter table employee_pay_months enable row level security;
