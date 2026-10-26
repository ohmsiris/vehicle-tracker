"""
เงินกู้ -- what an employee owes the company (a loan, an advance, or an accident cost turned into a loan) and how it is paid back
(employee_loans + employee_loan_entries, schema_v23.sql). Repaid by salary deduction, no interest.

A loan has a principal and a REPAYMENT PLAN: one of six modes (MODES), starting in `first_month` -- later than the loan month means a delayed
start. The ledger (entries) holds what really happened: repay (คืน), add (กู้เพิ่ม), adjust (ปรับยอด, signed). The balance is never stored:
principal + adds + adjusts - repayments. The plan is turned into a month-by-month schedule (build_schedule) and the repayments are laid over it
oldest month first, so every month is paid / part-paid / due / future and what is overdue is known. Money is worked in whole satang (integers).

Pure rules (clean_loan, build_schedule, summarize ...) are unit-tested; the database calls are thin. Needs schema_v23.sql.
"""
import math
import re
import time
import uuid
from datetime import date, datetime
from typing import Optional

from app import employees, supabase

KINDS = {"loan": "เงินกู้ทั่วไป", "accident": "อุบัติเหตุ / ความเสียหาย", "advance": "เบิกล่วงหน้า", "other": "อื่นๆ"}
MODES = {
    "monthly_fixed": "หักเดือนละเท่ากัน",
    "months_equal": "แบ่งเท่า ๆ กัน N เดือน",
    "percent": "หัก % ของยอดกู้ต่อเดือน",
    "lump_sum": "หักก้อนเดียว",
    "custom": "กำหนดเองทีละเดือน",
    "flexible": "ไม่กำหนดงวด (บันทึกเมื่อคืน)",
}
ENTRY_KINDS = ("repay", "add", "adjust")
METHODS = {"salary": "หักเงินเดือน", "cash": "เงินสด", "transfer": "โอน", "other": "อื่นๆ"}
STATUSES = ("active", "closed", "cancelled")
MAX_AMOUNT = 1_000_000
MAX_ROWS = 120
NEEDS_V23 = "ยังใช้เงินกู้ไม่ได้ — ต้องรันไฟล์ schema_v23.sql ใน Supabase ก่อน"
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})(?:-\d{2})?$")

employees.REFERENCED_BY.append(("employee_loans", "employee_id"))       # an employee with a loan can only be "ลาออก", never deleted


class InvalidLoan(employees.InvalidEmployee):
    """User-fixable problem; str() is a Thai message safe to show."""


# ------------------------------------------------------------------ small pure helpers
def cents(x) -> int:
    return int(round(float(x) * 100))


def baht(c: int) -> float:
    return round(c / 100, 2)


def this_month(today: date) -> str:
    return today.strftime("%Y-%m")


