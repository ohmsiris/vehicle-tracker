"""
The vehicle maintenance program (แผนซ่อมบำรุงรถ): which service items each truck needs, how often, and when each
truck's next one is due -- shown per truck as an Isuzu-style chart (what the latest event did, what is overdue or
unknown, what comes in the next rounds).

Every item is "whichever comes first": a distance (km) OR a time (months). The km per item depend on the rule set
(รถเล็ก / รถใหญ่: the dealership rules, checked against the Isuzu, Toyota and Hino service guides). The months come
from the truck's GROUP (service_groups): how many months one base step of km is worth for that truck -- e.g. group S2
is "2 months per 10,000 km", so a 40,000 km item is due every 8 months. The owner sets each truck's group from how it
is used (route, factory, CP ...); it can be changed per truck and the months per group can be changed too.

How far a truck has really driven is only used when it is known (odometer readings, mileage on bills, GPS distance
entered for a period); then "km since the last service" can bring a due date forward. Without it the months decide.

When an item was last done comes from what people record (service_records) and from the confirmed repair bills -- a
line such as "กรองเครื่อง" on a bill dated 2026-10-22 counts as that truck's engine filter done that day
(classify_line). Everything that decides something is a pure function (unit-tested); the database calls are thin.
Until schema_v10.sql / schema_v11.sql have been run, reads come back empty and writes say what is missing.
"""
import calendar
import math
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app import events, logsource, mgr, supabase

SMALL, BIG = "รถเล็ก", "รถใหญ่"
RULE_SETS = [SMALL, BIG]
NEEDS_V10 = "ต้องรัน schema_v10.sql และ schema_v11.sql ใน Supabase ก่อน จึงจะใช้แผนซ่อมบำรุงได้"
NEEDS_V13 = "ต้องรัน schema_v13.sql ใน Supabase ก่อน จึงจะเลื่อนเตือนได้"
SOON_DAYS = 10                       # "due soon" = due within this many days (changeable in the settings)
DEFAULT_REMIND_ITEMS = None   # which items are reminded about: None = every item (changeable in the settings)
OVERDUE_REPEAT_DAYS = 3              # an overdue item is reminded about again every this many days
MAX_SNOOZE_DAYS = 90
MAX_AS_OF_DAYS = 400                 # how far ahead the "look ahead" slider may go
GPS_WINDOW_DAYS = 120                # GPS periods older than this no longer describe how the truck is used now
MIN_ODOMETER_SPAN = 30               # days between two odometer readings before they beat the GPS figure
MAX_KM = 3_000_000
DAYS_PER_MONTH = 30.4375
SAME_EVENT_DAYS = 3                  # items done within this many days of the latest one count as part of that event

THAI_TZ = timezone(timedelta(hours=7))


def today_th() -> date:
    return datetime.now(THAI_TZ).date()


