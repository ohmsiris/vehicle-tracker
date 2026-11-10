"""
สายรถ · ใบขาย: the route trucks' daily sales sheet, kept by the driver on their phone (static/driver.*, served at /d/) -- owner, 10 Nov 2026.

It replaces the paper sheet one route at a time (route 35 first). The paper's layout stays: customers in route order with their bins
and price, products ซอง กั๊ก หลอด เล็ก แพ็ค โม่, loads (รับ) by trip, returns (คืน), what was sold, cash counted by note, mileage.

* Routes and customers come from the office's Word sheets (parse_sheet; scripts/import_route_sheets.py).
* A driver signs in once per phone with name + 4-digit PIN (the office sets it); the phone keeps a long random token (only its sha256
  is stored) and stays signed in until the office signs it out. Five wrong PINs lock the name for 10 minutes.
* The phone keeps the whole day as one document and sends all of it on every change (it works offline and catches up later); `rev`
  only goes up, so an old save that arrives late never overwrites a newer one. The server works out the day's totals and checks
  (compute) and rebuilds one sale_lines row per customer for reports.
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
PRODUCTS = [("zong", "ซอง"), ("kak", "กั๊ก"), ("lod", "หลอด"), ("lek", "เล็ก"), ("pack", "แพ็ค"), ("mo", "โม่")]
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
    not add up. A sale line: {"q": {product: n}, "price": {product: baht} (the driver may change a price), "paid": baht,
    "old_paid": baht, "discount": baht, "free": true (given free this once)}.
    returns = the ice back at the plant, counted by หน้าโรง (the office enters it); km_start = the last reading before this day."""
    loaded = {k: 0.0 for k in KEYS}
    for ld in doc.get("loads") or []:
        for k in KEYS:
            loaded[k] += _num((ld.get("q") or {}).get(k))
    sold = {k: 0.0 for k in KEYS}
    free_q = {k: 0.0 for k in KEYS}
    lines = []
    cash_sales = old_paid = credit = invoiced = amount_all = discounts = 0.0
    discount_shops = adhoc_free = 0
    for cid, s in (doc.get("sales") or {}).items():
        q = {k: _num((s.get("q") or {}).get(k)) for k in KEYS}
        if not any(q.values()) and not _num(s.get("old_paid")):
            continue
        cust = customers.get(str(cid)) or {}
        free = bool(cust.get("free") or s.get("free"))
        price = {**(cust.get("prices") or {}), **{k: _num(v) for k, v in (s.get("price") or {}).items() if k in KEYS}}
        gross = 0.0 if free else line_amount(q, price)
        disc = 0.0 if free else min(max(_num(s.get("discount")), 0.0), gross)
        amount = round(gross - disc, 2)
        billing = cust.get("billing") or s.get("billing") or "cash"
        paid = 0.0 if billing == "invoice" or free else min(max(_num(s.get("paid", amount)), 0.0), amount)
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
                      "billing": billing, "discount": round(disc, 2), "free": free})
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
    if discounts:
        flags.append(f"ส่วนลด {discounts:,.0f} บาท ({discount_shops} ร้าน) — ตรวจว่าอนุญาตแล้ว")
    if adhoc_free:
        flags.append(f"แจกฟรีนอกรายชื่อ {adhoc_free} ร้าน — ตรวจว่าอนุญาตแล้ว")
    return {"loaded": loaded, "sold": sold, "left": left, "returned": returned if back else None, "diff": diff, "free": free_q,
            "amount": round(amount_all, 2), "cash_sales": round(cash_sales, 2), "old_paid": round(old_paid, 2),
            "credit": round(credit, 2), "invoiced": round(invoiced, 2), "discount": round(discounts, 2), "expected_cash": round(expected_cash, 2),
            "counted": round(counted, 2) if has_cash else None, "km_start": start or None, "km": km, "customers": len(lines), "flags": flags,
            "lines": lines}


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


def login(driver_id: int, pin: str) -> str:
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
    supabase.insert("sale_driver_devices", [{"token_hash": _token_hash(token), "driver_id": d["id"], "last_seen": now.isoformat()}])
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
    if not last or now - datetime.fromisoformat(last) > timedelta(hours=1):          # no write on every request
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
            "products": [{"key": k, "label": l} for k, l in PRODUCTS], "notes": NOTES,
            "customers": _customers(r["id"]), "balances": _balances(r["id"]), "last_km": _last_km(r["id"], day),
            "sheet": _sheet(d), "pending_yesterday": pending["day"] if pending else None,
            "editable": edit_refusal(day, d["status"] if d else None, now) is None}


