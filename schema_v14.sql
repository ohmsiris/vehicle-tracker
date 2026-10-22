-- ---------------------------------------------------------------------------
-- schema_v14.sql -- places on the manager's board have a kind: branch / shop / other
--
-- Run once in the Supabase SQL editor, AFTER schema_v12.sql. Safe to run twice.
-- Nothing existing changes except that the places get a kind, and the shops the system already knows
-- (the ones the repair board sends trucks to) are added as places so a ticket can be filtered by where it is.
-- ---------------------------------------------------------------------------

alter table mgr_places add column if not exists kind text not null default 'other' check (kind in ('branch', 'shop', 'other'));

update mgr_places set kind = 'branch' where label in ('สระบุรี', 'แก่งคอย');

insert into mgr_places (label, kind, sort_order) values
    ('ชมรมย์การช่าง',          'shop', 10),
    ('อู่ช่างเก่ง เจริญยนต์',   'shop', 11),
    ('โชคพัฒนา',               'shop', 12),
    ('มงคลไดนาโม',             'shop', 13),
    ('24 ไดนาโม',              'shop', 14),
    ('อิซูซุจึงกงเฮงแก่งคอย',   'shop', 15),
    ('ฮีโน่ เอเซีย หนองแค',     'shop', 16)
on conflict (label) do nothing;

notify pgrst, 'reload schema';
