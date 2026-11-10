-- v60: สายรถ · ใบขาย -- a sent day is locked on the phone; the driver can ask the office to reopen it, and every reopen is kept.
alter table sale_days add column if not exists reopen_requested_at timestamptz;
alter table sale_days add column if not exists reopen_log jsonb not null default '[]'::jsonb;   -- [{"at", "what": "reopened" | "requested"}]
