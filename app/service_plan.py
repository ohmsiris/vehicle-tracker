"""
The vehicle maintenance program (แผนซ่อมบำรุงรถ): which service items each class of truck needs, how often, and
when each truck's next one is due.

The intervals come from the dealership rules, checked against the Isuzu (D-Max 1.9, NLR/NMR/NPR, FVM), Toyota
Revo and Hino service guides. Every item is "whichever comes first": a distance (km) OR a time (months) -- a truck
that barely moves still needs its oil changed. Both can be changed per class of truck (service_intervals).

How far a truck has driven is rarely known exactly, so it is estimated (see estimate_km_per_day): from odometer
readings if there are two far enough apart, else from the GPS distance entered for recent periods, else from a
fleet-wide average for that class of truck (flagged as an estimate). From that, "km since the last service" and the
date the km interval runs out are projected, and the earlier of that and the months deadline is the due date.

When an item was last done comes from two places: what people record here (service_records) and the confirmed
repair bills -- a line such as "กรองเครื่อง" on a bill dated 2026-10-22 counts as that truck's engine filter done
that day (classify_line). Everything that decides something is a pure function (unit-tested); the database calls
are thin. Until schema_v10.sql has been run reads come back empty and writes say what is missing (has_v10()).
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
CLASSES = [SMALL, BIG]
NEEDS_V10 = "ต้องรัน schema_v10.sql ใน Supabase ก่อน จึงจะใช้แผนซ่อมบำรุงได้"
SOON_DAYS = 14                       # "due soon" = due within this many days
GPS_WINDOW_DAYS = 120                # GPS periods older than this no longer describe how the truck is used now
MIN_ODOMETER_SPAN = 30               # days between two odometer readings before they beat the GPS figure
MAX_KM = 3_000_000

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
    "injector_washers": ("แหวนรองหัวฉีด", "เครื่องยนต์"),
    "gearbox_oil": ("น้ำมันเกียร์", "ช่วงล่าง / ส่งกำลัง"),
    "diff_oil": ("น้ำมันเฟืองท้าย", "ช่วงล่าง / ส่งกำลัง"),
    "brake_clutch_fluid": ("น้ำมันเบรก / คลัตช์", "ช่วงล่าง / ส่งกำลัง"),
    "power_steering": ("น้ำมันพาวเวอร์", "ช่วงล่าง / ส่งกำลัง"),
    "wheel_grease": ("จาระบีลูกปืนล้อ", "ช่วงล่าง / ส่งกำลัง"),
}

# class -> key -> (km, months). Km are the dealership's rules; months are the "6 months per step" of the official
# guides (10,000 km for pickups, 20,000 km for trucks), with the guides' own figures where they give one.
DEFAULT_PLAN = {
    SMALL: {                                           # D-Max, Revo
        "engine_oil": (10_000, 6),
        "engine_filter": (10_000, 6),                  # dealership 20,000; changed with every oil change by choice
        "fuel_filter": (50_000, 30),
        "air_filter": (40_000, 24),
        "coolant": (40_000, 24),
        "valve_adjust": (40_000, 24),
        "injector_washers": (80_000, 48),
        "gearbox_oil": (40_000, 24),
        "diff_oil": (40_000, 24),
        "brake_clutch_fluid": (40_000, 24),
        "power_steering": (40_000, 24),
        "wheel_grease": (30_000, 18),
    },
    BIG: {                                             # NLR/NMR/NPR, 6-wheelers, 10-wheelers
        "engine_oil": (20_000, 6),
        "engine_filter": (20_000, 6),
        "fuel_filter": (20_000, 6),
        "air_filter": (80_000, 12),
        "coolant": (40_000, 24),
        "valve_adjust": (40_000, 12),
        "gearbox_oil": (40_000, 12),
        "diff_oil": (40_000, 12),
        "brake_clutch_fluid": (40_000, 12),
        "power_steering": (40_000, 24),
        "wheel_grease": (60_000, 18),
    },
}

# Fleet averages from the GPS study (Sep-Oct 2026), used only when a truck has no distance data of its own.
DEFAULT_KM_PER_DAY = {SMALL: 130, BIG: 70}


def item_label(key: str) -> str:
    return ITEMS[key][0] if key in ITEMS else key


def effective_plan(vehicle_class: Optional[str], overrides: Optional[list[dict]] = None) -> list[dict]:
    """The items one class of truck needs, defaults with any saved overrides applied."""
    base = DEFAULT_PLAN.get(vehicle_class or "")
    if not base:
        return []
    saved = {o["item_key"]: o for o in (overrides or []) if o.get("vehicle_class") == vehicle_class}
    out = []
    for key, (label, group) in ITEMS.items():
        if key not in base:
            continue
        km, months = base[key]
        row = {"key": key, "label": label, "group": group, "default_km": km, "default_months": months,
               "interval_km": km, "interval_months": months, "customised": False}
        if key in saved:
            row.update(interval_km=saved[key].get("interval_km"), interval_months=saved[key].get("interval_months"), customised=True)
        out.append(row)
    return out


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


def estimate_km_per_day(readings: list[dict], today: date, vehicle_class: Optional[str]) -> dict:
    """{value, source, note}: source is 'odometer', 'gps' or 'default' (a class average -- an estimate)."""
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
    default = DEFAULT_KM_PER_DAY.get(vehicle_class or "")
    return {"value": default, "source": "default" if default else "none",
            "note": f"ค่าเฉลี่ยของ{vehicle_class} (ยังไม่มีข้อมูลระยะทางของคันนี้)" if default else "ยังไม่มีข้อมูลระยะทาง"}


def latest_odometer(readings: list[dict], hits: list[dict]) -> Optional[dict]:
    """The most recent odometer figure for a truck: a reading, or the mileage on a bill."""
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
    """One item of one truck: state ('overdue' / 'soon' / 'ok' / 'unknown'), when it falls due and by which limit."""
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
        candidates.append((add_months(done, item["interval_months"]), "months"))
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


def vehicle_plan(vehicle: dict, *, records: list[dict], readings: list[dict], overrides: list[dict], today: date) -> dict:
    """The whole programme for one truck. `records` = saved records + bill hits for THIS truck."""
    cls = vehicle.get("vehicle_class")
    plan = effective_plan(cls, overrides)
    est = estimate_km_per_day(readings, today, cls)
    odo = latest_odometer(readings, records)
    items = [item_status(it, last_done(it["key"], records), est["value"], odo, today) for it in plan]
    items.sort(key=lambda i: (STATE_ORDER[i["state"]], i["days_left"] if i["days_left"] is not None else 10**6))
    counts = {s: sum(1 for i in items if i["state"] == s) for s in ("overdue", "soon", "ok", "unknown")}
    worst = next((s for s in ("overdue", "soon", "ok", "unknown") if counts[s]), None)
    return {"vehicle_id": vehicle["id"], "km_per_day": est, "odometer": ({"date": odo["date"].isoformat(), "km": odo["km"]} if odo else None),
            "items": items, "counts": counts, "worst": worst if cls in DEFAULT_PLAN else None, "has_plan": bool(plan)}


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
    cls = data.get("vehicle_class")
    key = data.get("item_key")
    if cls not in CLASSES:
        raise InvalidPlan("ขนาดรถต้องเป็นรถเล็กหรือรถใหญ่")
    if key not in DEFAULT_PLAN[cls]:
        raise InvalidPlan("รายการนี้ไม่ได้อยู่ในแผนของรถกลุ่มนี้")
    km = _int(data.get("interval_km"), "ระยะทาง", maximum=1_000_000)
    months = _int(data.get("interval_months"), "จำนวนเดือน", maximum=120)
    if not km and not months:
        raise InvalidPlan("ใส่ระยะทางหรือจำนวนเดือนอย่างน้อยอย่างใดอย่างหนึ่ง")
    return {"vehicle_class": cls, "item_key": key, "interval_km": km or None, "interval_months": months or None}


# ----------------------------------------------------------------- database
_probe = {"at": 0.0, "ok": False}


def has_v10() -> bool:
    """True once schema_v10.sql has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table in ("service_records", "km_readings", "service_intervals"):
            supabase.select(table, {"select": "vehicle_id" if table != "service_intervals" else "item_key", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v10():
        raise InvalidPlan(NEEDS_V10)


def options() -> dict:
    return {"ready": has_v10(), "classes": CLASSES, "soon_days": SOON_DAYS,
            "items": [{"key": k, "label": v[0], "group": v[1]} for k, v in ITEMS.items()],
            "default_km_per_day": DEFAULT_KM_PER_DAY}


def _bill_events() -> list[dict]:
    return supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,mileage,bill_line_items(description)",
                                                      "status": "eq.confirmed", "order": "id.asc"})


