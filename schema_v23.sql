-- ---------------------------------------------------------------------------
-- schema_v23.sql -- เงินกู้ (employee loans): what an employee owes the company, and how it is paid back
--
-- Run once in the Supabase SQL editor, AFTER schema_v21 (employees). Safe to run twice. Nothing existing changes.
--
--   employee_loans         one row per loan / advance / accident cost turned into a loan: the principal, and HOW it is repaid
--     kind                 loan (เงินกู้ทั่วไป) / accident (อุบัติเหตุ / ความเสียหาย) / advance (เบิกล่วงหน้า) / other
--     repay_mode           monthly_fixed  เดือนละเท่ากัน (repay_amount)         months_equal  แบ่งเท่า ๆ กัน N เดือน (repay_months)
--                          percent        % ของยอดกู้ต่อเดือน (repay_percent)     lump_sum      หักก้อนเดียว
--                          custom         กำหนดเองทีละเดือน (schedule)           flexible      ไม่กำหนดงวด (บันทึกเมื่อคืน)
--     first_month          the month of the FIRST deduction ('YYYY-MM-01'): later than the loan month = a delayed start
--   employee_loan_entries  the ledger: repay (คืน, usually deducted from salary), add (กู้เพิ่ม), adjust (ปรับยอด, signed)
--   The balance is never stored: principal + adds + adjusts - repayments.
--
-- Like every table here: row-level security on, no policies -- only the server's service key can read it.
-- ---------------------------------------------------------------------------
create table if not exists employee_loans (
    id             uuid primary key default gen_random_uuid(),
    employee_id    uuid not null references employees (id) on delete restrict,
    kind           text not null default 'loan' check (kind in ('loan', 'accident', 'advance', 'other')),
    amount         numeric(12, 2) not null check (amount > 0),
    loan_date      date not null,
    reason         text,
    memo_ref       text,
    approved_by    text,
    repay_mode     text not null check (repay_mode in ('monthly_fixed', 'months_equal', 'percent', 'lump_sum', 'custom', 'flexible')),
    repay_amount   numeric(12, 2) check (repay_amount is null or repay_amount > 0),
    repay_months   integer check (repay_months is null or repay_months between 1 and 120),
    repay_percent  numeric(5, 2) check (repay_percent is null or (repay_percent > 0 and repay_percent <= 100)),
    schedule       jsonb,
    first_month    date,
    status         text not null default 'active' check (status in ('active', 'closed', 'cancelled')),
    closed_date    date,
    notes          text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create table if not exists employee_loan_entries (
    id          uuid primary key default gen_random_uuid(),
    loan_id     uuid not null references employee_loans (id) on delete cascade,
    entry_date  date not null,
    kind        text not null check (kind in ('repay', 'add', 'adjust')),
    amount      numeric(12, 2) not null,
    method      text not null default 'salary' check (method in ('salary', 'cash', 'transfer', 'other')),
    pay_month   date,
    note        text,
    created_at  timestamptz not null default now()
);

create index if not exists idx_employee_loans_employee on employee_loans (employee_id, status);
create index if not exists idx_employee_loan_entries_loan on employee_loan_entries (loan_id, entry_date);

drop trigger if exists trg_employee_loans_updated_at on employee_loans;
create trigger trg_employee_loans_updated_at
    before update on employee_loans
    for each row execute function set_updated_at();

alter table employee_loans enable row level security;
alter table employee_loan_entries enable row level security;
revoke all on employee_loans, employee_loan_entries from anon, authenticated;