class InvalidPlan(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


# ----------------------------------------------------------------- the plan
# key -> (label, group). The order here is the order everywhere.
ITEMS = {
    "engine_oil": ("น้ำมันเครื่อง", "เครื่องยนต์"),
    "engine_filter": ("กรองน้ำมันเครื่อง", "เครื่องยนต์"),
    "fuel_filter": ("กรองโซล่า / กรองดักน้ำ", "เครื่องยนต์"),
    "air_filter": ("กรองอากาศ", "เครื่องยนต์"),
    "coolant": ("น้ำยาหล่อเย็น", "เครื่องยนต์"),
    "valve_adjust": ("ปรับตั้งวาล์ว", "เครื่องยนต์"),
    "valve_cover_seal": ("ประเก็นฝาวาล์ว + ซีลฝาครอบหัวฉีด", "เครื่องยนต์"),
    "injector_washers": ("แหวนรองหัวฉีด", "เครื่องยนต์"),
    "gearbox_oil": ("น้ำมันเกียร์", "ช่วงล่าง / ส่งกำลัง"),
    "diff_oil": ("น้ำมันเฟืองท้าย", "ช่วงล่าง / ส่งกำลัง"),
    "brake_clutch_fluid": ("น้ำมันเบรก / คลัตช์", "ช่วงล่าง / ส่งกำลัง"),
    "power_steering": ("น้ำมันพาวเวอร์", "ช่วงล่าง / ส่งกำลัง"),
    "wheel_grease": ("จาระบีลูกปืนล้อ", "ช่วงล่าง / ส่งกำลัง"),
}

# The category each item gets when it is recorded as a repair (the names of the categories table).
ITEM_CATEGORY = {key: "ไส้กรองและของเหลว" for key in ITEMS}
ITEM_CATEGORY.update(valve_adjust="เครื่องยนต์", valve_cover_seal="เครื่องยนต์", injector_washers="เครื่องยนต์")

# rule set -> key -> km. The dealership's rules as the owner settled them (see Maintenance_Plan.xlsx):
# small = D-Max / Revo, big = NLR / NMR / NPR, 6- and 10-wheelers. A missing key means no such service for that rule set.
DEFAULT_KM = {
    SMALL: {
        "engine_oil": 10_000,
        "engine_filter": 10_000,           # dealership 20,000; changed with every oil change by choice
        "fuel_filter": 40_000,             # official 50,000; 40,000 groups it with the other 40,000 items
        "air_filter": 40_000,
        "coolant": 40_000,
        "gearbox_oil": 40_000,
        "diff_oil": 40_000,
        "brake_clutch_fluid": 40_000,
        "power_steering": 40_000,
        "wheel_grease": 30_000,
        "injector_washers": 100_000,
    },
    BIG: {
        "engine_oil": 20_000,
        "engine_filter": 20_000,
        "fuel_filter": 20_000,             # with the water trap
        "air_filter": 80_000,
        "coolant": 40_000,
        "valve_adjust": 40_000,
        "valve_cover_seal": 80_000,
        "gearbox_oil": 40_000,
        "diff_oil": 40_000,
        "brake_clutch_fluid": 40_000,
        "power_steering": 40_000,
        "wheel_grease": 60_000,
    },
}

# The groups: how many months one base step of km is worth. months_per_step None -> worked out from km_per_day.
DEFAULT_GROUPS = [
    {"code": "S2", "label": "รถเล็ก - รถสาย", "rule_set": SMALL, "step_km": 10_000, "months_per_step": 2, "km_per_day": None, "sort_order": 1},
    {"code": "S3", "label": "รถเล็ก - รถหน้าโรง", "rule_set": SMALL, "step_km": 10_000, "months_per_step": 3, "km_per_day": None, "sort_order": 2},
    {"code": "S6", "label": "รถเล็ก - นานสุดตามคู่มือ", "rule_set": SMALL, "step_km": 10_000, "months_per_step": 6, "km_per_day": None, "sort_order": 3},
    {"code": "B4", "label": "ใช้กฎรถใหญ่ - NLR", "rule_set": BIG, "step_km": 20_000, "months_per_step": 4, "km_per_day": None, "sort_order": 4},
    {"code": "B5", "label": "รถใหญ่ - รถสาย", "rule_set": BIG, "step_km": 20_000, "months_per_step": 5, "km_per_day": None, "sort_order": 5},
    {"code": "B6", "label": "รถใหญ่ - หน้าโรง / CP", "rule_set": BIG, "step_km": 20_000, "months_per_step": 6, "km_per_day": None, "sort_order": 6},
    {"code": "KK1", "label": "แก่งคอย เบอร์ 1 (บ้านนา) - ตามกม./วันของคันเอง", "rule_set": BIG, "step_km": 20_000, "months_per_step": None, "km_per_day": 242.3, "sort_order": 7},
]
GROUP_CODES = [g["code"] for g in DEFAULT_GROUPS]

# how many rounds (of one base step each) the chart looks ahead
CHART_ROUNDS = {SMALL: 10, BIG: 8}


def item_label(key: str) -> str:
    return ITEMS[key][0] if key in ITEMS else key


def group_months(group: dict) -> Optional[float]:
    """Months one base step of km is worth for this group."""
    if group.get("months_per_step"):
        return float(group["months_per_step"])
    if group.get("km_per_day"):
        return round(group["step_km"] / (float(group["km_per_day"]) * 30.4), 1)
    return None


def group_km_per_day(group: dict) -> Optional[float]:
    """The driving the group's months assume (km per day)."""
    months = group_months(group)
    return round(group["step_km"] / (months * 30.4), 1) if months else None


def merge_groups(rows: Optional[list[dict]]) -> dict:
    """code -> group. Saved rows win; a default that has no saved row is still available."""
    out = {g["code"]: dict(g) for g in DEFAULT_GROUPS}
    for r in rows or []:
        out[r["code"]] = {**out.get(r["code"], {}), **r}
    return out


def item_months(km: Optional[int], group: dict) -> Optional[float]:
    """How many months an item of `km` is worth in this group. Same arithmetic everywhere, so chart rounds line up."""
    months = group_months(group)
    if not km or not months:
        return None
    return (km / group["step_km"]) * months


def effective_plan(group: Optional[dict], overrides: Optional[list[dict]] = None) -> list[dict]:
    """The items one truck needs: its rule set's km (with any saved change) and the months its group makes of them."""
    if not group:
        return []
    base = DEFAULT_KM.get(group["rule_set"])
    if not base:
        return []
    saved = {o["item_key"]: o for o in (overrides or []) if o.get("vehicle_class") == group["rule_set"] and o.get("interval_km")}
    out = []
    for key, (label, grp) in ITEMS.items():
        if key not in base:
            continue
        km = saved[key]["interval_km"] if key in saved else base[key]
        out.append({"key": key, "label": label, "group": grp, "default_km": base[key], "interval_km": km,
                    "interval_months": item_months(km, group), "customised": key in saved})
    return out


def rule_set_items(rule_set: str, overrides: Optional[list[dict]] = None) -> list[dict]:
    """For the settings page: one rule set's items with default and saved km."""
    base = DEFAULT_KM.get(rule_set, {})
    saved = {o["item_key"]: o for o in (overrides or []) if o.get("vehicle_class") == rule_set and o.get("interval_km")}
    return [{"key": key, "label": label, "default_km": base[key], "interval_km": saved[key]["interval_km"] if key in saved else base[key],
             "customised": key in saved} for key, (label, _g) in ITEMS.items() if key in base]


# ------------------------------------------------- reading bills for history
# Matched at the START of a bill line (after an optional "ถ่าย/เปลี่ยน/อัด/ไส้" prefix) so that "สกรูแรงดันน้ำมันเครื่อง",
# "เข็มขัดรัดท่อยางน้ำมันพาวเวอร์" or "เหล็กยึดท่อกรองอากาศ" -- parts, not the service -- are left alone.
_PREFIX = r"^\s*(?:ถ่าย|เปลี่ยน|ล้าง|อัด|ไส้|ค่า)?\s*"
_LINE_RULES = [
    ("engine_filter", r"กรอง(?:น้ำมัน)?เครื่อง"),
    ("fuel_filter", r"กรอง(?:โซล่า|โซลา|ดีเซล|ดักน้ำ|น้ำมันเชื้อเพลิง|เชื้อเพลิง|น้ำมันโซล่า)"),
    ("air_filter", r"กรองอากาศ"),
    ("engine_oil", r"น้ำมันเครื่อง"),
    ("gearbox_oil", r"น้ำมันเกียร์"),
    ("diff_oil", r"(?:น้ำมัน)?เฟืองท้าย"),
    ("brake_clutch_fluid", r"น้ำมัน(?:เบรก|เบรค|คลัช|คลัทช์|คลัตช์)"),
    ("power_steering", r"น้ำมัน(?:พาวเวอร์|เพาเวอร์|พาวเวอ|เพาเวอ)"),
    ("coolant", r"น้ำยา(?:หล่อเย็น|หม้อน้ำ)"),
    ("wheel_grease", r"จา(?:ร|ระ)บี\s*(?:ลูกปืน)?ล้อ"),
    ("valve_adjust", r"(?:ปรับ)?ตั้งวาล์ว|ปรับวาล์ว"),
    ("valve_cover_seal", r"(?:ปะ|ประ)เก็นฝา(?:วาล์ว|ครอบวาล์ว)|ซีลฝาครอบหัวฉีด"),
    ("injector_washers", r"แหวนรอง(?:หัวฉีด)?|(?:ปะ|ประ)เก็นหัวฉีด"),
]
_COMPILED = [(key, re.compile(_PREFIX + pat)) for key, pat in _LINE_RULES]
_TOP_UP = re.compile(r"เติม")


def classify_line(description: Optional[str]) -> Optional[str]:
    """Which service item a bill line is, or None. A top-up ("น้ำมันเครื่อง (เติม)") is not a service."""
    text = re.sub(r"\s+", " ", str(description or "")).strip()
    if not text or _TOP_UP.search(text):
        return None
    for key, pattern in _COMPILED:
        if pattern.match(text):
            return key
    return None


def bill_hits(events: list[dict]) -> list[dict]:
    """Confirmed events (each {vehicle_id, event_date, mileage, source, bill_line_items: [{description}]}) -> one hit per
    (truck, item, day): {vehicle_id, item_key, done_date, odometer_km, source: 'bill' or 'manual', event_id}.
    An event is a bill, or a job recorded by hand (including from this programme)."""
    seen, out = set(), []
    for ev in events:
        for line in ev.get("bill_line_items") or []:
            key = classify_line(line.get("description"))
            if not key or not ev.get("vehicle_id") or not ev.get("event_date"):
                continue
            ident = (ev["vehicle_id"], key, ev["event_date"])
            if ident in seen:
                continue
            seen.add(ident)
            out.append({"vehicle_id": ev["vehicle_id"], "item_key": key, "done_date": ev["event_date"],
                        "odometer_km": ev.get("mileage"), "source": "manual" if ev.get("source") == "manual" else "bill",
                        "event_id": ev.get("id")})
    return out


# --------------------------------------------------------------- the maths
def parse_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def add_months_f(d: date, months: float) -> date:
    """Calendar months, plus the fraction of a month in days (2.7 months = 2 months and 21 days)."""
    whole = int(months)
    return add_months(d, whole) + timedelta(days=round((months - whole) * DAYS_PER_MONTH))


def estimate_km_per_day(readings: list[dict], today: date, default: Optional[float] = None) -> dict:
    """{value, source, note}: source is 'odometer', 'gps' (measured) or 'default' (what the truck's group assumes)."""
    odo = sorted((r for r in readings if r.get("kind") == "odometer" and parse_date(r.get("end_date"))), key=lambda r: r["end_date"])
    span = (parse_date(odo[-1]["end_date"]) - parse_date(odo[0]["end_date"])).days if len(odo) >= 2 else 0
    odo_value = (odo[-1]["km"] - odo[0]["km"]) / span if span > 0 and odo[-1]["km"] >= odo[0]["km"] else None
    since = today - timedelta(days=GPS_WINDOW_DAYS)
    km = days = 0
    for r in readings:
        if r.get("kind") != "gps":
            continue
        s, e = parse_date(r.get("start_date")), parse_date(r.get("end_date"))
        if s and e and e >= since:
            km += r["km"]
            days += (e - s).days + 1
    gps_value = km / days if days else None
    if odo_value is not None and (span >= MIN_ODOMETER_SPAN or gps_value is None):
        return {"value": round(odo_value, 1), "source": "odometer", "note": f"จากเลขไมล์ {len(odo)} ครั้ง ห่างกัน {span} วัน"}
    if gps_value is not None:
        return {"value": round(gps_value, 1), "source": "gps", "note": f"จาก GPS {days} วันล่าสุด"}
    return {"value": default, "source": "default" if default else "none",
            "note": "ตามที่กลุ่มของรถคันนี้กำหนด (ยังไม่มีข้อมูลระยะทางจริง)" if default else "ยังไม่มีข้อมูลระยะทาง"}


def latest_odometer(readings: list[dict], hits: list[dict]) -> Optional[dict]:
    """The most recent odometer figure for a truck: a reading, or the mileage on a bill / record."""
    points = [(parse_date(r["end_date"]), r["km"]) for r in readings if r.get("kind") == "odometer" and parse_date(r.get("end_date"))]
    points += [(parse_date(h["done_date"]), h["odometer_km"]) for h in hits if h.get("odometer_km") and parse_date(h.get("done_date"))]
    points = [p for p in points if p[0] and p[1] is not None]
    if not points:
        return None
    d, km = max(points)
    return {"date": d, "km": km}


def last_done(item_key: str, records: list[dict]) -> Optional[dict]:
    """The most recent time an item was done, from saved records and bill hits mixed. Ties favour a typed record."""
    best = None
    for r in records:
        if r.get("item_key") != item_key:
            continue
        d = parse_date(r.get("done_date"))
        if not d:
            continue
        rank = (d, 0 if r.get("source") == "bill" else 1)
        if best is None or rank > best[0]:
            best = (rank, {"date": d, "odometer_km": r.get("odometer_km"), "source": r.get("source") or "manual", "id": r.get("id"), "event_id": r.get("event_id")})
    return best[1] if best else None


def item_status(item: dict, last: Optional[dict], kmpd: Optional[float], odo_now: Optional[dict], today: date,
                soon_days: int = SOON_DAYS, est_kmpd: Optional[float] = None) -> dict:
    """One item of one truck: state ('overdue' / 'soon' / 'ok' / 'unknown'), when it falls due and by which limit.
    `kmpd` is the MEASURED km per day (None when nothing is measured: then only the months decide); `est_kmpd` (what the
    truck's group assumes) is only used for the "about N km left" shown beside the real figures.
    `progress` is how much of the interval is used up (0 = just done, 1 = due, above 1 = overdue)."""
    out = {"key": item["key"], "label": item["label"], "group": item["group"], "interval_km": item.get("interval_km"),
           "interval_months": item.get("interval_months"), "customised": item.get("customised", False),
           "last_date": None, "last_source": None, "last_odometer_km": None, "km_since": None, "km_basis": None,
           "due_date": None, "due_by": None, "days_left": None, "km_left": None, "state": "unknown",
           "progress": None, "est_km_since": None, "est_km_left": None}
    if last is None:
        return out
    done = last["date"]
    days = max((today - done).days, 0)
    out.update(last_date=done.isoformat(), last_source=last["source"], last_odometer_km=last.get("odometer_km"))
    km_since = basis = None
    if odo_now and last.get("odometer_km") is not None and odo_now["date"] >= done and odo_now["km"] >= last["odometer_km"]:
        km_since, basis = odo_now["km"] - last["odometer_km"], "odometer"
        if kmpd:                                       # the reading may be a few days old: carry the distance forward
            km_since += kmpd * max((today - odo_now["date"]).days, 0)
    elif kmpd:
        km_since, basis = kmpd * days, "estimate"
    candidates, used = [], []
    if item.get("interval_months"):
        candidates.append((add_months_f(done, item["interval_months"]), "months"))
        used.append(days / (item["interval_months"] * DAYS_PER_MONTH))
    if item.get("interval_km") and km_since is not None:
        out["km_since"] = round(km_since)
        out["km_basis"] = basis
        out["km_left"] = round(item["interval_km"] - km_since)
        used.append(km_since / item["interval_km"])
        if kmpd:
            candidates.append((today + timedelta(days=math.ceil(out["km_left"] / kmpd)), "km"))
        elif out["km_left"] <= 0:
            candidates.append((today, "km"))
    elif item.get("interval_km") and est_kmpd:         # no real distance: "about", from what the group assumes
        out["est_km_since"] = round(est_kmpd * days)
        out["est_km_left"] = round(item["interval_km"] - est_kmpd * days)
    if used:
        out["progress"] = round(max(used), 3)
    if not candidates:
        out["state"] = "ok"
        return out
    due, by = min(candidates)
    out.update(due_date=due.isoformat(), due_by=by, days_left=(due - today).days)
    out["state"] = "overdue" if out["days_left"] < 0 else "soon" if out["days_left"] <= soon_days else "ok"
    return out


STATE_ORDER = {"overdue": 0, "soon": 1, "unknown": 3, "ok": 2}


def build_chart(group: dict, plan: list[dict], statuses: list[dict], anchor_odometer: Optional[int], today: date) -> Optional[dict]:
    """The Isuzu-style chart for one truck. Columns: the latest event (what it did), then the next rounds, one base
    step of km / months apart. Rows: the items, with a cell per column: 'done' (done at the latest event), 'due' (falls
    due by that round), 'overdue' (already past due -- shown in the first round), 'unknown' (never recorded).
    None when the truck has no history at all."""
    dated = [parse_date(s["last_date"]) for s in statuses if s["last_date"]]
    if not dated:
        return None
    t0 = max(dated)
    months = group_months(group)
    rounds = CHART_ROUNDS.get(group["rule_set"], 8)
    visits = [add_months_f(t0, n * months) for n in range(1, rounds + 1)]
    columns = [{"n": 0, "label": "ครั้งล่าสุด", "date": t0.isoformat(), "km": anchor_odometer}]
    for n, v in enumerate(visits, 1):
        columns.append({"n": n, "label": "ครั้งถัดไป" if n == 1 else f"{n} ครั้งถัดไป", "date": v.isoformat(),
                        "km": anchor_odometer + n * group["step_km"] if anchor_odometer is not None else None})
    rows = []
    for item, s in zip(plan, statuses):
        cells: list[Optional[str]] = [None] * (rounds + 1)
        last = parse_date(s["last_date"]) if s["last_date"] else None
        if last and (t0 - last).days <= SAME_EVENT_DAYS:
            cells[0] = "done"
        if s["state"] == "unknown":
            cells[1] = "unknown"
        elif s["due_date"] and item.get("interval_months"):
            due, first = parse_date(s["due_date"]), True
            while due <= visits[-1]:
                n = next(i for i, v in enumerate(visits, 1) if v >= due)
                if cells[n] is None:
                    cells[n] = "overdue" if first and due < today else "due"
                due, first = add_months_f(due, item["interval_months"]), False
        rows.append({"key": s["key"], "label": s["label"], "last_date": s["last_date"], "last_source": s["last_source"], "state": s["state"],
                     "due_date": s["due_date"], "interval_km": s["interval_km"], "interval_months": s["interval_months"], "cells": cells})
    return {"columns": columns, "rows": rows}


def vehicle_plan(vehicle: dict, *, group: Optional[dict], records: list[dict], readings: list[dict], overrides: list[dict],
                 today: date, with_chart: bool = False, soon_days: int = SOON_DAYS, snoozes: Optional[dict] = None) -> dict:
    """The whole programme for one truck. `records` = saved records + bill hits for THIS truck; `group` = its group;
    `snoozes` = this truck's snoozed reminders {item_key: {until, for_due}}."""
    plan = effective_plan(group, overrides)
    default_kmpd = group_km_per_day(group) if group else None
    est = estimate_km_per_day(readings, today, default_kmpd)
    odo = latest_odometer(readings, records)
    measured = est["value"] if est["source"] in ("gps", "odometer") else None
    statuses = [item_status(it, last_done(it["key"], records), measured, odo, today, soon_days, default_kmpd) for it in plan]
    for s in statuses:                                  # a snooze counts only for the due date it was made for
        s["snoozed_until"] = s["snooze_ended"] = None
        sn = (snoozes or {}).get(s["key"])
        until = parse_date(sn.get("until")) if sn else None
        if until and s["due_date"] and sn.get("for_due") == s["due_date"]:
            s["snoozed_until" if until > today else "snooze_ended"] = until.isoformat()
    items = sorted(statuses, key=lambda i: (STATE_ORDER[i["state"]], i["days_left"] if i["days_left"] is not None else 10**6))
    counts = {s: sum(1 for i in items if i["state"] == s) for s in ("overdue", "soon", "ok", "unknown")}
    worst = next((s for s in ("overdue", "soon", "ok", "unknown") if counts[s]), None)
    out = {"vehicle_id": vehicle["id"], "group": ({**group, "months": group_months(group)} if group else None), "km_per_day": est,
           "odometer": ({"date": odo["date"].isoformat(), "km": odo["km"]} if odo else None),
           "items": items, "counts": counts, "worst": worst, "has_plan": bool(plan), "soon_days": soon_days}
    if with_chart:
        anchor = None
        if statuses:
            dated = [parse_date(s["last_date"]) for s in statuses if s["last_date"]]
            if dated:
                t0 = max(dated)
                anchor = next((r["odometer_km"] for r in records if r.get("odometer_km") is not None and parse_date(r.get("done_date")) == t0), None)
        out["chart"] = build_chart(group, plan, statuses, anchor, today) if plan else None
    return out


# --------------------------------------------------------------- validation
def _int(value, what: str, *, maximum: int = MAX_KM, allow_blank: bool = True) -> Optional[int]:
    if value in (None, ""):
        if allow_blank:
            return None
        raise InvalidPlan(f"กรอก{what}ด้วย")
    try:
        n = float(str(value).replace(",", ""))
    except ValueError:
        raise InvalidPlan(f"{what}ต้องเป็นตัวเลข")
    if n < 0 or n > maximum or n != int(n):
        raise InvalidPlan(f"{what}ไม่ถูกต้อง")
    return int(n)


def _positive(value, what: str, *, maximum: float) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", ""))
    except ValueError:
        raise InvalidPlan(f"{what}ต้องเป็นตัวเลข")
    if n <= 0 or n > maximum:
        raise InvalidPlan(f"{what}ไม่ถูกต้อง")
    return int(n) if n == int(n) else round(n, 2)


def _date(value, what: str, *, today: date) -> date:
    d = None
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
        try:
            d = date.fromisoformat(value.strip())
        except ValueError:
            d = None
    if d is None:
        raise InvalidPlan(f"{what}ไม่ถูกต้อง")
    if d.year >= 2400:
        raise InvalidPlan(f"{what}เป็นปี พ.ศ. — ใส่เป็น ค.ศ. (หรือเลือกจากปฏิทิน)")
    if d.year < 2000:
        raise InvalidPlan(f"{what}ไม่ถูกต้อง")
    if d > today:
        raise InvalidPlan(f"{what}ยังไม่ถึง")
    return d


def _uuid(value, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidPlan(f"{what}ไม่ถูกต้อง")


def _note(value) -> Optional[str]:
    text = re.sub(r"\s+", " ", str(value or "")).strip()[:300]
    return text or None


def clean_records(data: dict, *, today: date) -> list[dict]:
    """One request ("these items were done on this day") -> one row per item."""
    vehicle_id = _uuid(data.get("vehicle_id"), "รถ")
    keys = data.get("items")
    if not isinstance(keys, list) or not keys:
        raise InvalidPlan("เลือกรายการที่ทำอย่างน้อย 1 อย่าง")
    unknown = [k for k in keys if k not in ITEMS]
    if unknown:
        raise InvalidPlan(f"ไม่รู้จักรายการ: {', '.join(map(str, unknown))}")
    done = _date(data.get("done_date"), "วันที่ทำ", today=today)
    source = "baseline" if data.get("source") == "baseline" else "manual"
    odo = _int(data.get("odometer_km"), "เลขไมล์", maximum=MAX_KM)
    note = _note(data.get("notes"))
    return [{"vehicle_id": vehicle_id, "item_key": k, "done_date": done.isoformat(), "odometer_km": odo, "source": source, "notes": note}
            for k in dict.fromkeys(keys)]


def clean_service(data: dict, *, today: date) -> dict:
    """"These items were done on this day" -> what the repair record needs. Items come out in plan order."""
    vehicle_id = _uuid(data.get("vehicle_id"), "รถ")
    keys = data.get("items")
    if not isinstance(keys, list) or not keys:
        raise InvalidPlan("เลือกรายการที่ทำอย่างน้อย 1 อย่าง")
    unknown = [k for k in keys if k not in ITEMS]
    if unknown:
        raise InvalidPlan(f"ไม่รู้จักรายการ: {', '.join(map(str, unknown))}")
    done = _date(data.get("done_date"), "วันที่ทำ", today=today)
    return {"vehicle_id": vehicle_id, "items": [k for k in ITEMS if k in keys], "done_date": done,
            "odometer_km": _int(data.get("odometer_km"), "เลขไมล์", maximum=MAX_KM),
            "performed_by": _note(data.get("performed_by")), "notes": _note(data.get("notes"))}


def drop_done_already(keys: list[str], vehicle_id: str, done: date, hits: list[dict]) -> list[str]:
    """Items already recorded for this truck on this day (a bill, an earlier click) are not recorded twice."""
    have = {h["item_key"] for h in hits if h.get("vehicle_id") == vehicle_id and parse_date(h.get("done_date")) == done}
    return [k for k in keys if k not in have]


def service_payload(clean: dict, keys: list[str]) -> dict:
    """The body events.save_event() takes for a job recorded by hand: one line per item, so it appears in the repair
    log like any other record and the plan reads it back (the line text is the item's own name)."""
    return {"vehicle_id": clean["vehicle_id"], "source": "manual", "attachments": [], "fixes_request_ids": [],
            "event": {"event_date": clean["done_date"].isoformat(), "mileage": clean["odometer_km"], "performed_by": clean["performed_by"],
                      "notes": clean["notes"], "summary": "บันทึกจากแผนซ่อมบำรุง: " + ", ".join(item_label(k) for k in keys)},
            "line_items": [{"description": item_label(k), "category": ITEM_CATEGORY[k]} for k in keys]}


def clean_reading(data: dict, *, today: date) -> dict:
    vehicle_id = _uuid(data.get("vehicle_id"), "รถ")
    kind = data.get("kind")
    if kind not in ("gps", "odometer"):
        raise InvalidPlan("ชนิดข้อมูลต้องเป็น GPS หรือเลขไมล์")
    end = _date(data.get("end_date"), "วันที่สิ้นสุด" if kind == "gps" else "วันที่อ่านเลขไมล์", today=today)
    km = _int(data.get("km"), "ระยะทาง" if kind == "gps" else "เลขไมล์", allow_blank=False)
    row = {"vehicle_id": vehicle_id, "kind": kind, "end_date": end.isoformat(), "km": km, "notes": _note(data.get("notes")), "start_date": None}
    if kind == "gps":
        start = _date(data.get("start_date"), "วันที่เริ่ม", today=today)
        if start > end:
            raise InvalidPlan("วันที่เริ่มต้องไม่หลังวันที่สิ้นสุด")
        if (end - start).days > 400:
            raise InvalidPlan("ช่วงเวลายาวเกินไป (ไม่เกิน 400 วัน)")
        row["start_date"] = start.isoformat()
    return row


def clean_interval(data: dict) -> dict:
    """A change to the km of one item for a rule set (the months follow from each truck's group)."""
    rule_set = data.get("vehicle_class")
    key = data.get("item_key")
    if rule_set not in RULE_SETS:
        raise InvalidPlan("ชุดกฎต้องเป็นรถเล็กหรือรถใหญ่")
    if key not in DEFAULT_KM[rule_set]:
        raise InvalidPlan("รายการนี้ไม่ได้อยู่ในแผนของรถกลุ่มนี้")
    km = _int(data.get("interval_km"), "ระยะทาง", maximum=1_000_000, allow_blank=False)
    if not km:
        raise InvalidPlan("ระยะทางต้องมากกว่า 0")
    return {"vehicle_class": rule_set, "item_key": key, "interval_km": km, "interval_months": None}


def clean_group(data: dict, *, known: dict) -> dict:
    """A change to a group: the months one base step is worth (or, for a group worked out from driving, the km/day)."""
    code = data.get("code")
    if code not in known:
        raise InvalidPlan("ไม่พบกลุ่มนี้")
    months = _positive(data.get("months_per_step"), "จำนวนเดือน", maximum=60)
    kmpd = _positive(data.get("km_per_day"), "กม./วัน", maximum=2000)
    if months is None and kmpd is None:
        raise InvalidPlan("ใส่จำนวนเดือน หรือกม./วัน อย่างใดอย่างหนึ่ง")
    return {"code": code, "months_per_step": months, "km_per_day": kmpd}


def default_group(code: str) -> Optional[dict]:
    return next((g for g in DEFAULT_GROUPS if g["code"] == code), None)


def group_customised(group: dict) -> bool:
    """True when the months / km per day differ from the built-in values (the settings page offers "ค่าเริ่มต้น")."""
    base = default_group(group["code"])
    if not base:
        return False
    num = lambda x: None if x in (None, "") else float(x)
    return num(group.get("months_per_step")) != num(base["months_per_step"]) or num(group.get("km_per_day")) != num(base["km_per_day"])


def clean_vehicle_groups(data: dict, *, known: dict) -> list[tuple[str, Optional[str]]]:
    """Several trucks moved to other groups at once (the settings page): [(vehicle_id, code or None)]."""
    changes = data.get("changes")
    if not isinstance(changes, list) or not changes:
        raise InvalidPlan("ยังไม่ได้เปลี่ยนกลุ่มของรถคันไหน")
    if len(changes) > 500:
        raise InvalidPlan("เปลี่ยนได้ครั้งละไม่เกิน 500 คัน")
    out = {}
    for c in changes:
        vehicle_id, code = clean_vehicle_group(c if isinstance(c, dict) else {}, known=known)
        out[vehicle_id] = code
    return list(out.items())


def clean_vehicle_group(data: dict, *, known: dict) -> tuple[str, Optional[str]]:
    vehicle_id = _uuid(data.get("vehicle_id"), "รถ")
    code = str(data.get("group_code") or "").strip()
    if code and code not in known:
        raise InvalidPlan("ไม่พบกลุ่มนี้")
    return vehicle_id, (code or None)


# ----------------------------------------------------------------- database
_probe = {"at": 0.0, "ok": False}


def has_v10() -> bool:
    """True once schema_v10.sql AND schema_v11.sql have been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table, col in (("service_records", "vehicle_id"), ("km_readings", "vehicle_id"), ("service_intervals", "item_key"),
                           ("service_groups", "code"), ("vehicle_plans", "vehicle_id")):
            supabase.select(table, {"select": col, "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v10():
        raise InvalidPlan(NEEDS_V10)


def options() -> dict:
    return {"ready": has_v10(), "rule_sets": RULE_SETS, "soon_days": _plan_settings()["soon_days"],
            "items": [{"key": k, "label": v[0], "group": v[1]} for k, v in ITEMS.items()]}


def _bill_events() -> list[dict]:
    return supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,mileage,source,bill_line_items(description)",
                                                      "status": "eq.confirmed", "order": "id.asc"})


def _group(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r["vehicle_id"], []).append(r)
    return out


def _groups() -> dict:
    rows = supabase.select_all("service_groups", {"select": "*", "order": "code.asc"}) if has_v10() else []
    return merge_groups(rows)


def _vehicle_groups() -> dict:
    rows = supabase.select_all("vehicle_plans", {"select": "vehicle_id,group_code", "order": "vehicle_id.asc"}) if has_v10() else []
    return {r["vehicle_id"]: r["group_code"] for r in rows}


def _vehicles(branch: str = "", vehicle_class: str = "", only: Optional[str] = None) -> list[dict]:
    params = {"select": "id,plate_number,vehicle_number,nickname,branch,vehicle_class,make,model,usage,active", "order": "id.asc"}
    if only:
        params["id"] = f"eq.{only}"
    else:
        params["active"] = "eq.true"
    if branch:
        params["branch"] = f"eq.{branch}"
    if vehicle_class:
        params["vehicle_class"] = f"eq.{vehicle_class}"
    return supabase.select_all("vehicles", params)


def overview(branch: str = "", vehicle_class: str = "", *, today: Optional[date] = None) -> dict:
    """Every active truck with its programme, the trucks that need attention first. `today` is the day to look from
    (the "look ahead" slider passes a later one: nothing is saved, it only shows what would be due then)."""
    real_today = today_th()
    today = today or real_today
    ready = has_v10()
    soon_days, snoozes = _plan_settings()["soon_days"], _snoozes()
    vehicles = _vehicles(branch, vehicle_class)
    records = _group(supabase.select_all("service_records", {"select": "*", "order": "id.asc"})) if ready else {}
    readings = _group(supabase.select_all("km_readings", {"select": "*", "order": "id.asc"})) if ready else {}
    overrides = supabase.select_all("service_intervals", {"select": "*", "order": "item_key.asc"}) if ready else []
    groups, assigned = _groups(), _vehicle_groups()
    hits = _group(bill_hits(_bill_events()))
    rows = []
    for v in vehicles:
        group = groups.get(assigned.get(v["id"]))
        plan = vehicle_plan(v, group=group, records=records.get(v["id"], []) + hits.get(v["id"], []), readings=readings.get(v["id"], []),
                            overrides=overrides, today=today, soon_days=soon_days, snoozes=snoozes.get(v["id"]))
        rows.append({**{k: v.get(k) for k in ("id", "plate_number", "vehicle_number", "nickname", "branch", "vehicle_class", "make", "model", "usage")}, **plan})

    def rank(r):
        c = r["counts"]
        known = c["overdue"] + c["soon"] + c["ok"]
        return (-c["overdue"], -c["soon"], 0 if known else 1, str(r.get("branch")), _numkey(r.get("vehicle_number")))

    rows.sort(key=rank)
    totals = {s: sum(r["counts"][s] for r in rows) for s in ("overdue", "soon", "ok", "unknown")}
    return {"as_of": today.isoformat(), "simulated": today != real_today, "soon_days": soon_days, "ready": ready, "vehicles": rows, "totals": totals,
            "trucks": {"overdue": sum(1 for r in rows if r["counts"]["overdue"]), "soon": sum(1 for r in rows if not r["counts"]["overdue"] and r["counts"]["soon"]),
                       "no_history": sum(1 for r in rows if r["has_plan"] and r["counts"]["unknown"] == len(r["items"])),
                       "no_group": sum(1 for r in rows if not r["has_plan"])}}


def _numkey(value) -> tuple:
    m = re.match(r"^\s*(\d+)", str(value or ""))
    return (0, int(m.group(1)), str(value)) if m else (1, 0, str(value or ""))


def vehicle_detail(vehicle_id: str, *, today: Optional[date] = None) -> dict:
    real_today = today_th()
    today = today or real_today
    _uuid(vehicle_id, "รถ")
    rows = _vehicles(only=vehicle_id)
    if not rows:
        raise NotFound()
    v = rows[0]
    ready = has_v10()
    records = supabase.select_all("service_records", {"select": "*", "vehicle_id": f"eq.{vehicle_id}", "order": "done_date.desc,id.asc"}) if ready else []
    readings = supabase.select_all("km_readings", {"select": "*", "vehicle_id": f"eq.{vehicle_id}", "order": "end_date.desc,id.asc"}) if ready else []
    overrides = supabase.select_all("service_intervals", {"select": "*", "order": "item_key.asc"}) if ready else []
    events = supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,mileage,source,bill_line_items(description)",
                                                        "status": "eq.confirmed", "vehicle_id": f"eq.{vehicle_id}", "order": "id.asc"})
    hits = bill_hits(events)
    groups = _groups()
    code = _vehicle_groups().get(vehicle_id)
    plan = vehicle_plan(v, group=groups.get(code), records=records + hits, readings=readings, overrides=overrides, today=today, with_chart=True,
                        soon_days=_plan_settings()["soon_days"], snoozes=_snoozes().get(vehicle_id))
    history = sorted([{**r, "label": item_label(r["item_key"])} for r in records + hits], key=lambda r: (r["done_date"], r["item_key"]), reverse=True)
    choices = [{**g, "months": group_months(g)} for g in sorted(groups.values(), key=lambda g: g.get("sort_order", 99))]
    return {"as_of": today.isoformat(), "simulated": today != real_today, "ready": ready, "vehicle": {k: v.get(k) for k in ("id", "plate_number", "vehicle_number", "nickname", "branch", "vehicle_class", "make", "model")},
            **plan, "group_code": code, "groups": choices, "history": history, "readings": readings}


def _save_vehicle_group(vehicle_id: str, code: Optional[str]) -> None:
    if code is None:
        supabase.delete("vehicle_plans", {"vehicle_id": f"eq.{vehicle_id}"})
    else:
        supabase.insert("vehicle_plans", [{"vehicle_id": vehicle_id, "group_code": code, "updated_at": datetime.now(timezone.utc).isoformat()}],
                        on_conflict="vehicle_id")


def set_vehicle_group(data: dict) -> dict:
    _require()
    vehicle_id, code = clean_vehicle_group(data, known=_groups())
    if not _vehicles(only=vehicle_id):
        raise InvalidPlan("ไม่พบรถคันนี้")
    _save_vehicle_group(vehicle_id, code)
    return vehicle_detail(vehicle_id)


def set_vehicle_groups(data: dict) -> dict:
    """The settings page: which truck follows which group, several trucks in one save."""
    _require()
    changes = clean_vehicle_groups(data, known=_groups())
    ids = {v["id"] for v in _vehicles()}
    unknown = [vid for vid, _c in changes if vid not in ids]
    if unknown:
        raise InvalidPlan("ไม่พบรถบางคัน (อาจถูกปิดใช้งานไปแล้ว) — โหลดหน้าใหม่แล้วลองอีกครั้ง")
    for vehicle_id, code in changes:
        _save_vehicle_group(vehicle_id, code)
    return settings()


def record_service(data: dict) -> dict:
    """"บันทึกว่าทำแล้ว": saves the items as a repair record done by hand (source 'manual'), the same kind of record as a
    mechanic's own work -- it shows in สมุดงานซ่อม and can be edited or deleted there, and the plan reads it back."""
    clean = clean_service(data, today=today_th())
    if not _vehicles(only=clean["vehicle_id"]):
        raise InvalidPlan("ไม่พบรถคันนี้")
    existing = supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,mileage,source,bill_line_items(description)",
                                                          "status": "eq.confirmed", "vehicle_id": f"eq.{clean['vehicle_id']}",
                                                          "event_date": f"eq.{clean['done_date'].isoformat()}", "order": "id.asc"})
    keys = drop_done_already(clean["items"], clean["vehicle_id"], clean["done_date"], bill_hits(existing))
    if not keys:
        raise InvalidPlan("รายการเหล่านี้ถูกบันทึกไว้แล้วในวันนั้น (ดูในสมุดงานซ่อม)")
    try:
        saved = events.save_event(service_payload(clean, keys), [])
    except events.InvalidEvent as exc:
        raise InvalidPlan(str(exc))
    logsource.invalidate()
    return {"event_id": saved["event_id"], "items": keys}