def add_month(ym: str, n: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def parse_month(value, what: str = "เดือน") -> Optional[str]:
    if value in (None, ""):
        return None
    m = MONTH_RE.match(str(value).strip())
    if not m or not 1 <= int(m.group(2)) <= 12 or not 1950 <= int(m.group(1)) < 2400:
        raise InvalidLoan(f"{what}ไม่ถูกต้อง (ใช้ ค.ศ. เช่น 2026-12)")
    return f"{m.group(1)}-{m.group(2)}"


def _text(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _money(value, what: str, *, maximum: float = MAX_AMOUNT, signed: bool = False) -> float:
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        raise InvalidLoan(f"{what}ไม่ถูกต้อง")
    if not math.isfinite(n) or (n <= 0 and not signed) or (signed and n == 0) or abs(n) > maximum:
        raise InvalidLoan(f"{what}ต้อง{'ไม่เป็น 0' if signed else 'มากกว่า 0'} และไม่เกิน {maximum:,.0f}")
    return round(n, 2)


def _date(value, what: str, today: date) -> str:
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise InvalidLoan(f"{what}ไม่ถูกต้อง")
    if d.year < 1950 or d.year >= 2400:
        raise InvalidLoan(f"{what}ไม่ถูกต้อง (ปีต้องเป็น ค.ศ. เช่น 2026)")
    if d > today:
        raise InvalidLoan(f"{what}เป็นวันในอนาคต")
    return d.isoformat()


# ------------------------------------------------------------------ the loan's terms
def clean_terms(data: dict, *, amount: float, loan_month: str) -> dict:
    """The repayment plan from the form: {repay_mode, repay_amount, repay_months, repay_percent, schedule, first_month}. Raises InvalidLoan."""
    mode = _text(data.get("repay_mode"), 20)
    if mode not in MODES:
        raise InvalidLoan("เลือกวิธีคืนเงิน")
    first = parse_month(data.get("first_month"), "เดือนที่เริ่มหัก") or add_month(loan_month, 1)
    if first > add_month(loan_month, 36):
        raise InvalidLoan("เดือนที่เริ่มหักไกลเกินไป (เกิน 3 ปีหลังวันที่กู้)")
    out = {"repay_mode": mode, "repay_amount": None, "repay_months": None, "repay_percent": None, "schedule": None, "first_month": f"{first}-01"}
    if mode == "monthly_fixed":
        out["repay_amount"] = _money(data.get("repay_amount"), "ยอดหักต่อเดือน", maximum=amount)
    elif mode == "months_equal":
        try:
            n = int(str(data.get("repay_months")).strip())
        except (TypeError, ValueError):
            raise InvalidLoan("จำนวนเดือนไม่ถูกต้อง")
        if not 1 <= n <= MAX_ROWS:
            raise InvalidLoan(f"จำนวนเดือนต้อง 1–{MAX_ROWS}")
        out["repay_months"] = n
    elif mode == "percent":
        pct = _money(data.get("repay_percent"), "เปอร์เซ็นต์ต่อเดือน", maximum=100)
        out["repay_percent"] = pct
    elif mode == "custom":
        raw = data.get("amounts")
        if isinstance(raw, str):
            raw = [x for x in re.split(r"[,\s;]+", raw.strip()) if x]
        if not isinstance(raw, list) or not raw:
            raise InvalidLoan("ใส่ยอดหักของแต่ละเดือน (เช่น 2000, 2000, 1500)")
        if len(raw) > MAX_ROWS:
            raise InvalidLoan(f"กำหนดเองได้ไม่เกิน {MAX_ROWS} เดือน")
        amounts = [_money(x, "ยอดหัก", maximum=MAX_AMOUNT) for x in raw]
        if cents(sum(amounts)) > cents(amount):
            raise InvalidLoan(f"ยอดหักรวม {sum(amounts):,.2f} มากกว่ายอดกู้ {amount:,.2f}")
        out["schedule"] = [{"month": add_month(first, i), "amount": a} for i, a in enumerate(amounts)]
    return out


def clean_loan(data: dict, *, today: date) -> dict:
    """A new loan from the form (employee_id is checked by the caller). Raises InvalidLoan."""
    try:
        employee_id = str(uuid.UUID(str(data.get("employee_id"))))
    except ValueError:
        raise InvalidLoan("เลือกพนักงานด้วย")
    kind = _text(data.get("kind") or "loan", 20)
    if kind not in KINDS:
        raise InvalidLoan("เลือกประเภท")
    amount = _money(data.get("amount"), "ยอดเงิน")
    loan_date = _date(data.get("loan_date") or today.isoformat(), "วันที่", today)
    values = {"employee_id": employee_id, "kind": kind, "amount": amount, "loan_date": loan_date,
              "reason": _text(data.get("reason"), 300) or None, "memo_ref": _text(data.get("memo_ref"), 100) or None,
              "approved_by": _text(data.get("approved_by"), 100) or None, "notes": _text(data.get("notes"), 500) or None}
    values.update(clean_terms(data, amount=amount, loan_month=loan_date[:7]))
    return values


# ------------------------------------------------------------------ the schedule
def build_schedule(loan: dict, total_c: int) -> list[dict]:
    """The expected deductions, month by month: [{month 'YYYY-MM', amount (baht)}] for a `total_c` (satang) to be repaid. Flexible = none."""
    mode, first = loan.get("repay_mode"), str(loan.get("first_month") or "")[:7]
    if mode == "flexible" or total_c <= 0 or not first:
        return []
    if mode == "custom":
        return [{"month": r["month"], "amount": float(r["amount"])} for r in (loan.get("schedule") or [])]
    rows_c: list[int] = []
    if mode == "lump_sum":
        rows_c = [total_c]
    elif mode == "months_equal":
        n = int(loan.get("repay_months") or 1)
        base = (total_c // n) // 100 * 100 or total_c // n          # whole baht per month; the last month takes what is left
        rows_c = [base] * (n - 1) + [total_c - base * (n - 1)]
    else:                                                            # monthly_fixed / percent: the same amount until it is paid
        inst = cents(loan["repay_amount"]) if mode == "monthly_fixed" else max(1, cents(float(loan["amount"]) * float(loan["repay_percent"]) / 100))
        left = total_c
        while left > 0 and len(rows_c) < MAX_ROWS:
            rows_c.append(min(inst, left))
            left -= rows_c[-1]
    return [{"month": add_month(first, i), "amount": baht(c)} for i, c in enumerate(rows_c) if c > 0]


def summarize(loan: dict, entries: list[dict], today: date) -> dict:
    """Where the loan stands: totals, the schedule with what each month has paid, the next deduction and what is overdue."""
    add_c = sum(cents(e["amount"]) for e in entries if e["kind"] == "add")
    adj_c = sum(cents(e["amount"]) for e in entries if e["kind"] == "adjust")
    repaid_c = sum(cents(e["amount"]) for e in entries if e["kind"] == "repay")
    total_c = cents(loan["amount"]) + add_c + adj_c
    balance_c = total_c - repaid_c
    now = this_month(today)
    rows, left = [], repaid_c
    for r in build_schedule(loan, total_c):
        c = cents(r["amount"])
        paid = min(c, max(left, 0))
        left -= paid
        if paid >= c:
            state = "paid"
        elif r["month"] < now:
            state = "overdue"                                     # a month gone by and not (fully) deducted
        elif r["month"] == now:
            state = "partial" if paid else "due"
        else:
            state = "future"
        rows.append({"month": r["month"], "amount": r["amount"], "paid": baht(paid), "state": state})
    open_rows = [r for r in rows if r["state"] != "paid"] if balance_c > 0 else []
    overdue = sum(cents(r["amount"]) - cents(r["paid"]) for r in open_rows if r["month"] < now)
    this_row = next((r for r in open_rows if r["month"] == now), None)
    return {"amount": baht(cents(loan["amount"])), "adds": baht(add_c), "adjusts": baht(adj_c), "total": baht(total_c), "repaid": baht(repaid_c), "balance": baht(max(balance_c, 0)),
            "schedule": rows, "next": ({"month": open_rows[0]["month"], "amount": baht(cents(open_rows[0]["amount"]) - cents(open_rows[0]["paid"]))} if open_rows else None),
            "overdue": baht(overdue), "due_now": baht(cents(this_row["amount"]) - cents(this_row["paid"])) if this_row else 0.0,
            "progress": round(repaid_c / total_c, 4) if total_c > 0 else 0.0}


def clean_entry(data: dict, *, loan: dict, summary: dict, today: date) -> dict:
    """One ledger line: repay (≤ the balance), add (more borrowed), adjust (signed; the total can never go below what is repaid)."""
    kind = _text(data.get("kind"), 10)
    if kind not in ENTRY_KINDS:
        raise InvalidLoan("ประเภทรายการไม่ถูกต้อง")
    if loan.get("status") == "cancelled":
        raise InvalidLoan("เงินกู้นี้ยกเลิกแล้ว")
    amount = _money(data.get("amount"), "ยอดเงิน", signed=(kind == "adjust"))
    if kind == "repay" and cents(amount) > cents(summary["balance"]):
        raise InvalidLoan(f"คืนเกินยอดคงเหลือ ({summary['balance']:,.2f} บาท)")
    if kind == "adjust" and cents(summary["total"]) + cents(amount) < cents(summary["repaid"]):
        raise InvalidLoan("ปรับยอดแล้วต่ำกว่ายอดที่คืนไปแล้ว")
    method = _text(data.get("method") or ("salary" if kind == "repay" else "other"), 10)
    if method not in METHODS:
        raise InvalidLoan("วิธีรับเงินไม่ถูกต้อง")
    pay_month = parse_month(data.get("pay_month"), "เดือนที่หัก")
    return {"kind": kind, "amount": amount, "entry_date": _date(data.get("entry_date") or today.isoformat(), "วันที่", today), "method": method,
            "pay_month": f"{pay_month}-01" if pay_month else None, "note": _text(data.get("note"), 300) or None}


def preview(data: dict, today: date) -> dict:
    """The schedule a set of terms would give, before anything is saved."""
    amount = _money(data.get("amount"), "ยอดเงิน")
    loan_date = _date(data.get("loan_date") or today.isoformat(), "วันที่", today)
    terms = clean_terms(data, amount=amount, loan_month=loan_date[:7])
    loan = {"amount": amount, **terms}
    s = summarize(loan, [], today)
    s["scheduled"] = baht(sum(cents(r["amount"]) for r in s["schedule"]))
    s["unscheduled"] = baht(cents(amount) - cents(s["scheduled"])) if terms["repay_mode"] != "flexible" else None
    return s


def describe(loan: dict) -> str:
    """The plan in a sentence, for lists: "เดือนละ 2,000 เริ่ม 2026-12"."""
    mode, first = loan.get("repay_mode"), str(loan.get("first_month") or "")[:7]
    start = f" เริ่ม {first}" if first else ""
    if mode == "monthly_fixed":
        return f"เดือนละ {float(loan['repay_amount']):,.0f}{start}"
    if mode == "months_equal":
        return f"แบ่ง {loan['repay_months']} เดือน{start}"
    if mode == "percent":
        return f"เดือนละ {float(loan['repay_percent']):g}% ของยอดกู้{start}"
    if mode == "lump_sum":
        return f"หักก้อนเดียว{start}"
    if mode == "custom":
        return "กำหนดเอง " + " / ".join(f"{float(r['amount']):,.0f}" for r in (loan.get("schedule") or [])[:6]) + start
    return "ไม่กำหนดงวด"


# ------------------------------------------------------------------ the database
_probe = {"at": 0.0, "ok": False}


def has_v23() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("employee_loans", {"select": "id", "limit": "1"})
        supabase.select("employee_loan_entries", {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not (employees.has_v21() and has_v23()):
        raise InvalidLoan(NEEDS_V23)


def _all_loans() -> list[dict]:
    return supabase.select_all("employee_loans", {"select": "*", "order": "loan_date.desc,id.asc"})


def _entries_by_loan() -> dict[str, list[dict]]:
    out: dict = {}
    for e in supabase.select_all("employee_loan_entries", {"select": "*", "order": "entry_date.asc,created_at.asc,id.asc"}):
        out.setdefault(e["loan_id"], []).append(e)
    return out


def _who() -> dict[str, dict]:
    return {e["id"]: e for e in employees._all()}


def _card(loan: dict, entries: list[dict], who: dict, today: date) -> dict:
    e = who.get(loan["employee_id"]) or {}
    s = summarize(loan, entries, today)
    return {"id": loan["id"], "employee_id": loan["employee_id"], "employee": employees.display_name(e.get("prefix"), e.get("full_name") or "?"), "code": e.get("code") or "",
            "branch": e.get("branch") or "", "employee_status": e.get("status") or "active", "kind": loan["kind"], "kind_label": KINDS.get(loan["kind"], loan["kind"]),
            "loan_date": loan["loan_date"], "reason": loan.get("reason") or "", "memo_ref": loan.get("memo_ref") or "", "approved_by": loan.get("approved_by") or "",
            "notes": loan.get("notes") or "", "status": loan["status"], "closed_date": loan.get("closed_date"), "repay_mode": loan["repay_mode"],
            "mode_label": MODES.get(loan["repay_mode"], ""), "plan": describe(loan), "repay_amount": loan.get("repay_amount"), "repay_months": loan.get("repay_months"),
            "repay_percent": loan.get("repay_percent"), "first_month": str(loan.get("first_month") or "")[:7], **s}


def _totals(cards: list[dict]) -> dict:
    live = [c for c in cards if c["status"] == "active"]
    return {"active": len(live), "balance": round(sum(c["balance"] for c in live), 2), "overdue": round(sum(c["overdue"] for c in live), 2),
            "due_now": round(sum(c["due_now"] for c in live), 2), "people": len({c["employee_id"] for c in live})}


def options() -> dict:
    return {"ready": employees.has_v21() and has_v23(), "kinds": KINDS, "modes": MODES, "methods": METHODS}


def list_loans(employee_id: str = "") -> dict:
    if not (employees.has_v21() and has_v23()):
        return {"ready": False, "loans": [], "totals": _totals([])}
    today = employees.today_th()
    who, entries = _who(), _entries_by_loan()
    cards = [_card(l, entries.get(l["id"], []), who, today) for l in _all_loans() if not employee_id or l["employee_id"] == employee_id]
    return {"ready": True, "loans": cards, "totals": _totals(cards), "month": this_month(today)}


def _get(loan_id: str) -> dict:
    try:
        lid = str(uuid.UUID(str(loan_id)))
    except ValueError:
        raise employees.NotFound()
    rows = supabase.select("employee_loans", {"select": "*", "id": f"eq.{lid}", "limit": "1"})
    if not rows:
        raise employees.NotFound()
    return rows[0]


def _entries_of(loan_id: str) -> list[dict]:
    return supabase.select_all("employee_loan_entries", {"select": "*", "loan_id": f"eq.{loan_id}", "order": "entry_date.asc,created_at.asc,id.asc"})


def get_loan(loan_id: str) -> dict:
    _require()
    loan = _get(loan_id)
    entries = _entries_of(loan["id"])
    card = _card(loan, entries, _who(), employees.today_th())
    card["entries"] = [{"id": e["id"], "entry_date": e["entry_date"], "kind": e["kind"], "amount": float(e["amount"]), "method": e["method"], "method_label": METHODS.get(e["method"], ""),
                        "pay_month": str(e.get("pay_month") or "")[:7], "note": e.get("note") or ""} for e in entries]
    return card


def _refresh_status(loan: dict) -> None:
    """Paid off -> closed (with the date); owing again (e.g. after "กู้เพิ่ม") -> active."""
    if loan["status"] == "cancelled":
        return
    s = summarize(loan, _entries_of(loan["id"]), employees.today_th())
    if s["balance"] <= 0 and loan["status"] == "active":
        supabase.update("employee_loans", {"id": f"eq.{loan['id']}"}, {"status": "closed", "closed_date": employees.today_th().isoformat()})
    elif s["balance"] > 0 and loan["status"] == "closed":
        supabase.update("employee_loans", {"id": f"eq.{loan['id']}"}, {"status": "active", "closed_date": None})


def create_loan(data: dict) -> dict:
    _require()
    values = clean_loan(data, today=employees.today_th())
    emp = employees._get(values["employee_id"])
    if emp.get("status") == "resigned":
        raise InvalidLoan("คนนี้ลาออกแล้ว — บันทึกเงินกู้ใหม่ไม่ได้")
    row = supabase.insert("employee_loans", [values])[0]
    return get_loan(row["id"])


def edit_loan(loan_id: str, data: dict) -> dict:
    """Change the plan (mode, amounts, first month) or the notes of a loan that is still being repaid. The principal and the person stay."""
    _require()
    loan = _get(loan_id)
    if loan["status"] != "active":
        raise InvalidLoan("แก้ได้เฉพาะเงินกู้ที่ยังผ่อนอยู่")
    values: dict = {}
    for key, limit in (("reason", 300), ("memo_ref", 100), ("approved_by", 100), ("notes", 500)):
        if key in data:
            values[key] = _text(data.get(key), limit) or None
    if "kind" in data:
        if data["kind"] not in KINDS:
            raise InvalidLoan("เลือกประเภท")
        values["kind"] = data["kind"]
    if "repay_mode" in data:
        values.update(clean_terms(data, amount=float(loan["amount"]), loan_month=str(loan["loan_date"])[:7]))
    if values:
        supabase.update("employee_loans", {"id": f"eq.{loan['id']}"}, values)
    return get_loan(loan["id"])


def add_entry(loan_id: str, data: dict) -> dict:
    _require()
    loan = _get(loan_id)
    entries = _entries_of(loan["id"])
    values = clean_entry(data, loan=loan, summary=summarize(loan, entries, employees.today_th()), today=employees.today_th())
    supabase.insert("employee_loan_entries", [{"loan_id": loan["id"], **values}])
    _refresh_status(_get(loan["id"]))
    return get_loan(loan["id"])


def delete_entry(loan_id: str, entry_id: str) -> dict:
    _require()
    loan = _get(loan_id)
    try:
        eid = str(uuid.UUID(str(entry_id)))
    except ValueError:
        raise employees.NotFound()
    if not supabase.select("employee_loan_entries", {"select": "id", "id": f"eq.{eid}", "loan_id": f"eq.{loan['id']}", "limit": "1"}):
        raise employees.NotFound()
    supabase.delete("employee_loan_entries", {"id": f"eq.{eid}"})
    _refresh_status(_get(loan["id"]))
    return get_loan(loan["id"])


def cancel_loan(loan_id: str, data: dict) -> dict:
    """Written off / entered wrongly: kept in the history as ยกเลิก, no longer counted."""
    _require()
    loan = _get(loan_id)
    if loan["status"] == "cancelled":
        raise InvalidLoan("ยกเลิกไปแล้ว")
    note = _text(data.get("note"), 300)
    supabase.update("employee_loans", {"id": f"eq.{loan['id']}"}, {"status": "cancelled", "closed_date": employees.today_th().isoformat(),
                                                                    "notes": " · ".join(x for x in (loan.get("notes"), f"ยกเลิก: {note}" if note else "ยกเลิก") if x)[:500]})
    return get_loan(loan["id"])


def delete_loan(loan_id: str) -> None:
    """Only a loan with nothing recorded against it (entered by mistake)."""
    _require()
    loan = _get(loan_id)
    if _entries_of(loan["id"]):
        raise InvalidLoan("ลบไม่ได้ — มีรายการคืน/ปรับยอดแล้ว ใช้ “ยกเลิก” แทน")
    supabase.delete("employee_loans", {"id": f"eq.{loan['id']}"})


def month_deductions(month: str = "") -> dict:
    """What payroll should deduct in a month: for every loan still being repaid, this month's instalment (and any older one still unpaid)."""
    _require()
    today = employees.today_th()
    month = parse_month(month) or this_month(today)
    who, entries = _who(), _entries_by_loan()
    rows = []
    for loan in _all_loans():
        if loan["status"] != "active":
            continue
        s = summarize(loan, entries.get(loan["id"], []), date(int(month[:4]), int(month[5:7]), 28))      # looked at from that month
        row = next((r for r in s["schedule"] if r["month"] == month), None)
        recorded = sum(cents(e["amount"]) for e in entries.get(loan["id"], []) if e["kind"] == "repay" and str(e.get("pay_month") or "")[:7] == month)
        due = baht(cents(row["amount"]) - cents(row["paid"])) if row else 0.0
        if not row and not s["overdue"]:
            continue
        e = who.get(loan["employee_id"]) or {}
        rows.append({"loan_id": loan["id"], "employee_id": loan["employee_id"], "code": e.get("code") or "", "employee": employees.display_name(e.get("prefix"), e.get("full_name") or "?"),
                     "branch": e.get("branch") or "", "kind_label": KINDS.get(loan["kind"], ""), "scheduled": row["amount"] if row else 0.0, "due": due,
                     "overdue_before": baht(sum(cents(r["amount"]) - cents(r["paid"]) for r in s["schedule"] if r["month"] < month and r["state"] != "paid")),
                     "recorded": baht(recorded), "balance": s["balance"]})
    rows.sort(key=lambda r: (r["branch"], r["code"]))
    return {"month": month, "rows": rows, "total_due": round(sum(r["due"] for r in rows), 2), "total_overdue": round(sum(r["overdue_before"] for r in rows), 2)}


def apply_month(month: str, loan_ids: Optional[list] = None) -> dict:
    """"หักแล้ว": record this month's deduction of each loan as a salary repayment (only what is still unpaid for that month)."""
    _require()
    today = employees.today_th()
    month = parse_month(month) or this_month(today)
    done = 0
    for r in month_deductions(month)["rows"]:
        if r["due"] <= 0 or (loan_ids and r["loan_id"] not in loan_ids):
            continue
        supabase.insert("employee_loan_entries", [{"loan_id": r["loan_id"], "kind": "repay", "amount": r["due"], "entry_date": today.isoformat(), "method": "salary",
                                                    "pay_month": f"{month}-01", "note": f"หักเงินเดือน {month}"}])
        _refresh_status(_get(r["loan_id"]))
        done += 1
    return {"month": month, "recorded": done}
