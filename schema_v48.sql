-- v48: accounts the HQ cheque summaries need (Jan-Sep 2569, both branches).
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order, active) values
('1600', 'Between branches / group companies', 'บัญชีระหว่างสาขา / บริษัทในเครือ (ยืม-คืน)', 'asset', 'assets', 35, true),
('5050', 'Ice bought', 'ซื้อน้ำแข็ง (บางปะอิน / ระหว่างสาขา)', 'expense', 'cogs', 105, true),
('6300', 'Vehicle tax and compulsory insurance', 'ภาษีรถ / พรบ.', 'expense', 'selling', 250, true),
('7120', 'Foreign worker fees', 'ค่าดำเนินการแรงงานต่างด้าว', 'expense', 'admin', 312, true)
on conflict (code) do nothing;