def add_records(data: dict) -> list[dict]:
    _require()
    rows = clean_records(data, today=today_th())
    if not _vehicles(only=rows[0]["vehicle_id"]):
        raise InvalidPlan("ไม่พบรถคันนี้")
    existing = supabase.select("service_records", {"select": "item_key,done_date", "vehicle_id": f"eq.{rows[0]['vehicle_id']}", "done_date": f"eq.{rows[0]['done_date']}"})
    have = {r["item_key"] for r in existing}
    fresh = [r for r in rows if r["item_key"] not in have]        # a double click must not record the same thing twice
    return supabase.insert("service_records", fresh) if fresh else []


def delete_record(record_id: str) -> None:
    _require()
    _uuid(record_id, "รายการ")
    supabase.delete("service_records", {"id": f"eq.{record_id}"})


def add_reading(data: dict) -> dict:
    _require()
    row = clean_reading(data, today=today_th())
    if not _vehicles(only=row["vehicle_id"]):
        raise InvalidPlan("ไม่พบรถคันนี้")
    return supabase.insert("km_readings", [row])[0]


def delete_reading(reading_id: str) -> None:
    _require()
    _uuid(reading_id, "รายการ")
    supabase.delete("km_readings", {"id": f"eq.{reading_id}"})


def settings() -> dict:
    """The settings page: the groups (months per step, how many trucks) and the km per item for each rule set."""
    ready = has_v10()
    overrides = supabase.select_all("service_intervals", {"select": "*", "order": "item_key.asc"}) if ready else []
    counts: dict = {}
    for code in _vehicle_groups().values():
        counts[code] = counts.get(code, 0) + 1
    groups = [{**g, "months": group_months(g), "implied_km_per_day": group_km_per_day(g), "trucks": counts.get(g["code"], 0),
               "customised": group_customised(g), "default": {k: (default_group(g["code"]) or {}).get(k) for k in ("months_per_step", "km_per_day")}}
              for g in sorted(_groups().values(), key=lambda g: g.get("sort_order", 99))]
    plans = _vehicle_groups()
    vehicles = [{"id": v["id"], "plate_number": v.get("plate_number"), "vehicle_number": v.get("vehicle_number"), "nickname": v.get("nickname"),
                 "branch": v.get("branch"), "vehicle_class": v.get("vehicle_class"), "group_code": plans.get(v["id"])}
                for v in sorted(_vehicles(), key=lambda v: (v.get("branch") or "", _numkey(v.get("vehicle_number")), v.get("plate_number") or ""))] if ready else []
    return {"ready": ready, "groups": groups, "rule_sets": {r: rule_set_items(r, overrides) for r in RULE_SETS}, "vehicles": vehicles}


