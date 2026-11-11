-- v68 (11 Nov 2026): the drivers order tomorrow's ice today (owner: today it is written on paper, no cut-off; trucks back
-- early are loaded that evening, late ones at 4-6 am; leftovers go back to หน้าโรง). One order per route per day it is FOR;
-- the next day's sheet offers it as เที่ยว 1 (ตามที่สั่ง), หน้าโรง sees every truck's order and the totals (ยอดสั่งน้ำแข็ง).
create table if not exists sale_orders (
  id bigserial primary key,
  route_id bigint not null references sale_routes(id) on delete cascade,
  day date not null,
  q jsonb not null default '{}',
  note text,
  by_name text,
  updated_at timestamptz not null default now(),
  unique (route_id, day)
);
alter table sale_orders enable row level security;
