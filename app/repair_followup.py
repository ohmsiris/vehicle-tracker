"""
After a car comes back from a shop, and while it is away -- the follow-ups of the repair board, kept in step with the
manager's board (บอร์ดของฉัน):

* **Ask the driver** (`check_status`): the day after a car came back from a shop, ring the driver and ask if the car is
  fine (it sometimes returns with the old fault, or a new one, and nobody says). The answer is ok / problem / skipped;
  "problem" opens a new repair request for the truck. It lives on the same "รถกลับมาแล้ว" card -- no new column.
* **Chase the bill**: the same day a reminder to chase the shop's bill.
* **Progress notes** while the car is at a shop ("รออะไหล่ 2 วัน"): the latest one shows on the card, with an optional
  reminder to ask the shop again.

Each of these also makes a ticket on the manager's board (mgr_tickets.request_id + request_kind), so it shows up on the
day it is due. The two boards stay in step both ways: answering the question on the repair board closes the ticket;
finishing the ticket on the manager's board answers the question; a bill that is saved (or "no bill", or cancelling the
job) closes the bill ticket. Tickets are made best-effort -- the repair board must keep working if the manager's board
tables are missing.

Pure rules (check_values, clean_answer, clean_update, ticket_specs) are unit-tested; the database calls are thin.
Needs schema_v17.sql.
"""
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app import fleet, mgr, shops, supabase

logger = logging.getLogger("vehicle_tracker.followup")

CHECK_DAYS = 1                       # the day after the car came back
ANSWERS = ("ok", "problem", "skipped")
MAX_NOTE = 500
MAX_REMIND_DAYS = 30
NEEDS_V17 = "ต้องรัน schema_v17.sql ใน Supabase ก่อน จึงจะใช้การติดตามหลังรถกลับมาได้"
OPEN_TICKET = "in.(todo,doing,waiting)"