def set_interval(data: dict) -> dict:
    _require()
    row = clean_interval(data)
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.insert("service_intervals", [row], on_conflict="vehicle_class,item_key")
    return settings()


def reset_interval(vehicle_class: str, item_key: str) -> dict:
    _require()
    supabase.delete("service_intervals", {"vehicle_class": f"eq.{vehicle_class}", "item_key": f"eq.{item_key}"})
    return settings()


def reset_group(code: str) -> dict:
    """Back to the built-in months / km per day of a group."""
    base = default_group(code)
    if not base:
        raise InvalidPlan("ไม่พบกลุ่มนี้")
    return set_group({"code": code, "months_per_step": base["months_per_step"], "km_per_day": base["km_per_day"]})


def set_group(data: dict) -> dict:
    _require()
    row = clean_group(data, known=_groups())
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    base = _groups()[row["code"]]
    full = {"code": base["code"], "label": base["label"], "rule_set": base["rule_set"], "step_km": base["step_km"],
            "sort_order": base.get("sort_order", 99), **row}
    supabase.insert("service_groups", [full], on_conflict="code")
    return settings()


# ------------------------------------------------------- reminder settings
def _plan_settings() -> dict:
    """soon_days (how many days ahead "coming close" starts), enabled, items (which items are reminded about)."""
    saved: dict = {}
    try:
        saved = mgr.get_settings() if mgr.has_v12() else {}
    except supabase.SupabaseError:
        saved = {}
    try:
        soon = int(saved.get("plan_soon_days", SOON_DAYS))
    except (TypeError, ValueError):
        soon = SOON_DAYS
    items = saved.get("plan_remind_items")
    return {"soon_days": max(1, min(60, soon)), "enabled": bool(saved.get("plan_reminders", True)),
            "items": [k for k in (items if isinstance(items, list) else (DEFAULT_REMIND_ITEMS or list(ITEMS))) if k in ITEMS]}


