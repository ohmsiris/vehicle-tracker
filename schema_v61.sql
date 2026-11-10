-- v61: สายรถ · ใบขาย -- free customers (ฟรี on the sheet: police, district office ... per route), discounts / one-off free per sale line,
-- and the ice that comes back counted by หน้าโรง (not the driver).
alter table sale_route_customers add column if not exists free boolean not null default false;
alter table sale_days add column if not exists returns_foh jsonb;
alter table sale_days add column if not exists returns_at timestamptz;
alter table sale_lines add column if not exists discount numeric(12,2) not null default 0;
alter table sale_lines add column if not exists free boolean not null default false;
