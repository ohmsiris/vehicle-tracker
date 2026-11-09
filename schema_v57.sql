-- v57: PEA AMR meter readings (15 minutes) for both plants, and Kaeng Khoi drivers' daily trip tables (owner-reviewed in CP Accounting).
-- Same as CP Accounting's db/icebox/schema_fin_v1_kk.sql. kwh is what the AMR report shows; kwh x multiplier = billed kWh
-- (Saraburi's AMR export is already billed kWh -> 1; Kaeng Khoi's CP Accounting export is meter kWh -> 1.5).
create table if not exists elec_amr_readings (
  id bigserial primary key,
  branch text not null,
  ca_no text not null,                         -- elec_bills.ca_no
  meter_no text,
  slot_start timestamptz not null,             -- start of the 15 minutes (Asia/Bangkok)
  tou text not null check (tou in ('A', 'B', 'C')),   -- A on-peak, B weekday off-peak, C weekend / holiday
  kwh numeric(12,3) not null,
  multiplier numeric(6,3) not null default 1,
  unique (ca_no, slot_start)
);
create index if not exists elec_amr_readings_branch on elec_amr_readings (branch, slot_start);
alter table elec_amr_readings enable row level security;

create table if not exists elec_amr_daily (           -- days the 15-minute file does not cover (Kaeng Khoi)
  ca_no text not null,
  day date not null,
  kwh numeric(12,2) not null,
  multiplier numeric(6,3) not null default 1,
  primary key (ca_no, day)
);
alter table elec_amr_daily enable row level security;

create table if not exists fin_trip_tables (
  id bigserial primary key,
  branch text not null default 'แก่งคอย',
  month text not null,                         -- company month (26th - 25th)
  employee_code text,
  driver_written text,
  rates numeric[] not null,
  written_total numeric(14,2),
  computed_total numeric(14,2),
  source_file text, source_page int,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (source_file, source_page)
);
alter table fin_trip_tables enable row level security;

create table if not exists fin_trip_days (
  id bigserial primary key,
  table_id bigint not null references fin_trip_tables (id) on delete cascade,
  trip_date date not null,
  rate numeric(10,2) not null,                 -- baht per trip; 100 = CP day trip, 200 = CP night trip
  trips numeric(6,2) not null
);
create index if not exists fin_trip_days_date on fin_trip_days (trip_date);
alter table fin_trip_days enable row level security;
