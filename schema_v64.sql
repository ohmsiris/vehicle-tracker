-- v64 (10 Nov 2026): drivers no longer add customers to a route (owner: only the owner and the bookkeeper do). A shop the driver
-- sells to that is not on the list (เพิ่มลูกค้าเอง) stays on that day's sheet; its sale line has no customer_id and carries the
-- shop's name here instead.
alter table sale_lines add column if not exists walkin text;
