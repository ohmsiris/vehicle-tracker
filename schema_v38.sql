-- v38: a truck whose odometer does not work goes by time (its group's months), not km.
alter table vehicles add column if not exists odometer_broken boolean not null default false;