def _group(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r["vehicle_id"], []).append(r)
    return out


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
    hits = _group(bill_hits(_bill_events()))
    rows = []
    for v in vehicles:
        plan = vehicle_plan(v, records=records.get(v["id"], []) + hits.get(v["id"], []), readings=readings.get(v["id"], []),
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
                       "no_history": sum(1 for r in rows if r["has_plan"] and r["counts"]["unknown"] == len(r["items"]))}}


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
    plan = vehicle_plan(v, records=records + hits, readings=readings, overrides=overrides, today=today)
    history = sorted([{**r, "label": item_label(r["item_key"])} for r in records + hits], key=lambda r: (r["done_date"], r["item_key"]), reverse=True)
    return {"as_of": today.isoformat(), "ready": ready, "vehicle": {k: v.get(k) for k in ("id", "plate_number", "vehicle_number", "nickname", "branch", "vehicle_class", "make", "model")},
            **plan, "history": history, "readings": readings}


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


def intervals() -> dict:
    overrides = supabase.select_all("service_intervals", {"select": "*", "order": "item_key.asc"}) if has_v10() else []
    return {"ready": has_v10(), "classes": {c: effective_plan(c, overrides) for c in CLASSES}}


def set_interval(data: dict) -> dict:
    _require()
    row = clean_interval(data)
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    supabase.insert("service_intervals", [row], on_conflict="vehicle_class,item_key")
    return intervals()


def reset_interval(vehicle_class: str, item_key: str) -> dict:
    _require()
    supabase.delete("service_intervals", {"vehicle_class": f"eq.{vehicle_class}", "item_key": f"eq.{item_key}"})
    return intervals()
