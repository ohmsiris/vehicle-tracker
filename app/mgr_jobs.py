"""
The manager's reminders, pushed to LINE as plain text (one way: nothing reads replies, no AI):

  * a morning summary at the time set in the settings (default 08:00): what is overdue, due today, which follow-ups
    have come up, what is urgent, and how much is in progress
  * a reminder at a ticket's own time ("due 14:00" -> pushed at 14:00)

It also makes the recurring tickets that have come due, every minute -- so the board is right even if nobody opens it.

Each push is "claimed" in mgr_notifications first (a unique key), so a restart or a second instance can never send the
same message twice. build_digest() and the rules for what is sent are pure functions, unit-tested; run_tick() takes the
database and LINE as arguments so the whole decision can be tested without either.
"""
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from app import line_push, mgr, supabase

logger = logging.getLogger("vehicle_tracker.mgr_jobs")

TH_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
TIMED_WINDOW = timedelta(hours=3)         # a timed reminder that is more than this late (the app was off) is dropped
TICK_SECONDS = 60
MAX_LINES = 12                            # per section: the rest is "... and N more"


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


def build_digest(tickets: list[dict], today: date) -> Optional[str]:
    """The morning summary, or None when there is nothing to say. `tickets` = the active ones (todo / doing / waiting)."""
    active = [t for t in tickets if t.get("status") in mgr.ACTIVE]

    def key(t):
        return (not t.get("urgent"), str(t.get("due_time") or "99:99"), t.get("seq") or 0)

    overdue = sorted([t for t in active if (d := _parse(t.get("due_date"))) and d < today], key=lambda t: (_parse(t["due_date"]), key(t)))
    due_today = sorted([t for t in active if _parse(t.get("due_date")) == today], key=key)
    listed = {t["id"] for t in overdue + due_today}
    follow = sorted([t for t in active if t["id"] not in listed and (d := _parse(t.get("remind_on"))) and d <= today], key=key)
    listed |= {t["id"] for t in follow}
    urgent = sorted([t for t in active if t.get("urgent") and t["id"] not in listed], key=key)
    doing = [t for t in active if t.get("status") == "doing"]
    if not (overdue or due_today or follow or urgent):
        return None
    lines = [f"สรุปงานวันนี้ · {be(today)}", ""]
    lines += _section("เลยกำหนด", overdue, today, show_due=True)
    lines += _section("วันนี้", due_today, today)
    lines += _section("ถึงวันติดตาม", follow, today)
    lines += _section("ด่วน", urgent, today)
    if doing:
        lines.append(f"กำลังทำอยู่ {len(doing)} เรื่อง")
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


def run_tick(now: datetime, *, settings: dict, active_tickets: Callable[[], list[dict]], claim: Callable[[str], bool],
             release: Callable[[str], None], push: Callable[[str], None]) -> dict:
    """One pass of the reminder job. Returns what it did: {"digest": bool, "timed": n}."""
    done = {"digest": False, "timed": 0}
    wants_digest, wants_timed = settings.get("digest_enabled", True), settings.get("timed_enabled", True)
    tickets = active_tickets() if (wants_digest or wants_timed) else []
    if wants_digest:
        h, m = (int(x) for x in str(settings.get("digest_time", "08:00")).split(":")[:2])
        if (now.hour, now.minute) >= (h, m):
            key = f"digest:{now.date().isoformat()}"
            text = build_digest(tickets, now.date())
            if text and claim(key):
                try:
                    push(text)
                    done["digest"] = True
                except Exception:
                    release(key)                                  # try again next minute
                    logger.exception("morning summary not sent")
    if wants_timed:
        for t in due_timed(tickets, now):
            key = f"time:{t['id']}:{now.date().isoformat()}"
            if claim(key):
                try:
                    push(timed_text(t))
                    done["timed"] += 1
                except Exception:
                    release(key)
                    logger.exception("timed reminder not sent")
    return done


# ------------------------------------------------------------------ wiring
def _claim(key: str) -> bool:
    """True if this process is the one to send `key` (the first to insert it)."""
    return bool(supabase.insert("mgr_notifications", [{"key": key}], on_conflict="key", ignore_duplicates=True))


def _release(key: str) -> None:
    supabase.delete("mgr_notifications", {"key": f"eq.{key}"})


def _active() -> list[dict]:
    return supabase.select_all("mgr_tickets", {"select": "*", "status": "in.(todo,doing,waiting)", "order": "seq.asc"})


def tick() -> dict:
    """Recurring tickets, then (if LINE is set up) the reminders. Never raises."""
    result = {"created": 0, "digest": False, "timed": 0}
    try:
        if not mgr.has_v12():
            return result
        result["created"] = mgr.generate_recurring()
        if line_push.configured():
            now = datetime.now(mgr.THAI_TZ)
            result.update(run_tick(now, settings=mgr.get_settings(), active_tickets=_active, claim=_claim, release=_release, push=line_push.push_text))
    except Exception:
        logger.exception("manager job tick failed")
    return result


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
    logger.info("manager reminders: started (LINE %s)", "on" if line_push.configured() else "not configured")
    return True
