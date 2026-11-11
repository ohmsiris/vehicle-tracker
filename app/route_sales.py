"""
รถสาย · ใบขาย: the route trucks' daily sales sheet, kept by the driver on their phone (static/driver.*, served at /d/) -- owner, 10 Nov 2026.

It replaces the paper sheet one route at a time (route 35 first). The paper's layout stays: customers in route order with their bins
and price, products ซอง กั๊ก หลอด เล็ก แพ็ค โม่, loads (รับ) by trip, returns (คืน), what was sold, cash counted by note, mileage.

* Routes and customers come from the office's Word sheets (parse_sheet; scripts/import_route_sheets.py).
* A driver signs in once per phone with name + 4-digit PIN (the office sets it); the phone keeps a long random token (only its sha256
  is stored) and stays signed in until the office signs it out. Five wrong PINs lock the name for 10 minutes.
* The phone keeps the whole day as one document and sends all of it on every change (it works offline and catches up later); `rev`
  only goes up, so an old save that arrives late never overwrites a newer one. The server works out the day's totals and checks
  (compute) and rebuilds one sale_lines row per customer for reports.
* Customers (owner, 10 Nov 2026): only the owner and the bookkeeper add customers to a route. A shop not on the list is sold under
  เพิ่มลูกค้าเอง: the driver types the shop's name and price, it is cash only and lives on that day's sheet ("new:<n>" keys in
  doc.new_customers), never on the route list. Every such sale is flagged for the office, which turns it into a real customer
  (walkin_to_customer) or leaves it as a one-off.
* Chain stores (Lotus, Big C, 7-11, CJ ...) are วางบิล: the driver brings back a signed paper bill, no cash is taken.
* Locking (owner, 10 Nov 2026): the phone can only change TODAY's sheet, plus YESTERDAY's if it was never sent, until noon. Sending
  (ส่งใบขาย) locks the day. The driver can ask the office to reopen it; the office reopens it (the driver then fixes and sends
  again) and every request / reopen is kept in reopen_log. The server enforces all of this, so an old phone or an old offline copy
  cannot overwrite a sent day.
"""
import hashlib
import hmac
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo("Asia/Bangkok")
PRODUCTS = [("zong", "ซอง"), ("kak", "กั๊ก"), ("lod", "ใหญ่"), ("lek", "เล็ก"), ("pack", "แพ็ค"), ("mo", "บด")]   # owner's names (10 Nov 2026)
SHORT = {"zong": "ซ", "kak": "ก", "lod": "ญ", "lek": "ล", "pack": "พ", "mo": "บ"}       # one letter, for the บนรถ card
KEYS = [k for k, _ in PRODUCTS]
NOTES = [1000, 500, 100, 50, 20]                 # cash counted by note, plus coins as one amount
CHAIN = re.compile(r"lotus|big\s*c|7-11|เซเว่น|cj\s*x?|ซีเจ|tops|makro|แม็คโคร", re.I)
PIN_TRIES, LOCK_MINUTES = 5, 10


class RouteSalesError(ValueError):
    """A request the app should refuse with a message (shown to the driver or the office as it is)."""


def today() -> date:
    return datetime.now(BANGKOK).date()


def _num(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if v == v and abs(v) < 1e9 else 0.0          # no NaN / absurd numbers from a phone


# --------------------------------------------------------------------------------------------- the Word sheets (pure)
def _price(text: str) -> Optional[float]:
    m = re.search(r"\d+(?:\.\d+)?", (text or "").replace(",", ""))
    return float(m.group()) if m else None


def customer_from_row(cells: list[str]) -> Optional[dict]:
    """One table row of the sheet: 'n. name' | bins | price | ซอง | กั๊ก | หลอด | เล็ก | แพ็ค | โม่ | ... -> a customer, or None."""
    m = re.match(r"^\s*(\d+)\s*\.\s*(.+)$", cells[0] if cells else "")
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group(2)).strip()
    bins = ", ".join(b.strip() for b in (cells[1] if len(cells) > 1 else "").split("/") if b.strip())
    free = "ฟรี" in (cells[2] if len(cells) > 2 else "")
    base = _price(cells[2]) if len(cells) > 2 else None
    prices = {}
    for i, k in enumerate(KEYS, start=3):
        v = _price(cells[i]) if len(cells) > i else None
        if v:
            prices[k] = v
    chain = bool(CHAIN.search(name))
    if base and not prices:
        if chain:
            prices = {"pack": base}                      # Lotus / Big C / 7-11 buy แพ็ค at their contract price
        elif base >= 100:
            prices = {"zong": base}                      # a lone price of 100+ on a normal customer is a block price
        else:
            prices = {"lod": base, "lek": base, "mo": base}
    elif base:
        for k in ("lod", "lek", "mo"):                   # own prices for some products; the usual price for the bags not listed
            prices.setdefault(k, base)
    return {"seq": int(m.group(1)), "name": name, "bins": bins or None, "prices": {} if free else prices, "billing": "invoice" if chain else "cash",
            "free": free}


def parse_sheet(rows: list[list[str]]) -> dict:
    """All table rows of one driver's Word sheet -> {truck_no, driver_name, driver_phone, name, customers}. The sheet repeats its
    header on every page; customers keep their own numbers (the route order)."""
    out = {"truck_no": None, "driver_name": None, "driver_phone": None, "name": None, "customers": []}
    seen = set()
    for r in rows:
        if not r:
            continue
        if r[0] == "รถเบอร์" and out["truck_no"] is None:
            out["truck_no"] = (r[1] if len(r) > 1 else "").strip() or None
            out["driver_name"] = (r[3] if len(r) > 3 else "").strip() or None
        elif r[0] == "สาย" and out["name"] is None:
            out["name"] = (r[1] if len(r) > 1 else "").strip() or None
            out["driver_phone"] = (r[3] if len(r) > 3 else "").strip() or None
        else:
            c = customer_from_row(r)
            if c and c["seq"] not in seen:
                seen.add(c["seq"])
                out["customers"].append(c)
    out["customers"].sort(key=lambda c: c["seq"])
    return out


# --------------------------------------------------------------------------------------------- a day's sheet (pure)
def line_amount(qty: dict, price: dict) -> float:
    return round(sum(_num(qty.get(k)) * _num(price.get(k)) for k in KEYS), 2)


