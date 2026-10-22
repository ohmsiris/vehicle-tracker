"""
The manager's own ticket board (ผู้จัดการ): the same idea as the repair board, for the owner's own work -- chasing
suppliers, money, fixing things, dealing with the outside world, rounds and inspections ...

    ต้องทำ (todo) -> กำลังทำ (doing) -> รอคนอื่น / รอผล (waiting) -> เสร็จ (done)        (or ยกเลิก)

* "waiting" holds who or what it is waiting on and a follow-up date: that day the ticket comes back up.
* A ticket's type is picked by the AI from what was typed (app/mgr_ai.py) unless it was chosen by hand.
* Recurring tickets (every Monday, the 1st and 15th, every 3 months ...) create their tickets by themselves:
  see app/mgr_recurring.py; generate_recurring() is idempotent (one ticket per occurrence).
* app/mgr_jobs.py pushes the morning summary and timed reminders to LINE (plain text, no replies).

Everything that decides whether a change is allowed is a pure function (clean_ticket, transition, ...), unit-tested;
the database calls are thin. Until schema_v12.sql has been run reads come back empty and writes say what is missing.
"""
import re
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app import mgr_ai, mgr_recurring, supabase

STATUSES = ("todo", "doing", "waiting", "done", "cancelled")
ACTIVE = ("todo", "doing", "waiting")
NEEDS_V12 = "ต้องรัน schema_v12.sql ใน Supabase ก่อน จึงจะใช้บอร์ดผู้จัดการได้"
THAI_TZ = timezone(timedelta(hours=7))
FOLLOW_UP_CHOICES = (1, 3, 7)                        # days offered when something is put on "waiting"
RECENT_LIMIT = 40


def today_th() -> date:
    return datetime.now(THAI_TZ).date()


class InvalidTicket(ValueError):
    """User-fixable problem; str() is a Thai message safe to show."""


class NotFound(Exception):
    pass


# ------------------------------------------------------------------ cleaning
def _text(value, limit: int) -> str:
    return re.sub(r"[ \t]+", " ", str(value if value is not None else "")).strip()[:limit]


def _lines(value, limit: int) -> str:
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in str(value if value is not None else "").splitlines()]
    return "\n".join(ln for ln in lines if ln)[:limit]


