-- v62: สายรถ · ใบขาย -- why a discount / one-off free was given. 'factory' (น้ำแข็งเสีย / แตก, the plant's fault) keeps the driver's
-- commission whole: the value given away still counts in the driver's commission sales (owner, 10 Nov 2026).
alter table sale_lines add column if not exists waived numeric(12,2) not null default 0;     -- value given away (discount, or the price of ice given free)
alter table sale_lines add column if not exists waived_reason text;                          -- 'factory' | 'other'