def compute(doc: dict, customers: dict, returns: Optional[dict] = None, km_start: Optional[float] = None) -> dict:
    """doc = what the phone sent; customers = {customer id (str): {"prices", "billing", "free"}}. -> the day's totals and what does
    not add up. A sale line: {"q": {product: n}, "paid": baht (prices come from the route list only),
    "old_paid": baht, "discount": baht, "free": true (given free this once), "reason": "factory" | "other"}.
    A discount or one-off free with reason "factory" (น้ำแข็งเสีย / แตก -- the plant's fault, owner 10 Nov 2026) still counts at full
    price in `commission_sales`, so the driver's percentage is not cut by the plant's mistake. Customers free on the route list
    (ตำรวจ ...) are not sales and do not count, as on the paper sheet.
    returns = the ice back at the plant, counted by หน้าโรง (the office enters it); km_start = the last reading before this day."""
    loaded = {k: 0.0 for k in KEYS}
    for ld in doc.get("loads") or []:
        for k in KEYS:
            loaded[k] += _num((ld.get("q") or {}).get(k))
    sold = {k: 0.0 for k in KEYS}
    free_q = {k: 0.0 for k in KEYS}
    lines = []
    cash_sales = old_paid = credit = invoiced = amount_all = discounts = factory = other_waived = 0.0
    discount_shops = adhoc_free = 0
    flags_extra: list = []
    walkins: list = []
    for cid, s in (doc.get("sales") or {}).items():
        q = {k: _num((s.get("q") or {}).get(k)) for k in KEYS}
        if not any(q.values()) and not _num(s.get("old_paid")):
            continue
        cust = customers.get(str(cid)) or {}
        free = bool(cust.get("free") or s.get("free"))
        price = cust.get("prices") or {}                 # the route list's price only: the phone cannot change a price (owner, 10 Nov 2026)
        if not cust.get("free"):
            unpriced = [lab for k, lab in PRODUCTS if q[k] and _num(price.get(k)) <= 0]
            if unpriced:
                flags_extra.append(f"{cust.get('name') or cid}: ขาย {' '.join(unpriced)} แต่ร้านนี้ไม่มีราคา")
        full = 0.0 if cust.get("free") else line_amount(q, price)          # what the line is worth at the customer's price
        gross = 0.0 if free else full
        disc = 0.0 if free else min(max(_num(s.get("discount")), 0.0), gross)
        amount = round(gross - disc, 2)
        waived = round(full - amount, 2) if not cust.get("free") else 0.0  # given away: a discount, or ice given free this once
        reason = ("factory" if s.get("reason") == "factory" else "other") if waived else None
        if reason == "factory":
            factory += waived
        elif waived:
            other_waived += waived
        billing = "cash" if cust.get("walkin") else cust.get("billing") or s.get("billing") or "cash"
        paid = 0.0 if billing == "invoice" or free else amount if cust.get("walkin") else min(max(_num(s.get("paid", amount)), 0.0), amount)
        if cust.get("walkin") and any(q.values()):           # a shop the driver added: cash only, and the owner is told
            walkins.append({"key": str(cid), "name": cust.get("name") or "", "phone": cust.get("phone") or "", "amount": amount})
        op = max(_num(s.get("old_paid")), 0.0)
        for k in KEYS:
            sold[k] += q[k]
            if free:
                free_q[k] += q[k]
        if disc:
            discounts += disc
            discount_shops += 1
        if s.get("free") and not cust.get("free") and any(q.values()):
            adhoc_free += 1
        amount_all += amount
        if billing == "invoice":
            invoiced += amount
        else:
            cash_sales += paid
            credit += amount - paid
        old_paid += op
        lines.append({"customer": str(cid), "qty": {k: v for k, v in q.items() if v}, "amount": amount, "paid": paid, "old_paid": op,
                      "billing": billing, "discount": round(disc, 2), "free": free, "waived": waived, "waived_reason": reason})
    back = returns if returns is not None else doc.get("returns")
    returned = {k: _num((back or {}).get(k)) for k in KEYS}
    left = {k: loaded[k] - sold[k] for k in KEYS}
    diff = {k: returned[k] - left[k] for k in KEYS} if back else {k: 0.0 for k in KEYS}   # + เกิน / - ขาด
    counted = sum(_num((doc.get("cash") or {}).get(str(n))) * n for n in NOTES) + _num((doc.get("cash") or {}).get("coins"))
    expected_cash = cash_sales + old_paid
    start = km_start if km_start is not None else _num(doc.get("km_start"))
    km_end = _num(doc.get("km_end"))
    km = km_end - start if start and km_end else None
    flags = []
    for k, label in PRODUCTS:
        if back and diff[k]:
            flags.append(f"{label} {'เกิน' if diff[k] > 0 else 'ขาด'} {abs(diff[k]):g}")
        if left[k] < 0:
            flags.append(f"{label} ขายมากกว่าที่รับ {abs(left[k]):g}")
    has_cash = bool(doc.get("cash"))
    if has_cash and abs(counted - expected_cash) >= 1:
        flags.append(f"เงิน{'เกิน' if counted > expected_cash else 'ขาด'} {abs(counted - expected_cash):,.0f} บาท")
    if km is not None and (km < 0 or km > 600):
        flags.append(f"ไมล์ดูไม่ถูก ({km:,.0f} กม.)")
    for w in walkins:
        flags.append(f"ลูกค้าเพิ่มเอง: {w['name']} ฿{w['amount']:,.0f} — ยังไม่อยู่ในรายชื่อสาย")
    flags += flags_extra
    if factory:
        flags.append(f"ลด / แจกเพราะน้ำแข็งเสีย (โรงงาน) {factory:,.0f} บาท — ไม่หักค่าคอมคนขับ")
    if other_waived:
        flags.append(f"ส่วนลด / แจกฟรี อื่นๆ {other_waived:,.0f} บาท — ตรวจว่าอนุญาตแล้ว")
    return {"loaded": loaded, "sold": sold, "left": left, "returned": returned if back else None, "diff": diff, "free": free_q,
            "amount": round(amount_all, 2), "cash_sales": round(cash_sales, 2), "old_paid": round(old_paid, 2),
            "credit": round(credit, 2), "invoiced": round(invoiced, 2), "discount": round(discounts, 2), "expected_cash": round(expected_cash, 2),
            "factory_waived": round(factory, 2), "other_waived": round(other_waived, 2), "commission_sales": round(amount_all + factory, 2),
            "counted": round(counted, 2) if has_cash else None, "km_start": start or None, "km": km, "customers": len(lines), "flags": flags,
            "walkins": walkins, "lines": lines}


# --------------------------------------------------------------------------------------------- which day the phone may change (pure)
NOON = 12


def edit_refusal(day: date, status: Optional[str], now: datetime) -> Optional[str]:
    """None when the phone may save this day; otherwise the reason, shown to the driver as it is. now = Bangkok time."""
    t = now.date()
    if status in ("closed", "checked"):
        return "ส่งใบขายวันนี้แล้ว — ล็อกแล้ว ถ้าต้องแก้ ให้กด ขอให้ออฟฟิศเปิดแก้"
    if day == t:
        return None
    if day == t - timedelta(days=1):
        return None if now.hour < NOON else "เลยเที่ยงแล้ว ใบขายเมื่อวานล็อก — ขอให้ออฟฟิศเปิดแก้"
    return "แก้ได้เฉพาะใบขายวันนี้"


