"""
The home page (หน้าแรก): one screen that says what needs the owner today, across everything the app knows.

It is a first draft built to GROW: every card is one small function returning a plain dict, the page asks for all of them
in ONE request (the server reads the tables side by side, so a slow link does not pay for ten round trips), and a card that
cannot be built (its table is missing, or the database hiccupped) shows as "not available" without taking the page down.
To add a card: write a function here, add it to CARDS, draw it in static/home.js. The ideas not built yet are in HOME_SOON.

The pure parts (build_* functions) take rows and return numbers -- unit-tested; the loaders only fetch rows.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Callable, Optional

from app import machinery, mgr, mgr_jobs, purchasing, stock, supabase

logger = logging.getLogger("home")

CACHE_SECONDS = 20
AT_SHOP_SHOWN = 5
LOW_STOCK_SHOWN = 6
LATEST_SHOWN = 4
RECENT_DAYS = 30

# Ideas that belong on this page later. Shown as dashed "coming soon" cards so the owner can see where they will go and say
# which come first; `needs` is what has to exist before the card can show real numbers.
HOME_SOON = [
    {"key": "ice", "title": "ผลิต / ขายน้ำแข็ง", "blurb": "ผลิตวันนี้ ขายวันนี้ สต็อกน้ำแข็งในห้องเย็น เทียบสัปดาห์ก่อน", "needs": "บันทึกยอดผลิตและยอดขายรายวัน"},
    {"key": "money", "title": "สลิป / การเงิน", "blurb": "เงินเข้า-ออกวันนี้ ค่าซ่อม+ค่าอะไหล่รวมเป็นต้นทุนต่อเดือน", "needs": "ระบบอ่านสลิป (ยังไม่ได้สร้าง)"},
    {"key": "gmp", "title": "ตรวจโรงงาน / GMP", "blurb": "รายการตรวจประจำวัน-สัปดาห์ ที่ยังไม่ได้ทำ และใบรับรองที่ใกล้หมดอายุ", "needs": "รายการตรวจและวันหมดอายุ"},
    {"key": "docs", "title": "เอกสารรถ", "blurb": "ภาษี · พ.ร.บ. · ประกัน ใกล้ครบกำหนดกี่คัน", "needs": "วันครบกำหนดของแต่ละคัน (ใส่ที่ทะเบียนรถ)"},
    {"key": "drivers", "title": "พนักงานขับรถ", "blurb": "ใบขับขี่ใกล้หมดอายุ รถที่ยังไม่มีคนขับประจำ", "needs": "วันหมดอายุใบขับขี่ (มีตารางคนขับแล้ว)"},
    {"key": "orders", "title": "ใบสั่งซื้อ", "blurb": "ของที่สั่งแล้วยังไม่มา เทียบกับสต็อกที่ต่ำ", "needs": "ใบสั่งซื้อ (ยังไม่ได้สร้าง)"},
]


def month_start(d: date) -> date:
    return d.replace(day=1)


def prev_month_start(d: date) -> date:
    return month_start(month_start(d) - timedelta(days=1))


def _num(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _short(text, limit: int = 70) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _day(v) -> Optional[date]:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


# ------------------------------------------------------------------ pure builders
def build_repairs(requests: list[dict]) -> dict:
    """Where the repair board stands right now (only requests still being worked on)."""
    live = [r for r in requests if r.get("status") in ("open", "at_shop", "awaiting_bill")]
    at_shop = sorted((r for r in live if r["status"] == "at_shop"), key=lambda r: -(r.get("days_at_shop") or 0))
    awaiting = [r for r in live if r["status"] == "awaiting_bill"]
    return {
        "ready": True,
        "open": sum(1 for r in live if r["status"] == "open"),
        "at_shop": len(at_shop),
        "awaiting_bill": len(awaiting),
        "longest_at_shop_days": max((r.get("days_at_shop") or 0 for r in at_shop), default=0),
        "oldest_awaiting_days": max((r.get("days_in_status") or 0 for r in awaiting), default=0),
        "at_shop_list": [{"truck": r.get("truck_number"), "plate": r.get("plate"), "shop": r.get("shop"), "days": r.get("days_at_shop") or 0,
                          "problem": (r.get("problem") or "")[:80]} for r in at_shop[:AT_SHOP_SHOWN]],
    }


def build_spend(rows: list[dict], key: str, date_key: str, today: date) -> dict:
    """This month and last month's total of `key` over rows dated `date_key` (rows older than last month are ignored)."""
    this, last = month_start(today), prev_month_start(today)
    out = {"this": 0.0, "last": 0.0, "count_this": 0, "count_last": 0}
    for r in rows:
        d = _day(r.get(date_key))
        if not d:
            continue
        which = "this" if month_start(d) == this else "last" if month_start(d) == last else None
        if which:
            out[which] += _num(r.get(key))
            out["count_" + which] += 1
    return {"this": round(out["this"], 2), "last": round(out["last"], 2), "count_this": out["count_this"], "count_last": out["count_last"]}


