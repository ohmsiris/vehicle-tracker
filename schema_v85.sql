-- v85 (owner, 17 Nov 2026): on a สำรองขับ day, who the spare driver was (optional). Still no sales for anyone; the spare's days are counted.
alter table route_driver_days add column if not exists spare_by uuid references employees(id) on delete set null;
