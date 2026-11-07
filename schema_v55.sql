-- v55: Saraburi driver sales per driver per month (Drivers sheet "Driver Sales 2026" + the payroll PDF for months not in it yet), with
-- the truck's fuel and the gross sales worked out per the commission rules (app/driver_sales.py).
create table if not exists driver_sales_monthly (
  id bigserial primary key, month text not null, code text not null, name text, nick text, truck_no text, vehicle_id uuid,
  days numeric(6,2) default 0, wage numeric(14,2) default 0, rate numeric(10,2) default 0, sales_written numeric(14,2) default 0,
  commission numeric(14,2) default 0, bag_pay numeric(14,2) default 0, total_pay numeric(14,2) default 0, bags numeric(10,0) default 0,
  fuel_card numeric(14,2) default 0, fuel_cash numeric(14,2), gross_sales numeric(14,2), fuel_complete boolean default false,
  source text, created_at timestamptz not null default now(), unique (month, code));
alter table driver_sales_monthly enable row level security;
