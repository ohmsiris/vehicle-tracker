-- v56: a month whose card fuel per truck was split from a total by weight (scripts/fill_fuel_estimates.py) is marked, so the route page says so.
alter table driver_sales_monthly add column if not exists fuel_estimated boolean not null default false;
