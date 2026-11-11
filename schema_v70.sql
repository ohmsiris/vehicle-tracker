-- v70 (11 Nov 2026): a read page counts only once someone has checked it against the photo and pressed ยืนยันหน้านี้.
alter table cash_fuel_pages add column if not exists confirmed boolean not null default false;
alter table cash_fuel_pages add column if not exists confirmed_at timestamptz;
alter table cash_fuel_pages add column if not exists confirmed_by text;
