"""
Recurring tickets for the manager's board: "every Monday and Thursday", "on the 1st and 15th of the month", "every 3
months", "on the last day of every month", "every year on 15 Jan" ...

A rule is a small dict (stored as JSON):

    {"every": 1, "unit": "day"}
    {"every": 1, "unit": "week",  "weekdays": [0, 3]}                 Monday = 0 ... Sunday = 6
    {"every": 1, "unit": "month", "month_days": [1, 15]}              1..31, or "last" for the last day of the month
    {"every": 1, "unit": "month", "nth": {"n": 1, "weekday": 0}}      the first Monday (n = 1..4, or -1 for the last)
    {"every": 1, "unit": "year",  "month": 1, "day": 15}

`every` skips: every 2 weeks, every 3 months. Everything here is pure (dates in, dates out) so it is unit-tested;
app/mgr.py does the database side. A day that does not exist in a month (the 31st of April) falls on the month's last day.
"""
import calendar
from datetime import date, timedelta
from typing import Optional

UNITS = ("day", "week", "month", "year")
WEEKDAYS_TH = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
MONTHS_TH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
MAX_EVERY = 366
CATCH_UP_DAYS = 3                    # an occurrence older than this when nobody was looking is skipped, not created late
MAX_PER_RUN = 12


class InvalidRule(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


def _int(value, what: str, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise InvalidRule(f"{what}ต้องเป็นตัวเลข")
    if not low <= n <= high:
        raise InvalidRule(f"{what}ต้องอยู่ระหว่าง {low}-{high}")
    return n


def clean_rule(raw) -> dict:
    """A rule as typed in the form -> the normalised rule, or InvalidRule."""
    if not isinstance(raw, dict):
        raise InvalidRule("ยังไม่ได้ตั้งความถี่")
    unit = raw.get("unit")
    if unit not in UNITS:
        raise InvalidRule("เลือกความถี่: ทุกวัน / ทุกสัปดาห์ / ทุกเดือน / ทุกปี")
    rule = {"every": _int(raw.get("every", 1), "ทุกๆ", 1, MAX_EVERY), "unit": unit}
    if unit == "week":
        days = sorted({_int(d, "วันในสัปดาห์", 0, 6) for d in (raw.get("weekdays") or [])})
        if not days:
            raise InvalidRule("เลือกวันในสัปดาห์อย่างน้อย 1 วัน")
        rule["weekdays"] = days
    elif unit == "month":
        nth = raw.get("nth")
        if nth:
            n = _int(nth.get("n"), "ลำดับของสัปดาห์", -1, 4)
            if n == 0:
                raise InvalidRule("ลำดับของสัปดาห์ต้องเป็น 1-4 หรือ -1 (ครั้งสุดท้าย)")
            rule["nth"] = {"n": n, "weekday": _int(nth.get("weekday"), "วันในสัปดาห์", 0, 6)}
        else:
            days = []
            for d in raw.get("month_days") or []:
                days.append("last" if d == "last" else _int(d, "วันที่", 1, 31))
            if not days:
                raise InvalidRule("เลือกวันที่ของเดือนอย่างน้อย 1 วัน")
            rule["month_days"] = sorted(set(days), key=lambda d: 99 if d == "last" else d)
    elif unit == "year":
        rule["month"] = _int(raw.get("month"), "เดือน", 1, 12)
        rule["day"] = _int(raw.get("day"), "วันที่", 1, 31)
    return rule


def _month_dates(year: int, month: int, rule: dict) -> list[date]:
    last = calendar.monthrange(year, month)[1]
    if rule.get("nth"):
        wd, n = rule["nth"]["weekday"], rule["nth"]["n"]
        days = [d for d in range(1, last + 1) if date(year, month, d).weekday() == wd]
        return [date(year, month, days[n - 1 if n > 0 else -1])] if len(days) >= abs(n) else []
    return sorted({date(year, month, last if d == "last" else min(d, last)) for d in rule["month_days"]})


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def next_after(rule: dict, after: date, anchor: Optional[date] = None) -> date:
    """The first occurrence strictly after `after`. `anchor` fixes the grid for "every 2 weeks / 3 months" (default: `after`)."""
    anchor = anchor or after
    every, unit = rule["every"], rule["unit"]
    if unit == "day":
        if after < anchor:
            return anchor
        return anchor + timedelta(days=((after - anchor).days // every + 1) * every)
    if unit == "week":
        d = after + timedelta(days=1)
        for _ in range(7 * every + 7):
            weeks = (_monday(d) - _monday(anchor)).days // 7
            if d.weekday() in rule["weekdays"] and weeks % every == 0 and weeks >= 0:
                return d
            d += timedelta(days=1)
        raise InvalidRule("หาวันถัดไปไม่เจอ")
    if unit == "month":
        y, m = after.year, after.month
        for _ in range(12 * every + 2):
            months = (y - anchor.year) * 12 + (m - anchor.month)
            if months % every == 0 and months >= 0:
                for d in _month_dates(y, m, rule):
                    if d > after:
                        return d
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        raise InvalidRule("หาวันถัดไปไม่เจอ")
    y = after.year                                                     # year
    for _ in range(every + 2):
        if (y - anchor.year) % every == 0 and y >= anchor.year:
            d = date(y, rule["month"], min(rule["day"], calendar.monthrange(y, rule["month"])[1]))
            if d > after:
                return d
        y += 1
    raise InvalidRule("หาวันถัดไปไม่เจอ")


def first_on_or_after(rule: dict, start: date) -> date:
    return next_after(rule, start - timedelta(days=1), anchor=start)


def occurrences(rule: dict, start: date, until: date, limit: int = 400) -> list[date]:
    """Every occurrence from `start` to `until` inclusive (the grid is anchored on `start`)."""
    out, d = [], first_on_or_after(rule, start)
    while d <= until and len(out) < limit:
        out.append(d)
        d = next_after(rule, d, anchor=start)
    return out


def describe(rule: dict) -> str:
    """The rule in plain Thai: "ทุกสัปดาห์ วันจันทร์, วันพฤหัสบดี"."""
    every, unit = rule["every"], rule["unit"]
    if unit == "day":
        return "ทุกวัน" if every == 1 else f"ทุก {every} วัน"
    if unit == "week":
        days = ", ".join(f"วัน{WEEKDAYS_TH[d]}" for d in rule["weekdays"])
        return f"{'ทุกสัปดาห์' if every == 1 else f'ทุก {every} สัปดาห์'} {days}"
    if unit == "month":
        head = "ทุกเดือน" if every == 1 else f"ทุก {every} เดือน"
        if rule.get("nth"):
            n = rule["nth"]["n"]
            return f"{head} วัน{WEEKDAYS_TH[rule['nth']['weekday']]}{'สุดท้าย' if n == -1 else f'ที่ {n}'}ของเดือน"
        return f"{head} " + ("วันที่ " + ", ".join("สุดท้าย" if d == "last" else str(d) for d in rule["month_days"]))
    return f"{'ทุกปี' if every == 1 else f'ทุก {every} ปี'} {rule['day']} {MONTHS_TH[rule['month'] - 1]}"


def to_create(template: dict, today: date) -> tuple[list[date], Optional[date]]:
    """For one recurring template: the due dates to create tickets for now, and the template's new `next_due`.

    A date is created once its lead time has come (due - lead_days <= today). Occurrences that were missed by more than
    CATCH_UP_DAYS (the app was off, the template paused) are skipped rather than piled up. A template in "after_done" mode
    makes one ticket at a time: its `next_due` is empty while that ticket is open."""
    due = parse(template.get("next_due"))
    if not template.get("active", True) or due is None:
        return [], due
    rule, lead, end, start = template["rule"], int(template.get("lead_days") or 0), parse(template.get("end_date")), parse(template.get("start_date"))
    if template.get("mode") == "after_done":          # one at a time; the next is counted from the day this one is finished
        if (end and due > end) or due - timedelta(days=lead) > today:
            return [], due
        return [due], None
    made: list[date] = []
    for _ in range(MAX_PER_RUN * 4):
        if due is None or (end and due > end) or due - timedelta(days=lead) > today:
            break
        if due >= today - timedelta(days=CATCH_UP_DAYS):
            made.append(due)
        due = next_after(rule, due, anchor=start or due)
        if len(made) >= MAX_PER_RUN:
            break
    if end and due is not None and due > end:
        due = None
    return made, due


def next_after_done(template: dict, done_on: date) -> Optional[date]:
    """In "after_done" mode the next occurrence counts from the day the last one was finished."""
    end = parse(template.get("end_date"))
    d = next_after(template["rule"], done_on, anchor=done_on)
    return None if end and d > end else d


def parse(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None
