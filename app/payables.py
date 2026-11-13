"""
เจ้าหนี้ร้านค้า (#payables), owner, 13 Nov 2026: what each shop is owed and which bills are paid.

The bills stay where the work is recorded (the owner's choice -- it is easier to see which is which): truck repairs and tyres in
maintenance_events, factory / stock / machine bills in purchase_bills. This page only gathers them BY SHOP (the shop's one name, as
app/shops.py folds it) and records the payments: mum's cheque (or a transfer / cash) with the bills it pays ticked by the owner -- no
cutting by dates, because which bills a cheque covers is irregular. The ticked bills must add up to the cheque within ±1 baht, or the
payment carries a note saying why. "จ่ายก่อนเริ่มใช้" marks the old bills paid in one go (the first months are fixed by hand), and a
shop paid in cash / by transfer on the day can be set so its bills are never owed. Withholding tax is left out (owner: the shops sell
goods, not services). The books (acc_entries) are not touched yet. Tables: shop_payments, shop_payment_bills, shop_pay_settings
(schema_v75.sql). The pure parts are unit-tested.
"""
from datetime import date, datetime, timezone
from typing import Optional

TOLERANCE = 1.0
METHODS = {"cheque": "เช็ค", "transfer": "โอน", "cash": "เงินสด", "before": "จ่ายก่อนเริ่มใช้"}
BOOKS = ["บริษัท (กรุงไทย)", "คุณรัชนิดา (กรุงไทย)", "HQ"]


class PayablesError(ValueError):
    """Refused, with a message shown as it is."""


def _money(x) -> float:
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


# --------------------------------------------------------------------------------------------- the bills, by shop (pure)
def truck_bill(ev: dict, lines_total: float, vehicle: Optional[dict], shop: Optional[str]) -> Optional[dict]:
    amount = _money(ev.get("total_cost")) or round(lines_total, 2)
    if not shop or amount <= 0:
        return None
    v = vehicle or {}
    label = " · ".join(x for x in (f"#{v['vehicle_number']}" if v.get("vehicle_number") else "", v.get("plate_number") or "") if x)
    return {"source": "truck", "id": str(ev["id"]), "shop": shop, "date": str(ev["event_date"])[:10], "bill_no": None, "amount": amount,
            "branch": v.get("branch") or ev.get("branch"), "what": f"รถ {label}".strip() + (f" — {ev['summary']}" if ev.get("summary") else ""),
            "link": f"#event={ev['id']}"}


def purchase_bill(b: dict, shop: Optional[str]) -> Optional[dict]:
    amount = _money(b.get("total"))
    if not shop or amount <= 0 or b.get("purpose") == "reference":
        return None
    what = {"repair": "ซ่อมเครื่องจักร", "stock": "ซื้อของเข้าสต็อก"}.get(b.get("purpose") or "", "ซื้อของโรงงาน")
    return {"source": "purchase", "id": str(b["id"]), "shop": shop, "date": str(b["bill_date"])[:10], "bill_no": b.get("bill_no"), "amount": amount,
            "branch": b.get("branch"), "what": what + (f" — {b['notes']}" if b.get("notes") else ""), "link": f"#purchase={b['id']}"}


