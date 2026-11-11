-- v71: น้ำมันเงินสด — a fill with no truck written (ไม่มีทะเบียนรถ), counted without a truck (owner, 11 Nov 2026)
alter table cash_fuel_lines add column if not exists no_truck boolean not null default false;
