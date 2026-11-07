"""PT card fuel from PT's fleet-card export ("รายงานการเติมน้ำมัน.xlsx", one row per fill) -> the books (app/acc_cardfuel.py).

    python scripts/import_pt.py "<รายงานการเติมน้ำมัน.xlsx>" 2026-06 2026-10 [--write]

Only whole company months (26th-25th) between the two given months are booked; re-running replaces those months' PT entries (whatever
source wrote them). The export loses the Thai letters of the plate ("1?? 2090"), so a plate is matched on its number: the exact plate
first, else the one vehicle whose plate ends in those 4 digits. Rows that match no vehicle are booked to Saraburi with no truck and listed.
"""
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import acc_cardfuel  # noqa: E402
from app.acc_docs import norm_plate  # noqa: E402


def company_month(d) -> str:
    y, m = d.year, d.month
    if d.day >= 26:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return f"{y}-{m:02d}"


def read(path: str) -> list[tuple[str, str, float, str]]:
    """(company month, plate as exported, baht, transaction id) for each fill."""
    from openpyxl import load_workbook
    rows = list(load_workbook(path, read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    head = next(i for i, r in enumerate(rows) if r and r[0] == "DATE")
    col = {str(h): i for i, h in enumerate(rows[head]) if h}
    out, seen = [], set()
    for r in rows[head + 1:]:
        if not r or not r[0] or not r[col["LICENSE_PLATE_NO"]]:
            continue
        tid = str(r[col["TRANSACTION_ID"]])
        if tid in seen:
            continue
        seen.add(tid)
        d = datetime.strptime(str(r[0])[:10], "%d/%m/%Y").date()
        out.append((company_month(d), str(r[col["LICENSE_PLATE_NO"]]), float(str(r[col["AMOUNT"]]).replace(",", "")), tid))
    return out


def plate_key(raw: str, by: dict, vehicles: list[dict]) -> str:
    v = by.get(norm_plate(raw.replace("�", "")))
    if not v:
        groups = re.findall(r"\d+", raw)
        if groups and len(groups[-1]) == 4:
            hits = [x for x in vehicles if re.sub(r"\D", "", x.get("plate_number") or "").endswith(groups[-1])]
            v = hits[0] if len(hits) == 1 else None
    return v["plate_number"] if v else raw.replace("�", "?")


def main() -> None:
    path, first, last = sys.argv[1], sys.argv[2], sys.argv[3]
    from app import supabase
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch", "order": "id.asc"})
    by = {norm_plate(v["plate_number"]): v for v in vehicles if v.get("plate_number")}
    months: dict = {}
    for month, plate, amount, _ in read(path):
        if first <= month <= last:
            k = plate_key(plate, by, vehicles)
            months.setdefault(month, {})[k] = months.setdefault(month, {}).get(k, 0.0) + amount
    entries = []
    for month in sorted(months):
        out = acc_cardfuel.build(month, "pt", months[month], vehicles, "จากไฟล์ PT")
        entries += out["entries"]
        print(f"{month} pt {len(months[month]):>3} trucks {sum(months[month].values()):>12,.2f}")
        for f in out["flags"]:
            print("   !", f)
    if "--write" in sys.argv:
        from app.accounting import _insert_entries, _require
        _require()
        for month in months:
            supabase.delete("acc_entries", {"source": "eq.card_fuel", "source_id": f"like.fuel:pt:{month}:*"})
        _insert_entries(entries)
        print("written", len(entries))


if __name__ == "__main__":
    main()