def view(bills: list[dict], payments: list[dict], links: list[dict], pays_now: set, today: date) -> dict:
    """Every shop: what it is owed (bills not ticked by a payment), its oldest unpaid bill, its last payment; and each shop's detail."""
    paid_by = {(x["source"], str(x["bill_id"])): x["payment_id"] for x in links}
    paid_amount = {(x["source"], str(x["bill_id"])): _money(x["amount"]) for x in links}
    shop_of_bill = {(b["source"], b["id"]): b["shop"] for b in bills}
    shops: dict = {}
    for b in sorted(bills, key=lambda x: (x["date"], x["id"])):
        s = shops.setdefault(b["shop"], {"shop": b["shop"], "bills": [], "payments": [], "pays_now": b["shop"] in pays_now})
        pid = paid_by.get((b["source"], b["id"]))
        state = "paid" if pid else "now" if s["pays_now"] else "due"
        changed = round(b["amount"] - paid_amount[(b["source"], b["id"])], 2) if pid else 0.0
        s["bills"].append({**b, "state": state, "payment_id": pid, "age": (today - date.fromisoformat(b["date"])).days,
                           "changed": changed if abs(changed) > TOLERANCE else 0.0})          # edited after it was paid
    for p in sorted(payments, key=lambda x: (str(x["paid_on"]), x["id"]), reverse=True):
        ticked = [x for x in links if x["payment_id"] == p["id"]]
        home = next((shop_of_bill[(x["source"], str(x["bill_id"]))] for x in ticked if (x["source"], str(x["bill_id"])) in shop_of_bill), p["shop"])
        s = shops.setdefault(home, {"shop": home, "bills": [], "payments": [], "pays_now": home in pays_now})
        billed = round(sum(_money(x["amount"]) for x in ticked), 2)
        s["payments"].append({**{k: p.get(k) for k in ("id", "paid_on", "method", "book", "cheque_no", "note", "created_by")},
                              "paid_on": str(p["paid_on"])[:10], "amount": _money(p["amount"]), "method_label": METHODS.get(p["method"], p["method"]),
                              "bills": len(ticked), "billed": billed, "diff": round(_money(p["amount"]) - billed, 2),
                              "missing": sum(1 for x in ticked if (x["source"], str(x["bill_id"])) not in shop_of_bill)})
    out = []
    for s in shops.values():
        due = [b for b in s["bills"] if b["state"] == "due"]
        out.append({**s, "owed": round(sum(b["amount"] for b in due), 2), "due_count": len(due),
                    "oldest": due[0]["date"] if due else None, "oldest_age": due[0]["age"] if due else None,
                    "last_payment": s["payments"][0] if s["payments"] else None,
                    "flagged": sum(1 for p in s["payments"] if abs(p["diff"]) > TOLERANCE) + sum(1 for b in s["bills"] if b["changed"])})
    out.sort(key=lambda s: (-s["owed"], s["shop"]))
    owing = [s for s in out if s["owed"] > 0]
    month_start, today_iso = today.replace(day=1).isoformat(), today.isoformat()
    tiles = {"owed": round(sum(s["owed"] for s in owing), 2), "shops": len(owing), "bills": sum(s["due_count"] for s in owing),
             "over60": round(sum(b["amount"] for s in owing for b in s["bills"] if b["state"] == "due" and b["age"] > 60), 2),
             "paid_month": round(sum(p["amount"] for s in out for p in s["payments"] if p["method"] != "before" and month_start <= p["paid_on"] <= today_iso), 2)}
    return {"today": today.isoformat(), "tiles": tiles, "shops": out}


def check_payment(body: dict, due: dict) -> dict:
    """A payment as typed, against the shop's unpaid bills ({(source, id): amount}). The ticked bills must be unpaid bills of this shop;
    they add up to the amount within ±1 baht, or the payment says why (a note)."""
    method = str(body.get("method") or "")
    if method not in METHODS:
        raise PayablesError("เลือกวิธีจ่าย")
    picked = []
    for x in body.get("bills") or []:
        key = (str(x.get("source") or ""), str(x.get("id") or ""))
        if key not in due:
            raise PayablesError("มีบิลที่จ่ายไปแล้ว หรือไม่ใช่ของร้านนี้ — โหลดหน้าใหม่แล้วลองอีกครั้ง")
        picked.append({"source": key[0], "bill_id": key[1], "amount": due[key]})
    if not picked:
        raise PayablesError("ติ๊กบิลที่จ่ายอย่างน้อย 1 ใบ")
    billed = round(sum(p["amount"] for p in picked), 2)
    amount = billed if method == "before" else _money(body.get("amount"))
    if amount <= 0:
        raise PayablesError("ใส่ยอดที่จ่าย")
    note = str(body.get("note") or "").strip()[:300]
    if abs(amount - billed) > TOLERANCE and not note:
        raise PayablesError(f"ยอดจ่าย ฿{amount:,.2f} ไม่ตรงกับบิลที่ติ๊ก ฿{billed:,.2f} (ต่าง ฿{abs(amount - billed):,.2f}) — ใส่หมายเหตุว่าทำไม")
    try:
        paid_on = date.fromisoformat(str(body.get("paid_on") or "")[:10])
    except ValueError:
        raise PayablesError("ใส่วันที่จ่าย")
    return {"payment": {"method": method, "paid_on": paid_on.isoformat(), "amount": amount, "note": note or None,
                        "book": (str(body.get("book") or "").strip()[:60] or None) if method in ("cheque", "transfer") else None,
                        "cheque_no": (str(body.get("cheque_no") or "").strip()[:40] or None) if method == "cheque" else None},
            "bills": picked}


# --------------------------------------------------------------------------------------------- the database
def _bills() -> list[dict]:
    from app import shops, supabase
    snap = shops.snapshot()
    by_id = {s["id"]: s["name"] for s in snap if s.get("id")}
    vehicles = {v["id"]: v for v in supabase.select_all("vehicles", {"select": "id,branch,vehicle_number,plate_number", "order": "id.asc"})}
    events = supabase.select_all("maintenance_events", {"select": "id,vehicle_id,event_date,vendor,performed_by,branch,total_cost,summary",
                                                        "status": "eq.confirmed", "order": "id.asc"})
    no_total = [e["id"] for e in events if not _money(e.get("total_cost"))]
    sums: dict = {}
    for i in range(0, len(no_total), 100):
        for ln in supabase.select_all("bill_line_items", {"select": "event_id,line_total", "event_id": f"in.({','.join(no_total[i:i + 100])})", "order": "id.asc"}):
            sums[ln["event_id"]] = sums.get(ln["event_id"], 0.0) + _money(ln.get("line_total"))
    def shop_of(raw):
        if not str(raw or "").strip():
            return None
        hit = shops.explain(raw, snap)
        return hit["shop"] if hit["matched"] else (shops.similar_shop(raw, snap) or " ".join(str(raw).split()))
    out = []
    for e in events:
        name = shop_of(e.get("vendor"))                    # a job with no shop on it is the in-house mechanic's
        if name == shops.IN_HOUSE:
            continue
        b = truck_bill(e, sums.get(e["id"], 0.0), vehicles.get(e["vehicle_id"]), name)
        if b:
            out.append(b)
    for p in supabase.select_all("purchase_bills", {"select": "id,vendor_id,vendor_name,bill_date,bill_no,branch,total,purpose,notes", "order": "id.asc"}):
        name = by_id.get(p.get("vendor_id")) or shop_of(p.get("vendor_name"))
        b = purchase_bill(p, name)
        if b:
            out.append(b)
    return out


