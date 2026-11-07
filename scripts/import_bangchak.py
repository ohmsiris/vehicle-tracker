"""Bangchak card fuel from the monthly files the owner sends the accountant -> the books (one entry per truck, app/acc_cardfuel.py).

    python scripts/import_bangchak.py "<folder: YYYY-MM sub-folders>" [--write]

Each YYYY-MM folder holds that company month's files from the two stations (ห้วยบง, NM):
  * .xlsx  the fill-by-fill log (sheet 2: row no., date, ..., plate given, plate actually filled, ..., litres, price, baht) -> by plate
  * .pdf   "สรุปรายคนขับ" (driver, nickname, fills, litres, baht) -> by the driver's own truck (vehicles.main_driver_id); a driver
           with no truck of their own is booked without a truck and flagged
Re-running replaces those months' Bangchak entries.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cardfuel  # noqa: E402

AMOUNT = re.compile(r"^[\d,]+\.\d{2}$")


def norm_name(s: str) -> str:
    """Names as the PDF text gives them ("ส าเริง") and as the app has them ("สำเริง") compare equal."""
    s = re.sub(r"[\s​]", "", s or "").replace("ำ", "า")
    return re.sub("[่-์ํ]", "", s)


def from_xlsx(path: str) -> dict:
    from openpyxl import load_workbook
    ws = load_workbook(path, data_only=True).worksheets[1]
    out: dict = {}
    for r in ws.iter_rows(values_only=True):
        if isinstance(r[0], (int, float)) and len(r) > 13 and isinstance(r[13], (int, float)):
            plate = str(r[5] or r[4])
            out[plate] = out.get(plate, 0.0) + float(r[13])
    return out


def from_pdf(path: str, plate_of: dict) -> dict:
    import pymupdf
    lines = [ln.strip() for p in pymupdf.open(path) for ln in p.get_text().splitlines() if ln.strip()]
    out: dict = {}
    for i, ln in enumerate(lines):
        # a driver row: name, nickname, fills, litres, baht. The page total ("รวมทั้งหมด", fills, litres, baht) has one line fewer, so its
        # "name" would be the line before -- a number -- and is skipped.
        if AMOUNT.match(ln) and i >= 4:
            name, nick = lines[i - 4], lines[i - 3]
            if re.search(r"\d", name + nick) or name.startswith("รวมทั้งหมด") or not re.match(r"^\d+$", lines[i - 2]):
                continue
            key = plate_of.get(norm_name(name)) or f"ไม่ระบุรถ ({name})"
            out[key] = out.get(key, 0.0) + float(ln.replace(",", ""))
    return out


def main() -> None:
    root = sys.argv[1]
    from app import supabase
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch,main_driver_id", "order": "id.asc"})
    drivers = supabase.select_all("drivers", {"select": "id,name", "order": "id.asc"})
    plate_of = {}
    for d in drivers:
        mine = [v for v in vehicles if v.get("main_driver_id") == d["id"]]
        if len(mine) == 1:
            plate_of[norm_name(d["name"])] = mine[0]["plate_number"]
    entries = []
    for month in sorted(os.listdir(root)):
        if not re.match(r"^\d{4}-\d{2}$", month):
            continue
        by: dict = {}
        for f in sorted(os.listdir(os.path.join(root, month))):
            path = os.path.join(root, month, f)
            part = from_xlsx(path) if f.lower().endswith(".xlsx") else from_pdf(path, plate_of) if f.lower().endswith(".pdf") else {}
            print(f"{month} {f[:40]:<40} {sum(part.values()):>12,.2f}")
            for k, v in part.items():
                by[k] = by.get(k, 0.0) + v
        out = acc_cardfuel.build(month, "bangchak", by, vehicles, "จากไฟล์สรุปบางจาก")
        entries += out["entries"]
        print(f"{month} bangchak total {sum(by.values()):,.2f} · {len(by)} trucks")
        for fl in out["flags"]:
            print("   !", fl)
    if "--write" in sys.argv:
        from app.accounting import _insert_entries, _require
        _require()
        for e in entries:
            supabase.delete("acc_entries", {"source_id": f"eq.{e['source_id']}"})
        _insert_entries(entries)
        print("written", len(entries))


if __name__ == "__main__":
    main()
