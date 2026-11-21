"""
The home page (หน้าแรก, "เช้านี้"): one screen that tells the owner what needs him today and how the business is doing, across
everything the app knows. It is the owner's alone (GET /api/home and /api/home/trends both refuse everyone else, see app/main.py).

ONE view of both branches (owner, 21 Nov 2026): no branch switch, no per-branch blocks. Only the profit card and the profit chart
show the two branches side by side. Three parts, all from load():

  * todo   -- "ต้องดูวันนี้": what is urgent first (bad), then what to chase (warn), then what is coming (info) -- ordered by how
              pressing it is, not by menu
  * tiles  -- the day's numbers; every one carries a chip with the date its data reaches, because some feeds are days behind
  * profit, fresh -- the last COMPLETE accounting month, and how far each feed reaches

trends() is the two charts, asked for after the first paint.

Rules kept here: a truck is always "#<number> <plate> <class> <branch>" (truck_name) -- numbers repeat across branches; dates shown
are DD/MM/YYYY (DD/MM where space is tight), in Thai time; a part month is compared with the SAME days of the month before; the test
route (id 32, branch ทดสอบ) is in no number.

The page only READS: it never calls accounting.report() (which rebuilds entries) nor mgr.board() (which makes recurring tickets).

How it is built: thin loaders fetch rows side by side in a thread pool (the page waits for the slowest, not the sum); small pure
build_* / todo_* / tile_* functions turn rows into the dicts the page draws (unit-tested); every part is guarded, so one that fails
is named in "failed" and the rest of the page still shows. The whole answer is cached for under a minute; the slow, slow-moving
sources (the truck plan, payables, machines, the books) keep their own longer cache -- and ?fresh=true goes past all of them. An
answer with a failed part is kept only a few seconds, so one bad read does not stay on the page.
"""
import logging
import re
import threading
import time
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from statistics import median
from typing import Callable, Optional

from app import accounting, calendar_plan, fleet, licences, machine_plan, machinery, mgr, payables, route_sales, service_plan, shops, supabase, unit_cost

logger = logging.getLogger("home")

HOME_SECONDS = 45                    # the whole /api/home answer
TRENDS_SECONDS = HOME_SECONDS        # the whole /api/home/trends answer: no longer than the page, or the chart lags the tile beside it
                                     # (its two sources keep their own copies, so building it again reads nothing)
RETRY_SECONDS = 5                    # an answer with a failed part is kept only this long: the next look reads again
SLOW_SECONDS = 300                   # sources that are slow to read and move slowly: truck plan, payables, machines, feed dates
BOOKS_SECONDS = 900                  # the ledger (every line of the year): changes when a month is loaded

TEST_BRANCH = "ทดสอบ"                # the owner's test route: in no number
TEST_ROUTE_ID = 32
BRANCHES = accounting.BRANCHES       # the two real branches, in the order the profit card shows them

DANGER_WORDS = ("เบรก", "พวงมาลัย", "ล้อหลุด", "ลูกหมาก", "ยางระเบิด", "ไฟไหม้")
_NOT_DANGER = re.compile(r"น้ำมัน\s*(?:เบรก|พวงมาลัย)")       # brake / steering FLUID on a routine-service list is not a fault
SHOWN = 3                            # how many trucks / tickets one line names before "และอีก N"
LONG_SHOP_DAYS = 30
WARN_SHOP_DAYS = 14
OPEN_LONG_DAYS = 14
SALES_BEHIND_DAYS = 3                # the typed daily sales may be this many days behind yesterday before it is a to-do
METER_STALE_DAYS = 10
NEXT_DAYS = 7
NEXT_SHOWN = 6
SHEETS_AFTER_HOUR = 18               # today's phone sheets count as "not sent" only from this hour (Thai time)
SHEET_DAYS = 14                      # how far back an unsent phone sheet is chased (the ใบขายรถสาย page looks back the same)
FULL_DAY_SHARE = 0.9                 # a day of typed route sales counts once this share of the usual routes is typed
COUNT_SPARE_SALES = True             # the money of a สำรองขับ day is sales (the pay sheet gives it to nobody; the ice was still sold).
                                     # False = the pay sheet's figure: those days' money left out of the tile and the chart
SPARK_DAYS = 21
TREND_DAYS = 35
LEVELS = ("bad", "warn", "info")
OIL_VISIT = "ถ่ายน้ำมันเครื่อง"

TILE_LABELS = {"route_sales": "ขายรถสาย ทุกสาย", "kk_invoice": "แก่งคอย ออกใบกำกับ", "phone_sales": "ขายวันนี้ (มือถือ)",
               "at_shop": "รถอยู่ที่อู่", "repair_mtd": "ค่าซ่อมรถ", "payables": "ค้างจ่ายร้านค้า"}
LIVE_CHIP = {"text": "สด", "tone": "ok"}


# ------------------------------------------------------------------ small helpers (pure)
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
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _thai_day(stamp) -> Optional[date]:
    """The Thai calendar day of a database timestamp (stored in UTC): 2026-11-19T18:30Z is already the 20th here."""
    if not stamp:
        return None
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return _day(stamp)
    return at.astimezone(mgr.THAI_TZ).date() if at.tzinfo else at.date()


