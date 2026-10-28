-- ---------------------------------------------------------------------------
-- schema_v29.sql -- a short code per stock item, and which items go on the mechanic's weekly sheet. Run after schema_v28. Safe to run twice.
--
--   stock_items.code       E012 / W003 ...: printed on the shelf label and on the weekly sheet (a letter by category + 3 digits)
--   stock_items.track_out  true = on the weekly tick sheet (every piece taken is ticked); false = cheap consumables, not ticked: counted on the shelf and
--                          the use is the difference (stock-take)
-- ---------------------------------------------------------------------------
alter table stock_items add column if not exists code text;
alter table stock_items add column if not exists track_out boolean not null default true;
create unique index if not exists uq_stock_items_code on stock_items (lower(code)) where code is not null;
