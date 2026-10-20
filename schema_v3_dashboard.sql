-- Vehicle Tracker -- schema v3: everything Looker Studio needs (run AFTER
-- schema.sql and schema_v2.sql).
--
--   1. Views get a few more columns for charting (month, source, category in
--      English, ...) -- existing columns are untouched, new ones go on the end.
--   2. v_requests: EVERY repair request, finished ones included, with how long
--      each took -- v_active_requests only shows what's still open.
--   3. looker_ro: a database login that can SELECT those views and nothing
--      else. Looker Studio connects as this user, never as the service key.
--
-- BEFORE RUNNING: set the password in the marked line near the bottom. The
-- script refuses to run with the placeholder or a short password -- this login
-- is reachable from the internet, so it must not be guessable.

-- ---------------------------------------------------------------------------
-- 1. Richer views (columns appended; CREATE OR REPLACE keeps existing ones)
-- ---------------------------------------------------------------------------
create or replace view v_events as
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
    (select count(*) from event_attachments a where a.event_id = e.id)                          as attachment_count,
    -- v3 additions
    e.source,
    e.performed_by,
    date_trunc('month', e.event_date)::date                                                      as event_month,
    e.vehicle_id
from maintenance_events e
join vehicles v on v.id = e.vehicle_id
where e.status = 'confirmed';

create or replace view v_line_items as
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
    li.was_low_confidence,
    -- v3 additions
    c.name_en                   as category_en,
    date_trunc('month', e.event_date)::date as event_month,
    v.branch                    as vehicle_branch,
    e.source
from bill_line_items li
join maintenance_events e on e.id = li.event_id
join vehicles v           on v.id = e.vehicle_id
left join categories c    on c.name = li.category
where e.status = 'confirmed';

-- ---------------------------------------------------------------------------
-- 2. Every repair request, with how long it took
-- ---------------------------------------------------------------------------
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
    -- days from the report to the fix (NULL until it's fixed)
    (r.fixed_date - r.request_date)                 as days_to_fix,
    r.no_bill,
    r.no_bill_reason,
    (r.fixed_event_id is not null)                  as has_record,
    r.status_changed_at::date                       as status_changed_date,
    -- how long it has sat in its current state (open / awaiting_bill only)
    case when r.status in ('open', 'awaiting_bill')
         then (current_date - r.status_changed_at::date) end as days_in_status
from repair_requests r
left join vehicles v on v.id = r.vehicle_id;

-- Views run with their owner's rights (they bypass RLS), so keep the public
-- API roles away from the new one, same as the others.
revoke all on v_requests from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. looker_ro: read-only, views only
-- ---------------------------------------------------------------------------
do $$
declare
    -- >>> SET THIS: at least 20 random characters, then run the whole file. <<<
    v_password text := 'PASTE-A-LONG-RANDOM-PASSWORD-HERE';
begin
    if v_password like 'PASTE-%' or length(v_password) < 20 then
        raise exception 'Set a real password (20+ characters) in the v_password line first.';
    end if;

    if exists (select 1 from pg_roles where rolname = 'looker_ro') then
        execute format('alter role looker_ro with login password %L', v_password);
    else
        execute format('create role looker_ro login password %L', v_password);
    end if;
end
$$;

-- Belt and braces on top of "views only": it can't write even by accident,
-- can't run a runaway query, and can't hog connections.
alter role looker_ro set default_transaction_read_only = on;
alter role looker_ro set statement_timeout = '20s';
alter role looker_ro connection limit 5;

revoke all on all tables in schema public from looker_ro;
grant usage on schema public to looker_ro;
grant select on
    v_events, v_line_items, v_attachments,
    v_active_requests, v_requests, v_vehicle_status
to looker_ro;
