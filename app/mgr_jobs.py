"""
What needs the manager's attention today, worked out on the server for the whole site:

  * the numbers beside the menu items, and the summary bar at the top of every page (overdue / due today / follow-ups that
    have come up / urgent -- and the cars coming close to an oil change)
  * the reminders of tickets that have their own time ("due 14:00"), shown on screen when that time comes

There is no LINE and no other push: the phone is on do-not-disturb, so the reminders live where the owner already looks --
in this site, and (if switched on in the settings) as a browser notification while the site is open.

A background loop makes the recurring tickets that have come due, every minute, so the board is right even if nobody
opened it. build_digest(), due_timed() and summarize_tickets() are pure functions, unit-tested.
"""
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

from app import mgr, service_plan, supabase

logger = logging.getLogger("vehicle_tracker.mgr_jobs")

TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
TIMED_WINDOW = timedelta(hours=3)         # a timed reminder that is more than this late (the page was closed) is dropped
TICK_SECONDS = 60
CACHE_SECONDS = 20
MAX_LINES = 12                            # per section: the rest is "... and N more"
PLAN_ITEMS_SHOWN = 12


def be(d: date) -> str:
    return f"{d.day} {TH_MONTHS[d.month - 1]} {d.year + 543}"


def _parse(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _hhmm(value) -> str:
    return str(value)[:5] if value else ""


def _line(t: dict, today: date, *, show_due: bool = False) -> str:
    bits = []
    if t.get("due_time"):
        bits.append(_hhmm(t["due_time"]))
    due = _parse(t.get("due_date"))
    if show_due and due and due < today:
        bits.append(f"เลย {(today - due).days} วัน")
    if t.get("status") == "waiting" and t.get("waiting_on"):
        bits.append(f"รอ {t['waiting_on']}")
    elif t.get("place"):
        bits.append(t["place"])
    tail = f" ({' · '.join(bits)})" if bits else ""
    return f"• {'❗' if t.get('urgent') else ''}{t['title']}{tail}"


def _section(title: str, rows: list[dict], today: date, **kw) -> list[str]:
    if not rows:
        return []
    out = [f"{title} ({len(rows)})"] + [_line(t, today, **kw) for t in rows[:MAX_LINES]]
    if len(rows) > MAX_LINES:
        out.append(f"… และอีก {len(rows) - MAX_LINES} เรื่อง")
    return out + [""]


def _groups(tickets: list[dict], today: date) -> dict:
    """The active tickets sorted into: overdue, due today, follow-ups that have come up, urgent (the rest), in progress."""
    active = [t for t in tickets if t.get("status") in mgr.ACTIVE]

    def key(t):
        return (not t.get("urgent"), str(t.get("due_time") or "99:99"), t.get("seq") or 0)

    overdue = sorted([t for t in active if (d := _parse(t.get("due_date"))) and d < today], key=lambda t: (_parse(t["due_date"]), key(t)))
    due_today = sorted([t for t in active if _parse(t.get("due_date")) == today], key=key)
    listed = {t["id"] for t in overdue + due_today}
    follow = sorted([t for t in active if t["id"] not in listed and (d := _parse(t.get("remind_on"))) and d <= today], key=key)
    listed |= {t["id"] for t in follow}
    urgent = sorted([t for t in active if t.get("urgent") and t["id"] not in listed], key=key)
    return {"active": active, "overdue": overdue, "today": due_today, "follow_ups": follow, "urgent": urgent,
            "doing": [t for t in active if t.get("status") == "doing"]}


def build_digest(tickets: list[dict], today: date) -> Optional[str]:
    """The day's summary as text, or None when there is nothing to say. `tickets` = the active ones (todo / doing / waiting)."""
    g = _groups(tickets, today)
    if not (g["overdue"] or g["today"] or g["follow_ups"] or g["urgent"]):
        return None
    lines = [f"สรุปงานวันนี้ · {be(today)}", ""]
    lines += _section("เลยกำหนด", g["overdue"], today, show_due=True)
    lines += _section("วันนี้", g["today"], today)
    lines += _section("ถึงวันติดตาม", g["follow_ups"], today)
    lines += _section("ด่วน", g["urgent"], today)
    if g["doing"]:
        lines.append(f"กำลังทำอยู่ {len(g['doing'])} เรื่อง")
    return "\n".join(lines).strip()


def due_timed(tickets: list[dict], now: datetime) -> list[dict]:
    """Tickets with their own time today whose time has come (and is not too long ago)."""
    out = []
    for t in tickets:
        if t.get("status") not in mgr.ACTIVE or _parse(t.get("due_date")) != now.date() or not t.get("due_time"):
            continue
        h, m = int(str(t["due_time"])[:2]), int(str(t["due_time"])[3:5])
        at = datetime.combine(now.date(), datetime.min.time()).replace(hour=h, minute=m)
        if timedelta(0) <= now.replace(tzinfo=None) - at <= TIMED_WINDOW:
            out.append(t)
    return sorted(out, key=lambda t: str(t["due_time"]))


def timed_text(t: dict) -> str:
    where = f" · {t['place']}" if t.get("place") else ""
    return f"ถึงเวลา {_hhmm(t['due_time'])}: {t['title']}{where}" + ("\n❗ด่วน" if t.get("urgent") else "")


def summarize_tickets(tickets: list[dict], today: date) -> dict:
    """The numbers and the timed reminders for the top bar / menu badges."""
    g = _groups(tickets, today)
    timed = sorted([t for t in g["active"] if _parse(t.get("due_date")) == today and t.get("due_time")], key=lambda t: str(t["due_time"]))
    return {"ready": True, "total": len(g["active"]), "overdue": len(g["overdue"]), "today": len(g["today"]), "follow_ups": len(g["follow_ups"]),
            "urgent": len(g["urgent"]), "doing": len(g["doing"]), "text": build_digest(tickets, today) or "",
            "timed": [{"id": t["id"], "title": t["title"], "time": _hhmm(t["due_time"]), "place": t.get("place"), "urgent": bool(t.get("urgent"))} for t in timed]}


def summarize_plan(found: list[dict], enabled: bool, soon_days: int) -> dict:
    counts = {"overdue": 0, "due": 0, "soon": 0}
    for x in found:
        counts[x["stage"]] += 1
    return {"enabled": enabled, "soon_days": soon_days, "counts": counts, "total": len(found),
            "items": [{"vehicle_id": x["vehicle_id"], "truck": x["truck"], "label": x["label"], "stage": x["stage"], "days_left": x["days_left"], "km_left": x["km_left"],
                       "km_real": x["km_real"], "key": x["key"]} for x in found[:PLAN_ITEMS_SHOWN]]}


_cache: dict = {"at": 0.0, "value": None}


def today_summary(*, fresh: bool = False) -> dict:
    """Everything the top bar and the menu badges need, cached for a few seconds (the car side reads every truck)."""
    now = time.time()
    if not fresh and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    today = mgr.today_th()
    out = {"as_of": today.isoformat(), "now": datetime.now(mgr.THAI_TZ).strftime("%H:%M"), "mgr": {"ready": False}, "plan": {"enabled": False, "counts": {"overdue": 0, "due": 0, "soon": 0}, "total": 0, "items": []}}
    if mgr.has_v12():
        out["mgr"] = {**summarize_tickets(_active(), today), "timed_enabled": bool(mgr.get_settings().get("timed_enabled", True))}
    if service_plan.has_v10():
        preview = service_plan.reminder_preview(today=today)
        out["plan"] = summarize_plan(preview["items"], preview["enabled"], preview["soon_days"])
    _cache.update(at=now, value=out)
    return out


def _active() -> list[dict]:
    return supabase.select_all("mgr_tickets", {"select": "*", "status": "in.(todo,doing,waiting)", "order": "seq.asc"})


# ------------------------------------------------------------------ background loop
def tick() -> int:
    """Makes the recurring tickets that have come due. Never raises."""
    try:
        return mgr.generate_recurring() if mgr.has_v12() else 0
    except Exception:
        logger.exception("recurring tickets job failed")
        return 0


_started = False


def start_scheduler() -> bool:
    """Starts the once-a-minute loop in a background thread (once). Off when Supabase is not configured or
    DISABLE_SCHEDULER is set (a second copy of the app, a test)."""
    global _started
    if _started or not supabase.configured() or os.environ.get("DISABLE_SCHEDULER"):
        return False
    _started = True

    def loop():
        time.sleep(15)
        while True:
            tick()
            time.sleep(TICK_SECONDS)

    threading.Thread(target=loop, name="mgr-jobs", daemon=True).start()
    logger.info("recurring tickets: loop started")
    return True
