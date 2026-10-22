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

from app import supabase

SMALL, BIG = "รถเล็ก", "รถใหญ่"
RULE_SETS = [SMALL, BIG]
NEEDS_V10 = "ต้องรัน schema_v10.sql และ schema_v11.sql ใน Supabase ก่อน จึงจะใช้แผนซ่อมบำรุงได้"
SOON_DAYS = 14                       # "due soon" = due within this many days
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
    {"code": "S2", "label": "รถเล็ก - รถสาย (2 เดือน ต่อ 10,000 กม.)", "rule_set": SMALL, "step_km": 10_000, "months_per_step": 2, "km_per_day": None, "sort_order": 1},
    {"code": "S3", "label": "รถเล็ก - รถหน้าโรง (3 เดือน ต่อ 10,000 กม.)", "rule_set": SMALL, "step_km": 10_000, "months_per_step": 3, "km_per_day": None, "sort_order": 2},
    {"code": "S6", "label": "รถเล็ก - นานสุดตามคู่มือ (6 เดือน ต่อ 10,000 กม.)", "rule_set": SMALL, "step_km": 10_000, "months_per_step": 6, "km_per_day": None, "sort_order": 3},
    {"code": "B4", "label": "ใช้กฎรถใหญ่ - NLR (4 เดือน ต่อ 20,000 กม.)", "rule_set": BIG, "step_km": 20_000, "months_per_step": 4, "km_per_day": None, "sort_order": 4},
    {"code": "B5", "label": "รถใหญ่ - รถสาย (5 เดือน ต่อ 20,000 กม.)", "rule_set": BIG, "step_km": 20_000, "months_per_step": 5, "km_per_day": None, "sort_order": 5},
    {"code": "B6", "label": "รถใหญ่ - หน้าโรง / CP (6 เดือน ต่อ 20,000 กม.)", "rule_set": BIG, "step_km": 20_000, "months_per_step": 6, "km_per_day": None, "sort_order": 6},
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
    """Confirmed bills (each {vehicle_id, event_date, mileage, bill_line_items: [{description}]}) -> one hit per
    (truck, item, day): {vehicle_id, item_key, done_date, odometer_km, source: 'bill'}."""
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
                        "odometer_km": ev.get("mileage"), "source": "bill", "event_id": ev.get("id")})
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


def item_status(item: dict, last: Optional[dict], kmpd: Optional[float], odo_now: Optional[dict], today: date) -> dict:
    """One item of one truck: state ('overdue' / 'soon' / 'ok' / 'unknown'), when it falls due and by which limit.
    `kmpd` is the MEASURED km per day (None when nothing is measured: then only the months decide)."""
    out = {"key": item["key"], "label": item["label"], "group": item["group"], "interval_km": item.get("interval_km"),
           "interval_months": item.get("interval_months"), "customised": item.get("customised", False),
           "last_date": None, "last_source": None, "last_odometer_km": None, "km_since": None, "km_basis": None,
           "due_date": None, "due_by": None, "days_left": None, "km_left": None, "state": "unknown"}
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
    candidates = []
    if item.get("interval_months"):
        candidates.append((add_months_f(done, item["interval_months"]), "months"))
    if item.get("interval_km") and km_since is not None:
        out["km_since"] = round(km_since)
        out["km_basis"] = basis
        out["km_left"] = round(item["interval_km"] - km_since)
        if kmpd:
            candidates.append((today + timedelta(days=math.ceil(out["km_left"] / kmpd)), "km"))
        elif out["km_left"] <= 0:
            candidates.append((today, "km"))
    if not candidates:
        out["state"] = "ok"
        return out
    due, by = min(candidates)
    out.update(due_date=due.isoformat(), due_by=by, days_left=(due - today).days)
    out["state"] = "overdue" if out["days_left"] < 0 else "soon" if out["days_left"] <= SOON_DAYS else "ok"
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
                 today: date, with_chart: bool = False) -> dict:
    """The whole programme for one truck. `records` = saved records + bill hits for THIS truck; `group` = its group."""
    plan = effective_plan(group, overrides)
    default_kmpd = group_km_per_day(group) if group else None
    est = estimate_km_per_day(readings, today, default_kmpd)
    odo = latest_odometer(readings, records)
    measured = est["value"] if est["source"] in ("gps", "odometer") else None
    statuses = [item_status(it, last_done(it["key"], records), measured, odo, today) for it in plan]
    items = sorted(statuses, key=lambda i: (STATE_ORDER[i["state"]], i["days_left"] if i["days_left"] is not None else 10**6))
    counts = {s: sum(1 for i in items if i["state"] == s) for s in ("overdue", "soon", "ok", "unknown")}
    worst = next((s for s in ("overdue", "soon", "ok", "unknown") if counts[s]), None)
    out = {"vehicle_id": vehicle["id"], "group": ({**group, "months": group_months(group)} if group else None), "km_per_day": est,
           "odometer": ({"date": odo["date"].isoformat(), "km": odo["km"]} if odo else None),
           "items": items, "counts": counts, "worst": worst, "has_plan": bool(plan)}
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
    return {"ready": has_v10(), "rule_sets": RULE_SETS, "soon_days": SOON_DAYS,
            "items": [{"key": k, "label": v[0], "group": v[1]} for k, v in ITEMS.items()]}


