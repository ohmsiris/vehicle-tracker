-- v58: the electricity page's AMR section reads a branch's 15-minute readings in one call: one row per meter per day (Bangkok time),
-- slot = 0..95 (15-minute index), kW = kWh x multiplier x 4, tou = one letter per slot. Returned as one jsonb value so PostgREST's
-- row limit never cuts it.
create or replace function amr_days(p_branch text default '', p_from date default '2000-01-01')
returns jsonb language sql stable as $$
  select coalesce(jsonb_agg(d order by d->>'ca', d->>'day'), '[]'::jsonb) from (
    select jsonb_build_object(
      'ca', ca_no,
      'day', (slot_start at time zone 'Asia/Bangkok')::date,
      's', array_agg(((extract(hour from slot_start at time zone 'Asia/Bangkok') * 4
                       + extract(minute from slot_start at time zone 'Asia/Bangkok') / 15))::int order by slot_start),
      'k', array_agg(round(kwh * multiplier * 4, 1) order by slot_start),
      't', string_agg(tou, '' order by slot_start)) d
    from elec_amr_readings
    where (p_branch = '' or branch = p_branch) and (slot_start at time zone 'Asia/Bangkok')::date >= p_from
    group by ca_no, (slot_start at time zone 'Asia/Bangkok')::date
  ) x
$$;
