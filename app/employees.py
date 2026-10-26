"""
พนักงาน -- the staff of both branches in one table (employees, schema_v21.sql).

An employee is never deleted once anything points at them (loans, warning letters, memos): "removing" one is ลาออก
(status 'resigned', with the date and the reason), and they can be brought back. A row added by mistake, with nothing
pointing at it, can be deleted. A พนักงานขับรถ is also a row of `drivers` (who drives which truck): that row is made and kept up to date from the employee (sync_driver), never edited on its own.

Pure rules (clean_employee, clean_resign, summarize) are unit-tested; the database calls are thin. The section can have a
second lock (settings.hr_pin / HR_PIN): check_pin() is called by every endpoint in this module's group.
"""
import hmac
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app import fleet, supabase
from app.config import settings

BRANCHES = fleet.BRANCHES
STATUSES = ("active", "resigned")
DRIVER_POSITION = "พนักงานขับรถ"                  # a position that starts with this is a driver: it gets a row in `drivers` (who drives which truck)
POSITION_PRESETS = ["ผลิต", "พนักงานขับรถสาย", "พนักงานขับรถหน้าโรง", "เด็กรถ", "ช่าง", "แพ็ค", "หัวหน้างาน", "ผู้ช่วยหัวหน้างาน", "เสมียนออฟฟิศ", "ล้างกระสอบ", "นับกระสอบ", "อีเวนต์"]
NEEDS_V21 = "ยังใช้ส่วนพนักงานไม่ได้ — ต้องรันไฟล์ schema_v21.sql และ schema_v22.sql ใน Supabase ก่อน"
PREFIXES = ("นาย", "นาง", "นางสาว")
GENDERS = ("ชาย", "หญิง")
GENDER_OF = {"นาย": "ชาย", "นาง": "หญิง", "นางสาว": "หญิง"}
BANGKOK = timezone(timedelta(hours=7))
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_/.]{1,23}$")

# (table, column) that point at employees.id -- a later feature adds its table here, so "delete" refuses while anything is attached
REFERENCED_BY: list[tuple[str, str]] = []


