"""
The fleet itself: adding / editing / retiring trucks, what each is used for
(รถสาย, รถหน้าโรง ...), and the drivers with one main driver per truck.

Trucks are never deleted -- their repair history must survive -- so "remove"
means active = false (the truck then drops out of every picker but its history
stays searchable). Everything that decides whether a change is allowed is a
pure function (clean_vehicle / clean_driver) so it is unit-tested; the
database calls are thin.

usage / main_driver_id / notes and the drivers table arrive with
schema_v6.sql. Until that has been run the reads still work (those fields are
just empty) and every write explains what is missing (has_v6()).
"""
import re
import time
import uuid
from datetime import date
from typing import Optional

from app import roster, supabase

BRANCHES = ["สระบุรี", "แก่งคอย"]
USAGE_PRESETS = ["รถสาย", "รถหน้าโรง"]
MAX_TEXT = 100
MAX_NOTES = 300
NEEDS_V6 = "ต้องรัน schema_v6.sql ใน Supabase ก่อน จึงจะจัดการรถและคนขับได้"
NEEDS_V7 = "ต้องรัน schema_v7.sql ใน Supabase ก่อน จึงจะบันทึกข้อมูลทะเบียนและชื่อเล่นคนขับได้"
VEHICLE_CLASSES = ["รถเล็ก", "รถใหญ่"]                  # the size class on the master sheet; drives how often oil is changed
# the extra truck details (registration paper etc.) and driver nickname that arrive with schema_v7.sql
V7_VEHICLE_TEXT = ("color", "engine_number", "chassis_number", "registration_type", "owner_name", "holder_name")
V7_VEHICLE_KEYS = ("vehicle_class", "registered_date") + V7_VEHICLE_TEXT
V7_DRIVER_KEYS = ("nickname",)
NEEDS_V32 = "ต้องรัน schema_v32.sql ใน Supabase ก่อน จึงจะทำเครื่องหมายว่าประวัติอัปเดตแล้วได้"
NEEDS_V8 = "ต้องรัน schema_v8.sql ใน Supabase ก่อน จึงจะบันทึกชื่อเรียกรถได้"
# statuses that mean a truck still has work in progress, so it cannot be retired yet
BUSY = ("open", "at_shop", "awaiting_bill")