def dmy(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def dm(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def month_text(month: str) -> str:
    """'2026-09' -> '09/2026'."""
    return f"{month[5:7]}/{month[:4]}"


def baht(x) -> str:
    return f"{round(_num(x)):,}"


def _count(n: int, limit: int = SHOWN) -> int:
    return max(0, n - limit)


def pct_text(p: float) -> str:
    r = round(p, 1) if abs(p) < 9.95 else round(p)
    return "0%" if r == 0 else f"{'+' if r > 0 else '−'}{abs(r):g}%"                 # a real minus sign, as every number on the page


def delta(now: float, before: float, *, lower_is_good: bool = False) -> Optional[dict]:
    """{"text": "+19%", "tone": good | bad | flat} -- None when there is nothing to compare with."""
    if not before or before <= 0:
        return None
    p = (now - before) / before * 100
    if abs(p) < 0.5:
        return {"text": pct_text(p), "tone": "flat"}
    return {"text": pct_text(p), "tone": "good" if (p < 0) == lower_is_good else "bad"}


def lag_tone(days: int, ok: int, warn: int) -> str:
    return "ok" if days <= ok else "warn" if days <= warn else "bad"


def through_chip(d: date, today: date, ok: int = 1, warn: int = 7) -> dict:
    """The little "ถึง 15/11" beside a number: the day its data reaches, coloured by how far behind that is."""
    return {"text": f"ถึง {dm(d) if d.year == today.year else dmy(d)}", "tone": lag_tone((today - d).days, ok, warn)}


def _plate_key(s) -> str:
    """A plate or truck number without its spaces, hyphens and dots (as vehNorm in fleet.js): "83 2919" is "83-2919"."""
    return re.sub(r"[\s\-_.]", "", str(s or "")).lower()


def truck_name(v: Optional[dict], number=None, plate=None) -> str:
    """How a truck is ALWAYS written: "#31 84-4607 รถใหญ่ สระบุรี". When the number is the plate (or there is none) the plate is
    written once: "83-2919 รถใหญ่ สระบุรี". Never the number alone -- two trucks carry number 9: a truck with no plate on record says so."""
    v = v or {}
    num = " ".join(str(v.get("vehicle_number") or number or "").split())
    plate = " ".join(str(v.get("plate_number") or plate or "").split())
    rest = [str(v.get("vehicle_class") or "").strip(), str(v.get("branch") or "").strip()]
    if not plate:
        return " ".join(x for x in ([f"รถเบอร์ {num} (ไม่พบทะเบียน)"] if num else []) + rest if x) or "รถ (ไม่ระบุคัน)"
    head = f"#{num}" if num and _plate_key(num) != _plate_key(plate) else ""
    return " ".join(x for x in [head, plate] + rest if x)


def item(key: str, level: str, title: str, *, who: str = "", detail: str = "", link: str = "", link_label: str = "") -> dict:
    return {"key": key, "level": level, "title": title, "who": who, "detail": detail, "link": link, "link_label": link_label}


def _more(n: int, unit: str) -> list[str]:
    return [f"และอีก {n} {unit}"] if n > 0 else []


# ------------------------------------------------------------------ the repair board (pure)
def request_rows(requests: list[dict], vehicles: dict, today: date) -> list[dict]:
    """The live repair requests with what the page needs worked out: the truck's name, and the days the repair board itself counts
    (waiting = since the request, at a shop = since it was sent, waiting for the bill = since it came back) -- in Thai days."""
    out = []
    for r in requests:
        if r.get("status") not in ("open", "at_shop", "awaiting_bill"):
            continue
        v = vehicles.get(r.get("vehicle_id"))
        changed = _thai_day(r.get("status_changed_at")) or _day(r.get("request_date")) or today
        asked = _day(r.get("request_date")) or _thai_day(r.get("created_at")) or today
        sent = _day(r.get("sent_date")) or changed
        out.append({"status": r["status"], "urgent": bool(r.get("urgent")), "problem": " ".join(str(r.get("problem") or "").split()),
                    "truck": truck_name(v, r.get("truck_number_text"), r.get("plate_text")),
                    "truck_key": r.get("vehicle_id") or f"{r.get('truck_number_text')}|{r.get('plate_text')}",
                    "shop": r.get("shop") or r.get("fixed_by") or "", "request_date": asked,
                    "days_open": max(0, (today - asked).days), "days_at_shop": max(0, (today - sent).days),
                    "days_in_status": max(0, (today - changed).days)})
    return out


def is_danger(r: dict) -> bool:
    """A request the owner must see today: marked urgent, or its text names a fault that makes the truck unsafe to drive."""
    text = _NOT_DANGER.sub("", r.get("problem") or "")
    return bool(r.get("urgent")) or any(w in text for w in DANGER_WORDS)


def todo_repair_danger(rows: list[dict], today: date) -> list[dict]:
    by_truck: dict = {}
    for r in rows:
        if r["status"] == "open" and is_danger(r):
            by_truck.setdefault(r["truck_key"], []).append(r)
    trucks = [sorted(rs, key=lambda r: (not r["urgent"], -r["days_open"])) for rs in by_truck.values()]
    trucks.sort(key=lambda rs: (not rs[0]["urgent"], -rs[0]["days_open"], rs[0]["truck"]))
    out = []
    for rs in trucks[:SHOWN]:
        r = rs[0]
        when = "วันนี้" if r["days_open"] == 0 else "เมื่อวาน" if r["days_open"] == 1 else f" {dmy(r['request_date'])} ({r['days_open']} วันแล้ว)"
        bits = [f"แจ้งซ่อม{when} ยังไม่ได้ส่งอู่"] + ([f"คันนี้แจ้งไว้ {len(rs)} เรื่อง"] if len(rs) > 1 else [])
        out.append(item("repair_danger", "bad", _short(r["problem"], 60) or "แจ้งซ่อมด่วน", who=r["truck"], detail=" · ".join(bits),
                        link="#board", link_label="สถานะงานซ่อม"))
    if out and len(trucks) > SHOWN:
        out[-1]["detail"] += f" · และอีก {len(trucks) - SHOWN} คัน"
    return out


def _per_truck(rows: list[dict], days_key: str) -> list[dict]:
    """One row per truck (its longest), the longest first."""
    best: dict = {}
    for r in rows:
        if r["truck_key"] not in best or r[days_key] > best[r["truck_key"]][days_key]:
            best[r["truck_key"]] = r
    return sorted(best.values(), key=lambda r: (-r[days_key], r["truck"]))


def todo_at_shop_long(rows: list[dict]) -> list[dict]:
    long = _per_truck([r for r in rows if r["status"] == "at_shop" and r["days_at_shop"] >= LONG_SHOP_DAYS], "days_at_shop")
    if not long:
        return []
    names = [f"{r['truck']} {r['days_at_shop']} วัน" for r in long[:SHOWN]] + _more(_count(len(long)), "คัน")
    return [item("at_shop_long", "bad", f"รถอยู่อู่ {LONG_SHOP_DAYS} วันขึ้นไป {len(long)} คัน", detail=" · ".join(names), link="#board", link_label="สถานะงานซ่อม")]


def todo_awaiting_bill(rows: list[dict]) -> list[dict]:
    waiting = sorted((r for r in rows if r["status"] == "awaiting_bill"), key=lambda r: (-r["days_in_status"], r["truck"]))
    if not waiting:
        return []
    r = waiting[0]
    detail = f"นานสุด {r['truck']} {r['days_in_status']} วัน" + (f" ({r['shop']})" if r["shop"] else "")
    return [item("awaiting_bill", "warn", f"ซ่อมแล้ว รอบิล {len(waiting)} งาน", detail=detail, link="#board", link_label="ตามบิล")]


def todo_open_long(rows: list[dict]) -> list[dict]:
    old = sorted((r for r in rows if r["status"] == "open" and r["days_open"] >= OPEN_LONG_DAYS), key=lambda r: (-r["days_open"], r["truck"]))
    if not old:
        return []
    r = old[0]
    detail = f"นานสุด {r['truck']} {r['days_open']} วัน" + (f": {_short(r['problem'], 50)}" if r["problem"] else "") + " · ยังไม่ได้ส่งอู่"
    return [item("open_long", "warn", f"แจ้งซ่อมรอ {OPEN_LONG_DAYS} วันขึ้นไป {len(old)} งาน", detail=detail, link="#board", link_label="สถานะงานซ่อม")]


def tile_at_shop(rows: list[dict], fleet: int) -> dict:
    at_shop = _per_truck([r for r in rows if r["status"] == "at_shop"], "days_at_shop")
    longest = at_shop[0]["days_at_shop"] if at_shop else 0
    return tile("at_shop", len(at_shop), suffix=f"จาก {fleet} คัน", chip=dict(LIVE_CHIP),
                note=f"รอซ่อม {sum(1 for r in rows if r['status'] == 'open')} · รอบิล {sum(1 for r in rows if r['status'] == 'awaiting_bill')}",
                tone="bad" if longest >= LONG_SHOP_DAYS else "warn" if longest >= WARN_SHOP_DAYS else "", link="#board")


# ------------------------------------------------------------------ licences and company papers (pure)
def licence_rows(docs: list[dict], names: dict, today: date) -> list[dict]:
    """Every paper with its state by the register's own rule (licences.status), the most pressing first."""
    out = []
    for d in docs:
        st = licences.status(d, today)
        if st["days"] is None:
            continue
        out.append({"state": st["state"], "days": st["days"], "driving": d.get("kind") == "driving", "title": d.get("title") or licences.kind_of(d)["label"],
                    "person": names.get(d.get("employee_id")) or "", "place": d.get("place") or "", "expires_on": _day(d.get("expires_on"))})
    return sorted(out, key=lambda r: (r["days"], r["title"], r["person"]))


def _paper(r: dict) -> str:
    return " ".join(x for x in (r["title"], r["person"] if r["driving"] else r["place"]) if x)


def todo_licence_expired(rows: list[dict]) -> list[dict]:
    """Expired and not marked as handed in (ยื่นแล้ว): each one is its own line, a driving licence under the person's name."""
    gone = [r for r in rows if r["state"] == "expired"]
    out = []
    for r in gone[:SHOWN]:
        who = (r["person"] or "ไม่ระบุชื่อ") if r["driving"] else r["place"]
        out.append(item("licence_expired", "bad", f"{r['title']}หมดอายุแล้ว {-r['days']} วัน" if r["driving"] else f"{r['title']} หมดอายุแล้ว {-r['days']} วัน",
                        who=who, detail=f"หมด {dmy(r['expires_on'])} · ยังไม่ได้ยื่น", link="#licences", link_label="ใบอนุญาต"))
    if len(gone) > SHOWN:
        out.append(item("licence_expired", "bad", f"เอกสารหมดอายุแล้วอีก {len(gone) - SHOWN} รายการ", detail="ยังไม่ได้ยื่น", link="#licences", link_label="ใบอนุญาต"))
    return out


def todo_licence_soon(rows: list[dict]) -> list[dict]:
    soon = [r for r in rows if r["state"] == "soon"]
    if not soon:
        return []
    r = soon[0]
    left = "หมดวันนี้" if r["days"] == 0 else f"อีก {r['days']} วัน"
    handed = sum(1 for x in rows if x["state"] == "submitted")
    detail = f"ใกล้สุด {_paper(r)} หมด {dmy(r['expires_on'])} ({left})" + (f" · ยื่นแล้วรอใบใหม่ {handed} รายการ" if handed else "")
    return [item("licence_soon", "warn", f"เอกสารใกล้หมดอายุ {len(soon)} รายการ", detail=detail, link="#licences", link_label="ใบอนุญาต")]


# ------------------------------------------------------------------ the truck plan: engine oil (pure)
def _oil_overdue(row: dict) -> Optional[dict]:
    return next((i for i in row.get("items") or [] if i.get("key") in service_plan.OIL_KEYS and i.get("state") == "overdue"
                 and not i.get("snoozed_until")), None)


def todo_oil_due(plan_rows: list[dict]) -> list[dict]:
    """Trucks whose engine-oil change is overdue, by the plan page's own oil-first rule (service_plan.priority, tier 0): red only on a
    real odometer reading past the km, or past the time cap -- never on an estimate."""
    due = [(r, _oil_overdue(r)) for r in plan_rows if service_plan.priority(r)[0] == 0]
    due = [(r, oil) for r, oil in due if oil]
    if not due:
        return []
    by_km = [(r, oil) for r, oil in due if (oil.get("read_km_left") or 0) < 0]
    if by_km:
        r, oil = min(by_km, key=lambda x: x[1]["read_km_left"])
        worst = f"เลยมากสุด {truck_name(r)} {-round(oil['read_km_left']):,} กม. (ตามไมล์ที่อ่านจริง)"
    else:
        r, oil = min(due, key=lambda x: x[1].get("days_left") or 0)
        worst = f"เลยมากสุด {truck_name(r)} {-(oil.get('days_left') or 0)} วัน (ครบกำหนดเวลา)"
    at_shop = sum(1 for r, _ in due if r.get("at_shop"))
    return [item("oil_due", "warn", f"ถึงรอบถ่ายน้ำมันเครื่อง {len(due)} คัน", detail=worst + (f" · {at_shop} คันอยู่ที่อู่แล้ว" if at_shop else ""),
                 link="#plan", link_label="แผนซ่อมบำรุง")]


# ------------------------------------------------------------------ the owner's own board (pure)
def is_auto(ticket: dict) -> bool:
    """A ticket the repair board made by itself (โทรถามคนขับ / ตามบิล / ถามความคืบหน้า: app/repair_followup.py ties it to its request)."""
    return bool(ticket.get("request_id") or ticket.get("request_kind"))


def todo_my_overdue(tickets: list[dict], today: date) -> list[dict]:
    """The owner's OWN overdue tickets by name; the automatic repair follow-ups are only counted, so they do not bury them."""
    active = [t for t in tickets if t.get("status") in mgr.ACTIVE]
    late = sorted(((_day(t.get("due_date")), t) for t in active if _day(t.get("due_date")) and _day(t["due_date"]) < today), key=lambda x: (x[0], x[1].get("seq") or 0))
    mine = [(d, t) for d, t in late if not is_auto(t)]
    auto = sum(1 for _, t in late if is_auto(t))
    urgent = [t for t in active if t.get("urgent") and not is_auto(t)]
    if not mine and not urgent:
        return []
    bits = [f"{_short(t['title'], 40)} {(today - d).days} วัน" for d, t in mine[:SHOWN]] + _more(_count(len(mine)), "เรื่อง")
    if auto:
        bits.append(f"และงานตามรถ/ตามบิลอัตโนมัติอีก {auto} เรื่อง")
    if urgent:
        bits.append("ด่วน: " + ", ".join(_short(t["title"], 40) for t in urgent[:2]) + (f" และอีก {len(urgent) - 2} เรื่อง" if len(urgent) > 2 else ""))
    title = f"งานของฉันเลยกำหนด {len(mine)} เรื่อง" if mine else f"งานด่วนของฉัน {len(urgent)} เรื่อง"
    return [item("my_overdue", "warn", title, detail=" · ".join(bits), link="#mgr", link_label="บอร์ดของฉัน")]


# ------------------------------------------------------------------ phone sales sheets (pure)
def real_routes(routes: list[dict]) -> dict:
    """route id -> route, without the test route."""
    return {r["id"]: r for r in routes if r.get("id") != TEST_ROUTE_ID and r.get("branch") != TEST_BRANCH}


def route_name(route: dict, vehicles: dict) -> str:
    v = vehicles.get(route.get("vehicle_id"))
    if v:
        return f"รถสาย {truck_name(v)}"
    return " ".join(x for x in ("รถสาย", str(route.get("truck_no") or "").strip(), str(route.get("branch") or "").strip(), "(ยังไม่ผูกทะเบียน)") if x)


def todo_sales_sheets(days: list[dict], routes: list[dict], vehicles: dict, now: datetime) -> list[dict]:
    """Requests to reopen a sent sheet that are still waiting; past sheets never sent, by the ใบขายรถสาย page's own rule
    (route_sales.unsent: yesterday's from noon, and every older one, back SHEET_DAYS days); before noon, yesterday's that are still
    open (the driver can still send them); from 18:00, today's too. A sheet with nothing sold on it (0 baht) is not chased."""
    today = now.date()
    real = real_routes(routes)
    mine = [d for d in days if d.get("route_id") in real and _day(d.get("day"))]
    asked = sorted(((_thai_day(d["reopen_requested_at"]) or today, d) for d in mine if d.get("reopen_requested_at") and d.get("status") != "open"),
                   key=lambda x: (x[0], str(x[1].get("day"))))
    still = sorted((d for d in mine if d.get("status") == "open" and _num(d.get("amount")) > 0 and _day(d["day"]) >= today - timedelta(days=SHEET_DAYS)),
                   key=lambda d: (str(d["day"]), d["route_id"]))
    late = [d for d in still if route_sales.unsent(_day(d["day"]), d["status"], now)]
    yesterday = [d for d in still if _day(d["day"]) == today - timedelta(days=1) and not route_sales.unsent(_day(d["day"]), d["status"], now)]
    current = [d for d in still if _day(d["day"]) == today] if now.hour >= SHEETS_AFTER_HOUR else []

    def names(rows: list[dict]) -> str:
        per: dict = {}                                    # a route with several sheets is named once, with how many
        for d in rows:
            per[d["route_id"]] = per.get(d["route_id"], 0) + 1
        return ", ".join([route_name(real[r], vehicles) + (f" {n} ใบ" if n > 1 else "") for r, n in list(per.items())[:SHOWN]] + _more(_count(len(per)), "สาย"))

    groups = []
    if late:
        groups.append((f"ยังไม่ส่ง {len(late)} ใบ (เก่าสุด {dm(_day(late[0]['day']))})", late))
    if yesterday:
        groups.append((f"เมื่อวานยังเปิดอยู่ {len(yesterday)} สาย (ส่งได้ถึงเที่ยง)", yesterday))
    if current:
        groups.append((f"วันนี้ยังไม่ส่ง {len(current)} สาย", current))
    if not (asked or groups):
        return []
    bits = [f"{route_name(real[d['route_id']], vehicles)} ขอแก้ใบวันที่ {dmy(_day(d['day']))}" for _, d in asked[:2]] + _more(_count(len(asked), 2), "คำขอ")
    for n, (head, rows) in enumerate(groups):             # the group the title already names is only its routes
        bits.append(names(rows) if n == 0 and not asked else f"{head}: {names(rows)}")
    title = f"ใบขายรถสาย: คำขอแก้ใบขายรอ {(today - asked[0][0]).days} วัน" if asked else f"ใบขายรถสาย: {groups[0][0]}"
    return [item("sales_sheets", "warn", title, detail=" · ".join(bits), link="#sales-sheets", link_label="ใบขายรถสาย")]


def tile_phone_sales(days: list[dict], routes: list[dict], today: date) -> dict:
    """Today on the drivers' phones: what the sheets add up to, how many routes have one (of every active route, both branches), how
    many are sent."""
    real = real_routes(routes)
    rows = [d for d in days if d.get("route_id") in real and _day(d.get("day")) == today]
    of = sum(1 for r in real.values() if r.get("active"))
    sent = sum(1 for d in rows if d.get("status") != "open")
    return tile("phone_sales", round(sum(_num(d.get("amount")) for d in rows)), unit="฿", chip={"text": f"{len(rows)} จาก {of} สาย", "tone": "plain"},
                note=f"ส่งแล้ว {sent} · ยังเปิดอยู่ {len(rows) - sent}", link="#sales-sheets")


# ------------------------------------------------------------------ machines (pure)
def todo_machines(machines: list[dict]) -> list[dict]:
    """Machine jobs past their round (state "late" in the machine plan), the latest first. A machine with no hour meter is on assumed
    running hours, so its line says the hours are an estimate."""
    late = []
    for m in machines:
        plan = m.get("plan") or {}
        for j in plan.get("jobs") or []:
            if j.get("state") == "late" and j.get("days_left") is not None:
                where = " ".join(x for x in (machinery.machine_name(m), m.get("branch") or "") if x)
                guess = " (ชั่วโมงประมาณ)" if j.get("hours") and not plan.get("hours_now") else ""
                late.append((j["days_left"], f"{j['name']} {where} เลย {-j['days_left']} วัน{guess}"))
    if not late:
        return []
    late.sort()
    return [item("machines", "warn", f"เครื่องจักรเลยรอบ {len(late)} งาน", detail=" · ".join([t for _, t in late[:2]] + _more(_count(len(late), 2), "งาน")),
                 link="#machine-plan", link_label="แผนเครื่องจักร")]


# ------------------------------------------------------------------ feeds that stopped (pure)
def todo_stale_data(sales_through: Optional[date], meters: dict, today: date, sales_part: Optional[date] = None) -> list[dict]:
    """sales_through = the newest day of route_driver_days typed for every route; sales_part = a newer day typed for some routes only;
    meters = {branch: newest day of meter data}."""
    out = []
    yesterday = today - timedelta(days=1)
    if sales_through and (yesterday - sales_through).days > SALES_BEHIND_DAYS:
        first = sales_through + timedelta(days=1)
        out.append(item("stale_data", "warn", f"ยอดขายรายวันยังไม่ได้ลง {dm(first)}-{dm(yesterday)}",
                        detail=f"ลงไว้ถึง {dmy(sales_through)} · ค้าง {(yesterday - sales_through).days} วัน"
                               + (f" · {dm(sales_part)} ลงแล้วบางสาย" if sales_part else ""), link="#driver-days", link_label="ยอดขายรายวัน"))
    old = sorted((d, b) for b, d in (meters or {}).items() if d and (today - d).days > METER_STALE_DAYS)
    if old:
        out.append(item("stale_data", "warn", f"มิเตอร์ไฟ ไม่มีข้อมูลใหม่ {(today - old[0][0]).days} วัน",
                        detail=" · ".join(f"{b} ถึง {dmy(d)}" for d, b in old), link="#electricity", link_label="ค่าไฟฟ้า"))
    return out


# ------------------------------------------------------------------ the next 7 days (pure)
def todo_next7(tickets: list[dict], rules: list[dict], made: set, plan_rows: list[dict], machines: list[dict], today: date) -> list[dict]:
    """What is coming: the owner's dated tickets, the routine jobs not on the board yet, and the oil-change visits of trucks and
    machines that fall in the next NEXT_DAYS days -- "DD/MM title", days in order."""
    until = today + timedelta(days=NEXT_DAYS)
    found: list[tuple[date, str]] = []
    for t in tickets:
        d = _day(t.get("due_date"))
        if t.get("status") in mgr.ACTIVE and not is_auto(t) and d and today <= d <= until:
            found.append((d, _short(t.get("title"), 40)))
    for e in calendar_plan.routine_entries(rules, made, today, until):
        d = _day(e["date"])
        if d and today <= d <= until:                     # a rule that waits for its last ticket keeps a past date: that is not "coming"
            found.append((d, _short(e["label"], 40)))
    trucks: dict = {}
    for r in plan_rows:
        e = calendar_plan.truck_visit(r, today)
        d = _day(e["date"]) if e else None
        if d and today <= d <= until and not e["check"] and not e["at_shop"]:
            trucks.setdefault(d, []).append(truck_name(r))
    for d, names in trucks.items():                       # several trucks on one day are one entry, so they do not push the rest out
        found.append((d, f"{OIL_VISIT} {names[0]}" if len(names) == 1 else f"{OIL_VISIT} {len(names)} คัน"))
    for m in machines:
        for e in calendar_plan.machine_entries(m, today):
            d = _day(e["date"])
            if d and today <= d <= until:
                found.append((d, " ".join(x for x in (e["main"], machinery.machine_name(m), m.get("branch") or "") if x)))
    if not found:
        return []
    found.sort(key=lambda x: x[0])
    by_day: dict = {}
    for d, text in found[:NEXT_SHOWN]:
        by_day.setdefault(d, []).append(text)
    bits = [f"{dm(d)} {', '.join(texts)}" for d, texts in by_day.items()] + _more(len(found) - NEXT_SHOWN, "รายการ")
    return [item("next7", "info", f"{NEXT_DAYS} วันข้างหน้า", detail=" · ".join(bits), link="#mgr-calendar", link_label="ปฏิทิน")]


def build_todo(items: list[dict]) -> dict:
    """The list as the page shows it: bad first, then warn, then info; inside a level the order the items were given in."""
    ordered = sorted(items, key=lambda i: LEVELS.index(i["level"]))
    return {"bad": sum(1 for i in ordered if i["level"] == "bad"), "warn": sum(1 for i in ordered if i["level"] == "warn"), "items": ordered}


# ------------------------------------------------------------------ tiles (pure)
def tile(key: str, value, *, label: Optional[str] = None, unit: str = "", suffix: str = "", chip: Optional[dict] = None, delta: Optional[dict] = None,
         note: str = "", tone: str = "", spark: Optional[list] = None, link: str = "") -> dict:
    return {"key": key, "label": label or TILE_LABELS[key], "value": value, "unit": unit, "suffix": suffix, "chip": chip, "delta": delta,
            "note": note, "tone": tone, "spark": spark, "link": link}


def no_tile(key: str) -> dict:
    return {"key": key, "label": TILE_LABELS[key], "ready": False}


def route_day_totals(rows: list[dict]) -> dict:
    """route_driver_days rows -> {day: {"money", "routes", "spare"}}. Every row is one route's day, kept under its own driver; a row
    with no figure and no สำรองขับ tick is not a day typed. A สำรองขับ day's money is counted as sales (COUNT_SPARE_SALES): the pay
    sheet gives that day to nobody (no commission), but the ice was sold. "spare" = how many routes the spare drove that day."""
    out: dict = {}
    for r in rows:
        d = _day(r.get("day"))
        if not d or (r.get("money") is None and not r.get("spare")):
            continue
        t = out.setdefault(d, {"money": 0.0, "routes": set(), "spare": 0})
        t["routes"].add(r.get("employee_id"))
        if r.get("spare"):
            t["spare"] += 1
            if not COUNT_SPARE_SALES:
                continue
        t["money"] += _num(r.get("money"))
    return out


def typed_days(totals: dict) -> tuple[list[date], int]:
    """(the days typed for nearly every route, oldest first; the usual number of routes a day). Daily sales are typed driver by driver,
    so the newest day can be there for a few routes only -- and must not be read as the whole day's sales: a day counts once its routes
    reach FULL_DAY_SHARE of the usual (the median day of what was read)."""
    if not totals:
        return [], 0
    usual = int(median(len(t["routes"]) for t in totals.values()))
    return sorted(d for d, t in totals.items() if len(t["routes"]) >= FULL_DAY_SHARE * usual), usual


def sales_reach(rows: list[dict]) -> tuple[Optional[date], Optional[date]]:
    """(the newest day typed for every route; a newer day typed for some routes only, if there is one)."""
    totals = route_day_totals(rows)
    full, _ = typed_days(totals)
    if not full:
        return None, None
    return full[-1], max((d for d in totals if d > full[-1]), default=None)


def tile_route_sales(rows: list[dict], today: date) -> dict:
    """All routes on the newest FULLY typed day, against the same weekday a week before. A newer day typed for some routes only is
    named in the note and turns the tile amber, but is not the number."""
    totals = route_day_totals(rows)
    full, usual = typed_days(totals)
    if not full:
        return no_tile("route_sales")
    newest, before = full[-1], full[-1] - timedelta(days=7)
    t = totals[newest]
    change = delta(t["money"], totals[before]["money"]) if before in full else None
    n = len(t["routes"])
    bits = ([f"จาก {dmy(before)}"] if change else []) + [f"{n} จาก {usual} สาย" if n < usual else f"{n} สาย"] + ([f"สำรองขับ {t['spare']}"] if t["spare"] else [])
    part = max((d for d in totals if d > newest), default=None)
    if part:
        bits.append(f"{dm(part)} ลงแล้ว {len(totals[part]['routes'])} สาย ยังไม่นับ")
    return tile("route_sales", round(t["money"]), unit="฿", chip=through_chip(newest, today), delta=change, note=" · ".join(bits),
                tone="warn" if part else "", spark=[round(totals[d]["money"]) for d in sorted(totals) if d <= newest][-SPARK_DAYS:], link="#driver-days")


def _signed_by_day(rows: list[dict], today: date) -> dict:
    """tax_invoices rows -> {day: amount including VAT}, a credit note taken off (as app/tax_invoices.py summarize does)."""
    out: dict = {}
    for r in rows:
        d = _day(r.get("doc_date"))
        if d and d <= today:
            out[d] = out.get(d, 0.0) + (-1 if r.get("kind") == "credit_note" else 1) * _num(r.get("total"))
    return out


def same_days_before(d: date) -> tuple[date, date]:
    """The like-for-like range in the month before: 01-21/11 is compared with 01-21/10 (a shorter month ends on its last day)."""
    start = prev_month_start(d)
    return start, start.replace(day=min(d.day, monthrange(start.year, start.month)[1]))


def tile_kk_invoice(rows: list[dict], today: date) -> dict:
    """แก่งคอย's tax invoices on the newest document date; the month so far against the same days of the month before."""
    per = _signed_by_day(rows, today)
    if not per:
        return no_tile("kk_invoice")
    newest = max(per)
    first = month_start(newest)
    span = [first + timedelta(days=i) for i in range(newest.day)]
    month = sum(per.get(d, 0.0) for d in span)
    b0, b1 = same_days_before(newest)
    before = sum(v for d, v in per.items() if b0 <= d <= b1)
    change = delta(month, before)
    return tile("kk_invoice", round(per[newest]), unit="฿", chip=through_chip(newest, today),
                note=f"01-{dm(newest)} {baht(month)}" + (f" · {change['text']} จาก 01-{dm(b1)}" if change else ""),
                spark=[round(per.get(d, 0.0)) for d in span], link="#tax-invoices")


def tile_repair_mtd(events: list[dict], today: date, newest_on_record: Optional[date] = None) -> dict:
    """Truck repair spend from the 1st to the day the bills REACH (they arrive days late), against the SAME days last month -- not up
    to today (the month would read too low all month, and "-100%" on the 1st), and not the whole month. With no bill yet this month
    there is nothing to compare: the tile says so, and its chip shows the newest repair date on record (newest_on_record)."""
    costs = [(d, _num(e.get("total_cost"))) for e in events for d in [_day(e.get("event_date"))] if d and d <= today]
    newest = max((d for d, _ in costs if d >= month_start(today)), default=None)
    if newest is None:
        seen = max((d for d, _ in costs), default=None) or newest_on_record
        return tile("repair_mtd", 0, label=f"{TILE_LABELS['repair_mtd']} {today.month:02d}/{today.year}", unit="฿",
                    chip=through_chip(seen, today, ok=3, warn=10) if seen else None, note="เดือนนี้ยังไม่มีบิล", link="#dash")
    b0, b1 = same_days_before(newest)
    this = sum(c for d, c in costs if d >= month_start(today))
    last = sum(c for d, c in costs if b0 <= d <= b1)
    return tile("repair_mtd", round(this), label=f"{TILE_LABELS['repair_mtd']} 01-{dm(newest)}", unit="฿",
                chip=through_chip(newest, today, ok=3, warn=10), delta=delta(this, last, lower_is_good=True),
                note=f"จาก 01-{dm(b1)} ({baht(last)})", link="#dash")


def tile_payables(t: dict) -> dict:
    """t = the tiles of the เจ้าหนี้ร้านค้า page (payables.view): owed, shops, over60."""
    over = _num(t.get("over60"))
    return tile("payables", round(_num(t.get("owed"))), unit="฿", chip=dict(LIVE_CHIP),
                note=f"{t.get('shops') or 0} ร้าน · " + (f"เกิน 60 วัน {baht(over)}" if over > 0 else "ไม่มีบิลเกิน 60 วัน"),
                tone="warn" if over > 0 else "", link="#payables")


# ------------------------------------------------------------------ the books (pure)
def company_months(today: date, count: int = 12) -> list[str]:
    """The last `count` company months (26th-25th) up to the one `today` is in, oldest first."""
    month = accounting.period_of(today)["month"]
    y, m = int(month[:4]), int(month[5:7])
    out = []
    for _ in range(count):
        out.insert(0, f"{y}-{m:02d}")
        y, m = accounting._add_month(y, m, -1)
    return out


def build_books(lines: list[dict], accounts: dict, months: list[str], current: str) -> dict:
    """Net profit by company month, whole company and per branch (accounting.year_rows, the same sums as the งบกำไรขาดทุน page), for
    the months that have sales booked -- and which of them are not complete. lines = [{account, debit, credit, branch, date}].
    Not complete = unit_cost.incomplete_flags (the cheque-paid supplies are not booked yet), or the month is still running."""
    total = accounting.year_rows(lines, accounts, months)["months"]
    per = {b: accounting.year_rows([ln for ln in lines if ln.get("branch") == b], accounts, months, b)["months"] for b in BRANCHES}
    cheque = {m: 0.0 for m in months}
    for ln in lines:
        m = unit_cost.company_month(_day(ln["date"]))
        if ln["account"] in unit_cost.CHEQUE_ACCOUNTS and m in cheque:
            cheque[m] += _num(ln.get("debit")) - _num(ln.get("credit"))
    keep = [i for i, x in enumerate(total) if x["revenue"]]            # as accounting.year(): a month with costs but no sales would read as a loss
    flags = unit_cost.incomplete_flags([cheque[months[i]] for i in keep])
    return {"months": [months[i] for i in keep], "total": [round(total[i]["net_profit"]) for i in keep],
            "branches": [{"name": b, "values": [round(per[b][i]["net_profit"]) for i in keep]} for b in BRANCHES],
            "incomplete": [n for n, i in enumerate(keep) if flags[n] or months[i] >= current]}


def last_complete(books: dict) -> Optional[int]:
    done = [n for n in range(len(books["months"])) if n not in books["incomplete"]]
    return done[-1] if done else None


def build_profit(books: dict) -> dict:
    """The last COMPLETE month: the total and each branch, and which later month is not counted yet."""
    n = last_complete(books)
    if n is None:
        return {"ready": False}
    later = [month_text(books["months"][i]) for i in books["incomplete"] if i > n]
    return {"ready": True, "month": books["months"][n], "total": books["total"][n],
            "branches": [{"name": b["name"], "value": b["values"][n]} for b in books["branches"]],
            "note": f"{', '.join(later)} ยังไม่ครบ จึงยังไม่นับ" if later else "", "link": "#accounts"}


def build_profit_branch(books: dict) -> dict:
    if not books["months"]:
        return {"ready": False}
    return {"ready": True, "months": books["months"], "series": books["branches"], "incomplete": books["incomplete"]}


def build_sales_daily(rows: list[dict], today: date) -> dict:
    """The routes' daily sales, one value per CALENDAR day up to the newest fully typed day (the last TREND_DAYS): a day nobody typed
    is None, so the page breaks the line there instead of squeezing the days together. through_tone = how far behind that day is,
    by the same limits as the tile's chip."""
    totals = route_day_totals(rows)
    full, usual = typed_days(totals)
    if not full:
        return {"ready": False}
    through = full[-1]
    first = max(min(totals), through - timedelta(days=TREND_DAYS - 1))
    days = [first + timedelta(days=i) for i in range((through - first).days + 1)]
    return {"ready": True, "days": [d.isoformat() for d in days], "values": [round(totals[d]["money"]) if d in totals else None for d in days],
            "through": through.isoformat(), "through_tone": lag_tone((today - through).days, 1, 7), "note": f"{usual} สายต่อวัน สระบุรี"}


# ------------------------------------------------------------------ how far each feed reaches (pure)
def _months_behind(month: str, today: date) -> int:
    return (today.year * 12 + today.month) - (int(month[:4]) * 12 + int(month[5:7]))


def build_fresh(feeds: dict, today: date) -> list[dict]:
    """One entry per feed. feeds = {tax, repairs, route_sales, meters, sales_program: a day; books, elec_bills, payroll: 'YYYY-MM'}
    (a feed that is missing or None is left out). A day shows as DD/MM, a month as MM/YYYY, the live boards as "สด"."""
    out = [{"label": label, "value": "สด", "tone": "ok"} for label in ("สถานะงานซ่อม", "บอร์ดของฉัน", "ใบอนุญาต")]
    for key, label, ok, warn in (("tax", "ใบกำกับภาษีแก่งคอย", 1, 7), ("repairs", "บิลซ่อมรถ", 3, 10), ("route_sales", "ยอดขายรถสายรายวัน", 1, 7),
                                 ("meters", "มิเตอร์ไฟ", 3, METER_STALE_DAYS), ("sales_program", "โปรแกรมขายหน้าโรง", 10, 35)):
        d = _day(feeds.get(key))
        if d:
            out.append({"label": label, "value": dm(d) if d.year == today.year else dmy(d), "tone": lag_tone((today - d).days, ok, warn)})
    for key, label in (("books", "ปิดบัญชีถึง"), ("elec_bills", "ใบแจ้งค่าไฟ"), ("payroll", "เงินเดือน")):
        m = feeds.get(key)
        if m:
            out.append({"label": label, "value": month_text(m), "tone": lag_tone(_months_behind(m, today), 1, 2)})
    return out


def _oldest(days: list) -> Optional[date]:
    """Both branches are one view: a feed reaches only as far as the branch that is furthest behind."""
    known = [d for d in days if d]
    return min(known) if known else None


def _latest(days: list) -> Optional[date]:
    known = [d for d in days if d]
    return max(known) if known else None


# ------------------------------------------------------------------ caches
_FAILED = object()
_parts: dict = {}                    # name -> (kept until, value, read fresh?): the slow sources, and the two whole answers
_part_locks: dict = {}
_guard = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=14, thread_name_prefix="home")
_side = ThreadPoolExecutor(max_workers=16, thread_name_prefix="home-read")     # reads made INSIDE a source (never the same pool: it could wait on itself)


def _cached(name: str, seconds: float, fn: Callable[[], object], fresh: bool = False, keep: Optional[Callable[[object], bool]] = None):
    """fn()'s answer, kept `seconds`. fresh=True always reads again -- unless another FRESH request read it while this one was waiting
    its turn (two refreshes at once read once; an answer built from older copies does not count as fresh). An error is never kept, and
    an answer keep() turns down (one with a failed part) is kept only RETRY_SECONDS: the next look reads again."""
    seen = _parts.get(name)
    if not fresh and seen and time.time() < seen[0]:
        return seen[1]
    with _guard:
        lock = _part_locks.setdefault(name, threading.Lock())
    with lock:
        hit = _parts.get(name)
        if hit and time.time() < hit[0] and (not fresh or (hit is not seen and hit[2])):
            return hit[1]
        value = fn()
        good = keep is None or keep(value)
        _parts[name] = (time.time() + (seconds if good else min(seconds, RETRY_SECONDS)), value, fresh)
        return value


def forget() -> None:
    """Drop everything kept (the tests start clean with this)."""
    _parts.clear()


# ------------------------------------------------------------------ loaders (thin: fetch rows; no existence probes of their own)
def _src_requests(today: date, fresh: bool) -> list[dict]:
    return supabase.select_all("repair_requests", {
        "select": "seq,vehicle_id,truck_number_text,plate_text,request_date,created_at,urgent,problem,status,status_changed_at,shop,sent_date,fixed_by,fixed_date",
        "status": "in.(open,at_shop,awaiting_bill)", "order": "seq.asc"})


def _src_vehicles(today: date, fresh: bool) -> dict:
    rows = supabase.select_all("vehicles", {"select": "id,plate_number,vehicle_number,branch,vehicle_class,active", "order": "id.asc"})
    return {v["id"]: v for v in rows if v.get("branch") != TEST_BRANCH}


def _src_tickets(today: date, fresh: bool) -> list[dict]:
    return supabase.select_all("mgr_tickets", {"select": "id,seq,title,urgent,status,due_date,recurring_id,request_id,request_kind",
                                               "status": "in.(todo,doing,waiting)", "order": "seq.asc"})


def _src_routine(today: date, fresh: bool) -> dict:
    """The routine jobs (mgr_recurring) and which of their coming days are on the board already. Plain reads: nothing is created."""
    until = today + timedelta(days=NEXT_DAYS)
    rules, made = [f.result() for f in (
        _side.submit(supabase.select_all, "mgr_recurring", {"select": "id,title,rule,mode,next_due,start_date,end_date,active,time_of_day,place,notes,type_key",
                                                            "active": "eq.true", "next_due": f"lte.{until.isoformat()}", "order": "created_at.asc"}),
        _side.submit(supabase.select_all, "mgr_tickets", {"select": "recurring_id,due_date", "recurring_id": "not.is.null", "due_date": f"gte.{today.isoformat()}",
                                                          "and": f"(due_date.lte.{until.isoformat()})", "order": "id.asc"}))]
    return {"rules": rules, "made": {(t["recurring_id"], str(t["due_date"])[:10]) for t in made if t.get("due_date")}}


def _src_sheets(today: date, fresh: bool) -> dict:
    """The phone sheets of the last SHEET_DAYS days (an unsent one is chased that far back, as on the ใบขายรถสาย page), plus any sheet
    (however old) with a request to reopen it still waiting. Only the day's amount is read from `totals` -- not the lines."""
    since = (today - timedelta(days=SHEET_DAYS)).isoformat()
    days, routes = [f.result() for f in (
        _side.submit(supabase.select_all, "sale_days", {"select": "route_id,day,status,reopen_requested_at,amount:totals->amount",
                                                        "or": f"(day.gte.{since},reopen_requested_at.not.is.null)", "order": "day.asc,route_id.asc"}),
        _side.submit(supabase.select_all, "sale_routes", {"select": "id,branch,truck_no,vehicle_id,active", "order": "id.asc"}))]
    return {"days": days, "routes": routes}


def _src_route_days(today: date, fresh: bool) -> list[dict]:
    """The typed daily sales of the routes: the newest day and the TREND_DAYS before it. The tile and the chart both read this, so it is
    kept as long as the page itself: the chart, asked for right after the page, does not read it again."""
    def read():
        newest = supabase.select("route_driver_days", {"select": "day", "order": "day.desc", "limit": "1"})
        if not newest:
            return []
        since = _day(newest[0]["day"]) - timedelta(days=TREND_DAYS - 1)
        return supabase.select_all("route_driver_days", {"select": "day,employee_id,money,spare", "day": f"gte.{since.isoformat()}", "order": "id.asc"})
    return _cached("route_days", HOME_SECONDS, read, fresh)


def _src_tax(today: date, fresh: bool) -> list[dict]:
    since = prev_month_start(prev_month_start(today))          # the newest month and the one before it, even on the 1st
    return supabase.select_all("tax_invoices", {"select": "doc_date,kind,total", "branch": "eq.แก่งคอย", "doc_date": f"gte.{since.isoformat()}", "order": "id.asc"})


def _src_repair_bills(today: date, fresh: bool) -> dict:
    """This month's and last month's truck repair records, and the newest repair date on record (asked for only when those two months
    are empty)."""
    rows = supabase.select_all("v_events", {"select": "event_id,event_date,total_cost", "event_date": f"gte.{prev_month_start(today).isoformat()}",
                                            "order": "event_id.asc"})
    newest = max((d for d in (_day(r.get("event_date")) for r in rows) if d and d <= today), default=None)
    return {"rows": rows, "newest": newest or _day(_newest("v_events", "event_date"))}


def _src_licences(today: date, fresh: bool) -> dict:
    docs = supabase.select_all("company_docs", {"select": "id,kind,title,place,employee_id,expires_on,warn_days,submitted_on", "active": "eq.true",
                                                "order": "id.asc"})          # only what the state needs: no document numbers
    ids = sorted({d["employee_id"] for d in docs if d.get("employee_id")})
    people = supabase.select_all("employees", {"select": "id,full_name", "id": f"in.({','.join(ids)})", "order": "id.asc"}) if ids else []
    return {"docs": docs, "names": {p["id"]: p.get("full_name") or "" for p in people}}


def _ask_together(*checks: Callable[[], bool]) -> None:
    """The "has this table been made yet?" checks the plan functions start with (each keeps its answer 30 s), asked side by side
    instead of one after another: the function then finds the answers waiting. The same reads, not more -- a second off the page."""
    for f in [_side.submit(c) for c in checks]:
        f.result()


def _src_plan(today: date, fresh: bool) -> list[dict]:
    """Every active truck's programme, by the plan page's own function (it reads every repair bill: the slowest read of this page)."""
    def read():
        _ask_together(service_plan.has_v10, mgr.has_v12, service_plan.has_v13, fleet.has_v32)
        return service_plan.overview(today=today)["vehicles"]
    return _cached("plan", SLOW_SECONDS, read, fresh)


def _src_machines(today: date, fresh: bool) -> list[dict]:
    def read():
        _ask_together(machinery.has_v9, machinery.has_v33, machine_plan.has_v34)
        out = machine_plan.overview(today)
        return out["machines"] if out.get("ready") else []
    return _cached("machines", SLOW_SECONDS, read, fresh)


def _src_payables(today: date, fresh: bool) -> dict:
    def read():
        if fresh:
            shops.refresh()                                   # the shop names it sorts the bills by keep their own 30 s copy
        return payables.load(today)["tiles"]
    return _cached("payables", SLOW_SECONDS, read, fresh)


def _newest(table: str, column: str, **where) -> Optional[str]:
    rows = supabase.select(table, {"select": column, column: "not.is.null", **where, "order": f"{column}.desc", "limit": "1"})
    return rows[0][column] if rows else None


def _src_feeds(today: date, fresh: bool) -> dict:
    """The newest date of the feeds no other part of the page reads. One small read each (newest row only), side by side; a read that
    fails leaves its feed out and is named in "failed"."""
    def read():
        # the meter table holds both branches: each is asked by name. แก่งคอย also has daily totals (elec_amr_daily holds only that
        # branch: the days its 15-minute file does not cover), so it reaches as far as the later of the two
        asks = {"meters_sbr": lambda: _thai_day(_newest("elec_amr_readings", "slot_start", branch="eq.สระบุรี")),
                "meters_kk": lambda: _latest([_thai_day(_newest("elec_amr_readings", "slot_start", branch="eq.แก่งคอย")),
                                              _day(_newest("elec_amr_daily", "day"))]),
                "program_sbr": lambda: _day(_newest("sales_lines", "sale_date", branch="eq.สระบุรี")),
                "program_kk": lambda: _day(_newest("sales_lines", "sale_date", branch="eq.แก่งคอย")),
                "elec_bills": lambda: _newest("elec_bills", "bill_period"),
                "payroll": lambda: _newest("employee_pay_months", "month")}      # the month only: no pay or bank column is read
        futures = {k: _side.submit(fn) for k, fn in asks.items()}
        out, failed = {}, []
        for k, f in futures.items():
            try:
                out[k] = f.result()
            except Exception:
                logger.exception("home feed date %s failed", k)
                out[k] = None
                failed.append(k)
        return {**out, "failed": failed}
    return _cached("feeds", SLOW_SECONDS, read, fresh, keep=lambda v: not v["failed"])


def _src_books(today: date, fresh: bool) -> dict:
    """The year's profit-and-loss ledger lines, read ONCE for the total and both branches (accounting.year() would read the whole
    ledger three times), with the entry date joined on. Read-only: plain selects on acc_accounts / acc_lines / acc_entries."""
    def read():
        months = company_months(today)
        start = accounting.period_bounds(months[0])[0]
        accounts = accounting._accounts()
        codes = sorted(c for c, a in accounts.items() if a.get("kind") in ("income", "cogs", "expense"))      # the rest is the cash / payables side
        rows = supabase.select_all_parallel("acc_lines", {"select": "account,debit,credit,branch,entry:acc_entries!inner(entry_date)",
                                                          "entry.entry_date": f"gte.{start.isoformat()}", "account": f"in.({','.join(codes)})",
                                                          "order": "id.asc"}, 1000, 8) if codes else []
        lines = [{"account": ln["account"], "debit": ln["debit"], "credit": ln["credit"], "branch": ln.get("branch"), "date": ln["entry"]["entry_date"]}
                 for ln in rows]
        return build_books(lines, accounts, months, accounting.period_of(today)["month"])
    return _cached("books", BOOKS_SECONDS, read, fresh)


HOME_SOURCES: dict[str, Callable[[date, bool], object]] = {
    "requests": _src_requests, "vehicles": _src_vehicles, "tickets": _src_tickets, "routine": _src_routine, "sheets": _src_sheets,
    "route_days": _src_route_days, "tax": _src_tax, "repair_bills": _src_repair_bills, "licences": _src_licences, "plan": _src_plan,
    "machines": _src_machines, "payables": _src_payables, "feeds": _src_feeds, "books": _src_books}
TREND_SOURCES: dict[str, Callable[[date, bool], object]] = {"route_days": _src_route_days, "books": _src_books}


def _gather(sources: dict, today: date, fresh: bool) -> dict:
    """Every source at the same time; one that raises comes back as _FAILED (and is logged) instead of taking the page down."""
    def one(name: str, fn):
        try:
            return fn(today, fresh)
        except Exception:
            logger.exception("home source %s failed", name)
            return _FAILED
    futures = {name: _pool.submit(one, name, fn) for name, fn in sources.items()}
    return {name: f.result() for name, f in futures.items()}


class _Parts:
    """Builds the parts of the answer from the sources, each one guarded: a part whose source failed, or whose builder raises, gets
    its default and its name goes to `failed`."""

    def __init__(self, src: dict):
        self.src, self.failed = src, []

    def ok(self, name: str) -> bool:
        return self.src.get(name, _FAILED) is not _FAILED

    def build(self, name: str, needs: tuple, fn: Callable, default):
        if not all(self.ok(n) for n in needs):
            self.failed.append(name)
            return default
        try:
            return fn(*[self.src[n] for n in needs])
        except Exception:
            logger.exception("home part %s failed", name)
            self.failed.append(name)
            return default


# ------------------------------------------------------------------ the two answers
def build_home(src: dict, today: date, now: datetime) -> dict:
    """Sources (what the _src_* loaders returned, or _FAILED) -> the /api/home answer. Pure given the sources."""
    p = _Parts(dict(src))
    # two steps the parts below share; when one cannot be made, every part that needs it is named in "failed"
    p.src["rows"] = _Parts(src).build("rows", ("requests", "vehicles"), lambda reqs, veh: request_rows(reqs, veh, today), _FAILED)
    p.src["lic"] = _Parts(src).build("lic", ("licences",), lambda x: licence_rows(x["docs"], x["names"], today), _FAILED)
    feeds = src["feeds"] if p.ok("feeds") else {}
    try:
        sales_through, sales_part = sales_reach(src["route_days"]) if p.ok("route_days") else (None, None)
    except Exception:
        logger.exception("home part sales_reach failed")
        sales_through = sales_part = None
    meters = {"สระบุรี": feeds.get("meters_sbr"), "แก่งคอย": feeds.get("meters_kk")}

    todo = [
        ("repair_danger", ("rows",), lambda r: todo_repair_danger(r, today)),
        ("licence_expired", ("lic",), todo_licence_expired),
        ("at_shop_long", ("rows",), todo_at_shop_long),
        ("oil_due", ("plan",), todo_oil_due),
        ("my_overdue", ("tickets",), lambda t: todo_my_overdue(t, today)),
        ("licence_soon", ("lic",), todo_licence_soon),
        ("awaiting_bill", ("rows",), todo_awaiting_bill),
        ("open_long", ("rows",), todo_open_long),
        ("sales_sheets", ("sheets", "vehicles"), lambda s, veh: todo_sales_sheets(s["days"], s["routes"], veh, now)),
        ("machines", ("machines",), todo_machines),
        ("stale_data", (), lambda: todo_stale_data(sales_through, meters, today, sales_part)),
        ("next7", ("tickets", "routine", "plan", "machines"), lambda t, r, pl, m: todo_next7(t, r["rules"], r["made"], pl, m, today)),
    ]
    items = [i for key, needs, fn in todo for i in p.build(f"todo.{key}", needs, fn, [])]

    tiles = [
        ("route_sales", ("route_days",), lambda r: tile_route_sales(r, today)),
        ("kk_invoice", ("tax",), lambda r: tile_kk_invoice(r, today)),
        ("phone_sales", ("sheets",), lambda s: tile_phone_sales(s["days"], s["routes"], today)),
        ("at_shop", ("rows", "vehicles"), lambda r, veh: tile_at_shop(r, sum(1 for v in veh.values() if v.get("active")))),
        ("repair_mtd", ("repair_bills",), lambda r: tile_repair_mtd(r["rows"], today, r["newest"])),
        ("payables", ("payables",), tile_payables),
    ]
    built = [p.build(f"tiles.{key}", needs, fn, no_tile(key)) for key, needs, fn in tiles]
    profit = p.build("profit", ("books",), build_profit, {"ready": False})

    def fresh_list() -> list[dict]:
        books = src["books"] if p.ok("books") else None
        n = last_complete(books) if books else None
        return build_fresh({"tax": max(_signed_by_day(src["tax"], today), default=None) if p.ok("tax") else None,
                            "repairs": src["repair_bills"]["newest"] if p.ok("repair_bills") else None, "route_sales": sales_through,
                            "meters": _oldest([feeds.get("meters_sbr"), feeds.get("meters_kk")]),
                            "sales_program": _oldest([feeds.get("program_sbr"), feeds.get("program_kk")]),
                            "books": books["months"][n] if n is not None else None,
                            "elec_bills": feeds.get("elec_bills"), "payroll": feeds.get("payroll")}, today)
    fresh = p.build("fresh", (), fresh_list, [])
    failed = p.failed + ([f"fresh.{k}" for k in feeds.get("failed") or []] if p.ok("feeds") else ["fresh.feeds"])
    return {"as_of": today.isoformat(), "time": now.strftime("%H:%M"), "todo": build_todo(items), "tiles": built, "profit": profit,
            "fresh": fresh, "failed": failed}


def build_trends(src: dict, today: date) -> dict:
    """Sources -> the /api/home/trends answer. A chart that could not be built is {"ready": False} and is named in "failed", so the
    page can tell "no data" from "did not load"."""
    p = _Parts(src)
    return {"sales_daily": p.build("sales_daily", ("route_days",), lambda r: build_sales_daily(r, today), {"ready": False}),
            "profit_branch": p.build("profit_branch", ("books",), build_profit_branch, {"ready": False}), "failed": p.failed}


def load(*, fresh: bool = False) -> dict:
    """GET /api/home: everything but the charts. Kept HOME_SECONDS; fresh=True reads every source again."""
    def build() -> dict:
        now = datetime.now(mgr.THAI_TZ)
        if fresh:
            supabase._forget()                                # the 5-second copies of the small tables (vehicles, employees, settings ...)
        return build_home(_gather(HOME_SOURCES, now.date(), fresh), now.date(), now)
    return _cached("answer.home", HOME_SECONDS, build, fresh, keep=lambda v: not v["failed"])


def trends(*, fresh: bool = False) -> dict:
    """GET /api/home/trends: the two charts, asked for after the first paint. Kept TRENDS_SECONDS."""
    def build() -> dict:
        today = mgr.today_th()
        return build_trends(_gather(TREND_SOURCES, today, fresh), today)
    return _cached("answer.trends", TRENDS_SECONDS, build, fresh, keep=lambda v: not v["failed"])
