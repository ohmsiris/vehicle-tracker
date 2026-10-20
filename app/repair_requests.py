"""
The repair board: follow-up on repair requests from the paper ใบแจ้งซ่อม log.
The paper stays the master record; only repairs worth following up are added
here. A request moves through:

    open --fixed--------------> awaiting_bill --(bill saved)--> completed
     |  \\--fixed, no bill-----------------------------------> completed (no_bill)
     |                              \\--no bill----------------> completed (no_bill)
     \\--cancel--> cancelled          (cancel also from awaiting_bill)

Saving a bill (or, later, a manual entry) that lists the request in
`fixes_request_ids` completes it -- that happens inside the database's
save_maintenance_event(), not here. Everything else is done by
apply_action() below, which is pure (no I/O) so every rule is unit-tested.
"""
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app import supabase

BANGKOK = ZoneInfo("Asia/Bangkok")
ACTIONS = ("fixed_awaiting_bill", "fixed_no_bill", "no_bill", "cancel", "reopen")
MAX_PROBLEM_CHARS = 500
MAX_NAME_CHARS = 100


class InvalidRequest(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class StaleRequest(Exception):
    """The request changed state since the page loaded (someone else, or a
    double-click) -- the page should refresh rather than overwrite."""


def today_bangkok() -> date:
    return datetime.now(BANGKOK).date()


def _clean(text: Optional[str], limit: int) -> str:
    return (text or "").strip()[:limit]


def _parse_date(raw, *, default: date, what: str) -> date:
    if raw in (None, ""):
        return default
    try:
        d = date.fromisoformat(str(raw))
    except ValueError:
        raise InvalidRequest(f"{what}ไม่ถูกต้อง")
    if d.year >= 2400:      # a พ.ศ. year typed where ค.ศ. is expected (2569 for 2026)
        raise InvalidRequest(f"{what}: ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543})")
    if d > today_bangkok() + timedelta(days=1):
        raise InvalidRequest(f"{what}เป็นวันในอนาคต")
    return d


# --------------------------------------------------------------- pure rules
def apply_action(request: dict, action: str, *, fixed_by: str = "", fixed_date=None,
                 reason: str = "", today: Optional[date] = None) -> dict:
    """
    Returns the column values to write for `action` on `request` (a
    repair_requests row), or raises InvalidRequest. Never touches the DB.
    """
    today = today or today_bangkok()
    status = request["status"]
    if action not in ACTIONS:
        raise InvalidRequest("การกระทำไม่ถูกต้อง")

    if action in ("fixed_awaiting_bill", "fixed_no_bill"):
        if status != "open":
            raise InvalidRequest("งานนี้ไม่ได้อยู่ในสถานะรอซ่อมแล้ว")
        who = _clean(fixed_by, MAX_NAME_CHARS)
        if not who:
            raise InvalidRequest("ใส่ชื่อคนหรือร้านที่ซ่อมด้วย")
        when = _parse_date(fixed_date, default=today, what="วันที่ซ่อม")
        if when < date.fromisoformat(request["request_date"]):
            raise InvalidRequest("วันที่ซ่อมต้องไม่ก่อนวันที่แจ้ง")
        values = {"fixed_by": who, "fixed_date": when.isoformat()}
        if action == "fixed_awaiting_bill":
            return {**values, "status": "awaiting_bill"}
        return {**values, "status": "completed", "no_bill": True, "no_bill_reason": _clean(reason, MAX_PROBLEM_CHARS) or None}

    if action == "no_bill":
        if status != "awaiting_bill":
            raise InvalidRequest("ปิดแบบไม่มีบิลได้เฉพาะงานที่รอบิลอยู่")
        return {"status": "completed", "no_bill": True, "no_bill_reason": _clean(reason, MAX_PROBLEM_CHARS) or None}

    if action == "cancel":
        if status not in ("open", "awaiting_bill"):
            raise InvalidRequest("ยกเลิกได้เฉพาะงานที่ยังไม่เสร็จ")
        return {"status": "cancelled"}

    # reopen -- undo a mistake. Not possible once a real record (bill or
    # manual entry) is attached: that record would be left pointing at an
    # open request.
    if status == "open":
        raise InvalidRequest("งานนี้เปิดอยู่แล้ว")
    if request.get("fixed_event_id"):
        raise InvalidRequest("งานนี้ปิดด้วยบิลที่บันทึกแล้ว ย้อนกลับไม่ได้")
    return {"status": "open", "fixed_by": None, "fixed_date": None, "no_bill": False, "no_bill_reason": None}


# ---------------------------------------------------------------- database
def list_active() -> list[dict]:
    """Open + awaiting-bill requests. The board groups and sorts them."""
    return supabase.select("v_active_requests", {"select": "*", "order": "urgent.desc,request_date.asc,request_no.asc", "limit": "500"})


def list_recent(limit: int = 50) -> list[dict]:
    """Recently finished (completed or cancelled), newest first -- for the
    'what happened to it' check, including repairs closed with no bill."""
    rows = supabase.select("repair_requests", {
        "select": "id,seq,request_date,status,problem,reported_by,fixed_by,fixed_date,no_bill,no_bill_reason,status_changed_at,fixed_event_id,vehicle_id,plate_text,truck_number_text,vehicles(plate_number,vehicle_number,branch)",
        "status": "in.(completed,cancelled)", "order": "status_changed_at.desc", "limit": str(limit),
    })
    out = []
    for r in rows:
        v = r.pop("vehicles", None) or {}
        out.append({
            "request_id": r["id"], "request_no": r["seq"], "request_date": r["request_date"], "status": r["status"],
            "problem": r["problem"], "reported_by": r["reported_by"], "fixed_by": r["fixed_by"], "fixed_date": r["fixed_date"],
            "no_bill": r["no_bill"], "no_bill_reason": r["no_bill_reason"], "has_record": r["fixed_event_id"] is not None,
            "finished_at": r["status_changed_at"], "vehicle_id": r["vehicle_id"],
            "plate": v.get("plate_number") or r["plate_text"], "truck_number": v.get("vehicle_number") or r["truck_number_text"],
            "branch": v.get("branch"),
        })
    return out


def get(request_id: str) -> Optional[dict]:
    rows = supabase.select("repair_requests", {"select": "*", "id": f"eq.{request_id}", "limit": "1"})
    return rows[0] if rows else None


def create(data: dict) -> dict:
    vehicle_id = data.get("vehicle_id")
    if not vehicle_id or not supabase.select("vehicles", {"select": "id", "id": f"eq.{vehicle_id}", "limit": "1"}):
        raise InvalidRequest("ยังไม่ได้เลือกรถ")
    problem = _clean(data.get("problem"), MAX_PROBLEM_CHARS)
    if not problem:
        raise InvalidRequest("ใส่ปัญหาที่ต้องซ่อมด้วย")
    row = {
        "vehicle_id": vehicle_id,
        "problem": problem,
        "reported_by": _clean(data.get("reported_by"), MAX_NAME_CHARS) or None,
        "urgent": bool(data.get("urgent")),
        "request_date": _parse_date(data.get("request_date"), default=today_bangkok(), what="วันที่แจ้ง").isoformat(),
    }
    return supabase.insert("repair_requests", [row])[0]


def edit(request_id: str, data: dict) -> dict:
    """Fix a typo / flip urgent. Only the descriptive fields -- status
    changes go through act() so the rules are enforced."""
    values = {}
    if "problem" in data:
        problem = _clean(data["problem"], MAX_PROBLEM_CHARS)
        if not problem:
            raise InvalidRequest("ปัญหาห้ามว่าง")
        values["problem"] = problem
    if "reported_by" in data:
        values["reported_by"] = _clean(data["reported_by"], MAX_NAME_CHARS) or None
    if "urgent" in data:
        values["urgent"] = bool(data["urgent"])
    if not values:
        raise InvalidRequest("ไม่มีอะไรให้แก้")
    rows = supabase.update("repair_requests", {"id": f"eq.{request_id}"}, values)
    if not rows:
        raise InvalidRequest("ไม่พบงานนี้")
    return rows[0]


def act(request_id: str, action: str, **fields) -> dict:
    current = get(request_id)
    if current is None:
        raise InvalidRequest("ไม่พบงานนี้")
    values = apply_action(current, action, **fields)
    # Conditional on the status we read: if it changed in between (double
    # click, a second tab), nothing is written and the page refreshes.
    rows = supabase.update("repair_requests", {"id": f"eq.{request_id}", "status": f"eq.{current['status']}"}, values)
    if not rows:
        raise StaleRequest()
    return rows[0]


def known_fixers() -> list[str]:
    """Names/shops seen before, most recent first -- suggestions for the
    'who fixed it' box. Includes bill vendors so a shop you've already
    saved a bill from is one tap away."""
    seen: dict[str, None] = {}
    for table, column in (("repair_requests", "fixed_by"), ("maintenance_events", "vendor"), ("maintenance_events", "performed_by")):
        for r in supabase.select(table, {"select": column, column: "not.is.null", "order": "created_at.desc" if table == "maintenance_events" else "updated_at.desc", "limit": "200"}):
            name = (r.get(column) or "").strip()
            if name:
                seen.setdefault(name, None)
    return list(seen)
