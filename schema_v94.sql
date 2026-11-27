-- v94: each person's ปกส. (social security) deduction, from the employer's สปส.1-10 list (owner, 27 Nov 2026: "SSO1-10" for ก.ย. 2569).
-- sso = baht deducted each month (5% of the declared wage, capped); null = not known here (the page falls back to HQ's last sheet).
-- sso_source says which list it came from. ID-card numbers on that list are never stored.
alter table employee_pay_rules add column if not exists sso numeric;
alter table employee_pay_rules add column if not exists sso_source text;
