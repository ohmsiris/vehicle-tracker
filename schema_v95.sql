-- v95: hr_suggestions may hold a position change (owner, 28 Nov 2026: HR pages of the end-of-month paper recorded straight away from
-- ตรวจเอกสารก่อนลงบัญชี -- app/inbox_hr.py leaves an applied / dismissed row here so พนักงาน › จากเอกสารสิ้นเดือน shows it).
-- Until this runs, the app still records the change; only its row here is skipped.
alter table hr_suggestions drop constraint if exists hr_suggestions_kind_check;
alter table hr_suggestions add constraint hr_suggestions_kind_check check (kind in ('resign', 'warning', 'loan', 'loan_terms', 'role_change'));
