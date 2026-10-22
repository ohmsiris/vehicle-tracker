-- ---------------------------------------------------------------------------
-- schema_v12.sql -- the manager's own ticket board (ผู้จัดการ)
--
-- Run once in the Supabase SQL editor, AFTER schema_v2..v11. Safe to run twice.
-- Nothing existing changes.
--
--   mgr_types          the kinds of ticket (ตามงาน, การเงิน, แก้ปัญหา / ซ่อม ...): label, colour, order
--   mgr_places         where it happens (สระบุรี, แก่งคอย, โรงงาน, นอกสถานที่ ...)
--   mgr_tickets        the tickets: todo -> doing -> waiting (on someone / for a result) -> done
--   mgr_recurring      recurring tickets: every X days / weekdays / day of the month ... they create tickets by themselves
--   mgr_settings       reminder settings (what time the morning summary goes to LINE ...)
--   mgr_notifications  what has already been pushed to LINE, so nothing is sent twice
-- ---------------------------------------------------------------------------

create table if not exists mgr_types (
    key            text primary key,
    label          text not null,
    color          text not null default '#6b7280',
    sort_order     integer not null default 99,
    default_urgent boolean not null default false,
    active         boolean not null default true
);

create table if not exists mgr_places (
    id          uuid primary key default gen_random_uuid(),
    label       text not null unique,
    sort_order  integer not null default 99,
    active      boolean not null default true
);

create table if not exists mgr_recurring (
    id              uuid primary key default gen_random_uuid(),
    title           text not null,
    notes           text,
    type_key        text references mgr_types (key) on update cascade,
    place           text,
    urgent          boolean not null default false,
    rule            jsonb not null,                    -- {"every": 1, "unit": "week", "weekdays": [0, 2]} -- see app/mgr_recurring.py
    time_of_day     time,
    lead_days       integer not null default 0 check (lead_days >= 0),      -- create the ticket this many days before it is due
    mode            text not null default 'calendar' check (mode in ('calendar', 'after_done')),
    next_due        date,                              -- the next occurrence that has no ticket yet
    start_date      date,
    end_date        date,
    active          boolean not null default true,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create table if not exists mgr_tickets (
    id                 uuid primary key default gen_random_uuid(),
    seq                integer generated always as identity,
    title              text not null,
    notes              text,
    type_key           text references mgr_types (key) on update cascade,
    type_source        text not null default 'manual' check (type_source in ('manual', 'ai', 'rule')),
    place              text,
    urgent             boolean not null default false,
    status             text not null default 'todo' check (status in ('todo', 'doing', 'waiting', 'done', 'cancelled')),
    due_date           date,
    due_time           time,
    remind_on          date,                           -- a follow-up date: the ticket comes back to the top that day
    waiting_on         text,
    waiting_phone      text,
    vehicle_id         uuid references vehicles (id) on delete set null,
    machine_id         uuid references machines (id) on delete set null,
    checklist          jsonb not null default '[]',    -- [{"text": "...", "done": false}]
    recurring_id       uuid references mgr_recurring (id) on delete set null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    status_changed_at  timestamptz not null default now(),
    done_at            timestamptz
);

create index if not exists idx_mgr_tickets_status on mgr_tickets (status, due_date);
-- one ticket per occurrence of a recurring ticket: generating twice can never duplicate.
-- (Not a partial index: PostgREST's on_conflict needs a plain one. Tickets with no template have a NULL recurring_id,
-- and NULLs never collide in a unique index.)
drop index if exists uq_mgr_tickets_occurrence;
create unique index if not exists uq_mgr_tickets_occurrence on mgr_tickets (recurring_id, due_date);

create table if not exists mgr_settings (
    key    text primary key,
    value  jsonb not null
);

create table if not exists mgr_notifications (
    key      text primary key,                         -- "digest:2026-10-24", "time:<ticket id>:2026-10-24"
    sent_at  timestamptz not null default now()
);

drop trigger if exists trg_mgr_tickets_updated_at on mgr_tickets;
create trigger trg_mgr_tickets_updated_at before update on mgr_tickets
    for each row execute function set_updated_at();
drop trigger if exists trg_mgr_recurring_updated_at on mgr_recurring;
create trigger trg_mgr_recurring_updated_at before update on mgr_recurring
    for each row execute function set_updated_at();

insert into mgr_types (key, label, color, sort_order, default_urgent) values
    ('chase',    'ตามงาน / ตามของ',        '#2563eb', 1,  false),
    ('finance',  'การเงิน',                '#16a34a', 2,  false),
    ('fix',      'แก้ปัญหา / ซ่อม',         '#dc2626', 3,  false),
    ('external', 'ติดต่อภายนอก',            '#7c3aed', 4,  false),
    ('customer', 'ลูกค้า / ขาย',            '#ea580c', 5,  false),
    ('purchase', 'จัดซื้อ',                 '#0891b2', 6,  false),
    ('people',   'คน',                     '#db2777', 7,  false),
    ('visit',    'ตรวจ / เยี่ยมโรงงาน',      '#65a30d', 8,  false),
    ('decide',   'ตัดสินใจ / โครงการ',       '#4f46e5', 9,  false),
    ('shopping', 'ซื้อของ',                 '#ca8a04', 10, false),
    ('report',   'รายงาน',                 '#0d9488', 11, false),
    ('personal', 'ส่วนตัว',                 '#9333ea', 12, false),
    ('other',    'อื่นๆ',                   '#6b7280', 13, false)
on conflict (key) do nothing;

insert into mgr_places (label, sort_order) values
    ('สระบุรี', 1), ('แก่งคอย', 2), ('โรงงาน', 3), ('นอกสถานที่', 4)
on conflict (label) do nothing;

insert into mgr_settings (key, value) values
    ('digest_time', '"08:00"'), ('digest_enabled', 'true'), ('timed_enabled', 'true')
on conflict (key) do nothing;

-- no policies: only the backend's service key can read or write
alter table mgr_types         enable row level security;
alter table mgr_places        enable row level security;
alter table mgr_recurring     enable row level security;
alter table mgr_tickets       enable row level security;
alter table mgr_settings      enable row level security;
alter table mgr_notifications enable row level security;
revoke all on mgr_types, mgr_places, mgr_recurring, mgr_tickets, mgr_settings, mgr_notifications from anon, authenticated;

-- Make the API pick up the new tables straight away.
notify pgrst, 'reload schema';
