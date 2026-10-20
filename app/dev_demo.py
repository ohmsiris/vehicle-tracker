"""
Dev only: a made-up fleet history, returned by /api/dashboard when the
DEV_DASHBOARD_DEMO env var is set, so the dashboard page can be looked at and
tested without any real data. Never enabled in production (the variable is
simply not set there). Deterministic -- same rows every run.
"""
from datetime import date

from app.dashboard import add_months

VEHICLES = [("v1", "84-4786", "36", "สระบุรี"), ("v2", "81-3195", "7", "แก่งคอย"), ("v3", "1ฒถ 8966", "1", "สระบุรี"),
            ("v4", "70-1234", "12", "แก่งคอย"), ("v5", "1กข 4267", "18", "สระบุรี"), ("v6", "2กค 9999", "50", "สระบุรี")]
CATEGORIES = [("ไส้กรองและของเหลว", "Filters & Fluids"), ("เบรก", "Brakes"), ("ช่วงล่าง", "Suspension"),
              ("ล้อและยาง", "Wheels & Tires"), ("ค่าแรง", "Labor"), ("ระบบไฟฟ้า", "Electrical")]
SHOPS = ["ชมรมย์การช่าง", "โชคพัฒนา", "จึงกงเฮง", "ศูนย์ฮีโน่"]


def rows(today: date) -> dict:
    events, lines = [], []
    n = 0
    for back in range(11, -1, -1):
        month = add_months(today.replace(day=1), -back)
        for i, (vid, plate, truck, branch) in enumerate(VEHICLES):
            if (back + i) % 3 == 2:
                continue                      # not every truck every month
            n += 1
            day = min(3 + ((back * 7 + i * 5) % 24), 28)
            d = month.replace(day=day)
            if d > today:
                continue
            total = 800 + ((n * 937) % 9000) + (6000 if (n % 11 == 0) else 0)
            events.append({"event_id": f"e{n}", "event_date": d.isoformat(), "event_month": month.isoformat(), "vehicle_id": vid,
                           "plate_number": plate, "vehicle_number": truck, "vehicle_branch": branch,
                           "vendor": SHOPS[n % len(SHOPS)], "total_cost": float(total), "source": "bill"})
            split = [0.45, 0.25, 0.15, 0.15]
            for k, share in enumerate(split):
                cat, en = CATEGORIES[(n + k) % len(CATEGORIES)]
                lines.append({"event_month": month.isoformat(), "category": cat, "category_en": en,
                              "line_total": round(total * share, 2), "vehicle_branch": branch})
    # v6 never serviced; v4's last repair is old
    events = [e for e in events if e["vehicle_id"] != "v6" and not (e["vehicle_id"] == "v4" and e["event_date"] > add_months(today.replace(day=1), -5).isoformat())]
    last = {}
    for e in events:
        last[e["vehicle_id"]] = max(last.get(e["vehicle_id"], "0000"), e["event_date"])
    vehicles = []
    for vid, plate, truck, branch in VEHICLES:
        ls = last.get(vid)
        vehicles.append({"vehicle_id": vid, "plate_number": plate, "vehicle_number": truck, "branch": branch,
                         "open_requests": {"v3": 2, "v5": 1}.get(vid, 0), "awaiting_bill_requests": {"v1": 1, "v2": 2}.get(vid, 0),
                         "last_service_date": ls, "last_known_mileage": 480000 + int(truck) * 1000 if ls else None,
                         "next_service_mileage": 490000 + int(truck) * 1000 if (ls and vid in ("v1", "v3")) else None})
    requests = [
        {"status": "open", "branch": "สระบุรี", "days_in_status": 4}, {"status": "open", "branch": "สระบุรี", "days_in_status": 11},
        {"status": "open", "branch": "สระบุรี", "days_in_status": 2},
        {"status": "awaiting_bill", "branch": "สระบุรี", "days_in_status": 2, "days_to_fix": 3, "fixed_date": today.replace(day=1).isoformat(), "has_record": False},
        {"status": "awaiting_bill", "branch": "แก่งคอย", "days_in_status": 8, "days_to_fix": 5, "fixed_date": today.replace(day=1).isoformat(), "has_record": False},
        {"status": "awaiting_bill", "branch": "แก่งคอย", "days_in_status": 15, "days_to_fix": 1, "fixed_date": add_months(today.replace(day=1), -1).isoformat(), "has_record": False},
        {"status": "completed", "branch": "สระบุรี", "no_bill": True, "has_record": False, "days_to_fix": 2, "fixed_date": today.replace(day=1).isoformat(), "status_changed_date": today.replace(day=1).isoformat()},
        {"status": "completed", "branch": "สระบุรี", "no_bill": False, "has_record": True, "days_to_fix": 6, "fixed_date": today.replace(day=1).isoformat(), "status_changed_date": today.replace(day=1).isoformat()},
    ]
    return {"events": events, "lines": lines, "vehicles": vehicles, "requests": requests}
