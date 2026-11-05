-- v37: two ticket types for the manager's calendar (ปฏิทิน): the colours of the truck and machine maintenance visits.
-- They are ordinary types, so their colour is changed on the settings page like the others.
insert into mgr_types (key, label, color, sort_order, default_urgent, active) values
 ('truck_plan', 'รอบซ่อมบำรุงรถ', '#92400e', 14, false, true),
 ('machine_plan', 'รอบซ่อมบำรุงเครื่องจักร', '#475569', 15, false, true)
on conflict (key) do nothing;
