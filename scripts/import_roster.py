"""
Imports the master vehicle list (Drivers.xlsx) into the database: drivers, which truck each drives,
usage (รถสาย / รถหน้าโรง), size class (รถเล็ก / รถใหญ่) and the registration details.

    .venv/Scripts/python.exe scripts/import_roster.py            # dry run: shows what it WOULD do
    .venv/Scripts/python.exe scripts/import_roster.py --write    # does it

Needs schema_v6.sql and schema_v7.sql to have been run. Safe to run again: it only fills what is
still empty (see app/rosterimport.py for exactly what it may change). Needs `pip install openpyxl`.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fleet, rosterimport, supabase      # noqa: E402


def read_sheets(path: Path):
    try:
        import openpyxl
    except ImportError:
        sys.exit("ต้องติดตั้ง openpyxl ก่อน:  .venv/Scripts/python.exe -m pip install openpyxl")
    wb = openpyxl.load_workbook(path, data_only=True)
    sheets = [[list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets]
    return sheets[0], (sheets[1] if len(sheets) > 1 else [])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", default=str(ROOT / "Drivers.xlsx"))
    ap.add_argument("--write", action="store_true", help="actually write (default is a dry run)")
    args = ap.parse_args()

    if not supabase.configured():
        sys.exit("Supabase ยังไม่ได้ตั้งค่า (.env)")
    if not (fleet.has_v6() and fleet.has_v7()):
        sys.exit("ต้องรัน schema_v6.sql และ schema_v7.sql ใน Supabase ก่อน")

    master_rows, registration_rows = read_sheets(Path(args.file))
    master = rosterimport.parse_master(master_rows)
    registrations = rosterimport.parse_registration(registration_rows)
    vehicles = supabase.select_all("vehicles", {"select": "*", "order": "id.asc"})
    drivers = supabase.select_all("drivers", {"select": "*", "order": "id.asc"})
    plan = rosterimport.build_plan(master, registrations, vehicles, drivers)

    print(f"แถวรถในไฟล์: {len(master)} · ข้อมูลทะเบียน: {len(registrations)} คัน · รถในระบบ: {len(vehicles)} คัน")
    print(f"ตรงกับรถในระบบ: {plan['matched']} คัน")
    print(f"จะเพิ่มคนขับใหม่: {len(plan['driver_creates'])} คน · เติมข้อมูลคนขับเดิม: {len(plan['driver_updates'])} คน")
    print(f"จะอัปเดตรถ: {len(plan['vehicle_updates'])} คัน · จะเพิ่มรถใหม่: {len(plan['vehicle_creates'])} คัน")
    for title, items in (("ข้าม", plan["skipped"]), ("ขัดแย้ง (ไม่แตะ)", plan["conflicts"]), ("ต่างจากไฟล์", plan["differences"]),
                         ("รถในระบบที่ไม่อยู่ในไฟล์", plan["db_not_in_sheet"])):
        for item in items:
            print(f"  [{title}] {item}")

    if not args.write:
        print("\n(dry run — ยังไม่ได้เขียนอะไร  ใส่ --write เพื่อทำจริง)")
        return

    ids = {rosterimport.squash(d["name"]): d["id"] for d in drivers}
    for key, d in plan["driver_creates"].items():
        row = supabase.insert("drivers", [{k: v for k, v in d.items() if v}])[0]
        ids[key] = row["id"]
    for u in plan["driver_updates"]:
        supabase.update("drivers", {"id": f"eq.{u['id']}"}, u["values"])
    for u in plan["vehicle_updates"]:
        values = dict(u["values"])
        if u["driver_key"]:
            values["main_driver_id"] = ids[u["driver_key"]]
        supabase.update("vehicles", {"id": f"eq.{u['id']}"}, values)
    for c in plan["vehicle_creates"]:
        values = {k: v for k, v in c["values"].items() if v is not None and (k != "nickname" or fleet.has_v8())}
        if c["driver_key"]:
            values["main_driver_id"] = ids[c["driver_key"]]
        supabase.insert("vehicles", [values])
    fleet._changed()
    print("\nเสร็จแล้ว")


if __name__ == "__main__":
    main()
