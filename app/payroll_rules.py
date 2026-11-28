"""คำนวณเงินเดือน: the owner fixes one person's pay rule from the pay page (owner, 29 Nov 2026: "right now when I want to edit ... I have
to find the page, find the person, find the field"). employee_pay_rules (schema_v93 / v94) had no page that edits it -- it was filled
from the workbook by scripts/seed_pay_rules.py -- so its fields that the owner decides person by person are edited here, on the pay
page's unfolded person: the pay group, commission %, baht per ซอง, เบี้ยขยัน, the second daily rate (a truck-only day), the route driver a
เด็กรถ rides with, and the ปกส. deducted each month. PATCH /api/payroll/rules/<employee id>, the owner only (app/main.py).

app/payroll.py only reads; this module is the one place that writes the table. Nothing else of the person is touched: the daily rate
stays in employee_rates (the employee page), who pays (company / HQ) on the employee's own form, and no private column is ever read.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from app import payroll

# what the pay page may change, and nothing else
EDITABLE = ("pay_group", "commission_pct", "bag_rate", "bonus", "rate2", "rides_with", "sso")
# (lowest, highest) a number may be; None = the field may be emptied
LIMITS = {"commission_pct": (0, 100), "bag_rate": (0, 100), "rate2": (1, 2000), "sso": (0, 1000)}
SAY = {"pay_group": "กลุ่มคิดเงิน", "commission_pct": "ค่าคอม %", "bag_rate": "บาทต่อซอง", "bonus": "เบี้ยขยัน", "rate2": "อัตราที่สอง",
       "rides_with": "ตามคนขับ", "sso": "ปกส."}
SELECT = "employee_id," + ",".join(payroll.RULE_FIELDS)


class RuleError(payroll.PayrollError):
    """Refused, with a message shown as it is (400)."""


def _uuid(value: Any, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise RuleError(f"{what}ไม่ถูกต้อง")


def _number(key: str, value: Any) -> Optional[float]:
    """A number of the field, or None for an empty box. Never a bool, NaN or infinity; inside the field's limits."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise RuleError(f"{SAY[key]}: ต้องเป็นตัวเลข")
    try:
        n = float(str(value).replace(",", "").strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        raise RuleError(f"{SAY[key]}: ต้องเป็นตัวเลข")
    lo, hi = LIMITS[key]
    if not math.isfinite(n) or n < lo or n > hi:
        raise RuleError(f"{SAY[key]}: ต้องอยู่ระหว่าง {lo:g} ถึง {hi:g}")
    return round(n, 2)


def clean(data: Any, employee_id: str) -> dict:
    """The fields to write, checked. Pure. Raises RuleError for an unknown field, a wrong value, or nothing to change."""
    if not isinstance(data, dict) or not data:
        raise RuleError("ไม่มีอะไรให้บันทึก")
    unknown = [k for k in data if k not in EDITABLE]
    if unknown:
        raise RuleError("แก้ช่องนี้ในหน้านี้ไม่ได้: " + ", ".join(str(k)[:40] for k in unknown))
    out: dict = {}
    for key, value in data.items():
        if key == "pay_group":
            if value not in payroll.GROUP_ORDER:
                raise RuleError("กลุ่มคิดเงินไม่ถูกต้อง")
            out[key] = value
        elif key == "bonus":
            if not isinstance(value, bool):
                raise RuleError("เบี้ยขยัน: ต้องเลือกได้ / ไม่ได้")
            out[key] = value
        elif key == "rides_with":
            if value is None or (isinstance(value, str) and not value.strip()):
                out[key] = None
            else:
                rid = _uuid(value, "คนขับที่ตาม ")
                if rid == employee_id:
                    raise RuleError("ตามคนขับ: เลือกตัวเองไม่ได้")
                out[key] = rid
        else:
            out[key] = _number(key, value)
    return out


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return False


def save(employee_id: str, data: Any, today: date) -> dict:
    """PATCH /api/payroll/rules/<employee id>: write the checked fields; a person with no rule yet gets one (a pay group is then
    needed). A changed ปกส. says it was the owner's (sso_source), so the สปส.1-10 list is never named for it; emptied = HQ's sheet again.
    The pay page's answers are forgotten, so the next read shows the change. -> the rule as saved (its whitelisted columns)."""
    from app import supabase
    eid = _uuid(employee_id, "รหัสพนักงาน")
    values = clean(data, eid)
    if not supabase.select("employees", {"select": "id", "id": f"eq.{eid}", "limit": "1"}):
        raise RuleError("ไม่พบพนักงานคนนี้")
    if values.get("rides_with") and not supabase.select("employees", {"select": "id", "id": f"eq.{values['rides_with']}", "limit": "1"}):
        raise RuleError("ไม่พบคนขับที่เลือก")
    had = supabase.select("employee_pay_rules", {"select": SELECT, "employee_id": f"eq.{eid}", "limit": "1"})
    old = had[0] if had else {}
    if "sso" in values:
        if had and _same(old.get("sso"), values["sso"]):
            values.pop("sso")                                 # unchanged: the list it came from stays named
        else:
            values["sso_source"] = f"{payroll.SSO_BY_OWNER} {payroll.dmy(today)}" if values["sso"] is not None else None
    if had and not values:
        return {k: old.get(k) for k in ("employee_id",) + payroll.RULE_FIELDS}
    values["updated_at"] = datetime.now(timezone.utc).isoformat()
    if had:
        rows = supabase.update("employee_pay_rules", {"employee_id": f"eq.{eid}"}, values)
    else:
        if not values.get("pay_group"):
            raise RuleError("คนนี้ยังไม่มีกติกาคิดเงิน — เลือกกลุ่มคิดเงินก่อน")
        rows = supabase.insert("employee_pay_rules", [{"employee_id": eid, **values}])
    payroll.forget()
    row = (rows or [{}])[0] if isinstance(rows, list) else {}
    merged = {**old, "employee_id": eid, **{k: v for k, v in values.items() if k != "updated_at"}, **{k: row[k] for k in row if k in payroll.RULE_FIELDS}}
    return {k: merged.get(k) for k in ("employee_id",) + payroll.RULE_FIELDS}
