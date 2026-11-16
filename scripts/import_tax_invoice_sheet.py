"""Load the Google Sheet "สรุปใบกำกับภาษี แก่งคอย" (downloaded as .xlsx) into tax_invoices (owner, 16 Nov 2026).

    PYTHONPATH=. .venv/Scripts/python -X utf8 scripts/import_tax_invoice_sheet.py <file.xlsx> [--write]

Every tab named after a month is one calendar month. The customers (by the code in the invoice number) are set up first, and each
customer's price per kg is taken from the sheet: a new price row from the first month it changed. Without --write it only prints.
--write replaces the rows that came from the sheet (source 'sheet'); rows typed in the app are kept."""
import sys
from collections import defaultdict

from app import supabase
from app.acc_cp_invoice import sheet_month
from app.tax_invoices import BRANCH, PRIMARY, money, parse_number

CUSTOMERS = [  # code, name, mode, unit -- the sheet's formula, plus นครหลวงคอนกรีต (code 18, typed by hand in the sheet)
    (PRIMARY, "CP ซิลเลอร์", "kg", "กก."), ("07", "CPF ศูนย์จำหน่าย", "kg", "กก."), ("03", "CPF โกลบอล", "kg", "กก."),
    ("06", "CPF หนองโดน", "kg", "กก."), ("10", "CPF โรงหมู", "kg", "กก."), ("87", "อปร.2-3", "kg", "กก."),
    ("12", "CPF ศูนย์วิจัยพันธุ์ปลา (บางปะอิน)", "kg", "กก."), ("13", "CPF อ่างทอง (บางปะอิน)", "kg", "กก."),
    ("05", "ซิลเวอร์ฟาร์ม", "kg", "กก."), ("02", "ซันฟู้ด", "total", "กก."), ("18", "นครหลวงคอนกรีต", "kg", "หน่วย"),
]
NO = ("ไม่พบ",)


def num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def read(path: str) -> list[dict]:
    import openpyxl
    names = {c: n for c, n, _, _ in CUSTOMERS}
    out = []
    for ws in openpyxl.load_workbook(path, data_only=True).worksheets:
        month = sheet_month(ws.title)
        if not month:
            continue
        for r in ws.iter_rows(min_row=3, values_only=True):
            if len(r) < 8 or r[1] is None:
                continue
            p = parse_number(str(r[1]))
            if not p:
                print("   skipped (number not understood):", ws.title, r[:8])
                continue
            amount, vat, total = num(r[5]), num(r[6]), num(r[7])
            broken = None
            if amount is None and num(r[3]) is not None and num(r[4]) is not None:      # a broken formula in the sheet (#REF!): work it out
                amount, vat, total = num(r[3]) * num(r[4]), None, None
                broken = f"ในชีทสูตรเสีย ({r[5]}) — คำนวณใหม่จากน้ำหนัก x ราคา"
            if amount is None:
                print("   skipped (no amount):", ws.title, r[:8])
                continue
            sheet_name = str(r[2] or "").strip()
            code = p["code"]
            if p["kind"] == "credit_note":
                code = "07" if "ศูนย์จำหน่าย" in sheet_name or "ศูนยจำหน่าย" in sheet_name else None
            price_text = r[4] if isinstance(r[4], str) and not r[4].startswith(NO) else None
            out.append({"branch": BRANCH, "kind": p["kind"], "doc_no": p["doc_no"], "doc_date": None, "month": month,
                        "customer_code": code, "customer_name": names.get(code) or (None if sheet_name.startswith(NO) else sheet_name) or None,
                        "kg": num(r[3]), "price": num(r[4]), "amount": money(amount), "vat": money(vat if vat is not None else amount * 0.07),
                        "total": money(total if total is not None else amount * 1.07), "note": broken or price_text, "source": "sheet", "updated_by": "นำเข้าจาก Google Sheet"})
    return out


def prices(rows: list[dict]) -> list[dict]:
    """Each customer's price per kg from the sheet: a row whenever it changes, from the first of that month."""
    by = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for r in rows:
        if r["kind"] == "invoice" and r["price"] and r["customer_code"]:
            by[r["customer_code"]][r["month"]][r["price"]] += 1
    out = []
    for code, months in by.items():
        last = None
        for m in sorted(months):
            p = max(months[m].items(), key=lambda kv: kv[1])[0]          # the month's usual price
            if p != last:
                out.append({"code": code, "price": p, "from_date": f"{m}-01", "note": "จาก Google Sheet"})
                last = p
    return out


def main():
    path = sys.argv[1]
    rows = read(path)
    pr = prices(rows)
    per = defaultdict(lambda: [0, 0.0])
    for r in rows:
        per[r["month"]][0] += 1
        per[r["month"]][1] += (-1 if r["kind"] == "credit_note" else 1) * r["total"]
    for m in sorted(per):
        print(f"{m}: {per[m][0]} rows, total {per[m][1]:,.2f}")
    for p in pr:
        print("price", p)
    print(len(rows), "rows")
    if "--write" in sys.argv:
        supabase.insert("tax_invoice_customers", [{"code": c, "name": n, "mode": md, "unit": u, "sort": i * 10, "active": True}
                                                  for i, (c, n, md, u) in enumerate(CUSTOMERS)], on_conflict="code")
        supabase.delete("tax_invoice_prices", {"note": "eq.จาก Google Sheet"})
        supabase.insert("tax_invoice_prices", pr)
        supabase.delete("tax_invoices", {"source": "eq.sheet", "branch": f"eq.{BRANCH}"})
        for i in range(0, len(rows), 300):
            supabase.insert("tax_invoices", rows[i:i + 300])
        print("written")


if __name__ == "__main__":
    main()
