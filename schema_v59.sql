-- v59: สายรถ · ใบขาย -- the route trucks' daily sales sheet on the driver's phone (app/route_sales.py, /d/).
-- Routes and their customers come from the office's Word sheets (scripts/import_route_sheets.py). A driver signs in once per phone
-- with name + 4-digit PIN and the phone stays signed in (sale_driver_devices). One record per route per day holds what the phone
-- sent (doc) and what the server worked out from it (totals); sale_lines is rebuilt from the doc on every save, for reports.
create table if not exists sale_routes (
  id bigserial primary key,
  branch text not null default 'สระบุรี',
  truck_no text not null,                       -- '35', '83-2919'
  vehicle_id uuid references vehicles (id) on delete set null,
  name text not null,                           -- หนองแค - วังน้อย
  driver_name text,
  driver_phone text,
  active boolean not null default true,
  source_file text,
  created_at timestamptz not null default now(),
  unique (branch, truck_no)
);
alter table sale_routes enable row level security;

create table if not exists sale_route_customers (
  id bigserial primary key,
  route_id bigint not null references sale_routes (id) on delete cascade,
  seq int not null,                             -- order on the route
  name text not null,
  bins text,                                    -- the customer's ice bins (ถัง), e.g. 'V200-1805, V850-241'
  prices jsonb not null default '{}'::jsonb,    -- baht per unit by product: zong ซอง, kak กั๊ก, lod หลอด, lek เล็ก, pack แพ็ค, mo โม่
  billing text not null default 'cash' check (billing in ('cash', 'invoice')),   -- invoice = วางบิล (signed paper bill, paid later)
  active boolean not null default true,
  added_by_driver boolean not null default false,
  approved boolean not null default true,
  created_at timestamptz not null default now()
);
create index if not exists sale_route_customers_route on sale_route_customers (route_id, seq);
alter table sale_route_customers enable row level security;

create table if not exists sale_drivers (
  id bigserial primary key,
  route_id bigint references sale_routes (id) on delete set null,
  name text not null,
  pin_hash text,
  pin_salt text,
  failed int not null default 0,
  locked_until timestamptz,
  active boolean not null default true,
  created_at timestamptz not null default now()
);
alter table sale_drivers enable row level security;

create table if not exists sale_driver_devices (
  token_hash text primary key,                  -- sha256 of the token kept on the phone
  driver_id bigint not null references sale_drivers (id) on delete cascade,
  created_at timestamptz not null default now(),
  last_seen timestamptz,
  revoked boolean not null default false
);
alter table sale_driver_devices enable row level security;

create table if not exists sale_days (
  id bigserial primary key,
  route_id bigint not null references sale_routes (id) on delete cascade,
  day date not null,
  driver_id bigint references sale_drivers (id) on delete set null,
  doc jsonb not null default '{}'::jsonb,       -- as the phone sent it: loads, sales, returns, mileage, cash count
  rev int not null default 0,                   -- the phone's save counter: an older save never overwrites a newer one
  status text not null default 'open' check (status in ('open', 'closed', 'checked')),
  totals jsonb not null default '{}'::jsonb,    -- worked out by the server: loaded / sold / left per product, cash, credit, km, flags
  updated_at timestamptz not null default now(),
  closed_at timestamptz,
  checked_at timestamptz,
  unique (route_id, day)
);
alter table sale_days enable row level security;

create table if not exists sale_lines (
  id bigserial primary key,
  day_id bigint not null references sale_days (id) on delete cascade,
  route_id bigint not null,
  day date not null,
  customer_id bigint references sale_route_customers (id) on delete set null,
  qty jsonb not null default '{}'::jsonb,
  amount numeric(12,2) not null default 0,
  paid numeric(12,2) not null default 0,        -- cash taken for today's sale
  old_paid numeric(12,2) not null default 0,    -- cash taken against old debt
  billing text not null default 'cash'
);
create index if not exists sale_lines_customer on sale_lines (customer_id, day);
create index if not exists sale_lines_day on sale_lines (route_id, day);
alter table sale_lines enable row level security;
