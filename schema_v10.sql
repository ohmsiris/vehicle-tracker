-- ---------------------------------------------------------------------------
-- schema_v10.sql -- the vehicle maintenance program (แผนซ่อมบำรุงรถ)
--
-- Run once in the Supabase SQL editor, AFTER schema_v2..v9. Safe to run twice.
-- Nothing existing changes.
--
-- The service items themselves (engine oil, filters, gearbox oil ...) and their default intervals live in
-- app/service_plan.py -- they come from the dealership rules and the Isuzu / Toyota / Hino service guides.
-- These tables only hold what people enter:
--
--   service_records    "this truck had this item done on this date (at this odometer)"
--   km_readings        how far a truck drives: GPS distance over a period, or an odometer reading
--   service_intervals  changes to the default interval (km / months) for a class of truck
-- ---------------------------------------------------------------------------

create table if not exists service_records (
    id           uuid primary key default gen_random_uuid(),
    vehicle_id   uuid not null references vehicles (id) on delete cascade,
    item_key     text not null,                        -- engine_oil, engine_filter, gearbox_oil ... (see service_plan.py)
    done_date    date not null,
    odometer_km  integer check (odometer_km is null or odometer_km >= 0),
    source       text not null default 'manual',       -- manual / baseline ("start counting from today")
    notes        text,
    created_at   timestamptz not null default now()
);

create table if not exists km_readings (
    id          uuid primary key default gen_random_uuid(),
    vehicle_id  uuid not null references vehicles (id) on delete cascade,
    kind        text not null check (kind in ('gps', 'odometer')),
    start_date  date,                                  -- gps: first day of the period (odometer: empty)
    end_date    date not null,                         -- gps: last day; odometer: the day it was read
    km          integer not null check (km >= 0),      -- gps: distance driven in the period; odometer: the reading
    notes       text,
    created_at  timestamptz not null default now(),
    check (kind = 'odometer' or (start_date is not null and start_date <= end_date))
);

create table if not exists service_intervals (
    vehicle_class    text not null,                    -- รถเล็ก / รถใหญ่
    item_key         text not null,
    interval_km      integer check (interval_km is null or interval_km > 0),
    interval_months  integer check (interval_months is null or interval_months > 0),
    updated_at       timestamptz not null default now(),
    primary key (vehicle_class, item_key)
);

create index if not exists idx_service_records_vehicle on service_records (vehicle_id, item_key, done_date desc);
create index if not exists idx_km_readings_vehicle     on km_readings (vehicle_id, end_date desc);

-- no policies: only the backend's service key can read or write
alter table service_records   enable row level security;
alter table km_readings       enable row level security;
alter table service_intervals enable row level security;
revoke all on service_records, km_readings, service_intervals from anon, authenticated;

-- Make the API pick up the new tables straight away.
notify pgrst, 'reload schema';
