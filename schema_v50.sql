-- v50: staff savings held back from wages and paid back when someone leaves (เงินสะสม): a liability, not a cost.
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order, active)
values ('2400', 'Staff savings held', 'เงินสะสมพนักงาน (หักไว้ คืนเมื่อออก)', 'liability', 'liabilities', 43, true)
on conflict (code) do nothing;
