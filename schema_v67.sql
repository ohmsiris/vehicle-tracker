-- v67 (11 Nov 2026): every bin (ถัง) is its own record, so each one can be traced (owner: "each comma is a different tub").
-- A bin is model letter + size in litres + number (V200-1805 and S200-1805 are different bins). It is at one shop (customer_id,
-- null = back at the plant / not known) since a date; every move is kept in bin_moves. sale_route_customers.bins stays as the
-- text the phone and the printed sheet show, rebuilt from this table; bins_legacy keeps what the Word sheet said, bins_other
-- the bins written without a number ("500L", "T800").
create table if not exists bins (
  id bigserial primary key,
  branch text not null default 'สระบุรี',
  model text not null default '',
  size int not null,
  num int not null,
  number text not null,
  customer_id bigint references sale_route_customers(id) on delete set null,
  since date,
  note text,
  created_at timestamptz not null default now(),
  unique (branch, model, size, num)
);
create index if not exists bins_customer on bins(customer_id);
create table if not exists bin_moves (
  id bigserial primary key,
  bin_id bigint not null references bins(id) on delete cascade,
  from_customer bigint,
  to_customer bigint,
  at timestamptz not null default now(),
  who text,
  note text
);
create index if not exists bin_moves_bin on bin_moves(bin_id);
alter table sale_route_customers add column if not exists bins_legacy text;
alter table sale_route_customers add column if not exists bins_other text;
alter table bins enable row level security;
alter table bin_moves enable row level security;
