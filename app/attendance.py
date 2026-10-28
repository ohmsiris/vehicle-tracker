"""
เวลาทำงาน -- the fingerprint machine's report turned into shifts, and the owner's decision on every day that needs one
(attendance_punches / attendance_reviews / attendance_uploads, schema_v26.sql).

The machine exports an .xls whose sheet "รายงานบันทึกเวลา" holds, per person, the scans of each day of a pay period (the 26th to the 25th). Only the
scans are read from it. Which scans belong together is worked out here, from the scans alone, because people move between day and night shifts:
a night shift is a scan one evening and one next morning, so the scans of a person are laid in a row and paired up (pair_shifts) whatever the date.
A shift belongs to the day it STARTED. A scan that finds no partner is a single scan (สแกนขาเดียว); the owner decides what each one is worth.

What is flagged for the owner (review_flag):
  single  a scan with no partner                         -- every role
  short   worked less than the shift minus 10 minutes    -- ผลิต only (the one role with a fixed shift); never an automatic pay cut
The shift is 12 hours at Saraburi and 9 at Kaeng Khoi (where 3 hours of OT follow). Everyone else (ช่าง, drivers, clerks ...) has free hours: their hours are shown, and only single scans are flagged.
The owner's choices (DECISIONS): อนุมัติ / ค่าแรงครึ่งวัน / ค่าแรงเต็ม + ใบเตือน + หักเงิน / ไม่จ่าย. The first FREE_SINGLES single scans of a month come with a
suggestion of ค่าแรงครึ่งวัน; from the next one on there is no suggestion -- the owner decides. Nothing is ever decided automatically: a suggestion
becomes a decision only when it is accepted (one at a time, or with "ใช้ตามข้อเสนอทั้งหมด").

Pure rules (parse_report, pair_shifts, build_month ...) are unit-tested; the database calls are thin. Same HR_PIN lock as พนักงาน.
"""
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from app import employees, supabase

DECISIONS = {"approve": "อนุมัติ", "half": "ค่าแรงครึ่งวัน", "full_penalty": "ค่าแรงเต็ม + ใบเตือน + หักเงิน", "unpaid": "ไม่จ่าย"}
FRACTION = {"approve": 1.0, "half": 0.5, "full_penalty": 1.0, "unpaid": 0.0}      # how much of a day's wage each decision pays
FREE_HOURS = ("เด็กรถ", "พนักงานขับรถ")       # irregular hours: no shift time can be set, nothing is checked for length, no overtime
FIXED_POSITIONS = {"ผลิต"}                         # the roles with a fixed shift: their short days are flagged
OT_BRANCH = "แก่งคอย"                                  # the branch whose ผลิต are paid overtime for what they work past the shift
SHIFT_MINUTES = {"สระบุรี": 12 * 60, "แก่งคอย": 9 * 60}   # a ผลิต shift by branch: Kaeng Khoi's 9 hours are followed by 3 hours of OT (paid apart), Saraburi works 12 with none
DEFAULT_SHIFT = 12 * 60
LATE_GRACE = 10                                    # minutes of the shift that may be missing before a day is "short"
FREE_SINGLES = 3                                   # single scans a month that come with the "half a day" suggestion
DUP_GAP = timedelta(minutes=30)                    # a second scan this soon after the first is the same scan pressed twice
MAX_SHIFT = timedelta(hours=19)                    # two scans further apart than this are not one shift (a long day, e.g. 03:45 to 20:45, is possible)
MAX_UPLOAD = 8 * 1024 * 1024
SHEET = "รายงานบันทึกเวลา"
NEEDS_V27 = "ยังตั้งเวลาทำงานรายคนไม่ได้ — ต้องรันไฟล์ schema_v27.sql ใน Supabase ก่อน"
CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
NEEDS_V26 = "ยังใช้เวลาทำงานไม่ได้ — ต้องรันไฟล์ schema_v26.sql ใน Supabase ก่อน"
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})")
TIME_RE = re.compile(r"(\d{2}):(\d{2})")

employees.REFERENCED_BY.append(("attendance_punches", "employee_id"))       # an employee with scans or decisions can only be "ลาออก", never deleted
employees.REFERENCED_BY.append(("attendance_reviews", "employee_id"))


class InvalidAttendance(employees.InvalidEmployee):
    """User-fixable problem; str() is a Thai message safe to show."""


