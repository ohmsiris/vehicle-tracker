-- Vehicle Tracker -- schema v4: editing a saved repair + a written summary.
-- Run AFTER schema.sql, schema_v2.sql and schema_v3_dashboard.sql. Safe to run twice.
--
--   1. maintenance_events.summary -- an optional one-line "what was done",
--      written by a person (or, later, drafted by AI). When it is empty the
--      history list falls back to naming the biggest line items.
--   2. update_maintenance_event() -- changes a SAVED event and replaces its
--      line items in ONE transaction, so a failed edit can never leave an
--      event with half its lines (PostgREST can't do that across two tables).
--      Attached photos/PDFs and the repair requests an event closed are not
--      touched by an edit.

alter table maintenance_events add column if not exists summary text;

-- p (jsonb):
-- {
--   "event_id": uuid,                      (required; must be a confirmed event)
--   "vehicle_id": uuid,                    (optional: fixes a bill filed under the wrong truck)
--   "event_date": "YYYY-MM-DD",            (required)
--   "mileage", "next_service_mileage": int, "total_cost": number,
--   "vendor", "performed_by", "branch", "notes", "summary": text,
--   "line_items": [ {attachment_id, description, category, quantity, unit,
--                    unit_cost, line_total, low_confidence_fields} ]
--                                           (replaces ALL existing lines, renumbered 1..n)
-- }
-- A line's attachment_id (which bill page it came from) is kept only if that
-- attachment really belongs to this event; otherwise it is dropped to NULL.
create or replace function update_maintenance_event(p jsonb) returns uuid
language plpgsql
as $$
declare
    v_id uuid := (p->>'event_id')::uuid;
    l    jsonb;
    n    integer := 0;
begin
    update maintenance_events
       set vehicle_id           = coalesce(nullif(p->>'vehicle_id', '')::uuid, vehicle_id),
           event_date           = (p->>'event_date')::date,
           mileage              = nullif(p->>'mileage', '')::integer,
           next_service_mileage = nullif(p->>'next_service_mileage', '')::integer,
           total_cost           = nullif(p->>'total_cost', '')::numeric,
           vendor               = nullif(p->>'vendor', ''),
           performed_by         = nullif(p->>'performed_by', ''),
           branch               = nullif(p->>'branch', ''),
           notes                = nullif(p->>'notes', ''),
           summary              = nullif(p->>'summary', '')
     where id = v_id and status = 'confirmed';
    if not found then
        raise exception 'event % not found or not confirmed', v_id using errcode = 'P0002';
    end if;

    delete from bill_line_items where event_id = v_id;

    for l in select * from jsonb_array_elements(coalesce(p->'line_items', '[]'::jsonb)) loop
        n := n + 1;
        insert into bill_line_items (
            event_id, attachment_id, line_number, description, category,
            quantity, unit, unit_cost, line_total, low_confidence_fields
        ) values (
            v_id,
            (select a.id from event_attachments a
              where a.id = nullif(l->>'attachment_id', '')::uuid and a.event_id = v_id),
            n,
            l->>'description',
            nullif(l->>'category', ''),
            nullif(l->>'quantity', '')::numeric,
            nullif(l->>'unit', ''),
            nullif(l->>'unit_cost', '')::numeric,
            nullif(l->>'line_total', '')::numeric,
            -- "the AI wasn't sure" flags are kept for lines nobody touched (the page sends them back
            -- unchanged) and cleared for lines the person edited (the page sends an empty list).
            coalesce(array(select jsonb_array_elements_text(coalesce(l->'low_confidence_fields', '[]'::jsonb))), '{}')
        );
    end loop;

    return v_id;
end;
$$;

-- Same rule as save_maintenance_event: only the backend (service role) may call it.
revoke execute on function update_maintenance_event(jsonb) from public, anon, authenticated;
