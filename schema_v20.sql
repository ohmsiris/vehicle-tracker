-- schema_v20.sql -- the items-of-our-own feature (schema_v18 / v19) was removed: the maintenance items are edited in
-- app/service_plan.py (ITEMS, DEFAULT_KM, FIXED_MONTHS and the bill rules) instead. The two items that had been added in the app
-- (ฟลัชชิ่งน้ำมันเครื่อง every 12 months, เช็คช่วงล่าง every 3 months) are now built in. Safe to run twice.
drop table if exists service_custom_items;

-- The reminder list saved on the settings page named the old custom keys; it now names the built-in ones
-- (run only if your list is saved; the app falls back to "every item" when there is none).
-- update mgr_settings set value = '["engine_oil","engine_filter","fuel_filter","air_filter","coolant","valve_adjust","valve_cover_seal","injector_washers","intake_manifold","engine_flush","gearbox_oil","diff_oil","brake_clutch_fluid","power_steering","wheel_grease","chassis_check"]'::jsonb where key = 'plan_remind_items';
