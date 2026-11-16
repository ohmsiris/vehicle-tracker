"""The bookkeeper's monthly CP bill workbooks ("43.ใบวางบิล CPF สิงหาคม 2569.xls": one tab per bill) -> tax_invoices (owner, 16 Nov 2026).

    PYTHONPATH=. .venv/Scripts/python -X utf8 scripts/import_cp_bills.py <file.xls> [<file.xls> ...] [--write]

Each bill gives its number, date, item, kg and price. A number already in the app (from the summary sheet) gets its date and item, and any
difference in kg / amount is printed (the bill file wins: it is what was printed and sent); a new number is added. The leftover template
tab (an old 2568 bill at 0.36 with no weight) is skipped."""
import re
import sys

from app import supabase
from app.tax_invoices import BRANCH, compute, parse_number


def read(path: str) -> list[dict]:
    import xlrd
    out = []
    for s in xlrd.open_workbook(path).sheets():
        g = lambda r, c: s.cell_value(r, c) if r < s.nrows and c < s.ncols else ""  # noqa: E731
        p = parse_number(str(g(5, 4)))
        m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})", re.sub(r"(?<=\d)\s+(?=\d)", "", str(g(6, 4))))
        kg, price = g(9, 2), g(9, 4)
        if not p or not m or not isinstance(kg, float) or not kg or not isinstance(price, float):
            print("   skipped:", s.name, repr(g(5, 4)), repr(g(6, 4)), kg, price)
            continue
        day = f"{int(m.group(3)) - 543}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
        out.append({"doc_no": p["doc_no"], "code": p["code"], "doc_date": day, "item": str(g(9, 1)).strip(), "kg": kg, "price": price,
                    "printed_total": g(14, 5)})
    return out


def main():
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    bills = [b for f in files for b in read(f)]
    have = {}
    for r in supabase.select_all("tax_invoices", {"select": "id,doc_no,kg,amount,total,month,doc_date", "branch": f"eq.{BRANCH}", "order": "id.asc"}):
        have.setdefault(r["doc_no"], []).append(r)
    names = {c["code"]: c["name"] for c in supabase.select_all("tax_invoice_customers", {"select": "code,name", "order": "code.asc"})}
    updates, inserts, diffs = [], [], []
    for b in bills:
        sums = compute(b["kg"], b["price"])
        if abs(sums["total"] - round(b["printed_total"], 2)) > 0.011:
            diffs.append(f"{b['doc_no']}: the bill prints {b['printed_total']:,.2f}, kg x price gives {sums['total']:,.2f}")
        rows = have.get(b["doc_no"]) or []
        if len(rows) > 1:
            diffs.append(f"{b['doc_no']}: {len(rows)} rows in the app already -- left alone")
            continue
        fields = {"doc_date": b["doc_date"], "month": b["doc_date"][:7], "item": b["item"], "kg": b["kg"], "price": b["price"], **sums}
        if rows:
            r = rows[0]
            if abs(float(r["kg"] or 0) - b["kg"]) > 0.001 or abs(float(r["total"]) - sums["total"]) > 0.011:
                diffs.append(f"{b['doc_no']}: summary sheet {float(r['kg'] or 0):,.0f} kg {float(r['total']):,.2f} -> bill {b['kg']:,.0f} kg {sums['total']:,.2f}")
            if r["month"] != fields["month"]:
                diffs.append(f"{b['doc_no']}: summary sheet put it in {r['month']}, the bill is dated {b['doc_date']}")
            updates.append((r["id"], fields))
        else:
            inserts.append({"branch": BRANCH, "kind": "invoice", "doc_no": b["doc_no"], "customer_code": b["code"], "customer_name": names.get(b["code"]),
                            "note": None, "source": "cp_bill_file", "updated_by": "นำเข้าจากไฟล์บิล CPF", **fields})
    print(f"{len(bills)} bills: {len(updates)} already in the app (date added), {len(inserts)} new")
    for d in diffs:
        print("  ", d)
    if "--write" in sys.argv:
        for rid, f in updates:
            supabase.update("tax_invoices", {"id": f"eq.{rid}"}, f)
        for i in range(0, len(inserts), 300):
            supabase.insert("tax_invoices", inserts[i:i + 300])
        print("written")


if __name__ == "__main__":
    main()
