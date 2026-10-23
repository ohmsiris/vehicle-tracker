-- ---------------------------------------------------------------------------
-- schema_v15.sql -- the repair shops live in the database, editable in the app (ตั้งค่า)
--
-- Run once in the Supabase SQL editor, AFTER schema_v14.sql. Safe to run twice.
-- Until now the shops, the spellings that mean the same shop, and their phone numbers were written in app/shops.py.
-- They are copied here (so nothing changes), and from now on they are changed on the settings page.
--
--   shops          one row per place a truck is sent to (a dealership with several departments is ONE shop)
--   shop_aliases   words that, when found in a bill or a typed name, mean this shop ("จึงกงเฮง", "isuzu" ...)
--   shop_contacts  who to call: one row per person or department ("ศูนย์บริการ", "ศูนย์ซ่อมสีและตัวถัง")
--
-- The shops that were seeded into the manager's places (schema_v14) are removed from there: the board now takes its
-- shops from this table, so a shop is looked after in one place only.
-- ---------------------------------------------------------------------------

create table if not exists shops (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    sort_order  integer not null default 99,
    active      boolean not null default true,
    in_house    boolean not null default false,       -- the work done in the factory itself
    created_at  timestamptz not null default now()
);
create unique index if not exists uq_shops_name on shops (lower(name));

create table if not exists shop_aliases (
    id       uuid primary key default gen_random_uuid(),
    shop_id  uuid not null references shops (id) on delete cascade,
    alias    text not null
);
create unique index if not exists uq_shop_aliases_alias on shop_aliases (lower(alias));

create table if not exists shop_contacts (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references shops (id) on delete cascade,
    label       text not null,
    phone       text not null,
    sort_order  integer not null default 99
);

insert into shops (name, sort_order, in_house) values
    ('ชมรมย์การช่าง', 1, false), ('อู่ช่างเก่ง เจริญยนต์', 2, false), ('โชคพัฒนา', 3, false), ('มงคลไดนาโม', 4, false),
    ('24 ไดนาโม', 5, false), ('อิซูซุจึงกงเฮงแก่งคอย', 6, false), ('ฮีโน่ เอเซีย หนองแค', 7, false), ('ซ่อมในโรงงาน', 99, true)
on conflict ((lower(name))) do nothing;

insert into shop_aliases (shop_id, alias)
select s.id, a.alias from (values
    ('ซ่อมในโรงงาน', 'ช่างเอ๋'), ('ซ่อมในโรงงาน', 'ในโรงงาน'),
    ('ชมรมย์การช่าง', 'ชมรม'),
    ('อู่ช่างเก่ง เจริญยนต์', 'ช่างเก่ง'), ('อู่ช่างเก่ง เจริญยนต์', 'เก่งเจริญ'),
    ('โชคพัฒนา', 'โชค'),
    ('24 ไดนาโม', 'ช่างลิ้ม'), ('24 ไดนาโม', 'ลิ้ม'),
    ('มงคลไดนาโม', 'มงคล+ไดนาโม'),   -- "ไดนาโม" alone is a common word: needs both parts
    ('อิซูซุจึงกงเฮงแก่งคอย', 'isuzu'), ('อิซูซุจึงกงเฮงแก่งคอย', 'อีซูซุ'), ('อิซูซุจึงกงเฮงแก่งคอย', 'อิซูซุ'), ('อิซูซุจึงกงเฮงแก่งคอย', 'จึงกงเฮง'),
    ('ฮีโน่ เอเซีย หนองแค', 'hino'), ('ฮีโน่ เอเซีย หนองแค', 'ฮีโน่'), ('ฮีโน่ เอเซีย หนองแค', 'ฮีโน')
) as a (shop, alias)
join shops s on lower(s.name) = lower(a.shop)
on conflict ((lower(alias))) do nothing;

insert into shop_contacts (shop_id, label, phone, sort_order)
select s.id, c.label, c.phone, c.sort_order from (values
    ('ชมรมย์การช่าง', 'ช่างลม', '081-928-5474', 1),
    ('24 ไดนาโม', 'ช่างลิ้ม', '081-374-0394', 1),
    ('ซ่อมในโรงงาน', 'ช่างเอ๋', '085-967-4875', 1),
    ('อิซูซุจึงกงเฮงแก่งคอย', 'ศูนย์บริการ', '086-391-0548', 1),
    ('อิซูซุจึงกงเฮงแก่งคอย', 'ศูนย์ซ่อมสีและตัวถัง', '081-581-3465', 2)
) as c (shop, label, phone, sort_order)
join shops s on lower(s.name) = lower(c.shop)
where not exists (select 1 from shop_contacts x where x.shop_id = s.id and x.phone = c.phone);

delete from mgr_places where kind = 'shop';

-- no policies: only the backend's service key can read or write
alter table shops         enable row level security;
alter table shop_aliases  enable row level security;
alter table shop_contacts enable row level security;
revoke all on shops, shop_aliases, shop_contacts from anon, authenticated;

-- Make the API pick up the new tables straight away.
notify pgrst, 'reload schema';
