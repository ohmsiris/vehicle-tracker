-- ---------------------------------------------------------------------------
-- schema_v9.sql -- machinery: the machines, an index of their parts / specs,
-- and a log of maintenance reports
--
-- Run once in the Supabase SQL editor, AFTER schema_v2..v8. Safe to run twice.
-- Nothing existing changes.
--
--   machines         the ice machines (เครื่อง 1-5) and the brine-pond machines
--                    (เครื่องบ่อซอง): make, model, capacity, tube type
--   machine_parts    the index -- what is on each machine / in each area: motors,
--                    pumps, belts, bearings, breakers, contactors, overloads ...
--                    (the "อะไหล่ Main" + "Motor Index Main" sheets)
--   machine_events   maintenance reports: what was repaired / replaced / checked,
--                    when, by whom, what it cost (the "Saraburi_General" sheet)
-- ---------------------------------------------------------------------------

create table if not exists machines (
    id             uuid primary key default gen_random_uuid(),
    branch         text not null,
    name           text not null,                     -- "เครื่อง 1" / "เครื่องบ่อซอง Sabroe 30 ตัน"
    kind           text not null default 'เครื่อง',   -- 'เครื่อง' (numbered ice machine) or 'บ่อซอง'
    number         integer,                           -- บ่อซอง machines usually have none
    tube_type      text,                              -- หลอดใหญ่ / หลอดเหลี่ยม / หลอดเล็ก
    make           text,                              -- MYCOM / Sabroe
    model          text,                              -- 8WB-HE / SMC108L
    capacity_tons  numeric,
    notes          text,
    active         boolean not null default true,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create table if not exists machine_parts (
    id                 uuid primary key default gen_random_uuid(),
    machine_id         uuid references machines (id) on delete set null,
    location           text not null,                 -- as written on the sheet: "เครื่อง 1", "โรงซอง 1", "ระบบน้ำ" ...
    equipment          text,                          -- ปั๊มน้ำเย็น / พัดลม / สกรู 1 L / โม่ ...
    part               text,                          -- มอเตอร์ / สายพาน / ลูกปืน / Breaker / Magnetic Contactor ...
    spec               text,
    qty                numeric,
    unit               text,
    bearing_bore       text,                          -- รูลูกปืน (e.g. หุน)
    motor_hp           numeric,
    delta_a            text,                          -- text: the sheet has "22.5 (380)"
    star_a             text,
    link               text,                          -- the motor's datasheet / catalogue
    breaker            text,
    contactor          text,
    overload           text,
    status             text,                          -- Good / New / Adjust / ?
    current_contactor  text,
    current_overload   text,
    desired_a          text,
    notes              text,
    sort_order         integer not null default 0,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create table if not exists machine_events (
    id             uuid primary key default gen_random_uuid(),
    branch         text not null,
    machine_id     uuid references machines (id) on delete set null,
    area           text,                              -- for things that are not on one machine (chlorine pump, water meter ...)
    part_id        uuid references machine_parts (id) on delete set null,
    equipment      text,
    event_date     date not null,
    kind           text not null default 'ซ่อม',      -- ซ่อม / เปลี่ยนอะไหล่ / ตรวจเช็ค / ทำความสะอาด / หล่อลื่น / อื่นๆ
    summary        text not null,
    reason         text,
    brand          text,
    model          text,
    serial_number  text,
    vendor         text,
    vendor_contact text,
    performed_by   text,
    cost           numeric(12, 2) check (cost is null or cost >= 0),
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create index if not exists idx_machine_parts_machine   on machine_parts (machine_id);
create index if not exists idx_machine_parts_location  on machine_parts (location);
create index if not exists idx_machine_events_machine  on machine_events (machine_id, event_date desc);
create index if not exists idx_machine_events_date     on machine_events (event_date desc);

drop trigger if exists trg_machines_updated_at on machines;
create trigger trg_machines_updated_at before update on machines
    for each row execute function set_updated_at();
drop trigger if exists trg_machine_parts_updated_at on machine_parts;
create trigger trg_machine_parts_updated_at before update on machine_parts
    for each row execute function set_updated_at();
drop trigger if exists trg_machine_events_updated_at on machine_events;
create trigger trg_machine_events_updated_at before update on machine_events
    for each row execute function set_updated_at();

-- no policies: only the backend's service key can read or write
alter table machines       enable row level security;
alter table machine_parts  enable row level security;
alter table machine_events enable row level security;
revoke all on machines, machine_parts, machine_events from anon, authenticated;

-- Make the API pick up the new tables straight away.
notify pgrst, 'reload schema';
