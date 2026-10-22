"""
Machinery: the machines (the numbered ice machines and the brine-pond machines), an index of what is on
them (motors, pumps, belts, bearings, breakers, contactors ... with their specs), and a log of maintenance
reports (what was repaired / replaced / checked, when, by whom, what it cost).

Everything that decides whether a change is allowed is a pure function (clean_machine / clean_part /
clean_event) so it is unit-tested; the database calls are thin. Until schema_v9.sql has been run the
reads return empty lists and every write says what is missing (has_v9()).
"""
import re
import time
import uuid
from datetime import date
from typing import Optional

from app import supabase

KINDS = ["เครื่อง", "บ่อซอง"]
TUBE_TYPES = ["หลอดใหญ่", "หลอดเหลี่ยม", "หลอดเล็ก"]
EVENT_KINDS = ["ซ่อม", "เปลี่ยนอะไหล่", "ตรวจเช็ค", "ทำความสะอาด", "หล่อลื่น / จาระบี", "อื่นๆ"]
PART_STATUSES = ["Good", "New", "Adjust", "?"]
NEEDS_V9 = "ต้องรัน schema_v9.sql ใน Supabase ก่อน จึงจะใช้ระบบเครื่องจักรได้"
MAX_LIMIT = 500


class InvalidMachinery(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


_probe = {"at": 0.0, "ok": False}


def has_v9() -> bool:
    """True once schema_v9.sql has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table in ("machines", "machine_parts", "machine_events"):
            supabase.select(table, {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


# ------------------------------------------------------------------ helpers
def _clean(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def _clean_lines(value, limit: int) -> str:
    """Like _clean but keeps line breaks (a contactor cell may list three parts, one per line)."""
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in str(value if value is not None else "").splitlines()]
    return "\n".join(ln for ln in lines if ln)[:limit]


def _number(value, what: str, *, maximum: float = 1e9) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        n = float(str(value).replace(",", ""))
    except ValueError:
        raise InvalidMachinery(f"{what}ต้องเป็นตัวเลข")
    if n < 0 or n > maximum:
        raise InvalidMachinery(f"{what}ไม่ถูกต้อง")
    return int(n) if n == int(n) else n


def _uuid_or_none(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidMachinery("รหัสไม่ถูกต้อง")


def _squash(text) -> str:
    return re.sub(r"[\s\-_.]", "", str(text or "")).lower()


def machine_name(m: dict) -> str:
    """"เครื่อง 1", or for the brine-pond machines (which have no number) "เครื่องบ่อซอง Sabroe 30 ตัน"."""
    if m.get("kind") == "บ่อซอง":
        cap = m.get("capacity_tons")
        return " ".join(p for p in ("เครื่องบ่อซอง", m.get("make") or "", f"{cap:g} ตัน" if isinstance(cap, (int, float)) else "") if p)
    return f"เครื่อง {m['number']}" if m.get("number") else "เครื่อง"


# --------------------------------------------------------------- pure rules
def clean_machine(data: dict, *, existing: Optional[dict] = None, others: list[dict]) -> dict:
    creating = existing is None
    values: dict = {}

    def given(k):
        return k in data

    if creating or given("branch"):
        branch = _clean(data.get("branch"), 40)
        if not branch:
            raise InvalidMachinery("เลือกสาขาด้วย")
        values["branch"] = branch
    if creating or given("kind"):
        kind = _clean(data.get("kind"), 20) or "เครื่อง"
        if kind not in KINDS:
            raise InvalidMachinery(f"ประเภทเครื่องต้องเป็น {' หรือ '.join(KINDS)}")
        values["kind"] = kind
    if creating or given("number"):
        number = _number(data.get("number"), "เลขเครื่อง", maximum=999)
        values["number"] = int(number) if number else None
    for key in ("tube_type", "make", "model"):
        if creating or given(key):
            values[key] = _clean(data.get(key), 40) or None
    if creating or given("capacity_tons"):
        values["capacity_tons"] = _number(data.get("capacity_tons"), "ขนาด (ตัน)", maximum=10000)
    if creating or given("notes"):
        values["notes"] = _clean(data.get("notes"), 300) or None
    if given("active"):
        values["active"] = bool(data["active"])

    final = {**(existing or {}), **values}
    if final.get("kind", "เครื่อง") == "เครื่อง" and not final.get("number"):
        raise InvalidMachinery("เครื่องที่มีเลขต้องใส่เลขเครื่อง (เครื่องบ่อซองไม่ต้องมีเลข)")
    if final.get("number") and final.get("active", True):
        for o in others:
            if o.get("active", True) and o.get("branch") == final.get("branch") and o.get("kind") == final.get("kind") and o.get("number") == final["number"]:
                raise InvalidMachinery(f"{machine_name(final)} ของสาขา{final.get('branch')} มีอยู่แล้ว")
    # the name follows the details unless somebody gave it a name of their own
    if given("name") and _clean(data.get("name"), 80):
        values["name"] = _clean(data["name"], 80)
    elif creating or (existing and existing.get("name") == machine_name(existing)):
        if creating or machine_name(final) != existing.get("name"):
            values["name"] = machine_name(final)
    return values


PART_TEXT = {"equipment": 80, "part": 60, "spec": 200, "unit": 20, "bearing_bore": 20, "delta_a": 30, "star_a": 30, "link": 400,
             "desired_a": 30, "notes": 300}
PART_LINES = {"breaker": 300, "contactor": 300, "overload": 300, "current_contactor": 300, "current_overload": 300}


def clean_part(data: dict, *, existing: Optional[dict] = None, machine_ids: set) -> dict:
    creating = existing is None
    values: dict = {}
    if creating or "location" in data:
        location = _clean(data.get("location"), 60)
        if not location:
            raise InvalidMachinery("ใส่สถานที่ / เครื่องด้วย")
        values["location"] = location
    for key, limit in PART_TEXT.items():
        if creating or key in data:
            values[key] = _clean(data.get(key), limit) or None
    for key, limit in PART_LINES.items():
        if creating or key in data:
            values[key] = _clean_lines(data.get(key), limit) or None
    if creating or "qty" in data:
        values["qty"] = _number(data.get("qty"), "จำนวน", maximum=100000)
    if creating or "motor_hp" in data:
        values["motor_hp"] = _number(data.get("motor_hp"), "แรงม้า", maximum=10000)
    if "status" in data:
        status = _clean(data.get("status"), 20)
        if status and status not in PART_STATUSES:
            raise InvalidMachinery(f"สถานะต้องเป็น {', '.join(PART_STATUSES)}")
        values["status"] = status or None
    if "machine_id" in data:
        mid = _uuid_or_none(data.get("machine_id"))
        if mid is not None and mid not in machine_ids:
            raise InvalidMachinery("ไม่พบเครื่องที่เลือก")
        values["machine_id"] = mid
    if "sort_order" in data:
        values["sort_order"] = int(_number(data.get("sort_order"), "ลำดับ", maximum=100000) or 0)
    final = {**(existing or {}), **values}
    if not any(final.get(k) for k in ("equipment", "part", "spec")):
        raise InvalidMachinery("ใส่อย่างน้อยอุปกรณ์ ชิ้นส่วน หรือสเปค")
    return values


EVENT_TEXT = {"area": 80, "equipment": 120, "reason": 500, "brand": 100, "model": 100, "serial_number": 100, "vendor": 100,
              "vendor_contact": 200, "performed_by": 100, "notes": 500}


def clean_event(data: dict, *, existing: Optional[dict] = None, machines: dict[str, dict], today: Optional[date] = None) -> dict:
    """`machines` = id -> machine row. The branch follows the machine when one is chosen."""
    creating = existing is None
    today = today or date.today()
    values: dict = {}
    if "machine_id" in data:
        mid = _uuid_or_none(data.get("machine_id"))
        if mid is not None and mid not in machines:
            raise InvalidMachinery("ไม่พบเครื่องที่เลือก")
        values["machine_id"] = mid
    if creating or "event_date" in data:
        raw = _clean(data.get("event_date"), 20)
        if not raw:
            raise InvalidMachinery("ใส่วันที่ทำด้วย")
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            raise InvalidMachinery("วันที่ไม่ถูกต้อง")
        if d.year >= 2400:
            raise InvalidMachinery(f"วันที่: ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543})")
        if d > today:
            raise InvalidMachinery("วันที่เป็นวันในอนาคต")
        values["event_date"] = d.isoformat()
    if creating or "kind" in data:
        kind = _clean(data.get("kind"), 30) or EVENT_KINDS[0]
        if kind not in EVENT_KINDS:
            raise InvalidMachinery(f"ประเภทงานต้องเป็น {', '.join(EVENT_KINDS)}")
        values["kind"] = kind
    if creating or "summary" in data:
        summary = _clean(data.get("summary"), 500)
        if not summary:
            raise InvalidMachinery("ใส่ว่าทำอะไรด้วย")
        values["summary"] = summary
    for key, limit in EVENT_TEXT.items():
        if creating or key in data:
            values[key] = _clean(data.get(key), limit) or None
    if creating or "cost" in data:
        cost = _number(data.get("cost"), "ค่าใช้จ่าย", maximum=1e8)
        values["cost"] = cost
    if "part_id" in data:
        values["part_id"] = _uuid_or_none(data.get("part_id"))
    # branch: the machine's when there is one, else what was sent
    final = {**(existing or {}), **values}
    machine = machines.get(final.get("machine_id") or "")
    if machine:
        values["branch"] = machine["branch"]
    elif creating or "branch" in data:
        branch = _clean(data.get("branch"), 40)
        if not branch:
            raise InvalidMachinery("เลือกเครื่อง หรือเลือกสาขา")
        values["branch"] = branch
    return values


def _hay(row: dict, keys) -> str:
    return _squash("|".join(str(row.get(k) or "") for k in keys))


def filter_parts(parts: list[dict], machines: dict[str, dict], *, q: str = "", machine_id: str = "", location: str = "", part: str = "") -> list[dict]:
    """The parts index filtered the way the page asks. Words in `q` must ALL appear somewhere in a row
    (including its machine's name), spaces and dashes ignored."""
    tokens = [t for t in (_squash(w) for w in str(q or "").split()) if t]
    out = []
    for p in parts:
        if machine_id and p.get("machine_id") != machine_id:
            continue
        if location and p.get("location") != location:
            continue
        if part and p.get("part") != part:
            continue
        if tokens:
            hay = _hay(p, ("location", "equipment", "part", "spec", "unit", "breaker", "contactor", "overload", "status", "notes", "link")) \
                + _squash((machines.get(p.get("machine_id") or "") or {}).get("name"))
            if not all(t in hay for t in tokens):
                continue
        out.append(p)
    return out


def filter_events(events: list[dict], machines: dict[str, dict], *, q: str = "", machine_id: str = "", area: str = "", kind: str = "", branch: str = "",
                  date_from: str = "", date_to: str = "") -> list[dict]:
    tokens = [t for t in (_squash(w) for w in str(q or "").split()) if t]
    out = []
    for e in events:
        if machine_id and e.get("machine_id") != machine_id:
            continue
        if area and (e.get("area") or "") != area:
            continue
        if kind and e.get("kind") != kind:
            continue
        if branch and e.get("branch") != branch:
            continue
        if date_from and e["event_date"] < date_from:
            continue
        if date_to and e["event_date"] > date_to:
            continue
        if tokens:
            hay = _hay(e, ("area", "equipment", "kind", "summary", "reason", "brand", "model", "serial_number", "vendor", "performed_by", "notes")) \
                + _squash((machines.get(e.get("machine_id") or "") or {}).get("name"))
            if not all(t in hay for t in tokens):
                continue
        out.append(e)
    return out


def facet_counts(values) -> list[dict]:
    c: dict[str, int] = {}
    for v in values:
        if v:
            c[v] = c.get(v, 0) + 1
    return [{"value": k, "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---------------------------------------------------------------- database
def _require() -> None:
    if not has_v9():
        raise InvalidMachinery(NEEDS_V9)


def _machines(include_inactive: bool = True) -> list[dict]:
    params = {"select": "*", "order": "branch.asc,kind.asc,number.asc,id.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return supabase.select_all("machines", params)


def options() -> dict:
    return {"kinds": KINDS, "tube_types": TUBE_TYPES, "event_kinds": EVENT_KINDS, "part_statuses": PART_STATUSES}


def list_machines(include_inactive: bool = False) -> list[dict]:
    if not has_v9():
        return []
    rows = _machines(include_inactive)
    parts = supabase.select_all("machine_parts", {"select": "machine_id", "order": "id.asc"})
    events = supabase.select_all("machine_events", {"select": "machine_id,event_date", "order": "id.asc"})
    n_parts: dict[str, int] = {}
    for p in parts:
        if p.get("machine_id"):
            n_parts[p["machine_id"]] = n_parts.get(p["machine_id"], 0) + 1
    n_events: dict[str, int] = {}
    last: dict[str, str] = {}
    for e in events:
        mid = e.get("machine_id")
        if mid:
            n_events[mid] = n_events.get(mid, 0) + 1
            last[mid] = max(last.get(mid, ""), e["event_date"])
    return [{**m, "part_count": n_parts.get(m["id"], 0), "event_count": n_events.get(m["id"], 0), "last_event_date": last.get(m["id"])} for m in rows]


def get_machine(machine_id: str) -> dict:
    mid = _uuid_or_none(machine_id)
    rows = supabase.select("machines", {"select": "*", "id": f"eq.{mid}", "limit": "1"}) if has_v9() else []
    if not rows:
        raise NotFound()
    return rows[0]


def create_machine(data: dict) -> dict:
    _require()
    values = clean_machine(data, others=_machines())
    return {"machine_id": supabase.insert("machines", [values])[0]["id"]}


def edit_machine(machine_id: str, data: dict) -> dict:
    _require()
    mid = _uuid_or_none(machine_id)
    all_ = _machines()
    existing = next((m for m in all_ if m["id"] == mid), None)
    if existing is None:
        raise NotFound()
    values = clean_machine(data, existing=existing, others=[m for m in all_ if m["id"] != mid])
    if values:
        supabase.update("machines", {"id": f"eq.{mid}"}, values)
    return {"ok": True}


def list_parts(*, q: str = "", machine_id: str = "", location: str = "", part: str = "", limit: int = 200, offset: int = 0) -> dict:
    if not has_v9():
        return {"total": 0, "rows": [], "facets": {"locations": [], "parts": []}}
    machines = {m["id"]: m for m in _machines()}
    parts = supabase.select_all("machine_parts", {"select": "*", "order": "sort_order.asc,location.asc,equipment.asc,id.asc"})
    hits = filter_parts(parts, machines, q=q, machine_id=machine_id, location=location, part=part)
    limit = max(1, min(int(limit), MAX_LIMIT))
    return {"total": len(hits), "rows": hits[max(0, int(offset)):max(0, int(offset)) + limit],
            "facets": {"locations": facet_counts(p["location"] for p in parts), "parts": facet_counts(p.get("part") for p in parts)}}


def _machine_ids() -> set:
    return {m["id"] for m in _machines()}


def create_part(data: dict) -> dict:
    _require()
    values = clean_part(data, machine_ids=_machine_ids())
    return {"part_id": supabase.insert("machine_parts", [values])[0]["id"]}


def _get_part(part_id: str) -> dict:
    pid = _uuid_or_none(part_id)
    rows = supabase.select("machine_parts", {"select": "*", "id": f"eq.{pid}", "limit": "1"})
    if not rows:
        raise NotFound()
    return rows[0]


def edit_part(part_id: str, data: dict) -> dict:
    _require()
    existing = _get_part(part_id)
    values = clean_part(data, existing=existing, machine_ids=_machine_ids())
    if values:
        supabase.update("machine_parts", {"id": f"eq.{existing['id']}"}, values)
    return {"ok": True}


def delete_part(part_id: str) -> dict:
    _require()
    existing = _get_part(part_id)
    supabase.delete("machine_parts", {"id": f"eq.{existing['id']}"})
    return {"ok": True}


def list_events(*, q: str = "", machine_id: str = "", area: str = "", kind: str = "", branch: str = "", date_from: str = "", date_to: str = "",
                limit: int = 50, offset: int = 0) -> dict:
    if not has_v9():
        return {"total": 0, "rows": [], "stats": {"total_cost": 0, "count": 0}}
    machines = {m["id"]: m for m in _machines()}
    events = supabase.select_all("machine_events", {"select": "*", "order": "event_date.desc,created_at.desc,id.asc"})
    hits = filter_events(events, machines, q=q, machine_id=machine_id, area=area, kind=kind, branch=branch, date_from=date_from, date_to=date_to)
    limit = max(1, min(int(limit), MAX_LIMIT))
    page = [{**e, "machine_name": (machines.get(e.get("machine_id") or "") or {}).get("name")} for e in hits[max(0, int(offset)):max(0, int(offset)) + limit]]
    return {"total": len(hits), "rows": page, "stats": {"count": len(hits), "total_cost": round(sum(float(e.get("cost") or 0) for e in hits), 2)},
            "facets": {"areas": facet_counts(e.get("area") for e in events), "kinds": facet_counts(e.get("kind") for e in events)}}


def create_event(data: dict) -> dict:
    _require()
    values = clean_event(data, machines={m["id"]: m for m in _machines()})
    return {"event_id": supabase.insert("machine_events", [values])[0]["id"]}


def edit_event(event_id: str, data: dict) -> dict:
    _require()
    eid = _uuid_or_none(event_id)
    rows = supabase.select("machine_events", {"select": "*", "id": f"eq.{eid}", "limit": "1"})
    if not rows:
        raise NotFound()
    values = clean_event(data, existing=rows[0], machines={m["id"]: m for m in _machines()})
    if values:
        supabase.update("machine_events", {"id": f"eq.{eid}"}, values)
    return {"ok": True}
