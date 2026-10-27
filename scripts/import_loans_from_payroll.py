"""
Opening balances of the loans, read from the monthly pay workbooks (the เงินค้าง columns), into employee_loans (schema_v23.sql).

    .venv/Scripts/python.exe scripts/import_loans_from_payroll.py "<drivers .xlsx>" "<staff .xlsx>" ... [--as-of 2026-09-30] [--apply]

Each workbook's sheet has, per employee, three columns (found by their headings, so the two layouts need no special care):
    ค้างเงิน   what was owed when the month began      จ่ายค้าง   what the payroll deducted this month      ค้างย/ป   what is still owed after it
A person with something still owed after the month gets ONE loan for that balance: dated the month end (--as-of), repayment plan from what was deducted that
month (the same amount every month from the next month; "custom" for the accident memo, 2,000 then 1,500), or no schedule when nothing was deducted (to be set
by hand). The start-of-month figure and this month's deduction are kept in the loan's note. A person who already has a loan from this import is skipped, so it
can be run again. Needs the employee (by code, SB-FR... / SB-PD... / SB-KK...) to be in พนักงาน and still working.
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import employees, loans, supabase                    # noqa: E402

MARK = "ยอดค้างยกมา"
# codes whose balance has a known plan / reason (from the บันทึกข้อความ on file)
SPECIAL = {"SB-PD033": {"kind": "accident", "reason": "ค่าใช้จ่ายจากอุบัติเหตุรถยนต์ 1 ก.ย. 2569 (ค่ายกรถ)", "memo_ref": "บันทึกข้อความ 2 ก.ย. 2569", "amounts": [2000, 1500]}}


def num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def read_balances(path: str) -> list[dict]:
    import openpyxl
    book = openpyxl.load_workbook(path, data_only=True)
    out = []
    for ws in book.worksheets:
        cols = {}
        for r in (2, 3):
            for c in ws[r]:
                if c.value in ("ค้างเงิน", "จ่ายค้าง", "ค้างย/ป"):
                    cols[c.value] = c.column - 1
        if len(cols) < 3 or ws.max_row < 5:
            continue
        code_col = next((i for i, c in enumerate(ws[5]) if str(c.value or "").startswith("SB-")), None)
        if code_col is None:
            continue
        for r in range(5, ws.max_row + 1):
            v = [c.value for c in ws[r]]
            if len(v) <= max(cols.values()) or not str(v[code_col] or "").startswith("SB-"):
                continue
            before, paid, after = num(v[cols["ค้างเงิน"]]), num(v[cols["จ่ายค้าง"]]), num(v[cols["ค้างย/ป"]])
            if after > 0.004:
                out.append({"code": str(v[code_col]).strip().upper(), "name": str(v[code_col + 1] or "").strip(), "before": before, "paid": paid, "after": round(after, 2), "file": Path(path).name})
    return out


def plan_for(b: dict, emp: dict, as_of: str) -> dict:
    sp = SPECIAL.get(b["code"], {})
    first = loans.add_month(as_of[:7], 1)
    data = {"employee_id": emp["id"], "kind": sp.get("kind", "loan"), "amount": b["after"], "loan_date": as_of, "first_month": first,
            "reason": sp.get("reason") or f"{MARK} ตามใบเงินเดือน {as_of[:7]}", "memo_ref": sp.get("memo_ref"), "approved_by": None,
            "notes": f"{MARK}: ต้นเดือนค้าง {b['before']:,.0f} · หักเดือนนี้ {b['paid']:,.0f} · คงเหลือ {b['after']:,.0f} (จาก {b['file']})"}
    if sp.get("amounts"):
        data.update(repay_mode="custom", amounts=sp["amounts"])
    elif b["paid"] > 0:
        data.update(repay_mode="monthly_fixed", repay_amount=min(b["paid"], b["after"]))
    else:
        data.update(repay_mode="flexible")
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--as-of", default="2026-09-30")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    emps = {e["code"].upper(): e for e in supabase.select_all("employees", {"select": "*", "order": "code.asc"})}
    have = {(l["employee_id"]) for l in supabase.select_all("employee_loans", {"select": "employee_id,reason", "order": "id.asc"}) if MARK in (l.get("reason") or "")}
    todo, problems = [], []
    for f in args.files:
        for b in read_balances(f):
            e = emps.get(b["code"])
            if not e:
                problems.append(f"{b['code']} {b['name']}: not in พนักงาน")
            elif e["status"] == "resigned":
                problems.append(f"{b['code']} {b['name']}: resigned ({b['after']:,.0f} still owed)")
            elif e["id"] in have:
                continue
            else:
                todo.append((b, e, plan_for(b, e, args.as_of)))
    total = 0.0
    for b, e, d in todo:
        total += d["amount"]
        how = (f"custom {d['amounts']}" if d["repay_mode"] == "custom" else f"{d['repay_amount']:,.0f}/month" if d["repay_mode"] == "monthly_fixed" else "NO SCHEDULE (nothing was deducted)")
        print(f"{b['code']:9} {employees.display_name(e.get('prefix'), e['full_name'])[:30]:32} owed {d['amount']:>9,.0f}  was {b['before']:>8,.0f}  paid {b['paid']:>6,.0f}  -> {d['kind']:8} {how}")
    for p in problems:
        print("PROBLEM:", p)
    print(f"\nloans to create: {len(todo)} · total owed {total:,.0f} · problems: {len(problems)}")
    if not args.apply:
        print("dry run -- nothing written (add --apply)")
        return
    for b, e, d in todo:
        loans.create_loan(d)
    print(f"written: {len(todo)}")


if __name__ == "__main__":
    main()
