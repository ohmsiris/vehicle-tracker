-- v43: accounts for the HQ cheque summary and the documents. Applied 5 Nov 2026 via MCP (migration schema_v43_acc_accounts_cheques).
insert into acc_accounts (code, name_en, name_th, kind, section, sort_order) values
 ('2300','VAT payable (paid to Revenue Dept)','ภาษีมูลค่าเพิ่มค้างจ่าย (จ่ายสรรพากร)','liability','liabilities',42),
 ('7110','Social security','ประกันสังคม','expense','admin',300),
 ('7600','Accounting and tax services','ค่าทำบัญชีและภาษี','expense','admin',305),
 ('7910','Market and police payments','ส่วย (ตำรวจ / ตม. / ตลาด)','expense','admin',398)
on conflict (code) do nothing;
