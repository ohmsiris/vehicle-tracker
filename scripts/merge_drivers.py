"""
One-time merge of the drivers tab into the employees table, so a driver is ONE record edited in พนักงาน.

    .venv/Scripts/python.exe scripts/merge_drivers.py            (dry run: prints what would change)
    .venv/Scripts/python.exe scripts/merge_drivers.py --apply

  1. every `drivers` row is tied to its employee: the link made by the first import, else MANUAL below (spelling differs between the two lists);
  2. the employee takes the driver's name, nickname and phone (the drivers tab was the correct one);
  3. every employee in a driver position (พนักงานขับรถ...) then gets / refreshes their driver record from the employee (app.employees.sync_driver),
     so a driver who had no record (the Kaeng Khoi drivers ...) gets one and nobody can be on a truck without being an employee.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import employees, supabase                          # noqa: E402

# driver name in the drivers tab -> employee code (the roster spells these differently; checked by eye, see the dry run)
MANUAL = {
    "จำเนียน พิลาแก้ว": "SB-PD011", "นันทวัฒน์ แสงเพ็ง": "SB-PD041", "บุญนำ คตวงศ์ฆ้อง": "SB-PD010", "ปัณทรรศน์ โสภา": "SB-PD050",
    "ศุภกร มูลเซอร์": "SB-PD014", "สราวุฒิ ขัติวงศ์": "SB-PD029", "สะอาด ชามเกษม": "SB-PD006", "สำรวย อสุรินทร์": "SB-PD030",
    "สำเริง ทองพูล": "SB-PD002", "เอก ดีศรี": "SB-PD032", "อัฐพล เปรมชื่น": "SB-FR005",       # the last one is the mechanic (ช่าง) who is the main driver of truck 54
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    drivers = supabase.select_all("drivers", {"select": "*", "order": "name.asc"})
    emps = supabase.select_all("employees", {"select": "*", "order": "code.asc"})
    by_code = {e["code"].upper(): e for e in emps}
    by_driver = {e["driver_id"]: e for e in emps if e.get("driver_id")}
    todo, problems = [], []
    for d in drivers:
        e = by_driver.get(d["id"]) or by_code.get(MANUAL.get(d["name"], ""))
        if not e:
            problems.append(f"no employee for driver {d['name']}")
            continue
        if e.get("driver_id") and e["driver_id"] != d["id"]:
            problems.append(f"{e['code']} is already linked to another driver (wanted {d['name']})")
            continue
        values = {"full_name": d["name"], "nickname": d.get("nickname"), "phone": d.get("phone"), "driver_id": d["id"]}
        changed = {k: (e.get(k), v) for k, v in values.items() if (e.get(k) or None) != (v or None)}
        todo.append((e, values, changed))
    for e, values, changed in todo:
        print(f"{e['code']:9} {e['full_name']!r:40} <- driver {values['full_name']!r}" + (f"   changes: {changed}" if changed else "   (same)"))
    for p in problems:
        print("PROBLEM:", p)
    driver_emps = [e for e in emps if employees.is_driver_position(e.get("position"))]
    linked_after = {v["driver_id"] for _, v, _ in todo}
    new_records = [e["code"] for e in driver_emps if not e.get("driver_id") and e["id"] not in {x[0]["id"] for x in todo}]
    print(f"\ndrivers: {len(drivers)} · matched: {len(todo)} · problems: {len(problems)} · employees in a driver position without a driver record yet (will get one): {len(new_records)} {new_records}")
    if problems or not args.apply:
        print("dry run -- nothing written" if not args.apply else "stopped: fix the problems above first")
        return
    for e, values, changed in todo:
        if changed:
            supabase.update("employees", {"id": f"eq.{e['id']}"}, values)
    made = 0
    for e in supabase.select_all("employees", {"select": "*", "order": "code.asc"}):
        if employees.is_driver_position(e.get("position")) or e.get("driver_id") in linked_after:
            had = bool(e.get("driver_id"))
            employees.sync_driver(e)
            made += 0 if had else 1
    print(f"written. driver records created: {made}")


if __name__ == "__main__":
    main()
