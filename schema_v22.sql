-- ---------------------------------------------------------------------------
-- schema_v22.sql -- employees: คำนำหน้า (นาย / นาง / นางสาว) and เพศ. Run after schema_v21. Safe to run twice.
--   prefix   the title; full_name then holds the name WITHOUT it (documents write prefix + name: "นายสมชาย ใจดี")
--   gender   'ชาย' / 'หญิง': follows the prefix when there is one (นาย = ชาย, นาง / นางสาว = หญิง), else set by hand
-- ---------------------------------------------------------------------------
alter table employees add column if not exists prefix text;
alter table employees add column if not exists gender text;
alter table employees drop constraint if exists employees_prefix_check;
alter table employees add constraint employees_prefix_check check (prefix is null or prefix in ('นาย', 'นาง', 'นางสาว'));
alter table employees drop constraint if exists employees_gender_check;
alter table employees add constraint employees_gender_check check (gender is null or gender in ('ชาย', 'หญิง'));
