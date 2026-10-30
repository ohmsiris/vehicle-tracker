"""
The mechanics who work on the MACHINES (machine_mechanics): in-house ("internal") and outside ("external", contractors),
with a phone and a branch. A separate list from the truck mechanics and shops (app/shops.py): a truck mechanic is not
offered as "ผู้ทำ" on a machine job and the other way round. Needs schema_v35.sql.
"""
import time
from typing import Optional

from app import machinery, supabase
from app.machinery import InvalidMachinery, NotFound

KINDS = {"internal": "ช่างของเรา", "external": "ช่างนอก / ผู้รับเหมา"}
NEEDS_V35 = "ต้องรัน schema_v35.sql ใน Supabase ก่อน จึงจะจัดรายชื่อช่างเครื่องจักรได้"
_probe = {"at": 0.0, "ok": False}


def has_v35() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("machine_mechanics", {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def clean_mechanic(data: dict, *, existing: Optional[dict] = None, others: list[dict]) -> dict:
    """A mechanic from the settings page. The name is what shows in the "ผู้ทำ" list, so it is unique among the others."""
    creating = existing is None
    values: dict = {}
    if creating or "name" in data:
        name = machinery._clean(data.get("name"), 60)
        if not name:
            raise InvalidMachinery("ใส่ชื่อช่างด้วย")
        if any(o.get("name") == name for o in others):
            raise InvalidMachinery(f"มีช่างชื่อ “{name}” อยู่แล้ว")
        values["name"] = name
    for key, limit in (("full_name", 80), ("phone", 40), ("note", 200)):
        if creating or key in data:
            values[key] = machinery._clean(data.get(key), limit) or None
    if creating or "kind" in data:
        kind = machinery._clean(data.get("kind"), 20) or "internal"
        if kind not in KINDS:
            raise InvalidMachinery("ประเภทช่างต้องเป็น ช่างของเรา หรือ ช่างนอก")
        values["kind"] = kind
    if creating or "branch" in data:
        values["branch"] = machinery._clean(data.get("branch"), 40) or None
    if "active" in data:
        values["active"] = bool(data["active"])
    if "sort_order" in data:
        values["sort_order"] = int(machinery._number(data.get("sort_order"), "ลำดับ", maximum=100000) or 0)
    return values


def list_mechanics(include_inactive: bool = False) -> dict:
    if not has_v35():
        return {"ready": False, "mechanics": [], "kinds": KINDS}
    params = {"select": "*", "order": "kind.asc,sort_order.asc,name.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return {"ready": True, "mechanics": supabase.select_all("machine_mechanics", params), "kinds": KINDS}


def _all() -> list[dict]:
    return supabase.select_all("machine_mechanics", {"select": "*", "order": "id.asc"})


def _require() -> None:
    if not has_v35():
        raise InvalidMachinery(NEEDS_V35)


def create_mechanic(data: dict) -> dict:
    _require()
    values = clean_mechanic(data, others=_all())
    values.setdefault("sort_order", 100 if values["kind"] == "internal" else 110)
    supabase.insert("machine_mechanics", [values])
    return list_mechanics(True)


def edit_mechanic(mechanic_id: str, data: dict) -> dict:
    _require()
    mid = machinery._uuid_or_none(mechanic_id)
    rows = _all()
    existing = next((r for r in rows if r["id"] == mid), None)
    if not existing:
        raise NotFound()
    values = clean_mechanic(data, existing=existing, others=[r for r in rows if r["id"] != mid])
    if values:
        supabase.update("machine_mechanics", {"id": f"eq.{mid}"}, values)
    return list_mechanics(True)


def delete_mechanic(mechanic_id: str) -> dict:
    """A mechanic who has done jobs stays in the reports as written text; deleting only takes the name off the pick-list."""
    _require()
    mid = machinery._uuid_or_none(mechanic_id)
    if not any(r["id"] == mid for r in _all()):
        raise NotFound()
    supabase.delete("machine_mechanics", {"id": f"eq.{mid}"})
    return list_mechanics(True)
