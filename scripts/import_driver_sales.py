"""Saraburi driver sales -> driver_sales_monthly + the books (revenue per route, on the truck).

    python scripts/import_driver_sales.py "<Drivers.xlsx>" 2026 ["<payroll summary PDF>" YYYY-MM ...] [--write]

The sheet's "Driver Sales 2026" gives every month it has; a month it does not have yet is read from that month's payroll summary PDF
(สรุปเงินเดือน พขร.). Fuel per truck: card fuel already in the books (PT + Bangchak, VAT included) + cash fuel from Fuel2 where the
sheet has it. Gross sales per app/driver_sales.gross. Books: Dr 1200 / Cr 4100 per driver per month, dated the 25th, the truck on the
lines; re-running replaces the months it reads.
"""
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import driver_sales as ds  # noqa: E402

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def cash_fuel(wb, year: int) -> dict:
    """{(month, code): cash} from Fuel2's driver tables."""
    ws = wb["Fuel2"]
    starts = sorted((r, ws.cell(r, 7).value.strip()) for r in range(1, ws.max_row + 1)
                    if isinstance(ws.cell(r, 7).value, str) and ws.cell(r, 7).value.strip() in MONTHS)
    out = {}
    for i, (s, name) in enumerate(starts):
        e = starts[i + 1][0] if i + 1 < len(starts) else ws.max_row + 1
        month = f"{year}-{MONTHS.index(name) + 1:02d}"
        for r in range(s, e):
            code, cash = ws.cell(r, 2).value, ws.cell(r, 9).value
            if isinstance(code, str) and code.startswith("SB-"):
                out[(month, code)] = out.get((month, code), 0.0) + (float(cash) if isinstance(cash, (int, float)) else 0.0)
    return out