def build_stock(items: list[dict], have: dict) -> dict:
    """Things below the minimum the stock page says to keep ("ควรสั่งเพิ่ม"), the emptiest first."""
    low = []
    for it in items:
        if it.get("min_qty") is None or it.get("active") is False:
            continue
        qty = have.get(it["id"], 0)
        if qty < float(it["min_qty"]):
            low.append({"name": it["name"], "unit": it.get("unit"), "on_hand": qty, "min_qty": float(it["min_qty"]),
                        "short": float(it["min_qty"]) - qty})
    low.sort(key=lambda x: (x["on_hand"] / x["min_qty"] if x["min_qty"] else 0, x["name"]))
    return {"ready": True, "tracked": sum(1 for it in items if it.get("min_qty") is not None and it.get("active") is not False),
            "low_count": len(low), "low": low[:LOW_STOCK_SHOWN]}


def build_machinery(machines: list[dict], events: list[dict], today: date) -> dict:
    by_id = {m["id"]: machinery.machine_name(m) for m in machines}
    recent = [e for e in events if (_day(e.get("event_date")) or date.min) >= today - timedelta(days=RECENT_DAYS)]
    ordered = sorted(events, key=lambda e: str(e.get("event_date") or ""), reverse=True)
    return {"ready": True, "machines": len(machines), "events_recent": len(recent), "recent_days": RECENT_DAYS,
            "latest": [{"date": e.get("event_date"), "kind": e.get("kind"), "summary": _short(e.get("summary")),
                        "where": by_id.get(e.get("machine_id")) or e.get("area") or e.get("equipment") or ""} for e in ordered[:LATEST_SHOWN]]}


# ------------------------------------------------------------------ loaders (thin: fetch rows, call a builder)
def _repairs(today: date) -> dict:
    rows = supabase.select_all("v_requests", {"select": "status,truck_number,plate,shop,problem,days_at_shop,days_in_status",
                                              "status": "in.(open,at_shop,awaiting_bill)", "order": "request_no.asc"})
    return build_repairs(rows)


def _spend(today: date) -> dict:
    since = prev_month_start(today).isoformat()
    repairs = supabase.select_all("v_events", {"select": "event_id,event_date,total_cost", "event_date": f"gte.{since}", "order": "event_id.asc"})
    out = {"ready": True, "repairs": build_spend(repairs, "total_cost", "event_date", today), "purchases": {"ready": False}}
    if purchasing.has_v16():
        bills = supabase.select_all("purchase_bills", {"select": "id,bill_date,total", "bill_date": f"gte.{since}", "order": "id.asc"})
        out["purchases"] = {"ready": True, **build_spend(bills, "total", "bill_date", today)}
    return out


def _stock(today: date) -> dict:
    if not purchasing.has_v16():
        return {"ready": False}
    items = supabase.select_all("stock_items", {"select": "id,name,unit,min_qty,active", "min_qty": "not.is.null", "order": "name.asc"})
    return build_stock(items, stock.on_hand(stock._moves()) if items else {})


def _machinery(today: date) -> dict:
    if not machinery.has_v9():
        return {"ready": False}
    machines = supabase.select_all("machines", {"select": "id,name,kind,number,make,capacity_tons", "active": "eq.true", "order": "id.asc"})
    events = supabase.select_all("machine_events", {"select": "event_date,machine_id,area,equipment,kind,summary", "order": "event_date.desc,id.asc"})
    return build_machinery(machines, events, today)


def _today(today: date) -> dict:
    return mgr_jobs.today_summary()


# name -> loader. The page draws what it finds under the same names.
CARDS: dict[str, Callable[[date], dict]] = {"today": _today, "repairs": _repairs, "spend": _spend, "stock": _stock, "machinery": _machinery}

_cache: dict = {"at": 0.0, "value": None}
_lock = threading.Lock()


def _guarded(name: str, fn: Callable[[date], dict], today: date) -> dict:
    try:
        return fn(today)
    except Exception:                                # one card failing must not blank the page
        logger.exception("home card %s failed", name)
        return {"ready": False, "error": True}


def load(*, fresh: bool = False) -> dict:
    """Every card, built side by side. Cached for a few seconds (the page is opened often, the numbers move slowly)."""
    with _lock:
        if not fresh and _cache["value"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return _cache["value"]
    today = mgr.today_th()
    with ThreadPoolExecutor(max_workers=len(CARDS)) as pool:
        futures = {name: pool.submit(_guarded, name, fn, today) for name, fn in CARDS.items()}
        out = {name: f.result() for name, f in futures.items()}
    out.update(as_of=today.isoformat(), soon=HOME_SOON)
    with _lock:
        _cache.update(at=time.time(), value=out)
    return out
