-- ---------------------------------------------------------------------------
-- schema_v8.sql -- a nickname for the truck itself
--
-- Run once in the Supabase SQL editor, AFTER schema_v7. Safe to run twice.
--   vehicles.nickname   what the truck is called: หัวขาว, หัวน้ำเงิน, สต๊อคน้ำแข็ง ...
--                       (drivers have their own nickname; this one belongs to the truck)
-- A truck with no fleet number of its own shows this in the blue chip where the number would be.
-- ---------------------------------------------------------------------------

alter table vehicles add column if not exists nickname text;

-- Make the API pick up the new column straight away.
notify pgrst, 'reload schema';
