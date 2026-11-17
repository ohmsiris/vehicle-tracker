-- v83 (owner, 17 Nov 2026): ยอดขายรายวัน -- a day on a driver's sheet can be driven by a spare driver; that day counts for the spare.
alter table route_driver_days add column if not exists driven_by uuid references employees(id) on delete set null;
