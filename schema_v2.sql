-- Vehicle Tracker -- schema v2 (run AFTER schema.sql, in the same Supabase project)
--
-- Adds three things:
--   1. Manual entries: a maintenance_event no longer has to come from a bill.
--      The internal mechanic's work is typed in by hand (source = 'manual'),
--      into the same event + line-item tables, so one history covers both.
--   2. repair_requests: a reminder board for the paper ใบแจ้งซ่อม log. The
--      paper stays the master; only repairs worth following up are added.
--      open -> awaiting_bill (ticked done) -> completed (bill / manual entry
--      saved), so a finished repair can't be forgotten before its bill.
--   3. save_maintenance_event(): ONE function that saves an event with its
--      photos and line items together -- all or nothing -- and can close the
--      repair requests that event resolved. The backend calls this instead
--      of inserting into four tables one by one.
-- Plus two views for the board and the dashboard.

-- ---------------------------------------------------------------------------
-- 1. Manual entries
-- ---------------------------------------------------------------------------
alter table maintenance_events
    add column if not exists source       text not null default 'bill'
        check (source in ('bill', 'manual')),
    -- Who did the work: the internal mechanic's name for a manual entry.
    -- (For a bill, `vendor` already holds the shop.)
    add column if not exists performed_by text;

-- ---------------------------------------------------------------------------
-- 2. repair_requests -- the paper ใบแจ้งซ่อม log
-- ---------------------------------------------------------------------------
create table if not exists repair_requests (
    id              uuid primary key default gen_random_uuid(),
    -- Short running number to talk about ("request #123"), separate from the uuid.
    seq             bigint generated always as identity unique,
    request_date    date not null,
    -- The truck. Nullable on purpose: some paper rows give only a truck number
    -- or only a plate, and it may not match the fleet list. Whatever was
    -- written is kept as text either way.
    vehicle_id      uuid references vehicles (id),
    truck_number_text text,
    plate_text      text,
    reported_by     text,                       -- the driver who wrote it up
    problem         text not null,
    urgent          boolean not null default false,
    -- The follow-up flow (the paper log stays the master; this is only the
    -- reminder board for what's still unfinished):
    --   open          reported, not fixed yet
    --   awaiting_bill fixed (someone ticked it done) but the bill / record
    --                 hasn't been entered yet -- this is the state that
    --                 keeps a finished repair from being forgotten
    --   completed     the bill or manual entry is saved, OR it was closed on
    --                 purpose with no bill (no_bill = true) -- from either
    --                 'open' (ticked done, nothing to record) or
    --                 'awaiting_bill' (the bill never turned up)
    --   cancelled     no longer needed
    status          text not null default 'open'
                        check (status in ('open', 'awaiting_bill', 'completed', 'cancelled')),
    -- When status last changed, so "awaiting a bill for 6 days" can be shown.
    status_changed_at timestamptz not null default now(),
    -- The paper's last column: who fixed it -- the mechanic's name, or the
    -- shop. Free text so a new shop needs no setup.
    fixed_by        text,
    fixed_date      date,
    -- The maintenance event (bill or manual entry) that did the repair, if
    -- one was recorded. Many requests can point at one event (an oil change
    -- plus a tire swap on the same visit).
    fixed_event_id  uuid references maintenance_events (id) on delete set null,
    -- True when it was completed WITHOUT a bill or manual entry. Kept as its
    -- own flag (not just "completed with no event") so a repair closed on
    -- purpose with no paperwork is visible as that later -- its cost isn't
    -- in the records -- instead of looking like a data gap.
    no_bill         boolean not null default false,
    no_bill_reason  text,                       -- optional: "shop never gave one", "minor fix", ...
    notes           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    -- Once it's been fixed, we must know when (the board sorts and ages by it).
    constraint fixed_requests_have_a_date check (
        status in ('open', 'cancelled') or fixed_date is not null
    ),
    -- no_bill only makes sense on a completed request, and never alongside a
    -- real record of the repair.
    constraint no_bill_means_completed_without_event check (
        not no_bill or (status = 'completed' and fixed_event_id is null)
    )
);

create index if not exists idx_requests_status_date on repair_requests (status, request_date);
create index if not exists idx_requests_vehicle     on repair_requests (vehicle_id);

drop trigger if exists trg_requests_updated_at on repair_requests;
create trigger trg_requests_updated_at
    before update on repair_requests
    for each row execute function set_updated_at();

create or replace function touch_status_changed_at() returns trigger as $$
begin
    if new.status is distinct from old.status then
        new.status_changed_at = now();
    end if;
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_requests_status_changed on repair_requests;
create trigger trg_requests_status_changed
    before update on repair_requests
    for each row execute function touch_status_changed_at();

alter table repair_requests enable row level security;

-- ---------------------------------------------------------------------------
-- 3. Atomic save
--
-- p (jsonb) shape -- everything optional except vehicle_id and event_date:
-- {
--   "event_id":            uuid  (client may pre-generate it, so photo paths
--                                 <event_id>/<attachment_id>.<ext> exist
--                                 before this is called),
--   "vehicle_id":          uuid,
--   "source":              "bill" | "manual",
--   "event_date":          "YYYY-MM-DD",
--   "mileage", "next_service_mileage": int,
--   "vendor", "performed_by", "branch", "notes": text,
--   "total_cost":          number,
--   "low_confidence_fields": [text],
--   "extraction_model":    text,
--   "attachments": [ {id, type, storage_path, mime_type, byte_size, sha256,
--                     sort_order, extraction, extraction_error} ],
--   "line_items":  [ {attachment_id, line_number, description, category,
--                     quantity, unit, unit_cost, line_total,
--                     low_confidence_fields} ],
--   "fixes_request_ids": [uuid]   -- repair requests this event resolved
-- }
-- Returns the event id. Any failure (bad category, duplicate photo, ...)
-- rolls back the whole thing, so there are never half-saved events.
-- ---------------------------------------------------------------------------
create or replace function save_maintenance_event(p jsonb) returns uuid
language plpgsql
as $$
declare
    v_event_id uuid := coalesce(nullif(p->>'event_id', '')::uuid, gen_random_uuid());
    v_date     date := (p->>'event_date')::date;
    a          jsonb;
    l          jsonb;
begin
    insert into maintenance_events (
        id, vehicle_id, status, source, event_date, mileage, next_service_mileage,
        vendor, performed_by, branch, total_cost, notes,
        low_confidence_fields, extraction_model, confirmed_at
    ) values (
        v_event_id,
        (p->>'vehicle_id')::uuid,
        'confirmed',
        coalesce(nullif(p->>'source', ''), 'bill'),
        v_date,
        nullif(p->>'mileage', '')::integer,
        nullif(p->>'next_service_mileage', '')::integer,
        nullif(p->>'vendor', ''),
        nullif(p->>'performed_by', ''),
        nullif(p->>'branch', ''),
        nullif(p->>'total_cost', '')::numeric,
        nullif(p->>'notes', ''),
        coalesce(array(select jsonb_array_elements_text(coalesce(p->'low_confidence_fields', '[]'::jsonb))), '{}'),
        nullif(p->>'extraction_model', ''),
        now()
    );

    for a in select * from jsonb_array_elements(coalesce(p->'attachments', '[]'::jsonb)) loop
        insert into event_attachments (
            id, event_id, type, storage_path, mime_type, byte_size, sha256,
            sort_order, extraction, extraction_error
        ) values (
            (a->>'id')::uuid,
            v_event_id,
            a->>'type',
            a->>'storage_path',
            a->>'mime_type',
            (a->>'byte_size')::integer,
            a->>'sha256',
            coalesce(nullif(a->>'sort_order', '')::integer, 0),
            nullif(a->'extraction', 'null'::jsonb),
            nullif(a->>'extraction_error', '')
        );
    end loop;

    for l in select * from jsonb_array_elements(coalesce(p->'line_items', '[]'::jsonb)) loop
        insert into bill_line_items (
            event_id, attachment_id, line_number, description, category,
            quantity, unit, unit_cost, line_total, low_confidence_fields
        ) values (
            v_event_id,
            nullif(l->>'attachment_id', '')::uuid,
            (l->>'line_number')::integer,
            l->>'description',
            nullif(l->>'category', ''),
            nullif(l->>'quantity', '')::numeric,
            nullif(l->>'unit', ''),
            nullif(l->>'unit_cost', '')::numeric,
            nullif(l->>'line_total', '')::numeric,
            coalesce(array(select jsonb_array_elements_text(coalesce(l->'low_confidence_fields', '[]'::jsonb))), '{}')
        );
    end loop;

    -- Saving the bill / manual entry is what completes a request: both
    -- 'open' (fixed and recorded in one go) and 'awaiting_bill' (the usual
    -- case -- ticked done earlier, bill arriving now). Already completed or
    -- cancelled requests are left exactly as they were.
    update repair_requests r
       set status         = 'completed',
           no_bill        = false,
           no_bill_reason = null,
           fixed_event_id = v_event_id,
           fixed_date     = coalesce(r.fixed_date, v_date),
           fixed_by       = coalesce(r.fixed_by, nullif(p->>'performed_by', ''), nullif(p->>'vendor', ''))
     where r.status in ('open', 'awaiting_bill')
       and r.id in (
            select x::uuid
              from jsonb_array_elements_text(coalesce(p->'fixes_request_ids', '[]'::jsonb)) as x
       );

    return v_event_id;
end;
$$;

-- Supabase exposes functions in the public schema over its REST API; this
-- one writes data, so only the backend's service-role key may call it.
revoke execute on function save_maintenance_event(jsonb) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Views
-- ---------------------------------------------------------------------------

-- The follow-up board: everything not finished yet -- still to be fixed
-- ('open') or fixed but waiting on its bill ('awaiting_bill'). Oldest in its
-- current state first is the app's job (days_in_status is here to sort on).
create or replace view v_active_requests as
select
    r.id                                        as request_id,
    r.seq                                       as request_no,
    r.request_date,
    r.status,
    (current_date - r.status_changed_at::date)  as days_in_status,
    r.urgent,
    r.vehicle_id,
    coalesce(v.plate_number, r.plate_text)      as plate,
    coalesce(v.vehicle_number, r.truck_number_text) as truck_number,
    v.branch,
    r.reported_by,
    r.problem,
    r.fixed_by,
    r.fixed_date,
    r.notes
from repair_requests r
left join vehicles v on v.id = r.vehicle_id
where r.status in ('open', 'awaiting_bill');

-- One row per active vehicle: what's waiting on it and when it was last
-- serviced. "Last known mileage" and "next service due" come from the most
-- recent confirmed event that recorded one -- odometer readings are patchy,
-- so these can be empty and shouldn't be read as authoritative.
create or replace view v_vehicle_status as
select
    v.id                                        as vehicle_id,
    v.plate_number,
    v.plate_province,
    v.vehicle_number,
    v.branch,
    (select count(*) from repair_requests r
      where r.vehicle_id = v.id and r.status = 'open')                        as open_requests,
    (select count(*) from repair_requests r
      where r.vehicle_id = v.id and r.status = 'awaiting_bill')               as awaiting_bill_requests,
    (select min(r.request_date) from repair_requests r
      where r.vehicle_id = v.id and r.status = 'open')                        as oldest_open_request,
    (select max(e.event_date) from maintenance_events e
      where e.vehicle_id = v.id and e.status = 'confirmed')                   as last_service_date,
    (current_date - (select max(e.event_date) from maintenance_events e
      where e.vehicle_id = v.id and e.status = 'confirmed'))                  as days_since_service,
    (select e.mileage from maintenance_events e
      where e.vehicle_id = v.id and e.status = 'confirmed' and e.mileage is not null
      order by e.event_date desc, e.created_at desc limit 1)                  as last_known_mileage,
    (select e.next_service_mileage from maintenance_events e
      where e.vehicle_id = v.id and e.status = 'confirmed' and e.next_service_mileage is not null
      order by e.event_date desc, e.created_at desc limit 1)                  as next_service_mileage
from vehicles v
where v.active;

-- Same lockdown as the v1 views: views bypass RLS, so keep them away from
-- the public anon/authenticated roles.
revoke all on v_active_requests, v_vehicle_status from anon, authenticated;

-- Dashboard role (see schema.sql): extend its read access to the new views.
-- grant select on v_active_requests, v_vehicle_status to looker_ro;