def reminder_settings() -> dict:
    return {**_plan_settings(), "ready": mgr.has_v12() if supabase.configured() else False,
            "all_items": [{"key": k, "label": v[0]} for k, v in ITEMS.items()]}


def clean_reminder_settings(data: dict) -> dict:
    out: dict = {}
    if "soon_days" in data:
        out["plan_soon_days"] = _positive_int(data["soon_days"], "จำนวนวันที่เริ่มเตือน", 1, 60)
    if "enabled" in data:
        out["plan_reminders"] = bool(data["enabled"])
    if "items" in data:
        items = data["items"]
        if not isinstance(items, list) or any(k not in ITEMS for k in items):
            raise InvalidPlan("รายการที่เตือนไม่ถูกต้อง")
        out["plan_remind_items"] = list(dict.fromkeys(items))
    return out


def _positive_int(value, what: str, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise InvalidPlan(f"{what}ต้องเป็นตัวเลข")
    if not low <= n <= high:
        raise InvalidPlan(f"{what}ต้องอยู่ระหว่าง {low}-{high}")
    return n


def save_reminder_settings(data: dict) -> dict:
    values = clean_reminder_settings(data)
    try:
        if values:
            mgr.save_settings(values)
    except mgr.InvalidTicket as exc:
        raise InvalidPlan(str(exc))
    return reminder_settings()


def parse_as_of(value, *, today: Optional[date] = None) -> Optional[date]:
    """The day the "look ahead" slider asks about, or None for today. Only the future, a limited way."""
    if value in (None, ""):
        return None
    d = parse_date(value)
    today = today or today_th()
    if d is None or not today <= d <= today + timedelta(days=MAX_AS_OF_DAYS):
        raise InvalidPlan(f"ดูล่วงหน้าได้ไม่เกิน {MAX_AS_OF_DAYS} วัน")
    return None if d == today else d


# --------------------------------------------------------------- snoozing
_probe13 = {"at": 0.0, "ok": False}


def has_v13() -> bool:
    now = time.time()
    if now - _probe13["at"] < 30:
        return _probe13["ok"]
    try:
        supabase.select("service_snoozes", {"select": "vehicle_id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe13.update(at=now, ok=ok)
    return ok


def _snoozes() -> dict:
    """vehicle_id -> {item_key -> row}."""
    if not has_v13():
        return {}
    out: dict = {}
    for r in supabase.select_all("service_snoozes", {"select": "*", "order": "vehicle_id.asc,item_key.asc"}):
        out.setdefault(r["vehicle_id"], {})[r["item_key"]] = r
    return out


def clean_snooze(data: dict, *, today: date) -> dict:
    vehicle_id = _uuid(data.get("vehicle_id"), "รถ")
    key = data.get("item_key")
    if key not in ITEMS:
        raise InvalidPlan("ไม่รู้จักรายการ")
    days = _positive_int(data.get("days"), "จำนวนวันที่เลื่อน", 1, MAX_SNOOZE_DAYS)
    return {"vehicle_id": vehicle_id, "item_key": key, "until": (today + timedelta(days=days)).isoformat()}


def snooze(data: dict) -> dict:
    """Do not remind about this item of this truck for the next N days. Applies to its current due date only."""
    if not has_v13():
        raise InvalidPlan(NEEDS_V13)
    row = clean_snooze(data, today=today_th())
    detail = vehicle_detail(row["vehicle_id"])
    item = next((i for i in detail["items"] if i["key"] == row["item_key"]), None)
    if item is None or not item["due_date"]:
        raise InvalidPlan("รายการนี้ยังไม่มีวันครบกำหนด จึงไม่มีอะไรให้เลื่อนเตือน")
    supabase.insert("service_snoozes", [{**row, "for_due": item["due_date"]}], on_conflict="vehicle_id,item_key")
    return vehicle_detail(row["vehicle_id"])


def clear_snooze(vehicle_id: str, item_key: str) -> dict:
    if not has_v13():
        raise InvalidPlan(NEEDS_V13)
    _uuid(vehicle_id, "รถ")
    supabase.delete("service_snoozes", {"vehicle_id": f"eq.{vehicle_id}", "item_key": f"eq.{item_key}"})
    return vehicle_detail(vehicle_id)


# --------------------------------------------------------------- reminders
def _truck_label(r: dict) -> str:
    return " ".join(x for x in (f"#{r['vehicle_number']}" if r.get("vehicle_number") else "", r.get("plate_number") or "") if x) or "รถ"


def collect_reminders(rows: list[dict], today: date, soon_days: int, items: list[str]) -> list[dict]:
    """The items to remind about, from the overview rows. Stages: 'soon' (within `soon_days`), 'due' (today), 'overdue'
    (repeated every OVERDUE_REPEAT_DAYS days). Snoozed items are left out. Each carries a `key` that is unique for that
    stage of that due date (and for the end of a snooze), so a push can be claimed once."""
    out = []
    for r in rows:
        if not r.get("has_plan"):
            continue
        for it in r["items"]:
            dl = it.get("days_left")
            if it["key"] not in items or it["state"] == "unknown" or dl is None or dl > soon_days or it.get("snoozed_until"):
                continue
            stage = "overdue" if dl < 0 else "due" if dl == 0 else "soon"
            suffix = f":s{it['snooze_ended']}" if it.get("snooze_ended") else ""
            tail = f"over{(-dl) // OVERDUE_REPEAT_DAYS}" if stage == "overdue" else stage
            km = it["km_left"] if it.get("km_left") is not None else it.get("est_km_left")
            out.append({"key": f"plan:{r['id']}:{it['key']}:{it['due_date']}:{tail}{suffix}", "stage": stage, "vehicle_id": r["id"], "truck": _truck_label(r),
                        "branch": r.get("branch"), "item_key": it["key"], "label": it["label"], "days_left": dl, "due_date": it["due_date"],
                        "km_left": km, "km_real": it.get("km_left") is not None})
    order = {"overdue": 0, "due": 1, "soon": 2}
    return sorted(out, key=lambda x: (order[x["stage"]], x["days_left"], x["truck"]))


def _km_text(x: dict) -> str:
    if x["km_left"] is None:
        return ""
    sign = "≈ " if not x["km_real"] else ""
    return f" · เหลือ {sign}{abs(x['km_left']):,} กม." if x["km_left"] >= 0 else f" · เกิน {sign}{abs(x['km_left']):,} กม."


def reminder_text(reminders: list[dict], today: date, soon_days: int) -> str:
    """The reminders as plain text (what the preview under the look-ahead slider shows)."""
    months = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
    lines = [f"เตือนรอบซ่อมบำรุงรถ · {today.day} {months[today.month - 1]} {today.year + 543}", ""]
    for stage, title, phrase in (("due", "ครบกำหนดวันนี้", lambda x: ""), ("overdue", "เลยกำหนดแล้ว", lambda x: f" · เลย {-x['days_left']} วัน"),
                                 ("soon", f"ใกล้ถึงรอบ (ภายใน {soon_days} วัน)", lambda x: f" · อีก {x['days_left']} วัน")):
        rows = [x for x in reminders if x["stage"] == stage]
        if rows:
            lines += [f"{title} ({len(rows)})"] + [f"• {x['truck']} — {x['label']}{phrase(x)}{_km_text(x)}" for x in rows] + [""]
    return "\n".join(lines).strip()


def reminder_preview(*, today: Optional[date] = None) -> dict:
    """What the reminders would say for `today` (the look-ahead slider's day): every item in the window, snoozed ones left out."""
    today = today or today_th()
    cfg = _plan_settings()
    rows = overview(today=today)["vehicles"]
    found = collect_reminders(rows, today, cfg["soon_days"], cfg["items"])
    snoozed = [{"truck": _truck_label(r), "label": it["label"], "until": it["snoozed_until"]} for r in rows for it in r["items"]
               if it.get("snoozed_until") and it["key"] in cfg["items"]]
    return {"as_of": today.isoformat(), "soon_days": cfg["soon_days"], "enabled": cfg["enabled"], "items": found, "snoozed": snoozed,
            "text": reminder_text(found, today, cfg["soon_days"]) if found else ""}
