-- v77 (owner, 15 Nov 2026): "ครบกำหนดที่" comes from the LATEST engine-oil bill only. Before, a newer oil change with no
-- "ไมล์ครั้งต่อไป" written on it fell back to an older bill's written figure (truck 56: oil changed 19/07/2026 at 177,921 but the
-- card said due at 177,487 from February's bill). Now next_service_mileage is what the latest oil bill wrote (null if it wrote
-- nothing), and last_oil_mileage is that bill's odometer -- the app then adds the plan's oil interval (app/history.py).
create or replace view v_vehicle_status as
select
    v.id                                        as vehicle_id,
    v.plate_number,
    v.plate_province,
    v.vehicle_number,
    v.branch,
    (select count(*) from repair_requests r where r.vehicle_id = v.id and r.status = 'open')            as open_requests,
    (select count(*) from repair_requests r where r.vehicle_id = v.id and r.status = 'awaiting_bill')   as awaiting_bill_requests,
    (select min(r.request_date) from repair_requests r where r.vehicle_id = v.id and r.status = 'open') as oldest_open_request,
    (select max(e.event_date) from maintenance_events e where e.vehicle_id = v.id and e.status = 'confirmed') as last_service_date,
    current_date - (select max(e.event_date) from maintenance_events e where e.vehicle_id = v.id and e.status = 'confirmed') as days_since_service,
    (select e.mileage from maintenance_events e
      where e.vehicle_id = v.id and e.status = 'confirmed' and e.mileage is not null
      order by e.event_date desc, e.created_at desc limit 1)                                            as last_known_mileage,
    oil.next_service_mileage,
    (select count(*) from repair_requests r where r.vehicle_id = v.id and r.status = 'at_shop')         as at_shop_requests,
    oil.mileage                                                                                         as last_oil_mileage,
    oil.event_date                                                                                      as last_oil_date
from vehicles v
left join lateral (
    select e.mileage, e.next_service_mileage, e.event_date
      from maintenance_events e
     where e.vehicle_id = v.id and e.status = 'confirmed'
       and (e.mileage is not null or e.next_service_mileage is not null)
       and exists (select 1 from bill_line_items l where l.event_id = e.id and l.description ~ '^\s*(ถ่าย|เปลี่ยน|ค่า)?\s*น้ำมันเครื่อง')
     order by e.event_date desc, e.created_at desc
     limit 1) oil on true
where v.active;
