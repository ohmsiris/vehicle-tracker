-- v45: files behind the accounting checklist (แนบไฟล์). Applied 7 Nov 2026 via MCP (migration schema_v45_acc_files).
create table if not exists acc_files (
  id uuid primary key default gen_random_uuid(),
  feed text not null,            -- the checklist row (midmonth, electricity, hq_cheques ...)
  period text not null,          -- company month + half, e.g. 2026-10-2
  branch text not null,          -- สระบุรี / แก่งคอย / all
  file_name text not null,
  storage_path text not null,    -- served through the app, not a public link
  mime_type text,
  byte_size int,
  sha256 text,
  note text,
  uploaded_at timestamptz not null default now()
);
create index if not exists acc_files_period on acc_files(period, feed);
alter table acc_files enable row level security;
