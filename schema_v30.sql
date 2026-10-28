-- ---------------------------------------------------------------------------
-- schema_v30.sql -- what a purchase bill IS. Run after schema_v16 and schema_v9. Safe to run twice.
--
--   purchase_bills.purpose        stock      = bought for the store room (the normal bill)
--                                 repair     = parts / work for one machine's repair: the money counts, the bill becomes a repair entry of that machine
--                                 reference  = a parts list kept to know the parts of a machine model (e.g. a MYCOM 8WB-HE overhaul list): NO money counts anywhere
--   purchase_bills.machine_id     the machine of a repair bill
--   purchase_bills.machine_model  the model a reference bill is about ("MYCOM 8WB-HE"): every machine of that model shares it
--   machine_events.purchase_bill_id  the repair entry made from a repair bill (deleted with the bill)
-- ---------------------------------------------------------------------------
alter table purchase_bills add column if not exists purpose text not null default 'stock';
alter table purchase_bills drop constraint if exists purchase_bills_purpose_check;
alter table purchase_bills add constraint purchase_bills_purpose_check check (purpose in ('stock', 'repair', 'reference'));
alter table purchase_bills add column if not exists machine_id uuid references machines (id) on delete set null;
alter table purchase_bills add column if not exists machine_model text;
alter table machine_events add column if not exists purchase_bill_id uuid references purchase_bills (id) on delete cascade;