def unsent(day: date, status: Optional[str], now: datetime) -> bool:
    """A past day the driver never sent and can no longer send (yesterday after noon, or older). now = Bangkok time."""
    return status == "open" and day < now.date() and edit_refusal(day, status, now) is not None


def phone_label(agent: str) -> str:
    """A short name for the phone a driver signed in from, worked out from the browser's User-Agent: "Android · Chrome"."""
    a = agent or ""
    kind = "iPhone" if "iPhone" in a else "iPad" if "iPad" in a else "Android" if "Android" in a else \
        "Windows" if "Windows" in a else "Mac" if "Macintosh" in a else "เครื่องอื่น"
    app = "LINE" if re.search(r"\bLine/", a) else "Facebook" if ("FBAN" in a or "FBAV" in a) else \
        "Samsung" if "SamsungBrowser" in a else "Edge" if "Edg" in a else "Firefox" if ("Firefox" in a or "FxiOS" in a) else \
        "Chrome" if ("Chrome" in a or "CriOS" in a) else "Safari" if "Safari" in a else ""
    return f"{kind} · {app}" if app else kind


# --------------------------------------------------------------------------------------------- sign in
def hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 120_000).hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def valid_pin(pin: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", pin or ""))


def drivers_for_login() -> list[dict]:
    from app import supabase
    routes = {r["id"]: r for r in supabase.select_all("sale_routes", {"select": "id,truck_no,name", "order": "id.asc"})}
    out = []
    for d in supabase.select_all("sale_drivers", {"select": "id,name,route_id,pin_hash,active", "active": "eq.true", "order": "id.asc"}):
        if d["pin_hash"] and d["route_id"] in routes:
            r = routes[d["route_id"]]
            out.append({"id": d["id"], "name": d["name"], "truck_no": r["truck_no"], "route": r["name"]})
    return sorted(out, key=lambda x: (len(x["truck_no"]), x["truck_no"]))


def login(driver_id: int, pin: str, agent: str = "") -> str:
    from app import supabase
    rows = supabase.select("sale_drivers", {"select": "*", "id": f"eq.{int(driver_id)}", "active": "eq.true", "limit": "1"})
    if not rows or not rows[0]["pin_hash"]:
        raise RouteSalesError("ไม่พบชื่อนี้ หรือยังไม่ได้ตั้งรหัส — ติดต่อออฟฟิศ")
    d = rows[0]
    now = datetime.now(timezone.utc)
    if d["locked_until"] and datetime.fromisoformat(d["locked_until"]) > now:
        raise RouteSalesError(f"ใส่รหัสผิดหลายครั้ง ลองใหม่ใน {LOCK_MINUTES} นาที หรือให้ออฟฟิศตั้งรหัสใหม่")
    if not valid_pin(pin) or not hmac.compare_digest(hash_pin(pin, d["pin_salt"]), d["pin_hash"]):
        failed = d["failed"] + 1
        lock = (now + timedelta(minutes=LOCK_MINUTES)).isoformat() if failed >= PIN_TRIES else None
        supabase.update("sale_drivers", {"id": f"eq.{d['id']}"}, {"failed": 0 if lock else failed, "locked_until": lock})
        raise RouteSalesError("รหัสไม่ถูกต้อง" + (f" — ล็อก {LOCK_MINUTES} นาที" if lock else f" (เหลือ {PIN_TRIES - failed} ครั้ง)"))
    supabase.update("sale_drivers", {"id": f"eq.{d['id']}"}, {"failed": 0, "locked_until": None})
    token = secrets.token_urlsafe(32)
    supabase.insert("sale_driver_devices", [{"token_hash": _token_hash(token), "driver_id": d["id"], "last_seen": now.isoformat(),
                                             "agent": phone_label(agent)[:60]}])
    return token


def driver_for_token(token: str) -> dict:
    from app import supabase
    if not token or len(token) > 200:
        raise RouteSalesError("signin")
    dev = supabase.select("sale_driver_devices", {"select": "driver_id,revoked,last_seen", "token_hash": f"eq.{_token_hash(token)}", "limit": "1"})
    if not dev or dev[0]["revoked"]:
        raise RouteSalesError("signin")
    d = supabase.select("sale_drivers", {"select": "id,name,route_id,active", "id": f"eq.{dev[0]['driver_id']}", "limit": "1"})
    if not d or not d[0]["active"] or not d[0]["route_id"]:
        raise RouteSalesError("signin")
    now = datetime.now(timezone.utc)
    last = dev[0]["last_seen"]
    if not last or now - datetime.fromisoformat(last) > timedelta(minutes=10):       # no write on every request
        supabase.update("sale_driver_devices", {"token_hash": f"eq.{_token_hash(token)}"}, {"last_seen": now.isoformat()})
    return d[0]


def logout(token: str) -> None:
    from app import supabase
    supabase.update("sale_driver_devices", {"token_hash": f"eq.{_token_hash(token)}"}, {"revoked": True})


# --------------------------------------------------------------------------------------------- the driver's page
def _customers(route_id: int) -> list[dict]:
    from app import supabase
    return supabase.select_all("sale_route_customers", {"select": "id,seq,name,bins,prices,billing,free,approved,added_by_driver",
                                                        "route_id": f"eq.{route_id}", "active": "eq.true", "order": "seq.asc,id.asc"})


def _balances(route_id: int) -> dict:
    """ค้าง per customer: everything sold on credit minus old debt paid, over all the days in the app."""
    from app import supabase
    bal: dict = {}
    for ln in supabase.select_all("sale_lines", {"select": "customer_id,amount,paid,old_paid,billing", "route_id": f"eq.{route_id}", "order": "id.asc"}):
        if ln["billing"] == "invoice" or not ln["customer_id"]:
            continue
        bal[ln["customer_id"]] = bal.get(ln["customer_id"], 0.0) + _num(ln["amount"]) - _num(ln["paid"]) - _num(ln["old_paid"])
    return {str(k): round(v, 2) for k, v in bal.items() if abs(v) >= 1}


def _day(route_id: int, day: date) -> Optional[dict]:
    from app import supabase
    rows = supabase.select("sale_days", {"select": "id,day,doc,rev,status,totals,updated_at,closed_at,reopen_requested_at,reopen_log,returns_foh",
                                         "route_id": f"eq.{route_id}",
                                         "day": f"eq.{day.isoformat()}", "limit": "1"})
    return rows[0] if rows else None


def _last_km(route_id: int, before: date) -> Optional[float]:
    from app import supabase
    rows = supabase.select("sale_days", {"select": "doc", "route_id": f"eq.{route_id}", "day": f"lt.{before.isoformat()}",
                                         "order": "day.desc", "limit": "5"})
    for r in rows:
        if _num((r["doc"] or {}).get("km_end")):
            return _num(r["doc"]["km_end"])
    return None


def _sheet(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return None
    return {"doc": d["doc"], "rev": d["rev"], "status": d["status"], "closed_at": d.get("closed_at"),
            "reopen_requested_at": d.get("reopen_requested_at"), "returns_foh": d.get("returns_foh")}


def bootstrap(driver: dict, day: Optional[date] = None) -> dict:
    """The phone's start: the route, its customers and debts, and the sheet for `day` (today, unless the phone asks for an unsent
    yesterday). `pending_yesterday` is offered only while yesterday can still be finished (never sent, before noon)."""
    from app import supabase
    now = datetime.now(BANGKOK)
    t = now.date()
    day = day if day in (t, t - timedelta(days=1)) else t
    r = supabase.select("sale_routes", {"select": "id,truck_no,name,branch", "id": f"eq.{driver['route_id']}", "limit": "1"})[0]
    d = _day(r["id"], day)
    y = _day(r["id"], t - timedelta(days=1)) if day == t else None
    pending = y if y and y["status"] == "open" and now.hour < NOON else None
    return {"driver": {"id": driver["id"], "name": driver["name"]}, "route": r, "today": t.isoformat(), "day": day.isoformat(),
            "products": [{"key": k, "label": l, "short": SHORT[k]} for k, l in PRODUCTS], "notes": NOTES,
            "customers": _customers(r["id"]), "balances": _balances(r["id"]), "last_km": _last_km(r["id"], day),
            "sheet": _sheet(d), "pending_yesterday": pending["day"] if pending else None,
            "editable": edit_refusal(day, d["status"] if d else None, now) is None,
            "order_today": _order(r["id"], day), "order_next": _order(r["id"], day + timedelta(days=1)),
            "next_day": (day + timedelta(days=1)).isoformat()}


def save_day(driver: dict, day_text: str, rev: int, doc: dict) -> dict:
    """The phone's whole day. Shops the driver added (เพิ่มลูกค้าเอง, "new:<n>" keys) stay on this day only: the route list is never
    changed from a phone (owner, 10 Nov 2026). `id_map` is kept in the answer (always empty) for phones running an older page."""
    from app import supabase
    try:
        day = date.fromisoformat(day_text)
    except (TypeError, ValueError):
        raise RouteSalesError("วันที่ไม่ถูกต้อง")
    if not isinstance(doc, dict) or len(str(doc)) > 400_000:
        raise RouteSalesError("ข้อมูลไม่ถูกต้อง")
    route_id = driver["route_id"]
    current = _day(route_id, day)
    if current and int(rev) <= int(current["rev"]):          # a late copy of a save the server already has: nothing to do
        return {"rev": current["rev"], "status": current["status"], "totals": _public(current["totals"]), "id_map": {}, "stale": True}
    refusal = edit_refusal(day, current["status"] if current else None, datetime.now(BANGKOK))
    if refusal:
        raise RouteSalesError(refusal)
    doc = {**doc, "new_customers": clean_walkins(doc.get("new_customers"))}
    customers = {**{str(c["id"]): c for c in _customers(route_id)}, **walkin_customers(doc)}
    id_map: dict = {}
    doc["sales"] = {k: v for k, v in (doc.get("sales") or {}).items() if k in customers}      # a deleted customer's line is dropped
    totals = compute(doc, customers, (current or {}).get("returns_foh"), _last_km(route_id, day))
    status = "closed" if doc.get("closed") else "open"
    now = datetime.now(timezone.utc).isoformat()
    row = {"route_id": route_id, "day": day.isoformat(), "driver_id": driver["id"], "doc": doc, "rev": int(rev), "status": status,
           "totals": {k: v for k, v in totals.items() if k != "lines"}, "updated_at": now, "closed_at": now if status == "closed" else None}
    saved = supabase.insert("sale_days", [row], on_conflict="route_id,day")[0]
    supabase.delete("sale_lines", {"day_id": f"eq.{saved['id']}"})
    lines = [{"day_id": saved["id"], "route_id": route_id, "day": day.isoformat(), **_line_who(ln["customer"], customers), "qty": ln["qty"],
              "amount": ln["amount"], "paid": ln["paid"], "old_paid": ln["old_paid"], "billing": ln["billing"], "discount": ln["discount"],
              "free": ln["free"], "waived": ln["waived"], "waived_reason": ln["waived_reason"]} for ln in totals["lines"]]
    for i in range(0, len(lines), 500):
        supabase.insert("sale_lines", lines[i:i + 500])
    return {"rev": int(rev), "status": status, "totals": _public(row["totals"]), "id_map": id_map}


def clean_walkins(raw) -> list:
    """The shops a driver added on this day, tidied: [{"key": "new:<n>", "name", "phone", "prices": {product: baht}}]."""
    out, seen = [], set()
    for nc in raw if isinstance(raw, list) else []:
        if not isinstance(nc, dict):
            continue
        key = str(nc.get("key") or "")[:40]
        name = re.sub(r"\s+", " ", str(nc.get("name") or "")).strip()[:120]
        if not key.startswith("new:") or not name or key in seen:
            continue
        seen.add(key)
        prices = {k: _num(v) for k, v in (nc.get("prices") or {}).items() if k in KEYS and _num(v) > 0}
        out.append({"key": key, "name": name, "phone": re.sub(r"[^\d+\- ]", "", str(nc.get("phone") or ""))[:20], "prices": prices})
    return out[:30]


def walkin_customers(doc: dict) -> dict:
    """The day's walk-in shops in the shape compute() takes for a customer."""
    return {w["key"]: {"name": w["name"], "phone": w.get("phone") or "", "prices": w.get("prices") or {}, "billing": "cash", "walkin": True}
            for w in clean_walkins(doc.get("new_customers"))}


def _line_who(cid: str, customers: dict) -> dict:
    """sale_lines' customer columns: the route customer's id, or no id and the walk-in shop's name."""
    if str(cid).isdigit():
        return {"customer_id": int(cid), "walkin": None}
    return {"customer_id": None, "walkin": (customers.get(str(cid)) or {}).get("name") or str(cid)}


def request_reopen(driver: dict, day_text: str) -> dict:
    from app import supabase
    try:
        day = date.fromisoformat(day_text)
    except (TypeError, ValueError):
        raise RouteSalesError("วันที่ไม่ถูกต้อง")
    d = _day(driver["route_id"], day)
    if not d or d["status"] == "open":
        raise RouteSalesError("วันนี้ยังไม่ได้ล็อก")
    now = datetime.now(timezone.utc).isoformat()
    log = (d.get("reopen_log") or []) + [{"at": now, "what": "requested"}]
    supabase.update("sale_days", {"id": f"eq.{d['id']}"}, {"reopen_requested_at": now, "reopen_log": log})
    return {"reopen_requested_at": now}


def _public(totals: dict) -> dict:
    return {k: v for k, v in (totals or {}).items() if k != "lines"}


# --------------------------------------------------------------------------------------------- the office
def routes_overview(branch: str = "สระบุรี") -> list[dict]:
    from app import supabase
    routes = supabase.select_all("sale_routes", {"select": "id,truck_no,name,driver_name,driver_phone,branch,active", "branch": f"eq.{branch}",
                                                 "order": "id.asc"})
    drivers = {d["route_id"]: d for d in supabase.select_all("sale_drivers", {"select": "id,route_id,name,pin_hash,active,locked_until",
                                                                               "order": "id.asc"})}
    counts: dict = {}
    for c in supabase.select_all("sale_route_customers", {"select": "route_id,approved", "active": "eq.true", "order": "id.asc"}):
        n = counts.setdefault(c["route_id"], [0, 0])
        n[0] += 1
        n[1] += 0 if c["approved"] else 1
    phones: dict = {}
    for dev in supabase.select_all("sale_driver_devices", {"select": "driver_id,agent,created_at,last_seen", "revoked": "eq.false",
                                                           "order": "last_seen.desc.nullslast"}):
        phones.setdefault(dev["driver_id"], []).append({"agent": dev.get("agent") or "ไม่ทราบรุ่น", "since": dev["created_at"],
                                                        "last_seen": dev["last_seen"]})
    now = datetime.now(BANGKOK)
    since = (today() - timedelta(days=14)).isoformat()
    last = {}
    late: dict = {}
    walk: dict = {}
    requests: dict = {}
    for dday in supabase.select_all("sale_days", {"select": "route_id,day,status,totals,reopen_requested_at", "day": f"gte.{since}",
                                                  "order": "day.asc"}):
        last[dday["route_id"]] = dday
        if unsent(date.fromisoformat(dday["day"]), dday["status"], now):
            late[dday["route_id"]] = late.get(dday["route_id"], 0) + 1
        if (dday.get("totals") or {}).get("walkins") and dday["status"] != "checked":
            walk[dday["route_id"]] = walk.get(dday["route_id"], 0) + len(dday["totals"]["walkins"])
        if dday["reopen_requested_at"]:
            requests[dday["route_id"]] = requests.get(dday["route_id"], 0) + 1
    out = []
    for r in routes:
        d = drivers.get(r["id"])
        n = counts.get(r["id"], [0, 0])
        out.append({**r, "customers": n[0], "new_customers": n[1], "pin_set": bool(d and d["pin_hash"]),
                    "driver_id": d["id"] if d else None, "last_day": last.get(r["id"]), "reopen_requests": requests.get(r["id"], 0),
                    "phones": phones.get(d["id"], []) if d else [], "unsent": late.get(r["id"], 0),
                    "walkins": walk.get(r["id"], 0)})
    return sorted(out, key=lambda x: (len(x["truck_no"]), x["truck_no"]))


def set_pin(route_id: int, pin: str) -> dict:
    from app import supabase
    if not valid_pin(pin):
        raise RouteSalesError("รหัสต้องเป็นตัวเลข 4 หลัก")
    r = supabase.select("sale_routes", {"select": "id,driver_name,truck_no", "id": f"eq.{int(route_id)}", "limit": "1"})
    if not r:
        raise RouteSalesError("ไม่พบสายนี้")
    salt = secrets.token_hex(16)
    values = {"pin_salt": salt, "pin_hash": hash_pin(pin, salt), "failed": 0, "locked_until": None, "active": True}
    d = supabase.select("sale_drivers", {"select": "id", "route_id": f"eq.{r[0]['id']}", "limit": "1"})
    if d:
        supabase.update("sale_drivers", {"id": f"eq.{d[0]['id']}"}, values)
        return {"driver_id": d[0]["id"]}
    row = supabase.insert("sale_drivers", [{"route_id": r[0]["id"], "name": r[0]["driver_name"] or f"สาย {r[0]['truck_no']}", **values}])[0]
    return {"driver_id": row["id"]}


def sign_out_phones(route_id: int) -> int:
    from app import supabase
    d = supabase.select("sale_drivers", {"select": "id", "route_id": f"eq.{int(route_id)}", "limit": "1"})
    if not d:
        return 0
    return len(supabase.update("sale_driver_devices", {"driver_id": f"eq.{d[0]['id']}", "revoked": "eq.false"}, {"revoked": True}))


def route_days(route_id: int, days: int = 31) -> dict:
    from app import supabase
    since = (today() - timedelta(days=days)).isoformat()
    rows = supabase.select_all("sale_days", {"select": "id,day,status,totals,updated_at,closed_at,checked_at,reopen_requested_at,reopen_log",
                                             "route_id": f"eq.{int(route_id)}",
                                             "day": f"gte.{since}", "order": "day.desc"})
    now = datetime.now(BANGKOK)
    for x in rows:
        x["unsent"] = unsent(date.fromisoformat(x["day"]), x["status"], now)
    return {"days": rows}


def day_detail(day_id: int) -> dict:
    from app import supabase
    d = supabase.select("sale_days", {"select": "*", "id": f"eq.{int(day_id)}", "limit": "1"})
    if not d:
        raise RouteSalesError("ไม่พบวันนี้")
    cust = {str(c["id"]): c for c in _customers(d[0]["route_id"])}
    walk = {w["name"]: w for w in clean_walkins((d[0]["doc"] or {}).get("new_customers"))}
    lines = supabase.select_all("sale_lines", {"select": "customer_id,walkin,qty,amount,paid,old_paid,billing,discount,free,waived,waived_reason",
                                               "day_id": f"eq.{int(day_id)}", "order": "id.asc"})
    for ln in lines:
        if ln.get("walkin"):
            w = walk.get(ln["walkin"]) or {}
            ln["name"], ln["seq"], ln["approved"] = ln["walkin"], 9999, False
            ln["walkin_key"], ln["phone"], ln["prices"] = w.get("key"), w.get("phone"), w.get("prices")
            continue
        c = cust.get(str(ln["customer_id"])) or {}
        ln["name"], ln["seq"], ln["approved"] = c.get("name"), c.get("seq"), c.get("approved", True)
    lines.sort(key=lambda x: x["seq"] or 0)
    d[0]["unsent"] = unsent(date.fromisoformat(d[0]["day"]), d[0]["status"], datetime.now(BANGKOK))
    return {"day": d[0], "lines": lines, "products": [{"key": k, "label": l} for k, l in PRODUCTS]}


def reopen_day(day_id: int) -> dict:
    """The office lets the driver change a sent day again. rev goes up, so the server's copy (now open) wins over what the phone
    kept; the driver fixes it and has to send it again."""
    from app import supabase
    d = supabase.select("sale_days", {"select": "id,doc,rev,status,reopen_log", "id": f"eq.{int(day_id)}", "limit": "1"})
    if not d:
        raise RouteSalesError("ไม่พบวันนี้")
    d = d[0]
    if d["status"] == "open":
        return {"status": "open"}
    now = datetime.now(timezone.utc).isoformat()
    supabase.update("sale_days", {"id": f"eq.{d['id']}"}, {
        "status": "open", "rev": int(d["rev"]) + 1, "doc": {**(d["doc"] or {}), "closed": False}, "closed_at": None, "checked_at": None,
        "reopen_requested_at": None, "reopen_log": (d.get("reopen_log") or []) + [{"at": now, "what": "reopened"}]})
    return {"status": "open"}


def set_returns(day_id: int, returns: dict) -> dict:
    """หน้าโรง counts the ice that came back (any time, even after the day is sent); the day's totals and checks are worked out again."""
    from app import supabase
    d = supabase.select("sale_days", {"select": "id,route_id,day,doc", "id": f"eq.{int(day_id)}", "limit": "1"})
    if not d:
        raise RouteSalesError("ไม่พบวันนี้")
    d = d[0]
    back = {k: _num(v) for k, v in (returns or {}).items() if k in KEYS and str(v).strip() != ""}
    customers = {**{str(c["id"]): c for c in _customers(d["route_id"])}, **walkin_customers(d["doc"] or {})}
    totals = compute(d["doc"] or {}, customers, back, _last_km(d["route_id"], date.fromisoformat(d["day"])))
    supabase.update("sale_days", {"id": f"eq.{d['id']}"}, {"returns_foh": back, "returns_at": datetime.now(timezone.utc).isoformat(),
                                                        "totals": _public(totals)})
    return {"totals": _public(totals)}


def walkin_to_customer(day_id: int, key: str) -> dict:
    """The office makes a shop the driver added (เพิ่มลูกค้าเอง) a real customer at the end of the route, with the price the driver
    used; the day's sheet is moved over to it (its rev goes up, so the phone takes the office's copy)."""
    from app import supabase
    d = supabase.select("sale_days", {"select": "id,route_id,day,doc,rev,returns_foh", "id": f"eq.{int(day_id)}", "limit": "1"})
    if not d:
        raise RouteSalesError("ไม่พบวันนี้")
    d = d[0]
    doc = d["doc"] or {}
    w = next((x for x in clean_walkins(doc.get("new_customers")) if x["key"] == key), None)
    if not w:
        raise RouteSalesError("ไม่พบร้านนี้ในใบขายวันนี้ (อาจเพิ่มเข้ารายชื่อไปแล้ว)")
    existing = _customers(d["route_id"])
    row = supabase.insert("sale_route_customers", [{"route_id": d["route_id"], "seq": max([c["seq"] for c in existing] or [0]) + 1,
                                                    "name": w["name"], "prices": w["prices"], "billing": "cash", "added_by_driver": True,
                                                    "approved": True}])[0]
    sales = dict(doc.get("sales") or {})
    if key in sales:
        sales[str(row["id"])] = sales.pop(key)
    doc = {**doc, "sales": sales, "new_customers": [x for x in clean_walkins(doc.get("new_customers")) if x["key"] != key]}
    customers = {**{str(c["id"]): c for c in existing}, str(row["id"]): row, **walkin_customers(doc)}
    totals = compute(doc, customers, d.get("returns_foh"), _last_km(d["route_id"], date.fromisoformat(d["day"])))
    supabase.update("sale_days", {"id": f"eq.{d['id']}"}, {"doc": doc, "rev": int(d["rev"]) + 1, "totals": _public(totals)})
    supabase.delete("sale_lines", {"day_id": f"eq.{d['id']}"})
    lines = [{"day_id": d["id"], "route_id": d["route_id"], "day": d["day"], **_line_who(ln["customer"], customers), "qty": ln["qty"],
              "amount": ln["amount"], "paid": ln["paid"], "old_paid": ln["old_paid"], "billing": ln["billing"], "discount": ln["discount"],
              "free": ln["free"], "waived": ln["waived"], "waived_reason": ln["waived_reason"]} for ln in totals["lines"]]
    if lines:
        supabase.insert("sale_lines", lines)
    return {"customer_id": row["id"]}


def attention() -> dict:
    """The number beside ใบขายรถสาย in the menu: shops added by drivers not checked yet, reopen requests, days never sent."""
    rows = routes_overview()
    return {"walkins": sum(r["walkins"] for r in rows), "reopen_requests": sum(r["reopen_requests"] for r in rows),
            "unsent": sum(r["unsent"] for r in rows)}


# --------------------------------------------------------------------------------------------- tomorrow's ice (owner, 11 Nov 2026)
def clean_order(q) -> dict:
    return {k: _num(v) for k, v in (q or {}).items() if k in KEYS and _num(v) > 0}


def _order(route_id: int, day: date) -> Optional[dict]:
    from app import supabase
    rows = supabase.select("sale_orders", {"select": "day,q,note,by_name,updated_at", "route_id": f"eq.{int(route_id)}",
                                           "day": f"eq.{day.isoformat()}", "limit": "1"})
    return rows[0] if rows else None


def save_order(route_id: int, day: date, q: dict, by_name: str, note: Optional[str] = None) -> dict:
    """An order for `day` (empty = no order). The phone may order for today and tomorrow; the office for any day."""
    from app import supabase
    q = clean_order(q)
    if not q:
        supabase.delete("sale_orders", {"route_id": f"eq.{int(route_id)}", "day": f"eq.{day.isoformat()}"})
        return {"order": None}
    row = supabase.insert("sale_orders", [{"route_id": int(route_id), "day": day.isoformat(), "q": q, "by_name": (by_name or "")[:60],
                                           "note": (note or "")[:200] or None, "updated_at": datetime.now(timezone.utc).isoformat()}],
                          on_conflict="route_id,day")[0]
    return {"order": {k: row[k] for k in ("day", "q", "note", "by_name", "updated_at")}}


def driver_order(driver: dict, day_text: str, q: dict) -> dict:
    try:
        day = date.fromisoformat(day_text)
    except (TypeError, ValueError):
        raise RouteSalesError("วันที่ไม่ถูกต้อง")
    t = today()
    if day not in (t, t + timedelta(days=1)):
        raise RouteSalesError("สั่งได้เฉพาะวันนี้และพรุ่งนี้")
    return save_order(driver["route_id"], day, q, driver["name"])


def orders_for(day: date, branch: str = "สระบุรี") -> dict:
    """ยอดสั่งน้ำแข็ง: every route's order for `day`, the totals, and who has not ordered."""
    from app import supabase
    routes = supabase.select_all("sale_routes", {"select": "id,truck_no,name,driver_name,active", "branch": f"eq.{branch}", "order": "id.asc"})
    got = {o["route_id"]: o for o in supabase.select_all("sale_orders", {"select": "route_id,q,note,by_name,updated_at",
                                                                         "day": f"eq.{day.isoformat()}", "order": "id.asc"})}
    rows, total = [], {k: 0.0 for k in KEYS}
    for r in sorted((x for x in routes if x.get("active", True)), key=lambda x: (len(x["truck_no"]), x["truck_no"])):
        o = got.get(r["id"])
        for k, v in ((o or {}).get("q") or {}).items():
            if k in total:
                total[k] += _num(v)
        rows.append({"route_id": r["id"], "truck_no": r["truck_no"], "driver": r.get("driver_name"), "route": r.get("name"),
                     "q": (o or {}).get("q") or {}, "note": (o or {}).get("note"), "by_name": (o or {}).get("by_name"),
                     "updated_at": (o or {}).get("updated_at")})
    return {"day": day.isoformat(), "today": today().isoformat(), "rows": rows, "total": total,
            "ordered": sum(1 for x in rows if x["q"]), "products": [{"key": k, "label": lab, "short": SHORT[k]} for k, lab in PRODUCTS]}


# --------------------------------------------------------------------------------------------- the office edits a day itself
OFFICE_REV_STEP = 1000      # the office's save always wins over a phone's copy: the phone sees it is behind and takes the office's


def clean_doc(doc: dict) -> dict:
    """A day's sheet from the office page, tidied to the phone's shape (the same compute() reads both)."""
    if not isinstance(doc, dict) or len(str(doc)) > 400_000:
        raise RouteSalesError("ข้อมูลไม่ถูกต้อง")
    q = lambda d: {k: _num(v) for k, v in (d or {}).items() if k in KEYS and _num(v)}           # noqa: E731
    sales = {}
    for cid, s in (doc.get("sales") or {}).items():
        if not isinstance(s, dict):
            continue
        line = {"q": q(s.get("q"))}
        for k in ("paid", "old_paid", "discount"):
            if s.get(k) not in (None, ""):
                line[k] = max(_num(s.get(k)), 0.0)
        for k in ("free",):
            if s.get(k):
                line[k] = True
        if s.get("pay") in ("cash", "credit", "part"):
            line["pay"] = s["pay"]
        if s.get("reason") in ("factory", "other"):
            line["reason"] = s["reason"]
        if s.get("t"):
            line["t"] = str(s["t"])[:5]
        if line["q"] or line.get("old_paid"):
            sales[str(cid)] = line
    loads = [{"q": q(ld.get("q")), **({"t": str(ld["t"])[:5]} if ld.get("t") else {})} for ld in doc.get("loads") or [] if isinstance(ld, dict)]
    cash = {k: _num(v) for k, v in (doc.get("cash") or {}).items() if (k in [str(n) for n in NOTES] or k == "coins") and _num(v)}
    out = {"sales": sales, "loads": [ld for ld in loads if ld["q"]], "new_customers": clean_walkins(doc.get("new_customers")),
           "cash": cash or None, "km_end": _num(doc.get("km_end")) or None, "closed": bool(doc.get("closed"))}
    return out


def office_day(route_id: int, day: date) -> dict:
    """Everything the office day page shows: the route's customers (prices, bins, what each owed before this day), the sheet, and the
    days around it that have a sheet (for ‹ ›)."""
    from app import supabase
    r = supabase.select("sale_routes", {"select": "id,truck_no,name,driver_name,driver_phone,branch", "id": f"eq.{int(route_id)}", "limit": "1"})
    if not r:
        raise RouteSalesError("ไม่พบสายนี้")
    d = _day(r[0]["id"], day)
    before: dict = {}
    for ln in supabase.select_all("sale_lines", {"select": "customer_id,amount,paid,old_paid,billing", "route_id": f"eq.{r[0]['id']}",
                                                 "day": f"lt.{day.isoformat()}", "order": "id.asc"}):
        if ln["customer_id"] and ln["billing"] != "invoice":
            k = str(ln["customer_id"])
            before[k] = round(before.get(k, 0.0) + _num(ln["amount"]) - _num(ln["paid"]) - _num(ln["old_paid"]), 2)
    days = [x["day"] for x in supabase.select_all("sale_days", {"select": "day", "route_id": f"eq.{r[0]['id']}", "order": "day.asc"})]
    now = datetime.now(BANGKOK)
    sheet = None
    if d:
        sheet = {"id": d["id"], "doc": d["doc"], "rev": d["rev"], "status": d["status"], "totals": _public(d["totals"]),
                 "returns_foh": d.get("returns_foh"), "closed_at": d.get("closed_at"), "reopen_requested_at": d.get("reopen_requested_at"),
                 "reopen_log": d.get("reopen_log") or [], "unsent": unsent(day, d["status"], now)}
    from app import bins as bn
    custs = _customers(r[0]["id"])
    extra = {c["id"]: c for c in supabase.select_all("sale_route_customers", {"select": "id,bins_legacy,bins_other", "route_id": f"eq.{r[0]['id']}",
                                                                              "active": "eq.true", "order": "id.asc"})}
    have = bn.for_customers([c["id"] for c in custs])
    for c in custs:
        c.update({k: (extra.get(c["id"]) or {}).get(k) for k in ("bins_legacy", "bins_other")})
        c["bins_list"] = have.get(c["id"], [])
    clash = bn.clashes_for(custs, have)
    for c in custs:
        c["bins_clash"] = clash.get(c["id"], [])
    return {"route": r[0], "day": day.isoformat(), "today": now.date().isoformat(), "customers": custs,
            "owed_before": {k: v for k, v in before.items() if abs(v) >= 1}, "sheet": sheet, "days_with_sheet": days,
            "products": [{"key": k, "label": lab, "short": SHORT[k]} for k, lab in PRODUCTS], "notes": NOTES,
            "km_start": _last_km(r[0]["id"], day), "driver_selling": bool(d and d["status"] == "open" and day == now.date())}


def office_save_day(route_id: int, day: date, doc: dict, returns: Optional[dict] = None, who: str = "office") -> dict:
    """The owner / bookkeeper saves a day straight away (owner, 11 Nov 2026: no more ask the driver -> reopen -> the driver fixes).
    Works for a day the phone never sent, a sent one, and a day only on paper (created here). The status stays as it was (a new
    day is 'closed': it is a finished paper sheet); every save is written in reopen_log."""
    from app import supabase
    if day > today():
        raise RouteSalesError("ยังไม่ถึงวันนี้ — บันทึกล่วงหน้าไม่ได้")
    doc = clean_doc(doc)
    current = _day(int(route_id), day)
    customers = {**{str(c["id"]): c for c in _customers(int(route_id))}, **walkin_customers(doc)}
    doc["sales"] = {k: v for k, v in doc["sales"].items() if k in customers}
    back = {k: _num(v) for k, v in (returns or {}).items() if k in KEYS and str(v).strip() != ""} if returns is not None \
        else (current or {}).get("returns_foh")
    totals = compute(doc, customers, back or None, _last_km(int(route_id), day))
    status = current["status"] if current else "closed"
    doc["closed"] = status != "open"
    now = datetime.now(timezone.utc).isoformat()
    row = {"route_id": int(route_id), "day": day.isoformat(), "doc": doc, "rev": int((current or {}).get("rev") or 0) + OFFICE_REV_STEP,
           "status": status, "totals": _public(totals), "updated_at": now, "returns_foh": back or None,
           "reopen_log": ((current or {}).get("reopen_log") or []) + [{"at": now, "what": "office_edit", "who": who}],
           "reopen_requested_at": None}            # the driver asked for a fix: the office just made it
    if not current:
        row["closed_at"] = now
    if returns is not None:
        row["returns_at"] = now
    saved = supabase.insert("sale_days", [row], on_conflict="route_id,day")[0]
    supabase.delete("sale_lines", {"day_id": f"eq.{saved['id']}"})
    lines = [{"day_id": saved["id"], "route_id": int(route_id), "day": day.isoformat(), **_line_who(ln["customer"], customers), "qty": ln["qty"],
              "amount": ln["amount"], "paid": ln["paid"], "old_paid": ln["old_paid"], "billing": ln["billing"], "discount": ln["discount"],
              "free": ln["free"], "waived": ln["waived"], "waived_reason": ln["waived_reason"]} for ln in totals["lines"]]
    for i in range(0, len(lines), 500):
        supabase.insert("sale_lines", lines[i:i + 500])
    return {"id": saved["id"], "rev": saved["rev"], "status": status, "totals": _public(totals)}


# --------------------------------------------------------------------------------------------- the office keeps the route's customers
def _clean_customer(body: dict) -> dict:
    out = {}
    if "name" in body:
        name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()[:120]
        if not name:
            raise RouteSalesError("ใส่ชื่อร้าน")
        out["name"] = name
    if "bins" in body and "bins_list" not in body:
        out["bins"] = re.sub(r"\s+", " ", str(body.get("bins") or "")).strip()[:300] or None
    if "prices" in body:
        out["prices"] = {k: _num(v) for k, v in (body.get("prices") or {}).items() if k in KEYS and _num(v) > 0}
    if "billing" in body:
        out["billing"] = "invoice" if body.get("billing") == "invoice" else "cash"
    if "free" in body:
        out["free"] = bool(body.get("free"))
    if "active" in body:
        out["active"] = bool(body.get("active"))
    return out


def _make_room(route_id: int, seq: int, skip_id: Optional[int] = None) -> None:
    """Customer `seq` is taken: everyone from there on moves down one (the route order is the paper's numbering)."""
    from app import supabase
    rows = supabase.select_all("sale_route_customers", {"select": "id,seq", "route_id": f"eq.{int(route_id)}", "active": "eq.true",
                                                        "seq": f"gte.{int(seq)}", "order": "seq.desc"})
    if not any(r["seq"] == seq and r["id"] != skip_id for r in rows):
        return
    for r in rows:
        if r["id"] != skip_id:
            supabase.update("sale_route_customers", {"id": f"eq.{r['id']}"}, {"seq": r["seq"] + 1})


def add_customer(route_id: int, body: dict) -> dict:
    """The owner / bookkeeper adds a customer to a route (drivers cannot -- owner, 10 Nov 2026)."""
    from app import supabase
    values = _clean_customer({**body, "name": body.get("name")})
    existing = _customers(int(route_id))
    last = max([c["seq"] for c in existing] or [0])
    seq = int(_num(body.get("seq"))) or last + 1
    seq = min(max(seq, 1), last + 1)
    _make_room(int(route_id), seq)
    row = supabase.insert("sale_route_customers", [{"route_id": int(route_id), "seq": seq, "prices": {}, "billing": "cash",
                                                    "added_by_driver": False, "approved": True, **values}])[0]
    moved = _set_bins(row["id"], int(route_id), body)
    return {"id": row["id"], "seq": seq, "moved": moved}


def _set_bins(customer_id: int, route_id: int, body: dict, who: str = "office") -> list:
    """The form's bins (one per bin) -> app/bins.py, which also rebuilds the shop's bins text."""
    from app import bins as bn
    from app import supabase
    if "bins_list" not in body:
        return []
    r = supabase.select("sale_routes", {"select": "branch", "id": f"eq.{int(route_id)}", "limit": "1"})
    try:
        return bn.set_for_customer(customer_id, (r[0].get("branch") if r else None) or "สระบุรี", body.get("bins_list") or [],
                                   body.get("bins_other"), body.get("_who") or who)["moved"]
    except bn.BinError as exc:
        raise RouteSalesError(str(exc))


def update_customer(customer_id: int, body: dict) -> dict:
    from app import supabase
    c = supabase.select("sale_route_customers", {"select": "id,route_id,seq", "id": f"eq.{int(customer_id)}", "limit": "1"})
    if not c:
        raise RouteSalesError("ไม่พบลูกค้านี้")
    values = _clean_customer(body)
    if body.get("seq") not in (None, "") and int(_num(body["seq"])) != c[0]["seq"]:
        seq = max(int(_num(body["seq"])), 1)
        _make_room(c[0]["route_id"], seq, skip_id=c[0]["id"])
        values["seq"] = seq
    moved = _set_bins(c[0]["id"], c[0]["route_id"], body)
    if not values and "bins_list" not in body:
        raise RouteSalesError("ไม่มีอะไรเปลี่ยน")
    if values:
        values["approved"] = True
        supabase.update("sale_route_customers", {"id": f"eq.{int(customer_id)}"}, values)
    return {"ok": True, "moved": moved}


def check_day(day_id: int, checked: bool = True) -> dict:
    from app import supabase
    now = datetime.now(timezone.utc).isoformat()
    rows = supabase.update("sale_days", {"id": f"eq.{int(day_id)}"}, {"status": "checked" if checked else "closed", "checked_at": now if checked else None})
    if not rows:
        raise RouteSalesError("ไม่พบวันนี้")
    return {"status": rows[0]["status"]}
