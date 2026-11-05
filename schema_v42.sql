-- v42: accounting documents (เอกสารเบิกกลาง / สิ้นเดือน). Applied 5 Nov 2026 via MCP (migration schema_v42_acc_docs).
create table if not exists acc_docs (
  id uuid primary key default gen_random_uuid(),
  batch text not null,
  feed text,
  branch text,
  file_name text,
  page int not null,
  kind text not null check (kind in ('cover','expense','fuel','fuel_table','advance','memo','other')),
  title text,
  date_from date,
  date_to date,
  written_total numeric,
  read_total numeric,
  status text not null default 'ok' check (status in ('ok','mismatch','checked')),
  note text,
  summary text,
  person text,
  created_at timestamptz not null default now(),
  unique (batch, page)
);
alter table acc_entries add column if not exists doc_id uuid references acc_docs(id) on delete cascade;
create index if not exists acc_entries_doc_id on acc_entries(doc_id);
alter table acc_docs enable row level security;
