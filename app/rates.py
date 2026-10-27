"""
ค่าแรง -- each employee's daily wage with its history, and the probation (ทดลองงาน) period (employee_rates, employees.probation_end, schema_v25.sql).

Everyone is paid per day (every role, every day of the week the same). A raise -- or the end of probation -- is a new rate with the date it starts; the
old rate stays as history, and the rate for any day is the latest one starting on or before it (the pay calculation will use rate_on()). Probation is
usually 3 months from the start date, sometimes ended earlier: `probation_end` is simply the last day of the trial and is editable.
Overtime: Kaeng Khoi's ผลิต get 3 hours every shift at OT_HOURLY a baht an hour (the September sheet uses 40).

Pure rules (clean_rate, rate_on, default_probation_end, probation_state) are unit-tested; the database calls are thin. Same HR_PIN lock as พนักงาน.
"""
import calendar
import time
import uuid
from datetime import date, timedelta
from typing import Optional

from app import employees, supabase

RATE_KINDS = {"normal": "ปกติ", "probation": "ทดลองงาน"}
OT_HOURLY = 40
PROBATION_MONTHS = 3
SOON_DAYS = 30                       # "probation ends soon"
MAX_RATE = 5000
NEEDS_V25 = "ยังใช้ค่าแรงไม่ได้ — ต้องรันไฟล์ schema_v25.sql ใน Supabase ก่อน"

InvalidRate = type("InvalidRate", (employees.InvalidEmployee,), {"__doc__": "User-fixable problem; str() is a Thai message safe to show."})
employees.REFERENCED_BY.append(("employee_rates", "employee_id"))         # an employee with a rate history can only be ลาออก, never deleted


# ------------------------------------------------------------------ pure rules
def add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    y, m = idx // 12, idx % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def default_probation_end(start: Optional[date]) -> Optional[date]:
    """3 months from the start date: started 15 Oct -> the trial runs to 14 Jan."""
    return add_months(start, PROBATION_MONTHS) - timedelta(days=1) if start else None


def probation_state(probation_end, today: date) -> dict:
    """{on, days_left, soon, overdue}: on probation while today <= the last day; `soon` = ending within SOON_DAYS."""
    if not probation_end:
        return {"on": False, "days_left": None, "soon": False}
    end = date.fromisoformat(str(probation_end)[:10])
    left = (end - today).days
    return {"on": left >= 0, "days_left": left, "soon": 0 <= left <= SOON_DAYS, "past": left < 0}


def _money(value) -> float:
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise InvalidRate("ค่าแรงต่อวันไม่ถูกต้อง")
    if not 0 < n <= MAX_RATE:
        raise InvalidRate(f"ค่าแรงต่อวันต้องมากกว่า 0 และไม่เกิน {MAX_RATE:,}")
    return round(n, 2)


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def clean_rate(data: dict, *, today: date) -> dict:
    """A rate from the form: {daily_rate, effective_from, kind, reason, memo_ref, review, review_note}. Raises InvalidRate in Thai."""
    try:
        eff = date.fromisoformat(str(data.get("effective_from") or today.isoformat())[:10])
    except ValueError:
        raise InvalidRate("วันที่เริ่มใช้ไม่ถูกต้อง")
    if eff.year < 2000 or eff.year >= 2400:
        raise InvalidRate("วันที่เริ่มใช้ไม่ถูกต้อง (ปีต้องเป็น ค.ศ. เช่น 2026)")
    if eff > today + timedelta(days=90):
        raise InvalidRate("วันที่เริ่มใช้ไกลเกินไป (ไม่เกิน 3 เดือนข้างหน้า)")
    kind = _text(data.get("kind") or "normal", 12)
    if kind not in RATE_KINDS:
        raise InvalidRate("ประเภทค่าแรงไม่ถูกต้อง")
    review = bool(data.get("review"))
    return {"daily_rate": _money(data.get("daily_rate")), "effective_from": eff.isoformat(), "kind": kind, "reason": _text(data.get("reason"), 300) or None,
            "memo_ref": _text(data.get("memo_ref"), 100) or None, "review": review, "review_note": (_text(data.get("review_note"), 300) or None) if review else None}


