-- v52: HR items found in the end-of-month papers (resignations, warnings, factory loans), waiting for the owner to confirm in พนักงาน.
create table if not exists hr_suggestions (
  id uuid primary key default gen_random_uuid(),
  batch text not null,
  page integer not null,
  kind text not null check (kind in ('resign','warning','loan','loan_terms')),
  employee_code text not null,
  payload jsonb not null default '{}'::jsonb,
  summary text,
  status text not null default 'pending' check (status in ('pending','applied','dismissed')),
  result text,
  decided_at timestamptz,
  created_at timestamptz not null default now(),
  unique (batch, page, kind, employee_code)
);
alter table hr_suggestions enable row level security;
