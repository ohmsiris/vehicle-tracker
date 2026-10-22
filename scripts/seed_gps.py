"""
Seeds each truck's daily distance from the GPS app (screenshots of Sep-Oct 2026) into km_readings, so the
maintenance program has a real km/day for those trucks instead of the class average.

    .venv/Scripts/python.exe scripts/seed_gps.py            # dry run
    .venv/Scripts/python.exe scripts/seed_gps.py --write    # does it

Needs schema_v10.sql. Safe to run again: a truck that already has a GPS reading for the same period is skipped.
The distance entered is the GPS average (km/day) x the days in the period, so it reproduces that average.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import service_plan, supabase      # noqa: E402

OCT = ("2026-10-01", "2026-10-22", 22)
SEP = ("2026-09-01", "2026-09-30", 30)
# plate (as the GPS app shows it), km/day, period, what the truck is
GPS = [
    ("1ฒย4270", 179.5, OCT), ("1ฒฬ4856", 178.0, OCT), ("3ฒฆ8580", 129.8, OCT), ("1ฒบ6251", 128.8, OCT), ("1ฒฬ4880", 109.7, OCT),
    ("1ฒถ2085", 48.3, OCT), ("84-4771", 136.3, OCT), ("81-4648", 74.3, SEP), ("86-8971", 90.7, OCT), ("86-3139", 70.2, SEP),
    ("87-4620", 55.0, SEP), ("85-7588", 242.3, OCT),
]


def squash(text) -> str:
    return re.sub(r"[\s\-_.]", "", str(text or "")).lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not supabase.configured():
        sys.exit("Supabase ยังไม่ได้ตั้งค่า (.env)")
    if not service_plan.has_v10():
        sys.exit("ต้องรัน schema_v10.sql ใน Supabase ก่อน")
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch", "order": "id.asc"})
    by_plate = {squash(v["plate_number"]): v for v in vehicles}
    have = {(r["vehicle_id"], r["start_date"], r["end_date"]) for r in supabase.select_all("km_readings", {"select": "vehicle_id,start_date,end_date,kind", "kind": "eq.gps", "order": "id.asc"})}
    rows = []
    for plate, per_day, (start, end, days) in GPS:
        v = by_plate.get(squash(plate))
        if not v:
            print(f"ไม่พบทะเบียน {plate} ในระบบ — ข้าม")
            continue
        if (v["id"], start, end) in have:
            print(f"มีแล้ว: {v['plate_number']}")
            continue
        km = round(per_day * days)
        rows.append({"vehicle_id": v["id"], "kind": "gps", "start_date": start, "end_date": end, "km": km,
                     "notes": f"จากแอป GPS เฉลี่ย {per_day} กม./วัน"})
        print(f"{v['branch']} #{v['vehicle_number']} {v['plate_number']}: {km} กม. ({start} ถึง {end}, {per_day} กม./วัน)")
    if not args.write:
        print(f"\n(dry run — จะเพิ่ม {len(rows)} รายการ  ใส่ --write เพื่อทำจริง)")
        return
    if rows:
        supabase.insert("km_readings", rows)
    print(f"\nเพิ่ม {len(rows)} รายการแล้ว")


if __name__ == "__main__":
    main()
