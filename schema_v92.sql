-- v92: the accounting drop box (บัญชี › ลงข้อมูล › กล่องรับไฟล์; owner, 24 Nov 2026). One row per dropped file: what the app found it to be
-- (from its content), the preview it showed, and what it booked -- so re-dropping or removing a file replaces / removes exactly that.
create table if not exists inbox_files (
    id            uuid primary key default gen_random_uuid(),
    file_name     text not null,
    storage_path  text not null,                 -- private bucket
    mime_type     text,
    byte_size     bigint,
    sha256        text not null,
    kind          text,                          -- the reader that claimed it (cheques, pea_bill, payroll, card_fuel_pt, sales_program, scan ...)
    status        text not null default 'read' check (status in ('read', 'needs_choice', 'booked', 'stored', 'needs_ai', 'ai_running', 'failed')),
    month         text,                          -- company month YYYY-MM it belongs to (after the owner's choice)
    branches      text[] not null default '{}',
    preview       jsonb,
    choices       jsonb not null default '{}'::jsonb,
    batches       text[] not null default '{}',  -- the batch keys it booked (replace / unbook by these)
    booked_total  numeric,
    error         text,
    ai_usage      jsonb,
    uploaded_by   text,
    uploaded_at   timestamptz not null default now(),
    booked_at     timestamptz
);
create index if not exists idx_inbox_files_month on inbox_files (month, uploaded_at desc);
create unique index if not exists idx_inbox_files_sha on inbox_files (sha256);

alter table public.inbox_files enable row level security;
revoke all on public.inbox_files from anon, authenticated;
