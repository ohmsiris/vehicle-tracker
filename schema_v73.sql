-- v73: 10-wheel trucks (owner, 12 Nov 2026: แก่งคอย 1, 2, 4, 5) -- a second dual rear axle (MLO MLI MRI MRO)
alter table vehicle_tyre_specs drop constraint if exists vehicle_tyre_specs_wheels_check;
alter table vehicle_tyre_specs add constraint vehicle_tyre_specs_wheels_check check (wheels in (4, 6, 10));
alter table tyres drop constraint if exists tyres_position_check;
alter table tyres add constraint tyres_position_check check (position in ('FL','FR','RL','RR','RLO','RLI','RRI','RRO','MLO','MLI','MRI','MRO','?'));
