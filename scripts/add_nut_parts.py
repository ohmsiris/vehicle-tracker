"""Big parts bought at ร้านนัท อะไหล่ยนต์ (เซียงกงวังน้อย) for Saraburi small trucks, fitted elsewhere (Isuzu / ช่างเอ๋) -- owner, 9 Nov 2026.

    python scripts/add_nut_parts.py [--write]

Each becomes a confirmed bill in the truck's history (with its line) and, through sync_program, a truck-repair entry in the books.
Re-running skips a truck / date / amount that is already there.
"""
import os
import sys
import uuid
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import supabase  # noqa: E402

VENDOR = "ร้านนัท อะไหล่ยนต์ (เซียงกงวังน้อย)"
ISUZU = "อิซูซุจึงกงเฮงแก่งคอย"
# (truck no., date, item, category, amount, fitted by, note)
PARTS = [
    ("22", "2026-10-16", "หัวฉีด All New U4 (1 ชุด)", "ระบบเชื้อเพลิง", 14000, ISUZU, "บิลร้านนัท 16/10/69"),
    ("22", "2026-09-25", "ฝาสูบ All New U4", "เครื่องยนต์", 18000, ISUZU, "ไม่มีบิล — ราคาตามเจ้าของ"),
    ("9", "2026-09-25", "เครื่อง D-Max U4 (เครื่องเปลี่ยน)", "เครื่องยนต์", 68000, ISUZU, "ไม่มีบิล — ราคาตามเจ้าของ"),
    ("11", "2026-11-04", "เครื่อง D-Max U4 (เครื่องเปลี่ยน)", "เครื่องยนต์", 68000, ISUZU, "บิลร้านนัท 04/11/69"),
    ("20", "2026-11-04", "เพลาท้าย All New ตัวเตี้ย", "ระบบส่งกำลัง", 11000, "ช่างเอ๋", "บิลร้านนัท 04/11/69"),
    ("6", "2026-09-25", "เพลาท้าย All New ตัวเตี้ย", "ระบบส่งกำลัง", 11000, "ช่างเอ๋", "ไม่มีบิล — ราคาตามเจ้าของ"),
]


def main() -> None:
    vehicles = {v["vehicle_number"]: v for v in supabase.select_all("vehicles", {"select": "id,vehicle_number,plate_number,branch",
                                                                                   "branch": "eq.สระบุรี", "order": "id.asc"})}
    for no, d, item, cat, amt, by, note in PARTS:
        v = vehicles[no]
        dup = supabase.select("maintenance_events", {"select": "id", "vehicle_id": f"eq.{v['id']}", "event_date": f"eq.{d}", "total_cost": f"eq.{amt}"})
        print(f"#{no} {v['plate_number']} {d} {item} {amt:,} ({by})" + (" -- already there" if dup else ""))
        if dup or "--write" not in sys.argv:
            continue
        eid = str(uuid.uuid4())
        supabase.insert("maintenance_events", [{"id": eid, "vehicle_id": v["id"], "status": "confirmed", "event_date": d, "vendor": VENDOR,
                                                "branch": "สระบุรี", "total_cost": amt, "source": "bill", "performed_by": by,
                                                "summary": f"ซื้อ{item} ร้านนัท เซียงกงวังน้อย — เปลี่ยนโดย {by}", "notes": note,
                                                "confirmed_at": date.today().isoformat()}])
        supabase.insert("bill_line_items", [{"event_id": eid, "line_number": 1, "description": item, "category": cat, "quantity": 1,
                                             "unit": "ชุด", "unit_cost": amt, "line_total": amt, "low_confidence_fields": []}])
    if "--write" in sys.argv:
        from app.accounting import sync_program
        print("synced", sync_program(date(2026, 9, 1), date(2026, 11, 30)))


if __name__ == "__main__":
    main()
