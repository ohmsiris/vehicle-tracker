"""Card fuel (Bangchak, PT) from the owner's "Drivers" Google Sheet (exported .xlsx) -> the books.

    python scripts/import_card_fuel_sheet.py "<Drivers.xlsx>" 2026 [--write]

Reads "Fuel2" (a block per month: driver table with Fuel Bangchak, the PT statement by plate beside it) and "Fuel" (PT transactions of one
month, the month named in A1). Re-running replaces those months' entries.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cardfuel  # noqa: E402

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def read(path: str, year: str) -> list[tuple[str, str, dict]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, data_only=True)
    out = []
    ws = wb["Fuel2"]
    starts = sorted((r, ws.cell(r, 7).value.strip()) for r in range(1, ws.max_row + 1)
                    if isinstance(ws.cell(r, 7).value, str) and ws.cell(r, 7).value.strip() in MONTHS)
    for i, (s, name) in enumerate(starts):
        e = starts[i + 1][0] if i + 1 < len(starts) else ws.max_row + 1
        month = f"{year}-{MONTHS.index(name) + 1:02d}"
        bangchak, pt = {}, {}
        for r in range(s, e):
            row = [ws.cell(r, c).value for c in range(1, 11)]
            if isinstance(row[0], (int, float)) and isinstance(row[1], str) and row[1].startswith("SB-"):
                if _num(row[6]):                                              # driver table: Fuel Bangchak by the driver's truck
                    key = str(row[5]) if row[5] else (f"#{int(row[4])}" if isinstance(row[4], (int, float)) else f"ไม่ระบุรถ ({row[3] or row[1]})")
                    bangchak[key] = bangchak.get(key, 0.0) + _num(row[6])
            elif row[6] and isinstance(row[7], (int, float)) and not isinstance(row[0], (int, float)):
                plate = f"{row[6]:.0f}" if isinstance(row[6], float) else str(row[6])
                pt[plate] = pt.get(plate, 0.0) + _num(row[7])     # PT statement by plate
        out += [(month, "bangchak", bangchak), (month, "pt", pt)]
    ws = wb["Fuel"]
    name = str(ws.cell(1, 1).value or "").strip()
    if name in MONTHS:
        pt = {}
        for r in range(3, ws.max_row + 1):
            plate, amount = ws.cell(r, 1).value, ws.cell(r, 4).value
            if plate and isinstance(amount, (int, float)):
                plate = f"{plate:.0f}" if isinstance(plate, float) else plate
                pt[str(plate)] = pt.get(str(plate), 0.0) + amount
        out.append((f"{year}-{MONTHS.index(name) + 1:02d}", "pt", pt))
    return out


def main() -> None:
    path, year = sys.argv[1], sys.argv[2]
    from app import supabase
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch", "order": "id.asc"})
    entries = []
    for month, station, by_plate in read(path, year):
        out = acc_cardfuel.build(month, station, by_plate, vehicles, "จากชีท Drivers")
        entries += out["entries"]
        print(f"{month} {station:<8} {len(by_plate):>3} trucks  {sum(by_plate.values()):>12,.2f}")
        for f in out["flags"]:
            print("   !", f)
    if "--write" in sys.argv:
        from app.accounting import _insert_entries, _require
        _require()
        for e in entries:
            supabase.delete("acc_entries", {"source_id": f"eq.{e['source_id']}"})
        _insert_entries(entries)
        print("written", len(entries))


if __name__ == "__main__":
    main()
