-- v54: sales from the ice program (หน้าโรง customers, both branches) kept line by line for analysis, + trade receivables.
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order, active)
values ('1200', 'Trade receivables', 'ลูกหนี้การค้า', 'asset', 'assets', 12, true)
on conflict (code) do nothing;

create table if not exists sales_lines (
  id bigserial primary key,
  branch text not null,                 -- สระบุรี / แก่งคอย
  sale_date date not null,
  month text not null,                  -- company month YYYY-MM (26th-25th)
  product text not null,
  customer text not null,
  qty numeric(14,2) not null default 0,
  unit text,
  weight_kg numeric(14,2) not null default 0,
  price numeric(14,4) not null default 0,
  amount numeric(14,2) not null default 0,
  internal boolean not null default false,   -- ice to the company's other branch
  source text not null default 'program',
  created_at timestamptz not null default now()
);
create index if not exists sales_lines_month on sales_lines (branch, month);
create index if not exists sales_lines_customer on sales_lines (customer);
alter table sales_lines enable row level security;
