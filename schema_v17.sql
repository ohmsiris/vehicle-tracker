-- ---------------------------------------------------------------------------
-- schema_v17.sql -- after a car comes back from a shop: ask the driver; chase the bill; keep progress notes while it is away
--
-- Run once in the Supabase SQL editor, AFTER schema_v2..v16. Safe to run twice. Nothing existing changes.
--
--   repair_requests.check_status   the question to the driver the day after the car came back:
--                                  pending -> ok ("รถปกติ") / problem ("ยังมีปัญหา", a new job is opened) / skipped
--   repair_requests.check_due      the day to ask (the day after it came back)
--   repair_request_updates         progress notes on a car that is at a shop ("รออะไหล่ 2 วัน") -- the latest shows on the card
--   mgr_tickets.request_id/_kind   a manager ticket made FOR a repair request (ask the driver / chase the bill / ask the shop
--                                  how it is going), so the two boards stay in step: answering one closes the other
--   v_active_requests              also carries those columns, and keeps a car on the board until its question is answered
--                                  (even when the bill came first)
-- ---------------------------------------------------------------------------

alter table repair_requests add column if not exists check_status text;
alter table repair_requests add column if not exists check_due    date;
alter table repair_requests add column if not exists checked_at   timestamptz;
alter table repair_requests add column if not exists check_note   text;
alter table repair_requests drop constraint if exists repair_requests_check_status_check;
alter table repair_requests add constraint repair_requests_check_status_check
    check (check_status is null or check_status in ('pending', 'ok', 'problem', 'skipped'));

create table if not exists repair_request_updates (
    id          uuid primary key default gen_random_uuid(),
    request_id  uuid not null references repair_requests (id) on delete cascade,
    note        text not null,
    created_at  timestamptz not null default now()
);
create index if not exists idx_request_updates_request on repair_request_updates (request_id, created_at desc);
alter table repair_request_updates enable row level security;
revoke all on repair_request_updates from anon, authenticated;

alter table mgr_tickets add column if not exists request_id   uuid references repair_requests (id) on delete set null;
alter table mgr_tickets add column if not exists request_kind text;
alter table mgr_tickets drop constraint if exists mgr_tickets_request_kind_check;
alter table mgr_tickets add constraint mgr_tickets_request_kind_check
    check (request_kind is null or request_kind in ('driver_check', 'bill_chase', 'shop_update'));
create index if not exists idx_mgr_tickets_request on mgr_tickets (request_id) where request_id is not null;

create or replace view v_active_requests as
 SELECT r.id AS request_id,
    r.seq AS request_no,
    r.request_date,
    r.status,
    CURRENT_DATE - r.status_changed_at::date AS days_in_status,
    r.urgent,
    r.vehicle_id,
    COALESCE(v.plate_number, r.plate_text) AS plate,
    COALESCE(v.vehicle_number, r.truck_number_text) AS truck_number,
    v.branch,
    r.reported_by,
    r.problem,
    r.fixed_by,
    r.fixed_date,
    r.notes,
    r.shop,
    r.sent_date,
    r.expected_return,
    r.on_paper,
        CASE
            WHEN r.status = 'at_shop'::text THEN CURRENT_DATE - r.sent_date
            ELSE NULL::integer
        END AS days_at_shop,
    r.check_status,
    r.check_due,
    r.checked_at,
    r.check_note,
    r.fixed_event_id,
    r.no_bill,
    (select u.note from repair_request_updates u where u.request_id = r.id order by u.created_at desc limit 1) AS last_update,
    (select u.created_at from repair_request_updates u where u.request_id = r.id order by u.created_at desc limit 1) AS last_update_at,
    (select count(*) from repair_request_updates u where u.request_id = r.id) AS update_count
   FROM repair_requests r
     LEFT JOIN vehicles v ON v.id = r.vehicle_id
  WHERE r.status = ANY (ARRAY['open'::text, 'at_shop'::text, 'awaiting_bill'::text])
     OR (r.status = 'completed'::text AND r.check_status = 'pending'::text);