def _state() -> tuple[list, list, set]:
    from app import supabase
    payments = supabase.select_all("shop_payments", {"select": "*", "order": "id.asc"})
    links = supabase.select_all("shop_payment_bills", {"select": "payment_id,source,bill_id,amount", "order": "id.asc"})
    pays_now = {r["shop"] for r in supabase.select_all("shop_pay_settings", {"select": "shop,pays_now", "pays_now": "eq.true", "order": "shop.asc"})}
    return payments, links, pays_now


def load(today: date) -> dict:
    payments, links, pays_now = _state()
    out = view(_bills(), payments, links, pays_now, today)
    return {**out, "methods": [{"key": k, "label": v} for k, v in METHODS.items()], "books": BOOKS}


def _shop_due(shop: str, today: date) -> dict:
    payments, links, pays_now = _state()
    v = view([b for b in _bills() if b["shop"] == shop], payments, links, pays_now, today)
    s = next((x for x in v["shops"] if x["shop"] == shop), None)
    if not s:
        raise PayablesError("ไม่พบร้านนี้")
    return {(b["source"], b["id"]): b["amount"] for b in s["bills"] if b["state"] in ("due", "now")}


def add_payment(shop: str, body: dict, who: str, today: date) -> dict:
    from app import supabase
    clean = check_payment(body, _shop_due(shop, today))
    row = supabase.insert("shop_payments", [{**clean["payment"], "shop": shop, "created_by": who}])[0]
    try:
        supabase.insert("shop_payment_bills", [{**b, "payment_id": row["id"]} for b in clean["bills"]])
    except Exception:
        supabase.delete("shop_payments", {"id": f"eq.{row['id']}"})          # a bill paid at the same moment elsewhere: nothing half-saved
        raise PayablesError("บันทึกไม่สำเร็จ — มีบิลที่เพิ่งถูกจ่ายไปแล้ว โหลดหน้าใหม่แล้วลองอีกครั้ง")
    return {"id": row["id"]}


def paid_by(source: str, bill_id: str) -> Optional[dict]:
    """The payment a bill is ticked in, if any (a paid bill cannot be deleted: cancel the payment first)."""
    from app import supabase
    try:
        link = supabase.select("shop_payment_bills", {"select": "payment_id", "source": f"eq.{source}", "bill_id": f"eq.{bill_id}", "limit": "1"})
    except supabase.SupabaseError:
        return None                                    # the table is not there yet (schema_v75): nothing is paid
    if not link:
        return None
    p = supabase.select("shop_payments", {"select": "id,shop,paid_on,amount", "id": f"eq.{link[0]['payment_id']}", "limit": "1"})
    return p[0] if p else {"id": link[0]["payment_id"]}


def rename_shop(old: str, new: str) -> None:
    """A shop renamed on the settings page: its payments and its setting follow it."""
    from app import supabase
    try:
        supabase.update("shop_payments", {"shop": f"eq.{old}"}, {"shop": new})
        if not supabase.select("shop_pay_settings", {"select": "shop", "shop": f"eq.{new}", "limit": "1"}):
            supabase.update("shop_pay_settings", {"shop": f"eq.{old}"}, {"shop": new})
    except supabase.SupabaseError:
        pass                                           # schema_v75 not run yet


def delete_payment(payment_id: int) -> dict:
    """Undo a payment: its bills are owed again."""
    from app import supabase
    if not supabase.select("shop_payments", {"select": "id", "id": f"eq.{int(payment_id)}", "limit": "1"}):
        raise PayablesError("ไม่พบการจ่ายนี้")
    supabase.delete("shop_payments", {"id": f"eq.{int(payment_id)}"})
    return {"ok": True}


def set_pays_now(shop: str, on: bool) -> dict:
    from app import supabase
    supabase.insert("shop_pay_settings", [{"shop": shop, "pays_now": bool(on), "updated_at": datetime.now(timezone.utc).isoformat()}],
                    on_conflict="shop")
    return {"ok": True}
