"""
The repair board: follow-up on repair requests from the paper ใบแจ้งซ่อม log.
The paper stays the master record; only repairs worth following up are added
here. A request moves through:

    open --send_to_shop--> at_shop --returned--> awaiting_bill --(bill saved)--> completed
     |                        |  \\--returned, no bill----------------------> completed (no_bill)
     |--fixed (in-house, or the shop was never recorded)--> awaiting_bill
     |--fixed, no bill------------------------------------> completed (no_bill)
     \\--cancel--> cancelled      (cancel also from at_shop / awaiting_bill)
    reopen undoes one step (awaiting_bill -> at_shop when it had been at a shop, else -> open).

Saving a bill (or, later, a manual entry) that lists the request in
`fixes_request_ids` completes it -- that happens inside the database's
save_maintenance_event(), not here. Everything else is done by
apply_action() below, which is pure (no I/O) so every rule is unit-tested.
"""
import time
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from app import repair_followup, shops, supabase

BANGKOK = ZoneInfo("Asia/Bangkok")
ACTIONS = ("fixed_awaiting_bill", "fixed_no_bill", "no_bill", "cancel", "reopen", "send_to_shop", "returned", "returned_no_bill")
V5_ACTIONS = ("send_to_shop", "returned", "returned_no_bill")      # need the at_shop state (schema_v5.sql)
V5_COLUMNS = ("shop", "sent_date", "expected_return", "on_paper")
MAX_PROBLEM_CHARS = 500
MAX_NAME_CHARS = 100
MAX_EXPECTED_DAYS = 120                                           # "back in N days" further out than this is a typo
NEEDS_V5 = "ต้องรัน schema_v5.sql ใน Supabase ก่อน จึงจะใช้ขั้น “อยู่ที่อู่” ได้"


