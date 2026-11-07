-- v47: Kaeng Khoi's PEA bills (older layout): a bill PEA estimated (meter not read), and deposit interest PEA takes off a bill.
alter table elec_bills add column if not exists estimated boolean not null default false;
alter table elec_bills add column if not exists deposit_interest numeric(14,2) not null default 0;
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order, active)
values ('4900', 'Other income', 'รายได้อื่น (ดอกเบี้ย / เงินคืน)', 'income', 'revenue', 69, true)
on conflict (code) do nothing;