class InvalidFleet(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


_probe = {"at": 0.0, "ok": False}


def has_v6() -> bool:
    """True once schema_v6.sql has been run (probed, cached 30 s) -- so the app works both before and after it."""
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("drivers", {"select": "id", "limit": "1"})
        supabase.select("vehicles", {"select": "usage,main_driver_id,notes", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


_probe32 = {"at": 0.0, "ok": False}


def has_v32() -> bool:
    """True once schema_v32.sql (vehicles.history_current_on) has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe32["at"] < 30:
        return _probe32["ok"]
    try:
        supabase.select("vehicles", {"select": "history_current_on", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe32.update(at=now, ok=ok)
    return ok


_probe8 = {"at": 0.0, "ok": False}


def has_v8() -> bool:
    """True once schema_v8.sql (vehicles.nickname) has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe8["at"] < 30:
        return _probe8["ok"]
    try:
        supabase.select("vehicles", {"select": "nickname", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe8.update(at=now, ok=ok)
    return ok


_probe7 = {"at": 0.0, "ok": False}


def has_v7() -> bool:
    """True once schema_v7.sql has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe7["at"] < 30:
        return _probe7["ok"]
    try:
        supabase.select("vehicles", {"select": ",".join(V7_VEHICLE_KEYS), "limit": "1"})
        supabase.select("drivers", {"select": ",".join(V7_DRIVER_KEYS), "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe7.update(at=now, ok=ok)
    return ok


def _squash(text: str) -> str:
    return re.sub(r"[\s\-_.]", "", text or "").lower()


def _clean(text, limit: int = MAX_TEXT) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _uuid_or_none(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidFleet("รหัสไม่ถูกต้อง")


# --------------------------------------------------------------- pure rules
def clean_vehicle(data: dict, *, existing: Optional[dict] = None, others: list[dict], drivers: dict[str, dict],
                  busy_vehicle: bool = False) -> dict:
    """
    The column values to write for a new truck (existing=None) or for the fields present in `data`
    (a partial edit), or InvalidFleet. `others` = every OTHER truck (active or not), `drivers` = id -> driver row.
    """
    creating = existing is None
    values: dict = {}

    def given(key):
        return key in data

    if creating or given("plate_number"):
        plate = _clean(data.get("plate_number"), 30)
        if not plate:
            raise InvalidFleet("ใส่ทะเบียนรถด้วย")
        values["plate_number"] = plate
    if creating or given("plate_province"):
        values["plate_province"] = _clean(data.get("plate_province"), 40)
    if creating or given("branch"):
        branch = _clean(data.get("branch"), 40)
        if not branch:
            raise InvalidFleet("เลือกสาขาด้วย")
        values["branch"] = branch
    for key in ("vehicle_number", "make", "model", "usage"):
        if creating or given(key):
            values[key] = _clean(data.get(key), 40) or None
    if given("nickname"):                                   # the truck's own nickname (schema_v8)
        values["nickname"] = _clean(data.get("nickname"), 40) or None
    if creating or given("notes"):
        values["notes"] = _clean(data.get("notes"), MAX_NOTES) or None
    # ---- registration details and size class (only written when sent: they need schema_v7.sql)
    if given("vehicle_class"):
        cls = _clean(data.get("vehicle_class"), 20)
        if cls and cls not in VEHICLE_CLASSES:
            raise InvalidFleet(f"ประเภทรถต้องเป็น {' หรือ '.join(VEHICLE_CLASSES)}")
        values["vehicle_class"] = cls or None
    for key in V7_VEHICLE_TEXT:
        if given(key):
            values[key] = _clean(data.get(key), 80) or None
    if given("registered_date"):
        values["registered_date"] = _clean_registered_date(data.get("registered_date"))
    if creating or given("main_driver_id"):
        driver_id = _uuid_or_none(data.get("main_driver_id"))
        if driver_id is not None:
            d = drivers.get(driver_id)
            if d is None:
                raise InvalidFleet("ไม่พบคนขับที่เลือก")
            if not d.get("active", True):
                raise InvalidFleet(f"{d['name']} เลิกขับแล้ว เลือกคนอื่นหรือเปิดใช้งานคนขับก่อน")
        values["main_driver_id"] = driver_id
    if given("history_current"):                            # "the history is brought up to date": the day it was marked (kept while it stays marked), or cleared
        values["history_current_on"] = ((existing or {}).get("history_current_on") or date.today().isoformat()) if data["history_current"] else None
    if given("active"):
        values["active"] = bool(data["active"])
        if not values["active"] and busy_vehicle:
            raise InvalidFleet("รถคันนี้ยังมีงานซ่อมค้างอยู่ ปิดงานหรือยกเลิกก่อนจึงจะเลิกใช้ได้")

    # ---- uniqueness, judged on the final state of the truck
    final = {**(existing or {}), **values}
    plate_key = (_squash(final.get("plate_number")), _squash(final.get("plate_province")))
    if creating or "plate_number" in values or "plate_province" in values:
        for o in others:
            if (_squash(o.get("plate_number")), _squash(o.get("plate_province"))) == plate_key:
                raise InvalidFleet(f"ทะเบียน {final['plate_number']} มีอยู่ในระบบแล้ว")
    # A fleet number is unique within a branch AND size class: a big truck #2 and a small truck #2 can both exist.
    # (When either size class is not filled in yet, they are assumed to be the same kind.)
    number_changed = creating or any(k in values for k in ("vehicle_number", "branch", "active", "vehicle_class"))
    if number_changed and final.get("vehicle_number") and final.get("active", True):
        for o in others:
            same_kind = not final.get("vehicle_class") or not o.get("vehicle_class") or final["vehicle_class"] == o["vehicle_class"]
            if (o.get("active", True) and (o.get("branch") or "") == final.get("branch") and same_kind
                    and _squash(o.get("vehicle_number")) == _squash(final["vehicle_number"])):
                raise InvalidFleet(f"เบอร์ {final['vehicle_number']} ของสาขา{final.get('branch')} ใช้อยู่แล้ว (ทะเบียน {o.get('plate_number')}) — "
                                   "ถ้าเป็นรถคนละขนาด (เล็ก/ใหญ่) ให้เลือกประเภทรถก่อน")
    return values


def _clean_registered_date(raw) -> Optional[str]:
    text = _clean(raw, 20)
    if not text:
        return None
    try:
        d = date.fromisoformat(text)
    except ValueError:
        raise InvalidFleet("วันที่จดทะเบียนไม่ถูกต้อง")
    if d.year >= 2400:
        raise InvalidFleet(f"วันที่จดทะเบียน: ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543})")
    if d > date.today():
        raise InvalidFleet("วันที่จดทะเบียนเป็นวันในอนาคต")
    return d.isoformat()


def clean_driver(data: dict, *, existing: Optional[dict] = None, others: list[dict]) -> dict:
    creating = existing is None
    values: dict = {}
    if creating or "name" in data:
        name = _clean(data.get("name"), 80)
        if not name:
            raise InvalidFleet("ใส่ชื่อคนขับด้วย")
        values["name"] = name
    for key, limit in (("phone", 30), ("branch", 40)):
        if creating or key in data:
            values[key] = _clean(data.get(key), limit) or None
    if creating or "notes" in data:
        values["notes"] = _clean(data.get("notes"), MAX_NOTES) or None
    if "nickname" in data:
        values["nickname"] = _clean(data.get("nickname"), 40) or None
    if "active" in data:
        values["active"] = bool(data["active"])
    final = {**(existing or {}), **values}
    if final.get("active", True):
        for o in others:
            if o.get("active", True) and _squash(o.get("name")) == _squash(final["name"]):
                raise InvalidFleet(f"มีคนขับชื่อ {final['name']} อยู่แล้ว (ถ้าเป็นคนละคน ให้ใส่ชื่อเล่นหรือนามสกุลต่อท้าย)")
    return values


def usage_choices(vehicles: list[dict]) -> list[str]:
    """The presets first, then any other usage already typed on a truck (so a one-off label is one tap next time)."""
    seen = list(USAGE_PRESETS)
    for v in vehicles:
        u = (v.get("usage") or "").strip()
        if u and u not in seen:
            seen.append(u)
    return seen


# ---------------------------------------------------------------- database
def _require_v6() -> None:
    if not has_v6():
        raise InvalidFleet(NEEDS_V6)


def _require_v7_for(values: dict, keys) -> None:
    if any(k in values for k in keys) and not has_v7():
        raise InvalidFleet(NEEDS_V7)
    if "nickname" in values and keys is V7_VEHICLE_KEYS and not has_v8():
        raise InvalidFleet(NEEDS_V8)


def _changed() -> None:
    """Everything that caches the fleet (the picker list, the log) must forget it."""
    roster.invalidate_roster_cache()
    try:
        from app import logsource
        logsource.invalidate()
    except Exception:                                   # pragma: no cover -- the cache is an optimisation only
        pass


def drivers_by_id(include_inactive: bool = True) -> dict[str, dict]:
    if not has_v6():
        return {}
    params = {"select": "*", "order": "name.asc,id.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return {d["id"]: d for d in supabase.select_all("drivers", params)}


def list_drivers(include_inactive: bool = False) -> list[dict]:
    drivers = drivers_by_id(include_inactive=True)
    trucks: dict[str, list] = {}
    if drivers:
        for v in supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch,active,main_driver_id", "order": "id.asc"}):
            if v.get("main_driver_id") and v.get("active"):
                trucks.setdefault(v["main_driver_id"], []).append(
                    {"vehicle_id": v["id"], "plate": v["plate_number"], "truck_number": v.get("vehicle_number") or "", "branch": v.get("branch") or ""})
    employee_of: dict = {}
    try:                                                     # the employee each driver record belongs to (the driver is edited there)
        employee_of = {e["driver_id"]: e["id"] for e in supabase.select_all("employees", {"select": "id,driver_id", "driver_id": "not.is.null", "order": "id.asc"})}
    except supabase.SupabaseError:
        pass
    rows = [{"driver_id": d["id"], "employee_id": employee_of.get(d["id"]), "name": d["name"], "nickname": d.get("nickname") or "", "phone": d.get("phone") or "", "branch": d.get("branch") or "",
             "notes": d.get("notes") or "", "active": bool(d.get("active", True)), "trucks": trucks.get(d["id"], [])}
            for d in drivers.values() if include_inactive or d.get("active", True)]
    return sorted(rows, key=lambda r: (not r["active"], r["name"]))


def create_driver(data: dict) -> dict:
    _require_v6()
    others = list(drivers_by_id().values())
    values = clean_driver(data, others=others)
    _require_v7_for(values, V7_DRIVER_KEYS)
    row = supabase.insert("drivers", [values])[0]
    _changed()
    return {"driver_id": row["id"], "name": row["name"]}


def edit_driver(driver_id: str, data: dict) -> dict:
    _require_v6()
    did = _uuid_or_none(driver_id)
    drivers = drivers_by_id()
    existing = drivers.get(did)
    if existing is None:
        raise NotFound()
    values = clean_driver(data, existing=existing, others=[d for k, d in drivers.items() if k != did])
    _require_v7_for(values, V7_DRIVER_KEYS)
    freed = 0
    if values.get("active") is False and existing.get("active", True):
        # a driver who stopped driving no longer is anyone's main driver
        freed = len(supabase.update("vehicles", {"main_driver_id": f"eq.{did}"}, {"main_driver_id": None}))
    if values:
        supabase.update("drivers", {"id": f"eq.{did}"}, values)
    _changed()
    return {"ok": True, "freed_trucks": freed}


def _busy_vehicle_ids() -> set[str]:
    rows = supabase.select_all("repair_requests", {"select": "vehicle_id", "status": f"in.({','.join(BUSY)})", "order": "id.asc"})
    return {r["vehicle_id"] for r in rows if r.get("vehicle_id")}


def _all_vehicles() -> list[dict]:
    cols = "id,plate_number,plate_province,vehicle_number,make,model,branch,active"
    if has_v6():
        cols += ",usage,main_driver_id,notes"
    if has_v7():
        cols += "," + ",".join(V7_VEHICLE_KEYS)
    if has_v8():
        cols += ",nickname"
    if has_v32():
        cols += ",history_current_on"
    return supabase.select_all("vehicles", {"select": cols, "order": "id.asc"})


def create_vehicle(data: dict) -> dict:
    _require_v6()
    values = clean_vehicle(data, others=_all_vehicles(), drivers=drivers_by_id())
    _require_v7_for(values, V7_VEHICLE_KEYS)
    row = supabase.insert("vehicles", [values])[0]
    _changed()
    return {"vehicle_id": row["id"]}


def edit_vehicle(vehicle_id: str, data: dict) -> dict:
    _require_v6()
    vid = _uuid_or_none(vehicle_id)
    vehicles = _all_vehicles()
    existing = next((v for v in vehicles if v["id"] == vid), None)
    if existing is None:
        raise NotFound()
    busy = data.get("active") is False and vid in _busy_vehicle_ids()
    values = clean_vehicle(data, existing=existing, others=[v for v in vehicles if v["id"] != vid], drivers=drivers_by_id(), busy_vehicle=busy)
    _require_v7_for(values, V7_VEHICLE_KEYS)
    if "history_current_on" in values and not has_v32():
        raise InvalidFleet(NEEDS_V32)
    if values:
        supabase.update("vehicles", {"id": f"eq.{vid}"}, values)
    _changed()
    return {"ok": True}


# ------------------------------------------------------------ who to call about a repair request
FACTORY_USAGE = "รถหน้าโรง"            # a factory truck has no main driver: whoever reports the problem is the one to call
_HONORIFICS = ("นางสาว", "น.ส.", "นาย", "นาง", "คุณ", "พี่", "น้อง", "ลุง", "ป้า", "ครู")


def _name_key(text) -> str:
    """A name for comparing: no spaces, lower case, no leading honorific (คุณฉัตรชัย -> ฉัตรชัย)."""
    t = "".join(str(text or "").split()).lower()
    for h in _HONORIFICS:
        if t.startswith(h) and len(t) > len(h) + 1:
            return t[len(h):]
    return t


def _first_name_key(name) -> str:
    words = str(name or "").split()
    if len(words) > 1 and words[0] in _HONORIFICS:      # "นาย สมชาย ใจดี" -> สมชาย
        words = words[1:]
    return _name_key(words[0]) if words else ""


def match_reporter(reporter, drivers: list[dict]) -> Optional[dict]:
    """The driver a name typed as "ผู้แจ้ง" refers to: by nickname, full name, first name, or (3+ letters) the start of the
    name -- the first kind of match that finds anyone decides, and it must find exactly one (two drivers called
    ประสงค์ is not a guess worth making). Only active drivers with a phone number can be matched."""
    key = _name_key(reporter)
    if not key:
        return None
    pool = [d for d in drivers if d.get("active", True) and d.get("phone")]
    tiers = (
        lambda d: _name_key(d.get("nickname")) == key,
        lambda d: _name_key(d.get("name")) == key,
        lambda d: _first_name_key(d.get("name")) == key,
        lambda d: len(key) >= 3 and _name_key(d.get("name")).startswith(key),
    )
    for test in tiers:
        found = [d for d in pool if test(d)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            return None
    return None


def call_target(reporter, usage, main_driver: Optional[dict], drivers: list[dict]) -> Optional[dict]:
    """Who to ring about a request: {kind: 'driver' | 'reporter', name, phone}, or None.
    A factory truck (รถหน้าโรง) -> the person who reported it; any other truck -> its main driver, and when it has none
    (or no number) the reporter, if that name is a driver we know."""
    if usage != FACTORY_USAGE and main_driver and main_driver.get("phone"):
        return {"kind": "driver", "name": main_driver.get("nickname") or main_driver.get("name"), "phone": main_driver["phone"]}
    d = match_reporter(reporter, drivers)
    # named as the card says ("ผู้แจ้ง ฉัตรชัย"), not by the nickname the number is saved under
    return {"kind": "reporter", "name": str(reporter).strip(), "phone": d["phone"]} if d else None


def with_call_targets(rows: list[dict], vehicles: list[dict], drivers: list[dict]) -> list[dict]:
    """Adds `call` to each board row. vehicles = [{id, usage, main_driver_id}], drivers = list_drivers() rows."""
    by_vehicle = {v["id"]: v for v in vehicles}
    by_driver = {d["driver_id"]: d for d in drivers}
    out = []
    for r in rows:
        v = by_vehicle.get(r.get("vehicle_id")) or {}
        out.append({**r, "call": call_target(r.get("reported_by"), v.get("usage"), by_driver.get(v.get("main_driver_id")), drivers)})
    return out
