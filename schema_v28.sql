-- ---------------------------------------------------------------------------
-- schema_v28.sql -- one standard name for every stock item. Run after schema_v16. Safe to run twice.
--
--   stock_items.kind    ชนิด   (แมกเนติก / โอเวอร์โหลด / เบรกเกอร์ / ลวดเชื่อม ...)
--   stock_items.brand   ยี่ห้อ (Fuji / Mitsubishi / Schneider / Omron ...)
--   stock_items.model   รุ่น   (SC-N8, TH-T25 ...): the word a bill is matched by
--   The standard name = ชนิด + ยี่ห้อ + รุ่น + สเปค, built by the app; spec is the existing column (220V 40A, 12-18A ...).
-- ---------------------------------------------------------------------------
alter table stock_items add column if not exists kind text;
alter table stock_items add column if not exists brand text;
alter table stock_items add column if not exists model text;
