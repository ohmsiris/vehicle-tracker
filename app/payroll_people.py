"""
Each person's pay, month by month (owner, 13 Nov 2026): HQ's per-person payroll sheet ("ตารางรายรับ-จ่ายพนง." / KK "สรุป") read row by row
-- the three readers are payroll_people_drivers / _factory / _kk (xlsx and PDF; their sums match HQ's group totals, tested) -- kept in
employee_pay_months and shown on the employee page as a table and a stacked bar per month.

Money on the sheet that is not this month's pay -- savings paid back, back pay -- is moved to `refund` when HQ's own note on the row says so
(คืนสะสม, ลาออก, ค่าแรงขาด): it is shown apart, not as pay. Anything else unusual stays where the reader put it, with its flag, to go through
with the owner. The parts and deductions add up to what HQ printed (รวมรับ / รวมจ่าย / คงเหลือ).
"""
import json
import re
from typing import Optional

from app import payroll_people_drivers as drivers, payroll_people_factory as factory, payroll_people_kk as kk

READERS = {"drivers": drivers, "factory": factory, "kk": kk}
NOT_PAY = re.compile(r"คืนสะ|ลาออก|ค่าแรงขาด")
BASE = "E:/Saraburi/ClaudeCode/CP Accounting/เงินเดือน/"
# month -> group -> the file (xlsx where there is one; the corrected files for August), as found and checked on 13 Nov 2026
FILES = {
    "2026-01": {"drivers": "รายละเอียดเงินเดือน 2569/01/สรุปเงินเดือน พขร. สระบุรี มกราคม 2569 -.pdf",
                "factory": "รายละเอียดเงินเดือน 2569/01/ใบสรุป-ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี มกราคม 2569 .pdf",
                "kk": "เงินเดือนแก่งคอย (แก้ไข01-69.xlsx"},
    "2026-02": {"drivers": "รายละเอียดเงินเดือน 2569/02/สรุป-เงินเดือน พขร. สระบุรี กุมภาพันธ์ 2569 -.pdf",
                "factory": "รายละเอียดเงินเดือน 2569/02/สรุป-เงินเดือน ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี กุมภาพันธ์ 2569 .pdf",
                "kk": "เงินเดือนแก่งคอย กุมภาพันธ์ 2569.xlsx"},
    **{f"2026-{m:02d}": {"drivers": f"รายละเอียดเงินเดือน 2569/{m:02d}/เงินเดือน พขร. สระบุรี {th} 2569 -.xlsx",
                         "factory": f"รายละเอียดเงินเดือน 2569/{m:02d}/เงินเดือน ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี {th} 2569 .xlsx",
                         "kk": f"รายละเอียดเงินเดือน 2569/{m:02d}/เงินเดือนแก่งคอย {th} 2569.xlsx"}
       for m, th in ((3, "มีนาคม"), (4, "เมษายน"), (5, "พฤษภาคม"), (6, "มิถุนายน"), (7, "กรกฎาคม"), (9, "กันยายน"))},
    "2026-08": {"drivers": "รายละเอียดเงินเดือน 2569/08-แก้ไข-จัดส่ง/ใบสรุป พขร. สระบุรี สิงหาคม 2569 -.pdf",
                "factory": "รายละเอียดเงินเดือน 2569/08-แก้ไข-ช่างโรงงาน/สรุป ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี สิงหาคม 2569 .pdf",
                "kk": "เงินเดือนแก่งคอย สิงหาคม 2569.xlsx"},
    "2026-10": {"drivers": "รายละเอียดเงินเดือน 2569/10/สรุปเงินเดือน พขร. สระบุรี ตุลาคม 2569 -.pdf",
                "factory": "รายละเอียดเงินเดือน 2569/10/สรุปเงินเดือน ช่าง+ออฟฟิศ+ฝ่ายผลิตสระบุรี ตุลาคม 2569 .pdf",
                "kk": "รายละเอียดเงินเดือน 2569/10/สรุป-เงินเดือนแก่งคอย ตุลาคม 2569.pdf"},
}
PART_LABELS = {"wage": "ค่าแรง", "commission": "ค่าคอม", "bag": "ค่าซอง", "bonus": "เบี้ยขยัน", "ot": "OT", "other": "อื่นๆ"}
DEDUCTION_LABELS = {"savings": "สะสม", "advance": "เบิกล่วงหน้า", "loan": "เงินยืม / คืนเงินกู้", "common": "ส่วนกลาง", "card": "ทำบัตร / passport",
                    "shortage": "เงินขาด / ความผิด", "other": "อุปกรณ์ / อื่นๆ", "sso": "ปกส."}