def save_day(driver: dict, day_text: str, rev: int, doc: dict) -> dict:
    """The phone's whole day. New customers the driver added ("new:<n>" keys) are created (waiting for the office) and their real ids
    sent back, so the phone can swap them in."""
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
    customers = {str(c["id"]): c for c in _customers(route_id)}
    id_map = {}
    for nc in doc.get("new_customers") or []:
        key = str(nc.get("key") or "")
        name = re.sub(r"\s+", " ", str(nc.get("name") or "")).strip()[:120]
        if not key.startswith("new:") or not name:
            continue
        price = {k: _num(v) for k, v in (nc.get("prices") or {}).items() if k in KEYS and _num(v) > 0}
        seq = max([c["seq"] for c in customers.values()] or [0]) + 1
        row = supabase.insert("sale_route_customers", [{"route_id": route_id, "seq": seq, "name": name, "prices": price,
                                                        "billing": "cash", "added_by_driver": True, "approved": False}])[0]
        id_map[key] = str(row["id"])
        customers[str(row["id"])] = row
    if id_map:
        doc = {**doc, "sales": {id_map.get(k, k): v for k, v in (doc.get("sales") or {}).items()},
               "new_customers": [nc for nc in doc.get("new_customers") or [] if str(nc.get("key")) not in id_map]}
    doc["sales"] = {k: v for k, v in (doc.get("sales") or {}).items() if k in customers}      # a deleted customer's line is dropped
    totals = compute(doc, customers, (current or {}).get("returns_foh"), _last_km(route_id, day))
    status = "closed" if doc.get("closed") else "open"
    now = datetime.now(timezone.utc).isoformat()
    row = {"route_id": route_id, "day": day.isoformat(), "driver_id": driver["id"], "doc": doc, "rev": int(rev), "status": status,
           "totals": {k: v for k, v in totals.items() if k != "lines"}, "updated_at": now, "closed_at": now if status == "closed" else None}
    saved = supabase.insert("sale_days", [row], on_conflict="route_id,day")[0]
    supabase.delete("sale_lines", {"day_id": f"eq.{saved['id']}"})
    lines = [{"day_id": saved["id"], "route_id": route_id, "day": day.isoformat(), "customer_id": int(ln["customer"]), "qty": ln["qty"],
              "amount": ln["amount"], "paid": ln["paid"], "old_paid": ln["old_paid"], "billing": ln["billing"], "discount": ln["discount"],
              "free": ln["free"]} for ln in totals["lines"]]
    for i in range(0, len(lines), 500):
        supabase.insert("sale_lines", lines[i:i + 500])
    return {"rev": int(rev), "status": status, "totals": _public(row["totals"]), "id_map": id_map}


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
    since = (today() - timedelta(days=14)).isoformat()
    last = {}
    requests: dict = {}
    for dday in supabase.select_all("sale_days", {"select": "route_id,day,status,totals,reopen_requested_at", "day": f"gte.{since}",
                                                  "order": "day.asc"}):
        last[dday["route_id"]] = dday
        if dday["reopen_requested_at"]:
            requests[dday["route_id"]] = requests.get(dday["route_id"], 0) + 1
    out = []
    for r in routes:
        d = drivers.get(r["id"])
        n = counts.get(r["id"], [0, 0])
        out.append({**r, "customers": n[0], "new_customers": n[1], "pin_set": bool(d and d["pin_hash"]),
                    "driver_id": d["id"] if d else None, "last_day": last.get(r["id"]), "reopen_requests": requests.get(r["id"], 0)})
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
    return {"days": rows}


def day_detail(day_id: int) -> dict:
    from app import supabase
    d = supabase.select("sale_days", {"select": "*", "id": f"eq.{int(day_id)}", "limit": "1"})
    if not d:
        raise RouteSalesError("ไม่พบวันนี้")
    cust = {str(c["id"]): c for c in _customers(d[0]["route_id"])}
    lines = supabase.select_all("sale_lines", {"select": "customer_id,qty,amount,paid,old_paid,billing,discount,free", "day_id": f"eq.{int(day_id)}",
                                               "order": "id.asc"})
    for ln in lines:
        c = cust.get(str(ln["customer_id"])) or {}
        ln["name"], ln["seq"], ln["approved"] = c.get("name"), c.get("seq"), c.get("approved", True)
    lines.sort(key=lambda x: x["seq"] or 0)
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
    customers = {str(c["id"]): c for c in _customers(d["route_id"])}
    totals = compute(d["doc"] or {}, customers, back, _last_km(d["route_id"], date.fromisoformat(d["day"])))
    supabase.update("sale_days", {"id": f"eq.{d['id']}"}, {"returns_foh": back, "returns_at": datetime.now(timezone.utc).isoformat(),
                                                        "totals": _public(totals)})
    return {"totals": _public(totals)}


def check_day(day_id: int, checked: bool = True) -> dict:
    from app import supabase
    now = datetime.now(timezone.utc).isoformat()
    rows = supabase.update("sale_days", {"id": f"eq.{int(day_id)}"}, {"status": "checked" if checked else "closed", "checked_at": now if checked else None})
    if not rows:
        raise RouteSalesError("ไม่พบวันนี้")
    return {"status": rows[0]["status"]}