def rate_on(rates: list[dict], day: date) -> Optional[dict]:
    """The rate in force on `day`: the latest one that started on or before it (None before the first)."""
    best = None
    for r in rates:
        start = date.fromisoformat(str(r["effective_from"])[:10])
        if start <= day and (best is None or start > best[0]):
            best = (start, r)
    return best[1] if best else None


def card(r: dict) -> dict:
    return {"id": r["id"], "daily_rate": float(r["daily_rate"]), "effective_from": str(r["effective_from"])[:10], "kind": r["kind"], "kind_label": RATE_KINDS.get(r["kind"], ""),
            "reason": r.get("reason") or "", "memo_ref": r.get("memo_ref") or "", "review": bool(r.get("review")), "review_note": r.get("review_note") or ""}


# ------------------------------------------------------------------ the database
_probe = {"at": 0.0, "ok": False}


def has_v25() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("employee_rates", {"select": "id", "limit": "1"})
        supabase.select("employees", {"select": "pay_group,probation_end", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not (employees.has_v21() and has_v25()):
        raise InvalidRate(NEEDS_V25)


def all_rates() -> dict[str, list[dict]]:
    out: dict = {}
    for r in supabase.select_all("employee_rates", {"select": "*", "order": "effective_from.desc,id.asc"}):
        out.setdefault(r["employee_id"], []).append(r)
    return out


def current_summary(rates_by_emp: dict[str, list[dict]], today: date) -> dict[str, dict]:
    """employee id -> the rate in force today (for the list)."""
    out = {}
    for eid, rows in rates_by_emp.items():
        r = rate_on(rows, today)
        if r:
            out[eid] = {"daily_rate": float(r["daily_rate"]), "rate_since": str(r["effective_from"])[:10], "rate_kind": r["kind"], "rate_review": bool(r.get("review"))}
    return out


def list_rates(employee_id: str) -> dict:
    _require()
    emp = employees._get(employee_id)
    rows = sorted(supabase.select_all("employee_rates", {"select": "*", "employee_id": f"eq.{emp['id']}", "order": "effective_from.desc,id.asc"}),
                  key=lambda r: str(r["effective_from"]), reverse=True)
    today = employees.today_th()
    cur = rate_on(rows, today)
    return {"employee_id": emp["id"], "rates": [card(r) for r in rows], "current": card(cur) if cur else None, "probation_end": emp.get("probation_end"),
            "probation": probation_state(emp.get("probation_end"), today), "default_probation_end": (default_probation_end(date.fromisoformat(str(emp["start_date"])[:10])).isoformat()
                                                                                                   if emp.get("start_date") else None),
            "pay_group": emp.get("pay_group") or "company", "ot_hourly": OT_HOURLY}


def add_rate(employee_id: str, data: dict) -> dict:
    """A new rate from a date. Giving a date that already has one replaces it (a correction), so a day never has two."""
    _require()
    emp = employees._get(employee_id)
    values = clean_rate(data, today=employees.today_th())
    existing = supabase.select("employee_rates", {"select": "id", "employee_id": f"eq.{emp['id']}", "effective_from": f"eq.{values['effective_from']}", "limit": "1"})
    if existing:
        supabase.update("employee_rates", {"id": f"eq.{existing[0]['id']}"}, values)
    else:
        supabase.insert("employee_rates", [{"employee_id": emp["id"], **values}])
    return list_rates(emp["id"])


def delete_rate(rate_id: str) -> dict:
    _require()
    try:
        rid = str(uuid.UUID(str(rate_id)))
    except ValueError:
        raise employees.NotFound()
    rows = supabase.select("employee_rates", {"select": "*", "id": f"eq.{rid}", "limit": "1"})
    if not rows:
        raise employees.NotFound()
    supabase.delete("employee_rates", {"id": f"eq.{rid}"})
    return list_rates(rows[0]["employee_id"])
