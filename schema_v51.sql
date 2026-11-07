-- v51: ice moved between the two branches (counted in each branch, left out of the combined view) and advances never recovered.
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order, active) values
('4150', 'Ice sold to the other branch', 'ขายน้ำแข็งให้อีกสาขา', 'income', 'revenue', 61, true),
('5060', 'Ice bought from the other branch', 'ซื้อน้ำแข็งจากอีกสาขา', 'expense', 'cogs', 106, true),
('7920', 'Advances not recovered', 'เงินเบิกล่วงหน้าเก็บคืนไม่ได้', 'expense', 'admin', 397, true)
on conflict (code) do nothing;
