-- v84 (owner, 17 Nov 2026): a spare driver only drives and delivers -- no commission. A day is simply ticked สำรองขับ: the truck's
-- driver has the day off and the day's sales count for nobody. Replaces v83's driven_by (no data in it yet).
alter table route_driver_days add column if not exists spare boolean not null default false;
alter table route_driver_days drop column if exists driven_by;
