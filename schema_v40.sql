-- v40: the tyre store (ยางสำรอง). Applied 5 Nov 2026 via MCP (migration schema_v40_tyre_store).
-- A tyre that comes off a wheel says why (removed_reason) and where it went (removed_to: spare = into the store, scrap = thrown away);
-- a tyre put on from the store has source 'spare'. Spares are COUNTED by size (not numbered): tyre_stock_moves is the ledger
-- (+1 from a truck / a purchase, -1 to a truck / scrapped, +/- a correction after a real count); stock on hand = the sum per size.
alter table tyres add column if not exists removed_reason text,
  add column if not exists removed_to text check (removed_to in ('spare', 'scrap')),
  add column if not exists source text not null default 'new' check (source in ('new', 'spare'));

create table if not exists tyre_stock_moves (
  id uuid primary key default gen_random_uuid(),
  size text not null,
  qty integer not null check (qty <> 0),
  kind text not null check (kind in ('from_truck', 'purchase', 'to_truck', 'scrap', 'count')),
  vehicle_id uuid references vehicles(id) on delete set null,
  tyre_id uuid references tyres(id) on delete set null,
  brand text,
  moved_on date not null,
  notes text,
  created_at timestamptz not null default now()
);
create index if not exists tyre_stock_moves_size on tyre_stock_moves (size);

create table if not exists tyre_stock_sizes (
  size text primary key,
  min_qty integer not null default 0 check (min_qty >= 0),
  updated_at timestamptz not null default now()
);
alter table tyre_stock_sizes add column if not exists counted_on date;      -- v40b: the day each size was last counted
