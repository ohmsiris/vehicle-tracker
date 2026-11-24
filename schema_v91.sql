-- v91: a position change is just "เปลี่ยนตำแหน่ง" (owner, 24 Nov 2026 -- "sometimes it's very hard to know if it's a promote or demote").
-- New rows are kind 'change'; promote / demote / move stay allowed for rows already written that way.
alter table employee_role_changes drop constraint if exists employee_role_changes_kind_check;
alter table employee_role_changes alter column kind set default 'change';
alter table employee_role_changes add constraint employee_role_changes_kind_check check (kind in ('change', 'promote', 'demote', 'move'));
