-- ---------------------------------------------------------------------------
-- schema_v36.sql -- tyres on the truck page (a chassis drawing: which tyre is on which wheel, since when). Run after schema_v35. Safe to run twice.
--
--   vehicle_tyre_specs   per truck: the tyre size front / rear, 4 or 6 wheels (6 = dual rear), radial, tubeless. A truck with no row uses
--                        its class (รถใหญ่ = 6 wheels) and no sizes.
--   tyres                one row per tyre that went on a wheel: position (FL FR RL RR, or for 6 wheels FL FR RLO RLI RRI RRO; '?' = from an old
--                        bill, wheel not known), brand, size, the day and km it went on, price, shop, the bill it came from. removed_on is
--                        set when another tyre replaces it, so the wheel's history is the removed rows.
-- ---------------------------------------------------------------------------
create table if not exists vehicle_tyre_specs (
    vehicle_id  uuid primary key references vehicles(id) on delete cascade,
    front_size  text,
    rear_size   text,
    wheels      smallint check (wheels in (4, 6)),
    radial      boolean,
    tubeless    boolean,
    notes       text,
    updated_at  timestamptz not null default now()
);
alter table vehicle_tyre_specs enable row level security;

create table if not exists tyres (
    id          uuid primary key default gen_random_uuid(),
    vehicle_id  uuid not null references vehicles(id) on delete cascade,
    position    text not null check (position in ('FL', 'FR', 'RL', 'RR', 'RLO', 'RLI', 'RRI', 'RRO', '?')),
    brand       text,
    size        text,
    fitted_on   date,
    fitted_km   integer,
    price       numeric(12, 2),
    vendor      text,
    event_id    uuid references maintenance_events(id) on delete set null,
    removed_on  date,
    removed_km  integer,
    notes       text,
    created_at  timestamptz not null default now()
);
create index if not exists tyres_vehicle_idx on tyres (vehicle_id);
alter table tyres enable row level security;

-- the sizes the owner gave (Nov 2026): D-Max / Revo, หัวน้ำเงิน, NLR (4 wheels, radial, tubeless). The 6-wheel trucks wait for the owner's check.
insert into vehicle_tyre_specs (vehicle_id, front_size, rear_size, wheels, radial, tubeless)
select v.id,
       '205/70R15',
       case when v.nickname = 'หัวน้ำเงิน' then '225/75R15' when v.model ilike 'NLR%' then '225/75R15' else '225/75R14' end,
       4, true, true
  from vehicles v
 where v.vehicle_class = 'รถเล็ก'
   and (v.model ilike 'D-Max%' or v.model ilike '%Revo%' or v.nickname = 'หัวน้ำเงิน')
on conflict (vehicle_id) do nothing;

insert into vehicle_tyre_specs (vehicle_id, front_size, rear_size, wheels, radial, tubeless)
select v.id, '225/75R15', '225/75R15', 4, true, true
  from vehicles v
 where v.vehicle_class = 'รถเล็ก' and v.model ilike 'NLR%'
on conflict (vehicle_id) do nothing;
