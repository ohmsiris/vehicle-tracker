-- ---------------------------------------------------------------------------
-- schema_v5.sql -- "at the shop": the missing step in the repair flow
--
--   reported (open) -> sent to a shop (at_shop) -> back, waiting on the bill
--   (awaiting_bill) -> bill saved (completed)
--
-- Run once in the Supabase SQL editor, AFTER schema_v2/v3/v4. Safe to run twice.
-- Nothing existing changes: every current request keeps its status, and the
-- app keeps working the same until the new buttons are used.
--
--   1. repair_requests gets: shop, sent_date, expected_return, on_paper
--      (on_paper = false marks an emergency that was never written on the FOH
--      paper sheet, so it can be found later).
--   2. status accepts 'at_shop'.
--   3. The three views learn about it (new columns are added at the END, so
--      anything that reads them by name -- the app, Looker -- is unaffected).
--   4. save_maintenance_event() also closes requests that are at a shop.
-- ---------------------------------------------------------------------------

-- 1. columns -----------------------------------------------------------------
alter table repair_requests add column if not exists shop            text;
alter table repair_requests add column if not exists sent_date       date;
alter table repair_requests add column if not exists expected_return date;
alter table repair_requests add column if not exists on_paper        boolean not null default true;

-- 2. the new state -----------------------------------------------------------
do $$
declare c text;
begin
    -- the status check was created inline in v2; find it by what it says rather than by its generated name
    for c in
        select conname from pg_constraint
         where conrelid = 'repair_requests'::regclass and contype = 'c'
           and pg_get_constraintdef(oid) like '%awaiting_bill%'
    loop
        execute format('alter table repair_requests drop constraint %I', c);
    end loop;
end
$$;

alter table repair_requests add constraint repair_requests_status_check
    check (status in ('open', 'at_shop', 'awaiting_bill', 'completed', 'cancelled'));

alter table repair_requests drop constraint if exists fixed_requests_have_a_date;
alter table repair_requests add constraint fixed_requests_have_a_date check (
    status in ('open', 'at_shop', 'cancelled') or fixed_date is not null
);

-- A request at a shop must say which shop and since when.
alter table repair_requests drop constraint if exists at_shop_has_shop_and_date;
alter table repair_requests add constraint at_shop_has_shop_and_date check (
    status <> 'at_shop' or (shop is not null and sent_date is not null)
);

alter table repair_requests drop constraint if exists sent_not_before_request;
alter table repair_requests add constraint sent_not_before_request check (
    sent_date is null or sent_date >= request_date
);

-- 3. views -------------------------------------------------------------------
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
    r.notes,
    -- v5 (appended)
    r.shop,
    r.sent_date,
    r.expected_return,
    r.on_paper,
    case when r.status = 'at_shop' then (current_date - r.sent_date) end as days_at_shop
from repair_requests r
left join vehicles v on v.id = r.vehicle_id
where r.status in ('open', 'at_shop', 'awaiting_bill');

create or replace view v_requests as
select
    r.id                                            as request_id,
    r.seq                                           as request_no,
    r.request_date,
    date_trunc('month', r.request_date)::date       as request_month,
    r.status,
    r.urgent,
    coalesce(v.plate_number, r.plate_text)          as plate,
    coalesce(v.vehicle_number, r.truck_number_text) as truck_number,
    v.branch,
    r.reported_by,
    r.problem,
    r.fixed_by,
    r.fixed_date,
    (r.fixed_date - r.request_date)                 as days_to_fix,
    r.no_bill,
    r.no_bill_reason,
    (r.fixed_event_id is not null)                  as has_record,
    r.status_changed_at::date                       as status_changed_date,
    case when r.status in ('open', 'at_shop', 'awaiting_bill')
         then (current_date - r.status_changed_at::date) end as days_in_status,
    -- v5 (appended)
    r.shop,
    r.sent_date,
    r.expected_return,
    r.on_paper,
    case when r.status = 'at_shop' then (current_date - r.sent_date) end as days_at_shop
from repair_requests r
left join vehicles v on v.id = r.vehicle_id;

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
      order by e.event_date desc, e.created_at desc limit 1)                  as next_service_mileage,
    -- v5 (appended)
    (select count(*) from repair_requests r
      where r.vehicle_id = v.id and r.status = 'at_shop')                     as at_shop_requests
from vehicles v
where v.active;

revoke all on v_active_requests, v_requests, v_vehicle_status from anon, authenticated;

-- 4. a saved bill also closes a request that is still at the shop ---------------
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

    -- Saving the bill / manual entry is what completes a request: 'open'
    -- (fixed and recorded in one go), 'at_shop' (the bill came before anyone
    -- ticked "back") and 'awaiting_bill' (the usual case). Already completed
    -- or cancelled requests are left exactly as they were.
    update repair_requests r
       set status         = 'completed',
           no_bill        = false,
           no_bill_reason = null,
           fixed_event_id = v_event_id,
           fixed_date     = coalesce(r.fixed_date, v_date),
           fixed_by       = coalesce(r.fixed_by, r.shop, nullif(p->>'performed_by', ''), nullif(p->>'vendor', ''))
     where r.status in ('open', 'at_shop', 'awaiting_bill')
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

-- Make the API pick up the new columns straight away.
notify pgrst, 'reload schema';
