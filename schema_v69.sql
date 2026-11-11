-- v69 (11 Nov 2026): cash fuel (เติมน้ำมันเงินสด) read from the handwritten รายการเบิก pages, for the bookkeeper's monthly
-- รายการค่าน้ำมัน (per truck: plate, driver, every fill, total) that goes to HQ, and for the accounts. Pages come in two batches
-- (the 11th: 26-10, the 26th: 11-25). A page photo is stored once (image_hash: never read twice); each fuel line read from it is
-- a row the bookkeeper checks (truck, amount, counted or not).
create table if not exists cash_fuel_pages (
  id bigserial primary key,
  branch text not null default 'สระบุรี',
  storage_path text not null,
  image_hash text not null unique,
  name text,
  by_name text,
  status text not null default 'read',
  error text,
  raw jsonb,
  created_at timestamptz not null default now()
);
create table if not exists cash_fuel_lines (
  id bigserial primary key,
  page_id bigint references cash_fuel_pages(id) on delete cascade,     -- null: a line the bookkeeper typed in
  branch text not null default 'สระบุรี',
  month text not null,              -- the company month the line falls in ('2026-11' = 26 Oct - 25 Nov)
  batch int not null,               -- 1 = 26-10, 2 = 11-25
  day date not null,
  text text,                        -- the line as read
  plate_read text,
  number_read text,
  amount numeric not null default 0,
  requester text,
  position text,
  vehicle_id uuid references vehicles(id) on delete set null,
  driver_name text,
  counted boolean not null default true,
  checked boolean not null default false,
  dup_of bigint,
  note text,
  seq int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists cash_fuel_lines_month on cash_fuel_lines(branch, month);
alter table cash_fuel_pages enable row level security;
alter table cash_fuel_lines enable row level security;
