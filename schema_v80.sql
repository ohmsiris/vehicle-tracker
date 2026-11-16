-- v80 (owner, 16 Nov 2026): the CP bills are made in the app -- the bookkeeper types the date and the net weight off CPF's ใบชั่งน้ำหนัก,
-- the app gives the next number and makes the Excel bill in her exact template (app/templates/cp_bill.xlsx). Each customer: what goes on
-- its bill (name, address, tax id, terms, the items to choose from) and how its numbers run.
alter table tax_invoices add column if not exists item text;
alter table tax_invoices add column if not exists ticket_no text;          -- เลขที่บัตรชั่ง on CPF's weighing ticket (optional)
alter table tax_invoice_customers add column if not exists items jsonb not null default '[]'::jsonb;
alter table tax_invoice_customers add column if not exists bill_name text;
alter table tax_invoice_customers add column if not exists bill_address text;
alter table tax_invoice_customers add column if not exists bill_tax_id text;
alter table tax_invoice_customers add column if not exists bill_terms text;
alter table tax_invoice_customers add column if not exists number_format text;     -- 'SRI-{be}-{mm}{seq:03d}'
alter table tax_invoice_customers add column if not exists seq_monthly boolean not null default false;  -- the running number starts at 001 each month
update tax_invoice_customers set number_format = case code
    when 'P' then 'SRI-{be}-{mm}{seq:03d}'
    when '02' then 'IV-{be}-02{mm}'
    when '05' then 'IV-{be}-05{mm}{seq:03d}'
    when '18' then 'SRI-{be}-18-{mm}{seq:03d}'
    else 'SRI-{be}-' || code || '{mm}{seq:03d}' end,
  seq_monthly = code in ('P', '07', '03', '05');
update tax_invoice_customers set items = '["น้ำแข็งซอง 200 ก้อน (ไก่ชิ้นส่วน)", "น้ำแข็งซอง 100 ก้อน (ไก่ชิ้นส่วน)"]'::jsonb,
  bill_name = 'บมจ.ซีพีเอฟ (ประเทศไทย) สาขาที่ 00111',
  bill_address = '150  หมู่ 7  ถนนมิตรภาพ  ตำบลตาลเดี่ยว อำเภอแก่งคอย  จังหวัด สระบุรี 18110',
  bill_tax_id = '0107555000023', bill_terms = '30 วัน'
where code = 'P';
