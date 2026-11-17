-- v87: every detail of HQ's payroll row per person (owner, 17 Nov 2026): HQ's own line labels, loan balance (owed / paid / still owed),
-- savings carried forward, sales + commission %, daily rate, position, HQ's cell notes. Never bank / ID / SSO-number columns.
alter table employee_pay_months add column if not exists details jsonb not null default '{}';
