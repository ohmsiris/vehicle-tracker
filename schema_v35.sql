-- ---------------------------------------------------------------------------
-- schema_v35.sql -- the mechanics who work on the MACHINES (a separate list from the truck mechanics and shops). Run after schema_v33. Safe to run twice.
--
--   machine_mechanics   who can do machine work: in-house ("internal": พนักงานของเรา) or outside ("external": ผู้รับเหมา), with a phone and a branch.
--                       Picked as "ผู้ทำ" when a machine job is recorded. The truck mechanics (shops kind 'repair', the board's fixers) are NOT in here.
-- ---------------------------------------------------------------------------
create table if not exists machine_mechanics (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    full_name   text,
    phone       text,
    kind        text not null default 'internal' check (kind in ('internal', 'external')),
    branch      text,
    note        text,
    active      boolean not null default true,
    sort_order  integer not null default 100,
    created_at  timestamptz not null default now()
);
alter table machine_mechanics enable row level security;

insert into machine_mechanics (name, full_name, phone, kind, branch, note, sort_order)
select * from (values
    ('ช่างจรูญ', 'จรูญ มินฉิมพลี', '099-545-8240', 'internal', 'สระบุรี', null, 1),
    ('ช่างอาด', 'สอาด เถื่อนผา', '083-617-6813', 'internal', 'สระบุรี', null, 2),
    ('อู๊ด', null, null, 'internal', 'สระบุรี', null, 3),
    ('ช่างใหญ่', 'TUN LIN', null, 'internal', 'แก่งคอย', null, 4),
    ('ช่างยุทธ', null, '094-249-3423', 'external', null, 'ทำโอเวอร์ฮอลคอมเพรสเซอร์ (เครื่อง 4, 5) และซ่อม heat exchanger', 10),
    ('คุณไตร', null, '081-949-5993', 'external', null, null, 11),
    ('ช่างแอ้', null, null, 'external', null, 'ช่างของคุณยาย', 12)
) as v(name, full_name, phone, kind, branch, note, sort_order)
where not exists (select 1 from machine_mechanics);
