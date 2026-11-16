-- v81 (owner, 16 Nov 2026): bills for every CPF customer, not only CP.
--  * per bill (one tax invoice per delivery, her Excel bill): P CP ซิลเลอร์ (kg off CPF's weighing ticket), 07 ศูนย์จำหน่าย (ก้อน x 65 kg),
--    03 โกลบอล (กระสอบ x 25 kg)
--  * monthly (one ใบวางบิล a month listing each delivery): 10 โรงหมู (กระสอบ x 20 kg), 06 หนองโดน (bags and kg both typed)
--  * the others (ซันฟู้ด -- done by the owner's father --, ซิลเวอร์ฟาร์ม, บางปะอิน, ...) stay typed by hand, no bill file.
alter table tax_invoice_customers add column if not exists billing text not null default 'manual';   -- per_bill / monthly / manual
alter table tax_invoice_customers add column if not exists entry text not null default 'kg';         -- kg / bags / bags_kg
alter table tax_invoice_customers add column if not exists kg_per_bag numeric(10, 3);
alter table tax_invoice_customers add column if not exists item_format text;                          -- 'น้ำแข็งซอง {bags} ก้อน (ศูนย์จำหน่าย)'
create table if not exists tax_invoice_deliveries (                -- the monthly customers' deliveries (one row of their ใบวางบิล)
  id bigserial primary key,
  customer_code text not null references tax_invoice_customers(code) on update cascade,
  day date not null,
  note_no text,                                                   -- เลขที่บิลส่งของ (4733/236601)
  item text,
  bags numeric(12, 2),
  kg numeric(14, 2) not null,
  price numeric(12, 4) not null,
  amount numeric(14, 2) not null,
  invoice_id bigint references tax_invoices(id) on delete set null,   -- the month's ใบวางบิล / tax invoice it is on
  updated_by text,
  created_at timestamptz not null default now()
);
create index if not exists tax_invoice_deliveries_cust on tax_invoice_deliveries (customer_code, day);
alter table tax_invoice_deliveries enable row level security;
update tax_invoice_customers set billing = 'per_bill', entry = 'kg' where code = 'P';
update tax_invoice_customers set billing = 'per_bill', entry = 'bags', kg_per_bag = 65, item_format = 'น้ำแข็งซอง {bags} ก้อน (ศูนย์จำหน่าย)' where code = '07';
update tax_invoice_customers set billing = 'per_bill', entry = 'bags', kg_per_bag = 25, item_format = 'น้ำแข็งซองบด  จำนวน  {bags} กระสอบ' where code = '03';
update tax_invoice_customers set billing = 'monthly', entry = 'bags', kg_per_bag = 20, item_format = 'น้ำแข็งหลอดบด' where code = '10';
update tax_invoice_customers set billing = 'monthly', entry = 'bags_kg', item_format = 'น้ำแข็งซอง' where code = '06';