def _date(value, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        d = date.fromisoformat(str(value).strip())
    except ValueError:
        raise InvalidTicket(f"{what}ไม่ถูกต้อง")
    if d.year >= 2400:
        raise InvalidTicket(f"{what}เป็นปี พ.ศ. — ใส่เป็น ค.ศ. (หรือเลือกจากปฏิทิน)")
    if d.year < 2000 or d.year > 2100:
        raise InvalidTicket(f"{what}ไม่ถูกต้อง")
    return d.isoformat()


def _time(value, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", str(value).strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise InvalidTicket(f"{what}ไม่ถูกต้อง")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _uuid_or_none(value, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise InvalidTicket(f"{what}ไม่ถูกต้อง")


def clean_checklist(value) -> list[dict]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise InvalidTicket("รายการย่อยไม่ถูกต้อง")
    out = []
    for item in value[:30]:
        text = _text(item.get("text") if isinstance(item, dict) else item, 200)
        if text:
            out.append({"text": text, "done": bool(item.get("done")) if isinstance(item, dict) else False})
    return out


def clean_ticket(data: dict, *, existing: Optional[dict] = None, type_keys: set) -> dict:
    """The column values to write for a new ticket (existing=None) or for the fields present in `data` (a partial edit)."""
    creating = existing is None
    values: dict = {}

    def given(k):
        return k in data

    if creating or given("title"):
        title = _text(data.get("title"), 200)
        if not title:
            raise InvalidTicket("ใส่ชื่อเรื่องด้วย")
        values["title"] = title
    if creating or given("notes"):
        values["notes"] = _lines(data.get("notes"), 2000) or None
    if given("type_key") or creating:
        key = _text(data.get("type_key"), 40)
        if key and key != "auto" and key not in type_keys:
            raise InvalidTicket("ประเภทไม่ถูกต้อง")
        values["type_key"] = key if key and key != "auto" else None
        values["type_source"] = "manual" if values["type_key"] else None          # None: still to be decided (the AI)
    if creating or given("place"):
        values["place"] = _text(data.get("place"), 40) or None
    if creating or given("urgent"):
        values["urgent"] = bool(data.get("urgent"))
    if creating or given("due_date"):
        values["due_date"] = _date(data.get("due_date"), "วันกำหนด")
    if creating or given("due_time"):
        values["due_time"] = _time(data.get("due_time"), "เวลา")
    if creating or given("remind_on"):
        values["remind_on"] = _date(data.get("remind_on"), "วันติดตาม")
    if creating or given("waiting_on"):
        values["waiting_on"] = _text(data.get("waiting_on"), 120) or None
    if creating or given("waiting_phone"):
        phone = _text(data.get("waiting_phone"), 30)
        if phone and not re.fullmatch(r"[0-9+\-\s()]{5,30}", phone):
            raise InvalidTicket("เบอร์โทรไม่ถูกต้อง")
        values["waiting_phone"] = phone or None
    if given("checklist"):
        values["checklist"] = clean_checklist(data.get("checklist"))
    elif creating:
        values["checklist"] = []
    if given("vehicle_id"):
        values["vehicle_id"] = _uuid_or_none(data.get("vehicle_id"), "รถ")
    if given("machine_id"):
        values["machine_id"] = _uuid_or_none(data.get("machine_id"), "เครื่องจักร")
    final = {**(existing or {}), **values}
    if final.get("due_time") and not final.get("due_date"):
        raise InvalidTicket("ใส่วันกำหนดก่อน ถึงจะตั้งเวลาได้")
    return values


# --------------------------------------------------------------- state changes
ACTIONS = ("start", "wait", "todo", "resume", "done", "cancel", "reopen")


def transition(ticket: dict, action: str, data: Optional[dict] = None, *, today: date, now: Optional[datetime] = None) -> dict:
    """The column values for one move of a ticket, or InvalidTicket. `wait` takes {waiting_on, waiting_phone, remind_on}."""
    data = data or {}
    now = now or datetime.now(timezone.utc)
    status = ticket["status"]
    stamp = {"status_changed_at": now.isoformat()}
    if action not in ACTIONS:
        raise InvalidTicket("คำสั่งไม่ถูกต้อง")
    if action == "start":
        if status != "todo":
            raise InvalidTicket("เริ่มทำได้เฉพาะงานที่อยู่ในช่อง “ต้องทำ”")
        return {"status": "doing", **stamp}
    if action == "wait":
        if status not in ("todo", "doing", "waiting"):
            raise InvalidTicket("งานนี้ปิดไปแล้ว")
        values = clean_ticket({k: data.get(k) for k in ("waiting_on", "waiting_phone", "remind_on") if k in data}, existing=ticket, type_keys=set())
        if values.get("remind_on") and values["remind_on"] < today.isoformat():
            raise InvalidTicket("วันติดตามต้องไม่ใช่วันที่ผ่านมาแล้ว")
        return {"status": "waiting", **values, **stamp}
    if action in ("todo", "resume"):
        if status not in ACTIVE:
            raise InvalidTicket("งานนี้ปิดไปแล้ว — ใช้ “เปิดใหม่”")
        return {"status": "todo" if action == "todo" else "doing", **stamp}
    if action == "done":
        if status in ("done", "cancelled"):
            raise InvalidTicket("งานนี้ปิดไปแล้ว")
        return {"status": "done", "done_at": now.isoformat(), **stamp}
    if action == "cancel":
        if status in ("done", "cancelled"):
            raise InvalidTicket("งานนี้ปิดไปแล้ว")
        return {"status": "cancelled", "done_at": now.isoformat(), **stamp}
    if status not in ("done", "cancelled"):            # reopen
        raise InvalidTicket("งานนี้ยังไม่ได้ปิด")
    return {"status": "todo", "done_at": None, **stamp}


def move_action(ticket: dict, to: str) -> str:
    """Dragging a card to a column: which action that is."""
    if to == "done":
        return "done"
    if to == "waiting":
        return "wait"
    if to == "doing":
        return "start" if ticket["status"] == "todo" else "resume"
    if to == "todo":
        return "reopen" if ticket["status"] in ("done", "cancelled") else "todo"
    raise InvalidTicket("ช่องไม่ถูกต้อง")


# ------------------------------------------------------------------- settings
DEFAULT_SETTINGS = {"digest_time": "08:00", "digest_enabled": True, "timed_enabled": True}


def clean_settings(data: dict) -> dict:
    out: dict = {}
    if "digest_time" in data:
        t = _time(data["digest_time"], "เวลาส่งสรุป")
        if not t:
            raise InvalidTicket("ใส่เวลาส่งสรุป")
        out["digest_time"] = t
    for k in ("digest_enabled", "timed_enabled"):
        if k in data:
            out[k] = bool(data[k])
    return out


def clean_type(data: dict, *, existing: Optional[dict] = None) -> dict:
    out: dict = {}
    if existing is None or "label" in data:
        label = _text(data.get("label"), 60)
        if not label:
            raise InvalidTicket("ใส่ชื่อประเภทด้วย")
        out["label"] = label
    if "color" in data:
        color = _text(data.get("color"), 9)
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise InvalidTicket("สีต้องเป็นรหัส เช่น #2563eb")
        out["color"] = color.lower()
    if "sort_order" in data:
        try:
            out["sort_order"] = max(0, min(999, int(data["sort_order"])))
        except (TypeError, ValueError):
            raise InvalidTicket("ลำดับต้องเป็นตัวเลข")
    for k in ("active", "default_urgent"):
        if k in data:
            out[k] = bool(data[k])
    return out


# ------------------------------------------------------------------- database
_probe = {"at": 0.0, "ok": False}


def has_v12() -> bool:
    """True once schema_v12.sql has been run (probed, cached 30 s)."""
    now = time.time()
    if now - _probe["at"] < 30:
        return _probe["ok"]
    try:
        for table, col in (("mgr_tickets", "id"), ("mgr_types", "key"), ("mgr_places", "id"), ("mgr_recurring", "id"),
                           ("mgr_settings", "key"), ("mgr_notifications", "key")):
            supabase.select(table, {"select": col, "limit": "1"})
        ok = True
    except supabase.SupabaseError:
        ok = False
    _probe.update(at=now, ok=ok)
    return ok


def _require() -> None:
    if not has_v12():
        raise InvalidTicket(NEEDS_V12)


def list_types(include_inactive: bool = False) -> list[dict]:
    params = {"select": "*", "order": "sort_order.asc,key.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return supabase.select_all("mgr_types", params)


def list_places(include_inactive: bool = False) -> list[dict]:
    params = {"select": "*", "order": "sort_order.asc,label.asc"}
    if not include_inactive:
        params["active"] = "eq.true"
    return supabase.select_all("mgr_places", params)


def get_settings() -> dict:
    rows = supabase.select_all("mgr_settings", {"select": "*", "order": "key.asc"})
    return {**DEFAULT_SETTINGS, **{r["key"]: r["value"] for r in rows}}


def save_settings(data: dict) -> dict:
    _require()
    values = clean_settings(data)
    if values:
        supabase.insert("mgr_settings", [{"key": k, "value": v} for k, v in values.items()], on_conflict="key")
    return get_settings()


def _ticket_row(t: dict, types_by_key: dict) -> dict:
    ty = types_by_key.get(t.get("type_key")) or {}
    checklist = t.get("checklist") or []
    return {**t, "type_label": ty.get("label"), "type_color": ty.get("color"),
            "checklist_done": sum(1 for c in checklist if c.get("done")), "checklist_total": len(checklist)}


def board(*, today: Optional[date] = None) -> dict:
    """Everything the board page needs in one call (and creates any recurring ticket that has come due)."""
    today = today or today_th()
    if not has_v12():
        return {"ready": False, "today": today.isoformat(), "tickets": [], "recent": [], "types": [], "places": [], "settings": DEFAULT_SETTINGS}
    generate_recurring(today=today)
    types = list_types(include_inactive=True)
    by_key = {t["key"]: t for t in types}
    active = supabase.select_all("mgr_tickets", {"select": "*", "status": "in.(todo,doing,waiting)", "order": "seq.asc"})
    recent = supabase.select("mgr_tickets", {"select": "*", "status": "in.(done,cancelled)", "order": "done_at.desc.nullslast", "limit": str(RECENT_LIMIT)})
    return {"ready": True, "today": today.isoformat(), "tickets": [_ticket_row(t, by_key) for t in active],
            "recent": [_ticket_row(t, by_key) for t in recent], "types": [t for t in types if t.get("active", True)],
            "places": list_places(), "settings": get_settings()}


def create_ticket(data: dict) -> dict:
    _require()
    types = list_types(include_inactive=True)
    values = clean_ticket(data, type_keys={t["key"] for t in types})
    if not values.get("type_key"):                      # not chosen by hand: the AI reads the title, and a keyword rule backs it up
        key, source = mgr_ai.classify(values["title"], values.get("notes"), [t for t in types if t.get("active", True)])
        values["type_key"], values["type_source"] = key, source
    row = supabase.insert("mgr_tickets", [values])[0]
    return _ticket_row(row, {t["key"]: t for t in types})


def get_ticket(ticket_id: str) -> dict:
    try:
        tid = str(uuid.UUID(str(ticket_id)))
    except ValueError:
        raise NotFound()
    rows = supabase.select("mgr_tickets", {"select": "*", "id": f"eq.{tid}", "limit": "1"})
    if not rows:
        raise NotFound()
    return rows[0]


def edit_ticket(ticket_id: str, data: dict) -> dict:
    _require()
    existing = get_ticket(ticket_id)
    types = list_types(include_inactive=True)
    values = clean_ticket(data, existing=existing, type_keys={t["key"] for t in types})
    if values.get("type_key") is None and "type_key" in data:         # "auto" on an existing ticket: have the AI look again
        key, source = mgr_ai.classify(values.get("title") or existing["title"], values.get("notes", existing.get("notes")), [t for t in types if t.get("active", True)])
        values["type_key"], values["type_source"] = key, source
    if values:
        supabase.update("mgr_tickets", {"id": f"eq.{existing['id']}"}, values)
    return _ticket_row(get_ticket(existing["id"]), {t["key"]: t for t in types})


def act_ticket(ticket_id: str, action: str, data: Optional[dict] = None) -> dict:
    _require()
    t = get_ticket(ticket_id)
    today = today_th()
    if action == "move":
        action = move_action(t, str((data or {}).get("to") or ""))
    values = transition(t, action, data, today=today)
    supabase.update("mgr_tickets", {"id": f"eq.{t['id']}"}, values)
    if values["status"] in ("done", "cancelled") and t.get("recurring_id"):
        _after_closed(t, today)
    types = {x["key"]: x for x in list_types(include_inactive=True)}
    return _ticket_row(get_ticket(t["id"]), types)


def _after_closed(ticket: dict, today: date) -> None:
    """A recurring ticket in "after_done" mode: the next one is counted from today."""
    rows = supabase.select("mgr_recurring", {"select": "*", "id": f"eq.{ticket['recurring_id']}", "limit": "1"})
    if rows and rows[0].get("mode") == "after_done" and rows[0].get("next_due") is None and rows[0].get("active"):
        nxt = mgr_recurring.next_after_done(rows[0], today)
        supabase.update("mgr_recurring", {"id": f"eq.{rows[0]['id']}"}, {"next_due": nxt.isoformat() if nxt else None})


# --------------------------------------------------------------- recurring
def clean_recurring(data: dict, *, existing: Optional[dict] = None, type_keys: set) -> dict:
    creating = existing is None
    out: dict = {}
    if creating or "title" in data:
        title = _text(data.get("title"), 200)
        if not title:
            raise InvalidTicket("ใส่ชื่อเรื่องด้วย")
        out["title"] = title
    if creating or "notes" in data:
        out["notes"] = _lines(data.get("notes"), 2000) or None
    if creating or "type_key" in data:
        key = _text(data.get("type_key"), 40)
        if key and key not in type_keys:
            raise InvalidTicket("ประเภทไม่ถูกต้อง")
        out["type_key"] = key or None
    if creating or "place" in data:
        out["place"] = _text(data.get("place"), 40) or None
    if creating or "urgent" in data:
        out["urgent"] = bool(data.get("urgent"))
    if creating or "time_of_day" in data:
        out["time_of_day"] = _time(data.get("time_of_day"), "เวลา")
    if creating or "lead_days" in data:
        try:
            lead = int(data.get("lead_days") or 0)
        except (TypeError, ValueError):
            raise InvalidTicket("จำนวนวันที่สร้างล่วงหน้าต้องเป็นตัวเลข")
        if not 0 <= lead <= 60:
            raise InvalidTicket("สร้างล่วงหน้าได้ 0-60 วัน")
        out["lead_days"] = lead
    if creating or "mode" in data:
        mode = data.get("mode") or "calendar"
        if mode not in ("calendar", "after_done"):
            raise InvalidTicket("รูปแบบการสร้างไม่ถูกต้อง")
        out["mode"] = mode
    if creating or "rule" in data:
        try:
            out["rule"] = mgr_recurring.clean_rule(data.get("rule"))
        except mgr_recurring.InvalidRule as exc:
            raise InvalidTicket(str(exc))
    for k in ("start_date", "end_date"):
        if creating or k in data:
            out[k] = _date(data.get(k), "วันเริ่ม" if k == "start_date" else "วันสิ้นสุด")
    if "active" in data:
        out["active"] = bool(data["active"])
    final = {**(existing or {}), **out}
    if final.get("start_date") and final.get("end_date") and final["end_date"] < final["start_date"]:
        raise InvalidTicket("วันสิ้นสุดต้องไม่ก่อนวันเริ่ม")
    return out


def _recurring_row(r: dict, types_by_key: dict) -> dict:
    ty = types_by_key.get(r.get("type_key")) or {}
    return {**r, "type_label": ty.get("label"), "type_color": ty.get("color"), "described": mgr_recurring.describe(r["rule"])}


def list_recurring() -> list[dict]:
    if not has_v12():
        return []
    types = {t["key"]: t for t in list_types(include_inactive=True)}
    rows = supabase.select_all("mgr_recurring", {"select": "*", "order": "created_at.asc"})
    return [_recurring_row(r, types) for r in rows]


def create_recurring(data: dict) -> dict:
    _require()
    types = list_types(include_inactive=True)
    values = clean_recurring(data, type_keys={t["key"] for t in types})
    if not values.get("type_key"):                      # no type chosen: pick one once, here -- every ticket it makes gets it
        values["type_key"], _ = mgr_ai.classify(values["title"], values.get("notes"), [t for t in types if t.get("active", True)])
    start = mgr_recurring.parse(values.get("start_date")) or today_th()
    values["next_due"] = mgr_recurring.first_on_or_after(values["rule"], start).isoformat()
    row = supabase.insert("mgr_recurring", [values])[0]
    generate_recurring()
    return _recurring_row(row, {t["key"]: t for t in types})


def edit_recurring(recurring_id: str, data: dict) -> dict:
    _require()
    rows = supabase.select("mgr_recurring", {"select": "*", "id": f"eq.{recurring_id}", "limit": "1"})
    if not rows:
        raise NotFound()
    types = list_types(include_inactive=True)
    values = clean_recurring(data, existing=rows[0], type_keys={t["key"] for t in types})
    if "type_key" in values and not values["type_key"]:
        values["type_key"], _ = mgr_ai.classify(values.get("title") or rows[0]["title"], values.get("notes", rows[0].get("notes")), [t for t in types if t.get("active", True)])
    if any(k in values for k in ("rule", "start_date", "mode")):       # the grid changed: work out the next occurrence again
        final = {**rows[0], **values}
        start = mgr_recurring.parse(final.get("start_date")) or today_th()
        values["next_due"] = mgr_recurring.first_on_or_after(final["rule"], max(start, today_th())).isoformat()
    if values.get("active") and not rows[0].get("active") and rows[0].get("next_due") is None:
        values["next_due"] = mgr_recurring.first_on_or_after((values.get("rule") or rows[0]["rule"]), today_th()).isoformat()
    if values:
        supabase.update("mgr_recurring", {"id": f"eq.{rows[0]['id']}"}, values)
    generate_recurring()
    return _recurring_row(supabase.select("mgr_recurring", {"select": "*", "id": f"eq.{rows[0]['id']}", "limit": "1"})[0], {t["key"]: t for t in types})


def delete_recurring(recurring_id: str) -> None:
    _require()
    supabase.delete("mgr_recurring", {"id": f"eq.{recurring_id}"})           # its tickets stay, no longer linked


def generate_recurring(*, today: Optional[date] = None) -> int:
    """Creates the tickets of every recurring template that has come due. Safe to call any time, as often as you like:
    the unique index (template, due date) means an occurrence can never be created twice."""
    today = today or today_th()
    if not has_v12():
        return 0
    created = 0
    for r in supabase.select_all("mgr_recurring", {"select": "*", "active": "eq.true", "order": "created_at.asc"}):
        dues, nxt = mgr_recurring.to_create(r, today)
        for due in dues:
            row = {"title": r["title"], "notes": r.get("notes"), "type_key": r.get("type_key"), "type_source": "rule" if r.get("type_key") else "manual",
                   "place": r.get("place"), "urgent": r.get("urgent", False), "due_date": due.isoformat(), "due_time": r.get("time_of_day"),
                   "recurring_id": r["id"]}
            if r.get("mode") == "after_done" and supabase.select("mgr_tickets", {"select": "id", "recurring_id": f"eq.{r['id']}", "status": "in.(todo,doing,waiting)", "limit": "1"}):
                dues, nxt = [], r.get("next_due")                            # the last one is still open
                break
            created += len(supabase.insert("mgr_tickets", [row], on_conflict="recurring_id,due_date", ignore_duplicates=True))
        new_next = nxt.isoformat() if hasattr(nxt, "isoformat") else nxt
        if new_next != r.get("next_due"):
            supabase.update("mgr_recurring", {"id": f"eq.{r['id']}"}, {"next_due": new_next})
    return created


# -------------------------------------------------------- types and places
def save_type(key: Optional[str], data: dict) -> list[dict]:
    _require()
    if key:
        rows = supabase.select("mgr_types", {"select": "*", "key": f"eq.{key}", "limit": "1"})
        if not rows:
            raise NotFound()
        values = clean_type(data, existing=rows[0])
        if values:
            supabase.update("mgr_types", {"key": f"eq.{key}"}, values)
    else:
        values = clean_type(data)
        new_key = "t" + uuid.uuid4().hex[:8]
        values.setdefault("color", "#6b7280")
        values.setdefault("sort_order", 50)
        supabase.insert("mgr_types", [{"key": new_key, **values}])
    return list_types(include_inactive=True)


def save_place(place_id: Optional[str], data: dict) -> list[dict]:
    _require()
    label = _text(data.get("label"), 40)
    if place_id:
        values = {}
        if "label" in data:
            if not label:
                raise InvalidTicket("ใส่ชื่อสถานที่ด้วย")
            values["label"] = label
        if "active" in data:
            values["active"] = bool(data["active"])
        if "sort_order" in data:
            values["sort_order"] = max(0, min(999, int(data["sort_order"])))
        if values:
            supabase.update("mgr_places", {"id": f"eq.{place_id}"}, values)
    else:
        if not label:
            raise InvalidTicket("ใส่ชื่อสถานที่ด้วย")
        supabase.insert("mgr_places", [{"label": label, "sort_order": 50}], on_conflict="label", ignore_duplicates=True)
    return list_places(include_inactive=True)
