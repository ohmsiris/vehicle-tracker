-- v86 (owner, 17 Nov 2026): for some drivers (ทวาย SB-PD001, สำเริง SB-PD002, สะอาด SB-PD006) the day's money is typed as two sales figures,
-- ซอง and หลอด, in baht; เงินที่ได้ is their sum, worked out by the app and not typed.
create table if not exists route_driver_settings (
  employee_id uuid primary key references employees(id) on delete cascade,
  money_split boolean not null default false
);
alter table route_driver_settings enable row level security;
alter table route_driver_days add column if not exists money_parts jsonb;   -- {"zong": baht, "lod": baht}
insert into route_driver_settings (employee_id, money_split) values
  ('96a6e4a2-b98b-419d-af34-9e22e5012066', true), ('de5c2d81-f9aa-4f3a-b3b1-bb8519bacbd4', true), ('09149fd7-594b-4960-a082-287c7dab5b00', true)
on conflict (employee_id) do update set money_split = excluded.money_split;
