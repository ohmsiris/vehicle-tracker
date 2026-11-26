-- v93: how each person's pay is worked out (พนักงาน › คำนวณเงินเดือน; owner, 26 Nov 2026). One row per employee, first filled from
-- the workbook "แบบคำนวณเงินเดือน (เสนอ).xlsx" (sheet อัตรา). The daily rate itself stays in employee_rates; this is the rest:
-- which pay group, the commission %, baht per ซอง, OT, เบี้ยขยัน, and three things the owner told us about people who move around.
create table if not exists employee_pay_rules (
    employee_id    uuid primary key references employees(id) on delete cascade,
    pay_group      text not null,                 -- DRV-A..F, FAC-COM, DAY, KK-DRV, KK-PROD (the workbook's groups)
    commission_pct numeric,                       -- 5 = 5% of sales
    bag_rate       numeric,                       -- baht per ซอง
    ot_rate        numeric,                       -- baht per OT hour (null = the default, 40)
    ot_auto_hours  numeric,                       -- OT hours counted for every day worked (the workbook's "อัตโนมัติ")
    bonus          boolean not null default false, -- gets เบี้ยขยัน when the days are full
    rate2          numeric,                       -- a second daily rate: a day spent only on a truck (344 against 390)
    rides_with     uuid references employees(id) on delete set null,   -- เด็กรถ: the route driver whose truck this person rides
    route_until    date,                          -- a route driver whose route ended: last day driven (other work after that)
    note           text,
    updated_at     timestamptz not null default now()
);

alter table public.employee_pay_rules enable row level security;
revoke all on public.employee_pay_rules from anon, authenticated;
