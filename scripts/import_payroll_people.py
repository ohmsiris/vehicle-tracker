"""Load each person's monthly pay from HQ's payroll files into employee_pay_months (owner, 13 Nov 2026).

    PYTHONPATH=. .venv/Scripts/python -X utf8 scripts/import_payroll_people.py [2026-10 ...] [--write]

Without --write it only prints, per month and sheet, the people found and the totals (to check against HQ's own total row). With --write each month
is replaced. People are matched to the employee list by their code (SB-…); a code with no employee is kept, unlinked, and listed."""
import sys

from app import payroll_people as pp, supabase


def main():
    write = "--write" in sys.argv
    months = [a for a in sys.argv[1:] if not a.startswith("--")] or sorted(pp.FILES)
    emp = {e["code"].upper(): e["id"] for e in supabase.select_all("employees", {"select": "id,code", "order": "code.asc"}) if e.get("code")}
    alias = {"SB-KK073": "SB-KK073S"}                       # the same person, written two ways
    for month in months:
        rows = pp.read_month(month)
        by = {}
        for r in rows:
            by.setdefault(r["group"], []).append(r)
        unmatched = sorted({r["code"] for r in rows if (alias.get(r["code"], r["code"])) not in emp})
        for g, rs in by.items():
            print(f"{month} {g:8} {len(rs):3} คน  รวมรับ {sum(r['gross'] for r in rs):>12,.2f}  รวมจ่าย {sum(r['deduct_total'] for r in rs):>11,.2f}"
                  f"  คงเหลือ {sum(r['net'] for r in rs):>12,.2f}  refund {sum(r['refund'] for r in rs):>9,.0f}")
        if unmatched:
            print(f"   ไม่พบรหัสในรายชื่อพนักงาน: {', '.join(unmatched)}")
        if write:
            supabase.delete("employee_pay_months", {"month": f"eq.{month}"})
            payload = [{"month": month, "grp": r["group"], "code": r["code"], "employee_id": emp.get(alias.get(r["code"], r["code"])), "name": r["name"],
                        "dept": r.get("dept"), "days": r["days"], "parts": r["parts"], "refund": r["refund"], "gross": r["gross"],
                        "deductions": r["deductions"], "deduct_total": r["deduct_total"], "net": r["net"], "source": r["source"], "file": r["file"],
                        "flags": r["flags"]} for r in rows]
            for i in range(0, len(payload), 200):
                supabase.insert("employee_pay_months", payload[i:i + 200])
    print("written" if write else "(dry run -- add --write to save)")


main()