class InvalidFollowup(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


# --------------------------------------------------------------- pure rules
def check_values(returned_on: date) -> dict:
    """The columns that start the "ask the driver" step for a car that came back on `returned_on`."""
    return {"check_status": "pending", "check_due": (returned_on + timedelta(days=CHECK_DAYS)).isoformat(),
            "checked_at": None, "check_note": None}


def clear_check_values() -> dict:
    return {"check_status": None, "check_due": None, "checked_at": None, "check_note": None}


def clean_answer(result, note) -> tuple[str, Optional[str]]:
    if result not in ANSWERS:
        raise InvalidFollowup("เลือกว่ารถปกติ ยังมีปัญหา หรือข้าม")
    text = re.sub(r"\s+", " ", str(note or "")).strip()[:MAX_NOTE]
    if result == "problem" and not text:
        raise InvalidFollowup("บอกด้วยว่ามีปัญหาอะไร (จะเปิดเป็นงานซ่อมใหม่)")
    return result, text or None


def clean_update(note, remind_days) -> tuple[str, Optional[int]]:
    text = re.sub(r"\s+", " ", str(note or "")).strip()[:MAX_NOTE]
    if not text:
        raise InvalidFollowup("พิมพ์ความคืบหน้าด้วย")
    if remind_days in (None, "", 0, "0"):
        return text, None
    try:
        n = int(remind_days)
    except (TypeError, ValueError):
        raise InvalidFollowup("จำนวนวันที่จะเตือนไม่ถูกต้อง")
    if not 1 <= n <= MAX_REMIND_DAYS:
        raise InvalidFollowup(f"เตือนได้ภายใน {MAX_REMIND_DAYS} วัน")
    return text, n


def truck_label(vehicle: dict) -> str:
    number, plate = vehicle.get("vehicle_number"), vehicle.get("plate_number")
    return " · ".join(x for x in [f"#{number}" if number else None, plate] if x) or "รถ"


def ticket_specs(kind: str, request: dict, vehicle: dict, *, due: date, call: Optional[dict] = None,
                 shop_phone: Optional[str] = None, note: Optional[str] = None) -> dict:
    """The manager-board ticket for one follow-up (column values for mgr_tickets)."""
    label, shop = truck_label(vehicle), request.get("shop") or request.get("fixed_by") or ""
    base = {"request_id": request["id"], "request_kind": kind, "vehicle_id": request.get("vehicle_id"), "due_date": due.isoformat(),
            "place": vehicle.get("branch") if vehicle.get("branch") in ("สระบุรี", "แก่งคอย") else None,
            "type_source": "rule", "urgent": False, "status": "todo", "checklist": []}
    problem = f"งาน: {request.get('problem')}" if request.get("problem") else None
    if kind == "driver_check":
        return {**base, "type_key": "fix", "title": f"โทรถามคนขับ {label}: รถปกติไหม",
                "notes": "\n".join(x for x in [f"รถกลับจาก {shop}" if shop else None, problem, "ถามว่าอาการเดิมหายไหม มีอะไรใหม่ไหม"] if x),
                "waiting_on": call["name"] if call else None, "waiting_phone": call["phone"] if call else None}
    if kind == "bill_chase":
        return {**base, "type_key": "chase", "title": f"ตามบิล {label}" + (f" จาก {shop}" if shop else ""),
                "notes": "\n".join(x for x in [problem, "ตามบิลจากร้านแล้วบันทึกในระบบ"] if x),
                "waiting_on": shop or None, "waiting_phone": shop_phone}
    return {**base, "type_key": "chase", "title": f"ถามความคืบหน้า {label}" + (f" ที่ {shop}" if shop else ""),
            "notes": "\n".join(x for x in [problem, f"บันทึกล่าสุด: {note}" if note else None] if x),
            "waiting_on": shop or None, "waiting_phone": shop_phone}


# ------------------------------------------------------------------ database
_probe = {"at": 0.0, "ok": False}


def has_v17() -> bool:
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        supabase.select("repair_requests", {"select": "check_status,check_due", "limit": "1"})
        supabase.select("repair_request_updates", {"select": "id", "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _manager_ready() -> bool:
    try:
        if not mgr.has_v12():
            return False
        supabase.select("mgr_tickets", {"select": "request_id,request_kind", "limit": "1"})
        return True
    except supabase.SupabaseError:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vehicle(vehicle_id) -> dict:
    if not vehicle_id:
        return {}
    rows = supabase.select("vehicles", {"select": "id,plate_number,vehicle_number,branch,usage,main_driver_id", "id": f"eq.{vehicle_id}", "limit": "1"})
    return rows[0] if rows else {}


def _call_for(request: dict, vehicle: dict) -> Optional[dict]:
    if not fleet.has_v6():
        return None
    try:
        drivers = fleet.list_drivers(include_inactive=False)
        return fleet.call_target(request.get("reported_by"), vehicle.get("usage"), next((d for d in drivers if d["driver_id"] == vehicle.get("main_driver_id")), None), drivers)
    except supabase.SupabaseError:
        return None


def _shop_phone(shop: Optional[str]) -> Optional[str]:
    contacts = ((shops.directory().get(shop or "") or {}).get("contacts") or [])
    return contacts[0]["phone"] if contacts else None


def _make_ticket(spec: dict) -> None:
    """Inserts one ticket; skips it when the request already has an OPEN ticket of that kind (so a retry never doubles up)."""
    existing = supabase.select("mgr_tickets", {"select": "id", "request_id": f"eq.{spec['request_id']}", "request_kind": f"eq.{spec['request_kind']}",
                                               "status": OPEN_TICKET, "limit": "1"})
    if existing:
        return
    types = {t["key"] for t in mgr.list_types(include_inactive=True)}
    if spec.get("type_key") not in types:
        spec = {**spec, "type_key": None}
    supabase.insert("mgr_tickets", [spec])


def close_tickets(request_ids: list[str], kinds: tuple, status: str) -> None:
    """Close the open manager tickets of these requests (done / cancelled)."""
    if not request_ids or status not in ("done", "cancelled") or not _manager_ready():
        return
    now = _now()
    supabase.update("mgr_tickets", {"request_id": f"in.({','.join(request_ids)})", "request_kind": f"in.({','.join(kinds)})", "status": OPEN_TICKET},
                    {"status": status, "done_at": now, "status_changed_at": now, "remind_on": None})


def after_action(before: dict, after: dict, action: str) -> None:
    """Called by repair_requests.act once a status change is saved: the manager-board side of it. Best effort."""
    if not has_v17():
        return
    try:
        rid = after["id"]
        if action in ("returned", "returned_no_bill"):
            close_tickets([rid], ("shop_update",), "done")
            if _manager_ready():
                vehicle = _vehicle(after.get("vehicle_id"))
                due = date.fromisoformat(after["check_due"]) if after.get("check_due") else date.fromisoformat(after["fixed_date"]) + timedelta(days=CHECK_DAYS)
                call = _call_for(after, vehicle)
                _make_ticket(ticket_specs("driver_check", after, vehicle, due=due, call=call))
                if action == "returned":
                    _make_ticket(ticket_specs("bill_chase", after, vehicle, due=due, shop_phone=_shop_phone(after.get("shop") or after.get("fixed_by"))))
        elif action == "no_bill":
            close_tickets([rid], ("bill_chase",), "done")
        elif action == "cancel":
            close_tickets([rid], ("driver_check", "bill_chase", "shop_update"), "cancelled")
        elif action == "reopen":
            close_tickets([rid], ("driver_check", "bill_chase"), "cancelled")
        elif action in ("send_to_shop",):
            pass
    except supabase.SupabaseError:
        logger.exception("could not sync the manager board for request %s (%s)", after.get("id"), action)


def on_bill_saved(request_ids: list[str]) -> None:
    """A bill (or manual entry) closed these requests: the 'chase the bill' tickets are done. Best effort."""
    ids = [r for r in (request_ids or []) if r]
    if not ids or not has_v17():
        return
    try:
        close_tickets(ids, ("bill_chase",), "done")
    except supabase.SupabaseError:
        logger.exception("could not close the bill tickets for %s", ids)


def on_ticket_closed(ticket: dict, status: str) -> None:
    """The manager finished / cancelled a ticket that belongs to a repair request: finishing 'ask the driver' answers the
    question on the repair board (ok -- or skipped when cancelled), unless it was already answered there."""
    if not ticket.get("request_id") or ticket.get("request_kind") != "driver_check" or status not in ("done", "cancelled") or not has_v17():
        return
    try:
        supabase.update("repair_requests", {"id": f"eq.{ticket['request_id']}", "check_status": "eq.pending"},
                        {"check_status": "ok" if status == "done" else "skipped", "checked_at": _now(),
                         "check_note": "ปิดจากบอร์ดของฉัน" if status == "done" else None})
    except supabase.SupabaseError:
        logger.exception("could not answer the check of request %s from its ticket", ticket.get("request_id"))


def answer_check(request_id: str, result, note=None) -> dict:
    """The driver was asked. ok / skipped just record it; problem also opens a new repair request for the truck."""
    if not has_v17():
        raise InvalidFollowup(NEEDS_V17)
    result, text = clean_answer(result, note)
    rows = supabase.select("repair_requests", {"select": "*", "id": f"eq.{request_id}", "limit": "1"})
    if not rows:
        raise InvalidFollowup("ไม่พบงานนี้")
    req = rows[0]
    if req.get("check_status") != "pending":
        raise InvalidFollowup("งานนี้ไม่ได้รอถามคนขับแล้ว — รีเฟรชหน้าจอ")
    new_request = None
    if result == "problem":
        from app import repair_requests                    # (lazy: repair_requests calls back into this module)
        new_request = repair_requests.create({"vehicle_id": req["vehicle_id"], "problem": f"กลับจากซ่อมแล้วยังมีปัญหา: {text}",
                                              "reported_by": req.get("reported_by"), "urgent": False})
    supabase.update("repair_requests", {"id": f"eq.{request_id}", "check_status": "eq.pending"},
                    {"check_status": result, "checked_at": _now(), "check_note": text})
    if _manager_ready():
        close_tickets([request_id], ("driver_check",), "done" if result != "skipped" else "cancelled")
    return {"check_status": result, "new_request_id": (new_request or {}).get("id")}


# ---------------------------------------------------------- progress notes (the car is at a shop)
def list_updates(request_id: str) -> list[dict]:
    if not has_v17():
        return []
    return supabase.select_all("repair_request_updates", {"select": "id,note,created_at", "request_id": f"eq.{request_id}", "order": "created_at.desc"})


def add_update(request_id: str, note, remind_days=None) -> list[dict]:
    if not has_v17():
        raise InvalidFollowup(NEEDS_V17)
    text, days = clean_update(note, remind_days)
    rows = supabase.select("repair_requests", {"select": "*", "id": f"eq.{request_id}", "limit": "1"})
    if not rows:
        raise InvalidFollowup("ไม่พบงานนี้")
    req = rows[0]
    if req["status"] != "at_shop":
        raise InvalidFollowup("บันทึกความคืบหน้าได้เฉพาะรถที่อยู่ที่อู่")
    supabase.insert("repair_request_updates", [{"request_id": request_id, "note": text}])
    if days and _manager_ready():
        try:
            today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
            vehicle = _vehicle(req.get("vehicle_id"))
            # one "ask again" ticket at a time: the new reminder replaces the old one
            close_tickets([request_id], ("shop_update",), "cancelled")
            _make_ticket(ticket_specs("shop_update", req, vehicle, due=today + timedelta(days=days), shop_phone=_shop_phone(req.get("shop")), note=text))
        except supabase.SupabaseError:
            logger.exception("could not make the reminder ticket for request %s", request_id)
    return list_updates(request_id)


def delete_update(update_id: str) -> None:
    if not has_v17():
        raise InvalidFollowup(NEEDS_V17)
    supabase.delete("repair_request_updates", {"id": f"eq.{update_id}"})
