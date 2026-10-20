-- Vehicle Maintenance & Bill Tracker -- Supabase (Postgres) schema, DRAFT v1
--
-- Paste into Supabase's SQL editor. Safe to read top to bottom first; nothing
-- here touches existing data (fresh project assumed). Single-user by design:
-- the backend talks to Supabase with the service-role key (bypasses RLS), and
-- RLS is switched on with NO public policies so the anon key can't read or
-- write anything by accident. Photos live in Storage, not in these tables --
-- event_attachments only stores where each file is.
--
-- Flow this models: upload batch -> extraction (nothing written yet from the
-- page's point of view; the backend saves it as a 'draft' event so a refresh
-- doesn't lose the review) -> human edits -> "confirm and save" flips the
-- event to 'confirmed'. Only confirmed events should feed the dashboard.

create extension if not exists pgcrypto;  -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- categories: the 16 line-item/job categories, same list and spelling as
-- app/bill_extraction.py CATEGORIES in the NongTick repo. A table (not a
-- Postgres enum) so adding/renaming one later is an INSERT/UPDATE, not an
-- ALTER TYPE. Used by both bill_line_items.category and
-- maintenance_events.job_type (the event's dominant category, optional).
-- ---------------------------------------------------------------------------
create table categories (
    name        text primary key,
    name_en     text not null,
    sort_order  int  not null
);

insert into categories (name, name_en, sort_order) values
    ('เครื่องยนต์',          'Engine',                1),
    ('เกียร์',               'Transmission',          2),
    ('คลัตช์',               'Clutch',                3),
    ('ระบบส่งกำลัง',         'Drivetrain',            4),
    ('ช่วงล่าง',             'Suspension',            5),
    ('พวงมาลัย',             'Steering',              6),
    ('เบรก',                 'Brakes',                7),
    ('ล้อและยาง',            'Wheels & Tires',        8),
    ('ระบบไฟฟ้า',            'Electrical',            9),
    ('ระบบหล่อเย็น',         'Cooling',              10),
    ('ระบบเชื้อเพลิง',       'Fuel System',          11),
    ('ระบบแอร์',             'Air Conditioning',     12),
    ('ตัวถังและโครงรถ',      'Body & Chassis',       13),
    ('ไส้กรองและของเหลว',    'Filters & Fluids',     14),
    ('ค่าแรง',               'Labor',                15),
    ('อื่นๆ',                'Other',                16);

-- ---------------------------------------------------------------------------
-- vehicles: the fleet roster. Plate + province together identify a truck
-- (same plate number can exist in two provinces). vehicle_number is the
-- company's own fleet number, which bills sometimes show instead of a plate.
-- ---------------------------------------------------------------------------
create table vehicles (
    id               uuid primary key default gen_random_uuid(),
    plate_number     text not null,            -- number only, e.g. '1กข 4267'
    plate_province   text not null default '',  -- e.g. 'กทม'; '' when unknown
    vehicle_number   text,                      -- company fleet number
    make             text,
    model            text,
    branch           text,                      -- 'สระบุรี' / 'แก่งคอย'
    active           boolean not null default true,
    created_at       timestamptz not null default now(),
    unique (plate_number, plate_province)
);

-- ---------------------------------------------------------------------------
-- maintenance_events: one service visit = one batch of photos/PDFs.
-- status 'draft'  = extracted, being reviewed (or abandoned)
--        'confirmed' = human clicked "confirm and save"
-- Header-level fields the model wasn't sure about are listed in
-- low_confidence_fields (e.g. {'mileage','event_date'}) so the review page can
-- flag exactly those inputs instead of a single yes/no.
-- ---------------------------------------------------------------------------
create table maintenance_events (
    id                    uuid primary key default gen_random_uuid(),
    vehicle_id            uuid not null references vehicles (id),
    status                text not null default 'draft'
                              check (status in ('draft', 'confirmed')),
    event_date            date,
    mileage               integer check (mileage is null or mileage >= 0),  -- nullable: odometer data is unreliable fleet-wide
    next_service_mileage  integer check (next_service_mileage is null or next_service_mileage >= 0),
    job_type              text references categories (name),
    vendor                text,
    branch                text,
    total_cost            numeric(12, 2) check (total_cost is null or total_cost >= 0),
    notes                 text,
    low_confidence_fields text[] not null default '{}',
    extraction_model      text,               -- which model produced the draft, for comparing quality over time
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),
    confirmed_at          timestamptz,
    -- a confirmed event must be real data, not a half-filled draft
    constraint confirmed_event_is_complete check (
        status = 'draft' or (event_date is not null and confirmed_at is not null)
    )
);

create index idx_events_vehicle_date on maintenance_events (vehicle_id, event_date desc);
create index idx_events_status       on maintenance_events (status);

-- ---------------------------------------------------------------------------
-- event_attachments: every file in the batch. type 'bill' goes through
-- extraction; 'reference_photo' (truck, damage, the part) is stored as-is.
-- Files live in the Storage bucket 'event-files' (created below);
-- storage_path is the object key inside it. Images and PDFs both allowed --
-- mime_type tells the review page whether to render <img> or an embedded PDF.
-- sha256 + the unique index stop the same file being attached to an event
-- twice by accident (double-tap, re-upload after a flaky connection).
-- ---------------------------------------------------------------------------
create table event_attachments (
    id             uuid primary key default gen_random_uuid(),
    event_id       uuid not null references maintenance_events (id) on delete cascade,
    type           text not null check (type in ('bill', 'reference_photo')),
    storage_path   text not null unique,
    mime_type      text not null
                       check (mime_type in ('image/jpeg', 'image/png', 'image/webp', 'application/pdf')),
    byte_size      integer not null check (byte_size > 0),
    sha256         text not null,
    sort_order     integer not null default 0,        -- order shown on the review page
    -- Raw extraction result for a 'bill' attachment, kept as returned so a
    -- bad extraction can be re-examined later without re-running the model.
    -- NULL for reference photos, or before extraction finishes.
    extraction         jsonb,
    extraction_error   text,                           -- set if extraction failed for this file
    created_at     timestamptz not null default now(),
    unique (event_id, sha256),
    constraint extraction_only_on_bills check (type = 'bill' or (extraction is null and extraction_error is null))
);

create index idx_attachments_event on event_attachments (event_id, sort_order);

-- ---------------------------------------------------------------------------
-- bill_line_items: parsed rows from bill attachments. attachment_id says
-- WHICH bill in the batch a row came from, so the side-by-side review can
-- show each photo next to exactly its own rows. low_confidence_fields lists
-- which fields of THIS row the model flagged (e.g. {'quantity','unit_cost'});
-- was_low_confidence is derived from it, kept as a real column because it's
-- the cheap thing to filter/aggregate on in Looker ("how often do I have to
-- fix extraction, over time").
-- ---------------------------------------------------------------------------
create table bill_line_items (
    id                    uuid primary key default gen_random_uuid(),
    event_id              uuid not null references maintenance_events (id) on delete cascade,
    attachment_id         uuid references event_attachments (id) on delete set null,
    line_number           integer not null,
    description           text not null,
    category              text references categories (name),
    quantity              numeric(12, 3),
    unit                  text,
    unit_cost             numeric(12, 2),
    line_total            numeric(12, 2),
    low_confidence_fields text[] not null default '{}',
    was_low_confidence    boolean generated always as (cardinality(low_confidence_fields) > 0) stored,
    unique (event_id, line_number)
);

create index idx_line_items_event    on bill_line_items (event_id);
create index idx_line_items_category on bill_line_items (category);

-- ---------------------------------------------------------------------------
-- updated_at maintenance for events
-- ---------------------------------------------------------------------------
create or replace function set_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger trg_events_updated_at
    before update on maintenance_events
    for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- Row-level security: ON, with no policies => the anon/authenticated keys can
-- do nothing. The backend's service-role key bypasses RLS. If a login/page is
-- ever added that reads Supabase directly from the browser, add explicit
-- policies then.
-- ---------------------------------------------------------------------------
alter table categories         enable row level security;
alter table vehicles           enable row level security;
alter table maintenance_events enable row level security;
alter table event_attachments  enable row level security;
alter table bill_line_items    enable row level security;

-- ---------------------------------------------------------------------------
-- Storage bucket. PUBLIC read so Looker Studio can load photo thumbnails by
-- URL; protection is that every object path starts with a random UUID
-- (backend builds paths as <event_id>/<attachment_id>.<ext>), so URLs aren't
-- guessable. Writes still require the service-role key. If bill photos ever
-- need to be truly private, flip public to false and have the backend hand
-- out short-lived signed URLs instead (Looker would then lose the thumbnails).
-- 10 MB cap per file is plenty after client-side resizing (~400 KB typical).
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'event-files', 'event-files', true, 10485760,
    array['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
)
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Views for the dashboard. Looker Studio connects to THESE, not the raw
-- tables, and only ever sees confirmed data.
-- ---------------------------------------------------------------------------
create view v_events as
select
    e.id                        as event_id,
    e.event_date,
    v.plate_number,
    v.plate_province,
    v.vehicle_number,
    v.branch                    as vehicle_branch,
    e.vendor,
    e.job_type,
    e.mileage,
    e.next_service_mileage,
    e.total_cost,
    e.notes,
    cardinality(e.low_confidence_fields) > 0 as header_needed_review,
    (select count(*) from bill_line_items li where li.event_id = e.id)                          as line_count,
    (select count(*) from bill_line_items li where li.event_id = e.id and li.was_low_confidence) as low_confidence_line_count,
    (select count(*) from event_attachments a where a.event_id = e.id)                          as attachment_count
from maintenance_events e
join vehicles v on v.id = e.vehicle_id
where e.status = 'confirmed';

create view v_line_items as
select
    li.id                       as line_item_id,
    e.id                        as event_id,
    e.event_date,
    v.plate_number,
    v.vehicle_number,
    e.vendor,
    li.line_number,
    li.description,
    li.category,
    li.quantity,
    li.unit,
    li.unit_cost,
    li.line_total,
    li.was_low_confidence
from bill_line_items li
join maintenance_events e on e.id = li.event_id
join vehicles v           on v.id = e.vehicle_id
where e.status = 'confirmed';

-- Photo links for the dashboard: one row per attachment, with the public URL
-- built from the project URL. REPLACE <PROJECT_REF> with the id from your
-- Supabase project URL (https://<PROJECT_REF>.supabase.co) before running.
create view v_attachments as
select
    a.id                        as attachment_id,
    a.event_id,
    a.type,
    a.mime_type,
    'https://xsrqxbzwxrlfzogtqtvn.supabase.co/storage/v1/object/public/event-files/' || a.storage_path as file_url
from event_attachments a
join maintenance_events e on e.id = a.event_id
where e.status = 'confirmed';

-- Views run with their OWNER's rights, so they bypass the RLS lockdown above
-- -- and Supabase exposes everything in the public schema through its REST
-- API. Without this revoke, anyone holding the (public) anon key could read
-- the views. The service-role key and the looker_ro role below are unaffected.
revoke all on v_events, v_line_items, v_attachments from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Dashboard access: Looker Studio's PostgreSQL connector logs in with a
-- username/password, so give it a read-only role limited to the three views
-- (never the raw tables). Uncomment, set a real password, run once.
-- ---------------------------------------------------------------------------
-- create role looker_ro login password 'CHANGE-ME';
-- grant usage on schema public to looker_ro;
-- grant select on v_events, v_line_items, v_attachments to looker_ro;
