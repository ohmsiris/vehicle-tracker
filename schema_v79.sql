-- v79: ใบกำกับภาษี แก่งคอย (owner, 16 Nov 2026) -- every tax invoice / credit note Kaeng Khoi issues, typed by the bookkeeper; replaces the
-- Google Sheet "สรุปใบกำกับภาษี แก่งคอย". Customers by the code in the invoice number, prices per kg from a date. app/tax_invoices.py
create table if not exists tax_invoice_customers (
  code text primary key,                  -- 'P' (five digits after the year: CP ซิลเลอร์) or the two digits after the year ('07')
  name text not null,
  mode text not null default 'kg' check (mode in ('kg', 'total')),   -- 'total': billed as one typed amount (ซันฟู้ด)
  unit text not null default 'กก.',
  sort int not null default 50,
  active boolean not null default true
);
create table if not exists tax_invoice_prices (
  id bigserial primary key,
  code text not null references tax_invoice_customers(code) on update cascade,
  price numeric(12, 4) not null,
  from_date date not null,
  note text,
  created_at timestamptz not null default now()
);
create table if not exists tax_invoices (
  id bigserial primary key,
  branch text not null default 'แก่งคอย',
  kind text not null default 'invoice' check (kind in ('invoice', 'credit_note')),
  doc_no text not null,
  doc_date date,                          -- the day on the invoice (the sheet had none: imported rows keep only the month)
  month text not null,                    -- calendar month 'YYYY-MM' (the sheet's tab)
  customer_code text references tax_invoice_customers(code) on update cascade,
  customer_name text,
  kg numeric(14, 2),
  price numeric(12, 4),
  amount numeric(14, 2) not null,         -- before VAT (credit notes positive, subtracted in the summaries)
  vat numeric(14, 2) not null,
  total numeric(14, 2) not null,
  note text,
  source text not null default 'app',     -- 'sheet' = imported from the Google Sheet
  updated_by text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists tax_invoices_month on tax_invoices (branch, month);
create index if not exists tax_invoices_doc on tax_invoices (doc_no);
alter table tax_invoice_customers enable row level security;
alter table tax_invoice_prices enable row level security;
alter table tax_invoices enable row level security;
