-- v53: account 4900 (fines / equipment charged to staff, deposit interest) gets its own P&L section below expenses, so Revenue = sales only.
update acc_accounts set section = 'other', name_th = 'รายได้อื่น (ค่าปรับ / หักพนักงาน / ดอกเบี้ย)' where code = '4900';