class InvalidEmployee(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


class PinRequired(Exception):
    pass


def today_th() -> date:
    return datetime.now(BANGKOK).date()


# ------------------------------------------------------------------ the second lock
def pin_configured() -> bool:
    return bool(settings.hr_pin)


def check_pin(supplied: str) -> None:
    """Raises PinRequired when a PIN is set and `supplied` is not it. With no PIN configured the section is open (to the app token)."""
    if settings.hr_pin and not hmac.compare_digest((supplied or "").encode(), settings.hr_pin.encode()):
        raise PinRequired()


# ------------------------------------------------------------------ pure rules
# The title at the start of a name, longest first (นางสาว before นาง). English ones are turned into the Thai title.
_TITLES = [(re.compile(r"^\s*นางสาว\s*"), "นางสาว"), (re.compile(r"^\s*น\.ส\.\s*"), "นางสาว"), (re.compile(r"^\s*นาง\s*"), "นาง"),
           (re.compile(r"^\s*นาย\s*"), "นาย"), (re.compile(r"^\s*(?:miss|ms)\.?\s+", re.I), "นางสาว"), (re.compile(r"^\s*mrs\.?\s*", re.I), "นาง"),
           (re.compile(r"^\s*mr\.?\s*", re.I), "นาย")]
# a title or sex written inside the name: "ตุ๊กตา ( Mrs. THU ZAR WIN )", "ปิยวรรณ (หญิง) ถิ่นช่วง"
_HINTS = [(re.compile(r"\bmiss\b|\bms\.?\s", re.I), ("นางสาว", "หญิง")), (re.compile(r"\bmrs\.?\s", re.I), ("นาง", "หญิง")),
          (re.compile(r"\bmr\.?\s", re.I), ("นาย", "ชาย")), (re.compile(r"\(\s*หญิง\s*\)"), (None, "หญิง")), (re.compile(r"\(\s*ชาย\s*\)"), (None, "ชาย"))]


def split_prefix(name: str) -> tuple[Optional[str], str, Optional[str]]:
    """(prefix, the name without it, gender) read from a full name. The prefix is None when the name starts with none; the gender is then
    taken from a hint inside the name ("(หญิง)", "Mrs.") when there is one, else None -- for the owner to fill in."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    for pattern, prefix in _TITLES:
        m = pattern.match(text)
        if m and text[m.end():].strip():
            return prefix, text[m.end():].strip(), GENDER_OF[prefix]
    for pattern, (prefix, gender) in _HINTS:
        if pattern.search(text):
            return prefix, text, gender
    return None, text, None


def display_name(prefix: Optional[str], full_name: str) -> str:
    """How the name is written on a document: the title joined to the name (นายสมชาย ใจดี)."""
    return f"{prefix or ''}{full_name}"


def _clean(text, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _date(value, what: str, *, today: date, allow_future_days: int = 0) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        raise InvalidEmployee(f"{what}ไม่ถูกต้อง")
    if d.year < 1950 or d.year >= 2400:
        raise InvalidEmployee(f"{what}ไม่ถูกต้อง (ปีต้องเป็น ค.ศ. เช่น 2026)")
    if d > today + timedelta(days=allow_future_days):
        raise InvalidEmployee(f"{what}เป็นวันในอนาคต")
    return d.isoformat()


def _uuid_or_none(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidEmployee("รหัสไม่ถูกต้อง")


def clean_employee(data: dict, *, existing: Optional[dict] = None, others: list[dict], today: Optional[date] = None) -> dict:
    """The fields to write for an add (existing=None) or an edit (only the keys sent). `others` = every other employee (code must be unique).
    The status is not editable here -- ลาออก / กลับเข้าทำงาน have their own steps."""
    today = today or today_th()
    creating = existing is None
    values: dict = {}
    if creating or "code" in data:
        code = re.sub(r"\s+", "", str(data.get("code") or "")).upper()
        if not code:
            raise InvalidEmployee("ใส่รหัสพนักงานด้วย (เช่น SB-FR001)")
        if not CODE_RE.match(code):
            raise InvalidEmployee("รหัสพนักงานใช้ได้เฉพาะตัวอักษรอังกฤษ ตัวเลข และ - (เช่น SB-FR001)")
        if any(str(o.get("code") or "").upper() == code for o in others):
            raise InvalidEmployee(f"มีรหัส {code} อยู่แล้ว")
        values["code"] = code
    typed_prefix = _clean(data.get("prefix"), 10) if "prefix" in data else None
    if typed_prefix and typed_prefix not in PREFIXES:
        raise InvalidEmployee("คำนำหน้าต้องเป็น นาย / นาง / นางสาว")
    if "gender" in data and _clean(data.get("gender"), 10) not in ("", *GENDERS):
        raise InvalidEmployee("เพศต้องเป็น ชาย หรือ หญิง")
    if creating or "full_name" in data:
        name = _clean(data.get("full_name"), 120)
        if not name:
            raise InvalidEmployee("ใส่ชื่อ-สกุลด้วย")
        found, rest, hint = split_prefix(name)
        if found:                                           # a title typed into the name box moves to its own field, straight away
            name = rest
            typed_prefix = typed_prefix or found
        elif hint and not typed_prefix and not data.get("gender"):
            values["gender"] = hint
        values["full_name"] = name
    if typed_prefix:
        values["prefix"] = typed_prefix
        values["gender"] = GENDER_OF[typed_prefix]           # นาย = ชาย; นาง / นางสาว = หญิง
    elif "prefix" in data:
        values["prefix"] = None
    if not values.get("prefix") and "gender" in data:
        values["gender"] = _clean(data.get("gender"), 10) or None
    elif creating and "gender" not in values:
        values["gender"] = None
    if creating and "prefix" not in values:
        values["prefix"] = None
    if creating or "branch" in data:
        branch = _clean(data.get("branch"), 40)
        if branch not in BRANCHES:
            raise InvalidEmployee("เลือกสาขาให้ถูกต้อง")
        values["branch"] = branch
    for key, limit in (("nickname", 40), ("position", 60), ("phone", 30)):
        if creating or key in data:
            values[key] = _clean(data.get(key), limit) or None
    if creating or "notes" in data:
        values["notes"] = _clean(data.get("notes"), 500) or None
    if creating or "start_date" in data:
        values["start_date"] = _date(data.get("start_date"), "วันเริ่มงาน", today=today)
    final = {**(existing or {}), **values}
    if final.get("end_date") and final.get("start_date") and final["end_date"] < final["start_date"]:
        raise InvalidEmployee("วันเริ่มงานต้องไม่หลังวันที่ลาออก")
    return values


def clean_resign(data: dict, *, existing: dict, today: Optional[date] = None) -> dict:
    """ลาออก: the last day (default today; up to 60 days ahead for a notice already given) and the reason."""
    today = today or today_th()
    if existing.get("status") == "resigned":
        raise InvalidEmployee("คนนี้ลาออกไปแล้ว")
    end = _date(data.get("end_date"), "วันที่ลาออก", today=today, allow_future_days=60) or today.isoformat()
    if existing.get("start_date") and end < existing["start_date"]:
        raise InvalidEmployee("วันที่ลาออกต้องไม่ก่อนวันเริ่มงาน")
    return {"status": "resigned", "end_date": end, "end_reason": _clean(data.get("reason"), 300) or None}


def summarize(rows: list[dict]) -> dict:
    """Head count by position for the people still working (the line under the filters)."""
    live = [r for r in rows if r.get("status") != "resigned"]
    by: dict = {}
    for r in live:
        by[r.get("position") or "ยังไม่ระบุ"] = by.get(r.get("position") or "ยังไม่ระบุ", 0) + 1
    genders = {g: sum(1 for r in live if r.get("gender") == g) for g in GENDERS}
    genders["ยังไม่ระบุ"] = sum(1 for r in live if r.get("gender") not in GENDERS)
    return {"active": len(live), "resigned": len(rows) - len(live), "by_position": sorted(by.items(), key=lambda kv: (-kv[1], kv[0])), "by_gender": genders}


def present(row: dict, drivers: dict[str, dict]) -> dict:
    driver = drivers.get(row.get("driver_id") or "")
    return {"id": row["id"], "code": row["code"], "prefix": row.get("prefix") or "", "gender": row.get("gender") or "", "full_name": row["full_name"],
            "display_name": display_name(row.get("prefix"), row["full_name"]), "nickname": row.get("nickname") or "", "branch": row["branch"],
            "position": row.get("position") or "", "status": row.get("status") or "active", "start_date": row.get("start_date"),
            "end_date": row.get("end_date"), "end_reason": row.get("end_reason") or "", "phone": row.get("phone") or "",
            "driver_id": row.get("driver_id"), "driver_name": driver["name"] if driver else "", "notes": row.get("notes") or ""}


# ------------------------------------------------------------------ the database
_probe = {"at": 0.0, "ok": False}


def has_v21() -> bool:
    """True once schema_v21.sql has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("employees", {"select": "id,prefix,gender", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v21():
        raise InvalidEmployee(NEEDS_V21)


def _all() -> list[dict]:
    return supabase.select_all("employees", {"select": "*", "order": "code.asc,id.asc"})


def _drivers() -> dict[str, dict]:
    try:
        return {d["id"]: d for d in supabase.select_all("drivers", {"select": "id,name", "order": "id.asc"})}
    except supabase.SupabaseError:
        return {}


def options() -> dict:
    return {"ready": has_v21(), "branches": BRANCHES, "positions": POSITION_PRESETS, "prefixes": list(PREFIXES), "pin": pin_configured()}


def list_employees() -> dict:
    """Everyone (resigned too -- the page filters), with the head count by position."""
    if not has_v21():
        return {"ready": False, "employees": [], "summary": summarize([]), "positions": POSITION_PRESETS, "branches": BRANCHES}
    rows = _all()
    drivers = _drivers()
    seen = list(POSITION_PRESETS)
    for r in rows:
        if r.get("position") and r["position"] not in seen:
            seen.append(r["position"])
    return {"ready": True, "employees": [present(r, drivers) for r in rows], "summary": summarize(rows), "positions": seen, "branches": BRANCHES}


def _get(employee_id: str) -> dict:
    try:
        eid = str(uuid.UUID(str(employee_id)))
    except ValueError:
        raise NotFound()
    rows = supabase.select("employees", {"select": "*", "id": f"eq.{eid}", "limit": "1"})
    if not rows:
        raise NotFound()
    return rows[0]


def get_employee(employee_id: str) -> dict:
    _require()
    return present(_get(employee_id), _drivers())


# ---- the driver record: ONE place to edit. A พนักงานขับรถ is also a row of `drivers` (who drives which truck, who to call); that row is made and
# kept up to date from the employee here, never edited on its own (the drivers tab only reads it), so the two cannot drift apart.
def is_driver_position(position) -> bool:
    return str(position or "").startswith(DRIVER_POSITION)


def driver_fields(row: dict) -> dict:
    fields = {"name": row["full_name"], "phone": row.get("phone"), "branch": row.get("branch"), "notes": row.get("notes"), "active": row.get("status") != "resigned"}
    if fleet.has_v7():                                          # the nickname column of `drivers` comes with schema_v7
        fields["nickname"] = row.get("nickname")
    return fields


def sync_driver(row: dict) -> Optional[str]:
    """Make / update the driver record of this employee (a driver position, or already linked). Returns its id. A resigned employee's driver
    record is switched off and their trucks lose their main driver, as when a driver stops driving."""
    if not fleet.has_v6() or not (is_driver_position(row.get("position")) or row.get("driver_id")):
        return row.get("driver_id")
    fields = driver_fields(row)
    did = row.get("driver_id")
    if did:
        supabase.update("drivers", {"id": f"eq.{did}"}, fields)
        if not fields["active"]:
            supabase.update("vehicles", {"main_driver_id": f"eq.{did}"}, {"main_driver_id": None})
    else:
        did = supabase.insert("drivers", [fields])[0]["id"]
        supabase.update("employees", {"id": f"eq.{row['id']}"}, {"driver_id": did})
    fleet._changed()
    return did


def create_employee(data: dict) -> dict:
    _require()
    values = clean_employee(data, others=_all())
    row = supabase.insert("employees", [values])[0]
    sync_driver(row)
    return present(_get(row["id"]), _drivers())


def edit_employee(employee_id: str, data: dict) -> dict:
    _require()
    existing = _get(employee_id)
    values = clean_employee(data, existing=existing, others=[o for o in _all() if o["id"] != existing["id"]])
    if values:
        supabase.update("employees", {"id": f"eq.{existing['id']}"}, values)
    row = _get(existing["id"])
    sync_driver(row)
    return present(_get(existing["id"]), _drivers())


def resign(employee_id: str, data: dict) -> dict:
    _require()
    existing = _get(employee_id)
    supabase.update("employees", {"id": f"eq.{existing['id']}"}, clean_resign(data, existing=existing))
    sync_driver(_get(existing["id"]))
    return present(_get(existing["id"]), _drivers())


def reinstate(employee_id: str) -> dict:
    """Back to work: active again, the leaving date and reason cleared (the history stays with the person)."""
    _require()
    existing = _get(employee_id)
    if existing.get("status") != "resigned":
        raise InvalidEmployee("คนนี้ยังทำงานอยู่")
    supabase.update("employees", {"id": f"eq.{existing['id']}"}, {"status": "active", "end_date": None, "end_reason": None})
    sync_driver(_get(existing["id"]))
    return present(_get(existing["id"]), _drivers())


def attached(employee_id: str) -> int:
    """How many loans / letters / ... point at this employee."""
    total = 0
    for table, column in REFERENCED_BY:
        try:
            total += len(supabase.select(table, {"select": "id", column: f"eq.{employee_id}", "limit": "1"}))
        except supabase.SupabaseError:                  # that feature's table is not in this database yet
            continue
    return total


def delete_employee(employee_id: str) -> None:
    """Only for a row added by mistake: refuses once anything is attached (use ลาออก then)."""
    _require()
    existing = _get(employee_id)
    if attached(existing["id"]):
        raise InvalidEmployee("ลบไม่ได้ — มีเงินกู้หรือเอกสารของคนนี้อยู่ ให้กด “ลาออก” แทน (ประวัติยังอยู่ครบ)")
    did = existing.get("driver_id")
    if did:
        trucks = supabase.select("vehicles", {"select": "vehicle_number", "main_driver_id": f"eq.{did}"})
        if trucks:
            raise InvalidEmployee("ลบไม่ได้ — เป็นคนขับหลักของรถ #" + ", #".join(str(t.get("vehicle_number") or "?") for t in trucks) + " (เปลี่ยนคนขับหลักก่อน หรือกด “ลาออก”)")
    supabase.delete("employees", {"id": f"eq.{existing['id']}"})
    if did:
        supabase.delete("drivers", {"id": f"eq.{did}"})                 # the driver record made for this employee goes with them
        fleet._changed()
