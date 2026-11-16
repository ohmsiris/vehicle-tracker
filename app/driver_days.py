"""ยอดขายรายวัน (คิดเงินเดือน): the owner types, driver by driver and half-month by half-month, what each รถสาย driver sold each day
(the total of each product at the bottom of the paper sales sheet) and the money they brought in -- the figures the pay is worked out from
(owner, 16 Nov 2026). Even with the phone sales sheet, every number is typed here from the paper.

Half-months are the pay periods: 26th - 10th, then 11th - 25th (a company month "2026-11" = 26 Oct - 25 Nov; accounting.period_bounds).
One row per driver per day (route_driver_days, schema_v78): qty {product: number}, money (baht). A day left completely empty has no row."""
import re
from datetime import date, timedelta
from typing import Optional

from app import supabase
from app.accounting import period_bounds
from app.route_sales import PRODUCTS

BRANCH = "สระบุรี"
POSITION = "พนักงานขับรถสาย"
MAX_NUMBER = 1_000_000


class DriverDaysError(Exception):
    pass


def period_of(d: date) -> tuple[str, int]:
    """The half-month a day is in: ("2026-11", 1) for 26 Oct - 10 Nov."""
    if d.day >= 26:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return f"{y}-{m:02d}", 1
    return f"{d.year}-{d.month:02d}", (1 if d.day <= 10 else 2)


def days_of(month: str, half: int) -> list[date]:
    start, end = period_bounds(month, half)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _check_period(month: str, half) -> tuple[str, int]:
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", str(month or "")):
        raise DriverDaysError("เดือนไม่ถูกต้อง")
    try:
        half = int(half)
    except (TypeError, ValueError):
        half = 0
    if half not in (1, 2):
        raise DriverDaysError("งวดไม่ถูกต้อง (1 = 26–10, 2 = 11–25)")
    return month, half


def _number(v, label: str, whole: bool = False) -> Optional[float]:
    """A typed figure -> number; blank -> None. Commas are fine ("1,250")."""
    if v is None:
        return None
    text = str(v).replace(",", "").strip()
    if text == "":
        return None
    try:
        n = float(text)
    except ValueError:
        raise DriverDaysError(f"{label}: \"{v}\" ไม่ใช่ตัวเลข")
    if n < 0 or n > MAX_NUMBER:
        raise DriverDaysError(f"{label}: ตัวเลขไม่ถูกต้อง ({v})")
    if whole and n != int(n):
        raise DriverDaysError(f"{label}: ต้องเป็นจำนวนเต็ม ({v})")
    return int(n) if n == int(n) else round(n, 2)


def clean_rows(rows: list, month: str, half: int) -> list[dict]:
    """What the page sent -> the rows to keep: [{day, qty, money}], only days with at least one figure, only days of this half-month."""
    allowed = {d.isoformat() for d in days_of(month, half)}
    keys = [k for k, _ in PRODUCTS]
    labels = dict(PRODUCTS)
    out, seen = [], set()
    for r in rows or []:
        if not isinstance(r, dict):
            raise DriverDaysError("ข้อมูลไม่ถูกต้อง")
        day = str(r.get("day") or "")
        if day not in allowed:
            raise DriverDaysError(f"วันที่ {day} ไม่อยู่ในงวดนี้")
        if day in seen:
            raise DriverDaysError(f"วันที่ {day} ซ้ำ")
        seen.add(day)
        dd = f"{day[8:10]}/{day[5:7]}"
        q = r.get("qty") or {}
        qty = {k: n for k in keys if (n := _number(q.get(k), f"{dd} {labels[k]}", whole=True)) is not None}
        money = _number(r.get("money"), f"{dd} เงินที่ได้")
        if qty or money is not None:
            out.append({"day": day, "qty": qty, "money": money})
    return sorted(out, key=lambda x: x["day"])


def totals(rows: list[dict]) -> dict:
    t = {k: 0 for k, _ in PRODUCTS}
    money = 0.0
    for r in rows:
        for k, n in (r.get("qty") or {}).items():
            if k in t:
                t[k] += n or 0
        money += float(r.get("money") or 0)
    return {"qty": t, "money": round(money, 2), "days": sum(1 for r in rows if r.get("qty") or r.get("money") is not None)}


