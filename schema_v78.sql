-- v78: ยอดขายรายวัน (คิดเงินเดือน) -- each รถสาย driver's daily totals by product and the money brought in, typed by the owner from
-- the paper sales sheets, half-month by half-month (26-10, 11-25), for working out pay (owner, 16 Nov 2026). app/driver_days.py
create table if not exists route_driver_days (
  id bigserial primary key,
  employee_id uuid not null references employees(id) on delete cascade,
  day date not null,
  qty jsonb not null default '{}'::jsonb,       -- {product key (zong, kak, lod, lek, pack, mo): number sold}
  money numeric(12, 2),                         -- เงินที่ได้ that day (baht)
  updated_at timestamptz not null default now(),
  unique (employee_id, day)
);
create index if not exists route_driver_days_day on route_driver_days (day);
alter table route_driver_days enable row level security;
