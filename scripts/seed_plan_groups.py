"""
Puts each truck in its maintenance group (the settings the owner worked out per truck in Maintenance_Plan.xlsx):
S2 route pickups (2 months per 10,000 km), S3 factory pickups (3), S6 the repair truck (6), B4 NLR on big-truck rules,
B5 route trucks (5 months per 20,000 km), B6 factory / CP trucks (6), KK1 Kaeng Khoi #1 (its own km/day).

    .venv/Scripts/python.exe scripts/seed_plan_groups.py            # dry run
    .venv/Scripts/python.exe scripts/seed_plan_groups.py --write    # does it

Needs schema_v10.sql and schema_v11.sql. Safe to run again: a truck that already has a group is left alone, so a
group changed on the site is never put back. Trucks not listed (#41 stock truck, #54 the mechanic's own) get no
schedule.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import service_plan, supabase      # noqa: E402

GROUPS = {
    "S2": ["1ฒถ 8966", "1ฒฒ 2696", "1ฒถ 2085", "1ฒถ 2090", "1ฒถ 8960", "1ฒถ 8961", "1ฒถ 8975", "1ฒบ 6252", "1ฒบ 6251", "1ฒบ 6254", "1ฒย 4267",
           "1ฒย 4274", "1ฒย 4270", "1ฒห 6200", "1ฒห 6199", "1ฒฬ 4856", "3ฒฆ-8584", "3ฒฆ-8580", "1ฒบ 6257", "1ฒบ 2086", "1ฒบ 6248", "1ฒถ 2092"],
    "S3": ["1ฒถ 8967", "1ฒถ 2091", "1ฒฒ 1987", "1ฒถ 2094", "1ฒฬ 4861", "1ฒฬ 4880", "บฉ 6161", "บพ 1093", "1ฒถ 2087", "3 ฒฆ 8578"],
    "S6": ["บฉ 6159"],
    "B4": ["2ฒฉ 3728", "2ฒฉ 3729", "2ฒฉ 3732", "2ฒฉ 3733"],
    "B5": ["84-2051", "84-2682", "84-3954", "84-4607", "84-4618", "84-4771", "84-7169", "83-2919", "82-6356"],
    "B6": ["84-2154", "84-2688", "84-3436", "84-4764", "84-4786", "81-4648", "85-3057", "82-0427", "86-3139", "87-2063", "87-4620", "86-8971",
           "81-3195", "83-5687", "81-3196", "83-5689", "86-0974", "2 ฒฆ 3727"],
    "KK1": ["85-7588"],
}


def squash(text) -> str:
    return re.sub(r"[\s\-_.]", "", str(text or "")).lower()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    if not supabase.configured():
        sys.exit("Supabase ยังไม่ได้ตั้งค่า (.env)")
    if not service_plan.has_v10():
        sys.exit("ต้องรัน schema_v10.sql และ schema_v11.sql ใน Supabase ก่อน")
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch,active", "order": "id.asc"})
    by_plate = {squash(v["plate_number"]): v for v in vehicles}
    have = {r["vehicle_id"] for r in supabase.select_all("vehicle_plans", {"select": "vehicle_id", "order": "vehicle_id.asc"})}
    rows = []
    for code, plates in GROUPS.items():
        for plate in plates:
            v = by_plate.get(squash(plate))
            if not v:
                print(f"ไม่พบทะเบียน {plate} — ข้าม")
            elif v["id"] in have:
                print(f"มีกลุ่มอยู่แล้ว: {v['plate_number']}")
            else:
                rows.append({"vehicle_id": v["id"], "group_code": code})
                print(f"{code}  {v['branch']} #{v['vehicle_number']} {v['plate_number']}")
    print(f"\nจะใส่กลุ่มให้ {len(rows)} คัน")
    if not args.write:
        print("(dry run — ยังไม่ได้เขียนอะไร  ใส่ --write เพื่อทำจริง)")
        return
    if rows:
        supabase.insert("vehicle_plans", rows, on_conflict="vehicle_id", ignore_duplicates=True)
    print("เสร็จแล้ว")


if __name__ == "__main__":
    main()
