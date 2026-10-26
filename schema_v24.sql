-- ---------------------------------------------------------------------------
-- schema_v24.sql -- เอกสารพนักงาน: หนังสือเตือน and บันทึกข้อความ (employee_documents), and hr_settings (the owner's signature)
--
-- Run once in the Supabase SQL editor, AFTER schema_v21 (employees) and schema_v23 (loans). Safe to run twice. Nothing existing changes.
--
--   employee_documents  one row per paper. A DRAFT can be edited; ISSUING it gives it its number (SB001/2569, KK001/2569: branch + running
--                       number + พ.ศ., counted per branch per year) and freezes it: `snapshot` keeps the names / position / letterhead / loan table as
--                       they were that day, so an issued paper never changes when the employee record does.
--     doc_type          warning (หนังสือเตือน) / memo (บันทึกข้อความ)
--     warning_level     0 verbal (recorded) / 1 / 2 / 3 = the last warning        content: the fields typed on the form (jsonb)
--     status            draft -> issued -> signed (employee acknowledged) | refused (would not sign: witnesses) | cancelled
--   hr_settings         small key/value store for this section (the signature image, as a PNG data URI, key 'signature')
--
-- Like every table here: row-level security on, no policies -- only the server's service key can read it.
-- ---------------------------------------------------------------------------
create table if not exists employee_documents (
    id               uuid primary key default gen_random_uuid(),
    doc_type         text not null check (doc_type in ('warning', 'memo')),
    employee_id      uuid not null references employees (id) on delete restrict,
    branch           text not null,
    doc_date         date not null,
    subject          text,
    warning_level    integer check (warning_level is null or warning_level between 0 and 3),
    content          jsonb not null default '{}'::jsonb,
    loan_id          uuid references employee_loans (id) on delete set null,
    with_signature   boolean not null default true,
    status           text not null default 'draft' check (status in ('draft', 'issued', 'signed', 'refused', 'cancelled')),
    doc_year         integer,
    doc_seq          integer,
    doc_number       text,
    snapshot         jsonb,
    issued_at        timestamptz,
    acknowledged_date date,
    witnesses        text,
    notes            text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create unique index if not exists uq_employee_documents_number on employee_documents (branch, doc_year, doc_seq) where doc_seq is not null;
create index if not exists idx_employee_documents_employee on employee_documents (employee_id, doc_type, status);

drop trigger if exists trg_employee_documents_updated_at on employee_documents;
create trigger trg_employee_documents_updated_at
    before update on employee_documents
    for each row execute function set_updated_at();

create table if not exists hr_settings (
    key    text primary key,
    value  jsonb not null
);

alter table employee_documents enable row level security;
alter table hr_settings enable row level security;
revoke all on employee_documents, hr_settings from anon, authenticated;
