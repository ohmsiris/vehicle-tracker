-- v74: ใบอนุญาต · เอกสาร -- the company's licences and certificates and the drivers' licences, each with its scans (owner, 12 Nov 2026)
create table if not exists company_docs (
  id bigserial primary key,
  kind text not null,                         -- app/licences.py KINDS
  title text not null,
  company text not null default 'บริษัท สระบุรีวารีเทพ จำกัด',
  place text,                                 -- สำนักงานใหญ่ / สาขา 1 / สาขา 2 ...
  employee_id uuid references employees(id) on delete set null,   -- a driver's licence
  doc_no text,
  issued_on date,
  expires_on date,                            -- expiry, or the next due date (a yearly tax); null = does not expire
  warn_days int,                              -- null = the kind's own
  submitted_on date,                          -- ยื่นแล้ว รอใบใหม่: the reminders wait
  note text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists company_docs_employee on company_docs (employee_id);

create table if not exists company_doc_files (
  id bigserial primary key,
  doc_id bigint not null references company_docs(id) on delete cascade,
  storage_path text not null,
  name text,
  mime text,
  read jsonb,                                 -- what was read off the scan
  current boolean not null default true,      -- the copy in use; older copies are kept as history, never deleted
  uploaded_at timestamptz not null default now(),
  uploaded_by text
);
create index if not exists company_doc_files_doc on company_doc_files (doc_id);

alter table company_docs enable row level security;
alter table company_doc_files enable row level security;
