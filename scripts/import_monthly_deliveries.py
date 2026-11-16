"""The monthly customers' ใบสรุปวางบิล workbooks (one tab per month, one row per delivery) -> tax_invoice_deliveries (owner, 16 Nov 2026).

    PYTHONPATH=. .venv/Scripts/python -X utf8 scripts/import_monthly_deliveries.py <code> <file.xlsx> [--write]

code = 10 (โรงหมู) or 06 (หนองโดน). Each tab "1-31 ต.ค.2569" is a month; its deliveries are put on that month's tax invoice when the app
has one (from the summary sheet), and the sums are compared with it. --write replaces this customer's imported deliveries."""
import re
import sys

from app import supabase
from app.tax_invoices import money

FROM = "2025-08"
SHORT = {"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}


def tab_month(title: str):
    t = re.sub(r"\s+", "", title).replace("..", ".")
    y = re.search(r"25(\d\d)", t)
    for k, v in SHORT.items():
        if k.replace(".", "") in t.replace(".", "") and y:
            return f"{2500 + int(y.group(1)) - 543}-{v:02d}"
    return None


def main():
    code, path = sys.argv[1], sys.argv[2]
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    invoices = {r["month"]: r for r in supabase.select_all("tax_invoices", {"select": "id,doc_no,month,amount,kg", "customer_code": f"eq.{code}",
                                                                            "kind": "eq.invoice", "order": "id.asc"})}
    rows, report = [], []
    for ws in wb.worksheets:
        month = tab_month(ws.title)
        if not month:
            print("   skipped tab", repr(ws.title))
            continue
        got = []
        for r in range(5, ws.max_row + 1):
            b = ws.cell(r, 2).value
            kg, price = ws.cell(r, 6).value, ws.cell(r, 8).value
            if not hasattr(b, "year") or not isinstance(kg, (int, float)) or not isinstance(price, (int, float)):
                continue
            y = b.year - 543 if b.year > 2400 else b.year
            bags = ws.cell(r, 5).value
            got.append({"customer_code": code, "day": f"{y}-{b.month:02d}-{b.day:02d}", "note_no": (str(ws.cell(r, 3).value or "").strip() or None),
                        "item": (str(ws.cell(r, 4).value or "").strip() or None), "bags": bags if isinstance(bags, (int, float)) else None,
                        "kg": kg, "price": price, "amount": money(kg * price), "updated_by": "นำเข้าจากไฟล์ใบสรุปวางบิล"})
        if month < FROM:                                         # the summary sheet (and so the app's invoices) starts Aug 2568
            continue
        inv = invoices.get(month)
        amt = sum(g["amount"] for g in got)
        note = f"{month}: {len(got)} deliveries {amt:,.2f}"
        h2 = re.search(r"(SRI|IV)\s*-\s*\d{4}\s*-\s*\d+", str(ws["H2"].value or ""))
        if not inv and h2 and "--write" in sys.argv:              # the month is missing from the summary sheet: its ใบวางบิล number is on the tab
            from app.tax_invoices import BRANCH, compute, parse_number, _month_end
            last = _month_end(month)
            prices = {g["price"] for g in got}
            inv = supabase.insert("tax_invoices", [{"branch": BRANCH, "kind": "invoice", "doc_no": parse_number(h2.group(0))["doc_no"],
                                                    "doc_date": last.isoformat(), "month": month, "customer_code": code,
                                                    "customer_name": supabase.select("tax_invoice_customers", {"select": "name", "code": f"eq.{code}"})[0]["name"],
                                                    "kg": sum(g["kg"] for g in got), "price": prices.pop() if len(prices) == 1 else None,
                                                    **compute(None, None, amt), "note": "ไม่มีในชีทสรุป — เพิ่มจากไฟล์ใบสรุปวางบิล",
                                                    "source": "monthly_file", "updated_by": "นำเข้าจากไฟล์ใบสรุปวางบิล"}])[0]
            note += f"  + invoice {inv['doc_no']} added (not in the summary sheet)"
        elif not inv and h2:
            note += f"  (would add invoice {h2.group(0)})"
        if inv and abs(float(inv["amount"]) - amt) > 0.5 and "added" not in note:
            report.append(note + f"  <> invoice {inv['doc_no']} {float(inv['amount']):,.2f} -- NOT imported")
            continue
        if not inv:
            report.append(note + "  (no invoice for this month) -- NOT imported")
            continue
        if inv:
            for g in got:
                g["invoice_id"] = inv["id"]
            if abs(float(inv["amount"]) - amt) > 0.5:
                note += f"  <> invoice {inv['doc_no']} {float(inv['amount']):,.2f}"
            else:
                note += f"  = invoice {inv['doc_no']}"
        else:
            note += "  (no invoice in the app for this month)"
        report.append(note)
        rows += got
    print("\n".join(sorted(report)))
    print(len(rows), "deliveries")
    if "--write" in sys.argv:
        supabase.delete("tax_invoice_deliveries", {"customer_code": f"eq.{code}", "updated_by": "eq.นำเข้าจากไฟล์ใบสรุปวางบิล"})
        for i in range(0, len(rows), 300):
            supabase.insert("tax_invoice_deliveries", rows[i:i + 300])
        print("written")


if __name__ == "__main__":
    main()