# ------------------------------------------------------------------ database
def drivers() -> list[dict]:
    """The รถสาย drivers (active, Saraburi), with the number of the truck each drives now (the truck's main driver)."""
    # a truck's main driver is a row of the drivers list (fleet), matched to the employee by full name -- as น้ำมันเงินสด does
    names = {d["id"]: re.sub(r"\s+", " ", d.get("name") or "").strip() for d in supabase.select_all("drivers", {"select": "id,name", "order": "id.asc"})}
    trucks = {}
    for v in supabase.select_all("vehicles", {"select": "vehicle_number,main_driver_id,usage", "active": "eq.true", "branch": f"eq.{BRANCH}", "order": "id.asc"}):
        name = names.get(v.get("main_driver_id"))
        if name and v.get("vehicle_number") and (name not in trucks or v.get("usage") == "รถสาย"):
            trucks[name] = v["vehicle_number"]
    out = []
    for e in supabase.select_all("employees", {"select": "id,code,full_name,nickname", "branch": f"eq.{BRANCH}", "status": "eq.active",
                                               "position": f"eq.{POSITION}", "order": "full_name.asc"}):
        out.append({"id": e["id"], "code": e.get("code") or "", "name": e["full_name"], "nickname": e.get("nickname") or "",
                    "truck": trucks.get(re.sub(r"\s+", " ", e["full_name"] or "").strip())})
    out.sort(key=lambda x: (int(x["truck"]) if str(x["truck"] or "").isdigit() else 999, x["name"]))
    return out


def _employee(employee_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", str(employee_id or "")):
        raise DriverDaysError("ไม่พบคนขับ")
    rows = supabase.select("employees", {"select": "id,full_name,nickname", "id": f"eq.{employee_id}"})
    if not rows:
        raise DriverDaysError("ไม่พบคนขับ")
    return rows[0]


def _saved(employee_id: str, month: str, half: int) -> list[dict]:
    start, end = period_bounds(month, half)
    return supabase.select_all("route_driver_days", {"select": "day,qty,money,updated_at", "employee_id": f"eq.{employee_id}",
                                                     "day": f"gte.{start.isoformat()}", "and": f"(day.lte.{end.isoformat()})", "order": "day.asc"})


def overview(month: str, half) -> dict:
    """The page's list: every driver, and how many days of this half-month each already has."""
    month, half = _check_period(month, half)
    start, end = period_bounds(month, half)
    filled: dict = {}
    for r in supabase.select_all("route_driver_days", {"select": "employee_id,day", "day": f"gte.{start.isoformat()}",
                                                       "and": f"(day.lte.{end.isoformat()})", "order": "id.asc"}):
        filled[r["employee_id"]] = filled.get(r["employee_id"], 0) + 1
    return {"month": month, "half": half, "start": start.isoformat(), "end": end.isoformat(), "days": len(days_of(month, half)),
            "products": [{"key": k, "label": lab} for k, lab in PRODUCTS],
            "drivers": [{**d, "filled": filled.get(d["id"], 0)} for d in drivers()]}


def sheet(employee_id: str, month: str, half) -> dict:
    """One driver's half-month: a row for every day (empty where nothing is saved)."""
    month, half = _check_period(month, half)
    emp = _employee(employee_id)
    saved = {r["day"]: r for r in _saved(employee_id, month, half)}
    rows = [{"day": d.isoformat(), "qty": (saved.get(d.isoformat()) or {}).get("qty") or {}, "money": (saved.get(d.isoformat()) or {}).get("money")}
            for d in days_of(month, half)]
    last = max((r["updated_at"] for r in saved.values() if r.get("updated_at")), default=None)
    return {"employee_id": employee_id, "name": emp["full_name"], "nickname": emp.get("nickname") or "", "month": month, "half": half,
            "products": [{"key": k, "label": lab} for k, lab in PRODUCTS], "rows": rows, "totals": totals(list(saved.values())), "saved_at": last}


def save(employee_id: str, month: str, half, rows: list) -> dict:
    """Replace this driver's half-month with what the page holds (a day emptied on the page is removed)."""
    month, half = _check_period(month, half)
    _employee(employee_id)
    keep = clean_rows(rows, month, half)
    start, end = period_bounds(month, half)
    supabase.delete("route_driver_days", {"employee_id": f"eq.{employee_id}", "day": f"gte.{start.isoformat()}", "and": f"(day.lte.{end.isoformat()})"})
    if keep:
        supabase.insert("route_driver_days", [{"employee_id": employee_id, **r} for r in keep])
    return sheet(employee_id, month, half)