def _bill_events() -> list[dict]:
    return supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,mileage,bill_line_items(description)",
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
    """Every active truck with its programme, the trucks that need attention first."""
    today = today or today_th()
    ready = has_v10()
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
                            overrides=overrides, today=today)
        rows.append({**{k: v.get(k) for k in ("id", "plate_number", "vehicle_number", "nickname", "branch", "vehicle_class", "make", "model", "usage")}, **plan})

    def rank(r):
        c = r["counts"]
        known = c["overdue"] + c["soon"] + c["ok"]
        return (-c["overdue"], -c["soon"], 0 if known else 1, str(r.get("branch")), _numkey(r.get("vehicle_number")))

    rows.sort(key=rank)
    totals = {s: sum(r["counts"][s] for r in rows) for s in ("overdue", "soon", "ok", "unknown")}
    return {"as_of": today.isoformat(), "ready": ready, "vehicles": rows, "totals": totals,
            "trucks": {"overdue": sum(1 for r in rows if r["counts"]["overdue"]), "soon": sum(1 for r in rows if not r["counts"]["overdue"] and r["counts"]["soon"]),
                       "no_history": sum(1 for r in rows if r["has_plan"] and r["counts"]["unknown"] == len(r["items"])),
                       "no_group": sum(1 for r in rows if not r["has_plan"])}}


def _numkey(value) -> tuple:
    m = re.match(r"^\s*(\d+)", str(value or ""))
    return (0, int(m.group(1)), str(value)) if m else (1, 0, str(value or ""))


def vehicle_detail(vehicle_id: str, *, today: Optional[date] = None) -> dict:
    today = today or today_th()
    _uuid(vehicle_id, "รถ")
    rows = _vehicles(only=vehicle_id)
    if not rows:
        raise NotFound()
    v = rows[0]
    ready = has_v10()
    records = supabase.select_all("service_records", {"select": "*", "vehicle_id": f"eq.{vehicle_id}", "order": "done_date.desc,id.asc"}) if ready else []
    readings = supabase.select_all("km_readings", {"select": "*", "vehicle_id": f"eq.{vehicle_id}", "order": "end_date.desc,id.asc"}) if ready else []
    overrides = supabase.select_all("service_intervals", {"select": "*", "order": "item_key.asc"}) if ready else []
    events = supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,mileage,bill_line_items(description)",
                                                        "status": "eq.confirmed", "vehicle_id": f"eq.{vehicle_id}", "order": "id.asc"})
    hits = bill_hits(events)
    groups = _groups()
    code = _vehicle_groups().get(vehicle_id)
    plan = vehicle_plan(v, group=groups.get(code), records=records + hits, readings=readings, overrides=overrides, today=today, with_chart=True)
    history = sorted([{**r, "label": item_label(r["item_key"])} for r in records + hits], key=lambda r: (r["done_date"], r["item_key"]), reverse=True)
    choices = [{**g, "months": group_months(g)} for g in sorted(groups.values(), key=lambda g: g.get("sort_order", 99))]
    return {"as_of": today.isoformat(), "ready": ready, "vehicle": {k: v.get(k) for k in ("id", "plate_number", "vehicle_number", "nickname", "branch", "vehicle_class", "make", "model")},
            **plan, "group_code": code, "groups": choices, "history": history, "readings": readings}


def set_vehicle_group(data: dict) -> dict:
    _require()
    vehicle_id, code = clean_vehicle_group(data, known=_groups())
    if not _vehicles(only=vehicle_id):
        raise InvalidPlan("ไม่พบรถคันนี้")
    if code is None:
        supabase.delete("vehicle_plans", {"vehicle_id": f"eq.{vehicle_id}"})
    else:
        supabase.insert("vehicle_plans", [{"vehicle_id": vehicle_id, "group_code": code, "updated_at": datetime.now(timezone.utc).isoformat()}],
                        on_conflict="vehicle_id")
    return vehicle_detail(vehicle_id)


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
    groups = [{**g, "months": group_months(g), "implied_km_per_day": group_km_per_day(g), "trucks": counts.get(g["code"], 0)}
              for g in sorted(_groups().values(), key=lambda g: g.get("sort_order", 99))]
    return {"ready": ready, "groups": groups, "rule_sets": {r: rule_set_items(r, overrides) for r in RULE_SETS}}


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


def set_group(data: dict) -> dict:
    _require()
    row = clean_group(data, known=_groups())
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    base = _groups()[row["code"]]
    full = {"code": base["code"], "label": base["label"], "rule_set": base["rule_set"], "step_km": base["step_km"],
            "sort_order": base.get("sort_order", 99), **row}
    supabase.insert("service_groups", [full], on_conflict="code")
    return settings()