class InvalidRequest(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


def has_v5() -> bool:
    """True once schema_v5.sql has been run (probed, cached 30 s) -- so the app works both before and after it."""
    now = time.time()
    if now - _v5_probe["at"] < 30:
        return _v5_probe["ok"]
    try:
        supabase.select("repair_requests", {"select": ",".join(V5_COLUMNS), "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _v5_probe.update(at=now, ok=ok)
    return ok


_v5_probe = {"at": 0.0, "ok": False}


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


def _parse_planned_date(raw, *, not_before: date, what: str, today: date) -> Optional[date]:
    """An expected-return date: optional, may be in the future (but not absurdly), never before it was sent."""
    if raw in (None, ""):
        return None
    try:
        d = date.fromisoformat(str(raw))
    except ValueError:
        raise InvalidRequest(f"{what}ไม่ถูกต้อง")
    if d.year >= 2400:
        raise InvalidRequest(f"{what}: ปี {d.year} ดูเหมือนเป็น พ.ศ. แต่ช่องนี้ต้องเป็น ค.ศ. (ปี {d.year - 543})")
    if d < not_before:
        raise InvalidRequest(f"{what}ต้องไม่ก่อนวันที่ส่งซ่อม")
    if d > today + timedelta(days=MAX_EXPECTED_DAYS):
        raise InvalidRequest(f"{what}ไกลเกินไป (เกิน {MAX_EXPECTED_DAYS} วัน) ตรวจปีอีกครั้ง")
    return d


def _clean_shop(raw) -> str:
    name = shops.canonical(_clean(raw, MAX_NAME_CHARS))
    if not name:
        raise InvalidRequest("เลือกหรือพิมพ์ชื่ออู่ / ศูนย์ที่ส่งซ่อมด้วย")
    return name


# --------------------------------------------------------------- pure rules
def apply_action(request: dict, action: str, *, fixed_by: str = "", fixed_date=None,
                 reason: str = "", shop: str = "", sent_date=None, expected_return=None,
                 today: Optional[date] = None, with_check: bool = False) -> dict:
    """
    Returns the column values to write for `action` on `request` (a
    repair_requests row), or raises InvalidRequest. Never touches the DB.
    """
    today = today or today_bangkok()
    status = request["status"]
    if action not in ACTIONS:
        raise InvalidRequest("การกระทำไม่ถูกต้อง")

    if action == "send_to_shop":
        # open -> at_shop; from at_shop it re-sends (fix the shop / the date / the expected return)
        if status not in ("open", "at_shop"):
            raise InvalidRequest("ส่งซ่อมได้เฉพาะงานที่ยังรอซ่อมอยู่")
        where = _clean_shop(shop)
        asked = _parse_date(sent_date, default=today, what="วันที่ส่งซ่อม")
        if asked < date.fromisoformat(request["request_date"]):
            raise InvalidRequest("วันที่ส่งซ่อมต้องไม่ก่อนวันที่แจ้ง")
        back = _parse_planned_date(expected_return, not_before=asked, what="วันที่คาดว่าจะรับรถ", today=today)
        return {"status": "at_shop", "shop": where, "sent_date": asked.isoformat(), "expected_return": back.isoformat() if back else None}

    if action in ("returned", "returned_no_bill"):
        if status != "at_shop":
            raise InvalidRequest("รับรถกลับได้เฉพาะงานที่ส่งอู่อยู่")
        sent = date.fromisoformat(request["sent_date"])
        when = _parse_date(fixed_date, default=today, what="วันที่รถกลับมา")
        if when < sent:
            raise InvalidRequest("วันที่รถกลับมาต้องไม่ก่อนวันที่ส่งซ่อม")
        values = {"fixed_by": _clean(fixed_by, MAX_NAME_CHARS) or request.get("shop"), "fixed_date": when.isoformat()}
        if with_check:                                # a car that came back from a shop: ask the driver the day after (schema_v17)
            values.update(repair_followup.check_values(when))
        if action == "returned":
            return {**values, "status": "awaiting_bill"}
        return {**values, "status": "completed", "no_bill": True, "no_bill_reason": _clean(reason, MAX_PROBLEM_CHARS) or None}

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
        if status not in ("open", "at_shop", "awaiting_bill"):
            raise InvalidRequest("ยกเลิกได้เฉพาะงานที่ยังไม่เสร็จ")
        return {"status": "cancelled"}

    # reopen -- undo a mistake. Not possible once a real record (bill or
    # manual entry) is attached: that record would be left pointing at an
    # open request.
    if status == "open":
        raise InvalidRequest("งานนี้เปิดอยู่แล้ว")
    if request.get("fixed_event_id"):
        raise InvalidRequest("งานนี้ปิดด้วยบิลที่บันทึกแล้ว ย้อนกลับไม่ได้")
    if status == "at_shop":                       # recalled from the shop: back to waiting, shop details cleared
        return {"status": "open", "shop": None, "sent_date": None, "expected_return": None}
    cleared = {"fixed_by": None, "fixed_date": None, "no_bill": False, "no_bill_reason": None}
    if with_check:
        cleared.update(repair_followup.clear_check_values())
    if request.get("sent_date"):                  # it had been at a shop: undo only the "back" step
        return {"status": "at_shop", **cleared}
    return {"status": "open", **cleared}


# ---------------------------------------------------------------- database
def list_active() -> list[dict]:
    """Open + awaiting-bill requests. The board groups and sorts them."""
    return supabase.select("v_active_requests", {"select": "*", "order": "urgent.desc,request_date.asc,request_no.asc", "limit": "500"})


def list_recent(limit: int = 50) -> list[dict]:
    """Recently finished (completed or cancelled), newest first -- for the
    'what happened to it' check, including repairs closed with no bill."""
    rows = supabase.select("repair_requests", {
        "select": "id,seq,request_date,status,problem,reported_by,fixed_by,fixed_date,no_bill,no_bill_reason,status_changed_at,fixed_event_id,vehicle_id,plate_text,truck_number_text,"
                  + ("shop,sent_date," if has_v5() else "") + "vehicles(plate_number,vehicle_number,branch)",
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
            "branch": v.get("branch"), "shop": r.get("shop"), "sent_date": r.get("sent_date"),
        })
    return out


def get(request_id: str) -> Optional[dict]:
    rows = supabase.select("repair_requests", {"select": "*", "id": f"eq.{request_id}", "limit": "1"})
    return rows[0] if rows else None


def _norm(text: str) -> str:
    return "".join(str(text or "").split()).lower()


def find_duplicate(requests: list[dict], problem: str) -> Optional[dict]:
    """An unfinished request on the same truck that says the same thing (ignoring spacing and case), or None.
    `requests` are that truck's open / at-shop / waiting-for-bill requests."""
    key = _norm(problem)
    return next((r for r in requests if _norm(r.get("problem")) == key), None)


def create(data: dict) -> dict:
    vehicle_id = data.get("vehicle_id")
    if not vehicle_id or not supabase.select("vehicles", {"select": "id", "id": f"eq.{vehicle_id}", "limit": "1"}):
        raise InvalidRequest("ยังไม่ได้เลือกรถ")
    problem = _clean(data.get("problem"), MAX_PROBLEM_CHARS)
    if not problem:
        raise InvalidRequest("ใส่ปัญหาที่ต้องซ่อมด้วย")
    unfinished = supabase.select("repair_requests", {"select": "seq,problem", "vehicle_id": f"eq.{vehicle_id}",
                                                      "status": "in.(open,at_shop,awaiting_bill)", "limit": "200"})
    if find_duplicate(unfinished, problem):
        raise InvalidRequest(f"รถคันนี้มีงาน “{problem}” อยู่แล้วและยังไม่ปิด — ไม่ได้เพิ่มซ้ำ")
    requested = _parse_date(data.get("request_date"), default=today_bangkok(), what="วันที่แจ้ง")
    row = {
        "vehicle_id": vehicle_id,
        "problem": problem,
        "reported_by": _clean(data.get("reported_by"), MAX_NAME_CHARS) or None,
        "urgent": bool(data.get("urgent")),
        "request_date": requested.isoformat(),
    }
    if data.get("shop"):                          # the truck is already at a shop (an emergency that went straight there)
        row.update(apply_action({"status": "open", "request_date": row["request_date"]}, "send_to_shop",
                                shop=data["shop"], sent_date=data.get("sent_date") or requested.isoformat(),
                                expected_return=data.get("expected_return")))
    if data.get("on_paper") is False:
        row["on_paper"] = False
    if (row.get("shop") or "on_paper" in row) and not has_v5():
        raise InvalidRequest(NEEDS_V5)
    return supabase.insert("repair_requests", [row])[0]


def edit(request_id: str, data: dict) -> dict:
    """Fix a mistake: the problem text, who reported it, urgent, the date it was reported, or the TRUCK (it was
    entered against the wrong one). Only these descriptive fields -- status changes go through act() so the rules
    are enforced."""
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
    if "vehicle_id" in data or "request_date" in data:
        current = get(request_id)
        if current is None:
            raise InvalidRequest("ไม่พบงานนี้")
        if "vehicle_id" in data and data["vehicle_id"] != current.get("vehicle_id"):
            if current.get("fixed_event_id"):
                raise InvalidRequest("งานนี้ปิดด้วยบิลที่บันทึกแล้ว เปลี่ยนรถที่นี่ไม่ได้ — แก้ที่บิลแทน")
            found = supabase.select("vehicles", {"select": "id,active", "id": f"eq.{data['vehicle_id']}", "limit": "1"}) if data["vehicle_id"] else []
            if not found or found[0].get("active", True) is False:
                raise InvalidRequest("ไม่พบรถที่เลือก หรือรถคันนี้เลิกใช้แล้ว")
            unfinished = supabase.select("repair_requests", {"select": "seq,problem", "vehicle_id": f"eq.{data['vehicle_id']}",
                                                              "status": "in.(open,at_shop,awaiting_bill)", "limit": "200"})
            if find_duplicate(unfinished, values.get("problem", current["problem"])):
                raise InvalidRequest("รถคันที่เลือกมีงานเดียวกันนี้อยู่แล้วและยังไม่ปิด")
            values["vehicle_id"] = data["vehicle_id"]
        if "request_date" in data:
            if not data["request_date"]:
                raise InvalidRequest("ใส่วันที่แจ้งซ่อมด้วย")
            when = _parse_date(data["request_date"], default=today_bangkok(), what="วันที่แจ้ง")
            later = [d for d in (current.get("sent_date"), current.get("fixed_date")) if d]
            if later and when.isoformat() > min(later):
                raise InvalidRequest("วันที่แจ้งซ่อมต้องไม่หลังวันที่ส่งซ่อม / ซ่อมเสร็จ")
            values["request_date"] = when.isoformat()
    if not values:
        raise InvalidRequest("ไม่มีอะไรให้แก้")
    rows = supabase.update("repair_requests", {"id": f"eq.{request_id}"}, values)
    if not rows:
        raise InvalidRequest("ไม่พบงานนี้")
    return rows[0]


def act(request_id: str, action: str, **fields) -> dict:
    if action in V5_ACTIONS and not has_v5():
        raise InvalidRequest(NEEDS_V5)
    current = get(request_id)
    if current is None:
        raise InvalidRequest("ไม่พบงานนี้")
    values = apply_action(current, action, with_check=repair_followup.has_v17(), **fields)
    # Conditional on the status we read: if it changed in between (double
    # click, a second tab), nothing is written and the page refreshes.
    rows = supabase.update("repair_requests", {"id": f"eq.{request_id}", "status": f"eq.{current['status']}"}, values)
    if not rows:
        raise StaleRequest()
    repair_followup.after_action(current, rows[0], action)        # the manager board's side of it (best effort)
    return rows[0]


def known_fixers() -> list[str]:
    """Names/shops seen before, most recent first -- suggestions for the
    'who fixed it' box. Includes bill vendors so a shop you've already
    saved a bill from is one tap away. The many ways one outside shop was written on bills ("บริษัท จึงกงเฮงอีซูซุ จำกัด (สาขา…)", "อิซูซุจึงกงเฮง" ...)
    are folded into that shop's own name, so each shop is ONE option; in-house names and people are left as they are."""
    from app import shops
    seen: dict[str, None] = {}
    sources = (("repair_requests", "fixed_by"), ("maintenance_events", "vendor"), ("maintenance_events", "performed_by"))
    answers = supabase.parallel(*[(lambda t=table, c=column: supabase.select(t, {"select": c, c: "not.is.null", "order": "created_at.desc" if t == "maintenance_events" else "updated_at.desc",
                                                                                 "limit": "200"})) for table, column in sources])
    for (table, column), rows in zip(sources, answers):
        for r in rows:
            name = (r.get(column) or "").strip()
            if name:
                shop = shops.canonical(name)
                seen.setdefault(shop if shop and shop != shops.IN_HOUSE else name, None)
    return list(seen)
