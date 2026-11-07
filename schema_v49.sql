-- v49: owner's drawings (the owner's own payments made from company cheques, e.g. ผ่อนคอนโด).
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order, active)
values ('3200', 'Owner drawings', 'เงินถอนส่วนตัว / ค่าใช้จ่ายส่วนตัวเจ้าของ', 'equity', 'equity', 51, true)
on conflict (code) do nothing;
