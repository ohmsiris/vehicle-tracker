-- ---------------------------------------------------------------------------
-- schema_v31.sql -- a short remark next to an item's name, and the brand bought on a bill line. Run after schema_v16 and schema_v28. Safe to run twice.
--
--   stock_items.remark    a short explanation shown under the name ("รูมิล 35 มม. · F207 สี่เหลี่ยม · FYJ 35 TF", "ร่อง B · ยาว 29 นิ้ว"): what the name means,
--                         the shop shorthand (รูหุน / รูมิล), the other names the part goes by. Not part of the standard name.
--   purchase_lines.brand  the brand the line was bought as (read from its text: SKF, Mitsuboshi ...), so one item (a B-29 belt, a UCF207 bearing) can be priced brand by brand.
-- ---------------------------------------------------------------------------
alter table stock_items add column if not exists remark text;
alter table purchase_lines add column if not exists brand text;
