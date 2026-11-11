-- v72: น้ำมันเงินสด — no requester written (ไม่มีชื่อผู้เบิก) (owner, 11 Nov 2026)
alter table cash_fuel_lines add column if not exists no_name boolean not null default false;