def not_pay(row: dict) -> dict:
    """HQ's own note says this money is not the month's pay (savings back, back pay): it moves from the part it sat in to `refund`."""
    if not any(NOT_PAY.search(f) for f in row.get("flags") or []):
        return row
    parts = dict(row["parts"])
    moved = 0.0
    if parts.get("other"):
        moved, parts["other"] = parts["other"], 0.0
    elif parts.get("bag", 0) > drivers.BIG_BAG:
        moved, parts["bag"] = parts["bag"], 0.0
    if not moved:
        return row
    return {**row, "parts": parts, "refund": round((row.get("refund") or 0) + moved, 2), "flags": [*row["flags"], f"ย้าย {moved:,.0f} ไปเป็นคืนสะสม/จ่ายค้าง ตามหมายเหตุของ HQ"]}


def read_month(month: str) -> list[dict]:
    out = []
    for grp, rel in FILES.get(month, {}).items():
        for r in READERS[grp].parse_file(BASE + rel, month):
            out.append({**not_pay(r), "group": grp, "file": rel})
    return out


DETAIL_NUMS = ("rate", "sales", "commission_pct", "loan_before", "loan_paid", "loan_after", "savings_balance")


def details_of(r: dict) -> dict:
    """The row's `details` (HQ's own labels, the loan and savings balances, HQ's notes) in one shape; {} / missing on old rows."""
    d = r.get("details")
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except ValueError:
            d = None
    d = d if isinstance(d, dict) else {}
    num = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    lines = [{"side": x.get("side"), "label": str(x.get("label") or ""), "key": x.get("key"), "amount": float(x.get("amount") or 0)}
             for x in d.get("lines") or [] if isinstance(x, dict) and x.get("side") in ("income", "deduction")]
    notes = [{"label": str(x.get("label") or ""), "text": str(x.get("text") or "")} for x in d.get("notes") or [] if isinstance(x, dict) and x.get("text")]
    out = {"position": (str(d["position"]).strip() or None) if d.get("position") else None, **{k: num(d.get(k)) for k in DETAIL_NUMS},
           "lines": lines, "notes": notes}
    loan = ("loan_before", "loan_paid", "loan_after")
    if not any(out[k] for k in loan):                     # no loan this month: the drivers' reader says None, factory / KK say 0 -- one meaning
        out.update({k: None for k in loan})
    return out


def latest(months: list[dict], key: str) -> tuple[Optional[float], Optional[str]]:
    """The newest month (months are newest first) whose details carry `key`: (value, month)."""
    for m in months:
        if m["details"].get(key) is not None:
            return m["details"][key], m["month"]
    return None, None


def view(rows: list[dict]) -> dict:
    """One person's months, newest first, with what the page needs: the parts (as pay), the refund apart, the totals, and HQ's own
    detail of the row (its column labels, the loan and savings balances, its notes)."""
    months = []
    for r in sorted(rows, key=lambda x: x["month"], reverse=True):
        parts = {k: float((r.get("parts") or {}).get(k) or 0) for k in PART_LABELS}
        pay = round(sum(parts.values()), 2)
        months.append({"month": r["month"], "days": float(r.get("days") or 0), "parts": parts, "pay": pay, "refund": float(r.get("refund") or 0),
                       "deductions": {k: float((r.get("deductions") or {}).get(k) or 0) for k in DEDUCTION_LABELS},
                       "deduct_total": float(r.get("deduct_total") or 0), "net": float(r.get("net") or 0), "dept": r.get("dept"),
                       "source": r.get("source"), "flags": r.get("flags") or [], "details": details_of(r)})
    year = [m for m in months if m["month"].startswith(months[0]["month"][:4])] if months else []
    savings, savings_month = latest(months, "savings_balance")
    loan, loan_month = latest(months, "loan_after")
    tiles = {"avg_net": round(sum(m["net"] - m["refund"] for m in year) / len(year), 2) if year else 0,
             "days": round(sum(m["days"] for m in year), 1), "pay": round(sum(m["pay"] for m in year), 2), "months": len(year),
             "year": int(months[0]["month"][:4]) + 543 if months else None,
             "savings_balance": savings, "savings_month": savings_month, "loan_after": loan, "loan_month": loan_month,
             "deductions": {k: round(sum(m["deductions"][k] for m in year), 2) for k in DEDUCTION_LABELS},
             "deduct_total": round(sum(m["deduct_total"] for m in year), 2)}
    return {"months": months, "tiles": tiles, "part_labels": PART_LABELS, "deduction_labels": DEDUCTION_LABELS}


def for_employee(employee_id: str) -> dict:
    from app import supabase
    rows = supabase.select_all("employee_pay_months", {"select": "*", "employee_id": f"eq.{employee_id}", "order": "month.asc"})
    return view(rows)