def main() -> None:
    from openpyxl import load_workbook
    path, year = sys.argv[1], int(sys.argv[2])
    wb = load_workbook(path, data_only=True)
    rows = ds.parse(list(wb["Driver Sales 2026"].iter_rows(values_only=True)), year)
    rest = [a for a in sys.argv[3:] if a != "--write"]
    latest_truck = {}
    for r in sorted(rows, key=lambda x: x["month"]):
        if r["truck"]:
            latest_truck[r["code"]] = r["truck"]
    for pdf, month in zip(rest[::2], rest[1::2]):
        import pymupdf
        extra = ds.parse_payroll_text("\n".join(p.get_text() for p in pymupdf.open(pdf)), month)
        for r in extra:
            r["truck"] = latest_truck.get(r["code"])
        rows = [r for r in rows if r["month"] != month] + extra
    cash = cash_fuel(wb, year)

    from app import supabase
    vehicles = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch,main_driver_id", "order": "id.asc"})
    by_no = {str(v["vehicle_number"]): v for v in vehicles if v.get("vehicle_number") is not None}
    # the sheet's truck cell is sometimes not a truck ("หน้าโรง"): then the truck whose main driver he is in the app
    norm = lambda n: re.sub(r"\s+", "", n or "")
    own = {}
    for d in supabase.select_all("drivers", {"select": "id,name", "order": "id.asc"}):
        mine = [v for v in vehicles if v.get("main_driver_id") == d["id"]]
        if len(mine) == 1:
            own[norm(d["name"])] = mine[0]
    card, stations = {}, {}
    for e in supabase.select_all("acc_entries", {"select": "source_id,acc_lines(account,credit,vehicle_id)", "source": "eq.card_fuel", "order": "id.asc"}):
        _, station, month, _ = e["source_id"].split(":", 3)
        stations.setdefault(month, set()).add(station)
        for ln in e["acc_lines"]:
            if ln["account"] == "2100" and ln.get("vehicle_id"):
                card[(month, ln["vehicle_id"])] = card.get((month, ln["vehicle_id"]), 0.0) + float(ln["credit"])
    # a truck driven by two drivers in a month: its fuel goes with the driver who wrote the sales the fuel was taken from -- split by sales
    share: dict = {}
    for r in rows:
        v = (by_no.get(str(r["truck"])) if r["truck"] else None) or own.get(norm(r["name"]))
        r["vehicle"] = v
        if v and str(r["truck"]) != str(v["vehicle_number"]):
            r["truck"] = str(v["vehicle_number"])
        if v:
            share.setdefault((r["month"], v["id"]), []).append(r)
    out_rows, entries = [], []
    for r in rows:
        v = r["vehicle"]
        fc = 0.0
        if v:
            group = share[(r["month"], v["id"])]
            tot = sum(x["sales"] for x in group) or len(group)
            fc = round(card.get((r["month"], v["id"]), 0.0) * ((r["sales"] or 1) / tot if len(group) > 1 else 1), 2)
        cf = cash.get((r["month"], r["code"]))
        g = ds.gross(r, fc + (cf or 0.0))
        complete = stations.get(r["month"], set()) >= {"pt", "bangchak"}
        out_rows.append({"month": r["month"], "code": r["code"], "name": r["name"], "nick": r["nick"], "truck_no": r["truck"],
                         "vehicle_id": v["id"] if v else None, "days": r["days"], "wage": r["wage"], "rate": r["rate"], "sales_written": r["sales"],
                         "commission": r["commission"], "bag_pay": r["bag_pay"], "total_pay": r["total_pay"], "bags": g["bags"], "fuel_card": fc,
                         "fuel_cash": cf, "gross_sales": g["gross"], "fuel_complete": complete, "source": "sheet" if r["month"] not in rest[1::2] else "payroll_pdf"})
        amt = g["gross"] or 0.0
        if amt > 0:
            y, m = (int(x) for x in r["month"].split("-"))
            lab = f"#{r['truck']} " if r["truck"] else ""
            note = "" if complete or not g["fuel_added"] and r["code"] not in ds.NET_OF_FUEL else " · น้ำมันเดือนนี้ไม่ครบ ยอดเต็มอาจต่ำไป"
            ln = lambda a, d, c: {"account": a, "debit": d, "credit": c, "branch": "สระบุรี", "vehicle_id": v["id"] if v else None, "machine_id": None, "memo": None}
            entries.append({"entry_date": date(y, m, 25).isoformat(), "branch": "สระบุรี", "source": "driver_sales", "source_id": f"dsales:{r['month']}:{r['code']}",
                            "party": f"สายรถ {lab}{r['name']}"[:100], "doc_type": "receipt", "doc_no": None,
                            "description": f"ยอดขายสายรถ {lab}{r['name']} {m:02d}/{y + 543} · ยอดเขียน {r['sales']:,.0f}" + (f" + ซอง {g['bags']:,.0f} ถุง" if g["bags"] else "")
                                           + (f" + น้ำมัน {g['fuel_added']:,.0f}" if g["fuel_added"] else "") + note,
                            "lines": [ln("1200", round(amt, 2), 0.0), ln("4100", 0.0, round(amt, 2))]})
    tot: dict = {}
    for r in out_rows:
        t = tot.setdefault(r["month"], [0, 0, 0])
        t[0] += r["sales_written"]; t[1] += r["gross_sales"] or 0; t[2] += 1
    for k in sorted(tot):
        print(f"{k}  written {tot[k][0]:>12,.0f}  gross {tot[k][1]:>12,.0f}  drivers {tot[k][2]}")
    print("no truck:", sorted({(r["month"], r["code"], r["name"]) for r in rows if not r["vehicle"] and r["sales"]})[:20])
    if "--write" in sys.argv:
        from app.accounting import _insert_entries, _require
        _require()
        months = sorted(tot)
        for m in months:
            supabase.delete("driver_sales_monthly", {"month": f"eq.{m}"})
            supabase.delete("acc_entries", {"source": "eq.driver_sales", "source_id": f"like.dsales:{m}:*"})
        supabase.insert("driver_sales_monthly", out_rows)
        _insert_entries(entries)
        print("written", len(out_rows), "rows,", len(entries), "entries")


if __name__ == "__main__":
    main()
