-- v66 (10 Nov 2026): the customer register (Drive "Customer Info" sheet, สระบุรี tab: one row per bin-loan customer, CUST_ID = the
-- Drive folder number) and its photos (ID card, the signed form ...), linked to the route customer it is. Photos live in a PRIVATE
-- bucket: ID cards must never have a public link; the app serves them only to signed-in office people (app/customers.py).
create table if not exists customer_register (
  cust_no int not null,
  branch text not null default 'สระบุรี',
  customer_id bigint references sale_route_customers(id) on delete set null,
  match text,                   -- how it was linked: truck+bin, truck+bin+name, name, bin+name, manual; null = not linked yet
  suggest jsonb not null default '[]',   -- route customer ids that might be it (for the review list)
  shop text, contact_name text, phone text, id_card text, gps text,
  driver_name text, truck_no text, loan_date date, bins jsonb not null default '[]', note text,
  updated_at timestamptz not null default now(),
  primary key (branch, cust_no)
);
create index if not exists customer_register_customer on customer_register(customer_id);
create table if not exists customer_photos (
  id bigserial primary key,
  branch text not null default 'สระบุรี',
  cust_no int not null,
  storage_path text not null unique,
  source text unique,           -- where it came from (the Drive file path): never imported twice
  name text, mime_type text, byte_size int,
  created_at timestamptz not null default now()
);
create index if not exists customer_photos_cust on customer_photos(branch, cust_no);
alter table customer_register enable row level security;
alter table customer_photos enable row level security;
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('customer-files', 'customer-files', false, 10485760, array['image/jpeg','image/png','image/webp','application/pdf'])
on conflict (id) do update set public = false;