# ------------------------------------------------------------------ pay months
def pay_month_of(day: date) -> str:
    """The pay month a date falls in, named by the month of its 25th: 26 Aug .. 25 Sep is "2026-09"."""
    y, m = (day.year + (day.month == 12), day.month % 12 + 1) if day.day >= 26 else (day.year, day.month)
    return f"{y:04d}-{m:02d}"


def parse_month(value) -> str:
    m = MONTH_RE.match(str(value or "").strip())
    if not m or not 1 <= int(m.group(2)) <= 12 or not 1950 <= int(m.group(1)) < 2400:
        raise InvalidAttendance("เดือนไม่ถูกต้อง (ใช้ ค.ศ. เช่น 2026-09)")
    return f"{m.group(1)}-{m.group(2)}"


def month_range(month: str) -> tuple[date, date]:
    """'2026-09' -> (2026-08-26, 2026-09-25)."""
    y, m = int(month[:4]), int(month[5:7])
    py, pm = (y, m - 1) if m > 1 else (y - 1, 12)
    return date(py, pm, 26), date(y, m, 25)


# ------------------------------------------------------------------ reading the machine's report
def parse_report(data: bytes) -> dict:
    """{start, end, people: [{code, name, punches: [datetime]}]} from the machine's .xls. Raises InvalidAttendance in Thai."""
    try:
        import xlrd
    except ImportError:                                                  # pragma: no cover
        raise InvalidAttendance("เซิร์ฟเวอร์ยังไม่มีตัวอ่านไฟล์ .xls (xlrd)")
    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception:
        raise InvalidAttendance("อ่านไฟล์ไม่ได้ — ต้องเป็นไฟล์ .xls ที่ export จากเครื่องสแกนนิ้ว")
    if SHEET not in book.sheet_names():
        raise InvalidAttendance(f"ไม่พบแผ่นงาน “{SHEET}” — เลือกไฟล์รายงานจากเครื่องสแกนนิ้วให้ถูกต้อง")
    sheet = book.sheet_by_name(SHEET)
    period = None
    for r in range(min(6, sheet.nrows)):
        for c in range(min(sheet.ncols, 16)):
            m = PERIOD_RE.search(str(sheet.cell_value(r, c)))
            if m:
                period = (date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2)))
                break
        if period:
            break
    if not period or period[1] < period[0] or (period[1] - period[0]).days > 40:
        raise InvalidAttendance("ไม่พบช่วงวันที่ของรายงาน")
    start, end = period
    days = (end - start).days + 1
    people, code, name = [], None, ""
    for r in range(sheet.nrows):
        row = [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
        if row[0] == "ID:":
            code, name = row[2].upper(), (row[10] if len(row) > 10 else "")
            continue
        if code:
            punches = []
            for i, cell in enumerate(row[:days]):
                day = start + timedelta(days=i)
                for hh, mm in TIME_RE.findall(cell):
                    if int(hh) < 24 and int(mm) < 60:
                        punches.append(datetime(day.year, day.month, day.day, int(hh), int(mm)))
            people.append({"code": code, "name": name, "punches": punches})
            code = None
    if not people:
        raise InvalidAttendance("ไม่พบข้อมูลการสแกนในไฟล์")
    return {"start": start, "end": end, "people": people}


# ------------------------------------------------------------------ shifts
def pair_shifts(punches: list[datetime], target_minutes: int = DEFAULT_SHIFT) -> list[dict]:
    """Lay a person's scans in a row and pair them into shifts: [{in, out}] with out None for a single scan.

    A second scan within DUP_GAP of the one before is the same scan twice and is dropped. Then the pairing leaves the FEWEST scans alone
    (two scans more than MAX_SHIFT apart are never a pair); among equal ones it prefers shifts closest to `target_minutes` -- so one stray scan
    in the evening does not drag every shift after it out of step."""
    kept: list[datetime] = []
    for p in sorted(set(punches)):
        if not kept or p - kept[-1] >= DUP_GAP:
            kept.append(p)
    n = len(kept)
    best: list[Optional[tuple]] = [None] * (n + 2)        # best[i] = (singles, deviation, pairs_here) for the scans from i on
    best[n] = best[n + 1] = (0, 0, False)
    for i in range(n - 1, -1, -1):
        single = (best[i + 1][0] + 1, best[i + 1][1], False)
        choice = single
        if i + 1 < n and kept[i + 1] - kept[i] <= MAX_SHIFT:
            gap = (kept[i + 1] - kept[i]).total_seconds() / 60
            paired = (best[i + 2][0], best[i + 2][1] + abs(gap - target_minutes), True)
            if paired[:2] < single[:2]:
                choice = paired
        best[i] = choice
    out, i = [], 0
    while i < n:
        if best[i][2]:
            out.append({"in": kept[i], "out": kept[i + 1]})
            i += 2
        else:
            out.append({"in": kept[i], "out": None})
            i += 1
    return out


def is_free_hours(position) -> bool:
    return any(str(position or "").startswith(p) for p in FREE_HOURS)


def minutes_of(shift: dict) -> int:
    return int((shift["out"] - shift["in"]).total_seconds() // 60) if shift["out"] else 0


def expected_minutes(branch: Optional[str]) -> int:
    return SHIFT_MINUTES.get(branch or "", DEFAULT_SHIFT)


def parse_clock(value) -> Optional[int]:
    """'08:00' (or '08:00:00') -> minutes after midnight; None for blank. Raises InvalidAttendance in Thai."""
    text = str(value or "").strip()
    if not text:
        return None
    m = CLOCK_RE.match(text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise InvalidAttendance("เวลาไม่ถูกต้อง (เช่น 08:00)")
    return int(m.group(1)) * 60 + int(m.group(2))


def clock_text(minutes: Optional[int]) -> Optional[str]:
    return None if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def schedule_minutes(start, end) -> Optional[int]:
    """The length of a worker's own shift (the end may be past midnight); None when either time is not set."""
    a, b = parse_clock(start), parse_clock(end)
    if a is None or b is None or a == b:
        return None
    return (b - a) % 1440


def review_flag(shifts: list[dict], fixed: bool, expected: int = DEFAULT_SHIFT) -> str:
    """'single' / 'short' / 'ok' for the shifts that started on one day."""
    if any(s["out"] is None for s in shifts):
        return "single"
    if fixed and sum(minutes_of(s) for s in shifts) < expected - LATE_GRACE:
        return "short"
    return "ok"


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _shift_card(s: dict) -> dict:
    return {"in": _hhmm(s["in"]), "out": _hhmm(s["out"]) if s["out"] else None, "next_day": bool(s["out"] and s["out"].date() != s["in"].date()), "minutes": minutes_of(s)}


def review_card(r: dict) -> dict:
    return {"decision": r["decision"], "label": DECISIONS.get(r["decision"], ""), "deduct_amount": float(r.get("deduct_amount") or 0), "note": r.get("note") or "",
            "manual": bool(r.get("manual"))}


def build_month(month: str, emps: dict[str, dict], punches: dict[str, list[datetime]], reviews: list[dict]) -> dict:
    """The review screen's data: per person the days of the pay month, each with its shifts, flag, suggestion and the decision so far."""
    start, end = month_range(month)
    by_key = {(r["employee_id"], str(r["work_date"])[:10]): r for r in reviews}
    people, skipped_hq = [], 0
    for eid in sorted(set(punches) | {r["employee_id"] for r in reviews}):
        e = emps.get(eid)
        if not e:
            continue
        if (e.get("pay_group") or "company") == "hq":
            skipped_hq += 1
            continue
        free = is_free_hours(e.get("position"))
        custom = None if free else schedule_minutes(e.get("shift_start"), e.get("shift_end"))      # the worker's own shift time, an exception to the default
        fixed = (e.get("position") or "") in FIXED_POSITIONS or custom is not None
        expected = custom or expected_minutes(e.get("branch"))
        ot_applies = e.get("branch") == OT_BRANCH and (e.get("position") or "") in FIXED_POSITIONS      # Kaeng Khoi ผลิต: what is worked past the shift is OT
        by_day: dict[date, list[dict]] = {}
        for s in pair_shifts(punches.get(eid, []), target_minutes=expected if fixed else 9 * 60):
            if start <= s["in"].date() <= end:
                by_day.setdefault(s["in"].date(), []).append(s)
        for key, r in by_key.items():
            if key[0] == eid and r.get("manual") and start <= date.fromisoformat(key[1]) <= end:
                by_day.setdefault(date.fromisoformat(key[1]), [])
        days, singles = [], 0
        for day in sorted(by_day):
            shifts = by_day[day]
            flag = review_flag(shifts, fixed, expected) if shifts else "manual"
            if flag == "single":
                singles += 1
            r = by_key.get((eid, day.isoformat()))
            ot = sum(minutes_of(s) - expected for s in shifts if s["out"] and minutes_of(s) - expected > LATE_GRACE) if ot_applies else 0
            days.append({"date": day.isoformat(), "shifts": [_shift_card(s) for s in shifts], "flag": flag, "minutes": sum(minutes_of(s) for s in shifts), "ot_minutes": ot,
                         "single_no": singles if flag == "single" else None, "suggest": "half" if flag == "single" and singles <= FREE_SINGLES else None,
                         "review": review_card(r) if r else None})
        if not days:
            continue
        flagged = [d for d in days if d["flag"] in ("single", "short")]
        people.append({"employee_id": eid, "code": e["code"], "name": employees.display_name(e.get("prefix"), e["full_name"]), "position": e.get("position") or "",
                       "branch": e.get("branch") or "", "status": e.get("status") or "active", "fixed": fixed, "free": free, "expected": expected, "ot_applies": ot_applies,
                       "schedule": {"start": clock_text(parse_clock(e.get("shift_start"))), "end": clock_text(parse_clock(e.get("shift_end")))} if custom is not None else None,
                       "counts": {"days": len(days), "single": sum(d["flag"] == "single" for d in days), "short": sum(d["flag"] == "short" for d in days),
                                  "open": sum(d["review"] is None for d in flagged), "decided": sum(d["review"] is not None for d in flagged),
                                  "minutes": sum(d["minutes"] for d in days), "ot_minutes": sum(d["ot_minutes"] for d in days)},
                       "deduct": round(sum(d["review"]["deduct_amount"] for d in days if d["review"]), 2), "days": days})
    people.sort(key=lambda p: (p["branch"], p["position"], p["code"]))
    totals = {"people": len(people), "days": sum(p["counts"]["days"] for p in people), "single": sum(p["counts"]["single"] for p in people),
              "short": sum(p["counts"]["short"] for p in people), "open": sum(p["counts"]["open"] for p in people),
              "decided": sum(p["counts"]["decided"] for p in people), "ot_minutes": sum(p["counts"]["ot_minutes"] for p in people), "suggested": sum(1 for p in people for d in p["days"] if d["suggest"] and not d["review"])}
    return {"month": month, "start": start.isoformat(), "end": end.isoformat(), "people": people, "totals": totals, "hq_skipped": skipped_hq}


# ------------------------------------------------------------------ a decision
def clean_review(data: dict, *, today: date) -> dict:
    """{employee_id, work_date, decision, deduct_amount, note, manual}; an empty decision means "take the decision back". Raises InvalidAttendance."""
    try:
        eid = str(uuid.UUID(str(data.get("employee_id") or "")))
    except ValueError:
        raise InvalidAttendance("ไม่พบพนักงาน")
    try:
        day = date.fromisoformat(str(data.get("work_date") or "")[:10])
    except ValueError:
        raise InvalidAttendance("วันที่ไม่ถูกต้อง")
    if day.year < 2000 or day.year >= 2400 or day > today + timedelta(days=2):
        raise InvalidAttendance("วันที่ไม่ถูกต้อง (ปีต้องเป็น ค.ศ. และไม่เกินวันนี้)")
    decision = str(data.get("decision") or "").strip()
    if decision and decision not in DECISIONS:
        raise InvalidAttendance("เลือกการตัดสินให้ถูกต้อง")
    amount = 0.0
    if decision == "full_penalty":
        try:
            amount = round(float(str(data.get("deduct_amount") or 0).replace(",", "").strip()), 2)
        except ValueError:
            raise InvalidAttendance("จำนวนเงินที่หักไม่ถูกต้อง")
        if not 0 <= amount <= 100000:
            raise InvalidAttendance("จำนวนเงินที่หักต้องไม่ติดลบและไม่เกิน 100,000")
    note = " ".join(str(data.get("note") or "").split())[:300]
    return {"employee_id": eid, "work_date": day.isoformat(), "decision": decision, "deduct_amount": amount, "note": note or None, "manual": bool(data.get("manual"))}


# ------------------------------------------------------------------ the database
_probe = {"at": 0.0, "ok": False}


def has_v26() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table in ("attendance_punches", "attendance_reviews", "attendance_uploads"):
            supabase.select(table, {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


_EMP_COLS = "id,code,prefix,full_name,position,branch,status,pay_group"
_probe27 = {"at": 0.0, "ok": False}


def has_v27() -> bool:
    now = time.time()
    if now - _probe27["at"] < 30:
        return _probe27["ok"]
    try:
        supabase.select("employees", {"select": "shift_start,shift_end", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe27.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not (employees.has_v21() and has_v26()):
        raise InvalidAttendance(NEEDS_V26)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def import_report(data: bytes, filename: str) -> dict:
    """Read an uploaded report and add its scans (scans already stored are skipped, so the same file can be uploaded twice)."""
    _require()
    if len(data) > MAX_UPLOAD:
        raise InvalidAttendance("ไฟล์ใหญ่เกินไป")
    report = parse_report(data)
    by_code = {e["code"].upper(): e for e in supabase.select_all("employees", {"select": "id,code,full_name", "order": "code.asc"})}
    rows, people, unknown = [], 0, []
    for p in report["people"]:
        if not p["punches"]:                                             # the machine lists everyone ever enrolled: most have no scans
            continue
        e = by_code.get(p["code"])
        if not e:
            unknown.append(f"{p['code']} {p['name']}".strip())
            continue
        people += 1
        rows.extend({"employee_id": e["id"], "punched_at": _stamp(t), "source": (filename or "")[:200] or None} for t in p["punches"])
    added = 0
    for i in range(0, len(rows), 500):
        added += len(supabase.insert("attendance_punches", rows[i:i + 500], on_conflict="employee_id,punched_at", ignore_duplicates=True))
    supabase.insert("attendance_uploads", [{"filename": (filename or "")[:200] or None, "period_start": report["start"].isoformat(), "period_end": report["end"].isoformat(),
                                            "people": people, "punches_read": len(rows), "punches_added": added, "unknown_codes": ", ".join(unknown)[:1000] or None}])
    return {"period_start": report["start"].isoformat(), "period_end": report["end"].isoformat(), "month": pay_month_of(report["end"]), "people": people,
            "punches_read": len(rows), "punches_added": added, "unknown": unknown}


def _load_month(month: str) -> dict:
    start, end = month_range(month)
    window = {"and": f"(punched_at.gte.{start - timedelta(days=1)}T00:00:00,punched_at.lt.{end + timedelta(days=3)}T00:00:00)"}
    cols = _EMP_COLS + (",shift_start,shift_end" if has_v27() else "")
    rows, reviews, emp_rows = supabase.parallel(
        lambda: supabase.select_all_parallel("attendance_punches", {"select": "employee_id,punched_at", **window, "order": "employee_id.asc,punched_at.asc"}),
        lambda: supabase.select_all("attendance_reviews", {"select": "*", "work_date": f"gte.{start}", "and": f"(work_date.lte.{end})", "order": "id.asc"}),
        lambda: supabase.select_all("employees", {"select": cols, "order": "code.asc"}))
    punches: dict[str, list[datetime]] = {}
    for r in rows:
        punches.setdefault(r["employee_id"], []).append(datetime.fromisoformat(str(r["punched_at"])[:19]))
    emps = {e["id"]: e for e in emp_rows}
    return build_month(month, emps, punches, reviews)


def default_month() -> str:
    latest = supabase.select("attendance_punches", {"select": "punched_at", "order": "punched_at.desc", "limit": "1"})
    if latest:
        return pay_month_of(datetime.fromisoformat(str(latest[0]["punched_at"])[:19]).date())
    current = pay_month_of(employees.today_th())                       # nothing uploaded yet: the pay month that has just ended
    y, m = int(current[:4]), int(current[5:7])
    return f"{y - (m == 1):04d}-{(m - 2) % 12 + 1:02d}"


def get_month(month: str = "") -> dict:
    if not (employees.has_v21() and has_v26()):
        return {"ready": False}
    month = parse_month(month) if month else default_month()
    uploads = supabase.select("attendance_uploads", {"select": "*", "order": "created_at.desc", "limit": "12"})
    out = _load_month(month)
    out["uploads"] = [{"filename": u.get("filename") or "", "period_start": u["period_start"], "period_end": u["period_end"], "people": u["people"],
                       "punches_added": u["punches_added"], "unknown": u.get("unknown_codes") or "", "at": u["created_at"]} for u in uploads]
    out["decisions"] = DECISIONS
    out["free_singles"] = FREE_SINGLES
    out["ready"] = True
    return out


def set_review(data: dict) -> dict:
    """Save (or, with an empty decision, take back) the decision for one person's work day."""
    _require()
    values = clean_review(data, today=employees.today_th())
    emp = employees._get(values["employee_id"])
    match = {"employee_id": f"eq.{emp['id']}", "work_date": f"eq.{values['work_date']}"}
    if not values["decision"]:
        supabase.delete("attendance_reviews", match)
        return {"review": None}
    row = {**values, "decided_at": datetime.now(employees.BANGKOK).isoformat()}
    supabase.insert("attendance_reviews", [row], on_conflict="employee_id,work_date")
    return {"review": review_card(row)}


def apply_suggestions(month: str) -> dict:
    """Turn every open suggestion of the month into a decision (ค่าแรงครึ่งวัน for the first single scans)."""
    _require()
    month = parse_month(month)
    data = _load_month(month)
    now = datetime.now(employees.BANGKOK).isoformat()
    rows = [{"employee_id": p["employee_id"], "work_date": d["date"], "decision": d["suggest"], "deduct_amount": 0, "note": f"สแกนขาเดียวครั้งที่ {d['single_no']} ของเดือน",
             "manual": False, "decided_at": now} for p in data["people"] for d in p["days"] if d["suggest"] and not d["review"]]
    for i in range(0, len(rows), 200):
        supabase.insert("attendance_reviews", rows[i:i + 200], on_conflict="employee_id,work_date")
    return {"applied": len(rows)}


# ------------------------------------------------------------------ each worker's own shift time (settings)
def list_schedules() -> dict:
    """Everyone paid by us (not resigned, not paid by HQ) with the default shift of his branch and position and his own times, if set."""
    _require()
    ready = has_v27()
    rows = supabase.select_all("employees", {"select": _EMP_COLS + (",shift_start,shift_end" if ready else ""), "status": "eq.active", "order": "code.asc"})
    people = []
    for e in rows:
        if (e.get("pay_group") or "company") == "hq" or is_free_hours(e.get("position")):
            continue
        fixed = (e.get("position") or "") in FIXED_POSITIONS
        own = schedule_minutes(e.get("shift_start"), e.get("shift_end"))
        people.append({"employee_id": e["id"], "code": e["code"], "name": employees.display_name(e.get("prefix"), e["full_name"]), "position": e.get("position") or "",
                       "branch": e.get("branch") or "", "default_minutes": expected_minutes(e.get("branch")) if fixed else None,
                       "start": clock_text(parse_clock(e.get("shift_start"))) if own is not None else None, "end": clock_text(parse_clock(e.get("shift_end"))) if own is not None else None,
                       "minutes": own})
    return {"ready": ready, "people": people}


def set_schedule(data: dict) -> dict:
    """Set one worker's own shift time (both times), or clear it (both empty) to go back to the default."""
    _require()
    if not has_v27():
        raise InvalidAttendance(NEEDS_V27)
    try:
        eid = str(uuid.UUID(str(data.get("employee_id") or "")))
    except ValueError:
        raise InvalidAttendance("ไม่พบพนักงาน")
    start, end = parse_clock(data.get("start")), parse_clock(data.get("end"))
    if (start is None) != (end is None):
        raise InvalidAttendance("ใส่เวลาเริ่มและเวลาเลิกให้ครบทั้งสองช่อง หรือเว้นว่างทั้งคู่")
    if start is not None and start == end:
        raise InvalidAttendance("เวลาเริ่มและเวลาเลิกต้องไม่เป็นเวลาเดียวกัน")
    emp = employees._get(eid)
    if start is not None and is_free_hours(emp.get("position")):
        raise InvalidAttendance("เด็กรถและพนักงานขับรถทำงานเวลาไม่แน่นอน — ตั้งเวลากะไม่ได้ (ไม่เช็คทำงานไม่ครบเวลา และไม่มี OT)")
    supabase.update("employees", {"id": f"eq.{emp['id']}"}, {"shift_start": clock_text(start), "shift_end": clock_text(end)})
    return {"employee_id": emp["id"], "start": clock_text(start), "end": clock_text(end), "minutes": schedule_minutes(clock_text(start), clock_text(end))}
