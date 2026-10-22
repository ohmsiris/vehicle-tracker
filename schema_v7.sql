-- ---------------------------------------------------------------------------
-- schema_v7.sql -- everything on the master vehicle list
--
-- Run once in the Supabase SQL editor, AFTER schema_v6. Safe to run twice.
-- Adds the columns the master sheet (Drivers.xlsx) carries:
--   vehicles.vehicle_class       รถเล็ก / รถใหญ่  (decides how often oil is changed later)
--   vehicles.color, engine_number, chassis_number
--   vehicles.registered_date, registration_type   (from the registration papers)
--   vehicles.owner_name          ชื่อกรรมสิทธิ์ (who owns it on paper)
--   vehicles.holder_name         ชื่อผู้ครอบครอง (who holds it)
--   drivers.nickname             ชื่อเล่น  (drivers.name stays the full name)
-- ---------------------------------------------------------------------------

alter table vehicles add column if not exists vehicle_class     text;
alter table vehicles add column if not exists color             text;
alter table vehicles add column if not exists engine_number     text;
alter table vehicles add column if not exists chassis_number    text;
alter table vehicles add column if not exists registered_date   date;
alter table vehicles add column if not exists registration_type text;
alter table vehicles add column if not exists owner_name        text;
alter table vehicles add column if not exists holder_name       text;

alter table drivers  add column if not exists nickname          text;

-- Make the API pick up the new columns straight away.
notify pgrst, 'reload schema';
