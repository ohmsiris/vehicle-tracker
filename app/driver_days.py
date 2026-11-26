"""ยอดขายรายวัน (คิดเงินเดือน): the owner types, driver by driver and half-month by half-month, what each รถสาย driver sold each day
(the total of each product at the bottom of the paper sales sheet) and the money they brought in -- the figures the pay is worked out from
(owner, 16 Nov 2026). Even with the phone sales sheet, every number is typed here from the paper.

A spare driver (owner, 17 Nov 2026): a day is ticked สำรองขับ when the spare drove the truck. The spare only drives and delivers -- no
commission -- so that day counts for nobody: the truck's driver has the day off and its sales are not his. The summary and the Excel
export (a summary tab, then a tab per driver, a row per day) show the days driven, days off and the spare's days.

The page shows the WHOLE company month on one sheet (owner, 26 Nov 2026: "full page for the whole month is much better"): half = 0. Half-months
(1 = 26th - 10th, 2 = 11th - 25th) still work for an old page left open: a half-month save replaces only that half, so a stale tab can never
wipe the other half (a company month "2026-11" = 26 Oct - 25 Nov; accounting.period_bounds). Route drivers of BOTH branches are listed
(owner, 26 Nov 2026: หนึ่ง ชื่นชม, Kaeng Khoi's route truck); a truck number is shown with its branch when it is not Saraburi.
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
# money split (owner, 17 Nov 2026): for these drivers the day's money is typed as two sales figures in baht, ซอง and หลอด; เงินที่ได้ is their
# sum, worked out here (route_driver_settings.money_split)
PARTS = [("zong", "ยอดขายซอง (บาท)"), ("lod", "ยอดขายหลอด (บาท)")]


class DriverDaysError(Exception):
    pass


def period_of(d: date) -> tuple[str, int]:
    """The half-month a day is in: ("2026-11", 1) for 26 Oct - 10 Nov."""
    if d.day >= 26:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return f"{y}-{m:02d}", 1
    return f"{d.year}-{d.month:02d}", (1 if d.day <= 10 else 2)


def days_of(month: str, half: int) -> list[date]:
    start, end = period_bounds(month, half or None)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _check_period(month: str, half) -> tuple[str, int]:
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", str(month or "")):
        raise DriverDaysError("เดือนไม่ถูกต้อง")
    try:
        half = int(half or 0)
    except (TypeError, ValueError):
        raise DriverDaysError("งวดไม่ถูกต้อง (0 = ทั้งเดือน, 1 = 26–10, 2 = 11–25)")
    if half not in (0, 1, 2):
        raise DriverDaysError("งวดไม่ถูกต้อง (0 = ทั้งเดือน, 1 = 26–10, 2 = 11–25)")
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


def clean_rows(rows: list, month: str, half: int, owner: Optional[str] = None, spares: Optional[set] = None, split: bool = False) -> list[dict]:
    """What the page sent -> the rows to keep: [{day, qty, money, spare}], only days with at least one figure or ticked สำรองขับ, only
    days of this period (the month, or the half an old page asked for)."""
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
        parts = None
        if split:                                            # ซอง + หลอด in baht; the money is their sum, never typed
            given = r.get("parts") or {}
            parts = {k: n for k, label in PARTS if (n := _number(given.get(k), f"{dd} {label}")) is not None} or None
            money = round(sum(parts.values()), 2) if parts else None
        spare = r.get("spare") is True
        by = (str(r.get("spare_by") or "") or None) if spare else None          # who the spare was (optional)
        if by and (by == owner or (spares is not None and by not in spares)):
            raise DriverDaysError(f"{dd}: เลือกคนขับสำรองไม่ถูกต้อง")
        if qty or money is not None or spare:
            out.append({"day": day, "qty": qty, "money": money, "spare": spare, "spare_by": by, "money_parts": parts})
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


def summarize(rows: list[dict], people: list[dict], days: list[str]) -> list[dict]:
    """Per driver, over `days`: the days he drove (a row with figures, not ticked สำรองขับ), the days the spare drove his truck, his days
    off (every day of the period he did not drive -- the spare's days included), what he sold and the money. A สำรองขับ day counts for
    nobody: the spare only drives and delivers, no commission (owner, 17 Nov 2026). rows = route_driver_days {employee_id, day, qty, money,
    spare, spare_by}; people = [{id, name, nickname, truck}]. Whoever was the spare (spare_by) gets the day counted as a day he drove as the
    spare (as_spare_days) -- still no sales."""
    keys = [k for k, _ in PRODUCTS]
    acc = {p["id"]: {**p, "own": set(), "spare": set(), "as_spare": set(), "qty": {k: 0 for k in keys}, "money": 0.0} for p in people}
    for r in rows:
        if r.get("spare") and r.get("spare_by") in acc:
            acc[r["spare_by"]]["as_spare"].add(r["day"])
        a = acc.get(r["employee_id"])
        if a is None:
            continue
        if r.get("spare"):
            a["spare"].add(r["day"])
            continue
        if not (r.get("qty") or r.get("money") is not None):
            continue
        a["own"].add(r["day"])
        for k, n in (r.get("qty") or {}).items():
            if k in a["qty"]:
                a["qty"][k] += n or 0
        a["money"] += float(r.get("money") or 0)
    order = lambda a: (not a.get("truck"), int(a["truck"]) if str(a.get("truck") or "").isdigit() else 999, a["name"])  # noqa: E731
    out = []
    for a in sorted(acc.values(), key=order):
        if a.get("kind") == "หน้าโรง" and not a["as_spare"]:
            continue                                              # a หน้าโรง driver is listed only when he drove as the spare
        off = [d for d in days if d not in a["own"] and d not in a["as_spare"]]
        out.append({"id": a["id"], "name": a["name"], "nickname": a.get("nickname") or "", "truck": a.get("truck"), "kind": a.get("kind") or "สาย",
                    "branch": a.get("branch") or BRANCH,
                    "days_worked": len(a["own"]), "spare_days": len(a["spare"]), "as_spare_days": len(a["as_spare"]), "days_off": len(off),
                    "off_dates": off, "spare_dates": sorted(a["spare"]), "as_spare_dates": sorted(a["as_spare"]), "qty": a["qty"],
                    "money": round(a["money"], 2)})
    return out


# ------------------------------------------------------------------ database
def spares() -> list[dict]:
    """The รถสาย drivers (kind "สาย") and the หน้าโรง drivers (kind "หน้าโรง") -- only the หน้าโรง ones are offered as the day's spare
    (owner, 17 Nov 2026: "just FOH drivers is enough"); see spare_choices."""
    out = [{**d, "kind": "สาย"} for d in drivers()]
    for e in supabase.select_all("employees", {"select": "id,full_name,nickname", "branch": f"eq.{BRANCH}", "status": "eq.active",
                                               "position": "eq.พนักงานขับรถหน้าโรง", "order": "full_name.asc"}):
        out.append({"id": e["id"], "code": "", "name": e["full_name"], "nickname": e.get("nickname") or "", "truck": None, "kind": "หน้าโรง"})
    return out


def drivers() -> list[dict]:
    """The รถสาย drivers (active, both branches), with the truck each drives now (the truck's main driver) and their branch. Saraburi first;
    a truck number is matched within the driver's own branch (both branches have a #6)."""
    # a truck's main driver is a row of the drivers list (fleet), matched to the employee by full name -- as น้ำมันเงินสด does
    names = {d["id"]: re.sub(r"\s+", " ", d.get("name") or "").strip() for d in supabase.select_all("drivers", {"select": "id,name", "order": "id.asc"})}
    trucks = {}
    for v in supabase.select_all("vehicles", {"select": "vehicle_number,main_driver_id,usage,branch", "active": "eq.true", "order": "id.asc"}):
        name = names.get(v.get("main_driver_id"))
        key = (v.get("branch"), name)
        if name and v.get("vehicle_number") and (key not in trucks or v.get("usage") == "รถสาย"):
            trucks[key] = v["vehicle_number"]
    out = []
    for e in supabase.select_all("employees", {"select": "id,code,full_name,nickname,branch", "status": "eq.active",
                                               "position": f"eq.{POSITION}", "order": "full_name.asc"}):
        branch = e.get("branch") or BRANCH
        out.append({"id": e["id"], "code": e.get("code") or "", "name": e["full_name"], "nickname": e.get("nickname") or "", "branch": branch,
                    "truck": trucks.get((branch, re.sub(r"\s+", " ", e["full_name"] or "").strip()))})
    out.sort(key=lambda x: (x["branch"] != BRANCH, x["branch"], int(x["truck"]) if str(x["truck"] or "").isdigit() else 999, x["name"]))
    return out


def _employee(employee_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f-]{36}", str(employee_id or "")):
        raise DriverDaysError("ไม่พบคนขับ")
    rows = supabase.select("employees", {"select": "id,full_name,nickname", "id": f"eq.{employee_id}"})
    if not rows:
        raise DriverDaysError("ไม่พบคนขับ")
    return rows[0]


def split_ids() -> set:
    try:
        return {r["employee_id"] for r in supabase.select_all("route_driver_settings", {"select": "employee_id,money_split", "money_split": "eq.true",
                                                                                         "order": "employee_id.asc"})}
    except supabase.SupabaseError:
        return set()


def _saved(employee_id: str, month: str, half: int) -> list[dict]:
    start, end = period_bounds(month, half or None)
    return supabase.select_all("route_driver_days", {"select": "day,qty,money,spare,spare_by,money_parts,updated_at", "employee_id": f"eq.{employee_id}",
                                                     "day": f"gte.{start.isoformat()}", "and": f"(day.lte.{end.isoformat()})", "order": "day.asc"})


def _period_rows(start: date, end: date) -> list[dict]:
    return supabase.select_all("route_driver_days", {"select": "employee_id,day,qty,money,spare,spare_by,money_parts", "day": f"gte.{start.isoformat()}",
                                                     "and": f"(day.lte.{end.isoformat()})", "order": "day.asc,id.asc"})


def _norm(name) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()


def phone_suggestions(sale_days: list[dict], days: list[str]) -> dict:
    """The phone sales sheets (route_sales, sale_days rows) as this page's figures, day by day -- a suggestion while the paper and the phone
    run side by side (owner, 19 Nov 2026): qty = what the phone says was sold, money = the day's sales at list price (totals.commission_sales:
    cash + credit + billed, the figure the paper's เงินที่ได้ matches). Days with nothing sold are left out."""
    out: dict = {}
    for d in sale_days:
        day = str(d.get("day") or "")
        t = d.get("totals") or {}
        sold = {k: int(v) if float(v).is_integer() else float(v) for k, v in (t.get("sold") or {}).items() if k in dict(PRODUCTS) and v}
        if day not in days or not sold or day in out:
            continue
        money = t.get("commission_sales", t.get("amount"))
        out[day] = {"qty": sold, "money": round(float(money), 2) if money is not None else None, "sent": d.get("status") == "closed"}
    return out


def _phone_days(employee_id: str, full_name: str, start: date, end: date) -> dict:
    """This driver's phone sheets in the period: the routes whose truck he drives (fleet list), or whose driver / phone login carries his name."""
    try:
        me_ = next((d for d in drivers() if d["id"] == employee_id), {})
        truck, branch = me_.get("truck"), me_.get("branch") or BRANCH
        routes = supabase.select_all("sale_routes", {"select": "id,truck_no,driver_name", "branch": f"eq.{branch}", "order": "id.asc"})
        logins = supabase.select_all("sale_drivers", {"select": "route_id,name", "order": "id.asc"})
        me = _norm(full_name)
        ids = sorted({r["id"] for r in routes if (truck and str(r.get("truck_no") or "") == str(truck)) or _norm(r.get("driver_name")) == me}
                     | {x["route_id"] for x in logins if _norm(x.get("name")) == me})
        if not ids:
            return {}
        rows = supabase.select_all("sale_days", {"select": "day,status,totals", "route_id": f"in.({','.join(map(str, ids))})",
                                                 "day": f"gte.{start.isoformat()}", "and": f"(day.lte.{end.isoformat()})", "order": "day.asc,id.asc"})
    except supabase.SupabaseError:
        return {}
    return phone_suggestions(rows, [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)])


def overview(month: str, half) -> dict:
    """The page's list: every driver, how many days of this half-month each already has, and the period's summary."""
    month, half = _check_period(month, half)
    start, end = period_bounds(month, half or None)
    rows = _period_rows(start, end)
    filled: dict = {}
    for r in rows:
        filled[r["employee_id"]] = filled.get(r["employee_id"], 0) + 1
    people = spares()
    days = [d.isoformat() for d in days_of(month, half)]
    return {"month": month, "half": half, "start": start.isoformat(), "end": end.isoformat(), "days": len(days),
            "products": [{"key": k, "label": lab} for k, lab in PRODUCTS],
            "drivers": [{**d, "filled": filled.get(d["id"], 0)} for d in people if d["kind"] == "สาย"],
            "spares": [{"id": p["id"], "name": p["name"], "nickname": p["nickname"], "truck": p["truck"], "kind": p["kind"]}
                       for p in people if p["kind"] == "หน้าโรง"],
            "summary": summarize(rows, people, days)}


def sheet(employee_id: str, month: str, half) -> dict:
    """One driver's half-month: a row for every day (empty where nothing is saved)."""
    month, half = _check_period(month, half)
    emp = _employee(employee_id)
    saved = {r["day"]: r for r in _saved(employee_id, month, half)}
    rows = [{"day": d.isoformat(), "qty": (saved.get(d.isoformat()) or {}).get("qty") or {}, "money": (saved.get(d.isoformat()) or {}).get("money"),
             "spare": bool((saved.get(d.isoformat()) or {}).get("spare")), "spare_by": (saved.get(d.isoformat()) or {}).get("spare_by"),
             "parts": (saved.get(d.isoformat()) or {}).get("money_parts") or {}}
            for d in days_of(month, half)]
    last = max((r["updated_at"] for r in saved.values() if r.get("updated_at")), default=None)
    start, end = period_bounds(month, half or None)
    return {"phone": _phone_days(employee_id, emp["full_name"], start, end),"employee_id": employee_id, "name": emp["full_name"], "nickname": emp.get("nickname") or "", "month": month, "half": half,
            "split": employee_id in split_ids(), "parts": [{"key": k, "label": lab} for k, lab in PARTS],
            "products": [{"key": k, "label": lab} for k, lab in PRODUCTS], "rows": rows,
            "totals": totals([r for r in saved.values() if not r.get("spare")]), "spare_totals": totals([r for r in saved.values() if r.get("spare")]),
            "saved_at": last}


def save(employee_id: str, month: str, half, rows: list) -> dict:
    """Replace this driver's half-month with what the page holds (a day emptied on the page is removed)."""
    month, half = _check_period(month, half)
    _employee(employee_id)
    keep = clean_rows(rows, month, half, owner=employee_id, spares={p["id"] for p in spares() if p["kind"] == "หน้าโรง"},
                      split=employee_id in split_ids())
    start, end = period_bounds(month, half or None)
    supabase.delete("route_driver_days", {"employee_id": f"eq.{employee_id}", "day": f"gte.{start.isoformat()}", "and": f"(day.lte.{end.isoformat()})"})
    if keep:
        supabase.insert("route_driver_days", [{"employee_id": employee_id, **r} for r in keep])
    return sheet(employee_id, month, half)


# ------------------------------------------------------------------ the Excel export (owner, 17 Nov 2026)
def export(month: str, half) -> tuple[bytes, str]:
    """ยอดขายรายวัน as Excel: tab 1 = the summary (every driver: days driven, days off, the spare's days, each product, money); then a tab
    per driver, a row per day of the period (his days off and the spare's days marked; the spare's figures shown but not counted).
    half = 1 / 2, or 0 = the whole company month (26th - 25th)."""
    import io
    from datetime import date as _date
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    if not re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", str(month or "")):
        raise DriverDaysError("เดือนไม่ถูกต้อง")
    half = int(half or 0)
    if half not in (0, 1, 2):
        raise DriverDaysError("งวดไม่ถูกต้อง")
    start, end = period_bounds(month, half or None)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    rows = _period_rows(start, end)
    people = spares()
    names = {p["id"]: p for p in people}
    summary = summarize(rows, people, days)
    keys = [k for k, _ in PRODUCTS]
    labels = [lab for _, lab in PRODUCTS]
    be = lambda iso: f"{iso[8:10]}/{iso[5:7]}/{int(iso[:4]) + 543}"  # noqa: E731
    title = f"{be(start.isoformat())} - {be(end.isoformat())}"
    wb = Workbook()
    head_font, bold = Font(name="Angsana New", size=16, bold=True), Font(name="Angsana New", size=15, bold=True)
    body, grey = Font(name="Angsana New", size=15), Font(name="Angsana New", size=15, color="888888")
    thin = Side(style="thin", color="999999")
    box = Border(top=thin, bottom=thin, left=thin, right=thin)
    shade = PatternFill("solid", fgColor="EEEEEE")
    center = Alignment(horizontal="center", vertical="center")

    def table(ws, top, header, data, widths, money_col, total=None, faint=()):
        money_col = set(money_col) if isinstance(money_col, (set, list, tuple)) else {money_col}
        for c, h in enumerate(header, 1):
            cell = ws.cell(top, c, h)
            cell.font, cell.fill, cell.border, cell.alignment = bold, shade, box, center
        for i, row in enumerate(data, 1):
            for c, v in enumerate(row, 1):
                cell = ws.cell(top + i, c, v)
                cell.font, cell.border = (grey if i - 1 in faint else body), box
                if isinstance(v, (int, float)):
                    cell.number_format = "#,##0.00" if c in money_col else "#,##0"
        if total:
            r = top + len(data) + 1
            for c, v in enumerate(total, 1):
                cell = ws.cell(r, c, v)
                cell.font, cell.border, cell.fill = bold, box, shade
                if isinstance(v, (int, float)):
                    cell.number_format = "#,##0.00" if c in money_col else "#,##0"
        for c, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(1, c).column_letter].width = w

    ws = wb.active
    ws.title = "สรุป"
    ws["A1"], ws["A2"] = "ยอดขายรถสาย สรุป", f"งวด {title} · วันสำรองขับไม่นับยอด (คนขับสำรองไม่ได้ค่าคอม)"
    ws["A1"].font, ws["A2"].font = head_font, body
    header = ["คนขับ", "รถ", "วันที่ขับ", "หยุด", "สำรองขับรถคันนี้", "ไปเป็นสำรอง", *labels, "เงินที่ได้ (บาท)"]
    data = [[f"{x['name']}" + (f" ({x['nickname']})" if x["nickname"] else ""), (f"#{x['truck']}" + (f" {x['branch']}" if x.get("branch") and x["branch"] != BRANCH else "")) if x["truck"] else ("หน้าโรง" if x["kind"] == "หน้าโรง" else ""),
             x["days_worked"], x["days_off"], x["spare_days"], x["as_spare_days"], *[x["qty"][k] for k in keys], x["money"]] for x in summary]
    tot = ["รวม", "", sum(x["days_worked"] for x in summary), "", sum(x["spare_days"] for x in summary), sum(x["as_spare_days"] for x in summary),
           *[sum(x["qty"][k] for x in summary) for k in keys], round(sum(x["money"] for x in summary), 2)]
    table(ws, 4, header, data, [30, 9, 10, 8, 14, 12, *[10] * len(keys), 16], money_col=len(header), total=tot)
    ws.freeze_panes = "B5"

    split = split_ids()
    by: dict = {}
    for r in rows:
        by.setdefault(r["employee_id"], {})[r["day"]] = r
    used = set()
    dows = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
    as_spare: dict = {}
    for r in rows:
        if r.get("spare") and r.get("spare_by"):
            as_spare.setdefault(r["spare_by"], {})[r["day"]] = r
    for x in summary:
        mine = by.get(x["id"], {})
        if not mine and not x["truck"] and not x["as_spare_days"]:
            continue
        name = (x["nickname"] or x["name"]).strip()
        t = re.sub(r"[\\/*?:\[\]]", "-", f"{('#' + str(x['truck']) + ' ') if x['truck'] else ''}{name}")[:31]
        while t in used:
            t = t[:28] + "_" + str(len(used))
        used.add(t)
        sh = wb.create_sheet(t)
        sh["A1"] = f"{x['name']}" + (f" ({x['nickname']})" if x["nickname"] else "") + (f"  รถ #{x['truck']}" if x["truck"] else "")
        sh["A2"] = (f"งวด {title} · ขับ {x['days_worked']} วัน · หยุด {x['days_off']} วัน (สำรองขับรถคันนี้ {x['spare_days']})"
                    + (f" · ไปเป็นสำรอง {x['as_spare_days']} วัน" if x["as_spare_days"] else ""))
        sh["A1"].font, sh["A2"].font = head_font, body
        data, faint = [], []
        two = x["id"] in split                                   # ยอดขายซอง / ยอดขายหลอด (บาท) columns
        pcol = lambda r: [((r.get("money_parts") or {}).get(k) if r else None) for k, _ in PARTS] if two else []  # noqa: E731
        for i, d in enumerate(days):
            r = mine.get(d)
            label = f"{dows[_date.fromisoformat(d).weekday()]} {be(d)}"
            other = as_spare.get(x["id"], {}).get(d)
            if r and r.get("spare"):
                faint.append(i)
                who = names.get(r.get("spare_by")) or {}
                data.append([label, *[(r.get("qty") or {}).get(k) for k in keys], *pcol(r), float(r["money"]) if r.get("money") is not None else None,
                             f"สำรองขับ{(': ' + (who.get('nickname') or who.get('name') or '')) if who else ''} — ไม่นับ"])
            elif other and not (r and (r.get("qty") or r.get("money") is not None)):
                faint.append(i)
                owner = names.get(other["employee_id"]) or {}
                data.append([label, *[None] * len(keys), *pcol(None), None,
                             f"เป็นสำรองขับ {('#' + str(owner['truck'])) if owner.get('truck') else ''} {owner.get('nickname') or owner.get('name') or ''}".strip()])
            elif r and (r.get("qty") or r.get("money") is not None):
                data.append([label, *[(r.get("qty") or {}).get(k) for k in keys], *pcol(r), float(r["money"]) if r.get("money") is not None else None, ""])
            else:
                data.append([label, *[None] * len(keys), *pcol(None), None, "หยุด"])
        part_sums = [round(sum(float((r.get("money_parts") or {}).get(k) or 0) for r in mine.values() if not r.get("spare")), 2) for k, _ in PARTS] if two else []
        n_parts = len(part_sums)
        table(sh, 4, ["วันที่", *labels, *([lab for _, lab in PARTS] if two else []), "เงินที่ได้ (บาท)", "หมายเหตุ"], data,
              [16, *[10] * len(keys), *[16] * n_parts, 16, 20], money_col={len(keys) + 2 + i for i in range(n_parts + 1)},
              total=["รวม (ไม่รวมสำรองขับ)", *[x["qty"][k] for k in keys], *part_sums, x["money"], ""], faint=faint)
        sh.freeze_panes = "B5"
    buf = io.BytesIO()
    wb.save(buf)
    label = {0: "ทั้งเดือน", 1: "งวด 1", 2: "งวด 2"}[half]
    return buf.getvalue(), f"ยอดขายรถสาย {month} {label}.xlsx"
