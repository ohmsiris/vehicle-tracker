-- v75: เจ้าหนี้ร้านค้า -- what each shop is owed, and what has been paid (owner, 13 Nov 2026).
-- The bills stay where they are (truck repairs / tyres in maintenance_events, factory and stock bills in purchase_bills); a payment
-- (mum's cheque, a transfer, cash) is recorded per shop and ticks the bills it pays.
create table if not exists shop_payments (
  id bigserial primary key,
  shop text not null,                          -- the shop's one name (app/shops.py canonical)
  paid_on date not null,
  method text not null check (method in ('cheque', 'transfer', 'cash', 'before')),   -- before = paid before the app tracked it
  book text,                                   -- which cheque book / account
  cheque_no text,
  amount numeric(12, 2) not null,
  note text,
  created_by text,
  created_at timestamptz not null default now()
);
create index if not exists shop_payments_shop on shop_payments (shop);

create table if not exists shop_payment_bills (
  id bigserial primary key,
  payment_id bigint not null references shop_payments(id) on delete cascade,
  source text not null check (source in ('truck', 'purchase')),
  bill_id text not null,
  amount numeric(12, 2) not null,
  unique (source, bill_id)                     -- a bill is paid once
);

create table if not exists shop_pay_settings (
  shop text primary key,
  pays_now boolean not null default false,     -- paid in cash / by transfer on the day: never owed
  updated_at timestamptz not null default now()
);

alter table shop_payments enable row level security;
alter table shop_payment_bills enable row level security;
alter table shop_pay_settings enable row level security;
